"""Actual Host history producer/backpressure regression; no approval transport changes."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import history, serve
from agentj.state import State


class Delivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.hist = history.History(State(Path(self.tmp.name)), on=False)
        self.keep = patch.object(history, "MEM_KEEP", 500); self.keep.start(); self.addCleanup(self.keep.stop)
        self.sent = []; self.queue = asyncio.Queue()
        sessions = {str(i): SimpleNamespace(state="ready", p33=True, pub=b"synthetic", cid=str(i)) for i in range(2)}
        self.host = SimpleNamespace(hist=self.hist, post_q=self.queue, sessions=sessions,
                    st=SimpleNamespace(is_allowed=lambda _: True), readmark=SimpleNamespace(value=lambda _: 0))
        for name in ("_post", "_send_p33", "hist_emit", "hist_meta_all", "hist_metadata"):
            setattr(self.host, name, getattr(serve.Host, name).__get__(self.host))
        async def send(session, obj):
            self.sent.append((session.cid, json.loads(json.dumps(obj))))
        self.host.send_app = send

    async def drain(self):
        while not self.queue.empty():
            fn, args = self.queue.get_nowait(); await fn(*args)

    def turn(self, text=""):
        t = self.hist.add({"k": "phone", "text": "original"}, text)
        self.host.hist_emit([t]); return t

    async def test_ten_thousand_updates_retain_one_descriptor_and_complete_final_body(self):
        t = self.turn()
        for _ in range(10000):
            self.hist.update(t["id"], append="x")
            self.host.hist_emit([t])
        self.assertEqual(self.queue.qsize(), 1)
        from agentj.history_delivery import MAX_IDS, MAX_BYTES
        delivery = self.host._history_delivery
        self.assertLessEqual(len(delivery.pending), MAX_IDS)
        self.assertLessEqual(delivery.descriptor_bytes(), MAX_BYTES)
        self.assertTrue(all(type(k) is int and v is None for k, v in delivery.pending.items()))
        expected = self.hist.get(t["id"])["reply"]["text"]
        self.assertEqual(expected.count("x"), 10000)
        await self.drain()
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(row[1]["turn"]["reply"]["text"] == expected for row in self.sent))

    async def test_update_during_await_reaches_slow_and_fast_devices(self):
        t = self.turn("first"); entered = asyncio.Event(); release = asyncio.Event()
        original = self.host.send_app
        async def blocked(session, obj):
            if not entered.is_set():
                entered.set(); await release.wait()
            await original(session, obj)
        self.host.send_app = blocked
        fn, args = self.queue.get_nowait(); running = asyncio.create_task(fn(*args))
        await entered.wait()
        self.hist.update(t["id"], append=" + newest")
        self.host.hist_emit([t])
        self.assertEqual(self.queue.qsize(), 0, "the running drain owns the one callback")
        release.set(); await running; await self.drain()
        for cid in ("0", "1"):
            rows = [obj for dev, obj in self.sent if dev == cid]
            self.assertEqual(rows[-1]["turn"]["reply"]["text"], "first\n\n + newest")

    async def test_eviction_epoch_and_fixed_batch_do_not_reorder_control_callbacks(self):
        self.keep.stop()
        with patch.object(history, "MEM_KEEP", 20):
            for i in range(30): self.turn(str(i))
        self.host.hist_meta_all()
        controls = []
        async def control(name): controls.append(name)
        self.host._post(control, "ask")
        self.host._post(control, "pair")
        fn, args = self.queue.get_nowait(); await fn(*args)
        self.assertLessEqual(len(self.sent), 16 * 2)
        fn, args = self.queue.get_nowait(); await fn(*args)
        fn, args = self.queue.get_nowait(); await fn(*args)
        self.assertEqual(controls, ["ask", "pair"])
        self.hist.epoch += 1; self.hist.turns.clear()
        self.host.hist_meta_all()
        await self.drain()
        old = [obj["turn"]["id"] for _, obj in self.sent if obj["t"] == "hist_turn"]
        self.assertTrue(all(tid > 10 for tid in old))
        self.assertEqual(self.sent[-1][1]["t"], "hist_meta")
        self.assertEqual(self.sent[-1][1]["epoch"], self.hist.epoch)

    async def test_overflow_replays_retained_history_without_dropping_data(self):
        # A disk-backed History can temporarily exceed KEEP before its next compaction.
        with patch.object(history, "MEM_KEEP", 600):
            for i in range(550): self.turn(str(i))
        self.assertEqual(self.queue.qsize(), 1)
        from agentj.history_delivery import MAX_IDS, MAX_BYTES
        delivery = self.host._history_delivery
        self.assertLessEqual(len(delivery.pending), MAX_IDS)
        self.assertLessEqual(delivery.descriptor_bytes(), MAX_BYTES)
        await self.drain()
        for cid in ("0", "1"):
            ids = {obj["turn"]["id"] for dev, obj in self.sent if dev == cid and obj["t"] == "hist_turn"}
            self.assertEqual(ids, set(self.hist.turns))

    async def test_actual_agent_text_supported_path_does_not_queue_absent_legacy_or_voice(self):
        import test_p351_silent_final as fixture
        f = fixture.HostLevel("test_marker_alone_unchanged"); f.setUp(); self.addCleanup(f.doCleanups)
        host = f.host
        host.hist = self.hist
        host.cur_turn = self.hist.add({"k": "phone", "text": "read the report"})["id"]
        host.post_q = asyncio.Queue()
        host._post = serve.Host._post.__get__(host)
        host.media_later = lambda tid: None
        host.emit = lambda *a, **kw: None
        self.assertFalse(host.voice_stream.available())
        self.assertEqual(host.voice_stream.calls, {})
        for _ in range(1000): host.agent_text("delta")
        self.assertEqual(host.post_q.qsize(), 1)
        self.assertEqual(len(host._history_delivery.pending), 1)
        host.sessions = {"legacy": SimpleNamespace(state="ready", p33=False)}
        host.agent_text("legacy delta")
        self.assertEqual(host.post_q.qsize(), 2, "an actual legacy receiver retains its existing delivery")


if __name__ == "__main__": unittest.main()
