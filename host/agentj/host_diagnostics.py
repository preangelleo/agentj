"""Fixed Agent-facing diagnostics/support operations; identity keys stay in serve."""
from __future__ import annotations

import io
import os


def agent_side():
    from . import elevate, fence
    return bool(os.environ.get(elevate.ENV)) or fence.inside()


def host_instance(st):
    from . import instance
    from pathlib import Path
    if not isinstance(getattr(st, 'root', None), (str, Path)):
        return None
    root = Path(st.root).absolute()
    if root.resolve() != root:
        return None
    return next((n for n in range(1, instance.MAX + 1) if root == instance.state_root(n).absolute()), None)


def devices_unavailable(as_json):
    import json
    obj = {'ok':False, 'error':'host_state_unavailable', 'detail':'Settings → Remotes / 设置 → 遥控器管理'}
    return {'exit':1, 'output':(json.dumps(obj, ensure_ascii=False) if as_json else obj['error'] + ': ' + obj['detail']) + '\n'}


def asr_unavailable(as_json):
    import json
    obj={'ok':False,'error':'host_state_unavailable','detail':'ASR host state unavailable; do not infer from sandbox defaults / 无法读取宿主转写记录，不得用沙箱默认值推断'}
    return {'exit':1,'output':(json.dumps(obj,ensure_ascii=False) if as_json else obj['detail'])+'\n'}


def request(op, **fields):
    from . import elevate, fence, names
    from .state import State
    required = agent_side()
    if not required:
        return None
    bound = op in ('devices','asr_status')
    unavailable = asr_unavailable if op == 'asr_status' else devices_unavailable
    if bound and not os.environ.get(elevate.ENV):
        res = unavailable(fields.get('as_json'))
        print(res['output'], end='')
        return 1
    st = None if bound else State()
    from . import instance
    if bound:
        fields['selected_instance'] = instance.current()
    try:
        res = elevate.client_request(st, {"t": "host_diagnostics", "op": op, **fields},
                                     timeout=(90 + min(max(fields.get("wait", 0), 0), 180) if op in ("support", "feedback_send") else 30 if op in READ_ONLY else 300), path=os.environ.get(elevate.ENV) or names.elevate_sock(st))
    except (OSError, ValueError) as e:
        res = {"error": type(e).__name__}  # class name only
    if bound and (not isinstance(res, dict) or type(res.get('host_instance')) is not int or res.get('host_instance') != instance.current()):
        res = unavailable(fields.get('as_json'))
    if isinstance(res, dict) and type(res.get("exit")) is int and isinstance(res.get("output"), str):
        print(res["output"], end="")
        return res["exit"]
    why = str(res.get("error") or "")[:40] if isinstance(res, dict) else ""
    if op in READ_ONLY:          # #467: never an empty list / "no tasks" when the host did not answer
        print(f"({why}) " * bool(why) + fence.hidden_state("主机这次没有回答，稍后再试。 / The host did not answer this time; "
                                                              "try again later. "))
        return 1
    print(f"({why}) " * bool(why) + "聊天能继续时，可忽略这一步连接检查；安全隔离内不能读取本机钥匙文件，后台暂时没有返回结果。"
              "反馈若未收到编号，就还没有确认提交，请先查看客服会话再重试。 / If chat works, this connection check can be skipped. "
              "Isolation hides the local key files; the background service has not returned a result. "
              "Feedback without a ticket ID is not confirmed sent; check support threads before retrying.")
    return 1


def feedback_snapshot():
    """No harness/fence probes during feedback. Existing support.prepare redacts this."""
    from . import doctor, __version__
    rows = doctor.run_read_only()
    return "Agent J " + __version__ + "\n" + "\n".join(str(r.get("summary", "")) for r in rows)


ALLOWED = {  # P569: each op is signature-tested
    'asr_status': {'as_json','selected_instance'},
    "devices": {"as_json", "selected_instance"}, "diag": set(), "layout_last": set(), "status": set(), "reconnect": {"session", "caller_session", "as_json"},
    "support": {"kind", "text", "thread", "attach_doctor", "wait", "lang", "owner_confirmed"},
    "thread": {"thread", "wait"}, "list": set(),
    "feedback_send": {"raw", "owner_confirmed"}, "feedback_replies": {"as_json"},
    "tasks_list": {"as_json"},   # #467 (ADR-0269-w4-fence): read-only; serve's own state, fixed projection
}
READ_ONLY = frozenset({"tasks_list"})
# #467: what a fenced `agentj tasks list` may learn — the same rows the owner's terminal shows, last run reduced to its verdict
TASK_FIELDS = ("id", "title", "schedule", "tz", "mode", "suggested", "enabled", "stale", "uncertain", "problems", "next",
               "migrated", "last")
