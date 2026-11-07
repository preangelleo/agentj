"""0.26.9 (#468, ADR-0269-assist): the pinned official remote-assist keys (public halves only).

`assets/assist_keys.json` ships inside the signed host package; nothing here reads the network. An entry is usable only with
both public keys (32 bytes each, b64url) and `not_after` in the future. The shipped file contains only the public halves of the explicitly authorized official host.
An empty/expired pin set disables remote assistance; no network-provided root is adopted.

Test-only override: `AGENTJ_ASSIST_KEYS_TEST=<file>` replaces the list ONLY when all hold — `AGENTJ_TEST=1` (tests/_hermetic.py)
AND this module runs from a source checkout (`host/pyproject.toml` beside the package; an installed wheel never has it).
A production install ignores the variable whatever its value (test_p0269_assist proves it on a wheel-shaped copy).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

ASSET = Path(__file__).with_name("assets") / "assist_keys.json"
TEST_ENV = "AGENTJ_ASSIST_KEYS_TEST"
_ID = re.compile(r"[A-Za-z0-9._-]{1,40}")


def source_checkout() -> bool:
    return (Path(__file__).resolve().parent.parent / "pyproject.toml").is_file()


def override_path() -> Path | None:
    """The test file, or None (always None outside a test run in a source checkout)."""
    p = os.environ.get(TEST_ENV)
    if not p or os.environ.get("AGENTJ_TEST") != "1" or not source_checkout():
        return None
    return Path(p)


def _b32(v) -> bytes | None:
    from .wire import unb64u
    if not isinstance(v, str) or len(v) > 64:
        return None
    try:
        b = unb64u(v)
    except ValueError:
        return None
    return b if len(b) == 32 else None


def load(now: float | None = None) -> list[dict]:
    """Usable keys: [{"id", "x25519": bytes, "ed25519": bytes, "not_after": int}] (expired / placeholder entries dropped)."""
    now = time.time() if now is None else now
    p = override_path() or ASSET
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for e in (d.get("keys") if isinstance(d, dict) and isinstance(d.get("keys"), list) else [])[:8]:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not _ID.fullmatch(e["id"]):
            continue
        x, ed, na = _b32(e.get("x25519")), _b32(e.get("ed25519")), e.get("not_after")
        if x and ed and type(na) is int and na > now:
            out.append({"id": e["id"], "x25519": x, "ed25519": ed, "not_after": na})
    return out


def current(now: float | None = None) -> dict | None:
    """The key a new grant names: the usable one that lasts longest."""
    keys = load(now)
    return max(keys, key=lambda k: k["not_after"]) if keys else None


def by_id(kid: str, now: float | None = None) -> dict | None:
    return next((k for k in load(now) if k["id"] == kid), None)


def placeholder() -> bool:
    """Whether the shipped asset has no validly encoded public pin (an empty build stays off)."""
    try:
        d = json.loads(ASSET.read_text(encoding="utf-8"))
        return not any(_b32(e.get("x25519")) for e in d.get("keys", []) if isinstance(e, dict))
    except (OSError, ValueError, AttributeError):
        return True
