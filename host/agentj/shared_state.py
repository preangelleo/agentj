"""The single owner of the agent's three-state model.

States (PRODUCT_SPEC §4.1 status bar):
    working  — 蓝：agent 在干活
    idle     — 绿：空闲，等你说话
    waiting  — 红：等你（两种 kind：permission / question）

Everything here is derived from hook events only. `~/.claude/sessions/<pid>.json`
is a *validation* source, never the primary one — reports/M0-validation.md §5.6(c)
proved the session json sits at `busy` for the whole time a blocking PreToolUse
hook holds the turn, so it cannot see "等审批" at all.

The non-obvious part is sub-agents (ADR-006). A main-session `Stop` is only a
fake idle while a *foreground* sub-agent is still running (§1.4); while only
*background* sub-agents run, the main turn really ended and the phone must go
green with the turn's `last_assistant_message`. Classification, from real
2.1.280 hook logs (bridge/tests/fixtures/):
  * PostToolUse(Agent|Task).tool_response = {isAsync: true, status: "async_launched",
    agentId} → background, known ~0 ms after SubagentStart;
    {status: "completed", agentId} → that agent is finished (second close signal);
  * Stop.background_tasks[] lists every still-running background sub-agent by id →
    authoritative reconciliation: a background agent absent from it is gone even if
    its SubagentStop never reached us (the leak that kept the phone "working").
An open sub-agent we cannot classify is treated as foreground (conservative: no
false green), and a watchdog commits a deferred Stop after SUBAGENT_STALE_S of
total silence so a lost close can never pin the phone at "working" forever.
"""
import collections
import os
import threading
import time

from types import SimpleNamespace
from . import privacy
config = SimpleNamespace(IDLE_DEBOUNCE_S=1.2, SUBAGENT_STALE_S=600.0, COMPACT_STALE_S=600.0)
redact = SimpleNamespace(scrub_message=privacy.redact)
# Product approval UI is owned by Host.ask, not the relay snapshot.
def build_card(event):
    return {"tool_name": event.get("tool_name"), "summary": "", "options": []}

WORKING, IDLE, WAITING = "working", "idle", "waiting"
KIND_PERMISSION, KIND_QUESTION = "permission", "question"

DROP_WARN_S = 60.0       # at most one "dropped foreign events" line per this long
MISMATCH_WARN_S = 60.0   # a persisting session-json mismatch is re-reported this often
TARGET_TTL_S = 2.0       # how long the gate trusts its last read of the target's json


class _Scheduler:
    """Wall-clock timers. Tests swap in a fake with a manual clock (tests/replay.py)."""

    def now(self) -> float:
        return time.time()

    def call_later(self, delay, fn):
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()
        return t


AGENT_TOOLS = ("Agent", "Task")


