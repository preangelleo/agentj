"""Agent bridge (PROTOCOL §8): drives the customer's own, already-logged-in agent through its official headless
interfaces only — Claude Code: `claude -p` with stream-json in/out and `--permission-prompt-tool` (our MCP tool, see
permtool.py); Codex: `codex app-server` (agent_codex.py); OpenCode: `opencode serve` (agent_opencode.py). No private sockets,
no terminal injection.

Slash commands (slash.py, ADR-A70 – A72) ride the same queue as messages (`Cmd`): one at a time, after the turn that is
running; each adapter implements `cmd_<name>`. Claude Code's stream-json control requests are limited to
`CONTROL_SUBTYPES` (none of them changes a permission, a mode or a setting).

Bridge ≤ session: the agent runs as this OS user, in the directory the human chose, with the human's own login, settings
and permission rules. We never pass a bypass / skip-permissions / allowed-tools flag; the phone only answers the questions
the agent itself would have asked, and an approval returns the tool input unchanged.

Danger list (ADR-A47): Claude Code also gets one PreToolUse hook through `--settings` (`python -P -m agentj.danger hook`)
that answers "ask" for the five dangerous categories, so they reach the phone card by card even when the human's own rules
allow them; it never answers "allow" (only ever stricter). `disableAllHooks: false` in the same flag keeps a settings file the
Agent writes later from switching it off; a human who disabled hooks in their own settings gets a notice instead of a start.

L2 / L3: the agent runs inside the fence (fence.py: bubblewrap on Linux, sandbox-exec on macOS) unless the owner chose `--unfenced`; if the fence cannot start, the agent
runs unfenced with the harness's own permissions (F14) and the phone is told once. Claude Code gets its first message only after our permission tool has claimed serve's socket.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import signal
import sys
import time
from dataclasses import dataclass

from . import compactprep, danger, fence, slash, main_identity
from .slash import Result
from .envcompat import getenv
from .text import clean, clean_line, text_units

MAX_TEXT = 4000               # = wire.MAX_TEXT (UTF-16 units per app message)
LINE_LIMIT = 64 * 1024 * 1024  # stream-json lines can carry whole tool results
STATUSES = ("none", "idle", "working", "compacting", "waiting", "down")
PERM_TOOL = "mcp__agentj__approve"
CLAIM_WAIT = 30               # s: the permission tool must claim perm.sock this soon after Claude Code starts
# the only stream-json control requests serve ever sends to Claude Code (test_slash checks): reading state, choosing the
# model, stopping a turn. Never set_permission_mode / apply_flag_settings / update_settings / mcp_* (bridge ≤ session).
CONTROL_SUBTYPES = ("interrupt", "get_context_usage", "get_status", "list_models", "set_model")
CONTROL_WAIT = 20
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")   # `claude --effort` (2.1.285), "for the current session"


@dataclass
class Cmd:
    """A slash command in the Agent's queue (slash.py): runs after the turn in front of it, never in parallel."""
    name: str
    arg: str = ""
    by: str | None = None
    turn: int | None = None          # its history page (PROTOCOL §10.5 `cmd` turn), filled in when it ran
    data: dict | None = None         # model_set (§10.11): {"r", "model", "effort", "default", "device"}


UNCERTAIN = object()        # Agent.deliver: fn's answer "maybe delivered" (e.g. a 5xx after the request was sent, P33-X08)


