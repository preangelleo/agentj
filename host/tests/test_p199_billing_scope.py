"""P199: the account's own services need a usable Agent J seat on the host too — plaza (search / install / publish / reply),
support, friends and public bots ask the same seat verdict as P185 (seatcert: same 72-hour grace, same cache), say where to
renew or bind, and send nothing. The owner's local Agent, terminal and scheduled tasks never ask it.

Cases: usable (ok) · unbound · suspended · within the bootstrap grace (bound, cloud never answered) · beyond it (expired) ·
loopback exempt. No network: the Dashboard is a fake; friends / bots run on stand-ins.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import cloud, peer_service, plaza, seatcert, serve, support, wire  # noqa: E402
from agentj.peer_service import PeerService  # noqa: E402
from agentj.state import State  # noqa: E402

PROD_RELAY = "wss://relay.agentj.app"
H = 3600
T0 = 1_800_000_000
PKG = pathlib.Path(__file__).resolve().parents[1] / "agentj"


def bind(st):
    cloud.write_cloud(st, {"api": "https://agentj.example/api", "host_id": "h_199", "tenant": {"slug": "acme", "name": "Acme"},
                           "linked_at": 1, "last_seq": 0})


def fake_dash(mode):
    def post(url, payload):
        if mode == "down":
            raise cloud.CloudError("network")
        if mode == "suspended":
            return 402, {"error": "agent_suspended", "checkout": True}
        inner = json.loads(wire.unb64u(payload["body"]))
        exp = inner["ts"] + 72 * H
        p = wire.b64u(json.dumps({"v": 1, "ch": inner["channel"], "exp": exp}, separators=(",", ":")).encode())
        return 200, {"cert": p + "." + "A" * 86, "exp": exp}
    return post


class Seat(unittest.TestCase):
    """The six seat states, as `seatcert.refusal` (CLI processes) sees them through the serve's cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay=PROD_RELAY)
        os.environ.pop("AGENTJ_TEST_SEAT_ENFORCE", None)

    def state(self, kind):
        """Put this host in one seat state (as serve's seat_loop would have left the cache)."""
        clock = lambda: T0  # noqa: E731
        if kind == "unbound":
            return
        bind(self.st)
        if kind == "grace":                  # bound, the cloud never answered: the 72-hour bootstrap grace
            seatcert.SeatCert(self.st, PROD_RELAY, post=fake_dash("down"), clock=clock).refresh(now=time.time())
        elif kind == "expired":              # bound, the cloud has not answered for longer than the grace
            seatcert.SeatCert(self.st, PROD_RELAY, post=fake_dash("down"), clock=clock).refresh(now=time.time() - 73 * H)
        else:
            seatcert.SeatCert(self.st, PROD_RELAY, post=fake_dash(kind), clock=clock).refresh(now=time.time())

    def test_verdicts_and_wording(self):
        url = cloud.app_url(self.st)
        for kind, allowed in (("ok", True), ("grace", True), ("unbound", False), ("suspended", False), ("expired", False)):
            with self.subTest(kind=kind):
                self.tearDown_state()
                self.state(kind)
                for feature, zh in (("plaza", "Agent 广场"), ("support", "Agent J 客服"), ("friends", "Agent 好友"), ("bots", "对外客服 bot")):
                    msg = seatcert.refusal(self.st, feature)
                    if allowed:
                        self.assertIsNone(msg)
                        continue
                    self.assertTrue(msg.startswith(zh + "需要有效的 Agent J 席位。"), msg)
                    self.assertIn("\n", msg, "zh line, then en line")
                    if kind == "expired":
                        self.assertIn("agentj doctor", msg)
                    else:
                        self.assertIn(url, msg, "says where to renew / bind")
                    self.assertNotIn("A" * 20, msg, "never the certificate")

    def tearDown_state(self):
        for p in (pathlib.Path(self.st.root) / "seat" / "cert.json", pathlib.Path(self.st.root) / "cloud.json"):
            p.unlink(missing_ok=True)

    def test_loopback_relay_is_exempt(self):
        st = State(pathlib.Path(self.tmp.name) / "loop")
        st.init(relay="ws://127.0.0.1:9")
        self.assertIsNone(seatcert.refusal(st, "plaza"))
        os.environ["AGENTJ_TEST_SEAT_ENFORCE"] = "1"
        self.addCleanup(os.environ.pop, "AGENTJ_TEST_SEAT_ENFORCE", None)
        self.assertIsNotNone(seatcert.refusal(st, "plaza"))

    def test_one_language_for_serve(self):
        self.assertEqual(seatcert.feature_message("friends", "suspended", "en", "https://x.example/account").split(" the Agent J host")[0],
                         "Agent friends needs a valid Agent J seat.")
        self.assertNotIn("\n", seatcert.feature_message("friends", "suspended", "zh", "u"))

    # ------------------------------------------------------------ plaza
    def run_cmd(self, argv):
        import argparse
        ap = argparse.ArgumentParser()
        plaza.add_parser(ap.add_subparsers(dest="cmd"))
        a = ap.parse_args(argv)
        err = io.StringIO()
        with mock.patch.object(plaza, "_state", lambda: self.st), \
                mock.patch.object(cloud, "plaza_call", side_effect=AssertionError("no plaza call without a seat")) as pc, \
                redirect_stderr(err), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ex:
            plaza.cmd(a)
        return ex.exception.code, err.getvalue(), pc

    def test_plaza_refused_without_a_seat_nothing_sent(self):
        for kind in ("unbound", "suspended", "expired"):
            self.tearDown_state()
            self.state(kind)
            for argv in (["plaza", "search", "pdf"], ["plaza", "show", "p_" + "a" * 22], ["plaza", "mine"],
                         ["plaza", "install", "srt-to-text"], ["plaza", "resolve", "p_" + "a" * 22]):
                with self.subTest(kind=kind, argv=argv):
                    code, err, pc = self.run_cmd(argv)
                    self.assertEqual(code, plaza.EXIT_SEAT)
                    self.assertIn("Agent 广场需要有效的 Agent J 席位", err)
                    pc.assert_not_called()

    def test_plaza_installed_list_stays_local(self):
        self.state("unbound")
        code, err, _ = self.run_cmd(["plaza", "installed"])
        self.assertEqual(code, 0, err)

    def test_plaza_passes_with_a_seat_and_in_the_grace(self):
        for kind in ("ok", "grace"):
            with self.subTest(kind=kind):
                self.tearDown_state()
                self.state(kind)
                plaza.seat_gate(self.st)        # no raise

    # ------------------------------------------------------------ support
    def test_support_bound_without_a_seat_refused_before_any_call(self):
        for kind in ("suspended", "expired"):
            with self.subTest(kind=kind):
                self.tearDown_state()
                self.state(kind)
                with self.assertRaises(support.SupportSeatError) as e:
                    support.channel_for(self.st, post=lambda *a: self.fail("no call"))
                self.assertIn("Agent J 客服需要有效的 Agent J 席位", str(e.exception))
        err = io.StringIO()
        with mock.patch.object(support, "channel_for", side_effect=support.SupportSeatError("x")), redirect_stderr(err), \
                self.assertRaises(SystemExit) as ex:
            support._run(lambda: support.run_send("question", "hi", wait=0))
        self.assertEqual(ex.exception.code, support.EXIT_SEAT)

    def test_support_with_a_seat_or_in_the_grace_signs(self):
        for kind in ("ok", "grace"):
            with self.subTest(kind=kind):
                self.tearDown_state()
                self.state(kind)
                self.assertTrue(support.channel_for(self.st).signed)

    def test_support_install_session_is_not_a_seat_feature(self):
        with mock.patch.object(support, "_install_id", lambda: "aji_" + "x" * 43):
            ch = support.channel_for(self.st)
        self.assertFalse(ch.signed)
        self.assertIn("payment_required", support.ERRORS)


