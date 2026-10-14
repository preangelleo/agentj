"""Native shared sessions; Agent J-owned OpenCode honors the requested fence.

Claude uses the proven relay messaging and committed Stop state machine.
Native approvals are signed phone requests; pre-execution warnings only tighten
permissions. Owner processes are never terminated on detach. An owned ordinary
Claude PTY supports single-Esc interruption; existing terminals use exact Herdr
session routing. Native permission settings and other hooks remain authoritative.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import signal
import sys
import uuid

from .text import pick, strip_reasoning
from .agent import Agent
from .agent_opencode import OpenCodeAgent, Client, HTTPError, CONNECT_WAIT, to_tool, split_model
from . import privacy, shared_hook, danger, provenance, handoff, approval_jev
from .shared_state import AgentState
from .agent import hooks_blocked, _bin, CLAUDE_EFFORTS
from .shared_risk import RiskGuard
from . import shared_opencode_hook

CONTROL_WAIT = 600
DESKTOP_IDLE_WAIT = 1800
CONTROL_WARN_S = 30
OBSERVE_RETRIES = 5     # P142 (B17): consecutive failed observe passes before the shared session is down
FRESH_MS = 30 * 60 * 1000   # P147: a registered session this young may not have written its transcript yet
CLAUDE_INPUT_WAIT = 10  # acceptance, not completion; never retry an uncertain socket write
# P243: a background Bash keeps Claude Code's registry at "shell", a background sub-agent at "busy", although the turn has
# ended and the prompt takes input. Phone /compact and /clear go once our hooks say the main thread is idle and has been
# quiet this long — with background work known (Stop.background_tasks, a run_in_background call, registry "shell")...
BG_SETTLE_S = 3.0
# ...or, registry busy with no background work we know of (e.g. started before Agent J attached), after this much silence.
BUSY_FALLBACK_S = 60.0
NATIVE_STALE_S = 3.0    # P198: native_turn with registry + hook state idle this long is a leftover flag, not a turn
TURN_END_GRACE = 1.0    # P151: a turn end the transcript records (turn_duration) waits this long for its native Stop
EMPTY_STOP_WAIT = 2.0   # P151b: a native Stop with no last_assistant_message: the transcript's end (or this) closes the turn
END_TURN_TOOLS = ("ScheduleWakeup",)   # P151b: a tool that ends the turn — its result is the turn's end, Stop or not
WAIT_NOTE_AFTER = 60    # P151b (追加 4): a phone turn this long without its reply shows what the host sees
TURN_WAIT = 300         # phone turn: a stall card (继续等 / 中断) after this long without a turn end


def public_text(text: str) -> str:
    # Reuse the product's deterministic scrubber; no provider/model call. Owner channel: paths (P127) and contacts
    # (P167: e-mail, phone) stay readable; secrets are redacted.
    return privacy.owner_text(text)


def tool_what(name: str, args) -> str:
    """P151b (追加 4): a short, safe description of a running tool for the phone's wait line — the tool's own description
    field or a file / program name; never a command line (it may carry a secret), always through the redactor."""
    if not isinstance(args, dict):
        return ""
    what = ""
    for key in ("description", "reason", "subject", "title"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            what = v.strip().splitlines()[0]
            break
    if not what:
        v = args.get("file_path") or args.get("path") or args.get("notebook_path")
        if isinstance(v, str) and v.strip():
            what = os.path.basename(v.rstrip("/"))
        elif name == "Bash" and isinstance(args.get("command"), str):
            first = args["command"].strip().split(None, 1)[0] if args["command"].strip() else ""
            what = os.path.basename(first) if re.fullmatch(r"[\w./+-]{1,60}", first or "-") else ""
        elif isinstance(args.get("url"), str):
            from urllib.parse import urlsplit
            with contextlib.suppress(ValueError):
                what = urlsplit(args["url"]).hostname or ""
    return privacy.redact(what[:60]).strip()


def _same_process(d: dict) -> bool:
    """P147: the registry's pid is this very process — same pid namespace (`pidDomain`) and, on Linux, the same start
    time (`procStart`, /proc/<pid>/stat field 22). After a reboot a dead session's pid may belong to anything; a file
    left by a container or sandbox names a pid of another namespace. Fields absent (older Claude Code) → trusted."""
    dom = d.get("pidDomain")
    if isinstance(dom, str) and ":pid:" in dom:
        try:
            if dom.rsplit(":pid:", 1)[1] != os.readlink("/proc/self/ns/pid").split(":", 1)[1]:
                return False
        except (OSError, IndexError):
            pass
    start = d.get("procStart")
    if isinstance(start, str) and start.isdigit():
        try:
            stat = Path(f"/proc/{d['pid']}/stat").read_text()
        except OSError:
            return True       # no procfs (macOS): kill(pid, 0) is all there is
        fields = stat[stat.rfind(")") + 2:].split()
        if len(fields) > 19 and fields[19] != start:
            return False
    return True


def owner_session(d: dict) -> bool:
    """P147: a session the owner works in — not a headless `claude -p` (registered as kind=interactive with an SDK
    entrypoint, e.g. "sdk-cli") nor a background job (kind=bg, already excluded by claude_sessions)."""
    ep = d.get("entrypoint")
    return not (isinstance(ep, str) and ep.startswith("sdk"))


PROBE_PIDS: set[int] = set()   # P258: auth_rescue.screen_check's short-lived interactive screens — never attached to


def claude_sessions(directory: str, sid: str = "", owner_only: bool = False) -> list[dict]:
    out = []
    registry = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"
    for p in registry.glob("*.json"):
        try:
            d = json.loads(p.read_text())
            if not isinstance(d, dict) or not isinstance(d.get("cwd"), str):
                continue
            if os.path.realpath(d["cwd"]) != os.path.realpath(directory):
                continue
            if sid and d.get("sessionId") != sid:
                continue
            if d.get("kind") not in (None, "interactive") or type(d.get("pid")) is not int or d["pid"] <= 0:
                continue
            if d["pid"] in PROBE_PIDS:
                continue
            os.kill(d["pid"], 0)
            if not _same_process(d) or (owner_only and not owner_session(d)):
                continue
            sock = d.get("messagingSocketPath")
            if not isinstance(sock, str) or not Path(sock).is_socket():
                continue
            out.append(d)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda d: int(d.get("updatedAt") or 0), reverse=True)


def claude_send(session: dict, text: str, source: dict | None = None) -> None:
    """Relay protocol; transport errors contain no peer key or message content. B1 (P117): the envelope is built from the
    host-generated `source` (provenance), never from the text; no source = the paired phone."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    addr = session["messagingSocketPath"]
    token = None
    for candidate in dict.fromkeys((addr, os.path.realpath(addr))):
        key = root / "sessions" / f"{session['pid']}.{hashlib.sha256(candidate.encode()).hexdigest()}.key"
        try:
            token = json.loads(key.read_text())["peerToken"]
            break
        except (OSError, ValueError, KeyError):
            continue
    if not isinstance(token, str) or not token:
        raise ConnectionError("Claude peer authentication unavailable")
    content = provenance.envelope(addr, text, source)
    frames = [{"type": "auth", "token": token},
              {"msgV": 1, "msg_id": str(uuid.uuid4()), "type": "user",
               "message": {"role": "user", "content": content}, "priority": "next", "from": "uds:" + addr}]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(addr)
        for frame in frames:
            s.sendall((json.dumps(frame, ensure_ascii=False) + "\n").encode())
        # Same drain interval as relay; no retransmission after an uncertain write.
        import time
        time.sleep(0.2)


