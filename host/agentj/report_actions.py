"""Trusted host ingress for peer reports and signed, exact-checkpoint workflow resume.

Sources are constructed from the authenticated host session, never from a request,
peer JSON or report text. Reporter owns durable receipts; history and Scheduler remain
the existing owners of the main thread and workflow execution.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
import time

from . import controls, peer_guard, provenance, wire
from .friend_report import Reporter

PHONE_TYPES = ('wf_resume_get', 'wf_resume_do', 'fr_report_reply', 'fr_report_remark')
RID = re.compile(r'[0-9a-f]{32}')
REPORT_ID = re.compile(r'[0-9a-f]{64}')
_PHONE_AUTH = contextvars.ContextVar('report_phone_authority', default=None)


def phone_source(host, session):
    """Only a current READY paired Noise session can authorize these actions."""
    if (host.sessions.get(session.cid) is not session or session.state != 'ready' or not session.p33
            or not host.st.is_allowed(session.pub)
            or provenance.normalize(getattr(session, 'provenance', None)) != provenance.phone()):
        return None
    return provenance.phone()


async def on_phone(host, session, obj):
    t, r = obj.get('t'), obj.get('r')
    if t not in PHONE_TYPES or not isinstance(r, str) or not RID.fullmatch(r):
        return
    async def result(ok=False, **kw):
        await host.send_app(session, {'t':'ctl_res', 'r':r, 'action':t, 'ok':ok, **kw})
    source = phone_source(host, session)
    if source is None:
        return await result(why='owner_required')
    if t.startswith('wf_resume_'):
        from . import wf_resume, wfrun, tasks
        if t == 'wf_resume_get':
            try:
                challenge = wf_resume.challenge(host, host.channel, session.device, obj.get('run_id'), source=source)
            except (wf_resume.Refused, wfrun.RunError, tasks.TaskError) as e:
                return await result(why=getattr(e, 'why', getattr(e, 'reason', 'invalid')))
            return await result(True, challenge=challenge)
        await host.send_app(session, {'t':'ctl_pending', 'r':r, 'action':t, 'timeout_ms':620000})
        res = await wf_resume.execute(host, obj.get('challenge'), obj.get('sig'), channel=host.channel,
                                      device=session.device, source=source)
        return await result(res.get('result') == 'queued', **res)
    svc = host.peers
    if svc is None or host.stopped():
        return await result(why='unavailable')
    if t == 'fr_report_reply':
        target = {'id':obj.get('id'), 'text':obj.get('text')}
        if obj.get('resend') is True:
            target['resend'] = True
        good = isinstance(target['id'], str) and REPORT_ID.fullmatch(target['id'])
        good = good and isinstance(target['text'], str) and 0 < len(target['text']) <= 2000
    else:
        target = {'friend':obj.get('friend'), 'name':obj.get('name')}
        good = isinstance(target['friend'], str) and 0 < len(target['friend']) <= 64
        good = good and isinstance(target['name'], str) and 0 < len(target['name']) <= 80
    if not good:
        return await result(why='shape')
    why = controls.check(host.st, host.nonces, host.channel, session.device, obj, t, target)
    if why:
        controls.log(host.st, action=t, device=session.device, result='refused:'+why)
        return await result(why=why)
    key = host.st.sign_key(session.device)
    authorized = lambda: key is not None and phone_source(host, session) is not None and host.st.sign_key(session.device) == key
    rep = await reporter(svc)
    if not authorized():
        return await result(why='owner_required')
    if t == 'fr_report_reply':
        token = _PHONE_AUTH.set(authorized)
        try:
            res = await rep.reply(target['id'], target['text'], source=source, resend=target.get('resend') is True)
        finally:
            _PHONE_AUTH.reset(token)
    else:
        res = rep.set_remark(target['friend'], target['name'], source=source)
        from . import diskq
        if res.get('ok') and not await diskq.aflush():
            res = {'ok':False, 'why':'io'}
    if t == 'fr_report_reply' and res.get('state'):
        project_receipts(svc, {target['id']:res['state']})
        from . import diskq
        if not await diskq.aflush():
            res = {'ok':False, 'state':'reply_uncertain'}
    controls.log(host.st, action=t, device=session.device, result='ok' if res.get('ok') else 'refused', obj=target)
    await result(**res)
    if res.get('ok'):
        svc.changed(target.get('friend'))


async def reporter(svc):
    # Share PeerService's existing exact-value secret index, refreshed on the same schedule.
    if svc.index is None or time.monotonic() - svc.index_at > 3600:
        wd = (getattr(svc.host, 'agent_cfg', None) or {}).get('dir')
        svc.index = await asyncio.to_thread(peer_guard.SecretIndex.build, None, (), wd)
        svc.index_at = time.monotonic()
    rep = getattr(svc, '_reports', None)
    if rep is None:
        async def deliver(envelope):
            return await deliver_report(svc, envelope)
        async def reply(req):
            return await reply_report(svc, req)
        rep = svc._reports = Reporter(svc.store, deliver, reply, index=svc.index, clock=svc.clock)
    rep.index = svc.index
    return rep


async def report_decision(svc, fid, message, decision, *, blocked=None):
    """Called only after authenticated inbound gates/history and a LOCAL peer-session result.
    Wire fields called reason/source/report never influence reporting policy or authority.
    """
    if svc.host.stopped() or svc.same_owner.active(fid):
        return
    reason = 'blocked' if blocked else 'failed' if decision.error else 'decision' if decision.decision == 'ask_owner' else 'update'
    outcome = ('受阻' if svc.lang == 'zh' else 'Blocked') + ': ' + blocked if blocked else (decision.text or decision.topic or 'Peer session failed')
    summary = message['text'][:900] + '\n' + outcome[:900]
    rep = await reporter(svc)
    res = rep.submit(fid, message['mid'], summary, reason=reason, thread=message['thread'])
    if not res.get('ok') and res.get('why') == 'backpressure':
        # The original authenticated message is already durably in peer history. Keep it
        # there, visibly pending, rather than dropping it or pretending it was reported.
        svc.store.hist_set_status(fid, message['mid'], 'report_pending')
        svc.changed(fid)
    await flush(svc)


async def flush(svc):
    if svc.host.stopped():
        return
    if getattr(svc, '_reports', None) is None and not svc.store._p('reports.json').exists():
        return
    rep = await reporter(svc)
    await rep.flush()


async def deliver_report(svc, envelope):
    """Append the local peer's report to the owner's thread without starting a tool turn.

    Native allowlists can permit tools without Host.ask. A report is therefore an
    already-produced data card, not a new prompt submitted to the main harness.
    """
    host = svc.host
    if host.stopped():
        return False
    wrapped = json.loads(envelope['data'])
    data = json.loads(wrapped['text'])
    fid = data['friend']
    if svc.same_owner.active(fid) or svc.store.is_blocked(fid) or svc.store.get(fid) is None:
        return False
    rows = data['reports']
    name = data['name']
    text = '\n\n'.join((str(i + 1) + '. ' if len(rows) > 1 else '') + row['summary'] for i, row in enumerate(rows))
    host.hist_add({'k':'friend_report', 'name':name, 'text':''}, text, 'done',
                  card={'friend_report':{'friend':fid, 'name':name, 'ids':[row['id'] for row in rows],
                                        'source':{'kind':'unknown', 'role':'group'},
                                        'untrusted':True, 'execute':False}})
    from . import diskq
    if not await diskq.aflush():
        return False
    host.push_notify('reply')
    return True


async def reply_report(svc, req):
    """The signed owner's explicit reply only; reuse the peer's outbound gates and E2E sender.
    A relationship promoted to Same owner is refused, never silently rerouted to a main Agent.
    """
    fid, mid, text = req['friend'], req['reply_to'], req['text']
    authorized = _PHONE_AUTH.get()
    if not callable(authorized) or not authorized():
        return False
    try:
        if svc.host.stopped() or svc.seat_refusal() or not svc.store.settings().get("on") or svc.same_owner.active(fid):
            return False
        if svc.store.get(fid) is None or svc.store.is_blocked(fid):
            return False
        verdict = await svc._outbound(text)
        if not verdict.ok or not peer_guard.inbound(text).ok:
            return False
        if not authorized() or svc.host.stopped() or svc.seat_refusal() or not svc.store.settings().get("on") or svc.same_owner.active(fid) or svc.store.get(fid) is None or svc.store.is_blocked(fid):
            return False
        row = svc.guards.row(fid, mid)
        thread = req.get('thread')
        if not isinstance(thread, str) or not re.fullmatch(r'[0-9a-f]{16}', thread):
            return False
    except Exception:
        svc.log('peer_reply', result='pending', reason='before_send')
        return False
    if not await svc.guards.begin_reply(fid, mid, report=True, retry_unknown=req.get('retry_unknown') is True):
        return False if svc.guards.row(fid, mid).get('reply_state') == 'pending' else None
    try:
        if not authorized() or svc.host.stopped() or svc.seat_refusal() or not svc.store.settings().get("on") or svc.same_owner.active(fid) or svc.store.get(fid) is None or svc.store.is_blocked(fid):
            await svc.guards.release_reply(fid, mid, 'authority_changed')
            return False
    except Exception:
        await svc.guards.release_reply(fid, mid, 'before_send')
        return False
    # Once transport is called, an exception is ambiguous and must retain the latch.
    sent = svc._send_pmsg(fid, text, {'mid':mid, 'thread':thread, 'relay':(row or {}).get('relay')}, auto=False)
    if sent is None:
        await svc.guards.release_reply(fid, mid, 'send_refused')
        return False
    svc.resolve_question(fid, mid)
    if not await svc.guards.finish_reply(fid, mid):
        return None
    svc.store.auto_round(fid, reset=True)
    svc.changed(fid)
    return True


def listing(svc):
    """Bounded read-only CLI projection: every summary is explicitly untrusted data."""
    rep = getattr(svc, '_reports', None)
    if rep is None:
        rep = Reporter(svc.store, None, None, index=peer_guard.SecretIndex([]), clock=svc.clock)
    with svc.store.lock():
        d = rep._read()
    rows = sorted(d['records'].items(), key=lambda row:row[1]['at'], reverse=True)[:50]
    return {'ok':True, 'untrusted':True, 'execute':False,
            'reports':[dict(id=rid, name=d['remarks'].get(r['friend']) or r['friend'], **r) for rid, r in rows]}


def remarks(svc):
    rep = getattr(svc, '_reports', None)
    if rep is None:
        rep = Reporter(svc.store, None, None, index=peer_guard.SecretIndex([]), clock=svc.clock)
    with svc.store.lock():
        return dict(rep._read()['remarks'])


def project_receipts(svc, states):
    host = svc.host
    for page in list(getattr(getattr(host, 'hist', None), 'turns', {}).values()):
        card = page.get('card') or {}
        report = card.get('friend_report')
        if isinstance(report, dict):
            updates = {rid:states[rid] for rid in report.get('ids', ()) if rid in states}
            if updates:
                host.hist_update(page['id'], card={**card, 'friend_report':{**report, 'states':{**report.get('states', {}), **updates}}})


async def recover_replies(svc):
    """Reconcile durable send receipts and the original history cards before ingress starts."""
    from . import diskq
    svc.guards.recover_replies()
    if svc.store._p('reports.json').exists():
        rep = Reporter(svc.store, None, None, index=peer_guard.SecretIndex([]), clock=svc.clock)
        with svc.store.lock():
            data = rep._read()
            for record in data['records'].values():
                if record['state'] not in ('reported', 'reply_uncertain', 'reply_unknown'):
                    continue
                row = svc.guards.row(record['friend'], record['mid']) or {}
                state = row.get('reply_state', 'pending')
                record['state'] = {'pending':'reported', 'unknown':'reply_unknown', 'done':'replied'}.get(state, 'reply_uncertain')
            svc.store._write('reports.json', data)
        project_receipts(svc, {rid:record['state'] for rid, record in data['records'].items()})
    if not await diskq.aflush():
        raise OSError('peer reply recovery durability failed')
