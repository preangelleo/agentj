"""`agentj serve`: the host daemon. Dials the blind relay, runs Noise with each device, approves pairings only via the
local control socket (driven by `agentj pair` in a terminal), shows plaintext only on this machine's terminal.

Spec: protocol/PROTOCOL.md. Logs (host.log) carry metadata only, never message text.

Relay parity (PROTOCOL §10, PROMPT-33): a device that announces capability `p33` in its hello gets the bigger limits (60 KiB
messages, 20 000-unit text, `frag` for anything larger), the persistent history instead of §8's `msg` stream (history.py:
every message, reply, notice, command result and task run is a page), `say` with attachments / quote / withdraw (compose.py),
end-to-end blobs (uploads.py → inbox.py), voice transcribed on the Agent J host (asr.py), questions as signed cards, meters, the
model / effort pill and the menu (menu.py); after it is ready the relay is told to raise its frame budget (op 0x03 BULK). An
older device keeps exactly §3 / §8.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hmac
import hashlib
import json
import logging
import math
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

from .text import one, pick
from . import agent as agents
from .shared import SharedClaudeAgent
from . import notices, activity, approvals, cloud, controls, danger, fence, gate, memory, slash, tasks, update, webpush, wire
from . import asr as asr_mod
from . import cmdrun, compose, history, hsearch, humane, inbox, menu, uploads, preferences, proxy, provenance
from . import receipts as receipts_mod  # P313: say receipts on disk — a restart never runs a phone message twice
from . import meeting  # P182: /会议总结 → minutes, recipients only from the owner's words
from . import diskq  # P142: fsync'ed state writes off the loop (one ordered writer)
from . import approval_jev  # P157: layer 2 of the approval card (Jev may only make fewer cards)
from . import goal as goal_mod  # P158: /goal — one signed grant, reversible steps pass after a 10 s card
from . import approval_level  # P336: safe / standard / full, goal without the opening card, cards while no remote is online
from . import bg  # P192 (B18): swallowed errors on connection / approval paths leave a (rate-limited) line
from . import loopwatch  # P136: what was running when the loop stalled
from . import jcards  # P360 (§34): Agent J Cards — the logo menu from card 6 on (template cards, read-only data)
from . import relaydiag  # P317: why a relay leg is not up (journal / status / doctor)
from . import reset as reset_mod  # P332 (§33): 紧急修复 — the reset ladder, support help, repair actions
from . import media as media_mod  # F21: files the Agent shows to the phone (PROTOCOL §13)
from . import elevate  # F17: sudo + secret cards (PROTOCOL §11)
from . import pair_card  # P153: add a remote from a remote (PROTOCOL §3.3)
from . import passkey  # F20: the same phone without a second pairing (PROTOCOL §12)
from . import renewal  # P122: the same phone after its browser deleted the page's storage (§12.1)
from . import onboarding  # F28: seat → two required remotes → one welcome (P72)
TG_PAIR_CMDS = ("telegram-pair", "telegram_pair", "rebind-telegram")   # P283: the phone's 「重新绑定 Telegram」
# P320: three tiers (schema `apply`): hot (at once) · reload (`agentj reload`, the phone's /reload or ≡ Reload: serve re-reads
# the configuration and its key file, the owned harness restarts before its next turn, the conversation kept) · restart:serve.
RELOAD_CMDS = ("reload",)
RELOAD_KEYS = ("agent.instructions", "agent.isolation", "agent.allow_docker", "agent.high_risk_warnings", "agent.approvals",
               "channels.items")
RESTART_SERVE_KEYS = ("agent.working_root", "agent.session_mode", "agent.shared_session_id", "agent.shared_opencode_port")
from . import friend_cmds  # P73: /my-agent-id · /add-friend, answered by the host (ADR-A176)
from . import friends as friends_mod
from .remotes import TYPES as REMOTES_TYPES  # P171 (§24): the phone's 遥控器管理 page
from .noise import IK, IKPSK2, CipherState, Handshake, NoiseError
from .reporter import Reporter, in_daemon_thread
from .envcompat import getenv
from .state import MAX_DEVICES, DeviceLimit, State, device_label, device_names
from .text import LABEL_DIGITS, LABEL_MAX, clean, clean_label, clean_line, drop_replacement, text_units  # noqa: F401  (re-exported)


def _ttl(env: str, default: int) -> float:
    """Tests may only shorten a timeout, never lengthen it."""
    try:
        return min(default, float(getenv(env, default)))
    except ValueError:
        return default


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


_close_code = relaydiag.close_code   # P317: shared with the mailbox leg and doctor


RC_WHY = ("load", "close", "hb", "host", "error", "ticket", "visibility", "offline", "stall")   # P129/P133: see session.js


def _reconnect_reason(rc) -> tuple:
    """P129: the hello's optional {"why", "code", "down"} — what ended the page's previous session (diagnostics only, never
    acted on). Anything off-shape is dropped; numbers are bounded."""
    if not isinstance(rc, dict) or rc.get("why") not in RC_WHY:
        return ()
    code = rc.get("code") if type(rc.get("code")) is int and 1000 <= rc["code"] <= 4999 else None
    down = rc.get("down") if type(rc.get("down")) is int and 0 <= rc["down"] <= 7 * 86400 * 1000 else None
    return (rc["why"], code, down)


UPDATE_FIRST = 300      # s after start before the first daily update check (update.daily keeps 24 h between checks)
UPDATE_WAKE = 3600      # s between looks at the 24 h clock
PAIR_TTL = _ttl("AGENTJ_TEST_PAIR_TTL", 300)        # QR / pairing code lifetime (s)
HS_TTL = _ttl("AGENTJ_TEST_HS_TTL", 30)             # a connection must finish its handshake + hello within this (s)
APPROVE_TTL = _ttl("AGENTJ_TEST_APPROVE_TTL", 120)  # time the human has to type the safety code (s)
CODE_TRIES = 3                                      # P176: safety-code attempts from `agentj pair` per waiting device
AUTH_TIMEOUT = 8       # the relay gives an unauthenticated host socket 10 s from accept (relay AUTH_DEADLINE_MS): waiting longer is moot
RELAY_OPEN_TIMEOUT = 10   # TCP + TLS + upgrade (the relay takes a per-IP lease before it answers 101)
FAST_RETRIES = 3       # P129: a relay link that did not come up is retried after FAST_RETRY s this many times before backing off
FAST_RETRY = 1
RELAY_GRACE = 60       # s after serve start in which a relay link that never came up reads "connecting", not "reconnecting"
RELAY_SAY = 600        # P317: an unchanged relay failure is repeated in the journal at most this often (s)
CLOSE_SEAT = 4002      # P185: the relay refused / closed this host for lack of a valid seat certificate (PROTOCOL §2 seat certificate)
SEAT_RETRY = 20        # s before the relay is tried again after a seat refusal (a renewed certificate retries at once)
SEAT_TICK = 30         # s between seat_loop looks (local file reads; the Dashboard is asked only when seatcert says due)
SEAT_NOTE = 600        # s between two "no seat" notices to the phones
STOP_FLUSH = 5.0       # P292: serve's stop waits at most this long for queued fsyncs / log lines (launchd kills at 20 s)
# P331: with any worker thread busy on CPU (json5, regex, rglob in to_thread), each syscall the loop makes waits one GIL
# switch interval to run again (CPython default 5 ms): 400 read receipts took 5.2 s next to two busy threads, 0.8 s at 0.5 ms.
SWITCH_INTERVAL = 0.0005
LOOP_STALL = 3.0       # P129: the event loop answering this late (s) is logged as `loop_stall` (it delays every phone's pong)
EMPTY_TURN = 5.0       # P326: a turn ending sooner than this (s) with nothing shown is an error the owner is told about
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
FRIEND_READS = ("fr_list", "fr_hist", "fr_usage", "fr_ctx_get")   # P71 §17.7 (+ P73 fr_ctx_get); writes = controls.FRIEND_ACTIONS
WF_READS = ("wf_list", "wf_doc", "skill_get")   # P244 (§30.2): answered to the asking session only, read-only
PHONE_CONTROLS = ("mem_list", "mem_rm", "mem_undo", "act_list", "task_list", "task_set", "estop", "resume",
                  "systask_list")   # P207 §2: the Agent J host's own scheduled tasks, read-only (systasks.py)
# §10 (PROMPT-33)
Q_TTL = _ttl("AGENTJ_TEST_Q_TTL", 180)     # a question nobody answers is ended (relay's default)
MAX_QUESTIONS = 4                           # open at once; more are answered "nobody answered" at once
ASR_TAKE = compose.SAY_ASR_BUDGET                             # s per take (asr_res timeout)
ASR_QUEUE = 3                               # takes waiting per device (more → busy)
FF_MAX_SECS = 630                           # say-time ffmpeg: at most this much audio (≤ uploads.ASR_MAX as WAV)
FF_SEG_SECS = 600                           # P166: a longer file is transcribed in 10-minute segments …
FF_MAX_SEGS = 12                            # … at most two hours; the rest is said to be cut, the original stays attached
FF_CPU_SECS = 180                            # RLIMIT_CPU of one conversion
# declared MIME → the ffmpeg demuxer (pinned: no content probing, so a playlist or another format is never opened)
FF_DEMUX = {"video/mp4": "mov", "audio/webm": "matroska", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mov", "audio/aac": "aac",
            "audio/wav": "wav", "audio/x-wav": "wav",
            # P166: what phones and Telegram call the same containers, plus the other common ones
            "audio/x-m4a": "mov", "audio/m4a": "mov", "audio/x-mp4": "mov", "audio/mp3": "mp3", "audio/x-aac": "aac",
            "audio/flac": "flac", "audio/x-flac": "flac", "audio/opus": "ogg", "audio/amr": "amr", "audio/3gpp": "mov",
            "audio/x-caf": "caf", "audio/aiff": "aiff", "audio/x-aiff": "aiff", "audio/wave": "wav", "audio/vnd.wave": "wav",
            "video/quicktime": "mov", "video/x-m4v": "mov", "video/3gpp": "mov", "video/webm": "matroska",
            "video/x-matroska": "matroska", "video/mpeg": "mpeg", "video/x-msvideo": "avi"}
MODEL_SET_COALESCE = 2.0   # P239: pill switches closer than this collapse to the last one (host-side backstop)
MODEL_REFRESH_TIMEOUT = 8.0                # Background catalogue work never stalls the relay reader.
NEWS_TIMEOUT = 8.0                          # P374: one /v1/host/news call, off the loop
NEWS_INFLIGHT = 4                           # P374: news calls running at once (a burst of taps never piles up threads)
METER_GAP = 2.0                             # ≤ 1 meter message per 2 s
HIST_READY = 50                             # turns a p33 device gets on ready (it pages for more)
SWEEP_EVERY = 60
RETAIN_EVERY = 86_400
P33_ONLY = ("say", "say_cancel", "say_sync", "cmd_cancel", "blob_open", "blob_chunk", "blob_end", "blob_drop", "hist_get", "menu_get", "model_set",
            "q_answer", "tts_get", "pref_set", "notice_read", "media_get", "provider_get", "provider_key", "asr_install", "models_get", "ping",
            "tg_fwd", "turn_wait", "res_watch", "busy_get",   # P181 (§22.1) load card resources; P160 (§22) one 「在办」 row's detail
            "mail_get", "mail_set", "mail_key", "mail_test", "mail_check",   # P170 (§10.20)
            "dev_rename",    # P179 (§10.16a): a remote renames itself
            "hist_find", "hist_read",     # P188 · P247 (owner-level read sync) (§28): chat search, a read like hist_get
            "auth_get", "auth_start", "auth_cancel", "auth_press",   # P258 (§31): AI 登录救援
            "imsg_check", "imsg_fda",   # P274 (§10.21): the iMessage card's 「重新检测」 / 「在Agent J 主机上打开授权页」
            "qr_cancel",                # P314 (§32): cancel the automatic continue after a used-up usage limit
            "keys_get", "keys_op", "keys_set",   # P352: 「模型和 API Key」 key manager (keys.py)
            "notify_get", "notify_set", "hook_list", "hook_set", "news_get", "news_set",
            "news_email", "news_verify")          # P365: the settings list of automations allowed to post (notify.py)
# F14: paired phones may change optional warnings, session mode, isolation, docker and the upgrade mode (F19); native permissions stay authoritative.
PREF_SET_KEYS = ("updates.mode", "appearance.language", "appearance.theme", "voice.wake_enabled", "voice.speak_replies",
                 "agent.high_risk_warnings", "agent.session_mode", "agent.isolation", "agent.allow_docker")
PREF_SET_KEYS += ("heartbeat.enabled", "heartbeat.interval_minutes")    # P156: the settings panel's heartbeat switch
PREF_SET_KEYS += ("approval.jev_threshold",)     # P157: the phone's Jev threshold stepper (number, schema range and step)
PREF_SET_KEYS += ("approval.goal_auto",)         # P158: the phone's goal-mode countdown switch (enterprise: off)
PREF_SET_KEYS += ("updates.auto_install",)       # P203: 「空闲时自动更新」 = auto_install + mode together (one switch)
CARDS_HAVE_MAX = 256       # P203 (§29.1): ids a cards_get may list as already shown
CARDS_OPEN_MAX = 256       # … and the open ids one `cards` answer carries
UPG_TTL = 600              # P203 (§29.2): the upgrade confirmation card waits ten minutes
PREF_SET_KEYS += ("heartbeat.daily_at",)         # P207: the phone's "wake every day at HH:MM" ("" = off; schema pattern)
PREF_SET_KEYS += ("ui.card_hot_action",)         # P285: the reply card's bottom-left hot key (command / skill name; schema pattern)
PREF_SET_KEYS += ("ui.composer_buttons",)        # P340: the order of the four buttons above the message box (a permutation)
# P274: the iMessage card's parameters (macOS hosts only; on_pref_set refuses them elsewhere). instructions / vip / mute
# stay off this list: the phone shows them, the Agent changes them with `agentj imessage …` at the owner's word.
PREF_SET_KEYS += ("imessage.enabled", "imessage.interval_minutes", "imessage.delivery", "imessage.digest_at",
                  "imessage.quiet_hours", "imessage.strictness", "imessage.codes", "imessage.groups", "imessage.llm")
PREF_SET_KEYS += ("notify.enabled",)             # P365: 「允许自动化往这里发消息」 master switch (notify.py)
PREF_SET_KEYS += ("quota.auto_switch",)          # P302: the phone's 「额度告警时交给其他 AI 账号」 switch (P254 drew it, never whitelisted)
METER_KEYS = ("model", "model_name", "effort", "ctx", "h5", "week", "quota_windows", "shared_status", "shared_writer", "source_at")
Q_CANCEL = "用户在手机上取消了这个问题，没有选择任何选项。请不要替他做选择，停下来等他直接输入文字。"
Q_TIMEOUT = "没有人作答，请改用文字列出选项"
ASR_WHY = {"not_installed": "not_installed", "bad_audio": "bad_audio", "timeout": "timeout", "busy": "busy",
           "engine_failed": "broken", "off": "off", "broken": "broken", "no_speech": "no_speech"}


def page_asr(files: list) -> dict:
    """P363 (#72): what the owner's page shows under each voice attachment — the transcript (≤ wire.MAX_TEXT_P33 units, the
    rest marked `cut`; the full text stays in the inbox file the Agent got) or why there is none. Index = the attachment's
    place in src.att (the same blob order as `files`); a file without an `asr` (a picture, a silent video) is left out."""
    out = {}
    for i, f in enumerate(files):
        a = f.get("asr")
        if not isinstance(a, dict):
            continue
        body = (a.get("text") or "").strip() if a.get("ok") else ""
        if body:
            cut = len(body.encode("utf-16-le")) // 2 > wire.MAX_TEXT_P33
            r = {"ok": True, "text": body.encode("utf-16-le")[:wire.MAX_TEXT_P33 * 2].decode("utf-16-le", "ignore") if cut else body}
            if cut or a.get("file"):
                r["cut"] = True
            if a.get("partial"):
                r["partial"] = str(a["partial"])[:32]
        else:
            r = {"ok": False, "why": str(a.get("why") or "broken")[:32] if not a.get("ok") else "no_speech"}
        if a.get("secs"):
            r["secs"] = round(float(a["secs"]), 1)
        out[i] = r
    return out


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
    lt1: bool = False             # 0.18 (ADR-A193): the page renders long-task cards (lt_card / lt_caps)
    hist: dict | None = None      # its hello's {"epoch", "last"}: where its history pages stop (§10.5)
    iid: str = ""                 # 0.15.1: the browser install's per-host id from the pairing msg1 (same browser → replace)
    ready_at: float = 0.0         # P129: monotonic time the session became ready (dev_gone logs how long it lived)
    rc: tuple = ()                # P129: the page's own account of its previous drop (why, close code, ms offline), logged
    gone: str = ""                # 0.15.1: a removed device that resumed: "replaced" | "revoked" — told so after its hello
    pk_offer: tuple | None = None  # F20 (§12): (nonce, monotonic issue time) of the one open pk_offer of this session
    since: object = None          # F20: a passkey restore's hello `since`, used once the restore is accepted
    rt_at: float = 0.0            # P122: monotonic time of this session's last renewal ticket (≤ 1 per minute on rt_req)
    pair1: bool = False           # P153: the page announced "pair1" (adds remotes, renders pair_device cards)
    card: str = ""                # P153: while pending, the id of its pair_device card on the paired remotes


@dataclass
class Question:
    """A question the Agent asked (AskUserQuestion / requestUserInput / question.asked), waiting for a signed pick (§10.7)."""
    qid: str
    qs: list
    digest: str                   # approvals.question_digest(qs): what approvals.log keeps
    deadline: float               # monotonic
    fut: asyncio.Future
    task: str | None = None


def _cfg_patch(old: dict, new: dict) -> tuple[dict, dict]:
    """P318: an Agent's config request as {schema key: (present, value)} and the values those keys had when it asked."""
    of, nf = dict(preferences.flatten(old)), dict(preferences.flatten(new))
    keys = [k for k in set(of) | set(nf) if (k in of, of.get(k)) != (k in nf, nf.get(k))]
    return {k: (k in nf, nf.get(k)) for k in keys}, {k: (k in of, of.get(k)) for k in keys}


def _cfg_apply(doc: dict, patch: dict) -> dict:
    out = copy.deepcopy(doc)
    for k, (present, v) in patch.items():
        if present:
            preferences.put(out, k, copy.deepcopy(v))
        else:
            *up, leaf = k.split(".")
            parent = preferences.get(out, ".".join(up)) if up else out
            if isinstance(parent, dict):
                parent.pop(leaf, None)
    return out


def _superseded(f) -> bool:
    return f is not None and f.done() and not f.cancelled() and f.exception() is None and f.result() == "superseded"


def _cfg_security(key: str) -> bool:
    from .ctl_auth import SECURITY_PREFIXES
    return any(key.startswith(p) or p.startswith(key + ".") for p in SECURITY_PREFIXES)


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
    why_en: str = ""
    cred: str = ""                 # P128: "read" | "change" when cats has credentials
    scope: tuple | None = None     # (kind, key) for a low-risk request that may be batch-approved
    scope_text: str | None = None  # what the phone shows for that scope; an allow_batch signature covers it
    task: str | None = None        # the scheduled task this request comes from (display only)
    require_passkey: bool = False
    pk_if_any: bool = False        # P153: a passkey assertion is required from an approving remote that has a passkey
    pk_kind: str = "sandbox"       # passkey.elevate_challenge kind
    allow_offer: list = field(default_factory=list)   # P296: recipients the mail card offers to put on the allowlist
    allow_add: list = field(default_factory=list)     # P296: the ones the owner ticked (signed with the allow)
    goal: str = ""                 # P158: "auto" (10 s countdown, no answer = allowed) | "hold" | "attack" | "" (no goal)
    goal_why: str = ""             # P158: goal.WHY key shown on the card
    goal_who: str = ""             # P339: who asks in plain words (Claude Code / Codex / a scheduled task's label)
    goal_what: str = ""            # P339: what the step does in plain words (goal.human)
    goal_add: dict | None = None   # P339: {"text", "grants"} — 「批准，并加进目标授权」 (signed like a P296 allow_add pick)
    parked: bool = False           # P336 (#36): nobody was online — the Agent was told, the card waits on (approval_level)
    park_key: tuple = ()           # P336: (tool, input sha, task, harness, folder) — who may use its allow


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
    ctl: asyncio.StreamWriter | None
    cid: int | None = None
    timer: asyncio.Task | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    remote: dict | None = None
    phone: str | None = None      # P153 pair_new / P171 (§24) 遥控器管理: the paired remote that opened this pairing (it shows the QR and hears the outcome)
    code_fails: int = 0           # P176: wrong safety codes typed at `agentj pair` for the waiting device (< CODE_TRIES)
    rv: str | None = None         # P190 (§27): the reverse code's rendezvous channel (the asking remote compares codes)


# P128: approval card text in ONE language (appearance.language, the same value the phone UI uses); what the phone signs
# is exactly this text, so the host picks the language, not the phone.
CARD_TEXT = {
    "zh": {"change": "{tool} · 改密码或密钥。\n具体内容只在Agent J 主机上看，凭据不会发到手机。",
           "read": "{tool} · 查看密码或密钥（内容会显示在Agent J 主机屏幕上）。\n具体内容只在Agent J 主机上看，凭据不会发到手机。",
           "risk_scope": "\n本回合内同类高危动作一并批准。"},
    "en": {"change": "{tool} · Changes passwords or keys.\nCheck the exact details on the Agent J host; credentials never go to the phone.",
           "read": "{tool} · Shows passwords or keys (on the Agent J host's screen).\nCheck the exact details on the Agent J host; credentials never go to the phone.",
           "risk_scope": "\nAlso approves this kind of high-risk action for the rest of this turn."}}


def _hb_touch(host) -> None:
    """P156: any turn, phone message or desktop input moves the next heartbeat back (tolerates partial test hosts)."""
    hb = getattr(host, "heartbeat", None)
    if hb is not None:
        hb.touch()


def card_lang(lang) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def approval_summary(tool, tool_input, verdict, lang="zh"):
    if 'credentials' in verdict.cats:
        kind = "read" if getattr(verdict, "cred", "") == "read" else "change"
        return CARD_TEXT[card_lang(lang)][kind].format(tool=tool)
    return agents.summarize(tool, tool_input)



# ---------------------------------------------------------------- P313: say receipts on disk (receipts.py, PROTOCOL §14)
# Module functions, not Host methods: many test doubles borrow single Host methods and are built without __init__.
async def _rcpt_recv(host, device: str, sid: str) -> None:
    """The moment a phone `say` is accepted: its (device, sid) is on disk before anything acts on it. A disk that refuses
    only costs the restart guarantee (memory dedupe stays), never the message."""
    rc = getattr(host, "receipts", None)
    if rc is None:
        return
    try:
        await rc.persist(device, sid, "recv")
    except Exception:  # noqa: BLE001
        host.st.log("say_receipts", result="write_failed")


def _rcpt(host, device, sid, state: str, turn: int | None = None) -> None:
    """A later state of a receipt the phone created (Telegram / e-mail / host sends have none: nothing to do)."""
    rc = getattr(host, "receipts", None)
    if rc is not None and isinstance(device, str) and isinstance(sid, str) and rc.used(device, sid):
        rc.mark(device, sid, state, turn)


def say_sync_state(send, receipt: str | None) -> str | None:
    """P398 (#155): one sid's say_state for `say_sync` — None while it still waits (the phone keeps 「取消」)."""
    if send is not None:
        return {"delivered": "delivered", "uncertain": "delivered", "cancelled": "cancelled",
                "failed": "failed"}.get(send.state)
    if receipt in ("recv",):
        return None
    return {"deliv": "delivered", "lost": "failed"}.get(receipt, "done")   # done / unknown: nothing left to withdraw


def _rcpt_turn(host, tid) -> None:
    rc = getattr(host, "receipts", None)
    if rc is not None:
        rc.done_turn(tid)