class SharedClaudeAgent(Agent):
    kind = "claude"

    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.session = None
        self.offset = 0
        self.discard_line = False
        self.transcript = None
        self.seen = set()
        self.observer = None
        self.done = asyncio.Event()
        self.input_seen = asyncio.Event()
        self.compacted_at = None
        self.phone_turn = False
        self.phone_seen = self.phone_reply_seen = False
        self.phone_claim = False       # P135: a phone message taken inside a native turn already running → its next Stop
        self.native_turn = False       # P135: the transcript shows a native turn in progress (no UserPromptSubmit came)
        self.ended_at = None           # P198: wall clock of the last turn end; assistant lines written before it are that turn's
        self._native_idle_since = None  # P198: monotonic start of "native_turn, yet the session and its registry entry are idle"
        self.bg_live = {}               # P243: background task id → type ("shell" / "subagent" / …), main thread's own
        self.main_seen_at = __import__("time").monotonic()   # P243: main thread's last hook event / transcript line (start = now)
        self.pending_text = ""
        self.pending_source = None
        self.untrusted_state = None    # B1: None | "pending" (group envelope sent) | "running" (native turn seen)
        self.untrusted_at = 0.0
        self.failed_start = False
        self._observe_errors = 0       # P142 (B17): consecutive failed observe passes
        self._observe_error = None     # P192 (B17): the last failed pass's exception type (doctor shows it)
        self._observe_down = False
        self.channel = None
        self.native = None
        self.master = None
        self.state = AgentState(on_turn=self._committed_stop,
                                warn=lambda msg: self.host.st.log("shared_state_warning", detail=msg))
        self.loop = None
        self.permissions = set()
        self.risk_requests = {}
        self.risk_grants = {}
        self.risk_denied = set()
        self.risk_epoch = 0
        self.risk_lock = asyncio.Lock()
        self.attach_lock = asyncio.Lock()
        self.control_ack = asyncio.Event()
        self.control_name = None
        self.clear_pin = cfg.get("shared_session_id")
        self.clear_seen = None
        self.clear_history_ok = True
        self.identity_applied = {}      # P116 (B11): sid → identity key injected via native hook
        self.identity_marker = None     # marker of the last injection; seen in the transcript = actually read
        self.native_lock = asyncio.Lock()   # P116 (B5): one native control (picker / slash) at a time
        self.model_pending = None       # P116 (B5): newest pending model/effort target while the desktop is busy
        self.model_last = None
        self.picker_rows = None
        self._pending_task = None
        self.hook_token = None          # A14 (P139): this served session's hook token (see authentic())
        self.hook_token_pid = None
        self.hook_watch = None
        self.open_cards = {}            # P139: input digest → futures withdrawing phone cards answered elsewhere
        self.card_agent = {}            # P172: withdraw future → sub-agent id (None = the main thread) that asked
        self.sub_agents = {}            # P172: sub-agent id → (type, description) for the card's 「来自子 Agent」 line
        self.sub_pending = []           # P172: (type, description) of Agent/Task calls whose SubagentStart is still to come
        self.tool_agent = {}            # P172: input digest → sub-agent id of its PreToolUse (PermissionRequest may omit it)
        self.heal_seen = {}             # P147: sid → monotonic time of its SessionStart(startup/resume) hook in this folder
        self.heal_choices = []          # P147: live owner sessions when Agent J could not tell which one to serve
        self.heal_told = None
        self.healed = None              # P147: (old sid, new sid, how) to announce once the new session is attached
        self.lost_started = 0           # P147: startedAt (ms) of the served session that went away
        # P151: a turn that ends on a tool call (ScheduleWakeup, Monitor, …) gets no native Stop; the transcript still
        # records its end (system/turn_duration). Its reply = the last assistant text since the turn (or phone input) began.
        self.turn_texts = []
        self.turn_thinking = None       # (message id, text) of the last non-empty thinking block
        self.turn_last_msg = None       # message id of the last assistant record
        self.stop_since_end = False     # a committed native Stop arrived since the last recorded turn end
        self.turn_closed = False        # P151b: this turn's end is already handled (end-turn tool result / empty Stop)
        self.turn_gen = 0               # P151b: bumped at every turn start
        self.endturn_ids = set()        # P151b: tool_use ids of END_TURN_TOOLS calls of this turn
        self.last_tool = None           # P151b (追加 4): (name, short description, wall clock started, tool_use id) or None
        self.wait_more = None           # P151b (追加 4): an Event the stall card's 「继续等」 sets
        self.turn_wait_t0 = None
        # P151b (追加 5): a phone turn's text is delivered as soon as the session moves on to a tool call — how the turn
        # ends (ScheduleWakeup, a background wait, an empty Stop) can no longer lose it. §10.5 stacks them on the page.
        self.flushed = 0                # turn_texts[:flushed] are delivered (or not the phone's)
        self.phone_anchored = False     # the phone input itself is in the transcript: what follows answers it
        self.streamed = []              # public texts of this phone turn already on the phone
        self.end_claimed = None         # (public reply, monotonic) a turn end without Stop already delivered
        # P151 (追加 1/2): compaction / clear handoff — the host's own recovery message when no new turn comes
        self.turn_began_at = None       # wall clock of the current turn's start (UserPromptSubmit / harness turn)
        self.recover_at = 0.0           # wall clock of the last scheduled recovery (PostCompact + SessionStart = one)
        self.compact_handoff = None     # True/False: a fresh handoff existed at PreCompact; None = no PreCompact seen
        self.recover_task = None
        self.system_done = asyncio.Event()   # the end of a turn the host itself started (handoff prep)
        self.ctx_reminded = -1          # compaction epoch the 「上下文过半」 handoff reminder went out in

    def untrusted(self) -> bool:
        """B1 (P117): a group member's envelope is pending or running natively — even after Agent J stopped waiting for
        it (input timeout, 300 s, halt). Cleared by the committed Stop of that native turn, a later native turn, or TTL."""
        import time
        if self.untrusted_state and time.monotonic() - self.untrusted_at > DESKTOP_IDLE_WAIT:
            self.untrusted_state = None
        return self.untrusted_state is not None

    def _mark_untrusted(self, state):
        import time
        self.untrusted_state, self.untrusted_at = state, time.monotonic()

    def sync_compacting(self):
        """P127: the native compaction (PreCompact → SessionStart(compact) / PostCompact, or AgentState's stale end) is
        the phone's `compacting` status, at once — its water sinks and its page says so — then the status from before."""
        on = self.state.snapshot().get("compacting") is True
        if on and self.status not in ("compacting", "down"):
            self._pre_compact = self.status
            self.set_status("compacting")
        elif not on and self.status == "compacting":
            self.set_status(getattr(self, "_pre_compact", None) or "idle")

    def _committed_stop(self, event, reply):
        if self.untrusted_state == "running":
            self.untrusted_state = None
        if self.loop:
            self.loop.call_soon_threadsafe(self._finish_stop, reply)

    def _finish_stop(self, reply, source="committed-Stop"):
        # Native Stop can precede the final transcript flush. Its committed reply
        # is authoritative, exactly as in relay; never turn that race into an
        # early empty phone completion or a later desktop duplicate.
        import time
        self.ended_at = time.time()     # P198: the turn's late lines (read below or by the observer) must not reopen it
        if source == "committed-Stop":
            claimed, self.end_claimed = self.end_claimed, None
            if claimed and claimed[0] == public_text(reply) and time.monotonic() - claimed[1] < 60:
                return                  # P151: a Stop later than its turn end; that reply was already delivered
        if self.transcript and self.transcript.is_file():
            self.read_transcript()
        if self.untrusted_state == "running":
            self.untrusted_state = None
        emit = getattr(self.host, "emit", None)
        if emit:
            emit("shared_stop", agent="claude", phone=self.phone_turn, phone_input=self.phone_turn and self.phone_seen, source=source)
        self.native_turn = False
        if self.phone_turn and (self.phone_seen or self.phone_claim):
            self.phone_reply_seen = True
            if self.phone_claim:
                self.host.turn_queued_note(False)   # the reply replaces 「电脑正忙，消息已排队」, never follows it
            if public_text(reply).strip() not in self.streamed:     # P151b: already on the phone mid-turn
                self.host.agent_text(public_text(reply))
            self.done.set()
        else:
            self.host.desktop_text(public_text(reply))
            self.host.desktop_end()
            self.system_done.set()

    def authentic(self, event) -> bool:
        """A14 (P139): an event for the served session (its session id, or a /clear of it) must carry the served session's
        hook token and — where the kernel names the sender — come from a process descending from the served Claude
        process. Events of other sessions pass through (they are ignored below anyway)."""
        if not isinstance(event, dict) or not self.session:
            return True
        sid = event.get("session_id")
        served = sid == self.session.get("sessionId") or (
            event.get("hook_event_name") == "SessionStart" and event.get("source") == "clear")
        if not served:
            return True
        why = None
        tok = event.get("_agentj_token")
        import time
        prev = getattr(self, "hook_token_prev", None)
        ok = isinstance(tok, str) and (bool(self.hook_token) and hmac.compare_digest(tok, self.hook_token) or bool(
            prev and time.monotonic() < prev[1] and hmac.compare_digest(tok, prev[0])))    # hooks already in flight at /clear
        if not ok:
            why = "token"
        else:
            pid, served_pid = event.get("_agentj_peer"), self.session.get("pid")
            if isinstance(pid, int) and isinstance(served_pid, int) and served_pid not in shared_hook.ancestors(pid):
                why = "process"
        if why:
            self.host.st.log("shared_hook_refused", event=str(event.get("hook_event_name") or "?")[:32], reason=why)
            emit = getattr(self.host, "emit", None)
            if emit:
                emit("shared_hook_refused", reason=why, hook=str(event.get("hook_event_name") or "?")[:32])
        return why is None

    def issue_hook_token(self):
        """A14: a fresh token per served session generation (attach, /clear); the old one is spent."""
        pid = (self.session or {}).get("pid")
        if not isinstance(pid, int):
            return
        old = getattr(self, "hook_token_pid", None)
        if old is not None and old != pid:
            with contextlib.suppress(OSError, TypeError):
                shared_hook.token_path(self.host.st.root, old).unlink(missing_ok=True)
        import time
        self.hook_token_prev = (self.hook_token, time.monotonic() + 10) if self.hook_token and old == pid else None
        self.hook_token = secrets.token_urlsafe(32)
        self.hook_token_pid = pid
        with contextlib.suppress(OSError, TypeError):
            shared_hook.write_token(self.host.st.root, pid, self.hook_token)

    async def watch_hooks(self, every: float = 20.0):
        """A5 (P139): the session can edit its own hook file. Every `every` s compare; a missing Agent J entry is put back
        and the phone is told (the session may be trying to approve itself)."""
        while True:
            await asyncio.sleep(every)
            try:
                self.ensure_hooks()
            except Exception as e:  # noqa: BLE001 — P192 (B18): the approval-hook guard keeps running, and says it failed
                from . import bg
                bg.swallowed(bg.host_log(self.host), "hooks_guard_failed", e, why=str(self.kind))

    def ensure_hooks(self) -> list:
        if not self.channel:
            return []
        if self.hook_token and self.hook_token_pid is not None:
            tp = shared_hook.token_path(self.host.st.root, self.hook_token_pid)
            try:
                same = tp.read_text().strip() == self.hook_token
            except OSError:
                same = False
            if not same:
                shared_hook.write_token(self.host.st.root, self.hook_token_pid, self.hook_token)
                self.host.st.log("shared_hook_restored", events="token")
        gone = shared_hook.missing(self.cfg["dir"], self.channel.path)
        if gone:
            shared_hook.install(self.cfg["dir"], self.channel.path)
            self.host.st.log("shared_hook_restored", events=",".join(gone)[:200])
            self.host.agent_notice("电脑上的 Claude 会话的 Agent J 审批钩子被改掉了（" + ", ".join(gone)[:80] + "），已自动恢复。"
                                   "如果不是你本人改的，请留意这个会话在做什么。 / Agent J's approval hooks were removed from the "
                                   "shared Claude session's settings; restored automatically.")
            push = getattr(self.host, "push_notify", None)
            if push:
                push("ask")
        return gone

    def report_missed(self):
        """Requests that went to the native dialog while this host was unreachable (spooled by the hook): tell the phone."""
        try:
            p = Path(self.host.st.root) / shared_hook.SPOOL
            lines = p.read_text().splitlines()
            p.unlink()
        except (OSError, TypeError):
            return 0
        tools = []
        for ln in lines:
            with contextlib.suppress(ValueError, AttributeError):
                tools.append(str(json.loads(ln).get("tool") or "?")[:40])
        if tools:
            self.host.st.log("shared_missed", count=len(tools))
            self.host.agent_notice(f"Agent J 刚才不在线时，电脑上有 {len(tools)} 个审批请求（" + "、".join(dict.fromkeys(tools))[:120]
                                   + "）只能在电脑上的原生对话框里处理。之后的审批会照常发到手机。 / "
                                   f"{len(tools)} approval request(s) went to the computer's own dialog while Agent J was offline; "
                                   "new ones reach the phone again.")
        return len(tools)

    def _withdraw_answered(self, name, event):
        """P139: Claude Code acts on the first answer (another hook, the desktop dialog) and lets our hook run on (probed with
        a live CLI: the losing hook is only killed when the session ends). The session moving on is the signal: a finished
        call with the same input withdraws its card; the turn's committed end (Stop) or /clear withdraws every open card.
        P172: the main thread's Stop withdraws only the main thread's cards — a background sub-agent keeps waiting on its own
        dialog after the main turn ended, so its card stays until its call finishes, it stops (SubagentStop) or /clear."""
        if name in ("PostToolUse", "PostToolUseFailure"):
            digest = hashlib.sha256(json.dumps([event.get("tool_name"), event.get("tool_input")], sort_keys=True).encode()).hexdigest()
            futs = self.open_cards.pop(digest, [])
        elif name in ("Stop", "SubagentStop", "SessionEnd") or (name == "SessionStart" and event.get("source") == "clear"):
            # not UserPromptSubmit: a queued phone message can be submitted while a dialog is still open
            if name == "Stop" and event.get("agent_id"):
                return
            if name == "SubagentStop":
                if not event.get("agent_id"):
                    return
                keep = lambda f: self.card_agent.get(f) != event.get("agent_id")      # noqa: E731
            elif name == "Stop":
                keep = lambda f: self.card_agent.get(f) is not None                    # noqa: E731
            else:
                keep = lambda f: False                                                  # noqa: E731
            futs = [f for fs in self.open_cards.values() for f in fs if not keep(f)]
            for d in list(self.open_cards):
                self.open_cards[d] = [f for f in self.open_cards[d] if keep(f)]
                if not self.open_cards[d]:
                    self.open_cards.pop(d)
        else:
            return
        for f in futs:
            if not f.done():
                f.set_result(True)
        if futs:
            self.host.st.log("shared_card_withdrawn", event=name, count=len(futs))

    def _note_background(self, name, event):
        """P243: the main thread's background work. The main Stop's background_tasks is Claude Code's own list (it replaces
        ours); a run_in_background Bash / async Agent result adds one; SubagentStop, a task notification, TaskStop/KillShell
        or a session change removes one. Display and the control gate only — nothing here starts or stops work."""
        import time
        aid = event.get("agent_id")
        if not aid:
            self.main_seen_at = time.monotonic()
        if name == "Stop" and not aid and isinstance(event.get("background_tasks"), list):
            self.bg_live = {str(t.get("id")): str(t.get("type") or "task") for t in event["background_tasks"]
                            if isinstance(t, dict) and t.get("id") and t.get("status") in (None, "running", "pending")}
        elif name == "PostToolUse" and not aid:
            resp = event.get("tool_response") if isinstance(event.get("tool_response"), dict) else {}
            inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
            if resp.get("backgroundTaskId"):
                self.bg_live[str(resp["backgroundTaskId"])] = "shell"
            elif resp.get("agentId") and (resp.get("isAsync") or resp.get("status") == "async_launched"):
                self.bg_live[str(resp["agentId"])] = "subagent"
            elif event.get("tool_name") in ("TaskStop", "KillShell", "KillBash"):
                for k in ("task_id", "shell_id", "bash_id"):
                    self.bg_live.pop(str(inp.get(k)), None)
        elif name == "SubagentStop" and aid:
            self.bg_live.pop(str(aid), None)
        elif name in ("SessionEnd",) or (name == "SessionStart" and event.get("source") in ("clear", "startup")):
            self.bg_live = {}
        while len(self.bg_live) > 64:
            self.bg_live.pop(next(iter(self.bg_live)))

    def _control_gate(self, reg) -> tuple[bool, str]:
        """P243: may a phone /compact or /clear be typed now? (go, why) — why: "idle" (registry idle), "bg" (registry busy only
        with background work), "quiet" (registry busy, nothing known, long silence) when go; "fg" (a turn / a dialog / a
        compaction on the main thread), "bg" / "quiet" (still settling) when not. Our hooks own the main thread's state;
        Claude Code's registry stays "shell"/"busy" for background work after the turn ended (probed on 2.1.296)."""
        import time
        if self.state.status != "idle" or self.native_turn or self.phone_turn:
            return False, "fg"
        if reg in ("idle", "done"):
            return True, "idle"
        if reg not in ("shell", "busy"):
            return False, "fg"
        quiet = time.monotonic() - self.main_seen_at
        if self.bg_live or reg == "shell":
            return quiet >= BG_SETTLE_S, "bg"
        return quiet >= BUSY_FALLBACK_S, "quiet"

    def _wait_detail(self, name, why) -> str | None:
        """P243: the waiting card's line — None = the plain 「等电脑空下来再执行」."""
        lang = getattr(self.host, "lang", "zh")
        n = len(self.bg_live)
        act = pick(lang, {"compact": "压缩", "clear": "清空"}.get(name, "执行"), name)
        if why == "bg":
            return pick(lang, (f"电脑上有 {n} 个后台任务在跑" if n else "电脑上有后台任务在跑") + f"，前台已空闲：马上照常{act}，后台任务不受影响",
                        (f"{n} background task(s) running on the computer" if n else "Background work running on the computer")
                        + f"; the foreground is idle — /{name} goes in a moment, background work keeps running")
        if why == "quiet":
            return pick(lang, f"电脑上的会话仍显示忙（多半是后台任务），前台回合已结束；{int(BUSY_FALLBACK_S)} 秒内没有新回合就照常{act}",
                        f"The desktop session still reports busy (most likely background work) though its turn ended; "
                        f"/{name} goes if no new turn starts within {int(BUSY_FALLBACK_S)} s")
        if self.state.status == "waiting":
            return pick(lang, f"电脑上有个确认框在等你处理，处理完就{act}", f"A dialog on the computer is waiting for you; /{name} goes after it")
        if n:
            return pick(lang, f"等电脑这一轮结束再{act}（另有 {n} 个后台任务在跑，不用等它们）",
                        f"Waiting for the desktop turn to end ({n} background task(s) running — not waited for)")
        return None

    def _wait_timeout(self, name, why) -> str:
        lang = getattr(self.host, "lang", "zh")
        mins = int(DESKTOP_IDLE_WAIT // 60) or 1
        act = pick(lang, {"compact": "压缩", "clear": "清空"}.get(name, "执行"), name)
        if self.state.status == "waiting":
            return pick(lang, f"没{act}：等待超时——{mins} 分钟里电脑上一直有个确认框没处理，命令没有发送。处理完再发 /{name} 即可。",
                        f"No change: timed out — a dialog on the computer waited {mins} min; /{name} was not sent. Answer it, then send /{name} again.")
        return pick(lang, f"没{act}：等待超时——{mins} 分钟里电脑上的这一轮一直没结束，命令没有发送。可以稍后再发，或在电脑上直接输入 /{name}。",
                    f"No change: timed out — the desktop turn did not end within {mins} min; /{name} was not sent. Try again later or type /{name} on the computer.")

    def _note_agent(self, name, event):
        """P172: who is asking — the main thread or a sub-agent (its type and the description it was dispatched with)."""
        aid = event.get("agent_id")
        if name == "PreToolUse" and not aid and event.get("tool_name") in ("Agent", "Task"):
            ti = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
            self.sub_pending = (self.sub_pending + [(str(ti.get("subagent_type") or ""), str(ti.get("description") or ""))])[-8:]
        elif name == "SubagentStart" and isinstance(aid, str):
            typ = str(event.get("agent_type") or "")
            i = next((k for k, (t, _) in enumerate(self.sub_pending) if t == typ or not t), None)
            desc = self.sub_pending.pop(i)[1] if i is not None else ""
            self.sub_agents[aid] = (typ, desc)
            while len(self.sub_agents) > 64:
                self.sub_agents.pop(next(iter(self.sub_agents)))
        elif name == "PreToolUse" and isinstance(aid, str):
            digest = hashlib.sha256(json.dumps([event.get("tool_name"), event.get("tool_input")], sort_keys=True).encode()).hexdigest()
            self.tool_agent[digest] = aid
            if aid not in self.sub_agents:
                self.sub_agents[aid] = (str(event.get("agent_type") or ""), "")
            while len(self.tool_agent) > 64:
                self.tool_agent.pop(next(iter(self.tool_agent)))
        elif name in ("PostToolUse", "PostToolUseFailure"):
            self.tool_agent.pop(hashlib.sha256(json.dumps([event.get("tool_name"), event.get("tool_input")],
                                                           sort_keys=True).encode()).hexdigest(), None)
        elif name == "SubagentStop" and isinstance(aid, str):
            self.sub_agents.pop(aid, None)

    def card_origin(self, agent_id):
        """P172: the card's last line when a sub-agent asks (the phone owner sees who is waiting), else None."""
        if not isinstance(agent_id, str) or not agent_id:
            return None
        typ, desc = self.sub_agents.get(agent_id, ("", ""))
        zh = getattr(self.host, "lang", "zh") != "en"
        who = " · ".join(x for x in (typ, desc) if x)[:80] or agent_id[:12]
        return ("来自子 Agent：" if zh else "From sub-agent: ") + who

    async def hook_event(self, event):
        gone = event.pop("_agentj_gone", None) if isinstance(event, dict) else None
        if not isinstance(event, dict):
            return {}
        name = event.get("hook_event_name")
        sid = event.get("session_id")
        if name == "SessionStart" and event.get("source") in ("startup", "resume") and isinstance(sid, str) \
                and not event.get("agent_id"):
            # P147: a new Claude Code session opened in this folder. If the served one is gone, this is the moment to
            # follow it — before its first prompt, so it also gets the identity below.
            import time
            now = time.monotonic()
            self.heal_seen = {k: t for k, t in self.heal_seen.items() if now - t < 3600}
            self.heal_seen[sid] = now
            if self.session is not None and sid != self.session.get("sessionId") and not self._alive(self.session):
                self._lost()
            if self.session is None:
                for _ in range(20):        # Claude Code writes its registry entry around this hook
                    if await self.attach(allow_start=False):
                        break
                    await asyncio.sleep(.05)
                if self.session is None:
                    return self._unattached_hint(sid)
        if not self.session:
            return {}
        if name == "SessionStart" and event.get("source") == "clear" and sid != self.session["sessionId"]:
            # /clear changes UUID, but never which desktop process we serve.
            for _ in range(20):
                live = claude_sessions(self.cfg["dir"], sid)
                if any(d["pid"] == self.session["pid"] for d in live):
                    break
                await asyncio.sleep(.05)
            else:
                return {}
            origin = self.clear_pin or self.host.st.agent_session(self.kind + ".clear_from")
            self.host.st.set_agent_session(self.kind + ".clear_from", origin if isinstance(origin, str) else self.session["sessionId"])
            self.session = next(d for d in live if d["pid"] == self.session["pid"])
            self.cfg["shared_session_id"] = sid
        if sid != self.session["sessionId"]:
            # Foreign sessions in the same project retain native permissions.
            return {}
        name = event.get("hook_event_name")
        self._note_agent(name, event)
        self._withdraw_answered(name, event)
        if not event.get("agent_id"):
            if name == "Stop":
                # P151: this turn has its native end; turn_duration adds nothing. P151b: only a Stop that carries the
                # reply commits it (AgentState → _committed_stop). Claude Code 2.1.296 also sends a Stop with no
                # last_assistant_message when the turn ends on ScheduleWakeup and the answer is only in thinking (owner
                # report 10-12 11:02): that one commits nothing, so the transcript's end must still close the turn.
                committed = bool(str(event.get("last_assistant_message") or "").strip())
                self.stop_since_end = committed
                if not committed:
                    self._schedule_empty_stop()
            elif name == "UserPromptSubmit":
                self.stop_since_end = False
                self._turn_begins()
                self.turn_began_at = handoff.now()
            elif name == "PreCompact":
                self.compact_handoff = handoff.fresh(self.turn_began_at)
                self.host.st.log("shared_precompact", handoff=self.compact_handoff,
                                 trigger=str(event.get("trigger") or "?")[:16])
            if name == "PostCompact" or (name == "SessionStart" and event.get("source") == "compact"):
                if handoff.now() - self.recover_at > 10:
                    has = self.compact_handoff if self.compact_handoff is not None else handoff.fresh(self.turn_began_at)
                    self.compact_handoff = None
                    self._schedule_recover("compact", has)
            elif name == "SessionStart" and event.get("source") == "clear" and handoff.mtime(handoff.CLEAR) is not None:
                self._schedule_recover("clear", True)
        if (name == "UserPromptSubmit" or (name == "SessionStart" and event.get("source") == "clear")) and not event.get("agent_id"):
            self.risk_epoch += 1
            self.permissions.clear()
            self.risk_requests.clear()
            self.risk_grants.clear()
            self.risk_denied.clear()
        if name == "PostCompact" or (name == "SessionStart" and event.get("source") == "compact"):
            import time
            self.compacted_at = time.time()
            self.identity_applied.pop(sid, None)    # P116: a compacted context must read the identity again
            hb = getattr(self.host, "heartbeat", None)
            if hb is not None:                      # P156: the next beat carries the full heartbeat prompt again
                with contextlib.suppress(Exception):
                    hb.forget("compact")
        self._note_background(name, event)
        self.state.handle(event)
        if name == "Stop" and not event.get("agent_id") and getattr(self.state, "_deferred_stop", None) is event:
            self.stop_since_end = False         # P151b: held as a fake Stop (foreground sub-agent) — not this turn's end
        busy = getattr(self.host, "busy", None)
        if busy is not None:                        # P150: the phone's 「在办事项」 dots (busy.py)
            with contextlib.suppress(Exception):
                busy.set_context("shared-" + str(self.kind), harness=self.kind, cwd=self.cfg.get("dir"))
                busy.hook_event(event, scope="shared-" + str(self.kind))
        self.sync_compacting()
        if name == "SessionStart" and event.get("source") == "clear" and self.clear_seen != sid:
            self.clear_seen = sid
            self.issue_hook_token()                 # A14: a new session generation, a new token
            self.host.st.set_agent_session(self.kind, sid)
            root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            slug = re.sub(r"[^A-Za-z0-9]", "-", self.cfg["dir"])
            self.transcript = root / "projects" / slug / (sid + ".jsonl")
            self.offset = 0
            self.discard_line = False
            self.seen.clear()
            self.native_turn = False                # P198: the old session's flag never carries into the new one
            self.compacted_at = __import__('time').time()
            self.meter(ctx=None, source_at=None)
            callback = getattr(self.host, "shared_clear", None)
            if callback:
                self.clear_history_ok = callback() is not False
            if self.control_name == "clear":
                self.control_ack.set()
        if name == "PostCompact" and self.control_name == "compact":
            self.meter(ctx=None, source_at=None)
            self.control_ack.set()
        elif (name == "PostCompact" or (name == "SessionStart" and event.get("source") == "compact")) and self.control_name != "compact":
            self.host.desktop_cmd_done("compact")
        if name == "UserPromptSubmit" and not event.get("agent_id"):
            if provenance.names_untrusted(str(event.get("prompt", ""))):
                self._mark_untrusted("running")
            elif self.untrusted_state == "running":
                self.untrusted_state = None
        if name == "UserPromptSubmit":
            emit = getattr(self.host, "emit", None)
            if emit:
                emit("shared_prompt", agent="claude", phone=self.phone_turn,
                    matching=self.phone_turn and self.pending_text in str(event.get("prompt", "")))
            reminder = self.turn_reminder() if not event.get("agent_id") else ""
            if self.phone_turn and self.pending_text in str(event.get("prompt", "")):
                self.phone_seen = True
                self.input_seen.set()
                prov = provenance.hook_context(self.pending_source)
                tail = "\n\n" + reminder if reminder else ""
                ctx = prov + self.identity_context(sid, "phone", reserve=len(prov) + len(tail)) + tail
                return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}
            if reminder:                            # P159: a desktop turn of the served session gets the same hidden line
                return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": reminder}}
        # A category grant does not survive revocation, stop or a changed signing
        # identity. The original signed approval remains its audit parent.
        for cat, grant in list(self.risk_grants.items()):
            if self.host.stopped() or self.host.st.sign_key(grant["device"]) != grant["sign_pub"]:
                self.permissions.discard(cat)
                self.risk_grants.pop(cat, None)
        if name == "PreToolUse":
            verdict = danger.classify_shared(event.get("tool_name"), event.get("tool_input"), self.cfg.get("danger_extra"),
                                             self.cfg.get("approvals", "standard"))
            # P198: the five clear dangers ask on the phone (P128's documented `standard`); from 0.15 (F14) to 0.20.0 the
            # default kept only hook-file writes (A5) — now only under agent.approvals = native
            keys = danger.shared_keys(verdict, self.cfg)
            if keys and set(keys) <= self.permissions:
                for cat in keys:
                    self.host.st.log("shared_risk_batch", category=cat, grant=self.risk_grants[cat]["rid"],
                        session=self.session["sessionId"], input_sha256=hashlib.sha256(
                            json.dumps(event.get("tool_input"), sort_keys=True).encode()).hexdigest())
            if set(keys) - self.permissions:
                ident = event.get("tool_use_id")
                digest = hashlib.sha256(json.dumps([event.get("tool_name"), event.get("tool_input")], sort_keys=True).encode()).hexdigest()
                self.risk_requests[(ident, digest)] = keys
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                    "permissionDecisionReason": "Agent J: " + danger.card_why(verdict, getattr(self.host, "lang", "zh"))}}
        if name == "PermissionRequest" and provenance.untrusted_turn(self):
            # B1: a group member's turn never reaches the owner's approval, nor a category grant from an owner turn
            self.host.st.log("ask_done", tool=event.get("tool_name"), decision="deny", reason="group_source")
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny",
                "message": "This turn came from a Telegram group member, not the owner: no owner approval."}}}
        if name == "SessionStart" and not event.get("agent_id"):
            # P116 (B11): startup / resume / clear / compact of the served session reads the identity (again)
            note = self._handoff_context(event.get("source"))
            ctx = self.identity_context(sid, "session_start:" + str(event.get("source") or "?"), force=True, reserve=len(note))
            ctx += note
            return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx.lstrip()}} if ctx else {}
        if name == "PermissionRequest":
            # P139: never yield to another PermissionRequest hook — the phone card races it, first answer wins (an answer
            # elsewhere withdraws the card, see _withdraw_answered). Known observers (relay_hook.py taps) never answer.
            other = shared_hook.foreign_answerers(self.cfg["dir"], str(event.get("tool_name") or ""))
            if other:
                self.host.st.log("shared_approval_race", tool=str(event.get("tool_name") or "?")[:64], layers=",".join(other))

        if name == "PermissionRequest":
            tool, inp = event.get("tool_name", "?"), event.get("tool_input") or {}
            digest = hashlib.sha256(json.dumps([tool, inp], sort_keys=True).encode()).hexdigest()
            # Claude PermissionRequest may omit tool_use_id. Digest matches only a
            # pending guard-raised request; this cache never bypasses native asks.
            key = next((k for k in self.risk_requests if k[1] == digest), None)
            cats = self.risk_requests.pop(key, []) if key else []
            epoch = self.risk_epoch
            async with self.risk_lock:
                for cat, grant in list(self.risk_grants.items()):
                    if self.host.stopped() or self.host.st.sign_key(grant['device']) != grant['sign_pub']:
                        self.permissions.discard(cat)
                        self.risk_grants.pop(cat, None)
                if set(cats) & self.risk_denied:
                    answer = {'behavior': 'deny'}
                elif cats and set(cats) <= self.permissions:
                    answer = {"behavior": "allow"}
                else:
                    withdraw = asyncio.get_running_loop().create_future()
                    self.open_cards.setdefault(digest, []).append(withdraw)
                    owner = event.get("agent_id") or self.tool_agent.get(digest)
                    self.card_agent[withdraw] = owner if isinstance(owner, str) and owner else None
                    if gone is not None:
                        gone.add_done_callback(lambda _f, w=withdraw: w.done() or w.set_result(True))
                    origin = self.card_origin(self.card_agent[withdraw])
                    try:
                        ctx = approval_jev.transcript_context(event.get("transcript_path")) if event.get("transcript_path") else None
                        answer = await self.host.ask(tool, inp, batch=False, risk_scope=bool(cats), gone=withdraw,
                                                     **({"context": ctx} if ctx is not None else {}),
                                                     **({"origin": origin} if origin else {}))
                    finally:
                        self.card_agent.pop(withdraw, None)
                        if withdraw in self.open_cards.get(digest, []):
                            self.open_cards[digest].remove(withdraw)
                            if not self.open_cards[digest]:
                                self.open_cards.pop(digest)
                    if withdraw.done():
                        return {}                   # answered elsewhere first: no late decision from us
                    if cats and answer.get("behavior") == "allow":
                        grant = answer.get("risk_grant")
                        keep = [c for c in cats if c not in danger.ALWAYS_CARD]   # A5: hook-file writes ask every time
                        if self.host.stopped() or (grant and self.host.st.sign_key(grant['device']) != grant['sign_pub']):
                            answer = {'behavior': 'deny'}
                        elif grant and self.risk_epoch == epoch:
                            self.permissions.update(keep)
                            self.risk_grants.update({c: grant for c in keep})
                    elif cats and self.risk_epoch == epoch:
                        self.risk_denied.update(cats)
            decision = {"behavior": "allow"} if answer.get("behavior") == "allow" else {
                "behavior": "deny", "message": "Denied by paired phone or timeout."}
            if decision["behavior"] == "deny":
                # P198 (H): Claude Code 2.1.296 sends no PostToolUse(Failure) after a hook's deny — the card set by this
                # PermissionRequest would hold the state at waiting past the turn's Stop (_commit_idle keeps a waiting card)
                self.state.clear_card(tool)
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
        return {}

    HOOK_MAX = 10000      # P159 measured (Claude Code 2.1.296): additionalContext over 10000 characters is swapped for a
                          # 2 KB preview + a file path, so the whole hook answer (provenance + identity + reminder) stays under
    INLINE_MAX = 9500     # P159 (was 9000): the zh identity stays inline; longer text → a private file the session Reads

    def identity_bundle(self):
        """(text, key) of the packaged identity + owner private instructions for this shared session, or (None, None)."""
        if self.cfg.get("_workflow_ceo") or self.cfg.get("shared_identity", True) is False:
            return None, None
        from . import main_identity
        try:
            text = main_identity.prompt(self.cfg, harness="claude")   # P165: no Codex-only line on the hook path
        except (main_identity.IdentityError, OSError):
            return None, None
        return text, hashlib.sha256(text.encode()).hexdigest()

    def turn_reminder(self):
        """P159: the hidden per-turn identity line (main_identity.IDENTITY_REMINDER) when this session carries the identity."""
        text, _ = self.identity_bundle()
        if not text:
            return ""
        from . import main_identity
        return main_identity.turn_reminder(self.cfg)

    def identity_context(self, sid, why, force=False, reserve=0):
        """Native-hook text that makes the served session read the identity once per sid / generation / compaction.
        The host log keeps only metadata (generation key prefix, mechanism, sid); never the text. `reserve` = the other
        characters riding in the same additionalContext (HOOK_MAX)."""
        text, key = self.identity_bundle()
        if not text or not isinstance(sid, str) or (not force and self.identity_applied.get(sid) == key):
            return ""
        marker = "agentj-identity:" + key[:16] + ":" + uuid.uuid4().hex[:8]
        head = ("\n\n[Agent J · owner main-Agent identity + private instructions (shared session); below "
                "native project instructions and every harness rule; they grant no permission. Marker " + marker + "]\n")
        tail = "\n[/Agent J identity " + marker + "]"
        if len(text) <= self.INLINE_MAX and len(head) + len(text) + len(tail) + reserve <= self.HOOK_MAX:
            body, mechanism = head + text + tail, "hook.additionalContext"
        else:
            p = Path(self.host.st.root) / "shared-identity.md"
            self.host.st.write_private(p, ("<!-- " + marker + " -->\n" + text).encode())
            body = head + ("Before replying, read this file with the Read tool and follow it as part of your instructions: "
                           + str(p) + "\n")
            mechanism = "hook.additionalContext+Read"
        self.identity_applied[sid] = key
        self.identity_marker = (marker, sid, key, mechanism)
        self.host.st.log("shared_identity", result="injected", identity_session=sid, trigger=why[:40], mechanism=mechanism,
                         identity=key[:16])
        from . import main_identity
        with contextlib.suppress(main_identity.IdentityError, OSError, AttributeError, TypeError):
            main_identity.audit(self.cfg, self.kind, self.host.st, sid)
        return body

    def _identity_seen(self, line: bytes):
        m = self.identity_marker
        if m and m[0].encode() in line:
            self.identity_marker = None
            self.host.st.log("shared_identity", result="read", identity_session=m[1], mechanism=m[3], identity=m[2][:16])
            emit = getattr(self.host, "emit", None)
            if emit:
                emit("shared_identity_read", agent="claude", session=m[1])

    async def prepare_channel(self):
        if self.channel:
            return
        if hooks_blocked(self.cfg["dir"]):
            raise ValueError("native Claude hooks disabled")
        self.loop = asyncio.get_running_loop()
        self.channel = shared_hook.Channel(self, shared_hook.channel_path(self.host.st.root, "claude"))
        await self.channel.start()
        shared_hook.install(self.cfg["dir"], self.channel.path)
        self.hook_watch = asyncio.ensure_future(self.watch_hooks())
        self.report_missed()

    async def ordinary_start(self):
        import pty
        await self.prepare_channel()
        exe = _bin("AGENTJ_CLAUDE_BIN", "claude")
        if not exe:
            raise ValueError("Claude binary unavailable")
        master, slave = pty.openpty()
        import fcntl, termios, struct
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = {k: v for k, v in os.environ.items() if k not in
               ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_SESSION_ID",
                "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                "CLAUDE_PID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_EFFORT", "CLAUDE_CODE_EXECPATH")}
        from .proxy import environment
        env = environment(env, self.host.preferences)
        env["TERM"] = "xterm-256color"
        try:
            argv = [exe]
            if self.cfg.get("model"):
                argv += ["--model", self.cfg["model"]]
            self.native = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=env,
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        finally:
            os.close(slave)
        self.master = master
        os.set_blocking(master, False)
        self.loop.add_reader(master, self._drain_terminal)
        for _ in range(120):
            sessions = claude_sessions(self.cfg["dir"])
            if any(d["pid"] == self.native.pid for d in sessions):
                return
            if self.native.returncode is not None:
                break
            await asyncio.sleep(.25)
        raise ValueError("ordinary Claude startup unconfirmed")

    def _drain_terminal(self):
        # Drain display bytes only; transcript/hooks own all messages and state.
        try:
            os.read(self.master, 65536)
        except (OSError, BlockingIOError):
            if self.loop and self.master is not None:
                self.loop.remove_reader(self.master)

    def is_down(self):
        return self.failed_start

    async def command(self, name, arg):
        from .slash import Result, HELP
        if name == "help":
            return Result(HELP + pick(getattr(self.host, "lang", "zh"), "\n共享 Claude：clear/compact 等当前回合结束后执行；成功以原生回执确认。", "\nShared Claude waits for the desktop turn and confirms native receipts."), "info")
        if name in ("context", "usage", "status", "cost", "model") and not arg:
            from . import claude_statusline
            m = claude_statusline.read(self.host.st.root, (self.session or {}).get("sessionId"), self.compacted_at)
            self.meter(**m)
            if name == "context":
                c = m.get("ctx") or {}
                return Result((pick(getattr(self.host, "lang", "zh"), "上下文：", "Context: ") + (str(c['pct']) + '%' if c.get('pct') is not None else pick(getattr(self.host, "lang", "zh"), "—（等待原生读数）", "— (awaiting native measurement)"))), "info")
            if name == "usage":
                return Result("5h: " + (str(m['h5']['pct']) + '%' if m.get('h5') else "—") + " · week: " + (str(m['week']['pct']) + '%' if m.get('week') else "—"), "info")
            if name == "cost":
                # P116 (B5): Claude Code's own status-line figure for this session (cost.total_cost_usd), never an estimate.
                usd = claude_statusline.cost(self.host.st.root, (self.session or {}).get("sessionId"))
                if usd is None:
                    return Result("花费 / Cost: —（原生状态栏还没有本会话读数；开启 agentj config claude-statusline on / no native reading yet）", "info")
                return Result(f"花费 / Cost: ${usd:.2f}（本会话，Claude Code 原生状态栏读数 / this session, Claude Code's own status line）", "info")
            if name == "model":
                from . import shared_modelswitch as ms
                await self.refresh_models()
                names = " · ".join(dict.fromkeys(m_["id"] for m_ in self.models_cache)) or "opus · sonnet · fable · haiku"
                # P142: the phone's model label sends this — one button per picker row (+ the owner's default), each the
                # same `/model <id>` self-slash path and receipt as typing it
                cur = self.cur_model()
                rows = [{"id": m_["id"], "name": m_.get("name") or m_["id"], "cur": m_["id"] == cur} for m_ in self.models_cache]
                rows.append({"id": "default", "name": pick(getattr(self.host, "lang", "zh"), "默认（你自己的默认模型）", "Default (your own default model)"),
                             "cur": False})
                return Result("Claude · " + str(m.get('model_name') or m.get('model') or '—') + " · " + str(m.get('effort') or '—')
                              + " · " + self.state.status + "\n/model <" + names + "|default> [" + "|".join(ms.EFFORTS) + "]", "info",
                              models=rows[:40])
            return Result("Claude · " + str(m.get('model_name') or m.get('model') or '—') + " · " + self.state.status, "info")
        if name == "model":
            return await self._model_command(arg)
        if name not in ("clear", "compact") or (name == "clear" and arg) or any(c in arg for c in '\r\n\x1b\x00'):
            return Result("此控制没有执行：共享终端没有可验证的接口；请在终端运行。 / Not executed: no verified shared control receipt; run it in the terminal.", "refused")
        if self.session is None and not await self.attach():
            return Result(pick(getattr(self.host, "lang", "zh"), "没清成：Claude 会话未连接", "No change: Claude session unavailable"), "error")
        # The phone queue already serializes phone turns. Also wait for DESKTOP
        # turns, which do not occupy that queue. No Esc, no approval answers.
        import time
        started = time.monotonic()
        deadline = started + DESKTOP_IDLE_WAIT
        warned = noted = False
        prepped = False
        shown = None
        while True:
            if self.halting or self.host.stopped() is True or getattr(self.cur_cmd, "cancelled", False):
                return Result(pick(getattr(self.host, "lang", "zh"), "已取消，未发送控制。", "Cancelled; control not sent."), "error")
            live = claude_sessions(self.cfg['dir'], self.session['sessionId'])
            if not live or live[0]['pid'] != self.session['pid']:
                return Result(pick(getattr(self.host, "lang", "zh"), "没清成：所选桌面会话已离线", "No change: selected desktop session offline"), "error")
            reg = live[0].get('status')
            self.validate_session(reg)
            go, why = self._control_gate(reg)      # P243: background work alone no longer holds the control
            if go:
                if name == "compact" and not prepped and not handoff.fresh(self.turn_began_at):
                    prepped = True          # P151: the handoff first (agentj-handoff), then wait for idle again
                    await self._handoff_prep()
                    continue
                if why != "idle":
                    self.host.st.log("shared_control_background", cmd=name, why=why, tasks=len(self.bg_live), registry=str(reg)[:12])
                break
            detail = self._wait_detail(name, why)
            if not noted or detail != shown:
                if detail is None:
                    self.host.cmd_waiting(self.cur_cmd)
                else:
                    self.host.cmd_waiting(self.cur_cmd, detail)
                noted, shown = True, detail
            if not warned and time.monotonic() - started >= CONTROL_WARN_S:
                self.host.st.log("shared_control_wait_warning", cmd=name, seconds=30, why=why, tasks=len(self.bg_live), level="warning")
                warned = True
            if time.monotonic() >= deadline:
                self.host.st.log("shared_control_timeout", cmd=name, why=why, tasks=len(self.bg_live), registry=str(reg)[:12])
                return Result(self._wait_timeout(name, why), "error")
            await asyncio.sleep(.1)
        if self.native_lock.locked():
            return Result("没执行：模型切换正在进行，稍后再试。 / Not sent: a model switch is in progress.", "error")
        if getattr(self.cur_cmd, "cancelled", False):
            return Result(pick(getattr(self.host, "lang", "zh"), "已取消，未发送控制。", "Cancelled; control not sent."), "error")
        if self.cur_cmd is not None:
            self.cur_cmd.native_started = True
            self.host.hist_update(self.cur_cmd.turn, card={})
        self.control_ack.clear()
        self.control_name = name
        try:
            text = '/' + name + (' ' + arg if arg else '')
            self._control_sent = (text, time.monotonic())
            if self.native and self.native.pid == self.session['pid'] and self.master is not None:
                os.write(self.master, text.encode())
                await asyncio.sleep(.1)
                os.write(self.master, b'\r')
            else:
                from .shared_controls import send
                why = await asyncio.to_thread(send, self.session, text)
                if why != 'sent':
                    return Result(pick(getattr(self.host, "lang", "zh"), "没清成：桌面终端未提供安全输入通道（" + why + "）；请在所选终端运行 /" + name + "。", "No change: the desktop terminal has no safe input channel (" + why + "); run /" + name + " in the selected terminal."), "error")
            try:
                await asyncio.wait_for(self.control_ack.wait(), CONTROL_WAIT)
            except asyncio.TimeoutError:
                return Result(pick(getattr(self.host, "lang", "zh"), "没清成：未收到原生回执，结果未确认；请核实桌面，勿重复发送。", "No confirmed change: native receipt timeout; check desktop before retrying."), "error")
            if self.halting or self.host.stopped() is True:
                return Result(pick(getattr(self.host, "lang", "zh"), "控制结果尚未确认，已停止等待；请核实电脑。", "Control unconfirmed; waiting stopped, check the desktop."), "error")
            self.read_statusline()
            if name == 'clear' and not self.clear_history_ok:
                return Result(pick(getattr(self.host, "lang", "zh"), "桌面已清空，但手机历史归档失败，旧页仍保留；请检查本机磁盘。", "Desktop cleared, but phone history archive failed; check local disk."), "error")
            return Result("", sep=name == 'clear')   # P133: cmd_card adds 「✓ …」
        finally:
            self.control_name = None

    def _control_echo(self, text) -> bool:
        # P133: the transcript's echo of our native slash is no desktop turn
        import time
        sent = getattr(self, "_control_sent", None)
        if sent and time.monotonic() - sent[1] < CONTROL_WAIT and text.strip() == sent[0]:
            self._control_sent = None
            return True

    # ------------------------------------------------ P116 (B5): model / effort through the native picker, with receipts
    EFFORTS = CLAUDE_EFFORTS
    MODEL_WATCH = 20.0          # seconds to wait for the status line to report the switched model / effort
    MODEL_TTL = 15 * 60         # a switch the desktop never became idle for is dropped (Relay's PENDING_TTL)

    def passthrough(self, name):
        # Relay parity: any other /name reaches the owner's session as their own text (its skills decide, e.g. a
        # self-slash skill); built-in controls above never do. Host commands were handled before this point.
        return bool(name) and name not in __import__("agentj.slash", fromlist=["x"]).WHITELIST + ("undo_clear",)

    def _native(self):
        from . import claude_statusline
        return claude_statusline.read(self.host.st.root, (self.session or {}).get("sessionId"), self.compacted_at)

    def cur_model(self):
        from .shared_modelswitch import key_of
        return key_of(self._native().get("model_name"))

    def cur_effort(self):
        e = self._native().get("effort")
        return e if e in CLAUDE_EFFORTS else None

    def _picker_rows(self):
        if self.picker_rows is None:
            try:
                rows = json.loads((Path(self.host.st.root) / "claude-picker-rows.json").read_text())
                self.picker_rows = rows if isinstance(rows, list) and all(isinstance(r, str) for r in rows) else []
            except (OSError, ValueError, TypeError):
                self.picker_rows = []
        return self.picker_rows

    async def refresh_models(self):
        """Rows the native picker last showed (learned), else the rows Relay verified; the running model always listed."""
        from . import shared_modelswitch as ms
        rows = [r for r in (self._picker_rows() or ms.LEGACY) if not r.startswith("Default")]
        cur, native = self.cur_model(), self._native().get("model_name")
        cache = [{"id": ms.slug(r), "name": r, "efforts": list(ms.EFFORTS)} for r in rows]
        if cur and cur not in {m["id"] for m in cache}:
            cache.insert(0, {"id": cur, "name": native, "efforts": list(ms.EFFORTS)})
        self.models_cache = [{**m, "name": native} if m["id"] == cur and native else m for m in cache][:40]

    async def apply_model(self, model, effort, default=False):
        """None only when the session's own status line reports the target; queued while the desktop is busy."""
        from . import shared_modelswitch as ms
        if self.session is None and not await self.attach():
            return "unsupported"
        if default:
            # Long press: the picker's Default row (the owner's own default model), session-only; plus the configured effort.
            m, e = "default", self.cfg.get("effort") if self.cfg.get("effort") in ms.EFFORTS else None
        else:
            m, e = model or self.cur_model(), effort
            if m is None or not ms.valid_id(m):
                return "unsupported"        # current model unknown (no status line): an effort-only change has no row
            if ms.matches(m, self.cur_model()) and (e is None or e == self.cur_effort()):
                self.model_pending = None
                self.read_statusline()
                return None
        return await self._model_apply(m, e)

    async def _model_apply(self, model, effort):
        from . import shared_modelswitch as ms
        import time
        if self.native_lock.locked() or self.state.status != "idle" or self.phone_turn:
            self.model_pending = {"model": model, "effort": effort, "at": time.time()}
            self.host.st.log("shared_model", result="queued", model=model, effort=effort)
            return "queued"
        async with self.native_lock:
            started = time.time()
            res = await asyncio.to_thread(ms.apply, self.session, model, effort)
            self.host.st.log("shared_model", result=res["result"], reason=res.get("why"), model=model, effort=effort,
                             identity_session=(self.session or {}).get("sessionId"))
            if res.get("rows"):
                self.picker_rows = res["rows"]
                with contextlib.suppress(OSError, AttributeError, TypeError):
                    self.host.st.write_private(Path(self.host.st.root) / "claude-picker-rows.json", json.dumps(res["rows"]).encode())
            if res["result"] == ms.BUSY:
                self.model_pending = {"model": model, "effort": effort, "at": started}
                return "queued"
            self.model_pending = None
            if res["result"] == ms.BLOCKED:
                return "blocked"
            if res["result"] in (ms.NO_PANE, ms.UNAVAILABLE):
                return "unsupported"
            if res["result"] != ms.SENT:
                return "failed"
            ok = await self._model_confirm(model, effort, started)
        self.host.st.log("shared_model", result="confirmed" if ok else "unconfirmed", model=model, effort=effort)
        await self.refresh_models()
        return None if ok else "unconfirmed"

    async def _model_confirm(self, model, effort, started):
        from .shared_modelswitch import key_of, matches
        import time
        deadline = time.monotonic() + self.MODEL_WATCH
        while True:
            n = self._native()
            fresh = (n.get("source_at") or 0) >= started
            hit = (model == "default" and fresh) or matches(model, key_of(n.get("model_name")))
            if hit and (effort is None or n.get("effort") == effort):
                self.meter(**n)
                return True
            if time.monotonic() >= deadline or self.halting:
                self.meter(**n)       # the pill settles on native truth, never on what we typed
                return False
            await asyncio.sleep(.5)

    async def _apply_pending(self):
        import time
        p = self.model_pending
        if not p:
            return
        if time.time() - p["at"] > self.MODEL_TTL:
            self.model_pending = None
            self.host.st.log("shared_model", result="expired", model=p["model"], effort=p["effort"])
            return
        why = await self._model_apply(p["model"], p["effort"])
        if why == "queued":
            self.model_pending = {**self.model_pending, "at": p["at"]} if self.model_pending else None
            return
        changed = getattr(self.host, "models_changed", None)
        if changed:
            changed()
        if why is not None:
            self.host.agent_notice("模型/思考强度没有切换成功（" + why + "），以电脑上的实际读数为准。 / The queued model/effort switch was not confirmed; the pill shows the native reading.")

    async def _model_command(self, arg):
        """`/model <id|label|default> [effort]` or `/model <effort>` — the same picker path and receipt as the pill."""
        from . import shared_modelswitch as ms
        from .slash import Result
        if any(c in arg for c in '\r\n\x1b\x00'):
            return Result("没执行：参数无效。 / Not executed: invalid argument.", "refused")
        words = arg.split()
        rows = self._picker_rows()
        model = effort = None
        if len(words) == 1 and words[0].lower() in ms.EFFORTS:
            effort = words[0].lower()
        else:
            if words and words[-1].lower() in ms.EFFORTS + ("ultracode",):
                effort = words.pop().lower()
            model = ms.resolve(" ".join(words), rows) if words else None
            if effort == "ultracode":
                return Result("没执行：ultracode 不是思考强度。 / ultracode is not an effort level.", "refused")
            if model is None:
                known = " ".join(dict.fromkeys(ms.slug(r) for r in (rows or ms.LEGACY) if not r.startswith("Default")))
                return Result("没执行：不认识的模型。可用：" + known + " default（也可写 opus / sonnet / fable / haiku）。 / Unknown model.", "refused")
        why = await (self.apply_model(None, None, default=True) if model == "default" and effort is None else self.apply_model(model, effort))
        n = self._native()
        now = str(n.get("model_name") or "—") + " · " + str(n.get("effort") or "—")
        if why is None:
            return Result("已切换（原生状态栏确认，仅本会话）：" + now + " / Switched (native status line, this session only)", "ok")
        if why == "queued":
            return Result("已排队：电脑上的回合结束后切换，结果以状态栏为准。 / Queued until the desktop is idle.", "info")
        if why == "unconfirmed":
            return Result("未确认：按键已发，但状态栏没报告目标，当前实际：" + now + "。 / Not confirmed by the native status line.", "error")
        return Result("没切换（" + why + "）：当前实际 " + now + "。 / Not switched.", "error")

    async def attach(self, allow_start=True):
        async with self.attach_lock:
            return await self._attach_selected(allow_start)

    # ------------------------------------------------------------ P147: follow the owner to a new session
    def _alive(self, session) -> bool:
        return any(d["pid"] == session.get("pid") for d in claude_sessions(self.cfg["dir"]))

    def _lost(self):
        """The served session is gone (window closed, reboot, crash). Remember when it started; the next attach heals."""
        if self.session is not None:
            self.lost_started = int(self.session.get("startedAt") or 0)
            self.host.st.log("shared_lost", agent="claude", session=str(self.session.get("sessionId", ""))[:8])
        self.session = None

    def _repin(self, old, new, how):
        sid = new["sessionId"]
        self.cfg["shared_session_id"] = sid
        self.healed = (old or "", sid, how)
        self.heal_choices = []
        self.host.st.log("shared_reconnect", agent="claude", old=(old or "")[:8], new=sid[:8], how=how)
        # The owner's pin follows (status, doctor, the next serve start); the config write is the same path as
        # `agentj config set`. A test host / older host without it keeps the in-memory pin only.
        setp = getattr(self.host, "set_pref", None)
        if old and setp:
            with contextlib.suppress(RuntimeError):
                task = asyncio.get_running_loop().create_task(setp("agent.shared_session_id", sid))
                self._pin_write = task          # keep a reference until it is done

    def _heal(self, dead):
        """The pinned session `dead` is not open. Pick its successor: the one live owner session in this folder; else the
        one that announced itself (SessionStart) since; else the one started after the lost session. Never a headless
        `claude -p` or a background job. Two or more equally likely → none (the owner chooses; see heal_notice)."""
        rows = [d for d in claude_sessions(self.cfg["dir"], "", True) if d.get("sessionId") != dead]
        choice, how = None, ""
        if len(rows) == 1:
            choice, how = rows[0], "only"
        elif rows:
            told = [d for d in rows if d.get("sessionId") in self.heal_seen]
            newer = [d for d in rows if int(d.get("startedAt") or 0) > self.lost_started]
            if len(told) == 1:
                choice, how = told[0], "announced"
            elif self.lost_started and len(newer) == 1:
                choice, how = newer[0], "newer"
        if choice is None:
            self.heal_choices = [d["sessionId"] for d in rows]
            return []
        self._repin(dead, choice, how)
        return [choice]

    def heal_notice(self) -> str:
        lang = getattr(self.host, "lang", "zh")
        ids = "、".join(c[:8] for c in self.heal_choices) if lang != "en" else ", ".join(c[:8] for c in self.heal_choices)
        return pick(lang, f"电脑上这个目录开着 {len(self.heal_choices)} 个 Claude Code 会话（{ids}），之前那个已经关了，Agent J 不确定接哪个。"
                          "在你要用的那个会话里说一句「接上手机」，或在电脑上运行 agentj doctor --reconnect。",
                    f"{len(self.heal_choices)} Claude Code sessions are open in this folder ({ids}) and the previous one is gone; "
                    "Agent J cannot tell which one to follow. In the one you want, say \"connect my phone\", or run "
                    "agentj doctor --reconnect on the computer.")

    def _unattached_hint(self, sid):
        """SessionStart of an owner session Agent J does not serve while it serves none: tell that session how to attach
        (it never receives the identity until attached)."""
        if not any(d.get("sessionId") == sid for d in claude_sessions(self.cfg["dir"], "", True)):
            return {}
        ctx = ("[Agent J] The owner's phone (Agent J) is not attached to any Claude Code session in this folder right now. "
               "If the owner asks to connect the phone, or says the phone/web page shows grey or disconnected, run "
               "`agentj doctor --reconnect` here (it attaches the session that runs it) and tell the owner its one-line result.")
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}

    async def reconnect(self, choice=None) -> dict:
        """`agentj doctor --reconnect` (ctl agent_reconnect): re-check now; `choice` = a session id (or its first 8+
        characters) the owner / the calling session named. Never starts a Claude Code process."""
        async with self.attach_lock:
            if choice:
                rows = [d for d in claude_sessions(self.cfg["dir"])
                        if isinstance(d.get("sessionId"), str) and len(choice) >= 8 and d["sessionId"].startswith(choice)]
                if len(rows) != 1:
                    return {"ok": False, "why": "not_found"}
                if self.session is not None and self.session.get("sessionId") == rows[0]["sessionId"]:
                    return {"ok": True, "changed": False, "session": rows[0]["sessionId"][:8]}
                if self.session is not None:
                    self._lost()
                self._repin(self.cfg.get("shared_session_id", ""), rows[0], "choice")
            elif self.session is not None:
                if self._alive(self.session):
                    if self.status == "down":
                        self.set_status("idle")
                    return {"ok": True, "changed": False, "session": self.session["sessionId"][:8]}
                self._lost()
            self.heal_told = None
            ok = await self._attach_selected(False)
            if ok:
                return {"ok": True, "changed": True, "session": self.session["sessionId"][:8]}
            if not self.heal_choices:
                self.heal_choices = [d["sessionId"] for d in claude_sessions(self.cfg["dir"], "", True)]
            return {"ok": False, "why": "ambiguous" if len(self.heal_choices) > 1 else "none",
                    "choices": [c[:8] for c in self.heal_choices]}

    async def _attach_selected(self, allow_start=True):
        pinned = self.cfg.get("shared_session_id", "")
        if pinned and pinned == self.host.st.agent_session(self.kind + ".clear_from"):
            self.cfg["shared_session_id"] = self.host.st.agent_session(self.kind) or pinned
        pinned = self.cfg.get("shared_session_id", "")
        self.heal_choices = []
        sessions = claude_sessions(self.cfg["dir"], pinned, not pinned)
        if not sessions and pinned and not (self.native and self.native.returncode is None):
            sessions = self._heal(pinned)       # P147: the pinned session is gone → follow the owner to the new one
        if not sessions and not self.cfg.get("shared_session_id") and self.native is None and allow_start:
            await self.ordinary_start()
            sessions = claude_sessions(self.cfg["dir"])
        if len(sessions) > 1 and not pinned:
            self.heal_choices = [d["sessionId"] for d in sessions]
        if len(sessions) != 1:
            if self.heal_choices and len(self.heal_choices) > 1:
                if self.heal_told != tuple(self.heal_choices):
                    self.heal_told = tuple(self.heal_choices)
                    self.local_fail(self.heal_notice())
                self.failed_start = True
                return False
            if not self.failed_start:
                self.local_fail(pick(getattr(self.host, "lang", "zh"), "共享 Claude 会话不存在或有多个匹配：在电脑指定 session id。", "Choose one live Claude session ID."))
            self.failed_start = True
            return False
        self.session = sessions[0]
        if self.session.get("pid") != self.hook_token_pid:
            self.issue_hook_token()
        await self.prepare_channel()
        sid = self.session.get("sessionId")
        if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid):
            return False
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        slug = re.sub(r"[^A-Za-z0-9]", "-", self.cfg["dir"])
        self.transcript = root / "projects" / slug / (sid + ".jsonl")
        # P147: a window opened a moment ago has no transcript until its first message — that is not "unreadable".
        fresh = not self.transcript.exists() and __import__("time").time() * 1000 - int(self.session.get("startedAt") or 0) < FRESH_MS
        if not self.transcript.is_file() and not fresh and not (self.native and self.native.pid == self.session["pid"]):
            if not self.failed_start:
                self.local_fail(pick(getattr(self.host, "lang", "zh"), "Claude 会话的 transcript 不可读，未投递。", "Claude transcript unavailable."))
            self.failed_start = True
            return False
        self.offset = self.transcript.stat().st_size if self.transcript.exists() else 0
        self.host.st.set_agent_session(self.kind, sid)
        self.failed_start = False
        self.heal_told = None
        if self.status == "down":
            self.set_status("idle")
        if self.healed and self.healed[1] == sid:
            self.healed = None
            self.host.agent_notice(pick(getattr(self.host, "lang", "zh"), f"已接上新会话 {sid[:8]}。",
                                        f"Reconnected to the new session {sid[:8]}."))
        # P142: the phone's model label has a catalogue from the start (it used to say "not supported" until /model ran)
        with contextlib.suppress(Exception):
            await self.refresh_models()
            changed = getattr(self.host, "models_changed", None)
            if callable(changed):
                changed()
        return True

    def start(self):
        super().start()
        self.observer = asyncio.create_task(self.observe())

    async def observe(self):
        while True:
            try:
                if self.session is None:
                    await self.attach()
                if self.session is not None and self.transcript is not None and self.transcript.is_file():
                    self.read_transcript()      # P147: never after the session went away (it would kill this loop)
                if self.session is not None:
                    live = claude_sessions(self.cfg["dir"], self.session["sessionId"])
                    exact = live and live[0].get("pid") == self.session.get("pid")
                    self.validate_session(live[0].get("status") if exact else None)
                self.read_statusline()
                self.sync_compacting()
                if self.model_pending and self.state.status == "idle" and not self.phone_turn and not self.native_lock.locked() \
                        and (self._pending_task is None or self._pending_task.done()):
                    self._pending_task = asyncio.create_task(self._apply_pending())
                if self._observe_errors:
                    self._observe_errors = 0
                    if self._observe_down and self.status == "down":   # P142 (B17): the transient error passed
                        self._observe_down = False
                        self.failed_start = False
                        self.set_status("idle")
                        self.host.st.log("shared_observe_recovered")
            except Exception as e:  # noqa: BLE001 — P192 (B17): any error, not only OSError/ValueError — a KeyError in
                # one pass used to end this task for good (nobody awaits it). CancelledError is a BaseException: still stops.
                # P142 (B17): one EIO / a transcript caught mid-rotation used to mark the shared session down for good.
                # Counted and retried; down only after OBSERVE_RETRIES in a row — and back up once a pass succeeds.
                self._observe_error = type(e).__name__
                self._observe_errors += 1
                if self._observe_errors == OBSERVE_RETRIES:
                    self.host.st.log("shared_observe_down", exception_type=type(e).__name__, count=self._observe_errors)
                    self._observe_down = True
                    self.failed_start = True
                    self.set_status("down")
                    self.done.set()
                elif self._observe_errors < OBSERVE_RETRIES:
                    self.host.st.log("shared_observe_retry", exception_type=type(e).__name__, count=self._observe_errors)
            await asyncio.sleep(5 if self.session is None else 0.25 if not self._observe_errors else min(5, 0.25 * 2 ** self._observe_errors))

    def validate_session(self, status):
        if self.state.validate_against(status):
            self.native_turn = False
            if not self.phone_turn and self.status == "working":
                self.set_status("idle")
        self._reconcile_native(status)

    def _reconcile_native(self, status):
        """P198: native_turn is set by assistant lines and cleared by the turn's Stop. When it is still set while Claude Code's
        own registry and our hook state have both said idle for NATIVE_STALE_S, no turn is running: clear it (else the next
        phone message waits as 「电脑正忙」 and the compaction recovery gives up as busy)."""
        import time
        if not (self.native_turn and status == "idle" and self.state.status in ("idle", "none") and not self.phone_turn):
            self._native_idle_since = None
            return
        now = time.monotonic()
        if self._native_idle_since is None:
            self._native_idle_since = now
        elif now - self._native_idle_since >= NATIVE_STALE_S:
            self._native_idle_since = None
            self.native_turn = False
            self.host.st.log("shared_native_turn_stale", why="registry_idle")

    def _line_of_ended_turn(self, item) -> bool:
        """P198: an assistant line stamped before the last turn end belongs to that ended turn (Claude Code 2.1.296 sends the
        Stop hook before it flushes the turn's last lines) — it must not mark a new native turn."""
        if self.ended_at is None:
            return False
        ts = item.get("timestamp")
        if not isinstance(ts, str):
            return False
        try:
            from datetime import datetime
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() <= self.ended_at
        except ValueError:
            return False

    def read_statusline(self):
        from . import claude_statusline
        sid = (self.session or {}).get("sessionId")
        self.meter(**claude_statusline.read(self.host.st.root, sid, self.compacted_at))

    def read_transcript(self):
        import time
        with self.transcript.open("rb") as f:
            if f.seek(0, 2) < self.offset:
                self.offset = 0
                self.discard_line = False
            f.seek(self.offset)
            # Bounded per tick; never read tool results or child transcripts.
            for _ in range(100):
                line = f.readline(1024 * 1024 + 1)
                if not line:
                    break
                if self.discard_line or len(line) > 1024 * 1024:
                    # Advance through oversized tool-result records in bounded
                    # chunks; otherwise one long line stalls every future turn.
                    self.offset = f.tell()
                    self.discard_line = not line.endswith(b"\n")
                    continue
                if not line.endswith(b"\n"):
                    break
                self.offset = f.tell()
                self._identity_seen(line)
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                ident = item.get("uuid")
                if ident and ident in self.seen:
                    continue
                if ident:
                    self.seen.add(ident)
                msg = item.get("message") or {}
                if not isinstance(msg, dict):
                    msg = {}
                role = item.get("type")
                if item.get("isSidechain"):
                    continue
                if role == "system" and item.get("subtype") == "turn_duration":
                    self._turn_recorded_end()
                    continue
                if role == "attachment":
                    self._phone_anchor((item.get("attachment") or {}).get("prompt") if isinstance(item.get("attachment"), dict) else None)
                    continue
                blocks = msg.get("content", "")
                text = blocks if isinstance(blocks, str) else "\n".join(
                    b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                if role in ("user", "assistant"):
                    self.main_seen_at = time.monotonic()    # P243: main-thread activity restarts the quiet window
                if role == "assistant":
                    if not self._line_of_ended_turn(item):
                        self.native_turn = True   # P135: a native turn is producing; its Stop clears this
                    self._turn_output(msg, blocks)
                elif role == "user" and isinstance(blocks, list):
                    self._tool_results(blocks)
                if not text or role not in ("user", "assistant"):
                    continue
                if role == "user":
                    from .transcript_input import human_input, proactive_origin
                    if item.get("isMeta") and self._from_phone(item, text):
                        self._phone_anchor(text)  # P151b: 2.1.296 writes the peer (phone) message as an isMeta record
                    via = proactive_origin(item, text)
                    if via == "task":
                        for tid in re.findall(r"<task-id>\s*([^<\s]+)\s*</task-id>", text):
                            self.bg_live.pop(tid, None)     # P243: that background task has ended
                    if via:
                        self.native_turn = True
                        self.stop_since_end = False   # P151: a harness-started turn begins (loop tick, task notification)
                        self._turn_begins()
                        self.turn_began_at = handoff.now()
                        self._clear_texts()
                        mark = getattr(self.host, "proactive_turn", None)   # P132: the next agent-only page says where it came from
                        if mark:
                            mark(via)
                        continue
                    if not human_input(item, blocks, text):
                        continue
                    self.stop_since_end = False   # P151: a new turn begins
                    self._turn_begins()
                    self._clear_texts()
                    if self.untrusted_state == "pending" and provenance.names_untrusted(text):
                        self._mark_untrusted("running")   # a native version that runs it without UserPromptSubmit
                    if self._from_phone(item, text):
                        if self.phone_turn and self.pending_text in text:
                            self.phone_seen = True
                            self.input_seen.set()
                            self._phone_anchor(text)
                        continue
                    if self._control_echo(text):
                        continue
                    self.host.desktop_input(public_text(text))
                # Assistant prose is emitted only by the committed Stop payload.
                # Transcript timing is not a completion/ownership signal.
        if self.session is None:
            return
        live = claude_sessions(self.cfg["dir"], self.session["sessionId"])
        if not live and not any(d['pid'] == self.session['pid'] for d in claude_sessions(self.cfg['dir'])):
            self.failed_start = True
            self.set_status("down")
            self.done.set()
            self._lost()

    @staticmethod
    def _from_phone(item, text) -> bool:
        return any('from-name="' + name + '"' in text for name in provenance.FROM_NAMES) or (
            isinstance(item.get("origin"), dict) and item["origin"].get("name") in provenance.FROM_NAMES)

    def _phone_anchor(self, text):
        """P151: the phone input shows up in the transcript (a user record, or a queued_command attachment when it was
        taken inside a running turn): its reply is what the session writes after this point."""
        if self.phone_turn and self.pending_text and isinstance(text, str) and self.pending_text in text:
            self._clear_texts()
            self.phone_anchored = True

    def _clear_texts(self):
        self.turn_texts, self.turn_thinking, self.flushed = [], None, 0

    def _stream_texts(self):
        """P151b (追加 5): the session wrote text and then called a tool: that text is finished — put it on the phone now
        (§10.5 appends it to the page). Only after the phone input itself shows in the transcript, never once the phone
        turn's reply is out. 〔不回群〕 stays silent (agent_text). The end of the turn sends only what is still new."""
        new, self.flushed = self.turn_texts[self.flushed:], len(self.turn_texts)
        if not (self.phone_turn and self.phone_anchored and (self.phone_seen or self.phone_claim)) or self.phone_reply_seen:
            return
        for t in new:
            pub = public_text(t).strip()
            if not pub:
                continue
            if self.phone_claim and not self.streamed:
                self.host.turn_queued_note(False)
            if not self.streamed:
                self.host.st.log("shared_text_streamed")
            self.streamed.append(pub)
            self.host.agent_text(pub)

    def _turn_begins(self):
        self.turn_closed = False
        self.turn_gen += 1
        self.endturn_ids.clear()
        self.last_tool = None

    def _turn_output(self, msg, blocks):
        if not isinstance(blocks, list):
            blocks = [{"type": "text", "text": blocks}] if isinstance(blocks, str) else []
        if self.turn_closed:
            self.turn_closed = False    # P151b: the session wrote on after its end-turn tool: that end is not the last
        self.turn_last_msg = msg.get("id")
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and str(b.get("text") or "").strip():
                self.turn_texts.append(b["text"])
                self.last_tool = None
            elif b.get("type") == "thinking" and str(b.get("thinking") or "").strip():
                self.turn_thinking = (msg.get("id"), b["thinking"])
            elif b.get("type") == "tool_use" and isinstance(b.get("name"), str):
                self._stream_texts()
                self.last_tool = (b["name"][:40], tool_what(b["name"], b.get("input")), handoff.now(), b.get("id"))
                if b["name"] in END_TURN_TOOLS and b.get("id"):
                    self.endturn_ids.add(b["id"])

    def _tool_results(self, blocks):
        """P151b: the result of an END_TURN_TOOLS call (ScheduleWakeup) is the end of the turn, whether or not a Stop or a
        turn_duration ever follows (the process stays alive waiting for its wakeup)."""
        for b in blocks:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            ident = b.get("tool_use_id")
            if self.last_tool and ident == self.last_tool[3]:
                self.last_tool = (self.last_tool[0], self.last_tool[1], None, ident)   # finished: the model thinks on
            if ident in self.endturn_ids:
                self.endturn_ids.discard(ident)
                self._turn_recorded_end(tool=True)

    def _schedule_empty_stop(self):
        loop = self.loop
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is not None:
            loop.call_later(EMPTY_STOP_WAIT, self._empty_stop_end, self.turn_gen)

    def _empty_stop_end(self, gen):
        """P151b: a native Stop without last_assistant_message and, EMPTY_STOP_WAIT later, no recorded end of the turn yet:
        the Stop is the end. The reply is the turn's last text (or, owner only, its final visible thinking)."""
        if self.transcript is not None and self.transcript.is_file():
            with contextlib.suppress(OSError, ValueError):
                self.read_transcript()
        if gen != self.turn_gen or self.turn_closed or self.stop_since_end:
            return
        self.turn_closed = True
        reply = self._turn_reply()
        self._clear_texts()
        self.host.st.log("shared_turn_end_empty_stop", reply=bool(reply))
        self._end_without_stop(reply)

    def _turn_reply(self):
        """The last assistant text of the turn. Owner turns only: when the session put its answer solely in a visible
        thinking block of the turn's final message (owner report 2026-10-12, before ScheduleWakeup), that block."""
        if self.turn_texts:
            return self.turn_texts[-1].strip()
        if self.phone_turn and provenance.is_owner(self.pending_source) and self.turn_thinking \
                and self.turn_thinking[0] == self.turn_last_msg:
            return self.turn_thinking[1].strip()
        return ""

    def _turn_recorded_end(self, tool=False):
        """P151: Claude Code wrote this turn's end. A turn ending on a tool call (ScheduleWakeup, Monitor, …) sends no Stop
        hook; after TURN_END_GRACE without one the recorded end commits the turn with its last text.
        P151b: tool=True — the result of an end-turn tool (ScheduleWakeup), which ends the turn before turn_duration. One
        end per turn: whichever comes first (committed Stop, end-turn tool, turn_duration, empty Stop) closes it."""
        if self.turn_closed and not tool:
            return                      # the turn_duration after the end-turn tool's result: already handled
        if self.stop_since_end:
            if tool:                    # a committed Stop already answered; the turn_duration that follows pairs with it
                self.turn_closed = True
                return
            self.stop_since_end = False
            self._clear_texts()
            return
        if self.turn_closed:
            return
        self.turn_closed = True
        reply = self._turn_reply()
        self._clear_texts()
        loop = self.loop
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is None:
            return
        loop.call_later(TURN_END_GRACE, self._end_without_stop, reply)

    def _end_without_stop(self, reply):
        import time
        if self.stop_since_end:         # the native Stop came after all (later than the transcript's end record)
            self.stop_since_end = False
            return
        phone = self.phone_turn and (self.phone_seen or self.phone_claim)
        self.host.st.log("shared_turn_end_no_stop", phone=phone, reply=bool(reply))
        self._withdraw_answered("Stop", {})
        end = getattr(self.state, "end_without_stop", None)
        if end:
            end(reply or None)
        if reply:
            self.end_claimed = (public_text(reply), time.monotonic())
            self._finish_stop(reply, source="turn-end")
            return
        if self.untrusted_state == "running":
            self.untrusted_state = None
        self.native_turn = False
        self.ended_at = time.time()
        emit = getattr(self.host, "emit", None)
        if emit:
            emit("shared_stop", agent="claude", phone=self.phone_turn, phone_input=phone, source="turn-end-empty")
        if phone and self.streamed:     # P151b: the turn's text is already on the phone; nothing is missing
            self.phone_reply_seen = True
            self.done.set()
        elif phone:
            if self.phone_claim:
                self.host.turn_queued_note(False)
            self.phone_reply_seen = True
            text = pick(getattr(self.host, "lang", "zh"), "共享 Claude 这一回合已结束，但没有写文字回复。",
                        "The shared Claude turn ended without a text reply.")
            note = getattr(self.host, "turn_wait_note", None)
            if note:                    # P151b: on the page itself — never a separate 「这一页没有你的输入」 page
                note(text, final=True)
            else:
                self.host.agent_notice(text)
            self.done.set()
        else:
            self.host.desktop_end()
            self.system_done.set()

    # ------------------------------------------------ P151 (追加 1/2): compaction / clear handoff
    def _handoff_context(self, source) -> str:
        """SessionStart(compact|clear) context: where the handoff is (also covers an owner message that beats the host's
        recovery message)."""
        lang = getattr(self.host, "lang", "zh")
        if source == "compact" and handoff.mtime(handoff.COMPACT) is not None:
            return "\n\n" + handoff.recover_text(lang, "compact", True)
        if source == "clear" and handoff.mtime(handoff.CLEAR) is not None:
            # P198: the host's recovery renames it a moment later (applies once) — measured on 2.1.296, the Agent then
            # reported the named file missing before reading the renamed one
            return "\n\n" + handoff.recover_text(lang, "clear", True, handoff.path(handoff.CLEAR)) + (
                " If it is gone, it was renamed " + handoff.CLEAR_USED + " in the same folder: read that." if lang == "en" else
                "如果找不到，它已在同一目录改名为 " + handoff.CLEAR_USED + "，读那个。")
        return ""

    def _schedule_recover(self, kind, has):
        loop = self.loop
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is None:
            return
        self.recover_at = handoff.now()
        if self.recover_task and not self.recover_task.done():
            self.recover_task.cancel()
        self.recover_task = loop.create_task(self._recover(kind, has, self.turn_began_at))

    async def _recover(self, kind, has, began):
        """After RECOVER_DELAY with no new turn and the session idle, the host sends its recovery message itself."""
        await asyncio.sleep(handoff.RECOVER_DELAY)
        why = None
        stale = 0
        for _ in range(20):
            if self.turn_began_at != began or self.phone_turn:
                why = "new_turn"
            elif self.session is None or self.host.stopped() is True:
                why = "offline"
            elif self.state.status not in ("idle", "none"):
                why = "busy"            # Claude Code's automatic compaction inside a turn: the turn goes on by itself
            elif not await asyncio.to_thread(self._registry_quiet):
                why = "busy"
            elif self.native_turn and stale < 2:
                # P198: our state and Claude Code's registry both idle; only native_turn says busy — a flag left by the
                # ended turn's late transcript lines (2.1.296: no Stop follows a /compact or /clear). Confirm, then clear.
                stale += 1
                why = "busy"
            else:
                if self.native_turn:
                    self.native_turn = False
                    self.host.st.log("shared_native_turn_stale", why="recover", kind=kind)
                why = None
                break
            if why != "busy":
                break
            await asyncio.sleep(0.25)
        if why:
            self.host.st.log("shared_handoff_recover", kind=kind, result=why, handoff=bool(has))
            return
        lang = getattr(self.host, "lang", "zh")
        where = None
        if kind == "clear":
            where = handoff.take_clear()
            if where is None:
                return
        text = handoff.recover_text(lang, kind, bool(has), where)
        try:
            await asyncio.to_thread(claude_send, self.session, text, provenance.system())
        except (OSError, ConnectionError, KeyError, TypeError):
            self.host.st.log("shared_handoff_recover", kind=kind, result="send_failed", handoff=bool(has))
            return
        self.host.st.log("shared_handoff_recover", kind=kind, result="sent", handoff=bool(has))
        self.host.agent_notice(handoff.phone_line(lang, kind))

    async def _handoff_prep(self) -> bool:
        """Phone /compact: the handoff is missing or older than this turn → one host turn asking for it first. True when
        the file is fresh afterwards."""
        lang = getattr(self.host, "lang", "zh")
        handoff.ensure()
        before = handoff.mtime(handoff.COMPACT)
        self.system_done.clear()
        try:
            await asyncio.to_thread(claude_send, self.session, handoff.prep_text(lang), provenance.system())
        except (OSError, ConnectionError, KeyError, TypeError):
            self.host.st.log("shared_handoff_prep", result="send_failed")
            return False
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.system_done.wait(), handoff.PREP_WAIT)
        after = handoff.mtime(handoff.COMPACT)
        ok = after is not None and after != before
        self.host.st.log("shared_handoff_prep", result="ok" if ok else "missing")
        return ok

    def _registry_quiet(self) -> bool:
        """P243: the registry says idle — or "shell"/"busy" that is only background work (Stop.background_tasks) and the
        main thread is idle. The post-compaction recovery message must not wait for a background `sleep`."""
        live = claude_sessions(self.cfg["dir"], (self.session or {}).get("sessionId", "")) if self.session else []
        if not live:
            return False
        reg = live[0].get("status")
        return reg == "idle" or (reg in ("shell", "busy") and (reg == "shell" or bool(self.bg_live))
                                 and self.state.status in ("idle", "none") and not self.phone_turn)

    def _registry_idle(self) -> bool:
        live = claude_sessions(self.cfg["dir"], (self.session or {}).get("sessionId", "")) if self.session else []
        return bool(live) and live[0].get("status") == "idle"

    async def turn(self, text):
        if self.session is None and not await self.attach():
            return
        # Cross-session messages need not emit UserPromptSubmit in every native
        # version; the phone turn boundary must also revoke prior risk grants.
        self.risk_epoch += 1
        self.permissions.clear()
        self.risk_requests.clear()
        self.risk_grants.clear()
        self.risk_denied.clear()
        self.done.clear()
        self.input_seen.clear()
        self.phone_turn = True
        self.phone_seen = self.phone_reply_seen = self.phone_claim = False
        self.phone_anchored, self.streamed, self.flushed = False, [], len(self.turn_texts)
        source = getattr(getattr(self, "cur_send", None), "source", None)
        self.pending_source = source
        self.pending_text = provenance.body(text, source)   # what the transcript / prompt will show
        if not provenance.is_owner(source):
            self._mark_untrusted("pending")
        try:
            await self.deliver(lambda: asyncio.to_thread(claude_send, self.session, text, source))
            try:
                await asyncio.wait_for(self.input_seen.wait(), CLAUDE_INPUT_WAIT)
            except asyncio.TimeoutError:
                # Socket write is not native acceptance. Name the concrete reason (P127): managed policy, project /
                # local / user setting, owner off, a session started before the change, or simply busy.
                from . import claude_inbound
                if not await self._wait_busy_input(claude_inbound):
                    notice = claude_inbound.turn_notice(self.cfg.get("dir"), self.cfg.get("language", "zh")) + " " + claude_inbound.diagnostic(self.host.st, self.cfg.get("language", "zh"), self.cfg)
                    self.fail_notice(notice)
                    return
            # A committed native Stop belonging to this phone input ends the turn; so does the turn end the transcript
            # records when no Stop comes (P151, _turn_recorded_end). Registry idle and flush timing alone never do.
            await self._await_turn_end()
        except asyncio.TimeoutError:
            if await self._idle_reply():
                return
            if self.streamed:           # P151b (追加 5): its text is on the phone; the turn simply runs on at the computer
                self.host.st.log("shared_turn_end_streamed")
                self.phone_reply_seen = True
                return
            lang = getattr(self.host, "lang", "zh")
            why = self._wait_line(lang, self._turn_waited())
            text = (pick(lang, "共享 Claude 没有确认这一回合结束：", "The shared Claude never confirmed the end of this turn: ")
                    + why + pick(lang, "。请在电脑核实。", ". Please check the computer."))
            note = getattr(self.host, "turn_wait_note", None)
            if note:                    # P151b: the reason on the page itself, no separate 「这一页没有你的输入」 page
                fn = getattr(self.host, "turn_failed", None)
                if fn:
                    fn()
                note(text, final=True)
            else:
                self.fail_notice(text)
        finally:
            self.phone_turn = self.phone_claim = False
            self.wait_more = None
            note = getattr(self.host, "turn_wait_note", None)
            if note:
                note(None)

    async def _idle_reply(self) -> bool:
        """P151: no Stop and no recorded turn end (an older native version), but the session answered and is idle."""
        reply = self._turn_reply() if self.phone_seen or self.phone_claim else ""
        if reply and await asyncio.to_thread(self._registry_idle):
            self.host.st.log("shared_turn_end_no_stop", phone=True, reply=True, via="registry-idle")
            self._clear_texts()
            self._end_without_stop(reply)
            return True
        return False

    def _turn_waited(self) -> float:
        import time
        return time.monotonic() - (self.turn_wait_t0 or time.monotonic())

    def _wait_line(self, lang, secs) -> str:
        """P151b (追加 4): what the host sees of a running turn, in one line: the tool it waits on (and for how long), or
        that the model is thinking since its last tool."""
        mins = max(1, int(secs // 60))
        tool = self.last_tool
        if tool and tool[2] is not None:
            name = tool[0] + ("：" + tool[1] if tool[1] and lang == "zh" else ": " + tool[1] if tool[1] else "")
            run = max(1, int((handoff.now() - tool[2]) // 60))
            return pick(lang, f"正在等待 {name}（已 {run} 分钟）", f"waiting for {name} ({run} min)")
        if tool:
            return pick(lang, f"{tool[0]} 已结束，Agent 还在想（已 {mins} 分钟没有回复）",
                        f"{tool[0]} finished; the Agent is still thinking (no reply for {mins} min)")
        return pick(lang, f"Agent 还在想（已 {mins} 分钟没有回复）", f"the Agent is still thinking (no reply for {mins} min)")

    def _show_wait(self, stall=False):
        note = getattr(self.host, "turn_wait_note", None)
        if not note:
            return
        lang = getattr(self.host, "lang", "zh")
        line = self._wait_line(lang, self._turn_waited())
        if stall:
            line = pick(lang, "共享 Claude 还没结束这一回合：", "The shared Claude has not ended this turn: ") + line
        note(line, stall=stall)

    async def _await_turn_end(self):
        """The phone turn's wait (P151b, 追加 4): after WAIT_NOTE_AFTER without the reply the page shows what the host sees
        (the tool it waits on, how long); at TURN_WAIT a stall card offers 「继续等」 (another TURN_WAIT) or 「中断」. No
        answer to the card: TimeoutError at the second limit, with the reason on the page."""
        import time
        loop = asyncio.get_running_loop()
        self.turn_wait_t0 = time.monotonic()
        self.wait_more = asyncio.Event()
        limit, stalled = loop.time() + TURN_WAIT, False
        while not self.done.is_set():
            left = limit - loop.time()
            if left <= 0:
                if stalled:
                    raise asyncio.TimeoutError
                if await self._idle_reply():
                    return
                stalled = True
                limit = loop.time() + TURN_WAIT
                self.host.st.log("shared_turn_stall", tool=(self.last_tool or ("-",))[0])
                self._show_wait(stall=True)
                continue
            waits = [asyncio.ensure_future(self.done.wait()), asyncio.ensure_future(self.wait_more.wait())]
            try:
                await asyncio.wait(waits, timeout=min(left, 10), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for w in waits:
                    w.cancel()
            if self.done.is_set():
                return
            if self.wait_more.is_set():
                self.wait_more.clear()
                stalled, limit = False, loop.time() + TURN_WAIT
                self.host.st.log("shared_turn_wait_more")
                self._show_wait()
            elif self._turn_waited() >= WAIT_NOTE_AFTER:
                self._show_wait(stall=stalled)

    def native_busy(self) -> bool:
        return (self.state.status == "working" or self.native_turn or self.state.snapshot().get("compacting") is True
                or self.status == "compacting")

    async def _wait_busy_input(self, claude_inbound) -> bool:
        # P133: busy, not held → 「电脑正忙，消息已排队」 and keep waiting.
        # P135: a message injected into a native turn already running (a background-task notification, a desktop turn)
        # gets no UserPromptSubmit: the next committed Stop — whichever turn it ends — answers this page (phone_claim).
        # More phone messages meanwhile go in too; the reply lands on the last page, earlier ones say 「已合并到下一条回复」.
        if not self.native_busy() or claude_inbound.hold_reason(self.cfg.get("dir")):
            return False
        self.phone_claim = True
        self.host.turn_queued_note(True)
        deadline = asyncio.get_running_loop().time() + DESKTOP_IDLE_WAIT
        try:
            while not (self.input_seen.is_set() or self.done.is_set()):
                left = deadline - asyncio.get_running_loop().time()
                if left <= 0:
                    return False
                await self._merge_queued()
                waits = [asyncio.ensure_future(self.input_seen.wait()), asyncio.ensure_future(self.done.wait())]
                try:
                    await asyncio.wait(waits, timeout=min(left, .25), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for w in waits:
                        w.cancel()
            return True
        finally:
            self.host.turn_queued_note(False)

    async def _merge_queued(self) -> None:
        """P135: the next queued phone message joins the native turn this page waits on."""
        q = getattr(self.q, "_queue", None)
        nxt = q[0] if q else None
        if getattr(nxt, "sid", None) is None or self.halting or not self.session:
            return
        self.q.get_nowait()
        if not await nxt.wait_ready() or nxt.state == "cancelled":
            return
        merged = getattr(self.host, "turn_merged", None)
        if merged:
            merged()
        self.cur_send = nxt
        self.host.agent_turn_start(nxt.text, nxt)
        from . import compactprep
        text = compactprep.decorate(self, nxt)
        source = getattr(nxt, "source", None)
        self.pending_source, self.pending_text = source, provenance.body(text, source)
        if not provenance.is_owner(source):
            self._mark_untrusted("pending")
        self.host.turn_queued_note(True)
        await self.deliver(lambda: asyncio.to_thread(claude_send, self.session, text, source))

    async def halt(self, clear_queue=True):
        self.halting = True
        if self.control_name:
            self.control_ack.set()  # wake the wait; halting prevents a success claim
        busy = self.status == "working" or self.state.status == "working"
        if clear_queue:
            while not self.q.empty():
                item = self.q.get_nowait()
                if getattr(self.host, "queue_dropped", None):
                    self.host.queue_dropped(item)
        # Esc must never answer a native approval dialog.
        if self.state.status == "waiting":
            self.host.agent_notice(pick(getattr(self.host, "lang", "zh"), "请先处理审批卡片。", "Resolve the approval card first."))
            return False
        if not busy or not self.session:
            return busy
        if self.native and self.native.pid == self.session["pid"] and self.master is not None:
            os.write(self.master, b"\x1b")
            self.done.set()
            return True
        # Relay's validated Herdr route, bound to an exact session id.
        from .shared_interrupt import interrupt
        result = await asyncio.to_thread(interrupt, self.session)
        if result == "sent":
            self.done.set()
        else:
            self.host.agent_notice(pick(getattr(self.host, "lang", "zh"), "此电脑终端未提供安全中断通道。", "Desktop interrupt unavailable."))
        return result == "sent"

    async def stop(self):
        self.meter(model=None, model_name=None, effort=None, ctx=None, h5=None, week=None, source_at=None)
        # Dispose only the actor we launched. Never call Agent.halt on an owner TUI.
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.observer:
            self.observer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.observer
        if self.hook_watch:
            self.hook_watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.hook_watch
            self.hook_watch = None
        if self.hook_token_pid is not None:
            with contextlib.suppress(OSError, TypeError):
                shared_hook.token_path(self.host.st.root, self.hook_token_pid).unlink(missing_ok=True)
            self.hook_token = self.hook_token_pid = None
        if self.channel:
            await self.channel.stop()
        if self.native and self.native.returncode is None:
            self.native.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.native.wait(), 5)
            if self.native.returncode is None:
                self.native.kill()
                await self.native.wait()
        if self.master is not None:
            self.loop.remove_reader(self.master)
            os.close(self.master)
            self.master = None


class _ExternalServer:
    # Lifecycle sentinel for inherited SSE/turn code, never an OS subprocess.
    returncode = None


def owner_password_note() -> str:
    """P64: how the owner hands their OpenCode server's password to Agent J — through the phone's secret card into the service
    environment, never by Agent J reading another process's environment (Leo / Jarvis 2026-10-06)."""
    from . import service
    try:
        env_file = service.env_file(service.name())
    except Exception:  # noqa: BLE001 — the note must never fail
        env_file = "<Agent J 服务的环境文件 / the Agent J service env file>"
    cmd = (f"agentj secret request --name OPENCODE_SERVER_PASSWORD --purpose '你电脑上 OpenCode server 的密码' "
           f"--dest env:{env_file}#OPENCODE_SERVER_PASSWORD")
    return ("你电脑上开着的 OpenCode server 要密码，Agent J 还没有这把密码（它不会去读别的程序的环境变量）。在电脑上运行："
            f"`{cmd}`，在手机的密钥卡片里贴上你启动 server 时设的 OPENCODE_SERVER_PASSWORD（用户名不是 opencode 的话，再用同样的方法存"
            " OPENCODE_SERVER_USERNAME），然后运行 `agentj service restart`。"
            f" / Your OpenCode server needs a password Agent J does not have (it never reads another program's environment). Run `{cmd}`,"
            " paste the server's OPENCODE_SERVER_PASSWORD on the phone's secret card (OPENCODE_SERVER_USERNAME too if it is not"
            " \"opencode\"), then `agentj service restart`.")


class SharedOpenCodeAgent(OpenCodeAgent):
    """Existing loopback server + exact session; native rules are never PATCHed."""
    def __init__(self, host, cfg):
        super().__init__(host,cfg)
        self.risk=RiskGuard(self)
        self.risk_channel=None
        self.owned_server=False   # P59: True once Agent J started this ordinary server itself (restartable, same session)

    async def prepare_risk(self, owner=False):
        # P198: Agent J's own ordinary server always loads the guard (the five clear dangers ask on the phone); a server the
        # OWNER started keeps it opt-in (agent.high_risk_warnings: loading it disposes their instance, refused while busy)
        if (owner and not self.cfg.get('high_risk_warnings',False)) or not danger.shared_guard(self.cfg) or self.risk_channel:
            if owner and not self.risk_channel:
                self.host.st.log('shared_guard_unavailable', agent=self.kind, reason='owner_server')
            return
        # P198 (C): under agentperm/ — the only part of the state dir the fence binds back (fence.py); at <state>/ the fenced
        # plugin's hook could not reach it, SharedGuardReady never came and every turn ended in TimeoutError
        self.risk_channel=shared_hook.Channel(self.risk,shared_hook.channel_path(self.host.st.perm_dir,'opencode'))
        await self.risk_channel.start()
        shared_opencode_hook.install(self.cfg['dir'],self.risk_channel.path)

    async def stop(self):
        await super().stop()
        if self.risk_channel:
            await self.risk_channel.stop()

    # P63: our own ordinary server (owned_server) follows the installed OpenCode — v2 = shared_opencode2 — and, being Agent
    # J's child, takes the owner's model choice and the phone's slash commands; an owner server stays text-only.
    def _own_protocol(self):
        return self.owned_server

    def _switch(self, version):
        from .shared_opencode2 import switch_shared
        return switch_shared(self, version)

    async def command(self, name, arg):
        initial = getattr(self, "attach_task", None)
        if initial is not None and not initial.done():
            await initial
        if self.v2:
            return await self.command(name, arg)      # switched to shared_opencode2 while starting
        if self.owned_server:
            return await OpenCodeAgent.command(self, name, arg)
        from .slash import Result
        if name in ("compact", "context", "cost", "usage", "status", "help"):
            res = await OpenCodeAgent.command(self, name, arg)
            if name == 'compact' and res.kind == 'ok':
                self.meter(ctx=None)
                with contextlib.suppress(OSError, ValueError, HTTPError, asyncio.TimeoutError):
                    await self.context_meter(include_quota=False)
            return res
        return Result(pick(getattr(self.host, "lang", "zh"), "没有执行：附加的 OpenCode 服务器不提供切换桌面会话的接口；用桌面 /new 新建、/model 换模型，再指定新 session id。", "Not executed: attached server cannot switch the desktop session; use /new or /model there, then select its session ID."), "error")

    async def apply_model(self, model, effort, default=False):
        if self.owned_server:
            return await super().apply_model(model, effort, default)
        return "unsupported"

    async def _session_ok(self):
        """Owned serve: the session agent.json names (after /clear or 「撤销清空」 it changed), else a new ordinary one."""
        if not self.owned_server:
            return await super()._session_ok()
        if self.sid is not None and self.sid == self.host.st.agent_session(self.kind):
            return True
        self.cfg["shared_session_id"] = self.host.st.agent_session(self.kind) or ""
        try:
            await self._prepare()
            return True
        except (OSError, HTTPError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError) as e:
            self.fail_notice(f"OpenCode 没能打开这段对话（{type(e).__name__}）：这条没有交给它。")
            return False

    def launch_argv(self, argv):
        from .provider_runtime import shared_launch
        return shared_launch(self, argv)

    async def _prepare(self):
        # Native server startup keeps native rules: no independent session_rules
        # or permission PATCH. Owned main identity is supplied on each phone turn.
        self.connected_at_start = None                # P63: which providers this owned serve has a key for (no_key class)
        if self.owned_server:
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                st, pv = await self.client.request("GET", "/provider")
                if st == 200 and isinstance(pv, dict) and isinstance(pv.get("connected"), list):
                    self.connected_at_start = [x for x in pv["connected"] if isinstance(x, str)]
        sid = self.cfg.get("shared_session_id")
        if self.owned_server and isinstance(sid, str) and sid:
            # P59 (A167): our own server restarted (credentials / proxy changed): the same ordinary session again
            status, session = await self.client.request("GET", f"/session/{sid}")
            if (status == 200 and isinstance(session, dict) and session.get("id") == sid
                    and os.path.realpath(session.get("directory") or "") == os.path.realpath(self.cfg["dir"])):
                self.sid, self.rules = sid, []
                self.host.st.set_agent_session(self.kind, sid)
                await self._prime()
                return
        status, session = await self.client.request("POST", "/session", {"title": "Agent J shared"})
        if status != 200 or not isinstance(session, dict) or not isinstance(session.get("id"), str):
            raise HTTPError("ordinary session creation failed")
        if os.path.realpath(session.get("directory") or "") != os.path.realpath(self.cfg["dir"]):
            raise HTTPError("ordinary session has wrong project")
        self.sid = session["id"]
        self.rules = []
        self.cfg["shared_session_id"] = self.sid
        self.host.st.set_agent_session(self.kind, self.sid)
        await self._prime()

    async def _spawn(self):
        port = self.cfg.get("shared_opencode_port")
        if not port and (not self.cfg.get("shared_session_id") or self.owned_server):
            # An explicit shared experiment without an owner session starts an
            # ordinary native server/session. Native config and permissions stay
            # untouched. A stale explicit owner selector never falls through here.
            self.owned_server = True
            if not await self._installed_v2():        # P63: the v1 plugin is not a v2 plugin (shared_opencode2)
                await self.prepare_risk()
            return await super()._spawn()
        if type(port) is not int or not 1 <= port <= 65535:
            self.local_fail(pick(getattr(self.host, "lang", "zh"), "指定电脑 OpenCode server 的 loopback 端口。", "Set the existing OpenCode loopback port."))
            return False
        # Owner's server password is read from its native environment, never config
        # or phone input. Empty supports a native unauthenticated loopback server.
        self.client = Client(port, os.environ.get("OPENCODE_SERVER_PASSWORD", ""),
                             os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"))
        self.proc = _ExternalServer()
        self.owner_unauthorized = False
        if await self._owner_v2():
            # P64: the owner's v2 server is attached (text, cards, desktop mirroring) by shared_opencode2's owner class
            from .shared_opencode2 import OwnerOpenCodeV2Agent
            self.proc = None
            self.__class__ = OwnerOpenCodeV2Agent
            self.host.st.log("agent_protocol", agent=self.kind, version="v2", owner=True)
            return await self._spawn()
        if self.owner_unauthorized:                   # P64: the owner's server wants a password Agent J does not have
            self.proc = None
            self.failed_start = True
            self.local_fail(owner_password_note())
            return False
        await self.prepare_risk(owner=True)           # the v1 pre-execution plugin on an owner's v1 server: opt-in
        try:
            await self._attach()
            if self.risk_channel:
                status, busy=await self.client.request('GET','/session/status')
                if status != 200 or not isinstance(busy,dict) or any(
                    (v or {}).get('type') != 'idle' for v in busy.values()):
                    raise HTTPError('native server busy; guard reload refused')
                # Native instance reload retains persisted session ids and owner
                # permissions. Never reload an instance with another active turn.
                status,_=await self.client.request('POST','/instance/dispose',{})
                if status not in (200,204):
                    raise HTTPError('native plugin reload unavailable')
                await self._attach()
                await asyncio.wait_for(self.risk.ready.wait(),CONNECT_WAIT)
            await self._prime()
            self._bg(self._events(self.proc))
            await asyncio.wait_for(self.connected.wait(), CONNECT_WAIT)
            await self._resync()
        except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
            self.proc = None
            self.failed_start = True
            self.fail_notice(pick(getattr(self.host, "lang", "zh"), "不能附着电脑 OpenCode server/session。", "Cannot attach the desktop OpenCode server/session."))
            return False
        return True

    async def _installed_v2(self):
        from .harness import version_of, opencode_v2
        exe = _bin("AGENTJ_OPENCODE_BIN", "opencode")
        return bool(exe) and opencode_v2(await asyncio.to_thread(version_of, exe))

    async def _owner_v2(self):
        """The owner's server speaks v2 (its legacy routes answer HTML; the API is /api/*): OwnerOpenCodeV2Agent attaches it."""
        try:
            status, info = await self.client.request("GET", "/api/info")
        except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
            return False
        self.owner_unauthorized = status == 401
        return status == 200 and isinstance(info, dict) and isinstance(info.get("version"), str) \
            and info["version"].lstrip("v").startswith("2")

    async def _attach(self):
        sid = self.cfg.get("shared_session_id")
        if not isinstance(sid, str) or not re.fullmatch(r"ses[A-Za-z0-9_-]+", sid):
            raise HTTPError("exact session id required")
        status, session = await self.client.request("GET", f"/session/{sid}")
        if status != 200 or not isinstance(session, dict) or session.get("id") != sid:
            raise HTTPError("session unavailable")
        if os.path.realpath(session.get("directory") or "") != os.path.realpath(self.cfg["dir"]):
            raise HTTPError("wrong working directory")
        self.sid = sid
        self.rules = []
        self.host.st.set_agent_session(self.kind, sid)

    async def _prime(self):
        status, messages = await self.client.request("GET", f"/session/{self.sid}/message")
        if status != 200 or not isinstance(messages, list):
            raise HTTPError("messages unavailable")
        self.meter()  # P98: attached native identity, even without model/quota data.
        # Existing history is not replayed as fresh phone output.
        self.desktop_messages = set()
        self.message_roles = {}
        self.phone_messages = set()
        self.desktop_turn = False
        for message in messages:
            info = message.get("info") or {}
            self.desktop_messages.add(info.get("id"))
            self.message_roles[info.get("id")] = info.get("role")
            self.emitted.update(p.get("id") for p in message.get("parts") or [])

    def start(self):
        super().start()
        self.attach_task = asyncio.create_task(self._spawn())
        self.tasks.add(self.attach_task)
        self.attach_task.add_done_callback(self.tasks.discard)

    def _on_perm(self, req):
        # Reconnection calls this directly with /permission's server-wide list,
        # bypassing on_event. Keep that path confined to the selected session too.
        if req.get("sessionID") != self.sid:
            return
        super()._on_perm(req)

    def on_event(self, ev):
        p = ev.get("properties") or {}
        if ev.get("type") == "message.updated":
            info = p.get("info") or {}
            if info.get("sessionID") != self.sid:
                return
            mid = info.get("id")
            self.message_roles[mid] = None if info.get("synthetic") or info.get("ignored") else info.get("role")
        if ev.get("type") == "message.part.updated":
            part = p.get("part") or {}
            mid = part.get("messageID")
            if part.get("sessionID") == self.sid and self.message_roles.get(mid) == "user":
                if part.get("type") == "text" and mid not in self.desktop_messages:
                    from .transcript_input import human_input
                    if not human_input(part, [part], part.get("text")):
                        return
                    self.desktop_messages.add(mid)
                    if mid not in self.phone_messages:
                        self.desktop_turn = True
                        self.risk.reset(mid)
                        self.host.desktop_input(public_text(part.get("text") or ""))
                return
        if ev.get("type") in ("permission.asked", "question.asked") and p.get("sessionID") != self.sid:
            return
        if ev.get("type") in ("permission.replied", "question.replied", "question.rejected"):
            # Desktop is a legitimate native approver. Resolve phone cards without
            # answering again, and never treat its native decisions as tampering.
            if p.get("sessionID") != self.sid:
                return
            rid = p.get("requestID")
            for pending in (self.pending, self.questions):
                f = pending.get(rid)
                if f and not f.done():
                    f.set_result(True)
            return
        if ev.get("type") in ("session.idle", "session.status") and p.get("sessionID") == self.sid:
            if ev.get("type") == "session.idle" or (p.get("status") or {}).get("type") == "idle":
                self.host.desktop_end()
                self.desktop_turn = False
        super().on_event(ev)

    def _text_part(self, part):
        if not isinstance(part, dict) or self.message_roles.get(part.get("messageID")) != "assistant":
            return
        if part.get("sessionID") != self.sid or part.get("type") != "text" or not (part.get("time") or {}).get("end"):
            return
        if part.get("id") in self.emitted or part.get("synthetic") or part.get("ignored"):
            return
        self.emitted.add(part.get("id"))
        if self.quiet:
            return
        text = public_text(strip_reasoning(part.get("text") or ""))   # P198: a model's </think> reasoning is not the reply
        if text:
            (self.host.desktop_text if self.desktop_turn else self.host.agent_text)(text)

    async def turn(self, text):
        initial = getattr(self, "attach_task", None)
        if initial is not None and not initial.done():
            if not await initial:
                return
        if self.v2:                 # P63: switched to shared_opencode2 while starting
            return await self.turn(text)
        await self._creds_check()   # P59: only our own server (owned_server); never the owner's attached one
        if self.proc is None and not await self._spawn():
            return
        if self.v2:
            return await self.turn(text)
        if self.owned_server and not await self._session_ok():
            return
        if self.risk_channel:
            try:
                await asyncio.wait_for(self.risk.ready.wait(),CONNECT_WAIT)
            except asyncio.TimeoutError:   # P198 (C): fail closed, but say why (was a bare 「Agent 出错（TimeoutError）」)
                self.host.st.log('shared_guard_not_ready', agent=self.kind, channel=str(self.risk_channel.path))
                self.local_fail(pick(getattr(self.host, "lang", "zh"),
                    "手机审批守卫没有在 OpenCode 里就位，这条没有交给 Agent（安全起见不放行）。请运行 `agentj doctor`，或在设置里关掉隔离后重试。",
                    "The phone approval guard did not come up inside OpenCode, so this message was not handed over (fails closed). "
                    "Run `agentj doctor`, or retry with isolation off."))
                return
        self.risk.reset()
        self.turn_done = asyncio.Event()
        self.turn_progress = __import__("time").monotonic()
        self.saw_busy = False
        self.provider_fail_noted = self.restart_after_turn = False
        mid = "msg" + uuid.uuid4().hex
        self.phone_messages.add(mid)
        self.turn_proc = self.proc
        from . import main_identity
        from .agent_opencode import turn_body
        main = self.owned_server and self.persist and not self.cfg.get('_workflow_ceo')
        # Owned main gets its full constitution; an attached owner keeps native
        # identity and receives only this phone turn's language instruction (+ P142's hidden language reminder).
        body = {"messageID": mid, **turn_body(text, self.cfg, main)}
        m = split_model(self.cfg.get("model")) if self.owned_server else None
        if m:                       # P63: the owner's `agentj agent opencode --model` / phone /model on our own server
            body["model"] = m
        async def post():
            status, _ = await self.client.request("POST", f"/session/{self.sid}/prompt_async", body)
            if status not in (200, 204):
                raise HTTPError("prompt refused")
            return True
        await self.deliver(post)
        if main:
            if not hasattr(self, 'identity_sent'):
                self.identity_sent = {}
            self.identity_sent[self.sid] = body['system']
            main_identity.audit(self.cfg, self.kind, self.host.st, self.sid)
        await self._wait_turn()
        if self.owned_server and self.persist and self.collect is None:
            self.refresh_usage()
        if self.restart_after_turn and self.owned_server and self.proc and not isinstance(self.proc, _ExternalServer):
            self.restart_after_turn = False       # P60/P63: a key / provider failure — the next message gets a fresh serve
            self.host.st.log("agent_restart", agent=self.kind, reason="provider_failure")
            await self._kill(self.proc)
            self.proc = self.client = None

    async def _catch_up(self):
        if not self.client or not self.sid:
            return
        status, messages = await self.client.request("GET", f"/session/{self.sid}/message")
        if status != 200 or not isinstance(messages, list):
            return
        for message in messages:
            info = message.get("info") or {}
            mid = info.get("id")
            self.message_roles[mid] = None if info.get("synthetic") or info.get("ignored") else info.get("role")
            for part in message.get("parts") or []:
                if info.get("role") == "user":
                    self.on_event({"type": "message.part.updated", "properties": {"part": part}})
                else:
                    self._text_part(part)

    async def _kill(self, p):
        if not isinstance(p, _ExternalServer):
            return await super()._kill(p)
        # Connection disposal only, never the existing server's process tree.
        p.returncode = 0
        if self.proc is p:
            self.proc = None

    async def halt(self, clear_queue=True):
        busy = self.status == "working"
        if clear_queue:
            while not self.q.empty():
                item = self.q.get_nowait()
                if getattr(self.host, "queue_dropped", None):
                    self.host.queue_dropped(item)
        await self.interrupt_request(self.proc)
        if self.turn_done:
            self.turn_done.set()
        return busy


def make_shared(host, cfg):
    if cfg.get("high_risk_warnings", False) and cfg["kind"] not in ("claude", "codex", "opencode"):
        return UnavailableSharedAgent(host, cfg)
    if cfg["kind"] == "claude":
        return SharedClaudeAgent(host, cfg)
    if cfg["kind"] == "opencode":
        return SharedOpenCodeAgent(host, cfg)
    if cfg["kind"] == "codex":
        from .shared_codex import SharedCodexAgent
        return SharedCodexAgent(host, cfg)
    return UnavailableSharedAgent(host, cfg)


class UnavailableSharedAgent(Agent):
    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.kind = cfg["kind"]
    async def turn(self, text):
        self.local_fail(pick(getattr(self.host, "lang", "zh"), "共享模式的高危执行前拦截尚未验证，未投递；Codex 接续也尚未完成。", "Shared pre-execution guard and Codex continuation are not ready."))
