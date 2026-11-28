"""#541: authoritative support-read receipts; external material never supplies authority."""
from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import os
import re
import secrets
import sys
import time
from pathlib import Path

from . import approval_review as review

TTL, KEEP = 600, 128
PREFIX = 'AGENTJ-MATERIAL-REF: '
REF = re.compile(r'am_[A-Za-z0-9_-]{32}')
CAPTURE = contextvars.ContextVar('agentj_support_read_capture', default=None)


def _hash(text):
    return hashlib.sha256(text.rstrip('\n').encode()).hexdigest()


def _root(source):
    if not isinstance(source, dict):
        return None
    value = source.get('root_message_id', source.get('message_id'))
    session = source.get('session')
    return (str(session), value) if session is not None and value is not None else None


def _unknown(source, why):
    origin = copy.deepcopy(source or {})
    origin.setdefault('authorization_origin', copy.deepcopy(source or {}))
    for key in ('material_ref', 'trigger_record', 'external_message_id'):
        origin.pop(key, None)
    origin.setdefault('session', '')
    origin.setdefault('root_message_id', None)
    origin.setdefault('initiator', '主 Agent J')
    origin.setdefault('task_record', '')
    origin.setdefault('chain', [])
    origin.update(channel='unknown', sender='', text='无法核证这条外部材料是谁发起的',
                  message_id=None, trigger_message_id=None, suspicious=True,
                  verified=False, material_verified=False, role='unknown_external',
                  material_unknown=why, history_outside_window=False)
    return origin


def _support_thread(tool, inp):
    if tool != 'Bash' or not isinstance(inp, dict) or not isinstance(inp.get('command'), str):
        return None
    from . import danger, support
    commands = danger.lex(inp['command'])
    if len(commands) != 1:
        return None
    words = commands[0].words
    if len(words) < 4 or Path(words[0]).name != 'agentj' or words[1:3] != ['support', 'thread']:
        return None
    return words[3] if support._THREAD.fullmatch(words[3]) else None


def _external(tool, inp):
    if tool in ('WebFetch', 'WebSearch') or isinstance(tool, str) and tool.startswith('mcp__'):
        return True
    if tool == 'Bash' and isinstance(inp, dict) and isinstance(inp.get('command'), str):
        from . import danger
        for cmd in danger.lex(inp['command']):
            for simple in danger.unwrap(cmd):
                if simple.name in ('curl', 'wget', 'http', 'https', 'lynx'):
                    return True
                if simple.name == 'agentj' and simple.args[:2] == ['support', 'thread']:
                    return True
    return False


def _anchor(agent):
    from . import peer_origin
    session = getattr(agent, 'session', None) or {}
    pid = session.get('pid')
    row = peer_origin._stat(Path('/proc'), pid) if type(pid) is int else None
    expected = session.get('procStart')
    if not row or expected is not None and str(expected) != row[2]:
        return None
    return (pid, row[2])


def _unit(host, peer):
    """Reuse #519's existing kernel/signed-launch proof, never a requested unit name."""
    from . import peer_origin
    proc = Path('/proc')
    sender, receiver, ns = peer_origin._stat(proc, peer), peer_origin._stat(proc, os.getpid()), peer_origin._ns(proc)
    launches = getattr(host, 'peer_launch', None)
    if not launches or not sender or not receiver or not ns:
        return None
    origin = {'kind': 'peer', 'fromMode': 'bypass', 'verifiedPeerPid': peer, 'verifiedPeerProcStart': sender[2]}
    session = {'pid': os.getpid(), 'procStart': receiver[2], 'pidDomain': 'host:pid:' + ns}
    unit, _ = launches.verify(origin, session)
    return unit


STEP_MAX = 1.0      # seconds of wall-clock vs continuous-clock drift that void a macOS turn stamp (clock step)


def _continuous():
    """A clock that keeps counting through sleep and is not stepped: CLOCK_BOOTTIME (Linux), CLOCK_MONOTONIC (macOS,
    which unlike Python's time.monotonic() advances while asleep)."""
    if hasattr(time, 'CLOCK_BOOTTIME'):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    if hasattr(time, 'CLOCK_MONOTONIC'):
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    return time.monotonic()


def _drift(wall, cont):
    return abs((time.time() - wall) - (_continuous() - cont))


def turn_start(host, send):
    """R-0052: serve.Host.agent_turn_start is the one place a Send becomes the executing turn (agent.py queue loop and
    shared._merge_queued both call it). Host-held stamp: (send, start in process_evidence.now()'s clock, wall,
    continuous, stepped). `stepped` (macOS): the wall clock moved against the continuous clock since the previous stamp,
    so wall-clock span / birth comparisons around this turn cannot be trusted → every lineage check refuses this turn."""
    from .core.process_evidence import now
    stepped = clock_check(host)
    host.__dict__['_turn_started'] = (send, now(), time.time(), _continuous(), stepped)
    active = host.__dict__.get('_chat_secret_validation')
    if active:
        from . import provenance
        if provenance.owner_turn_contract(src=getattr(send, 'source', None)) != 'host_system_turn':
            active.clear()


