"""Offline synthetic native scheduler inventory and signed Host controls. No real scheduler calls."""
import asyncio
import csv
import io
import json
import pathlib
import plistlib
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import systasks, controls, wire
from agentj import native_scheduler_control as native


class FakeState:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.key = Ed25519PrivateKey.generate()
        self.allowed = True
        self.controls_path = self.root / 'controls.log'
    def sign_key(self, device):
        return self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw) if self.allowed else None
    def is_allowed(self, pub):
        return self.allowed
    def write_private(self,path,data):
        path.write_bytes(data)
    def append_private(self,path,text):
        with path.open('a') as f:
            f.write(text+'\n')
    def passkey_of_device(self,device):
        return None


class FakeBackend:
    platform = 'linux'
    def __init__(self,source='systemd', enabled=True,loaded=True):
        self.enabled,self.loaded,self.config = enabled,loaded,'fixed-config'
        self.calls=[]
        self.fail=False
        self.before=None
        self.source=source
    def snapshot(self,target):
        native.validate(target)
        snap = {k:target[k] for k in ('platform','source','scope','id')}
        snap.update(config=self.config, enabled=self.enabled, loaded=self.loaded)
        snap['snapshot']=native.digest(snap)
        return snap
    commands = native.Backend.commands
    def run(self,argv):
        self.calls.append(argv)
        if self.before:
            self.before()
        if self.fail:
            return None
        if 'disable' in argv:
            self.enabled=False
            if '--now' in argv: self.loaded=False
        elif 'enable' in argv: self.enabled=True
        elif 'start' in argv: self.loaded=True
        elif 'stop' in argv: self.loaded=False
        return ''


class NativeControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st=FakeState(self.tmp.name)
        self.backend=FakeBackend()
        self.controller=native.Controller(self.st,self.backend)
        self.target=dict(platform='linux',source='systemd',scope='user',id='fixture.timer',op='disable',snapshot='0'*64)
        self.refresh()
    def refresh(self,op=None):
        if op:self.target['op']=op
        self.target['snapshot']=self.backend.snapshot(self.target)['snapshot']
    def test_disable_restore_original_enabled_and_running(self):
        self.assertTrue(self.controller.apply(self.target,lambda:None)['ok'])
        self.assertFalse(self.backend.enabled)
        self.assertTrue(self.controller.journal())
        self.refresh('restore')
        self.controller.apply(self.target,lambda:None)
        self.assertTrue(self.backend.enabled)
        self.assertTrue(self.backend.loaded)
        self.assertEqual(self.controller.journal(),{})
    def test_restore_original_disabled_and_inactive(self):
        self.backend.enabled=self.backend.loaded=False
        self.refresh()
        self.controller.apply(self.target,lambda:None)
        self.refresh('restore')
        self.controller.apply(self.target,lambda:None)
        self.assertFalse(self.backend.enabled or self.backend.loaded)
    def test_changed_snapshot_no_effect(self):
        self.backend.config='replacement'
        with self.assertRaisesRegex(native.Refused,'changed'):
            self.controller.apply(self.target,lambda:None)
        self.assertEqual(self.backend.calls,[])
    def test_changed_definition_cannot_restore(self):
        self.controller.apply(self.target,lambda:None)
        self.backend.config='replacement'
        self.refresh('restore')
        with self.assertRaisesRegex(native.Refused,'changed'):
            self.controller.apply(self.target,lambda:None)
        self.assertEqual(len(self.backend.calls),1)
    def test_failed_command_records_recovery_not_success(self):
        self.backend.fail=True
        with self.assertRaisesRegex(native.Refused,'read_only'):
            self.controller.apply(self.target,lambda:None)
        self.assertTrue(self.controller.journal())
        self.backend.fail=False
        self.refresh('restore')
        self.controller.apply(self.target,lambda:None)
        self.assertTrue(self.backend.enabled)
    def test_noop_runner_does_not_report_success(self):
        self.backend.run=lambda argv:''
        with self.assertRaisesRegex(native.Refused,'verify_failed'):
            self.controller.apply(self.target,lambda:None)
    def test_unpaired_guard_blocks_effect(self):
        def guard(): raise native.Refused('no_device')
        with self.assertRaisesRegex(native.Refused,'no_device'):
            self.controller.apply(self.target,guard)
        self.assertEqual(self.backend.calls,[])
    def test_revocation_between_commands_retains_recovery(self):
        self.controller.apply(self.target,lambda:None)
        self.refresh('restore')
        live=[True]
        self.backend.before=lambda:live.__setitem__(0,False)
        def guard():
            if not live[0]:raise native.Refused('no_device')
        with self.assertRaisesRegex(native.Refused,'no_device'):
            self.controller.apply(self.target,guard)
        self.assertFalse(self.backend.loaded)
        self.assertTrue(self.controller.journal())
    def test_object_changes_between_probe_and_effect(self):
        count=[0]
        def guard():
            count[0]+=1
            if count[0]==2:self.backend.config='new'
        with self.assertRaisesRegex(native.Refused,'changed'):
            self.controller.apply(self.target,guard)
        self.assertEqual(self.backend.calls,[])
    def test_unsupported_sources_and_injections(self):
        for ident in ('--all.timer','../x.timer','x.timer;whoami','x.timer\nstart','x/y.timer'):
            with self.subTest(ident=ident),self.assertRaises(native.Refused):
                native.validate(dict(self.target,id=ident))
        with self.assertRaisesRegex(native.Refused,'read_only'):
            native.validate(dict(self.target,source='cron'))
    def test_fixed_argv_all_platforms(self):
        snap=dict(source='launchd',id='org.fixture.job',scope='system',domain='system',path='/Library/LaunchDaemons/org.fixture.job.plist',loaded=True)
        self.assertEqual(native.Backend.commands(None,snap,'disable',snap),[['launchctl','disable','system/org.fixture.job'],['launchctl','bootout','system/org.fixture.job']])
        restore=dict(snap,loaded=False)
        self.assertEqual(native.Backend.commands(None,restore,'restore',dict(snap,enabled=True))[1],['launchctl','bootstrap','system',snap['path']])
        win=dict(source='schtasks',scope='all-users',id='\\Folder\\Fixture')
        self.assertEqual(native.Backend.commands(None,win,'restore',dict(enabled=False)),[['schtasks','/change','/tn','\\Folder\\Fixture','/disable']])

    def host(self):
        session=NS(state='ready',device='device',pub=b'fixture',cid='cid')
        h=NS(st=self.st,channel='fixture-channel',sessions={'cid':session},nonces=controls.Nonces(),native_scheduler_controller=self.controller,_ctl_res=AsyncMock())
        return h,session
    def signed(self,h,target=None):
        target=target or self.target
        msg=dict(t=native.ACTION,r='1'*32,n='a'*32,ts=int(time.time()*1000),**target)
        data=controls.signed_message(h.channel,'device',native.ACTION,msg['n'],msg['ts'],controls.object_digest(native.ACTION,target))
        msg['sig']=wire.b64u(self.st.key.sign(data))
        return msg
    def dispatch(self,h,s,msg):
        asyncio.run(native.on_phone(h,s,msg))
        return h._ctl_res.call_args.kwargs
    def test_signed_host_control_and_replay(self):
        h,s=self.host();msg=self.signed(h)
        self.assertTrue(self.dispatch(h,s,msg)['ok'])
        self.assertEqual(self.dispatch(h,s,msg)['why'],'replay')
        self.assertEqual(len(self.backend.calls),1)
    def test_signature_binds_every_field(self):
        for field,value in [('id','other.timer'),('op','restore'),('scope','system'),('platform','linux2'),('snapshot','f'*64)]:
            h,s=self.host();msg=self.signed(h);msg[field]=value
            with self.subTest(field=field):
                result=self.dispatch(h,s,msg)
                self.assertFalse(result['ok'])
                self.assertEqual(self.backend.calls,[])
    def test_old_session_cannot_control(self):
        h,s=self.host();msg=self.signed(h);h.sessions['cid']=NS()
        self.assertEqual(self.dispatch(h,s,msg)['why'],'no_device')
    def test_system_requires_uv(self):
        h,s=self.host();self.target['scope']='system';self.refresh()
        self.assertEqual(self.dispatch(h,s,self.signed(h))['why'],'no_passkey')
        self.assertEqual(self.backend.calls,[])
    def test_system_valid_uv_uses_same_signed_target(self):
        h,s=self.host();self.target['scope']='system';self.refresh();msg=self.signed(h);msg['fa']={}
        self.st.passkey_of_device=lambda device:{'id':'fixture-pk'}
        self.st.passkey_count=lambda *args:None
        with patch('agentj.passkey.approval_fields',return_value={'id':'fixture-pk'}),patch('agentj.passkey.verify_assertion',return_value=1):
            self.assertTrue(self.dispatch(h,s,msg)['ok'])
    def test_uv_counter_advance_is_not_authorization_root_change(self):
        h,s=self.host();self.target['scope']='system';self.refresh();msg=self.signed(h);msg['fa']={}
        pk={'id':'fixture-pk','spki':'public-fixture','sc':0}
        self.st.passkey_of_device=lambda device:dict(pk)
        self.st.passkey_count=lambda device,ident,count:pk.update(sc=count)
        with patch('agentj.passkey.approval_fields',return_value={'id':'fixture-pk'}),patch('agentj.passkey.verify_assertion',return_value=1):
            self.assertTrue(self.dispatch(h,s,msg)['ok'])
        self.assertEqual(pk['sc'],1)
    def test_malformed_target_has_no_native_effect(self):
        for field in native.FIELDS:
            with self.subTest(field=field),self.assertRaisesRegex(native.Refused,'shape'):
                native.validate(dict(self.target,**{field:{'unexpected':'object'}}))
        self.assertEqual(self.backend.calls,[])

    def test_no_secrets_or_command_content_in_audit(self):
        h,s=self.host();self.dispatch(h,s,self.signed(h))
        text=self.st.controls_path.read_text()
        self.assertNotIn('fixture.timer',text)
        self.assertNotIn('config',text)


