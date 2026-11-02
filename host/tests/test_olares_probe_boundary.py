"""Safe mock-only boundary tests. No real subprocess or sensitive probe payload."""
import _hermetic
import os
import contextlib
import subprocess
import unittest
from unittest import mock
from agentj import harness, olares_probe, olares_supervisor, binaries


class ProbeRouting(unittest.TestCase):
    def setUp(self):
        # Even a missing routing guard must not execute a real native candidate.
        for name in ('run', 'Popen'):
            p = mock.patch.object(subprocess, name, side_effect=AssertionError('real subprocess forbidden'))
            p.start(); self.addCleanup(p.stop)
        p = mock.patch('agentj.scratch.home', side_effect=lambda prefix: contextlib.nullcontext('/fake/version-home'))
        p.start(); self.addCleanup(p.stop)
        p = mock.patch('agentj.olares_runtime.prepare', return_value=True)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(olares_supervisor, 'enabled', return_value=True)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(olares_probe, 'inside', return_value=False)
        p.start(); self.addCleanup(p.stop)

    def test_detect_routes_all_candidates_without_host_resolution(self):
        def answer(op, kind, **kw):
            return {'harnesses': [{'name': kind, 'supported': True, 'installed': False, 'logged_in': None}],
                    'usable': [], 'decision': 'none'}
        with mock.patch.object(olares_probe, 'run', side_effect=answer) as run, \
             mock.patch.object(binaries, 'resolve', side_effect=AssertionError('host resolution')):
            harness.detect()
            self.assertEqual([c.args[:2] for c in run.call_args_list], [('detect', k) for k in harness.NAMES])

    def test_probe_routes_before_detection_even_with_environment_marker(self):
        with mock.patch.dict(os.environ, {'AGENTJ_FENCED': '1'}), \
             mock.patch.object(olares_probe, 'run', return_value=olares_probe.failure('claude', 'probe')) as run, \
             mock.patch.object(binaries, 'resolve', side_effect=AssertionError('host resolution')):
            harness.probe(only=('claude',))
            self.assertEqual(run.call_args_list[0], mock.call('probe', 'claude', None, harness.PROBE_TIMEOUT))
            self.assertEqual(len(run.call_args_list), len(harness.NAMES))
            self.assertTrue(all(c.args[0] == 'detect' for c in run.call_args_list[1:]))

    def test_direct_version_query_fails_closed(self):
        self.assertIsNone(harness.version_of('/fake/candidate'))

    def test_auxiliary_catalogues_fail_closed_or_route(self):
        from agentj import opencode_free, opencode_catalogue
        with mock.patch.object(olares_probe, 'run', return_value=olares_probe.failure('opencode', 'detect')):
            self.assertIsNone(opencode_free.listed('/fake/candidate'))
            self.assertIsNone(opencode_catalogue.native_models('/fake/candidate'))

    def test_start_routes_before_any_executable_or_workdir_use(self):
        from agentj import agent_codex, opencode_start
        for kind, mod in [('codex', agent_codex), ('opencode', opencode_start)]:
            with self.subTest(kind=kind), mock.patch.object(olares_probe, 'run', return_value={'secs': 0, 'reason': 'start_failed'}) as run:
                cfg = {'dir': '/fake/work', 'session_mode': 'shared'}
                self.assertEqual(mod.start_probe(None, cfg)['reason'], 'start_failed')
                run.assert_called_once_with('start', kind, None, 150, cfg=cfg)

    def test_inside_uses_sealed_mount_check_not_marker(self):
        with mock.patch.object(olares_probe, '_CHILD', None), \
             mock.patch.object(olares_probe, 'sealed', return_value=True), \
             mock.patch.dict(os.environ, {'AGENTJ_FENCED': '1'}):
            self.assertFalse(type(self).inside_original())

    inside_original = staticmethod(olares_probe.inside)

    def test_all_candidate_versions_are_measured_only_in_child_branch(self):
        # Synthetic candidate paths, fake version subprocess only.
        with mock.patch.object(olares_probe, 'inside', return_value=True), \
             mock.patch.object(olares_probe, '_CHILD', 'claude'), \
             mock.patch.object(binaries, 'resolve', return_value={'path': '/fake/one'}), \
             mock.patch.object(binaries, 'installations', return_value=['/fake/one', '/fake/two']), \
             mock.patch.object(binaries, 'wrapper', return_value=False), \
             mock.patch.object(harness, 'login_of', return_value=('missing', '')), \
             mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='1.2.3', stderr='')) as run:
            h = harness._one('claude')
            self.assertEqual([c.args[0] for c in run.call_args_list],
                             [['/fake/one', '--version'], ['/fake/two', '--version']])
            self.assertEqual(len(h['installations']), 2)

    def test_child_detection_only_resolves_requested_kind(self):
        with mock.patch.object(olares_probe, 'inside', return_value=True), \
             mock.patch.object(olares_probe, '_CHILD', 'claude'), \
             mock.patch.object(harness, '_one', return_value={'name':'claude','supported':True,'installed':False,'logged_in':None}) as one:
            harness.detect()
            one.assert_called_once_with('claude', None)

    def test_environment_excludes_service_keys_and_loaders_for_all_ops(self):
        sample = {'HOME': '/fake/home', 'PATH': '/usr/bin', 'LANG': 'C',
                  'OLARES_API_KEY': 'synthetic', 'PROVIDER_API_KEY': 'synthetic',
                  'LD_PRELOAD': 'synthetic', 'NODE_OPTIONS': 'synthetic',
                  'BASH_ENV': '/fake/rc', 'ENV': '/fake/rc', 'OPENCODE_CONFIG_CONTENT': 'synthetic'}
        for metadata in (True, False):
            env = olares_probe.environment('claude', sample, metadata=metadata)
            self.assertEqual(env, {k: sample[k] for k in ('HOME', 'PATH', 'LANG')})

    def test_native_configuration_retained_without_metadata_secrets(self):
        sample = {'HOME': '/fake/home', 'PATH': '/usr/bin',
                  'HTTPS_PROXY': 'http://proxy.invalid:8080', 'NO_PROXY': 'localhost',
                  'AGENTJ_CLAUDE_BIN': '/fake/claude', 'AGENTJ_OPENCODE_BIN': '/fake/opencode',
                  'CLAUDE_CONFIG_DIR': '/fake/claude-config', 'CODEX_HOME': '/fake/codex',
                  'ANTHROPIC_API_KEY': 'synthetic', 'OPENAI_API_KEY': 'synthetic',
                  'OLARES_API_KEY': 'synthetic', 'UNKNOWN_TOKEN': 'synthetic'}
        for metadata in (True, False):
            with self.subTest(metadata=metadata):
                env = olares_probe.environment('claude', sample, metadata=metadata)
                for key in ('HTTPS_PROXY', 'NO_PROXY', 'AGENTJ_CLAUDE_BIN', 'CLAUDE_CONFIG_DIR'):
                    self.assertEqual(env.get(key), sample[key])
                for key in ('AGENTJ_OPENCODE_BIN', 'CODEX_HOME', 'OLARES_API_KEY', 'UNKNOWN_TOKEN', 'OPENAI_API_KEY'):
                    self.assertNotIn(key, env)
                self.assertEqual('ANTHROPIC_API_KEY' in env, not metadata)
        env = olares_probe.environment('opencode', sample)
        self.assertEqual(env.get('OPENAI_API_KEY'), sample['OPENAI_API_KEY'])
        self.assertEqual(env.get('CLAUDE_CONFIG_DIR'), sample['CLAUDE_CONFIG_DIR'])
        self.assertEqual(env.get('AGENTJ_OPENCODE_BIN'), sample['AGENTJ_OPENCODE_BIN'])

    def test_probe_version_drops_auth_even_inside_child(self):
        with mock.patch.object(olares_probe, 'inside', return_value=True), \
             mock.patch.object(olares_probe, '_CHILD', 'claude'), \
             mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'synthetic', 'AGENTJ_CLAUDE_BIN': '/fake/claude'}), \
             mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='1.2.3', stderr='')) as run:
            self.assertEqual(harness.version_of('/fake/claude'), '1.2.3')
            self.assertNotIn('ANTHROPIC_API_KEY', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['env']['AGENTJ_CLAUDE_BIN'], '/fake/claude')

    def test_supervisor_preserves_fixed_session_names_only(self):
        from pathlib import Path
        import tempfile
        from test_p155_switch_harness import mk_state
        sample = {'HTTPS_PROXY': 'http://proxy.invalid:8080', 'AGENTJ_CLAUDE_BIN': '/fake/claude',
                  'CLAUDE_CONFIG_DIR': '/fake/config', 'ANTHROPIC_API_KEY': 'synthetic',
                  'OPENCODE_SERVER_PASSWORD':'synthetic-only','OPENCODE_SERVER_USERNAME':'synthetic-owner',
                  'UNKNOWN_TOKEN': 'synthetic', 'NODE_OPTIONS': 'synthetic'}
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, sample, clear=True):
            os.environ.update(HOME=folder, XDG_CONFIG_HOME=folder + '/config')
            st = mk_state(Path(folder) / 'state', 'opencode')
            manager = olares_supervisor.Supervisor(st, Path(folder))
            self.addCleanup(manager.close)
            for key in ('HTTPS_PROXY', 'AGENTJ_CLAUDE_BIN', 'CLAUDE_CONFIG_DIR', 'ANTHROPIC_API_KEY', 'OPENCODE_SERVER_PASSWORD', 'OPENCODE_SERVER_USERNAME'):
                self.assertEqual(manager.base_env.get(key), sample[key])
            for key in ('UNKNOWN_TOKEN', 'NODE_OPTIONS'):
                self.assertNotIn(key, manager.base_env)

    def test_untrusted_or_missing_fence_never_launches(self):
        from agentj import fence, state
        from pathlib import Path
        st = mock.Mock(root=Path('/fake/state'), perm_dir=Path('/fake/state/agentperm'))
        st.agent_config.return_value = {'dir': '/fake/work'}
        with mock.patch.object(olares_supervisor, 'trusted', return_value=True), \
             mock.patch.object(state, 'State', return_value=st), \
             mock.patch.object(state, 'default_state', return_value=st.root), \
             mock.patch.object(Path, 'is_dir', return_value=True), \
             mock.patch.object(Path, 'mkdir'), \
             mock.patch.object(fence, 'bwrap_argv', side_effect=OSError('unavailable')):
            self.assertEqual(olares_probe.run('detect', 'claude')['usable'], [])

    def test_fake_launch_has_only_fixed_child_and_original_fence(self):
        from agentj import fence, state
        from pathlib import Path
        st = mock.Mock(root=Path('/fake/state'), perm_dir=Path('/fake/state/agentperm'))
        st.agent_config.return_value = {'dir': '/fake/work'}
        command = ['bwrap', '--unshare-pid', '--as-pid-1', '--proc', '/proc',
                   '--tmpfs', '/tmp', '--tmpfs', '/fake/state', '--bind',
                   '/fake/state/agentperm', '/fake/state/agentperm', '--']
        payload = {'harnesses': [{'name': 'claude', 'installed': True, 'supported': True,
                                'logged_in': None, 'version': '1.2.3', 'callable': True}]}
        import json
        proc = mock.Mock(pid=123456, wait=mock.Mock(return_value=0))
        selector = mock.MagicMock()
        selector.__enter__.return_value.select.return_value = [True]
        with mock.patch.object(olares_supervisor, 'trusted', return_value=True), \
             mock.patch.object(state, 'State', return_value=st), \
             mock.patch.object(state, 'default_state', return_value=st.root), \
             mock.patch.object(Path, 'is_dir', return_value=True), \
             mock.patch.object(Path, 'mkdir'), \
             mock.patch.object(fence, 'bwrap_argv', return_value=command), \
             mock.patch.object(subprocess, 'Popen', return_value=proc) as launch, \
             mock.patch.object(olares_probe.selectors, 'DefaultSelector', return_value=selector), \
             mock.patch.object(os, 'read', side_effect=[json.dumps(payload).encode(), b'']), \
             mock.patch.object(os, 'killpg'):
            res = olares_probe.run('probe', 'claude', environ={'HOME': '/fake/home', 'PATH': '/usr/bin',
                                     'OLARES_API_KEY': 'synthetic', 'NODE_OPTIONS': 'synthetic'})
            self.assertEqual(res['usable'], ['claude'])
            argv = launch.call_args.args[0]
            self.assertEqual(argv[0], '/usr/bin/bwrap')
            self.assertEqual(argv[-4:], ['/opt/agentj/bin/python', '-I', '-c', 'from agentj.olares_probe import main; main()'])
            self.assertNotIn('--bind', argv)
            self.assertIn('--unshare-pid', argv)
            self.assertEqual(launch.call_args.kwargs['env'], {'HOME': '/fake/home', 'PATH': '/usr/bin'})
            self.assertEqual(json.loads(proc.stdin.write.call_args.args[0]),
                             {'op': 'probe', 'kind': 'claude', 'model': None, 'timeout': 60})

    def test_child_entry_uses_canonical_module_instance(self):
        # -m creates __main__ plus a later second agentj.olares_probe instance:
        # harness would see _CHILD=None and recursively refuse normal detection.
        argv = olares_probe.child_argv()
        self.assertEqual(argv[:3], [olares_supervisor.IMAGE_PYTHON, '-I', '-c'])
        with mock.patch.object(olares_probe, 'main') as canonical:
            exec(compile(argv[3], '<fixed-child-entry>', 'exec'), {})
            canonical.assert_called_once_with()

    def test_version_child_private_native_environment_preserves_global_home(self):
        sample={'HOME':'/fake/original','XDG_DATA_HOME':'/fake/original/data',
                'CLAUDE_CONFIG_DIR':'/fake/auth','CODEX_HOME':'/fake/auth2','ANTHROPIC_API_KEY':'synthetic'}
        with mock.patch.object(olares_probe,'inside',return_value=True), \
             mock.patch.object(olares_probe,'_CHILD','opencode'), \
             mock.patch.dict(os.environ,sample), \
             mock.patch.object(subprocess,'run',return_value=mock.Mock(returncode=0,stdout='2.2.5',stderr='')) as run:
            self.assertEqual(harness.version_of('/fake/opencode'),'2.2.5')
            native=run.call_args.kwargs['env']
            self.assertEqual(native['HOME'],'/fake/version-home')
            self.assertEqual(native['XDG_DATA_HOME'],'/fake/version-home/data')
            for key in ('CLAUDE_CONFIG_DIR','CODEX_HOME','ANTHROPIC_API_KEY'):
                self.assertNotIn(key,native)
            self.assertEqual(os.environ['HOME'],sample['HOME'])
            self.assertEqual(os.environ['XDG_DATA_HOME'],sample['XDG_DATA_HOME'])

    def test_known_kind_version_routes_semantically_and_rejects_selection_mismatch(self):
        answer={'harnesses':[{'name':'opencode','supported':True,'installed':True,
                 'logged_in':None,'version':'2.2.5','executable':'/fake/opencode'}]}
        with mock.patch.object(olares_probe,'run',return_value=answer) as run, \
             mock.patch.object(harness,'version_of',side_effect=AssertionError('native host version')):
            self.assertIsNone(harness.selected_version('unknown','/fake/opencode'))
            self.assertIsNone(harness.selected_version('opencode',None))
            run.assert_not_called()
            self.assertEqual(harness.selected_version('opencode','/fake/opencode'),'2.2.5')
            self.assertIsNone(harness.selected_version('opencode','/fake/other'))
            self.assertEqual(run.call_args_list[0],mock.call('detect','opencode'))

    def test_opencode_protocol_selection_uses_known_kind_route(self):
        import asyncio,tempfile
        from types import SimpleNamespace
        from agentj import agent_opencode as oc,agent_opencode2 as oc2
        agent=object.__new__(oc.OpenCodeAgent)
        agent.host=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()))
        agent.proc=None
        async def in_memory_thread(fn,*args):
            return fn(*args)
        with mock.patch.object(asyncio,'to_thread',side_effect=in_memory_thread), tempfile.NamedTemporaryFile() as exe, \
             mock.patch.object(oc,'_bin',return_value=exe.name), \
             mock.patch.object(harness,'selected_version',return_value='2.2.5') as version, \
             mock.patch.object(harness,'version_of',side_effect=AssertionError('native host version')):
            self.assertTrue(asyncio.run(agent._protocol()))
            version.assert_called_once_with('opencode',exe.name)
            self.assertIs(type(agent),oc2.OpenCodeV2Agent)

    def test_default_state_start_fields_roundtrip_as_own_server(self):
        import tempfile
        from pathlib import Path
        from test_p155_switch_harness import mk_state
        with tempfile.TemporaryDirectory() as folder:
            st=mk_state(Path(folder)/'state','opencode')
            cfg=st.agent_config()
            self.assertEqual(cfg['shared_opencode_port'],0)
            self.assertEqual(cfg['shared_session_id'],'')
            with mock.patch.object(olares_probe,'spec',wraps=olares_probe.spec) as check, \
                 mock.patch.object(olares_supervisor,'trusted',return_value=False):
                olares_probe.run('start','opencode',cfg=cfg)
                request=check.call_args.args[0]
                self.assertNotIn('shared_opencode_port',request)
                self.assertNotIn('shared_session_id',request)
                self.assertEqual(olares_probe.spec(request),request)

    def test_codex_start_sends_only_consumed_shared_mode(self):
        cfg={'session_mode':'shared','shared_opencode_port':12345,
             'shared_session_id':'019a3af0-1234-7000-8000-123456789abc'}
        with mock.patch.object(olares_probe,'spec',wraps=olares_probe.spec) as check, \
             mock.patch.object(olares_supervisor,'trusted',return_value=False):
            olares_probe.run('start','codex','gpt-6',cfg=cfg)
            request=check.call_args.args[0]
            self.assertEqual(request['session_mode'],'shared')
            self.assertNotIn('shared_opencode_port',request)
            self.assertNotIn('shared_session_id',request)
            self.assertEqual(olares_probe.spec(request),request)
        self.assertEqual(cfg['shared_opencode_port'],12345)
        self.assertEqual(cfg['shared_session_id'],'019a3af0-1234-7000-8000-123456789abc')

    def test_explicit_owner_server_fields_remain_validated(self):
        cfg={'session_mode':'shared','shared_opencode_port':12345,'shared_session_id':'ses_fixture'}
        with mock.patch.object(olares_probe,'spec',wraps=olares_probe.spec) as check, \
             mock.patch.object(olares_supervisor,'trusted',return_value=False):
            olares_probe.run('start','opencode',cfg=cfg)
            request=check.call_args.args[0]
            for key,value in cfg.items():self.assertEqual(request[key],value)
            self.assertEqual(olares_probe.spec(request),request)

    def test_start_does_not_silently_normalize_invalid_owner_fields(self):
        for key,values in (('shared_opencode_port',(False,-1,'0',65536)),
                           ('shared_session_id',(False,0,' ','invalid'))):
            for value in values:
                with self.subTest(key=key,value=value), \
                     mock.patch.object(olares_supervisor,'trusted') as trust:
                    self.assertEqual(olares_probe.run('start','opencode',cfg={key:value})['reason'],'start_failed')
                    trust.assert_not_called()

    def test_owner_server_auth_only_for_validated_shared_start(self):
        import json
        from pathlib import Path
        from agentj import fence,state
        cases=[('start',{'session_mode':'shared','shared_opencode_port':12345,'shared_session_id':'ses_fixture'},True),
               ('start',{'session_mode':'shared','shared_opencode_port':0,'shared_session_id':''},False),
               ('start',{'session_mode':'independent','shared_opencode_port':12345},False),
               ('probe',{},False),('detect',{},False)]
        sample={'HOME':'/fake/home','PATH':'/usr/bin','OPENCODE_SERVER_PASSWORD':'synthetic-only',
                'OPENCODE_SERVER_USERNAME':'synthetic-owner','OLARES_API_KEY':'synthetic-manager'}
        for op,cfg,expected in cases:
            with self.subTest(op=op,cfg=cfg):
                st=mock.Mock(root=Path('/fake/state'),perm_dir=Path('/fake/state/agentperm'))
                st.agent_config.return_value={'dir':'/fake/work'}
                command=['bwrap','--tmpfs','/fake/state','--bind','/fake/state/agentperm','/fake/state/agentperm','--']
                response={'secs':.1} if op=='start' else {'harnesses':[{'name':'opencode','installed':True,'supported':True,'logged_in':None}]}
                proc=mock.Mock(pid=123456,wait=mock.Mock(return_value=0))
                selector=mock.MagicMock();selector.__enter__.return_value.select.return_value=[True]
                with mock.patch.object(olares_supervisor,'trusted',return_value=True), \
                     mock.patch.object(state,'State',return_value=st), \
                     mock.patch.object(state,'default_state',return_value=st.root), \
                     mock.patch.object(Path,'is_dir',return_value=True),mock.patch.object(Path,'mkdir'), \
                     mock.patch.object(fence,'bwrap_argv',return_value=command), \
                     mock.patch.object(subprocess,'Popen',return_value=proc) as launch, \
                     mock.patch.object(olares_probe.selectors,'DefaultSelector',return_value=selector), \
                     mock.patch.object(os,'read',side_effect=[json.dumps(response).encode(),b'']), \
                     mock.patch.object(os,'killpg'):
                    olares_probe.run(op,'opencode',cfg=cfg,environ=sample)
                    env=launch.call_args.kwargs['env']
                    for key in ('OPENCODE_SERVER_PASSWORD','OPENCODE_SERVER_USERNAME'):
                        self.assertEqual(key in env,expected)
                        if expected:self.assertEqual(env[key],sample[key])
                    self.assertNotIn('OLARES_API_KEY',env)

    def test_semantic_request_rejects_paths_and_bad_start_kind(self):
        for req in ({'op': 'detect', 'kind': 'claude', 'timeout': 60, 'argv': []},
                    {'op': 'start', 'kind': 'claude', 'timeout': 60}):
            with self.subTest(req=req), self.assertRaises(ValueError):
                olares_probe.spec(req)

    def test_auxiliary_host_entrypoints_refuse_native_calls(self):
        from agentj import fallback, channel_free, opencode_free, agent_kimi
        self.assertIsNone(fallback.free_models('/fake/candidate'))
        self.assertEqual(channel_free.catalogue('/fake/candidate'), ([], None))
        with self.assertRaises(opencode_free.ProbeError):
            opencode_free.serve('/fake/candidate', {}, '/fake/work', 12345, 5)
        with self.assertRaises(agent_kimi.ProbeError):
            agent_kimi.probe('/fake/candidate', {})

    def test_olares_layout_without_trust_still_requires_boundary(self):
        with mock.patch.object(olares_supervisor, 'enabled', return_value=False), \
             mock.patch.object(type(olares_supervisor.ENTRY), 'exists', return_value=True):
            self.assertTrue(olares_probe.required())
            self.assertEqual(olares_probe.run('probe', 'claude')['usable'], [])
            self.assertIsNone(harness.version_of('/fake/candidate'))
            from agentj import opencode_free, opencode_start
            self.assertIsNone(opencode_free.listed('/fake/candidate'))
            self.assertEqual(opencode_start.start_probe(None, {})['reason'], 'start_failed')

    def test_sealed_requires_empty_hidden_state_and_pid_mounts(self):
        from agentj import state
        from pathlib import Path
        mountinfo = ('1 0 0:1 / /fake/state rw - tmpfs tmpfs rw\n'
                     '2 1 0:2 / /fake/state/agentperm rw - tmpfs tmpfs rw\n'
                     '3 0 0:3 / /tmp rw - tmpfs tmpfs rw\n'
                     '4 0 0:4 / /proc rw - proc proc rw\n')
        def entries(path):
            return iter([Path('/fake/state/agentperm')]) if str(path) == '/fake/state' else iter([])
        with mock.patch.object(state, 'default_state', return_value=Path('/fake/state')), \
             mock.patch.object(Path, 'resolve', lambda p: p), \
             mock.patch.object(Path, 'read_text', return_value=mountinfo), \
             mock.patch.object(Path, 'iterdir', entries), \
             mock.patch.object(Path, 'stat', return_value=mock.Mock(st_ino=123)):
            self.assertTrue(olares_probe.sealed())
            with mock.patch.object(Path, 'read_text', return_value=mountinfo.replace('tmpfs tmpfs', 'ext4 ext4')):
                self.assertFalse(olares_probe.sealed())

    def test_direct_native_probe_helpers_fail_before_using_caller_inputs(self):
        from agentj import opencode_free, agent_opencode2, agent_qwen, agent_dsh, agent_kimi, ark_plan_fix
        for fn, args in ((opencode_free.probe, ('/fake/candidate', None, {}, 5)),
                         (agent_opencode2.probe, ('/fake/candidate', None, {}, 5)),
                         (agent_qwen.probe, ('/fake/candidate', {})),
                         (agent_dsh.probe, ('/fake/candidate', {}))):
            with self.subTest(fn=fn.__module__), self.assertRaises((opencode_free.ProbeError, agent_kimi.ProbeError)):
                fn(*args)
        with mock.patch.object(harness, 'agent_bin', side_effect=AssertionError('host resolution')):
            self.assertFalse(ark_plan_fix._native_probe(None, None, None, None))

    def test_non_olares_version_query_keeps_existing_behavior_with_fake_process(self):
        with mock.patch.object(olares_probe, 'required', return_value=False), \
             mock.patch.object(subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='1.2.3', stderr='')) as run:
            self.assertEqual(harness.version_of('/fake/candidate'), '1.2.3')
            run.assert_called_once()

    def test_untrusted_fence_binary_is_refused_before_state_or_process(self):
        from agentj import state
        with mock.patch.object(olares_supervisor, 'trusted', side_effect=lambda p: p != '/usr/bin/bwrap'), \
             mock.patch.object(state, 'State', side_effect=AssertionError('must refuse before State')):
            self.assertEqual(olares_probe.run('start', 'codex')['reason'], 'start_failed')


if __name__ == '__main__':
    unittest.main()