def clock_check(host):
    """r2 F1: macOS wall-clock continuity, independent of any Send. A Host-held (wall, continuous) reference is checked
    and advanced on every stamp, every non-owner span open / close and every macOS lineage proof (desktop included);
    > STEP_MAX drift anywhere → the sticky step. Returns whether a step is (now) recorded. Linux: no-op (BOOTTIME)."""
    if sys.platform != 'darwin':
        return False
    wall, cont = time.time(), _continuous()
    ref = host.__dict__.get('_clock_ref')
    if (isinstance(ref, tuple) and len(ref) == 2 and not host.__dict__.get('_clock_step')
            and abs((wall - ref[0]) - (cont - ref[1])) > STEP_MAX):
        _clock_stepped(host)
    host.__dict__['_clock_ref'] = (wall, cont)
    return bool(host.__dict__.get('_clock_step'))


def _clock_stepped(host):
    """r1 F2: a wall-clock step makes every wall-clock birth / span this serve recorded incomparable — not just this
    turn's. Sticky for this serve's life: macOS caller-origin and owner_local lineage refuse from now on (restart clears),
    and the serve-era floor is raised so nothing that existed before the step can ever count as owner-era."""
    from .core.process_evidence import now
    host.__dict__['_clock_step'] = True
    host.__dict__['_nonowner_floor'] = max(host.__dict__.get('_nonowner_floor') or 0.0, now())
    with contextlib.suppress(Exception):
        host.st.log('approval_clock_step')


def _turn_began(host, send):
    """The stamped start of `send`'s turn, or None (no stamp, another Send's stamp, or a macOS clock step)."""
    stamp = host.__dict__.get('_turn_started')
    if not isinstance(stamp, tuple) or len(stamp) != 5 or send is None or stamp[0] is not send:
        return None
    if sys.platform == 'darwin':
        if not host.__dict__.get('_clock_step') and _drift(stamp[2], stamp[3]) > STEP_MAX:
            _clock_stepped(host)
        if clock_check(host):
            return None
    return stamp[1]


def lineage_blocked(host, agent, anchor_born):
    """The refusal half of the caller-origin proof, shared with application_ingress._owner_tree (owner_local), which
    deliberately skips the turn-start rule: the anchor is tainted by a guest turn's running in-process work, a macOS
    clock step happened, or the current Send was never stamped as the executing turn."""
    if clock_check(host):
        return True
    send = getattr(agent, 'cur_send', None)
    if send is not None and _turn_began(host, send) is None:
        return True
    return _anchor_tainted(host, agent, anchor_born)


def _harness_leftovers(host, agent):
    """R-0047+R-0052 r0 F2: work that runs INSIDE the anchor process and can outlive the turn that started it (Codex
    native sub-agent threads in the app-server, Claude background Tasks / shells / pending Agent calls). Host-held
    counters only; a harness the Host cannot count for is treated as having leftovers."""
    bg, session = getattr(agent, 'bg_live', None), getattr(agent, 'session', None)
    if isinstance(session, dict):
        # r1 F1: an empty list proves nothing until Claude Code itself reported this very session's background list
        # (main Stop.background_tasks → shared.bg_known); a fresh serve / re-attach starts unknown.
        generation = (session.get('pid'), session.get('procStart'))
        if (getattr(agent, 'bg_known', None) != generation or session.get('pid') is None
                or getattr(agent, 'bg_overflow', None) == generation):
            return True
        state = getattr(agent, 'state', None)
        if state is not None:                   # r3 F1: foreground sub-agents still open (a fake Stop was held)
            try:
                if state._foreground_open():
                    return True
            except Exception:  # noqa: BLE001 — cannot count → running
                return True
        return (not isinstance(bg, dict) or bool(bg) or bool(getattr(agent, 'sub_pending', None))
                or bool(getattr(agent, 'bg_nested', None)))
    scope = getattr(agent, 'busy_scope', None)
    board = getattr(getattr(host, 'busy', None), 'mem', None)
    if not callable(scope) or not isinstance(board, dict):
        return True
    try:
        name = scope()
        return any(':th:' in k and isinstance(v, dict) and v.get('session') == name for k, v in list(board.items()))
    except Exception:  # noqa: BLE001 — cannot count → leftovers
        return True


