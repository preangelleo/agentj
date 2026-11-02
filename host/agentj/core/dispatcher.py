"""Dispatcher (AGENTJ-CORE-SPEC 12.4 host layer, D13): starts, stops, resumes and reconciles CEO processes of orders.

A CEO is its own process — `python -P -m agentj.core.ceo <id>` — so serve restarts and upgrades never cut it: on Linux a
transient user unit `agentj-o<id>` (systemd-run --user), elsewhere (or AGENTJ_ORDER_LAUNCHER=proc) a detached process with its log in the order directory. The order directory
is the only truth; the CEO writes `pid` itself, liveness = that pid alive AND running this order (pid reuse guard).

Limits (D12, 12.9): at most CEO_MAX running CEOs across every main session; more are queued (`queued` event) and the next
one starts when a CEO ends (drain). Preflight (§14.1): an order for a project that already has a live order queues behind it
(blocked-by); when that one ends, drain starts it — the automatic un-blocking of §14.2.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import stat
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import orders
from .orders import Order, OrderError

STOP_GRACE = 30
# The existing systemctl hard-stop ceiling is shared with exit confirmation, never added per phase.
_STOP_HARD = 30
_OWNED = {}  # absolute order path -> launch-owned Popen; never inferred from a PID file


def _key(o):
    return str(o.path.resolve())


@contextmanager
def _claim(o):
    """Atomic Linux order lifecycle transitions; no lock file or permission/approval policy."""
    if not sys.platform.startswith("linux"):
        yield  # retain the pre-existing legacy launch capability; no cross-CLI guarantee is claimed
        return
    import fcntl
    try:
        fd = os.open(o.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        raise OrderError("stop_unconfirmed") from e
    try:
        opened, current = os.fstat(fd), os.stat(o.path, follow_symlinks=False)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise OrderError("stop_unconfirmed")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise OrderError("order_busy") from e
        except OSError as e:
            raise OrderError("stop_unconfirmed") from e
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _last_launch(o):
    try:
        return next((e for e in reversed(o.events()) if e.get("ev") == "launch"), {})
    except (OSError, ValueError) as e:
        raise OrderError("stop_unconfirmed") from e


def _record(o, name):
    try:
        from ._guardian import _load
        value = _load(o.path / name)
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise OrderError("stop_unconfirmed") from e


def _identities(value, *, runner=True):
    names = ("guardian", "runner") if runner else ("guardian",)
    if not isinstance(value.get("generation"), str) or not value["generation"].isdigit():
        raise OrderError("stop_unconfirmed")
    for name in names:
        pid, token = value.get(name + "_pid"), value.get(name + "_start")
        if type(pid) is not int or pid <= 0 or not isinstance(token, str) or not token.isdigit():
            raise OrderError("stop_unconfirmed")
    if "ceo_pid" in value or "ceo_start" in value:
        if type(value.get("ceo_pid")) is not int or value["ceo_pid"] <= 0 \
                or not isinstance(value.get("ceo_start"), str) or not value["ceo_start"].isdigit():
            raise OrderError("stop_unconfirmed")


def _wait_status(o, expected):
    from ._guardian import bound
    record = _record(o, "execution.json") or {}
    _identities(record)
    if not bound(record, expected):
        raise OrderError("stop_unconfirmed")
    expected_runner = {"generation": record["generation"], "runner_pid": record["runner_pid"],
                       "runner_start": record["runner_start"]}
    if not bound(_record(o, "runner.json") or {}, expected_runner):
        raise OrderError("stop_unconfirmed")
    status = _record(o, "exit-status.json")
    if status is None or status.get("generation") != expected["generation"]:
        return None
    _identities(status)
    if not bound(status, {**expected, **expected_runner}) or type(status.get("returncode")) is not int:
        raise OrderError("stop_unconfirmed")
    return status


class _Execution:
    """Observe one guardian's actual exit AND its matching whole-owned-tree receipt."""
    def __init__(self, pid, token, *, proc=None, fd=None, o=None, generation=None, exited=False):
        self.pid, self.token, self.proc, self.fd = pid, token, proc, fd
        self.o, self.generation, self.gone = o, generation, exited
        self.kind = "proc"

    def expected(self):
        return {"generation": self.generation, "guardian_pid": self.pid, "guardian_start": self.token}

    def exited(self):
        status = _wait_status(self.o, self.expected())
        if status is not None or self.gone:
            return True
        fd = self.fd
        if fd is None:
            return False
        try:
            return bool(select.select([fd], [], [], 0)[0])
        except OSError as e:
            if _wait_status(self.o, self.expected()) is not None:
                return True  # another completed observer closed the same owned descriptor
            raise OrderError("stop_unconfirmed") from e


    def quiet(self):
        from ._guardian import bound
        record = _record(self.o, "execution.json") or {}
        last = _last_launch(self.o)
        if last.get("generation", self.generation) != self.generation or not bound(record, self.expected()):
            raise OrderError("stop_unconfirmed")
        if not self.exited():
            return False
        status = _wait_status(self.o, self.expected())
        if status is None:
            return False  # the runner must publish its actual child wait status first
        if status["returncode"] != 0:
            raise OrderError("stop_unconfirmed")
        proof = _record(self.o, "exit-proof.json") or {}
        _identities(proof, runner=False)
        if not bound(proof, self.expected()) or proof.get("quiet") is not True \
                or (proof.get("ceo_exit") is not None and type(proof["ceo_exit"]) is not int):
            raise OrderError("stop_unconfirmed")
        return True

    def kill(self, *, deadline):
        from ..codex_procs import start_token
        if self.exited():
            return  # only quiet() plus the actual wait status can establish success
        if not self.token or start_token(self.pid) != self.token:
            raise OrderError("stop_unconfirmed")
        if (_record(self.o, "execution.json") or {}).get("generation") != self.generation:
            raise OrderError("stop_unconfirmed")
        orders._write(self.o.path / "hard-stop.json", json.dumps({**self.expected(), "deadline": deadline}))

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)


