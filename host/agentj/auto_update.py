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
import shutil
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


def due(st, *, now=None, blocked=False, stopped=False, rand=secrets.randbelow):
    with st.config_lock():
        return _due(st, now=now, blocked=blocked, stopped=stopped, rand=rand)


def _due(st, *, now=None, blocked=False, stopped=False, rand=secrets.randbelow):
    """One opportunity each local date. Missed/busy/stopped never retries that night."""
    now = time.time() if now is None else now
    if not enabled(st):
        return False
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
    with urllib.request.urlopen(req, timeout=30) as r:
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
        signature_ok = not require_signature or minisign.verify(data, rec.get('signature', '')) == f'agentj-host/v1 version={version} sha256={digest}'
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
        comment = minisign.verify(data, signature)
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


def install_commands(info, wheel):
    if info.get('legacy') or info['kind'] == 'checkout':
        raise ValueError('unsupported_install')
    if info['kind'] == 'uv':
        return [[shutil.which('uv') or 'uv', 'tool', 'install', '--force', str(wheel)]]
    if info['kind'] == 'pipx':
        return [[shutil.which('pipx') or 'pipx', 'install', '--force', str(wheel)]]
    return [[str(Path(info['where'])/'bin/python'), '-m', 'pip', 'install', '--no-deps', '--force-reinstall', str(wheel)]]


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


def finish(st, result):
    with st.config_lock():
        _finish(st, result)


def _finish(st, result):
    rec = read(st)
    if rec.get('result') == result:
        return  # Manager retried after result persistence but before marking the job done.
    rec.update(result=result, notice_pending=True)
    write(st, rec)
    activity.record(st, 'auto_update', result=result['result'], to=result.get('to'),
                    reason=result.get('reason'), text=notice(result))
    st.log('auto_update_done', status=result['result'])


def notice(r, lang='zh'):
    if r.get('result') == 'ok':
        return (f"Updated automatically overnight to {r['to']} ({r.get('summary_en') or 'latest published fixes'})." if lang == 'en' else
                f"夜里已自动升级到 {r['to']}（{r.get('summary') or '安装最新正式发布的改进'}）。")
    if r.get('result') == 'rolled_back':
        return (f"Automatic update failed; rolled back to {r['from']} ({r['reason']})." if lang == 'en' else
                f"自动升级失败，已回滚到 {r['from']}，原因：{r['reason']}。")
    return (f"Automatic update did not complete ({r['reason']}); run agentj doctor." if lang == 'en' else
            f"自动升级未完成，原因：{r['reason']}；请运行 agentj doctor。")


def take_notice(st, lang='zh'):
    with st.config_lock():
        return _take_notice(st, lang)


def _take_notice(st, lang='zh'):
    rec = read(st)
    if not rec.get('notice_pending') or not rec.get('result'):
        return None
    result = rec['result']
    expected = result.get('to') if result['result'] == 'ok' else result.get('from')
    if expected and expected != __version__:
        return None
    rec['notice_pending'] = False
    write(st, rec)
    return notice(result, lang)


def quiet(argv, **kw):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900, **kw)


def run(st, *, check_fn=update.check, cache=cached_wheel, runner=quiet, restart=None):
    """Journal before mutations. A killed installing worker restores the cached old wheel.
    A manager retries abnormal termination; a file lock excludes competing workers.
    """
    from . import phone_update, service_recovery
    fd = os.open(st.root/'auto-update-worker.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    job = read(st, JOB)
    def save(phase):
        job['phase'] = phase
        write(st, job, JOB)
    def install(wheel):
        p = Path(wheel)
        rec = json.loads(p.with_suffix('.verified.json').read_text())
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != rec['sha256']:
            raise ValueError('artifact_hash_mismatch')
        if str(p) == job.get('new') and minisign.verify(data, rec.get('signature', '')) != f"agentj-host/v1 version={job['to']} sha256={digest}":
            raise ValueError('artifact_signature_mismatch')
        for argv in install_commands(job['install'], wheel):
            if runner(argv).returncode:
                raise ValueError('install_failed')
    def verify(version):
        argv = job['argv']
        v = runner(argv + ['--version'])
        if v.returncode or version not in (v.stdout or '').split():
            raise ValueError('version_mismatch')
        if restart:
            restart(version)
        else:
            r = runner(argv + ['service', 'install', '--deferred', version])
            if r.returncode:
                raise ValueError('restart_failed')
            end = time.monotonic() + 90
            while True:
                rec = service_recovery.read(st) or {}
                if rec.get('target') == version and rec.get('status') == 'ok':
                    break
                if rec.get('status') == 'failed' or time.monotonic() >= end:
                    raise ValueError('restart_failed')
                time.sleep(.25)
        h = runner(argv + ['doctor', '--upgrade-only', '--json'])
        rows = json.loads(h.stdout)['checks']
        if update.upgrade_health(rows)[0]:
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
            install_commands(info, '/unused.whl')
            job = {'attempt': secrets.token_hex(16), 'from': __version__, 'to': latest['latest'], 'install': info,
                   'argv': update.new_argv(info)}
            # Rollback old releases predate artifact signatures; retain hash-verified wheel.
            old = cache(st, job['from'], require_signature=False)
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
            save('installing')
            install(job['new'])
            save('installed')
        elif job['phase'] == 'installing':
            raise ValueError('worker_interrupted')
        if job['phase'] == 'installed':
            verify(job['to'])
            result = {**{k:job[k] for k in ('attempt','from','to','summary','summary_en') if k in job}, 'result': 'ok', 'reason': 'upgraded'}
            finish(st, result)
            save('done')
            return result
        if job['phase'] == 'rollback':
            raise ValueError(job.get('failure', 'worker_interrupted'))
    except Exception as e:
        # Exception details may contain URLs or owner content: fixed codes only.
        reason = str(e) if isinstance(e, ValueError) and str(e) in {
            'install_failed','version_mismatch','restart_failed','upgrade_self_check_failed','worker_interrupted',
            'artifact_hash_mismatch','artifact_signature_mismatch','artifact_identity_mismatch','unsupported_install'} else type(e).__name__
        result = {'attempt': job.get('attempt'), 'from': job.get('from', __version__), 'to': job.get('to'), 'result': 'failed', 'reason': reason}
        if job.get('phase') in ('installing','installed','rollback'):
            job['failure'] = reason
            save('rollback')
            try:
                install(job['old'])
                verify(job['from'])
                result['result'] = 'rolled_back'
            except Exception:
                result['reason'] = 'rollback_failed'
        finish(st, result)
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
