"""P452: scheduling for the transport and for work it launches. No root required.

This file is also an isolated exec trampoline: it runs BEFORE the existing fence,
never imports host state, and preserves argv, environment, stdio, PID and exit status.
"""
from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path
import subprocess
import sys

TRANSPORT_UNIT = ("CPUWeight=1000", "IOWeight=1000", "MemoryLow=256M", "OOMScoreAdjust=0")
PING_INTERVAL = 20
PING_TIMEOUT = 90
OPEN_TIMEOUT = 30


def work_argv(argv):
    return [sys.executable, "-I", str(Path(__file__).resolve()), "--", *argv]


def _work_scope():
    """Move THIS trampoline into a low-weight sibling scope before exec/fork.

    Unlike wrapping the payload in systemd-run, manager failure cannot lose a task
    or run it twice. A failed/unsupported scope just leaves the nice fallback.
    PIDs are attached by the user's manager; no cgroup writes or root delegation.
    """
    bus = "/usr/bin/busctl" if os.access("/usr/bin/busctl", os.X_OK) else None
    if not bus:
        return False
    unit = f"agentj-work-{os.getpid()}.scope"
    try:
        parts = Path("/proc/self/cgroup").read_text().strip().split("/")
        # A sibling scope must die with the originating host service, even if
        # serve crashes before its normal process-group cleanup can run.
        parent = next((x for x in reversed(parts) if x.endswith(".service") and not x.startswith("user@")), None)
        lifetime = ["BindsTo", "as", "1", parent, "After", "as", "1", parent] if parent else []
        r = subprocess.run([bus, "--user", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                            "org.freedesktop.systemd1.Manager", "StartTransientUnit", "ssa(sv)a(sa(sv))",
                            unit, "fail", str(5 + (2 if parent else 0)), "Description", "s", "Agent J background work",
                            "PIDs", "au", "1", str(os.getpid()), "CPUWeight", "t", "20", "IOWeight", "t", "20",
                            "CollectMode", "s", "inactive-or-failed", *lifetime, "0"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
        if r.returncode:
            return False
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if unit in Path("/proc/self/cgroup").read_text().split("/")[-1].splitlines():
                return True
            time.sleep(.01)
        return False
    except (OSError, subprocess.SubprocessError):
        return False


def exec_work(argv):
    if not argv:
        raise SystemExit(2)
    if hasattr(os, "setpriority"):
        with contextlib.suppress(OSError):
            os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError):
            Path("/proc/self/oom_score_adj").write_text("200")
        _work_scope()
    elif sys.platform == "darwin":
        policy = "/usr/bin/taskpolicy" if os.access("/usr/bin/taskpolicy", os.X_OK) else None
        if policy:
            argv = [policy, "-b", *argv]
    os.execvpe(argv[0], argv, os.environ)


def _properties(unit):
    from . import service
    r = service._systemctl("show", unit, "-p", "ControlGroup", "-p", "MainPID", "-p", "CPUWeight",
                           "-p", "IOWeight", "-p", "MemoryLow", "-p", "OOMScoreAdjust", timeout=5)
    if r.returncode:
        return {}
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def inspect_unit(unit):
    """Effective kernel files as well as manager configuration; never read environment."""
    p = _properties(unit)
    cg = p.get("ControlGroup", "")
    if not cg.startswith("/") or ".." in Path(cg).parts:
        return {"unit": unit, "effective": False, "missing": ["cgroup"]}
    root = Path("/sys/fs/cgroup") / cg.lstrip("/")
    expected = {"cpu.weight": "1000", "io.weight": "default 1000", "memory.low": str(256 * 1024**2)}
    missing = []
    for file, want in expected.items():
        try:
            value = (root / file).read_text().strip()
        except OSError:
            value = ""
        if value != want:
            missing.append(file)
    # Controllers must reach the actual unit, not merely be listed at cgroup root.
    try:
        delegated = set((root.parent / "cgroup.subtree_control").read_text().split())
    except OSError:
        delegated = set()
    missing += sorted({"cpu", "io", "memory"} - delegated)
    try:
        oom = int(Path("/proc", str(int(p.get("MainPID", "0"))), "oom_score_adj").read_text())
        if oom > 0:
            missing.append("oom_score_adj")
    except (ValueError, OSError):
        missing.append("oom_score_adj")
    return {"unit": unit, "effective": not missing, "missing": missing}


def memory_state():
    """Read-only pressure observation, never an uptime or reservation guarantee."""
    if not sys.platform.startswith("linux"):
        return "unknown"
    try:
        group = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::/"))
        if ".." not in Path(group).parts:
            root = Path("/sys/fs/cgroup") / group.lstrip("/")
            current = int((root / "memory.current").read_text())
            limits = [(root / name).read_text().strip() for name in ("memory.high", "memory.max")]
            limits = [int(x) for x in limits if x.isdecimal() and int(x) > 0]
            if limits and current >= min(limits) * .9:
                return "pressured"
    except (OSError, ValueError, StopIteration):
        pass
    try:
        values = dict((k, int(v.split()[0])) for k, v in
                      (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()))
        return "pressured" if values["MemAvailable"] < max(256 * 1024, values["MemTotal"] // 20) else "observed_available"
    except (OSError, ValueError, KeyError):
        return "unknown"


def status(svc):
    from . import service
    if not svc.get("installed"):
        return {"effective": False, "detail": "service not installed"}
    if svc.get("kind") == "systemd":
        units = [inspect_unit(svc["name"] + ".service"), inspect_unit(service.rescue_name(svc["name"]) + ".service")]
        return {"effective": all(x["effective"] for x in units), "detail": "; ".join(
            x["unit"] + ": " + (", ".join(x["missing"]) or "effective") for x in units)}
    if svc.get("kind") == "launchd":
        # launchctl print reports the loaded process classification, not just a file waiting for restart.
        effective = True
        for label in (svc["name"], service.rescue_name(svc["name"])):
            r = service._launchctl("print", f"{service._gui()}/{label}", timeout=5)
            effective &= r.returncode == 0 and bool(re.search(r"spawn type = interactive(?: \(|$)", r.stdout, re.M)) and "low priority i/o = 1" not in r.stdout
        return {"effective": bool(effective), "detail": "host + rescue: loaded Interactive classification" if effective else "host or rescue is not loaded as Interactive"}
    return {"effective": False, "detail": "no supported service manager (Windows uses WSL2)"}


if __name__ == "__main__":
    exec_work(sys.argv[2:] if sys.argv[1:2] == ["--"] else [])
