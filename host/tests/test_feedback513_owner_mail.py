"""#513: real polling/dispatch seam, local IMAP/SMTP; no model or external mail."""
import _hermetic  # noqa: F401
import email
import email.policy
from unittest.mock import patch
from test_p170_mail import _Base, AGENT, BOSS
from mailfake import mail
from agentj import mail as M

class OwnerIntent(_Base):
    async def poll(self, **kw):
        self.imap.add(mail(**kw))
        return await self.m.check()

    async def test_external_to_self_cc_and_command_body_is_archive_only(self):
        res = await self.poll(to='artist@outside.example', extra=f'Cc: {AGENT}\n', body='请画一幅插画。', msgid='<external@corp.example>')
        self.assertEqual(res['delivered'], 0)
        self.assertEqual(self.delivered(), [])
        self.assertEqual(self.notes, [])
        self.assertEqual(self.smtp.sent, [])
        self.assertEqual(M.read_state(self.st)['counts'].get('archive'), 1)
        self.assertEqual(M.read_state(self.st)['routes'], {})
        self.assertIn('\\Seen', self.imap.flags.get(2, set()))

    async def test_agent_to_with_external_cc_is_archive_only(self):
        self.assertEqual((await self.poll(extra='Cc: collaborator@outside.example\n'))['delivered'], 0)
        self.assertEqual(self.delivered(), [])

    async def test_unknown_material_thread_even_only_agent_to_is_archive(self):
        self.assertEqual((await self.poll(subject='Re: 素材邮件', extra='In-Reply-To: <material@outside.example>\nReferences: <material@outside.example>\n'))['delivered'], 0)
        self.assertEqual(self.delivered(), [])

    async def test_missing_or_malformed_or_resent_recipient_is_archive(self):
        variants = [mail().replace(f'To: {AGENT}\n', ''), mail(to=f'{AGENT}, invalid'),
                    mail(extra=f'Resent-To: {AGENT}\n'), mail(extra=f'To: {AGENT}\n'),
                    mail(extra='Bcc: collaborator@outside.example\n')]
        for raw in variants:
            self.imap.add(raw=raw.encode())
            self.assertEqual((await self.m.check())['delivered'], 0)
        self.assertEqual(self.delivered(), [])

    async def test_direct_owner_mail_positive_and_known_thread_positive(self):
        self.assertEqual((await self.poll(msgid='<direct@corp.example>'))['delivered'], 1)
        await self.finish(101, '已处理。')
        self.assertEqual(len(self.smtp.sent), 1)
        reply = email.message_from_bytes(self.smtp.sent[0][2], policy=email.policy.default)
        mid = str(reply['Message-ID'])
        self.assertEqual((await self.poll(msgid='<followup@corp.example>', subject='Re: 请整理周报',
            extra=f'In-Reply-To: {mid}\nReferences: <direct@corp.example> {mid}\n'))['delivered'], 1)
        self.assertEqual(len(self.delivered()), 2)

    async def test_notes_sent_id_does_not_authorize_material_thread(self):
        s = M.read_state(self.st); s['sent'].append('<material@corp.example>'); M.save_state(self.st, s)
        self.assertEqual((await self.poll(extra='In-Reply-To: <material@corp.example>\n'))['delivered'], 0)

    async def test_reply_subject_without_thread_is_archive(self):
        self.assertEqual((await self.poll(subject='Re: 画图素材'))['delivered'], 0)

    async def test_unknown_ancestor_cannot_piggyback_known_thread(self):
        await self.poll(msgid='<direct@corp.example>')
        self.assertEqual((await self.poll(msgid='<mixed@corp.example>', extra='In-Reply-To: <direct@corp.example>\nReferences: <external@outside.example> <direct@corp.example>\n'))['delivered'], 0)

    async def test_config_generation_change_invalidates_known_thread(self):
        await self.poll(msgid='<direct@corp.example>')
        cfg = M.read_config(self.st); cfg['generation'] = 'changed'; M.save_config(self.st, cfg)
        self.assertEqual((await self.poll(msgid='<later@corp.example>', extra='In-Reply-To: <direct@corp.example>\n'))['delivered'], 0)

    async def test_old_owner_reply_route_does_not_send_after_upgrade(self):
        s = M.read_state(self.st)
        s['routes']['513'] = dict(to=BOSS, role='owner', verified=True, subject='External thread', msgid='<external@corp.example>', refs='', gen=M.read_config(self.st)['generation'], state='awaiting')
        M.save_state(self.st, s)
        self.assertEqual(await self.real_reply(513, '自动处理的结果'), 'archive')
        self.assertEqual(self.smtp.sent, [])

    async def test_dispatch_rechecks_intent_before_attachment_or_drawer(self):
        cfg = M.read_config(self.st)
        msg = email.message_from_string(mail(to='artist@outside.example'), policy=email.policy.default)
        v = M.judge(msg, cfg, []); v.id = M.id_hash(M.msg_id(msg))
        with patch('agentj.owner_channels.mail_draw', side_effect=AssertionError('must not reach drawer')):
            self.assertFalse(await self.m.deliver(cfg, msg, v))
        self.assertEqual(self.delivered(), [])

    async def test_old_unverified_owner_route_demoted_to_guest_still_archives(self):
        s = M.read_state(self.st)
        s['routes']['513'] = dict(to=BOSS, role='guest', verified=False, subject='External thread',
            msgid='<external@corp.example>', refs='', gen=M.read_config(self.st)['generation'], state='awaiting')
        M.save_state(self.st, s)
        self.assertEqual(await self.real_reply(513, '自动处理的结果'), 'archive')
        self.assertEqual(self.smtp.sent, [])

    async def test_recipient_change_during_attachment_processing_rechecks_before_draw(self):
        cfg = M.read_config(self.st)
        msg = email.message_from_string(mail(), policy=email.policy.default)
        v = M.judge(msg, cfg, []); v.id = M.id_hash(M.msg_id(msg))
        original = M.body_text
        def changed(message):
            new = dict(cfg, address='changed@example.cn'); M.save_config(self.st, new)
            return original(message)
        with patch.object(M, 'body_text', side_effect=changed), patch('agentj.owner_channels.mail_draw', side_effect=AssertionError('must not draw')):
            self.assertFalse(await self.m.deliver(cfg, msg, v))
        self.assertEqual(self.delivered(), [])
