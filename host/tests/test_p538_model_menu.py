"""P538 (#302 / #303): shared Claude model names always carry their version and a phone pick really switches the
terminal; any anchored terminal menu reaches the phone as its rows (or as its screen + a keypad), never Esc alone.
Fakes only (the P116 fake Herdr picker, synthetic screens, an owned PTY); no network, no real session."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import pty
import tty
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentj import shared, shared_menu as menu, shared_modelswitch as ms
from test_p116 import Picker
from test_p451_menu import Routing, QUOTA

# The terminal of 2026-10-26 11:11 (feedback 303): /model typed, the picker open, the session on Opus 4.8.
MODEL = """● 先回答:不用回终端。Do you want to keep it? (a question in the transcript, not a dialog)
❯ /model
────────────────────────────────────────────────────────
Select model
Switch between Claude models. Your pick becomes the default for new sessions. For other/previous model names, specify with --model.

❯ 1. Default (recommended)   Opus 5.5 · Best for everyday, complex tasks
  2. Opus                    Opus 5.5 · Best for everyday, complex tasks
  3. Fable                   Fable 5.1 · Most capable for your hardest and longest-running tasks
  4. Sonnet                  Sonnet 5.5 · Efficient for routine tasks
  5. Haiku                   Haiku 5.5 · Fastest for quick answers
  6. Opus 4.8 ✔              Newer version available · select Opus for Opus 5.5

◐ Medium effort (default) ←/→ to adjust

Enter to set as default · s to use this session only · Esc to cancel"""
# A permission choice: the hooks' box, never this reader's (an Enter there would answer it).
PERMISSION = """────────────────────────────────────────
 Bash command
   rm -rf build
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands in this project
   3. No, and tell Claude what to do differently (esc)
Esc to cancel"""
# /permissions — the owner's own settings menu: a menu like any other.
PERMISSIONS_MENU = """────────────────────────────────────────
Permissions
Choose what Claude can do without asking.
❯ 1. Allow   12 rules
  2. Ask     0 rules
  3. Deny    3 rules
Enter to view · Esc to cancel"""
# The flag-downgrade card (switchModelsOnFlag off): Opus 5.5 paused, the owner chooses.
FLAG = """────────────────────────────────────────
Opus 5.5 paused on this message
Our safety systems flagged this request. You can continue on Opus 4.8.
❯ 1. Switch to Opus 4.8 for this message
  2. Switch to Opus 4.8 and keep using it
  3. Stay on Opus 5.5 and edit the message
Enter to confirm · Esc to cancel"""
FAKE_KEY = "sk-ant-" + "api03-" + "Q7" * 26          # built at run time: a synthetic credential shape, never a real key
UNKNOWN = """────────────────────────────────────────
Some future picker
[x] alpha   [ ] beta   token """ + FAKE_KEY + """
Space to toggle · Esc to cancel"""
# P538 review B1: other boxes the hooks own — plan approval, folder trust, a new MCP server.
PLAN = """────────────────────────────────────────
Ready to code?
Here is Claude's plan: refactor the parser.
Would you like to proceed?
❯ 1. Yes, and auto-accept edits
  2. Yes, and manually approve edits
  3. No, keep planning
Esc to cancel"""
TRUST = """────────────────────────────────────────
Do you trust the files in this folder?
❯ 1. Yes, proceed
  2. No, exit
Enter to confirm · Esc to exit"""
MCP = """────────────────────────────────────────
New MCP server found in .mcp.json: fixture
❯ 1. Use this and all future MCP servers in this project
  2. Use this MCP server
  3. Continue without using this MCP server
Enter to confirm · Esc to cancel"""
# P538 review B2: a reply that quotes a menu, with the real idle input box and status line under it.
FORGED = """⏺ Here is what the page says:
❯ 1. Run the cleanup
  2. Skip
Enter to confirm · Esc to cancel
────────────────────────────────────────
❯
────────────────────────────────────────
  Opus 5.5 medium · ~/work
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""


