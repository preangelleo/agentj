"""P380 #96: optional native continuation updates the one durable clear greeting card."""
import _hermetic
from _testtmp import TEST_TMP
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
from agentj import humane, slash
from agentj.agent import Agent
from agentj.history import History
from agentj.serve import Host
from agentj.state import State


class ResumeCopy(unittest.TestCase):
    def test_harness_language_and_safe_command_matrix(self):
        expected = {'codex': 'codex resume', 'claude': 'claude --resume', 'opencode': 'opencode -s'}
        for lang in ('zh', 'en'):
            for kind, command in expected.items():
                host = SimpleNamespace(lang=lang, preferences={})
                row = humane.clear_resume(host, kind, 'new-id_123')
                self.assertEqual(row, {'title': humane.DATA['clear_resume'][kind][lang], 'command': command + ' new-id_123'})
                self.assertNotIn('新对话已开始', row['title'])
                self.assertNotIn('不会自动跟过来', row['title'])
                self.assertNotIn('继续请运行', row['title'])
                self.assertIn('如果您也想' if lang == 'zh' else 'To continue this same conversation', row['title'])

    def test_no_command_without_known_harness_and_safe_id(self):
        host = SimpleNamespace(lang='en', preferences={})
        for kind, sid in [('other', 'new-id'), ('codex', None), ('claude', ''), ('opencode', 'x; echo unsafe'), ('codex', 'x\nnext'), ('codex', '--help'), ('claude', '-dangerously-skip-permissions')]:
            self.assertIsNone(humane.clear_resume(host, kind, sid))

    def test_independent_new_session_updates_only_after_clear_once(self):
        for kind in ('codex', 'claude', 'opencode'):
            values, updates = {}, []
            st = SimpleNamespace(agent_session=lambda k: values.get(k), set_agent_session=lambda k, v: values.__setitem__(k, v))
            a = Agent.__new__(Agent); a.kind = kind
            a.host = SimpleNamespace(st=st, clear_resume=lambda *args: updates.append(args))
            a.record_session('ordinary'); self.assertEqual(updates, [])
            values[kind + '.prev'] = 'ordinary'; values[kind] = None
            a.record_session('after-clear'); a.record_session('after-clear')
            self.assertEqual(updates, [(kind, 'after-clear')])


class OwnedSharedOpenCode(unittest.IsolatedAsyncioTestCase):
    async def test_owned_v1_new_session_uses_single_card_update_path(self):
        from agentj.shared import SharedOpenCodeAgent
        a = SharedOpenCodeAgent.__new__(SharedOpenCodeAgent)
        values = {'opencode.prev': 'old-id'}
        st = SimpleNamespace(agent_session=lambda k: values.get(k), set_agent_session=lambda k, v: values.__setitem__(k, v))
        a.kind = 'opencode'; a.owned_server = True; a.cfg = {'dir': '/tmp/p380-project'}
        a.host = SimpleNamespace(st=st, clear_resume=Mock(), agent_notice=Mock())
        a.client = SimpleNamespace(request=AsyncMock(side_effect=[(200, {'connected': []}), (200, {'id': 'ses_new', 'directory': '/tmp/p380-project'})]))
        a._prime = AsyncMock()
        await a._prepare()
        self.assertEqual(values['opencode'], 'ses_new')
        a.host.clear_resume.assert_called_once_with('opencode', 'ses_new')
        a.host.agent_notice.assert_not_called()


class SingleCard(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(tmp.cleanup)
        self.st = State(Path(tmp.name) / 'state'); self.st.root.mkdir()
        self.h = Host.__new__(Host); h = self.h
        h.st = self.st; h.hist = History(self.st); h.lang = 'zh'; h.preferences = {}
        h.agent = SimpleNamespace(kind='codex'); h.telegram = None
        for name in ('emit', '_remember', '_post', 'hist_emit', 'activity', 'hist_meta_all'):
            setattr(h, name, Mock())

    def test_single_greeting_delayed_id_updates_original_card_and_survives_restart(self):
        h = self.h; self.st.set_agent_session('codex', 'old-id')
        self.st.set_agent_session('codex', None)
        h.cmd_done(SimpleNamespace(name='clear', by='owner', turn=None), slash.Result(humane.of(h, 'clear.kept'), undo=True, sep=True))
        self.assertEqual(len(h.hist.turns), 1)
        original = next(iter(h.hist.turns.values())); greeting = original['reply']['text']
        self.assertIn('董事长', greeting); self.assertTrue(original['card']['undo'])
        self.assertNotIn('resume', original['card'])
        later = h.hist_add({'k': 'phone', 'text': 'hello'}, 'later reply')
        h.hist = History(self.st)  # a restart before the real harness id exists
        h.clear_resume('codex', 'new-id')
        h.clear_resume('codex', 'new-id')
        self.assertEqual(len(h.hist.turns), 2)
        self.assertEqual(h.hist.get(original['id'])['reply']['text'], greeting)
        self.assertEqual(h.hist.get(later['id'])['reply']['text'], 'later reply')
        self.assertEqual(h.emit.call_count, 1, 'only the original command receipt, no resume notice')
        restored = History(self.st)
        self.assertEqual(restored.get(original['id'])['card']['resume']['command'], 'codex resume new-id')
        self.assertTrue(restored.get(original['id'])['card']['undo'])

    def test_first_session_without_clear_adds_no_card_or_notice(self):
        h = self.h
        a = Agent.__new__(Agent); a.kind = 'codex'; a.host = h
        a.record_session('first-id')
        self.assertEqual(h.hist.turns, {})
        h.emit.assert_not_called()

    def test_immediate_shared_id_is_attached_to_final_card_only(self):
        for kind in ('claude', 'codex', 'opencode'):
            h = self.h; h.agent.kind = kind; self.st.set_agent_session(kind, 'new-id')
            h.cmd_card('clear', slash.Result('waiting'), interim=True)
            interim = h._remember.call_args.kwargs['x']
            self.assertNotIn('resume', interim); self.assertNotIn('clear_harness', interim)
            h.cmd_card('clear', slash.Result(''))
            card = list(h.hist.turns.values())[-1]['card']
            self.assertEqual(card['resume'], humane.clear_resume(h, kind, 'new-id'))

    def test_failure_noop_undo_and_other_harness_never_attach(self):
        h = self.h
        for kind in ('error', 'info'):
            h.cmd_card('clear', slash.Result('unchanged', kind))
            card = list(h.hist.turns.values())[-1]['card']
            self.assertNotIn('clear_harness', card); self.assertNotIn('resume', card)
        page = h.hist_add({'k': 'cmd'}, 'greeting', card={'cmd': 'clear', 'clear_harness': 'codex', 'undo': True})
        h.clear_resume('claude', 'new-id'); self.assertNotIn('resume', h.hist.get(page['id'])['card'])
        h.hist_add({'k': 'cmd'}, 'undone', card={'cmd': 'undo_clear'})
        h.clear_resume('codex', 'new-id'); self.assertNotIn('resume', h.hist.get(page['id'])['card'])
        h.hist_add({'k': 'cmd'}, 'newer clear', card={'cmd': 'clear', 'clear_harness': 'claude'})
        h.clear_resume('codex', 'new-id'); self.assertNotIn('resume', h.hist.get(page['id'])['card'])


if __name__ == '__main__':
    unittest.main()
