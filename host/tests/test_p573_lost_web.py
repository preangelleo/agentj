"""#573 recovered 0.26.9 host contracts; newer silent notices are retained."""
import _hermetic
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as N
from agentj import busy, completion_recovery, auth_rescue

class Where(unittest.TestCase):
    def test_remote_of(self):
        r = busy.remote_of
        self.assertEqual(r('~/bin/olares-run -c abc --at /w -o /o -- bash run.sh'), 'Olares')
        self.assertEqual(r('ssh -i ~/.ssh/k -p 2222 olares@olares "ls /data"'), 'olares')
        self.assertEqual(r('cd x && ssh -o BatchMode=yes fixture-remote uptime'), 'fixture-remote')
        self.assertEqual(r('ssh 10.0.0.5 ls'), 'ssh')            # an address is never shown
        self.assertEqual(r('ssh root@[fe80::1] ls'), 'ssh')
        for local in ('python3 -m pytest', 'echo /usr/bin/ssh-keygen', 'git push origin x', '', None):
            self.assertIsNone(r(local), local)

    def test_wait_loop(self):
        self.assertTrue(busy.wait_loop('until grep -q PASS out.log; do sleep 10; done'))
        self.assertTrue(busy.wait_loop('  while ! test -f x; do sleep 2; done'))
        self.assertFalse(busy.wait_loop('npm test && sleep 1'))
        self.assertFalse(busy.wait_loop(None))

    def test_board(self):
        tmp = tempfile.mkdtemp(prefix='w4-busy-')
        self.addCleanup(shutil.rmtree, tmp, True)
        b = busy.Board(Path(tmp))
        ev = lambda tid, cmd, desc: {'hook_event_name': 'PostToolUse', 'session_id': 's1', 'tool_name': 'Bash',
                                    'tool_input': {'command': cmd, 'description': desc, 'run_in_background': True},
                                    'tool_response': {'backgroundTaskId': tid}}
        b.hook_event(ev('b1', '~/bin/olares-run -c 1 --at /w -o /o -- bash r.sh', 'host tests on Olares'))
        b.hook_event(ev('b2', 'until grep -q DONE /tmp/x.log; do sleep 5; done', 'wait for tests'))
        b.hook_event(ev('b3', 'npm test', 'web tests'))
        items = {i['label']: i for i in b.items()}
        self.assertEqual(set(items), {'host tests on Olares', 'web tests'})
        self.assertEqual(items['host tests on Olares']['where'], 'Olares')
        self.assertNotIn('where', items['web tests'])
        b.hook_event({'hook_event_name': 'Stop', 'session_id': 's1', 'background_tasks': [{'id': 'b2', 'type': 'shell', 'status': 'completed', 'command': 'until x; do sleep 1; done'}]})
        self.assertFalse(any('wait' in (f.get('label') or '') for f in b.finished))   # never a background result


class HumanList(unittest.TestCase):
    """0.26.9 #446: /recover-background reads as plain words — a short clickable Read per report, the rest marked
    「不用你处理」, no 64-hex id in the visible words, no JSON."""
    def test_list_and_refusals_are_plain_words(self):
        from agentj import completion_recovery as c
        items = [{'id': 'a' * 64, 'task_id': 5, 'state': 'awaiting_owner_verification', 'report_available': True, 'created_at': 1762260000, 'task_label': 'mail run'},
                 {'id': 'b' * 64, 'task_id': 'shared:x:y', 'state': 'awaiting_owner_verification', 'report_available': False, 'created_at': 1762260000, 'tool': 'Bash'},
                 {'id': 'c' * 64, 'task_id': 'z', 'state': 'reconciled', 'settled': {'by': 'native_task_notification'}}]
        zh = c.human({'result': 'awaiting_owner_verification', 'items': items}, 'zh')
        self.assertIn('不用你处理（2 条）', zh)
        self.assertIn('[读报告](reply:/recover-background ' + 'a' * 64 + ')', zh)
        visible = zh.replace('(reply:/recover-background ' + 'a' * 64 + ')', '')
        self.assertNotIn('a' * 64, visible); self.assertNotIn('b' * 64, visible); self.assertNotIn('{', zh)
        en = c.human({'result': 'awaiting_owner_verification', 'items': items[1:]}, 'en')
        self.assertIn('None needs anything from you', en)
        self.assertEqual(c.human({'result': 'awaiting_owner_verification', 'items': []}, 'zh'), '没有要你核对的后台结果。')
        self.assertEqual(c.human({'result': 'refused', 'why': 'already_reconciled'}, 'zh'), '这条已经核对过了，不用再看。')
        self.assertEqual(c.human({'result': 'refused', 'why': 'nonsense'}, 'en'), 'The command was not understood.')