class Inventory(unittest.TestCase):
    def test_system_timer_metadata_and_fixed_read_commands(self):
        calls=[]
        def run(argv):
            calls.append(argv)
            if 'list-timers' in argv:return json.dumps([dict(unit='fixture.timer',activates='fixture.service',next=2000000000000000)])
            return 'Id=fixture.timer\nActiveState=active\n\nId=fixture.service\nResult=exit-code\nExecStart=private-token\n'
        rows=systasks.scan_systemd(run,system=True)
        self.assertEqual(rows[0]['scope'],'system')
        self.assertEqual(rows[0]['last_result'],'exit-code')
        self.assertNotIn('private-token',json.dumps(rows))
        self.assertTrue(all('--user' not in argv for argv in calls))
    def test_cron_anacron_synthetic_directories_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)
            (p/'cron.d').mkdir();(p/'cron.daily').mkdir()
            (p/'crontab').write_text('TOKEN=private-value\n0 1 * * * root /usr/bin/backup --token private-value\n@reboot root /usr/bin/boot --secret private-value\n')
            (p/'anacrontab').write_text('1 5 daily /bin/daily --token private-value\n')
            (p/'cron.d'/'fixture').write_text('0 2 * * * user /bin/job --password private-value\n')
            (p/'cron.daily'/'fixture').write_text('SECRET CONTENT MUST NOT BE READ')
            before={str(f):f.read_bytes() for f in p.rglob('*') if f.is_file()}
            rows=systasks.scan_system_cron(tmp)
            self.assertEqual(len([r for r in rows if r.get('visibility')!='unavailable']),5)
            self.assertNotIn('private-value',json.dumps(rows))
            self.assertTrue(all(not r['control']['supported'] for r in rows))
            self.assertEqual(before,{str(f):f.read_bytes() for f in p.rglob('*') if f.is_file()})
    def test_launchdaemon_synthetic_plist_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'org.fixture.plist'
            p.write_bytes(plistlib.dumps(dict(Label='org.fixture',StartInterval=3600,ProgramArguments=['/bin/job','--token','private-value'],EnvironmentVariables={'TOKEN':'private-value'})))
            rows=systasks.scan_launchd(lambda a:'PID\tStatus\tLabel\n-\t7\torg.fixture\n',directory=tmp,system=True)
            self.assertEqual(rows[0]['scope'],'system')
            self.assertNotIn('private-value',json.dumps(rows))
    def test_windows_full_path_all_users_and_microsoft(self):
        header=list(systasks._WIN_COLS)
        # Named header lookup covers localized-column fallback separately in existing P207 suite.
        output=io.StringIO();wr=csv.writer(output);wr.writerow(header+['extra']*8)
        values={k:'' for k in header};values.update({'TaskName':'\\Microsoft\\Fixture','Last Result':'5','Run As User':'other-user','Scheduled Task State':'Ready'})
        wr.writerow([values[k] for k in header]+['private-value']*8)
        rows=systasks.parse_schtasks(output.getvalue(),user='different',all_users=True)
        self.assertEqual(rows[0]['id'],'\\Microsoft\\Fixture')
        self.assertEqual(rows[0]['last_result'],'5')
        self.assertNotIn('private-value',json.dumps(rows))
    def test_windows_snapshot_hides_arguments_and_detects_definition_change(self):
        xml='<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings><Enabled>true</Enabled></Settings><Actions><Exec><Command>job.exe</Command><Arguments>private-value</Arguments></Exec></Actions></Task>'
        b=native.Backend(run=lambda argv:xml,platform='win32',home='/work/.qa369')
        target=dict(platform='win32',source='schtasks',scope='all-users',id='\\Folder\\Fixture',snapshot='0'*64,op='disable')
        snap=b.snapshot(target)
        self.assertNotIn('private-value',json.dumps(snap))
        b.run=lambda argv:xml.replace('private-value','new-value')
        self.assertNotEqual(snap['snapshot'],b.snapshot(target)['snapshot'])
    def test_platform_mismatch(self):
        target=dict(platform='linux',source='systemd',scope='user',id='a.timer',snapshot='0'*64,op='disable')
        with self.assertRaisesRegex(native.Refused,'platform_changed'):
            native.Backend(run=lambda argv:None,platform='darwin',home='/work/.qa369').snapshot(target)



