"""Host-held display origins for shared native turn boundaries; no grants or persistence."""
import copy
import re
import secrets
from dataclasses import dataclass
from . import provenance, native_owner_launch


def _valid(agent, origin):
    epoch = str(getattr(getattr(agent.host, 'hist', None), 'epoch', ''))
    return isinstance(origin, dict) and origin.get('session') == epoch and origin.get('verified') is True and not origin.get('suspicious')


@dataclass
class TranscriptAttestation:
    session_id: str
    request_id: str
    nonce: str
    record_id: str
    body: str
    source: dict
    send: object
    consumed: bool = False


def mint(agent, send, body):
    sid = (agent.session or {}).get("sessionId")
    rid = getattr(send, "sid", None)
    if not all(isinstance(x, str) and x for x in (sid, rid, body)):
        return None
    # record_id is the host frame id.  claude preserves it as origin.msg_id;
    # the transcript uuid is Claude-owned and therefore deliberately ignored.
    return TranscriptAttestation(sid, rid, secrets.token_hex(16), secrets.token_hex(16), body,
                                 provenance.normalize(getattr(send, "source", None)), send)


def delivered(agent, item, text):
    """Admit only the host-held, record-level attestation for this delivery."""
    att = getattr(agent, "native_request", None)
    sid = (agent.session or {}).get("sessionId")
    if not isinstance(att, TranscriptAttestation) or att.consumed or sid != att.session_id:
        return
    if (getattr(agent, "cur_send", None) is not att.send
            or getattr(agent, "pending_source", None) != att.source
            or getattr(agent, "pending_text", None) != att.body):
        return
    origin = item.get("origin") if isinstance(item, dict) else None
    if (not isinstance(item, dict) or item.get("sessionId") != att.session_id
            or item.get("isMeta") is not True
            or not isinstance(origin, dict) or origin.get("kind") != "peer"
            or origin.get("msg_id") != att.record_id
            or any(item.get(k) for k in ("synthetic", "ignored", "isCompactSummary", "isSidechain"))):
        return
    expected = re.compile(
        r'<cross-session-message from="uds:[^"\n]*" from-name="'
        + re.escape(provenance.from_name(att.source) + '" ' + provenance.attributes(att.source) + '>\n'
                    + provenance.header(att.source) + '\n')
        + re.escape(att.body)
        + re.escape('\n</cross-session-message>')
    )
    # The transcript item is the record boundary.  Require the complete item
    # to be exactly the host envelope; prefixes/suffixes are another record or
    # an injection and must never satisfy this attestation.
    if expected.fullmatch(text) is None:
        return
    att.consumed = True
    agent.native_source = att.source
    agent.phone_seen = True
    agent.input_seen.set()
    if agent.untrusted_state == "pending":
        agent._mark_untrusted("running", not provenance.unattended_send(att.send))
    origin = getattr(att.send, "approval_origin", None)
    if provenance.is_owner(att.source) and not agent.untrusted() and _valid(agent, origin):
        agent._owner_approval_origin = (sid, copy.deepcopy(origin))
    return True

def prepare(agent, source):
    """A fresh Host system delivery may resume only its session's proven owner context."""
    agent._native_approval_origin = None
    agent._native_owner_launch = None
    owner = agent.__dict__.get('_owner_approval_origin')
    if (source == provenance.system() and not agent.untrusted() and owner
            and owner[0] == (agent.session or {}).get('sessionId') and _valid(agent, owner[1])):
        agent.cur_send.approval_origin = copy.deepcopy(owner[1])


