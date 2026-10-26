"""P531: contracts measured with isolated OpenCode 1.19.4 and 2.1.4."""
import _hermetic  # noqa: F401
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from agentj import fallback, opencode_free, agent_opencode2, peer_session

DS = 'opencode/deepseek-v4.1-flash-free'
GLM = 'opencode/glm-5.3-flash-free'

class Compatibility(unittest.TestCase):
    def test_prefixed_v2_dispatches_native_api(self):
        with patch.object(fallback.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='opencode v2.1.4\n')), \
             patch.object(opencode_free, 'probe', return_value='v1') as old, \
             patch.object(agent_opencode2, 'probe', return_value='v2') as new:
            self.assertEqual(fallback.cloud_reply('fixture', DS, 'fixed error'), 'v2')
            old.assert_not_called()
            self.assertEqual(new.call_args.kwargs['diagnostic'], True)

    def test_zero_retention_current_ids_and_removed_models(self):
        self.assertEqual(set(fallback.ZERO_MODELS), {GLM})
        self.assertEqual(opencode_free.pick([GLM, DS]), DS)
        self.assertEqual(opencode_free.pick(['opencode/big-pickle', GLM]), GLM)
        self.assertIsNone(opencode_free.pick(['opencode/longcat-2.5-preview-free', 'opencode/space-bunny-free']))

    def test_peer_v2_missing_dir_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as root, patch.object(peer_session, 'opencode_flags', return_value={'--agent', '--format', '--title', '--session'}):
            a = object.__new__(peer_session.PeerSessions)
            self.assertIsNone(a._opencode_argv('fixture', None, {}, root))

class ConfigDocuments(unittest.IsolatedAsyncioTestCase):
    async def test_v21_config_documents_keep_custom_provider_models_selectable(self):
        from unittest.mock import AsyncMock
        a = object.__new__(agent_opencode2.OpenCodeV2Agent)
        a.client = SimpleNamespace(request=AsyncMock(side_effect=[
            (200, {'data': [{'id':'p531','connections':[]}]}),
            (200, {'data':[{'type':'document','info':{'providers':{'p531':{'settings':{'apiKey':'synthetic-only'}}}}}]})]))
        self.assertEqual(await a._connected(), ['p531'])
        self.assertNotIn('synthetic-only', repr(getattr(a, 'auxiliary_config', {})))

class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_clear_meter_does_not_request_a_null_session(self):
        from unittest.mock import AsyncMock
        from agentj import agent_opencode
        for cls in (agent_opencode.OpenCodeAgent, agent_opencode2.OpenCodeV2Agent):
            a = object.__new__(cls)
            a.sid = None
            a.client = SimpleNamespace(request=AsyncMock())
            self.assertEqual(await a._last_assistant(), {})
            a.client.request.assert_not_called()

    async def test_failed_v21_compaction_is_not_reported_as_success(self):
        from unittest.mock import AsyncMock, Mock
        with tempfile.TemporaryDirectory() as root:
            for failed in (False, True):
                host = SimpleNamespace(st=SimpleNamespace(agent_session=lambda kind: 'ses_p531'), agent_status=Mock())
                a = agent_opencode2.OpenCodeV2Agent(host, {'dir': root, 'language': 'en'})
                a.sid = 'ses_p531'
                a._ready = AsyncMock(return_value=True)
                a._last_assistant = AsyncMock(return_value={})
                a._wait_turn = AsyncMock()
                async def request(*args, **kwargs):
                    if failed:
                        a.on_event({'type':'session.compaction.failed', 'data':{'sessionID':a.sid}})
                    return 200, {}
                a.client = SimpleNamespace(request=request)
                result = await a.cmd_compact('')
                self.assertEqual(result.kind, 'error' if failed else 'ok')
                if failed:
                    self.assertIn('did not complete', result.text)

