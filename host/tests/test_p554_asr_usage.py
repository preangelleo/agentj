"""#554: completed ASR provenance is local, bounded, and queryable without guessing config."""
import contextlib
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from _testtmp import TEST_TMP
from agentj import asr, preferences as p, voice


def config(mode='custom', engine='sensevoice'):
    return p.validate({'voice': {'asr': {'mode': mode, 'engine': engine,
        'base_url': 'http://127.0.0.1:12345/v1', 'model': 'Qwen/Qwen3-ASR-1.7B'}}})


class Usage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def read(self):
        return asr.status(self.root)['last_used']

    def test_no_success_is_explicit_null(self):
        self.assertIsNone(self.read())

    def test_custom_success_records_actual_model_without_content_or_endpoint(self):
        local=Mock()
        with patch('agentj.voice.cloud_asr', return_value={'ok': True, 'engine': 'custom', 'text': 'PRIVATE_TRANSCRIPT'}):
            voice.transcribe('/PRIVATE_AUDIO', config(), state_dir=self.root, local_asr=local)
        row=self.read()
        self.assertEqual(row['engine'], 'custom')
        self.assertEqual(row['model'], 'Qwen/Qwen3-ASR-1.7B')
        self.assertFalse(row['fallback'])
        self.assertGreaterEqual(row['elapsed_ms'], 0)
        self.assertTrue(row['at'].endswith('Z'))
        raw=(self.root/'asr'/'last_used.json').read_text()
        for forbidden in ('PRIVATE_', '12345', 'http', 'text', 'path'):
            self.assertNotIn(forbidden, raw)
        self.assertEqual((self.root/'asr'/'last_used.json').stat().st_mode & 0o777, 0o600)

    def test_remote_failure_records_actual_fallback_model_and_total_time(self):
        clock = [10.]
        def remote(*args, **kwargs):
            clock[0] = 11.
            return {'ok': False}
        def recover(*args, **kwargs):
            clock[0] = 12.
            return {'ok': True, 'engine': 'sherpa', 'text': 'local'}
        local = Mock()
        local.transcribe.side_effect = recover
        with patch('agentj.voice.cloud_asr', side_effect=remote), patch('agentj.voice.time.monotonic', side_effect=lambda: clock[0]):
            voice.transcribe('/sample', config(), state_dir=self.root, local_asr=local)
        row=self.read()
        self.assertEqual((row['engine'], row['model'], row['fallback']), ('sherpa', asr.MODEL_ID, True))
        self.assertEqual(row['elapsed_ms'], 2000)

    def test_failure_preserves_previous_success_and_timestamp(self):
        local=Mock();local.transcribe.return_value={'ok': False, 'reason': 'off'}
        with patch('agentj.voice.cloud_asr', return_value={'ok': True, 'engine': 'custom', 'text': 'a'}):
            voice.transcribe('/sample', config(), state_dir=self.root, local_asr=local)
        before=self.read()
        with patch('agentj.voice.cloud_asr', return_value={'ok': False}):
            result=voice.transcribe('/sample', config(engine='off'), state_dir=self.root, local_asr=local)
        self.assertFalse(result['ok']);self.assertEqual(self.read(), before)

    def test_direct_local_and_voxtype_fallback_are_recorded(self):
        with patch('agentj.asr._transcribe_once', return_value={'ok': True, 'engine': 'sherpa', 'text': 'a'}):
            asr.transcribe('/sample', timeout_s=5, state_dir=self.root, engine_override='sensevoice')
        self.assertFalse(self.read()['fallback'])
        with patch('agentj.asr._transcribe_once', side_effect=[{'ok': False}, {'ok': True, 'engine': 'sherpa', 'text': 'a'}]):
            asr.transcribe('/sample', timeout_s=5, state_dir=self.root, engine_override='voxtype')
        self.assertTrue(self.read()['fallback'])

    def test_voxtype_model_is_unknown_not_inferred_from_current_preferences(self):
        with patch('agentj.asr._transcribe_once', return_value={'ok': True, 'engine': 'voxtype', 'text': 'a'}):
            asr.transcribe('/sample', timeout_s=5, state_dir=self.root, engine_override='voxtype')
        self.assertEqual(self.read()['engine'], 'voxtype');self.assertIsNone(self.read()['model'])

    def test_corrupt_record_is_null(self):
        d=self.root/'asr';d.mkdir();(d/'last_used.json').write_text('{bad')
        self.assertIsNone(self.read())

    def test_custom_status_uses_actual_fallback_setting_and_last_used(self):
        from agentj import cli
        for engine, expected in [('off', False), ('sensevoice', True), ('voxtype', True)]:
            output=io.StringIO()
            with patch('agentj.cli.State', return_value=SimpleNamespace(root=self.root)), patch('agentj.cli._need_init'), patch('agentj.preferences.effective', return_value=config(engine=engine)), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as cm:
                cli.cmd_asr(SimpleNamespace(args=['status', '--json']))
            self.assertEqual(cm.exception.code, 0)
            result=json.loads(output.getvalue())
            self.assertEqual(result['local_fallback'], expected)
            self.assertIn('last_used', result);self.assertIsNone(result['last_used'])

    def test_cloud_status_and_test_use_shared_routing(self):
        from agentj import cli
        output=io.StringIO()
        cfg=config(mode='cloud')
        with patch('agentj.cli.State', return_value=SimpleNamespace(root=self.root)), patch('agentj.cli._need_init'), patch('agentj.preferences.effective', return_value=cfg), contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            cli.cmd_asr(SimpleNamespace(args=['status', '--json']))
        self.assertEqual(json.loads(output.getvalue())['active'], p.get(cfg, 'voice.asr.provider'))

    def test_write_failure_does_not_fail_successful_transcription(self):
        with patch('agentj.voice.cloud_asr', return_value={'ok': True, 'engine': 'custom', 'text': 'a'}), patch('tempfile.mkstemp', side_effect=OSError('PRIVATE_SECRET')):
            result=voice.transcribe('/sample', config(), state_dir=self.root, local_asr=Mock())
        self.assertTrue(result['ok']);self.assertIsNone(self.read())

    def test_concurrent_writes_never_mix_records_or_leave_tempfiles(self):
        from agentj import asr_usage
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: asr_usage.record({'ok': True, 'engine': 'custom'}, state_dir=self.root, model=f'model-{i}', elapsed_ms=i, fallback=False), range(40)))
        row=self.read()
        self.assertEqual(row['model'], 'model-'+str(row['elapsed_ms']))
        directory = self.root / 'asr'
        self.assertEqual({x.name for x in directory.iterdir()}, {'last_used.json', 'history.jsonl'})
        history = [json.loads(line) for line in (directory / 'history.jsonl').read_text().splitlines()]
        self.assertEqual({row['elapsed_ms'] for row in history}, set(range(40)))
        self.assertEqual(len(history), 40)
        self.assertEqual((directory / 'history.jsonl').stat().st_mode & 0o777, 0o600)

    def test_bounded_history_retains_misses_and_preserves_legacy_latest_success(self):
        from agentj import asr_usage
        directory = self.root / 'asr'
        directory.mkdir()
        legacy = {'engine': 'custom', 'model': 'test', 'at': '2026-11-28T00:44:02.000000Z',
                  'fallback': False, 'elapsed_ms': 77}
        (directory / 'last_used.json').write_text(json.dumps(legacy))
        for i in range(asr_usage.HISTORY_LIMIT + 3):
            asr_usage.record({'ok': False, 'reason': 'timeout', 'remote_failure': 'timeout', 'remote_attempted': True},
                             state_dir=self.root, model=None, elapsed_ms=i)
        history = [json.loads(line) for line in (directory / 'history.jsonl').read_text().splitlines()]
        self.assertEqual([row['elapsed_ms'] for row in history], list(range(3, asr_usage.HISTORY_LIMIT + 3)))
        self.assertTrue(all(row['result'] == 'timeout' and row['engine'] == 'remote' for row in history))
        self.assertEqual(self.read(), legacy)

    def test_status_rejects_endpoint_and_secret_shaped_model(self):
        for model in ('https://private.invalid/api?key=hidden', 'sk-'+'x'*40):
            cfg=config();cfg['voice']['asr']['model']=model
            self.assertIsNone(voice.asr_status(cfg,state_dir=self.root)['model'])

    def test_rpc_rejects_other_instance_and_arbitrary_fields(self):
        from agentj import host_diagnostics as hd
        st=SimpleNamespace(root=self.root)
        for fields in ({'selected_instance':2}, {'selected_instance':1, 'path':'/private'},
                       {'selected_instance':1, 'url':'https://private.invalid'}, {'selected_instance':True}):
            with patch.object(hd,'host_instance',return_value=1),patch('agentj.voice.asr_status') as read:
                result=hd.execute(st,{'op':'asr_status','as_json':True,**fields})
            self.assertEqual(result['exit'],1);read.assert_not_called()

    def test_fenced_missing_socket_never_reads_default_state(self):
        from agentj import host_diagnostics as hd, elevate
        with patch.object(hd,'agent_side',return_value=True),patch.dict(os.environ,{},clear=True),patch('agentj.state.State') as state,contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hd.request('asr_status',as_json=True),1)
        state.assert_not_called();self.assertEqual(json.loads(out.getvalue())['error'],'host_state_unavailable')

    def test_fenced_wrong_host_response_never_reads_default_state(self):
        from agentj import host_diagnostics as hd, elevate
        with patch.object(hd,'agent_side',return_value=True),patch.dict(os.environ,{elevate.ENV:'/nonexistent'}),patch('agentj.instance.current',return_value=1),patch('agentj.state.State') as state,patch.object(elevate,'client_request',return_value={'exit':0,'output':'PRIVATE','host_instance':2}),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hd.request('asr_status',as_json=True),1)
        state.assert_not_called();self.assertNotIn('PRIVATE',out.getvalue())


