"""Audit 11-22: a removed bot is reported to the cloud until the cloud accepts it once, then never again.

Before: `bots.runtime.Runtime.run` listed removed bots (so the cloud learns `status: removed`, takes the public page down and
freezes the name) and re-registered every listed bot every 60 s for ever — one cloud request per removed bot per minute.
The whole path below is the real one (run loop -> register -> _register_request -> signed manifest); only the HTTPS call,
the clock and the cloud link are stand-ins. Every case runs with a throw-away HOME / XDG_* and starts no service.
"""
import _hermetic  # noqa: F401 — throw-away state / config dirs before agentj is imported
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from _testtmp import TEST_TMP
from agentj import cloud, wire
from agentj.bots import runtime as rt
from agentj.bots.store import Store
from agentj.poll_backoff import Backoff

PERIOD = 61          # one registration period (the loop re-registers a bot after 60 s)


class Clock:
    def __init__(self):
        import time
        self.now, self.real = 1000.0, time

    def monotonic(self):
        return self.now

    def time(self):
        return self.real.time()

    def time_ns(self):
        return self.real.time_ns()


class Removed(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        home = self.root / "home"
        env = {"HOME": str(home)}
        for name, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"), ("XDG_DATA_HOME", ".local/share"),
                          ("XDG_CACHE_HOME", ".cache"), ("XDG_RUNTIME_DIR", "run")):
            env[name] = str(home / sub)
            (home / sub).mkdir(parents=True, exist_ok=True)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = self.root / "state"
        self.sk = Ed25519PrivateKey.generate()
        self.clock = Clock()
        self.posts = []          # (bot, status) of every cloud registration request
        self.answers = []        # queued (code, value) answers; empty = 200 with a ticket
        self.logs = []
        self.store = self.open_store()
        self.bid = self.store.create({"slug": "sample-shop", "terms_accepted": True})["id"]

    def open_store(self):
        store = Store(self.state / "bots")
        self.addCleanup(store.close)
        return store

    def post_json(self, url, payload, *a, **k):
        inner = json.loads(wire.unb64u(payload["body"]))
        self.posts.append((inner["manifest"]["bot"], inner["manifest"]["status"]))
        return self.answers.pop(0) if self.answers else (200, {"ok": True, "host_ticket": {"claim": {}}})

    def runtime(self, store=None):
        store = store or self.store
        host = SimpleNamespace(st=SimpleNamespace(root=self.state, log=lambda ev, **k: self.logs.append((ev, k))), sk=self.sk,
                               channel="c" * 43, cfg={}, stopped=lambda: False, seat_refusal=lambda feature=None: None,
                               agent=None, agent_cfg={"kind": "claude"})
        r = rt.Runtime(SimpleNamespace(host=host, state=lambda: store, approve=None, handoff=None))
        r.registration_backoffs[self.bid] = Backoff(clock=self.clock.monotonic, jitter=lambda: 0)
        return r

    async def periods(self, r, n):
        """Run the real loop for n registration periods; returns the cloud requests made in them."""
        before, left = len(self.posts), [n]
        r.stopping = False

        async def next_period(_):
            self.clock.now += PERIOD
            left[0] -= 1
            if left[0] <= 0:
                r.stopping = True
        with mock.patch.object(rt, "time", self.clock), mock.patch.object(rt.asyncio, "sleep", next_period), \
                mock.patch.object(cloud, "read_cloud", lambda st: {"host_id": "h"}), \
                mock.patch.object(cloud, "api_url", lambda st, *a, **k: "https://api.invalid"), \
                mock.patch.object(cloud, "post_json", self.post_json):
            await r.run()
        return self.posts[before:]

    async def test_accepted_removal_is_not_registered_again(self):
        r = self.runtime()
        self.assertEqual(await self.periods(r, 1), [(self.bid, "paused")])
        self.store.remove(self.bid)
        self.assertEqual(await self.periods(r, 1), [(self.bid, "removed")], "the removal reaches the cloud")
        self.assertEqual(await self.periods(r, 5), [], "an accepted removal is never registered again")

    async def test_refused_or_failed_removal_is_retried_until_accepted(self):
        r = self.runtime()
        self.store.remove(self.bid)
        self.answers += [(503, {}), (200, {"ok": True}), (409, {"error": "stale"})]   # outage, no ticket, refusal
        for _ in range(3):
            self.assertEqual(await self.periods(r, 1), [(self.bid, "removed")], "not accepted: asked again next period")
        self.assertEqual(await self.periods(r, 1), [(self.bid, "removed")], "accepted now")
        self.assertEqual(await self.periods(r, 3), [])

    async def test_transport_error_is_retried(self):
        r = self.runtime()
        self.store.remove(self.bid)
        good = self.post_json

        def down(url, payload, *a, **k):
            good(url, payload)
            raise cloud.CloudError("network")
        self.post_json = down
        self.assertEqual(await self.periods(r, 2), [(self.bid, "removed")] * 2)
        self.post_json = good
        self.assertEqual(await self.periods(r, 1), [(self.bid, "removed")])
        self.assertEqual(await self.periods(r, 3), [])

    async def test_restart_does_not_report_an_accepted_removal_again(self):
        self.store.remove(self.bid)
        self.assertEqual(await self.periods(self.runtime(), 1), [(self.bid, "removed")])
        self.store.close()
        again = self.open_store()                               # serve restarted: new process state, same disk
        self.assertEqual(await self.periods(self.runtime(again), 3), [])

    async def test_restart_before_acceptance_still_reports(self):
        self.store.remove(self.bid)
        self.answers.append((503, {}))
        self.assertEqual(await self.periods(self.runtime(), 1), [(self.bid, "removed")])
        self.store.close()
        again = self.open_store()
        self.assertEqual(await self.periods(self.runtime(again), 1), [(self.bid, "removed")])
        self.assertEqual(await self.periods(self.runtime(again), 2), [])

    async def test_revived_bot_registers_as_before(self):
        r = self.runtime()
        self.store.remove(self.bid)
        self.assertEqual(await self.periods(r, 2), [(self.bid, "removed")])
        # No product path revives a bot today; a restored row must still fall back to the ordinary schedule.
        self.store.db.execute("UPDATE bot_lifecycle SET removed=0 WHERE bot=?", (self.bid,))
        self.assertEqual(await self.periods(r, 3), [(self.bid, "paused")] * 3)
        self.store.remove(self.bid)                             # removed again later (new epoch): reported again, once
        self.assertEqual(await self.periods(r, 3), [(self.bid, "removed")])

    async def test_live_bots_keep_their_minute_schedule(self):
        r = self.runtime()
        other = self.store.create({"slug": "other-shop", "terms_accepted": True})["id"]
        r.registration_backoffs[other] = Backoff(clock=self.clock.monotonic, jitter=lambda: 0)
        self.store.remove(self.bid)
        seen = await self.periods(r, 4)
        self.assertEqual(seen.count((other, "paused")), 4, "a paused / enabled bot is registered every period, unchanged")
        self.assertEqual(seen.count((self.bid, "removed")), 1)

    async def test_unsaved_acceptance_means_asking_again(self):
        r = self.runtime()
        self.store.remove(self.bid)
        with mock.patch.object(Store, "removal_reported", side_effect=OSError("disk")):
            self.assertEqual(await self.periods(r, 2), [(self.bid, "removed")] * 2, "nothing recorded: keep reporting")
        self.assertEqual(await self.periods(r, 1), [(self.bid, "removed")])
        self.assertEqual(await self.periods(r, 2), [])


if __name__ == "__main__":
    unittest.main()
