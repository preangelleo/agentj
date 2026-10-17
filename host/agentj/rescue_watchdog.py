"""The rescue watchdog (P332, PROTOCOL §33 "Rescue watchdog"): a tiny process next to `agentj serve`, in its own service unit
(`agentj-rescue.service` / launchd `net.agentj.rescue`), so the phone's 「紧急修复」 still works when serve is dead or its event
loop is starved — the relay tunnel is serve's, and it goes down with it.

Every POLL s it asks serve's control socket `status` (CTL_TIMEOUT s). TAKEOVER failures in a row → it opens the relay
**host** socket for this channel itself (same Ed25519 host key, cached seat certificate), accepts RESUME (§3, IK) from
allowlisted devices only — never a pairing — and answers `ping`, signed `reset` and signed `rescue_do` (§8 control-v1: ready
session, approval key, ts window, nonce once). HANDBACK answers in a row → `rescue on:false` + its socket closes (serve's own
link replaces it anyway: relay close 4001). After a 4001 it needs TAKEOVER fresh failures before it takes over again; a
4001 within FLAP_S of its own takeover proves serve is alive, so that threshold doubles each time (≤ MAX_BACKOFF_S of
polls) until serve answers `status` HANDBACK times in a row. When the serve unit is inactive with Result=success (an
intentional stop: `systemctl --user stop`), the watchdog stays passive — no takeover, and an active one hands back.

Never imports serve (a test checks sys.modules). Nothing the phone sends becomes part of a command line: an action is a name
on ACTIONS, run by fixed code. One line per request / result in `<state>/rescue.log` (0600; no message text, no key).
It holds no more rights than serve: same user, same state directory, read-only on the allowlist.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import sys
import time

from . import controls, wire
from .noise import IK, Handshake

POLL = 15.0
CTL_TIMEOUT = 3.0
TAKEOVER = 3
HANDBACK = 2
FLAP_S = 60.0            # a 4001 this soon after our takeover = serve is alive: back off (review P332 LOW-7)
MAX_BACKOFF_S = 600.0    # the takeover threshold never grows past 10 min of polls
RESTART_WAIT = 60.0
HS_TTL = 30.0
AUTH_TIMEOUT = 8.0
OPEN_TIMEOUT = 10.0
MAX_SESSIONS = 64
LOG_MAX = 256 * 1024
CLOSE_REPLACED, CLOSE_SEAT = 4001, 4002
ACTIONS = ("help", "report", "reconnect", "restart", "linger", "rollback", "new_session")
HELP_ACTIONS = ("reconnect", "restart")   # what support's AGENTJ-ACTIONS may RUN (review P332 HIGH-1; the rest is offered)
_R16 = re.compile(r"[0-9a-f]{16}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")

TEXT = {
    "restart": ("Agent J 主机上的 Agent J 没响应，正在重启它…", "Agent J on the Agent J host is not responding — restarting it…"),
    "restart_fail": ("没能重启Agent J 主机上的 Agent J。可以试第 ② 步：请 Agent J Father 远程帮忙。",
                     "Could not restart Agent J on the Agent J host. Try step ②: ask Agent J Father for help."),
    "restarted": ("已经让Agent J 主机上的 Agent J 重新启动。", "Agent J on the Agent J host was told to restart."),
    "wait": ("正在等 Agent J 重新上线（最多 1 分钟）…", "Waiting for Agent J to come back (up to a minute)…"),
    "back": ("好了，Agent J 已经重新启动，可以回去聊天了。", "Done — Agent J has restarted. You can go back to the chat."),
    "not_back": ("Agent J 重启了，但 1 分钟内没有恢复。建议试第 ② 步：请 Agent J Father 远程帮忙。",
                 "Agent J restarted but did not come back within a minute. Try step ②: ask Agent J Father for help."),
    "unavailable": ("Agent J 主机上的 Agent J 现在没在运行，这一步做不了。先试「重置连接」。",
                    "Agent J is not running on the Agent J host, so this step cannot run now. Try \"Reset connection\" first."),
    "linger": ("正在设置：Agent J 主机重启后不用登录，Agent J 也会自己运行…", "Setting Agent J to start after a reboot without a login…"),
    "linger_ok": ("已设置好：Agent J 主机重启后不用登录，Agent J 也会自己运行。", "Done: Agent J now starts after a reboot without a login."),
    "linger_fail": ("没能设置开机自动运行。可以请 Agent J Father 远程帮忙。", "Could not set Agent J to start at boot. Ask Agent J Father for remote help."),
    "rollback": ("正在换回上一个版本的 Agent J…", "Going back to the previous version of Agent J…"),
    "rollback_ok": ("已换回上一个版本的 Agent J。", "Agent J is back on the previous version."),
    "rollback_none": ("这台Agent J 主机上没有可以换回的旧版本。", "There is no earlier version on the Agent J host to go back to."),
    "rollback_fail": ("没能换回上一个版本。可以请 Agent J Father 远程帮忙。", "Could not go back to the previous version. Ask Agent J Father for remote help."),
    "report": ("正在检查这台Agent J 主机，并把报告发给 Agent J Father…", "Checking the Agent J host and sending the report to Agent J Father…"),
    "report_ok": ("报告已发给 Agent J Father，单号 {t}。", "The report reached Agent J Father — ticket {t}."),
    "report_fail": ("报告没能发出去：这台Agent J 主机可能连不上网。", "The report could not be sent: the Agent J host may be offline."),
    "help_fail": ("没能联系上 Agent J Father：这台Agent J 主机可能连不上网。", "Could not reach Agent J Father: the Agent J host may be offline."),
}


def say(key: str, lang: str, **kw) -> str:
    zh, en = TEXT[key]
    return (en if lang == "en" else zh).format(**kw)


# ------------------------------------------------------------------ the signed object (§8 control-v1)
# The same check serve runs (controls.check): reset's object text is "connection", rescue_do's is the action's name.
def object_digest(action: str, target: dict) -> str:
    return controls.object_digest(action, target)


def check(st, nonces, channel: str, device: str, obj: dict, action: str, target: dict) -> str | None:
    """None = may run, else shape · stale · no_key · bad_signature · replay."""
    return controls.check(st, nonces, channel, device, obj, action, target)


# ------------------------------------------------------------------ serve probe and the fixed actions (blocking; run in a thread)
def probe(st, timeout: float = CTL_TIMEOUT) -> str:
    """"ok" (serve answered `status`) · "down" (no control socket / refused) · "stall" (there, but no answer in time)."""
    from . import names
    try:
        return "down" if names.ctl_call(st, {"cmd": "status"}, timeout) is None else "ok"
    except names.ServeBusy:
        return "stall"
    except Exception:  # noqa: BLE001 — any other failure to talk to it counts as not answering
        return "stall"


def serve_stopped_on_purpose() -> bool:
    """Linux: the serve unit is loaded, inactive, Result=success — someone stopped it (serve never exits 0 by itself and its
    unit restarts it on failure). Then the watchdog must not take the channel over (review P332 MEDIUM-2). Anything else,
    other systems and every error → False (the watchdog keeps watching)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        from . import service
        r = service._systemctl("show", f"{service.name()}.service", "-p", "LoadState,ActiveState,Result", timeout=5)
        props = dict(l.split("=", 1) for l in (r.stdout or "").splitlines() if "=" in l)
    except Exception:  # noqa: BLE001 — no systemctl, a bad name, a timeout: not a reason to stay passive
        return False
    return (r.returncode == 0 and props.get("LoadState") == "loaded" and props.get("ActiveState") == "inactive"
            and props.get("Result") == "success")


