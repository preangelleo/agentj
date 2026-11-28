"""Host-owned transition checkpoints for phone work that never reached an executor."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time


def switch_receipt(st):
    from . import switch_harness as switch
    try:
        rec = json.loads((st.root / switch.RECEIPT).read_bytes())
        if (type(rec["at"]) in (int, float) and 0 <= time.time() - rec["at"] < switch.JOB_MAX_AGE
                and isinstance(rec["job"], str) and switch._JOB.fullmatch(rec["job"])
                and rec["from"] in switch.KINDS and rec["target"] in switch.KINDS):
            return rec
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {}


def workspace(host):
    cfg = getattr(host, "agent_cfg", None) or getattr(getattr(host, "agent", None), "cfg", None) or {}
    try:
        path = Path(cfg["dir"]).resolve(strict=True)
        info = path.stat()
    except (OSError, TypeError, ValueError, KeyError, RuntimeError):
        return None
    if stat.S_ISDIR(info.st_mode):
        return [str(path), info.st_dev, info.st_ino, cfg.get("session_mode", "independent"),
                cfg.get("fence", True), cfg.get("high_risk_warnings", True)]


def _repair(st):
    from . import rescue_repair
    try:
        rec = json.loads((st.root / rescue_repair.MARKER).read_bytes())
        if (rec["flow"] in ("restart", "repair", "deep_repair") and type(rec["at"]) is int
                and 0 <= time.time() - rec["at"] <= rescue_repair.MARKER_TTL and rec["pid"] == os.getpid()):
            return rec
    except (KeyError, TypeError, ValueError, OSError):
        pass
    return {}


def transition(host):
    """Current transition observed by serve. A stale detached job is never a release signal."""
    switch = switch_receipt(host.st)
    if switch.get("status") in ("waiting", "switching"):
        return ["switch", switch["job"], switch["from"], switch["target"]]
    agent = getattr(host, "agent", None)
    if agent is not None and getattr(agent, "status", None) == "compacting":
        sid = host.st.agent_session(agent.kind)
        if isinstance(sid, str) and sid:
            return ["compact", agent.kind, sid]
    if _repair(host.st):
        return ["repair", getattr(agent, "kind", None)]
    return None


def permits(host, checkpoint):
    """Recheck the selected work folder and exact switch/native session before delivery."""
    if not isinstance(checkpoint, dict) or not checkpoint.get("workspace") or workspace(host) != checkpoint["workspace"]:
        return False
    scope = checkpoint.get("transition")
    if not isinstance(scope, list) or not scope:
        return False
    kind = getattr(getattr(host, "agent", None), "kind", None)
    if scope[0] == "switch" and len(scope) == 4:
        rec = switch_receipt(host.st)
        expected = scope[3] if rec.get("status") == "ok" else scope[2] if rec.get("status") == "rolled_back" else None
        return (expected is not None and kind == expected and rec["job"] == scope[1]
                and rec["from"] == scope[2] and rec["target"] == scope[3])
    if scope[0] == "compact" and len(scope) == 3:
        return kind == scope[1] and host.st.agent_session(kind) == scope[2]
    return len(scope) == 2 and scope[0] in ("repair", "upgrade") and kind == scope[1] and bool(kind)