def launched(agent, name, event):
    if (name in ('SessionEnd', 'SessionStart') and not event.get('agent_id')
            and event.get('session_id') == (agent.session or {}).get('sessionId')):
        agent.__dict__.pop('_native_task_origins', None)
        agent.__dict__.pop('_owner_approval_origin', None)
        agent._native_approval_origin = None
        agent._native_owner_launch = None
        agent.__dict__.pop('_owner_launch_pending',None)
        agent.__dict__.pop('_owner_background_calls',None)
        agent.__dict__.pop('_owner_cancelled_tasks',None)
        agent.__dict__.pop('_owner_cancel_overflow',None)
        return
    if name == 'PreToolUse' and not event.get('agent_id'):
        from . import approval_review
        native_owner_launch.before(agent,event,approval_review.snapshot(agent.host))
        return
    if name not in ('PostToolUse','PostToolUseFailure') or event.get('agent_id'):
        return
    response = event.get('tool_response') or {}
    if not isinstance(response, dict):
        return
    if event.get('tool_name') in ('TaskStop','KillShell','KillBash'):
        inp=event.get('tool_input') or {}
        for task in (inp.get('task_id'),inp.get('shell_id'),inp.get('bash_id')):
            key=f'shared-{agent.kind}:{(agent.session or {}).get("sessionId")}:{task}'
            cancelled=agent.__dict__.setdefault('_owner_cancelled_tasks',set())
            if len(cancelled)<4096:cancelled.add(key)
            elif key not in cancelled:agent._owner_cancel_overflow=True
            row=agent.__dict__.get('_native_task_origins',{}).get(key)
            if row is not None:row['owner_launch']=None
            active=getattr(agent,'_native_owner_launch',None)
            if active and any(k==key for _,k,_ in active[1]):agent._native_owner_launch=None
    tid = response.get('backgroundTaskId') or (response.get('agentId') if response.get('isAsync') or response.get('status') == 'async_launched' else None)
    exact=native_owner_launch.after(agent,event,isinstance(tid,str) and bool(tid))
    if name!='PostToolUse':exact=None
    agent._native_launch_decision=(event,bool(exact and exact['trusted']))
    if not isinstance(tid, str) or not tid:
        return
    origin = exact['origin'] if exact is not None else None
    owner_launch = exact['owner_launch'] if exact is not None else None
    sid = (agent.session or {}).get('sessionId')
    key = f'shared-{agent.kind}:{sid}:{tid}'
    if agent.__dict__.get('_owner_cancel_overflow') or key in agent.__dict__.get('_owner_cancelled_tasks',set()):
        origin=owner_launch=None
        agent._native_launch_decision=(event,False)
    # This runs before Board.hook_event; save the origin with the exact native
    # launch and resolve its generation after the Board commits that same event.
    rows = agent.__dict__.setdefault('_native_task_origins', {})
    rows[key] = {'origin': copy.deepcopy(origin), 'generation': None, 'owner_launch':owner_launch,'trusted':bool(exact and exact['trusted']) and not agent.__dict__.get('_owner_cancel_overflow') and key not in agent.__dict__.get('_owner_cancelled_tasks',set())}
    while len(rows) > 64:
        rows.pop(next(iter(rows)))


def committed_launch(agent, event):
    if event.get('hook_event_name') != 'PostToolUse':
        return
    response = event.get('tool_response') or {}
    if not isinstance(response, dict) or event.get('agent_id'):
        return
    tid = response.get('backgroundTaskId') or (response.get('agentId') if response.get('isAsync') or response.get('status') == 'async_launched' else None)
    key = f"shared-{agent.kind}:{(agent.session or {}).get('sessionId')}:{tid}"
    row = agent.__dict__.get('_native_task_origins', {}).get(key)
    if row is not None:
        board = getattr(agent.host, 'busy', None)
        row['generation'] = getattr(board, 'native_starts', {}).get(key, {}).get('generation')


def completed(agent, ids, trusted, *, successful=False):
    """Consume exact native launches once; external/unknown launches never inherit owner."""
    board = getattr(agent.host, 'busy', None)
    sid = (agent.session or {}).get('sessionId')
    rows = agent.__dict__.get('_native_task_origins', {})
    origins = []
    owners = []
    agent._native_owner_launch = None
    for tid in ids:
        key = f'shared-{agent.kind}:{sid}:{tid}'
        row = rows.pop(key, None)
        start = getattr(board, 'native_starts', {}).get(key, {})
        if (not row or not row['generation'] or row['generation'] != start.get('generation')
                or not _valid(agent, row['origin'])):
            trusted = False
        else:
            origins.append(row['origin'])
            owners.append((row.get('owner_launch'),key,row['generation']))
    agent._native_approval_origin = None
    if trusted and origins and all(o == origins[0] for o in origins):
        origin = copy.deepcopy(origins[0])
        origin['chain'] = [*origin.get('chain', []), {'kind': 'native_task', 'id': ','.join(ids)}][-16:]
        agent._native_approval_origin = (sid, origin)
        native_owner_launch.activate(agent, owners, agent._native_approval_origin, successful)


def snapshot(agent):
    bound = getattr(agent, '_native_approval_origin', None)
    if (bound and not getattr(agent, 'phone_turn', False) and bound[0] == (agent.session or {}).get('sessionId')
            and provenance.unattended_turn(agent) == 'system' and _valid(agent, bound[1])):
        return copy.deepcopy(bound[1])
    return None


def notification_trusted(agent,ids):
    if agent.__dict__.get('_owner_cancel_overflow'):return False
    sid=(agent.session or {}).get('sessionId')
    rows=agent.__dict__.get('_native_task_origins',{})
    cancelled=agent.__dict__.get('_owner_cancelled_tasks',set())
    starts=getattr(getattr(agent.host,'busy',None),'native_starts',{})
    for tid in ids:
        key=f'shared-{agent.kind}:{sid}:{tid}'
        row=rows.get(key)
        if (key in cancelled or not row or row.get('trusted') is not True or not row.get('generation')
                or row['generation']!=starts.get(key,{}).get('generation')):return False
    return bool(ids)
