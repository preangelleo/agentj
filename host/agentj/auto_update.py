"""Opt-out nightly upgrades. Private journal and verified wheels survive restart.

No credentials, terminal input, native-session clearing, or arbitrary checkout edits.
The manager owns a restartable worker independently of the serve cgroup.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
from . import __version__, activity, controls, minisign, preferences, update

RECORD = 'auto-update.json'
JOB = 'auto-update-job.json'
IDLE = 1800
MAX_WHEEL = 32 * 1024 * 1024
ORIGIN = 'https://agentj.app/dl/'
# P393 (#121): the post-upgrade doctor is asked again (up to SELF_CHECK_TRIES, SELF_CHECK_WAIT s apart) while the only
# blockers are a just-restarted serve / service manager that has not answered yet.
SELF_CHECK_TRIES = 3
SELF_CHECK_WAIT = 10
TRANSIENT_CHECKS = frozenset(('serve', 'service'))


def read(st, name=RECORD):
    try:
        r = json.loads((st.root / name).read_text())
        return r if isinstance(r, dict) else {}
    except (OSError, ValueError):
        return {}


def write(st, rec, name=RECORD):
    st.write_private(st.root / name, json.dumps(rec, ensure_ascii=False).encode())


def enabled(st):
    try:
        return preferences.get(preferences.effective(st), 'updates.auto_install', True) is not False
    except (OSError, ValueError):
        return False


def command(args):
    import argparse
    p = argparse.ArgumentParser(prog='agentj config auto-update')
    p.add_argument('switch', choices=('on', 'off', 'status'))
    a = p.parse_args(args)
    if a.switch != 'status':
        rc = preferences.command(['set', 'updates.auto_install', 'true' if a.switch == 'on' else 'false'])
        if rc:
            return rc
    from .state import State
    st = State()
    print(json.dumps({'enabled': enabled(st), 'last': read(st).get('result'),
                      'window': '03:00–05:00 local time'}, ensure_ascii=False))
    return 0


def touch(st, now=None):
    with st.config_lock():
        return _touch(st, now)


def _touch(st, now=None):
    """Only authenticated phone activity calls this; independent of log/history switches."""
    rec = read(st)
    rec['phone_at'] = time.time() if now is None else now
    write(st, rec)


def busy(host):
    a = host.agent
    ps = getattr(host.peers, 'sessions', None)
    return bool((a and (a.status != 'idle' or (getattr(a, 'q', None) and not a.q.empty()))) or host._open_question() or any(not a.fut.done() for a in host.asks.values())
                or host.scheduler.current_id or host.elevate.cards
                or getattr(getattr(host, 'bots', None), 'phone_tasks', None)
                or (ps and (ps.active or ps._procs))
                or (host.peers and (getattr(host.peers, 'jobs', None) or getattr(host.peers, 'asks', None))))


SOON = 900   # P203: a scheduled task due within 15 minutes is on the upgrade card's list


IMPACT_TEXT = {   # P203: the upgrade card's lines (the phone has its own i18n copy: upg.k.*)
    'turn': ('Agent J 正在进行的回合', "Agent J's turn in progress"),
    'shared_turn': ('共享会话里正在进行的回合', 'the turn running in the shared session'),
    'queue': ('排队等待的 {n} 条消息', '{n} queued message(s)'),
    'ask': ('{n} 张等待中的审批卡', '{n} approval card(s) waiting'),
    'question': ('{n} 张等待中的问题卡', '{n} question card(s) waiting'),
    'secret': ('{n} 张等待中的 sudo / 密钥卡', '{n} sudo / secret card(s) waiting'),
    'card': ('{n} 张其他待处理卡片（领取、登录、联系人、开工卡）', '{n} other open card(s) (pickup, sign-in, contact, opening)'),
    'friend_card': ('{n} 张好友卡', '{n} friend card(s)'),
    'friend': ('{n} 个正在进行的好友会话', '{n} friend session(s) in progress'),
    'task': ('正在跑的定时任务', 'a scheduled task that is running'),
    'task_queue': ('{n} 个排队的定时任务', '{n} scheduled task(s) queued'),
    'bots': ('{n} 个机器人后台操作', '{n} bot operation(s) in the background'),
    'bg': ('{n} 个后台任务（在办事项）', '{n} background task(s) (in progress list)'),
    'task_soon': ('{n} 个 15 分钟内要跑的定时任务', '{n} scheduled task(s) due within 15 minutes'),
}


def impact_text(items, lang='zh'):
    """One line per item, in the owner's language (unknown kinds are skipped)."""
    i = 1 if lang == 'en' else 0
    return [IMPACT_TEXT[x['k']][i].format(n=x['n']) for x in items if x.get('k') in IMPACT_TEXT]