# ---------------------------------------------------------------- friends (serve side; the CLI talks to it on friends.sock)
from test_p71_peer_service import FakeHost, FakeNet, FakeSessions  # noqa: E402


class Friends(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ[peer_service.TEST_CERT_ENV] = wire.b64u(os.urandom(32))
        self.addCleanup(os.environ.pop, peer_service.TEST_CERT_ENV, None)
        self.host = FakeHost(self.tmp.name)
        self.refusal = None
        self.host.seat_refusal = lambda feature=None: self.refusal
        self.svc = PeerService(self.host, sessions=FakeSessions(), net_factory=FakeNet)
        self.svc.HOUSEKEEP = 3600
        await self.svc.start()
        self.addAsyncCleanup(self.svc.stop)

    async def test_mailbox_down_while_refused_back_when_the_seat_returns(self):
        self.assertEqual(self.svc.state, "connecting")
        net = self.svc.net
        self.refusal = "Agent 好友需要有效的 Agent J 席位。…"
        await self.svc.apply()
        self.assertEqual(self.svc.state, "need_seat")
        self.assertTrue(self.svc.seat_blocked)
        self.assertIsNone(self.svc.net, "no mailbox: nothing in, nothing out")
        self.assertTrue(net._stop.is_set())
        self.assertIn('"result": "blocked"', self.host.st.log_path.read_text())
        self.refusal = None
        await self.svc.apply()
        self.assertEqual(self.svc.state, "connecting")
        self.assertFalse(self.svc.seat_blocked)

    async def test_add_and_tell_refused_with_the_reason(self):
        self.refusal = "Agent 好友需要有效的 Agent J 席位。到账户页续费"
        res = await self.svc.cli({"t": "add", "id": "x"})
        self.assertEqual((res["ok"], res["why"]), (False, "seat"))
        self.assertIn("续费", res["message"])
        res = await self.svc.cli({"t": "tell", "friend": "小鹿", "text": "你好"})
        self.assertEqual((res["ok"], res["why"], res["message"]), (False, "seat", self.refusal))
        ok, why, _ = await self.svc.write("fr_add", {"id": "x", "note": ""})
        self.assertEqual((ok, why), (False, "seat"))
        # local settings and reading stay available
        self.assertTrue((await self.svc.cli({"t": "list"}))["ok"])
        self.assertTrue((await self.svc.cli({"t": "groups"}))["ok"])
        self.assertEqual(peer_service.WHY_ZH["seat"], "Agent 好友需要有效的 Agent J 席位")

    async def test_a_host_without_seat_refusal_is_unaffected(self):
        del self.host.seat_refusal
        self.assertIsNone(self.svc.seat_refusal())


class FriendsServeWording(unittest.TestCase):
    def test_serve_feature_wording_uses_the_same_verdict(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name) / "s")
        st.init(relay=PROD_RELAY)
        host = serve.Host(st, events="quiet", read_stdin=False)
        self.assertTrue(host.seat_refusal("friends").startswith("Agent 好友需要有效的 Agent J 席位。"))
        self.assertTrue(host.seat_refusal().startswith("这台Agent J 主机还没有绑定 Agent J 席位"), "P185 wording unchanged without a feature")
        bind(st)
        host.seat.post = fake_dash("ok")
        host.seat.refresh()
        self.assertIsNone(host.seat_refusal("bots"))