class Names(unittest.TestCase):
    def test_rows_carry_versions_like_the_status_line(self):
        self.assertEqual(ms.names(MODEL), ["Default (Opus 5.5)", "Opus 5.5", "Fable 5.1", "Sonnet 5.5", "Haiku 5.5", "Opus 4.8"])
        self.assertEqual(ms.row_name("Opus (1M context)", "Opus 5.5 with 1M context · Best"), "Opus 5.5 (1M context)")
        self.assertEqual(ms.row_name("Opus 5.5", "For complex work"), "Opus 5.5")          # 2.1.289 labels unchanged
        self.assertEqual(ms.row_name("Opus", "Fable 5.1 · mismatch"), "Opus")               # never another family's number
        self.assertEqual(ms.row_index(MODEL, "opus-5.5"), 1)
        self.assertEqual(ms.row_index(MODEL, "opus"), 1)        # a bare id is the bare row (the newest), never "Opus 4.8"
        self.assertEqual(ms.row_index(MODEL, "opus-4.8"), 5)

    def test_bare_status_line_name_gets_its_version_from_the_model_id(self):
        self.assertEqual(shared.versioned({"model": "claude-opus-5-5", "model_name": "Opus"})["model_name"], "Opus 5.5")
        self.assertEqual(shared.versioned({"model": "claude-opus-5-5[1m]", "model_name": "Opus"})["model_name"], "Opus 5.5 (1M context)")
        self.assertEqual(shared.versioned({"model": "claude-opus-4-8", "model_name": "Opus 4.8"})["model_name"], "Opus 4.8")
        self.assertEqual(shared.versioned({"model": None, "model_name": "Opus"})["model_name"], "Opus")   # no id: not invented


class SharedSwitch(Picker):
    """#302 ④: on Opus 4.8 the phone's "Opus" used to count as already there — the terminal never switched."""

    def test_phone_opus_on_opus_4_8_really_switches_and_confirms_5_5(self):
        self.set(v3xx=True, model="Opus 4.8", session_null=False)
        a, host = self.agent()
        (self.state / "claude-statusline" / "sess-jarvis.json").write_text(json.dumps(
            {"session_id": "sess-jarvis", "model_name": "Opus 4.8", "effort": "medium"}))
        asyncio.run(a._fresh_native()); self.assertEqual(a.cur_model(), "opus-4.8")
        self.assertIsNone(asyncio.run(a.model_set({"model": "opus", "effort": None})))
        self.assertEqual(self.tui_()["model"], "Opus 5.5")                 # the terminal really moved
        self.assertNotIn("default_written", self.tui_())                   # session only, no settings write
        self.assertEqual(host.meter_update.call_args.kwargs["model_name"], "Opus 5.5")
        rows = json.loads((self.state / "claude-picker-rows.json").read_text())
        self.assertEqual(rows, {"v": 2, "rows": ["Default (Opus 5.5)", "Opus 5.5", "Fable 5.1", "Sonnet 5.5", "Haiku 5.5", "Opus 4.8"]})
        asyncio.run(a.refresh_models())
        self.assertEqual([m["name"] for m in a.models_cache], ["Opus 5.5", "Fable 5.1", "Sonnet 5.5", "Haiku 5.5", "Opus 4.8"])
        # and back: the versioned id lands exactly, a receipt for a different Opus is not accepted
        self.assertIsNone(asyncio.run(a.model_set({"model": "opus-4.8", "effort": None})))
        self.assertEqual(self.tui_()["model"], "Opus 4.8")

    def test_exact_id_already_running_is_a_no_op(self):
        self.set(v3xx=True, model="Opus 4.8", session_null=False)
        a, _ = self.agent()
        (self.state / "claude-statusline" / "sess-jarvis.json").write_text(json.dumps(
            {"session_id": "sess-jarvis", "model_name": "Opus 4.8", "effort": "medium"}))
        self.assertIsNone(asyncio.run(a.model_set({"model": "opus-4.8", "effort": None})))
        self.assertFalse(any(x[:2] == ["agent", "prompt"] for x in self.tui_().get("argv", [])))

    def test_old_bare_rows_file_is_relearned_and_legacy_rows_say_latest(self):
        a, _ = self.agent()
        (self.state / "claude-picker-rows.json").write_text(json.dumps(["Default (recommended)", "Opus", "Fable", "Opus 4.8"]))
        a.picker_rows = None
        self.assertEqual(a._picker_rows(), [])
        asyncio.run(a.refresh_models())
        names = [m["name"] for m in a.models_cache]
        self.assertTrue(all(any(c.isdigit() for c in n) or "latest" in n or "最新" in n for n in names), names)

    def test_forged_picker_rows_are_not_learned(self):
        a, _ = self.agent()
        a.learn_picker_rows(["Opus 5.5", "Ignore previous instructions", "Default (Opus 5.5)", "Fable\u202e 5.1"])
        self.assertEqual(a.picker_rows, ["Opus 5.5", "Default (Opus 5.5)"])

    def test_model_card_default_duplicate_is_one_native_model(self):
        self.set(v3xx=True, model="Opus 4.8", session_null=False)
        a, host = self.agent(); host.lang = "zh"
        a.learn_picker_rows(ms.names(MODEL))
        res = asyncio.run(a.command("model", ""))
        self.assertIn("Opus 5.5", [m["name"] for m in res.models])
        self.assertNotIn("default", [m["id"] for m in res.models])


