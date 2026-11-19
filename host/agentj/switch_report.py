"""A switch worker wakes the host. Only a locally admitted job can publish a reply."""
import hashlib
import json
import os
import time

from . import ctl_auth

SCOPE = 'harness-switch-report.json'


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
        if scope.get('published'):
            return {'ok':True,'duplicate':True}
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
        if row:
            # A prior disk append or publication-marker write may have failed. Re-commit
            # this exact turn ID before consuming the job; the phone still sees one page.
            host.hist.update(row['id'], end='done')
        else:
            meta = switch._result_meta(rec)
            if not meta.get('target') or not meta.get('at'):
                return {'ok':False,'error':'bad_receipt'}
            src = {'k':'agent','text':'','switch_job':job,'quote':{'id':message}}
            host.hist_add(src, switch._say_receipt(meta, host.lang), 'done')
        if not host.hist.last_write_ok:
            return {'ok':False,'error':'history_unavailable'}
        scope['published'] = True
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
            or scope.get('published') or (job and job != expected)
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
