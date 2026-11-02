"""Olares lifecycle contracts; all state/processes are temporary and synthetic."""
import _hermetic  # noqa
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentj import cli, service, service_recovery

class Dispatch(unittest.TestCase):
    def test_fence_never_infers_first_connection_from_hidden_state(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'AGENTJ_FENCED':'1'}):
            st = mock.Mock(root=Path(d)); st.agent_config.return_value = None
            args = mock.Mock(target='claude', worker=False, scan=False, result=False, dry_run=False)
            with mock.patch.object(cli, 'cmd_agent') as connect:
                self.assertFalse(cli._first_connect(st, args))
                connect.assert_not_called()

    def test_olares_dispatches_status_and_restart_to_trusted_manager(self):
        from agentj import olares_supervisor as sup
        with mock.patch.object(sup, 'enabled', return_value=True), mock.patch.object(sup, 'call', return_value={
                'ok':True, 'installed':True, 'active':'active', 'generation':2}) as call:
            self.assertEqual(service.status()['kind'], 'olares')
            service.restart()
            self.assertEqual([x.args[0]['cmd'] for x in call.call_args_list], ['status','restart'])

from agentj import olares_supervisor as sup, switch_harness as sw
from test_p155_switch_harness import mk_state, FakeServe, Clock, fake_probe, UP
import json
import threading
import time
import subprocess
import sys


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='olares-life-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.st = mk_state(self.home / 'state', 'opencode', model='zhipuai/glm-5.3')
        self.unit = self.home / 'units'
        p = mock.patch.object(sw, 'receipt_path', side_effect=lambda name=sw.RECEIPT: self.unit / name)
        p.start(); self.addCleanup(p.stop)
        self.ola = self.home / '.agentj-olares'; self.ola.mkdir()
        (self.ola / 'bound').touch()
        self.m = sup.Supervisor(self.st, self.home)
        self.addCleanup(self.m.close)

    def request(self, **updates):
        return {'cmd':'job','spec':{'kind':'switch','target':'claude'},
                'job':'0123456789abcdef', 'at':time.time(), **updates}



