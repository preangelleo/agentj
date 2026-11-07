"""Native Codex child → original Host task pins; never an approval grant."""
from __future__ import annotations

import contextvars
import copy
import time
from . import approval_review, provenance

TTL = 3600
MAX = 2048
_ACTIVE = contextvars.ContextVar('codex_child_pin', default=None)


def live(agent, pin):
    if not pin:
        return False
    root, current = pin, pin
    for _ in range(16):
        if current is None:
            return True
        if not _live_one(agent, current):
            break
        current = current.get('parent_pin')
    else:
        if current is None:
            return True
    root['until'] = 0  # never revoke a sibling/ancestor due to this child's depth
    return False


def _live_one(agent, pin):
    host = agent.host
    send = pin['send']
    ok = (time.monotonic() < pin['until'] and agent.proc is pin['proc']
          and getattr(agent.proc, 'returncode', None) is None and not agent.halting
          and host.st is pin['state'] and str(host.hist.epoch) == pin['session']
          and getattr(host, 'estop', None) is pin['estop'] and not host.stopped()
          and not host.stopping.is_set() and getattr(host, 'task_epoch', None) == pin['task_epoch']
          and (send is None or (send.state not in ('cancelled', 'failed')
               and provenance.normalize(send.source) == pin['source']
               and getattr(send, 'approval_origin', None) == pin['origin'])))
    if ok and pin['device']:
        ok = host.st.sign_key(pin['device']) == pin['key']
    return ok


def capture(agent, parent, turn):
    if parent != agent.tid:
        pin = agent.__dict__.get('_child_sources', {}).get(parent)
        return dict(pin, parent_pin=pin) if pin else None
    if not turn or turn != agent.turn_id:
        return None
    send = getattr(agent, 'cur_send', None)
    origin = approval_review.snapshot(agent.host)
    source = provenance.turn_source(agent)
    if not origin or not origin.get('task_record'):
        return None
    task_id = getattr(agent.host, '_task_id', None)
    if callable(task_id):
        task_id()  # establish Host's lazy revocation epoch before pinning it
    device = getattr(send, 'device', None)
    return dict(send=send, source=copy.deepcopy(source), origin=copy.deepcopy(origin),
                until=time.monotonic() + TTL, proc=agent.proc, state=agent.host.st,
                session=str(agent.host.hist.epoch), estop=getattr(agent.host, 'estop', None),
                task_epoch=getattr(agent.host, 'task_epoch', None), device=device,
                key=agent.host.st.sign_key(device) if device else None)


def note(agent, method, params):
    if agent.__dict__.get('_child_source_proc') is not agent.proc:
        agent._child_sources, agent._child_spawns = {}, {}
        agent._child_source_proc = agent.proc
    pins = agent.__dict__.setdefault('_child_sources', {})
    pending = agent.__dict__.setdefault('_child_spawns', {})
    item = params.get('item') if isinstance(params.get('item'), dict) else {}
    if method in ('item/started', 'item/completed') and item.get('type') == 'collabAgentToolCall':
        parent = item.get('senderThreadId')
        if parent != params.get('threadId'):
            return
        ident = (parent, item.get('id'))
        if item.get('tool') == 'spawnAgent':
            if method == 'item/started' and ident not in pending and len(pending) < MAX:
                pending[ident] = capture(agent, parent, params.get('turnId'))
            pin = pending.get(ident) if ident in pending else capture(agent, parent, params.get('turnId'))
            if item.get('status') != 'failed':
                for child in (item.get('receiverThreadIds') or [])[:MAX]:
                    if isinstance(child, str) and child != agent.tid and child not in pins and len(pins) < MAX:
                        pins[child] = dict(pin) if pin else None
            if method == 'item/completed':
                pending.pop(ident, None)
        elif item.get('tool') in ('closeAgent', 'interruptAgent') and method == 'item/completed':
            for child in item.get('receiverThreadIds') or []:
                if pins.get(child):
                    pins[child]['until'] = 0
    elif method == 'thread/started':
        thread = params.get('thread') or {}
        source = thread.get('source') or {}
        sub = source.get('subAgent', source) if isinstance(source, dict) else {}
        spawn = sub.get('thread_spawn') or {}
        parent, child = spawn.get('parent_thread_id'), thread.get('id')
        candidates = [pin for (sender, _), pin in pending.items() if sender == parent]
        if isinstance(child, str) and child != agent.tid and child not in pins and len(pins) < MAX and len(candidates) == 1:
            pins[child] = dict(candidates[0]) if candidates[0] else None


def is_child(agent):
    active = _ACTIVE.get()
    return active is not None and active[0] is agent


def active_live(agent):
    active = _ACTIVE.get()
    return active is None or (active[0] is agent and live(agent, active[1]))


async def run(agent, pin, coro):
    token = _ACTIVE.set((agent, pin))
    valid = live(agent, pin)
    try:
        with provenance.shared_child_request(agent, pin['source'] if valid else None), \
                approval_review.origin_scope(pin['origin'] if valid else {}):
            return await coro
    finally:
        _ACTIVE.reset(token)


def wrap(agent, coro):
    active = _ACTIVE.get()
    return run(agent, active[1], coro) if active and active[0] is agent else coro
