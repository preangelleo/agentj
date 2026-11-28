"""Independent Claude source evidence from its Host-owned output pipe."""
import asyncio
import contextlib
import contextvars
import copy
import time
from dataclasses import dataclass

from . import approval_review, provenance, trust_taint

KEEP = 512
UNKNOWN = object()
_ACTIVE = contextvars.ContextVar('claude_native_approval', default=None)


@dataclass
class Launch:
    roster: object
    send: object
    source: dict
    origin: dict
    epoch: str
    estop: object
    state: object
    device: str | None
    key: bytes | None
    until: float

    @classmethod
    def capture(cls, roster, send):
        host = roster.agent.host
        if send is None or roster.agent.cur_send is not send:
            return None
        source = provenance.normalize(send.source)
        origin = getattr(send, 'approval_origin', None)
        epoch = str(host.hist.epoch)
        if (not provenance.owner_intent(source, send) or not isinstance(origin, dict)
                or origin.get('verified') is not True or origin.get('suspicious')
                or origin.get('session') != epoch or origin.get('message_id') != send.turn
                or origin != host.__dict__.get('_approval_origins', {}).get(send.turn)):
            return None
        device = send.device if source.get('kind') == 'phone' else None
        key = host.st.sign_key(device) if device else None
        if source.get('kind') == 'phone' and not key:
            return None
        return cls(roster, send, source, copy.deepcopy(origin), epoch, host.estop, host.st,
                   device, key, time.monotonic() + 3600)

    def live(self):
        from .approval_material import feedback_pairing_live
        host = self.roster.agent.host
        return (self.roster.live() and time.monotonic() < self.until and host.st is self.state
                and host.estop is self.estop and str(host.hist.epoch) == self.epoch
                and self.send.state not in ('cancelled', 'failed')
                and self.source == provenance.normalize(self.send.source)
                and self.send.approval_origin == self.origin
                and (not self.device or (self.state.sign_key(self.device) == self.key
                                         and feedback_pairing_live(host, self.send))))


@dataclass
class Child:
    launch: Launch | None
    task: str
    run: str
    external_read: bool = False
    ended: bool = False

    def live(self):
        return not self.ended and self.launch is not None and self.launch.live()


@dataclass
class Call:
    ident: str
    message: str
    tool: str
    digest: str
    is_child: bool
    child: Child | None
    notice: tuple | None
    claimed: bool = False
    conflicting: bool = False


