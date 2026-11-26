"""0378 (0.30.78, chairman 11-26): a phone /compact stalled the shared Claude session. Claude Code 2.1.323's one-time
"We recommend compacting earlier" dialog swallowed the typed control; the host waited ten silent minutes, the messages
behind it were written into a native queue that never dequeued them, and their pages stayed `held: pending` for good —
also after 「重启 Agent」. Entirely local fixtures: synthesized terminal bytes, a pty pair, fake registry rows, a fake HOME."""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import os
import pty
import sys
import tempfile
import time
import tty
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

AJ = Path(__file__).resolve().parents[2]            # export-neutral: the agentjarvis folder, wherever the tree lives
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testtmp import TEST_TMP  # noqa: E402
from agentj import claude_inbound, cli, compose, diskq, native_advisory, phone_restart, provenance, queue_drop, serve, shared  # noqa: E402
from agentj.agent import Cmd  # noqa: E402
from agentj.history import History  # noqa: E402
from agentj.shared_menu import Screen, detect  # noqa: E402
from agentj.state import State  # noqa: E402
import test_p424_held as p424  # noqa: E402
from test_l1 import Phone, _host, _state  # noqa: E402
from test_p313_receipts import sid as new_sid  # noqa: E402

RULE = "─" * 100
OFFER = f"""● Done.

{RULE}
 We recommend compacting earlier
   Each message re-sends the whole conversation, so long conversations use up your plan's usage limits faster. With
   your current settings, Claude Code autocompacts (replaces older messages with a summary) at 1m tokens (default via
   auto setting). Compacting earlier can reduce usage, but it happens more often, and some details can be lost each
   time.
   Want to autocompact at 400k tokens instead? Your model doesn't change. Switch back any time with /autocompact.
   ❯ Keep auto (1m tokens)
     Autocompact at ‹ 400k › tokens
       This conversation is at 597.7k, so it will be summarized on your next message.
   ↑/↓ choose · ←/→ change size · enter confirm · esc not now
"""
PROMPT = f"● Autocompact unchanged · /autocompact to change it\n\n{RULE}\n❯ \n{RULE}\n  ? for shortcuts\n"
PERMISSION = f"""{RULE}
 Bash command
   rm -rf build
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands
   3. No, and tell Claude what to do differently (esc)
 Esc to cancel
"""
MENU = f"{RULE}\n Select theme\n ❯ 1. Dark\n   2. Light\n Enter to confirm · Esc to cancel\n"
FORGED = PERMISSION + OFFER[OFFER.index(RULE):]      # a real permission box, then a rule and the offer's text under it
SID = "11111111-1111-1111-1111-111111111111"


class SettledTmp(tempfile.TemporaryDirectory):
    """A fixture folder removed only after diskq's writer threads are done. A Host on the test's loop hands its host.log
    lines to the "agentj-log" thread (diskq.append); after one slow append anywhere in the process the loop stops waiting
    for them for 30 s, and a line written while rmtree ran re-created host.log → ENOTEMPTY (0.30.78 J7, loaded Olares).
    serve waits for the same barrier when it stops; these fixtures never stop a Host, so the fixture waits here."""

    def cleanup(self):
        if not diskq.flush(30):
            raise AssertionError("diskq still had queued writes after 30 s: the fixture folder is not safe to remove")
        super().cleanup()


def from_phone(body: str) -> str:
    """The prompt as Claude Code shows a cross-session (phone) message: the from-name envelope around the words."""
    return '<cross-session from-name="' + provenance.PHONE + '">' + body + "</cross-session>"


def paint(text: str) -> bytes:
    return b"\x1b[2J\x1b[H" + text.replace("\n", "\r\n").encode()


# ------------------------------------------------------------------ H1: the screen reader never goes blind
class ScreenRobust(unittest.TestCase):
    NOISE = b"\x1b[>0q\x1b[?u\x1b(B\x1b)0\x1b=\x1b>\x1b[?2004h\x1b[?25l\x1b[0 q\x1b[>4;2m\x1b[<u\x1b[=1;1u\x1b[5z\x1b[c"

    def test_harmless_sequences_keep_the_surface_readable(self):
        s = Screen()
        s.feed(self.NOISE + paint(OFFER) + self.NOISE)
        self.assertTrue(s.valid)
        self.assertEqual(s.pending, "")
        self.assertIn("We recommend compacting earlier", s.text())
        self.assertEqual(detect(s.text()), {"kind": "autocompact_offer", "reset": "", "keys": []})

    def test_each_named_sequence_alone(self):
        for seq in (b"\x1b[>0q", b"\x1b[?u", b"\x1b(B", b"\x1b)B", b"\x1b=", b"\x1b>", b"\x1b[0 q", b"\x1b[?1049h", b"\x1bc"):
            with self.subTest(seq=seq):
                s = Screen()
                s.feed(seq + b"hello")
                self.assertTrue(s.valid, seq)
                self.assertEqual(s.pending, "", seq)
                self.assertEqual(s.text().strip(), "hello", seq)

    def test_byte_by_byte_is_the_same_screen(self):
        data = self.NOISE + paint(OFFER) + b"\x1b]0;title\x07\x1bP1$r0 q\x1b\\" + self.NOISE
        whole, split = Screen(), Screen()
        whole.feed(data)
        for i in range(len(data)):
            split.feed(data[i:i + 1])
        self.assertEqual(split.text(), whole.text())
        self.assertNotEqual(whole.text(), "")
        self.assertEqual(split.pending, "")

    def test_private_u_is_not_restore_cursor(self):
        s = Screen()
        s.feed(b"\x1b[5;5H\x1b[s\x1b[1;1HX\x1b[?uY")       # the old reader took `ESC [ ? u` for `ESC [ u`
        self.assertEqual(s.text().splitlines()[0], "XY")

    def test_malformed_and_overlong_never_stall(self):
        s = Screen()
        s.feed(b"\x1b[\x01ab")                              # not a CSI at all: dropped, the text goes on
        self.assertEqual(s.pending, "")
        self.assertIn("ab", s.text())
        s.feed(b"\x1b]52;c;" + b"A" * 20000)                # an unterminated string: withheld while it lasts…
        self.assertEqual(s.text(), "")
        s.feed(b"\x07" + paint("back"))                     # …and readable again at its terminator
        self.assertEqual(s.text().strip(), "back")

    def test_unmodelled_scroll_region_is_withheld_until_a_clear(self):
        s = Screen()
        s.feed(b"x\x1b[r\x1b[1;40r")                        # the default region changes nothing
        self.assertEqual(s.text().strip(), "x")
        s.feed(b"\x1b[5;20r")
        self.assertEqual(s.text(), "", "fail closed: never keys on a screen we cannot place")
        s.feed(paint("fresh"))
        self.assertEqual(s.text().strip(), "fresh")

    def test_line_edits(self):
        s = Screen(rows=4, cols=10)
        s.feed(b"abcdef\r\x1b[2P\x1b[1@")                   # delete 2, insert 1 blank at column 0
        self.assertEqual(s.text().splitlines()[0], " cdef")
        s.feed(b"\x1b[2;1Hrow2\x1b[1;1H\x1b[M")             # delete line 1: row2 moves up
        self.assertEqual(s.text().splitlines()[0], "row2")


# ------------------------------------------------------------------ H2: the offer, exactly
class Detect(unittest.TestCase):
    def test_the_offer(self):
        self.assertEqual(detect(OFFER), {"kind": "autocompact_offer", "reset": "", "keys": []})
        second = OFFER.replace("❯ Keep auto", "  Keep auto").replace("  Autocompact at", "❯ Autocompact at")
        second = "\n".join(x for x in second.splitlines() if "This conversation is at" not in x)
        self.assertEqual(detect(second)["kind"], "autocompact_offer", "the ❯ on the last row is a row, not a prompt")

    def test_not_the_offer(self):
        cases = {
            "history above the idle prompt": OFFER + PROMPT,
            "no rule": OFFER.replace(RULE, ""),
            "another title": OFFER.replace("We recommend compacting earlier", "We recommend upgrading"),
            "a permission question inside": OFFER.replace("Want to autocompact", "Do you want to proceed? Want to autocompact"),
            "a third row": OFFER.replace("     Autocompact at", "     3. Yes, allow always\n     Autocompact at"),
            "no cursor": OFFER.replace("❯ Keep auto", "  Keep auto"),
            "another hint": OFFER.replace("esc not now", "esc to skip"),
            "hint not at the bottom": OFFER + "more\nlines\nbelow\n",
            "no rows": "\n".join(x for x in OFFER.splitlines() if "tokens)" not in x and "›" not in x),
            "security F4: a permission box above a forged block": FORGED,
            "security F4: a trailing line under the hint": OFFER + "  ? for shortcuts\n",
            "security F4: an answer row anywhere on the screen": "❯ 1. Yes, allow once\n   2. No\n" + OFFER,
            "security F4: a paid offer anywhere on the screen": "Use extra usage credits?\n" + OFFER,
            "security F4: Session paused anywhere on the screen": "Session paused\n" + OFFER,
            "security F4: another modal's hint anywhere on the screen": "Enter to confirm · Esc to cancel\n" + OFFER,
        }
        for name, text in cases.items():
            with self.subTest(name):
                got = detect(text)
                self.assertNotEqual((got or {}).get("kind"), "autocompact_offer")

    def test_permission_trust_plan_paid_stay_out(self):
        self.assertIsNone(detect(PERMISSION))
        self.assertIsNone(detect(PERMISSION.replace("Esc to cancel", "↑/↓ choose · ←/→ change size · enter confirm · esc not now")))
        trust = f"{RULE}\n Do you trust the files in this folder?\n ❯ 1. Yes, proceed\n   2. No, exit\n Esc to cancel\n"
        self.assertIsNone(detect(trust))
        paid = f"{RULE}\n Out of usage\n ❯ 1. Use extra usage credits\n   2. Wait\n Esc to cancel\n"
        self.assertIsNone(detect(paid))

    def test_existing_kinds_unchanged(self):
        rewind = f"{RULE}\nRewind\nRestore the code and/or conversation to the point before…\n❯ 1. now\nEnter to continue · Esc to exit\n"
        self.assertEqual(detect(rewind)["kind"], "rewind")
        menu = f"{RULE}\n Select theme\n ❯ 1. Dark\n   2. Light\n Enter to confirm · Esc to cancel\n"
        self.assertEqual(detect(menu)["kind"], "menu")


