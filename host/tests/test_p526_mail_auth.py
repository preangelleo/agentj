"""P526: human mail settings still bind the complete P513 redacted request."""
import _hermetic  # noqa: F401
import hashlib
import json
import unittest
from agentj import action_policy, mail


class Presentation(unittest.TestCase):
    def test_only_custom_password_cards_include_servers(self):
        for preset in mail.PRESETS:
            cfg = mail.apply_preset(mail.blank_config(), preset, 'demo@example.com')
            for lang in ('zh', 'en'):
                purpose = mail.password_purpose(cfg, lang)
                self.assertEqual('IMAP:' in purpose, preset == 'custom')
                self.assertEqual('SMTP:' in purpose, preset == 'custom')
                self.assertIn(cfg['address'], purpose)

    def test_changed_fields_localized_and_full_request_retained(self):
        before = mail.apply_preset(mail.blank_config(), 'gmail', 'demo@gmail.com')
        for lang, heading, poll in [('zh', '保存邮件通道设置', '检查频率'), ('en', 'Save mail channel settings', 'Check interval')]:
            target = {'kind': 'mail_set', 'before': before, 'cfg': {'poll_minutes': 10}}
            shown = action_policy.summary('host_write', target, lang=lang, presentation=True)
            self.assertIn(heading, shown['summary'])
            self.assertIn(poll, shown['summary'])
            self.assertIn('10', shown['summary'])
            self.assertNotIn('generation', shown['summary'])
            self.assertNotIn('SHA', shown['summary'])
            self.assertNotIn('IMAP', shown['summary'])
            raw = json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            self.assertIn(hashlib.sha256(raw.encode()).hexdigest(), shown['details'])
            self.assertIn('generation', shown['details'])
            self.assertIn('demo@gmail.com', shown['details'])

    def test_all_settings_have_human_labels_and_redaction_is_shared(self):
        before = mail.apply_preset(mail.blank_config(), 'gmail', 'demo@gmail.com')
        cfg = {'enabled': True, 'auth': 'off', 'auth_servid': 'mx.example.com', 'reply_per_hour': 7,
               'allow': [{'addr': 'guest@example.com', 'role': 'guest'}], 'username': 'other@example.com'}
        shown = action_policy.summary('host_write', {'kind': 'mail_set', 'before': before, 'cfg': cfg}, lang='zh', presentation=True)
        for text in ['启用', '发件人校验', '关闭', '可信邮件服务器', '回复上限', '访客', '登录账号']:
            self.assertIn(text, shown['summary'])
        shown = action_policy.summary('host_write', {'kind': 'mail_test', 'before': before, 'password': 'synthetic-hidden-value'}, lang='zh', presentation=True)
        self.assertIn('测试邮件连接', shown['summary'])
        self.assertIn('demo@gmail.com', shown['summary'])
        self.assertIn('imap.gmail.com', shown['summary'])
        self.assertNotIn('synthetic-hidden-value', json.dumps(shown))

    def test_effective_provider_servers_and_username_are_visible(self):
        before = mail.apply_preset(mail.blank_config(), 'qq', 'demo@qq.com')
        shown = action_policy.summary('host_write', {'kind':'mail_set', 'before':before,
            'cfg':{'preset':'gmail', 'address':'demo@gmail.com'}}, lang='zh', presentation=True)
        for value in ['Gmail', 'demo@gmail.com']:
            self.assertIn(value, shown['summary'])
        self.assertNotIn('IMAP', shown['summary'])
        self.assertNotIn('登录账号', shown['summary'])
        self.assertIn('imap.qq.com', shown['details'])
        self.assertIn('gmail', shown['details'])
        self.assertNotIn('generation', shown['summary'])


    def test_custom_server_changes_remain_explicit_and_oversize_refuses(self):
        before = mail.apply_preset(mail.blank_config(), 'custom', 'demo@example.com')
        before['imap']['host'] = 'imap.example.com'
        before['smtp']['host'] = 'smtp.example.com'
        target = {'kind':'mail_set', 'before':before, 'cfg':{'smtp':{'host':'other.example.com', 'port':587, 'security':'starttls'}}}
        shown = action_policy.summary('host_write', target, presentation=True)
        self.assertIn('other.example.com:587 (starttls)', shown['summary'])
        self.assertNotIn('IMAP', shown['summary'])
        with self.assertRaises(ValueError):
            action_policy.summary('host_write', {**target, 'extra':'x'*24000}, presentation=True)