_LAST_FIELDS = ("verdict", "status", "at")


def caller_refusal(req, sock, host) -> str | None:
    """Security review A-1 (w4-fence): a READ_ONLY op answers only a caller that is identified (SO_PEERCRED / LOCAL_PEERPID,
    like report_local's peer check) AND runs inside the owner's own Agent session (its process chain meets serve's
    `_agent_pids()`: the owned Agent or the served shared session). Friend / visitor / group sessions, bots and scheduled
    tasks are other children of serve and are refused. Other ops are unchanged. None = allowed."""
    if not isinstance(req, dict) or req.get("op") not in READ_ONLY:
        return None
    from .shared_hook import ancestors, peer_pid
    pid, _ = peer_pid(sock) if sock is not None else (None, None)
    if not isinstance(pid, int) or pid <= 1:
        return "peer_unverified"
    try:
        owner = {p for p in host._agent_pids() if isinstance(p, int) and p > 1}
    except Exception:  # noqa: BLE001 — no known owner session: nobody qualifies
        owner = set()
    if not owner or not set(ancestors(pid)) & owner:
        return "not_owner_agent"
    return None


def refused(why: str) -> dict:
    from . import fence
    return {"exit": 1, "output": f"Request refused: {why}. " + fence.hidden_state(
        "这条只读查询只回答主人自己的 Agent 会话。 / This read-only query answers only the owner's own Agent session. ") + "\n"}


def task_row(r: dict) -> dict:
    out = {k: r[k] for k in TASK_FIELDS if k in r}
    last = r.get("last")
    out["last"] = {k: last[k] for k in _LAST_FIELDS if k in last} if isinstance(last, dict) else None
    return out


