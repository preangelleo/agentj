"""Audit 2026-11-22 N-H3 / N-H2: relay frame lanes are per phone.

(a) the backlog is bounded per phone connection: a phone that fills its own lane is refused (its connection is closed, its
    not-yet-started frames are dropped) and the relay read loop never waits for it — other phones are unaffected;
(b) an emergency stop / an approval decision of a phone is not queued behind that same phone's slow handler. Only the
    scheduling differs: the frame is decrypted in arrival order and handed to the very same `_app` path (same session,
    same paired-request context), so every allowlist / signature / Face ID check runs exactly as before;
N-H2: when the relay drops, status stops pointing at the dead socket at once and a hung frame delays reconnect by at most
    the settle bound. The hung handler is not cancelled (nothing half-applied); it ends on its own."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import provenance, serve, wire


def raw(cid, n):
    return bytes([0, 0, 0, 0, cid, n])


class _Recv:
    """Stands in for the Noise receive cipher: strict arrival order, like the real nonce counter."""
    def __init__(self):
        self.n = 0

    def decrypt(self, ad, body):
        want, body = body[0], body[1:]
        if want != self.n:
            raise ValueError("out of order")
        self.n += 1
        return body


class _Phone:
    def __init__(self, host, cid, device):
        self.cid, self.n = cid, 0
        self.s = serve.Session(cid, state="ready", device=device, pub=device.encode(), recv=_Recv(), p33=True)
        self.s.provenance = provenance.phone()
        host.sessions[cid] = self.s

    def frame(self, obj):
        body = bytes([self.n]) + wire.pad_json(obj)
        self.n += 1
        return bytes([wire.OP_DATA]) + self.cid.to_bytes(4, "big") + bytes([wire.DATA]) + body


def host():
    h = object.__new__(serve.Host)
    h.sessions, h.logged, h.closed, h.seen = {}, [], [], []
    h.st = types.SimpleNamespace(log=lambda ev, **kw: h.logged.append((ev, kw)), is_allowed=lambda pub: True)

    async def close_cid(cid, reason, pair_result=None):
        h.sessions.pop(cid, None)
        h.closed.append((cid, reason))
    h.close_cid = close_cid
    return h


class PerPhoneBacklog(unittest.TestCase):
    def test_a_full_phone_is_refused_and_never_stalls_the_read_loop_or_other_phones(self):
        async def scenario():
            h = host()
            release = asyncio.Event()

            async def on_frame(f):
                if f[4] == 1:
                    await release.wait()
                h.seen.append((f[4], f[5]))
            h.on_frame = on_frame
            # before the fix the only bound was the link-wide one: three frames of one phone and the read loop waited
            link_wide = 64 if hasattr(serve, "PHONE_FRAME_BACKLOG") else 3
            with mock.patch.object(serve, "PHONE_FRAME_BACKLOG", 3, create=True), \
                    mock.patch.object(serve, "RELAY_FRAME_BACKLOG", link_wide):
                for n in range(1, 6):          # the read loop: phone 1 sends five frames into a handler that never returns
                    try:
                        await asyncio.wait_for(serve.Host._queue_relay_frame(h, raw(1, n)), 0.5)
                    except asyncio.TimeoutError:
                        self.fail(f"the relay read loop stalled on phone 1's frame {n}: every other phone waits too")
                    if n == 1:
                        await asyncio.sleep(0)             # its first frame is running (and stuck) before the rest arrive
                await asyncio.wait_for(serve.Host._queue_relay_frame(h, raw(2, 9)), 0.5)
                await asyncio.sleep(0.05)
                self.assertEqual(h.seen, [(2, 9)], "phone 2 is served while phone 1 is stuck")
                self.assertEqual([c for c in h.closed if c[0] == 1], [(1, "frame_backlog")], "phone 1 is refused, safely")
                self.assertEqual([c for c in h.closed if c[0] == 2], [])
                release.set()
                await serve.Host._settle_relay_frames(h)
            # the frame that was already running finishes; the refused phone's waiting frames never start
            self.assertEqual([x for x in h.seen if x[0] == 1], [(1, 1)])
            self.assertIn("relay_frame_backlog", [ev for ev, _ in h.logged])

        asyncio.run(scenario())

    def test_per_phone_order_is_kept_below_the_bound(self):
        async def scenario():
            h = host()
            gate = asyncio.Event()

            async def on_frame(f):
                if f[5] == 1:
                    await gate.wait()
                h.seen.append((f[4], f[5]))
            h.on_frame = on_frame
            for n in (1, 2, 3, 4):
                await serve.Host._queue_relay_frame(h, raw(1, n))
            gate.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(h.seen, [(1, 1), (1, 2), (1, 3), (1, 4)])
            self.assertEqual(h.closed, [])

        asyncio.run(scenario())


class UrgentFrames(unittest.TestCase):
    def scenario_host(self):
        h = host()
        h.release = asyncio.Event()
        h.ctx = []

        async def _app(s, obj):
            task, verify = provenance._PAIRED_REQUEST.get()
            h.ctx.append((obj["t"], task is asyncio.current_task(), verify()))
            if obj["t"] == "msg":
                await h.release.wait()
            h.seen.append((s.cid, obj["t"], obj.get("k")))
        h._app = _app
        return h

    def test_stop_and_approval_are_not_queued_behind_the_same_phones_slow_handler(self):
        async def scenario():
            h = self.scenario_host()
            p = _Phone(h, 7, "dev-a")
            for obj in ({"t": "msg", "text": "slow"}, {"t": "say", "k": 1}, {"t": "estop", "r": "r1"},
                        {"t": "answer", "id": "x", "k": 1}, {"t": "say", "k": 2}, {"t": "answer", "id": "y", "k": 2}):
                await serve.Host._queue_relay_frame(h, p.frame(obj))
            await asyncio.sleep(0.05)
            self.assertEqual(h.seen, [(7, "estop", None), (7, "answer", 1), (7, "answer", 2)],
                             "stop / approval decisions are handled while this phone's own message handler is still running")
            h.release.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(h.seen[3:], [(7, "msg", None), (7, "say", 1), (7, "say", 2)], "everything else keeps its order")
            self.assertEqual(h.closed, [])
            # same path, same checks: each ran in its own task inside the paired-request context of the live session
            self.assertEqual([c for c in h.ctx if c[0] in ("estop", "answer")],
                             [("estop", True, True), ("answer", True, True), ("answer", True, True)])

        asyncio.run(scenario())

    def test_urgent_frames_of_an_unready_session_get_no_shortcut(self):
        async def scenario():
            h = self.scenario_host()
            p = _Phone(h, 7, "dev-a")
            p.s.state = "pending"                          # an unapproved device: nothing is handled early
            frames = []

            async def on_frame(f):
                frames.append(f)
            h.on_frame = on_frame
            await serve.Host._queue_relay_frame(h, p.frame({"t": "estop", "r": "r1"}))
            await serve.Host._settle_relay_frames(h)
            self.assertEqual((h.seen, len(frames), p.s.recv.n), ([], 1, 0), "left to the ordinary path, undecrypted")

        asyncio.run(scenario())

    def test_a_resume_still_waiting_when_a_later_stop_arrives_is_dropped(self):
        # the phone sent resume, then stop: the stop must win. Handled early, the stop would otherwise be undone by the
        # older resume when its turn comes.
        async def scenario():
            h = self.scenario_host()
            sent = []

            async def send_app(s, obj, **kw):
                sent.append(obj)
            h.send_app = send_app
            p = _Phone(h, 7, "dev-a")
            for obj in ({"t": "msg", "text": "slow"}, {"t": "resume", "r": "aaaaaaaaaaaaaaaa"}, {"t": "estop", "r": "r2"},
                        {"t": "resume", "r": "bbbbbbbbbbbbbbbb"}):
                await serve.Host._queue_relay_frame(h, p.frame(obj))
            await asyncio.sleep(0.05)
            h.release.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual([(t, k) for _, t, k in h.seen], [("estop", None), ("msg", None), ("resume", None)],
                             "only the resume sent AFTER the stop runs")
            self.assertIn("relay_frame_superseded", [ev for ev, _ in h.logged])
            self.assertEqual([m for m in sent if m.get("t") == "ctl_res"],
                             [{"t": "ctl_res", "r": "aaaaaaaaaaaaaaaa", "action": "resume", "ok": False, "why": "superseded"}])

        asyncio.run(scenario())

    def test_frames_are_decrypted_in_arrival_order_whatever_runs_first(self):
        async def scenario():
            h = self.scenario_host()
            p = _Phone(h, 7, "dev-a")
            for obj in ({"t": "msg", "text": "slow"}, {"t": "estop", "r": "r1"}, {"t": "say", "k": 1}, {"t": "answer", "id": "x"}):
                await serve.Host._queue_relay_frame(h, p.frame(obj))
            h.release.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(p.s.recv.n, 4)
            self.assertEqual(h.closed, [], "no frame failed to decrypt")
            self.assertEqual(len(h.seen), 4)

        asyncio.run(scenario())

    def test_a_frame_that_fails_to_decrypt_closes_only_that_phone_after_its_earlier_frames(self):
        async def scenario():
            h = self.scenario_host()
            p, q = _Phone(h, 7, "dev-a"), _Phone(h, 8, "dev-b")
            await serve.Host._queue_relay_frame(h, p.frame({"t": "say", "k": 1}))
            bad = p.frame({"t": "say", "k": 2})
            await serve.Host._queue_relay_frame(h, bad[:6] + bytes([9]) + bad[7:])      # wrong counter
            await serve.Host._queue_relay_frame(h, q.frame({"t": "say", "k": 3}))
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(sorted(h.seen), [(7, "say", 1), (8, "say", 3)])
            self.assertEqual([c[0] for c in h.closed], [7])
            self.assertTrue(h.closed[0][1].startswith("bad_frame:"))

        asyncio.run(scenario())


class SettleIsBounded(unittest.TestCase):
    def test_a_hung_frame_does_not_delay_reconnect_and_status_leaves_the_dead_socket_at_once(self):
        async def scenario():
            h = host()
            h.cfg, h.channel, h.identity_ok = {"relay": "wss://relay.invalid"}, "c" * 8, True
            h.stopping = asyncio.Event()
            h.seat = types.SimpleNamespace(cert=lambda: None)
            h.relay_up = h.relay_ever_up = h.relay_seat_refused = False
            h.ws, h.relay_last, h.relay_said = None, None, (None, 0.0)
            h.emit = lambda *a, **k: None
            h._tap = lambda *a, **k: None
            h.credential_revocations = types.SimpleNamespace(sync=lambda: asyncio.sleep(0))
            h._auth = lambda ws, cert: asyncio.sleep(0)
            h._drop_all = lambda reason: asyncio.sleep(0)
            hung, cancelled, seen_down = asyncio.Event(), [], []
            connects = []

            async def on_frame(f):
                try:
                    await hung.wait()                         # a handler that never comes back
                except asyncio.CancelledError:
                    cancelled.append(f[5])
                    raise
            h.on_frame = on_frame

            class WS:
                def __init__(self, n): self.n = n
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return False
                def __aiter__(self): return self
                async def __anext__(self):
                    if self.n == 1 and not getattr(self, "sent", False):
                        self.sent = True
                        return raw(1, 1)
                    if self.n == 1:
                        await asyncio.sleep(0.05)
                        raise ConnectionError("relay dropped")
                    await asyncio.sleep(3600)

            def connect(url, **kw):
                connects.append(time.monotonic())
                return WS(len(connects))

            async def watch():                                 # what `agentj status` would read while the old link settles
                while len(connects) < 2:
                    if len(connects) == 1 and h.__dict__.get("_relay_frames_running") and h.ws is None:
                        seen_down.append(h.relay_up)
                    await asyncio.sleep(0.01)

            with mock.patch.object(serve, "connect", connect), mock.patch.object(serve, "RELAY_SETTLE", 0.3, create=True), \
                    mock.patch.object(serve, "FAST_RETRY", 0.01):
                loop = asyncio.create_task(serve.Host.relay_loop(h))
                w = asyncio.create_task(watch())
                try:
                    await asyncio.wait_for(w, 3)
                except asyncio.TimeoutError:
                    self.fail("reconnect waited for a hung frame (no bound on the settle)")
                finally:
                    h.stopping.set()
                    loop.cancel()
                    await asyncio.gather(loop, w, return_exceptions=True)
                    late = list(h.__dict__.get("_relay_frames_running", ()))
                    hung.set()
                    await asyncio.gather(*late, return_exceptions=True)
            self.assertLess(connects[1] - connects[0], 1.5)
            self.assertTrue(seen_down and not any(seen_down), "ws / relay_up were cleared before the settle, not after")
            self.assertEqual(cancelled, [], "the straggler is left to finish: no handler is cut between two of its steps")
            self.assertIn("relay_settle_timeout", [ev for ev, _ in h.logged])

        asyncio.run(scenario())

    def test_frames_that_finish_inside_the_bound_are_never_cancelled(self):
        async def scenario():
            h = host()
            release, done = asyncio.Event(), []

            async def on_frame(f):
                await release.wait()
                await asyncio.sleep(0)
                done.append(f[5])
            h.on_frame = on_frame
            await serve.Host._queue_relay_frame(h, raw(1, 1))
            await serve.Host._queue_relay_frame(h, raw(1, 2))
            settle = asyncio.create_task(serve.Host._settle_relay_frames(h))
            await asyncio.sleep(0.02)
            self.assertFalse(settle.done())
            release.set()
            await settle
            self.assertEqual(done, [1, 2])

        asyncio.run(scenario())


class DetachedSessionFrames(unittest.TestCase):
    def test_a_frame_whose_session_ended_while_it_waited_is_not_acted_on_except_a_stop(self):
        async def scenario():
            h = host()
            release = asyncio.Event()

            async def _app(s, obj):
                if obj["t"] == "msg":
                    await release.wait()
                h.seen.append(obj["t"])
            h._app = _app
            p = _Phone(h, 7, "dev-a")
            for obj in ({"t": "msg", "text": "slow"}, {"t": "say"}):
                await serve.Host._queue_relay_frame(h, p.frame(obj))
            await asyncio.sleep(0.01)
            h.sessions.pop(7)                                  # closed meanwhile (revoked, relay gone, …)
            for t in ("answer", "estop"):
                await serve.Host._dispatch_relay_frame(h, p.frame({"t": t}), None, ("app", p.s, {"t": t}), "u", 0)
            release.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(h.seen, ["estop", "msg"], "no approval decision and no queued frame of an ended session is applied")

        asyncio.run(scenario())


class ResumeLosesToALaterStop(unittest.TestCase):
    def test_do_resume_restarts_nothing_when_a_stop_landed_while_it_waited(self):
        async def scenario():
            h = object.__new__(serve.Host)
            started, posted = [], []
            h.estop = {"on": True}
            h.st = types.SimpleNamespace(log=lambda *a, **k: None, root=None)
            h.emit = lambda *a, **k: None
            h.activity = lambda *a, **k: None

            async def peers_estop(on):
                h.estop = {"on": True}                         # the urgent stop of the same phone ran meanwhile
            h.peers = types.SimpleNamespace(estop=peers_estop)
            h.agent = types.SimpleNamespace(task=None, start=lambda: started.append(1))
            h._post = lambda *a, **k: posted.append(a)
            with mock.patch.object(serve.controls, "set_estop", lambda st, on, by: {"on": on}), \
                    mock.patch("agentj.core.enabled", lambda: False):
                await serve.Host.do_resume(h, "phone:dev-a", "A")
            self.assertEqual((started, posted), ([], []))

        asyncio.run(scenario())


class ChairmanUrgentTypes(unittest.TestCase):
    """Chairman 11-22: main-thread interrupt and the sudo / secret card answer (`elev_answer`) join the urgent lane;
    follow-up answers (`q_answer`, `lt_answer`) do not."""
    scenario_host = UrgentFrames.scenario_host

    def test_the_urgent_set_is_exactly_this(self):
        self.assertEqual(set(serve.PHONE_URGENT_TYPES), {"estop", "answer", "main_thread_interrupt", "elev_answer"})
        from agentj import elevate
        self.assertIn("elev_answer", elevate.PHONE_TYPES)

    def test_each_new_type_is_not_queued_behind_the_same_phones_slow_handler_and_runs_the_same_path(self):
        for t in ("main_thread_interrupt", "elev_answer"):
            with self.subTest(t=t):
                async def scenario():
                    h = self.scenario_host()
                    p = _Phone(h, 7, "dev-a")
                    for obj in ({"t": "msg", "text": "slow"}, {"t": "say", "k": 1}, {"t": t, "id": "card-1", "k": 5}):
                        await serve.Host._queue_relay_frame(h, p.frame(obj))
                    await asyncio.sleep(0.05)
                    self.assertEqual(h.seen, [(7, t, 5)], "handled while this phone's own message handler is still running")
                    self.assertEqual([c for c in h.ctx if c[0] == t], [(t, True, True)],
                                     "through _app, in its own task, inside the live session's paired-request context")
                    h.release.set()
                    await serve.Host._settle_relay_frames(h)
                    self.assertEqual(h.seen[1:], [(7, "msg", None), (7, "say", 1)])
                    self.assertEqual(p.s.recv.n, 3)
                asyncio.run(scenario())

    def test_follow_up_answers_keep_their_place(self):
        async def scenario():
            h = self.scenario_host()
            p = _Phone(h, 7, "dev-a")
            for obj in ({"t": "msg", "text": "slow"}, {"t": "q_answer", "k": 1}, {"t": "lt_answer", "k": 2}):
                await serve.Host._queue_relay_frame(h, p.frame(obj))
            await asyncio.sleep(0.05)
            self.assertEqual(h.seen, [])
            h.release.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual([t for _, t, _ in h.seen], ["msg", "q_answer", "lt_answer"])
        asyncio.run(scenario())

    def test_a_decision_never_overtakes_an_earlier_frame_that_names_the_same_card(self):
        # e.g. the phone sent elev_stop / a withdrawal for card-1, then an answer for card-1: strict arrival order
        for t in ("elev_answer", "answer"):
            with self.subTest(t=t):
                async def scenario():
                    h = self.scenario_host()
                    p = _Phone(h, 7, "dev-a")
                    for obj in ({"t": "msg", "text": "slow"}, {"t": "elev_stop", "id": "card-1"},
                                {"t": t, "id": "card-1", "k": 1}, {"t": t, "id": "card-2", "k": 2}):
                        await serve.Host._queue_relay_frame(h, p.frame(obj))
                    await asyncio.sleep(0.05)
                    self.assertEqual(h.seen, [(7, t, 2)], "only the decision on the card nobody else named went ahead")
                    h.release.set()
                    await serve.Host._settle_relay_frames(h)
                    self.assertEqual(h.seen[1:], [(7, "msg", None), (7, "elev_stop", None), (7, t, 1)])
                asyncio.run(scenario())

    def test_no_new_type_is_executed_for_a_session_that_ended_while_it_waited(self):
        async def scenario():
            h = self.scenario_host()
            p = _Phone(h, 7, "dev-a")
            h.sessions.pop(7)
            for t in ("answer", "main_thread_interrupt", "elev_answer"):
                await serve.Host._dispatch_relay_frame(h, p.frame({"t": t}), None, ("app", p.s, {"t": t}), "u", 0)
            self.assertEqual(h.seen, [], "the stop-everything exception stays stop-everything only")
            await serve.Host._dispatch_relay_frame(h, p.frame({"t": "estop"}), None, ("app", p.s, {"t": "estop"}), "u", 0)
            self.assertEqual([t for _, t, _ in h.seen], ["estop"])
        asyncio.run(scenario())


class SecurityR2(unittest.TestCase):
    scenario_host = UrgentFrames.scenario_host

    def test_hr3_a_decision_without_a_plain_card_id_keeps_its_ordinary_place(self):
        for t in ("answer", "elev_answer"):
            for bad in ({}, {"id": None}, {"id": 7}, {"id": ""}, {"id": ["card-1"]}, {"id": "x" * 65}):
                with self.subTest(t=t, bad=bad):
                    async def scenario():
                        h = self.scenario_host()
                        p = _Phone(h, 7, "dev-a")
                        for obj in ({"t": "msg", "text": "slow"}, {"t": t, **bad}):
                            await serve.Host._queue_relay_frame(h, p.frame(obj))
                        await asyncio.sleep(0.03)
                        self.assertEqual(h.seen, [], "no card id, no urgent lane")
                        h.release.set()
                        await serve.Host._settle_relay_frames(h)
                        self.assertEqual([x[1] for x in h.seen], ["msg", t])
                    asyncio.run(scenario())

    def test_hr3_the_same_card_rule_sees_an_id_one_level_inside_an_earlier_frame(self):
        for earlier in ({"t": "cards_get", "target": {"card": "card-1"}}, {"t": "grant_off", "ids": ["card-0", "card-1"]}):
            with self.subTest(earlier=earlier["t"]):
                async def scenario():
                    h = self.scenario_host()
                    p = _Phone(h, 7, "dev-a")
                    for obj in ({"t": "msg", "text": "slow"}, earlier, {"t": "answer", "id": "card-1"}, {"t": "answer", "id": "card-2"}):
                        await serve.Host._queue_relay_frame(h, p.frame(obj))
                    await asyncio.sleep(0.03)
                    self.assertEqual(h.seen, [(7, "answer", None)])
                    self.assertEqual(len(h.seen), 1, "only the decision on the card nobody else named went ahead")
                    h.release.set()
                    await serve.Host._settle_relay_frames(h)
                    self.assertEqual([x[1] for x in h.seen], ["answer", "msg", earlier["t"], "answer"])
                asyncio.run(scenario())

    def test_hr3_the_three_handlers_that_see_an_answer_first_key_their_cards_by_id(self):
        import inspect
        from agentj import peer_service, wechat
        self.assertIn("a.rid == obj.get('id')", inspect.getsource(wechat))
        self.assertIn('rid = obj.get("id")', inspect.getsource(peer_service.PeerService.answer))


class SecurityR1(unittest.TestCase):
    def test_h6_one_device_with_many_connections_has_one_budget_and_cannot_stall_another_phones_stop(self):
        async def scenario():
            h = host()
            h.release = asyncio.Event()

            async def _app(s, obj):
                if obj["t"] == "msg":
                    await h.release.wait()
                h.seen.append((s.cid, obj["t"]))
            h._app = _app
            conns = [_Phone(h, cid, "dev-a") for cid in (1, 2, 3, 4, 5)]         # ONE paired device, five connections
            other = _Phone(h, 9, "dev-b")
            with mock.patch.object(serve, "PHONE_FRAME_BACKLOG", 3), mock.patch.object(serve, "RELAY_FRAME_BACKLOG", 8):
                for _ in range(3):
                    for p in conns:
                        try:
                            await asyncio.wait_for(serve.Host._queue_relay_frame(h, p.frame({"t": "msg", "text": "slow"})), 0.5)
                        except asyncio.TimeoutError:
                            self.fail("one device with several connections filled the link-wide backlog: the read loop waits")
                        await asyncio.sleep(0)
                await asyncio.wait_for(serve.Host._queue_relay_frame(h, other.frame({"t": "estop", "r": "r1"})), 0.5)
                await asyncio.sleep(0.05)
                self.assertIn((9, "estop"), h.seen, "the other phone's stop got through")
                self.assertTrue(all(reason == "frame_backlog" and cid in (1, 2, 3, 4, 5) for cid, reason in h.closed) and h.closed)
                h.release.set()
                await serve.Host._settle_relay_frames(h)
            self.assertEqual(h.__dict__.get("_relay_devs"), {})

        asyncio.run(scenario())

    def test_h7_a_stale_frame_never_closes_a_newer_session_on_the_same_cid(self):
        async def scenario():
            h = host()

            async def _app(s, obj):
                raise RuntimeError("boom")
            h._app = _app
            old = _Phone(h, 7, "dev-a")
            new = _Phone(h, 7, "dev-b")                    # the relay gave the cid to another connection meanwhile
            await serve.Host._app_entry(h, old.s, {"t": "estop"})
            self.assertEqual(h.closed, [], "the stale frame's failure must not close the session that holds the cid now")
            self.assertIs(h.sessions[7], new.s)
            await serve.Host._app_entry(h, new.s, {"t": "say"})
            self.assertEqual([c[0] for c in h.closed], [7])

        asyncio.run(scenario())

    def test_h7_a_stale_stop_of_a_device_that_is_no_longer_allowed_is_dropped_before_any_handler(self):
        async def scenario():
            h = host()
            ran = []

            async def _app(s, obj):
                ran.append(obj["t"])
            h._app = _app
            old = _Phone(h, 7, "dev-a")
            h.sessions.pop(7)
            h.st.is_allowed = lambda pub: False
            await serve.Host._app_entry(h, old.s, {"t": "estop"})
            self.assertEqual((ran, h.closed), ([], []))

        asyncio.run(scenario())

    def test_a_handler_that_outlives_its_session_has_no_phone_authority_and_cannot_send(self):
        # N-H2 hardening (characterisation): after the relay drop the straggler is left running, not cancelled. Its
        # session is no longer the live one, so the request source every authority decision reads is not `phone`.
        async def scenario():
            h = host()
            p = _Phone(h, 7, "dev-a")
            before, after, gate = [], [], asyncio.Event()

            async def _app(s, obj):
                before.append(provenance._paired_source())
                await gate.wait()
                after.append((provenance._paired_source(), h._paired_live(s)))
            h._app = _app
            await serve.Host._queue_relay_frame(h, p.frame({"t": "say"}))
            await asyncio.sleep(0.01)
            h.sessions.pop(7)                              # _drop_all → _detach
            gate.set()
            await serve.Host._settle_relay_frames(h)
            self.assertEqual(before[0]["kind"], "phone")
            self.assertNotEqual(after[0][0].get("kind"), "phone")
            self.assertFalse(after[0][1])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
