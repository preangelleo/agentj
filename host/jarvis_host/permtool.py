"""The agent's permission prompt tool (PROTOCOL §8): a minimal stdio MCP server that Claude Code starts itself
(`--permission-prompt-tool mcp__agentjarvis__approve`). Every call is forwarded to `jarvis serve` over its local socket
(agentperm/perm.sock, 0600) and blocks until the phone answers or serve's deadline denies. Fails closed: anything unexpected
= deny.

L2 (G-A24): the tool *claims* the socket the moment Claude Code starts it — before Claude Code has read the first message,
so before the model can run anything — with the one-time token serve put in the agent's environment, and then keeps that one
connection for every request. serve accepts exactly one claim per agent start, so the token the agent can read in its own
environment is already spent; a second connection is refused and logged. The tool also makes itself non-dumpable, so other
processes of the same user cannot read its memory or borrow its socket.

Standard library only; started as `python -m jarvis_host.permtool` with AGENTJARVIS_PERM_SOCK / AGENTJARVIS_PERM_TOKEN
inherited from the agent process (never on a command line).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading

TOOL = "approve"
MAX_LINE = 8 * 1024 * 1024
DENY_NO_SERVE = "jarvis serve 没在运行或没有回应：默认拒绝。"


class Link:
    """The one claimed connection to serve; requests are multiplexed by id, answers routed back to the waiting thread."""

    def __init__(self):
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.waiting: dict[int, list] = {}    # id → [Event, answer]
        self.next_id = 0
        self.dead = False

    def claim(self) -> bool:
        path, token = os.environ.get("AGENTJARVIS_PERM_SOCK", ""), os.environ.get("AGENTJARVIS_PERM_TOKEN", "")
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(path)
            s.sendall((json.dumps({"t": "claim", "token": token}) + "\n").encode())
            f = s.makefile("rb")
            ok = json.loads(f.readline(MAX_LINE) or b"{}")
            if ok.get("t") != "claimed":
                s.close()
                return self._die()
            s.settimeout(None)
        except (OSError, ValueError):
            return self._die()
        self.sock = s
        threading.Thread(target=self._read, args=(f,), daemon=True).start()
        return True

    def _die(self) -> bool:
        self.dead = True
        with self.lock:
            for slot in self.waiting.values():
                slot[0].set()
        return False

    def _read(self, f) -> None:
        try:
            while line := f.readline(MAX_LINE):
                m = json.loads(line)
                if isinstance(m, dict) and m.get("t") == "answer":
                    with self.lock:
                        slot = self.waiting.get(m.get("id"))
                    if slot:
                        slot[1] = m
                        slot[0].set()
        except (OSError, ValueError):
            pass
        self._die()

    def send(self, obj: dict) -> None:
        with self.lock:
            if self.sock and not self.dead:
                self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode())

    def ask(self, args: dict, mcp_id) -> dict:
        try:
            limit = float(os.environ.get("AGENTJARVIS_PERM_WAIT", "200"))
        except ValueError:
            limit = 200.0
        if self.dead or not self.sock:
            return {"behavior": "deny", "message": DENY_NO_SERVE}
        slot = [threading.Event(), None]
        with self.lock:
            self.next_id += 1
            rid = self.next_id
            self.waiting[rid] = slot
            _calls[mcp_id] = rid
        try:
            self.send({"t": "ask", "id": rid, "tool": args.get("tool_name"), "input": args.get("input"),
                       "tool_use_id": args.get("tool_use_id")})
            slot[0].wait(limit)               # serve answers before this (its own deadline is shorter); this only guards a hang
        except OSError:
            pass
        finally:
            with self.lock:
                self.waiting.pop(rid, None)
                _calls.pop(mcp_id, None)
        ans = slot[1]
        if isinstance(ans, dict) and ans.get("behavior") == "allow" and isinstance(ans.get("updatedInput"), dict):
            return {"behavior": "allow", "updatedInput": ans["updatedInput"]}
        if ans is None:
            return {"behavior": "deny", "message": DENY_NO_SERVE}
        msg = ans.get("message") if isinstance(ans.get("message"), str) else "已拒绝。"
        return {"behavior": "deny", "message": msg}

    def cancel(self, mcp_id) -> None:
        with self.lock:
            rid = _calls.get(mcp_id)
        if rid is not None:
            try:
                self.send({"t": "cancel", "id": rid})
            except OSError:
                pass


LINK = Link()
_calls: dict = {}   # MCP request id → our request id (for notifications/cancelled)


def handle(m: dict) -> dict | None:
    if "id" not in m:                      # notifications (initialized, cancelled …) need no answer
        if m.get("method") == "notifications/cancelled":
            LINK.cancel((m.get("params") or {}).get("requestId"))
        return None
    meth, mid = m.get("method"), m["id"]
    if meth == "initialize":
        pv = (m.get("params") or {}).get("protocolVersion") or "2025-06-18"
        res = {"protocolVersion": pv, "capabilities": {"tools": {}}, "serverInfo": {"name": "agentjarvis", "version": "1"}}
    elif meth == "tools/list":
        res = {"tools": [{"name": TOOL, "description": "agentjarvis: ask the paired phone to approve a tool call",
                          "inputSchema": {"type": "object", "properties": {"tool_name": {"type": "string"},
                                                                          "input": {"type": "object"},
                                                                          "tool_use_id": {"type": "string"}},
                                          "required": ["tool_name", "input"]}}]}
    elif meth == "tools/call":
        params = m.get("params") or {}
        args = params.get("arguments") if params.get("name") == TOOL else None
        out = LINK.ask(args, mid) if isinstance(args, dict) else {"behavior": "deny", "message": "unknown tool"}
        res = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
    elif meth == "ping":
        res = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


_out = threading.Lock()


def _answer(m: dict) -> None:
    out = handle(m)
    if out is not None:
        with _out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    from .fence import no_dump
    no_dump()
    LINK.claim()                          # first thing, before Claude Code sends anything (and before the model runs)
    for k in ("AGENTJARVIS_PERM_TOKEN",):
        os.environ.pop(k, None)
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict):
            continue
        if m.get("method") == "tools/call":   # parallel tool calls → parallel cards; each blocks only its own thread
            threading.Thread(target=_answer, args=(m,), daemon=True).start()
        else:
            _answer(m)


if __name__ == "__main__":
    main()
