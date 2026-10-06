"""P63: the default session mode (shared) on OpenCode v2.

`agent.session_mode = "shared"` is the default. Without an owner server (`shared_opencode_port` unset) Agent J starts an
ordinary `opencode serve` itself (shared.py, `owned_server`). Before P63 that path asked `opencode --version` and refused
2.x outright ("v2 的 /api 协议尚未接入") — the official installer ships v2, so a default install with the official OpenCode
never answered (0.15.3a1). The independent mode was fine: P60's e2e only ran that one.

This class is that owned ordinary server on v2: the protocol is agent_opencode2's (session / prompt / `/api/event` /
permission reply / interrupt / compact / model / forms → question cards), the shared semantics are shared.py's:
- no fence (`cfg["fence"] = False`), the owner's HOME and OpenCode's own permission rules; no rules of ours are added;
- the main-Agent identity is not injected — a shared session reads the working root's native AGENTS.md (as on v1);
- the ordinary session is titled "Agent J shared", kept across serve restarts (key / proxy change), its id in
  `shared_session_id`;
- `high_risk_warnings`: the v1 pre-execution plugin (`shared_opencode_hook`, OpenCode 1.x `tool.execute.before`) is not a
  v2 plugin, so on v2 the warning layer rides on v2's own permission rules instead (the risky patterns ask on the phone —
  what the independent v2 adapter does with the same switch);
- slash commands work (the server is Agent J's own child); a server the owner started (`shared_opencode_port`) is never
  driven on v2: shared.py says so in plain words (not a key problem; drop the port, or use the independent mode).
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

from .agent_opencode import HTTPError, session_rules, split_model
from .agent_opencode2 import AGENT_TRIES, OpenCodeV2Agent, _data, rules_from_v2, rules_to_v2

TITLE = "Agent J shared"


def switch_shared(agent, version) -> bool:
    """shared.py / this module: the owned ordinary server follows the installed OpenCode (v1 ⇄ v2), staying shared."""
    from .harness import opencode_v2
    if opencode_v2(version) == agent.v2:
        return False
    from .shared import SharedOpenCodeAgent
    agent.__class__ = SharedOpenCodeV2Agent if opencode_v2(version) else SharedOpenCodeAgent
    agent.host.st.log("agent_protocol", agent=agent.kind, version="v2" if agent.v2 else "v1")
    return True


class SharedOpenCodeV2Agent(OpenCodeV2Agent):
    """Reached only by a class switch from shared.SharedOpenCodeAgent (owned_server, risk, attach_task already set)."""
    owned_server = True

    def _own_protocol(self) -> bool:
        return True

    def _switch(self, version) -> bool:
        return switch_shared(self, version)

    def launch_argv(self, argv):
        return argv                                  # no fence, no permission flags: as shared.py's owned server

    async def _identity(self) -> None:
        return None                                  # native AGENTS.md (shared semantics), never an injected entry

    async def _prepare(self) -> None:
        st, info = await self.client.request("GET", "/api/info")
        if st != 200 or not isinstance(info, dict) or not isinstance(info.get("version"), str):
            raise HTTPError(f"info {st}")
        self.root = self.cfg["dir"]
        self.agent_rules, self.rules = [], []
        if self.cfg.get("high_risk_warnings", False):    # the v1 plugin is not a v2 plugin: v2's own ask rules instead
            ag = None
            for _ in range(AGENT_TRIES):
                st, ags = await self.client.request("GET", "/api/agent")
                ags = _data(ags)
                if st != 200 or not isinstance(ags, list):
                    raise HTTPError(f"agent {st}")
                ag = next((a for a in ags if isinstance(a, dict) and (a.get("id") or a.get("name")) == "build"), None)
                if ag is not None:
                    break
                await asyncio.sleep(0.25)
            if ag is None:
                raise HTTPError("no primary agent")
            self.agent_rules = rules_from_v2(ag.get("permissions"))
            self.rules = rules_to_v2(session_rules(self.agent_rules))
        self.connected_at_start = await self._connected()
        self.texts_seen, self.identity_sent, self.step_tokens = set(), {}, None
        await self._attach()

    async def _attach(self) -> None:
        """The ordinary session agent.json names (same project), else a new one; never a PATCH of its permissions."""
        sid = self.host.st.agent_session(self.kind)
        if sid:
            st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
            s = _data(s)
            where = ((s.get("location") or {}).get("directory") if isinstance(s, dict) else None)
            if st != 200 or not isinstance(s, dict) or s.get("id") != sid or (
                    isinstance(where, str) and os.path.realpath(where) != os.path.realpath(self.cfg["dir"])):
                sid = None
        if not sid:
            body = {"title": TITLE, "location": {"directory": self.cfg["dir"]}}
            m = split_model(self.cfg.get("model"))
            if m:
                body["model"] = {"id": m["modelID"], "providerID": m["providerID"]}
            if self.rules:
                body["permissions"] = self.rules
            st, s = await self.client.request("POST", "/api/session", body)
            if st != 200 or not isinstance(_data(s), dict) or not isinstance(_data(s).get("id"), str):
                raise HTTPError(f"session {st}")
            sid = _data(s)["id"]
        st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
        if st != 200 or not isinstance(_data(s), dict):
            raise HTTPError("session not found")
        self.session_model = _data(s).get("model") if isinstance(_data(s).get("model"), dict) else None
        self.sid = sid
        self.cfg["shared_session_id"] = sid
        self.host.st.set_agent_session(self.kind, sid)

    async def _initial(self) -> bool:
        initial = getattr(self, "attach_task", None)
        if initial is not None and not initial.done() and initial is not asyncio.current_task():
            return bool(await initial)
        return True

    async def turn(self, text: str) -> None:
        if not await self._initial():                 # the start-up spawn (shared.py start()) finishes first; it said why
            return
        return await super().turn(text)

    async def command(self, name, arg):
        await self._initial()
        return await super().command(name, arg)

    async def stop(self) -> None:
        await super().stop()
        if getattr(self, "risk_channel", None):
            await self.risk_channel.stop()
