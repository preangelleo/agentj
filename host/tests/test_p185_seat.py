"""P185: the relay seat certificate on the host (agentj/seatcert.py, PROTOCOL §2 seat certificate, ADR-A198).

The six cases the brief names — unbound · valid · expired · within the 72-hour grace · beyond it · cloud unreachable — plus
suspended (402), "not bound" (403), the bootstrap grace of a bound host that never got a certificate, renewal timing, the
loopback exemption, and the serve doors: the relay auth frame carries the certificate, phone messages / pairing / the
pre-check are refused with the reason, the status view never shows the certificate. No network: the Dashboard is a fake.
"""
import asyncio
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import cloud, pairprep, seatcert, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402

PROD_RELAY = "wss://relay.agentj.app"
H = 3600
T0 = 1_800_000_000


def cert_for(channel: str, exp: int) -> str:
    p = wire.b64u(json.dumps({"v": 1, "ch": channel, "exp": exp}, separators=(",", ":")).encode())
    return p + "." + "A" * 86      # the host never verifies the signature (the relay does)


class FakeDash:
    """POST /v1/host/relay/cert: `mode` = ok | not_bound | suspended | down | 404; records every call."""

    def __init__(self, st, mode="ok"):
        self.st, self.mode, self.calls = st, mode, []

    def __call__(self, url, payload):
        self.calls.append(url)
        if self.mode == "down":
            raise cloud.CloudError("network")
        if self.mode == "404":
            return 404, {"error": "not_found"}
        if self.mode == "not_bound":
            return 403, {"error": "not_bound"}
        if self.mode == "suspended":
            return 402, {"error": "agent_suspended", "checkout": True}
        inner = json.loads(wire.unb64u(payload["body"]))
        exp = inner["ts"] + 72 * H
        return 200, {"cert": cert_for(inner["channel"], exp), "exp": exp}


def bind(st):
    cloud.write_cloud(st, {"api": "https://agentj.example/api", "host_id": "h_185", "tenant": {"slug": "acme", "name": "Acme"},
                           "linked_at": 1, "last_seq": 0})


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    relay = PROD_RELAY

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay=self.relay)
        self.clock = Clock()
        self.dash = FakeDash(self.st)
        self.seat = seatcert.SeatCert(self.st, self.relay, post=self.dash, clock=self.clock)
        os.environ.pop("AGENTJ_TEST_SEAT_ENFORCE", None)


