"""P179: a paired remote's two names — pure functions over one allowlist record (no state access; cloud code uses them too)."""
from __future__ import annotations


def _num(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


# P179: a remote's name has two sources. `self_name` = what the device said about itself when it paired (its browser / OS
# label, untrusted); `owner_name` = the label the owner gave it later (account page, or that remote itself from its
# message card). `name` stays the one effective label every older reader uses: owner_name when set, else self_name.
# Owner names are labels the owner chose — never identity proof.
def device_names(v) -> dict:
    """→ {"name", "self_name", "owner_name", "named_by": "owner"|"device", "owner_named_at"} for one allowlist record.
    A legacy record (before P179, not migrated yet) is read as device-named."""
    v = v if isinstance(v, dict) else {}
    owner = v.get("owner_name") if isinstance(v.get("owner_name"), str) and v.get("owner_name").strip() else None
    own = v.get("self_name") if isinstance(v.get("self_name"), str) else None
    if own is None and owner is None:
        own = str(v.get("name", ""))
    return {"name": owner or own or str(v.get("name", "")), "self_name": own, "owner_name": owner,
            "named_by": "owner" if owner else "device", "owner_named_at": _num(v.get("owner_named_at")) if owner else 0}


def platform_of(self_name: str | None) -> str:
    """The browser / OS part of a device's own label (「网页 · iOS Safari」 → 「iOS Safari」); "" when there is none."""
    s = (self_name or "").strip()
    for pre in ("网页 · ", "Web · "):
        if s.startswith(pre):
            return s[len(pre):].strip()
    return ""


def device_label(v, limit: int = 64) -> str:
    """Who sent a message, as pages and the Agent see it: 「张伟（老板）· iOS Safari」 when the owner named the remote
    (the device's own browser / OS part after it), else the device's own label exactly as before P179."""
    n = device_names(v)
    if n["named_by"] != "owner":
        return n["name"][:limit]
    plat = platform_of(n["self_name"])
    out = n["owner_name"] + (" · " + plat if plat else "")
    return out if len(out) <= limit else n["owner_name"][:limit]