def _anchor_tainted(host, agent, anchor_born):
    """F2: a non-owner turn ran while this anchor generation (pid + start) was alive — or before this serve remembers —
    and harness-internal work is still running: that work may be the guest's, so no caller under this anchor is the
    owner's. Ends when the leftovers end, /stop ends them, or the anchor exits (a new generation has no such history)."""
    from . import application_ingress
    margin = application_ingress.SPAN_MARGIN
    floor = host.__dict__.get('_nonowner_floor')
    spans = host.__dict__.get('_nonowner_spans') or []
    since = anchor_born
    session, clean = getattr(agent, 'session', None), getattr(agent, 'bg_clean', None)
    if isinstance(session, dict):
        # r3 F1: live work this serve never saw start (unseen agent_id) and an overflowed inventory are of unknown
        # origin — tainted unconditionally, never filtered through the clean point or the guest-span window. Unseen work
        # clears only on its own SubagentStop; overflow only with a new session generation.
        nested, generation = getattr(agent, 'bg_nested', None), (session.get('pid'), session.get('procStart'))
        current = getattr(agent, 'bg_nested_gen', generation) == generation
        if (getattr(agent, 'bg_overflow', None) == generation
                or (current and isinstance(nested, dict) and {'unseen', 'unresolved'} & set(nested.values()))):
            return True
    if (isinstance(session, dict) and isinstance(clean, tuple) and len(clean) == 3
            and clean[:2] == (session.get('pid'), session.get('procStart')) and isinstance(clean[2], (int, float))
            and not (sys.platform == 'darwin' and host.__dict__.get('_clock_step'))):
        since = max(since, clean[2])      # Claude Code reported nothing in-process running here at that instant
    overlapped = ((floor is None or since < floor + margin)
                  or any(end is None or end + margin >= since for _, end in spans))
    return overlapped and _harness_leftovers(host, agent)


def _owned_process(process, pid):
    import asyncio
    import subprocess
    return (isinstance(process, (asyncio.subprocess.Process, subprocess.Popen)) and process.pid == pid
            and process.returncode is None)


def _fence_init(supervisor, child):
    """Identify the kernel PID 1 of our bwrap --unshare-pid --as-pid-1 launch."""
    proc = Path('/proc')
    try:
        if not (proc / str(supervisor) / 'exe').samefile('/usr/bin/bwrap'):
            return False
        def namespace_pids(pid):
            return next(line.split()[1:] for line in (proc / str(pid) / 'status').read_text().splitlines()
                        if line.startswith('NSpid:'))
        parent_ids, child_ids = namespace_pids(supervisor), namespace_pids(child)
        return bool(parent_ids and len(child_ids) == len(parent_ids) + 1 and child_ids[-1] == '1'
                    and not (proc / str(child) / 'ns/pid').samefile(proc / str(supervisor) / 'ns/pid'))
    except (OSError, StopIteration):
        return False


