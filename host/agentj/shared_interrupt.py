"""Relay exact-session Esc route; no permission or text injection."""
import json
import os
import shutil
import subprocess
import sys


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
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform == "darwin":
        from .term_mac import LIB
        got = LIB.args(pid)  # kernel separates argv and env; never parse ps's combined display
        env = got[1] if got else []
    else:
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().decode(errors="replace").split("\0")
        except OSError:
            return None
    panes = [kv.partition("=")[2] for kv in env if kv.startswith("HERDR_PANE_ID=")]
    return panes[0] if len(panes) == 1 and panes[0] else None


def _agents(binary) -> list[dict]:
    out = _run(binary, "agent", "list") or {}
    res = out.get("result") if isinstance(out, dict) else None
    agents = res.get("agents") if isinstance(res, dict) else None
    return agents if isinstance(agents, list) else []


def locate(session: dict, binary: str) -> tuple[str | None, str | None]:
    """(pane_id, herdr agent_status) for `session`, or (None, None)."""
    sid = session.get("sessionId")
    agents = [a for a in _agents(binary) if isinstance(a, dict) and a.get("pane_id")]
    matches = [a for a in agents if sid and isinstance(a.get("agent_session"), dict)
               and a["agent_session"].get("value") == sid]
    if len(matches) > 1:
        return None, None
    pane = _pane_from_env(session.get("pid"))
    if pane:
        rows = [a for a in agents if a["pane_id"] == pane]
        if len(rows) != 1 or (matches and matches[0]["pane_id"] != pane):
            return None, None
        return pane, rows[0].get("agent_status")
    if matches:
        return matches[0]["pane_id"], matches[0].get("agent_status")
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
    if status not in {"working", "busy", "running"}:
        return UNAVAILABLE
    ok = _run(binary, "agent", "send-keys", pane, "esc") is not None
    return SENT if ok else UNAVAILABLE


