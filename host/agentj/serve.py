
from __future__ import annotations

from . import resource_policy, restart_guard, secret_display_route

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
from . import action_policy, confirm_tier, harness_cmds, notices, activity, approvals, cloud, controls, danger, fence, gate, login_flow, memory, slash, tasks, update, webpush, wire
from . import asr as asr_mod
from . import terminal as term_mod   # P408 (§38): the web terminal (terminal.py, term_*.py)
from . import cmdrun, compose, history, hsearch, humane, inbox, menu, uploads, preferences, proxy, provenance
from . import receipts as receipts_mod  # P313: say receipts on disk — a restart never runs a phone message twice
from . import meeting  # P182: /会议总结 → minutes, recipients only from the owner's words
from . import diskq  # P142: fsync'ed state writes off the loop (one ordered writer)
from . import approval_policy, budget as budget_mod, interrupts as interrupts_mod, privacy   # 0269-w3-policy E1 / E3 / E4
from .background_done import chain_turn as _chain_turn   # E2
from . import approval_review
from . import approval_jev  # opaque verdict metadata; model opinions never authorize effects
from . import owner_grant  # P574: same-owner task grant (one Face ID, checked by the other hosts)
from . import goal as goal_mod  # P158: /goal — one signed grant, reversible steps pass after a 10 s card
from . import approval_level  # P336: safe / standard / full, goal without the opening card, cards while no remote is online
from . import bg  # P192 (B18): swallowed errors on connection / approval paths leave a (rate-limited) line
from . import loopwatch  # P136: what was running when the loop stalled
from . import jcards  # P360 (§34): Agent J Cards — the logo menu from card 6 on (template cards, read-only data)
from . import relaydiag  # P317: why a relay leg is not up (journal / status / doctor)
from . import reset as reset_mod  # P332 (§33): 紧急修复 — the reset ladder, support help, repair actions
from . import assist as assist_mod  # 0269-w2-assist (#468, §41): official remote assist — separately scoped signed tasks / official PTY over Noise
from . import media as media_mod  # F21: files the Agent shows to the phone (PROTOCOL §13)
from . import elevate  # F17: sudo + secret cards (PROTOCOL §11)
from . import pair_card  # P153: add a remote from a remote (PROTOCOL §3.3)
from . import passkey  # F20: the same phone without a second pairing (PROTOCOL §12)
from . import renewal  # P122: the same phone after its browser deleted the page's storage (§12.1)
from . import onboarding  # F28: seat → two required remotes → one welcome (P72)
TG_PAIR_CMDS = ("telegram-pair", "telegram_pair", "rebind-telegram")   # P283: the phone's 「重新绑定 Telegram」
RELOAD_CMDS = ("reload",)
RELOAD_KEYS = ("agent.instructions", "agent.isolation", "agent.allow_docker", "agent.high_risk_warnings", "agent.approvals",
               "channels.items")
RESTART_SERVE_KEYS = ("agent.working_root", "agent.session_mode", "agent.shared_session_id", "agent.shared_opencode_port")
from . import friend_cmds  # P73: /my-agent-id · /add-friend, answered by the host (ADR-A176)
from . import friends as friends_mod
from .remotes import TYPES as REMOTES_TYPES  # P171 (§24): the phone's 遥控器管理 page
from .pkpeer import TYPES as PKPEER_TYPES  # P547
from .seat_transfer import TYPES as SEAT_XFER_TYPES  # P547
from .noise import IK, IKPSK2, CipherState, Handshake, NoiseError
from .reporter import Reporter, in_daemon_thread
from .envcompat import getenv
from .state import MAX_DEVICES, DeviceLimit, State, device_label, device_names
from .text import LABEL_DIGITS, LABEL_MAX, clean, clean_label, clean_line, drop_replacement, text_units  # noqa: F401  (re-exported)


def _ttl(env: str, default: int) -> float:

    try:
        return min(default, float(getenv(env, default)))
    except ValueError:
        return default


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


_close_code = relaydiag.close_code   # P317: shared with the mailbox leg and doctor


RC_WHY = ("load", "close", "hb", "host", "error", "ticket", "visibility", "offline", "stall")   # P129/P133: see session.js


def _reconnect_reason(rc) -> tuple:

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
LEARN_TOOL = "agentj learn"   # P204: the /learn proposal card (learn.TOOL)
PHASE_GAP = 0.4    # P204 (§29): at most one tool / writing switch per this many seconds to the phones (the last one always)
PUSH_GAP = {"reply": 20, "ask": 2, "security": 2}   # minimum seconds between two pushes of a kind to one device
BATCH_MAX = 20         # automatic approvals one batch grant may give (ADR-A48) …
BATCH_SECS = 600       # … within this many seconds, and never past the end of the Agent's turn
MAX_GRANTS = 8         # batch grants open at once
TASK_GRANT_MAX = 12    # P573 (#389): steps one task grant may still allow without a card …
TASK_GRANT_SECS = 600  # … within this many seconds, and only inside the turn (history page) it was signed in
MAX_TASK_GRANTS = 16   # task grants kept at once (the oldest goes)
CHUNK = 11 * 1024      # phone-control answers are split into app messages of about this many JSON bytes (MAX_JSON = 16 KiB)
ITEM_SHOW = 6000       # a memory item longer than this is shown cut on the phone (its id still covers the whole text)
_RID = re.compile(r"[A-Za-z0-9_-]{1,32}")
FRIEND_READS = ("fr_list", "fr_hist", "fr_usage", "fr_ctx_get", "fr_glog")   # P71 §17.7 (+ P73 fr_ctx_get); writes = controls.FRIEND_ACTIONS
WF_READS = ("wf_list", "wf_doc", "skill_get")   # P244 (§30.2): answered to the asking session only, read-only
SKILL_ACTS = ("skill_archive", "skill_restore")   # P426 #181-3 (§39): the owner's archive / restore taps on the 技能 page
PHONE_CONTROLS = ("mem_list", "mem_rm", "mem_undo", "act_list", "task_list", "task_set", "task_migrate", "estop", "resume",
                  "systask_list")   # P207 §2: the Agent J host's own scheduled tasks, read-only (systasks.py)
Q_TTL = _ttl("AGENTJ_TEST_Q_TTL", 180)     # a question nobody answers is ended (relay's default)
MAX_QUESTIONS = 4                           # open at once; more are answered "nobody answered" at once
ASR_TAKE = compose.SAY_ASR_BUDGET                             # s per take (asr_res timeout)
ASR_QUEUE = 3                               # takes waiting per device (more → busy)
FF_MAX_SECS = 630                           # say-time ffmpeg: at most this much audio (≤ uploads.ASR_MAX as WAV)
FF_SEG_SECS = 600                           # P166: a longer file is transcribed in 10-minute segments …
FF_MAX_SEGS = 12                            # … at most two hours; the rest is said to be cut, the original stays attached
FF_CPU_SECS = 180                            # RLIMIT_CPU of one conversion
FF_DEMUX = {"video/mp4": "mov", "audio/webm": "matroska", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mov", "audio/aac": "aac",
            "audio/wav": "wav", "audio/x-wav": "wav",
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
VOICE_EVERY = 900          # #0304: expired voice originals go within 15 min of their retention
P33_ONLY = ("web_preview_attest", "say", "say_cancel", "say_sync", "cmd_cancel", "blob_open", "blob_chunk", "blob_end", "blob_drop", "hist_get", "menu_get", "model_set",
            "q_answer", "tts_get", "pref_set", "notice_read", "media_get", "provider_get", "provider_key", "asr_install", "models_get", "ark_plan_get", "ping",
            "tg_fwd", "wx_fwd", "turn_wait", "res_watch", "busy_get",   # P181 (§22.1) load card resources; P160 (§22) one 「在办」 row's detail
            "mail_get", "mail_set", "mail_key", "mail_test", "mail_check",   # P170 (§10.20)
            "dev_rename",    # P179 (§10.16a): a remote renames itself
            "tts_script", "form_get", "form_draft", "hist_find", "hist_read",     # P188 · P247 (owner-level read sync) (§28): chat search, a read like hist_get
            "auth_get", "auth_start", "auth_cancel", "auth_press",   # P258 (§31): AI 登录救援
            "auth_rescue",              # 0269-login (§31.1): 「重新登录」 under a reply — signed login / code / cancel
            "acct_get", "acct_use", "acct_rename", "acct_del",       # #427: several accounts per harness (accounts.py)
            "imsg_check", "imsg_fda",   # P274 (§10.21): the iMessage card's 「重新检测」 / 「在Agent J 主机上打开授权页」
            "qr_cancel",                # P314 (§32): cancel the automatic continue after a used-up usage limit
            "keys_get", "keys_op", "keys_set",   # P352: 「模型和 API Key」 key manager (keys.py)
            "notify_get", "notify_set", "hook_list", "hook_set", "news_get", "news_set",
            "news_email", "news_verify",          # P365: the settings list of automations allowed to post (notify.py)
            "preauth_list", "preauth_revoke",     # P539: 「Computer Use 免问」 list / revoke (narrowing only)
            "automation_grants", "automation_grant_revoke")   # 0304 A3: 「自动化外发授权」 list / revoke (narrowing only)
PREF_SET_KEYS = ("updates.mode", "appearance.language", "appearance.theme", "voice.wake_enabled", "voice.speak_replies",
                 "agent.high_risk_warnings", "agent.session_mode", "agent.isolation", "agent.allow_docker")
PREF_SET_KEYS += tuple("voice.asr."+key for key in ("mode", "engine", "provider", "base_url", "model", "key_env", "hotwords", "corrections"))
PREF_SET_KEYS += ("heartbeat.enabled", "heartbeat.interval_minutes")    # P156: the settings panel's heartbeat switch
PREF_SET_KEYS += ("approval.jev_threshold",)     # P157: the phone's Jev threshold stepper (number, schema range and step)
PREF_SET_KEYS += ("approval.goal_auto",)         # P158: the phone's goal-mode countdown switch (enterprise: off)
PREF_SET_KEYS += ("updates.auto_install",)       # P203: 「空闲时自动更新」 = auto_install + mode together (one switch)
CARDS_HAVE_MAX = 256       # P203 (§29.1): ids a cards_get may list as already shown
CARDS_OPEN_MAX = 256       # … and the open ids one `cards` answer carries
UPG_TTL = 600              # P203 (§29.2): the upgrade confirmation card waits ten minutes
PREF_SET_KEYS += ("heartbeat.quiet_enabled", "heartbeat.quiet_start", "heartbeat.quiet_end", "heartbeat.context_max_tokens")
PREF_SET_KEYS += ("heartbeat.daily_at",)         # P207: the phone's "wake every day at HH:MM" ("" = off; schema pattern)
PREF_SET_KEYS += ("ui.card_hot_action",)         # P285: the reply card's bottom-left hot key (command / skill name; schema pattern)
PREF_SET_KEYS += ("ui.remote_servers",)     # #444: display-only machine aliases
PREF_SET_KEYS += ("ui.composer_buttons",)        # P340: the order of the four buttons above the message box (a permutation)
FORWARD_KEY = "ui.forward_destination"            # P554: one host setting for every remote; a phone writes it only by forward_set
FORWARD_DESTS = ("share", "wx", "tg")
PREF_SET_KEYS += ("imessage.enabled", "imessage.interval_minutes", "imessage.delivery", "imessage.digest_at",
                  "imessage.quiet_hours", "imessage.strictness", "imessage.codes", "imessage.groups", "imessage.llm")
PREF_SET_KEYS += ("notify.enabled",)             # P365: 「允许自动化往这里发消息」 master switch (notify.py)
PREF_SET_KEYS += ("quota.auto_switch",)          # P302: the phone's 「额度告警时交给其他 AI 账号」 switch (P254 drew it, never whitelisted)
PREF_SET_KEYS += ("inbox.voice_retention_hours",)   # #0304: how long voice originals stay in the inbox (schema enum)
METER_KEYS = ("model", "model_name", "effort", "ctx", "h5", "week", "quota_windows", "shared_status", "shared_writer", "source_at", "reset_credits")
Q_CANCEL = "用户在手机上取消了这个问题，没有选择任何选项。请不要替他做选择，停下来等他直接输入文字。"
Q_TIMEOUT = "没有人作答，请改用文字列出选项"
UNKNOWN_ACTION = ("Agent J 的规则里没有这种动作，所以没有给主人弹确认卡，直接拒绝了（不是主人拒绝的）。请换一种常见做法（普通的读文件、"
                  "改文件或命令）再试；还不行就用一句话告诉主人你想做什么，请他在手机上确认。 / This action type is not in Agent J's "
                  "policy, so no card was shown and it was denied automatically (the owner did not deny it). Try a common way (a "
                  "plain file read, edit or command); otherwise tell the owner in one sentence what you want to do and ask on the phone.")
ASR_WHY = {"not_installed": "not_installed", "bad_audio": "bad_audio", "timeout": "timeout", "busy": "busy",
           "engine_failed": "broken", "off": "off", "broken": "broken", "no_speech": "no_speech"}


def page_asr(files: list) -> dict:

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
        if a.get("notice") in ("local_fallback", "remote_unavailable"):
            r["notice"] = a["notice"]
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
    gone_moved: bool = False      # 0.30.7: "replaced" by the same phone's other identity (passkey / ticket restore)
    gone: str = ""                # 0.15.1: a removed device that resumed: "replaced" | "revoked" — told so after its hello
    pk_offer: tuple | None = None  # F20 (§12): (nonce, monotonic issue time, P547 peer request id | None) of the one open pk_offer
    since: object = None          # F20: a passkey restore's hello `since`, used once the restore is accepted
    rt_at: float = 0.0            # P122: monotonic time of this session's last renewal ticket (≤ 1 per minute on rt_req)
    pair1: bool = False           # P153: the page announced "pair1" (adds remotes, renders pair_device cards)
    card: str = ""                # P153: while pending, the id of its pair_device card on the paired remotes
    provenance: dict | None = None  # set by verified paired transport, never defaulted from absence


@dataclass
class Question:

    qid: str
    qs: list
    digest: str                   # approvals.question_digest(qs): what approvals.log keeps
    deadline: float               # monotonic
    fut: asyncio.Future
    task: str | None = None


def _cfg_patch(old: dict, new: dict) -> tuple[dict, dict]:

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
    uv_ok: bool = False            # P539: the approving remote's allow passed the passkey (UV) on this card
    pk_fail: bool = False          # P539: a passkey answer on this card was refused (ask_done why "passkey")
    reason: str | None = None      # P572 (#385): the Agent's reason, read out of the signed summary ("" = none given;

    tg_key: tuple | None = None    # P573 (#389): (turn, action class) a signed allow of this card opens a task grant for
    task_scope: str | None = None
    tg_by: str = ""                # P573: the task grant (its signed parent rid) that answered this still-open card
    only: str | None = None        # P574: origin remote alone may see or answer a task grant
    fa: dict | None = None         # P574: the verified assertion of an owner-grant card (owner_grant.proof_of)
    settings_uv_at: float | None = None  # exact successful WebAuthn time, not request resolution
    settings_session: object | None = None  # #454 private, exact live requesting Noise session
    host_card: bool = False        # trusted host presentation marker; never inferred from tool name
    prow: str = ""                 # 0269-w3-policy: the card's class (approval_policy.ROWS) — shown with why the task does not cover it


@dataclass
class Grant:

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


CARD_TEXT = {
    "zh": {"change": "{tool} · 改密码或密钥。\n具体内容只在Agent J 主机上看，凭据不会发到手机。",
           "read": "{tool} · 查看密码或密钥（内容会显示在Agent J 主机屏幕上）。\n具体内容只在Agent J 主机上看，凭据不会发到手机。",
           "risk_scope": "\n本回合内同类高危动作一并批准。"},
    "en": {"change": "{tool} · Changes passwords or keys.\nCheck the exact details on the Agent J host; credentials never go to the phone.",
           "read": "{tool} · Shows passwords or keys (on the Agent J host's screen).\nCheck the exact details on the Agent J host; credentials never go to the phone.",
           "risk_scope": "\nAlso approves this kind of high-risk action for the rest of this turn."}}


def _echo_page(host, text: str) -> bool:

    turns = getattr(getattr(host, "hist", None), "turns", None)
    last = turns[max(turns)] if isinstance(turns, dict) and turns else None
    if not last or last.get("end") == "open" or (last.get("reply") or {}).get("text", "").strip() != text.strip():
        return False
    host.st.log("hist_echo_dropped", turn=last.get("id"))
    return True


def _hb_touch(host) -> None:

    hb = getattr(host, "heartbeat", None)
    if hb is not None:
        hb.touch()


PK_OFFERS = 4   # P565 追加 A: open pk_offers kept per device (a reconnect may bring a new one before the old answer arrives)


def _pk_pick(obj: dict, here: tuple | None, mine: list[tuple]) -> tuple | None:

    try:
        c = json.loads(passkey.b64(obj.get("cd"), passkey.MAX_CD, "cd").decode("utf-8"))
        ch = c.get("challenge") if isinstance(c, dict) else None
    except Exception:   # noqa: BLE001 — a bad cd is refused by verify_registration
        ch = None
    if isinstance(ch, str):
        for o in ([here] if here else []) + list(reversed(mine)):
            if hmac.compare_digest(ch.rstrip("=").encode(), wire.b64u(o[0]).encode()):
                return o
    return here


def _pk_same(had: dict | None, obj: dict) -> bool:

    try:
        cid = wire.b64u(passkey.b64(obj.get("id"), passkey.MAX_CRED_ID, "id"))
    except Exception:   # noqa: BLE001
        return False
    return bool(had) and isinstance(had.get("id"), str) and hmac.compare_digest(had["id"], cid)


def _pk_where(obj: dict) -> dict:

    att = passkey.attachment(obj.get("aa"))
    out = {"local": att != "cross-platform"}
    if (plat := passkey.platform(obj.get("plat"))):
        out["plat"] = plat
    return out


def _done_why(reason, decision, a=None) -> dict:

    if reason == "device":
        return {} if decision in ("allow", "allow_batch") else {"why": "denied"}
    if reason == "timeout":
        return {"why": "passkey" if a is not None and getattr(a, "pk_fail", False) else "timeout"}
    w = {"offline": "nobody", "no_device": "nobody", "estop": "estop", "stopped": "estop", "serve_stop": "serve_stop"}.get(reason)
    return {"why": w} if w else {}


def _timeout_text(lang, secs: int, pk_fail: bool = False) -> str:

    if pk_fail:
        return pick(lang, f"手机上 {secs} 秒内没有批准：默认拒绝。主人点了同意，但这台遥控器的通行密钥没通过。可以再发起一次；"
                          "或请主人在手机上「设置 → Agent 与遥控器 → 本机 Face ID / 指纹」先设好。",
                    f"Not approved on the phone within {secs} s: denied. The owner tapped allow but this remote's passkey "
                    "check failed. You can ask again, or ask the owner to set up 'Settings → Agent & remotes → Face ID / fingerprint on this device' first.")
    return pick(lang, f"手机上 {secs} 秒内没有批准：默认拒绝（主人可能没看到，或这台遥控器的通行密钥还没准备好）。"
                      "可以再发起一次；或请主人在手机上「设置 → Agent 与遥控器 → 本机 Face ID / 指纹」先设好。",
                f"Not approved on the phone within {secs} s: denied (the owner may not have seen it, or this remote's passkey "
                "is not set up yet). You can ask again, or ask the owner to set up 'Settings → Agent & remotes → Face ID / fingerprint on this device' first.")


def card_lang(lang) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def policy_registry_bound(tool, bind):

    if bind is None:
        return None
    from . import policy_registry
    return policy_registry.bound_action(tool, bind)


CU_SCRIPT_HEAD = {"zh": "Computer Use：在这台 Agent J 主机上点击、输入并运行操控脚本",
                  "en": "Computer Use: click, type and run a control script on this computer"}
CU_SCRIPT_GRANT_NOTE = {
    "zh": "勾选免问后，这台遥控器批准过的 Computer Use 操作（包括它运行的操控脚本）以后不再弹卡，相当于这台 Agent J 主机上的 Computer Use "
          "完全放开；随时可在 设置 → 安全 → 免问的动作 取消。",
    "en": "If you tick don't-ask-again, Computer Use actions approved from this remote (including the control scripts it "
          "runs) no longer show a card — Computer Use on this computer is fully open; undo it any time in Settings → "
          "Safety → Actions that skip approval."}


TASK_GRANT_NOTE = {"zh": "（批准后，这个任务里仅上述具体动作不再询问。）",
                   "en": "(This task may run only the exact actions listed above without asking again.)"}


def _same_owner_turn(agent) -> bool:
    if agent is None or not provenance.delegated_turn(agent):
        return False
    o = provenance._REQUEST.get()
    return provenance.turn_source(agent)["kind"] == "same_owner" and (o is provenance._UNSET or o.get("kind") == "same_owner")


def computer_use_script_summary(tool_input, lang="zh") -> str:

    lg = card_lang(lang)
    inp = tool_input if isinstance(tool_input, dict) else {}
    code = inp.get("code") if isinstance(inp.get("code"), str) else None      # cua-repl's `js` takes {code}
    rest = {k: v for k, v in inp.items() if k != "code"} if code is not None else inp
    body = (code or "") + (("\n" if code else "") + json.dumps(rest, ensure_ascii=False, indent=1) if rest else "")
    if not body.strip():
        body = "（重置操控脚本的运行环境）" if lg == "zh" else "(reset the control script's runtime)"
    return CU_SCRIPT_HEAD[lg] + "\n" + body


def approval_summary(tool, tool_input, verdict, lang="zh", *, workdir=None):
    text = action_policy.summary(tool, tool_input, credentials="credentials" in verdict.cats, lang=lang, workdir=workdir)
    if 'credentials' in verdict.cats:
        kind = "read" if getattr(verdict, "cred", "") == "read" else "change"
        text = CARD_TEXT[card_lang(lang)][kind].format(tool=tool) + "\n" + text
    return text




async def _rcpt_recv(host, device: str, sid: str) -> None:

    rc = getattr(host, "receipts", None)
    if rc is None:
        return
    try:
        await rc.persist(device, sid, "recv")
    except Exception:  # noqa: BLE001
        host.st.log("say_receipts", result="write_failed")


def _rcpt(host, device, sid, state: str, turn: int | None = None) -> None:

    rc = getattr(host, "receipts", None)
    if rc is not None and isinstance(device, str) and isinstance(sid, str) and rc.used(device, sid):
        rc.mark(device, sid, state, turn)


def say_sync_state(send, receipt: str | None) -> str | None:

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


def _peer_floor(host, tool, tool_input, v, requirement, opaque, host_card, asks, bound, ttl, lv, dispatch=False) -> bool:   # #455 ADR: True = card
    from . import peer_origin, policy_registry
    if host_card or asks or bound or ttl is not None:
        return True
    if dispatch:                            # the CEO-dispatch whitelist shape, judged before (ADR-#455)
        return False
    if (v.danger or opaque or requirement != "allow"
            or tool in approval_level.HARNESS_KEEPS or tool in approval_level.HOST_CARDS or tool in policy_registry.HOST_TOOLS):
        return True
    try:
        return bool(confirm_tier.harness_keeps(tool, tool_input, env=getattr(host.agent, "approval_env", None))
                    or approval_level.harness_floor(tool, tool_input, v)
                    or approval_level.redline(tool, tool_input, v, host.danger_extra, (host.agent_cfg or {}).get("dir")))
    except Exception:  # noqa: BLE001 — cannot tell → the card
        return True


SAME_MACHINE_CARD = frozenset({"send", "spend", "price", "delete", "credentials"})


def _same_machine_card(host, tool, tool_input, v, requirement, dispatch, env, known_recipients) -> bool:
    """hotfix #455: the five dangerous kinds (pay, delete, send out, change credentials, change prices) in a same-computer
    turn always ask the owner on the phone — whatever the approval level frees in the owner's own turn. True = the card."""
    if set(getattr(v, "cats", None) or ()) & SAME_MACHINE_CARD:
        return True
    try:
        tier, _ = approval_policy.decide(tool, tool_input, v, requirement=requirement, dispatch=dispatch,
                                          workdir=(host.agent_cfg or {}).get("dir"), env=env, classify=host._classify,
                                          budget=budget_mod.state(host.st), known_recipients=known_recipients, chain=True)
    except Exception:  # noqa: BLE001 — cannot tell → the card
        return True
    return tier is not None and tier != approval_policy.NONE


AUTONOMOUS_CHOICE = ("全放行：按主人已有目标自主选择合理默认并继续，不等待主人回复；真正需要 UV 的动作走原权限通道。 / "
                     "Full access: choose a reasonable default consistent with the owner's goal and continue without a reply. "
                     "Use the existing permission channel for actions requiring UV.")

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
        self.wechat = None
        self.external_channels = None
        self.telegram = None
        self.mail = None                  # P170: the e-mail channel (mail.py), started with serve
        self._news_inflight = 0           # P374: /v1/host/news calls running (on_news)
        self.ws = None
        self.sessions: dict[int, Session] = {}
        self.reload_loads: dict[str, deque[float]] = {}  # P0307: process-local, per paired device
        self.reload_storm_at: dict[str, float] = {}
        from .remote_pair import RemotePair
        self.remote_pair = RemotePair(self)
        from .remotes import Remotes
        self.remotes = Remotes(self)                   # P171 (§24): the phone's 遥控器管理 page
        from .bots.owner import Manager as BotManager
        self.bots = BotManager(self)
        self.pairing: Pairing | None = None
        from .credential_revoke import Revocations
        self.credential_revocations = Revocations(self)
        from .security_rotation import Rotation
        self.security_rotation = Rotation(self)
        self.pk_guard = passkey.Guard()   # F20: pk_restore rate limit + nonce replay memory (in memory only)
        self.pk_offers: dict[str, list[tuple]] = {}   # P565 追加 A: device → its open pk_offers (bound to the device, not the socket)
        from . import pkpeer, seat_transfer
        self.pkpeer = pkpeer.Desk(self)                # P547: Face ID set up with another remote's approval
        self.st.on_passkey_noted = lambda did: asyncio.get_running_loop() and self._bg(self.remotes.changed(), 'pk_noted')
        self.seat_xfer = seat_transfer.Desk(self)      # P547: seat transfer from a remote (signed + Face ID)
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
        self.push_generation: dict[str, int] = {}   # process-local: invalidate in-flight POSTs on every opt-in / opt-out
        self.post_q: asyncio.Queue | None = None
        self.danger_extra = danger.parse_extra(self.cfg.get("danger_extra"))[0]
        if self.agent_cfg:
            self.agent_cfg["danger_extra"] = self.danger_extra
        self.grants: dict[str, Grant] = {}
        self.turn_lock = asyncio.Lock()        # one user of the Agent at a time: a chat turn or a scheduled task run
        self.nonces = controls.Nonces()
        from .private_files import Downloads
        self.private_files = Downloads(self)
        from .owner_hosts import Desk as OwnerHosts
        self.owner_hosts = OwnerHosts(self)
        self.estop = controls.estop_state(st)
        self.scheduler = tasks.Scheduler(self)
        self.task_label: str | None = None
        from .ask import CliAsks
        from .local_files import Files
        self.local_files = Files(self)
        self.cli_asks = CliAsks(self)             # P322: `agentj ask` — the question card for any harness
        self.turn_by: str | None = None
        self.turn_t0 = 0.0
        self.turn_acc = None
        self.phase_now = None
        self.phase_timer = None
        self.usage_today_cache = None
        self.hist = history.History(st)
        from .readmark import ReadMark
        self.readmark = ReadMark(st, self.hist)   # P321: seeded at the newest reply on a host's first mark
        self.lang = preferences.get(self.preferences, "appearance.language", "zh")
        self.uploads = uploads.Uploads(st, self.agent_cfg["dir"] if self.agent_cfg else None,
                                       asr_off=lambda: self.asr_state() == "off")
        self.media = media_mod.Media(st, self.agent_cfg["dir"] if self.agent_cfg else None,   # F21 (§13)
                                     cwd=self._agent_cwd, generated=self._agent_image_root)                     # P424 #64: ∪ the session's current folder
        self.media.lang = lambda: "en" if self.lang == "en" else "zh"
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
        from .voice_stream import Manager as VoiceStreamManager
        self.voice_stream = VoiceStreamManager(self)
        self.asr = asr_mod                       # tests swap in a stand-in engine
        self.asr_lock: asyncio.Lock | None = None
        self.ff_lock: asyncio.Lock | None = None    # one say-time ffmpeg conversion at a time (P33-X09)
        self.asr_waiting: dict[str, int] = {}
        self.model_sets = 0
        self.model_set_at = 0.0                  # P239: monotonic time of the last pill switch handed to the Agent
        self.model_set_held: dict | None = None  # P239: a switch held back inside MODEL_SET_COALESCE (only the last one runs)
        from .forms import FormService
        self.forms = FormService(self)
        self.elevate = elevate.Elevator(self)   # F17: admin password / secret cards (PROTOCOL §11)
        from .auth_rescue import Rescue
        self.auth_rescue = Rescue(self)          # P258: AI 登录救援 (subscription login expired → re-login from the phone)
        from .assist import Assist
        self.assist = Assist(self)               # 0269-w2-assist (#468, §41): official assist scopes are independent (tasks vs owner-authorized PTY)
        self.reset_desk = reset_mod.Desk(self)    # P332 (§33): reset / rescue_do, one run at a time, also during an e-stop
        from .turn_heal import TurnHeal
        self.turn_heal = TurnHeal(self)           # 0269 (#461): stuck-turn watchdog; /clear /new / reset preempt the turn
        self.cmd_desk = cmdrun.Desk(self)         # P361 (§34): Settings 「执行」, one run at a time
        self.term_desk = term_mod.Desk(self)      # P408 (§38): 网页终端 — one line-mode terminal at a time
        from .capability import Capabilities
        self.capabilities = Capabilities(self)  # 0.18 (ADR-A193): capability.sock, opening cards, dispatch
        self.goals = goal_mod.Goals(self)       # P158: <state>/goal.json, `agentj goal …` over capability.sock
        from . import learn as learn_mod
        self.learner = learn_mod.Learner(self)  # P204: `/learn` — one skill proposal at a time, written after the owner's yes
        self.peers = None                       # P71: Agent friends (peer_service.PeerService, PROTOCOL §17), set in run()
        self.hooks = None                       # P366: webhooks (hook_service.HookService, AGENTJ-HOOKS-SPEC), set in run()
        self.upg: dict | None = None            # P203: the open upgrade confirmation card (one at a time, §29)
        from . import busy as busy_mod
        self.busy = busy_mod.Board(st.root, on_change=self.busy_changed, verify_sources=True)   # P150 (§22): who works in the background
        self.busy.remote_aliases = lambda: preferences.get(self.preferences, "ui.remote_servers", [])
        from . import native_owner_launch
        self.busy.trusted = lambda: native_owner_launch.board_trusted(self.agent)
        from .busy_launch import Launch
        self.busy.launch = Launch(st.root)      # #450: outside jobs inherit a trusted launching turn (ADR-#450)
        self.busy.chain = self.busy.launch.current_chain   # 0269-w3-policy E2: native jobs started in an owner turn
        self.interrupts = interrupts_mod.Counter(st.root)  # E4: cards / questions per task, counts only
        from .peer_origin import Launches, register
        self.peer_launch = Launches(st.root)    # #455: owner-signed systemd-run units whose sessions may speak same_host
        with contextlib.suppress(Exception):    # #455 F3 B: the dispatch registry, before any fence starts (stat only)
            register()
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
        pass


    async def _ws_send(self, data) -> None:
        if self.ws is None:
            raise ConnectionError("relay down")
        self._tap("out", data)
        await self.ws.send(data)

    async def _op(self, op: int, cid: int, payload: bytes = b"") -> None:
        await self._ws_send(bytes([op]) + cid.to_bytes(4, "big") + payload)

    def _detach(self, cid: int, pair_result: dict | None = None) -> Session | None:

        if getattr(self, "voice_stream", None) is not None:
            self._post(self.voice_stream.drop, cid)
        s = self.sessions.pop(cid, None)
        if s:
            from . import auth_settings_uv
            auth_settings_uv.clear(self, s)
        if s:self.bots.detach(s)
        if s and getattr(self, "term_desk", None) is not None:
            self.term_desk.session_gone(s)       # P408: the opening session gone → the terminal closes (relay_lost)
        if s and getattr(self, "assist", None) is not None and self.assist.terminal.owner is s:
            self._bg(self.assist.end("customer_disconnected"), "assist_end")
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

        file_message = obj
        file_key = obj.get('t') == 'private_file_res' and any(k in obj for k in ('key', 'nonce_prefix'))
        direct = getattr(getattr(self, 'private_files', None), 'direct', None)
        async def file_delivery_live():
            if not file_key:
                return True
            current = getattr(getattr(self, 'private_files', None), 'direct', None)
            return bool(direct is not None and current is direct and await direct.delivery_live(s, file_message))
        from .silent import public_message
        obj = public_message(obj)
        from . import display_privacy
        if isinstance(obj, dict) and obj.get("t") in display_privacy.DISPLAY_MESSAGES:
            obj = await asyncio.to_thread(display_privacy.project, self, obj)
        def special_delivery_live():
            if obj.get('t')!='secret_out_val':return True
            from . import approval_special
            out=getattr(getattr(self,'elevate',None),'out',None)
            card=getattr(out,'cards',{}).get(obj.get('id'))
            return bool(card and card.get('delivery_authority')==(s,self.st.sign_key(s.device))
                        and self.st.is_allowed(s.pub) and approval_special.source_allows(card,self))
        async with self.send_lock:  # nonce order == wire order
            if not await file_delivery_live():return False
            if not special_delivery_live():return False
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
                if not await file_delivery_live():return False
                if not special_delivery_live():return False
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
                cert = await asyncio.wait_for(asyncio.to_thread(self.seat.cert), RELAY_OPEN_TIMEOUT)
                phase = "connect"
                async with connect(url, max_size=2**17, ping_interval=resource_policy.PING_INTERVAL, ping_timeout=resource_policy.PING_TIMEOUT, open_timeout=resource_policy.OPEN_TIMEOUT) as ws:
                    phase = "auth"
                    await self._auth(ws, cert)
                    phase = "revocations"  # a Dashboard failure is distinct from relay authentication
                    await self.credential_revocations.sync()  # P490: apply before accepting any restored remote
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
                        elif isinstance(msg, str):
                            await self.private_files.direct.relay_message(ws, msg)
            except Exception as e:  # network, handshake refusal, relay auth failure → back off and retry
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
            fails = 0 if phase == "up" else fails + 1
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

        key, now = (d["phase"], d["why"], d["code"], d["http"]), time.monotonic()
        if key == self.relay_said[0] and now - self.relay_said[1] < RELAY_SAY:
            return
        self.relay_said = (key, now)
        self.emit("relay_fail", line=f"中继连接失败 / relay connect failed #{d['attempts']}: {relaydiag.text2(d)} · "
                  f"{d.get('retry_in')} s 后重试 / retry in {d.get('retry_in')} s")

    def _say_mbox_fail(self, d: dict) -> None:

        key, now = (d.get("phase"), d.get("why"), d.get("code"), d.get("http")), time.monotonic()
        said = getattr(self, "mbox_said", (None, 0.0))
        if key == said[0] and now - said[1] < RELAY_SAY:
            return
        self.mbox_said = (key, now)
        self.emit("mbox_fail", line=f"好友信箱连接失败 / friends mailbox connect failed: {relaydiag.text2(d)}")

    async def seat_loop(self) -> None:

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

        if self.relay_up:
            return "up"
        if self.relay_seat_refused:
            return "seat"
        if not self.relay_ever_up and time.monotonic() - self.started_at < RELAY_GRACE:
            return "connecting"
        return "reconnecting"

    async def loop_watch(self) -> None:

        probe = loopwatch.StallProbe(asyncio.get_running_loop(), LOOP_STALL)   # P136: and what was running when it stuck
        probe.start()
        try:
            while not self.stopping.is_set():
                t = time.monotonic()
                probe.beat()
                await asyncio.sleep(1)
                from . import main_thread
                if main_thread.status(self) is not None:
                    self.status_changed()
                late = time.monotonic() - t - 1
                if late >= LOOP_STALL:
                    self._recovery_stall = (time.monotonic(), int(late * 1000))
                    self.st.log("loop_stall", ms=int(late * 1000), **probe.take())
                else:
                    probe.take()
        finally:
            probe.stop.set()

    async def _auth(self, ws, cert: str | None | bool = False) -> None:
        if cert is False:                          # P292: never a file read on the loop inside the auth deadline
            cert = await asyncio.to_thread(self.seat.cert)
        msg = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT))
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


    def _new_session(self, cid: int) -> Session | None:
        if getattr(self, '_unbinding_all', False):
            return None
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
            with provenance.paired_request(lambda: self._paired_live(s)):
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
            did = wire.device_id(hs.rs)
            self.st.log("pk_hello", cid=s.cid, device=did)
            msg2 = hs.write_message(b"")
            s.send, s.recv, s.h = hs.split()
            s.pub, s.device, s.mode, s.state = hs.rs, did, "pk", "hello"
            s.sign_pub = _sign_key(json.loads(payload))
            await self._op(wire.OP_DATA, s.cid, bytes([wire.HS_RESP]) + msg2)
            return
        if not self.st.is_allowed(hs.rs):
            did = wire.device_id(hs.rs)
            self.st.log("unknown_device", cid=s.cid, device=did)
            msg2 = hs.write_message(b"")
            s.send, s.recv, s.h = hs.split()
            s.pub, s.device, s.mode, s.state = hs.rs, did, "gone", "hello"
            s.gone = self.st.removed_why(did) or "revoked"
            s.gone_moved = s.gone == "replaced" and self.st.removed_moved(did)
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

    def _paired_live(self, s: Session) -> bool:
        return (isinstance(s, Session) and self.sessions.get(s.cid) is s and s.state == "ready"
                and self.st.is_allowed(s.pub) and provenance.normalize(s.provenance)["kind"] == "phone")

    async def _app(self, s: Session, obj: dict) -> None:
        from . import application_ingress
        with application_ingress.paired(self, s, obj):
            return await self._app_frame(s, obj)

    async def _app_frame(self, s: Session, obj: dict) -> None:
        t = obj["t"]
        if s.state == "hello":
            if t != "hello":
                return await self.close_cid(s.cid, "expected_hello")
            if s.mode == "gone":
                await self.send_app(s, {"t": "removed", "why": s.gone, **({"moved": True} if s.gone_moved else {})})
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
        if isinstance(t, str) and t.startswith("call_"):
            return await self.voice_stream.handle(s, obj)
        if t in ("wf_resume_get", "wf_resume_do", "fr_report_reply", "fr_report_remark"):
            from . import report_actions
            return await report_actions.on_phone(self, s, obj)
        if t == "policy_prepare":
            r, action, target = obj.get("r"), obj.get("action"), obj.get("target")
            if not wire.is_rid(r) or action != "host_write" or not isinstance(target, dict):
                return
            try:
                target = self._policy_target(target.get("kind"), target)
                digest = controls.object_digest(action, target)
                n = self.nonces.issue_mail(s.device,digest)
                pk = self.st.passkey_of_device(s.device)
                fields = {"n":n, "target":target, "level":"uv",
                          **action_policy.summary(action, target, lang=self.lang, presentation=True)}
                if pk:
                    fields.update(fa=pk["id"], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(passkey.elevate_challenge(self.channel,s.device,n,action,n,digest)))
                return await self.send_app(s, {"t":"policy_ready", "r":r, **fields})
            except (KeyError, TypeError, ValueError):
                return
        if t in ("msg", "say", "answer", "slash", "blob_open", "blob_chunk", "blob_end", "ask_answer", "estop", "resume", "task_set", "task_migrate", "lt_answer") or t in elevate.PHONE_TYPES or t in ("fr_answer", "fr_accept"):
            self._phone_touch()                   # P292: config lock + write, off the loop
        if t == 'main_thread_interrupt':
            from . import main_thread
            return await main_thread.interrupt(self, s, obj)
        if t in ("private_file_get", "private_file_revoke"):
            return await self.private_files.on_phone(s, obj)
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
            if cmd and cmd[0] != "goal" and self.agent and cmd[0] not in slash.AGENT_CMDS and not self.agent.passthrough(cmd[0]) \
                    and self.native_route(cmd[0]) not in ("run", "agent"):   # P412 #175: the harness's own → through / to the Agent
                return await self.on_slash(s, cmd[0], cmd[1], confirm=False, typed=True)
            await self._accept(s, text, sid=None)
        elif t in P33_ONLY:
            if s.p33:                          # §10 messages only from a device that announced p33 (§10.0)
                await self.on_p33(s, t, obj)
        elif t in ("bots_read", "bots_write"):
            await self.bots.phone(s, obj, background=True)
        elif isinstance(t, str) and t.startswith(("fr_", "pg_")):   # P71 (§17.7): the friends page
            await self.on_friends(s, t, obj)
        elif t == "event_write":
            from .subscriptions import router
            await router(self).on_phone(s, obj)
        elif t in ("heartbeat_get", "heartbeat_write"):
            await self.heartbeat.on_phone(s, obj)
        elif t == "wechat":
            if self.wechat is not None:
                await self.wechat.on_phone(s, obj)
        elif t == "approval_review":
            await approval_review.on_phone(self, s, obj)
        elif t == "answer":
            await self._answer(s, obj)
        elif t in ("roster_bind_get", "roster_bind"):
            if self.peers is not None and s.p33:
                await self.peers.xfer.bind_phone(s, obj)
        elif t in ("pk_reg", "pk_offer_req"):  # F20 (§12): register this device's passkey
            await self._pk_reg(s, t, obj)
        elif t in PKPEER_TYPES:                # P547: Face ID set up with another remote's approval (pkpeer.py)
            if s.p33 or t in ("pk_peer_answer", "pk_peer_cancel"):
                await self.pkpeer.on_phone(s, t, obj)
        elif t in assist_mod.PHONE_TYPES:      # 0269-w2-assist (§41): assist_grant (signed + Face ID) · assist_revoke (signed)
            if s.p33:
                await self.assist.on_phone(s, t, obj)
        elif t in SEAT_XFER_TYPES:             # P547: seat transfer — signed + this remote's Face ID (seat_transfer.py)
            if s.p33:
                await self.seat_xfer.on_phone(s, t, obj)
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
                await self.send_app(s, {"t": "push_state", "on": s.device in webpush.load_subs(self.st)})
                return
            subs = webpush.load_subs(self.st)
            old = subs.get(s.device) or {}
            if isinstance(old.get("kinds"), list):          # P204: a re-subscribe keeps the device's categories
                sub["kinds"] = old["kinds"]
            if old != sub:
                subs[s.device] = sub
                webpush.save_subs(self.st, subs)
                self.st.log("push_sub", device=s.device)
            self.push_generation[s.device] = self.push_generation.get(s.device, 0) + 1
            await self.send_app(s, {"t": "push_state", "on": True})
        elif t == "push_pref":                 # P204 (§9): which kinds this device wants (审批 ask / 完成 reply / 告警 security)
            kinds = webpush.parse_kinds(obj)
            subs = webpush.load_subs(self.st)
            if kinds is None or s.device not in subs:
                self.st.log("push_pref_refused", device=s.device)
                return
            if subs[s.device].get("kinds") != kinds:
                subs[s.device]["kinds"] = kinds
                webpush.save_subs(self.st, subs)
                self.st.log("push_pref", device=s.device, status=",".join(kinds) or "none")
        elif t == "push_off":
            subs = webpush.load_subs(self.st)
            if subs.pop(s.device, None):
                webpush.save_subs(self.st, subs)
                self.st.log("push_off", device=s.device)
            self.push_generation[s.device] = self.push_generation.get(s.device, 0) + 1
            # Correlation is an echo to this paired session only, never stored or logged.
            r = obj.get("r")
            ack = {"t": "push_state", "on": False}
            if (isinstance(r, str) and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", r)):
                ack["r"] = r
            await self.send_app(s, ack)
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
        elif t in ("files_get", "files_write") and s.p33:
            await self.local_files.on_phone(s, obj)
        elif t == "native_scheduler_control" and s.p33:
            from .native_scheduler_control import freeze_phone_boundary, on_phone
            boundary = freeze_phone_boundary(self, s)
            self._bg(on_phone(self, s, obj, _bound=boundary), "native_scheduler_control")
        elif t in ("setup_run", "setup_cancel"):
            from .install_assist import Runner
            if not hasattr(self, "install_runner"):
                self.install_runner = Runner(self)
            self._bg(self.install_runner.on_phone(s, obj), "install_assist")
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
        elif t in SKILL_ACTS:           # P426 #181-3 (§39): archive / restore one skill (a paired, ready remote's own tap)
            if s.p33:
                await self.on_skill_act(s, t, obj)
        elif t in jcards.PHONE_TYPES:   # P360 (§34): Agent J Cards — the list, one card's data + buttons, a card's switch
            if s.p33:
                await self.on_jcard(s, t, obj)
        elif t in ("security_status","security_begin","security_verify","security_retire"):
            await self.security_rotation.on_phone(s,obj)
        elif t in REMOTES_TYPES:        # P171 (§24): list / rename / unbind / add remotes from a paired remote
            if s.p33:
                await self.remotes.on_phone(s, t, obj)
        elif t == "layout_diag":        # P489: measurements only, paired + allowlisted above; no Agent turn
            if s.p33 and wire.is_rid(obj.get("r")):
                from . import layout_diag
                now = time.monotonic()
                last = getattr(s, "layout_diag_at", None)
                if last is not None and now - last < 5:
                    ok, why = False, "rate"
                else:
                    s.layout_diag_at = now
                    ok = await asyncio.to_thread(layout_diag.save, self.st, obj.get("sample"))
                    why = "save_failed" if not ok else None
                if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                    await self.send_app(s, {"t":"layout_diag_res", "r":obj["r"], "ok":ok, **({"why":why} if why else {})})
        elif t == "agent_name":         # P181 (§22.2): long-press the Agent's name → rename, signed like every phone write
            if s.p33:
                await self.on_agent_name(s, obj)
        elif t == "mail_allow":         # P207 (§25): the address book adds an address to the mail allowlist (signed)
            if s.p33:
                await self.on_mail_allow(s, obj)
        elif t == "approval_set":       # P336 (§8): approval level / goal without the opening card — signed, never pref_set
            if s.p33:
                await self.on_approval_set(s, obj)
        elif t in ("owner_hosts_get", "owner_hosts_set"):
            if s.p33:
                await self.owner_hosts.on_phone(s, obj)
        elif t in ("budget_get", "budget_set"):   # 0269-w3-policy (§42): the owner's day / month budget (budget.py)
            if s.p33:
                await self.on_budget(s, t, obj)
        elif t == "forward_set":        # P554 (§8): the forward button's destination, one host setting for every remote (signed)
            if s.p33:
                await self.on_forward_set(s, obj)
        elif t == "cmd_run":            # P361 (§34): Settings 「执行」 — signed + Face ID, whitelisted names only (cmdrun.py)
            if s.p33:
                await self.cmd_desk.on_phone(s, obj)
        elif t in term_mod.PHONE_TYPES:  # P408 (§38): the web terminal — during an e-stop only term_close / term_forget act
            if s.p33:
                await self.term_desk.on_phone(s, obj)
        elif t == "slash":              # a command from the phone's ≡「全部命令」 menu (or typed: the page sends it like this)
            name, arg = obj.get("cmd"), obj.get("arg", "")
            if isinstance(name, str) and isinstance(arg, str) and len(name) <= 32 and text_units(arg) <= slash.ARG_MAX:
                await self.on_slash(s, name.lower(), arg.strip(), confirm=obj.get("confirm") is True, typed=False, authorization=obj)


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

        return None if gate.is_set(self.st) else False

    async def decide(self, p: Pairing, code: str, passphrase: str | None = None, terminal: bool = False,
                     retry: bool = False) -> dict:

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
        if (getattr(self, '_unbinding_all', False) or self.sessions.get(s.cid) is not s
                or s.state != 'pending'):
            await self.close_cid(s.cid, 'unbound_all')
            return {'ev': 'denied', 'reason': 'unbound_all'}
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
        s.provenance = provenance.phone()
        s.state, s.ready_at = "ready", time.monotonic()
        self.reporter.trigger("approve")
        await self.send_app(s, {"t": "approved", **self._caps(s)})
        await self._bulk(s)
        await self._pk_offer(s)                 # F20: once, right after a pairing (never on a resume)
        await self._rt_issue(s, force=True)     # P122: the renewal ticket for this pairing (§12.1)
        await self.remotes.changed()           # P525: every open remote recomputes pairing hints/checklist
        self.st.log("pair_approved", cid=s.cid, device=s.device, name=s.name, code_ok=source == "local", source=source)
        self.emit("approved", device=s.device, name=s.name)
        self._bg(self._ready_then_onboard(s), "onboard")   # F28: the first remote ever → the main Agent's welcome there
        res = {"ev": "approved", "device": s.device, "name": s.name}
        res.update({k: got[k] for k in ("replaced", "evicted") if got[k]})
        return res


    async def _send_pair1(self, obj_fn) -> None:
        for x in list(self.sessions.values()):
            if x.state == "ready" and x.pair1 and self.st.is_allowed(x.pub):
                await self.send_app(x, obj_fn(x))

    async def _pair_new(self, s: Session, obj: dict) -> None:

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
        if getattr(self, '_unbinding_all', False) or not self.st.is_allowed(s.pub):
            return
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

        p = self.pairing
        if not p or p.phone != s.device:
            return
        cid = p.cid
        self._pair_finish(p, {"ev": "denied", "reason": "cancelled"})
        if cid is not None and cid in self.sessions:
            await self.close_cid(cid, "pair_cancelled")
        self.st.log("pair_cancelled", device=s.device)

    def _bind_native_ask(self, a, kind, subject=None):
        # Only these Host constructors can create this private authority record.
        if kind not in ('pair', 'pass_reset', 'restart', 'terminal_cfg', 'held_channel'):
            raise ValueError('native_ask_kind')
        from . import approval_special
        if kind == 'held_channel':
            from .wechat import _HeldChannelApproval
            if type(subject) is not _HeldChannelApproval or subject.host is not self or a.tool != 'wechat_confirm':
                raise ValueError('held_channel_subject')
            book = self.__dict__.setdefault('_held_channel_asks',{})
            if a.rid in book or len(book) >= 128:raise ValueError('held_channel_full')
            subject.bind(a)
            book[a.rid] = (a,subject)
        a.host_card = True
        main = ((None,None,None) if kind == 'held_channel' else
                (self.agent,approval_special._current_task(self),approval_review.digest(approval_special._current_source(self))))
        a.native_authority = (self,self.channel,str(self.hist.epoch),kind,subject,
                              a.tool,a.input_sha,a.digest,*main)

    def _native_ask_live(self, a, *, pending=True):
        ref = getattr(a, 'native_authority', None)
        if (not isinstance(ref, tuple) or len(ref) != 11 or ref[0] is not self
                or ref[1] != self.channel or ref[2] != str(self.hist.epoch)
                or self.stopped() or time.monotonic() > a.deadline
                or (a.tool, a.input_sha, a.digest) != ref[5:8]
                or approvals.input_digest(a.tool_input) != ref[6]):
            return False
        kind, subject = ref[3:5]
        if kind == 'held_channel':
            from .wechat import _HeldChannelApproval
            return (type(subject) is _HeldChannelApproval and subject.host is self
                    and subject.ask is a and subject.live())
        if pending and self.asks.get(a.rid) is not a:return False
        if kind == 'pair':
            pairing, session = subject
            return (self.pairing is pairing and self.sessions.get(session.cid) is session
                    and session.state == 'pending' and session.card == a.rid)
        if kind == 'pass_reset':
            from . import pass_reset
            return a.tool == pass_reset.TOOL
        if kind == 'terminal_cfg':
            from .terminal import TOOL
            return a.tool == TOOL and subject is self.term_desk
        if kind == 'restart':
            from . import approval_special
            return (self.agent is ref[8] and approval_special._current_task(self) == ref[9]
                    and approval_review.digest(approval_special._current_source(self)) == ref[10])
        return False

    def _pair_card_open(self, p: Pairing, s: Session) -> None:

        if not self._approvers():
            return
        s.deadline = max(s.deadline, s.pending_since + pair_card.CARD_TTL)
        self._set_timer(s, self._approve_timeout(s))
        text = pair_card.summary(self.lang, s.name, wire.safety_code(s.h), self._pending_event(s))
        target = {"device": s.device, "handshake": pair_card.handshake_id(s.h)}
        rid = secrets.token_hex(16)
        a = Ask(rid, pair_card.TOOL, text, approvals.shown_digest(pair_card.TOOL, text), target, approvals.input_digest(target),
                s.deadline, asyncio.get_running_loop().create_future(), require_passkey=True, pk_kind=pair_card.PK_KIND)
        self._bind_native_ask(a, 'pair', (p, s))
        self.asks[rid] = a
        s.card = rid
        self.st.log("ask", id=rid, tool=pair_card.TOOL, agent="host", kind="pair")
        self.activity("ask", id=rid, tool=pair_card.TOOL, summary=text.split("\n", 1)[0])
        self.emit("ask", id=rid, tool=pair_card.TOOL, summary=text)
        self._post(self._send_pair1, lambda x: self._ask_msg(a, x))
        self.status_changed()
        self.push_notify("ask")
        self._bg(self._pair_card_wait(p, s, a), "pair_card")


    async def _pass_consent(self, req: dict, pid) -> dict:

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
                asyncio.get_running_loop().create_future(), require_passkey=True, pk_kind=pass_reset.PK_KIND)
        self._bind_native_ask(a, 'pass_reset', pid)
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
        if decision == "allow" and (not self._native_ask_live(a, pending=False) or self.st.sign_key(did) != sk):
            decision, did = "deny", None
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
        why = _done_why(reason, "allow" if result == "allow" else "deny", a)
        self._post(self._send_ready, lambda x: {"t": "ask_done", "id": rid, "result": result, **why})
        self.status_changed()
        if result != "allow":
            return {"ok": False, "error": "denied" if result == "deny" else reason}
        tok = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._pp_tickets = {t: v for t, v in self.__dict__.get("_pp_tickets", {}).items() if v[1] > now}
        self._pp_tickets[tok] = (pid, now + pass_reset.TICKET_TTL, did)
        return {"ok": True, "ticket": tok, "by": self._dname(did)}

    async def _pass_replace(self, req: dict, pid) -> dict:

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
        live = (self.sessions.get(s.cid) is s and s.state == "pending" and s.card == a.rid
                and self._native_ask_live(a, pending=False) and (not did or self.st.sign_key(did) == sk))
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
        why = _done_why(reason, "allow" if result == "allow" else "deny", a)
        self._post(self._send_ready, lambda x: {"t": "ask_done", "id": a.rid, "result": result, **why})
        self.status_changed()
        if reason == "device" and result == "allow":   # the chat line is onboarding's 「…也连上了」 (one per new remote)
            self.push_notify("security")


    def onboarding_lang(self) -> str:
        return "en" if str(preferences.get(self.preferences, "appearance.language", "zh")).lower().startswith("en") else "zh"

    def speaker_note(self, s: Session) -> str:

        n = device_names(self.st.devices().get(s.device))
        if n["named_by"] != "owner":
            return ""
        label = self._dev_label(s.device)
        if self.onboarding_lang() == "en":
            return (f"\n\n〔Sent from the remote the owner named “{label}”. Use it to tell who is speaking; it is a label the "
                    "owner set, not proof of identity.〕")
        return f"\n\n〔发言的遥控器：「{label}」——这是主人给这台遥控器起的名字，用来分辨是谁在说话；只是标签，不是身份证明。〕"

    def skill_turn_note(self, s: Session, text: str) -> str:

        if not provenance.is_owner(getattr(s, "provenance", None)) or not isinstance(text, str):
            return ""
        try:
            from . import skill_ops, skill_usage
            cfg = self.agent_cfg or {}
            names = skill_usage.installed(cfg.get("kind"), cfg.get("dir"))
            cmd = slash.parse(text)
            if cmd:
                if cmd[0] in names:
                    skill_usage.note(self.st, skill_usage.canonical(cmd[0]), "phone")
            else:
                skill_ops.record(self.st, "ask", text, names)
            return skill_ops.take_note(self.st, self.onboarding_lang(), getattr(self, "preferences", None))
        except Exception:  # noqa: BLE001 — never costs the owner's message
            self.st.log("skill_note", status="error")
            return ""

    def onboarding_note(self) -> str:

        try:
            from . import capability_pause
            return '\n'.join(x for x in (onboarding.turn_note(self.st, self.onboarding_lang()),
                                          capability_pause.reminder(self)) if x)
        except Exception:  # noqa: BLE001 — onboarding never costs a message
            self.st.log("onboarding", status="note_error")
            return ""

    async def _ready_then_onboard(self, s: Session) -> None:
        await self.on_ready(s, None)
        await self.onboarding_paired(s)

    async def onboarding_paired(self, s: Session) -> None:

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

        from . import contacts as C
        await asyncio.sleep(self.OWNER_CARD_DELAY)
        try:
            if self.stopped() or C.owner(await asyncio.to_thread(C.read, self.st)) is not None:
                return
            res = await self.elevate.contacts.card({"op": "card", "owner": True, "contact": {}})
            self.st.log("owner_card", result=res.get("result"))
        except Exception:  # noqa: BLE001 — a failed card never costs the welcome
            self.st.log("owner_card", result="error")

    def _track_reload_load(self, device: str, why: str | None) -> None:
        if why != "load" or not device:
            return
        now = time.monotonic()
        loads = self.reload_loads.setdefault(device, deque())
        while loads and now - loads[0] > 60:
            loads.popleft()
        loads.append(now)
        if len(loads) < 5 or now - self.reload_storm_at.get(device, float("-inf")) < 600:
            return
        self.reload_storm_at[device] = now
        self.st.log("reload_storm", device=device, n=len(loads), window=60)
        self.local_notice(pick(self.lang,
            "遥控器网页在一分钟内多次重新加载；已记录异常，请检查自动刷新或预览页。",
            "The remote page reloaded repeatedly within one minute; check automatic refresh or preview."))

    async def _accept_resume(self, s: Session, since) -> None:

        s.provenance = provenance.phone()
        s.state = "ready"
        self._set_timer(s, None)
        await self.send_app(s, {"t": "ready", **self._caps(s)})
        await self._bulk(s)
        s.ready_at = time.monotonic()
        why, code, down = s.rc or (None, None, None)
        self.st.log("resume_ok", cid=s.cid, device=s.device, why=why, code=code, ms=down)
        self._track_reload_load(s.device, why)
        self._bg(asyncio.to_thread(self._touch_seen, s.device), "touch_seen")   # P292: ≤ 1 write a minute, off the loop
        self.emit("ready", device=s.device, name=s.name)
        self.reporter.trigger("online")
        await self.on_ready(s, since)


    @staticmethod
    def _pk_hello(payload: bytes) -> bool:

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

        if getattr(self, '_unbinding_all', False):
            return await self._pk_fail(s, 'unknown')

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
            att = passkey.attachment(obj.get("aa", obj.get("att")))   # P539: advisory only, outside every check
            new = self.st.passkey_restore(old, f["id"], s.pub, s.sign_pub, count, _iid(obj) or None,
                                          local=None if att is None else att == "platform")
            if not new:
                raise passkey.PasskeyError("unknown", "gone")
        except passkey.PasskeyError as e:
            self.st.log("pk_restore", cid=s.cid, device=old or s.device, result="fail", reason=e.detail)
            return await self._pk_fail(s, e.why)
        if old != new:
            self.st.log("auto_unbind", device=old, reason="passkey")
            await self._drop_device(old, "replaced", moved=True)   # 0.30.7: same remote, new browser storage
        s.device, s.mode = new, "resume"
        s.name = self._dev_label(new)
        self.st.log("pk_restore", cid=s.cid, device=new, result="ok", id=old)
        self.emit("passkey", device=new, name=s.name)
        await self.send_app(s, {"t": "pk_ok"})
        await self._accept_resume(s, s.since)
        await self._rt_issue(s, force=True)       # P122: the old context's ticket died with it (State.passkey_restore)


    async def _rt_restore(self, s: Session, obj: dict) -> None:

        if getattr(self, '_unbinding_all', False):
            return await self._pk_fail(s, 'unknown')

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
            await self._drop_device(old, "replaced", moved=True)
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

        if s.state != "ready" or not self.st.is_allowed(s.pub) or (not force and time.monotonic() - s.rt_at < 60):
            return
        if self.st.set_ticket(s.pub, renewal.new_ticket()):
            self.st.log("rt_issue", device=s.device)
            await self._rt_send(s)

    async def _rt_send(self, s: Session) -> None:

        rt = (self.st.devices().get(s.device) or {}).get("rt")
        if isinstance(rt, dict) and isinstance(rt.get("i"), str) and isinstance(rt.get("k"), str):
            s.rt_at = time.monotonic()
            await self.send_app(s, {"t": "rt", "i": rt["i"], "k": rt["k"]})

    async def _pk_offer(self, s: Session, peer: str | None = None, *, asked: bool = False) -> None:

        if not s.p33 or s.state != "ready" or not self.st.is_allowed(s.pub):
            return
        if peer is not None and not self.pkpeer.approved(s.device, peer):
            self.st.log("pk_offer", device=s.device, result="refused", reason="peer_gone")
            return
        if peer is None and not self.pkpeer.direct_ok(s.device):
            self.st.log("pk_offer", device=s.device, result="refused", reason="peer_required")
            if asked:
                await self.send_app(s, {"t": "pk_reg_res", "ok": False, "why": "peer_required"})
            return
        nonce = secrets.token_bytes(32)
        s.pk_offer = (nonce, time.monotonic(), peer)
        live = [o for o in self.pk_offers.get(s.device, []) if time.monotonic() - o[1] <= passkey.OFFER_TTL]
        self.pk_offers[s.device] = (live + [s.pk_offer])[-PK_OFFERS:]
        pk = None if peer else self.st.passkey_of_device(s.device)
        await self.send_app(s, {"t": "pk_offer", "n": wire.b64u(nonce), **({"peer": peer} if peer else {}),
                                **({"replace": pk["id"]} if pk else {})})

    async def _pk_reg(self, s: Session, t: str, obj: dict) -> None:
        if not s.p33:
            return
        if t == "pk_offer_req":
            return await self._pk_offer(s, asked=True)
        offer = _pk_pick(obj, s.pk_offer, self.pk_offers.get(s.device, []))   # P565 追加 A: this socket's or the device's
        s.pk_offer = None                             # single use, whatever the outcome
        if offer is not None:
            for x in list(self.sessions.values()):   # review S3: the same offer held by the device's half-open earlier socket ends too
                if x.device == s.device and x.pk_offer and x.pk_offer[0] == offer[0]:
                    x.pk_offer = None
            mine = [o for o in self.pk_offers.get(s.device, []) if o[0] != offer[0]]
            if mine:
                self.pk_offers[s.device] = mine
            else:
                self.pk_offers.pop(s.device, None)
        try:
            if not offer or time.monotonic() - offer[1] > passkey.OFFER_TTL:
                if _pk_same(self.st.passkey_of_device(s.device), obj):
                    self.st.log("pk_reg", cid=s.cid, device=s.device, result="ok", reason="again")
                    return await self.send_app(s, {"t": "pk_reg_res", "ok": True})
                raise passkey.PasskeyError("expired", "no_offer")
            rec = passkey.verify_registration(obj, offer[0])
            rec.update(_pk_where(obj))               # P539: advisory local / plat (never part of a check)
            peer = offer[2] if len(offer) > 2 else None
            peer_ok = bool(peer) and self.pkpeer.approved(s.device, peer)
            if peer and not peer_ok:
                raise passkey.PasskeyError("expired", "peer_gone")
            if not peer and not self.pkpeer.direct_ok(s.device):   # review N3: another remote got a key since this direct offer
                raise passkey.PasskeyError("peer_required", "peer_required")
            had = self.st.passkey_of_device(s.device)
            if not self.st.set_passkey(s.pub, rec, replacement=obj.get("replace_fa"), peer_ok=peer_ok,
                                       challenge=passkey.replacement_offer_challenge(offer[0], had)):
                raise (passkey.PasskeyError("proof", "replace_unproven") if had and not peer_ok
                       else passkey.PasskeyError("unknown", "gone"))
            if peer_ok:
                via = self.pkpeer.via_of(peer)
                self.pkpeer.used(peer)
                if via and self.peers is not None:   # P574: same-owner friends may now trust this key (owner_grant.via_ok)
                    with contextlib.suppress(Exception):
                        self.peers.xfer.note_via(rec["id"], {**via, "bind": obj.get("roster_sig") or ""})
        except passkey.PasskeyError as e:
            self.st.log("pk_reg", cid=s.cid, device=s.device, result="fail", reason=e.detail)
            return await self.send_app(s, {"t": "pk_reg_res", "ok": False, "why": e.why})
        if self.peers is not None:
            binding = {k: rec[k] for k in ("id", "alg", "spki", "rp")}
            binding.update(dev=s.device, name=self._dev_label(s.device), sign_pub=wire.b64u(self.st.sign_key(s.device) or b""), bind=obj.get("roster_sig") or "")
            if owner_grant.binding_ok(binding):
                with self.peers.store._edit("roster-via.json", {}) as rows:
                    rows.setdefault(rec["id"], {})["bind"] = binding["bind"]
                self.peers.xfer.roster_tick()
            with contextlib.suppress(Exception):
                self.peers.same_owner.legacy_notice()   # 0304 security impl r1 M1: first passkey → legacy relations listed once
        self.pk_offers.pop(s.device, None)            # P565 追加 A: a key was taken — the device's other offers end with it
        self.st.log("pk_reg", cid=s.cid, device=s.device, result="ok")
        await self.send_app(s, {"t": "pk_reg_res", "ok": True})
        with contextlib.suppress(Exception):     # P552 (#325 ①): a new / replaced key while a switch is not in force → one tap
            if approval_level.unsigned(self.preferences, self.st):
                await self.send_app(s, {"t": "approval_resign", "text": approval_level.resign_hint_text(self.lang)})
                await self.send_app(s, self.preferences_msg(s))
        await self.remotes.changed()              # P525: passkey row is state, too
        await self.elevate.out.on_ready(s)        # F32: open pickup cards now carry this device's Face ID (`fa`)

    def _observe_view(self) -> dict | None:

        a = self.agent
        if a is None or not hasattr(a, "_observe_errors"):
            return None
        return {"errors": a._observe_errors, "down": bool(getattr(a, "_observe_down", False)),
                "error": getattr(a, "_observe_error", None),
                "menu": (getattr(getattr(a, "menu_desk", None), "current", None) or {}).get("kind")}

    def _bg(self, coro, what: str = "bg") -> asyncio.Task:

        tasks = self.__dict__.setdefault("_bg_tasks", set())
        task = asyncio.get_running_loop().create_task(coro)
        tasks.add(task)

        def done(t: asyncio.Task) -> None:
            tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                self.st.log("bg_task_failed", why=what, exception_type=type(t.exception()).__name__)
        task.add_done_callback(done)
        return task


    def _touch_seen(self, did: str) -> None:
        with contextlib.suppress(OSError):
            self.st.touch_seen(did)

    def _phone_touch(self) -> None:

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

        from . import ctl_auth
        cmd = req.get("cmd")
        if cmd == "terminal_cfg" and (req.get("op") not in ("enable", "allow") or approval_level.autonomous(self)):
            # #520: full access routes to the host's real terminal card. First use
            # still requires its UV; repeated access needs no typed-owner claim.
            return True                       # P408: reading and narrowing the web terminal need only the socket
        tok = req.get("claim")
        claims = getattr(self, "ctl_claims", {})
        rec = claims.pop(tok, None) if isinstance(tok, str) else None
        claimed = bool(rec and rec[0] == pid and time.monotonic() < rec[1])
        if claimed and cmd in ("resume", "pk_peer_host"):   # P555: the terminal Face ID approval: resume's bar, password required
            if cmd == "pk_peer_host" and not gate.is_set(self.st):   # review B1: a tty is no proof against a same-uid Agent
                self.st.log("ctl_refused", cmd=cmd, reason="no_passphrase")
                await self._ctl_send(w, {"ok": False, "error": "no_passphrase"})
                return False
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
        if claimed and cmd in ("files_prepare", "passphrase_consent", "passphrase_replace", "terminal_cfg") and not ctl_auth.has_tty(pid):
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
        origin_key = self._cfg_origin_key()
        hit = [c for c in pend if set(c["patch"]) & set(patch)]
        if not keys and not hit:
            async def still_ordinary():           # under config_apply_lock: an owner change meanwhile cannot ride along
                now = preferences.validate(preferences.parse(raw) if isinstance(raw, str) else preferences.read()[1])
                extra = ctl_auth.security_changes(self.preferences, now)
                return {"ok": False, "code": "conflict", "keys": extra, "key": extra[0],
                        "error": "changed while waiting"} if extra else None
            return await self._ctl_send(w, await self.apply_preferences(raw, build=still_ordinary))
        if len(hit) == 1 and origin_key is not None and hit[0].get("origin_key") == origin_key and all(hit[0]["patch"].get(k) == v for k, v in patch.items()):
            self.st.log("ctl_config_card_repeat", keys=",".join(sorted(patch))[:200])   # identical repeat: the card stays
            return await self._ctl_send(w, self._cfg_pending_reply(hit[0]["keys"]))
        for c in hit:                                 # the same config item asked again: one card, the newest values
            if origin_key is not None and c.get("origin_key") == origin_key:
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

        from . import ctl_auth
        pend = self.__dict__.setdefault("_cfg_cards", [])
        patch, base = _cfg_patch(old, candidate)
        origin_key = self._cfg_origin_key()
        for c in [c for c in pend if set(c["patch"]) & set(patch)]:
            if origin_key is not None and c.get("origin_key") == origin_key:
                patch, base = {**c["patch"], **patch}, {**c["base"], **base}
            pend.remove(c)
            if not c["gone"].done():
                c["gone"].set_result("superseded")
            self.st.log("ctl_config_card_merged", keys=",".join(sorted(c["patch"]))[:200])
        view = _cfg_apply(old, patch)
        self._cfg_card_start(patch, base, ctl_auth.security_changes(old, view) or keys, view)

    def _cfg_origin_key(self, origin=None):
        from . import approval_review
        source=approval_review.snapshot(self) if origin is None else origin
        if not source or source.get('verified') is not True or source.get('suspicious') or source.get('root_message_id') is None or source.get('message_id') is None:
            return None
        return approval_review.digest({key:source.get(key) for key in ('session','root_message_id','message_id','channel','material_ref')})

    def _cfg_card_start(self, patch: dict, base: dict, keys: list, view: dict) -> None:
        pend = self.__dict__.setdefault("_cfg_cards", [])
        shown = {k: preferences.get(view, k) for k in keys}    # the card's values; replay must land exactly these
        from . import approval_review
        origin = approval_review.snapshot(self) or {}
        me = {"patch": patch, "base": base, "keys": keys, "gone": asyncio.get_running_loop().create_future(),
              "origin": copy.deepcopy(origin), "origin_key": self._cfg_origin_key(origin)}
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
                self.st.log("ctl_config_card_done", result="deny", why=None if self._online_approvers() else "无遥控器在线 / no remote online")
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
        async def scoped_card():
            with approval_review.origin_scope(me["origin"]):
                await card()
        task = asyncio.ensure_future(scoped_card())
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

    def _completion_caller_known(self, pid, uid):
        """Completion provenance only: live pinned CEO descendants or a signed systemd dispatch."""
        if uid != os.getuid() or type(pid) is not int or pid <= 1:
            return False
        if self.scheduler.completion_caller_known(pid):
            return True
        if self.busy.launch.known_process(pid):
            return True
        known = self.peer_launch.verified_process(pid)
        if known:
            self.peer_launch.save()
        return known

    def _busy_launch_request(self, req, pid, uid):
        """Pin a live wrapper before its command runs; never accept caller-supplied process facts."""
        from . import busy as busy_mod
        if uid != os.getuid() or not isinstance(pid, int) or isinstance(pid, bool):
            return False
        table, sids = self.busy.launch.scan(self.busy.context)
        found = next((it for it in busy_mod.read_items(self.st.root)
                      if it.get("key") == req.get("key") and it.get("pid") == pid), None)
        if found is None:
            return False
        if self._completion_caller_known(pid, uid):
            self.busy.launch.accept_verified(found, table)
        else:
            self.busy.launch.judge(found, self.busy.context, table, sids)
            if self.busy.launch.ok(found["key"]):
                self.busy.launch.accept_verified(found, table)
        self.busy.launch.save()
        return self.busy.launch.ok(found["key"])

    async def on_ctl(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        from . import ctl_auth
        from .shared_hook import peer_pid
        info = getattr(w, "get_extra_info", None)
        pid, puid = peer_pid(info("socket")) if info else (None, None)
        from . import bounded_plan, approval_review
        origin = bounded_plan.caller_origin(self, pid) if puid == os.getuid() else None
        source_token = approval_review.ORIGIN.set(copy.deepcopy(origin or {}))
        from . import application_ingress
        frames = application_ingress.frames(self, r, w, pid, puid)
        try:
            async for line in frames:
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
                if cmd == "local_service_notice":
                    from . import local_service_notice
                    service = await asyncio.to_thread(local_service_notice.verified, pid, puid)
                    if not service:
                        await self._ctl_send(w, {"ok": False, "error": "unverified_service"})
                    else:
                        try:
                            await self._ctl_send(w, local_service_notice.deliver(self, req, service=service))
                        except ValueError as exc:
                            await self._ctl_send(w, {"ok": False, "error": str(exc)})
                elif cmd == "ceo_data":
                    from . import untrusted_dispatch
                    try:
                        if puid != os.getuid() or type(pid) is not int or pid <= 0:
                            raise ValueError("peer")
                        data = {k: v for k, v in req.items() if k != "cmd"}
                        worker = untrusted_dispatch.manager(self)
                        result = (worker.status(data["id"]) if set(data) == {"id"} else
                                  await asyncio.to_thread(worker.submit, data))
                        # submit only persists off-loop; scheduling always belongs to this loop.
                        worker.kick()
                        await self._ctl_send(w, {"ok": True, **result})
                    except ValueError as exc:
                        await self._ctl_send(w, {"ok": False, "error": str(exc)})
                elif cmd == "busy_native":
                    from . import busy_native
                    try:
                        await self._ctl_send(w, await busy_native.start(self, req, pid, puid))
                    except ValueError as exc:
                        await self._ctl_send(w, {"ok": False, "error": str(exc)})
                elif cmd == "busy_launch":
                    await self._ctl_send(w, {"ok": await asyncio.to_thread(self._busy_launch_request, req, pid, puid)})
                elif cmd == "opencode_auth":
                    agent = self.agent
                    result = await agent.authentication() if agent and hasattr(agent, "authentication") else {"known": False}
                    fail = getattr(agent, "last_provider_fail", None)     # P59: the last turn's failure class (doctor)
                    await self._ctl_send(w, {"ok": True, **result, **({"last_failure": fail} if fail else {})})
                elif cmd == "opencode_runtime":
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
                elif cmd == "files_prepare":
                    from . import local_files
                    await self._ctl_send(w, await asyncio.to_thread(local_files.prepare, self.st))
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
                elif cmd == "wechat_terminal":
                    if not ctl_auth.has_tty(pid) or self.wechat is None:
                        await self._ctl_send(w, {"ok": False, "error": "terminal_required"})
                    elif req.get("op") == "setup":
                        await self._ctl_send(w, await self.wechat.setup())
                    elif req.get("op") == "view":
                        await self._ctl_send(w, {"ok": True, **self.wechat.view(terminal=True)})
                    elif req.get("op") == "verify":
                        qr, code = self.wechat.qr, req.get("code")
                        valid = qr and qr["id"] == req.get("id") and isinstance(code, str) and re.fullmatch(r"[0-9]{1,16}", code)
                        if valid:
                            qr["code"] = code
                        await self._ctl_send(w, {"ok": bool(valid)})
                    else:
                        await self._ctl_send(w, {"ok": False, "error": "shape"})
                elif cmd == "telegram_pair":        # P283: a one-time Telegram pairing code → the phone's card only
                    res = await self.telegram_pair(req.get("key_env"), by="terminal")
                    await self._ctl_send(w, res)
                elif cmd == "service_restart_ready":
                    proof = await asyncio.to_thread(restart_guard.prove, "Bash", {"command": "agentj service restart"})
                    await self._ctl_send(w, {"ok": True, "ready": proof is not None})
                elif cmd == "upgrade_ready":        # P142 (A12): the recovery worker restarts only an idle serve
                    from . import auto_update
                    if req.get("purpose") == "upgrade":
                        ready, why = auto_update.restart_ready(self)
                        self._upgrade_wait_note(ready, why)
                    else:
                        ready = not auto_update.busy(self)
                    await self._ctl_send(w, {"ok": True, "ready": ready})
                elif cmd == "busy_list":
                    await asyncio.to_thread(self.busy.refresh_sources)
                    await self._ctl_send(w, {"ok": True, "busy": self.busy_msg()})
                elif cmd == "upgrade_impact":       # P203: `agentj update apply` asks what a restart now would interrupt
                    if req.get("caller") == "agent":
                        self.upgrade_turn = self.turn_t0 if self.agent and self.agent.status != "idle" else None
                    await self._ctl_send(w, {"ok": True, "items": self.upg_items("agent" if req.get("caller") == "agent" else None)})
                elif cmd == "upgrade_card":         # P203: … and, when it is not empty, puts the confirmation card on the phones
                    await self._ctl_send(w, {"ok": True, **self.upg_open("agent" if req.get("caller") == "agent" else None)})
                elif cmd == "auto_update_ready":
                    from . import auto_update
                    rec = auto_update.read(self.st)
                    await self._ctl_send(w, {"ok": True, "ready": auto_update.enabled(self.st) and
                        not self.stopped() and not auto_update.busy(self) and
                        time.time() - rec.get("phone_at", time.time()) >= auto_update.IDLE})
                elif cmd == "owner_hosts_commands":
                    from . import owner_hosts
                    await self._ctl_send(w, owner_hosts.handle_commands(self, req))
                elif cmd == "status":
                    from . import service_runtime
                    await self._ctl_send(w, {"environment": service_runtime.snapshot(), "ok": True, "relay_up": self.relay_up, "relay_state": self.relay_state(), "channel": self.channel,
                                             "relay_last": None if self.relay_up else self.relay_last,   # P317: the host leg's stuck point
                                             "identity_ok": self.identity_ok,
                                             "agent": self.agent.kind if self.agent else None, "agent_status": self.eff_status(),
                                             "agent_model": self.agent.cur_model() if self.agent else None,
                                             "agent_hello_deferred": isinstance(self.agent, SharedClaudeAgent) and self.agent.hello_deferred(),
                                             "agent_hello_at": getattr(self.agent, "_hello_at", 0),
                                             "agent_hello_failed": getattr(self.agent, "_hello_failed", False),
                                             "harness_readiness": getattr(self.agent, "readiness", lambda: {"ready": False, "code": "unverified"})(),
                                             "agent_said_at": getattr(self, "agent_said_at", 0),
                                             **dict(zip(("agent_turn_start_at", "agent_turn_end_at", "agent_turn_failed"),
                                                        getattr(self, "turn_at", (0.0, 0.0, False)))),
                                             **self._agent_phase(),
                                             "asks": len(self.asks), "estop": self.estop,
                                             "task_running": self.scheduler.current_id,
                                             "turn": self.turn_heal.view(),     # 0269: stuck-turn state for doctor
                                             "friends": self.peers.status_view() if self.peers is not None else None,
                                             "wechat": self.wechat.status() if self.wechat is not None else None,
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
                    invalid_reason = 'invalid_model'
                    if valid and model is not None and req.get('kind') == 'opencode':
                        from .opencode_provider import validate_model, ProviderError
                        from .provider_runtime import configured
                        known = set(configured()) | set(getattr(self.agent, 'config_providers', [])) | set(getattr(self.agent, 'connected_at_start', None) or [])
                        try:
                            validate_model(model, known)
                        except ProviderError as error:
                            valid = False
                            invalid_reason = str(error)
                    if busy or not valid:
                        await self._ctl_send(w,{'ok':False,'error':'busy' if busy else invalid_reason})
                    else:
                        previous=cfg.get('model')
                        if req.get('write') is True and applicable:
                            if self.agent is not None and not self.agent.owns_harness():
                                await self._ctl_send(w, {'ok': False, 'error': 'shared_harness: stop or change the shared harness yourself'})
                                continue
                            if not self.set_model(model):
                                await self._ctl_send(w, {'ok': False, 'error': 'agent_model_changed'})
                                continue
                            restart = self.restart_harness('provider')
                        else:
                            restart = None
                        await self._ctl_send(w,{'ok':True,'applicable':applicable,'model':previous,
                                               'restart':restart})
                elif cmd == "agent_reconnect":      # P147 `agentj doctor --reconnect`: re-attach the shared session now
                    await self._ctl_send(w, await self.agent_reconnect(req.get("session")))
                elif cmd == "assist_operator":
                    try:
                        res = await self.assist.operator.ctl(req)
                    except (OSError, ValueError, TypeError):
                        res = {"ok": False, "why": "operator_io"}
                    await self._ctl_send(w, res)
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
                    trusted = ctl_auth.refusal(pid, os.getpid(), self._agent_pids()) is None and ctl_auth.has_tty(pid)
                    await self._ctl_send(w, await self.reload_config(trusted=trusted, by="terminal" if trusted else "agent"))
                elif cmd == "agent_restart":        # `agentj agent restart` (P59): the owned harness, before its next turn
                    await self._ctl_send(w, self.restart_harness("cli"))
                elif cmd == "config_apply":
                    await self._ctl_send(w, await self.apply_preferences(req.get("raw")))
                elif cmd in ("voice_keys", "voice_list", "voice_test"):     # P208 `agentj voice`
                    await self._ctl_send(w, await self.voice_ctl(req))
                elif cmd == "mail_cfg":             # P170: the owner's terminal (claimed) — applies at once
                    await self._ctl_mail(w, req, claimed=True)
                elif cmd == "plaza_perm":           # P426 #181-1: a plaza package wants more than the Agent may do → signed card
                    from . import plaza_perms
                    await self._ctl_send(w, await plaza_perms.serve_card(     # #437: queued cards keep the CLI's line alive
                        self, req, lambda ev: asyncio.ensure_future(self._ctl_send(w, {"sent": True, **ev}))))
                elif cmd == "plaza_install":        # #428: `agentj plaza request` — serve checks, asks one signed card, installs
                    from . import plaza_host
                    await self._ctl_send(w, await plaza_host.handle(
                        self, req, lambda ev: asyncio.ensure_future(self._ctl_send(w, {"sent": True, **ev}))))
                elif cmd == "plaza_verify":         # #466: rerun an installed package's self-check (same sha256 + files)
                    from . import plaza_host
                    await self._ctl_send(w, await plaza_host.verify(self, req))
                elif cmd == "mail_status":
                    await self._ctl_send(w, {"ok": True, **(self.mail.view() if self.mail else {})})
                elif cmd == "mail_check":
                    await self._ctl_send(w, await self.mail.check() if self.mail else {"ok": False, "why": "not_running"})
                elif cmd == "mail_notes":           # P182: recipients are checked against the owner's own words (meeting.py)
                    await self._ctl_send(w, await self.mail.send_notes(req.get("to") or [], req.get("subject") or "", req.get("body"),
                                                                     bool(req.get("to_owner"))) if self.mail else {"ok": False, "why": "not_running"})
                elif cmd == "mail_key":
                    await self._ctl_send(w, await self.mail.secret_card() if self.mail else {"result": "refused", "why": "not_running"})
                elif cmd == "mail_trash":           # P565: a delivered mail judged phishing → the mailbox's trash (unclaimed: the Agent calls it)
                    await self._ctl_send(w, await self.mail.trash(req.get("id")) if self.mail else {"ok": False, "why": "not_running"})
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
                elif cmd == "terminal_cfg":         # P408: `agentj terminal …` (widening: claim + terminal + a passkey remote's card)
                    from . import term_cli
                    await self._ctl_send(w, await term_cli.ctl_cfg(self.term_desk, req))
                elif cmd == "terminal_go":          # P414 (§37.13): `agentj terminal open [--cmd]` — shows a screen, sends nothing
                    await self._ctl_send(w, await self.term_desk.ctl_go(req, pid))
                elif cmd == "terminal_read":        # P414 (§37.13): `agentj terminal output` — the agent-masked transcript
                    await self._ctl_send(w, await self.term_desk.ctl_read(req, pid))
                elif cmd == "passphrase_consent":   # #118: a paired remote's signed allow → a one-use ticket
                    await self._ctl_send(w, await self._pass_consent(req, pid))
                elif cmd == "passphrase_replace":   # #118: the new password typed at that terminal, with the ticket
                    await self._ctl_send(w, await self._pass_replace(req, pid))
                elif cmd == "uv_security":
                    from . import list_approval
                    await self._ctl_send(w, await list_approval.security(self,req))
                elif cmd == "revoke":
                    device = str(req.get("device", ""))
                    ans = await self.ask("agentj revoke", {"device":device}, grant_info=True)
                    proof = ans.get("grant_info") or {}
                    allowed = (ans.get("behavior") == "allow" and proof.get("uv") is True
                               and self.st.sign_key(proof.get("device")) == proof.get("sign_pub"))
                    if allowed:
                        removed, closed = await self.revoke(device)
                        await self._ctl_send(w, {"ok":removed,"closed":closed})
                    else:
                        await self._ctl_send(w, {"ok":False,"error":"uv_required","closed":0})
                elif cmd == "stop":                 # `agentj stop` (the terminal: no signature needed — it only stops)
                    await self.do_estop("terminal", "终端")
                    await self._ctl_send(w, {"ok": True, "estop": self.estop})
                elif cmd == "pk_peer_host":         # P555: `agentj faceid approve <对照码>` (pkpeer.host_approve)
                    await self._ctl_send(w, await self.pkpeer.host_approve(req.get("code")))
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
                            rid = self.scheduler.request(tid, "manual", caller_known=await asyncio.to_thread(self._completion_caller_known, pid, puid))
                            from . import diskq
                            if not await diskq.aflush():
                                raise tasks.TaskError("busy", "运行记录尚未完成落盘；重试前请核对状态 / Run journal has not settled; inspect state before retrying")
                        except tasks.TaskError as err:
                            await self._ctl_send(w, {"ok": False, "error": err.reason, "detail": err.detail})
                        except OSError:
                            await self._ctl_send(w, {"ok": False, "error": "journal_unavailable", "detail": "无法保存运行记录，请检查磁盘空间和文件权限 / Could not save run journal; check disk space and file permissions"})
                        else:
                            await self._ctl_send(w, {"ok": True, "queued": tid, "run_id": rid})
                elif cmd == "automation_resume":    # P426 #181-5: `agentj automation resume` (wfrun.py)
                    from . import wfrun
                    trusted = ctl_auth.refusal(pid, os.getpid(), self._agent_pids()) is None and ctl_auth.has_tty(pid)
                    await self._ctl_send(w, await wfrun.resume(self, req.get("run"), bool(req.get("yes")), trusted))
                elif cmd == "tasks_changed":
                    self.scheduler.wake.set()
                    await self._ctl_send(w, {"ok": True})
                elif cmd in ("tasks_migrate", "task_migrated_ran"):   # P570b: the owner's terminal only / a moved task's run ended
                    from . import sysmigrate
                    if cmd == "task_migrated_ran":
                        sysmigrate.ran_notice(self, req.get("id"))
                    await self._ctl_send(w, await sysmigrate.ctl(self, req, lambda: self._ctl_send(w, {"sent": True}))
                                         if cmd == "tasks_migrate" else {"ok": True})
                elif cmd == "seat_transfer_notice":  # P547 `agentj seat transfer`: serve re-reads transfer.json (no text taken)
                    await self.seat_xfer.from_terminal()
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
            await frames.aclose()
            approval_review.ORIGIN.reset(source_token)
            with contextlib.suppress(Exception):
                w.close()

    def report_view(self) -> tuple[set, dict]:

        online = {s.device for s in self.sessions.values() if s.state == "ready" and s.device}
        waiting = [s.pending_since for s in self.sessions.values() if s.state == "pending"]
        since = int(time.time() - (time.monotonic() - min(waiting))) if waiting else None
        return online, {"count": len(waiting), "since": since}

    async def revoke(self, did: str) -> tuple[bool, int]:

        removed = self.st.remove_device(did)
        n = await self._drop_device(did, "revoked", removed)
        if removed:
            await self.remotes.changed()
        return removed, n

    def _revoked_authority(self, did: str, moved: bool = False) -> None:
        """0304 A1-2/A1-7: a removed remote loses its pending authority at once, not at TTL. Every open card only it
        could answer is denied (list_approval then sends ask_done to every remote); same-owner rounds it started or
        consented to end with their cards (`gone`); same-owner peers get the shorter roster now, not in ≤30 s."""
        for a in list(self.asks.values()):
            if getattr(a, "only", None) == did and not a.fut.done():
                a.fut.set_result(("deny", None, None, None))
        # Q3: the special review cards bound to it (terminal login / sudo / secret input / key manager / mail
        # password — all elevate cards with `only`) end the same way a deny from the phone ends them.
        for ad in list(self.__dict__.get("_approval_special", {}).values()):
            only = getattr(ad, "only", None)
            mine = only == did or (isinstance(only, list) and bool(only) and set(only) <= {did})   # vshare: only=[did]
            if (mine and not getattr(ad, "terminal", True) and not ad.fut.done()
                    and not ad.card.get("busy")):     # a card already executing finishes on its own
                # security impl r1 L7: the same order as a deny from the phone (approval_special.on_phone / elevate
                # deny): the review deny event first, then the controller's finish.
                with contextlib.suppress(Exception):
                    if callable(getattr(ad, "denied", None)):
                        ad.denied()
                with contextlib.suppress(Exception):
                    ad.finish("declined" if ad.tool == "SecretPickup" else "denied")
                    self.st.log("ask_revoked", id=ad.rid, tool=ad.tool)
        peers = getattr(self, "peers", None)
        if peers is None:
            return
        if not moved:
            # 0.30.7 (preview-frset): a seat MOVE (the same remote proved the same passkey / renewal ticket from another
            # browser storage) is not a removal — its rounds and the legacy relations it consented to stay. Cards only
            # the old id could answer still end above (the old id can no longer answer them).
            with contextlib.suppress(Exception):
                peers.same_owner.device_gone(did)
        with contextlib.suppress(Exception):
            peers.same_owner.roster_moved()      # R-F11: rounds whose snapshot held it restart (new code)
        with contextlib.suppress(Exception):
            peers.xfer.roster_tick()

    async def _drop_device(self, did: str, why: str, removed: bool = True, moved: bool = False) -> int:
        if removed:
            # Subscription lifecycle only; the existing pairing/authentication decision is unchanged.
            from .subscriptions import router
            try:
                router(self).revoke_device(did)
            except (ValueError, OSError):
                self.st.log('event_revoke', reason='persistence_failed')
            self._revoked_authority(did, moved=moved)

        victims = [c for c, s in list(self.sessions.items()) if s.device == did]
        for c in victims:
            self._detach(c)
        if getattr(self, "elevate", None) is not None:
            self.elevate.grants.drop_device(did, "unpaired")   # #116: its 10-minute admin approval ends with it
        if getattr(self, "term_desk", None) is not None:
            self.term_desk.device_gone(did)                    # P408: its terminal entry and cached password go too
        with contextlib.suppress(Exception):
            await self._preauth_revoke("unpaired", device=did)   # P539: its 「Computer Use 免问」 grant ends with it
        if removed and not self.st.devices():
            from .core import enabled as core_enabled
            if core_enabled():
                from .core.order_host import pause_all
                await pause_all(self)
        if removed:
            self.st.log("revoked", device=did, reason=why)
            self.reporter.trigger("revoke")
            self.emit("revoked", device=did)
            subs = webpush.load_subs(self.st)
            if subs.pop(did, None):
                webpush.save_subs(self.st, subs)
            with contextlib.suppress(Exception):
                self.uploads.device_gone(did)       # its partial uploads and never-announced files go too
            if (self.agent_cfg or {}).get("follow_harness") and not approval_level.follow_harness(self.preferences, self.st):
                with contextlib.suppress(Exception):   # P551: the signed `harness` switch left with it — standard hook again
                    self._reload_runtime()
            self.approval_check("unpaired")             # P552 (#325 ①): a switch it signed is no longer in force — tell the owner
        for c in victims:
            await self._notify_close(c)
            self.st.log("close", cid=c, device=did, reason=why)
        return len(victims)

    async def _ctl_pair(self, r: asyncio.StreamReader, w: asyncio.StreamWriter, pid=None) -> None:
        epoch = getattr(self, '_pairing_epoch', 0)
        if getattr(self, '_unbinding_all', False):
            return await self._ctl_send(w, {'ev': 'error', 'reason': 'unbound_all'})
        if self.seat_refusal():                 # P185: no usable seat → no new remote (checked before relay_down: it is the cause)
            return await self._ctl_send(w, {"ev": "error", "reason": "seat_required"})
        if not self.relay_up:
            return await self._ctl_send(w, {"ev": "error", "reason": "relay_down"})
        old = self.pairing
        if old:
            self._pair_finish(old, {"ev": "replaced"})
            if old.cid is not None:
                await self.close_cid(old.cid, "pair_replaced")
        if getattr(self, '_unbinding_all', False) or epoch != getattr(self, '_pairing_epoch', 0):
            return await self._ctl_send(w, {'ev': 'error', 'reason': 'unbound_all'})
        p = Pairing(secrets.token_bytes(16), secrets.token_bytes(32), time.time() + PAIR_TTL, time.monotonic() + PAIR_TTL, w)
        self.pairing = p
        p.timer = asyncio.create_task(self._expire_pairing(p))
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

        from .poll_backoff import Backoff
        backoff = Backoff(base=SYNC_EVERY)
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
                    backoff.reset()
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
                    wait = max(SYNC_EVERY, backoff.failed(res.status, getattr(res, "retry_after", None)))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), wait)

    async def seat_unbound(self, expected) -> None:

        if not expected or expected.get("via") != "seat":
            return
        with cloud.cloud_lock(self.st):
            current = cloud.read_cloud(self.st)
            if not current or any(current.get(k) != expected.get(k) for k in ("api", "host_id", "linked_at", "via")):
                return
            self.st.cloud_path.unlink()
            devices = list(self.st.devices())
        from .core import enabled as core_enabled
        if core_enabled():
            from .core.order_host import pause_all
            await pause_all(self)
        for did in devices:
            await self.revoke(did)
        self.st.log("seat_unbound", remotes=len(devices))
        self.emit("agent_notice", text=("This seat was unbound; existing remotes were disconnected. Chat history stays on the Agent J host." if self.lang == "en" else "这个席位已解绑，已有遥控器已断开；聊天记录仍在这台Agent J 主机上。"))

    def adopt_name(self, name: tuple) -> None:

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

        if self.sessions.get(s.cid) is not s:
            return
        from . import codex_reset_credits
        if getattr(getattr(self, "agent", None), "kind", None) == "codex":
            self._bg(codex_reset_credits.refresh_meter(self), "codex-quota-metadata")
        from . import bailian
        with contextlib.suppress(OSError):
            bailian.notify_retired(self)
        from . import auto_update
        from . import codex_full_access     # 0.30.7: the receipt of a 「改回全放行并重启」 tap made before the restart
        with contextlib.suppress(OSError):
            codex_full_access.deliver(self)
        note = auto_update.take_notice(self.st, self.lang, self.preferences, notices.release_notes.current(self.st, self.agent))
        if note:
            self.agent_notice(note,repair="reconnect")
        from . import claude_inbound
        notice = claude_inbound.default_notice(self.st, self.lang)
        if notice:
            self.agent_notice(notice)  # durable history, also delivered after a reconnect
            claude_inbound.notice_delivered(self.st)
        since = since if isinstance(since, int) and not isinstance(since, bool) and since >= 0 else 0
        if since > self.seq:            # a device that remembers a seq from before this serve started: replay it all
            since = 0
        await self.send_app(s, self._status_msg())
        await self.send_app(s, {"t": "call_capability", "enabled": self.voice_stream.available()})
        if s.p33: await self.send_app(s, self.preferences_msg(s))
        if s.p33: await self.remotes.send_state(s)    # P525: repair changes missed while disconnected
        await self.send_app(s, self._estop_msg())
        with contextlib.suppress(Exception):           # 0269-w2-assist (§41): the grant's state + recent tasks
            await self.assist.on_ready(s)
        if s.p33 and self.auth_rescue.needs_dot():       # P258: the red dot reaches a phone that was away
            await self.send_app(s, {"t": "auth_rescue", "view": await asyncio.to_thread(self.auth_rescue.view)})
        if s.p33 and self.quota_resume.items:            # P314 (§32): 「额度用完，预计 HH:MM 自动继续」
            await self.send_app(s, self.quota_resume.msg())
        # Host persistence is authoritative on reconnect; announce it before the key prompts browser recovery.
        await self.send_app(s, {"t": "push_state", "on": s.device in await asyncio.to_thread(webpush.load_subs, self.st)})
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
            replayed = 0
            for e in list(self.backlog):
                if e["seq"] > since:
                    sent = await self.send_app(s, self._render(e, s))
                    replayed += bool(sent)
                    missed += since > 0
            if since > 0:
                # Host-only §8 replay counters; ciphertext relay cannot inspect since.
                # Counts only: no source, device identifier, cursor or message data.
                self.st.log("since_replay", attempts=1, frames=replayed)
                self.emit("since_replay", attempts=1, frames=replayed)
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
            if not a.fut.done() and (not a.only or a.only == s.device) and (a.tool != pair_card.TOOL or s.pair1):
                await self.send_app(s, self._ask_msg(a, s))
        for g in list(self.grants.values()):
            if self._grant_live(g):
                await self.send_app(s, self._grant_msg(g))
        if s.p33 and self.peers is not None:      # P71: open friend cards (they survive a serve restart)
            for m in self.peers.ask_msgs():
                await self.send_app(s, m)
        if s.p33 and self._upg_live():            # P203: the open upgrade confirmation card comes back on reconnect
            await self.send_app(s, self._upg_msg())
        if getattr(self, "external_channels", None) is not None:
            for m in self.external_channels.open_messages(s):
                await self.send_app(s, m)
        if getattr(self, "wechat", None) is not None:
            await self.send_app(s, self.wechat.view())
            for m in self.wechat.open_messages(s):
                await self.send_app(s, m)
        await self.elevate.on_ready(s)
        await self.capabilities.on_ready(s)
        approval_review.retry_events(self)
        await self.pkpeer.on_ready(s)            # P547: open approval cards / an approved offer within the TTL
        await self.seat_xfer.on_ready(s)         # P547: host_moved for a remote that was away when the seat moved
        with contextlib.suppress(Exception):      # P115: a checklist resumed in the last 6 h comes back on reconnect
            from . import first_run
            rec = await asyncio.to_thread(first_run.read, self.st)
            if rec is not None:
                fresh = await asyncio.to_thread(first_run.check, self.st, ['desktop', 'phone', 'passkey', 'admin', 'exit'], self.setup_serve())
                await self.send_app(s, {"t": "setup_card", "show": first_run.active(rec), **fresh})

    # ------------------------------------------------------------ agent bridge (PROTOCOL §8)
    def harness_hello(self, req: dict) -> dict:

        from . import switch_harness
        if not self.agent or self.stopped():
            return {"ok": False, "error": "no_agent"}
        from . import handoff
        cfg = self._switch_cfg()
        where = handoff.switch_where(cfg) if cfg else None
        inline = why = None
        if cfg and req.get("outcome") == "ok" and req.get("handoff") == "ok":   # P539 (#301): the file rides along
            inline, why = handoff.switch_inline(cfg, self.lang)
        text = switch_harness.hello_prompt(self.agent.kind, req, self.lang, where, inline, why)
        if text is None:
            return {"ok": False, "error": "bad_request"}
        if req.get("outcome") == "ok" and (req.get("handoff") == "none" or why is not None):
            text += handoff.switch_fallback(self.hist.turns)
        at = time.time()
        self.agent.submit(switch_harness.HelloSend(text))
        self.st.log("harness_hello", agent=self.agent.kind, outcome=req.get("outcome"), handoff=req.get("handoff"),
                    inline="ok" if inline else (why or "no"))
        return {"ok": True, "at": at}

    def _switch_cfg(self) -> dict | None:
        cfg = getattr(self.agent, "cfg", None)
        if not isinstance(cfg, dict) or not isinstance(cfg.get("dir"), str):
            cfg = self.st.agent_config()
        return cfg if isinstance(cfg, dict) and isinstance(cfg.get("dir"), str) else None

    def harness_handoff(self, req: dict) -> dict:

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

        m = {"t": "status", "s": self.eff_status(), "agent": self.agent.kind if self.agent else None,
             "name": self.st.agent_name(), "goal_active": bool(getattr(self, "goals", None) and self.goals.current())}
        if m["s"] == "waiting" and self._open_question():
            m["kind"] = "question"
        if m["s"] == "working" and self.phase_now:     # P204 (§29): 「执行工具中 · Bash」 vs 「生成中」
            m["ph"] = self.phase_now[0]
            if self.phase_now[1]:
                m["tool"] = self.phase_now[1]
        if self.telegram is not None and self.telegram.error:   # P129: the phone's status area says it; gone on recovery
            m["tg"] = "down"
        if self.telegram is not None and hasattr(self.telegram, 'status_view'):
            view = self.telegram.status_view()
            m['channels'] = {'tg_bound': view['enrolled'], 'tg_forward': view['forward']}
        from . import main_thread
        view = main_thread.status(self)
        if view is not None:
            m['main_thread'] = view
        return m

    def telegram_changed(self) -> None:

        msg = self._status_msg()
        self._post(self._send_ready, lambda s: msg)

    def preferences_msg(self, s=None):
        value = dict(self.preferences)
        value = preferences.merge(value, {})
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
                return {"t":"preferences","value":value,"problem":e.result(),"host":self.host_info(s)}
        return {"t": "preferences", "value": value, "problem": self.config_problem, "host": self.host_info(s)}

    def _terminal_commands(self):
        cached = getattr(self, "_cmdrun_available", (0, cmdrun.available_commands()))
        if time.monotonic() - cached[0] > 60 and not getattr(self, "_cmdrun_detecting", False):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return cached[1]
            self._cmdrun_detecting = True
            self._bg(self._detect_terminal_commands(), "cmd_detect")
        return cached[1]

    async def _detect_terminal_commands(self):
        try:
            harnesses = await asyncio.to_thread(cmdrun.command_harnesses, detect=True)
            names = cmdrun.available_commands()
            self._cmdrun_harnesses = harnesses
            self._cmdrun_available = (time.monotonic(), names)
            # facts only; never race the pref_res/preferences pair. #461f: _send_ready takes a function of the session — the
            # dict itself was 'not callable' (bg_task_failed why=cmd_detect TypeError) and the phone never got the list.
            await self._send_ready(lambda s: {"t": "cmds_state", "names": names, "harnesses": harnesses})
        finally:
            self._cmdrun_detecting = False

    def host_info(self, s=None) -> dict:

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
        from . import jev
        info["jev"] = jev.resolve(st=self.st, prefs=self.preferences).public()
        info["signed_prefs"] = list(approval_level.KEYS)   # P336: changed only by the signed `approval_set` control
        info["owner_hosts"] = 1  # feedback400: signed table + pinned SSH/scp receipts
        info["budget"] = 1                                   # 0269-w3-policy (§42): budget_get / budget_set (budget.py)
        info["forward_set"] = True                          # P554: a phone changes ui.forward_destination only by the signed `forward_set`
        info["run_harnesses"] = getattr(self, "_cmdrun_harnesses", None) or cmdrun.command_harnesses()
        info["run_cmds"] = self._terminal_commands()           # P361 (§34): the names Settings may 「执行」
        with contextlib.suppress(Exception):               # P408 (§38): this device's row (me / sudo mode / web_ok)
            info["terminal"] = self.term_desk.info(getattr(s, "device", None) or None)
        with contextlib.suppress(Exception):               # P414 (§37.9): the computer-password vault, as this device sees it
            info["vault"] = self.term_desk.vault_info(getattr(s, "device", None) or None)
        info["approval"] = {"level": approval_level.level(self.preferences, self.st),       # P336: what is in force
                            "goal_nocard": approval_level.goal_preauth(self.st, self.preferences) is not None}
        with contextlib.suppress(Exception):   # P556: what 「全放行」 does to the AI coding tool this host starts (Settings says it)
            ac = self.agent_cfg or {}
            info["approval"]["max"] = {"kind": ac.get("kind"), "shared": ac.get("session_mode") == "shared",
                                       "how": approval_level.max_how(ac.get("kind"))}
        with contextlib.suppress(Exception):   # P552 (#325 ①): a switch the configuration turns on that is NOT in force, and why
            info["approval"]["unsigned"] = [{"key": i["key"], "value": i["value"], "why": i["why"],
                                             "by": self._signer_name(i.get("device"))}
                                            for i in approval_level.unsigned(self.preferences, self.st)]
        from . import service as _svc
        info["platform"] = _svc.platform()   # P274: the phone shows the iMessage card only for "macos"
        im = getattr(self, "imessage", None)
        if im is not None and info["platform"] == "macos":
            with contextlib.suppress(Exception):
                info["imessage"] = im.brief()
        return info

    async def apply_preferences(self, raw=None, ack=None, language_at=None, build=None, asr_phone=False):

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
                if asr_phone:
                    from .voice import phone_asr_change_allowed
                    if not phone_asr_change_allowed(old,candidate):
                        raise preferences.ConfigError('voice.asr','keyed ASR settings require the trusted local configuration command')
                from . import claude_auth
                await asyncio.to_thread(claude_auth.require_transition, self.st, old, candidate, running=True)
                from . import voice
                from . import service
                try:
                    service.load_launch_binary_env(candidate)
                except service.ServiceError as e:
                    raise preferences.ConfigError("/", e.detail or e.reason) from None
                voice.validate_runtime(candidate, environment="service")
                if isinstance(raw,str):
                    preferences.commit_files(self.st,raw,candidate,signed_key=self.__dict__.get("_approval_set_key"))
                else:
                    preferences.save_good(self.st,candidate)
                self.preferences = candidate
                self.ask_ttl = min(ASK_TTL, preferences.get(candidate, "approval.timeout"))
                self.config_problem = None
                self._approvals_hot(old, candidate)
                for k in approval_level.KEYS:          # P336 N4: a value written any other way voids that key's signature
                    if preferences.get(old, k) != preferences.get(candidate, k) and k != self.__dict__.get("_approval_set_key"):
                        approval_level.drop_signer(self.st, k)
                self.term_desk.config_applied()        # P408 (N2): a `local` terminal cache dies when the fence / mode no longer allows it
            except (preferences.ConfigError, OSError) as e:
                self.preferences = old
                self.config_problem = e.result() if isinstance(e, preferences.ConfigError) else {"ok": False, "key": "/", "error": "runtime persistence failed ("+type(e).__name__+", errno="+str(e.errno)+")"}
                if ack:
                    await ack(self.config_problem)
                await self._send_ready(lambda x: self.preferences_msg(x))
                return self.config_problem
            if preferences.get(candidate, "voice.asr.mode") == "custom":
                await asyncio.to_thread(self.asr.shutdown)
            lang_changed = preferences.get(old, "appearance.language") != preferences.get(candidate, "appearance.language")
            preferences.note_language(self.st, candidate, language_at)
            if lang_changed:
                self.language_changed(preferences.get(candidate, "appearance.language"), from_account=language_at is not None)
            restart = any(preferences.get(old, key) != preferences.get(candidate, key) for key in RESTART_SERVE_KEYS)
            reload_keys = [key for key in RELOAD_KEYS if preferences.get(old, key) != preferences.get(candidate, key)]
            self.reload_pending.update(reload_keys)
            proxied = any(preferences.get(old, "proxy." + k) != preferences.get(candidate, "proxy." + k)
                          for k in ("https", "http", "no_proxy", "https_env", "http_env", "no_proxy_env"))
            harness_note = self.restart_harness("proxy") if proxied else None
            if self.agent_cfg is not None:   # F14: the next Agent process starts with the new isolation / docker choice
                raw = self.st.config().get("agent") or {}
                self.agent_cfg["fence"] = raw.get("fence") is not False and preferences.get(candidate, "agent.isolation", True) is not False
                self.agent_cfg["docker"] = raw.get("docker") is True or preferences.get(candidate, "agent.allow_docker", False) is True
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
            await self._send_ready(lambda x: self.preferences_msg(x))
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

        from . import claude_inbound, claude_statusline
        claude_statusline.ensure_default(self.st)   # P127: status-line tap default (never raises; doctor reports)
        try:
            claude_inbound.ensure_shared_default(self.st)
            return False
        except (OSError, claude_inbound.SettingsError):
            return True

    async def agent_reconnect(self, session=None) -> dict:

        a = self.agent
        if a is None:
            return {"ok": False, "why": "no_agent", "agent_status": self.eff_status()}
        fn = getattr(a, "reconnect", None)
        if fn is None:
            return {"ok": a.status != "down", "why": "unsupported", "agent": a.kind, "agent_status": self.eff_status()}
        sid = session if isinstance(session, str) and re.fullmatch(r"[0-9A-Fa-f-]{8,64}", session) else None
        try:
            res = await asyncio.wait_for(fn(sid), 40)
        except asyncio.TimeoutError:
            res = {"ok": False, "why": "timeout"}
        res.update(agent=a.kind, agent_status=self.eff_status())
        self.push_status()                  # P306: the phones see the answer at once, not at the next turn
        self.st.log("agent_reconnect", result="ok" if res.get("ok") else str(res.get("why"))[:16])
        return res

    def restart_harness(self, why: str) -> dict:

        a = self.agent
        if a is None:
            return {"ok": True, "when": "next_start"}
        if not a.owns_harness():
            return {"ok": False, "when": "own", "error": "shared"}
        return {"ok": True, "when": a.request_restart(why)}

    async def reload_config(self, trusted: bool = False, by: str = "cli") -> dict:

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

        refreshed = self._reload_agent_settings()
        note = None
        if refreshed or env_changed or self.reload_pending:
            note = self.restart_harness("reload") if self.agent is not None else {"ok": True, "when": "next_start"}
        self.reload_pending.clear()
        return note

    def _approvals_hot(self, old: dict, new: dict) -> None:

        if preferences.get(old, approval_level.KEY) == preferences.get(new, approval_level.KEY) or \
                preferences.get(old, "agent.approvals") != preferences.get(new, "agent.approvals"):
            return
        if set(approval_level.SIGNED) & {preferences.get(old, approval_level.KEY), preferences.get(new, approval_level.KEY)}:
            self.reload_pending.add(approval_level.KEY)   # P551: the hook / shared guard read the harness level at launch
            return
        was, now = approval_level.shared_mode(old), approval_level.shared_mode(new)
        if "native" in (was, now) and was != now:
            self.reload_pending.add(approval_level.KEY)
            return
        for c in (self.agent_cfg, getattr(self.agent, "cfg", None) if self.agent is not None else None):
            if c is not None and "approvals" in c:
                c["approvals"] = now

    def _reload_agent_settings(self) -> list:

        if self.agent_cfg is None:
            return []
        prefs, raw = self.preferences, (self.st.config().get("agent") or {})
        fresh = {"instructions": preferences.get(prefs, "agent.instructions", ""),
                 "high_risk_warnings": preferences.get(prefs, "agent.high_risk_warnings", False),
                 "approvals": approval_level.shared_mode(prefs, self.st),   # P336: safe → "safe"; P551 harness → native
                 "follow_harness": approval_level.follow_harness(prefs, self.st),   # P551 (#320): read at the harness's start
                 "fence": raw.get("fence") is not False and preferences.get(prefs, "agent.isolation", True) is not False,
                 "docker": raw.get("docker") is True or preferences.get(prefs, "agent.allow_docker", False) is True}
        fields = [k for k in ("instructions", "high_risk_warnings", "approvals", "follow_harness", "fence", "docker")
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

        self.lang = "en" if lang == "en" else "zh"
        if self.agent_cfg is not None:
            self.agent_cfg["language"] = lang
        if self.agent is not None and getattr(self.agent, "cfg", None) is not None and not self.agent.cfg.get("_workflow_ceo"):
            self.agent.cfg["language"] = lang
            self.agent.identity_changed()
        self.st.log("language_set", status=lang, trigger="account" if from_account else "local")
        if not from_account:
            self.reporter.trigger("language")

    async def set_pref(self, key: str, value, ack=None, language_at=None, asr_phone=False) -> dict:

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
            return await self.apply_preferences(newraw, ack=ack, language_at=language_at, **({"asr_phone": True} if asr_phone else {}))
        finally:
            os.close(fd)

    def account_language(self, account: dict) -> None:

        if not preferences.account_is_newer(self.st, account, self.preferences):
            return
        if preferences.get(self.preferences, "appearance.language") == account["language"]:
            preferences.note_language(self.st, self.preferences, account["language_at"])
            return
        self.st.log("language_synced", status=account["language"])
        self._bg(self.set_pref("appearance.language", account["language"], language_at=account["language_at"]), "language_sync")

    def _policy_target(self, kind, obj):
        if kind not in ("mail_set", "mail_test"):
            raise ValueError("unknown mail target")
        from . import mail
        before = mail.read_config(self.st)
        if kind == 'mail_set':
            raw = obj.get('cfg')
            if not isinstance(raw, dict) or len(json.dumps(raw)) > 8000:
                raise ValueError('invalid mail settings')
            action_policy.mail_config(before, raw)  # reject invalid previews before offering UV
        return {"kind":kind, **({"cfg":obj.get("cfg")} if kind == "mail_set" else {}),
                "before":before}

    def _policy_write(self, s, kind, obj):
        try:
            target = self._policy_target(kind, obj)
        except (ValueError, TypeError):
            return "shape"
        auth=obj.get("authorization") or {}
        why=controls.check(self.st,self.nonces,self.channel,s.device,auth,"host_write",target)
        controls.log(self.st,action="host_write",device=s.device,result="refused:"+why if why else "applied",obj=target)
        return why

    async def on_pref_set(self, s: Session, obj: dict) -> None:

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
        if self.sessions.get(s.cid) is not s or s.state != "ready" or not self.st.is_allowed(s.pub):
            self.st.log("pref_set_refused", device=s.device)
            return await res(False, "not_paired")
        if key not in PREF_SET_KEYS:
            self.st.log("pref_set_refused", device=s.device)
            return await res(False, "not_allowed")
        if isinstance(key, str) and key.startswith("imessage."):
            from . import service as _svc
            if _svc.platform() != "macos":           # P274: only a Mac has chat.db; the card is not shown elsewhere
                return await res(False, "not_macos")
        meta = preferences.SCHEMA[key]
        typ_ok = {"boolean": type(value) is bool, "string": isinstance(value, str),
                  "number": type(value) in (int, float) and math.isfinite(value),
                  "integer": type(value) is int,
                  "object": key == "voice.asr.corrections" and isinstance(value, dict),
                  "array": (key == "ui.composer_buttons" and preferences.composer_order_ok(value)) or (key == "voice.asr.hotwords" and isinstance(value, list)) or (key == "ui.remote_servers" and preferences.remote_servers_ok(value))}.get(meta["type"], False)
        if not typ_ok or ("enum" in meta and value not in meta["enum"]) or (meta["type"] in ("number", "integer") and not (
                meta.get("minimum", -math.inf) <= value <= meta.get("maximum", math.inf) and (not meta.get("multipleOf") or abs(
                    value / meta["multipleOf"] - round(value / meta["multipleOf"])) < 1e-6))):
            return await res(False, "bad_value")
        if meta["type"] == "string" and (len(value) > meta.get("maxLength", 1000) or any(ord(c) < 32 for c in value) or (
                meta.get("pattern") and not re.fullmatch(meta["pattern"], value))):
            return await res(False, "bad_value")
        if key == 'voice.asr.key_env' and value:
            return await res(False, 'not_allowed')  # Secret references never travel through unsigned phone prefs.
        if preferences.get(self.preferences, key) == value:
            return await res(True)

        async def ack(result: dict) -> None:
            await res(bool(result.get("ok")), None if result.get("ok") else
                      ("busy" if result.get("error") == "busy" else "rejected"), reload=bool(result.get("reload")))
            if not result.get("ok") and key == "agent.session_mode" and str(result.get("error", "")).startswith("Agent J 主机上的服务没有 Claude 登录"):
                from . import claude_auth
                self.agent_notice(claude_auth.failure_notice(self.lang))
        self.st.log("pref_set", device=s.device, status=key)
        await self.set_pref(key, value, ack=ack, **({"asr_phone": True} if key.startswith("voice.asr.") else {}))
        if key.startswith("imessage.") and getattr(self, "imessage", None) is not None:
            self.imessage.kick()                     # P274: a switch / interval change takes effect at once

    async def on_dev_rename(self, s: Session, obj: dict) -> None:

        r, name = obj.get("r"), obj.get("name")
        if not wire.is_rid(r):
            return

        async def res(ok: bool, **kw) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "dev_rename_res", "r": r, "ok": ok, **kw})
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

        msg = self.busy.msg(self.st.agent_name())
        if self.wf.last:
            from .workflows import task_assignment
            for task in msg["tasks"]:
                task.update(task_assignment(task, self.wf.last))
        for task in msg["tasks"]:
            task.pop("cwd", None)   # local matching only; no absolute path on the list
        return msg

    def busy_changed(self) -> None:

        msg = self.busy_msg()
        key = json.dumps([msg["tasks"], msg["by"], msg.get("load"), msg.get("cap"), msg.get("live_process_count"), msg.get("task_count"), msg.get("unknown_count")], ensure_ascii=False, sort_keys=True)
        if key == self.busy_sent:
            return
        self.busy_sent = key
        self._post(self._send_p33, lambda s: msg)

    async def event_loop(self) -> None:
        from .subscriptions import router
        failed = False
        while not self.stopping.is_set():
            try:
                await router(self).tick()
                failed = False
            except Exception:
                if not failed:
                    self.st.log('github_poll', reason='state_unavailable', pages=0)
                    failed = True
            await asyncio.sleep(2)

    async def busy_loop(self) -> None:

        from . import busy as busy_mod
        n = 0
        while True:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.busy.refresh_outside, n % 5 == 0)
                runner = getattr(self, "scheduler", None)
                proc = getattr(runner, "proc", None) or getattr(getattr(runner, "runner", None), "proc", None)
                for key, value in list(self.busy.mem.items()):
                    if key.startswith("wf:") and proc is not None:
                        value["pid"] = proc.pid
                        adapter = getattr(runner, "runner", None)
                        if adapter is not None:
                            value["native"] = True
                            selected = getattr(adapter, "busy_selected", {})
                            for field in ("model", "effort"):
                                if selected.get(field):
                                    value[field] = selected[field]
                agent = self.agent
                if n % 5 == 0:
                    await asyncio.to_thread(self.busy.reconcile_claude, None)
                if n % 5 == 0 and hasattr(agent, "refresh_busy"):
                    await agent.refresh_busy()
                await asyncio.to_thread(self.busy.refresh_sources)
                await self.background_done.prepare()
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

        from . import resources
        sampler = await asyncio.to_thread(resources.Sampler,
                                          workdir=lambda: self._agent_cwd() or (self.agent_cfg or {}).get("dir"))
        await asyncio.to_thread(sampler.sample)             # the baseline for the rates
        await asyncio.sleep(resources.FIRST)
        while True:
            watchers = self._res_watchers()
            if not watchers:
                return
            msg = resources.message(await asyncio.to_thread(sampler.sample), self.busy.load)
            with contextlib.suppress(Exception):           # P204 (§29): today's spend, on the same card
                msg["use"] = await asyncio.to_thread(self.usage_today)
            for s in watchers:
                await self.send_app(s, msg)
            await asyncio.sleep(resources.EVERY)

    async def on_agent_name(self, s: Session, obj: dict) -> None:

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

    async def on_forward_set(self, s: Session, obj: dict) -> None:

        r = obj.get("r")
        if not wire.is_rid(r):
            return
        dest, migrate = obj.get("dest"), obj.get("migrate", False)
        if dest not in FORWARD_DESTS or type(migrate) is not bool:
            return await self._ctl_res(s, r, "forward_set", False, why="shape")
        target = {"dest": dest, "migrate": migrate}
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "forward_set", target)
        if why:
            self.st.log("control_refused", device=s.device, action="forward_set", reason=why)
            controls.log(self.st, action="forward_set", device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, "forward_set", False, why=why)
        lock = self.__dict__.setdefault("_forward_lock", asyncio.Lock())   # first-wins: read and write as one step
        async with lock:
            have = preferences.get(self.preferences, FORWARD_KEY, "")
            if not (migrate and have):
                res = {"ok": True} if have == dest else await self.set_pref(FORWARD_KEY, dest)
        if migrate and have:
            controls.log(self.st, action="forward_set", device=s.device, result="kept", obj=target,
                         sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
            return await self._ctl_res(s, r, "forward_set", True, dest=have, migrated=False)
        ok = bool(res.get("ok"))
        controls.log(self.st, action="forward_set", device=s.device, result="ok" if ok else "rejected", obj=target,
                     sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
        if not ok:
            return await self._ctl_res(s, r, "forward_set", False, why="rejected")
        self.st.log("forward_set", device=s.device, status=dest + (" migrate" if migrate else ""))
        await self._send_ready(lambda x: self.preferences_msg(x))
        await self._ctl_res(s, r, "forward_set", True, dest=dest, migrated=migrate)

    async def on_budget(self, s: Session, t: str, obj: dict) -> None:
        """0269-w3-policy E3 (PROTOCOL §42): `budget_get` → `budget_state`; `budget_set` signed (§8 control-v1) — raising
        (a first / higher limit, another currency) also needs this remote's Face ID, lowering / clearing does not."""
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        if t == "budget_get":
            return await self.send_app(s, await asyncio.to_thread(budget_mod.phone_msg, self.st, r))
        tgt = budget_mod.fields(obj)            # only the fields the phone sent (web wire.js), e.g. {"day": 10}
        if not tgt or any(isinstance(v, bool) or not budget_mod.amount_ok(v) for v in tgt.values()):
            return await self._ctl_res(s, r, "budget_set", False, why="shape")
        if self.stopped():
            return await self._ctl_res(s, r, "budget_set", False, why="estop")
        old = await asyncio.to_thread(budget_mod.state, self.st)
        widen = budget_mod.raises(old, budget_mod.apply(old, tgt))   # from none counts as from 0
        pk = self.st.passkey_of_device(s.device) if widen else None
        if widen and not pk:
            controls.log(self.st, action="budget_set", device=s.device, result="refused:no_passkey")
            return await self._ctl_res(s, r, "budget_set", False, why="no_passkey")
        ch = None
        if pk:
            n = obj.get("n")
            if not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
                return await self._ctl_res(s, r, "budget_set", False, why="shape")
            ch = passkey.elevate_challenge(self.channel, s.device, n, "budget_set", n, controls.object_digest("budget_set", tgt))
            if obj.get("fa") is None:
                return await self._ctl_res(s, r, "budget_set", False, why="passkey_required", fa=pk["id"],
                                           fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(ch))
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, "budget_set", tgt)
        if not why and pk:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        if why:
            controls.log(self.st, action="budget_set", device=s.device, result="refused:" + why,
                         extra={"passkey": "uv"} if pk else None)
            return await self._ctl_res(s, r, "budget_set", False, why=why)
        await asyncio.to_thread(budget_mod.store_signed, self.st, tgt, s.device,
                                {"n": obj["n"], "ts": obj["ts"], "sig": obj["sig"]})
        controls.log(self.st, action="budget_set", device=s.device, result="ok", obj=tgt, sig=obj.get("sig"), n=obj.get("n"),
                     ts=obj.get("ts"), extra={"passkey": "uv"} if pk else None)
        self.st.log("budget_set", device=s.device, status="raise" if widen else "lower")
        msg = await asyncio.to_thread(budget_mod.phone_msg, self.st)
        self._post(self._send_p33, lambda x: msg)
        await self._ctl_res(s, r, "budget_set", True, **{k: v for k, v in msg.items() if k != "t"})

    async def on_approval_set(self, s: Session, obj: dict) -> None:

        r = obj.get("r")
        if not wire.is_rid(r):
            return
        key, value = obj.get("key"), obj.get("value")
        cheap = False
        if key == "jev.mode" and isinstance(value, str) and value.startswith("cheap"):
            from .privacy_cheap import review
            try:
                cheap = value == review(preferences.get(self.preferences, "jev.cheap", {}))
            except (ValueError, TypeError):
                pass
        if not ((key == approval_level.KEY and value in approval_level.LEVELS and value != "full")
                or (key == approval_level.GOAL_KEY and type(value) is bool)
                or (key == "jev.mode" and (value in ("typesafe", "openrouter", "off") or cheap))):
            return await self._ctl_res(s, r, "approval_set", False, why="shape")
        target = {"key": key, "value": value}
        widen = (key == approval_level.KEY and value in approval_level.SIGNED) or (key == approval_level.GOAL_KEY and value is True) or (key == "jev.mode" and (value == "off" or cheap))
        uv = widen and confirm_tier.owner_action("approval_widen") == confirm_tier.UV   # P550 (P551 review S4): the table decides
        pk = self.st.passkey_of_device(s.device) if uv else None
        if uv and not pk:
            self.st.log("control_refused", device=s.device, action="approval_set", reason="no_passkey")
            controls.log(self.st, action="approval_set", device=s.device, result="refused:no_passkey")
            return await self._ctl_res(s, r, "approval_set", False, why="no_passkey")
        ch = None
        if pk:
            n = obj.get("n")
            if not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
                return await self._ctl_res(s, r, "approval_set", False, why="shape")
            ch = passkey.elevate_challenge(self.channel, s.device, n, "approval_set", n,
                                           controls.object_digest("approval_set", target))
            if obj.get("fa") is None:
                return await self._ctl_res(s, r, "approval_set", False, why="passkey_required", fa=pk["id"], fa_s=passkey.pk_state(pk),
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
            controls.log(self.st, action="approval_set", device=s.device, result="refused:" + why,
                         extra={"passkey": "uv"} if pk else None)   # P550 (S5)
            return await self._ctl_res(s, r, "approval_set", False, why=why)
        rid, sk = secrets.token_hex(16), self.st.sign_key(s.device)
        text = controls.object_text("approval_set", target)
        try:                                    # the signer first: the `preferences` broadcast below shows the effective value
            await asyncio.to_thread(approval_level.note_signer, self.st, key, value, s.device, sk, rid,
                                    {"n": obj["n"], "ts": obj["ts"], "sig": obj["sig"]}, s.name or self._dname(s.device))
        except Exception as e:  # noqa: BLE001 — logged; full / the goal switch then simply do not count
            bg.swallowed(self.st.log, "approval_signer_failed", e, every=0)
        self._level_before = preferences.get(self.preferences, approval_level.KEY)
        self._approval_set_key = key            # apply_preferences keeps this key's signer (N4: any other write drops it)
        try:
            res = {"ok": True} if preferences.get(self.preferences, key) == value else await self.set_pref(key, value)
        finally:
            self._approval_set_key = None
        ok = bool(res.get("ok"))
        controls.log(self.st, action="approval_set", device=s.device, result="ok" if ok else "rejected", obj=target,
                     sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"), extra={"passkey": "uv"} if pk else None)   # P550 (S5)
        if not ok:
            await asyncio.to_thread(approval_level.drop_signer, self.st, key)
            return await self._ctl_res(s, r, "approval_set", False, why="rejected")
        digest = hashlib.sha256(text.encode()).hexdigest()
        with contextlib.suppress(Exception):
            approvals.record(self.st, rid=rid, agent="phone", tool="approval_set", input_sha256=digest, shown_sha256=digest,
                             decision="allow", reason="device", device=s.device, sign_pub=sk, sig=wire.unb64u(obj["sig"]),
                             control={"n": obj["n"], "sig_ts": obj["ts"],
                                      "object_sha256": controls.object_digest("approval_set", target)})
        await self._send_ready(lambda x: self.preferences_msg(x))   # the effective level / switch (host.approval)
        self.st.log("approval_set", device=s.device, key=key, status=str(value))
        if key == approval_level.GOAL_KEY and value is False:
            d = goal_mod.load(self.st)          # a goal that started without a card ends with the switch that allowed it
            if d and d.get("state") == "active" and d.get("preauth"):
                self.goals.end("stopped", by="switch")
        self.approval_check("signed")            # P552: in force again → the next lapse is told again
        if key == approval_level.KEY:
            self._unsigned_full_told = False
            if set(approval_level.SIGNED) & {value, getattr(self, "_level_before", None)} or approval_level.KEY in self.reload_pending:
                with contextlib.suppress(Exception):   # P551: the hook reads the level at launch — restart before the next turn
                    self._reload_runtime()
        by = s.name or self._dname(s.device) or s.device
        self.activity("decision", by=by, result="allow", tool=pick(self.lang, "审批设置", "Approval setting"), text=text[:80])
        self.agent_notice(approval_level.changed_text(key, value, by, self.lang))
        self.push_notify("security")
        await self._ctl_res(s, r, "approval_set", True, key=key, value=value)

    def theme_refresh(self) -> bool:

        from . import theme
        st = theme.stamp(self.st.root)
        if self.theme_cached is not None and st == self.theme_stamp:
            return False
        old = self.theme_cached
        self.theme_stamp, self.theme_cached = st, theme.phone_message(self.st.root)
        return old is not None and old.get("rev") != self.theme_cached.get("rev")

    def name_changed(self) -> None:

        msg = self._status_msg()
        self._post(self._send_ready, lambda s: msg)
        self._post(self._send_ready, lambda x: self.preferences_msg(x))

    def _post(self, coro_fn, *args) -> None:

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
                obj = obj_fn(s)
                if obj is not None:
                    await self.send_app(s, obj)

    async def _send_legacy(self, obj_fn) -> None:

        for s in list(self.sessions.values()):
            if s.state == "ready" and not s.p33 and self.st.is_allowed(s.pub):
                await self.send_app(s, obj_fn(s))

    async def _send_p33(self, obj_fn) -> None:
        for s in list(self.sessions.values()):
            if s.state == "ready" and s.p33 and self.st.is_allowed(s.pub):
                await self.send_app(s, obj_fn(s))

    async def _send_device(self, device: str, obj: dict) -> None:

        for s in list(self.sessions.values()):
            if s.state == "ready" and s.p33 and s.device == device and self.st.is_allowed(s.pub):
                await self.send_app(s, obj)

    # ------------------------------------------------------------ history pages (PROTOCOL §10.5)
    def hist_metadata(self):
        return {**self.hist.meta(), "read": self.readmark.value(self.hist)}

    def hist_emit(self, turns: list[dict]) -> None:
        from .history_delivery import for_host
        for_host(self).enqueue(turns)

    def hist_meta_all(self) -> None:
        from .history_delivery import for_host
        for_host(self).enqueue(meta=True)

    def hist_add(self, src: dict, reply: str = "", end: str = "done", card: dict | None = None, form: dict | None = None) -> dict:
        if src.get("k") == "sys":
            src = {**src,"name":"AgentJ"}  # presentation only; never rename the configured owner/Agent
        if src.get("k") in ("sys", "cmd"):
            from .system_text import remote
            reply = remote(reply)
        t = self.hist.add(src, wire.well_formed(reply), end, card, form=form)
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
    def _agent_cwd(self) -> str | None:

        c = getattr(getattr(self.agent, "state", None), "cwd", None)
        return c if isinstance(c, str) else None

    def _agent_image_root(self) -> str | None:
        """#558: current native thread only, using its actual launch home (including account slots)."""
        a = getattr(self, "agent", None)
        sid = getattr(a, "tid", None)
        if getattr(a, "kind", None) != "codex" or not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
            return None
        home = getattr(a, "image_home", None)
        if not isinstance(home, str) or not home:
            return None
        return os.path.join(os.path.realpath(home), "generated_images", sid)

    def media_later(self, tid: int | None) -> None:
        from .media_live import schedule
        schedule(self, tid)

    async def _attach_media(self, tid: int) -> None:
        self.media_later(tid)
        await asyncio.gather(*list(self.media_jobs))

    def on_media_get(self, s: Session, obj: dict) -> None:

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
        except Exception as e:  # noqa: BLE001 — P424 #142: a download that cannot be served says so at once (下载 → 「下载失败」)
            if live():
                await self.send_app(s, {"t": "media_err", "mid": mid, "why": "failed"})
            self.st.log("media_window_fail", device=s.device, exception_type=type(e).__name__)
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
        msg = self._status_msg()
        phase = (msg.get("ph"), msg.get("tool"))
        if st == self.sent_status and msg["goal_active"] == getattr(self, "sent_goal_active", None) and phase == getattr(self, "sent_phase", None) and msg.get("main_thread") == getattr(self, "sent_main_thread", None):
            return
        self.sent_status = st
        self.sent_goal_active = msg["goal_active"]
        self.sent_phase = phase
        self.sent_main_thread = msg.get("main_thread")
        st = st.split(":")[0]
        self.emit("agent_status", s=st)
        self._post(self._send_ready, lambda s: msg)

    def push_status(self) -> None:

        self.sent_status = None
        self.status_changed()

    def agent_status(self, s: str) -> None:
        self.status_changed()

    async def key_import_note(self) -> str:

        from . import keys
        task = self.__dict__.get("_key_import_task")
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(keys.import_note, self))
            self._key_import_task = task
        done, _ = await asyncio.wait({task}, timeout=.05)
        if not done:
            return ""
        self._key_import_task = None
        try:
            return task.result()
        except (OSError, ValueError):
            return ""

    def native_route(self, name):

        a = self.agent
        if not a or isinstance(a, SharedClaudeAgent):
            return None
        return harness_cmds.route(a.kind, name, getattr(a, "init", None))

    def native_text(self, text):

        a = self.agent
        if not a or isinstance(a, SharedClaudeAgent):
            return None
        p = slash.parse(text)
        if not p or a.passthrough(p[0]):           # a skill of the same name (/focus, /import …) stays the skill
            return None
        return harness_cmds.agent_text(text, a.kind, getattr(a, "init", None), self.lang)

    def agent_text(self, text: str) -> None:
        text = drop_replacement(text)       # P281: every harness's reply text passes here — no stray 「◆?」 on any channel
        if not text or not text.strip():
            return
        _hb_touch(self)
        self.turn_shown = True
        self.jev_plan = (getattr(self, "jev_plan", "") + "\n" + text)[-approval_jev.MAX_TEXT:]
        from .silent import is_silent
        page = self.hist.get(self.cur_turn) if self.cur_turn is not None else None
        if page is not None and is_silent(text):
            root = next((ids[0] for ids in getattr(self.hist, "parts", {}).values() if self.cur_turn in ids), self.cur_turn)
            review_card = (page.get("card") or (self.hist.get(root) or {}).get("card") or {}).get("approval_review")
            if review_card in ("question", "denied", "renew"):
                from .silent import strip_markers
                text = strip_markers(text)
                if not text.strip():
                    prior = (page.get("reply") or {}).get("text", "")
                    if prior.strip() and not page.get("silent") and not is_silent(prior):
                        self.turn_text = True
                        return
                    text = ("审批解释尚未完成，请让主线程继续说明。" if self.lang != "en" else
                            "The approval explanation is not complete. Ask the main conversation to continue explaining.")
        if is_silent(text):
            folded_tid = self.cur_turn
            self.turn_text = False
            if page is not None and getattr(self, "wait_note", None):
                self.turn_wait_note(None)   # a wait line is the host's, never the Agent's words to fold
            if page is not None:
                rep = page.get("reply") or {}
                said = (rep.get("text") or "").strip()
                if said and not is_silent(said):
                    ids = next((ids for ids in getattr(self.hist, "parts", {}).values() if self.cur_turn in ids), [self.cur_turn])
                    for tid in ids:
                        old = self.hist.get(tid) or {}
                        prior = (old.get("reply") or {}).get("text", "")
                        self.hist_update(tid, text=text, pre=prior)
                    self.turn_text = False
                else:
                    changed = self.hist_update(self.cur_turn, append=text)
                    if changed:
                        self.cur_turn = changed[-1]["id"]
            else:
                folded_tid = self.hist_add({"k": "agent", "text": ""}, text, "done")["id"]
            self.st.log("silent_folded", turn=folded_tid)
            return
        from .silent import strip_markers
        if page is not None and (page.get("silent") is True or is_silent(page["reply"].get("text"))):
            rep = page["reply"]
            prior = "\n\n".join(v for v in (rep.get("pre", ""), rep.get("silent_raw", rep.get("text", ""))) if v)
            self.hist_update(self.cur_turn, text=strip_markers(prior), pre="")
        text = strip_markers(text)
        if getattr(self, "voice_stream", None) is not None and self.voice_stream.wants_reply(getattr(self.agent, "cur_send", None)):
            self._post(self.voice_stream.reply, getattr(self.agent, "cur_send", None), text)
        self.turn_text = True
        if getattr(self, "wait_note", None):
            self.turn_wait_note(None)       # P151b: the reply replaces the wait line, never follows it
        self.agent_said_at = time.time()          # P155: the harness-switch job's proof that the new Agent answered
        for chunk in agents.split_text(text, wire.MAX_TEXT):
            self.emit("agent_msg", text=clean(chunk, wire.MAX_TEXT))
            e = self._remember("agent", chunk)
            if any(s.state == "ready" and not s.p33 for s in self.sessions.values()):
                self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        tid = None
        if self.cur_turn is not None and self.hist.get(self.cur_turn) is not None:
            changed = self.hist_update(self.cur_turn, append=text)
            if changed:
                self.cur_turn = changed[-1]["id"]
            self.media_later(tid := self.cur_turn)
        elif not _echo_page(self, text):    # the Agent spoke outside a turn of ours (e.g. a background task finished)
            self.media_later(tid := self.hist_add(self._agent_src(), text, "done")["id"])
        login_flow.on_text(self, tid, text)    # 0269-login: a harness login failure → 「重新登录」 under this page

    def proactive_turn(self, via: str) -> None:

        _hb_touch(self)
        self.desktop_end()
        self.proactive_via = via if via in ("loop", "task") else None

    def _agent_src(self) -> dict:
        via = getattr(self, "proactive_via", None)
        return {"k": "agent", "text": privacy.owner_text(getattr(self, "proactive_input", "") or "") if via else "",
                **({"via": via} if via else {})}

    def desktop_input(self, text: str) -> None:
        if not text or not text.strip():
            return
        _hb_touch(self)
        self.desktop_end()
        desktop = self.hist_add({"k": "host", "name": pick(self.lang, "Agent J 主机", "Desktop"), "text": text}, "", "open")
        self.desktop_turn = desktop["id"]
        from . import approval_context
        approval_review.remember(self, desktop, provenance.terminal())

    def turn_queued_note(self, on: bool) -> None:     # P133: busy shared session, message in its queue
        tid, note = self.cur_turn, humane.of(self, "shared.busy_queued")
        page = self.hist.get(tid) if tid is not None else None
        if page and page["end"] == "open" and page["reply"]["text"] == ("" if on else note):
            card = {k: v for k, v in (page.get("card") or {}).items() if k != "queued"}   # P281: the phone's robot marks it
            self.hist_update(tid, text=note if on else "", card={**card, **({"queued": True} if on else {})})

    def turn_wait_note(self, text: str | None, stall: bool = False, final: bool = False) -> None:

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
        from .silent import is_silent
        if is_silent(text):
            if tid is None:
                tid = self.hist_add(self._agent_src(), text, "done")["id"]
                self.st.log("silent_folded", turn=tid)
                return
            ids = next((ids for ids in getattr(self.hist, "parts", {}).values() if tid in ids), [tid])
            for part in ids:
                old = (self.hist.get(part) or {}).get("reply") or {}
                prior = old.get("text", "")
                self.hist_update(part, text=text, pre=prior if not is_silent(prior) else old.get("pre", ""))
            self.st.log("silent_folded", turn=tid)
            return
        if tid is None:     # P205 (6): a finished page of its own (e.g. 后台任务完成) — its files are offered like any page's
            if _echo_page(self, text):
                return
            self.media_later(tid := self.hist_add(self._agent_src(), text, "done")["id"])
        else:
            changed = self.hist_update(tid, append=text)
            if changed:
                self.desktop_turn = changed[-1]["id"]
            self.media_later(tid := self.desktop_turn)
        login_flow.on_text(self, tid, text)

    def desktop_end(self) -> None:
        from . import main_thread
        main_thread.completed(self)
        self.proactive_via = None
        self.proactive_input = ""
        _hb_touch(self)
        tid = getattr(self, "desktop_turn", None)
        if tid is not None:
            self.hist_update(tid, end="done")
            self.media_later(tid)
        self.desktop_turn = None

    def agent_recovery_notice(self, text: str) -> None:
        self.agent_notice(text, recovery=True)

    def agent_notice(self, text: str, *, recovery=False, card: dict | None = None, repair: str | None = None) -> int | None:
        from .system_text import remote
        text = remote(one(self.lang, text))       # P128: a legacy "中文 / English" notice reaches the phone in one language
        local, self.notice_local = getattr(self, "notice_local", False), False
        self.turn_shown = True
        self.emit("agent_notice", text=text)
        e = self._remember("notice", text)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        t = self.hist_add({"k": "sys", "text": "", **({"local": True} if local else {}),
                           **({"repair":"reconnect"} if repair=="reconnect" else {})}, text, "done",
                          card={"recovery": "restart_session"} if recovery else card)
        login_flow.mark(self, t["id"], login_flow.take_pending(self))   # 0269-login: an adapter's login failure notice
        return t["id"]

    def channel_notice(self, text: str, card: dict, files=()) -> dict:
        from .system_text import remote
        from . import channel_card
        text = remote(text)
        self.turn_shown = True
        self.emit("agent_notice", text=text)
        e = self._remember("notice", text)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        source = card["channel"]["source"]
        t = self.hist_add({"k": {"mail": "email"}.get(source, source), "text": card.pop("_input", card["channel"]["text"]),
                           "via": source, "name": card["channel"]["label"],
                           "att": [{"name": name, "mime": "application/octet-stream", "bytes": 0, "kind": "file"}
                                   for name in card["channel"].get("files", [])]}, "", "open", card=card)
        self.channel_media(t, files)
        return t

    def channel_media(self, t, files=()) -> asyncio.Task | None:

        from . import channel_card
        refs = channel_card.media_refs(files)
        if not refs or not getattr(self.media, "workdir", None) or not isinstance(t, dict) or type(t.get("id")) is not int:
            return

        async def attach(tid=t["id"]):
            items, skips = await asyncio.to_thread(self.media.scan, refs, tid)
            if items or skips:
                self.hist_update(tid, media=(items, skips))
                self.st.log("media_page", turn=tid, status=f"{len(items)}/{len(skips)}")
        try:
            return self._bg(attach(), "channel_media")
        except RuntimeError:            # not inside the loop (a unit test calling it directly): no cards, the text stands
            attach().close()

    def local_notice(self, text: str, card: dict | None = None) -> None:

        self.notice_local = True
        try:
            # a card-less notice keeps the one-argument call (stand-ins and older overrides of agent_notice)
            self.agent_notice(text, card=card) if card else self.agent_notice(text)
        finally:
            self.notice_local = False

    def turn_failed(self) -> None:
        self.cur_failed = True

    def fallback_failure(self, text: str) -> None:
        from .privacy import owner_text
        from .silent import strip_markers
        self._fallback_error = owner_text(strip_markers(text)[:4000])

    # ------------------------------------------------------------ spend & speed (P204, PROTOCOL §29, usage.py)
    def _quota_pct(self):
        h5 = self.meter_state.get("h5")
        return h5.get("pct") if isinstance(h5, dict) and isinstance(h5.get("pct"), (int, float)) else None

    def turn_use(self, key: str, toks=None, usd=None, model=None) -> None:
        if self.turn_acc is not None:
            self.turn_acc.put(key, toks, usd, model)

    def _turn_use_done(self) -> dict | None:
        """The finished turn's `use` (its page and the local log), or None when nothing was measured."""
        acc, self.turn_acc = self.turn_acc, None
        self.agent_phase(None)
        if acc is None:
            return None
        from . import usage as usage_mod
        # a subscription login is what the harness shows by sending its 5-hour window; then quota share, not dollars
        q1 = self._quota_pct()
        use = acc.finish(billing="sub" if q1 is not None else "api", q1=q1, st=self.st)
        if not any(use.get(k) for k in usage_mod.TOKEN_KEYS):   # nothing reported (e.g. a shared session): the time only
            return None if use["s"] < 1 else {"s": use["s"], **({"h": use["h"]} if use.get("h") else {})}
        usage_mod.record(self.st, "turn", use)
        self.usage_today_cache = None
        return use

    def usage_today(self) -> dict:
        from . import usage as usage_mod
        day = time.strftime("%Y-%m-%d")
        if not self.usage_today_cache or self.usage_today_cache[0] != day:
            self.usage_today_cache = (day, usage_mod.today(self.st))
        return self.usage_today_cache[1]

    def agent_phase(self, ph, tool=None) -> None:
        """P204 (§29): tool running vs writing. The turn's clock always; the phones at most every PHASE_GAP s (the last
        state always arrives)."""
        if self.turn_acc is not None:
            self.turn_acc.set_phase(ph)
        elif ph is not None:
            return
        new = (ph, clean_line(tool, 40) if ph == "tool" and isinstance(tool, str) else None) if ph else None
        if new == self.phase_now:
            return
        self.phase_now = new
        if self.phase_timer is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self.phase_timer = loop.call_later(0 if new is None else PHASE_GAP, self._phase_send)

    def _phase_send(self) -> None:
        self.phase_timer = None
        self.status_changed()

    def official_notices(self, items: list) -> None:

        cats = notices.local_categories(self.st, self.preferences)
        for n in items:
            action = notices.policy(n, self.preferences, cats)
            if action == "silent":
                notices.processed(self.st, n['id'])
                continue
            if notices.presented(self.st, n['id']):
                text = pick(self.lang, "Agent J 官方", "Agent J official") + " · " + n['title_' + self.lang] + "\n" + notices.body(n, self.lang, notices.release_notes.current(self.st, self.agent))
                entry = self._remember("notice", text)
                self._post(self._send_legacy, lambda s, e=entry: self._render(e, s))
                self.hist_add({"k": "sys", "text": "", "notice_id": n['id']}, text, "done")
                self.push_notify("security" if n['type'] == "security" else "ask" if n['priority'] == "urgent" else "reply")
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

        await self.upgraded_notice()
        await asyncio.sleep(UPDATE_FIRST)
        while True:
            try:
                from . import auto_update, nightly_report
                nightly = await asyncio.to_thread(nightly_report.check, self.st)
                if nightly is not None:
                    self.reporter.trigger("nightly_update")
                with contextlib.suppress(Exception):     # 0.30.8 #1: trashbin daily sweep (04:30) + disk watermark, off-loop
                    from . import trash as trash_mod
                    await asyncio.to_thread(trash_mod.tick, self.st)
                idle_newer = auto_update.newer_known(self.st) and not auto_update.impact(self)
                if auto_update.due(self.st, blocked=auto_update.busy(self), stopped=self.stopped(), newer=idle_newer):
                    job = auto_update.read(self.st, auto_update.JOB)
                    if job.get("phase") == "done":
                        (self.st.root / auto_update.JOB).unlink(missing_ok=True)
                    auto_update.launch(self.st)
                note = nightly["notice"] if nightly is not None else await asyncio.to_thread(update.daily, self.st)
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

        try:
            from . import main_identity as _mi, starters
            line = await asyncio.to_thread(starters.take_announcement, _mi.working_root(self.agent_cfg),
                                           preferences.get(self.preferences, "appearance.language", "zh"))
        except Exception:  # noqa: BLE001 — the notice goes out without it
            return ""
        return "\n\n" + line if line else ""

    async def upgraded_notice(self) -> None:

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
            for _ in range(180):
                recovery = service_recovery.read(self.st)
                if not recovery or recovery.get("status") not in ("pending", "running") or service_recovery.failure(self.st):
                    break
                await asyncio.sleep(.25)
            warning = service_recovery.failure(self.st)
            notice = self.st.root / service_recovery.NOTICE
            if warning and (notice.exists() or service_recovery.receipt_path().exists() or warning.get("status") != "failed"):
                self.cmd_card("update", slash.Result(service_recovery.failure_text(warning,mobile=True), "error"))
                notice.unlink(missing_ok=True)
            completed = phone_update.take(self.st)
            if completed:
                if completed.get("reason") in ("upgraded", "doctor_failed"):
                    from . import doctor
                    try:
                        rows = await asyncio.to_thread(doctor.run, self.st)
                        completed["counts"] = update.doctor_counts(rows)
                        completed["checks"] = [{"id": r.get("id"), "status": r.get("status"), **({"session_disconnected": True} if r.get("session_disconnected") is True else {}), **({"summary": "no verified main Agent launch"} if update.benign_waiting(r) else {})} for r in rows]
                        failed, completed["existing_issues"] = update.upgrade_health(rows)
                    except Exception:
                        completed["counts"], failed = None, True
                    completed["reason"] = "doctor_failed" if failed else "upgraded"
                    completed["result"] = "failed" if failed else "ok"
                web, machine = await self._receipt_context()   # P320: the page's version + which computer
                text = phone_update.text(completed, self.lang, web, machine)
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
        self.st.log("upgrade_notice", status=str(rec.get("to"))[:32])
        if not counts or update.upgrade_blockers(rows)[0]:
            self.cmd_card("update", slash.Result(note, "error"))
            return
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": "", "repair":"reconnect"}, note, "done")

    async def _receipt_context(self) -> tuple:

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
        from . import application_ingress
        application_ingress.note_turn(self, send)     # #0302: guest-born processes never get the owner_local frame
        if (peers := getattr(self, "peers", None)) is not None:
            peers.xfer.begin_execution(send)
        self._fallback_error, self._fallback_used = "", False
        if getattr(self, "heartbeat", None) is not None:
            self.heartbeat.usage_start(send)
        if (launch := getattr(getattr(self, "busy", None), "launch", None)) is not None:
            owner_words = (text if send is not None and provenance.is_owner(getattr(send, "source", None))
                           and isinstance(text, str) else None)
            launch.turn(self.busy.trusted(), words=privacy.redact(owner_words) if owner_words else None)   # E2: owner chain
        self._foreign_consumed = False
        if getattr(self, "agent", None) is not None:
            try:    # #502: external content plus its immediate follow-up; cannot tell → set
                if provenance.is_owner(provenance.turn_source(self.agent)) and not provenance.untrusted_turn(self.agent):
                    self._foreign_seen(False)
                elif provenance.untrusted_turn(self.agent) and provenance.unattended_turn(self.agent) not in ("system", "scheduled_task"):
                    if not getattr(send, "verified_mail_notice", False):
                        self._foreign_seen(True)
                else:
                    self._foreign_consumed = self._foreign_seen()
            except Exception:  # noqa: BLE001
                self._foreign_seen(True)
        from . import latency
        if not hasattr(self, "lat_observer"):
            self.lat_observer = latency.Observer()
        self.lat_observer.begin(harness=getattr(self.agent, "kind", None),
                                fresh=bool(getattr(self.agent, "fresh", False)),
                                short=len(text or "") <= latency.SHORT_UNITS)
        self.turn_t0 = time.monotonic()
        self.turn_shown, self.turn_at = False, (time.time(), 0.0, False)
        _hb_touch(self)
        self.jev_owner, self.jev_plan = text or "", ""      # P157: layer 2's view of this turn
        by = send.by if send is not None and send.by else self.turn_by
        self.activity("turn_start", by=by, text=text[:120])
        self.turn_by = None
        from . import usage as usage_mod
        self.turn_acc = usage_mod.Turn(self.agent.kind if self.agent else None, self._quota_pct())
        self.cur_turn, self.cur_failed = (send.turn if send is not None else None), False
        if getattr(send, 'device', None) == 'same-owner':
            self.hist_update(send.turn, text='', end='open')  # the queue/working status is not the Agent's answer
        # P282: the Agent took this say off the queue (a shared Codex may need ~1 min before turn/start): no longer "queued"
        if send is not None and isinstance(getattr(send, "sid", None), str) and not any(
                getattr(send, k, None) for k in ("heartbeat_key", "bot_handoff_id", "official_notice_id", "quota_resume_id",
                                                 "auto_notify", "hook_notice", "compact_resume")):
            self._post(self._send_device, send.device, {"t": "say_state", "sid": send.sid, "s": "taken"})

    def _foreign_seen(self, value=None) -> bool:
        """External content taints the next turn only. Own completion and verified read-only mail do not add taint."""
        st = getattr(self, "st", None)
        if st is None:
            return True if value is None else bool(value)     # no state: cannot tell → seen
        p = st.root / "busy" / "foreign_seen"
        if value is None:
            return p.exists()
        try:
            if value:
                p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                p.touch()
            else:
                p.unlink(missing_ok=True)
        except OSError:
            pass
        return bool(value)

    def agent_first_out(self) -> None:
        observer = getattr(self, "lat_observer", None)
        if observer is not None:
            observer.first_output()

    def agent_turn_end(self) -> None:
        from . import application_ingress, main_thread
        application_ingress.close_span(self)          # #0302 r2: a non-owner turn's span ends with the turn
        main_thread.completed(self)
        if getattr(self, "voice_stream", None) is not None and self.voice_stream.wants_reply(getattr(self.agent, "cur_send", None)):
            self._post(self.voice_stream.reply, getattr(self.agent, "cur_send", None), "", True)
        approval_review.acknowledge(self, getattr(self.agent, "cur_send", None))
        approval_review.retry_events(self)
        self.__dict__.get("task_grants", {}).clear()
        if self.__dict__.get("_bounded_plan_manager") is not None:
            self._bounded_plan_manager.end_all()
        if getattr(self, "_foreign_consumed", False):
            self._foreign_seen(False)
            self._foreign_consumed = False
        if (peers := getattr(self, "peers", None)) is not None:
            peers.xfer.end_execution()
        if getattr(self, "heartbeat", None) is not None:
            self.heartbeat.usage_end()
        if (launch := getattr(getattr(self, "busy", None), "launch", None)) is not None:
            launch.end()
        self.st.log("turn_end", agent=self.agent.kind if self.agent else None)
        observer = getattr(self, "lat_observer", None)
        if observer is not None:
            from . import latency
            row = observer.finish("stopped" if getattr(self.agent, "halting", False) else
                                  "failed" if getattr(self, "cur_failed", False) else "done")
            if row is not None:
                try:
                    latency.add(self.st, row)
                except OSError:
                    self.st.log("latency_unavailable", reason="io")

        if getattr(self, "turn_heal", None) is not None:
            self.turn_heal.turn_ended()
        _hb_touch(self)
        halted = bool(self.agent and self.agent.halting)
        secs = time.monotonic() - self.turn_t0 if self.turn_t0 else None
        if (self.cur_turn is not None and not halted and not self.cur_failed and not getattr(self, "turn_shown", True)
                and secs is not None and secs < EMPTY_TURN):
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
        use = self._turn_use_done()
        if self.cur_turn is not None:
            self.hist_update(self.cur_turn, end="stopped" if halted else ("failed" if self.cur_failed else "done"),
                             **({"use": use} if use else {}))
            self.media_later(self.cur_turn)
            _rcpt_turn(self, self.cur_turn)             # P313
        if self.telegram and self.cur_turn is not None:
            self.telegram.completed(self.hist.get(self.cur_turn))
        if self.mail and self.cur_turn is not None:      # P170: the reply goes back by e-mail, same thread
            with contextlib.suppress(Exception):
                self.mail.completed(self.hist.get(self.cur_turn))
        if self.wechat and self.cur_turn is not None:    # P562: the owner's WeChat message → the answer back to WeChat
            with contextlib.suppress(Exception):
                self.wechat.completed(self.hist.get(self.cur_turn))
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

        out = [self._ask_msg(a, s) for a in list(self.asks.values())
               if not a.fut.done() and (not a.only or a.only == s.device) and (a.tool != pair_card.TOOL or s.pair1)]
        out += [self._grant_msg(g) for g in list(self.grants.values()) if self._grant_live(g)]
        if s.p33:
            out += [self._question_msg(q) for q in list(self.questions.values()) if not q.fut.done()]
            if self.peers is not None:
                out += self.peers.ask_msgs()
            if self._upg_live():
                out.append(self._upg_msg())
        if getattr(self, "external_channels", None) is not None:
            out += self.external_channels.open_messages(s)
        if getattr(self, "wechat", None) is not None:
            out += self.wechat.open_messages(s)
        out += self.elevate.all_open_msgs(s)
        out += self.capabilities.open_msgs(s)
        return out

    async def on_cards_get(self, s: Session, obj: dict) -> None:

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
        m = {"t": "ask", "id": a.rid, "tool": a.tool, "summary": a.envelope["details"] if hasattr(a, "envelope") else approval_review.safe_text(a.summary, 24000), "ttl": max(0, int(a.deadline - time.monotonic())),
             "cat": list(a.cats), "why": a.why}
        if hasattr(a, "envelope"):
            m["minimum_approval_protocol"] = 2 if a.review_v2_only else 1
            m["update_required_text"] = "请更新遥控器页面后审批；当前卡需要新版安全签名。 / Update the remote page to approve this card with its new secure signature."
            m.update(approval_v=2, envelope=a.envelope, envelope_sha256=a.envelope_digest, revision=a.review_revision, review_state=a.review_state)
        if a.tool == owner_grant.TOOL:
            m["owner_grant"] = a.tool_input.get("otg")
        if a.host_card:
            m["host_tool"] = True
        if a.why_en:
            m["why_en"] = a.why_en        # P128: the phone shows why or why_en by its language (never both)
        if a.cred:
            m["cred"] = a.cred            # "read" → 查看密码或密钥, "change" → 改密码或密钥
        pk = self.st.passkey_of_device(s.device) if s is not None and (a.require_passkey or a.pk_if_any) else None
        if a.require_passkey or (a.pk_if_any and pk):
            m["passkey_required"] = True
            if pk:
                m["fa"], m["fa_s"] = pk["id"], passkey.pk_state(pk)
                m["pk_kind"] = a.pk_kind
                m["fa_ch"] = wire.b64u(passkey.elevate_challenge(self.channel, s.device, a.rid, a.pk_kind, a.rid, getattr(a, "legacy_digest", a.digest)))
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
        if a.task_scope is not None:
            m["task_scope"] = a.task_scope
        if a.task_scope is not None and not a.require_passkey:   # P573 (#389): this allow also covers the task's same-class steps
            m["task_grant"] = TASK_GRANT_NOTE[card_lang(self.lang)]
        if a.allow_offer and not a.goal_add:   # P296: unticked 「以后发给 X 不用再确认」 boxes on the mail card
            m["allow_offer"] = list(a.allow_offer)
        if getattr(a, "order_origin", None):
            m["order_origin"] = a.order_origin
        m["reason"] = a.reason or ""
        if a.prow:                        # 0269-w3-policy (PROTOCOL §42): class + why the owner's task does not cover it
            from . import approval_policy
            m.update(approval_policy.card_fields(a.prow, card_lang(self.lang)))
        if hasattr(a, "envelope"):
            m.update(approval_v=2, envelope=a.envelope, envelope_sha256=a.envelope_digest, revision=a.review_revision)
            if pk is not None:
                m["envelope_fa_ch"] = wire.b64u(passkey.elevate_challenge(self.channel, s.device, a.rid, a.pk_kind, a.rid, a.envelope_digest))
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
        if scope is None or provenance.untrusted_turn(self.agent):
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

        now = time.monotonic()
        if now - self.__dict__.get("_offline_reminded", -1e9) < approval_level.OFFLINE_GAP:
            return
        self._offline_reminded = now
        n = sum(1 for a in self.asks.values() if not a.fut.done())
        n += sum(1 for c in self.elevate.cards.values() if not c["fut"].done() and not c.get("busy"))

        async def go():
            sent = await approval_level.remind_offline(self, max(1, n))
            self.st.log("offline_remind", result=",".join(sent) or "none", count=n)
        self._bg(go(), "offline_remind")

    def _park_key(self, tool: str, isha: str) -> tuple:

        source = approval_review.snapshot(self) or {}
        return (tool, isha, self.task_label, self.agent.kind if self.agent else "?", (self.agent_cfg or {}).get("dir"),
                source.get("session"), source.get("root_message_id"))

    def _parked_find(self, key: tuple):
        return next((a for a in self.asks.values() if a.parked and not a.fut.done() and a.park_key == key), None)

    def _parked_take(self, key: tuple, uv: bool = False) -> dict | None:
        ok = self.__dict__.setdefault("_parked_ok", {})
        rec = ok.pop(key, None)    # P570: a Face ID step only by a Face ID allow
        if rec is None or time.monotonic() > rec["until"] or self.st.sign_key(rec["device"]) != rec["sk"] or uv and not rec.get("uv"):
            return None
        return rec

    def _park(self, a: Ask, base: dict, summary: str, key: tuple, unattended: str | None = None, why: str = "no_remote") -> dict:

        parked = [x for x in self.asks.values() if x.parked and not x.fut.done()]
        for old in parked[:max(0, len(parked) - approval_level.PARK_MAX + 1)]:
            old.fut.set_result(("deny", None, None, None))          # the oldest parked card is withdrawn
        a.parked = True
        a.park_key = key
        a.review_waiter_ended = True
        if why != "question":
            a.deadline = time.monotonic() + approval_level.PARK_SECS
        loop = asyncio.get_running_loop()
        timer = loop.call_later(max(0, a.deadline - time.monotonic()), lambda: a.fut.done() or a.fut.set_result(("deny", None, None, None)))
        a.fut.add_done_callback(lambda f: (timer.cancel(), self._park_settle(a, base, summary)))
        r = "questioned" if why == "question" else "unanswered" if why == "unanswered" else "offline"
        approvals.record(self.st, **base, decision="deny", reason=r)
        self.st.log("ask_parked", id=a.rid, tool=a.tool, reason=why, **({"via": unattended} if unattended else {}))
        self.activity("decision", id=a.rid, tool=a.tool, result="parked", reason=r, cats=a.cats, task=a.task,
                      summary=summary[:200])
        self.emit("ask_parked", id=a.rid)
        if why == "question":
            return {"behavior":"deny", "request_id":a.rid, "review_pending":True, "message":"Owner questioned this approval. No operation executed. Explain the same request before reopening it; do not change its parameters."}
        return {"behavior": "deny", "message": approval_level.waiting_text(card_lang(self.lang)) if why == "unanswered"
                else approval_level.nobody_text(card_lang(self.lang))}

    # ------------------------------------------------------------ P539 (#300): 「这台遥控器上 Computer Use 免问」 (preauth.py)
    def _preauth_use(self, g: dict, base: dict, tool: str, tool_input: dict, summary: str, v) -> dict:

        rid = base["rid"]
        approvals.record(self.st, **base, decision="allow", reason="preauth", device=g["device"],
                         grant=g.get("rid") or "preauth", auto="preauth")
        self.st.log("preauth_use", id=rid, action=g["action"], device=g["device"], tool=tool,
                    via=(g.get("bind") or {}).get("kind"), **({"plugin": g["bind"]["id"]} if (g.get("bind") or {}).get("id") else {}))
        self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="preauth", device=g["device"])
        from . import preauth
        name = self._dname(g["device"])
        line = preauth.phone_line(tool, tool_input, self.lang)   # L2: no typed text on the phones, only its length
        self.activity("auto", id=rid, tool=tool, summary=line, by=name, cats=v.cats, task=self.task_label)
        self.emit("auto", id=rid, tool=tool, grant=None)
        return {"behavior": "allow", "updatedInput": tool_input}

    async def _preauth_add(self, a: Ask, action: str, did: str, sk: bytes, picks: list, bind: dict | None = None) -> None:

        from . import preauth
        if self.stopped() or not await asyncio.to_thread(preauth.add, self.st, action, did, sk, a.rid, bind):
            return
        approvals.record(self.st, rid=secrets.token_hex(16), agent=self.agent.kind if self.agent else "?", tool="preauth",
                         input_sha256=approvals.input_digest({"action": action}), shown_sha256=a.digest, decision="allow",
                         reason="preauth_add", device=did, sign_pub=sk, grant=a.rid, auto="preauth",
                         scope_sha256=approvals.allow_add_line(picks))
        self.st.log("preauth_add", id=a.rid, action=action, device=did, via=(bind or preauth.MCP_OPS)["kind"],
                    **({"plugin": bind["id"]} if bind and bind.get("id") else {}))
        self.activity("preauth_add", id=a.rid, action=action, by=self._dname(did))
        await self._preauth_push()

    async def _preauth_push(self, s=None) -> None:

        from . import preauth
        items = await asyncio.to_thread(preauth.items, self.st, self._dname)
        msg = {"t": "preauth", "items": items}
        if s is not None:
            await self.send_app(s, msg)
        else:
            self._post(self._send_p33, lambda x: msg)

    async def _preauth_revoke(self, why: str, *, action: str | None = None, device: str | None = None,
                              by: str | None = None) -> int:
        from . import preauth
        n = await asyncio.to_thread(preauth.revoke, self.st, action=action, device=device)
        if n:
            self.st.log("preauth_revoke", action=action, device=by or device, reason=why, count=n)
            self.activity("preauth_revoke", action=action, why=why, by=self._dname(by) if by else None)
            await self._preauth_push()
        return n

    async def on_preauth(self, s: Session, t: str, obj: dict) -> None:

        from . import preauth
        if t == "preauth_revoke":
            action, dev = obj.get("action"), obj.get("device")
            if action is not None and action not in preauth.GRANTABLE:
                return
            if dev is not None and not (isinstance(dev, str) and 1 <= len(dev) <= 64):
                return
            if not await self._preauth_revoke("phone", action=action, device=dev, by=s.device):
                await self._preauth_push(s)
            return
        await self._preauth_push(s)

    def _goal_grant_tap(self, a: Ask, did: str | None, picks: list) -> None:

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

    def _park_settle(self, a: Ask, base: dict, summary: str) -> None:
        self.asks.pop(a.rid, None)
        try:
            decision, did, sk, sig = a.fut.result()
        except Exception:  # noqa: BLE001
            decision, did, sk, sig = "deny", None, None, None
        reason = "device" if did else ("estop" if self.stopped() else "serve_stop" if self.stopping.is_set() else "timeout")
        picks = list(a.allow_add) if decision == "allow" and reason == "device" else []   # P339: the signed allow_add line
        if getattr(a, "answer_v", None) == 2:
            base = {**base, "shown_sha256":a.answer_digest, "approval_v":2}
        approvals.record(self.st, **base, decision=decision, reason=reason, device=did, sign_pub=sk, sig=sig,
                         scope_sha256=approvals.allow_add_line(picks) if picks else None,
                         **({"task_scope":a.task_scope,"task_uv":bool(a.require_passkey and a.uv_ok),"task_id":a.review_task}
                            if getattr(a,"list_model",False) and a.task_scope else {}))
        approval_review.finished(self, a, decision, reason)
        allowed = decision in ("allow", "allow_batch") and reason == "device" and not getattr(a, "secret_display_route", False)
        bounded = getattr(a, "bounded_plan", None)
        if bounded is not None:
            bounded.owner.finish_approval(bounded, a.rid, did, sk, allowed)
        if allowed and picks:
            self._goal_grant_tap(a, did, picks)
        if allowed and not self.stopped() and bounded is None:
            self.__dict__.setdefault("_parked_ok", {})[a.park_key] = {
                "rid": a.rid, "device": did, "sk": sk, "until": time.monotonic() + approval_level.PARK_OK_SECS,
                "uv": bool(a.require_passkey and a.uv_ok)}
        result = ("allow" if allowed else decision) if reason == "device" else {"timeout": "timeout", "estop": "stopped"}.get(reason, "gone")
        self.st.log("ask_done", id=a.rid, tool=a.tool, decision=decision, reason=reason, device=did, parked=True)
        self.activity("decision", id=a.rid, tool=a.tool, result=decision if reason == "device" else reason, reason=reason,
                      by=self._dname(did) if did else None, cats=a.cats, task=a.task, summary=summary[:200])
        self.emit("ask_done", id=a.rid, result=result, reason=reason, device=did)
        why = _done_why("offline" if reason == "timeout" else reason, decision, a)   # parked = nobody was online
        self._post(self._send_ready, lambda s: {"t": "ask_done", "id": a.rid, "result": result, **why})
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
                send = ReadBackSend(note)
                send.approval_origin = getattr(a, "review_origin", None)
                if allowed and getattr(a,"list_model",False):
                    from . import list_approval
                    list_approval.bind_resume(self,a,send,did,sk,sig)
                if getattr(a, "bounded_plan", None) is not None:
                    send.bounded_plan_resume = a.rid
                self.agent.submit(send)

    async def _parked_rewait(self, a: Ask, base: dict, gone, key: tuple, _receipt: dict | None = None) -> dict:

        if getattr(a, "review_state", None) == "questioned":
            return {"behavior": "deny", "message": "主人正在核查这张审批卡；请先解释，不要重试执行。 / The owner is reviewing this card; explain before retrying."}
        if not self._online_approvers():
            if type(_receipt) is dict:
                _receipt.update(reason='offline', approval_card_shown=None)
            approvals.record(self.st, **base, decision="deny", reason="offline")
            self.st.log("ask_done", id=base["rid"], tool=a.tool, decision="deny", reason="offline")
            return {"behavior": "deny", "message": approval_level.nobody_text(card_lang(self.lang), again=True)}
        done, _ = await asyncio.wait({a.fut} | ({gone} if gone else set()), timeout=self.ask_ttl, return_when=asyncio.FIRST_COMPLETED)
        await asyncio.sleep(0)                  # the done-callback (_park_settle) runs first
        ok = self._parked_take(key)
        if ok is not None:
            approvals.record(self.st, **base, decision="allow", reason="parked", device=ok["device"], grant=ok["rid"],
                             auto="parked")
            self.st.log("ask_done", id=base["rid"], tool=a.tool, decision="allow", reason="parked", device=ok["device"])
            self.activity("auto", id=base["rid"], tool=a.tool, summary=a.summary, by="parked", task=self.task_label)
            return {"behavior": "allow", "updatedInput": a.tool_input}
        approvals.record(self.st, **base, decision="deny", reason="parked_wait")
        did = a.fut.done() and a.fut.result()[1]          # P570
        if type(_receipt) is dict:
            reason = ('device' if did and a.fut.result()[0] == 'deny' else
                      'agent_gone' if gone is not None and gone.done() else
                      'estop' if self.stopped() else 'serve_stop' if self.stopping.is_set() else
                      'timeout' if not done else 'unknown')
            _receipt.update(reason=reason, approval_card_shown=None)
        msg = pick(self.lang, "用户在手机上拒绝了这个操作。", "The owner declined this on the phone.") if did else _timeout_text(self.lang, int(self.ask_ttl))
        return {"behavior": "deny", "message": msg}

    def new_perm_env(self, background: str | None = None, *, cwd: str | None = None) -> dict:

        self.perm_token = secrets.token_urlsafe(32)
        self.perm_bg_next = background
        directory = cwd or (getattr(self, 'agent_cfg', None) or {}).get('dir')
        self.perm_cwd_next = os.path.realpath(directory) if isinstance(directory, str) else None
        self.perm_task_next = provenance.task_scope() if background == "task" else None
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

        claimed = False
        gone: dict[int, asyncio.Future] = {}
        try:
            line = await r.readline()
            req = json.loads(line) if line else None
            tok = self.perm_token
            side = None
            if (not isinstance(req, dict) or req.get("t") != "claim" or not tok
                    or not hmac.compare_digest(str(req.get("token", "")).encode(), tok.encode())):
                side = self._side_claim(req)     # P543: an owner-channel avatar's own one-time claim (never the main one)
                if side is None:
                    self.st.log("perm_refused", reason="spent" if tok is None else "bad_token")
                    return
            conn_bg = None
            conn_task = None
            conn_context = None
            if side is None:
                self.perm_token, self.perm_w, claimed = None, w, True
                conn_bg, self.perm_bg_next = self.__dict__.get("perm_bg_next"), None   # P552: bound to this connection
                conn_task, self.perm_task_next = self.__dict__.get("perm_task_next"), None
                launch_cwd, self.perm_cwd_next = self.__dict__.get("perm_cwd_next"), None
                if launch_cwd is not None:
                    from .list_approval import PermissionContext
                    conn_context = PermissionContext(self, w, launch_cwd)
            await self._perm_send(w, {"t": "claimed"})
            (side["claimed"] if side else self.perm_claimed).set()
            self.st.log("perm_claimed", **({"side": "avatar"} if side else {}))
            while line := await r.readline():
                m = json.loads(line)
                if not isinstance(m, dict) or not isinstance(m.get("id"), int):
                    continue
                if m.get("t") == "cancel" and m["id"] in gone and not gone[m["id"]].done():
                    gone[m["id"]].set_result(True)
                elif m.get("t") == "ask" and m["id"] not in gone:
                    gone[m["id"]] = asyncio.get_running_loop().create_future()
                    self._bg(self._perm_ask(w, m, gone, side, conn_bg, conn_task, conn_context), "perm_ask")
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

    async def _perm_ask(self, w: asyncio.StreamWriter, m: dict, gone: dict, side: dict | None = None,
                        background: str | None = None, task_scope=None, launch_context=None) -> None:
        rid = m["id"]
        ans = {"behavior": "deny", "message": "请求无效，已拒绝。"}
        try:
            tool, tool_input = m.get("tool"), m.get("input")
            if isinstance(tool, str) and isinstance(tool_input, dict):
                if side is not None:
                    from .owner_avatar import ask_for
                    ans = await ask_for(self, side["label"], side["on_card"], clean_line_tool(tool), tool_input, gone.get(rid))
                else:
                    with provenance.task_request(task_scope):
                        broker = self.peers.xfer.broker if self.peers is not None and not background and task_scope is None else None
                        ans = await broker.execute(tool, tool_input, m.get("tool_use_id"), gone.get(rid)) if broker else None
                        # 0.30.6's host_turns.admit shortcut is absorbed by ADR-0307-offlist (list_approval → approval_offlist).
                        if ans is None:
                            # security r1 M1: a request on a permission connection that is no longer the main one never
                            # borrows the main thread's current turn (typed unknown → reads only, no card).
                            stale = not background and task_scope is None and self.perm_w is not w
                            ans = await self.ask(clean_line_tool(tool), tool_input, gone.get(rid), _harness_permission=True, _native_context=launch_context,
                                                 **({"background": background} if background else {"background": "detached"} if stale else {}))
        finally:
            gone.pop(rid, None)
            if side is not None or self.perm_w is w:
                await self._perm_send(w, {"t": "answer", "id": rid, **ans})

    def side_perm_env(self, label: str, on_card=None) -> tuple[dict, dict]:

        tok = secrets.token_urlsafe(32)
        side = {"token": tok, "label": str(label)[:120], "on_card": on_card, "claimed": asyncio.Event()}
        self.__dict__.setdefault("side_perm", {})[tok] = side
        return ({"AGENTJ_PERM_SOCK": str(self.st.perm_sock_path), "AGENTJ_PERM_TOKEN": tok,
                 "AGENTJ_PERM_WAIT": str(int(self.ask_ttl + 30))}, side)

    def side_perm_drop(self, side: dict) -> None:
        self.__dict__.get("side_perm", {}).pop(side.get("token"), None)

    def _side_claim(self, req) -> dict | None:
        tok = req.get("token") if isinstance(req, dict) and req.get("t") == "claim" else None
        if not isinstance(tok, str) or not tok:
            return None
        for k in list(self.__dict__.get("side_perm", {})):
            if hmac.compare_digest(k.encode(), tok.encode()):
                return self.side_perm.pop(k)          # one claim per token
        return None

    async def ask(self, tool: str, tool_input: dict, gone: asyncio.Future | None = None, batch: bool = True, risk_scope: bool = False, require_passkey: bool = False, context: dict | None = None,
                  ttl: float | None = None, grant_info: bool = False, origin: str | None = None,
                  allow_offer: list | None = None, _company_proposal=None, bind: dict | None = None,
                  avatar: str | None = None, background: str | None = None, always_ask: bool = False, task_actions: list | None = None, task_target: dict | None = None, only: str | None = None, _receipt: dict | None = None,
                  known_recipients: bool = False, _host_card: bool = False, _settings_session=None, _learn_proposal=None, _plan_only: bool = False, _bounded_context=None, _harness_permission=False, _native_context=None, _settings_context=None,
                  _front: dict | None = None, _order_owner: bool = False) -> dict:

        # Chairman 2026-11-08: main approval is explicit-list only. The legacy
        # implementation below is retained for rollback, never called here.
        if tool == "AskUserQuestion" and self.agent and self.agent.kind == "claude":
            return await self._ask_user_question(tool_input, gone)
        from . import list_approval, bounded_plan, policy_registry
        if tool == bounded_plan.TOOL and (not isinstance(_bounded_context, bounded_plan.SealedPlan)
                or _bounded_context.owner is not bounded_plan.manager(self)
                or _company_proposal is not policy_registry.HOST_CARD):
            return {"behavior":"deny", "message":"Host-owned bounded executor requires a sealed concrete plan."}
        if tool == "agentj learn" and (_learn_proposal is None or not self.learner.authorize(_learn_proposal, tool_input)):
            return {"behavior":"deny", "message":"Host-owned policy registration required."}
        return await list_approval.ask(self, Ask, tool, tool_input, gone=gone, ttl=ttl,
            grant_info=grant_info, receipt=_receipt, only=only, host_card=_host_card,
            settings_session=_settings_session, bounded_context=_bounded_context, harness_permission=_harness_permission,
            task_actions=task_actions, task_target=task_target, native_context=_native_context, settings_context=_settings_context,
            front=_front, background=("avatar", str(avatar)[:40]) if avatar is not None else background,
            order_owner=_order_owner is True)

        def observe(reason, shown=None):
            if type(_receipt) is dict:
                _receipt.update(reason=reason, approval_card_shown=shown)
        observe('unknown')
        def stopping_result():
            observe('serve_stop', False)
            rid = secrets.token_hex(16)
            base = dict(rid=rid, agent=getattr(self.agent, "kind", "?"), tool=tool,
                        input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(tool, ""), cats=[])
            approvals.record(self.st, **base, decision="deny", reason="serve_stop")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="serve_stop")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="serve_stop", cats=[], summary="")
            self.emit("ask_done", id=rid, result="deny", reason="serve_stop")
            return {"behavior": "deny", "message": "agentj serve 已停止：默认拒绝。", **({"reason": "serve_stop"} if grant_info else {})}
        if self.stopping.is_set():
            return stopping_result()
        # #488: unreadable launcher execution is never authorized by full
        # access, a prior grant, a dispatch identity, or even a fresh UV.
        if danger.unresolved_execution(tool, tool_input):
            observe('unresolved_execution', False)
            self.st.log("ask_done", tool=tool, decision="deny", reason="unresolved_execution")
            return {"behavior": "deny", "message": "Agent J: cannot resolve launcher execution; denied.",
                    **({"reason": "unresolved_execution"} if grant_info else {})}
        from . import policy_registry
        learned = (tool == "agentj learn" and _learn_proposal is not None
                   and self.learner.authorize(_learn_proposal, tool_input))
        if tool == "agentj learn" and not learned:
            policy_registry.log_unknown(self.st, tool, tool_input, agent=getattr(self.agent, "kind", "?"))
            return {"behavior": "deny", "message": UNKNOWN_ACTION}
        if learned:
            require_passkey, grant_info, always_ask, batch = True, True, True, False
        own = _company_proposal is policy_registry.HOST_CARD    # P570b L-c: not judged by the main turn's source
        from . import bounded_plan
        if tool == bounded_plan.TOOL and (not isinstance(_bounded_context, bounded_plan.SealedPlan)
                or _bounded_context.owner is not bounded_plan.manager(self) or not own):
            return {"behavior":"deny", "message":"Host-owned bounded executor requires a sealed concrete plan."}
        review_limit = approval_review.explanation_tool_allowed(self, tool, tool_input)
        if review_limit is False and not (tool == bounded_plan.TOOL and getattr(_bounded_context, "renewed_from", None) == (getattr(getattr(self.agent, "cur_send", None), "approval_review", None) or {}).get("id")):
            return {"behavior":"deny", "message":("Approval explanation permits ordinary read-only inspection only." if card_lang(self.lang) == "en" else "解释只允许普通只读核对；不能借解释执行原操作。")}
        if review_limit == "renew_uv":
            require_passkey = always_ask = True
        caller_uv = require_passkey
        if avatar is None and not own and (via := provenance.owner_channel_turn(self.agent)):
            from . import owner_channels
            return await owner_channels.ask(self, via, lambda lbl: self.ask(
                tool, tool_input, gone, batch, risk_scope, require_passkey, context, ttl, grant_info, origin, allow_offer,
                _company_proposal, bind, avatar=lbl, background=background, always_ask=always_ask, task_actions=task_actions, task_target=task_target, only=only, _receipt=_receipt,
                known_recipients=known_recipients, _host_card=_host_card, _settings_session=_settings_session, _learn_proposal=_learn_proposal, _plan_only=_plan_only, _bounded_context=_bounded_context))
        if tool == "AskUserQuestion" and not self.stopped() and approval_level.level(self.preferences, self.st) == "harness":
            self.st.log("question_autonomous", tool=tool, reason="level_harness")
            return {"behavior": "deny", "message": AUTONOMOUS_CHOICE}
        if avatar is not None and tool == "AskUserQuestion":            # P543: the channel asks in its reply instead
            return {"behavior": "deny", "message": "这条消息来自主人的通道，不能弹出提问卡，请直接在回复里问主人。 / Ask the owner in your reply instead."}
        always_ask = always_ask or task_actions is not None
        strict = always_ask and grant_info      # #428 Info: only a paired device's signed allow may answer this card
        batch = batch and not strict
        bound, own_pk = policy_registry_bound(tool, bind), require_passkey or always_ask   # own_pk: the caller's own card
        if tool == "AskUserQuestion" and self.agent and self.agent.kind == "claude" and isinstance(tool_input, dict) and avatar is None:
            return await self._ask_user_question(tool_input, gone)       # §10.7: a question card, not an approval card
        from . import handoff           # P539 (#301): reading Agent J's own handoff file never needs the owner
        if not strict and not self.stopped() and handoff.owner_side(self.agent) and (handoff.own_read(tool, tool_input, self._switch_cfg()) or handoff.own_state(tool, tool_input, self._switch_cfg())):
            own_rid = await asyncio.to_thread(handoff.record_own, self.st, getattr(self.agent, "kind", None), tool, tool_input)
            if self.stopping.is_set():
                return stopping_result()
            self.st.log("ask_done", id=own_rid, tool=tool, decision="allow", reason="own_handoff")
            return {"behavior": "allow", "updatedInput": tool_input}
        from . import background_done as bd_mod   # #450 1b: a known completion turn reads its own report (ADR-#450)
        own_t = None if strict or self.stopped() or avatar is not None or own else bd_mod.own_report(self.agent, tool, tool_input, (self.agent_cfg or {}).get("dir"))
        if own_t and (own_t == "busy" or await asyncio.to_thread(bd_mod.own_clean, own_t, (self.agent_cfg or {}).get("dir"))):
            own_rid = secrets.token_hex(16)
            await asyncio.to_thread(approvals.record, self.st, rid=own_rid, agent=getattr(self.agent, "kind", "?"), tool=tool,
                                    input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(tool, bd_mod.OWN_SHOWN),
                                    decision="allow", reason="own_report", cats=[])
            if self.stopping.is_set():
                return stopping_result()
            self.st.log("ask_done", id=own_rid, tool=tool, decision="allow", reason="own_report", via="system")
            return {"behavior": "allow", "updatedInput": tool_input}
        from . import untrusted_dispatch
        if (not strict and not always_ask and not bound and avatar is None and not own and not self.stopped()
                and await asyncio.to_thread(untrusted_dispatch.safe_command, self, tool, tool_input)):
            if self.stopping.is_set():
                return stopping_result()
            rid = secrets.token_hex(16)
            approvals.record(self.st, rid=rid, agent=getattr(self.agent, "kind", "?"), tool=tool,
                             input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(tool, "CEO data diagnosis"),
                             decision="allow", reason="ceo_data_only", cats=[])
            return {"behavior": "allow", "updatedInput": tool_input}
        policy_env = getattr(self.agent, "approval_env", None)
        requirement = ("uv" if learned else policy_registry.decision("main", bound) if bound else
                       policy_registry.main_requirement(tool, tool_input, env=policy_env, cwd=(self.agent_cfg or {}).get("dir")))
        if requirement == "deny":
            observe('unknown_action', False)
            policy_registry.log_unknown(self.st, tool, tool_input, agent=getattr(self.agent, "kind", "?"))   # P553
            return {"behavior": "deny", "message": UNKNOWN_ACTION}
        v = self._classify(tool, tool_input)
        if not bound and not learned:
            requirement = self.owner_hosts.narrow_requirement(tool, tool_input, requirement, v)
        # #510: only the host can prove this installed running service; never a model flag.
        restart_input = None
        if not strict and not always_ask and not own_pk and not bound and avatar is None and not own:
            restart_input = await asyncio.to_thread(restart_guard.prove, tool, tool_input)
        from . import peer_origin
        dispatch = False
        if tool == "Bash" and set(v.rules) <= {"cred.service"} and isinstance(tool_input, dict) and not set(tool_input) - {"command", "description", "timeout"}:
            unit, why = await asyncio.to_thread(self._dispatch_check, tool_input.get("command"))
            if unit and why == "ok" and not self._dispatch_turn():
                why = "turn"
            dispatch = why == "ok" and requirement in ("uv", "allow") and not strict and not bound and avatar is None and not own
            dispatch_boundary_invalid = bool(unit and why not in ("ok", "shape", "turn"))
            if not dispatch and (unit or set(v.rules) == {"cred.service"}):  # #572: service launches are not credential changes
                # Preserve the existing UV rule while showing the actual class.
                # #469: the shape is not 「改凭据」 — the card names its class and why (ADR-#455 F3 B)
                v = peer_origin.unproven(why if why != "ok" else "turn", card_lang(self.lang))
                self.st.log("dispatch_unproven", why=why)
        if dispatch:                            # #455: CEO dispatch whitelist — owner: no Face ID; same_host: no card (ADR-#455)
            v, requirement = danger.Verdict(), "allow"
        opaque = approval_jev.gray(v)
        if opaque and requirement == "allow":
            requirement = "hold"
        host_card = approval_level.host_card(tool, tool_input, requirement=requirement)
        if requirement in ("hold", "uv"):
            batch = False
        if requirement == "uv":
            require_passkey = True
        delegated = not own and provenance.delegated_turn(self.agent)
        unattended = provenance.unattended_turn(self.agent) if avatar is None and not own else None
        if unattended:
            delegated = True
        peer = unattended == "same_host"        # #455: never an auto path below this branch (ADR-#455)
        if peer:
            origin = origin or pick(self.lang, "来源：同机 Agent 会话 ", "Asked during: same-computer Agent session ") + str(provenance.turn_source(self.agent).get("unit"))[:80]
        if avatar is not None:
            delegated, batch = True, False
            origin = origin or avatar
        if tool == "company.write" and _company_proposal is policy_registry.COMPANY_PROPOSAL:
            delegated = True
        if delegated:
            batch = False
        kind = self.agent.kind if self.agent else "?"
        same_owner = avatar is None and not unattended and _same_owner_turn(self.agent)   # P573 (f)
        try:
            summary = (tool_input["card"] if learned else tool_input["summary"] if own else
                       tool_input["card"] if tool == owner_grant.TOOL and isinstance(tool_input, dict)
                       and isinstance(tool_input.get("card"), str) else
                       computer_use_script_summary(tool_input, self.lang) if bound else
                       action_policy.summary(tool, tool_input, lang=self.lang, workdir=(self.agent_cfg or {}).get("dir")) if opaque or tool in
                       ("agentj mail", "agentj mail send", "agentj mail notes", "agentj mail reply", "agentj calendar", "company.write", "agentj data", "agentj report script")
                       else approval_summary(tool, tool_input, v, self.lang, workdir=(self.agent_cfg or {}).get("dir")))
        except (ValueError, TypeError, RecursionError):
            return {"behavior": "deny", "message": "动作参数无效 / invalid action parameters"}
        if len(summary.encode()) > 24000:
            return {"behavior":"deny", "message":("日历内容过长，无法完整确认 / calendar content is too large to review"
                                                   if tool == "agentj calendar" else "邮件内容过长，无法完整确认 / mail is too large to review")}
        if require_passkey:
            batch = False
        if tool == "SandboxOnce" and (require_passkey or always_ask):
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
                           "it may access local files and credentials. The owner confirms on a paired remote; this launch only.")
            else:
                why = fence.REASONS.get(tool_input.get("reason"), "隔离检查失败")
                summary = ("沙箱不可用，Agent J 尚未启动：" + why + "。\n修复隔离工具后重试；"
                           "需要安装或管理员权限时，可在手机上批准。\n本次不隔离运行：批准后会在沙箱外运行，可能访问本机文件和凭据。仅本次启动有效，需主人在已配对的遥控器上确认。")
            summary += "\n" + approval_summary(tool, tool_input, v, self.lang, workdir=(self.agent_cfg or {}).get("dir"))
        if risk_scope:
            summary += CARD_TEXT[card_lang(self.lang)]["risk_scope"]
        if bound and not delegated and ttl is None and approval_level.level(self.preferences, self.st) != "safe":
            summary += "\n" + CU_SCRIPT_GRANT_NOTE[card_lang(self.lang)]   # P539: the 免问 box would free arbitrary scripts
        if origin:
            summary += "\n" + str(origin)[:120]     # last line: the first stays the command (notices, push)
        if len(summary.encode()) > 24000:
            return {"behavior": "deny", "message": ("日历内容过长，无法完整确认 / calendar content is too large to review"
                                                    if tool == "agentj calendar" else "动作内容过长，无法完整确认 / action is too large to review")}
        plan_scope = None
        if task_actions is not None:
            try:
                if not isinstance(task_actions, list) or not 1 <= len(task_actions) <= TASK_GRANT_MAX:
                    raise ValueError("plan size")
                actions, lines, object_bindings = [], [], {}
                for step in task_actions:
                    if not isinstance(step, dict) or set(step) != {"tool", "input"} or not isinstance(step["input"], dict):
                        raise ValueError("plan shape")
                    name, inp = step["tool"], step["input"]
                    if not policy_registry.tool_action(name) or name in approval_level.HARNESS_KEEPS or name in goal_mod.SOFT_EXEMPT:
                        raise ValueError("own card")
                    if name == "Bash":
                        command = inp.get("command")
                        if not isinstance(command, str) or any(x in command for x in ("$", "`", "*", "?", "[", "{", "\n")):
                            raise ValueError("dynamic plan operand")
                        parsed = danger.lex(command)
                        forbidden = set(danger._SHELLS) | {"python", "python3", "node", "ruby", "perl", "php", "eval", "source", ".", "xargs", "find"}
                        if not parsed or any(c.subst or any(p.name in forbidden for p in danger.unwrap(c)) for c in parsed):
                            raise ValueError("unbounded plan program")
                    verdict = self._classify(name, inp)
                    if (set(verdict.cats) & {"credentials", "opaque"}
                            or secret_display_route.reveals_to_session(name, inp, (self.agent_cfg or {}).get("dir"), policy_env)):
                        raise ValueError("individual UV required")
                    from . import bounded_plan
                    binding = bounded_plan.shell_seal(name, inp, (self.agent_cfg or {}).get("dir"), self.__dict__.setdefault("_plan_seal_key", secrets.token_bytes(32)))
                    object_bindings[name + ":" + approvals.input_digest(inp)] = binding
                    actions.append({"tool": name, "input_sha256": approvals.input_digest(inp),
                                    "class": self._task_class(name, verdict)})
                    lines.append(approval_summary(name, inp, verdict, self.lang, workdir=(self.agent_cfg or {}).get("dir")))
                    require_passkey |= self._task_uv(name, inp, verdict, policy_env)
                if not any(x["tool"] == tool and x["input_sha256"] == approvals.input_digest(tool_input) for x in actions):
                    raise ValueError("first action absent")
                if any(len(line) > 1000 for line in lines):
                    raise ValueError("plan display too large")
                if len({(x["tool"], x["input_sha256"]) for x in actions}) != len(actions):
                    raise ValueError("duplicate plan step")
                task = self._task_id()
                plan_scope = json.dumps({"task": task, "actions": actions, "target": task_target, "uv": bool(require_passkey),
                                         "expires": int(time.time()) + TASK_GRANT_SECS,
                                         "display_steps": [approval_review.safe_text(line, 1000) for line in lines], "object_bindings": object_bindings}, sort_keys=True, separators=(",", ":"))
                summary += "\n具体动作 / Exact actions:\n" + "\n".join(str(i + 1) + ". " + line for i, line in enumerate(lines))
                if task_target:
                    summary += "\n接收设备 / Receiver: " + str(task_target.get("receiver", ""))
                if len(summary.encode()) + len(plan_scope.encode()) > 14000:
                    raise ValueError("plan too large")
                batch = False
            except (ValueError, TypeError, KeyError, RecursionError):
                return {"behavior": "deny", "message": "任务动作清单无效 / invalid task action plan"}
        digest, isha = approvals.shown_digest(tool, summary), approvals.input_digest(tool_input)
        rid = secrets.token_hex(16)
        workdir = (self.agent_cfg or {}).get("dir")
        lv = approval_level.level(self.preferences, self.st)   # P336: safe | standard (as before) | full (signed only)
        if lv != "full" and not self.__dict__.get("_unsigned_full_told") and approval_level.unsigned_full(self.preferences, self.st):
            self._unsigned_full_told = True    # the file says full, no live signed switch backs it: standard, told once
            self.st.log("approval_level_unsigned")
            self.agent_notice(approval_level.unsigned_text(self.lang, preferences.get(self.preferences, approval_level.KEY)))
        sc = None if v.danger or not batch or lv == "safe" else danger.batch_scope(tool, tool_input, workdir, self.danger_extra)
        base = dict(rid=rid, agent=kind, tool=tool, input_sha256=isha, shown_sha256=digest, cats=v.cats)
        if background == "order" and origin:
            base["order_origin"] = str(origin)[:600]
        # #562: fixed display labels only; this projection never participates in admission.
        try:
            from . import approval_audit
            audits = self.__dict__.setdefault("_approval_audits", {})
            audits[rid] = approval_audit.presentation({}, tool, tool_input, lang=self.lang)
            while len(audits) > 128:
                audits.pop(next(iter(audits)))
        except Exception:  # noqa: BLE001 — presentation never changes permission
            pass
        if self.stopped():                # stopped: nothing is asked, nothing runs
            observe('estop', False)
            approvals.record(self.st, **base, decision="deny", reason="estop")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="estop")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="estop", cats=v.cats, summary=summary)
            return {"behavior": "deny", "message": "已急停（全部停下）：什么都不执行，等人恢复。", **({"reason": "estop"} if grant_info else {})}
        if self.stopping.is_set():
            observe('serve_stop', False)
            approvals.record(self.st, **base, decision="deny", reason="serve_stop")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="serve_stop")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="serve_stop", cats=v.cats, summary=summary)
            self.emit("ask_done", id=rid, result="deny", reason="serve_stop")
            return {"behavior": "deny", "message": "agentj serve 已停止：默认拒绝。", **({"reason": "serve_stop"} if grant_info else {})}
        if provenance.untrusted_turn(self.agent) and not delegated and not own:
            observe('group_source', False)
            recovery = getattr(getattr(self, "background_done", None), "recovery", None)
            if recovery is not None:
                await asyncio.to_thread(recovery.refusal, tool, tool_input, "group_source")
            approvals.record(self.st, **base, decision="deny", reason="group_source")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="group_source")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="group_source", cats=v.cats, summary=summary)
            self.emit("ask_done", id=rid, result="deny", reason="group_source")
            return {"behavior": "deny", "message": provenance.refusal_text(self.agent) + "\n" + untrusted_dispatch.HINT, **({"reason": "group_source"} if grant_info else {})}
        # Owner 11-07: ordinary maintenance is not a credential/payment/publication
        # action. Qualify effects only for the same real owner ingress and task.
        from . import native_scratch
        scratch_context = native_scratch.context(self)
        native_bg_scratch = scratch_context is not None and scratch_context.background is not None
        if (not strict and not always_ask and not bound and not learned and avatar is None
                and not own and (not delegated or native_bg_scratch) and not caller_uv and lv != "safe"
                and not provenance.owner_grant_required(self.agent)):
            from . import ordinary_maintenance, approval_special
            maintenance_source, maintenance_task = approval_special._current_context(self)
            source_owner = (maintenance_source and maintenance_source.get("verified") is True
                            and not maintenance_source.get("suspicious")
                            and (provenance.is_owner(provenance.turn_source(self.agent))
                                 or scratch_context is not None and scratch_context.background is not None))
            if source_owner:
                actor = self.agent
                sender = getattr(actor, "cur_send", None)
                device = getattr(sender, "device", None)
                key = self.st.sign_key(device) if device else None
                state, epoch = self.st, self.hist.epoch
                maintenance_root, pairing_epoch = self.st.root, getattr(self, "_pairing_epoch", None)
                ordinary = await asyncio.to_thread(ordinary_maintenance.qualify, tool, tool_input,
                    cwd=workdir, state_root=self.st.root, _native_scratch=scratch_context)
                if ordinary:
                    effect_live = await asyncio.to_thread(ordinary_maintenance.still_valid, ordinary)
                    current_source, current_task = approval_special._current_context(self)
                    if (not effect_live or self.stopped() or self.stopping.is_set() or self.st is not state or self.hist.epoch != epoch
                            or self.st.root != maintenance_root or getattr(self, "_pairing_epoch", None) != pairing_epoch
                            or self.agent is not actor or getattr(actor, "cur_send", None) is not sender
                            or getattr(actor, "halting", False) or getattr(sender, "state", None) == "cancelled"
                            or current_source != maintenance_source or current_task != maintenance_task
                            or approval_level.level(self.preferences, self.st) != lv
                            or (self.agent_cfg or {}).get("dir") != workdir
                            or policy_registry.main_requirement(tool, tool_input,
                                env=getattr(actor, "approval_env", None), cwd=workdir) == "deny"
                            or device and (not key or self.st.sign_key(device) != key)):
                        return {"behavior":"deny", "message":"来源已变化，重新核验 / Source changed"}
                    approvals.record(self.st, **base, decision="allow", reason="ordinary_maintenance", auto="owner")
                    return {"behavior":"allow", "updatedInput":tool_input}
        if (restart_input is not None and set(v.cats) == {"service"} and set(v.rules) == {"service.restart"}
                and not provenance.owner_grant_required(self.agent)):
            return await self._restart_request(tool, tool_input, restart_input, v, base, summary, gone, lv,
                                               full=not delegated or bool(unattended))
        if dispatch and unattended is None and avatar is None and not own:
            approvals.record(self.st, **base, decision="allow", reason="dispatch")
            await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="dispatch")
            self.activity("auto", id=rid, tool=tool, summary=summary, by="dispatch", task=self.task_label)
            self.emit("auto", id=rid, tool=tool, grant=None)
            return {"behavior": "allow", "updatedInput": tool_input}
        if peer and lv != "harness" and not _peer_floor(self, tool, tool_input, v, requirement, opaque, host_card, require_passkey or own_pk or always_ask, bound, ttl, lv, dispatch):
            approvals.record(self.st, **base, decision="allow", reason="same_host")
            if dispatch:
                await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="same_host", via="same_host")
            self.activity("auto", id=rid, tool=tool, summary=summary, by="same_host", cats=v.cats, task=self.task_label)
            self.emit("auto", id=rid, tool=tool, grant=None)
            return {"behavior": "allow", "updatedInput": tool_input}
        # hotfix #455: a host system notice turn (a background completion, a heartbeat, a compaction recovery) is the
        # same-computer tier, like a verified same_host session: the same allowlist runs without a card; any step with a
        # dangerous row (delete / send / publish / spend / price / secrets) always raises the owner's card, parked so the
        # rest goes on. A loop wake-up keeps P570c. External sources never reach here (unattended is None for them).
        sm_card = False
        if (unattended == "system" and avatar is None and not own
                and not strict and not always_ask and ttl is None and not bound and task_actions is None):
            # S2: the no-card path only when no outside text entered the session since the owner spoke
            # at 「减少弹窗」 the level's own path allows these as before (label level_harness); here only the default levels
            if lv != "harness" and not _peer_floor(self, tool, tool_input, v, requirement, opaque, host_card, require_passkey or own_pk, bound, ttl, lv, dispatch):
                approvals.record(self.st, **base, decision="allow", reason="same_machine")
                if dispatch:
                    await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="same_machine", via="system")
                self.activity("auto", id=rid, tool=tool, summary=summary, by="same_machine", cats=v.cats, task=self.task_label)
                self.emit("auto", id=rid, tool=tool, grant=None)
                return {"behavior": "allow", "updatedInput": tool_input}
            sm_card = lv != "harness" and await asyncio.to_thread(_same_machine_card, self, tool, tool_input, v, requirement, dispatch, policy_env, known_recipients)
            if sm_card:
                batch = False
        if same_owner and self.peers is not None:
            sender = getattr(self.agent, "cur_send", None)
            owner = self.peers.same_owner
            data = getattr(sender, "data", {})
            if owner.explicit_plan(data) and (not owner.valid_plan(getattr(sender, "fid", None), data)
                                               or getattr(sender, "turn", None) != self.cur_turn):
                from .same_owner import PLAN_ROOT_NOTE
                return {"behavior": "deny", "message": PLAN_ROOT_NOTE["zh" if self.lang == "zh" else "en"]}
        og_turn = provenance.owner_grants(self.agent) if avatar is None and self.peers is not None else None
        if not always_ask and avatar is None and background is None and (og_turn or (self.peers is not None and self.peers.xfer.task_scope())) and tool != owner_grant.TOOL and tool not in goal_mod.SOFT_EXEMPT:
            # P574 (#389): a same-owner task under the grant the owner signed on the remote that started it — the step it
            # lists runs here with no card (counted, attack / opaque / credentials never covered); the phone gets one line
            try:
                if og_turn:
                    hit = await asyncio.to_thread(self.peers.xfer.grant_tier, og_turn[0], og_turn[1], tool, tool_input, v,
                                                  (self.agent_cfg or {}).get("dir"), self._classify)
                else:
                    hit = await asyncio.to_thread(self.peers.xfer.origin_tier, tool, tool_input, v,
                                                  (self.agent_cfg or {}).get("dir"), self._classify)
            except Exception:  # noqa: BLE001 — a receiving OTG task fails closed below
                hit = None
            if self.stopped():
                observe('estop', False)
                approvals.record(self.st, **base, decision="deny", reason="estop")
                self.emit("ask_done", id=rid, result="deny", reason="estop")
                return {"behavior":"deny", "message":"已急停 / Stop everything is on", **({"reason": "estop"} if grant_info else {})}
            if hit:
                approvals.record(self.st, **base, decision="allow", reason="owner_grant", grant=hit["tid"], auto="owner_grant")
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="owner_grant", steps=hit["steps"])
                self.activity("auto", id=rid, tool=tool, summary=summary, by="owner_grant", cats=v.cats, task=self.task_label,
                              origin=hit["origin"])
                self.emit("auto", id=rid, tool=tool, grant=hit["tid"])
                line = (("按「" + hit["origin"] + "」签的授权单：") if card_lang(self.lang) == "zh" else
                        ("Under the grant signed on “" + hit["origin"] + "”: ")) + summary.split("\n", 1)[0][:180]
                return {"behavior": "allow", "updatedInput": tool_input}
        if (avatar is None and provenance.owner_grant_required(self.agent)
                and (v.danger or require_passkey or requirement in ("hold", "uv"))):
            # A delegated OTG task returns to its origin for extra scope; the receiving host never opens another card.
            observe('owner_grant_scope', False)
            approvals.record(self.st, **base, decision="deny", reason="owner_grant_scope")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="owner_grant_scope")
            self.emit("ask_done", id=rid, result="deny", reason="owner_grant_scope")
            return {"behavior":"deny", "message":"授权单未覆盖这一步或已失效，请回发起主机补授权。 / The grant does not cover this step or is no longer valid; ask the originating host for added scope.", **({"reason": "owner_grant_scope"} if grant_info else {})}
        # #520: Full access is one standing authorisation, also in native/system/task turns.
        # Source admission and exact delegated grant boundaries above remain authoritative.
        full_source = (own or not provenance.untrusted_turn(self.agent)
                       or provenance.unattended_turn(self.agent) in ("system", "same_host", "scheduled_task")
                       or bool(provenance.owner_channel_turn(self.agent)))
        if lv == "harness" and full_source and not learned and not provenance.owner_grant_required(self.agent):
            def full_boundary():
                return (id(self.agent), id(getattr(self.agent, "cur_send", None)), self.cur_turn,
                        provenance.turn_source(self.agent), provenance._REQUEST.get(),
                        provenance.untrusted_turn(self.agent), provenance.unattended_turn(self.agent))
            boundary = full_boundary()
            keep = await asyncio.to_thread(approval_level.full_access_keep, tool, tool_input, v,
                                           env=policy_env, workdir=workdir, requirement=requirement,
                                           require_passkey=require_passkey)
            if self.stopped():
                observe('estop', False)
                approvals.record(self.st, **base, decision="deny", reason="estop")
                return {"behavior": "deny", "message": "已急停 / Stop everything is on"}
            if boundary != full_boundary() or provenance.owner_grant_required(self.agent):
                observe('group_source', False)
                approvals.record(self.st, **base, decision="deny", reason="group_source")
                return {"behavior": "deny", "message": "来源已变化，重新核验后继续 / Source changed; revalidate before continuing"}
            if self.stopping.is_set():
                return stopping_result()
            if keep is None:
                if dispatch:
                    await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
                    if self.stopping.is_set():
                        return stopping_result()
                    if self.stopped():
                        approvals.record(self.st, **base, decision="deny", reason="estop")
                        return {"behavior": "deny", "message": "已急停 / Stop everything is on"}
                    if boundary != full_boundary() or provenance.owner_grant_required(self.agent):
                        approvals.record(self.st, **base, decision="deny", reason="group_source")
                        return {"behavior": "deny", "message": "来源已变化，重新核验后继续 / Source changed; revalidate before continuing"}
                grant = approval_level.level_grant(self.st)
                if not grant or approval_level.level(self.preferences, self.st) != "harness":
                    return {"behavior": "deny", "message": "授权已撤销 / Authorisation revoked"}
                approvals.record(self.st, **base, decision="allow", reason="level_harness", grant=grant, auto="level")
                observe('level_harness', False)
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="level_harness",
                            **({"via": "avatar"} if avatar is not None else {"via": unattended} if unattended else {}))
                self.activity("auto", id=rid, tool=tool, summary=summary, by="harness", cats=v.cats, task=self.task_label)
                self.emit("auto", id=rid, tool=tool, grant=grant)
                line = summary.split("\n", 1)[0][:200]
                answer = {"behavior": "allow", "updatedInput": tool_input}
                if grant_info:
                    signer = approval_level._live(self.st, approval_level.KEY)
                    if signer is None:
                        return {"behavior": "deny", "message": "授权已撤销 / Authorisation revoked"}
                    answer["grant_info"] = {"rid": rid, "device": signer["device"],
                                             "sign_pub": self.st.sign_key(signer["device"]), "inherited": True, "parent_grant": grant}
                return answer
            # Downloaded/decoded unknown code retains its ordinary card even
            # under Full access; it is not itself a credential or other UV effect.
            if keep == "injection":
                batch, own_pk, strict, always_ask = False, True, True, True
            else:
                # A true UV effect cannot fall through into legacy no-card routes.
                require_passkey, batch, own_pk, strict = True, False, True, True
        if policy_registry.dispatch(tool) and not strict:
            approvals.record(self.st, **base, decision="allow", reason="dispatch")
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="dispatch")
            self.activity("auto", id=rid, tool=tool, summary=summary, by="dispatch", cats=v.cats, task=self.task_label)
            return {"behavior": "allow", "updatedInput": tool_input}
        prow, scope_park = "", False        # 0269-w3-policy E1 / E2: the default policy table (ADR-0269-w3-policy)
        chain = _chain_turn(self.agent) if unattended == "system" and avatar is None and not own else None
        if (not strict and not always_ask and not own_pk and avatar is None and not own and not peer and ttl is None and not bound
                and task_actions is None and self.task_label is None
                and (chain or (not delegated and not same_owner and not provenance.untrusted_turn(self.agent)))
                and self._approvers() and approval_policy.in_force(self.preferences, self.st)):   # no paired phone: deny as before
            try:   # worker thread: reversibility may ask git; the budget file is read there too
                ptier, prow = await asyncio.to_thread(lambda: approval_policy.decide(
                    tool, tool_input, v, requirement=requirement, dispatch=dispatch, workdir=workdir, env=policy_env,
                    classify=self._classify, budget=budget_mod.state(self.st), known_recipients=known_recipients, chain=bool(chain)))
            except Exception:  # noqa: BLE001 — cannot tell → the strictest step
                ptier, prow = approval_policy.FACE, "self_protect"
            if self.stopped():
                observe('estop', False)
                approvals.record(self.st, **base, decision="deny", reason="estop")
                return {"behavior": "deny", "message": "已急停 / Stop everything is on", **({"reason": "estop"} if grant_info else {})}
            if ptier == approval_policy.NONE:
                observe('owner_scope', False)
                approvals.record(self.st, **base, decision="allow", reason="owner_scope", policy=prow)
                if dispatch:
                    await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="owner_scope", policy=prow,
                            **({"via": "chain"} if chain else {}))
                self.activity("auto", id=rid, tool=tool, summary=summary, by="owner_scope", cats=v.cats, task=self.task_label)
                self.emit("auto", id=rid, tool=tool, grant=None)
                return {"behavior": "allow", "updatedInput": tool_input}
            if ptier == approval_policy.TAP:
                requirement, require_passkey, batch = "hold", False, False
            elif ptier == approval_policy.FACE:
                require_passkey, batch = True, False
            scope_park = ptier is not None
            if chain and ptier is not None:     # E2: the card says whose words authorised the chain (signed with the summary)
                quote = pick(self.lang, "主人原话：「", "The owner's words: “") + str((chain.get("words") or [""])[0])[:80] + \
                    pick(self.lang, "」", "”") + " · " + pick(self.lang, "任务链 ", "chain ") + str((chain.get("ids") or ["?"])[0])[:8]
                summary += "\n" + quote
                digest = approvals.shown_digest(tool, summary)
                base["shown_sha256"] = digest
        if lv != "safe" and (not delegated or (unattended and lv == "harness")) and avatar is None and ttl is None and not own_pk and (not peer or lv == "harness") \
                and not sm_card:     # hotfix #455: a same-computer card stays a card
            from . import background as bgmod   # P552 (#325 ②): background work, decided from the real call — never the model's words
            bgk = (("headless:" + h) if (h := bgmod.headless(tool, tool_input)) else
                   background if background in ("order", "task") else None)
            if bgk:
                try:
                    keep = confirm_tier.harness_keeps(tool, tool_input, env=policy_env)
                    floor = approval_level.harness_floor(tool, tool_input, v)
                except Exception:  # noqa: BLE001 — cannot tell → the strictest step
                    keep = "delete"
                if keep:
                    require_passkey, batch = True, False    # one of the four ③ kinds: Face ID, whoever runs it
                elif requirement == "uv" and lv != "harness":
                    pass        # review B2: outside 「减少弹窗」 the level's own ③ floor stays (P573: sudo / opaque are a button now)
                elif tool not in approval_level.HARNESS_KEEPS and tool not in policy_registry.HOST_TOOLS and not floor:
                    why = "dispatch_headless" if bgk.startswith("headless:") else "background"
                    approvals.record(self.st, **base, decision="allow", reason=why)
                    self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason=why, status=bgk,
                                **({"via": unattended} if unattended else {}))
                    self.activity("auto", id=rid, tool=tool, summary=summary, by=why, cats=v.cats, task=self.task_label)
                    return {"behavior": "allow", "updatedInput": tool_input}
        from . import preauth
        pa_action = bound or policy_registry.tool_action(tool)
        pa_bind = preauth.binding(pa_action, tool, bind) if pa_action in preauth.GRANTABLE else None
        pa_action = pa_action if pa_bind is not None and not delegated and ttl is None and lv != "safe" and not strict else None
        if pa_action and (pg := await asyncio.to_thread(preauth.live, self.st, pa_action, pa_bind)) is not None:
            return self._preauth_use(pg, base, tool, tool_input, summary, v)   # P539: 「<remote> 批准过：Computer Use 免问」
        harness_card = False
        if same_owner and not own_pk and lv != "safe" and self.peers is not None:
            proof = self.peers.same_owner.authorized(getattr(self.agent, "cur_send", None), tool, tool_input,
                                                      self._task_class(tool, v))
            if proof:
                need_uv = require_passkey or self._task_uv(tool, tool_input, v, policy_env)
                if need_uv and not json.loads(proof["task_scope"])["uv"]:
                    message = "接收设备要求 Face ID；请在发起设备重新授权计划。 / This device requires Face ID; re-authorize the plan on the originating device."
                    approvals.record(self.st, **base, decision="deny", reason="task_uv", grant=proof["id"],
                                     task_scope=proof["task_scope"], task_uv=True)
                    return {"behavior": "deny", "message": message}
                approvals.record(self.st, **base, decision="allow", reason="same_owner", grant=proof["id"], task_uv=bool(need_uv),
                                 task_scope=proof["task_scope"], peer_proof=proof, task_id=str(self.cur_turn),
                                 task_action={"tool": tool, "input_sha256": isha, "class": self._task_class(tool, v)})
                self.activity("auto", id=rid, tool=tool, summary=summary, by="same_owner", cats=v.cats, task=self.task_label)
                return {"behavior": "allow", "updatedInput": tool_input}
        if same_owner and tool == "AgentJSameOwnerAction" and lv != "safe" and not own_pk:
            approvals.record(self.st, rid=rid, agent=kind, tool=tool, input_sha256=isha, shown_sha256=digest, cats=v.cats,
                             decision="allow", reason="same_owner")
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="same_owner")
            self.activity("auto", id=rid, tool=tool, summary=summary, by="same_owner", cats=v.cats, task=self.task_label)
            return {"behavior": "allow", "updatedInput": tool_input}
        if lv == "harness" and (not delegated or avatar is not None or unattended) and ttl is None and not own_pk and (not peer or lv == "harness") \
                and not sm_card and tool not in approval_level.HARNESS_KEEPS and not (unattended and tool == "AgentJSameOwnerAction"):
            try:   # no folder: the Agent may have cd'd, and a `cwd` field is the model's — relative paths are not scratch
                keep = confirm_tier.harness_keeps(tool, tool_input, env=policy_env) or approval_level.harness_floor(
                    tool, tool_input, v)    # P556: + an attack on Agent J itself / fetch-and-run / an inline key going out
            except Exception:  # noqa: BLE001 — cannot tell → the strictest step
                keep = "delete"
            if keep and keep not in confirm_tier.HARNESS_KINDS and keep not in approval_level.FLOOR_FACE:
                harness_card, batch = True, False    # P573 (e): a fetch run in the same call — one button, still asked
            elif keep:
                require_passkey, batch = True, False
            else:
                grant = approval_level.level_grant(self.st)
                approvals.record(self.st, **base, decision="allow", reason="level_harness",
                                 **({"grant": grant, "auto": "level"} if grant else {}))
                if dispatch:
                    await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="level_harness",
                            **({"via": "avatar"} if avatar is not None else {"via": unattended} if unattended else {}))
                self.activity("auto", id=rid, tool=tool, summary=summary, by="level_harness", cats=v.cats, task=self.task_label)
                self.emit("auto", id=rid, tool=tool, grant=None)
                line = summary.split("\n", 1)[0][:200]
                return {"behavior": "allow", "updatedInput": tool_input}
        pkey = self._park_key(tool, isha)       # P336 N5: who is asking, fixed before any await
        if not always_ask and (not delegated or unattended) and (not require_passkey or unattended or scope_park) and ttl is None:
            ok = self._parked_take(pkey, uv=require_passkey)
            if ok is not None:                # P336 (#36): the owner allowed this exact step on its parked card
                approvals.record(self.st, **base, decision="allow", reason="parked", device=ok["device"], grant=ok["rid"],
                                 auto="parked")
                await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
                self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="parked", device=ok["device"])
                self.activity("auto", id=rid, tool=tool, summary=summary, by="parked", task=self.task_label)
                return {"behavior": "allow", "updatedInput": tool_input}
        from .capability import goal_cli, host_mediated, staging_write
        main_root = (self.agent_cfg or {}).get("dir") if self.task_label is None else None
        if batch and ((not v.danger and (host_mediated(tool, tool_input) or staging_write(main_root, tool, tool_input)))
                      or goal_cli(tool, tool_input)):     # P339: `agentj goal grant --match scp` is data, not cred.sql
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
        g = None if delegated or not batch or v.danger or require_passkey else self._grant_for(tool, sc[:2] if sc else None)
        if g is not None:                 # inside a live batch approval: allowed at once, logged, one quiet line on the phone
            g.left -= 1
            approvals.record(self.st, **base, decision="allow", reason="batch", device=g.device, grant=g.rid)
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="batch", device=g.device)
            self.activity("auto", id=rid, tool=tool, summary=summary, by=self._dname(g.device), task=self.task_label)
            self.emit("auto", id=rid, tool=tool, grant=g.rid)
            line = summary.split("\n", 1)[0][:200]
            if g.left <= 0:
                self.end_grant(g.rid, "limit")
            else:
                self._post(self._send_ready, lambda s: self._grant_msg(g))
            return {"behavior": "allow", "updatedInput": tool_input}
        tgk = self._task_grant_key(tool, v, delegated=delegated, same_owner=same_owner, avatar=avatar, ttl=ttl,
                                   own_pk=caller_uv and task_actions is None, lv=lv)
        tg = self._task_grant_use(tgk, tool, isha, require_passkey or self._task_uv(tool, tool_input, v, policy_env), tool_input) if tgk is not None else None
        if tg is not None:                # P573 (#389): the owner already signed this kind of step in this task
            if not await self._task_operation_reserve(tg, tool, isha):
                return {"behavior":"deny", "message":"Safety ledger or task authority is unavailable; no operation executed."}
            approvals.record(self.st, **base, decision="allow", reason="task_grant", device=tg["device"], grant=tg["rid"],
                             auto="task", task_scope=tg["scope"], task_uv=bool(require_passkey or self._task_uv(tool, tool_input, v, policy_env)), task_id=self._task_id(), task_action=self._task_action(tool, isha, tgk))
            self.st.log("ask_done", id=rid, tool=tool, decision="allow", reason="task_grant", device=tg["device"])
            self.activity("auto", id=rid, tool=tool, summary=summary, by="task_grant", cats=v.cats, task=self.task_label)
            self.emit("auto", id=rid, tool=tool, grant=tg["rid"])
            line = summary.split("\n", 1)[0][:200]
            return {"behavior": "allow", "updatedInput": tool_input}
        gt, gwhy, gd = None, None, None
        if not strict and not delegated and not harness_card and requirement not in ("hold", "uv") and not require_passkey and self.task_label is None and tool not in goal_mod.SOFT_EXEMPT:
            gd = self.goals.current()
            if gd is not None:                  # P158: the host sorts the request; the Agent's own words decide nothing
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
            return {"behavior": "allow", "updatedInput": tool_input}
        if gt == "reversible" and (lv == "safe" or not preferences.get(self.preferences, "approval.goal_auto", True)):
            gt, gwhy = "hold", gwhy             # the owner (enterprise / the safe level) switched the countdown off: a card
        free = None                             # P336: full level / a goal without cards — everything but the red lines
        if not strict and not delegated and not harness_card and not require_passkey and ttl is None and gt != "attack" and not host_card and self._approvers():
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
            return {"behavior": "allow", "updatedInput": tool_input}
        if avatar is None and provenance.owner_grant_required(self.agent):
            observe('owner_grant_scope', False)
            approvals.record(self.st, **base, decision="deny", reason="owner_grant_scope")
            self.emit("ask_done", id=rid, result="deny", reason="owner_grant_scope")
            return {"behavior":"deny", "message":"请回发起主机补授权。 / Return to the originating host for added scope.", **({"reason": "owner_grant_scope"} if grant_info else {})}
        if not self._approvers():
            observe('no_device', False)
            approvals.record(self.st, **base, decision="deny", reason="no_device")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="no_device")
            self.activity("decision", id=rid, tool=tool, result="deny", reason="no_device", cats=v.cats, summary=summary)
            self.emit("ask_done", id=rid, result="deny", reason="no_device")
            return {"behavior": "deny", "message": "没有能批准的已配对手机：默认拒绝。先用 agentj pair 配对一台手机。", **({"reason": "no_device"} if grant_info else {})}
        parkable = ttl is None and gt != "reversible" and (
            (not delegated and not require_passkey and not host_card) or
            (bool(unattended) and (tool == "AgentJSameOwnerAction" or tool not in approval_level.HOST_CARDS + approval_level.HARNESS_KEEPS))
            or scope_park)      # 0269-w3-policy E1: an owner-scoped step that needs a card waits; the rest of the task goes on
        prev = self._parked_find(pkey) if parkable else None
        if prev is not None:                    # P336 (#36): the same step's card is already waiting on the phone
            return await self._parked_rewait(prev, base, gone, pkey, _receipt=_receipt)
        if sum(1 for a in self.asks.values() if not a.fut.done()) >= MAX_ASKS:
            observe('too_many', False)
            approvals.record(self.st, **base, decision="deny", reason="too_many")
            return {"behavior": "deny", "message": "同时等待批准的请求太多：默认拒绝。", **({"reason": "too_many"} if grant_info else {})}
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
                only=only, scope=sc[:2] if sc else None, scope_text=sc[2] if sc else None, task=self.task_label, require_passkey=require_passkey,
                goal={"reversible": "auto", "hold": "hold", "attack": "attack"}.get(gt or "", ""), goal_why=str(gwhy or ""),
                goal_who=g_who, goal_what=g_what, goal_add=g_add,
                allow_offer=[g_add["text"]] if g_add else [str(x) for x in allow_offer or []][:10],
                reason=action_policy.reason(summary), host_card=_host_card is True,
                settings_session=_settings_session if _host_card is True and tool == "AuthRescue" else None)
        a.order_origin = [str(origin)[:600]] if background == "order" and origin else []
        a.tg_key = tgk if not gt else None
        a.task_scope = plan_scope if a.tg_key else None
        a.prow = prow or approval_policy.card_row(tool, v, requirement, require_passkey, tool_input)
        if pa_action and not g_add and require_passkey:   # P539: 「以后这台遥控器上 Computer Use 免问」 (signed pick, needs UV)
            a.allow_offer.append(preauth.offer(pa_action))
        a.bounded_plan = _bounded_context
        if _bounded_context is not None:
            _bounded_context.rid = a.rid
        a.tg_plan_only = bool(_plan_only)
        a.secret_display_route = secret_display_route.reveals_to_session(tool, tool_input, (self.agent_cfg or {}).get("dir"), policy_env)
        source_before_bind = approval_review.snapshot(self)
        approval_review.bind(self, a, origin=source_before_bind)
        # Same source rule as _answer: never publish a card whose allow is disabled.
        source = a.envelope["source"]
        if source.get("verified") is not True or source.get("suspicious") is True:
            observe('group_source', False)
            approvals.record(self.st, **base, decision="deny", reason="group_source")
            self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="group_source")
            self.emit("ask_done", id=rid, result="deny", reason="group_source")
            return {"behavior": "deny", "message": provenance.refusal_text(self.agent), **({"reason": "group_source"} if grant_info else {})}
        self._interrupt("cards", face=require_passkey, chain=chain)
        a.review_request_source = approval_review.digest(source_before_bind)
        a.review_live_authority = self._approval_live_authority()
        if _bounded_context is None:
            from .bounded_plan import OperationOnce
            ledger_path = self.st.root / "approval-operation-once.sqlite3"
            ledger = self.__dict__.get("_approval_operation_once")
            if ledger is None and ledger_path.exists():
                ledger = self._approval_operation_once = OperationOnce(ledger_path)
            if ledger is not None:
                try:
                    consumed = await asyncio.to_thread(ledger.consumed, a.operation_once_key)
                except (ValueError, OSError):
                    return {"behavior":"deny", "message":"Safety ledger is unavailable; no operation executed."}
                if consumed:
                    return {"behavior":"deny", "message":"This exact task operation is already consumed or terminal; do not retry."}
                if self.stopped() or self.agent is not a.review_agent or approval_review.snapshot(self) != source_before_bind:
                    return {"behavior":"deny", "message":"Task authority changed during safety metadata verification."}
        self.asks[rid] = a
        self.activity("ask", id=rid, tool=tool, cats=v.cats, why=v.why or None, why_en=v.why_en or None, cred=v.cred or None,
                      summary=summary, task=self.task_label)
        self.st.log("ask", id=rid, tool=tool, agent=kind, kind="danger" if v.danger or require_passkey else "low",
                    tier="uv" if require_passkey else "hold" if requirement == "hold" else "card")
        self.emit("ask", id=rid, tool=tool, summary=summary)
        self._post(lambda fn: self._send_ready(lambda s: fn(s) if not a.only or a.only == s.device else None), lambda s: self._ask_msg(a, s))   # same queue as the Agent's text: order is kept
        self.status_changed()
        self.push_notify("ask")
        offline = not self._online_approvers()
        if offline and gt != "reversible":
            self._remind_offline()               # P336 (#36): push above + one content-free Telegram / e-mail line
        try:
            waiters = {a.fut, a.review_pause} | ({gone} if gone else set())
            if offline and parkable:
                done, _ = await asyncio.wait(waiters, timeout=max(0.0, min(a.deadline - time.monotonic(),
                                                                          approval_level.PARK_GRACE)),
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done and not self._online_approvers() and not self.stopped() and not self.stopping.is_set():
                    observe('offline')
                    return self._park(a, base, summary, pkey, unattended)   # the card waits on; the Agent does the independent work
            done, _ = await asyncio.wait(waiters, timeout=max(0.0, a.deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if (not done and scope_park and parkable and not self.stopped() and not self.stopping.is_set()
                    and not (a.goal == "auto")):
                observe('unanswered')            # E1: no 120 s timeout-as-refusal for an owner-scoped step — it waits
                return self._park(a, base, summary, pkey, unattended, why="unanswered")
            if a.review_pause in done and not a.fut.done():
                return self._park(a, base, summary, pkey, unattended, why="question")
            if a.fut in done:
                decision, did, sk, sig = a.fut.result()
                reason = "device" if did else ("task_grant" if a.tg_by else "estop" if self.stopped() else "serve_stop")
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
        approval_review.finished(self, a, decision, reason)
        gd = self.goals.current() if a.goal else None
        picks = list(a.allow_add) if decision == "allow" and reason == "device" else []
        if getattr(a, "answer_v", None) == 2:
            base = {**base, "shown_sha256":a.answer_digest, "approval_v":2}
        approvals.record(self.st, **base, decision=decision, reason=reason,
                         device=did if reason != "task_grant" else getattr(a, "tg_dev", None), sign_pub=sk, sig=sig,
                         scope=a.scope_text if decision == "allow_batch" else None,
                         task_scope=a.task_scope, task_uv=(bool(a.require_passkey and a.uv_ok) if reason == "device" else bool(require_passkey or self._task_uv(tool, tool_input, v, policy_env))) if a.task_scope else None, task_id=self._task_id() if a.task_scope else None, task_action=self._task_action(tool, isha, tgk) if a.task_scope else None,
                         scope_sha256=approvals.allow_add_line(picks) if picks else None,
                         **({"grant": gd["grant"].get("rid"), "auto": "goal"} if reason == "goal_auto" and gd else
                            {"grant": a.tg_by, "auto": "task"} if reason == "task_grant" else {}))
        if decision in ("allow", "allow_batch") and reason == "device" and _superseded(gone):
            approvals.record(self.st, **base, decision="deny", reason="superseded_after_allow", device=did)
        if gd is not None:
            if reason == "goal_auto":
                self.goals.count("auto")
            elif decision != "allow" and a.goal in ("hold", "attack") and reason in ("device", "timeout"):
                self.goals.block(summary, a.goal_why or a.goal, tool=tool, cats=a.cats, human_text=a.goal_what, who=a.goal_who,
                                 rule=a.why, rule_en=a.why_en)
            elif decision == "allow" and reason == "device":
                self._goal_grant_tap(a, did, picks)
        if decision == "allow" and reason == "device" and pa_action in preauth.picked(picks) and a.uv_ok and sk:
            await self._preauth_add(a, pa_action, did, sk, picks, pa_bind)
        picks = preauth.strip(picks)
        raw = decision
        if decision == "allow" and reason == "device" and not a.goal and not _superseded(gone):
            await self._task_grant_open(a, did, sk)
        if decision == "allow_batch" and reason == "device":
            self._open_grant(a, did)
        decision = "allow" if decision == "allow_batch" else decision
        result = decision if reason in ("device", "goal_auto", "task_grant") else {"timeout": "timeout", "estop": "stopped"}.get(reason, "gone")
        self.st.log("ask_done", id=rid, tool=tool, decision=decision, reason=reason, device=did)
        self.activity("decision", id=rid, tool=tool, result=raw if reason == "device" else reason, reason=reason,
                      by=self._dname(did) if did else None, cats=a.cats, task=a.task, summary=summary[:200])
        self.emit("ask_done", id=rid, result=result, reason=reason, device=did)
        why = _done_why(reason, decision, a)
        self._post(self._send_ready, lambda s: {"t": "ask_done", "id": rid, "result": result, **why})
        self.status_changed()
        observe(reason)
        if decision == "allow":
            if getattr(a, "secret_display_route", False):
                observe("secret_display_route")
                return {"behavior":"deny", "message":("Use agentj secret send for the controlled pickup card; values reach only the UV-confirming device." if card_lang(self.lang) == "en" else "请使用 agentj secret send 受控领取卡；秘密只交给完成本人验证的遥控器。")}
            if reason == "device":
                await self._peer_launch_note(tool, tool_input, avatar, own, unattended)
                if a.settings_session is not None and not _superseded(gone):
                    from . import auth_settings_uv
                    auth_settings_uv.mint(self, a.settings_session, a)
            answer = {"behavior": "allow", "updatedInput": tool_input}
            if risk_scope and reason == "device" and lv != "safe":   # P158: never from a countdown; P336: not under safe
                answer["risk_grant"] = {"rid": rid, "device": did, "sign_pub": sk}
            if grant_info and reason == "device":
                answer["grant_info"] = {"rid": rid, "device": did, "sign_pub": sk, **({"approval_v":2,"envelope":a.envelope} if getattr(a,"answer_v",None)==2 else {})}
                if getattr(a, "fa", None):      # P574: what another host needs to check this Face ID itself
                    answer["grant_info"].update(fa=a.fa, sig=wire.b64u(sig), summary=summary, kind=a.pk_kind, channel=self.channel)
            if picks and not a.goal_add:
                answer["allow_add"] = picks
            return answer
        if a.goal in ("hold", "attack") and reason in ("device", "timeout"):
            from .privacy import owner_text
            gl = card_lang(self.lang)
            return {"behavior": "deny", "message": goal_mod.hold_message(a.goal_why or a.goal, gl, reason,
                                                                         owner_text(a.why_en if gl == "en" and a.why_en else a.why))}
        msg = {"device": "用户在手机上拒绝了这个操作。", "timeout": _timeout_text(self.lang, int(self.ask_ttl), a.pk_fail),
               "serve_stop": "agentj serve 已停止：默认拒绝。", "estop": "已急停（全部停下）：默认拒绝。"}.get(reason, "已拒绝。")
        return {"behavior": "deny", "message": msg, **({"reason": reason} if grant_info else {})}

    async def _restart_request(self, tool, original, pinned, verdict, base, summary, gone, lv, *, full):
        """#510: paired tap or signed full; execute only the fixed host action, never the original shell."""
        from subprocess import SubprocessError
        rid = base["rid"]
        reason, did, sk, sig = "level_harness", None, None, None
        decision = "allow" if full and lv in ("harness", "full") and self._approvers() else "deny"
        if decision != "allow":
            if not self._approvers():
                approvals.record(self.st, **base, decision="deny", reason="no_device")
                return {"behavior": "deny", "message": "没有能批准的已配对手机 / No paired approver"}
            a = Ask(rid, tool, summary, base["shown_sha256"], original, base["input_sha256"],
                    time.monotonic() + self.ask_ttl, asyncio.get_running_loop().create_future(),
                    cats=verdict.cats, why=verdict.why, why_en=verdict.why_en, require_passkey=False,
                    reason=action_policy.reason(summary))
            a.prow = "service_restart"
            self._bind_native_ask(a, 'restart')
            self.asks[rid] = a
            self.st.log("ask", id=rid, tool=tool, agent=base["agent"], kind="danger", tier="hold")
            self.emit("ask", id=rid, tool=tool, summary=approval_review.safe_text(summary, 24000))
            self._post(lambda fn: self._send_ready(fn), lambda session: self._ask_msg(a, session))
            self.status_changed()
            self.push_notify("ask")
            self._interrupt("cards", face=False)
            try:
                done, _ = await asyncio.wait({a.fut} | ({gone} if gone else set()), timeout=self.ask_ttl,
                                             return_when=asyncio.FIRST_COMPLETED)
                if a.fut in done:
                    decision, did, sk, sig = a.fut.result()
                    reason = "device" if did else "serve_stop"
                else:
                    decision, reason = "deny", "agent_gone" if gone in done else "timeout"
            finally:
                self.asks.pop(rid, None)
            self.status_changed()
        if decision == "allow":
            current = await asyncio.to_thread(restart_guard.prove, tool, original)
            if current != pinned or self.stopped() or self.stopping.is_set() or (gone is not None and gone.done()) or (did and (not self._native_ask_live(a, pending=False) or self.st.sign_key(did) != sk)):
                decision, reason = "deny", "restart_proof_changed"
            else:
                try:
                    await asyncio.to_thread(restart_guard.schedule, tool, original, self.st)
                except RuntimeError as exc:
                    decision, reason = "deny", ("restart_schedule_unknown_do_not_retry" if
                        str(exc) == "restart_schedule_unknown_do_not_retry" else "restart_schedule_failed")
                except (OSError, ValueError, SubprocessError):
                    decision, reason = "deny", "restart_schedule_failed"
        approvals.record(self.st, **base, decision=decision, reason=reason, device=did, sign_pub=sk, sig=sig,
                         **({"grant": approval_level.level_grant(self.st)} if reason == "level_harness" else {}))
        self.st.log("ask_done", id=rid, tool=tool, decision=decision, reason=reason, device=did)
        self.emit("ask_done", id=rid, result=decision, reason=reason, device=did)
        self._post(self._send_ready, lambda session: {"t": "ask_done", "id": rid, "result": decision, "reason": reason})
        if decision == "allow":
            line = pick(self.lang, "已安排重启 Agent J，连接会短暂断开。", "Agent J restart scheduled; this remote will briefly disconnect.")
            return {"behavior": "deny", "message": line + pick(self.lang,
                    "主机已处理此请求，不要执行或重试原命令。", " The host handled this request; do not run or retry the original command."),
                    "host_handled": "service_restart"}
        if reason == "restart_schedule_unknown_do_not_retry":
            return {"behavior": "deny", "message": pick(self.lang, "重启安排结果不确定，请勿重复执行。", "Restart scheduling is uncertain; do not retry."), "reason": reason}
        return {"behavior": "deny", "message": pick(self.lang, "重启未执行：", "Restart was not scheduled: ") + reason}

    def _interrupt(self, what: str, face: bool = False, chain=None) -> None:
        """E4 (ADR-0269-w3-policy): one card / question shown to the owner, counted per task — counts only."""
        try:
            if chain is None and provenance.unattended_turn(self.agent) == "system":
                chain = _chain_turn(self.agent)
            if chain:
                task, kind = "c:" + str(chain["ids"][0]), "chain"
            else:
                src = provenance.turn_source(self.agent)["kind"] if self.agent is not None else "none"
                task, kind = "t:" + str(self.cur_turn if self.cur_turn is not None else src), src
            self.interrupts.bump(task, kind, what, face=face)
            self.interrupts.save()      # one small 0600 file, written in step with approvals.record's own append (no late write)
        except Exception as e:  # noqa: BLE001 — a metric never blocks a card
            bg.swallowed(self.st.log, "interrupts_failed", e, every=0)

    def _dispatch_turn(self) -> bool:   # #455: the owner's own turn or a same-computer turn (hotfix #455: + a host system notice)
        u = provenance.unattended_turn(self.agent)
        return u == "same_host" or (u == "system" and not self._foreign_seen()) or (u is None and not provenance.untrusted_turn(self.agent) and not provenance.delegated_turn(self.agent)
                                    and provenance.is_owner(provenance.turn_source(self.agent)) and not _same_owner_turn(self.agent))

    def _dispatch_ctx(self) -> dict:   # #455 F3 B (thread): where the dispatch's bare words resolve, read by the host
        from . import peer_origin
        cfg = self.agent_cfg or {}
        if cfg.get("session_mode") == "shared":    # an unfenced desktop session: its own PATH (point-in-time rule)
            caller, strict = peer_origin.session_path(getattr(self.agent, "session", None)), False
        else:                                      # serve started it with serve's PATH; fenced → the strict rule
            caller, strict = os.environ.get("PATH"), bool(cfg.get("fence", True))
        return {"caller": caller, "strict": strict, "unit": peer_origin.manager_env}   # read only if a unit-side word is bare

    def _dispatch_check(self, command):
        from . import peer_origin
        return peer_origin.check_batch(command, (self.agent_cfg or {}).get("dir"), self.st.root, self._dispatch_ctx())

    async def _peer_launch_note(self, tool, tool_input, avatar, own, unattended):
        launches = getattr(self, "peer_launch", None)
        if (launches is None or tool != "Bash" or avatar is not None or own or not isinstance(tool_input, dict)
                or unattended not in (None, "same_host", "system") or not self._dispatch_turn()):
            return
        cmd, root = tool_input.get("command"), (self.agent_cfg or {}).get("dir")
        names = await asyncio.to_thread(lambda: launches.note(cmd, root, ctx=self._dispatch_ctx()))
        if names:
            import copy
            origin = approval_review.snapshot(self)
            refs = self.__dict__.setdefault("_approval_launch_refs", {})
            for name in names:
                ref = copy.deepcopy(origin) if origin else {}
                if ref:
                    ref["chain"] = (ref.get("chain", []) + [{"kind": "registered_task", "id": name}])[-16:]
                    ref["task_record"] = name
                refs[name] = ref
            while len(refs) > 64:
                refs.pop(next(iter(refs)))
            await asyncio.to_thread(launches.save)
            from . import approval_context
            unit, why = await asyncio.to_thread(self._dispatch_check, cmd)
            if unit and why == "ok":
                approval_review.launch(self, unit)

    def _classify(self, tool, tool_input):
        # Presentation adapters receive no heuristic danger verdict in the main
        # owner path. Sandbox-specific classifiers remain independently owned.
        return danger.Verdict()

    def policy_refused(self, tool: str, tool_input: dict, why: str, notice: str, fix: str | None = None) -> None:

        rid = secrets.token_hex(16)
        v = self._classify(tool, tool_input)
        summary = approval_summary(tool, tool_input, v, self.lang, workdir=(self.agent_cfg or {}).get("dir"))
        approvals.record(self.st, rid=rid, agent=self.agent.kind if self.agent else "?", tool=tool,
                         input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(tool, summary),
                         cats=v.cats, decision="deny", reason="policy")
        self.st.log("ask_done", id=rid, tool=tool, decision="deny", reason="policy")
        self.activity("decision", id=rid, tool=tool, result="policy", reason="policy", why=why, cats=v.cats,
                      task=self.task_label, summary=summary[:200])
        self.emit("ask_done", id=rid, result="deny", reason="policy")
        from . import codex_full_access        # 0.30.7: typed host metadata for the one button, never inferred from the text
        text = f"{notice}（{why}：{summary.splitlines()[0][:120]}）"
        if fix == codex_full_access.NAME:
            self.local_notice(text, card={"fix": codex_full_access.NAME})
        else:
            self.local_notice(text)

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

    # ------------------------------------------------------------ P573 (#389): one signature per task and action class
    def _task_grant_key(self, tool: str, v, *, delegated: bool, same_owner: bool, avatar, ttl, own_pk: bool,
                        lv: str) -> tuple | None:
        if lv == "safe" or ttl is not None or own_pk or avatar is not None or (delegated and not same_owner):
            return None
        if self.task_label is not None or self.cur_turn is None:
            return None
        from . import policy_registry
        if tool in approval_level.HARNESS_KEEPS or tool in goal_mod.SOFT_EXEMPT or (
                policy_registry.HOST_TOOLS.get(tool) in ("device.pair", "payment")):
            return None
        if provenance.untrusted_turn(self.agent) and not same_owner:
            return None
        cls = self._task_class(tool, v)
        return (self._task_id(), self.agent.kind if self.agent else "?", (self.agent_cfg or {}).get("dir"),
                tuple([cls[0], tuple(cls[1]), tuple(cls[2])]))

    @staticmethod
    def _task_uv(tool, inp, verdict, env=None):
        from . import policy_registry
        try:
            req = policy_registry.main_requirement(tool, inp, env=env) if env else policy_registry.main_requirement(tool, inp)
            keep = confirm_tier.harness_keeps(tool, inp, env=env) or approval_level.harness_floor(tool, inp, verdict)
            return req == "uv" or keep in confirm_tier.HARNESS_KINDS + approval_level.FLOOR_FACE
        except Exception:  # noqa: BLE001 — an unknown floor cannot lower a signed plan's required tier
            return True

    def _approval_live_authority(self):
        """Host-owned live task identity, independent of request/socket ContextVars."""
        agent = self.agent
        origin = getattr(getattr(agent, "cur_send", None), "approval_origin", None)
        identity = origin if origin else [self.cur_turn, self.__dict__.get("_approval_origins", {}).get(getattr(self, "desktop_turn", None))]
        return approval_review.digest([self.__dict__.get("task_epoch"),
                                       getattr(agent, "kind", None), (self.agent_cfg or {}).get("dir"), identity,
                                       getattr(self, "desktop_turn", None), getattr(agent, "phone_turn", None),
                                       getattr(agent, "phone_seen", None)])

    def _task_id(self):
        origin = approval_review.snapshot(self) or {}
        identity = [origin.get("session"), origin.get("root_message_id")] if origin.get("root_message_id") else self.cur_turn
        context = [identity, getattr(self.agent, "kind", None), (self.agent_cfg or {}).get("dir")]
        return self.__dict__.setdefault("task_epoch", secrets.token_hex(16)) + ":" + approvals.input_digest(context)

    @staticmethod
    def _task_class(tool, v):
        from . import policy_registry
        return [policy_registry.tool_action(tool) or str(tool)[:64], sorted(v.cats),
                sorted(set(getattr(v, "rules", None) or []))]

    @staticmethod
    def _task_action(tool, isha, key):
        return {"tool": tool, "input_sha256": isha, "class": [key[3][0], list(key[3][1]), list(key[3][2])]}

    # Signed concrete plans never widen tool/input/class; roots, turn and expiration remain live checks.
    def _task_grant_use(self, key, tool, isha, face, inp=None):
        rec = self.__dict__.setdefault("task_grants", {}).get(key)
        if rec is None:
            return None
        if (time.monotonic() > rec["until"] or (rec.get("scope") and time.time() >= json.loads(rec["scope"])["expires"]) or rec["left"] <= 0
                or self.st.sign_key(rec["device"]) != rec["sk"] or self.stopped() or (self._task_id() if isinstance(key[0], str) else self.cur_turn) != key[0]):
            self.task_grants.pop(key, None)
            return None
        if (tool, isha) not in rec["listed"] or (face and not rec["face"]):
            return None
        bindings = json.loads(rec["scope"]).get("object_bindings", {}) if rec.get("scope") else {}
        expected = bindings.get(tool + ":" + isha)
        if expected is not None:
            from . import bounded_plan
            try:
                actual = bounded_plan.shell_seal(tool, inp, (self.agent_cfg or {}).get("dir"), self.__dict__.get("_plan_seal_key")) if inp is not None else None
            except (ValueError, OSError):
                actual = None
            if actual != expected:
                return None
        step = (tool, isha)
        if step in rec["used"]:
            return None
        rec["used"].add(step)
        rec["left"] -= 1
        return rec

    async def _task_operation_reserve(self, rec, tool, isha):
        from .bounded_plan import OperationOnce
        ledger = self.__dict__.get("_approval_operation_once")
        if ledger is None:
            ledger = self._approval_operation_once = OperationOnce(self.st.root / "approval-operation-once.sqlite3")
        source = approval_review.snapshot(self) or {}
        task = self._task_id()
        operation = approval_review.digest({'channel':self.channel, 'session':source.get('session'),
                                          'root':source.get('root_message_id'), 'tool':tool, 'input':isha})
        try:
            reserved = await asyncio.to_thread(ledger.reserve, operation, rec["rid"])
        except (ValueError, OSError):
            return False
        return bool(reserved and not self.stopped() and time.monotonic() <= rec["until"]
                    and self.st.sign_key(rec["device"]) == rec["sk"] and self._task_id() == task
                    and approval_review.snapshot(self) == source)

    async def _task_grant_open(self, a, did, sk):
        if a.tg_key is None or not a.task_scope or not did or not sk:
            return
        tg = self.__dict__.setdefault("task_grants", {})
        scope = json.loads(a.task_scope)
        if scope["target"] is not None:
            return
        used = set() if getattr(a, "tg_plan_only", False) else {(a.tool, a.input_sha)}
        for action in scope["actions"]:
            cls = action["class"]
            key = a.tg_key[:3] + ((cls[0], tuple(cls[1]), tuple(cls[2])),)
            if len(tg) >= MAX_TASK_GRANTS and key not in tg:
                tg.pop(next(iter(tg)))
            listed = {(x["tool"], x["input_sha256"]) for x in scope["actions"] if x["class"] == cls}
            tg[key] = {"rid": a.rid, "device": did, "sk": sk, "face": bool(a.require_passkey and a.uv_ok),
                       "listed": listed, "used": used, "scope": a.task_scope, "left": max(0, len(listed) - len(listed & used)),
                       "until": min(a.deadline, time.monotonic() + max(0, scope["expires"] - time.time()))}
        for x in list(self.asks.values()):
            if x is a or x.fut.done() or x.parked or x.tg_key is None:
                continue
            rec = self._task_grant_use(x.tg_key, x.tool, x.input_sha, x.require_passkey, x.tool_input)
            if rec is not None and await self._task_operation_reserve(rec, x.tool, x.input_sha) and not x.fut.done():
                x.tg_by, x.tg_dev, x.task_scope = a.rid, did, a.task_scope
                x.fut.set_result(("allow", None, None, None))

    async def _answer_refused(self, s: Session, a: Ask, reason: str) -> None:
        self.st.log("answer_refused", id=a.rid, device=s.device, reason=reason)
        await self.send_app(s, {"t": "answer_refused", "id": a.rid, "why": reason, "ask": self._ask_msg(a, s)})

    async def _answer_held_channel(self, controller, ask, session, obj):
        from .wechat import _HeldChannelApproval
        ref = getattr(ask,'native_authority',None)
        cap = ref[4] if isinstance(ref,tuple) and len(ref)==11 and ref[3]=='held_channel' else None
        if (type(cap) is not _HeldChannelApproval or cap.controller is not controller or cap.host is not self
                or cap.ask is not ask or controller.cards.get(cap.slot) is not ask
                or obj.get('id') != ask.rid or not self._paired_live(session) or not self._native_ask_live(ask)):
            return False
        await self._answer(session,obj,_held_channel=ask)
        return True

    def _friend_main(self, s: Session, agent_text: str, label: str) -> str | None:
        """ADR-0304-friend-card-main-agent: a verified signed 「交给主 Agent 来办」 from paired session `s` → ONE delegated
        turn with the existing owner-approved friend-handover source `{"kind": "friend_task", "role": "task"}` (same_owner
        ForwardSend approved=True; assist.py): the main Agent works with the owner's tools, but Host.ask treats the turn as
        delegated — no batch, goal, parked, Full-access or Jev exemptions; every sensitive step is a fresh card; sudo asks.
        Never `phone()`: the friend's words are in it. No automatic reply to the friend (the Agent's own `friends tell`).
        `agent_text` = PeerService.main_text; `label` = the host-written chat line (page src `phone`: the owner tapped it;
        its approval origin is unverified + suspicious). Submitted synchronously, last: None = queued."""
        if self.stopped():
            return "estop"
        if not self._paired_live(s):
            return "not_paired"
        if not self.agent:
            return "no_agent"
        if self.sends.full(s.device):
            return "too_many"
        source = {"kind": "friend_task", "role": "task"}
        turn = self.hist_add({"k": "phone", "dev": s.device, "name": (s.name or s.device)[:64], "text": label}, "", "open")
        send = compose.Send("friend-main", secrets.token_urlsafe(16), turn["id"], text=agent_text, by=s.name or s.device,
                            source=source)
        send.auto_notify = True              # no phone say receipts / replay for this send (like ForwardSend, AssistSend)
        from . import approval_context
        send.approval_origin = approval_context.remember(self, turn, source)
        send.approval_origin = approval_review.remember(self, turn, source)     # verified False, suspicious True
        from . import friend_handoff
        friend_handoff.mint(self, send, agent_text)          # ADR-0306: its mail to others = a UV card naming the friend
        try:
            self.agent.submit(send)
        except Exception:  # noqa: BLE001 — an adapter that raised on submit cannot deliver this instance
            send.state = "cancelled"
            self.hist_update(turn["id"], end="failed")
            return "submit_failed"
        self.st.log("friend_main_turn", device=s.device, id=turn["id"])          # ids only
        return None

    async def _answer(self, s: Session, obj: dict, *, _held_channel=None) -> None:
        if _held_channel is None and getattr(self, "wechat", None) is not None and await self.wechat.answer(s, obj):
            return
        if _held_channel is None and getattr(self, "external_channels", None) is not None and await self.external_channels.answer(s, obj):
            return
        refused = []
        if _held_channel is None and self.peers is not None and self.peers.answer(
                s.device, obj, refuse=refused.append, handoff=lambda text, label: self._friend_main(s, text, label)):
            for receipt in refused:
                await self.send_app(s, receipt)
            return                         # P71: a friend card (friend_request / peer_question), decided by its own rules
        rid, ok, sig, batch = obj.get("id"), obj.get("ok"), obj.get("sig"), obj.get("batch", False)
        if _held_channel is not None:
            ref = getattr(_held_channel,'native_authority',None)
            if (not isinstance(ref,tuple) or len(ref)!=11 or ref[3]!='held_channel'
                    or rid != _held_channel.rid or not self._paired_live(s) or not self._native_ask_live(_held_channel)):return
            a = _held_channel
        else:
            a = self.asks.get(rid) if isinstance(rid,str) else None
        if a is None or a.fut.done():
            return                         # unknown or already decided (another phone, timeout): nothing to do
        if not isinstance(ok, bool) or not isinstance(sig, str) or not isinstance(batch, bool) \
                or time.monotonic() > a.deadline:
            await self._answer_refused(s, a, "shape_or_late")
            return
        if a.only and a.only != s.device:
            return await self._answer_refused(s, a, "wrong_remote")
        if (ok and a.tool == "agentj secret grant" and self.peers is not None
                and not self.peers.xfer.bound(s.device)):
            # 0304 R-F5: the same-owner grant card is signable by any roster-bound remote; an unbound remote's
            # allow does not consume it (the card stays open for the others) and is offered the binding instead.
            entry = self.peers.xfer.binding_entry(s.device)
            if entry:
                await self.send_app(s, {"t": "roster_bind_offer", "entry": entry})
            return await self._answer_refused(s, a, "roster_binding_required")
        if batch and (not ok or a.scope is None or a.cats):
            await self._answer_refused(s, a, "no_batch")
            return
        if a.task_scope and json.loads(a.task_scope)["task"] != self._task_id() and not getattr(a, "review_event", None):
            await self._answer_refused(s, a, "stale_task")
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
        answer_v = obj.get("approval_v")
        display_digest = getattr(a, "legacy_digest", a.digest)
        allowed_fields = {"t", "id", "ok", "sig", "batch", "allow_add", "fa"}
        if type(answer_v) is int and answer_v == 2:
            allowed_fields |= {"approval_v", "revision", "envelope_sha256"}
            if (not hasattr(a, "envelope") or type(obj.get("revision")) is not int or obj.get("revision") != a.review_revision
                    or obj.get("envelope_sha256") != a.envelope_digest):
                return await self._answer_refused(s, a, "approval_context")
            display_digest = a.envelope_digest
        elif answer_v is not None:
            return await self._answer_refused(s, a, "approval_version")
        if answer_v is None and (getattr(a, "review_v2_only", False) or batch or picks):
            return await self._answer_refused(s, a, "client_update_required")
        if hasattr(a, "envelope"):
            if a.review_state != "pending":
                return await self._answer_refused(s, a, "stale_envelope")
            source = a.envelope["source"]
            if ok and not getattr(a, "list_model", False) and (source.get("verified") is not True or source.get("suspicious") is True):
                return await self._answer_refused(s, a, "unverified_trigger")
        if set(obj) - allowed_fields:
            return await self._answer_refused(s, a, "approval_fields")
        if not sk or not approvals.verify(sk, sigb, self.channel, s.device, rid, decision, display_digest, scope, picks, task_scope=a.task_scope, approval_v=answer_v):
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
                challenge = passkey.elevate_challenge(self.channel, s.device, rid, a.pk_kind, rid, display_digest)
                count = passkey.verify_assertion(pk, f, challenge)
                self.st.passkey_count(s.device, pk["id"], count)
            except (passkey.PasskeyError, ValueError):
                from . import auth_settings_uv
                auth_settings_uv.clear(self, s)
                a.pk_fail = True
                await self._answer_refused(s, a, "passkey_required")
                return
            if a.settings_session is s:
                a.settings_uv_at = time.monotonic()
            a.uv_ok = True                 # P539: this allow passed the passkey (a preauth pick needs it)
            if getattr(a, "list_model", False) or a.task_scope or getattr(a, "bounded_plan", None) is not None or a.tool in (owner_grant.TOOL, "agentj mail workflow-grant"):  # P574 + narrow workflow mail: the assertion itself travels to the other same-owner hosts
                a.fa = {k: obj["fa"][k] for k in ("id", "cd", "ad", "sig")}
        from . import auth_settings_uv
        if not ok:
            auth_settings_uv.clear(self, s)
        if getattr(a, "decision_pending", False):
            return await self._answer_refused(s, a, "decision_pending")
        a.decision_pending = True
        review_bound = hasattr(a, 'envelope')
        if not review_bound and not self._native_ask_live(a):
            a.decision_pending = False
            return await self._answer_refused(s, a, 'authority_changed')
        answer_task = self._task_id() if _held_channel is None else None
        answer_source = approval_review.digest(approval_review.snapshot(self)) if _held_channel is None else None
        from .list_approval import SettingsContext
        settings_context = getattr(a, 'settings_context', None)
        settings_bound = type(settings_context) is SettingsContext
        def settings_live():
            return settings_bound and settings_context.live(self)
        answer_live_authority = (None if _held_channel is not None else
                                 settings_context.current(self) if settings_bound else self._approval_live_authority())
        if (review_bound and not getattr(a, "review_event", None)
                and (answer_live_authority != a.review_live_authority or (settings_bound and not settings_live()))):
            a.decision_pending = False
            return await self._answer_refused(s, a, "authority_changed")
        reserved_here, ledger = False, None
        try:
            if answer_v == 2 and ok and getattr(a, "bounded_plan", None) is None and not getattr(a, "tg_plan_only", False):
                from .bounded_plan import OperationOnce
                ledger = self.__dict__.get("_approval_operation_once")
                if ledger is None:
                    ledger = self._approval_operation_once = OperationOnce(self.st.root / "approval-operation-once.sqlite3")
                try:
                    reserved = await asyncio.to_thread(ledger.reserve, a.operation_once_key, a.rid)
                except (ValueError, OSError):
                    return await self._answer_refused(s, a, "operation_ledger_unavailable")
                if not reserved:
                    return await self._answer_refused(s, a, "operation_already_consumed")
                reserved_here = True   # P0: a refusal below releases it, else every retry reads operation_already_consumed
            if (a.fut.done() or time.monotonic() > a.deadline or self.stopped()
                    or self.st.sign_key(s.device) != sk
                    or (not review_bound and not self._native_ask_live(a))
                    or (settings_bound and not settings_live())
                    or (review_bound and not settings_bound and (self.agent is not a.review_agent
                    or (hasattr(a, "envelope") and str(self.hist.epoch) != a.envelope["session"])
                    or self._task_id() != answer_task
                    or self._approval_live_authority() != answer_live_authority
                    or (not getattr(a, "review_event", None) and self._approval_live_authority() != a.review_live_authority)
                    or approval_review.digest(approval_review.snapshot(self)) != answer_source))):
                # P0 (0.28.0): the card's source is pinned by the signed envelope. The answer runs in the phone socket's
                # context, which cannot re-derive a hook/subagent request's origin (nor the main turn's after a newer
                # owner message); comparing it with the ask-time source refused every such allow and deny.
                if reserved_here and callable(getattr(ledger, "release", None)):
                    try:
                        await asyncio.to_thread(ledger.release, a.operation_once_key, a.rid)
                    except (ValueError, OSError):
                        pass
                return await self._answer_refused(s, a, "authority_changed")
            a.answer_v, a.answer_digest = answer_v, display_digest
            if hasattr(a, "envelope") and not ok:
                if answer_v == 2:
                    from .bounded_plan import OperationOnce
                    ledger = self.__dict__.get("_approval_operation_once")
                    if ledger is None:
                        ledger = self._approval_operation_once = OperationOnce(self.st.root / "approval-operation-once.sqlite3")
                    try:
                        await asyncio.to_thread(ledger.finish,a.operation_once_key,a.rid,'denied')
                    except (ValueError, OSError):
                        return await self._answer_refused(s, a, 'operation_ledger_unavailable')
                a.review_state = "denied"
                approval_review.submit_event(self, a, "deny", secrets.token_hex(16), device=s.device)
            a.allow_add = list(picks or [])
            if _held_channel is not None:
                a._held_verified = (self,s.device,sk,sigb,display_digest)
            a.fut.set_result((decision, s.device, sk, sigb))
        finally:
            a.decision_pending = False

    # ------------------------------------------------------------ Agent friends (P71, PROTOCOL §17.7)
    def peer_broadcast(self, obj: dict) -> None:

        self._post(self._send_p33, lambda s, o=obj: o)

    async def on_friends(self, s: Session, t: str, obj: dict) -> None:

        r = obj.get("r")
        if t not in FRIEND_READS and t not in controls.FRIEND_ACTIONS and t != "fr_gsay":   # P426: fr_gsay = 「我的设备」 only
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
        elif t == "fr_gsay":                   # P426 #170: a 「我的设备」 group message — the owner's own devices only
            target = {"text": obj.get("text")}
            bad = not (isinstance(target["text"], str) and 0 < len(target["text"]) <= wire.MAX_TEXT_P33)
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
        root_offer = None
        if t == "fr_set" and target.get("op") == "owner-confirm-v3":
            root_offer = await self.owner_uv_offer(s, r, t, target, obj)
            if root_offer is None:
                return
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, t, target)
        if not why and root_offer is not None:
            why = self.owner_uv_check(s, root_offer, obj)
        if why:
            self.st.log("control_refused", device=s.device, action=t, reason=why)
            controls.log(self.st, action=t, device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, t, False, why=why)
        so_start = t == "fr_set" and target.get("op") == "group" and target.get("value") == "same-owner"
        if so_start and obj.get("cont") is not None:
            # 0306 A5: the page continues a Same owner start after binding. The ticket grants nothing — this is
            # still this remote's own signed start (checked above) and the code confirm still needs a fresh UV.
            from .peer import parse_id
            pid = parse_id(target["friend"]) or target["friend"]
            miss = self.peers.same_owner.cont_take(obj.get("cont"), s.device, s.cid, pid)
            self.st.log("same_owner_continue", device=s.device, result=miss or "ok")
        try:
            ok, why, fid = await self.peers.write(t, target, device=s.device, proof={
                "channel": self.channel, "device": s.device, "pub": wire.b64u(self.st.sign_key(s.device)),
                **{k: obj[k] for k in ("n", "ts", "sig")}, **({"fa":obj["fa"]} if root_offer is not None else {})})
        except OSError:
            ok, why, fid = False, "io", None
        result = "ok" if ok else str(why)
        self.st.log("control", device=s.device, action=t, result=result)
        controls.log(self.st, action=t, device=s.device, result=result, obj=target, sig=obj.get("sig"), n=obj.get("n"),
                     ts=obj.get("ts"))
        extra = {}
        if so_start and not ok and why == "roster_binding_required":
            # 0306 A5: first use of this remote — bind (Touch ID once), then the page sends the start again with `cont`
            if self.st.passkey_of_device(s.device):
                extra = {"next": "bind", "cont": self.peers.same_owner.cont_issue(s.device, s.cid, fid)}
            else:
                extra = {"next": "passkey"}
        await self._ctl_res(s, r, t, ok, **({} if ok else {"why": why, **extra}))
        if ok:
            self.peers.changed(fid if t in ("fr_set", "fr_ctx") else None)

    # ------------------------------------------------------------ phone controls (PROTOCOL §8 "phone controls", ADR-A50 – A54)
    def stopped(self) -> bool:
        return bool(self.estop.get("on"))

    def activity(self, kind: str, **kw) -> None:
        try:
            audits = self.__dict__.get("_approval_audits", {})
            audit = audits.get(kw.get("id"))
            if (kind in ("auto", "goal_auto") or kind == "decision" and kw.get("reason") in ("goal_auto", "task_grant")) and audit is not None:
                kw.update(audit)
            if kind in ("decision", "auto", "goal_auto"):
                audits.pop(kw.get("id"), None)
            if kind == "goal_auto":
                kind = "auto"
                kw.setdefault("by", "goal")
            activity.record(self.st, kind, **kw)
        except Exception:  # noqa: BLE001 — the activity log never costs anything else
            pass

    def _signer_name(self, did: str | None) -> str:

        if not did:
            return ""
        if did in self.st.devices():
            return self._dname(did) or ""
        rec = next((r for r in approval_level.signers(self.st).values() if isinstance(r, dict) and r.get("device") == did), {})
        return str(rec.get("name") or "")[:60] or did[:8]

    def approval_check(self, why: str = "") -> bool:

        try:
            if self.agent_cfg is not None and bool(self.agent_cfg.get("follow_harness")) != approval_level.follow_harness(
                    self.preferences, self.st):
                self._reload_runtime()
            items = approval_level.unsigned(self.preferences, self.st)
            mark = approval_level.unsigned_mark(items)
            if mark == approval_level.told(self.st):
                return False
            approval_level.set_told(self.st, mark)
        except Exception as e:  # noqa: BLE001 — a check never breaks the caller
            bg.swallowed(self.st.log, "approval_check_failed", e, every=0)
            return False
        self._post(self._send_ready, lambda x: self.preferences_msg(x))
        if not items:
            return False
        names = {i.get("device"): self._signer_name(i.get("device")) for i in items}
        text = approval_level.owner_unsigned_text(items, names, self.lang)
        self.st.log("approval_unsigned_owner", reason=why or "check", status=",".join(f"{i['key']}:{i['why']}" for i in items))
        self.agent_notice(text)
        self.push_notify("security")
        async def tell():
            sent = await approval_level.tell_owner(self, text)
            self.st.log("approval_unsigned_told", status=",".join(sent) or "none")   # review N1: none = phone line + push only
        coro = tell()
        try:
            self._bg(coro, "approval_unsigned_tell")
        except RuntimeError:                  # not inside the loop (a unit test calling it directly)
            coro.close()
        return True

    async def _approval_watch(self, first: float = 30.0, every: float = 600.0) -> None:

        await asyncio.sleep(first)
        while True:
            self.approval_check("watch")
            await asyncio.sleep(every)

    def _dname(self, did: str | None) -> str | None:
        if not did:
            return None
        return self._dev_label(did) or did

    def _dev_label(self, did: str | None) -> str:

        rec = self.st.devices().get(did) if did else None
        return clean_label(device_label(rec)) if isinstance(rec, dict) else ""

    def device_renamed(self, did: str) -> None:

        label = self._dev_label(did)
        for x in self.sessions.values():
            if x.device == did and label:
                x.name = label
        self.reporter.trigger("online")
        self._post(self.remotes.changed)

    def _estop_msg(self) -> dict:
        by = self.estop.get("by", "")
        name = self.estop.get("by_name") or ("终端" if by == "terminal" else self._dname(by[6:]) if by.startswith("phone:") else by)
        return {"t": "estop_state", "on": self.stopped(), "at": self.estop.get("at", 0), "by": (name or "")[:64]}

    async def do_estop(self, by: str, name: str) -> None:
        from . import auth_settings_uv
        auth_settings_uv.clear(self)

        rec = controls.set_estop(self.st, True, by)
        self.estop = {**rec, "by_name": name}
        self.st.log("estop", status="on", device=by.split(":", 1)[1] if by.startswith("phone:") else None)
        from .core import enabled as core_enabled
        if core_enabled():
            from .core.dispatcher import set_paused
            set_paused(self.st.root, True)
        self.emit("estop", on=True, by=by)
        self.activity("estop", by=name)
        self.__dict__.pop("_parked_ok", None)          # P336: an allow given to a parked card dies with 「全部停下」
        self.__dict__.pop("_uv_list_resumes", None)    # Exact question continuations also die with emergency stop.
        with contextlib.suppress(Exception):           # P539: every 「免问」 pre-authorization ends too (re-grant after resume)
            await self._preauth_revoke("estop")
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
        if getattr(self, "external_channels", None) is not None:
            await self.external_channels.pause()
        if getattr(self, "wechat", None) is not None:
            await self.wechat.pause()
        self.elevate.cancel_all("stopped")            # F17: open sudo / secret cards end (a running command finishes)
        with contextlib.suppress(Exception):           # P408: the web terminal closes at once
            await self.term_desk.close("estop")
        with contextlib.suppress(Exception):           # 0269-w2-assist: the official assist grant ends, its task stops
            await self.assist.end("estop", by=by)
        if self.peers is not None:                    # P71 §17.9: no mailbox frame leaves, every peer session ends
            await self.peers.estop(True)
        if self.hooks is not None:                    # P366: every hook answers 423, `agentj hook wait` returns stopped
            self.hooks.estop(True)
        busy = False
        if self.agent:
            busy = await self.agent.halt(clear_queue=True)
        from .core import enabled as core_enabled
        if core_enabled():
            from .core.order_host import pause_all
            await pause_all(self)
        ran = await self.scheduler.stop_current()
        from . import sysmigrate      # P570b M1-R
        with contextlib.suppress(Exception):
            await asyncio.to_thread(sysmigrate.halt, self.st)
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
        from .core import enabled as core_enabled
        if core_enabled():
            from .core.dispatcher import set_paused
            set_paused(self.st.root, False)
        self.activity("resume", by=name)
        if self.peers is not None:
            await self.peers.estop(False)
        actor = self.agent
        actor_task = getattr(actor, "task", None)
        if actor is not None and callable(getattr(actor, "start", None)) and (actor_task is None or actor_task.done()):
            actor.start()  # P435: a reopen interrupted by e-stop left a dormant, fresh adapter
        self.status_changed()
        m = self._estop_msg()
        self._post(self._send_ready, lambda s: m)
        note = f"已恢复（{name}）：Agent J 接收新消息，定时任务按各自的启用状态继续。"
        e = self._remember("notice", note)
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": ""}, note, "done")
        self.scheduler.wake.set()

    def task_notice(self, text: str, guide: str | None = None) -> None:

        e = self._remember("notice", clean(text, wire.MAX_TEXT))
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        self.hist_add({"k": "sys", "text": "", **({"guide": guide} if guide in ("wsl", "loop") else {})}, clean(text, wire.MAX_TEXT_P33), "done")
        self.push_notify("reply")

    def task_finished(self, res: dict) -> None:

        for gid in list(self.grants):
            self.end_grant(gid, "turn_end")
        title = res.get("title") or res["id"]
        run = tasks.load(self.st)["runs"].get(res.get("run_id")) or {}
        owner_notify = (run.get("id") == res["id"] and run.get("owner_notify") is True
                        and run.get("trigger") in ("schedule", "catch_up"))
        if not owner_notify:
            # #506: journal/report is the result; the main session reviews it through the existing system queue.
            self.st.log("task_done", id=res["id"], result=res["verdict"])
            rid = res.get("run_id") or secrets.token_hex(16)
            ended = time.time()
            self.busy.finish({"key": "task-result:" + rid, "generation": rid, "kind": "ceo", "label": title,
                              "since": ended - max(0, res.get("secs") or 0),
                              "foreign": not (run.get("id") == res["id"] and run.get("completion_known") is True),
                              "report": os.path.join(self.agent_cfg["dir"], res["report"]) if self.agent_cfg and res.get("report") else res.get("report")},
                             exit_code=res.get("exit_code"), ended=ended)
            return
        state = ("stopped" if res.get("stopped") else "attention" if res.get("status") == "interrupted" else res["verdict"])
        words = {"ok": ("已完成", "completed"), "attention": ("需要留意", "needs attention"),
                 "fail": ("未完成", "failed"), "stopped": ("已中断", "was stopped")}.get(state, ("需要留意", "needs attention"))
        text = pick(self.lang, f"定时任务「{title}」{words[0]}。", f'Scheduled task “{title}” {words[1]}.')
        self.st.log("task_done", id=res["id"], result=res["verdict"])
        self.emit("task_done", id=res["id"], verdict=res["verdict"], line=text, report=res.get("report"))
        self.activity("task_done", id=res["id"], title=title, verdict=res["verdict"], line=text, secs=res.get("secs"),
                      readonly=res.get("readonly"), report=res.get("report"), stopped=res.get("stopped"))
        e = self._remember("notice", clean(text, wire.MAX_TEXT))
        self._post(self._send_legacy, lambda s, e=e: self._render(e, s))
        page = text
        if res.get("deliver") and not res.get("stopped"):   # 0.18: the brief's owner deliverables, offered as files (§13)
            page += "\n\n" + "\n".join(f"[{os.path.basename(p)}]({p})" for p in res["deliver"][:5])
        t = self.hist_add({"k": "task", "name": clean(str(title), 64), "text": ""}, clean(page, wire.MAX_TEXT_P33),
                          "stopped" if res.get("stopped") or res.get("status") == "interrupted" else "done")
        if isinstance(t, dict) and isinstance(res.get("resume"), dict):
            run_id = res["resume"].get("run_id")
            if isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id):
                self.hist_update(t["id"], card={"wf_resume":{"run_id":run_id}})
        if res.get("deliver") and isinstance(t, dict):
            self.media_later(t.get("id"))
        if isinstance(res.get("use"), dict):     # P204 (§29): the run's spend in the log (history off too) and on its page
            from . import usage as usage_mod
            usage_mod.record(self.st, "task", res["use"], task=str(res["id"])[:64])
            self.usage_today_cache = None
            if isinstance(t, dict):
                self.hist_update(t.get("id"), use=res["use"])
        self.push_notify("reply")

    async def _send_chunks(self, s: Session, head: dict, key: str, items: list, tail: dict | None = None) -> None:

        batch, size = [], 0
        for it in items:
            n = len(json.dumps(it, ensure_ascii=False).encode())
            if batch and size + n > CHUNK:
                await self.send_app(s, {**head, key: batch, "more": True})
                batch, size = [], 0
            batch.append(it)
            size += n
        await self.send_app(s, {**head, key: batch, "more": False, **(tail or {})})

    async def owner_uv_offer(self, s: Session, r, action: str, target: dict, obj: dict):

        pk = self.st.passkey_of_device(s.device)
        n = obj.get("n")
        why = "shape" if not (isinstance(n, str) and re.fullmatch(r"[0-9a-f]{32}", n)) else None if pk else "no_passkey"
        if why:
            self.st.log("control_refused", device=s.device, action=action, reason=why)
            controls.log(self.st, action=action, device=s.device, result="refused:" + why)
            await self._ctl_res(s, r, action, False, why=why)
            return None
        ch = passkey.elevate_challenge(self.channel, s.device, n, action, n, controls.object_digest(action, target))
        if obj.get("fa") is None:
            await self._ctl_res(s, r, action, False, why="passkey_required", fa=pk["id"], fa_ch=wire.b64u(ch))
            return None
        return pk, ch

    def owner_uv_check(self, s: Session, offer, obj: dict) -> str | None:

        pk, ch = offer
        try:
            f = passkey.approval_fields(obj.get("fa"))
            if f["id"] != pk["id"]:
                raise passkey.PasskeyError("bad", "credential")
            self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
        except (passkey.PasskeyError, ValueError):
            return "passkey_failed"
        return None

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
            from . import sysmigrate
            rows = await asyncio.to_thread(lambda: sysmigrate.mark(self.st, tasks.rows(self.st, wd)))
            for x in rows:
                x["running"] = self.scheduler.current_id == x["id"]
                last = x.get("last") or {}
                rid = last.get("run_id")
                if isinstance(rid, str) and not x["running"]:
                    from . import wfrun
                    try:
                        rec = wfrun.load(self.st, rid)
                        if rec.get("id") == x["id"] and rec.get("status") in ("failed", "interrupted"):
                            x["resume"] = {"run_id":rid}
                    except wfrun.RunError:
                        pass
            return await self._send_chunks(s, {"t": "tasks", "r": r}, "items", rows,
                                           {"paused": self.stopped(), "agent": self.agent_cfg["kind"] if self.agent_cfg else None})
        if t == "systask_list":         # P207 §2: read-only list of the Agent J host's own timers / cron / LaunchAgents / schtasks
            from . import systasks
            items = await asyncio.to_thread(systasks.rows)
            return await self._send_chunks(s, {"t": "systasks", "r": r}, "items", items, {"max": systasks.MAX})
        # ---- writes: signed like an approval (Invariant 20)
        action = {"mem_rm": "mem_rm", "mem_undo": "mem_undo", "estop": "estop", "resume": "resume",
                  "task_set": "task_on" if obj.get("on") is True else "task_off", "task_migrate": t}[t]
        target = {k: obj.get(k) for k in ("src", "file", "fsha", "iid", "id", "tsha")}
        if action in ("mem_rm",) and not all(isinstance(target[k], str) and len(target[k]) <= 300
                                              for k in ("src", "file", "fsha", "iid")):
            return await self._ctl_res(s, r, action, False, why="shape")
        if action in ("mem_undo", "task_on", "task_off", "task_migrate") and not (isinstance(target["id"], str) and len(target["id"]) <= 64):
            return await self._ctl_res(s, r, action, False, why="shape")
        if action in ("task_on", "task_off", "task_migrate") and not isinstance(target["tsha"], str):
            target["tsha"] = ""
        pk = None          # P573 (#386 ⑤): lifting the emergency stop is the owner's signed tap — no Face ID on top
        ch = None
        if pk:
            n = obj.get("n")
            if not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
                return await self._ctl_res(s, r, action, False, why="shape")
            ch = passkey.elevate_challenge(self.channel, s.device, n, action, n,
                                           controls.object_digest(action, target))
            if obj.get("fa") is None:
                return await self._ctl_res(s, r, action, False, why="passkey_required", fa=pk["id"], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(ch))
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, action, target)
        if not why and pk:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        sig = obj.get("sig") if isinstance(obj.get("sig"), str) else None
        if why:
            self.st.log("control_refused", device=s.device, action=action, reason=why)
            controls.log(self.st, action=action, device=s.device, result="refused:" + why)
            self.activity("control_refused", action=action, by=s.name or s.device, why=why)
            return await self._ctl_res(s, r, action, False, why=why)
        if action == "task_migrate":    # P570c: the signed tap is the owner's confirmation; the move + test run off this loop
            from . import sysmigrate
            controls.log(self.st, action=action, device=s.device, result="ok", obj=target, sig=sig, n=obj.get("n"), ts=obj.get("ts"))
            return self._bg(sysmigrate.phone(self, s, r, target), action)
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
        if result == "migrated":                # P570b: the phone shows an unknown code as it is — a sentence, not `migrated`
            from .sysmigrate import why as mwhy
            result = mwhy("migrated_on", card_lang(self.lang))
        await self._ctl_res(s, r, action, result == "ok", **({} if result == "ok" else {"why": result}), **extra)

    # ------------------------------------------------------------ P115: first-run checklist (first_run.py, ADR-A196)
    def setup_serve(self) -> dict:

        return {"relay_up": self.relay_up, "agent": self.agent.kind if self.agent else None}

    def push_setup(self, snap: dict, show: bool = True) -> None:

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

        from . import first_run, onboarding
        if getattr(s, "source_kind", None) in ("telegram", "wechat", "external_mcp"):
            return
        try:
            snap = await asyncio.to_thread(first_run.phone_round_trip, self.st, onboarding.kind_of(s.name or ""),
                                           getattr(s, "setup_net", None), self.setup_serve())
        except (first_run.SetupError, OSError):
            return
        if snap:
            self.push_setup(snap, show=True)

    async def on_wf(self, s: Session, t: str, obj: dict) -> None:

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

    async def on_skill_act(self, s: Session, t: str, obj: dict) -> None:

        r = obj.get("r")
        if not wire.is_rid(r):
            return
        from . import skill_usage, skills_cli
        op = "archive" if t == "skill_archive" else "restore"
        name = obj.get("name")
        if not isinstance(name, str) or not skill_usage.NAME.fullmatch(name):
            return await self._ctl_res(s, r, t, False, why="shape", name="")
        target = {"op": op, "name": name}
        why = controls.check(self.st, self.nonces, self.channel, s.device, obj, t, target)
        if why:
            self.st.log("control_refused", device=s.device, action=t, reason=why)
            controls.log(self.st, action=t, device=s.device, result="refused:" + why)
            return await self._ctl_res(s, r, t, False, why=why, name=name)
        res = await asyncio.to_thread(skills_cli.execute, self.st, {"op": op, "name": name, "by": "phone"},
                                      self.agent_cfg or {}, self.lang, getattr(self, "preferences", None))
        ok = bool(res.get("ok"))
        controls.log(self.st, action=t, device=s.device, result="ok" if ok else str(res.get("why") or "?")[:16], obj=target,
                     sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
        await self._ctl_res(s, r, t, ok, name=name, **({} if ok else {"why": str(res.get("why") or "?")[:16]}))

    async def jcard_open(self, req: dict) -> dict:

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

        r = obj.get("r")
        if not isinstance(r, str) or not _RID.fullmatch(r):
            return
        lang = obj.get("lang") if obj.get("lang") in ("zh", "en") else self.lang
        cid, on = obj.get("id"), obj.get("on")
        try:                            # whatever an Agent left under agentperm/cards, the phone gets an answer, never a closed session
            if t == "jcard_list":
                res = {"t": "jcard_items", **await asyncio.to_thread(jcards.phone_list, self.st.root, lang)}
            elif t == "jcard_get":
                res = {"t": "jcard_get", **await asyncio.to_thread(jcards.phone_get, self.st.root, cid, lang, self.agent_cfg, automation_page=obj.get("automation_page", 0))}
            elif t == "jcard_cal":
                from . import jcards_cal_remote
                res = {"t": t, **await jcards_cal_remote.phone(self, obj)}
                self.st.log("jcard_cal", device=s.device, status="ok" if res.get("ok") else "refused")
            elif t == "jcard_note":
                from . import jcards_data
                res = {"t": t, **await asyncio.to_thread(jcards_data.phone_change, self.st.root, cid,
                       obj.get("op"), obj.get("entry"), obj.get("revision"), obj.get("text"),
                       obj.get("title"), obj.get("date"))}
                self.st.log("jcard_note", device=s.device, status="ok" if res.get("ok") else "refused")
            elif t == "jpalette":
                from . import jpalettes
                res = {"t": t, **await asyncio.to_thread(jpalettes.request, self.st.root, obj)}
                self.st.log("jpalette", device=s.device, status="ok" if res.get("ok") else "refused")
            elif t == "jcard_color":
                from . import jcards_data
                res = {"t": t, **await asyncio.to_thread(jcards_data.set_color, self.st.root, cid, obj.get("color"))}
                self.st.log("jcard_color", device=s.device, status="ok" if res.get("ok") else "refused")
            elif t in ("jcard_files", "jcard_file"):   # P432: the official Files card (no-follow listing; a download = a §13 media item)
                from . import jcards_files
                c = await asyncio.to_thread(jcards.find, self.st.root, cid)
                if t == "jcard_file" and self.stopped():
                    res = {"t": t, "ok": False, "why": "stopped"}
                elif not c or not c["official"] or cid != "files" or not c["on"]:
                    res = {"t": t, "ok": False, "why": "off" if c else "not_found"}
                elif t == "jcard_files":
                    res = {"t": t, **await asyncio.to_thread(jcards_files.listing, self.st.root, self.agent_cfg, obj.get("root"), obj.get("path"))}
                else:
                    res = {"t": t, **await asyncio.to_thread(jcards_files.fetch, self.st.root, self.agent_cfg, self.media, obj.get("root"), obj.get("path"))}
                    self.st.log("jcard_file", device=s.device, status="ok" if res.get("ok") else str(res.get("why"))[:16])
            elif t == "jcard_call":     # P432: a confirmed button of a card's own server (127.0.0.1, declared route, bounded)
                from . import jcards_server
                res = ({"t": t, "ok": False, "id": None, "act": None, "why": "stopped"} if self.stopped() else   # e-stop: no side effects
                       {"t": t, **await asyncio.to_thread(jcards_server.call, self.st.root, cid, obj.get("act"), obj.get("item"), lang)})
                self.st.log("jcard_call", device=s.device, status=f"{res.get('id')}:{res.get('act')}:" + ("ok" if res.get("ok") else str(res.get("why") or "no"))[:24])
            elif t == "jcard_perm":     # P432: a plaza card's permission summary (the owner reads it before switching it on)
                from . import jcards_pkg
                c = await asyncio.to_thread(jcards.find, self.st.root, cid)
                if not c or c["official"]:
                    res = {"t": t, "ok": False, "id": None, "why": "not_found"}
                else:
                    src = await asyncio.to_thread(jcards_pkg.source, self.st.root, cid, c["doc"])
                    res = {"t": t, "ok": True, "id": cid, "text": jcards_pkg.summary_text(c["doc"], lang, src), "digest": jcards_pkg.digest(c["doc"])}
            else:
                seen = obj.get("seen") if isinstance(obj.get("seen"), str) else None
                ok = isinstance(cid, str) and type(on) is bool and await asyncio.to_thread(jcards.set_on, self.st.root, cid, on, seen)
                res = None
        except Exception as e:  # noqa: BLE001
            self.st.log("jcard_failed", device=s.device, reason=type(e).__name__)
            res = {"jcard_list": {"t": "jcard_items", "cards": []}, "jcard_get": {"t": "jcard_get", "ok": False, "why": "not_found"}}.get(
                t, {"t": t if t in ("jcard_note", "jcard_color", "jcard_cal", "jpalette", "jcard_call", "jcard_files", "jcard_file", "jcard_perm") else "jcard_res", "ok": False, "id": None, "on": None})
            ok = False
        if res is not None:
            return await self.send_app(s, {**res, "r": r})
        if ok:
            self.st.log("jcard_set", device=s.device, status=f"{cid}:{'on' if on else 'off'}")
        await self.send_app(s, {"t": "jcard_res", "r": r, "ok": bool(ok), "id": cid if ok else None, "on": on if ok else None})

    async def _wf_first_sweep(self) -> None:

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

        x = {"cmd": name, "ok": res.kind in ("ok", "info"), "kind": res.kind}
        if name=="update" and not interim and res.kind in ("ok","error"):
            x.update(repair="reconnect",system_name="AgentJ")
        if name == 'model' and self.agent is not None:
            from .harness import LABEL
            label = LABEL.get(self.agent.kind, self.agent.kind)
            if not res.text.startswith(label + ' · '):
                res.text = label + ' · ' + res.text
        if name == "clear" and not interim and res.kind == "ok" and self.agent is not None:
            kind = self.agent.kind
            x["clear_harness"] = kind
            resume = humane.clear_resume(self, kind, self.st.agent_session(kind))
            if resume:
                x["resume"] = resume
        if name == "update" and res.kind == "error" and not interim:
            x["open"] = "upgrade_repair"
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

        if not isinstance(cmd, agents.Cmd) or cmd.cancelled or cmd.native_started:
            return
        text = detail or humane.of(self, "shared.wait_idle")
        self.hist_update(cmd.turn, text=text, end="open", card={"cmd": cmd.name, "cancel_wait": True})

    def workflow_cmd_waiting(self, cmd) -> bool:

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

        src = {"k": "cmd", "text": ("/" + name + (" " + arg if arg else ""))[:200]}
        if s is not None:
            src.update(dev=s.device, name=(s.name or s.device)[:64])
        return self.hist_add(src, "", "open")["id"]

    def shared_clear(self) -> bool:

        before = self.hist.meta()["count"]
        archived = self.hist.reset("shared-clear")
        self.desktop_turn = None
        self.hist_meta_all()
        ok = not before or bool(archived)
        if not ok:
            self.agent_notice(pick(self.lang, "桌面已清空，但手机历史归档失败；请检查本机磁盘。", "Desktop cleared, but phone history archive failed; check local disk."))
        return ok

    def cmd_done(self, cmd, res: "slash.Result") -> None:

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

        if getattr(item, "background_done_ids", None):
            self.background_done.complete(item)
        if getattr(item, "heartbeat_key", None) or getattr(item, "background_done_ids", None) \
                or getattr(item, "quota_resume_id", None) or getattr(item, "auto_notify", None) or getattr(item, "hook_notice", None) \
                or getattr(item, "compact_resume", None):
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
            item.cancelled = True
            if item.name == "model_set":
                self.model_set_done(item, "stopped")
            else:
                self.hist_update(item.turn, end="stopped")

    def set_model(self, model: str | None) -> bool:

        if self.st.set_agent_model(model, expected=self.agent_cfg) is False:
            return False
        if self.agent_cfg is not None:
            self.agent_cfg["model"] = model
        return True

    async def on_slash(self, s: Session, name: str, arg: str, confirm: bool, typed: bool, authorization=None) -> int:

        if name == 'recover-background':
            from . import completion_recovery, diskq
            recovered = await completion_recovery.owner_command(self, s, '/recover-background' + (' ' + arg if arg else ''))
            recovered = recovered or {'result':'refused', 'why':'shape'}
            turn = self.cmd_turn(s, name)
            self.cmd_card(name, slash.Result(completion_recovery.human(recovered, getattr(self, 'lang', 'zh')), 'info'), s.name or s.device, turn)
            if recovered.get('result') == 'reconciled' and await diskq.aflush():
                await asyncio.to_thread(self.background_done.recovery.acknowledge, recovered['id'])
            return turn
        if name in ("stop-all", "stop_all", "estop"):
            turn = self.cmd_turn(s, name)
            self.cmd_card(name, slash.Result(pick(self.lang, "请到「设置 → 安全」操作「紧急停止所有 Agent」。", "Go to Settings → Safety → Emergency stop all Agents."), "info"), turn=turn)
            return turn
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
            if update.market_managed():
                return done(slash.Result(update.market_text(self.lang), "info"))
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
            if self.native_route(name) == "none":   # P412 #175: the harness's own, but no web page can do it — say why
                return done(slash.Result(harness_cmds.none_note(self.agent.kind, name, self.lang), "refused"))
            if self.agent.kind == "claude" and not getattr(self.agent, "init", None):
                return done(slash.Result("还不知道 Claude Code 有哪些技能：先发一条普通消息，再试一次。\n" + slash.refuse(self.lang),
                                         "refused"))
            return done(slash.Result(slash.refuse(self.lang), "refused"))
        if name == "help":
            return done(await self.agent.command("help", arg))
        if name == "stop":
            return done(await self.stop_turn(by))
        if self.stopped():
            return done(slash.Result("已急停：恢复之后再用这个命令。", "refused"))
        if name == "clear" and not confirm:
            return done(slash.Result(humane.of(self, "clear.phone_only"), "info"))
        if name == "clear":             # 0269 (#461): /clear and /new interrupt and discard the running turn, never queue
            await self.turn_heal.preempt(by, "clear")
        from . import command_queries
        if command_queries.applies(self.agent) and command_queries.readonly(name, arg):
            async def answer_query(agent):
                done(await command_queries.query(agent, name, arg))
            self._bg(answer_query(self.agent), "slash_query")
            return turn
        preempted = name == "clear"     # 0269: the running turn was interrupted above — not 「等这一轮忙完就办」
        if self.agent.status in ("working", "compacting") or not self.agent.q.empty():
            if not preempted:
                self.cmd_card(name, slash.Result(humane.of(self, "cmd.after_turn"), "info"), by, turn, interim=True)
        cmd = agents.Cmd(name, arg, by, turn)
        self.agent.submit(cmd)
        if command_queries.applies(self.agent):
            command_queries.expire_queued(self.agent, cmd)
        if not self.workflow_cmd_waiting(cmd) and isinstance(self.agent, SharedClaudeAgent) and name in ("clear", "compact"):
            self.cmd_waiting(cmd)
        return turn

    async def phone_upgrade(self, turn, by):
        if update.market_managed():
            self.cmd_card("update", slash.Result(update.market_text(self.lang), "info"), by, turn)
            return
        from . import phone_update
        launched = False
        try:
            check = await asyncio.to_thread(update.check)
            if check["status"] != "newer":
                rec = {"reason": "already_current" if check["status"] == "current" else "unknown", "to": check.get("latest") or update.__version__}
                self.cmd_card("update", slash.Result(phone_update.text(rec, self.lang), "info" if check["status"] == "current" else "error"), by, turn)
                return
            if self.agent and self.agent.status != "idle":   # 0.26.10 #484: the signed /update answered the impact card:
                self.upgrade_turn = self.turn_t0               # the restart does not wait for the turn running now
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

        lang = "en" if self.lang == "en" else "zh"
        if name == "my-agent-id":
            return friend_cmds.my_agent_id(self.st, lang, getattr(self.peers, "state", None))
        return friend_cmds.add_friend(arg, lang, channel)

    def _upgrade_wait_note(self, ready: bool, why) -> None:
        """0.26.10 #484: the upgrade recovery worker polls every 2 s; the first answer of a wait tells the phones once that
        the new version is installed and restarts when the current task ends (≤ READY_LIMIT), and why it did not wait."""
        if why and why != "busy":
            if getattr(self, "_upg_wait_why", None) != why:
                self.st.log("upgrade_wait", status="ready" if ready else "waiting", reason=why)
            self._upg_wait_why = why
        if why == "stuck_queued" and not getattr(self, "_upg_delivering", False):    # security #484 note 3
            self._upg_delivering = True

            async def deliver():
                try:
                    await auto_update.deliver_before_restart(self)
                finally:
                    self._upg_delivering = False
            from . import auto_update
            self._bg(deliver(), "upgrade_deliver")
        if ready or getattr(self, "_upg_wait_told", False):
            return
        self._upg_wait_told = True
        from . import auto_update
        self.agent_notice(pick(self.lang, *auto_update.RESTART_WAIT_TEXT))

    async def stop_turn(self, by: str, answered: bool = True) -> "slash.Result":

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

        if s is not None and not s.p33:
            return {}
        caps = [wire.CAP_P33, "heartbeat", wire.CAP_LT1, pair_card.CAP, "mailuv1", "privatefiles1", "privatefiles2"]
        if getattr(self, "telegram", None) is not None and self.telegram.can_forward():   # P118 B10: share → the owner's Telegram
            caps.append("tgfwd")
        if getattr(self, "telegram", None) is not None:   # P183: this host forwards a reply's file too (tg_fwd {mid}); the phone
            caps.append("tgfile")                          # shows the icon and, while Telegram is not linked, says where to link it
        if getattr(self, "wechat", None) is not None and self.wechat.can_forward():
            caps.append("wxfwd")
        if getattr(self, "wechat", None) is not None and self.wechat.store.data['account']:
            caps.append("wxbound")
        if getattr(self, "telegram", None) is not None and getattr(self.telegram, 'status_view', lambda: {})().get('enrolled'):
            caps.append("tgbound")
        caps.extend(("use1", "form1"))
        caps.append("busyget")                 # P160 (§22): a tap on one 「在办」 row asks for its detail
        caps.append("contacts")          # P184 (§25): address book, contact cards, 「转发到邮箱」 (mail ready or not: the dialog says)
        caps.append(hsearch.CAP)                               # P188 (§28): the Agent J host answers hist_find
        caps += ["cards1", "upg1"]       # P203 (§29): signed open-cards snapshot · upgrade impact + confirmation card
        caps.append("systasks")                                # P207 §2: answers systask_list (the Agent J host's own scheduled tasks)
        caps.append("wfresume1")                              # P498 signed exact-run resume
        caps.append("friendreport1")                          # P498 owner-signed report reply
        caps.append("wf1")                                     # P244 §30: answers wf_list / wf_doc / skill_get (the five pages)
        caps.append("jpalette1")  # P420 bounded host palette library
        caps.append("sk1")                                     # P426 §39: skill health on the 技能 page + skill_archive / skill_restore
        caps.append(jcards.CAP)                                # P360 §34: Agent J Cards (jcard_list / jcard_get / jcard_set)
        caps.append(compose.CAP_MQ)                            # P286 §10.6: takes quote_more (≤ 5 quotes in one say)
        caps.extend(("hooks1", "externalmcp1"))                                  # P366: answers hook_list / hook_set (Settings → Webhook)
        caps.append(compose.CAP_SYNC)                          # P398 §10.2: answers say_sync (queued sends after a reconnect)
        caps.append("heartbeat_prompt")  # P537: append-only capability for prompt editing
        with contextlib.suppress(Exception):     # 0269-w2-assist (§41): only while a pinned official key ships (placeholder = off)
            if self.assist.available():
                caps.append(assist_mod.CAP)
                if self.term_desk.pty_supported():
                    from .assist_terminal import CAP as assist_term_cap
                    caps.append(assist_term_cap)
        with contextlib.suppress(Exception):     # 0.26.9 (§38.15, ADR-0269-pty): the web terminal also runs as a real PTY
            if self.term_desk.pty_supported():
                caps.append(term_mod.PTY_CAP)
        m = {"caps": caps, "asr": self.asr_state(), "hist": "on" if self.hist.on else "off"}
        stall = getattr(self, "_recovery_stall", None)
        if stall is not None:
            m["recovery_stall"] = {"age_ms": max(0, int((time.monotonic() - stall[0]) * 1000)), "ms": stall[1]}
        m.update(self._host_kind())
        return m

    def _host_kind(self) -> dict:

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
            from . import display_privacy
            text=await asyncio.to_thread(display_privacy.text,self,text)
            import hashlib, json, time as _time
            cache_key=hashlib.sha256(json.dumps([text,self.preferences.get("voice",{})],sort_keys=True).encode()).digest()
            cached=getattr(self,"tts_cache",None)
            if cached and cached[0]==cache_key:
                audio=cached[1]
            else:
                # P208: a local model / cloud voice reads the spoken script (rewritten by the AI tool's mid-tier model,
                # cached on disk); rewrite off or failed → the reply's plain words. Every reading lands in the usage log.
                from . import spoken, voice_engines
                t0=_time.monotonic()
                sc=await asyncio.to_thread(spoken.script,self.st,text,self.preferences,(getattr(self,"agent_cfg",None) or {}).get("kind"))
                sc={**sc,"text":await asyncio.to_thread(display_privacy.text,self,sc["text"])}
                audio=await asyncio.to_thread(voice.synthesize,sc["text"][:4000],self.preferences)
                self.tts_cache=(cache_key,audio)
                voice_engines.record(self.st,seconds=_time.monotonic()-t0,engine=voice_engines.engine_of(self.preferences),
                                     chars=len(sc["text"][:4000]),lang=sc["lang"],rewrite=sc)
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

        r = obj.get("r")
        if not wire.is_rid(r):
            return
        ar = self.auth_rescue
        if t == "auth_rescue":             # 0269-login: signed; its answers go to this device only (login_flow.py)
            return await ar.login.on_phone(s, obj)

        async def answer(res: dict) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "auth_res", "r": r, "ok": bool(res.get("ok", True)),
                                        **({"why": res["why"]} if res.get("why") else {}),
                                        **({"entitlements": "NOT_VERIFIED", "restart": res["restart"]}
                                           if res.get("restart") == "next_new_turn" else {}),
                                        "view": await asyncio.to_thread(ar.view)})
        kind, route, bid = obj.get("kind"), obj.get("route"), obj.get("id")
        if t == "auth_get":
            await ar.refresh_health(force=obj.get("check") is not False)
            return await answer({"ok": True})
        if t == "auth_cancel":
            return await answer(await ar.cancel_flow())
        if not isinstance(kind, str) or len(kind) > 16:
            return await answer({"ok": False, "why": "unsupported"})
        self.st.log("auth_rescue_req", device=s.device, agent=kind, status=t)
        if t == "auth_start":
            acct = obj.get("account")              # #427: {"name"} = a new account's own slot, never over the one in use

            async def go():
                with provenance.paired_request(lambda: self._paired_live(s)):
                    if kind == "codex" and route == "quota_recheck" and acct is None:
                        from .quota_refresh import recheck
                        await answer(await recheck(self, s, r))
                    else:
                        await answer(await ar.start(kind, route if isinstance(route, str) else "", remote=s,
                                                    account=acct if acct is not None else None))
            self._bg(go(), "auth_start")     # the passkey card waits for the owner; never hold the relay reader
            return
        if t == "auth_press":
            async def go2():
                await answer(await ar.press(kind, bid if isinstance(bid, str) else ""))
            self._bg(go2(), "auth_press")
            return

    def on_acct(self, s: Session, t: str, obj: dict) -> None:

        r = obj.get("r")
        if not wire.is_rid(r) or t not in ("acct_get", "acct_use", "acct_rename", "acct_del"):
            return
        from . import accounts

        def live() -> bool:
            return self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub)

        async def go():
            if not live():
                return
            res = await accounts.phone(self, s, t, obj)
            if live():
                await self.send_app(s, {"t": "acct_res", "r": r, **res})
        self._bg(go(), "acct")          # a remove waits for the owner's card; never hold the relay reader

    def auth_signal(self, kind: str, text) -> None:

        try:
            self.auth_rescue.text_signal(kind, text)
        except Exception:  # noqa: BLE001 — detection must never break the failure notice itself
            pass
    async def on_imsg(self, s: Session, t: str, obj: dict) -> None:

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
            self._post(self._send_ready, lambda x: self.preferences_msg(x))
        self._bg(run(), t)

    def on_news(self, s: Session, t: str, obj: dict) -> asyncio.Task | None:

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

    async def on_tts_script(self, s, obj):
        """P208 (PROTOCOL §10.14a): `tts_script {r,id}` → `tts_script_res {r,ok,text?,spoken?,why?}` — the spoken script of one
        completed reply, for the phone's own voices when `voice.tts.spoken` is on. Same checks as tts_get; one at a time."""
        from . import spoken
        r=obj.get("r")
        if self.sessions.get(s.cid) is not s or s.state!="ready" or not self.st.is_allowed(s.pub) or not wire.is_id22(r):
            return
        turn=self.hist.get(obj.get("id"))
        if not turn or turn.get("end") not in ("done","stopped","failed"):
            return await self.send_app(s,{"t":"tts_script_res","r":r,"ok":False,"why":"not_ready"})
        if getattr(self,"tts_script_busy",False):
            return await self.send_app(s,{"t":"tts_script_res","r":r,"ok":False,"why":"busy"})
        self.tts_script_busy=True
        try:
            text=turn.get("reply",{}).get("text","")
            from . import display_privacy
            text=await asyncio.to_thread(display_privacy.text,self,text)
            sc=await asyncio.to_thread(spoken.script,self.st,text,self.preferences,(getattr(self,"agent_cfg",None) or {}).get("kind"))
            if sc["spoken"] and not sc["cached"]:
                from . import voice_engines
                voice_engines.record(self.st,seconds=0,engine="phone",chars=0,lang=sc["lang"],rewrite=sc)
            if self.sessions.get(s.cid) is s and self.st.is_allowed(s.pub):
                await self.send_app(s,{"t":"tts_script_res","r":r,"ok":True,"text":sc["text"][:8000],"spoken":sc["spoken"]})
        except Exception:
            self.st.log("tts_script_failed")
            await self.send_app(s,{"t":"tts_script_res","r":r,"ok":False,"why":"engine"})
        finally:
            self.tts_script_busy=False

    async def voice_ctl(self, req: dict) -> dict:
        """P208: `agentj voice` asks the running service (its environment holds the provider keys). Names in, yes/no out;
        voices without keys; a test reading written to the caller's new .wav file."""
        from . import voice_engines
        cmd=req.get("cmd")
        if cmd=="voice_keys":
            names=[n for n in (req.get("names") or [])[:20] if isinstance(n,str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}",n)]
            return {"ok":True,"keys":{n:bool(os.environ.get(n)) for n in names}}
        if cmd=="voice_list":
            if req.get("engine")!="elevenlabs":
                return {"ok":False,"error":"unknown engine"}
            env=preferences.get(self.preferences,"voice.tts.key_env") if voice_engines.engine_of(self.preferences)=="elevenlabs" else None
            return await asyncio.to_thread(voice_engines.list_elevenlabs,os.environ.get(env or voice_engines.KEY_ENV["elevenlabs"]))
        if cmd=="voice_test":
            if getattr(self,"tts_busy",False):
                return {"ok":False,"error":"busy"}
            self.tts_busy=True
            try:
                return await asyncio.to_thread(voice_engines.run_test,self.st,req.get("say"),str(req.get("out") or ""),
                                               self.preferences,(getattr(self,"agent_cfg",None) or {}).get("kind"))
            finally:
                self.tts_busy=False
        return {"ok":False,"error":"unknown"}

    async def on_mail(self, s: Session, t: str, obj: dict) -> None:

        from . import mail as mail_mod
        r = obj.get("r")
        if not wire.is_rid(r) or self.mail is None:
            return

        async def answer(ok: bool, why: str | None = None, **extra) -> None:
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                await self.send_app(s, {"t": "mail_res", "r": r, "ok": ok, **({"why": why} if why else {}), **extra,
                                        "view": await asyncio.to_thread(self.mail.view, include_external=True)})
        if t == "mail_get":
            return await answer(True)
        if t in ("mail_set", "mail_test") and (why := self._policy_write(s, t, obj)):
            return await answer(False, why)
        if t == "mail_set":
            raw = obj.get("cfg")
            if not isinstance(raw, dict) or len(json.dumps(raw)) > 8000:
                return await answer(False, "shape")
            try:
                old = mail_mod.read_config(self.st)
                new = action_policy.mail_config(old, raw)
                cfg = self.mail.set_config(new)
            except mail_mod.MailError as e:
                return await answer(False, e.why)
            self.st.log("mail_set", device=s.device, allow=len(cfg["allow"]), enabled=cfg["enabled"])
            return await answer(True)
        if t == "mail_key":                 # one answer, when the card ends (the page waits up to the card's 10 minutes)
            async def card():
                with provenance.paired_request(lambda: self._paired_live(s)):
                    res = await self.mail.secret_card(recover=False, only=s.device, settings=True)
                receipt = res.get("receipt") or {}
                await answer(res.get("result") == "saved", None if res.get("result") == "saved" else
                             (res.get("detail") or res.get("why") or res.get("result")), card=res.get("result"), verify=receipt.get("verify"), detail=receipt.get("detail"))
            self._bg(card(), "mail_key")
            return
        if t == "mail_test":
            cfg = mail_mod.read_config(self.st)
            async def test():
                if mail_mod.read_config(self.st) != cfg:
                    return await answer(False, "stale")
                pw = mail_mod.load_secret(self.st, cfg["address"]) if cfg["address"] else None
                if not pw:
                    return await answer(False, "no_password")
                res = await asyncio.to_thread(mail_mod.mail_recovery.run, self, mail_mod.test_connection, cfg, pw)
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

        from . import mail as mail_mod
        if self.mail is None:
            return await self._ctl_send(w, {"ok": False, "error": "not_running"})
        old = mail_mod.read_config(self.st)
        try:
            # 0304 A4 r2 N1: the server runs the same servid merge, so a raw ctl clear still records the opt-out.
            new = mail_mod.validate(mail_mod.servid_merge(old, mail_mod.validate({**old, **{k: v for k, v in (req.get("cfg") or {}).items() if k != "generation"}})))
        except mail_mod.MailError as e:
            return await self._ctl_send(w, {"ok": False, "error": e.why})
        keys = mail_mod.widening(old, new)
        if old != new and not keys:
            keys = ["configuration"]
        if not keys:
            cfg = self.mail.set_config(new)
            self.st.log("mail_set", device="terminal" if claimed else "agent", allow=len(cfg["allow"]), enabled=cfg["enabled"])
            return await self._ctl_send(w, {"ok": True, "applied": True})
        self.st.log("mail_card", keys=",".join(keys)[:200])
        zh = card_lang(self.lang) != "en"
        ans = await self.ask("agentj mail", {"changes": keys, "before": old, "after": new}, batch=False,
                             always_ask=True)   # P573: one button (was Face ID); still asked under 全放行 (who reaches the Agent)
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
        try:
            if self.telegram is None:
                res = {"ok": False, "why": "not_configured"}
            elif mid is not None:      # P183: one file a reply showed (its §13 media id) instead of the reply's words
                res = await self.telegram.forward_media(mid, s.device) if wire.is_id22(mid) else {"ok": False, "why": "not_found"}
            else:
                res = await self.telegram.forward(turn_id, s.device)
        except Exception as e:  # noqa: BLE001 — P424 #142: the phone always gets an answer, never a spinner until its timeout
            self.st.log("telegram_forward", device=s.device, result="error", exception_type=type(e).__name__)
            res = {"ok": False, "why": "send_failed"}
        if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
            await self.send_app(s, {"t": "tg_fwd_res", "r": r, **{k: v for k, v in res.items() if k in ("ok", "why", "parts")}})

    async def on_busy_get(self, s: Session, r: str, wid: str) -> None:
        from . import busy as busy_mod
        f = self.busy.find(wid)                          # copies, here on the loop (the Board changes only here)
        try:
            d = await asyncio.to_thread(busy_mod.detail_of, *f) if f else None
        except Exception:  # noqa: BLE001 — display only
            d = None
        from .core.order_view import project
        d = await asyncio.to_thread(project, self, wid, d)
        if d is not None and f and f[0]["key"].startswith("wf:"):   # P204 (§29): a workflow robot shows its last run's spend
            with contextlib.suppress(Exception):
                last = (self.scheduler.last_of(f[0]["key"][3:]) or {}).get("use")
                if isinstance(last, dict):
                    d["use"] = last
        if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
            await self.send_app(s, {"t": "busy_info", "r": r, "id": wid, "item": d})

    async def on_p33(self, s: Session, t: str, obj: dict) -> None:
        if t == "web_preview_attest":  # #547: read-only metadata, existing authenticated Noise channel only
            if not wire.is_rid(obj.get("r")) or self.sessions.get(s.cid) is not s or s.state != "ready":
                return
            from . import web_preview
            signature = await asyncio.to_thread(web_preview.attest, self.st, self.channel, s.pub, obj.get("message"))
            if self.sessions.get(s.cid) is s and s.state == "ready" and self.st.is_allowed(s.pub):
                self.st.log("preview_attest", device=s.device, ok=signature is not None)
                await self.send_app(s, {"t": "web_preview_proof", "r": obj["r"], "ok": signature is not None,
                                        **({"signature": signature} if signature else {})})
            return
        if t in ("form_get", "form_draft"):
            if not self._paired_live(s):
                return
            return await self.forms.on_phone(s, obj)
        if t in ("preauth_list", "preauth_revoke"):   # P539
            return await self.on_preauth(s, t, obj)
        if t in ("automation_grants", "automation_grant_revoke"):   # 0304 A3: same narrowing-only control as preauth
            from . import automation_grant
            return await automation_grant.on_phone(self, s, t, obj)
        if t == "ark_plan_get":
            if wire.is_rid(obj.get("r")):
                from . import ark_plan_fix
                await self.send_app(s, {"t":"ark_plan", "r":obj["r"], **await asyncio.to_thread(ark_plan_fix.view, self.st)})
            return
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
        if t == "tts_script":              # P208 (§10.14a): the spoken script for the phone's own voices (spoken = on)
            self._bg(self.on_tts_script(s, obj), "tts_script")
            return
        if t.startswith("mail_"):          # P170: the e-mail channel's settings page
            return await self.on_mail(s, t, obj)
        if t in ("news_get", "news_set", "news_email", "news_verify"):  # P374 · P393 #128: Daily AI News
            return self.on_news(s, t, obj)
        if t.startswith("auth_"):          # P258: AI 登录救援
            return await self.on_auth(s, t, obj)
        if t.startswith("acct_"):          # #427: several accounts per harness
            return self.on_acct(s, t, obj)
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
        if t == "wx_fwd":
            r = obj.get("r")
            if wire.is_rid(r):
                from .wechat_forward import on_forward
                self._bg(on_forward(self, s, r, obj.get("id")), "wx_fwd")
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
            self.cancel_waiting_cmd(s, obj.get("turn"))
            return
        if t == "turn_wait":
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
                if getattr(send, "form_id", None):       # P212: a withdrawn answer re-opens its form
                    self.forms.reopen(send.form_id)
                self.st.log("say_cancel", cid=s.cid, device=s.device, result=r)
                self._post(self._send_device, s.device, {"t": "say_state", "sid": sid, "s": "cancelled"})
            await self.send_app(s, {"t": "say_cancel_res", "sid": sid, "r": r})
            return
        if t == "say_sync":
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
            kind = self.agent.kind if self.agent else (self.agent_cfg or {}).get("kind")
            native = harness_cmds.for_menu(kind, getattr(self.agent, "init", None) if self.agent else None,
                                           [x["cmd"][1:] for x in res.get("skills") or []], self.lang)   # P412 (#175)
            return await self.send_app(s, {"t": "menu", "r": r, **res, "upgrade": update.phone_view(self.st), "reload": True,
                                           "reset": True, "kind": kind if isinstance(kind, str) else None,
                                           "native": native})   # P320: ≡ Reload · P332: ≡ 重置连接
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
                if not ok:
                    return await self.send_app(s, {"t": "provider_res", "r": r, "ok": False, "why": "unsupported"})
                async def provider_card():
                    replied = False
                    async def opened(c):
                        nonlocal replied
                        if self._paired_live(s):
                            replied = await self.send_app(s, {"t": "provider_res", "r": r, "ok": True,
                                                              "card": self.elevate.card_msg(c, s)})
                    res = await provider_runtime.request_key(self, pid, session=s, on_card=opened, settings=True)
                    if not replied:
                        await self.send_app(s, {"t": "provider_res", "r": r, "ok": False, "why": res.get("result", "failed")})
                self._bg(provider_card(), "request_key")
                return
            delivery = getattr(self, "_asr_install_delivery", None)
            if getattr(self, "_asr_installing", False) or (delivery is not None and not delivery.done()):
                return await self.send_app(s, {"t": "asr_install_res", "r": r, "ok": False, "why": "busy"})
            self._asr_installing = True
            async def install_asr():
                stage = "prepare"
                loop = asyncio.get_running_loop()
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
                    self._asr_installing = False
                    enqueue(result)
            self._bg(install_asr(), "install_asr")
            return
        if t == "models_get":
            r = obj.get('r')
            if not wire.is_rid(r):
                return
            agent = self.agent
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
                self._catalogue_refresh_task = asyncio.create_task(refresh_catalogue())
            return
        if t == "model_set":
            return await self.on_model_set(s, obj)
        if t == "q_answer":
            return self._q_answer(s, obj)

    # ------------------------------------------------------------ say (§10.2)
    async def _accept(self, s: Session, text: str, sid: str | None, blobs: list | None = None, quote: dict | None = None,
                      quote_text: str | None = None, agent_text: str | None = None, extra_src: dict | None = None) -> compose.Send | None:

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
            from .telegram import OWNER_PRIVATE
            src["text"]=text.removeprefix(OWNER_PRIVATE)
        elif getattr(s,"source_kind",None)=="email":     # P170: the page shows 「邮件」 and the mail's own words
            src["k"]="email"
            src["text"]=text.split("\n",2)[-1] if text.startswith("【") else text
        elif getattr(s, 'source_kind', None) == 'external_mcp':
            src['k'] = 'external_mcp'
        elif getattr(s,"source_kind",None)=="wechat":    # P562: the page shows 「微信」 and the owner's own words
            src["k"]="wechat"
            src["text"]=text.split("\n",1)[-1] if text.startswith("【") else text
        if quote:
            src["quote"] = quote
        if blobs:
            src["att"] = [{"name": b.name[:128], "mime": b.mime, "bytes": b.size, "kind": b.kind} for b in blobs]
        if extra_src:
            src.update(extra_src)
        turn = self.hist_add(src, "", "open" if self.agent else "done")
        from .report_actions import phone_source
        if phone_source(self, s) is not None:
            note = await asyncio.to_thread(self.background_done.recovery.notice)
            if note:
                self.agent_notice(note)
        if not self.agent:
            return None
        send = compose.Send(s.device, sid or secrets.token_urlsafe(16), turn["id"], blobs=blobs, by=s.name or s.device)
        event_guard = getattr(s, "event_delivery_allowed", None)
        if callable(event_guard):  # host-only fixed subscription binding; external JSON cannot supply this callable
            send.delivery_allowed = event_guard
            if not event_guard():
                return None
        if extra_src and isinstance(extra_src.get("form"), dict):
            send.form_id = extra_src["form"]["id"]  # visible before queueing / model-switch awaits
        from . import approval_context
        send.approval_origin = approval_context.remember(self, turn, getattr(s, "provenance", None))
        send.verified_mail_notice = getattr(s, "verified_mail_notice", False) is True
        send.onboarding_note = (self.speaker_note(s) if getattr(s, "source_kind", None) != "telegram" else "") + self.onboarding_note()
        if not slash.parse(text) and provenance.is_owner(getattr(s, "provenance", None)):
            send.onboarding_note += await self.key_import_note()
        send.onboarding_note += self.skill_turn_note(s, text)   # P426 #181: skill use + "save it as a skill?" (once)
        gc = slash.parse(text) if not blobs and agent_text is None else None
        if gc and gc[0] == "goal" and gc[1] and not goal_mod.host_cmd(gc[1]):   # P158: `/goal <what>` → the Agent proposes
            if provenance.is_owner(getattr(s, "provenance", None)):
                self.goals.owner_asked()       # P336: only the owner's own /goal may start without the opening card
            send.onboarding_note += goal_mod.start_note(gc[1], card_lang(self.lang))
            text = goal_mod.agent_words(gc[1], card_lang(self.lang))     # P198: never Claude Code's own /goal
        elif gc and gc[0] == "learn":                    # P204: `/learn [note]` → the Agent distils a skill
            from . import learn as learn_mod
            text = learn_mod.agent_words(gc[1], card_lang(self.lang))
        send.approval_origin = approval_review.remember(self, turn, getattr(s, "provenance", None))
        send.source = getattr(s, "provenance", None)      # B1: set only by host code (telegram.incoming), never from text
        if self.peers is not None:
            await asyncio.to_thread(self.peers.xfer.admit, send, text, quote)
        files = [{"path": b.path, "mime": b.mime, "bytes": b.size, "origin": b.origin, "secs": b.secs,
                  "voice": getattr(b, "voice", "")} for b in blobs]
        voice = [f for f in files if f["voice"] or (f["origin"] == "recording" and f["mime"].startswith("audio/"))]
        if agent_text is None:
            text = self._meeting_text(s, text, bool(voice))
        if agent_text is not None:
            send.text = agent_text
        elif not files and (native := slash.parse(text)) and self.native_route(native[0]) == "run" and not self.agent.passthrough(native[0]):
            send.text = text  # exact native command/arguments; onboarding or quote prose is not an API argument
        elif voice:
            send.ready = asyncio.get_running_loop().create_future()
            send.prep = asyncio.create_task(self._prep_say(send, text, files, voice, quote_text))
        else:
            send.text = compose.render(text, files, self.lang, quote_text, native=self.native_text) + send.onboarding_note
        self.sends.add(send)
        self.turn_by = s.name or s.device
        self.quota_resume.owner_spoke("main")   # P314: the owner moved on by hand; no automatic continue on top
        await self._model_set_flush()        # P240: a pill switch still held by the 2 s backstop goes ahead of this message
        if callable(event_guard) and not event_guard():
            send.state = "cancelled"
            return None
        self.agent.submit(send)
        return send

    def _meeting_text(self, s: Session, text: str, has_audio: bool) -> str:

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
        if obj.get("form") is not None and not self._paired_live(s):
            return await res(ok=False, why="not_paired")
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
        form = obj.get("form")
        is_meeting = meeting.parse(text) is not None and form is None   # P182: /会议总结 · /meeting-notes is a message for the Agent, never refused
        hc = slash.parse(text) if not att and reply_to is None and form is None and not is_meeting else None
        if hc and (friend_cmds.name_of(hc[0]) or hc[0] in TG_PAIR_CMDS or hc[0] in RELOAD_CMDS or hc[0] in reset_mod.RESET_CMDS
                   or hc[0] == "recover-background" or hc[0] == "goal" and goal_mod.host_cmd(hc[1])):
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
        if not att and reply_to is None and form is None:
            from . import quota_refresh
            if quota_refresh.upgrade_intent(self, text):
                live = quota_refresh.owner_bound(self, s, self.agent)
                self.sends.add(compose.Send(s.device, sid, 0, state="delivered"))
                await _rcpt_recv(self, s.device, sid)
                _rcpt(self, s.device, sid, "done")
                self._bg(quota_refresh.upgraded(self, s, text, sid, _bound=live), "quota_upgrade")
                return await res(ok=True, state="delivered")
        cmd = slash.parse(text) if not att and reply_to is None and form is None and not is_meeting else None
        if cmd and cmd[0] != "goal" and cmd[0] not in slash.AGENT_CMDS and not self.agent.passthrough(cmd[0]) \
                and self.native_route(cmd[0]) not in ("run", "agent"):   # P155 /switch-harness, P158 `/goal <what>`, P412 #175 the harness's own → the Agent
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
        await _rcpt_recv(self, s.device, sid)
        from . import main_thread
        main_thread.owner_queued(self)
        frec = None
        if form is not None:
            from .forms import FormError
            try:
                frec = self.forms.take(form, att)
            except FormError as e:
                return await res(ok=False, why="form_" + e.why)
        try:
            blobs = self.uploads.claim(s.device, att)
        except uploads.Refused as e:
            return await res(ok=False, why=e.why, **({"att": e.att} if e.att else {}))
        if frec is not None:
            text = self.forms.accept(s, frec, form, blobs, self.uploads.workdir)
        send = await self._accept(s, text, sid, blobs, quote, quote_text,
                                  extra_src={"form": {"id": frec["id"], "title": frec["spec"]["title"][:80]}} if frec else None)
        if frec is not None and send is not None:
            send.form_id = frec["id"]
        _rcpt(self, s.device, sid, "recv", send.turn)
        self._post(self.send_app, s, {"t": "say_res", "sid": sid, "ok": True, "state": "queued", "turn": send.turn})
        if send.prep is not None:
            self._post(self._send_device, s.device, {"t": "say_state", "sid": sid, "s": "transcribing"})

    async def bang_turn(self, s: Session, sid: str, line: str, out: str, cut: bool, hidden: int, state: str, nonce: str) -> int:

        from .term_bang import agent_text
        extra = {"term": {"out": out, "cut": bool(cut), "hidden": int(hidden), "state": state, "sid": sid}}
        text = "!" + line
        page_only = (provenance.untrusted_turn(self.agent) or not self.agent or self.stopped() or self.seat_refusal()
                     or self.sends.used(s.device, sid) or self.sends.full(s.device))
        if page_only:
            src = {"k": "phone", "dev": s.device, "name": (s.name or s.device)[:64], "text": text, **extra}
            self.st.log("term_bang_page", device=s.device, reason="page_only")
            return self.hist_add(src, "", "done")["id"]
        send = await self._accept(s, text, sid, agent_text=agent_text(line, out, state, cut, hidden, nonce), extra_src=extra)
        return send.turn

    async def _prep_say(self, send: compose.Send, text: str, files: list, voice: list, quote_text: str | None) -> None:

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
            send.text = compose.render(text, files, self.lang, quote_text, native=self.native_text) + getattr(send, "onboarding_note", "")
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
        deadline = time.monotonic() + budget
        d = self.uploads.root / device
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        texts, why, cut, notice = [], None, False, None
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
            notice = res.get("notice") or notice
            if res["ok"]:
                texts.append(res["text"])
            elif res["why"] != "no_speech":
                why = res["why"]
                break
            if size < 44 + FF_SEG_SECS * 32_000 - 64_000:          # shorter than a full segment: that was the last one
                break
        if texts:
            out = {"ok": True, "text": "\n".join(texts), "secs": f.get("secs"), **({"notice": notice} if notice else {})}
            if why or cut:
                out["partial"] = "cut" if cut else why
            return out
        return {"ok": False, "why": why or "no_speech", "secs": f.get("secs"), **({"notice": notice} if notice else {})}

    def _transcript_file(self, device: str, f: dict) -> str:

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
            return {"ok": True, "text": wire.well_formed(r["text"]), "secs": secs, **({"notice": r["notice"]} if r.get("notice") in ("local_fallback", "remote_unavailable") else {})}
        return {"ok": False, "why": "no_speech" if r.get("ok") else ASR_WHY.get(r.get("reason"), "broken"), "secs": secs, **({"notice": r["notice"]} if r.get("notice") in ("local_fallback", "remote_unavailable") else {})}

    async def _ffmpeg(self, ff: str, demux: str, src: str, dst: str, timeout: float, start: float = 0,
                      secs: float = FF_MAX_SECS) -> str:

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

        if getattr(send, "background_done_ids", None):
            self.background_done.complete(send)
            self.st.log("background_done_delivery", count=len(send.background_done_ids), outcome="delivered")
            return
        if getattr(send, 'heartbeat_key', None):
            self.st.log("heartbeat_delivery", outcome="delivered")
            return
        if getattr(send, 'compact_resume', None):      # #0303: compactprep.resume — no phone, no attachments
            self.st.log("compact_resume_delivery", outcome="delivered")
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

    def msg_steered(self, send: compose.Send, uncertain: bool = False) -> None:
        """0269-w2-steer (#471, PROTOCOL §10.2a): the owner's message went into the running Codex turn — its page is closed
        as absorbed (card.steered = the running turn's page) and every phone hears `msg_steered {mid, turn}`. `uncertain`
        (security-r2 N-4, P33-X08): written but Codex never confirmed — never re-sent; the page says so."""
        into = self.cur_turn
        self.st.log("msg_steered", device=send.device, id=send.turn, into=into, **({"uncertain": True} if uncertain else {}))
        page = self.hist.get(send.turn)
        card = {**((page or {}).get("card") or {}), "steered": into if into is not None else True, **({"uncertain": True} if uncertain else {})}
        self.hist_update(send.turn, end="done", card=card, **({"text": humane.of(self, "steer.uncertain")} if uncertain else {}))
        obj = {"t": "msg_steered", "mid": send.turn, "turn": into, **({"uncertain": True} if uncertain else {})}
        self._post(self._send_p33, lambda s: obj)
        if uncertain:     # like say_sync's answer for an uncertain send: no 「取消」 any more (say_cancel → already_delivered)
            self._post(self._send_device, send.device, {"t": "say_state", "sid": send.sid, "s": "delivered"})

    def say_uncertain(self, send: compose.Send) -> None:

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
        if getattr(send, 'compact_resume', None):      # #0303: never replayed
            self.st.log("compact_resume_delivery", outcome="uncertain")
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
            notices.processed(self.st, send.official_notice_id)
            self.official_pending.pop(send.official_notice_id, None)
            self.st.log("notice_delivery_uncertain", id=send.official_notice_id)
            return
        self.uploads.commit(send.blobs)
        _rcpt(self, send.device, send.sid, "deliv", send.turn)
        self.st.log("say_uncertain", device=send.device, id=send.turn)

    # ------------------------------------------------------------ voice (§10.9)
    def asr_state(self) -> str:

        if preferences.get(self.preferences, "voice.asr.mode") == "custom":
            return "ready"  # Reachability is tested on the take; failures can still use local fallback.
        if preferences.get(self.preferences, "voice.asr.mode") == "cloud":
            from . import voice
            return "ready" if voice.has_key(preferences.get(self.preferences, "voice.asr.key_env")) else "broken"
        try:
            v = self.asr.ready_state(self.st.root, engine_override=preferences.get(self.preferences,"voice.asr.engine"))
        except Exception:  # noqa: BLE001
            return "broken"
        return v if v in ("ready", "not_installed", "off", "broken") else "broken"

    async def _transcribe(self, path: str, timeout: float, local_only: bool = False) -> dict:

        if self.asr_lock is None:
            self.asr_lock = asyncio.Lock()
        async with self.asr_lock:
            try:
                from . import voice
                if not local_only:
                    r = await asyncio.wait_for(asyncio.to_thread(voice.transcribe, path, self.preferences, timeout,
                                                                         state_dir=self.st.root, local_asr=self.asr), timeout + 5)
                else:
                    r = await asyncio.wait_for(asyncio.to_thread(self.asr.transcribe, path, timeout_s=timeout,
                                                                        state_dir=self.st.root, engine_override=preferences.get(self.preferences, "voice.asr.engine")), timeout + 5)
            except asyncio.TimeoutError:
                return {"ok": False, "reason": "timeout"}
            except Exception:  # noqa: BLE001
                return {"ok": False, "reason": "engine_failed"}
        return r if isinstance(r, dict) else {"ok": False, "reason": "engine_failed"}

    async def _asr_take(self, device: str, b) -> None:

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
                if r.get("notice") in ("local_fallback", "remote_unavailable"):
                    res["notice"] = r["notice"]
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
        if getattr(self, "heartbeat", None) is not None:
            m["heartbeat_usage"] = self.heartbeat.usage_view()
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
        from .harness import LABEL
        return {"t": "models", "harness": LABEL.get(a.kind, a.kind) if a else None, "models": models[:40], "effort": a.cur_effort() if a else None,
                "default": {"model": dm if isinstance(dm, str) else None, "effort": de if isinstance(de, str) else None}}

    def models_changed(self) -> None:
        m = self._models_msg()
        self._post(self._send_p33, lambda s: m)

    def set_effort(self, effort: str | None) -> bool:
        if self.st.set_agent_effort(effort, expected=self.agent_cfg) is False:
            return False
        if self.agent_cfg is not None:
            self.agent_cfg["effort"] = effort
        return True

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
            m = {"t": "model_res", "r": d.get("r"), "ok": why in (None, "queued"), **({"why": why} if why else {})}
            self._post(self.send_app, s, m)
        self.models_changed()

    # ------------------------------------------------------------ questions (§10.7): an answer is an approval
    def _question_msg(self, q: Question) -> dict:
        m = {"t": "question", "id": q.qid, "qs": q.qs, "text_max": 2000, "ttl": max(0, int(q.deadline - time.monotonic()))}
        if q.task:
            m["task"] = q.task[:80]
        return m

    async def question(self, qs: list, gone: asyncio.Future | None = None, task: str | None = None) -> tuple[str, list | None]:

        kind = self.agent.kind if self.agent else "?"
        qid = secrets.token_hex(16)
        digest = approvals.question_digest(qs)
        base = dict(qid=qid, agent=kind, q_sha256=digest)
        if self.stopped():
            approvals.record_question(self.st, **base, decision="stopped", reason="estop")
            return "stopped", None
        if approval_level.level(self.preferences, self.st) == "harness":
            approvals.record_question(self.st, **base, decision="autonomous", reason="level_harness")
            return "autonomous", None
        if not self._approvers() or sum(1 for q in self.questions.values() if not q.fut.done()) >= MAX_QUESTIONS:
            approvals.record_question(self.st, **base, decision="no_device", reason="no_device")
            return "no_device", None
        q = Question(qid, qs, digest, time.monotonic() + Q_TTL, asyncio.get_running_loop().create_future(), task)
        self.questions[qid] = q
        self._interrupt("questions")
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

        from . import refusal_flag      # P539 (#299): the model-switch question in plain words, answered in its own labels
        local = refusal_flag.localize(tool_input, getattr(self, "lang", "zh"))
        card = approvals.norm_questions(local[0] if local else tool_input.get("questions"))
        if card is None:
            return {"behavior": "deny", "message": Q_TIMEOUT}
        decision, picks = await self.question(card, gone, task=self.task_label)
        if decision == "answer" and picks:
            ans = {}
            for q, p in zip(card, picks):
                labels = [p] if isinstance(p, str) else [q["o"][n - 1]["l"] for n in p]
                ans[q["q"]] = ", ".join(labels) if q["m"] else labels[0]
            if local:
                ans = local[1](ans)
                if refusal_flag.stayed(ans, local[2]):
                    refusal_flag.after_stay(self, self.agent, local[2])
            return {"behavior": "allow", "updatedInput": {**tool_input, "answers": ans}}
        if decision == "autonomous":
            return {"behavior": "deny", "message": AUTONOMOUS_CHOICE}
        if decision == "cancel":
            return {"behavior": "deny", "message": Q_CANCEL}
        return {"behavior": "deny", "message": Q_TIMEOUT}

    # ------------------------------------------------------------ housekeeping
    async def _scratch_sweep(self) -> None:

        from . import scratch
        try:
            gone = await asyncio.to_thread(scratch.sweep)
        except Exception as e:  # noqa: BLE001 — a sweep never blocks serve
            self.st.log("scratch_sweep_failed", reason=type(e).__name__)
            return
        if gone:
            self.st.log("scratch_leftover_removed", result=len(gone))

    async def sweep_loop(self) -> None:

        last_retain = last_voice = 0.0
        while True:
            with contextlib.suppress(Exception):
                self.uploads.sweep()
            if (time.monotonic() - last_voice > VOICE_EVERY or not last_voice) and self.agent_cfg:
                last_voice = time.monotonic()
                with contextlib.suppress(Exception):
                    gone = await asyncio.to_thread(inbox.reap_voice, self.agent_cfg["dir"], self.st,
                                                   inbox.voice_hours(self.preferences), self.uploads.staged_paths(),
                                                   log=self.st.log)
                    if gone:
                        self.st.log("inbox_voice_reap", count=len(gone), bytes=sum(x[2] for x in gone))
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
            if did in visible or did not in self.st.devices() or not webpush.wants(sub, kind):
                continue
            if now - self.push_last.get((did, kind), -1e9) < PUSH_GAP[kind]:
                continue
            self.push_last[(did, kind)] = now
            fut = in_daemon_thread(webpush.send, self._push_key(), sub, kind, "high" if kind in ("ask", "security") else "normal")
            generation = self.push_generation.get(did, 0)
            fut.add_done_callback(lambda f, did=did, sub=sub, generation=generation: self._push_done(did, f, sub, generation))

    def _push_done(self, did: str, f, sent_sub: dict | None = None, generation: int | None = None) -> None:
        try:
            status = f.result()
        except Exception:  # noqa: BLE001
            status = 0
        self.st.log("push_ok" if status in (200, 201, 202) else "push_fail", device=did,
                    status=cloud.status_class(status) if status else "network")
        if status in (404, 410):            # the browser dropped this subscription
            subs = webpush.load_subs(self.st)
            # A reconnect can replace (or re-register identical bytes after off) while this POST is in flight.
            if (sent_sub is not None and generation is not None
                    and self.push_generation.get(did, 0) == generation and subs.get(did) == sent_sub):
                subs.pop(did)
                webpush.save_subs(self.st, subs)

    async def stdin_loop(self) -> None:
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

        from websockets import __version__ as ws_version
        from websockets.asyncio.connection import Connection
        exc = context.get("exception")
        handle = context.get("handle")
        callback = getattr(handle, "_callback", None)
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
        self._scratch_task = asyncio.create_task(self._scratch_sweep())   # P542: crash leftovers, off the start path
        self.post_q = asyncio.Queue()
        self.configure_claude_inbound(await asyncio.to_thread(self._claude_inbound_files))   # P142: owner files, off the loop
        jobs = [asyncio.create_task(self.relay_loop()), asyncio.create_task(self.loop_watch()), asyncio.create_task(self.reporter.run()),
                 asyncio.create_task(self.sync_loop()), asyncio.create_task(self.credential_revocations.run()), asyncio.create_task(self.remote_pair.run()), asyncio.create_task(self.post_loop()),
                 asyncio.create_task(self.update_loop()), asyncio.create_task(self.preferences_loop()),
                 asyncio.create_task(self.busy_loop()), asyncio.create_task(self.seat_loop()), asyncio.create_task(self.seat_xfer.run()),
                 asyncio.create_task(self.turn_heal.run())]
        perm_server = None
        order_server = None
        order_gate_clock = None
        from .core import enabled as _core_on
        if _core_on():                       # P359: Agent J Harness CEOs ask the phone through <state>/orders/ask.sock
            try:
                from .core import askbridge, dispatcher as _disp
                order_server = await askbridge.Server(self.ask, self.st.root).start()
                _disp.set_paused(self.st.root, self.stopped())
                from .core.order_gates import watch as order_gate_watch
                order_gate_clock = asyncio.create_task(order_gate_watch(self))
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
                from . import jcards_cal_remote            # P428: offline-queue runner, only when a two-way calendar exists
                jobs.append(asyncio.create_task(jcards_cal_remote.start(self)))
            from .service import ensure_agentj_on_path   # P155: the skills' `agentj …` must run in the Agent's shell
            if ensure_agentj_on_path():
                self.st.log("agentj_path_added")
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
            self.status_changed()
            self.st.log("agent_on", agent=self.agent.kind, isolation_requested=self.agent_cfg.get("fence", True), session_mode=self.agent_cfg.get("session_mode", "independent"), phase="configured")
        await self.bots.start()
        await self.elevate.start()                 # F17: <state>/agentperm/elevate.sock for `agentj sudo` / `agentj secret`
        self.scheduler.recover()                   # P248: reconcile before accepting any dispatch
        from . import untrusted_dispatch
        untrusted_dispatch.manager(self).kick()    # #519: queued data diagnosis does not wait for an owner turn
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
        from .wechat import WeChat
        from .external_channels import ExternalChannels
        try:
            self.external_channels = ExternalChannels(self)
            jobs.append(asyncio.create_task(self.external_channels.run()))
        except Exception:
            self.st.log('external_channel_start_failed', reason='state_unavailable')
        try:
            self.wechat = WeChat(self)
            jobs.append(asyncio.create_task(self.wechat.run()))
        except Exception:
            self.st.log("wechat_start_failed", reason="state_unavailable")
        self.telegram=Telegram(self)
        jobs.append(asyncio.create_task(self.telegram.run()))
        from .mail import Mail                    # P170: the e-mail channel (allowlist + sender check before any model)
        self.mail = Mail(self)
        jobs.append(asyncio.create_task(self.mail.run()))
        jobs.append(asyncio.create_task(self.mail.triage.run()))
        jobs.append(asyncio.create_task(self._approval_watch()))   # P552 (#325 ①): a lapsed signed switch is told to the owner
        jobs.append(asyncio.create_task(self.scheduler.loop()))
        jobs.append(asyncio.create_task(self.event_loop()))
        jobs.append(asyncio.create_task(self.heartbeat.loop()))
        self.quota_resume.start()                 # P314: re-arm the waiting continues (at most once each)
        jobs.append(asyncio.create_task(self.imessage.loop()))   # P274: idle unless macOS + imessage.enabled
        jobs.append(asyncio.create_task(self.goals.loop()))     # P158: deadline / unpaired phone ends the goal
        jobs.append(asyncio.create_task(self.sweep_loop()))
        from .browser_login import daily_loop     # P114: the daily read-only sign-in check (status only, never a QR)
        jobs.append(asyncio.create_task(daily_loop(self)))
        jobs.append(asyncio.create_task(self.assist.loop()))   # 0269-w2-assist: expiry / CLI revoke / unpair / seat sweep
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
            with contextlib.suppress(Exception):    # P408: serve stopping closes the web terminal (`host`)
                await self.term_desk.close("host")
            if self.external_channels is not None:
                await self.external_channels.stop()
            if self.wechat is not None:
                await self.wechat.stop()
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
            if order_gate_clock:
                order_gate_clock.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await order_gate_clock
            if order_server:
                await order_server.close()
            with contextlib.suppress(FileNotFoundError):
                sock.unlink()
            self.st.log("serve_stop")
            from . import telegram as telegram_mod
            telegram_mod.IN_SERVE = False
            await diskq.aflush(STOP_FLUSH)


_IID = re.compile(r"[A-Za-z0-9_-]{22}")


def _iid(info: dict) -> str:

    v = info.get("iid")
    return v if isinstance(v, str) and _IID.fullmatch(v) else ""


def _sign_key(info: dict) -> bytes:

    v = info.get("sk")
    if not isinstance(v, str) or len(v) > 64:
        return b""
    try:
        b = wire.unb64u(v)
    except ValueError:
        return b""
    return b if len(b) == 32 else b""


def _keep_progress(page) -> dict:

    p = (page or {}).get("card") or {}
    return {"progress": p["progress"]} if isinstance(p.get("progress"), str) else {}


def clean_line_tool(tool: str) -> str:
    from .policy_registry import harness_tool
    return harness_tool(tool)
