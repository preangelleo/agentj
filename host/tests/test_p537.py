"""P537: factory merge, signed phone editing, watchlist injection and avatar review."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import heartbeat as hb, controls, main_identity, avatar_log
from test_controls import _state, Phone, sign


class Prompt(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)/'cfg'/'heartbeat.md'
        self.mock=patch.object(hb,'prompt_path',return_value=self.p);self.mock.start();self.addCleanup(self.mock.stop)

    def test_factory_checklist_and_custom_preservation(self):
        for lang in ('zh','en'):
            self.assertIn('systemd',hb.TEMPLATE[lang]);self.assertIn('launchd',hb.TEMPLATE[lang])
            self.assertIn('Windows',hb.TEMPLATE[lang]);self.assertIn('avatar-log --since',hb.TEMPLATE[lang])
            text='My personal instruction\n'
            merged=hb.merge_checklist(text,lang)
            self.assertTrue(merged.startswith(text));self.assertEqual(hb.merge_checklist(merged,lang),merged)
        hb.write_prompt('keep my text')
        self.assertFalse(hb.ensure_prompt());self.assertEqual(hb.read_prompt()[0],'keep my text')
        self.assertTrue(hb.prompt_view('zh')['update_available'])
        hb.update_prompt({'op':'merge','text':None,'sha':hb.prompt_view('zh')['sha']},'zh')
        self.assertFalse(hb.prompt_view('zh')['update_available'])
        self.assertTrue(hb.read_prompt()[0].startswith('keep my text'))

    def test_atomic_revision_and_rejected_values(self):
        hb.write_prompt('original')
        target={'op':'save','text':'new words','sha':hb.prompt_view('en')['sha']}
        hb.write_prompt('changed elsewhere')
        with self.assertRaisesRegex(ValueError,'changed'):hb.update_prompt(target,'en')
        for text in ('', 'x'*(hb.PROMPT_MAX+1), 'bad\x01control', 'sk-ant-api03-'+'A'*90):
            with self.assertRaises(ValueError):hb.update_prompt({**target,'sha':hb.prompt_view('en')['sha'],'text':text},'en')
        self.assertEqual(hb.read_prompt()[0],'changed elsewhere')
        self.assertEqual(self.p.stat().st_mode&0o777,0o600)
        self.p.unlink();self.p.symlink_to(Path(self.tmp.name)/'other')
        with self.assertRaises((ValueError,OSError)):hb.update_prompt({'op':'save','text':'no','sha':None},'en')
        self.assertFalse((Path(self.tmp.name)/'other').exists())

    def test_all_harnesses_pointer_outside_core(self):
        for lang in ('zh','en'):
            for h in ('claude','codex','opencode'):
                text=main_identity.prompt({'language':lang},h)
                self.assertIn('agentj heartbeat prompt show',text)
                self.assertNotIn('<!-- agentj:watchlist v1 -->',text)
        self.assertEqual(main_identity.verify_core()['version'],22)

    def test_doctor_read_only_notice(self):
        from agentj.doctor import check_heartbeat_prompt
        hb.write_prompt('custom')
        self.assertEqual(check_heartbeat_prompt()['status'],'warn')
        self.assertEqual(hb.read_prompt()[0],'custom')

    def test_phone_real_signature_and_tamper_replay_stale(self):
        with tempfile.TemporaryDirectory() as d:
            st=_state(d);ph=Phone(st)
            host=SimpleNamespace(st=st,nonces=controls.Nonces(),channel=ph.channel,send_app=AsyncMock(),lang='zh')
            beat=hb.Heartbeat(host)
            session=SimpleNamespace(p33=True,device=ph.did)
            hb.write_prompt('original')
            target={'op':'save','text':'saved by phone','sha':hb.prompt_view('zh')['sha']}
            obj=sign(ph,'heartbeat_write',target,{'t':'heartbeat_write','r':'a'*16,**target})
            asyncio.run(beat.on_phone(session,{**obj,'text':'tampered'}))
            self.assertFalse(host.send_app.call_args.args[1]['ok']);self.assertEqual(hb.read_prompt()[0],'original')
            asyncio.run(beat.on_phone(session,obj));self.assertTrue(host.send_app.call_args.args[1]['ok'])
            self.assertEqual(hb.read_prompt()[0],'saved by phone')
            asyncio.run(beat.on_phone(session,obj));self.assertEqual(host.send_app.call_args.args[1]['why'],'replay')
            target={'op':'reset','text':None,'sha':hb.prompt_view('zh')['sha']}
            old=sign(ph,'heartbeat_write',target,{'t':'heartbeat_write','r':'b'*16,**target},ts=int(time.time()*1000)-300000)
            asyncio.run(beat.on_phone(session,old));self.assertEqual(host.send_app.call_args.args[1]['why'],'stale')
            good=sign(ph,'heartbeat_write',target,{'t':'heartbeat_write','r':'c'*16,**target})
            asyncio.run(beat.on_phone(session,good));self.assertEqual(hb.read_prompt()[0],hb.TEMPLATE['zh'])
            self.assertNotIn('saved by phone',st.log_path.read_text())


class Avatar(unittest.TestCase):
    def test_review_includes_old_unknown_and_current_outcomes(self):
        store=SimpleNamespace(data={'records':{'old':{'at':1,'text':'old','id':'old','state':'replied'},
            'new':{'at':10,'text':'question','id':'new','source':'telegram','reply_summary':'answer','state':'replied','outcome':'sent','delivery':'sent'}}})
        data=avatar_log.rows([store,None],0)
        self.assertEqual([r['id'] for r in data],['old','new']);self.assertIsNone(data[0]['reply'])
        self.assertTrue(data[0]['needs_review']);self.assertFalse(data[1]['needs_review'])
        self.assertEqual(len(avatar_log.rows([store],5)),1)
        self.assertEqual(avatar_log.since_time('2h',10000),2800)
        with self.assertRaises(ValueError):avatar_log.since_time('garbage')
        huge=SimpleNamespace(data={'records':{str(i):{'id':str(i),'at':i,'text':'字'*1000,'state':'pending'} for i in range(300)}})
        host=SimpleNamespace(wechat=SimpleNamespace(store=huge),external_channels=None)
        snap=avatar_log.snapshot(host,'1970-01-01')
        self.assertTrue(snap['truncated']);self.assertLess(len(json.dumps(snap,ensure_ascii=False).encode()),hb.REQ_MAX)

    def test_delivery_record_is_allowlisted_projection(self):
        from agentj.external_channels import ExternalChannels
        import hashlib
        rid=hashlib.sha256(b'mail:m1').hexdigest()
        channel=SimpleNamespace(store=SimpleNamespace(data={'records':{rid:{'state':'replied'}}},save=lambda:None))
        ExternalChannels.record_delivery(channel,'mail','m1','sent')
        self.assertEqual(channel.store.data['records'][rid]['delivery'],'sent')
        ExternalChannels.record_delivery(channel,'mail','m1','failed')
        self.assertEqual(channel.store.data['records'][rid]['delivery'],'unknown')

class SocketCLI(unittest.IsolatedAsyncioTestCase):
    async def test_encrypted_store_to_fenced_agent_cli(self):
        from agentj.wechat_store import Store
        with tempfile.TemporaryDirectory(prefix='p537-',dir='/tmp') as d:
            st=_state(d);store=Store(st,namespace='external')
            record=store.put('a'*64,[],'owner question')
            record.update(source='mail',state='replied',reply_summary='avatar answer',outcome='sent',delivery='sent',private_config='must not be projected')
            store.save()
            # Re-open from ciphertext: this is not a projection over a fabricated plaintext-only store.
            reopened=Store(st,namespace='external')
            self.assertNotIn(b'owner question',reopened.path.read_bytes())
            host=SimpleNamespace(st=st,external_channels=SimpleNamespace(store=reopened),wechat=None)
            beat=hb.Heartbeat(host)
            path=str(Path(d)/'heartbeat.sock')
            server=await asyncio.start_unix_server(beat.on_client,path=path,limit=hb.REQ_MAX)
            try:
                env={**os.environ,hb.ENV:path,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),
                     'HOME':d,'XDG_CONFIG_HOME':d+'/config','AGENTJ_STATE_DIR':d+'/unused'}
                proc=await asyncio.create_subprocess_exec(sys.executable,'-m','agentj.cli','inbox','avatar-log','--since','2h','--json',
                    env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                stdout,stderr=await asyncio.wait_for(proc.communicate(),10)
                self.assertEqual(proc.returncode,0,stderr.decode())
                result=json.loads(stdout);self.assertTrue(result['ok']);self.assertEqual(result['records'][0]['reply'],'avatar answer')
                self.assertEqual(result['records'][0]['delivery'],'sent');self.assertNotIn('private_config',stdout.decode())
                self.assertIn('wechat',result['unavailable'])
            finally:
                server.close();await server.wait_closed();store.close();reopened.close()