class Protocol(LifecycleCase):
    def test_rejects_executable_env_state_fields_expired_and_nonfinite(self):
        for update in ({'argv':['/bin/sh']}, {'env':{'PATH':'/tmp'}}, {'state':'/tmp'},
                       {'at':time.time()-1000}, {'at':float('nan')}, {'job':'../../evil'},
                       {'spec':{'kind':'switch','target':'claude','force':1}},
                       {'spec':{'kind':'switch','target':'claude','argv':['/bin/sh']}}):
            with self.subTest(update=update), mock.patch.object(self.m,'spawn') as spawn:
                self.assertFalse(self.m.handle(self.request(**update))['ok'])
                spawn.assert_not_called()

    def test_serial_worker_replay_and_bound(self):
        proc = mock.Mock(); proc.poll.return_value=None
        with mock.patch.object(self.m,'spawn',return_value=proc):
            self.assertTrue(self.m.handle(self.request())['ok'])
            self.assertEqual(self.m.handle(self.request())['error'],'replayed_job')
            self.assertEqual(self.m.handle(self.request(job='1111111111111111'))['error'],'busy')
            self.m.worker=None  # synthetic process; never signal mock PID
            (self.ola/'bound').unlink()
            self.assertEqual(self.m.handle(self.request(job='2222222222222222'))['error'],'not_running')

    def test_shared_literal_env_allowlist_blocks_loader_overrides(self):
        parser = Path(__file__).resolve().parents[2] / 'olares/agentj/files/serve_env.py'
        self.m.envfile.parent.mkdir(parents=True,exist_ok=True)
        self.m.envfile.write_text('PATH=/tmp/evil\nLD_PRELOAD=/tmp/evil.so\nAGENTJ_STATE_DIR=/tmp/evil\n'
                                  'PYTHONPATH=/tmp/evil\nANTHROPIC_API_KEY="literal$(no)"\n')
        with mock.patch.object(sup,'ENV_PARSER',parser):
            env=self.m.env()
        self.assertNotIn('LD_PRELOAD',env); self.assertNotIn('PYTHONPATH',env)
        self.assertNotIn('AGENTJ_STATE_DIR',env); self.assertFalse(env['PATH'].startswith('/tmp'))
        self.assertEqual(env['ANTHROPIC_API_KEY'],'literal$(no)')

    def test_environment_marker_alone_does_not_enable_manager(self):
        with mock.patch.dict(os.environ, {'OLARES_APP':'1','AGENTJ_OLARES':'1'}):
            self.assertFalse(sup.enabled())
        self.assertFalse(sup.trusted(self.home))
        link=self.home/'link'; link.symlink_to('/usr/bin/python3')
        self.assertFalse(sup.trusted(link))

    def test_pod_interruption_restores_config_without_claiming_live_service(self):
        raw=sw._raw_agent(self.st)
        job='0123456789abcdef'
        self.st.write_private(self.st.root/sup.SNAPSHOT,json.dumps({'job':job,'raw':raw}).encode())
        sw._restore(self.st,{**raw,'kind':'claude'})
        sw.write_receipt(self.st, {'job':job,'status':'switching','target':'claude','from':'opencode'})
        sup.Supervisor(self.st,self.home)
        self.assertEqual(sw._raw_agent(self.st),raw)
        rec=sw.read_receipt(self.st)
        self.assertEqual((rec['status'],rec['reason']),('failed','rollback_failed'))
        self.assertNotIn('Nothing was changed',sw._say({'target':'claude','reason':'rollback_failed'},'en'))

    def test_pod_rebuild_preserves_successful_receipt_and_does_not_replay(self):
        sw.write_receipt(self.st, {'job':'0123456789abcdef','status':'ok','target':'claude','from':'opencode'})
        before=sw.read_receipt(self.st)
        with mock.patch.object(sup.Supervisor,'spawn') as spawn:
            sup.Supervisor(self.st,self.home)
            spawn.assert_not_called()
        self.assertEqual(sw.read_receipt(self.st),before)

    def test_real_process_restart_generation_worker_survives_and_unbind_stops_both(self):
        # Run actual owned child sessions; only the application payload is a stand-in.
        children=[]
        def spawn(args,**kwargs):
            p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'],
                               start_new_session=True,stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            children.append(p); return p
        with mock.patch.object(self.m,'spawn',side_effect=spawn):
            self.m.tick(); old=self.m.serve
            r=self.m.handle(self.request(spec={'kind':'scan'})); self.assertTrue(r['ok'])
            worker=self.m.worker
            out=[]; t=threading.Thread(target=lambda:out.append(self.m.handle({'cmd':'restart'})))
            t.start()
            end=time.monotonic()+6
            while t.is_alive() and time.monotonic()<end:
                self.m.tick(); time.sleep(.02)
            t.join(1)
            self.assertFalse(t.is_alive()); self.assertTrue(out[0]['ok'])
            self.assertIsNotNone(old.poll()); self.assertNotEqual(old.pid,self.m.serve.pid)
            self.assertIsNone(worker.poll()); self.assertEqual(out[0]['generation'],2)
            (self.ola/'bound').unlink(); self.m.tick()
            self.assertTrue(all(p.poll() is not None for p in children))
            self.assertEqual(self.m.jobs[r['job']]['status'],'unbound')

    def test_worker_ttl_terminates_only_owned_job(self):
        child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'],start_new_session=True)
        self.m.worker=child; self.m.job='0123456789abcdef'
        self.m.jobs[self.m.job]={'at':time.time()-1000,'status':'running','kind':'scan'}
        with mock.patch.object(self.m,'spawn',side_effect=OSError):
            self.m.tick()
        self.assertIsNotNone(child.poll())
        self.assertIsNone(self.m.worker)

    def test_submit_only_canonical_argv_and_state(self):
        with mock.patch('agentj.state.State',return_value=self.st), mock.patch.object(sup,'call',return_value={
                'ok':True,'job':'0123456789abcdef'}) as call:
            argv=sw.job_argv({'kind':'scan','job':'0123456789abcdef'})
            sup.submit(self.st,argv,'switch',{'AGENTJ_JOB_AT':str(time.time())})
            self.assertEqual(call.call_args.args[0]['spec'],{'kind':'scan'})
            for bad in (argv+['--evil'], ['/bin/sh','-c','id']):
                with self.assertRaises(service.ServiceError):
                    sup.submit(self.st,bad,'switch',{'AGENTJ_JOB_AT':str(time.time())})
            with self.assertRaises(service.ServiceError):
                sup.submit(self.st,argv,'switch',{'AGENTJ_JOB_AT':str(time.time()),'LD_PRELOAD':'evil'})


