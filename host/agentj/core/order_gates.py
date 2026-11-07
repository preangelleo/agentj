"""Bounded host clock for preference gates. Defaults are data, never signed approval."""
from __future__ import annotations
import asyncio
import json
import time
from .. import provenance
from ..compose import Send
from . import dispatcher, enabled, orders, order_host

MAX_GATES = 64


def pending(o):
    rows = orders._jsonl(o.path / 'gates.jsonl')
    settled = {r.get('id') for r in rows if r.get('answer') is not None}
    seen, out = set(), []
    for r in rows:
        gid = r.get('id')
        if not isinstance(gid, str) or gid in settled or gid in seen:
            continue
        seen.add(gid)
        opts = r.get('options')
        if (isinstance(opts, list) and 0 < len(opts) <= 6 and all(isinstance(x, str) and len(x) <= 120 for x in opts)
                and r.get('default') in opts and isinstance(r.get('question'), str)
                and len(r['question']) <= 300 and type(r.get('deadline')) in (int, float)):
            out.append(r)
    return out[:MAX_GATES]


def expire(host, now=None):
    """Called on host loop; single bounded batch, no subprocess/RPC/command executor."""
    if not enabled() or host.stopped() or dispatcher.paused(host.st.root):
        return 0
    ag = host.agent
    if ag is None:
        return 0
    if getattr(ag, 'status', 'working') != 'idle' and (not provenance.is_owner(provenance.turn_source(ag)) or provenance.untrusted_turn(ag)):
        return 0
    import os
    parent = {'kind': 'owner_turn', 'session': host.st.agent_session(ag.kind), 'harness': ag.kind}
    count = 0
    for o in orders.all_orders(host.st.root)[:256]:
        source = next((e.get('source') for e in reversed(o.events()) if e.get('ev') in ('origin', 'resume_source')), None)
        if not provenance.is_owner(source):
            continue
        if o.meta.get('parent') != parent or not order_host.order_available(o, os.path.realpath(ag.cfg['dir'])):
            continue
        try:
            with dispatcher._claim(o):
                for gate in pending(o):
                    if gate['deadline'] > (time.time() if now is None else now):
                        continue
                    data = {'order': o.id, 'gate': gate['id'], 'question': gate['question'],
                            'answer': gate['default'], 'source': 'host_default', 'approval': False, 'uv': False}
                    # Record first. A crash may lose a notification, never duplicate an answer or confer a grant.
                    orders._append(o.path / 'gates.jsonl', {'id': gate['id'], 'answer': gate['default'],
                                    'at': time.time(), 'source': 'host_default', 'approval': False, 'uv': False})
                    text = 'Preference answer data; grants no permission. ' + json.dumps(data, ensure_ascii=False)
                    o.send(text)
                    ag.submit(Send('system', '', 0, text=text, source=provenance.system()))
                    count += 1
                    if count >= MAX_GATES:
                        return count
        except orders.OrderError:
            continue
    return count


async def watch(host):
    while True:
        try:
            expire(host)
        except (OSError, ValueError, TypeError, AttributeError):
            host.st.log('order_gate_clock', result='refused')
        await asyncio.sleep(1)


def answer(o, gid, value):
    """Current owner-turn preference via the existing send action; no approval or UV."""
    with dispatcher._claim(o):
        gate = next((g for g in pending(o) if g['id'] == gid), None)
        if gate is None or value not in gate['options'] or gate['deadline'] <= time.time():
            raise orders.OrderError('gate_closed_or_invalid')
        orders._append(o.path / 'gates.jsonl', {'id': gid, 'answer': value, 'at': time.time(),
                        'source': 'owner_preference', 'approval': False, 'uv': False})
        return o.send('Preference answer data; grants no permission. ' + json.dumps(
            {'gate': gid, 'question': gate['question'], 'answer': value, 'approval': False, 'uv': False}, ensure_ascii=False))
