"""P258 AI 登录救援: classification (login vs balance vs rate limit), the interactive screens a restart can stop at, the Claude
setup-token / auth-login PTY flow, the Codex device-code flow, token storage + live check, recovery and self-check, the
expiry reminder (P288: 7/3/1 days), and the passkey gate. Everything runs against tests/fakep258.py — never a real login."""
import _hermetic  # noqa: F401
import asyncio
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from agentj import auth_rescue as ar
from agentj import shared
from agentj.state import State

HERE = pathlib.Path(__file__).resolve().parent
GOOD = "sk-ant-oat01-" + "A" * 95 + "Q"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p258-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        (self.tmp / "cc").mkdir()
        (self.tmp / "cx").mkdir()
        self.log = self.tmp / "fake.log"
        bins = {}
        for kind in ("claude", "codex"):
            w = self.tmp / kind
            w.write_text(f"#!/bin/sh\nFAKE258_KIND={kind} exec {sys.executable} {HERE / 'fakep258.py'} \"$@\"\n")
            w.chmod(0o700)
            bins[kind] = str(w)
        env = {"AGENTJ_CLAUDE_BIN": bins["claude"], "AGENTJ_CODEX_BIN": bins["codex"], "FAKE258_LOG": str(self.log),
               "HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"), "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"),
               "CODEX_HOME": str(self.tmp / "cx"), "AGENTJ_STATE_DIR": str(self.tmp / "st"), "FAKE258_GOOD_TOKEN": GOOD}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FAKE258_SCREENS", "FAKE258_DEVICE",
                  "FAKE258_PROBE", "FAKE258_NOURL", "FAKE258_WIPE"):
            os.environ.pop(k, None)

    def screens(self, kind, seq, press=(), secs=6.0):
        os.environ["FAKE258_SCREENS"] = seq
        return ar.screen_check(kind, str(self.work), press, secs=secs)

    def logged(self):
        return self.log.read_text() if self.log.exists() else ""


# ------------------------------------------------------------------ ① classify
class Classify(unittest.TestCase):
    def test_login_lines(self):
        for t in ("Login expired · Please run /login", "Invalid API key · Please run /login", "Not logged in",
                  "OAuth token has expired", "API Error: 401 {\"type\":\"authentication_error\"}",
                  "unexpected status 401 Unauthorized: refresh token was revoked"):
            self.assertEqual(ar.classify("claude", t), "login", t)

    def test_balance_never_rescue(self):
        for t in ("Credit balance is too low", "insufficient_quota: check your plan and billing", "HTTP 402 Payment Required",
                  "余额不足，请充值", "Credit balance is too low · Please run /login to switch account"):
            self.assertEqual(ar.classify("claude", t), "balance", t)

    def test_rate_limit_never_rescue(self):
        for t in ("Claude AI usage limit reached|1760000000", "429 Too Many Requests", "rate_limit_exceeded",
                  "You've hit your weekly limit · resets Oct 19", "usage limit reached, sign in again later? no: try again at 3 PM"):
            self.assertEqual(ar.classify("claude", t), "rate_limit", t)

    def test_other(self):
        for t in ("", None, "ECONNRESET", "the model is overloaded", "port 14013 in use"):
            self.assertIsNone(ar.classify("claude", t))