def _direct_scope(host, peer, *, secret_input=False):
    """Independent harness/new-turn descendants and registered CEOs have host-held roots without Claude hooks."""
    from . import busy_launch, peer_origin
    if sys.platform == 'darwin':
        return _darwin_scope(host, peer)
    agent = getattr(host, 'agent', None)
    unit = _unit(host, peer)
    if unit:
        source = host.__dict__.get('_approval_launch_refs', {}).get(unit)
        if _root(source) is not None:
            return {'kind': 'unit', 'unit': unit, 'origin': copy.deepcopy(source),
                    'peer': (peer, peer_origin._stat(Path('/proc'), peer)[2])}
    send, process = getattr(agent, 'cur_send', None), getattr(agent, 'proc', None)
    session = getattr(agent, 'session', None)
    desktop = False
    if isinstance(session, dict):
        pid = session.get('pid')
        if getattr(agent, 'phone_turn', False):
            if not getattr(agent, 'phone_seen', False):
                return None  # queued owner input is not the native turn currently executing
            source, created = getattr(send, 'approval_origin', None), getattr(send, 'created', None)
        else:
            desktop = True
            source = host.__dict__.get('_approval_origins', {}).get(getattr(host, 'desktop_turn', None))
            try:
                created = float(source.get('host_received_monotonic'))
            except (ValueError, TypeError, AttributeError):
                return None
    else:
        pid, created = getattr(process, 'pid', None), getattr(send, 'created', None)
        source = getattr(send, 'approval_origin', None)
    table = busy_launch.procs()
    row = table.get(pid)
    if _root(source) is None or type(peer) is not int or not row or not isinstance(created, (int, float)) or \
            peer_origin._uid(Path('/proc'), peer) != os.getuid():
        return None
    if isinstance(session, dict):
        if str(session.get('procStart')) != row[2]:
            return None
    else:
        pins = getattr(getattr(host, 'busy', None), 'context', {})
        board_pin = any(value.get('pid') == pid and value.get('proc_start') == row[2]
                        for value in pins.values() if isinstance(value, dict))
        # A fresh Host-launched harness can request its first secret before its busy-board event is processed.
        # The kernel's direct, still-unreaped child is an exact process-generation pin in its own right.
        child_pin = (secret_input and row[0] == os.getpid() and _owned_process(process, pid)
                     and source.get('channel') == 'phone' and source.get('verified') is True
                     and not source.get('suspicious') and source.get('message_id') == getattr(send, 'turn', None)
                     and source == host.__dict__.get('_approval_origins', {}).get(getattr(send, 'turn', None)))
        if not (board_pin or child_pin) or getattr(process, 'returncode', None) is not None:
            return None
    # /proc starttime uses boot-time ticks. Convert the host-created monotonic task stamp to that clock;
    # a suspend between creation and this observation shifts the threshold later, conservatively rejecting ambiguity.
    if not hasattr(time, 'CLOCK_BOOTTIME'):
        return None
    began = created + (time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic())
    stamp = None if desktop else _turn_began(host, send)
    if stamp is not None:
        began = max(began, stamp)
    from . import application_ingress
    if host.__dict__.get('_nonowner_floor') is None:     # same lazy floor as application_ingress._owner_tree: serve-era only
        me = table.get(os.getpid())
        if not me:
            return None
        host.__dict__['_nonowner_floor'] = int(me[2]) / busy_launch.HZ
    current, seen = peer, set()
    # R-0052: Send.created is the QUEUE time; the turn starts when this Send became cur_send (serve.agent_turn_start
    # stamps it, host-held). No stamp / another Send's stamp proves nothing → ambiguous (`unknown_agent`: refused as a
    # caller, and still the stricter material-receipt outcome, never a silent None).
    ambiguous = (not desktop and stamp is None) or _anchor_tainted(host, agent, int(row[2]) / busy_launch.HZ)
    # A namespace init may adopt children. Only a host-recorded process generation with wholly
    # known owner-only history can identify it as the persistent root, even on an empty busy board.
    anchor_born = int(row[2]) / busy_launch.HZ
    clean_generation = (secret_input and anchor_born >= host.__dict__['_nonowner_floor']
                        and not any(end is None or end + application_ingress.SPAN_MARGIN >= anchor_born
                                    for _, end in host.__dict__.get('_nonowner_spans') or ()))
    while current > 1 and current != pid and current not in seen and len(seen) < 64:
        seen.add(current)
        branch = table.get(current)
        if not branch:
            return None
        born = int(branch[2]) / busy_launch.HZ
        # R-0052: a hop born inside any host-held non-owner span (friend / guest / external / webhook turn on this anchor,
        # including a span still open when the owner's Send was merged into it) is guest-born, never the owner's caller.
        # The Host owns the outer supervisor. Only its unique namespace init is part of the persistent
        # harness itself; the peer and every other child still need fresh, non-guest process generations.
        fenced_harness = (secret_input and current != peer and branch[0] == pid and not desktop
                          and not isinstance(session, dict) and child_pin and clean_generation and _fence_init(pid, current))
        ambiguous |= (born < began and not fenced_harness) or application_ingress._guest_born(host, born)
        current = branch[0]
    if current != pid or peer not in table:
        return None
    return {'kind': 'unknown_agent' if ambiguous or not seen else 'desktop' if desktop else 'agent',
            'desktop': desktop, 'desktop_turn': getattr(host, 'desktop_turn', None),
            'agent': agent, 'send': send, 'anchor': (pid, row[2]),
            **({'owned_process': process} if not isinstance(session, dict) and not board_pin else {}),
            'peer': (peer, table[peer][2]), 'origin': copy.deepcopy(source)}



def _darwin_scope(host, peer):
    """Exact current-turn descendants only; an old background child cannot borrow a newer owner."""
    from .core.process_evidence import darwin_row, start_matches
    agent = getattr(host, 'agent', None)
    session, send = getattr(agent, 'session', None), getattr(agent, 'cur_send', None)
    if type(peer) is not int:
        return None
    if not isinstance(session, dict):
        return _darwin_owned_scope(host, agent, send, peer)
    desktop = not getattr(agent, 'phone_turn', False)
    if not desktop and not getattr(agent, 'phone_seen', False):
        return None
    source = (host.__dict__.get('_approval_origins', {}).get(getattr(host, 'desktop_turn', None)) if desktop
              else getattr(send, 'approval_origin', None))
    try:
        created = float(source['host_received_monotonic']) if desktop else float(send.created)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    anchor = session.get('pid')
    row = darwin_row(anchor)
    if _root(source) is None or str(host.hist.epoch) != str(source.get('session')) or not start_matches(session.get('procStart'), row):
        return None
    began = time.time() - (time.monotonic() - created)
    if not desktop:
        stamp = _turn_began(host, send)       # R-0052 r0 F1: the turn start, not the queue time (Send.created)
        if stamp is None:
            return None
        began = max(began, stamp)
    chain = _darwin_chain(host, agent, peer, anchor, row, began)
    if chain is None:
        return None
    return {'platform': 'darwin', 'kind': 'desktop' if desktop else 'agent', 'desktop': desktop,
            'desktop_turn': getattr(host, 'desktop_turn', None), 'agent': agent, 'send': send,
            'anchor': (anchor, row), 'peer': (peer, chain[0][1]), 'origin': copy.deepcopy(source)}

