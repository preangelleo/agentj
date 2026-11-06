"""0.26.10 owner acceptance: persistent mail authority, quota, human goal results, package hints."""
import _hermetic
import copy
import os
import unittest
from unittest.mock import patch
from agentj import workflow_mail as wm, contacts, voice, goal, market, plaza_deps, workflows
from test_workflow_owner_mail import Delivery
from test_p194_owner_direct import ME


class Mail(Delivery):
    async def test_non_recipient_task_edit_and_contact_name_preserve_authority(self):
        await self.grant()
        self.contract['tsha'] = self.e['tsha'] = 'f'*64
        contacts.local(self.st, {'op': 'owner_set', 'owner_ok': True,
                                 'contact': {'name': 'A new display name', 'emails': [ME]}})
        self.ask.reset_mock()
        result = await self.service.send(self.req, os.getpid())
        self.assertTrue(result['ok'], result)
        self.ask.assert_not_awaited()

    async def test_more_than_three_deliveries_no_daily_or_total_cap(self):
        await self.grant(max=1, per_day=1)
        self.ask.reset_mock()
        for i in range(6):
            req = copy.deepcopy(self.req)
            req['delivery_manifest']['idempotency_key'] = 'episode-'+str(i)
            self.assertTrue((await self.service.send(req, os.getpid()))['ok'])
        self.assertEqual(len(self.smtp.sent), 6)
        self.ask.assert_not_awaited()
        self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'duplicate')

    async def test_legacy_missing_sender_pin_requires_one_migration_approval(self):
        await self.grant()
        row=self.service.load()['grants']['story']
        row['core'].pop('mail_account')
        self.assertEqual(self.service.why(row,self.contract),'smtp_binding_required')
        await self.grant()
        self.assertIsNone(self.service.why(self.service.load()['grants']['story'],self.contract))

    async def test_default_grant_has_no_periodic_renewal_but_explicit_expiry_is_kept(self):
        await self.grant()
        row=self.service.load()['grants']['story']
        self.assertTrue(row['core']['never_expires'])
        self.assertIn('直到你撤销',wm.shown(row['core']))
        future=__import__('time').time()+100*86400
        with patch.object(wm.time,'time',return_value=future):
            self.assertIsNone(self.service.why(row,self.contract))
        await self.service.control({'op':'revoke','workflow_id':'story'},None)
        await self.grant(days=2)
        row=self.service.load()['grants']['story']
        self.assertFalse(row['core']['never_expires'])
        with patch.object(wm.time,'time',return_value=future):
            self.assertEqual(self.service.why(row,self.contract),'expired')

    async def test_explicit_shorter_expiry_is_not_ignored_and_default_never_widens_it(self):
        await self.grant()
        self.ask.reset_mock()
        result=await self.grant(days=2)
        row=self.service.load()['grants']['story']
        self.assertFalse(row['core']['never_expires'])
        self.ask.assert_awaited_once()
        self.assertEqual(row['core']['expires']-row['core']['issued'],2*86400)
        signed_id,expiry=row['core']['id'],row['core']['expires']
        self.ask.reset_mock()
        for request in ({}, {'days':2}):
            result=await self.grant(**request)
            self.assertTrue(result['unchanged'])
            self.assertFalse(result['grant']['never_expires'])
            current=self.service.load()['grants']['story']['core']
            self.assertEqual((current['id'],current['expires']),(signed_id,expiry))
        self.ask.assert_not_awaited()

    async def test_sender_config_change_requires_new_authority(self):
        await self.grant()
        old = self.service.mail_account()
        with patch.object(self.service, 'mail_account', return_value=old+'changed'):
            result = await self.service.send(self.req, os.getpid())
        self.assertEqual(result['reason'], 'account_changed')
        self.assertFalse(self.smtp.sent)

    async def test_wrapped_smtp_error_retains_safe_code(self):
        from agentj import mail
        await self.grant()
        with patch.object(self.svc,'smtp_send',side_effect=mail.MailError('smtp_timeout')):
            result=await self.service.send(self.req,os.getpid())
        self.assertEqual(result['error']['code'],'smtp_timeout')
        self.assertTrue(result['error']['timeout'])

    async def test_unknown_records_safe_reason_and_owner_retry_is_once(self):
        await self.grant()
        with patch.object(self.svc, 'smtp_send', side_effect=TimeoutError('secret must never be copied')):
            result = await self.service.send(self.req, os.getpid())
        self.assertEqual(result['error'], {'type': 'TimeoutError', 'stage': 'smtp', 'timeout': True})
        self.assertNotIn('secret must', str(self.service.load()))
        key = result['delivery']
        from types import SimpleNamespace
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(source={'kind':'phone','role':'owner'}))
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'duplicate')
        from agentj import provenance
        self.host.agent.cur_send.source={'kind':'email','role':'group'}
        self.assertEqual((await self.service.control({'op':'retry','workflow_id':'story','delivery':key},os.getpid()))['why'], 'workflow_source')
        self.host.agent.cur_send.source={'kind':'phone','role':'owner'}
        self.ask.side_effect = None
        self.ask.return_value = {'behavior': 'deny'}
        self.assertEqual((await self.service.control({'op':'retry','workflow_id':'story','delivery':key},os.getpid()))['why'], 'denied')
        self.ask.return_value = {'behavior':'allow'}
        self.assertTrue((await self.service.control({'op':'retry','workflow_id':'story','delivery':key},os.getpid()))['retry_once'])
        self.assertTrue(self.ask.await_args.kwargs['require_passkey'])
        altered=copy.deepcopy(self.req)
        altered['body']='changed body'
        altered['delivery_manifest']['body_sha256']=__import__('hashlib').sha256(altered['body'].encode()).hexdigest()
        self.assertEqual((await self.service.send(altered,os.getpid()))['why'], 'retry_manifest_changed')
        self.assertTrue((await self.service.send(self.req,os.getpid()))['ok'])
        self.assertEqual((await self.service.control({'op':'retry','workflow_id':'story','delivery':key},os.getpid()))['why'], 'retry_unavailable')
        self.assertEqual((await self.service.send(self.req,os.getpid()))['why'], 'duplicate')


