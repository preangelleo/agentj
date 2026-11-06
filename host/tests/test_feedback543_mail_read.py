"""#543: only processing receipts change Seen; Gmail FETCH order is arbitrary."""
import _hermetic
import asyncio
import email
import email.policy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock
from agentj import mail as M
from agentj.state import State

RAW=b'From: owner@example.com\r\nTo: agent@example.com\r\nMessage-ID: <one@example.com>\r\n\r\nHello'
HEAD=b'Message-ID: <one@example.com>\r\n\r\n'
MID=M.id_hash('<one@example.com>', RAW[:2048])

class Identity(unittest.TestCase):
    def client(self, reverse=False):
        c=Mock(); c.select.return_value=('OK',[]); c.untagged_responses={'UIDVALIDITY':[b'9']}
        fields=[(b'5 (UID 5 BODY[HEADER.FIELDS (MESSAGE-ID)] {40}',HEAD),(b' BODY[]<0> {100}',RAW)]
        if reverse: fields=[(b'5 (UID 5 BODY[]<0> {100}',RAW),(b' BODY[HEADER.FIELDS (MESSAGE-ID)] {40}',HEAD)]
        c.uid.return_value=('OK',fields+[b')']); return c
    def test_header_first_positive_control(self):
        M._mail_identity(self.client(),9,5,MID,'read_gone')
    def test_gmail_body_first(self):
        M._mail_identity(self.client(True),9,5,MID,'read_gone')
    def test_gmail_body_first_without_message_id(self):
        c=self.client(True)
        raw=RAW.replace(b'Message-ID: <one@example.com>\r\n',b'')
        c.uid.return_value=('OK',[(b'5 (UID 5 BODY[]<0> {100}',raw),(b' BODY[HEADER.FIELDS (MESSAGE-ID)] {2}',b'\r\n'),b')'])
        M._mail_identity(c,9,5,M.id_hash('',raw),'read_gone')
    def test_gmail_header_message_id_beyond_body_prefix(self):
        c=self.client(True)
        c.uid.return_value=('OK',[(b'5 (UID 5 BODY[]<0> {2048}',b'X-Filler: '+b'a'*2038),(b' BODY[HEADER.FIELDS (MESSAGE-ID)] {40}',HEAD),b')'])
        M._mail_identity(c,9,5,MID,'read_gone')
    def test_invalid_identity_parts_never_authorize_store(self):
        for mutate in ('missing','duplicate','wrong_uid','wrong_epoch','wrong_id'):
            with self.subTest(mutate=mutate):
                c=self.client(True); fields=c.uid.return_value[1]
                if mutate=='missing': fields.pop(1)
                if mutate=='duplicate': fields.append(fields[0])
                if mutate=='wrong_uid': fields[0]=(fields[0][0].replace(b'UID 5',b'UID 6'),fields[0][1])
                if mutate=='wrong_epoch': c.untagged_responses={'UIDVALIDITY':[b'10']}
                with self.assertRaises(M.MailError):
                    M._mail_identity(c,9,5,'wrong' if mutate=='wrong_id' else MID,'read_gone')

class Processing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=State(Path(self.tmp.name)); self.st.init()
        self.m=M.Mail(SimpleNamespace(st=self.st,agent=object(),stopped=lambda:False,lang='en'))
        self.cfg=M.validate({**M.blank_config(),'enabled':True,'auth':'off','address':'agent@example.com','imap':{'host':'imap.example.com','port':993,'security':'ssl'},'smtp':{'host':'smtp.example.com','port':465,'security':'ssl'},'allow':[{'addr':'owner@example.com','role':'owner'}]})
        M.save_config(self.st,self.cfg); M.save_state(self.st,{**M.read_state(self.st),'uidvalidity':9})
    async def test_owner_no_action_archive_marks_seen(self):
        with patch.object(M,'owner_request',return_value='thread'), patch.object(self.m,'password',return_value='synthetic'), patch.object(M,'mark_read',return_value=True) as mark:
            self.assertEqual(await self.m.one(self.cfg,5,len(RAW),RAW),'archive'); mark.assert_called_once()
    async def test_failure_unread_and_manual_unread_not_redelivered(self):
        with patch.object(self.m,'password',return_value='synthetic'),patch.object(M,'mark_read',return_value=True) as mark,patch.object(self.m,'deliver',new=AsyncMock(return_value=False)):
            self.assertEqual(await self.m.one(self.cfg,5,len(RAW),RAW),'not_delivered');mark.assert_not_called()
        good=RAW.replace(b'<one',b'<two')
        with patch.object(self.m,'password',return_value='synthetic'),patch.object(M,'mark_read',return_value=True) as mark,patch.object(self.m,'deliver',new=AsyncMock(return_value=True)) as delivery:
            self.assertEqual(await self.m.one(self.cfg,6,len(good),good),'delivered')
            self.assertEqual(await self.m.one(self.cfg,6,len(good),good),'duplicate')
            delivery.assert_awaited_once();mark.assert_not_called()

from test_p170_mail import _Base
from mailfake import mail
from agentj import mail_read as R

