"""P203 (PROTOCOL §29): the open-cards snapshot and the upgrade confirmation card.

- cards_get is a signed read (control-v1, action cards_get): unsigned / wrong key / no approval key → refused, nothing sent;
- a card made while no phone was connected reaches a phone that asks; cards it lists in `have` are not sent again; `open`
  lists every open id, so a card answered on another remote is gone from the next answer;
- the pair card only to a pair1 remote; a question and a batch grant are in the snapshot;
- upgrade impact: counts and kinds (running turn, queue, cards, tasks), the Agent's own turn not listed for caller=agent;
- the upgrade card goes to the phones, is in the snapshot, ends on the phone's signed /update (started) or 「取消」;
- `agentj update apply` from an Agent with something to interrupt installs nothing and puts the card up; at a terminal
  y/N; --yes skips; no serve → the old path;
- 「空闲时自动更新」: a newer version the daily check saw is installed the first idle time, once a day, never while busy.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import auto_update, controls, pair_card, serve, update  # noqa: E402

from test_l1 import Phone, _owner_host as _host, _ready, _state  # noqa: E402
from test_controls import sign  # noqa: E402


class FakeAgent:
    native_source = {"kind":"terminal","role":"owner"}
    kind, status, halting = "claude", "idle", False

    def __init__(self):
        self.q = SimpleNamespace(qsize=lambda: 0, empty=lambda: True)

    def submit(self, x):
        pass

    async def halt(self, clear_queue=True):
        return True

    async def stop(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.cfg = pathlib.Path(self.tmp.name) / "claudecfg"
        self.cfg.mkdir()
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cfg)})
        self.env.start()
        self.st.set_agent_config("claude", str(self.work))
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.host.agent = FakeAgent()
        self.host.ask_ttl = 30

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def got(self, t, r=None, cid=None):
        return [o for c, o in self.sent if o["t"] == t and (r is None or o.get("r") == r) and (cid is None or c == cid)]

    def ready(self, ph, cid=11, p33=True, pair1=False):
        s = _ready(self.host, ph, cid=cid)
        s.p33, s.pair1 = p33, pair1
        return s

    def cards_get(self, ph, r, have=(), **kw):
        return sign(ph, "cards_get", {}, {"t": "cards_get", "r": r, "have": list(have)}, **kw)


class Snapshot(Base):
    def test_refused_without_a_valid_signature(self):
        ph, other = Phone(self.st), Phone(self.st, "别的")
        nokey = Phone(self.st, "无钥", with_key=False)
        s, s2 = self.ready(ph), self.ready(nokey, cid=12)

        async def go():
            await self.host._app(s, {"t": "cards_get", "r": "u1", "have": []})
            await self.host._app(s, self.cards_get(ph, "w1", key=other.sk))
            await self.host._app(s2, self.cards_get(nokey, "k1"))
            await self.host._app(s, self.cards_get(ph, "o1", ts=int(time.time() * 1000) - 600_000))
            await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertEqual([(o["r"], o["ok"], o["why"]) for o in self.got("cards")],
                         [("u1", False, "shape"), ("w1", False, "bad_signature"), ("k1", False, "no_key"), ("o1", False, "stale")])
        lines = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertTrue(all(x["action"] == "cards_get" and x["result"].startswith("refused:") for x in lines))
        # a phone without p33 never gets an answer at all
        s.p33 = False
        asyncio.run(self.host._app(s, self.cards_get(ph, "n1")))
        self.assertEqual(self.got("cards", "n1"), [])

    def test_card_made_offline_arrives_and_answered_elsewhere_disappears(self):
        a, b = Phone(self.st, "手机"), Phone(self.st, "平板")

        async def go():
            # no remote connected: the approval card waits (the phones are paired and can sign)
            t = asyncio.create_task(self.host.ask("Bash", {"command": "touch x"}))
            for _ in range(200):
                if self.host.asks:
                    break
                await asyncio.sleep(0.01)
            rid = next(iter(self.host.asks))
            self.assertEqual(self.got("ask"), [], "nobody was connected")
            sa = self.ready(a, cid=21)
            await self.host._app(sa, self.cards_get(a, "c1"))
            await asyncio.sleep(0.05)
            asks = self.got("ask", cid=21)
            self.assertEqual([m["id"] for m in asks], [rid], "the missing card is sent")
            self.assertEqual(self.got("cards", "c1")[0]["open"], [rid])
            # the same request listing the card as shown: only the id list
            await self.host._app(sa, self.cards_get(a, "c2", have=[rid]))
            await asyncio.sleep(0.05)
            self.assertEqual(len(self.got("ask", cid=21)), 1)
            self.assertEqual(self.got("cards", "c2")[0]["open"], [rid])
            # the tablet connects and approves; the phone's next snapshot no longer lists the card
            sb = self.ready(b, cid=22)
            await self.host._app(sb, b.answer(asks[0], True))
            ans = await asyncio.wait_for(t, 5)
            self.assertEqual(ans["behavior"], "allow")
            await self.host._app(sa, self.cards_get(a, "c3", have=[rid]))
            await asyncio.sleep(0.05)
            self.assertEqual(self.got("cards", "c3")[0]["open"], [])
        asyncio.run(go())
        self.assertIn('"ev": "cards_snap"', self.st.log_path.read_text())

    def test_pair_card_question_grant_and_upgrade_card_in_the_snapshot(self):
        ph = Phone(self.st)
        s = self.ready(ph)
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            mono = time.monotonic()
            self.host.asks["p" * 32] = serve.Ask("p" * 32, pair_card.TOOL, "配对", "d", {}, "", mono + 60, fut)
            self.host.questions["q" * 32] = serve.Question("q" * 32, [{"q": "哪个", "o": [{"l": "A"}]}], "d", mono + 60, loop.create_future())
            self.host.grants["g" * 32] = serve.Grant("g" * 32, "Bash", ("bash", ("ls",)), "Bash：ls", ph.did, 5, mono + 60)
            ids = [m["id"] for m in self.host.open_cards(s)]
            self.assertNotIn("p" * 32, ids, "the pair card only goes to a pair1 remote")
            self.assertIn("q" * 32, ids)
            self.assertIn("g" * 32, ids)
            s.pair1 = True
            self.assertIn("p" * 32, [m["id"] for m in self.host.open_cards(s)])
            self.host.upg = {"id": "u" * 32, "deadline": time.monotonic() + 60, "caller": None, "to": ""}
            m = [x for x in self.host.open_cards(s) if x["t"] == "upg_card"]
            self.assertEqual(m[0]["id"], "u" * 32)
            s.p33 = False                    # an old page: neither questions nor the upgrade card
            self.assertEqual({x["t"] for x in self.host.open_cards(s)}, {"ask", "grant"})
        finally:
            loop.close()


class Upgrade(Base):
    def test_impact_lists_what_a_restart_interrupts(self):
        self.assertEqual(auto_update.impact(self.host), [])
        self.host.agent.status = "working"
        self.host.agent.q = SimpleNamespace(qsize=lambda: 2, empty=lambda: False)
        self.host.scheduler.current_id = "daily"
        loop = asyncio.new_event_loop()
        try:
            self.host.asks["a" * 32] = serve.Ask("a" * 32, "Bash", "rm", "d", {}, "", time.monotonic() + 60, loop.create_future())
            got = {i["k"]: i["n"] for i in auto_update.impact(self.host)}
            self.assertEqual(got, {"turn": 1, "queue": 2, "ask": 1, "task": 1})
            self.assertNotIn("turn", {i["k"] for i in auto_update.impact(self.host, "agent")}, "the Agent's own turn")
            self.host.agent_cfg["session_mode"] = "shared"
            self.assertIn("shared_turn", {i["k"] for i in auto_update.impact(self.host)})
            lines = auto_update.impact_text(auto_update.impact(self.host), "zh")
            self.assertIn("排队等待的 2 条消息", lines)
            self.assertIn("1 张等待中的审批卡", lines)
            self.assertIn("1 approval card(s) waiting", auto_update.impact_text(auto_update.impact(self.host), "en"))
        finally:
            loop.close()

    def test_upg_check_card_update_and_cancel(self):
        ph = Phone(self.st)
        s = self.ready(ph)

        async def go():
            await self.host._app(s, {"t": "upg_check", "r": "k1"})
            ans = self.got("upg_impact", "k1")[0]
            self.assertEqual(ans["items"], [])
            self.assertEqual(ans["current"], update.__version__)
            self.host.agent.status = "working"
            res = self.host.upg_open()
            await asyncio.sleep(0.05)
            card = self.got("upg_card")[0]
            self.assertEqual(card["id"], res["id"])
            self.assertEqual([i["k"] for i in card["items"]], ["turn"])
            self.assertEqual(res["phones"], 1)
            # cancel: narrows only, any ready remote; the card ends everywhere
            await self.host._app(s, {"t": "upg_card_off", "id": res["id"]})
            await asyncio.sleep(0.05)
            self.assertIsNone(self.host.upg)
            self.assertEqual(self.got("upg_card_done")[-1], {"t": "upg_card_done", "id": res["id"], "result": "cancelled"})
            # a new card answered by the phone's signed /update: started (the upgrade itself is P142's path, not run here)
            res2 = self.host.upg_open()
            with mock.patch("agentj.phone_update.reserve", return_value=True), \
                 mock.patch.object(serve.Host, "phone_upgrade", new=lambda self_, turn, by: asyncio.sleep(0)):
                await self.host._app(s, sign(ph, "update", {}, {"t": "slash", "cmd": "update"}))
            await asyncio.sleep(0.05)
            self.assertEqual(self.got("upg_card_done")[-1]["result"], "started")
            self.assertEqual(self.got("upg_card_done")[-1]["id"], res2["id"])
        asyncio.run(go())

    def test_ctl_items_skip_the_agents_own_turn(self):
        self.host.agent.status = "working"
        self.assertEqual([i["k"] for i in self.host.upg_items()], ["turn"])
        self.assertEqual(self.host.upg_items("agent"), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_gate(self, answers, env=None, tty=False, inp=""):
        from agentj import cli
        calls = []

        def ctl(st, req, timeout=None):
            calls.append(req)
            return answers.get(req["cmd"])
        out = io.StringIO()
        with mock.patch("agentj.names.ctl_call", ctl), mock.patch.dict(os.environ, env or {}), \
             mock.patch("sys.stdout", out), mock.patch("sys.stdin", io.StringIO(inp)), \
             mock.patch("sys.stdin.isatty", return_value=tty, create=True), mock.patch("sys.stdout.isatty", return_value=tty, create=True), \
             mock.patch("builtins.input", side_effect=lambda p: inp):
            res = cli._upgrade_gate({"status": "newer", "latest": "9.9.9"})
        return res, calls, out.getvalue()

    def test_agent_with_something_to_interrupt_gets_the_card(self):
        items = [{"k": "ask", "n": 1}]
        res, calls, _ = self.run_gate({"upgrade_impact": {"ok": True, "items": items}, "upgrade_card": {"ok": True, "id": "x"}},
                                      env={"CLAUDECODE": "1"})
        self.assertEqual(res["reason"], "confirm_on_phone")
        self.assertEqual([c["cmd"] for c in calls], ["upgrade_impact", "upgrade_card"])
        self.assertEqual(calls[0]["caller"], "agent")
        block = update.result_block(res)
        self.assertIn("UPGRADE_RESULT refused", block)
        self.assertIn("interrupts: 1 张等待中的审批卡", block)
        self.assertEqual(update._exit_of(res["reason"]), 10)

    def test_nothing_to_interrupt_or_no_serve_goes_on(self):
        self.assertIsNone(self.run_gate({"upgrade_impact": {"ok": True, "items": []}}, env={"CLAUDECODE": "1"})[0])
        self.assertIsNone(self.run_gate({}, env={"CLAUDECODE": "1"})[0], "no serve: the old path")

    def test_terminal_asks_y_n(self):
        ans = {"upgrade_impact": {"ok": True, "items": [{"k": "turn", "n": 1}]}}
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "OPENCODE", "AGENTJ_PERM_TOKEN", "AGENTJ_PERM_SOCK",
                      "AGENTJ_FENCED", "CODEX_SANDBOX_NETWORK_DISABLED"):
                os.environ.pop(k, None)
            res, calls, out = self.run_gate(ans, tty=True, inp="n")
            self.assertEqual(res["reason"], "not_confirmed")
            self.assertIn("Agent J 正在进行的回合", out)
            self.assertEqual([c["cmd"] for c in calls], ["upgrade_impact"], "no card for a terminal owner")
            self.assertIsNone(self.run_gate(ans, tty=True, inp="y")[0])


class IdleAutoUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, hour, minute=0):
        return time.mktime(time.strptime(f"2026-10-13 {hour:02d}:{minute:02d}:00", "%Y-%m-%d %H:%M:%S"))

    def test_once_a_day_only_when_idle_and_newer(self):
        noon = self.at(12)
        rand = lambda n: 0                                   # noqa: E731 — the night opportunity at 03:00
        self.assertFalse(auto_update.due(self.st, now=noon, rand=rand), "no newer version known")
        auto_update.touch(self.st, now=noon - 60)
        self.assertFalse(auto_update.due(self.st, now=noon, rand=rand, newer=True), "phone used a minute ago")
        later = noon + auto_update.IDLE
        self.assertFalse(auto_update.due(self.st, now=later, rand=rand, newer=True, blocked=True), "busy")
        self.assertFalse(auto_update.due(self.st, now=later, rand=rand, newer=True, stopped=True), "stopped")
        self.assertTrue(auto_update.due(self.st, now=later, rand=rand, newer=True))
        self.assertEqual(auto_update.read(self.st)["decision"], "idle")
        self.assertFalse(auto_update.due(self.st, now=later + 60, rand=rand, newer=True), "once a day")
        with mock.patch.object(auto_update, "enabled", return_value=False):
            auto_update.write(self.st, {**auto_update.read(self.st), "idle_day": ""})
            self.assertFalse(auto_update.due(self.st, now=later + 120, rand=rand, newer=True), "switched off")

    def test_notice_says_while_idle(self):
        r = {"result": "ok", "to": "0.20.2", "when": "idle"}
        self.assertIn("Agent J 主机空闲时已自动升级到 0.20.2", auto_update.notice(r))
        self.assertIn("while idle", auto_update.notice(r, "en"))
        self.assertIn("夜里", auto_update.notice({"result": "ok", "to": "0.20.2"}))

    def test_newer_known_reads_the_daily_record(self):
        self.assertFalse(auto_update.newer_known(self.st))
        (self.st.root / "update.json").write_text(json.dumps({"latest": "999.0.0"}))
        self.assertTrue(auto_update.newer_known(self.st))
        (self.st.root / "update.json").write_text(json.dumps({"latest": update.__version__}))
        self.assertFalse(auto_update.newer_known(self.st))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_sign_cards_get_the_same(self):
        here = pathlib.Path(__file__).resolve().parent
        for wire_js in (here.parents[1] / "protocol" / "wire.js", here.parents[1] / "web" / "public" / "proto" / "wire.js"):
            js = (f"import {{ controlMessage }} from {json.dumps(wire_js.as_uri())};\n"
                  f"console.log(Buffer.from(await controlMessage('CH', 'DEV', 'cards_get', '{'c' * 32}', 1790000000123, {{}})).toString('hex'));")
            r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = controls.signed_message("CH", "DEV", "cards_get", "c" * 32, 1790000000123, controls.object_digest("cards_get", {})).hex()
            self.assertEqual(r.stdout.strip(), want, wire_js)

    def test_auto_install_is_phone_settable(self):
        self.assertIn("updates.auto_install", serve.PREF_SET_KEYS)
        self.assertIn("cards_get", controls.ACTIONS)
        self.assertEqual(controls.object_text("cards_get", {}), "cards")


if __name__ == "__main__":
    unittest.main()
