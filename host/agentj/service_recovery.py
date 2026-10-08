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


def launch_job(st, argv, purpose='recovery', *, restart=False):
    """Ask a manager to spawn; never fork the worker in the host's cgroup/job."""
    label = 'net.agentj.' + purpose + '.' + uuid.uuid4().hex
    env, _ = service.service_env()
    env['AGENTJ_STATE_DIR'] = str(st.root)
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
        cmd += ['--setenv=' + k + '=' + v for k, v in env.items()]
        r = subprocess.run(cmd + argv, capture_output=True, timeout=15)
        if r.returncode:
            # F14's PID namespace cannot authenticate on systemd's private socket;
            # the user's D-Bus broker can authenticate the same uid correctly.
            bus = ['busctl', '--user', 'call', 'org.freedesktop.systemd1',
                   '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
                   'StartTransientUnit', 'ssa(sv)a(sa(sv))', label + '.service', 'replace', str(7 if restart else 5),
                   'Description', 's', 'Agent J service recovery',
                   'Type', 's', 'exec', 'CollectMode', 's', 'inactive-or-failed',
                   'Environment', 'as', str(len(env)), *[k + '=' + v for k, v in env.items()],
                   'ExecStart', 'a(sasb)', '1', argv[0], str(len(argv)), *argv, 'false', '0']
            if restart:
                bus[-1:-1] = ['Restart', 's', 'on-failure', 'RestartSec', 't', '5000000']
            r = subprocess.run(bus, capture_output=True, timeout=15)
            if r.returncode:
                raise service.ServiceError('recovery_launch_failed')
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
    path = os.environ.get('AGENTJ_JOB_PLIST')
    label = os.environ.get('AGENTJ_JOB_LABEL', '')
    if service.platform() == 'macos' and path and re.fullmatch(r'net\.agentj\.(?:recovery|update|autoupdate)\.[a-f0-9]{32}', label):
        p = Path(service.plist_path(label))
        if str(p) == path:
            p.unlink(missing_ok=True)
            service._launchctl('bootout', service._gui() + '/' + label)


def schedule(st, argv, target):
    record(st, 'pending', target, external=True)
    try:
        launch_job(st, argv + ['service', 'install', '--recovery-worker', target])
    except Exception:
        record(st, 'failed', target, 'launch_failed', external=True)
        raise


def run(st, target, *, timeout=45, install=None, status=None):
    from . import __version__
    record(st, 'running', target)
    try:
        if __version__ != target:
            raise service.ServiceError('recovery_version_mismatch')
        # The old fenced apply may have written its marker only in a private
        # mount namespace. Write the normal marker from this manager-owned job.
        from . import update, auto_update
        auto_job = auto_update.read(st, auto_update.JOB)
        auto_active = auto_job.get('phase') in ('installing', 'installed', 'rollback')
        if not (st.root / update.UPGRADED).exists() and not (st.root / 'phone-update-result.json').exists() and not auto_active:
            update.write_marker(st, target, target)
        (install or service.install)(st)
        end = time.monotonic() + timeout
        while (status or service.status)().get('active') != 'active':
            if time.monotonic() >= end:
                raise service.ServiceError('recovery_inactive')
            time.sleep(.25)
        record(st, 'ok', target)
        (st.root / NOTICE).unlink(missing_ok=True)
        return 0
    except Exception as e:
        record(st, 'failed', target, e.reason if isinstance(e, service.ServiceError) else type(e).__name__)
        return 1
    finally:
        cleanup_job(st)


def failure(st):
    rec = read(st)
    if rec and (rec.get('status') == 'failed' or
                rec.get('status') in ('pending', 'running') and time.time() - rec.get('at', 0) > 90):
        return rec
    return None


def failure_text(rec):
    return ('升级后的服务启动未完成。运行 `agentj service start`，再运行 `agentj doctor`。 / '
            'The upgraded service did not finish starting. Run `agentj service start`, then `agentj doctor`.')
