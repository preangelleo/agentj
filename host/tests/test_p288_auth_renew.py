"""P288 AI 登录到期前主动续期: the 7 / 3 / 1-day reminder cadence, the 「还有 N 天」 view + renew card, immediate replacement
(and the old login kept when the new one fails its live check — Claude token and Codex auth.json), the self-reopening
authorize round, the daily tool-free health check (login → rescue alert at once; network failures stay quiet), and the
heartbeat line. Everything runs against tests/fakep258.py — never a real login or a real model call."""
import _hermetic  # noqa: F401
import asyncio
import os
import pathlib
import unittest
from datetime import date, timedelta
from unittest import mock

from agentj import auth_rescue as ar
from agentj import elevate
import test_p258_auth_rescue as t258

GOOD, Base, _Agent, _Elev = t258.GOOD, t258.Base, t258._Agent, t258._Elev


class Manager(Base, unittest.IsolatedAsyncioTestCase):
    """P258's Manager fixture without inheriting its tests."""
    setUp = t258.Manager.setUp
    probe = t258.Manager.probe
    drain = t258.Manager.drain
    env_text = t258.Manager.env_text

OLD = "sk-ant-oat01-" + "O" * 95 + "Z"


class _SeqElev(_Elev):
    """Answers card after card from a list: None = the card expired unused, bytes = what the owner pasted."""

    def __init__(self, seq, wait=0.0):
        super().__init__(None)
        self.seq, self.wait = list(seq), wait

    async def request(self, card):
        self.cards.append({k: v for k, v in card.items() if k != "sink"})
        await asyncio.sleep(self.wait)
        v = self.seq.pop(0) if self.seq else None
        if v is None:
            return {"result": "expired"}
        return await asyncio.to_thread(card["sink"], bytearray(v))


# ------------------------------------------------------------------ ① cadence (pure)
class Cadence(unittest.TestCase):
    def test_next_mark_table(self):
        self.assertIsNone(ar.next_mark(8, []))
        self.assertEqual(ar.next_mark(7, []), 7)
        self.assertIsNone(ar.next_mark(6, [7]))
        self.assertEqual(ar.next_mark(3, [7]), 3)
        self.assertIsNone(ar.next_mark(2, [7, 3]))
        self.assertEqual(ar.next_mark(1, [7, 3]), 1)
        self.assertIsNone(ar.next_mark(0, [7, 3, 1]))
        self.assertIsNone(ar.next_mark(-4, [7, 3, 1]))

    def test_missed_days_fire_once(self):
        self.assertEqual(ar.next_mark(5, []), 7)          # serve was off on day 7: the 7-day one, late
        self.assertEqual(ar.next_mark(2, []), 3)          # off until day 2: only the 3-day one
        self.assertEqual(ar.next_mark(0, []), 1)          # first seen on the expiry day
        self.assertIsNone(ar.next_mark(5, [3]))           # a smaller mark already sent: never go back to 7

    def test_constants(self):
        self.assertEqual((ar.WARN_DAYS, ar.MARKS, ar.TOKEN_DAYS), (7, (7, 3, 1), 365))


