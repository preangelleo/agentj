import _hermetic
import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import time
import unittest
import zipfile
from unittest.mock import Mock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import auto_update as au, controls, preferences, update, __version__
from agentj.state import State
from agentj.serve import Host

class Nightly(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ, {'HOME':self.tmp.name,'AGENTJ_STATE_DIR':self.tmp.name+'/st','XDG_CONFIG_HOME':self.tmp.name+'/cfg','TZ':'UTC'})
        env.start();self.addCleanup(env.stop);time.tzset();self.addCleanup(time.tzset)
        self.st=State();self.st.init()
        self.now=time.mktime((2026,10,9,3,0,0,0,0,-1))
        au.touch(self.st,self.now-1801)
    def due(self,now=None,**kw):return au.due(self.st,now=self.now if now is None else now,rand=lambda _:0,**kw)
    def test_default_on_and_explicit_off_survives(self):
        self.assertTrue(au.enabled(self.st))
        for initial,command in ((True,'off'),(False,'on')):
            # Arrange opposite effective values; a no-op is not evidence of an authorized change.
            path=preferences.ensure()
            path.write_text(preferences.edit(path.read_text(),'updates.auto_install',initial))
            self.assertEqual(au.enabled(self.st),initial)
            before=path.read_bytes()
            with patch('builtins.print'):
                self.assertEqual(au.command([command]),3)
            self.assertEqual(path.read_bytes(),before,'offline never changes a UV-bound key')
            self.assertEqual(au.enabled(self.st),initial)
        self.assertFalse(au.enabled(self.st));self.assertFalse(self.due())
        self.assertFalse(au.read(self.st).get('attempted',False))
    def test_window_random_persisted_and_at_most_once(self):
        self.assertFalse(au.due(self.st,now=self.now-1,rand=lambda _:123))
        self.assertEqual(au.read(self.st)['second'],10923)
        self.assertTrue(au.due(self.st,now=self.now+123,rand=Mock(side_effect=AssertionError)))
        self.assertFalse(self.due(self.now+124))
    def test_busy_waits_for_idle_within_the_same_night(self):
        self.assertFalse(self.due(blocked=True))
        self.assertEqual(au.read(self.st)['decision'],'busy')
        self.assertFalse(au.read(self.st)['attempted'])
        self.assertFalse(self.due(self.now+30,blocked=True))
        self.assertTrue(self.due(self.now+1800))
        self.assertFalse(self.due(self.now+1830))
        self.assertTrue(self.due(self.now+86400))

    def test_recent_phone_waits_until_idle_but_never_past_five(self):
        au.touch(self.st,self.now-60)
        self.assertFalse(self.due())
        self.assertTrue(self.due(self.now+au.IDLE))
        self.assertFalse(self.due(self.now+au.IDLE+30))
        tomorrow=self.now+86400
        self.assertFalse(self.due(tomorrow,blocked=True))
        self.assertFalse(self.due(tomorrow+7200))
        self.assertEqual(au.read(self.st)['decision'],'window_missed')

    def test_window_deferral_records_only_reason_changes(self):
        with patch('agentj.activity.record') as record:
            self.assertFalse(self.due(blocked=True))
            self.assertFalse(self.due(self.now+30,blocked=True))
            self.assertEqual(record.call_count,1)
            self.assertFalse(self.due(self.now+60,stopped=True))
            self.assertEqual(record.call_count,2)
    def test_stopped_and_recent_phone_skip(self):
        self.assertFalse(self.due(stopped=True));self.assertEqual(au.read(self.st)['decision'],'stopped')
        au.touch(self.st,self.now+86400-1799)
        self.assertFalse(self.due(self.now+86400));self.assertEqual(au.read(self.st)['decision'],'phone_recent')
    def test_after_window_missed_and_first_start_not_assumed_idle(self):
        self.assertFalse(self.due(self.now+7200));self.assertEqual(au.read(self.st)['decision'],'window_missed')
        (self.st.root/au.RECORD).unlink()
        self.assertFalse(self.due());self.assertEqual(au.read(self.st)['decision'],'phone_recent')
    def test_local_timezone_window(self):
        os.environ['TZ']='Asia/Tokyo';time.tzset()
        now=time.mktime((2026,10,10,3,0,0,0,0,-1));au.touch(self.st,now-1801)
        self.assertTrue(self.due(now))
    def test_busy_covers_agent_queue_approvals_friends_tasks(self):
        h=Host(self.st);self.assertFalse(au.busy(h))
        h.agent=Mock(status='working');self.assertTrue(au.busy(h));h.agent=None
        h.scheduler.current_id='task';self.assertTrue(au.busy(h));h.scheduler.current_id=None
        h.elevate.cards['x']={};self.assertTrue(au.busy(h));h.elevate.cards.clear()
        h.peers=Mock(sessions=Mock(active=1,_procs=set()),jobs=set(),asks={});self.assertTrue(au.busy(h))
        h.peers.sessions.active=0;h.peers.asks['x']={};self.assertTrue(au.busy(h))
        h.peers=None;h.agent=Mock(status='idle',q=asyncio.Queue());h.agent.q.put_nowait('queued');self.assertTrue(au.busy(h))
    def test_result_retry_does_not_rearm_consumed_notice(self):
        result = {'attempt': 'same-worker', 'result': 'ok', 'from': '0.16.4a1', 'to': __version__, 'reason': 'upgraded'}
        au.finish(self.st, result)
        self.assertIsNotNone(au.take_notice(self.st))
        with patch('agentj.activity.record') as record:
            au.finish(self.st, result)
            record.assert_not_called()
        self.assertIsNone(au.take_notice(self.st))
        au.finish(self.st, {**result, 'attempt': 'another-night'})
        self.assertIsNotNone(au.take_notice(self.st))

    def test_completed_nightly_job_preserves_manual_recovery_notice(self):
        from agentj import service_recovery, update
        au.write(self.st, {'phase': 'done'}, au.JOB)
        rc = service_recovery.run(self.st, __version__, install=lambda st: None, status=lambda: {'active': 'active'})
        self.assertEqual(rc, 0)
        self.assertTrue((self.st.root / update.UPGRADED).exists())

    def test_result_one_notice_survives_history_off_and_doctor_reads(self):
        au.finish(self.st,{'result':'ok','from':'0.16.4a1','to':__version__,'reason':'upgraded'})
        self.assertIn('我夜里自己升级到',au.take_notice(self.st));self.assertIsNone(au.take_notice(self.st))
        self.assertEqual(au.read(self.st)['result']['to'],__version__)
        self.assertEqual((self.st.root/au.RECORD).stat().st_mode & 0o777,0o600)