class Verdicts(Base):
    def test_unbound(self):
        self.assertEqual(self.seat.verdict(), "unbound")
        self.assertFalse(self.seat.allowed())
        self.assertFalse(self.seat.due(), "no account → the Dashboard is never asked")
        self.assertEqual(self.dash.calls, [])
        self.assertIn("agentj.app/account", seatcert.message("unbound", "zh", cloud.app_url(self.st)))
        self.assertIsNone(self.seat.cert())

    def test_valid(self):
        bind(self.st)
        self.assertTrue(self.seat.due())
        self.assertEqual(self.seat.refresh(), "ok")
        self.assertEqual(self.seat.verdict(), "ok")
        c = self.seat.cert()
        self.assertTrue(c)
        p = json.loads(wire.unb64u(c.split(".")[0]))
        self.assertEqual((p["ch"], p["exp"]), (self.st.config()["channel"], T0 + 72 * H))
        self.assertEqual(stat.S_IMODE(self.seat.path().stat().st_mode), 0o600)
        self.assertNotIn(c, self.st.log_path.read_text(), "the certificate is never logged")
        self.assertNotIn("cert", self.seat.view())
        self.assertFalse(self.seat.due(), "fresh: no ask")

    def test_expired_and_the_72h_grace_when_the_cloud_is_unreachable(self):
        bind(self.st)
        self.seat.refresh()
        self.dash.mode = "down"
        for hours in (6, 24, 48, 71):        # within the grace: renewals fail, the cached certificate still serves
            self.clock.t = T0 + hours * H
            self.assertTrue(self.seat.due())
            self.assertEqual(self.seat.refresh(), "unreachable")
            self.assertEqual(self.seat.verdict(), "ok", f"{hours} h")
            self.assertTrue(self.seat.allowed())
        self.clock.t = T0 + 72 * H           # beyond the grace
        self.assertEqual(self.seat.verdict(), "expired")
        self.assertFalse(self.seat.allowed())
        self.assertIsNone(self.seat.cert())
        self.dash.mode = "ok"                # the cloud is back → renewed, serving again
        self.clock.t += 1000
        self.assertTrue(self.seat.due())
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "ok")

    def test_bootstrap_grace_bound_host_never_certified_and_cloud_down(self):
        """An owner upgrading from 0.18 while our cloud is down is not locked out: 72 h from the first try."""
        bind(self.st)
        self.assertEqual(self.seat.verdict(), "grace", "bound, not asked yet")
        self.dash.mode = "down"
        self.assertEqual(self.seat.refresh(), "unreachable")
        self.clock.t = T0 + 71 * H
        self.assertEqual(self.seat.verdict(), "grace")
        self.assertTrue(self.seat.allowed())
        self.clock.t = T0 + 72 * H
        self.assertEqual(self.seat.verdict(), "expired")
        self.assertFalse(self.seat.allowed())

    def test_a_dashboard_older_than_the_route_is_not_a_verdict(self):
        bind(self.st)
        self.dash.mode = "404"
        self.assertEqual(self.seat.refresh(), "unreachable")
        self.assertEqual(self.seat.verdict(), "grace")

    def test_suspended_and_not_bound_end_the_seat_at_once(self):
        bind(self.st)
        self.seat.refresh()
        self.dash.mode = "suspended"
        self.clock.t += 7 * H
        self.assertEqual(self.seat.refresh(), "payment_required")
        self.assertEqual(self.seat.verdict(), "suspended")
        self.assertIsNone(self.seat.cert(), "the certificate is dropped")
        self.assertIn("续费", seatcert.message("suspended", "zh", "https://agentj.app/account"))
        self.dash.mode = "down"              # an unreachable cloud afterwards does not undo the last verdict
        self.clock.t += seatcert.RETRY_REFUSED
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "suspended")
        self.dash.mode = "ok"                # paid → the next ask restores it
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "ok")
        self.dash.mode = "not_bound"         # the owner removed this computer
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "unbound")

    def test_renewal_timing(self):
        bind(self.st)
        self.assertTrue(self.seat.due())
        self.seat.refresh()
        self.clock.t = T0 + seatcert.RENEW_AGE - 1
        self.assertFalse(self.seat.due())
        self.clock.t = T0 + seatcert.RENEW_AGE
        self.assertTrue(self.seat.due())
        self.dash.mode = "down"
        self.seat.refresh()
        self.assertFalse(self.seat.due(), "a failed ask waits RETRY_FAIL")
        self.clock.t += seatcert.RETRY_FAIL
        self.assertTrue(self.seat.due())

    def test_a_fresh_bind_is_noticed_without_waiting(self):
        self.assertFalse(self.seat.due())
        bind(self.st)
        self.assertTrue(self.seat.due())

    def test_a_rebind_clears_the_old_refusal_and_asks_at_once(self):
        bind(self.st)
        self.dash.mode = "not_bound"
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "unbound")
        self.assertFalse(self.seat.due(), "refused: wait RETRY_REFUSED")
        cloud.write_cloud(self.st, {"api": "https://agentj.example/api", "host_id": "h_186", "tenant": {"slug": "acme", "name": "Acme"},
                                    "linked_at": 2, "last_seq": 0})
        self.assertTrue(self.seat.due(), "a new binding is asked about at once")
        self.assertEqual(self.seat.verdict(), "grace")
        self.dash.mode = "ok"
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "ok")
        self.clock.t += seatcert.RETRY_REFUSED
        self.assertLessEqual(seatcert.RETRY_REFUSED, 900, "paying resumes within 15 minutes")

    def test_relay_cert_answer_must_name_this_channel(self):
        bind(self.st)

        def wrong(url, payload):
            return 200, {"cert": cert_for("x" * 22, T0 + 72 * H), "exp": T0 + 72 * H}
        r = cloud.relay_cert(self.st, post=wrong, now=self.clock)
        self.assertEqual((r.kind, r.status), ("fail", "bad_response"))


class Exemption(Base):
    relay = "ws://127.0.0.1:9"

    def test_loopback_relay_is_exempt_unless_enforced(self):
        self.assertEqual(self.seat.verdict(), "exempt")
        self.assertTrue(self.seat.allowed())
        self.assertFalse(self.seat.due())
        os.environ["AGENTJ_TEST_SEAT_ENFORCE"] = "1"
        self.addCleanup(os.environ.pop, "AGENTJ_TEST_SEAT_ENFORCE", None)
        self.assertEqual(self.seat.verdict(), "unbound")
        self.assertEqual(pairprep.precheck(self.st)["route"], "seat")