def _norm(res, default_text: str = "") -> tuple[bool, str]:
    """reset.py's runners may answer bool, (ok, text) or {"ok", "text"}."""
    if isinstance(res, tuple) and res:
        return bool(res[0]), str(res[1]) if len(res) > 1 and res[1] else default_text
    if isinstance(res, dict):
        return bool(res.get("ok")), str(res.get("text") or default_text)
    return bool(res), default_text


def _reset_fn(name: str):
    try:
        from . import reset as reset_mod
    except Exception:  # noqa: BLE001 — another engineer's module may not be there (yet): fall back
        return None
    return getattr(reset_mod, name, None)


def restart_serve(st) -> bool:
    fn = _reset_fn("run_restart_serve")
    if fn:
        try:
            return _norm(fn(st))[0]
        except Exception:  # noqa: BLE001
            return False
    from . import service
    try:
        service.restart()
        return True
    except Exception:  # noqa: BLE001 — ServiceError, OSError, no service manager: a plain "could not"
        return False


def enable_linger(st) -> bool:
    fn = _reset_fn("run_linger")
    if fn:
        try:
            return _norm(fn(st))[0]
        except Exception:  # noqa: BLE001
            return False
    import getpass
    import shutil
    import subprocess
    exe = shutil.which("loginctl")
    if not sys.platform.startswith("linux") or not exe:
        return False
    try:
        user = getpass.getuser()        # this process's own user — never a name from the phone
        return subprocess.run([exe, "enable-linger", user], capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError, KeyError):
        return False


