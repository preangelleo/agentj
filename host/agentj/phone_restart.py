"""P435: an owner-signed manual restart, preserving native permissions and the conversation.

Only reset.Desk calls this after signature verification. Never used by automatic support advice.
"""
from __future__ import annotations
import asyncio
import os
import signal
from . import agent, shared_pin


# Native launcher restrictions: copy only from the verified local process, never from a phone request.
# Values (including inline native settings) stay in memory; never log or serialize them.
VALUES = {'--permission-mode', '--permission-prompts', '--permission-prompt-tool', '--settings',
          '--setting-sources', '--agent', '--agents', '--max-budget-usd', '--max-turns'}
MULTI = {'--allowedTools', '--allowed-tools', '--disallowedTools', '--disallowed-tools',
         '--tools', '--mcp-config', '--add-dir', '--plugin-dir'}
SWITCHES = {'--restricted', '--bare', '--strict-mcp-config', '--dangerously-skip-permissions',
            '--allow-dangerously-skip-permissions'}


def native_flags(argv):
    out = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == '--':
            break
        key = arg.split('=', 1)[0]
        i += 1
        if key in SWITCHES:
            out.append(arg)
        elif key in VALUES | MULTI:
            out.append(arg)
            if '=' in arg:
                continue
            start = i
            if key in VALUES:
                if i < len(argv) and not argv[i].startswith('--'):
                    out.append(argv[i]); i += 1
            else:
                while i < len(argv) and not argv[i].startswith('-'):
                    out.append(argv[i]); i += 1
            if start == i:
                raise ValueError('native restriction argument missing')
    return out


def profile(a, sid=None):
    selected = getattr(a, 'session', None)
    if not selected and sid:
        from .shared import claude_sessions
        rows = claude_sessions(a.cfg['dir'], sid)
        if len(rows) != 1:
            raise ValueError('native restriction profile unavailable')
        selected = a.session = rows[0]
    if not selected:
        return []
    native = getattr(a, 'native', None)
    if native is not None and native.pid == selected.get('pid'):
        return list(getattr(a, '_phone_restart_flags', []))
    from .shared import _same_process
    if not selected.get('procStart') or not _same_process(selected):
        raise ValueError('process identity unavailable')
    from pathlib import Path
    argv = Path(f"/proc/{selected['pid']}/cmdline").read_bytes().rstrip(b'\0').split(b'\0')
    if not _same_process(selected):
        raise ValueError('process identity changed')
    return native_flags([os.fsdecode(v) for v in argv])


async def end_selected(a):
    """Terminate exactly the attached Claude process, not its terminal or any other session.

    Linux pidfd binds the signal to the verified process. Elsewhere require the registry's
    process-start identity on every signal; missing identity fails closed.
    """
    from .shared import claude_sessions, _same_process
    selected = a.session
    if not selected:
        return
    pid, sid = selected.get('pid'), selected.get('sessionId')
    if a.native and a.native.pid == pid:
        return  # the owned child handle (including an already reaped child) is authoritative
    rows = claude_sessions(a.cfg['dir'], sid)
    if not any(r.get('pid') == pid for r in rows):
        raise ValueError('selected process unverified')
    if not selected.get('procStart') or not _same_process(selected):
        raise ValueError('process identity unavailable')
    fd = None
    try:
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            fd = os.pidfd_open(pid)
        elif not selected.get('procStart'):
            raise ValueError('process identity unavailable')
        def alive():
            try:
                os.kill(pid, 0)
                return _same_process(selected)
            except ProcessLookupError:
                return False
        def send(sig):
            if not alive():
                return
            if fd is not None:
                signal.pidfd_send_signal(fd, sig)
            else:
                os.kill(pid, sig)
        send(signal.SIGTERM)
        for _ in range(50):
            if not alive():
                return
            await asyncio.sleep(.1)
        send(signal.SIGKILL)
        for _ in range(20):
            if not alive():
                return
            await asyncio.sleep(.1)
        raise ValueError('selected process did not exit')
    finally:
        if fd is not None:
            os.close(fd)


