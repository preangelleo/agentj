import _hermetic
import asyncio
import base64
import hashlib
import io
import json
import os
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
        with patch('builtins.print'):
            self.assertEqual(au.command(['off']),0)
        self.assertFalse(au.enabled(self.st));self.assertFalse(self.due())
        self.assertFalse(au.read(self.st).get('attempted',False))
        with patch('builtins.print'):self.assertEqual(au.command(['on']),0)
        self.assertTrue(au.enabled(self.st))
    def test_window_random_persisted_and_at_most_once(self):
        self.assertFalse(au.due(self.st,now=self.now-1,rand=lambda _:123))
        self.assertEqual(au.read(self.st)['second'],10923)
        self.assertTrue(au.due(self.st,now=self.now+123,rand=Mock(side_effect=AssertionError)))
        self.assertFalse(self.due(self.now+124))
    def test_busy_skip_reschedules_next_local_date(self):
        self.assertFalse(self.due(blocked=True));self.assertFalse(self.due(self.now+1800))
        self.assertEqual(au.read(self.st)['decision'],'busy')
        self.assertTrue(self.due(self.now+86400))
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
        self.assertIn('夜里已自动升级',au.take_notice(self.st));self.assertIsNone(au.take_notice(self.st))
        self.assertEqual(au.read(self.st)['result']['to'],__version__)
        self.assertEqual((self.st.root/au.RECORD).stat().st_mode & 0o777,0o600)

class Artifacts(Nightly):
    def signed(self,version='0.16.6a1'):
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
        with patch.object(au.minisign,'TRUSTED_SIGNERS',keys):
            p=au.cached_wheel(self.st,'0.16.6a1',fetch=fetch)
            p.write_bytes(b'tampered')
            with self.assertRaises(ValueError):au.cached_wheel(self.st,'0.16.6a1',fetch=lambda url,n:b'tampered' if url.endswith('.whl') else fetch(url,n))
        self.assertEqual(p.name,'agentj-0.16.6a1-py3-none-any.whl')
    def test_forged_cache_receipt_cannot_replace_signature(self):
        body,digest,sig,keys=self.signed()
        def fetch(url,_):return (digest+'\n').encode() if url.endswith('.sha256') else sig.encode() if url.endswith('.minisig') else body
        with patch.object(au.minisign,'TRUSTED_SIGNERS',keys):
            p=au.cached_wheel(self.st,'0.16.6a1',fetch=fetch)
            p.write_bytes(b'forged wheel')
            p.with_suffix('.verified.json').write_text(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'signed':True}))
            calls=[]
            def restore(url,n):calls.append(url);return fetch(url,n)
            self.assertEqual(au.cached_wheel(self.st,'0.16.6a1',fetch=restore).read_bytes(),body)
            self.assertTrue(calls,'forged receipt must force a fresh signature verification')
            self.assertEqual(au.cached_wheel(self.st,'0.16.6a1',fetch=lambda *a: self.fail('valid signed cache should work offline')).read_bytes(),body)

    def test_wrong_signature_and_missing_signature_refused(self):
        body,digest,sig,keys=self.signed()
        def fetch(url,_):return (digest+'\n').encode() if url.endswith('.sha256') else sig.encode() if url.endswith('.minisig') else body
        with self.assertRaises(au.minisign.MinisignError):au.cached_wheel(self.st,'0.16.6a1',fetch=fetch)
        def missing(url,n):
            if url.endswith('.minisig'):raise OSError('not published')
            return fetch(url,n)
        with self.assertRaises(OSError):au.cached_wheel(self.st,'0.16.6a1',fetch=missing)

class SharedVerification(unittest.TestCase):
    def test_manual_and_phone_apply_refuse_artifact_before_install(self):
        st=Mock()
        with patch.object(update,'writable',return_value=True),patch.object(update,'verified_plan',side_effect=ValueError('wrong signature')),patch.object(update,'install_kind',return_value={'kind':'uv','where':'/fixture'}):
            runner=Mock()
            r=update.apply(st,check_fn=lambda:{'status':'newer','latest':'9.9.9'},run=runner,svc_on=False)
            self.assertEqual(r['reason'],'artifact_refused');runner.assert_not_called()
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
        self.assertEqual(summary['version'],__version__);self.assertTrue(summary['zh']);self.assertTrue(summary['en'])

class Worker(Nightly):
    def fixtures(self):
        paths={}
        for v in (__version__,'0.16.6a1'):
            p=self.st.root/f'agentj-{v}-py3-none-any.whl'
            body,digest,sig,keys=Artifacts.signed(self,v);self.fixture_keys=keys
            p.write_bytes(body)
            p.with_suffix('.verified.json').write_text(json.dumps({'sha256':digest,'signature':sig}));paths[v]=p
        return paths
    def run_worker(self, fail=False, journal=None):
        paths=self.fixtures();self.installed=[];self.restarted=[]
        if journal:au.write(self.st, {**journal,'from':__version__,'to':'0.16.6a1','old':str(paths[__version__]),'new':str(paths['0.16.6a1']),'install':{'kind':'pip','where':self.tmp.name},'argv':['fixture-cli']},au.JOB)
        def runner(argv):
            if '--version' in argv:return Mock(returncode=0,stdout='agentj '+(self.installed[-1] if self.installed else __version__))
            if 'doctor' in argv:return Mock(returncode=0,stdout=json.dumps({'checks':[{'id':'main-core','status':'ok'}]}))
            with zipfile.ZipFile(argv[-1]) as z:v=z.read('fixture-version').decode()
            self.installed.append(v)
            return Mock(returncode=1 if fail and v=='0.16.6a1' else 0)
        with patch.object(au.minisign,'TRUSTED_SIGNERS',self.fixture_keys),patch.object(update,'install_kind',return_value={'kind':'pip','where':self.tmp.name}),patch.object(update,'new_argv',return_value=['fixture-cli']):
            return au.run(self.st,check_fn=lambda:{'status':'newer','latest':'0.16.6a1'},cache=lambda st,v,**kw:paths[v],runner=runner,restart=self.restarted.append)
    def test_full_install_verify_restart_and_persisted_result(self):
        r=self.run_worker();self.assertEqual(r['result'],'ok');self.assertEqual(self.installed,['0.16.6a1']);self.assertEqual(self.restarted,['0.16.6a1'])
        self.assertEqual(au.read(self.st,au.JOB)['phase'],'done')
    def test_upgrade_failed_restores_cached_old_wheel(self):
        r=self.run_worker(fail=True);self.assertEqual(r['result'],'rolled_back');self.assertEqual(self.installed,['0.16.6a1',__version__]);self.assertEqual(self.restarted,[__version__])
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

if __name__=='__main__':unittest.main()