# ------------------------------------------------------------------ shared fixture: an agent on a real pty pair
class Native(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = SettledTmp(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = Mock()
        self.host.lang = "zh"
        self.host.st.root = self.root
        self.host.st.agent_session.return_value = None
        self.host.stopped.return_value = False
        self.host.asks = {}
        self.host.question = AsyncMock(return_value=("timeout", None))
        self.a = shared.SharedClaudeAgent(self.host, {"kind": "claude", "dir": str(self.root), "session_mode": "shared",
                                                      "_workflow_ceo": True})
        self.a.cfg.pop("_workflow_ceo")
        self.a.cfg["shared_identity"] = False
        self.host.agent = self.a
        self.a.session = {"sessionId": SID, "pid": 123, "status": "idle", "version": "2.1.323"}
        self.a.read_statusline = Mock()
        self.a.cur_cmd = Cmd("compact", turn=9)
        self.reg, self.waiting_for = "idle", None
        live = patch.object(shared, "claude_sessions", side_effect=lambda d, sid="", *a: [
            {**self.a.session, "sessionId": sid or SID, "status": self.reg, "waitingFor": self.waiting_for}])
        live.start()
        self.addCleanup(live.stop)
        fresh = patch.object(shared.handoff, "fresh", return_value=True)
        fresh.start()
        self.addCleanup(fresh.stop)
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(slave, False)
        self.a.master, self.slave = master, slave
        self.a.native = Mock(pid=123, returncode=None)
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)

    def typed(self) -> bytes:
        try:
            return os.read(self.slave, 4096)
        except BlockingIOError:
            return b""

    def logs(self, name):
        return [c.kwargs for c in self.host.st.log.call_args_list if c.args and c.args[0] == name]

    def show(self, text):
        self.a.menu_desk.surface.feed(paint(text))

    async def close_on_esc(self, seen):
        """The stand-in native: Esc on the offer = "not now" (the dialog goes, the prompt is back, registry idle)."""
        while True:
            got = self.typed()
            if got:
                seen.append(got)
                if got == b"\x1b":
                    self.show(PROMPT)
                    self.reg, self.waiting_for = "idle", None
            await asyncio.sleep(.02)


# ------------------------------------------------------------------ H3: one Esc, only there
class Advisory(Native):
    async def test_offer_on_our_own_pty_is_dismissed_once(self):
        self.show(OFFER)
        self.reg, self.waiting_for = "waiting", "dialog open"
        seen = []
        watcher = asyncio.create_task(self.close_on_esc(seen))
        try:
            await self.a.menu_desk.poll()
            self.assertIsNone(self.a.menu_desk.current)
            await self.a.menu_desk.poll()
        finally:
            watcher.cancel()
        self.assertEqual(seen, [b"\x1b"], "a single Esc, nothing else")
        self.assertEqual(self.logs("shared_native_notice"),
                         [{"kind": "autocompact_offer", "action": "not_now", "result": "closed"}])
        self.host.question.assert_not_called()
        self.assertEqual(self.a._control_gate("idle"), (True, "idle"))

    async def test_offer_is_dismissed_before_a_phone_message_is_written(self):
        self.show(OFFER)
        self.reg, self.waiting_for = "waiting", "dialog open"
        seen, order = [], []
        watcher = asyncio.create_task(self.close_on_esc(seen))
        self.a.cur_send = None
        try:
            with patch.object(shared, "claude_send", side_effect=lambda *x: order.append(b"".join(seen))), \
                    patch.object(shared, "CLAUDE_INPUT_WAIT", .05), patch.object(claude_inbound, "hold_reason", return_value="default"):
                await asyncio.wait_for(self.a.turn("手机上的下一条"), 8)
        finally:
            watcher.cancel()
        self.assertEqual(order, [b"\x1b"], "the dialog was told `not now` before the message went to the native queue")

    async def test_still_open_is_not_pressed_again_and_becomes_the_owners_card(self):
        self.show(OFFER)
        self.reg, self.waiting_for = "waiting", "dialog open"
        with patch.object(native_advisory.asyncio, "sleep", new=AsyncMock()):
            await self.a.menu_desk.poll()
            await self.a.menu_desk.poll()
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(self.typed(), b"\x1b")
        self.assertEqual(self.logs("shared_native_notice")[0]["result"], "still_open")
        self.assertEqual(self.a._control_gate("idle"), (False, "menu"), "a control is never typed onto it")
        self.host.question.assert_awaited()

    async def test_registry_must_agree(self):
        self.show(OFFER)                                    # registry still `idle`: a screen alone presses nothing
        await self.a.menu_desk.poll()
        self.assertEqual(self.typed(), b"")
        self.assertEqual(self.a._control_gate("idle"), (False, "menu"))
        self.assertEqual(self.logs("shared_native_notice"), [])

    async def test_a_permission_looking_dialog_is_never_escaped(self):
        self.reg, self.waiting_for = "waiting", "dialog open"
        for text in (PERMISSION, FORGED,
                     PERMISSION.replace("Esc to cancel", "↑/↓ choose · ←/→ change size · enter confirm · esc not now"),
                     OFFER.replace("Want to autocompact", "Do you want to proceed? Want to autocompact")):
            with self.subTest(text=text[-60:]):
                self.show(text)
                await self.a.menu_desk.poll()
                await self.a.menu_desk.poll()
                self.assertEqual(self.typed(), b"", "constitution: no key into a permission box")
        self.assertEqual(self.logs("shared_native_notice"), [])
        self.host.question.assert_not_called()

    async def test_hook_known_permission_card_blocks_it(self):
        self.show(OFFER)
        self.reg, self.waiting_for = "waiting", "dialog open"
        self.a.state.handle({"hook_event_name": "PermissionRequest", "tool_name": "Bash"})
        self.assertEqual(self.a.state.status, "waiting")
        await self.a.menu_desk.poll()
        self.assertEqual(await native_advisory.advisory(self.a, detect(OFFER)), "wait")
        self.assertEqual(self.typed(), b"")

    async def test_stop_and_pty_replacement_after_registry_read_do_not_write(self):
        self.show(OFFER)
        for change in (
            lambda: self.host.stopped.configure_mock(return_value=True),
            lambda: setattr(self.a, "halting", True),
            lambda: setattr(self.a, "session", {**self.a.session, "sessionId": "other"}),
            lambda: self.a.menu_desk.surface.feed(b"\x1b["),
        ):
            with self.subTest(change=change):
                old = self.a.session
                row = {**old, "status": "waiting", "waitingFor": "dialog open"}

                async def read(*args):
                    change()
                    return row

                with patch.object(native_advisory.asyncio, "to_thread", side_effect=read):
                    self.assertEqual(await native_advisory.advisory(self.a, detect(OFFER)), "wait")
                self.assertEqual(self.typed(), b"")
                self.host.stopped.return_value = False
                self.a.session, self.a.halting = old, False

    async def test_the_owners_own_terminal_is_never_pressed(self):
        self.a.native = None                                # herdr: the owner started this process in a terminal
        self.reg, self.waiting_for = "waiting", "dialog open"
        desk = self.a.menu_desk
        with patch.object(type(desk), "screen_text", new=AsyncMock(return_value=(OFFER, ("herdr", "pane-1")))), \
                patch("agentj.shared_interrupt._run") as run, patch("agentj.shared_interrupt._agents", return_value=[]):
            await desk.poll()
            for _ in range(5):
                await asyncio.sleep(0)
            run.assert_not_called()
        self.assertEqual(self.typed(), b"")
        self.assertEqual(self.logs("shared_native_notice"), [])
        self.assertEqual(desk.current["kind"], "autocompact_offer", "it stays blocked there")
        self.host.question.assert_awaited()                 # the owner's own menu card, as for any other menu
        self.assertIn("建议提早自动压缩", self.host.question.await_args.args[0][0]["q"])


# ------------------------------------------------------------------ H4: the control's life is logged and bounded
class MenuCards(Native):
    async def test_owned_pty_menu_and_paused_card_choices_after_terminal_sequences(self):
        from test_p451_menu import QUOTA
        from test_p539_flag import PAUSED

        async def read_key(want):
            for _ in range(100):
                got = self.typed()
                if got:
                    self.assertEqual(got, want)
                    os.write(self.slave, paint(PROMPT))
                    self.a._drain_terminal()
                    return
                await asyncio.sleep(.01)
            self.fail("no menu key from verified choice")

        for screen, kind in ((MENU, "menu"), (QUOTA, "quota"), (PAUSED, "paused")):
            with self.subTest(kind=kind):
                self.host.question.reset_mock()
                self.host.question.return_value = ("answer", [[1]]) if kind == "paused" else ("cancel", None)
                os.write(self.slave, ScreenRobust.NOISE + paint(screen))
                self.a._drain_terminal()
                await self.a.menu_desk.poll()
                self.assertEqual(self.a.menu_desk.current["kind"], kind)
                await read_key(b"\r" if kind == "paused" else b"\x1b")
                await asyncio.wait_for(self.a.menu_desk.task, 4)
                self.host.question.assert_awaited_once()
                await self.a.menu_desk.poll()
                self.assertIsNone(self.a.menu_desk.current)


class Control(Native):
    async def until_typed(self, want, secs=2.0):
        got, end = b"", time.monotonic() + secs
        while got != want and time.monotonic() < end:
            got += self.typed()
            await asyncio.sleep(.02)
        return got

    async def hook(self, name, **kw):
        await self.a.hook_event({"hook_event_name": name, "session_id": kw.pop("session_id", SID), **kw})

    async def test_compact_sent_accepted_done(self):
        task = asyncio.create_task(self.a.command("compact", ""))
        self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r")
        self.assertEqual(self.logs("shared_control_sent"), [{"cmd": "compact", "transport": "pty"}])
        self.assertEqual(self.logs("shared_control_accepted"), [])
        self.assertIn("已发给主会话", self.host.cmd_waiting.call_args.args[1])
        await self.hook("PreCompact", trigger="manual")
        await asyncio.sleep(.6)
        self.assertFalse(task.done(), "PreCompact is acceptance, not completion")
        self.assertEqual([x["cmd"] for x in self.logs("shared_control_accepted")], ["compact"])
        self.assertIn("正在压缩", self.host.cmd_waiting.call_args.args[1])
        await self.hook("PostCompact")
        res = await asyncio.wait_for(task, 2)
        self.assertEqual(res.kind, "ok")
        done = self.logs("shared_control_done")
        self.assertEqual([x["cmd"] for x in done], ["compact"])
        self.assertGreaterEqual(done[0]["ms"], self.logs("shared_control_accepted")[0]["ms"])
        self.assertEqual(self.logs("shared_control_unaccepted"), [])

    async def test_clear_accepted_by_its_session_start(self):
        self.a.cur_cmd = Cmd("clear", turn=9)
        task = asyncio.create_task(self.a.command("clear", ""))
        self.assertEqual(await self.until_typed(b"/clear\r"), b"/clear\r")
        await self.hook("SessionStart", source="clear", session_id="22222222-2222-2222-2222-222222222222")
        self.assertEqual((await asyncio.wait_for(task, 2)).kind, "ok")
        self.assertEqual([x["cmd"] for x in self.logs("shared_control_accepted")], ["clear"])
        self.assertEqual([x["cmd"] for x in self.logs("shared_control_done")], ["clear"])

    async def test_herdr_transport_is_named(self):
        self.a.native = None
        with patch("agentj.shared_controls.send", return_value="sent"):
            task = asyncio.create_task(self.a.command("compact", ""))
            await asyncio.sleep(.2)
            self.a.control_accepted.set()
            self.a.control_ack.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.logs("shared_control_sent"), [{"cmd": "compact", "transport": "herdr"}])
        self.assertEqual(self.typed(), b"")

    async def test_unaccepted_returns_fast_names_the_restart_and_frees_the_queue(self):
        for name, head in (("compact", "没压缩成"), ("clear", "没清成")):
            with self.subTest(name):
                self.host.st.log.reset_mock()
                self.a.cur_cmd = Cmd(name, turn=9)
                self.reg, self.waiting_for = "waiting", "dialog open"      # what the registry says once the control was typed
                gate = patch.object(self.a, "_control_gate", return_value=(True, "idle"))
                t0 = time.monotonic()
                with gate, patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .3):
                    res = await asyncio.wait_for(self.a.command(name, ""), 5)
                self.assertLess(time.monotonic() - t0, 3, "not CONTROL_WAIT (600 s)")
                self.assertEqual(res.kind, "error")
                self.assertTrue(res.text.startswith(head), res.text)
                self.assertIn("设置 → 紧急修复 →「重启 Agent」", res.text)
                self.assertEqual(self.typed(), ("/" + name + "\r").encode(), "typed once: no blind retry")
                (row,) = self.logs("shared_control_unaccepted")
                self.assertEqual((row["cmd"], row["registry"], row["waiting_for"], row["menu"]), (name, "waiting", "dialog open", None))
                self.assertIsNone(self.a.control_name)
                self.assertFalse(self.a.native_lock.locked())
                self.reg, self.waiting_for = "idle", None
        # the actor is free: the next phone message is delivered as usual
        self.a.cur_send = None
        with patch.object(shared, "claude_send") as wrote, patch.object(shared, "CLAUDE_INPUT_WAIT", .05), \
                patch.object(claude_inbound, "hold_reason", return_value="default"):
            await asyncio.wait_for(self.a.turn("排在后面的那条"), 3)
        wrote.assert_called_once()
        en = native_advisory.unaccepted_text(SimpleNamespace(host=SimpleNamespace(lang="en")), "compact")
        self.assertIn("Settings → Emergency repair → Restart Agent", en)

    async def test_one_retry_only_after_the_advisory_was_dismissed(self):
        with patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .6), patch.object(native_advisory, "CONTROL_SETTLE_S", .05):
            task = asyncio.create_task(self.a.command("compact", ""))
            self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r")
            self.a._advisory_closed = self.a._settle_at = time.monotonic()    # the offer took the keys; "not now" since
            self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r", "typed once more")
            res = await asyncio.wait_for(task, 5)                       # never accepted: no third attempt
        self.assertEqual(res.kind, "error")
        self.assertEqual(self.typed(), b"")
        self.assertEqual(len(self.logs("shared_control_sent")), 2)
        self.assertEqual(len(self.logs("shared_control_unaccepted")), 1)

    async def test_retry_types_nothing_unless_every_first_typing_precondition_holds(self):
        """security F1: when the dialog closed a queued turn may have started and reached a permission box — the retry's
        Enter must never get there. Registry busy / waiting, a hook-known approval, or a menu on the desk ⇒ zero bytes."""
        def busy():
            self.reg = "busy"

        def waiting():
            self.reg, self.waiting_for = "waiting", "dialog open"

        def approval():
            self.a.state.handle({"hook_event_name": "PermissionRequest", "tool_name": "Bash"})

        def menu():
            self.show(MENU)
        for name, change in (("registry busy", busy), ("registry waiting", waiting), ("approval card", approval), ("menu", menu)):
            with self.subTest(name):
                self.reg, self.waiting_for = "idle", None
                self.a.state = shared.AgentState(on_turn=self.a._committed_stop, warn=lambda m: None, on_drop=self.a._bg_unresolved)
                self.show(PROMPT)
                self.a.menu_desk.current = self.a.menu_desk.key = None
                self.a.cur_cmd = Cmd("compact", turn=9)
                self.host.st.log.reset_mock()
                with patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .5), patch.object(native_advisory, "CONTROL_SETTLE_S", .05):
                    task = asyncio.create_task(self.a.command("compact", ""))
                    self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r")
                    change()                                            # what the closing dialog let through
                    self.a._advisory_closed = self.a._settle_at = time.monotonic()
                    res = await asyncio.wait_for(task, 5)
                self.assertEqual(self.typed(), b"", "not one more byte into the pty")
                self.assertEqual(res.kind, "error")
                self.assertIn("重启 Agent", res.text)
                self.assertEqual(len(self.logs("shared_control_sent")), 1)
                self.assertEqual(len(self.logs("shared_control_unaccepted")), 1)

    async def test_first_typing_and_retry_share_one_gate(self):
        seen = []
        real = self.a._control_ready

        async def ready():
            out = await real()
            seen.append(out[:2])
            return out
        with patch.object(self.a, "_control_ready", new=ready), patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .4), \
                patch.object(native_advisory, "CONTROL_SETTLE_S", .05):
            task = asyncio.create_task(self.a.command("compact", ""))
            self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r")
            n = len(seen)
            self.a._advisory_closed = self.a._settle_at = time.monotonic()
            self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r")
            self.assertEqual(seen[n:], [(True, "idle")], "the retry asked the same helper, once, and it held")
            await asyncio.wait_for(task, 5)
        self.a._settle_at = None
        self.assertEqual((await self.a._control_ready())[:2], (True, "idle"))
        self.a.native_lock = SimpleNamespace(locked=lambda: True)
        self.assertEqual((await self.a._control_ready())[:2], (False, "lock"))
        self.a.native_lock = asyncio.Lock()
        self.a.cur_cmd.cancelled = True
        self.assertEqual((await self.a._control_ready())[:2], (False, "cancelled"))

    async def test_waiting_for_is_logged_through_an_allowlist(self):
        self.assertEqual([native_advisory.waiting_for({"waitingFor": x}) for x in
                          ("dialog open", "input needed", "sandbox request", "goal proposal", "rm -rf ~/secret-project", "", None)],
                         ["dialog open", "input needed", "sandbox request", "goal proposal", "other", None, None])
        self.reg, self.waiting_for = "waiting", "approve: curl https://example.com/private"
        with patch.object(self.a, "_control_gate", return_value=(True, "idle")), patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .2):
            await asyncio.wait_for(self.a.command("compact", ""), 5)
        self.assertEqual(self.logs("shared_control_unaccepted")[0]["waiting_for"], "other")
        with patch.object(native_advisory, "STUCK_WAITING_S", 0):
            self.a.cur_cmd = None
            native_advisory.watch(self.a, {"status": "waiting", "waitingFor": "approve: curl https://example.com/private"})
            native_advisory.watch(self.a, {"status": "waiting", "waitingFor": "approve: curl https://example.com/private"})
        self.assertEqual(self.logs("shared_native_stuck")[0]["waiting_for"], "other")

    async def test_handoff_prompt_never_taken_fails_within_the_acceptance_bound(self):
        """Q1: a native that cannot dequeue — the handoff turn never starts. Not PREP_WAIT (300 s) of 「等…忙完」."""
        t0 = time.monotonic()
        with patch.object(shared.handoff, "fresh", return_value=False), patch.object(shared.handoff, "ensure"), \
                patch.object(shared.handoff, "mtime", return_value=None), patch.object(shared, "claude_send") as wrote, \
                patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .3), patch.object(shared.handoff, "PREP_WAIT", 30.0):
            res = await asyncio.wait_for(self.a.command("compact", ""), 10)
        self.assertLess(time.monotonic() - t0, 3)
        wrote.assert_called_once()
        self.assertEqual(self.typed(), b"", "the control itself was never typed")
        self.assertEqual(res.kind, "error")
        self.assertIn("设置 → 紧急修复 →「重启 Agent」", res.text)
        (row,) = self.logs("shared_control_unaccepted")
        self.assertEqual((row["cmd"], row["stage"], row["registry"]), ("compact", "handoff", "idle"))
        self.assertIn("正在让主会话先写交接", self.host.cmd_waiting.call_args_list[0].args[1])

    async def test_handoff_turn_that_started_keeps_prep_wait(self):
        for signal in ("prompt", "record", "stop"):
            with self.subTest(signal):
                self.host.st.log.reset_mock()
                self.a.cur_cmd = Cmd("compact", turn=9)
                self.a._settle_at = None
                sent = []
                with patch.object(shared.handoff, "fresh", side_effect=lambda *x: bool(sent) and self.a.system_done.is_set()), \
                        patch.object(shared.handoff, "ensure"), patch.object(shared.handoff, "mtime", return_value=None), \
                        patch.object(shared, "claude_send", side_effect=lambda s, text, src: sent.append(text)), \
                        patch.object(native_advisory, "CONTROL_ACCEPT_WAIT", .3), patch.object(native_advisory, "CONTROL_SETTLE_S", .05):
                    task = asyncio.create_task(self.a.command("compact", ""))
                    await asyncio.sleep(.15)
                    self.assertEqual(len(sent), 1)
                    if signal == "prompt":
                        await self.hook("UserPromptSubmit", prompt=from_phone(sent[0]))
                    elif signal == "record":
                        self.a._held_seen(from_phone(sent[0]))
                    if signal != "stop":
                        await asyncio.sleep(.5)                 # past the acceptance bound: the turn is running, we wait
                        self.assertFalse(task.done())
                        self.assertEqual(self.logs("shared_control_unaccepted"), [])
                    await self.hook("Stop", last_assistant_message="handoff written")
                    self.a.system_done.set()
                    self.assertEqual(await self.until_typed(b"/compact\r", 4), b"/compact\r")
                    self.a.control_accepted.set()
                    self.a.control_ack.set()
                    self.assertEqual((await asyncio.wait_for(task, 3)).kind, "ok")
                self.assertEqual(self.logs("shared_control_unaccepted"), [])

    async def test_unknown_dialog_ends_the_waiting_control(self):
        """Q2: registry `waiting`, nothing of ours knows why — the control ends after STUCK_WAITING_S, the queue is free."""
        self.reg, self.waiting_for = "waiting", "dialog open"
        t0 = time.monotonic()
        with patch.object(native_advisory, "STUCK_WAITING_S", .4):
            res = await asyncio.wait_for(self.a.command("compact", ""), 5)
        self.assertLess(time.monotonic() - t0, 3, "not DESKTOP_IDLE_WAIT (1800 s)")
        self.assertEqual(res.kind, "error")
        self.assertIn("停在一个提示框上", res.text)
        self.assertIn("设置 → 紧急修复 →「重启 Agent」", res.text)
        self.assertEqual(self.typed(), b"")
        (row,) = self.logs("shared_control_unaccepted")
        self.assertEqual((row["cmd"], row["stage"], row["registry"], row["waiting_for"]), ("compact", "gate", "waiting", "dialog open"))
        self.assertFalse(self.a.cur_cmd.native_started)

    async def test_known_waits_keep_the_long_wait(self):
        def approval():
            self.reg = "waiting"
            self.a.state.handle({"hook_event_name": "PermissionRequest", "tool_name": "Bash"})

        def menu():
            self.reg = "waiting"
            self.show(MENU)

        def turn():
            self.reg = "busy"
            self.a.state.handle({"hook_event_name": "UserPromptSubmit"})
        for name, setup in (("approval card", approval), ("menu card", menu), ("foreground turn", turn)):
            with self.subTest(name):
                self.a.state = shared.AgentState(on_turn=self.a._committed_stop, warn=lambda m: None, on_drop=self.a._bg_unresolved)
                self.show(PROMPT)
                self.a.menu_desk.current = self.a.menu_desk.key = None
                self.a.cur_cmd = Cmd("compact", turn=9)
                self.host.st.log.reset_mock()
                setup()
                with patch.object(native_advisory, "STUCK_WAITING_S", .2), patch.object(shared, "DESKTOP_IDLE_WAIT", 1.0):
                    res = await asyncio.wait_for(self.a.command("compact", ""), 5)
                self.assertEqual(self.logs("shared_control_unaccepted"), [])
                self.assertEqual(len(self.logs("shared_control_timeout")), 1, "today's long wait, to its own end")
                self.assertEqual(res.kind, "error")

    async def test_control_is_one_write_from_2_1_323(self):
        """security r2: no gap between the text and its Enter where the native is verified to take one chunk."""
        self.assertEqual([native_advisory.single_chunk({"version": v}) for v in
                          ("2.1.323", "2.1.324", "2.2.0", "3.0.0", "2.1.322", "2.1.296", "1.9.999", "", "2.1", "x.y.z", None)],
                         [True, True, True, True, False, False, False, False, False, False, False])
        for version, want in (("2.1.323", [b"/compact\r"]), ("2.1.322", [b"/compact", b"\r"]), (None, [b"/compact", b"\r"])):
            with self.subTest(version):
                self.a.session = {"sessionId": SID, "pid": 123, "status": "idle", **({"version": version} if version else {})}
                self.a.cur_cmd = Cmd("compact", turn=9)
                writes = []
                real = os.write

                def write(fd, data):
                    if fd == self.a.master:
                        writes.append(data)
                        if data.endswith(b"\r"):
                            self.a.control_accepted.set()
                            self.a.control_ack.set()
                    return real(fd, data)
                with patch.object(shared.os, "write", side_effect=write):
                    self.assertEqual((await asyncio.wait_for(self.a.command("compact", ""), 3)).kind, "ok")
                self.assertEqual(writes, want)
                self.typed()

    async def test_settle_guard_waits_out_the_dialog_race(self):
        await self.hook("Stop", last_assistant_message="handoff written")     # the turn just ended; registry reads idle
        with patch.object(native_advisory, "CONTROL_SETTLE_S", .5):
            task = asyncio.create_task(self.a.command("compact", ""))
            await asyncio.sleep(.2)
            self.assertEqual(self.typed(), b"", "idle for 0.2 s after the Stop is not yet idle")
            self.reg, self.waiting_for = "waiting", "dialog open"             # …the offer opens 1–2 s after the Stop
            await asyncio.sleep(.6)
            self.assertEqual(self.typed(), b"", "never typed into a waiting native")
            self.reg, self.waiting_for = "idle", None
            self.assertEqual(await self.until_typed(b"/compact\r"), b"/compact\r")
            self.a.control_accepted.set()
            self.a.control_ack.set()
            self.assertEqual((await asyncio.wait_for(task, 2)).kind, "ok")

    async def test_gate_dismisses_the_offer_then_types(self):
        self.show(OFFER)
        self.reg, self.waiting_for = "waiting", "dialog open"
        seen = []
        watcher = asyncio.create_task(self.close_on_esc(seen))
        try:
            with patch.object(native_advisory, "CONTROL_SETTLE_S", .2):
                task = asyncio.create_task(self.a.command("compact", ""))
                end = time.monotonic() + 5
                while b"".join(seen) != b"\x1b/compact\r" and time.monotonic() < end:
                    await asyncio.sleep(.05)
                self.assertEqual(b"".join(seen), b"\x1b/compact\r", "Esc first, the control only after the dialog is gone")
                await self.hook("PreCompact", trigger="manual")
                await self.hook("PostCompact")
                self.assertEqual((await asyncio.wait_for(task, 2)).kind, "ok")
        finally:
            watcher.cancel()
        self.assertEqual(self.logs("shared_native_notice")[0]["result"], "closed")

    async def test_progress_reaches_the_command_page_after_it_was_typed(self):
        st = State(self.root / "s")
        h = SimpleNamespace(hist=History(st, on=True))
        h.hist_update = lambda tid, **kw: h.hist.update(tid, **kw)
        page = h.hist.add({"k": "cmd", "text": "/compact"}, "", "open")["id"]
        cmd = Cmd("compact", turn=page)
        serve.Host.cmd_waiting(h, cmd, "排队说明")               # before it is typed: the 「取消」 card
        self.assertEqual(h.hist.get(page)["card"], {"cmd": "compact", "cancel_wait": True})
        cmd.native_started = True
        serve.Host.cmd_waiting(h, cmd, "主会话正在压缩…")
        self.assertEqual((h.hist.get(page)["reply"]["text"], h.hist.get(page)["card"], h.hist.get(page)["end"]),
                         ("主会话正在压缩…", {"cmd": "compact", "control_pending": True}, "open"), "progress, and nothing left to cancel")


