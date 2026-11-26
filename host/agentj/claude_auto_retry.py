"""A Claude classifier denial can request one native prompt for the same dispatch."""
import asyncio
from dataclasses import dataclass
import os
import re
import time

from . import approval_offlist, approval_review, approvals, danger, provenance
from .list_approval import NativeContext

_DENIAL = re.compile(r"^\[(Destructive or Unwanted Actions|Undermining Oversight)\](?:\s|$)")
_UNSAFE_OPTIONS = re.compile(r"^(?:-[cs]|--(?:dangerously-[\w-]+|permission-mode|sandbox|ask-for-approval|"
                             r"full-auto|unfenced|allow-docker|settings|config|profile))(?:=|$)")
_WINDOW = 90


def supported(session):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str((session or {}).get("version", "")))
    return bool(match and tuple(map(int, match.groups())) >= (2, 1, 89))


def owner(adapter):
    host = adapter.host
    if (host.agent is not adapter or not adapter.phone_turn or not adapter.phone_seen
            or adapter.turn_closed or host.stopped() or host.stopping.is_set()
            or provenance.untrusted_turn(adapter)):
        return False
    origin = approval_review.snapshot(host)
    return (isinstance(origin, dict) and origin.get("channel") == "phone"
            and origin.get("verified") is True and origin.get("suspicious") is False
            and approval_offlist.source(host)["type"] == "owner_remote")


@dataclass(frozen=True)
class RetryContext(NativeContext):
    state: object
    turn: int
    authority: str
    key: str
    until: float

    def live(self, host):
        adapter = self.adapter
        return (adapter.host is host and self.state.live(host) and supported(adapter.session)
                and adapter.session is self.process
                and adapter.session["sessionId"] == self.session
                and os.path.realpath(adapter.cfg["dir"]) == self.cwd
                and adapter.turn_gen == self.turn and time.monotonic() < self.until
                and owner(adapter) and host._approval_live_authority() == self.authority)

    async def admits(self, host, tool, inp):
        if not self.live(host) or tool != "Bash" or approvals.input_digest(inp) != self.key:
            return False
        src = approval_offlist.source(host)
        result = await asyncio.to_thread(approval_offlist.decide, host, tool, inp, self.cwd, src)
        return result[0] == "allow" and self.live(host)


