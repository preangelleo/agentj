"""P326 (Mac 10-15): the switch gate's start probe for OpenCode — the start the session will make, before anything changes.

The login probe (`opencode run` in a temporary folder) proves the model answers; it does not prove that the session Agent J
opens afterwards comes up. On the Mac the probe passed and the real shared session never reached an OpenCode server (a
Claude / Codex pin sent it to a "your own server" branch with no port): every turn ended in a second, the switch rolled back
three minutes later. So, like P312's Codex probe, the job starts what serve will start:
- our own server (independent mode, or shared mode without an owner port): `opencode serve` on a free loopback port with a
  fresh password, in the Agent's folder, the service's environment and the same isolation — up when it prints its address
  and answers `GET /path` (v1) or `GET /api/info` (v2); then it is ended. No session, no turn, nothing kept;
- the owner's own server (`agent.shared_opencode_port`): it answers on that port with the password the service holds, and
  the pinned session is there.
{"secs"[, "reason"]}; None = not measured (no folder, isolation unavailable here — the session reports that itself).
"""
from __future__ import annotations

from . import resource_policy

import asyncio
import contextlib
import os
import re
import secrets
import selectors
import signal
import subprocess
import time

OC_SESSION = re.compile(r"ses[A-Za-z0-9_-]+")
_LISTEN = re.compile(rb"listening on http://127\.0\.0\.1:(\d+)")


def _prefs():
    from . import preferences
    try:
        return preferences.validate(preferences.read()[1])
    except Exception:  # noqa: BLE001 — the defaults, like the service with an unreadable file
        return preferences.defaults()


async def _answers(client, pin: str | None = None) -> str | None:
    """None = the server is usable; else a reason code."""
    from .agent_opencode import HTTPError
    try:
        st, info = await client.request("GET", "/api/info", timeout=10)
        if st == 401:
            return "owner_password"
        if st == 200 and isinstance(info, dict) and str(info.get("version", "")).lstrip("v").startswith("2"):
            return None                                   # v2: attached by shared_opencode2 (its own checks)
        if pin is None:
            st, path = await client.request("GET", "/path", timeout=10)
            return None if st == 200 and isinstance(path, dict) else "start_failed"
        if not OC_SESSION.fullmatch(pin):
            return "owner_session"
        st, s = await client.request("GET", f"/session/{pin}", timeout=10)
        if st == 401:
            return "owner_password"
        return None if st == 200 and isinstance(s, dict) and s.get("id") == pin else "owner_session"
    except (OSError, HTTPError, ValueError, asyncio.TimeoutError):
        return "owner_unreachable" if pin is not None else "start_failed"


def _owner(cfg: dict, port) -> dict:
    from .agent_opencode import Client
    if type(port) is not int or not 1 <= port <= 65535:
        return {"secs": 0, "reason": "owner_unreachable"}
    t0 = time.monotonic()
    client = Client(port, os.environ.get("OPENCODE_SERVER_PASSWORD", ""), os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"))
    why = asyncio.run(_answers(client, str(cfg.get("shared_session_id") or "")))
    return {"secs": round(time.monotonic() - t0, 1), **({"reason": why} if why else {})}


def start_probe(st, cfg: dict, limit: float = 150, *, clock=time.monotonic) -> dict | None:
    d = str(cfg.get("dir") or "")
    if not os.path.isdir(d):
        return None
    shared = cfg.get("session_mode") == "shared"
    port = cfg.get("shared_opencode_port")
    if shared and port:
        return _owner(cfg, port)
    from . import fence, proxy
    from .agent import _bin
    from .agent_opencode import Client, free_port, opencode_env
    from .provider_runtime import fresh_environment
    exe = _bin("AGENTJ_OPENCODE_BIN", "opencode")
    if not exe:
        return {"secs": 0, "reason": "start_failed"}
    port, password = free_port(), secrets.token_urlsafe(32)
    argv = [exe, "serve", "--hostname", "127.0.0.1", "--port", str(port)]
    try:
        env = opencode_env(proxy.environment(fresh_environment(os.environ), _prefs()), password)
        if cfg.get("fence", True):
            if fence.problem(st, d):
                return None
            argv = fence.wrap(st, argv, d, allow_docker=cfg.get("docker", False))
            env = fence.launch_environment(env, cfg.get("docker", False), "opencode")
        p = subprocess.Popen(resource_policy.work_argv(argv), cwd=d, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, ValueError):
        return {"secs": 0, "reason": "start_failed"}
    t0, buf, out = clock(), b"", {}
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout, selectors.EVENT_READ)
            listening = None
            while listening is None:
                left = t0 + limit - clock()
                if left <= 0:
                    out["reason"] = "start_timeout"
                    return out
                if sel.select(min(1.0, left)):
                    raw = os.read(p.stdout.fileno(), 65536)
                    if not raw:
                        out["reason"] = "start_failed"
                        return out
                    buf = (buf + raw)[-8192:]
                    m = _LISTEN.search(buf)
                    if m:
                        listening = int(m.group(1))
        if listening != port:
            out["reason"] = "start_failed"
            return out
        why = asyncio.run(_answers(Client(port, password)))
        if why:
            out["reason"] = why
        return out
    finally:
        out["secs"] = round(clock() - t0, 1)
        with contextlib.suppress(OSError):
            os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        p.stdout.close()