def newer_known(st):
    """P203: the daily check (update.json) already saw a version newer than this one — no network here."""
    latest = update.read_rec(st).get('latest')
    return isinstance(latest, str) and update.compare(latest, __version__) == 1


def impact(host, caller=None, now=None):
    """P203: what restarting the host now would interrupt — the upgrade card's list, counts and kinds only (no text the
    Agent wrote). [] = nothing: upgrade at once. caller='agent': `agentj update apply` run by the Agent itself, whose own
    turn ends before the restart (the manager waits for an idle serve), so the running turn is not listed."""
    items = []
    def add(k, n=1):
        if n:
            items.append({'k': k, 'n': int(n)})
    a = host.agent
    shared = (getattr(host, 'agent_cfg', None) or {}).get('session_mode') == 'shared'
    if a and a.status != 'idle' and caller != 'agent':
        add('shared_turn' if shared else 'turn')
    q = getattr(a, 'q', None) if a else None
    with_q = q.qsize() if q is not None and hasattr(q, 'qsize') else 0
    add('queue', with_q)
    add('ask', sum(1 for x in host.asks.values() if not x.fut.done()))
    add('question', sum(1 for x in getattr(host, 'questions', {}).values() if not x.fut.done()))
    el = host.elevate
    add('secret', sum(1 for c in el.cards.values() if not c['fut'].done()))
    others = len(getattr(el.out, 'cards', {})) + len(getattr(el.login, 'cards', {}))
    others += sum(1 for c in getattr(el.contacts, 'cards', {}).values() if not c['fut'].done())
    others += sum(1 for c in getattr(host.capabilities, 'cards', {}).values() if not c.get('done'))
    add('card', others)
    peers = getattr(host, 'peers', None)
    if peers is not None:
        add('friend_card', len(getattr(peers, 'asks', {}) or {}))
        ps = getattr(peers, 'sessions', None)
        add('friend', (getattr(ps, 'active', 0) or 0) + len(getattr(peers, 'jobs', {}) or {}) if ps is not None else 0)
    sch = host.scheduler
    add('task', 1 if sch.current_id else 0)
    add('task_queue', len(sch.waiting))
    add('bots', len(getattr(getattr(host, 'bots', None), 'phone_tasks', None) or {}))
    board = getattr(host, 'busy', None)
    try:
        add('bg', board.restart_split()[0] if board is not None else 0)   # P423 #160: independent workers keep running
    except Exception:  # noqa: BLE001 — a display list must never block an upgrade decision
        pass
    add('task_soon', soon(host, now))
    return items


def soon(host, now=None):
    """Enabled scheduled tasks whose next run is within SOON seconds (0 when none or unreadable)."""
    from . import tasks
    import datetime as dt
    wd = host.scheduler.workdir()
    if not wd:
        return 0
    now = time.time() if now is None else now
    try:
        rows = tasks.rows(host.st, wd, dt.datetime.fromtimestamp(now, dt.timezone.utc))
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for r in rows if r.get('enabled') and isinstance(r.get('next'), int) and r['next'] - now <= SOON)


def _primary_present():
    from . import instance
    return instance.exists(1)


def due(st, *, now=None, blocked=False, stopped=False, rand=secrets.randbelow, newer=False):
    with st.config_lock():
        return _due(st, now=now, blocked=blocked, stopped=stopped, rand=rand, newer=newer)


