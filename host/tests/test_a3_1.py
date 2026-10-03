"""A3.1: at most 5 remotes per host (Q32, 2026-10-02), enforced by the host; the full-list prompt's unbind; and the
Dashboard's unbind requests, which the host executes only after its own checks (allowlist, switch, hourly cap)."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import gate, cloud, serve, wire  # noqa: E402
from agentj.state import MAX_DEVICES, DeviceLimit, State  # noqa: E402
from fakecp import FakeCP  # noqa: E402

RID = [wire.b64u(bytes([i]) * 16) for i in range(1, 12)]   # 22-char request ids


PASS = "test-passphrase-L2"


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    gate.set_passphrase(st, PASS)   # L2: the human's approval passphrase
    return st


def _fill(st, n=MAX_DEVICES):
    return [st.add_device(os.urandom(32), f"遥控器{i}") for i in range(n)]


class Cap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowlist_holds_at_most_five(self):
        self.assertEqual(MAX_DEVICES, 5)
        ids = _fill(self.st)
        self.assertTrue(self.st.device_full())
        before = self.st.devices_path.read_bytes()
        with self.assertRaises(DeviceLimit):
            self.st.add_device(os.urandom(32), "第六台")
        self.assertEqual(self.st.devices_path.read_bytes(), before, "a refused sixth device writes nothing")
        # re-approving an already listed device is not a new one
        pub = wire.unb64u(self.st.devices()[ids[0]]["pub"])
        self.assertEqual(self.st.add_device(pub, "改名"), ids[0])
        self.assertTrue(self.st.remove_device(ids[1]))
        self.st.add_device(os.urandom(32), "腾出位置后")
        self.assertEqual(len(self.st.devices()), 5)

    def _pending(self, host):
        s = serve.Session(cid=7, state="pending", device="NEWNEWNEWNEWNEWN", name="新手机", pub=os.urandom(32), h=os.urandom(32))
        s.deadline = time.monotonic() + 60
        host.sessions[7] = s
        return s

    def test_decide_refuses_when_full_even_with_the_right_code(self):
        _fill(self.st)
        host = serve.Host(self.st, events="jsonl", read_stdin=False)

        async def go():
            s = self._pending(host)
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
            host.pairing = p
            ev = host._pending_event(s)
            self.assertTrue(ev["full"])
            self.assertEqual(ev["limit"], 5)
            self.assertEqual(len(ev["devices"]), 5)
            self.assertEqual(set(ev["devices"][0]), {"id", "name", "paired_at", "online"})
            res = await host.decide(p, wire.safety_code(s.h), PASS)   # the right code
            return res
        res = asyncio.run(go())
        self.assertEqual(res, {"ev": "denied", "reason": "device_limit"})
        self.assertEqual(len(self.st.devices()), 5)
        self.assertNotIn(7, host.sessions, "the waiting device is dropped")
        self.assertIn('"reason": "device_limit"', self.st.log_path.read_text())

    def test_re_pairing_a_listed_device_is_not_a_new_one(self):
        """Review A31-04 (claude): a full host still lets a listed device re-pair, without the full-list prompt."""
        ids = _fill(self.st)
        host = serve.Host(self.st, events="jsonl", read_stdin=False)

        async def send_app(s, obj):
            return True
        host.send_app = send_app

        async def go():
            s = self._pending(host)
            s.pub = wire.unb64u(self.st.devices()[ids[0]]["pub"])
            s.device = ids[0]
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
            host.pairing = p
            self.assertNotIn("full", host._pending_event(s))
            return await host.decide(p, wire.safety_code(s.h), PASS)
        self.assertEqual(asyncio.run(go())["ev"], "approved")
        self.assertEqual(len(self.st.devices()), 5)

    def test_unbind_from_the_prompt_makes_room_then_the_code_approves(self):
        ids = _fill(self.st)
        host = serve.Host(self.st, events="jsonl", read_stdin=False)
        sent = []

        async def send_app(s, obj):   # no relay in this unit: record what would be sent
            sent.append(obj)
            return True
        host.send_app = send_app

        async def go():
            s = self._pending(host)
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
            host.pairing = p
            removed, _ = await host.revoke(ids[2])           # what the ctl "unbind" command does
            self.assertTrue(removed)
            self.assertNotIn("full", host._pending_event(s))
            return await host.decide(p, wire.safety_code(s.h), PASS)
        res = asyncio.run(go())
        self.assertEqual(res["ev"], "approved")
        self.assertEqual(sent[0], {"t": "approved"})        # L1: then the Agent status + push key (on_ready)
        self.assertEqual([m["t"] for m in sent[1:]], ["status", "estop_state", "push_key"])
        self.assertEqual(len(self.st.devices()), 5)
        self.assertNotIn(ids[2], self.st.devices())


class RemoteUnbind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.ids = _fill(self.st)
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_checks_before_acting(self):
        async def go():
            out = [await self.host.remote_unbind(RID[0], "UNKNOWNUNKNOWNUN")]
            self.st.set_remote_unbind(False)
            out.append(await self.host.remote_unbind(RID[1], self.ids[0]))
            self.st.set_remote_unbind(True)
            out.append(await self.host.remote_unbind(RID[2], self.ids[0]))
            out.append(await self.host.remote_unbind(RID[2], self.ids[0]))   # the same request again: not redone
            return out
        self.assertEqual(asyncio.run(go()), ["unknown_device", "disabled", "revoked", "revoked"])
        self.assertNotIn(self.ids[0], self.st.devices())
        self.assertEqual(len(self.st.devices()), 4)
        log = [json.loads(x) for x in self.st.log_path.read_text().splitlines() if '"remote_unbind"' in x]
        self.assertEqual([x["result"] for x in log], ["unknown_device", "disabled", "revoked"])
        self.assertTrue(all(set(x) <= {"ts", "ev", "request", "device", "result"} for x in log))

    def test_host_hourly_cap_survives_restart(self):
        async def go(host, pairs):
            return [await host.remote_unbind(r, d) for r, d in pairs]
        self.assertEqual(serve.REMOTE_UNBIND_PER_HOUR, 3)
        self.assertEqual(asyncio.run(go(self.host, list(zip(RID[:4], self.ids[:4])))), ["revoked"] * 3 + ["rate_limited"])
        self.assertIn(self.ids[3], self.st.devices())
        # a restart (new Host on the same state) neither resets the cap nor forgets a decided request (review A31-02)
        again = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.assertEqual(asyncio.run(go(again, [(RID[5], self.ids[4]), (RID[0], self.ids[0])])), ["rate_limited", "revoked"])
        self.assertEqual(len(self.st.devices()), 2)
        self.assertEqual(os.stat(self.st.unbind_path).st_mode & 0o777, 0o600)
        self.st.check_perms()
        rec = json.loads(self.st.unbind_path.read_text())
        self.assertEqual(set(rec), {"executed", "decided", "throttled", "seen"})
        self.assertNotIn("遥控器", self.st.unbind_path.read_text(), "ids and times only, no labels")

    def test_decision_budget_bounds_log_volume(self):
        """A compromised control plane sending endless fresh ids gets ≤ 30 decisions an hour, then silence (one log line)."""
        self.st.set_remote_unbind(False)

        async def go():
            out = []
            for i in range(60):
                out.append(await self.host.remote_unbind(wire.b64u(i.to_bytes(16, "big")), "UNKNOWNUNKNOWNUN"))
            return out
        out = asyncio.run(go())
        self.assertEqual(out[:30], ["disabled"] * 30)
        self.assertEqual(out[30:], [None] * 30)
        lines = self.st.log_path.read_text().splitlines()
        self.assertEqual(sum('"remote_unbind"' in x for x in lines), 30)
        self.assertEqual(sum('"remote_unbind_throttled"' in x for x in lines), 1)

    def test_switch_persists_in_config(self):
        self.assertTrue(self.st.remote_unbind())
        self.st.set_remote_unbind(False)
        self.assertFalse(State(self.st.root).remote_unbind())
        self.assertEqual(os.stat(self.st.config_path).st_mode & 0o777, 0o600)


class Sync(unittest.TestCase):
    def test_parse_sync_whitelist(self):
        ok = {"unbind": [{"id": RID[0], "device": "A" * 16, "x": 1}], "approve": ["A" * 16]}
        self.assertEqual(cloud.parse_sync(ok), ((RID[0], "A" * 16),))
        for bad in ({}, {"unbind": "x"}, {"unbind": [{"id": "short", "device": "A" * 16}]},
                    {"unbind": [{"id": RID[0], "device": "A" * 15}]}, {"unbind": [{"id": RID[0], "device": "A" * 16}] * 2},
                    {"unbind": [{"id": RID[i], "device": "A" * 16} for i in range(11)]}, {"unbind": [5]}):
            self.assertIsNone(cloud.parse_sync(bad), bad)

    def test_sync_loop_end_to_end_against_fake_control_plane(self):
        with tempfile.TemporaryDirectory() as d, FakeCP() as cp:
            st = _state(d)
            ids = _fill(st, 3)
            cloud.write_cloud(st, {"api": cp.url, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                   "linked_at": 1, "last_seq": 0})
            cp.unbind = [{"id": RID[0], "device": ids[1]}, {"id": RID[1], "device": "NOPENOPENOPENOPE"}]
            serve.SYNC_MIN_GAP = 0.2
            cp.sync_extra = {"approve": [ids[0]], "devices": ["EVILEVILEVILEVIL"]}
            host = serve.Host(st, events="jsonl", read_stdin=False)
            before = set(st.devices())

            async def go():
                t = asyncio.create_task(host.sync_loop())
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if len(cp.syncs) >= 2:
                        break
                host.stopping.set()
                await asyncio.wait_for(t, 15)
            asyncio.run(go())
            self.assertEqual(set(st.devices()), before - {ids[1]}, "exactly the requested, listed device is gone; nothing added")
            self.assertGreaterEqual(len(cp.syncs), 2)
            self.assertEqual(cp.syncs[0]["results"], [])
            self.assertEqual(sorted((x["id"], x["result"]) for x in cp.syncs[1]["results"]),
                             sorted([(RID[0], "revoked"), (RID[1], "unknown_device")]))
            self.assertEqual(cp.unbind, [])
            gaps = [b - a for a, b in zip(cp.sync_times, cp.sync_times[1:])]
            self.assertTrue(all(g >= 0.15 for g in gaps), gaps)

    def test_hostile_answers_never_make_a_tight_loop(self):
        """Review A31-01: a control plane answering every sync with 10 fresh ids gets syncs ≥ SYNC_MIN_GAP apart."""
        with tempfile.TemporaryDirectory() as d, FakeCP() as cp:
            st = _state(d)
            _fill(st, 2)
            cloud.write_cloud(st, {"api": cp.url, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                   "linked_at": 1, "last_seq": 0})
            cp.unbind_factory = lambda n: [{"id": wire.b64u(os.urandom(16)), "device": "NOPENOPENOPENOPE"} for _ in range(10)]
            old = serve.SYNC_MIN_GAP
            serve.SYNC_MIN_GAP = 0.3
            host = serve.Host(st, events="jsonl", read_stdin=False)

            async def go():
                t = asyncio.create_task(host.sync_loop())
                await asyncio.sleep(2.0)
                host.stopping.set()
                await asyncio.wait_for(t, 15)
            try:
                asyncio.run(go())
            finally:
                serve.SYNC_MIN_GAP = old
            self.assertLessEqual(len(cp.syncs), 8, "≈ 2 s / 0.3 s, never a tight loop")
            self.assertEqual(len(st.devices()), 2)


if __name__ == "__main__":
    unittest.main()