class RecoveryQuiet(unittest.TestCase):
    def test_quiet_notices_stay_quiet_and_list_remains_readable(self):
        with tempfile.TemporaryDirectory() as td:
            h = N(st=N(root=td, config=lambda:{'channel':'test'}, log=lambda *a,**kw:None), agent_cfg={'dir':td}, background_done=N(pending={},save=lambda:None))
            b = completion_recovery.Book(h)
            b.add('a'*64, {'task_id':3,'source':'unknown','recovery_point':'reconcile_only','task_label':'mail run'})
            self.assertEqual(b.notice(), '')
            shown = completion_recovery.human(b.recover(), 'en')
            self.assertIn('mail run', shown)
            self.assertIn('Nothing to do', shown)
            self.assertNotIn('a'*64, shown)

class AccountSummary(unittest.TestCase):
    def test_add_another_summary_is_one_sentence(self):
        for lang in ('zh','en'):
            rescue = object.__new__(auth_rescue.Rescue)
            rescue.host = N(lang=lang)
            shown = rescue._summary('codex', 'Backup 1')
            self.assertNotIn('\n',shown)
            self.assertIn('another' if lang=='en' else '另一个',shown)
            self.assertIn('not affected' if lang=='en' else '不受影响',shown)


class HostMarker(unittest.IsolatedAsyncioTestCase):
    async def test_host_confirmation_sets_private_marker(self):
        from unittest.mock import AsyncMock
        rescue = object.__new__(auth_rescue.Rescue)
        rescue.host = N(lang='en', ask=AsyncMock(return_value={'behavior':'allow'}))
        self.assertTrue(await rescue._confirm('codex','Backup 1'))
        self.assertIs(rescue.host.ask.call_args.kwargs['_host_card'], True)
        self.assertIs(rescue.host.ask.call_args.kwargs['always_ask'], True)

    async def test_wire_marker_cannot_come_from_tool_input(self):
        from agentj import serve
        a=serve.Ask('r','AuthRescue','Add another','digest',{'host_tool':True},'input',1,None)
        h=N(st=N(passkey_of_device=lambda *_:None))
        self.assertNotIn('host_tool',serve.Host._ask_msg(h,a))
        a.host_card=True
        self.assertIs(serve.Host._ask_msg(h,a)['host_tool'],True)


class RemoteAliases(unittest.TestCase):
    def test_labels_validate_and_do_not_configure_connections(self):
        from agentj import preferences
        rows=[{'host':'10.0.0.5','name':'Olares worker'},{'host':'lab','name':'Lab'}]
        self.assertTrue(preferences.remote_servers_ok(rows))
        preferences.validate({'ui':{'remote_servers':rows}})
        self.assertEqual(busy.remote_of('ssh root@10.0.0.5 uptime',rows),'Olares worker')
        self.assertEqual(busy.remote_of('ssh -p 2222 lab uptime',rows),'Lab')
        for bad in ([{'host':'x','name':'x','command':'ls'}],rows+rows,[{'host':'user@host','name':'x'}],[{'host':'x','name':'x\ny'}],[{'host':'x','name':'x'*41}]):
            self.assertFalse(preferences.remote_servers_ok(bad))
            with self.assertRaises(preferences.ConfigError):
                preferences.validate({'ui':{'remote_servers':bad}})

    def test_real_background_task_uses_configured_name(self):
        b=busy.Board(None)
        b.remote_aliases=lambda:[{'host':'lab','name':'GPU worker'}]
        b.hook_event({'hook_event_name':'PostToolUse','session_id':'s','tool_name':'Bash','tool_input':{'command':'ssh lab pytest','description':'Tests','run_in_background':True},'tool_response':{'backgroundTaskId':'b'}})
        self.assertEqual(b.items()[0]['where'],'GPU worker')