class Artifacts(Nightly):
    def signed(self,version='99.0.0a1'):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr(f'agentj-{version}.dist-info/METADATA',f'Name: agentj\nVersion: {version}\n')
            z.writestr('fixture-version',version)
        body=buf.getvalue();digest=hashlib.sha256(body).hexdigest()
        k=Ed25519PrivateKey.generate();kid=b'abcdefgh';sig=k.sign(hashlib.blake2b(body,digest_size=64).digest())
        comment=f'agentj-host/v1 version={version} sha256={digest}'
        signature='untrusted comment: fixture\n'+base64.b64encode(b'ED'+kid+sig).decode()+'\ntrusted comment: '+comment+'\n'+base64.b64encode(k.sign(sig+comment.encode())).decode()+'\n'
        keyring={au.minisign.key_id_hex(kid):base64.b64encode(b'Ed'+kid+k.public_key().public_bytes_raw()).decode()}
        return body,digest,signature,keyring
    def test_signature_and_hash_cache_tampering_rejected(self):
        body,digest,sig,keys=self.signed()
        def fetch(url,_):return (digest+'\n').encode() if url.endswith('.sha256') else sig.encode() if url.endswith('.minisig') else body
        with patch.object(au.minisign,'keyring',lambda role,_k=keys:_k):
            p=au.cached_wheel(self.st,'99.0.0a1',fetch=fetch)
            p.write_bytes(b'tampered')
            with self.assertRaises(ValueError):au.cached_wheel(self.st,'99.0.0a1',fetch=lambda url,n:b'tampered' if url.endswith('.whl') else fetch(url,n))
        self.assertEqual(p.name,'agentj-99.0.0a1-py3-none-any.whl')
    def test_forged_cache_receipt_cannot_replace_signature(self):
        body,digest,sig,keys=self.signed()
        def fetch(url,_):return (digest+'\n').encode() if url.endswith('.sha256') else sig.encode() if url.endswith('.minisig') else body
        with patch.object(au.minisign,'keyring',lambda role,_k=keys:_k):
            p=au.cached_wheel(self.st,'99.0.0a1',fetch=fetch)
            p.write_bytes(b'forged wheel')
            p.with_suffix('.verified.json').write_text(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'signed':True}))
            calls=[]
            def restore(url,n):calls.append(url);return fetch(url,n)
            self.assertEqual(au.cached_wheel(self.st,'99.0.0a1',fetch=restore).read_bytes(),body)
            self.assertTrue(calls,'forged receipt must force a fresh signature verification')
            self.assertEqual(au.cached_wheel(self.st,'99.0.0a1',fetch=lambda *a: self.fail('valid signed cache should work offline')).read_bytes(),body)

    def test_wrong_signature_and_missing_signature_refused(self):
        body,digest,sig,keys=self.signed()
        def fetch(url,_):return (digest+'\n').encode() if url.endswith('.sha256') else sig.encode() if url.endswith('.minisig') else body
        with self.assertRaises(au.minisign.MinisignError):au.cached_wheel(self.st,'99.0.0a1',fetch=fetch)
        def missing(url,n):
            if url.endswith('.minisig'):raise OSError('not published')
            return fetch(url,n)
        with self.assertRaises(OSError):au.cached_wheel(self.st,'99.0.0a1',fetch=missing)

