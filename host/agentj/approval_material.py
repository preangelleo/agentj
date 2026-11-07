"""#541: authoritative support-read receipts; external material never supplies authority."""
from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import os
import re
import secrets
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


def _direct_scope(host, peer):
    """Independent harness/new-turn descendants and registered CEOs have host-held roots without Claude hooks."""
    from . import busy_launch, peer_origin
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
        if not any(value.get('pid') == pid and value.get('proc_start') == row[2]
                   for value in pins.values() if isinstance(value, dict)) or getattr(process, 'returncode', None) is not None:
            return None
    # /proc starttime uses boot-time ticks. Convert the host-created monotonic task stamp to that clock;
    # a suspend between creation and this observation shifts the threshold later, conservatively rejecting ambiguity.
    if not hasattr(time, 'CLOCK_BOOTTIME'):
        return None
    began = created + (time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic())
    current, seen, ambiguous = peer, set(), False
    while current > 1 and current != pid and current not in seen and len(seen) < 64:
        seen.add(current)
        branch = table.get(current)
        if not branch:
            return None
        ambiguous |= int(branch[2]) / busy_launch.HZ < began
        current = branch[0]
    if current != pid or peer not in table:
        return None
    return {'kind': 'unknown_agent' if ambiguous or not seen else 'desktop' if desktop else 'agent',
            'desktop': desktop, 'desktop_turn': getattr(host, 'desktop_turn', None),
            'agent': agent, 'send': send, 'anchor': (pid, row[2]),
            'peer': (peer, table[peer][2]), 'origin': copy.deepcopy(source)}


def _scope_live(host, scope, peer):
    from . import peer_origin
    row = peer_origin._stat(Path('/proc'), peer)
    if not row or scope['peer'] != (peer, row[2]) or str(host.hist.epoch) != str(scope['origin'].get('session')):
        return False
    if scope['kind'] == 'unit':
        source = host.__dict__.get('_approval_launch_refs', {}).get(scope['unit'])
        return _unit(host, peer) == scope['unit'] and _root(source) == _root(scope['origin'])
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