class Roster:
    def __init__(self, agent, process):
        self.agent, self.process = agent, process
        self.session = self.pending = self.send = self.notice = None
        self.offers, self.children, self.completed, self.calls = {}, {}, {}, {}
        self.observed = asyncio.Event()

    def live(self):
        agent = self.agent
        host = agent.host
        return (host.agent is agent and agent.proc is self.process and self.process.returncode is None
                and agent.__dict__.get('claude_children') is self and not agent.halting and not host.stopped()
                and not host.stopping.is_set())

    @staticmethod
    def _put(rows, key, value):
        if key in rows:
            return
        rows[key] = value
        if len(rows) > KEEP:
            rows.pop(next(iter(rows)))

    def sent(self, uuid, send):
        self.pending = (uuid, send)

    def note(self, event):
        kind, subtype = event.get('type'), event.get('subtype')
        sid = event.get('session_id')
        if not isinstance(sid, str) or not sid:
            return
        if kind == 'system' and subtype == 'init' and sid != self.session:
            self.session = sid
            self.send = self.notice = None
            for rows in (self.offers, self.children, self.completed, self.calls):
                rows.clear()
        if sid != self.session:
            return
        parent = event.get('parent_tool_use_id')
        if kind == 'user' and not parent:
            origin = event.get('origin')
            if isinstance(origin, dict) and origin.get('kind') == 'task-notification':
                self.send = None
                run = origin.get('runId')
                self.notice = (run, self.completed.pop(run, None)
                               if origin.get('producer') == 'session-task' and isinstance(run, str) else None)
                self.agent.host.st.log('claude_native_notice', result='matched' if self.notice[1] else 'unverified')
            elif event.get('isReplay') is True and self.pending and event.get('uuid') == self.pending[0]:
                self.send = self.pending[1]
                self.pending = self.notice = None
            elif event.get('isReplay') is True:
                self.send = self.notice = None
        elif kind == 'result' and not parent:
            self.send = self.notice = None
        elif kind == 'system' and subtype in ('task_started', 'task_notification'):
            self._task(event, subtype)
        elif kind == 'assistant':
            self._assistant(event, parent)

    def _task(self, event, subtype):
        call, task, run = (event.get(name) for name in ('tool_use_id', 'task_id', 'run_id'))
        if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (call, task, run)):
            return
        if subtype == 'task_started' and event.get('task_type') == 'local_agent':
            old = self.children.get(call)
            if old is not None and (task, run) != (old.task, old.run):
                old.ended = True
            if call in self.offers:
                launch, parent, external_read = self.offers.pop(call)
                if event.get('parent_task_id') == parent:
                    self._put(self.children, call, Child(launch, task, run, external_read))
            return
        child = self.children.get(call)
        if child is not None and (task, run) == (child.task, child.run) and not child.ended:
            child.ended = True
            if event.get('status') == 'completed':
                if run in self.completed:
                    self.completed[run] = None
                else:
                    self._put(self.completed, run, child.launch)
        if subtype == 'task_notification':
            self.agent.host.st.log('claude_native_completion', result='matched' if run in self.completed else 'unverified')

    def _assistant(self, event, parent):
        message = event.get('message')
        identity = event.get('uuid')
        if (not isinstance(message, dict) or message.get('model') == '<synthetic>'
                or event.get('isReplay') or event.get('isApiErrorMessage') or not isinstance(identity, str)):
            return
        if not parent and self.send is None and self.pending is None and self.notice is None and self.completed:
            # Claude can process an autonomous result before echoing that input
            # on stdout. With no accepted owner input, the sole native receipt
            # is at most a read-only continuation for this root turn.
            self.notice = self.completed.popitem() if len(self.completed) == 1 else (None, None)
            self.agent.host.st.log('claude_native_notice', result='sole_completed_read' if self.notice[1] else 'unverified')
        child = self.children.get(parent) if isinstance(parent, str) else None
        if child is not None and event.get('agent_id') != child.task:
            child = None
        for block in message.get('content') or []:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            ident, tool, inp = block.get('id'), block.get('name'), block.get('input')
            if not isinstance(ident, str) or not 0 < len(ident) <= 256 or not isinstance(tool, str) or not isinstance(inp, dict):
                continue
            digest = approval_review.digest(inp)
            old = self.calls.get(ident)
            if old is not None:
                if (old.message, old.tool, old.digest) != (identity, tool, digest):
                    old.conflicting = True
                    self.offers.pop(ident, None)
                    if ident in self.children:
                        self.children[ident].ended = True
                continue
            if child and not child.external_read and trust_taint.tool_kind(tool, inp) is not None:
                child.external_read = True
            if tool in ('Agent', 'Task') and ident not in self.children and not inp.get('isolation'):
                launch = child.launch if child and child.live() else Launch.capture(self, self.send) if not parent else None
                external_read = bool(child and child.external_read or trust_taint.tainted(self.agent))
                self._put(self.offers, ident, (launch, child.task if child else None, external_read))
            self._put(self.calls, ident, Call(ident, identity, tool, digest, bool(parent), child, self.notice))
        self.observed.set()
        self.observed = asyncio.Event()

    async def find(self, ident, tool, inp):
        if not self.live():
            return None
        ambiguous = self.notice or self.offers or any(not c.ended for c in self.children.values())
        if not isinstance(ident, str):
            return UNKNOWN if ambiguous else None
        deadline = time.monotonic() + 0.2
        while ident not in self.calls and ambiguous and self.live():
            try:
                await asyncio.wait_for(self.observed.wait(), max(0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
        entry = self.calls.get(ident)
        if entry is None or entry.conflicting or entry.claimed or (entry.tool, entry.digest) != (tool, approval_review.digest(inp)):
            # This connection also serves ordinary tools on older Claude versions.
            # Known child/notification work must never borrow the current Send.
            return UNKNOWN if entry is not None or ambiguous else None
        if entry.is_child or entry.notice:
            entry.claimed = True
            return entry if entry.is_child or self.notice is entry.notice else UNKNOWN
        return None


@dataclass(frozen=True)
class Request:
    roster: Roster
    call: Call | None
    child: Child | None
    notice: tuple | None
    launch: Launch | None
    task: object

    def live(self):
        return (self.roster.live() and (self.launch is None or self.launch.live())
                and (self.call is None or (self.roster.calls.get(self.call.ident) is self.call and not self.call.conflicting))
                and (self.child is None or not self.child.ended)
                and (self.notice is None or self.roster.notice is self.notice))


def request(host):
    entry = _ACTIVE.get()
    if entry is not None and entry.roster.agent.host is host:
        try:
            if entry.task is asyncio.current_task():
                return entry
        except RuntimeError:
            pass
    return None


@contextlib.contextmanager
def scope(roster, binding):
    if binding is None:
        yield
        return
    call = None if binding is UNKNOWN else binding
    child, notice = (call.child, call.notice if not call.is_child else None) if call else (None, roster.notice)
    launch = child.launch if child and child.live() else notice[1] if notice else None
    launch = launch if call and launch is not None and launch.live() else None
    origin = copy.deepcopy(launch.origin) if launch else {}
    if child and launch:
        origin['chain'] = [*origin.get('chain', []), {'kind': 'subagent', 'id': child.task}][-16:]
    token = _ACTIVE.set(Request(roster, call, child, notice, launch, asyncio.current_task()))
    try:
        with approval_review.origin_scope(origin):
            yield
    finally:
        _ACTIVE.reset(token)