class Retry:
    def __init__(self, adapter):
        self.adapter = adapter
        self.seen = {}
        self.context = None
        self.retry_id = None
        self.used = False
        self.approved = None
        self.generation = adapter.turn_gen

    def valid(self, event):
        cwd = event.get("cwd")
        return (event.get("tool_name") == "Bash" and isinstance(event.get("tool_input"), dict)
                and event.get("permission_mode") == "auto" and not event.get("agent_id")
                and not event.get("parent_tool_use_id") and isinstance(cwd, str)
                and os.path.isabs(cwd) and "\x00" not in cwd
                and os.path.realpath(cwd) == os.path.realpath(self.adapter.cfg["dir"]))

    @staticmethod
    def key(event):
        try:
            return approvals.input_digest(event["tool_input"])
        except (KeyError, ValueError, TypeError, RecursionError):
            return None

    async def before(self, event, gone):
        if not self.valid(event):
            return {}
        key, call = self.key(event), event.get("tool_use_id")
        if key is None or not isinstance(call, str) or not call or len(call) > 128:
            return {}
        target = self.context
        if target is None or key != target.key:
            if len(self.seen) < 64 or key in self.seen:
                self.seen[key] = None if key in self.seen else call
            return {}
        denial = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                  "permissionDecisionReason": "The one-time phone approval was refused, expired or already used."}}
        host = self.adapter.host
        if (self.used or self.retry_id is not None or self.seen.get(key) == call
                or gone is None or gone.done() or not target.live(host)):
            return denial
        self.retry_id = call
        inp = event["tool_input"]
        answer = await host.ask("Bash", inp, gone=gone, ttl=target.until - time.monotonic(), batch=False,
                                grant_info=True, _harness_permission=True, _native_context=target)
        proof = answer.get("grant_info") or {}
        if (answer.get("behavior") != "allow" or proof.get("uv") is not True
                or not all(proof.get(key) for key in ("rid", "device", "sign_pub"))):
            self.used = True
            return denial
        unit, why = await asyncio.to_thread(host._dispatch_check, inp["command"])
        if (self.used or not unit or why != "ok" or gone.done() or not target.live(host)
                or host.st.sign_key(proof["device"]) != proof["sign_pub"]):
            self.used = True
            return denial
        self.approved = (proof["rid"], proof["device"], proof["sign_pub"])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                "permissionDecisionReason": "Owner verified this specific dispatch on the paired remote."}}

    async def denied(self, event):
        if self.context is not None or not self.valid(event) or not owner(self.adapter):
            return {}
        key, call, reason = self.key(event), event.get("tool_use_id"), event.get("reason")
        if (not key or not call or self.seen.get(key) != call
                or not isinstance(reason, str) or not _DENIAL.match(reason)):
            return {}
        inp = event["tool_input"]
        command = inp.get("command")
        if not isinstance(command, str) or inp.get("dangerouslyDisableSandbox"):
            return {}
        # Check parsed harness arguments, including quoted options. The dispatch
        # recognizer independently proves the launcher, shell and path shape.
        commands = danger.lex(command)
        if any(_UNSAFE_OPTIONS.match(word) for c in commands for p in danger.unwrap(c)
               if p.name in ("claude", "codex", "opencode") for word in p.args):
            return {}
        host = self.adapter.host
        session = self.adapter.session
        cwd = os.path.realpath(self.adapter.cfg["dir"])
        host._task_id()
        from .list_approval import StateSubject
        try:
            state = StateSubject.capture(host)
        except (OSError, ValueError, KeyError):
            return {}
        target = RetryContext(self.adapter, session, cwd, session["sessionId"], state, self.generation,
                              host._approval_live_authority(), key, time.monotonic() + _WINDOW)
        if not await target.admits(host, "Bash", inp):
            return {}
        unit, why = await asyncio.to_thread(host._dispatch_check, command)
        if (why != "ok" or not unit or self.context is not None or self.seen.get(key) != call
                or not target.live(host)):
            return {}
        self.context = target
        return {"hookSpecificOutput": {"hookEventName": "PermissionDenied", "retry": True}}

    def permission(self, event, gone):
        target = self.context
        if target is None or self.retry_id is None or event.get("tool_name") != "Bash":
            return None
        key = self.key(event)
        host = self.adapter.host
        proof = self.approved
        # Claude omits the tool-use ID in this hook. Reject altered input
        # during the approved handoff, then return unrelated calls to normal policy.
        if key != target.key:
            return {"behavior": "deny"} if proof and target.live(host) else None
        if event.get("tool_use_id", self.retry_id) != self.retry_id:
            return {"behavior": "deny"}
        self.approved = None
        self.used = True
        if (not proof or not self.valid(event) or not target.live(host)
                or gone is None or gone.done() or host.st.sign_key(proof[1]) != proof[2]):
            return {"behavior": "deny"}
        host.st.log("claude_auto_retry", id=proof[0], device=proof[1], uv=True, input_sha256=target.key)
        return {"behavior": "allow"}


def current(adapter, event):
    if not supported(adapter.session):
        return None
    if (event.get("tool_name") != "Bash"
            or event.get("hook_event_name") not in ("PreToolUse", "PermissionDenied", "PermissionRequest")):
        return None
    entry = getattr(adapter, "_auto_retry", None)
    if event.get("hook_event_name") == "PermissionRequest":
        for candidate in (entry, getattr(adapter, "_retired_auto_retry", None)):
            if (candidate is not None and candidate.context is not None and candidate.retry_id is not None
                    and (candidate is entry or time.monotonic() < candidate.context.until)
                    and candidate.key(event) == candidate.context.key):
                return candidate
    if entry is not None and entry.generation == adapter.turn_gen:
        return entry
    if entry is not None and entry.context is not None and entry.retry_id is not None:
        entry.approved = None
        adapter._retired_auto_retry = entry
    adapter._auto_retry = Retry(adapter)
    return adapter._auto_retry