def _darwin_chain(host, agent, peer, anchor, row, began):
    """Every hop from `peer` up to `anchor` started after `began`, none inside a Host-held non-owner span, the anchor not
    tainted by a guest turn's still-running in-process work (R-0052 r0 F1/F2, the Linux twin); rows re-read unchanged."""
    from . import application_ingress
    from .core.process_evidence import darwin_row
    if clock_check(host):                                 # r1/r2 F2/F1: wall-clock births are incomparable since a step
        return None
    if host.__dict__.get('_nonowner_floor') is None:     # serve-era only, as application_ingress._owner_tree
        me = darwin_row(os.getpid())
        if not me:
            return None
        host.__dict__['_nonowner_floor'] = me[1]
    if _anchor_tainted(host, agent, row[1]):
        return None
    current, chain = peer, []
    while current != anchor and type(current) is int and current > 1 and len(chain) < 64:
        if current in {p for p, _ in chain}:
            return None
        child = darwin_row(current)
        if not child or child[1] < began or application_ingress._guest_born(host, child[1]):
            return None
        chain.append((current, child))
        current = child[0]
    if current != anchor or not chain or darwin_row(anchor) != row or any(darwin_row(p) != v for p, v in reversed(chain)):
        return None
    return chain


def _darwin_owned_scope(host, agent, send, peer):
    """R-0047: a Host-spawned harness (Codex app-server, own Claude, OpenCode) has no shared-session `procStart` anchor.

    Same facts as the Linux owned-process branch, all Host-held: the turn being delivered (`cur_send`, cleared at turn
    end) and its host-written `approval_origin`; the anchor is the Host's own live, unreaped child (`proc`, parent ==
    this process, so its PID cannot have been reused); every hop from the caller up to it started after the turn.
    """
    from .core.process_evidence import darwin_row
    process = getattr(agent, 'proc', None)
    anchor, source = getattr(process, 'pid', None), getattr(send, 'approval_origin', None)
    try:
        created = float(send.created)
    except (TypeError, ValueError, AttributeError):
        return None
    if type(anchor) is not int or anchor <= 1 or getattr(process, 'returncode', 0) is not None:
        return None
    row = darwin_row(anchor)
    if not row or row[0] != os.getpid() or _root(source) is None or str(host.hist.epoch) != str(source.get('session')):
        return None
    stamp = _turn_began(host, send)           # R-0052 r0 F1: the turn start, not the queue time (Send.created)
    if stamp is None:
        return None
    began = max(time.time() - (time.monotonic() - created), stamp)
    chain = _darwin_chain(host, agent, peer, anchor, row, began)
    if chain is None:
        return None
    return {'platform': 'darwin', 'kind': 'agent', 'desktop': False, 'desktop_turn': getattr(host, 'desktop_turn', None),
            'agent': agent, 'send': send, 'anchor': (anchor, row), 'peer': (peer, chain[0][1]),
            'origin': copy.deepcopy(source)}


def _scope_live(host, scope, peer):
    if not isinstance(scope, dict):
        return False
    if scope.get('platform') == 'darwin':
        current = _darwin_scope(host, peer)
        return (current is not None and current.get('agent') is scope.get('agent')     # r0 F5/F6: identity, not ==
                and current.get('send') is scope.get('send')
                and all(current.get(k) == scope.get(k) for k in ('desktop_turn', 'anchor', 'peer', 'origin')))
    from . import peer_origin
    row = peer_origin._stat(Path('/proc'), peer)
    if not row or scope['peer'] != (peer, row[2]) or str(host.hist.epoch) != str(scope['origin'].get('session')):
        return False
    if scope['kind'] == 'unit':
        source = host.__dict__.get('_approval_launch_refs', {}).get(scope['unit'])
        return _unit(host, peer) == scope['unit'] and _root(source) == _root(scope['origin'])
    if 'owned_process' in scope:
        process = scope['owned_process']
        root = peer_origin._stat(Path('/proc'), scope['anchor'][0])
        if (host.agent is not scope['agent'] or getattr(host.agent, 'proc', None) is not process or not root
                or root[1] != os.getpid() or not _owned_process(process, scope['anchor'][0])):
            return False
    if scope.get('desktop'):
        source = host.__dict__.get('_approval_origins', {}).get(getattr(host, 'desktop_turn', None))
        return host.agent is scope['agent'] and getattr(host, 'desktop_turn', None) == scope['desktop_turn'] and \
            _descendant(peer, scope['anchor']) and _root(source) == _root(scope['origin'])
    return host.agent is scope['agent'] and getattr(host.agent, 'cur_send', None) is scope['send'] and \
        _descendant(peer, scope['anchor']) and _root(getattr(scope['send'], 'approval_origin', None)) == _root(scope['origin'])