class IsolatedMainAgent(unittest.IsolatedAsyncioTestCase):
    async def test_real_socket_cli_with_state_reads_denied(self):
        from agentj import elevate, host_diagnostics as hd, asr_usage
        from agentj.state import State
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            st=State(Path(tmp)/'s');st.init(relay='ws://127.0.0.1:1')
            asr_usage.record({'ok':True,'engine':'custom'},state_dir=st.root,model='Qwen/Qwen3-ASR-1.7B',elapsed_ms=77,fallback=False)
            # Fixture follows 4676679f3a (0.30.0 native ingress): every request on the host socket is framed against the
            # live host (agent, host key, channel, history epoch, stop state) before its handler runs, so the stand-in
            # carries those. The caller here is a bare child of the test process: no live native session, no terminal.
            host=SimpleNamespace(st=st,agent=SimpleNamespace(session=None,cur_send=None,proc=None,kind='claude'),agent_cfg={},
                                 _agent_pids=lambda:set(),kp=SimpleNamespace(pub=b'h'*32),channel='synthetic-channel',
                                 hist=SimpleNamespace(epoch='synthetic'),stopped=lambda:False,stopping=asyncio.Event())
            server=elevate.Elevator(host)
            await server.start()
            try:
                child='''
import pathlib,os
from agentj import cli
from agentj.state import State
root=os.environ['AGENTJ_STATE_DIR']
old=pathlib.Path.open
def denied(self,*a,**kw):
    if str(self).startswith(root): raise PermissionError('state denied')
    return old(self,*a,**kw)
pathlib.Path.open=denied
State.exists=lambda self: (_ for _ in ()).throw(PermissionError('state denied'))
cli.main(['asr','status','--json'])
'''
                env=dict(os.environ,AGENTJ_STATE_DIR=str(st.root),AGENTJ_INSTANCE='1',PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                with patch.object(hd,'host_instance',return_value=1),patch('agentj.preferences.effective',return_value=config()):
                    proc=await asyncio.create_subprocess_exec(sys.executable,'-c',child,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    out,err=await asyncio.wait_for(proc.communicate(),20)
                self.assertEqual(proc.returncode,0,err.decode())
                row=json.loads(out)['last_used']
                self.assertEqual((row['engine'],row['model'],row['elapsed_ms']),('custom','Qwen/Qwen3-ASR-1.7B',77))
            finally:
                await server.stop()
