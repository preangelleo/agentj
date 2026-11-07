"""serve's side of the Agent J Harness requests that arrive on elevate.sock (the one host socket a fenced Agent sees).

    {"t": "core_mcp", "op": "list" | "add" | "remove", "name"?, "spec"?}     P431 C8: the owner's MCP server list
    {"t": "core_order", "args": {…the dispatch tool's arguments…}}           P431 C17: `agentj order` inside the fence

The state directory is invisible inside the fence, so these are the only ways a fenced Agent reaches the server list or the
orders. Adding an MCP server would let code run as a tool, so `add` waits for the owner's card (never a batch grant);
`list` / `remove` only show or take away. Fail closed: off unless AGENTJ_CORE=1, refused while 「全部停下」 is on, refused for
a Telegram group member's turn (like every other card).
"""
from __future__ import annotations

import asyncio

from . import enabled


def _blocked(host) -> dict | None:
    if not enabled():
        return {"result": "off"}
    if host.stopped():
        return {"result": "stopped"}
    try:
        from .. import provenance
        ag = getattr(host, "agent", None)
        if provenance.untrusted_turn(ag) and not provenance.delegated_turn(ag):
            return {"result": "group_source"}
    except Exception:  # noqa: BLE001 — cannot tell who asks: refuse
        return {"result": "refused", "why": "provenance"}
    return None


async def handle(host, req: dict, *, peer=None) -> dict:
    b = _blocked(host)
    if b:
        return b
    if req.get("t") == "core_mcp":
        return await _mcp(host, req)
    if req.get("t") == "core_order":
        return await _order(host, req, peer=peer)
    return {"result": "refused", "why": "shape"}


async def _mcp(host, req: dict) -> dict:
    from . import mcp
    root, op = host.st.root, req.get("op")
    if op == "list":
        servers = mcp.load(root)
        return {"result": "ok", "text": "\n".join(mcp.describe(n, s) for n, s in sorted(servers.items())) or "(none)"}
    name = req.get("name")
    if not isinstance(name, str) or not mcp.NAME_RE.match(name):
        return {"result": "refused", "why": "name"}
    if op == "remove":
        ok = await asyncio.to_thread(mcp.remove, root, name)
        host.st.log("core_mcp", op="remove", result="ok" if ok else "none")
        return {"result": "ok" if ok else "none"}
    if op != "add":
        return {"result": "refused", "why": "op"}
    spec = req.get("spec")
    why = mcp.problems(spec)
    if why:
        return {"result": "refused", "why": "spec", "detail": "; ".join(why)[:300]}
    card = {"server": name, "what": mcp.describe(name, spec),
            "runs": "inside the Agent's isolation, without any key" if spec["type"] == "stdio" else
                    "called from Agent J; the key (by name) only in its header",
            "allow_without_card": spec.get("allow") or []}
    ans = await host.ask("agentj core mcp add", card, None, batch=False)
    if not (isinstance(ans, dict) and ans.get("behavior") == "allow"):
        host.st.log("core_mcp", op="add", result="denied")
        return {"result": "denied"}
    await asyncio.to_thread(mcp.add, root, name, spec)
    host.st.log("core_mcp", op="add", result="ok", type=spec["type"])
    return {"result": "ok"}


async def _order(host, req: dict, *, peer=None) -> dict:
    from .order_host import handle
    return await handle(host, req, peer=peer)
