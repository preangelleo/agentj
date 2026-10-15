"""One-shot service-manager jobs survive replacement of the main service tree.

Records/logs contain only version, lifecycle state and fixed error codes. No
credential is copied to job arguments, plists or logs.
"""
from __future__ import annotations
import json
import os
import plistlib
import re
import subprocess
import time
import uuid
from . import service
from pathlib import Path

RECORD = 'service-recovery.json'
NOTICE = 'service-recovery-notice.json'


def receipt_path():
    # F14 callers cannot read/write host keys. This metadata-only handoff lives
    # beside the service definition, so it survives the caller's sandbox.
    if service.platform() == 'macos':
        return Path(service.plist_path(service.name())).with_suffix('.recovery.json')
    return Path(service.unit_dir()) / (service.name() + '.recovery.json')


def read(st):
    records = []
    for p in (st.root / RECORD, receipt_path()):
        try:
            rec = json.loads(p.read_text())
            if isinstance(rec, dict) and isinstance(rec.get('at'), (float, int)):
                records.append(rec)
        except (OSError, ValueError):
            pass
    return max(records, key=lambda r:r['at']) if records else None


def record(st, status, target, reason=None, *, external=False):
    rec = {'status': status, 'target': target, 'at': time.time()}
    if reason:
        rec['reason'] = reason
    data = json.dumps(rec).encode()
    if external:
        p = receipt_path(); p.parent.mkdir(parents=True, exist_ok=True)
        service._write(str(p), data, 0o600)
    try:
        st.write_private(st.root / RECORD, data)
        if status == 'failed':
            st.write_private(st.root / NOTICE, data)
    except PermissionError:
        if not external:
            raise
    if not external:
        receipt_path().unlink(missing_ok=True)
    return rec


def launch_job(st, argv, purpose='recovery', *, restart=False, extra_env=None):
    """Ask a manager to spawn; never fork the worker in the host's cgroup/job."""
    label = 'net.agentj.' + purpose + '.' + uuid.uuid4().hex
    env, _ = service.service_env()
    env['AGENTJ_STATE_DIR'] = str(st.root)
    env.update(extra_env or {})
    env['HOME'] = os.path.expanduser('~')
    env['AGENTJ_JOB_LABEL'] = label
    for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR'):
        if os.environ.get(key):
            env[key] = os.environ[key]
    if os.environ.get('AGENTJ_SERVICE_NAME'):
        env['AGENTJ_SERVICE_NAME'] = service.name()
    if service.platform() == 'linux':
        cmd = ['systemd-run', '--user', '--collect', '--quiet', '--unit=' + label]
        if restart:
            cmd += ['--property=Restart=on-failure', '--property=RestartSec=5']
        # P191 B26: names only on the command line (`--setenv=NAME` copies the value from systemd-run's own environment,
        # systemd-run(1)); values never appear in /proc/<pid>/cmdline or ps. They are non-secret anyway (paths, label,
        # credential-free proxies: service.service_env), and the busctl fallback below must still pass them as arguments.
        cmd += ['--setenv=' + k for k in env]
        try:
            r = subprocess.run(cmd + argv, capture_output=True, timeout=15, env={**os.environ, **env})
        except FileNotFoundError:     # P278: no systemd-run (a container, WSL without systemd): the bus, then a plain reason
            r = None
        if r is None or r.returncode:
            # F14's PID namespace cannot authenticate on systemd's private socket;
            # the user's D-Bus broker can authenticate the same uid correctly.
            # `--` ends busctl's own options: the job's argv (`--recovery-worker`, `--worker --job …`) follows as plain
            # arguments; without it busctl took them for its own flags and refused (P155, found in a real fence on QA-J)
            bus = ['busctl', '--user', '--', 'call', 'org.freedesktop.systemd1',
                   '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
                   'StartTransientUnit', 'ssa(sv)a(sa(sv))', label + '.service', 'replace', str(7 if restart else 5),
                   'Description', 's', 'Agent J service recovery',
                   'Type', 's', 'exec', 'CollectMode', 's', 'inactive-or-failed',
                   'Environment', 'as', str(len(env)), *[k + '=' + v for k, v in env.items()],
                   'ExecStart', 'a(sasb)', '1', argv[0], str(len(argv)), *argv, 'false', '0']
            if restart:
                bus[-1:-1] = ['Restart', 's', 'on-failure', 'RestartSec', 't', '5000000']
            try:
                r = subprocess.run(bus, capture_output=True, timeout=15)
            except FileNotFoundError:
                r = None
            if r is None or r.returncode:
                # P278: say why when there is no systemd user manager at all (never a traceback)
                why = service.systemd_problem()
                raise service.ServiceError(why if why in service.NO_MANAGER else 'recovery_launch_failed')
    elif service.platform() == 'macos':
        path = Path(service.plist_path(label))
        path.parent.mkdir(parents=True, exist_ok=True)
        env['AGENTJ_JOB_PLIST'] = str(path)
        job = {'Label': label, 'ProgramArguments': argv, 'EnvironmentVariables': env,
               'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False} if restart else False,
               'StandardOutPath': str(st.root / 'service-recovery.log'),
               'StandardErrorPath': str(st.root / 'service-recovery.log')}
        st.write_private(path, plistlib.dumps(job))
        r = service._launchctl('bootstrap', service._gui(), str(path))
        if r.returncode:
            path.unlink(missing_ok=True)
            raise service.ServiceError('recovery_launch_failed')
    else:
        raise service.ServiceError('unsupported_os')