class SharedVerification(unittest.TestCase):
    def test_manual_and_phone_apply_refuse_artifact_before_install(self):
        st=Mock()
        with patch.object(update,'writable',return_value=True),patch.object(update,'verified_plan',side_effect=ValueError('wrong signature')),patch.object(update,'install_kind',return_value={'kind':'uv','where':'/fixture'}):
            runner=Mock()
            r=update.apply(st,check_fn=lambda:{'status':'newer','latest':'9.9.9'},run=runner,svc_on=False)
            self.assertEqual(r['reason'],'signature_or_hash_mismatch');runner.assert_not_called()
    def test_upgrade_doctor_does_not_probe_owner_workflows_or_network(self):
        from agentj import doctor, main_identity
        with tempfile.TemporaryDirectory() as td:
            st=State(Path(td));st.init()
            with patch.object(doctor,'check_tasks',side_effect=AssertionError('owner workflow')),patch.object(doctor,'check_asr',side_effect=AssertionError('ASR')),patch.object(doctor,'check_relay',side_effect=AssertionError('network')),patch.object(doctor.service,'status',return_value={'installed':False}),patch.object(doctor.service,'legacy_status',return_value={'installed':False}),patch.object(doctor,'check_serve',return_value={'id':'serve','status':'ok'}):
                ids={r['id'] for r in doctor.run_upgrade(st)}
            self.assertNotIn('tasks',ids);self.assertNotIn('asr',ids);self.assertNotIn('relay',ids)
            self.assertIn('main-core',ids)
    def test_candidate_signed_wheel_and_summary(self):
        root=Path(__file__).resolve().parents[2]
        # A source unit run may precede candidate construction; this assertion is
        # only made against the matching candidate artifact, never a stale wheel.
        p=root/'site/public/dl'/f'agentj-{__version__}-py3-none-any.whl'
        if not p.exists():self.skipTest('candidate artifact not constructed yet')
        signature=Path(str(p)+'.minisig')
        self.assertTrue(signature.is_file(),'candidate wheel signature is mandatory')
        digest=hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(au.minisign.verify(p.read_bytes(),signature.read_text()),f'agentj-host/v1 version={__version__} sha256={digest}')
        with zipfile.ZipFile(p) as z:
            summary=json.loads(z.read('agentj/release-summary.json'))
        self.assertEqual(summary['version'],__version__)
        for lang in ('zh','en'):
            self.assertIsInstance(summary[lang],str)
            self.assertTrue(0<len(summary[lang])<=160)
            self.assertFalse(any(ord(c)<32 for c in summary[lang]))