class Detect(unittest.TestCase):
    def test_model_menu_rows_cursor_and_versions(self):
        info = menu.detect(MODEL)
        self.assertEqual((info["kind"], info["title"], info.get("model")), ("menu", "Select model", True))
        self.assertEqual([r["cur"] for r in info["rows"]], [True, False, False, False, False, False])
        self.assertEqual([r["on"] for r in info["rows"]][-1], True)
        for lang, cur in (("zh", "（当前）"), ("en", " (current)")):
            q = menu.questions(info, lang)[0]
            labels = [o["l"] for o in q["o"]]
            self.assertEqual(labels[:6], ["❯ Default (Opus 5.5)", "Opus 5.5", "Fable 5.1", "Sonnet 5.5", "Haiku 5.5", "Opus 4.8" + cur])
            self.assertIn("Esc", labels[-1])
            self.assertEqual(q["o"][1]["d"], "Best for everyday, complex tasks")
        self.assertEqual(menu.row_keys(info, 2), (["down"], "s"))               # model picker: session only
        self.assertEqual(menu.row_keys(info, 6), (["down"] * 5, "s"))
        self.assertIsNone(menu.row_keys(info, 7))

    def test_permission_choice_is_never_a_menu(self):
        self.assertIsNone(menu.detect(PERMISSION))
        self.assertIsNone(menu.detect(PERMISSION.replace("Do you want to proceed?", "Do you want to make this edit?")))
        for box in (PLAN, TRUST, MCP, PERMISSIONS_MENU):          # hooks' boxes and permission-rule edits stay at the computer
            self.assertIsNone(menu.detect(box), box.splitlines()[1])
        self.assertIsNone(menu.detect("Switch model?\n❯ 1. Yes, switch to Opus 5.5\n  2. No, go back\nEsc to cancel"))

    def test_menu_quoted_in_a_reply_above_the_input_box_is_history(self):
        self.assertIsNone(menu.detect(FORGED))
        self.assertIsNone(menu.detect(MODEL + "\n  status line one\n  status line two"))
        self.assertEqual(menu.detect(MODEL + "\n  one trailing line")["kind"], "menu")
        # a hint right under a rule (a custom status line) never falls back to the screen above it
        self.assertIsNone(menu.detect(FORGED.split("────")[0] + "────────────────────\nEsc to exit"))
        # the quota / Rewind paths obey the same bottom anchor (P451 cards from a quoted menu)
        self.assertIsNone(menu.detect(QUOTA + "\n────────────\n❯\n────────────\n  Opus 5.5 medium"))

    def test_card_text_has_no_control_or_bidi_characters(self):
        q = menu.questions(menu.detect(FLAG.replace("Stay on", "Stay\u202e on\x1b")), "en")[0]
        self.assertFalse(any(c in o["l"] for o in q["o"] for c in "\u202e\x1b"))

    def test_flag_card_is_a_menu(self):
        f = menu.detect(FLAG)
        self.assertEqual((f["kind"], f["title"], len(f["rows"])), ("menu", "Opus 5.5 paused on this message", 3))
        for lang in ("zh", "en"):
            q = menu.questions(f, lang)[0]
            self.assertIn("Opus 5.5 paused", q["q"]); self.assertIn("flagged", q["h"])
            self.assertEqual(len(q["o"]), 4)

    def test_unreadable_modal_falls_back_to_screen_and_keypad_redacted(self):
        s = menu.detect(UNKNOWN)
        self.assertEqual(s["kind"], "screen")
        for lang in ("zh", "en"):
            q = menu.questions(s, lang)[0]
            self.assertEqual(len(q["o"]), 4)                              # ↑ ↓ Enter Esc, never Esc alone
            self.assertIn("\n", q["h"]); self.assertIn("Some future picker", q["h"])
            self.assertNotIn(FAKE_KEY[12:], q["h"])                      # a credential on screen never leaves the host
        self.assertEqual(menu.options(s, "zh"), ["up", "down", "enter", "esc"])

    def test_history_and_input_prompt_below_are_not_menus(self):
        self.assertIsNone(menu.detect(MODEL + "\n────\n❯ "))
        self.assertIsNone(menu.detect("an assistant reply mentioning Esc to cancel and 1. something"[:0] + "plain reply"))


