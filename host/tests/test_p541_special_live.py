"""Real Host/card/controller handoffs with synthetic authority changes and no effects."""
import _hermetic
import asyncio
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from agentj import approval_special,approval_review,elevate,serve,wire
import test_p541_special as fixture
intent=fixture.intent
from test_f17 import answer
from test_p73_secret import fa_out

class Live(unittest.TestCase):
    setUp=fixture.Special.setUp
    messages=fixture.Special.messages
    pickup=fixture.Special.pickup
    wait_card=fixture.Special.wait_card

    def mutate(self,kind):
        if kind=='agent':self.host.agent=SimpleNamespace()
        elif kind=='task':self.host.task_epoch='new-task'
        else:
            self.host.agent.phone_turn=True
            self.host.agent.cur_send=SimpleNamespace(approval_origin=None)

    async def stale_pickup(self,kind):
        card=await self.pickup()
        c=self.host.elevate.out.cards[card['id']]
        self.assertTrue(approval_special.source_allows(c,self.host))
        self.mutate(kind)
        self.assertFalse(approval_special.source_allows(c,self.host))
        await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],'fa':fa_out(self.a.auth,self.a,card)})
        self.assertFalse(self.messages('secret_out_val'))
        self.host.elevate.out.end_all('stopped')
        await asyncio.sleep(.01)

    def test_same_epoch_new_agent_refused(self):asyncio.run(self.stale_pickup('agent'))
    def test_same_epoch_new_task_refused(self):asyncio.run(self.stale_pickup('task'))
    def test_same_epoch_unknown_source_refused(self):asyncio.run(self.stale_pickup('source'))

    def test_frozen_request_context_cannot_hide_live_source_change(self):
        async def run():
            card=await self.pickup();c=self.host.elevate.out.cards[card['id']]
            with approval_review.origin_scope(c['review_adapter'].review_origin):
                self.mutate('source')
                self.assertFalse(approval_special.source_allows(c,self.host))
            self.host.elevate.out.end_all('stopped');await asyncio.sleep(.01)
        asyncio.run(run())

    def test_exact_host_question_continuation_and_forged_metadata(self):
        async def run():
            card=await self.pickup();c=self.host.elevate.out.cards[card['id']]
            await self.host._app(self.sa,intent(self.a,card,'question'))
            send=self.events[-1]
            self.host.agent.cur_send=send;self.host.task_epoch='review-task'
            self.assertTrue(approval_special.source_allows(c,self.host))
            old=copy.deepcopy(send.approval_review)
            send.approval_review['event_id']='forged-event'
            self.assertFalse(approval_special.source_allows(c,self.host))
            send.approval_review=old
            fresh=self.messages('approval_review_result')[-1]['ask']
            await self.host._app(self.sa,intent(self.a,fresh,'reopen'))
            self.assertEqual(c['review_state'],'pending')
            self.host.elevate.out.end_all('stopped');await asyncio.sleep(.01)
        asyncio.run(run())

    def test_secret_wire_lock_rechecks_before_encrypt(self):
        async def run():
            card=await self.pickup();c=self.host.elevate.out.cards[card['id']]
            self.sa.send=SimpleNamespace(encrypt=Mock(return_value=b'synthetic-cipher'))
            self.host.send_app=serve.Host.send_app.__get__(self.host)
            self.host._op=AsyncMock()
            await self.host.send_lock.acquire()
            task=asyncio.create_task(self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],'fa':fa_out(self.a.auth,self.a,card)}))
            for _ in range(100):
                if c.get('busy'):break
                await asyncio.sleep(.001)
            self.assertTrue(c.get('busy'))
            self.mutate('task');self.host.send_lock.release()
            await asyncio.wait_for(task,2)
            kinds=[wire.unpad_json(call.args[1],wire.MAX_JSON_P33).get('t') for call in self.sa.send.encrypt.call_args_list]
            self.assertNotIn('secret_out_val',kinds)
            self.assertEqual(c['review_state'],'unknown')
            self.assertEqual(bytes(c['value']),b'')
        asyncio.run(run())

    def key_validation_race(self,change):
        async def run():
            from pathlib import Path
            target=Path(self.tmp.name)/'synthetic-target'
            spec=elevate.norm_secret({'name':'TEST_ONLY','purpose':'Synthetic','dest':str(target)},self.st.root)
            caller=asyncio.create_task(self.host.elevate.request(spec))
            card=await self.wait_card('elev')
            entered,release=threading.Event(),threading.Event()
            original=self.host.elevate._save
            def save(c,value):
                entered.set()
                if not release.wait(2):raise RuntimeError('fixture timeout')
                return original(c,value)
            with patch.object(self.host.elevate,'_save',side_effect=save):
                task=asyncio.create_task(self.host._app(self.sa,answer(self.a,card,True,'synthetic-value')))
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                change();release.set()
                await asyncio.wait_for(task,2)
                result=await asyncio.wait_for(caller,2)
            self.assertEqual(result['result'],'unknown')
            self.assertFalse(target.exists())
            await self.host.elevate.stop()
        asyncio.run(run())

    def test_key_validation_wait_cannot_write_after_new_task(self):
        self.key_validation_race(lambda:self.mutate('task'))

    def test_key_validation_wait_cannot_write_after_signing_key_rotation(self):
        replacement=patch.object(self.st,'sign_key',return_value=b'R'*32)
        self.addCleanup(replacement.stop)
        self.key_validation_race(replacement.start)