class Withdrawn(Exception):
    """The phone took the message back (`say_cancel`) before it reached the harness: nothing was written."""


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Split a reply into chunks of ≤ limit UTF-16 units, preferring line breaks. Never drops text."""
    out, cur, n = [], [], 0
    for ch in text:
        u = 2 if ord(ch) > 0xFFFF else 1
        if n + u > limit:
            s = "".join(cur)
            cut = s.rfind("\n")
            if cut > len(s) // 2:
                out.append(s[:cut])
                cur, n = list(s[cut + 1:]), text_units(s[cut + 1:])
            else:
                out.append(s)
                cur, n = [], 0
        cur.append(ch)
        n += u
    if cur:
        out.append("".join(cur))
    return [c for c in out if c.strip()]


def summarize(tool: str, tool_input) -> str:
    """What the phone shows for a permission request (and what the device's signature covers, §8)."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    s = None
    if tool == "Bash" and isinstance(inp.get("command"), str):
        s = inp["command"]
        if isinstance(inp.get("description"), str) and inp["description"].strip():
            s = f"{s}\n— {inp['description'].strip()}"
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read") and isinstance(inp.get("file_path") or inp.get("notebook_path"), str):
        path = inp.get("file_path") or inp.get("notebook_path")
        if tool == "Write" and isinstance(inp.get("content"), str):
            s = f"{path}\n（写入 {len(inp['content'].encode())} 字节）\n{inp['content'][:600]}"
        elif tool == "Edit" and isinstance(inp.get("old_string"), str):
            s = f"{path}\n- {inp['old_string'][:400]}\n+ {str(inp.get('new_string', ''))[:400]}"
        else:
            s = path
    elif tool in ("WebFetch",) and isinstance(inp.get("url"), str):
        s = inp["url"]
    elif tool == "Delete" and isinstance(inp.get("file_path"), str):          # Codex: a patch that deletes a file
        s = "删除 " + inp["file_path"] + (("\n" + inp["detail"]) if isinstance(inp.get("detail"), str) else "")
    elif tool == "CodexPermissions" and isinstance(inp.get("permissions"), str):   # Codex asks for more than its sandbox
        s = "Codex 要求本轮放宽沙箱：" + inp["permissions"] + (f"\n— {inp['reason']}" if isinstance(inp.get("reason"), str) else "")
    if s is None:
        s = json.dumps(inp, ensure_ascii=False, separators=(", ", ": "))
    s = clean(s, 4000)
    return s if text_units(s) <= 2000 else s[:1990] + " …"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _unix(v) -> int | None:
    """A reset time in unix seconds (harnesses report seconds or milliseconds); None when it is not a number."""
    if not _num(v) or v <= 0:
        return None
    return int(v / 1000) if v > 10_000_000_000 else int(v)


def _signal_tree(p, sig: int) -> None:
    """The Agent runs in its own session (start_new_session): signal its whole process group, so what it started in the
    background goes with it. Linux's fence also ends the PID namespace; macOS has none (G-A53)."""
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            p.send_signal(sig)


class Agent:
    """Base: a queue of user messages, one turn at a time, status reported to the host."""
    kind = "?"

    def __init__(self, host, cfg: dict):
        # Main adapters share the host model/effort configuration; workflow CEOs
        # receive their own configuration and must not mutate the main session.
        self.host, self.cfg = host, dict(cfg) if cfg.get("_workflow_ceo") else cfg
        if not cfg.get("_workflow_ceo"):
            self.cfg["dir"] = str(main_identity.working_root(cfg))
        self.q: asyncio.Queue[str] = asyncio.Queue()
        self.status = "idle"
        self.task: asyncio.Task | None = None
        self.halting = False
        self.ended: set = set()       # processes serve ended on purpose: their exit is not news (no notice)
        self.turn_proc = None         # the process the running turn talks to (an older one's exit cannot end it)
        self.cur_send = None          # the phone's `say` this turn delivers (compose.Send), None for anything else
        self.cur_cmd = None           # the command (Cmd) running now — F24 shows its progress on that page
        self.models_cache: list = []  # §10.11: [{"id", "name", "efforts"}] as the harness listed them

    def set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            self.host.agent_status(s)

    def submit(self, text) -> None:
        self.q.put_nowait(text)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    def meter(self, **kw) -> None:
        """§10.10: a number the harness reported exactly (never an estimate) → the phones' meters."""
        fn = getattr(self.host, "meter_update", None)
        if fn:
            fn(**kw)

    def fail_notice(self, text: str) -> None:
        """A notice that means this turn did not finish normally (its page ends `failed`)."""
        fn = getattr(self.host, "turn_failed", None)
        if fn:
            fn()
        self.host.agent_notice(text)

    def local_fail(self, text: str) -> None:
        """A turn that cannot run until the human changes something on the computer (fence / hooks): failed + 「在电脑上处理」."""
        fn = getattr(self.host, "turn_failed", None)
        if fn:
            fn()
        self.local_only(text)

    def local_only(self, text: str) -> None:
        """§10.8: something only the human at the computer can answer — a page with relay's 「在电脑上处理」 card."""
        fn = getattr(self.host, "local_notice", None)
        (fn or self.host.agent_notice)(text)

    async def deliver(self, fn):
        """The "delivered" moment (§10.2): fn = the write to the harness (Claude Code's stdin line, Codex `turn/start`, OpenCode
        `prompt_async`). Run under the say's lock, so a `say_cancel` either wins before it (Withdrawn, nothing written) or
        waits and reads already_delivered. fn returning False = refused by the harness (the say is `failed`)."""
        s = self.cur_send
        if s is None:
            return await fn()
        async with s.lock:
            if getattr(s, 'official_notice_id', None):
                from . import notices
                if not notices.active(self.host.st, s.official_notice_id):
                    self.host.official_pending.pop(s.official_notice_id, None)
                    raise Withdrawn()
            if s.state == "cancelled":
                raise Withdrawn()
            if s.state == "delivered":          # a retry of the same message after the process died
                return await fn()
            s.state = "delivering"
            try:
                r = await fn()
            except BaseException:
                # P33-X08: the write raised part-way — the harness may already have the message. Never `failed` (that
                # would let a withdraw answer `cancelled` and release attachments the Agent may be reading).
                s.state = "uncertain"
                fn3 = getattr(self.host, "say_uncertain", None)
                if fn3:
                    fn3(s)
                raise
            s.state = "uncertain" if r is UNCERTAIN else "failed" if r is False else "delivered"
        if s.state == "uncertain":
            fn3 = getattr(self.host, "say_uncertain", None)
            if fn3:
                fn3(s)
            return r
        fn2 = getattr(self.host, "say_delivered", None)
        if fn2 and s.state == "delivered":
            fn2(s)
        return r

    async def run(self) -> None:
        while True:
            text = await self.q.get()
            if getattr(text, "sid", None) is not None:      # a phone's say (compose.Send): wait for its transcripts
                send = text
                if not await send.wait_ready() or send.state == "cancelled":     # withdrawn while it waited
                    if self.q.empty() and self.status == "working":
                        self.set_status("down" if self.is_down() else "idle")
                    continue
                async with getattr(self.host, "turn_lock", None) or contextlib.nullcontext():
                    if send.state == "cancelled":
                        if self.q.empty() and self.status == "working":
                            self.set_status("down" if self.is_down() else "idle")
                        continue
                    self.halting = False
                    self.cur_send = send
                    self.set_status("working")
                    if hasattr(self.host, "agent_turn_start"):
                        self.host.agent_turn_start(send.text, send)
                    try:
                        await self._identity_refresh()
                        if not (send.text or "").strip():   # F27: never an empty prompt (compose.render always names the files)
                            send.state = "failed"
                            self.fail_notice("这条消息是空的（没有文字，附件也没整理出来），没有发给 Agent。")
                        else:
                            await self.turn(compactprep.decorate(self, send))   # F24: handover note / context reminder
                    except asyncio.CancelledError:
                        raise
                    except Withdrawn:
                        pass
                    except Exception as e:  # noqa: BLE001 — an adapter bug costs one turn, never serve
                        self.fail_notice(f"Agent 出错（{type(e).__name__}），这条没有完成。")
                    finally:
                        self.cur_send = None
                    self.host.agent_turn_end()
                    try:                                    # P59: context nearly full → the handover before the harness
                        await compactprep.proactive(self)   # compacts by itself (Codex; ADR-A165)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — never costs the queue
                        self.host.st.log("compact_auto_prep", agent=self.kind, result="error")
                    if self.q.empty():
                        self.set_status("down" if self.is_down() else "idle")
                continue
            if isinstance(text, Cmd):
                async with getattr(self.host, "turn_lock", None) or contextlib.nullcontext():
                    self.halting = False
                    if text.name == "model_set":            # the phone's pill (§10.11): no card, a model_res + meter
                        try:
                            why = await self.model_set(text.data or {})
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001
                            why = "unsupported"
                        self.host.model_set_done(text, why)
                        if self.q.empty():
                            self.set_status("down" if self.is_down() else "idle")
                        continue
                    self.cur_cmd = text              # F24: compactprep shows its progress on this command's page
                    try:
                        res = await self.command(text.name, text.arg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 — a command bug costs one card, never serve
                        res = Result(f"命令出错（{type(e).__name__}）。", "error")
                    finally:
                        self.cur_cmd = None
                    self.host.cmd_done(text, res)
                    if self.q.empty():
                        self.set_status("down" if self.is_down() else "idle")
                continue
            # one user of the Agent at a time: a scheduled task run holds the same lock (tasks.py, ADR-A53)
            async with getattr(self.host, "turn_lock", None) or contextlib.nullcontext():
                self.halting = False
                self.set_status("working")
                if hasattr(self.host, "agent_turn_start"):
                    self.host.agent_turn_start(text)
                try:
                    await self._identity_refresh()
                    await self.turn(text)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — an adapter bug costs one turn, never serve
                    self.fail_notice(f"Agent 出错（{type(e).__name__}），这条没有完成。")
                self.host.agent_turn_end()
                if self.q.empty():
                    self.set_status("down" if self.is_down() else "idle")

    async def halt(self, clear_queue: bool = True) -> bool:
        """Stop everything (ADR-A51) — or make room for a scheduled task run (clear_queue=False, called while holding
        turn_lock, so no turn is running): drop the messages still queued (they never ran and never will), ask the harness
        to interrupt the turn (`interrupt_request`), then end the Agent's whole process tree. The conversation itself is
        kept (Claude Code --resume / Codex thread id). Returns True when a turn was running."""
        if clear_queue:
            while not self.q.empty():
                item = self.q.get_nowait()
                fn = getattr(self.host, "queue_dropped", None)
                if fn:
                    fn(item)
        busy = self.status == "working"
        p = getattr(self, "proc", None)
        if p is None or p.returncode is not None:
            return busy
        self.halting = True                  # the adapter says nothing about the process it was told to end
        p._agentj_requested_stop = True      # survives watcher cleanup while startup HTTP calls unwind
        self.ended.add(p)
        with contextlib.suppress(Exception):
            await self.interrupt_request(p)
        td = getattr(self, "turn_done", None)
        if td is not None:
            td.set()
        if getattr(self, "proc", None) is p:
            self.proc = None
        _signal_tree(p, signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(p.wait(), 5)
        _signal_tree(p, signal.SIGKILL)
        return busy

    async def interrupt_request(self, p) -> None:
        """The harness's own "stop this turn" (none by default; the process tree is ended right after anyway)."""

    def is_down(self) -> bool:
        return False

    def perm_lost(self) -> None:
        """The permission tool's connection to serve dropped (ClaudeAgent restarts; others have none)."""

    def launch_argv(self, argv: list[str]) -> list[str] | None:
        """argv inside the fence; as is when the owner chose --unfenced, or (F14) when the fence cannot start — degraded to
        the harness's own permissions with one notice per serve. None only when the identity check fails."""
        if not self.cfg.get("_workflow_ceo"):
            try:
                main_identity.verify_core()
                main_identity.validate_working_root(self.cfg, self.host.st)
            except (main_identity.IdentityError, OSError):
                self.host.st.log("agent_identity_fail", agent=self.kind, reason="core_or_root_invalid")
                self.local_fail("主 Agent 核心或工作根目录校验失败，未启动。Core or working root validation failed; Agent was not started. Run agentj doctor.")
                return None
        self.isolation_effective = False
        if not self.cfg.get("fence", True):
            return argv
        why = fence.problem(self.host.st, self.cfg["dir"])
        if why:
            # F14: no fence → run with the harness's own permissions (what a shared session has), told once per serve
            self.host.st.log("agent_fence_fail", agent=self.kind, reason=why, status="degraded")
            if not getattr(self.host, "fence_degraded_told", False):
                self.host.fence_degraded_told = True
                self.host.agent_notice(f"沙箱不可用，按普通模式运行（{fence.REASONS.get(why, why)}）。"
                                       "Sandbox unavailable: running in normal mode with the harness's own permissions.")
            return argv
        wrapped = fence.wrap(self.host.st, argv, self.cfg["dir"], allow_docker=self.cfg.get("docker", False))
        self.isolation_effective = True
        return wrapped

    async def turn(self, text: str) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task

    # ------------------------------------------------ slash commands (slash.py)
    def passthrough(self, name: str) -> bool:
        """May `/name` go to the harness as an ordinary message? (Claude Code: one of its skills.)"""
        return False

    async def command(self, name: str, arg: str) -> Result:
        fn = getattr(self, "cmd_" + name, None) if name in slash.WHITELIST + slash.INTERNAL else None
        if fn is None:
            return Result(slash.REFUSE, "refused")
        if name == "compact" and compactprep.applies(self):    # F24: the handover first, then the compaction as before
            return await compactprep.compact(self, fn, arg)
        if name == "clear" and compactprep.applies(self):
            key = compactprep.session_key(self)
            res = await fn(arg)
            if res.undo:
                compactprep.cleared(self, key)
            return res
        return await fn(arg)

    async def drop_conversation(self) -> None:
        """Forget the loaded conversation (the next turn opens the one agent.json names)."""

    # ------------------------------------------------ A1 (P44): the injected main identity changed (the owner's language)
    identity_stale = False

    def identity_changed(self) -> None:
        """serve: the text main_identity.prompt(self.cfg) yields changed. Applied before the next turn (hot, never mid-turn)."""
        if not self.cfg.get("_workflow_ceo"):
            self.identity_stale = True

    async def _identity_refresh(self) -> None:
        if self.restart_pending:              # P59 (A167): an owned-harness restart asked for while a turn ran (or idle)
            self.restart_pending = False
            p = getattr(self, "proc", None)
            if p is not None and p.returncode is None and self.owns_harness():
                self.host.st.log("agent_restart", agent=self.kind, reason=self.restart_why)
                await self._end_proc()        # the next spawn resumes the same conversation with the new environment
                self.halting = False
        if self.identity_stale:
            self.identity_stale = False
            await self.reload_identity()
            self.halting = False

    # ------------------------------------------------ P59 (A167): restart the harness Agent J owns, conversation kept
    restart_pending = False
    restart_why = ""

    def owns_harness(self) -> bool:
        """Is the harness process Agent J's own child (independent mode)? A shared session is the owner's process."""
        return self.cfg.get("session_mode") != "shared" and hasattr(self, "proc")

    def request_restart(self, why: str) -> str:
        """Before the next turn (never mid-turn: the main Agent may be the one asking). → "next_turn" | "next_start"."""
        p = getattr(self, "proc", None)
        if p is None or p.returncode is not None:
            return "next_start"
        self.restart_pending, self.restart_why = True, why[:32]
        return "next_turn"

    async def reload_identity(self) -> None:
        """Default: nothing (OpenCode sends `system` with every prompt; a shared session is the owner's own harness — we
        inject nothing there and never restart it). ClaudeAgent / CodexAgent override this."""

    async def _end_proc(self) -> None:
        p = getattr(self, "proc", None)
        if p is not None and p.returncode is None:
            self.halting = True
            self.ended.add(p)
            if getattr(self, "proc", None) is p:
                self.proc = None
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    async def cmd_clear(self, arg: str) -> Result:
        st = self.host.st
        old = st.agent_session(self.kind)
        if not old:
            return Result("现在就是一段新对话，不用清空。", "info", sep=True)
        await self.drop_conversation()
        st.set_agent_session(self.kind + ".prev", old)
        st.set_agent_session(self.kind, None)
        return Result("已清空：Agent 不再记得上面的对话。旧对话仍保存在这台电脑上，可以撤销。", sep=True, undo=True)

    async def cmd_undo_clear(self, arg: str) -> Result:
        st = self.host.st
        prev = st.agent_session(self.kind + ".prev")
        if not prev:
            return Result("没有可以撤销的清空。", "error")
        await self.drop_conversation()
        st.set_agent_session(self.kind, prev)
        st.set_agent_session(self.kind + ".prev", None)
        return Result("已撤销清空：接着之前的对话（清空之后说的话不在这段对话里）。", sep=True)

    async def cmd_help(self, arg: str) -> Result:
        return Result(slash.HELP)

    # ------------------------------------------------ model and effort pill (PROTOCOL §10.11)
    EFFORTS: tuple | None = None      # the harness's effort levels when its model list does not say (None = no effort)

    def cur_model(self) -> str | None:
        return self.cfg.get("model")

    def cur_effort(self) -> str | None:
        return self.cfg.get("effort")

    async def list_models(self) -> list[dict]:
        """[{"id", "name", "efforts": [...] | None}] — what the harness offers (cached; [] when it gave no list)."""
        if not self.models_cache:
            with contextlib.suppress(Exception):
                await self.refresh_models()
        return self.models_cache

    def efforts_for(self, model: str | None, models: list[dict]):
        for m in models:
            if m["id"] == model:
                return m.get("efforts")
        return list(self.EFFORTS) if self.EFFORTS else None

    async def model_set(self, d: dict) -> str | None:
        """None = applied; else why not: unknown_model | unknown_effort | unsupported."""
        if d.get("default") is True:
            await self.apply_model(None, None, default=True)
            return None
        model, effort = d.get("model"), d.get("effort")
        models = await self.list_models()
        if model is not None and (not isinstance(model, str) or not slash.MODEL_RE.match(model)
                                  or (models and model not in {m["id"] for m in models})):
            return "unknown_model"
        if effort is not None:
            eff = self.efforts_for(model or self.cur_model(), models)
            if not eff:
                return "unsupported"
            if effort not in eff:
                return "unknown_effort"
        if model is None and effort is None:
            return None
        return await self.apply_model(model, effort)

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        """Store the choice (config.json agent.model / agent.effort) so the next turn uses it; adapters add what the harness
        needs on top."""
        if default:
            self.host.set_model(None)
            self.host.set_effort(None)
        if model is not None:
            self.host.set_model(model)
        if effort is not None:
            self.host.set_effort(effort)
        self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()), effort=self.cur_effort())
        return None

    def model_name(self, model: str | None) -> str | None:
        for m in self.models_cache:
            if m["id"] == model:
                return m["name"]
        return None

    async def refresh_models(self) -> None:
        """Ask the harness for its list once it runs (adapters); the host sends `models` to the phones."""


def source_root() -> str | None:
    """The directory holding `agentj/` when it is a source checkout; None when the package is installed in a venv's
    site-packages (`uv tool` / pipx / pip): then the venv's interpreter finds it alone and nothing extra goes into the
    agent's environment (L3)."""
    import site
    root = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    installed = {os.path.realpath(p) for p in site.getsitepackages() + [site.getusersitepackages()]}
    return None if root in installed else root


def hook_settings(extra: list | None = None, research: bool = False) -> dict:
    """The only settings serve adds to Claude Code (`--settings`, flag level — above user / project / local, below managed
    policy): one PreToolUse hook for every tool, and hooks kept on. No permission rule, no mode (Invariant 11).
    research (a scheduled `mode: research` task, ADR-A53): the same hook also denies every call that is not read-only."""
    cmd = (f"{shlex.quote(sys.executable)} -P -m agentj.danger hook {danger.encode_extra(extra or [])}"
           + (" research" if research else "")
           + " || exit 2")          # the hook cannot start → a blocking error: the call does not run (fail closed)
    return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]},
            "disableAllHooks": False}


