"""Image-owned Olares lifecycle manager. The fence hides its fixed /tmp socket.

Only the supervisor creates processes: fixed isolated image Python, semantic
switch fields, fresh allowlisted service environment, no caller argv or state.
The old phone signature/nonce/TTL checks remain upstream of cmdrun unchanged.
"""
from __future__ import annotations
import contextlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import uuid

IMAGE_PYTHON = '/opt/agentj/bin/python'
ENTRY = Path('/opt/agentj-olares/entry.sh')
ENV_PARSER = Path('/opt/agentj-olares/serve_env.py')
JOURNAL = 'olares-lifecycle.json'
SNAPSHOT = 'olares-switch-transaction.json'
MAX_PACKET = 8192
TTL = 900


def trusted(path):
    """Every image ancestor, link and resolved target is root-owned and read-only."""
    try:
        paths = set()
        for p in (Path(path), Path(path).resolve(strict=True)):
            paths.update((p, *p.parents))
        for p in paths:
            s = p.lstat()
            if s.st_uid != 0 or (not stat.S_ISLNK(s.st_mode) and s.st_mode & 0o022):
                return False
            if not os.statvfs(p).f_flag & os.ST_RDONLY:
                return False
        return True
    except OSError:
        return False


def socket_path():
    return Path('/tmp') / ('agentj-run-' + str(os.getuid())) / 'olares-supervisor.sock'


def enabled():
    # Neither OLARES_* nor a caller-provided path grants manager authority.
    return all(trusted(p) for p in (ENTRY, ENV_PARSER, IMAGE_PYTHON, __file__))


def call(req, timeout=50):
    from .service import ServiceError
    if not enabled():
        raise ServiceError('olares_untrusted')
    p = socket_path()
    try:
        s, d = p.lstat(), p.parent.lstat()
        if not stat.S_ISSOCK(s.st_mode) or s.st_uid != os.getuid() or not stat.S_ISDIR(d.st_mode) or \
                d.st_uid != os.getuid() or d.st_mode & 0o077:
            raise OSError('unsafe socket')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(timeout)
            sock.connect(str(p))
            _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                raise OSError('wrong manager')
            sock.sendall(json.dumps(req).encode() + b'\n')
            data = b''
            while b'\n' not in data and len(data) <= MAX_PACKET:
                part = sock.recv(2048)
                if not part:
                    break
                data += part
            r = json.loads(data)
            if not isinstance(r, dict) or r.get('ok') is not True or not re.fullmatch('[0-9a-f]{32}', r.get('epoch', '')):
                raise ServiceError(r.get('error', 'olares_protocol') if isinstance(r, dict) else 'olares_protocol')
            return r
    except (OSError, ValueError, TypeError) as e:
        raise ServiceError('olares_unavailable') from e


def submit(st, argv, purpose, extra_env):
    """Translate only the existing canonical switch argv, then send semantic data."""
    from . import service, switch_harness as sw
    from .state import State
    if st.root.resolve() != State().root.resolve() or purpose != 'switch' or set(extra_env or {}) != {'AGENTJ_JOB_AT'}:
        raise service.ServiceError('olares_bad_job')
    prefix = service.agentj_argv() + ['agent', 'switch']
    if argv[:len(prefix)] != prefix:
        raise service.ServiceError('olares_bad_job')
    tail = argv[len(prefix):]
    try:
        job = tail[tail.index('--job') + 1]
        model = tail[tail.index('--model') + 1] if '--model' in tail else None
        spec = {'kind': 'scan'} if '--scan' in tail else {
            'kind': 'switch', 'target': None if '--undo' in tail else tail[0], 'model': model,
            'dry_run': '--dry-run' in tail, 'force': '--force' in tail, 'undo': '--undo' in tail}
        checked, err = sw.check_spec(spec)
        at = float(extra_env['AGENTJ_JOB_AT'])
        if err or not sw._JOB.fullmatch(job) or argv != sw.job_argv({**checked, 'job': job}):
            raise ValueError()
    except (ValueError, IndexError, TypeError, KeyError):
        raise service.ServiceError('olares_bad_job') from None
    r = call({'cmd': 'job', 'spec': checked, 'job': job, 'at': at})
    if r.get('job') != job:
        raise service.ServiceError('olares_protocol')


