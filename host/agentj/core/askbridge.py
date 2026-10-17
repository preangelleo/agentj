"""The approval line of a CEO process to the owner's phone (AGENTJ-CORE-SPEC 12.7.1): serve listens on
`<state>/orders/ask.sock` (0600; the state directory is invisible inside the fence, so no fenced process can even reach it),
a CEO connects with its order id and the one-time token the dispatcher wrote into the order directory (`.ask_token`, 0600).

The ORIGIN CHAIN on the card is built here, from order.json — 「<parent> › <CEO>（<project>）」 — never from what the CEO
sends; the CEO only adds the sub-agent suffix its own host code generated (`sub-agent「…」#n`, cleaned and capped).
Batch grants never apply (D16: an "allow all" of one session does not reach an order). Fail closed: a bad hello, a finished
order, a dropped connection → deny.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import socket
from pathlib import Path

from .. import bg
from . import orders

SOCK = "ask.sock"
WHO_MAX = 80


def sock_path(st_root=None) -> Path:
    return orders.root(st_root) / SOCK


def origin(meta: dict, who: str | None) -> str:
    par = meta.get("parent") or {}
    head = {"owner_turn": "主 Agent", "cli": "主 Agent", "task": "定时任务", "goal": "目标模式", "bot": "机器人"}.get(
        par.get("kind") or meta.get("origin"), "主 Agent")
    chain = f"{head} › {meta.get('ceo', 'CEO')}（{meta.get('project_name', '?')}）· {meta.get('id')}"
    if who:
        w = re.sub(r"[\x00-\x1f]", " ", str(who))
        tail = w.split("›")[-1].strip()[:WHO_MAX]
        if tail:
            chain += f" › {tail}"
    return chain


def _peer_pid(w) -> int | None:
    """Linux SO_PEERCRED: the pid of the process on the other end (None where the OS has no such call)."""
    try:
        import struct
        s = w.get_extra_info("socket")
        cred = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", cred)[0]
    except (AttributeError, OSError, TypeError):
        return None


class Server:
    """In serve: ask(tool, tool_input, gone=None, *, origin, batch) is Host.ask."""

    def __init__(self, ask, st_root=None):
        self.ask, self.st_root, self.srv = ask, st_root, None

    async def start(self):
        p = sock_path(self.st_root)
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
        old = os.umask(0o177)
        try:
            self.srv = await asyncio.start_unix_server(self.on_conn, path=str(p))
        finally:
            os.umask(old)
        os.chmod(p, 0o600)
        return self

    async def close(self):
        if self.srv:
            self.srv.close()
            with contextlib.suppress(Exception):
                await self.srv.wait_closed()

    async def on_conn(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        gone: dict[int, asyncio.Future] = {}
        try:
            hello = json.loads((await r.readline()) or b"null")
            meta = self._admit(hello, _peer_pid(w))
            if meta is None:
                w.write(b'{"t":"refused"}\n')
                return
            w.write(b'{"t":"ok"}\n')
            await w.drain()
            while line := await r.readline():
                m = json.loads(line)
                if not isinstance(m, dict) or type(m.get("id")) is not int:
                    continue
                if m.get("t") == "ask" and m["id"] not in gone:
                    gone[m["id"]] = asyncio.get_running_loop().create_future()
                    bg.spawn(self._one(w, m, meta, gone), "order_ask")
        except (ValueError, ConnectionError, asyncio.LimitOverrunError):
            pass
        finally:
            for f in gone.values():
                if not f.done():
                    f.set_result(True)
            with contextlib.suppress(Exception):
                w.close()

    def _admit(self, hello, peer: int | None = None) -> dict | None:
        """Token from the order directory AND (where the OS says who connects) the connecting process is the order's CEO —
        the pid it wrote itself (P359 review L6: a copied token alone is not enough)."""
        if not isinstance(hello, dict) or hello.get("t") != "hello":
            return None
        try:
            o = orders.get(str(hello.get("order")), self.st_root)
            tok = (o.path / ".ask_token").read_text().strip()
            pid = int((o.path / "pid").read_text().strip())
        except (orders.OrderError, OSError, ValueError):
            return None
        if not tok or not hmac.compare_digest(str(hello.get("token", "")).encode(), tok.encode()) or o.result():
            return None
        if peer is not None and peer != pid:
            return None
        return o.meta

    async def _one(self, w, m, meta, gone) -> None:
        ans = {"behavior": "deny", "message": "请求无效，已拒绝。"}
        try:
            tool, inp = m.get("tool"), m.get("input")
            if isinstance(tool, str) and isinstance(inp, dict):
                ans = await self.ask(tool[:64], inp, gone.get(m["id"]), origin=origin(meta, m.get("who")), batch=False)
        except Exception:  # noqa: BLE001 — fail closed
            ans = {"behavior": "deny", "message": "批准通道出错：默认拒绝。"}
        finally:
            gone.pop(m["id"], None)
        with contextlib.suppress(Exception):
            w.write((json.dumps({"t": "answer", "id": m["id"], "behavior": ans.get("behavior"),
                                 "message": ans.get("message")}, ensure_ascii=False) + "\n").encode())
            await w.drain()


class Client:
    """In the CEO process. connect() → False when serve is not there (the gate then has no approver: C14)."""

    def __init__(self, order: orders.Order, st_root=None, timeout: float = 900):
        self.order, self.st_root, self.timeout = order, st_root, timeout
        self.r = self.w = None
        self.n = 0
        self.wait: dict[int, asyncio.Future] = {}
        self.reader = None

    async def connect(self) -> bool:
        try:
            tok = (self.order.path / ".ask_token").read_text().strip()
            self.r, self.w = await asyncio.open_unix_connection(str(sock_path(self.st_root)))
            self.w.write((json.dumps({"t": "hello", "order": self.order.id, "token": tok}) + "\n").encode())
            await self.w.drain()
            ok = json.loads(await asyncio.wait_for(self.r.readline(), 10) or b"null")
        except (OSError, ValueError, asyncio.TimeoutError, socket.error):
            return False
        if not isinstance(ok, dict) or ok.get("t") != "ok":
            return False
        self.reader = asyncio.create_task(self._read())
        return True

    async def _read(self):
        try:
            while line := await self.r.readline():
                m = json.loads(line)
                f = self.wait.pop(m.get("id"), None)
                if f and not f.done():
                    f.set_result(m)
        except (ValueError, ConnectionError):
            pass
        for f in self.wait.values():
            if not f.done():
                f.set_result({"behavior": "deny", "message": "批准通道断开：默认拒绝。"})

    async def __call__(self, tool: str, tool_input: dict, who: str | None) -> dict:
        if self.w is None or self.reader is None or self.reader.done():
            return {"behavior": "deny", "message": "批准通道没有连上：默认拒绝。"}
        self.n += 1
        f = asyncio.get_running_loop().create_future()
        self.wait[self.n] = f
        self.w.write((json.dumps({"t": "ask", "id": self.n, "tool": tool, "input": tool_input, "who": who},
                                 ensure_ascii=False) + "\n").encode())
        await self.w.drain()
        try:
            return await asyncio.wait_for(f, self.timeout)
        except asyncio.TimeoutError:
            return {"behavior": "deny", "message": "等待批准超时：默认拒绝。"}

    async def close(self):
        if self.w:
            with contextlib.suppress(Exception):
                self.w.close()
        if self.reader:
            self.reader.cancel()
