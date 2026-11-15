"""P506 #271: main Agent requests its own fixed /compact through capability.sock.

No target, command text or approval answer is accepted. The peer must descend from
the currently served process, have owner provenance and have written a fresh handoff.
The existing command queue runs it after this turn; the caller never waits for itself.
#449: a heartbeat beat that carried the host's own context notice (>= half the window) holds a one-shot invite; during
that beat the same request is accepted without an owner turn (still: own process tree, fresh handoff, not stopped,
not an untrusted or delegated turn). The invite is consumed when the queued compaction runs.
"""
from __future__ import annotations
import asyncio
import copy
import hashlib
import stat
import os
import time
from types import SimpleNamespace
from . import compactprep, handoff, provenance, shared_hook
from .agent import Cmd
from .slash import Result

TEXT = {
 'queued': ('交接已确认，压缩已排队；结束本轮后执行，结果会显示在当前对话。', 'Handoff checked; compaction is queued for after this turn. Its result will appear in this conversation.'),
 'shape': ('这里只接受本会话的压缩请求，不能指定命令或其他会话。', 'Only this conversation can be compacted; commands and other targets are not accepted.'),
 'unsupported': ('当前 AI 接口尚未接入自行压缩；交接已保留，请用 Agent J 网页的压缩按钮，并查看返回的具体原因。', 'This AI interface is not wired for self-compaction yet. Keep the handoff, use Compact in the Agent J web app and check the returned reason.'),
 'not_ready': ('当前会话还没有可核实的输入连接；交接保留，请在 Agent J 网页重新连接会话后再试。', 'This conversation has no verifiable input connection yet. Keep the handoff and reconnect the conversation in the Agent J web app, then retry.'),
 'caller': ('无法确认请求来自当前主会话；没有压缩任何会话。', 'The caller could not be verified as the current main conversation; nothing was compacted.'),
 'source': ('这次压缩请求缺少有效的会话授权，没有执行压缩。', 'This compaction request has no valid conversation authorization; nothing was compacted.'),
 'handoff': ('请先按 agentj-handoff 写好本轮交接，再请求自行压缩。', 'Write this turn’s handoff with agentj-handoff before requesting compaction.'),
 'stopped': ('Agent J 已急停，没有排入压缩。', 'Agent J is stopped; no compaction was queued.'),
 'stale': ('会话已更换，或交接已不再新鲜；这次自行压缩已取消，请重新写交接后再试。', 'The conversation changed or its handoff is no longer fresh. This compaction was cancelled; update the handoff and retry.'),
}


def detail(host, why):
    return TEXT[why][1 if getattr(host, 'lang', 'zh') == 'en' else 0]


def marker(agent):
    session = getattr(agent, 'session', None)
    sid = session.get('sessionId') if isinstance(session, dict) else None
    if not sid:
        sid = agent.host.st.agent_session(agent.kind)
    return (id(agent), agent.kind, sid, (getattr(agent, 'cfg', {}) or {}).get('dir'), getattr(agent.host, 'turn_t0', 0)) if sid else None


def roots(agent):
    values = [getattr(getattr(agent, name, None), 'pid', None) for name in ('proc', 'native')]
    for name in ('session', 'writer'):
        row = getattr(agent, name, None)
        if isinstance(row, dict):
            values.append(row.get('pid'))
    return {v for v in values if type(v) is int and v>1}


def fresh(agent):
    send = getattr(agent, 'cur_send', None)
    created = getattr(send, 'created', None)
    since = time.time() - (time.monotonic()-created) if isinstance(created, (int,float)) else None
    cfg = getattr(agent, 'cfg', {})
    if cfg.get('session_mode') == 'shared' and agent.kind == 'claude':
        return handoff.fresh(since)
    probe = compactprep.probe(cfg.get('dir', ''), compactprep.FILE)
    if not probe or time.time()-probe[0] > compactprep.FRESH or probe[0] > time.time()+1:
        return False
    key = compactprep.session_key(agent)
    last = compactprep.load(agent.host.st).get(key, {}).get('at', 0) if key else 0
    return probe[0] > last and (since is None or probe[0] >= since-1)


