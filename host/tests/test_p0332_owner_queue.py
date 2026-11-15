"""0332: queue presentation follows host-owned message provenance, never text claims."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import tempfile
import unittest
from types import SimpleNamespace
from agentj import held, provenance
from agentj.serve import Host
from agentj.turn_heal import TurnHeal
import test_p424_held as fixture

OWNERS = [provenance.phone(), provenance.terminal(), provenance.wechat_owner(),
          provenance.telegram_owner(33), provenance.email_owner('owner@example.test')]
OTHERS = [provenance.system(), provenance.automation('done'), {'kind':'scheduled_task','role':'task'},
          provenance.email_guest('guest@example.test'), provenance.telegram_forward(33),
          provenance.external_mcp('A'*22), None]

class OwnerQueue(unittest.TestCase):
    def make(self, root, source):
        h=fixture.Host424(root)
        h.cur_turn=h.hist.add({'k':'phone','text':'owner text is not a trust signal'},'', 'open')['id']
        send=SimpleNamespace(source=source)
        h.agent=SimpleNamespace(cur_send=send)
        return h, send

    def test_timeout_reuses_card_without_notice_or_settings_sentence(self):
        for source in OWNERS:
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                h,send=self.make(root,source)
                tid=held.note(h,'old timeout sentence','old desktop diagnosis')
                self.assertEqual(tid,h.cur_turn)
                p=h.hist.get(tid)
                self.assertEqual(p['reply']['text'],'')
                self.assertTrue(p['card']['queued'])
                self.assertNotIn('detail',p['card'])
                self.assertEqual(h.events,[])
                self.assertEqual(h.legacy,[])
                h.turn_heal=TurnHeal(h);h.turn_heal.pages.add(tid)
                h.turn_heal.state={'busy':True,'waited_s':20}
                held.delivered(h,tid)
                h.turn_heal._refresh_pages()
                self.assertEqual(h.hist.get(tid)['reply']['text'],'')
                self.assertFalse(h.hist.get(tid)['card'].get('queued'))
                self.assertNotIn('held',h.hist.get(tid)['card'])

    def test_background_webhook_system_heartbeat_guest_never_queue_notice(self):
        for source in OTHERS+[provenance.phone()]:
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                h,send=self.make(root,source)
                if source==provenance.phone(): send.hook_notice=True
                tid=held.note(h,'old unreceived sentence','settings')
                p=h.hist.get(tid)
                self.assertEqual(p['reply']['text'],'')
                self.assertTrue(p['card'].get('queued',False))
                self.assertEqual(h.events,[])
                self.assertEqual(h.legacy,[])
                held.delivered(h,tid)
                h.hist_update(tid,text='actual reply',end='done')
                self.assertEqual(h.hist.get(tid)['reply']['text'],'actual reply')
                self.assertNotIn('held',h.hist.get(tid)['card'])

    def test_busy_queue_marker_is_internal_and_clears(self):
        for source in OWNERS+OTHERS:
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                h,send=self.make(root,source)
                Host.turn_queued_note(h,True)
                p=h.hist.get(h.cur_turn)
                self.assertTrue(p.get('card',{}).get('queued'))
                self.assertEqual(p['reply']['text'],'')
                Host.turn_queued_note(h,False)
                self.assertFalse(h.hist.get(h.cur_turn).get('card',{}).get('queued',False))

    def test_watchdog_retains_delivery_marker_without_notice(self):
        for source in OWNERS+OTHERS:
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                h,send=self.make(root,source)
                page=h.hist.get(h.cur_turn)
                page['src'].update(source_kind=provenance.normalize(source)['kind'],source_role=provenance.normalize(source)['role'])
                heal=TurnHeal(h);heal.pages.add(h.cur_turn);heal.state={'busy':True,'waited_s':20,'stuck':False}
                heal._refresh_pages()
                self.assertTrue(page.get('card',{}).get('queued'))

    def test_actual_agent_submit_projects_every_source_and_delivery_clears(self):
        import asyncio
        from agentj.agent import Agent
        from agentj.compose import Send
        for source in OWNERS+OTHERS+[provenance.phone()]:
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                h,old=self.make(root,source)
                h.queue_pending=lambda send: Host.queue_pending(h,send)
                a=SimpleNamespace(host=h,cfg={},q=asyncio.Queue())
                send=Send('fixture','A'*22,h.cur_turn,text='actual input')
                send.source=source
                if source==provenance.phone(): send.hook_notice=True
                else: send.background_done_ids=['job'];h.background_done=SimpleNamespace(complete=lambda send:None)
                Agent.submit(a,send)
                self.assertIs(a.q.get_nowait(),send)
                p=h.hist.get(send.turn)
                self.assertTrue(p['card']['queued'])
                self.assertEqual((p['src']['source_kind'],p['src']['source_role']),
                                 (provenance.normalize(source)['kind'],provenance.normalize(source)['role']))
                if getattr(send,'hook_notice',None): self.assertEqual(p['src']['via'],'hook')
                h.turn_heal=TurnHeal(h)
                h.turn_heal.pages.add(send.turn)
                h.turn_heal.state={'busy':True,'waited_s':20}
                Host.say_delivered(h,send)
                h.turn_heal._refresh_pages()
                self.assertFalse(h.hist.get(send.turn)['card'].get('queued'))
                h.hist_update(send.turn,text='actual reply',end='done')
                self.assertEqual(h.hist.get(send.turn)['reply']['text'],'actual reply')

    def test_native_timeout_without_page_never_falls_back_to_old_notice(self):
        import asyncio
        from unittest.mock import patch
        from agentj import shared,claude_inbound
        async def run():
            with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                h=fixture.Host424(root);a=fixture.Held.agent(self,h)
                send=fixture.send(h,'system turn');send.turn=None;send.source=provenance.system()
                a.cur_send=send
                with patch.object(shared,'claude_send'),patch.object(shared,'CLAUDE_INPUT_WAIT',.01),patch.object(claude_inbound,'hold_reason',return_value='default'):
                    await a.turn(send.text)
                self.assertEqual(h.notices,[])
                self.assertEqual(h.events,[])
                self.assertEqual(h.legacy,[])
        asyncio.run(run())


class ChannelPending(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_timeout_preserves_route_then_actual_reply_once(self):
        from unittest.mock import patch
        from agentj import telegram
        enrolled={'owner_id':33}
        calls=[]
        chan=SimpleNamespace(enabled=lambda:True,host=SimpleNamespace(st=None),root=None,
            turn_enrollment={1:enrolled},turn_routes={1:(33,33,'tg-owner')},
            queue_reply=lambda *args:calls.append(args) or True)
        turn={'id':1,'src':{'k':'telegram','dev':'tg-owner'},'end':'done','reply':{'text':''},'card':{'held':'pending'}}
        with patch.object(telegram,'configuration',return_value=enrolled),patch.object(telegram.tg_cursor,'outbox_set'):
            telegram.Telegram.completed(chan,turn)
            self.assertEqual(calls,[]);self.assertIn(1,chan.turn_routes)
            turn.update(card={},reply={'text':'actual reply'})
            telegram.Telegram.completed(chan,turn)
            telegram.Telegram.completed(chan,turn)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][-1],'actual reply')

    async def test_wechat_timeout_preserves_route_then_actual_reply_once(self):
        from agentj import wechat
        calls=[]
        async def answer(*args):calls.append(args)
        record={'turn':1,'state':'main'}
        chan=SimpleNamespace(closed=False,store=SimpleNamespace(data={'records':{'one':record}},save=lambda:None),
                             answer_main=answer,card_tasks=set())
        turn={'id':1,'src':{'k':'wechat'},'end':'done','reply':{'text':''},'card':{'held':'pending'}}
        wechat.WeChat.completed(chan,turn)
        self.assertEqual(calls,[]);self.assertEqual(record['state'],'main')
        turn.update(card={},reply={'text':'actual reply'})
        wechat.WeChat.completed(chan,turn);wechat.WeChat.completed(chan,turn)
        import asyncio
        await asyncio.sleep(0)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][1],'actual reply')

    async def test_mail_timeout_preserves_route_until_actual_completed_reply(self):
        from agentj import mail
        calls=[]
        chan=SimpleNamespace(host=SimpleNamespace(cur_turn=1),_start_reply_job=lambda *args:calls.append(args))
        turn={'id':1,'src':{'k':'email'},'end':'done','reply':{'text':''},'card':{'held':'pending'}}
        mail.Mail.completed(chan,turn)
        self.assertEqual(calls,[])
        turn.update(card={},reply={'text':'actual reply'})
        mail.Mail.completed(chan,turn)
        self.assertEqual(calls,[(turn,'actual reply')])