# ---------------------------------------------------------------- bots
class Bots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from agentj.bots.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.store = Store(root / "bots")
        self.addCleanup(self.store.close)
        self.bid = self.store.create({"slug": "sample-shop", "enabled": True, "terms_accepted": True})["id"]
        self.refusal = None
        self.logs = []
        self.host = SimpleNamespace(st=SimpleNamespace(root=root, log=lambda ev, **k: self.logs.append((ev, k))),
                                    stopped=lambda: False, seat_refusal=lambda feature=None: self.refusal, agent=None,
                                    agent_cfg={"kind": "claude"})

    async def one_round(self):
        from agentj.bots import runtime as rt
        r = rt.Runtime(SimpleNamespace(host=self.host, state=lambda: self.store, approve=None, handoff=None))
        served, registered = [], []

        def serve_bot(bid):                  # recorded when the runtime decides to serve (the task itself is cancelled at stop)
            served.append(bid)
            return asyncio.sleep(0)

        async def register(bid):
            registered.append(bid)
            return {}, 1

        async def stop_after_one(_):
            r.stopping = True
        r.serve_bot, r.register = serve_bot, register
        with mock.patch.object(rt.cloud, "read_cloud", lambda st: {"host_id": "h"}), mock.patch.object(rt.asyncio, "sleep", stop_after_one):
            await r.run()
        return served, registered

    async def test_public_bot_paused_while_refused(self):
        self.refusal = "对外客服 bot 需要有效的 Agent J 席位。"
        self.assertEqual(await self.one_round(), ([], []), "not served, not registered (the 5-minute public lease runs out)")
        self.assertIn(("bots", {"outcome": "seat_paused"}), self.logs)

    async def test_public_bot_served_with_a_seat(self):
        served, registered = await self.one_round()
        self.assertEqual(registered, [self.bid])
        self.assertEqual(served, [self.bid])

    async def test_going_public_refused_editing_stays_local(self):
        from agentj.bots.owner import Manager
        from agentj.bots.store import BotError
        m = Manager.__new__(Manager)
        m.host, m.store = self.host, self.store
        self.refusal = "对外客服 bot 需要有效的 Agent J 席位。"
        c = self.store.get(self.bid)
        with self.assertRaises(BotError) as e:
            await m.write({"op": "save", "id": self.bid, "config": {**c, "enabled": True}})
        self.assertEqual(str(e.exception), "seat_required")
        res = await m.write({"op": "save", "id": self.bid, "config": {**c, "enabled": False, "title": "新标题"}})
        self.assertEqual(res["bot"]["title"], "新标题")


# ---------------------------------------------------------------- what the seat never gates
class NotGated(unittest.TestCase):
    def test_local_agent_terminal_and_automation_never_ask_the_seat(self):
        """The P199 gate lives only in the account-service doors; the local Agent, terminal and scheduled tasks never ask it."""
        gated = {"plaza.py", "support.py", "peer_service.py", "bots/runtime.py", "bots/owner.py", "serve.py", "seatcert.py",
                 "telegram.py", "pairprep.py", "admin.py", "cli.py", "doctor.py"}      # serve / telegram / pair*: P185's own doors
        for name in ("tasks.py", "heartbeat.py", "agent.py", "compose.py", "wake.py", "goal.py", "shared.py", "taskspec.py",
                     "agent_codex.py", "agent_opencode.py", "agent_opencode2.py", "harness.py", "market.py"):
            with self.subTest(name=name):
                src = (PKG / name).read_text()
                self.assertNotRegex(src, r"seat_refusal|seatcert\.refusal|seatcert", f"{name} must not consult the seat")
        self.assertNotIn("tasks.py", gated)

    def test_serve_only_feature_doors_call_the_feature_gate(self):
        src = (PKG / "serve.py").read_text()
        self.assertEqual(re.findall(r"seat_refusal\(\"(\w+)\"\)", src), [], "serve's own doors stay P185 (no feature wording)")


if __name__ == "__main__":
    unittest.main()
