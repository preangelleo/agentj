"""P313 (PROTOCOL §14): phone `say` receipts on disk — a host restart never runs the same phone message twice.

Unit: receipts.Receipts (states only go forward, torn lines, bounds, compaction, 0600, interrupted at load).
Serve: a `say` is on disk before the Agent gets it; after a restart a resend of the same sid is `dup` (never a second
delivery) whatever state it was in; a message the old serve had not finished ends its page `failed` with one line that
explains the uncertain outcome. A missing page binding gets one aggregate notice; a finished page is left alone."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import agent, browser_login, compose, diskq, history, humane, provenance, receipts, serve, upgrade_messages, wire  # noqa: E402

from test_l1 import Phone, _host, _state  # noqa: E402


def sid() -> str:
    return wire.b64u(os.urandom(16))


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def reopen(self, **kw) -> receipts.Receipts:
        return receipts.Receipts(self.root, **kw)

    def test_survives_a_restart_and_reports_what_was_open(self):
        r = self.reopen()
        a, b, c = sid(), sid(), sid()
        asyncio.run(r.persist("D", a, "recv"))
        r.mark("D", a, "recv", 7)              # the page id once the page exists
        r.mark("D", b, "recv", 8)
        r.mark("D", b, "deliv", 8)
        r.mark("D", c, "recv", 9)
        r.mark("D", c, "deliv", 9)
        r.mark("D", c, "done")
        r.flush()
        p = self.root / receipts.FILE
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertNotIn("text", p.read_text(), "keys and states only, never the words")
        r2 = self.reopen()
        for x in (a, b, c):
            self.assertTrue(r2.used("D", x), "every sid is still dup after the restart")
        self.assertFalse(r2.used("E", a), "per device")
        self.assertEqual(sorted((x["i"], x["s"], x["n"]) for x in r2.interrupted),
                         sorted([(a, "recv", 7), (b, "deliv", 8)]))
        self.assertEqual(r2.state("D", a), "lost")
        self.assertEqual(r2.state("D", c), "done")
        self.assertEqual(self.reopen().interrupted, [], "reported once: the next restart is quiet")

    def test_state_never_goes_back_and_order_on_disk_does_not_matter(self):
        r = self.reopen()
        s = sid()
        r.mark("D", s, "done")
        self.assertIsNone(r.mark("D", s, "recv"))
        r.flush()
        with open(self.root / receipts.FILE, "a") as f:     # an out-of-order older line, a torn line, junk
            f.write(json.dumps({"d": "D", "i": s, "s": "recv", "t": 1, "n": None}) + "\n")
            f.write('{"d":"D","i":"x","s":\n["no"]\n{"d":"D","i":"y","s":"bogus"}\n')
        r2 = self.reopen()
        self.assertEqual(r2.state("D", s), "done")
        self.assertEqual(r2.interrupted, [])
        self.assertFalse(r2.used("D", "y"))

    def test_unbound_acceptance_does_not_erase_same_state_page_proof(self):
        r = self.reopen()
        s = sid()
        r.mark("D", s, "recv", 7, reply_page=(4, 1234))
        r.flush()
        with open(self.root / receipts.FILE, "a") as f:
            f.write(json.dumps({"d": "D", "i": s, "s": "recv", "t": 1, "n": None}) + "\n")
        self.assertEqual(self.reopen().interrupted[0]["page"], (4, 1234))

    def test_acceptance_cannot_prove_confirmed_delivery(self):
        r = self.reopen()
        accepted, uncertain, notice = sid(), sid(), sid()
        r.mark("D", accepted, "recv", 7, reply_page=(4, 1234))
        r.mark("D", uncertain, "recv", 8, reply_page=(4, 1235))
        r.mark("D", uncertain, "deliv", 8)
        r.mark("D", notice, "deliv", 9, reply_page=(4, 1236), notice_only=True)
        r.flush()
        notice_row = json.loads((self.root / receipts.FILE).read_text().splitlines()[-1])
        self.assertEqual(notice_row["notice_page"], [4, 1236])
        self.assertNotIn("page", notice_row)
        answered = mock.Mock(return_value=True)
        restored = self.reopen(answered=answered)
        answered.assert_not_called()
        self.assertEqual([(row["s"], row["page"]) for row in restored.interrupted],
                         [("recv", (4, 1234)), ("deliv", None), ("deliv", (4, 1236))])

    def test_compaction_snapshot_does_not_mix_new_delivery_and_old_page_fields(self):
        r = self.reopen()
        mid, coordinates = sid(), (4, 1234)
        r.mark("D", mid, "recv", 7, reply_page=coordinates)
        r.flush()
        serialize = r._line
        pending_lines = []

        def advance_after_snapshot(key, snap):
            with mock.patch.object(r, "_line", side_effect=serialize), \
                    mock.patch.object(r, "_append", side_effect=pending_lines.append):
                r.mark("D", mid, "deliv", 7, reply_page=coordinates, notice_only=True).result()
            return serialize(key, snap)

        with mock.patch.object(r, "_line", side_effect=advance_after_snapshot):
            r._rewrite()
        snapshot = json.loads((self.root / receipts.FILE).read_text())
        self.assertEqual((snapshot["s"], snapshot["page"]), ("recv", list(coordinates)))
        r._append(pending_lines[0])
        r._append(json.dumps({"d": "D", "i": mid, "s": "recv", "t": 1, "n": None}))
        answered = mock.Mock(return_value=True)
        restored = self.reopen(answered=answered)
        answered.assert_not_called()
        self.assertEqual((restored.interrupted[0]["s"], restored.interrupted[0]["page"]),
                         ("deliv", coordinates))

    def test_bounded_by_age_and_count_and_compacted(self):
        now = [1_000_000.0]
        r = self.reopen(now=lambda: now[0])
        old = sid()
        r.mark("D", old, "done")
        now[0] += receipts.KEEP_S + 1
        fresh = [sid() for _ in range(receipts.PER_DEVICE + 70)]
        for x in fresh:
            r.mark("D", x, "recv")
            r.mark("D", x, "done")
        r.flush()
        self.assertLessEqual(sum(1 for d, _ in r.items if d == "D"), receipts.PER_DEVICE + 64)
        lines = (self.root / receipts.FILE).read_text().count("\n")
        self.assertLess(lines, 2 * (receipts.PER_DEVICE + 64) + 64 + 2, "the file is rewritten, not grown forever")
        r2 = self.reopen(now=lambda: now[0])
        self.assertFalse(r2.used("D", old), "older than a week: gone")
        self.assertEqual(sum(1 for d, _ in r2.items if d == "D"), receipts.PER_DEVICE)
        self.assertTrue(r2.used("D", fresh[-1]))
        self.assertFalse(r2.used("D", fresh[0]), "beyond the newest 1 024 per device (the §14 bound)")

    def test_an_unwritable_store_never_stops_a_message(self):
        r = self.reopen(log=lambda *a, **k: None)
        r.path = self.root / "missing-dir" / receipts.FILE
        s = sid()
        asyncio.run(r.persist("D", s, "recv"))
        self.assertTrue(r.broken)
        self.assertTrue(r.used("D", s), "the memory half still answers dup")


class _Agent:
    """Only what on_say / _accept touch: the queue (the harness never runs here)."""
    kind = "claude"
    halting = False
    cur_send = None

    def __init__(self):
        self.got = []

    def submit(self, send):
        self.got.append(send)

    def passthrough(self, name):
        return False


class ServeRestart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.ph = Phone(self.st)

    def tearDown(self):
        self.tmp.cleanup()

    def boot(self):
        sent = []
        host = _host(self.st, sent)
        host.agent = _Agent()
        host.receipts_restart()          # what serve.run does right after serve_start
        s = serve.Session(cid=11, state="ready", device=self.ph.did, name="手机", pub=self.ph.pub, p33=True)
        host.sessions[11] = s
        return host, s, sent

    async def say(self, host, s, sent, x, text="帮我把周报发出去"):
        with mock.patch.object(host, "_setup_round_trip", new=mock.AsyncMock()), \
                mock.patch.object(host, "_model_set_flush", new=mock.AsyncMock()):
            await host.on_say(s, {"t": "say", "sid": x, "text": text, "ts": 0})
            for _ in range(20):                      # the `ok` answer goes out through serve's post queue
                await asyncio.sleep(0)
        return [o for _, o in sent if o.get("t") == "say_res" and o.get("sid") == x][-1]

    def test_restart_never_replays_and_tells_the_phone_once(self):
        a, b, c = sid(), sid(), sid()

        async def first():
            host, s, sent = self.boot()
            self.pair_origin(host, s)
            # the receipt is on disk before the Agent has the message
            orig = host.agent.submit

            def submit(send):
                rows = (self.st.root / receipts.FILE).read_text()
                self.assertIn(send.sid, rows, "fsync'ed before the hand-over")
                self.assertEqual(host.receipts.items[(s.device, send.sid)]["reply_page"],
                                 (host.hist.epoch, host.hist.get(send.turn)["ts"]))
                orig(send)
            host.agent.submit = submit
            for x in (a, b, c):
                self.assertTrue((await self.say(host, s, sent, x))["ok"])
            sa, sb, sc = host.agent.got
            host.say_delivered(sb)                   # b: the harness has it, its turn never ended
            host.say_delivered(sc)                   # c: delivered and finished
            host.cur_turn = sc.turn
            host.agent_turn_end()
            self.assertEqual((await self.say(host, s, sent, a))["why"], "dup", "same process: dup as before")
            host.receipts.flush()
            return sa.turn, sb.turn, sc.turn
        ta, tb, tc = asyncio.run(first())

        async def second():                          # serve died and came back
            host, s, sent = self.boot()
            for x in (a, b, c):
                r = await self.say(host, s, sent, x)
                self.assertEqual((r["ok"], r["why"]), (False, "dup"), "a resend after the restart is never delivered")
            self.assertEqual(host.agent.got, [], "nothing replayed to the Agent")
            pa, pb, pc = host.hist.get(ta), host.hist.get(tb), host.hist.get(tc)
            self.assertEqual(pa["end"], "failed")
            self.assertIn(humane.of(host, "say.restart_unsure"), pa["reply"]["text"])
            self.assertEqual(pb["end"], "failed")
            self.assertIn(humane.of(host, "say.restart_unsure"), pb["reply"]["text"])
            self.assertEqual(pc["end"], "done", "a finished message says nothing more")
            # a new message (new sid) still works
            new = sid()
            self.assertTrue((await self.say(host, s, sent, new))["ok"])
            self.assertEqual(len(host.agent.got), 1)
            host.receipts.flush()
        asyncio.run(second())

        async def third():                           # told once: the next restart is quiet
            host, s, sent = self.boot()
            self.assertEqual(host.receipts.interrupted, [])
            self.assertEqual((await self.say(host, s, sent, b))["why"], "dup")
        asyncio.run(third())

    def test_without_the_page_one_notice_counts_them(self):
        r = receipts.Receipts(self.st.root)
        r.mark(self.ph.did, sid(), "recv", 99999)    # a page this history never had (history off: pages in memory)
        r.mark(self.ph.did, sid(), "recv")
        r.flush()

        async def go():
            return self.boot()
        host, s, sent = asyncio.run(go())
        texts = [p["reply"]["text"] for p in host.hist.page()[0]]
        self.assertIn(humane.of(host, "say.restart_some", n=2), texts)

    def test_terminal_send_does_not_rebind_an_existing_phone_receipt(self):
        async def run():
            host, session, _ = self.boot()
            self.pair_origin(host, session)
            host.agent.cur_send = compose.Send(session.device, sid(), 0)
            host.agent.cur_send.source = provenance.phone()
            mid = sid()
            await host.receipts.persist(session.device, mid, "recv")
            with mock.patch.object(host, "_setup_round_trip", new=mock.AsyncMock()), \
                    mock.patch.object(host, "_model_set_flush", new=mock.AsyncMock()):
                page = await host.bang_turn(session, mid, "pwd", "synthetic output", False, 0, "ok", sid())
            self.assertEqual(host.agent.got[-1].turn, page)
            self.assertIsNone(host.receipts.items[(session.device, mid)]["n"])
            host.receipts.flush()
            await diskq.aflush()

        asyncio.run(run())

    def pair_origin(self, host, session):
        session.provenance = provenance.phone()
        session.sign_pub = self.st.sign_key(session.device)
        session.login_phone = browser_login.RequestPhone.from_session(host, session)

    def test_claimed_held_receipt_keeps_bound_page_without_replay(self):
        mid = sid()

        async def submit_once():
            host, session, sent = self.boot()
            self.pair_origin(host, session)
            with mock.patch.object(upgrade_messages, "pending", return_value=True), \
                    mock.patch.object(upgrade_messages, "kick"):
                await host._app(session, {"t": "say", "sid": mid, "text": "本次升级遇到了什么问题？", "ts": 0})
            page_id = next(obj["turn"] for _, obj in sent if obj.get("t") == "say_res")
            self.assertEqual(host.receipts.state(session.device, mid), "held")
            held = json.loads((self.st.root / receipts.FILE).read_text().splitlines()[-1])
            self.assertEqual(held["page"], [host.hist.epoch, host.hist.get(page_id)["ts"]])
            self.assertFalse(host.agent.got)

            with mock.patch.object(host, "_setup_round_trip", new=mock.AsyncMock()), \
                    mock.patch.object(host, "_model_set_flush", new=mock.AsyncMock()):
                await asyncio.gather(upgrade_messages.resume(host), upgrade_messages.resume(host))
            self.assertEqual(len(host.agent.got), 1)
            self.assertEqual(host.receipts.state(session.device, mid), "recv")
            host.receipts.flush()
            await diskq.aflush()
            return page_id

        page_id = asyncio.run(submit_once())

        async def after_restart():
            host, session, frames = self.boot()
            await upgrade_messages.resume(host)
            self.assertEqual((await self.say(host, session, frames, mid))["why"], "dup")
            self.assertFalse(host.agent.got)
            page = host.hist.get(page_id)
            self.assertEqual(page["end"], "failed")
            self.assertEqual(page["reply"]["text"], humane.of(host, "say.restart_unsure"))
            await diskq.aflush()

        for _ in range(2):
            asyncio.run(after_restart())

    def test_uncertain_delivery_has_a_bound_notice_and_never_replays(self):
        mid = sid()

        async def first():
            host, session, frames = self.boot()
            self.pair_origin(host, session)
            accepted = await self.say(host, session, frames, mid, text="请解释这次变动")
            send = host.agent.got[0]
            claude = agent.ClaudeAgent(host, {"kind": "claude", "dir": str(self.st.root)})
            claude.cur_send = send
            submit = mock.AsyncMock(return_value=agent.UNCERTAIN)
            self.assertIs(await claude.deliver(submit), agent.UNCERTAIN)
            submit.assert_awaited_once()
            host.receipts.flush()
            await diskq.aflush()
            return accepted["turn"]

        page_id = asyncio.run(first())

        async def after_restart():
            host, session, frames = self.boot()
            self.assertEqual((await self.say(host, session, frames, mid))["why"], "dup")
            self.assertFalse(host.agent.got)
            page = host.hist.get(page_id)
            self.assertEqual((page["end"], page["reply"]["text"]),
                             ("failed", humane.of(host, "say.restart_unsure")))
            self.assertFalse(any(row["src"]["k"] == "sys" for row in host.hist.page()[0]))
            await diskq.aflush()

        for _ in range(2):
            asyncio.run(after_restart())

    def test_unbound_held_failure_does_not_touch_reused_page(self):
        mid = sid()

        async def first():
            host, session, frames = self.boot()
            self.pair_origin(host, session)
            with mock.patch.object(upgrade_messages, "pending", return_value=True), \
                    mock.patch.object(upgrade_messages, "kick"):
                await host._app(session, {"t": "say", "sid": mid, "text": "升级期间尚未投递的问题", "ts": 0})
            self.assertEqual(host.receipts.state(session.device, mid), "held")
            await diskq.aflush()
            return next(row["turn"] for _, row in frames if row.get("t") == "say_res")

        page_id = asyncio.run(first())
        history.set_enabled(self.st, False)

        async def after_restart():
            host, session, frames = self.boot()
            self.pair_origin(host, session)
            page = host.hist_add({"k": "phone", "dev": session.device, "source_kind": "phone",
                                  "source_role": "owner", "text": "不相干的新页"}, "", "open")
            self.assertEqual(page["id"], page_id)
            await upgrade_messages.resume(host)
            self.assertEqual((await self.say(host, session, frames, mid))["why"], "dup")
            self.assertFalse(host.agent.got)
            self.assertEqual((page["end"], page["reply"]["text"]), ("open", ""))
            self.assertTrue(any(row["src"]["k"] == "sys" and row["reply"]["text"] == humane.of(host, "say.restart_lost")
                                for row in host.hist.page()[0]))
            host.receipts.flush()
            await diskq.aflush()

        asyncio.run(after_restart())

    def test_history_disabled_send_cannot_finish_against_older_page_after_reenable(self):
        older, recent = sid(), sid()

        async def old_then_disabled():
            host, s, sent = self.boot()
            self.pair_origin(host, s)
            old = await self.say(host, s, sent, older, text="旧问题已经回答了")
            prior = host.agent.got[-1]
            host.say_delivered(prior)
            host.hist_update(prior.turn, text="旧的完成答复", end="done")
            host.receipts.done_turn(prior.turn)
            host.receipts.flush()

            history.set_enabled(self.st, False)
            off, sess, frames = self.boot()
            self.pair_origin(off, sess)
            accepted = await self.say(off, sess, frames, recent, text="历史关闭后提出的新问题")
            specific = off.agent.got[-1]
            self.assertEqual(accepted["turn"], old["turn"], "memory and disk history reused the same page number")
            off.say_delivered(specific)
            self.assertIsNone(upgrade_messages.reply_page_coordinates(off, specific))
            off.receipts.flush()
            history.set_enabled(self.st, True)
            return old["turn"]

        prior_page = asyncio.run(old_then_disabled())

        async def after_restart():
            host, s, sent = self.boot()
            self.assertEqual(host.receipts.state(s.device, older), "done")
            self.assertEqual(host.receipts.state(s.device, recent), "lost")
            self.assertEqual((await self.say(host, s, sent, recent))["why"], "dup")
            self.assertEqual(host.agent.got, [])
            self.assertEqual(host.hist.get(prior_page)["reply"]["text"], "旧的完成答复")
            replies = [row["reply"]["text"] for row in host.hist.page()[0]]
            self.assertNotIn(humane.of(host, "say.restart_some", n=1), replies)

        for _ in range(2):
            asyncio.run(after_restart())

    def test_legacy_delivered_receipt_cannot_fail_an_unrelated_open_history_page(self):
        self.check_receipt_collision("deliv")

    def test_legacy_accepted_receipt_cannot_fail_an_unrelated_open_history_page(self):
        self.check_receipt_collision("recv")

    def check_receipt_collision(self, state):
        mid = sid()

        async def create_collision():
            old, session, _ = self.boot()
            open_page = old.hist_add({"k": "phone", "dev": session.device, "source_kind": "phone",
                                      "source_role": "owner", "text": "磁盘历史中未完成的另一条消息"}, "", "open")
            await diskq.aflush()
            history.set_enabled(self.st, False)
            off, session, frames = self.boot()
            self.pair_origin(off, session)
            received = await self.say(off, session, frames, mid, text="关闭历史后已经交给执行器的问题")
            send = off.agent.got[0]
            if state == "deliv":
                off.say_delivered(send)
            off.receipts.flush()
            self.assertEqual(received["turn"], open_page["id"])
            self.assertEqual(off.receipts.state(session.device, mid), state)
            self.assertIsNone(upgrade_messages.reply_page_coordinates(off, send))
            history.set_enabled(self.st, True)
            return open_page

        old = asyncio.run(create_collision())

        async def after_restart():
            host, session, frames = self.boot()
            self.assertEqual(host.hist.get(old["id"]), old)
            self.assertEqual(host.receipts.state(session.device, mid), "lost")
            self.assertEqual((await self.say(host, session, frames, mid))["why"], "dup")
            self.assertEqual(host.agent.got, [])
            replies = [row["reply"]["text"] for row in host.hist.page()[0]]
            self.assertEqual(replies.count(humane.of(host, "say.restart_some", n=1)), 1)
            await diskq.aflush()

        for _ in range(2):
            asyncio.run(after_restart())

    def test_final_page_requires_both_persisted_coordinates(self):
        async def go():
            host, s, sent = self.boot()
            self.pair_origin(host, s)
            accepted = await self.say(host, s, sent, sid(), text="今天的问题")
            send = host.agent.got[-1]
            coordinates = upgrade_messages.reply_page_coordinates(host, send)
            self.assertEqual(coordinates, (host.hist.epoch, host.hist.get(send.turn)["ts"]))
            host.hist_update(accepted["turn"], text="今天的答案", end="done")
            epoch, stamp = coordinates
            self.assertTrue(upgrade_messages.answered_page(host.hist, s.device, send.turn, coordinates))
            self.assertFalse(upgrade_messages.answered_page(host.hist, s.device, send.turn, (epoch + 1, stamp)))
            self.assertFalse(upgrade_messages.answered_page(host.hist, s.device, send.turn, (epoch, stamp + 1)))
            host.hist = history.History(self.st)     # real history_reload replaces the object
            self.assertIsNone(upgrade_messages.reply_page_coordinates(host, send))

        asyncio.run(go())

    def test_native_final_before_receipt_is_one_original_reply_after_two_restarts(self):
        mid = sid()

        async def finish_native():
            host, s, sent = self.boot()
            self.pair_origin(host, s)
            accepted = await self.say(host, s, sent, mid, text="这次升级修了什么？")
            send = host.agent.got[-1]
            send.state = "delivered"
            host.say_delivered(send)
            claude = agent.ClaudeAgent(host, {"kind": "claude", "dir": str(self.st.root)})
            proc = object()
            claude.proc = claude.turn_proc = proc
            claude.turn_done = asyncio.Event()
            claude.cur_send = send
            claude.init = {"session_id": "actual-native-session"}
            host.agent, host.cur_turn = claude, send.turn
            result = {"type": "result", "subtype": "success", "is_error": False,
                      "session_id": "actual-native-session", "result": "升级后的原回复"}
            upgrade_messages.checkpoint_reply(claude, proc, {**result, "session_id": "unrelated-session"})
            self.assertEqual(host.hist.get(send.turn)["end"], "open")
            claude._observe_restart_result(object(), result)
            self.assertIsNone(claude._restart_result)
            claude._observe_restart_result(proc, result)
            upgrade_messages.checkpoint_reply(claude, proc, claude._restart_result)
            upgrade_messages.checkpoint_reply(claude, proc, claude._restart_result)
            self.assertEqual(host.hist.get(send.turn)["reply"]["text"], "升级后的原回复")
            self.assertEqual(host.hist.get(send.turn)["end"], "done")
            host.receipts.flush()
            return accepted["turn"]

        original_page = asyncio.run(finish_native())

        async def after_restart():
            host, s, sent = self.boot()
            self.assertEqual(host.receipts.state(s.device, mid), "done")
            self.assertEqual(host.receipts.interrupted, [])
            self.assertEqual((await self.say(host, s, sent, mid))["why"], "dup")
            self.assertEqual(host.agent.got, [])
            replies = [t for t in host.hist.page()[0] if "升级后的原回复" in t["reply"]["text"]]
            self.assertEqual([(t["id"], t["end"], t["reply"]["text"]) for t in replies],
                             [(original_page, "done", "升级后的原回复")])

        for _ in range(2):
            asyncio.run(after_restart())

    def test_native_tool_or_ambiguous_page_cannot_checkpoint(self):
        async def go():
            mid = sid()
            host, s, sent = self.boot()
            self.pair_origin(host, s)
            await self.say(host, s, sent, mid, text="帮我处理一下这份报告")
            send = host.agent.got[-1]
            send.state = "delivered"
            host.say_delivered(send)
            claude = agent.ClaudeAgent(host, {"kind": "claude", "dir": str(self.st.root)})
            proc = object()
            claude.proc = claude.turn_proc = proc
            claude.turn_done = asyncio.Event()
            claude.cur_send = send
            claude.init = {"session_id": "actual-native-session"}
            host.agent, host.cur_turn = claude, send.turn
            result = {"type": "result", "subtype": "success", "is_error": False,
                      "session_id": "actual-native-session", "result": "另一段正文"}
            claude._observe_restart_result(proc, {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}})
            claude._observe_restart_result(proc, result)
            self.assertIsNone(claude._restart_result)
            host.hist_update(send.turn, text="已有内容与结果不吻合")
            upgrade_messages.checkpoint_reply(claude, proc, result)
            self.assertEqual(host.hist.get(send.turn)["end"], "open")
            host.receipts.flush()
            return mid, send.turn

        mid, turn = asyncio.run(go())

        async def after_restart():
            host, phone, _ = self.boot()
            self.assertEqual(host.receipts.state(phone.device, mid), "lost")
            self.assertEqual(host.hist.get(turn)["end"], "failed")
            self.assertEqual(host.agent.got, [])

        asyncio.run(after_restart())

    def test_withdrawn_and_stopped_messages_finish_their_receipt(self):
        async def go():
            host, s, sent = self.boot()
            x, y = sid(), sid()
            await self.say(host, s, sent, x)
            await self.say(host, s, sent, y)
            await host._app(s, {"t": "say_cancel", "sid": x})
            host.queue_dropped(host.agent.got[1])
            self.assertEqual(host.receipts.state(self.ph.did, x), "done")
            self.assertEqual(host.receipts.state(self.ph.did, y), "done")
            host.receipts.flush()
        asyncio.run(go())
        self.assertEqual(receipts.Receipts(self.st.root).interrupted, [], "never ran → nothing to tell after a restart")


if __name__ == "__main__":
    unittest.main()
