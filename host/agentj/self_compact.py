"""P506 #271: main Agent requests its own fixed /compact through capability.sock.

No target, command text or approval answer is accepted. The peer must descend from
the currently served process, have owner provenance and have written a fresh handoff.
The existing command queue runs it after this turn; the caller never waits for itself.
"""
from __future__ import annotations
import asyncio
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
 'source': ('无法确认这轮来自主人本人，不能自行压缩主会话。', 'This turn cannot be verified as coming from the owner; it cannot compact the main conversation.'),
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


def owner_channel(src):
    if not isinstance(src, dict):
        return False
    src = provenance.normalize(src)
    return src['role'] == 'owner' and src['kind'] == 'phone'


def guard(agent, cmd):
    data = getattr(cmd, 'data', None) or {}
    expected = data.get('self_compact')
    if expected is None:
        return None
    if agent.host.agent is not agent or marker(agent) != expected or not fresh(agent):
        agent.host.st.log('self_compact', result='stale')
        return Result(detail(agent.host, 'stale'), 'refused')
    src = turn_source(agent)
    actor = agent
    if getattr(agent, 'cur_send', None) is None and not hasattr(agent, 'out_src') and (src is _UNKNOWN or src is None):
        # The queue runs after Send is cleared. Its host-captured owner source remains
        # valid only under the unchanged conversation/turn marker checked above.
        native = getattr(agent, 'native_source', None)
        if native is not None and not owner_channel(native):
            return Result(detail(agent.host, 'source'), 'refused')
        src = data.get('self_compact_source')
        actor = SimpleNamespace(cur_send=SimpleNamespace(source=src), untrusted=getattr(agent, 'untrusted', None))
    if agent.host.stopped() or not owner_channel(data.get('self_compact_source')) or not owner_channel(src) or provenance.untrusted_turn(actor) or provenance.delegated_turn(actor):
        return Result(detail(agent.host, 'stopped' if agent.host.stopped() else 'source'), 'refused')
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
    if not owner_channel(src) or provenance.untrusted_turn(agent) or provenance.delegated_turn(agent):
        return refused('source')
    if not fresh(agent):
        return refused('handoff')
    pending = [x for x in list(agent.q._queue) if isinstance(x, Cmd) and x.name=='compact' and not x.cancelled]
    if pending:
        return {'result':'queued', 'detail':detail(host, 'queued')}
    turn = host.cmd_turn(None, 'compact')
    agent.submit(Cmd('compact', '', 'Agent J', turn, data={'self_compact':marker(agent), 'self_compact_source':provenance.normalize(src)}))
    host.st.log('self_compact', result='queued')
    return {'result':'queued', 'detail':detail(host, 'queued')}
