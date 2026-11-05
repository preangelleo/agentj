"""#440: owner-selected OpenAI ASR routing, credential boundary and demand-only local fallback."""
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock
from agentj import preferences as p, voice
from _testtmp import TEST_TMP


def config(**changes):
    return p.validate({'voice': {'asr': {'mode': 'custom', 'base_url': 'http://127.0.0.1:12345/v1',
                                        'model': 'Qwen/Qwen3-ASR-1.7B', **changes}}})


class CustomASR(unittest.TestCase):
    def test_config_and_endpoint(self):
        cfg=config()
        self.assertEqual(p.get(cfg,'voice.asr.key_env'),'')
        for url in ('http://127.0.0.1:12345','http://127.0.0.1:12345/v1/',
                    'http://127.0.0.1:12345/v1/audio/transcriptions'):
            self.assertEqual(voice.asr_endpoint(url),'http://127.0.0.1:12345/v1/audio/transcriptions')
        for url in ('', 'file:///tmp/a', 'https://user:secret@example.com', 'https://example.com?key=hidden',
                    'https://example.com/#token','https://example.com/\r\nheader:secret','https://example.com:bad'):
            with self.subTest(url=url),self.assertRaises(p.ConfigError) as cm: config(base_url=url)
            self.assertNotIn('hidden',str(cm.exception));self.assertNotIn('user:secret',str(cm.exception))
        with self.assertRaises(p.ConfigError): config(hotwords=[{'wrong':'type'}])
        with self.assertRaises(p.ConfigError): config(corrections={'x':['wrong']})

    def test_prompt_keyless_and_exact_corrections(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as d:
            path=Path(d)/'voice.wav';path.write_bytes(b'RIFF-controlled')
            cfg=config(corrections={'Olaris':'Olares'})
            with patch('agentj.voice._request',return_value=b'{"text":"Olaris Agent J"}') as request:
                result=voice.cloud_asr(path,cfg)
                self.assertEqual(result,{'ok':True,'text':'Olares Agent J','engine':'custom'})
                url,key,body=request.call_args.args[:3]
                self.assertEqual(key,'');self.assertTrue(url.endswith('/v1/audio/transcriptions'))
                self.assertIn(b'name="prompt"',body);self.assertIn(b'Claude Code',body)
                self.assertIn(b'Qwen/Qwen3-ASR-1.7B',body)

    def test_remote_success_never_loads_local(self):
        local=Mock()
        with patch('agentj.voice.cloud_asr',return_value={'ok':True,'text':'你好','engine':'custom'}):
            self.assertEqual(voice.transcribe('/sample',config(),local_asr=local)['engine'],'custom')
        local.transcribe.assert_not_called();local.shutdown.assert_called_once()

    def test_fallback_has_budget_notice_and_releases_worker(self):
        local=Mock();local.transcribe.return_value={'ok':True,'text':'本地识别','engine':'sherpa'}
        with patch('agentj.voice.cloud_asr',return_value={'ok':False,'reason':'engine_failed'}) as remote:
            result=voice.transcribe('/sample',config(engine='voxtype'),60,state_dir='/isolated',local_asr=local)
        self.assertEqual(result['notice'],'local_fallback');self.assertEqual(result['text'],'本地识别')
        self.assertEqual(remote.call_args.args[2],15)
        self.assertGreater(local.transcribe.call_args.kwargs['timeout_s'],40)
        self.assertEqual(local.transcribe.call_args.kwargs['engine_override'],'sensevoice')
        self.assertEqual(local.shutdown.call_count,2)

    def test_failure_not_silent_and_no_exception_detail(self):
        local=Mock();local.transcribe.side_effect=ValueError('PRIVATE_SENTINEL')
        with patch('agentj.voice.cloud_asr',return_value={'ok':False,'reason':'engine_failed'}):
            result=voice.transcribe('/sample',config(),local_asr=local)
        self.assertEqual(result,{'ok':False,'reason':'engine_failed','notice':'remote_unavailable'})
        self.assertNotIn('PRIVATE_SENTINEL',str(result));self.assertEqual(local.shutdown.call_count,2)

    def test_named_key_missing_never_contacts_endpoint(self):
        with patch.dict(os.environ,{},clear=True),patch('agentj.voice._request') as request:
            result=voice.cloud_asr('/missing',config(key_env='AJ_TEST_ASR_KEY'))
        self.assertFalse(result['ok']);request.assert_not_called()

    def test_serve_response_keeps_notice(self):
        from agentj.serve import Host, PREF_SET_KEYS
        result=Host._asr_result({'ok':True,'text':'你好','notice':'local_fallback'},2)
        self.assertEqual(result['notice'],'local_fallback')
        failed=Host._asr_result({'ok':False,'reason':'engine_failed','notice':'remote_unavailable'},2)
        self.assertEqual(failed['notice'],'remote_unavailable')
        from agentj.serve import page_asr
        self.assertEqual(page_asr([{'asr':failed}])[0]['notice'],'remote_unavailable')
        self.assertIn('voice.asr.base_url',PREF_SET_KEYS)
        self.assertIn('voice.asr.hotwords',PREF_SET_KEYS)

    def test_no_auth_header_for_keyless_and_redirect_refused(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        opener=Mock();opener.open.return_value=Response(b'{}')
        with patch('urllib.request.build_opener',return_value=opener) as build:
            voice._request('http://127.0.0.1/v1/audio/transcriptions','',b'body','application/test',3,100)
        req=opener.open.call_args.args[0]
        self.assertFalse(req.has_header('Authorization'))
        self.assertIsNone(build.call_args.args[0]().redirect_request(None,None,None,None,None,None))


class PhoneASRSecurity(unittest.IsolatedAsyncioTestCase):
    """No real keys or network: remote preferences cannot turn host secrets into Authorization headers."""
    def host(self,cfg):
        from agentj.serve import Host
        from unittest.mock import AsyncMock
        host=Host.__new__(Host)
        host.preferences=cfg;host.config_apply_lock=asyncio.Lock()
        host._send_ready=AsyncMock();host.send_app=AsyncMock();host.set_pref=AsyncMock()
        host.st=SimpleNamespace(log=lambda *a,**kw:None,is_allowed=lambda pub:True)
        phone=SimpleNamespace(cid='phone',device='paired-fixture',state='ready',pub='fixture-pub')
        host.sessions={'phone':phone}
        return host,phone

    async def test_phone_cannot_select_any_environment_secret_even_with_same_name(self):
        from agentj.serve import Host
        for mode in ('custom','cloud'):
            cfg=config(key_env='PRIVATE_INTERNAL_TOKEN')
            cfg['voice']['asr']['mode']=mode
            host,phone=self.host(cfg)
            with patch.dict(os.environ,{'PRIVATE_INTERNAL_TOKEN':'fake-private-token-only'},clear=True),patch('agentj.voice._request') as request:
                await Host.on_pref_set(host,phone,{'r':'0123456789abcdef','key':'voice.asr.key_env','value':'PRIVATE_INTERNAL_TOKEN'})
            host.set_pref.assert_not_called();request.assert_not_called()
            self.assertFalse(host.send_app.call_args.args[1]['ok'])

    async def test_keyed_route_changes_refused_before_secret_loading_or_persistence(self):
        from agentj.serve import Host
        old=config(key_env='PRIVATE_INTERNAL_TOKEN')
        for key,value in [('base_url','https://attacker.invalid'),('model','different-model'),('provider','openai'),('mode','cloud')]:
            candidate=json.loads(json.dumps(old));candidate['voice']['asr'][key]=value
            host,_=self.host(old)
            with patch('agentj.preferences.read',return_value=('',candidate)),patch('agentj.service.load_launch_binary_env') as load,patch('agentj.preferences.save_good') as save,patch('agentj.voice._request') as request:
                result=await Host.apply_preferences(host,asr_phone=True)
            self.assertFalse(result['ok'],key);load.assert_not_called();save.assert_not_called();request.assert_not_called()

    async def test_check_occurs_after_config_lock_not_stale_phone_snapshot(self):
        from agentj.serve import Host
        anonymous=config()
        candidate=config(base_url='https://attacker.invalid')
        host,_=self.host(anonymous)
        await host.config_apply_lock.acquire()
        with patch('agentj.preferences.read',return_value=('',candidate)),patch('agentj.service.load_launch_binary_env') as load:
            pending=asyncio.create_task(Host.apply_preferences(host,asr_phone=True))
            await asyncio.sleep(0)  # Real task is blocked on the existing apply lock.
            host.preferences=config(key_env='PRIVATE_INTERNAL_TOKEN')
            host.config_apply_lock.release()
            result=await pending
        self.assertFalse(result['ok']);load.assert_not_called()

    async def test_unsigned_ordering_cannot_restore_key_after_anonymous_rebinding(self):
        old=config(key_env='PRIVATE_INTERNAL_TOKEN')
        cleared=config()
        rebound=config(base_url='https://attacker.invalid')
        restored=config(base_url='https://attacker.invalid',key_env='PRIVATE_INTERNAL_TOKEN')
        self.assertTrue(voice.phone_asr_change_allowed(old,cleared))
        self.assertTrue(voice.phone_asr_change_allowed(cleared,rebound))
        self.assertFalse(voice.phone_asr_change_allowed(rebound,restored))
        combined=config(base_url='https://attacker.invalid')
        self.assertFalse(voice.phone_asr_change_allowed(old,combined))
        local=json.loads(json.dumps(old));local['voice']['asr']['mode']='local'
        self.assertTrue(voice.phone_asr_change_allowed(old,local))
        local['voice']['asr']['base_url']='https://attacker.invalid'
        self.assertFalse(voice.phone_asr_change_allowed(old,local))
        self.assertFalse(voice.phone_asr_change_allowed(p.defaults(),config(key_env='PRIVATE_INTERNAL_TOKEN')))

    async def test_anonymous_endpoint_handler_marks_locked_validation(self):
        from agentj.serve import Host
        host,phone=self.host(config())
        await Host.on_pref_set(host,phone,{'r':'0123456789abcdef','key':'voice.asr.base_url','value':'http://127.0.0.1:7890/v1'})
        host.set_pref.assert_awaited_once()
        self.assertTrue(host.set_pref.call_args.kwargs['asr_phone'])
        self.assertTrue(voice.phone_asr_change_allowed(config(),config(model='owner-selected',base_url='http://127.0.0.1:7890/v1')))