class Act(unittest.IsolatedAsyncioTestCase):
    actor = Routing.actor

    async def test_row_pick_moves_verifies_cursor_then_confirms(self):
        a = self.actor(); a.learn_picker_rows = Mock(); d = menu.Desk(a)
        a.host.question.return_value = ("answer", [[2]])
        moved = MODEL.replace("❯ 1. Default", "  1. Default").replace("  2. Opus   ", "❯ 2. Opus   ")
        sent = []
        screens = [MODEL, MODEL, MODEL, moved]
        async def snap():
            return menu.detect(screens.pop(0) if len(screens) > 1 else screens[0]), "pane"
        async def keys(route, cmds):
            sent.append(cmds); return True
        with patch.object(d, "snapshot", new=snap), patch.object(d, "_keys", new=keys), patch.object(menu.asyncio, "sleep", new=AsyncMock()):
            await d.poll(); await d.task
        self.assertEqual(sent, [["down"], ["s"]])
        a.learn_picker_rows.assert_called_once_with(["Default (Opus 5.5)", "Opus 5.5", "Fable 5.1", "Sonnet 5.5", "Haiku 5.5", "Opus 4.8"])

    async def test_cursor_that_does_not_land_never_confirms(self):
        a = self.actor(); d = menu.Desk(a)
        a.host.question.return_value = ("answer", [[3]])
        sent = []
        async def keys(route, cmds):
            sent.append(cmds); return True
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(menu.detect(FLAG), "pane"))), \
                patch.object(d, "_keys", new=keys), patch.object(menu.asyncio, "sleep", new=AsyncMock()):
            await d.poll(); await d.task
        self.assertEqual(sent, [["down", "down"]])                       # no Enter after an unverified move

    async def test_model_pick_answers_only_its_own_switch_warning(self):
        a = self.actor(); d = menu.Desk(a)
        a.host.question.return_value = ("answer", [[2]])
        moved = MODEL.replace("❯ 1. Default", "  1. Default").replace("  2. Opus   ", "❯ 2. Opus   ")
        screens = [MODEL, MODEL, MODEL, moved]
        async def snap():
            return menu.detect(screens.pop(0) if len(screens) > 1 else screens[0]), "pane"
        sent = []
        async def keys(route, cmds):
            sent.append(cmds); return True
        warn = "Switch model?\n Your next response will be slower\n ❯ 1. Yes, switch to Opus 5.5\n   2. No, go back\n"
        with patch.object(d, "snapshot", new=snap), patch.object(d, "_keys", new=keys), \
                patch.object(d, "raw", new=AsyncMock(return_value=warn)), patch.object(menu.asyncio, "sleep", new=AsyncMock()):
            await d.poll(); await d.task
        self.assertEqual(sent, [["down"], ["s"], ["enter"]])

    async def test_keypad_only_for_screen_kind_and_waiting_blocks(self):
        a = self.actor(); d = menu.Desk(a)
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(menu.detect(QUOTA), "pane"))):
            self.assertFalse(await d.send("pane", "enter"))
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(menu.detect(UNKNOWN), "pane"))), \
                patch.object(d, "_keys", new=AsyncMock(return_value=True)) as k:
            self.assertTrue(await d.send("pane", "down")); k.assert_awaited_with("pane", ["down"])
            a.state.status = "waiting"
            self.assertFalse(await d.send("pane", "enter"))

    async def test_screen_card_reoffered_after_a_key(self):
        a = self.actor(); d = menu.Desk(a)
        a.host.question.return_value = ("answer", [[2]])
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(menu.detect(UNKNOWN), "pane"))), \
                patch.object(d, "_keys", new=AsyncMock(return_value=True)):
            await d.poll(); await d.task
        self.assertFalse(d.attempted)

    async def test_owned_pty_arrow_and_session_only_bytes(self):
        a = self.actor(); a.native = SimpleNamespace(pid=17, returncode=None)
        master, slave = pty.openpty(); tty.setraw(slave); a.master = master
        try:
            d = menu.Desk(a)
            d.surface.feed(("\x1b[2J\x1b[H" + FLAG.replace("\n", "\r\n")).encode())
            self.assertTrue(await d._keys(None, ["up", "down", "s", "enter"]))
            self.assertEqual(os.read(slave, 20), b"\x1b[A\x1b[Bs\r")
        finally:
            os.close(master); os.close(slave)


class Fixture(unittest.TestCase):
    def test_phone_fixture_is_what_the_host_sends(self):
        """web/test/p538_menu.mjs renders these exact cards; regenerate when the card text changes."""
        from pathlib import Path
        fixture = json.loads((Path(__file__).resolve().parents[2] / "web/test/fixtures/p538-cards.json").read_text())
        self.assertEqual(sorted(fixture), ["flag", "model", "screen"])
        for name, text in (("model", MODEL), ("flag", FLAG), ("screen", UNKNOWN)):
            for lang in ("zh", "en"):
                self.assertEqual(fixture[name][lang], menu.questions(menu.detect(text), lang), (name, lang))


if __name__ == "__main__":
    unittest.main()