# ------------------------------------------------------------------ ② restart screens (every catalogued one)
class Screens(Base):
    def test_clean_start_is_ready(self):
        r = self.screens("claude", "ready")
        self.assertTrue(r["ready"])
        self.assertIsNone(r["blocker"])
        self.assertFalse(shared.PROBE_PIDS)

    def test_trust_theme_notes_answered_by_fixed_keys(self):
        r = self.screens("claude", "trust,theme,security_notes,ready")
        self.assertTrue(r["ready"], r)
        self.assertEqual(r["answered"], ["trust", "theme", "security_notes"])
        self.assertIn("key trust '\\x1b[B\\r'", self.logged())     # Down + Enter: Enter alone = 「No, exit」

    def test_update_notice_does_not_block(self):
        r = self.screens("claude", "update,ready")
        self.assertTrue(r["ready"])
        self.assertEqual(r["info"], ["update"])

    def test_confirm_screens_reported_then_pressed(self):
        for sid in ("api_key", "bypass", "mcp"):
            r = self.screens("claude", f"{sid},ready")
            self.assertFalse(r["ready"], sid)
            self.assertEqual((r["blocker"]["id"], r["blocker"]["action"]), (sid, "confirm"))
            r = self.screens("claude", f"{sid},ready", press=(sid,))
            self.assertTrue(r["ready"], (sid, r))

    def test_fail_screens_named(self):
        for sid in ("login_method", "login_expired"):
            r = self.screens("claude", sid)
            self.assertEqual((r["blocker"]["id"], r["blocker"]["action"]), (sid, "fail"))
            self.assertTrue(r["blocker"]["zh"])

    def test_unknown_screen_times_out_with_scrubbed_line(self):
        r = self.screens("claude", "unknown", secs=2.5)
        self.assertEqual(r["blocker"]["id"], "unknown")
        self.assertIn("Something brand new", r["last"])

    def test_exit_reported(self):
        r = self.screens("claude", "exit", secs=4)
        self.assertEqual(r["blocker"]["id"], "exited")

    def test_codex_screens(self):
        r = self.screens("codex", "trust,update,ready")
        self.assertTrue(r["ready"], r)
        self.assertEqual(r["answered"], ["trust", "update"])
        self.assertEqual(self.screens("codex", "login")["blocker"]["id"], "login")
        self.assertEqual(self.screens("codex", "approval,ready")["blocker"]["id"], "approval")
        self.assertTrue(self.screens("codex", "approval,ready", press=("approval",))["ready"])

    def test_opencode_has_no_screen(self):
        self.assertTrue(ar.screen_check("opencode", str(self.work))["ready"])

    def test_scrub(self):
        self.assertNotIn(GOOD, ar.scrub("token " + GOOD))
        self.assertEqual(ar.confirm_keys("claude", "trust"), None)      # auto, never a phone press
        self.assertEqual(ar.confirm_keys("claude", "api_key"), ar.DOWN + ar.ENTER)


