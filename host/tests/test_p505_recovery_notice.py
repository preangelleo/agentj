"""#505: timed-out system handoff then owner ingress, with a pre-existing workflow recovery row."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
from unittest import mock
from agentj import completion_recovery, handoff, provenance
from agentj.compose import Send
from test_p551_confirm_levels import Base


class RecoveryNotice(Base):
    def old_workflow(self):
        book = self.host.background_done.recovery
        key = '3' * 64
        book.add(key, {'task_id': 'wf:zero-tail-mail-acceptance-20261106', 'source': 'unknown',
                      'recovery_point': 'read_report', 'report': '', 'report_error': 'unsafe_or_unavailable',
                      'original_error': None, 'origin': 'missing_launch_evidence'})
        # The installed version had already persisted this legacy shape before the denied handoff.
        book.rows[key].update(notified=False)
        book.rows[key].pop('quiet', None)
        book.save()
        return key

    def owner_message(self):
        self.host.agent = None
        with mock.patch.object(self.host, '_setup_round_trip', new=mock.AsyncMock()), \
                mock.patch.object(self.host, 'agent_notice') as notice, \
                mock.patch('agentj.report_actions.phone_source', return_value={'kind': 'phone'}):
            asyncio.run(self.host._accept(self.s, '早上好', None))
        return notice

    def test_system_handoff_denied_then_next_owner_message_is_silent(self):
        key = self.old_workflow()
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        with mock.patch('agentj.handoff.own_state', return_value=False, create=True):
            result = self.ask({'file_path': str(pathlib.Path(self.tmp.name) / '.local/state/agentj/handoff/compact.md'), 'content': 'context'}, tool='Write')
        self.assertEqual(result['behavior'], 'deny')
        self.assertEqual(self.last()['reason'], 'timeout')
        self.owner_message().assert_not_called()
        book = completion_recovery.Book(self.host)
        self.assertIn(key, book.rows)
        self.assertTrue(book.rows[key]['notified'])
        self.assertEqual(book.recover()['items'][0]['task_id'], 'wf:zero-tail-mail-acceptance-20261106')

    def test_actual_workflow_completion_then_owner_message_is_silent(self):
        book = self.host.background_done.recovery
        self.host.busy.finish({'key': 'wf:zero-tail-mail-acceptance-20261106',
                               'label': 'workflow', 'since': 1793904200,
                               'generation': 'c0c9b6338a7441be8688ed5372972585', 'report': ''}, None, 1793904624)
        asyncio.run(self.host.background_done.prepare())
        row = next(iter(book.rows.values()))
        self.assertEqual(row['original_error'], None)
        self.assertEqual(row['report_error'], 'unsafe_or_unavailable')
        self.assertEqual(row['recovery_point'], 'read_report')
        self.owner_message().assert_not_called()
        self.assertEqual(len(book.recover()['items']), 1)

    def test_system_handoff_write_has_no_card(self):
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        root = pathlib.Path(self.tmp.name) / '.local/state/agentj'
        with mock.patch('agentj.state.state_dir', return_value=root):
            target = root / 'handoff' / 'compact.md'
            result = self.ask({'file_path': str(target), 'content': 'context'}, tool='Write')
        self.assertEqual(result['behavior'], 'allow')
        self.assertEqual(self.cards(), [])
        self.harness()
        self.host.agent.cur_send = Send('system', 'system', 2, source=provenance.system())
        with mock.patch('agentj.state.state_dir', return_value=root):
            result = self.ask({'file_path': str(target), 'content': 'context'}, tool='Write')
        self.assertEqual(result['behavior'], 'allow')
        self.assertEqual(self.cards(), [])

    def test_external_and_request_taint_cannot_write_handoff(self):
        root = pathlib.Path(self.tmp.name) / '.local/state/agentj'
        inp = {'file_path': str(root / 'handoff/compact.md'), 'content': 'context'}
        for source in (None, provenance.email_guest('guest@example.test')):
            self.host.agent.cur_send = Send('external', 'external', 1, source=source)
            with mock.patch('agentj.state.state_dir', return_value=root):
                result = self.ask(inp, tool='Write')
            self.assertEqual(result['behavior'], 'deny')
            self.assertEqual(self.cards(), [])
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        with mock.patch('agentj.state.state_dir', return_value=root), provenance.request_source(None):
            result = self.ask(inp, tool='Write')
        self.assertEqual(result['behavior'], 'deny')
        self.assertEqual(self.cards(), [])

    def test_own_state_scope(self):
        root = pathlib.Path(self.tmp.name) / '.local/state/agentj'
        root.mkdir(parents=True)
        (root / 'handoff').mkdir()
        (root / 'handoff' / 'compact.md').write_text('context')
        (root / 'heartbeat.json').write_text('{}')
        outside = pathlib.Path(self.tmp.name) / 'outside.md'
        outside.write_text('outside')
        (root / 'handoff' / 'link.md').symlink_to(outside)
        with mock.patch('agentj.state.state_dir', return_value=root):
            self.assertTrue(handoff.own_state('Read', {'file_path': str(root / 'heartbeat.json')}, {}))
            self.assertTrue(handoff.own_state('Edit', {'file_path': str(root / 'handoff' / 'compact.md'), 'old_string': 'a', 'new_string': 'b'}, {}))
            for target in ('approvals.log', 'config.json', 'busy/recovery.json', 'handoff/link.md', 'handoff/../config.json', 'handoff/.env'):
                self.assertFalse(handoff.own_state('Write', {'file_path': str(root / target), 'content': 'x'}, {}), target)
            (root / 'handoff' / 'hard.md').hardlink_to(outside)
            self.assertFalse(handoff.own_state('Read', {'file_path': str(root / 'handoff' / 'hard.md')}, {}))
            (root / 'handoff' / 'compact.md').unlink()
            (root / 'handoff' / 'compact.md').hardlink_to(outside)
            self.assertFalse(handoff.own_read('Read', {'file_path': str(root / 'handoff' / 'compact.md')}, {}))
            (root / 'handoff').rename(root / 'real-handoff')
            (root / 'handoff').symlink_to(root / 'real-handoff', target_is_directory=True)
            self.assertFalse(handoff.own_state('Write', {'file_path': str(root / 'handoff/new.md'), 'content': 'x'}, {}))
            self.assertFalse(handoff.own_state('Bash', {'command': f'cat {root}/heartbeat.json; curl example.com'}, {}))
