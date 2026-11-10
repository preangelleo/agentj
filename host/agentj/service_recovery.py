"""One-shot service-manager jobs survive replacement of the main service tree.

Records/logs contain only version, lifecycle state and fixed error codes. No
credential is copied to job arguments, plists or logs.
"""
from __future__ import annotations
import json
import os
import plistlib
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
    from . import olares_supervisor
    if olares_supervisor.enabled():
        if restart:
            raise service.ServiceError('olares_bad_job')
        return olares_supervisor.submit(st, argv, purpose, extra_env)
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
        path = Path(service.job_plist_path(label))    # P323: never in ~/Library/LaunchAgents (a Login Items row each)
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
    # P323: jobs live in service.job_dir(); a job an older version wrote into LaunchAgents is still its own to remove.
    if service.platform() == 'macos' and path and service.JOB_RX.fullmatch(label):
        for p in (Path(service.job_plist_path(label)), Path(service.plist_path(label))):
            if str(p) == path:
                p.unlink(missing_ok=True)
                service._launchctl('bootout', service._gui() + '/' + label)


def schedule(st, argv, target):
    try:
        record(st, 'pending', target, external=True)
    except OSError as e:     # #460: name the step in the receipt (a P591 fence pins the unit folder read-only: EROFS)
        e.step = 'write ' + receipt_path().name
        raise
    try:
        launch_job(st, argv + ['service', 'install', '--recovery-worker', target])
    except Exception as e:
        reason = e.reason if isinstance(e, service.ServiceError) and e.reason in service.NO_MANAGER else 'launch_failed'
        record(st, 'failed', target, reason, external=True)
        raise


HANDOFF = 'upgrade-handoff'     # #460: doctor's row when its upgrade repair scheduled the restart (update.resume_handoff)
HANDED = ('✓ 服务重启已由主机（隔离外）交给独立任务 / the host (outside the Agent fence) scheduled the service restart')


def via_host(st, target):
    """#460: inside the Agent fence (P591) the user's service manager is out of reach; the host outside the fence does this
    hand-off over elevate.sock. Upgrade op `service` (0.26.8a2+ host: it runs this same `--deferred` itself); an older host
    lacks it, so its `health` op runs this version's doctor outside the fence, whose upgrade repair schedules the restart from
    the host's own pending record. Fixed ops only, no argv crosses. → exit code."""
    import sys
    from . import update
    r = update._host(st, 'service', 180, to=target)
    if r is not None:
        if r.get('tail'):
            print(r['tail'], file=sys.stderr)
        rc = r['rc'] if isinstance(r.get('rc'), int) else 1
        if rc == 0:
            print(HANDED)
        return rc
    h = update._host(st, 'health', update.HEALTH_TIMEOUT + 30) if update._host(st, 'ping', 10) else None
    if any(isinstance(x, dict) and x.get('id') == HANDOFF and x.get('status') == 'ok' for x in (h or {}).get('checks') or []):
        print(HANDED)
        return 0
    print('✗ 在 Agent 的隔离环境里够不到 systemd，主机（隔离外）也没能安排服务重启（command=elevate.sock upgrade phase=service at=' + time.strftime('%Y-%m-%dT%H:%M:%S%z') + '）；'
          '请从已配对手机发 /update，由主机来升级 / inside the Agent fence systemd is out of reach and the host outside the fence '
          'could not schedule the restart either; send /update from the paired phone so the host upgrades', file=sys.stderr)
    return 1


READY_LIMIT = 600     # s (P142 A12): longest the restart waits for an idle serve; then it restarts anyway
READY_POLL = 2.0


def wait_ready(st, *, limit=READY_LIMIT, poll=READY_POLL, ctl=None, sleep=time.sleep, purpose=None):
    """P142 (A12): before restarting, wait until the running serve has no sudo/secret card, no open approval or question,
    no turn in flight (owned or shared) and no task — `upgrade_ready` over the control socket; an older serve without it
    answers from `status`. Returns idle | not_running | timeout. purpose="upgrade" (0.26.10 #484): serve does not wait on
    the turn that launched the upgrade nor on a stuck turn, and tells the phone it restarts when the task ends."""
    from .names import ServeBusy, ctl_call
    ctl = ctl or ctl_call
    end = time.monotonic() + limit
    while True:
        try:
            r = ctl(st, {'cmd': 'upgrade_ready', **({'purpose': purpose} if purpose else {})}, 10)
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


