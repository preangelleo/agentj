"""Pure-mock directory guards and launch ordering; no native/security subprocess runs."""
import _hermetic
import asyncio,errno,os,stat,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from agentj import inbox,olares_runtime as runtime,olares_probe,olares_supervisor,fence

class FS:
    def __init__(self):
        self.nodes={'/opt/data':('dir',1000,0o750)};self.fds={};self.next=10;self.created=[];self.race=None
    def full(self,name,parent):
        return name if parent is None else self.fds[parent]+'/'+name
    def open(self,name,flags,dir_fd=None):
        assert flags & os.O_NOFOLLOW and flags & os.O_DIRECTORY
        path=self.full(name,dir_fd)
        if path not in self.nodes:raise FileNotFoundError(path)
        if self.nodes[path][0]!='dir':raise OSError(errno.ELOOP,'synthetic link or non-directory')
        self.next+=1;self.fds[self.next]=path;return self.next
    def mkdir(self,name,mode=0o777,dir_fd=None):
        assert dir_fd is not None and '/' not in name and name not in ('.','..')
        path=self.full(name,dir_fd);self.created.append(path)
        if path in self.nodes:raise FileExistsError(path)
        self.nodes[path]=('link' if path==self.race else 'dir',1000,mode)
    def fstat(self,fd):
        kind,uid,mode=self.nodes[self.fds[fd]]
        return SimpleNamespace(st_uid=uid,st_mode=(stat.S_IFDIR if kind=='dir' else stat.S_IFREG)|mode)
    def close(self,fd):self.fds.pop(fd)