def cleanup_job(st):
    # The one-shot job has no KeepAlive; unload only our UUID label after the
    # durable result is written. launchd may terminate us at this last operation.
    # P282: `switch` too — /switch-harness scan / switch jobs stayed loaded with RunAtLoad and re-ran at every login.
    path = os.environ.get('AGENTJ_JOB_PLIST')
    label = os.environ.get('AGENTJ_JOB_LABEL', '')
    if service.platform() == 'macos' and path and re.fullmatch(r'net\.agentj\.(?:recovery|update|autoupdate|switch)\.[a-f0-9]{32}', label):
        p = Path(service.plist_path(label))
        if str(p) == path:
            p.unlink(missing_ok=True)
            service._launchctl('bootout', service._gui() + '/' + label)


def schedule(st, argv, target):
    record(st, 'pending', target, external=True)
    try:
        launch_job(st, argv + ['service', 'install', '--recovery-worker', target])
    except Exception as e:
        reason = e.reason if isinstance(e, service.ServiceError) and e.reason in service.NO_MANAGER else 'launch_failed'
        record(st, 'failed', target, reason, external=True)
        raise


READY_LIMIT = 600     # s (P142 A12): longest the restart waits for an idle serve; then it restarts anyway
READY_POLL = 2.0


def wait_ready(st, *, limit=READY_LIMIT, poll=READY_POLL, ctl=None, sleep=time.sleep):
    """P142 (A12): before restarting, wait until the running serve has no sudo/secret card, no open approval or question,
    no turn in flight (owned or shared) and no task — `upgrade_ready` over the control socket; an older serve without it
    answers from `status`. Returns idle | not_running | timeout."""
    from .names import ServeBusy, ctl_call
    ctl = ctl or ctl_call
    end = time.monotonic() + limit
    while True:
        try:
            r = ctl(st, {'cmd': 'upgrade_ready'}, 10)
            if r is not None and r.get('ok') is not True:
                s = ctl(st, {'cmd': 'status'}, 10)
                r = None if s is None else {'ok': True, 'ready': s.get('ok') is True and s.get('agent_status') in ('idle', None)
                                                              and not s.get('asks') and not s.get('task_running')}
        except ServeBusy:
            r = {'ok': True, 'ready': False}
        if r is None:
            return 'not_running'
        if r.get('ready') is True:
            return 'idle'
        if time.monotonic() >= end:
            return 'timeout'
        sleep(poll)