def owner_lang() -> str:
    try:
        from . import preferences
        v = preferences.get(preferences.validate(preferences.read()[1]), "appearance.language", "zh")
        return "en" if v == "en" else "zh"
    except Exception:  # noqa: BLE001 — never a reason not to answer
        return "zh"


# ------------------------------------------------------------------ one phone session
class Sess:
    __slots__ = ("cid", "state", "send", "recv", "pub", "device", "p33", "timer")

    def __init__(self, cid: int):
        self.cid, self.state = cid, "new"
        self.send = self.recv = None
        self.pub, self.device, self.p33, self.timer = b"", "", False, None


class Watchdog:
    def __init__(self, st, *, poll: float = POLL, probe_fn=None, restart_fn=None, linger_fn=None, kit=None,
                 restart_wait: float = RESTART_WAIT, lang_fn=owner_lang, stopped_fn=serve_stopped_on_purpose,
                 clock=time.monotonic):
        self.st, self.poll = st, poll
        self.probe_fn = probe_fn or (lambda: probe(st))
        self.stopped_fn, self.clock = stopped_fn, clock
        self.need = TAKEOVER     # failures in a row before a takeover (doubles after a flapping takeover)
        self.taken_at = None     # clock() of our last takeover
        self.passive = False     # the serve unit was stopped on purpose
        self.restart_fn = restart_fn or (lambda: restart_serve(st))
        self.linger_fn = linger_fn or (lambda: enable_linger(st))
        self.kit, self.restart_wait, self.lang_fn = kit, restart_wait, lang_fn
        self.fails = self.goods = 0
        self.since = 0           # ms: start of the failure streak that led to the takeover
        self.why = "serve_down"
        self.active = False      # we hold (or are trying to hold) the relay host socket
        self.ws = None
        self.sessions: dict[int, Sess] = {}
        self.nonces = controls.Nonces()
        self.busy = False
        self.send_lock = asyncio.Lock()
        self.relay_task: asyncio.Task | None = None
        self.stopping = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        cfg = st.config()
        self.channel, self.relay = cfg["channel"], (cfg.get("relay") or "").rstrip("/")
        self.kp, self.sk = st.host_keypair(), st.signing_key()

    # ------------------------------------------------------------ rescue.log (metadata only)
    def log(self, ev: str, **kw) -> None:
        rec = {"ts": int(time.time()), "ev": ev, **{k: v for k, v in kw.items() if v is not None}}
        p = self.st.root / "rescue.log"
        try:
            if p.exists() and p.stat().st_size > LOG_MAX:
                os.replace(p, p.with_name("rescue.log.1"))
            self.st.append_private(p, json.dumps(rec, ensure_ascii=False))
        except OSError:
            pass

    # ------------------------------------------------------------ the counter (pure: tests drive it directly)
    def observe(self, result: str) -> str | None:
        """One `status` probe result ("ok" · "down" · "stall" · "stopped" = the unit was stopped on purpose) →
        "takeover" | "handback" | None."""
        if result == "stopped":
            self.fails = self.goods = 0
            return "handback" if self.active else None
        if result == "ok":
            self.fails, self.goods = 0, self.goods + 1
            if self.goods >= HANDBACK:
                self.need = TAKEOVER          # serve answers again: the flapping back-off is over
            if self.active and self.goods >= HANDBACK:
                return "handback"
            return None
        self.goods = 0
        if self.fails == 0:
            self.since = int(time.time() * 1000)
        self.fails += 1
        self.why = "serve_down" if result == "down" else "serve_stall"
        if not self.active and self.fails >= self.need:
            return "takeover"
        return None

    def replaced(self) -> None:
        """Relay close 4001: serve's own link took the channel. Wait for `need` fresh failures; a 4001 within FLAP_S of our
        takeover proves serve is alive (its control socket is the broken part), so `need` doubles, ≤ MAX_BACKOFF_S of polls."""
        self.active, self.fails, self.goods = False, 0, 0
        if self.taken_at is not None and self.clock() - self.taken_at < FLAP_S:
            cap = max(TAKEOVER, int(MAX_BACKOFF_S / max(self.poll, 0.001)))
            self.need = min(self.need * 2, cap)
            self.log("backoff", count=self.need)

    # ------------------------------------------------------------ main loop
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.log("start")
        while not self.stopping.is_set():
            res = await asyncio.to_thread(self.probe_fn)
            if res != "ok" and await asyncio.to_thread(self._stopped):
                res = "stopped"
            if (res == "stopped") != self.passive:
                self.passive = res == "stopped"
                self.log("passive" if self.passive else "watching")
            step = self.observe(res)
            if step == "takeover":
                self.log("takeover", why=self.why, count=self.fails)
                self.active, self.taken_at = True, self.clock()
                self.relay_task = asyncio.create_task(self.relay_loop())
            elif step == "handback" and not self.busy:   # a running action finishes (and is answered) first
                self.log("handback")
                await self.handback()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), self.poll)
        await self.handback()

    def _stopped(self) -> bool:
        try:
            return bool(self.stopped_fn and self.stopped_fn())
        except Exception:  # noqa: BLE001
            return False

    async def handback(self) -> None:
        self.active = False
        for s in list(self.sessions.values()):
            if s.state == "ready":
                with contextlib.suppress(Exception):
                    await self.send_app(s, {"t": "rescue", "on": False})
        ws = self.ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close(1000)
        t = self.relay_task
        if t and t is not asyncio.current_task():
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        self.relay_task = None

    async def relay_loop(self) -> None:
        from websockets.asyncio.client import connect
        from . import seatcert
        url = f"{self.relay}/v1/host/{self.channel}"
        backoff = 2.0
        while self.active and not self.stopping.is_set():
            code, phase = None, "connect"
            try:
                cert = await asyncio.to_thread(seatcert.SeatCert(self.st, self.relay).cert)
                async with connect(url, max_size=2**17, ping_interval=20, ping_timeout=20, open_timeout=OPEN_TIMEOUT) as ws:
                    phase = "auth"
                    await self._auth(ws, cert)
                    phase, self.ws, backoff = "up", ws, 2.0
                    self.log("relay_up")
                    try:
                        async for msg in ws:
                            if isinstance(msg, bytes):
                                await self.on_frame(msg)
                    finally:
                        code = ws.close_code
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — network, refusal: back off and retry while still needed
                from .relaydiag import close_code
                code = close_code(e) or code
            finally:
                self.ws = None
                self._drop_all()
            self.log("relay_down", phase=phase, code=code)
            if code == CLOSE_REPLACED:
                self.replaced()
                return
            if not self.active:
                return
            wait = 60.0 if code == CLOSE_SEAT else backoff
            backoff = min(backoff * 2, 60.0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), wait)

    async def _auth(self, ws, cert) -> None:
        msg = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT))
        if msg.get("t") != "challenge" or not isinstance(msg.get("n"), str) or not _NONCE.fullmatch(msg["n"]):
            raise ValueError("bad challenge")      # sign only a nonce of the relay's exact shape (F10)
        sig = self.sk.sign(f"agentjarvis-relay-auth-v1\n{self.channel}\n{msg['n']}".encode())
        auth = {"t": "auth", "pk": wire.b64u(self.st._ed_pub(self.sk)), "sig": wire.b64u(sig)}
        if cert:
            auth["cert"] = cert
        await ws.send(json.dumps(auth))
        ok = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT))
        if ok.get("t") != "ok":
            raise ValueError("relay refused auth")

    # ------------------------------------------------------------ frames
    def _drop_all(self) -> None:
        for s in self.sessions.values():
            if s.timer:
                s.timer.cancel()
        self.sessions.clear()

    async def _op(self, op: int, cid: int, payload: bytes = b"") -> None:
        ws = self.ws
        if ws is None:
            raise ConnectionError("relay down")
        await ws.send(bytes([op]) + cid.to_bytes(4, "big") + payload)

    async def close_cid(self, cid: int, reason: str) -> None:
        s = self.sessions.pop(cid, None)
        if s and s.timer and s.timer is not asyncio.current_task():
            s.timer.cancel()
        with contextlib.suppress(Exception):
            await self._op(wire.OP_CLOSE, cid)
        self.log("close", device=s.device if s else None, reason=reason)

    async def send_app(self, s: Sess, obj: dict) -> bool:
        async with self.send_lock:
            if self.sessions.get(s.cid) is not s or s.send is None:
                return False
            ct = s.send.encrypt(b"", wire.pad_json(obj, wire.MAX_JSON_P33 if s.p33 else wire.MAX_JSON))
            await self._op(wire.OP_DATA, s.cid, bytes([wire.DATA]) + ct)
            return True

    async def on_frame(self, f: bytes) -> None:
        if len(f) < 5:
            return
        op, cid, payload = f[0], int.from_bytes(f[1:5], "big"), f[5:]
        if op == wire.OP_GONE:
            s = self.sessions.pop(cid, None)
            if s and s.timer:
                s.timer.cancel()
            return
        if op not in (wire.OP_OPEN, wire.OP_DATA):
            return
        s = self.sessions.get(cid)
        if s is None:
            if len(self.sessions) >= MAX_SESSIONS:
                return await self.close_cid(cid, "too_many_sessions")
            s = self.sessions[cid] = Sess(cid)
            s.timer = asyncio.create_task(self._hs_timeout(s))
        if op == wire.OP_DATA:
            try:
                await self.on_data(s, payload)
            except Exception as e:  # noqa: BLE001 — a peer's bad frame costs only its own session
                await self.close_cid(cid, "bad_frame:" + type(e).__name__)

    async def _hs_timeout(self, s: Sess) -> None:
        await asyncio.sleep(HS_TTL)
        if self.sessions.get(s.cid) is s and s.state != "ready":
            await self.close_cid(s.cid, "handshake_timeout")

    async def on_data(self, s: Sess, data: bytes) -> None:
        if not data:
            raise ValueError("empty")
        kind, body = data[0], data[1:]
        if s.state == "new" and kind == wire.RESUME_INIT:
            hs = Handshake(IK, False, self.kp, prologue=wire.resume_prologue(self.channel))
            hs.read_message(body)
            if not self.st.is_allowed(hs.rs):        # allowlist only; no passkey / renewal restore, no `removed` here
                self.log("refused", device=wire.device_id(hs.rs), why="not_paired")
                return await self.close_cid(s.cid, "unknown_device")
            msg2 = hs.write_message(b"")
            s.send, s.recv, _ = hs.split()
            s.pub, s.device, s.state = hs.rs, wire.device_id(hs.rs), "hello"
            await self._op(wire.OP_DATA, s.cid, bytes([wire.HS_RESP]) + msg2)
        elif s.state == "new" and kind == wire.PAIR_INIT:
            self.log("refused", why="pairing")       # pairing is never accepted by the watchdog
            await self.close_cid(s.cid, "pairing_refused")
        elif kind == wire.DATA and s.state in ("hello", "ready"):
            obj = wire.unpad_json(s.recv.decrypt(b"", body), wire.MAX_JSON_P33 if s.p33 else wire.MAX_JSON)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            await self.on_app(s, obj)
        else:
            await self.close_cid(s.cid, "unexpected_kind")

    def available(self) -> list:
        """The actions this watchdog can carry out now (the page offers these; the others answer `unavailable`)."""
        out = ["restart", "help", "report"]
        if sys.platform.startswith("linux"):
            out.append("linger")
        if _reset_fn("run_rollback"):
            out.append("rollback")
        return out

    def rescue_msg(self) -> dict:
        return {"t": "rescue", "on": True, "why": self.why, "since": self.since, "actions": self.available()}

    async def on_app(self, s: Sess, obj: dict) -> None:
        t = obj.get("t")
        if not self.st.is_allowed(s.pub):            # revoked meanwhile
            return await self.close_cid(s.cid, "revoked")
        if s.state == "hello":
            if t != "hello":
                return await self.close_cid(s.cid, "expected_hello")
            caps = obj.get("caps")
            s.p33 = isinstance(caps, list) and wire.CAP_P33 in caps[:16]
            s.state = "ready"
            if s.timer:
                s.timer.cancel()
                s.timer = None
            self.log("resume_ok", device=s.device)
            ready = {"t": "ready", "mode": "rescue"}
            if s.p33:
                ready["caps"] = [wire.CAP_P33, "heartbeat"]
            await self.send_app(s, ready)
            await self.send_app(s, self.rescue_msg())
            return
        if t == "ping":
            if wire.is_rid(obj.get("r")):
                await self.send_app(s, {"t": "pong", "r": obj["r"]})
        elif t in ("reset", "rescue_do"):
            await self.on_request(s, t, obj)
        else:
            await self.send_app(s, self.rescue_msg())

    # ------------------------------------------------------------ signed requests
    async def on_request(self, s: Sess, t: str, obj: dict) -> None:
        r = obj.get("r")
        if not isinstance(r, str) or not _R16.fullmatch(r):
            self.log("request", device=s.device, action=t, result="refused", why="shape")
            return
        if t == "reset":
            action, target, name = "reset", {"what": "connection"}, "restart"
        else:
            name = obj.get("action")
            if not isinstance(name, str) or name not in ACTIONS:   # never the phone's string in the log (review LOW-6)
                return await self.refuse(s, r, "reset", t, "shape", "unknown")
            action, target = "rescue_do", {"action": name}
        flow = "help" if name in ("help", "report") else "reset"
        why = "shape" if not s.p33 else check(self.st, self.nonces, self.channel, s.device, obj, action, target)
        if why is None and self.busy:
            why = "busy"
        if why:
            return await self.refuse(s, r, flow, action, why, name)
        self._controls_log(action, s.device, "ok", target, obj)
        self.log("request", device=s.device, action=name, result="accepted")
        self.busy = True
        from . import bg   # light (asyncio + time): holds the task and logs a failure without its text
        bg.spawn(self._run_action(s, r, flow, name), "rescue_action", None)

    async def refuse(self, s: Sess, r: str, flow: str, action: str, why: str, name: str | None) -> None:
        self._controls_log(action, s.device, "refused:" + why, None, None)
        self.log("request", device=s.device, action=name or action, result="refused", why=why)
        await self.send_app(s, {"t": "reset_done", "r": r, "flow": flow, "ok": False, "why": why})

    def _controls_log(self, action, device, result, target, obj) -> None:
        rec = {"ts": int(time.time()), "action": action, "device": device, "result": result, "by": "rescue_watchdog"}
        if target is not None:
            rec["object_sha256"] = object_digest(action, target)
        if obj:
            rec["n"], rec["sig_ts"], rec["sig"] = obj.get("n"), obj.get("ts"), obj.get("sig")
        with contextlib.suppress(OSError):
            self.st.append_private(self.st.controls_path, json.dumps(rec, ensure_ascii=False))

    def _emit(self, s: Sess, r: str, flow: str):
        """A thread-safe progress callback for one request: emit(step, state, text, offer=None)."""
        loop = self.loop

        def emit(step: int, state: str, text: str, offer=None) -> None:
            msg = {"t": "reset_step", "r": r, "flow": flow, "step": int(step), "state": str(state), "text": str(text)[:400]}
            offer = _offer(offer)
            if offer:
                msg["offer"] = offer
            fut = asyncio.run_coroutine_threadsafe(self.send_app(s, msg), loop)
            with contextlib.suppress(Exception):
                fut.result(10)
        return emit

    async def _run_action(self, s: Sess, r: str, flow: str, name: str) -> None:
        lang = await asyncio.to_thread(self.lang_fn)
        emit = self._emit(s, r, flow)
        try:
            done = await asyncio.to_thread(self.do, name, lang, emit)
        except Exception as e:  # noqa: BLE001 — never takes the watchdog down
            self.log("result", action=name, result="error", why=type(e).__name__)
            done = {"ok": False, "step": 1, "text": say("help_fail" if flow == "help" else "restart_fail", lang)}
        finally:
            self.busy = False
        self.log("result", device=s.device, action=name, result="ok" if done.get("ok") else "fail",
                 why=done.get("why"), step=done.get("step"))
        msg = {"t": "reset_done", "r": r, "flow": flow, **{k: v for k, v in done.items() if k in
                                                            ("ok", "step", "text", "why", "ticket", "category")}}
        offer = _offer(done.get("offer"))
        if offer:
            msg["offer"] = offer
        await self.send_app(s, msg)
        for o in list(self.sessions.values()):        # every other ready p33 remote hears the outcome too
            if o is not s and o.state == "ready" and o.p33:
                with contextlib.suppress(Exception):
                    await self.send_app(o, msg)

    # ------------------------------------------------------------ the whitelist (blocking; in a worker thread)
    def do(self, name: str, lang: str, emit) -> dict:
        if name == "restart":
            return self._restart(lang, emit)
        if name == "linger":
            emit(1, "run", say("linger", lang))
            ok = bool(self.linger_fn())
            emit(1, "ok" if ok else "fail", say("linger_ok" if ok else "linger_fail", lang))
            return {"ok": ok, "step": 1, "text": say("linger_ok" if ok else "linger_fail", lang)}
        if name == "rollback":
            fn = _reset_fn("run_rollback")
            if fn is None:
                return self._unavailable(lang)
            emit(1, "run", say("rollback", lang))
            try:
                res = fn(self.st)
            except Exception:  # noqa: BLE001
                res = {"ok": False}
            ok = _norm(res)[0]
            key = "rollback_ok" if ok else "rollback_none" if isinstance(res, dict) and res.get("why") == "unavailable" \
                else "rollback_fail"
            emit(1, "ok" if ok else "fail", say(key, lang))
            return {"ok": ok, "step": 1, "text": say(key, lang), **({} if ok or key != "rollback_none" else {"why": "unavailable"})}
        if name in ("reconnect", "new_session"):
            return self._unavailable(lang)            # both need a running Agent J (serve does them, §33 ①)
        if name == "report":
            return self._report(lang, emit)
        if name == "help":
            return self._help(lang, emit)
        return {"ok": False, "why": "shape", "step": 0, "text": ""}   # unreachable: on_request admits only ACTIONS

    def _unavailable(self, lang: str) -> dict:
        return {"ok": False, "why": "unavailable", "step": 0, "text": say("unavailable", lang)}

    def _restart(self, lang: str, emit) -> dict:
        emit(1, "run", say("restart", lang))
        if not self.restart_fn():
            emit(1, "fail", say("restart_fail", lang))
            return {"ok": False, "step": 1, "text": say("restart_fail", lang)}
        emit(1, "ok", say("restarted", lang))
        emit(2, "run", say("wait", lang))
        end = time.monotonic() + self.restart_wait
        while time.monotonic() < end:
            if self.probe_fn() == "ok":
                emit(2, "ok", say("back", lang))
                return {"ok": True, "step": 2, "text": say("back", lang)}
            time.sleep(min(1.0, max(0.05, self.restart_wait / 60)))
        emit(2, "fail", say("not_back", lang))
        return {"ok": False, "step": 2, "text": say("not_back", lang)}

    def _kit(self):
        if self.kit is not None:
            return self.kit
        from . import rescue_kit
        return rescue_kit

    def _report(self, lang: str, emit) -> dict:
        emit(1, "run", say("report", lang))
        ticket, _healthy, _err = self._kit().report_only(lang, tag="watchdog")
        key = "report_ok" if ticket else "report_fail"
        emit(1, "ok" if ticket else "fail", say(key, lang, t=ticket or ""))
        return {"ok": bool(ticket), "step": 1, "text": say(key, lang, t=ticket or ""), **({"ticket": ticket} if ticket else {})}

    def _help(self, lang: str, emit) -> dict:
        kit = self._kit()
        flow = getattr(kit, "help_flow", None)
        if flow is None:                                # rescue_kit without the help flow: a report is the next best thing
            return self._report(lang, emit)

        def run_action(action: str) -> tuple[bool, str]:
            if action not in HELP_ACTIONS:             # support's word runs only reconnect / restart; the rest is offered
                return False, "not allowed"
            res = self.do(action, lang, lambda *_a, **_k: None)
            return bool(res.get("ok")), str(res.get("text") or "")
        estop = bool(controls.estop_state(self.st).get("on"))   # during an e-stop: diagnose + report, nothing runs
        res = flow(lang, emit, run_action, with_doctor=False, tag="help", estop=estop)
        res = res if isinstance(res, dict) else {}
        step = res.get("step")
        out = {"ok": bool(res.get("ok")), "step": step if type(step) is int else 3,
               "text": str(res.get("text") or (say("help_fail", lang) if not res.get("ticket") else ""))}
        for k in ("ticket", "category"):
            if isinstance(res.get(k), str):
                out[k] = res[k]
        offer = _offer(res.get("offer"))
        if offer:
            out["offer"] = offer
        return out