class Presentation(unittest.TestCase):
    def test_olares_observed_typo_and_user_override(self):
        self.assertEqual(voice.correct_transcript('Olares 千问把 Olares 转成 Olayris', {}), 'Olares 千问把 Olares 转成 Olares')
        self.assertEqual(voice.correct_transcript('Olayris', {'voice':{'asr':{'corrections':{'Olayris':'custom'}}}}), 'custom')

    def test_goal_expiry_outcome_progress_and_no_zero_terms(self):
        d = {'goal':'finish work','ended':'expired','milestones':[{'text':'two items passed'}]}
        for lang, phrase in [('zh','没在截止前完成'),('en','Not finished before the deadline')]:
            text=goal.result_text(d,lang)
            self.assertIn(phrase,text); self.assertIn('two items passed',text)
            self.assertNotIn('10-second',text); self.assertNotIn('倒计时',text); self.assertNotIn('授权：无',text)
        self.assertIn('已完成',goal.result_text({**d,'ended':'achieved'}))

    def test_homebrew_copy_is_not_called_user_directory(self):
        with patch.object(market,'brew_problem',return_value='组/其他人写权限 mode 0775'):
            text = plaza_deps.task_text('svg','1.0',[{'name':'node','user':['/opt/homebrew/bin']}],False,'/usr/bin', ['node'])
        self.assertIn('Homebrew',text); self.assertIn('组/其他人写权限',text); self.assertIn('0775',text)
        self.assertNotIn('你的用户目录里有',text)

    def test_os_mismatch_warning(self):
        s = {k: [] for k in ('harness','os','cli','python_requirements','env','accounts','skills','roles','automations','verify','post_install')}
        s['os']=['linux']
        # summarize returns the stable preview schema, use an actual minimal fixture below.
        with patch.object(market.sys,'platform','darwin'):
            text='\n'.join(market._summary_lines(s))
        self.assertIn('不匹配',text)
        from agentj import plaza_perms
        with patch.object(market.sys,'platform','darwin'):
            warning=market.os_warning(['linux'])
        info={'name':'svg-animation-skill','version':'1.0.0','type':'skill','sha256':'a'*64,'digest':'b'*16,
              'certified':True,'publisher':{'known':False},'perm':plaza_perms.EMPTY,'extra':[],'audit':[],
              'target':'/tmp/skills/svg-animation-skill','harness':'codex','os_warning':warning}
        self.assertIn('不匹配',plaza_perms.install_card_text(info,False))
        with patch.object(market.sys,'platform','darwin'):
            self.assertIsNone(market.os_warning(['linux','darwin']))


