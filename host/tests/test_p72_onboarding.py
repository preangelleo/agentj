"""P72 / F28: first-install onboarding. State machine (no seat / seat, no remote / computer only / phone only / both), the
welcome exactly once (persisted across a restart, never on an upgraded host that already had remotes), one line for later
remotes, the tour note on the next owner messages, the reminder that stops once both required remotes are paired, doctor's
`onboard` row, `agentj onboarding --json`, and the welcome turn through Claude Code / Codex / OpenCode stand-ins."""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import cloud, doctor, gate, main_identity, onboarding, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402
from test_slash import _Chain  # noqa: E402

PC, PHONE = "网页 · Linux Chrome", "网页 · iOS Safari"


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    return st


def _seat(st):
    cloud.write_cloud(st, {"api": "https://agentj.app/api", "host_id": "h1", "tenant": {"slug": "acme-co", "name": "Acme"},
                           "linked_at": 1, "last_seq": 0, "via": "seat"})


class Kinds(unittest.TestCase):
    def test_labels(self):
        for label, kind in [(PHONE, "phone"), ("网页 · Android Chrome", "phone"), ("网页 · iOS 主屏幕", "phone"),
                            ("网页 · Android 主屏幕", "phone"), (PC, "computer"), ("网页 · macOS Safari", "computer"),
                            ("网页 · Windows Edge", "computer"), ("网页 · 浏览器", "other"), ("未命名设备", "other"), ("", "other")]:
            self.assertEqual(onboarding.kind_of(label), kind, label)


class StateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def s(self):
        return onboarding.status(self.st)

    def test_not_activated(self):
        s = self.s()
        self.assertEqual((s["seat"], s["computer"], s["phone"], s["welcomed"], s["next"], s["complete"]),
                         (False, False, False, False, "seat", False))
        self.assertEqual(oct((self.st.root / onboarding.FILE).stat().st_mode & 0o777), "0o600")

    def test_activated_nothing_paired(self):
        _seat(self.st)
        self.assertEqual((self.s()["next"], self.s()["complete"]), ("computer", False))

    def test_computer_only(self):
        _seat(self.st)
        self.assertEqual(onboarding.paired(self.st, "d1", PC), "welcome")
        s = self.s()
        self.assertEqual((s["computer"], s["phone"], s["welcomed"], s["next"]), (True, False, True, "phone"))

    def test_phone_only(self):
        _seat(self.st)
        self.assertEqual(onboarding.paired(self.st, "d1", PHONE), "welcome")
        s = self.s()
        self.assertEqual((s["computer"], s["phone"], s["next"]), (False, True, "computer"))

    def test_both_paired(self):
        _seat(self.st)
        onboarding.paired(self.st, "d1", PHONE)
        self.assertEqual(onboarding.paired(self.st, "d2", PC), "also")
        s = self.s()
        self.assertEqual((s["computer"], s["phone"], s["next"], s["complete"]), (True, True, None, True))

    def test_both_paired_without_seat_still_asks_for_the_seat(self):
        onboarding.paired(self.st, "d1", PHONE)
        onboarding.paired(self.st, "d2", PC)
        self.assertEqual((self.s()["next"], self.s()["complete"]), ("seat", False))

    def test_welcome_once_then_one_line_per_new_device_and_survives_a_restart(self):
        self.assertEqual(onboarding.paired(self.st, "d1", PC), "welcome")
        self.assertIsNone(onboarding.paired(self.st, "d1", PC), "the same device again: nothing")
        st2 = State(self.st.root)                 # serve restarted / package upgraded: same state dir
        self.assertEqual(onboarding.paired(st2, "d2", PHONE), "also")
        self.assertEqual(onboarding.paired(st2, "d3", "网页 · Android Chrome"), "also")
        self.assertIsNone(onboarding.paired(st2, "d3", "网页 · Android Chrome"))
        self.assertEqual(onboarding.read(st2)["phone"]["device"], "d2", "the first phone is the main phone")

    def test_upgraded_host_with_remotes_is_never_welcomed(self):
        self.st.add_device(os.urandom(32), PHONE)
        s = self.s()
        self.assertTrue(s["welcomed"])
        self.assertTrue(s["phone"])
        self.assertEqual(onboarding.paired(self.st, "new", PC), "also")

    def test_corrupt_file_is_rederived_not_rewelcomed(self):
        self.st.add_device(os.urandom(32), PC)
        (self.st.root / onboarding.FILE).write_text("{not json")
        self.assertTrue(self.s()["welcomed"])
        self.assertNotEqual(onboarding.paired(self.st, "x", PHONE), "welcome")