# ------------------------------------------------------------------ ③ Claude PTY flow
class ClaudeFlowT(Base):
    def test_setup_token_ok(self):
        f = ar.ClaudeFlow("setup_token", str(self.work))
        try:
            self.assertTrue(f.start().startswith("https://claude.com/cai/oauth/authorize?"))
            tok = f.submit("GOODCODE")
            self.assertEqual(bytes(tok).decode(), GOOD)
            self.assertEqual(f.pty.buf, "")
        finally:
            f.close()

    def test_bad_code_rejected(self):
        f = ar.ClaudeFlow("setup_token", str(self.work))
        try:
            f.start()
            with self.assertRaises(ar.RescueError) as c:
                f.submit("WRONG")
            self.assertEqual(c.exception.args[0], "rejected")
        finally:
            f.close()

    def test_bad_code_shape(self):
        f = ar.ClaudeFlow("setup_token", str(self.work))
        try:
            f.start()
            with self.assertRaises(ar.RescueError):
                f.submit("two words")
        finally:
            f.close()

    def test_no_url(self):
        os.environ["FAKE258_NOURL"] = "1"
        with mock.patch.object(ar, "URL_WAIT", 1.5):
            with self.assertRaises(ar.RescueError) as c:
                ar.ClaudeFlow("setup_token").start()
        self.assertEqual(c.exception.args[0], "no_url")

    def test_auth_login_route(self):
        f = ar.ClaudeFlow("login", str(self.work))
        try:
            f.start()
            self.assertIsNone(f.submit("GOODCODE"))
        finally:
            f.close()

    def test_old_credentials_never_reach_the_flow(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-old"
        f = ar.ClaudeFlow("setup_token", str(self.work))
        try:
            f.start()
            with open(f"/proc/{f.pty.pid}/environ", "rb") as fh:
                env = fh.read()
            self.assertNotIn(b"sk-ant-api-old", env)
            self.assertIn(b"BROWSER=true", env)
        finally:
            f.close()


# ------------------------------------------------------------------ ④ Codex device flow
class CodexFlowT(Base):
    def test_ok(self):
        (self.tmp / "cx" / "auth.json").write_text('{"old": true}')
        f = ar.CodexFlow()
        url, code, ttl = f.start()
        self.assertEqual((url, code, ttl), ("https://auth.openai.com/codex/device", "AB12-CD34E", 900))
        self.assertEqual(f.wait(), "ok")
        self.assertIn("new", (self.tmp / "cx" / "auth.json").read_text())

    def test_failure_restores_previous_login(self):
        (self.tmp / "cx" / "auth.json").write_text('{"old": true}')
        os.environ.update(FAKE258_DEVICE="fail", FAKE258_WIPE="1")
        f = ar.CodexFlow()
        f.start()
        self.assertEqual(f.wait(), "failed")
        self.assertEqual((self.tmp / "cx" / "auth.json").read_text(), '{"old": true}')
        self.assertEqual(stat.S_IMODE((self.tmp / "cx" / "auth.json").stat().st_mode), 0o600)

    def test_device_disabled(self):
        os.environ["FAKE258_DEVICE"] = "disabled"
        with self.assertRaises(ar.RescueError) as c:
            ar.CodexFlow().start()
        self.assertEqual(c.exception.args[0], "device_disabled")

    def test_timeout_and_cancel(self):
        os.environ["FAKE258_DEVICE"] = "hang"
        f = ar.CodexFlow()
        f.start()
        f.expires = -29            # deadline = expires + 30 s → 1 s
        self.assertEqual(f.wait(), "timeout")
        f = ar.CodexFlow()
        f.start()
        import threading
        ev = threading.Event()
        ev.set()
        self.assertEqual(f.wait(ev), "cancelled")


# ------------------------------------------------------------------ ⑤ the manager
class _Elev:
    def __init__(self, value):
        self.value, self.cards = value, []

    async def request(self, card):
        self.cards.append({k: v for k, v in card.items() if k != "sink"})
        if self.value is None:
            return {"result": "expired"}
        return await asyncio.to_thread(card["sink"], bytearray(self.value))


class _Agent:
    kind = "claude"

    def __init__(self, owns=True):
        self.owns, self.restarts = owns, []

    def owns_harness(self):
        return self.owns

    def request_restart(self, why):
        self.restarts.append(why)
        return "next_turn"


class _Host:
    def __init__(self, st, work, allow=True, value=b"GOODCODE"):
        self.st, self.lang, self.agent_cfg = st, "zh", {"dir": str(work)}
        self.agent = None
        self.notices, self.asks, self.allow = [], [], allow
        self.elevate = _Elev(value)
        self.tasks = []

    def agent_notice(self, t):
        self.notices.append(t)

    async def ask(self, tool, tool_input, batch=True, require_passkey=False, **kw):
        self.asks.append((tool, batch, require_passkey, tool_input.get("summary", "")))
        return {"behavior": "allow" if self.allow else "deny"}

    def _bg(self, coro, what="bg"):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t


class Manager(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Base.setUp(self)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = _Host(self.st, self.work)
        self.r = ar.Rescue(self.host)
        p = mock.patch("agentj.harness_setup.enabled", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def probe(self, ok=True):
        return mock.patch("agentj.harness.probe", return_value={"harnesses": [{"name": "claude", "callable": ok},
                                                                              {"name": "codex", "callable": ok}]})

    async def drain(self):
        while self.host.tasks:
            await self.host.tasks.pop(0)

    def env_text(self):
        p = pathlib.Path(ar.service_env_path())
        return p.read_text() if p.exists() else ""

    async def test_signal_login_alerts_once_balance_no_dot(self):
        self.r.text_signal("claude", "Login expired · Please run /login")
        self.r.text_signal("claude", "Please run /login")
        self.assertEqual(len(self.host.notices), 1)
        self.assertIn("设置 → 紧急修复 → AI 登录救援", self.host.notices[0])
        self.assertTrue(self.r.view()["dot"])
        self.r.clear("claude")
        self.r.text_signal("claude", "Credit balance is too low")
        v = self.r.view()
        self.assertFalse(v["dot"])
        self.assertEqual(next(h for h in v["harnesses"] if h["kind"] == "claude")["state"], "balance")
        self.assertEqual(len(self.host.notices), 1)

    async def test_store_token_good(self):
        out = {}
        res = await asyncio.to_thread(self.r._store_token, bytearray(GOOD.encode()), out)
        self.assertEqual(res["result"], "saved")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=", self.env_text())
        self.assertEqual(stat.S_IMODE(os.stat(ar.service_env_path()).st_mode), 0o600)
        self.assertEqual(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), GOOD)
        self.assertEqual(ar.read_meta(self.st)["claude"]["minted"], date.today().isoformat())
        self.assertNotIn(GOOD, str(res))
        self.assertNotIn(GOOD, (pathlib.Path(self.st.root) / ar.META).read_text())

    async def test_store_token_refusals_write_nothing(self):
        for value, why in ((b"hello", "bad_token"), (("sk-ant-oat01-" + "B" * 96).encode(), "verify")):
            out = {}
            res = await asyncio.to_thread(self.r._store_token, bytearray(value), out)
            self.assertEqual(res["result"], "failed")
            self.assertEqual(out["why"], why)
            self.assertEqual(self.env_text(), "")

    async def test_passkey_required(self):
        self.host.allow = False
        res = await self.r.start("codex", "device")
        self.assertEqual(res, {"ok": False, "why": "passkey"})
        tool, batch, pk, summary = self.host.asks[0]
        # P573 (#386 ⑤): login rescue shows a link / code, never a password — one button that 全放行 still asks
        self.assertEqual((tool, batch, pk), ("AuthRescue", False, False))
        self.assertIn("已配对的遥控器上确认", summary)
        self.assertIsNone(self.r.flow)

    async def test_bad_route(self):
        self.assertEqual((await self.r.start("codex", "paste"))["why"], "unsupported")
        self.assertEqual((await self.r.start("nope", "device"))["why"], "unsupported")

    async def test_codex_end_to_end(self):
        os.environ["FAKE258_SCREENS"] = "trust,ready"
        with self.probe(True):
            self.assertEqual(await self.r.start("codex", "device"), {"ok": True})
            for _ in range(50):
                if self.r.flow.get("stage") == "device":
                    break
                await asyncio.sleep(.1)
            self.assertEqual(self.r.view()["flow"]["code"], "AB12-CD34E")
            await self.drain()
        self.assertEqual(self.r.last["codex"]["result"], "recovered")
        self.assertIn("已重新登录并恢复", self.host.notices[-1])

    async def test_claude_setup_token_end_to_end(self):
        os.environ["FAKE258_SCREENS"] = "trust,theme,ready"
        self.host.agent = _Agent(owns=True)
        with self.probe(True):
            await self.r.start("claude", "setup_token")
            await self.drain()
        self.assertEqual(self.r.last["claude"]["result"], "recovered", self.r.last)
        self.assertEqual(self.r.last["claude"]["answered"], ["trust", "theme"])
        self.assertEqual(self.host.agent.restarts, ["auth_rescue"])
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=", self.env_text())
        card = self.host.elevate.cards[0]
        self.assertEqual((card["kind"], card["name"]), ("secret", "CLAUDE_AUTH_CODE"))
        for t in self.host.notices:
            self.assertNotIn(GOOD, t)
        self.assertNotIn(GOOD, (pathlib.Path(self.st.root) / "host.log").read_text()
                         if (pathlib.Path(self.st.root) / "host.log").exists() else "")
        self.assertNotIn("GOODCODE", self.logged())               # the fake logs only the code's length

    async def test_claude_paste_bad_token(self):
        self.host.elevate = _Elev(b"not-a-token")
        await self.r.start("claude", "paste")
        await self.drain()
        self.assertEqual(self.r.last["claude"], {**self.r.last["claude"], "result": "failed", "why": "bad_token"})

    async def test_conflict_blocks_recovered(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-x"
        self.host.elevate = _Elev(GOOD.encode())
        with self.probe(True):
            await self.r.start("claude", "paste")
            await self.drain()
        self.assertEqual(self.r.last["claude"]["result"], "partial")
        self.assertEqual(self.r.last["claude"]["why"], "conflict")
        self.assertIn("ANTHROPIC_API_KEY", self.host.notices[-1])
        self.assertNotIn("sk-ant-api-x", self.host.notices[-1])

    async def test_blocked_screen_then_press(self):
        os.environ["FAKE258_SCREENS"] = "mcp,ready"
        with self.probe(True):
            res = await self.r.recover("claude", route="paste")
            self.assertEqual(res, {"ok": False, "why": "blocked", "screen": "mcp"})
            self.assertIn("代按确认", self.host.notices[-1])
            self.assertEqual(self.r.view()["harnesses"][0]["last"]["screen"]["action"], "confirm")
            self.assertEqual(await self.r.press("claude", "mcp"), {"ok": True})
            await self.drain()
        self.assertEqual(self.r.last["claude"]["result"], "recovered")
        self.assertEqual((await self.r.press("claude", "trust"))["why"], "unsupported")   # auto screens are never phone-pressed

    async def test_probe_failure_is_not_recovered(self):
        os.environ["FAKE258_SCREENS"] = "ready"
        with self.probe(False):
            res = await self.r.recover("codex")
        self.assertEqual(res["why"], "probe")
        self.assertNotIn("并恢复", self.host.notices[-1])

    async def test_owner_session_note(self):
        os.environ["FAKE258_SCREENS"] = "ready"
        self.host.agent = _Agent(owns=False)
        with self.probe(True):
            await self.r.recover("claude", route="login")
        self.assertEqual(self.r.last["claude"]["note"], "owner_session")
        self.assertIn("重开一次", self.host.notices[-1])

    async def test_expiry_reminder(self):
        today = date(2026, 10, 14)
        ar.write_meta(self.st, {"claude": {"minted": (today - timedelta(days=300)).isoformat()}})
        self.assertIsNone(self.r.daily(today))
        ar.write_meta(self.st, {"claude": {"minted": (today - timedelta(days=360)).isoformat()}})   # P288: 7-day window
        self.assertIn("还有 5 天", self.r.daily(today))
        self.assertIsNone(self.r.daily(today))                    # once per mark (test_p288_auth_renew: 7 / 3 / 1)
        self.assertTrue(self.r.view()["dot"])
        self.assertEqual(stat.S_IMODE((pathlib.Path(self.st.root) / ar.META).stat().st_mode), 0o600)


# ------------------------------------------------------------------ ⑥ wiring
class Wiring(unittest.TestCase):
    def test_types_and_always_ask(self):
        from agentj import serve, approval_jev, goal
        for t in ("auth_get", "auth_start", "auth_cancel", "auth_press"):
            self.assertIn(t, serve.P33_ONLY)
        self.assertIn("AuthRescue", approval_jev.ALWAYS_ASK)
        self.assertIn("AuthRescue", goal.SOFT_EXEMPT)
        self.assertTrue(hasattr(serve.Host, "on_auth") or any(hasattr(c, "on_auth") for c in vars(serve).values()
                                                               if isinstance(c, type)))

    def test_probe_pids_skipped(self):
        src = pathlib.Path(shared.__file__).read_text()
        self.assertIn("if d[\"pid\"] in PROBE_PIDS", src)


if __name__ == "__main__":
    unittest.main()
