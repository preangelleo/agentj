import _hermetic  # All host runtime paths remain temporary during tests.
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from agentj import provider_runtime as pr
from agentj import agent_opencode as oc
from agentj.shared_opencode2 import SharedOpenCodeV2Agent

class Runtime(unittest.TestCase):
    def test_builtin_auth_errors_use_phone_path_and_one_language(self):
        a = object.__new__(oc.OpenCodeAgent)
        for lang in ('zh', 'en'):
            a.cfg = {'language':lang}
            with mock.patch.object(pr, 'key_name', return_value=None):
                for reason in ('login','no_key'):
                    text = a.provider_note(reason, 'openrouter', 'wrong desktop command')
                    self.assertNotIn('opencode auth', text)
                    self.assertNotIn('wrong desktop', text)
                    self.assertIn('模型与 Key' if lang == 'zh' else 'Models & Key', text)
                    if lang == 'zh': self.assertNotIn('The account', text)
                    else: self.assertNotIn('账户', text)

    def test_provider_failed_verification_never_overwrites_key(self):
        from agentj import elevate
        e = object.__new__(elevate.Elevator)
        with mock.patch.object(elevate, 'verify_secret', return_value={'verify':'fail','detail':'HTTP 401'}), mock.patch.object(elevate, 'write_secret') as write:
            result = e._save({'verify_before_write':True}, bytearray(b'test-fixture-invalid'))
        self.assertEqual(result['why'], 'verify')
        write.assert_not_called()

    def test_environment_reads_changed_file_not_old_parent(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'agentj.env'
            cfg = {'relay': {'options': {'apiKey': '{env:RELAY_API_KEY}'}}}
            with mock.patch.object(pr, 'configured', return_value=cfg), mock.patch.object(pr, 'env_path', return_value=str(p)):
                p.write_text('RELAY_API_KEY=first\nUNRELATED_KEY=ignore\n')
                base = {'RELAY_API_KEY': 'old'}
                self.assertEqual(pr.fresh_environment(base)['RELAY_API_KEY'], 'first')
                p.write_text('RELAY_API_KEY="second"\n')
                self.assertEqual(pr.fresh_environment(base)['RELAY_API_KEY'], 'second')
                self.assertEqual(base['RELAY_API_KEY'], 'old')
                self.assertNotIn('UNRELATED_KEY', pr.fresh_environment(base))
    def test_usage_windows_precise_and_null_hidden(self):
        v = pr.windows({'subscription': {'weekly_usage_usd': 2, 'weekly_limit_usd': 10, 'daily_usage_usd': 1, 'daily_limit_usd': 4}})
        self.assertEqual([x['window'] for x in v], ['weekly', 'daily'])
        self.assertEqual([x['pct'] for x in v], [20, 25])
        self.assertEqual(pr.windows(None), [])
        self.assertEqual(pr.windows({'subscription': {'weekly_limit_usd': 0}}), [])
    def test_usage_endpoint_and_private_rejection(self):
        with mock.patch.object(pr.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]):
            self.assertEqual(pr.public_url('https://relay.example/v1/', 'usage'), 'https://relay.example/v1/usage')
        with mock.patch.object(pr.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            self.assertIsNone(pr.public_url('https://relay.example', 'usage'))
        for u in ('http://relay.example', 'https://key@relay.example', 'https://relay.example/?key=value', None):
            self.assertIsNone(pr.public_url(u, 'usage'))
    def test_local_model_catalogue_exists_even_without_server_key(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.models_cache = []
        a.host = SimpleNamespace(models_changed=mock.Mock())
        with mock.patch.object(pr, 'configured', return_value={'relay': {'models': {'m': {'name': 'M'}}}}):
            a._set_models({})
        self.assertEqual(a.models_cache[0]['id'], 'relay/m')
    def test_malformed_config_does_not_break_host(self):
        with mock.patch.object(pr, 'configured', return_value={'relay': {'options': None}}):
            self.assertIsNone(pr.key_name('relay'))
        with mock.patch.object(pr, 'configured', return_value={'relay': {'options': {'apiKey': []}}}):
            self.assertIsNone(pr.key_name('relay'))

class AsyncRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_requested_stop_during_prepare_does_not_emit_false_startup_failure(self):
        from agentj.serve import Host
        from agentj.state import State
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); work=root/'work'; work.mkdir()
            wrapper=work/'opencode'
            fake=Path(__file__).with_name('fakeopencode2.py')
            wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {fake} "$@"\n'); wrapper.chmod(0o700)
            st=State(root/'state'); st.init(relay='ws://127.0.0.1:1')
            st.set_agent_config('opencode', str(work), model='opencode/test', fence=False)
            host=Host(st, read_stdin=False)
            host.agent_notice=mock.Mock()
            a=oc.OpenCodeAgent(host, host.agent_cfg)
            a.fail_notice=mock.Mock()
            entered=asyncio.Event(); released=asyncio.Event()
            async def blocked_prepare():
                entered.set()
                await released.wait()
                raise oc.HTTPError('prepare stopped')
            a._prepare=blocked_prepare
            with mock.patch.dict(os.environ, {'AGENTJ_OPENCODE_BIN':str(wrapper)}):
                spawn=asyncio.create_task(a._spawn())
                await asyncio.wait_for(entered.wait(), 5)
                proc=a.proc
                await a.halt()
                self.assertTrue(proc._agentj_requested_stop)
                released.set()
                self.assertFalse(await spawn)
                a.fail_notice.assert_not_called()
                await a.stop()

    async def test_phone_ready_gets_configured_model_and_catalogue_before_first_turn(self):
        from agentj.serve import Host, Session
        from agentj.state import State
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root/'work'; work.mkdir()
            st = State(root/'state'); st.init(relay='ws://127.0.0.1:1')
            st.set_agent_config('opencode', str(work), model='relay/m', fence=False)
            host = Host(st, read_stdin=False)
            with mock.patch.object(pr, 'configured', return_value={'relay':{'models':{'m':{'name':'M'}}}}):
                host.agent = oc.OpenCodeAgent(host, host.agent_cfg)
            session = Session(cid=1); session.p33=True; session.state='ready';session.device='paired-device'
            host.sessions[1] = session
            host.send_app = mock.AsyncMock(return_value=True)
            host.elevate.on_ready = mock.AsyncMock()
            with mock.patch.object(pr, 'key_name', return_value=None), mock.patch.object(pr, 'probe', return_value=[]):
                await host.on_ready(session, 0)
                await asyncio.sleep(.01)
            sent = [c.args[1] for c in host.send_app.call_args_list]
            self.assertEqual(next(x for x in sent if x['t']=='meter')['model'], 'relay/m')
            self.assertEqual(next(x for x in sent if x['t']=='models')['models'][0]['id'], 'relay/m')
            self.assertIsNone(host.agent.proc)
            if host.meter_task:
                host.meter_task.cancel()

    async def test_metadata_control_returns_only_whitelisted_model_and_numbers(self):
        from agentj.serve import Host
        agent = SimpleNamespace(kind='opencode', cur_model=lambda:'relay/m', _providers=mock.AsyncMock(return_value={'providers':[{'id':'relay', 'options':{'apiKey':'private-fixture-value'}, 'models':{'m':{'limit':{'context':100}, 'options':{'secret':'private-fixture-value'}}}}]}), _last_assistant=mock.AsyncMock(return_value={'tokens':{'input':15}, 'text':'private-fixture-answer'}), _ctx=oc.OpenCodeAgent._ctx)
        host=SimpleNamespace(agent=agent,_ctl_send=mock.AsyncMock())
        reader=asyncio.StreamReader();reader.feed_data(b'{"cmd":"opencode_metadata"}\n');reader.feed_eof()
        await Host.on_ctl(host, reader, SimpleNamespace(close=lambda:None))
        result=host._ctl_send.call_args.args[1]
        self.assertEqual(result, {'ok':True,'model':'relay/m','assistant_model':None,'metadata_present':True,'context_limit':100,'context_used':15})
        self.assertNotIn('private-fixture', json.dumps(result))

    async def test_model_switch_clears_previous_provider_quota_and_context(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.cfg = {'model':'relay/old'}
        a.models_cache = []
        a.meter = mock.Mock()
        def set_model(model):
            a.cfg['model'] = model
        a.host = SimpleNamespace(set_model=set_model)
        self.assertIsNone(await a.apply_model('other/new', None))
        self.assertEqual(a.cfg['model'], 'other/new')
        self.assertEqual(a.meter.call_args.kwargs, {'ctx':None,'quota_windows':[]})

    async def test_context_measured_tokens_and_limit_reach_meter_without_guessing(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.cfg = {'model':'relay/m'}
        a.models_cache = [{'id':'relay/m','name':'M'}]
        a._last_assistant = mock.AsyncMock(return_value={'tokens':{'input':10,'output':2,'cache':{'read':3}}})
        a._providers = mock.AsyncMock(return_value={'providers':[{'id':'relay','models':{'m':{'limit':{'context':100}}}}]})
        a.meter = mock.Mock()
        with mock.patch.object(pr, 'configured', return_value={}), mock.patch.object(pr, 'probe', return_value=[]):
            await a.context_meter()
            self.assertEqual(a.meter.call_args.kwargs['ctx'], {'used':15,'max':100})
            a._providers.return_value = {}
            await a.context_meter()
        self.assertEqual(a.meter.call_args.kwargs['ctx'], {'used':15,'max':None})

    async def test_unapproved_or_revoked_devices_cannot_run_new_actions(self):
        from agentj.serve import Host
        for t in ('provider_get', 'provider_key', 'models_get', 'asr_install', 'ping'):
            host = SimpleNamespace(on_p33=mock.AsyncMock(), close_cid=mock.AsyncMock(), st=SimpleNamespace(is_allowed=lambda _:False))
            await Host._app(host, SimpleNamespace(state='pending'), {'t':t,'r':'test-1'})
            host.on_p33.assert_not_awaited()
            await Host._app(host, SimpleNamespace(state='ready',pub=b'unapproved',cid=1), {'t':t,'r':'test-1'})
            host.on_p33.assert_not_awaited()
            host.close_cid.assert_awaited_once_with(1, 'revoked')

    async def test_new_actions_reject_invalid_request_ids(self):
        from agentj.serve import Host
        host = SimpleNamespace(send_app=mock.AsyncMock(), asr=SimpleNamespace(install=mock.Mock()))
        for t in ('provider_get','provider_key','models_get','asr_install','ping'):
            for r in (None, 3, 'bad request id'):
                await Host.on_p33(host, SimpleNamespace(), t, {'r':r})
        host.send_app.assert_not_awaited()
        host.asr.install.assert_not_called()

    async def test_models_get_refreshes_catalogue_and_never_installs_asr(self):
        from agentj.serve import Host
        host = SimpleNamespace(agent=SimpleNamespace(kind='opencode',_set_models=mock.Mock(),refresh_models=mock.AsyncMock()), send_app=mock.AsyncMock(), _models_msg=lambda:{'t':'models','models':[]}, asr=SimpleNamespace(install=mock.Mock()))
        await Host.on_p33(host, SimpleNamespace(), 'models_get', {'r':'models-test-1'})
        await host._catalogue_refresh_task
        host.agent.refresh_models.assert_awaited_once()
        host.asr.install.assert_not_called()
        self.assertEqual(host.send_app.call_args.args[1]['t'], 'models')

    async def test_asr_zero_exit_is_not_ready_when_off_or_broken(self):
        from agentj.serve import Host
        for state in ('off', 'broken'):
            host = SimpleNamespace(asr=SimpleNamespace(install=mock.Mock(return_value=0)), st=SimpleNamespace(root=Path('/tmp/p67-asr-state'), log=mock.Mock()), send_app=mock.AsyncMock(), asr_state=lambda:state)
            await Host.on_p33(host, SimpleNamespace(device='paired-test-device'), 'asr_install', {'r':'asr-test-1'})
            for _ in range(50):
                if host.send_app.called: break
                await asyncio.sleep(.01)
            self.assertFalse(host.send_app.call_args.args[1]['ok'])
            self.assertEqual(host.send_app.call_args.args[1]['why'], state)

    async def test_asr_phone_install_message_calls_host_installer(self):
        from agentj.serve import Host
        # on_p33 is reached only after the paired Noise session is ready (on_frame).
        host = SimpleNamespace(asr=SimpleNamespace(install=mock.Mock(return_value=0)), st=SimpleNamespace(root=Path('/tmp/p67-asr-state'), log=mock.Mock()), send_app=mock.AsyncMock(), asr_state=lambda:'ready')
        session = SimpleNamespace(device='paired-test-device')
        await Host.on_p33(host, session, 'asr_install', {'r':'asr-test-1'})
        for _ in range(50):
            if host.send_app.called:
                break
            await asyncio.sleep(.01)
        host.asr.install.assert_called_once()
        self.assertTrue(host.asr.install.call_args.kwargs['yes'])
        self.assertTrue(host.send_app.call_args.args[1]['ok'])

    async def test_provider_open_cards_are_deduplicated(self):
        host = SimpleNamespace(_provider_cards={'relay'})
        with mock.patch.object(pr, 'key_name', return_value='RELAY_API_KEY'):
            result = await pr.request_key(host, 'relay')
        self.assertEqual(result['result'], 'busy')

    async def test_provider_card_existing_env_reference_and_header(self):
        cfg = {'relay': {'npm': '@ai-sdk/anthropic', 'options': {'apiKey': '{env:RELAY_API_KEY}', 'baseURL': 'https://relay.example'}}}
        host = SimpleNamespace(st=SimpleNamespace(root=Path(tempfile.gettempdir())/'p67-card-state'), elevate=SimpleNamespace(request=mock.AsyncMock(return_value={'result':'ok'})))
        with mock.patch.object(pr, 'configured', return_value=cfg), mock.patch.object(pr, 'env_path', return_value='/tmp/agentj-test.env'), mock.patch.object(pr, 'public_url', return_value='https://relay.example/v1/models'):
            await pr.request_key(host, 'relay')
        card = host.elevate.request.call_args.args[0]
        self.assertEqual(card['verify_header'], 'x-api-key: {value}')
        self.assertEqual(card['name'], 'RELAY_API_KEY')
        self.assertIn('RELAY_API_KEY', card['dest'])
    async def test_shared_owned_injects_language_on_every_change(self):
        a = object.__new__(SharedOpenCodeV2Agent)
        a.cfg = {'language': 'zh', '_workflow_ceo': False}
        a.owned_server = True
        a.persist = True
        a.sid = 'test-session'
        a.identity_sent = {}
        a.client = SimpleNamespace(request=mock.AsyncMock(return_value=(200, {})))
        await a._identity()
        first = a.client.request.call_args.args[2]['value']
        self.assertIn('只用中文', first)
        a.cfg['language'] = 'en'
        await a._identity()
        second = a.client.request.call_args.args[2]['value']
        self.assertIn('only in English', second)
        self.assertNotEqual(first, second)
        self.assertEqual(a.client.request.call_count, 2)
    async def test_custom_401_automatically_requests_single_language_card(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.cfg = {'language': 'zh'}
        a.host = object()
        pending = []
        a._bg = pending.append
        with mock.patch.object(pr, 'key_name', return_value='RELAY_API_KEY'):
            text = a.provider_note('login', 'relay', 'wrong fallback')
        self.assertIn('密钥卡', text)
        self.assertNotIn('auth login', text)
        self.assertNotIn('English', text)
        pending[0].close()


class IsolationDiagnostic(unittest.TestCase):
    def test_missing_bwrap_reports_both_prerequisites_without_global_relaxation(self):
        from agentj import doctor, fence
        from agentj.state import State
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(doctor.shutil, 'which', return_value=None), mock.patch.object(fence, 'apparmor_userns_restricted', return_value=True):
            row = doctor.check_fence(State(Path(d)/'state'), required=True)
        self.assertEqual((row['status'],row['reason']), ('fail','no_bwrap'))
        self.assertIn('bubblewrap', row['hint'])
        self.assertIn('/etc/apparmor.d/bwrap-agentj', row['hint'])
        self.assertIn('profile bwrap-agentj /usr/bin/bwrap', row['hint'])
        self.assertNotIn('userns=0', row['hint'])

    def test_restricted_policy_passes_when_real_probe_passes(self):
        from agentj import doctor, fence
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(doctor.shutil, 'which', return_value='/usr/bin/bwrap'), mock.patch.object(fence, 'problem', return_value=None), mock.patch.object(fence, 'apparmor_userns_restricted', return_value=True), mock.patch.object(fence,'apparmor_userns_policy',return_value=True):
            row = doctor.isolation_preflight()
        self.assertEqual(row['status'], 'ok')
        self.assertTrue(row['apparmor_restrict_userns'])
        self.assertIsNone(row['reason'])

    def test_blocked_restricted_policy_has_only_per_binary_repair(self):
        from agentj import doctor, fence
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.object(doctor.shutil, 'which', return_value='/usr/bin/bwrap'), mock.patch.object(doctor, '_in_container', return_value=False), mock.patch.object(fence, 'problem', return_value='apparmor_userns'), mock.patch.object(fence, 'apparmor_userns_restricted', return_value=True):
            row = doctor.isolation_preflight()
        self.assertEqual((row['status'], row['reason']), ('fail','apparmor_userns'))
        self.assertIn('apparmor_parser -r /etc/apparmor.d/bwrap-agentj', row['hint'])
        self.assertNotIn('userns=0', row['hint'])

    def test_isolation_only_json_never_runs_full_doctor(self):
        import io
        from contextlib import redirect_stdout
        from agentj import doctor
        row={'id':'fence','status':'fail','reason':'no_bwrap','summary':'missing','hint':'install'}
        out=io.StringIO()
        with mock.patch.object(doctor,'run',side_effect=AssertionError('full doctor forbidden')), mock.patch.object(doctor,'isolation_preflight',return_value=row), redirect_stdout(out):
            self.assertEqual(doctor.main(as_json=True,isolation_only=True),1)
        value=json.loads(out.getvalue())
        self.assertFalse(value['ok'])
        self.assertEqual(value['checks'], [row])


class Heartbeat(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_ping_replies_request_id_without_model_calls(self):
        from agentj.serve import Host
        host=SimpleNamespace(send_app=mock.AsyncMock(), agent=SimpleNamespace(refresh_models=mock.AsyncMock()))
        session=SimpleNamespace(state='ready',pub=b'paired',cid=1,p33=True)
        host.st=SimpleNamespace(is_allowed=lambda _:True)
        host.on_p33=lambda s,t,obj: Host.on_p33(host,s,t,obj)
        await Host._app(host,session,{'t':'ping','r':'heartbeat-1'})
        host.send_app.assert_awaited_once_with(session,{'t':'pong','r':'heartbeat-1'})
        host.agent.refresh_models.assert_not_awaited()

    async def test_heartbeat_capability_is_only_announced_to_p33(self):
        from agentj.serve import Host
        host=SimpleNamespace(asr_state=lambda:{'state':'off'},hist=SimpleNamespace(on=False))
        self.assertIn('heartbeat',Host._caps(host,SimpleNamespace(p33=True))['caps'])
        self.assertEqual(Host._caps(host,SimpleNamespace(p33=False)),{})


class ProviderDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_provider_get_replies_even_when_configured_catalogue_empty(self):
        from agentj.serve import Host
        host=SimpleNamespace(send_app=mock.AsyncMock(), st=SimpleNamespace(is_allowed=lambda _:True))
        host.on_p33=lambda s,t,obj: Host.on_p33(host,s,t,obj)
        session=SimpleNamespace(state='ready',pub=b'paired',cid=1,p33=True)
        with mock.patch.object(pr,'configured',return_value={}):
            await Host._app(host,session,{'t':'provider_get','r':'provider-test-1'})
        host.send_app.assert_awaited_once_with(session,{'t':'providers','r':'provider-test-1','providers':[]})


class PreflightCLI(unittest.TestCase):
    def test_cli_isolation_preflight_skips_customer_state_migration(self):
        import io
        from contextlib import redirect_stdout
        from agentj import cli, doctor, migrate
        row={'id':'fence','status':'ok','reason':None,'summary':'works','hint':''}
        out=io.StringIO()
        with mock.patch.object(migrate,'auto',side_effect=AssertionError('customer state must not be touched')), mock.patch.object(doctor,'isolation_preflight',return_value=row), redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['doctor','--isolation-only','--json'])
        self.assertEqual(caught.exception.code,0)
        self.assertTrue(json.loads(out.getvalue())['ok'])


class CatalogueBudget(unittest.TestCase):
    def test_large_native_catalogue_stops_after_budget_preserving_local_priority(self):
        class LimitedModels(dict):
            def items(self):
                for i in range(39):
                    yield 'm-'+str(i), {'name':'Native '+str(i)}
                raise AssertionError('must not enumerate beyond the 40 model budget')
        a=object.__new__(oc.OpenCodeAgent); a.host=SimpleNamespace(); a.models_cache=[]
        with mock.patch.object(pr,'configured',return_value={'local':{'models':{'chosen':{'name':'Chosen'}}}}):
            a._set_models({'providers':[{'id':'native','models':LimitedModels()}]})
        self.assertEqual(len(a.models_cache),40)
        self.assertEqual(a.models_cache[0]['id'],'local/chosen')
        self.assertEqual(len({x['id'] for x in a.models_cache}),40)


class CatalogueBackground(unittest.IsolatedAsyncioTestCase):
    async def test_slow_native_refresh_cannot_block_provider_get_or_heartbeat(self):
        import time
        from agentj.serve import Host
        began=asyncio.Event(); release=asyncio.Event()
        async def slow_refresh():
            began.set()
            await release.wait()
            raise OSError('native metadata is unavailable')
        agent=SimpleNamespace(kind='opencode',_set_models=mock.Mock(),refresh_models=slow_refresh)
        host=SimpleNamespace(agent=agent,send_app=mock.AsyncMock(),_models_msg=lambda:{'t':'models','models':[{'id':'local/chosen'}]},st=SimpleNamespace(is_allowed=lambda _:True))
        host.on_p33=lambda s,t,obj: Host.on_p33(host,s,t,obj)
        session=SimpleNamespace(state='ready',pub=b'paired',cid=1,p33=True)
        try:
            with mock.patch.object(pr,'configured',return_value={}):
                start=time.monotonic()
                await asyncio.wait_for(Host._app(host,session,{'t':'models_get','r':'models-1'}),.2)
                await asyncio.wait_for(began.wait(),.2)
                await asyncio.wait_for(Host._app(host,session,{'t':'provider_get','r':'providers-1'}),.2)
                await asyncio.wait_for(Host._app(host,session,{'t':'ping','r':'ping-1'}),.2)
                self.assertLess(time.monotonic()-start,.6)
            self.assertFalse(host._catalogue_refresh_task.done())
            messages=[call.args[1] for call in host.send_app.await_args_list]
            self.assertEqual([m['t'] for m in messages],['models','providers','pong'])
            self.assertEqual(messages[0]['models'],[{'id':'local/chosen'}])
        finally:
            release.set()
            await host._catalogue_refresh_task


class CatalogueTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_native_timeout_preserves_immediately_usable_local_models(self):
        from agentj import serve
        stopped=asyncio.Event()
        async def stalled():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        agent=SimpleNamespace(kind='opencode',_set_models=mock.Mock(),refresh_models=stalled)
        host=SimpleNamespace(agent=agent,send_app=mock.AsyncMock(),_models_msg=lambda:{'t':'models','models':[{'id':'local/chosen'}]})
        with mock.patch.object(serve,'MODEL_REFRESH_TIMEOUT',.01):
            await serve.Host.on_p33(host,SimpleNamespace(),'models_get',{'r':'models-timeout-1'})
            await asyncio.wait_for(host._catalogue_refresh_task,.3)
        self.assertTrue(stopped.is_set())
        self.assertEqual(host.send_app.call_args.args[1]['models'],[{'id':'local/chosen'}])


class RuntimeDiagnostic(unittest.IsolatedAsyncioTestCase):
    async def test_local_runtime_exposes_only_model_numbers_booleans_no_instructions_or_key(self):
        from agentj.serve import Host
        from agentj import main_identity
        cfg={'language':'zh','model':'relay/m','instructions':'private-fixture-user-text','apiKey':'private-fixture-key'}
        agent=SimpleNamespace(kind='opencode',cfg=cfg,sid='fixture-session',identity_sent={'fixture-session':main_identity.prompt(cfg)},session_model={'providerID':'native','id':'actual','apiKey':'private-fixture-key'},owned_server=True,persist=True,cur_model=lambda:'relay/m')
        host=SimpleNamespace(agent=agent,meter_state={'model':'relay/m','ctx':{'used':12,'max':100,'key':'private-fixture-key'},'quota_windows':[{'window':'weekly','used':1,'limit':5,'pct':20,'key':'private-fixture-key'}]},_ctl_send=mock.AsyncMock())
        reader=asyncio.StreamReader();reader.feed_data(b'{"cmd":"opencode_runtime"}\n');reader.feed_eof()
        await Host.on_ctl(host,reader,SimpleNamespace(close=lambda:None))
        value=host._ctl_send.call_args.args[1]
        self.assertTrue(value['identity_cached_current'])
        self.assertEqual(value['native_session_model'],'native/actual')
        self.assertEqual(value['meter']['ctx'],{'used':12,'max':100})
        self.assertNotIn('private-fixture',json.dumps(value))


class UsageIndependence(unittest.IsolatedAsyncioTestCase):
    async def test_native_context_failure_cannot_hide_successful_subscription(self):
        a=object.__new__(oc.OpenCodeAgent)
        a.cfg={'model':'relay/m'};a.tasks=set()
        a.host=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()))
        a.context_meter=mock.AsyncMock(side_effect=OSError('native unavailable'))
        a.meter=mock.Mock()
        windows=[{'window':'weekly','used':2,'limit':10,'pct':20}]
        with mock.patch.object(pr,'probe',return_value=windows):
            a.refresh_usage()
            await asyncio.gather(*list(a.tasks))
        a.meter.assert_called_once_with(quota_windows=windows)
        a.host.st.log.assert_called_once_with('agent_meter_fail',agent='opencode',reason='OSError')
