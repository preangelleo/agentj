import asyncio
import copy
import inspect
import json
import pathlib
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import busy, serve, ctl_auth, wire


class RelayDispatchTests(unittest.TestCase):
    def test_dispatches_frames_without_waiting_and_preserves_device_order(self):
        async def scenario():
            h = object.__new__(serve.Host)
            h._relay_frame_tasks = {}
            seen = []
            started = asyncio.Event()
            release = asyncio.Event()

            async def on_frame(frame):
                seen.append((frame[4], frame[5]))
                if frame[5] == 1:
                    started.set()
                    await release.wait()

            h.on_frame = on_frame
            first = asyncio.create_task(h._dispatch_relay_frame(b"\x00\x00\x00\x00\x01\x01"))
            await asyncio.wait_for(started.wait(), 1)
            second = asyncio.create_task(h._dispatch_relay_frame(b"\x00\x00\x00\x00\x01\x02"))
            await asyncio.sleep(0)
            self.assertEqual(seen, [(1, 1)])
            self.assertFalse(second.done())
            release.set()
            await asyncio.gather(first, second)
            self.assertEqual(seen, [(1, 1), (1, 2)])

        asyncio.run(scenario())


    def test_three_queued_frames_of_one_phone_keep_their_order_and_other_phones_do_not_wait(self):
        async def scenario():
            h = object.__new__(serve.Host)
            seen, release = [], asyncio.Event()

            async def on_frame(frame):
                if frame[4] == 1 and frame[5] == 1:
                    await release.wait()
                seen.append((frame[4], frame[5]))

            h.on_frame = on_frame
            for n in (1, 2, 3):
                await serve.Host._queue_relay_frame(h, bytes([0, 0, 0, 0, 1, n]))
            await serve.Host._queue_relay_frame(h, bytes([0, 0, 0, 0, 2, 9]))
            await asyncio.sleep(0.01)
            self.assertEqual(seen, [(2, 9)], "the slow phone holds back only itself")
            release.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(seen, [(2, 9), (1, 1), (1, 2), (1, 3)])
            self.assertEqual(h._relay_frame_tasks, {})

        asyncio.run(scenario())

    def test_a_failed_frame_is_logged_later_frames_still_run_and_disconnect_waits_instead_of_cancelling(self):
        async def scenario():
            h = object.__new__(serve.Host)
            logged, seen, release = [], [], asyncio.Event()
            h.st = types.SimpleNamespace(log=lambda ev, **kw: logged.append((ev, kw.get("why"))))

            async def on_frame(frame):
                if frame[5] == 1:
                    raise RuntimeError("boom")
                if frame[5] == 3:
                    await release.wait()
                    await asyncio.sleep(0)      # a handler with several steps is never cut between them
                seen.append(frame[5])

            h.on_frame = on_frame
            for n in (1, 2, 3):
                await serve.Host._queue_relay_frame(h, bytes([0, 0, 0, 0, 1, n]))
            settle = asyncio.create_task(serve.Host._settle_relay_frames(h))
            await asyncio.sleep(0.02)
            self.assertFalse(settle.done(), "the disconnect waits for the frame that is still running")
            release.set()
            await settle
            self.assertEqual(seen, [2, 3])
            self.assertIn(("bg_task_failed", "relay_frame"), logged)

        asyncio.run(scenario())

    def test_read_loop_waits_only_when_the_backlog_is_full(self):
        async def scenario():
            h = object.__new__(serve.Host)
            release = asyncio.Event()

            async def on_frame(frame):
                await release.wait()

            h.on_frame = on_frame
            with mock.patch.object(serve, "RELAY_FRAME_BACKLOG", 2):
                await serve.Host._queue_relay_frame(h, bytes([0, 0, 0, 0, 1, 1]))
                second = asyncio.create_task(serve.Host._queue_relay_frame(h, bytes([0, 0, 0, 0, 2, 1])))
                await asyncio.sleep(0.02)
                self.assertFalse(second.done())
                release.set()
                await second
            await serve.Host._settle_relay_frames(h)

        asyncio.run(scenario())