def _group_path(group):
    if not isinstance(group, str) or not group.startswith("/") or ".." in group.split("/"):
        raise OrderError("stop_unconfirmed")
    return Path("/sys/fs/cgroup") / group.lstrip("/")


def _group_absent(group):
    try:
        _group_path(group).stat()
        return False
    except FileNotFoundError:
        return True
    except OSError as e:
        raise OrderError("stop_unconfirmed") from e


def _slice_group(run, deadline):
    for attempt in range(2):
        try:
            r = run(["systemctl", "--user", "show", "app.slice", "--property=Id,LoadState,ActiveState,ControlGroup"],
                    capture_output=True, text=True, timeout=max(0, deadline - time.monotonic()))
            fields = dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
            if r.returncode != 0 or fields.get("Id") != "app.slice" or fields.get("LoadState") != "loaded":
                raise OrderError("launch_failed", "Owned application slice unavailable.")
            group = fields.get("ControlGroup", "")
            if group:
                _group_path(group)
                if group.rsplit("/", 1)[-1] != "app.slice":
                    raise OrderError("launch_failed", "Owned application slice unavailable.")
                return group
            if attempt or fields.get("ActiveState") != "inactive":
                raise OrderError("launch_failed", "Owned application slice unavailable.")
            r = run(["systemctl", "--user", "start", "app.slice"], capture_output=True,
                    timeout=max(0, deadline - time.monotonic()))
            if r.returncode != 0:
                raise OrderError("launch_failed", "Owned application slice unavailable.")
        except (OSError, subprocess.SubprocessError) as e:
            raise OrderError("launch_failed", "Owned application slice unavailable.") from e
    raise OrderError("launch_failed")


