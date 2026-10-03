"""`agentj serve`: the host daemon. Dials the blind relay, runs Noise with each device, approves pairings only via the
local control socket (driven by `agentj pair` in a terminal), shows plaintext only on this machine's terminal.

Spec: protocol/PROTOCOL.md. Logs (host.log) carry metadata only, never message text.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from websockets.asyncio.client import connect

from . import agent as agents
from . import activity, approvals, cloud, controls, danger, fence, gate, memory, slash, tasks, update, webpush, wire
from .noise import IK, IKPSK2, CipherState, Handshake, NoiseError
from .reporter import Reporter, in_daemon_thread
from .envcompat import getenv
from .state import MAX_DEVICES, DeviceLimit, State
from .text import LABEL_DIGITS, LABEL_MAX, clean, clean_label, text_units  # noqa: F401  (re-exported)


def _ttl(env: str, default: int) -> float:
    """Tests may only shorten a timeout, never lengthen it."""
    try:
        return min(default, float(getenv(env, default)))
    except ValueError:
        return default


UPDATE_FIRST = 300      # s after start before the first daily update check (update.daily keeps 24 h between checks)
UPDATE_WAKE = 3600      # s between looks at the 24 h clock
PAIR_TTL = _ttl("AGENTJ_TEST_PAIR_TTL", 300)        # QR / pairing code lifetime (s)
HS_TTL = _ttl("AGENTJ_TEST_HS_TTL", 30)             # a connection must finish its handshake + hello within this (s)
APPROVE_TTL = _ttl("AGENTJ_TEST_APPROVE_TTL", 120)  # time the human has to type the safety code (s)
AUTH_TIMEOUT = 10
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")   # relay auth challenge n = b64url(32 random bytes) (PROTOCOL §1)
CLOSE_TIMEOUT = 2      # a close notice to the relay is best-effort; never let a slow relay hold up a revoke
MAX_SESSIONS = 64      # the relay caps a channel at 32 device sockets; this bounds a misbehaving relay too
SYNC_EVERY = _ttl("AGENTJ_TEST_SYNC_EVERY", 20)   # Dashboard unbind requests are fetched this often while bound (s)
SYNC_IDLE = 60         # …and this often while not bound / unbound (a `agentj login` while serve runs is picked up)
SYNC_MIN_GAP = 2       # never sync faster than this, whatever the answers say (review A31-01)
REMOTE_UNBIND_PER_HOUR = 3   # the host's own cap on executed Dashboard unbinds, persisted (review A31-02/03)
REMOTE_DECISIONS_PER_HOUR = 30   # cap on all decisions incl. refusals: bounds log / terminal volume (review A31-01)
ASK_TTL = _ttl("AGENTJ_TEST_ASK_TTL", 120)   # a permission request the phone does not answer in time is denied (s)
BACKLOG = 100          # recent chat kept in memory (never on disk) and replayed to a device after it (re)connects
MAX_ASKS = 16          # outstanding permission requests at once; more are denied at once
PUSH_GAP = {"reply": 20, "ask": 2}   # minimum seconds between two pushes of a kind to one device
BATCH_MAX = 20         # automatic approvals one batch grant may give (ADR-A48) …
BATCH_SECS = 600       # … within this many seconds, and never past the end of the Agent's turn
MAX_GRANTS = 8         # batch grants open at once
CHUNK = 11 * 1024      # phone-control answers are split into app messages of about this many JSON bytes (MAX_JSON = 16 KiB)
ITEM_SHOW = 6000       # a memory item longer than this is shown cut on the phone (its id still covers the whole text)
_RID = re.compile(r"[A-Za-z0-9_-]{1,32}")
PHONE_CONTROLS = ("mem_list", "mem_rm", "mem_undo", "act_list", "task_list", "task_set", "estop", "resume")


@dataclass
class Session:
    cid: int
    state: str = "new"            # new → hello → (pending →) ready
    mode: str = ""                # pair | resume
    send: CipherState | None = None
    recv: CipherState | None = None
    h: bytes = b""
    pub: bytes = b""
    device: str = ""
    name: str = ""
    timer: asyncio.Task | None = None
    deadline: float = 0.0         # monotonic; the approval deadline while pending
    pending_since: float = 0.0    # monotonic; when the human started being asked (reported as wall time, §7)
    sign_pub: bytes = b""         # the device's Ed25519 approval key from the pairing msg1 (PROTOCOL §8)
    fg: bool = True               # the page is visible (device "vis"); no push while a visible session is ready


@dataclass
class Ask:
    """One permission request from the agent, waiting for a signed answer from a paired phone (PROTOCOL §8)."""
    rid: str
    tool: str
    summary: str
    digest: str                   # shown_digest(tool, summary): what the device's signature covers
    tool_input: dict
    input_sha: str
    deadline: float               # monotonic
    fut: asyncio.Future
    cats: list = field(default_factory=list)   # danger categories ([] = low risk), danger.classify
    why: str = ""
    scope: tuple | None = None     # (kind, key) for a low-risk request that may be batch-approved
    scope_text: str | None = None  # what the phone shows for that scope; an allow_batch signature covers it
    task: str | None = None        # the scheduled task this request comes from (display only)


@dataclass
class Grant:
    """A batch approval (ADR-A48): the human allowed "this kind of low-risk action" for the rest of this Agent turn."""
    rid: str                      # the signed request that created it
    tool: str
    scope: tuple                  # (kind, key)
    text: str
    device: str
    left: int
    until: float                  # monotonic


@dataclass
class Pairing:
    pid: bytes
    psk: bytes
    expires: float                # unix time, shown in the link
    deadline: float               # monotonic, enforced
    ctl: asyncio.StreamWriter
    cid: int | None = None
    timer: asyncio.Task | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class Host:
    def __init__(self, st: State, events: str = "text", read_stdin: bool = True):
        self.st = st
        self.cfg = st.config()
        self.channel = self.cfg["channel"]
        self.kp = st.host_keypair()
        self.sk = st.signing_key()
        self.events = events
        self.read_stdin = read_stdin
        self.ws = None
        self.sessions: dict[int, Session] = {}
        self.pairing: Pairing | None = None
        self.send_lock = asyncio.Lock()
        self.stopping = asyncio.Event()
        self.relay_up = False
        self.name_refused = False   # one agent_name_refused line per refused streak, not one per sync
        self.reporter = Reporter(st, self.report_view)  # Dashboard metadata reports (§7); no-op unless linked
        # agent bridge (L1, PROTOCOL §8) and Web Push (§9)
        self.ask_ttl = ASK_TTL
        self.agent_cfg = st.agent_config()
        self.perm_token: str | None = None          # one-time: issued per agent start, spent by the first claim (L2)
        self.perm_w: asyncio.StreamWriter | None = None   # the claimed connection of the agent's permission tool
        self.perm_claimed = asyncio.Event()
        self.agent = None
        self.asks: dict[str, Ask] = {}
        self.backlog: deque = deque(maxlen=BACKLOG)   # in memory only
        self.seq = 0
        self.sent_status = None
        self.turn_text = False
        self.push_key = None
        self.push_last: dict[tuple, float] = {}
        self.post_q: asyncio.Queue | None = None
        # danger list (ADR-A47 / A49): fixed rules + the human's additions from config.json (additions only)
        self.danger_extra = danger.parse_extra(self.cfg.get("danger_extra"))[0]
        if self.agent_cfg:
            self.agent_cfg["danger_extra"] = self.danger_extra
        self.grants: dict[str, Grant] = {}
        # phone controls (PROMPT-26 item 3, ADR-A50 – A54): signed commands, the stop switch, the task scheduler
        self.turn_lock = asyncio.Lock()        # one user of the Agent at a time: a chat turn or a scheduled task run
        self.nonces = controls.Nonces()
        self.estop = controls.estop_state(st)
        self.scheduler = tasks.Scheduler(self)
        self.task_label: str | None = None
        self.turn_by: str | None = None
        self.turn_t0 = 0.0

    # ------------------------------------------------------------ output
    def emit(self, ev: str, **kw) -> None:
        if self.events == "jsonl":
            print(json.dumps({"ev": ev, **kw}, ensure_ascii=False), flush=True)
            return
        if self.events == "quiet":   # service mode (L3): stdout is a journal / log file — metadata only, never text
            if ev in ("msg", "agent_msg", "agent_notice"):
                return
            if ev == "ask":
                print(f"· 等手机批准 [{kw['id'][:8]}] {kw['tool']}", flush=True)
                return
            if ev == "grant":     # the scope may hold a folder name: metadata mode prints the id only
                print(f"· 手机授权本轮批量批准 [{kw['id'][:8]}]", flush=True)
                return
        if ev == "msg":  # continuation lines are marked, so a device cannot print lines that look like host output
            print(f"« {kw['name']}: " + kw["text"].replace("\n", "\n  │ "), flush=True)
        elif ev == "relay":
            print(f"· 中继{'已连接' if kw['up'] else '断开，正在重连'}", flush=True)
        elif ev == "agent_name":   # a §1-valid name (no control / format / line-break chars), quoted, after the "· " marker
            print(f"· Agent 名已按 Dashboard 改为「{kw['name']}」", flush=True)
        elif ev == "agent_name_refused":
            print("· Dashboard 发来的 Agent 名不合规，本机没有采用（保留原名）", flush=True)
        elif ev == "remote_unbind":
            print(f"· Dashboard 请求解绑遥控器 {kw.get('name') or ''} {kw['device']}：{kw['result']}", flush=True)
        elif ev == "agent_msg":
            print("» Agent: " + kw["text"].replace("\n", "\n  │ "), flush=True)
        elif ev == "agent_notice":
            print(f"· {kw['text']}", flush=True)
        elif ev == "agent_status":
            print(f"· Agent 状态：{ {'idle': '空闲', 'working': '干活中', 'waiting': '等手机批准', 'down': '未运行', 'none': '未接'}.get(kw['s'], kw['s']) }", flush=True)
        elif ev == "ask":
            print(f"· 等手机批准 [{kw['id'][:8]}] {kw['tool']}: " + kw["summary"].replace("\n", "\n  │ "), flush=True)
        elif ev == "ask_done":
            print(f"· 批准结果 [{kw['id'][:8]}]：{kw['result']}", flush=True)
        elif ev == "auto":
            print(f"· 按手机的批量授权自动批准 [{kw['id'][:8]}] {kw['tool']}", flush=True)
        elif ev == "grant":
            print(f"· 手机授权：本轮自动批准同类低风险操作（{kw['scope']}）", flush=True)
        elif ev == "grant_end":
            print(f"· 批量授权结束 [{kw['id'][:8]}]：{kw['why']}", flush=True)
        elif ev == "estop":
            print("· 已急停：Agent 停下、待批准全部拒绝、批量授权收回、定时任务暂停" if kw["on"] else "· 已恢复", flush=True)
        elif ev == "task_done":
            print(f"· 定时任务 {kw['id']}：{kw['verdict']}", flush=True)
        elif ev == "update":   # a version string from GitHub, already parsed as one (update.parse)
            print(f"· 有新版本 {kw['latest']}（本机 {kw['current']}）：在终端运行 `agentj update apply` 升级", flush=True)
        elif ev in ("ready", "approved", "revoked", "denied", "closed"):
            print(f"· {ev} {kw.get('name') or ''} {kw.get('device') or ''}".rstrip(), flush=True)

    def _tap(self, direction: str, data) -> None:
        """Every frame to / from the relay passes here. Does nothing in the shipped host; the end-to-end tests override it
        (host/tests/wiredump.py) to record what the relay sees. The release package carries no frame recorder (L2)."""

    # ------------------------------------------------------------ relay plumbing
    async def _ws_send(self, data) -> None:
        if self.ws is None:
            raise ConnectionError("relay down")
        self._tap("out", data)
        await self.ws.send(data)

    async def _op(self, op: int, cid: int, payload: bytes = b"") -> None:
        await self._ws_send(bytes([op]) + cid.to_bytes(4, "big") + payload)

    def _detach(self, cid: int, pair_result: dict | None = None) -> Session | None:
        """Synchronously forget a session (no await): from here on none of its frames is acted on and nothing is sent to
        it. If it was the pending pairing, that pairing ends too."""
        s = self.sessions.pop(cid, None)
        if s and s.timer and s.timer is not asyncio.current_task():
            s.timer.cancel()
        if s and s.state in ("pending", "ready"):
            self.reporter.trigger("pair_end" if s.state == "pending" else "gone")
        p = self.pairing
        if p and p.cid == cid:
            self._pair_finish(p, pair_result or {"ev": "denied", "reason": "bad_handshake"})
        return s

    async def _notify_close(self, cid: int) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._op(wire.OP_CLOSE, cid), CLOSE_TIMEOUT)

    async def close_cid(self, cid: int, reason: str, pair_result: dict | None = None) -> None:
        s = self._detach(cid, pair_result)
        await self._notify_close(cid)
        self.st.log("close", cid=cid, device=s.device if s else None, reason=reason)

    async def send_app(self, s: Session, obj: dict) -> bool:
        async with self.send_lock:  # nonce order == wire order
            if self.sessions.get(s.cid) is not s:  # closed / revoked while this send was queued
                return False
            ct = s.send.encrypt(b"", wire.pad_json(obj))
            await self._op(wire.OP_DATA, s.cid, bytes([wire.DATA]) + ct)
            return True

    async def relay_loop(self) -> None:
        backoff = 1
        url = f"{self.cfg['relay'].rstrip('/')}/v1/host/{self.channel}"
        while not self.stopping.is_set():
            try:
                async with connect(url, max_size=2**17, ping_interval=20, ping_timeout=20, open_timeout=15) as ws:
                    await self._auth(ws)
                    self.ws, self.relay_up, backoff = ws, True, 1
                    self.st.log("relay_up", channel=self.channel)
                    self.emit("relay", up=True)
                    async for msg in ws:
                        self._tap("in", msg)
                        if isinstance(msg, bytes):
                            await self.on_frame(msg)
            except Exception as e:  # network, handshake refusal, relay auth failure → back off and retry
                self.st.log("relay_down", reason=type(e).__name__)
            finally:
                if self.relay_up:
                    self.emit("relay", up=False)
                self.ws, self.relay_up = None, False
                await self._drop_all("relay_down")
            if self.stopping.is_set():
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), backoff)
            backoff = min(backoff * 2, 30)

    async def _auth(self, ws) -> None:
        msg = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT))
        # F10: sign only a nonce of the relay's exact shape (32 random bytes, b64url) — never an arbitrary string
        if msg.get("t") != "challenge" or not isinstance(msg.get("n"), str) or not _NONCE.fullmatch(msg["n"]):
            raise ValueError("bad challenge")
        sig = self.sk.sign(f"agentjarvis-relay-auth-v1\n{self.channel}\n{msg['n']}".encode())
        await ws.send(json.dumps({"t": "auth", "pk": wire.b64u(self.st.signing_pub()), "sig": wire.b64u(sig)}))
        ok = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT))
        if ok.get("t") != "ok":
            raise ValueError("relay refused auth")

    async def _drop_all(self, reason: str) -> None:
        for cid in list(self.sessions):
            self._detach(cid, {"ev": "gone"})

    def _set_timer(self, s: Session, coro) -> None:
        if s.timer and s.timer is not asyncio.current_task():
            s.timer.cancel()
        s.timer = asyncio.create_task(coro) if coro else None

    # ------------------------------------------------------------ frames
    def _new_session(self, cid: int) -> Session | None:
        if cid in self.sessions:
            return self.sessions[cid]
        if len(self.sessions) >= MAX_SESSIONS:
            return None
        s = self.sessions[cid] = Session(cid)
        self._set_timer(s, self._handshake_timeout(s))
        return s

    async def on_frame(self, f: bytes) -> None:
        if len(f) < 5:
            return
        op, cid, payload = f[0], int.from_bytes(f[1:5], "big"), f[5:]
        if op == wire.OP_OPEN:
            if not self._new_session(cid):
                await self.close_cid(cid, "too_many_sessions")
        elif op == wire.OP_GONE:
            self._detach(cid, {"ev": "gone"})
        elif op == wire.OP_DATA:
            s = self._new_session(cid)  # data from a cid we never saw 0x11 for = a new connection (PROTOCOL §3)
            if not s:
                return await self.close_cid(cid, "too_many_sessions")
            try:
                await self.on_data(s, payload)
            except Exception as e:  # anything a peer sends can cost at most its own session, never the relay link
                await self.close_cid(cid, f"bad_frame:{type(e).__name__}")

    async def on_data(self, s: Session, data: bytes) -> None:
        if not data:
            raise ValueError("empty")
        kind, body = data[0], data[1:]
        if s.state == "new" and kind == wire.PAIR_INIT:
            await self._pair_init(s, body)
        elif s.state == "new" and kind == wire.RESUME_INIT:
            await self._resume_init(s, body)
        elif kind == wire.DATA and s.state in ("hello", "pending", "ready"):
            obj = wire.unpad_json(s.recv.decrypt(b"", body))
            await self._app(s, obj)
        else:
            await self.close_cid(s.cid, "unexpected_kind")

    async def _pair_init(self, s: Session, body: bytes) -> None:
        pid, msg1 = body[:16], body[16:]
        p = self.pairing
        if not p or len(pid) != 16 or not hmac.compare_digest(pid, p.pid) or p.cid is not None:
            self.st.log("pair_unknown", cid=s.cid)
            return await self.close_cid(s.cid, "pair_unknown_or_used")
        if time.monotonic() > p.deadline:
            self.st.log("pair_expired", cid=s.cid)
            self._pair_finish(p, {"ev": "expired"})
            return await self.close_cid(s.cid, "pair_expired")
        hs = Handshake(IKPSK2, False, self.kp, prologue=wire.pair_prologue(self.channel, p.pid), psk=p.psk)
        try:
            payload = hs.read_message(msg1)
        except NoiseError:
            # msg1 needs the host's X25519 key, which only the QR carries (the relay never sees it): a bad one is not
            # from the QR holder, so it does not use up the QR
            self.st.log("pair_bad_msg1", cid=s.cid)
            return await self.close_cid(s.cid, "pair_bad_msg1")
        p.cid = s.cid  # one-time: consumed by the first valid PAIR_INIT that carries its id
        info = json.loads(payload)  # may raise → on_frame closes this cid → the pairing ends (_detach)
        if not isinstance(info, dict) or info.get("v") != 1 or not isinstance(info.get("name", ""), str):
            return await self.close_cid(s.cid, "pair_bad_payload")
        s.sign_pub = _sign_key(info)
        msg2 = hs.write_message(b"")
        s.send, s.recv, s.h = hs.split()
        s.pub, s.device, s.mode, s.state = hs.rs, wire.device_id(hs.rs), "pair", "hello"
        s.name = clean_label(info.get("name", ""))
        await self._op(wire.OP_DATA, s.cid, bytes([wire.HS_RESP]) + msg2)
        self.st.log("pair_handshake", cid=s.cid, device=s.device)

    async def _resume_init(self, s: Session, msg1: bytes) -> None:
        hs = Handshake(IK, False, self.kp, prologue=wire.resume_prologue(self.channel))
        payload = hs.read_message(msg1)
        if not self.st.is_allowed(hs.rs):
            self.st.log("unknown_device", cid=s.cid, device=wire.device_id(hs.rs))
            return await self.close_cid(s.cid, "unknown_device")
        try:
            info = json.loads(payload) if payload else {}
        except ValueError:
            info = {}
        sk = _sign_key(info) if isinstance(info, dict) else b""
        if sk and self.st.set_sign_key_if_absent(hs.rs, sk):   # a device paired before L1 registers its approval key once
            self.st.log("sign_key_added", device=wire.device_id(hs.rs))
        msg2 = hs.write_message(b"")
        s.send, s.recv, s.h = hs.split()
        s.pub, s.device, s.mode, s.state = hs.rs, wire.device_id(hs.rs), "resume", "hello"
        s.name = self.st.devices().get(s.device, {}).get("name", "")
        await self._op(wire.OP_DATA, s.cid, bytes([wire.HS_RESP]) + msg2)

    async def _app(self, s: Session, obj: dict) -> None:
        t = obj["t"]
        if s.state == "hello":
            if t != "hello":
                return await self.close_cid(s.cid, "expected_hello")
            if s.mode == "resume":
                if not self.st.is_allowed(s.pub):  # revoked between msg1 and hello
                    return await self.close_cid(s.cid, "revoked")
                s.state = "ready"
                self._set_timer(s, None)
                await self.send_app(s, {"t": "ready"})
                self.st.log("resume_ok", cid=s.cid, device=s.device)
                self.emit("ready", device=s.device, name=s.name)
                self.reporter.trigger("online")
                await self.on_ready(s, obj.get("since"))
                return
            # pairing: hello decrypted ⇒ device knows the PSK. Wait for the human.
            p = self.pairing
            if not p or p.cid != s.cid:
                return await self.close_cid(s.cid, "pair_state")
            s.state = "pending"
            s.pending_since = time.monotonic()
            s.deadline = s.pending_since + APPROVE_TTL
            self._set_timer(s, self._approve_timeout(s))
            self.st.log("pair_pending", cid=s.cid, device=s.device, name=s.name)
            self.reporter.trigger("pending")
            await self._ctl_send(p.ctl, self._pending_event(s))
            return
        if s.state == "pending":
            return  # nothing from an unapproved device is acted on
        if not self.st.is_allowed(s.pub):  # belt and braces: a ready session whose device left the allowlist
            return await self.close_cid(s.cid, "revoked")
        if t == "msg":
            text = obj.get("text")
            if not isinstance(text, str) or text_units(text) > wire.MAX_TEXT:  # reject, never silently truncate
                return await self.close_cid(s.cid, "bad_msg")
            if self.stopped() and self.agent:      # stopped: nothing is queued to run later behind the human's back
                self.st.log("msg_refused", cid=s.cid, device=s.device, reason="estop")
                self.activity("message_refused", by=s.name or s.device, text=text[:120])
                await self.send_app(s, {"t": "msg", "id": secrets.token_hex(8), "ts": int(time.time() * 1000), "seq": self.seq,
                                        "from": "notice", "text": "已急停：这条没有交给 Agent，也不会排队。恢复后再发。"})
                return
            cmd = slash.parse(text)
            if cmd and self.agent and not self.agent.passthrough(cmd[0]):
                return await self.on_slash(s, cmd[0], cmd[1], confirm=False, typed=True)
            self.emit("msg", device=s.device, name=s.name, text=clean(text, wire.MAX_TEXT))
            self.st.log("msg_in", cid=s.cid, device=s.device)
            entry = self._remember("device", text, device=s.device, name=s.name)
            for o in list(self.sessions.values()):     # other phones see what was said; the sender already shows it
                if o is not s and o.state == "ready" and self.st.is_allowed(o.pub):
                    await self.send_app(o, self._render(entry, o))
            if self.agent:
                self.turn_by = s.name or s.device
                self.agent.submit(text)
        elif t == "answer":
            await self._answer(s, obj)
        elif t == "push_sub":
            sub = webpush.parse_sub(obj)
            if sub is None:
                self.st.log("push_sub_refused", device=s.device)
                return
            subs = webpush.load_subs(self.st)
            if subs.get(s.device) != sub:
                subs[s.device] = sub
                webpush.save_subs(self.st, subs)
                self.st.log("push_sub", device=s.device)
        elif t == "push_off":
            subs = webpush.load_subs(self.st)
            if subs.pop(s.device, None):
                webpush.save_subs(self.st, subs)
                self.st.log("push_off", device=s.device)
        elif t == "vis" and isinstance(obj.get("fg"), bool):
            s.fg = obj["fg"]
        elif t == "grant_off":          # take a batch approval back (narrows only: any ready, paired device may)
            gid = obj.get("id")
            for g in list(self.grants.values()):
                if gid is None or g.rid == gid:
                    self.end_grant(g.rid, "revoked", by=s.device)
        elif t in PHONE_CONTROLS:
            await self.on_control(s, t, obj)
        elif t == "slash":              # a command from the phone's 「命令」 buttons (or typed: the page sends it like this)
            name, arg = obj.get("cmd"), obj.get("arg", "")
            if isinstance(name, str) and isinstance(arg, str) and len(name) <= 32 and text_units(arg) <= slash.ARG_MAX:
                await self.on_slash(s, name.lower(), arg.strip(), confirm=obj.get("confirm") is True, typed=False)

    # ------------------------------------------------------------ deadlines
    async def _handshake_timeout(self, s: Session) -> None:
        await asyncio.sleep(HS_TTL)
        if self.sessions.get(s.cid) is s and s.state in ("new", "hello"):
            self.st.log("hs_timeout", cid=s.cid)
            await self.close_cid(s.cid, "handshake_timeout", {"ev": "denied", "reason": "hs_timeout"})

    async def _approve_timeout(self, s: Session) -> None:
        await asyncio.sleep(max(0.0, s.deadline - time.monotonic()))
        if self.sessions.get(s.cid) is s and s.state == "pending":
            self.st.log("pair_timeout", cid=s.cid)
            await self.close_cid(s.cid, "approve_timeout", {"ev": "denied", "reason": "timeout"})

    # ------------------------------------------------------------ pairing / approval
    async def _expire_pairing(self, p: Pairing) -> None:
        await asyncio.sleep(max(0.0, p.deadline - time.monotonic()))
        if self.pairing is p and p.cid is None:
            self.st.log("pair_expired")
            self._pair_finish(p, {"ev": "expired"})

    def _pair_finish(self, p: Pairing, result: dict) -> None:
        if self.pairing is p:
            self.pairing = None
        if p.timer:
            p.timer.cancel()
        if not p.done.done():
            p.done.set_result(result)

    def _pending_event(self, s: Session) -> dict:
        """What `agentj pair` shows while a device waits. When the allowlist is full it also gets the current devices, so
        the human can unbind one before approving (Q32: at most MAX_DEVICES remotes per host)."""
        ev = {"ev": "pending", "name": s.name, "device": s.device, "limit": MAX_DEVICES, "deadline_in": max(0, int(s.deadline - time.monotonic()))}
        devs = self.st.devices()
        if len(devs) >= MAX_DEVICES and s.device not in devs:   # re-pairing a listed device does not need a free slot
            online = {x.device for x in self.sessions.values() if x.state == "ready"}
            ev["full"] = True
            ev["devices"] = [{"id": d, "name": v.get("name", ""), "paired_at": v.get("paired_at", 0), "online": d in online}
                             for d, v in sorted(devs.items(), key=lambda kv: kv[1].get("paired_at", 0))]
        return ev

    async def decide(self, p: Pairing, code: str, passphrase: str | None = None) -> dict:
        """The human's answer for the pending device. A non-empty code also needs the approval passphrase (L2, gate.py),
        checked before the code; a wrong one keeps the device waiting (`pass_wrong`, the code is not used up) until the
        gate locks."""
        s = self.sessions.get(p.cid) if p.cid is not None else None
        if not s or s.state != "pending":
            return {"ev": "denied", "reason": "no_pending_device"}
        if time.monotonic() > s.deadline:  # the timer may not have run yet; the deadline is what counts
            res = {"ev": "denied", "reason": "timeout"}
            await self.close_cid(s.cid, "approve_timeout", res)
            return res
        if self.st.device_full() and s.device not in self.st.devices():  # hard cap for a NEW device: unbind one first
            res = {"ev": "denied", "reason": "device_limit"}
            self.st.log("pair_denied", cid=s.cid, device=s.device, reason="device_limit")
            await self.close_cid(s.cid, "device_limit", res)
            self.emit("denied", device=s.device, name=s.name)
            return res
        if code.strip():
            if not passphrase and gate.is_set(self.st):     # nothing typed: ask again, no try used
                return {"ev": "pass_wrong", "left": gate.tries_left(self.st)}
            try:
                await asyncio.to_thread(gate.verify, self.st, passphrase)
            except gate.GateError as e:
                if e.reason == "wrong":
                    self.st.log("pair_pass_wrong", cid=s.cid, device=s.device)
                    return {"ev": "pass_wrong", "left": e.info.get("left", 0)}
                res = {"ev": "denied", "reason": "pass_" + e.reason}   # pass_not_set | pass_locked
                self.st.log("pair_denied", cid=s.cid, device=s.device, reason=res["reason"])
                self._set_timer(s, None)
                await self.close_cid(s.cid, res["reason"], res)
                self.emit("denied", device=s.device, name=s.name)
                return res
            if self.sessions.get(s.cid) is not s or s.state != "pending" or time.monotonic() > s.deadline:
                return {"ev": "denied", "reason": "timeout" if self.sessions.get(s.cid) is s else "gone"}
        ok = hmac.compare_digest(code.strip().encode(), wire.safety_code(s.h).encode())
        self._set_timer(s, None)
        if not ok:
            res = {"ev": "denied", "reason": "code_mismatch" if code.strip() else "denied"}
            self.st.log("pair_denied", cid=s.cid, device=s.device, code_ok=False)
            await self.close_cid(s.cid, res["reason"], res)
            self.emit("denied", device=s.device, name=s.name)
            return res
        try:
            self.st.add_device(s.pub, s.name, s.sign_pub or None)
        except DeviceLimit:
            res = {"ev": "denied", "reason": "device_limit"}
            await self.close_cid(s.cid, "device_limit", res)
            return res
        s.state = "ready"
        self.reporter.trigger("approve")
        await self.send_app(s, {"t": "approved"})
        self.st.log("pair_approved", cid=s.cid, device=s.device, name=s.name, code_ok=True)
        self.emit("approved", device=s.device, name=s.name)
        asyncio.create_task(self.on_ready(s, None))
        return {"ev": "approved", "device": s.device, "name": s.name}

    # ------------------------------------------------------------ control socket (local, 0600)
    async def _ctl_send(self, w: asyncio.StreamWriter, obj: dict) -> None:
        with contextlib.suppress(Exception):
            w.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            await w.drain()

    async def on_ctl(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while line := await r.readline():
                req = json.loads(line)
                cmd = req.get("cmd")
                if cmd == "status":
                    await self._ctl_send(w, {"ok": True, "relay_up": self.relay_up, "channel": self.channel,
                                             "agent": self.agent.kind if self.agent else None, "agent_status": self.eff_status(),
                                             "asks": len(self.asks), "estop": self.estop,
                                             "task_running": self.scheduler.current_id,
                                             "sessions": [{"device": s.device, "name": s.name, "state": s.state}
                                                          for s in self.sessions.values() if s.state != "new"]})
                elif cmd == "report_view":
                    online, pending = self.report_view()
                    await self._ctl_send(w, {"ok": True, "online": sorted(online), "pending": pending})
                elif cmd == "pair":
                    await self._ctl_pair(r, w)
                    return
                elif cmd == "revoke":
                    removed, closed = await self.revoke(str(req.get("device", "")))
                    await self._ctl_send(w, {"ok": removed, "closed": closed})
                elif cmd == "stop":                 # `agentj stop` (the terminal: no signature needed — it only stops)
                    await self.do_estop("terminal", "终端")
                    await self._ctl_send(w, {"ok": True, "estop": self.estop})
                elif cmd == "resume":               # `agentj resume` checked the approval passphrase before calling
                    await self.do_resume("terminal", "终端")
                    await self._ctl_send(w, {"ok": True, "estop": self.estop})
                elif cmd == "task_run":
                    tid = str(req.get("id", ""))
                    if self.stopped():
                        await self._ctl_send(w, {"ok": False, "error": "stopped"})
                    elif not self.agent_cfg:
                        await self._ctl_send(w, {"ok": False, "error": "no_agent"})
                    else:
                        try:
                            tasks.find(self.agent_cfg["dir"], tid)
                        except tasks.TaskError:
                            await self._ctl_send(w, {"ok": False, "error": "unknown"})
                        else:
                            self.scheduler.request(tid, "manual")
                            await self._ctl_send(w, {"ok": True, "queued": tid})
                elif cmd == "tasks_changed":
                    self.scheduler.wake.set()
                    await self._ctl_send(w, {"ok": True})
                elif cmd == "agent_name_changed":   # `agentj name` / `agentj admin` renamed: tell the Dashboard soon
                    self.reporter.trigger("agent_name")
                    self.name_changed()
                    await self._ctl_send(w, {"ok": True})
                elif cmd == "send":
                    text = str(req.get("text", ""))
                    if text_units(text) > wire.MAX_TEXT:
                        await self._ctl_send(w, {"ok": False, "error": "too_long"})
                    else:
                        await self._ctl_send(w, {"ok": True, "delivered": await self.broadcast(text)})
                else:
                    await self._ctl_send(w, {"ok": False, "error": "unknown command"})
        except (json.JSONDecodeError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(Exception):
                w.close()

    def report_view(self) -> tuple[set, dict]:
        """What a §7 report would say right now: devices with a ready session, and the pairings waiting for the human
        (count + wall-clock start of the oldest, converted from the monotonic clock). No labels, codes or pairing ids."""
        online = {s.device for s in self.sessions.values() if s.state == "ready" and s.device}
        waiting = [s.pending_since for s in self.sessions.values() if s.state == "pending"]
        since = int(time.time() - (time.monotonic() - min(waiting))) if waiting else None
        return online, {"count": len(waiting), "since": since}

    async def revoke(self, did: str) -> tuple[bool, int]:
        """Allowlist first, then detach every session of the device before the first await: from that instant none of
        its frames is acted on and nothing more is sent to it, however slow the relay is to carry the close notices."""
        removed = self.st.remove_device(did)
        victims = [c for c, s in list(self.sessions.items()) if s.device == did]
        for c in victims:
            self._detach(c)
        if removed:
            self.st.log("revoked", device=did)
            self.reporter.trigger("revoke")
            self.emit("revoked", device=did)
            subs = webpush.load_subs(self.st)
            if subs.pop(did, None):
                webpush.save_subs(self.st, subs)
        for c in victims:
            await self._notify_close(c)
            self.st.log("close", cid=c, device=did, reason="revoked")
        return removed, len(victims)

    async def _ctl_pair(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if not self.relay_up:
            return await self._ctl_send(w, {"ev": "error", "reason": "relay_down"})
        old = self.pairing
        if old:
            self._pair_finish(old, {"ev": "replaced"})
            if old.cid is not None:
                await self.close_cid(old.cid, "pair_replaced")
        p = Pairing(secrets.token_bytes(16), secrets.token_bytes(32), time.time() + PAIR_TTL, time.monotonic() + PAIR_TTL, w)
        self.pairing = p
        p.timer = asyncio.create_task(self._expire_pairing(p))
        link = wire.pairing_link(self.cfg["web"], self.cfg["relay"], self.channel, self.kp.pub, p.pid, p.psk,
                                 int(p.expires))
        self.st.log("pair_begin")
        await self._ctl_send(w, {"ev": "link", "link": link, "expires": int(p.expires)})
        reader = asyncio.create_task(r.readline())
        try:
            while True:
                done, _ = await asyncio.wait({reader, p.done}, return_when=asyncio.FIRST_COMPLETED)
                if p.done in done:
                    await self._ctl_send(w, p.done.result())
                    return
                line = reader.result()
                if not line:  # `agentj pair` went away → deny
                    cid = p.cid
                    self._pair_finish(p, {"ev": "denied", "reason": "abandoned"})
                    if cid is not None and cid in self.sessions:
                        await self.close_cid(cid, "pair_abandoned")
                    return
                req = json.loads(line)
                if req.get("cmd") == "unbind":  # the human picked a device to unbind from the full-list prompt
                    did = str(req.get("device", ""))
                    pend = self.sessions.get(p.cid) if p.cid is not None else None
                    removed, _ = (False, 0) if pend and pend.device == did else await self.revoke(did)
                    await self._ctl_send(w, {"ev": "unbound", "ok": removed, "device": did})
                    s = self.sessions.get(p.cid) if p.cid is not None else None
                    if s and s.state == "pending":
                        await self._ctl_send(w, self._pending_event(s))
                    reader = asyncio.create_task(r.readline())
                    continue
                if req.get("cmd") == "code":
                    pw = req.get("pass")
                    res = await self.decide(p, str(req.get("code", "")), pw if isinstance(pw, str) else None)
                    if res.get("ev") == "pass_wrong":     # the device keeps waiting; the human may type the passphrase again
                        await self._ctl_send(w, res)
                        reader = asyncio.create_task(r.readline())
                        continue
                    self._pair_finish(p, res)
                    await self._ctl_send(w, res)
                    return
                reader = asyncio.create_task(r.readline())
        finally:
            reader.cancel()

    # ------------------------------------------------------------ Dashboard unbind requests (A3.1, PROTOCOL §7 sync)
    async def remote_unbind(self, rid: str, did: str) -> str | None:
        """One owner request from the Dashboard. It can only take a device away, and only after this host's own checks:
        the switch (`agentj remote-unbind`), the device on our own allowlist, our own hourly cap (persisted). Logged either
        way. A request id is decided once (remembered on disk). None = not decided now (decision budget used up: the
        request stays pending at the Dashboard and is not answered — review A31-01)."""
        ledger = self.st.unbind_ledger()
        if rid in ledger["seen"]:
            return ledger["seen"][rid][0]
        now = time.time()
        if sum(1 for t in ledger["decided"] if now - t < 3600) >= REMOTE_DECISIONS_PER_HOUR:
            if not any(now - t < 3600 for t in ledger["throttled"]):
                ledger["throttled"].append(now)
                self.st.save_unbind_ledger(ledger)
                self.st.log("remote_unbind_throttled")
            return None
        name = self.st.devices().get(did, {}).get("name", "")
        if not self.st.remote_unbind():
            result = "disabled"
        elif did not in self.st.devices():
            result = "unknown_device"
        elif sum(1 for t in ledger["executed"] if now - t < 3600) >= REMOTE_UNBIND_PER_HOUR:
            result = "rate_limited"
        else:
            removed, _ = await self.revoke(did)
            result = "revoked" if removed else "unknown_device"
            if removed:
                ledger["executed"].append(now)
        ledger["decided"].append(now)
        ledger["seen"][rid] = [result, int(now)]
        self.st.save_unbind_ledger(ledger)
        self.st.log("remote_unbind", request=rid, device=did, result=result)
        self.emit("remote_unbind", device=did, name=name, result=result)
        return result

    async def sync_loop(self) -> None:
        """Fetch the owner's unbind requests (signed sync), act on each after our own checks, report the results on the
        next sync. Best effort: never blocks serve; failures are metadata-only log lines. Whatever the answers say, syncs
        are ≥ SYNC_MIN_GAP apart and at most one HTTP call is in flight (review A31-01)."""
        results: list[tuple[str, str]] = []
        fut: asyncio.Future | None = None
        while not self.stopping.is_set():
            wait = SYNC_IDLE
            if fut is not None and not fut.done():   # a previous call still hangs: never start a second one
                wait = SYNC_EVERY
            else:
                fut = in_daemon_thread(cloud.send_sync, self.st, list(results))
                done, _ = await asyncio.wait({fut}, timeout=cloud.HTTP_TIMEOUT + 5)
                try:
                    res = fut.result() if done else cloud.SyncResult("fail", "timeout")
                except Exception:  # noqa: BLE001 — a sync bug must never reach serve
                    res = cloud.SyncResult("fail", "error")
                if res.kind == "ok":
                    self.adopt_name(res.name)
                    sent = {i for i, _ in results[:cloud.MAX_SYNC_ITEMS]}
                    results = [x for x in results if x[0] not in sent]
                    for rid, did in res.unbind:
                        r = await self.remote_unbind(rid, did)
                        if r is not None:
                            results.append((rid, r))
                    wait = SYNC_MIN_GAP if results else SYNC_EVERY  # acknowledge soon, never in a tight loop
                elif res.kind == "fail":
                    self.st.log("sync_fail", status=res.status)
                    wait = SYNC_EVERY
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), wait)

    def adopt_name(self, name: tuple) -> None:
        """A3.2 §3: the sync answer's canonical Agent name. Adopted when valid and different (logged, printed, and a report
        is sent — the signed report is the acknowledgement); refused when invalid (local name kept, logged once per streak).
        It is a display string only: it never reaches the allowlist, a path, a shell or a prompt."""
        kind, n = name
        if kind == "bad":
            if not self.name_refused:
                self.name_refused = True
                self.st.log("agent_name_refused")
                self.emit("agent_name_refused")
            return
        self.name_refused = False
        if kind != "ok" or n == self.st.agent_name():
            return
        try:
            self.st.set_agent_name(n)
        except (ValueError, OSError):
            self.st.log("agent_name_refused")
            return
        self.st.log("agent_name_synced")
        self.emit("agent_name", name=n)
        self.reporter.trigger("agent_name")
        self.name_changed()

    async def broadcast(self, text: str, frm: str = "host") -> int:
        """Callers check text_units(text) <= MAX_TEXT first. Kept in the in-memory backlog for devices that reconnect."""
        entry = self._remember(frm, text)
        n = 0
        for s in list(self.sessions.values()):
            if s.state == "ready" and self.st.is_allowed(s.pub):
                n += await self.send_app(s, self._render(entry, s))
        if n:
            self.st.log("msg_out", bytes=len(text.encode()))
        return n

    # ------------------------------------------------------------ chat backlog (memory only) + per-device rendering
    def _remember(self, frm: str, text: str, device: str | None = None, name: str = "", x: dict | None = None) -> dict:
        self.seq += 1
        e = {"seq": self.seq, "from": frm, "device": device, "name": name, "text": text, "id": secrets.token_hex(8),
             "ts": int(time.time() * 1000)}
        if x:
            e["x"] = x
        self.backlog.append(e)
        return e

    def _render(self, e: dict, s: Session) -> dict:
        frm = "you" if e["from"] == "device" and e["device"] == s.device else e["from"]
        m = {"t": "msg", "id": e["id"], "text": e["text"], "ts": e["ts"], "seq": e["seq"], "from": frm}
        if frm == "device":
            m["name"] = e["name"]
        if e.get("x"):
            m.update(e["x"])
        return m

    async def on_ready(self, s: Session, since) -> None:
        """A device just became ready: current Agent status, the push key, the chat it missed (seq > since), and every
        permission request still waiting."""
        if self.sessions.get(s.cid) is not s:
            return
        since = since if isinstance(since, int) and not isinstance(since, bool) and since >= 0 else 0
        if since > self.seq:            # a device that remembers a seq from before this serve started: replay it all
            since = 0
        await self.send_app(s, self._status_msg())
        await self.send_app(s, self._estop_msg())
        await self.send_app(s, {"t": "push_key", "k": webpush.b64u(webpush.vapid_public(self._push_key()))})
        for e in list(self.backlog):
            if e["seq"] > since:
                await self.send_app(s, self._render(e, s))
        for a in list(self.asks.values()):
            if not a.fut.done():
                await self.send_app(s, self._ask_msg(a))
        for g in list(self.grants.values()):
            if self._grant_live(g):
                await self.send_app(s, self._grant_msg(g))

    # ------------------------------------------------------------ agent bridge (PROTOCOL §8)
    def eff_status(self) -> str:
        if self.stopped():
            return "stopped"
        if not self.agent:
            return "none"
        if any(not a.fut.done() for a in self.asks.values()):
            return "waiting"
        return self.agent.status

    def _status_msg(self) -> dict:
        """PROTOCOL §8 status; `name` = the Agent's display name (or null), so the phone can show it (title, top left)."""
        return {"t": "status", "s": self.eff_status(), "agent": self.agent.kind if self.agent else None,
                "name": self.st.agent_name()}

    def name_changed(self) -> None:
        """The Agent name changed (`agentj name`, the admin page, a Dashboard rename adopted in sync): every ready phone gets
        a fresh status at once."""
        msg = self._status_msg()
        self._post(self._send_ready, lambda s: msg)

    def _post(self, coro_fn, *args) -> None:
        """Agent callbacks are synchronous; outbound sends are queued so the phone sees them in order."""
        if self.post_q is not None:
            self.post_q.put_nowait((coro_fn, args))
        else:                               # not inside run() (unit tests): send right away
            asyncio.get_running_loop().create_task(coro_fn(*args))

    async def post_loop(self) -> None:
        while True:
            fn, args = await self.post_q.get()
            try:
                await fn(*args)
            except Exception:  # noqa: BLE001 — a send to a vanished session costs nothing else
                pass

    async def _send_ready(self, obj_fn) -> None:
        for s in list(self.sessions.values()):
            if s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, obj_fn(s))

    def status_changed(self) -> None:
        st = self.eff_status()
        if st == self.sent_status:
            return
        self.sent_status = st
        self.emit("agent_status", s=st)
        msg = self._status_msg()
        self._post(self._send_ready, lambda s: msg)

    # callbacks from agent.Agent
    def agent_status(self, s: str) -> None:
        self.status_changed()

    def agent_text(self, text: str) -> None:
        self.turn_text = True
        for chunk in agents.split_text(text, wire.MAX_TEXT):
            self.emit("agent_msg", text=clean(chunk, wire.MAX_TEXT))
            e = self._remember("agent", chunk)
            self._post(self._send_ready, lambda s, e=e: self._render(e, s))

    def agent_notice(self, text: str) -> None:
        self.emit("agent_notice", text=text)
        e = self._remember("notice", text)
        self._post(self._send_ready, lambda s, e=e: self._render(e, s))

    async def update_loop(self) -> None:
        """Once a day: is there a newer host on the public repo? Tell the phones once per version (E2E, like every message);
        never install anything (Z4: the human runs `agentj update apply`)."""
        await asyncio.sleep(UPDATE_FIRST)
        while True:
            try:
                note = await asyncio.to_thread(update.daily, self.st)
            except Exception:  # noqa: BLE001 — a failed check costs nothing
                note = None
            if note:
                latest = update.read_rec(self.st).get("latest")
                self.st.log("update_available", status=str(latest)[:32])
                self.emit("update", latest=latest, current=update.__version__)
                e = self._remember("notice", note)
                self._post(self._send_ready, lambda s, e=e: self._render(e, s))
            await asyncio.sleep(UPDATE_WAKE)

    def agent_turn_start(self, text: str) -> None:
        self.turn_t0 = time.monotonic()
        self.activity("turn_start", by=self.turn_by, text=text[:120])
        self.turn_by = None

    def agent_turn_end(self) -> None:
        self.st.log("turn_end", agent=self.agent.kind if self.agent else None)
        self.activity("turn_end", secs=round(time.monotonic() - self.turn_t0, 1) if self.turn_t0 else None,
                      result="stopped" if self.agent and self.agent.halting else "done")
        for gid in list(self.grants):     # a batch approval never outlives the turn it was given in (ADR-A48)
            self.end_grant(gid, "turn_end")
        if self.turn_text:
            self.turn_text = False
            self.push_notify("reply")

    def _ask_msg(self, a: Ask) -> dict:
        m = {"t": "ask", "id": a.rid, "tool": a.tool, "summary": a.summary, "ttl": max(0, int(a.deadline - time.monotonic())),
             "cat": list(a.cats), "why": a.why}
        if a.scope_text:
            m["batch"] = a.scope_text
            m["batch_max"], m["batch_secs"] = BATCH_MAX, BATCH_SECS
        if a.task:
            m["task"] = a.task[:80]
        return m

    # ------------------------------------------------------------ batch approval (ADR-A48)
    def _grant_msg(self, g: Grant) -> dict:
        return {"t": "grant", "id": g.rid, "scope": g.text, "left": g.left, "secs": max(0, int(g.until - time.monotonic()))}

    def _grant_live(self, g: Grant) -> bool:
        return g.left > 0 and time.monotonic() < g.until and self.st.sign_key(g.device) is not None

    def end_grant(self, gid: str, why: str, by: str | None = None) -> None:
        g = self.grants.pop(gid, None)
        if g is None:
            return
        self.st.log("grant_end", id=gid, reason=why, device=by or g.device)
        self.activity("grant_end", id=gid, why=why)
        self.emit("grant_end", id=gid, why=why)
        self._post(self._send_ready, lambda s: {"t": "grant_end", "id": gid, "why": why})

    def _grant_for(self, tool: str, scope: tuple | None) -> Grant | None:
        if scope is None:
            return None
        for g in list(self.grants.values()):
            if not self._grant_live(g):
                self.end_grant(g.rid, "limit" if g.left <= 0 else ("expired" if time.monotonic() >= g.until
                                                                    else "device_gone"))
                continue
            if g.tool == tool and danger.same_scope(g.scope, scope):
                return g
        return None

    def _approvers(self) -> list[str]:
        return [d for d, v in self.st.devices().items() if v.get("sk")]

    def new_perm_env(self) -> dict:
        """A fresh one-time token for the next agent start (L2): the permission tool must claim perm.sock with it before
        the agent gets its first message; afterwards the token is spent, so the agent reading it from its own environment
        gains nothing. Any previous claimed connection is cut."""
        self.perm_token = secrets.token_urlsafe(32)
        self.perm_claimed = asyncio.Event()
        if self.perm_w:
            with contextlib.suppress(Exception):
                self.perm_w.close()
            self.perm_w = None
        return {"AGENTJ_PERM_SOCK": str(self.st.perm_sock_path), "AGENTJ_PERM_TOKEN": self.perm_token,
                "AGENTJ_PERM_WAIT": str(int(self.ask_ttl + 30))}

    async def _perm_send(self, w: asyncio.StreamWriter, obj: dict) -> None:
        with contextlib.suppress(Exception):
            w.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            await w.drain()

    async def on_perm(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        """The agent's permission tool (permtool.py) claims this socket once per agent start, then sends every permission
        request over that one connection; each gets a signed answer from a paired phone, or deny. Every decision is a
        line in approvals.log. A connection without a valid, unspent claim is refused and logged (L2, G-A24)."""
        claimed = False
        gone: dict[int, asyncio.Future] = {}
        try:
            line = await r.readline()
            req = json.loads(line) if line else None
            tok = self.perm_token
            if (not isinstance(req, dict) or req.get("t") != "claim" or not tok
                    or not hmac.compare_digest(str(req.get("token", "")).encode(), tok.encode())):
                self.st.log("perm_refused", reason="spent" if tok is None else "bad_token")
                return
            self.perm_token, self.perm_w, claimed = None, w, True
            await self._perm_send(w, {"t": "claimed"})
            self.perm_claimed.set()
            self.st.log("perm_claimed")
            while line := await r.readline():
                m = json.loads(line)
                if not isinstance(m, dict) or not isinstance(m.get("id"), int):
                    continue
                if m.get("t") == "cancel" and m["id"] in gone and not gone[m["id"]].done():
                    gone[m["id"]].set_result(True)
                elif m.get("t") == "ask" and m["id"] not in gone:
                    gone[m["id"]] = asyncio.get_running_loop().create_future()
                    asyncio.create_task(self._perm_ask(w, m, gone))
        except (ValueError, ConnectionError, asyncio.LimitOverrunError):
            pass
        finally:
            for f in gone.values():         # the permission tool went away: every request it had open is denied
                if not f.done():
                    f.set_result(True)
            if claimed and self.perm_w is w:
                self.perm_w = None
                self.st.log("perm_lost")
                if self.agent and not self.stopping.is_set():
                    self.agent.perm_lost()      # restart the agent (with a new token) before it can act again
            with contextlib.suppress(Exception):
                w.close()

    async def _perm_ask(self, w: asyncio.StreamWriter, m: dict, gone: dict) -> None:
        rid = m["id"]
        ans = {"behavior": "deny", "message": "请求无效，已拒绝。"}
        try:
            tool, tool_input = m.get("tool"), m.get("input")
            if isinstance(tool, str) and isinstance(tool_input, dict):
                ans = await self.ask(clean_line_tool(tool), tool_input, gone.get(rid))
        finally:
            gone.pop(rid, None)
            if self.perm_w is w:
                await self._perm_send(w, {"t": "answer", "id": rid, **ans})

    async def ask(self, tool: str, tool_input: dict, gone: asyncio.Future | None = None, batch: bool = True) -> dict:
        """batch=False: never offered for (or approved by) a batch grant (a Codex sandbox / network escalation)."""
        kind = self.agent.kind if self.agent else "?"
        summary = agents.summarize(tool, tool_input)
        digest, isha = approvals.shown_digest(tool, summary), approvals.input_digest(tool_input)
        rid = secrets.token_hex(16)
        v = danger.classify(tool, tool_input, self.danger_extra)
        workdir = (self.agent_cfg or {}).get("dir")
        sc = None if v.danger or not batch else danger.batch_scope(tool, tool_input, workdir, self.danger_extra)
        base = dict(rid=rid, agent=kind, tool=tool, input_sha256=isha, shown_sha256=digest, cats=v.cats)
        if self.stopped():                # stopped: nothing is asked, nothing runs
            approvals.record(self.st, **base, decision="deny", reason="estop")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="estop")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="estop", cats=v.cats, summary=summary)
            return {"behavior": "deny", "message": "已急停（全部停下）：什么都不执行，等人恢复。"}
        g = None if v.danger else self._grant_for(tool, sc[:2] if sc else None)
        if g is not None:                 # inside a live batch approval: allowed at once, logged, one quiet line on the phone
            g.left -= 1
            approvals.record(self.st, **base, decision="allow", reason="batch", device=g.device, grant=g.rid)
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="batch", device=g.device)
            self.activity("auto", id=rid, tool=tool, summary=summary, by=self._dname(g.device), task=self.task_label)
            self.emit("auto", id=rid, tool=tool, grant=g.rid)
            line = summary.split("\n", 1)[0][:200]
            self._post(self._send_ready, lambda s: {"t": "auto", "id": rid, "tool": tool, "summary": line, "grant": g.rid})
            if g.left <= 0:
                self.end_grant(g.rid, "limit")
            else:
                self._post(self._send_ready, lambda s: self._grant_msg(g))
            return {"behavior": "allow", "updatedInput": tool_input}
        if not self._approvers():
            approvals.record(self.st, **base, decision="deny", reason="no_device")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="no_device")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="no_device", cats=v.cats, summary=summary)
            self.emit("ask_done", id=rid, result="deny", reason="no_device")
            return {"behavior": "deny", "message": "没有能批准的已配对手机：默认拒绝。先用 agentj pair 配对一台手机。"}
        if sum(1 for a in self.asks.values() if not a.fut.done()) >= MAX_ASKS:
            approvals.record(self.st, **base, decision="deny", reason="too_many")
            return {"behavior": "deny", "message": "同时等待批准的请求太多：默认拒绝。"}
        a = Ask(rid, tool, summary, digest, tool_input, isha, time.monotonic() + self.ask_ttl,
                asyncio.get_running_loop().create_future(), cats=v.cats, why=v.why,
                scope=sc[:2] if sc else None, scope_text=sc[2] if sc else None, task=self.task_label)
        self.asks[rid] = a
        self.activity("ask", id=rid, tool=tool, cats=v.cats, why=v.why or None, summary=summary, task=self.task_label)
        self.st.log("ask", id=rid, tool=tool, agent=kind, kind="danger" if v.danger else "low")
        self.emit("ask", id=rid, tool=tool, summary=summary)
        self._post(self._send_ready, lambda s: self._ask_msg(a))   # same queue as the Agent's text: order is kept
        self.status_changed()
        self.push_notify("ask")
        try:
            waiters = {a.fut} | ({gone} if gone else set())
            # FIRST_COMPLETED: the answer (or the agent giving up) ends the wait at once — with the default ALL_COMPLETED an
            # approval only took effect at the deadline (L1 bug, found in L2)
            done, _ = await asyncio.wait(waiters, timeout=max(0.0, a.deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if a.fut in done:
                decision, did, sk, sig = a.fut.result()
                # serve stopping / the stop switch resolve pending requests with no device
                reason = "device" if did else ("estop" if self.stopped() else "serve_stop")
            else:
                decision, did, sk, sig = "deny", None, None, None
                reason = "agent_gone" if gone in done else ("serve_stop" if self.stopping.is_set() else "timeout")
                if not a.fut.done():
                    a.fut.set_result((decision, None, None, None))
        finally:
            self.asks.pop(rid, None)
        approvals.record(self.st, **base, decision=decision, reason=reason, device=did, sign_pub=sk, sig=sig,
                         scope=a.scope_text if decision == "allow_batch" else None)
        raw = decision
        if decision == "allow_batch" and reason == "device":
            self._open_grant(a, did)
        decision = "allow" if decision == "allow_batch" else decision
        result = decision if reason == "device" else {"timeout": "timeout", "estop": "stopped"}.get(reason, "gone")
        self.st.log("ask_done", id=rid, tool=tool, decision=decision, reason=reason, device=did)
        self.activity("decision", id=rid, tool=tool, result=raw if reason == "device" else reason, reason=reason,
                      by=self._dname(did) if did else None, cats=a.cats, task=a.task, summary=summary[:200])
        self.emit("ask_done", id=rid, result=result, reason=reason, device=did)
        self._post(self._send_ready, lambda s: {"t": "ask_done", "id": rid, "result": result})
        self.status_changed()
        if decision == "allow":
            return {"behavior": "allow", "updatedInput": tool_input}
        msg = {"device": "用户在手机上拒绝了这个操作。", "timeout": f"手机上 {int(self.ask_ttl)} 秒内没有批准：默认拒绝。",
               "serve_stop": "agentj serve 已停止：默认拒绝。", "estop": "已急停（全部停下）：默认拒绝。"}.get(reason, "已拒绝。")
        return {"behavior": "deny", "message": msg}

    def policy_refused(self, tool: str, tool_input: dict, why: str, notice: str) -> None:
        """A request the harness asked that would give it more than the human's own configuration allows (Codex beyond its own
        sandbox, ADR-A73): refused without a card — approvals.log (reason `policy`), host.log, activity, one notice."""
        summary = agents.summarize(tool, tool_input)
        rid = secrets.token_hex(16)
        v = danger.classify(tool, tool_input, self.danger_extra)
        approvals.record(self.st, rid=rid, agent=self.agent.kind if self.agent else "?", tool=tool,
                         input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(tool, summary),
                         cats=v.cats, decision="deny", reason="policy")
        self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="policy")
        self.activity("decision", id=rid, tool=tool, result="policy", reason="policy", why=why, cats=v.cats,
                      task=self.task_label, summary=summary[:200])
        self.emit("ask_done", id=rid, result="deny", reason="policy")
        self.agent_notice(f"{notice}（{why}：{summary.splitlines()[0][:120]}）")

    def _open_grant(self, a: Ask, did: str) -> None:
        if a.scope is None:
            return
        while len(self.grants) >= MAX_GRANTS:
            self.end_grant(next(iter(self.grants)), "limit")
        g = Grant(a.rid, a.tool, a.scope, a.scope_text or "", did, BATCH_MAX, time.monotonic() + BATCH_SECS)
        self.grants[g.rid] = g
        self.st.log("grant", id=g.rid, tool=g.tool, device=did)
        self.activity("grant", id=g.rid, tool=g.tool, scope=g.text, by=self._dname(did))
        self.emit("grant", id=g.rid, scope=g.text)
        self._post(self._send_ready, lambda s: self._grant_msg(g))

    async def _answer(self, s: Session, obj: dict) -> None:
        rid, ok, sig, batch = obj.get("id"), obj.get("ok"), obj.get("sig"), obj.get("batch", False)
        a = self.asks.get(rid) if isinstance(rid, str) else None
        if a is None or a.fut.done():
            return                         # unknown or already decided (another phone, timeout): nothing to do
        if not isinstance(ok, bool) or not isinstance(sig, str) or not isinstance(batch, bool) \
                or time.monotonic() > a.deadline:
            self.st.log("answer_refused", id=rid, device=s.device, reason="shape_or_late")
            return
        if batch and (not ok or a.scope is None or a.cats):
            # a dangerous request (or one that was not offered for batching) can only be decided one by one (ADR-A47)
            self.st.log("answer_refused", id=rid, device=s.device, reason="no_batch")
            return
        sk = self.st.sign_key(s.device)
        try:
            sigb = wire.unb64u(sig)
        except ValueError:
            sigb = b""
        decision = "allow_batch" if batch else ("allow" if ok else "deny")
        scope = a.scope_text if batch else None   # the host checks the scope IT offered: a phone cannot widen it
        if not sk or not approvals.verify(sk, sigb, self.channel, s.device, rid, decision, a.digest, scope):
            self.st.log("answer_refused", id=rid, device=s.device, reason="no_key" if not sk else "bad_signature")
            return
        a.fut.set_result((decision, s.device, sk, sigb))

    # ------------------------------------------------------------ phone controls (PROTOCOL §8 "phone controls", ADR-A50 – A54)
    def stopped(self) -> bool:
        return bool(self.estop.get("on"))

    def activity(self, kind: str, **kw) -> None:
        try:
            activity.record(self.st, kind, **kw)
        except Exception:  # noqa: BLE001 — the activity log never costs anything else
            pass

    def _dname(self, did: str | None) -> str | None:
        if not did:
            return None
        return self.st.devices().get(did, {}).get("name") or did

    def _estop_msg(self) -> dict:
        by = self.estop.get("by", "")
        name = self.estop.get("by_name") or ("终端" if by == "terminal" else self._dname(by[6:]) if by.startswith("phone:") else by)
        return {"t": "estop_state", "on": self.stopped(), "at": self.estop.get("at", 0), "by": (name or "")[:64]}

    async def do_estop(self, by: str, name: str) -> None:
        """Stop everything: persist the switch first (a crash right after still comes back stopped), then deny every open
        permission request, end every batch grant, interrupt the Agent's turn (its queue is dropped), end a running task."""
        rec = controls.set_estop(self.st, True, by)
        self.estop = {**rec, "by_name": name}
        self.st.log("estop", status="on", device=by.split(":", 1)[1] if by.startswith("phone:") else None)
        self.emit("estop", on=True, by=by)
        self.activity("estop", by=name)
        for a in list(self.asks.values()):
            if not a.fut.done():
                a.fut.set_result(("deny", None, None, None))
        for gid in list(self.grants):
            self.end_grant(gid, "estop")
        busy = False
        if self.agent:
            busy = await self.agent.halt(clear_queue=True)
        ran = await self.scheduler.stop_current()
        self.status_changed()
        m = self._estop_msg()
        self._post(self._send_ready, lambda s: m)
        note = "已急停（" + name + "）：" + ("正在进行的一轮已中断；" if busy else "") + ("正在跑的定时任务已中断；" if ran else "") + \
            "待批准的全部拒绝，批量授权已收回，定时任务暂停。恢复之前 Agent 不接新消息。"
        e = self._remember("notice", note)
        self._post(self._send_ready, lambda s, e=e: self._render(e, s))

    async def do_resume(self, by: str, name: str) -> None:
        rec = controls.set_estop(self.st, False, by)
        self.estop = {**rec, "by_name": name}
        self.st.log("estop", status="off", device=by.split(":", 1)[1] if by.startswith("phone:") else None)
        self.emit("estop", on=False, by=by)
        self.activity("resume", by=name)
        self.status_changed()
        m = self._estop_msg()
        self._post(self._send_ready, lambda s: m)
        e = self._remember("notice", f"已恢复（{name}）：Agent 接收新消息，定时任务按各自的启用状态继续。")
        self._post(self._send_ready, lambda s, e=e: self._render(e, s))
        self.scheduler.wake.set()

    def task_finished(self, res: dict) -> None:
        """A scheduled run ended: its batch grants end with it; one notice to the phones (task name + the VERDICT sentence)."""
        for gid in list(self.grants):
            self.end_grant(gid, "turn_end")
        title = res.get("title") or res["id"]
        if res.get("stopped"):
            text = f"定时任务「{title}」：被急停中断"
        else:
            text = f"定时任务「{title}」：{res['verdict']} — {res['line']}" + ("（只读运行）" if res.get("readonly") else "")
        self.st.log("task_done", id=res["id"], result=res["verdict"])
        self.emit("task_done", id=res["id"], verdict=res["verdict"], line=res["line"], report=res.get("report"))
        self.activity("task_done", id=res["id"], title=title, verdict=res["verdict"], line=res["line"], secs=res.get("secs"),
                      readonly=res.get("readonly"), report=res.get("report"), stopped=res.get("stopped"))
        e = self._remember("notice", clean(text, wire.MAX_TEXT))
        self._post(self._send_ready, lambda s, e=e: self._render(e, s))
        self.push_notify("reply")

    async def _send_chunks(self, s: Session, head: dict, key: str, items: list, tail: dict | None = None) -> None:
        """Send `items` as several app messages {**head, key: [...], "more": bool} of ≤ CHUNK JSON bytes each."""
        batch, size = [], 0
        for it in items:
            n = len(json.dumps(it, ensure_ascii=False).encode())
            if batch and size + n > CHUNK:
                await self.send_app(s, {**head, key: batch, "more": True})
                batch, size = [], 0
            batch.append(it)
            size += n
        await self.send_app(s, {**head, key: batch, "more": False, **(tail or {})})

    async def _ctl_res(self, s: Session, r: str, action: str, ok: bool, **kw) -> None:
        await self.send_app(s, {"t": "ctl_res", "r": r, "action": action, "ok": ok, **kw})

    async def on_control(self, s: Session, t: str, obj: dict) -> None:
        r = obj.get("r")
        if not isinstance(r, str) or not _RID.fullmatch(r):
            return
        if t == "mem_list":
            return await self.send_memory(s, r)
        if t == "act_list":
            before = obj.get("before") if isinstance(obj.get("before"), str) else None
            items, nxt = await asyncio.to_thread(activity.page, self.st, before)
            return await self._send_chunks(s, {"t": "act_page", "r": r}, "items", items,
                                           {"next": nxt, "on": activity.enabled(self.st)})
        if t == "task_list":
            wd = self.agent_cfg["dir"] if self.agent_cfg else None
            rows = await asyncio.to_thread(tasks.rows, self.st, wd)
            for x in rows:
                x["running"] = self.scheduler.current_id == x["id"]
            return await self._send_chunks(s, {"t": "tasks", "r": r}, "items", rows,
                                           {"paused": self.stopped(), "agent": self.agent_cfg["kind"] if self.agent_cfg else None})
        # ---- writes: signed like an approval (Invariant 20)
        action = {"mem_rm": "mem_rm", "mem_undo": "mem_undo", "estop": "estop", "resume": "resume",
                  "task_set": "task_on" if obj.get("on") is True else "task_off"}[t]
        target = {k: obj.get(k) for k in ("src", "file", "fsha", "iid", "id", "tsha")}
        if action in ("mem_rm",) and not all(isinstance(target[k], str) and len(target[k]) <= 300
                                              for k in ("src", "file", "fsha", "iid")):
            return await self._ctl_res(s, r, action, False, why="shape")
        if action in ("mem_undo", "task_on", "task_off") and not (isinstance(target["id"], str) and len(target["id"]) <= 64):
            return await self._ctl_res(s, r, action, False, why="shape")
        if action in ("task_on", "task_off") and not isinstance(target["tsha"], str):
            target["tsha"] = ""
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, action, target)
        sig = obj.get("sig") if isinstance(obj.get("sig"), str) else None
        if why:
            self.st.log("control_refused", device=s.device, action=action, reason=why)
            controls.log(self.st, action=action, device=s.device, result="refused:" + why)
            self.activity("control_refused", action=action, by=s.name or s.device, why=why)
            return await self._ctl_res(s, r, action, False, why=why)
        name = s.name or s.device
        result, extra = "ok", {}
        try:
            if action == "estop":
                await self.do_estop("phone:" + s.device, name)
            elif action == "resume":
                await self.do_resume("phone:" + s.device, name)
            elif action == "mem_rm":
                if not self.agent_cfg:
                    raise memory.MemoryError_("unknown_source")
                rec = await asyncio.to_thread(memory.remove, self.st, self.agent_cfg["kind"], self.agent_cfg["dir"],
                                              target["src"], target["file"], target["fsha"], target["iid"])
                extra = {"undo": rec["id"], "label": rec["label"]}
                self.activity("mem_rm", by=name, label=rec["label"], text=memory.preview(rec, 200), undo=rec["id"])
            elif action == "mem_undo":
                rec = await asyncio.to_thread(memory.restore, self.st, target["id"])
                self.activity("mem_undo", by=name, label=rec.get("label"), text=memory.preview(rec, 200))
            else:
                wd = self.agent_cfg["dir"] if self.agent_cfg else None
                on = action == "task_on"
                await asyncio.to_thread(tasks.set_enabled, self.st, wd, target["id"], on, "phone:" + s.device,
                                        target["tsha"] if on else None)
                self.activity(action, by=name, id=target["id"])
                self.scheduler.wake.set()
        except memory.MemoryError_ as e:
            result = e.reason
        except tasks.TaskError as e:
            result = e.reason
        except OSError:
            result = "io"
        self.st.log("control", device=s.device, action=action, result=result)
        controls.log(self.st, action=action, device=s.device, result=result, obj=target, sig=sig, n=obj.get("n"),
                     ts=obj.get("ts"))
        await self._ctl_res(s, r, action, result == "ok", **({} if result == "ok" else {"why": result}), **extra)

    async def send_memory(self, s: Session, r: str) -> None:
        c = self.agent_cfg
        res = await asyncio.to_thread(memory.scan, c["kind"] if c else None, c["dir"] if c else None)
        tr = await asyncio.to_thread(memory.trash, self.st)
        srcs = []
        items = []
        for src in res["sources"]:
            srcs.append({"id": src["id"], "label": src["label"], "path": src["path"], "kind": src["kind"],
                         **({"problem": src["problem"]} if src.get("problem") else {}),
                         "n": sum(len(f["items"]) for f in src["files"]),
                         "bad": [{"file": f["file"][:120], "problem": f["problem"]} for f in src["files"] if f.get("problem")][:10]})
            for f in src["files"]:
                for it in f["items"]:
                    text = it["text"]
                    x = {"src": src["id"], "file": f["file"], "fsha": f["fsha"], "iid": it["iid"], "kind": it["kind"],
                         "text": text[:ITEM_SHOW]}
                    if len(text) > ITEM_SHOW:
                        x["cut"] = len(text)
                    for k in ("title", "desc"):
                        if it.get(k):
                            x[k] = it[k][:200]
                    items.append(x)
        trash = [{"id": t["id"], "ts": t.get("ts", 0), "label": t.get("label", ""), "text": memory.preview(t, 160)}
                 for t in tr[:30]]
        await self.send_app(s, {"t": "mem_sources", "r": r, "harness": res["harness"], "sources": srcs[:20], "trash": trash})
        await self._send_chunks(s, {"t": "mem_items", "r": r}, "items", items)

    # ------------------------------------------------------------ slash commands (slash.py, ADR-A70 – A72)
    def cmd_card(self, name: str, res: "slash.Result", by: str | None = None) -> None:
        """A command result → one chat entry `from: "cmd"` (kept in the backlog like every message) on every phone."""
        x = {"cmd": name, "ok": res.kind in ("ok", "info"), "kind": res.kind}
        if res.models:
            x["models"] = res.models[:40]
        if res.undo:
            x["undo"] = True
        if res.sep:
            x["sep"] = True
        if by:
            x["by"] = by[:64]
        text = res.text if text_units(res.text) <= wire.MAX_TEXT else res.text[:3900] + " …"
        self.emit("agent_notice", text=f"/{name}：{text}")
        e = self._remember("cmd", text, x=x)
        self._post(self._send_ready, lambda s, e=e: self._render(e, s))

    def cmd_done(self, cmd, res: "slash.Result") -> None:
        """Called by the Agent's queue when a command ran (agent.Cmd)."""
        self.st.log("slash", cmd=cmd.name, result=res.kind)
        self.activity("slash", cmd=cmd.name, result=res.kind, by=cmd.by)
        self.cmd_card(cmd.name, res, cmd.by)

    def set_model(self, model: str) -> None:
        """/model <name>: kept in config.json (`agent.model`), so a restart uses it too."""
        self.st.set_agent_model(model)
        if self.agent_cfg is not None:
            self.agent_cfg["model"] = model

    async def on_slash(self, s: Session, name: str, arg: str, confirm: bool, typed: bool) -> None:
        """A command from a ready session of a paired device (the only place one can come from). /stop and /help at once;
        the rest after the turn in front of it (the Agent's queue). Logged: command name + result class, never text."""
        by = s.name or s.device
        self.st.log("slash_in", cid=s.cid, device=s.device, cmd=name if name in slash.WHITELIST + slash.INTERNAL else "other")

        def done(res):
            self.st.log("slash", cmd=name if name in slash.WHITELIST + slash.INTERNAL else "other", result=res.kind)
            self.activity("slash", cmd=name if name in slash.WHITELIST + slash.INTERNAL else "other", result=res.kind, by=by)
            self.cmd_card(name if name in slash.WHITELIST + slash.INTERNAL else "refused", res, by)
        if not self.agent:
            return done(slash.Result(slash.HELP if name == "help" else "还没接 Agent：在电脑上运行 agentj agent claude --dir <目录>。",
                                     "info" if name == "help" else "error"))
        if name not in slash.WHITELIST + slash.INTERNAL or (name in slash.INTERNAL and typed):
            if self.agent.kind == "claude" and not getattr(self.agent, "init", None):
                return done(slash.Result("还不知道 Claude Code 有哪些技能：先发一条普通消息，再试一次。\n" + slash.REFUSE,
                                         "refused"))
            return done(slash.Result(slash.REFUSE, "refused"))
        if name == "help":
            return done(await self.agent.command("help", arg))
        if name == "stop":
            return done(await self.stop_turn(by))
        if self.stopped():
            return done(slash.Result("已急停：恢复之后再用这个命令。", "refused"))
        if name == "clear" and not confirm:
            return done(slash.Result("清空要在手机上确认：点输入框旁的「命令」→「清空」。", "info"))
        if self.agent.status in ("working", "compacting") or not self.agent.q.empty():
            self.cmd_card(name, slash.Result("这一轮结束后执行。", "info"), by)
        self.agent.submit(agents.Cmd(name, arg, by))

    async def stop_turn(self, by: str) -> "slash.Result":
        """/stop: the stop switch's interrupt for the running turn (and a running scheduled task) — nothing is paused,
        the queue and every task's enabling stay as they are."""
        busy = await self.agent.halt(clear_queue=False) if self.agent else False
        ran = await self.scheduler.stop_running()
        self.status_changed()
        if busy or ran:
            return slash.Result("已停下" + ("这一轮" if busy else "") + ("、正在跑的定时任务" if ran else "") +
                                "。没有暂停任何东西：可以接着发消息。")
        return slash.Result("现在没有正在进行的一轮。", "info")

    # ------------------------------------------------------------ Web Push (PROTOCOL §9): no content, host → push service
    def _push_key(self):
        if self.push_key is None:
            self.push_key = webpush.load_vapid(self.st)
        return self.push_key

    def push_notify(self, kind: str) -> None:
        subs = webpush.load_subs(self.st)
        if not subs:
            return
        visible = {s.device for s in self.sessions.values() if s.state == "ready" and s.fg}
        now = time.monotonic()
        for did, sub in subs.items():
            if did in visible or did not in self.st.devices():
                continue
            if now - self.push_last.get((did, kind), -1e9) < PUSH_GAP[kind]:
                continue
            self.push_last[(did, kind)] = now
            fut = in_daemon_thread(webpush.send, self._push_key(), sub, kind, "high" if kind == "ask" else "normal")
            fut.add_done_callback(lambda f, did=did: self._push_done(did, f))

    def _push_done(self, did: str, f) -> None:
        try:
            status = f.result()
        except Exception:  # noqa: BLE001
            status = 0
        self.st.log("push_ok" if status in (200, 201, 202) else "push_fail", device=did,
                    status=cloud.status_class(status) if status else "network")
        if status in (404, 410):            # the browser dropped this subscription
            subs = webpush.load_subs(self.st)
            if subs.pop(did, None):
                webpush.save_subs(self.st, subs)

    async def stdin_loop(self) -> None:
        # a daemon thread, so a blocked readline never holds up shutdown
        loop, q = asyncio.get_running_loop(), asyncio.Queue()
        threading.Thread(target=lambda: [loop.call_soon_threadsafe(q.put_nowait, ln) for ln in iter(sys.stdin.readline, "")]
                         + [loop.call_soon_threadsafe(q.put_nowait, None)], daemon=True).start()
        while (line := await q.get()) is not None:
            line = line.rstrip("\n")
            if line and text_units(line) > wire.MAX_TEXT:
                print(f"· 太长了（上限 {wire.MAX_TEXT} 字），没有发送", flush=True)
            elif line:
                n = await self.broadcast(line)
                if not n:
                    print("· 没有在线的已批准设备", flush=True)

    # ------------------------------------------------------------ main
    async def run(self) -> None:
        fence.no_dump()   # same-user processes cannot read this process's memory, environment or sockets
        sock = self.st.sock_path
        if sock.exists():
            try:
                _, w = await asyncio.open_unix_connection(str(sock))
                w.close()
                raise SystemExit(f"agentj serve is already running ({sock})")
            except (ConnectionRefusedError, FileNotFoundError):
                sock.unlink()
        old = os.umask(0o177)
        try:
            server = await asyncio.start_unix_server(self.on_ctl, path=str(sock))
        finally:
            os.umask(old)
        os.chmod(sock, 0o600)
        self.st.log("serve_start", channel=self.channel)
        self.post_q = asyncio.Queue()
        jobs = [asyncio.create_task(self.relay_loop()), asyncio.create_task(self.reporter.run()),
                 asyncio.create_task(self.sync_loop()), asyncio.create_task(self.post_loop()),
                 asyncio.create_task(self.update_loop())]
        perm_server = None
        if self.agent_cfg:
            psock = self.st.perm_sock_path
            self.st.perm_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.st.perm_dir, 0o700)
            with contextlib.suppress(FileNotFoundError):
                psock.unlink()
            old = os.umask(0o177)
            try:
                perm_server = await asyncio.start_unix_server(self.on_perm, path=str(psock), limit=8 * 1024 * 1024)
            finally:
                os.umask(old)
            os.chmod(psock, 0o600)
            self.agent = agents.make(self, self.agent_cfg)
            self.agent.start()
            self.sent_status = self.eff_status()
            self.st.log("agent_on", agent=self.agent.kind, fence=self.agent_cfg.get("fence", True))
        jobs.append(asyncio.create_task(self.scheduler.loop()))
        if self.read_stdin:
            jobs.append(asyncio.create_task(self.stdin_loop()))
        try:
            await self.stopping.wait()
        finally:
            for a in list(self.asks.values()):      # serve stopping: every pending request is denied
                if not a.fut.done():
                    a.fut.set_result(("deny", None, None, None))
            await self.scheduler.stop_current()
            if self.agent:
                await self.agent.stop()
            await asyncio.sleep(0)
            for t in jobs:
                t.cancel()
            server.close()
            if perm_server:
                perm_server.close()
                with contextlib.suppress(FileNotFoundError):
                    self.st.perm_sock_path.unlink()
            with contextlib.suppress(FileNotFoundError):
                sock.unlink()
            self.st.log("serve_stop")


def _sign_key(info: dict) -> bytes:
    """The device's Ed25519 approval key from a msg1 payload ("sk", base64url, 32 bytes) — or b"" (older client)."""
    v = info.get("sk")
    if not isinstance(v, str) or len(v) > 64:
        return b""
    try:
        b = wire.unb64u(v)
    except ValueError:
        return b""
    return b if len(b) == 32 else b""


def clean_line_tool(tool: str) -> str:
    from .text import clean_line
    return clean_line(tool, 64) or "?"
