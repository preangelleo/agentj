"""P207 host side: `heartbeat.daily_at` (wake once a day at HH:MM) and the address book's phone ops (`ct_rm`, `ct_export`,
`ct_import`) plus the signed `mail_allow` control (an address joins the P170 e-mail channel's allowlist).

Daily rule: due once today's HH:MM has passed, no daily beat is recorded for today (heartbeat.json `daily`), and serve was
running at HH:MM or started at most DAILY_GRACE after it; late beats (busy Agent) still go the same day, never the next.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state)
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import contacts as C, controls, heartbeat as hb, mail as M, preferences, serve  # noqa: E402
import test_p156_heartbeat as hbt  # noqa: E402
from test_controls import sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402
from test_p184_contacts import _Svc  # noqa: E402

DAY = time.mktime((2026, 10, 13, 12, 0, 0, 0, 0, -1))      # a fixed local noon
T730 = time.mktime((2026, 10, 13, 7, 30, 0, 0, 0, -1))


# ------------------------------------------------------------------ Job A: heartbeat.daily_at
class DailyPure(unittest.TestCase):
    def test_target_and_rule(self):
        self.assertEqual(hb.daily_target(DAY, "07:30"), T730)
        for bad in ("", "7:30", "24:00", "12:60", None, 730):
            self.assertIsNone(hb.daily_target(DAY, bad), bad)
        started = T730 - 3600
        self.assertFalse(hb.daily_due(T730 - 60, "07:30", None, started), "before HH:MM")
        self.assertTrue(hb.daily_due(T730, "07:30", None, started), "at HH:MM")
        self.assertTrue(hb.daily_due(T730 + 5 * 3600, "07:30", None, started), "late (busy) but the same day")
        self.assertFalse(hb.daily_due(T730 + 60, "07:30", hb.day_of(T730), started), "already fired today")
        self.assertTrue(hb.daily_due(T730 + 60, "07:30", hb.day_of(T730 - 86400), started), "yesterday's does not count")
        self.assertTrue(hb.daily_due(T730 + 1800, "07:30", None, T730 + 1800), "started 30 min after: still within grace")
        self.assertFalse(hb.daily_due(T730 + 7200, "07:30", None, T730 + 7200), "started 2 h after: skipped today")
        self.assertTrue(hb.daily_due(T730 + 86400 + 10, "07:30", hb.day_of(T730), T730 + 7200), "but due tomorrow")
        self.assertFalse(hb.daily_due(T730 + 60, "", None, started), "off")
        self.assertFalse(hb.daily_due(T730 + 60, "7:30", None, started), "malformed = off")

    def test_parse_daily(self):
        for raw, want in (("07:30", "07:30"), ("7:30", "07:30"), ("23:59", "23:59"), ("0:00", "00:00"), ("07：30", "07:30"),
                          ("off", ""), ("", ""), ("关", ""), (None, "")):
            self.assertEqual(hb.parse_daily(raw), want, raw)
        for bad in ("24:00", "12:60", "730", "7", "abc", "07:30:00"):
            with self.assertRaises(ValueError, msg=bad):
                hb.parse_daily(bad)

    def test_schema_and_settable(self):
        m = preferences.SCHEMA["heartbeat.daily_at"]
        self.assertEqual((m["type"], m["default"], m["tier"]), ("string", "", "user"))
        self.assertEqual(preferences.defaults()["heartbeat"]["daily_at"], "")
        self.assertIn("heartbeat.daily_at", serve.PREF_SET_KEYS)
        for good in ("07:30", "", "00:00", "23:59"):
            preferences.validate({"heartbeat": {"daily_at": good}})
        for bad in ("7:30", "24:00", 5, "07:30 "):
            with self.assertRaises(preferences.ConfigError, msg=bad):
                preferences.validate({"heartbeat": {"daily_at": bad}})


class Daily(unittest.TestCase):
    setUp = hbt.Decide.setUp

    def daily(self, hhmm="07:30", started=T730 - 3600, activity=None):
        self.host.preferences = preferences.merge(self.host.preferences or preferences.defaults(), {"heartbeat": {"daily_at": hhmm}})
        self.h.started = started
        self.h.last_activity = activity if activity is not None else started

    def test_fires_once_a_day_and_survives_a_restart(self):
        self.daily(activity=T730 - 60)                  # a turn ended a minute before 07:30: the idle beat is not due
        self.assertIsNone(asyncio.run(self.h.tick(T730 - 30)), "not yet 07:30")
        r = asyncio.run(self.h.tick(T730 + 20))
        self.assertTrue(r and r["ok"], r)
        self.assertEqual(json.loads((self.st.root / hb.STATE_FILE).read_text())["last"]["by"], "daily")
        self.agent.got[-1].state = "delivered"
        self.assertEqual(self.h.last_activity, T730 + 20, "the daily beat resets the idle clock")
        self.assertIsNone(asyncio.run(self.h.tick(T730 + 80)), "at most once a day")
        restarted = hb.Heartbeat(self.host)             # serve restarts at 07:35: the state file says today is done
        self.host.heartbeat = restarted
        restarted.started = restarted.last_activity = T730 + 300
        self.assertIsNone(asyncio.run(restarted.tick(T730 + 360)), "no double fire after a restart")
        self.assertEqual(json.loads((self.st.root / hb.STATE_FILE).read_text())["daily"], hb.day_of(T730))
        status = restarted.status()
        self.assertEqual((status["daily_at"], status["daily_last"]), ("07:30", hb.day_of(T730)))

    def test_busy_at_the_minute_fires_later_the_same_day(self):
        self.daily(activity=T730 - 60)
        self.agent.status = "working"
        self.assertEqual(asyncio.run(self.h.tick(T730 + 20)), {"ok": False, "why": "busy"})
        self.agent.status = "idle"
        self.h.last_activity = T730 + 3 * 3600 - 60      # the long turn just ended
        r = asyncio.run(self.h.tick(T730 + 3 * 3600))
        self.assertTrue(r and r["ok"], r)

    def test_missed_by_more_than_the_grace_is_skipped(self):
        self.daily(started=T730 + 2 * 3600)              # serve came up at 09:30
        self.assertIsNone(asyncio.run(self.h.tick(T730 + 2 * 3600 + 30)))
        self.assertEqual(self.agent.got, [])

    def test_off_and_disabled(self):
        self.daily(hhmm="", activity=T730 - 60)
        self.assertIsNone(asyncio.run(self.h.tick(T730 + 20)), "empty = off")
        self.daily(activity=T730 - 60)
        self.host.preferences = preferences.merge(self.host.preferences, {"heartbeat": {"enabled": False}})
        self.assertIsNone(asyncio.run(self.h.tick(T730 + 20)), "heartbeat off = no daily beat either")

    def test_an_idle_beat_after_hhmm_counts_as_the_day(self):
        self.daily(activity=T730 - 3 * 3600)             # idle 3 h: the interval beat is due at 07:40 anyway
        r = asyncio.run(self.h.tick(T730 + 600))
        self.assertTrue(r["ok"])
        self.assertEqual(json.loads((self.st.root / hb.STATE_FILE).read_text())["last"]["by"], "timer")
        self.agent.got[-1].state = "delivered"
        self.assertFalse(self.h.daily_is_due(T730 + 700), "no second beat right behind it")

    def test_socket_op(self):
        async def go():
            set_pref = AsyncMock(return_value={"ok": True})
            self.host.set_pref = set_pref
            self.assertEqual(await self.h.handle({"op": "daily", "at": "7:30"}), {"ok": True, "daily_at": "07:30"})
            set_pref.assert_awaited_with("heartbeat.daily_at", "07:30")
            self.assertEqual((await self.h.handle({"op": "daily", "at": "off"}))["daily_at"], "")
            set_pref.assert_awaited_with("heartbeat.daily_at", "")
            self.assertFalse((await self.h.handle({"op": "daily", "at": "25:00"}))["ok"])
        asyncio.run(go())


class DailyCLI(unittest.TestCase):
    run_cli = hbt.CLI.run_cli

    def test_offline(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"XDG_CONFIG_HOME": d + "/cfg", "AGENTJ_STATE_DIR": d + "/state"}
            self.assertEqual(json.loads(self.run_cli("heartbeat", "status", "--json", env=env).stdout)["daily_at"], "")
            r = self.run_cli("heartbeat", "daily", "07:30", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("07:30", r.stdout)
            self.assertEqual(json.loads(self.run_cli("heartbeat", "status", "--json", env=env).stdout)["daily_at"], "07:30")
            self.assertIn("每天 07:30", self.run_cli("heartbeat", "status", env=env).stdout)
            self.assertNotEqual(self.run_cli("heartbeat", "daily", "25:00", env=env).returncode, 0)
            self.assertEqual(self.run_cli("heartbeat", "daily", "off", env=env).returncode, 0)
            self.assertEqual(json.loads(self.run_cli("heartbeat", "status", "--json", env=env).stdout)["daily_at"], "")


# ------------------------------------------------------------------ Job B: ct_rm / ct_export / ct_import
VCF = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:张三\r\nEMAIL:zhang@x.cn\r\nEND:VCARD\r\n"
       "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:李四\r\nEMAIL:li@x.cn\r\nTEL:+86 138 0000 0000\r\nEND:VCARD\r\n")


class Book(_Svc):
    async def test_import_export_remove(self):
        res = await self.phone({"t": "ct_import", "name": "people.vcf", "text": VCF})
        self.assertEqual((res["t"], res["ok"], res["format"], res["added"], res["total"]), ("ct_res", True, "vcf", 2, 2))
        again = await self.phone({"t": "ct_import", "name": "people.vcf", "text": VCF})
        self.assertEqual((again["added"], again["merged"] + again["same"]), (0, 2), "the same file twice dedupes")
        book = C.read(self.st)
        self.assertTrue(all(c["source"] == "phone" for c in book["contacts"]))
        csv = "Name,E-mail 1 - Value\n王五,wang@x.cn\n"
        res = await self.phone({"t": "ct_import", "name": "google.csv", "text": csv})
        self.assertEqual((res["ok"], res["format"], res["added"]), (True, "csv", 1))

        res = await self.phone({"t": "ct_export", "fmt": "vcf"})
        self.assertEqual((res["ok"], res["fmt"], res["count"]), (True, "vcf", 3))
        self.assertIn("zhang@x.cn", res["text"])
        self.assertEqual((await self.phone({"t": "ct_export"}))["fmt"], "vcf", "vcf is the default")
        res = await self.phone({"t": "ct_export", "fmt": "csv"})
        self.assertEqual((res["ok"], res["fmt"]), (True, "csv"))
        self.assertIn("wang@x.cn", res["text"])
        self.assertEqual((await self.phone({"t": "ct_export", "fmt": "pdf"}))["why"], "fmt")

        zid = next(c["id"] for c in C.read(self.st)["contacts"] if c.get("name") == "张三")
        res = await self.phone({"t": "ct_rm", "id": zid})
        self.assertEqual((res["ok"], res["result"]), (True, "removed"))
        self.assertIsNone(C.get(C.read(self.st), zid))
        self.assertEqual((await self.phone({"t": "ct_rm", "id": zid}))["why"], "not_found")
        self.assertEqual((await self.phone({"t": "ct_rm"}))["why"], "shape")
        log = self.st.log_path.read_text()
        self.assertNotIn("zhang@x.cn", log, "logs carry counts and result words only")
        self.assertNotIn("张三", log)

    async def test_owner_slot_and_refusals(self):
        C.local(self.st, {"op": "owner_set", "contact": {"name": "我", "emails": ["me@x.cn"]}, "owner_ok": True})
        res = await self.phone({"t": "ct_rm", "id": C.OWNER_ID})
        self.assertEqual((res["ok"], res["why"]), (False, "owner"))
        self.assertIsNotNone(C.owner(C.read(self.st)), "slot 0 stays")
        self.assertIsNone(await self.phone({"t": "ct_rm", "id": C.OWNER_ID}, dev="stranger"), "an unapproved device gets nothing")
        self.assertIsNone(await self.phone({"t": "ct_import", "text": VCF}, dev="stranger"))
        self.assertIsNone(await self.phone({"t": "ct_export"}, dev="stranger"))
        self.assertEqual(len(C.read(self.st)["contacts"]), 1, "nothing imported from a stranger")
        self.assertEqual((await self.phone({"t": "ct_import", "text": ""}))["why"], "empty")
        self.assertEqual((await self.phone({"t": "ct_import", "text": VCF, "fmt": "xls"}))["why"], "shape")
        big = "x" * (1024 * 1024 + 1)
        self.assertEqual((await self.phone({"t": "ct_import", "name": "a.vcf", "text": big}))["why"], "too_big")
        with patch("agentj.contacts.export_text", return_value="x" * (2 * 1024 * 1024)):
            self.assertEqual((await self.phone({"t": "ct_export", "fmt": "vcf"}))["why"], "too_big")

    def test_routed_through_elevate(self):
        from agentj import elevate
        for t in ("ct_rm", "ct_export", "ct_import"):
            self.assertIn(t, elevate.PHONE_TYPES)
            self.assertIn(t, elevate.Elevator.contacts_types)


# ------------------------------------------------------------------ Job B: signed mail_allow
class MailAllow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []

    def got(self, t):
        return [o for _, o in self.sent if o["t"] == t]

    def test_object_text(self):
        self.assertIn("mail_allow", controls.ACTIONS)
        self.assertEqual(controls.object_text("mail_allow", {"addr": "Boss@X.cn", "role": "guest"}), "Boss@X.cn\nguest")
        js = (HERE.parents[1] / "protocol" / "wire.js").read_text()
        self.assertIn("'mail_allow'", js)
        self.assertEqual(js, (HERE.parents[1] / "web" / "public" / "proto" / "wire.js").read_text(), "the page's copy")
        import shutil
        if not shutil.which("node"):
            return
        o = {"addr": "Boss@X.cn", "role": "owner"}
        code = (f"import {{ controlMessage }} from {json.dumps((HERE.parents[1] / 'protocol' / 'wire.js').as_uri())};\n"
                f"console.log(Buffer.from(await controlMessage('CH', 'DEV', 'mail_allow', '{'b' * 32}', 1790000000123, "
                f"{json.dumps(o)})).toString('hex'));")
        r = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        want = controls.signed_message("CH", "DEV", "mail_allow", "b" * 32, 1790000000123, controls.object_digest("mail_allow", o))
        self.assertEqual(r.stdout.strip(), want.hex(), "JS and Python sign the same bytes")

    def test_signed_add(self):
        ph, other = Phone(self.st, "iPhone"), Phone(self.st, "x")
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        s.p33 = True
        host.mail = M.Mail(host)
        own = {"addr": "Boss@Example.com", "role": "owner"}
        guest = {"addr": "friend@example.org", "role": "guest"}
        upper = {"addr": "BOSS@example.com", "role": "guest"}

        async def go():
            await host._app(s, {"t": "mail_allow", "r": "u", **own})                                     # unsigned
            await host._app(s, sign(ph, "mail_allow", own, {"t": "mail_allow", "r": "k", **own}, key=other.sk))
            await host._app(s, sign(ph, "mail_allow", guest, {"t": "mail_allow", "r": "p", **own}))      # re-pointed
            await host._app(s, sign(ph, "mail_allow", own, {"t": "mail_allow", "r": "ok", **own}))
            await host._app(s, sign(ph, "mail_allow", upper, {"t": "mail_allow", "r": "again", **upper}))
            await host._app(s, sign(ph, "mail_allow", guest, {"t": "mail_allow", "r": "g", **guest}))
            bad = {"addr": "not an address", "role": "guest"}
            await host._app(s, sign(ph, "mail_allow", bad, {"t": "mail_allow", "r": "bad", **bad}))
            role = {"addr": "x@example.org", "role": "admin"}
            await host._app(s, sign(ph, "mail_allow", {"addr": "x@example.org", "role": "guest"}, {"t": "mail_allow", "r": "role", **role}))
            s.p33 = False
            await host._app(s, sign(ph, "mail_allow", guest, {"t": "mail_allow", "r": "old", **guest}))
            s.p33 = True
            host.mail = None
            n = {"addr": "n@example.org", "role": "guest"}
            await host._app(s, sign(ph, "mail_allow", n, {"t": "mail_allow", "r": "nomail", **n}))
            await asyncio.sleep(0.05)
        asyncio.run(go())
        res = {o["r"]: o for o in self.got("ctl_res")}
        self.assertTrue(all(o["action"] == "mail_allow" for o in res.values()))
        self.assertEqual([res[r].get("why") for r in ("u", "k", "p")], ["shape", "bad_signature", "bad_signature"])
        self.assertEqual((res["ok"]["ok"], res["ok"].get("already")), (True, None))
        self.assertEqual((res["again"]["ok"], res["again"].get("already")), (True, True), "case-insensitive: already there")
        self.assertTrue(res["g"]["ok"])
        self.assertEqual(res["bad"]["why"], "address")
        self.assertEqual(res["role"]["why"], "shape")
        self.assertNotIn("old", res, "p33 sessions only")
        self.assertEqual(res["nomail"]["why"], "no_mail")
        allow = M.read_config(self.st)["allow"]
        self.assertEqual(allow, [{"addr": "Boss@example.com", "role": "owner"}, {"addr": "friend@example.org", "role": "guest"}])
        self.assertFalse(M.read_config(self.st)["enabled"], "the mailbox itself stays as it was (not configured)")
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "mail_allow"', log)
        self.assertNotIn("example.com", log.lower(), "no address in the activity / state log")
        ctl = self.st.controls_path.read_text()
        self.assertNotIn("example.com", ctl.lower(), "controls.log keeps hashes only")
        self.assertIn('"result": "already"', ctl)


if __name__ == "__main__":
    unittest.main()
