"""A switch worker wakes the host. Only a locally admitted job can publish a reply."""
import hashlib
import json
import os
import time

from . import ctl_auth

SCOPE = 'harness-switch-report.json'


TASK_STATES = ("checkpoint", "harness", "login", "approval", "queued", "queued_unsent", "resumed", "failed")
_TASK_TEXT = {
    "checkpoint": ("还没确认能从哪一步接着做。请先看原任务最后一步的结果。",
                   "It’s unclear where to pick up. Check the last result in the original task."),
    "harness": ("等新助手准备好后，再核对原任务。",
                "Waiting for the new assistant to be ready before checking the original task."),
    "login": ("请先登录要切换到的助手，再继续。",
              "Sign in to the assistant you’re switching to before continuing."),
    "approval": ("请先在原来配对的遥控器上处理确认卡。",
                 "Respond to the confirmation card on the original paired remote."),
    "queued": ("正在确认任务有没有交到新助手手里，先别重发。",
               "Checking whether the task reached the new assistant. Please don’t resend yet."),
    "queued_unsent": ("任务还没发出，正在等新助手接收。",
                      "The task is still waiting to be sent to the new assistant."),
    "resumed": ("任务已自动接上。进展请看原任务消息。",
                "The task resumed automatically. Follow the original task message for updates."),
    "failed": ("没能确认自动续做。请先看原任务消息，确认停在哪一步。",
               "I couldn’t confirm that the task resumed. Check the original task message to see where it stopped."),
}


def task_text(rec, lang):
    lines = _TASK_TEXT.get(rec.get("task_resume"))
    if lines is None:
        return ""
    english = lang == "en"
    text = lines[1] if english else lines[0]
    count = rec.get("manual_resumes", 0)
    if count >= 1:
        if english:
            unit = "time" if count == 1 else "times"
            text += f" You asked to continue manually {count} {unit}."
        else:
            text += f"已手动续做 {count} 次。"
    return "\n" + text


def task_meta(st, job):
    scope = _read(st.root / SCOPE)
    if not job or scope.get("job") != job:
        return {}
    state, manual = scope.get("task_resume"), scope.get("manual_resumes")
    return ({"task_resume": state, "manual_resumes": manual}
            if state in TASK_STATES and type(manual) is int and 0 <= manual <= 999 else {})


def manual_request(text):
    return isinstance(text, str) and text.strip().lower().rstrip("。.!！") in ("继续", "继续做", "continue", "resume")


def _open_delivery_status(host, device, tasks, open_ids):
    pages = {page["id"]: (host.hist.epoch, page["ts"]) for page in tasks}
    held, uncertain = set(), set()
    for (owner, _), receipt in host.receipts.items.items():
        turn = receipt.get("n")
        if owner != device or turn not in pages or receipt.get("reply_page") != pages[turn]:
            continue
        state = receipt.get("s")
        if state == "deliv" and not receipt.get("notice_only"):
            return "resumed"
        if state == "held" and receipt.get("seal"):
            held.add(turn)
        else:
            uncertain.add(turn)
    # Claim changes held to recv before submission. Older queued metadata carried no proof.
    return "queued_unsent" if open_ids <= held - uncertain else "queued"


def _task_status(host, scope, rec, row):
    status, count = rec.get("task_resume"), 0
    if status not in TASK_STATES:
        return {}

    def phone(page):
        source = page["src"]
        return (source.get("k") == "phone" and source.get("dev") == scope["device"]
                and source.get("source_kind") == "phone" and source.get("source_role") == "owner")

    turns = host.hist.turns.values()
    tasks = [page for page in turns if phone(page) and page["src"].get("switch_resume_job") == scope["job"]]
    if tasks and rec.get("status") in ("ok", "rolled_back"):
        open_ids = {page["id"] for page in tasks if page["end"] == "open"}
        if open_ids:
            status = _open_delivery_status(host, scope["device"], tasks, open_ids)
        elif any(page["end"] == "done" and page["reply"].get("text") for page in tasks):
            status = "resumed"
        else:
            status = "failed"

    if row:
        for page in turns:
            if page["id"] <= row["id"] or not phone(page):
                continue
            source = page["src"]
            quote = (source.get("quote") or {}).get("id")
            if not manual_request(source.get("text")) or (quote not in (None, scope["message"], row["id"])):
                break
            if not source.get("switch_resume_job"):
                count += 1
    return {"task_resume": status, "manual_resumes": min(count, 999)}


