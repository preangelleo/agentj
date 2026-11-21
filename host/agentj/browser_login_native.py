"""Current foreground Codex command evidence for Channels sign-in."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import os
import shlex
import shutil
import sys

from .core import process_evidence

MAX_CALLS = 32


def _command(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 1024 or any(c in raw for c in '$`<>;&|\r\n'):
        return None
    try:
        words = shlex.split(raw)
    except ValueError:
        return None
    if words[1:4] != ['browser', 'login', 'channels-weixin']:
        return None
    exe, flags = words[0], words[4:]
    if exe != 'agentj':
        installed = shutil.which('agentj')
        if not os.path.isabs(exe) or not installed or os.path.realpath(exe) != os.path.realpath(installed):
            return None
    if len(flags) != len(set(flags)) or set(flags) - {'--wait', '--json'}:
        return None
    return frozenset(flags)


@dataclass
class Call:
    command: frozenset
    cwd: str
    began: float
    anchor: tuple
    existing: set
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False
    closed: bool = False
    used: bool = False


@dataclass
class Turn:
    proc: object
    send: object
    native: tuple
    calls: dict = field(default_factory=dict)
    shell_items: set = field(default_factory=set)
    unknown: bool = False

    def live(self, agent):
        return (agent.host.agent is agent and self.proc is agent.proc
                and self.proc.returncode is None and getattr(agent, 'turn_proc', None) is self.proc
                and self.send is agent.cur_send and self.native == (agent.tid, agent.turn_id))


def _turn(agent):
    if not (getattr(agent, 'proc', None) and getattr(agent, 'cur_send', None)
            and isinstance(agent.tid, str) and isinstance(agent.turn_id, str)):
        return None
    current = agent.__dict__.get('_channels_command_turn')
    if current is None or not current.live(agent):
        current = Turn(agent.proc, agent.cur_send, (agent.tid, agent.turn_id))
        if not current.live(agent):
            return None
        agent._channels_command_turn = current
    return current


def prehook(agent, event, tool, inp):
    if (tool != 'Bash' or not isinstance(inp, dict) or not event.get('_agentj_path_authenticated')
            or getattr(agent, 'research', False) or getattr(agent.host, 'agent', None) is not agent):
        return
    command = _command(inp.get('command'))
    if command is None:
        return
    turn = _turn(agent)
    cid, cwd = event.get('tool_use_id'), event.get('cwd')
    if (turn is None or turn.native != (event.get('session_id'), event.get('turn_id'))
            or not isinstance(cid, str) or not 0 < len(cid) <= 200
            or not isinstance(cwd, str) or not os.path.isabs(cwd)):
        return
    # Shared Codex may invoke both copies of its trusted hook. Never refresh the first observation.
    cwd = os.path.realpath(cwd)
    if cid in turn.calls:
        old = turn.calls[cid]
        if (command, cwd) != (old.command, old.cwd):
            old.closed = True
            old.ready.set()
        return
    if len(turn.calls) >= MAX_CALLS:
        turn.unknown = True
        return
    began, anchor = process_evidence.now(), process_evidence.row(turn.proc.pid)
    if not anchor or anchor[0] != os.getpid():
        return
    existing = set()
    if sys.platform == 'linux':
        from . import busy_launch
        # Exclude existing same-tick processes as well as earlier shell generations.
        began = math.floor(began * busy_launch.HZ) / busy_launch.HZ
        table = busy_launch.procs()
        if turn.proc.pid not in table:
            return
        existing = {(pid, row[2]) for pid, row in table.items() if int(row[2]) / busy_launch.HZ >= began}
    turn.calls[cid] = Call(command, cwd, began, anchor, existing)


def note(agent, method, params, item):
    if item.get('type') != 'commandExecution' or getattr(agent.host, 'agent', None) is not agent:
        return
    if (params.get('threadId'), params.get('turnId')) != (agent.tid, agent.turn_id):
        return
    turn, cid = _turn(agent), item.get('id')
    if turn is None or not isinstance(cid, str) or not 0 < len(cid) <= 200:
        return
    row = turn.calls.get(cid)
    if method == 'item/completed':
        turn.shell_items.discard(cid)
        if row is not None:
            row.closed = True
            row.ready.set()
        return
    if method != 'item/started':
        return
    if len(turn.shell_items) >= MAX_CALLS:
        turn.unknown = True
        return
    turn.shell_items.add(cid)
    if row is not None:
        from .agent_codex import unwrap_shell
        raw, cwd = item.get('command'), item.get('cwd')
        row.started = (item.get('status') == 'inProgress' and isinstance(raw, str)
                       and _command(unwrap_shell(raw)) == row.command
                       and isinstance(cwd, str) and os.path.realpath(cwd) == row.cwd)
        row.closed |= not row.started
        row.ready.set()


def _peer(peer, anchor, row):
    from . import approval_material

    current, chain = peer, {}
    while current != anchor and type(current) is int and current > 1 and current not in chain and len(chain) < 64:
        child = process_evidence.row(current)
        if not child:
            return False
        parent, born, token = child
        fence = (sys.platform == 'linux' and current != peer and parent == anchor
                 and approval_material._fence_init(anchor, current))
        if not fence and (born < row.began or (current, token) in row.existing):
            return False
        chain[current] = child
        current = parent
    return (current == anchor and bool(chain) and process_evidence.row(anchor) == row.anchor
            and all(process_evidence.row(pid) == old for pid, old in chain.items()))


async def claim(agent, req, peer):
    """The socket and native notification stream can deliver in either order."""
    if req != {'t': 'browser_login', 'site': 'channels-weixin'}:
        return None
    turn = agent.__dict__.get('_channels_command_turn')
    if turn is None or not turn.live(agent):
        return None
    from . import approval_material
    found = [(cid, row) for cid, row in turn.calls.items() if not row.closed and not row.used]
    if len(found) != 1:
        return None
    cid, row = found[0]
    try:
        await asyncio.wait_for(row.ready.wait(), 1)
    except asyncio.TimeoutError:
        return None
    def live():
        return (turn.live(agent) and not turn.unknown and turn.calls.get(cid) is row
                and row.started and not row.closed and turn.shell_items == {cid}
                and approval_material.phone_main_live(agent))
    if not live() or row.used or not await asyncio.to_thread(_peer, peer, turn.proc.pid, row) or not live() or row.used:
        return None
    row.used = True
    return live