def _due(st, *, now=None, blocked=False, stopped=False, rand=secrets.randbelow, newer=False):
    """One night opportunity each local date. Missed/busy/stopped never retries that night.
    P203 「空闲时自动更新」: besides it, a newer version the daily check already saw (`newer`, no extra network) is installed
    the first time the host is idle — no turn, card or task, no phone input for IDLE s — at most once a day."""
    now = time.time() if now is None else now
    if not enabled(st):
        return False
    from .instance import current
    if current() > 1 and _primary_present():
        return False    # P180: one shared program — instance 1 upgrades it and restarts every instance (service_recovery)
    local = time.localtime(now)
    day = time.strftime('%Y-%m-%d', local)
    rec = read(st)
    if rec.get('day') != day:
        rec.update(day=day, second=3*3600 + rand(2*3600), attempted=False)
        # A serve restart is not proof of thirty idle minutes.
        rec.setdefault('phone_at', now)
        write(st, rec)
    second = local.tm_hour*3600 + local.tm_min*60 + local.tm_sec
    if rec.get('attempted') or second < rec['second']:
        if (newer and rec.get('idle_day') != day and not stopped and not blocked
                and now - rec.get('phone_at', now) >= IDLE):
            rec.update(idle_day=day, decision='idle')
            write(st, rec)
            return True
        return False
    rec['attempted'] = True
    reason = ('window_missed' if second >= 5*3600 else 'stopped' if stopped else
              'busy' if blocked else 'phone_recent' if now - rec.get('phone_at', now) < IDLE else 'check')
    rec['decision'] = reason
    write(st, rec)
    if reason != 'check':
        activity.record(st, 'auto_update', result='skipped', reason=reason, text='夜间自动升级顺延 / Nightly update deferred: '+reason)
    return reason == 'check'


