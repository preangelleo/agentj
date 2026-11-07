"""0.26.9 #464 / #462: a harness's login failure in a reply gets a 「重新登录」 button, and the button signs the MAIN session in.

detect(): only a harness's own error line (short reply, whole line, catalogued wording; balance / rate limit never) → the page
`login: {agent, why}`. Login.on_phone(): `auth_rescue {action: login|code|cancel}` signed by a paired device (controls.check)
→ the official CLI in a private PTY with the main session's own HOME / CLAUDE_CONFIG_DIR (claude_live.where) → the link to that
phone only → its one-time code back (never logged, never stored) → proven in the main session (claude_live.deliver + one
tool-free call in its exact environment; Codex: the main agent's own app-server `account/read`). ADR-0269-login.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import tempfile
import threading
import time

from . import auth_rescue as ar

AGENTS = ("claude", "codex", "opencode")
WHYS = ("not_logged_in", "org_disabled", "expired", "device")
WAIT = 600                       # the whole flow: link → code → success, 10 minutes
CODE_RE = re.compile(r"[A-Za-z0-9._~#+/=\-]{8,512}")
SHORT, LINES = 600, 4            # a harness error reply is short; a longer reply is the Agent talking about one
PENDING_S = 3.0                  # an adapter failure signal → the very next notice page carries the button
_F = re.I
_LEAD = r"(?:[⎿●•>*\-\s]*)(?:(?:api\s+)?error:?\s*(?:\d{3}\s*)?)?"
PATTERNS = {
    "claude": (
        ("org_disabled", re.compile(_LEAD + r"(?:\{.{0,160}?)?your organization has disabled claude subscription access\b.*", _F)),
        ("not_logged_in", re.compile(_LEAD + r"not logged in\s*[·•∙|:\-–—]?\s*please run /login\.?", _F)),
        ("not_logged_in", re.compile(_LEAD + r"please run /login\.?", _F)),
        ("expired", re.compile(_LEAD + r"(?:login expired|oauth token has expired)\b.*", _F)),
        ("expired", re.compile(_LEAD + r"invalid api key\s*[·•∙]\s*(?:please run /login|fix external api key)\.?", _F)),
        ("expired", re.compile(r"(?:api\s+)?error:?\s*401\b.*(?:authentication_error|oauth token|invalid (?:x-api-key|bearer token)|revoked).*", _F)),
    ),
    "codex": (
        ("device", re.compile(_LEAD + r".{0,40}device code (?:login|authori[sz]ation|auth)\b.{0,40}\b(?:not enabled|disabled)\b.*", _F)),
        ("expired", re.compile(r"(?:[⎿●•>*\-\s]*)(?:(?:stream\s+)?error:?\s*)?(?:unexpected status\s+|exceeded retry limit, last status:\s*)401 unauthorized\b.*", _F)),
        ("expired", re.compile(_LEAD + r"your (?:access|refresh) token .{0,80}(?:expired|revoked|already (?:been )?used|could not be refreshed).*", _F)),
        ("not_logged_in", re.compile(_LEAD + r"not logged in\b.{0,60}(?:codex login|log in|sign in).*", _F)),
        ("not_logged_in", re.compile(_LEAD + r"(?:please (?:log|sign) in(?: again)?|you(?:'ve| have) been (?:logged|signed) out)\b.*", _F)),
    ),
    "opencode": (
        ("not_logged_in", re.compile(_LEAD + r"(?:provider)?autherror\b.*", _F)),
        ("not_logged_in", re.compile(_LEAD + r"no (?:api key|credentials) (?:found|configured) for (?:provider\s+)?\S+.*", _F)),
        ("not_logged_in", re.compile(_LEAD + r".{0,40}\bopencode auth login\b.*", _F)),
    ),
}
# item 3: the `/login` slash text as an instruction ("run /login", 「运行 /login」) in a short harness reply
SLASH_RE = re.compile(r"(?:\brun|\btype|\buse|运行|输入|执行|使用|用)\s*`?/login`?(?![\w/.\-])", _F)
SCREEN_IDS = ("login_expired", "login_method", "login")     # auth_rescue.BLOCKERS: the harness drew its own sign-in screen


def detect(kind, text) -> dict | None:
    """→ {"agent", "why"} when `text` is the harness's own login failure, else None. Cheap: called on every reply text."""
    if kind not in AGENTS or not isinstance(text, str) or not text.strip() or len(text) > SHORT or "```" in text:
        return None
    if ar.classify(kind, text) in ("balance", "rate_limit"):     # P258 order: a no-credit / 429 reply never starts a re-login
        return None
    lines = [ln.strip() for ln in ar.plain(text).splitlines() if ln.strip()]
    if not lines or len(lines) > LINES:
        return None
    for ln in lines:
        for why, rx in PATTERNS[kind]:
            if rx.fullmatch(ln):
                return {"agent": kind, "why": why}
    if re.search(r"please run /login", text, re.I):   # the harness line quoted inside the Agent's own sentence
        return None
    sq = ar.squash(text)
    seen = {n for h, bid, needles, *_ in ar.BLOCKERS if h == kind and bid in SCREEN_IDS for n in needles if n in sq}
    if len(seen) >= 2 and len(text) <= 300:        # the harness drew its own sign-in screen (two of its lines, not one word)
        return {"agent": kind, "why": "expired" if "expired" in sq else "not_logged_in"}
    if SLASH_RE.search(text):
        return {"agent": kind, "why": "not_logged_in"}
    return None


