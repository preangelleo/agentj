"""#510: host-only evidence for a restart of this installed, running service.

No shell is executed here. Never return service environment or file contents to callers.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from . import service


def _manager_env():
    return {'PATH': '/usr/bin:/bin', 'XDG_RUNTIME_DIR': '/run/user/' + str(os.getuid()),
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + str(os.getuid()) + '/bus'}


def _manager(*args, timeout=3):
    import subprocess
    return subprocess.run(['/usr/bin/systemctl', '--user', *args], capture_output=True, text=True,
                          timeout=timeout, env=_manager_env())


def _dropin_dirs(n):
    unit_root = Path(service.unit_dir())
    return (unit_root / (n + '.service.d'), unit_root.parent / 'user.control' / (n + '.service.d'))


def _resource_dropin(path, n):
    # systemctl set-property uses user.control and emits comment headers.
    if path.parent not in _dropin_dirs(n) or path.resolve().parent != path.parent.absolute():
        return False
    lines = [line.strip() for line in path.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith(('#', ';'))]
    return bool(re.fullmatch(r'\[Service\]\n(?:CPUWeight|IOWeight)=[1-9][0-9]{0,3}', '\n'.join(lines)))


def _snapshot():
    try:
        n = service.name()
        paths = [Path(service.unit_dir()) / (n + '.service'), Path(service.env_file(n))]
        for root in _dropin_dirs(n):
            paths += sorted(root.glob('*.conf'))
        return tuple((str(p), hashlib.sha256(p.read_bytes()).digest() if p.exists() else None) for p in paths)
    except (OSError, ValueError):
        return None


# Taken when serve imports us, before accepting any tool request; never persisted.
_INITIAL = _snapshot()


def shape(tool, inp):
    if tool not in ('Bash', 'exec_command', 'shell_command') or not isinstance(inp, dict):
        return None
    key = 'cmd' if tool == 'exec_command' else 'command'
    if set(inp) - {key, 'description', 'timeout', 'yield_time_ms', 'max_output_tokens'} or key not in inp:
        return None
    text = inp[key]
    if not isinstance(text, str) or re.search(r'[\n\r;$`|&<>\\()]', text):
        return None
    try:
        args = shlex.split(text)
    except ValueError:
        return None
    if args == ['agentj', 'service', 'restart']:
        return args
    if args[:1] != ['systemd-run']:
        return None
    seen, i = set(), 1
    while i < len(args) and args[i].startswith('--'):
        key, eq, value = args[i].partition('=')
        if key in seen or key not in ('--user', '--collect', '--unit', '--on-active'):
            return None
        seen.add(key)
        if key in ('--user', '--collect'):
            if eq:
                return None
        else:
            if not eq:
                i += 1
                value = args[i] if i < len(args) else ''
            if key == '--unit':
                if not re.fullmatch(r'agentj-restart-[A-Za-z0-9_-]{1,40}', value):
                    return None
            elif not re.fullmatch(r'[0-9]{1,3}(?:s)?', value) or not 1 <= int(value.rstrip('s')) <= 300:
                return None
        i += 1
    return args if {'--user', '--on-active'} <= seen and args[i:] == ['agentj', 'service', 'restart'] else None


def _installed():
    # Wheel RECORD is inside the same fenced read-only installation as the code.
    dist = importlib.metadata.distribution('agentj')
    files = dist.files or []
    checked = set()
    for f in files:
        if str(f).startswith('agentj/') and str(f).endswith('.py') or Path(os.path.abspath(dist.locate_file(f))) == Path(sys.executable).absolute().parent / 'agentj':
            if not f.hash or f.hash.mode != 'sha256':
                return False
            digest = base64.urlsafe_b64encode(hashlib.sha256(Path(dist.locate_file(f)).read_bytes()).digest()).decode().rstrip('=')
            if digest != f.hash.value:
                return False
            checked.add(str(f))
    return {'agentj/serve.py', 'agentj/service.py', 'agentj/cli.py', 'agentj/restart_guard.py'} <= checked and any(Path(os.path.abspath(dist.locate_file(f))) == Path(sys.executable).absolute().parent / 'agentj' and str(f) in checked for f in files)


def prove(tool, inp):
    args = shape(tool, inp)
    if not args:
        return None
    try:
        from . import fence
        if service.platform() != 'linux' or not fence.code_ro() or _INITIAL is None or _snapshot() != _INITIAL or not _installed():
            return None
        exe = Path(sys.executable).absolute().parent / 'agentj'
        if not exe.is_file() or not os.access(exe, os.X_OK):
            return None
        script = exe.read_text()
        if script.splitlines()[0] != '#!' + str(Path(sys.executable).absolute()) or 'from agentj.cli import main' not in script:
            return None
        manager = _manager('show-environment')
        if manager.returncode or any(re.match(r'(?:PYTHON[A-Z_]*|LD_[A-Z_]*|BASH_ENV|ENV)=', line)
                                     for line in manager.stdout.splitlines()):
            return None
        env_path = Path(service.env_file(service.name()))
        if env_path.exists() and any(re.match(r'\s*(?:export\s+)?(?:PYTHON[A-Z_]*|LD_[A-Z_]*|BASH_ENV|ENV)\s*=', line)
                                     for line in env_path.read_text().splitlines()):
            return None
        n = service.name()
        r = _manager('show', n + '.service', '--property=MainPID,FragmentPath,DropInPaths,NeedDaemonReload', timeout=3)
        if r.returncode:
            return None
        props = dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)
        unit = Path(service.unit_dir()) / (n + '.service')
        if any(props.get(k) != v for k, v in {'MainPID': str(os.getpid()), 'FragmentPath': str(unit), 'NeedDaemonReload': 'no'}.items()):
            return None
        for drop in shlex.split(props.get('DropInPaths', '')):
            path = Path(drop)
            if not _resource_dropin(path, n):
                return None
        raw = unit.read_text()
        env = {}
        for line in raw.splitlines():
            if line.startswith('Environment='):
                words = shlex.split(line.partition('=')[2])
                if len(words) != 1 or '=' not in words[0]:
                    return None
                k, v = words[0].split('=', 1)
                if k in env:
                    return None
                env[k] = v.replace('%%', '%')
        from .instance import ENV as INSTANCE_ENV
        allowed = {'PATH', *service.CAPTURED_ENV, *service.PROXY_ENV, INSTANCE_ENV}
        if set(env) - allowed:
            return None
        # Older installers wrote binary selections inline; they are unrelated to the host executable.
        projected = '\n'.join(line for line in raw.split('\n') if not any(
            line.startswith('Environment=' + service._sd_quote(k + '=' + env[k]))
            for k in service.CAPTURED_ENV[1:] if k in env))
        if projected != service.unit_text(n, [str(exe)], env):
            return None
        if args[0] == 'systemd-run':
            if not Path('/usr/bin/systemd-run').is_file():
                return None
            args[0] = '/usr/bin/systemd-run'
            args[-3] = str(exe)
        else:
            args[0] = str(exe)
        key = 'command' if 'command' in inp else 'cmd'
        return {**inp, key: shlex.join(args)}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError, service.ServiceError):
        return None


def schedule(tool, inp, st):
    """Detached manager timer survives serve exit; no caller-controlled command or environment."""
    import secrets
    import subprocess
    if os.environ.get('AGENTJ_TEST') == '1':
        raise RuntimeError('restart_disabled_in_tests')
    from .state import state_dir
    root = Path(st.root).resolve()
    if root != state_dir().resolve() or not root.is_relative_to(Path.home().resolve()):
        raise RuntimeError('restart_not_live_state')
    live = _manager('show', service.name() + '.service', '--property=MainPID', '--value', timeout=3)
    if live.returncode or live.stdout.strip() != str(os.getpid()):
        raise RuntimeError('restart_not_service_process')
    if prove(tool, inp) is None:
        raise RuntimeError('restart_proof_changed')
    args = shape(tool, inp)
    if not args:
        raise ValueError('shape')
    delay = 2
    for i, word in enumerate(args):
        if word == '--on-active':
            delay = int(args[i + 1].rstrip('s'))
        elif word.startswith('--on-active='):
            delay = int(word.split('=', 1)[1].rstrip('s'))
    try:
        r = subprocess.run(['/usr/bin/systemd-run', '--user', '--collect',
                        '--unit=agentj-restart-' + secrets.token_hex(6), '--on-active=' + str(delay) + 's',
                        '/usr/bin/systemctl', '--user', 'restart', service.name() + '.service'],
                       capture_output=True, timeout=5, env=_manager_env())
    except subprocess.SubprocessError as exc:
        raise RuntimeError('restart_schedule_unknown_do_not_retry') from exc
    if r.returncode:
        raise RuntimeError('restart_schedule_failed')