class Worker(Nightly):
    def fixtures(self):
        paths={}
        for v in (__version__,'99.0.0a1'):
            p=self.st.root/f'agentj-{v}-py3-none-any.whl'
            body,digest,sig,keys=Artifacts.signed(self,v);self.fixture_keys=keys
            p.write_bytes(body)
            p.with_suffix('.verified.json').write_text(json.dumps({'sha256':digest,'signature':sig}));paths[v]=p
        return paths
    def run_worker(self, fail=False, journal=None):
        paths=self.fixtures();self.installed=[];self.restarted=[]
        if journal:au.write(self.st, {**journal,'from':__version__,'to':'99.0.0a1','old':str(paths[__version__]),'new':str(paths['99.0.0a1']),'install':{'kind':'pip','where':self.tmp.name},'argv':['fixture-cli']},au.JOB)
        def runner(argv):
            if '--version' in argv:return Mock(returncode=0,stdout='agentj '+(self.installed[-1] if self.installed else __version__))
            if 'doctor' in argv:return Mock(returncode=0,stdout=json.dumps({'checks':[{'id':'main-core','status':'ok'}]}))
            with zipfile.ZipFile(argv[-1]) as z:v=z.read('fixture-version').decode()
            self.installed.append(v)
            return Mock(returncode=1 if fail and v=='99.0.0a1' else 0)
        with patch.object(au.minisign,'keyring',lambda role,_k=self.fixture_keys:_k),patch.object(update,'install_kind',return_value={'kind':'pip','where':self.tmp.name}),patch.object(update,'new_argv',return_value=['fixture-cli']):
            return au.run(self.st,check_fn=lambda:{'status':'newer','latest':'99.0.0a1'},cache=lambda st,v,**kw:paths[v],runner=runner,restart=self.restarted.append)
    def test_full_install_verify_restart_and_persisted_result(self):
        r=self.run_worker();self.assertEqual(r['result'],'ok');self.assertEqual(self.installed,['99.0.0a1']);self.assertEqual(self.restarted,['99.0.0a1'])
        self.assertEqual(au.read(self.st,au.JOB)['phase'],'done')
    def test_upgrade_failed_restores_cached_old_wheel(self):
        r=self.run_worker(fail=True);self.assertEqual(r['result'],'rolled_back');self.assertEqual(self.installed,['99.0.0a1',__version__]);self.assertEqual(self.restarted,[__version__])
        self.assertIn('已回滚',au.take_notice(self.st));self.assertIsNone(au.take_notice(self.st))
    def test_worker_killed_during_install_resumes_by_rollback(self):
        r=self.run_worker(journal={'phase':'installing'});self.assertEqual(r['result'],'rolled_back');self.assertEqual(self.installed,[__version__])
    def test_worker_killed_after_install_finishes_verification(self):
        r=self.run_worker(journal={'phase':'installed'})
        # fixture version check reports old without install; rejects and restores old
        self.assertEqual(r['result'],'rolled_back')
    def test_off_or_estop_no_execution(self):
        with patch.object(au,'enabled',return_value=False):
            self.assertIsNone(self.run_worker());self.assertFalse(self.installed)
        controls.set_estop(self.st,True,'terminal');self.assertIsNone(self.run_worker());self.assertFalse(self.installed)
    def test_no_newer_no_execution(self):
        with patch.object(update,'install_kind') as install:
            self.assertIsNone(au.run(self.st,check_fn=lambda:{'status':'current'}));install.assert_not_called()
    def test_checkout_never_modified(self):
        with self.assertRaises(ValueError):au.install_commands({'kind':'checkout','where':'/owner/project'},'/unused')
    def test_manager_retry_and_snapshot_not_dependent_on_installed_module(self):
        with patch('agentj.service_recovery.launch_job') as launch:
            self.assertTrue(au.launch(self.st));self.assertFalse(au.launch(self.st))
            args=launch.call_args;self.assertTrue(args.kwargs['restart']);self.assertEqual(args.args[2],'autoupdate')
            self.assertTrue((Path(args.args[1][1]).parent/'agentj/auto_update.py').is_file())