def mark(host, tid, hit) -> None:
    """Put `login` on that history page (the page shows 「重新登录」 under it)."""
    if hit and type(tid) is int:
        with contextlib.suppress(Exception):
            host.hist_update(tid, login=dict(hit))
            host.st.log("login_hint", agent=hit["agent"], reason=hit["why"])


def on_text(host, tid, text) -> None:
    a = getattr(host, "agent", None)
    mark(host, tid, detect(getattr(a, "kind", None), text))


def take_pending(host) -> dict | None:
    r = getattr(host, "auth_rescue", None)
    p = getattr(r, "pending_login", None)
    if not p:
        return None
    r.pending_login = None
    return p[0] if time.monotonic() - p[1] <= PENDING_S else None


# ------------------------------------------------------------------ proof in the main session
def main_reply(env: dict | None, cwd) -> str | None:
    """One tool-free `claude -p` (the bots' no-tool command) in EXACTLY the main session's auth context (claude_live.session_env:
    auth names + HOME / CLAUDE_CONFIG_DIR), nothing of serve's own credentials. → None when it answered, else a reason word.
    w2-sec M-5: it runs in its own scratch folder (as auth_rescue.health_check), never `cwd` — the project folder is
    Agent-writable and its `.claude/settings.json` (apiKeyHelper, env, awsAuthRefresh) is not proven ignored by `-p`.
    `cwd` is kept in the signature for callers and ignored."""
    del cwd
    from types import SimpleNamespace
    from . import claude_live
    from .bots import native
    exe = ar.binary("claude")
    if not exe or env is None:
        return "unreadable"
    run = {k: v for k, v in os.environ.items() if k not in claude_live.AUTH and k not in ar.CLAUDE_STRIP
           and k != "CLAUDE_CONFIG_DIR" and not k.startswith(ar._HEALTH_ENV_DROP)}
    run.update(env)
    with tempfile.TemporaryDirectory(prefix="agentj-0269-") as d:
        (open(os.path.join(d, "system.md"), "w")).write("Reply to the supplied greeting only. No tools are available.")
        try:
            cmd = native.command(SimpleNamespace(name="claude", model=None), __import__("pathlib").Path(d), run, selected=exe)
            p = subprocess.run(cmd, input="Hello. Reply with a short greeting only.", capture_output=True, text=True,
                               env=run, cwd=d, timeout=ar.HEALTH_TIMEOUT, start_new_session=True)
        except (OSError, subprocess.SubprocessError, Exception):  # noqa: BLE001
            return "status"
    out, err = (p.stdout or "")[-200_000:], (p.stderr or "")[-20_000:]
    ok = False
    if p.returncode == 0:
        with contextlib.suppress(Exception):
            reply, _ = native.parse("claude", out)
            ok = isinstance(reply, str) and bool(reply.strip())
    if ok:
        return None
    hit = detect("claude", _result_text(out) or err.strip()[-300:])
    return hit["why"] if hit else (ar.classify("claude", out + "\n" + err) or "status")


