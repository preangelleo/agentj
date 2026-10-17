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
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import orders
from .orders import Order, OrderError

STOP_GRACE = 30


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


def running(st_root=None) -> list[Order]:
    return [o for o in orders.all_orders(st_root) if o.state() == "running" and alive(o)]


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


def launch(o: Order, *, environ=None, st_root=None, run=subprocess.run, popen=subprocess.Popen) -> str:
    """Start (or queue) the order's CEO. → 'started' | 'queued'."""
    env = dict(os.environ if environ is None else environ)
    env["AGENTJ_ORDER_ID"] = o.id
    if st_root is not None:
        env["AGENTJ_STATE_DIR"] = str(st_root)
    if o.state() == "running" and alive(o):
        raise OrderError("already_running")
    go, blockers = orders.preflight(o, st_root, live=alive)
    if go == "queue" or len(running(st_root)) >= orders.CEO_MAX:
        o.event("queued", blocked_by=blockers, reason="project_busy" if blockers else "ceo_limit")
        return "queued"
    tok = secrets.token_urlsafe(24)
    orders._write(o.path / ".ask_token", tok)
    (o.path / "pid").unlink(missing_ok=True)
    o.event("launch", launcher=launcher(env))
    from ..agent import source_root
    src = source_root()
    if src:
        env["PYTHONPATH"] = src
    if launcher(env) == "systemd":
        from .. import service
        argv = ["systemd-run", "--user", "--unit", f"agentj-{o.id}", "--collect", "--quiet",
                f"--working-directory={o.meta['project']}", f"--description=agentj order {o.id}",
                "--property=EnvironmentFile=-" + service.env_file(service.name(), env)]
        argv += [f"--setenv={n}" for n in _env_names(o, env) + (["PYTHONPATH"] if src else []) + ["PATH", "HOME", "LANG"]
                 if env.get(n)]
        r = run(argv + ["--"] + ceo_argv(o), env=env, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            o.event("failed", reason="launch")
            raise OrderError("launch_failed", (r.stderr or "")[:200])
        return "started"
    log = open(o.path / "ceo.log", "ab")
    try:
        os.chmod(o.path / "ceo.log", 0o600)
        p = popen(ceo_argv(o), cwd=o.meta["project"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                  start_new_session=True)
        if hasattr(p, "wait"):               # reaped when it ends: no zombie under serve while the CEO outlives a turn
            threading.Thread(target=p.wait, name=f"reap-{o.id}", daemon=True).start()
    finally:
        log.close()
    return "started"


def wait_started(o: Order, timeout: float = 15) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if alive(o) or o.result():
            return True
        time.sleep(0.2)
    return False


def stop(o: Order, *, grace: float | None = None, run=subprocess.run) -> str:
    """O7: ask for a partial report first, hard stop after `grace` seconds (the sub-agents live in the same process group)."""
    grace = STOP_GRACE if grace is None else grace
    if o.result():
        return o.state()
    o.set_control("stop")
    o.event("stop_requested")
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if o.result():
            return o.state()
        if not alive(o) and o.state() != "queued":
            break
        time.sleep(0.5)
    if o.state() == "queued" or not o.result():
        p = pid_of(o)
        if p and _proc_has(p, o.id):
            if launcher() == "systemd":
                run(["systemctl", "--user", "stop", f"agentj-{o.id}"], capture_output=True, timeout=30)
            try:
                os.killpg(p, signal.SIGKILL)
            except OSError:
                pass
        if not o.result():
            o.event("stopped", hard=True)
            o.write_result({"state": "stopped", "exit_code": None, "verdict": "aborted", "head": orders.parse_head(o.report()),
                            "failure_kind": "unknown", "ended": time.time()})
    return o.state()


def resume(o: Order, text: str | None = None, *, environ=None, st_root=None, **kw) -> str:
    if o.state() == "running" and alive(o):
        raise OrderError("still_running")
    if text:
        o.send(text)
    prev = o.result()
    if prev:
        hist = o.path / "history.jsonl"
        orders._append(hist, prev)
        (o.path / "result.json").unlink(missing_ok=True)
    o.set_control(None)
    o.event("resume", after=(prev or {}).get("verdict"))
    return launch(o, environ=environ, st_root=st_root, **kw)


def reconcile(st_root=None) -> list[str]:
    """After a serve restart (12.4): a 'running' order whose CEO is gone and left no result is failed (lost)."""
    lost = []
    for o in orders.all_orders(st_root):
        if o.state() == "running" and not alive(o) and not o.result():
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