# ------------------------------------------------------------------ H5: a stuck native is said once
class Stuck(Native):
    ROW = {"status": "waiting", "waitingFor": "dialog open"}

    async def test_waiting_with_nothing_on_the_phone_fires_once(self):
        with patch.object(native_advisory, "STUCK_WAITING_S", .05):
            native_advisory.watch(self.a, self.ROW)
            self.assertEqual(self.logs("shared_native_stuck"), [], "not before the threshold")
            await asyncio.sleep(.08)
            for _ in range(4):
                native_advisory.watch(self.a, self.ROW)
        self.assertEqual(self.logs("shared_native_stuck"), [{"why": "dialog", "registry": "waiting", "waiting_for": "dialog open"}])
        self.host.agent_recovery_notice.assert_called_once()
        self.assertIn("设置 → 紧急修复 →「重启 Agent」", self.host.agent_recovery_notice.call_args.args[0])
        with patch.object(native_advisory, "STUCK_WAITING_S", 0):     # the dialog went, a later one is a new episode
            native_advisory.watch(self.a, {"status": "idle"})
            native_advisory.watch(self.a, self.ROW)
            native_advisory.watch(self.a, self.ROW)
        self.assertEqual(len(self.logs("shared_native_stuck")), 2)

    async def test_known_waits_are_not_stuck(self):
        with patch.object(native_advisory, "STUCK_WAITING_S", 0):
            self.a.state.handle({"hook_event_name": "PermissionRequest", "tool_name": "Bash"})   # a card is on the phone
            native_advisory.watch(self.a, self.ROW)
            native_advisory.watch(self.a, self.ROW)
            self.a.state = SimpleNamespace(status="idle")
            self.a.menu_desk.current = {"kind": "menu"}                                          # a menu card is
            native_advisory.watch(self.a, self.ROW)
            native_advisory.watch(self.a, self.ROW)
        self.assertEqual(self.logs("shared_native_stuck"), [])
        self.host.agent_recovery_notice.assert_not_called()

    async def test_owner_terminal_without_phone_work_is_logged_not_announced(self):
        self.a.native, self.a.cur_cmd = None, None
        with patch.object(native_advisory, "STUCK_WAITING_S", 0):
            native_advisory.watch(self.a, self.ROW)
            native_advisory.watch(self.a, self.ROW)
        self.assertEqual(len(self.logs("shared_native_stuck")), 1)
        self.host.agent_recovery_notice.assert_not_called()

    async def test_owner_terminal_with_a_phone_command_waiting_is_announced(self):
        self.a.native = None                                # cur_cmd: the phone's /compact waits behind that dialog
        with patch.object(native_advisory, "STUCK_WAITING_S", 0):
            native_advisory.watch(self.a, self.ROW)
            native_advisory.watch(self.a, self.ROW)
        self.host.agent_recovery_notice.assert_called_once()

    async def test_observe_calls_the_watch(self):
        seen = []
        with patch.object(native_advisory, "watch", side_effect=lambda a, row: seen.append(row)), \
                patch.object(self.a, "attach", new=AsyncMock()), patch.object(self.a.menu_desk, "poll", new=AsyncMock()):
            self.reg, self.waiting_for = "waiting", "dialog open"
            task = asyncio.create_task(self.a.observe())
            await asyncio.sleep(.1)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertTrue(seen and seen[0]["status"] == "waiting" and seen[0]["waitingFor"] == "dialog open")


