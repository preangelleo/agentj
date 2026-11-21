"""Native worker prose stays private and an owner report uses only the final result."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import main_identity
from agentj.agent import ClaudeAgent


class HelperReport(unittest.TestCase):
    def setUp(self):
        self.host = SimpleNamespace(agent_text=Mock())
        self.agent = ClaudeAgent(self.host, {'language': 'zh'})
        self.agent.use_phase = Mock()
        self.agent.use_put = Mock()
        self.agent.cur_model = Mock(return_value=None)

    def native_blocks(self, blocks, parent=None, message_id=None):
        self.agent.on_event({'type': 'assistant', 'parent_tool_use_id': parent,
                             'message': {'model': 'fixture', 'id': message_id, 'content': blocks}})

    def assistant(self, text, parent=None, message_id=None):
        self.native_blocks([{'type': 'text', 'text': text}], parent, message_id)

    def begin_report(self):
        self.agent.cur_send = SimpleNamespace(background_done_ids=['worker-fixture'])
        self.agent.turn_done = asyncio.Event()

    def finish_report(self, text='', parent=None):
        self.agent.on_event({'type': 'result', 'subtype': 'success', 'parent_tool_use_id': parent,
                             'result': text, 'usage': {}})

    def test_native_child_text_never_enters_owner_output(self):
        self.assistant('Checking another source.', parent='worker-fixture')
        self.host.agent_text.assert_not_called()
        self.assistant('核对完毕。')
        self.host.agent_text.assert_called_once_with('核对完毕。')

    def test_background_completion_publishes_only_native_final_result(self):
        self.begin_report()
        self.assistant('Checking another source.')
        self.finish_report('Child result', parent='worker-fixture')
        self.host.agent_text.assert_not_called()
        self.assertFalse(self.agent.turn_done.is_set())
        self.finish_report('已核对最终结果。')
        self.assertTrue(self.agent.turn_done.is_set())
        self.host.agent_text.assert_called_once_with('已核对最终结果。')

    def test_blank_success_preserves_latest_standalone_main_reply(self):
        self.begin_report()
        self.assistant('检查缓存。', message_id='progress')
        self.native_blocks([{'type': 'tool_use', 'name': 'Read', 'id': 'reader'}], message_id='progress')
        self.assistant('核对完毕。', message_id='final')
        self.assistant('请换网络重试。', message_id='final')
        self.assistant('Raw child quote', parent='worker-fixture')
        self.host.agent_text.assert_not_called()
        self.finish_report()
        self.assistant('Late progress.', message_id='late')
        self.finish_report('Duplicate result')
        self.host.agent_text.assert_called_once_with('核对完毕。\n\n请换网络重试。')

    def test_blank_success_does_not_restore_a_split_tool_preamble(self):
        self.begin_report()
        self.assistant('Checking another source.', message_id='tool')
        self.native_blocks([{'type': 'tool_use', 'name': 'Search', 'id': 'search'}], message_id='tool')
        self.assistant('Still checking.', message_id='tool')
        self.finish_report()
        self.host.agent_text.assert_called_once_with('[silent]')

    def test_reporting_policy_is_localized_in_native_context(self):
        zh = main_identity.prompt({'language': 'zh'}, 'claude')
        en = main_identity.prompt({'language': 'en'}, 'claude')
        self.assertIn('主动汇报用主人语言', zh)
        self.assertIn('中间尝试留在内部', zh)
        self.assertIn('Use the owner’s language for unsolicited reports', en)
        self.assertIn('keep intermediate attempts internal', en)
