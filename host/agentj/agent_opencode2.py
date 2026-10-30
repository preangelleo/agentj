"""OpenCode v2 adapter (P60): the same `opencode serve` child as v1 (agent_opencode.py) — same fence, same random
`OPENCODE_SERVER_PASSWORD`, same startup line — but v2's server speaks a different protocol. Two lines ship side by side
(as of 2026-10-17): `https://opencode.ai/install` and `npm i -g opencode-ai` install 1.x (since 1.19.2 the curl installer
refuses 2.x; 1.x never updates itself to 2.x), `https://opencode.ai/v2/install`, `@opencode/cli`, Homebrew core and mise
≥ 2026.10.9 install 2.x — so a Mac user's `brew upgrade` lands on v2. Before P60 Agent J refused v2 outright ("OpenCode v2
的 /api 协议尚未接入"). Measured on 2.0.23 (reports/qa/p60/), rules re-checked on 2.0.31 source (P357):

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
- permission rules are `{action, resource, effect}`; the shell tool's action is `shell` (v1: `bash`), a subagent's
  `subagent` (v1: `task`), write / patch ask as `edit` — renamed by `rules_to_v2` (P357, checked on 2.0.31 source);
- which providers have a key: `GET /api/integration` → `connections` (env / key / oauth), never a value;
- a sub-agent's session takes its parent's session rules (P426, opencode_subagents): its `permission.asked` / `form.created`
  carry the CHILD's sessionID (routed by `session.created` parentID, answered on the child's own route).

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
from .text import clean_line, strip_reasoning

IDENTITY_KEY = "agentj.identity"
# v2 keeps several keys per provider (「DeepSeek 2」): a second key that is saved but not active is never used (measured:
# `auth import` keeps the first one active; 401 until `auth switch`). A running v2 serve uses the newly active key at once.
V2_SWITCH = (" 同一服务商存了好几把 key 时（例如「DeepSeek 2」），在Agent J 主机运行 `opencode auth switch {provider}` 选中正确的那一把。"
             " / Several keys for one provider: pick the right one with `opencode auth switch {provider}`.")
AGENT_TRIES = 40
END = ("session.execution.succeeded", "session.execution.failed", "session.execution.interrupted")


def _data(r):
    return r.get("data") if isinstance(r, dict) else None


# v1 permission names → v2 action names (v2.0.31 `v1/config/migrate.ts` normalizeAction). v2 renames them only while it
# loads a config FILE; rules a client writes through the session API are taken as they are — before P357 our `task` deny
# reached v2 as a `task` rule that matches no v2 tool, so a subagent fell through to `*` ask and, once approved, ran on the
# general agent's own rules with no card for its shell / edits (a subagent session never sees its parent's rules).
# Every other name is kept as it is (v2 has the same `read`, `edit`, `glob`, `grep`, `skill`, `webfetch`, `websearch`,
# `question`, `external_directory`; v1-only `list` / `lsp` / `todo*` / `doom_loop` and anything unknown match no v2 tool,
# so keeping them neither drops a deny nor widens an allow — `list` must NOT become `read`: our `list * allow` would then
# come after `read *.env ask` and open .env). v2-only `execute` (Code Mode) is never asked itself: it runs JavaScript whose
# every tool call asks under that tool's own action; the tool is offered unless the last rule naming it is a whole deny,
# so in high-risk mode it follows our leading `* * ask` (on, inner calls carded) and in research mode `* * deny` (off).
V1_TO_V2 = {"bash": "shell", "task": "subagent", "write": "edit", "patch": "edit"}
V2_TO_V1 = {"shell": "bash", "subagent": "task"}


def rules_from_v2(rules) -> list[dict]:
    """v2 {action, resource, effect} → the v1 shape session_rules / evaluate know ({permission, pattern, action}); the shell
    tool is `bash` there, a subagent `task`."""
    out = []
    for r in rules if isinstance(rules, list) else []:
        if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("action", "resource", "effect")):
            out.append({"permission": V2_TO_V1.get(r["action"], r["action"]), "pattern": r["resource"],
                        "action": r["effect"]})
    return out


def rules_to_v2(rules) -> list[dict]:
    out = []
    for r in rules or []:
        perm = r["permission"]
        out.append({"action": V1_TO_V2.get(perm, perm), "resource": r["pattern"], "effect": r["action"]})
    return out


def v2_error(err) -> tuple[str, dict]:
    """A v2 structured error → (name, data) in the shape provider_error_reason reads (statusCode / message / responseBody)."""
    err = err if isinstance(err, dict) else {}
    resp = err.get("response") if isinstance(err.get("response"), dict) else {}
    data = {"statusCode": err.get("status"), "message": err.get("message") or "", "responseBody": resp.get("body") or ""}
    data.update({k: err[k] for k in ("providerID", "modelID", "resetAt", "reset_at") if k in err})
    name = str(err.get("type") or "")
    if name == "provider.no-route":            # "Model unavailable: <provider>/<model>" — the provider has no key / no model
        data["message"] = "model not found: " + str(data["message"])
    return name, data


def to_v1_request(p: dict) -> dict:
    """permission.asked (v2) → the v1 request shape to_tool knows."""
    action = str(p.get("action") or "?")
    res = [x for x in (p.get("resources") or []) if isinstance(x, str)]
    md = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
    sid = {"sessionID": p["sessionID"]} if isinstance(p.get("sessionID"), str) else {}
    if action == "shell":
        return {"id": p.get("id"), "permission": "bash", "patterns": res, **sid,
                "metadata": {**md, "command": md.get("command") if isinstance(md.get("command"), str) else "\n".join(res)}}
    return {"id": p.get("id"), "permission": V2_TO_V1.get(action, action), "patterns": res, "metadata": md, **sid}


def _agents_v1(ags) -> list:
    """GET /api/agent → the v1 shape opencode_subagents reads ({name = the v2 id, mode, permission})."""
    return [{"name": a.get("id") or a.get("name"), "mode": a.get("mode"), "permission": rules_from_v2(a.get("permissions"))}
            for a in ags or [] if isinstance(a, dict)]


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
        if ag is not None:
            self.agent_rules = self._sub_plan(_agents_v1(ags), ag.get("id") or ag.get("name"), self.agent_rules)
        if ag is None and (self.research or self.cfg.get("high_risk_warnings", False)):
            raise HTTPError("no primary agent")
        hi = self.research or self.cfg.get("high_risk_warnings", False)
        self.rules = rules_to_v2(session_rules(self.agent_rules, self.research, self.sub_ok)) if hi else []
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
                    c = _data(c) if isinstance(_data(c), (dict, list)) else c
                    from .provider_setup import remember
                    remember(self, c if st == 200 else None)
                    if st == 200 and isinstance(c, dict) and isinstance(c.get("provider"), dict):
                        out += [k for k in c["provider"] if isinstance(k, str) and k not in out]
                    elif st == 200 and isinstance(c, list):
                        # 2.1: config is a list of documents; custom providers have no integration connection.
                        # Project names only, never retain settings (which may contain inline keys).
                        for doc in c:
                            info = doc.get("info") if isinstance(doc, dict) else None
                            providers = info.get("providers") if isinstance(info, dict) else None
                            if isinstance(providers, dict):
                                out += [k for k in providers if isinstance(k, str) and k not in out]
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
                self.record_session(sid)
        st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
        if st != 200 or not isinstance(_data(s), dict):
            raise HTTPError("session not found")
        if self.rules and not ends_with_ours(rules_from_v2(_data(s).get("permissions")), rules_from_v2(self.rules)):
            raise HTTPError("rules not in place")      # never prompt a session whose last rules are not ours
        self.session_model = _data(s).get("model") if isinstance(_data(s).get("model"), dict) else None
        self.sid = sid
        self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()))

    # ------------------------------------------------ the event stream
    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        p = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        mine = p.get("sessionID") == self.sid and self.sid is not None
        kid = p.get("sessionID") in self.children           # P426: a sub-agent's session (parentID = ours)
        if mine:
            self.touch()                   # P219: progress on our session (stall watch)
        if mine and isinstance(t, str) and t.startswith("session."):
            self.turn_progress = time.monotonic()
        if t == "session.created":
            self._child_seen(p.get("sessionID"), p.get("parentID"), p.get("agent"), p.get("title"), True, p.get("permissions"))
        elif kid and t in ("session.step.failed", "session.execution.failed"):
            from .provider_setup import error_model, limit_note
            name, data = v2_error(p.get("error"))
            if provider_error_reason(name, data) in ("balance", "quota_window", "rate_limit"):
                self.host.agent_notice(limit_note(error_model(data), self.cfg.get("model") or "?", data, self.cfg.get("language", "en")))
            if t in END:
                self._child_done(p["sessionID"])
        elif kid and t in END:
            self._child_done(p["sessionID"])
        elif kid and t == "session.execution.started":
            self.children[p["sessionID"]].pop("done", None)
        if t == "permission.asked" and (mine or kid):
            self._on_perm(to_v1_request(p))
        elif t == "permission.asked" and isinstance(p.get("sessionID"), str) and self.sid:
            self._bg(self._adopt(p["sessionID"], lambda: self._on_perm(to_v1_request(p))))
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
        elif t == "session.compaction.failed" and mine:
            self._compaction_failed = True
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
            fsid = form.get("sessionID")
            if isinstance(form.get("id"), str) and form["id"] not in self.questions:
                if fsid in (self.sid, None) or fsid in self.children:
                    self._bg(self._form(form))
                elif isinstance(fsid, str) and self.sid:
                    self._bg(self._adopt(fsid, lambda: self._bg(self._form(form))))
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
        text = strip_reasoning(text)                 # P198: a model's </think> reasoning is not the reply
        if self.quiet or not text:
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
        from .provider_setup import error_model
        failed_model = error_model(data)
        if not self.cfg.get("model") and split_model(failed_model):
            m = split_model(failed_model)
            self.session_model = {"providerID": m["providerID"], "id": m["modelID"]}
            self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()))
        sel = split_model(failed_model or self.cur_model())
        pid = sel["providerID"] if sel else None
        reason = provider_error_reason(name, data, self.connected_at_start, pid)
        if failed_model and failed_model != self.cfg.get("model") and reason in ("balance", "quota_window", "rate_limit"):
            self.host.agent_notice(self.provider_note(reason, pid, PROVIDER_NOTES[reason], data, failed_model))
            return
        if self.provider_fail_noted:
            return
        if code == 401 and pid in ('ark-agent-plan', 'ark-coding-plan') and not self.cfg.get('_workflow_ceo'):
            from . import ark_plan_fix
            self._bg(ark_plan_fix.notify(self.host, failed_model or self.cfg.get('model')))
        self.host.st.log("agent_provider_fail", agent=self.kind, reason=reason, http_status=code)
        self.last_provider_fail = {"reason": reason, "http_status": code, "at": int(time.time())}
        if reason in RESTART_AFTER and self.owns_harness():
            self.restart_after_turn = True
        notes = PROVIDER_NOTES if self.owns_harness() else {**PROVIDER_NOTES, "login": PROVIDER_LOGIN_ATTACHED}
        notes = {**notes, "no_key": notes["no_key"].replace("{provider}", clean_line(pid or "该服务商", 40)),
                 "login": notes["login"] + V2_SWITCH.replace("{provider}", clean_line(pid or "<服务商>", 40))}
        self.provider_fail_noted = True
        if self._recover_model(reason, pid):
            return
        if code == 401 and pid in ('ark-agent-plan', 'ark-coding-plan') and not self.cfg.get('_workflow_ceo'):
            self.fail_notice(ark_plan_fix.checking_note(failed_model or self.cfg.get('model'), self.cfg.get('language','en'), data))
        else:
            self.fail_notice(self.provider_note(reason, pid, notes[reason], data, failed_model))
        if reason in ("model_missing", "login", "no_key"):
            self._offer_models()

    async def _form(self, form: dict) -> None:
        """form.created → one question card (choices only); anything else, a cancel / timeout → the form is cancelled and the
        model is told nobody chose (it asks in text)."""
        from .approvals import norm_questions
        fid = form["id"]
        gone = asyncio.get_running_loop().create_future()
        self.questions[fid] = gone
        client = self.client
        sid = form.get("sessionID") if form.get("sessionID") in self.children else self.sid   # P426: a sub-agent's own form
        answer = None
        try:
            raw, keys = form_questions(form)
            card = norm_questions(self._label(raw, sid)) if raw and not self.research else None
            with self._src_ctx(sid):
                if card is not None and self._req_src(sid) is not None and self._refused():
                    card = None                         # S3: a group member's sub-agent asks the owner nothing
                if card is not None:
                    outcome, picks = await self.host.question(card, gone, task=getattr(self.host, "task_label", None))
                    if outcome == "answer" and picks:
                        answer = {}
                        for (key, multi, values), q, pk in zip(keys, card, picks):
                            labels = [pk] if isinstance(pk, str) else [q["o"][n - 1]["l"] for n in pk]
                            vals = [pk] if isinstance(pk, str) else [values.get(lab, lab) for lab in labels]
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

    async def _reply_permission(self, rid: str, reply: str, msg: str, sid=None) -> None:
        body = {"decision": reply} if reply == "once" else {"decision": "reject", "message": msg}
        sid = sid if sid in self.children else self.sid        # P426: a sub-agent's request is answered on its session
        await self.client.request("POST", f"/api/session/{quote(sid)}/permission/{quote(rid)}/reply", body)

    # ------------------------------------------------ P426: sub-agents (child sessions)
    async def _sub_agents(self) -> list:
        st, ags = await self.client.request("GET", "/api/agent")
        return _agents_v1(_data(ags)) if st == 200 and isinstance(_data(ags), list) else []

    async def _session_info(self, sid: str) -> dict:
        st, s = await self.client.request("GET", f"/api/session/{quote(sid)}")
        return _data(s) if st == 200 and isinstance(_data(s), dict) else {}

    async def _child_stop(self, cid: str) -> None:
        await self.client.request("POST", f"/api/session/{quote(cid)}/interrupt", None, timeout=3)

    async def _children(self) -> list:
        st, r = await self.client.request("GET", f"/api/session?parentID={quote(self.sid)}&limit=50")
        return [k for k in _data(r) if isinstance(k, dict)] if st == 200 and isinstance(_data(r), list) else []

    async def _resync(self) -> None:
        owner = self._sync_owner()
        c = self.client
        if not c or not self.sid:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, reqs = await c.request("GET", f"/api/session/{quote(self.sid)}/permission")
            if not self._sync_current(owner):
                return
            reqs = _data(reqs)
            if st == 200 and isinstance(reqs, list):
                live = {r.get("id") for r in reqs if isinstance(r, dict)}
                for rid, f in list(self.pending.items()):
                    if rid not in live and not f.done():
                        f.set_result(True)
                for r in reqs:
                    if isinstance(r, dict):
                        self._on_perm(to_v1_request(r))
            for cid in list(self.children)[-8:]:            # P426: a sub-agent's request that went by while it was down
                st, reqs = await c.request("GET", f"/api/session/{quote(cid)}/permission")
                if not self._sync_current(owner):
                    return
                for r in _data(reqs) if st == 200 and isinstance(_data(reqs), list) else []:
                    if isinstance(r, dict):
                        self._on_perm(to_v1_request(r))
            await self._catch_up()
            if not self._sync_current(owner):
                return
            if self.turn_done and not self.turn_done.is_set():
                idle = await self._idle_since_turn()
                if not self._sync_current(owner):
                    return
                if idle:
                    self._turn_end()

    async def _messages(self) -> list:
        if not self.sid:
            return []
        st, r = await self.client.request("GET", f"/api/session/{quote(self.sid)}/message")
        return _data(r) if st == 200 and isinstance(_data(r), list) else []

    async def _catch_up(self) -> None:
        if not (self.client and self.sid and self.turn_done):
            return
        owner = self._sync_owner()
        messages = await self._messages()
        if not self._sync_current(owner):
            return
        for m in reversed(messages):                         # the list is newest first
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

    def _reminder_main(self) -> bool:
        return True                                   # shared v2 on an owner's own server: language only (shared_opencode2)

    async def _reminder(self) -> None:
        """P159: the hidden per-turn line (main_identity.turn_reminder) as a v2 synthetic input admitted without starting a
        run (`resume: false`, measured 2.0.27: stored as type "synthetic", the model reads it, the prompt that follows
        starts the turn). A v2 without the route only logs; the turn still goes."""
        if self.cfg.get("_workflow_ceo") or not self.persist:
            return
        from . import main_identity
        text = main_identity.turn_reminder(self.cfg, self._reminder_main())
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, _ = await self.client.request("POST", f"/api/session/{quote(self.sid)}/synthetic",
                                              {"text": text, "resume": False})
            if st != 200:
                self.host.st.log("agent_reminder_fail", agent=self.kind, status=st)

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
        self._note_turn()
        if await self._protocol():
            return await self.turn(text)
        await self._creds_check()
        for attempt in range(2):
            if not await self._ensure_started():
                return
            if not self.v2:
                return await self.turn(text)
            if not await self._session_ok():
                return
            await self._free_before_prompt()
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
                await self._reminder()
            except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                pass

            from .native_commands import opencode_request
            native = opencode_request(text, sid, self.cfg.get("model"), True)
            path, body = native or (f"/api/session/{quote(sid)}/prompt", {"text": text})

            async def post():
                box["st"], _ = await client.request("POST", path, body)
                if box["st"] == 200:
                    return True
                return False if isinstance(box["st"], int) and 400 <= box["st"] < 500 else UNCERTAIN
            try:
                await self.deliver(post)
                st = box["st"]
            except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                if self.proc:
                    await self._kill(self.proc)
                self._delivery_uncertain()
                return  # the native request may already have executed; never replay it
            if st == 404 and not attempt and not native:                 # the conversation is gone: a fresh one
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
                context_task = self.refresh_usage()
                if self.cfg.get('session_mode') != 'shared':
                    await context_task  # preserve the ordinary-session compaction reminder boundary
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
                self.fail_notice("OpenCode 长时间没有回复。这条消息不会自动重发；可在手机「模型和 API Key」检查或切换服务商后重试。" if self.cfg.get('language', 'en').startswith('zh') else "OpenCode has not responded for a long time. This message will not be resent automatically. Check or switch providers in Models & API keys on the phone, then retry.")
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
        connected providers plus explicit zero-price anonymous OpenCode routes."""
        try:
            st, r = await self.client.request("GET", "/api/model")
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        if st != 200 or not isinstance(_data(r), list):
            return {}
        connected = await self._connected()
        from .opencode_catalogue import free_models
        prov: dict = {}
        anonymous = []
        for m in _data(r):
            if not isinstance(m, dict) or not isinstance(m.get("providerID"), str) or not isinstance(m.get("id"), str):
                continue
            # 2.1's anonymous OpenCode free routes have no integration connection.
            anonymous_free = bool(free_models([m])) if m["providerID"] == "opencode" else False
            if m.get("enabled") is False or (connected is not None and m["providerID"] not in connected and not anonymous_free):
                continue
            if anonymous_free:
                anonymous.append(f"{m['providerID']}/{m['id']}")
            prov.setdefault(m["providerID"], {})[m["id"]] = {"name": m.get("name") or m["id"], "limit": m.get("limit")}
        return {"providers": [{"id": k, "name": k, "models": v} for k, v in prov.items()], "default": {},
                "connected": connected, "anonymous_models": anonymous}

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
        self._compaction_failed = False
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
        if self._compaction_failed:
            return Result("OpenCode 未完成压缩。" if self.cfg.get("language", "en").startswith("zh") else
                          "OpenCode did not complete compaction.", "error")
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
                      f"（含推理 {slash.tokens(t.get('reasoning'))}）" + await self._children_line(s.get("cost")))

    async def cmd_status(self, arg: str) -> Result:
        from .opencode_subagents import state_text
        ver = ""
        if await self._ensure_started():
            with contextlib.suppress(OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                st, h = await self.client.request("GET", "/api/info")
                if st == 200 and isinstance(h, dict) and isinstance(h.get("version"), str):
                    ver = clean_line(h["version"], 40)
        lines = [f"Agent：OpenCode {ver}".rstrip(), f"对话：{self.host.st.agent_session(self.kind) or '（新对话）'}",
                 f"模型：{self.cfg.get('model') or '默认'}", f"目录：{self.cfg['dir']}",
                 "审批：OpenCode 自己的权限规则；它要问的都问手机",
                 state_text(self.subagent_state()),
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("\n".join(lines))


def describe_error(err) -> str:
    """For logs / tests: the class of a v2 error, never its text."""
    name, data = v2_error(err)
    return provider_error_reason(name, data)



# ------------------------------------------------ P395 (#144): the switch gate's probe on 2.x
# Before: every OpenCode probe ran the bots' v1 one-shot `opencode run --pure --agent agentj-bot`; 2.x refuses `--pure`
# ("Unrecognized flag") and never sees an agent defined in OPENCODE_CONFIG_CONTENT, so no 2.x owner could switch to OpenCode.
# Now 2.x is probed the way its session runs: our own `opencode serve`, a session with Agent J's read-only rules in v2's
# shape, one greeting, the session's outcome (measured on 2.0.24: `outcome` + `time.idle` on the session, the reply in its
# messages; a provider error on `session.execution.failed` and — when a step ran — on the assistant message). The probe
# session is deleted (DELETE → 204, measured).
PROBE_POLL = 0.5
LINE_MAX = 200


def probe_line(err, secrets_in=()) -> str | None:
    """OpenCode's own error as one line the owner may see: type, status, message — credentials redacted, ≤ 200 chars."""
    if not isinstance(err, dict):
        return None
    import re
    from .privacy import redact
    from .text import clean_line
    msg = str(err.get("message") or "")
    for s in secrets_in:
        if isinstance(s, str) and len(s) >= 8:
            msg = msg.replace(s, "…")
    msg = re.sub(r"[A-Za-z0-9_\-]{32,}", "…", redact(msg))
    status = err.get("status") if isinstance(err.get("status"), int) else None
    line = clean_line(" ".join(x for x in (str(err.get("type") or ""), str(status or ""), msg) if x), LINE_MAX)
    return line or None


def probe(exe: str, model: str | None, env: dict, timeout: float, *, waits=(), sleep=time.sleep,
          clock=time.monotonic, prompt=None, diagnostic=False) -> str:
    """One greeting through a 2.x `opencode serve` in an empty scratch folder → the reply text; raises
    opencode_free.ProbeError(code, line) — `line` is OpenCode's own error (probe_line) or None. A free-tier refusal / 429 is
    retried after each of `waits` while `timeout + sum(waits)` allows."""
    import base64
    import json
    import secrets
    import threading
    import urllib.error
    import urllib.request
    from . import scratch
    from .agent_opencode import _rule, opencode_env
    from .opencode_free import PROMPT, START_WAIT, ProbeError, _env, _port, _stop, serve
    deadline = clock() + timeout + sum(waits)
    password = secrets.token_urlsafe(32)
    port = _port()
    root = scratch.make("agentj-oc2-probe-")      # P542: private cache, removed in the finally below
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 127.0.0.1 never through a proxy
    auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
    events: list = []
    keys = [v for k, v in env.items() if k.endswith(("_KEY", "_TOKEN")) and isinstance(v, str)]
    proc = None

    def req(method, path, body=None):
        return urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                      data=None if body is None else json.dumps(body).encode(),
                                      headers={"Authorization": auth, "Content-Type": "application/json"})

    def call(method, path, body=None, grace=0.0):
        left = deadline + grace - clock()
        if left <= 0:
            raise ProbeError("native_timeout", None)
        try:
            with opener.open(req(method, path, body), timeout=min(left, timeout)) as r:
                raw = r.read(4 * 1024 * 1024)
                return r.status, _data(json.loads(raw)) if raw.strip() else None
        except urllib.error.HTTPError as e:
            return e.code, None
        except (TimeoutError, OSError) as e:
            raise ProbeError("native_timeout" if "timed out" in str(e) or isinstance(e, TimeoutError) else "native_failed",
                             None) from None
        except ValueError:
            raise ProbeError("native_failed", None) from None

    def listen():                     # the event stream: only where a provider error is said for a failure with no step
        try:
            with opener.open(req("GET", "/api/event"), timeout=max(1.0, deadline - clock())) as r:
                for raw in r:
                    if raw.startswith(b"data:"):
                        try:
                            events.append(json.loads(raw[5:]))
                        except ValueError:
                            pass
        except Exception:  # noqa: BLE001 — ended with the server, or timed out: polling decides alone
            pass

    def ended(sid, t0, seen):
        st, s = call("GET", f"/api/session/{quote(sid)}")
        idle = ((s or {}).get("time") or {}).get("idle") if isinstance(s, dict) else None
        if isinstance(idle, (int, float)) and idle >= t0 and s.get("outcome"):
            return s["outcome"]
        for e in events[seen:]:
            d = e.get("data") if isinstance(e, dict) and isinstance(e.get("data"), dict) else {}
            if d.get("sessionID") == sid and e.get("type") in END:
                return e["type"].rsplit(".", 1)[1]
        return None

    try:
        proc = serve(exe, opencode_env(_env(env), password), root, port, min(deadline, clock() + START_WAIT), clock)
        threading.Thread(target=listen, daemon=True).start()
        up = clock() + 3
        while not events and clock() < up:             # `server.connected` first, so no failure event is missed
            sleep(0.05)
        rules = rules_to_v2(session_rules([], research=True) + [_rule("webfetch", "*", "deny")])
        if diagnostic:
            rules = rules_to_v2([_rule("*", "*", "deny"), _rule("read", root + "/*", "allow")])
        body = {"title": "Agent J probe", "location": {"directory": root}, "permissions": rules}
        m = split_model(model) if model else None
        if m:
            body["model"] = {"id": m["modelID"], "providerID": m["providerID"]}
        st, ses = call("POST", "/api/session", body)
        if st != 200 or not isinstance(ses, dict) or not isinstance(ses.get("id"), str):
            raise ProbeError("native_failed", None)
        sid = ses["id"]
        try:
            for wait in (*waits, None):
                t0 = int(time.time() * 1000) - 1000
                seen = len(events)
                st, _ = call("POST", f"/api/session/{quote(sid)}/prompt", {"text": prompt if prompt is not None else PROMPT})
                if st != 200:
                    raise ProbeError("native_failed", None)
                outcome = None
                while outcome is None:
                    if clock() >= deadline:
                        raise ProbeError("native_timeout", None)
                    sleep(PROBE_POLL)
                    outcome = ended(sid, t0, seen)
                st, msgs = call("GET", f"/api/session/{quote(sid)}/message")
                last = next((x for x in (msgs if isinstance(msgs, list) else []) if isinstance(x, dict)
                             and x.get("type") == "assistant" and ((x.get("time") or {}).get("created") or 0) >= t0), {})
                text = "\n".join(c.get("text") or "" for c in last.get("content") or []
                                 if isinstance(c, dict) and c.get("type") == "text").strip()
                if outcome == "succeeded" and text:
                    return text
                err = last.get("error") if isinstance(last.get("error"), dict) else next(
                    (e["data"]["error"] for e in events[seen:] if isinstance(e, dict) and isinstance(e.get("data"), dict)
                     and e["data"].get("sessionID") == sid and isinstance(e["data"].get("error"), dict)), None)
                if outcome == "succeeded" and not err:
                    raise ProbeError("empty_probe_reply", None)
                blob = json.dumps(err or {})
                code = ("free_tier" if "FreeTierError" in blob or (err or {}).get("status") == 429 else
                        "provider_unavailable" if (err or {}).get("type") == "provider.no-route" else "native_failed")
                if code != "free_tier" or wait is None or clock() + wait >= deadline:
                    raise ProbeError(code, probe_line(err, keys))
                sleep(wait)
            raise ProbeError("free_tier", None)
        finally:
            try:
                call("DELETE", f"/api/session/{quote(sid)}", grace=5.0)     # no probe session left in the owner's list
            except ProbeError:
                pass
    finally:
        _stop(proc)
        scratch.remove(root)