class AgentState:
    def __init__(self, on_change=None, warn=None, scheduler=None, on_turn=None):
        self._lock = threading.RLock()
        self._on_change = on_change or (lambda snap: None)
        # A committed Stop with a reply = one turn of history (history.py, ADR-033).
        # Called under the lock, before the frame that shows the reply is published,
        # so the snapshot's history meta and last_message always agree.
        self._on_turn = on_turn or (lambda event, reply: None)
        self._warn = warn or (lambda msg: None)
        self._sched = scheduler or _Scheduler()

        self.status = IDLE
        self.kind = None                # only meaningful when status == WAITING
        self.session_id = None
        self.cwd = None
        self.prompt_id = None
        self.last_message = None        # most recent assistant message
        self.card = None                # pending decision card, or None
        self.ask = None                 # AskUserQuestion answerable from the phone (ask.py public view)
        self.approval = None            # tool approval answerable from the phone (approve.py public view)
        self.updated_at = self._sched.now()
        self.seq = 0

        # agent_id -> True (background) / None (not yet classified → foreground)
        self._agents: dict[str, bool | None] = {}
        self._subagent_depth = 0        # SubagentStart without agent_id (old harness)
        self._idle_timer = None
        self._stale_timer = None
        self._deferred_stop: dict | None = None   # a Stop held back by a foreground agent
        self._main_active = False       # main turn running: UserPromptSubmit → committed Stop
        self._mismatch = None           # (key, since, last_warned, checks) — see validate_against
        # Context compaction (G11): PreCompact → working until it ends. None, or
        # {"saw_busy": bool} while a compaction is in flight.
        self._compacting: dict | None = None
        self._compact_timer = None

    # ------------------------------------------------------------------ public

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "seq": self.seq,
                "status": self.status,
                "kind": self.kind,
                "agent": self._agent_name(),
                "session_id": self.session_id,
                "last_message": self.last_message,
                "card": self.card,
                "ask": self.ask,
                "approval": self.approval,
                "subagents": len(self._agents) + self._subagent_depth,
                "background_subagents": sum(1 for v in self._agents.values() if v),
                # read-only view of G11's compaction (the PWA's water effect, 2b)
                "compacting": self._compacting is not None,
                "updated_at": round(self.updated_at, 3),
            }

    def handle(self, event: dict):
        """Feed one hook event. Thread-safe; called from the ingest thread."""
        name = event.get("hook_event_name")
        with self._lock:
            self.session_id = event.get("session_id") or self.session_id
            self.cwd = event.get("cwd") or self.cwd
            if event.get("prompt_id"):
                self.prompt_id = event["prompt_id"]
            if self._deferred_stop is not None:
                self._arm_stale()           # any traffic = not silent yet

            aid = event.get("agent_id")
            is_subagent = bool(aid or event.get("agent_type"))
            # Traffic from a background sub-agent says nothing about the main turn:
            # it must neither flip an idle phone back to "working" nor cancel the
            # idle debounce. PermissionRequest is still honoured (it needs Leo).
            from_bg = is_subagent and name != "SubagentStart" and self._is_background(aid)

            if name == "PreCompact":
                self._on_precompact(event)
            elif name == "PostCompact" or (name == "SessionStart" and event.get("source") == "compact"):
                # 2.1.280 order: PreCompact → SubagentStop(the summariser) →
                # SessionStart(source=compact) → PostCompact, ~20 ms apart. The first
                # of the two ends the compaction; the second is a no-op. Neither is a
                # new session: the turn state from before compaction carries on.
                self._end_compact()
            elif name == "SessionStart":
                self._end_compact(quiet=True)
                self._main_active = False
                if event.get("source") == "clear":
                    # /clear = a new conversation: the phone starts from an empty
                    # page, like the history (ADR-034). resume / startup keep it.
                    self.last_message = None
                self._set(IDLE)
            elif name == "UserPromptSubmit":
                self._end_compact(quiet=True)
                self.card = None
                self._deferred_stop = None
                self._cancel_stale()
                self._cancel_idle()
                self._main_active = True
                self._set(WORKING)
            elif name in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
                          "MessageDisplay", "UserPromptExpansion"):
                # Any tool traffic clears a pending card: the prompt was answered
                # locally (Leo pressed a key) or the turn moved on.
                cleared = False
                if self.card and name in ("PostToolUse", "PostToolUseFailure") and \
                        event.get("tool_name") == self.card.get("tool_name"):
                    self.card = None
                    cleared = True
                if name == "PostToolUse" and not is_subagent and \
                        event.get("tool_name") in AGENT_TOOLS:
                    self._on_agent_tool_result(event)
                if from_bg:
                    if cleared and self.status == WAITING:
                        self._set(WORKING if self._main_active else IDLE)
                    return
                self._cancel_idle()
                self._main_active = True
                if self.status != WAITING or self.card is None:
                    self._set(WORKING)
            elif name == "SubagentStart":
                if aid:
                    self._agents.setdefault(aid, None)
                else:
                    self._subagent_depth += 1
                self._cancel_idle()
                self._main_active = True
                self._set(WORKING)
            elif name == "SubagentStop":
                was_bg = self._close_agent(aid)
                if not was_bg:
                    self._main_active = True    # main turn resumes after a foreground agent
                    self._set(WORKING)
            elif name == "Stop":
                self._on_stop(event, is_subagent)
            elif name == "PermissionRequest":
                self._on_permission_request(event)
            elif name == "Notification":
                # +6 s corroboration of a permission prompt (§5.1). Never the
                # primary signal; only used if we somehow missed the request.
                if event.get("notification_type") == "permission_prompt" and self.status != WAITING:
                    self._warn("Notification/permission_prompt arrived without a "
                               "preceding PermissionRequest — state corrected")
                    self.kind = KIND_PERMISSION
                    self._set(WAITING)
            elif name in ("SessionEnd",):
                self._end_compact(quiet=True)
                self.card = None
                if event.get("reason") == "clear":
                    self.last_message = None        # see SessionStart(clear)
                self._agents.clear()
                self._subagent_depth = 0
                self._deferred_stop = None
                self._main_active = False
                self._cancel_stale()
                self._set(IDLE)

    def validate_against(self, session_status: str | None):
        """Compare with ~/.claude/sessions/<pid>.json. Hook wins; mismatch warns."""
        if not session_status:
            return
        expected = {"busy": WORKING, "idle": IDLE, "waiting": WAITING, "shell": WORKING}
        mapped = expected.get(session_status)
        with self._lock:
            if self._compacting is not None:
                # A compaction that never reports its end (Esc, an error, PostCompact
                # not registered and SessionStart lost) must not pin the phone at
                # working: once the session json has gone busy and come back to
                # idle, the compaction is over. The json is still not the primary
                # source — it can only *end* a compaction the hooks started.
                if session_status == "busy":
                    self._compacting["saw_busy"] = True
                elif session_status == "idle" and self._compacting["saw_busy"]:
                    self._warn("compaction ended without SessionStart(compact)/PostCompact "
                               "(session json back to idle)")
                    self._end_compact()
            # A blocking PreToolUse hook legitimately reads `busy` while we say
            # WAITING — that is §5.6(c), not a bug. Everything else is a warning.
            if mapped is None or mapped == self.status or \
                    (self.status == WAITING and session_status == "busy"):
                self._mismatch = None
                return
            # Called every second: an unthrottled line per tick buried the one
            # useful fact (2 508 identical lines on 2026-09-23/24). Say it when it
            # starts or changes, then remind every MISMATCH_WARN_S while it lasts.
            key = (self.status, session_status)
            now = self._sched.now()
            if self._mismatch and self._mismatch[0] == key:
                _, since, last, n = self._mismatch
                n += 1
                if now - last >= MISMATCH_WARN_S:
                    self._warn(f"state mismatch: hook={self.status} "
                               f"session_json={session_status} persisting "
                               f"{now - since:.0f}s ({n} checks, hook wins)")
                    last = now
                self._mismatch = (key, since, last, n)
                return
            self._mismatch = (key, now, now, 1)
            self._warn(f"state mismatch: hook={self.status} session_json={session_status} "
                       f"(hook wins)")

    def set_ask(self, public: dict | None):
        """The ask broker's phone-facing view (bridge/bridge/ask.py, ADR-023)."""
        with self._lock:
            self.ask = public
            self._bump()

    def question_from_ask(self, event: dict):
        """A blocking ask hook registered. The signal tap's datagram normally built
        the card already; if it did not (tap not configured), build it now."""
        with self._lock:
            if self.card is None or self.card.get("tool_name") != "AskUserQuestion":
                self._on_permission_request(event)

    def set_approval(self, public: dict | None):
        """The approval broker's phone-facing view (bridge/bridge/approve.py, ADR-045)."""
        with self._lock:
            self.approval = public
            self._bump()

    def permission_from_approval(self, event: dict):
        """A blocking approve hook registered. The signal tap's datagram normally
        built the card already; if it did not (tap not configured), build it now."""
        with self._lock:
            if self.card is None or self.card.get("tool_name") != event.get("tool_name"):
                self._on_permission_request(event)

    def turn_interrupted(self, tool_name: str):
        """The terminal answered a permission dialog with "No" / Esc (the blocking
        approve hook was killed before any decision, ADR-045). Claude Code ends
        the turn without a Stop, so nothing else would bring the phone back to
        idle — the session json cannot either (hook wins, validate_against)."""
        with self._lock:
            if self.card and self.card.get("tool_name") == tool_name:
                self.card = None
                if self.status == WAITING:
                    self._cancel_idle()
                    self._deferred_stop = None
                    self._cancel_stale()
                    self._main_active = False
                    self._set(IDLE)

    def clear_card(self, tool_name: str):
        """The question ended without a PostToolUse (the hook's timeout deny):
        drop its card; the turn goes on, so the agent is working again."""
        with self._lock:
            if self.card and self.card.get("tool_name") == tool_name:
                self.card = None
                if self.status == WAITING:
                    self._set(WORKING if self._main_active else IDLE)

    def note_assistant_message(self, text: str | None):
        if not text:
            return
        with self._lock:
            self.last_message = redact.scrub_message(text)
            self._bump()

    # ------------------------------------------------------------- sub-agents

    def _is_background(self, aid) -> bool:
        if not aid:
            return False
        if aid in self._agents:
            return bool(self._agents[aid])
        # Unknown id (its SubagentStart predates a bridge restart, or was lost).
        # While the main turn is over only background agents can still be talking.
        return not self._main_active

    def _foreground_open(self) -> int:
        return sum(1 for v in self._agents.values() if not v) + self._subagent_depth

    def _close_agent(self, aid) -> bool:
        """Returns True if the closed agent was known to be background."""
        if aid and aid in self._agents:
            return bool(self._agents.pop(aid))
        if aid:
            return self._is_background(aid)
        if self._subagent_depth > 0:
            self._subagent_depth -= 1
        return False

    def _on_agent_tool_result(self, event):
        resp = event.get("tool_response")
        if not isinstance(resp, dict):
            return
        aid = resp.get("agentId")
        if not aid:
            return
        if resp.get("isAsync") or resp.get("status") == "async_launched":
            self._agents[aid] = True
        elif resp.get("status") == "completed" and aid in self._agents:
            # The Agent tool returned, so this foreground agent is done even if
            # its SubagentStop was lost.
            self._agents.pop(aid, None)

    def _reconcile_background(self, tasks):
        """Stop.background_tasks is the harness's own list of live background work."""
        live = {t.get("id") for t in tasks
                if isinstance(t, dict) and t.get("type") == "subagent"}
        for aid in list(self._agents):
            if aid in live:
                self._agents[aid] = True
            elif self._agents[aid]:
                del self._agents[aid]
                self._warn(f"reconciled background sub-agent {aid[:8]}: gone from "
                           f"Stop.background_tasks, its SubagentStop never arrived")

    # ------------------------------------------------------------- compaction

    def _on_precompact(self, event):
        """G11: `/compact` (trigger=manual) or auto-compaction mid-turn (trigger=auto).
        Either way the session is busy for its whole duration (~15 s measured), so
        the phone says working. `_main_active` is left alone: it records whether a
        turn was running before, which decides where the compaction ends."""
        self._compacting = {"saw_busy": False, "trigger": event.get("trigger")}
        self._cancel_idle()
        self._cancel_compact_timer()
        self._compact_timer = self._sched.call_later(config.COMPACT_STALE_S, self._on_compact_stale)
        if self.status != WAITING or self.card is None:
            self._set(WORKING)

    def _end_compact(self, quiet=False):
        if self._compacting is None:
            return
        self._compacting = None
        self._cancel_compact_timer()
        if quiet:
            return
        # Manual /compact from an idle prompt → back to idle. Auto-compaction in the
        # middle of a turn → the turn goes on, still working.
        if self.status == WAITING and self.card:
            return
        self._set(WORKING if self._main_active or self._foreground_open() else IDLE)

    def _cancel_compact_timer(self):
        if self._compact_timer is not None:
            self._compact_timer.cancel()
            self._compact_timer = None

    def _on_compact_stale(self):
        with self._lock:
            self._compact_timer = None
            if self._compacting is None:
                return
            self._warn(f"no end of compaction after {config.COMPACT_STALE_S:.0f}s; "
                       f"going back to the pre-compaction state")
            self._end_compact()

    # ----------------------------------------------------------------- private

    def _on_stop(self, event, is_subagent):
        # A subagent's own Stop is never the main turn ending.
        if is_subagent:
            return
        tasks = event.get("background_tasks")
        if isinstance(tasks, list):
            self._reconcile_background(tasks)
        fg = self._foreground_open()
        if fg:
            # §1.4: the fake Stop — a foreground sub-agent is still running. Hold it:
            # the real Stop normally follows; if everything goes silent instead, the
            # stale watchdog commits this one so the phone can never stick at working.
            self._warn(f"ignored fake Stop (foreground subagents={fg}, "
                       f"background={len(self._agents) - (fg - self._subagent_depth)})")
            self._deferred_stop = event
            self._arm_stale()
            return
        self._commit_stop(event)

    def _commit_stop(self, event):
        self._main_active = False
        self._deferred_stop = None
        self._cancel_stale()
        msg = event.get("last_assistant_message")
        if msg:
            self.last_message = redact.scrub_message(msg)
            try:
                self._on_turn(event, self.last_message)
            except Exception as e:          # history is a convenience; the reply is not
                self._warn(f"history: turn not recorded ({type(e).__name__})")
        self._schedule_idle()

    def _arm_stale(self):
        self._cancel_stale()
        self._stale_timer = self._sched.call_later(config.SUBAGENT_STALE_S, self._on_stale)

    def _cancel_stale(self):
        if self._stale_timer is not None:
            self._stale_timer.cancel()
            self._stale_timer = None

    def _on_stale(self):
        with self._lock:
            self._stale_timer = None
            ev = self._deferred_stop
            if ev is None:
                return
            fg = [a[:8] for a, v in self._agents.items() if not v]
            self._warn(f"no hook traffic for {config.SUBAGENT_STALE_S:.0f}s after a held "
                       f"Stop; dropping foreground sub-agents {fg} "
                       f"(+{self._subagent_depth} unnamed) as lost and going idle")
            for a in [a for a, v in self._agents.items() if not v]:
                del self._agents[a]
            self._subagent_depth = 0
            self._commit_stop(ev)

    def _on_permission_request(self, event):
        tool = event.get("tool_name") or "?"
        self.kind = KIND_QUESTION if tool == "AskUserQuestion" else KIND_PERMISSION
        card = build_card(event)
        # The same question can arrive twice (signal datagram + ask registration);
        # keep the first card so received_at — and a card Leo folded — hold still.
        if not (self.card and self.status == WAITING and
                {k: self.card.get(k) for k in ("tool_name", "summary", "options")} ==
                {k: card.get(k) for k in ("tool_name", "summary", "options")}):
            self.card = card
        self._cancel_idle()
        self._set(WAITING)

    def _schedule_idle(self):
        self._cancel_idle()
        self._idle_timer = self._sched.call_later(config.IDLE_DEBOUNCE_S, self._commit_idle)

    def _commit_idle(self):
        with self._lock:
            self._idle_timer = None
            if self._foreground_open():
                return
            if self.status == WAITING and self.card:
                return
            self._set(IDLE)

    def _cancel_idle(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _set(self, status):
        changed = status != self.status
        self.status = status
        if status != WAITING:
            self.kind = None
        self._bump(emit=changed or True)

    def _bump(self, emit=True):
        self.seq += 1
        self.updated_at = self._sched.now()
        if emit:
            snap = self.snapshot()
            threading.Thread(target=self._on_change, args=(snap,), daemon=True).start()

    def _agent_name(self):
        if not self.cwd:
            return "agent"
        return self.cwd.rstrip("/").rsplit("/", 1)[-1] or "agent"

