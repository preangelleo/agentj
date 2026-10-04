"""Relay exact-session Esc route; no permission or text injection."""
import json
import os
import shutil
import subprocess


HERDR_ENV = "AGENTJ_HERDR"
TIMEOUT_S = 4.0

SENT, IDLE, BLOCKED, NO_PANE, UNAVAILABLE = "sent", "idle", "blocked", "no_pane", "unavailable"
# Herdr's agent_status words: a key goes only to a pane it sees as working.
_QUIET = {"idle", "done"}
_BLOCKED = {"blocked"}


def herdr_bin() -> str | None:
    override = os.environ.get(HERDR_ENV)
    if override:
        return override
    found = shutil.which("herdr")
    if found:
        return found
    home = os.path.join(os.path.expanduser("~"), ".local", "bin", "herdr")
    return home if os.access(home, os.X_OK) else None


def _run(binary: str, *args: str) -> dict | None:
    try:
        p = subprocess.run([binary, *args], capture_output=True, text=True,
                           timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout or "null")
    except ValueError:
        return {}


def _pane_from_env(pid) -> str | None:
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as f:
            env = f.read().split(b"\0")
    except (OSError, TypeError, ValueError):
        return None
    for kv in env:
        if kv.startswith(b"HERDR_PANE_ID="):
            return kv.split(b"=", 1)[1].decode(errors="replace") or None
    return None


def _agents(binary) -> list[dict]:
    out = _run(binary, "agent", "list") or {}
    res = out.get("result") if isinstance(out, dict) else None
    agents = res.get("agents") if isinstance(res, dict) else None
    return agents if isinstance(agents, list) else []


def locate(session: dict, binary: str) -> tuple[str | None, str | None]:
    """(pane_id, herdr agent_status) for `session`, or (None, None)."""
    sid = session.get("sessionId")
    agents = _agents(binary)
    for a in agents:
        s = a.get("agent_session") or {}
        if sid and s.get("value") == sid and a.get("pane_id"):
            return a["pane_id"], a.get("agent_status")
    pane = _pane_from_env(session.get("pid"))
    if pane:
        for a in agents:
            if a.get("pane_id") == pane:
                return pane, a.get("agent_status")
        return pane, None
    return None, None


def interrupt(session: dict) -> str:
    """Send one Esc to `session`'s pane if (and only if) it is working.

    Returns sent|idle|blocked|no_pane|unavailable.
    """
    binary = herdr_bin()
    if not binary:
        return UNAVAILABLE
    pane, status = locate(session, binary)
    if not pane:
        return NO_PANE
    if status in _BLOCKED:
        return BLOCKED
    if status in _QUIET:
        return IDLE
    ok = _run(binary, "agent", "send-keys", pane, "esc") is not None
    return SENT if ok else UNAVAILABLE