def download(url, limit):
    req = urllib.request.Request(url, headers={'User-Agent': 'agentj-upgrade/1'})
    with update.open_download(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError('artifact_too_large')
    return data


def cached_wheel(st, version, *, fetch=download, require_signature=True):
    """Published /dl wheel, SHA256, optional minisign bound to host/version/hash.
    Auto installs always require signature. Rollback accepts the previously cached
    verified bytes even if their release has since left the site's latest pointer.
    """
    if update.parse(version) is None:
        raise ValueError('bad_version')
    filename = f'agentj-{version}-py3-none-any.whl'
    path = st.root / 'upgrade-cache' / filename
    receipt = path.with_suffix('.verified.json')
    try:
        rec = json.loads(receipt.read_text())
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        signature_ok = not require_signature or minisign.verify(data, rec.get('signature', ''), minisign.keyring('release')) == f'agentj-host/v1 version={version} sha256={digest}'
        if rec['sha256'] == digest and signature_ok:
            return path
    except (OSError, ValueError, KeyError):
        pass
    if not require_signature:
        # Site retains latest only. Repack the exact installed, RECORD-verified
        # distribution if its original published wheel has already disappeared.
        return installed_wheel(st, version)
    data = fetch(ORIGIN + filename, MAX_WHEEL)
    digest = hashlib.sha256(data).hexdigest()
    stated = fetch(ORIGIN + filename + '.sha256', 256).decode().split()[0]
    if stated != digest:
        raise ValueError('artifact_hash_mismatch')
    signed = False
    if require_signature:
        signature = fetch(ORIGIN + filename + '.minisig', minisign.MAX_SIG).decode()
        comment = minisign.verify(data, signature, minisign.keyring('release'))
        if comment != f'agentj-host/v1 version={version} sha256={digest}':
            raise ValueError('artifact_signature_mismatch')
        signed = True
    # Wheel identity is checked before replacement, not inferred from a filename.
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        meta = z.read(f'agentj-{version}.dist-info/METADATA').decode()
        if f'\nName: agentj\n' not in '\n'+meta or f'\nVersion: {version}\n' not in '\n'+meta:
            raise ValueError('artifact_identity_mismatch')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    st.write_private(path, data)
    st.write_private(receipt, json.dumps({'sha256': digest, 'signed': signed, 'signature': signature if signed else None}).encode())
    return path


def installed_wheel(st, version):
    import base64, importlib.metadata, io, zipfile
    dist = importlib.metadata.distribution('agentj')
    if dist.version != version:
        raise ValueError('rollback_version_mismatch')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in dist.files or []:
            name = str(f)
            if not name.startswith(('agentj/', 'jarvis_host/', f'agentj-{version}.dist-info/')):
                continue
            if '__pycache__' in name or name.endswith(('.pyc', '/RECORD')):
                continue
            data = Path(dist.locate_file(f)).read_bytes()
            if not f.hash or f.hash.mode != 'sha256' or base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=') != f.hash.value:
                raise ValueError('installed_record_mismatch')
            z.writestr(name, data)
        if 'agentj/__init__.py' not in z.namelist():
            raise ValueError('installed_record_missing')
        z.writestr(f'agentj-{version}.dist-info/RECORD', '')
    data = buf.getvalue()
    p = st.root / 'upgrade-cache' / f'agentj-{version}-py3-none-any.whl'
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    st.write_private(p, data)
    st.write_private(p.with_suffix('.verified.json'), json.dumps({'sha256': hashlib.sha256(data).hexdigest(),
                      'signed': False, 'installed_record': True}).encode())
    return p


class Command(list):
    """P423 (#193): an installer argv plus the variables it runs with (`env`: names → paths, never a secret)."""
    def __init__(self, argv, env=None):
        super().__init__(argv)
        self.env = dict(env or {})


def install_env(info):
    """P423 (#193): uv installs into its *current* tool directory (UV_TOOL_DIR, else $XDG_DATA_HOME/uv/tools, else
    ~/.local/share/uv/tools). The service / fence environment can differ from the shell agentj was first installed from:
    uv then exits 0 having installed a second copy elsewhere and the running venv stays old (not_upgraded at verify).
    The install is pinned to the tools directory of the venv this process runs from (`<tools>/<name>`)."""
    if info.get('kind') != 'uv' or not info.get('where'):
        return {}
    return {'UV_TOOL_DIR': os.path.dirname(os.path.normpath(os.path.abspath(info['where'])))}


def run_env(cmd):
    """The environment a Command runs with (None: inherit this process's unchanged)."""
    extra = getattr(cmd, 'env', None)
    return {**os.environ, **extra} if extra else None


def command_line(cmd):
    """A Command as one shell line (`NAME=value … argv`), for the progress lines and the owner-local recover command."""
    import shlex
    extra = getattr(cmd, 'env', None) or {}
    return ' '.join([*(f'{k}={shlex.quote(str(v))}' for k, v in extra.items()), shlex.join(cmd)])


def install_commands(info, wheel):
    if info.get('legacy') or info['kind'] == 'checkout':
        raise ValueError('unsupported_install')
    if info['kind'] == 'uv':
        return [Command([update.installer_binary('uv'), 'tool', 'install', '--force', str(wheel)], install_env(info))]
    if info['kind'] == 'pipx':
        return [[update.installer_binary('pipx'), 'install', '--force', str(wheel)]]
    return [[str(Path(info['where'])/'bin/python'), '-m', 'pip', 'install', '--no-deps', '--force-reinstall', str(wheel)]]


def recovery_commands(info, wheel):
    """Use the cached old wheel with uv's independent base interpreter, even if --force removed the tool env."""
    cmds = install_commands(info, wheel)
    if info['kind'] == 'uv':
        base = os.path.realpath(getattr(sys, '_base_executable', sys.executable))
        cmds[0] += ['--python', base]
    return cmds


def install_template(info):
    # Command metadata is an allowlisted template, never exception.cmd or owner-local argv.
    return {'uv': 'uv tool install --force <wheel>', 'pipx': 'pipx install --force <wheel>'}.get(
        info['kind'], 'python -m pip install --no-deps --force-reinstall <wheel>')


def launch(st):
    from . import phone_update, service_recovery
    if not phone_update.reserve(st):
        return False
    try:
        # Snapshot the updater package: rolling back to a pre-P83 wheel must not
        # remove the worker entry point needed for retry after a SIGKILL.
        snapshot = st.root / 'upgrade-cache' / 'worker-lib'
        for p in Path(__file__).parent.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                dst = snapshot / 'agentj' / p.relative_to(Path(__file__).parent)
                dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                st.write_private(dst, p.read_bytes())
        worker = snapshot / 'run.py'
        st.write_private(worker, b"from agentj.auto_update import run\nfrom agentj.state import State\nrun(State())\n")
        service_recovery.launch_job(st, [sys.executable, str(worker)], 'autoupdate', restart=True)
    except Exception:
        phone_update.release(st)
        raise
    return True


def finish(st, result, diag=None):
    with st.config_lock():
        _finish(st, result)
    _diagnose(st, result, diag or {})


def _diagnose(st, result, diag):
    """P277: the attempt is the host's last upgrade (heartbeat → Dashboard); a failure reports itself to support (metadata +
    the redacted installer tail; `updates.report_failures`). Never raises: the phone notice above is already recorded."""
    try:
        from . import upgrade_diag
        res = {**result, **upgrade_diag.installer_fields(diag), 'reason': upgrade_diag.category(result.get('reason')), 'step': diag.get('step') or 'install',
               **({'stderr_tail': diag['stderr_tail']} if diag.get('stderr_tail') else {}),
               **({'failed_checks': update.check_ids(diag['failed_checks'])} if diag.get('failed_checks') else {}),
               **({'detail': diag['detail']} if diag.get('detail') else {}),
               **{k: diag[k] for k in ('rollback_failure', 'rollback_tail', 'rollback_check') if diag.get(k)}}
        if result.get('result') == 'rolled_back':
            res.update(result='failed', rollback='ok')
        elif result.get('result') == 'failed' and result.get('reason') == 'rollback_failed':
            res.update(rollback='failed', reason=upgrade_diag.category(diag.get('failure')))
        upgrade_diag.remember(st, res)
        if res['result'] != 'ok':
            lang = 'zh'
            try:
                lang = 'en' if preferences.get(preferences.effective(st), 'appearance.language', 'zh') == 'en' else 'zh'
            except Exception:  # noqa: BLE001
                pass
            upgrade_diag.after_failure(st, res, lang=lang)
    except Exception:  # noqa: BLE001
        pass


def _finish(st, result):
    rec = read(st)
    if rec.get('result') == result:
        return  # Manager retried after result persistence but before marking the job done.
    rec.update(result=result, result_at=int(time.time()), notice_pending=True)
    write(st, rec)
    activity.record(st, 'auto_update', result=result['result'], to=result.get('to'),
                    reason=result.get('reason'), text=notice(result))
    st.log('auto_update_done', status=result['result'])


def notice(r, lang='zh', prefs=None):
    from . import humane, upgrade_diag
    if r.get('result') == 'ok' and r.get('when') == 'idle':
        return (f"Updated automatically while idle to {r['to']} ({r.get('summary_en') or 'latest published fixes'})." if lang == 'en' else
                f"Agent J 主机空闲时已自动升级到 {r['to']}（{r.get('summary') or '安装最新正式发布的改进'}）。")
    if r.get('result') == 'ok':
        summary = (r.get('summary_en') if lang == 'en' else r.get('summary')) or humane.say('update.auto_ok_default', lang)
        return humane.say('update.auto_ok', lang, prefs, to=r['to'], summary=str(summary).rstrip('。.'))
    if r.get('result') == 'rolled_back':
        return humane.say('update.auto_rollback', lang, prefs, **{'from': r['from'], 'reason': r['reason']})
    text = (f"Automatic update did not complete ({r['reason']}); run agentj doctor." if lang == 'en' else
            f"自动升级未完成，原因：{r['reason']}；请运行 agentj doctor。")
    if upgrade_diag.install_unchanged(r):
        frm = r.get('from') or __version__
        return (f"Automatic update did not complete ({r['reason']}); still on {frm}, working as before." if lang == 'en' else
                f"自动升级未完成，原因：{r['reason']}；Agent J 主机仍是 {frm}，照常可用。")
    if r.get('reason') == 'rollback_failed' and r.get('recover'):
        text += (" Rollback also failed; restore the previous version: " if lang == 'en' else
                 " 回滚也没成功；请运行这一条装回原版本：") + r['recover']
    return text


def take_notice(st, lang='zh', prefs=None):
    with st.config_lock():
        return _take_notice(st, lang, prefs)


def _take_notice(st, lang='zh', prefs=None):
    rec = read(st)
    if not rec.get('notice_pending') or not rec.get('result'):
        return None
    result = rec['result']
    expected = result.get('to') if result['result'] == 'ok' else result.get('from')
    if expected and expected != __version__:
        return None
    rec['notice_pending'] = False
    write(st, rec)
    return notice(result, lang, prefs)


def quiet(argv, **kw):
    env = run_env(argv)
    if env is not None and 'env' not in kw:     # P423 (#193): the pinned uv tool directory
        kw['env'] = env
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900, **kw)


