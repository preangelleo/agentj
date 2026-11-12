"""Independent busy dispatch: one-shot launch and bounded, data-only progress.

This module does not load user configuration or make network probes. Manager
acceptance is never retried: a lost response is an unknown launch outcome.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import time

STALL_AFTER = 600


class Progress:
    action = 'record-only'

    def __init__(self, now, snapshot):
        self.last = now
        self.snapshot = snapshot
        self.stalled = False
        self.lock = threading.Lock()

    def output(self, now):
        with self.lock:
            self.last = now
            self.stalled = False

    def observe(self, now, snapshot):
        with self.lock:
            if snapshot != self.snapshot:
                self.snapshot = snapshot
                self.last = now
                self.stalled = False
            if not self.stalled and now - self.last >= STALL_AFTER:
                self.stalled = True
                return True
        return False


def tree_snapshot(cwd, limit=256):
    """Bounded top-level metadata, no content, symlink traversal or home scan."""
    cwd = Path(cwd).absolute()
    home = Path.home().absolute()
    if cwd == home or cwd in home.parents or cwd == Path('/'):
        return ()
    out = []
    try:
        with os.scandir(cwd) as entries:
            for i, entry in enumerate(entries):
                if i >= limit:
                    break
                if entry.name.startswith('.') or entry.is_symlink():
                    continue
                info = entry.stat(follow_symlinks=False)
                out.append((entry.name, info.st_mtime_ns, info.st_size))
    except OSError:
        pass
    return tuple(sorted(out))


def write_probe(cwd):
    """Verify the work directory before launch, remove exactly our own file."""
    fd, name = tempfile.mkstemp(prefix='.agentj-write-probe-', dir=cwd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'agentj busy write probe\n')
            stream.flush()
    finally:
        Path(name).unlink(missing_ok=True)


def reject_bypass(cmd):
    forbidden = {'--dangerously-skip-permissions', '--dangerously-bypass-approvals-and-sandbox',
                 '--yolo', '--allow-dangerously-skip-permissions'}
    if any(arg.split('=', 1)[0] in forbidden for arg in cmd):
        raise ValueError('unsafe permission bypass is not supported by busy run')


def check_detach(cmd):
    # A user manager executes outside a bwrap/sandbox-exec session. Never escape
    # that confinement merely because the manager socket happens to be visible.
    if os.environ.get('AGENTJ_FENCED') == '1' or os.environ.get('CODEX_SANDBOX_NETWORK_DISABLED') == '1':
        raise ValueError('detached manager cannot preserve this session sandbox; use foreground busy run')
    reject_bypass(cmd)
    # Native configuration can widen permissions independently of argv. Until
    # a trusted session-bound policy receipt is available, do not pretend that
    # copying argv proves the effective sandbox or approval policy.
    names = {Path(arg).name for arg in cmd if isinstance(arg, str)}
    if names & {'codex', 'claude', 'opencode', 'codex.js', 'claude.js', 'opencode.js'}:
        raise ValueError('native sandbox/approval cannot be verified for detached harness; use session-managed dispatch')
    if cmd and Path(cmd[0]).name in {'sh', 'bash', 'zsh', 'fish', 'env'}:
        raise ValueError('detached shell wrappers cannot prove the session permission boundary; use a direct command')


def manager_command(platform, label, argv, cwd, log):
    if not platform.startswith('linux'):
        raise ValueError('systemd is Linux only')
    return ['/usr/bin/systemd-run', '--user', '--quiet', '--collect', '--unit=' + label,
            '--property', 'Restart=no', '--property', 'Nice=10', '--property', 'CPUWeight=20',
            '--property', 'IOWeight=20', '--working-directory=' + cwd,
            '--property=StandardOutput=null', '--property=StandardError=null', '--', *argv]


def launchd_job(label, argv, cwd, log):
    return {'Label': label, 'ProgramArguments': argv, 'WorkingDirectory': cwd,
            'RunAtLoad': True, 'KeepAlive': False, 'Nice': 10,
            'ProcessType': 'Background', 'LowPriorityIO': True,
            'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}


def detach(root, a, cmd, key):
    """Start one supervisor, then wait for its actual busy registration or receipt."""
    from . import busy
    # Native work stays owned by the authenticated Host adapter, including its
    # existing approval broker. A user manager must never run a naked native CLI.
    if cmd and Path(cmd[0]).name in {'codex', 'claude', 'opencode'}:
        from . import names
        from .state import State
        reject_bypass(cmd)
        write_probe(os.getcwd())
        request = {'cmd': 'busy_native', 'key': key, 'argv': cmd, 'cwd': os.getcwd()}
        request.update({name: getattr(a, name, None) for name in ('who', 'label', 'report')})
        try:
            answer = names.ctl_call(State(root), request, timeout=10)
        except names.ServeBusy:
            raise RuntimeError('Host launch outcome unknown; inspect ' + key + '; never replay') from None
        if not answer or not answer.get('ok'):
            raise RuntimeError((answer or {}).get('error') or 'trusted Host unavailable; use the paired Host session; not launched')
        print(json.dumps(answer, ensure_ascii=False))
        return 0
    check_detach(cmd)
    write_probe(os.getcwd())
    argv = [sys.executable, '-m', 'agentj', 'busy', 'run', '--launch-token', key, '--managed']
    for name in ('who', 'label', 'kind', 'report', 'harness', 'model', 'workflow', 'role', 'parent', 'todo'):
        value = getattr(a, name, None)
        if value:
            argv += ['--' + name, value]
    for path in (getattr(a, 'watch', []) or [])[:16]:
        argv += ['--watch', path]
    if getattr(a, 'watch_git', False):
        argv += ['--watch-git']
    argv += ['--', *cmd]
    # Managers do not inherit the caller environment. Pin only the busy state
    # root; never ask the manager to copy credentials or authentication config.
    argv = ['/usr/bin/env', 'AGENTJ_STATE_DIR=' + str(Path(root).absolute()), *argv]
    label = 'agentj-busy-' + key.split(':', 1)[1]
    log = str(busy.items_dir(root) / (busy._safe(key) + '.log'))
    manager = 'session-supervisor'
    # Absence of a user bus permits a process-session fallback. An attempted
    # manager launch never falls back, because its result may be ambiguous.
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if sys.platform.startswith('linux') and runtime and Path(runtime, 'bus').exists() and Path('/usr/bin/systemd-run').exists():
        manager = 'systemd'
        result = subprocess.run(manager_command(sys.platform, label, argv, os.getcwd(), log),
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError('manager rejected launch; not replayed')
    elif sys.platform == 'darwin':
        from . import service
        from .state import _write_private
        directory = busy.items_dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (label + '.plist')
        _write_private(path, plistlib.dumps(launchd_job(label, argv, os.getcwd(), log)))
        result = service._launchctl('bootstrap', service._gui(), str(path), timeout=10)
        if result.returncode:
            raise RuntimeError('launchd outcome unknown or rejected; not replayed')
        manager = 'launchd'
    else:
        # No shell, no inherited terminal, no process-group dependency on caller.
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    ack = busy.items_dir(root) / ('.ack-' + busy._safe(key))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ack.exists():
            result = json.loads(ack.read_text())
            ack.unlink(missing_ok=True)
            print(f'{result["key"]} manager={manager}; sandbox/approval=native unchanged; no permission bypass')
            return 0
        time.sleep(.05)
    raise RuntimeError('launch unconfirmed; inspect this key, do not replay: ' + key)


def codex_session_snapshot(root, tid, now=None, limit=256):
    """Only this native thread's fixed dated rollout directory, metadata only."""
    import datetime
    import re
    if not isinstance(tid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', tid):
        return ()
    root = Path(root) / 'sessions'
    now = datetime.datetime.now(datetime.timezone.utc) if now is None else now
    out = []
    for delta in (0, 1):
        directory = root / (now - datetime.timedelta(days=delta)).strftime('%Y/%m/%d')
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            continue
        try:
            with os.scandir(directory) as entries:
                for i, entry in enumerate(entries):
                    if i >= limit:
                        break
                    if entry.is_symlink() or not entry.name.endswith('-' + tid + '.jsonl'):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    out.append((entry.name, info.st_mtime_ns, info.st_size))
        except OSError:
            pass
    return tuple(sorted(out))


def cpu_snapshot(pid, start=None, root=Path("/proc")):
    """Only pinned process counters; never argv, environment or transcript text."""
    try:
        fields = (root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        if start is not None and fields[19] != start:
            return None
        return fields[19], sum(int(fields[i]) for i in (11, 12, 13, 14))
    except (OSError, ValueError, IndexError):
        return None


def supervise(child, cwd, update, managed=False, watch=()):
    """Drain both streams concurrently; forward bytes unchanged in foreground.

    Store no raw output. Diagnostic tail is redacted only after complete lines;
    oversized lines are omitted as a whole, never truncated across a credential.
    """
    from .privacy import redact
    pin = cpu_snapshot(child.pid)
    def snapshot():
        return (cpu_snapshot(child.pid, pin[0]) if pin else None,
                tuple(tree_snapshot(path) for path in watch))
    progress = Progress(time.monotonic(), snapshot())
    tails = ['', '']

    def pump(stream, target, index):
        pending = b''
        overflow = False
        try:
            while True:
                data = os.read(stream.fileno(), 4096)
                if not data:
                    break
                was_stalled = progress.stalled
                progress.output(time.monotonic())
                if was_stalled:
                    update({"status": "running", "stalled_at": None})
                if not managed:
                    try:
                        target.buffer.write(data)
                        target.buffer.flush()
                    except (AttributeError, OSError, ValueError):
                        # Python test capture has no binary stream.
                        with contextlib.suppress(OSError, ValueError):
                            target.write(data.decode('utf-8', 'replace'))
                            target.flush()
                for part in data.splitlines(keepends=True):
                    if not overflow:
                        pending += part
                    if len(pending) > 16384:
                        pending = b''
                        overflow = True
                    if part.endswith(b'\n'):
                        text = '[oversized output omitted]' if overflow else redact(pending.decode('utf-8', 'replace'), paths=True)
                        tails[index] = (tails[index] + text)[-2048:]
                        pending, overflow = b'', False
            if pending or overflow:
                text = '[oversized output omitted]' if overflow else redact(pending.decode('utf-8', 'replace'), paths=True)
                tails[index] = (tails[index] + text)[-2048:]
        finally:
            stream.close()

    threads = [threading.Thread(target=pump, args=(stream, target, i), daemon=True)
               for i, (stream, target) in enumerate(((child.stdout, sys.stdout), (child.stderr, sys.stderr)))]
    for thread in threads:
        thread.start()
    while child.poll() is None:
        was_stalled = progress.stalled
        if progress.observe(time.monotonic(), snapshot()):
            update({'status': 'stalled', 'stall_action': 'notify-owner', 'stalled_at': time.time()})
        elif was_stalled and not progress.stalled:
            update({'status': 'running', 'stalled_at': None})
        time.sleep(.25)
    for thread in threads:
        thread.join(timeout=1)
    return child.returncode, redact('\n'.join(tails), paths=True)[-2048:]


def reserve_capacity(path, available, now=None):
    """Serialize admission and reserve startup room while load1 catches up.

    The reservation count is bounded by current capacity, never a fixed parallel
    job threshold. Reservations disappear with their busy item or after 60s.
    """
    import fcntl
    from .state import _write_private
    now = time.time() if now is None else now
    path = Path(path)
    fd = os.open(path.parent / '.admission.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if not path.exists():
            return False
        recent = 0
        with os.scandir(path.parent) as entries:
            for index, entry in enumerate(entries):
                if index >= 256:
                    # Never admit when the bounded scan cannot establish room.
                    return False
                if entry.name.startswith('.') or not entry.name.endswith('.json') or entry.is_symlink():
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_size > 4096:
                        continue
                    other = json.loads(Path(entry.path).read_text())
                    admitted = other.get('admitted_at')
                    if type(admitted) in (int, float) and 0 <= now - admitted < 60:
                        recent += 1
                except (OSError, ValueError, AttributeError):
                    continue
        if recent >= available:
            return False
        own = json.loads(path.read_text())
        own['admitted_at'] = now
        _write_private(path, json.dumps(own, ensure_ascii=False).encode())
        return True
    finally:
        os.close(fd)
