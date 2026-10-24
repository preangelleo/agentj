"""P501d: discovery is not qualification; free avatars stay visibly unavailable."""
import _hermetic
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from agentj import channel_avatar, channel_free, channel_plans

MODEL = 'opencode/fixture-free'

class FreeAvailability(unittest.TestCase):
    def test_fresh_catalogue_remains_visible_but_not_selectable(self):
        detected={'harnesses':[{'name':'opencode','installed':True,'supported':True,'logged_in':False}]}
        with patch('agentj.harness.detect',return_value=detected), patch.object(channel_plans.profiles,'listing',return_value=[]), \
             patch('agentj.binaries.resolve',return_value={'path':'fixture'}), \
             patch.object(channel_free,'catalogue',return_value=([MODEL],b'{}')):
            rows=channel_plans.inventory()
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row['id'],'free')
        self.assertEqual(row['models'],[MODEL])
        self.assertFalse(row['selectable'])
        self.assertEqual(row['why'],'free_temporarily_unavailable')
        self.assertIn('暂不可用',row['label']['zh'])
        self.assertIn('unavailable',row['label']['en'])
        single=channel_avatar.choice({}, {}, ['claude',row])
        self.assertFalse(single['ask'])
        self.assertEqual(single['recommend'],'claude')

    def test_direct_free_aliases_cannot_bypass_unavailable_state(self):
        for wanted in ('free','免费'):
            with self.subTest(wanted=wanted), self.assertRaisesRegex(ValueError,'free_temporarily_unavailable'):
                channel_avatar.choose(wanted,{},['opencode'],free_model=MODEL)

class FreeBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_select_discovers_but_never_prompts_or_changes_saved_plan(self):
        from agentj.external_channels import ExternalChannels
        channel=object.__new__(ExternalChannels)
        main={'kind':'codex'}
        saved={'avatar':{'config':{'kind':'claude'}},'sessions':{'old':'session'}}
        channel.h=NS(agent_cfg=main,lang='zh',agent=NS(cur_send=NS(source={'kind':'phone','role':'owner'})))
        channel.store=NS(data=saved,save=MagicMock())
        channel.sessions=NS(stop_all=AsyncMock()); channel.approval=AsyncMock(return_value=True)
        with patch.object(channel_plans,'inventory',return_value=[]), patch('agentj.binaries.resolve',return_value={'path':'fixture'}), \
             patch.object(channel_free,'catalogue',return_value=([MODEL],b'{}')), \
             patch('agentj.external_channels.openrouter_bound',return_value=False):
            for wanted in ('free','免费'):
                result=await channel.select({'id':wanted})
                self.assertFalse(result['ok'])
                self.assertEqual(result['why'],'free_temporarily_unavailable')
                self.assertFalse(result['selectable'])
                self.assertEqual(result['models'],[MODEL])
                self.assertIn('暂不可用',result['label']['zh'])
        channel.approval.assert_not_awaited(); channel.sessions.stop_all.assert_not_awaited()
        channel.store.save.assert_not_called()
        self.assertEqual(saved['avatar']['config'],{'kind':'claude'})
        self.assertEqual(saved['sessions'],{'old':'session'})
        self.assertEqual(main,{'kind':'codex'})

    async def test_existing_free_config_cannot_start_process_or_fallback(self):
        from agentj.wechat import Sessions
        host=NS(st=NS(),agent_cfg={'kind':'codex'},external_channels=NS(store=NS(data={'avatar':{'config':{
            'kind':'opencode','model':MODEL,'channel_free':True}}})))
        sessions=Sessions(host,NS())
        with patch('agentj.opencode_free.listed',return_value=[MODEL]) as listed, \
             patch.object(sessions,'_exe',return_value='fixture'), \
             patch('asyncio.create_subprocess_exec',new_callable=AsyncMock) as spawn, \
             patch.object(channel_free,'catalogue') as catalogue:
            result=await sessions.turn({'id':'owner'},{},'hello')
        self.assertEqual(result.error,'free_temporarily_unavailable')
        spawn.assert_not_awaited(); catalogue.assert_not_called(); listed.assert_not_called()
        self.assertEqual(host.agent_cfg,{'kind':'codex'})

    async def test_native_entry_is_disabled_before_any_environment_or_catalogue_read(self):
        sessions=NS()
        with patch.object(channel_free,'catalogue') as catalogue, patch('asyncio.create_subprocess_exec',new_callable=AsyncMock) as spawn:
            result=await channel_free.NativeFree(sessions).turn({}, {}, 'hello')
        self.assertEqual(result.error,'free_temporarily_unavailable')
        catalogue.assert_not_called(); spawn.assert_not_awaited()

    async def test_onboarding_mentions_unavailable_option_without_counting_it(self):
        from agentj.external_channels import ExternalChannels
        channel=object.__new__(ExternalChannels)
        channel.h=NS(agent_cfg={'kind':'claude'},lang='zh',agent_notice=MagicMock())
        channel.store=NS(data={},save=MagicMock())
        disabled={'id':'free','kind':'opencode','selectable':False,'models':[MODEL],
                  'why':'free_temporarily_unavailable','label':{'zh':'免费模型：暂不可用','en':'Free models: temporarily unavailable'}}
        rows=[{'id':k,'kind':k,'config':{'kind':k}} for k in ('claude','codex')]+[disabled]
        with patch.object(channel_plans,'inventory',return_value=rows):
            result=await channel.offer_choice()
        self.assertEqual(result['recommend'],'codex')
        notice=channel.h.agent_notice.call_args.args[0]
        self.assertIn('暂不可用',notice)
        self.assertIn('推荐 Codex',notice)

    async def test_unavailable_reply_waits_without_claim_or_main_handoff(self):
        from pathlib import Path
        from agentj.wechat import WeChat
        from agentj import peer_session
        for lang,word in (('zh','暂不可用'),('en','temporarily unavailable')):
            channel=object.__new__(WeChat)
            channel.h=NS(lang=lang); channel.sessions=NS();channel.index=object()
            channel.store=NS(path=Path('external.bin'))
            channel.model=AsyncMock(return_value=peer_session._fail('free_temporarily_unavailable'))
            reply=await channel.assistant('hello')
            self.assertIsNone(reply)
            self.assertTrue(channel.assistant_waiting)
            self.assertIn(word,channel.waiting_reply())
            self.assertNotIn('已转给',channel.waiting_reply())