def fixed_reasons():
    """The ValueError texts auto_update raises itself — the only exception text that may become a reason."""
    from . import upgrade_diag
    return {'install_failed', 'version_mismatch', 'restart_failed', 'upgrade_self_check_failed', 'worker_interrupted',
            'artifact_hash_mismatch', 'artifact_signature_mismatch', 'artifact_identity_mismatch', 'unsupported_install',
            'upgrade_cache_unreadable', 'revocation_downgrade_blocked', *upgrade_diag.CATEGORY.values(), *upgrade_diag.REASON_WORDS}


def failure_reason(e, d):
    """#166/#192: one fixed category for a failure (the rollback's): the installer's own when it ran, else the code / type."""
    from . import upgrade_diag
    if isinstance(e, ValueError) and str(e) == 'install_failed':
        return d.get('failure') or 'uv_failed'
    return upgrade_diag.category(str(e) if isinstance(e, ValueError) and str(e) in fixed_reasons() else e)


def installed_is(argv, version, runner):
    """#192: after a failed rollback, is `version` still what this installation's agentj reports? Never raises."""
    if not argv or not version:
        return False
    try:
        v = runner(list(argv) + ['--version'])
        return not v.returncode and version in (v.stdout or '').split()
    except Exception:  # noqa: BLE001 — a missing / broken tool environment is a "no"
        return False