_UNKNOWN = object()


def turn_source(agent):
    """Require an explicit host-verified source; a Send alone proves no owner origin."""
    send = getattr(agent, 'cur_send', None)
    if send is not None:
        src = getattr(send, 'source', _UNKNOWN)
        return src
    # Internal OpenCode turns have no Send and must not create an owner origin.
    if agent.kind == 'opencode' and getattr(agent, 'out_src_known', False) is not True:
        return _UNKNOWN
    # OpenCode retains the source of native continuations; shared Claude has pending_source.
    return getattr(agent, 'out_src', getattr(agent, 'pending_source', _UNKNOWN))


def heartbeat_invite(host, agent):
    """#449: the token of the current beat's self-compact invite, if the host issued one for this session; else None."""
    send = getattr(agent, 'cur_send', None)
    hb = getattr(host, 'heartbeat', None)
    try:
        from .heartbeat import HeartbeatSend, session_key
    except Exception:  # noqa: BLE001
        return None
    token = getattr(send, 'compact_invite', None)
    if type(send) is not HeartbeatSend or hb is None or not hb.has_invite(token, session_key(agent)):
        return None
    # A beat is a host-written system turn (never an owner turn). Its source must still be exactly that — no paired,
    # task or request scope ranking it as someone else's, no delegated turn, no untrusted shared-terminal input.
    if provenance.turn_source(agent).get('kind') != 'system' or provenance.delegated_turn(agent):
        return None
    req = provenance._REQUEST.get()
    if req is not provenance._UNSET and provenance.normalize(req).get('kind') not in ('system', 'phone', 'terminal'):
        return None
    fn = getattr(agent, 'untrusted', None)
    if callable(fn) and fn():
        return None
    return token


def owner_channel(src):
    if not isinstance(src, dict):
        return False
    src = provenance.normalize(src)
    # #0302: the owner terminal is the owner too. A shared Claude session's native source is the terminal, so after the
    # turn ended every queued owner compaction was refused (TB 2026-11-09 14:39, phone menu /compact).
    return src['role'] == 'owner' and src['kind'] in ('phone', 'terminal')


AUTH_TTL = 300.0
_ABSENT = object()


def handoff_binding(agent):
    """Bind the exact handoff bytes; never accept a substituted link or oversized file."""
    if agent.cfg.get('session_mode') != 'shared' or agent.kind != 'claude':
        probe = compactprep.probe(agent.cfg.get('dir', ''), compactprep.FILE)
        return probe if probe and probe[1] <= compactprep.READ_MAX else None
    try:
        p = handoff.path(handoff.COMPACT)
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
        with os.fdopen(fd, 'rb') as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or not 0 < st.st_size <= compactprep.READ_MAX:
                return None
            data = f.read(compactprep.READ_MAX + 1)
        return str(p), st.st_mtime_ns, st.st_size, hashlib.sha256(data).digest()
    except OSError:
        return None


def origin_binding(agent):
    # These are host-owned native-turn observations, not inferred owner identities.
    # Cleanup may clear cur_send; a new native source or generation cancels the request.
    return tuple(copy.deepcopy(v) if isinstance(v, dict) else v for v in
                 (getattr(agent, k, _ABSENT) for k in ('turn_gen', 'pending_source', 'native_source', 'out_src')))


