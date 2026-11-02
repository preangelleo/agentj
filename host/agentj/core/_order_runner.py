"""Private scheduling owner, outside the order guardian's child tree.

Keeps the original CLI/serve-independent normal-completion drain. A real wait status and the
same-generation tree receipt precede dispatching another order; explicit hard-stop never drains.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import _guardian
from .orders import _write


def run(path, generation, argv):
    me = _guardian.identity(os.getpid())
    if me is None:
        return 97
    expected_runner = {"generation": generation, "runner_pid": os.getpid(), "runner_start": me[1]}
    startup = time.monotonic() + 15
    while True:
        try:
            if _guardian.bound(_guardian._load(path / "runner.json"), expected_runner):
                break
        except FileNotFoundError:
            pass
        if time.monotonic() >= startup:
            return 97
        time.sleep(0.01)
    p = subprocess.Popen([sys.executable, "-P", "-m", "agentj.core._guardian", str(path), generation, "--", *argv],
                         start_new_session=True)
    child = _guardian.identity(p.pid)
    if child is None or child[0] != os.getpid():
        p.kill()
        p.wait()
        return 97
    expected = {"generation": generation, "guardian_pid": p.pid, "guardian_start": child[1]}
    if not _guardian.bound(_guardian._load(path / "runner.json"), expected_runner):
        p.kill()  # this own guardian has not received its identity and cannot have launched a CEO
        p.wait()
        return 97
    try:
        _guardian.publish(path, "execution.json", {**expected_runner, **expected}, None, expected_runner, startup)
    except Exception:
        p.kill()  # still awaiting identity, this owned guardian cannot have launched customer work
        p.wait()
        return 97
    code = p.wait()
    if not _guardian.bound(_guardian._load(path / "execution.json"), {**expected, **expected_runner}):
        return 97  # an older runner must never overwrite a newer generation
    if not _guardian.bound(_guardian._load(path / "runner.json"), expected_runner):
        return 97
    deadline = _guardian.request(path, expected)
    _guardian.publish(path, "exit-status.json", {**expected_runner, **expected, "returncode": code},
                      {**expected_runner, **expected}, expected_runner, deadline or time.monotonic() + 30)
    if code != 0 or not _guardian.bound(_guardian._load(path / "exit-proof.json"), expected):
        return 97
    proof = _guardian._load(path / "exit-proof.json")
    if proof.get("quiet") is not True:
        return 97
    try:
        if _guardian.request(path, expected) is not None:
            return 0  # preserve hard-stop -> immediate resume before queued same-project orders
        if not _guardian.bound(_guardian._load(path / "execution.json"), {**expected, **expected_runner}):
            return 0  # the owner has already resumed a newer generation
        from .dispatcher import drain
        drain()
    except Exception:
        return 97
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 4 or args[2] != "--" or not args[1].isdigit():
        return 97
    return run(Path(args[0]), args[1], args[3:])


if __name__ == "__main__":
    sys.exit(main())