def _offer(offer) -> list:
    """`offer` on reset_step / reset_done: only action names the owner can confirm, deduplicated, in order."""
    if not isinstance(offer, (list, tuple)):
        return []
    out = []
    for a in offer:
        if isinstance(a, str) and a in ACTIONS and a not in ("help", "report") and a not in out:
            out.append(a)
    return out


# ------------------------------------------------------------------ entry
def _single(st):
    """One watchdog per state directory: a second one (a foreground `agentj rescue watchdog` next to the unit) would take the
    channel from the first. → the open lock file, or None when another one holds it."""
    fd = os.open(st.root / "rescue.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


async def _main(st) -> None:
    lock = None
    while lock is None:
        lock = _single(st)
        if lock is None:
            await asyncio.sleep(60)
    wd = Watchdog(st)
    loop = asyncio.get_running_loop()
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, wd.stopping.set)
    try:
        await wd.run()
    finally:
        wd.log("stop")


def main(argv=None) -> int:
    from . import instance
    try:
        instance.apply(None)           # the unit carries AGENTJ_INSTANCE: same state dir / unit name as its serve
    except instance.InstanceError:
        pass
    from .state import State
    st = State()
    while not st.exists():             # not initialised yet: nothing to guard; look again later (cheap)
        time.sleep(60)
    try:
        asyncio.run(_main(st))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