def run(st, *, check_fn=update.check, cache=cached_wheel, runner=quiet, restart=None):
    """Journal before mutations. A killed installing worker restores the cached old wheel.
    A manager retries abnormal termination; a file lock excludes competing workers.
    """
    # Load result/notice/redaction code before uv can remove this tool environment.
    from . import phone_update, service_recovery, upgrade_diag, humane, privacy  # noqa: F401
    fd = os.open(st.root/'auto-update-worker.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    job = read(st, JOB)
    diag = job.get('diagnostic') or {}  # Preserve the first failure across a manager retry during rollback.
    rb = {}                             # #166/#192: the rollback's own failure evidence (category, exit, redacted tail)
    def save(phase):
        job['phase'] = phase
        write(st, job, JOB)
    def install(wheel, *, diagnose=True):
        # A rollback (diagnose=False) records into its own `rb` dict: the first failure's evidence in `diag` stays intact.
        d = diag if diagnose else rb
        from .credential_revoke import require_compatible_version
        require_compatible_version(st, job['to'] if diagnose else job['from'])
        p = Path(wheel)
        try:
            rec = json.loads(p.with_suffix('.verified.json').read_text())
            data = p.read_bytes()
            want = rec['sha256']
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('upgrade_cache_unreadable') from None   # #166: a fixed code, not a bare exception type
        digest = hashlib.sha256(data).hexdigest()
        if digest != want:
            raise ValueError('artifact_hash_mismatch')
        if str(p) == job.get('new') and minisign.verify(data, rec.get('signature', ''), minisign.keyring('release')) != f"agentj-host/v1 version={job['to']} sha256={digest}":
            raise ValueError('artifact_signature_mismatch')
        cmds = (recovery_commands(job['install'], wheel) if not diagnose else install_commands(job['install'], wheel))
        if diagnose:
            save('installing')  # Validation/refusal above has not changed installed files.
        for argv in cmds:
            from .upgrade_diag import tail
            if diagnose:
                diag['command'] = install_template(job['install'])
            try:
                r = runner(argv)
            except (OSError, subprocess.SubprocessError) as e:
                # Never stringify the exception: it can contain argv, URLs or owner paths.
                d['exception'] = type(e).__name__
                if type(getattr(e, 'returncode', None)) is int:
                    d['exit'] = e.returncode
                d['stderr_tail'] = (tail(getattr(e, 'stderr', '') or '')
                                    or tail(getattr(e, 'stdout', '') or '')
                                    or type(e).__name__ + ': installer could not complete')
                d['failure'] = upgrade_diag.installer_reason(e, d.get('stderr_tail', ''))
                raise ValueError('install_failed') from None
            if r.returncode:
                d['exit'] = r.returncode
                d['stderr_tail'] = tail(getattr(r, 'stderr', '') or '') or tail(getattr(r, 'stdout', '') or '')
                d['failure'] = upgrade_diag.installer_reason(stderr=d['stderr_tail'])
                raise ValueError('install_failed')
    def verify(version, *, diagnose=True):
        argv = job['argv']
        v = runner(argv + ['--version'])
        if v.returncode or version not in (v.stdout or '').split():
            if diagnose:            # P423 (#193): what --version said (redacted tail) and why it is not the target
                diag.update(update.version_diag(v, version, job.get('from'), job['install']))
            raise ValueError('version_mismatch')
        if restart:
            restart(version)
        else:
            r = runner(argv + ['service', 'install', '--deferred', version])
            if r.returncode:
                from .upgrade_diag import tail
                (diag if diagnose else rb)['stderr_tail'] = tail(getattr(r, 'stderr', '') or '')
                raise ValueError('restart_failed')
            end = time.monotonic() + 90 + service_recovery.READY_LIMIT   # P142: the worker may wait for an idle serve
            while True:
                rec = service_recovery.read(st) or {}
                if rec.get('target') == version and rec.get('status') == 'ok':
                    break
                if rec.get('status') in ('failed', 'rolled_back') or time.monotonic() >= end:
                    raise ValueError('restart_failed')
                time.sleep(.25)
        for attempt in range(SELF_CHECK_TRIES):
            h = runner(argv + ['doctor', '--upgrade-only', '--post-upgrade', '--json'])
            try:
                rows = json.loads(h.stdout)['checks']
            except (ValueError, TypeError, KeyError, AttributeError):
                rows = None         # P393: reported as its own id, never a bare exception type
            blockers = update.upgrade_blockers(rows)[0]
            # A just-restarted serve / service manager can take a moment to answer: look again before rolling back.
            if not blockers or not set(blockers) <= TRANSIENT_CHECKS or attempt == SELF_CHECK_TRIES - 1:
                break
            time.sleep(SELF_CHECK_WAIT)
        if blockers:
            ids = update.check_ids(blockers)
            st.log('upgrade_health_failed', reason='doctor_failed', status='upgrade' if diagnose else 'rollback',
                   why=','.join(ids))
            # launchd redirects this manager worker's stdout to service-recovery.log. Fixed IDs only.
            print('upgrade_health_failed ' + ('upgrade' if diagnose else 'rollback') + ': ' + ','.join(ids), flush=True)
            if diagnose:
                diag['failed_checks'] = update.check_ids(blockers)
                if update.DOCTOR_UNPARSABLE in blockers:
                    rc = getattr(h, 'returncode', None)
                    diag['detail'] = f"doctor exit {rc}" if isinstance(rc, int) else 'doctor output unreadable'
            raise ValueError('upgrade_self_check_failed')
    try:
        if job.get('phase') == 'done':
            return read(st).get('result')
        if not job:
            if not enabled(st) or controls.estop_state(st).get('on'):
                return None
            latest = check_fn()
            if latest.get('status') != 'newer':
                return None
            info = update.install_kind()
            # No checkout git pulls or other owner code changes during unattended updates.
            job = {'attempt': secrets.token_hex(16), 'from': __version__, 'to': latest['latest'], 'install': info,
                   'argv': update.new_argv(info), 'when': 'idle' if read(st).get('decision') == 'idle' else 'night'}
            install_commands(info, '/unused.whl')
            # Rollback old releases predate artifact signatures; retain hash-verified wheel.
            old = cache(st, job['from'], require_signature=False)
            job['old'] = str(old)
            new = cache(st, job['to'], require_signature=True)
            import zipfile
            with zipfile.ZipFile(new) as z:
                try:
                    summary = json.loads(z.read('agentj/release-summary.json'))
                    if summary.get('version') == job['to']:
                        for lang, key in (('zh', 'summary'), ('en', 'summary_en')):
                            line = summary.get(lang)
                            if isinstance(line, str) and 0 < len(line) <= 160 and not any(ord(c) < 32 for c in line):
                                job[key] = line
                except (KeyError, ValueError):
                    pass
            job.update(old=str(old), new=str(new))
            save('prepared')
        if job['phase'] == 'prepared':
            # Recheck opt-out and stop immediately before modifying installed files.
            if not enabled(st) or controls.estop_state(st).get('on'):
                save('done')
                return None
            if restart is None:
                from .names import ctl_call
                ready = ctl_call(st, {'cmd': 'auto_update_ready'}, 10)
                if not ready or not ready.get('ready'):
                    save('done')
                    return None
            install(job['new'])
            save('installed')
        elif job['phase'] == 'installing':
            raise ValueError('worker_interrupted')
        if job['phase'] == 'installed':
            verify(job['to'])
            result = {**{k:job[k] for k in ('attempt','from','to','summary','summary_en','when') if k in job}, 'result': 'ok', 'reason': 'upgraded'}
            finish(st, result, {'step': 'health'})
            save('done')
            return result
        if job['phase'] == 'rollback':
            raise ValueError(upgrade_diag.category(job.get('failure', 'worker_interrupted')))
    except Exception as e:
        # Exception details may contain URLs or owner content: fixed codes only.
        reason = str(e) if isinstance(e, ValueError) and str(e) in fixed_reasons() else upgrade_diag.category(e)
        result = {'attempt': job.get('attempt'), 'from': job.get('from', __version__), 'to': job.get('to'), 'result': 'failed', 'reason': reason,
                  'phase': job.get('phase')}
        if reason == 'install_failed':
            reason = diag.get('failure') or 'uv_failed'
        elif not isinstance(e, ValueError):
            reason = upgrade_diag.category(e)
        if reason in ('unknown', 'ValueError'):
            reason = 'uv_failed' if job.get('phase') in ('installing', 'rollback') else (
                'self_check_failed' if job.get('phase') == 'installed' else upgrade_diag.plan_reason(e))
        result['reason'] = reason
        diag['failure'] = reason
        diag.setdefault('step', 'service' if reason in ('restart_failed', 'service_restart_failed') else {
            None: 'download', 'prepared': 'download', 'installing': 'install', 'installed': 'verify'}.get(job.get('phase'), 'install'))
        if job.get('phase') in ('installing','installed','rollback'):
            # Owner-local fallback only after installation was attempted; never imply a rollback before it.
            if job.get('old') and not any((st.root/n).exists() for n in ('credential_revocations.json','security_rotation.json')):
                result['recover'] = ' && '.join(command_line(c) for c in recovery_commands(job['install'], job['old']))
            job['failure'] = reason
            job['diagnostic'] = dict(diag)
            save('rollback')
            try:
                install(job['old'], diagnose=False)
                verify(job['from'], diagnose=False)
                result['result'] = 'rolled_back'
                if service_recovery.service.status().get('active') == 'active':
                    service_recovery.record(st, 'rolled_back', job['to'], 'rolled_back')
            except Exception as r_e:
                # #166/#192: say why the rollback failed (fixed category + its own redacted, bounded tail), then look at
                # what is really installed: an installer that could not even start (no uv on the service PATH) changed
                # nothing, so the previous version is still there — that is a rollback, not rollback_failed.
                diag['rollback_failure'] = failure_reason(r_e, rb)
                if rb.get('stderr_tail'):
                    diag['rollback_tail'] = rb['stderr_tail']
                if str(r_e) != 'revocation_downgrade_blocked' and installed_is(job.get('argv'), job.get('from'), runner):
                    result['result'] = 'rolled_back'
                    diag['rollback_check'] = 'version_unchanged'
                else:
                    result['reason'] = 'rollback_failed'
                    # Keep the independent recover command prepared above, not the missing venv's Python.
        finish(st, result, diag)
        if job.get('phase'):
            save('done')
        return result
    finally:
        # Auto result owns the notification, suppress manual-upgrade marker.
        (st.root/update.UPGRADED).unlink(missing_ok=True)
        phone_update.release(st)
        os.close(fd)
        service_recovery.cleanup_job(st)


if __name__ == '__main__':
    from .state import State
    run(State())
