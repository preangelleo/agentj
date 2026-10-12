import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
from agentj import voice,wake,preferences as p,telegram
from agentj.state import State

class VoicePreferencesTest(unittest.TestCase):
    def test_macos_capacity_uses_native_memory_query(self):
        import subprocess
        from agentj import voice
        with patch('agentj.voice.sys.platform','darwin'),patch('agentj.voice.os.cpu_count',return_value=4),patch('agentj.voice.subprocess.run',return_value=subprocess.CompletedProcess([],0,'8589934592\n','')):
            self.assertEqual(voice.hardware()['memory_mib'],8192)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='aj-voice-',dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.env=patch.dict(os.environ,{'HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'.config'),'AGENTJ_STATE_DIR':str(self.home/'state'),'PATH':'/usr/bin:/bin'})
        self.env.start();self.addCleanup(self.env.stop);self.st=State();self.st.init()
    def test_wake_bilingual_mixed_and_unknown_pronunciation(self):
        for name in ['嘿小J','嘿贾维斯','Hey Ada','Hey Agent J','嘿 Alice','嘿小王']:
            with self.subTest(name=name):
                v=wake.keyword(name);self.assertTrue(v.endswith(' @agentj'));self.assertTrue(all(t in wake.tokens() for t in v.split()[:-1]))
        with self.assertRaises(p.ConfigError):wake.keyword('Hey Qzxypfl')
        self.assertEqual(wake.keyword('Hey Qzxypfl','HH EY1 K W IY1'),'HH EY1 K W IY1 @agentj')
    def test_cloud_asr_direct_owner_provider(self):
        cfg=p.defaults();cfg['voice']['asr'].update(mode='cloud',model='qwen/qwen3-asr-1.7b',key_env='AJ_TEST_ASR_KEY')
        f=self.home/'test.wav';f.write_bytes(b'RIFF-fake-controlled-audio')
        with patch.dict(os.environ,{'AJ_TEST_ASR_KEY':'test-only-sentinel'}),patch('agentj.voice._request',return_value=b'{"text":"hello"}') as request:
            self.assertEqual(voice.cloud_asr(f,cfg)['text'],'hello')
            self.assertEqual(request.call_args.args[0],voice.ASR_URL);self.assertIn(b'qwen/qwen3-asr-1.7b',request.call_args.args[2])
    def test_cloud_tts_direct_provider_and_errors_redacted(self):
        cfg=p.defaults();cfg['voice']['tts'].update(mode='cloud',model='gpt-4o-mini-tts',voice='alloy',key_env='AJ_TEST_TTS_KEY')
        with patch.dict(os.environ,{'AJ_TEST_TTS_KEY':'test-only-sentinel'}),patch('agentj.voice._request',return_value=b'RIFF-test') as request:
            self.assertEqual(voice.synthesize('hello',cfg),b'RIFF-test');self.assertEqual(request.call_args.args[0],voice.TTS_URL)
        # Synthetic error marker verifies redaction; no real provider credential.
        with (
            patch.dict(os.environ, {'AJ_TEST_TTS_KEY': 'test-only-sentinel'}),
            patch('agentj.voice._request', side_effect = ValueError('SECRET_SENTINEL')),
        ):
            with self.assertRaises(p.ConfigError) as cm:voice.synthesize('hello',cfg)
            self.assertNotIn('SECRET_SENTINEL',str(cm.exception))
    def test_elevenlabs_pcm_wrapped_and_plain_voice_id_only(self):
        import io,wave
        cfg=p.defaults();cfg['voice']['tts'].update(mode='cloud',provider='elevenlabs',model='eleven_multilingual_v2',voice='TestVoice123',key_env='AJ_TEST_TTS_KEY')
        pcm=b'\x00\x00'*100
        with patch.dict(os.environ,{'AJ_TEST_TTS_KEY':'test-only-sentinel'}),patch('agentj.voice._request',return_value=pcm) as request:
            audio=voice.synthesize('hello',cfg)
            with wave.open(io.BytesIO(audio),'rb') as w:
                self.assertEqual(w.getframerate(),16000);self.assertEqual(w.readframes(100),pcm)
            self.assertEqual(request.call_args.kwargs['auth_header'],'xi-api-key')
            self.assertEqual(request.call_args.args[0],voice.TTS_ELEVEN_URL+'TestVoice123?output_format=pcm_16000')
            cfg['voice']['tts']['voice']='../another-endpoint'
            with self.assertRaises(p.ConfigError):voice.synthesize('hello',cfg)
            self.assertEqual(request.call_count,1)
    def test_openai_asr_uses_selected_endpoint(self):
        cfg=p.defaults();cfg['voice']['asr'].update(mode='cloud',provider='openai',model='gpt-4o-mini-transcribe',key_env='AJ_TEST_ASR_KEY')
        f=self.home/'sample.wav';f.write_bytes(b'RIFF-controlled')
        with patch.dict(os.environ,{'AJ_TEST_ASR_KEY':'test-only-sentinel'}),patch('agentj.voice._request',return_value=b'{"text":"hello"}') as request:
            self.assertTrue(voice.cloud_asr(f,cfg)['ok']);self.assertEqual(request.call_args.args[0],voice.ASR_OPENAI_URL)
    def test_cloud_not_selected_by_low_hardware(self):
        with patch('os.cpu_count',return_value=1),patch('os.sysconf',return_value=100):self.assertEqual(voice.hardware()['tier'],'cloud-recommended')
        self.assertEqual(p.defaults()['voice']['asr']['mode'],'local')
    def test_tg_key_never_in_user_file(self):
        with self.assertRaises(p.ConfigError):p.parse('{channels:{items:[{id:"phone",type:"phone",key_env:"SECRET_SENTINEL"}]}}')
        with patch('os.isatty',return_value=False):self.assertFalse(telegram.enroll(123,'TELEGRAM_BOT_TOKEN')['ok'])

class TelegramPreferencesTest(unittest.IsolatedAsyncioTestCase):
    def test_unicode_reply_chunks_respect_provider_limit(self):
        text='\U0001f600'*5000+'Finished'
        parts=list(telegram.chunks(text))
        self.assertEqual(''.join(parts),text)
        self.assertTrue(all(len(x.encode('utf-16-le'))//2<=4000 for x in parts))

    async def test_reenrollment_rejects_stale_poll_and_old_reply(self):
        with tempfile.TemporaryDirectory(prefix='aj-tg-race-',dir='/var/tmp') as d:
            st=State(Path(d)/'state');st.init()
            old={'owner_id':123,'key_env':'AJ_TEST_TG_KEY','generation':'old'}
            new={'owner_id':456,'key_env':'AJ_TEST_TG_KEY','generation':'new'}
            host=SimpleNamespace(st=st,preferences={'channels':{'items':[{'id':'telegram','type':'telegram'}]}},agent=object(),stopped=lambda:False,_accept=AsyncMock())
            tg=telegram.Telegram(host);tg.turn_enrollment[1]=old
            update={'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'late'}}
            with patch('agentj.telegram.configuration',return_value=new):
                self.assertFalse(await tg.incoming(update,old))
                tg.completed({'id':1,'src':{'k':'telegram'},'reply':{'text':'Private old reply'}})
            host._accept.assert_not_called();self.assertTrue(tg.out.empty())
    async def test_allowlist_private_owner_and_no_approval(self):
        with tempfile.TemporaryDirectory(prefix='aj-tg-',dir='/var/tmp') as d:
            st=State(Path(d)/'state');st.init()
            host=SimpleNamespace(st=st,preferences={'channels':{'items':[{'id':'telegram','type':'telegram'}]}},agent=object(),stopped=lambda:False,_accept=AsyncMock(),stop_turn=AsyncMock())
            tg=telegram.Telegram(host);cfg={'owner_id':123,'key_env':'AJ_TEST_TG_KEY'}
            enrolled=patch('agentj.telegram.configuration',return_value=cfg);enrolled.start();self.addCleanup(enrolled.stop)
            def msg(sender=123,chat=123,text='hello',typ='private'):return {'message':{'from':{'id':sender},'chat':{'id':chat,'type':typ},'text':text}}
            for update in [msg(sender=456),msg(chat=-123,typ='group')]:self.assertFalse(await tg.incoming(update,cfg))
            with patch('agentj.telegram.api',return_value={}):
                self.assertFalse(await tg.incoming(msg(text='/approve'),cfg))
                self.assertTrue(await tg.incoming(msg(),cfg))
            host._accept.assert_awaited_once();self.assertEqual(host._accept.call_args.args[0].source_kind,'telegram')

if __name__=='__main__':unittest.main()

class SpeechTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='aj-tts-noise-',dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.env=patch.dict(os.environ,{'HOME':str(root),'AGENTJ_STATE_DIR':str(root/'state'),'XDG_CONFIG_HOME':str(root/'.config'),'PATH':'/usr/bin:/bin'})
        self.env.start();self.addCleanup(self.env.stop)
        from agentj.serve import Host,Session
        from agentj.noise import CipherState,Keypair
        self.st=State();self.st.init();p.ensure();self.host=Host(self.st,read_stdin=False)
        peer=Keypair.generate();did=self.st.add_device(peer.pub,'test phone');key=b'k'*32
        self.s=Session(cid=1,state='ready',device=did,pub=peer.pub,p33=True,send=CipherState(key));self.recv=CipherState(key)
        self.host.sessions={1:self.s};self.frames=[]
        async def op(_op,cid,payload=b''):self.frames.append(payload)
        self.host._op=op
    async def test_reply_audio_encrypted_chunks_and_revoke_stops_delivery(self):
        from agentj import wire
        raw=b'RIFF-private-acoustic-sentinel'+b'\0'*30000
        turn=self.host.hist.add({'k':'agent'},reply='Final result',end='done')
        with patch('agentj.voice.synthesize',return_value=raw):await self.host.on_tts(self.s,{'r':'A'*22,'id':turn['id']})
        self.assertTrue(self.frames);self.assertFalse(any(b'acoustic-sentinel' in frame for frame in self.frames))
        msgs=[wire.unpad_json(self.recv.decrypt(b'',f[1:]),wire.MAX_JSON_P33) for f in self.frames]
        import base64
        self.assertEqual(b''.join(base64.b64decode(m['data']) for m in msgs if m['t']=='tts_chunk'),raw)
        self.assertTrue(msgs[-1]['ok']);self.assertEqual(msgs[-1]['bytes'],len(raw))
        self.frames=[]
        async def revoke(_op,cid,payload=b''):
            self.frames.append(payload);self.host.sessions.clear()
        self.host._op=revoke
        with patch('agentj.voice.synthesize',return_value=raw):await self.host.on_tts(self.s,{'r':'B'*22,'id':turn['id']})
        self.assertEqual(len(self.frames),1,'revoked after first encrypted chunk receives no remaining audio')
    async def test_telegram_real_host_callbacks_queue_final_reply_for_original_enrollment(self):
        from unittest.mock import Mock
        cfg={'owner_id':123,'key_env':'AJ_TEST_TG_KEY','generation':'test-generation'}
        self.host.preferences['channels']['items']=[{'id':'telegram','type':'telegram'}]
        self.host.agent=SimpleNamespace(kind='claude',status='idle',halting=False,submit=Mock())
        self.host.telegram=telegram.Telegram(self.host)
        update={'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'Please answer'}}
        with patch('agentj.telegram.configuration',return_value=cfg),patch.object(self.host,'push_notify'):
            self.assertTrue(await self.host.telegram.incoming(update,cfg))
            send=self.host.agent.submit.call_args.args[0]
            self.host.agent_turn_start('Please answer',send)
            self.host.agent_text('Final answer')
            self.assertTrue(self.host.telegram.out.empty(),'process is not read aloud or forwarded')
            self.host.agent_turn_end()
            enrollment,text=self.host.telegram.out.get_nowait()
            self.assertEqual(enrollment,cfg);self.assertEqual(text,'Final answer')
            self.assertEqual(self.host.hist.get(send.turn)['src']['k'],'telegram')
    async def test_revoked_before_task_starts_never_calls_provider(self):
        turn=self.host.hist.add({'k':'agent'},reply='Final private result',end='done')
        self.host.sessions.clear()
        with patch('agentj.voice.synthesize') as synth:await self.host.on_tts(self.s,{'r':'A'*22,'id':turn['id']})
        synth.assert_not_called();self.assertFalse(self.frames)
    async def test_incomplete_turn_never_calls_provider(self):
        turn=self.host.hist.add({'k':'agent'},reply='Incomplete private work',end='open')
        with patch('agentj.voice.synthesize') as synth:await self.host.on_tts(self.s,{'r':'A'*22,'id':turn['id']})
        synth.assert_not_called()
