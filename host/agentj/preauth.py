"""P539 (#300): a narrow, revocable pre-authorization — 「<这台遥控器> 批准过：Computer Use 免问」.

The owner opts in on one card: an allow, signed by a paired remote, that also passed the passkey (UV), whose signed
allow_add line is exactly the offer `preauth:<action>` this host made (the P296 mechanism). From then on a request of that
canonical action runs without a card, only while ALL of these still hold: the granting remote is still on the allowlist
with the same approval key (so unpairing, a passkey restore / re-pairing — a new device id — or a new key ends it), the
emergency stop is off (engaging it revokes every grant), the approval level is not "safe", and the request is the
owner's own turn (never a friend / group / channel turn). Every use is logged (`preauth_use`).

Grantable actions are a closed constant: {"computer.control"} only. Red lines (secrets, payment, admin / sudo, device
pairing, deletion, mail, publishing) are never grantable — no model, page or file can add one. The grant names the remote
that made it; revoking (`preauth_revoke` from any paired remote, narrowing needs no passkey) drops it. Stored host-side in
`<state>/preauth.json` (0600, ≤ MAX grants): action, device id, approval key, time and the approving card id — no secret."""
from __future__ import annotations

import json
import time

from . import wire

GRANTABLE = frozenset({"computer.control"})
PREFIX = "preauth:"
MAX = 16
FILE = "preauth.json"


def offer(action) -> str | None:
    """The allow_offer pick a card for `action` carries, or None (not grantable)."""
    return PREFIX + action if isinstance(action, str) and action in GRANTABLE else None


def picked(picks) -> list[str]:
    """The grantable actions named in a signed allow_add list (anything else ignored)."""
    out = []
    for x in picks or []:
        if isinstance(x, str) and x.startswith(PREFIX) and x[len(PREFIX):] in GRANTABLE and x[len(PREFIX):] not in out:
            out.append(x[len(PREFIX):])
    return out


def strip(picks) -> list:
    """allow_add without the preauth picks (what the caller — e.g. `agentj mail send` — gets back)."""
    return [x for x in picks or [] if not (isinstance(x, str) and x.startswith(PREFIX))]


MCP_OPS = {"kind": "mcp_ops"}


def binding(action: str, tool, bind=None) -> dict | None:
    """The binding a grant must carry to cover this concrete request, or None (no grant can).
    - bind = a trusted Codex plugin binding (agent_codex, from Codex's own mcpToolCall item): the plugin's script tools
      → that same {"kind": "codex_plugin", "id": <pluginId>, "server": "cua_repl"}.
    - no bind: only a KNOWN Computer Use operation (policy_registry.COMPUTER_USE_GRANT_OPS) on an allowlisted Computer Use
      server → {"kind": "mcp_ops"}; an unknown operation on those servers, a look-alike `left_click` on any other server, or
      a bare canonical name keeps its card (M1, P539 review).
    A grant of one kind never covers the other."""
    from . import policy_registry
    if action != "computer.control":
        return None
    if bind is not None:
        return dict(bind) if policy_registry.bound_action(tool, bind) == action else None
    return dict(MCP_OPS) if policy_registry.computer_use_grant_op(tool) else None


def covers(action: str, tool, bind=None) -> bool:
    return binding(action, tool, bind) is not None


def _bind_ok(b) -> bool:
    from . import policy_registry
    if b == MCP_OPS:
        return True
    return policy_registry.codex_plugin_bind(b.get("id"), b.get("server")) == b if isinstance(b, dict) else False


TYPING = frozenset({"type_text", "press_key", "hold_key", "type", "key"})


def phone_line(tool, tool_input, lang: str = "zh") -> str:
    """The quiet line a preauth use shows on the phones / activity (L2): the operation and its numbers (click
    coordinates) only — never typed text: a typing / key operation shows how many characters, not which."""
    from .text import pick
    op = tool.rsplit("__", 1)[-1] if isinstance(tool, str) else "?"
    nums, chars = [], 0
    for k, v in (tool_input if isinstance(tool_input, dict) else {}).items():
        if isinstance(v, bool) or not isinstance(k, str) or len(k) > 32:
            continue
        if isinstance(v, (int, float)):
            nums.append(f"{k}={v:g}")
        elif isinstance(v, list) and 0 < len(v) <= 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            nums.append(k + "=(" + ",".join(f"{x:g}" for x in v) + ")")
        elif isinstance(v, str):
            chars += len(v)
        elif isinstance(v, list):
            chars += sum(len(x) for x in v if isinstance(x, str))
    line = op[:64] + (" " + " ".join(nums[:6]) if nums else "")
    if op in TYPING or chars:
        line += pick(lang, f"（输入了 {chars} 个字）", f" (typed {chars} characters)") if op in TYPING else \
            pick(lang, f"（另有 {chars} 个字的文字参数，未显示）", f" ({chars} characters of text not shown)")
    return line[:200]