def _result_text(out: str) -> str:
    import json
    with contextlib.suppress(ValueError, TypeError):
        d = json.loads(out)
        if isinstance(d, dict) and isinstance(d.get("result"), str):
            return d["result"][:300]
    return ""


async def codex_main(host) -> str | None:
    """The Codex main session itself: an owned app-server restarts now (it reads auth.json at start) and the main agent's own
    connection answers `account/read`; a ChatGPT account also `account/rateLimits/read` (an authenticated backend call)."""
    a = getattr(host, "agent", None)
    if a is None or getattr(a, "kind", None) != "codex":
        return "not_main"
    try:
        if a.owns_harness():
            await a._end_proc()
            a.halting = False
        if getattr(a, "proc", None) is None and not await a._spawn():
            return "unreadable"
        acct = await a.call("account/read", {"refreshToken": True}, 20)
        account = acct.get("account") if isinstance(acct, dict) else None
        if not isinstance(account, dict):
            return "not_logged_in"
        if account.get("type") == "chatgpt":
            await a.call("account/rateLimits/read", {}, 20)
    except Exception as e:  # noqa: BLE001 — RPCError / ConnectionError / timeout: say which kind, never the text
        return "not_logged_in" if ar.classify("codex", str(e)) == "login" else "status"
    return None


# ------------------------------------------------------------------ the phone flow
def _plan_claude(host) -> dict:
    """Where the main session reads its login (ADR-462 precedence: env token > stored /login)."""
    from . import claude_live
    k, a = claude_live.where(host)
    if k == "none":
        return {"why": "not_main"}
    env = claude_live.session_env(host, k, a)
    if env is None:
        return {"why": "unreadable"}
    bad = [n for n in claude_live.OUTRANK if env.get(n)]
    if bad:
        return {"why": "conflict", "names": bad}
    # merge ruling 2: a config dir other than serve's only when it is provably the main session's own (never a path
    # from an Agent / a reply), a safe dir of ours outside the Agent-writable workspace — else nothing is written anywhere
    cwd = (getattr(a, "cfg", None) or {}).get("dir") or (getattr(host, "agent_cfg", None) or {}).get("dir")
    real = claude_live.context(env, cwd, claude_live.proven(host, k, a))
    if real is None:
        return {"why": "context", "where": k}
    ctx = real or {n: env[n] for n in ("HOME", "CLAUDE_CONFIG_DIR") if env.get(n)}     # the checked real paths
    if env.get(claude_live.TOKEN) or claude_live.expected()["method"] == "token":
        # a stored login would be masked by the token in that process: mint a new token instead (same link + code UX)
        return {"route": "setup_token", "where": k, "ctx": ctx, "masked": "env_token"}
    return {"route": "login", "where": k, "ctx": ctx}


