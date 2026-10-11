"""P132: pages the Agent started itself (a /loop or ScheduleWakeup firing, a background task finishing) name their source,
so the phone can give every page a header card; they are never mirrored as a desktop input."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _hermetic  # noqa: F401,E402
from agentj.serve import Host
from agentj.transcript_input import human_input, proactive_origin


class Origin(unittest.TestCase):
    def test_claude_records_classify_loop_and_task(self):
        self.assertEqual(proactive_origin({'turnOrigin': 'scheduled', 'isMeta': True, 'scheduledTaskId': 'x'}), 'loop')
        self.assertEqual(proactive_origin({'scheduledTaskId': 'x'}), 'loop')
        self.assertEqual(proactive_origin({'turnOrigin': 'task_notification', 'origin': {'kind': 'task-notification'}}), 'task')
        self.assertEqual(proactive_origin({}, '<task-notification> <task-id>b1</task-id>'), 'task')
        for r in ({'turnOrigin': 'human', 'origin': {'kind': 'human'}}, {}, {'origin': 'odd'}, None):
            self.assertIsNone(proactive_origin(r, 'hello'))

    def test_task_notification_is_not_a_human_input(self):
        self.assertFalse(human_input({'turnOrigin': 'task_notification'}, [], '<task-notification>done</task-notification>'))
        self.assertFalse(human_input({}, [], '<task-notification>done</task-notification>'))
        self.assertTrue(human_input({'turnOrigin': 'human'}, [], 'real keyboard input'))


class AgentSource(unittest.TestCase):
    def host(self):
        h = SimpleNamespace(added=[], desktop_turn=None, lang='zh', hist_update=lambda *a, **k: [], media_later=lambda tid: None)
        h.hist_add = lambda src, text, end: (h.added.append((src, text)), {'id': len(h.added)})[1]
        for name in ('proactive_turn', '_agent_src', 'desktop_text', 'desktop_end', 'desktop_input'):
            setattr(h, name, getattr(Host, name).__get__(h))
        return h

    def test_via_names_the_page_until_the_turn_ends(self):
        h = self.host()
        h.proactive_turn('loop'); h.desktop_text('checked'); h.desktop_text('second chunk'); h.desktop_end()
        h.desktop_text('plain report')
        h.proactive_turn('task'); h.desktop_text('task done')
        h.proactive_turn('bogus'); h.desktop_text('x')
        self.assertEqual([s for s, _ in h.added], [
            {'k': 'agent', 'text': '', 'via': 'loop'}, {'k': 'agent', 'text': '', 'via': 'loop'},
            {'k': 'agent', 'text': ''}, {'k': 'agent', 'text': '', 'via': 'task'}, {'k': 'agent', 'text': ''}])

    def test_desktop_input_clears_a_pending_via(self):
        h = self.host()
        h.proactive_turn('loop'); h.desktop_input('typed on the computer')
        self.assertEqual(h.added[0][0], {'k': 'host', 'name': h.added[0][0]['name'], 'text': 'typed on the computer'})
        self.assertIsNone(h.proactive_via)


if __name__ == '__main__':
    unittest.main()
