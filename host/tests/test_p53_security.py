"""Synthetic-only regression. Darwin checks are backend simulations, not hardware acceptance."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from agentj import fence, provenance
from agentj.core import orders, native_order, order_host, askbridge

class NativeFence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.master = self.base/'master'; self.master.mkdir()
        self.work = self.base/'work'; self.work.mkdir()
        self.home = self.base/'home'; self.home.mkdir()
        self.order = NS(path=self.master/'orders'/'one')
        self.st = native_order.NativeState(self.order, {'dir':str(self.work)})
        self.view = getattr(self.st, 'fence_state', self.st)
        self.st.perm_dir.mkdir(parents=True, exist_ok=True)
        self.canary = self.master/'canary'; self.canary.write_text('ordinary synthetic canary')
        self.sibling = native_order.NativeState(NS(path=self.master/'orders'/'two'), {}).perm_dir
        (self.sibling/'claim').write_text('synthetic sibling claim')
        self.claim = self.st.perm_dir/'task-claim-synthetic'; self.claim.write_text('synthetic current claim')
        self.master_state = native_order.State(self.master)
        self.config = self.master_state.config_path
        self.config.write_bytes(b'{"agent":{"kind":"claude","dir":"synthetic"}}\n')
        self.session = self.master_state.agent_path
        self.session.write_bytes(b'{"claude":"synthetic-master-session"}\n')
        self.private = self.st.root/'session-canary';self.private.write_text('private native state')
        self.before = [p.read_bytes() for p in (self.config,self.session)]
        self.env = {'HOME':str(self.home),'TMPDIR':str(self.base),'PATH':'/usr/bin:/bin'}

    def test_protection_view_delegates_private_state(self):
        self.assertEqual(self.view.root,self.master)
        self.assertEqual(self.view.perm_dir,self.st.perm_dir)
        self.view.set_agent_session('claude','synthetic-private-session')
        self.assertEqual(self.st.agent_session('claude'),'synthetic-private-session')
        self.assertEqual(self.view.agent_config(),{'dir':str(self.work)})
        self.assertEqual([p.read_bytes() for p in (self.config,self.session)],self.before)
        class Base:
            def launch_argv(adapter,argv):
                self.assertEqual(adapter.host.st.root,self.master)
                self.assertEqual(adapter.host.st.perm_dir,self.st.perm_dir)
                return argv
        class Adapter(native_order.FencedAdapter,Base): pass
        adapter=Adapter();original=NS(st=self.st);adapter.host=original
        self.assertEqual(adapter.launch_argv(['synthetic']),['synthetic'])
        self.assertIs(adapter.host,original)
        self.assertIs(adapter.host.st,self.st)

    def test_linux_and_darwin_protection_parameters(self):
        args = fence.bwrap_argv(self.view,str(self.work),home=str(self.home),environ=self.env)
        self.assertIn(['--tmpfs',str(self.master)], [args[i:i+2] for i in range(len(args))])
        self.assertEqual(args[-args[::-1].index('--bind')-1:][:3], ['--bind',str(self.st.perm_dir),str(self.st.perm_dir)])
        profile, params = fence.sbpl_profile(self.view,str(self.work),home=str(self.home),environ=self.env)
        self.assertEqual(params['STATE'],str(self.master))
        self.assertEqual(params['PERM'],str(self.st.perm_dir))
        self.assertIn('(deny network-outbound (remote unix-socket (subpath (param "STATE"))))',profile)
        self.assertIn('(allow file-read* file-write* (subpath (param "PERM")))',profile)

    def test_real_fence_subprocess_canary_and_private_state(self):
        sock = socket.socket(socket.AF_UNIX); self.addCleanup(sock.close)
        endpoint = self.st.perm_dir/'perm.sock'; sock.bind(str(endpoint)); sock.listen(1)
        code = '''import pathlib,socket,sys
canary,sibling,claim,endpoint,work,config,session,private=sys.argv[1:]
for name in (canary,sibling,config,session,private):
 try: pathlib.Path(name).read_bytes()
 except OSError: pass
 else: raise AssertionError('protected synthetic path readable: '+name)
assert pathlib.Path(claim).read_text()=='synthetic current claim'
s=socket.socket(socket.AF_UNIX);s.connect(endpoint);s.sendall(b'canary');s.close()
p=pathlib.Path(work)/'ordinary';p.write_text('work');assert p.read_text()=='work'
'''
        argv = [sys.executable,'-c',code,str(self.canary),str(self.sibling/'claim'),str(self.claim),str(endpoint),str(self.work),str(self.config),str(self.session),str(self.private)]
        if sys.platform == 'darwin':
            prefix = fence.sandbox_argv(self.view,str(self.work),home=str(self.home),environ=self.env)
        else:
            prefix = fence.bwrap_argv(self.view,str(self.work),home=str(self.home),environ=self.env)
        result = subprocess.run(prefix+argv,env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        sock.settimeout(2); conn,_=sock.accept()
        with conn: self.assertEqual(conn.recv(20),b'canary')
        self.st.set_agent_session('claude','private-session')
        self.st.set_agent_model('ignored')
        self.assertEqual([p.read_bytes() for p in (self.config,self.session)],self.before)
        self.assertEqual(self.master_state.agent_session('claude'),'synthetic-master-session')
        self.assertEqual(self.master_state.config()['agent']['kind'],'claude')
        self.assertEqual(self.st.root,self.order.path/'native')

class ProcessEvidence(unittest.TestCase):
    def test_real_process_chain_and_uid(self):
        from agentj.core import process_evidence as pe
        p = subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            token=pe.codex_procs.start_token(p.pid)
            self.assertTrue(pe.descendant(p.pid,os.getpid(),pe.codex_procs.start_token(os.getpid())))
            self.assertTrue(pe.descendant(p.pid,p.pid,token))
            self.assertFalse(pe.descendant(os.getpid(),p.pid,token))
            self.assertFalse(pe.descendant(p.pid,p.pid,token+'wrong'))
            self.assertFalse(pe.descendant(None,p.pid,token))
            with mock.patch.object(pe,'uid',return_value=os.getuid()+1):
                self.assertFalse(pe.descendant(p.pid,p.pid,token))
        finally:
            p.terminate();p.wait(timeout=5)

    def test_darwin_kernel_peer_backend_simulation(self):
        from agentj.core import process_evidence as pe
        kernel=mock.Mock();kernel.getsockopt.return_value=__import__('struct').pack('i',123)
        with mock.patch.object(pe.sys,'platform','darwin'):
            self.assertEqual(pe.peer_pid(NS(get_extra_info=lambda _:kernel)),123)
            kernel.getsockopt.assert_called_once_with(0,0x002,4)
            kernel.getsockopt.side_effect=OSError('missing')
            self.assertIsNone(pe.peer_pid(NS(get_extra_info=lambda _:kernel)))

    def test_darwin_ps_backend_simulation_and_reuse(self):
        from agentj.core import process_evidence as pe
        from agentj import codex_procs
        def ps(argv,**kw):
            self.assertEqual(argv[0],'ps');self.assertEqual(argv[2] in ('lstart=','ppid=','uid='),True)
            self.assertEqual(argv[3],'-p');self.assertTrue(argv[4].isdigit())
            value={'lstart=':'synthetic-start','ppid=':'10','uid=':str(os.getuid())}[argv[2]]
            return NS(returncode=0,stdout=value)
        with mock.patch.object(codex_procs,'_linux',return_value=False), mock.patch.object(pe.subprocess,'run',side_effect=ps):
            self.assertTrue(pe.descendant(20,10,'synthetic-start'))
        rows=[('a',os.getuid(),10),('b',os.getuid(),10)]
        with mock.patch.object(pe,'snapshot',side_effect=rows):
            self.assertFalse(pe.descendant(20,20,'a'))

    def test_askbridge_never_accepts_missing_peer(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as d:
            from test_p53_orders import BRIEF
            o=orders.create(project=d,prompt_text='synthetic',title='synthetic',brief=BRIEF,st_root=Path(d)/'state')
            (o.path/'.ask_token').write_text('synthetic')
            (o.path/'pid').write_text(str(os.getpid()))
            server=askbridge.Server(None,Path(d)/'state')
            hello={'t':'hello','order':o.id,'token':'synthetic'}
            self.assertIsNone(server._admit(hello,None))
            self.assertIsNotNone(server._admit(hello,os.getpid()))

class SourcesAndProjects(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'project';self.project.mkdir()
        self.ag=NS(kind='core',cfg={'dir':str(self.project),'fence':True},native_source=provenance.phone())
        # Extract the actual Host classifier, avoiding optional serve dependencies.
        import ast
        source=Path(__file__).resolve().parents[1]/'agentj'/'serve.py'
        tree=ast.parse(source.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Host')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_dispatch_turn')
        same_owner=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_same_owner_turn')
        scope={'provenance':provenance}
        exec(compile(ast.Module(body=[same_owner,fn],type_ignores=[]),str(source),'exec'),scope)
        self.foreign=False
        self.host=NS(agent=self.ag,stopped=lambda:False,_foreign_seen=lambda:self.foreign,
            st=NS(root=self.root/'state',agent_session=lambda _:'synthetic-session',log=lambda *a,**kw:None))
        self.host._dispatch_turn=lambda:scope['_dispatch_turn'](self.host)
        patch=mock.patch.dict(os.environ,{'AGENTJ_CORE':'1'});patch.start();self.addCleanup(patch.stop)

    def test_trusted_system_and_forged_or_tainted_source(self):
        self.ag.cur_send=NS(source=provenance.system(),hook_notice=None)
        parent,_=order_host.authority(self.host,internal=self.ag)
        self.assertEqual(parent['kind'],'task');self.assertEqual(parent['source_class'],'system')
        self.foreign=True
        with self.assertRaises(orders.OrderError):order_host.authority(self.host,internal=self.ag)
        self.foreign=False
        self.ag.cur_send=NS(source=provenance.system(),hook_notice='foreign notice')
        with self.assertRaises(orders.OrderError):order_host.authority(self.host,internal=self.ag)
        del self.ag.cur_send
        for src in (provenance.system(),None,{'kind':'email','role':'owner','sender':'x@example.test'}, {'kind':'friend_task','role':'task'}):
            self.ag.native_source=src
            with self.assertRaises(orders.OrderError):order_host.authority(self.host,internal=self.ag)

    def test_same_host_requires_host_recognized_evidence(self):
        self.ag.native_source=provenance.SameHost(kind='same_host',role='owner')
        self.ag.untrusted=lambda:False;self.ag.peer_low=False
        parent,_=order_host.authority(self.host,internal=self.ag)
        self.assertEqual(parent['source_class'],'same_host')
        self.ag.peer_low=True
        with self.assertRaises(orders.OrderError):order_host.authority(self.host,internal=self.ag)

    async def test_system_order_freezes_real_origin_and_wrong_session_denies(self):
        from test_p53_orders import BRIEF
        self.ag.cur_send=NS(source=provenance.system(),hook_notice=None)
        with mock.patch.object(order_host.dispatcher,'launch',return_value='synthetic'):
            result=await order_host.handle(self.host,{'args':{'action':'new','project':str(self.project),
                'prompt':'synthetic','brief':BRIEF},'nonce':__import__('secrets').token_hex(16)},internal=self.ag)
        self.assertEqual(result['result'],'ok',result)
        o=orders.get(result['id'],self.host.st.root)
        self.assertEqual(o.meta['origin'],'task')
        self.assertEqual(o.meta['parent']['source_class'],'system')
        source=next(e['source'] for e in o.events() if e['ev']=='origin')
        self.assertTrue(native_order.frozen_source(o,source))
        self.assertIn('Host 系统通知',askbridge.origin(o.meta,None))
        self.ag.session={'sessionId':'wrong','pid':os.getpid(),'procStart':'synthetic'}
        with self.assertRaises(orders.OrderError) as error:
            order_host.authority(self.host,peer=os.getpid())
        self.assertEqual(error.exception.code,'session')

    async def test_child_project_and_symlink_escape_and_session(self):
        from test_p53_orders import BRIEF
        child=self.project/'child';child.mkdir()
        outside=self.root/'outside';outside.mkdir()
        (self.project/'escape').symlink_to(outside,target_is_directory=True)
        async def call(args):
            return await order_host.handle(self.host,{'args':args,'nonce':__import__('secrets').token_hex(16)},internal=self.ag)
        with mock.patch.object(order_host.dispatcher,'launch',return_value='synthetic'):
            result=await call({'action':'new','project':str(child),'prompt':'synthetic','brief':BRIEF})
            self.assertEqual(result['result'],'ok',result)
            bad=await call({'action':'new','project':str(self.project/'escape'),'prompt':'synthetic','brief':BRIEF})
            self.assertEqual(bad['why'],'project_not_allowed')
        self.assertEqual((await call({'action':'send','id':result['id'],'text':'synthetic'}))['result'],'ok')
        self.host.st.agent_session=lambda _:'wrong-session'
        self.assertEqual((await call({'action':'status','id':result['id']}))['why'],'order_owner')