async def prepare(a, sid):
    if a.kind == 'claude' and a.cfg.get('session_mode') == 'shared':
        from .shared import claude_sessions
        await a.ordinary_start(resume=sid, extra_flags=getattr(a, '_phone_restart_flags', []))
        rows = [r for r in claude_sessions(a.cfg['dir']) if r['pid'] == a.native.pid]
        if len(rows) != 1 or (sid and rows[0]['sessionId'] != sid):
            raise ValueError('startup session unconfirmed')
        a._repin(sid, rows[0], 'phone_restart')
        if not await a.attach(False):
            raise ValueError('attach failed')
        return
    if a.kind == 'opencode':
        if not await a._spawn():
            raise ValueError('native connection failed')
    fn = {'claude': '_ensure', 'codex': '_thread', 'opencode': '_session_ok'}[a.kind]
    if not await getattr(a, fn)():
        raise ValueError('native resume failed')


async def run(host):
    if host.stopped():
        return {'ok': False, 'why': 'estop'}
    old = host.agent
    if old is None or old.kind not in ('claude', 'codex', 'opencode'):
        return {'ok': False, 'why': 'unavailable'}
    cfg = dict(old.cfg)
    sid = ((getattr(old, 'session', None) or {}).get('sessionId') if old.kind == 'claude' else None) \
        or cfg.get('shared_session_id') or host.st.agent_session(old.kind)
    flags = []
    if old.kind == 'claude' and cfg.get('session_mode') == 'shared':
        flags = profile(old, sid)
        await end_selected(old)
    old.halting = True
    current = getattr(old, 'cur_send', None)
    queue = getattr(old, 'q', None)
    # Includes the message picked from q but still waiting for transcription (not yet cur_send).
    pending = [x for x in getattr(getattr(host, 'sends', None), 'items', {}).values()
               if getattr(x, 'state', None) == 'pending']
    dropped = set()
    def drop(item):
        if id(item) not in dropped:
            host.queue_dropped(item)
            dropped.add(id(item))
    if queue is not None:
        while not queue.empty():
            drop(queue.get_nowait())
    await old.stop()  # cancels the current actor and releases its turn lock; only owned native processes are killed
    for item in pending:
        if item.state == 'pending':
            drop(item)
    if current is not None:
        # A delivered/uncertain write must never be reported as "cancelled before delivery".
        if getattr(current, 'state', None) == 'pending':
            drop(current)
        if getattr(host, 'cur_turn', None) == getattr(current, 'turn', None):
            host.agent_turn_end()
    def replacement(activate=False):
        a = agent.make(host, cfg)
        a._phone_restart_flags = flags
        if queue is not None:
            a.q = queue  # arrivals during reopening follow the replacement, not the stopped actor
        host.agent = a
        host.agent_cfg = dict(a.cfg)
        if activate:
            a.start()
        return a
    fresh = False
    async with host.turn_lock:
        for gid in list(getattr(host, 'grants', {})):
            host.end_grant(gid, 'turn_end')
        host.__dict__.pop('_parked_ok', None)
        for attempt in range(2):
            if host.stopped():
                replacement()  # do_resume starts this dormant actor after the owner's explicit resume
                return {'ok': False, 'why': 'estop'}
            a = replacement()
            try:
                await asyncio.wait_for(prepare(a, sid if not fresh else None), 45)
                if host.stopped():
                    await a.stop()
                    replacement()
                    return {'ok': False, 'why': 'estop'}
                actual = host.st.agent_session(a.kind)
                if a.cfg.get('session_mode') == 'shared' and actual:
                    shared_pin.write(host.st, a.kind, actual)
                    a.cfg['shared_session_id'] = actual
                host.agent_cfg = dict(a.cfg)
                a.start()
                a.set_status('idle')
                host.st.log('phone_restart', agent=a.kind, result='new' if fresh else 'resumed')
                return {'ok': True, 'new': fresh}
            except asyncio.CancelledError:
                await a.stop()
                raise
            except Exception:
                await a.stop()
                if host.stopped() or attempt:
                    dormant = replacement(activate=not host.stopped())
                    dormant.set_status('down')
                    return {'ok': False, 'why': 'estop' if host.stopped() else 'failed'}
                if sid:
                    from .reset import write_undo
                    write_undo(host.st, old.kind, sid)
                cfg['shared_session_id'] = ''
                host.st.set_agent_session(old.kind, None)
                shared_pin.write(host.st, old.kind, '')
                fresh = True
    return {'ok': False, 'why': 'failed'}
