"""Internal cancellation/timeout must never become a cached owner refusal."""
import _hermetic
import asyncio,unittest
from agentj import shared
import test_shared_hooks as fixtures

class RefusalIntent(unittest.IsolatedAsyncioTestCase):
    async def fixture(self,reasons):
        f=fixtures.ClaudeHooks();await f.asyncSetUp();self.addCleanup(f.tmp.cleanup)
        sequence=iter(reasons)
        async def ask(*args,**kwargs):
            reason=next(sequence);receipt=kwargs.get('_receipt')
            if isinstance(receipt,dict):receipt['reason']=reason
            return {'behavior':'allow'}if reason=='allowed'else{'behavior':'deny','message':'fixture host refusal'}
        f.host.ask.side_effect=ask
        return f
    async def request(self,f,path,child=None):
        inp={'file_path':path}
        if child:
            f.a.tool_agent[shared.card_digest('Read',inp)]=child
            f.a.sub_agents[child]=('worker','synthetic')
        await f.event('PreToolUse',tool='Read',inp=inp)
        return await f.event('PermissionRequest',tool='Read',inp=inp)
    async def test_timeout_cancel_and_internal_refusals_do_not_poison_next_card(self):
        for reason in ('timeout','agent_gone','superseded','serve_stop','estop','unknown'):
            with self.subTest(reason=reason):
                f=await self.fixture([reason,'allowed'])
                first=await self.request(f,'first.pem');self.assertEqual(first['hookSpecificOutput']['decision']['behavior'],'deny')
                second=await self.request(f,'second.pem')
                self.assertEqual(f.host.ask.await_count,2)
                self.assertEqual(second['hookSpecificOutput']['decision']['behavior'],'allow')
    async def test_genuine_owner_denial_keeps_same_actor_category_refusal(self):
        f=await self.fixture(['device'])
        await self.request(f,'first.pem');second=await self.request(f,'second.pem')
        self.assertEqual(f.host.ask.await_count,1);self.assertEqual(second['hookSpecificOutput']['decision']['behavior'],'deny')
    async def test_child_owner_refusal_never_blocks_main_or_other_child(self):
        f=await self.fixture(['device','allowed','allowed'])
        await self.request(f,'first.pem','child-a')
        await self.request(f,'second.pem')
        await self.request(f,'third.pem','child-b')
        self.assertEqual(f.host.ask.await_count,3)
    async def test_stopped_child_digest_does_not_carry_denial_to_identical_main_command(self):
        f=await self.fixture(['device','allowed'])
        await self.request(f,'first.pem','child-a')
        f.a._note_agent('SubagentStop',{'agent_id':'child-a'})
        result=await self.request(f,'first.pem')
        self.assertEqual(f.host.ask.await_count,2)
        self.assertEqual(result['hookSpecificOutput']['decision']['behavior'],'allow')

    async def test_native_subagent_stop_withdraws_card_without_denial_cache(self):
        f=await self.fixture([]);entered=asyncio.Event()
        async def wait(*args,**kwargs):
            entered.set();await kwargs['gone']
            if isinstance(kwargs.get('_receipt'),dict):kwargs['_receipt']['reason']='agent_gone'
            return {'behavior':'deny'}
        f.host.ask.side_effect=wait
        pending=asyncio.create_task(self.request(f,'first.pem','child-a'))
        await entered.wait();f.a._withdraw_answered('SubagentStop',{'agent_id':'child-a'})
        self.assertEqual(await pending,{})
        self.assertEqual(f.a.risk_denied,set())


class RiskCache(unittest.IsolatedAsyncioTestCase):
    async def test_native_risk_guard_only_caches_confirmed_owner_denial(self):
        from types import SimpleNamespace as NS
        from unittest.mock import Mock,AsyncMock
        from agentj.shared_risk import RiskGuard
        for why in ('timeout','agent_gone','serve_stop','unknown','device'):
            host=Mock();host.lang='en';host.stopped.return_value=False;host.st.sign_key.return_value='fixture'
            adapter=NS(host=host,cfg={'dir':'/tmp'},kind='codex')
            async def ask(*args,**kwargs):
                kwargs['_receipt']['reason']=why
                return {'behavior':'deny'}
            host.ask=AsyncMock(side_effect=ask)
            guard=RiskGuard(adapter)
            for _ in range(2):self.assertFalse(await guard.check('Read',{'file_path':'~/.ssh/id_rsa'}))
            self.assertEqual(host.ask.await_count,1 if why=='device'else 2,why)

class HostReceipt(unittest.IsolatedAsyncioTestCase):
    async def test_actual_host_receipt_distinguishes_timeout_from_cancel(self):
        import json
        import test_0280_ordinary_maintenance as setup
        f=setup.Maintenance();await f.asyncSetUp();f._asyncioRunner=self._asyncioRunner;self.addCleanup(f.doCleanups)
        # 0.30.7 ADR-0307-offlist: the subject is the card's timeout/cancel receipt; keep Bash a listed (tap) card.
        from _listed_cards import listed;listed(self,'Bash')
        for cancelled in (False,True):
            receipt={};gone=asyncio.get_running_loop().create_future() if cancelled else None
            task=asyncio.create_task(f.host.ask('Bash',{'command':'stripe charges create --amount 100'},gone=gone,_receipt=receipt))
            if cancelled:
                for _ in range(100):
                    if f.host.asks:break
                    await asyncio.sleep(.001)
                self.assertTrue(f.host.asks);gone.set_result(True)
            result=await asyncio.wait_for(task,1)
            self.assertEqual(result['behavior'],'deny')
            self.assertEqual(receipt['reason'],'agent_gone' if cancelled else 'timeout')
