"""Owner draw completion: real Mail/Host/IMAP/SMTP, fake image provider only."""
import _hermetic
import asyncio
import copy
import email
import email.policy
from unittest import mock
from unittest.mock import AsyncMock
import test_p562_acceptance as acceptance
from agentj import mail, channel_native, application_ingress
from agentj.peer_session import TurnResult


ASK_336, PNG = acceptance.ASK_336, acceptance.PNG


class DrawCompletion(acceptance._Real):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from mailfake import FakeIMAP, FakeSMTP
        from agentj.external_channels import ExternalChannels
        self.M = mail
        self.agent_addr, self.code, self.boss = 'agent@example.cn', 'fixture-password', 'boss@corp.example'
        self.imap, self.smtp = FakeIMAP(self.agent_addr, self.code), FakeSMTP(self.agent_addr, self.code)
        self.addCleanup(self.imap.close); self.addCleanup(self.smtp.close)
        self.host.external_channels = ExternalChannels(self.host)
        self.addAsyncCleanup(self.host.external_channels.stop)
        self.host.mail = mail.Mail(self.host)
        cfg = mail.blank_config()
        cfg.update(enabled=True, address=self.agent_addr, allow=[{'addr':self.boss,'role':'owner'}],
                   imap={'host':'127.0.0.1','port':self.imap.port,'security':'plain'},
                   smtp={'host':'127.0.0.1','port':self.smtp.port,'security':'plain'})
        self.host.mail.set_config(cfg)
        mail.store_secret(self.st, bytearray(self.code.encode()), self.agent_addr)
        await self.host.mail.check()
        self.n = 0

    deliver = acceptance.MailAcceptance.deliver
    mails = acceptance.MailAcceptance.mails

    async def draw(self, mutation=None):
        result = TurnResult('reply', 'Image ready.', 'image', None, None)
        result.images = [('image/png', PNG)]
        opened = mail.smtp_open
        def boundary(cfg, pw):
            client = opened(cfg, pw)
            if mutation:
                mutation(next(iter(self.host.mail._reply_jobs.values())))
            return client
        with mock.patch.object(channel_native, 'draw_models', return_value=[None]), \
                mock.patch.object(channel_native.NativeCodex, 'turn', AsyncMock(return_value=result)), \
                mock.patch.object(mail, 'smtp_open', side_effect=boundary):
            self.assertTrue(await self.deliver(ASK_336))
        await asyncio.sleep(0)

    async def test_real_draw_sends_exact_png_once_and_has_no_generic_authority(self):
        await self.draw()
        self.assertEqual(len(self.smtp.sent), 1)
        [message] = self.mails()
        self.assertEqual(str(message['To']), self.boss)
        [attachment] = list(message.iter_attachments())
        self.assertEqual(attachment.get_content_type(), 'image/png')
        self.assertEqual(attachment.get_payload(decode=True), PNG)
        self.assertFalse(self.sends())
        self.assertFalse(self.cards())
        self.assertFalse(self.host.mail._reply_jobs)
        self.assertFalse(application_ingress.allowed(self.host))
        self.assertEqual((await self.host.ask('agentj mail send', {'to':[self.boss]}))['reason'],
                         'application_ingress_unverified')
        self.assertTrue(await self.host.mail.check())
        self.assertEqual(len(self.smtp.sent), 1)
        turn = self.host.hist.get(max(self.host.hist.turns))
        self.host.mail.completed(turn)
        await asyncio.gather(*list(self.host.mail.sending))
        self.assertEqual(len(self.smtp.sent), 1)

    async def test_draw_payload_and_subject_revocation_after_login_sends_zero_data(self):
        for mode in ('images','files','result_reply','result_turn','config','route','epoch','history','body','stop'):
            with self.subTest(mode=mode):
                def mutate(job):
                    if mode == 'images': job.text.images = (('image/png', PNG+b'changed'),)
                    elif mode == 'files': job.text.files = (('changed.txt','text/plain',b'changed'),)
                    elif mode == 'result_reply': job.drawn.reply = str(job.text)
                    elif mode == 'result_turn': job.drawn.turn += 1
                    elif mode == 'config': self.host.mail.set_config({**mail.read_config(self.st),'reply_per_hour':7})
                    elif mode == 'route':
                        state = mail.read_state(self.st)
                        state['routes'][str(job.tid)]['to'] = 'changed@example.test'
                        mail.save_state(self.st,state)
                    elif mode == 'epoch': self.host.hist.epoch += 1
                    elif mode == 'history': self.host.hist.turns[job.tid] = copy.deepcopy(job.turn)
                    elif mode == 'body': job.turn['reply']['text'] = 'changed'
                    else: self.host.stopping.set()
                await self.draw(mutate)
                self.assertFalse(self.smtp.sent)
                self.host.stopping.clear()

    async def test_mutated_draw_result_before_target_binding_sends_zero_data(self):
        original = self.host.mail._files
        def files(*args):
            job = next(iter(self.host.mail._reply_jobs.values()))
            job.text.images = (('image/png', PNG+b'changed'),)
            return original(*args)
        with mock.patch.object(self.host.mail, '_files', side_effect=files):
            await self.draw()
        self.assertFalse(self.smtp.sent)