class Notes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        _seat(self.st)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tour_note_then_reminder_every_six_hours_then_silence_once_both_paired(self):
        t0 = 1_800_000_000
        onboarding.paired(self.st, "d1", PC, now=t0)
        notes = [onboarding.turn_note(self.st, "zh", now=t0 + i) for i in range(onboarding.TOUR_TURNS)]
        self.assertTrue(all("新手引导" in n and "主力手机" in n for n in notes), notes)
        n = onboarding.turn_note(self.st, "zh", now=t0 + 100)          # tour over → the first reminder
        self.assertIn("安装还差一步：主力手机", n)
        self.assertEqual(onboarding.turn_note(self.st, "zh", now=t0 + 200), "", "at most once per REMIND_SECS")
        later = t0 + 100 + onboarding.REMIND_SECS
        self.assertIn("主力手机", onboarding.turn_note(self.st, "en", now=later).replace("the main phone", "主力手机"))
        onboarding.paired(self.st, "d2", PHONE, now=later + 1)
        for k in range(3):
            self.assertEqual(onboarding.turn_note(self.st, "zh", now=later + onboarding.REMIND_SECS * (k + 2)), "",
                             "both required remotes paired: never mentioned again")

    def test_tour_expires_by_time(self):
        t0 = 1_800_000_000
        onboarding.paired(self.st, "d1", PC, now=t0)
        n = onboarding.turn_note(self.st, "en", now=t0 + onboarding.TOUR_SECS + 1)
        self.assertNotIn("tour", n)
        self.assertIn("Setup still needs: the main phone", n)

    def test_no_note_before_the_first_remote_or_on_an_upgraded_host(self):
        self.assertEqual(onboarding.turn_note(self.st, "zh", now=1_800_000_000), "", "the installing Agent leads until then")
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            st.add_device(os.urandom(32), PHONE)        # remotes before F28 → legacy: never nagged about the rest
            self.assertEqual(onboarding.turn_note(st, "zh", now=1_800_000_000), "")

    def test_reminder_points_at_this_computers_browser_and_at_a_missing_seat_first(self):
        t0 = 1_800_000_000
        onboarding.paired(self.st, "d1", PHONE, now=t0)
        n = onboarding.turn_note(self.st, "zh", now=t0 + onboarding.TOUR_SECS + 1)
        self.assertIn("这台电脑的浏览器", n)
        self.assertIn("agentj admin", n)
        os.unlink(self.st.cloud_path)
        n = onboarding.turn_note(self.st, "zh", now=t0 + onboarding.TOUR_SECS + 2 + onboarding.REMIND_SECS)
        self.assertIn("agentj login --seat-file", n)

    def test_texts(self):
        s = {"computer": True, "phone": False, "seat": True}
        for lang in ("zh", "en"):
            w = onboarding.welcome_prompt("computer", PC, s, lang)
            self.assertIn(onboarding.MANUAL[lang], w)
            self.assertIn("董事长助理" if lang == "zh" else "chief-of-staff", w)
            self.assertIn("跳过" if lang == "zh" else "skip", w)
            self.assertIn("主力手机" if lang == "zh" else "the main phone", w)
            for word in ("绿", "橙", "紫") if lang == "zh" else ("green", "orange", "purple"):
                self.assertIn(word, w)
        both = {"computer": True, "phone": True, "seat": True}
        self.assertIn("都配好了", onboarding.welcome_prompt("phone", PHONE, both, "zh"))
        self.assertEqual(onboarding.also_text("phone", PHONE, both, "zh"),
                         "这台（主力手机）「网页 · iOS Safari」也连上了。两个必做的遥控器（这台电脑的浏览器、主力手机）都配好了。")
        self.assertTrue(onboarding.also_text("other", "", both, "en").startswith("This remote is connected too."))
        self.assertIn("董事长助理", onboarding.fallback_welcome("computer", s, "zh"))


class DoctorAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_doctor_row(self):
        r = doctor.check_onboarding(self.st)
        self.assertEqual((r["id"], r["status"]), ("onboard", "warn"))
        self.assertIn("agentj login", r["hint"])
        _seat(self.st)
        onboarding.paired(self.st, "d1", PHONE)
        self.assertIn("passkey", doctor.check_onboarding(self.st)["hint"])
        onboarding.paired(self.st, "d2", PC)
        r = doctor.check_onboarding(self.st)
        self.assertEqual(r["status"], "ok")
        self.assertIn("主力手机 ✓", r["summary"])
        self.assertEqual(doctor.check_onboarding(State(pathlib.Path(self.tmp.name) / "none"))["status"], "warn")

    def test_cli_json(self):
        _seat(self.st)
        onboarding.paired(self.st, "d1", PC)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(onboarding.command(SimpleNamespace(json=True), self.st), 0)
        self.assertEqual(json.loads(out.getvalue()), {"seat": True, "computer": True, "phone": False, "welcomed": True,
                                                      "next": "phone", "complete": False})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            onboarding.command(SimpleNamespace(json=False), self.st)
        self.assertIn("下一步 / next:", out.getvalue())

    def test_identity_line_both_languages(self):
        for lang in ("zh", "en"):
            p = main_identity.prompt({"kind": "claude", "language": lang})
            self.assertIn(main_identity.ONBOARDING_LINE[lang], p)
            self.assertIn("agentj onboarding", p)
        main_identity.verify_core()        # the hashed core is unchanged (v5)


