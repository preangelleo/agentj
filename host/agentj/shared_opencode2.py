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
- slash commands work (the server is Agent J's own child). A v2 server the OWNER started (`shared_opencode_port`) is attached
  by OwnerOpenCodeV2Agent below (P64; before it, a plain notice only): text, cards and desktop mirroring, nothing else.
"""
from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import quote

from .agent_opencode import CONNECT_WAIT, HTTPError, session_rules, split_model
from .agent_opencode2 import AGENT_TRIES, END, OpenCodeV2Agent, _data, rules_from_v2, rules_to_v2
from .privacy import redact as public_text

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


OWNER_V2_RISK = ("附着你自己开的 OpenCode v2 server 时，「高危操作提醒」用不了：v1 的预执行插件不是 v2 插件，而改你 server 上会话的权限规则"
                 "又不是 Agent J 该做的事。二选一：关掉 agent.high_risk_warnings，或去掉 agent.shared_opencode_port 让 Agent J 自己启动 OpenCode。"
                 " / High-risk warnings are unavailable on an OpenCode v2 server you started (the v1 plugin is not a v2 plugin and Agent"
                 " J never edits your session's rules): turn agent.high_risk_warnings off, or unset agent.shared_opencode_port.")


class OwnerOpenCodeV2Agent(SharedOpenCodeV2Agent):
    """P64: a shared session on an OpenCode v2 server the OWNER started (`shared_opencode_port` + `shared_session_id`).

    Same contract as the v1 owner path (shared.SharedOpenCodeAgent): the owner's server and session are attached, never
    started, restarted, re-permissioned or killed; the password comes from Agent J's own environment
    (OPENCODE_SERVER_PASSWORD / _USERNAME), never config or the phone; text only (slash commands and /model stay on the
    desktop); the desktop is a legitimate approver (its replies resolve the phone's cards, never count as tampering); what
    the owner types on the desktop is mirrored to the phone. v2 shape (measured on 2.0.23): a typed prompt is
    `session.inbox.enqueued {inboxID, item: {type: user, payload: {text}}}`; the reply streams as `session.text.ended`."""
    owned_server = False

    def _own_protocol(self) -> bool:
        return False                                 # the server's own /api/info decided v2, not `opencode --version` here

    def _switch(self, version) -> bool:
        return False

    async def _spawn(self) -> bool:
        from .shared import _ExternalServer
        if self.cfg.get("high_risk_warnings", False):
            self.proc, self.failed_start = None, True
            self.local_fail(OWNER_V2_RISK)
            return False
        self.proc = _ExternalServer()
        self.phone_texts: list = []                  # texts posted from the phone, until their inbox event names them
        self.phone_inbox: set = set()
        self.desktop_turn = False
        try:
            st, info = await self.client.request("GET", "/api/info")
            if st != 200 or not isinstance(info, dict) or not isinstance(info.get("version"), str):
                raise HTTPError(f"info {st}")
            self.root = self.cfg["dir"]
            self.agent_rules, self.rules = [], []
            self.connected_at_start = await self._connected()
            self.texts_seen, self.identity_sent, self.step_tokens = set(), {}, None
            await self._attach()
            self.connected = asyncio.Event()
            self._bg(self._events(self.proc))
            await asyncio.wait_for(self.connected.wait(), CONNECT_WAIT)
        except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
            self.proc = None
            self.failed_start = True
            self.fail_notice("不能附着电脑 OpenCode server/session。 / Cannot attach the desktop OpenCode server/session.")
            return False
        self.failed_start = False
        self.host.st.log("agent_attach", agent=self.kind, protocol="v2", owner=True)
        return True

    async def _attach(self) -> None:
        """Exactly the session the owner named, in this project — never a new one, never a PATCH."""
        sid = self.cfg.get("shared_session_id")
        if not isinstance(sid, str) or not re.fullmatch(r"ses[A-Za-z0-9_-]+", sid):
            raise HTTPError("exact session id required")
        st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
        s = _data(s)
        if st != 200 or not isinstance(s, dict) or s.get("id") != sid:
            raise HTTPError("session unavailable")
        where = (s.get("location") or {}).get("directory") if isinstance(s.get("location"), dict) else None
        if not isinstance(where, str) or os.path.realpath(where) != os.path.realpath(self.cfg["dir"]):
            raise HTTPError("wrong working directory")
        self.session_model = s.get("model") if isinstance(s.get("model"), dict) else None
        self.sid = sid
        self.host.st.set_agent_session(self.kind, sid)

    async def _session_ok(self) -> bool:
        return self.sid is not None

    async def _model(self) -> None:
        return None                                  # the owner's session keeps the owner's model

    async def _creds_check(self) -> None:
        return None                                  # never restart a server Agent J did not start

    async def command(self, name, arg):
        await self._initial()
        from .slash import Result
        return Result("共享模式目前仅验证文字投递与桌面同步；此控制请在电脑执行。 / Use the desktop for this control.", "error")

    async def apply_model(self, model, effort, default=False):
        return "unsupported"

    async def turn(self, text: str) -> None:
        if not await self._initial():
            return
        if self.proc is None and not await self._spawn():
            return
        self.phone_texts.append(text)
        self.desktop_end_if_open()
        return await super().turn(text)

    def desktop_end_if_open(self) -> None:
        if self.desktop_turn:
            self.desktop_turn = False
            self.host.desktop_end()

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        p = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        mine = self.sid is not None and p.get("sessionID") == self.sid
        if t == "session.inbox.enqueued" and mine:
            item = p.get("item") if isinstance(p.get("item"), dict) else {}
            text = (item.get("payload") or {}).get("text") if isinstance(item.get("payload"), dict) else None
            iid = p.get("inboxID")
            if item.get("type") == "user" and isinstance(text, str) and iid not in self.phone_inbox:
                if text in self.phone_texts:
                    self.phone_texts.remove(text)
                    self.phone_inbox.add(iid)
                else:
                    self.desktop_end_if_open()
                    self.desktop_turn = True
                    self.host.desktop_input(public_text(text))
            return
        if t in ("permission.replied", "form.replied", "form.cancelled"):
            # The desktop is a legitimate native approver: resolve the phone's card, never call it tampering.
            rid = p.get("requestID") or p.get("formID") or p.get("id")
            for pending in (self.pending, self.questions):
                f = pending.get(rid)
                if f and not f.done():
                    f.set_result(True)
            self.q_sent.pop(rid, None)
            return
        if t == "session.updated":
            return                                   # the owner may change their own session's rules
        super().on_event(ev)
        if t in END and mine and self.desktop_turn and not (self.turn_done and not self.turn_done.is_set()):
            self.desktop_end_if_open()

    def _text(self, key: str, text) -> None:
        if key in self.texts_seen or not isinstance(text, str) or not text.strip():
            return
        self.texts_seen.add(key)
        text = public_text(text)
        if not text:
            return
        if self.desktop_turn and not (self.turn_done and not self.turn_done.is_set()):
            self.host.desktop_text(text)
        else:
            self.host.agent_text(text)

    async def _kill(self, p) -> None:
        from .shared import _ExternalServer
        if not isinstance(p, _ExternalServer):
            return await super()._kill(p)
        p.returncode = 0                             # connection disposal only, never the owner's process
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
