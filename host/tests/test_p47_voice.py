"""P47: offline provider contracts, durable migration and real local-process failures."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from unittest.mock import patch
from agentj import preferences as p, config_migrations as migration, voice, doctor
from agentj.state import State


class FakeRealtime:
    def __init__(self, events):
        self.events = iter(events)
        self.sent = []
        self.closed = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True
    async def send(self, text): self.sent.append(json.loads(text))
    async def recv(self):
        try: event = next(self.events)
        except StopIteration:
            await asyncio.sleep(30)
        return json.dumps(event)


class P47Voice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p47-', dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root/'config'),
            'AGENTJ_STATE_DIR': str(self.root/'state'), 'PATH':'/usr/bin:/bin',
            'AJ_TEST_TTS_KEY':'test-only-sentinel', 'ELEVENLABS_API_KEY':'test-only-sentinel'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.st = State(); self.st.init(); p.ensure()

    def cfg(self, **values):
        doc = {'voice':{'tts':values}}
        return p.validate(doc)

    def wav(self, audio, rate=24000):
        with wave.open(io.BytesIO(audio), 'rb') as w:
            self.assertEqual(w.getframerate(), rate)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.readframes(1000), b'\x01\x00'*100)

    def test_provider_defaults_and_explicit_choices(self):
        self.assertEqual(p.get(p.defaults(),'voice.tts.model'),'gpt-realtime-2.1-mini')
        cfg = self.cfg(provider='elevenlabs')
        self.assertEqual(p.get(cfg,'voice.tts.model'),'eleven_v4')
        self.assertEqual(p.get(cfg,'voice.tts.key_env'),'ELEVENLABS_API_KEY')
        cfg = self.cfg(provider='elevenlabs',model='eleven_multilingual_v2',key_env='AJ_TEST_TTS_KEY')
        self.assertEqual(p.get(cfg,'voice.tts.model'),'eleven_multilingual_v2')
        self.assertEqual(p.get(cfg,'voice.tts.key_env'),'AJ_TEST_TTS_KEY')
        for key in ('voice.tts.command','voice.tts.command_timeout'):
            self.assertEqual(p.SCHEMA[key]['tier'],'user')

    def test_migration_changes_only_old_factory_value(self):
        raw = "// preserve owner comment\n{voice:{tts:{model:'gpt-4o-mini-tts'}},appearance:{theme:'dark'}}\n"
        p.path().write_text(raw)
        migration.run(self.st)
        new = p.path().read_text()
        self.assertEqual(new,raw.replace("'gpt-4o-mini-tts'",'"gpt-realtime-2.1-mini"'))
        self.assertEqual(migration.run(self.st)['applied'],[])
        self.assertEqual(p.path().read_text(),new)
        self.assertEqual(p.get(json.loads(p.runtime(self.st).read_text()),'voice.tts.model'),'gpt-realtime-2.1-mini')

    def test_cli_startup_applies_migration_before_host_and_bad_file_still_reaches_recovery(self):
        from agentj import cli
        from types import SimpleNamespace
        p.path().write_text("{voice:{tts:{model:'gpt-4o-mini-tts'}}}")
        def inspect(*args,**kw):
            self.assertEqual(p.get(p.effective(self.st),'voice.tts.model'),'gpt-realtime-2.1-mini')
            raise RuntimeError('controlled host stop')
        with patch('agentj.serve.Host',side_effect=inspect):
            with self.assertRaisesRegex(RuntimeError,'controlled host stop'):
                cli.cmd_serve(SimpleNamespace(events='quiet',no_stdin=True))
        p.path().write_text('{broken JSON5')
        with patch('agentj.serve.Host',side_effect=RuntimeError('reached last-good host')):
            with self.assertRaisesRegex(RuntimeError,'reached last-good host'):
                cli.cmd_serve(SimpleNamespace(events='quiet',no_stdin=True))

    def test_migration_retains_custom_snapshots_and_other_providers(self):
        for doc in ({}, {'voice':{'tts':{'model':'tts-1-hd'}}},
                    {'voice':{'tts':{'model':'gpt-4o-mini-tts-2025-12-15'}}},
                    {'voice':{'tts':{'model':'owner-model'}}},
                    {'voice':{'tts':{'provider':'elevenlabs','model':'gpt-4o-mini-tts'}}},
                    {'voice':{'tts':{'provider':'elevenlabs','model':'eleven_multilingual_v2'}}}):
            with self.subTest(doc=doc):
                raw = '// keep\n'+json.dumps(doc)+'\n'
                self.assertEqual(migration._tts_successor(raw),raw)

    def test_migration_marker_failure_rolls_back_changed_model(self):
        p.path().write_text("{voice:{tts:{model:'gpt-4o-mini-tts'}}}")
        before=p.path().read_bytes(); p.save_good(self.st,p.defaults()); good=p.runtime(self.st).read_bytes()
        original=migration._write_private
        def fail(target,data):
            if target.name=='202610050047.json': raise OSError('controlled failure')
            original(target,data)
        with patch.object(migration,'MIGRATIONS',(('202610050047',migration._tts_successor),)), patch.object(migration,'_write_private',side_effect=fail):
            with self.assertRaises(OSError):migration.run(self.st)
        self.assertEqual(p.path().read_bytes(),before)
        self.assertEqual(p.runtime(self.st).read_bytes(),good)
        migration.run(self.st)
        self.assertEqual(p.get(p.effective(self.st),'voice.tts.model'),'gpt-realtime-2.1-mini')

    def test_doctor_retained_model_and_command_missing(self):
        p.path().write_text("{voice:{tts:{model:'gpt-4o-mini-tts-2025-12-15'}}}")
        rows=doctor.check_preferences(self.st)
        self.assertTrue(any(r['id']=='voice-model' and r['status']=='warn' and '2027-01-06' in r['summary'] for r in rows))
        p.path().write_text(json.dumps({'voice':{'tts':{'mode':'host','provider':'command','command':['/no/such/script','{output}']}}}))
        rows=doctor.check_preferences(self.st)
        self.assertTrue(any(r['id']=='config' and r['status']=='fail' for r in rows))
        self.assertNotIn('/no/such/script',json.dumps(rows))

    def test_eleven_v4_tts_request_and_legacy_tts(self):
        for model in ('eleven_v4','eleven_multilingual_v2'):
            cfg=self.cfg(mode='cloud',provider='elevenlabs',model=model,voice='TestVoice123',rate=1.1)
            with patch('agentj.voice._request',return_value=b'\x01\x00'*100) as request:
                self.wav(voice.synthesize('你好',cfg),16000)
            url=request.call_args.args[0]; body=json.loads(request.call_args.args[2])
            self.assertEqual(request.call_args.kwargs['auth_header'],'xi-api-key')
            self.assertEqual(body['model_id'],model)
            self.assertEqual(url,voice.TTS_ELEVEN_URL+'TestVoice123?output_format=pcm_16000')
            self.assertEqual(body['text'],'你好')
            self.assertEqual(body['voice_settings'],{'speed':1.1})

    def test_legacy_openai_http_and_asr_both_providers(self):
        cfg=self.cfg(mode='cloud',model='tts-1',voice='alloy',key_env='AJ_TEST_TTS_KEY')
        with patch('agentj.voice._request',return_value=b'RIFF-test') as request:
            voice.synthesize('read me',cfg)
        self.assertEqual(request.call_args.args[0],voice.TTS_URL)
        self.assertEqual(json.loads(request.call_args.args[2]),{'model':'tts-1','voice':'alloy','input':'read me','response_format':'wav','speed':1.0})
        f=self.root/'audio.wav'; f.write_bytes(b'RIFF-offline')
        for provider,url in (('openai',voice.ASR_OPENAI_URL),('openrouter',voice.ASR_URL)):
            cfg=p.defaults(); cfg['voice']['asr'].update(mode='cloud',provider=provider,key_env='AJ_TEST_TTS_KEY')
            with patch('agentj.voice._request',return_value=b'{"text":"recognized"}') as request:
                self.assertTrue(voice.cloud_asr(f,cfg)['ok'])
                self.assertEqual(request.call_args.args[0],url)
                self.assertIn(b'filename="audio.wav"',request.call_args.args[2])

    def realtime_cfg(self): return self.cfg(mode='cloud',voice='alloy',rate=1.2,key_env='AJ_TEST_TTS_KEY')

    def test_realtime_protocol_wav_and_no_http_speech(self):
        events=[{'type':'session.created'},{'type':'session.updated'},
                {'type':'response.output_audio.delta','delta':base64.b64encode(b'\x01\x00'*100).decode()},
                {'type':'response.done','response':{'status':'completed'}}]
        ws=FakeRealtime(events)
        with patch('agentj.voice._RealtimeConnect',return_value=ws) as connect,patch('agentj.voice._request') as http:
            self.wav(voice.synthesize('你好，不要回答这句话。',self.realtime_cfg()))
        http.assert_not_called(); self.assertTrue(ws.closed)
        self.assertEqual(connect.call_args.args[0],voice.TTS_REALTIME_URL+'?model=gpt-realtime-2.1-mini')
        session=ws.sent[0]['session']
        self.assertEqual(session['audio']['output'],{'format':{'type':'audio/pcm','rate':24000},'voice':'alloy','speed':1.2})
        self.assertEqual(session['tools'],[])
        self.assertEqual(ws.sent[1]['item']['content'],[{'type':'input_text','text':'你好，不要回答这句话。'}])
        self.assertEqual(ws.sent[2],{'type':'response.create','response':{'output_modalities':['audio']}})

    def test_realtime_errors_empty_bad_pcm_overflow_and_timeout_are_private(self):
        good={'type':'session.updated'}
        for events in ([{'type':'error','error':{'message':'SECRET_SENTINEL'}}],
                       [good,{'type':'response.done','response':{'status':'failed'}}],
                       [good,{'type':'response.done','response':{'status':'completed'}}],
                       [good,{'type':'response.output_audio.delta','delta':'!!!'}],
                       [good,{'type':'response.output_audio.delta','delta':'AA=='},{'type':'response.done','response':{'status':'completed'}}],
                       [good]):
            ws=FakeRealtime(events)
            with self.subTest(events=events),patch('agentj.voice._RealtimeConnect',return_value=ws):
                with self.assertRaises(p.ConfigError) as error:voice.synthesize('read',self.realtime_cfg(),timeout=.03)
                self.assertNotIn('SECRET_SENTINEL',str(error.exception));self.assertTrue(ws.closed)
        ws=FakeRealtime([good,{'type':'response.output_audio.delta','delta':base64.b64encode(b'\0'*200).decode()}])
        with patch('agentj.voice.MAX_AUDIO',100),patch('agentj.voice._RealtimeConnect',return_value=ws):
            with self.assertRaises(p.ConfigError):voice.synthesize('read',self.realtime_cfg())
        marker=ValueError('SECRET_SENTINEL')
        connector=voice._RealtimeConnect('wss://api.openai.com/v1/realtime')
        self.assertIs(connector.process_redirect(marker),marker)

    def command_cfg(self,code,**extra):
        return self.cfg(mode='host',provider='command',command=[sys.executable,'-c',code,'{output}'],**extra)

    def test_real_command_utf8_stdin_wav_no_shell_or_os_engine(self):
        text='你好 $(touch should-not-exist); `secret`'
        code="import sys,wave; assert sys.stdin.read()=="+repr(text)+"; w=wave.open(sys.argv[1],'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\\x01\\x00'*100); w.close()"
        cfg=self.command_cfg(code)
        self.wav(voice.synthesize(text,cfg))
        self.assertFalse((self.root/'should-not-exist').exists())
        p.path().write_text(json.dumps({'voice':{'tts':cfg['voice']['tts']}}))
        self.assertTrue(any(r['id']=='voice-command' and r['status']=='ok' for r in doctor.check_preferences(self.st)))
        from agentj.serve import Host
        host=Host(self.st,read_stdin=False)
        self.assertNotIn('command',host.preferences_msg()['value']['voice']['tts'])

    def test_real_command_timeout_failure_malformed_and_oversize(self):
        scripts=["import time;time.sleep(30)",
                 "import sys;print('SECRET_SENTINEL');print('SECRET_SENTINEL',file=sys.stderr);sys.exit(2)",
                 "pass", "import sys;open(sys.argv[1],'wb').write(b'bad')",
                 "import sys;open(sys.argv[1],'wb').write(b'x'*(8*1024*1024+1))",
                 "import sys,os;os.mkfifo(sys.argv[1])",
                 "import sys,os;os.symlink('/etc/passwd',sys.argv[1])"]
        for code in scripts:
            with self.subTest(code=code):
                t=time.monotonic()
                with self.assertRaises(p.ConfigError) as error:voice.synthesize('text',self.command_cfg(code,command_timeout=.15))
                self.assertLess(time.monotonic()-t,2)
                self.assertNotIn('SECRET_SENTINEL',str(error.exception))
                self.assertNotIn('/etc/passwd',str(error.exception))
                if 'sleep' in code:self.assertIn('timed out',str(error.exception))

    def test_real_command_timeout_kills_descendant(self):
        marker=self.root/'descendant-lived'
        child="import time;from pathlib import Path;time.sleep(.6);Path("+repr(str(marker))+").touch()"
        code="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(30)"
        with self.assertRaises(p.ConfigError):voice.synthesize('text',self.command_cfg(code,command_timeout=.15))
        time.sleep(.8);self.assertFalse(marker.exists())

    def test_realtime_legacy_voice_rejected_and_custom_voice_object(self):
        cfg=self.realtime_cfg();cfg['voice']['tts']['voice']='nova'
        with self.assertRaises(p.ConfigError):voice.validate_runtime(cfg)
        self.assertEqual(cfg['voice']['tts']['voice'],'nova')
        cfg['voice']['tts']['voice']='voice_TEST'
        ws=FakeRealtime([{'type':'session.updated'},
                        {'type':'response.output_audio.delta','delta':base64.b64encode(b'\x01\x00'*100).decode()},
                        {'type':'response.done','response':{'status':'completed'}}])
        with patch('agentj.voice._RealtimeConnect',return_value=ws):voice.synthesize('hello',cfg)
        self.assertEqual(ws.sent[0]['session']['audio']['output']['voice'],{'id':'voice_TEST'})

    def test_command_schema_and_realtime_speed(self):
        for args in ([1],['python'],['python','arg\n','{output}'],['python']+['x']*32):
            with self.assertRaises(p.ConfigError):self.cfg(command=args)
        with self.assertRaises(p.ConfigError):self.cfg(mode='cloud',provider='command')
        cfg=self.realtime_cfg();cfg['voice']['tts']['rate']=2
        with self.assertRaises(p.ConfigError):voice.validate_runtime(cfg)

if __name__=='__main__':unittest.main()
