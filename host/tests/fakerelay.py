"""Test-only stand-ins for the blind relay (PROTOCOL.md §2) and a device (§3–§4), so host tests can drive a real `jarvis serve`
through a full pairing without wrangler/workerd. The relay verifies the host's auth like the real one (channel derivation +
Ed25519 over the challenge) and forwards opaque frames; the device is the web client's protocol on the host's own noise.py."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from websockets.asyncio.server import serve as ws_serve
from websockets.sync.client import connect as ws_connect

from jarvis_host import wire
from jarvis_host.noise import IKPSK2, Handshake, Keypair

_PATH = re.compile(r"/v1/(host|dev)/([A-Za-z0-9_-]{22})")


class FakeRelay:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.hosts: dict[str, object] = {}
        self.devs: dict[int, tuple[str, object]] = {}
        self.next_cid = 1
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.ready.set()
        self.loop.run_forever()

    async def _start(self):
        self.server = await ws_serve(self._handler, "127.0.0.1", 0, max_size=2**17)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        self.ready.wait(10)
        return self

    def __exit__(self, *a):
        async def stop():
            self.server.close()
            await self.server.wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)

    async def _handler(self, ws):
        m = _PATH.fullmatch(ws.request.path)
        if not m:
            return await ws.close(4004)
        role, ch = m.groups()
        if role == "host":
            await self._host(ws, ch)
        else:
            await self._dev(ws, ch)

    async def _host(self, ws, ch):
        n = wire.b64u(os.urandom(32))
        await ws.send(json.dumps({"t": "challenge", "n": n}))
        auth = json.loads(await asyncio.wait_for(ws.recv(), 10))
        pk = wire.unb64u(auth["pk"])
        if wire.channel_id(pk) != ch:
            return await ws.close(4003)
        Ed25519PublicKey.from_public_bytes(pk).verify(wire.unb64u(auth["sig"]), f"agentjarvis-relay-auth-v1\n{ch}\n{n}".encode())
        await ws.send(json.dumps({"t": "ok"}))
        self.hosts[ch] = ws
        for cid, (c, d) in list(self.devs.items()):
            if c == ch:
                await d.send(json.dumps({"t": "host", "up": True}))
        try:
            async for f in ws:
                if not isinstance(f, bytes) or len(f) < 5:
                    continue
                op, cid, payload = f[0], int.from_bytes(f[1:5], "big"), f[5:]
                dev = self.devs.get(cid)
                if not dev or dev[0] != ch:
                    continue
                if op == wire.OP_DATA:
                    await dev[1].send(payload)
                elif op == wire.OP_CLOSE:
                    await dev[1].close(4010)
        finally:
            if self.hosts.get(ch) is ws:
                del self.hosts[ch]

    async def _dev(self, ws, ch):
        cid = self.next_cid
        self.next_cid += 1
        self.devs[cid] = (ch, ws)
        host = self.hosts.get(ch)
        await ws.send(json.dumps({"t": "host", "up": host is not None}))
        try:
            if host:
                await host.send(bytes([wire.OP_OPEN]) + cid.to_bytes(4, "big"))
            async for f in ws:
                h = self.hosts.get(ch)
                if isinstance(f, bytes) and h:
                    await h.send(bytes([wire.OP_DATA]) + cid.to_bytes(4, "big") + f)
        except Exception:
            pass
        finally:
            self.devs.pop(cid, None)
            h = self.hosts.get(ch)
            if h:
                try:
                    await h.send(bytes([wire.OP_GONE]) + cid.to_bytes(4, "big"))
                except Exception:
                    pass


def parse_link(link: str) -> dict:
    p = json.loads(base64.urlsafe_b64decode(link.split("#p=", 1)[1] + "=="))
    return {"relay": p["r"], "channel": p["c"], "host_pub": wire.unb64u(p["k"]), "pid": wire.unb64u(p["i"]),
            "psk": wire.unb64u(p["p"])}


class PyDevice:
    """Pairs from a QR link like the web client: IKpsk2 msg1 → HS_RESP → hello; shows the safety code (returned)."""

    def __init__(self, name: str = "测试手机"):
        self.name, self.kp, self.ws = name, Keypair.generate(), None
        self.pub = self.kp.pub
        self.id = wire.device_id(self.pub)

    def pair(self, link: str, timeout: float = 10) -> str:
        p = parse_link(link)
        self.ws = ws_connect(f"{p['relay']}/v1/dev/{p['channel']}", open_timeout=timeout).__enter__()
        while True:
            m = self.ws.recv(timeout)
            if isinstance(m, str) and json.loads(m) == {"t": "host", "up": True}:
                break
        hs = Handshake(IKPSK2, True, self.kp, prologue=wire.pair_prologue(p["channel"], p["pid"]), rs=p["host_pub"], psk=p["psk"])
        msg1 = hs.write_message(json.dumps({"v": 1, "name": self.name}).encode())
        self.ws.send(bytes([wire.PAIR_INIT]) + p["pid"] + msg1)
        resp = self._bin(timeout)
        assert resp[0] == wire.HS_RESP, resp[:1]
        hs.read_message(resp[1:])
        self.send, self.recv, h = hs.split()
        self.ws.send(bytes([wire.DATA]) + self.send.encrypt(b"", wire.pad_json({"t": "hello"})))
        return wire.safety_code(h)

    def _bin(self, timeout: float) -> bytes:
        while True:
            m = self.ws.recv(timeout)
            if isinstance(m, bytes):
                return m

    def app(self, timeout: float = 10) -> dict:
        b = self._bin(timeout)
        assert b[0] == wire.DATA
        return wire.unpad_json(self.recv.decrypt(b"", b[1:]))

    def closed(self, timeout: float = 10) -> bool:
        """True when the relay closes this socket (host sent op 0x02) before any app message arrives."""
        from websockets.exceptions import ConnectionClosed
        try:
            while True:
                m = self.ws.recv(timeout)
                if isinstance(m, bytes):
                    return False
        except ConnectionClosed:
            return True
        except TimeoutError:
            return False

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