class NativeReported(unittest.TestCase):
    def test_already_reported_native_shell_is_reconciled_without_owner(self):
        with tempfile.TemporaryDirectory() as td:
            h=N(st=N(root=td,config=lambda:{'channel':'test'},log=lambda *a,**kw:None),agent_cfg={'dir':td},background_done=N(pending={},save=lambda:None))
            b=completion_recovery.Book(h)
            key='c'*64
            b.add(key, {'task_id':'shared-shell-1','source':'unknown','recovery_point':'reconcile_only','task_label':'Agent tests'})
            b.native['shared-shell-1']=123
            b._settle()
            self.assertEqual(b.rows[key]['state'],'reconciled')
            self.assertEqual(b.rows[key]['settled']['by'],'native_task_notification')
            self.assertEqual(b.notice(),'')


class AliasPhoneIngress(unittest.IsolatedAsyncioTestCase):
    async def test_paired_phone_can_only_set_valid_display_aliases(self):
        from agentj import serve
        from unittest.mock import AsyncMock, Mock
        session=N(cid=1,state='ready',pub=b'paired',device='phone')
        h=N(sessions={1:session},st=N(is_allowed=lambda pub:pub==b'paired',log=Mock()),preferences={},send_app=AsyncMock(),set_pref=AsyncMock())
        rows=[{'host':'lab','name':'GPU worker'}]
        await serve.Host.on_pref_set(h,session,{'r':'a'*32,'key':'ui.remote_servers','value':rows})
        h.set_pref.assert_awaited_once()
        self.assertEqual(h.set_pref.call_args.args,('ui.remote_servers',rows))
        h.set_pref.reset_mock()
        await serve.Host.on_pref_set(h,session,{'r':'b'*32,'key':'ui.remote_servers','value':[{'host':'x','name':'bad\nname'}]})
        h.set_pref.assert_not_awaited()
        self.assertEqual(h.send_app.call_args.args[1]['problem'],'bad_value')


