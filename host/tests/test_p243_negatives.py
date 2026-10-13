"""P240 integration acceptance for P243 (negative cases): with background work alive and the settle window already open,
a phone /compact must still not be typed while the main thread runs a foreground tool, waits on an approval dialog or is
compacting; and a control the owner cancelled before it was sent is never typed later, even when the gate opens."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from unittest.mock import patch
from agentj import shared
from test_p243_bg_compact import Base


class Negatives(Base):
    async def held(self, *events):
        self.turn_with_background()
        for ev in events:
            self.a._note_background(ev['hook_event_name'], ev)
            self.a.state.handle(ev)
        self.reg = 'shell'
        self.a.main_seen_at -= 1000          # the quiet window alone would let it through
        with patch.object(shared, 'BG_SETTLE_S', 0), patch.object(shared, 'BUSY_FALLBACK_S', 0), \
                patch.object(shared, 'DESKTOP_IDLE_WAIT', .3):
            res, writes = await self.run_compact()
        self.assertEqual(writes, []); self.assertEqual(res.kind, 'error')
        self.assertFalse(self.a.cur_cmd.native_started)

    async def test_foreground_tool_with_background_alive_is_not_typed_into(self):
        await self.held({'hook_event_name': 'UserPromptSubmit'},
                        {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'make test'}})

    async def test_approval_dialog_with_background_alive_is_not_typed_into(self):
        await self.held({'hook_event_name': 'UserPromptSubmit'},
                        {'hook_event_name': 'PermissionRequest', 'tool_name': 'Bash'})

    async def test_compaction_in_progress_with_background_alive_is_not_typed_into(self):
        await self.held({'hook_event_name': 'PreCompact', 'trigger': 'manual'})

    async def test_cancelled_while_waiting_is_never_typed_after_the_gate_opens(self):
        self.a.state.handle({'hook_event_name': 'UserPromptSubmit'})
        self.reg = 'busy'

        async def owner():
            await asyncio.sleep(.15)
            self.a.cur_cmd.cancelled = True                     # withdrawn on the phone while it waited
            await asyncio.sleep(.05)
            self.a.state.handle({'hook_event_name': 'Stop', 'last_assistant_message': 'done'})
            self.reg = 'idle'                                  # the gate is open now
        with patch.object(shared, 'DESKTOP_IDLE_WAIT', 2):
            side = asyncio.create_task(owner())
            res, writes = await self.run_compact(timeout=3)
            await side
        self.assertEqual(writes, []); self.assertIn('已取消', res.text)
        self.assertFalse(self.a.cur_cmd.native_started)

    async def test_cancelled_during_the_handoff_turn_is_not_typed(self):
        # P151 runs the handoff turn first; a cancel that lands during it must still stop the /compact
        async def prep():
            self.a.cur_cmd.cancelled = True
        with patch.object(shared.handoff, 'fresh', return_value=False), patch.object(self.a, '_handoff_prep', side_effect=prep):
            res, writes = await self.run_compact()
        self.assertEqual(writes, []); self.assertIn('已取消', res.text)


if __name__ == '__main__':
    unittest.main()
