"""Host-owned dispatch authority. No request field supplies identity, policy or state paths."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time

from .. import provenance
from . import dispatcher, enabled, orders, process_evidence

FIELDS = {
    'new': {'action', 'project', 'prompt', 'title', 'brief', 'mode', 'harness'},
    'status': {'action', 'id'}, 'send': {'action', 'id', 'text', 'gate', 'answer'},
    'stop': {'action', 'id'}, 'resume': {'action', 'id', 'text'},
    'report': {'action', 'id', 'section'},
}


def authority(host, peer=None, internal=None):
    if not enabled() or host.stopped():
        raise orders.OrderError('stopped_or_off')
    ag = host.agent
    if getattr(ag, 'stop_flag', False) or getattr(ag, 'halting', False):
        raise orders.OrderError('stopped_or_off')
    source = provenance.turn_source(ag)
    unattended = provenance.unattended_turn(ag)
    owner = provenance.is_owner(source) and not provenance.untrusted_turn(ag) and not provenance.delegated_turn(ag)
    dispatch = getattr(host, '_dispatch_turn', None)
    foreign = getattr(host, '_foreign_seen', None)
    background = (unattended in ('system', 'same_host') and callable(dispatch) and dispatch()
                  and callable(foreign) and not foreign())
    if not owner and not background:
        raise orders.OrderError('source')
    cfg = copy.deepcopy(ag.cfg)
    sid = host.st.agent_session(ag.kind)
    if not isinstance(sid, str) or not sid or not cfg.get('dir'):
        raise orders.OrderError('session')
    if internal is not ag:
        # Bind only descendants of the currently served, live process. A same-host
        # launch proof by itself does not confer this session's dispatch authority.
        session = getattr(ag, 'session', None)
        proc = getattr(ag, 'proc', None)
        if ag.kind == 'core':
            proc = getattr(getattr(ag, 'toolbox', None), 'proc', None)
        if isinstance(session, dict):
            anchor, token = session.get('pid'), session.get('procStart')
            if session.get('sessionId') != sid:
                raise orders.OrderError('session')
        elif proc is not None and proc.poll() is None:
            anchor = proc.pid
            token = process_evidence.codex_procs.start_token(anchor)
        else:
            raise orders.OrderError('peer')
        if not process_evidence.descendant(peer, anchor, token):
            raise orders.OrderError('peer')
        if not isinstance(session, dict) and proc.poll() is not None:
            raise orders.OrderError('peer')
    parent = {'kind': 'owner_turn' if owner else 'task', 'session': sid, 'harness': ag.kind}
    if not owner:
        parent['source_class'] = unattended
    return parent, cfg


def available_project(project, root):
    return isinstance(project, str) and (os.path.realpath(project) == root
        or os.path.realpath(project).startswith(root + os.sep))


def order_available(o, root):
    # Older orders have an exact-root admission contract. New orders freeze the
    # available parent root at admission; changing it must not widen access.
    admitted = next((e.get('available_root') for e in o.events() if e.get('ev') == 'origin'), None)
    return (admitted or o.meta.get('project')) == root and available_project(o.meta.get('project'), root)


async def handle(host, req, *, peer=None, internal=None):
    try:
        parent, cfg = authority(host, peer, internal)
        source = copy.deepcopy(provenance.turn_source(host.agent))
        if not isinstance(req, dict) or set(req) - {'t', 'args', 'nonce'}:
            raise orders.OrderError('shape')
        args = copy.deepcopy(req.get('args'))
        if isinstance(args, dict):
            act = args.get('action')
            for name in ('project', 'prompt', 'title', 'mode', 'harness', 'id', 'text', 'gate', 'answer'):
                if name in args and not isinstance(args[name], str):
                    raise orders.OrderError('shape')
            if 'section' in args and (type(args['section']) is not int or args['section'] < 1):
                raise orders.OrderError('shape')
        nonce = req.get('nonce')
        if not isinstance(args, dict) or args.get('action') not in FIELDS or set(args) - FIELDS[args['action']]:
            raise orders.OrderError('shape')
        if not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce):
            raise orders.OrderError('nonce')
        lock = getattr(host, '_core_order_lock', None)
        if lock is None:
            host._core_order_lock = lock = asyncio.Lock()
        async with lock:
            if (authority(host, peer, internal) != (parent, cfg)
                    or provenance.turn_source(host.agent) != source):
                raise orders.OrderError('session_changed')
            digest = hashlib.sha256(json.dumps(parent, sort_keys=True).encode()).hexdigest()
            ledger = orders.root(host.st.root) / '_requests' / (digest + '.jsonl')
            if ledger.exists() and ledger.stat().st_size > 2_000_000:
                raise orders.OrderError('replay_capacity')
            try:
                raw = ledger.read_text() if ledger.exists() else ''
                rows = [json.loads(line) for line in raw.splitlines()]
                if any(not isinstance(row, dict) or not isinstance(row.get('nonce'), str) for row in rows):
                    raise ValueError('ledger')
            except ValueError as e:
                raise orders.OrderError('replay_ledger') from e
            used = {row['nonce'] for row in rows}
            # Consume before mutations and retain across serve restarts. Never evict
            # evidence to turn a replay into a fresh append/resume/new request.
            if nonce in used or len(used) >= 4096:
                raise orders.OrderError('replay')
            orders._mk(ledger.parent)
            orders._append(ledger, {'nonce': nonce, 'action': args['action']})
            act = args['action']
            root = os.path.realpath(cfg['dir'])
            if act == 'new':
                project = args.get('project')
                if not isinstance(project, str) or not available_project(project, root):
                    raise orders.OrderError('project_not_allowed')
                brief = args.get('brief')
                mode = 'research' if cfg.get('research') else args.get('mode', 'work')
                if orders.brief_problems(brief, mode):
                    raise orders.OrderError('brief_incomplete')
                # Frozen by the host; no model-controlled token/concurrency/grant/fence policy.
                harness = args.get('harness', 'core')
                if harness not in ('core', 'claude', 'codex'):
                    raise orders.OrderError('bad_field')
                model = cfg.get('model') if harness == host.agent.kind or harness == 'core' else None
                # Host-selected native defaults; no model field is accepted from RPC.
                if harness != 'core' and not model:
                    from .presets import providers
                    model = providers()['anthropic' if harness == 'claude' else 'openai']['roles']['ceo']
                o = orders.create(project=os.path.realpath(project), prompt_text=args.get('prompt', ''), brief=brief,
                                  title=args.get('title', ''), parent=parent, model=model, harness=harness,
                                  mode=mode, origin=parent['kind'], st_root=host.st.root, allowed_roots=[root],
                                  fence=cfg.get('fence', True), danger_extra=cfg.get('danger_extra'))
                o.event('origin', parent=parent, source=source, available_root=root)
                text = await asyncio.to_thread(dispatcher.launch, o, st_root=host.st.root)
                return {'result': 'ok', 'id': o.id, 'text': text}
            if not isinstance(args.get('id'), str) or not orders.ID_RE.fullmatch(args['id']):
                raise orders.OrderError('bad_id')
            o = orders.get(args['id'], host.st.root)
            if o.meta.get('parent') != parent or not order_available(o, root):
                raise orders.OrderError('order_owner')
            if act == 'resume' and o.state() == 'budget':
                raise orders.OrderError('budget_requires_owner')
            if act == 'send' and ('gate' in args or 'answer' in args):
                from .order_gates import answer
                n = answer(o, args.get('gate'), args.get('answer'))
                return {'result': 'ok', 'text': f'preference appended #{n}'}
            if act == 'resume':
                o.event('resume_source', source=source)
            text = await asyncio.to_thread(dispatcher.tool, args, parent=parent, allowed_roots=[root],
                                           st_root=host.st.root)
            host.st.log('core_order', action=act, order=o.id, parent=parent)
            return {'result': 'refused' if text.startswith('Error:') else 'ok', 'text': text}
    except (orders.OrderError, OSError, ValueError, TypeError, AttributeError) as e:
        why = e.code if isinstance(e, orders.OrderError) else 'authority_or_shape'
        host.st.log('core_order', result='refused', why=why)
        return {'result': 'refused', 'why': why}


async def pause_all(host):
    """Host emergency stop: latch admission BEFORE waiting for owned-tree exits."""
    dispatcher.set_paused(host.st.root, True)
    async def one(o):
        deadline = time.monotonic() + 30
        while True:
            try:
                await asyncio.to_thread(dispatcher.stop, o)
                return
            except orders.OrderError as e:
                if e.code == 'order_busy' and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                    continue
                host.st.log('core_order_stop_unconfirmed', order=o.id, why=e.code)
                return
    await asyncio.gather(*(one(o) for o in orders.all_orders(host.st.root)))