def _descendant(peer, anchor):
    from . import peer_origin
    if type(peer) is not int or not anchor or peer_origin._uid(Path('/proc'), peer) != os.getuid():
        return None
    first = peer_origin._stat(Path('/proc'), peer)
    if not first:
        return None
    current, seen = peer, set()
    while current > 1 and current not in seen and len(seen) < 64:
        seen.add(current)
        row = peer_origin._stat(Path('/proc'), current)
        if not row:
            return None
        if current == anchor[0]:
            return (peer, first[2]) if row[2] == anchor[1] else None
        current = row[1]
    return None


class Capture(list):
    pass


@contextlib.contextmanager
def capture(host=None, peer=None, request=None):
    """Entered only by the fixed serve-side read broker, never from client request fields."""
    rows = Capture()
    rows.scope = _direct_scope(host, peer) if host is not None and request.get('op') == 'thread' else None
    token = CAPTURE.set(rows)
    try:
        yield rows
    finally:
        CAPTURE.reset(token)


def observed_response(thread, status, view):
    rows = CAPTURE.get()
    if rows is None or not 200 <= status < 300 or not isinstance(view, dict):
        return
    if not isinstance(view.get('thread'), dict) or view['thread'].get('id') != thread:
        return
    messages = view.get('messages')
    if not isinstance(messages, list):
        return
    for message in messages[-32:]:
        if not isinstance(message, dict) or message.get('author') not in ('support', 'system'):
            continue
        text = review.safe_text(message.get('body'), 1200)
        if not text:
            continue
        remote = message.get('id') or message.get('seq')
        rows.append({'thread': thread, 'text': text, 'remote_id': review.safe_text(remote, 100),
                     'at': review.safe_text(message.get('created_at'), 40),
                     'response_sha256': review.digest(view)})
        del rows[:-32]


def finish_capture(host, captured, peer, request, response):
    """Attach a receipt only to host-fetched output matching a pinned pending tool call."""
    if request.get('op') != 'thread' or not isinstance(response, dict) or response.get('exit') != 0 or not captured:
        return response
    now = time.monotonic()
    pending = host.__dict__.get('_approval_material_pending', {})
    candidates = [(key, entry) for key, entry in pending.items() if entry['until'] > now and
                  entry['session'] == str(host.hist.epoch) and entry['thread'] == request.get('thread') and
                  _descendant(peer, entry['anchor'])]
    matching = [row for row in captured if row['thread'] == request.get('thread')]
    if not matching:
        return response
    direct = getattr(captured, 'scope', None)
    if not candidates and direct is not None and direct['kind'] == 'unknown_agent' and _scope_live(host, direct, peer):
        source = _unknown(direct['origin'], 'caller_predates_active_task')
        if direct.get('desktop'):
            host.__dict__['_approval_origins'][direct['desktop_turn']] = source
        else:
            direct['send'].approval_origin = source
        return response
    if len(candidates) == 1 and _root(candidates[0][1]['origin']) is not None:
        key, entry = candidates[0]
    elif not candidates and direct is not None and _scope_live(host, direct, peer):
        key = None
        entry = {'origin': direct['origin'], 'native_session': None,
                 'input_sha256': review.digest(request), 'until': now + TTL}
    else:
        return response
    record = {'origin': copy.deepcopy(entry['origin']), 'session': str(host.hist.epoch),
              'native_session': entry['native_session'], 'input_sha256': entry['input_sha256'],
              'pending_keys': [key for key, _ in candidates],
              'peer': direct['peer'] if key is None else _descendant(peer, entry['anchor']),
              'until': min(entry['until'], now + TTL), 'request_id': secrets.token_hex(16),
              'response': copy.deepcopy(matching[-1])}
    ref = 'am_' + secrets.token_urlsafe(24)
    output = str(response.get('output') or '').rstrip('\n') + '\n' + PREFIX + ref + '\n'
    record['output_sha256'] = _hash(output)
    records = host.__dict__.setdefault('_approval_material_receipts', {})
    records[ref] = record
    while len(records) > KEEP:
        records.pop(next(iter(records)))
    if key is None:
        source = _publish(host, record, ref)
        if direct['kind'] == 'unit':
            host.__dict__['_approval_launch_refs'][direct['unit']] = copy.deepcopy(source)
        elif direct['kind'] == 'desktop':
            host.__dict__['_approval_origins'][direct['desktop_turn']] = copy.deepcopy(source)
        else:
            direct['send'].approval_origin = copy.deepcopy(source)
    return {**response, 'output': output}


def _output(event):
    value = event.get('tool_response')
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('stdout', 'output'):
            if isinstance(value.get(key), str):
                return value[key]
        if isinstance(value.get('content'), list):
            text = [item.get('text', '') for item in value['content'] if isinstance(item, dict) and item.get('type') == 'text']
            if len(text) == 1:
                return text[0]
    return ''


