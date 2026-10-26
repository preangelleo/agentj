"""P539 (#299): a model's safety flag never strands the owner on the phone. Synthetic screens and fakes only."""
import _hermetic  # noqa: F401
import asyncio
import os
import pty
import tty
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentj import refusal_flag as rf
from agentj import shared_menu as menu
from test_p451_menu import QUOTA, REWIND

BODY = ("Opus 5.5's safeguards flagged this session. This sometimes happens with safe, normal conversations. "
        "Edit and retry, or continue with Sonnet 5.")
PAUSED = ("╭──────────────────────────────────────────────╮\n"
          "│ Session paused                               │\n"
          "│ " + BODY[:60] + " │\n│ " + BODY[60:] + " │\n"
          "│                                              │\n"
          "│ ❯ 1. Switch to Sonnet 5                      │\n"
          "│   2. Edit prompt and retry with Opus 5.5     │\n"
          "╰──────────────────────────────────────────────╯\n"
          "  Esc to cancel")
PAUSED_PLAIN = "Session paused\n" + BODY + "\n  1. Switch to Claude Sonnet 4.6\n❯ 2. Edit prompt and retry with Opus 5.5\nEnter to confirm · Esc to cancel"
CREDITS = "Session paused\nYou can continue on usage credits.\n❯ 1. Continue on usage credits\n  2. Switch models\nEsc to cancel"
SECRET = "do-not-log-this-owner-text"

FIRST = {"questions": [{"question": "Opus 5.5's safeguards flagged this message. This sometimes happens with safe, normal "
                                    "conversations. Switch to Sonnet 5 and keep going whenever this happens? You can change "
                                    "this later in /config.",
                        "header": "Model switch", "multiSelect": False,
                        "options": [{"label": "Switch automatically",
                                     "description": "Continue on Sonnet 5 now, and switch without asking from now on"},
                                    {"label": "Stay on Opus 5.5",
                                     "description": "Stop here without switching, and ask me each time a message is flagged"}]}],
         "metadata": {"source": "refusal_fallback_preference"}}


class Detect(unittest.TestCase):
    def test_paused_positive_with_and_without_cursor_glyph_and_box(self):
        d = menu.detect(PAUSED)
        self.assertEqual((d["kind"], d["cursor"], d["model"], d["fallback"]), ("paused", 1, "Opus 5.5", "Sonnet 5"))
        d = menu.detect(PAUSED_PLAIN)
        self.assertEqual((d["cursor"], d["fallback"]), (2, "Claude Sonnet 4.6"))
        generic = PAUSED_PLAIN.replace("Switch to Claude Sonnet 4.6", "Switch to the fallback model").replace(" with Opus 5.5", "")
        d = menu.detect(generic)
        self.assertEqual((d["kind"], d["model"], d["fallback"]), ("paused", "", ""))

    def test_paused_negatives_and_other_menus_unchanged(self):
        self.assertIsNone(menu.detect("Earlier:\n" + PAUSED_PLAIN + "\n❯ hello"))          # history above an input prompt
        self.assertIsNone(menu.detect(CREDITS))                                               # same title, other modal
        self.assertIsNone(menu.detect(PAUSED_PLAIN.replace("Esc to cancel", "")))
        self.assertIsNone(menu.detect(PAUSED_PLAIN.replace("2. Edit prompt", "3. Edit prompt")))
        self.assertIsNone(menu.detect(PAUSED_PLAIN.replace("Sonnet 4.6", "Sonnet; rm -rf")))  # not a model name
        self.assertIsNone(menu.detect(PAUSED_PLAIN.replace("❯ 2.", "  2.")))                  # no cursor: not an active select
        self.assertEqual(menu.detect(QUOTA)["kind"], "quota")
        self.assertEqual(menu.detect(REWIND)["kind"], "rewind")

    def test_questions_both_languages_four_choices_no_esc_row(self):
        info = menu.detect(PAUSED)
        zh, en = menu.questions(info, "zh")[0], menu.questions(info, "en")[0]
        self.assertIn("Opus 5.5 的安全检查", zh["q"]); self.assertIn("没有丢", zh["q"])
        self.assertEqual([o["l"] for o in zh["o"]], ["换到 Sonnet 5 继续这一轮", "用 Opus 5.5 原话重试这一轮", "改一改再发", "开新对话"])
        self.assertTrue(all(o["d"] for o in zh["o"] + en["o"]))
        self.assertIn("safety check", en["q"])
        self.assertEqual(menu.actions(info), ["1", "retry", "edit", "new"])
        self.assertEqual(menu.actions(menu.detect(QUOTA))[-1], "esc")
        for q in (zh, en):
            self.assertNotRegex(q["q"] + "".join(o["l"] + o["d"] for o in q["o"]), "试用|trial")


