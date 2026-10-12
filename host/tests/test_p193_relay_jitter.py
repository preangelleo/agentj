"""P193 (B12): the host's relay reconnect wait is jittered (a relay deploy drops every host at once; they must not all come
back in the same instant): the fast first retries ±25 % around FAST_RETRY, the doubling ladder +0…50 % (never shorter)."""
import _hermetic  # noqa: F401,I001
import unittest
from unittest.mock import patch

from agentj import serve
import test_p129_reconnect as p129


class Jitter(unittest.TestCase):
    setUp, tearDown, _run = p129.RelayFirstConnect.setUp, p129.RelayFirstConnect.tearDown, p129.RelayFirstConnect._run   # FAST_RETRY = 0.1 there

    def gaps(self, draw):
        with patch.object(serve.secrets, "randbelow", lambda n: draw):
            relay, _, _, _ = self._run(["close4003", "close4003", "ok"])
        return [b - a for a, b in zip(relay.times, relay.times[1:])]

    def test_fast_retry_follows_the_draw(self):
        top = self.gaps(500)                      # 0.1 s × 1.25
        self.tearDown(); self.setUp()             # a fresh state dir for the second run
        low = self.gaps(0)                        # 0.1 s × 0.75
        self.assertTrue(all(g >= 0.123 for g in top), top)
        self.assertTrue(all(g >= 0.073 for g in low), low)
        self.assertGreater(sum(top) / len(top), sum(low) / len(low) + 0.02, (top, low))


if __name__ == "__main__":
    unittest.main()