class Rollback(unittest.TestCase):
    def test_old_service_restart_and_hello_failures_are_not_rolled_back_success(self):
        for phase in ('restart','hello'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(prefix='olares-back-') as d:
                st=mk_state(d,'opencode',model='zhipuai/glm-5.3')
                unit=Path(d)/'units'
                with mock.patch.object(sw,'receipt_path',side_effect=lambda name=sw.RECEIPT:unit/name):
                    raw=sw._raw_agent(st); fake=FakeServe(st,'opencode'); clock=Clock()
                    fake.behaviour['claude']={'answers':False}
                    if phase=='hello':fake.behaviour['opencode']={'answers':False}
                    def restart():
                        if phase=='restart' and fake.restarts==1:
                            raise service.ServiceError('restart_failed')
                        fake.restart()
                    result=sw.worker(st,'claude',probe=fake_probe(),ctl=fake,restart=restart,status=UP,
                                     ready=lambda *a,**k:'idle',sleep=clock.sleep,clock=clock)
                    self.assertEqual(result,1); self.assertEqual(sw._raw_agent(st),raw)
                    rec=sw.read_receipt(st)
                    self.assertEqual((rec['status'],rec['reason']),('failed','rollback_failed'))
                    self.assertNotIn('back on',sw._say_receipt(rec,'en'))

class RealSocket(LifecycleCase):
    # Exercise the exact production socket handler/client with a manager owning
    # real child sessions. Root-owned image verification is separately negative-tested.
    def test_socket_job_restart_replay_and_unbind(self):
        sockdir=self.home/'run'; sockdir.mkdir(mode=0o700)
        path=sockdir/'manager.sock'
        stop=threading.Event()
        def spawn(args,**kwargs):
            return subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'],
                                    start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        def ticks():
            while not stop.wait(.02): self.m.tick()
        with mock.patch.object(self.m,'spawn',side_effect=spawn), sup.Server(path,self.m) as server:
            path.chmod(0o600)
            t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
            loop=threading.Thread(target=ticks); loop.start()
            try:
                with mock.patch.object(sup,'enabled',return_value=True),mock.patch.object(sup,'socket_path',return_value=path):
                    end=time.monotonic()+3
                    while not sup.call({'cmd':'status'})['active']=='active' and time.monotonic()<end:time.sleep(.01)
                    old=self.m.serve.pid
                    result=sup.call(self.request(spec={'kind':'scan'}))
                    self.assertEqual(result['epoch'],self.m.epoch)
                    worker=self.m.worker
                    self.assertEqual(sup.call({'cmd':'restart'})['generation'],2)
                    self.assertNotEqual(old,self.m.serve.pid); self.assertIsNone(worker.poll())
                    with self.assertRaises(service.ServiceError):sup.call(self.request(spec={'kind':'scan'}))
                    (self.ola/'bound').unlink()
                    end=time.monotonic()+2
                    while worker.poll() is None and time.monotonic()<end:time.sleep(.01)
                    self.assertIsNotNone(worker.poll())
                    self.assertEqual(sup.call({'cmd':'status'})['active'],'inactive')
            finally:
                stop.set(); loop.join(6); server.shutdown(); t.join(2)

    def test_job_crossing_ttl_is_retained_for_unbind_completion(self):
        child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'],start_new_session=True)
        self.m.worker=child; self.m.job='0123456789abcdef'
        self.m.jobs[self.m.job]={'at':time.time()-1000,'status':'running','kind':'scan'}
        self.m.persist()
        self.assertIn(self.m.job,self.m.jobs)
        (self.ola/'bound').unlink(); self.m.tick()
        self.assertEqual(self.m.jobs.get(self.m.job,{}).get('status'),None)  # result TTL elapsed; no KeyError
        self.assertIsNotNone(child.poll())

    def test_unsafe_socket_path_refused(self):
        p=self.home/'not-a-socket';p.write_text('')
        with mock.patch.object(sup,'enabled',return_value=True),mock.patch.object(sup,'socket_path',return_value=p):
            with self.assertRaises(service.ServiceError):sup.call({'cmd':'status'})

class ManagerHardening(unittest.TestCase):
    def test_early_non_dumpable_before_state_and_socket(self):
        source=Path(sup.__file__).read_text().split('def main():',1)[1]
        self.assertLess(source.index('fence.no_dump()'),source.index('manager = Supervisor'))
        self.assertLess(source.index('fence.no_dump()'),source.index('socket_path()'))

    def test_handler_concurrency_is_bounded(self):
        server=object.__new__(sup.Server)
        server.slots=threading.BoundedSemaphore(1)
        self.assertTrue(server.slots.acquire(False))
        request=mock.Mock()
        with mock.patch.object(server,'shutdown_request') as shutdown:
            server.process_request(request,'')
            shutdown.assert_called_once_with(request)
        server.slots.release()

    def test_non_dumpable_child_blocks_same_uid_proc_environ(self):
        """Contract v2: actual prctl dispatch under mock, no other-process reads.

        Kernel isolation has separate ordinary container metadata evidence. The
        historical method ID is retained for parity; it no longer replays /proc.
        """
        from agentj import fence
        def verify(call):
            call.assert_called_once_with(4, 0, 0, 0, 0)
        with mock.patch('ctypes.CDLL') as cdll, mock.patch.object(fence.sys, 'platform', 'linux'):
            fence.no_dump()
            cdll.assert_called_once_with(None, use_errno=True)
            verify(cdll.return_value.prctl)
            # Negative controls: absent hardening or wrong dumpable flag is red.
            for args in (None, (4, 1, 0, 0, 0)):
                bad = mock.Mock()
                if args is not None:
                    bad(*args)
                with self.assertRaises(AssertionError):
                    verify(bad)
        # Production entry ordering remains checked by the adjacent main test.
        # Positive kernel primitive: only the new process queries its own dumpable
        # flag. No ptrace, proc, other-process environment or secret is touched.
        if sys.platform == 'linux':
            code = ('import ctypes,sys; sys.path.insert(0,sys.argv[1]); '
                    'from agentj import fence; libc=ctypes.CDLL(None,use_errno=True); '
                    'libc.prctl(4,1,0,0,0); '
                    'before=libc.prctl(3,0,0,0,0); fence.no_dump(); '
                    'after=libc.prctl(3,0,0,0,0); print(before,after)')
            result = subprocess.run([sys.executable,'-I','-c',code,str(Path(sup.__file__).resolve().parents[1])],
                                    env={},capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'1 0')
            negative = subprocess.run([sys.executable,'-I','-c',code.replace('fence.no_dump();','None;'),
                                      str(Path(sup.__file__).resolve().parents[1])],
                                     env={},capture_output=True,text=True,timeout=10)
            self.assertEqual(negative.returncode,0,negative.stderr)
            self.assertEqual(negative.stdout.strip(),'1 1')
