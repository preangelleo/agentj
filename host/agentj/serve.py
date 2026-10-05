"""`agentj serve`: the host daemon. Dials the blind relay, runs Noise with each device, approves pairings only via the
local control socket (driven by `agentj pair` in a terminal), shows plaintext only on this machine's terminal.

Spec: protocol/PROTOCOL.md. Logs (host.log) carry metadata only, never message text.

Relay parity (PROTOCOL §10, PROMPT-33): a device that announces capability `p33` in its hello gets the bigger limits (60 KiB
messages, 20 000-unit text, `frag` for anything larger), the persistent history instead of §8's `msg` stream (history.py:
every message, reply, notice, command result and task run is a page), `say` with attachments / quote / withdraw (compose.py),
end-to-end blobs (uploads.py → inbox.py), voice transcribed on this computer (asr.py), questions as signed cards, meters, the
model / effort pill and the menu (menu.py); after it is ready the relay is told to raise its frame budget (op 0x03 BULK). An
older device keeps exactly §3 / §8.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from websockets.asyncio.client import connect

from . import agent as agents
from . import notices, activity, approvals, cloud, controls, danger, fence, gate, memory, slash, tasks, update, webpush, wire
from . import asr as asr_mod
from . import compose, history, inbox, menu, uploads, preferences
from . import elevate  # F17: sudo + secret cards (PROTOCOL §11)
from .noise import IK, IKPSK2, CipherState, Handshake, NoiseError
from .reporter import Reporter, in_daemon_thread
from .envcompat import getenv
from .state import MAX_DEVICES, DeviceLimit, State
from .text import LABEL_DIGITS, LABEL_MAX, clean, clean_label, clean_line, text_units  # noqa: F401  (re-exported)


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
PUSH_GAP = {"reply": 20, "ask": 2, "security": 2}   # minimum seconds between two pushes of a kind to one device
BATCH_MAX = 20         # automatic approvals one batch grant may give (ADR-A48) …
BATCH_SECS = 600       # … within this many seconds, and never past the end of the Agent's turn
MAX_GRANTS = 8         # batch grants open at once
CHUNK = 11 * 1024      # phone-control answers are split into app messages of about this many JSON bytes (MAX_JSON = 16 KiB)
ITEM_SHOW = 6000       # a memory item longer than this is shown cut on the phone (its id still covers the whole text)
_RID = re.compile(r"[A-Za-z0-9_-]{1,32}")
PHONE_CONTROLS = ("mem_list", "mem_rm", "mem_undo", "act_list", "task_list", "task_set", "estop", "resume")
# §10 (PROMPT-33)
Q_TTL = _ttl("AGENTJ_TEST_Q_TTL", 180)     # a question nobody answers is ended (relay's default)
MAX_QUESTIONS = 4                           # open at once; more are answered "nobody answered" at once
ASR_TAKE = 60.0                             # s per take (asr_res timeout)
ASR_QUEUE = 3                               # takes waiting per device (more → busy)
FF_MAX_SECS = 119                           # say-time ffmpeg: at most this much audio (≤ uploads.ASR_MAX as WAV)
FF_CPU_SECS = 60                            # RLIMIT_CPU of one conversion
# declared MIME → the ffmpeg demuxer (pinned: no content probing, so a playlist or another format is never opened)
FF_DEMUX = {"video/mp4": "mov", "audio/webm": "matroska", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mov", "audio/aac": "aac",
            "audio/wav": "wav", "audio/x-wav": "wav"}
METER_GAP = 2.0                             # ≤ 1 meter message per 2 s
HIST_READY = 50                             # turns a p33 device gets on ready (it pages for more)
SWEEP_EVERY = 60
RETAIN_EVERY = 86_400
P33_ONLY = ("say", "say_cancel", "blob_open", "blob_chunk", "blob_end", "blob_drop", "hist_get", "menu_get", "model_set",
            "q_answer", "tts_get", "pref_set", "notice_read")
# F14: paired phones may change optional warnings, session mode, isolation, docker and the upgrade mode (F19); native permissions stay authoritative.
PREF_SET_KEYS = ("updates.mode", "appearance.language", "appearance.theme", "voice.wake_enabled", "voice.speak_replies",
                 "agent.high_risk_warnings", "agent.session_mode", "agent.isolation", "agent.allow_docker")
METER_KEYS = ("model", "model_name", "effort", "ctx", "h5", "week")
Q_CANCEL = "用户在手机上取消了这个问题，没有选择任何选项。请不要替他做选择，停下来等他直接输入文字。"
Q_TIMEOUT = "没有人作答，请改用文字列出选项"
ASR_WHY = {"not_installed": "not_installed", "bad_audio": "bad_audio", "timeout": "timeout", "busy": "busy",
           "engine_failed": "broken", "off": "off", "broken": "broken", "no_speech": "no_speech"}


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
    p33: bool = False             # the device announced capability "p33" in its hello (PROTOCOL §10.0)
    hist: dict | None = None      # its hello's {"epoch", "last"}: where its history pages stop (§10.5)
    iid: str = ""                 # 0.15.1: the browser install's per-host id from the pairing msg1 (same browser → replace)
    gone: str = ""                # 0.15.1: a removed device that resumed: "replaced" | "revoked" — told so after its hello


@dataclass
class Question:
    """A question the Agent asked (AskUserQuestion / requestUserInput / question.asked), waiting for a signed pick (§10.7)."""
    qid: str
    qs: list
    digest: str                   # approvals.question_digest(qs): what approvals.log keeps
    deadline: float               # monotonic
    fut: asyncio.Future
    task: str | None = None


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


def approval_summary(tool, tool_input, verdict):
    if 'credentials' in verdict.cats:
        return f"{tool} · 读取或修改凭据 / Read or change credentials.\n具体内容仅在电脑查看，不发送凭据到手机。 / Inspect exact details on the computer."
    return agents.summarize(tool, tool_input)


class Host:
    def __init__(self, st: State, events: str = "text", read_stdin: bool = True):
        self.st = st
        self.cfg = st.config()
        self.channel = self.cfg["channel"]
        self.kp = st.host_keypair()
        self.sk = st.signing_key()
        self.events = events
        self.read_stdin = read_stdin
        self.telegram = None
        self.ws = None
        self.sessions: dict[int, Session] = {}
        self.pairing: Pairing | None = None
        self.send_lock = asyncio.Lock()
        self.stopping = asyncio.Event()
        self.relay_up = False
        self.name_refused = False   # one agent_name_refused line per refused streak, not one per sync
        self.official_pending = {}
        self.reporter = Reporter(st, self.report_view, on_account=self.account_language, on_notices=self.official_notices)  # Dashboard metadata reports (§7); no-op unless linked
        # agent bridge (L1, PROTOCOL §8) and Web Push (§9)
        try:
            self.preferences = preferences.validate(preferences.read()[1])
            from . import voice
            voice.validate_runtime(self.preferences)
            self.config_problem = None
        except (preferences.ConfigError,OSError) as e:
            try:
                self.preferences = preferences.validate(json.loads(preferences.runtime(st).read_text()))
            except (OSError, ValueError):
                self.preferences = preferences.defaults()
            self.config_problem = e.result() if isinstance(e,preferences.ConfigError) else {"ok":False,"key":"/","error":"configuration unreadable"}
        self.config_stamp = None
        self.config_apply_lock = asyncio.Lock()
        self.ask_ttl = min(ASK_TTL, preferences.get(self.preferences, "approval.timeout", ASK_TTL))
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
        # relay parity (PROTOCOL §10, PROMPT-33)
        self.hist = history.History(st)
        self.lang = compose.lang_of(st)
        self.uploads = uploads.Uploads(st, self.agent_cfg["dir"] if self.agent_cfg else None,
                                       asr_off=lambda: self.asr_state() == "off")
        self.sends = compose.Sends()
        self.questions: dict[str, Question] = {}
        self.cur_turn: int | None = None        # the history page the running Agent turn answers
        self.cur_failed = False
        self.meter_state = {k: None for k in METER_KEYS}
        self.meter_at = 0.0
        self.meter_task: asyncio.Task | None = None
        self.asr = asr_mod                       # tests swap in a stand-in engine
        self.asr_lock: asyncio.Lock | None = None
        self.ff_lock: asyncio.Lock | None = None    # one say-time ffmpeg conversion at a time (P33-X09)
        self.asr_waiting: dict[str, int] = {}
        self.model_sets = 0
        self.elevate = elevate.Elevator(self)   # F17: admin password / secret cards (PROTOCOL §11)

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
            print(f"· Agent 名已按账号后台改为「{kw['name']}」", flush=True)
        elif ev == "agent_name_refused":
            print("· 账号后台发来的 Agent 名用不了，这台电脑保留原名", flush=True)
        elif ev == "remote_unbind":
            print(f"· 账号后台请求解绑遥控器 {kw.get('name') or ''} {kw['device']}：{kw['result']}", flush=True)
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
        """One app message. To a p33 session (§10.0 / §10.1) up to 60 KiB per frame, anything larger as back-to-back `frag`
        frames under the same lock (nothing in between); to an older session §3's 16 KiB."""
        async with self.send_lock:  # nonce order == wire order
            if self.sessions.get(s.cid) is not s:  # closed / revoked while this send was queued
                return False
            if s.p33:
                try:
                    msgs = wire.frag_split(obj, secrets.token_hex(8))
                except ValueError:
                    self.st.log("send_too_big", cid=s.cid, kind=str(obj.get("t"))[:16])
                    return False
                limit = wire.MAX_JSON_P33
            else:
                msgs, limit = [obj], wire.MAX_JSON
            for i, m in enumerate(msgs):
                # P33-X07: a revoke detaches the session synchronously while an earlier fragment's write is awaited —
                # re-check before every fragment, so a revoked device never gets the rest of a big message
                if i and (self.sessions.get(s.cid) is not s or (s.state == "ready" and not self.st.is_allowed(s.pub))):
                    self.st.log("frag_abort", cid=s.cid, device=s.device)
                    return False
                ct = s.send.encrypt(b"", wire.pad_json(m, limit))
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
            obj = wire.unpad_json(s.recv.decrypt(b"", body), wire.MAX_JSON_P33 if s.p33 else wire.MAX_JSON)
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
        s.iid = _iid(info)
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
            # 0.15.1: finish the handshake and, after its hello, tell the device WHY (PROTOCOL §3 `removed`) — a page that
            # only sees the close cannot tell "a newer pairing took your place" from "removed" from a passing hiccup.
            # Nothing else is answered on this session; the IK handshake proved the device holds the key it claims.
            did = wire.device_id(hs.rs)
            self.st.log("unknown_device", cid=s.cid, device=did)
            msg2 = hs.write_message(b"")
            s.send, s.recv, s.h = hs.split()
            s.pub, s.device, s.mode, s.state = hs.rs, did, "gone", "hello"
            s.gone = self.st.removed_why(did) or "revoked"
            await self._op(wire.OP_DATA, s.cid, bytes([wire.HS_RESP]) + msg2)
            return
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
            if s.mode == "gone":
                await self.send_app(s, {"t": "removed", "why": s.gone})
                return await self.close_cid(s.cid, "unknown_device")
            caps = obj.get("caps")
            s.p33 = isinstance(caps, list) and wire.CAP_P33 in caps[:16]
            h = obj.get("hist")
            if s.p33 and isinstance(h, dict) and type(h.get("epoch")) is int and type(h.get("last")) is int:
                s.hist = {"epoch": h["epoch"], "last": h["last"]}
            if s.mode == "resume":
                if not self.st.is_allowed(s.pub):  # revoked between msg1 and hello
                    return await self.close_cid(s.cid, "revoked")
                s.state = "ready"
                self._set_timer(s, None)
                await self.send_app(s, {"t": "ready", **self._caps(s)})
                await self._bulk(s)
                self.st.log("resume_ok", cid=s.cid, device=s.device)
                with contextlib.suppress(OSError):
                    self.st.touch_seen(s.device)
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
            limit = wire.MAX_TEXT_P33 if s.p33 else wire.MAX_TEXT
            if not isinstance(text, str) or text_units(text) > limit:  # reject, never silently truncate
                return await self.close_cid(s.cid, "bad_msg")
            if self.stopped() and self.agent:      # stopped: nothing is queued to run later behind the human's back
                self.st.log("msg_refused", cid=s.cid, device=s.device, reason="estop")
                self.activity("message_refused", by=s.name or s.device, text=text[:120])
                if not s.p33:
                    await self.send_app(s, {"t": "msg", "id": secrets.token_hex(8), "ts": int(time.time() * 1000),
                                            "seq": self.seq, "from": "notice",
                                            "text": "已急停：这条没有交给 Agent，也不会排队。恢复后再发。"})
                return
            cmd = slash.parse(text)
            if cmd and self.agent and not self.agent.passthrough(cmd[0]):
                return await self.on_slash(s, cmd[0], cmd[1], confirm=False, typed=True)
            await self._accept(s, text, sid=None)
        elif t in P33_ONLY:
            if s.p33:                          # §10 messages only from a device that announced p33 (§10.0)
                await self.on_p33(s, t, obj)
        elif t == "answer":
            await self._answer(s, obj)
        elif t in elevate.PHONE_TYPES:  # F17: a signed, sealed sudo password / secret for an open card (§11)
            await self.elevate.on_phone(s, obj)
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
        elif t == "slash":              # a command from the phone's ≡「全部命令」 menu (or typed: the page sends it like this)
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

    def _online(self) -> set:
        return {x.device for x in self.sessions.values() if x.state == "ready"}

    def _pending_event(self, s: Session) -> dict:
        """What `agentj pair` shows while a device waits. 0.15.1 (P55): a full allowlist no longer blocks — the event says
        which remote approving would replace: `replaces` (the same browser paired before under a lost key) or `evict` (the
        stalest remote, offline first). Approving = consent; `agentj revoke` stays for picking one by hand."""
        ev = {"ev": "pending", "name": s.name, "device": s.device, "limit": MAX_DEVICES, "deadline_in": max(0, int(s.deadline - time.monotonic()))}
        devs = self.st.devices()
        online = self._online()
        twin = next((k for k, v in devs.items() if s.iid and isinstance(v, dict) and v.get("iid") == s.iid and k != s.device), None)
        if twin:
            ev["replaces"] = {"id": twin, "name": devs[twin].get("name", ""), "paired_at": devs[twin].get("paired_at", 0),
                              "online": twin in online}
        else:
            cand = self.st.evict_candidate(online, s.device)
            if cand:
                ev["evict"] = cand
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
        try:   # 0.15.1: full → the stalest remote makes room (State.pair_device); same browser → its old record goes
            got = self.st.pair_device(s.pub, s.name, s.sign_pub or None, iid=s.iid or None, evict=True,
                                      online=self._online() - {s.device})
        except DeviceLimit:   # not reachable with evict=True; kept so a future cap change fails closed
            res = {"ev": "denied", "reason": "device_limit"}
            await self.close_cid(s.cid, "device_limit", res)
            return res
        for kind in ("replaced", "evicted"):
            if got[kind]:
                self.st.log("auto_unbind", device=got[kind]["id"], reason="same_browser" if kind == "replaced" else "full")
                await self._drop_device(got[kind]["id"], "replaced")
        s.state = "ready"
        self.reporter.trigger("approve")
        await self.send_app(s, {"t": "approved", **self._caps(s)})
        await self._bulk(s)
        self.st.log("pair_approved", cid=s.cid, device=s.device, name=s.name, code_ok=True)
        self.emit("approved", device=s.device, name=s.name)
        asyncio.create_task(self.on_ready(s, None))
        res = {"ev": "approved", "device": s.device, "name": s.name}
        res.update({k: got[k] for k in ("replaced", "evicted") if got[k]})
        return res

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
                if cmd == "opencode_auth":
                    agent = self.agent
                    result = await agent.authentication() if agent and hasattr(agent, "authentication") else {"known": False}
                    await self._ctl_send(w, {"ok": True, **result})
                elif cmd == "status":
                    await self._ctl_send(w, {"ok": True, "relay_up": self.relay_up, "channel": self.channel,
                                             "agent": self.agent.kind if self.agent else None, "agent_status": self.eff_status(),
                                             "asks": len(self.asks), "estop": self.estop,
                                             "task_running": self.scheduler.current_id,
                                             "sessions": [{"device": s.device, "name": s.name, "state": s.state}
                                                          for s in self.sessions.values() if s.state != "new"]})
                elif cmd == "config_apply":
                    await self._ctl_send(w, await self.apply_preferences(req.get("raw")))
                elif cmd == "config_revision":
                    import hashlib
                    revision=hashlib.sha256(json.dumps(self.preferences,sort_keys=True).encode()).hexdigest()
                    await self._ctl_send(w,{"ok":True,"revision":revision})
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
                elif cmd == "history_clear" and req.get("all") is True:     # `agentj history clear --all` (P33-C07)
                    n = self.hist.purge()
                    self.hist_meta_all()
                    await self._ctl_send(w, {"ok": True, "deleted": n, "epoch": self.hist.epoch})
                elif cmd == "history_clear":        # `agentj history clear` while serve runs (§10.5): archive + new epoch
                    name = self.hist.reset("cli")
                    if name:
                        self.hist_meta_all()
                    self.st.log("history_clear", status="ok" if name else "empty")
                    await self._ctl_send(w, {"ok": True, "archived": bool(name), "epoch": self.hist.epoch})
                elif cmd == "history_reload":       # `agentj history on|off` / `agentj config history …`
                    self.hist = history.History(self.st)
                    self.hist_meta_all()
                    await self._ctl_send(w, {"ok": True, "on": self.hist.on})
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
        n = await self._drop_device(did, "revoked", removed)
        return removed, n

    async def _drop_device(self, did: str, why: str, removed: bool = True) -> int:
        """A device already left the allowlist (revoke, or replaced by a newer pairing): end its sessions, its push
        subscription and its uploads. Its page learns why on its next resume (`removed`, PROTOCOL §3)."""
        victims = [c for c, s in list(self.sessions.items()) if s.device == did]
        for c in victims:
            self._detach(c)
        if removed:
            self.st.log("revoked", device=did, reason=why)
            self.reporter.trigger("revoke")
            self.emit("revoked", device=did)
            subs = webpush.load_subs(self.st)
            if subs.pop(did, None):
                webpush.save_subs(self.st, subs)
            with contextlib.suppress(Exception):
                self.uploads.device_gone(did)       # its partial uploads and never-announced files go too
        for c in victims:
            await self._notify_close(c)
            self.st.log("close", cid=c, device=did, reason=why)
        return len(victims)

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
        """Callers check text_units(text) <= MAX_TEXT first. Kept in the in-memory backlog for devices that reconnect; a page
        `src.k: host` for p33 devices (what the human at the computer said to the phones)."""
        entry = self._remember(frm, text)
        turn = self.hist.add({"k": "host", "text": wire.well_formed(text)}, end="done")
        n = 0
        for s in list(self.sessions.values()):
            if s.state == "ready" and self.st.is_allowed(s.pub):
                n += await self.send_app(s, {"t": "hist_turn", "epoch": self.hist.epoch, "turn": turn} if s.p33
                                         else self._render(entry, s))
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
        if s.p33: await self.send_app(s, self.preferences_msg())
        await self.send_app(s, self._estop_msg())
        await self.send_app(s, {"t": "push_key", "k": webpush.b64u(webpush.vapid_public(self._push_key()))})
        if s.p33:                       # §10.5: history pages instead of §8's msg replay; meters, models (§10.10 / §10.11)
            await self.send_app(s, self._meter_msg())
            if self.agent:
                await self.send_app(s, self._models_msg())
            await self.send_app(s, {"t": "hist_meta", **self.hist.meta()})
            h = s.hist or {}
            if h.get("epoch") == self.hist.epoch:
                turns, more = self.hist.page(after=h.get("last", 0), limit=HIST_READY)
                if more:                # more than 50 missed: the newest 50; it pages back with hist_get before
                    turns, _ = self.hist.page(limit=HIST_READY)
            else:
                turns, _ = self.hist.page(limit=HIST_READY)
            for t in turns:
                await self.send_app(s, {"t": "hist_turn", "epoch": self.hist.epoch, "turn": t})
            for q in list(self.questions.values()):
                if not q.fut.done():
                    await self.send_app(s, self._question_msg(q))
        else:
            for e in list(self.backlog):
                if e["seq"] > since:
                    await self.send_app(s, self._render(e, s))
        for a in list(self.asks.values()):
            if not a.fut.done():
                await self.send_app(s, self._ask_msg(a))
        for g in list(self.grants.values()):
            if self._grant_live(g):
                await self.send_app(s, self._grant_msg(g))
        await self.elevate.on_ready(s)

    # ------------------------------------------------------------ agent bridge (PROTOCOL §8)
    def eff_status(self) -> str:
        if self.stopped():
            return "stopped"
        if not self.agent:
            return "none"
        if any(not a.fut.done() for a in self.asks.values()) or self._open_question():
            return "waiting"
        return self.agent.status

    def _open_question(self) -> bool:
        return any(not q.fut.done() for q in self.questions.values())

    def _status_msg(self) -> dict:
        """PROTOCOL §8 status; `name` = the Agent's display name (or null), so the phone can show it (title, top left).
        §10.10: `kind: "question"` while a question is open (the phone's purple)."""
        m = {"t": "status", "s": self.eff_status(), "agent": self.agent.kind if self.agent else None,
             "name": self.st.agent_name()}
        if m["s"] == "waiting" and self._open_question():
            m["kind"] = "question"
        return m

    def preferences_msg(self):
        # Preferences contain only pure data; no environment values or sensitive State fields.
        value = dict(self.preferences)
        value = preferences.merge(value, {})
        # Local executable configuration never belongs on the phone.
        value.get("voice", {}).get("tts", {}).pop("command", None)
        if not preferences.get(value, "voice.wake_word"):
            preferences.put(value, "voice.wake_word", "嘿 " + (self.st.agent_name() or "Agent J"))
        from . import wake
        try:
            value["voice"]["wake_tokens"] = wake.keyword(preferences.get(value,"voice.wake_word"),preferences.get(value,"voice.wake_pronunciation",""))
        except preferences.ConfigError as e:
            value["voice"]["wake_tokens"] = ""
            if preferences.get(value,"voice.wake_enabled"):
                return {"t":"preferences","value":value,"problem":e.result(),"host":self.host_info()}
        return {"t": "preferences", "value": value, "problem": self.config_problem, "host": self.host_info()}

    def host_info(self) -> dict:
        """P44 (C6): read-only facts the phone's settings panel shows next to `value` — this computer's program version,
        when its language was last set (A1), the phone-settable keys, and the paired phones (metadata only: id, label,
        paired_at, online now). The safety switches are in `value.agent.*`; the panel shows them read-only with the
        `agentj config …` command."""
        lang, at = preferences.language_state(self.st, self.preferences)
        online = {x.device for x in self.sessions.values() if x.state == "ready" and x.device}
        devs = []
        for did, v in sorted(self.st.devices().items(), key=lambda kv: (kv[1] or {}).get("paired_at", 0) if isinstance(kv[1], dict) else 0):
            if isinstance(did, str) and isinstance(v, dict):
                pa = v.get("paired_at")
                devs.append({"id": did, "name": clean_label(str(v.get("name", ""))), "online": did in online,
                             "paired_at": int(pa) if isinstance(pa, (int, float)) and not isinstance(pa, bool) else 0})
        return {"version": update.__version__, "language_at": at, "settable": list(PREF_SET_KEYS), "devices": devs}

    async def apply_preferences(self, raw=None, ack=None, language_at=None):
        """Activate a configuration (raw = new file text to commit; None = the file as it is now). ack(result) is awaited
        before the `preferences` broadcast (pref_res first, C6). language_at = the account's time when the account's
        language is being applied (A1), so it is not reported back as a newer host change."""
        async with self.config_apply_lock:
            old = self.preferences
            try:
                candidate = preferences.validate(preferences.parse(raw) if isinstance(raw,str) else preferences.read()[1])
                # Providers and channels with external dependencies are checked before activation.
                from . import voice
                voice.validate_runtime(candidate)
                if isinstance(raw,str):
                    preferences.commit_files(self.st,raw,candidate)
                else:
                    preferences.save_good(self.st,candidate)
                self.preferences = candidate
                self.ask_ttl = min(ASK_TTL, preferences.get(candidate, "approval.timeout"))
                self.config_problem = None
            except (preferences.ConfigError, OSError) as e:
                self.preferences = old
                self.config_problem = e.result() if isinstance(e, preferences.ConfigError) else {"ok": False, "key": "/", "error": "runtime persistence failed ("+type(e).__name__+", errno="+str(e.errno)+")"}
                if ack:
                    await ack(self.config_problem)
                await self._send_ready(lambda _: self.preferences_msg())
                return self.config_problem
            lang_changed = preferences.get(old, "appearance.language") != preferences.get(candidate, "appearance.language")
            preferences.note_language(self.st, candidate, language_at)
            if lang_changed:
                self.language_changed(preferences.get(candidate, "appearance.language"), from_account=language_at is not None)
            restart = any(preferences.get(old, key) != preferences.get(candidate, key)
                          for key in ("agent.working_root", "agent.instructions", "agent.isolation", "agent.allow_docker",
                                      "proxy.https_env", "proxy.http_env", "proxy.no_proxy_env"))
            if self.agent_cfg is not None:   # F14: the next Agent process starts with the new isolation / docker choice
                raw = self.st.config().get("agent") or {}
                self.agent_cfg["fence"] = raw.get("fence") is not False and preferences.get(candidate, "agent.isolation", True) is not False
                self.agent_cfg["docker"] = raw.get("docker") is True or preferences.get(candidate, "agent.allow_docker", False) is True
            result = {"ok": True, "applied": not restart,
                      "verify": {"ok": True, "detail": "stored; restart serve for Agent instructions or proxy variables" if restart else "host configuration activated; voice needs a listening test"},
                      "needs": ["restart serve"] if restart else []}
            if ack:
                await ack(result)
            await self._send_ready(lambda _: self.preferences_msg())
            return result

    def language_changed(self, lang, from_account: bool = False) -> None:
        """A1: the one language changed. The main Agent speaks it from its next turn (identity text, hot); the account
        learns a local change with the next report (soon: triggered here)."""
        if self.agent_cfg is not None:
            self.agent_cfg["language"] = lang
        if self.agent is not None and getattr(self.agent, "cfg", None) is not None and not self.agent.cfg.get("_workflow_ceo"):
            self.agent.cfg["language"] = lang
            self.agent.identity_changed()
        self.st.log("language_set", status=lang, trigger="account" if from_account else "local")
        if not from_account:
            self.reporter.trigger("language")

    async def set_pref(self, key: str, value, ack=None, language_at=None) -> dict:
        """One user-tier key written the way `agentj config set` writes it (structural JSON5 edit under preferences.lock,
        history, last-good), then activated. Used by `pref_set` (C6) and the account language sync (A1)."""
        import fcntl
        fd = os.open(self.st.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            for _ in range(50):                      # ≤ 5 s; never block the event loop on the file lock
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
            else:
                res = {"ok": False, "key": key, "error": "busy"}
                if ack:
                    await ack(res)
                return res
            try:
                preferences.ensure()
                raw, _ = preferences.read()
                newraw = preferences.edit(raw, key, value)
            except (preferences.ConfigError, OSError, ValueError, AssertionError, IndexError, KeyError):
                res = {"ok": False, "key": key, "error": "configuration unreadable"}
                if ack:
                    await ack(res)
                return res
            return await self.apply_preferences(newraw, ack=ack, language_at=language_at)
        finally:
            os.close(fd)

    def account_language(self, account: dict) -> None:
        """Reporter callback (A1): the account's language after the server's merge. Applied only when strictly newer
        than this host's (last write wins; ties keep what is here — the server already kept the account value)."""
        if not preferences.account_is_newer(self.st, account, self.preferences):
            return
        if preferences.get(self.preferences, "appearance.language") == account["language"]:
            preferences.note_language(self.st, self.preferences, account["language_at"])
            return
        self.st.log("language_synced", status=account["language"])
        asyncio.get_running_loop().create_task(
            self.set_pref("appearance.language", account["language"], language_at=account["language_at"]))

    async def on_pref_set(self, s: Session, obj: dict) -> None:
        """C6 `pref_set {r, key, value}` from a ready, paired p33 session (E2E). Whitelist PREF_SET_KEYS only; the value
        is checked against the schema (type / enum) before anything is written. → `pref_res {r, ok, key, problem?}`,
        then the normal `preferences` broadcast."""
        r, key, value = obj.get("r"), obj.get("key"), obj.get("value")
        if not wire.is_rid(r):
            return
        key_out = key if isinstance(key, str) and len(key) <= 64 else None

        async def res(ok: bool, problem: str | None = None) -> None:
            m = {"t": "pref_res", "r": r, "ok": ok, "key": key_out}
            if problem:
                m["problem"] = problem
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, m)
        if key not in PREF_SET_KEYS:
            self.st.log("pref_set_refused", device=s.device)
            return await res(False, "not_allowed")
        meta = preferences.SCHEMA[key]
        typ_ok = {"boolean": type(value) is bool, "string": isinstance(value, str)}.get(meta["type"], False)
        if not typ_ok or ("enum" in meta and value not in meta["enum"]):
            return await res(False, "bad_value")
        if preferences.get(self.preferences, key) == value:
            return await res(True)

        async def ack(result: dict) -> None:
            await res(bool(result.get("ok")), None if result.get("ok") else
                      ("busy" if result.get("error") == "busy" else "rejected"))
        self.st.log("pref_set", device=s.device, status=key)
        await self.set_pref(key, value, ack=ack)

    async def preferences_loop(self):
        while not self.stopping.is_set():
            try:
                raw = preferences.path().read_bytes()
                import hashlib
                stamp = hashlib.sha256(raw).digest()
                if stamp != self.config_stamp:
                    self.config_stamp = stamp
                    import fcntl
                    fd=os.open(self.st.root / "preferences.lock",os.O_RDWR|os.O_CREAT,0o600)
                    try:
                        fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
                        await self.apply_preferences()
                    except BlockingIOError:
                        self.config_stamp=None
                    finally:
                        os.close(fd)
            except FileNotFoundError:
                pass
            except OSError:
                self.config_problem = {"ok": False, "key": "/", "error": "configuration unreadable"}
            await asyncio.sleep(0.5)

    def name_changed(self) -> None:
        """The Agent name changed (`agentj name`, the admin page, a Dashboard rename adopted in sync): every ready phone gets
        a fresh status at once."""
        msg = self._status_msg()
        self._post(self._send_ready, lambda s: msg)
        self._post(self._send_ready, lambda s: self.preferences_msg())

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

    async def _send_legacy(self, obj_fn) -> None:
        """§8 `msg` entries: only to sessions WITHOUT p33 (a p33 device gets the same as history pages, §10.5)."""
        for s in list(self.sessions.values()):
            if s.state == "ready" and not s.p33 and self.st.is_allowed(s.pub):
                await self.send_app(s, obj_fn(s))

    async def _send_p33(self, obj_fn) -> None:
        for s in list(self.sessions.values()):
            if s.state == "ready" and s.p33 and self.st.is_allowed(s.pub):
                await self.send_app(s, obj_fn(s))

    async def _send_device(self, device: str, obj: dict) -> None:
        """An answer for one device (say_state, asr_res …): every ready p33 session of it (it may have reconnected)."""
        for s in list(self.sessions.values()):
            if s.state == "ready" and s.p33 and s.device == device and self.st.is_allowed(s.pub):
                await self.send_app(s, obj)

    # ------------------------------------------------------------ history pages (PROTOCOL §10.5)
    def hist_emit(self, turns: list[dict]) -> None:
        ep = self.hist.epoch
        for t in turns:
            self._post(self._send_p33, lambda s, t=t: {"t": "hist_turn", "epoch": ep, "turn": t})

    def hist_meta_all(self) -> None:
        m = {"t": "hist_meta", **self.hist.meta()}
        self._post(self._send_p33, lambda s: m)

    def hist_add(self, src: dict, reply: str = "", end: str = "done", card: dict | None = None) -> dict:
        t = self.hist.add(src, wire.well_formed(reply), end, card)
        self.hist_emit([t])
        return t

    def hist_update(self, tid: int | None, **kw) -> list[dict]:
        if tid is None:
            return []
        if isinstance(kw.get("append"), str):
            kw["append"] = wire.well_formed(kw["append"])
        changed = self.hist.update(tid, **kw)
        self.hist_emit(changed)
        return changed

    def status_changed(self) -> None:
        st = self.eff_status()
        if st == "waiting" and self._open_question():
            st = "waiting:question"
        if st == self.sent_status:
            return
        self.sent_status = st
        st = st.split(":")[0]
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
            self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        # §10.5: the reply of a page = every finished reply text of that turn, joined by a blank line (results only)
        if self.cur_turn is not None and self.hist.get(self.cur_turn) is not None:
            changed = self.hist_update(self.cur_turn, append=text)
            if changed:
                self.cur_turn = changed[-1]["id"]
        else:                          # the Agent spoke outside a turn of ours (e.g. a background task finished)
            self.hist_add({"k": "agent", "text": ""}, text, "done")

    def desktop_input(self, text: str) -> None:
        self.desktop_end()
        self.desktop_turn = self.hist_add({"k": "host", "name": "电脑 / Desktop", "text": text}, "", "open")["id"]

    def desktop_text(self, text: str) -> None:
        tid = getattr(self, "desktop_turn", None)
        if tid is None:
            self.hist_add({"k": "agent", "text": ""}, text, "done")
        else:
            changed = self.hist_update(tid, append=text)
            if changed:
                self.desktop_turn = changed[-1]["id"]

    def desktop_end(self) -> None:
        tid = getattr(self, "desktop_turn", None)
        if tid is not None:
            self.hist_update(tid, end="done")
        self.desktop_turn = None

    def agent_notice(self, text: str) -> None:
        local, self.notice_local = getattr(self, "notice_local", False), False
        self.emit("agent_notice", text=text)
        e = self._remember("notice", text)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": "", **({"local": True} if local else {})}, text, "done")

    def local_notice(self, text: str) -> None:
        """§10.8: something only the human at the computer can answer: a `sys` page with `local: true` (relay's
        「在电脑上处理」 card) — a notice for older phones (the same agent_notice path, marked)."""
        self.notice_local = True
        try:
            self.agent_notice(text)
        finally:
            self.notice_local = False

    def turn_failed(self) -> None:
        self.cur_failed = True

    def official_notices(self, items: list) -> None:
        """Only cloud's pinned-signature inbox reaches here. Body stays data; trusted local policy owns behavior."""
        cats = notices.local_categories(self.st, self.preferences)
        for n in items:
            action = notices.policy(n, self.preferences, cats)
            if action == "silent":
                notices.processed(self.st, n['id'])
                continue
            if notices.presented(self.st, n['id']):
                text = "Agent J 官方 / Agent J official · " + n['title_' + self.lang] + "\n" + n['body_' + self.lang]
                entry = self._remember("notice", text)
                self._post(self._send_legacy, lambda s, e=entry: self._render(e, s))
                self.hist_add({"k": "sys", "text": "", "notice_id": n['id']}, text, "done")
                self.push_notify("security" if n['type'] == "security" else "ask" if n['priority'] == "urgent" else "reply")
            # The phone hears immediately; Agent delivery waits for a fresh snapshot after recovery.
            if not self.agent or self.stopped():
                continue
            if n['id'] in self.official_pending:
                continue
            send = notices.NoticeSend(self, n, action, self.lang)
            self.official_pending[n['id']] = send
            self.agent.submit(send)
            self.st.log("official_notice", id=n['id'], type=n['type'], action=action)
        if notices.receipts(self.st): self.reporter.trigger("notice_receipt")

    async def update_loop(self) -> None:
        """Once a day: is there a newer host on the public repo? Tell the phones once per version (E2E, like every message);
        never install anything (Z4: the human runs `agentj update apply`). F12 first: after an authorized upgrade the phones
        hear once that it happened (update.take_marker)."""
        await self.upgraded_notice()
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
                self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
                self.hist_add({"k": "sys", "text": ""}, note, "done")
            await asyncio.sleep(UPDATE_WAKE)

    async def upgraded_notice(self) -> None:
        """F12 / C3: serve restarted after `agentj update apply --authorization`: one line to the phones, in the owner's
        language, with the doctor counts — "已升级到 <v>（doctor: N ✓ / M ! / K ✗）". Once per version (the marker is removed
        as it is read)."""
        try:
            rec = update.take_marker(self.st)
        except Exception:  # noqa: BLE001
            rec = None
        if not rec:
            return
        try:
            from . import doctor
            counts = update.doctor_counts(await asyncio.to_thread(doctor.run, self.st))
        except Exception:  # noqa: BLE001 — the line still goes out, with "doctor: ?"
            counts = None
        note = update.upgraded_text(rec, counts, preferences.get(self.preferences, "appearance.language", "zh"))
        self.st.log("upgrade_notice", status=str(rec.get("to"))[:32])
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")

    def agent_turn_start(self, text: str, send=None) -> None:
        self.turn_t0 = time.monotonic()
        by = send.by if send is not None and send.by else self.turn_by
        self.activity("turn_start", by=by, text=text[:120])
        self.turn_by = None
        self.cur_turn, self.cur_failed = (send.turn if send is not None else None), False

    def agent_turn_end(self) -> None:
        self.st.log("turn_end", agent=self.agent.kind if self.agent else None)
        halted = bool(self.agent and self.agent.halting)
        self.activity("turn_end", secs=round(time.monotonic() - self.turn_t0, 1) if self.turn_t0 else None,
                      result="stopped" if halted else "done")
        if self.cur_turn is not None:
            self.hist_update(self.cur_turn, end="stopped" if halted else ("failed" if self.cur_failed else "done"))
        if self.telegram and self.cur_turn is not None:
            self.telegram.completed(self.hist.get(self.cur_turn))
        send = getattr(self.agent, 'cur_send', None)
        if getattr(send, 'official_notice_id', None):
            self.official_pending.pop(send.official_notice_id, None)
        self.cur_turn, self.cur_failed = None, False
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

    async def ask(self, tool: str, tool_input: dict, gone: asyncio.Future | None = None, batch: bool = True, risk_scope: bool = False) -> dict:
        """batch=False: never offered for (or approved by) a batch grant (a Codex sandbox / network escalation)."""
        if tool == "AskUserQuestion" and self.agent and self.agent.kind == "claude" and isinstance(tool_input, dict):
            return await self._ask_user_question(tool_input, gone)       # §10.7: a question card, not an approval card
        kind = self.agent.kind if self.agent else "?"
        classifier = danger.classify_shared if (self.agent_cfg or {}).get("session_mode") == "shared" else danger.classify
        v = classifier(tool, tool_input, self.danger_extra)
        summary = approval_summary(tool, tool_input, v)
        if risk_scope:
            summary += "\n本回合同类高危动作合并批准。 / Applies to this risk category for this turn."
        digest, isha = approvals.shown_digest(tool, summary), approvals.input_digest(tool_input)
        rid = secrets.token_hex(16)
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
            answer = {"behavior": "allow", "updatedInput": tool_input}
            if risk_scope:
                answer["risk_grant"] = {"rid": rid, "device": did, "sign_pub": sk}
            return answer
        msg = {"device": "用户在手机上拒绝了这个操作。", "timeout": f"手机上 {int(self.ask_ttl)} 秒内没有批准：默认拒绝。",
               "serve_stop": "agentj serve 已停止：默认拒绝。", "estop": "已急停（全部停下）：默认拒绝。"}.get(reason, "已拒绝。")
        return {"behavior": "deny", "message": msg}

    def policy_refused(self, tool: str, tool_input: dict, why: str, notice: str) -> None:
        """A request the harness asked that would give it more than the human's own configuration allows (Codex beyond its own
        sandbox, ADR-A73): refused without a card — approvals.log (reason `policy`), host.log, activity, one notice."""
        rid = secrets.token_hex(16)
        classifier = danger.classify_shared if (self.agent_cfg or {}).get("session_mode") == "shared" else danger.classify
        v = classifier(tool, tool_input, self.danger_extra)
        summary = approval_summary(tool, tool_input, v)
        approvals.record(self.st, rid=rid, agent=self.agent.kind if self.agent else "?", tool=tool,
                         input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(tool, summary),
                         cats=v.cats, decision="deny", reason="policy")
        self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="policy")
        self.activity("decision", id=rid, tool=tool, result="policy", reason="policy", why=why, cats=v.cats,
                      task=self.task_label, summary=summary[:200])
        self.emit("ask_done", id=rid, result="deny", reason="policy")
        self.local_notice(f"{notice}（{why}：{summary.splitlines()[0][:120]}）")

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
        for q in list(self.questions.values()):        # §10.7: an open question is cancelled at once (`stopped`)
            if not q.fut.done():
                q.fut.set_result(("stopped", None, None, None, None))
        for gid in list(self.grants):
            self.end_grant(gid, "estop")
        self.elevate.cancel_all("stopped")            # F17: open sudo / secret cards end (a running command finishes)
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
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")

    async def do_resume(self, by: str, name: str) -> None:
        rec = controls.set_estop(self.st, False, by)
        self.estop = {**rec, "by_name": name}
        self.st.log("estop", status="off", device=by.split(":", 1)[1] if by.startswith("phone:") else None)
        self.emit("estop", on=False, by=by)
        self.activity("resume", by=name)
        self.status_changed()
        m = self._estop_msg()
        self._post(self._send_ready, lambda s: m)
        note = f"已恢复（{name}）：Agent 接收新消息，定时任务按各自的启用状态继续。"
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")
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
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        # §10.5: a task run is its own page (src.k task): the title, the verdict sentence
        self.hist_add({"k": "task", "name": clean(str(title), 64), "text": ""}, clean(text, wire.MAX_TEXT_P33),
                      "stopped" if res.get("stopped") else "done")
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
    def cmd_card(self, name: str, res: "slash.Result", by: str | None = None, turn: int | None = None,
                 interim: bool = False) -> None:
        """A command result → one chat entry `from: "cmd"` (kept in the backlog like every message) on every older phone, and
        the command's page (§10.5 `src.k: cmd`, `card` = the same fields) for p33 phones. interim = 「这一轮结束后执行。」."""
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
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        if turn is not None and self.hist.get(turn) is not None:
            self.hist_update(turn, text=wire.well_formed(res.text), end="open" if interim else "done",
                             card=None if interim else x)
        elif not interim:
            self.hist_add({"k": "cmd", "text": "/" + name, **({"name": by[:64]} if by else {})}, res.text, "done", x)

    def cmd_turn(self, s: Session | None, name: str, arg: str = "") -> int:
        """The page of a command, opened when it is asked for (its result fills it in)."""
        src = {"k": "cmd", "text": ("/" + name + (" " + arg if arg else ""))[:200]}
        if s is not None:
            src.update(dev=s.device, name=(s.name or s.device)[:64])
        return self.hist_add(src, "", "open")["id"]

    def cmd_done(self, cmd, res: "slash.Result") -> None:
        """Called by the Agent's queue when a command ran (agent.Cmd). /clear that cleared (and 「撤销清空」) also reset the
        history (§10.5): the command's page is closed, then archived / restored, and the result is the first page after."""
        self.st.log("slash", cmd=cmd.name, result=res.kind)
        self.activity("slash", cmd=cmd.name, result=res.kind, by=cmd.by)
        if cmd.name == "clear" and res.undo:
            self.hist_update(cmd.turn, text=wire.well_formed(res.text), end="done")
            if self.hist.reset("clear"):
                self.hist_meta_all()
            return self.cmd_card(cmd.name, res, cmd.by)
        if cmd.name == "undo_clear" and res.kind == "ok":
            self.hist_update(cmd.turn, text=wire.well_formed(res.text), end="done")
            if self.hist.undo_reset():
                self.hist_meta_all()
            return self.cmd_card(cmd.name, res, cmd.by)
        self.cmd_card(cmd.name, res, cmd.by, cmd.turn)

    def queue_dropped(self, item) -> None:
        """The stop switch dropped something still queued (agent.halt): a say is withdrawn (its attachments staged again,
        its page ends `stopped`), a command's page ends `stopped`."""
        if getattr(item, "sid", None) is not None:
            item.state = "cancelled"
            self.uploads.release(item.blobs)
            if item.prep is not None and not item.prep.done():
                item.prep.cancel()
            self.hist_update(item.turn, end="stopped")
            self._post(self._send_device, item.device, {"t": "say_state", "sid": item.sid, "s": "cancelled", "why": "stopped"})
        elif isinstance(item, agents.Cmd):
            if item.name == "model_set":
                self.model_set_done(item, "stopped")
            else:
                self.hist_update(item.turn, end="stopped")

    def set_model(self, model: str) -> None:
        """/model <name>: kept in config.json (`agent.model`), so a restart uses it too."""
        self.st.set_agent_model(model)
        if self.agent_cfg is not None:
            self.agent_cfg["model"] = model

    async def on_slash(self, s: Session, name: str, arg: str, confirm: bool, typed: bool) -> int:
        """A command from a ready session of a paired device (the only place one can come from). /stop and /help at once;
        the rest after the turn in front of it (the Agent's queue). Logged: command name + result class, never text.
        Returns the id of the command's history page (§10.5)."""
        by = s.name or s.device
        known = name in slash.WHITELIST + slash.INTERNAL
        self.st.log("slash_in", cid=s.cid, device=s.device, cmd=name if known else "other")
        turn = self.cmd_turn(s, name if known else "refused", arg if known else "")

        def done(res):
            self.st.log("slash", cmd=name if known else "other", result=res.kind)
            self.activity("slash", cmd=name if known else "other", result=res.kind, by=by)
            self.cmd_card(name if known else "refused", res, by, turn)
            return turn
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
            return done(slash.Result("清空要在手机上确认：点输入框左边的 ≡「全部命令」→「清空对话（可撤销）」。", "info"))
        if self.agent.status in ("working", "compacting") or not self.agent.q.empty():
            self.cmd_card(name, slash.Result("这一轮结束后执行。", "info"), by, turn, interim=True)
        self.agent.submit(agents.Cmd(name, arg, by, turn))
        return turn

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

    # ============================================================ relay parity (PROTOCOL §10, PROMPT-33)
    def _caps(self, s: Session | None = None) -> dict:
        """What `approved` / `ready` announce (§10.0): our capability, local transcription, history — to a device that
        announced p33 itself (an older one gets exactly §3's message)."""
        if s is not None and not s.p33:
            return {}
        return {"caps": [wire.CAP_P33], "asr": self.asr_state(), "hist": "on" if self.hist.on else "off"}

    async def _bulk(self, s: Session) -> None:
        """§10.13: a ready, allowlisted p33 device may upload faster — tell the relay (it answers the device {"t":"rate"});
        never for a pairing in progress. A relay without BULK ignores the op."""
        if s.p33 and s.state == "ready" and self.st.is_allowed(s.pub) and self.sessions.get(s.cid) is s:
            with contextlib.suppress(Exception):
                await self._op(wire.OP_BULK, s.cid)

    async def on_tts(self, s, obj):
        if self.sessions.get(s.cid) is not s or s.state != "ready" or not self.st.is_allowed(s.pub): return
        from . import voice
        import base64
        r=obj.get("r")
        if not wire.is_id22(r): return
        if getattr(self, "tts_busy", False):
            return await self.send_app(s,{"t":"tts_end","r":r,"ok":False,"why":"busy"})
        turn=self.hist.get(obj.get("id"))
        if not turn or turn.get("end") not in ("done", "stopped", "failed"):
            return await self.send_app(s,{"t":"tts_end","r":r,"ok":False,"why":"missing"})
        self.tts_busy=True
        try:
            text=turn.get("reply",{}).get("text","")
            audio=await asyncio.to_thread(voice.synthesize,text,self.preferences)
            for i in range(0,len(audio),24*1024):
                if self.sessions.get(s.cid) is not s or not self.st.is_allowed(s.pub): return
                if not await self.send_app(s,{"t":"tts_chunk","r":r,"i":i//(24*1024),"data":base64.b64encode(audio[i:i+24*1024]).decode()}): return
            await self.send_app(s,{"t":"tts_end","r":r,"ok":True,"bytes":len(audio),"mime":"audio/wav"})
        except Exception:
            await self.send_app(s,{"t":"tts_end","r":r,"ok":False,"why":"engine"})
        finally:
            self.tts_busy=False

    async def on_p33(self, s: Session, t: str, obj: dict) -> None:
        if t == "notice_read":
            nid = obj.get("id")
            if isinstance(nid, str) and notices.ID.fullmatch(nid) and notices.mark_read(self.st, nid):
                self.reporter.trigger("notice_read")
                self.st.log("notice_read", id=nid, device=s.device)
            return
        if t == "pref_set":
            return await self.on_pref_set(s, obj)
        if t == "tts_get":
            asyncio.create_task(self.on_tts(s, obj))
            return
        if t == "say":
            return await self.on_say(s, obj)
        if t == "say_cancel":
            sid = obj.get("sid")
            if not wire.is_id22(sid):
                return
            r, send = await self.sends.cancel(s.device, sid)
            if r == "cancelled" and send is not None:
                self.uploads.release(send.blobs)        # the attachments are staged again under the same ids
                self.hist_update(send.turn, end="stopped", card={"withdrawn": True})
                self.st.log("say_cancel", cid=s.cid, device=s.device, result=r)
                self._post(self._send_device, s.device, {"t": "say_state", "sid": sid, "s": "cancelled"})
            await self.send_app(s, {"t": "say_cancel_res", "sid": sid, "r": r})
            return
        if t == "blob_open":
            for m in self.uploads.open(s.device, obj):
                await self.send_app(s, m)
            return
        if t == "blob_chunk":
            for m in self.uploads.chunk(s.device, obj):
                await self.send_app(s, m)
            return
        if t == "blob_end":
            out, take = self.uploads.end(s.device, obj.get("bid"))
            for m in out:
                await self.send_app(s, m)
            if take is not None:
                asyncio.create_task(self._asr_take(s.device, take))
            return
        if t == "blob_drop":
            for m in self.uploads.drop(s.device, obj.get("bid")):
                await self.send_app(s, m)
            return
        if t == "hist_get":
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            lim = obj.get("limit")
            lim = lim if type(lim) is int and 1 <= lim <= history.PAGE_MAX else history.PAGE_MAX
            b, a = obj.get("before"), obj.get("after")
            turns, more = self.hist.page(before=b if type(b) is int else None, after=a if type(a) is int else None, limit=lim)
            return await self.send_app(s, {"t": "hist_page", "r": r, **self.hist.meta(), "turns": turns, "more": more})
        if t == "menu_get":
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            skills = []
            if self.agent and self.agent.kind == "claude":
                skills = [x for x in (getattr(self.agent, "init", {}) or {}).get("skills") or [] if isinstance(x, str)]
            wd = self.agent_cfg["dir"] if self.agent_cfg else None
            res = await asyncio.to_thread(menu.served, wd, skills, self.lang)
            configured = preferences.get(self.preferences, "menu.items", [])
            if configured:
                res.update(source="config", items=[{k: v for k, v in x.items() if k != "id"} for x in menu.arrange(configured)])
            return await self.send_app(s, {"t": "menu", "r": r, **res})
        if t == "model_set":
            return await self.on_model_set(s, obj)
        if t == "q_answer":
            return self._q_answer(s, obj)

    # ------------------------------------------------------------ say (§10.2)
    async def _accept(self, s: Session, text: str, sid: str | None, blobs: list | None = None, quote: dict | None = None,
                      quote_text: str | None = None) -> compose.Send | None:
        """A message for the Agent from a ready session: its page (src.k phone), the §8 entry for older phones, and the
        queued Send (withdrawable until delivered). Returns the Send (None without an Agent)."""
        blobs = blobs or []
        self.emit("msg", device=s.device, name=s.name, text=clean(text, wire.MAX_TEXT_P33))
        self.st.log("msg_in", cid=s.cid, device=s.device)
        entry = self._remember("device", text, device=s.device, name=s.name)
        for o in list(self.sessions.values()):     # other older phones see what was said; the sender already shows it
            if o is not s and o.state == "ready" and not o.p33 and self.st.is_allowed(o.pub):
                await self.send_app(o, self._render(entry, o))
        src = {"k": "phone", "dev": s.device, "name": (s.name or s.device)[:64], "text": text}
        if getattr(s,"source_kind",None)=="telegram":
            src["k"]="telegram"
        if quote:
            src["quote"] = quote
        if blobs:
            src["att"] = [{"name": b.name[:128], "mime": b.mime, "bytes": b.size, "kind": b.kind} for b in blobs]
        turn = self.hist_add(src, "", "open" if self.agent else "done")
        if not self.agent:
            return None
        send = compose.Send(s.device, sid or secrets.token_urlsafe(16), turn["id"], blobs=blobs, by=s.name or s.device)
        files = [{"path": b.path, "mime": b.mime, "bytes": b.size, "origin": b.origin, "secs": b.secs,
                  "voice": getattr(b, "voice", "")} for b in blobs]
        voice = [f for f in files if f["origin"] == "recording" and f["mime"].startswith("audio/")]
        if voice:
            send.ready = asyncio.get_running_loop().create_future()
            send.prep = asyncio.create_task(self._prep_say(send, text, files, voice, quote_text))
        else:
            send.text = compose.render(text, files, self.lang, quote_text)
        self.sends.add(send)
        self.turn_by = s.name or s.device
        self.agent.submit(send)
        return send

    async def on_say(self, s: Session, obj: dict) -> None:
        sid = obj.get("sid")
        if not wire.is_id22(sid):
            return

        async def res(**kw):
            await self.send_app(s, {"t": "say_res", "sid": sid, **kw})
        text, att, reply_to, excerpt = obj.get("text"), obj.get("att"), obj.get("reply_to"), obj.get("excerpt")
        if not isinstance(text, str) or (att is not None and not isinstance(att, list)) \
                or (reply_to is not None and type(reply_to) is not int) or (excerpt is not None and reply_to is None) \
                or not isinstance(obj.get("ts", 0), int):
            return await res(ok=False, why="shape")
        why = wire.text_problem(text) or (wire.text_problem(excerpt, compose.EXCERPT_MAX) if excerpt is not None else None)
        if why:
            return await res(ok=False, why=why)
        if wire.units(text) + (wire.units(excerpt) if excerpt else 0) > wire.MAX_TEXT_P33:
            return await res(ok=False, why="too_long")
        if not text.strip() and not att:
            return await res(ok=False, why="shape")
        if self.sends.used(s.device, sid):
            return await res(ok=False, why="dup")
        if self.sends.full(s.device):       # 16 of this device's sends still wait: refuse, never evict one (P33-C03)
            return await res(ok=False, why="too_many")
        if self.stopped() and self.agent:
            self.st.log("msg_refused", cid=s.cid, device=s.device, reason="estop")
            self.activity("message_refused", by=s.name or s.device, text=text[:120])
            return await res(ok=False, why="stopped")
        if not self.agent:
            return await res(ok=False, why="no_agent")
        cmd = slash.parse(text) if not att and reply_to is None else None
        if cmd and not self.agent.passthrough(cmd[0]):
            self.sends.add(compose.Send(s.device, sid, 0, state="delivered"))
            turn = await self.on_slash(s, cmd[0], cmd[1], confirm=False, typed=True)
            self._post(self.send_app, s, {"t": "say_res", "sid": sid, "ok": True, "state": "delivered", "turn": turn})
            return
        quote = quote_text = None
        if reply_to is not None:
            qt = self.hist.get(reply_to)
            if qt is None:
                return await res(ok=False, why="reply_unknown")
            quote = compose.quote_info(qt, excerpt, self.lang)
            quote_text = compose.quote_block(qt, excerpt, self.lang)
        try:
            blobs = self.uploads.claim(s.device, att)
        except uploads.Refused as e:
            return await res(ok=False, why=e.why, **({"att": e.att} if e.att else {}))
        send = await self._accept(s, text, sid, blobs, quote, quote_text)
        # through the same queue as its page (hist_turn), so the phone has the page before the answer that names it
        self._post(self.send_app, s, {"t": "say_res", "sid": sid, "ok": True, "state": "queued", "turn": send.turn})
        if send.prep is not None:
            self._post(self._send_device, s.device, {"t": "say_state", "sid": sid, "s": "transcribing"})

    async def _prep_say(self, send: compose.Send, text: str, files: list, voice: list, quote_text: str | None) -> None:
        """Say-time transcription (§10.9): every recording of one message within SAY_ASR_BUDGET together; past it the message
        goes out anyway with 「转写失败（超过 60 秒）」. A withdraw cancels this task."""
        deadline = time.monotonic() + compose.SAY_ASR_BUDGET
        try:
            for f in voice:
                left = deadline - time.monotonic()
                if left <= 0:
                    f["asr"] = {"ok": False, "why": "timeout", "secs": f.get("secs")}
                    continue
                f["asr"] = await self._transcribe_file(f, left, send.device)
            send.text = compose.render(text, files, self.lang, quote_text)
        finally:
            if send.ready is not None and not send.ready.done():
                send.ready.set_result(True)

    async def _transcribe_file(self, f: dict, budget: float, device: str, local_only: bool = False) -> dict:
        state = self.asr.ready_state(state_dir=self.st.root, engine_override=preferences.get(self.preferences, "voice.asr.engine")) if local_only else self.asr_state()
        if state != "ready":
            return {"ok": False, "why": ASR_WHY.get(state, "broken"), "secs": f.get("secs")}
        # P33-C02: only the host-private copy in the state dir (uploads.Blob.voice) — never the inbox file, which the Agent
        # can rewrite or replace with a link between upload and say
        path, tmp = f.get("voice") or "", None
        if not path or not os.path.isfile(path) or os.path.islink(path):
            return {"ok": False, "why": "bad_audio", "secs": f.get("secs")}
        try:
            if f["mime"] not in uploads.WAV_MIMES or not uploads.wav_ok(path):
                demux = FF_DEMUX.get(f["mime"])
                ff = shutil.which("ffmpeg")
                if not ff or not demux:
                    return {"ok": False, "why": "format", "secs": f.get("secs")}
                d = self.uploads.root / device
                d.mkdir(mode=0o700, parents=True, exist_ok=True)
                tmp = str(d / f"conv-{secrets.token_hex(6)}.wav")
                done = await self._ffmpeg(ff, demux, path, tmp, max(1.0, budget / 2))
                if done != "ok":
                    return {"ok": False, "why": "timeout" if done == "timeout" else "format", "secs": f.get("secs")}
                if not uploads.wav_ok(tmp):
                    return {"ok": False, "why": "format", "secs": f.get("secs")}
                path = tmp
            r = await self._transcribe(path, min(ASR_TAKE, budget), local_only=local_only)
        finally:
            if tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        if r.get("ok") and (r.get("text") or "").strip():
            return {"ok": True, "text": wire.well_formed(r["text"]), "secs": f.get("secs")}
        return {"ok": False, "why": "no_speech" if r.get("ok") else ASR_WHY.get(r.get("reason"), "broken"), "secs": f.get("secs")}

    async def _ffmpeg(self, ff: str, demux: str, src: str, dst: str, timeout: float) -> str:
        """One container → 16 kHz mono PCM16 WAV conversion (P33-C04 / X09): the demuxer pinned from the declared MIME (no
        content probing: an HLS playlist is not opened as one), local files only (`-protocol_whitelist file`), at most
        FF_MAX_SECS of audio and uploads.ASR_MAX output bytes (also RLIMIT_FSIZE / RLIMIT_CPU in the child), one conversion
        per host at a time, and the child killed + reaped on timeout, withdraw (task cancelled) and every other exit.
        → "ok" | "timeout" | "fail"."""
        if self.ff_lock is None:
            self.ff_lock = asyncio.Lock()
        argv = [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-f", demux,
                "-t", str(FF_MAX_SECS), "-i", "file:" + src, "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
                "-bitexact", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-t", str(FF_MAX_SECS),
                "-fs", str(uploads.ASR_MAX), "-f", "wav", "-y", "file:" + dst]

        def limits():                                   # in the child, before exec
            import resource
            with contextlib.suppress(Exception):
                resource.setrlimit(resource.RLIMIT_FSIZE, (uploads.ASR_MAX + 65536, uploads.ASR_MAX + 65536))
            with contextlib.suppress(Exception):
                resource.setrlimit(resource.RLIMIT_CPU, (int(FF_CPU_SECS), int(FF_CPU_SECS)))
        async with self.ff_lock:
            p = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                                     preexec_fn=limits, start_new_session=True)
            try:
                await asyncio.wait_for(p.wait(), timeout)
            except asyncio.TimeoutError:
                return "timeout"
            finally:
                if p.returncode is None:                # timeout, a withdraw (CancelledError) or anything else
                    with contextlib.suppress(ProcessLookupError, OSError):
                        os.killpg(p.pid, signal.SIGKILL)
                    with contextlib.suppress(ProcessLookupError):
                        p.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.shield(p.wait())
            return "ok" if p.returncode == 0 else "fail"

    def say_delivered(self, send: compose.Send) -> None:
        """The harness has the message (agent.Agent.deliver): the attachments belong to the Agent now."""
        if getattr(send, 'official_notice_id', None):
            notices.processed(self.st, send.official_notice_id)
            self.official_pending.pop(send.official_notice_id, None)
            self.reporter.trigger("notice_delivered")
            return
        self.uploads.commit(send.blobs)
        self.st.log("say_delivered", device=send.device, id=send.turn)
        self._post(self._send_device, send.device, {"t": "say_state", "sid": send.sid, "s": "delivered"})

    def say_uncertain(self, send: compose.Send) -> None:
        """The write to the harness failed part-way (P33-X08): it may have the message, so the attachments stay where they
        are (never deleted by the staged TTL, never released to the phone) and a withdraw answers already_delivered."""
        if getattr(send, 'official_notice_id', None):
            # Native transport may have accepted it: do not replay auto-upgrade authority on an uncertain write.
            notices.processed(self.st, send.official_notice_id)
            self.official_pending.pop(send.official_notice_id, None)
            self.st.log("notice_delivery_uncertain", id=send.official_notice_id)
            return
        self.uploads.commit(send.blobs)
        self.st.log("say_uncertain", device=send.device, id=send.turn)

    # ------------------------------------------------------------ voice (§10.9)
    def asr_state(self) -> str:
        """`ready.asr` (§10.0): asr.ready_state — cheap (no disk walk), read for every ready and every take."""
        if preferences.get(self.preferences, "voice.asr.mode") == "cloud":
            from . import voice
            return "ready" if voice.has_key(preferences.get(self.preferences, "voice.asr.key_env")) else "broken"
        try:
            v = self.asr.ready_state(self.st.root, engine_override=preferences.get(self.preferences,"voice.asr.engine"))
        except Exception:  # noqa: BLE001
            return "broken"
        return v if v in ("ready", "not_installed", "off", "broken") else "broken"

    async def _transcribe(self, path: str, timeout: float, local_only: bool = False) -> dict:
        """One decode at a time per host (an asyncio.Lock is FIFO: takes are transcribed in the order they finished)."""
        if self.asr_lock is None:
            self.asr_lock = asyncio.Lock()
        async with self.asr_lock:
            try:
                from . import voice
                if not local_only and preferences.get(self.preferences, "voice.asr.mode") == "cloud":
                    r = await asyncio.wait_for(asyncio.to_thread(voice.cloud_asr, path, self.preferences, timeout), timeout + 5)
                else:
                    r = await asyncio.wait_for(asyncio.to_thread(self.asr.transcribe, path, timeout_s=timeout,
                                                                        state_dir=self.st.root, engine_override=preferences.get(self.preferences, "voice.asr.engine")), timeout + 5)
            except asyncio.TimeoutError:
                return {"ok": False, "reason": "timeout"}
            except Exception:  # noqa: BLE001
                return {"ok": False, "reason": "engine_failed"}
        return r if isinstance(r, dict) else {"ok": False, "reason": "engine_failed"}

    async def _asr_take(self, device: str, b) -> None:
        """A finished `asr` blob → asr_res to that device. The audio never reaches the inbox and is deleted after."""
        res = {"t": "asr_res", "bid": b.bid}
        try:
            state = self.asr_state()
            if state != "ready":
                res.update(ok=False, why=ASR_WHY.get(state, "broken"))
            elif self.asr_waiting.get(device, 0) >= ASR_QUEUE:
                res.update(ok=False, why="busy")
            elif not uploads.wav_ok(str(self.uploads.part_path(b))):
                res.update(ok=False, why="bad_audio")
            else:
                self.asr_waiting[device] = self.asr_waiting.get(device, 0) + 1
                try:
                    r = await self._transcribe(str(self.uploads.part_path(b)), ASR_TAKE)
                finally:
                    self.asr_waiting[device] -= 1
                text = wire.well_formed(r.get("text") or "") if r.get("ok") else ""
                if r.get("ok") and text.strip():
                    cut = history._cut_units(text, wire.MAX_TEXT_P33)
                    res.update(ok=True, text=text[:cut], engine=str(r.get("engine") or "")[:16],
                               ms=int(r.get("ms") or 0) if isinstance(r.get("ms"), (int, float)) else 0)
                else:
                    res.update(ok=False, why="no_speech" if r.get("ok") else ASR_WHY.get(r.get("reason"), "broken"))
        finally:
            self.uploads.asr_done(b)
        self.st.log("asr", device=device, result="ok" if res.get("ok") else res.get("why"))
        await self._send_device(device, res)

    # ------------------------------------------------------------ meters (§10.10)
    def _meter_msg(self) -> dict:
        m = dict(self.meter_state)
        return {"t": "meter", **m, "at": int(time.time())}

    def meter_update(self, **kw) -> None:
        changed = False
        for k, v in kw.items():
            if k not in METER_KEYS:
                continue
            if isinstance(v, str):
                v = clean_line(v, {"model": 100, "model_name": 64, "effort": 16}.get(k, 64)) or None
            if self.meter_state.get(k) != v:
                self.meter_state[k] = v
                changed = True
        if not changed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self.meter_task is None or self.meter_task.done():
            self.meter_task = loop.create_task(self._meter_later())

    async def _meter_later(self) -> None:
        await asyncio.sleep(max(0.0, self.meter_at + METER_GAP - time.monotonic()))
        self.meter_at = time.monotonic()
        m = self._meter_msg()
        self._post(self._send_p33, lambda s: m)

    # ------------------------------------------------------------ model and effort (§10.11)
    def _models_msg(self) -> dict:
        a = self.agent
        cur_m = a.cur_model() if a else None
        models = [{"id": m["id"], "name": m["name"], "efforts": m.get("efforts"), "cur": m["id"] == cur_m
                   or (m.get("resolved") and m.get("resolved") == cur_m)} for m in (a.models_cache if a else [])]
        dm = getattr(a, "human", {}).get("model") if a and a.kind == "codex" else None
        de = getattr(a, "human", {}).get("model_reasoning_effort") if a and a.kind == "codex" else None
        return {"t": "models", "models": models[:40], "effort": a.cur_effort() if a else None,
                "default": {"model": dm if isinstance(dm, str) else None, "effort": de if isinstance(de, str) else None}}

    def models_changed(self) -> None:
        m = self._models_msg()
        self._post(self._send_p33, lambda s: m)

    def set_effort(self, effort: str | None) -> None:
        self.st.set_agent_effort(effort)
        if self.agent_cfg is not None:
            self.agent_cfg["effort"] = effort

    async def on_model_set(self, s: Session, obj: dict) -> None:
        r = obj.get("r")
        if not wire.is_rid(r):
            return

        async def res(ok, why=None):
            await self.send_app(s, {"t": "model_res", "r": r, "ok": ok, **({"why": why} if why else {})})
        if self.stopped():
            return await res(False, "stopped")
        if not self.agent:
            return await res(False, "unsupported")
        d = {"r": r, "cid": s.cid}
        if obj.get("default") is True:
            d["default"] = True
        else:
            m, e = obj.get("model"), obj.get("effort")
            if (m is not None and (not isinstance(m, str) or len(m) > 100)) or (e is not None and (not isinstance(e, str)
                                                                                                    or len(e) > 16)):
                return await res(False, "unknown_model" if m is not None and not isinstance(m, str) else "unknown_effort")
            d.update(model=m, effort=e)
        if self.model_sets >= 2:
            return await res(False, "busy")
        self.model_sets += 1
        self.st.log("model_set", cid=s.cid, device=s.device)
        self.agent.submit(agents.Cmd("model_set", "", s.name or s.device, None, d))

    def model_set_done(self, cmd, why: str | None) -> None:
        self.model_sets = max(0, self.model_sets - 1)
        d = cmd.data or {}
        s = self.sessions.get(d.get("cid"))
        self.activity("slash", cmd="model", result="ok" if why is None else "error", by=cmd.by)
        if s is not None and s.state == "ready":
            m = {"t": "model_res", "r": d.get("r"), "ok": why is None, **({"why": why} if why else {})}
            self._post(self.send_app, s, m)
        self.models_changed()

    # ------------------------------------------------------------ questions (§10.7): an answer is an approval
    def _question_msg(self, q: Question) -> dict:
        m = {"t": "question", "id": q.qid, "qs": q.qs, "ttl": max(0, int(q.deadline - time.monotonic()))}
        if q.task:
            m["task"] = q.task[:80]
        return m

    async def question(self, qs: list, gone: asyncio.Future | None = None, task: str | None = None) -> tuple[str, list | None]:
        """Ask the phones; → ("answer", picks) | ("cancel", None) | ("timeout" | "gone" | "stopped" | "no_device", None).
        Every outcome is one approvals.log line (hashes and option numbers, never the text)."""
        kind = self.agent.kind if self.agent else "?"
        qid = secrets.token_hex(16)
        digest = approvals.question_digest(qs)
        base = dict(qid=qid, agent=kind, q_sha256=digest)
        if self.stopped():
            approvals.record_question(self.st, **base, decision="stopped", reason="estop")
            return "stopped", None
        if not self._approvers() or sum(1 for q in self.questions.values() if not q.fut.done()) >= MAX_QUESTIONS:
            approvals.record_question(self.st, **base, decision="no_device", reason="no_device")
            return "no_device", None
        q = Question(qid, qs, digest, time.monotonic() + Q_TTL, asyncio.get_running_loop().create_future(), task)
        self.questions[qid] = q
        self.st.log("question", id=qid, agent=kind)
        self.activity("question", id=qid, n=len(qs), task=task)
        self.emit("question", id=qid, n=len(qs))
        self._post(self._send_p33, lambda s: self._question_msg(q))
        self.status_changed()
        self.push_notify("ask")
        did = sk = sig = picks = None
        try:
            waiters = {q.fut} | ({gone} if gone else set())
            done, _ = await asyncio.wait(waiters, timeout=max(0.0, q.deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if q.fut in done:
                decision, picks, did, sk, sig = q.fut.result()
                reason = "device" if did else ("estop" if self.stopped() else "serve_stop")
            else:
                decision, reason = ("gone", "agent_gone") if gone in done else \
                    (("gone", "serve_stop") if self.stopping.is_set() else ("timeout", "timeout"))
                if not q.fut.done():
                    q.fut.set_result((decision, None, None, None, None))
        finally:
            self.questions.pop(qid, None)
        if decision not in ("answer", "cancel"):
            decision = {"estop": "stopped", "serve_stop": "gone", "timeout": "timeout", "agent_gone": "gone"}.get(reason,
                                                                                                                  decision)
        approvals.record_question(self.st, **base, decision=decision, reason=reason, device=did, sign_pub=sk, sig=sig,
                                  picks=approvals.picks_text(picks) if decision in ("answer", "cancel") else None)
        result = {"answer": "answered", "cancel": "cancelled"}.get(decision, decision)
        self.st.log("question_done", id=qid, result=result, device=did)
        self.activity("question_done", id=qid, result=result, by=self._dname(did) if did else None)
        self._post(self._send_p33, lambda s: {"t": "question_done", "id": qid, "result": result})
        self.status_changed()
        return decision, picks

    def _q_answer(self, s: Session, obj: dict) -> None:
        qid, sig = obj.get("id"), obj.get("sig")
        q = self.questions.get(qid) if isinstance(qid, str) else None
        if q is None or q.fut.done():
            return
        cancel = obj.get("cancel") is True
        picks = None if cancel else obj.get("pick")
        if not isinstance(sig, str) or time.monotonic() > q.deadline or (cancel and "pick" in obj) \
                or (not cancel and not approvals.check_picks(q.qs, picks)):
            self.st.log("answer_refused", id=qid, device=s.device, reason="shape_or_late")
            return
        sk = self.st.sign_key(s.device)
        try:
            sigb = wire.unb64u(sig)
        except ValueError:
            sigb = b""
        action = "cancel" if cancel else "answer"
        if not sk or not approvals.verify_question(sk, sigb, self.channel, s.device, qid, action, q.qs, picks):
            self.st.log("answer_refused", id=qid, device=s.device, reason="no_key" if not sk else "bad_signature")
            return
        q.fut.set_result((action, picks, s.device, sk, sigb))

    async def _ask_user_question(self, tool_input: dict, gone: asyncio.Future | None) -> dict:
        """Claude Code's AskUserQuestion reaches the permission tool like any tool (probed with 2.1.285, reports/qa/parity/):
        an answer = allow with the input AS THE HOST RECEIVED IT plus `answers` (built by the host from option numbers) —
        the one exception to "updatedInput = input" (Invariant 11); cancel / timeout = deny with a fixed message."""
        card = approvals.norm_questions(tool_input.get("questions"))
        if card is None:
            return {"behavior": "deny", "message": Q_TIMEOUT}
        decision, picks = await self.question(card, gone, task=self.task_label)
        if decision == "answer" and picks:
            ans = {}
            for q, p in zip(card, picks):
                labels = [q["o"][n - 1]["l"] for n in p]
                ans[q["q"]] = ", ".join(labels) if q["m"] else labels[0]
            return {"behavior": "allow", "updatedInput": {**tool_input, "answers": ans}}
        if decision == "cancel":
            return {"behavior": "deny", "message": Q_CANCEL}
        return {"behavior": "deny", "message": Q_TIMEOUT}

    # ------------------------------------------------------------ housekeeping
    async def sweep_loop(self) -> None:
        """Staged uploads nobody sent expire (their inbox file goes, §10.3); the inbox keeps 30 days / 2 GiB (§10.4)."""
        last_retain = 0.0
        while True:
            with contextlib.suppress(Exception):
                self.uploads.sweep()
            if time.monotonic() - last_retain > RETAIN_EVERY or not last_retain:
                last_retain = time.monotonic()
                if self.agent_cfg:
                    with contextlib.suppress(Exception):
                        gone = await asyncio.to_thread(inbox.retain, self.agent_cfg["dir"], self.uploads.staged_paths(),
                                                       log=self.st.log)
                        if gone:
                            self.st.log("inbox_retain", status=str(len(gone)))
            await asyncio.sleep(SWEEP_EVERY)

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
            fut = in_daemon_thread(webpush.send, self._push_key(), sub, kind, "high" if kind in ("ask", "security") else "normal")
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
        old = os.umask(0o077)
        try:
            server = await asyncio.start_unix_server(self.on_ctl, path=str(sock))
        finally:
            os.umask(old)
        os.chmod(sock, 0o600)
        self.st.log("serve_start", channel=self.channel)
        self.post_q = asyncio.Queue()
        jobs = [asyncio.create_task(self.relay_loop()), asyncio.create_task(self.reporter.run()),
                 asyncio.create_task(self.sync_loop()), asyncio.create_task(self.post_loop()),
                 asyncio.create_task(self.update_loop()), asyncio.create_task(self.preferences_loop())]
        perm_server = None
        if self.agent_cfg:
            psock = self.st.perm_sock_path
            self.st.perm_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.st.perm_dir, 0o700)
            with contextlib.suppress(FileNotFoundError):
                psock.unlink()
            old = os.umask(0o077)
            try:
                perm_server = await asyncio.start_unix_server(self.on_perm, path=str(psock), limit=8 * 1024 * 1024)
            finally:
                os.umask(old)
            os.chmod(psock, 0o600)
            self.agent = agents.make(self, self.agent_cfg)
            self.agent.start()
            self.sent_status = self.eff_status()
            self.st.log("agent_on", agent=self.agent.kind, fence=self.agent_cfg.get("fence", True))
        await self.elevate.start()                 # F17: <state>/agentperm/elevate.sock for `agentj sudo` / `agentj secret`
        from .telegram import Telegram
        self.telegram=Telegram(self)
        jobs.append(asyncio.create_task(self.telegram.run()))
        jobs.append(asyncio.create_task(self.scheduler.loop()))
        jobs.append(asyncio.create_task(self.sweep_loop()))
        if self.read_stdin:
            jobs.append(asyncio.create_task(self.stdin_loop()))
        try:
            await self.stopping.wait()
        finally:
            for a in list(self.asks.values()):      # serve stopping: every pending request is denied
                if not a.fut.done():
                    a.fut.set_result(("deny", None, None, None))
            for q in list(self.questions.values()):
                if not q.fut.done():
                    q.fut.set_result(("gone", None, None, None, None))
            await self.scheduler.stop_current()
            await self.elevate.stop()
            if self.agent:
                await self.agent.stop()
            with contextlib.suppress(Exception):         # the resident ASR worker (§10.9) goes with serve
                await asyncio.to_thread(getattr(self.asr, "shutdown", lambda: None))
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


_IID = re.compile(r"[A-Za-z0-9_-]{22}")


def _iid(info: dict) -> str:
    """The page's per-host install id (0.15.1, PROTOCOL §3): base64url of 16 bytes, or "" (older page / malformed)."""
    v = info.get("iid")
    return v if isinstance(v, str) and _IID.fullmatch(v) else ""


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
