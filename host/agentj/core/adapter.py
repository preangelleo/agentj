"""CoreAgent (AGENTJ-CORE-SPEC 2.2 `core/adapter`): Agent J Harness as the main Agent inside serve — `agent.kind = "core"`,
next to ClaudeAgent / CodexAgent, behind AGENTJ_CORE=1 (P359).

Same process as serve: an approval is a function call to Host.ask (origin None for the main session itself, the host-built
`sub-agent「…」#n` for its own sub-agents). The model key never leaves this process; the toolbox runs in the fence rooted at
the working folder (refusing to start when the fence cannot, as every harness does). The conversation is a session file under
`<state>/core/sessions/<id>.jsonl` named by the host's agent_session("core"), so /clear and its undo are the base class's.
Tools: the seven + `task` + `dispatch` (orders to project CEOs; the project must be the working folder or inside it).
P431 C5: the slash commands are kernel functions on the same session file — /compact (summary + handover file, no extra
Agent turn), /context (exact parts), /cost and /usage (tokens per model since /clear), /model (keyed presets), /status.
P431 C8: the owner's MCP servers (core/mcp.py) start with the first turn that needs them and stay for the session; their
tools go through the same gate; a change of mcp.json starts them again at the next turn.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

from ..agent import Agent
from .. import slash
from ..slash import Result
from . import LABEL, compact, dispatcher, llm, mcp, prompt
from .presets import PresetError, resolve


async def _accepted() -> bool:
    return True


class CoreAgent(Agent):
    kind = "core"
    LABEL = LABEL

    def __init__(self, host, cfg: dict):
        super().__init__(host, cfg)
        self.toolbox = None
        self.stop_flag = False
        self.denied: set = set()
        self.usage = {"input": 0, "output": 0}
        self.hub, self.hub_key = None, None

    def _mcp_hub(self):
        """The session's MCP hub, (re)built when the owner's server list changed (blocking: call in a thread)."""
        p = mcp.path(self.host.st.root)
        try:
            key = (p.stat().st_mtime_ns, p.stat().st_size)
        except OSError:
            key = None
        if key != self.hub_key:
            if self.hub is not None:
                self.hub.close()
            self.hub_key = key
            log = getattr(self.host.st, "log", None)
            self.hub = mcp.Hub.for_session(self.host.st.root, self.cfg["dir"], st=self.host.st,
                                           fenced=self.cfg.get("fence", True), log=log) if key else None
        return self.hub

    def is_down(self) -> bool:
        return False

    def session_open(self) -> bool:
        return True

    def _sid(self) -> str:
        st = self.host.st
        sid = st.agent_session(self.kind)
        if not sid:
            sid = "core-" + secrets.token_hex(8)
            st.set_agent_session(self.kind, sid)
        return sid

    def _session(self):
        from .session import Session
        return Session(Path(self.host.st.root) / "core" / "sessions" / f"{self._sid()}.jsonl")

    async def _ask(self, tool: str, tool_input: dict, who: str | None) -> dict:
        return await self.host.ask(tool, tool_input, None, origin=who)

    def _toolbox(self):
        if self.toolbox is None or self.toolbox.proc.poll() is not None:
            from .toolbox import Toolbox
            self.toolbox = Toolbox(self.cfg["dir"], st=self.host.st, fenced=self.cfg.get("fence", True))
        return self.toolbox

    def _system(self, preset) -> str:
        from ..main_identity import prompt as identity
        try:
            ident = identity(self.cfg, self.kind)
        except Exception:  # noqa: BLE001 — the identity text is optional for the kernel
            ident = ""
        return prompt.system("main", self.cfg["dir"], preset.tier, identity=ident, edit_mode=preset.edit_mode)

    def _handover(self, text: str, files: list) -> str | None:
        """The compaction summary as `<working root>/.agentj/handover/compact.md` (compactprep's path), written through
        the inbox's O_NOFOLLOW directory chain: tmp + rename inside the directory fd, 0600; never through a planted link."""
        import os
        from .. import compactprep, inbox
        data = compact.handover_text(text, files).encode()
        name = compactprep.FILE + ".md"
        try:
            with inbox.dot_dir(self.cfg["dir"]) as d:
                fd = inbox._open_dir(compactprep.DIR, d, True)
                try:
                    tmp = f".{name}.{os.getpid()}.{secrets.token_hex(3)}"
                    f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                    try:
                        os.write(f, data)
                    finally:
                        os.close(f)
                    os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    os.close(fd)
        except (OSError, inbox.Unsafe):
            return None
        return compactprep.handover_path(self)

    def _plain(self):
        """(preset, a tool-less Loop on this conversation) for the commands; (None, reason) without a usable model."""
        from .loop import Loop
        from .pipeline import Gate
        try:
            preset = resolve(self.cfg.get("model"), "main")
        except PresetError as e:
            return None, e.args[0]
        lp = Loop(preset=preset, system=self._system(preset), specs=prompt.specs("main", preset.tier, preset.edit_mode),
                  gate=Gate(self.cfg["dir"], None), session=self._session(), on_compact=self._handover)
        return preset, lp

    async def turn(self, text: str) -> None:
        self.stop_flag = False
        root = self.cfg["dir"]
        try:
            preset = resolve(self.cfg.get("model"), "main")
        except PresetError as e:
            self.fail_notice(f"{LABEL} 没有可用的模型：{e.args[0]}。让 Agent 发手机密钥卡存一把模型 key（例如火山方舟 ARK_API_KEY），"
                             "再 `agentj service restart`。")
            return
        try:
            tb = await asyncio.to_thread(self._toolbox)
        except RuntimeError as e:
            self.local_fail(f"{LABEL} 的隔离没有起来（{e}），这条没有执行。在 Agent J 主机上运行 agentj doctor 查看。")
            return
        from .loop import Loop, SubAgents
        from .pipeline import Gate
        stopped = lambda: self.stop_flag or bool(getattr(self.host, "stopped", lambda: False)())
        hub = await asyncio.to_thread(self._mcp_hub) if preset.tier != "small" else None
        mcp_specs = await asyncio.to_thread(mcp.offered, hub, preset.tier)
        gate = Gate(root, self._ask, denied=self.denied, stopped=stopped, extra=self.cfg.get("danger_extra"),
                    mcp_allowed=hub.allowed if hub else None)
        from .toolbox import Toolbox
        subs = SubAgents(preset=preset, root=root, gate=gate, sessions_dir=Path(self.host.st.root) / "core" / "sessions",
                         make_toolbox=lambda: Toolbox(root, st=self.host.st, fenced=self.cfg.get("fence", True)),
                         control=lambda: "stop" if stopped() else None)

        async def dispatch(args):
            from .order_host import handle
            res = await handle(self.host, {"args": args, "nonce": secrets.token_hex(16)}, internal=self)
            return res.get("text") or "Error: " + res.get("why", "refused")
        lp = Loop(preset=preset, system=self._system(preset),
                  specs=prompt.specs("main", preset.tier, preset.edit_mode) + mcp_specs,
                  gate=gate, toolbox=tb, session=self._session(),
                  control=lambda: "stop" if stopped() else None, extra={"task": subs, "dispatch": dispatch},
                  on_delta=self.touch, on_compact=self._handover, mcp=hub)
        try:
            await self.deliver(_accepted)          # §10.2: the kernel took the message (no harness write to wait for)
            r = await lp.run(text)
        except llm.LLMError as e:
            self._auth_signal(f"{e.kind} {e.status or ''}")
            self.fail_notice(f"{LABEL}：模型服务出错（{e.kind}{' ' + str(e.status) if e.status else ''}），这条没有完成。")
            return
        self.touch()
        self.usage = {k: self.usage[k] + lp.usage[k] + subs.usage[k] for k in self.usage}
        self.meter(ctx={"used": lp.last_input, "max": preset.context, "session_id": self.host.st.agent_session(self.kind),
                        "model_id": preset.name, "at": time.time()} if lp.last_input else None, model=preset.name)
        if r.get("text"):
            self.host.agent_text(r["text"])

    async def halt(self, clear_queue: bool = True) -> bool:
        busy = self.status == "working"
        self.stop_flag = True
        await super().halt(clear_queue)
        return busy

    async def stop(self) -> None:
        await super().stop()
        tb, self.toolbox = self.toolbox, None
        if tb is not None:
            await asyncio.to_thread(tb.close)
        hub, self.hub, self.hub_key = self.hub, None, None
        if hub is not None:
            await asyncio.to_thread(hub.close)

    async def cmd_status(self, arg: str) -> Result:
        try:
            p = resolve(self.cfg.get("model"), "main").name
        except PresetError:
            p = "—"
        return Result(f"{LABEL} · {p} · 本次累计 tokens 输入 {self.usage['input']} / 输出 {self.usage['output']}\n"
                      f"会话：{self.host.st.agent_session(self.kind) or '（新对话）'} · 目录：{self.cfg['dir']}\n"
                      "审批：危险步骤都上手机；隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关"), "info")

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        preset, lp = self._plain()
        if preset is None:
            return Result(f"{LABEL} 没有可用的模型（{lp}），没法压缩。", "error")
        try:
            r = await lp.compact(arg)
        except llm.LLMError as e:
            return Result(f"没有压缩：模型服务出错（{e.kind}）。对话原样保留。", "error")
        if r is None:
            return Result("对话还很短，没有可压缩的部分。", "info")
        self.meter(ctx=None)                       # the provider has not counted the compacted prompt yet
        try:
            from ..compactprep import handover_path
            where = handover_path(self) if Path(handover_path(self)).is_file() else None
        except Exception:  # noqa: BLE001
            where = None
        return Result(f"已压缩：{slash.tokens(r['before'])} → {slash.tokens(r['after'])} tokens（保留最近 {r['kept']} 条，"
                      f"涉及文件 {len(r['files'])} 个）" + (f"\n交接写在 {where}" if where else ""))

    async def cmd_context(self, arg: str) -> Result:
        preset, lp = self._plain()
        if preset is None:
            return Result("—", "info")
        b = compact.breakdown(lp.system, lp.specs, lp.msgs)
        limit = lp._limit()
        return Result(f"上下文 {slash.tokens(b['total'])} / {slash.tokens(preset.context)}{slash.pct(b['total'], preset.context)}"
                      f" · 模型 {preset.name}\n系统提示 {slash.tokens(b['system'])} · 工具定义 {slash.tokens(b['tools'])} · "
                      f"你的消息 {slash.tokens(b['user'])} · 回复 {slash.tokens(b['assistant'])} · 工具输出 "
                      f"{slash.tokens(b['tool_output'])}" + (f" · 压缩摘要 {slash.tokens(b['summary'])}" if b["summary"] else "")
                      + f"\n超过 {slash.tokens(limit)} 时先裁旧工具输出，再自动压缩（/compact 可随时手动压缩）。"
                      f"按估算口径（与内核预算同一算法），服务商实际计数可能略有不同。", "info")

    def _usage_lines(self) -> list[str]:
        tot = self._session().usage()
        return [f"{m}：输入 {slash.tokens(i)} · 输出 {slash.tokens(o)}" for m, (i, o) in sorted(tot.items())]

    async def cmd_cost(self, arg: str) -> Result:
        lines = self._usage_lines()
        if not lines:
            return Result("本会话花费：还没有调用过模型。", "info")
        return Result("本会话用量（自上次 /clear，按服务商报告的 token 计）：\n" + "\n".join(lines)
                      + f"\n{LABEL} 不保存服务商单价，金额以服务商账单为准。", "info")

    async def cmd_usage(self, arg: str) -> Result:
        lines = self._usage_lines()
        try:
            plan = resolve(self.cfg.get("model"), "main").plan
        except PresetError:
            plan = False
        return Result(("本会话 token：\n" + "\n".join(lines) if lines else "本会话 token：还没有调用过模型。")
                      + ("\n这是 Coding Plan 的 key：套餐窗口额度请在服务商控制台查看；长时间后台任务可能被限流。" if plan else ""),
                      "info")

    async def refresh_models(self) -> None:
        from .presets import listing
        self.models_cache = [{"id": f"{p['name']}/{m}", "name": f"{p['label']} · {m}", "efforts": None}
                             for p in listing() if p["has_key"] for m in p["models"]][:40]

    async def cmd_model(self, arg: str) -> Result:
        try:
            cur = resolve(self.cfg.get("model"), "main").name
        except PresetError:
            cur = ""
        if not arg:
            await self.refresh_models()
            if not self.models_cache:
                return Result(f"{LABEL} 还没有任何模型 key。让 Agent 发手机密钥卡存一把（例如火山方舟 ARK_API_KEY）。", "info")
            return Result(f"当前模型：{cur or '默认'}。点一个切换（之后的对话都用它）：",
                          models=[{"id": m["id"], "name": m["name"], "cur": m["id"] == cur} for m in self.models_cache])
        if not slash.MODEL_RE.match(arg):
            return Result("模型名不对。", "error")
        try:
            p = resolve(arg, "main")
        except PresetError as e:
            return Result(f"没有这个模型：{e.args[0]}（发 /model 看能选的）。", "error")
        if not p.key():
            return Result(f"{p.provider} 还没有 key（{p.key_env}）：让 Agent 发手机密钥卡存一把，不要发在聊天里。", "error")
        self.host.set_model(p.name)
        self.cfg["model"] = p.name
        self.meter(model=p.name, model_name=p.name, ctx=None)
        return Result(f"已切换到 {p.name}，下一条消息起生效。")