class PreCheck(Base):
    def test_unbound_route_is_seat_and_says_where(self):
        chk = pairprep.precheck(self.st)
        self.assertEqual((chk["route"], chk["seat"]), ("seat", "unbound"))
        self.assertFalse(chk["account"])
        self.assertIn("agentj.app/account", pairprep.both(chk))
        self.assertIn("席位", pairprep.message(chk, "zh"))

    def test_bound_with_a_seat_keeps_the_account_route(self):
        bind(self.st)
        self.seat.refresh()
        self.assertEqual(pairprep.precheck(self.st, True)["route"], "account")


class ServeDoors(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay=PROD_RELAY)
        self.host = serve.Host(self.st, events="quiet", read_stdin=False)
        self.sent = []

        async def send_app(s, obj):
            self.sent.append(obj)
            return True
        self.host.send_app = send_app
        self.notices = []
        self.host.local_notice = self.notices.append
        self.host.agent = SimpleNamespace(passthrough=lambda c: True, submit=lambda s: None, kind="claude")

    def session(self):
        return SimpleNamespace(cid=1, device="d" * 16, name="Phone", state="ready", p33=True, pub=b"x")

    async def test_phone_message_refused_without_a_seat_with_one_notice(self):
        s = self.session()
        for sid in ("a" * 22, "b" * 22):
            await self.host.on_say(s, {"sid": sid, "text": "hello", "ts": 0})
        res = [m for m in self.sent if m.get("t") == "say_res"]
        self.assertEqual([(m["ok"], m["why"]) for m in res], [(False, "seat"), (False, "seat")])
        self.assertEqual(len(self.notices), 1, "one notice per SEAT_NOTE, not one per message")
        self.assertIn("agentj.app/account", self.notices[0])
        self.assertIn('"reason": "seat"', self.st.log_path.read_text())

    async def test_phone_message_passes_with_a_seat(self):
        bind(self.st)
        self.host.seat.post = FakeDash(self.st)
        await asyncio.to_thread(self.host.seat.refresh)
        self.assertIsNone(self.host.seat_refusal())
        accepted = []

        async def accept(*a, **k):
            accepted.append(a[1])
            return SimpleNamespace(turn=1, prep=None)
        self.host._accept = accept
        await self.host.on_say(self.session(), {"sid": "c" * 22, "text": "hello", "ts": 0})
        self.assertEqual(accepted, ["hello"])

    async def test_pairing_refused_before_any_link(self):
        out = []

        async def ctl_send(w, obj):
            out.append(obj)
        self.host._ctl_send = ctl_send
        self.host.relay_up = True
        await self.host._ctl_pair(None, None)
        self.assertEqual(out, [{"ev": "error", "reason": "seat_required"}])
        self.assertIsNone(self.host.pairing)

    async def test_auth_frame_carries_the_certificate_only_when_held(self):
        class WS:
            def __init__(self):
                self.out, self.inq = [], [json.dumps({"t": "challenge", "n": "A" * 43}), json.dumps({"t": "ok"})]

            async def recv(self):
                return self.inq.pop(0)

            async def send(self, m):
                self.out.append(json.loads(m))
        ws = WS()
        await self.host._auth(ws)
        self.assertNotIn("cert", ws.out[0])
        bind(self.st)
        self.host.seat.post = FakeDash(self.st)
        await asyncio.to_thread(self.host.seat.refresh)
        ws = WS()
        await self.host._auth(ws)
        self.assertEqual(ws.out[0]["cert"], self.host.seat.cert())
        self.assertEqual(self.host.seat_sent, ws.out[0]["cert"])

    async def test_seat_refused_relay_state_and_status_view(self):
        self.host.relay_seat_refused = True
        self.assertEqual(self.host.relay_state(), "seat")
        v = self.host.seat.view()
        self.assertEqual(v["verdict"], "unbound")
        self.assertEqual(set(v), {"verdict", "exp", "url"})


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ Telegram (the owner's own bot never passes the relay)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_p118_telegram import _Base as _TgBase, msg as tg_msg  # noqa: E402


class TelegramDoor(_TgBase):
    async def test_owner_message_refused_without_a_seat_owner_hears_why_group_hears_nothing(self):
        self.host.seat_refusal = lambda: seatcert.message("suspended", "zh", "https://agentj.app/account")
        self.assertFalse(await self.t.incoming(tg_msg('hello'), self.cfg))
        self.host._accept.assert_not_called()
        sent = self.bot.sent('sendMessage')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['fields']['chat_id'], 123)
        self.assertIn('agentj.app/account', sent[0]['fields']['text'])
        self.assertIn('"reason": "seat"', self.st.log_path.read_text())

    async def test_owner_message_passes_with_a_seat(self):
        self.host.seat_refusal = lambda: None
        self.assertTrue(await self.t.incoming(tg_msg('hello'), self.cfg))
        self.assertEqual(self.host._accept.await_count, 1)