class Host0378(p424.Host424):
    def __init__(self, root, lang="zh"):
        super().__init__(root, lang)
        self.sends = SimpleNamespace(items={})

    def stopped(self):
        return False

    def status_changed(self):
        pass


class NoDequeue(unittest.IsolatedAsyncioTestCase):
    agent, not_taken = p424.Held.agent, p424.Held.not_taken

    async def test_taken_meanwhile_is_not_stuck(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            with patch.object(native_advisory, "STUCK_HELD_S", .2):
                tid = await self.not_taken(h, a, "慢了一点", reason=None)
                a._held_seen(a.held[0][1])                  # the native dequeued it after all
                await asyncio.sleep(.3)
            self.assertEqual(h.hist.get(tid)["card"], {})
            self.assertEqual([ev for ev, _ in h.logs if ev == "shared_native_stuck"], [])

    async def test_never_dequeued_flags_the_queue_card_once(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            a.state.status = "idle"
            with patch.object(native_advisory, "STUCK_HELD_S", .2):
                first = await self.not_taken(h, a, "第一条", reason=None)
                self.assertEqual(h.hist.get(first)["card"], {"held": "pending", "queued": True}, "slow is not yet stuck")
                second = await self.not_taken(h, a, "第二条", reason=None)
                await asyncio.sleep(.4)
            for tid in (first, second):
                card = h.hist.get(tid)["card"]
                self.assertEqual((card["held"], card["queued"], card["stuck"]), ("pending", True, True))
            stuck = [kw for ev, kw in h.logs if ev == "shared_native_stuck"]
            self.assertEqual([x["why"] for x in stuck], ["no_dequeue"], "once per episode")
            self.assertEqual(h.notices, [], "P424 #75: no page of its own — the queue card says it")
            self.assertEqual(sorted(a.held_sends), [first, second])

    async def test_a_policy_hold_is_not_a_stuck_native(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            with patch.object(native_advisory, "STUCK_HELD_S", .05):
                tid = await self.not_taken(h, a, "按设置暂存", reason="default")
                await asyncio.sleep(.15)
            self.assertNotIn("stuck", h.hist.get(tid)["card"])
            self.assertEqual([ev for ev, _ in h.logs if ev == "shared_native_stuck"], [])


# ------------------------------------------------------------------ H6: held pages never stay queued for good
class HeldPages(unittest.IsolatedAsyncioTestCase):
    """security F2: a held page's words were already written to the native. Redelivery / 「没有送达」 only when that
    process verifiably ended; otherwise the page leaves the queue as unconfirmed, with a tombstone."""
    agent, not_taken = p424.Held.agent, p424.Held.not_taken

    async def hold(self, h, a, *texts):
        return [await self.not_taken(h, a, t, reason=None) for t in texts]

    def unconfirmed(self, h, tid):
        page = h.hist.get(tid)
        self.assertEqual((page["end"], page["card"]), ("stopped", {"unconfirmed": True}))
        self.assertIn("已从排队中移除", page["reply"]["text"])
        self.assertIn("可能仍会被执行", page["reply"]["text"])
        self.assertIn("设置 → 紧急修复 →「重启 Agent」", page["reply"]["text"])

    async def test_ended_native_redelivers_in_order_to_the_same_conversation(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            sid = a.session["sessionId"]
            one, two = await self.hold(h, a, "先发的", "后发的")
            new = SimpleNamespace(q=asyncio.Queue())
            new.q.put_nowait(SimpleNamespace(sid="later", turn=99, state="pending"))
            items = native_advisory.take_held(a)
            self.assertEqual(a.held, [])
            self.assertEqual(native_advisory.redeliver(h, new, items, sid, True), 2)
            order = [new.q.get_nowait() for _ in range(3)]
            self.assertEqual([x.turn for x in order], [one, two, 99], "held first, in their order, then the queue")
            for tid in (one, two):
                page = h.hist.get(tid)
                self.assertEqual((page["end"], page["card"]), ("open", {"queued": True}))
            self.assertEqual([x.state for x in order[:2]], ["pending", "pending"])
            self.assertEqual([kw["turn"] for ev, kw in h.logs if ev == "shared_held_redelivered"], [one, two])

    async def test_ended_native_other_conversation_or_lost_words_are_undelivered(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            one, two = await self.hold(h, a, "甲", "乙")
            items = native_advisory.take_held(a)
            items[1] = (two, None, items[1][2], items[1][3])            # its words are gone
            new = SimpleNamespace(q=asyncio.Queue())
            self.assertEqual(native_advisory.redeliver(h, new, items[:1], "99999999-9999-9999-9999-999999999999", True), 0)
            self.assertEqual(native_advisory.redeliver(h, new, items[1:], a.session["sessionId"], True), 0)
            self.assertTrue(new.q.empty())
            for tid in (one, two):
                page = h.hist.get(tid)
                self.assertEqual(page["end"], "failed", "leaves the phone's queue (pendingQueue: not failed / stopped)")
                self.assertEqual(page["card"], {"undelivered": True}, "the phone's own sentence keys on this marker")
                self.assertIn("没有送达主会话", page["reply"]["text"])
            self.assertEqual([kw["turn"] for ev, kw in h.logs if ev == "shared_held_undelivered"], [one, two])

    async def test_a_native_that_may_be_alive_is_never_redelivered_or_called_undelivered(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            (one,) = await self.hold(h, a, "进程可能还活着")
            new = SimpleNamespace(q=asyncio.Queue())
            self.assertEqual(native_advisory.redeliver(h, new, native_advisory.take_held(a), a.session["sessionId"]), 0)
            self.assertTrue(new.q.empty(), "no second copy while the first may still run")
            self.unconfirmed(h, one)
            self.assertEqual([ev for ev, _ in h.logs if ev in ("shared_held_undelivered", "shared_held_redelivered")], [])
            self.assertEqual([kw["turn"] for ev, kw in h.logs if ev == "shared_held_unconfirmed"], [one])

    async def test_process_ended_is_a_fact_not_a_guess(self):
        a = SimpleNamespace(session={"pid": os.getpid()}, native=None)
        self.assertFalse(native_advisory.process_ended(a), "a live pid we do not own")
        a.native = Mock(pid=os.getpid(), returncode=None)
        self.assertFalse(native_advisory.process_ended(a), "our child, still running")
        a.native = Mock(pid=os.getpid(), returncode=-15)
        self.assertTrue(native_advisory.process_ended(a), "our child, reaped")
        gone = os.fork()
        if gone == 0:
            os._exit(0)
        os.waitpid(gone, 0)
        self.assertTrue(native_advisory.process_ended(SimpleNamespace(session={"pid": gone}, native=None)))
        self.assertFalse(native_advisory.process_ended(SimpleNamespace(session=None, native=None)))

    async def run_restart(self, h, a, prepare, ended=True):
        made = []

        def make(host, cfg):
            b = SimpleNamespace(kind="claude", cfg=dict(cfg), q=asyncio.Queue(), started=False, status=None)
            b.start = lambda: setattr(b, "started", True)
            b.set_status = lambda s: setattr(b, "status", s)
            b.stop = AsyncMock()
            made.append(b)
            return b
        h.turn_lock, h.grants, h.agent_cfg = asyncio.Lock(), {}, {}
        h.st.agent_session = lambda kind: a.session["sessionId"]
        h.st.set_agent_session = lambda *x: None
        a.cfg["session_mode"] = "shared"
        a.stop = AsyncMock()
        if ended:
            a.native = Mock(pid=a.session["pid"], returncode=-15)        # Agent J's own child, reaped by old.stop()
        else:
            a.session["pid"], a.native = os.getpid(), None               # a process that is still there
        with patch.object(phone_restart, "profile", return_value=[]), patch.object(phone_restart, "end_selected", new=AsyncMock()), \
                patch.object(phone_restart, "prepare", new=prepare), patch.object(phone_restart.agent, "make", side_effect=make), \
                patch.object(phone_restart.shared_pin, "write"), patch("agentj.reset.write_undo"):
            res = await phone_restart.run(h)
        return res, made

    async def test_phone_restart_ends_waiting_controls_and_never_carries_them(self):
        """Q3: a /compact · /clear still waiting when the Agent is restarted — never an orphan page, never carried over."""
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            pages = [h.hist.add({"k": "cmd", "text": "/" + n}, "等主会话空闲…", "open", {"cmd": n, "cancel_wait": True})["id"]
                     for n in ("compact", "clear", "compact", "compact")]
            current, locked, queued, typed = (Cmd(n, turn=t) for n, t in zip(("compact", "clear", "compact", "compact"), pages))
            typed.native_started = True
            pill = Cmd("model_set", data={"r": "x"})
            words = SimpleNamespace(sid="s1", turn=77, state="pending")
            a.cur_cmd, a.lock_cmd = current, locked
            for item in (queued, pill, words, typed):
                a.q.put_nowait(item)
            res, made = await self.run_restart(h, a, AsyncMock())
            self.assertTrue(res["ok"])
            for cmd, tid in ((current, pages[0]), (locked, pages[1]), (queued, pages[2])):
                page = h.hist.get(tid)
                self.assertTrue(cmd.cancelled)
                self.assertEqual((page["end"], page["card"]), ("stopped", {}), "no 「取消」 left that finds nothing")
                self.assertEqual(page["reply"]["text"], f"主会话已重启，这条 /{cmd.name} 没有执行；需要的话请再发一次。")
            self.assertEqual([kw for ev, kw in h.logs if ev == "shared_control_dropped"],
                             [{"cmd": "clear", "why": "restart"}, {"cmd": "compact", "why": "restart"}, {"cmd": "compact", "why": "restart"}])
            self.assertTrue(typed.cancelled)
            self.assertEqual(h.hist.get(pages[3])["end"], "stopped")
            self.assertIn("无法确认", h.hist.get(pages[3])["reply"]["text"])
            carried = []
            while not made[-1].q.empty():
                carried.append(made[-1].q.get_nowait())
            self.assertIn(words, carried)
            self.assertIn(pill, carried)
            self.assertEqual([x for x in carried if isinstance(x, Cmd) and not x.cancelled and x.name != "model_set"], [],
                             "no waiting control reaches the reopened adapter alive")
            self.assertEqual(native_advisory.drop_controls(h, SimpleNamespace(q=asyncio.Queue())), 0)

    async def test_phone_restart_resumed_queues_the_held_messages_first(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            one, two = await self.hold(h, a, "重启前没送到的一", "重启前没送到的二")
            res, made = await self.run_restart(h, a, AsyncMock())
            self.assertEqual(res, {"ok": True, "new": False})
            new = made[-1]
            self.assertTrue(new.started)
            self.assertEqual([new.q.get_nowait().turn for _ in range(2)], [one, two])
            self.assertEqual(h.hist.get(one)["card"], {"queued": True})
            self.assertEqual([ev for ev, _ in h.logs if ev in ("shared_held_undelivered", "shared_held_unconfirmed")], [])

    async def test_phone_restart_into_a_new_conversation_marks_them_undelivered(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            (one,) = await self.hold(h, a, "原会话没恢复")
            res, made = await self.run_restart(h, a, AsyncMock(side_effect=[ValueError("resume failed"), None]))
            self.assertEqual(res, {"ok": True, "new": True})
            self.assertTrue(made[-1].q.empty(), "never into another conversation")
            page = h.hist.get(one)
            self.assertEqual((page["end"], page["card"]), ("failed", {"undelivered": True}))
            self.assertEqual([kw["turn"] for ev, kw in h.logs if ev == "shared_held_undelivered"], [one])

    async def test_phone_restart_failed_never_leaves_them_pending(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            (one,) = await self.hold(h, a, "重启失败")
            res, _ = await self.run_restart(h, a, AsyncMock(side_effect=ValueError("no")))
            self.assertFalse(res["ok"])
            self.assertEqual(h.hist.get(one)["end"], "failed")
            self.assertNotIn("held", h.hist.get(one)["card"])

    async def test_phone_restart_that_did_not_end_the_process_is_unconfirmed(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            (one,) = await self.hold(h, a, "旧进程没有被结束")
            res, made = await self.run_restart(h, a, AsyncMock(), ended=False)
            self.assertTrue(res["ok"])
            self.assertTrue(made[-1].q.empty(), "no second delivery")
            self.unconfirmed(h, one)

    async def test_serve_start_takes_them_off_the_queue_as_unconfirmed(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            words = "serve 重启之前发出、还没被主会话接走的那一条"
            (one,) = await self.hold(h, a, words)
            from agentj import diskq
            await diskq.aflush()
            again = Host0378(root)                           # a new serve: the page is on disk, the native may still be alive
            self.assertEqual(again.hist.get(one)["card"]["held"], "pending")
            self.assertEqual(native_advisory.clear_held(again), 1)
            self.unconfirmed(again, one)
            self.assertIn(("shared_held_unconfirmed", {"turn": one}), again.logs)
            self.assertEqual([ev for ev, _ in again.logs if ev == "shared_held_undelivered"], [])
            self.assertEqual(native_advisory.clear_held(again), 0)
            b = self.agent(again)                            # …and it takes the message after all
            b._held_seen(words + " — typed at the desktop, no phone label")
            self.unconfirmed(again, one)
            b._held_seen(from_phone(words))
            self.unconfirmed(again, one)

    async def test_unconfirmed_pages_are_never_reclassified_from_body_or_labels(self):
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            a = self.agent(h)
            words = "同一句话先后发了两次，都没被接走"
            (old,) = await self.hold(h, a, words)
            native_advisory.clear_held(h, a)
            a._held_seen(from_phone(words))
            self.unconfirmed(h, old)
            (new,) = await self.hold(h, a, words)
            a._held_seen(from_phone(a.held[0][1]))
            self.assertEqual(h.hist.get(new)["card"], {}, "the live held page")
            self.unconfirmed(h, old)

    async def test_switch_and_service_restart_close_persisted_controls(self):
        from agentj import diskq
        with SettledTmp(dir=TEST_TMP) as root:
            h = Host0378(root)
            waiting = h.hist.add({"k": "cmd", "text": "/compact"}, "", "open",
                                 {"cmd": "compact", "cancel_wait": True})["id"]
            active = h.hist.add({"k": "cmd", "text": "/clear"}, "", "open",
                                {"cmd": "clear", "control_pending": True})["id"]
            done = h.hist.add({"k": "cmd", "text": "/clear"}, "done", "done", {"cmd": "clear"})["id"]
            await diskq.aflush()
            again = Host0378(root)
            native_advisory.close_previous_controls(again)
            self.assertIn("无法确认", again.hist.get(waiting)["reply"]["text"])
            self.assertIn("无法确认", again.hist.get(active)["reply"]["text"])
            self.assertEqual([again.hist.get(t)["end"] for t in (waiting, active, done)], ["stopped", "stopped", "done"])

    async def test_rebind_to_another_process_never_redelivers(self):
        for same in (True, False):
            with self.subTest(same=same), SettledTmp(dir=TEST_TMP) as root:
                h = Host0378(root)
                a = self.agent(h)
                (one,) = await self.hold(h, a, "换进程")
                sid = a.session["sessionId"] if same else "99999999-9999-9999-9999-999999999999"
                with patch.object(a, "prepare_channel", new=AsyncMock()), patch.object(a, "refresh_models", new=AsyncMock()), \
                        patch.object(shared, "claude_transcript", return_value=Path(root) / "t.jsonl"), \
                        patch.object(a, "issue_hook_token"), patch("agentj.shared_report_history.attach"):
                    (Path(root) / "t.jsonl").write_text("")
                    h.st.set_agent_session = lambda *x: None
                    ok = await a._bind_session({"sessionId": sid, "pid": 4242, "startedAt": 0})
                self.assertTrue(ok)
                self.unconfirmed(h, one)
                self.assertTrue(a.q.empty(), "the old process may still run it: no second copy")
                self.assertEqual(a.held, [])


# ------------------------------------------------------------------ real Host: /stop, queue_drop, `agentj agent restart`
class QueueAgent:
    """What on_say / stop_turn / queue_drop touch on the adapter: the queue, never a harness."""
    kind = "claude"
    halting = False
    halt_quiet = False
    halt_note = None
    cur_send = cur_cmd = lock_cmd = None
    status = "idle"

    def __init__(self, host):
        self.host, self.q, self.held, self.held_sends, self.cfg = host, asyncio.Queue(), [], {}, {}

    def submit(self, item):
        if getattr(item, "sid", None) is not None:
            self.host.queue_pending(item)
        self.q.put_nowait(item)

    def passthrough(self, name):
        return False

    async def halt(self, clear_queue=True, expected_target=None):
        return False

    def owns_harness(self):
        return False


class Served(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = SettledTmp(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.addAsyncCleanup(self.settle_host)               # registered later = runs first: writers end, then the folder goes
        self.st = _state(self.tmp.name)
        self.ph = Phone(self.st)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.host.agent = QueueAgent(self.host)
        self.s = serve.Session(cid=11, provenance={"kind": "phone", "role": "owner"}, state="ready", device=self.ph.did,
                               name="手机", pub=self.ph.pub, p33=True)
        self.host.sessions[11] = self.s

    async def settle_host(self):
        """This Host is never stopped, so its own off-loop work may still be on its way to the fixture folder when the test
        body returns: a `say` starts the phone_touch task (auto-update.json through asyncio.to_thread) and queues a receipt
        line on the "agentj-receipts" thread. On a loaded machine either one landed while the folder was being removed
        (ENOTEMPTY). Wait for the Host's background tasks and the receipt writer; SettledTmp then waits for diskq."""
        h = self.host
        for _ in range(8):
            tasks = [t for t in h.__dict__.get("_bg_tasks", ()) if not t.done()]
            if not tasks:
                break
            _, late = await asyncio.wait(tasks, timeout=30)
            self.assertFalse(late, f"background tasks still running: {sorted(t.get_name() for t in late)}")
        else:
            self.fail("the Host kept starting background tasks")
        await asyncio.to_thread(h.receipts.flush)

    async def say(self, text):
        x = new_sid()
        with patch.object(self.host, "_setup_round_trip", new=AsyncMock()), patch.object(self.host, "_model_set_flush", new=AsyncMock()):
            await self.host._app(self.s, {"t": "say", "sid": x, "text": text, "ts": 0})
            for _ in range(20):
                await asyncio.sleep(0)
        res = [o for _, o in self.sent if o.get("t") == "say_res" and o.get("sid") == x][-1]
        self.assertTrue(res["ok"], res)
        return res["turn"], self.host.sends.get(self.s.device, x)

    async def drop(self, **kw):
        await self.host._app(self.s, {"t": "queue_drop", **kw})
        for _ in range(20):
            await asyncio.sleep(0)
        return [o for _, o in self.sent if o.get("t") == "queue_dropped"][-1]

    def held_page(self, text="一条没被主会话接收的排队消息"):
        tid = self.host.hist_add({"k": "phone", "dev": self.s.device, "text": text, "source_kind": "phone", "source_role": "owner"},
                                 "", "done", card={"held": "pending", "queued": True, "stuck": True})["id"]
        self.host.agent.held.append((tid, text))
        return tid


class QueueDrop(Served):
    async def test_single_then_all_running_turn_untouched_and_persisted(self):
        h = self.host
        (t1, s1), (t2, s2), (t3, s3) = [await self.say(x) for x in ("正在跑的", "排队二", "排队三")]
        h.cur_turn, s1.state = t1, "delivered"              # the first one runs natively
        held = self.held_page()
        res = await self.drop(turns=[t2])
        self.assertEqual(res, {"t": "queue_dropped", "turns": [t2], "skipped": []})
        page = h.hist.get(t2)
        self.assertEqual((page["end"], page["card"]), ("stopped", {"dropped": True}))
        self.assertEqual(s2.state, "cancelled", "never delivered: the run loop skips a cancelled Send")
        self.assertEqual((h.hist.get(t3)["end"], h.hist.get(t3)["card"]["queued"], s3.state), ("open", True, "pending"))
        self.assertIn({"t": "say_state", "sid": s2.sid, "s": "cancelled", "why": "dropped"}, [o for _, o in self.sent],
                      "its own device is told it was removed from the queue — not a withdraw, not the stop switch")
        res = await self.drop(turns=[t1, t2, 424242], r="A" * 22)
        self.assertEqual((res["turns"], res["skipped"]), ([], [t1, t2, 424242]), "running · already dropped · unknown")
        res = await self.drop(all=True)
        self.assertEqual((sorted(res["turns"]), res["skipped"]), (sorted([t3, held]), []))
        self.assertEqual((h.hist.get(t1)["end"], s1.state), ("open", "delivered"), "`all` skips the running turn")
        self.assertEqual((h.hist.get(held)["end"], h.hist.get(held)["card"]), ("stopped", {"unconfirmed": True}),
                         "left the queue, but its words were already with the native: never `dropped` / `undelivered`")
        self.assertIn("可能仍会被执行", h.hist.get(held)["reply"]["text"])
        self.assertEqual(h.agent.held, [])
        self.assertEqual(s3.state, "cancelled")
        from agentj import diskq
        await diskq.aflush()
        again = History(self.st, on=True)                  # a serve restart reads the same pages
        for tid in (t2, t3):
            self.assertEqual((again.get(tid)["end"], again.get(tid)["card"]), ("stopped", {"dropped": True}), tid)
        self.assertEqual((again.get(held)["end"], again.get(held)["card"]), ("stopped", {"unconfirmed": True}))
        self.assertEqual(again.get(t1)["end"], "open")

    async def test_capability_announced_to_p33_only(self):
        self.assertEqual(queue_drop.CAP, "queuedrop1")
        self.assertIn("queuedrop1", self.host._caps(self.s)["caps"])
        self.assertIn("queuedrop1", self.host._caps()["caps"])
        self.s.p33 = False
        self.assertEqual(self.host._caps(self.s), {})

    async def test_stop_switch_keeps_its_own_reason(self):
        _, s1 = await self.say("急停时还在排队")
        self.host.queue_dropped(s1)                         # Agent.halt(clear_queue=True): the stop switch
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertIn({"t": "say_state", "sid": s1.sid, "s": "cancelled", "why": "stopped"}, [o for _, o in self.sent])

    async def test_logged_with_the_device_and_r_echoed(self):
        t1, _ = await self.say("一")
        logs = []
        self.host.st.log = lambda ev, **kw: logs.append((ev, kw))
        res = await self.drop(turns=[t1], r="B" * 22)
        self.assertEqual(res.get("r"), "B" * 22)
        self.assertIn(("queue_drop", {"count": 1, "by": self.ph.did}), logs)

    async def test_waiting_command_follows_cmd_cancels_own_rule(self):
        """security F5: exactly cancel_waiting_cmd — the asking device's own waiting /clear · /compact, never typed/started."""
        h = self.host
        page = h.cmd_turn(self.s, "compact")
        cmd = Cmd("compact", by="手机", turn=page)
        cmd.workflow_wait = True
        h.agent.submit(cmd)
        h.cmd_waiting(cmd)
        self.assertEqual((await self.drop(all=True))["turns"], [], "`all` is the message queue")
        self.assertFalse(cmd.cancelled)
        cmd.native_started = True
        self.assertEqual((await self.drop(turns=[page]))["skipped"], [page], "typed: not droppable")
        cmd.native_started, cmd.workflow_wait = False, False
        self.assertEqual((await self.drop(turns=[page]))["skipped"], [page], "another harness's started / current command")
        cmd.workflow_wait = True
        with patch.object(h, "cancel_waiting_cmd", wraps=h.cancel_waiting_cmd) as rule:
            self.assertEqual((await self.drop(turns=[page]))["turns"], [page])
            rule.assert_called_once_with(self.s, page)
        self.assertTrue(cmd.cancelled)
        self.assertEqual((h.hist.get(page)["end"], h.hist.get(page)["card"]), ("stopped", {"dropped": True}))
        other = h.cmd_turn(None, "clear")                    # not this device's command
        theirs = Cmd("clear", turn=other)
        theirs.workflow_wait = True
        h.agent.submit(theirs)
        h.cmd_waiting(theirs)
        self.assertEqual((await self.drop(turns=[other]))["skipped"], [other])
        self.assertFalse(theirs.cancelled)

    def host_send(self, marker, value, src=None):
        """A host-made Send as the real code builds it: compose.Send + provenance.system() on an `agent` page with a `via`."""
        h = self.host
        page = h.hist_add(src or {"k": "agent", "text": "host prompt", "via": marker}, "", "open")["id"]
        send = compose.Send("host", new_sid(), page, text="host prompt")
        send.source = provenance.system()
        setattr(send, marker, value)
        h.sends.add(send)
        h.agent.submit(send)                                 # Agent.submit → queue_pending: card.queued + source_kind / role
        return page, send

    async def test_host_made_sends_are_never_dropped(self):
        """security F3: provenance.system() is role "owner" — `all` used to cancel heartbeats, resumes, completions, hooks."""
        h = self.host
        t1, s1 = await self.say("主人的")
        made = [self.host_send(k, v) for k, v in (("heartbeat_key", "beat"), ("quota_resume_id", "q1"), ("compact_resume", True),
                                                  ("auto_notify", True), ("background_done_ids", ["b1"]), ("hook_notice", True))]
        for page, send in made:
            src = h.hist.get(page)["src"]
            self.assertEqual((src["source_role"], src["source_kind"], h.hist.get(page)["card"]["queued"]), ("owner", "system", True))
        res = await self.drop(all=True)
        self.assertEqual(res["turns"], [t1], "only the owner's own message")
        res = await self.drop(turns=[p for p, _ in made])
        self.assertEqual((res["turns"], res["skipped"]), ([], [p for p, _ in made]))
        for page, send in made:
            self.assertEqual((send.state, h.hist.get(page)["end"], h.hist.get(page)["card"]["queued"]), ("pending", "open", True))
        # even on a page that looks like the owner's phone message, a Send carrying a host marker is refused
        page, send = self.host_send("quota_resume_id", "q2", {"k": "phone", "dev": self.s.device, "text": "继续"})
        send.source = provenance.phone()
        h.queue_pending(send)
        self.assertEqual(h.hist.get(page)["src"]["source_kind"], "phone")
        res = await self.drop(turns=[page])
        self.assertEqual((res["turns"], res["skipped"], send.state), ([], [page], "pending"))

    async def test_owner_rule_mirrors_the_web_and_only_a_paired_phone_may_ask(self):
        h = self.host
        ok = {"k": "phone", "source_kind": "phone", "source_role": "owner"}
        for src, want in ((ok, True), ({"k": "host", "source_kind": "terminal", "source_role": "owner"}, True),
                          ({"k": "wechat", "source_kind": "wechat", "source_role": "owner"}, True),
                          ({"k": "telegram", "source_kind": "telegram", "source_role": "owner"}, True),
                          ({"k": "email", "source_kind": "email", "source_role": "owner"}, True),
                          ({"k": "phone"}, True), ({"k": "host"}, True),
                          ({**ok, "via": "hook"}, False), ({**ok, "via": "heartbeat"}, False),
                          ({"k": "agent", "source_kind": "system", "source_role": "owner"}, False),
                          ({"k": "agent", "source_kind": "phone", "source_role": "owner"}, False),
                          ({"k": "phone", "source_kind": "system", "source_role": "owner"}, False),
                          ({"k": "telegram", "source_kind": "telegram", "source_role": "guest"}, False),
                          ({"k": "telegram"}, False), ({"k": "sys"}, False), ({"k": "task"}, False)):
            self.assertEqual(queue_drop._owner({"src": src}), want, src)
        web = (AJ / "web" / "public" / "js" / "phone-activity.js")
        if web.is_file():                                    # the same five kinds the page's ownerInput accepts
            self.assertIn("new Set(['phone','terminal','wechat','telegram','email'])", web.read_text(encoding="utf-8"))
        t1, s1 = await self.say("主人的")
        n = len(self.sent)
        for bad in ({"turns": "all"}, {"turns": []}, {"turns": [True]}, {"turns": list(range(65))}, {"all": 1}, {}):
            await self.host._app(self.s, {"t": "queue_drop", **bad})
        self.s.p33 = False
        await self.host._app(self.s, {"t": "queue_drop", "all": True})
        self.s.p33 = True
        self.s.provenance = {"kind": "telegram", "role": "owner"}      # not a paired phone session
        await self.host._app(self.s, {"t": "queue_drop", "all": True})
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertEqual(len(self.sent), n, "no reply, nothing dropped")
        self.assertEqual((h.hist.get(t1)["end"], s1.state), ("open", "pending"))

    async def test_revoked_phone_cannot_drop_after_waiting_for_send_lock(self):
        turn, send = await self.say("等锁期间撤销配对")
        async with send.lock:
            task = asyncio.create_task(queue_drop.on_phone(self.host, self.s, {"turns": [turn]}))
            await asyncio.sleep(.05)
            self.assertFalse(task.done())
            self.host.sessions.pop(self.s.cid)
        await task
        self.assertEqual(send.state, "pending")
        self.assertEqual(self.host.hist.get(turn)["end"], "open")

    async def test_a_send_being_written_is_not_dropped(self):
        t1, s1 = await self.say("正在写入")
        async with s1.lock:                                 # Agent.deliver holds the lock through the write
            task = asyncio.create_task(self.drop(turns=[t1]))
            await asyncio.sleep(.05)
            self.assertFalse(task.done())
            s1.state = "delivered"
        res = await task
        self.assertEqual((res["turns"], res["skipped"]), ([], [t1]))
        self.assertEqual(self.host.hist.get(t1)["end"], "open")


class StopClearsHeld(Served):
    async def test_stop_takes_held_pages_off_the_queue_as_unconfirmed(self):
        h = self.host
        one, two = self.held_page("第一条没被接走的排队消息"), self.held_page("第二条没被接走的排队消息")
        with patch.object(h.scheduler, "stop_running", new=AsyncMock(return_value=False)):
            res = await h.stop_turn(self.ph.did)
        self.assertIn("2", res.text)
        self.assertIn("可能仍会被执行", res.text)
        self.assertNotIn("没有送达", res.text, "never claimed: the native had them")
        for tid in (one, two):
            page = h.hist.get(tid)
            self.assertEqual((page["end"], page["card"]), ("stopped", {"unconfirmed": True}))
        self.assertEqual(h.agent.held, [])
        with patch.object(h.scheduler, "stop_running", new=AsyncMock(return_value=False)):
            again = await h.stop_turn(self.ph.did)
        self.assertEqual(again.kind, "info", "nothing left: the ordinary 「没有在跑的」 answer")


class CliRestart(Served):
    def shared_agent(self, owned):
        a = shared.SharedClaudeAgent(self.host, {"kind": "claude", "dir": self.tmp.name, "session_mode": "shared", "_workflow_ceo": True})
        a.session = {"sessionId": SID, "pid": 4321}
        a.native = Mock(pid=4321, returncode=None) if owned else None
        a.master = 7 if owned else None
        self.host.agent = a
        return a

    async def settle(self):
        for _ in range(10):
            await asyncio.sleep(.01)

    async def test_owned_shared_native_is_restarted_through_phone_restart(self):
        a = self.shared_agent(True)
        with patch.object(phone_restart, "run", new=AsyncMock(return_value={"ok": True, "new": False})) as run:
            res = self.host.restart_harness("cli", human=True)
            again = self.host.restart_harness("cli", human=True)
            await self.settle()
        self.assertEqual(res, {"ok": True, "when": "now", "shared": True})
        self.assertTrue(again["ok"])
        run.assert_awaited_once_with(self.host)
        self.assertFalse(a.owns_harness(), "still a shared session: only this path restarts it")

    async def test_a_running_turn_is_not_interrupted(self):
        a = self.shared_agent(True)
        a.native_turn = True
        with patch.object(phone_restart, "run", new=AsyncMock(return_value={"ok": True})) as run:
            res = self.host.restart_harness("cli", human=True)
            await self.settle()
            run.assert_not_awaited()
            a.native_turn = False
            await asyncio.sleep(.7)
            run.assert_awaited_once()
        self.assertEqual(res["when"], "next_turn")

    async def test_a_hook_state_stuck_at_working_does_not_hold_the_restart_for_good(self):
        a = self.shared_agent(True)
        a.native_turn = True                                # never clears: the very state the owner restarts for
        logs = []
        self.host.st.log = lambda ev, **kw: logs.append((ev, kw))
        with patch.object(native_advisory, "CLI_RESTART_WAIT", .3), \
                patch.object(phone_restart, "run", new=AsyncMock(return_value={"ok": True})) as run:
            res = self.host.restart_harness("cli", human=True)
            await asyncio.sleep(.1)
            run.assert_not_awaited()
            await asyncio.sleep(1.0)
            run.assert_awaited_once_with(self.host)
        self.assertEqual(res["when"], "next_turn")
        self.assertIn(("agent_restart", {"by": "cli", "caller": "human", "result": "wait_timeout", "seconds": 0}), logs)
        self.assertIn(("agent_restart", {"by": "cli", "caller": "human", "result": "ok"}), logs)

    async def test_only_the_human_at_a_terminal_may_restart_the_shared_native(self):
        """security F6: an Agent's process tree / a caller without a tty keeps the refusal a shared session always had —
        also while the hook state waits on an approval — and no other restart reason reaches this path."""
        a = self.shared_agent(True)
        logs = []
        self.host.st.log = lambda ev, **kw: logs.append((ev, kw))
        with patch.object(phone_restart, "run", new=AsyncMock()) as run:
            for why, human in (("reload", False), ("proxy", True), ("provider", True)):
                self.assertEqual(self.host.restart_harness(why, human=human), {"ok": False, "when": "own", "error": "shared"}, why)
            not_human = {"ok": False, "when": "own", "error": "not_human"}     # Q4: its own answer, not the foreign-session one
            self.assertEqual(self.host.restart_harness("cli", human=False), not_human)
            self.assertEqual(self.host.restart_harness("cli"), not_human, "default: not human")
            a.state.handle({"hook_event_name": "PermissionRequest", "tool_name": "Bash"})
            self.assertEqual(a.state.status, "waiting")
            self.assertFalse(self.host.restart_harness("cli", human=False)["ok"], "an approval is waiting: never for an Agent")
            await self.settle()
            run.assert_not_awaited()
            self.assertTrue(self.host.restart_harness("cli", human=True)["ok"], "the human at the computer may")
            await self.settle()
            run.assert_awaited_once()
        self.assertIn(("agent_restart", {"by": "cli", "caller": "agent", "result": "refused", "why": "not_human"}), logs)
        self.host.estop = {"on": True}
        self.assertEqual(self.host.restart_harness("cli", human=True), {"ok": False, "when": "own", "error": "estop"})
        self.assertIn(("agent_restart", {"by": "cli", "caller": "human", "result": "refused", "why": "estop"}), logs)

    async def test_ctl_classifies_the_caller_with_the_existing_check(self):
        import inspect
        src = inspect.getsource(serve.Host.on_ctl)
        at = src.index('cmd == "agent_restart"')
        branch = src[at:at + 400]
        self.assertIn("ctl_auth.refusal(pid, os.getpid(), self._agent_pids()) is None and ctl_auth.has_tty(pid)", branch)
        self.assertIn('self.restart_harness("cli", human=human)', branch)

    async def test_the_owners_own_terminal_is_refused(self):
        self.shared_agent(False)
        with patch.object(phone_restart, "run", new=AsyncMock()) as run:
            res = self.host.restart_harness("cli", human=True)
            await self.settle()
        self.assertEqual(res, {"ok": False, "when": "own", "error": "shared"})
        run.assert_not_awaited()
        self.shared_agent(False)
        self.assertEqual(self.host.restart_harness("cli", human=False)["error"], "shared", "foreign first, whoever asks")

    def cli_says(self, answer):
        out = io.StringIO()
        with patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), patch.object(cli.names, "ctl_call", return_value=answer), \
                contextlib.redirect_stdout(out):
            try:
                cli.cmd_agent(SimpleNamespace(mode="restart", json=False))
            except SystemExit as e:
                return "exit", str(e.code)
        return "ok", out.getvalue()

    def test_cli_words(self):
        how, text = self.cli_says({"ok": True, "when": "now", "shared": True})
        self.assertEqual(how, "ok")
        self.assertIn("Agent J 自己启动的主会话进程", text)
        how, text = self.cli_says({"ok": False, "when": "own", "error": "shared"})
        self.assertEqual(how, "exit")
        self.assertIn("不是 Agent J 启动的", text)
        how, text = self.cli_says({"ok": False, "when": "own", "error": "not_human"})
        self.assertEqual(how, "exit")
        self.assertIn("请在 Agent J 主机的终端里直接运行这条命令（不能由 Agent 或脚本代跑）；也可以在手机 设置 → 紧急修复 →「重启 Agent」。", text)
        self.assertIn("Settings → Emergency repair → Restart Agent", text)
        self.assertNotIn("不是 Agent J 启动的", text)
        how, text = self.cli_says({"ok": True, "when": "next_turn", "shared": True})
        self.assertEqual(how, "ok")
        self.assertIn("不打断正在进行的这一轮", text)


class Docs(unittest.TestCase):
    def test_wire_contract_is_in_the_protocol(self):
        text = (AJ / "protocol" / "PROTOCOL.md").read_text(encoding="utf-8")
        for needle in ('{"t":"queue_drop","turns":[', '{"t":"queue_drop","all":true', '{"t":"queue_dropped","turns":[',
                       "card.dropped:true", "card.unconfirmed:true", "shared_held_undelivered", '`ready.caps` `"queuedrop1"`', "card.undelivered:true",
                       '`why:"dropped"`'):
            self.assertIn(needle, text)
        self.assertIn("queue_drop", serve.P33_ONLY)


if __name__ == "__main__":
    unittest.main()