def guard(agent, cmd):
    data = getattr(cmd, 'data', None) or {}
    if data.get('self_compact') is None:
        return None
    auth = getattr(agent, '_self_compact_authorization', None)
    if auth is None or auth.cmd is not cmd:
        return Result(detail(agent.host, 'source'), 'refused')
    agent._self_compact_authorization = None  # one attempt, including failed validation
    if (agent.host.agent is not agent or marker(agent) != auth.marker
            or time.monotonic() > auth.deadline or origin_binding(agent) != auth.origin
            or handoff_binding(agent) != auth.handoff or not fresh(agent)):
        agent.host.st.log('self_compact', result='stale')
        return Result(detail(agent.host, 'stale'), 'refused')
    if agent.host.stopped():
        return Result(detail(agent.host, 'stopped'), 'refused')
    if auth.invite is not None:
        from .heartbeat import session_key
        hb = getattr(agent.host, 'heartbeat', None)
        if hb is None or not hb.take_invite(auth.invite, session_key(agent)):
            return Result(detail(agent.host, 'stale'), 'refused')
        return None
    # Authorize the captured request, not the now-idle source resolver. An unrelated
    # active Send or a lower-trust request context still cannot borrow that authority.
    send = getattr(agent, 'cur_send', None)
    retained = [getattr(agent, k) for k in ('pending_source', 'out_src') if hasattr(agent, k)]
    actor = SimpleNamespace(cur_send=SimpleNamespace(source=auth.source), untrusted=getattr(agent, 'untrusted', None))
    if (send is not None and (send is not auth.send or getattr(send, 'source', None) != auth.source)
            or data.get('self_compact_source') != auth.source
            or any(not owner_channel(src) for src in retained)
            or provenance.untrusted_turn(actor) or provenance.delegated_turn(actor)):
        return Result(detail(agent.host, 'source'), 'refused')
    return None


async def enqueue(host, req, pid):
    def refused(why):
        host.st.log('self_compact', result=why)
        return {'result':'refused', 'why':why, 'detail':detail(host, why)}
    if req != {'t':'self_compact'}:
        return refused('shape')
    agent = getattr(host, 'agent', None)
    if agent is None:
        return refused('not_ready')
    if agent.kind not in ('claude','codex','opencode'):
        return refused('unsupported')
    if not marker(agent):
        return refused('not_ready')
    if type(pid) is not int or pid<=1:
        return refused('caller')
    chain = await asyncio.to_thread(shared_hook.ancestors, pid)
    if host.agent is not agent or not (set(chain) & roots(agent)):
        return refused('caller')
    if host.stopped():
        return refused('stopped')
    src = turn_source(agent)
    invite = heartbeat_invite(host, agent)
    if invite is None and (not owner_channel(src) or provenance.untrusted_turn(agent) or provenance.delegated_turn(agent)):
        return refused('source')
    if not fresh(agent):
        return refused('handoff')
    pending = [x for x in list(agent.q._queue) if isinstance(x, Cmd) and x.name=='compact' and not x.cancelled]
    if pending:
        return {'result':'queued', 'detail':detail(host, 'queued')}
    turn = host.cmd_turn(None, 'compact')
    data = {'self_compact':marker(agent), 'self_compact_source':provenance.normalize(src)}
    if invite is not None:
        data['self_compact_invite'] = invite
    cmd = Cmd('compact', '', 'Agent J', turn, data=data)
    binding = handoff_binding(agent)
    if binding is None:
        return refused('handoff')
    agent._self_compact_authorization = SimpleNamespace(cmd=cmd, marker=marker(agent),
        deadline=time.monotonic()+AUTH_TTL, origin=origin_binding(agent), handoff=binding,
        source=provenance.normalize(src), send=getattr(agent, 'cur_send', None), invite=invite)
    auth = agent._self_compact_authorization
    def expire():
        if getattr(agent, '_self_compact_authorization', None) is auth:
            agent._self_compact_authorization = None
    asyncio.get_running_loop().call_later(AUTH_TTL, expire)
    agent.submit(cmd)
    host.st.log('self_compact', result='queued', **({'by': 'heartbeat'} if invite is not None else {}))
    return {'result':'queued', 'detail':detail(host, 'queued')}