class SettingsUVScope(unittest.IsolatedAsyncioTestCase):
    def setup_host(self):
        from unittest.mock import Mock, AsyncMock
        import asyncio
        session=N(cid=1,state='ready',device='phone',pub=b'paired')
        st=N(sign_key=lambda _:b'k'*32,passkey_of_device=lambda _:{'id':'p','alg':-7,'spki':'public','rp':'agentj.app'},log=Mock())
        host=N(st=st,channel='channel',stopped=lambda:False,stopping=asyncio.Event(),sessions={1:session},ask=AsyncMock(return_value={'behavior':'allow'}))
        host._paired_live=lambda sess:host.sessions.get(sess.cid) is sess and sess.state=='ready'
        return host,session

    def verified(self,sess):
        return N(settings_uv_at=__import__('time').monotonic(),host_card=True,tool='AuthRescue',settings_session=sess,require_passkey=True,uv_ok=True,only=sess.device,rid='r')

    async def test_valid_scope_skips_only_same_session_settings_confirmation(self):
        from agentj import auth_settings_uv as uv
        from unittest.mock import patch
        host,sess=self.setup_host()
        rescue=object.__new__(auth_rescue.Rescue);rescue.host=host
        with patch.object(uv.time,'monotonic',return_value=100):
            self.assertTrue(uv.mint(host,sess,self.verified(sess)))
        with patch.object(uv.time,'monotonic',return_value=399):
            self.assertTrue(await rescue._confirm('codex','Backup',remote=sess))
            host.ask.assert_not_awaited()
            self.assertFalse(uv.reuse(host,sess,'delete'))
            self.assertTrue(await rescue._confirm('codex','Backup'))
            host.ask.assert_awaited_once()  # Agent/CLI has no live requesting session
        host.ask.reset_mock()
        with patch.object(uv.time,'monotonic',return_value=400):
            self.assertFalse(uv.reuse(host,sess,'add'))

    async def test_scope_rejects_each_binding_change_and_no_uv(self):
        from agentj import auth_settings_uv as uv
        from unittest.mock import patch
        for change in ('channel','signkey','passkey','disconnected','stopped','noUV','wrongSession','wrongTool'):
            host,sess=self.setup_host();a=self.verified(sess)
            a.settings_uv_at=10
            if change=='noUV':a.uv_ok=False
            if change=='wrongSession':a.settings_session=N(cid=1,device='phone')
            if change=='wrongTool':a.tool='Read'
            with patch.object(uv.time,'monotonic',return_value=10):
                minted=uv.mint(host,sess,a)
                if change in ('noUV','wrongSession','wrongTool'):
                    self.assertFalse(minted);continue
                self.assertTrue(minted)
                if change=='channel':host.channel='other'
                if change=='signkey':host.st.sign_key=lambda _:b'x'*32
                if change=='passkey':host.st.passkey_of_device=lambda _:{'id':'replacement','alg':-7,'spki':'public','rp':'agentj.app'}
                if change=='disconnected':host.sessions.clear()
                if change=='stopped':host.stopped=lambda:True
                self.assertFalse(uv.reuse(host,sess,'login'),change)

    async def test_first_card_binds_session_uv_and_discloses_scope(self):
        host,sess=self.setup_host();host.lang='en'
        rescue=object.__new__(auth_rescue.Rescue);rescue.host=host
        self.assertTrue(await rescue._confirm('codex','Backup',remote=sess))
        kw=host.ask.call_args.kwargs
        self.assertIs(kw['_settings_session'],sess)
        self.assertTrue(kw['require_passkey'])
        self.assertEqual(kw['only'],'phone')
        self.assertIn('5 minutes',host.ask.call_args.args[1]['summary'])
        self.assertIn('adding another AI account',host.ask.call_args.args[1]['summary'])

    async def test_cancel_and_explicit_clear_invalidate_without_sliding_expiry(self):
        from agentj import auth_settings_uv as uv
        from unittest.mock import patch
        host,sess=self.setup_host();rescue=object.__new__(auth_rescue.Rescue);rescue.host=host
        import asyncio
        rescue.cancel=asyncio.Event();rescue.flow=None
        with patch.object(uv.time,'monotonic',return_value=10):uv.mint(host,sess,self.verified(sess))
        with patch.object(uv.time,'monotonic',return_value=309):self.assertTrue(uv.reuse(host,sess,'login'))
        with patch.object(uv.time,'monotonic',return_value=310):self.assertFalse(uv.reuse(host,sess,'login'))
        uv.mint(host,sess,self.verified(sess));await rescue.cancel_flow()
        self.assertFalse(uv.reuse(host,sess,'add'))


class SettingsUVLifecycle(unittest.IsolatedAsyncioTestCase):
    setup_host = SettingsUVScope.setup_host
    verified = SettingsUVScope.verified
    async def test_estop_activation_clears_before_resume_and_relay_detach_clears(self):
        from agentj import auth_settings_uv as uv, serve
        from unittest.mock import Mock, patch
        host,sess=self.setup_host();uv.mint(host,sess,self.verified(sess))
        # Abort after the real hook clears memory, before any persistent state write.
        with patch('agentj.serve.controls.set_estop',side_effect=RuntimeError('stop-after-clear')):
            with self.assertRaisesRegex(RuntimeError,'stop-after-clear'):
                await serve.Host.do_estop(host,'terminal','test')
        self.assertFalse(uv.reuse(host,sess,'add'))  # stop-on/off cannot revive it
        uv.mint(host,sess,self.verified(sess))
        host.bots=N(detach=Mock());host.reporter=N(trigger=Mock());host.pairing=None
        sess.timer=None;sess.card=None
        self.assertIs(serve.Host._detach(host,1),sess)
        host.sessions[1]=sess  # reconnecting even with the same object cannot revive a removed grant
        self.assertFalse(uv.reuse(host,sess,'login'))
