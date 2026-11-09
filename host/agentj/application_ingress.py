"""Transient transport evidence for an ordinary application call, never a grant.

Only real request handlers enter this scope. It cannot authorize native tools,
listed UV operations, a later frame, another task, or durable/peer delegation.
"""
from __future__ import annotations
import asyncio
import contextlib
import contextvars
import os
import sys
from . import codex_procs, ctl_auth, provenance

_CURRENT = contextvars.ContextVar('agentj_application_frame', default=None)
_MAIL_REPLY = contextvars.ContextVar('agentj_completed_mail_reply', default=None)


def _process(pid):
    if type(pid) is not int or pid <= 1:
        return None
    if sys.platform.startswith('linux'):
        try:
            if os.stat(f'/proc/{pid}').st_uid != os.getuid():
                return None
        except OSError:
            return None
        return codex_procs.start_token(pid)
    from .core.process_evidence import snapshot
    row = snapshot(pid)
    return row[0] if row else None


def _sandboxed(agent):
    source = provenance.turn_source(agent)
    return (getattr(agent, 'research', False) or source.get('kind') == 'friend_task'
            or (source.get('role') == 'group' and source.get('kind') != 'unknown')
            or (bool(getattr(agent, 'untrusted_state', None)) and getattr(agent, 'untrusted_foreign', False)))


class _Frame:
    def __init__(self, host, writer, frame, check, kind):
        self.host, self.writer, self.frame = host, writer, frame
        self.check, self.kind = check, kind
        self.task = asyncio.current_task()
        self.state = host.st
        self.host_key = host.kp.pub
        self.channel = host.channel
        self.epoch = str(host.hist.epoch)
        self.closed = False

    def _transport_live(self):
        try:
            return (not self.closed and self.task is not None and not self.task.done()
                    and self.host.st is self.state and self.host.kp.pub == self.host_key
                    and self.host.channel == self.channel and str(self.host.hist.epoch) == self.epoch
                    and not self.host.stopped() and not self.host.stopping.is_set() and self.check())
        except (OSError, ValueError, AttributeError, TypeError):
            return False

    def live(self):
        return _CURRENT.get() is self and asyncio.current_task() is self.task and self._transport_live()

    def worker_live(self):
        # Only a synchronous worker awaited by the same still-live frame. An
        # inherited asyncio task never gains transport intent from this method.
        try:
            if asyncio.current_task() is not None:
                return False
        except RuntimeError:
            pass
        return _CURRENT.get() is self and self._transport_live()


@contextlib.contextmanager
def _enter(host, writer, frame, check, kind):
    row = _Frame(host, writer, frame, check, kind)
    token = _CURRENT.set(row)
    try:
        yield
    finally:
        row.closed = True
        _CURRENT.reset(token)


def _session_binding(host, agent):
    session = getattr(agent, 'session', None)
    values = tuple((key, session.get(key)) for key in ('sessionId', 'session_id', 'id', 'pid', 'procStart', 'pidDomain')) if isinstance(session, dict) else ()
    return (id(session), values, getattr(agent, 'kind', None), getattr(agent, 'tid', None), getattr(agent, 'sid', None),
            getattr(agent, 'proc', None), (getattr(agent, 'cfg', None) or {}).get('dir'),
            (getattr(host, 'agent_cfg', None) or {}).get('dir'))


def native(host, writer, frame, pid, uid):
    """Existing Host-held invocation/launch proof plus the actual kernel peer.

    An ancestor PID or a current owner/system label alone is insufficient. Old
    guest children cannot borrow the native process's later owner turn.
    """
    from . import approval_material
    start = _process(pid) if uid == os.getuid() else None
    agent = host.agent
    session = getattr(agent, 'session', None)
    binding = _session_binding(host, agent)
    scope = approval_material._direct_scope(host, pid) if start else None
    if scope is not None and scope.get('kind') == 'unknown_agent':
        scope = None
    terminal = bool(start and ctl_auth.refusal(pid, os.getpid(), host._agent_pids()) is None and ctl_auth.has_tty(pid))
    def check():
        if not start or _process(pid) != start or writer.is_closing():
            return False
        if terminal:
            return ctl_auth.refusal(pid, os.getpid(), host._agent_pids()) is None and ctl_auth.has_tty(pid)
        return (host.agent is agent and getattr(agent, 'session', None) is session
                and not _sandboxed(agent) and binding == _session_binding(host, agent)
                and scope is not None and approval_material._scope_live(host, scope, pid))
    return _enter(host, writer, frame, check, 'terminal' if terminal else 'native')


def paired(host, session, frame):
    binding = (session.cid, session.device, session.pub, host.st.sign_key(session.device))
    source = provenance._paired_source()
    state = provenance._PAIRED_REQUEST.get()
    admitted = (source is not provenance._UNSET and provenance.is_owner(source)
                and state is not provenance._UNSET and state[0] is asyncio.current_task())
    verify = state[1] if admitted else lambda: False
    def check():
        return (admitted and verify() and host._paired_live(session)
                and binding == (session.cid, session.device, session.pub, host.st.sign_key(session.device)))
    return _enter(host, session, frame, check, 'paired')


async def frames(host, reader, writer, pid, uid):
    """Close this frame before reading another; caller explicitly aclose()s on exit."""
    while line := await reader.readline():
        with native(host, writer, line, pid, uid):
            yield line


def current(host):
    row = _CURRENT.get()
    return row if type(row) is _Frame and row.host is host and row.live() else None


def actor(host):
    row = current(host)
    return row.kind if row else None


def allowed(host):
    return current(host) is not None


@contextlib.contextmanager
def _completed_mail_reply(host, job):
    """Private, fixed reply executor only; never a generic application frame."""
    from .mail import _ReplyJob
    value = job if type(job) is _ReplyJob and job.host is host else None
    token = _MAIL_REPLY.set(value)
    try:
        yield
    finally:
        _MAIL_REPLY.reset(token)


def _completed_reply_allowed(host, tool, inp):
    from .mail import _ReplyJob
    job = _MAIL_REPLY.get()
    return (type(job) is _ReplyJob and job.host is host
            and tool == 'agentj mail reply' and job.authorizes(inp))


def ordinary(host, tool, inp):
    """Read current transport intent only; never emit a signature or capability."""
    from . import policy_registry, uv_list
    cwd = (getattr(host, 'agent_cfg', None) or {}).get('dir')
    if (tool not in policy_registry.HOST_TOOLS or tool in ('AskUserQuestion', 'SandboxOnce', 'AuthRescue', 'AgentJBoundedPlan')
            or uv_list.required(tool, inp, cwd=cwd, env=uv_list.host_environment(host))):
        return {'behavior': 'deny', 'reason': 'not_ordinary_application'}
    if not allowed(host) and not _completed_reply_allowed(host, tool, inp):
        return {'behavior': 'deny', 'reason': 'estop' if host.stopped() else 'application_ingress_unverified'}
    return {'behavior': 'allow', 'updatedInput': inp}


class Expired(Exception):
    pass


async def run(host, callback, *args, **kwargs):
    """Recheck inside an awaited IO worker, after the event-loop/worker handoff."""
    row = current(host)
    if row is None:
        raise Expired('application_ingress_unverified')
    def effect():
        if not row.worker_live():
            raise Expired('application_ingress_unverified')
        return callback(*args, **kwargs)
    return await asyncio.to_thread(effect)