class Supervisor:
    def __init__(self, st, home=None):
        self.st = st
        self.home = Path(home or os.path.expanduser('~'))
        self.ola = self.home / '.agentj-olares'
        self.envfile = self.home / '.config/systemd/user/agentj.env'
        self.lock = threading.RLock()
        self.epoch = uuid.uuid4().hex
        self.generation = 0
        self.serve = self.worker = None
        self.job = None
        self.restart_wanted = False
        self.next_start = 0
        self.stamp = None
        self.jobs = {}
        from .olares_probe import BASE_ENV, CONFIG_ENV, OWNER_SERVER_ENV, login_names
        from .harness import BIN_ENV, NAMES
        names = set(BASE_ENV) | set(BIN_ENV.values()) | set(OWNER_SERVER_ENV) | {'XDG_RUNTIME_DIR',
                 'AGENTJ_ACCOUNT_URL', 'AGENTJ_OLARES_TERMINAL_URL', 'OLARES_ROUTER_URL', 'OLARES_API_KEY'}
        for kind in NAMES:
            names |= CONFIG_ENV.get(kind, set()) | login_names(kind)
        self.base_env = {k: v for k, v in os.environ.items() if k in names or re.fullmatch(r'LC_[A-Z_]+', k)}
        self.base_env.update(HOME=str(self.home), PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' +
                             str(self.home / '.local/bin') + ':' + str(self.home / '.opencode/bin'))
        # A new pod never replays jobs from the old pod, even when its volume survives.
        try:
            old = json.loads((st.root / JOURNAL).read_text())
            self.jobs = {k: {**v, **({'status': 'interrupted'} if v.get('status') == 'running' else {})}
                         for k, v in old.get('jobs', {}).items() if re.fullmatch('[0-9a-f]{16}', k) and isinstance(v, dict)}
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        from . import switch_harness as sw
        rec = sw.read_receipt(st) or {}
        if rec.get('status') in sw.LIVE:
            self.interrupted('interrupted')
        else:
            (st.root / SNAPSHOT).unlink(missing_ok=True)
        self.persist()

    def persist(self):
        now = time.time()
        self.jobs = {k: v for k, v in self.jobs.items() if k == self.job and self.worker is not None or now - v.get('at', 0) <= TTL}
        self.st.write_private(self.st.root / JOURNAL, json.dumps({'epoch': self.epoch, 'jobs': self.jobs}).encode())

    def interrupted(self, reason):
        from . import switch_harness as sw
        rec = sw.read_receipt(self.st) or {}
        code = 'not_running'
        try:
            snap = json.loads((self.st.root / SNAPSHOT).read_text())
            if snap.get('job') == rec.get('job') and isinstance(snap.get('raw'), dict):
                sw._restore(self.st, snap['raw'])
                code = 'rollback_failed'  # configuration restored, old service/hello not yet verified
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            code = 'config_mismatch'
        if rec.get('status') in sw.LIVE:
            sw.write_receipt(self.st, {**rec, 'status': 'failed', 'reason': code})
        (self.st.root / SNAPSHOT).unlink(missing_ok=True)

    def bound(self):
        return (self.ola / 'bound').is_file()

    def env(self):
        # Identical literal parser for serve and worker; no shell source and no load-path variables.
        parse = runpy.run_path(str(ENV_PARSER))['parse']
        try:
            saved = parse(self.envfile.read_text())
        except FileNotFoundError:
            saved = {}
        return {**self.base_env, **saved}

    def spawn(self, args, *, job_at=None):
        env = self.env()
        if job_at is not None:
            env['AGENTJ_JOB_AT'] = str(job_at)
        # This is the trusted service manager, not a detached fallback in serve's tree.
        return subprocess.Popen([IMAGE_PYTHON, '-I', '-m', 'agentj.cli', *args], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=str(self.home), start_new_session=True)

    @staticmethod
    def stop(proc):
        if proc is None:
            return
        # Every child owns its session; only groups created by this manager are signalled.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        # The session leader may have exited before descendants; reap its own group too.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)

    def reply(self, **r):
        return {'epoch': self.epoch, **r}

    def handle(self, req):
        from . import switch_harness as sw
        if not isinstance(req, dict) or req.get('cmd') not in ('status', 'restart', 'job'):
            return self.reply(ok=False, error='bad_request')
        cmd = req['cmd']
        if cmd in ('status', 'restart') and set(req) != {'cmd'}:
            return self.reply(ok=False, error='bad_field')
        with self.lock:
            active = self.bound() and self.serve is not None and self.serve.poll() is None
            if cmd == 'status':
                return self.reply(ok=True, installed=True, active='active' if active else 'inactive', generation=self.generation)
            if not self.bound():
                return self.reply(ok=False, error='not_running')
            if cmd == 'job':
                if set(req) != {'cmd', 'spec', 'job', 'at'}:
                    return self.reply(ok=False, error='bad_field')
                spec, err = sw.check_spec(req['spec'])
                at, job = req['at'], req['job']
                if err or not isinstance(job, str) or not sw._JOB.fullmatch(job) or \
                        type(at) not in (float, int) or not math.isfinite(at) or abs(time.time() - at) > TTL:
                    return self.reply(ok=False, error=err or 'bad_job')
                self.persist()
                if job in self.jobs:
                    return self.reply(ok=False, error='replayed_job')
                if self.worker is not None or len(self.jobs) >= 64:
                    return self.reply(ok=False, error='busy')
                # Build from checked fields, never from request argv, executable, path or environment.
                args = sw.job_argv({**spec, 'job': job})[len(sw.service.agentj_argv()):]
                if spec['kind'] == 'switch':
                    self.st.write_private(self.st.root / SNAPSHOT, json.dumps(
                        {'job': job, 'raw': sw._raw_agent(self.st)}).encode())
                self.jobs[job] = {'at': time.time(), 'status': 'running', 'kind': spec['kind']}
                self.persist()
                try:
                    self.worker = self.spawn(args, job_at=at)
                except (OSError, ValueError):
                    self.jobs[job]['status'] = 'launch_failed'
                    self.persist()
                    return self.reply(ok=False, error='launch_failed')
                self.job = job
                return self.reply(ok=True, job=job, generation=self.generation)
            # A restart request is acknowledged only after the old process group is gone
            # and a distinct serve generation has survived at least one manager tick.
            old = self.generation
            self.restart_wanted = True
        end = time.monotonic() + 40
        while time.monotonic() < end:
            time.sleep(.1)
            with self.lock:
                if not self.bound():
                    return self.reply(ok=False, error='not_running')
                if self.generation > old and self.serve is not None and self.serve.poll() is None and not self.restart_wanted:
                    return self.reply(ok=True, generation=self.generation)
        return self.reply(ok=False, error='restart_failed')

    def tick(self):
        with self.lock:
            if not self.bound():
                self.stop(self.serve); self.serve = None
                if self.worker is not None:
                    self.stop(self.worker); self.worker = None
                    self.jobs[self.job]['status'] = 'unbound'
                    self.interrupted('unbound')
                    self.persist()
                return
            if self.worker is not None and time.time() - self.jobs[self.job]['at'] > TTL:
                self.stop(self.worker); self.worker = None
                self.jobs[self.job]['status'] = 'expired'
                self.interrupted('expired')
                self.restart_wanted = True
                self.persist()
            if self.worker is not None and self.worker.poll() is not None:
                code = self.worker.returncode
                self.stop(self.worker); self.worker = None
                self.jobs[self.job]['status'] = 'done' if code == 0 else 'failed'
                from . import switch_harness as sw
                if (sw.read_receipt(self.st) or {}).get('status') in sw.LIVE:
                    self.interrupted('worker_failed')
                    self.restart_wanted = True
                else:
                    (self.st.root / SNAPSHOT).unlink(missing_ok=True)
                self.persist()
            try:
                s = self.envfile.stat()
                stamp = (s.st_mtime_ns, s.st_size, s.st_ino)
            except FileNotFoundError:
                stamp = None
            marker = self.ola / 'restart'
            changed = stamp != self.stamp or marker.exists()
            if self.serve is not None and (self.serve.poll() is not None or self.restart_wanted or changed):
                self.stop(self.serve); self.serve = None
            if self.serve is None and time.monotonic() >= self.next_start:
                marker.unlink(missing_ok=True)
                try:
                    self.serve = self.spawn(['serve', '--events', 'quiet', '--no-stdin'])
                    self.generation += 1
                    self.stamp = stamp
                    self.restart_wanted = False
                except (OSError, ValueError):
                    pass
                self.next_start = time.monotonic() + 2

    def close(self):
        with self.lock:
            self.stop(self.worker); self.stop(self.serve)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            _, uid, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                return
            data = self.rfile.readline(MAX_PACKET + 1)
            if len(data) > MAX_PACKET:
                return
            result = self.server.manager.handle(json.loads(data))
            self.wfile.write(json.dumps(result).encode() + b'\n')
        except (OSError, ValueError, TypeError):
            return


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, path, manager):
        self.manager = manager
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(str(path), Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    from . import fence
    fence.no_dump()
    if not enabled():
        raise SystemExit('Olares lifecycle image trust failed')
    from .state import State
    manager = Supervisor(State())
    path = socket_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    d = path.parent.lstat()
    if not stat.S_ISDIR(d.st_mode) or d.st_uid != os.getuid() or d.st_mode & 0o077:
        raise SystemExit('Olares runtime trust failed')
    path.unlink(missing_ok=True)

    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    with Server(path, manager) as server:
        path.chmod(0o600)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            while not stopped.wait(.2):
                manager.tick()
        finally:
            manager.close()
            server.shutdown()
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