class PairControlTests(unittest.TestCase):
    """S1: opening a pairing stays an ordinary control (the admin page and `agentj pair` send no claim). The unbind
    inside that stream is gone: it removes nothing, asks nobody, and spends no claim — whatever the request carries."""

    def run_pair(self, request, claims=None):
        async def scenario():
            h = object.__new__(serve.Host)
            sent, revoked, asked, logs = [], [], [], []
            h.seat_refusal = lambda: None
            h.relay_up, h.pairing, h.sessions, h.channel = True, None, {}, wire.b64u(bytes(16))
            h.cfg = {"web": "https://m.agentj.app", "relay": wire.PAIR_DEFAULT_RELAY}
            h.kp = types.SimpleNamespace(pub=bytes(32))
            h.ctl_claims = dict(claims or {})
            h.st = types.SimpleNamespace(log=lambda ev, **kw: logs.append((ev, kw)))
            h.emit = lambda ev, **kw: logs.append(("emit:" + ev, kw))

            async def ctl_send(w, obj): sent.append(obj)
            async def revoke(did): revoked.append(did); return True, 0
            async def ask(*a, **kw): asked.append(a); return {"behavior": "allow", "grant_info": {"uv": True}}
            async def expire(p): await asyncio.Event().wait()
            async def close_cid(*a): return None
            h._ctl_send, h.revoke, h.ask, h._expire_pairing, h.close_cid = ctl_send, revoke, ask, expire, close_cid
            r = asyncio.StreamReader()
            r.feed_data(json.dumps(request).encode() + b"\n")
            r.feed_eof()
            with mock.patch.object(ctl_auth, "has_tty", return_value=True):
                await serve.Host._ctl_pair(h, r, None, pid=4242)
            if h.pairing is not None and getattr(h.pairing, "timer", None):
                h.pairing.timer.cancel()
            return sent, revoked, asked, h.ctl_claims, logs
        return asyncio.run(scenario())

    def test_pair_itself_needs_no_claim(self):
        self.assertNotIn("pair", ctl_auth.SENSITIVE)
        sent, *_ = self.run_pair({"cmd": "noop"})
        self.assertEqual(sent[0]["ev"], "link")

    def test_inner_unbind_removes_nothing_with_or_without_a_claim(self):
        claims = {"tok": (4242, time.monotonic() + 30)}
        for request in ({"cmd": "unbind", "device": "victim"}, {"cmd": "unbind", "device": "victim", "claim": "tok"}):
            sent, revoked, asked, left, logs = self.run_pair(request, claims=claims)
            self.assertEqual((revoked, asked), ([], []), request)
            self.assertIn({"ev": "unbound", "ok": False, "device": "victim", "error": "removed"}, sent)
            self.assertEqual(left, claims, "the claim is not spent: it was never looked at")
            self.assertIn(("ctl_refused", {"cmd": "pair_unbind", "reason": "removed"}), logs)
            self.assertIn(("emit:ctl_refused", {"cmd": "pair_unbind", "reason": "removed"}), logs)

    def test_the_pair_stream_has_no_path_to_revoke_and_revoke_stays_claimed(self):
        self.assertIn("revoke", ctl_auth.SENSITIVE)
        self.assertNotIn("self.revoke(", inspect.getsource(serve.Host._ctl_pair))


class BusyThreadSafetyTests(unittest.TestCase):
    def test_board_has_shared_mutation_lock(self):
        board = busy.Board(None)
        self.assertTrue(hasattr(board, "_lock"))
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    board.start(f"{n}:{i}", "worker")
                    board.annotate(f"{n}:{i}", model="x")
                    board.stop(f"{n}:{i}")
            except Exception as exc:  # pragma: no cover - assertion reports the race
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(board.items(), [])

    def test_board_built_without_init_still_locks_and_a_copy_never_carries_the_lock(self):
        bare = busy.Board.__new__(busy.Board)
        bare.alias, bare.mem = {}, {}
        bare._alias("child", "key")
        self.assertEqual(bare.alias, {"child": "key"})
        board = busy.Board(None)
        board.start("k", "worker")
        twin = copy.deepcopy({"board": board})["board"]
        self.assertNotIn("_lock", twin.__dict__)
        self.assertEqual([t["id"] for t in twin.items()], ["k"])
        self.assertIsNot(twin._lock, board._lock)


if __name__ == "__main__":
    unittest.main()
