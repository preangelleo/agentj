"""#481: the owner-facing release note shipped with each version — `agentj news --version [X]`.

Data: agentj/assets/news/<version>.json = {version, items:[{zh:{what, action}, en:{what, action}}]} (built by p44-release).
Plain words in the owner's language; a missing or broken file → one honest line, never a traceback. Advice to the Agent:
read it after an upgrade or when the owner asks what is new, quote it, never guess and never name internal files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DIR = Path(__file__).with_name("assets") / "news"
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}(?:[a-z]{1,8}\d{0,3})?$")   # 0.26.9 · 0.26.8a2 — no path pieces
MAX_BYTES = 256 * 1024
MAX_ITEMS = 60
TEXT_MAX = 600


def installed() -> str:
    from . import __version__
    return __version__


def _clean(v) -> str:
    return " ".join(v.split())[:TEXT_MAX] if isinstance(v, str) else ""


def load(version: str | None = None, base: Path | None = None) -> dict:
    """→ {ok, version, items:[{zh:{what,action}, en:{what,action}}]} | {ok: False, version, why}."""
    version = version or installed()
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        return {"ok": False, "version": str(version)[:40], "why": "bad_version"}
    p = (base or DIR) / f"{version}.json"
    try:
        raw = p.read_bytes() if p.is_file() and not p.is_symlink() else None
    except OSError:
        raw = None
    if raw is None:
        return {"ok": False, "version": version, "why": "missing"}
    try:
        d = json.loads(raw[:MAX_BYTES + 1]) if len(raw) <= MAX_BYTES else None
    except ValueError:
        d = None
    items = []
    for it in (d.get("items") if isinstance(d, dict) and isinstance(d.get("items"), list) else [])[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        row = {L: {"what": _clean((it.get(L) or {}).get("what")), "action": _clean((it.get(L) or {}).get("action"))}
               for L in ("zh", "en") if isinstance(it.get(L), dict)}
        if any(r["what"] for r in row.values()):
            items.append(row)
    if not isinstance(d, dict) or d.get("version") != version or not items:
        return {"ok": False, "version": version, "why": "unreadable"}
    return {"ok": True, "version": version, "items": items}


def render(res: dict, lang: str = "zh") -> str:
    en = lang == "en"
    v = res.get("version", "")
    if not res.get("ok"):
        if res.get("why") == "bad_version":
            return f"“{v}” is not a version number (like 0.26.9)." if en else f"「{v}」不是版本号（例如 0.26.9）。"
        return (f"This host has no release note for Agent J {v}; say so plainly rather than guessing." if en else
                f"这台主机上没有 Agent J {v} 的更新说明；如实告诉主人，不要猜。")
    out = [f"Agent J {v} — what's new for you:" if en else f"Agent J {v} 更新说明："]
    L, other = ("en", "zh") if en else ("zh", "en")
    for i, it in enumerate(res["items"], 1):
        r = it.get(L) if (it.get(L) or {}).get("what") else it.get(other)
        line = f"{i}. {r['what']}"
        if r.get("action"):
            line += (f" — what you do: {r['action']}" if en else f"（你要做：{r['action']}）")
        out.append(line)
    return "\n".join(out)
