"""0.15.2 (P57 lane "offline"): the host-side contract the phone's offline queue relies on (PROTOCOL §14, web
public/js/outbox.js). The phone fixes a message's `sid` when it queues it and resends with the same sid when a say_res was
lost; the host must answer `dup` for it — never deliver it twice. These cases pin down exactly how far that goes
(compose.Sends): per device, not per session (a reconnect is a new session); beyond the 10-minute table; for slash commands
typed as a say; the newest 1 024 sids per device; and NOT across a host restart (memory only) — the documented limit.
No wire change; the serve-level `dup` answer itself is covered by test_p33_chain (say → dup)."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import compose, wire  # noqa: E402


def sid() -> str:
    return wire.b64u(os.urandom(16))


class OfflineQueueContract(unittest.TestCase):
    def test_a_resend_after_a_lost_say_res_is_dup_per_device_not_per_session(self):
        ss = compose.Sends()
        s = sid()
        self.assertFalse(ss.used("D", s))
        self.assertTrue(ss.add(compose.Send("D", s, 1)))
        # the phone reconnects (a new Noise session, same device id) and sends the same sid again
        self.assertTrue(ss.used("D", s), "same device, same sid → dup")
        self.assertFalse(ss.used("E", s), "another device's sid space is its own")

    def test_dup_outlives_the_ten_minute_table(self):
        ss = compose.Sends()
        s = sid()
        t0 = time.monotonic()
        ss.add(compose.Send("D", s, 1, state="delivered", created=t0))
        with mock.patch.object(compose.time, "monotonic", return_value=t0 + compose.SEND_TTL + 3600):
            ss._sweep()
            self.assertIsNone(ss.get("D", s), "the finished send left the withdraw table")
            self.assertTrue(ss.used("D", s), "… but its sid is still remembered: a resend an hour later is dup")

    def test_a_slash_command_typed_as_a_say_is_remembered_too(self):
        ss = compose.Sends()
        s = sid()
        ss.add(compose.Send("D", s, 0, state="delivered"))      # serve.on_say: a whitelisted /compact as text
        self.assertTrue(ss.used("D", s))

    def test_the_newest_1024_sids_per_device_and_no_more(self):
        self.assertEqual(compose.SEEN_MAX, 1024)
        ss = compose.Sends()
        first = sid()
        ss.add(compose.Send("D", first, 0, state="delivered"))
        for _ in range(compose.SEEN_MAX - 1):
            ss.add(compose.Send("D", sid(), 0, state="delivered"))
        self.assertTrue(ss.used("D", first), "1 024 sends later the first is still dup")
        ss.add(compose.Send("D", sid(), 0, state="delivered"))
        with mock.patch.object(compose.time, "monotonic", return_value=time.monotonic() + compose.SEND_TTL + 1):
            self.assertFalse(ss.used("D", first), "the 1 025th send pushed it out (the phone queues ≤ 50, so a queued "
                                                   "message is never that far behind)")

    def test_a_host_restart_forgets_the_sids(self):
        ss = compose.Sends()
        s = sid()
        ss.add(compose.Send("D", s, 1, state="delivered"))
        self.assertFalse(compose.Sends().used("D", s), "memory only: after a restart a resend of a say whose say_res was "
                                                       "lost would be delivered again (PROTOCOL §14 limit)")


if __name__ == "__main__":
    unittest.main()