from test_p503_policy import HostExecution, MailPhoneUV
from unittest.mock import patch
import asyncio


class PhoneFlow(HostExecution):
    async def test_prepare_display_and_signed_execution_bind_same_target(self):
        self.h.lang = 'zh'
        cfg = {'preset':'gmail', 'address':'demo@gmail.com', 'poll_minutes':10}
        await self.h._app(self.session, {'t':'policy_prepare', 'r':'c'*16, 'action':'host_write',
                                         'target':{'kind':'mail_set', 'cfg':cfg}})
        prepared = self.sent[-1][1]
        self.assertEqual(prepared['t'], 'policy_ready')
        self.assertIn('保存邮件通道设置', prepared['summary'])
        self.assertNotIn('generation', prepared['summary'])
        self.assertIn('Request SHA-256', prepared['details'])
        auth = MailPhoneUV.authorize(self, 'mail_set', cfg)
        await self.h.on_mail(self.session, 'mail_set', {'r':'d'*16, 'cfg':cfg, 'authorization':auth})
        self.assertTrue(self.sent[-1][1]['ok'])
        self.assertEqual(mail.read_config(self.st)['smtp']['host'], 'smtp.gmail.com')
        changed = {**cfg, 'poll_minutes':11}
        await self.h.on_mail(self.session, 'mail_set', {'r':'e'*16, 'cfg':changed, 'authorization':auth})
        self.assertFalse(self.sent[-1][1]['ok'])
        self.assertEqual(mail.read_config(self.st)['poll_minutes'], 10)

    async def test_explicit_phone_key_always_opens_card_and_tests_real_local_servers(self):
        from mailfake import FakeIMAP, FakeSMTP
        password = 'abcdefgh' * 2
        imap, smtp = FakeIMAP('demo@gmail.com', password), FakeSMTP('demo@gmail.com', password)
        self.addCleanup(imap.close); self.addCleanup(smtp.close)
        cfg = mail.blank_config()
        cfg.update(preset='custom', address='demo@gmail.com', username='demo@gmail.com',
                   imap={'host':'127.0.0.1', 'port':imap.port, 'security':'plain'},
                   smtp={'host':'127.0.0.1', 'port':smtp.port, 'security':'plain'})
        self.h.mail.set_config(cfg)
        for good in (True, False):
            seen = []
            async def request(card):
                seen.append(card)
                self.assertIn('16', card['purpose'])
                entered = 'abcd efgh abcd efgh' if good else 'xxxx xxxx xxxx xxxx'
                return await asyncio.to_thread(card['sink'], bytearray(entered.encode()))
            with patch.object(self.h.elevate, 'request', side_effect=request), patch.object(mail.mail_recovery, 'recover') as recovery:
                self.sent.clear()
                await self.h.on_mail(self.session, 'mail_key', {'r':'f'*16})
                for _ in range(300):
                    if any(m.get('t') == 'mail_res' for _, m in self.sent): break
                    await asyncio.sleep(.01)
                recovery.assert_not_called()
            res = self.sent[-1][1]
            self.assertEqual(len(seen), 1)
            self.assertTrue(res['ok'])
            self.assertEqual(res['verify'], 'ok' if good else 'fail')
            self.assertFalse(smtp.sent)
            expected = password if good else 'x'*16
            self.assertEqual(mail.load_secret(self.st, cfg['address']), expected)
            self.assertNotIn(expected.encode(), (self.st.root / mail.DIR / mail.SECRET).read_bytes())