_MANAGED = ("/etc/claude-code/managed-settings.json", "/Library/Application Support/ClaudeCode/managed-settings.json")


def hooks_blocked(workdir: str) -> str | None:
    """Why the danger hook would not run (or would re-enable hooks the human switched off), else None. Read once per start:
    managed policy `allowManagedHooksOnly` / `disableAllHooks`; the human's or the folder's `disableAllHooks: true`."""
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    files = [(p, "managed") for p in _MANAGED] + [(os.path.join(cfg_dir, "settings.json"), "user")] + [
        (os.path.join(workdir, ".claude", n), "project") for n in ("settings.json", "settings.local.json")]
    for path, level in files:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if level == "managed" and d.get("allowManagedHooksOnly") is True:
            return f"管理员策略（{path}）只允许受管 hooks"
        if d.get("disableAllHooks") is True:
            return f"{'管理员策略' if level == 'managed' else '你的 Claude Code 设置'}（{path.replace(os.path.expanduser('~'), '~')}）关掉了所有 hooks（disableAllHooks）"
    return None


def _bin(env: str, name: str) -> str | None:
    from .binaries import resolve
    return resolve(name)["path"]


class ClaudeAgent(Agent):
    """One long-lived `claude -p --input-format stream-json` process; restarted with --resume <session id> if it exits."""
    kind = "claude"

    async def reload_identity(self) -> None:
        """A1: --append-system-prompt is read at start: end the idle process; the next turn starts Claude Code again with
        --resume (same conversation) and the new identity text."""
        await self._end_proc()

    def __init__(self, host, cfg: dict):
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.turn_done: asyncio.Event | None = None
        self.got_init = False
        self.err_tail = ""
        self.failed_start = False
        self.init: dict = {}                       # the last system/init (skills, model, version)
        self.rate: dict | None = None              # the last rate_limit_event.rate_limit_info (/usage)
        self.ctl: dict[str, asyncio.Future] = {}   # control request id → its control_response
        self.capture: list | None = None           # a local command (/compact, /cost): its text is the card, not a reply
        self.last_result: dict = {}
        self.boundary: dict | None = None          # compact_metadata of the last compact_boundary
        self.replay = False                        # after a compact_boundary: Claude Code re-sends a kept synthetic message

    def is_down(self) -> bool:
        return self.failed_start

    def argv(self, resume: str | None, research: bool = False) -> list[str]:
        # the running venv's own interpreter + the installed module (L3); -P: the agent's folder (Claude Code's cwd) is
        # never put on sys.path, so a `agentj/` or `json.py` planted there cannot stand in for the permission tool
        mcp = {"mcpServers": {"agentj": {"type": "stdio", "command": sys.executable,
                                              "args": ["-P", "-m", "agentj.permtool"]}}}
        a = [_bin("AGENTJ_CLAUDE_BIN", "claude") or "claude", "-p", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose", "--permission-prompt-tool", PERM_TOOL,
             "--disallowedTools", PERM_TOOL, "--mcp-config", json.dumps(mcp),
             "--settings", json.dumps(hook_settings(self.cfg.get("danger_extra"), research) if research or self.cfg.get("high_risk_warnings", False) else {}, separators=(",", ":"))]
        if not self.cfg.get("_workflow_ceo"):
            a += ["--append-system-prompt", main_identity.prompt(self.cfg)]
        if self.cfg.get("model"):
            a += ["--model", self.cfg["model"]]
        if self.cfg.get("effort") in CLAUDE_EFFORTS:      # §10.11: "for the current session" — no settings write
            a += ["--effort", self.cfg["effort"]]
        if resume:
            a += ["--resume", resume]
        return a

    async def _spawn(self) -> bool:
        exe = _bin("AGENTJ_CLAUDE_BIN", "claude")
        if not exe:
            self.failed_start = True
            self.fail_notice("这台电脑上没找到 Claude Code（claude 命令）。装好并登录后再试。")
            return False
        why = hooks_blocked(self.cfg["dir"]) if self.cfg.get("high_risk_warnings", False) or self.cfg.get("_research") else None
        if why:
            self.failed_start = True
            self.host.st.log("agent_hooks_off", agent=self.kind)
            self.local_fail(f"{why}。危险动作（花钱、删除、对外发送、改凭据、改价）必须经手机逐条批准，这要靠一个 hook，"
                                   "所以 Agent 没有启动。去掉 disableAllHooks 这一项后再发一条消息。")
            return False
        argv = self.launch_argv(self.argv(self.host.st.agent_session(self.kind)))
        if argv is None:
            self.failed_start = True
            return False
        from .proxy import environment
        env = environment(os.environ, self.host.preferences)
        env.update(self.host.new_perm_env())
        src = source_root()
        if src:   # a source checkout (launcher + PYTHONPATH): the tool must import this same package
            env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["MCP_TOOL_TIMEOUT"] = str(int((self.host.ask_ttl + 60) * 1000))   # never cut a pending approval short
        sid = self.host.st.agent_session(self.kind)
        self.proc = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=env,
                                                         stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
                                                         start_new_session=True)
        self.got_init, self.failed_start, self.err_tail = False, False, ""
        self.reader = asyncio.create_task(self._read(self.proc, resumed=bool(sid)))
        asyncio.create_task(self._drain_err(self.proc))
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        # no message before the permission tool holds serve's socket: by then the one-time token is spent (L2)
        proc = self.proc
        try:
            await asyncio.wait_for(self.host.perm_claimed.wait(), CLAIM_WAIT)
        except asyncio.TimeoutError:
            self.host.st.log("agent_no_claim", agent=self.kind)
            self.fail_notice("Claude Code 的批准通道没有接上：为了安全，这条消息没有交给它。")
            await self._kill(proc)
            return False
        if proc.returncode is not None or self.proc is not proc:
            return False
        if not self.cfg.get("_workflow_ceo"):
            main_identity.audit(self.cfg, self.kind, self.host.st, self.host.st.agent_session(self.kind))
        return True

    async def control(self, subtype: str, **kw) -> dict:
        """One stream-json control request (what the Agent SDK sends) → its response. Only CONTROL_SUBTYPES."""
        if subtype not in CONTROL_SUBTYPES:
            raise ValueError("control subtype not allowed: " + subtype)
        p = self.proc
        if p is None or p.stdin is None or p.returncode is not None:
            raise ConnectionError("Claude Code is not running")
        rid = "aj-" + os.urandom(6).hex()
        f = asyncio.get_running_loop().create_future()
        self.ctl[rid] = f
        try:
            p.stdin.write((json.dumps({"type": "control_request", "request_id": rid,
                                       "request": {"subtype": subtype, **kw}}) + "\n").encode())
            await p.stdin.drain()
            r = await asyncio.wait_for(f, CONTROL_WAIT)
        finally:
            self.ctl.pop(rid, None)
        if r.get("subtype") != "success":
            raise RuntimeError(clean(str(r.get("error") or r.get("subtype") or "error"), 200))
        return r.get("response") if isinstance(r.get("response"), dict) else {}

    async def interrupt_request(self, p) -> None:
        """Claude Code's stream-json control request `interrupt` (what the Agent SDK sends), then ≤ 2 s for the turn's
        result. Measured (2.1.285): the turn ends at once but a backgrounded Bash keeps running — so halt() always ends the
        process tree after this."""
        td = self.turn_done
        if td is None or td.is_set() or p.stdin is None:
            return
        req = {"type": "control_request", "request_id": "aj-stop-" + os.urandom(4).hex(), "request": {"subtype": "interrupt"}}
        p.stdin.write((json.dumps(req) + "\n").encode())
        await p.stdin.drain()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(td.wait(), 2)

    def perm_lost(self) -> None:
        p = self.proc
        if p and p.returncode is None:
            self.host.agent_notice("Claude Code 的批准通道断了：为了安全，已重启 Claude Code。")
            asyncio.create_task(self._kill(p))

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.proc = None
        self.ended.add(p)
        if p.returncode is None:
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    async def _drain_err(self, proc) -> None:
        with contextlib.suppress(Exception):
            while line := await proc.stderr.readline():
                self.err_tail = (self.err_tail + line.decode("utf-8", "replace"))[-2000:]

    async def _read(self, proc, resumed: bool) -> None:
        try:
            while line := await proc.stdout.readline():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    self.on_event(ev)
        except Exception:  # noqa: BLE001
            pass
        code = await proc.wait()
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if self.proc is proc:
            self.proc = None
        for f in list(self.ctl.values()):
            if not f.done():
                f.set_exception(ConnectionError("Claude Code exited"))
        quiet = proc in self.ended
        self.ended.discard(proc)
        if resumed and not self.got_init and not quiet:   # a stale session id: start a fresh conversation next time
            self.host.st.set_agent_session(self.kind, None)
        if self.turn_proc is proc and self.turn_done and not self.turn_done.is_set():
            last = clean(self.err_tail.strip().splitlines()[-1], 200) if self.err_tail.strip() else ""
            if not quiet and not self.halting:
                self.fail_notice(f"Claude Code 退出了（{code}）" + (f"：{last}" if last else ""))
            self.turn_done.set()

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            first = not self.init
            self.got_init = True
            self.init = ev
            sid = ev.get("session_id")
            if isinstance(sid, str):
                self.host.st.set_agent_session(self.kind, sid)
            if first:
                m = self.cur_model()
                self.meter(model=m, model_name=self.model_name(m), effort=self.cur_effort())
                if not self.models_cache and getattr(self, "_models_task", None) is None:
                    self._models_task = asyncio.get_running_loop().create_task(self.refresh_models())
        elif t == "system" and ev.get("subtype") == "status":
            if ev.get("status") == "compacting":
                self.set_status("compacting")
            elif self.status == "compacting":
                self.set_status("working")
        elif t == "system" and ev.get("subtype") == "compact_boundary":
            meta = ev.get("compact_metadata") if isinstance(ev.get("compact_metadata"), dict) else {}
            self.boundary, self.replay = meta, True
            if self.capture is None:               # Claude Code compacted on its own in the middle of a turn
                self.host.agent_notice(f"Claude Code 自动压缩了上下文：{slash.tokens(meta.get('pre_tokens'))} → "
                                       f"{slash.tokens(meta.get('post_tokens'))} tokens")
                with contextlib.suppress(RuntimeError):      # F24: a new epoch (handover note / reminder) all the same
                    asyncio.get_running_loop().create_task(compactprep.compacted(self, auto=True))
        elif t == "control_response":
            r = ev.get("response") if isinstance(ev.get("response"), dict) else {}
            f = self.ctl.get(r.get("request_id"))
            if f and not f.done():
                f.set_result(r)
        elif t == "rate_limit_event":
            if isinstance(ev.get("rate_limit_info"), dict):
                self.rate = ev["rate_limit_info"]
                wins = self.rate.get("unifiedWindows") if isinstance(self.rate.get("unifiedWindows"), dict) else {}
                upd = {}
                for k, key in (("five_hour", "h5"), ("seven_day", "week")):
                    w = wins.get(k)
                    if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)) \
                            and not isinstance(w.get("utilization"), bool):
                        upd[key] = {"pct": round(100 * float(w["utilization"]), 1), "reset": _unix(w.get("resetsAt"))}
                if upd:
                    self.meter(**upd)
        elif t == "assistant":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            synthetic = msg.get("model") == "<synthetic>"
            if synthetic and self.replay:          # the kept segment re-sent after a compact_boundary: not a new reply
                return
            if not synthetic:
                self.replay = False
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    if self.capture is not None:
                        self.capture.append(block["text"])
                    else:
                        self.host.agent_text(block["text"])
        elif t == "result":
            self.replay = False
            self.last_result = ev
            if (ev.get("is_error") or ev.get("subtype") not in ("success", None)) and not self.halting \
                    and self.capture is None:
                r = ev.get("result")
                self.fail_notice("Agent 这一轮没有正常完成" + (f"：{clean(r, 300)}" if isinstance(r, str) and r else "。"))
            if self.turn_done:
                self.turn_done.set()

    async def turn(self, text: str) -> None:
        for attempt in range(2):
            if self.proc is None and not await self._spawn():
                return
            self.turn_done, self.turn_proc = asyncio.Event(), self.proc
            line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"
            proc = self.proc

            async def write():
                proc.stdin.write(line.encode())
                await proc.stdin.drain()
            try:
                await self.deliver(write)          # §10.2: the stdin line written = delivered
            except (ConnectionError, AttributeError):
                self.proc = None
                continue
            await self.turn_done.wait()
            if self.got_init or attempt:   # a resumed process that died before init is retried once, fresh
                if self.capture is None:
                    await self.context_meter()
                return

    async def context_meter(self) -> None:
        """§10.10: `get_context_usage` after each turn end (and compaction) — the exact numbers Claude Code reports."""
        if self.proc is None or self.proc.returncode is not None:
            self.meter(ctx=None)
            return
        selected = self.cur_model()
        try:
            u = await asyncio.wait_for(self.control("get_context_usage"), 5)
        except (RuntimeError, ConnectionError, OSError, ValueError, asyncio.TimeoutError):
            if self.cur_model() == selected:
                self.meter(ctx=None)
            return
        u = u if isinstance(u, dict) else {}
        used, mx = u.get("totalTokens"), u.get("maxTokens")
        from .model_limits import positive
        # Claude exposes no trustworthy OpenAI provider endpoint here; retain
        # measured tokens without substituting an alias or stale percentage.
        ctx = {"used": used, "max": positive(mx)} if type(used) is int and used >= 0 else None
        if self.cur_model() == selected:
            self.meter(ctx=ctx)

    def cur_model(self) -> str | None:
        m = self.cfg.get("model") or self.init.get("model")
        return m if isinstance(m, str) and m else None

    EFFORTS = CLAUDE_EFFORTS

    async def refresh_models(self) -> None:
        try:
            r = await self.control("list_models")
        except (RuntimeError, ConnectionError, OSError, ValueError, asyncio.TimeoutError):
            self._models_task = None
            return
        out = []
        for m in r.get("models") or []:
            if isinstance(m, dict) and isinstance(m.get("value"), str) and slash.MODEL_RE.match(m["value"]):
                out.append({"id": clean_line(m["value"], 100), "name": clean_line(str(m.get("displayName") or m["value"]), 60),
                            "efforts": list(CLAUDE_EFFORTS),
                            "resolved": clean_line(str(m.get("resolvedModel") or ""), 100)})
        self.models_cache = out[:40]
        fn = getattr(self.host, "models_changed", None)
        if fn:
            fn()
        cur = self.cur_model()
        self.meter(model=cur, model_name=self.model_name(cur))

    def model_name(self, model: str | None) -> str | None:
        for m in self.models_cache:
            if model and (m["id"] == model or m.get("resolved") == model):
                return m["name"]
        return None

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        """Model: the `set_model` control request (already allowed, CONTROL_SUBTYPES). Effort: no allowed control request
        exists (apply_flag_settings stays forbidden, Invariant 26), so the idle process is restarted with
        `--resume <id> --effort <level>`. Default (long press): both cleared, the process restarted without the flags."""
        if model is not None and not default and self.proc is not None and self.proc.returncode is None:
            try:
                await self.control("set_model", model=model)
            except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError):
                return "unsupported"
        if default or effort is not None:
            await self._end_proc()                 # the next message starts Claude Code with the new flags
            self.init, self.got_init = {}, False
        return await super().apply_model(model, effort, default)

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        self.proc = None
        if p and p.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                p.stdin.close()
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    # ------------------------------------------------ slash commands (slash.py)
    def passthrough(self, name: str) -> bool:
        skills = self.init.get("skills") if isinstance(self.init.get("skills"), list) else []
        return name in skills and name not in slash.WHITELIST

    async def _ensure(self) -> bool:
        return self.proc is not None or await self._spawn()

    async def local(self, text: str) -> dict:
        """Send a command Claude Code executes itself (`/compact`, `/cost`); its output is collected for the card."""
        self.capture, self.boundary, self.last_result = [], None, {}
        try:
            await self.turn(text)
            return {"texts": self.capture, "result": self.last_result, "boundary": self.boundary}
        finally:
            self.capture = None

    async def drop_conversation(self) -> None:
        await self._end_proc()                     # the next message starts Claude Code with the session agent.json names
        self.init, self.got_init = {}, False

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        t0 = time.monotonic()
        r = await self.local("/compact")
        b = r["boundary"]
        await self.context_meter()
        if b:
            return Result(f"已压缩：{slash.tokens(b.get('pre_tokens'))} → {slash.tokens(b.get('post_tokens'))} tokens"
                          f"{slash.secs(b.get('duration_ms') if isinstance(b.get('duration_ms'), int) else (time.monotonic() - t0) * 1000)}")
        res = r["result"].get("result") if isinstance(r["result"].get("result"), str) else ""
        return Result("没有压缩" + (f"：{clean(res, 300)}" if res.strip() else "（Claude Code 没有报告结果）。"), "error")

    async def cmd_cost(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("本会话花费：$0（还没有对话）", "info")
        r = await self.local("/cost")
        text = r["result"].get("result") if isinstance(r["result"].get("result"), str) else "\n".join(r["texts"])
        if not text.strip():
            return Result("本会话花费：" + slash.NONE, "info")
        return Result("本会话花费（Claude Code 报告）：\n" + clean(text.strip(), 1500))

    async def cmd_context(self, arg: str) -> Result:
        if not await self._ensure():
            return Result("Claude Code 没起来：" + slash.NONE, "error")
        try:
            u = await self.control("get_context_usage")
        except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"上下文：{slash.NONE}（{clean(str(e), 120) or type(e).__name__}）", "error")
        total, mx = u.get("totalTokens"), u.get("maxTokens")
        cats = [c for c in u.get("categories") or [] if isinstance(c, dict) and c.get("kind") == "used"]
        detail = " · ".join(f"{clean_line(str(c.get('name')), 40)} {slash.tokens(c.get('tokens'))}" for c in cats[:6])
        model = clean_line(str(self.init.get("model") or self.cfg.get("model") or ""), 60)
        return Result(f"上下文 {slash.tokens(total)} / {slash.tokens(mx)}{slash.pct(total, mx)}" + (f" · 模型 {model}" if model else "")
                      + (f"\n{detail}" if detail else ""))

    async def cmd_usage(self, arg: str) -> Result:
        r = self.rate if isinstance(self.rate, dict) else {}
        wins = r.get("unifiedWindows") if isinstance(r.get("unifiedWindows"), dict) else {}
        lines = []
        for k, name in (("five_hour", "5 小时窗口"), ("seven_day", "7 天窗口"), ("seven_day_opus", "7 天窗口（Opus）")):
            w = wins.get(k)
            if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
                rs = slash.when(w.get("resetsAt"))
                lines.append(f"{name}已用 {round(100 * w['utilization'])}%" + (f"（{rs} 重置）" if rs else ""))
        if not lines:
            return Result("套餐用量：" + slash.NONE + "（Claude Code 还没报告过：发一条消息之后再看）", "info")
        return Result("套餐用量（Claude Code 报告）：" + "；".join(lines))

    async def cmd_status(self, arg: str) -> Result:
        rows = []
        if await self._ensure():
            with contextlib.suppress(RuntimeError, ConnectionError, OSError, asyncio.TimeoutError):
                st = await self.control("get_status")
                for sec in st.get("sections") or []:
                    for row in (sec.get("rows") if isinstance(sec, dict) else None) or []:
                        if isinstance(row, dict) and row.get("label") in ("Version", "Session ID", "cwd", "Model"):
                            rows.append(f"{clean_line(str(row['label']), 20)}：{clean_line(str(row.get('value')), 200)}")
        if not rows:   # the control request failed: what the host knows itself
            rows = [f"Version：{clean_line(str(self.init.get('claude_code_version') or '?'), 40)}",
                    f"Session ID：{self.host.st.agent_session(self.kind) or '（新对话）'}", f"cwd：{self.cfg['dir']}",
                    f"Model：{clean_line(str(self.init.get('model') or self.cfg.get('model') or '默认'), 80)}"]
        rows += ["审批：Claude Code 问到的都上手机；危险清单生效",
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("Agent：Claude Code\n" + "\n".join(rows))

    async def cmd_model(self, arg: str) -> Result:
        if not await self._ensure():
            return Result("Claude Code 没起来，没法换模型。", "error")
        if not arg:
            try:
                r = await self.control("list_models")
            except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
                return Result(f"没拿到模型列表（{clean(str(e), 120) or type(e).__name__}）。", "error")
            cur = self.cfg.get("model") or self.init.get("model") or ""
            models = []
            for m in r.get("models") or []:
                if isinstance(m, dict) and isinstance(m.get("value"), str):
                    v = clean_line(m["value"], 100)
                    models.append({"id": v, "name": clean_line(str(m.get("displayName") or v), 60),
                                   "desc": clean_line(str(m.get("description") or ""), 120),
                                   "cur": v == cur or m.get("resolvedModel") == cur})
            return Result(f"当前模型：{cur or '默认'}。点一个切换（之后的对话都用它）：", models=models[:40])
        if not slash.MODEL_RE.match(arg):
            return Result(f"模型名不对：{clean_line(arg, 100)}", "error")
        try:
            await self.control("set_model", model=arg)
        except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"没有切换（{clean(str(e), 120) or type(e).__name__}）。", "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=self.model_name(arg))
        return Result(f"已切换到 {arg}：立即生效，写进了 config.json（重启后也用它）。")

    async def cmd_help(self, arg: str) -> Result:
        skills = [s for s in (self.init.get("skills") or []) if isinstance(s, str)][:40]
        return Result(slash.HELP + (("\nClaude Code 的技能（照原文转给它）：" + " ".join("/" + clean_line(s, 40) for s in skills))
                                    if skills else ""))


def make(host, cfg: dict | None):
    if not cfg:
        return None
    if cfg.get("session_mode") == "shared" and not cfg.get("_workflow_ceo"):
        from .shared import make_shared
        return make_shared(host, cfg)
    if cfg["kind"] == "claude":
        return ClaudeAgent(host, cfg)
    if cfg["kind"] == "codex":
        from .agent_codex import CodexAgent        # codex app-server + phone approvals (ADR-A70)
        return CodexAgent(host, cfg)
    if cfg["kind"] == "opencode":
        from .agent_opencode import OpenCodeAgent   # opencode serve + SSE + phone approvals (ADR-A55)
        return OpenCodeAgent(host, cfg)
    return None
