"""0.30.0 #22: compatible execution preferences reach the native identity layer."""
import _hermetic
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from agentj import main_identity
from agentj.agent_codex import CodexAgent


class OwnerExecutionPreference(unittest.TestCase):
    def test_personal_preference_is_in_actual_native_developer_instructions(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {'kind': 'codex', 'dir': td, 'language': 'zh', 'instructions': '能自己做就做。'}
            owner_file = Path(td) / 'owner-preference.md'
            owner_file.write_text('简单文本工作请亲自处理，不派子 Agent。')
            owner_file.chmod(0o600)
            cfg['private_instructions_file'] = str(owner_file)
            host = SimpleNamespace(st=Mock())
            host.st.agent_session.return_value = None
            agent = CodexAgent(host, cfg)
            agent.call = AsyncMock(return_value={'thread': {'id': 'isolated-test'}})
            agent.meter = Mock()
            agent.models_cache = [{'id': 'model'}]
            asyncio.run(agent._thread())
            method, body = agent.call.call_args.args
            self.assertEqual(method, 'thread/start')
            text = body['developerInstructions']
            self.assertEqual(text, main_identity.prompt(cfg))
            self.assertIn(cfg['instructions'], text)
            self.assertIn(owner_file.read_text(), text)
            self.assertLess(text.index('派活不是禁令'), text.index(cfg['instructions']))
            self.assertIn('指纹清单、凭据不进对话', text)
            self.assertIn('既有付款/删除确认和 harness 硬限制不解除', text)

    def test_higher_priority_reminders_do_not_reinstate_a_delegation_ban(self):
        for lang, default, preference in (
            ('zh', '默认', '不另验身份'),
            ('en', 'Default', 'without an extra identity check'),
        ):
            self.assertIn(default, main_identity.IDENTITY_REMINDER[lang])
            self.assertIn(preference, main_identity.IDENTITY_REMINDER[lang])
        for lang, banned in (
            ('zh', ('绝不 Edit/Write 工作流文件', '绝不下场干活')),
            ('en', ('NEVER Edit/Write workflow files', 'never do the work yourself:')),
        ):
            text = main_identity.prompt({'language': lang}) + main_identity.turn_reminder({'language': lang})
            for phrase in banned:
                self.assertNotIn(phrase, text)


if __name__ == '__main__':
    unittest.main()