class Login:
    """One chat-started login at a time (also blocks Settings' rescue: Rescue.flow is set, without the link)."""

    def __init__(self, rescue):
        self.r = rescue
        self.active: dict | None = None

    @property
    def h(self):
        return self.r.host

    def _say(self, key, **kw) -> str:
        from . import humane
        return humane.say(key, self.r.lang(), getattr(self.h, "preferences", None), **kw)

    async def _send(self, device, r, view) -> None:
        send = getattr(self.h, "_send_device", None)
        if send:
            await send(device, {"t": "auth_rescue", "r": r, "view": view})

    async def _done(self, device, r, ok: bool, why: str | None = None, text: str | None = None, **extra) -> None:
        v = {"step": "done", "ok": ok, **({"why": why} if why else {}), **extra}
        if text:
            v["text"] = text
        await self._send(device, r, v)

    @staticmethod
    def target(obj) -> dict | None:
        a = obj.get("action")
        if a == "login":
            g = obj.get("agent")
            return {"action": "login", "agent": g} if g in AGENTS else None
        if a == "code":
            c = obj.get("code")
            return {"action": "code", "code": c} if isinstance(c, str) and len(c) <= 600 else None
        if a == "cancel":
            return {"action": "cancel"}
        return None

    async def on_phone(self, s, obj: dict) -> None:
        from . import controls, wire
        h, r = self.h, obj.get("r")
        if not wire.is_rid(r):
            return
        target = self.target(obj)
        act = target["action"] if target else "shape"
        safe = {k: v for k, v in (target or {}).items() if k != "code"}          # the code never reaches a log line
        why = "shape" if target is None else controls.check(h.st, h.nonces, h.channel, s.device, obj, "auth_rescue", target)
        if not why and act in ("code", "cancel") and not (self.active and self.active["device"] == s.device):
            why = "not_requester" if self.active else "no_flow"
        audit = {"action": "auth_rescue", "device": s.device, "extra": {"step": act}}
        if why:
            h.st.log("control_refused", device=s.device, action="auth_rescue", reason=why)
            controls.log(h.st, result="refused:" + why, obj=safe if act == "login" else None, **audit)
            return await self._done(s.device, r, False, why)
        controls.log(h.st, result="ok", obj=safe if act == "login" else None, n=obj.get("n"), ts=obj.get("ts"),
                     sig=obj.get("sig") if act != "code" else None, **audit)
        if act == "cancel":
            self.active["cancel"].set()
            return await self._send(s.device, r, {"step": "cancelling"})
        if act == "code":
            code = target["code"].strip()
            fut = self.active.get("code")
            if fut is None or fut.done():
                return await self._done(s.device, r, False, "used")
            if not CODE_RE.fullmatch(code):
                return await self._send(s.device, r, {"step": "url", "again": "bad_code", **self.active["shown"]})
            fut.set_result(code)
            return await self._send(s.device, r, {"step": "verifying"})
        await self._start(s, r, target["agent"])

    async def _start(self, s, r, agent, bound_live=None) -> bool:
        from . import controls, provenance
        h, rescue = self.h, self.r
        refuse = ("estop" if controls.estop_state(h.st).get("on") else
                  "unsupported" if agent == "opencode" else
                  "not_main" if rescue.current() != agent else
                  "busy" if self.active or (rescue.flow and rescue.flow.get("stage") not in ("done", "failed")) else None)
        if bound_live is not None and not bound_live():
            refuse = "cancelled"
        if refuse:
            h.st.log("auth_login", device=s.device, agent=agent, result="refused", reason=refuse)
            return await self._done(s.device, r, False, refuse, self._say("login." + refuse) if refuse in ("estop", "busy", "not_main") else None)
        self.active = {"device": s.device, "agent": agent, "r": r, "cancel": threading.Event(), "code": None, "shown": {},
                       "deadline": time.monotonic() + WAIT, "bound_live": bound_live}
        try:
            with provenance.paired_request(lambda: h._paired_live(s)):
                ok = await (rescue._confirm(agent, remote=s) if bound_live is not None else rescue._confirm(agent))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok or (bound_live is not None and not bound_live()):
            self.active = None
            h.st.log("auth_login", device=s.device, agent=agent, result="refused", reason="passkey")
            return await self._done(s.device, r, False, "passkey")
        rescue.cancel.clear()
        rescue.flow = {"kind": agent, "route": "chat_login", "stage": "starting"}       # no link / code: other phones see a busy row
        rescue.broadcast()
        h.st.log("auth_login", device=s.device, agent=agent, result="start")
        try:
            await self._send(s.device, r, {"step": "starting", "agent": agent})
        except BaseException:
            self.active = None
            rescue._end(agent, "failed", "cancelled")
            raise
        if bound_live is not None and not bound_live():
            self.active = None
            rescue._end(agent, "failed", "cancelled")
            return False
        bg = getattr(h, "_bg", None)
        coro = self._run()
        (bg(coro, "auth_login") if bg else asyncio.ensure_future(coro))
        return True

    def _stop_now(self) -> bool:
        from . import controls
        a = self.active
        return bool(a and ((a.get("bound_live") is not None and not a["bound_live"]()) or a["cancel"].is_set() or self.r.cancel.is_set() or controls.estop_state(self.h.st).get("on")
                           or time.monotonic() > a["deadline"]))

    def _stop_why(self) -> str:
        from . import controls
        if controls.estop_state(self.h.st).get("on"):
            return "estop"
        return "timeout" if time.monotonic() > self.active["deadline"] else "cancelled"

    async def _run(self) -> None:
        a = self.active
        res = {"ok": False, "why": "failed"}
        try:
            res = await (self._claude() if a["agent"] == "claude" else self._codex())
        except Exception:  # noqa: BLE001 — a bug here must still end the flow (and never print a code)
            res = {"ok": False, "why": "failed"}
        finally:
            agent, device, r = a["agent"], a["device"], a["r"]
            self.active = None
            ok = bool(res.get("ok"))
            why = res.get("why")
            text = res.get("text") or (self._say("login.ok") if ok else
                                       self._say("login.failed", why=self.r.why(why, h=ar.LABEL[agent], n=", ".join(res.get("names") or ()))))
            if ok and res.get("pending_binding"):
                self.r.flow["account"] = res["account_name"]
                self.r._end(agent, "added", None)
                self.h.agent_notice(text)
            elif ok:
                self.r.clear(agent)
                self.r._health_mark(agent, {"ok": True})
                self.r._end(agent, "recovered", None)
                self.h.agent_notice(text)
            else:
                self.r._end(agent, "partial" if why == "main_session" else "failed", why)
            self.h.st.log("auth_login", device=device, agent=agent, result="ok" if ok else "failed", reason=why)
            await self._done(device, r, ok, None if ok else why, text, **{k: res[k] for k in ("live", "route") if res.get(k)})

    async def _wait_code(self) -> str | None:
        fut = asyncio.get_running_loop().create_future()
        self.active["code"] = fut
        while not fut.done():
            if self._stop_now():
                fut.cancel()
                return None
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(fut), .5)
        return fut.result()

    async def _claude(self) -> dict:
        from . import claude_live
        a, h = self.active, self.h
        plan = await asyncio.to_thread(_plan_claude, h)
        if plan.get("why") == "context":
            return {"ok": False, **plan, "text": claude_live.text(self.r.lang(), "refused")}
        if plan.get("why"):
            return {"ok": False, **plan}
        flow = ar.ClaudeFlow(plan["route"], cwd=self.r.workdir(), ctx=plan["ctx"])
        try:
            try:
                url = await asyncio.to_thread(flow.start)
            except ar.RescueError as e:
                return {"ok": False, "why": e.args[0]}
            a["shown"] = {"agent": "claude", "url": url, "route": plan["route"], "expires_at": int(time.time() + a["deadline"] - time.monotonic()),
                          **({"masked": plan["masked"], "note": self._say("login.masked")} if plan.get("masked") else {})}
            self.r.flow.update(stage="url")
            self.r.broadcast()
            await self._send(a["device"], a["r"], {"step": "url", **a["shown"]})
            code = await self._wait_code()
            if code is None:
                return {"ok": False, "why": self._stop_why()}
            self.r.flow.update(stage="verifying")
            self.r.broadcast()
            try:
                tok = await asyncio.to_thread(flow.submit, code)
            except ar.RescueError as e:
                return {"ok": False, "why": e.args[0]}
            finally:
                code = None
        finally:
            await asyncio.to_thread(flow.close)
        if plan["route"] == "setup_token":
            outcome: dict = {}
            got = await asyncio.to_thread(self.r._store_token, tok, outcome)
            if got.get("result") != "saved":
                return {"ok": False, "why": outcome.get("why") or "verify"}
        if self._stop_now():
            return {"ok": False, "why": self._stop_why()}
        return await self._prove_claude(plan)

    async def _prove_claude(self, plan) -> dict:
        from . import claude_live
        h, lang = self.h, self.r.lang()
        live = await claude_live.deliver(h, "chat_login", wait=True)
        if live.get("verified") is not True:
            owner = live.get("restart") == "owner_session"
            msg = claude_live.text(lang, "owner" if owner else "bad", live.get("live"))
            if owner:
                getattr(h, "agent_recovery_notice", h.agent_notice)(msg)          # the signed one-tap 「重开 AI 会话」 card
            return {"ok": False, "why": "main_session", "live": live.get("live") or "unknown", "route": plan["route"],
                    "text": self._say("login.main", why=msg)}
        k, ag = claude_live.where(h)
        env = claude_live.session_env(h, k, ag)
        cwd = (getattr(ag, "cfg", None) or {}).get("dir") or self.r.workdir()
        bad = await asyncio.to_thread(main_reply, env, cwd)
        if bad is not None:
            return {"ok": False, "why": "main_session", "live": bad, "route": plan["route"],
                    "text": self._say("login.main", why=self.r.why("probe") + f" ({bad})")}
        return {"ok": True, "route": plan["route"]}

    async def _codex(self) -> dict:
        a = self.active
        if a.get("bound_live") is not None:
            return await self.r._codex(bound_live=a["bound_live"])
        flow = ar.CodexFlow()
        try:
            url, code, ttl = await asyncio.to_thread(flow.start)
        except ar.RescueError as e:
            return {"ok": False, "why": "device_disabled" if e.args[0] == "device_disabled" else e.args[0]}
        left = max(1, int(a["deadline"] - time.monotonic()))
        flow.expires = max(1, min(flow.expires, left - 30))                    # the 10-minute cap includes the poll
        a["shown"] = {"agent": "codex", "url": url, "code": code, "expires_at": int(time.time()) + min(ttl, left)}
        self.r.flow.update(stage="device")
        self.r.broadcast()
        await self._send(a["device"], a["r"], {"step": "url", **a["shown"]})
        stop = threading.Event()

        async def watch():
            while not stop.is_set():
                if self._stop_now():
                    stop.set()
                await asyncio.sleep(.5)
        w = asyncio.ensure_future(watch())
        try:
            res = await asyncio.to_thread(flow.wait, stop)
        finally:
            stop.set()
            w.cancel()
        if res == "cancelled":
            return {"ok": False, "why": self._stop_why()}
        if res != "ok":
            return {"ok": False, "why": res}
        self.r.flow.update(stage="verifying")
        self.r.broadcast()
        await self._send(a["device"], a["r"], {"step": "verifying"})
        hc = await asyncio.to_thread(ar.health_check, "codex")
        if not hc["ok"]:
            await asyncio.to_thread(flow.restore)
            return {"ok": False, "why": "quota" if hc.get("cls") in ("balance", "rate_limit") else "verify_codex"}
        flow.backup = None
        bad = await codex_main(self.h)
        if bad is not None:
            return {"ok": False, "why": "main_session", "live": bad,
                    "text": self._say("login.main", why=self.r.why("main_session"))}
        return {"ok": True, "route": "device"}

    async def _codex_slot(self) -> dict:
        """Isolated device login: guarded current-account renewal or new registration; no replay."""
        from . import accounts
        a, root = self.active, self.h.st.root
        expected = accounts.load(root)["harness"]["codex"]
        expected_active, expected_switched = expected.get("active"), expected.get("switched")
        iid, flow, registered = None, None, False
        closed = False
        def live():
            return not closed and self.active is a and not self._stop_now()
        async def worker(fn, *args):
            # Cancellation waits for this owned operation to observe its stop flag
            # before cleanup. A to_thread cancellation alone cannot stop its writer.
            task = asyncio.create_task(asyncio.to_thread(fn, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                a["cancel"].set()
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
                raise
        try:
            if not live():
                return {"ok": False, "why": "cancelled"}
            def allocate():
                nonlocal iid
                iid, slot = accounts.new_codex_slot(root)
                return iid, slot
            iid, slot = await worker(allocate)
            if not live():
                return {"ok": False, "why": "cancelled"}
            flow = ar.CodexFlow(home=slot, bound_live=live)
            url, code, ttl = await worker(flow.start)
            if not live():
                return {"ok": False, "why": "cancelled"}
            left = max(1, int(a["deadline"] - time.monotonic()))
            flow.expires = min(flow.expires, max(1, left - 30))
            a["shown"] = {"agent": "codex", "url": url, "code": code,
                          "expires_at": int(time.time()) + min(ttl, left)}
            # Also populate the existing Settings AuthRescue device card. The
            # chat login sheet ignores unsolicited steps while idle; a push alone
            # would otherwise hide the code for quota-card and text entry points.
            self.r.flow.update(stage="device", route="device", url=url, code=code,
                               expires_at=a["shown"]["expires_at"], round=1, rounds=1)
            self.r.broadcast()
            self.h.agent_notice("请在手机「设置 → AI 登录救援」查看本次设备码；登录完成后明确选择账号，再发新消息继续。")
            await self._send(a["device"], a["r"], {"step": "url", **a["shown"]})
            if not live():
                return {"ok": False, "why": "cancelled"}
            res = await worker(flow.wait, a["cancel"])
            if not live() or res != "ok":
                return {"ok": False, "why": res if live() else "cancelled"}
            self.r.flow.update(stage="verifying")
            self.r.broadcast()
            await self._send(a["device"], a["r"], {"step": "verifying"})
            if not live():
                return {"ok": False, "why": "cancelled"}
            hc = await worker(ar.health_check, "codex", ar.HEALTH_TIMEOUT, {"CODEX_HOME": str(slot)})
            if not live() or not hc.get("ok"):
                return {"ok": False, "why": "cancelled" if not live() else "verify_codex"}
            verify_account = getattr(a["bound_live"], "verify_account", None)
            if verify_account is not None:
                verified = await verify_account()
                if not live() or not verified:
                    return {"ok": False, "why": "cancelled"}
            # The worker checks immediately before the existing registry commit;
            # CLI writes stayed in the unregistered slot, never active credentials.
            def commit():
                if not live():
                    raise ar.RescueError("cancelled")
                nonlocal registered
                item = accounts.renew_from_isolated_slot(root, iid,
                    expected_active=expected_active, expected_switched=expected_switched, bound_live=live)
                if item is not None:
                    return item
                item = accounts.commit_codex(root, iid, "Codex " + iid, self.r.lang(), bound_live=live)
                registered = True
                return item
            item = await worker(commit)
            if not live():
                return {"ok": False, "why": "cancelled"}
            if item.get("renewed"):
                from . import quota_refresh
                agent = self.h.agent
                if not agent.owns_harness() or not live():
                    return {"ok": False, "why": "cancelled"}
                quota_refresh.requalify_request(self.h)
                agent.request_restart("auth_rescue")
                return {"ok": True, "route": "device", "live": "NOT_VERIFIED",
                        "text": "当前账号续登已保存。请发送新消息继续；旧请求不会重放。权益同步 NOT_VERIFIED。"}
            return {"ok": True, "pending_binding": True, "account_name": "Codex " + iid, "route": "device",
                    "text": "设备码登录已保存。请在手机账号设置中明确选择该账号，再发送新消息继续。权益同步 NOT_VERIFIED。"}
        except (ar.RescueError, accounts.AccountError) as e:
            return {"ok": False, "why": e.args[0]}
        finally:
            closed = True
            if flow is not None:
                await asyncio.to_thread(flow.close, restore=False)
            if iid is not None and not registered:
                await asyncio.to_thread(accounts.discard_slot, root, "codex", iid)