STABLE_S = 12.0   # ticket U33rz: launchd shows `running` for the half second a crash-looping serve lives (ThrottleInterval 5)


def stays_up(status, timeout: float, stable: float | None = None) -> None:
    """The new service is active AND stays active for `stable` s in a row (≤ a quarter of the timeout), else
    ServiceError('recovery_inactive') — so a crash loop after an upgrade rolls back instead of being recorded as ok."""
    from . import service
    stable = min(STABLE_S, timeout / 4) if stable is None else stable
    end = time.monotonic() + timeout
    since = None
    while True:
        now = time.monotonic()
        if status().get('active') == 'active':
            since = now if since is None else since
            if now - since >= stable:
                return
        else:
            since = None
        if now >= end + (stable if since is not None else 0):
            raise service.ServiceError('recovery_inactive')
        time.sleep(.25)


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
        waited = ready(st) if ready else wait_ready(st, purpose='upgrade')
        st.log('upgrade_wait', status=waited)
        record(st, 'running', target)
        # The marker is the last claim before the restart (P142). The old fenced apply may have written its marker only in a
        # private mount namespace: write the normal marker from this manager-owned job.
        if not (st.root / update.UPGRADED).exists() and not (st.root / 'phone-update-result.json').exists() and not auto_active:
            update.write_marker(st, pending['from'] if pending else target, target)
        try:
            (install or service.install)(st)
            stays_up(status or service.status, timeout)
        except Exception:
            # P142 (B15): the new service did not come up — auto_update's cached-wheel rollback, then the old service.
            if pending and pending.get('old') and isinstance(pending.get('install'), dict):
                ok = update.roll_back(st, pending['install'], pending['old'], pending['from'], runner or subprocess.run)
                st.log('upgrade_rollback', status='ok' if ok else 'failed', reason='recovery_inactive')
                if ok:
                    (install or service.install)(st)
                    stays_up(status or service.status, timeout)
                    record(st, 'rolled_back', target, 'rolled_back')
                    return 1
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
    if rec and (rec.get('status') in ('failed', 'rolled_back') or
                rec.get('status') in ('pending', 'running') and time.time() - rec.get('at', 0) > 90):
        return rec
    return None


def failure_text(rec, *, mobile=False):
    if mobile:
        if rec and rec.get('reason') == 'rolled_back':
            return ('新版本服务未启动，已自动回滚，原版本照常可用。可用「修复连接」检查状态。 / '
                    'The new service did not start; the previous version was restored. Use Repair connection to check its status.')
        return ('后台服务启动未完成。使用「修复连接」，或前往设置中的「紧急修复」。主机离线时，请先唤醒那台电脑。 / '
                'Background service did not finish starting. Use Repair connection or Settings → Emergency repair. If the host is offline, wake that computer first.')
    if rec and rec.get('reason') in service.NO_MANAGER:   # P278: no systemd here — say so and how to fix it
        return service.MESSAGES[rec['reason']]
    if rec and rec.get('reason') == 'rolled_back':   # P142 (B15)
        return ('新版本的服务没能启动，已自动回滚到原来的版本，一切照旧。运行 `agentj doctor` 可查看详情。 / '
                'The new version\'s service did not start, so Agent J rolled back to the previous version; everything works as '
                'before. Run `agentj doctor` for details.')
    return ('升级后的服务启动未完成。运行 `agentj service start`，再运行 `agentj doctor`。 / '
            'The upgraded service did not finish starting. Run `agentj service start`, then `agentj doctor`.')


def health(st, svc):
    """Read-only doctor view; legacy failed/rolled_back receipts remain recognizable."""
    rec = failure(st)
    if not rec:
        return None
    recovered = (rec.get('status') == 'rolled_back' or rec.get('reason') == 'rolled_back')
    if recovered and svc.get('active') == 'active':
        return ('warn', '已自动退回旧版、服务正常。 / Agent J automatically rolled back to the previous version; '
                'the service is running normally.')
    return ('fail', failure_text(rec) if not recovered else
            '已退回旧版，但服务尚未恢复正常。 / Rolled back, but the service is not running normally.')