class NightlyInstallJourney(Nightly):
    @unittest.skipUnless(shutil.which('uv'), 'isolated wheel installer requires uv')
    def test_busy_night_reaches_real_isolated_wheel_install_once(self):
        target=Path(self.tmp.name)/'fixture-venv'
        subprocess.run([sys.executable,'-m','venv','--without-pip',str(target)],check=True,capture_output=True)
        paths={}; keys={}
        for version in (__version__,'99.0.0a1'):
            body=io.BytesIO()
            with zipfile.ZipFile(body,'w') as z:
                dist=f'agentj-{version}.dist-info'
                z.writestr(dist+'/METADATA',f'Metadata-Version: 2.1\nName: agentj\nVersion: {version}\n')
                z.writestr(dist+'/WHEEL','Wheel-Version: 1.0\nGenerator: isolated-nightly-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                z.writestr('nightly_fixture.py',"import json,sys\nprint(json.dumps({'checks':[{'id':'main-core','status':'ok'}]}) if 'doctor' in sys.argv else 'agentj "+version+"')\n")
                z.writestr(dist+'/RECORD','')
            data=body.getvalue(); digest=hashlib.sha256(data).hexdigest()
            k=Ed25519PrivateKey.generate();kid=version.encode()[:8].ljust(8,b'0')
            sig=k.sign(hashlib.blake2b(data,digest_size=64).digest())
            comment=f'agentj-host/v1 version={version} sha256={digest}'
            signature='untrusted comment: fixture\n'+base64.b64encode(b'ED'+kid+sig).decode()+'\ntrusted comment: '+comment+'\n'+base64.b64encode(k.sign(sig+comment.encode())).decode()+'\n'
            keys[au.minisign.key_id_hex(kid)]=base64.b64encode(b'Ed'+kid+k.public_key().public_bytes_raw()).decode()
            wheel=self.st.root/f'agentj-{version}-py3-none-any.whl';wheel.write_bytes(data)
            wheel.with_suffix('.verified.json').write_text(json.dumps({'sha256':digest,'signature':signature}))
            paths[version]=wheel
        info={'kind':'pip','where':str(target)}
        argv=[str(target/'bin/python'),'-m','nightly_fixture']; launched=[]; restarted=[]
        runner=lambda cmd:subprocess.run(cmd,capture_output=True,text=True,timeout=60)
        self.assertFalse(self.due(blocked=True))
        with patch.object(update,'install_kind',return_value=info),patch.object(update,'new_argv',return_value=argv),patch.object(au.minisign,'keyring',lambda role:keys):
            for seconds in (1800,1830,1860):
                if self.due(self.now+seconds,newer=getattr(self,'known_newer',False)):
                    launched.append(seconds)
                    if au.read(self.st,au.JOB).get('phase')=='done':
                        (self.st.root/au.JOB).unlink()  # same completed-job removal as Host.update_loop
                    result=au.run(self.st,check_fn=lambda:{'status':'newer','latest':'99.0.0a1'},cache=lambda st,v,**kw:paths[v],runner=runner,restart=restarted.append)
                    self.assertEqual(result['result'],'ok')
        self.assertEqual(launched,[1800]);self.assertEqual(restarted,['99.0.0a1'])
        self.assertEqual(runner(argv+['--version']).stdout.strip(),'agentj 99.0.0a1')
        self.assertIsNone(au.take_notice(self.st), 'old running host must not claim the new version')
        with patch.object(au,'__version__','99.0.0a1'):
            self.assertIn('99.0.0a1',au.take_notice(self.st))
            self.assertIsNone(au.take_notice(self.st))

class KnownNewerNightlyJourney(NightlyInstallJourney):
    known_newer=True

    def test_idle_opportunity_before_three_does_not_relaunch_at_random_night_time(self):
        self.assertTrue(self.due(self.now-1,newer=True))
        self.assertFalse(self.due(self.now,newer=True))
        self.assertFalse(self.due(self.now+30,newer=True))

    def test_no_newer_at_night_preserves_automatic_daytime_new_release(self):
        self.assertTrue(self.due())
        request = au.read(self.st)['schedule']['id']
        with patch.dict(os.environ, {au.SCHEDULE_ENV: request}):
            from agentj import phone_update
            self.assertTrue(phone_update.reserve(self.st, holder=request))
            self.assertIsNone(au.run(self.st,check_fn=lambda:{'status':'current'}))
        self.assertTrue(self.due(self.now+9*3600,newer=True))
        self.assertFalse(self.due(self.now+9*3600+30,newer=True))

    def test_worker_reservation_only_blocks_while_held(self):
        from agentj import phone_update
        self.assertTrue(phone_update.reserve(self.st))
        self.assertFalse(phone_update.reserve(self.st))
        phone_update.release(self.st)
        self.assertTrue(phone_update.reserve(self.st))
        phone_update.release(self.st)

class DispatchRecovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'HOME': tmp.name, 'AGENTJ_STATE_DIR': tmp.name + '/state',
                                     'XDG_CONFIG_HOME': tmp.name + '/config', 'TZ': 'UTC'})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(time.tzset)
        time.tzset()
        self.st = State()
        self.st.init()
        self.now = time.mktime((2026, 11, 26, 3, 0, 0, 0, 0, -1))
        au.touch(self.st, self.now - au.IDLE)

    def due(self, elapsed=0, **options):
        return au.due(self.st, now=self.now + elapsed, rand=lambda _: 0, **options)

    def test_reservation_failure_retries_once_and_waits_for_idle(self):
        from agentj import phone_update
        with patch('time.time', return_value=self.now), patch('agentj.service_recovery.launch_job') as launch:
            self.assertTrue(phone_update.reserve(self.st))
            self.assertTrue(self.due())
            self.assertFalse(au.launch(self.st))
            self.assertEqual(au.read(self.st)['decision'], 'lease_busy')
            self.assertFalse(au.read(self.st)['attempted'])
            launch.assert_not_called()
            phone_update.release(self.st)
        self.assertFalse(self.due(299))
        self.assertFalse(self.due(300, blocked=True))
        self.assertFalse(self.due(330, stopped=True))
        self.assertTrue(self.due(360))
        self.assertFalse(self.due(361))

    def test_missing_worker_recovers_and_old_request_cannot_enter(self):
        self.assertTrue(self.due())
        abandoned = au.read(self.st)['schedule']['id']
        now = au.DISPATCH_GRACE + au.DISPATCH_BACKOFF[0]
        self.assertFalse(self.due(au.DISPATCH_GRACE))
        self.assertEqual(au.read(self.st)['decision'], 'launch_failed')
        self.assertTrue(self.due(now))
        newer = au.read(self.st)['schedule']['id']
        self.assertNotEqual(abandoned, newer)
        check = Mock(side_effect=AssertionError('stale worker must not check or install'))
        with patch.dict(os.environ, {au.SCHEDULE_ENV: abandoned}):
            self.assertIsNone(au.run(self.st, check_fn=check))
        check.assert_not_called()
        self.assertEqual(au.read(self.st)['schedule']['id'], newer)

    def test_unavailable_index_retries_but_disabled_and_stop_cannot_launch(self):
        self.assertTrue(self.due())
        request = au.read(self.st)['schedule']['id']
        with patch.dict(os.environ, {au.SCHEDULE_ENV: request}), patch('time.time', return_value=self.now):
            from agentj import phone_update
            self.assertTrue(phone_update.reserve(self.st, holder=request))
            self.assertIsNone(au.run(self.st, check_fn=lambda: {'status': 'unknown', 'why': 'network'}))
        self.assertEqual(au.read(self.st)['decision'], 'check_unavailable')
        self.assertFalse(self.due(300, stopped=True))
        with patch.object(au, 'enabled', return_value=False):
            self.assertFalse(self.due(330))
            self.assertFalse(au.launch(self.st))
        self.assertTrue(self.due(360))

    def test_lease_is_exclusive_and_a_late_worker_cannot_release_its_successor(self):
        from concurrent.futures import ThreadPoolExecutor
        from agentj import phone_update
        holders = ['synthetic-a', 'synthetic-b']
        with patch('time.time', return_value=self.now), ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(lambda holder: phone_update.reserve(self.st, holder=holder), holders))
        self.assertEqual(outcomes.count(True), 1)
        original = holders[outcomes.index(True)]
        with patch('time.time', return_value=self.now + phone_update.LEASE + 1):
            self.assertTrue(phone_update.reserve(self.st, holder='synthetic-new'))
            phone_update.release(self.st, holder=original)
            phone_update.release(self.st)
            self.assertTrue(phone_update.holds(self.st, 'synthetic-new'))
            phone_update.release(self.st, holder='synthetic-new')
            self.assertFalse(phone_update.busy(self.st))

    def test_numbered_instances_share_one_installation_lease(self):
        from agentj import instance, phone_update
        primary, secondary = State(instance.state_root(1)), State(instance.state_root(2))
        with patch('agentj.instance.current', return_value=2) as number:
            self.assertTrue(phone_update.reserve(secondary, holder='secondary'))
            number.return_value = 1
            self.assertTrue(phone_update.busy(primary))
            self.assertFalse(phone_update.reserve(primary, holder='primary'))
            phone_update.release(primary, holder='primary')
            self.assertTrue(phone_update.holds(primary, 'secondary'))
            number.return_value = 2
            phone_update.release(secondary, holder='secondary')
            number.return_value = 1
            self.assertTrue(phone_update.reserve(primary, holder='primary'))

    def test_only_lowest_present_instance_schedules_when_primary_is_absent(self):
        with patch.object(au, '_primary_present', return_value=False), \
                patch('agentj.instance.exists', side_effect=lambda n: n in (2, 3)), \
                patch('agentj.instance.current', return_value=2) as current:
            self.assertTrue(au.schedules_installs(self.st))
            current.return_value = 3
            self.assertFalse(au.schedules_installs(self.st))

    def test_install_budget_is_bound_to_the_target_and_local_date(self):
        self.assertTrue(self.due())
        request = au.read(self.st)['schedule']['id']
        with patch('time.time', return_value=self.now):
            self.assertTrue(au._start_scheduled_install(self.st, request, '99.0.0'))
            self.assertFalse(au._start_scheduled_install(self.st, request, '99.0.0'))
            self.assertFalse(au._start_scheduled_install(self.st, 'expired', '99.0.1'))
            self.assertTrue(au._start_scheduled_install(self.st, request, '99.0.1'))
        with patch('time.time', return_value=self.now+86400):
            self.assertTrue(au._start_scheduled_install(self.st, request, '99.0.0'))