def _path(st):
    return st.root / FILE


def _read(st) -> list[dict]:
    try:
        d = json.loads(_path(st).read_text())
    except (FileNotFoundError, ValueError, UnicodeDecodeError, OSError):
        return []                                        # damaged → no grant (fails closed: cards again)
    items = d.get("items") if isinstance(d, dict) else None
    out = []
    for g in items if isinstance(items, list) else []:
        if (isinstance(g, dict) and g.get("action") in GRANTABLE and isinstance(g.get("device"), str)
                and isinstance(g.get("sk"), str) and isinstance(g.get("at"), int) and not isinstance(g.get("at"), bool)):
            b = g.get("bind", MCP_OPS)                   # a grant from before bindings: the MCP-name kind
            if _bind_ok(b):
                out.append({**{k: g[k] for k in ("action", "device", "sk", "at", "rid") if k in g}, "bind": dict(b)})
    return out[-MAX:]


def _write(st, items: list[dict]) -> None:
    st.write_private(_path(st), json.dumps({"v": 1, "items": items[-MAX:]}).encode())


def load(st) -> list[dict]:
    return _read(st)


def add(st, action: str, device: str, sign_pub: bytes, rid: str | None = None, bind: dict | None = None) -> bool:
    """Record the owner's grant, bound to what the card covered (`binding()`; default the MCP-name kind) — replacing the
    same remote's earlier one for that action and binding. Only a grantable action and a known binding."""
    bind = dict(MCP_OPS) if bind is None else dict(bind)
    if action not in GRANTABLE or not device or not sign_pub or not _bind_ok(bind):
        return False
    with st.file_lock("preauth"):
        items = [g for g in _read(st) if not (g["action"] == action and g["device"] == device and g["bind"] == bind)]
        items.append({"action": action, "device": device, "sk": wire.b64u(sign_pub), "at": int(time.time()),
                      **({"rid": rid} if isinstance(rid, str) else {}), "bind": bind})
        _write(st, items)
    return True


def revoke(st, *, action: str | None = None, device: str | None = None) -> int:
    """Drop grants (all, one action, one remote's, or both filters). → how many went."""
    with st.file_lock("preauth"):
        items = _read(st)
        keep = [g for g in items if not ((action is None or g["action"] == action) and (device is None or g["device"] == device))]
        if len(keep) != len(items) or (not keep and _path(st).exists()):
            _write(st, keep)
    return len(items) - len(keep)


def _alive(st, g: dict) -> bool:
    sk = st.sign_key(g["device"])
    return bool(sk) and wire.b64u(sk) == g["sk"]


def live(st, action: str, bind: dict | None = None) -> dict | None:
    """A grant of `action` with exactly this binding (default the MCP-name kind) whose remote is still paired with the
    same approval key, or None. Dead ones are dropped."""
    bind = dict(MCP_OPS) if bind is None else bind
    if action not in GRANTABLE:
        return None
    items = _read(st)
    dead = [g for g in items if not _alive(st, g)]
    if dead:
        with st.file_lock("preauth"):
            _write(st, [g for g in _read(st) if _alive(st, g)])
    return next((g for g in items if g["action"] == action and g["bind"] == bind and g not in dead), None)


def items(st, label=None) -> list[dict]:
    """What the phone shows: [{action, device, name, at, via}] of live grants (never the key); via = the binding kind
    ("mcp_ops" | "codex_plugin")."""
    out = []
    for g in _read(st):
        if _alive(st, g):
            out.append({"action": g["action"], "device": g["device"], "name": (label(g["device"]) if label else "") or g["device"],
                        "at": g["at"], "via": g["bind"]["kind"]})
    return out