def _apply(host, agent, aid, source):
    if aid:
        agent.child_approval_origins[aid] = copy.deepcopy(source)
    elif getattr(agent, 'cur_send', None) is not None and (not isinstance(getattr(agent, 'session', None), dict) or
            getattr(agent, 'phone_turn', False) and getattr(agent, 'phone_seen', False)):
        agent.cur_send.approval_origin = copy.deepcopy(source)
    else:
        tid = getattr(host, 'desktop_turn', None)
        if tid is not None:
            host.__dict__.setdefault('_approval_origins', {})[tid] = copy.deepcopy(source)


def observe(host, agent, event):
    """Only called after shared's authenticated session-id check, never on raw CLI input."""
    if not hasattr(host, 'hist'):
        return  # Legacy unit fixtures without a real Host cannot mint provenance.
    name, aid = event.get('hook_event_name'), event.get('agent_id')
    tool, inp, tid = event.get('tool_name'), event.get('tool_input') or {}, event.get('tool_use_id')
    if name not in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure') or not isinstance(tid, str) or len(tid) > 200:
        return
    pending = host.__dict__.setdefault('_approval_material_pending', {})
    key = (event.get('session_id'), aid, tid)
    if name == 'PreToolUse':
        thread = _support_thread(tool, inp)
        if not thread and not _external(tool, inp):
            return
        origin = review.snapshot(host) or {}
        pending[key] = {'origin': copy.deepcopy(origin), 'input_sha256': review.digest([tool, inp]),
                        'session': str(host.hist.epoch), 'native_session': event.get('session_id'),
                        'anchor': _anchor(agent), 'thread': thread, 'until': time.monotonic() + TTL}
        while len(pending) > KEEP:
            pending.pop(next(iter(pending)))
        return
    entry = pending.pop(key, None)
    if entry is None:
        if name == 'PostToolUse' and _external(tool, inp):
            _apply(host, agent, aid, _unknown(review.snapshot(host), 'missing_tool_request'))
        return
    if name != 'PostToolUse':
        return
    origin = entry['origin']
    current = review.snapshot(host) or {}
    output = _output(event)
    refs = re.findall(r'(?m)^' + re.escape(PREFIX) + r'(am_[A-Za-z0-9_-]{32})$', output)
    record = host.__dict__.get('_approval_material_receipts', {}).get(refs[0]) if len(refs) == 1 else None
    valid = record is not None and key in record['pending_keys'] and entry['until'] > time.monotonic() and \
        record['until'] > time.monotonic() and record['session'] == str(host.hist.epoch) and \
        record['native_session'] == event.get('session_id') and record['input_sha256'] == review.digest([tool, inp]) and \
        record['output_sha256'] == _hash(output) and record.get('consumed_key') in (None, key) and \
        _root(origin) is not None and _root(origin) == _root(current)
    if not valid:
        return _apply(host, agent, aid, _unknown(current, 'unverified_tool_material'))
    source = _publish(host, record, refs[0])
    record['consumed_key'] = key
    _apply(host, agent, aid, source)


def _publish(host, record, ref):
    material, origin = record['response'], record['origin']
    # A local host record is a navigation anchor; the external message id is diagnostic only.
    turn = host.hist_add({'k': 'support', 'name': '客服（外部材料）', 'text': material['text']}, '', 'done')
    source = copy.deepcopy(origin)
    source.setdefault('authorization_origin', copy.deepcopy(origin))
    source.update(message_id=turn['id'], trigger_message_id=turn['id'], channel='support', verified=False, material_verified=True,
                  sender='客服', role='external_advice', text=material['text'], time=turn['ts'], suspicious=True,
                  history_outside_window=False, external_message_id=material['remote_id'],
                  material_ref=ref, task_record=f'support:{material["thread"]}:{material["remote_id"]}')
    source['trigger_record'] = {'ref': ref, 'message_id': turn['id'], 'channel': 'support',
        'role': 'external_advice', 'text': material['text'], 'external_message_id': material['remote_id'],
        'remote_message_known': bool(material['remote_id']), 'human_origin_known': False}
    record['source'] = copy.deepcopy(source)
    return source


def snapshot(host, source):
    """Resolve only an inherited host-created receipt. Missing/expired proof never restores an owner trigger."""
    if not isinstance(source, dict):
        return None
    if source.get('material_unknown'):
        return _unknown(source, source['material_unknown'])
    ref = source.get('material_ref')
    if not ref:
        return None
    record = host.__dict__.get('_approval_material_receipts', {}).get(ref)
    if not record or record.get('source') is None or record['until'] <= time.monotonic() or \
            record['session'] != str(host.hist.epoch) or _root(source) != _root(record['origin']):
        return _unknown(source, 'material_proof_expired_or_mismatched')
    resolved = copy.deepcopy(record['source'])
    resolved['chain'] = copy.deepcopy(source.get('chain', []))
    return resolved


