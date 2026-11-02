"""Private Linux order supervisor: only its own kernel children, never the host process table.

The CEO keeps its original pid file and can outlive serve. This single-threaded subreaper also owns
ordinary detached Toolbox/Bash descendants after their parent exits. A receipt is written only after
all direct/adopted children have been reaped. The order's existing private directory is the channel;
no additional environment or directory access is given to toolboxes.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .orders import Order, OrderError, _write


def identity(pid):
    """Numeric identity of a specific child, not a process-table search."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[1]), fields[19]
    except (OSError, ValueError, IndexError):
        return None


def children():
    # This helper never starts a thread; its main task owns every direct/adopted child.
    raw = Path(f"/proc/self/task/{os.getpid()}/children").read_text()
    return [int(p) for p in raw.split()]


def _load(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate record field")
            value[key] = item
        return value
    value = json.loads(path.read_text(), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("invalid lifecycle record")
    return value


def bound(record, expected):
    return isinstance(record, dict) and all(type(record.get(k)) is type(v) and record[k] == v for k, v in expected.items())


def publish(path, name, value, expected, runner, deadline):
    """Serialize generation-check + atomic metadata write with the API's existing-directory claim."""
    from .dispatcher import _claim, _last_launch
    o = Order(path)
    while True:
        try:
            with _claim(o):
                if not bound(_load(path / "runner.json"), runner):
                    raise ValueError("runner generation changed")
                last = _last_launch(o)
                if last and last.get("generation") != runner["generation"]:
                    raise ValueError("launch generation changed")
                if expected is not None and not bound(_load(path / "execution.json"), expected):
                    raise ValueError("execution generation changed")
                _write(path / name, json.dumps(value))
                return
        except OrderError as e:
            if e.code != "order_busy" or time.monotonic() >= deadline:
                raise
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))


def request(path, expected):
    try:
        value = _load(path / "hard-stop.json")
    except FileNotFoundError:
        return None
    if not bound(value, expected):
        return None  # an earlier generation's request is not ours
    deadline = value.get("deadline")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise ValueError("invalid deadline")
    if deadline > time.monotonic() + 30:
        raise ValueError("invalid deadline")
    return deadline


def _reap(pid):
    try:
        got, status = os.waitpid(pid, os.WNOHANG)
        return os.waitstatus_to_exitcode(status) if got == pid else None
    except ChildProcessError:
        return None


def end_child(pid):
    """Signal a pidfd only after two same-start, direct-child ownership observations."""
    before = identity(pid)
    if before is None:
        return
    if before[0] != os.getpid():
        raise ValueError("child ownership changed")
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        if identity(pid) != before:
            raise ValueError("child identity changed")
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
    finally:
        os.close(fd)


def cleanup(deadline):
    while True:
        owned = children()
        if not owned:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("owned children did not exit")
        for pid in owned:
            if time.monotonic() >= deadline:
                raise TimeoutError("owned children did not exit")
            if _reap(pid) is None:
                end_child(pid)
                _reap(pid)
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def supervise(path, generation, argv):
    if not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return 97
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, this helper only
        return 97
    me = identity(os.getpid())
    if me is None:
        return 97
    expected = {"generation": generation, "guardian_pid": os.getpid(), "guardian_start": me[1]}
    # Parent publishes this owned Popen's identity before the helper can start work.
    startup_end = time.monotonic() + 15
    while True:
        try:
            record = _load(path / "execution.json")
            if bound(record, expected):
                break
        except FileNotFoundError:
            pass
        if time.monotonic() >= startup_end:
            return 97
        time.sleep(0.01)
    parent = identity(os.getppid())
    expected_runner = {"generation": generation, "runner_pid": os.getppid(),
                       "runner_start": parent[1] if parent else None}
    if parent is None or not bound(record, expected_runner):
        return 97
    expected_record = {**expected, **expected_runner}
    if not bound(_load(path / "runner.json"), expected_runner):
        return 97
    record["armed"] = True
    if not bound(_load(path / "execution.json"), expected_record):
        return 97
    publish(path, "execution.json", record, expected_record, expected_runner, startup_end)
    deadline = None
    try:
        deadline = request(path, expected)
        code = None
        if deadline is None:
            p = subprocess.Popen(argv, start_new_session=True)
            child = identity(p.pid)
            if child is None or child[0] != os.getpid():
                raise ValueError("CEO identity unavailable")
            record.update(ceo_pid=p.pid, ceo_start=child[1])
            if not bound(_load(path / "execution.json"), expected_record):
                raise ValueError("execution generation changed")
            publish(path, "execution.json", record, expected_record, expected_runner, startup_end)
            while True:
                if not bound(_load(path / "execution.json"), expected_record):
                    raise ValueError("execution generation changed")
                deadline = request(path, expected)
                if deadline is not None:
                    break
                code = p.poll()
                if code is not None:
                    break
                time.sleep(0.02)
        if deadline is None:
            deadline = time.monotonic() + 30
        cleanup(deadline)
        if not bound(_load(path / "execution.json"), expected_record):
            raise ValueError("execution generation changed")
        publish(path, "exit-proof.json", {**expected, "quiet": True, "ceo_exit": code},
                expected_record, expected_runner, deadline)
        return 0
    except Exception:
        # A failed observer never issues an exit proof, even if emergency cleanup succeeds.
        try:
            cleanup(min(deadline or time.monotonic() + 30, time.monotonic() + 30))
        except Exception:
            pass
        return 97


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 4 or args[2] != "--" or not args[1].isdigit():
        return 97
    return supervise(Path(args[0]), args[1], args[3:])


if __name__ == "__main__":
    sys.exit(main())