# ------------------------------------------------------------------ ② reminders, view, heartbeat
class Reminders(Manager):
    def mint(self, days_ago, today):
        ar.write_meta(self.st, {"claude": {"minted": (today - timedelta(days=days_ago)).isoformat()}})

    async def test_three_reminders_over_ten_days(self):
        start = date(2026, 10, 1)
        self.mint(355, start)                                # 10 days left on `start`
        sent = {}
        for i in range(12):
            day = start + timedelta(days=i)
            t = self.r.daily(day)
            t2 = self.r.daily(day)                           # hourly loop: never twice a day
            self.assertIsNone(t2)
            if t:
                sent[10 - i] = t
        self.assertEqual(sorted(sent), [1, 3, 7])
        self.assertIn("还有 7 天到期", sent[7])
        self.assertIn("现在续期", sent[1])
        self.assertEqual(len(self.host.notices), 3)
        for t in self.host.notices:
            self.assertNotIn("https://", t)                  # an authorize link never goes into a notice (Telegram etc.)

    async def test_expiry_day_text(self):
        today = date(2026, 10, 14)
        self.mint(365, today)
        self.assertIn("今天到期", self.r.daily(today))

    async def test_past_expiry_left_to_the_health_check(self):
        today = date(2026, 10, 14)
        self.mint(367, today)                                # expired two days ago
        self.assertIsNone(self.r.daily(today))
        with mock.patch.object(ar, "date") as d:
            d.today.return_value = today
            d.fromisoformat = date.fromisoformat
            v = self.r.view()
        self.assertNotIn("renew", v)
        self.assertEqual(v["harnesses"][0]["token"]["days_left"], -2)

    async def test_push_on_reminder(self):
        pushes = []
        self.host.push_notify = pushes.append
        today = date(2026, 10, 14)
        self.mint(358, today)
        self.r.daily(today)
        self.assertEqual(pushes, ["security"])

    async def test_view_days_left_and_renew_card(self):
        today = date.today()
        self.mint(361, today)
        self.r.daily(today)
        v = self.r.view()
        row = v["harnesses"][0]
        self.assertEqual((row["kind"], row["expiry"], row["token"]["days_left"], row["warn"]), ("claude", "known", 4, True))
        self.assertEqual(v["renew"], {"kind": "claude", "days_left": 4, "expires": row["token"]["expires"], "mark": 7})
        self.assertTrue(v["dot"])
        self.mint(100, today)
        v = self.r.view()
        self.assertNotIn("renew", v)
        self.assertFalse(v["harnesses"][0]["warn"])

    async def test_expiry_inventory(self):
        v = {h["kind"]: h for h in self.r.view()["harnesses"]}
        self.assertEqual((v["claude"]["expiry"], v["codex"]["expiry"], v["opencode"]["expiry"]), ("auto", "auto", "none"))
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = OLD          # a token stored elsewhere: no day to count from
        self.assertEqual(self.r.view()["harnesses"][0]["expiry"], "unknown")
        self.assertNotIn(OLD, str(self.r.view()))

    async def test_heartbeat_line(self):
        today = date.today()
        self.mint(300, today)
        self.assertIsNone(ar.heartbeat_line(self.st, "zh"))
        self.mint(362, today)
        self.assertIn("还有 3 天到期", ar.heartbeat_line(self.st, "zh"))
        self.assertIn("expires in 3 days", ar.heartbeat_line(self.st, "en"))
        src = (pathlib.Path(ar.__file__).parent / "heartbeat.py").read_text()
        self.assertIn("auth_rescue.heartbeat_line(h.st, lang)", src)


# ------------------------------------------------------------------ ③ replacement + rollback
class Replace(Manager):
    def write_old(self):
        p = ar.service_env_path()
        pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
        elevate.write_secret({"mode": "env", "path": p, "key": "CLAUDE_CODE_OAUTH_TOKEN"}, bytearray(OLD.encode()))
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = OLD

    async def test_new_token_replaces_at_once_and_recounts(self):
        self.write_old()
        ar.write_meta(self.st, {"claude": {"minted": (date.today() - timedelta(days=362)).isoformat(),
                                           "reminded": {"expires": "x", "marks": [7, 3]}}})
        res = await asyncio.to_thread(self.r._store_token, bytearray(GOOD.encode()), {})
        self.assertEqual(res["result"], "saved")
        self.assertIn(GOOD, self.env_text())
        self.assertNotIn(OLD, self.env_text())
        self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], GOOD)
        meta = ar.read_meta(self.st)
        self.assertEqual(ar.token_due(meta)["days_left"], 365)
        self.assertNotIn("reminded", meta["claude"])          # the marks start over for the new expiry
        self.assertIn("replaced", meta["claude"])
        self.assertNotIn("renew", self.r.view())

    async def test_failed_check_keeps_old_token(self):
        self.write_old()
        before = self.env_text()
        for value in (b"sk-ant-oat01-" + b"B" * 96, b"garbage"):
            out = {}
            res = await asyncio.to_thread(self.r._store_token, bytearray(value), out)
            self.assertEqual(res["result"], "failed")
            self.assertEqual(self.env_text(), before)
            self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], OLD)

    async def test_codex_failed_check_restores_previous_auth(self):
        auth = pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json"
        auth.write_text('{"old": true}')
        os.environ["FAKE258_DEVICE"] = "ok"
        os.environ["FAKE258_CODEX_PROBE"] = "login"
        with self.probe(True):
            await self.r.start("codex", "device")
            await self.drain()
        self.assertEqual(auth.read_text(), '{"old": true}')
        self.assertEqual(self.r.last["codex"]["why"], "verify_codex")
        self.assertIn("已保留原来的登录", self.r.view()["flow"]["why"])

    async def test_codex_passing_check_keeps_new_auth(self):
        auth = pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json"
        auth.write_text('{"old": true}')
        os.environ["FAKE258_SCREENS"] = "ready"
        with self.probe(True):
            await self.r.start("codex", "device")
            await self.drain()
        self.assertEqual(auth.read_text(), '{"new": true}')
        self.assertEqual(self.r.last["codex"]["result"], "recovered")
        self.assertTrue(ar.read_meta(self.st)["health"]["codex"]["ok"])


