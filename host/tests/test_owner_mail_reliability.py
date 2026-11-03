import _hermetic
import base64
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from _testtmp import TEST_TMP
from agentj import report_host, report_local
import os
import pwd

# Gates replace HOME/state. The optional reviewed package is a read-only test
# fixture selected from the OS account home, never discovered through real state.
SOURCE = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir) / '.claude/skills/web-page-report'
HAS_ENGINE = (SOURCE / 'scripts/web_report.py').is_file()
requires_engine = unittest.skipUnless(HAS_ENGINE, 'reviewed report package fixture is not installed')


def fixture(test):
    if HAS_ENGINE:
        active = patch('agentj.report.find_engine', return_value=SOURCE)
        active.start()
        test.addCleanup(active.stop)

class HostReports(unittest.TestCase):
    def setUp(self):
        fixture(self)

    @requires_engine
    def test_host_helpers_execute_real_pinned_package(self):
        r = report_host.execute({'t':'report_local','args':['templates','--json'],'files':{}})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['returncode'], 0, r)
        self.assertIn('templates',json.loads(r['stdout']))

    def test_host_cannot_open_arbitrary_input_or_template(self):
        for args in (['validate','/etc/passwd'], ['schema','/etc'], ['validate','/input/input-data/../../etc/passwd'], ['schema','/input/input-template/../../etc']):
            with self.subTest(args=args):
                self.assertFalse(report_host.execute({'t':'report_local','args':args,'files':{}})['ok'])

    def test_payload_paths_cannot_escape(self):
        for name in ('/etc/passwd','input-data/../../escaped','input-data/.secret'):
            with self.subTest(name=name):
                self.assertEqual(report_host.execute({'t':'report_local','args':['templates'],'files':{name:base64.b64encode(b'x').decode()}})['code'],'path')

    def test_fenced_helper_failure_uses_host_not_approval(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root=pathlib.Path(tmp)
            engine=root/'engine';engine.mkdir()
            import os
            actual=pathlib.Path.stat
            def stat(path,*a,**kw):
                value=actual(path,*a,**kw)
                if str(path).startswith('/usr/bin/'):
                    values=list(value);values[4]=65534
                    return os.stat_result(values)
                return value
            with patch.object(report_local,'digest',return_value=report_local.TRUSTED_SHA256),patch.object(pathlib.Path,'stat',stat),patch('agentj.report_host.request',return_value='host-result') as host:
                self.assertEqual(report_local.run(engine,['templates'],['templates'],root,capture=True),'host-result')
                host.assert_called_once()

    def test_unavailable_host_has_receipt_and_no_card(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp, patch('agentj.elevate.client_request',return_value={'result':'unavailable'}):
            root=pathlib.Path(tmp)
            r=report_host.request(root,['templates'],['templates'],root,capture=True)
            self.assertEqual(r.returncode,4)
            receipt=json.loads(r.stderr)
            self.assertEqual(receipt['code'],'host_unavailable')
            self.assertFalse(receipt['approval_card_shown'])
            self.assertIn('resume original',receipt['recovery'])

    @requires_engine
    def test_engine_integrity_failure_does_not_execute(self):
        with patch('agentj.report_local.digest',return_value='wrong'),patch('agentj.report_local.run') as run:
            r=report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertEqual(r['code'],'integrity')
            run.assert_not_called()

    def test_payload_limits_precede_engine_discovery(self):
        cases = [
            ({f'input-data/f{i}': '' for i in range(129)}, 'input_limits'),
            ({'input-data/' + 'd/' * 9 + 'x': ''}, 'input_limits'),
            ({'input-data/x': base64.b64encode(b'x' * (512 * 1024 + 1)).decode()}, 'input_limits'),
            ({f'input-data/f{i}': base64.b64encode(b'x' * 360000).decode() for i in range(3)}, 'input_limits'),
            ({'input-data//x': ''}, 'path'),
        ]
        for files, code in cases:
            with self.subTest(code=code), patch('agentj.report.find_engine') as find:
                r = report_host.execute({'t':'report_local','args':['templates'],'files':files})
                self.assertEqual(r['code'], code)
                find.assert_not_called()

    def test_single_host_job_refuses_without_queue(self):
        with patch('agentj.report_host._JOB') as lock:
            lock.acquire.return_value = False
            r = report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertEqual(r['code'], 'host_busy')
            lock.release.assert_not_called()

    def test_sandbox_timeout_is_classified(self):
        import subprocess
        with patch('agentj.report_host._execute', side_effect=subprocess.TimeoutExpired('fixed', 30)):
            self.assertEqual(report_host.execute({'t':'report_local','args':['templates'],'files':{}})['code'], 'sandbox_timeout')

    def test_exact_shape_rejects_shell_and_extra_paths(self):
        for args in (['templates','--out','/tmp/x'], ['templates','--template','/etc'], ['render','/input/input-data/x','--out','/output/email.html','--email','--shell','x'], ['templates'] * 20):
            with self.subTest(args=args):
                self.assertEqual(report_host.execute({'t':'report_local','args':args,'files':{}})['code'],'shape')

    @requires_engine
    def test_actual_timeout_from_local_run_has_specific_receipt(self):
        import subprocess
        # Exercise the real local wrapper's TimeoutExpired conversion, not just execute's catch.
        with patch('agentj.report_host.bounded_run', side_effect=subprocess.TimeoutExpired('fixed', 30)):
            r = report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertEqual(r['code'], 'sandbox_timeout')

    @requires_engine
    def test_helper_missing_and_integrity_are_distinct(self):
        actual = pathlib.Path.is_file
        def missing(path):
            return False if path.name == 'bwrap' else actual(path)
        with patch.object(pathlib.Path, 'is_file', missing):
            self.assertEqual(report_host.execute({'t':'report_local','args':['templates'],'files':{}})['code'], 'helper_missing')
        actual_stat = pathlib.Path.stat
        import os
        def untrusted(path, *a, **kw):
            value = actual_stat(path, *a, **kw)
            if str(path).startswith('/usr/bin/'):
                items = list(value); items[4] = 65534
                return os.stat_result(items)
            return value
        with patch.object(pathlib.Path,'stat', untrusted):
            self.assertEqual(report_host.execute({'t':'report_local','args':['templates'],'files':{}})['code'], 'helper_integrity')

    @requires_engine
    def test_actual_resource_limits_and_no_external_access(self):
        from agentj import report
        import shutil
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            source=pathlib.Path(tmp)/'source'
            shutil.copytree(report.find_engine(), source, ignore=shutil.ignore_patterns('__pycache__'))
            (source/report.ENGINE).write_text('''import resource, json, pathlib, socket, os
r={name:resource.getrlimit(getattr(resource, 'RLIMIT_'+name))[0] for name in ('CPU','AS','NPROC','NOFILE','FSIZE','CORE')}
r['uid']=os.getuid()
try:
 b=bytearray(600*1024*1024)
 r['memory_blocked']=False
except MemoryError:
 r['memory_blocked']=True
try:
 with open('/out/large','wb') as f: f.write(b'x'*(9*1024*1024))
 r['file_blocked']=False
except OSError:
 r['file_blocked']=True
try:
 pathlib.Path('/etc/passwd').read_bytes()
 r['host_read_blocked']=False
except OSError:
 r['host_read_blocked']=True
try:
 socket.create_connection(('198.51.100.1',9),timeout=.2)
 r['network_blocked']=False
except OSError:
 r['network_blocked']=True
fds=[]
try:
 for i in range(80): fds.append(open('/dev/null','rb'))
 r['fds_blocked']=False
except OSError:
 r['fds_blocked']=True
finally:
 for f in fds: f.close()
import time
children=[]
r['forks_blocked']=False
try:
 for i in range(24):
  try: pid=os.fork()
  except OSError:
   r['forks_blocked']=True
   break
  if pid==0:
   time.sleep(.2)
   os._exit(0)
  children.append(pid)
finally:
 for pid in children: os.waitpid(pid,0)
print(json.dumps(r))
''')
            with patch('agentj.report.find_engine', return_value=source), patch.object(report_local,'TRUSTED_SHA256',report_local.digest(source)):
                r=report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertTrue(r['ok'], r)
            values=json.loads(r['stdout'])
            self.assertEqual({k:values[k] for k in ('CPU','AS','NPROC','NOFILE','FSIZE','CORE')}, {'CPU':15,'AS':536870912,'NPROC':16,'NOFILE':64,'FSIZE':8388608,'CORE':0})
            self.assertTrue(all(values[k] for k in ('memory_blocked','file_blocked','host_read_blocked','network_blocked','fds_blocked','forks_blocked')),values)

    @requires_engine
    def test_actual_stdout_overflow_is_resource_failure(self):
        from agentj import report
        import shutil
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            source=pathlib.Path(tmp)/'source'
            shutil.copytree(report.find_engine(), source, ignore=shutil.ignore_patterns('__pycache__'))
            (source/report.ENGINE).write_text("import sys\nsys.stdout.write('x' * (9*1024*1024))\n")
            with patch('agentj.report.find_engine', return_value=source), patch.object(report_local,'TRUSTED_SHA256',report_local.digest(source)):
                r=report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertEqual(r['code'],'resource_limit',r)


    @requires_engine
    def test_actual_response_frame_is_bounded(self):
        from agentj import report
        import shutil
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            source=pathlib.Path(tmp)/'source'
            shutil.copytree(report.find_engine(), source, ignore=shutil.ignore_patterns('__pycache__'))
            (source/report.ENGINE).write_text("print('x' * (2*1024*1024))")
            with patch('agentj.report.find_engine', return_value=source), patch.object(report_local,'TRUSTED_SHA256',report_local.digest(source)):
                r=report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertEqual(r['code'],'output_limits',r)

    @requires_engine
    def test_actual_cpu_limit_is_resource_failure(self):
        from agentj import report
        import shutil
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            source=pathlib.Path(tmp)/'source'
            shutil.copytree(report.find_engine(), source, ignore=shutil.ignore_patterns('__pycache__'))
            (source/report.ENGINE).write_text("while True: pass")
            with patch('agentj.report.find_engine', return_value=source), patch.object(report_local,'TRUSTED_SHA256',report_local.digest(source)):
                r=report_host.execute({'t':'report_local','args':['templates'],'files':{}})
            self.assertEqual(r['code'],'resource_limit',r)


import asyncio
import os
from agentj import elevate, report

class NativeSocket(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture(self)
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        self.sock=self.root/'report.sock'
        self.elevator=elevate.Elevator.__new__(elevate.Elevator)
        self.server=await asyncio.start_unix_server(self.elevator.on_client,path=str(self.sock),limit=elevate.MAX_FRAME)
        self.addAsyncCleanup(self.close_server)

    async def close_server(self):
        self.server.close()
        await self.server.wait_closed()

    async def call(self,args,files=None):
        return await asyncio.to_thread(elevate.client_request,None,{'t':'report_local','args':args,'files':files or {}},path=self.sock,timeout=40)

    @requires_engine
    async def test_real_handler_r15_frozen_bytes_and_export(self):
        with patch('agentj.action_policy.confirm_cli',side_effect=AssertionError('unexpected card')):
            for action in ('schema','example'):
                r=await self.call([action,'R15'])
                self.assertTrue(r.get('ok'),r)
                self.assertEqual(r['returncode'],0,r)
                json.loads(r['stdout'])
                if action=='example': data=r['stdout'].encode()
            # >64KiB verifies the actual configured frame size, without arbitrary host reads.
            files={'input-data/data.json':base64.b64encode(data).decode(),'input-data/padding':base64.b64encode(b'x'*100000).decode()}
            r=await self.call(['validate','/input/input-data/data.json','--json'],files)
            self.assertTrue(r.get('ok'),r)
            self.assertEqual(r['returncode'],0,r)
            root=self.root/'snapshot'; (root/'input-data').mkdir(parents=True)
            (root/'input-data/data.json').write_bytes(data)
            dest=self.root/'export.html'
            original=['render',str(root/'input-data/data.json'),'--email','--out',str(dest)]
            with patch('agentj.names.elevate_sock',return_value=self.sock):
                result=await asyncio.to_thread(report_host.request,report.find_engine(),original,original,root,capture=True)
            self.assertEqual(result.returncode,0,result.stderr)
            html=dest.read_bytes()
            self.assertGreater(len(html),100)
            report_local.check_email(html,allow_https_images=True)
            self.assertFalse((root/'email.html').exists())

    async def test_socket_unknown_argv_refused_without_execution(self):
        with patch('agentj.report_local.run',side_effect=AssertionError('must not execute')):
            r=await self.call(['templates','--shell','id'])
        self.assertEqual(r.get('code'),'shape',r)

    async def test_actual_overlapping_socket_calls_refuse_without_queue(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def wait(_):
            entered.set()
            release.wait(5)
            return {'ok':True,'code':'executed','returncode':0,'stdout':'','stderr':''}
        with patch('agentj.report_host._execute',side_effect=wait):
            first=asyncio.create_task(self.call(['templates']))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                second=await self.call(['templates'])
                self.assertEqual(second.get('code'),'host_busy',second)
            finally:
                release.set()
                await first

    async def test_missing_peer_credentials_fail_closed(self):
        from unittest.mock import Mock, AsyncMock
        reader=asyncio.StreamReader()
        reader.feed_data(b'{"t":"report_local","args":["templates"],"files":{}}\n')
        writer=Mock()
        writer.get_extra_info.return_value=None
        writer.drain=AsyncMock()
        with patch('agentj.report_host.execute') as execute:
            await self.elevator.on_client(reader,writer)
        execute.assert_not_called()
        self.assertEqual(json.loads(writer.write.call_args.args[0])['code'],'peer_unverified')

    async def test_different_uid_never_reaches_handler(self):
        # Real socket credentials are read, then compare against an intentionally different server UID.
        with patch('agentj.elevate.os.getuid',return_value=os.getuid()+1),patch('agentj.report_host.execute') as execute:
            r=await self.call(['templates'])
        self.assertEqual(r.get('result'),'unavailable',r)
        execute.assert_not_called()
