"""Synthetic credentials through the actual phone encryption boundary; no real HOME reads."""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from agentj.serve import Host
from agentj import silent, wire

ENV_PREFIX = '_'.join(('DB', 'PASSWORD')) + '='
KEY_BEGIN = '-----' + 'BEGIN ' + 'OPENSSH ' + 'PRIVATE ' + 'KEY' + '-----'
KEY_END = '-----' + 'END ' + 'OPENSSH ' + 'PRIVATE ' + 'KEY' + '-----'

class Egress(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cwd = self.home / 'coding' / 'project'; self.cwd.mkdir(parents=True)
        self.env_value = 'ordinarypassword123'
        self.auth_value = 'authopaquevalue456'
        self.key_value = 'c3ludGhldGljLXByaXZhdGUtYm9keS0xMjM0NTY3ODkw'
        (self.home/'coding'/'.env').write_text(ENV_PREFIX+self.env_value+'\n')
        (self.home/'.codex').mkdir(); (self.home/'.codex'/'auth.json').write_text(json.dumps({'tokens':{'access_token':self.auth_value}}))
        (self.home/'.ssh').mkdir(); (self.home/'.ssh'/'id_ed25519').write_text(KEY_BEGIN+'\n'+self.key_value+'\n'+KEY_END+'\n')
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'CODEX_HOME':str(self.home/'.codex'), 'AGENTJ_ENV_FILE':str(self.home/'absent.env')}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        from agentj import display_privacy   # 0.30.0 ships masking off; these tests prove the dormant code
        p = patch.object(display_privacy, 'ENABLED', True); p.start(); self.addCleanup(p.stop)
        self.frames=[]
        def encrypt(aad, data):
            self.frames.append(wire.unpad_json(data))
            return b'ciphertext'
        self.s = NS(cid=1,device='fixture',pub=b'pub',state='ready',p33=False,send=NS(encrypt=encrypt))
        self.h = NS(agent_cfg={'dir':str(self.cwd)},sessions={1:self.s},send_lock=asyncio.Lock(),_op=AsyncMock(),st=NS(log=lambda *a,**k:None,is_allowed=lambda _:True))
    async def send(self, message):
        await Host.send_app(self.h,self.s,message)
        return self.frames[-1]
    async def test_three_positive_controls_and_bare_values_after_renderer(self):
        for label, text, value in (
            ('dotenv',ENV_PREFIX+self.env_value,self.env_value),
            ('auth-json',json.dumps({'access_token':self.auth_value}),self.auth_value),
            ('ssh-private-key',(self.home/'.ssh'/'id_ed25519').read_text(),self.key_value),
            ('dotenv-value-only',self.env_value,self.env_value),
            ('auth-value-only',self.auth_value,self.auth_value),
            ('ssh-body-line-only',self.key_value,self.key_value)):
            with self.subTest(control=label):
                entry={'id':'fixed-id','text':text,'seq':1,'from':'agent','ts':1}
                message=Host._render(self.h,entry,self.s)
                out=await self.send(message)
                self.assertNotIn(value,json.dumps(out),label)
                self.assertIn('<redacted>',out['text'])
                self.assertEqual(text,message['text'])
    async def test_history_pre_tool_output_search_and_reconnect(self):
        for kind, fields in (
            ('hist_turn',{'turn':{'reply':{'text':self.env_value,'pre':self.auth_value,'output':self.key_value}}}),
            ('hist_page',{'turns':[{'reply':{'text':self.env_value}}]}),
            ('hist_found',{'hits':[{'snip':self.auth_value}]})):
            with self.subTest(kind=kind):
                out=await self.send({'t':kind,**fields})
                raw=json.dumps(out)
                for value in (self.env_value,self.auth_value,self.key_value):self.assertNotIn(value,raw)
    async def test_streamed_known_prefixes_and_sentence_output(self):
        for value in (self.env_value,self.auth_value,self.key_value):
            for size in range(1,len(value)+1):
                out=await self.send({'t':'hist_turn','turn':{'end':'open','reply':{'text':value[:size]}}})
                self.assertEqual('<redacted>',out['turn']['reply']['text'])
                for prefix in (ENV_PREFIX, '"access_token": "'):
                    labelled=await self.send({'t':'msg','text':prefix+value[:size]})
                    self.assertIn('<redacted>',labelled['text'])
            out=await self.send({'t':'call_event','kind':'sentence','text':value+'.'})
            self.assertNotIn(value,out['text'])
    async def test_tts_source_and_synthesizer_are_masked(self):
        from agentj import spoken, voice, voice_engines
        self.h.hist=NS(get=lambda _: {'end':'done','reply':{'text':self.env_value}})
        self.h.preferences={}
        self.h.send_app=lambda session,obj: Host.send_app(self.h,session,obj)
        with patch.object(spoken,'script',return_value={'text':self.auth_value,'lang':'en','spoken':False,'cached':False}) as rewrite, patch.object(voice,'synthesize',return_value=b'audio') as synth, patch.object(voice_engines,'record'):
            await Host.on_tts(self.h,self.s,{'r':'a'*22,'id':1})
            self.assertEqual('<redacted>',rewrite.call_args.args[1])
            self.assertEqual('<redacted>',synth.call_args.args[0])

    async def test_short_known_password_and_nested_display_value(self):
        (self.home/'coding'/'.env').write_text(ENV_PREFIX+'short7\n')
        for size in range(1,len('short7')+1):
            for prefix in ('', ENV_PREFIX, '"access_token": "'):
                self.assertIn('<redacted>',(await self.send({'t':'msg','text':prefix+'short7'[:size]}))['text'])
        for text in ('short7',ENV_PREFIX+'short7',': '.join(('password','short7'))):
            self.assertNotIn('short7',(await self.send({'t':'msg','text':text}))['text'])
        out=await self.send({'t':'hist_turn','turn':{'reply':{'content':{'value':self.auth_value}}}})
        self.assertNotIn(self.auth_value,json.dumps(out))

    async def test_source_change_is_refreshed_and_unreadable_fails_closed(self):
        await self.send({'t':'msg','text':'ordinary'})
        changed='secondopaquevalue789'
        (self.home/'coding'/'.env').write_text(ENV_PREFIX+changed)
        self.assertNotIn(changed,(await self.send({'t':'msg','text':changed}))['text'])
        from agentj import peer_guard
        with patch.object(peer_guard.SecretIndex,'require_complete',side_effect=OSError('synthetic incomplete')):
            self.assertIn('<redacted>',(await self.send({'t':'msg','text':'hidden during scan failure'}))['text'])
    async def test_negative_controls_and_exact_signed_secret_contracts(self):
        ordinary='build passed; ~/project/output.log; user@example.com; systemctl --user status service'
        self.assertEqual(ordinary,(await self.send({'t':'msg','id':'keep-id','text':ordinary}))['text'])
        for kind in ('ask','approval_review','config','secret_out_val'):
            # secret_out_val has its own delivery guard; projection itself must be byte-exact.
            obj={'t':kind,'value':self.auth_value,'summary':self.env_value,'sig':'unchanged'}
            self.assertIs(obj,silent.public_message(obj))
