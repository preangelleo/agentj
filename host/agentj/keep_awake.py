"""Reversible idle-sleep policy; no passwords, GUI authorization or direct sudo.
System changes use the existing paired-phone Elevator. Status/dry-run never write.

P418 (#183, Leo 10-20): `--lid` also keeps a closed lid awake on AC power — Linux a logind drop-in
(HandleLidSwitchExternalPower=ignore), macOS `pmset -c disablesleep 1` (global: a closed Mac then neither sleeps nor shuts
down, so the owner shuts it down before a flight / when Agent J has nothing to do). `setup` is the one install-end question
(terminal [Y/n], Enter = yes; `--phone` = an Agent J card, first option = yes) → keep-awake on (+ lid) and, on Linux,
linger (Agent J starts at boot without a login); every change still goes through the phone password card, then read back.
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
from .text import pick

TARGETS = ('sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target', 'suspend-then-hibernate.target')
LIMITS = 'Screen may turn off. Without --lid a closed lid can still sleep; battery exhaustion, shutdown and firmware policies can still stop the host. / 屏幕可关；不带 --lid 时合盖仍可能休眠；电池耗尽、关机与固件策略仍可能使主机离线。'
LID_DROPIN = '/etc/systemd/logind.conf.d/90-agentj-keep-awake.conf'
LID_SCRIPT = ("mkdir -p /etc/systemd/logind.conf.d && printf '%s\\n' '# Agent J keep-awake: agentj keep-awake off removes this file' "
              "'[Login]' 'HandleLidSwitchExternalPower=ignore' > " + LID_DROPIN)
OFFER = 'keep-awake-offer.json'
LID_EFFECT = ('Lid closed on AC power no longer sleeps; shut down before travel / 插电合盖也不睡；出门、长时间不用请主动关机')
WINDOWS = 'WSL cannot configure Windows host sleep. In Windows PowerShell: powercfg /query SCHEME_CURRENT SUB_SLEEP; powercfg /change standby-timeout-ac 0; powercfg /change hibernate-timeout-ac 0; then query again. Lid closed on AC: powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0; powercfg /setactive SCHEME_CURRENT. Save original values first; restore those values afterwards. Battery: use -dc only deliberately. / WSL 内不能设置宿主防休眠，请在 Windows 主机操作（插电合盖不睡：LIDACTION 0），先记录原值，完成后读回验证；电池参数用 -dc。'


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
            # P418: disablesleep is global (not in `-g custom`); `pmset -g` lists SleepDisabled only on hosts that have it
            code, out, _ = read(['/usr/bin/pmset', '-g'])
            m = re.search(r'^\s*SleepDisabled\s+(\d+)\s*$', out, re.M) if not code else None
            result.update(disablesleep=int(m[1]) if m else 0, lid_awake=bool(m and m[1] != '0'))
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
            result.update(targets=states, logind=lid, awake=all(v == 'masked' for v in states.values()),
                          lid_awake=lid.get('HandleLidSwitchExternalPower', lid.get('HandleLidSwitch')) == 'ignore',
                          lid_owned=os.path.lexists(LID_DROPIN))
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
        if 'disablesleep' in data and (type(data['disablesleep']) is not int or data['disablesleep'] not in (0, 1)):
            raise ValueError('Invalid saved disablesleep value')
        if not isinstance(data.get('sleep'), dict) or not (data['sleep'] or 'disablesleep' in data) or any(k not in ('ac', 'battery') or type(v) is not int or not 0 <= v <= 100000 for k, v in data['sleep'].items()):
            raise ValueError('Invalid saved pmset values')
    else:
        if not isinstance(data.get('targets'), list) or any(t not in TARGETS for t in data['targets']):
            raise ValueError('Invalid saved targets')
        if type(data.get('lid', False)) is not bool:
            raise ValueError('Invalid saved lid flag')
    return data


def plan(action, current, saved, power, lid=False):
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
        # P418: the owner's own disablesleep value is kept once and put back by `off`
        dis = (saved or {}).get('disablesleep')
        if action == 'on' and lid and current.get('disablesleep', 0) != 1:
            dis = current.get('disablesleep', 0) if dis is None else dis
            commands.append(['/usr/bin/pmset', '-c', 'disablesleep', '1'])
        elif action == 'off' and dis is not None and current.get('disablesleep', 0) != dis:
            commands.append(['/usr/bin/pmset', '-c', 'disablesleep', str(dis)])
        record = {'version': 1, 'system': kind, 'sleep': original}
        if dis is not None:
            record['disablesleep'] = dis
        return commands, record if original or dis is not None else None
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
    lid_owned = bool((saved or {}).get('lid'))
    if action == 'on' and lid and not os.path.lexists(LID_DROPIN):
        # P418: a drop-in of our own; the owner's logind.conf and other drop-ins stay untouched. Effective at the next
        # logind start (a restart could end the desktop session) — until then the masked sleep targets keep the lid awake.
        commands.append(['/bin/sh', '-c', LID_SCRIPT])
        lid_owned = True
    elif action == 'on' and lid and not lid_owned:
        raise ValueError('Existing lid drop-in not created by Agent J: ' + LID_DROPIN)
    elif action == 'off' and lid_owned and os.path.lexists(LID_DROPIN):
        commands.append(['/bin/rm', '-f', LID_DROPIN])
    record = {'version': 1, 'system': kind, 'targets': owned}
    if lid_owned:
        record['lid'] = True
    return commands, record


def verified(action, after, saved, power, lid=False):
    if after.get('error'):
        return False
    if after['system'] == 'macos':
        want = (saved or {}).get('sleep', {}) if action == 'off' else {k: 0 for k in (('ac', 'battery') if power == 'all' else (power,))}
        dis = (saved or {}).get('disablesleep') if action == 'off' else (1 if lid else None)
        return all(after['sleep'].get(k) == v for k, v in want.items()) and (dis is None or after.get('disablesleep', 0) == dis)
    if action == 'on':
        return all(v in ('masked', 'masked-runtime') for v in after['targets'].values()) and (not lid or bool(after.get('lid_owned')))
    return all(after['targets'].get(t) not in ('masked', 'masked-runtime') for t in (saved or {}).get('targets', [])) \
        and not ((saved or {}).get('lid') and after.get('lid_owned'))


def execute(action='status', power='ac', dry_run=False, st=None, lid=False):
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
        commands, record = plan(action, before, saved, power, lid)
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
            commands, record = plan(action, before, saved, power, lid)
            result.update(before=before, commands=commands)
            if action == 'on' and record:
                st.write_private(path, json.dumps(record).encode())
            for argv in commands:
                receipt = elevate.client_request(st, {'t': 'sudo', 'argv': argv, 'cwd': '/', 'timeout': 60,
                    'why': 'Keep computer reachable / 保持Agent J 主机可远程连接',
                    'effect': (LID_EFFECT if action == 'on' and 'disablesleep' in argv or LID_SCRIPT in argv else
                               'Disable idle sleep; battery use can increase / 关闭主机自动休眠，可能增加耗电' if action == 'on' else 'Restore saved sleep settings / 恢复保存的休眠设置')})
                if receipt.get('result') != 'done' or receipt.get('code') != 0:
                    return {**result, 'ok': False, 'receipt': receipt, 'after': status(), 'restore_retained': True}
            after = status()
            good = verified(action, after, saved if action == 'off' else record, power, lid)
            if good and action == 'off':
                path.unlink(missing_ok=True)
            return {**result, 'ok': good, 'verified': good, 'after': after, 'restore_retained': path.exists()}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {**result, 'ok': False, 'error': str(e)}


# ------------------------------------------------------------------ P418 (#183): the install-end question
Q = {'zh': '最后一步：把这台电脑设成「永不休眠」吗？插着电时它不会自动睡眠，合上盖子也不睡，Agent J 随时能干活（屏幕照样会关）。',
     'en': 'Last step: keep this computer awake? On power it never goes to sleep, not even with the lid closed, so Agent J can '
           'always work (the screen still turns off).'}
Q_MAC = {'zh': '再确认一下 Mac 合盖：设好后，合上盖子电脑既不睡也不关机。长时间坐飞机 / 离开网络、或者不需要 Agent J 干活时，'
               '一定要主动关机。合盖也不睡？',
         'en': 'One more thing for the Mac lid: once set, a closed Mac neither sleeps nor shuts down. Before a long flight, away '
               'from the network, or when Agent J has nothing to do, always shut it down yourself. Keep it awake with the lid closed?'}
REMIND = {'zh': '记住：合盖也不休眠、也不关机。长时间离开网络或不需要 Agent J 干活时，要主动关机。',
          'en': 'Remember: a closed lid no longer sleeps or shuts down. Away from the network for long, or nothing for Agent J to '
                'do? Shut the computer down yourself.'}
MAC_BOOT = {'zh': '重启后（开了 FileVault 的 Mac）要登录一次，登录后 Agent J 自动起来。想停电恢复后自动开机，可选：sudo pmset autorestart 1。',
            'en': 'After a restart (a Mac with FileVault), log in once and Agent J starts by itself. Optional, to power on again after '
                  'a power cut: sudo pmset autorestart 1.'}
CARD = {'zh': ('是（推荐）', '以后再说', '是，合盖也不睡', '是，但合盖照常睡'),
        'en': ('Yes (recommended)', 'Not now', 'Yes, lid too', 'Yes, lid sleeps')}


def offer_answer(st) -> dict | None:
    """The owner's recorded answer to the install-end question (None = never asked here)."""
    try:
        d = json.loads((st.root / OFFER).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get('answer') in ('yes', 'no', 'guidance') else None


def _remember(st, answer, lid, via):
    import time
    with __import__('contextlib').suppress(OSError):
        st.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        st.write_private(st.root / OFFER, json.dumps({'answer': answer, 'lid': bool(lid), 'via': via,
                                                     'at': int(time.time())}).encode())


def _user():
    import getpass
    u = os.environ.get('USER') or ''
    try:
        u = u or getpass.getuser()
    except Exception:  # noqa: BLE001
        pass
    return u if re.fullmatch(r'[A-Za-z0-9._][A-Za-z0-9._-]{0,63}', u or '') else ''


def linger_on(st) -> dict:
    """Linux: Agent J's user service starts at boot without a login. `loginctl enable-linger` as the owner first (polkit
    allows it for an active local session); refused → the same command through the phone password card. Read back."""
    from . import reset, service
    if system() != 'linux':
        return {'ok': True, 'state': 'not_applicable'}
    before = service._linger()
    if before == 'yes':
        return {'ok': True, 'state': 'already'}
    if before is None:
        return {'ok': False, 'state': 'unavailable'}
    res = reset.run_linger(st)
    if not (res.get('ok') and service._linger() == 'yes'):
        user, exe = _user(), shutil.which('loginctl') or '/usr/bin/loginctl'
        if not user:
            return {'ok': False, 'state': 'unavailable'}
        receipt = elevate.client_request(st, {'t': 'sudo', 'argv': [exe, 'enable-linger', user], 'cwd': '/', 'timeout': 60,
            'why': 'Start Agent J at boot without a login / 开机不用登录也自动运行 Agent J',
            'effect': 'Agent J runs after a restart before anyone logs in / 重启后没人登录时 Agent J 也运行'})
        if receipt.get('result') != 'done' or receipt.get('code') != 0:
            return {'ok': False, 'state': 'refused', 'receipt': {k: receipt.get(k) for k in ('result', 'code')}}
    good = service._linger() == 'yes'
    return {'ok': good, 'state': 'enabled' if good else 'unverified'}


def _ask_terminal(text, lang):
    try:
        ans = input(text + (' [Y/n] 回车 = 是：' if lang == 'zh' else ' [Y/n] Enter = yes: ')).strip().lower()
    except EOFError:
        ans = ''
    except KeyboardInterrupt:
        print()
        return False
    return ans in ('', 'y', 'yes', '是', '好', '要')


def _ask_phone(st, kind, lang):
    """→ (answer 'yes' | 'no' | None (not shown / not answered), lid). One Agent J card; the first option is yes."""
    from .names import ServeBusy, ctl_call
    c = CARD[lang]
    if kind == 'macos':
        opts = [{'label': c[2], 'description': REMIND[lang]}, {'label': c[3]}, {'label': c[1]}]
        text = Q[lang] + '\n' + REMIND[lang]
    else:
        opts = [{'label': c[0], 'description': pick(lang, '插电合盖也不睡；开机不用登录就自动运行。', 'Lid closed on power stays awake; '
                                                     'starts at boot without a login.')}, {'label': c[1]}]
        text = Q[lang]
    q = {'question': text, 'header': pick(lang, '防休眠', 'Keep awake'), 'options': opts, 'other': False}
    try:
        res = ctl_call(st, {'cmd': 'ask_open', 'questions': [q], 'wait': 170}, timeout=190, max_bytes=256 * 1024)
    except ServeBusy:
        res = None
    if not res or res.get('result') != 'answered':
        return (None if not res or res.get('result') not in ('cancelled', 'timeout') else 'no'), False
    picked = (res.get('answers') or [{}])[0].get('picked') or []
    if c[1] in picked:
        return 'no', False
    return 'yes', c[2] in picked or (kind != 'macos' and c[0] in picked)


def setup(st, mode='terminal', lid=None, lang='zh', out=print) -> dict:
    """The one install-end question. mode: terminal ([Y/n], Enter = yes) | yes (already asked in chat) | phone (an Agent
    J card). Linux keeps the lid awake unless --no-lid; macOS only after its own second yes (terminal / card) or --lid."""
    lang = 'en' if lang == 'en' else 'zh'
    kind = system()
    res = {'ok': True, 'system': kind, 'mode': mode}
    if kind not in ('linux', 'macos'):
        out(pick(lang, '这台电脑的电源设置要在宿主系统里改：', 'Set power on the host system: ') + one(lang, WINDOWS if kind in ('wsl', 'windows') else
            'No supported systemd host detected; configure the host power manager. / 未检测到支持的 systemd 主机，请设置宿主电源管理。'))
        _remember(st, 'guidance', False, mode)
        return {**res, 'answer': 'guidance'}
    if mode == 'terminal':
        yes = _ask_terminal(Q[lang], lang)
        if yes and kind == 'macos' and lid is None:
            lid = _ask_terminal(Q_MAC[lang], lang)
    elif mode == 'phone':
        answer, picked_lid = _ask_phone(st, kind, lang)
        if answer is None:
            out(pick(lang, '手机上没弹出 / 没人回答这张卡片；以后在电脑上运行 agentj keep-awake setup。',
                     'The card was not shown or not answered; run agentj keep-awake setup on the computer later.'))
            return {**res, 'ok': False, 'answer': None}
        yes, lid = answer == 'yes', picked_lid if lid is None else lid
    else:
        yes = True
    if lid is None:
        lid = kind == 'linux'
    if not yes:
        _remember(st, 'no', False, mode)
        out(pick(lang, '好，没改。以后想设：agentj keep-awake setup。', 'OK, nothing changed. Later: agentj keep-awake setup.'))
        return {**res, 'answer': 'no'}
    out(pick(lang, '要改系统设置：手机上会弹出密码卡，在手机上输入这台电脑的登录密码批准（每一项一张）。',
             'This changes system settings: a password card opens on your phone; approve it with this computer\'s login password '
             '(one card per change).'))
    r = elevate.client_request(st, {'t': 'keep_awake', 'action': 'on', 'power': 'ac', 'dry_run': False, 'lid': bool(lid)})
    if r.get('result') == 'unavailable':
        r = {'ok': False, 'error': elevate.MSG['unavailable']}
    res.update(answer='yes', lid=bool(lid), keep_awake={k: r.get(k) for k in ('ok', 'verified', 'error', 'note')})
    lg = linger_on(st)          # independent of the sleep cards: a refused sleep change still gets the boot start
    res['linger'] = lg
    after = status()
    res['after'] = {k: after.get(k) for k in ('awake', 'lid_awake', 'disablesleep')}
    ok_awake, ok_lid = after.get('awake') is True, (not lid) or after.get('lid_awake') is True
    lines = [('✓ ' if ok_awake else '✗ ') + pick(lang, '插电时不自动睡眠', 'No idle sleep on power')]
    if lid:
        lines.append(('✓ ' if ok_lid else '✗ ') + pick(lang, '合盖也不睡', 'Lid closed stays awake')
                     + (pick(lang, '（重启后合盖设置正式生效，在那之前睡眠已被禁止，合盖同样不会睡）',
                             ' (the lid setting takes full effect after a restart; sleep is already blocked meanwhile)')
                        if kind == 'linux' and ok_lid and any(LID_SCRIPT in c for c in r.get('commands') or []) else ''))
    if kind == 'linux':
        lines.append(('✓ ' if lg.get('ok') else '✗ ') + pick(lang, '开机不用登录也自动运行 Agent J', 'Agent J starts at boot without a login'))
    if r.get('error') or not r.get('ok'):
        lines.append(pick(lang, '没设成：', 'Not done: ') + one(lang, str(r.get('error') or r.get('note') or
                     pick(lang, '手机上的密码卡没有批准', 'the phone password card was not approved'))))
    lines.append(pick(lang, '想恢复原来的休眠设置：agentj keep-awake off', 'To restore the original sleep settings: agentj keep-awake off')
                 + (pick(lang, '；不想开机自启：loginctl disable-linger', '; no start at boot: loginctl disable-linger') if kind == 'linux' else ''))
    if kind == 'macos':
        if lid and ok_lid:
            lines.append('⚠ ' + REMIND[lang])
        lines.append(MAC_BOOT[lang])
    out('\n'.join(lines))
    res['ok'] = bool(r.get('ok')) and ok_awake and ok_lid and lg.get('ok', True)
    _remember(st, 'yes', lid, mode)
    return res


def one(lang, text):
    from .text import one as _one
    return _one(lang, text)


def cmd(a):
    if a.action == 'setup':
        st = State()
        lang = a.lang
        if not lang:
            try:
                from . import main_identity
                lang = main_identity.language_of(st.agent_config() or {})
            except Exception:  # noqa: BLE001
                lang = 'zh'
        mode = 'phone' if a.phone else 'yes' if a.yes else 'terminal'
        if a.once and offer_answer(st):      # install.sh: a rerun never asks the same question again
            print(pick(lang, '防休眠已经问过了（改主意：agentj keep-awake setup）。', 'Keep-awake was already asked (to change it: agentj keep-awake setup).'))
            return 0
        if mode == 'terminal' and not sys.stdin.isatty():
            print(Q[lang] + '\n' + pick(lang, '（这里不是终端：在电脑终端里运行 agentj keep-awake setup；主人在聊天里已答「是」就加 --yes，'
                                         '或用 --phone 在手机上弹卡片问。）', '(Not a terminal: run agentj keep-awake setup in a '
                                         'terminal; add --yes when the owner already said yes in chat, or --phone to ask on the phone.)'))
            res = {'ok': True, 'answer': None, 'mode': 'no_terminal'}
        else:
            res = setup(st, mode, a.lid, lang, out=(lambda *_: None) if a.json else print)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        return 0 if res['ok'] else 1
    # The host owns the restore record; fenced Agents see only elevate.sock.
    req = {'t': 'keep_awake', 'action': a.action, 'power': a.power, 'dry_run': a.dry_run}
    if a.lid:
        req['lid'] = True
    res = elevate.client_request(State(), req)
    if res.get("result") == 'unavailable':
        res = execute(a.action, a.power, True, lid=bool(a.lid)) if a.action == 'status' or a.dry_run else {'ok': False, 'error': elevate.MSG['unavailable']}
    if res.get('before', {}).get('system') == 'macos' and (res.get('after') or res['before']).get('lid_awake'):
        res['reminder'] = REMIND['zh'] + ' / ' + REMIND['en']     # P418 (#183): said on every Mac status while it holds
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res['ok'] else 1


def add_parser(sub):
    p = sub.add_parser("keep-awake", help="主机防休眠：status / on / off（恢复原值）/ setup（安装收尾那一问）/ reversible host idle-sleep policy")
    p.add_argument("action", nargs="?", choices=["status", "on", "off", "setup"], default="status")
    p.add_argument("--power", choices=["ac", "battery", "all"], default="ac", help="macOS: default AC only; battery/all increases drain")
    p.add_argument("--lid", action="store_true", default=None, help="插电合盖也不睡 / keep a closed lid awake on AC (Linux logind drop-in; macOS disablesleep)")
    p.add_argument("--no-lid", dest="lid", action="store_false", default=None, help="setup: 合盖照常睡 / setup: the lid still sleeps")
    p.add_argument("--yes", action="store_true", help="setup: 主人已在聊天里同意 / setup: the owner already said yes")
    p.add_argument("--phone", action="store_true", help="setup: 在手机上弹卡片问（第一项 = 是）/ setup: ask with a phone card (first option = yes)")
    p.add_argument("--once", action="store_true", help="setup: 已经问过就不再问（安装脚本用）/ setup: skip when already answered (installers)")
    p.add_argument("--lang", choices=["zh", "en"])
    p.add_argument("--dry-run", action="store_true", help="Read and preview only; no state files or phone cards")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd(a)))