class _Unit:
    """The original owned transient unit, whose manager controls its complete cgroup."""
    kind = "systemd"

    def __init__(self, o, run):
        self.o, self.run = o, run
        self.deadline = time.monotonic() + _STOP_HARD

    def quiet(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            r = self.run(["systemctl", "--user", "show", f"agentj-{self.o.id}",
                          "--property=Id,LoadState,ActiveState,SubState,MainPID,Transient,Description,KillMode,ControlGroup"],
                         capture_output=True, text=True, timeout=remaining)
        except subprocess.TimeoutExpired:
            return False
        except (OSError, subprocess.SubprocessError) as e:
            raise OrderError("stop_unconfirmed") from e
        fields = dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
        if fields.get("Id") != f"agentj-{self.o.id}.service":
            raise OrderError("stop_unconfirmed")
        binding = _record(self.o, "unit-execution.json")
        last = _last_launch(self.o)
        if binding is not None:
            if binding.get("generation") != last.get("generation") or binding.get("unit") != fields["Id"]:
                raise OrderError("stop_unconfirmed")
            group = binding.get("cgroup")
            _group_path(group)
            if group.rsplit("/", 1)[-1] != f"agentj-{self.o.id}.service":
                raise OrderError("stop_unconfirmed")
            if fields.get("LoadState") == "not-found" and r.returncode in (0, 1):
                return _group_absent(group)
            if fields.get("ControlGroup") != group:
                raise OrderError("stop_unconfirmed")
        if r.returncode != 0:
            raise OrderError("stop_unconfirmed")
        if fields.get("Transient") != "yes" or fields.get("Description") != f"agentj order {self.o.id}" \
                or fields.get("KillMode") != "control-group":
            raise OrderError("stop_unconfirmed")
        group = fields.get("ControlGroup", "")
        if not group.startswith("/") or ".." in group.split("/") \
                or group.rsplit("/", 1)[-1] != f"agentj-{self.o.id}.service":
            raise OrderError("stop_unconfirmed")
        if fields.get("ActiveState") in ("inactive", "failed") and fields.get("MainPID") == "0":
            try:
                data = (Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events").read_text()
            except FileNotFoundError:
                # This exact, manager-owned cgroup has already been removed.
                return _group_absent(group)
            except OSError as e:
                raise OrderError("stop_unconfirmed") from e
            values = dict(line.split() for line in data.splitlines() if len(line.split()) == 2)
            return values.get("populated") == "0"
        return False

    def kill(self, *, deadline):
        self.deadline = deadline
        binding = _record(self.o, "unit-execution.json")
        if binding is not None:
            last = _last_launch(self.o)
            if binding.get("generation") != last.get("generation"):
                raise OrderError("stop_unconfirmed")
            orders._write(self.o.path / "unit-hard-stop.json", json.dumps({**binding, "deadline": deadline}))
        try:
            r = self.run(["systemctl", "--user", "stop", "--no-block", f"agentj-{self.o.id}"], capture_output=True,
                         timeout=max(0, deadline - time.monotonic()))
        except (OSError, subprocess.SubprocessError) as e:
            raise OrderError("stop_unconfirmed") from e
        if r.returncode != 0:
            raise OrderError("stop_unconfirmed")

    def close(self):
        pass


def _execution(o, run=subprocess.run):
    from ..codex_procs import start_token
    from ._guardian import bound
    last = (_last_launch(o) or None)
    if last is not None and last.get("launcher") == "systemd":
        return _Unit(o, run)
    if last is not None and last.get("supervision") != "guardian-v1":
        raise OrderError("legacy_stop_unconfirmed", "The original execution has no owned-tree exit proof; its report is preserved.")
    owned = _OWNED.get(_key(o))
    if owned is not None:
        if last is not None and last.get("generation") != owned.generation:
            raise OrderError("stop_unconfirmed")
        return owned
    record = _record(o, "execution.json")
    if record is None:
        if last is not None:
            raise OrderError("stop_unconfirmed")
        if pid_of(o):
            raise OrderError("legacy_stop_unconfirmed", "The original execution has no owned-tree exit proof; its report is preserved.")
        return None  # queued/new order, no execution was launched
    if last is not None and last.get("generation") != record.get("generation"):
        raise OrderError("stop_unconfirmed")
    pid, token, generation = record.get("guardian_pid"), record.get("guardian_start"), record.get("generation")
    if type(pid) is not int or pid <= 0 or not isinstance(token, str) or not token \
            or not isinstance(generation, str) or not generation.isdigit():
        raise OrderError("stop_unconfirmed")
    execution = _Execution(pid, token, o=o, generation=generation)
    if start_token(pid) != token:
        # Only a same-generation completed receipt permits a historical order to resume.
        execution.gone = True
        if not execution.quiet():
            raise OrderError("stop_unconfirmed")
        return execution
    if not hasattr(os, "pidfd_open"):
        raise OrderError("stop_unconfirmed")
    try:
        execution.fd = os.pidfd_open(pid)
        if start_token(pid) != token and not execution.exited():
            raise OrderError("stop_unconfirmed")
        if not bound(_record(o, "execution.json") or {}, execution.expected()):
            raise OrderError("stop_unconfirmed")
        return execution
    except (OSError, OrderError) as e:
        execution.close()
        raise OrderError("stop_unconfirmed") from e


def _release(o, execution):
    if execution is not None:
        if _OWNED.get(_key(o)) is execution:
            # A failed guardian stays pinned: neither a result nor a stale receipt makes it successful.
            try:
                if execution.quiet():
                    _OWNED.pop(_key(o), None)
                    execution.close()
            except OrderError:
                pass
        else:
            execution.close()


def _proc_has(pid: int, oid: str) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return oid.encode() in f.read()
    except OSError:
        return sys.platform != "linux"           # no /proc (macOS): the pid alone


def pid_of(o: Order) -> int | None:
    try:
        return int((o.path / "pid").read_text().strip())
    except (OSError, ValueError):
        return None


def alive(o: Order) -> bool:
    p = pid_of(o)
    return bool(p) and _proc_has(p, o.id)


def _occupied(o):
    # Only newly supervised generations gain tree-aware occupancy. Historical scheduling stays compatible.
    last = _last_launch(o)
    if last.get("supervision") == "legacy" or (last.get("supervision") not in ("guardian-v1", "unit-v1") \
            and _key(o) not in _OWNED and not (o.path / "execution.json").exists()):
        return o.state() == "running" and alive(o)
    execution = None
    try:
        execution = _execution(o)
        return execution is not None and not execution.quiet()
    except OrderError:
        return True
    finally:
        _release(o, execution)


def running(st_root=None) -> list[Order]:
    return [o for o in orders.all_orders(st_root) if _occupied(o)]


def launcher(environ=None) -> str:
    env = os.environ if environ is None else environ
    want = env.get("AGENTJ_ORDER_LAUNCHER", "")
    if want in ("systemd", "proc"):
        return want
    from ..fence import inside
    if sys.platform.startswith("linux") and shutil.which("systemd-run") and not inside(env):
        return "systemd"
    return "proc"


def ceo_argv(o: Order) -> list[str]:
    return [sys.executable, "-P", "-m", "agentj.core.ceo", o.id]


def _env_names(o: Order, environ) -> list[str]:
    """Non-secret names the CEO needs: state dir, the core switch, proxies, the launcher. NEVER a key (P359 security
    review H1): systemd keeps --setenv values in the transient unit file and on the bus, both reachable from inside the
    fence (fence.py binds the user's systemd dir and bus back). The key reaches a systemd CEO only through the service's own
    EnvironmentFile (a path in the unit, the values read by systemd at start)."""
    from .toolbox import SECRETISH
    names = ["AGENTJ_STATE_DIR", "AGENTJ_CORE", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY",
             "no_proxy", "AGENTJ_ORDER_LAUNCHER", "AGENTJ_ORDER_ID"]
    return [n for n in names if environ.get(n) and not SECRETISH.search(n)]


def launch(o: Order, **kw) -> str:
    with _claim(o):
        pending = _launch_unlocked(o, **kw)
    return pending.finish() if isinstance(pending, _Pending) else pending


def _launch_unlocked(o: Order, *, environ=None, st_root=None, run=subprocess.run, popen=subprocess.Popen):
    """Start (or queue) the order's CEO. → 'started' | 'queued'."""
    env = dict(os.environ if environ is None else environ)
    env["AGENTJ_ORDER_ID"] = o.id
    if st_root is not None:
        env["AGENTJ_STATE_DIR"] = str(st_root)
    if o.state() == "running" and alive(o):
        raise OrderError("already_running")
    if _occupied(o):
        raise OrderError("already_running")
    go, blockers = orders.preflight(o, st_root, live=_occupied)
    occupied = running(st_root)
    for other in occupied:
        if other.id != o.id and other.meta["project"] == o.meta["project"] \
                and (other.meta.get("seq", 0), other.id) < (o.meta.get("seq", 0), o.id) and other.id not in blockers:
            blockers.append(other.id)
    if blockers or go == "queue" or len(occupied) >= orders.CEO_MAX:
        o.event("queued", blocked_by=blockers, reason="project_busy" if blockers else "ceo_limit")
        return "queued"
    tok = secrets.token_urlsafe(24)
    orders._write(o.path / ".ask_token", tok)
    (o.path / "pid").unlink(missing_ok=True)
    kind = launcher(env)
    generation = str(time.monotonic_ns())
    supervised = kind == "proc" and sys.platform.startswith("linux") and hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")
    if kind == "systemd":
        launch_deadline = time.monotonic() + 30
        group = _slice_group(run, launch_deadline)
        orders._write(o.path / "unit-execution.json", json.dumps({"generation": generation,
                     "unit": f"agentj-{o.id}.service", "cgroup": group + f"/agentj-{o.id}.service"}))
    o.event("launch", launcher=kind, generation=generation,
            supervision="guardian-v1" if supervised else "unit-v1" if kind == "systemd" else "legacy")
    from ..agent import source_root
    src = source_root()
    if src:
        env["PYTHONPATH"] = src
    if kind == "systemd":
        from .. import service
        argv = ["systemd-run", "--user", "--unit", f"agentj-{o.id}", "--collect", "--quiet",
                "--slice=app.slice", f"--working-directory={o.meta['project']}", f"--description=agentj order {o.id}",
                "--property=KillMode=control-group",
                "--property=EnvironmentFile=-" + service.env_file(service.name(), env)]
        argv += [f"--setenv={n}" for n in _env_names(o, env) + (["PYTHONPATH"] if src else []) + ["PATH", "HOME", "LANG"]
                 if env.get(n)]
        r = run(argv + ["--"] + ceo_argv(o), env=env, capture_output=True, text=True,
                timeout=max(0, launch_deadline - time.monotonic()))
        if r.returncode != 0:
            o.event("failed", reason="launch")
            raise OrderError("launch_failed", (r.stderr or "")[:200])
        # The unit's own CEO cannot unlock a cgroup that is still running itself.
        # This sibling owns only later scheduling, after native whole-unit quiescence.
        handoff = popen([sys.executable, "-P", "-m", "agentj.core._unit_handoff", str(o.path.resolve()), generation],
                        cwd=o.meta["project"], env=env, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if hasattr(handoff, "wait"):
            threading.Thread(target=handoff.wait, name=f"drain-{o.id}", daemon=True).start()
        return "started"
    log = open(o.path / "ceo.log", "ab")
    try:
        os.chmod(o.path / "ceo.log", 0o600)
        from ..codex_procs import start_token
        argv = ([sys.executable, "-P", "-m", "agentj.core._order_runner", str(o.path.resolve()), generation, "--"]
                + ceo_argv(o)) if supervised else ceo_argv(o)
        p = popen(argv, cwd=o.meta["project"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                  start_new_session=True)
        if hasattr(p, "wait"):
            if supervised:
                token = start_token(p.pid)
                if not token:
                    p.kill()
                    p.wait(timeout=15)
                    raise OrderError("launch_failed", "Owned supervisor identity unavailable.")
                orders._write(o.path / "runner.json", json.dumps({"generation": generation,
                             "runner_pid": p.pid, "runner_start": token}))
                return _Pending(o, p, generation, token)
            else:
                # Keep the existing non-Linux launch capability; no Linux tree proof is invented.
                threading.Thread(target=p.wait, name=f"reap-{o.id}", daemon=True).start()
    finally:
        log.close()
    return "started"


class _Pending:
    def __init__(self, o, p, generation, token):
        self.o, self.p, self.generation, self.token = o, p, generation, token

    def finish(self):
        o, p, generation, token = self.o, self.p, self.generation, self.token
        end = time.monotonic() + 15
        while True:
            record = _record(o, "execution.json") or {}
            if record.get("generation") == generation and record.get("runner_pid") == p.pid \
                    and record.get("runner_start") == token and record.get("armed") is True:
                break
            if p.poll() is not None or time.monotonic() >= end:
                raise OrderError("launch_failed", "Owned supervisor did not start.")
            time.sleep(0.01)
        execution = _Execution(record["guardian_pid"], record["guardian_start"], proc=p, o=o, generation=generation)
        from ..codex_procs import start_token
        try:
            execution.fd = os.pidfd_open(execution.pid)
            if start_token(execution.pid) != execution.token:
                execution.close()
                execution.gone = True  # never observe a reused numeric PID as this guardian
        except ProcessLookupError:
            execution.gone = True
        try:
            while True:
                try:
                    with _claim(o):
                        if _last_launch(o).get("generation") != generation:
                            raise OrderError("stop_unconfirmed")
                        _OWNED[_key(o)] = execution
                    break
                except OrderError as e:
                    if e.code != "order_busy" or time.monotonic() >= end:
                        raise
                    time.sleep(min(0.01, max(0, end - time.monotonic())))
        except BaseException:
            execution.close()
            raise
        def reap():
            p.wait()
            _release(o, execution)
        threading.Thread(target=reap, name=f"reap-{o.id}", daemon=True).start()
        return "started"


def wait_started(o: Order, timeout: float = 15) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if alive(o) or o.result():
            return True
        time.sleep(0.2)
    return False


def stop(o: Order, *, grace: float | None = None, run=subprocess.run) -> str:
    """Request a partial report, then hard-stop; success requires the owned execution's exit."""
    grace = STOP_GRACE if grace is None else grace
    with _claim(o):
        generation = _last_launch(o).get("generation")
        try:
            execution = _execution(o, run=run)
        except OrderError as e:
            if e.code == "legacy_stop_unconfirmed":
                o.set_control("stop")
                o.event("stop_requested", unconfirmed=True)
            raise
        if execution is None and alive(o):
            raise OrderError("stop_unconfirmed")
        if not o.result():
            o.set_control("stop")
            o.event("stop_requested")
    try:
        end = time.monotonic() + grace
        if isinstance(execution, _Unit):
            execution.deadline = end
        while execution is not None and not execution.quiet() and time.monotonic() < end:
            time.sleep(min(0.5, max(0, end - time.monotonic())))
        if execution is not None and not execution.quiet():
            end = time.monotonic() + _STOP_HARD
            with _claim(o):
                if _last_launch(o).get("generation") != generation:
                    raise OrderError("stop_unconfirmed")
                execution.kill(deadline=end)
            while not execution.quiet() and time.monotonic() < end:
                time.sleep(min(0.05, max(0, end - time.monotonic())))
            if not execution.quiet():
                raise OrderError("stop_unconfirmed")
        with _claim(o):
            if _last_launch(o).get("generation") != generation:
                raise OrderError("stop_unconfirmed")
            if execution is not None and not execution.quiet():
                raise OrderError("stop_unconfirmed")
            if not o.result():
                o.event("stopped", hard=True)
                o.write_result({"state": "stopped", "exit_code": None, "verdict": "aborted", "head": orders.parse_head(o.report()),
                                "failure_kind": "unknown", "ended": time.time()})
            return o.state()
    finally:
        _release(o, execution)


def resume(o: Order, text: str | None = None, **kw) -> str:
    with _claim(o):
        pending = _resume_unlocked(o, text, **kw)
    return pending.finish() if isinstance(pending, _Pending) else pending


def _resume_unlocked(o: Order, text: str | None = None, *, environ=None, st_root=None, **kw):
    if alive(o):
        raise OrderError("still_running")
    execution = _execution(o)
    try:
        if execution is not None and not execution.quiet():
            raise OrderError("still_running")
    finally:
        _release(o, execution)
    if text:
        o.send(text)
    prev = o.result()
    if prev:
        hist = o.path / "history.jsonl"
        orders._append(hist, prev)
        (o.path / "result.json").unlink(missing_ok=True)
    o.set_control(None)
    o.event("resume", after=(prev or {}).get("verdict"))
    return _launch_unlocked(o, environ=environ, st_root=st_root, **kw)


def reconcile(st_root=None) -> list[str]:
    """After a serve restart (12.4): a 'running' order whose CEO is gone and left no result is failed (lost)."""
    lost = []
    for o in orders.all_orders(st_root):
        if o.state() == "running" and not _occupied(o) and not o.result():
            o.event("failed", reason="lost")
            h = orders.parse_head(o.report())
            o.write_result({"state": "failed", "exit_code": None, "head": h, "verdict": orders.judge(None, h),
                            "failure_kind": "unknown", "ended": time.time()})
            lost.append(o.id)
    return lost


def drain(st_root=None, environ=None, **kw) -> list[str]:
    """Start queued orders whose blockers are over, while there is room."""
    started = []
    for o in orders.all_orders(st_root):
        if o.state() != "queued":
            continue
        if len(running(st_root)) >= orders.CEO_MAX:
            break
        try:
            if launch(o, environ=environ, st_root=st_root, **kw) == "started":
                started.append(o.id)
        except OrderError:
            continue
    return started


# ------------------------------------------------------------------------------------------------ the `dispatch` tool
def tool(args: dict, *, parent: dict, allowed_roots, st_root=None, environ=None, model: str | None = None,
         origin: str = "owner_turn", fence: bool = True, danger_extra: list | None = None) -> str:
    """The main Agent's one tool, six actions (12.5). Returns short text for the model (12.8 budgets)."""
    act = args.get("action")
    try:
        if act == "new":
            brief = args.get("brief") if isinstance(args.get("brief"), dict) else {}
            o = orders.create(project=str(args.get("project") or ""), prompt_text=str(args.get("prompt") or ""), brief=brief,
                              title=str(args.get("title") or ""), parent=parent, model=model,
                              mode=args.get("mode") or "work", origin=origin, st_root=st_root, allowed_roots=allowed_roots,
                              fence=fence, danger_extra=danger_extra, grants=args.get("grants"))
            how = launch(o, environ=environ, st_root=st_root)
            return f"order {o.id} {how}. You will be told when it ends; then read its report head."
        o = orders.get(str(args.get("id") or ""), st_root)
        if act == "status":
            evs = o.events()
            last = evs[-1] if evs else {}
            subs = sum(1 for e in evs if e.get("ev") == "subagent_start") - sum(1 for e in evs if e.get("ev") == "subagent_end")
            return (f"order {o.id}: {o.state()}" + (" (process alive)" if alive(o) else "")
                    + f" · last event {last.get('ev', '-')} · sub-agents running {max(0, subs)}")
        if act == "send":
            n = o.send(str(args.get("text") or ""))
            return f"appended #{n}; the CEO gets it at its next step" + ("" if alive(o) else " (not running: resume it)")
        if act == "stop":
            return f"order {o.id}: {stop(o)}"
        if act == "resume":
            how = resume(o, args.get("text") or None, environ=environ, st_root=st_root)
            return f"order {o.id} resumed: {how}"
        if act == "report":
            sec = args.get("section")
            return orders.section(o, int(sec)) if isinstance(sec, int) else orders.head_card(o)
        return "Error: action must be new, status, send, stop, resume or report."
    except OrderError as e:
        return f"Error: {e.code}" + (f" ({e.detail})" if e.detail else "")