class BackendSnapshots(unittest.TestCase):
    def test_systemd_exact_file_identity_and_no_exec_or_environment_queries(self):
        calls=[]
        def run(argv):
            calls.append(argv)
            if argv[-1]=='fixture.timer':
                return 'Id=fixture.timer\nLoadState=loaded\nUnitFileState=enabled\nActiveState=active\nFragmentPath=/etc/systemd/system/fixture.timer\nUnit=fixture.service\nTimersCalendar={ OnCalendar=*-*-* 01:00:00 ; next_elapse=tomorrow }\n'
            return 'Id=fixture.service\nFragmentPath=/etc/systemd/system/fixture.service\n'
        b=native.Backend(run=run,platform='linux',home='/work/.qa369')
        target=dict(platform='linux',source='systemd',scope='system',id='fixture.timer',snapshot='0'*64,op='disable')
        identities=lambda path,roots:[path,1,2,3,4,5]
        with patch.object(native,'_file_identity',side_effect=identities):
            snap=b.snapshot(target)
        self.assertTrue(snap['loaded'] and snap['enabled'])
        self.assertEqual(len(calls),2)
        self.assertNotIn('ExecStart',str(calls))
        self.assertNotIn('Environment',str(calls))
        with patch.object(native,'_file_identity',side_effect=lambda path,roots:[path,1,2,3,6,7]):
            self.assertNotEqual(snap['snapshot'],b.snapshot(target)['snapshot'])
    def test_launchd_user_fixture_disable_restore_with_real_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp)/'Library/LaunchAgents';folder.mkdir(parents=True)
            path=folder/'org.fixture.plist'
            original=plistlib.dumps(dict(Label='org.fixture',StartInterval=30,EnvironmentVariables={'TOKEN':'private-value'}))
            path.write_bytes(original)
            state={'enabled':True,'loaded':True};calls=[]
            def run(argv):
                calls.append(argv)
                if argv[1]=='print-disabled':return '"org.fixture" => '+('false' if state['enabled'] else 'true')
                if argv[1]=='list':return 'PID\tStatus\tLabel\n'+('-\t0\torg.fixture\n' if state['loaded'] else '')
                if argv[1] in ('disable','enable'):state['enabled']=argv[1]=='enable'
                if argv[1] in ('bootout','bootstrap'):state['loaded']=argv[1]=='bootstrap'
                return ''
            backend=native.Backend(run=run,platform='darwin',home=tmp,uid=501)
            st=FakeState(tmp);ctl=native.Controller(st,backend)
            target=dict(platform='darwin',source='launchd',scope='user',id='org.fixture',snapshot='0'*64,op='disable')
            target['snapshot']=backend.snapshot(target)['snapshot']
            ctl.apply(target,lambda:None)
            self.assertEqual(state,{'enabled':False,'loaded':False})
            target.update(op='restore',snapshot=backend.snapshot(target)['snapshot'])
            ctl.apply(target,lambda:None)
            self.assertEqual(state,{'enabled':True,'loaded':True})
            self.assertEqual(path.read_bytes(),original)
            self.assertNotIn('private-value',st.controls_path.read_text() if st.controls_path.exists() else '')
            self.assertTrue(any(argv[1]=='bootstrap' for argv in calls))
    def test_launchdaemon_no_implicit_privilege(self):
        backend=native.Backend(run=lambda argv:self.fail('must not query another launchctl domain'),platform='darwin',home='/work/.qa369',uid=501)
        target=dict(platform='darwin',source='launchd',scope='system',id='org.fixture',snapshot='0'*64,op='disable')
        with self.assertRaisesRegex(native.Refused,'read_only'):backend.snapshot(target)
    def test_file_snapshot_rejects_symlink_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);p=root/'fixture';p.write_text('fixture')
            link=root/'link';link.symlink_to(p)
            with self.assertRaisesRegex(native.Refused,'read_only'):native._file_identity(str(link),[tmp])
            with self.assertRaisesRegex(native.Refused,'read_only'):native._file_identity('/elsewhere/fixture',[tmp])
    def test_permissions_are_explicitly_not_visible(self):
        row=systasks.scan_systemd(lambda argv:None,system=True)[0]
        self.assertEqual(row['visibility'],'unavailable')
        self.assertFalse(row['control']['supported'])