def verified_caller_origin(host, peer):
    """Existing fixed brokers use the same live kernel/root proof; no mutable latest-owner fallback."""
    scope = _direct_scope(host, peer)
    if scope is None or scope['kind'] == 'unknown_agent' or not _scope_live(host, scope, peer):
        return None
    source = copy.deepcopy(scope['origin'])
    resolved = snapshot(host, source)
    return resolved if resolved is not None else source


def pickup_caller_origin(host, peer):
    """Preserve known external provenance when the caller cannot prove the current turn."""
    origin = verified_caller_origin(host, peer)
    if origin is not None or type(peer) is not int:
        return origin

    from . import approval_special
    from .core import process_evidence

    agent = host.agent
    send, session = getattr(agent, 'cur_send', None), getattr(agent, 'session', None)
    if isinstance(session, dict):
        anchor = session.get('pid')
        pin = process_evidence.row(anchor)
        if not process_evidence.start_matches(session.get('procStart'), pin):
            return None
    else:
        process = getattr(agent, 'proc', None)
        anchor = getattr(process, 'pid', None)
        pin = process_evidence.row(anchor)
        if not pin or pin[0] != os.getpid() or getattr(process, 'returncode', None) is not None:
            return None

    # Parentage alone can deny a pickup, but does not prove the owner's current turn.
    chain = {}
    pid = peer
    while pid != anchor and pid > 1 and pid not in chain and len(chain) < 64:
        row = process_evidence.row(pid)
        if not row:
            return None
        chain[pid] = row
        pid = row[0]
    if (pid != anchor or process_evidence.row(anchor) != pin
            or any(process_evidence.row(child) != row for child, row in chain.items())
            or host.agent is not agent or getattr(agent, 'session', None) is not session
            or getattr(agent, 'cur_send', None) is not send):
        return None
    if isinstance(session, dict):
        if getattr(agent, 'phone_turn', False):
            source = getattr(send, 'approval_origin', None) if getattr(agent, 'phone_seen', False) else None
        else:
            source = host.__dict__.get('_approval_origins', {}).get(getattr(host, 'desktop_turn', None))
    else:
        source = getattr(send, 'approval_origin', None)
    if (source and str(source.get('session')) == str(host.hist.epoch)
            and not approval_special.pickup_source_allowed(source)):
        return _unknown({'session': str(host.hist.epoch)}, 'pickup_lineage_unverified')
    return None


async def observe_phone_delivery(host, peer):
    """Let the served adapter consume a pending original phone record before fixed CLI gates."""
    observe = getattr(getattr(host, 'agent', None), 'observe_phone_delivery', None)
    if peer is not None and callable(observe):
        await observe(peer)


def verified_phone_chat(host, peer):
    """Only a live chat descendant of the exact host-recorded phone Send can carry this proof."""
    return _verified_phone_chat(host, peer, feedback_pairing_live)


def _verified_phone_chat(host, peer, pairing_live):
    scope = _direct_scope(host, peer, secret_input=True)
    if scope is None or scope['kind'] != 'agent' or not _scope_live(host, scope, peer):
        return None
    original = scope['origin']
    turn = getattr(scope['send'], 'turn', None)
    if type(turn) is not int or original != host.__dict__.get('_approval_origins', {}).get(turn):
        return None
    origin = snapshot(host, original) or original
    if (origin.get('channel') != 'phone' or origin.get('verified') is not True or origin.get('suspicious')
            or origin.get('message_id') != turn or str(origin.get('session')) != str(host.hist.epoch)):
        return None
    agent = scope['agent']
    if (not phone_main_live(agent) or not pairing_live(host, scope['send'])
            or getattr(scope['send'], 'state', None) in ('cancelled', 'failed')):
        return None
    return agent, copy.deepcopy(origin)


def phone_main_live(agent):
    from . import codex_children
    if getattr(agent, 'kind', None) != 'codex':
        return True
    done = getattr(agent, 'turn_done', None)
    return not (done is not None and done.is_set()) and not codex_children.phone_caller_ambiguous(agent)


def verified_phone_feedback(host, peer, *, pairing_live=None):
    """Use the existing exact phone proof for a Host-owned native process."""
    from . import provenance
    agent = getattr(host, 'agent', None)
    send = getattr(agent, 'cur_send', None)
    if (send is None or isinstance(getattr(agent, 'session', None), dict)
            or provenance.normalize(getattr(send, 'source', None)) != provenance.phone()
            or provenance.untrusted_turn(agent) or provenance.owner_grant_required(agent)
            or getattr(send, 'state', None) in ('cancelled', 'failed')):
        return None
    proof = _verified_phone_chat(host, peer, pairing_live or feedback_pairing_live)
    return proof[1] if proof is not None and proof[0] is agent else None


def feedback_pairing_live(host, send):
    """Recheck any identity pinned at Host's authenticated phone ingress."""
    if not hasattr(send, 'phone_binding'):
        return True
    from .browser_login import RequestPhone
    binding = send.phone_binding
    return isinstance(binding, RequestPhone) and binding.device == send.device and binding.still_paired(host)