class Lifecycle(_Base):
    async def test_pending_then_silent_seen_and_manual_unread_stays_unread(self):
        uid=self.imap.add(mail(msgid='<silent543@corp.example>'))
        self.assertEqual((await self.m.check())['delivered'],1)
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
        await self.finish(101,'')
        self.assertIn('\\Seen',self.imap.flags.get(uid,set()))
        self.imap.flags[uid].discard('\\Seen')
        await self.m.check()
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
        self.assertEqual(len(self.delivered()),1)
    async def test_pending_then_sent_seen(self):
        uid=self.imap.add(mail(msgid='<sent543@corp.example>'))
        await self.m.check(); await self.finish(101,'Done.')
        self.assertEqual(len(self.smtp.sent),1)
        self.assertIn('\\Seen',self.imap.flags.get(uid,set()))
    async def test_no_password_reply_stays_unread(self):
        uid=self.imap.add(mail(msgid='<failed543@corp.example>'))
        await self.m.check()
        with patch.object(self.m,'password',return_value=None):
            self.assertEqual(await self.real_reply(101,'Done.'),'no_password')
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
    async def test_completed_store_failure_retries_without_redelivery(self):
        uid=self.imap.add(mail(msgid='<retry543@corp.example>'))
        await self.m.check()
        with patch.object(M,'mark_read',side_effect=M.MailError('read_failed')):
            await self.finish(101,'')
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
        await self.m.check()
        self.assertIn('\\Seen',self.imap.flags.get(uid,set()))
        self.assertEqual(len(self.delivered()),1)
    async def test_historical_only_conclusive_notice_receipts_once(self):
        good=self.imap.add(mail(msgid='<historical543@corp.example>'))
        other=self.imap.add(mail(msgid='<ambiguous543@corp.example>'))
        s=M.read_state(self.st)
        mids=[M.id_hash('<historical543@corp.example>'), M.id_hash('<ambiguous543@corp.example>')]
        s['seen']=mids;s['last_uid']=other;M.save_state(self.st,s)
        self.st.log_path.write_text(__import__('json').dumps({'ev':'mail_baseline','count':1,'ts':__import__('time').time()})+'\n'+__import__('json').dumps({'ev':'mail_notice','id':mids[0]})+'\n')
        cfg=M.read_config(self.st);pw=self.m.password(cfg)
        await self.m.reconcile_read(cfg,pw)
        self.assertIn('\\Seen',self.imap.flags.get(good,set()))
        self.assertNotIn('\\Seen',self.imap.flags.get(other,set()))
        self.imap.flags[good].discard('\\Seen')
        await self.m.reconcile_read(cfg,pw)
        self.assertNotIn('\\Seen',self.imap.flags.get(good,set()))
    async def test_old_account_epoch_and_false_legacy_toggle(self):
        cfg=M.read_config(self.st);cfg['mark_processed_read']=False;M.save_config(self.st,cfg)
        uid=self.imap.add(mail(msgid='<compat543@corp.example>'))
        await self.m._mark_processed(cfg,self.imap.uidvalidity+1,uid,M.id_hash('<compat543@corp.example>'))
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
        await self.m._mark_processed(cfg,self.imap.uidvalidity,uid,M.id_hash('<compat543@corp.example>'))
        self.assertIn('\\Seen',self.imap.flags.get(uid,set()))

    async def test_concurrent_completions_keep_all_acknowledgments(self):
        import time
        cfg=M.read_config(self.st)
        def delayed(*args):
            time.sleep(.02)
            return True
        with patch.object(M,'mark_read',side_effect=delayed) as mark:
            await asyncio.gather(*(self.m._mark_processed(cfg,self.imap.uidvalidity,uid,M.id_hash('<parallel%d@corp.example>'%uid)) for uid in (55,56)))
            await self.m._mark_processed(cfg,self.imap.uidvalidity,55,M.id_hash('<parallel55@corp.example>'))
        rows=R.load(self.m,cfg)['rows']
        self.assertTrue(all(r['acked'] for r in rows.values()))
        self.assertEqual(mark.call_count,2)
    async def test_legacy_account_change_excludes_old_notice(self):
        import json
        cfg=M.read_config(self.st)
        uid=self.imap.add(mail(msgid='<old-account543@corp.example>'))
        mid=M.id_hash('<old-account543@corp.example>')
        s=M.read_state(self.st);s['seen']=[mid];s['last_uid']=uid;M.save_state(self.st,s)
        self.st.log_path.write_text(json.dumps({'ev':'mail_baseline','ts':1})+'\n'+json.dumps({'ev':'mail_notice','id':mid})+'\n')
        await self.m.reconcile_read(cfg,self.m.password(cfg))
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
    async def test_old_generation_silent_reply_never_marks_new_account(self):
        uid=self.imap.add(mail(msgid='<old-route543@corp.example>'))
        await self.m.check()
        cfg=M.read_config(self.st);cfg['generation']='new-generation';M.save_config(self.st,cfg)
        await self.finish(101,'')
        self.assertNotIn('\\Seen',self.imap.flags.get(uid,set()))
