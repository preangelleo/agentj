"""CoreAgent (AGENTJ-CORE-SPEC 2.2 `core/adapter`): Agent J Harness as the main Agent inside serve — `agent.kind = "core"`,
next to ClaudeAgent / CodexAgent, behind AGENTJ_CORE=1 (P359).

Same process as serve: an approval is a function call to Host.ask (origin None for the main session itself, the host-built
`sub-agent「…」#n` for its own sub-agents). The model key never leaves this process; the toolbox runs in the fence rooted at
the working folder (refusing to start when the fence cannot, as every harness does). The conversation is a session file under
`<state>/core/sessions/<id>.jsonl` named by the host's agent_session("core"), so /clear and its undo are the base class's.
Tools: the seven + `task` + `dispatch` (orders to project CEOs; the project must be the working folder or inside it).
"""
from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from ..agent import Agent
from ..slash import Result
from . import LABEL, dispatcher, llm, prompt
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

    async def turn(self, text: str) -> None:
        self.stop_flag = False
        root = self.cfg["dir"]
        try:
            preset = resolve(self.cfg.get("model"), "main")
        except PresetError as e:
            self.fail_notice(f"{LABEL} 没有可用的模型：{e.args[0]}。让 Agent 发手机密钥卡存一把模型 key（例如火山方舟 ARK_API_KEY），"
                             "再 agentj service restart。")
            return
        try:
            tb = await asyncio.to_thread(self._toolbox)
        except RuntimeError as e:
            self.local_fail(f"{LABEL} 的隔离没有起来（{e}），这条没有执行。在 Agent J 主机上运行 agentj doctor 查看。")
            return
        from .loop import Loop, SubAgents
        from .pipeline import Gate
        stopped = lambda: self.stop_flag or bool(getattr(self.host, "stopped", lambda: False)())
        gate = Gate(root, self._ask, denied=self.denied, stopped=stopped, extra=self.cfg.get("danger_extra"))
        from .toolbox import Toolbox
        subs = SubAgents(preset=preset, root=root, gate=gate, sessions_dir=Path(self.host.st.root) / "core" / "sessions",
                         make_toolbox=lambda: Toolbox(root, st=self.host.st, fenced=self.cfg.get("fence", True)),
                         control=lambda: "stop" if stopped() else None)

        async def dispatch(args):
            return await asyncio.to_thread(dispatcher.tool, args, parent={"kind": "owner_turn", "session": self._sid()},
                                           allowed_roots=[root], model=self.cfg.get("model"), origin="owner_turn",
                                           fence=self.cfg.get("fence", True), danger_extra=self.cfg.get("danger_extra"))
        from ..main_identity import prompt as identity
        try:
            ident = identity(self.cfg, self.kind)
        except Exception:  # noqa: BLE001 — the identity text is optional for the kernel
            ident = ""
        lp = Loop(preset=preset, system=prompt.system("main", root, preset.tier, identity=ident),
                  specs=prompt.specs("main", preset.tier), gate=gate, toolbox=tb, session=self._session(),
                  control=lambda: "stop" if stopped() else None, extra={"task": subs, "dispatch": dispatch})
        try:
            await self.deliver(_accepted)          # §10.2: the kernel took the message (no harness write to wait for)
            r = await lp.run(text)
        except llm.LLMError as e:
            self._auth_signal(f"{e.kind} {e.status or ''}")
            self.fail_notice(f"{LABEL}：模型服务出错（{e.kind}{' ' + str(e.status) if e.status else ''}），这条没有完成。")
            return
        self.touch()
        self.usage = {k: self.usage[k] + lp.usage[k] + subs.usage[k] for k in self.usage}
        self.meter(used=lp.usage["input"])
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

    async def cmd_status(self, arg: str) -> Result:
        try:
            p = resolve(self.cfg.get("model"), "main").name
        except PresetError:
            p = "—"
        return Result(f"{LABEL} · {p} · 本次累计 tokens 输入 {self.usage['input']} / 输出 {self.usage['output']}", "info")

    async def cmd_context(self, arg: str) -> Result:
        try:
            p = resolve(self.cfg.get("model"), "main")
        except PresetError:
            return Result("—", "info")
        return Result(f"系统提示 + 工具定义 ≈ {prompt.harness_tokens('main', p.tier)} tokens（上限 {prompt.HARNESS_MAX}）", "info")