def execute(st, req):
    """No paths, URLs, credentials, subprocesses or caller-selected function names accepted."""
    from . import cloud, feedback, reconnect, support
    op, allowed = req.get("op"), ALLOWED
    fields = set(req) - {"t", "op"}
    if not isinstance(op, str) or op not in allowed or fields - allowed[op]:
        return {"exit": 1, "output": "Request refused: shape\n"}
    for key in ("as_json", "attach_doctor", "owner_confirmed"):
        if key in req and type(req[key]) is not bool:
            return {"exit": 1, "output": "Request refused: boolean\n"}
    for key, limit in (("session", 256), ("caller_session", 256), ("text", 65536), ("thread", 64), ("raw", feedback.MAX_BODY)):
        if key in req and req[key] is not None and (not isinstance(req[key], str) or len(req[key].encode()) > limit):
            return {"exit": 1, "output": "Request refused: text\n"}
    wait = req.get("wait", 0)
    if type(wait) is not int or not 0 <= wait <= 180:
        return {"exit": 1, "output": "Request refused: wait\n"}
    if op == "support" and (req.get("kind") not in support.KINDS or not isinstance(req.get("text"), str)
                            or req.get("lang") not in (None, "zh", "en")):
        return {"exit": 1, "output": "Request refused: support fields\n"}
    out = io.StringIO()
    try:
        if op == 'asr_status':
            from . import voice, preferences
            import json
            if type(req.get('selected_instance')) is not int or req['selected_instance'] != host_instance(st):
                return asr_unavailable(req.get('as_json'))
            result=voice.asr_status(preferences.effective(st),state_dir=st.root)
            text=json.dumps(result,ensure_ascii=False)
            return {'exit':0,'output':text+'\n','host_instance':host_instance(st)}
        if op == 'devices':
            from . import instance, names, cli
            from types import SimpleNamespace
            if type(req.get('selected_instance')) is not int or req['selected_instance'] != host_instance(st):
                return devices_unavailable(req.get('as_json'))
            import json
            records = json.loads(st.devices_path.read_text())
            if not isinstance(records, dict) or any(not isinstance(v, dict) for v in records.values()):
                return devices_unavailable(req.get('as_json'))
            status = names.ctl_call(st, {'cmd':'status'}, 10)
            if not status or status.get('ok') is not True or not isinstance(status.get('sessions'), list) or any(
                    not isinstance(v, dict) or not isinstance(v.get('state'), str) or v['state'] == 'ready' and not isinstance(v.get('device'), str) for v in status['sessions']):
                return devices_unavailable(req.get('as_json'))
            from . import credential_revoke
            journal = credential_revoke.read(st)
            records = {k:v for k,v in records.items() if not credential_revoke.blocked(st,k,v,journal)}
            cli.cmd_devices(SimpleNamespace(json=req.get('as_json', False)), st=st, status=status, records=records, out=out)
            return {'exit':0, 'output':out.getvalue(), 'host_instance':host_instance(st), 'host_pid':os.getpid(), 'host_ppid':os.getppid()}
        if op == "diag":
            from . import field_tools
            import json
            print(json.dumps(field_tools.collect(st, local=True)), file=out)
            rc = 0
        elif op == "layout_last":
            from . import layout_diag
            rc = layout_diag.last(st, out)
        elif op == "status":
            from . import names
            import json
            res = names.ctl_call(st, {"cmd": "status"}, 10) or {}
            safe = {k: res.get(k) for k in ("ok", "agent", "agent_status", "relay_up", "relay_state", "identity_ok", "harness_readiness")}
            safe.update(host_instance=host_instance(st) or "unknown", host_pid=os.getpid(), host_ppid=os.getppid())
            safe["agent_name"] = st.agent_name()  # P502: current owner-defined display data, including inside the fence
            from .text import agent_label
            safe["agent_display_name"] = agent_label(safe["agent_name"])
            from . import upgrade_diag
            last = upgrade_diag.read(st)
            if last:
                evidence = upgrade_diag.exception_fields(last)
                safe["last_upgrade"] = {k: last.get(k) for k in ("from", "to", "result", "reason", "at")}
                safe["last_upgrade"].update({k: evidence.get(k, "unknown") for k in
                                             ("exception_type", "errno", "failure_stage", "failure_at")})
            print(json.dumps(safe), file=out)
            rc = 0 if res.get("ok") else 1
        elif op == "reconnect":
            from . import preferences
            lang = preferences.get(preferences.effective(st), "appearance.language", "zh")
            rc = reconnect.run(st, lang, session=req.get("session"),
                               env={"CLAUDE_CODE_SESSION_ID": req.get("caller_session")},
                               ask=False, as_json=req.get("as_json", False), out=out)
        elif op == "support":
            rc = support.run_send(req.get("kind"), req.get("text"), thread=req.get("thread"),
                                  attach_doctor=req.get("attach_doctor", False), wait=wait, lang=req.get("lang"),
                                  ch=support.channel_for(st), doctor_fn=feedback_snapshot, out=out,
                                  owner_confirmed=req.get("owner_confirmed", False) and not support._full_access(st))  # #520: no fabricated human answer
        elif op == "thread":
            rc = support.run_thread(req.get("thread"), wait=wait, ch=support.channel_for(st), out=out)
        elif op == "list":
            rc = support.run_list(st=st, out=out)
        elif op == "tasks_list":
            from . import cli
            rc = cli.tasks_list(st, as_json=req.get("as_json", False), out=out, safe=True)
        elif op == "feedback_send":
            rc = feedback.run_send(None, raw_text=req.get("raw"),
                                   owner_confirmed=req.get("owner_confirmed", False) and not support._full_access(st),
                                   out=out, state=st)
        else:
            rc = feedback.run_replies(as_json=req.get("as_json", False), out=out)
    except (support.SupportError, cloud.CloudError, feedback.FeedbackError) as exc:
        if op == 'devices':
            return devices_unavailable(req.get('as_json'))
        # Existing errors are safe, but arbitrary OS errors may contain private paths.
        rc = support.EXIT_SEAT if isinstance(exc, support.SupportSeatError) else 1
        print(str(exc), file=out)
    except Exception as exc:  # P569: class name only, never the text
        if op == 'asr_status':
            return asr_unavailable(req.get('as_json'))
        if op == 'devices':
            return devices_unavailable(req.get('as_json'))
        rc, name = 1, type(exc).__name__
        try:
            st.log("host_diagnostics_failed", action=op, exception_type=name)
        except OSError:
            pass
        print(f"后台请求出错（{name}），不是还没回复 / Background request failed ({name}); not 'no reply yet'", file=out)
    return {"exit": rc, "output": out.getvalue()}