class Serve(unittest.TestCase):
    """Through the real approval path (Host.decide), with a recording stand-in Agent."""
    PASS = "test-passphrase-P72"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        gate.set_passphrase(self.st, self.PASS)
        _seat(self.st)
        onboarding.read(self.st)                     # a fresh install: the onboarding file exists before any remote
        self.host = serve.Host(self.st, events="quiet", read_stdin=False)
        self.sent = []
        self.submitted = []

        async def send_app(s, obj):
            self.sent.append((s.cid, obj))
            return True

        async def op(o, cid, payload=b""):
            pass
        self.host.send_app, self.host._op = send_app, op
        self.host.agent = SimpleNamespace(submit=self.submitted.append, kind="claude", status="idle",
                                          cfg={}, passthrough=lambda n: False)

    def tearDown(self):
        self.tmp.cleanup()

    async def _pair(self, cid, name):
        pub = os.urandom(32)
        s = serve.Session(cid=cid, state="pending", device=wire.device_id(pub), name=name, pub=pub, h=os.urandom(32))
        s.deadline = time.monotonic() + 60
        self.host.sessions[cid] = s
        p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=cid)
        self.host.pairing = p
        res = await self.host.decide(p, wire.safety_code(s.h), self.PASS)
        self.assertEqual(res["ev"], "approved")
        for _ in range(200):
            await asyncio.sleep(0.01)
        return s

    def test_first_pairing_asks_the_agent_for_the_welcome_second_gets_one_line(self):
        async def go():
            await self._pair(7, PC)
            self.assertEqual(len(self.submitted), 1)
            w = self.submitted[0]
            self.assertIsInstance(w, onboarding.WelcomeSend)
            self.assertEqual((w.turn, w.by), (0, "Agent J"))
            self.assertIn("董事长助理", w.text)
            self.assertIn("这台电脑的浏览器", w.text)
            await self._pair(8, PHONE)
            self.assertEqual(len(self.submitted), 1, "no second welcome")
            lines = [t for t in self.host.hist.page(limit=50)[0] if t["src"].get("k") == "sys"
                     and "也连上了" in t["reply"].get("text", "")]  # unrelated OS residency reminders are covered by P94
            self.assertEqual(len(lines), 1)
            self.assertIn("也连上了", lines[0]["reply"]["text"])
            self.assertIn("都配好了", lines[0]["reply"]["text"])
            # the owner's next message carries the tour note for the Agent, never on the phone's page
            s = self.host.sessions[8]
            await self.host._app(s, {"t": "msg", "id": "a" * 16, "text": "好", "ts": 0})
            say = self.submitted[-1]
            self.assertTrue(say.text.startswith("好"))
            self.assertIn("新手引导", say.text)
            page = self.host.hist.get(say.turn)
            self.assertEqual(page["src"]["text"], "好")
        asyncio.run(go())

    def test_no_agent_posts_the_fixed_welcome_once(self):
        self.host.agent = None

        async def go():
            await self._pair(7, PHONE)
            pages = self.host.hist.page(limit=50)[0]
            self.assertEqual([t["src"]["k"] for t in pages], ["agent"])
            self.assertIn("董事长助理", pages[0]["reply"]["text"])
            await self._pair(9, PC)
            self.assertEqual([t["src"]["k"] for t in self.host.hist.page(limit=50)[0]], ["agent", "sys"])
        asyncio.run(go())

    def test_restarted_serve_does_not_welcome_again(self):
        async def go():
            await self._pair(7, PC)
            self.host = serve.Host(self.st, events="quiet", read_stdin=False)
            self.setUp_host_again()
            await self._pair(8, PC)
            self.assertEqual(self.submitted, [])
        asyncio.run(go())

    def setUp_host_again(self):
        self.submitted = []

        async def send_app(s, obj):
            return True

        async def op(o, cid, payload=b""):
            pass
        self.host.send_app, self.host._op = send_app, op
        self.host.agent = SimpleNamespace(submit=self.submitted.append, kind="claude", status="idle", cfg={},
                                          passthrough=lambda n: False)


class _Welcome(_Chain):
    """The welcome turn reaches each harness (stand-ins echo their input) and the reply lands as the Agent's own page."""

    def test_welcome_reaches_the_harness_and_the_phone(self):
        if self.KIND == "?":
            self.skipTest("base")
        onboarding.read(self.st)        # created before any remote: a fresh install

        async def script(c):
            host, s = c["host"], c["s"]
            s.name = PHONE
            await host.onboarding_paired(s)
            await c["wait"](lambda: any("董事长助理" in m["text"] for m in c["msgs"](("agent",))))
            await c["idle"]()
            await host.onboarding_paired(s)          # the same device again (e.g. resume): nothing more
            await asyncio.sleep(0.3)
            self.assertEqual(sum("董事长助理" in m["text"] for m in c["msgs"](("agent",))), 1)
        self.run_chain(script)


class WelcomeClaude(_Welcome):
    KIND = "claude"


class WelcomeCodex(_Welcome):
    KIND = "codex"


class WelcomeOpenCode(_Welcome):
    KIND = "opencode"


del _Welcome

if __name__ == "__main__":
    unittest.main()
