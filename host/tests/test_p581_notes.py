import _hermetic
import asyncio
import contextlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agentj import auto_update, notices, release_notes
from agentj.serve import Host


class HarnessNotes(unittest.TestCase):
    def notice(self, lang='en'):
        return dict(id='N'*22, type='upgrade', version='0.25.20a1', priority='normal',
                    category=None, package=None, link=None, title_en='Agent J host update',
                    title_zh='Agent J 主机更新', body_en='Common fix\n[claude] Claude status line\n[codex] Codex recovery',
                    body_zh='共同修复\n[claude] Claude 状态栏\n[codex] Codex 恢复')

    def test_legacy_universal_multi_target_and_unknown(self):
        text = 'Legacy\n[all] Universal\n[claude,codex] Shared\n[claude] Claude only\n- [codex] Codex only\n[future] Future only'
        self.assertEqual(release_notes.filter_text(text, 'codex'), 'Legacy\nUniversal\nShared\n- Codex only')
        self.assertEqual(release_notes.filter_text(text), 'Legacy\nUniversal')
        self.assertEqual(release_notes.filter_text(text, 'opencode'), 'Legacy\nUniversal')
        self.assertEqual(release_notes.filter_text(text, 'future'), 'Legacy\nUniversal')
        self.assertEqual(release_notes.KINDS, frozenset(__import__('agentj.state', fromlist=['State']).State.AGENT_KINDS))

    def test_current_uses_running_adapter_and_unknown_stays_unknown(self):
        st = Mock(agent_config=Mock(return_value={'kind':'claude'}))
        self.assertEqual(release_notes.current(st, SimpleNamespace(kind='codex')), 'codex')
        self.assertIsNone(release_notes.current(st, SimpleNamespace(kind='future')))
        self.assertEqual(release_notes.current(st), 'claude')

    def test_phone_history_and_agent_use_same_projection_in_both_languages(self):
        for lang in ('zh', 'en'):
            n = self.notice(lang)
            h = Mock(st=Mock(agent_config=Mock(return_value={'kind':'codex'})), preferences={}, lang=lang, agent=None)
            with patch.object(notices, 'local_categories', return_value=set()), patch.object(notices, 'presented', return_value=True), patch.object(notices, 'receipts', return_value=[]):
                Host.official_notices(h, [n])
            text = h.hist_add.call_args.args[1]
            body = notices.body(n, lang, 'codex')
            self.assertTrue(text.endswith(body))
            self.assertNotIn('Claude', text)
            self.assertNotIn('[codex]', text)
            encoded = notices.agent_data(n, 'ask_upgrade', lang, 'codex').split('Official notice JSON: ')[1].split('\nLocal host policy:')[0]
            self.assertEqual(json.loads(encoded)['body'], body)
            self.assertEqual(n['body_'+lang].count('[claude]'), 1)  # signed source is immutable

    def test_queue_rechecks_harness_at_delivery(self):
        n = self.notice()
        st = Mock(agent_config=Mock(return_value={'kind':'claude'}))
        h = SimpleNamespace(st=st, agent=SimpleNamespace(kind='claude'), lang='en', preferences={}, official_pending={}, stopped=lambda:False)
        send = notices.NoticeSend(h, n, 'ask_upgrade', 'en')
        self.assertIn('Claude status line', send.text)
        h.agent.kind = 'codex'  # running adapter wins over stale switch configuration
        with patch.object(notices, 'local_categories', return_value=set()), patch.object(notices, 'active', return_value=True), patch.object(notices, 'read', return_value={'items':{n['id']:{'notice':n}}}):
            self.assertTrue(asyncio.run(send.wait_ready()))
        self.assertNotIn('Claude status line', send.text)
        self.assertIn('Codex recovery', send.text)

    def test_security_keeps_common_warning_and_existing_policy(self):
        n = self.notice(); n['type']='security'; n['priority']='urgent'
        text = notices.agent_data(n, notices.policy(n, {}), 'en', 'codex')
        self.assertIn('Common fix', text)
        self.assertIn('Claude status line', text)
        self.assertIn('Upgrade immediately', text)

    def test_security_full_body_reaches_phone_prompt_and_queue(self):
        for lang in ('zh', 'en'):
            n = self.notice(); n.update(type='security', priority='urgent')
            n['body_'+lang] = '\n[claude] ' + ('安全警告' if lang == 'zh' else 'Security warning') + '\n\n- [codex] Detail\n[future] Future detail\n'
            expected = '\n' + ('安全警告' if lang == 'zh' else 'Security warning') + '\n\n- Detail\nFuture detail\n'
            for harness in ('codex', 'opencode', None, 'future'):
                self.assertEqual(notices.body(n, lang, harness), expected)
            h = Mock(st=Mock(agent_config=Mock(return_value={'kind':'claude'})), preferences={},
                     lang=lang, agent=SimpleNamespace(kind='codex', submit=Mock()),
                     official_pending={}, stopped=lambda:False)
            with patch.object(notices, 'local_categories', return_value=set()), patch.object(notices, 'presented', return_value=True), patch.object(notices, 'receipts', return_value=[]):
                Host.official_notices(h, [n])
            self.assertTrue(h.hist_add.call_args.args[1].endswith(expected))
            send = h.agent.submit.call_args.args[0]
            h.agent.kind = 'opencode'
            with patch.object(notices, 'local_categories', return_value=set()), patch.object(notices, 'active', return_value=True), patch.object(notices, 'read', return_value={'items':{n['id']:{'notice':n}}}):
                self.assertTrue(asyncio.run(send.wait_ready()))
            encoded = send.text.split('Official notice JSON: ')[1].split('\nLocal host policy:')[0]
            self.assertEqual(json.loads(encoded)['body'], expected)
            self.assertIn('[claude]', n['body_'+lang])
            self.assertIn('Upgrade immediately', send.text)

    def test_warning_in_upgrade_or_automatic_summary_fails_open(self):
        for warning in ('Security fix', 'SECURITY warning', 'Vulnerability fixed', 'Vulnerabilities fixed',
                        'CVE-2026-1234', 'Exploit fixed', 'Risk mitigated', 'Breach advisory',
                        'Warning: review settings', '安全修复', '警告：请检查', '修复可远程利用的漏洞', '降低风险'):
            prose = '[claude] ' + warning + '\n[codex] Recovery\n[future] Explanation'
            expected = warning + '\nRecovery\nExplanation'
            self.assertEqual(release_notes.filter_text(prose, 'codex'), expected)
            for lang in ('zh', 'en'):
                n = self.notice(); n['body_'+lang] = prose
                self.assertEqual(notices.body(n, lang, 'codex'), expected)
                result = dict(result='ok', to='0.25.20a1', summary=prose, summary_en=prose, when='idle')
                self.assertIn(expected, auto_update.notice(result, lang, harness='codex'))
        # English indicators match whole words, not unrelated substrings.
        self.assertEqual(release_notes.filter_text('[claude] Prewarning example\n[codex] Recovery', 'codex'), 'Recovery')

    def test_automatic_result_summaries_and_empty_fallback(self):
        for lang in ('en', 'zh'):
            r = dict(result='ok', to='0.25.20a1', summary='[claude] Claude 状态栏\n[codex] Codex 恢复',
                     summary_en='[claude] Claude status line\n[codex] Codex recovery')
            for when in ('night', 'idle'):
                r['when']=when
                text = auto_update.notice(r, lang, harness='codex')
                self.assertNotIn('Claude', text)
                self.assertIn('Codex', text)
                self.assertNotIn('[codex]', text)
                fallback = auto_update.notice(r, lang)
                self.assertNotIn('Codex', fallback)
                self.assertNotIn('Claude', fallback)

    def test_durable_auto_notice_projects_when_consumed(self):
        from agentj import __version__
        st = Mock(agent_config=Mock(return_value={'kind':'claude'}))
        st.config_lock.return_value = contextlib.nullcontext()
        result = dict(result='ok', to=__version__, summary_en='[claude] Claude status line\n[codex] Codex recovery')
        rec = dict(result=result, notice_pending=True)
        with patch.object(auto_update, 'read', return_value=rec), patch.object(auto_update, 'write') as write:
            text = auto_update.take_notice(st, 'en', harness='codex')
        self.assertIn('Codex recovery', text)
        self.assertNotIn('Claude status line', text)
        self.assertFalse(write.call_args.args[1]['notice_pending'])
        self.assertIn('[claude]', result['summary_en'])

    def test_on_ready_unknown_adapter_does_not_reuse_stale_config(self):
        from agentj import __version__, bailian, claude_inbound
        st = Mock(agent_config=Mock(return_value={'kind':'claude'}))
        st.config_lock.side_effect = contextlib.nullcontext
        session = SimpleNamespace(cid='fixture')
        h = SimpleNamespace(st=st, sessions={'fixture':session}, lang='en', preferences={},
                            agent=SimpleNamespace(kind='future'), agent_notice=Mock())
        result = dict(result='ok', to=__version__,
                      summary_en='[all] Common fix\n[claude] Claude status line')
        with patch.object(auto_update, 'read', return_value=dict(result=result, notice_pending=True)), \
             patch.object(auto_update, 'write'), patch.object(bailian, 'notify_retired'), \
             patch.object(claude_inbound, 'default_notice', side_effect=RuntimeError('stop after upgrade notice')):
            with self.assertRaisesRegex(RuntimeError, 'stop after upgrade notice'):
                asyncio.run(Host.on_ready(h, session, 0))
        text = h.agent_notice.call_args.args[0]
        self.assertIn('Common fix', text)
        self.assertNotIn('Claude', text)
        # Legacy callers that omit the argument still use the configured harness.
        with patch.object(auto_update, 'read', return_value=dict(result=result, notice_pending=True)), \
             patch.object(auto_update, 'write'):
            self.assertIn('Claude status line', auto_update.take_notice(st, 'en'))


if __name__ == '__main__':
    unittest.main()