class AllPlatformRecovery(unittest.TestCase):
    def test_launchdaemon_system_directory_simulated_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'org.fixture.plist'
            original=plistlib.dumps(dict(Label='org.fixture',StartInterval=60))
            path.write_bytes(original)
            state={'enabled':True,'loaded':True}
            def run(argv):
                if argv[1]=='print-disabled':return '"org.fixture" => '+('false' if state['enabled'] else 'true')
                if argv[1]=='list':return 'PID\tStatus\tLabel\n'+('-\t0\torg.fixture\n' if state['loaded'] else '')
                if argv[1] in ('disable','enable'):state['enabled']=argv[1]=='enable'
                if argv[1] in ('bootout','bootstrap'):state['loaded']=argv[1]=='bootstrap'
                return ''
            backend=native.Backend(run=run,platform='darwin',home=tmp,uid=0,launchd_system_directory=tmp)
            ctl=native.Controller(FakeState(tmp),backend)
            target=dict(platform='darwin',source='launchd',scope='system',id='org.fixture',snapshot='0'*64,op='disable')
            target['snapshot']=backend.snapshot(target)['snapshot']
            ctl.apply(target,lambda:None)
            self.assertFalse(state['enabled'] or state['loaded'])
            target.update(op='restore',snapshot=backend.snapshot(target)['snapshot'])
            ctl.apply(target,lambda:None)
            self.assertTrue(state['enabled'] and state['loaded'])
            self.assertEqual(path.read_bytes(),original)
    def test_windows_real_backend_fake_fixed_runner_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            enabled=[True];calls=[]
            def run(argv):
                calls.append(argv)
                if '/query' in argv:
                    return '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings><Enabled>'+str(enabled[0]).lower()+'</Enabled></Settings><Actions><Exec><Command>job.exe</Command><Arguments>private-value</Arguments></Exec></Actions></Task>'
                enabled[0]='/enable' in argv
                return ''
            backend=native.Backend(run=run,platform='win32',home=tmp)
            ctl=native.Controller(FakeState(tmp),backend)
            target=dict(platform='win32',source='schtasks',scope='all-users',id='\\Folder\\Fixture',snapshot='0'*64,op='disable')
            target['snapshot']=backend.snapshot(target)['snapshot']
            ctl.apply(target,lambda:None)
            self.assertFalse(enabled[0])
            target.update(op='restore',snapshot=backend.snapshot(target)['snapshot'])
            ctl.apply(target,lambda:None)
            self.assertTrue(enabled[0])
            self.assertEqual([a for a in calls if '/change' in a],[['schtasks','/change','/tn','\\Folder\\Fixture','/disable'],['schtasks','/change','/tn','\\Folder\\Fixture','/enable']])