def actor(lang="zh"):
    log = Mock()
    sent = []
    host = SimpleNamespace(lang=lang, st=SimpleNamespace(log=log, is_allowed=lambda pub: True), question=AsyncMock(),
                           agent_notice=Mock(), turn_queued_note=Mock(), sessions={},
                           hist=SimpleNamespace(turns={7: {"src": {"k": "phone", "text": SECRET}}}))
    host._post = lambda fn, *args: sent.append(args)
    host.send_app = object()
    host.stopped = lambda: False
    send = SimpleNamespace(device="dev-1", turn=7, source={"kind": "phone", "role": "owner"})
    a = SimpleNamespace(host=host, session={"sessionId": "fixture", "pid": 17}, native=None, master=None,
                        native_lock=asyncio.Lock(), state=SimpleNamespace(status="working"), phone_turn=False,
                        phone_claim=False, phone_seen=False, done=asyncio.Event(), status="working",
                        last_phone_prompt=send, cur_send=None, submit=Mock())
    return a, sent


class Keys(unittest.IsolatedAsyncioTestCase):
    async def run_pick(self, n, screens, send_result=True):
        a, sent = actor()
        a.host.sessions = {1: SimpleNamespace(state="ready", p33=True, pub=b"k", device="dev-1"),
                           2: SimpleNamespace(state="ready", p33=True, pub=b"k", device="dev-2")}
        d = menu.Desk(a)
        a.host.question.return_value = ("answer", [[n]])
        screens = list(screens) or [(None, "pane")]

        async def screen():         # the listed screens in order, then the last one stays
            return screens.pop(0) if len(screens) > 1 else screens[0]
        clock = iter(range(0, 10 ** 6))
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(menu.detect(PAUSED), "pane"))), \
                patch.object(d, "screen_text", new=screen), \
                patch.object(menu, "time", new=SimpleNamespace(monotonic=lambda: float(next(clock)))), \
                patch.object(d, "send", new=AsyncMock(return_value=send_result)) as send, \
                patch.object(menu.asyncio, "sleep", new=AsyncMock()):
            await d.poll(); await d.task
        return a, sent, send

    async def test_switch_selects_row_one(self):
        a, sent, send = await self.run_pick(1, [])
        self.assertEqual([c.args[1] for c in send.await_args_list], ["1"])
        self.assertIn("已换到 Sonnet 5", a.host.agent_notice.call_args.args[0])
        self.assertEqual(sent, [])

    async def test_retry_selects_row_two_then_enter_only_after_modal_is_gone(self):
        restored = "Opus 5.5's safeguards flagged…\n❯ please fix the parser"
        screens = [(PAUSED, "pane"), (PAUSED, "pane"), (restored, "pane")] + [(restored, "pane")] * 10
        a, sent, send = await self.run_pick(2, screens)
        self.assertEqual([c.args[1] for c in send.await_args_list], ["2", "enter"])
        self.assertIn("原话重试", a.host.agent_notice.call_args.args[0])

    async def test_retry_without_restored_prompt_never_presses_enter_and_says_how_to_go_on(self):
        screens = [(PAUSED, "pane")] * 60
        a, sent, send = await self.run_pick(2, screens)
        self.assertEqual([c.args[1] for c in send.await_args_list], ["2"])
        self.assertIn("紧急修复", a.host.agent_notice.call_args.args[0])

    async def test_edit_escapes_and_returns_the_owner_words_to_that_phone_only_never_logged(self):
        a, sent, send = await self.run_pick(3, [])
        self.assertEqual([c.args[1] for c in send.await_args_list], ["esc"])
        self.assertEqual(len(sent), 1)
        s, obj = sent[0]
        self.assertEqual((s.device, obj), ("dev-1", {"t": "draft_restore", "text": SECRET}))
        self.assertIn("原话已放回", a.host.agent_notice.call_args.args[0])
        self.assertNotIn(SECRET, repr(a.host.st.log.call_args_list))
        self.assertNotIn(SECRET, repr(a.host.agent_notice.call_args_list))

    async def test_new_conversation_escapes_then_queues_clear(self):
        a, sent, send = await self.run_pick(4, [(None, "pane")] * 20)
        self.assertEqual([c.args[1] for c in send.await_args_list], ["esc"])
        cmd = a.submit.call_args.args[0]
        self.assertEqual((cmd.name, cmd.arg), ("clear", ""))
        self.assertIn("开新对话", a.host.agent_notice.call_args.args[0])

    async def test_failed_key_tells_the_owner_about_emergency_repair(self):
        a, sent, send = await self.run_pick(1, [], send_result=False)
        self.assertIn("紧急修复", a.host.agent_notice.call_args.args[0])

    async def test_desktop_prompt_has_no_words_to_restore(self):
        a, sent = actor()
        a.last_phone_prompt = None
        self.assertEqual(rf.phone_draft(a, a.host), (None, ""))
        a.host.sessions = {1: SimpleNamespace(state="ready", p33=True, pub=b"k", device="dev-1")}
        self.assertEqual(rf.draft_restore(a.host, None, ""), 1)
        self.assertEqual(sent[0][1], {"t": "draft_restore", "text": ""})
        self.assertIn("重新输入", rf.restore_note("zh", False))

    async def test_group_turn_words_are_not_restored(self):
        a, _ = actor()
        a.last_phone_prompt.source = {"kind": "group", "role": "guest"}
        self.assertEqual(rf.phone_draft(a, a.host), (None, ""))

    async def test_owned_pty_paused_row_and_enter_bytes(self):
        a, _ = actor()
        a.native = SimpleNamespace(pid=17, returncode=None)
        master, slave = pty.openpty(); tty.setraw(slave); a.master = master
        try:
            d = menu.Desk(a)
            d.surface.feed(("\x1b[2J\x1b[H" + PAUSED.replace("\n", "\r\n")).encode())
            self.assertTrue(await d.send(None, "2")); self.assertEqual(os.read(slave, 10), b"\x1b[B\r")
            self.assertFalse(await d.send(None, "3"))
            self.assertFalse(await d.send(None, "enter"))                # the modal is still there
            d.surface.feed(b"\x1b[2J\x1b[Hflagged above\r\n\xe2\x9d\xaf please fix it")
            self.assertTrue(await d.send(None, "enter")); self.assertEqual(os.read(slave, 10), b"\r")
            d.surface.feed(b"\x1b[2J\x1b[H\xe2\x9d\xaf ")
            self.assertFalse(await d.send(None, "enter"))                # an empty input box: nothing to submit
        finally:
            os.close(master); os.close(slave)