class Pairing(unittest.IsolatedAsyncioTestCase):
    async def test_forced_unpaired_preference_write_does_not_reach_persistence(self):
        from test_p440_custom_asr import PhoneASRSecurity, config
        from agentj.serve import Host
        for state, allowed in [('pending',True),('ready',False)]:
            host,phone=PhoneASRSecurity().host(config())
            phone.state=state
            host.st.is_allowed=lambda pub:allowed
            await Host.on_pref_set(host,phone,{'r':'0123456789abcdef','key':'ui.font_size','value':20})
            host.set_pref.assert_not_called()

    async def test_goal_status_uses_live_goal(self):
        from types import SimpleNamespace
        from agentj.serve import Host
        host=Host.__new__(Host)
        host.eff_status=lambda:'idle';host.agent=None;host.telegram=None
        host.st=SimpleNamespace(agent_name=lambda:'Agent J')
        host.goals=SimpleNamespace(current=lambda:{'state':'active'})
        self.assertTrue(host._status_msg()['goal_active'])
        host.goals.current=lambda:None
        self.assertFalse(host._status_msg()['goal_active'])


class Roster(unittest.TestCase):
    def test_roster_name_only_and_no_prose_fallback(self):
        import tempfile
        from pathlib import Path
        from _testtmp import TEST_TMP
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            root=Path(temp)
            for name,ceo in [('story-channel','织夜'),('two','乙'),('three','丙'),('four','丁')]:
                d=root/name/'documentation';d.mkdir(parents=True)
                (d/'CONSTITUTION.md').write_text('# Constitution')
                (d/'ROLES.md').write_text('# Roles')
                (d/'IDENTITY.md').write_text('# CEO 织夜的身份\n以 CEO 织夜的身份修复')
                rows=[[ceo, './'+name+'/AGENTS.md']]
                self.assertEqual(workflows.scan_folder(str(root),name,rows)['ceo'],ceo)
                self.assertEqual(workflows.scan_folder(str(root),name,[[f'CEO {ceo}', './'+name+'/AGENTS.md']])['ceo'],ceo)
                self.assertNotIn('ceo',workflows.scan_folder(str(root),name,[]))

    def test_legacy_and_host_plaza_refusal_distinguish_no_from_expiry(self):
        from agentj import plaza_perms
        denied=market._perm_refusal('svg',{'why':plaza_perms.WHYS['device']})
        expired=market._perm_refusal('svg',{'why':plaza_perms.WHYS['timeout']})
        self.assertIn('拒绝',denied);self.assertNotIn('超时',denied)
        self.assertIn('超时',expired);self.assertNotIn('你已在手机上拒绝',expired)


class Citations(unittest.IsolatedAsyncioTestCase):
    async def test_harness_citation_paths_do_not_reach_media_scanner(self):
        from agentj import media_live
        from types import SimpleNamespace
        captured=[]
        for directive in ('codex-file-citation','codex-citation','file-citation'):
            page={'end':'done','reply':{'text':f'Summary :{directive}{{path="/tmp/uploaded.pdf" purpose="source"}}'}}
            host=SimpleNamespace(hist=SimpleNamespace(get=lambda tid:page,parts={0:[1]}))
            scanner=SimpleNamespace(scan_keys=lambda text,tid:(captured.append(text) or [],[],[]))
            await media_live.scan(host,1,{'generation':1,'scanner':scanner,'keys':set()})
            self.assertNotIn('uploaded.pdf',captured[-1])
            self.assertIn('Summary',captured[-1])