if __name__=='__main__':unittest.main()


class SignedAutoInstallConfig(unittest.IsolatedAsyncioTestCase):
    """Auto off/on persists only after a real paired signature and fresh synthetic WebAuthn UV."""
    async def asyncSetUp(self):
        from _p541_device import Phone, _owner_host
        from test_l1 import _state, _ready
        from agentj import approval_review, approval_jev, provenance
        from types import SimpleNamespace
        tmp=tempfile.TemporaryDirectory(prefix='p83-uv-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'XDG_CONFIG_HOME':str(self.root/'config'),
                                  'AGENTJ_STATE_DIR':str(self.root/'s')})
        env.start();self.addCleanup(env.stop)
        self.st=_state(tmp.name);preferences.ensure()
        self.sent=[];self.host=_owner_host(self.st,self.sent);self.host.ask_ttl=5
        self.phone=Phone(self.st,with_passkey=True);self.session=_ready(self.host,self.phone)
        page=self.host.hist_add({'k':'phone','name':'你','text':'Synthetic auto-update setting request.'},'','done')
        origin=approval_review.remember(self.host,page,provenance.phone())
        self.host.agent.cur_send=SimpleNamespace(approval_origin=origin,source=provenance.phone())
        no_jev=patch.object(approval_jev,'available',return_value=False);no_jev.start();self.addCleanup(no_jev.stop)
        self.server=await asyncio.start_unix_server(self.host.on_ctl,str(self.st.sock_path))
        self.addAsyncCleanup(self.cleanup_server)

    async def cleanup_server(self):
        self.server.close();await self.server.wait_closed()
        for ask in list(self.host.asks.values()):
            if not ask.fut.done():ask.fut.set_result(('deny',None,None,None))
        await asyncio.gather(*list(getattr(self.host,'_ctl_bg',())),return_exceptions=True)

    async def wait(self, predicate):
        for _ in range(200):
            if predicate():return
            await asyncio.sleep(.005)
        self.fail('auto-install approval did not reach its bounded fixture state')

    async def request(self,value):
        raw=preferences.edit(preferences.read()[0],'updates.auto_install',value)
        reader,writer=await asyncio.open_unix_connection(str(self.st.sock_path))
        try:
            writer.write((json.dumps({'cmd':'config_apply','raw':raw})+'\n').encode());await writer.drain()
            result=json.loads(await reader.readline())
        finally:
            writer.close();await writer.wait_closed()
        self.assertEqual(result.get('code'),'owner_approval',result)
        await self.wait(lambda:bool(self.host.asks))
        return next(iter(self.host.asks.values()))

    async def test_signed_without_uv_cannot_write_then_fresh_uv_off_on_persists(self):
        for value in (False,True):
            before=preferences.path().read_bytes();ask=await self.request(value)
            self.assertTrue(ask.require_passkey)
            no_uv=self.phone.answer(self.host._ask_msg(ask,self.session),True);no_uv.pop('fa')
            await self.host._answer(self.session,no_uv)
            self.assertFalse(ask.fut.done());self.assertFalse(ask.uv_ok)
            self.assertEqual(preferences.path().read_bytes(),before)
            await self.host._answer(self.session,self.phone.answer(self.host._ask_msg(ask,self.session),True))
            await self.wait(lambda:preferences.get(preferences.read()[1],'updates.auto_install') is value)
            self.assertEqual(preferences.get(self.host.preferences,'updates.auto_install'),value)
            self.assertEqual(au.enabled(self.st),value)
            await self.wait(lambda:not getattr(self.host,'_ctl_bg',()))