class Catalogue(unittest.TestCase):
    def test_v21_all_price_bands_must_be_zero_not_just_a_free_suffix(self):
        from agentj import opencode_catalogue as cat
        def row(mid, bands, **kw):
            return {'id':mid, 'providerID':'opencode', 'cost':bands, **kw}
        zero = {'input':0, 'output':0}
        rows = [row('deepseek-v4.1-flash-free',[zero]), row('glm-5.3-flash-free',[zero]),
                row('paid-free',[zero, {'input':1,'output':0}]), row('unknown-free',[]),
                row('bad-free',[{'input':False,'output':False}]), row('retired-free',[zero],status='deprecated')]
        self.assertEqual(cat.free_models(rows), [DS, GLM])
        self.assertIsNone(cat.free_models([]))
        self.assertIsNone(cat.free_models([row('unknown-free',[])]))

    def test_retired_verbose_dispatches_native_catalogue(self):
        from agentj import opencode_catalogue as cat
        result = SimpleNamespace(returncode=1, stdout='', stderr='Unrecognized flag: --verbose in command opencode models')
        opencode_free._cache.pop('p531-fixture', None)
        self.addCleanup(opencode_free._cache.pop, 'p531-fixture', None)
        with patch.dict('os.environ', {'AGENTJ_OC_FREE_LIST':'on'}), \
             patch.object(opencode_free.subprocess,'run',return_value=result), \
             patch.object(cat,'native_models',return_value=[DS, GLM]) as native:
            self.assertEqual(opencode_free.listed('p531-fixture'), [DS, GLM])
            self.assertEqual(fallback.free_models('p531-fixture'), [DS, GLM])
            self.assertEqual(native.call_count, 2)

    def test_native_catalogue_never_carries_owner_keys_or_config(self):
        import io, json, os
        from pathlib import Path
        from agentj import opencode_catalogue as cat
        captured = {}
        def serve(exe, env, home, port, deadline):
            captured.update(env=env, home=home)
            return object()
        raw = json.dumps({'data':[{'id':'glm-5.3-flash-free','providerID':'opencode','cost':[{'input':0,'output':0}]}]}).encode()
        opener = SimpleNamespace(open=lambda *a,**kw:io.BytesIO(raw))
        with patch.dict(os.environ, {'P531_CANARY_API_KEY':'owner-canary', 'OPENCODE_CONFIG_CONTENT':'owner-private-config'}), \
             patch.object(opencode_free,'_port',return_value=12345), \
             patch.object(opencode_free,'serve',side_effect=serve), patch.object(opencode_free,'_stop') as stop, \
             patch.object(cat.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(cat.native_models('fixture'), [GLM])
        self.assertNotIn('P531_CANARY_API_KEY', captured['env'])
        self.assertNotIn('owner-private-config', repr(captured['env']))
        self.assertFalse(Path(captured['home']).exists())
        stop.assert_called_once()

    def test_v2_detect_exposes_free_ids_without_claiming_paid_login(self):
        from agentj import harness
        with patch('agentj.binaries.resolve', return_value={'path':'/tmp/p531-fixture','reason':'test'}), \
             patch('agentj.binaries.installations', return_value=[]), \
             patch.object(harness,'version_of',return_value='opencode v2.1.4'), \
             patch.object(harness,'opencode_model',return_value=None), \
             patch.object(opencode_free,'listed',return_value=[DS,GLM]):
            row=harness._one('opencode')
        self.assertEqual(row['free_models'],[DS,GLM])
        self.assertEqual(row['free_model'],DS)
        self.assertIsNone(row['logged_in'])

class AnonymousMenu(unittest.IsolatedAsyncioTestCase):
    async def test_free_models_without_connections_are_shown_but_paid_models_are_not(self):
        from unittest.mock import AsyncMock
        zero={'input':0,'output':0}
        rows=[{'providerID':'opencode','id':'deepseek-v4.1-flash-free','cost':[zero]},
              {'providerID':'opencode','id':'glm-5.3-flash-free','cost':[zero]},
              {'providerID':'opencode','id':'paid','cost':[{'input':1,'output':2}]},
              {'providerID':'opencode','id':'unknown-free','cost':[]},
              {'providerID':'other','id':'free','cost':[zero]}]
        a=object.__new__(agent_opencode2.OpenCodeV2Agent)
        a.client=SimpleNamespace(request=AsyncMock(return_value=(200,{'data':rows})))
        a._connected=AsyncMock(return_value=[])
        providers=await a._providers()
        self.assertEqual(set(providers['providers'][0]['models']),{'deepseek-v4.1-flash-free','glm-5.3-flash-free'})
        self.assertEqual(providers['connected'],[])
        a.cfg={'model':DS}
        a.subagent_state=lambda: {}
        self.assertTrue((await a.authentication())['selected_connected'])
        a.cfg['model']='opencode/paid'
        self.assertFalse((await a.authentication())['selected_connected'])
        with patch('agentj.provider_runtime.configured',return_value={}):
            a.host=SimpleNamespace()
            a._set_models(providers)
        self.assertEqual({m['id'] for m in a.usable_models},{DS,GLM})

class AvatarCatalogue(unittest.TestCase):
    def test_v21_discovery_does_not_reopen_unqualified_free_avatar(self):
        from agentj import channel_free, opencode_catalogue
        channel_free._CATALOGUE.pop('p531-fixture', None)
        self.addCleanup(channel_free._CATALOGUE.pop,'p531-fixture',None)
        result=SimpleNamespace(returncode=1,stdout='',stderr='Unrecognized flag: --refresh in command opencode models')
        with patch.object(channel_free.subprocess,'run',return_value=result), \
             patch.object(opencode_catalogue,'native_models',return_value=[DS,GLM]):
            models, raw=channel_free.catalogue('p531-fixture')
            self.assertEqual(models,[DS,GLM])
            self.assertIsNone(raw)
            self.assertFalse(channel_free.option('p531-fixture')['selectable'])