class Host:
    def __init__(self, st: State, events: str = "text", read_stdin: bool = True):
        self.st = st
        from . import userdata                     # P207: missing user files come back from the newest snapshot first,
        self.userdata_restored = []                # then this version keeps its own snapshot (once per version)
        with contextlib.suppress(Exception):
            self.userdata_restored = userdata.heal(st)
        with contextlib.suppress(Exception):
            userdata.version_snapshot(st, update.__version__)
        with contextlib.suppress(OSError, ValueError):   # P179: pre-P179 remotes → self_name / owner_name (audit-based)
            st.migrate_device_names()
        self.cfg = st.config()
        self.channel = self.cfg["channel"]
        from . import seatcert                     # P185: the relay seat certificate (renewed by seat_loop)
        self.seat = seatcert.SeatCert(st, self.cfg.get("relay") or "")
        self.seat_noted = 0.0                      # when the last "no seat" notice reached the phones (≤ 1 per SEAT_NOTE)
        self.relay_seat_refused = False            # the relay's last answer was 4002 "seat"
        self.seat_sent = None                      # the certificate the last relay auth carried
        self.kp = st.host_keypair()
        self.sk = st.signing_key()
        self.events = events
        self.read_stdin = read_stdin
        self.telegram = None
        self.mail = None                  # P170: the e-mail channel (mail.py), started with serve
        self._news_inflight = 0           # P374: /v1/host/news calls running (on_news)
        self.ws = None
        self.sessions: dict[int, Session] = {}
        from .remote_pair import RemotePair
        self.remote_pair = RemotePair(self)
        from .remotes import Remotes
        self.remotes = Remotes(self)                   # P171 (§24): the phone's 遥控器管理 page
        from .bots.owner import Manager as BotManager
        self.bots = BotManager(self)
        self.pairing: Pairing | None = None
        self.pk_guard = passkey.Guard()   # F20: pk_restore rate limit + nonce replay memory (in memory only)
        self.send_lock = asyncio.Lock()
        self.stopping = asyncio.Event()
        self.relay_up = False
        self.relay_ever_up = False         # P129: since this serve started (doctor / admin: connecting vs reconnecting)
        self.relay_last = None             # P317: the last failed attempt {phase, why, code, http, kind, at, attempts, retry_in}
        self.relay_said = (None, 0.0)      # P317: what the journal line last said, and when (one line per change / RELAY_SAY s)
        self.identity_ok = wire.channel_id(st._ed_pub(self.sk)) == self.channel   # P317: else every auth is refused (4003)
        self.started_at = time.monotonic()
        self.name_refused = False   # one agent_name_refused line per refused streak, not one per sync
        self.official_pending = {}
        self.reporter = Reporter(st, self.report_view, on_account=self.account_language, on_notices=self.official_notices)  # Dashboard metadata reports (§7); no-op unless linked
        # agent bridge (L1, PROTOCOL §8) and Web Push (§9)
        try:
            self.preferences = preferences.validate(preferences.read()[1])
            from . import voice
            voice.validate_runtime(self.preferences, environment="service")
            self.config_problem = None
        except (preferences.ConfigError,OSError) as e:
            try:
                self.preferences = preferences.validate(json.loads(preferences.runtime(st).read_text()))
            except (OSError, ValueError):
                self.preferences = preferences.defaults()
            self.config_problem = e.result() if isinstance(e,preferences.ConfigError) else {"ok":False,"key":"/","error":"configuration unreadable"}
        self.config_stamp = None
        self.config_apply_lock = asyncio.Lock()
        self.boot_preferences = self.preferences   # P320: what serve started with — restart:serve keys compare against it
        self.reload_pending: set = set()           # P320: reload-tier keys changed since the last reload
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
        self.turn_shown = False                     # P326: anything (text, a silent marker, a notice) shown in this turn
        self.turn_at = (0.0, 0.0, False)            # P326: last turn's start / end (epoch) + failed-or-empty, for status
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
        from .ask import CliAsks
        self.cli_asks = CliAsks(self)             # P322: `agentj ask` — the question card for any harness
        self.turn_by: str | None = None
        self.turn_t0 = 0.0
        # relay parity (PROTOCOL §10, PROMPT-33)
        self.hist = history.History(st)
        from .readmark import ReadMark
        self.readmark = ReadMark(st, self.hist)   # P321: seeded at the newest reply on a host's first mark
        self.lang = preferences.get(self.preferences, "appearance.language", "zh")
        self.uploads = uploads.Uploads(st, self.agent_cfg["dir"] if self.agent_cfg else None,
                                       asr_off=lambda: self.asr_state() == "off")
        self.media = media_mod.Media(st, self.agent_cfg["dir"] if self.agent_cfg else None)   # F21 (§13)
        self.media_streams: dict[tuple, asyncio.Task] = {}   # (cid, mid) → the window being sent
        self.media_jobs: set = set()
        self.sends = compose.Sends()
        self.receipts = receipts_mod.Receipts(st.root, st.log)   # P313 (§14): the same (device, sid) keys, on disk
        self.questions: dict[str, Question] = {}
        self.cur_turn: int | None = None        # the history page the running Agent turn answers
        self.cur_failed = False
        self.wait_note = None                    # P151b: (text, final, stall) of the running phone turn's wait line
        self.meter_state = {k: None for k in METER_KEYS}
        self.meter_at = 0.0
        self.meter_task: asyncio.Task | None = None
        self.asr = asr_mod                       # tests swap in a stand-in engine
        self.asr_lock: asyncio.Lock | None = None
        self.ff_lock: asyncio.Lock | None = None    # one say-time ffmpeg conversion at a time (P33-X09)
        self.asr_waiting: dict[str, int] = {}
        self.model_sets = 0
        self.model_set_at = 0.0                  # P239: monotonic time of the last pill switch handed to the Agent
        self.model_set_held: dict | None = None  # P239: a switch held back inside MODEL_SET_COALESCE (only the last one runs)
        self.elevate = elevate.Elevator(self)   # F17: admin password / secret cards (PROTOCOL §11)
        from .auth_rescue import Rescue
        self.auth_rescue = Rescue(self)          # P258: AI 登录救援 (subscription login expired → re-login from the phone)
        self.reset_desk = reset_mod.Desk(self)    # P332 (§33): reset / rescue_do, one run at a time, also during an e-stop
        self.cmd_desk = cmdrun.Desk(self)         # P361 (§34): Settings 「执行」, one run at a time
        from .capability import Capabilities
        self.capabilities = Capabilities(self)  # 0.18 (ADR-A193): capability.sock, opening cards, dispatch
        self.goals = goal_mod.Goals(self)       # P158: <state>/goal.json, `agentj goal …` over capability.sock
        self.peers = None                       # P71: Agent friends (peer_service.PeerService, PROTOCOL §17), set in run()
        self.hooks = None                       # P366: webhooks (hook_service.HookService, AGENTJ-HOOKS-SPEC), set in run()
        self.upg: dict | None = None            # P203: the open upgrade confirmation card (one at a time, §29)
        from . import busy as busy_mod
        self.busy = busy_mod.Board(st.root, on_change=self.busy_changed)   # P150 (§22): who works in the background
        from .background_done import Notifier
        self.background_done = Notifier(self)
        self.busy_sent: str | None = None
        from . import workflows as wf_mod
        self.wf = wf_mod.Scanner(st)            # P244 (§30): the five pages' read-only picture (workflows.py)
        from .heartbeat import Heartbeat
        self.heartbeat = Heartbeat(self)        # P156: wakes the main Agent after an idle spell (heartbeat.py)
        from .quota_resume import Resumer
        self.quota_resume = Resumer(self)       # P314: a turn stopped by a used-up usage limit continues after the reset
        from .imessage import IMessage
        self.imessage = IMessage(self)          # P274: macOS read-only iMessage assistant (off by default)
        self.res_watch: dict[int, float] = {}    # P181 (§22.1): cid → monotonic expiry of its load-card watch
        self.res_task: asyncio.Task | None = None
        self.theme_stamp = self.theme_cached = None   # P169 (§23): the phone's look, re-read when current.json changes

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
        elif ev in ("relay_fail", "mbox_fail"):   # P317: metadata only (relaydiag), also in quiet (service / journal) mode
            print(f"· {kw['line']}", flush=True)
        elif ev == "agent_name":   # a §1-valid name (no control / format / line-break chars), quoted, after the "· " marker
            print(f"· Agent 名已按账号后台改为「{kw['name']}」", flush=True)
        elif ev == "agent_name_refused":
            print("· 账号后台发来的 Agent 名用不了，这台Agent J 主机保留原名", flush=True)
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
        elif ev == "passkey":   # F20: no QR, no code — the paired phone proved itself with its passkey (§12)
            print(f"· 手机用 Face ID 连回来了（同一台，没有新增遥控器）：{kw.get('name') or ''} {kw.get('device') or ''}".rstrip(), flush=True)
        elif ev == "ticket":    # P122: the browser lost its storage; its cookie ticket proved the same phone (§12.1)
            print(f"· 手机自动连回来了（浏览器清过数据；同一台，没有新增遥控器）：{kw.get('name') or ''} {kw.get('device') or ''}".rstrip(), flush=True)
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
        if s:self.bots.detach(s)
        if s and s.timer and s.timer is not asyncio.current_task():
            s.timer.cancel()
        if s and s.state in ("pending", "ready"):
            self.reporter.trigger("pair_end" if s.state == "pending" else "gone")
        if s and s.card:
            self._pair_card_end(s)
        p = self.pairing
        if p and p.cid == cid:
            self._pair_finish(p, pair_result or {"ev": "denied", "reason": "bad_handshake"})
        return s

    async def _notify_close(self, cid: int) -> None:
        try:
            await asyncio.wait_for(self._op(wire.OP_CLOSE, cid), CLOSE_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — P192 (B18): the relay may keep the channel open; say so
            bg.swallowed(self.st.log, "relay_close_failed", e)

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
        backoff, fails = 1, 0
        url = f"{self.cfg['relay'].rstrip('/')}/v1/host/{self.channel}"
        if not self.identity_ok:            # P317: say it once, plainly — the relay will refuse every signature (4003)
            self.st.log("relay_identity", reason="channel_mismatch")
            self.emit("relay_fail", line=relaydiag.text2({"phase": "auth", "why": "identity"}) + " → agentj doctor")
        attempts = 0
        while not self.stopping.is_set():
            phase, t0 = "seat", time.monotonic()
            try:
                # P292: read before the relay's 10 s auth deadline starts, off the loop; P317: bounded (a parked disk)
                cert = await asyncio.wait_for(asyncio.to_thread(self.seat.cert), RELAY_OPEN_TIMEOUT)
                phase = "connect"
                async with connect(url, max_size=2**17, ping_interval=20, ping_timeout=20, open_timeout=RELAY_OPEN_TIMEOUT) as ws:
                    phase = "auth"
                    await self._auth(ws, cert)
                    phase = "up"
                    self.ws, self.relay_up, self.relay_ever_up, backoff, fails = ws, True, True, 1, 0
                    self.relay_seat_refused = False
                    self.st.log("relay_up", channel=self.channel, ms=_ms(t0), count=attempts)
                    if self.relay_last is not None:   # P317: the journal says the failure streak is over
                        self.emit("relay_fail", line=f"中继已连上（之前失败 {attempts} 次）/ relay up after {attempts} failed attempts")
                    self.relay_last, self.relay_said, attempts = None, (None, 0.0), 0
                    self.emit("relay", up=True)
                    async for msg in ws:
                        self._tap("in", msg)
                        if isinstance(msg, bytes):
                            await self.on_frame(msg)
            except Exception as e:  # network, handshake refusal, relay auth failure → back off and retry
                # P129: where it failed (connect / auth / up), the relay's close code and how long it took — no message text
                code = _close_code(e)
                self.relay_seat_refused = code == CLOSE_SEAT
                d = relaydiag.classify(e, phase)
                attempts += 1
                self.st.log("relay_down", reason="seat" if code == CLOSE_SEAT else proxy.relay_failure(e, url), phase=phase, code=code,
                            ms=_ms(t0), why=d["why"], http_status=d["http"], count=attempts)
                if phase != "up":
                    self.relay_last = {**d, "at": int(time.time()), "attempts": attempts}
            finally:
                if self.relay_up:
                    self.emit("relay", up=False)
                self.ws, self.relay_up = None, False
                await self._drop_all("relay_down")
            if self.stopping.is_set():
                break
            # P129: a link that was up and dropped, or one that has not come up yet (first tries), is retried at once:
            # a slow first answer (cold relay, a busy start) must not cost the doubling ladder. Only repeated failures back off.
            fails = 0 if phase == "up" else fails + 1
            # P193 (B12): jittered, so a relay deploy that drops every host does not bring them all back at once — the fast
            # retries ±25 % around FAST_RETRY, the ladder +0…50 % (never shorter than its step)
            fast, spread = fails <= FAST_RETRIES, secrets.randbelow(501) / 1000
            wait = FAST_RETRY * (0.75 + spread) if fast else backoff * (1 + spread)
            if self.relay_seat_refused:         # P185: no seat — asking again every second changes nothing
                cert = await asyncio.to_thread(self.seat.cert)
                wait = wait if cert and cert != self.seat_sent else SEAT_RETRY
            if phase != "up" and self.relay_last is not None:
                self.relay_last["retry_in"] = round(wait, 1)
                self._say_relay_fail(self.relay_last)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), wait)
            if fails > FAST_RETRIES:
                backoff = min(backoff * 2, 30)

    def _say_relay_fail(self, d: dict) -> None:
        """P317: a failed relay attempt reaches the journal / `serve --events text` (before, only host.log had it): the first
        one, every change of phase / reason / close code, and an unchanged streak at most every RELAY_SAY s."""
        key, now = (d["phase"], d["why"], d["code"], d["http"]), time.monotonic()
        if key == self.relay_said[0] and now - self.relay_said[1] < RELAY_SAY:
            return
        self.relay_said = (key, now)
        self.emit("relay_fail", line=f"中继连接失败 / relay connect failed #{d['attempts']}: {relaydiag.text2(d)} · "
                  f"{d.get('retry_in')} s 后重试 / retry in {d.get('retry_in')} s")

    def _say_mbox_fail(self, d: dict) -> None:
        """P317: the friends mailbox leg's failed attempt, the same way (its own change / RELAY_SAY memory)."""
        key, now = (d.get("phase"), d.get("why"), d.get("code"), d.get("http")), time.monotonic()
        said = getattr(self, "mbox_said", (None, 0.0))
        if key == said[0] and now - said[1] < RELAY_SAY:
            return
        self.mbox_said = (key, now)
        self.emit("mbox_fail", line=f"好友信箱连接失败 / friends mailbox connect failed: {relaydiag.text2(d)}")

    async def seat_loop(self) -> None:
        """P185: keep the relay seat certificate fresh (seatcert decides when to ask the Dashboard — never when exempt or
        unbound); a renewal goes to the open relay link as {"t":"cert"}, so the link never drops for a renewal."""
        while not self.stopping.is_set():
            try:
                if self.seat.due():
                    before = self.seat.cert()
                    await asyncio.to_thread(self.seat.refresh)
                    cert = self.seat.cert()
                    if cert and cert != before and self.relay_up:
                        with contextlib.suppress(Exception):
                            await self._ws_send(json.dumps({"t": "cert", "cert": cert}))
            except Exception as e:  # noqa: BLE001 — the renewal never takes serve down
                self.st.log("seat_cert_failed", reason=type(e).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), SEAT_TICK)

    def seat_refusal(self, feature: str | None = None) -> str | None:
        """P185: None while this host may serve (seat ok / bootstrap grace / exempt), else the one-language reason with where
        to fix it. Shown to the phones as a 「在Agent J 主机上处理」 notice at most every SEAT_NOTE seconds.
        P199: `feature` (plaza / support / friends / bots) → the same verdict, worded for that feature (seatcert.feature_message)."""
        seat = getattr(self, "seat", None)
        if seat is None:
            return None
        from . import seatcert, cloud as _cloud
        v = seat.verdict()
        if v in seatcert.ALLOWED:
            return None
        if feature:
            return seatcert.feature_message(feature, v, getattr(self, "lang", "zh"), _cloud.app_url(self.st))
        return seatcert.message(v, getattr(self, "lang", "zh"), _cloud.app_url(self.st))

    def seat_note(self, text: str) -> None:
        now = time.time()
        if now - self.seat_noted >= SEAT_NOTE:
            self.seat_noted = now
            self.local_notice(text)

    def relay_state(self) -> str:
        """P129: "up" · "connecting" (never up since start, within RELAY_GRACE) · "reconnecting" (down after being up, or
        still not up after the grace)."""
        if self.relay_up:
            return "up"
        if self.relay_seat_refused:
            return "seat"
        if not self.relay_ever_up and time.monotonic() - self.started_at < RELAY_GRACE:
            return "connecting"
        return "reconnecting"

    async def loop_watch(self) -> None:
        """P129: the relay link and every phone's heartbeat share this event loop; a start-up step or handler that blocks it
        delays every pong (a phone gives up after its probe deadline) and the relay's auth answer (10 s deadline). Log it."""
        probe = loopwatch.StallProbe(asyncio.get_running_loop(), LOOP_STALL)   # P136: and what was running when it stuck
        probe.start()
        try:
            while not self.stopping.is_set():
                t = time.monotonic()
                probe.beat()
                await asyncio.sleep(1)
                late = time.monotonic() - t - 1
                if late >= LOOP_STALL:
                    self.st.log("loop_stall", ms=int(late * 1000), **probe.take())
                else:
                    probe.take()
        finally:
            probe.stop.set()

    async def _auth(self, ws, cert: str | None | bool = False) -> None:
        if cert is False:                          # P292: never a file read on the loop inside the auth deadline
            cert = await asyncio.to_thread(self.seat.cert)
        msg = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT))
        # F10: sign only a nonce of the relay's exact shape (32 random bytes, b64url) — never an arbitrary string
        if msg.get("t") != "challenge" or not isinstance(msg.get("n"), str) or not _NONCE.fullmatch(msg["n"]):
            raise ValueError("bad challenge")
        sig = self.sk.sign(f"agentjarvis-relay-auth-v1\n{self.channel}\n{msg['n']}".encode())
        auth = {"t": "auth", "pk": wire.b64u(self.st._ed_pub(self.sk)), "sig": wire.b64u(sig)}   # P292: the key in memory
        self.seat_sent = cert                      # P185 (§2 seat certificate): the relay seat certificate, when this host has one
        if cert:
            auth["cert"] = cert
        await ws.send(json.dumps(auth))
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
            pairing = bool(self.pairing and self.pairing.cid == cid)
            s = self._detach(cid, {"ev": "gone"})
            if s and s.state == "ready":       # P129: a phone's socket ended at the relay (network, sleep, the page's own close)
                self.st.log("dev_gone", cid=cid, device=s.device, ms=int((time.monotonic() - s.ready_at) * 1000))
            elif s and pairing:                # P153: the device being paired left — `agentj pair` says 「手机断开了」 (10-12: 4029)
                self.st.log("pair_gone", cid=cid, device=s.device, phase=s.state)
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
        elif kind == wire.DATA and s.state in ("hello", "pending", "ready", "pk"):
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
        if not self.st.is_allowed(hs.rs) and self._pk_hello(payload):
            # F20 (§12): an unlisted key that asks for a passkey restore (`pk:1` + an approval key in msg1): finish the
            # handshake, take its hello, then wait ≤ 30 s for one pk_restore — no `ready`, no `removed` before that
            did = wire.device_id(hs.rs)
            self.st.log("pk_hello", cid=s.cid, device=did)
            msg2 = hs.write_message(b"")
            s.send, s.recv, s.h = hs.split()
            s.pub, s.device, s.mode, s.state = hs.rs, did, "pk", "hello"
            s.sign_pub = _sign_key(json.loads(payload))
            await self._op(wire.OP_DATA, s.cid, bytes([wire.HS_RESP]) + msg2)
            return
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
        s.name = self._dev_label(s.device)
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
            s.lt1 = isinstance(caps, list) and wire.CAP_LT1 in caps[:16]
            s.pair1 = s.p33 and isinstance(caps, list) and pair_card.CAP in caps[:16]
            s.rc = _reconnect_reason(obj.get("rc"))
            h = obj.get("hist")
            if s.p33 and isinstance(h, dict) and type(h.get("epoch")) is int and type(h.get("last")) is int:
                s.hist = {"epoch": h["epoch"], "last": h["last"]}
            if s.mode == "pk":                     # F20: the next (and only) message must be pk_restore
                s.state, s.since = "pk", obj.get("since")
                self._set_timer(s, self._pk_timeout(s))
                return
            if s.mode == "resume":
                if not self.st.is_allowed(s.pub):  # revoked between msg1 and hello
                    return await self.close_cid(s.cid, "revoked")
                return await self._accept_resume(s, obj.get("since"))
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
            if not p.remote:                       # P153: every paired remote gets the card (an account QR grants at once)
                self._pair_card_open(p, s)
            await self._ctl_send(p.ctl, self._pending_event(s))
            if p.phone:
                await self._send_device(p.phone, {"t": "pair_ev", "ev": "pending", "name": s.name})
            if s.card:
                await self.send_app(s, {"t": "pair_wait", "ttl": max(0, int(s.deadline - time.monotonic()))})
            await self.remote_pair.pending(p, s)
            await self.remotes.pending(p, s)
            return
        if s.state == "pending":
            return  # nothing from an unapproved device is acted on
        if s.state == "pk":
            return await self._pk_restore(s, obj)
        if not self.st.is_allowed(s.pub):  # belt and braces: a ready session whose device left the allowlist
            return await self.close_cid(s.cid, "revoked")
        if t in ("msg", "say", "answer", "slash", "blob_open", "blob_chunk", "blob_end", "ask_answer", "estop", "resume", "task_set", "lt_answer") or t in elevate.PHONE_TYPES or t in ("fr_answer", "fr_accept"):
            self._phone_touch()                   # P292: config lock + write, off the loop
        if t == "msg":
            text = obj.get("text")
            limit = wire.MAX_TEXT_P33 if s.p33 else wire.MAX_TEXT
            if not isinstance(text, str) or text_units(text) > limit:  # reject, never silently truncate
                return await self.close_cid(s.cid, "bad_msg")
            hc = slash.parse(text)
            if hc and (friend_cmds.name_of(hc[0]) or hc[0] in TG_PAIR_CMDS or hc[0] in RELOAD_CMDS or hc[0] in reset_mod.RESET_CMDS):   # P73 · P283 · P320 · P332: the host's own answer
                return await self.on_slash(s, hc[0], hc[1], confirm=False, typed=True)
            if hc and hc[0] == "goal" and goal_mod.host_cmd(hc[1]):  # P158: /goal status · /goal stop
                return await self.on_slash(s, "goal", hc[1], confirm=False, typed=True)
            if self.stopped() and self.agent:      # stopped: nothing is queued to run later behind the human's back
                self.st.log("msg_refused", cid=s.cid, device=s.device, reason="estop")
                self.activity("message_refused", by=s.name or s.device, text=text[:120])
                if not s.p33:
                    await self.send_app(s, {"t": "msg", "id": secrets.token_hex(8), "ts": int(time.time() * 1000),
                                            "seq": self.seq, "from": "notice",
                                            "text": "已急停：这条没有交给 Agent J，也不会排队。恢复后再发。"})
                return
            cmd = slash.parse(text)
            if cmd and cmd[0] != "goal" and self.agent and cmd[0] not in slash.AGENT_CMDS and not self.agent.passthrough(cmd[0]):
                return await self.on_slash(s, cmd[0], cmd[1], confirm=False, typed=True)
            await self._accept(s, text, sid=None)
        elif t in P33_ONLY:
            if s.p33:                          # §10 messages only from a device that announced p33 (§10.0)
                await self.on_p33(s, t, obj)
        elif t in ("bots_read", "bots_write"):
            await self.bots.phone(s, obj, background=True)
        elif isinstance(t, str) and t.startswith(("fr_", "pg_")):   # P71 (§17.7): the friends page
            await self.on_friends(s, t, obj)
        elif t == "answer":
            await self._answer(s, obj)
        elif t in ("pk_reg", "pk_offer_req"):  # F20 (§12): register this device's passkey
            await self._pk_reg(s, t, obj)
        elif t == "pair_new":                  # P153 (§3.3): this remote adds another one
            await self._pair_new(s, obj)
        elif t == "pair_stop":
            await self._pair_stop(s)
        elif t == "rt_req":                    # P122 (§12.1): a ready device asks for a (new) renewal ticket
            await self._rt_issue(s)
        elif t in elevate.PHONE_TYPES:  # F17: a signed, sealed sudo password / secret for an open card (§11)
            await self.elevate.on_phone(s, obj)
        elif t == "lt_answer":          # 0.18 (ADR-A193): the signed answer to an opening card (preflight + brief + enable)
            await self.capabilities.on_phone(s, obj)
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
        elif t == "cards_get":          # P203 (§29.1): a signed read — every card still waiting, as this device sees it
            if s.p33:
                await self.on_cards_get(s, obj)
        elif t in ("upg_check", "upg_card_off"):   # P203 (§29.2): what an upgrade now would interrupt · close the card
            if s.p33:
                await self.on_upg_phone(s, t, obj)
        elif t in ("setup_get", "setup_mark", "setup_close"):   # P115 / P131: the first-run checklist (first_run.py)
            await self.on_setup(s, t, obj)
        elif t in ("reset", "rescue_do"):   # P332 (§33): signed; allowed during an e-stop (repairs connectivity only)
            if s.p33:
                await self.reset_desk.on_phone(s, t, obj)
        elif t in PHONE_CONTROLS:
            await self.on_control(s, t, obj)
        elif t in WF_READS:             # P244 (§30): 工作流 · 技能 · 记忆 · Agent J pages, read-only
            if s.p33:
                await self.on_wf(s, t, obj)
        elif t in jcards.PHONE_TYPES:   # P360 (§34): Agent J Cards — the list, one card's data + buttons, a card's switch
            if s.p33:
                await self.on_jcard(s, t, obj)
        elif t in REMOTES_TYPES:        # P171 (§24): list / rename / unbind / add remotes from a paired remote
            if s.p33:
                await self.remotes.on_phone(s, t, obj)
        elif t == "agent_name":         # P181 (§22.2): long-press the Agent's name → rename, signed like every phone write
            if s.p33:
                await self.on_agent_name(s, obj)
        elif t == "mail_allow":         # P207 (§25): the address book adds an address to the mail allowlist (signed)
            if s.p33:
                await self.on_mail_allow(s, obj)
        elif t == "approval_set":       # P336 (§8): approval level / goal without the opening card — signed, never pref_set
            if s.p33:
                await self.on_approval_set(s, obj)
        elif t == "cmd_run":            # P361 (§34): Settings 「执行」 — signed + Face ID, whitelisted names only (cmdrun.py)
            if s.p33:
                await self.cmd_desk.on_phone(s, obj)
        elif t == "slash":              # a command from the phone's ≡「全部命令」 menu (or typed: the page sends it like this)
            name, arg = obj.get("cmd"), obj.get("arg", "")
            if isinstance(name, str) and isinstance(arg, str) and len(name) <= 32 and text_units(arg) <= slash.ARG_MAX:
                await self.on_slash(s, name.lower(), arg.strip(), confirm=obj.get("confirm") is True, typed=False, authorization=obj)

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
        if p.phone:
            self.remotes.finished(p, result)

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

    def terminal_code_ok(self, pid) -> bool | None:
        """P176 let `agentj pair` approve with the 6-digit code alone on a bound host without a passphrase. P221 (Leo 10-13):
        pairing at the terminal always needs the remote pairing password (`agentj pair` sets it first), so a keyboard is
        never enough: False = refused (the phone keeps waiting for the account page / a paired remote), None = a password
        is set and the usual rules apply. Kept so a pre-0.20.3 `agentj pair` sending `terminal` is answered, not broken."""
        return None if gate.is_set(self.st) else False

    async def decide(self, p: Pairing, code: str, passphrase: str | None = None, terminal: bool = False,
                     retry: bool = False) -> dict:
        """The human's answer for the pending device. A non-empty code also needs the remote pairing password (L2, gate.py),
        checked before the code; a wrong one keeps the device waiting (`pass_wrong`, the code is not used up) until the
        gate locks. P221: `terminal` no longer stands in for the passphrase (P176 did, on a bound host without one);
        `retry` lets a wrong code keep the device waiting (`code_wrong`) until CODE_TRIES are used."""
        s = self.sessions.get(p.cid) if p.cid is not None else None
        if not s or s.state != "pending":
            return {"ev": "denied", "reason": "no_pending_device"}
        if time.monotonic() > s.deadline:  # the timer may not have run yet; the deadline is what counts
            res = {"ev": "denied", "reason": "timeout"}
            await self.close_cid(s.cid, "approve_timeout", res)
            return res
        if code.strip():                                    # P221: no keyboard-only approval any more (was P176)
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
        if not ok and retry and code.strip() and p.code_fails + 1 < CODE_TRIES:
            p.code_fails += 1
            self.st.log("pair_code_wrong", cid=s.cid, device=s.device, left=CODE_TRIES - p.code_fails)
            return {"ev": "code_wrong", "left": CODE_TRIES - p.code_fails}
        self._set_timer(s, None)
        if not ok:
            res = {"ev": "denied", "reason": "code_mismatch" if code.strip() else "denied"}
            self.st.log("pair_denied", cid=s.cid, device=s.device, code_ok=False)
            await self.close_cid(s.cid, res["reason"], res)
            self.emit("denied", device=s.device, name=s.name)
            return res
        return await self._grant_pair(s)

    async def _grant_pair(self, s: Session, source: str = 'local') -> dict:
        self._set_timer(s, None)
        if s.card:                    # P153: approved here (terminal / admin page / account page): the remotes' card folds
            self._pair_card_end(s)
        try:   # 0.15.1: full → the stalest remote makes room (State.pair_device); same browser → its old record goes
            got = self.st.pair_device(s.pub, s.name, s.sign_pub or None, iid=s.iid or None, evict=True,
                                      online=self._online() - {s.device}, source=source)
        except DeviceLimit:   # not reachable with evict=True; kept so a future cap change fails closed
            res = {"ev": "denied", "reason": "device_limit"}
            await self.close_cid(s.cid, "device_limit", res)
            return res
        for kind in ("replaced", "evicted"):
            if got[kind]:
                self.st.log("auto_unbind", device=got[kind]["id"], reason="same_browser" if kind == "replaced" else "full")
                await self._drop_device(got[kind]["id"], "replaced")
        s.name = self._dev_label(s.device) or s.name   # P179: a remote the owner named before keeps that name
        s.state, s.ready_at = "ready", time.monotonic()
        self.reporter.trigger("approve")
        await self.send_app(s, {"t": "approved", **self._caps(s)})
        await self._bulk(s)
        await self._pk_offer(s)                 # F20: once, right after a pairing (never on a resume)
        await self._rt_issue(s, force=True)     # P122: the renewal ticket for this pairing (§12.1)
        self.st.log("pair_approved", cid=s.cid, device=s.device, name=s.name, code_ok=source == "local", source=source)
        self.emit("approved", device=s.device, name=s.name)
        self._bg(self._ready_then_onboard(s), "onboard")   # F28: the first remote ever → the main Agent's welcome there
        res = {"ev": "approved", "device": s.device, "name": s.name}
        res.update({k: got[k] for k in ("replaced", "evicted") if got[k]})
        return res

    # ------------------------------------------------------------ P153: add a remote from a remote (PROTOCOL §3.3)
    async def _send_pair1(self, obj_fn) -> None:
        for x in list(self.sessions.values()):
            if x.state == "ready" and x.pair1 and self.st.is_allowed(x.pub):
                await self.send_app(x, obj_fn(x))

    async def _pair_new(self, s: Session, obj: dict) -> None:
        """A paired remote asks for a QR to add another one (signed control `pair_new`). The link goes back to that remote
        only, inside its own Noise session; the pairing is the same single one `agentj pair` / the account page use, so a
        pairing already in progress elsewhere answers `busy` (this remote's own earlier one is replaced)."""
        r = obj.get("r")
        if not s.pair1 or not isinstance(r, str) or not _RID.fullmatch(r):
            return
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "pair_new", {})
        if why:
            self.st.log("control_refused", device=s.device, action="pair_new", reason=why)
            controls.log(self.st, action="pair_new", device=s.device, result="refused:" + why)
            return await self.send_app(s, {"t": "pair_link", "r": r, "ok": False, "why": why})
        old = self.pairing
        if old and old.phone != s.device:
            return await self.send_app(s, {"t": "pair_link", "r": r, "ok": False, "why": "busy"})
        if self.stopped():
            return await self.send_app(s, {"t": "pair_link", "r": r, "ok": False, "why": "estop"})
        if old:
            self._pair_finish(old, {"ev": "replaced"})
            if old.cid is not None:
                await self.close_cid(old.cid, "pair_replaced")
        p = Pairing(secrets.token_bytes(16), secrets.token_bytes(32), time.time() + PAIR_TTL, time.monotonic() + PAIR_TTL,
                    None, phone=s.device)
        self.pairing = p
        p.timer = asyncio.create_task(self._expire_pairing(p))
        link = wire.pairing_link(self.cfg["web"], self.cfg["relay"], self.channel, self.kp.pub, p.pid, p.psk, int(p.expires))
        qr = wire.pairing_qr(link).png_data_uri(scale=6, border=2, dark="#141414", light="#faf9f5")
        self.st.log("pair_begin", via="remote", device=s.device)
        controls.log(self.st, action="pair_new", device=s.device, result="ok", obj={}, sig=obj.get("sig"), n=obj.get("n"),
                     ts=obj.get("ts"))
        self.activity("pair_begin", by=self._dname(s.device))
        await self.send_app(s, {"t": "pair_link", "r": r, "ok": True, "link": link, "qr": qr,
                                "ttl": max(0, int(p.deadline - time.monotonic()))})
        self._bg(self._pair_outcome(p), "pair_outcome")

    async def _pair_outcome(self, p: Pairing) -> None:
        """The remote that opened the pairing hears how it ended (its QR sheet closes or says why)."""
        with contextlib.suppress(asyncio.CancelledError):
            res = await asyncio.shield(p.done)
            ev = res.get("ev") if isinstance(res, dict) else None
            out = {"t": "pair_ev", "ev": ev if ev in ("approved", "denied", "expired", "replaced") else "denied"}
            if ev == "approved":
                out["name"] = res.get("name", "")
            elif ev == "denied":
                out["why"] = str(res.get("reason", ""))[:32]
            await self._send_device(p.phone, out)

    async def _pair_stop(self, s: Session) -> None:
        """The opening remote closed its QR sheet: the pairing ends (a device already waiting is refused)."""
        p = self.pairing
        if not p or p.phone != s.device:
            return
        cid = p.cid
        self._pair_finish(p, {"ev": "denied", "reason": "cancelled"})
        if cid is not None and cid in self.sessions:
            await self.close_cid(cid, "pair_cancelled")
        self.st.log("pair_cancelled", device=s.device)

    def _pair_card_open(self, p: Pairing, s: Session) -> None:
        """A device waits: ONE pair_device card on every paired remote that renders it (pair1). Its answer is a §8 signed
        answer (+ the remote's passkey when it has one); the 6-digit code is shown on both screens, never typed. No remote
        with an approval key → no card (the terminal / account routes only, as before)."""
        if not self._approvers():
            return
        s.deadline = max(s.deadline, s.pending_since + pair_card.CARD_TTL)
        self._set_timer(s, self._approve_timeout(s))
        text = pair_card.summary(self.lang, s.name, wire.safety_code(s.h), self._pending_event(s))
        target = {"device": s.device, "handshake": pair_card.handshake_id(s.h)}
        rid = secrets.token_hex(16)
        a = Ask(rid, pair_card.TOOL, text, approvals.shown_digest(pair_card.TOOL, text), target, approvals.input_digest(target),
                s.deadline, asyncio.get_running_loop().create_future(), pk_if_any=True, pk_kind=pair_card.PK_KIND)
        self.asks[rid] = a
        s.card = rid
        self.st.log("ask", id=rid, tool=pair_card.TOOL, agent="host", kind="pair")
        self.activity("ask", id=rid, tool=pair_card.TOOL, summary=text.split("\n", 1)[0])
        self.emit("ask", id=rid, tool=pair_card.TOOL, summary=text)
        self._post(self._send_pair1, lambda x: self._ask_msg(a, x))
        self.status_changed()
        self.push_notify("ask")
        self._bg(self._pair_card_wait(p, s, a), "pair_card")

    # ------------------------------------------------------------ #118: reset the remote pairing password, keep every remote
    async def _pass_consent(self, req: dict, pid) -> dict:
        """`agentj passphrase reset --phone` (pass_reset.py): ONE card on every ready paired remote; the first signed answer
        (+ its passkey when it has one) decides. An allow gives this peer process a one-use ticket; nothing else changes."""
        from . import pass_reset
        code = req.get("code")
        if not isinstance(code, str) or not pass_reset.CODE.fullmatch(code):
            return {"ok": False, "error": "shape"}
        if self.stopped():
            return {"ok": False, "error": "stopped"}
        if not gate.is_set(self.st):
            return {"ok": False, "error": "not_set"}
        if not self._approvers():
            return {"ok": False, "error": "no_device"}
        if any(a.tool == pass_reset.TOOL and not a.fut.done() for a in self.asks.values()):
            return {"ok": False, "error": "busy"}
        text = pass_reset.summary(self.lang, code)
        target = {"op": pass_reset.TOOL, "code": code}
        rid = secrets.token_hex(16)
        a = Ask(rid, pass_reset.TOOL, text, approvals.shown_digest(pass_reset.TOOL, text), target,
                approvals.input_digest(target), time.monotonic() + pass_reset.CARD_TTL,
                asyncio.get_running_loop().create_future(), pk_if_any=True, pk_kind=pass_reset.PK_KIND)
        self.asks[rid] = a
        self.st.log("ask", id=rid, tool=pass_reset.TOOL, agent="host", kind="passphrase")
        self.activity("ask", id=rid, tool=pass_reset.TOOL, summary=text.split("\n", 1)[0])
        self.emit("ask", id=rid, tool=pass_reset.TOOL, summary=text)
        self._post(self._send_ready, lambda s: self._ask_msg(a, s))
        self.status_changed()
        self.push_notify("ask")
        try:
            await asyncio.wait({a.fut}, timeout=max(0.0, a.deadline - time.monotonic()))
            if not a.fut.done():
                a.fut.set_result(("timeout", None, None, None))
            decision, did, sk, sig = a.fut.result()
        finally:
            self.asks.pop(rid, None)
        if did and decision == "allow":
            result, reason = "allow", "device"
        elif did:
            result, reason = "deny", "device"
        else:
            result = reason = "timeout" if decision == "timeout" else ("estop" if self.stopped() else "serve_stop")
        approvals.record(self.st, rid=rid, agent="host", tool=pass_reset.TOOL, input_sha256=a.input_sha,
                         shown_sha256=a.digest, decision="allow" if result == "allow" else "deny", reason=reason,
                         device=did if reason == "device" else None, sign_pub=sk, sig=sig)
        self.st.log("ask_done", id=rid, tool=pass_reset.TOOL, decision="allow" if result == "allow" else "deny",
                    reason=reason, device=did)
        self.activity("decision", id=rid, tool=pass_reset.TOOL, result=result, reason=reason,
                      by=self._dname(did) if did else None, summary=text.split("\n", 1)[0])
        self.emit("ask_done", id=rid, result=result, reason=reason, device=did)
        self._post(self._send_ready, lambda x: {"t": "ask_done", "id": rid, "result": result})
        self.status_changed()
        if result != "allow":
            return {"ok": False, "error": "denied" if result == "deny" else reason}
        tok = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._pp_tickets = {t: v for t, v in self.__dict__.get("_pp_tickets", {}).items() if v[1] > now}
        self._pp_tickets[tok] = (pid, now + pass_reset.TICKET_TTL, did)
        return {"ok": True, "ticket": tok, "by": self._dname(did)}

    async def _pass_replace(self, req: dict, pid) -> dict:
        """The new password, typed at the terminal that asked, with the ticket a remote's allow gave that same process."""
        from . import pass_reset
        tok, new = req.get("ticket"), req.get("new")
        tickets = self.__dict__.get("_pp_tickets", {})
        rec = tickets.pop(tok, None) if isinstance(tok, str) else None
        if not rec or rec[0] != pid or time.monotonic() > rec[1]:
            self.st.log("ctl_refused", cmd="passphrase_replace", reason="ticket")
            return {"ok": False, "error": "ticket"}
        if not isinstance(new, str):
            tickets[tok] = rec
            return {"ok": False, "error": "shape"}
        try:
            await asyncio.to_thread(gate.replace, self.st, new, rec[2])   # scrypt: never on the event loop
        except gate.GateError as e:
            tickets[tok] = rec                                             # too short / long: the owner may try again
            return {"ok": False, "error": "passphrase", "reason": e.reason}
        self.agent_notice(pass_reset.notice(self.lang, self._dname(rec[2])))
        self.push_notify("security")
        return {"ok": True, "kept": len(self.st.devices())}

    def _pair_card_end(self, s: Session) -> None:
        a = self.asks.get(s.card)
        s.card = ""
        if a is not None and not a.fut.done():
            a.fut.set_result(("end", None, None, None))

    async def _pair_card_wait(self, p: Pairing, s: Session, a: Ask) -> None:
        try:
            await asyncio.wait({a.fut}, timeout=max(0.0, a.deadline - time.monotonic()))
            if not a.fut.done():
                a.fut.set_result(("timeout", None, None, None))
            decision, did, sk, sig = a.fut.result()
        finally:
            self.asks.pop(a.rid, None)
        live = self.sessions.get(s.cid) is s and s.state == "pending" and s.card == a.rid
        if did and live:
            s.card = ""
            if decision == "allow":
                res = await self._grant_pair(s, source="remote")
                self._pair_finish(p, res)
                result = "allow" if res.get("ev") == "approved" else "gone"
            else:
                res = {"ev": "denied", "reason": "denied_remote"}
                self.st.log("pair_denied", cid=s.cid, device=s.device, reason="denied_remote", by=did)
                await self.close_cid(s.cid, "denied_remote", res)
                self.emit("denied", device=s.device, name=s.name)
                result = "deny"
        elif decision == "deny" and self.stopped():
            result = "stopped"
            if live:
                await self.close_cid(s.cid, "estop", {"ev": "denied", "reason": "estop"})
        else:
            result = "timeout" if decision == "timeout" else "gone"
        reason = "device" if did and result in ("allow", "deny") else result
        if result == "gone" and decision == "end" and self.st.is_allowed(s.pub):
            reason = "elsewhere"   # P242: approved on another route (the asking remote's code, terminal, account page)
        approvals.record(self.st, rid=a.rid, agent="host", tool=pair_card.TOOL, input_sha256=a.input_sha,
                         shown_sha256=a.digest, decision="allow" if result == "allow" else "deny", reason=reason,
                         device=did if reason == "device" else None, sign_pub=sk, sig=sig)
        self.st.log("ask_done", id=a.rid, tool=pair_card.TOOL, decision="allow" if result == "allow" else "deny",
                    reason=reason, device=did)
        self.activity("decision", id=a.rid, tool=pair_card.TOOL, result=result, reason=reason,
                      by=self._dname(did) if did else None, summary=a.summary.split("\n", 1)[0])
        self.emit("ask_done", id=a.rid, result=result, reason=reason, device=did)
        self._post(self._send_ready, lambda x: {"t": "ask_done", "id": a.rid, "result": result})
        self.status_changed()
        if reason == "device" and result == "allow":   # the chat line is onboarding's 「…也连上了」 (one per new remote)
            self.push_notify("security")

    # ------------------------------------------------------------ F28 (P72): first-use onboarding (agentj/onboarding.py)
    def onboarding_lang(self) -> str:
        return "en" if str(preferences.get(self.preferences, "appearance.language", "zh")).lower().startswith("en") else "zh"

    def speaker_note(self, s: Session) -> str:
        """P179: which remote is speaking, for the Agent — only once the owner named it (a device's own label says nothing
        about the person). Marked as the owner's label, not identity proof. "" when the remote is not owner-named."""
        n = device_names(self.st.devices().get(s.device))
        if n["named_by"] != "owner":
            return ""
        label = self._dev_label(s.device)
        if self.onboarding_lang() == "en":
            return (f"\n\n〔Sent from the remote the owner named “{label}”. Use it to tell who is speaking; it is a label the "
                    "owner set, not proof of identity.〕")
        return f"\n\n〔发言的遥控器：「{label}」——这是主人给这台遥控器起的名字，用来分辨是谁在说话；只是标签，不是身份证明。〕"

    def onboarding_note(self) -> str:
        """The bracketed note for the Agent on the owner's next message (tour pace / a missing required remote), or ""."""
        try:
            return onboarding.turn_note(self.st, self.onboarding_lang())
        except Exception:  # noqa: BLE001 — onboarding never costs a message
            self.st.log("onboarding", status="note_error")
            return ""

    async def _ready_then_onboard(self, s: Session) -> None:
        await self.on_ready(s, None)
        await self.onboarding_paired(s)

    async def onboarding_paired(self, s: Session) -> None:
        """A remote was just approved: the first one ever gets the main Agent's welcome (written by the Agent itself, in its
        own harness; a fixed text when no Agent runs), every later new one a single line. Persisted: never twice."""
        try:
            what = await asyncio.to_thread(onboarding.paired, self.st, s.device, s.name)
            if not what:
                return
            lang, kind = self.onboarding_lang(), onboarding.kind_of(s.name)
            now = await asyncio.to_thread(onboarding.status, self.st)
            if what == "welcome":           # P194: the owner card (slot 0 of the address book) follows the first welcome
                self._bg(self._owner_card_after_welcome(), "owner-card")
            if what == "welcome" and self.agent and not self.stopped():
                self.agent.submit(onboarding.WelcomeSend(onboarding.welcome_prompt(kind, s.name, now, lang)))
                return
            text = onboarding.also_text(kind, s.name, now, lang) if what == "also" else onboarding.fallback_welcome(kind, now, lang)
            e = self._remember("notice" if what == "also" else "agent", text)
            self._post(self._send_legacy, lambda o, e=e: self._render(e, o))
            self.hist_add({"k": "sys" if what == "also" else "agent", "text": ""}, text, "done")
        except Exception:  # noqa: BLE001 — a failed welcome never costs the pairing
            self.st.log("onboarding", status="error")

    OWNER_CARD_DELAY = 40.0            # seconds after the first pairing: the welcome is read first, then the card rises

    async def _owner_card_after_welcome(self) -> None:
        """P194: once, after the first remote's welcome — the owner card asks for the owner's own name / main e-mail / phone
        (slot 0). Skippable; filled later in Settings or by `agentj contacts owner set`. Nothing is saved without the tap."""
        from . import contacts as C
        await asyncio.sleep(self.OWNER_CARD_DELAY)
        try:
            if self.stopped() or C.owner(await asyncio.to_thread(C.read, self.st)) is not None:
                return
            res = await self.elevate.contacts.card({"op": "card", "owner": True, "contact": {}})
            self.st.log("owner_card", result=res.get("result"))
        except Exception:  # noqa: BLE001 — a failed card never costs the welcome
            self.st.log("owner_card", result="error")

    async def _accept_resume(self, s: Session, since) -> None:
        """A resume (or a passkey restore, §12) is accepted: `ready`, BULK, history replay."""
        s.state = "ready"
        self._set_timer(s, None)
        await self.send_app(s, {"t": "ready", **self._caps(s)})
        await self._bulk(s)
        s.ready_at = time.monotonic()
        why, code, down = s.rc or (None, None, None)
        self.st.log("resume_ok", cid=s.cid, device=s.device, why=why, code=code, ms=down)
        self._bg(asyncio.to_thread(self._touch_seen, s.device), "touch_seen")   # P292: ≤ 1 write a minute, off the loop
        self.emit("ready", device=s.device, name=s.name)
        self.reporter.trigger("online")
        await self.on_ready(s, since)

    # ------------------------------------------------------------ F20 passkey (PROTOCOL §12) — proves "the same phone", nothing more
    @staticmethod
    def _pk_hello(payload: bytes) -> bool:
        """Does a RESUME msg1 ask for a passkey (`pk:1`) or renewal-ticket (`rt:1`, P122 §12.1) restore, with an approval
        key (the restore replaces it)?"""
        try:
            info = json.loads(payload) if payload else None
        except ValueError:
            return False
        return isinstance(info, dict) and (info.get("pk") == 1 or info.get("rt") == 1) and bool(_sign_key(info))

    async def _pk_timeout(self, s: Session) -> None:
        await asyncio.sleep(HS_TTL)
        if self.sessions.get(s.cid) is s and s.state == "pk":
            self.st.log("pk_restore", cid=s.cid, device=s.device, result="fail", reason="timeout")
            await self._pk_fail(s, "expired")

    async def _pk_fail(self, s: Session, why: str) -> None:
        s.state = "closing"                   # nothing more from it is acted on
        await self.send_app(s, {"t": "pk_fail", "why": why})
        await self.close_cid(s.cid, "pk_fail")

    async def _pk_restore(self, s: Session, obj: dict) -> None:
        """The one message a pk session may send. Every check passes or the session ends with pk_fail; then the device
        record that holds this passkey takes the session's key (State.passkey_restore) and the old id ends like a P55
        `replaces` (sessions, push subscription, uploads; told `replaced` if it resumes)."""
        self._set_timer(s, None)
        if obj.get("t") == "rt_restore":
            return await self._rt_restore(s, obj)
        old = None
        try:
            if obj.get("t") != "pk_restore":
                raise passkey.PasskeyError("bad", "not_pk_restore")
            if not self.pk_guard.attempt():
                raise passkey.PasskeyError("bad", "rate")
            f = passkey.restore_fields(obj)
            if not passkey.ts_ok(f["ts"]):
                raise passkey.PasskeyError("expired", "ts")
            if not self.pk_guard.fresh(f["nonce"]):
                raise passkey.PasskeyError("bad", "replay")
            hit = self.st.passkey_of(f["id"])
            if not hit:
                raise passkey.PasskeyError("unknown", "credential")
            old, pk = hit
            if not passkey.handle_matches(f["uh"], self.channel, self.kp.pub):
                raise passkey.PasskeyError("bad", "user_handle")
            challenge = passkey.restore_challenge(s.pub, s.sign_pub, f["ts"], f["nonce"])
            count = passkey.verify_assertion(pk, f, challenge)
            new = self.st.passkey_restore(old, f["id"], s.pub, s.sign_pub, count, _iid(obj) or None)
            if not new:
                raise passkey.PasskeyError("unknown", "gone")
        except passkey.PasskeyError as e:
            self.st.log("pk_restore", cid=s.cid, device=old or s.device, result="fail", reason=e.detail)
            return await self._pk_fail(s, e.why)
        if old != new:
            self.st.log("auto_unbind", device=old, reason="passkey")
            await self._drop_device(old, "replaced")
        s.device, s.mode = new, "resume"
        s.name = self._dev_label(new)
        self.st.log("pk_restore", cid=s.cid, device=new, result="ok", id=old)
        self.emit("passkey", device=new, name=s.name)
        await self.send_app(s, {"t": "pk_ok"})
        await self._accept_resume(s, s.since)
        await self._rt_issue(s, force=True)       # P122: the old context's ticket died with it (State.passkey_restore)

    # ------------------------------------------------------------ P122 renewal ticket (PROTOCOL §12.1) — §12's restore, by cookie
    async def _rt_restore(self, s: Session, obj: dict) -> None:
        """A pk session's one message was `rt_restore`: the same checks and the same take-over as `_pk_restore`, with the
        ticket's HMAC proof in place of the WebAuthn assertion; the used ticket is rotated in the same locked write."""
        old = None
        try:
            if not self.pk_guard.attempt():
                raise passkey.PasskeyError("bad", "rate")
            f = renewal.restore_fields(obj)
            if not passkey.ts_ok(f["ts"]):
                raise passkey.PasskeyError("expired", "ts")
            if not self.pk_guard.fresh(f["nonce"]):
                raise passkey.PasskeyError("bad", "replay")
            hit = self.st.ticket_of(f["i"])
            if not hit:
                raise passkey.PasskeyError("unknown", "ticket")
            old, rt = hit
            if not passkey.handle_matches(f["uh"], self.channel, self.kp.pub):
                raise passkey.PasskeyError("bad", "user_handle")
            renewal.verify(rt, f, renewal.restore_challenge(s.pub, s.sign_pub, f["ts"], f["nonce"]))
            new = self.st.ticket_restore(old, f["i"], s.pub, s.sign_pub, renewal.new_ticket(), _iid(obj) or None)
            if not new:
                raise passkey.PasskeyError("unknown", "gone")
        except passkey.PasskeyError as e:
            self.st.log("rt_restore", cid=s.cid, device=old or s.device, result="fail", reason=e.detail)
            return await self._pk_fail(s, e.why)
        if old != new:
            self.st.log("auto_unbind", device=old, reason="ticket")
            await self._drop_device(old, "replaced")
        s.device, s.mode = new, "resume"
        s.name = self._dev_label(new)
        self.st.log("rt_restore", cid=s.cid, device=new, result="ok", id=old)
        self.emit("ticket", device=new, name=s.name)
        await self.send_app(s, {"t": "pk_ok"})
        await self._accept_resume(s, s.since)
        await self._rt_send(s)                      # the rotated ticket, now that the page is ready
        if not self.st.passkey_of_device(new):
            await self._pk_offer(s)                 # no passkey yet: offer it once more (the old context's 「以后再说」 is gone)

    async def _rt_issue(self, s: Session, force: bool = False) -> None:
        """A new renewal ticket for this ready, allowlisted device (replacing its old one), sent to it. On `rt_req` at most
        once a minute per session (a page bug cannot rewrite devices.json in a loop)."""
        if s.state != "ready" or not self.st.is_allowed(s.pub) or (not force and time.monotonic() - s.rt_at < 60):
            return
        if self.st.set_ticket(s.pub, renewal.new_ticket()):
            self.st.log("rt_issue", device=s.device)
            await self._rt_send(s)

    async def _rt_send(self, s: Session) -> None:
        """The device's current ticket (id + secret) to its own ready session — inside Noise only; never logged."""
        rt = (self.st.devices().get(s.device) or {}).get("rt")
        if isinstance(rt, dict) and isinstance(rt.get("i"), str) and isinstance(rt.get("k"), str):
            s.rt_at = time.monotonic()
            await self.send_app(s, {"t": "rt", "i": rt["i"], "k": rt["k"]})

    async def _pk_offer(self, s: Session) -> None:
        """Offer a passkey registration to a ready p33 device whose record has none (after a pairing, or on request)."""
        if not s.p33 or s.state != "ready" or not self.st.is_allowed(s.pub):
            return
        nonce = secrets.token_bytes(32)
        s.pk_offer = (nonce, time.monotonic())
        await self.send_app(s, {"t": "pk_offer", "n": wire.b64u(nonce)})

    async def _pk_reg(self, s: Session, t: str, obj: dict) -> None:
        if not s.p33:
            return
        if t == "pk_offer_req":
            return await self._pk_offer(s)
        offer, s.pk_offer = s.pk_offer, None          # single use, whatever the outcome
        try:
            if not offer or time.monotonic() - offer[1] > passkey.OFFER_TTL:
                raise passkey.PasskeyError("expired", "no_offer")
            rec = passkey.verify_registration(obj, offer[0])
            if not self.st.set_passkey(s.pub, rec):
                raise passkey.PasskeyError("unknown", "gone")
        except passkey.PasskeyError as e:
            self.st.log("pk_reg", cid=s.cid, device=s.device, result="fail", reason=e.detail)
            return await self.send_app(s, {"t": "pk_reg_res", "ok": False, "why": e.why})
        self.st.log("pk_reg", cid=s.cid, device=s.device, result="ok")
        await self.send_app(s, {"t": "pk_reg_res", "ok": True})
        await self.elevate.out.on_ready(s)        # F32: open pickup cards now carry this device's Face ID (`fa`)

    def _observe_view(self) -> dict | None:
        """P192 (B17): the shared session's observe loop for `agentj doctor` — consecutive errors, down, last error type."""
        a = self.agent
        if a is None or not hasattr(a, "_observe_errors"):
            return None
        return {"errors": a._observe_errors, "down": bool(getattr(a, "_observe_down", False)),
                "error": getattr(a, "_observe_error", None)}

    def _bg(self, coro, what: str = "bg") -> asyncio.Task:
        """P142 (B13): a fire-and-forget task held by reference (the loop keeps only a weak one — an unreferenced task can be
        collected mid-flight) whose failure lands in host.log as `bg_task_failed` instead of vanishing."""
        tasks = self.__dict__.setdefault("_bg_tasks", set())
        task = asyncio.get_running_loop().create_task(coro)
        tasks.add(task)

        def done(t: asyncio.Task) -> None:
            tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                self.st.log("bg_task_failed", why=what, exception_type=type(t.exception()).__name__)
        task.add_done_callback(done)
        return task

    # ------------------------------------------------------------ control socket (local, 0600)
    def _touch_seen(self, did: str) -> None:
        with contextlib.suppress(OSError):
            self.st.touch_seen(did)

    def _phone_touch(self) -> None:
        """P292: auto_update.touch (phone activity for the nightly upgrade) takes the config flock and writes a file — never on
        the loop. One at a time; a touch asked for meanwhile runs once after it (the newest time is all that counts)."""
        if getattr(self, "_touching", None) is not None:
            self._touch_again = True
            return
        from . import auto_update

        async def run():
            try:
                while True:
                    self._touch_again = False
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(auto_update.touch, self.st)
                    if not self._touch_again:
                        break
            finally:
                self._touching = None
        self._touching = self._bg(run(), "phone_touch")

    async def _ctl_send(self, w: asyncio.StreamWriter, obj: dict) -> None:
        with contextlib.suppress(Exception):
            w.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            await w.drain()

    def _agent_pids(self) -> set:
        """A4: processes whose descendants are the Agent (the shared session; an owned harness is serve's child anyway)."""
        out = set()
        a = self.agent
        sess = getattr(a, "session", None)
        if isinstance(sess, dict) and isinstance(sess.get("pid"), int):
            out.add(sess["pid"])
        for attr in ("proc", "native"):
            pid = getattr(getattr(a, attr, None), "pid", None)
            if isinstance(pid, int):
                out.add(pid)
        return out

    async def _ctl_gate(self, w: asyncio.StreamWriter, req: dict, pid) -> bool:
        """A4 (P139, ctl_auth): True = run the sensitive command now. Otherwise the answer has been sent."""
        from . import ctl_auth
        cmd = req.get("cmd")
        tok = req.get("claim")
        claims = getattr(self, "ctl_claims", {})
        rec = claims.pop(tok, None) if isinstance(tok, str) else None
        claimed = bool(rec and rec[0] == pid and time.monotonic() < rec[1])
        if claimed and cmd == "resume":
            if gate.is_set(self.st):
                pw = req.get("pass")
                try:
                    if not isinstance(pw, str):
                        raise gate.GateError("wrong")
                    await asyncio.to_thread(gate.verify, self.st, pw)
                except gate.GateError as e:
                    self.st.log("ctl_refused", cmd=cmd, reason="passphrase_" + e.reason)
                    await self._ctl_send(w, {"ok": False, "error": "passphrase", "reason": e.reason})
                    return False
            elif not ctl_auth.has_tty(pid):
                self.st.log("ctl_refused", cmd=cmd, reason="no_terminal")
                await self._ctl_send(w, {"ok": False, "error": "claim_required", "why": "terminal"})
                return False
        if claimed and cmd in ("passphrase_consent", "passphrase_replace") and not ctl_auth.has_tty(pid):
            self.st.log("ctl_refused", cmd=cmd, reason="no_terminal")      # #118: the owner types at a terminal
            await self._ctl_send(w, {"ok": False, "error": "claim_required", "why": "terminal"})
            return False
        if claimed:
            return True
        if cmd == "config_apply":
            await self._ctl_config_from_agent(w, req.get("raw"))
            return False
        if cmd == "mail_cfg":                 # P170: widening mail changes from an Agent → the owner's card
            await self._ctl_mail(w, req, claimed=False)
            return False
        self.st.log("ctl_refused", cmd=cmd, reason="no_claim")
        self.emit("ctl_refused", cmd=cmd)
        await self._ctl_send(w, {"ok": False, "error": "claim_required"})
        return False

    async def _ctl_config_from_agent(self, w: asyncio.StreamWriter, raw) -> None:
        """A4: `agentj config set` from inside an Agent. Ordinary keys apply at once; a security key needs the owner's signed
        approval on the phone (a card now; applied when allowed).
        P318: the request is kept as a per-key patch {key: (present, value)} plus the value each key had when asked. A new
        request touching a key of a still-pending card merges into ONE card (the old card ends `gone`, approvals.log reason
        `superseded`). On allow the patch is replayed on the configuration as it is THEN (unrelated changes meanwhile are
        kept); a security key someone else set to another value meanwhile → nothing applied (never reverts their change)."""
        from . import ctl_auth
        try:
            candidate = preferences.validate(preferences.parse(raw) if isinstance(raw, str) else preferences.read()[1])
        except preferences.ConfigError as e:
            self.st.log("ctl_config_refused", reason="invalid",      # key NAMES only: a schema key, never Agent text
                        keys=e.key if e.key in preferences.SCHEMA else "/")
            return await self._ctl_send(w, e.result())
        old = self.preferences
        keys = ctl_auth.security_changes(old, candidate)
        pend = self.__dict__.setdefault("_cfg_cards", [])
        patch, base = _cfg_patch(old, candidate)
        hit = [c for c in pend if set(c["patch"]) & set(patch)]
        if not keys and not hit:
            async def still_ordinary():           # under config_apply_lock: an owner change meanwhile cannot ride along
                now = preferences.validate(preferences.parse(raw) if isinstance(raw, str) else preferences.read()[1])
                extra = ctl_auth.security_changes(self.preferences, now)
                return {"ok": False, "code": "conflict", "keys": extra, "key": extra[0],
                        "error": "changed while waiting"} if extra else None
            return await self._ctl_send(w, await self.apply_preferences(raw, build=still_ordinary))
        if len(hit) == 1 and all(hit[0]["patch"].get(k) == v for k, v in patch.items()):
            self.st.log("ctl_config_card_repeat", keys=",".join(sorted(patch))[:200])   # identical repeat: the card stays
            return await self._ctl_send(w, self._cfg_pending_reply(hit[0]["keys"]))
        for c in hit:                                 # the same config item asked again: one card, the newest values
            patch, base = {**c["patch"], **patch}, {**c["base"], **base}
            pend.remove(c)
            if not c["gone"].done():
                c["gone"].set_result("superseded")
            self.st.log("ctl_config_card_merged", keys=",".join(sorted(c["patch"]))[:200])
        view = _cfg_apply(old, patch)
        keys = ctl_auth.security_changes(old, view)
        if not keys:                                  # merged back to no security change: the ordinary rest applies now
            res = await self._cfg_replay(patch, base, {})
            self.st.log("ctl_config_card_done", result="merged_ordinary" if res.get("ok") else "merged_ordinary_failed")
            return await self._ctl_send(w, res)
        self.st.log("ctl_config_card", keys=",".join(keys)[:200])
        await self._ctl_send(w, self._cfg_pending_reply(keys))
        self._cfg_card_start(patch, base, keys, view)

    def _config_card(self, raw, candidate: dict, keys: list, old: dict) -> None:
        """P320: a reload of a file the Agent edited with security keys → the same P318 card (one card per config item:
        a pending card for the same keys merges, newest values); applied when allowed, then the harness reloads."""
        from . import ctl_auth
        pend = self.__dict__.setdefault("_cfg_cards", [])
        patch, base = _cfg_patch(old, candidate)
        for c in [c for c in pend if set(c["patch"]) & set(patch)]:
            patch, base = {**c["patch"], **patch}, {**c["base"], **base}
            pend.remove(c)
            if not c["gone"].done():
                c["gone"].set_result("superseded")
            self.st.log("ctl_config_card_merged", keys=",".join(sorted(c["patch"]))[:200])
        view = _cfg_apply(old, patch)
        self._cfg_card_start(patch, base, ctl_auth.security_changes(old, view) or keys, view)

    def _cfg_card_start(self, patch: dict, base: dict, keys: list, view: dict) -> None:
        pend = self.__dict__.setdefault("_cfg_cards", [])
        shown = {k: preferences.get(view, k) for k in keys}    # the card's values; replay must land exactly these
        me = {"patch": patch, "base": base, "keys": keys, "gone": asyncio.get_running_loop().create_future()}
        pend.append(me)

        async def card():
            try:
                ans = await self.ask("agentj config", {"changes": shown}, gone=me["gone"], batch=False)
            finally:
                if me in pend:
                    pend.remove(me)
            if me["gone"].done():                     # an allow that raced the merge applies nothing: say so
                self.st.log("ctl_config_card_done",
                            result="superseded_after_allow" if ans.get("behavior") == "allow" else "superseded")
                return
            if ans.get("behavior") != "allow":
                self.st.log("ctl_config_card_done", result="deny")
                return
            res = await self._cfg_replay(patch, base, shown)
            if res.get("code") == "conflict":
                self.st.log("ctl_config_card_done", result="stale", keys=",".join(res.get("keys") or [])[:200])
                self.agent_notice("配置在等待批准期间已被改过，这次批准没有生效，请重新发起。 / The configuration changed while "
                                  "waiting; this approval was not applied.")
                return
            self.st.log("ctl_config_card_done", result="applied" if res.get("ok") else "failed")
            if res.get("ok") and res.get("reload"):   # P320: the approved text is active — refresh the harness from it (never
                self._reload_runtime()                 # a second read of the file: that one was not on the card)
        task = asyncio.ensure_future(card())
        self._ctl_bg = getattr(self, "_ctl_bg", set())
        self._ctl_bg.add(task)
        task.add_done_callback(self._ctl_bg.discard)

    @staticmethod
    def _cfg_pending_reply(keys: list) -> dict:
        return {"ok": False, "key": keys[0], "code": "owner_approval",
                "error": "a security setting changed from inside an Agent needs the owner's approval: a card "
                         "was sent to the paired phone; nothing changed yet (applied when the owner allows) / "
                         "安全设置需要主人在手机上批准，已发卡片，批准后生效"}

    async def _cfg_replay(self, patch: dict, base: dict, shown: dict) -> dict:
        """P318: put an Agent's config patch on the configuration as it is now. A key already at the asked value is skipped;
        a key someone else changed meanwhile: ordinary → their newer value stays, security → `conflict`, nothing applied.
        The result may change no security key beyond `shown` (what the owner's card showed), and each of those must end at
        exactly the value the card showed. Read → edit → validate → checks → commit all run under config_apply_lock
        (apply_preferences' build callback), so an owner edit meanwhile is never overwritten by stale text."""
        from . import ctl_auth

        def conflict(keys, why):
            keys = sorted(keys)
            return {"ok": False, "code": "conflict", "keys": keys, "key": keys[0] if keys else "/", "error": why}

        async def build():
            cur = dict(preferences.flatten(self.preferences))
            use, clash = {}, []
            for k, want in patch.items():
                now = (k in cur, cur.get(k))
                if now == want:
                    continue
                if now != base.get(k):
                    if _cfg_security(k):
                        clash.append(k)
                    continue
                use[k] = want
            if clash:
                return conflict(clash, "changed while waiting")
            text = await asyncio.to_thread(lambda: preferences.read()[0])
            try:
                for k, (present, v) in use.items():
                    text = preferences.edit(text, k, v, remove=not present)
                final = preferences.validate(preferences.parse(text))
            except (preferences.ConfigError, AssertionError, IndexError, KeyError) as e:
                return e.result() if isinstance(e, preferences.ConfigError) else {"ok": False, "key": "/", "error": "edit failed"}
            extra = set(ctl_auth.security_changes(self.preferences, final)) - set(shown)
            if extra:                                 # e.g. the file holds an unapproved security edit: never ride along
                return conflict(extra, "unapproved security change")
            off = [k for k, v in shown.items() if preferences.get(final, k) != v]
            if off:                                   # what lands must be what the owner saw on the card
                return conflict(off, "changed while waiting")
            return text
        return await self.apply_preferences(build=build)

    async def on_ctl(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        from . import ctl_auth
        from .shared_hook import peer_pid
        info = getattr(w, "get_extra_info", None)
        pid, puid = peer_pid(info("socket")) if info else (None, None)
        try:
            while line := await r.readline():
                req = json.loads(line)
                if not isinstance(req, dict):
                    break
                cmd = req.get("cmd")
                if cmd == "claim":               # A4: one-time claim for a sensitive command on this connection
                    why = ctl_auth.refusal(pid, os.getpid(), self._agent_pids())
                    if why:
                        self.st.log("ctl_refused", cmd="claim", reason=why)
                        self.emit("ctl_refused", cmd="claim", reason=why)
                        await self._ctl_send(w, {"ok": False, "error": "claim_refused", "why": why})
                        continue
                    self.ctl_claims = {t: v for t, v in getattr(self, "ctl_claims", {}).items() if v[1] > time.monotonic()}
                    tok = secrets.token_urlsafe(24)
                    self.ctl_claims[tok] = (pid, time.monotonic() + ctl_auth.CLAIM_TTL)
                    await self._ctl_send(w, {"ok": True, "claim": tok})
                    continue
                if cmd in ctl_auth.SENSITIVE and not await self._ctl_gate(w, req, pid):
                    continue
                if cmd == "opencode_auth":
                    agent = self.agent
                    result = await agent.authentication() if agent and hasattr(agent, "authentication") else {"known": False}
                    fail = getattr(agent, "last_provider_fail", None)     # P59: the last turn's failure class (doctor)
                    await self._ctl_send(w, {"ok": True, **result, **({"last_failure": fail} if fail else {})})
                elif cmd == "opencode_runtime":
                    # Owner-local diagnostic: only boolean state, model ids and meters.
                    from . import main_identity
                    from .agent_opencode import split_model
                    agent = self.agent
                    cfg = getattr(agent, 'cfg', {}) or {}
                    sid = getattr(agent, 'sid', None)
                    cached = getattr(agent, 'identity_sent', {}) or {}
                    identity_current = False
                    with contextlib.suppress(ValueError, OSError):
                        identity_current = cached.get(sid) == main_identity.prompt(cfg)
                    model = getattr(agent, 'session_model', None)
                    model = model if isinstance(model, dict) else {}
                    native_model = model.get('providerID', '') + '/' + model.get('id', '') if all(isinstance(model.get(k), str) for k in ('providerID', 'id')) else None
                    meters = getattr(self, 'meter_state', {}) or {}
                    ctx = meters.get('ctx')
                    ctx = {'used': ctx['used'], 'max': ctx.get('max')} if isinstance(ctx, dict) and type(ctx.get('used')) is int and ctx['used'] >= 0 and (ctx.get('max') is None or type(ctx.get('max')) is int and ctx['max'] > 0) else None
                    windows = meters.get('quota_windows')
                    import math
                    windows = [{k: x[k] for k in ('window', 'used', 'limit', 'pct')} for x in windows if isinstance(x, dict) and x.get('window') in ('weekly', 'daily', 'monthly') and all(type(x.get(k)) in (int, float) and math.isfinite(x[k]) for k in ('used', 'limit', 'pct'))] if isinstance(windows, list) else None
                    await self._ctl_send(w, {'ok': True, 'adapter': type(agent).__name__ if agent else None, 'owned_server': bool(getattr(agent, 'owned_server', False)), 'persist': bool(getattr(agent, 'persist', False)), 'workflow_ceo': bool(cfg.get('_workflow_ceo')), 'language': main_identity.language_of(cfg), 'identity_cached_current': identity_current, 'cfg_model': agent.cur_model() if agent and hasattr(agent, 'cur_model') else None, 'native_session_model': native_model if split_model(native_model) else None, 'meter': {'model': meters.get('model'), 'ctx': ctx, 'quota_windows': windows}})
                elif cmd == "opencode_metadata":
                    agent = self.agent
                    selected = agent.cur_model() if agent and agent.kind == 'opencode' else None
                    prov = await agent._providers() if selected else {}
                    found = False
                    limit = None
                    if isinstance(selected, str) and '/' in selected:
                        pid, mid = selected.split('/', 1)
                        for p in prov.get('providers') or []:
                            models = p.get('models') if isinstance(p, dict) else None
                            if isinstance(p, dict) and p.get('id') == pid and isinstance(models, dict) and mid in models:
                                found = True
                                mm = models[mid]
                                limits = mm.get('limit') if isinstance(mm, dict) else None
                                value = limits.get('context') if isinstance(limits, dict) else None
                                if type(value) is int and value > 0:
                                    limit = value
                    info = await agent._last_assistant() if selected else {}
                    used = agent._ctx(info) if selected else None
                    used = used if type(used) is int and used >= 0 else None
                    actual = info.get('providerID', '') + '/' + info.get('modelID', '') if all(isinstance(info.get(k), str) for k in ('providerID', 'modelID')) else None
                    from .agent_opencode import split_model
                    await self._ctl_send(w, {'ok': True, 'model': selected, 'assistant_model': actual if split_model(actual) else None, 'metadata_present': found, 'context_limit': limit, 'context_used': used})
                elif cmd == "env_present":          # P142 (B14): is a key NAME set in serve's environment? yes/no only
                    name = req.get("name")
                    if isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", name):
                        from . import service
                        try:
                            service.load_launch_binary_env({"key_env": name})
                        except service.ServiceError as e:
                            await self._ctl_send(w, {"ok": False, "error": e.detail or e.reason})
                        else:
                            await self._ctl_send(w, {"ok": True, "present": bool(os.environ.get(name))})
                    else:
                        await self._ctl_send(w, {"ok": False, "error": "bad_name"})
                elif cmd == "telegram_pair":        # P283: a one-time Telegram pairing code → the phone's card only
                    res = await self.telegram_pair(req.get("key_env"), by="terminal")
                    await self._ctl_send(w, res)
                elif cmd == "upgrade_ready":        # P142 (A12): the recovery worker restarts only an idle serve
                    from . import auto_update
                    await self._ctl_send(w, {"ok": True, "ready": not auto_update.busy(self)})
                elif cmd == "upgrade_impact":       # P203: `agentj update apply` asks what a restart now would interrupt
                    await self._ctl_send(w, {"ok": True, "items": self.upg_items("agent" if req.get("caller") == "agent" else None)})
                elif cmd == "upgrade_card":         # P203: … and, when it is not empty, puts the confirmation card on the phones
                    await self._ctl_send(w, {"ok": True, **self.upg_open("agent" if req.get("caller") == "agent" else None)})
                elif cmd == "auto_update_ready":
                    from . import auto_update
                    rec = auto_update.read(self.st)
                    await self._ctl_send(w, {"ok": True, "ready": auto_update.enabled(self.st) and
                        not self.stopped() and not auto_update.busy(self) and
                        time.time() - rec.get("phone_at", time.time()) >= auto_update.IDLE})
                elif cmd == "status":
                    await self._ctl_send(w, {"ok": True, "relay_up": self.relay_up, "relay_state": self.relay_state(), "channel": self.channel,
                                             "relay_last": None if self.relay_up else self.relay_last,   # P317: the host leg's stuck point
                                             "identity_ok": self.identity_ok,
                                             "agent": self.agent.kind if self.agent else None, "agent_status": self.eff_status(),
                                             "agent_said_at": getattr(self, "agent_said_at", 0),
                                             **dict(zip(("agent_turn_start_at", "agent_turn_end_at", "agent_turn_failed"),
                                                        getattr(self, "turn_at", (0.0, 0.0, False)))),
                                             **self._agent_phase(),
                                             "asks": len(self.asks), "estop": self.estop,
                                             "task_running": self.scheduler.current_id,
                                             "friends": self.peers.status_view() if self.peers is not None else None,
                                             "telegram": self.telegram.status_view() if self.telegram is not None else None,
                                             "mail": self.mail.status_view() if self.mail is not None else None,
                                             "seat": self.seat.view(),
                                             "agent_observe": self._observe_view(),
                                             "sessions": [{"device": s.device, "name": s.name, "state": s.state}
                                                          for s in self.sessions.values() if s.state != "new"]})
                elif cmd == "provider_model":
                    from . import slash as provider_slash
                    cfg=self.st.agent_config() or {}
                    applicable=cfg.get('kind')==req.get('kind')
                    model=req.get('model')
                    busy=self.eff_status() == 'waiting'
                    valid=model is None or (isinstance(model,str) and provider_slash.MODEL_RE.fullmatch(model))
                    if busy or not valid:
                        await self._ctl_send(w,{'ok':False,'error':'busy' if busy else 'invalid_model'})
                    else:
                        previous=cfg.get('model')
                        if req.get('write') is True and applicable:
                            self.set_model(model)
                            self.restart_harness('provider')
                        await self._ctl_send(w,{'ok':True,'applicable':applicable,'model':previous})
                elif cmd == "agent_reconnect":      # P147 `agentj doctor --reconnect`: re-attach the shared session now
                    await self._ctl_send(w, await self.agent_reconnect(req.get("session")))
                elif cmd == "ask_open":             # P322 `agentj ask`: the §10.7 card from a shell call, any harness
                    await self._ctl_send(w, await self.cli_asks.open(req))
                elif cmd == "ask_wait":
                    await self._ctl_send(w, await self.cli_asks.wait(req))
                elif cmd == "status_push":          # P306 `agentj doctor --reconnect`, links fine: re-send the status anyway
                    self.push_status()
                    await self._ctl_send(w, {"ok": True, "agent_status": self.eff_status()})
                elif cmd == "harness_hello":        # P155: after a harness switch / roll-back — a fixed host turn, no free text
                    await self._ctl_send(w, self.harness_hello(req))
                elif cmd == "harness_handoff":      # P289: switch.md before a harness switch / carry on from it after
                    await self._ctl_send(w, self.harness_handoff(req))
                elif cmd == "harness_progress":     # P281: the switch / scan job's phase → a fixed line the owner sees
                    await self._ctl_send(w, self.harness_progress(req))
                elif cmd == "harness_job":          # P339: `agentj agent switch` → serve launches the job (semantic fields only)
                    from . import switch_harness
                    await self._ctl_send(w, await asyncio.to_thread(switch_harness.host_job, self.st, req))
                elif cmd == "reload":               # P320 `agentj reload`: an Agent's terminal is not trusted with security keys
                    # trusted = what a claim needs (no Agent ancestry AND a terminal on stdin): `env -i setsid agentj reload`
                    # from an Agent escapes the ancestry check but has no terminal (security review P320 F3)
                    trusted = ctl_auth.refusal(pid, os.getpid(), self._agent_pids()) is None and ctl_auth.has_tty(pid)
                    await self._ctl_send(w, await self.reload_config(trusted=trusted, by="terminal" if trusted else "agent"))
                elif cmd == "agent_restart":        # `agentj agent restart` (P59): the owned harness, before its next turn
                    await self._ctl_send(w, self.restart_harness("cli"))
                elif cmd == "config_apply":
                    await self._ctl_send(w, await self.apply_preferences(req.get("raw")))
                elif cmd == "mail_cfg":             # P170: the owner's terminal (claimed) — applies at once
                    await self._ctl_mail(w, req, claimed=True)
                elif cmd == "mail_status":
                    await self._ctl_send(w, {"ok": True, **(self.mail.view() if self.mail else {})})
                elif cmd == "mail_check":
                    await self._ctl_send(w, await self.mail.check() if self.mail else {"ok": False, "why": "not_running"})
                elif cmd == "mail_notes":           # P182: recipients are checked against the owner's own words (meeting.py)
                    await self._ctl_send(w, await self.mail.send_notes(req.get("to") or [], req.get("subject") or "", req.get("body"),
                                                                     bool(req.get("to_owner"))) if self.mail else {"ok": False, "why": "not_running"})
                elif cmd == "mail_key":
                    await self._ctl_send(w, await self.mail.secret_card() if self.mail else {"result": "refused", "why": "not_running"})
                elif cmd == "mail_expect":          # P206 A: the calling Agent waits for one mail (outside data, only to this caller)
                    await self._ctl_send(w, await self.mail.expect(req) if self.mail else {"ok": False, "why": "not_running"})
                elif cmd == "config_revision":
                    import hashlib
                    revision=hashlib.sha256(json.dumps(self.preferences,sort_keys=True).encode()).hexdigest()
                    await self._ctl_send(w,{"ok":True,"revision":revision})
                elif cmd == "report_view":
                    online, pending = self.report_view()
                    await self._ctl_send(w, {"ok": True, "online": sorted(online), "pending": pending})
                elif cmd == "pair":
                    await self._ctl_pair(r, w, pid)
                    return
                elif cmd == "passphrase_consent":   # #118: a paired remote's signed allow → a one-use ticket
                    await self._ctl_send(w, await self._pass_consent(req, pid))
                elif cmd == "passphrase_replace":   # #118: the new password typed at that terminal, with the ticket
                    await self._ctl_send(w, await self._pass_replace(req, pid))
                elif cmd == "revoke":
                    removed, closed = await self.revoke(str(req.get("device", "")))
                    await self._ctl_send(w, {"ok": removed, "closed": closed})
                elif cmd == "stop":                 # `agentj stop` (the terminal: no signature needed — it only stops)
                    await self.do_estop("terminal", "终端")
                    await self._ctl_send(w, {"ok": True, "estop": self.estop})
                elif cmd == "resume":               # `agentj resume` checked the remote pairing password before calling
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
                            rid = self.scheduler.request(tid, "manual")
                            from . import diskq
                            if not await diskq.aflush():
                                raise tasks.TaskError("busy", "运行记录尚未完成落盘；重试前请核对状态 / Run journal has not settled; inspect state before retrying")
                        except tasks.TaskError as err:
                            await self._ctl_send(w, {"ok": False, "error": err.reason, "detail": err.detail})
                        except OSError:
                            await self._ctl_send(w, {"ok": False, "error": "busy", "detail": "无法保存运行记录 / Could not save run journal"})
                        else:
                            await self._ctl_send(w, {"ok": True, "queued": tid, "run_id": rid})
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
                    if getattr(self, "recall", None):
                        self.recall.bind_history()
                    self.hist_meta_all()
                    await self._ctl_send(w, {"ok": True, "on": self.hist.on})
                elif cmd == "notify":                # P365: the owner's own automation → one page on the phones
                    from . import notify
                    await self._ctl_send(w, await notify.post(self, req, puid))
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
        if getattr(self, "elevate", None) is not None:
            self.elevate.grants.drop_device(did, "unpaired")   # #116: its 10-minute admin approval ends with it
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

    async def _ctl_pair(self, r: asyncio.StreamReader, w: asyncio.StreamWriter, pid=None) -> None:
        if self.seat_refusal():                 # P185: no usable seat → no new remote (checked before relay_down: it is the cause)
            return await self._ctl_send(w, {"ev": "error", "reason": "seat_required"})
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
        # P75: the live 0.16 phone accepts both formats. Smaller QR by default; explicit 0 keeps JSON.
        make = wire.pairing_link if os.environ.get("AGENTJ_PAIR_COMPACT") != "0" else wire.pairing_link_v1
        link = make(self.cfg["web"], self.cfg["relay"], self.channel, self.kp.pub, p.pid, p.psk, int(p.expires))
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
                    code = str(req.get("code", ""))
                    terminal = None   # P176: only `agentj pair` asks for this (the admin page keeps its passphrase rule)
                    if code.strip() and not isinstance(pw, str) and req.get("terminal") is True:
                        terminal = await asyncio.to_thread(self.terminal_code_ok, pid)   # macOS: has_tty runs ps
                        if terminal is False:   # not a human's terminal: keep waiting (the account page can still approve)
                            self.st.log("pair_terminal_refused", device=getattr(self.sessions.get(p.cid), "device", ""))
                            await self._ctl_send(w, {"ev": "code_refused", "why": "terminal"})
                            reader = asyncio.create_task(r.readline())
                            continue
                    res = await self.decide(p, code, pw if isinstance(pw, str) else None, terminal=terminal is True,
                                            retry=req.get("retry") is True)
                    if res.get("ev") in ("pass_wrong", "code_wrong"):   # the device keeps waiting; the human may type again
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
                link_at_send = cloud.read_cloud(self.st)
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
                elif res.kind == "unbound":
                    await self.seat_unbound(link_at_send)
                    wait = SYNC_IDLE
                elif res.kind == "fail":
                    self.st.log("sync_fail", status=res.status)
                    wait = SYNC_EVERY
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), wait)

    async def seat_unbound(self, expected) -> None:
        """A removed paid-seat binding ends that host's existing remote access. Never add/approve a device.

        A stale reply cannot revoke a new link; preserve chat files and native Agent work. The local owner can
        install/rebind normally, without a new agent-created permission lock.
        """
        if not expected or expected.get("via") != "seat":
            return
        with cloud.cloud_lock(self.st):
            current = cloud.read_cloud(self.st)
            if not current or any(current.get(k) != expected.get(k) for k in ("api", "host_id", "linked_at", "via")):
                return
            self.st.cloud_path.unlink()
            devices = list(self.st.devices())
        for did in devices:
            await self.revoke(did)
        self.st.log("seat_unbound", remotes=len(devices))
        self.emit("agent_notice", text=("This seat was unbound; existing remotes were disconnected. Chat history stays on the Agent J host." if self.lang == "en" else "这个席位已解绑，已有遥控器已断开；聊天记录仍在这台Agent J 主机上。"))

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
        `src.k: host` for p33 devices (what the human at the Agent J host said to the phones)."""
        if frm == "notice":
            text = one(self.lang, text)
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
        from . import auto_update
        note = auto_update.take_notice(self.st, self.lang, self.preferences)
        if note:
            self.agent_notice(note)
        from . import claude_inbound
        notice = claude_inbound.default_notice(self.st, self.lang)
        if notice:
            self.agent_notice(notice)  # durable history, also delivered after a reconnect
            claude_inbound.notice_delivered(self.st)
        since = since if isinstance(since, int) and not isinstance(since, bool) and since >= 0 else 0
        if since > self.seq:            # a device that remembers a seq from before this serve started: replay it all
            since = 0
        await self.send_app(s, self._status_msg())
        if s.p33: await self.send_app(s, self.preferences_msg())
        await self.send_app(s, self._estop_msg())
        if s.p33 and self.auth_rescue.needs_dot():       # P258: the red dot reaches a phone that was away
            await self.send_app(s, {"t": "auth_rescue", "view": await asyncio.to_thread(self.auth_rescue.view)})
        if s.p33 and self.quota_resume.items:            # P314 (§32): 「额度用完，预计 HH:MM 自动继续」
            await self.send_app(s, self.quota_resume.msg())
        await self.send_app(s, {"t": "push_key", "k": webpush.b64u(webpush.vapid_public(self._push_key()))})
        if s.p33:                       # §10.5: history pages instead of §8's msg replay; meters, models (§10.10 / §10.11)
            await self.send_app(s, self._meter_msg())
            if self.agent:
                await self.send_app(s, self._models_msg())
                if self.agent.kind == 'opencode':
                    from . import provider_runtime
                    selected = self.agent.cur_model()
                    if isinstance(selected, str) and '/' in selected:
                        provider = selected.split('/', 1)[0]
                        name = provider_runtime.key_name(provider)
                        if name and not provider_runtime.fresh_environment(os.environ).get(name):
                            self._bg(provider_runtime.request_key(self, provider), "request_key")
                        async def quota_initial(pid=provider):
                            windows = await asyncio.to_thread(provider_runtime.probe, pid)
                            self.meter_update(quota_windows=windows)
                        self._bg(quota_initial(), "quota_initial")
            await self.send_app(s, {"t": "hist_meta", **self.hist_metadata()})
            # IDs track insertions, not revisions. A known open page may have completed
            # offline without advancing the phone's cursor; replay the bounded current head.
            turns, _ = self.hist.page(limit=HIST_READY)
            for t in turns:
                await self.send_app(s, {"t": "hist_turn", "epoch": self.hist.epoch, "turn": t})
            known = getattr(s, "hist", None)
            known = known if isinstance(known, dict) and known.get("epoch") == self.hist.epoch else None
            missed = sum(1 for t in turns if t["id"] > known["last"]) if known else 0
            for q in list(self.questions.values()):
                if not q.fut.done():
                    await self.send_app(s, self._question_msg(q))
        else:
            missed = 0
            for e in list(self.backlog):
                if e["seq"] > since:
                    await self.send_app(s, self._render(e, s))
                    missed += since > 0
        # P193 (B9): how many turns a device got back on resume that it had not seen (counts only, never content). The relay
        # is at-most-once: this is the path that makes up for a reply sent while the phone or this host was off the relay.
        if missed:
            self.st.log("backfill", cid=s.cid, device=s.device, n=missed)
            self.emit("backfill", device=s.device, n=missed)
        if s.p33:                       # P150 (§22): an empty list too (a reconnected phone drops stale dots); §8-only phones never had it
            await self.send_app(s, self.busy_msg())
        if s.p33:                       # P169 (§23): the theme (factory = a few bytes; custom = colours + its PNGs)
            if self.theme_cached is None:
                await asyncio.to_thread(self.theme_refresh)
            await self.send_app(s, self.theme_cached)
        for a in list(self.asks.values()):
            if not a.fut.done() and (a.tool != pair_card.TOOL or s.pair1):
                await self.send_app(s, self._ask_msg(a, s))
        for g in list(self.grants.values()):
            if self._grant_live(g):
                await self.send_app(s, self._grant_msg(g))
        if s.p33 and self.peers is not None:      # P71: open friend cards (they survive a serve restart)
            for m in self.peers.ask_msgs():
                await self.send_app(s, m)
        if s.p33 and self._upg_live():            # P203: the open upgrade confirmation card comes back on reconnect
            await self.send_app(s, self._upg_msg())
        await self.elevate.on_ready(s)
        await self.capabilities.on_ready(s)
        with contextlib.suppress(Exception):      # P115: a checklist resumed in the last 6 h comes back on reconnect
            from . import first_run
            rec = await asyncio.to_thread(first_run.read, self.st)
            if first_run.active(rec):
                await self.send_app(s, {"t": "setup_card", "show": True, **first_run.snapshot(rec)})
        # P287: no "log in after a restart" message from here: a host that can send it is already logged in. The phone
        # keeps its own offline line (web/js/offhint.js) from the `plat` / `boot` this ready carried.

    # ------------------------------------------------------------ agent bridge (PROTOCOL §8)
    def harness_hello(self, req: dict) -> dict:
        """P155 (ADR-P155): the 「自报家门」 (or 「切回来了」) turn the harness-switch job asks for. The text is the host's own
        (switch_harness.hello_prompt from kinds + a fixed code); `at` lets the job see the answer (status.agent_said_at)."""
        from . import switch_harness
        if not self.agent or self.stopped():
            return {"ok": False, "error": "no_agent"}
        from . import handoff
        cfg = self._switch_cfg()
        where = handoff.switch_where(cfg) if cfg else None
        text = switch_harness.hello_prompt(self.agent.kind, req, self.lang, where)
        if text is None:
            return {"ok": False, "error": "bad_request"}
        at = time.time()
        self.agent.submit(switch_harness.HelloSend(text))
        self.st.log("harness_hello", agent=self.agent.kind, outcome=req.get("outcome"), handoff=req.get("handoff"))
        return {"ok": True, "at": at}

    def _switch_cfg(self) -> dict | None:
        cfg = getattr(self.agent, "cfg", None)
        if not isinstance(cfg, dict) or not isinstance(cfg.get("dir"), str):
            cfg = self.st.agent_config()
        return cfg if isinstance(cfg, dict) and isinstance(cfg.get("dir"), str) else None

    def harness_handoff(self, req: dict) -> dict:
        """P289: the switch job's two host turns around a harness switch — `write` (the harness being left writes switch.md)
        and `resume` (the new one, introduced, carries on from it). Host text from kinds only (switch_harness.handoff_prompt);
        `at` lets the job see the answer, the job itself checks that the file was really written."""
        from . import handoff, switch_harness
        if not self.agent or self.stopped():
            return {"ok": False, "error": "no_agent"}
        cfg = self._switch_cfg()
        text = switch_harness.handoff_prompt(self.agent.kind, req, cfg, self.lang) if cfg else None
        if text is None:
            return {"ok": False, "error": "bad_request"}
        if req.get("step") == "write":
            handoff.switch_ensure(cfg)
        at = time.time()
        self.agent.submit(switch_harness.HandoffSend(text))
        self.st.log("harness_handoff", agent=self.agent.kind, step=req.get("step"))
        return {"ok": True, "at": at}

    def harness_progress(self, req: dict) -> dict:
        """P281: what a running /switch-harness is doing — 「正在探测 / 正在切换 / 已切回」 — never an empty card. The text is
        the host's own (switch_harness.progress_text from a phase code + kinds). In-turn phases (scan, probe, wait) go on the
        open page as card.progress — the phone shows it after the dancing robot, under any words the Agent already wrote;
        the rest (the turn is over, Agent J restarts) are pages of their own (card.switch = the phase)."""
        from . import switch_harness
        text = switch_harness.progress_text(req, self.lang)
        if text is None:
            return {"ok": False, "error": "bad_request"}
        phase = req["phase"]
        self.st.log("harness_progress", phase=phase)
        if phase in switch_harness.IN_TURN:
            tid = self.cur_turn
            page = self.hist.get(tid) if tid is not None else None
            if not page or page["end"] != "open":
                return {"ok": True, "shown": False}
            card = {k: v for k, v in (page.get("card") or {}).items() if k != "progress"}
            self.hist_update(tid, card={**card, "progress": text})
            return {"ok": True, "shown": True}
        self.hist_add({"k": "sys", "text": ""}, text, "done", card={"switch": phase})
        return {"ok": True, "shown": True}

    def _agent_phase(self) -> dict:
        """P312: the harness-switch job's diagnosis of a silent new Agent — fixed codes only (agent.phase / err_kind)."""
        try:
            return {"agent_phase": self.agent.phase(), "agent_err": self.agent.err_kind()} if self.agent else {}
        except Exception:  # noqa: BLE001 — a diagnosis never breaks `status`
            return {}

    def eff_status(self) -> str:
        if self.stopped():
            return "stopped"
        if not self.agent:
            return "none"
        if any(not a.fut.done() and getattr(a, "parked", False) is not True for a in self.asks.values()) or self._open_question():
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
        if self.telegram is not None and self.telegram.error:   # P129: the phone's status area says it; gone on recovery
            m["tg"] = "down"
        return m

    def telegram_changed(self) -> None:
        """P129: the Telegram channel went down for real (≥3 failures / ≥2 min) or came back: every ready phone gets a fresh
        status (its status area shows / hides the line)."""
        msg = self._status_msg()
        self._post(self._send_ready, lambda s: msg)

    def preferences_msg(self):
        # Preferences contain only pure data; no environment values or sensitive State fields.
        value = dict(self.preferences)
        value = preferences.merge(value, {})
        # Local executable configuration never belongs on the phone.
        value.get("voice", {}).get("tts", {}).pop("command", None)
        value.pop("proxy", None)        # P59: host-only (local proxy addresses / variable names stay on the Agent J host)
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
        """P44 (C6): read-only facts the phone's settings panel shows next to `value` — the Agent J host's program version,
        when its language was last set (A1), the phone-settable keys, and the paired phones (metadata only: id, label,
        paired_at, online now). The safety switches are in `value.agent.*`; the panel shows them read-only with the
        `agentj config …` command."""
        lang, at = preferences.language_state(self.st, self.preferences)
        online = {x.device for x in self.sessions.values() if x.state == "ready" and x.device}
        devs = []
        for did, v in sorted(self.st.devices().items(), key=lambda kv: (kv[1] or {}).get("paired_at", 0) if isinstance(kv[1], dict) else 0):
            if isinstance(did, str) and isinstance(v, dict):
                pa = v.get("paired_at")
                n = device_names(v)   # P179: who named it — the device itself (pairing) or the owner (+ the device's own label)
                devs.append({"id": did, "name": clean_label(n["name"]), "online": did in online,
                             "paired_at": int(pa) if isinstance(pa, (int, float)) and not isinstance(pa, bool) else 0,
                             "named_by": n["named_by"], "self_name": clean_label(n["self_name"] or ""), "label": clean_label(device_label(v))})
        info = {"version": update.__version__, "language_at": at, "settable": list(PREF_SET_KEYS), "devices": devs,
                "limit": MAX_DEVICES}   # P171: 「已用 N / 10」
        info["signed_prefs"] = list(approval_level.KEYS)   # P336: changed only by the signed `approval_set` control
        info["run_cmds"] = list(cmdrun.RUN_CMDS)           # P361 (§34): the names Settings may 「执行」
        info["approval"] = {"level": approval_level.level(self.preferences, self.st),       # P336: what is in force
                            "goal_nocard": approval_level.goal_preauth(self.st, self.preferences) is not None}
        from . import service as _svc
        info["platform"] = _svc.platform()   # P274: the phone shows the iMessage card only for "macos"
        im = getattr(self, "imessage", None)
        if im is not None and info["platform"] == "macos":
            with contextlib.suppress(Exception):
                info["imessage"] = im.brief()
        return info

    async def apply_preferences(self, raw=None, ack=None, language_at=None, build=None):
        """Activate a configuration (raw = new file text to commit; None = the file as it is now). ack(result) is awaited
        before the `preferences` broadcast (pref_res first, C6). language_at = the account's time when the account's
        language is being applied (A1), so it is not reported back as a newer host change.
        build (P318): an async callback run INSIDE config_apply_lock (asyncio.Lock is not re-entrant — never call
        apply_preferences from it) → str = the text to commit instead of raw · dict = refuse with that result, nothing
        changed (config_problem untouched) · None = go on with raw."""
        async with self.config_apply_lock:
            if build is not None:
                built = await build()
                if isinstance(built, dict):
                    return built
                if isinstance(built, str):
                    raw = built
            old = self.preferences
            try:
                candidate = preferences.validate(preferences.parse(raw) if isinstance(raw,str) else preferences.read()[1])
                from . import claude_auth
                await asyncio.to_thread(claude_auth.require_transition, self.st, old, candidate, running=True)
                # Providers and channels with external dependencies are checked before activation.
                from . import voice
                from . import service
                try:
                    service.load_launch_binary_env(candidate)
                except service.ServiceError as e:
                    raise preferences.ConfigError("/", e.detail or e.reason) from None
                voice.validate_runtime(candidate, environment="service")
                if isinstance(raw,str):
                    preferences.commit_files(self.st,raw,candidate)
                else:
                    preferences.save_good(self.st,candidate)
                self.preferences = candidate
                self.ask_ttl = min(ASK_TTL, preferences.get(candidate, "approval.timeout"))
                self.config_problem = None
                self._approvals_hot(old, candidate)
                for k in approval_level.KEYS:          # P336 N4: a value written any other way voids that key's signature
                    if preferences.get(old, k) != preferences.get(candidate, k) and k != self.__dict__.get("_approval_set_key"):
                        approval_level.drop_signer(self.st, k)
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
            # P320: only RESTART_SERVE_KEYS still need serve restarted; RELOAD_KEYS wait for a reload (the phone offers it)
            restart = any(preferences.get(old, key) != preferences.get(candidate, key) for key in RESTART_SERVE_KEYS)
            reload_keys = [key for key in RELOAD_KEYS if preferences.get(old, key) != preferences.get(candidate, key)]
            self.reload_pending.update(reload_keys)
            # P59 (A167): proxy settings reach only harness children, read at their launch → restart the owned harness
            # before its next turn (conversation kept), not serve. A shared/attached harness keeps its own environment.
            proxied = any(preferences.get(old, "proxy." + k) != preferences.get(candidate, "proxy." + k)
                          for k in ("https", "http", "no_proxy", "https_env", "http_env", "no_proxy_env"))
            harness_note = self.restart_harness("proxy") if proxied else None
            if self.agent_cfg is not None:   # F14: the next Agent process starts with the new isolation / docker choice
                raw = self.st.config().get("agent") or {}
                self.agent_cfg["fence"] = raw.get("fence") is not False and preferences.get(candidate, "agent.isolation", True) is not False
                self.agent_cfg["docker"] = raw.get("docker") is True or preferences.get(candidate, "agent.allow_docker", False) is True
            # P116 (B11): the private instructions file and shared identity are hot — the next turn reloads (never mid-turn)
            for key, ck in (("agent.private_instructions_file", "private_instructions_file"), ("agent.shared_identity", "shared_identity")):
                if preferences.get(old, key) != preferences.get(candidate, key):
                    for c in (self.agent_cfg, getattr(self.agent, "cfg", None) if self.agent is not None else None):
                        if c is not None:
                            c[ck] = preferences.get(candidate, key)
                    if self.agent is not None and not self.agent.cfg.get("_workflow_ceo"):
                        self.agent.identity_changed()
            result = {"ok": True, "applied": not restart and not reload_keys,
                      "verify": {"ok": True, "detail": "stored; restart serve for the working root or session mode" if restart else
                                 "stored; run agentj reload to activate" if reload_keys else "host configuration activated; voice needs a listening test"},
                      "needs": (["restart serve"] if restart else []) + (["reload"] if reload_keys else [])}
            if reload_keys:
                result["reload"] = reload_keys
            if harness_note is not None:
                result["harness"] = harness_note
                if harness_note.get("when") == "own":
                    result["needs"] = result["needs"] + ["restart the shared harness yourself"]
            if ack:
                await ack(result)
            await self._send_ready(lambda _: self.preferences_msg())
            # P142: the owner's Claude settings (flock + backup + fsync + rename) in a thread, after the answer
            self.configure_claude_inbound(await asyncio.to_thread(self._claude_inbound_files))
            return result

    def configure_claude_inbound(self, failed: bool | None = None) -> None:
        from . import claude_inbound
        if failed is None:
            failed = self._claude_inbound_files()
        try:
            if failed:
                raise claude_inbound.SettingsError("files")
            if any(s.state == "ready" for s in self.sessions.values()):
                notice = claude_inbound.default_notice(self.st, self.lang)
                if notice:
                    self.agent_notice(notice)
                    claude_inbound.notice_delivered(self.st)
        except (OSError, claude_inbound.SettingsError):
            self.agent_notice("Could not enable Claude phone messages; check Claude settings and run `agentj config claude-inbound on`."
                              if self.lang == "en" else
                              "未能开启 Claude 手机消息；请检查 Claude 设置并运行 agentj config claude-inbound on。")

    def _claude_inbound_files(self) -> bool:
        """The owner's Claude settings writes (backup + fsync + rename): True = failed. P142: the loop runs it in a thread."""
        from . import claude_inbound, claude_statusline
        claude_statusline.ensure_default(self.st)   # P127: status-line tap default (never raises; doctor reports)
        try:
            claude_inbound.ensure_shared_default(self.st)
            return False
        except (OSError, claude_inbound.SettingsError):
            return True

    async def agent_reconnect(self, session=None) -> dict:
        """P147: the shared Claude adapter re-checks its session (heal / the named one); others report their status."""
        a = self.agent
        if a is None:
            return {"ok": False, "why": "no_agent", "agent_status": self.eff_status()}
        fn = getattr(a, "reconnect", None)
        if fn is None:
            return {"ok": a.status != "down", "why": "unsupported", "agent": a.kind, "agent_status": self.eff_status()}
        sid = session if isinstance(session, str) and re.fullmatch(r"[0-9A-Fa-f-]{8,64}", session) else None
        try:
            res = await asyncio.wait_for(fn(sid), 15)
        except asyncio.TimeoutError:
            res = {"ok": False, "why": "timeout"}
        res.update(agent=a.kind, agent_status=self.eff_status())
        self.push_status()                  # P306: the phones see the answer at once, not at the next turn
        self.st.log("agent_reconnect", result="ok" if res.get("ok") else str(res.get("why"))[:16])
        return res

    def restart_harness(self, why: str) -> dict:
        """P59 (A167): restart the harness process Agent J owns before its next turn, the conversation kept (Claude Code
        --resume, Codex thread, OpenCode session id). Never mid-turn (the main Agent may be the one asking), never a
        shared/attached harness (the owner's own process)."""
        a = self.agent
        if a is None:
            return {"ok": True, "when": "next_start"}
        if not a.owns_harness():
            return {"ok": False, "when": "own", "error": "shared"}
        return {"ok": True, "when": a.request_restart(why)}

    async def reload_config(self, trusted: bool = False, by: str = "cli") -> dict:
        """P320 `agentj reload` (also the phone's /reload, ≡ Reload, Alt+Shift+L; run by itself after an Agent's `agentj config
        set` of a reload-tier key): make the configuration file and the service's key file effective WITHOUT restarting serve.
        1) the private service environment file is read again (key names only; the Telegram bot key added tonight) ·
        2) the configuration file is activated like `agentj config apply` — a security key changed in the file by an Agent
        (trusted=False: the phone, an Agent's terminal) goes to the owner's approval card instead · 3) the Agent settings read
        at harness launch (instructions, isolation, docker, warnings, approvals) are refreshed and the owned harness restarts
        before its next turn, the conversation kept · 4) the Telegram channel binds again with the new key. RESTART_SERVE_KEYS
        changed since serve started are named, never applied half-way. Never prints or returns a value."""
        from . import service, ctl_auth
        out = {"ok": True, "changed": [], "pending": [], "restart_serve": [], "env_changed": 0, "harness": None}
        try:
            env = await asyncio.to_thread(service.reload_service_env, self.preferences)
        except service.ServiceError as e:
            env, out["env_error"] = [], e.reason
        out["env_changed"] = len(env)
        try:
            raw, doc = preferences.read()            # one read: the text checked = the text on the card = the text committed
            candidate = preferences.validate(doc)
        except (preferences.ConfigError, OSError) as e:
            res = e.result() if isinstance(e, preferences.ConfigError) else {"ok": False, "key": "/", "error": "configuration unreadable"}
            self.st.log("reload", result="config_error", by=by)
            return {**out, **res, "ok": False}
        old = self.preferences
        a, b = ctl_auth._flat(old), ctl_auth._flat(candidate)
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        sec = ctl_auth.security_changes(old, candidate) if changed else []
        if sec and not trusted:
            self.st.log("ctl_config_card", keys=",".join(sec)[:200])
            self._config_card(raw, candidate, sec, old)
            out["pending"] = sec
        elif changed:
            res = await self.apply_preferences(raw)
            if not res.get("ok"):
                self.st.log("reload", result="rejected", by=by)
                return {**out, **res, "ok": False}
            out["changed"] = changed
        if not out["pending"]:
            out["harness"] = self._reload_runtime(bool(env))
        if env and self.telegram is not None:
            self.telegram.bot_id = None           # the next poll binds with the key as it is now (getMe)
        out["restart_serve"] = [k for k in RESTART_SERVE_KEYS
                                if preferences.get(self.boot_preferences, k) != preferences.get(self.preferences, k)]
        out["text"] = self.reload_text(out)
        self.st.log("reload", result="pending" if out["pending"] else "ok", by=by, status=f"{len(out['changed'])}/{len(env)}")
        self.push_status()
        return out

    def _reload_runtime(self, env_changed: bool = False) -> dict | None:
        """P320: the active configuration → the running adapter (launch fields) + the owned harness restarts before its next
        turn when anything it reads at launch changed. → restart_harness's answer, or None when nothing needed it."""
        refreshed = self._reload_agent_settings()
        note = None
        if refreshed or env_changed or self.reload_pending:
            note = self.restart_harness("reload") if self.agent is not None else {"ok": True, "when": "next_start"}
        self.reload_pending.clear()
        return note

    def _approvals_hot(self, old: dict, new: dict) -> None:
        """P336: a change of approval.level alone reaches the shared hooks at once (they read cfg live). A change that also
        touches agent.approvals, or crosses `native` (the shared Codex guard is installed at spawn), waits for a reload."""
        if preferences.get(old, approval_level.KEY) == preferences.get(new, approval_level.KEY) or \
                preferences.get(old, "agent.approvals") != preferences.get(new, "agent.approvals"):
            return
        was, now = approval_level.shared_mode(old), approval_level.shared_mode(new)
        if "native" in (was, now) and was != now:
            self.reload_pending.add(approval_level.KEY)
            return
        for c in (self.agent_cfg, getattr(self.agent, "cfg", None) if self.agent is not None else None):
            if c is not None and "approvals" in c:
                c["approvals"] = now

    def _reload_agent_settings(self) -> list:
        """P320: the Agent settings read when the harness starts, refreshed into the running adapter's cfg from the ACTIVE,
        validated self.preferences — never the file on disk (an Agent's unapproved edit must not reach the harness; security
        review F1). → the fields that changed (instructions also reload the identity text)."""
        if self.agent_cfg is None:
            return []
        prefs, raw = self.preferences, (self.st.config().get("agent") or {})
        fresh = {"instructions": preferences.get(prefs, "agent.instructions", ""),
                 "high_risk_warnings": preferences.get(prefs, "agent.high_risk_warnings", False),
                 "approvals": approval_level.shared_mode(prefs),   # P336: safe level → "safe"; else agent.approvals
                 "fence": raw.get("fence") is not False and preferences.get(prefs, "agent.isolation", True) is not False,
                 "docker": raw.get("docker") is True or preferences.get(prefs, "agent.allow_docker", False) is True}
        fields = [k for k in ("instructions", "high_risk_warnings", "approvals", "fence", "docker")
                  if self.agent_cfg.get(k) != fresh.get(k) or (self.agent is not None and getattr(self.agent, "cfg", None) is not None
                                                                and self.agent.cfg is not self.agent_cfg and self.agent.cfg.get(k) != fresh.get(k))]
        for k in fields:
            for c in (self.agent_cfg, getattr(self.agent, "cfg", None) if self.agent is not None else None):
                if c is not None:
                    c[k] = fresh.get(k)
        if "instructions" in fields and self.agent is not None and not self.agent.cfg.get("_workflow_ceo"):
            self.agent.identity_changed()
        return fields

    def reload_text(self, r: dict) -> str:
        """The one card / terminal line of a reload, in the owner's language (names of settings only, never values)."""
        en = self.lang == "en"
        parts = []
        n = len(r.get("changed") or [])
        parts.append((f"Reloaded: {n} setting{'s' if n != 1 else ''} now in effect." if n else "Reloaded: the configuration had no new changes.")
                     if en else (f"已重新加载：{n} 项设置已生效。" if n else "已重新加载：配置没有新的改动。"))
        if r.get("env_changed"):
            k = r["env_changed"]
            parts.append(f"{k} key{'s' if k != 1 else ''} from the service key file updated." if en else f"服务密钥文件里 {k} 个 key 已更新。")
        if r.get("env_error"):
            parts.append("The service key file could not be read (it must be yours, mode 600); keys unchanged."
                         if en else "服务密钥文件读不了（须属于你、权限 600），key 没有变。")
        h = r.get("harness") or {}
        if h.get("when") == "next_turn":
            parts.append("The main Agent restarts before its next message, same conversation." if en else "主 Agent 会在下一条消息前重启，对话接着原来的。")
        elif h.get("when") == "own":
            parts.append("The shared session is your own program: restart it yourself to pick up Agent settings."
                         if en else "共享会话是你Agent J 主机上自己的程序：Agent 设置要你自己重启它才生效。")
        if r.get("pending"):
            keys = ", ".join(r["pending"][:6])
            parts.append(f"Security settings ({keys}) wait for your approval on the phone card." if en else f"安全设置（{keys}）要你在手机卡片上批准后才生效。")
        if r.get("restart_serve"):
            keys = ", ".join(r["restart_serve"])
            parts.append((f"{keys} still needs a service restart:" if en else f"{keys} 仍需重启后台服务才生效：") + "\n```sh\nagentj service restart\n```\n")
        return " ".join(parts)

    def language_changed(self, lang, from_account: bool = False) -> None:
        """A1: the one language changed. The main Agent speaks it from its next turn (identity text, hot); the account
        learns a local change with the next report (soon: triggered here)."""
        self.lang = "en" if lang == "en" else "zh"
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
        self._bg(self.set_pref("appearance.language", account["language"], language_at=account["language_at"]), "language_sync")

    async def on_pref_set(self, s: Session, obj: dict) -> None:
        """C6 `pref_set {r, key, value}` from a ready, paired p33 session (E2E). Whitelist PREF_SET_KEYS only; the value
        is checked against the schema (type / enum) before anything is written. → `pref_res {r, ok, key, problem?}`,
        then the normal `preferences` broadcast."""
        r, key, value = obj.get("r"), obj.get("key"), obj.get("value")
        if not wire.is_rid(r):
            return
        key_out = key if isinstance(key, str) and len(key) <= 64 else None

        async def res(ok: bool, problem: str | None = None, reload: bool = False) -> None:
            m = {"t": "pref_res", "r": r, "ok": ok, "key": key_out}
            if problem:
                m["problem"] = problem
            if reload:                                # P320: saved, takes effect after a reload — the phone offers 「Reload」
                m["reload"] = True
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, m)
        if key not in PREF_SET_KEYS:
            self.st.log("pref_set_refused", device=s.device)
            return await res(False, "not_allowed")
        if isinstance(key, str) and key.startswith("imessage."):
            from . import service as _svc
            if _svc.platform() != "macos":           # P274: only a Mac has chat.db; the card is not shown elsewhere
                return await res(False, "not_macos")
        meta = preferences.SCHEMA[key]
        # P207: "integer" (heartbeat.interval_minutes) was missing here, so every phone change of it answered bad_value
        typ_ok = {"boolean": type(value) is bool, "string": isinstance(value, str),
                  "number": type(value) in (int, float) and math.isfinite(value),
                  "integer": type(value) is int,
                  # P340: the one phone-settable list — the composer buttons' order, a permutation of the four ids
                  "array": key == "ui.composer_buttons" and preferences.composer_order_ok(value)}.get(meta["type"], False)
        if not typ_ok or ("enum" in meta and value not in meta["enum"]) or (meta["type"] in ("number", "integer") and not (
                meta.get("minimum", -math.inf) <= value <= meta.get("maximum", math.inf) and (not meta.get("multipleOf") or abs(
                    value / meta["multipleOf"] - round(value / meta["multipleOf"])) < 1e-6))):
            return await res(False, "bad_value")
        # P207: strings also obey the schema's maxLength / pattern (heartbeat.daily_at "HH:MM" or ""), answered bad_value
        if meta["type"] == "string" and (len(value) > meta.get("maxLength", 1000) or any(ord(c) < 32 for c in value) or (
                meta.get("pattern") and not re.fullmatch(meta["pattern"], value))):
            return await res(False, "bad_value")
        if preferences.get(self.preferences, key) == value:
            return await res(True)

        async def ack(result: dict) -> None:
            await res(bool(result.get("ok")), None if result.get("ok") else
                      ("busy" if result.get("error") == "busy" else "rejected"), reload=bool(result.get("reload")))
            if not result.get("ok") and key == "agent.session_mode" and str(result.get("error", "")).startswith("Agent J 主机上的服务没有 Claude 登录"):
                from . import claude_auth
                self.agent_notice(claude_auth.failure_notice(self.lang))
        self.st.log("pref_set", device=s.device, status=key)
        await self.set_pref(key, value, ack=ack)
        if key.startswith("imessage.") and getattr(self, "imessage", None) is not None:
            self.imessage.kick()                     # P274: a switch / interval change takes effect at once

    async def on_dev_rename(self, s: Session, obj: dict) -> None:
        """P179 `dev_rename {r, name}` (§10.16a): a paired remote renames ITSELF from its message card (long-press on the
        source label). Only its own record — there is no target field; other remotes are renamed on the account page.
        Same writer, audit and owner_name field as the account page's device_name. → `dev_rename_res {r, ok, name?, why?}`."""
        r, name = obj.get("r"), obj.get("name")
        if not wire.is_rid(r):
            return

        async def res(ok: bool, **kw) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "dev_rename_res", "r": r, "ok": ok, **kw})
        # the same rules as the account page (1–64, no control characters), then the host's label cleaning
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            return await res(False, why="bad_name")
        name = clean_label(name.strip()).strip()
        if not name:
            return await res(False, why="bad_name")
        try:
            ok = self.st.rename_device(s.device, name, via="phone")
        except ValueError:
            return await res(False, why="bad_name")
        if not ok:
            return await res(False, why="gone")
        digest = hashlib.sha256(json.dumps({"device": s.device, "name": name}, sort_keys=True).encode()).hexdigest()
        approvals.record(self.st, rid=r, agent="phone", tool="device_name", input_sha256=digest, shown_sha256=digest,
                         decision="allow", reason="own_device", device=s.device)
        self.activity("decision", device=s.device, summary="device_name / own device: ok", request=r)
        self.st.log("device_renamed", device=s.device, via="phone")
        self.device_renamed(s.device)
        await res(True, name=name)

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

    def busy_msg(self) -> dict:
        """PROTOCOL §22: `{"t":"busy","tasks":[{id, who, kind, label, lk?, since, c}],"by":<Agent name>,"load"?:0..1,"cap"?:n}`
        — display only (load: P154, the line under the reply card's header; cap: 追加 11, how many robots fit)."""
        msg = self.busy.msg(self.st.agent_name())
        # Resolve only a declared folder / actual cwd / exact named employee in the last paired catalogue scan.
        if self.wf.last:
            from .workflows import task_assignment
            for task in msg["tasks"]:
                task.update(task_assignment(task, self.wf.last))
        for task in msg["tasks"]:
            task.pop("cwd", None)   # local matching only; no absolute path on the list
        return msg

    def busy_changed(self) -> None:
        """P150: the 「在办事项」 list changed (or may have: TTL) → every ready phone, once per real change."""
        msg = self.busy_msg()
        key = json.dumps([msg["tasks"], msg["by"], msg.get("load"), msg.get("cap")], ensure_ascii=False, sort_keys=True)
        if key == self.busy_sent:
            return
        self.busy_sent = key
        self._post(self._send_p33, lambda s: msg)

    async def busy_loop(self) -> None:
        """P150: item files every 2 s, systemd `agentj-p*` units every 10 s (both off the event loop); colours saved.
        P154: the load is sampled every busy.LOAD_EVERY s and sent only when it moved by more than busy.LOAD_STEP."""
        from . import busy as busy_mod
        n = 0
        while True:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.busy.refresh_outside, n % 5 == 0)
                self.background_done.tick()
                await asyncio.to_thread(self.busy.sample_load)   # P331: load / memory probes (vm_stat …) never on the loop
                self.busy_changed()
                colors = self.busy.take_colors()
                if colors is not None:
                    await asyncio.to_thread(busy_mod.save_colors, self.st.root, colors)
            with contextlib.suppress(Exception):
                if await asyncio.to_thread(self.theme_refresh):
                    msg = self.theme_cached
                    self._post(self._send_p33, lambda s: msg)
            n += 1
            await asyncio.sleep(2)

    # ------------------------------------------------------------ P181 (§22.1 / §22.2): the load card, the header rename
    def on_res_watch(self, s: Session, obj: dict) -> None:
        """The page's load card is open (`on: true`, renewed every resources.RENEW s) or closed. Sampling runs only while at
        least one ready session watches, and stops by itself when the last watch ends or expires (resources.WATCH_TTL)."""
        from . import resources
        if obj.get("on") is True:
            self.res_watch[s.cid] = time.monotonic() + resources.WATCH_TTL
            if self.res_task is None or self.res_task.done():
                self.res_task = self._bg(self.res_loop(), "res")
        else:
            self.res_watch.pop(s.cid, None)

    def _res_watchers(self) -> list:
        now = time.monotonic()
        for cid in [c for c, exp in self.res_watch.items() if exp < now or c not in self.sessions]:
            self.res_watch.pop(cid, None)
        return [s for c in self.res_watch if (s := self.sessions.get(c)) is not None and s.state == "ready" and s.p33
                and self.st.is_allowed(s.pub)]

    async def res_loop(self) -> None:
        """Sample (off the event loop) → `{"t":"res",…}` to the watching sessions only; a fresh Sampler per run, so nothing is
        kept between two openings of the card."""
        from . import resources
        sampler = await asyncio.to_thread(resources.Sampler)
        await asyncio.to_thread(sampler.sample)             # the baseline for the rates
        await asyncio.sleep(resources.FIRST)
        while True:
            watchers = self._res_watchers()
            if not watchers:
                return
            msg = resources.message(await asyncio.to_thread(sampler.sample), self.busy.load)
            for s in watchers:
                await self.send_app(s, msg)
            await asyncio.sleep(resources.EVERY)

    async def on_agent_name(self, s: Session, obj: dict) -> None:
        """A signed `agent_name` (controls action, object = the name as typed) → names.rename, the same path as `agentj name`
        (linked: the Dashboard first; the short-name rule). Any ready paired remote is the owner's (friends never get one)."""
        from . import names
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        name = obj.get("name")
        if not isinstance(name, str) or len(name) > 200:
            return await self._ctl_res(s, r, "agent_name", False, why="shape")
        target = {"name": name}
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "agent_name", target)
        if why:
            self.st.log("control_refused", device=s.device, action="agent_name", reason=why)
            controls.log(self.st, action="agent_name", device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, "agent_name", False, why=why)
        try:
            res = await asyncio.to_thread(names.rename, self.st, name, notify=False)
        except OSError:
            res = {"ok": False, "error": "failed", "suggestions": []}
        controls.log(self.st, action="agent_name", device=s.device, result="ok" if res["ok"] else res["error"], obj=target,
                     sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
        if not res["ok"]:
            return await self._ctl_res(s, r, "agent_name", False, why=res["error"],
                                       suggestions=[x for x in res.get("suggestions", []) if isinstance(x, str)][:5])
        self.activity("decision", by=s.name or s.device, result="allow", tool=pick(self.lang, "Agent 改名", "Agent renamed"),
                      text=res["name"])
        self.reporter.trigger("agent_name")
        self.name_changed()
        await self._ctl_res(s, r, "agent_name", True, name=res["name"])

    async def on_mail_allow(self, s: Session, obj: dict) -> None:
        """P207 (§25): a signed `mail_allow {r, addr, role, n, ts, sig}` (controls action, object = addr \n role) → the
        address joins the P170 e-mail channel's allowlist with that role (owner | guest), through Mail.set_config like the
        phone's mail_set. Works before the mailbox itself is set up (the allowlist is only config). Already on the list
        (case-insensitive) → ok with `already: true`, nothing changed. → `ctl_res {r, action: "mail_allow", ok, already?, why?}`."""
        from . import mail as mail_mod
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        addr, role = obj.get("addr"), obj.get("role")
        if not isinstance(addr, str) or len(addr) > 254 or role not in mail_mod.ROLES:
            return await self._ctl_res(s, r, "mail_allow", False, why="shape")
        target = {"addr": addr, "role": role}
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "mail_allow", target)
        if why:
            self.st.log("control_refused", device=s.device, action="mail_allow", reason=why)
            controls.log(self.st, action="mail_allow", device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, "mail_allow", False, why=why)
        res: dict = {}
        if self.mail is None:
            res = {"why": "no_mail"}
        else:
            try:
                norm = mail_mod.norm_addr(addr)
                cfg = await asyncio.to_thread(mail_mod.read_config, self.st)
                if any(str(x.get("addr", "")).lower() == norm.lower() for x in cfg.get("allow") or []):
                    res = {"already": True}
                else:
                    new = dict(cfg, allow=[*cfg.get("allow", []), {"addr": norm, "role": role}])
                    cfg = self.mail.set_config(new)    # on the loop, like mail_set (it adds a page and wakes the poller)
            except mail_mod.MailError as e:
                res = {"why": e.why}
        ok = "why" not in res
        controls.log(self.st, action="mail_allow", device=s.device, result=("already" if res.get("already") else "ok") if ok
                     else res["why"], obj=target, sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
        self.st.log("mail_allow", device=s.device, role=role, result=("already" if res.get("already") else "ok") if ok
                    else res["why"])
        if ok and not res.get("already"):
            self.activity("decision", by=s.name or s.device, result="allow",
                          tool=pick(self.lang, "邮件白名单", "Mail allowlist"), text=role)
        await self._ctl_res(s, r, "mail_allow", ok, **res)

    async def on_approval_set(self, s: Session, obj: dict) -> None:
        """P336: a signed `approval_set {r, key, value, n, ts, sig}` (controls action, object = key \n value) → the key is
        written like `agentj config set` (set_pref), the signer is remembered (approval_level.note_signer: the goal switch
        only counts while that remote stays paired), approvals.log gets the control line, every remote a notice, a security
        push. → `ctl_res {r, action: "approval_set", ok, why?}`. The keys are not in PREF_SET_KEYS: an unsigned pref_set is
        refused, an Agent's `agentj config set approval.*` stays an owner card."""
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        key, value = obj.get("key"), obj.get("value")
        if not ((key == approval_level.KEY and value in approval_level.LEVELS)
                or (key == approval_level.GOAL_KEY and type(value) is bool)):
            return await self._ctl_res(s, r, "approval_set", False, why="shape")
        target = {"key": key, "value": value}
        # P122 / P336: widening (to full, the goal switch on) also needs Face ID when this remote has a passkey — two steps:
        # without `fa` the host answers passkey_required + the challenge over this very request (nonce untouched), the
        # phone asks Face ID and sends the same signed request again with `fa`.
        widen = (key == approval_level.KEY and value == "full") or (key == approval_level.GOAL_KEY and value is True)
        pk = self.st.passkey_of_device(s.device) if widen else None
        ch = None
        if pk:
            n = obj.get("n")
            if not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
                return await self._ctl_res(s, r, "approval_set", False, why="shape")
            ch = passkey.elevate_challenge(self.channel, s.device, n, "approval_set", n,
                                           controls.object_digest("approval_set", target))
            if obj.get("fa") is None:
                return await self._ctl_res(s, r, "approval_set", False, why="passkey_required", fa=pk["id"],
                                           fa_ch=wire.b64u(ch))
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "approval_set", target)
        if not why and pk:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        if why:
            self.st.log("control_refused", device=s.device, action="approval_set", reason=why)
            controls.log(self.st, action="approval_set", device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, "approval_set", False, why=why)
        rid, sk = secrets.token_hex(16), self.st.sign_key(s.device)
        text = controls.object_text("approval_set", target)
        try:                                    # the signer first: the `preferences` broadcast below shows the effective value
            await asyncio.to_thread(approval_level.note_signer, self.st, key, value, s.device, sk, rid,
                                    {"n": obj["n"], "ts": obj["ts"], "sig": obj["sig"]})
        except Exception as e:  # noqa: BLE001 — logged; full / the goal switch then simply do not count
            bg.swallowed(self.st.log, "approval_signer_failed", e, every=0)
        self._approval_set_key = key            # apply_preferences keeps this key's signer (N4: any other write drops it)
        try:
            res = {"ok": True} if preferences.get(self.preferences, key) == value else await self.set_pref(key, value)
        finally:
            self._approval_set_key = None
        ok = bool(res.get("ok"))
        controls.log(self.st, action="approval_set", device=s.device, result="ok" if ok else "rejected", obj=target,
                     sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
        if not ok:
            await asyncio.to_thread(approval_level.drop_signer, self.st, key)
            return await self._ctl_res(s, r, "approval_set", False, why="rejected")
        digest = hashlib.sha256(text.encode()).hexdigest()
        with contextlib.suppress(Exception):
            approvals.record(self.st, rid=rid, agent="phone", tool="approval_set", input_sha256=digest, shown_sha256=digest,
                             decision="allow", reason="device", device=s.device, sign_pub=sk, sig=wire.unb64u(obj["sig"]),
                             control={"n": obj["n"], "sig_ts": obj["ts"],
                                      "object_sha256": controls.object_digest("approval_set", target)})
        await self._send_ready(lambda _: self.preferences_msg())   # the effective level / switch (host.approval)
        self.st.log("approval_set", device=s.device, key=key, status=str(value))
        if key == approval_level.GOAL_KEY and value is False:
            d = goal_mod.load(self.st)          # a goal that started without a card ends with the switch that allowed it
            if d and d.get("state") == "active" and d.get("preauth"):
                self.goals.end("stopped", by="switch")
        if key == approval_level.KEY:
            self._unsigned_full_told = False
        by = s.name or self._dname(s.device) or s.device
        self.activity("decision", by=by, result="allow", tool=pick(self.lang, "审批设置", "Approval setting"), text=text[:80])
        self.agent_notice(approval_level.changed_text(key, value, by, self.lang))
        self.push_notify("security")
        await self._ctl_res(s, r, "approval_set", True, key=key, value=value)

    def theme_refresh(self) -> bool:
        """P169: re-read the theme when <state>/agentperm/theme/current.json changed (`agentj theme apply|reset|restore`,
        also from inside the Agent's fence) → True when the phones need the new one. Off the event loop (PNG checks)."""
        from . import theme
        st = theme.stamp(self.st.root)
        if self.theme_cached is not None and st == self.theme_stamp:
            return False
        old = self.theme_cached
        self.theme_stamp, self.theme_cached = st, theme.phone_message(self.st.root)
        return old is not None and old.get("rev") != self.theme_cached.get("rev")

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
            self._bg(coro_fn(*args), "post_now")

    async def post_loop(self) -> None:
        while True:
            fn, args = await self.post_q.get()
            try:
                await fn(*args)
            except Exception as e:  # noqa: BLE001 — a send to a vanished session costs nothing else (P192: but is logged)
                bg.swallowed(self.st.log, "post_failed", e, why=getattr(fn, "__name__", "post"))

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
    def hist_metadata(self):
        return {**self.hist.meta(), "read": self.readmark.value(self.hist)}

    def hist_emit(self, turns: list[dict]) -> None:
        ep = self.hist.epoch
        for t in turns:
            self._post(self._send_p33, lambda s, t=t: {"t": "hist_turn", "epoch": ep, "turn": t})

    def hist_meta_all(self) -> None:
        m = {"t": "hist_meta", **self.hist_metadata()}
        self._post(self._send_p33, lambda s: m)

    def hist_add(self, src: dict, reply: str = "", end: str = "done", card: dict | None = None) -> dict:
        if src.get("k") in ("sys", "cmd"):
            from .system_text import remote
            reply = remote(reply)
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

    # ------------------------------------------------------------ media out (F21, PROTOCOL §13, media.py)
    def media_later(self, tid: int | None) -> None:
        """A page ended: find the files its reply shows (in a thread — hashing and the secret scan read them), then update
        the page with `media` / `media_skip`. Never delays the end itself."""
        if tid is None or not self.media.workdir:
            return
        try:
            job = asyncio.get_running_loop().create_task(self._attach_media(tid))
        except RuntimeError:            # not inside the loop (a unit test calling a callback directly)
            return
        self.media_jobs.add(job)
        job.add_done_callback(self.media_jobs.discard)

    async def _attach_media(self, tid: int) -> None:
        t = self.hist.get(tid)
        if t is None:
            return
        ids = next((ids for ids in self.hist.parts.values() if tid in ids), [tid])   # a long reply: every part's text
        text = "\n\n".join(x["reply"]["text"] for x in (self.hist.get(i) for i in ids) if x)
        try:
            items, skips, keys = await asyncio.to_thread(self.media.scan_keys, text, tid)
        except Exception:  # noqa: BLE001 — a scan that fails offers nothing; the page stays as it is
            self.st.log("media_scan_fail", turn=tid)
            return
        if keys:          # P211: a credential file the owner's page linked → a secret pickup card, never a plain card
            from . import owner_route
            await owner_route.offer_files(self, keys, getattr(self, "lang", "zh"))
        if items or skips:
            self.hist_update(tid, media=(items, skips))
            self.st.log("media_page", turn=tid, status=f"{len(items)}/{len(skips)}")

    def on_media_get(self, s: Session, obj: dict) -> None:
        """§13 `media_get {mid, o}` from a ready, allowlisted p33 session (any approved device: the history is shared)."""
        mid, o = obj.get("mid"), obj.get("o")
        if not wire.is_id22(mid):
            return
        if type(o) is not int or o < 0:
            self._post(self.send_app, s, {"t": "media_err", "mid": mid, "why": "shape"})
            return
        key = (s.cid, mid)
        old = self.media_streams.pop(key, None)
        if old is not None:
            old.cancel()
        if sum(1 for k in self.media_streams if k[0] == s.cid) >= 3:
            self._post(self.send_app, s, {"t": "media_err", "mid": mid, "why": "busy"})
            return
        task = asyncio.get_running_loop().create_task(self._media_window(s, mid, o))
        self.media_streams[key] = task
        task.add_done_callback(lambda _t, k=key: self.media_streams.get(k) is _t and self.media_streams.pop(k, None))

    async def _media_window(self, s: Session, mid: str, o: int) -> None:
        def live() -> bool:
            return self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub)
        try:
            win = await asyncio.to_thread(self.media.window, mid, o)
        except (media_mod.Gone, OSError) as e:
            if live():
                await self.send_app(s, {"t": "media_err", "mid": mid, "why": "gone"})
            self.st.log("media_gone", device=s.device, reason=str(e)[:32])
            return
        for off, data, last in win:
            if not live():              # revoked / closed between two chunks: nothing more goes out
                return
            if not await self.send_app(s, {"t": "media_chunk", "mid": mid, "o": off, "d": wire.b64u(data), "last": last}):
                return
            await asyncio.sleep(1 / media_mod.FPS)

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

    def push_status(self) -> None:
        """P306: every ready phone gets the current status now, changed or not (doctor --reconnect, a re-attach): a phone
        that missed one change must not stay grey until the next one."""
        self.sent_status = None
        self.status_changed()

    # callbacks from agent.Agent
    def agent_status(self, s: str) -> None:
        self.status_changed()

    def agent_text(self, text: str) -> None:
        text = drop_replacement(text)       # P281: every harness's reply text passes here — no stray 「◆?」 on any channel
        if not text or not text.strip():
            return
        _hb_touch(self)
        self.turn_shown = True
        self.jev_plan = (getattr(self, "jev_plan", "") + "\n" + text)[-approval_jev.MAX_TEXT:]
        from .silent import is_silent, MARKER
        page = self.hist.get(self.cur_turn) if self.cur_turn is not None else None
        if is_silent(text):
            if page is not None and getattr(self, "wait_note", None):
                self.turn_wait_note(None)   # a wait line is the host's, never the Agent's words to fold
            if page is not None:
                # P351 (Leo 10-16): silence is the turn's final reply alone — process notes written earlier in the same turn
                # (Codex commentary, Claude's text before a tool) fold into the silent turn; no push, no unread, no page
                rep = page.get("reply") or {}
                said = (rep.get("text") or "").strip()
                if said and not rep.get("part") and not is_silent(said):
                    self.hist_update(self.cur_turn, text=MARKER, pre=said)
                    self.turn_text = False
                    self.st.log("silent_folded", turn=self.cur_turn)
                else:
                    changed = self.hist_update(self.cur_turn, append=text)
                    if changed:
                        self.cur_turn = changed[-1]["id"]
            else:
                self.hist_add({"k": "agent", "text": ""}, text, "done")
            return
        if page is not None and (page.get("reply") or {}).get("pre") and is_silent(page["reply"].get("text")):
            # P351: more text after the marker — the marker was not the final reply; the folded notes come back, in order
            self.hist_update(self.cur_turn, text=page["reply"]["pre"] + "\n\n" + page["reply"]["text"], pre="")
        self.turn_text = True
        if getattr(self, "wait_note", None):
            self.turn_wait_note(None)       # P151b: the reply replaces the wait line, never follows it
        self.agent_said_at = time.time()          # P155: the harness-switch job's proof that the new Agent answered
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
            self.media_later(self.hist_add(self._agent_src(), text, "done")["id"])

    def proactive_turn(self, via: str) -> None:
        """P132: the harness started a turn itself (via = 'loop' | 'task'); its reply page names that source."""
        _hb_touch(self)
        self.desktop_end()
        self.proactive_via = via if via in ("loop", "task") else None

    def _agent_src(self) -> dict:
        via = getattr(self, "proactive_via", None)
        return {"k": "agent", "text": "", **({"via": via} if via else {})}

    def desktop_input(self, text: str) -> None:
        if not text or not text.strip():
            return
        _hb_touch(self)
        self.desktop_end()
        self.desktop_turn = self.hist_add({"k": "host", "name": pick(self.lang, "Agent J 主机", "Desktop"), "text": text}, "", "open")["id"]

    def turn_queued_note(self, on: bool) -> None:     # P133: busy shared session, message in its queue
        tid, note = self.cur_turn, humane.of(self, "shared.busy_queued")
        page = self.hist.get(tid) if tid is not None else None
        if page and page["end"] == "open" and page["reply"]["text"] == ("" if on else note):
            card = {k: v for k, v in (page.get("card") or {}).items() if k != "queued"}   # P281: the phone's robot marks it
            self.hist_update(tid, text=note if on else "", card={**card, **({"queued": True} if on else {})})

    def turn_wait_note(self, text: str | None, stall: bool = False, final: bool = False) -> None:
        """P151b (追加 4): what the host sees of a phone turn still running — 「正在等待 Bash：…（已 3 分钟）」 — on that page's
        reply line (card.wait; card.stall = the 「继续等 / 中断」 buttons). final = the turn's closing line (no reply came),
        kept; None = the reply arrived or the wait ended: a non-final line is withdrawn. Never overwrites a real reply."""
        tid = self.cur_turn
        page = self.hist.get(tid) if tid is not None else None
        prev = getattr(self, "wait_note", None)
        if text is None:
            if page and prev and not prev[1] and page["reply"]["text"] == prev[0]:
                self.hist_update(tid, text="", card=_keep_progress(page))
            self.wait_note = None
            return
        if not page or page["end"] != "open":
            return
        cur = page["reply"]["text"]
        if cur and cur != (prev[0] if prev else None) and cur != humane.of(self, "shared.busy_queued"):
            return
        if prev and prev[0] == text and prev[2] == stall and cur == text:
            return
        if not (stall or final) and _keep_progress(page) and not cur:
            return                      # P281: 「正在探测 Codex（2/3）…」 says more than 「正在等待 Bash…」
        self.wait_note = (text, final, stall)
        self.hist_update(tid, text=text, card={**_keep_progress(page), "wait": True, **({"stall": True} if stall else {})})

    def turn_merged(self) -> None:      # P135: a later phone message joined the same native turn; the reply goes there
        tid = self.cur_turn
        page = self.hist.get(tid) if tid is not None else None
        if page and page["end"] == "open":
            self.hist_update(tid, text=humane.of(self, "shared.merged"), end="done")
        _rcpt_turn(self, tid)                           # P313: its words reached the running turn
        self.cur_turn, self.cur_failed = None, False

    def desktop_cmd_done(self, name: str) -> None:    # P133: a desktop-typed /compact has no Stop
        tid = self.desktop_turn
        page = self.hist.get(tid) if tid is not None else None
        if page and page["src"].get("text", "").strip() == "/" + name and not page["reply"]["text"]:
            self.hist_update(tid, append=slash.done_line(name, self.lang, getattr(self, "preferences", None)))
            self.desktop_end()

    def desktop_text(self, text: str) -> None:
        if not text or not text.strip():
            return
        tid = getattr(self, "desktop_turn", None)
        if tid is None:     # P205 (6): a finished page of its own (e.g. 后台任务完成) — its files are offered like any page's
            self.media_later(self.hist_add(self._agent_src(), text, "done")["id"])
        else:
            changed = self.hist_update(tid, append=text)
            if changed:
                self.desktop_turn = changed[-1]["id"]

    def desktop_end(self) -> None:
        self.proactive_via = None
        _hb_touch(self)
        tid = getattr(self, "desktop_turn", None)
        if tid is not None:
            self.hist_update(tid, end="done")
            self.media_later(tid)
        self.desktop_turn = None

    def agent_notice(self, text: str) -> None:
        from .system_text import remote
        text = remote(one(self.lang, text))       # P128: a legacy "中文 / English" notice reaches the phone in one language
        local, self.notice_local = getattr(self, "notice_local", False), False
        self.turn_shown = True
        self.emit("agent_notice", text=text)
        e = self._remember("notice", text)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": "", **({"local": True} if local else {})}, text, "done")

    def local_notice(self, text: str) -> None:
        """§10.8: something only the human at the Agent J host can answer: a `sys` page with `local: true` (relay's
        「在Agent J 主机上处理」 card) — a notice for older phones (the same agent_notice path, marked)."""
        self.notice_local = True
        try:
            self.agent_notice(text)
        finally:
            self.notice_local = False

    def turn_failed(self) -> None:
        self.cur_failed = True

    def fallback_failure(self, text: str) -> None:
        from .privacy import owner_text
        self._fallback_error = owner_text(text[:4000])

    def official_notices(self, items: list) -> None:
        """Only cloud's pinned-signature inbox reaches here. Body stays data; trusted local policy owns behavior."""
        cats = notices.local_categories(self.st, self.preferences)
        for n in items:
            action = notices.policy(n, self.preferences, cats)
            if action == "silent":
                notices.processed(self.st, n['id'])
                continue
            if notices.presented(self.st, n['id']):
                text = pick(self.lang, "Agent J 官方", "Agent J official") + " · " + n['title_' + self.lang] + "\n" + n['body_' + self.lang]
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
        """Daily published-version notice plus opt-out, idle-only nightly upgrades."""
        await self.upgraded_notice()
        await asyncio.sleep(UPDATE_FIRST)
        while True:
            try:
                from . import auto_update
                # P203: the idle opportunity only when nothing at all would be interrupted (the upgrade card's list is empty)
                idle_newer = auto_update.newer_known(self.st) and not auto_update.impact(self)
                if auto_update.due(self.st, blocked=auto_update.busy(self), stopped=self.stopped(), newer=idle_newer):
                    # Remove completed journal only when starting a later opportunity.
                    job = auto_update.read(self.st, auto_update.JOB)
                    if job.get("phase") == "done":
                        (self.st.root / auto_update.JOB).unlink(missing_ok=True)
                    auto_update.launch(self.st)
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
            await asyncio.sleep(min(UPDATE_WAKE, 30))

    async def _starters_line(self) -> str:
        """P245: the upgrade notice names the example workflows this version put into the working root (once)."""
        try:
            from . import main_identity as _mi, starters
            line = await asyncio.to_thread(starters.take_announcement, _mi.working_root(self.agent_cfg),
                                           preferences.get(self.preferences, "appearance.language", "zh"))
        except Exception:  # noqa: BLE001 — the notice goes out without it
            return ""
        return "\n\n" + line if line else ""

    async def upgraded_notice(self) -> None:
        """F12 / C3: serve restarted after `agentj update apply --authorization`: one line to the phones, in the owner's
        language, with the doctor counts — "已升级到 <v>（doctor: N ✓ / M ! / K ✗）". Once per version (the marker is removed
        as it is read)."""
        if getattr(self, "userdata_restored", None):       # P207: self-heal told the owner, never silently
            from . import userdata
            note = userdata.restored_text(self.userdata_restored, preferences.get(self.preferences, "appearance.language", "zh"))
            self.userdata_restored = []
            e = self._remember("notice", note)
            self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
            self.hist_add({"k": "sys", "text": ""}, note, "done")
        with contextlib.suppress(Exception):     # P323: finished one-shot launchd jobs left behind (Login Items rows)
            from . import service
            swept = await asyncio.to_thread(service.sweep_jobs)
            if swept:
                self.st.log("launchd_jobs_swept", count=len(swept))
        try:
            from . import phone_update, service_recovery
            # Bounded wait for manager-owned restart verification before doctor.
            for _ in range(180):
                recovery = service_recovery.read(self.st)
                if not recovery or recovery.get("status") not in ("pending", "running") or service_recovery.failure(self.st):
                    break
                await asyncio.sleep(.25)
            warning = service_recovery.failure(self.st)
            notice = self.st.root / service_recovery.NOTICE
            if warning and (notice.exists() or service_recovery.receipt_path().exists() or warning.get("status") != "failed"):
                self.cmd_card("update", slash.Result(service_recovery.failure_text(warning), "error"))
                notice.unlink(missing_ok=True)
            completed = phone_update.take(self.st)
            if completed:
                if completed.get("reason") in ("upgraded", "doctor_failed"):
                    # Diagnose the newly restarted service, rather than reporting the updater's old-process checks.
                    from . import doctor
                    try:
                        rows = await asyncio.to_thread(doctor.run, self.st)
                        completed["counts"] = update.doctor_counts(rows)
                        completed["checks"] = [{"id": r.get("id"), "status": r.get("status")} for r in rows]
                        failed, completed["existing_issues"] = update.upgrade_health(rows)
                    except Exception:
                        completed["counts"], failed = None, True
                    completed["reason"] = "doctor_failed" if failed else "upgraded"
                    completed["result"] = "failed" if failed else "ok"
                web, machine = await self._receipt_context()   # P320: the page's version + which computer
                text = phone_update.text(completed, self.lang, web, machine)
                if completed.get("result") == "ok":
                    text += await self._starters_line()
                self.cmd_card("update", slash.Result(text, "ok" if completed.get("result") == "ok" else "error"))
                return
            rec = update.take_marker(self.st)
        except Exception:  # noqa: BLE001
            rec = None
        if not rec:
            return
        try:
            from . import doctor
            rows = await asyncio.to_thread(doctor.run, self.st)
            counts = update.doctor_counts(rows)
        except Exception:  # noqa: BLE001 — the line still goes out, with "doctor: ?"
            counts, rows = None, None
        web, machine = await self._receipt_context()
        note = update.upgraded_text(rec, counts, preferences.get(self.preferences, "appearance.language", "zh"), rows, web, machine)
        note += await self._starters_line()
        self.st.log("upgrade_notice", status=str(rec.get("to"))[:32])
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")

    async def _receipt_context(self) -> tuple:
        """P320 (Leo 10-15 22:49): the upgrade receipt checks the page's served version first (no 「稍后上线」 when it is
        already out) and names the Agent J host (several computers, several receipts)."""
        from .text import machine_name
        try:
            web = await asyncio.to_thread(update.web_state, (self.cfg or {}).get("web"))
        except Exception:  # noqa: BLE001
            web = None
        try:
            machine = machine_name() or self.st.agent_name()
        except Exception:  # noqa: BLE001
            machine = None
        return web, machine

    def agent_turn_start(self, text: str, send=None) -> None:
        self._fallback_error, self._fallback_used = "", False
        self.turn_t0 = time.monotonic()
        self.turn_shown, self.turn_at = False, (time.time(), 0.0, False)
        _hb_touch(self)
        self.jev_owner, self.jev_plan = text or "", ""      # P157: layer 2's view of this turn
        by = send.by if send is not None and send.by else self.turn_by
        self.activity("turn_start", by=by, text=text[:120])
        self.turn_by = None
        self.cur_turn, self.cur_failed = (send.turn if send is not None else None), False
        # P282 (追加): the Agent has taken this say off the queue. A shared Codex still spawns its app-server and resumes the
        # thread before `turn/start` (measured 46–65 s on a busy Mac) — the phone must stop calling it "queued, not handed
        # to the Agent" while the Agent is already working on it (`delivered` still follows at the write).
        if send is not None and isinstance(getattr(send, "sid", None), str) and not any(
                getattr(send, k, None) for k in ("heartbeat_key", "bot_handoff_id", "official_notice_id", "quota_resume_id",
                                                 "auto_notify", "hook_notice")):
            self._post(self._send_device, send.device, {"t": "say_state", "sid": send.sid, "s": "taken"})

    def agent_turn_end(self) -> None:
        self.st.log("turn_end", agent=self.agent.kind if self.agent else None)
        _hb_touch(self)
        halted = bool(self.agent and self.agent.halting)
        secs = time.monotonic() - self.turn_t0 if self.turn_t0 else None
        if (self.cur_turn is not None and not halted and not self.cur_failed and not getattr(self, "turn_shown", True)
                and secs is not None and secs < EMPTY_TURN):
            # P326 (Mac 10-15): a turn that ends at once without a word, a card or a notice is an error the owner is told
            # about — never a silent 「done」 (OpenCode never reached its server: every message ended in one second)
            self.st.log("turn_empty", agent=self.agent.kind if self.agent else None, secs=round(secs, 1))
            from .harness import LABEL
            label = LABEL.get(self.agent.kind, self.agent.kind) if self.agent else "Agent"
            self.agent_notice(pick(self.lang, f"{label} 没有处理这条消息：这一轮 {secs:.0f} 秒就结束了，一个字也没回。再发一次试试；"
                                              "还是这样，在Agent J 主机上运行 `agentj doctor`。",
                                   f"{label} did not handle this message: the turn ended after {secs:.0f} s without a word. "
                                   "Send it again; if it keeps happening, run `agentj doctor` on the Agent J host."))
            self.cur_failed = True
        self.turn_at = (getattr(self, "turn_at", (0.0,))[0], time.time(), bool(self.cur_failed))
        self.activity("turn_end", secs=round(time.monotonic() - self.turn_t0, 1) if self.turn_t0 else None,
                      result="stopped" if halted else "done")
        if self.cur_turn is not None:
            self.hist_update(self.cur_turn, end="stopped" if halted else ("failed" if self.cur_failed else "done"))
            self.media_later(self.cur_turn)
            _rcpt_turn(self, self.cur_turn)             # P313
        if self.telegram and self.cur_turn is not None:
            self.telegram.completed(self.hist.get(self.cur_turn))
        if self.mail and self.cur_turn is not None:      # P170: the reply goes back by e-mail, same thread
            with contextlib.suppress(Exception):
                self.mail.completed(self.hist.get(self.cur_turn))
        send = getattr(self.agent, 'cur_send', None)
        if getattr(send, 'official_notice_id', None):
            self.official_pending.pop(send.official_notice_id, None)
        self.cur_turn, self.cur_failed = None, False
        for gid in list(self.grants):     # a batch approval never outlives the turn it was given in (ADR-A48)
            self.end_grant(gid, "turn_end")
        if self.turn_text:
            self.turn_text = False
            self.push_notify("reply")

    # ------------------------------------------------------------ P203 (§29): open-cards snapshot + upgrade card
    def open_cards(self, s: Session) -> list[dict]:
        """Every card still waiting for a human, exactly as on_ready re-sends it to this device: approvals (the pair card
        only to a pair1 remote, Face ID fields for this device's own passkey), live batch grants, questions, friend cards,
        sudo / secret / pickup / sign-in / contact cards, opening cards (lt1) and the upgrade card."""
        out = [self._ask_msg(a, s) for a in list(self.asks.values())
               if not a.fut.done() and (a.tool != pair_card.TOOL or s.pair1)]
        out += [self._grant_msg(g) for g in list(self.grants.values()) if self._grant_live(g)]
        if s.p33:
            out += [self._question_msg(q) for q in list(self.questions.values()) if not q.fut.done()]
            if self.peers is not None:
                out += self.peers.ask_msgs()
            if self._upg_live():
                out.append(self._upg_msg())
        out += self.elevate.all_open_msgs(s)
        out += self.capabilities.open_msgs(s)
        return out

    async def on_cards_get(self, s: Session, obj: dict) -> None:
        """`cards_get {r, have, n, ts, sig}` (§29.1): read-only, but signed with this device's approval key (control-v1,
        action `cards_get`, object "cards") so only an approved remote that can answer cards gets them. The host sends the
        open cards the device does not list in `have`, then `cards {r, ok, open}` — every open id, so the device drops the
        ones that ended while it was away. Queued behind earlier broadcasts (same order as ask / ask_done)."""
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        have = obj.get("have")
        have = {x for x in have[:CARDS_HAVE_MAX] if isinstance(x, str) and len(x) <= 64} if isinstance(have, list) else set()
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "cards_get", {})
        if why:
            self.st.log("control_refused", device=s.device, action="cards_get", reason=why)
            controls.log(self.st, action="cards_get", device=s.device, result="refused:" + why)
            return await self.send_app(s, {"t": "cards", "r": r, "ok": False, "why": why})
        self._post(self._cards_reply, s, r, have)

    async def _cards_reply(self, s: Session, r: str, have: set) -> None:
        if self.sessions.get(s.cid) is not s or s.state != "ready" or not self.st.is_allowed(s.pub):
            return
        msgs = self.open_cards(s)
        sent = 0
        for m in msgs:
            if m.get("id") not in have:
                await self.send_app(s, m)
                sent += 1
        # P392: a lost terminal broadcast must close its request-bound card even without reconnecting.
        for m in self.elevate.completed_msgs(s):
            if m["id"] in have:
                await self.send_app(s, m)
        ids = [m["id"] for m in self.open_cards(s) if isinstance(m.get("id"), str)]   # what is open right now
        self.st.log("cards_snap", cid=s.cid, device=s.device, n=len(ids), sent=sent)
        await self.send_app(s, {"t": "cards", "r": r, "ok": True, "open": ids[:CARDS_OPEN_MAX]})

    def upg_items(self, caller=None) -> list:
        from . import auto_update
        return auto_update.impact(self, caller)

    def _upg_live(self) -> bool:
        return self.upg is not None and time.monotonic() < self.upg["deadline"]

    def _upg_msg(self) -> dict:
        u = self.upg
        return {"t": "upg_card", "id": u["id"], "items": self.upg_items(u.get("caller")), "from": update.__version__,
                "to": u.get("to") or "", "ttl": max(0, int(u["deadline"] - time.monotonic()))}

    def upg_open(self, caller=None) -> dict:
        """`agentj update apply` (an Agent, or a script without --yes) found things a restart would interrupt: one card on
        every p33 phone lists them; 「现在升级」 there is the phone's own signed /update (the owner's decision, not the
        Agent's). A newer request replaces the open card. → {"id", "phones", "items"}."""
        from . import auto_update
        items = self.upg_items(caller)
        latest = update.read_rec(self.st).get("latest")
        rid = secrets.token_hex(16)
        old, self.upg = self.upg, {"id": rid, "deadline": time.monotonic() + UPG_TTL, "caller": caller,
                                    "to": latest if isinstance(latest, str) and auto_update.newer_known(self.st) else ""}
        if old is not None:
            self._post(self._send_p33, lambda s, i=old["id"]: {"t": "upg_card_done", "id": i, "result": "replaced"})
        self._post(self._send_p33, lambda s: self._upg_msg())
        self._bg(self._upg_expire(rid), "upg_expire")
        phones = sum(1 for x in self.sessions.values() if x.state == "ready" and x.p33 and self.st.is_allowed(x.pub))
        self.st.log("upg_card", id=rid, n=len(items), phones=phones)
        self.activity("auto_update", result="card", text=f"升级前确认卡：{len(items)} 项会被打断 / Upgrade confirmation card: {len(items)} item(s) would be interrupted")
        self.push_notify("ask")
        return {"id": rid, "phones": phones, "items": items}

    def upg_end(self, result: str, by: str | None = None) -> None:
        u, self.upg = self.upg, None
        if u is None:
            return
        self.st.log("upg_card_done", id=u["id"], result=result, device=by)
        self._post(self._send_p33, lambda s: {"t": "upg_card_done", "id": u["id"], "result": result})

    async def _upg_expire(self, rid: str) -> None:
        await asyncio.sleep(UPG_TTL)
        if self.upg is not None and self.upg["id"] == rid:
            self.upg_end("expired")

    async def on_upg_phone(self, s: Session, t: str, obj: dict) -> None:
        """`upg_check {r}` → `upg_impact {r, items, current, latest}` (the menu / settings button asks before signing
        /update; [] = upgrade at once). `upg_card_off {id}` closes the card (narrows only: any ready paired remote)."""
        if t == "upg_card_off":
            if self.upg is not None and obj.get("id") == self.upg["id"]:
                self.upg_end("cancelled", by=s.device)
            return
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        from . import auto_update
        latest = update.read_rec(self.st).get("latest")
        await self.send_app(s, {"t": "upg_impact", "r": r, "items": self.upg_items(), "current": update.__version__,
                                "latest": latest if isinstance(latest, str) else None,
                                "newer": auto_update.newer_known(self.st)})

    def _ask_msg(self, a: Ask, s=None) -> dict:
        m = {"t": "ask", "id": a.rid, "tool": a.tool, "summary": a.summary, "ttl": max(0, int(a.deadline - time.monotonic())),
             "cat": list(a.cats), "why": a.why}
        if a.why_en:
            m["why_en"] = a.why_en        # P128: the phone shows why or why_en by its language (never both)
        if a.cred:
            m["cred"] = a.cred            # "read" → 查看密码或密钥, "change" → 改密码或密钥
        pk = self.st.passkey_of_device(s.device) if s is not None and (a.require_passkey or a.pk_if_any) else None
        if a.require_passkey or (a.pk_if_any and pk):
            m["passkey_required"] = True
            if pk:
                m["fa"] = pk["id"]
                m["fa_ch"] = wire.b64u(passkey.elevate_challenge(self.channel, s.device, a.rid, a.pk_kind, a.rid, a.digest))
        if a.scope_text:
            m["batch"] = a.scope_text
            m["batch_max"], m["batch_secs"] = BATCH_MAX, BATCH_SECS
        if a.task:
            m["task"] = a.task[:80]
        if a.goal:                        # P158: the countdown / "waits for you" / red attack tag
            m["goal"] = a.goal
            m["goal_why"] = goal_mod.why_text(a.goal_why, card_lang(self.lang))
            if a.goal_what:               # P339: plain words above the (folded) raw command
                m["goal_who"], m["goal_what"] = a.goal_who[:60], a.goal_what[:160]
            if a.goal_add:
                m["goal_add"] = a.goal_add["text"]
        elif a.tool == goal_mod.TOOL:      # P158: the opening card (one signature for the whole goal)
            m["goal"] = "grant"
        if a.allow_offer and not a.goal_add:   # P296: unticked 「以后发给 X 不用再确认」 boxes on the mail card
            m["allow_offer"] = list(a.allow_offer)
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

    # ------------------------------------------------------------ P336 (#36): cards while no remote is online
    def _online_approvers(self) -> set:
        return self._online() & set(self._approvers())

    def _remind_offline(self) -> None:
        """One content-free reminder (Telegram owner chat / owner e-mail, as set up) per approval_level.OFFLINE_GAP."""
        now = time.monotonic()
        if now - self.__dict__.get("_offline_reminded", -1e9) < approval_level.OFFLINE_GAP:
            return
        self._offline_reminded = now
        n = sum(1 for a in self.asks.values() if not a.fut.done())

        async def go():
            sent = await approval_level.remind_offline(self, max(1, n))
            self.st.log("offline_remind", result=",".join(sent) or "none", count=n)
        self._bg(go(), "offline_remind")

    def _park_key(self, tool: str, isha: str) -> tuple:
        """Who may use a parked card's allow: the same step from the same requester (thread / scheduled task, harness, folder)."""
        return (tool, isha, self.task_label, self.agent.kind if self.agent else "?", (self.agent_cfg or {}).get("dir"))

    def _parked_find(self, key: tuple):
        return next((a for a in self.asks.values() if a.parked and not a.fut.done() and a.park_key == key), None)

    def _parked_take(self, key: tuple) -> dict | None:
        ok = self.__dict__.setdefault("_parked_ok", {})
        rec = ok.pop(key, None)
        if rec is None or time.monotonic() > rec["until"] or self.st.sign_key(rec["device"]) != rec["sk"]:
            return None
        return rec

    def _park(self, a: Ask, base: dict, summary: str, key: tuple) -> dict:
        """Nobody online after PARK_GRACE: the card stays (PARK_SECS), the Agent is told now; the answer settles later."""
        parked = [x for x in self.asks.values() if x.parked and not x.fut.done()]
        for old in parked[:max(0, len(parked) - approval_level.PARK_MAX + 1)]:
            old.fut.set_result(("deny", None, None, None))          # the oldest parked card is withdrawn
        a.parked = True
        a.park_key = key
        a.deadline = time.monotonic() + approval_level.PARK_SECS
        loop = asyncio.get_running_loop()
        timer = loop.call_later(approval_level.PARK_SECS, lambda: a.fut.done() or a.fut.set_result(("deny", None, None, None)))
        a.fut.add_done_callback(lambda f: (timer.cancel(), self._park_settle(a, base, summary)))
        self.st.log("ask_parked", id=a.rid, tool=a.tool)
        self.activity("decision", id=a.rid, tool=a.tool, result="parked", reason="offline", cats=a.cats, task=a.task,
                      summary=summary[:200])
        self.emit("ask_parked", id=a.rid)
        return {"behavior": "deny", "message": approval_level.nobody_text(card_lang(self.lang))}

    def _goal_grant_tap(self, a: Ask, did: str | None, picks: list) -> None:
        """P339 「批准，并加进目标授权」: the owner's signed allow whose allow_add line is exactly the offer text this host made
        appends the offered grant to the running goal; approvals.log `reason: goal_grant_add` names that allow; one quiet line."""
        if not a.goal_add or not did or picks != [a.goal_add["text"]] or self.stopped():
            return
        cur = self.goals.current()               # review #4: only the goal the offer was made for, never a later one
        if cur is None or not a.goal_add.get("gid") or cur.get("id") != a.goal_add["gid"]:
            return
        offer = approvals.allow_add_line([a.goal_add["text"]])   # review #7: the signed line's digest, re-checked on --verify
        if not self.goals.add_grants(a.goal_add["grants"], a.rid, did, "tap", gid=a.goal_add["gid"], offer_sha256=offer):
            return
        approvals.record(self.st, rid=secrets.token_hex(16), agent=self.agent.kind if self.agent else "?", tool=goal_mod.TOOL_GRANT,
                         input_sha256=approvals.input_digest(a.goal_add["grants"]), shown_sha256=a.digest, decision="allow",
                         reason="goal_grant_add", device=did, grant=a.rid, auto="goal", scope_sha256=offer)
        line = ("Added to the goal's grant: " if card_lang(self.lang) == "en" else "已加进目标授权：") + a.goal_add["text"][:160]
        self._post(self._send_ready, lambda s: {"t": "auto", "id": a.rid, "tool": a.tool, "by": "goal", "summary": line})

    def _park_settle(self, a: Ask, base: dict, summary: str) -> None:
        self.asks.pop(a.rid, None)
        try:
            decision, did, sk, sig = a.fut.result()
        except Exception:  # noqa: BLE001
            decision, did, sk, sig = "deny", None, None, None
        reason = "device" if did else ("estop" if self.stopped() else "serve_stop" if self.stopping.is_set() else "timeout")
        picks = list(a.allow_add) if decision == "allow" and reason == "device" else []   # P339: the signed allow_add line
        approvals.record(self.st, **base, decision=decision, reason=reason, device=did, sign_pub=sk, sig=sig,
                         scope_sha256=approvals.allow_add_line(picks) if picks else None)
        allowed = decision in ("allow", "allow_batch") and reason == "device"
        if allowed and picks:
            self._goal_grant_tap(a, did, picks)
        if allowed and not self.stopped():
            self.__dict__.setdefault("_parked_ok", {})[a.park_key] = {
                "rid": a.rid, "device": did, "sk": sk, "until": time.monotonic() + approval_level.PARK_OK_SECS}
        result = ("allow" if allowed else decision) if reason == "device" else {"timeout": "timeout", "estop": "stopped"}.get(reason, "gone")
        self.st.log("ask_done", id=a.rid, tool=a.tool, decision=decision, reason=reason, device=did, parked=True)
        self.activity("decision", id=a.rid, tool=a.tool, result=decision if reason == "device" else reason, reason=reason,
                      by=self._dname(did) if did else None, cats=a.cats, task=a.task, summary=summary[:200])
        self.emit("ask_done", id=a.rid, result=result, reason=reason, device=did)
        self._post(self._send_ready, lambda s: {"t": "ask_done", "id": a.rid, "result": result})
        self.status_changed()
        if reason == "device" and a.task is None and self.agent is not None and not self.stopped():
            from .capability import ReadBackSend
            en = card_lang(self.lang) == "en"
            first = summary.split("\n", 1)[0][:160]
            note = ((f"[Agent J system note, not the owner's words] The owner {'allowed' if allowed else 'denied'} the step "
                     f"that waited while no remote was online: {first}. " + ("Retry it now, exactly as before." if allowed
                                                                           else "Do not retry it.")) if en else
                    (f"〔Agent J 系统提示，不是主人说的话〕刚才没人在线时等着的那一步，主人{'批准' if allowed else '拒绝'}了：{first}。"
                     + ("现在原样重试这一步。" if allowed else "不要重试它。")))
            with contextlib.suppress(Exception):
                self.agent.submit(ReadBackSend(note))

    async def _parked_rewait(self, a: Ask, base: dict, gone, key: tuple) -> dict:
        """The Agent retried a step whose parked card is still open: no second card. Nobody online → told again at once;
        someone online → wait for that card's answer (up to the usual lifetime)."""
        if not self._online_approvers():
            approvals.record(self.st, **base, decision="deny", reason="offline")
            self.st.log("ask_done", id=base["rid"], tool=a.tool, decision="deny", reason="offline")
            return {"behavior": "deny", "message": approval_level.nobody_text(card_lang(self.lang), again=True)}
        await asyncio.wait({a.fut} | ({gone} if gone else set()), timeout=self.ask_ttl, return_when=asyncio.FIRST_COMPLETED)
        await asyncio.sleep(0)                  # the done-callback (_park_settle) runs first
        ok = self._parked_take(key)
        if ok is not None:
            approvals.record(self.st, **base, decision="allow", reason="parked", device=ok["device"], grant=ok["rid"],
                             auto="parked")
            self.st.log("ask_done", id=base["rid"], tool=a.tool, decision="allow", reason="parked", device=ok["device"])
            self.activity("auto", id=base["rid"], tool=a.tool, summary=a.summary, by="parked", task=self.task_label)
            return {"behavior": "allow", "updatedInput": a.tool_input}
        approvals.record(self.st, **base, decision="deny", reason="parked_wait")
        msg = "用户在手机上拒绝了这个操作。" if a.fut.done() else f"手机上 {int(self.ask_ttl)} 秒内没有批准：默认拒绝。"
        return {"behavior": "deny", "message": msg}

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
        try:
            w.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            await w.drain()
        except Exception as e:  # noqa: BLE001 — P192 (B18): an approval answer that never reached the permission tool
            bg.swallowed(self.st.log, "perm_send_failed", e, every=0)

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
                    self._bg(self._perm_ask(w, m, gone), "perm_ask")
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

    async def ask(self, tool: str, tool_input: dict, gone: asyncio.Future | None = None, batch: bool = True, risk_scope: bool = False, require_passkey: bool = False, context: dict | None = None,
                  ttl: float | None = None, grant_info: bool = False, origin: str | None = None,
                  allow_offer: list | None = None) -> dict:
        """batch=False: never offered for (or approved by) a batch grant (a Codex sandbox / network escalation).
        context (P157): {"owner", "plan"} for layer 2 (approval_jev); None = what this host saw of the current turn.
        ttl (P158): this card's own wait (the goal card); grant_info: an allow carries {rid, device, sign_pub}.
        origin (P172): who is asking when it is not the main thread (「来自子 Agent：general-purpose · 写报告」), the card's last line.
        allow_offer (P296, `agentj mail send`): addresses the card offers as unticked boxes 「以后发给 X 不用再确认」; an allow
        from a device then carries `allow_add` = the ticked ones (signed, ⊆ the offer)."""
        if tool == "AskUserQuestion" and self.agent and self.agent.kind == "claude" and isinstance(tool_input, dict):
            return await self._ask_user_question(tool_input, gone)       # §10.7: a question card, not an approval card
        kind = self.agent.kind if self.agent else "?"
        v = self._classify(tool, tool_input)
        summary = approval_summary(tool, tool_input, v, self.lang)
        if require_passkey:
            from . import fence
            batch = False
            if card_lang(self.lang) == "en":
                why = {"no_bwrap": "bubblewrap (bwrap) is missing; on the Agent J host run: sudo apt-get install -y bubblewrap socat (Ubuntu / Debian / WSL)",
                       "bwrap_failed": "bubblewrap failed; check user namespaces and container policy",
                       "apparmor_userns": "AppArmor blocks user namespaces; ask for agentj doctor repair guidance",
                       "no_sandbox_exec": "macOS sandbox-exec is missing",
                       "sandbox_failed": "macOS sandbox-exec failed; check whether Agent J is already sandboxed",
                       "unsupported_os": "isolation is supported on Linux and macOS; use WSL2 on Windows"}.get(tool_input.get("reason"), "isolation check failed")
                summary = ("Sandbox unavailable; Agent J has not started: " + why + ".\nRepair the isolation tool and retry; "
                           "installation/admin approval can be completed on the phone.\nRun this launch without isolation: "
                           "it may access local files and credentials. Owner passkey required; this launch only.")
            else:
                why = fence.REASONS.get(tool_input.get("reason"), "隔离检查失败")
                summary = ("沙箱不可用，Agent J 尚未启动：" + why + "。\n修复隔离工具后重试；"
                           "需要安装或管理员权限时，可在手机上批准。\n本次不隔离运行：批准后会在沙箱外运行，可能访问本机文件和凭据。仅本次启动有效，需主人通行密钥批准。")
        if risk_scope:
            summary += CARD_TEXT[card_lang(self.lang)]["risk_scope"]
        if tool == "AuthRescue" and isinstance(tool_input.get("summary"), str):
            summary = tool_input["summary"][:600]   # P258: the rescue card shows (and the phone signs) exactly this text
        if tool in (goal_mod.TOOL, goal_mod.TOOL_GRANT, "AgentJHook") and isinstance(tool_input.get("card"), str):
            summary = tool_input["card"]            # P158 / P339: the goal card shows (and the phone signs) exactly this text
        if origin:
            summary += "\n" + str(origin)[:120]     # last line: the first stays the command (notices, push)
        digest, isha = approvals.shown_digest(tool, summary), approvals.input_digest(tool_input)
        rid = secrets.token_hex(16)
        workdir = (self.agent_cfg or {}).get("dir")
        lv = approval_level.level(self.preferences, self.st)   # P336: safe | standard (as before) | full (signed only)
        if lv != "full" and not self.__dict__.get("_unsigned_full_told") and approval_level.unsigned_full(self.preferences, self.st):
            self._unsigned_full_told = True    # the file says full, no live signed switch backs it: standard, told once
            self.st.log("approval_level_unsigned")
            self.agent_notice(approval_level.unsigned_text(self.lang))
        sc = None if v.danger or not batch or lv == "safe" else danger.batch_scope(tool, tool_input, workdir, self.danger_extra)
        base = dict(rid=rid, agent=kind, tool=tool, input_sha256=isha, shown_sha256=digest, cats=v.cats)
        if self.stopped():                # stopped: nothing is asked, nothing runs
            approvals.record(self.st, **base, decision="deny", reason="estop")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="estop")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="estop", cats=v.cats, summary=summary)
            return {"behavior": "deny", "message": "已急停（全部停下）：什么都不执行，等人恢复。"}
        if provenance.untrusted_turn(self.agent):
            # B1 (P117): a Telegram group member's turn never obtains the owner's approval — not asked, not batch-granted
            approvals.record(self.st, **base, decision="deny", reason="group_source")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="group_source")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="group_source", cats=v.cats, summary=summary)
            self.emit("ask_done", id=rid, result="deny", reason="group_source")
            return {"behavior": "deny", "message": "这一轮来自 Telegram 群成员，不是主人本人：不能请求主人批准，已拒绝。"
                                                   " / A group member's turn cannot obtain the owner's approval."}
        pkey = self._park_key(tool, isha)       # P336 N5: who is asking, fixed before any await
        if not require_passkey and ttl is None:
            ok = self._parked_take(pkey)
            if ok is not None:                # P336 (#36): the owner allowed this exact step on its parked card
                approvals.record(self.st, **base, decision="allow", reason="parked", device=ok["device"], grant=ok["rid"],
                                 auto="parked")
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="parked", device=ok["device"])
                self.activity("auto", id=rid, tool=tool, summary=summary, by="parked", task=self.task_label)
                return {"behavior": "allow", "updatedInput": tool_input}
        from .capability import goal_cli, host_mediated, staging_write
        main_root = (self.agent_cfg or {}).get("dir") if self.task_label is None else None
        if batch and ((not v.danger and (host_mediated(tool, tool_input) or staging_write(main_root, tool, tool_input)))
                      or goal_cli(tool, tool_input)):     # P339: `agentj goal grant --match scp` is data, not cred.sql
            # ADR-A194: host-gated long-task CLI / inert wizard staging — no card
            approvals.record(self.st, **base, decision="allow", reason="host")
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="host")
            self.activity("auto", id=rid, tool=tool, summary=summary, by="host", task=self.task_label)
            return {"behavior": "allow", "updatedInput": tool_input}
        scope = getattr(self, "brief_scope", None)
        if scope is not None and batch:
            from .capability import brief_allows
            if brief_allows(scope, tool, tool_input, bool(v.danger)):   # ADR-A194: inside the confirmed brief, no card
                approvals.record(self.st, **base, decision="allow", reason="brief")
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="brief")
                self.activity("auto", id=rid, tool=tool, summary=summary, by="brief", task=self.task_label)
                return {"behavior": "allow", "updatedInput": tool_input}
        g = None if v.danger or require_passkey else self._grant_for(tool, sc[:2] if sc else None)
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
        gt, gwhy, gd = None, None, None
        if not require_passkey and self.task_label is None and tool not in goal_mod.SOFT_EXEMPT:
            gd = self.goals.current()
            if gd is not None:                  # P158: the host sorts the request; the Agent's own words decide nothing
                # P339: off the event loop — the reversible check may ask git or (read-only) ssh, each bounded to 5 s
                gt, gwhy = await asyncio.to_thread(goal_mod.tier, gd, tool, tool_input, v, (self.agent_cfg or {}).get("dir"),
                                                   self._classify)
                if gt == "granted" and gd.get("preauth") and approval_level.redline(tool, tool_input, v, self.danger_extra):
                    gt, gwhy = "hold", "credentials"    # P336: a grant nobody signed never covers a red line
        if gt in ("low", "granted"):
            picks = gwhy if gt == "granted" else None
            self.goals.count(gt, picks)
            approvals.record(self.st, **base, decision="allow", reason="goal", grant=gd["grant"].get("rid"), auto="goal")
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="goal", tier=gt)
            self.activity("goal_auto", id=rid, tool=tool, summary=summary[:200], cats=v.cats, tier=gt,
                          grant=[gd["grants"][i]["what"] for i in picks] if picks else None)
            if gt == "granted":                 # a dangerous step under the grant: one quiet line; low risk stays silent
                self.emit("auto", id=rid, tool=tool, grant=None)
                line = summary.split("\n", 1)[0][:200]
                self._post(self._send_ready, lambda s: {"t": "auto", "id": rid, "tool": tool, "summary": line, "by": "goal"})
            return {"behavior": "allow", "updatedInput": tool_input}
        if gt == "reversible" and (lv == "safe" or not preferences.get(self.preferences, "approval.goal_auto", True)):
            gt, gwhy = "hold", gwhy             # the owner (enterprise / the safe level) switched the countdown off: a card
        free = None                             # P336: full level / a goal without cards — everything but the red lines
        if not require_passkey and ttl is None and gt != "attack" and not approval_level.host_card(tool) and self._approvers():
            if lv == "full":
                free = "level_full"
            elif gd is not None and approval_level.goal_preauth(self.st, self.preferences):
                free = "goal_nocard"
            if free and approval_level.redline(tool, tool_input, v, self.danger_extra):
                free = None                     # a suspected attack, credentials, money: still the owner's card
        if free:
            grant = gd["grant"].get("rid") if free == "goal_nocard" else approval_level.level_grant(self.st)
            approvals.record(self.st, **base, decision="allow", reason=free,
                             **({"grant": grant, "auto": "goal" if free == "goal_nocard" else "level"} if grant else {}))
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason=free)
            if free == "goal_nocard":
                self.goals.count("nocard")
            self.activity("auto", id=rid, tool=tool, summary=summary, by=free, cats=v.cats, task=self.task_label)
            self.emit("auto", id=rid, tool=tool, grant=None)
            line = summary.split("\n", 1)[0][:200]
            by = "full" if free == "level_full" else "goal"
            self._post(self._send_ready, lambda s: {"t": "auto", "id": rid, "tool": tool, "summary": line, "by": by})
            return {"behavior": "allow", "updatedInput": tool_input}
        if gt == "attack":
            pass                                # P158: a suspected attack never goes to Jev — the red card
        elif lv == "safe":
            pass                                # P336: no Jev layer 2 under the safe level — the card
        elif not require_passkey and not approval_level.host_card(tool) and approval_jev.gray(v) and approval_jev.available():
            # P157 layer 2: only a "can't tell" verdict, never a clear danger; any failure falls through to the card
            ctx = context if context is not None else {"owner": getattr(self, "jev_owner", ""), "plan": getattr(self, "jev_plan", "")}
            thr = approval_jev.threshold(self.preferences)
            try:
                res = await asyncio.wait_for(asyncio.to_thread(approval_jev.judge, tool, tool_input, ctx, threshold=thr),
                                             approval_jev.TIMEOUT_S + 0.5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — fail closed: the card
                res = approval_jev.Result("unavailable", reason="timeout")
            self.st.log("jev_layer2", id=rid, tool=tool, status=res.status, p=None if res.p is None else round(res.p, 2),
                        threshold=thr)
            if res.status == "allow" and not self.stopped():
                approvals.record(self.st, **base, decision="allow", reason="jev")
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="jev")
                self.activity("jev", id=rid, tool=tool, summary=summary, why=res.reason, cats=v.cats, task=self.task_label)
                self.emit("auto", id=rid, tool=tool, grant=None)
                line = summary.split("\n", 1)[0][:200]
                self._post(self._send_ready, lambda s: {"t": "auto", "id": rid, "tool": tool, "summary": line, "by": "jev",
                                                        "why": res.reason})
                return {"behavior": "allow", "updatedInput": tool_input}
        if not self._approvers():
            approvals.record(self.st, **base, decision="deny", reason="no_device")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="no_device")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="no_device", cats=v.cats, summary=summary)
            self.emit("ask_done", id=rid, result="deny", reason="no_device")
            return {"behavior": "deny", "message": "没有能批准的已配对手机：默认拒绝。先用 agentj pair 配对一台手机。"}
        parkable = not require_passkey and ttl is None and gt != "reversible" and not approval_level.host_card(tool)
        prev = self._parked_find(pkey) if parkable else None
        if prev is not None:                    # P336 (#36): the same step's card is already waiting on the phone
            return await self._parked_rewait(prev, base, gone, pkey)
        if sum(1 for a in self.asks.values() if not a.fut.done()) >= MAX_ASKS:
            approvals.record(self.st, **base, decision="deny", reason="too_many")
            return {"behavior": "deny", "message": "同时等待批准的请求太多：默认拒绝。"}
        wait = goal_mod.auto_secs() if gt == "reversible" else (ttl if ttl else self.ask_ttl)
        g_who, g_what, g_add = "", "", None
        if gt in ("reversible", "hold", "attack"):   # P339: who wants to do what, in plain words; maybe one-tap 加进目标授权
            from .harness import LABEL as _HL
            gl = card_lang(self.lang)
            g_who = self.task_label or _HL.get(kind, kind if kind != "?" else "")
            g_what = goal_mod.human(tool, tool_input, v, gl, self._classify)
            if gt == "hold" and gwhy == "irreversible" and not allow_offer:
                offer = goal_mod.offer_grant(tool, tool_input, v, (self.agent_cfg or {}).get("dir"), self._classify, gl)
                if offer and len(gd.get("grants") or []) + len(offer) <= goal_mod.MAX_ALL_GRANTS:
                    g_add = {"text": goal_mod.offer_text(offer, gl), "grants": offer, "gid": gd.get("id")}   # review #4: this goal only
        a = Ask(rid, tool, summary, digest, tool_input, isha, time.monotonic() + wait,
                asyncio.get_running_loop().create_future(), cats=v.cats, why=v.why, why_en=v.why_en, cred=v.cred,
                scope=sc[:2] if sc else None, scope_text=sc[2] if sc else None, task=self.task_label, require_passkey=require_passkey,
                goal={"reversible": "auto", "hold": "hold", "attack": "attack"}.get(gt or "", ""), goal_why=str(gwhy or ""),
                goal_who=g_who, goal_what=g_what, goal_add=g_add,
                allow_offer=[g_add["text"]] if g_add else [str(x) for x in allow_offer or []][:10])
        self.asks[rid] = a
        self.activity("ask", id=rid, tool=tool, cats=v.cats, why=v.why or None, why_en=v.why_en or None, cred=v.cred or None,
                      summary=summary, task=self.task_label)
        self.st.log("ask", id=rid, tool=tool, agent=kind, kind="danger" if v.danger else "low")
        self.emit("ask", id=rid, tool=tool, summary=summary)
        self._post(self._send_ready, lambda s: self._ask_msg(a, s))   # same queue as the Agent's text: order is kept
        self.status_changed()
        self.push_notify("ask")
        offline = not self._online_approvers()
        if offline and gt != "reversible":
            self._remind_offline()               # P336 (#36): push above + one content-free Telegram / e-mail line
        try:
            waiters = {a.fut} | ({gone} if gone else set())
            # FIRST_COMPLETED: the answer (or the agent giving up) ends the wait at once — with the default ALL_COMPLETED an
            # approval only took effect at the deadline (L1 bug, found in L2)
            if offline and parkable:
                done, _ = await asyncio.wait(waiters, timeout=max(0.0, min(a.deadline - time.monotonic(),
                                                                          approval_level.PARK_GRACE)),
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done and not self._online_approvers() and not self.stopped() and not self.stopping.is_set():
                    return self._park(a, base, summary, pkey)   # the card waits on; the Agent does the independent work
            done, _ = await asyncio.wait(waiters, timeout=max(0.0, a.deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if a.fut in done:
                decision, did, sk, sig = a.fut.result()
                # serve stopping / the stop switch resolve pending requests with no device
                reason = "device" if did else ("estop" if self.stopped() else "serve_stop")
            else:
                decision, did, sk, sig = "deny", None, None, None
                reason = ("superseded" if _superseded(gone) else "agent_gone") if gone in done else \
                    ("serve_stop" if self.stopping.is_set() else "timeout")      # P318: a newer card for the same item
                if reason == "timeout" and a.goal == "auto" and not self.stopped() and self.goals.current() is not None:
                    decision, reason = "allow", "goal_auto"     # P158: reversible step, nobody answered in 10 s
                if not a.fut.done():
                    a.fut.set_result((decision, None, None, None))
        finally:
            if not a.parked:
                self.asks.pop(rid, None)
        gd = self.goals.current() if a.goal else None
        picks = list(a.allow_add) if decision == "allow" and reason == "device" else []
        approvals.record(self.st, **base, decision=decision, reason=reason, device=did, sign_pub=sk, sig=sig,
                         scope=a.scope_text if decision == "allow_batch" else None,
                         scope_sha256=approvals.allow_add_line(picks) if picks else None,
                         **({"grant": gd["grant"].get("rid"), "auto": "goal"} if reason == "goal_auto" and gd else {}))
        if decision in ("allow", "allow_batch") and reason == "device" and _superseded(gone):
            # P318: the signed allow raced a merge that superseded this card — the caller applies nothing; say so
            approvals.record(self.st, **base, decision="deny", reason="superseded_after_allow", device=did)
        if gd is not None:
            if reason == "goal_auto":
                self.goals.count("auto")
            elif decision != "allow" and a.goal in ("hold", "attack") and reason in ("device", "timeout"):
                self.goals.block(summary, a.goal_why or a.goal, tool=tool, cats=a.cats, human_text=a.goal_what, who=a.goal_who,
                                 rule=a.why, rule_en=a.why_en)
            elif decision == "allow" and reason == "device":
                self._goal_grant_tap(a, did, picks)
        raw = decision
        if decision == "allow_batch" and reason == "device":
            self._open_grant(a, did)
        decision = "allow" if decision == "allow_batch" else decision
        result = decision if reason in ("device", "goal_auto") else {"timeout": "timeout", "estop": "stopped"}.get(reason, "gone")
        self.st.log("ask_done", id=rid, tool=tool, decision=decision, reason=reason, device=did)
        self.activity("decision", id=rid, tool=tool, result=raw if reason == "device" else reason, reason=reason,
                      by=self._dname(did) if did else None, cats=a.cats, task=a.task, summary=summary[:200])
        self.emit("ask_done", id=rid, result=result, reason=reason, device=did)
        self._post(self._send_ready, lambda s: {"t": "ask_done", "id": rid, "result": result})
        self.status_changed()
        if decision == "allow":
            answer = {"behavior": "allow", "updatedInput": tool_input}
            if risk_scope and reason == "device" and lv != "safe":   # P158: never from a countdown; P336: not under safe
                answer["risk_grant"] = {"rid": rid, "device": did, "sign_pub": sk}
            if grant_info and reason == "device":
                answer["grant_info"] = {"rid": rid, "device": did, "sign_pub": sk}
            if picks and not a.goal_add:
                answer["allow_add"] = picks
            return answer
        if a.goal in ("hold", "attack") and reason in ("device", "timeout"):
            from .privacy import owner_text
            gl = card_lang(self.lang)
            return {"behavior": "deny", "message": goal_mod.hold_message(a.goal_why or a.goal, gl, reason,
                                                                         owner_text(a.why_en if gl == "en" and a.why_en else a.why))}
        msg = {"device": "用户在手机上拒绝了这个操作。", "timeout": f"手机上 {int(self.ask_ttl)} 秒内没有批准：默认拒绝。",
               "serve_stop": "agentj serve 已停止：默认拒绝。", "estop": "已急停（全部停下）：默认拒绝。"}.get(reason, "已拒绝。")
        return {"behavior": "deny", "message": msg}

    def _classify(self, tool, tool_input):
        cfg = self.agent_cfg or {}
        if cfg.get("session_mode") == "shared":
            return danger.classify_shared(tool, tool_input, self.danger_extra, cfg.get("approvals", "standard"), cfg.get("dir"))
        return danger.classify(tool, tool_input, self.danger_extra)

    def policy_refused(self, tool: str, tool_input: dict, why: str, notice: str) -> None:
        """A request the harness asked that would give it more than the human's own configuration allows (Codex beyond its own
        sandbox, ADR-A73): refused without a card — approvals.log (reason `policy`), host.log, activity, one notice."""
        rid = secrets.token_hex(16)
        v = self._classify(tool, tool_input)
        summary = approval_summary(tool, tool_input, v, self.lang)
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

    async def _answer_refused(self, s: Session, a: Ask, reason: str) -> None:
        self.st.log("answer_refused", id=a.rid, device=s.device, reason=reason)
        await self.send_app(s, {"t": "answer_refused", "id": a.rid, "why": reason, "ask": self._ask_msg(a, s)})

    async def _answer(self, s: Session, obj: dict) -> None:
        refused = []
        if self.peers is not None and self.peers.answer(s.device, obj, refuse=refused.append):
            for receipt in refused:
                await self.send_app(s, receipt)
            return                         # P71: a friend card (friend_request / peer_question), decided by its own rules
        rid, ok, sig, batch = obj.get("id"), obj.get("ok"), obj.get("sig"), obj.get("batch", False)
        a = self.asks.get(rid) if isinstance(rid, str) else None
        if a is None or a.fut.done():
            return                         # unknown or already decided (another phone, timeout): nothing to do
        if not isinstance(ok, bool) or not isinstance(sig, str) or not isinstance(batch, bool) \
                or time.monotonic() > a.deadline:
            await self._answer_refused(s, a, "shape_or_late")
            return
        if batch and (not ok or a.scope is None or a.cats):
            # a dangerous request (or one that was not offered for batching) can only be decided one by one (ADR-A47)
            await self._answer_refused(s, a, "no_batch")
            return
        sk = self.st.sign_key(s.device)
        try:
            sigb = wire.unb64u(sig)
        except ValueError:
            sigb = b""
        decision = "allow_batch" if batch else ("allow" if ok else "deny")
        scope = a.scope_text if batch else None   # the host checks the scope IT offered: a phone cannot widen it
        picks = obj.get("allow_add")
        if picks is not None:             # P296: ticked boxes — only on an allow, only addresses this card offered, in its order
            if not (ok and not batch and isinstance(picks, list) and picks and all(isinstance(x, str) for x in picks)
                    and len(set(picks)) == len(picks) and picks == [x for x in a.allow_offer if x in picks]):
                await self._answer_refused(s, a, "allow_add")
                return
        if not sk or not approvals.verify(sk, sigb, self.channel, s.device, rid, decision, a.digest, scope, picks):
            await self._answer_refused(s, a, "no_key" if not sk else "bad_signature")
            return
        if a.tool == pair_card.TOOL and not s.pair1:
            await self._answer_refused(s, a, "no_pair1")   # only a page that showed the card
            return
        pk = self.st.passkey_of_device(s.device) if ok and (a.require_passkey or a.pk_if_any) else None
        if ok and (a.require_passkey or (a.pk_if_any and pk)):
            try:
                if not pk:
                    raise passkey.PasskeyError("bad", "no_passkey")
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                challenge = passkey.elevate_challenge(self.channel, s.device, rid, a.pk_kind, rid, a.digest)
                count = passkey.verify_assertion(pk, f, challenge)
                self.st.passkey_count(s.device, pk["id"], count)
            except (passkey.PasskeyError, ValueError):
                await self._answer_refused(s, a, "passkey_required")
                return
        a.allow_add = list(picks or [])
        a.fut.set_result((decision, s.device, sk, sigb))

    # ------------------------------------------------------------ Agent friends (P71, PROTOCOL §17.7)
    def peer_broadcast(self, obj: dict) -> None:
        """peer_service → every ready p33 phone (fr_changed, friend cards, their ask_done), in the order of the post queue."""
        self._post(self._send_p33, lambda s, o=obj: o)

    async def on_friends(self, s: Session, t: str, obj: dict) -> None:
        """§17.7: reads answer the asking session; the six writes are signed like every phone control (controls.check) and
        answered ctl_res. There is no message that sends text to a friend from a device: fr_send / fr_msg / anything else is
        dropped and logged."""
        r = obj.get("r")
        if t not in FRIEND_READS and t not in controls.FRIEND_ACTIONS:
            self.st.log("fr_send_refused", device=s.device, kind=t[:24])
            return
        if not s.p33 or self.peers is None or not isinstance(r, str) or not _RID.fullmatch(r):
            return
        if t in FRIEND_READS:
            res = self.peers.read(t, obj)          # small files; the outbox sqlite lives on this thread
            if res is not None:
                await self.send_app(s, {**res, "r": r})
            return
        if t == "fr_set":
            target = {"friend": obj.get("friend"), "op": obj.get("op"), "value": obj.get("value")}
            bad = not (isinstance(target["friend"], str) and len(target["friend"]) <= 64 and isinstance(target["op"], str)
                       and len(target["op"]) <= 16 and (target["value"] is None or (isinstance(target["value"], str)
                                                                                  and len(target["value"]) <= 64)))
        elif t == "pg_set":
            target = {"group": obj.get("group")}
            bad = not isinstance(target["group"], dict) or len(json.dumps(target["group"], ensure_ascii=False)) > 8192
        elif t == "pg_del":
            target = {"id": obj.get("id")}
            bad = not (isinstance(target["id"], str) and len(target["id"]) <= 64)
        elif t == "fr_add":
            target = {"id": obj.get("id"), "note": obj.get("note")}
            bad = not (isinstance(target["id"], str) and len(target["id"]) <= 64
                       and (target["note"] is None or (isinstance(target["note"], str) and len(target["note"]) <= 280)))
        elif t == "fr_discoverable":
            target = {"on": obj.get("on")}
            bad = not isinstance(target["on"], bool)
        elif t == "fr_ctx":                    # P73 (ADR-A176): a friend's 「补充设定」, ≤ 4000 characters — the owner's own setting
            target = {"friend": obj.get("friend"), "text": obj.get("text")}
            bad = not (isinstance(target["friend"], str) and len(target["friend"]) <= 64 and isinstance(target["text"], str))
            if not bad and len(target["text"]) > friends_mod.MAX_CONTEXT:
                return await self._ctl_res(s, r, t, False, why="too_long")
        else:
            target = {"owner": obj.get("owner"), "intro": obj.get("intro")}
            bad = not all(v is None or (isinstance(v, str) and len(v) <= 140) for v in target.values())
        if bad:
            return await self._ctl_res(s, r, t, False, why="shape")
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, t, target)
        if why:
            self.st.log("control_refused", device=s.device, action=t, reason=why)
            controls.log(self.st, action=t, device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, t, False, why=why)
        try:
            ok, why, fid = await self.peers.write(t, target)
        except OSError:
            ok, why, fid = False, "io", None
        result = "ok" if ok else str(why)
        self.st.log("control", device=s.device, action=t, result=result)
        controls.log(self.st, action=t, device=s.device, result=result, obj=target, sig=obj.get("sig"), n=obj.get("n"),
                     ts=obj.get("ts"))
        await self._ctl_res(s, r, t, ok, **({} if ok else {"why": why}))
        if ok:
            self.peers.changed(fid if t in ("fr_set", "fr_ctx") else None)

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
        return self._dev_label(did) or did

    def _dev_label(self, did: str | None) -> str:
        """P179: how a remote is named on pages, in the activity log and to the Agent — 「张伟（老板）· iOS Safari」 once
        the owner named it, else its own pairing label (as before)."""
        rec = self.st.devices().get(did) if did else None
        return clean_label(device_label(rec)) if isinstance(rec, dict) else ""

    def device_renamed(self, did: str) -> None:
        """P179: after any rename (account page or the remote itself) — live sessions of that remote speak under the new
        label, the account page gets a report, and every phone gets the new names (settings list + source labels)."""
        label = self._dev_label(did)
        for x in self.sessions.values():
            if x.device == did and label:
                x.name = label
        self.reporter.trigger("online")
        self._post(self._send_ready, lambda _s: self.preferences_msg())

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
        self.__dict__.pop("_parked_ok", None)          # P336: an allow given to a parked card dies with 「全部停下」
        for a in list(self.asks.values()):
            if not a.fut.done():
                a.fut.set_result(("deny", None, None, None))
        for q in list(self.questions.values()):        # §10.7: an open question is cancelled at once (`stopped`)
            if not q.fut.done():
                q.fut.set_result(("stopped", None, None, None, None))
        for gid in list(self.grants):
            self.end_grant(gid, "estop")
        for scope in list(self.quota_resume.items):    # P314: no automatic continue after stop-everything
            self.quota_resume.drop(scope, "stopped")
        with contextlib.suppress(Exception):           # P158: the goal's one-time grant ends with everything else
            self.goals.end("estop", by="estop")
        self.bots.estop()
        self.elevate.cancel_all("stopped")            # F17: open sudo / secret cards end (a running command finishes)
        if self.peers is not None:                    # P71 §17.9: no mailbox frame leaves, every peer session ends
            await self.peers.estop(True)
        if self.hooks is not None:                    # P366: every hook answers 423, `agentj hook wait` returns stopped
            self.hooks.estop(True)
        busy = False
        if self.agent:
            busy = await self.agent.halt(clear_queue=True)
        ran = await self.scheduler.stop_current()
        self.status_changed()
        m = self._estop_msg()
        self._post(self._send_ready, lambda s: m)
        note = "已急停（" + name + "）：" + ("正在进行的一轮已中断；" if busy else "") + ("正在跑的定时任务已中断；" if ran else "") + \
            "待批准的全部拒绝，批量授权已收回，定时任务暂停。恢复之前 Agent J 不接新消息。"
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")

    async def do_resume(self, by: str, name: str) -> None:
        rec = controls.set_estop(self.st, False, by)
        self.estop = {**rec, "by_name": name}
        self.st.log("estop", status="off", device=by.split(":", 1)[1] if by.startswith("phone:") else None)
        self.emit("estop", on=False, by=by)
        self.activity("resume", by=name)
        if self.peers is not None:
            await self.peers.estop(False)
        self.status_changed()
        m = self._estop_msg()
        self._post(self._send_ready, lambda s: m)
        note = f"已恢复（{name}）：Agent J 接收新消息，定时任务按各自的启用状态继续。"
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")
        self.scheduler.wake.set()

    def task_finished(self, res: dict) -> None:
        """A scheduled run ended: its batch grants end with it; one notice to the phones (task name + the VERDICT sentence)."""
        for gid in list(self.grants):
            self.end_grant(gid, "turn_end")
        title = res.get("title") or res["id"]
        if res.get("status") == "interrupted" and not res.get("stopped"):
            text = f"工作流「{title}」：{res['line']}"
        elif res.get("stopped"):
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
        page = text
        if res.get("deliver") and not res.get("stopped"):   # 0.18: the brief's owner deliverables, offered as files (§13)
            page += "\n\n" + "\n".join(f"[{os.path.basename(p)}]({p})" for p in res["deliver"][:5])
        t = self.hist_add({"k": "task", "name": clean(str(title), 64), "text": ""}, clean(page, wire.MAX_TEXT_P33),
                          "stopped" if res.get("stopped") or res.get("status") == "interrupted" else "done")
        if res.get("deliver") and isinstance(t, dict):
            self.media_later(t.get("id"))
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
        if t == "systask_list":         # P207 §2: read-only list of the Agent J host's own timers / cron / LaunchAgents / schtasks
            from . import systasks
            items = await asyncio.to_thread(systasks.rows)
            return await self._send_chunks(s, {"t": "systasks", "r": r}, "items", items, {"max": systasks.MAX})
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

    # ------------------------------------------------------------ P115: first-run checklist (first_run.py, ADR-A196)
    def setup_serve(self) -> dict:
        """What the checklist probes need from this running serve (the CLI asks the control socket for the same)."""
        return {"relay_up": self.relay_up, "agent": self.agent.kind if self.agent else None}

    def push_setup(self, snap: dict, show: bool = True) -> None:
        """The checklist card to every ready remote (phone and the Agent J host's browser show the same revision)."""
        show = show and not snap.get("closed")      # P131 追加2: a closed checklist never shows itself again (older pages too)
        msg = {"t": "setup_card", "show": show, **{k: snap[k] for k in snap if k not in ("ok", "result", "r")}}
        self._post(self._send_ready, lambda s: msg)

    async def on_setup(self, s: Session, t: str, obj: dict) -> None:
        from . import first_run
        r = obj.get("r")
        if not isinstance(r, str) or not _RID.fullmatch(r):
            return
        net = obj.get("net")
        if net in ("cellular", "wifi", "unknown"):
            s.setup_net = net
        if t == "setup_close":                # P131: the banner's ✕ — no signature (it only hides; nothing is approved)
            try:
                res = await asyncio.to_thread(first_run.close, self.st)
            except (first_run.SetupError, OSError) as e:
                return await self.send_app(s, {"t": "setup_card", "r": r, "ok": False, "why": getattr(e, "reason", "io")})
            self.st.log("setup_close", device=s.device)
            self.activity("setup_close", by=s.name or s.device, id="checklist", text="close")
            await self.send_app(s, {"t": "setup_card", "r": r, "show": False, **{k: v for k, v in res.items() if k != "r"}})
            if res.get("revision"):
                self.push_setup(res, show=False)       # every other open page hides its banner too
            return
        if t == "setup_get":                  # read (and, with check, a live read-only re-probe); never changes a choice
            perm = obj.get("perm") if isinstance(obj.get("perm"), dict) else None
            report = {"mic": perm.get("mic")} if perm and perm.get("mic") in ("granted", "denied", "prompt") else None
            item = obj.get("item") if isinstance(obj.get("item"), str) and obj.get("item") in first_run.BY_ID else None
            try:
                if obj.get("check") is True:
                    res = await asyncio.to_thread(first_run.check, self.st, [item] if item else None, self.setup_serve(), report,
                                                  bool(item))
                elif obj.get("open") is True:     # P131: the menu's 「首次准备清单」 reopens a closed checklist
                    res = await asyncio.to_thread(first_run.reopen, self.st)
                else:
                    res = await asyncio.to_thread(first_run.status_, self.st)
            except (first_run.SetupError, OSError) as e:
                res = {"ok": False, "why": getattr(e, "reason", "io")}
            return await self.send_app(s, {"t": "setup_card", "r": r, "show": False, **{k: v for k, v in res.items() if k != "r"}})
        target = {"item": obj.get("item"), "choice": obj.get("choice"), "rev": obj.get("rev")}
        if not (isinstance(target["item"], str) and target["item"] in first_run.BY_ID and target["choice"] in first_run.MARKS
                and type(target["rev"]) is int and target["rev"] > 0):
            return await self._ctl_res(s, r, "setup_mark", False, why="shape")
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "setup_mark", target)
        if why:
            self.st.log("control_refused", device=s.device, action="setup_mark", reason=why)
            controls.log(self.st, action="setup_mark", device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, "setup_mark", False, why=why)
        result, snap = "ok", None
        try:
            snap = await asyncio.to_thread(first_run.mark, self.st, target["item"], target["choice"], target["rev"],
                                           obj["n"], self.setup_serve())
        except first_run.SetupError as e:
            result = e.reason
        except OSError:
            result = "io"
        self.st.log("control", device=s.device, action="setup_mark", result=result)
        controls.log(self.st, action="setup_mark", device=s.device, result=result, obj=target, sig=obj.get("sig"),
                     n=obj.get("n"), ts=obj.get("ts"))
        self.activity("setup_mark", by=s.name or s.device, id=target["item"], text=target["choice"])
        await self._ctl_res(s, r, "setup_mark", result == "ok", **({} if result == "ok" else {"why": result}))
        if snap:
            self.push_setup(snap, show=True)
        if result == "ok" and target["choice"] in first_run.ACTIONS:   # P131: install / adopt / open-and-sign-in
            self.setup_act = asyncio.ensure_future(self._setup_act(target["item"], target["choice"], obj["n"]))

    async def _setup_act(self, item: str, choice: str, ref: str) -> None:
        from . import first_run
        try:
            snap = await asyncio.to_thread(first_run.act, self.st, item, choice, ref, self.setup_serve())
        except (first_run.SetupError, OSError):
            return
        self.push_setup(snap, show=True)

    async def _setup_round_trip(self, s: Session) -> None:
        """The leave-the-computer check: a message from a phone while the check is armed is its evidence."""
        from . import first_run, onboarding
        if getattr(s, "source_kind", None) == "telegram":
            return
        try:
            snap = await asyncio.to_thread(first_run.phone_round_trip, self.st, onboarding.kind_of(s.name or ""),
                                           getattr(s, "setup_net", None), self.setup_serve())
        except (first_run.SetupError, OSError):
            return
        if snap:
            self.push_setup(snap, show=True)

    async def on_wf(self, s: Session, t: str, obj: dict) -> None:
        """P244 (§30.2): wf_list (etag → wf_same; chunked per workflows.messages) · wf_doc · skill_get — only names / ids of the
        last scan are ever read (workflows.Scanner)."""
        r = obj.get("r")
        if not isinstance(r, str) or not _RID.fullmatch(r):
            return
        if t == "wf_list" or self.wf.last is None:
            from . import main_identity
            c = self.agent_cfg or {}
            root = main_identity.working_root(c)
            items = list(self.busy.mem.values()) + list(self.busy.outside)
            scan = self.wf.sweep if t == "wf_list" else self.wf.scan     # P276: wf_list also fills the CEO roster
            pic = await asyncio.to_thread(scan, root, c.get("kind"), self.lang, items)
            if t == "wf_list":
                if obj.get("etag") == pic["tail"]["etag"]:
                    return await self.send_app(s, {"t": "wf_same", "r": r})
                from .workflows import messages
                for m in messages(pic, r):
                    await self.send_app(s, m)
                self.busy_changed()  # the organisation metadata may now be available
                return
        if t == "wf_doc":
            res = await asyncio.to_thread(self.wf.doc, obj.get("folder"), obj.get("name"))
        else:
            res = await asyncio.to_thread(self.wf.skill, obj.get("id"))
        await self.send_app(s, {"t": t, "r": r, **res})

    async def jcard_open(self, req: dict) -> dict:
        """P360 (§34): `agentj card open <id> [--at <anchor>]` → `{"t": "jcard_open", id, at?}` to every ready phone, which
        opens that card (or system page) and scrolls to the place. Navigation only — nothing is read, sent or changed, so
        no approval; the target is checked again here (a switched-on card, or a page; a well-formed anchor)."""
        r = await asyncio.to_thread(jcards.open_target, self.st.root, req.get("id"), req.get("at"))
        if not r["ok"]:
            return {"ok": False, "why": r["why"]}
        now = time.monotonic()                 # an Agent may call it with no approval: never faster than one push per 2 s
        if now - getattr(self, "_jcard_open_at", -1e9) < jcards.OPEN_GAP:
            return {"ok": False, "why": "too_soon"}
        self._jcard_open_at = now
        msg = {"t": "jcard_open", "id": r["id"], **({"at": r["at"]} if r.get("at") else {})}
        n = sum(1 for s in self.sessions.values() if s.state == "ready" and s.p33 and self.st.is_allowed(s.pub))
        self._post(self._send_p33, lambda s, m=msg: m)
        self.st.log("jcard_open", status=r["id"])
        return {"ok": True, "delivered": n}

    async def on_jcard(self, s: Session, t: str, obj: dict) -> None:
        """P360 (§34): Agent J Cards, answered to the asking session only. jcard_list / jcard_get read; jcard_set flips one
        card's switch (display only — a card never acts by itself: its buttons become chat messages the owner confirms)."""
        r = obj.get("r")
        if not isinstance(r, str) or not _RID.fullmatch(r):
            return
        lang = obj.get("lang") if obj.get("lang") in ("zh", "en") else self.lang
        cid, on = obj.get("id"), obj.get("on")
        try:                            # whatever an Agent left under agentperm/cards, the phone gets an answer, never a closed session
            if t == "jcard_list":
                res = {"t": "jcard_items", **await asyncio.to_thread(jcards.phone_list, self.st.root, lang)}
            elif t == "jcard_get":
                res = {"t": "jcard_get", **await asyncio.to_thread(jcards.phone_get, self.st.root, cid, lang, self.agent_cfg)}
            elif t == "jcard_note":
                from . import jcards_data
                # Add belongs to the Agent skill/CLI; paired remotes only edit/delete existing entries.
                if obj.get("op") not in ("edit", "delete"):
                    res = {"t": t, "ok": False, "why": "bad_op"}
                else:
                    res = {"t": t, **await asyncio.to_thread(jcards_data.note_change, self.st.root, cid,
                           obj.get("op"), obj.get("entry"), obj.get("revision"), obj.get("text"))}
                self.st.log("jcard_note", device=s.device, status="ok" if res.get("ok") else "refused")
            elif t == "jcard_color":
                from . import jcards_data
                res = {"t": t, **await asyncio.to_thread(jcards_data.set_color, self.st.root, cid, obj.get("color"))}
                self.st.log("jcard_color", device=s.device, status="ok" if res.get("ok") else "refused")
            else:
                ok = isinstance(cid, str) and type(on) is bool and await asyncio.to_thread(jcards.set_on, self.st.root, cid, on)
                res = None
        except Exception as e:  # noqa: BLE001
            self.st.log("jcard_failed", device=s.device, reason=type(e).__name__)
            res = {"jcard_list": {"t": "jcard_items", "cards": []}, "jcard_get": {"t": "jcard_get", "ok": False, "why": "not_found"}}.get(
                t, {"t": t if t in ("jcard_note", "jcard_color") else "jcard_res", "ok": False, "id": None, "on": None})
            ok = False
        if res is not None:
            return await self.send_app(s, {**res, "r": r})
        if ok:
            self.st.log("jcard_set", device=s.device, status=f"{cid}:{'on' if on else 'off'}")
        await self.send_app(s, {"t": "jcard_res", "r": r, "ok": bool(ok), "id": cid if ok else None, "on": on if ok else None})

    async def _wf_first_sweep(self) -> None:
        """P276: one bounded scan of the working root at start (workflows.SCAN_BUDGET), adding new WDB folders to the CEO
        roster; never blocks or stops serve."""
        try:
            from . import main_identity
            c = self.agent_cfg or {}
            await asyncio.to_thread(self.wf.sweep, main_identity.working_root(c), c.get("kind"), self.lang, ())
        except Exception as e:  # noqa: BLE001
            self.st.log("wf_sweep_failed", reason=type(e).__name__)

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
        if name == "clear" and not interim and res.kind == "ok" and self.agent is not None:
            kind = self.agent.kind
            x["clear_harness"] = kind
            resume = humane.clear_resume(self, kind, self.st.agent_session(kind))
            if resume:
                x["resume"] = resume
        if res.models:
            x["models"] = res.models[:40]
        if res.undo:
            x["undo"] = True
        if res.sep:
            x["sep"] = True
        if getattr(res, "open", ""):          # P73: 「打开我的名片」 / 「打开加好友」 under a friend command's answer
            x["open"] = res.open[:64]
        if by:
            x["by"] = by[:64]
        if not interim and res.kind == "ok":     # P133
            res.text = slash.with_done(name, res.text, self.lang, getattr(self, "preferences", None))
        from .system_text import remote
        res.text = remote(res.text)
        text = res.text if text_units(res.text) <= wire.MAX_TEXT else res.text[:3900] + " …"
        self.emit("agent_notice", text=f"/{name}：{text}")
        e = self._remember("cmd", text, x=x)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        if turn is not None and self.hist.get(turn) is not None:
            self.hist_update(turn, text=wire.well_formed(res.text), end="open" if interim else "done",
                             card=None if interim else x)
        elif not interim:
            self.hist_add({"k": "cmd", "text": "/" + name, **({"name": by[:64]} if by else {})}, res.text, "done", x)
        if self.telegram and turn is not None and not interim:      # P59: the owner's Telegram /compact gets its result line
            self.telegram.cmd_result(turn, res.text)

    def clear_resume(self, kind: str, sid: str) -> None:
        """P380: fill the existing clear card when the harness creates its real session, also after restart.

        The card's harness marker belongs to that clear, not the newest chat reply. Undo or a later clear
        ends the search; no new message is emitted and the greeting/undo metadata remain intact.
        """
        resume = humane.clear_resume(self, kind, sid)
        if not resume:
            return
        for page in reversed(list(self.hist.turns.values())):
            card = page.get("card") or {}
            if card.get("cmd") == "undo_clear":
                return
            if card.get("cmd") == "clear":
                if card.get("clear_harness") == kind and "resume" not in card:
                    self.hist_update(page["id"], card={**card, "resume": resume})
                return

    def cmd_waiting(self, cmd, detail: str | None = None) -> None:
        """A queued shared /clear or /compact waiting for the desktop; P243: `detail` says why (background tasks, a dialog)."""
        if not isinstance(cmd, agents.Cmd) or cmd.cancelled or cmd.native_started:
            return
        text = detail or humane.of(self, "shared.wait_idle")
        self.hist_update(cmd.turn, text=text, end="open", card={"cmd": cmd.name, "cancel_wait": True})

    def workflow_cmd_waiting(self, cmd) -> bool:
        """Reuse the P144/P243 card, but name the workflow that actually owns turn_lock."""
        label = getattr(self, "task_label", None)
        lock = getattr(self, "turn_lock", None)
        if not label or not lock or not lock.locked() or cmd.name not in ("clear", "compact"):
            return False
        cmd.workflow_wait = True
        action = pick(self.lang, "压缩" if cmd.name == "compact" else "清空", "compaction" if cmd.name == "compact" else "clear")
        label = clean_line(str(label), 160)
        self.cmd_waiting(cmd, pick(self.lang, f"正在完成定时任务 {label}，{action}尚未发送。",
                                  f"Finishing scheduled task {label}; {action} has not been sent."))
        return True

    def cmd_lock_acquired(self, cmd) -> None:
        # Shared Claude retains P243's desktop wait and its precise native-send boundary.
        # Other adapters may send handoff prompts as soon as command() starts: close
        # withdrawal before that first possible write, never claim a late cancellation.
        if not cmd.workflow_wait or cmd.cancelled:
            return
        if isinstance(self.agent, SharedClaudeAgent):
            self.cmd_waiting(cmd)       # task no longer owns the lock; P243 explains any desktop wait
        else:
            cmd.native_started = True
            self.hist_update(cmd.turn, text=pick(self.lang, "开始处理命令。", "Processing the command."), card={})

    def cancel_waiting_cmd(self, s, turn) -> bool:
        page = self.hist.get(turn) if type(turn) is int else None
        if not page or page.get("end") != "open" or page.get("src", {}).get("dev") != s.device:
            return False
        if self.agent is None:
            return False
        items = [getattr(self.agent, "lock_cmd", None), self.agent.cur_cmd, *list(self.agent.q._queue)]
        for cmd in items:
            if (isinstance(cmd, agents.Cmd) and cmd.turn == turn and cmd.name in ("clear", "compact")
                    and (isinstance(self.agent, SharedClaudeAgent) or cmd.workflow_wait)
                    and not cmd.native_started and not cmd.cancelled):
                cmd.cancelled = True
                self.hist_update(turn, text=pick(self.lang, "已取消，未发送控制。", "Cancelled; control not sent."), end="stopped", card={})
                self.st.log("shared_control_cancel", cmd=cmd.name, device=s.device)
                return True
        return False

    def cmd_turn(self, s: Session | None, name: str, arg: str = "") -> int:
        """The page of a command, opened when it is asked for (its result fills it in)."""
        src = {"k": "cmd", "text": ("/" + name + (" " + arg if arg else ""))[:200]}
        if s is not None:
            src.update(dev=s.device, name=(s.name or s.device)[:64])
        return self.hist_add(src, "", "open")["id"]

    def shared_clear(self) -> bool:
        """Native SessionStart(clear), including desktop-origin clears, owns reset.
        Phone cmd_done must not reset again; shared Claude never claims native undo.
        """
        before = self.hist.meta()["count"]
        archived = self.hist.reset("shared-clear")
        self.desktop_turn = None
        self.hist_meta_all()
        ok = not before or bool(archived)
        if not ok:
            self.agent_notice(pick(self.lang, "桌面已清空，但手机历史归档失败；请检查本机磁盘。", "Desktop cleared, but phone history archive failed; check local disk."))
        return ok

    def cmd_done(self, cmd, res: "slash.Result") -> None:
        """Called by the Agent's queue when a command ran (agent.Cmd). /clear that cleared (and 「撤销清空」) also reset the
        history (§10.5): the command's page is closed, then archived / restored, and the result is the first page after."""
        if getattr(cmd, "cancelled", False):
            return
        self.st.log("slash", cmd=cmd.name, result=res.kind)
        self.activity("slash", cmd=cmd.name, result=res.kind, by=cmd.by)
        if cmd.name in ("compact", "clear", "new", "reset") and res.kind == "ok":
            hb = getattr(self, "heartbeat", None)  # P156
            if hb is not None:
                hb.forget(cmd.name)                #: the next beat carries the full heartbeat prompt again
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
        if getattr(item, "background_done_ids", None):
            self.background_done.complete(item)
        if getattr(item, "heartbeat_key", None) or getattr(item, "background_done_ids", None) \
                or getattr(item, "quota_resume_id", None) or getattr(item, "auto_notify", None) or getattr(item, "hook_notice", None):
            item.state = "cancelled"
            self.hist_update(item.turn, end="stopped")
            return
        if getattr(item, "sid", None) is not None:
            item.state = "cancelled"
            _rcpt(self, item.device, item.sid, "done")      # P313: dropped by the stop switch — never ran
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

    async def on_slash(self, s: Session, name: str, arg: str, confirm: bool, typed: bool, authorization=None) -> int:
        """A command from a ready session of a paired device (the only place one can come from). /stop and /help at once;
        the rest after the turn in front of it (the Agent's queue). Logged: command name + result class, never text.
        Returns the id of the command's history page (§10.5)."""
        name = slash.ALIAS.get(name, name)    # P341: /new from an older or third-party page is /clear
        by = s.name or s.device
        if name in TG_PAIR_CMDS:               # P283: 「重新绑定 Telegram」 — the host's own answer, a new pairing code
            self.st.log("slash_in", cid=s.cid, device=s.device, cmd="telegram-pair")
            turn = self.cmd_turn(s, "telegram-pair")
            await self.telegram_pair(by=by, turn=turn)
            return turn
        if name in reset_mod.RESET_CMDS:       # P332: /reset = ① (signed like /update; the run's steps go to 紧急修复)
            self.st.log("slash_in", cid=s.cid, device=s.device, cmd="reset")
            turn = self.cmd_turn(s, "reset")
            text, kind = await self.reset_desk.slash(s, authorization)
            self.cmd_card("reset", slash.Result(text, kind), by, turn)
            return turn
        if name in RELOAD_CMDS:                # P320: /reload — the host's own answer, no Agent, also while stopped
            self.st.log("slash_in", cid=s.cid, device=s.device, cmd="reload")
            turn = self.cmd_turn(s, "reload")
            res = await self.reload_config(trusted=False, by="phone")
            self.cmd_card("reload", slash.Result(res.get("text") or res.get("error") or "", "ok" if res.get("ok") else "error"), by, turn)
            return turn
        hc = friend_cmds.name_of(name)
        if hc:                                 # P73: answered here — no Agent, no model, no tokens; works while stopped
            self.st.log("slash_in", cid=s.cid, device=s.device, cmd=hc)
            turn = self.cmd_turn(s, hc, arg if hc == "add-friend" else "")
            res = self.friend_cmd(hc, arg)
            self.st.log("slash", cmd=hc, result=res.kind)
            self.activity("slash", cmd=hc, result=res.kind, by=by)
            self.cmd_card(hc, res, by, turn)
            return turn
        if name == "goal" and goal_mod.host_cmd(arg):   # P158: answered here, also while stopped / without an Agent
            self.st.log("slash_in", cid=s.cid, device=s.device, cmd="goal")
            turn = self.cmd_turn(s, "goal", arg)
            res = self.goal_cmd(goal_mod.host_cmd(arg), "phone:" + s.device)
            self.st.log("slash", cmd="goal", result=res.kind)
            self.activity("slash", cmd="goal", result=res.kind, by=by)
            self.cmd_card("goal", res, by, turn)
            return turn
        known = name in slash.WHITELIST + slash.INTERNAL
        self.st.log("slash_in", cid=s.cid, device=s.device, cmd=name if known else "other")
        turn = self.cmd_turn(s, name if known else "refused", arg if known else "")

        def done(res):
            self.st.log("slash", cmd=name if known else "other", result=res.kind)
            self.activity("slash", cmd=name if known else "other", result=res.kind, by=by)
            self.cmd_card(name if known else "refused", res, by, turn)
            return turn
        if name == "update":
            from . import phone_update
            why = "unpaired" if s.state != "ready" or not self.st.is_allowed(s.pub) else controls.check(
                self.st, self.nonces, self.channel, s.device, authorization or {}, "update", {})
            controls.log(self.st, action="update", device=s.device, result=why or "ok", obj={},
                         sig=(authorization or {}).get("sig"), n=(authorization or {}).get("n"), ts=(authorization or {}).get("ts"))
            if why or arg:
                return done(slash.Result("Send /update from a paired phone" if self.lang == "en" else "请从已配对手机发送 /update", "refused"))
            if not phone_update.reserve(self.st):
                return done(slash.Result("Upgrade in progress" if self.lang == "en" else "正在升级，请稍候", "info"))
            self.upg_end("started", by=s.device)    # P203: the confirmation card (if any) is answered by this signed /update
            self.cmd_card("update", slash.Result(humane.of(self, "update.started"), "info"), by, turn, interim=True)
            self.phone_upgrade_task = asyncio.create_task(self.phone_upgrade(turn, by))
            return turn
        if not self.agent:
            return done(slash.Result(slash.HELP if name == "help" else "还没接 Agent：在Agent J 主机上运行 `agentj agent claude --dir <目录>`。",
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
            return done(slash.Result(humane.of(self, "clear.phone_only"), "info"))
        if self.agent.status in ("working", "compacting") or not self.agent.q.empty():
            self.cmd_card(name, slash.Result(humane.of(self, "cmd.after_turn"), "info"), by, turn, interim=True)
        cmd = agents.Cmd(name, arg, by, turn)
        self.agent.submit(cmd)
        if not self.workflow_cmd_waiting(cmd) and isinstance(self.agent, SharedClaudeAgent) and name in ("clear", "compact"):
            self.cmd_waiting(cmd)
        return turn

    async def phone_upgrade(self, turn, by):
        from . import phone_update
        launched = False
        try:
            check = await asyncio.to_thread(update.check)
            if check["status"] != "newer":
                rec = {"reason": "already_current" if check["status"] == "current" else "unknown", "to": check.get("latest") or update.__version__}
                self.cmd_card("update", slash.Result(phone_update.text(rec, self.lang), "info" if check["status"] == "current" else "error"), by, turn)
                return
            await asyncio.to_thread(phone_update.launch, self.st)
            launched = True
            for _ in range(1800):
                await asyncio.sleep(1)
                rec = phone_update.take(self.st)
                if rec:
                    web, machine = await self._receipt_context() if rec.get("reason") in ("upgraded", "doctor_failed") else ("older", None)
                    self.cmd_card("update", slash.Result(phone_update.text(rec, self.lang, web, machine), "ok" if rec.get("result") == "ok" else "error"), by, turn)
                    return
            self.cmd_card("update", slash.Result("Upgrade timed out: run `agentj doctor`" if self.lang == "en" else "升级超时：在Agent J 主机运行 `agentj doctor`", "error"), by, turn)
        except Exception as e:
            from . import service as _svc
            if isinstance(e, _svc.ServiceError) and e.reason in _svc.NO_MANAGER:     # P278: no systemd to run the upgrade job
                self.cmd_card("update", slash.Result(_svc.MESSAGES[e.reason], "error"), by, turn)
                return
            self.cmd_card("update", slash.Result("Could not start upgrade: run `agentj update apply`" if self.lang == "en" else "无法启动升级：在Agent J 主机运行 `agentj update apply`", "error"), by, turn)
        finally:
            if not launched or not phone_update.busy(self.st):
                phone_update.release(self.st)

    def goal_cmd(self, what: str, by: str) -> "slash.Result":
        """P158 `/goal status` · `/goal stop` (stop only takes power away: no signature needed, like `agentj stop`)."""
        lang = card_lang(self.lang)
        if what == "stop":
            for a in list(self.asks.values()):          # a goal card still open on the phone is withdrawn too
                if a.tool == goal_mod.TOOL and not a.fut.done():
                    a.fut.set_result(("deny", None, None, None))
            d = self.goals.end("stopped", by=by[:16])
            if d is None:
                return slash.Result("No goal is running." if lang == "en" else "现在没有进行中的目标。", "info")
            return slash.Result("Goal mode ended; its one-time grant is withdrawn. Approvals ask as usual again."
                                if lang == "en" else "目标模式已结束，一次性授权已撤销；之后的审批照常弹卡。")
        self.goals.check()
        d = goal_mod.load(self.st)
        return slash.Result(goal_mod.status_text(self.st, d if d and d.get("state") in ("active", "proposed") else None, lang), "info")

    async def telegram_pair(self, key_env=None, by: str | None = None, turn: int | None = None) -> dict:
        """P283: start Telegram pairing. The code goes on a command card on the paired phones (history), never into the
        answer (the terminal or an Agent gets the bot name and how long it is valid), a log or Telegram."""
        from . import tg_pair, service
        try:
            changed = await asyncio.to_thread(service.reload_service_env, getattr(self, "preferences", {}))
            if changed:
                self._reload_runtime(True)
                if self.telegram is not None:
                    self.telegram.bot_id = None
        except service.ServiceError:
            self.st.log("telegram_env_reload", result="failed")
        lang = "en" if self.lang == "en" else "zh"
        if self.telegram is None:
            res = {"ok": False, "error": "telegram_unavailable"}
        else:
            res = await self.telegram.start_pairing(key_env)
        if res.get("ok"):
            card = slash.Result(tg_pair.card(lang, res.get("bot"), res.pop("code")), "info")
            res["shown_on"] = "phone"
        else:
            why = {"missing_key": res.get("detail") or "", "bot_key_rejected": pick(lang, "Telegram 不认这个 bot 密钥（getMe 失败）。请在 @BotFather 里核对后重新保存。", "Telegram rejected the bot key (getMe failed). Check it in @BotFather and save it again."),
                   "bad_name": pick(lang, "bot 密钥的环境变量名不合法。", "The bot key's environment variable name is not valid.")}.get(res.get("error"), "")
            card = slash.Result(pick(lang, "没能生成 Telegram 配对码。", "Could not make a Telegram pairing code.") + (" " + why if why else ""), "error")
        self.st.log("slash", cmd="telegram-pair", result=card.kind)
        self.cmd_card("telegram-pair", card, by, turn)
        return res

    def friend_cmd(self, name: str, arg: str = "", channel: str = "phone") -> "slash.Result":
        """P73 (ADR-A176) `/my-agent-id` · `/add-friend` (friend_cmds.py). Never adds a friend: that is the phone's signed fr_add."""
        lang = "en" if self.lang == "en" else "zh"
        if name == "my-agent-id":
            return friend_cmds.my_agent_id(self.st, lang, getattr(self.peers, "state", None))
        return friend_cmds.add_friend(arg, lang, channel)

    async def stop_turn(self, by: str, answered: bool = True) -> "slash.Result":
        """/stop: the stop switch's interrupt for the running turn (and a running scheduled task) — nothing is paused,
        the queue and every task's enabling stay as they are. P328: a running turn the phone cannot stop (Agent.cannot_halt)
        answers with that one explanation — never also 「现在没有在跑的一轮」. answered=False (Telegram's /stop, whose caller shows
        no answer): the explanation stays a notice on the phone."""
        busy, note = False, None
        if self.agent:
            self.agent.halt_quiet, self.agent.halt_note = answered, None
            try:
                busy = await self.agent.halt(clear_queue=False)
            finally:
                self.agent.halt_quiet, note = False, self.agent.halt_note
            note = note if isinstance(note, str) and note else None
        ran = await self.scheduler.stop_running()
        self.status_changed()
        if busy or ran:
            what = humane.of(self, "stop.what_both" if busy and ran else "stop.what_turn" if busy else "stop.what_task")
            done = humane.of(self, "stop.done", what=what)
            return slash.Result(done + "\n" + note if note else done)
        if note:
            return slash.Result(note, "info")
        return slash.Result(humane.of(self, "stop.none"), "info")

    # ============================================================ relay parity (PROTOCOL §10, PROMPT-33)
    def _caps(self, s: Session | None = None) -> dict:
        """What `approved` / `ready` announce (§10.0): our capability, local transcription, history — to a device that
        announced p33 itself (an older one gets exactly §3's message)."""
        if s is not None and not s.p33:
            return {}
        caps = [wire.CAP_P33, "heartbeat", wire.CAP_LT1, pair_card.CAP]
        if getattr(self, "telegram", None) is not None and self.telegram.can_forward():   # P118 B10: share → the owner's Telegram
            caps.append("tgfwd")
        if getattr(self, "telegram", None) is not None:   # P183: this host forwards a reply's file too (tg_fwd {mid}); the phone
            caps.append("tgfile")                          # shows the icon and, while Telegram is not linked, says where to link it
        caps.append("busyget")                 # P160 (§22): a tap on one 「在办」 row asks for its detail
        caps.append("contacts")          # P184 (§25): address book, contact cards, 「转发到邮箱」 (mail ready or not: the dialog says)
        caps.append(hsearch.CAP)                               # P188 (§28): the Agent J host answers hist_find
        caps += ["cards1", "upg1"]       # P203 (§29): signed open-cards snapshot · upgrade impact + confirmation card
        caps.append("systasks")                                # P207 §2: answers systask_list (the Agent J host's own scheduled tasks)
        caps.append("wf1")                                     # P244 §30: answers wf_list / wf_doc / skill_get (the five pages)
        caps.append(jcards.CAP)                                # P360 §34: Agent J Cards (jcard_list / jcard_get / jcard_set)
        caps.append(compose.CAP_MQ)                            # P286 §10.6: takes quote_more (≤ 5 quotes in one say)
        caps.append("hooks1")                                  # P366: answers hook_list / hook_set (Settings → Webhook)
        caps.append(compose.CAP_SYNC)                          # P398 §10.2: answers say_sync (queued sends after a reconnect)
        m = {"caps": caps, "asr": self.asr_state(), "hist": "on" if self.hist.on else "off"}
        m.update(self._host_kind())
        return m

    def _host_kind(self) -> dict:
        """P287 (§10.0): `plat` + `boot`, so a phone that later finds the Agent J host offline can say why in its own words
        (it cannot hear from us then). `boot` may need loginctl: worked out once in the background; until then only `plat`."""
        from . import service
        k = getattr(self, "_hk", None)
        if k is None:
            k = self._hk = {"plat": service.host_platform()}
            async def fill():
                b = await asyncio.to_thread(service.boot_mode)
                if b:
                    k["boot"] = b
            c = fill()
            try:
                self._bg(c, "host_kind")
            except RuntimeError:                        # no running loop (a synchronous caller): `plat` alone
                c.close()
        return dict(k)

    async def _bulk(self, s: Session) -> None:
        """§10.13: a ready, allowlisted p33 device may upload faster — tell the relay (it answers the device {"t":"rate"});
        never for a pairing in progress. A relay without BULK ignores the op."""
        if s.p33 and s.state == "ready" and self.st.is_allowed(s.pub) and self.sessions.get(s.cid) is s:
            with contextlib.suppress(Exception):
                await self._op(wire.OP_BULK, s.cid)

    async def on_tts(self, s, obj):
        from . import voice
        import base64
        def failed(why, exc=None):
            error = voice.speech_error(exc) if exc is not None else None
            self.st.log("tts_failed", why=why, exception_type=error.exception_type if error else None,
                        http_status=error.http_status if error else None)
        if self.sessions.get(s.cid) is not s or s.state != "ready" or not self.st.is_allowed(s.pub):
            failed("not_ready")
            return
        r=obj.get("r")
        if not wire.is_id22(r):
            failed("invalid_request")
            return
        if getattr(self, "tts_busy", False):
            failed("busy")
            return await self.send_app(s,{"t":"tts_end","r":r,"ok":False,"why":"busy"})
        turn=self.hist.get(obj.get("id"))
        if not turn or turn.get("end") not in ("done", "stopped", "failed"):
            failed("not_ready")
            return await self.send_app(s,{"t":"tts_end","r":r,"ok":False,"why":"not_ready"})
        self.tts_busy=True
        try:
            text=turn.get("reply",{}).get("text","")
            # One in-memory result only, bound to the exact reply and effective speech settings.
            # Retry after an iOS playback denial must not charge the provider again.
            import hashlib, json
            cache_key=hashlib.sha256(json.dumps([text,self.preferences.get("voice",{})],sort_keys=True).encode()).digest()
            cached=getattr(self,"tts_cache",None)
            if cached and cached[0]==cache_key:
                audio=cached[1]
            else:
                audio=await asyncio.to_thread(voice.synthesize,text,self.preferences)
                self.tts_cache=(cache_key,audio)
            for i in range(0,len(audio),24*1024):
                if self.sessions.get(s.cid) is not s or not self.st.is_allowed(s.pub):
                    failed("not_ready")
                    return
                if not await self.send_app(s,{"t":"tts_chunk","r":r,"i":i//(24*1024),"data":base64.b64encode(audio[i:i+24*1024]).decode()}):
                    failed("network")
                    return
            if not await self.send_app(s,{"t":"tts_end","r":r,"ok":True,"bytes":len(audio),"mime":"audio/wav"}):
                failed("network")
        except Exception as exc:
            error=voice.speech_error(exc)
            failed(error.why, error)
            await self.send_app(s,{"t":"tts_end","r":r,"ok":False,"why":error.why})
        finally:
            self.tts_busy=False

    async def on_auth(self, s: Session, t: str, obj: dict) -> None:
        """P258 (PROTOCOL §31): the paired phone's 「AI 登录救援」. auth_get → the view; auth_start {kind, route} → owner passkey
        card, then the official login flow (device code / authorize URL + sealed code card / sealed token card); auth_press
        {kind, id} → re-check pressing one catalogued confirm screen's fixed keys (passkey); auth_cancel. Every answer:
        auth_res {r, ok[, why], view}. No code or token ever travels in these messages (the secret card carries it, sealed)."""
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        ar = self.auth_rescue

        async def answer(res: dict) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "auth_res", "r": r, "ok": bool(res.get("ok", True)),
                                        **({"why": res["why"]} if res.get("why") else {}),
                                        "view": await asyncio.to_thread(ar.view)})
        kind, route, bid = obj.get("kind"), obj.get("route"), obj.get("id")
        if t == "auth_get":
            return await answer({"ok": True})
        if t == "auth_cancel":
            return await answer(await ar.cancel_flow())
        if not isinstance(kind, str) or len(kind) > 16:
            return await answer({"ok": False, "why": "unsupported"})
        self.st.log("auth_rescue_req", device=s.device, agent=kind, status=t)
        if t == "auth_start":
            async def go():
                await answer(await ar.start(kind, route if isinstance(route, str) else ""))
            self._bg(go(), "auth_start")     # the passkey card waits for the owner; never hold the relay reader
            return
        if t == "auth_press":
            async def go2():
                await answer(await ar.press(kind, bid if isinstance(bid, str) else ""))
            self._bg(go2(), "auth_press")
            return

    def auth_signal(self, kind: str, text) -> None:
        """P258: an adapter's failure text → classify (login / balance / rate_limit) → the rescue's dot and alert."""
        try:
            self.auth_rescue.text_signal(kind, text)
        except Exception:  # noqa: BLE001 — detection must never break the failure notice itself
            pass
    async def on_imsg(self, s: Session, t: str, obj: dict) -> None:
        """P274 (§10.21) the iMessage card: `imsg_check` = a fresh doctor (then one scan), `imsg_fda` = open System Settings
        at Full Disk Access on the Mac. → `imsg_res {r, ok, why?, check?}` (no message text), then a `preferences`
        broadcast so every phone's card shows the new status."""
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        from . import service as _svc
        im = getattr(self, "imessage", None)

        async def answer(res: dict) -> None:
            m = {"t": "imsg_res", "r": r, "ok": bool(res.get("ok"))}
            if not res.get("ok"):
                m["why"] = str(res.get("status") or res.get("why") or "error")[:32]
            chk = {k: res.get(k) for k in ("status", "grant_to", "hint") if res.get(k)}
            if chk:
                m["check"] = chk
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, m)
        if im is None or _svc.platform() != "macos":
            return await answer({"ok": False, "why": "not_macos"})
        self.st.log(t, device=s.device)

        async def run() -> None:
            res = await im.check() if t == "imsg_check" else await asyncio.to_thread(im.open_fda)
            await answer(res)
            self._post(self._send_ready, lambda s2: self.preferences_msg())
        self._bg(run(), t)

    def on_news(self, s: Session, t: str, obj: dict) -> asyncio.Task | None:
        """P374 (PROTOCOL §10.20a): read / switch this host's person's Daily AI News through one signed `/v1/host/news`
        (cloud.news); P393 #128: `news_email` mails a code to the seat person's own address (cloud.news_email) and
        `news_verify` confirms it (cloud.news_verify). In a thread with an 8 s budget, never on the reader, ≤ NEWS_INFLIGHT
        at once across all four. The answer goes to the asking session only; the log line has status words only, never the
        e-mail or the code."""
        on = obj.get("on")
        if t == "news_set" and not isinstance(on, bool):
            return None
        email, code = obj.get("email"), obj.get("code")
        if t in ("news_email", "news_verify") and not isinstance(email, str):
            return None
        if t == "news_verify" and not isinstance(code, str):
            return None
        lang = obj.get("lang") if obj.get("lang") in ("zh", "en") else self.onboarding_lang()
        r = obj.get("r")
        rid = {"r": r} if wire.is_rid(r) else {}

        def call(post):
            if t == "news_email":
                return cloud.news_email(self.st, email, lang, post=post)
            if t == "news_verify":
                return cloud.news_verify(self.st, email, code, post=post)
            return cloud.news(self.st, on if t == "news_set" else None, post=post)

        async def answer(m: dict) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "news_res", **rid, **m})

        async def run() -> None:
            res = None
            try:
                if self._news_inflight >= NEWS_INFLIGHT:
                    res = cloud.NewsResult("rate_limited", status="busy")
                else:
                    self._news_inflight += 1
                    try:
                        post = lambda url, payload: cloud.post_json(url, payload, timeout=NEWS_TIMEOUT - 1)  # noqa: E731
                        res = await asyncio.wait_for(asyncio.to_thread(call, post), NEWS_TIMEOUT)
                    finally:
                        self._news_inflight -= 1
            except asyncio.TimeoutError:
                res = cloud.NewsResult("unreachable", status="timeout")
            except Exception:  # noqa: BLE001 — a bad key file / state read: the phone gets "fail", never a hang
                res = cloud.NewsResult("fail", status="error")
            self.st.log(t, device=s.device, status=res.kind, http=res.status, **({"on": res.on} if res.kind == "ok" else {}))
            if cloud.news_is_state(res):
                await answer({"ok": True, "on": res.on, "email": res.email, "sample_url": res.sample_url,
                              "queued": res.queued, "seats": res.seats, **({"need_email": True} if res.kind == "need_email" else {})})
            elif res.kind == "sent":
                await answer({"ok": True, "sent": True, "expires_in": res.expires_in})
            else:
                await answer({"ok": False, "problem": cloud.news_problem(res.kind),
                              **({"retry_after": res.retry_after} if res.retry_after else {})})
        return self._bg(run(), t)

    async def on_mail(self, s: Session, t: str, obj: dict) -> None:
        """P170 (PROTOCOL §10.20): the paired phone's e-mail settings. The app password never comes this way — 「设置授权码」
        opens the sealed secret card (elevate.py), the host logs in with it, then stores it encrypted."""
        from . import mail as mail_mod
        r = obj.get("r")
        if not wire.is_rid(r) or self.mail is None:
            return

        async def answer(ok: bool, why: str | None = None, **extra) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "mail_res", "r": r, "ok": ok, **({"why": why} if why else {}), **extra,
                                        "view": await asyncio.to_thread(self.mail.view)})
        if t == "mail_get":
            return await answer(True)
        if t == "mail_set":
            raw = obj.get("cfg")
            if not isinstance(raw, dict) or len(json.dumps(raw)) > 8000:
                return await answer(False, "shape")
            try:
                old = mail_mod.read_config(self.st)
                new = {**old, **{k: v for k, v in raw.items() if k != "generation"}}
                if raw.get("preset") and raw.get("address"):
                    if "username" not in raw:
                        new["username"] = ""          # the preset's sign-in name = the (new) address
                    new = mail_mod.apply_preset(new, raw["preset"], raw["address"])
                    if raw.get("preset") == "custom":
                        new.update({k: raw[k] for k in ("imap", "smtp") if k in raw})
                cfg = self.mail.set_config(new)
            except mail_mod.MailError as e:
                return await answer(False, e.why)
            self.st.log("mail_set", device=s.device, allow=len(cfg["allow"]), enabled=cfg["enabled"])
            return await answer(True)
        if t == "mail_key":                 # one answer, when the card ends (the page waits up to the card's 10 minutes)
            async def card():
                res = await self.mail.secret_card()
                receipt = res.get("receipt") or {}
                await answer(res.get("result") == "saved", None if res.get("result") == "saved" else
                             (res.get("detail") or res.get("why") or res.get("result")), card=res.get("result"), verify=receipt.get("verify"), detail=receipt.get("detail"))
            self._bg(card(), "mail_key")
            return
        if t == "mail_test":
            async def test():
                cfg = mail_mod.read_config(self.st)
                pw = mail_mod.load_secret(self.st, cfg["address"]) if cfg["address"] else None
                if not pw:
                    return await answer(False, "no_password")
                res = await asyncio.to_thread(mail_mod.test_connection, cfg, pw)
                st2 = mail_mod.read_state(self.st)
                st2["auth_seen"] = res.get("auth_sample")
                mail_mod.save_state(self.st, st2)
                self.st.log("mail_test", imap=res["imap"], smtp=res["smtp"])
                await answer(res["ok"], None if res["ok"] else (res["imap"] if res["imap"] != "ok" else res["smtp"]),
                             test={k: res[k] for k in ("imap", "smtp", "auth_sample")})
            self._bg(test(), "mail_test")
            return
        if t == "mail_check":
            async def check():
                res = await self.mail.check()
                await answer(bool(res.get("ok")), res.get("why"), check={k: v for k, v in res.items() if k != "why"})
            self._bg(check(), "mail_check")

    async def _ctl_mail(self, w: asyncio.StreamWriter, req: dict, claimed: bool) -> None:
        """P170 `agentj mail set|allow|deny|on|off`: from the owner's terminal it applies at once; from inside an Agent a change
        that widens who reaches the Agent (a new allowlisted address, weaker sender checks, turning it on, another server)
        becomes an approval card on the phone, applied only when the owner allows and nothing changed meanwhile.
        P296: the call waits for that card — allowed → `{ok, applied}`; declined / timed out / stale → `{ok: false, why}` (the
        CLI exits non-zero). An allowlisted address also means mail TO it goes out without a card, and the card says so."""
        from . import mail as mail_mod
        if self.mail is None:
            return await self._ctl_send(w, {"ok": False, "error": "not_running"})
        old = mail_mod.read_config(self.st)
        try:
            new = mail_mod.validate({**old, **{k: v for k, v in (req.get("cfg") or {}).items() if k != "generation"}})
        except mail_mod.MailError as e:
            return await self._ctl_send(w, {"ok": False, "error": e.why})
        keys = [] if claimed else mail_mod.widening(old, new)
        if not keys:
            cfg = self.mail.set_config(new)
            self.st.log("mail_set", device="terminal" if claimed else "agent", allow=len(cfg["allow"]), enabled=cfg["enabled"])
            return await self._ctl_send(w, {"ok": True, "applied": True})
        self.st.log("mail_card", keys=",".join(keys)[:200])
        zh = card_lang(self.lang) != "en"
        adds = [k.split("+", 1)[1] for k in keys if k.startswith(("allow+", "owner+"))]
        lines = [("邮件设置改动（Agent 发起）：" if zh else "Mail settings change (asked by the Agent): ") + "、".join(keys)]
        if adds:
            lines.append(("加入邮件白名单：" if zh else "Add to the mail allowlist: ") + "、".join(adds))
            lines.append("加入后：他的来信会直接处理，给他发信不用再确认" if zh else
                         "Once added: their mail is handled directly, and mail to them goes out without asking you")
        ans = await self.ask("agentj mail", {"changes": keys, "summary": "\n".join(lines)}, batch=False)
        if ans.get("behavior") != "allow":
            self.st.log("mail_card_done", result="deny")
            return await self._ctl_send(w, {"ok": False, "why": "denied", "keys": keys,
                                            "detail": "主人没在手机上确认，邮件设置没有改动 / the owner did not confirm on the phone; "
                                                      "nothing changed"})
        if mail_mod.read_config(self.st) != old:
            self.st.log("mail_card_done", result="stale")
            return await self._ctl_send(w, {"ok": False, "why": "stale", "keys": keys,
                                            "detail": "邮件设置在等待确认期间被改过，这次没有生效，请重新发起 / the mail settings "
                                                      "changed while waiting; not applied, ask again"})
        self.mail.set_config(new)
        self.st.log("mail_card_done", result="applied")
        await self._ctl_send(w, {"ok": True, "applied": True, "keys": keys})

    async def on_tg_fwd(self, s: Session, r: str, turn_id, mid=None) -> None:
        if self.telegram is None:
            res = {"ok": False, "why": "not_configured"}
        elif mid is not None:      # P183: one file a reply showed (its §13 media id) instead of the reply's words
            res = await self.telegram.forward_media(mid, s.device) if wire.is_id22(mid) else {"ok": False, "why": "not_found"}
        else:
            res = await self.telegram.forward(turn_id, s.device)
        if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
            await self.send_app(s, {"t": "tg_fwd_res", "r": r, **{k: v for k, v in res.items() if k in ("ok", "why", "parts")}})

    async def on_busy_get(self, s: Session, r: str, wid: str) -> None:
        from . import busy as busy_mod
        f = self.busy.find(wid)                          # copies, here on the loop (the Board changes only here)
        try:
            d = await asyncio.to_thread(busy_mod.detail_of, *f) if f else None
        except Exception:  # noqa: BLE001 — display only
            d = None
        if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
            await self.send_app(s, {"t": "busy_info", "r": r, "id": wid, "item": d})

    async def on_p33(self, s: Session, t: str, obj: dict) -> None:
        if t == "ping":
            r = obj.get("r")
            if wire.is_rid(r):
                await self.send_app(s, {"t": "pong", "r": r})
            return
        if t == "notice_read":
            nid = obj.get("id")
            if isinstance(nid, str) and notices.ID.fullmatch(nid) and notices.mark_read(self.st, nid):
                self.reporter.trigger("notice_read")
                self.st.log("notice_read", id=nid, device=s.device)
            return
        if t == "pref_set":
            return await self.on_pref_set(s, obj)
        if t == "res_watch":               # P181 (§22.1): the load card opened (renewed) / closed on this session
            return self.on_res_watch(s, obj)
        if t == "dev_rename":
            return await self.on_dev_rename(s, obj)
        if t == "tts_get":
            self._bg(self.on_tts(s, obj), "tts")
            return
        if t.startswith("mail_"):          # P170: the e-mail channel's settings page
            return await self.on_mail(s, t, obj)
        if t in ("news_get", "news_set", "news_email", "news_verify"):  # P374 · P393 #128: Daily AI News
            return self.on_news(s, t, obj)
        if t.startswith("auth_"):          # P258: AI 登录救援
            return await self.on_auth(s, t, obj)
        if t in ("imsg_check", "imsg_fda"):  # P274 (§10.21): the iMessage card
            return await self.on_imsg(s, t, obj)
        if t == "qr_cancel":               # P314 (§32): only stops an automatic action (like grant_off): no signature
            r, rid = obj.get("r"), obj.get("id")
            if not wire.is_rid(r):
                return
            ok = isinstance(rid, str) and len(rid) <= 32 and self.quota_resume.cancel(rid, by=s.device)
            if ok:
                self.activity("quota_resume_cancel", by=s.name or s.device)
            await self.send_app(s, {"t": "qr_res", "r": r, "ok": bool(ok), **({} if ok else {"why": "gone"}),
                                    "items": self.quota_resume.view()})
            return
        if t == "tg_fwd":                  # P118 (B10): one tap → this reply to the owner's own Telegram private chat
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            self._bg(self.on_tg_fwd(s, r, obj.get("id"), obj.get("mid")), "tg_fwd")   # Telegram may take seconds: never hold the reader
            return
        if t == "busy_get":                # P160 (§22): one row's detail — read-only, never starts / stops anything
            r, wid = obj.get("r"), obj.get("id")
            if wire.is_rid(r) and isinstance(wid, str) and 0 < len(wid) <= 40:
                self._bg(self.on_busy_get(s, r, wid), "busy_get")   # systemctl / a file read: never hold the reader
            return
        if t == "say":
            return await self.on_say(s, obj)
        if t == "cmd_cancel":
            # Already authenticated, ready and allowlisted by _app; bound to the originating device/page.
            self.cancel_waiting_cmd(s, obj.get("turn"))
            return
        if t == "turn_wait":
            # P151b (追加 4): the stall card's 「继续等」 — the running phone turn waits another round; 「中断」 is /stop.
            more = getattr(self.agent, "wait_more", None)
            if type(obj.get("turn")) is int and obj.get("turn") == self.cur_turn and more is not None:
                more.set()
                self.st.log("turn_wait_more", device=s.device)
            return
        if t == "say_cancel":
            sid = obj.get("sid")
            if not wire.is_id22(sid):
                return
            r, send = await self.sends.cancel(s.device, sid)
            if r == "cancelled" and send is not None:
                self.uploads.release(send.blobs)        # the attachments are staged again under the same ids
                _rcpt(self, s.device, sid, "done")       # P313: withdrawn — never ran, never will
                self.hist_update(send.turn, end="stopped", card={"withdrawn": True})
                self.st.log("say_cancel", cid=s.cid, device=s.device, result=r)
                self._post(self._send_device, s.device, {"t": "say_state", "sid": sid, "s": "cancelled"})
            await self.send_app(s, {"t": "say_cancel_res", "sid": sid, "r": r})
            return
        if t == "say_sync":
            # P398 (#155): after a reconnect the phone asks where its still-queued sends are — a say_state lost while it was
            # away (the Agent took it, it was withdrawn elsewhere) would otherwise leave 「排队中 · 取消」 on a message the
            # Agent already has. The answer is this host's own record (compose.Sends, then the receipts on disk), never a guess.
            sids = obj.get("sids")
            if not isinstance(sids, list):
                return
            rc = getattr(self, "receipts", None)
            for sid in sids[:compose.MAX_SENDS * 2]:
                if not wire.is_id22(sid):
                    continue
                st = say_sync_state(self.sends.get(s.device, sid), rc.state(s.device, sid) if rc is not None else None)
                if st is not None:
                    await self.send_app(s, {"t": "say_state", "sid": sid, "s": st})
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
                self._bg(self._asr_take(s.device, take), "asr_take")
            return
        if t == "blob_drop":
            for m in self.uploads.drop(s.device, obj.get("bid")):
                await self.send_app(s, m)
            return
        if t == "media_get":
            return self.on_media_get(s, obj)
        if t == "hist_read":
            if self.sessions.get(s.cid) is not s or s.state != "ready" or not self.st.is_allowed(s.pub):
                return
            try:
                changed = self.readmark.see(self.hist, obj.get("epoch"), obj.get("id"))
            except OSError:
                self.st.log("hist_read_failed", device=s.device)
                return
            if changed:
                self.st.log("hist_read", device=s.device, epoch=self.hist.epoch, read=self.readmark.value(self.hist))
                self._post(self._send_p33, lambda s: {"t": "hist_read", "epoch": self.hist.epoch, "read": self.readmark.value(self.hist)})
            else:   # P321: a lower / refused receipt — this remote learns the host's mark (and stops retrying it)
                await self.send_app(s, {"t": "hist_read", "epoch": self.hist.epoch, "read": self.readmark.value(self.hist)})
            return
        if t == "hist_get":
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            lim = obj.get("limit")
            lim = lim if type(lim) is int and 1 <= lim <= history.PAGE_MAX else history.PAGE_MAX
            b, a = obj.get("before"), obj.get("after")
            turns, more = self.hist.page(before=b if type(b) is int else None, after=a if type(a) is int else None, limit=lim)
            return await self.send_app(s, {"t": "hist_page", "r": r, **self.hist_metadata(), "turns": turns, "more": more})
        if t == "hist_find":                   # P188 (§28): search the current epoch; answered to the asking session only
            r, q, sort, lim = obj.get("r"), obj.get("q"), obj.get("sort", "score"), obj.get("limit", hsearch.HITS_MAX)
            if not wire.is_rid(r):
                return
            if not isinstance(q, str) or not q.strip() or text_units(q) > hsearch.Q_MAX or sort not in hsearch.SORTS \
                    or type(lim) is not int or not 1 <= lim <= hsearch.HITS_MAX:
                return await self.send_app(s, {"t": "hist_found", "r": r, "error": "shape"})
            turns = list(self.hist.turns.values())
            hits, n = await asyncio.to_thread(hsearch.search, turns, q, sort, lim)
            self.st.log("hist_find", device=s.device, count=n)          # never the query or the words
            return await self.send_app(s, {"t": "hist_found", "r": r, "epoch": self.hist.epoch, "sort": sort, "hits": hits,
                                           "n": n, "more": n > len(hits)})
        if t == "menu_get":
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            skills = []
            if self.agent and self.agent.kind == "claude":
                skills = [x for x in (getattr(self.agent, "init", {}) or {}).get("skills") or [] if isinstance(x, str)]
            wd = self.agent_cfg["dir"] if self.agent_cfg else None
            res = await asyncio.to_thread(menu.for_phone, wd, skills, self.lang, preferences.get(self.preferences, "menu.items", []))
            return await self.send_app(s, {"t": "menu", "r": r, **res, "upgrade": update.phone_view(self.st), "reload": True,
                                           "reset": True})   # P320: ≡ Reload · P332: ≡ 重置连接
        if t in ("notify_get", "notify_set"):          # P365: which automations may post here
            from . import notify
            return await notify.on_phone(self, s, t, obj)
        if t in ("hook_list", "hook_set") and self.hooks is not None:   # P366: the 「Webhook」 list (no secret ever leaves)
            return await self.hooks.on_phone(s, t, obj)
        if t in ("keys_get", "keys_op", "keys_set"):   # P352: names only; a copied value goes to this session alone
            from . import keys
            return await keys.on_phone(self, s, t, obj)
        if t in ("provider_get", "provider_key", "asr_install"):
            from . import provider_runtime
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            if t == "provider_get":
                ps = [{"id": k, "name": k, "key_editable": bool(provider_runtime.key_name(k))}
                      for k in provider_runtime.configured()]
                return await self.send_app(s, {"t": "providers", "r": r, "providers": ps})
            if t == "provider_key":
                pid = obj.get("provider")
                ok = isinstance(pid, str) and bool(provider_runtime.key_name(pid))
                await self.send_app(s, {"t": "provider_res", "r": r, "ok": ok, **({} if ok else {"why": "unsupported"})})
                if ok:
                    self._bg(provider_runtime.request_key(self, pid), "request_key")
                return
            delivery = getattr(self, "_asr_install_delivery", None)
            if getattr(self, "_asr_installing", False) or (delivery is not None and not delivery.done()):
                return await self.send_app(s, {"t": "asr_install_res", "r": r, "ok": False, "why": "busy"})
            self._asr_installing = True
            async def install_asr():
                stage = "prepare"
                loop = asyncio.get_running_loop()
                # Best-effort delivery is independent of local activation. Bound memory even if
                # a relay write stalls; never cancel an encrypted write and then reuse its nonce.
                updates = asyncio.Queue(maxsize=4)
                def enqueue(message):
                    if updates.full():
                        updates.get_nowait()  # coalesce old progress, always retain the terminal result
                    updates.put_nowait(message)
                def update(value):
                    nonlocal stage
                    stage = value
                    enqueue({"t": "asr_install_progress", "r": r, "stage": value})
                def progress(value):
                    if value in ("download", "extract", "install", "test"):
                        loop.call_soon_threadsafe(update, value)
                async def report_progress():
                    while True:
                        message = await updates.get()
                        try:
                            await self.send_app(s, message)
                        except Exception:
                            pass  # detached/disappearing remote never aborts the local installer
                        if message["t"] == "asr_install_res":
                            return
                self._asr_install_delivery = self._bg(report_progress(), "asr_install_progress")
                result = {"t": "asr_install_res", "r": r, "ok": False, "why": "failed"}
                try:
                    code = await asyncio.to_thread(self.asr.install, state_dir=self.st.root, yes=True, out=lambda _: None, progress=progress)
                    # Flush thread-safe callbacks to capture the actual last installer stage.
                    await asyncio.sleep(0)
                    if code == 0 and obj.get('enable') is True:
                        await self.set_pref('voice.asr.mode', 'local')
                        await self.set_pref('voice.asr.engine', 'auto')
                    state = self.asr_state()
                    ok = code == 0 and state == 'ready'
                    self.st.log("asr_install", device=s.device, result="ok" if ok else "failed")
                    result = {"t": "asr_install_res", "r": r, "ok": ok, "state": state, **({} if ok else {'why': state if code == 0 else stage + '_failed'})}
                except Exception:
                    pass  # report the fixed failure code, never exception text
                finally:
                    # Transport backpressure cannot hold installation ownership or prevent activation.
                    self._asr_installing = False
                    enqueue(result)
            self._bg(install_asr(), "install_asr")
            return
        if t == "models_get":
            r = obj.get('r')
            if not wire.is_rid(r):
                return
            agent = self.agent
            # Local config/cache is immediately usable, even when a native API stalls.
            if agent and agent.kind == 'opencode':
                agent._set_models({})
            await self.send_app(s, {**self._models_msg(), 'r': r})
            refresh = getattr(agent, 'refresh_models', None)
            pending = getattr(self, '_catalogue_refresh_task', None)
            if callable(refresh) and (pending is None or pending.done()):
                async def refresh_catalogue():
                    from .agent_opencode import HTTPError
                    with contextlib.suppress(OSError, ValueError, HTTPError, AttributeError, asyncio.TimeoutError):
                        await asyncio.wait_for(refresh(), MODEL_REFRESH_TIMEOUT)
                # _set_models calls models_changed when native refresh succeeds. Never
                # await the native API in the relay frame reader (ping/provider_get).
                self._catalogue_refresh_task = asyncio.create_task(refresh_catalogue())
            return
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
        _hb_touch(self)                     # P156: a phone conversation pushes the heartbeat back
        await self._setup_round_trip(s)
        self.emit("msg", device=s.device, name=s.name, text=clean(text, wire.MAX_TEXT_P33))
        self.st.log("msg_in", cid=s.cid, device=s.device)
        entry = self._remember("device", text, device=s.device, name=s.name)
        for o in list(self.sessions.values()):     # other older phones see what was said; the sender already shows it
            if o is not s and o.state == "ready" and not o.p33 and self.st.is_allowed(o.pub):
                await self.send_app(o, self._render(entry, o))
        src = {"k": "phone", "dev": s.device, "name": (s.name or s.device)[:64], "text": text}
        if getattr(s,"source_kind",None)=="telegram":
            src["k"]="telegram"
            # P127: the Agent's source line is not the owner's words; the page's label already says Telegram
            from .telegram import OWNER_PRIVATE
            src["text"]=text.removeprefix(OWNER_PRIVATE)
        elif getattr(s,"source_kind",None)=="email":     # P170: the page shows 「邮件」 and the mail's own words
            src["k"]="email"
            src["text"]=text.split("\n",2)[-1] if text.startswith("【") else text
        if quote:
            src["quote"] = quote
        if blobs:
            src["att"] = [{"name": b.name[:128], "mime": b.mime, "bytes": b.size, "kind": b.kind} for b in blobs]
        turn = self.hist_add(src, "", "open" if self.agent else "done")
        if not self.agent:
            return None
        send = compose.Send(s.device, sid or secrets.token_urlsafe(16), turn["id"], blobs=blobs, by=s.name or s.device)
        send.onboarding_note = (self.speaker_note(s) if getattr(s, "source_kind", None) != "telegram" else "") + self.onboarding_note()
        gc = slash.parse(text) if not blobs else None
        if gc and gc[0] == "goal" and gc[1] and not goal_mod.host_cmd(gc[1]):   # P158: `/goal <what>` → the Agent proposes
            if provenance.is_owner(getattr(s, "provenance", None)):
                self.goals.owner_asked()       # P336: only the owner's own /goal may start without the opening card
            send.onboarding_note += goal_mod.start_note(gc[1], card_lang(self.lang))
            text = goal_mod.agent_words(gc[1], card_lang(self.lang))     # P198: never Claude Code's own /goal
        send.source = getattr(s, "provenance", None)      # B1: set only by host code (telegram.incoming), never from text
        files = [{"path": b.path, "mime": b.mime, "bytes": b.size, "origin": b.origin, "secs": b.secs,
                  "voice": getattr(b, "voice", "")} for b in blobs]
        # P166: every audio / video attachment with its private copy (a picked m4a too); Telegram transcribes its own first
        voice = [f for f in files if f["voice"] or (f["origin"] == "recording" and f["mime"].startswith("audio/"))]
        text = self._meeting_text(s, text, bool(voice))
        if voice:
            send.ready = asyncio.get_running_loop().create_future()
            send.prep = asyncio.create_task(self._prep_say(send, text, files, voice, quote_text))
        else:
            send.text = compose.render(text, files, self.lang, quote_text) + send.onboarding_note
        self.sends.add(send)
        self.turn_by = s.name or s.device
        self.quota_resume.owner_spoke("main")   # P314: the owner moved on by hand; no automatic continue on top
        await self._model_set_flush()        # P240: a pill switch still held by the 2 s backstop goes ahead of this message
        self.agent.submit(send)
        return send

    def _meeting_text(self, s: Session, text: str, has_audio: bool) -> str:
        """P182: what the Agent reads for `/会议总结` — the owner's words become one instruction (meeting.instruction); the
        addresses in them (and, within meeting.FOLLOW_S, in the owner's next phone messages) become the only recipients
        `agentj mail notes` will send to. Only a paired phone's own words count (not Telegram groups, e-mail or friends)."""
        if getattr(s, "source_kind", None):
            return text
        words = meeting.parse(text)
        try:
            if words is None:
                meeting.follow_up(self.st, text)
                return text
            meeting.grant(self.st, meeting.addresses(words), command=True)
            from . import mail as mail_mod
            cfg = mail_mod.read_config(self.st)
            ready = bool(cfg.get("address")) and mail_mod.has_secret(self.st, cfg["address"])
            self.st.log("meeting_cmd", device=s.device, audio=has_audio, to=len(meeting.addresses(words)))
            return meeting.instruction(self.lang, words, has_audio, None if has_audio else meeting.last_group(self.st), ready)
        except Exception:   # noqa: BLE001 — a broken grants file must never lose the owner's message
            return text

    async def on_say(self, s: Session, obj: dict) -> None:
        sid = obj.get("sid")
        if not wire.is_id22(sid):
            return

        async def res(**kw):
            await self.send_app(s, {"t": "say_res", "sid": sid, **kw})
        text, att, reply_to, excerpt = obj.get("text"), obj.get("att"), obj.get("reply_to"), obj.get("excerpt")
        more = obj.get("quote_more")    # P286: quotes 2…5 — [{"id": <turn id>[, "ex": "<≤ 2 000>"]}], after reply_to's
        if not isinstance(text, str) or (att is not None and not isinstance(att, list)) \
                or (reply_to is not None and type(reply_to) is not int) or (excerpt is not None and reply_to is None) \
                or (more is not None and (reply_to is None or not isinstance(more, list) or not 1 <= len(more) < compose.QUOTES_MAX
                    or not all(isinstance(q, dict) and type(q.get("id")) is int and (q.get("ex") is None or isinstance(q["ex"], str))
                               for q in more))) \
                or not isinstance(obj.get("ts", 0), int):
            return await res(ok=False, why="shape")
        exs = [excerpt] + [q.get("ex") for q in more or []]
        why = wire.text_problem(text) or next((w for x in exs if x is not None
                                               for w in [wire.text_problem(x, compose.EXCERPT_MAX)] if w), None)
        if why:
            return await res(ok=False, why=why)
        if wire.units(text) + sum(wire.units(x) for x in exs if x) > wire.MAX_TEXT_P33:
            return await res(ok=False, why="too_long")
        if not text.strip() and not att:
            return await res(ok=False, why="shape")
        rc = getattr(self, "receipts", None)
        if self.sends.used(s.device, sid) or (rc is not None and rc.used(s.device, sid)):     # P313: also across a restart
            return await res(ok=False, why="dup")
        if self.sends.full(s.device):       # 16 of this device's sends still wait: refuse, never evict one (P33-C03)
            return await res(ok=False, why="too_many")
        is_meeting = meeting.parse(text) is not None      # P182: /会议总结 · /meeting-notes is a message for the Agent, never refused
        hc = slash.parse(text) if not att and reply_to is None and not is_meeting else None
        if hc and (friend_cmds.name_of(hc[0]) or hc[0] in TG_PAIR_CMDS or hc[0] in RELOAD_CMDS or hc[0] in reset_mod.RESET_CMDS
                   or hc[0] == "goal" and goal_mod.host_cmd(hc[1])):
            # P73: /my-agent-id · /add-friend; P283: /telegram-pair; P158: /goal status · /goal stop — the host's own answer, also stopped / no Agent
            self.sends.add(compose.Send(s.device, sid, 0, state="delivered"))
            await _rcpt_recv(self, s.device, sid)
            turn = await self.on_slash(s, hc[0], hc[1], confirm=False, typed=True)
            _rcpt(self, s.device, sid, "done")
            self._post(self.send_app, s, {"t": "say_res", "sid": sid, "ok": True, "state": "delivered", "turn": turn})
            return
        if self.stopped() and self.agent:
            self.st.log("msg_refused", cid=s.cid, device=s.device, reason="estop")
            self.activity("message_refused", by=s.name or s.device, text=text[:120])
            return await res(ok=False, why="stopped")
        refusal = self.seat_refusal()           # P185: no usable seat → nothing reaches the Agent; the phone hears why
        if refusal:
            self.st.log("msg_refused", cid=s.cid, device=s.device, reason="seat")
            self.seat_note(refusal)
            return await res(ok=False, why="seat")
        if not self.agent:
            return await res(ok=False, why="no_agent")
        cmd = slash.parse(text) if not att and reply_to is None and not is_meeting else None
        if cmd and cmd[0] != "goal" and cmd[0] not in slash.AGENT_CMDS and not self.agent.passthrough(cmd[0]):   # P155 /switch-harness, P158 `/goal <what>` → the Agent
            self.sends.add(compose.Send(s.device, sid, 0, state="delivered"))
            await _rcpt_recv(self, s.device, sid)
            turn = await self.on_slash(s, cmd[0], cmd[1], confirm=False, typed=True)
            _rcpt(self, s.device, sid, "done")
            self._post(self.send_app, s, {"t": "say_res", "sid": sid, "ok": True, "state": "delivered", "turn": turn})
            return
        quote = quote_text = None
        if reply_to is not None:
            items = []
            for qid, ex in [(reply_to, excerpt)] + [(q["id"], q.get("ex")) for q in more or []]:
                qt = self.hist.get(qid)
                if qt is None:
                    return await res(ok=False, why="reply_unknown")
                items.append((qt, ex or None))
            quote = compose.quote_info(*items[0], self.lang)
            if len(items) > 1:              # src.quote stays the first (older phones draw it); src.quotes = all, in order
                quote = dict(quote, more=[compose.quote_info(qt, ex, self.lang) for qt, ex in items[1:]])
            quote_text = compose.quotes_block(items, self.lang)
        try:
            blobs = self.uploads.claim(s.device, att)
        except uploads.Refused as e:
            return await res(ok=False, why=e.why, **({"att": e.att} if e.att else {}))
        await _rcpt_recv(self, s.device, sid)      # P313: on disk before the Agent (or the phone's `ok`) hears of it
        send = await self._accept(s, text, sid, blobs, quote, quote_text)
        _rcpt(self, s.device, sid, "recv", send.turn)
        # through the same queue as its page (hist_turn), so the phone has the page before the answer that names it
        self._post(self.send_app, s, {"t": "say_res", "sid": sid, "ok": True, "state": "queued", "turn": send.turn})
        if send.prep is not None:
            self._post(self._send_device, s.device, {"t": "say_state", "sid": sid, "s": "transcribing"})

    async def _prep_say(self, send: compose.Send, text: str, files: list, voice: list, quote_text: str | None) -> None:
        """Each recording gets its own processing allowance; failures still deliver the original audio.
        Queue waiting does not consume a later recording's processing allowance. Withdraw cancels this task.
        """
        try:
            for f in voice:
                left = compose.asr_budget(f.get("secs"))
                if left <= 0:
                    f["asr"] = {"ok": False, "why": "timeout", "secs": f.get("secs")}
                    continue
                try:
                    f["asr"] = await self._transcribe_file(f, left, send.device)
                except Exception:  # noqa: BLE001 — F27: a transcriber bug still sends the message (「转写失败」), never ""
                    f["asr"] = {"ok": False, "why": "broken", "secs": f.get("secs")}
                if f["mime"].startswith("video/") and not f["asr"].get("ok") and f["asr"].get("why") in ("format", "no_speech"):
                    f.pop("asr")            # P166: a silent / soundless video is just a file, not a failed transcript
                elif f["asr"].get("ok") and len(f["asr"].get("text") or "") > compose.ASR_INLINE:
                    f["asr"]["file"] = await asyncio.to_thread(self._transcript_file, send.device, f)
                    if f["asr"]["file"]:
                        f["asr"]["text"] = f["asr"]["text"][:compose.ASR_INLINE]
            send.text = compose.render(text, files, self.lang, quote_text) + getattr(send, "onboarding_note", "")
            with contextlib.suppress(Exception):           # P363 (#72): the transcript back onto the owner's page, every remote
                self.hist_update(send.turn, att_asr=page_asr(files))
            with contextlib.suppress(Exception):           # P182: the latest recording group, for a later /会议总结 without files
                meeting.remember(self.st, files)
        finally:
            if send.ready is not None and not send.ready.done():
                send.ready.set_result(True)

    async def _transcribe_file(self, f: dict, budget: float, device: str, local_only: bool = False) -> dict:
        state = self.asr.ready_state(state_dir=self.st.root, engine_override=preferences.get(self.preferences, "voice.asr.engine")) if local_only else self.asr_state()
        if state != "ready":
            return {"ok": False, "why": ASR_WHY.get(state, "broken"), "secs": f.get("secs")}
        # P33-C02: only the host-private copy in the state dir (uploads.Blob.voice) — never the inbox file, which the Agent
        # can rewrite or replace with a link between upload and say
        path = f.get("voice") or ""
        if not path or not os.path.isfile(path) or os.path.islink(path):
            return {"ok": False, "why": "bad_audio", "secs": f.get("secs")}
        if f["mime"] in uploads.WAV_MIMES and uploads.wav_ok(path):
            r = await self._transcribe(path, min(ASR_TAKE, budget), local_only=local_only)
            return self._asr_result(r, f.get("secs"))
        demux = FF_DEMUX.get(f["mime"])
        ff = shutil.which("ffmpeg")
        if not ff or not demux:
            return {"ok": False, "why": "format", "secs": f.get("secs")}
        # P166: segment by segment (FF_SEG_SECS each, ≤ FF_MAX_SEGS) so a 1-hour recording is transcribed whole; each WAV
        # stays within the decoder's 630 s and is deleted at once. A failed later segment keeps what came before.
        deadline = time.monotonic() + budget
        d = self.uploads.root / device
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        texts, why, cut = [], None, False
        for k in range(FF_MAX_SEGS + 1):
            left = deadline - time.monotonic()
            if k == FF_MAX_SEGS:
                cut = True
                break
            if left <= 1:
                why = "timeout"
                break
            tmp = str(d / f"conv-{secrets.token_hex(6)}.wav")
            try:
                done = await self._ffmpeg(ff, demux, path, tmp, max(1.0, min(left / 2, FF_CPU_SECS)),
                                          start=k * FF_SEG_SECS, secs=FF_SEG_SECS)
                size = os.path.getsize(tmp) if done == "ok" and os.path.isfile(tmp) else 0
                if k and done == "ok" and size <= 44 + 32_000:     # past the end (< 1 s left)
                    break
                if done != "ok" or not uploads.wav_ok(tmp) or size <= 44:
                    why = "timeout" if done == "timeout" else "format"
                    break
                r = await self._transcribe(tmp, min(ASR_TAKE, max(1.0, deadline - time.monotonic())), local_only=local_only)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            res = self._asr_result(r, None)
            if res["ok"]:
                texts.append(res["text"])
            elif res["why"] != "no_speech":
                why = res["why"]
                break
            if size < 44 + FF_SEG_SECS * 32_000 - 64_000:          # shorter than a full segment: that was the last one
                break
        if texts:
            out = {"ok": True, "text": "\n".join(texts), "secs": f.get("secs")}
            if why or cut:
                out["partial"] = "cut" if cut else why
            return out
        return {"ok": False, "why": why or "no_speech", "secs": f.get("secs")}

    def _transcript_file(self, device: str, f: dict) -> str:
        """P166: a long transcript as `<name>-transcript.txt` next to the audio in the inbox; "" when it cannot be placed."""
        d = self.uploads.root / device
        tmp = d / f"tr-{secrets.token_hex(6)}.txt"
        try:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.st.write_private(tmp, f["asr"]["text"].encode())
            name = os.path.splitext(os.path.basename(f["path"]))[0][14:] or "audio"
            return inbox.place(self.uploads.workdir, str(tmp), name + "-transcript", "text/plain", secrets.token_urlsafe(16))
        except (OSError, inbox.Unsafe, TypeError):
            return ""
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    @staticmethod
    def _asr_result(r: dict, secs) -> dict:
        if r.get("ok") and (r.get("text") or "").strip():
            return {"ok": True, "text": wire.well_formed(r["text"]), "secs": secs}
        return {"ok": False, "why": "no_speech" if r.get("ok") else ASR_WHY.get(r.get("reason"), "broken"), "secs": secs}

    async def _ffmpeg(self, ff: str, demux: str, src: str, dst: str, timeout: float, start: float = 0,
                      secs: float = FF_MAX_SECS) -> str:
        """One container → 16 kHz mono PCM16 WAV conversion (P33-C04 / X09): the demuxer pinned from the declared MIME (no
        content probing: an HLS playlist is not opened as one), local files only (`-protocol_whitelist file`), at most
        FF_MAX_SECS of audio and uploads.ASR_MAX output bytes (also RLIMIT_FSIZE / RLIMIT_CPU in the child), one conversion
        per host at a time, and the child killed + reaped on timeout, withdraw (task cancelled) and every other exit.
        → "ok" | "timeout" | "fail"."""
        if self.ff_lock is None:
            self.ff_lock = asyncio.Lock()
        secs = min(secs, FF_MAX_SECS)
        argv = [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file", "-f", demux,
                *(["-ss", str(start)] if start else []), "-t", str(secs), "-i", "file:" + src, "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
                "-bitexact", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-t", str(secs),
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

    # ------------------------------------------------------------ P313: say receipts on disk (receipts.py, §14)
    def receipts_restart(self) -> None:
        """Once at start: the phone messages an earlier serve took but did not finish are never replayed (their sid stays
        `dup`). Each one's page — still `open` — ends `failed` with one line saying whether the Agent had it, so the owner
        can resend it by hand; a page that already ended needs no word; without the page (history off) one notice counts them."""
        if getattr(self, "receipts", None) is None:
            return
        left = 0
        for r in self.receipts.interrupted:
            page = self.hist.get(r["n"]) if r["n"] is not None else None
            if page is None:
                left += 1
                continue
            if page["end"] != "open":
                continue
            key = "say.restart_lost" if r["s"] == "recv" else "say.restart_unsure"
            self.hist_update(r["n"], append=humane.of(self, key), end="failed")
        if left:
            self.hist_add({"k": "sys", "text": ""}, humane.of(self, "say.restart_some", n=left), "done")
        if self.receipts.interrupted:
            self.st.log("say_restart", count=len(self.receipts.interrupted), result=left)
        self.receipts.interrupted = []

    def say_delivered(self, send: compose.Send) -> None:
        """The harness has the message (agent.Agent.deliver): the attachments belong to the Agent now."""
        if getattr(send, "background_done_ids", None):
            self.background_done.complete(send)
            self.st.log("background_done_delivery", count=len(send.background_done_ids), outcome="delivered")
            return
        if getattr(send, 'heartbeat_key', None):
            self.st.log("heartbeat_delivery", outcome="delivered")
            return
        if getattr(send, 'quota_resume_id', None):     # P314: no phone, no attachments
            self.st.log("quota_resume_delivery", outcome="delivered")
            return
        if getattr(send, 'auto_notify', None):         # P365: `agentj notify` copy for the main Agent (notify.py)
            self.st.log("notify_agent_delivery", outcome="delivered")
        if getattr(send, 'hook_notice', None):         # P366: a webhook's triage notice for the main Agent
            self.st.log("hook_agent_delivery", outcome="delivered")
            return
        if getattr(send, 'bot_handoff_id', None):
            self.st.log("bot_handoff_delivery", id=send.bot_handoff_id, outcome="delivered")
            return
        if getattr(send, 'official_notice_id', None):
            notices.processed(self.st, send.official_notice_id)
            self.official_pending.pop(send.official_notice_id, None)
            self.reporter.trigger("notice_delivered")
            return
        self.uploads.commit(send.blobs)
        _rcpt(self, send.device, send.sid, "deliv", send.turn)
        self.st.log("say_delivered", device=send.device, id=send.turn)
        self._post(self._send_device, send.device, {"t": "say_state", "sid": send.sid, "s": "delivered"})

    def say_uncertain(self, send: compose.Send) -> None:
        """The write to the harness failed part-way (P33-X08): it may have the message, so the attachments stay where they
        are (never deleted by the staged TTL, never released to the phone) and a withdraw answers already_delivered."""
        if getattr(send, 'bot_handoff_id', None):
            self.st.log("bot_handoff_delivery", id=send.bot_handoff_id, outcome="uncertain")
            return
        if getattr(send, "background_done_ids", None):
            self.background_done.complete(send)  # uncertain native writes are never replayed
            self.st.log("background_done_delivery", count=len(send.background_done_ids), outcome="uncertain")
            return
        if getattr(send, 'heartbeat_key', None):
            self.st.log("heartbeat_delivery", outcome="uncertain")
            return
        if getattr(send, 'quota_resume_id', None):     # P314: never replayed
            self.st.log("quota_resume_delivery", outcome="uncertain")
            return
        if getattr(send, 'auto_notify', None):         # P365: never replayed
            self.st.log("notify_agent_delivery", outcome="uncertain")
        if getattr(send, 'hook_notice', None):         # P366: never replayed (the delivery is on disk)
            self.st.log("hook_agent_delivery", outcome="uncertain")
            return
        if getattr(send, 'official_notice_id', None):
            # Native transport may have accepted it: do not replay auto-upgrade authority on an uncertain write.
            notices.processed(self.st, send.official_notice_id)
            self.official_pending.pop(send.official_notice_id, None)
            self.st.log("notice_delivery_uncertain", id=send.official_notice_id)
            return
        self.uploads.commit(send.blobs)
        _rcpt(self, send.device, send.sid, "deliv", send.turn)
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
                    r = await self._transcribe(str(self.uploads.part_path(b)), min(ASR_TAKE, compose.asr_budget(b.secs)))
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
    def _shared_follow(self) -> dict | None:
        """Opaque native-session identity; never the transport generation or a configured stale pin."""
        a = getattr(self, "agent", None)
        if not a or a.cfg.get("session_mode") != "shared":
            return None
        kind = a.kind
        sid = ((getattr(a, "session", None) or {}).get("sessionId") if kind == "claude"
               else getattr(a, "sid", None) if kind == "opencode"
               else a.cfg.get("shared_session_id") if kind == "codex" else None)
        if not isinstance(sid, str) or not sid:
            return None
        return {"agent": kind, "id": hashlib.sha256(sid.encode()).hexdigest()[:32]}

    def _meter_msg(self) -> dict:
        m = dict(self.meter_state)
        for optional in ("quota_windows", "source_at"):
            if m.get(optional) is None:
                m.pop(optional, None)
        follow = self._shared_follow()
        if follow is not None:
            m["shared_follow"] = follow
        return {"t": "meter", **m, "at": int(time.time())}

    def meter_update(self, **kw) -> None:
        follow = self._shared_follow()
        changed = follow != getattr(self, "_last_shared_follow", None)
        self._last_shared_follow = follow
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
        models = [{"id": m["id"], "name": m["name"], "efforts": m.get("efforts"), **({"disabled": True} if m.get("disabled") else {}), "cur": m["id"] == cur_m
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
        # P239 backstop (the phone already waits 5 s after the last tap): a switch that follows another within
        # MODEL_SET_COALESCE seconds is held; a newer one inside the window replaces it (the replaced one is answered
        # ok + "superseded"); only the last runs, MODEL_SET_COALESCE after it arrived. A lone switch runs at once.
        held, self.model_set_held = self.model_set_held, None
        if held is not None:
            held["h"].cancel()
            await self._model_set_res(held["s"], held["d"].get("r"), True, "superseded")
        if held is not None or time.monotonic() - self.model_set_at < MODEL_SET_COALESCE:
            entry = {"s": s, "d": d}
            entry["h"] = asyncio.get_running_loop().call_later(
                MODEL_SET_COALESCE, lambda: asyncio.ensure_future(self._model_set_go(entry)))
            self.model_set_held = entry
            return
        await self._model_set_go({"s": s, "d": d})

    async def _model_set_flush(self) -> None:
        """P240: the owner's next message runs on the switch they made last — a switch held inside MODEL_SET_COALESCE
        is handed to the Agent now, ahead of the message (the Agent's queue keeps that order)."""
        held = self.model_set_held
        if held is not None:
            held["h"].cancel()
            await self._model_set_go(held)

    async def _model_set_res(self, s: Session, r, ok: bool, why: str | None = None) -> None:
        if s is not None and s.state == "ready":
            await self.send_app(s, {"t": "model_res", "r": r, "ok": ok, **({"why": why} if why else {})})

    async def _model_set_go(self, entry: dict) -> None:
        if self.model_set_held is entry:
            self.model_set_held = None
        elif "h" in entry:
            return                                   # replaced while it waited
        s, d = entry["s"], entry["d"]
        if self.stopped():
            return await self._model_set_res(s, d.get("r"), False, "stopped")
        if not self.agent:
            return await self._model_set_res(s, d.get("r"), False, "unsupported")
        if self.model_sets >= 2:
            return await self._model_set_res(s, d.get("r"), False, "busy")
        self.model_sets += 1
        self.model_set_at = time.monotonic()
        self.st.log("model_set", cid=s.cid, device=s.device)
        self.agent.submit(agents.Cmd("model_set", "", s.name or s.device, None, d))

    def model_set_done(self, cmd, why: str | None) -> None:
        self.model_sets = max(0, self.model_sets - 1)
        d = cmd.data or {}
        s = self.sessions.get(d.get("cid"))
        self.activity("slash", cmd="model", result="ok" if why in (None, "queued") else "error", by=cmd.by)
        if s is not None and s.state == "ready":
            # P116 (B5): "queued" = accepted, applied when the desktop turn ends; the pill settles on the native meter
            m = {"t": "model_res", "r": d.get("r"), "ok": why in (None, "queued"), **({"why": why} if why else {})}
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
    def loop_exception_handler(self, loop, context) -> None:
        """Only the library's pre-connection recv_messages race is quieted; all other errors keep their handler."""
        from websockets import __version__ as ws_version
        from websockets.asyncio.connection import Connection
        exc = context.get("exception")
        handle = context.get("handle")
        callback = getattr(handle, "_callback", None)
        # asyncio transport wrappers clear their protocol before reporting the error.
        # The failing attribute object and library frame identify the connection reliably.
        conn = getattr(exc, "obj", None)
        tb = getattr(exc, "__traceback__", None)
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        if (isinstance(exc, AttributeError) and getattr(exc, "name", None) == "recv_messages"
                and isinstance(conn, Connection) and callable(callback)
                and not hasattr(conn, "recv_messages")
                and tb is not None and tb.tb_frame.f_code is Connection.connection_lost.__code__
                and tb.tb_frame.f_locals.get("self") is conn):
            logging.getLogger(__name__).debug("websockets %s: connection_lost before connection_made (recv_messages)", ws_version)
            return
        previous = getattr(self, "_previous_exception_handler", None)
        if previous:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(self.loop_exception_handler)
        try:
            await self._run()
        finally:
            if loop.get_exception_handler() == self.loop_exception_handler:
                loop.set_exception_handler(self._previous_exception_handler)

    async def _run(self) -> None:
        fence.no_dump()   # same-user processes cannot read this process's memory, environment or sockets
        sys.setswitchinterval(SWITCH_INTERVAL)       # P331: a worker thread's CPU work no longer taxes every loop syscall 5 ms
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
        self.receipts_restart()                      # P313: what the last serve left half-done → one line, never a replay
        from . import codex_procs                    # ADR-A178: app-servers an earlier serve left behind (one serve per state)
        ended = await asyncio.to_thread(codex_procs.sweep, self.st)
        if ended:
            self.st.log("codex_leftover_ended", result=len(ended))
        self.post_q = asyncio.Queue()
        self.configure_claude_inbound(await asyncio.to_thread(self._claude_inbound_files))   # P142: owner files, off the loop
        jobs = [asyncio.create_task(self.relay_loop()), asyncio.create_task(self.loop_watch()), asyncio.create_task(self.reporter.run()),
                 asyncio.create_task(self.sync_loop()), asyncio.create_task(self.remote_pair.run()), asyncio.create_task(self.post_loop()),
                 asyncio.create_task(self.update_loop()), asyncio.create_task(self.preferences_loop()),
                 asyncio.create_task(self.busy_loop()), asyncio.create_task(self.seat_loop())]
        perm_server = None
        order_server = None
        from .core import enabled as _core_on
        if _core_on():                       # P359: Agent J Harness CEOs ask the phone through <state>/orders/ask.sock
            try:
                from .core import askbridge, dispatcher as _disp
                order_server = await askbridge.Server(self.ask, self.st.root).start()
                lost = await asyncio.to_thread(_disp.reconcile, self.st.root)
                self.st.log("orders_bridge", lost=len(lost))
            except Exception as e:  # noqa: BLE001 — orders never stop serve
                bg.swallowed(self.st.log, "orders_bridge_failed", e, every=0)
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
            from . import personalize                # P57: bundled skills linked where free, before the harness starts
            self.st.log("skills_linked", **personalize.ensure())
            try:                                     # P215: WDB wizard skill + boot-doc core upgrade in the working root
                from . import main_identity as _mi, wizard as _wiz
                kind = self.agent_cfg.get("kind")
                rows = await asyncio.to_thread(_wiz.refresh_root, _mi.working_root(self.agent_cfg),
                                               [kind] if kind in _wiz.HARNESSES else None,
                                               preferences.get(self.preferences, "appearance.language", None), st=self.st)
                changed = [r["status"] for r in rows if r.get("status") not in ("unchanged",)]
                if changed:
                    self.st.log("root_docs_refreshed", n=len(changed), kinds=",".join(sorted(set(changed))))
            except Exception:  # noqa: BLE001 — the documents never stop the Agent from starting
                self.st.log("root_docs_refresh_failed")
            if not self.agent_cfg.get("_workflow_ceo"):
                jobs.append(asyncio.create_task(self._wf_first_sweep()))   # P276: scan + CEO roster once at start
            from .service import ensure_agentj_on_path   # P155: the skills' `agentj …` must run in the Agent's shell
            if ensure_agentj_on_path():
                self.st.log("agentj_path_added")
            # the Agent's sockets are bound just below; their (fixed) paths go into the environment BEFORE the harness can
            # spawn, so a first command never misses one (0.18: seen as "Agent J is not running" from a fenced `agentj …`)
            from . import capability as _cap, heartbeat as _hb, peer_service as _peer, recall as _recall
            for env, name in ((elevate.ENV, elevate.SOCK_NAME), (_cap.ENV, _cap.SOCK_NAME), (_recall.ENV, _recall.SOCK_NAME),
                              (_hb.ENV, _hb.SOCK_NAME), ("AGENTJ_IMESSAGE_SOCK", "imessage.sock"),
                              (_peer.ENV, getattr(_peer, "SOCK_NAME", "friends.sock")), ("AGENTJ_HOOKS_SOCK", "hooks.sock")):
                os.environ[env] = str(self.st.perm_dir / name)
            from . import harness_setup                # P219: the harness's one-time terminal questions, answered ahead
            if harness_setup.enabled() and not self.agent_cfg.get("_workflow_ceo"):
                with contextlib.suppress(Exception):
                    r = await asyncio.to_thread(harness_setup.prepare, self.agent_cfg["kind"], self.agent_cfg["dir"])
                    self.st.log("harness_prepared", agent=self.agent_cfg["kind"], result=",".join(r["done"]) or None,
                                reason=",".join(r["skipped"]) or None)
            self.agent = agents.make(self, self.agent_cfg)
            self.agent.start()
            # P306: a phone that resumed before the Agent existed (relay is up first after a restart / upgrade) was told
            # "none"; recording "idle" here without sending it left that phone grey until the first turn changed status.
            self.status_changed()
            self.st.log("agent_on", agent=self.agent.kind, isolation_requested=self.agent_cfg.get("fence", True), session_mode=self.agent_cfg.get("session_mode", "independent"), phase="configured")
        await self.bots.start()
        await self.elevate.start()                 # F17: <state>/agentperm/elevate.sock for `agentj sudo` / `agentj secret`
        self.scheduler.recover()                   # P248: reconcile before accepting any dispatch
        await self.capabilities.start()            # 0.18: <state>/agentperm/capability.sock for `agentj capability`
        try:                                       # P156: heartbeat.sock + the default heartbeat.md; never blocks serve
            from .heartbeat import ensure_prompt
            ensure_prompt(self.lang)
            await self.heartbeat.start()
        except Exception as e:  # noqa: BLE001
            self.st.log("heartbeat_start_failed", reason=type(e).__name__)
        try:                                       # P274: imessage.sock for `agentj imessage …`; never blocks serve
            await self.imessage.start()
        except Exception as e:  # noqa: BLE001
            self.st.log("imessage_start_failed", reason=type(e).__name__)
        with contextlib.suppress(Exception):       # P115: pinned Google CLI pre-install, quiet, never blocks serve
            from . import google
            google.ensure_background(self.st)
        from .recall import Server as RecallServer
        self.recall = RecallServer(self)           # F22 (P57): <state>/agentperm/recall.sock for `agentj recall` (§15.4)
        await self.recall.start()
        from .peer_service import PeerService     # P71: Agent friends (§17) + <state>/agentperm/friends.sock
        self.peers = PeerService(self)
        try:
            await self.peers.start()
        except Exception as e:  # noqa: BLE001 — friends never keep serve from running
            self.st.log("peer_start_failed", reason=type(e).__name__)
        from .hook_service import HookService     # P366: webhooks + <state>/agentperm/hooks.sock (after peers: same seat cert)
        self.hooks = HookService(self)
        try:
            await self.hooks.start()
        except Exception as e:  # noqa: BLE001 — webhooks never keep serve from running
            self.st.log("hook_start_failed", reason=type(e).__name__)
        from . import telegram as telegram_mod
        from .telegram import Telegram
        telegram_mod.IN_SERVE = True              # P142 (B14): key checks in this process read serve's own environment
        self.telegram=Telegram(self)
        jobs.append(asyncio.create_task(self.telegram.run()))
        from .mail import Mail                    # P170: the e-mail channel (allowlist + sender check before any model)
        self.mail = Mail(self)
        jobs.append(asyncio.create_task(self.mail.run()))
        jobs.append(asyncio.create_task(self.scheduler.loop()))
        jobs.append(asyncio.create_task(self.heartbeat.loop()))
        self.quota_resume.start()                 # P314: re-arm the waiting continues (at most once each)
        jobs.append(asyncio.create_task(self.imessage.loop()))   # P274: idle unless macOS + imessage.enabled
        jobs.append(asyncio.create_task(self.goals.loop()))     # P158: deadline / unpaired phone ends the goal
        jobs.append(asyncio.create_task(self.sweep_loop()))
        from .browser_login import daily_loop     # P114: the daily read-only sign-in check (status only, never a QR)
        jobs.append(asyncio.create_task(daily_loop(self)))
        jobs.append(asyncio.create_task(self.auth_rescue.loop()))   # P258/P288: expiry reminders (7/3/1 days) + daily login health check
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
            await self.bots.stop()
            await self.elevate.stop()
            await self.capabilities.stop()
            with contextlib.suppress(Exception):
                await self.heartbeat.stop()
            with contextlib.suppress(Exception):
                await self.imessage.stop()
            await self.recall.stop()
            if self.peers is not None:
                with contextlib.suppress(Exception):
                    await self.peers.stop()
            if self.hooks is not None:
                with contextlib.suppress(Exception):
                    await self.hooks.stop()
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
            if order_server:
                await order_server.close()
            with contextlib.suppress(FileNotFoundError):
                sock.unlink()
            self.st.log("serve_stop")
            from . import telegram as telegram_mod
            telegram_mod.IN_SERVE = False
            # P142: queued state writes and log lines reach the disk — P292: bounded, a stuck disk never holds the exit
            await diskq.aflush(STOP_FLUSH)


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


def _keep_progress(page) -> dict:
    """P281: a wait line never drops what a running /switch-harness said it is doing (card.progress)."""
    p = (page or {}).get("card") or {}
    return {"progress": p["progress"]} if isinstance(p.get("progress"), str) else {}


def clean_line_tool(tool: str) -> str:
    from .text import clean_line
    return clean_line(tool, 64) or "?"
