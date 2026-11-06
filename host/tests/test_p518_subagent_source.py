"""Shared native sub-agents retain only their authenticated launch turn's source."""
import _hermetic
import asyncio
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, Mock
from agentj import provenance, shared
from agentj.compose import Send

class Sources(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = N(st=Mock(), stopped=lambda:False, lang='en', ask=AsyncMock(return_value={'behavior':'allow'}),
                      agent_status=Mock(), emit=Mock(), agent_cfg={}, preferences={})
        self.a = shared.SharedClaudeAgent(self.host, {'dir':'/tmp', 'shared_identity':False})
        self.a.session = {'sessionId':'s', 'pid':1}
        self.a.identity_context = Mock(return_value='')
        self.a._handoff_context = Mock(return_value='')
        self.a._note_skill = Mock()
        self.host.agent = self.a

    async def event(self, name, **kw):
        return await self.a.hook_event({'session_id':'s', 'hook_event_name':name, **kw})

    def source(self, src):
        self.a.cur_send = Send('fixture', 'hello', 0, source=src)
        self.a.native_source = src

    async def launch(self, aid, src):
        self.source(src)
        await self.event('PreToolUse', tool_name='Agent', tool_input={'subagent_type':'worker'})
        await self.event('SubagentStart', agent_id=aid, agent_type='worker')

    async def ask(self, aid=None):
        out=await self.event('PermissionRequest', **({'agent_id':aid} if aid else {}), tool_name='Read', tool_input={'file_path':'/tmp/test'})
        return out['hookSpecificOutput']['decision']['behavior']

    async def test_child_sessionstart_does_not_reset_main(self):
        await self.event('SessionStart', source='startup')
        await self.launch('owner', provenance.phone())
        self.a.cur_send = None
        await self.event('SessionStart', source='startup', agent_id='owner')
        self.assertEqual(await self.ask(), 'allow')
        self.assertEqual(await self.ask('owner'), 'allow')

    async def test_background_owner_does_not_take_later_unknown(self):
        await self.launch('owner', provenance.phone())
        self.source(provenance.normalize(None))
        self.assertEqual(await self.ask('owner'), 'allow')
        self.assertEqual(await self.ask(), 'deny')

    async def test_unknown_child_does_not_take_later_owner(self):
        await self.launch('unknown', provenance.normalize(None))
        self.source(provenance.phone())
        self.assertEqual(await self.ask('unknown'), 'deny')
        self.assertEqual(await self.ask('missing'), 'deny')
        self.assertEqual(await self.ask(), 'allow')

    async def test_session_generation_and_stop_revoke(self):
        await self.launch('owner', provenance.phone())
        await self.event('SubagentStop', agent_id='owner')
        self.assertEqual(await self.ask('owner'), 'deny')
        await self.launch('other', provenance.phone())
        await self.event('SessionStart', source='resume')
        self.source(provenance.phone())
        self.assertEqual(await self.ask('other'), 'deny')

    async def test_external_guest_friend_remain_refused(self):
        for src in ({'kind':'unknown','role':'group'}, provenance.email_guest('guest@example.test'), {'kind':'friend','role':'group'}):
            await self.launch('foreign',src)
            self.source(provenance.phone())
            self.assertEqual(await self.ask('foreign'),'deny')

    async def test_context_not_inherited_by_new_task(self):
        self.source(provenance.normalize(None))
        async def child(): return provenance.turn_source(self.a)
        with provenance.shared_child_request(self.a, provenance.phone()):
            self.assertEqual(provenance.turn_source(self.a),provenance.phone())
            self.assertEqual((await asyncio.create_task(child()))['kind'],'unknown')
            self.assertEqual((await asyncio.to_thread(provenance.turn_source,self.a))['kind'],'unknown')
        self.assertEqual(provenance.turn_source(self.a)['kind'],'unknown')

    async def test_ambiguous_same_type_never_upgrades_either_child(self):
        for src in (provenance.normalize(None), provenance.phone()):
            self.source(src)
            await self.event('PreToolUse', tool_name='Agent', tool_input={'subagent_type':'worker'})
        for aid in ('one','two'):
            await self.event('SubagentStart', agent_id=aid, agent_type='worker')
            self.assertEqual(await self.ask(aid),'deny')

    async def test_nested_unknown_parent_cannot_borrow_current_owner(self):
        await self.launch('unknown',provenance.normalize(None))
        self.source(provenance.phone())
        await self.event('PreToolUse',agent_id='unknown',tool_name='Agent',tool_input={'subagent_type':'worker'})
        await self.event('SubagentStart',agent_id='nested',agent_type='worker')
        self.assertEqual(await self.ask('nested'),'deny')

    async def test_system_child_keeps_unattended_policy_source(self):
        self.a.untrusted_state='running'
        self.a.untrusted_foreign=False
        await self.launch('system',provenance.system())
        self.source(provenance.normalize(None))
        self.assertEqual(await self.ask('system'),'allow')

    async def test_missing_agent_id_can_only_reduce_trust(self):
        await self.launch('foreign',provenance.normalize(None))
        self.source(provenance.phone())
        await self.event('PreToolUse',agent_id='foreign',tool_name='Read',tool_input={'file_path':'/tmp/test'})
        self.assertEqual(await self.ask(),'deny')
        await self.launch('owner',provenance.phone())
        await self.event('PreToolUse',agent_id='owner',tool_name='Read',tool_input={'file_path':'/tmp/test'})
        self.source(provenance.normalize(None))
        self.assertEqual(await self.ask(),'deny')

    async def test_dropped_pending_cannot_upgrade_late_start(self):
        self.source(provenance.normalize(None))
        await self.event('PreToolUse',tool_name='Agent',tool_input={'subagent_type':'worker'})
        await self.event('UserPromptSubmit',prompt='a new owner prompt')
        self.source(provenance.phone())
        await self.event('PreToolUse',tool_name='Agent',tool_input={'subagent_type':'worker'})
        await self.event('SubagentStart',agent_id='late',agent_type='worker')
        self.assertEqual(await self.ask('late'),'deny')

    async def test_pending_overflow_cannot_upgrade_old_unknown(self):
        self.source(provenance.normalize(None))
        await self.event('PreToolUse',tool_name='Agent',tool_input={'subagent_type':'worker'})
        self.source(provenance.phone())
        for _ in range(64):
            await self.event('PreToolUse',tool_name='Agent',tool_input={'subagent_type':'worker'})
        await self.event('SubagentStart',agent_id='late',agent_type='worker')
        self.assertEqual(await self.ask('late'),'deny')
