"""P366 webhooks inside `agentj serve` (AGENTJ-HOOKS-SPEC §2 – §8): the relay socket, `<state>/agentperm/hooks.sock` for
`agentj hook …` (also from inside the fence), the delivery pipeline and the phone's 「Webhook」 list. Policy lives in hooks.py.

Pipeline for one public request (fixed order; the first failure answers the sender and ends it):
  hook known & live (else 404 / 410) → enabled and not stopped (423) → source IP (403) → end-to-end hooks: open the sealed
  body (400) → signature + replay (401) → dedupe
  (200 duplicate) → per-hook minute (429) → seat day quota (429) → written to disk → `once` used up → delivered to its target
  (main · owner · wait · order:<id> · bot:<id>) AND the main Agent's triage notice (always; ≤ 3 a minute per hook, then one
  merged notice) → 200.
The relay socket is opened only while at least one hook is live (no extra relay connection for hosts that never use hooks).
It authenticates with the friends seat certificate (§17.3; `PeerService._cert`) under its own signature prefix.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import json
import os
import pathlib
import secrets
import socket
import struct
import sys
import time

from . import hooks, relaydiag, wire

SOCK_NAME = "hooks.sock"
ENV = "AGENTJ_HOOKS_SOCK"
TOOL = "AgentJHook"                 # the resident hook's approval card (approval_level.HOST_CARDS: never auto-allowed)
REQ_MAX = 256 * 1024
WAIT_MAX = 86400
WAITERS_MAX = 8                     # concurrent `agentj hook wait` per hook
MERGE = 3                           # triage notices per hook per minute before they merge
RECONNECT = (1.0, 60.0)
SECRET_DIR = "~/.local/share/agentj-hooks"


class OrderSink:
    """Hook deliveries for a Harness order (P359 `core/orders.py`). Without P359 in this build every call answers "no such
    order" (tests pass a stand-in). With it: an order is live while not terminal; `grants.hooks` in order.json is what its
    parent allowed ({"max": n, "life": [...], "ttl_max": s}); a delivery is one inbox message read at the next step."""

    def __init__(self, st_root=None):
        self.st_root = st_root

    def _orders(self):
        try:
            from .core import orders
            return orders
        except ImportError:
            return None

    def _get(self, oid: str):
        o = self._orders()
        if o is None:
            return None
        try:
            return o.get(oid, self.st_root)
        except Exception:  # noqa: BLE001 — bad id / no such order
            return None

    def live(self, oid: str) -> bool:
        o = self._get(oid)
        return o is not None and o.state() not in ("done", "failed", "stopped", "budget")

    def grant(self, oid: str) -> dict | None:
        o = self._get(oid)
        if o is None or not self.live(oid):
            return None
        g = ((o.meta.get("grants") or {}) if isinstance(o.meta.get("grants"), dict) else {}).get("hooks")
        return g if isinstance(g, dict) else None

    def deliver(self, oid: str, text: str, hook: str, delivery: str) -> bool:
        o = self._get(oid)
        if o is None or not self.live(oid):
            return False
        try:
            o.send(text)
            o.event("hook_delivered", hook=hook[:8], delivery=delivery)
            return True
        except Exception:  # noqa: BLE001
            return False


class HookService:
    def __init__(self, host, store: hooks.Store | None = None, orders: OrderSink | None = None, clock=time.time):
        self.host, self.st, self.clock = host, host.st, clock
        self.store = store or hooks.Store(self.st.root, clock=clock)
        self.orders = orders or OrderSink(self.st.root)
        self.queues: dict[str, collections.deque] = {}           # hook id → deliveries waiting for `agentj hook wait`
        self.waiters: dict[str, list[asyncio.Future]] = {}
        self.minute: dict[str, collections.deque] = {}           # hook id → accepted times (per-hook minute limit)
        self.notes: dict[str, dict] = {}                         # hook id → triage notice merge state
        self.ws = None
        self.up = False
        self.last_down: dict | None = None
        self.net_task: asyncio.Task | None = None
        self.server = None
        self.cards: set[asyncio.Task] = set()
        self.widening: set[str] = set()                           # resident hooks with a widening card open

    # ------------------------------------------------------------ helpers
    @property
    def lang(self) -> str:
        return "en" if str(getattr(self.host, "lang", "zh") or "").lower().startswith("en") else "zh"

    def log(self, ev: str, **kw) -> None:
        with contextlib.suppress(Exception):
            self.st.log(ev, **kw)

    def hb(self) -> str | None:
        k = self._keys()
        return hooks.hookbox_of(k.mbox) if k else None

    def _keys(self):
        from .peer import PeerKeys
        try:
            return PeerKeys.load_or_create(self.st)
        except Exception:  # noqa: BLE001
            return None

    def url(self, h: dict, token: str | None = None) -> str:
        return hooks.url_of(self.hb() or "<hb>", h, token)

    def ids_text(self) -> str:
        """The live (active) hook ids for the relay: an unknown id is answered 404 at the edge without using the box's
        window (review M4). The ids are in the public URLs anyway; nothing else about a hook is sent."""
        return json.dumps({"t": "ids", "h": [h["id"] for h in self.store.live() if h["state"] == "active"][:32]},
                          separators=(",", ":"))

    def changed(self) -> None:
        if self.ws is not None and self.up:
            ws, text = self.ws, self.ids_text()
            t = asyncio.ensure_future(ws.send(text))
            self.cards.add(t)
            t.add_done_callback(lambda f: (self.cards.discard(f), f.cancelled() or f.exception()))
        with contextlib.suppress(Exception):
            self.host.peer_broadcast({"t": "hook_changed"})   # every ready p33 phone re-reads its 「Webhook」 list

    # ------------------------------------------------------------ start / stop
    async def start(self) -> None:
        await asyncio.to_thread(self.store.prune)
        st = self.st
        st.perm_dir.mkdir(mode=0o700, exist_ok=True)
        path = st.perm_dir / SOCK_NAME
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        old = os.umask(0o077)
        try:
            self.server = await asyncio.start_unix_server(self.on_client, path=str(path), limit=REQ_MAX)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        os.environ[ENV] = str(path)
        self.ensure_net()

    async def stop(self) -> None:
        for fut in [f for fs in self.waiters.values() for f in fs]:
            if not fut.done():
                fut.set_result({"ok": False, "why": "stopped"})
        if self.net_task:
            self.net_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.net_task
        if self.server:
            self.server.close()

    def estop(self, on: bool) -> None:
        if on:
            for hid, fs in self.waiters.items():
                for f in fs:
                    if not f.done():
                        f.set_result({"ok": False, "why": "stopped"})

    def ensure_net(self) -> None:
        """Connect while a hook is live; drop the socket when none is."""
        want = bool(self.store.live())
        if want and (self.net_task is None or self.net_task.done()):
            self.net_task = asyncio.ensure_future(self.run_net())
        elif not want and self.net_task is not None and not self.net_task.done():
            self.net_task.cancel()

    # ------------------------------------------------------------ the relay socket (AGENTJ-HOOKS-SPEC §2)
    async def _cert(self) -> str | None:
        peers = getattr(self.host, "peers", None)
        if peers is None or not hasattr(peers, "_cert"):
            return None
        return await peers._cert()

    async def run_net(self) -> None:
        from websockets.asyncio.client import connect
        keys = self._keys()
        relay = (self.host.cfg.get("relay") or "").rstrip("/")
        if keys is None or not relay:
            return
        url = f"{relay}/v1/hook/{keys.mbox}"
        backoff = RECONNECT[0]
        while True:
            cert = None
            with contextlib.suppress(Exception):
                cert = await self._cert()
            if cert:
                phase = "connect"
                try:
                    async with connect(url, max_size=2 ** 18, ping_interval=20, ping_timeout=20, open_timeout=15) as ws:
                        phase = "auth"
                        ch = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        n = ch.get("n") if isinstance(ch, dict) and ch.get("t") == "challenge" else None
                        await ws.send(auth_text(keys, n, cert))
                        ok = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if not isinstance(ok, dict) or ok.get("t") != "ok":
                            raise ValueError("relay refused auth")
                        phase = "up"
                        await ws.send(self.ids_text())
                        self.ws, self.up, self.last_down, backoff = ws, True, None, RECONNECT[0]
                        self.log("hook_net_up")
                        async for m in ws:
                            if isinstance(m, bytes) and len(m) >= 9 and m[0] == 0x41:
                                t = asyncio.ensure_future(self._frame(ws, m))
                                self.cards.add(t)          # keep a reference until it answers
                                t.add_done_callback(self.cards.discard)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — network / refusal / replaced → back off and retry
                    d = relaydiag.classify(e, phase)
                    self.log("hook_net_down", phase=phase, code=d["code"], why=d["why"])
                    if phase != "up":
                        self.last_down = d
                finally:
                    self.ws, self.up = None, False
            else:
                self.last_down = {"phase": "seat", "why": "seat", "code": None, "http": None, "kind": "no_cert"}
            await asyncio.sleep(backoff if cert else 3600)
            backoff = min(backoff * 2, RECONNECT[1])

    async def _frame(self, ws, m: bytes) -> None:
        rid, n = struct.unpack(">II", m[1:9])
        try:
            meta = json.loads(m[9:9 + n])
            body = m[9 + n:]
            if isinstance(meta, dict):        # set by this host only (forward / after opening a sealed body), never by the relay
                meta.pop("local", None)
                meta.pop("sealed", None)
            status, obj = await self.handle(meta if isinstance(meta, dict) else {}, body)
        except Exception as e:  # noqa: BLE001 — a bug never takes the socket down; the sender retries
            self.log("hook_error", err=type(e).__name__)
            status, obj = 500, {"error": "host"}
        raw = json.dumps(obj, separators=(",", ":")).encode()[:512]
        with contextlib.suppress(Exception):
            await ws.send(b"\x42" + struct.pack(">IH", rid, status) + raw)

    # ------------------------------------------------------------ one public request
    async def handle(self, meta: dict, body: bytes, test: bool = False) -> tuple[int, dict]:
        hid = meta.get("h")
        h = self.store.get(hid) if isinstance(hid, str) and hooks.ID_RE.fullmatch(hid) else None
        if h is None or h["state"] == "pending":
            return 404, {"error": "not_found"}
        if h["state"] != "active":
            return 410, {"error": "gone"}
        if not h.get("enabled", True):
            return 423, {"error": "paused", "retry": 300}
        if self.host.stopped():
            return 423, {"error": "stopped", "retry": 300}
        if len(body) > hooks.BODY_MAX:
            return 413, {"error": "too_big"}
        if not hooks.ip_ok(str(meta.get("ip") or ""), h.get("ip_allow") or []):
            self.store.log_refusal(h, "ip")
            self.log("hook_refused", hook=hid[:8], reason="ip")
            return 403, {"error": "ip"}
        sealed, kid = hooks.seal_header(meta)
        if sealed or h.get("e2e"):
            # an end-to-end hook takes only sealed bodies from the public entry (a plain one already crossed the relay in the
            # clear: refusing it tells the sender at once); `forward` is local and may stay plain
            why = ""
            if not sealed:
                why = "" if meta.get("local") else "plain"
            elif not h.get("e2e") or kid == "?":
                why = "seal"
            else:
                plain = await asyncio.to_thread(hooks.unseal, self.store.seal_keys(hid), hid, body, kid)
                if plain is None:
                    why = "seal"
                else:
                    body, meta = plain, {**meta, "sealed": True}
            if why:
                self.store.log_refusal(h, why)
                self.log("hook_refused", hook=hid[:8], reason=why)
                self._fail_warn(h)
                return 400, {"error": "sealed_required" if why == "plain" else "seal"}
        ok, why, keys = hooks.verify(h, self.store.keys(hid), meta, body, self.clock())
        if not ok:
            self.store.log_refusal(h, why)
            self.log("hook_refused", hook=hid[:8], reason=why)
            self._fail_warn(h)
            return (405, {"error": "method"}) if why == "method" else (401, {"error": why})
        # review H2: limits first, the dedupe keys are recorded only for a delivery that is accepted — a 429 never turns the
        # sender's retry into a "duplicate"
        if self.store.seen_any(hid, keys):
            self.log("hook_duplicate", hook=hid[:8])
            return 200, {"ok": True, "duplicate": True}
        q = self.minute.setdefault(hid, collections.deque())
        now = self.clock()
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= hooks.PER_MIN:
            return 429, {"error": "rate", "retry": 60}
        if not test and not self.store.take_day(hid):
            self.log("hook_refused", hook=hid[:8], reason="quota")
            return 429, {"error": "quota", "retry": 3600}
        q.append(now)
        self.store.remember(hid, keys)
        once = h["life"] == "once" and not test
        if once:                      # review M1: used up before the first await — a concurrent second delivery gets 410
            h["state"] = "used"
        try:
            rec = await asyncio.to_thread(self.store.put_delivery, hid, meta, body, "ok", h)
        except Exception:
            if once:
                h["state"] = "active"
            raise
        h["last"] = {"at": round(now), "result": "ok"}
        h["count"] = int(h.get("count") or 0) + 1
        if once:
            self.store.end(h, "used")
            self.ensure_net()
        else:
            self.store.save()
        self.log("hook_in", hook=hid[:8], bytes=len(body), target=h["target"].split(":")[0])
        await self.deliver(h, rec, test)
        self.changed()
        return 200, {"ok": True}

    def _fail_warn(self, h: dict) -> None:
        f = h.get("fails") or {}
        if f.get("n", 0) >= hooks.FAIL_WARN and not f.get("warned"):
            f["warned"] = True
            self.store.save()
            self.host.agent_notice(("有人在反复试 Webhook「{n}」（1 小时内验签失败 {k} 次）。不是你在测试的话，建议轮换密钥："
                                    "agentj hook rotate {n}").format(n=h["name"], k=f["n"]) if self.lang != "en" else
                                   ("Someone keeps trying webhook 「{n}」 ({k} bad signatures within an hour). If that is not your "
                                    "test, rotate its secret: agentj hook rotate {n}").format(n=h["name"], k=f["n"]))

    # ------------------------------------------------------------ the targets + the main Agent's triage
    async def deliver(self, h: dict, rec: dict, test: bool = False) -> None:
        t = h["target"]
        where = {"zh": "送到了主 Agent（就是你）", "en": "it was addressed to you, the main Agent"}
        env_text = None
        if t == "owner":
            text = hooks.owner_text(self.lang, h, rec, test)
            self.host.agent_notice(text)
            with contextlib.suppress(Exception):
                self.host.push_notify("reply")
            self._telegram(text)
            where = {"zh": "主人已经在手机上看到了", "en": "the owner already sees it on the phone"}
        elif t.startswith("order:"):
            oid = t.split(":", 1)[1]
            env_text = hooks.envelope(self.lang, h, rec, "派单 " + oid if self.lang != "en" else "order " + oid, test=test)
            if self.orders.deliver(oid, env_text, h["id"], rec["id"]):
                where = {"zh": f"已送进派单 {oid} 的 inbox，CEO 下一步会读到", "en": f"handed to order {oid}'s inbox"}
            else:
                where = {"zh": f"原定的派单 {oid} 已结束或不存在，请你决定怎么处理",
                         "en": f"its order {oid} has ended or does not exist — decide what to do with it"}
        elif t.startswith("bot:"):
            where = {"zh": f"属于接待员 {t[4:12]}（不会进访客对话）", "en": f"it belongs to reception bot {t[4:12]} (not sent to any visitor)"}
        elif t == "wait":
            where = {"zh": "交给了等它的进程（agentj hook wait）", "en": "handed to the process waiting for it (agentj hook wait)"}
        self._enqueue(h, rec, env_text or hooks.envelope(self.lang, h, rec, where[self.lang], test=test))
        self._triage(h, rec, where[self.lang], test)

    def _enqueue(self, h: dict, rec: dict, text: str) -> None:
        fs = [f for f in self.waiters.get(h["id"], []) if not f.done()]
        item = {"ok": True, "delivery": rec["id"], "hook": h["id"], "text": text, "at": rec["at"]}
        if fs:
            fs[0].set_result(item)
            self.waiters[h["id"]] = fs[1:]
            return
        q = self.queues.setdefault(h["id"], collections.deque(maxlen=hooks.QUEUE_MAX))
        q.append(item)

    def _triage(self, h: dict, rec: dict, where: str, test: bool) -> None:
        """One notice per delivery, ≤ MERGE a minute per hook; more within the minute → one merged notice at its end."""
        st = self.notes.setdefault(h["id"], {"times": collections.deque(), "held": None, "n": 0, "task": None})
        now = self.clock()
        while st["times"] and now - st["times"][0] >= 60:
            st["times"].popleft()
        if len(st["times"]) < MERGE:
            st["times"].append(now)
            self._to_agent(hooks.envelope(self.lang, h, rec, where, test=test), h["id"])
            return
        st["held"], st["n"] = (h, rec, where, test), st["n"] + 1
        if st["task"] is None or st["task"].done():
            delay = max(1.0, 60 - (now - st["times"][0]))

            async def flush():
                await asyncio.sleep(delay)
                held, n = st["held"], st["n"]
                st["held"], st["n"] = None, 0
                if held:
                    hh, rr, ww, tt = held
                    st["times"].append(self.clock())
                    self._to_agent(hooks.envelope(self.lang, hh, rr, ww, more=n - 1, test=tt), hh["id"])
            st["task"] = asyncio.ensure_future(flush())

    def _to_agent(self, body: str, hid: str) -> str:
        a = getattr(self.host, "agent", None)
        if a is None or not callable(getattr(a, "submit", None)):
            self.log("hook_agent", hook=hid[:8], result="no_agent")
            return "no_agent"
        try:
            from . import provenance
            from .compose import Send
            if getattr(a, "kind", None) == "codex":        # P351: Codex writes a progress note first unless told not to
                from .heartbeat import NO_NOTES
                body += "\n" + NO_NOTES[self.lang]
            page = self.host.hist_add({"k": "agent", "via": "hook", "text": ""}, "", "open")
            s = Send("hook", secrets.token_urlsafe(16), page["id"], text=body, by="hook")
            s.source = provenance.system()
            s.hook_notice = hid
            a.submit(s)
            return "queued"
        except Exception as e:  # noqa: BLE001 — the delivery is on disk and at its target; the notice is best effort
            self.log("hook_agent", hook=hid[:8], result="failed", err=type(e).__name__)
            return "failed"

    def _telegram(self, text: str) -> None:
        tg = getattr(self.host, "telegram", None)
        if tg is None:
            return
        try:
            from .telegram import configuration
            cfg = configuration(self.st)
            if cfg and tg.enabled():
                tg.out.put_nowait((dict(cfg), text))
        except Exception:  # noqa: BLE001 — Telegram is a copy; the phone page is the record
            pass

    # ------------------------------------------------------------ hooks.sock (agentj hook …)
    async def on_client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            sock = w.get_extra_info("socket")
            if sock is not None and hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    return
            req = None
            try:
                req = json.loads(await asyncio.wait_for(r.readline(), 10))
                res = await self.cli(req) if isinstance(req, dict) else {"ok": False, "why": "shape"}
            except (ValueError, asyncio.TimeoutError, asyncio.LimitOverrunError):
                res = {"ok": False, "why": "shape"}
            self.log("hook_cli", kind=str(req.get("t"))[:12] if isinstance(req, dict) else None,
                     result="ok" if res.get("ok") else str(res.get("why"))[:24])
            w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
            await w.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(Exception):
                w.close()

    def view(self, h: dict) -> dict:
        v = {k: h.get(k) for k in ("id", "name", "preset", "life", "expires", "target", "auth", "ip_allow", "enabled", "note",
                                   "created", "approved", "state", "ended", "last", "count", "seal")}
        v["e2e"] = bool(h.get("e2e"))      # false = the body crosses our relay in the clear (said on the CLI and the phone)
        v["url"] = self.url(h)
        return v

    async def cli(self, req: dict) -> dict:
        t = req.get("t")
        try:
            if t == "presets":
                return {"ok": True, "presets": {k: {kk: vv for kk, vv in p.items()} for k, p in hooks.PRESETS.items()}}
            if t == "create":
                return await self._create(req)
            if t == "list":
                rows = self.store.all() if req.get("all") else self.store.live()
                rows = [h for h in rows if self._owns(h, req)]
                return {"ok": True, "hooks": [self.view(h) for h in rows], "up": self.up, "net": self.last_down,
                        "quota": {"used": self.store.used_today(), "day_max": hooks.DAY_MAX, "hooks_max": hooks.HOOKS_MAX,
                                  "resident_max": hooks.RESIDENT_MAX}}
            h = self.store.find(req.get("id"))
            if t == "triage":
                return self._triage_set(req)
            if h is None:
                return {"ok": False, "why": "no_hook"}
            if not self._owns(h, req):            # review M2: an order reads, waits on and changes only its own hooks
                return {"ok": False, "why": "not_yours"}
            if t == "show":
                return {"ok": True, "hook": self.view(h), "deliveries": self.store.deliveries(h["id"], 10)}
            if t == "logs":
                if req.get("show"):
                    rec = self.store.delivery(req["show"])
                    if not rec or rec.get("hook") != h["id"]:
                        return {"ok": False, "why": "no_delivery"}
                    return {"ok": True, "text": hooks.envelope(self.lang, h, rec, "（记录 / record）"), "delivery": rec["id"]}
                return {"ok": True, "deliveries": self.store.deliveries(h["id"], max(1, min(200, int(req.get("limit") or 20))))}
            if t == "wait":
                return await self._wait(h, req)
            if t == "revoke":
                self.store.end(h, "revoked")
                self.queues.pop(h["id"], None)
                for f in self.waiters.pop(h["id"], []):
                    if not f.done():
                        f.set_result({"ok": False, "why": "gone"})
                self.ensure_net()
                self.changed()
                self.log("hook_revoked", hook=h["id"][:8])
                return {"ok": True, "hook": self.view(h)}
            if t == "update":
                return await self._update(h, req)
            if t == "rotate":
                return await self._rotate(h, req)
            if t == "test":
                return await self._test(h, req)
            if t == "forward":
                return await self._forward(h, req)
        except hooks.HookError as e:
            return {"ok": False, "why": e.code, "detail": e.detail}
        return {"ok": False, "why": "unknown"}

    def _owns(self, h: dict, req: dict) -> bool:
        """An order may change only the hooks it created; the main Agent / the owner any (resident widening → the card)."""
        oid = req.get("order")
        return not oid or h["created"]["by"] == "order:" + str(oid)

    async def _create(self, req: dict) -> dict:
        spec = req.get("hook") if isinstance(req.get("hook"), dict) else {}
        by = "main"
        oid = req.get("order")
        if oid:
            if not isinstance(oid, str) or not hooks.TARGET_RE.fullmatch("order:" + oid):
                return {"ok": False, "why": "bad_order"}
            g = self.orders.grant(oid)
            if g is None:
                return {"ok": False, "why": "no_grant"}
            life = spec.get("life") or hooks.PRESETS.get(spec.get("preset") or "hmac", {}).get("life")
            if life == "resident":
                return {"ok": False, "why": "resident_needs_owner"}
            if life not in (g.get("life") or ["once", "ttl"]):
                return {"ok": False, "why": "no_grant", "detail": "life"}
            mine = [x for x in self.store.all() if x["created"]["by"] == "order:" + oid]
            if len(mine) >= int(g.get("max") or 1):
                return {"ok": False, "why": "no_grant", "detail": "max"}
            tgt = spec.get("target") or "order:" + oid
            if tgt not in ("order:" + oid, "wait"):
                return {"ok": False, "why": "bad_target", "detail": "an order delivers to itself or to wait"}
            spec = {**spec, "target": tgt}
            cap = int(g.get("ttl_max") or hooks.ONCE_DEF)
            spec["ttl"] = min(hooks.parse_duration(spec.get("ttl"), min(cap, hooks.ONCE_DEF), hooks.TTL_MAX), cap)
            by = "order:" + oid
        h = hooks.build(spec, by, self.clock())
        if h["target"].startswith("order:") and not self.orders.live(h["target"][6:]):
            return {"ok": False, "why": "bad_target", "detail": "no such live order"}
        # the stored key = the exact secret string the sender holds (HMAC key bytes / the bearer or URL token)
        key, src = hooks.new_secret(), "generated"
        if isinstance(req.get("secret"), str) and req["secret"]:
            if h["auth"]["kind"] == "token" or not 8 <= len(req["secret"]) <= 512:
                return {"ok": False, "why": "bad_secret"}
            key, src = req["secret"].encode(), "file"
        elif h["auth"]["kind"] == "stripe":
            src = "stripe_later"           # Stripe makes the signing secret: until `rotate --secret-file`, nothing verifies
        h["secret_set"] = src != "stripe_later"
        self.store.add(h, key)
        if h.get("e2e"):
            self.store.new_seal(h)
            self.store.save()
        self.log("hook_created", hook=h["id"][:8], life=h["life"], kind=h["auth"]["kind"], by=by.split(":")[0])
        out = {"ok": True, "hook": self.view(h), "secret_source": src}
        if src == "generated":
            out["secret"] = key.decode()          # → the CLI's --secret-out file, never printed
            if h["auth"]["kind"] == "token":
                out["url_secret"] = self.url(h, key.decode())
        if h["life"] == "resident":
            self._card(h)
            out["pending"] = True
        else:
            self.ensure_net()
        self.changed()
        return out

    def _card(self, h: dict) -> None:
        text = hooks.card_text(h, self.url(h), self.lang)

        async def card():
            try:
                ans = await self.host.ask(TOOL, {"card": text, "hook": h["id"]}, batch=False, grant_info=True)
            except Exception as e:  # noqa: BLE001
                ans = {"behavior": "deny"}
                self.log("hook_card_error", err=type(e).__name__)
            cur = self.store.get(h["id"])
            if cur is None or cur["state"] != "pending":
                return
            if ans.get("behavior") == "allow":
                cur["state"] = "active"
                g = ans.get("grant") or {}
                cur["approved"] = {"device": g.get("device") if isinstance(g, dict) else None, "at": round(self.clock())}
                self.store.save()
                self.ensure_net()
                self.log("hook_approved", hook=h["id"][:8])
                msg = (f"常驻 Webhook「{h['name']}」已批准，开始接收：{self.url(h)}" if self.lang != "en"
                       else f"Resident webhook 「{h['name']}」 approved and receiving: {self.url(h)}")
            else:
                self.store.end(cur, "revoked")
                self.log("hook_denied", hook=h["id"][:8])
                msg = (f"主人没有批准常驻 Webhook「{h['name']}」，它已作废。" if self.lang != "en"
                       else f"The owner did not approve resident webhook 「{h['name']}」; it was dropped.")
            self.changed()
            self._to_agent("〔Agent J 系统提示〕" + msg + "\n没有要补充的，最终回复只写 〔不回群〕" if self.lang != "en" else
                           "[Agent J system notice] " + msg + "\nWith nothing to add, reply exactly 〔不回群〕", h["id"])
        task = asyncio.ensure_future(card())
        self.cards.add(task)
        task.add_done_callback(self.cards.discard)

    async def _update(self, h: dict, req: dict) -> dict:
        u = req.get("set") if isinstance(req.get("set"), dict) else {}
        if h["state"] == "pending":            # review M3: the card shows one definition; nothing changes under it
            return {"ok": False, "why": "pending"}
        if u.get("e2e") is True and not h.get("e2e"):      # tightening: at once, no card
            hooks.e2e_ok(h["auth"])
            self.store.new_seal(h)
            h["e2e"] = True
            self.store.save()
            self.log("hook_e2e", hook=h["id"][:8], on=True)
        widen = False
        new = dict(h)
        if u.get("e2e") is False and h.get("e2e"):         # back to plain through the relay: a widening (resident → card)
            new.update(e2e=False, seal=None)
            widen = True
        if "name" in u:
            new["name"] = hooks._clean_name(u["name"]) or h["name"]
        if "note" in u:
            note = str(u["note"] or "").strip()
            if len(note) > hooks.NOTE_MAX:
                raise hooks.HookError("too_long", "note")
            new["note"] = note
        if "enabled" in u:
            new["enabled"] = u["enabled"] is not False
            widen = widen or (new["enabled"] and not h.get("enabled", True))
        if "ip_allow" in u:
            new["ip_allow"] = hooks.check_ips(u["ip_allow"])
            widen = widen or (bool(h["ip_allow"]) and (not new["ip_allow"] or not set(new["ip_allow"]) <= set(h["ip_allow"])))
        if "target" in u:
            if not hooks.target_ok(u["target"]):
                raise hooks.HookError("bad_target", str(u["target"])[:60])
            if req.get("order") and u["target"] not in ("order:" + req["order"], "wait"):
                raise hooks.HookError("bad_target", "an order delivers to itself or to wait")
            new["target"] = u["target"]
            widen = widen or u["target"] != h["target"]      # review M3: re-routing a resident hook is the owner's call
        if "ttl" in u:
            if h["life"] == "resident":
                raise hooks.HookError("bad_life", "resident hooks do not expire")
            cap = hooks.ONCE_MAX if h["life"] == "once" else hooks.TTL_MAX
            new["expires"] = round(self.clock() + hooks.parse_duration(u["ttl"], hooks.TTL_DEF, cap))
        if u.get("life") == "resident" and h["life"] != "resident":
            if req.get("order"):
                return {"ok": False, "why": "resident_needs_owner"}
            new.update(life="resident", expires=None, state="pending")
            h.update(new)
            self.store.save()
            self._card(h)
            return {"ok": True, "hook": self.view(h), "pending": True}
        if widen and h["life"] == "resident":
            # a resident hook widened (back on, IPs opened, re-routed): the owner's card; on allow only the fields it showed
            # are applied, and only when none of them changed meanwhile (a pause from the phone is never undone silently)
            fields = [k for k in ("name", "note", "enabled", "ip_allow", "target", "e2e", "seal") if new.get(k) != h.get(k)]
            base = {k: h.get(k) for k in fields}
            pend = dict(new)
            if h["id"] in self.widening:
                return {"ok": False, "why": "pending"}
            self.widening.add(h["id"])

            async def card():
                try:
                    text = hooks.card_text(pend, self.url(pend), self.lang, plain_again=bool(h.get("e2e")) and not pend.get("e2e"))
                    try:
                        ans = await self.host.ask(TOOL, {"card": text, "hook": h["id"], "update": True}, batch=False)
                    except Exception:  # noqa: BLE001 — no card, no change
                        return
                    cur = self.store.get(h["id"])
                    if ans.get("behavior") == "allow" and cur is not None and cur["state"] == "active" \
                            and all(cur.get(k) == v for k, v in base.items()):
                        cur.update({k: pend[k] for k in fields})
                        if not cur.get("e2e"):
                            self.store.drop_seal(cur)
                        self.store.save()
                        self.changed()
                    elif ans.get("behavior") == "allow":
                        self.log("hook_card_stale", hook=h["id"][:8])
                finally:
                    self.widening.discard(h["id"])
            task = asyncio.ensure_future(card())
            self.cards.add(task)
            task.add_done_callback(self.cards.discard)
            return {"ok": True, "hook": self.view(h), "pending": True}
        was = bool(h.get("e2e"))
        h.update(new)
        if not h.get("e2e"):
            self.store.drop_seal(h)
            if was:
                self.log("hook_e2e", hook=h["id"][:8], on=False)
        self.store.save()
        self.changed()
        return {"ok": True, "hook": self.view(h)}

    async def _rotate(self, h: dict, req: dict) -> dict:
        grace = hooks.parse_duration(req.get("grace"), hooks.GRACE_DEF, hooks.GRACE_MAX) if req.get("grace") not in (0, "0") else 0
        given = isinstance(req.get("secret"), str) and req["secret"]
        if given and (h["auth"]["kind"] == "token" or not 8 <= len(req["secret"]) <= 512):
            return {"ok": False, "why": "bad_secret"}
        if h["state"] == "pending":
            return {"ok": False, "why": "pending"}
        if req.get("enc_key"):
            # the delivery key pair: anyone may encrypt to a public key (the HMAC still decides who may post), so a new pair
            # never widens anything — no card, even for a resident hook; the old private key opens bodies through the grace
            if not h.get("e2e"):
                return {"ok": False, "why": "not_e2e"}
            self.store.new_seal(h, grace)
            self.store.save()
            self.changed()
            self.log("hook_rotated", hook=h["id"][:8], src="enc_key")
            return {"ok": True, "hook": self.view(h), "enc_key": True}
        if h["life"] == "resident" and h.get("secret_set", True):
            # review M2: a resident hook's secret decides who may post into the owner's phone — a new one is the owner's card.
            # Exception: the first Stripe whsec_ after creation (until then nothing could verify).
            return self._rotate_card(h, req["secret"].encode() if given else None, grace, req.get("secret_out"))
        if given:
            key = req["secret"].encode()
            self.store.set_key(h["id"], key, grace)
            h["secret_set"] = True
            self.store.save()
            self.log("hook_rotated", hook=h["id"][:8], src="file")
            return {"ok": True, "hook": self.view(h), "secret_source": "file"}
        key = hooks.new_secret()
        self.store.set_key(h["id"], key, grace)
        self.log("hook_rotated", hook=h["id"][:8], src="generated")
        out = {"ok": True, "hook": self.view(h), "secret_source": "generated", "secret": key.decode()}
        if h["auth"]["kind"] == "token":
            out["url_secret"] = self.url(h, key.decode())
        return out

    def _rotate_card(self, h: dict, key: bytes | None, grace: int, dest) -> dict:
        if h["id"] in self.widening:
            return {"ok": False, "why": "pending"}
        self.widening.add(h["id"])
        text = (f"给常驻 Webhook「{h['name']}」换密钥（旧密钥再有效 {grace // 3600} 小时）。新密钥{'来自你在密钥卡里存的文件' if key else '由 Agent J 主机生成，写进本机文件'}。"
                if self.lang != "en" else f"Rotate the secret of resident webhook 「{h['name']}」 (the old one stays valid "
                f"{grace // 3600} h). The new secret {'comes from the file you saved with a secret card' if key else 'is generated and written to a file on this computer'}.")

        async def card():
            try:
                try:
                    ans = await self.host.ask(TOOL, {"card": text, "hook": h["id"], "rotate": True}, batch=False)
                except Exception:  # noqa: BLE001
                    return
                cur = self.store.get(h["id"])
                if ans.get("behavior") != "allow" or cur is None or cur["state"] != "active":
                    return
                new = key or hooks.new_secret()
                if key is None:
                    try:
                        where = _write_secret(dest, new.decode(), h["id"])
                    except SystemExit:
                        self.log("hook_rotate_failed", hook=h["id"][:8])
                        return
                    if h["auth"]["kind"] == "token":
                        with contextlib.suppress(SystemExit):
                            _write_secret(f"file:{os.path.dirname(where.split('#')[0])}/{h['id']}.url", self.url(h, new.decode()), h["id"])
                self.store.set_key(h["id"], new, grace)
                self.log("hook_rotated", hook=h["id"][:8], src="card")
                self._to_agent(("〔Agent J 系统提示〕主人批准了给「{n}」换密钥，新密钥已生效（文件里）。没有要补充的，最终回复只写 〔不回群〕"
                                if self.lang != "en" else "[Agent J system notice] The owner approved the new secret for 「{n}」; it "
                                "is in effect (in its file). With nothing to add, reply exactly 〔不回群〕").format(n=h["name"]), h["id"])
            finally:
                self.widening.discard(h["id"])
        task = asyncio.ensure_future(card())
        self.cards.add(task)
        task.add_done_callback(self.cards.discard)
        return {"ok": True, "hook": self.view(h), "pending": True}

    async def _test(self, h: dict, req: dict) -> dict:
        """Sign a delivery the way this hook's sender would and run it through the same pipeline (no quota)."""
        keys = self.store.keys(h["id"])
        if not keys:
            return {"ok": False, "why": "no_secret"}
        body = str(req.get("body") or '{"test": true, "from": "agentj hook test"}').encode()[:hooks.BODY_MAX]
        hd, tok = hooks.sign_test(h, keys[0], body, self.clock())
        if h.get("e2e") and h.get("seal"):        # sealed the way a sender would, with the published public key
            body = hooks.seal(hooks.unb64u(h["seal"]["pub"]), h["id"], body)
            hd.update({hooks.SEAL_HEADER: f"{hooks.SEAL_V}; kid={h['seal']['kid']}", "Content-Type": "application/octet-stream"})
        meta = {"m": "POST", "h": h["id"], "t": tok, "q": "", "hd": {k.lower(): v for k, v in hd.items()},
                "ip": (h.get("ip_allow") or [""])[0].split("/")[0]}
        status, obj = await self.handle(meta, body, test=True)
        return {"ok": status == 200, "status": status, "answer": obj, "net_up": self.up}

    async def _forward(self, h: dict, req: dict) -> dict:
        """The self-built route (§10): an event the owner's own entry already checked comes in through hooks.sock and is
        checked again with this hook's own method, then handled exactly like a public request."""
        body = str(req.get("body") or "").encode()
        heads = req.get("headers") if isinstance(req.get("headers"), dict) else {}
        meta = {"m": "POST", "h": h["id"], "t": str(req.get("token") or ""), "q": "", "local": True,
                "hd": {str(k).lower()[:64]: str(v)[:2048] for k, v in list(heads.items())[:hooks.HEAD_MAX // 64]}, "ip": ""}
        status, obj = await self.handle(meta, body)
        return {"ok": status == 200, "status": status, "answer": obj}

    async def _wait(self, h: dict, req: dict) -> dict:
        timeout = max(1, min(WAIT_MAX, int(req.get("timeout") or 1800)))
        q = self.queues.get(h["id"])
        while q:
            item = q.popleft()
            if self.clock() - item["at"] < hooks.QUEUE_TTL:
                return item
        if self.host.stopped():
            return {"ok": False, "why": "stopped"}
        if h["state"] != "active":
            return {"ok": False, "why": "gone"}
        if len([f for f in self.waiters.get(h["id"], []) if not f.done()]) >= WAITERS_MAX:
            return {"ok": False, "why": "busy"}
        fut = asyncio.get_running_loop().create_future()
        self.waiters.setdefault(h["id"], []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "why": "timeout"}
        finally:
            fs = self.waiters.get(h["id"], [])
            if fut in fs:
                fs.remove(fut)

    def _triage_set(self, req: dict) -> dict:
        did, verdict = req.get("delivery"), req.get("verdict")
        if verdict not in ("continue", "ask", "spam") or self.store.delivery(did) is None:
            return {"ok": False, "why": "bad_triage"}
        self.store.doc["triage"][did] = {"v": verdict, "why": str(req.get("why") or "")[:200], "at": round(self.clock())}
        self.store.save()
        self.log("hook_triage", verdict=verdict)
        self.changed()
        return {"ok": True}

    # ------------------------------------------------------------ the phone (Web App 「设置 → Webhook」)
    async def on_phone(self, s, t: str, obj: dict) -> None:
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        if t == "hook_set":
            h = self.store.find(obj.get("id"))
            op = obj.get("op")
            if h is None or op not in ("pause", "resume", "revoke"):
                return await self.host.send_app(s, {"t": "hook_list_res", "r": r, "ok": False, "why": "shape"})
            if op == "revoke":
                await self.cli({"t": "revoke", "id": h["id"]})
            elif op == "pause":
                h["enabled"] = False
                self.store.save()
            else:                       # the owner on a paired phone turns back on what they approved before
                h["enabled"] = True
                self.store.save()
            self.log("hook_phone", device=s.device, op=op)
        rows = self.store.all()
        body = {"hooks": [{**self.view(h), "auth": h["auth"]["kind"], "seal": None} for h in rows if h["state"] in ("active", "pending")
                          or (h.get("ended") and self.clock() - h["ended"] < 86400)][:40],
                "recent": self.store.deliveries(None, 50), "up": self.up,
                "quota": {"used": self.store.used_today(), "day_max": hooks.DAY_MAX, "hooks_max": hooks.HOOKS_MAX}}
        await self.host.send_app(s, {"t": "hook_list_res", "r": r, "ok": True, **body})


def auth_text(keys, n, cert: str) -> str:
    """The hook socket's answer to the relay challenge (§17.2's shape, its own prefix)."""
    import re
    if not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", n):
        raise ValueError("bad challenge")
    sig = keys.ed.sign(f"agentj-hook-auth-v1\n{keys.mbox}\n{n}".encode())
    return json.dumps({"t": "auth", "x": wire.b64u(keys.x_pub), "pk": wire.b64u(keys.ed_pub), "sig": wire.b64u(sig),
                       "cert": cert}, separators=(",", ":"))


# ------------------------------------------------------------------ CLI side (owner terminal or an Agent, fenced or not)
def sock_paths() -> list[str]:
    out = []
    if os.environ.get(ENV):
        out.append(os.environ[ENV])
    with contextlib.suppress(Exception):
        from .state import State
        out.append(str(State().perm_dir / SOCK_NAME))
    return list(dict.fromkeys(out))


def ask(req: dict, timeout: float = 30) -> dict | None:
    for path in sock_paths():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                s.connect(path)
                s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                return json.loads(buf.split(b"\n", 1)[0])
            except (OSError, ValueError):
                continue
    return None


ROUTES = ("两条路线 / two routes：没有自己的云和域名 → 用托管 Webhook（agentj hook create）；有且需要完全自定义 → 自己搭"
          "（Cloudflare Worker / Lambda），验签后用 agentj hook forward 转进 Agent J。/ No cloud or domain of your own → hosted "
          "hooks; your own cloud and full control → build it, then forward the checked event in.")
HONEST = ("诚实说明 / honest note：托管 Webhook 的请求会在我们的中继内存里以明文经过——我们承诺不看、不存、不记录。"
          "隐私要求高的：发送方能自定义请求就用端到端加密投递（--encrypt），否则用自建路线（自己的 Cloudflare / AWS）。"
          "/ A hosted webhook's request passes through our relay's memory in the clear; we commit not to read, store or log it. "
          "For private data: encrypt end to end (--encrypt) when the sender shapes its own request, or build your own entry.")

WHY = {
    "no_hook": "找不到这个 hook（agentj hook list）/ no such hook",
    "quota_hooks": "已有 20 个生效中的 hook，先撤销不用的（agentj hook list · agentj hook revoke <id>）/ 20 live hooks: revoke one first",
    "quota_resident": "常驻 hook 已有 10 个，先撤销不用的 / 10 resident hooks already",
    "no_grant": "这张派单没有建 hook 的授权（或已用完）：请上级在派单时给 grants.hooks / the order has no hooks grant",
    "resident_needs_owner": "常驻 hook 只能由主人在手机上批准，派单不能建 / resident hooks need the owner's card",
    "bad_target": "目标不对：main · owner · wait · order:<单号> · bot:<id> / bad target",
    "bad_preset": "没有这个预置（agentj hook presets）/ unknown preset",
    "bad_life": "寿命只能是 once · ttl · resident / life is once, ttl or resident",
    "bad_duration": "时长格式：30m · 24h · 7d（一次性最长 7 天，临时最长 30 天）/ duration like 30m, 24h, 7d",
    "bad_auth": "验签参数不对（agentj hook presets 看可选值）/ bad signature settings",
    "bad_header": "请求头名字只能用字母、数字和 - / bad header name",
    "bad_ip": "IP 白名单格式：1.2.3.4 或 1.2.3.0/24，最多 64 条 / bad IP list",
    "bad_secret": "密钥文件内容不对（8–512 个字符；URL 令牌方式不收外来密钥）/ bad secret file",
    "bad_order": "单号格式不对 / bad order id", "not_yours": "这个 hook 不是这张派单建的 / not this order's hook",
    "too_long": "说明太长（≤ 200 字）/ note too long", "no_delivery": "找不到这条投递 / no such delivery",
    "bad_triage": "分诊：<投递号> continue|ask|spam / triage: <delivery> continue|ask|spam",
    "no_secret": "这个 hook 还没有密钥（Stripe：先 agentj hook rotate <id> --secret-file <whsec 文件>）/ no secret yet",
    "stopped": "已急停，恢复后再用 / stopped", "pending": "这个 hook 有一张卡在等主人批准，批完再改 / a card is waiting for the owner",
    "e2e_needs_hmac": "端到端加密只配 HMAC 且签 {时间戳}.{原文}（--auth hmac --signed ts.body；Bearer / URL 令牌会在密文旁明文经过；Stripe、GitHub 不能加密）/ --encrypt needs --auth hmac --signed ts.body",
    "not_e2e": "这个 hook 不是端到端加密的（agentj hook update <id> --encrypt）/ not an end-to-end hook",
    "busy": "等这个 hook 的进程太多了（最多 8 个）/ too many waiters on this hook", "gone": "hook 已作废 / the hook has ended", "shape": "参数不对 / bad request",
}


def _why(res: dict) -> str:
    w = res.get("why")
    return WHY.get(w, str(w)) + (f"（{res['detail']}）" if res.get("detail") else "")


def _write_secret(dest: str, value: str, hid: str) -> str:
    """--secret-out: file:<path> (whole file) or env:<file>#KEY (one KEY=value line); 0600, never a symlink, never inside the
    Agent J state directory. → the path written."""
    from .state import state_dir
    dest = dest or f"file:{SECRET_DIR}/{hid}.secret"
    kind, _, rest = dest.partition(":")
    key = None
    if kind == "env":
        rest, _, key = rest.partition("#")
        key = key or "AGENTJ_HOOK_SECRET"
        if not key.replace("_", "").isalnum():
            raise SystemExit("✗ env:<文件>#KEY 的 KEY 只能是变量名 / bad variable name")
    elif kind != "file":
        raise SystemExit("✗ --secret-out 写 file:<路径> 或 env:<文件>#KEY / --secret-out file:<path> or env:<file>#KEY")
    p = pathlib.Path(os.path.expanduser(rest)).absolute()
    sd = os.path.realpath(state_dir())
    real_parent = os.path.realpath(p.parent)
    if real_parent == sd or real_parent.startswith(sd.rstrip(os.sep) + os.sep):
        raise SystemExit("✗ 密钥不能写进 Agent J 的状态目录 / not inside Agent J's state directory")
    d = pathlib.Path(real_parent)
    home = pathlib.Path(os.path.realpath(os.path.expanduser("~")))
    for up in (d, *d.parents):                  # review L6: a secret inside a git work tree is one `git add .` from a leak
        if up == home or up == up.parent:       # (a dotfiles repo at ~ itself does not count)
            break
        if (up / ".git").exists():
            raise SystemExit("✗ 密钥不能写进 git 仓库目录（换个位置，如默认的 ~/.local/share/agentj-hooks）/ not inside a git work tree")
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.is_symlink():
        raise SystemExit("✗ 目标是符号链接 / the destination is a symlink")
    if kind == "env":
        lines = []
        with contextlib.suppress(FileNotFoundError):
            lines = [ln for ln in p.read_text().splitlines() if not ln.startswith(key + "=")]
        data = "\n".join(lines + [f"{key}={value}"]) + "\n"
    else:
        data = value + "\n"
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    return str(p) + (f"#{key}" if key else "")


def _read_secret(path: str) -> str:
    p = pathlib.Path(os.path.expanduser(path))
    if p.is_symlink() or not p.is_file():
        raise SystemExit("✗ 读不到密钥文件（不存在或是链接）/ secret file missing or a symlink")
    txt = p.read_text().strip()
    if "\n" in txt and "=" in txt.splitlines()[0]:     # an env file: the last KEY=value line
        txt = txt.splitlines()[-1].split("=", 1)[1].strip()
    elif txt.count("=") == 1 and txt.split("=", 1)[0].replace("_", "").isalnum():
        txt = txt.split("=", 1)[1].strip()
    return txt


def _call(req: dict, timeout: float = 30) -> dict:
    res = ask(req, timeout)
    if res is None:
        print("✗ Agent J 现在没在运行（agentj service status）/ Agent J is not running", file=sys.stderr)
        raise SystemExit(5)
    return res


def _fmt_hook(h: dict) -> str:
    exp = "" if not h.get("expires") else time.strftime(" · 到期 %m-%d %H:%M", time.localtime(h["expires"]))
    last = h.get("last") or {}
    lt = time.strftime("%m-%d %H:%M", time.localtime(last["at"])) + " " + str(last.get("result")) if last.get("at") else "—"
    state = {"active": "✓", "pending": "… 等主人批准", "used": "· 已用", "revoked": "· 已撤销", "expired": "· 已过期"}.get(
        h["state"], h["state"])
    if h["state"] == "active" and not h.get("enabled", True):
        state = "⏸ 暂停"
    auth = h["auth"]["kind"] if isinstance(h.get("auth"), dict) else h.get("auth")
    mode = (f"端到端加密 / end-to-end encrypted（kid {h['seal']['kid']}）" if h.get("e2e") and h.get("seal")
            else "明文经过中继 / plain through the relay")
    return (f"{state} {h['id'][:8]}  {h['name']}  [{h['preset']} · {h['life']} · {auth} → {h['target']}]{exp}\n"
            f"    {h['url']}\n    {mode}\n    最近：{lt} · 共 {h.get('count') or 0} 条")


def _seal_lines(h: dict) -> str:
    """What the sender needs for an end-to-end hook: the public key (not a secret) and the header."""
    s = h.get("seal") or {}
    return (f"  投递公钥 / delivery public key（X25519, b64url，可公开）: {s.get('pub')}\n"
            f"  发送方：HPKE 加密 body（{hooks.SEAL_SUITE}，info = \"agentj-hook-seal-v1\\n{h['id']}\"），"
            f"带请求头 {hooks.SEAL_HEADER}: {hooks.SEAL_V}; kid={s.get('kid')}；HMAC 签的是加密前的原文。示例：agentj-hooks 技能 / /docs/webhooks/")


def cmd_hook(a) -> None:
    order = a.order or os.environ.get("AGENTJ_ORDER_ID") or None
    sub = a.hcmd
    if sub == "presets":                   # static: no serve needed
        res = {"ok": True, "presets": hooks.PRESETS}
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
            return
        print(ROUTES + "\n")
        for k, p in res.get("presets", {}).items():
            print(f"  {k:9} {p['life']:8} {p['auth']['kind']:7} → {p['target']:6}  {p['zh']}")
        return
    if sub == "create":
        spec = {}
        if a.file:
            try:
                spec = json.loads(pathlib.Path(a.file).read_text())
            except (OSError, ValueError):
                raise SystemExit("✗ --file 读不到或不是 JSON / --file unreadable or not JSON")
            spec = {k: v for k, v in spec.items() if k not in ("id", "created", "approved", "state")}
        for k in ("preset", "name", "life", "ttl", "target", "note"):
            if getattr(a, k, None) is not None:
                spec[k] = getattr(a, k)
        auth = dict(spec.get("auth") or {})
        for k, v in (("kind", a.auth), ("header", a.header), ("algo", a.algo), ("encoding", a.encoding), ("prefix", a.prefix),
                     ("signed", a.signed), ("ts_header", a.ts_header), ("id_header", a.id_header)):
            if v is not None:
                auth[k] = v
        if auth:
            spec["auth"] = auth
        if a.ip:
            spec["ip_allow"] = a.ip
        if a.encrypt:
            spec["e2e"] = True
        req = {"t": "create", "hook": spec, **({"order": order} if order else {})}
        if a.secret_file:
            req["secret"] = _read_secret(a.secret_file)
        res = _call(req)
        if not res.get("ok"):
            raise SystemExit("✗ " + _why(res))
        h = res["hook"]
        where = None
        if res.get("secret"):
            where = _write_secret(a.secret_out, res["secret"], h["id"])
        if res.get("url_secret") and where:
            _write_secret(f"file:{os.path.dirname(where.split('#')[0])}/{h['id']}.url", res["url_secret"], h["id"])
        if a.json:
            print(json.dumps({"hook": h, "pending": bool(res.get("pending")), "secret_file": where,
                              "secret_source": res.get("secret_source"), "e2e": bool(h.get("e2e")),
                              "honest": HONEST}, ensure_ascii=False))
            return
        print(_fmt_hook(h))
        if h.get("e2e"):
            print(_seal_lines(h))
        if where:
            print(f"  密钥已写入 / secret written to: {where}（0600，不会打印；交给发起方时用 $(cat 路径)）")
            if res.get("url_secret"):
                print(f"  完整 URL（含令牌）/ full URL with token: {os.path.dirname(where.split('#')[0])}/{h['id']}.url")
        if res.get("secret_source") == "stripe_later":
            print("  Stripe 的签名密钥由 Stripe 生成：在 Stripe 后台建好 endpoint 后，用密钥卡把 whsec_… 存到文件，再运行\n"
                  f"  agentj hook rotate {h['id'][:8]} --secret-file <文件> --grace 0")
        if res.get("pending"):
            print("  常驻 hook：已发审批卡到主人手机，批准后才开始接收 / resident: the owner's approval card was sent")
        print("  " + HONEST)
        return
    if sub == "list":
        res = _call({"t": "list", "all": a.all})
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
            return
        q = res.get("quota") or {}
        print(f"Webhook：今天 {q.get('used', 0)}/{q.get('day_max')} 条 · 连接 {'在线' if res.get('up') else '未连接'}"
              + ("（需要有效的 Agent J 席位）" if (res.get("net") or {}).get("why") == "seat" else ""))
        for h in res.get("hooks", []):
            print(_fmt_hook(h))
        if not res.get("hooks"):
            print("  （还没有 hook：agentj hook create --preset callback …；agentj hook presets 看场景）")
        return
    if sub == "wait":
        t = a.timeout
        secs = 1800 if not t else int(t) if str(t).isdigit() else None
        if secs is None:
            from .hooks import parse_duration
            try:
                secs = parse_duration(t, 1800, WAIT_MAX)
            except Exception:  # noqa: BLE001
                raise SystemExit("✗ " + WHY["bad_duration"]) from None
        res = _call({"t": "wait", "id": a.id, "timeout": secs}, timeout=secs + 15)
        if res.get("ok"):
            print(json.dumps(res, ensure_ascii=False) if a.json else res["text"])
            return
        code = {"timeout": 2, "gone": 3, "stopped": 4}.get(res.get("why"), 1)
        print(("等到超时，没有投递 / timed out" if code == 2 else "✗ " + _why(res)), file=sys.stderr)
        raise SystemExit(code)
    if sub == "triage":
        res = _call({"t": "triage", "delivery": a.delivery, "verdict": a.verdict, "why": a.why or ""})
        if not res.get("ok"):
            raise SystemExit("✗ " + _why(res))
        print("✓ 已记录 / recorded")
        return
    base = {"id": a.id, **({"order": order} if order else {})}
    if sub == "show":
        res = _call({"t": "show", **base})
    elif sub == "logs":
        res = _call({"t": "logs", **base, "limit": a.limit, "show": a.show})
    elif sub == "revoke":
        res = _call({"t": "revoke", **base})
    elif sub == "update":
        u = {}
        for k in ("name", "target", "ttl", "note", "life"):
            if getattr(a, k, None) is not None:
                u[k] = getattr(a, k)
        if a.enable or a.disable:
            u["enabled"] = bool(a.enable)
        if a.ip or a.no_ip:
            u["ip_allow"] = [] if a.no_ip else a.ip
        if a.encrypt or a.no_encrypt:
            u["e2e"] = bool(a.encrypt)
        res = _call({"t": "update", **base, "set": u})
    elif sub == "rotate":
        req = {"t": "rotate", **base, "grace": a.grace, **({"enc_key": True} if a.enc_key else {})}
        if a.secret_file:
            req["secret"] = _read_secret(a.secret_file)
        res = _call(req)
        if res.get("ok") and res.get("secret"):
            res["secret_file"] = _write_secret(a.secret_out, res.pop("secret"), res["hook"]["id"])
            if res.get("url_secret"):
                _write_secret(f"file:{os.path.dirname(res['secret_file'].split('#')[0])}/{res['hook']['id']}.url",
                              res.pop("url_secret"), res["hook"]["id"])
    elif sub == "test":
        body = a.body or ""
        if body.startswith("@"):
            body = pathlib.Path(body[1:]).read_text()[:hooks.BODY_MAX]
        res = _call({"t": "test", **base, "body": body})
    elif sub == "forward":
        body = a.body or ""
        if body.startswith("@"):
            body = pathlib.Path(body[1:]).read_text()[:hooks.BODY_MAX]
        elif body == "-":
            body = sys.stdin.read(hooks.BODY_MAX + 1)
        heads = dict(x.split(":", 1) for x in (a.header or []) if ":" in x)
        res = _call({"t": "forward", **base, "body": body, "headers": {k.strip(): v.strip() for k, v in heads.items()},
                     "token": a.token or ""})
    else:
        raise SystemExit("用法：agentj hook {create|list|show|update|rotate|revoke|test|logs|wait|triage|forward|presets}")
    res.pop("secret", None)
    res.pop("url_secret", None)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return
    if not res.get("ok"):
        raise SystemExit("✗ " + (_why(res) if res.get("why") else f"HTTP {res.get('status')} {res.get('answer')}"))
    if res.get("text"):
        print(res["text"])
    elif res.get("hook"):
        print(_fmt_hook(res["hook"]))
        if res.get("pending"):
            print("  已发审批卡到主人手机，批准后生效 / sent to the owner's phone for approval")
        if res.get("secret_file"):
            print(f"  新密钥已写入 / new secret: {res['secret_file']}")
        if res["hook"].get("e2e") and (sub in ("show", "update") or res.get("enc_key")):
            print(_seal_lines(res["hook"]))
    if res.get("deliveries") is not None:
        for d in res["deliveries"]:
            tri = (d.get("triage") or {}).get("v") or ""
            print(f"  {d['id']}  {time.strftime('%m-%d %H:%M:%S', time.localtime(d['at']))}  {d['method']}  {d['bytes']} B  "
                  f"{d['result']}  {tri}")
    if "status" in res:
        print(f"✓ HTTP {res['status']} {json.dumps(res.get('answer'), ensure_ascii=False)}")


def add_parser(sub) -> None:
    hk = sub.add_parser("hook", help="Webhook：create · list · show · update · rotate · revoke · test · logs · wait · triage · "
                                     "forward · presets / webhooks", description=ROUTES)
    hs = hk.add_subparsers(dest="hcmd", required=True)
    c = hs.add_parser("create", help="建一个 hook（预置见 agentj hook presets）/ create a hook")
    c.add_argument("--preset", choices=sorted(hooks.PRESETS), help="场景预置 / scenario preset")
    c.add_argument("--name", help="名字（≤ 40 字）")
    c.add_argument("--life", choices=hooks.LIFE, help="once 一次性 · ttl 临时 · resident 常驻（要主人手机批准）")
    c.add_argument("--ttl", help="有效期：30m · 24h · 7d（once ≤ 7d，ttl ≤ 30d）")
    c.add_argument("--target", help="送到：main · owner · wait · order:<单号> · bot:<id>")
    c.add_argument("--auth", choices=hooks.AUTH_KINDS, help="验签方式 / signature check")
    c.add_argument("--header", help="签名 / 令牌所在的请求头")
    c.add_argument("--algo", choices=sorted(hooks.ALGOS), help="HMAC 算法")
    c.add_argument("--encoding", choices=("hex", "base64"), help="HMAC 编码")
    c.add_argument("--prefix", help="签名前缀，如 sha256=")
    c.add_argument("--signed", choices=("body", "ts.body"), help="签名内容")
    c.add_argument("--ts-header", dest="ts_header", help="时间戳请求头（ts.body 用）")
    c.add_argument("--id-header", dest="id_header", help="去重用的事件 id 请求头")
    c.add_argument("--ip", action="append", help="来源 IP / CIDR 白名单（可重复）")
    c.add_argument("--note", help="给主 Agent 的一句话用途说明（≤ 200 字）")
    c.add_argument("--secret-file", dest="secret_file", help="用别人生成的密钥（如 Stripe 的 whsec_…），从这个文件读")
    c.add_argument("--secret-out", dest="secret_out", help=f"生成的密钥写到 file:<路径> 或 env:<文件>#KEY（默认 {SECRET_DIR}/<id>.secret）")
    c.add_argument("--order", help="派单 CEO / sub-agent：给自己的单建（默认读 AGENTJ_ORDER_ID）")
    c.add_argument("--encrypt", action="store_true", help="端到端加密投递：发送方用这个 hook 的公钥加密 body，中继只转发密文"
                   "（只配 --auth hmac，签名默认 ts.body；Stripe / GitHub 不能用）/ end-to-end encrypted delivery")
    c.add_argument("--file", help="hook 定义 JSON（hooks.schema.json），参数覆盖文件")
    c.add_argument("--json", action="store_true")
    li = hs.add_parser("list", help="生效中的 hook、最近投递、今天用量")
    li.add_argument("--all", action="store_true", help="含 7 天内作废的")
    li.add_argument("--json", action="store_true")
    for name, hlp in (("show", "一个 hook 的定义 + 最近 10 条投递"), ("revoke", "立刻作废"),
                      ("test", "按这个 hook 的方式自签一条走一遍（不占额度）"), ("logs", "投递记录"),
                      ("update", "改名字 / 目标 / 有效期 / IP / 开关；放宽常驻要主人批准"), ("rotate", "换密钥（旧的宽限期内仍有效）"),
                      ("forward", "自建入口验签后把事件转进来（本机）")):
        p = hs.add_parser(name, help=hlp)
        p.add_argument("id", help="hook id（前 8 位或名字也行）")
        p.add_argument("--order", help="派单号（默认读 AGENTJ_ORDER_ID）")
        p.add_argument("--json", action="store_true")
        if name == "logs":
            p.add_argument("--limit", type=int, default=20)
            p.add_argument("--show", help="看一条投递原文（包信封）")
        if name in ("test", "forward"):
            p.add_argument("--body", help="载荷文字，@文件 读文件" + ("，- 读标准输入" if name == "forward" else ""))
        if name == "forward":
            p.add_argument("--header", action="append", help="请求头 K: V（可重复；按这个 hook 的验签方式带上签名 / 令牌）")
            p.add_argument("--token", help="URL 令牌方式的令牌")
        if name == "update":
            p.add_argument("--name")
            p.add_argument("--target")
            p.add_argument("--ttl")
            p.add_argument("--note")
            p.add_argument("--life", choices=("resident",), help="改成常驻（要主人手机批准）")
            p.add_argument("--ip", action="append")
            p.add_argument("--no-ip", dest="no_ip", action="store_true", help="去掉 IP 白名单（常驻要批准）")
            g = p.add_mutually_exclusive_group()
            g.add_argument("--enable", action="store_true")
            g.add_argument("--disable", action="store_true")
            e = p.add_mutually_exclusive_group()
            e.add_argument("--encrypt", action="store_true", help="改成端到端加密投递（立即生效）")
            e.add_argument("--no-encrypt", dest="no_encrypt", action="store_true", help="改回明文经过中继（常驻要批准）")
        if name == "rotate":
            p.add_argument("--grace", default="24h", help="旧密钥宽限：0 · 1h · 24h · 7d（默认 24h）")
            p.add_argument("--secret-file", dest="secret_file", help="换成别人生成的新密钥（如 Stripe）")
            p.add_argument("--secret-out", dest="secret_out", help="新密钥写到哪（同 create）")
            p.add_argument("--enc-key", dest="enc_key", action="store_true",
                           help="换端到端加密的投递密钥对（新公钥要交给发送方；旧私钥宽限期内仍能解密）")
    w = hs.add_parser("wait", help="挂起等这个 hook 的下一条投递（退出码 0 有 · 2 超时 · 3 已作废 · 4 急停）")
    w.add_argument("id")
    w.add_argument("--timeout", default="30m", help="最长等多久：90s · 30m · 24h（默认 30m）")
    w.add_argument("--json", action="store_true")
    t = hs.add_parser("triage", help="记录主 Agent 的分诊结论")
    t.add_argument("delivery", help="投递号 d…")
    t.add_argument("verdict", choices=("continue", "ask", "spam"))
    t.add_argument("--why", help="一句话理由")
    pr = hs.add_parser("presets", help="场景预置与默认值")
    pr.add_argument("--json", action="store_true")
    for p in (li, w, t, pr):
        p.set_defaults(order=None)
    hk.set_defaults(fn=cmd_hook)