class DirectoryGuards(unittest.TestCase):
    def setUp(self):
        self.fs=FS();self.st=SimpleNamespace(root=Path('/opt/data/.local/state/agentj'))
        patches=[mock.patch.dict(os.environ,{'HOME':'/opt/data','PATH':'/usr/bin'},clear=True),
                 mock.patch.object(olares_supervisor,'enabled',return_value=True),
                 mock.patch.object(olares_supervisor,'trusted',return_value=True),
                 mock.patch.object(fence,'inside',return_value=False),
                 mock.patch('agentj.preferences.path',return_value=Path('/opt/data/.config/agentj/config.json5')),
                 mock.patch.object(os.path,'realpath',side_effect=lambda p:str(p)),
                 mock.patch.object(os,'getuid',return_value=1000),
                 mock.patch.object(os,'open',side_effect=self.fs.open),
                 mock.patch.object(os,'mkdir',side_effect=self.fs.mkdir),
                 mock.patch.object(os,'fstat',side_effect=self.fs.fstat),
                 mock.patch.object(os,'close',side_effect=self.fs.close),
                 mock.patch.object(os,'chmod',side_effect=AssertionError('chmod forbidden')),
                 mock.patch.object(subprocess,'run',side_effect=AssertionError('native forbidden')),
                 mock.patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden'))]
        for patch in patches:patch.start();self.addCleanup(patch.stop)
    def test_default_plan_creates_only_application_roots_and_fixed_parents(self):
        self.assertTrue(runtime.prepare(self.st,{}))
        for rel in ('.config','.local/share','.cache','.local/state'):
            self.assertEqual(self.fs.nodes['/opt/data/'+rel+'/opencode'],('dir',1000,0o700))
        self.assertFalse(self.fs.fds)
    def test_existing_owned_safe_directories_keep_modes(self):
        self.fs.nodes['/opt/data/.cache']=('dir',1000,0o755)
        self.fs.nodes['/opt/data/.cache/opencode']=('dir',1000,0o750)
        self.assertTrue(runtime.prepare(self.st,{}))
        self.assertEqual(self.fs.nodes['/opt/data/.cache/opencode'][2],0o750)
    def test_original_in_home_xdg_supported_without_caller_environment(self):
        with mock.patch.dict(os.environ,{'XDG_DATA_HOME':'/opt/data/custom/data'}):
            self.assertTrue(runtime.prepare(self.st,{}))
            self.assertIn('/opt/data/custom/data/opencode',self.fs.nodes)
        with self.assertRaises(TypeError):runtime.prepare(self.st,{},environ={'XDG_DATA_HOME':'/tmp'})
    def test_outside_dotdot_and_protected_overrides_fail_before_mkdir(self):
        for path in ('/tmp/runtime','/opt/data/../outside','/opt/data/./cache',
                     '/opt/data/.local/state/agentj','/opt/data/.config/systemd',
                     '/opt/data/.ssh','/opt/data/.claude','/opt/data/.config/herdr','/opt/data/agentj-seats/2'):
            with self.subTest(path=path),mock.patch.dict(os.environ,{'XDG_DATA_HOME':path}):
                self.assertFalse(runtime.prepare(self.st,{}));self.assertFalse(self.fs.created)
    def test_alias_of_protected_state_is_rejected_by_metadata_boundary(self):
        def real(path):return '/opt/data/private-state' if str(path)==str(self.st.root) else str(path)
        with mock.patch.object(os.path,'realpath',side_effect=real),mock.patch.dict(os.environ,{'XDG_DATA_HOME':'/opt/data/private-state'}):
            self.assertFalse(runtime.prepare(self.st,{}));self.assertFalse(self.fs.created)
    def test_custom_seats_and_shared_model_paths_remain_protected(self):
        for name in ('AGENTJ_SEATS_ROOT','AGENTJ_ASR_SHARED_DIR'):
            with self.subTest(name=name),mock.patch.dict(os.environ,{name:'/opt/data/private-shared','XDG_DATA_HOME':'/opt/data/private-shared'}):
                self.assertFalse(runtime.prepare(self.st,{}));self.assertFalse(self.fs.created)
    def test_any_existing_unsafe_component_refuses_entire_plan_before_creation(self):
        for node in (('link',1000,0o755),('dir',0,0o755),('dir',1000,0o777),('file',1000,0o600)):
            with self.subTest(node=node):
                self.fs.nodes['/opt/data/.cache']=node
                self.assertFalse(runtime.prepare(self.st,{}));self.assertFalse(self.fs.created);self.assertFalse(self.fs.fds)
    def test_replacement_link_between_missing_check_and_mkdir_is_not_followed(self):
        self.fs.race='/opt/data/.config'
        self.assertFalse(runtime.prepare(self.st,{}));self.assertFalse(self.fs.fds)
        self.assertEqual(self.fs.created,['/opt/data/.config'])
    def test_trust_fence_and_original_home_fail_without_creation(self):
        for patch in (mock.patch.object(olares_supervisor,'trusted',return_value=False),
                      mock.patch.object(fence,'inside',return_value=True),
                      mock.patch.dict(os.environ,{'HOME':'/tmp/other'})):
            with patch:self.assertFalse(runtime.prepare(self.st,{}))
        self.assertFalse(self.fs.created)
    def test_untrusted_layout_refuses_and_non_olares_keeps_original_route(self):
        with mock.patch.object(olares_supervisor,'enabled',return_value=False),mock.patch.object(Path,'exists',return_value=True):
            self.assertFalse(runtime.prepare(self.st,{}))
        with mock.patch.object(olares_supervisor,'enabled',return_value=False),mock.patch.object(Path,'exists',return_value=False):
            self.assertTrue(runtime.prepare(self.st,{}))
        self.assertFalse(self.fs.created)
    def test_existing_owner_server_does_no_filesystem_preparation(self):
        with mock.patch.object(runtime,'_plans',side_effect=AssertionError('owner server needs no mkdir')):
            self.assertTrue(runtime.prepare(self.st,{'session_mode':'shared','shared_opencode_port':12345}))
        self.assertFalse(self.fs.created)

class Ordering(unittest.TestCase):
    def test_actual_own_spawn_prepares_before_protocol_and_fence(self):
        from agentj import agent_opencode as oc,harness
        events=[];agent=object.__new__(oc.OpenCodeAgent);agent.cfg={'dir':'/fake/work'}
        agent.host=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()),lang='en')
        async def memory_thread(fn,*args):return fn(*args)
        async def authorized(argv):events.append('fence');return None
        agent.authorized_launch_argv=authorized
        with mock.patch.object(runtime,'prepare',side_effect=lambda *a:events.append('prepare') or True), \
             mock.patch.object(harness,'selected_version',side_effect=lambda *a:events.append('version') or '2.2.5'), \
             mock.patch.object(oc,'_bin',return_value='/fake/opencode'),mock.patch.object(oc,'free_port',return_value=12345), \
             mock.patch.object(asyncio,'to_thread',side_effect=memory_thread):
            self.assertFalse(asyncio.run(agent._spawn()))
        self.assertEqual(events,['prepare','version','fence'])
    def test_actual_spawn_runtime_failure_stops_before_protocol_and_fence(self):
        from agentj import agent_opencode as oc,harness
        agent=object.__new__(oc.OpenCodeAgent);agent.cfg={'dir':'/fake/work'}
        agent.host=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()),lang='en')
        agent._startup_failure=mock.Mock()
        async def memory_thread(fn,*args):return fn(*args)
        with mock.patch.object(runtime,'prepare',return_value=False), \
             mock.patch.object(oc,'_bin',return_value='/fake/opencode'), \
             mock.patch.object(harness,'selected_version',side_effect=AssertionError('no version after refusal')), \
             mock.patch.object(asyncio,'to_thread',side_effect=memory_thread):
            self.assertFalse(asyncio.run(agent._spawn()))
        self.assertTrue(agent.failed_start)
        agent._startup_failure.assert_called_once_with('launch','native_runtime_unavailable')
    def test_semantic_start_prepares_before_fence_only_for_owned_server(self):
        from agentj import state
        st=mock.Mock(root=Path('/fake/state'),perm_dir=Path('/fake/state/agentperm'));st.agent_config.return_value={'dir':'/fake/work'}
        for op,cfg,expected in [('start',{},['prepare','fence']),('start',{'session_mode':'shared','shared_opencode_port':12345},['fence']),('probe',{},['fence']),('detect',{},['fence'])]:
            events=[]
            with self.subTest(op=op,cfg=cfg),mock.patch.object(olares_supervisor,'enabled',return_value=True), \
                 mock.patch.object(olares_supervisor,'trusted',return_value=True),mock.patch.object(state,'State',return_value=st), \
                 mock.patch.object(state,'default_state',return_value=st.root),mock.patch.object(Path,'is_dir',return_value=True), \
                 mock.patch.object(Path,'mkdir'),mock.patch.object(runtime,'prepare',side_effect=lambda *a:events.append('prepare') or True), \
                 mock.patch.object(fence,'bwrap_argv',side_effect=lambda *a,**k:events.append('fence') or (_ for _ in ()).throw(OSError('synthetic unavailable'))):
                olares_probe.run(op,'opencode',cfg=cfg)
                self.assertEqual(events,expected)