class AsyncRevocation(unittest.IsolatedAsyncioTestCase):
    async def test_device_removed_during_native_runner_no_followup_effect(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            st=FakeState(tmp);backend=FakeBackend(enabled=False,loaded=False);ctl=native.Controller(st,backend)
            original=dict(platform='linux',source='systemd',scope='user',id='fixture.timer',op='restore',snapshot='0'*64)
            # A previous owner disable had an enabled and active original.
            snap=backend.snapshot(original)
            ctl.st.write_private(ctl.path,json.dumps({ctl.key(original):{'original':dict(snap,enabled=True,loaded=True),'pending':None}}).encode())
            original['snapshot']=snap['snapshot']
            session=NS(state='ready',device='device',pub=b'fixture',cid='cid')
            host=NS(st=st,channel='fixture-channel',sessions={'cid':session},nonces=controls.Nonces(),native_scheduler_controller=ctl,_ctl_res=AsyncMock())
            msg=dict(t=native.ACTION,r='1'*32,n='b'*32,ts=int(time.time()*1000),**original)
            msg['sig']=wire.b64u(st.key.sign(controls.signed_message(host.channel,'device',native.ACTION,msg['n'],msg['ts'],controls.object_digest(native.ACTION,original))))
            entered,release=threading.Event(),threading.Event()
            def before():
                entered.set()
                release.wait(2)
            backend.before=before
            task=asyncio.create_task(native.on_phone(host,session,msg))
            try:
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                host.sessions.clear();st.allowed=False
            finally:release.set()
            await task
            self.assertEqual(len(backend.calls),1)
            self.assertFalse(backend.loaded)
            self.assertTrue(ctl.journal())
            self.assertFalse(host._ctl_res.call_args.args[3])



class RecoveryDiscovery(unittest.TestCase):
    setUp = NativeControls.setUp
    refresh = NativeControls.refresh
    def test_unloaded_object_remains_restorable_in_catalogue(self):
        self.controller.apply(self.target,lambda:None)
        rows=self.controller.restore_rows([])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],'fixture.timer')
        self.assertEqual(self.controller.describe(rows[0])['target']['op'],'restore')
    def test_journal_identity_not_duplicated_by_scanner(self):
        self.controller.apply(self.target,lambda:None)
        rows=self.controller.restore_rows([dict(src='systemd',scope='user',id='fixture.timer')])
        self.assertEqual(len(rows),1)

if __name__=='__main__':unittest.main()