def run(st, target, *, timeout=45, install=None, status=None, ready=None, runner=None):
    from . import __version__
    record(st, 'running', target)
    pending = None
    try:
        if __version__ != target:
            raise service.ServiceError('recovery_version_mismatch')
        from . import update, auto_update
        auto_job = auto_update.read(st, auto_update.JOB)
        auto_active = auto_job.get('phase') in ('installing', 'installed', 'rollback')
        pending = None if auto_active else update.read_pending(st, target)
        record(st, 'waiting', target)
        waited = (ready or wait_ready)(st)
        st.log('upgrade_wait', status=waited)
        record(st, 'running', target)
        # The marker is the last claim before the restart (P142). The old fenced apply may have written its marker only in a
        # private mount namespace: write the normal marker from this manager-owned job.
        if not (st.root / update.UPGRADED).exists() and not (st.root / 'phone-update-result.json').exists() and not auto_active:
            update.write_marker(st, pending['from'] if pending else target, target)
        try:
            (install or service.install)(st)
            end = time.monotonic() + timeout
            while (status or service.status)().get('active') != 'active':
                if time.monotonic() >= end:
                    raise service.ServiceError('recovery_inactive')
                time.sleep(.25)
        except Exception:
            # P142 (B15): the new service did not come up — auto_update's cached-wheel rollback, then the old service.
            if pending and pending.get('old') and isinstance(pending.get('install'), dict):
                ok = update.roll_back(st, pending['install'], pending['old'], pending['from'], runner or subprocess.run)
                st.log('upgrade_rollback', status='ok' if ok else 'failed', reason='recovery_inactive')
                if ok:
                    (install or service.install)(st)
                    raise service.ServiceError('rolled_back')
            raise
        (st.root / update.PENDING).unlink(missing_ok=True)
        record(st, 'ok', target)
        (st.root / NOTICE).unlink(missing_ok=True)
        restart_siblings(st, target)
        return 0
    except Exception as e:
        record(st, 'failed', target, e.reason if isinstance(e, service.ServiceError) else type(e).__name__)
        return 1
    finally:
        cleanup_job(st)


SIBLING_ENV = 'AGENTJ_RECOVERY_SIBLING'


def restart_siblings(st, target, *, launch=None, active=None):
    """P180: every instance runs the same installed program, so after one instance's service is back on `target` the other
    instances' running services restart on it too — each through its own recovery job (it waits for its own idle serve).
    A job started for a sibling does not fan out again. → the instance numbers a job was launched for."""
    from . import instance
    if os.environ.get(SIBLING_ENV):
        return []
    me, out = instance.current(), []
    for n in instance.numbers():
        if n == me:
            continue
        try:
            env = dict(os.environ)
            instance.apply(n, env)
            if not (active or _sibling_active)(n, env):
                continue
            from .state import State
            (launch or launch_job)(State(instance.state_root(n)),
                                   service.agentj_argv() + ['--instance', str(n), 'service', 'install', '--recovery-worker', target],
                                   extra_env={SIBLING_ENV: '1'})
            out.append(n)
        except Exception:  # noqa: BLE001 — one sibling's failure never fails this instance's upgrade
            st.log('sibling_restart', status='failed', id=str(n))
    if out:
        st.log('sibling_restart', status='launched', count=len(out))
    return out


def _sibling_active(n, env):
    old = dict(os.environ)
    try:
        os.environ.clear(); os.environ.update(env)
        s = service.status()
    finally:
        os.environ.clear(); os.environ.update(old)
    return s.get('installed') and s.get('active') == 'active'


def failure(st):
    rec = read(st)
    if rec and (rec.get('status') == 'failed' or
                rec.get('status') in ('pending', 'running') and time.time() - rec.get('at', 0) > 90):
        return rec
    return None


def failure_text(rec):
    if rec and rec.get('reason') in service.NO_MANAGER:   # P278: no systemd here — say so and how to fix it
        return service.MESSAGES[rec['reason']]
    if rec and rec.get('reason') == 'rolled_back':   # P142 (B15)
        return ('新版本的服务没能启动，已自动回滚到原来的版本，一切照旧。运行 `agentj doctor` 可查看详情。 / '
                'The new version\'s service did not start, so Agent J rolled back to the previous version; everything works as '
                'before. Run `agentj doctor` for details.')
    return ('升级后的服务启动未完成。运行 `agentj service start`，再运行 `agentj doctor`。 / '
            'The upgraded service did not finish starting. Run `agentj service start`, then `agentj doctor`.')
