"""P124 support regression: real failure classification and private host.log metadata."""
import _hermetic
import asyncio, io, json, os, tempfile, unittest, urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from agentj import preferences as p, voice, serve
from agentj.state import State
from websockets.exceptions import InvalidStatus

class SpeechFailures(unittest.TestCase):
    def cfg(self, provider='openai', model='gpt-4o-mini-tts'):
        return p.validate({'voice':{'tts':{'mode':'cloud','provider':provider,'model':model,
            'voice':'ZhCloneVoice01' if provider=='elevenlabs' else 'alloy','key_env':'P124_FAKE_SPEECH_KEY'}}})
    def test_missing_key_never_calls_provider(self):
        with patch.dict(os.environ,{},clear=True), patch('agentj.voice._request') as request:
            with self.assertRaises(voice.SpeechError) as caught:voice.synthesize('hello',self.cfg())
            self.assertEqual(caught.exception.why,'missing_key');request.assert_not_called()
    def test_http_and_network_all_providers_no_body_or_key_retained(self):
        for provider,model in [('openai','gpt-4o-mini-tts'),('elevenlabs','eleven_v4'),('openai','gpt-realtime-2.1-mini')]:
            for status,why in [(400,'provider_rejected'),(401,'provider_rejected'),(402,'provider_rejected'),(429,'provider_busy'),(500,'provider_unavailable'),(503,'provider_unavailable'),(None,'network')]:
                with self.subTest(provider=provider,model=model,status=status):
                    secret='P124_PRIVATE_SENTINEL'
                    exc=urllib.error.HTTPError('https://invalid/'+secret,status,secret,{},io.BytesIO(secret.encode())) if status else urllib.error.URLError(secret)
                    async def fail(*args):raise exc
                    target='agentj.voice._realtime_audio' if model.startswith('gpt-realtime') else 'agentj.voice._request'
                    with patch.dict(os.environ,{'P124_FAKE_SPEECH_KEY':secret}),patch(target,side_effect=fail if model.startswith('gpt-realtime') else exc):
                        with self.assertRaises(voice.SpeechError) as caught:voice.synthesize('private reply',self.cfg(provider,model))
                    error=caught.exception;self.assertEqual(error.why,why);self.assertEqual(error.http_status,status)
                    self.assertNotIn(secret,str(error));self.assertNotIn(secret,repr(vars(error)))
    def test_realtime_handshake_status(self):
        for status,why in [(401,'provider_rejected'),(429,'provider_busy'),(503,'provider_unavailable')]:
            error=voice.speech_error(InvalidStatus(SimpleNamespace(status_code=status)))
            self.assertEqual((error.why,error.http_status),(why,status))

class HostFailures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=State(Path(self.tmp.name)/'state');self.st.init(relay='ws://127.0.0.1:1')
        self.st.is_allowed=lambda pub:True
        self.s=SimpleNamespace(cid='session',state='ready',pub=b'pub')
        self.host=serve.Host.__new__(serve.Host);self.host.st=self.st;self.host.sessions={'session':self.s}
        self.host.hist=SimpleNamespace(get=lambda id:{'end':'done','reply':{'text':'PRIVATE_REPLY_SENTINEL'}})
        self.host.preferences={};self.host.send_app=AsyncMock(return_value=True)
        self.obj={'r':'A'*22,'id':1}
    def last_log(self):return json.loads(self.st.log_path.read_text().splitlines()[-1])
    async def test_sanitized_end_and_log_reset_busy(self):
        for status,why in [(401,'provider_rejected'),(402,'provider_rejected'),(429,'provider_busy'),(503,'provider_unavailable'),(None,'network')]:
            exc=urllib.error.HTTPError('https://invalid/PRIVATE_KEY_SENTINEL',status,'PRIVATE_KEY_SENTINEL',{},None) if status else urllib.error.URLError('PRIVATE_KEY_SENTINEL')
            with patch('agentj.voice.synthesize',side_effect=exc):await self.host.on_tts(self.s,self.obj)
            self.assertEqual(self.host.send_app.call_args.args[1]['why'],why)
            log=self.last_log();self.assertEqual((log['ev'],log['why'],log['http_status']),('tts_failed',why,status))
            self.assertEqual(log['exception_type'],type(exc).__name__);self.assertFalse(self.host.tts_busy)
            self.assertNotIn('PRIVATE_',self.st.log_path.read_text());self.assertNotIn('PRIVATE_',str(self.host.send_app.call_args))
    async def test_busy_missing_invalid_and_detached(self):
        self.host.tts_busy=True;await self.host.on_tts(self.s,self.obj);self.assertEqual(self.last_log()['why'],'busy')
        self.host.tts_busy=False;self.host.hist.get=lambda id:None
        await self.host.on_tts(self.s,self.obj);self.assertEqual(self.last_log()['why'],'not_ready')
        await self.host.on_tts(self.s,{'r':'bad'});self.assertEqual(self.last_log()['why'],'invalid_request')
        self.host.sessions.clear();await self.host.on_tts(self.s,self.obj);self.assertEqual(self.last_log()['why'],'not_ready')
    async def test_missing_key_and_send_failure(self):
        with patch('agentj.voice.synthesize',side_effect=voice.SpeechError('missing_key')):await self.host.on_tts(self.s,self.obj)
        self.assertEqual(self.last_log()['why'],'missing_key')
        self.host.send_app=AsyncMock(return_value=False)
        with patch('agentj.voice.synthesize',return_value=b'RIFFaudio'):await self.host.on_tts(self.s,self.obj)
        self.assertEqual(self.last_log()['why'],'network');self.assertFalse(self.host.tts_busy)
    async def test_success_chunk_end(self):
        with patch('agentj.voice.synthesize',return_value=b'RIFFaudio'):await self.host.on_tts(self.s,self.obj)
        messages=[c.args[1] for c in self.host.send_app.call_args_list]
        self.assertEqual([m['t'] for m in messages],['tts_chunk','tts_end']);self.assertTrue(messages[-1]['ok'])

    async def test_retry_cache_and_changed_reply_invalidation(self):
        with patch('agentj.voice.synthesize',return_value=b'RIFFaudio') as synth:
            await self.host.on_tts(self.s,self.obj)
            await self.host.on_tts(self.s,self.obj)
            self.assertEqual(synth.call_count,1)
            self.host.hist.get=lambda id:{'end':'done','reply':{'text':'different'}}
            await self.host.on_tts(self.s,self.obj)
            self.assertEqual(synth.call_count,2)


class PhoneVoicePreferences(unittest.TestCase):
    def test_mode_changes_keep_cloud_and_phone_voice_separate(self):
        from agentj import preferences as p
        doc={'voice':{'tts':{'mode':'cloud','voice':'cloud-id','phone_voice':'Local phone','model':'tts-test'}}}
        self.assertEqual(p.get(p.validate(doc),'voice.tts.voice'),'cloud-id')
        doc['voice']['tts']['mode']='phone'
        actual=p.validate(doc)
        self.assertEqual(p.get(actual,'voice.tts.phone_voice'),'Local phone')
        self.assertEqual(p.get(actual,'voice.tts.voice'),'cloud-id')
        self.assertEqual(p.get(p.defaults(),'voice.tts.phone_voice'),'')