# ------------------------------------------------------------------ ④ the authorize round reopens itself
class Rounds(Manager):
    async def test_expired_link_reopens_then_succeeds(self):
        os.environ["FAKE258_SCREENS"] = "ready"
        self.host.elevate = _SeqElev([None, b"GOODCODE"])
        self.host.agent = _Agent(owns=True)
        with self.probe(True):
            await self.r.start("claude", "setup_token")
            await self.drain()
        self.assertEqual(len(self.host.elevate.cards), 2)
        self.assertEqual(self.r.last["claude"]["result"], "recovered", self.r.last)
        self.assertIn(GOOD, self.env_text())
        logs = (pathlib.Path(self.st.root) / "host.log").read_text() if (pathlib.Path(self.st.root) / "host.log").exists() else ""
        self.assertNotIn(GOOD, logs)

    async def test_refused_code_reopens(self):
        os.environ["FAKE258_SCREENS"] = "ready"
        self.host.elevate = _SeqElev([b"WRONG", b"GOODCODE"])
        with self.probe(True):
            await self.r.start("claude", "setup_token")
            await self.drain()
        self.assertEqual(self.r.last["claude"]["result"], "recovered")

    async def test_round_shows_on_phone(self):
        seen = []
        orig = self.r.broadcast

        def spy():
            f = self.r.view()["flow"]
            if f:
                seen.append(dict(f))
            orig()
        self.r.broadcast = spy
        self.host.elevate = _SeqElev([None, None, None])
        await self.r.start("claude", "setup_token")
        await self.drain()
        rounds = sorted({f["round"] for f in seen if f.get("stage") == "url"})
        self.assertEqual(rounds, [1, 2, 3])
        self.assertTrue(any(f.get("reopened") == "expired" for f in seen))
        self.assertTrue(all(f.get("expires_at") for f in seen if f.get("stage") == "url"))
        self.assertEqual((self.r.last["claude"]["result"], self.r.last["claude"]["why"]), ("failed", "expired"))
        self.assertEqual(len(self.host.elevate.cards), ar.RENEW_ROUNDS)

    async def test_cancel_stops_rounds(self):
        self.host.elevate = _SeqElev([None, None, None], wait=1.0)
        await self.r.start("claude", "setup_token")
        for _ in range(100):
            if self.r.flow.get("stage") == "url":
                break
            await asyncio.sleep(.05)
        await self.r.cancel_flow()
        await self.drain()
        self.assertEqual(self.r.last["claude"]["why"], "cancelled")
        self.assertEqual(len(self.host.elevate.cards), 1)


# ------------------------------------------------------------------ ⑤ daily health check
class Health(Base):
    def test_claude(self):
        for mode, ok, cls in (("ok", True, None), ("", False, "login"), ("balance", False, "balance"),
                              ("rate", False, "rate_limit"), ("down", False, None)):
            os.environ["FAKE258_PROBE"] = mode
            r = ar.health_check("claude", timeout=20)
            self.assertEqual((r["ok"], r["cls"]), (ok, cls), mode)

    def test_codex(self):
        for mode, ok, cls in (("ok", True, None), ("login", False, "login"), ("down", False, None)):
            os.environ["FAKE258_CODEX_PROBE"] = mode
            r = ar.health_check("codex", timeout=20)
            self.assertEqual((r["ok"], r["cls"]), (ok, cls), mode)

    def test_never_returns_output(self):
        os.environ["FAKE258_PROBE"] = ""
        r = ar.health_check("claude", timeout=20)
        self.assertEqual(set(r), {"ok", "cls", "why"})
        self.assertNotIn("Invalid API key", str(r))

    def test_no_binary(self):
        os.environ["AGENTJ_CODEX_BIN"] = str(self.tmp / "missing")
        with mock.patch.object(ar, "binary", return_value=None):
            self.assertEqual(ar.health_check("codex")["why"], "no_binary")


