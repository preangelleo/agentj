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
        if pin and pin.get('native_turn') is not None and pin['native_turn'] != turn:
            return None
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
        agent._child_sources, agent._child_spawns, agent._child_inputs = {}, {}, {}
        agent._native_children, agent._native_inputs, agent._native_turns = set(), {}, set()
        agent._child_source_proc = agent.proc
    pins = agent.__dict__.setdefault('_child_sources', {})
    pending = agent.__dict__.setdefault('_child_spawns', {})
    item = params.get('item') if isinstance(params.get('item'), dict) else {}
    if method == 'turn/started':
        child = params.get('threadId')
        value = params.get('turn')
        turn = value.get('id') if isinstance(value, dict) else None
        turns = agent.__dict__.setdefault('_native_turns', set())
        if isinstance(child, str) and child != agent.tid and isinstance(turn, str) and turn and (child, turn) not in turns:
            if len(turns) >= MAX:
                return
            turns.add((child, turn))
            record = agent.__dict__.setdefault('_native_inputs', {}).get(child)
            if record:
                record['child_turn'] = turn
                _activate(agent, child, record)
        return
    if method in ('item/started', 'item/completed') and item.get('type') == 'subAgentActivity':
        child = item.get('agentThreadId')
        if method == 'item/started' and item.get('kind') in ('started', 'interacted'):
            parent, turn, ident = params.get('threadId'), params.get('turnId'), item.get('id')
            inputs = agent.__dict__.setdefault('_child_inputs', {})
            if not isinstance(parent, str) or not isinstance(ident, str):
                return
            key = (parent, ident)
            if key in inputs:
                return  # duplicate/old activity cannot stage a new native child turn
            _input(agent, parent, turn, ident, [child], replace=True, bind=False)
            if key in inputs:
                # A busy followup has no new native turn yet. Retire its old pin,
                # but never relabel old-turn requests with this new owner task.
                _stage(agent, child, parent, turn, ident, ready=True)
        elif item.get('kind') == 'interrupted' and isinstance(child, str) and pins.get(child):
            pins[child]['until'] = 0
            agent.__dict__.setdefault('_native_inputs', {}).pop(child, None)
        return  # completed is task output, not a new input or an authority revocation
    if method in ('item/started', 'item/completed') and item.get('type') == 'collabAgentToolCall':
        parent = item.get('senderThreadId')
        if parent != params.get('threadId'):
            return
        ident = (parent, item.get('id'))
        if item.get('tool') == 'sendInput':
            if item.get('status') == 'failed':
                record = agent.__dict__.get('_child_inputs', {}).get(ident)
                for pin in record['bound'].values() if record else ():
                    if pin:
                        pin['until'] = 0
                if record:
                    record['failed'] = True
                    for child, assignment in list(agent.__dict__.get('_native_inputs', {}).items()):
                        if assignment and assignment['key'] == (parent, record['turn'], item.get('id')):
                            agent._native_inputs.pop(child, None)
            else:
                _input(agent, parent, params.get('turnId'), item.get('id'), item.get('receiverThreadIds') or [],
                       replace=True, bind=False)
                success = method == 'item/completed' and item.get('status') == 'completed'
                for child in (item.get('receiverThreadIds') or [])[:MAX]:
                    _stage(agent, child, parent, params.get('turnId'), item.get('id'), ready=success)
        elif item.get('tool') == 'spawnAgent':
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
        sub = source.get('subAgent', source.get('subagent', source)) if isinstance(source, dict) else {}
        spawn = sub.get('thread_spawn') or {}
        parent, child = spawn.get('parent_thread_id'), thread.get('id')
        candidates = [pin for (sender, _), pin in pending.items() if sender == parent]
        if isinstance(child, str) and child != agent.tid and child not in pins and len(pins) < MAX and len(candidates) == 1:
            pins[child] = dict(candidates[0]) if candidates[0] else None


def _input(agent, parent, turn, ident, children, *, replace, bind=True):
    if not isinstance(parent, str) or not isinstance(ident, str) or not ident:
        return
    pins, inputs = agent._child_sources, agent.__dict__.setdefault('_child_inputs', {})
    key = (parent, ident)
    if key not in inputs:
        # Retain the first event's source, including None. A later completion/replay
        # cannot borrow another current turn. Capacity exhaustion fails closed.
        if len(inputs) >= MAX:
            for child in children[:MAX]:
                if isinstance(child, str) and pins.get(child):
                    pins[child]['until'] = 0
            return
        inputs[key] = {'turn': turn, 'pin': capture(agent, parent, turn), 'bound': {}, 'staged': set(),
                       'targets': {c for c in children[:MAX] if isinstance(c, str)}}
    record = inputs[key]
    if record['turn'] != turn:
        return
    pin, bound = record['pin'], record['bound']
    if not record['targets']:
        record['targets'].update(c for c in children[:MAX] if isinstance(c, str))
    if not bind:
        for child in children[:MAX]:
            if isinstance(child, str) and child in record['targets'] and child not in record['staged'] and child not in bound:
                record['staged'].add(child)
                if pins.get(child):
                    pins[child]['until'] = 0
        return
    for child in children[:MAX]:
        if not isinstance(child, str) or not child or child not in record['targets'] or child in (agent.tid, parent) or child in bound:
            continue
        bound[child] = None
        if child not in pins and len(pins) >= MAX:
            continue
        if child not in pins or replace:
            if pins.get(child) is not None:
                pins[child]['until'] = 0  # retire old waiters and descendants before rebinding
            pins[child] = dict(pin) if pin else None
            bound[child] = pins[child]


def _stage(agent, child, parent, turn, ident, *, ready):
    if not isinstance(parent, str) or not isinstance(ident, str):
        return
    known = agent.__dict__.setdefault('_native_children', set())
    staged = agent.__dict__.setdefault('_native_inputs', {})
    record = agent.__dict__.get('_child_inputs', {}).get((parent, ident))
    if not isinstance(child, str) or not child or child in (agent.tid, parent) or not record or record.get('failed') or record['turn'] != turn or child not in record['targets'] or child in record['bound']:
        return
    if child not in known and len(known) >= MAX:
        if agent._child_sources.get(child):
            agent._child_sources[child]['until'] = 0
        return
    known.add(child)
    key = (parent, turn, ident)
    old = staged.get(child)
    if child not in staged:
        staged[child] = {'key': key, 'ready': ready, 'child_turn': None}
    elif old and old['key'] == key:
        old['ready'] |= ready
    else:
        staged[child] = None  # multiple unconsumed inputs do not prove which native turn owns the next task
    if staged.get(child):
        _activate(agent, child, staged[child])


def _activate(agent, child, assignment):
    if not assignment['ready'] or not assignment['child_turn']:
        return
    parent, owner_turn, ident = assignment['key']
    _input(agent, parent, owner_turn, ident, [child], replace=True)
    pin = agent._child_sources.get(child)
    if pin:
        pin['native_turn'] = assignment['child_turn']
    agent._native_inputs.pop(child, None)


def for_request(agent, child, turn):
    """Only a trusted native turn/started event can assign a child turn to a task."""
    pin = agent.__dict__.get('_child_sources', {}).get(child)
    if child in agent.__dict__.get('_native_children', ()):
        return pin if pin and isinstance(turn, str) and pin.get('native_turn') == turn else None
    return pin  # the older collabAgentToolCall protocol keeps its existing contract


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
