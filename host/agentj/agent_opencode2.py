"""OpenCode v2 adapter (P60): the same `opencode serve` child as v1 (agent_opencode.py) — same fence, same random
`OPENCODE_SERVER_PASSWORD`, same startup line — but v2's server speaks a different protocol, and the official installer has
shipped v2 (`@opencode/cli`, 2.0.x) since early October 2026: a user who installs OpenCode today gets v2, and before P60
Agent J refused it outright ("OpenCode v2 的 /api 协议尚未接入"). Measured on 2.0.23 (reports/qa/p60/):

- every legacy route (`/session`, `/event`, `/provider` …) answers 200 with the web UI's HTML; the JSON API is `/api/*`
  (OpenAPI at `/openapi.json`), each answer wrapped as `{"data": …}`;
- `POST /api/session {title, location: {directory}, model: {id, providerID}, permissions}` → a session; a message is
  `POST /api/session/{id}/prompt {text}` (accepted = 200; it runs in the background); `POST …/interrupt`, `POST …/compact`,
  `POST …/model {model}`; Agent J's main-Agent identity rides as a session instruction entry
  (`PUT /api/experimental/session/{id}/instructions/entries/agentj.identity`);
- the event stream `GET /api/event` sends `{type, data: {sessionID, …}}`: `session.text.ended` (a finished text block),
  `session.execution.started / succeeded / failed / interrupted` (the turn), `session.step.ended` (tokens / cost),
  `session.step.failed` / `session.execution.failed` with `error: {type: "provider.auth" | "provider.no-route" | …, status,
  message}`, `permission.asked {id, action, resources}` answered by `POST /api/session/{sid}/permission/{id}/reply
  {decision: once | reject, message}`, `form.created` (the v1 question tool);
- permission rules are `{action, resource, effect}` and the shell tool is called `shell` (v1: `bash`);
- which providers have a key: `GET /api/integration` → `connections` (env / key / oauth), never a value.

Everything else (the fence, the phone's cards, danger.classify, the turn queue, tamper checks, key-failure restart) is the
v1 adapter's, inherited. `OpenCodeAgent._spawn` switches an instance to this class when `opencode --version` says 2.x.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from urllib.parse import quote

from . import slash
from .agent import UNCERTAIN
from .agent_opencode import (HTTPError, OpenCodeAgent, PROVIDER_LOGIN_ATTACHED, PROVIDER_NOTES, RESTART_AFTER,
                             TURN_IDLE, WATCH, ends_with_ours, provider_error_reason, session_rules, split_model)
from .slash import Result
from .text import clean_line

IDENTITY_KEY = "agentj.identity"
# v2 keeps several keys per provider (「DeepSeek 2」): a second key that is saved but not active is never used (measured:
# `auth import` keeps the first one active; 401 until `auth switch`). A running v2 serve uses the newly active key at once.
V2_SWITCH = (" 同一服务商存了好几把 key 时（例如「DeepSeek 2」），在电脑运行 `opencode auth switch {provider}` 选中正确的那一把。"
             " / Several keys for one provider: pick the right one with `opencode auth switch {provider}`.")
AGENT_TRIES = 40
END = ("session.execution.succeeded", "session.execution.failed", "session.execution.interrupted")


def _data(r):
    return r.get("data") if isinstance(r, dict) else None


def rules_from_v2(rules) -> list[dict]:
    """v2 {action, resource, effect} → the v1 shape session_rules / evaluate know ({permission, pattern, action}); the shell
    tool is `bash` there."""
    out = []
    for r in rules if isinstance(rules, list) else []:
        if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("action", "resource", "effect")):
            out.append({"permission": "bash" if r["action"] == "shell" else r["action"], "pattern": r["resource"],
                        "action": r["effect"]})
    return out


def rules_to_v2(rules) -> list[dict]:
    out = []
    for r in rules or []:
        perm = r["permission"]
        out.append({"action": "shell" if perm == "bash" else perm, "resource": r["pattern"], "effect": r["action"]})
    return out


def v2_error(err) -> tuple[str, dict]:
    """A v2 structured error → (name, data) in the shape provider_error_reason reads (statusCode / message / responseBody)."""
    err = err if isinstance(err, dict) else {}
    resp = err.get("response") if isinstance(err.get("response"), dict) else {}
    data = {"statusCode": err.get("status"), "message": err.get("message") or "", "responseBody": resp.get("body") or ""}
    name = str(err.get("type") or "")
    if name == "provider.no-route":            # "Model unavailable: <provider>/<model>" — the provider has no key / no model
        data["message"] = "model not found: " + str(data["message"])
    return name, data


def to_v1_request(p: dict) -> dict:
    """permission.asked (v2) → the v1 request shape to_tool knows."""
    action = str(p.get("action") or "?")
    res = [x for x in (p.get("resources") or []) if isinstance(x, str)]
    md = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
    if action == "shell":
        return {"id": p.get("id"), "permission": "bash", "patterns": res,
                "metadata": {**md, "command": md.get("command") if isinstance(md.get("command"), str) else "\n".join(res)}}
    return {"id": p.get("id"), "permission": action, "patterns": res, "metadata": md}


def form_questions(form: dict) -> tuple[list | None, list]:
    """form.created → (the question card's raw list, [(field key, multiselect?)]) — None when a field is not a choice the card
    can show (free text, numbers, external links): the form is cancelled and the model asks in text."""
    raw, keys = [], []
    for f in form.get("fields") or []:
        if not isinstance(f, dict) or f.get("hidden"):
            continue
        opts = f.get("options") if isinstance(f.get("options"), list) else None
        if f.get("type") not in ("multiselect", "string") or not opts:
            return None, []
        multi = f["type"] == "multiselect" and f.get("maxItems") != 1
        raw.append({"question": f.get("title") or f.get("description") or form.get("title") or "?",
                    "header": "", "multiSelect": multi,
                    "options": [{"label": o.get("label") or o.get("value"), "description": o.get("description") or ""}
                                for o in opts if isinstance(o, dict)]})
        keys.append((f.get("key"), f["type"] == "multiselect", {o.get("label") or o.get("value"): o.get("value")
                                                               for o in opts if isinstance(o, dict)}))
    return (raw or None), keys


class OpenCodeV2Agent(OpenCodeAgent):
    EVENT_PATH = "/api/event"
    v2 = True

    # ------------------------------------------------ start
    async def _prepare(self) -> None:
        st, info = await self.client.request("GET", "/api/info")
        if st != 200 or not isinstance(info, dict) or not isinstance(info.get("version"), str):
            raise HTTPError(f"info {st}")
        self.root = self.cfg["dir"]
        ag = None
        for _ in range(AGENT_TRIES):                     # measured: the agent list is empty for a moment after start
            st, ags = await self.client.request("GET", "/api/agent")
            ags = _data(ags)
            if st != 200 or not isinstance(ags, list):
                raise HTTPError(f"agent {st}")
            ag = next((a for a in ags if isinstance(a, dict) and (a.get("id") or a.get("name")) == "build"), None) \
                or next((a for a in ags if isinstance(a, dict) and a.get("mode") == "primary"), None)
            if ag is not None:
                break
            await asyncio.sleep(0.25)
        # no list yet: OpenCode's own rules still apply inside OpenCode; only our optional extra rules need the human's denies
        self.agent_rules = rules_from_v2(ag.get("permissions")) if ag else []
        if ag is None and (self.research or self.cfg.get("high_risk_warnings", False)):
            raise HTTPError("no primary agent")
        hi = self.research or self.cfg.get("high_risk_warnings", False)
        self.rules = rules_to_v2(session_rules(self.agent_rules, self.research)) if hi else []
        self.connected_at_start = await self._connected()
        self.texts_seen: set = set()
        self.identity_sent: dict = {}
        self.step_tokens: dict | None = None
        await self._attach()

    async def _connected(self) -> list | None:
        """Integrations (≈ providers) with at least one connection — env variable, stored key or OAuth — plus the providers
        the owner declared in OpenCode's config (`agentj provider add`: their key is an {env:…} reference that v2 does not list
        as a connection, measured); names only."""
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, r = await self.client.request("GET", "/api/integration")
            if st == 200 and isinstance(_data(r), list):
                out = [x["id"] for x in _data(r) if isinstance(x, dict) and isinstance(x.get("id"), str) and x.get("connections")]
                with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                    st, c = await self.client.request("GET", "/api/config")
                    c = _data(c) if isinstance(_data(c), dict) else c
                    if st == 200 and isinstance(c, dict) and isinstance(c.get("provider"), dict):
                        out += [k for k in c["provider"] if isinstance(k, str) and k not in out]
                return out
        return None

    async def _attach(self) -> None:
        sid = self.host.st.agent_session(self.kind) if self.persist else None
        if sid:
            st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
            if st == 200 and isinstance(_data(s), dict):
                if self.rules and not ends_with_ours(rules_from_v2(_data(s).get("permissions")), rules_from_v2(self.rules)):
                    st, _ = await self.client.request("PATCH", f"/api/session/{quote(sid)}", {"permissions": self.rules})
                    if st != 200:
                        raise HTTPError(f"patch {st}")
            else:
                sid = None
                self.host.st.set_agent_session(self.kind, None)
        if not sid:
            body = {"title": "Agent J" if self.persist else "Agent J 定时任务", "location": {"directory": self.cfg["dir"]}}
            m = split_model(self.cfg.get("model"))
            if m:
                body["model"] = {"id": m["modelID"], "providerID": m["providerID"]}
            if self.rules:
                body["permissions"] = self.rules
            st, s = await self.client.request("POST", "/api/session", body)
            if st != 200 or not isinstance(_data(s), dict) or not isinstance(_data(s).get("id"), str):
                raise HTTPError(f"session {st}")
            sid = _data(s)["id"]
            if self.persist:
                self.host.st.set_agent_session(self.kind, sid)
        st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
        if st != 200 or not isinstance(_data(s), dict):
            raise HTTPError("session not found")
        if self.rules and not ends_with_ours(rules_from_v2(_data(s).get("permissions")), rules_from_v2(self.rules)):
            raise HTTPError("rules not in place")      # never prompt a session whose last rules are not ours
        self.session_model = _data(s).get("model") if isinstance(_data(s).get("model"), dict) else None
        self.sid = sid

    # ------------------------------------------------ the event stream
    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        p = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        mine = p.get("sessionID") == self.sid and self.sid is not None
        if mine and isinstance(t, str) and t.startswith("session."):
            self.turn_progress = time.monotonic()
        if t == "permission.asked" and mine:
            self._on_perm(to_v1_request(p))
        elif t == "permission.replied":
            rid = p.get("requestID")
            f = self.pending.get(rid)
            if f and not f.done():
                f.set_result(True)
            if p.get("reply") in ("once", "always") and rid not in self.ours:
                self._tamper("foreign_reply")
        elif t == "session.text.ended" and mine:
            self._text(f"{p.get('assistantMessageID')}:{p.get('ordinal')}", p.get("text"))
        elif t == "session.step.ended" and mine:
            if isinstance(p.get("tokens"), dict):
                self.step_tokens = p["tokens"]
        elif t == "session.execution.started" and mine:
            self.saw_busy = True
        elif t in ("session.step.failed", "session.execution.failed") and mine and isinstance(p.get("error"), dict):
            self._failed(p["error"])
        elif t == "session.updated" and mine:
            info = p.get("info") if isinstance(p.get("info"), dict) else p
            if "permissions" in info and self.rules and not ends_with_ours(rules_from_v2(info.get("permissions")),
                                                                            rules_from_v2(self.rules)):
                self._tamper("session_rules")
        elif t == "form.created" and self.client:
            form = p.get("form") if isinstance(p.get("form"), dict) else p
            if form.get("sessionID") in (self.sid, None) and isinstance(form.get("id"), str) and form["id"] not in self.questions:
                self._bg(self._form(form))
        elif t in ("form.replied", "form.cancelled"):
            rid = p.get("formID") or p.get("id")
            f = self.questions.get(rid)
            if f and not f.done():
                f.set_result(True)
            if t == "form.replied":
                mine_sent = self.q_sent.pop(rid, None)
                if mine_sent is None:
                    self._tamper("foreign_question_reply")
            else:
                self.q_sent.pop(rid, None)
        if t in END and mine:
            if self.saw_busy or t != "session.execution.succeeded":
                self._turn_end()

    def _text(self, key: str, text) -> None:
        if key in self.texts_seen or not isinstance(text, str) or not text.strip():
            return
        self.texts_seen.add(key)
        if self.quiet:
            return
        if self.collect is not None:
            self.collect.append(text)
        else:
            self.host.agent_text(text)

    def _failed(self, err: dict) -> None:
        name, data = v2_error(err)
        if name in ("aborted", "permission.rejected"):
            return
        code = data.get("statusCode")
        code = code if type(code) is int and 100 <= code <= 599 else None
        sel = split_model(self.cfg.get("model"))
        pid = sel["providerID"] if sel else None
        reason = provider_error_reason(name, data, self.connected_at_start, pid)
        if self.provider_fail_noted:
            return
        self.host.st.log("agent_provider_fail", agent=self.kind, reason=reason, http_status=code)
        self.last_provider_fail = {"reason": reason, "http_status": code, "at": int(time.time())}
        if reason in RESTART_AFTER and self.owns_harness():
            self.restart_after_turn = True
        notes = PROVIDER_NOTES if self.owns_harness() else {**PROVIDER_NOTES, "login": PROVIDER_LOGIN_ATTACHED}
        notes = {**notes, "no_key": notes["no_key"].replace("{provider}", clean_line(pid or "该服务商", 40)),
                 "login": notes["login"] + V2_SWITCH.replace("{provider}", clean_line(pid or "<服务商>", 40))}
        self.provider_fail_noted = True
        self.fail_notice("主机已连接，但这一轮没有正常完成。" + notes[reason])

    async def _form(self, form: dict) -> None:
        """form.created → one question card (choices only); anything else, a cancel / timeout → the form is cancelled and the
        model is told nobody chose (it asks in text)."""
        from .approvals import norm_questions
        fid = form["id"]
        gone = asyncio.get_running_loop().create_future()
        self.questions[fid] = gone
        client, sid = self.client, self.sid
        answer = None
        try:
            raw, keys = form_questions(form)
            card = norm_questions(raw) if raw and not self.research else None
            if card is not None:
                outcome, picks = await self.host.question(card, gone, task=getattr(self.host, "task_label", None))
                if outcome == "answer" and picks:
                    answer = {}
                    for (key, multi, values), q, pk in zip(keys, card, picks):
                        labels = [q["o"][n - 1]["l"] for n in pk]
                        vals = [values.get(lab, lab) for lab in labels]
                        answer[key] = vals if multi else vals[0]
        except Exception:  # noqa: BLE001 — fail closed: cancelled
            answer = None
        finally:
            self.questions.pop(fid, None)
        if gone.done() or not client:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            if answer:
                self.q_sent[fid] = answer
                await client.request("POST", f"/api/session/{quote(sid)}/form/{quote(fid)}/reply", {"answer": answer})
            else:
                await client.request("DELETE", f"/api/session/{quote(sid)}/form/{quote(fid)}")

    async def _reply_permission(self, rid: str, reply: str, msg: str) -> None:
        body = {"decision": reply} if reply == "once" else {"decision": "reject", "message": msg}
        await self.client.request("POST", f"/api/session/{quote(self.sid)}/permission/{quote(rid)}/reply", body)

    async def _resync(self) -> None:
        c = self.client
        if not c or not self.sid:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, reqs = await c.request("GET", f"/api/session/{quote(self.sid)}/permission")
            reqs = _data(reqs)
            if st == 200 and isinstance(reqs, list):
                live = {r.get("id") for r in reqs if isinstance(r, dict)}
                for rid, f in list(self.pending.items()):
                    if rid not in live and not f.done():
                        f.set_result(True)
                for r in reqs:
                    if isinstance(r, dict):
                        self._on_perm(to_v1_request(r))
            await self._catch_up()
            if self.turn_done and not self.turn_done.is_set() and await self._idle_since_turn():
                self._turn_end()

    async def _messages(self) -> list:
        st, r = await self.client.request("GET", f"/api/session/{quote(self.sid)}/message")
        return _data(r) if st == 200 and isinstance(_data(r), list) else []

    async def _catch_up(self) -> None:
        if not (self.client and self.sid and self.turn_done):
            return
        for m in reversed(await self._messages()):            # the list is newest first
            if not isinstance(m, dict) or m.get("type") != "assistant":
                continue
            if ((m.get("time") or {}).get("created") or 0) < self.turn_t0 - 5000:
                continue
            n = 0
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    self._text(f"{m.get('id')}:{n}", c.get("text"))
                    n += 1

    async def _idle_since_turn(self) -> bool:
        st, s = await self.client.request("GET", f"/api/session/{quote(self.sid)}")
        s = _data(s)
        idle = ((s or {}).get("time") or {}).get("idle") if isinstance(s, dict) else None
        return isinstance(idle, (int, float)) and idle >= self.turn_t0

    # ------------------------------------------------ turns
    async def _identity(self) -> None:
        if not (self.persist and not self.cfg.get("_workflow_ceo")):
            return
        from . import main_identity
        text = main_identity.prompt(self.cfg)
        if self.identity_sent.get(self.sid) == text:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, _ = await self.client.request(
                "PUT", f"/api/experimental/session/{quote(self.sid)}/instructions/entries/{IDENTITY_KEY}", {"value": text})
            if st in (200, 204):
                self.identity_sent[self.sid] = text
            else:
                self.host.st.log("agent_identity_fail", agent=self.kind, status=st)

    async def _model(self) -> None:
        m = split_model(self.cfg.get("model"))
        cur = self.session_model or {}
        if not m or (cur.get("providerID"), cur.get("id")) == (m["providerID"], m["modelID"]):
            return
        st, _ = await self.client.request("POST", f"/api/session/{quote(self.sid)}/model",
                                          {"model": {"id": m["modelID"], "providerID": m["providerID"]}})
        if st in (200, 204):
            self.session_model = {"id": m["modelID"], "providerID": m["providerID"]}

    async def turn(self, text: str) -> None:
        if await self._protocol():
            return await self.turn(text)
        await self._creds_check()
        for attempt in range(2):
            if self.proc is None and not await self._spawn():
                return
            if not self.v2:
                return await self.turn(text)
            if not await self._session_ok():
                return
            self.turn_done, self.turn_proc = asyncio.Event(), self.proc
            self.turn_t0 = time.time() * 1000
            self.turn_progress = time.monotonic()
            self.provider_fail_noted = self.restart_after_turn = False
            self.retry_noted = self.saw_busy = False
            client, sid = self.client, self.sid
            box = {}
            try:
                await self._identity()
                await self._model()
            except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                pass

            async def post():
                box["st"], _ = await client.request("POST", f"/api/session/{quote(sid)}/prompt", {"text": text})
                if box["st"] == 200:
                    return True
                return False if isinstance(box["st"], int) and 400 <= box["st"] < 500 else UNCERTAIN
            try:
                await self.deliver(post)
                st = box["st"]
            except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                if self.proc:
                    await self._kill(self.proc)
                continue
            if st == 404 and not attempt:                 # the conversation is gone: a fresh one
                if self.persist:
                    self.host.st.set_agent_session(self.kind, None)
                await self._kill(self.proc)
                continue
            if st != 200:
                self.fail_notice(f"OpenCode 没有接这条消息（HTTP {st}）。")
                return
            if self.persist and not self.cfg.get("_workflow_ceo"):
                from . import main_identity
                main_identity.audit(self.cfg, self.kind, self.host.st, self.sid)
            await self._wait_turn()
            if not self.provider_fail_noted:
                self.last_provider_fail = None
            if self.restart_after_turn and self.proc:
                self.restart_after_turn = False
                self.host.st.log("agent_restart", agent=self.kind, reason="provider_failure")
                await self._kill(self.proc)
                self.proc = self.client = None
                return
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                await self._catch_up()
            if self.persist and self.collect is None:
                with contextlib.suppress(Exception):
                    await self.context_meter()
            return

    async def _wait_turn(self) -> None:
        """Until session.execution.* ; every WATCH s also ask the session itself (time.idle after this turn began)."""
        while not self.turn_done.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.turn_done.wait(), WATCH)
            if self.turn_done.is_set():
                return
            if self.pending or self.questions:
                self.turn_progress = time.monotonic()
                continue
            if time.monotonic() - self.turn_progress >= TURN_IDLE:
                self.fail_notice("主机已连接，但 OpenCode 长时间没有回复或处理进度。请在电脑运行 `agentj doctor`；确认 `opencode auth login` 已登录你选用的模型服务。这条消息不会自动重发。")
                await self.interrupt_request(self.proc)
                self._turn_end()
                return
            if not self.client:
                continue
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                if await self._idle_since_turn():
                    await self._catch_up()
                    self._turn_end()

    async def interrupt_request(self, p) -> None:
        td = self.turn_done
        if td is None or td.is_set() or not self.client or not self.sid:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await self.client.request("POST", f"/api/session/{quote(self.sid)}/interrupt", None, timeout=3)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(td.wait(), 2)

    # ------------------------------------------------ models, meters, slash commands
    async def _providers(self) -> dict:
        """v1's shape ({providers: [{id, models: {id: {name, limit}}}], default, connected}) built from /api/model — only the
        providers that have a key (connected), so /model offers what can actually answer."""
        try:
            st, r = await self.client.request("GET", "/api/model")
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        if st != 200 or not isinstance(_data(r), list):
            return {}
        connected = await self._connected()
        prov: dict = {}
        for m in _data(r):
            if not isinstance(m, dict) or not isinstance(m.get("providerID"), str) or not isinstance(m.get("id"), str):
                continue
            if m.get("enabled") is False or (connected is not None and m["providerID"] not in connected):
                continue
            prov.setdefault(m["providerID"], {})[m["id"]] = {"name": m.get("name") or m["id"], "limit": m.get("limit")}
        return {"providers": [{"id": k, "name": k, "models": v} for k, v in prov.items()], "default": {},
                "connected": connected}

    async def _last_assistant(self) -> dict:
        """v1's info shape (role, tokens, providerID, modelID) of the newest assistant message."""
        try:
            msgs = await self._messages()
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        for m in msgs:                                   # newest first
            if isinstance(m, dict) and m.get("type") == "assistant" and isinstance(m.get("tokens"), dict):
                mod = m.get("model") if isinstance(m.get("model"), dict) else {}
                return {"role": "assistant", "tokens": m["tokens"], "providerID": mod.get("providerID"), "modelID": mod.get("id")}
        return {}

    @staticmethod
    def _ctx(info: dict):
        t = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        parts = [t.get("input"), t.get("output"), c.get("read"), c.get("write")]
        return sum(x for x in parts if isinstance(x, int)) if any(isinstance(x, int) for x in parts) else None

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        if not await self._ready():
            return Result("OpenCode 没起来，没有压缩。", "error")
        before = self._ctx(await self._last_assistant())
        self.quiet = True
        self.set_status("compacting")
        t0 = time.monotonic()
        self.turn_done, self.turn_proc = asyncio.Event(), self.proc
        self.turn_t0, self.turn_progress, self.saw_busy = time.time() * 1000, time.monotonic(), False
        try:
            st, _ = await self.client.request("POST", f"/api/session/{quote(self.sid)}/compact", {}, timeout=60)
            if st == 200:
                await asyncio.wait_for(self._wait_turn(), 600)
        except (OSError, HTTPError, asyncio.TimeoutError) as e:
            return Result(f"没有压缩：{type(e).__name__}", "error")
        finally:
            await asyncio.sleep(0.2)
            self.quiet = False
            self.set_status("working")
        if st != 200:
            return Result(f"没有压缩（HTTP {st}）。", "error")
        took = slash.secs((time.monotonic() - t0) * 1000)
        after = self._ctx(await self._last_assistant())
        if isinstance(before, int) and isinstance(after, int) and after != before:
            return Result(f"已压缩：{slash.tokens(before)} → {slash.tokens(after)} tokens{took}")
        return Result(f"已压缩{took}。")

    async def cmd_cost(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("本会话花费：$0（还没有对话）", "info")
        if not await self._ready():
            return Result("OpenCode 没起来：" + slash.NONE, "error")
        try:
            st, s = await self.client.request("GET", f"/api/session/{quote(self.sid)}")
        except (OSError, HTTPError, asyncio.TimeoutError):
            st, s = 0, None
        s = _data(s)
        if st != 200 or not isinstance(s, dict):
            return Result("本会话花费：" + slash.NONE, "error")
        t = s.get("tokens") if isinstance(s.get("tokens"), dict) else {}
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        return Result(f"本会话花费（OpenCode 报告）：{slash.money(s.get('cost'))}\ntoken：输入 {slash.tokens(t.get('input'))}"
                      f"（缓存读 {slash.tokens(c.get('read'))}）· 输出 {slash.tokens(t.get('output'))}"
                      f"（含推理 {slash.tokens(t.get('reasoning'))}）")

    async def cmd_status(self, arg: str) -> Result:
        ver = ""
        if self.proc is not None or await self._spawn():
            with contextlib.suppress(OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                st, h = await self.client.request("GET", "/api/info")
                if st == 200 and isinstance(h, dict) and isinstance(h.get("version"), str):
                    ver = clean_line(h["version"], 40)
        lines = [f"Agent：OpenCode {ver}".rstrip(), f"对话：{self.host.st.agent_session(self.kind) or '（新对话）'}",
                 f"模型：{self.cfg.get('model') or '默认'}", f"目录：{self.cfg['dir']}",
                 "审批：OpenCode 自己的权限规则；它要问的都问手机",
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("\n".join(lines))


def describe_error(err) -> str:
    """For logs / tests: the class of a v2 error, never its text."""
    name, data = v2_error(err)
    return provider_error_reason(name, data)

