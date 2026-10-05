"""P59 (F2, ADR A166): ElevenLabs v4 model id and the local speech command provider.
Offline only: the ElevenLabs request is patched; the command provider runs real tiny Python scripts."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import wave
from unittest.mock import patch
from agentj import preferences as p, voice

ROOT = Path(__file__).resolve().parents[2]

WRITE_WAV = ("import sys,wave; w=wave.open(sys.argv[-1],'wb'); w.setnchannels(1); w.setsampwidth(2); "
             "w.setframerate(16000); w.writeframes(b'\\x01\\x00'*100); w.close()")


class P59TTS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p59-tts-', dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root/'config'),
            'AGENTJ_STATE_DIR': str(self.root/'state'), 'PATH': '/usr/bin:/bin',
            'ELEVENLABS_API_KEY': 'test-only-sentinel'})
        self.env.start(); self.addCleanup(self.env.stop)
        from agentj.state import State
        self.st = State(); self.st.init(); p.ensure()

    def cfg(self, **tts):
        return p.validate({'voice': {'tts': tts}})

    def command_cfg(self, code, *extra, **tts):
        return self.cfg(mode='host', provider='command', command=[sys.executable, '-c', code, *extra, '{output}'], **tts)

    def frames(self, audio):
        with wave.open(io.BytesIO(audio), 'rb') as w:
            return w.getframerate(), w.readframes(1000)

    # --- ElevenLabs v4 -----------------------------------------------------------------------------------------
    def test_eleven_v4_model_id_accepted_as_is(self):
        for model in ('eleven_v4', 'eleven_v4_turbo', 'eleven_multilingual_v2'):
            with self.subTest(model=model):
                cfg = self.cfg(mode='cloud', provider='elevenlabs', model=model, voice='ZhCloneVoice01')
                voice.validate_runtime(cfg)
                self.assertEqual(p.get(cfg, 'voice.tts.model'), model)
                with patch('agentj.voice._request', return_value=b'\x01\x00'*100) as req:
                    self.assertEqual(self.frames(voice.synthesize('你好，世界', cfg)), (16000, b'\x01\x00'*100))
                url, key, body = req.call_args.args[:3]
                self.assertEqual(url, voice.TTS_ELEVEN_URL+'ZhCloneVoice01?output_format=pcm_16000')
                self.assertEqual(json.loads(body)['model_id'], model)
                self.assertTrue(url.startswith('https://api.elevenlabs.io/'))
        # Omitted model → v4 default for the provider; a non-ElevenLabs id is refused for this provider.
        self.assertEqual(p.get(self.cfg(provider='elevenlabs'), 'voice.tts.model'), 'eleven_v4')
        with self.assertRaises(p.ConfigError):
            voice.validate_runtime(self.cfg(mode='cloud', provider='elevenlabs', model='gpt-realtime-2.1-mini', voice='ZhCloneVoice01'))

    # --- local command provider --------------------------------------------------------------------------------
    def test_command_disabled_by_default(self):
        d = p.defaults()
        self.assertEqual((p.get(d, 'voice.tts.mode'), p.get(d, 'voice.tts.provider'), p.get(d, 'voice.tts.command')),
                         ('phone', 'openai', []))
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(p.ConfigError):
                voice.synthesize('hello', p.validate({}))
            popen.assert_not_called()
        # provider=command without argv is refused at activation, never run.
        with self.assertRaises(p.ConfigError):
            voice.validate_runtime(self.cfg(mode='host', provider='command'))

    def test_command_happy_path_stdin_private_dir(self):
        text = '你好，这是一段很长的回复文字'
        code = ("import os,stat,sys,wave; t=sys.stdin.read(); assert t=="+repr(text)+"; "
                "assert "+repr(text)+" not in ' '.join(sys.argv); out=sys.argv[-1]; d=os.path.dirname(out); "
                "assert os.getcwd()==d and stat.S_IMODE(os.stat(d).st_mode)==0o700; "+WRITE_WAV)
        self.assertEqual(self.frames(voice.synthesize(text, self.command_cfg(code))), (16000, b'\x01\x00'*100))

    def test_command_environment_drops_agentj_runtime_values(self):
        code = ("import os,sys; assert 'OPENCODE_SERVER_PASSWORD' not in os.environ; "
                "assert not [k for k in os.environ if k.startswith('AGENTJ_')]; "
                "assert os.environ['MY_SPEECH_KEY']=='owner-own'; assert os.environ['PATH']; "+WRITE_WAV)
        with patch.dict(os.environ, {'OPENCODE_SERVER_PASSWORD': 'x', 'AGENTJ_SUDO_BIN': '/x', 'MY_SPEECH_KEY': 'owner-own'}):
            voice.synthesize('hi', self.command_cfg(code))

    def test_command_no_shell_injection(self):
        marker = self.root/'pwned'
        hostile = '; touch '+str(marker)+' $(touch '+str(marker)+') `touch '+str(marker)+'`'
        code = "import sys; assert sys.argv[1]=="+repr(hostile)+"; "+WRITE_WAV
        voice.synthesize('$(touch '+str(marker)+')', self.command_cfg(code, hostile))
        self.assertFalse(marker.exists())

    def test_command_timeout_non_wav_and_oversize_refused(self):
        cases = {'timeout': "import time; time.sleep(30)",
                 'non-wav': "import sys; open(sys.argv[-1],'wb').write(b'ID3'+b'\\0'*200)",
                 'mp3-in-riff': "import sys; open(sys.argv[-1],'wb').write(b'RIFF'+b'\\0'*100)",
                 'oversize': "import sys; open(sys.argv[-1],'wb').write(b'RIFF'+b'x'*(8*1024*1024))",
                 'stderr-secret': "import sys; print('SECRET_SENTINEL', file=sys.stderr); sys.exit(3)"}
        for name, code in cases.items():
            with self.subTest(name):
                t = time.monotonic()
                with self.assertRaises(p.ConfigError) as err:
                    voice.synthesize('text', self.command_cfg(code, command_timeout=0.2))
                self.assertLess(time.monotonic()-t, 3)
                self.assertNotIn('SECRET_SENTINEL', str(err.exception))
                if name == 'timeout':
                    self.assertIn('timed out', str(err.exception))

    def test_tier_follows_existing_mechanism(self):
        schema = json.loads((ROOT/'host/agentj/config/schema.json').read_text())['keys']
        # Human tier is only the four pairing/relay pointers (A-P45b); the speech command keys are user tier (A166).
        self.assertEqual(sorted(k for k, m in schema.items() if m['tier'] == 'human'),
                         ['human.devices', 'human.relay', 'human.remote_unbind', 'human.web'])
        for key in ('voice.tts.command', 'voice.tts.command_timeout', 'voice.tts.provider', 'voice.tts.mode'):
            self.assertEqual(schema[key]['tier'], 'user', key)
        self.assertIn('command', schema['voice.tts.provider']['enum'])
        # The existing tier enforcement still refuses human/locked keys through the same CLI path.
        for key, code in (('human.relay', 2), ('security.e2e', 1)):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(p.command(['set', key, 'x', '--json']), code)
            self.assertFalse(json.loads(out.getvalue())['ok'])
        # The owner's Agent can set the argv from the computer (dry run) ...
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(p.command(['set', 'voice.tts.command', json.dumps([sys.executable, '{output}']), '--dry-run', '--json']), 0)
        self.assertTrue(json.loads(out.getvalue())['ok'])
        # ... but never from the phone channel, and argv is never projected to the phone.
        from agentj import serve
        self.assertNotIn('voice.tts.command', serve.PREF_SET_KEYS)
        self.assertNotIn('voice.tts.provider', serve.PREF_SET_KEYS)
        settings = (ROOT/'web/public/js/settings.js').read_text()
        self.assertNotIn('voice.tts.command', settings)
        p.path().write_text(json.dumps({'voice': {'tts': {'mode': 'host', 'provider': 'command',
                                                          'command': [sys.executable, '-c', 'pass', '{output}']}}}))
        host = serve.Host(self.st, read_stdin=False)
        self.assertNotIn('command', host.preferences_msg()['value']['voice']['tts'])


if __name__ == '__main__':
    unittest.main()