class HealthDaily(Manager):
    def targets(self, *kinds):
        return mock.patch.object(ar, "health_targets", return_value=list(kinds))

    async def test_login_failure_alerts_at_once_and_once_a_day(self):
        os.environ["FAKE258_PROBE"] = ""                      # Claude: "Invalid API key · Please run /login"
        os.environ["FAKE258_CODEX_PROBE"] = "ok"
        d1 = date(2026, 10, 14)
        with self.targets("claude", "codex"):
            res = await self.r.health_daily(d1)
            self.assertEqual((res["claude"]["cls"], res["codex"]["ok"]), ("login", True))
            self.assertEqual(len(self.host.notices), 1)
            self.assertIn("每日体检", self.host.notices[0])
            self.assertTrue(self.r.view()["dot"])
            self.assertEqual(await self.r.health_daily(d1), {})          # once a day
            os.environ["FAKE258_PROBE"] = "ok"
            await self.r.health_daily(d1 + timedelta(days=1))            # back to normal → the stale dot goes away
        self.assertFalse(self.r.view()["dot"])
        h = {x["kind"]: x.get("health") for x in self.r.view()["harnesses"]}
        self.assertTrue(h["claude"]["ok"] and h["codex"]["ok"])

    async def test_network_failure_is_quiet(self):
        os.environ["FAKE258_PROBE"] = "down"
        with self.targets("claude"):
            res = await self.r.health_daily(date(2026, 10, 14))
        self.assertFalse(res["claude"]["ok"])
        self.assertEqual(self.host.notices, [])
        self.assertFalse(self.r.view()["dot"])

    async def test_balance_is_a_hint_not_a_rescue(self):
        os.environ["FAKE258_PROBE"] = "balance"
        with self.targets("claude"):
            await self.r.health_daily(date(2026, 10, 14))
        self.assertFalse(self.r.view()["dot"])
        self.assertEqual(self.r.view()["harnesses"][0]["state"], "balance")

    async def test_skips_a_harness_mid_rescue(self):
        self.r.flow = {"kind": "claude", "stage": "url"}
        with self.targets("claude"), mock.patch.object(ar, "health_check") as hc:
            await self.r.health_daily(date(2026, 10, 14))
        hc.assert_not_called()

    async def test_loop_waits_before_the_first_call(self):
        calls = []

        async def fake(*a, **k):
            calls.append(1)
            return {}
        with mock.patch.object(ar, "HEALTH_DELAY", 0.4), mock.patch.object(self.r, "health_daily", fake), \
                mock.patch("agentj.browser_login.quiet", return_value=False):
            t = asyncio.ensure_future(self.r.loop(every=0.1))
            await asyncio.sleep(0.2)
            self.assertEqual(calls, [])                       # a short-lived serve (tests) never makes a model call
            await asyncio.sleep(0.6)
            t.cancel()
        self.assertTrue(calls)

    async def test_loop_is_silent_in_quiet_hours(self):
        calls = []

        async def fake(*a, **k):
            calls.append(1)
            return {}
        today = date.today()
        ar.write_meta(self.st, {"claude": {"minted": (today - timedelta(days=360)).isoformat()}})
        with mock.patch.object(ar, "HEALTH_DELAY", 0), mock.patch.object(self.r, "health_daily", fake), \
                mock.patch("agentj.browser_login.quiet", return_value=True):
            t = asyncio.ensure_future(self.r.loop(every=0.05))
            await asyncio.sleep(0.3)
            t.cancel()
        self.assertEqual((calls, self.host.notices), ([], []))

    def test_targets_skip_never_signed_in(self):
        d = {"harnesses": [{"name": "claude", "installed": True, "supported": True, "logged_in": True},
                           {"name": "codex", "installed": True, "supported": True, "logged_in": False},
                           {"name": "opencode", "installed": False, "supported": True, "logged_in": None},
                           {"name": "gemini", "installed": True, "supported": False, "logged_in": True}]}
        with mock.patch("agentj.harness.detect", return_value=d):
            self.assertEqual(ar.health_targets(), ["claude"])


if __name__ == "__main__":
    unittest.main()
