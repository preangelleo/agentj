"""P550: P538 (generic TUI menus on the phone) × P539 ("Session paused" card) in one shared_menu. Synthetic screens only."""
import _hermetic  # noqa: F401
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentj import shared_menu as menu
from test_p539_flag import CREDITS, PAUSED, PAUSED_PLAIN
from test_p538_model_menu import UNKNOWN

PROMPT = "some reply\n────────────────────────────────────────\n❯ fix the parser please\n────────────────────────────────────────"
PAID = ("────────────────────────────────────────\nOut of credits\n❯ 1. Upgrade your plan\n  2. Wait for reset\n"
        "Enter to confirm · Esc to cancel")


class Merge(unittest.IsolatedAsyncioTestCase):
    def actor(self):
        host = SimpleNamespace(stopped=Mock(return_value=False), lang="zh")
        return SimpleNamespace(host=host, state=SimpleNamespace(status="idle"), session={"pid": 1}, native=None, master=None)

    def test_paused_card_wins_and_other_paused_or_paid_modals_are_not_generic_menus(self):
        self.assertEqual(menu.detect(PAUSED)["kind"], "paused")
        self.assertEqual(menu.options(menu.detect(PAUSED_PLAIN), "zh"), ["1", "retry", "edit", "new"])
        self.assertEqual(menu.actions(menu.detect(PAUSED)), ["1", "retry", "edit", "new"])
        for screen in (CREDITS, PAUSED_PLAIN.replace("2. Edit prompt", "3. Edit prompt"), PAID):
            self.assertIsNone(menu.detect(screen))
        self.assertEqual(menu.detect(UNKNOWN)["kind"], "screen")          # P538 keypad still offered

    async def test_enter_is_scoped_to_its_caller(self):
        d = menu.Desk(self.actor())
        keys = AsyncMock(return_value=True)
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(menu.detect(UNKNOWN), "pane"))), \
                patch.object(d, "_keys", new=keys):
            self.assertFalse(await d.send("pane", "enter", want="prompt"))   # a retry never presses Enter on a screen card
            self.assertTrue(await d.send("pane", "enter", want="screen"))
        with patch.object(d, "snapshot", new=AsyncMock(return_value=(None, "pane"))), \
                patch.object(d, "screen_text", new=AsyncMock(return_value=(PROMPT, "pane"))), patch.object(d, "_keys", new=keys):
            self.assertFalse(await d.send("pane", "enter", want="screen"))   # a stale keypad tap never submits a prompt
            self.assertTrue(await d.send("pane", "enter", want="prompt"))
        keys.assert_awaited_with("pane", ["enter"])


if __name__ == "__main__":
    unittest.main()