def _read(path):
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def bind(st, job, origin=None):
    """Only the page proven at admission owns a main-column reply. All jobs retain local results."""
    scope = {'job':job, 'at':time.time()}
    if origin is not None:
        scope.update(epoch=origin.epoch, device=origin.device, message=origin.source.get('message_id'),
                     channel=origin.source.get('channel'), key_hash=hashlib.sha256(origin.signing_key).hexdigest())
    st.write_private(st.root/SCOPE, json.dumps(scope).encode())


def receive(host, req, pid, uid):
    if set(req) != {'cmd','job'} or uid != os.getuid() or ctl_auth.refusal(pid, os.getpid(), host._agent_pids()):
        return {'ok':False, 'error':'refused'}
    from . import switch_harness as switch
    job = req.get('job')
    if not isinstance(job,str) or not switch._JOB.fullmatch(job):
        return {'ok':False, 'error':'bad_job'}
    return deliver(host, job)


def deliver(host, expected=None):
    """Same UI row on live delivery and recovery. Unrelated commands cannot supply result text."""
    from . import switch_harness as switch
    if not switch._LOCK.acquire(blocking=False):
        return {'ok':False,'error':'busy'}
    try:
        st = host.st
        scope = _read(st.root/SCOPE)
        job = scope.get('job')
        if not isinstance(job,str) or not switch._JOB.fullmatch(job) or (expected and expected != job):
            return {'ok':False,'error':'wrong_job'}
        if not scope.get('device'):
            return {'ok':False,'error':'unbound'}
        rec = switch.read_receipt(st)
        if not rec or rec.get('job') != job or rec.get('status') not in ('ok','rolled_back','failed'):
            return {'ok':False,'error':'no_result'}
        if not (0 <= time.time() - scope['at'] <= switch.JOB_MAX_AGE and rec['at'] >= scope['at']):
            return {'ok':False,'error':'stale_job'}
        checked = rec.get('target') if rec.get('status') == 'ok' else rec.get('from') if rec.get('status') == 'rolled_back' else None
        if checked and switch.current_kind(st) != checked:
            return {'ok':False,'error':'context_changed'}
        device, message = scope['device'], scope.get('message')
        key = st.sign_key(device)
        if not key or hashlib.sha256(key).hexdigest() != scope['key_hash'] or scope.get('epoch') != host.hist.epoch:
            return {'ok':False,'error':'context_changed'}
        old = (host.hist.get(message) or {}).get('src') or {}
        if scope.get('channel') == 'phone' and old.get('k') == 'phone' and old.get('dev') == device:
            pass
        elif not (scope.get('channel') == 'host' and old.get('k') == 'host' and set(st.devices()) == {device}):
            return {'ok':False,'error':'context_changed'}
        row = next((row for row in host.hist.turns.values() if row['src'].get('switch_job') == job),None)
        task = _task_status(host, scope, rec, row)
        if scope.get('published') and all(scope.get(key) == value for key, value in task.items()):
            return {'ok':True,'duplicate':True}
        meta = switch._result_meta({**rec, **task})
        if not meta.get('target') or not meta.get('at'):
            return {'ok':False,'error':'bad_receipt'}
        if row:
            # Both the initial publication and later task receipts use this exact history page.
            if task:
                host.hist_update(row['id'], text=switch._say_receipt(meta, host.lang), end='done')
            else:
                host.hist.update(row['id'], end='done')
        else:
            src = {'k':'agent','text':'','switch_job':job,'quote':{'id':message}}
            host.hist_add(src, switch._say_receipt(meta, host.lang), 'done')
        if not host.hist.last_write_ok:
            return {'ok':False,'error':'history_unavailable'}
        scope.update(task, published=True)
        st.write_private(st.root/SCOPE, json.dumps(scope).encode())
        return {'ok':True}
    except (OSError, KeyError, TypeError, ValueError):
        return {'ok':False,'error':'unavailable'}
    finally:
        switch._LOCK.release()


def watch(host, job=None):
    """Wait only for the admitted page's pending job, including across a service restart."""
    import asyncio
    from . import switch_harness as switch

    scope = _read(host.st.root/SCOPE)
    expected = scope.get('job')
    if (not isinstance(expected,str) or not switch._JOB.fullmatch(expected) or not scope.get('device')
            or (job and job != expected)
            or type(scope.get('at')) not in (int,float) or not 0 <= time.time()-scope['at'] < switch.JOB_MAX_AGE):
        return
    if host.__dict__.get('_switch_report_job') == expected:
        return
    host._switch_report_job = expected

    async def wait():
        try:
            while time.time() - scope['at'] < switch.JOB_MAX_AGE:
                result = deliver(host, expected)
                if result.get('error') not in ('no_result','busy','unavailable','history_unavailable'):
                    return
                await asyncio.sleep(2)
        finally:
            if host._switch_report_job == expected:
                host._switch_report_job = None

    host._bg(wait(), 'switch_report')