class FirstFlagCard(unittest.IsolatedAsyncioTestCase):
    def host(self, pick_n):
        from agentj.serve import Host
        a, sent = actor()
        a.phone_turn = True
        h = a.host
        h.agent = a
        h.task_label = None
        h.question = AsyncMock(return_value=("answer", [[pick_n]]))
        h.sessions = {1: SimpleNamespace(state="ready", p33=True, pub=b"k", device="dev-1")}
        return Host, h, a, sent

    async def test_card_is_rewritten_and_the_pick_maps_back_to_the_original_label(self):
        Host, h, a, sent = self.host(1)
        r = await Host._ask_user_question(h, FIRST, None)
        card = h.question.call_args.args[0][0]
        self.assertIn("以后再被拦也自动换", card["o"][0]["l"])
        self.assertIn("/config", card["o"][0]["d"])
        self.assertEqual(card["o"][1]["l"], "留在 Opus 5.5")
        self.assertIn("改一改再发", card["o"][1]["d"])
        q = FIRST["questions"][0]["question"]
        self.assertEqual(r["behavior"], "allow")
        self.assertEqual(r["updatedInput"]["answers"], {q: "Switch automatically"})
        self.assertEqual(r["updatedInput"]["questions"], FIRST["questions"])   # Claude Code's own input, unchanged
        self.assertEqual(sent, [])

    async def test_stay_maps_back_and_gives_the_words_back_without_waiting(self):
        Host, h, a, sent = self.host(2)
        r = await Host._ask_user_question(h, FIRST, None)
        self.assertEqual(r["updatedInput"]["answers"], {FIRST["questions"][0]["question"]: "Stay on Opus 5.5"})
        self.assertEqual(sent[0][1], {"t": "draft_restore", "text": SECRET})
        self.assertTrue(a.done.is_set())
        self.assertIn("已留在 Opus 5.5", h.agent_notice.call_args.args[0])

    async def test_english_card_and_header_alone_is_not_enough(self):
        qs, back, _ = rf.localize(FIRST, "en")
        self.assertIn("switch automatically from now on", qs[0]["options"][0]["label"])
        self.assertEqual(back({qs[0]["question"]: qs[0]["options"][1]["label"]}),
                         {FIRST["questions"][0]["question"]: "Stay on Opus 5.5"})
        # L1: header + "Stay on" + the question text are words a model can write; Claude Code's metadata marker is required
        self.assertIsNone(rf.first_flag({"questions": FIRST["questions"]}))
        self.assertIsNone(rf.localize({"questions": FIRST["questions"]}, "en"))
        self.assertIsNone(rf.first_flag({**FIRST, "metadata": {"source": "model"}}))
        self.assertIsNone(rf.first_flag({**FIRST, "metadata": "refusal_fallback_preference"}))

    async def test_other_questions_untouched(self):
        other = {"questions": [{"question": "Which color?", "header": "Color", "multiSelect": False,
                                "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]}
        self.assertIsNone(rf.localize(other, "zh"))
        stayish = {"questions": [{**other["questions"][0], "header": "Other",
                                  "options": [{"label": "Stay on Opus 5.5"}, {"label": "Go"}]}]}
        self.assertIsNone(rf.localize(stayish, "zh"))
        from agentj.serve import Host
        Host_, h, a, sent = self.host(2)
        r = await Host._ask_user_question(h, other, None)
        self.assertEqual(h.question.call_args.args[0][0]["q"], "Which color?")
        self.assertEqual(r["updatedInput"]["answers"], {"Which color?": "Blue"})
        self.assertEqual(sent, [])


class OwnSession(unittest.TestCase):
    def test_stream_json_refusal_events_tell_the_owner_how_to_go_on(self):
        from agentj.agent import ClaudeAgent
        a = object.__new__(ClaudeAgent)
        a.cfg = {"language": "zh"}
        a.host = SimpleNamespace(agent_notice=Mock(), st=SimpleNamespace(log=Mock()))
        ClaudeAgent._refusal_notice(a, {"type": "system", "subtype": "model_refusal_fallback", "original_model": "claude-opus-5-5",
                                        "fallback_model": "claude-sonnet-5", "direction": "retry", "scope": "session"})
        self.assertIn("claude-sonnet-5 回答", a.host.agent_notice.call_args.args[0])
        ClaudeAgent._refusal_notice(a, {"type": "system", "subtype": "model_refusal_no_fallback", "original_model": "claude-opus-5-5"})
        self.assertIn("换个说法再发", a.host.agent_notice.call_args.args[0])
        a.host.agent_notice.reset_mock()
        ClaudeAgent._refusal_notice(a, {"type": "system", "subtype": "model_refusal_fallback", "scope": "local"})
        a.host.agent_notice.assert_not_called()


if __name__ == "__main__":
    unittest.main()
