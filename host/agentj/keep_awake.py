"""Reversible idle-sleep policy; no passwords, GUI authorization or direct sudo.
System changes use the existing paired-phone Elevator. Status/dry-run never write.
"""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from . import elevate
from .state import State

TARGETS = ('sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target', 'suspend-then-hibernate.target')
LIMITS = 'Screen may turn off. Lid, battery exhaustion, shutdown and firmware policies can still stop the host. / 屏幕可关；合盖、电池耗尽、关机与固件策略仍可能使主机离线。'
WINDOWS = 'WSL cannot configure Windows host sleep. In Windows PowerShell: powercfg /query SCHEME_CURRENT SUB_SLEEP; powercfg /change standby-timeout-ac 0; powercfg /change hibernate-timeout-ac 0; then query again. Save original values first; restore those values afterwards. Battery: use -dc only deliberately. / WSL 内不能设置宿主防休眠，请在 Windows 主机操作，先记录原值，完成后读回验证；电池参数用 -dc。'


def read(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def system():
    if sys.platform == 'darwin':
        return 'macos'
    if sys.platform.startswith('win'):
        return 'windows'
    if sys.platform.startswith('linux'):
        try:
            if 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower():
                return 'wsl'
        except OSError:
            pass
        return 'linux' if Path('/run/systemd/system').is_dir() else 'unsupported'
    return 'unsupported'


def status():
    kind = system()
    result = {'system': kind, 'awake': None, 'limits': LIMITS}
    try:
        if kind == 'macos':
            code, out, _ = read(['/usr/bin/pmset', '-g', 'custom'])
            if code:
                raise ValueError('pmset read failed')
            values, scope = {}, None
            for line in out.splitlines():
                if line.strip() in ('AC Power:', 'Battery Power:'):
                    scope = 'ac' if line.strip() == 'AC Power:' else 'battery'
                m = re.fullmatch(r'\s*sleep\s+(\d+)\s*', line)
                if m and scope:
                    values[scope] = int(m[1])
            if 'ac' not in values:
                raise ValueError('pmset sleep value unavailable')
            result.update(sleep=values, awake=values['ac'] == 0,
                          battery_awake=values.get('battery') == 0 if 'battery' in values else None)
        elif kind == 'linux':
            states = {}
            for target in TARGETS:
                code, out, _ = read([shutil.which('systemctl') or '/usr/bin/systemctl', 'is-enabled', target])
                if out not in ('masked', 'masked-runtime', 'static', 'disabled', 'enabled', 'indirect', 'not-found', 'alias'):
                    raise ValueError('systemctl state unavailable: ' + target)
                states[target] = out
            # cat-config is informational: do not restart logind and disconnect the desktop.
            _, config, _ = read([shutil.which('systemd-analyze') or '/usr/bin/systemd-analyze', 'cat-config', 'systemd/logind.conf'])
            lid = {}
            for line in config.splitlines():
                m = re.fullmatch(r'\s*(HandleLidSwitch(?:ExternalPower|Docked)?|IdleAction)\s*=\s*(\w+)\s*', line)
                if m:
                    lid[m[1]] = m[2]
            result.update(targets=states, logind=lid, awake=all(v == 'masked' for v in states.values()))
        elif kind in ('wsl', 'windows'):
            result['guidance'] = WINDOWS
        else:
            result['guidance'] = 'No supported systemd host detected; configure the host power manager. / 未检测到支持的 systemd 主机，请设置宿主电源管理。'
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        result['error'] = str(e)
    return result


def load(path):
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('system') not in ('macos', 'linux'):
        raise ValueError('Invalid keep-awake restore record')
    if data['system'] == 'macos':
        if not isinstance(data.get('sleep'), dict) or not data['sleep'] or any(k not in ('ac', 'battery') or type(v) is not int or not 0 <= v <= 100000 for k, v in data['sleep'].items()):
            raise ValueError('Invalid saved pmset values')
    else:
        if not isinstance(data.get('targets'), list) or any(t not in TARGETS for t in data['targets']):
            raise ValueError('Invalid saved targets')
    return data


def plan(action, current, saved, power):
    kind = current['system']
    if current.get('error'):
        raise ValueError(current['error'])
    if kind not in ('linux', 'macos'):
        return [], None
    if saved and saved['system'] != kind:
        raise ValueError('Restore record belongs to another OS')
    if action == 'off' and not saved:
        return [], None
    if kind == 'macos':
        scopes = ['ac', 'battery'] if power == 'all' else [power]
        original = dict(saved['sleep']) if saved else {}
        commands = []
        if action == 'on':
            for scope in scopes:
                if scope not in current['sleep']:
                    raise ValueError('Requested power source unavailable: ' + scope)
                if current['sleep'][scope] != 0:
                    original.setdefault(scope, current['sleep'][scope])
                    commands.append(['/usr/bin/pmset', '-c' if scope == 'ac' else '-b', 'sleep', '0'])
        else:
            commands = [['/usr/bin/pmset', '-c' if k == 'ac' else '-b', 'sleep', str(v)] for k, v in original.items() if current['sleep'].get(k) != v]
        return commands, {'version': 1, 'system': kind, 'sleep': original} if original else None
    if power != 'ac':
        raise ValueError('--power is macOS-only; Linux masks apply to every power source')
    owned = list(saved['targets']) if saved else []
    if action == 'on':
        # Never replace a user-owned unit/override; mask would fail but could partially apply.
        todo = [t for t, v in current['targets'].items() if v not in ('masked', 'masked-runtime')]
        for t in todo:
            if os.path.lexists('/etc/systemd/system/' + t):
                raise ValueError('Existing system unit override: ' + t)
        owned = list(dict.fromkeys(owned + todo))
        commands = [[shutil.which('systemctl') or '/usr/bin/systemctl', 'mask', *todo]] if todo else []
    else:
        todo = [t for t in owned if current['targets'].get(t) == 'masked']
        commands = [[shutil.which('systemctl') or '/usr/bin/systemctl', 'unmask', *todo]] if todo else []
    return commands, {'version': 1, 'system': kind, 'targets': owned}


def verified(action, after, saved, power):
    if after.get('error'):
        return False
    if after['system'] == 'macos':
        want = (saved or {}).get('sleep', {}) if action == 'off' else {k: 0 for k in (('ac', 'battery') if power == 'all' else (power,))}
        return all(after['sleep'].get(k) == v for k, v in want.items())
    if action == 'on':
        return all(v in ('masked', 'masked-runtime') for v in after['targets'].values())
    return all(after['targets'].get(t) not in ('masked', 'masked-runtime') for t in (saved or {}).get('targets', []))


def execute(action='status', power='ac', dry_run=False, st=None):
    st = st or State()
    path = st.root / 'keep-awake.json'
    before = status()
    result = {'ok': not before.get('error'), 'action': action, 'before': before, 'dry_run': dry_run, 'commands': []}
    if action == 'status':
        return result
    if before['system'] not in ('linux', 'macos'):
        return {**result, 'ok': False, 'guidance_only': True}
    try:
        # Pure preview before any lock/state creation.
        saved = load(path)
        commands, record = plan(action, before, saved, power)
        result['commands'] = commands
        if dry_run:
            return result
        if not commands and not saved:
            return {**result, 'verified': True, 'after': before, 'note': 'No owned changes to restore / 没有本命令修改的设置可恢复' if action == 'off' else 'Already set / 已设置'}
        st.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(st.root / 'keep-awake.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Reread under lock: two concurrent requests cannot replace the restore snapshot.
            before = status(); saved = load(path)
            commands, record = plan(action, before, saved, power)
            result.update(before=before, commands=commands)
            if action == 'on' and record:
                st.write_private(path, json.dumps(record).encode())
            for argv in commands:
                receipt = elevate.client_request(st, {'t': 'sudo', 'argv': argv, 'cwd': '/', 'timeout': 60,
                    'why': 'Keep computer reachable / 保持电脑可远程连接',
                    'effect': ('Disable idle sleep; battery use can increase / 关闭主机自动休眠，可能增加耗电' if action == 'on' else 'Restore saved sleep settings / 恢复保存的休眠设置')})
                if receipt.get('result') != 'done' or receipt.get('code') != 0:
                    return {**result, 'ok': False, 'receipt': receipt, 'after': status(), 'restore_retained': True}
            after = status()
            good = verified(action, after, saved if action == 'off' else record, power)
            if good and action == 'off':
                path.unlink(missing_ok=True)
            return {**result, 'ok': good, 'verified': good, 'after': after, 'restore_retained': path.exists()}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {**result, 'ok': False, 'error': str(e)}


def cmd(a):
    # The host owns the restore record; fenced Agents see only elevate.sock.
    res = elevate.client_request(State(), {'t': 'keep_awake', 'action': a.action, 'power': a.power, 'dry_run': a.dry_run})
    if res.get('result') == 'unavailable':
        res = execute(a.action, a.power, True) if a.action == 'status' or a.dry_run else {'ok': False, 'error': elevate.MSG['unavailable']}
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res['ok'] else 1


def add_parser(sub):
    p = sub.add_parser("keep-awake", help="主机防休眠：status / on / off（恢复原值）/ reversible host idle-sleep policy")
    p.add_argument("action", nargs="?", choices=["status", "on", "off"], default="status")
    p.add_argument("--power", choices=["ac", "battery", "all"], default="ac", help="macOS: default AC only; battery/all increases drain")
    p.add_argument("--dry-run", action="store_true", help="Read and preview only; no state files or phone cards")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd(a)))
