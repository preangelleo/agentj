"""P579: offline regression; no env files, credentials, or native owner session."""
import _hermetic  # noqa: F401
import asyncio
import copy
import email
import email.policy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from agentj import mail as M, provider_runtime as PR, shared_modelswitch as ms, action_policy


class ProviderURL(unittest.TestCase):
    def test_402_ark_versioned_paths_remain_exact(self):
        with patch.object(PR.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8',443))]):
            for path in ('/api/v3','/api/coding/v3','/api/plan/v3'):
                for suffix in ('models','responses','messages'):
                    self.assertEqual(PR.public_url('https://ark.example'+path, suffix), 'https://ark.example'+path+'/'+suffix)
            self.assertEqual(PR.public_url('https://example.com', 'models'), 'https://example.com/v1/models')

    def test_402_actual_probe_uses_versioned_endpoint_for_every_harness(self):
        from agentj import provider_profiles as PP
        import json
        for path in ('/api/v3','/api/coding/v3','/api/plan/v3'):
            for harness,api,suffix in [('codex','openai','responses'),('claude','anthropic','messages'),('opencode','openai','responses')]:
                with self.subTest(path=path,harness=harness):
                    response=Mock();response.status=200;response.read.return_value=json.dumps({'output':[{'text':'OK'}],'content':[{'text':'OK'}]}).encode()
                    opener=MagicMock();opener.open.return_value.__enter__=Mock(return_value=response);opener.open.return_value.__exit__=Mock(return_value=False)
                    with patch.object(PR.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]), patch.object(PP.urllib.request,'build_opener',return_value=opener):
                        PP.probe({'base_url':'https://ark.example'+path,'model':'fixture','harness':harness,'api':api},'synthetic')
                    self.assertEqual(opener.open.call_args.args[0].full_url,'https://ark.example'+path+'/'+suffix)


class ModelPicker(unittest.TestCase):
    def test_403_absent_1m_is_never_default_success(self):
        with patch.object(ms,'herdr_bin',return_value='herdr'), patch.object(ms,'locate',return_value=('pane','idle')), \
             patch.object(ms,'screen',return_value='❯ '), patch.object(ms,'_pick',side_effect=['row_missing',None,'row_missing']) as pick:
            result=ms.apply({}, 'opus-1m-context', None)
        self.assertEqual(result['result'], ms.FAILED)
        self.assertEqual(result['why'], 'row_missing')

    def test_403_default_suffix_does_not_change_model_identity(self):
        self.assertEqual(ms.key_of('Opus 5.5 (default)'), 'opus-5.5')

    def test_403_native_one_million_row_survives_description(self):
        self.assertEqual(ms.row_name('Opus', 'Opus 5.5 (1M context) · Best'), 'Opus 5.5 (1M context)')


class MailRead(unittest.IsolatedAsyncioTestCase):
    async def test_397_only_successfully_delivered_allowlist_marks_read(self):
        from agentj.state import State
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp));st.init()
            host=SimpleNamespace(st=st,agent=object(),stopped=lambda:False,lang='en')
            m=M.Mail(host)
            cfg=M.validate({**M.blank_config(), 'auth':'off', 'allow':[{'addr':'owner@example.com','role':'owner'}]})
            M.save_config(st,cfg)
            M.save_state(st,{**M.read_state(st),'uidvalidity':9})
            raw=b'From: owner@example.com\r\nMessage-ID: <one@example.com>\r\n\r\nHello'
            with patch.object(m,'deliver',new=AsyncMock(return_value=True)), patch.object(m,'password',return_value='synthetic'), \
                 patch.object(M,'mark_read',return_value=True, create=True) as mark:
                self.assertEqual(await m.one(cfg,5,len(raw),raw),'delivered')
                mark.assert_called_once()
                mark.reset_mock()
                outsider=raw.replace(b'owner@example.com',b'other@example.com')
                self.assertEqual(await m.one(cfg,6,len(outsider),outsider),'not_allowed')
                mark.assert_not_called()
            self.assertTrue(M.blank_config()['mark_processed_read'])

    async def test_397_own_auto_only_if_allowlisted_and_authenticated(self):
        from agentj.state import State
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp));st.init();m=M.Mail(SimpleNamespace(st=st,agent=object(),stopped=lambda:False,lang='en'))
            cfg=M.validate({**M.blank_config(),'auth':'off','allow':[{'addr':'owner@example.com','role':'owner'}]})
            M.save_config(st,cfg)
            raw=b'From: owner@example.com\r\nX-AgentJ: reply\r\nMessage-ID: <auto@example.com>\r\n\r\nHello'
            with patch.object(M,'mark_read',return_value=True) as mark, patch.object(m,'password',return_value='synthetic'):
                self.assertEqual(await m.one(cfg,5,len(raw),raw),'auto');mark.assert_called_once();mark.reset_mock()
                raw=raw.replace(b'owner@example.com',b'other@example.com')
                self.assertEqual(await m.one(cfg,6,len(raw),raw),'auto');mark.assert_not_called()

    async def test_397_disabled_and_failed_delivery_leave_unread(self):
        from agentj.state import State
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp));st.init();m=M.Mail(SimpleNamespace(st=st,agent=object(),stopped=lambda:False,lang='en'))
            cfg=M.validate({**M.blank_config(),'auth':'off','allow':[{'addr':'owner@example.com','role':'owner'}]})
            raw=b'From: owner@example.com\r\nMessage-ID: <one@example.com>\r\n\r\nHello'
            with patch.object(M,'mark_read',return_value=True, create=True) as mark, patch.object(m,'password',return_value='synthetic'):
                with patch.object(m,'deliver',new=AsyncMock(return_value=False)):
                    await m.one(cfg,5,len(raw),raw)
                cfg['mark_processed_read']=False
                raw=raw.replace(b'<one',b'<two')
                with patch.object(m,'deliver',new=AsyncMock(return_value=True)):
                    await m.one(cfg,6,len(raw),raw)
                mark.assert_not_called()


class Downgrade(unittest.TestCase):
    def test_395_only_exact_role_downgrade_avoids_uv(self):
        before=M.validate({**M.blank_config(),'allow':[{'addr':'owner@example.com','role':'owner'}]})
        target={'kind':'mail_set','before':before,'cfg':{'allow':[{'addr':'owner@example.com','role':'guest'}]}}
        self.assertTrue(action_policy.mail_role_downgrade(target))
        for field,value in [('enabled',True),('address','other@example.com'),('auth','off')]:
            bad=copy.deepcopy(target);bad['cfg'][field]=value
            self.assertFalse(action_policy.mail_role_downgrade(bad))
        bad=copy.deepcopy(target);bad['cfg']['allow'][0]['addr']='other@example.com'
        self.assertFalse(action_policy.mail_role_downgrade(bad))
        bad=copy.deepcopy(target);bad['before']['allow'][0]['role']='guest';bad['cfg']['allow'][0]['role']='owner'
        self.assertFalse(action_policy.mail_role_downgrade(bad))

class MarkIdentity(unittest.TestCase):
    def test_397_loopback_imap_marks_only_target_seen(self):
        from mailfake import FakeIMAP
        server=FakeIMAP('owner@example.com','synthetic')
        try:
            raw=b'From: owner@example.com\r\nMessage-ID: <one@example.com>\r\n\r\nHello'
            server.add(raw.decode());server.add(raw.decode().replace('<one','<two'))
            cfg={**M.blank_config(),'address':'owner@example.com','imap':{'host':'127.0.0.1','port':server.port,'security':'plain'}}
            self.assertTrue(M.mark_read(cfg,'synthetic',server.uidvalidity,1,M.id_hash('<one@example.com>')))
            self.assertEqual(server.flags,{1:{'\\Seen'}})
        finally:
            server.close()

    def test_397_epoch_identity_and_store_failure(self):
        raw=b'From: owner@example.com\r\nMessage-ID: <one@example.com>\r\n\r\nHello'
        mid=M.id_hash('<one@example.com>',raw[:2048])
        def client(epoch=b'9', body=raw, status='OK'):
            c=Mock();c.untagged_responses={'UIDVALIDITY':[epoch]};c.select.return_value=('OK',[])
            c.uid.side_effect=[('OK',[(b'header',(b'Message-ID: <other@example.com>\r\n' if body == b'changed' else b'Message-ID: <one@example.com>\r\n')),(b'body',body)]),(status,[])]
            return c
        for c in (client(epoch=b'10'), client(body=b'changed'), client(status='NO')):
            with patch.object(M,'imap_login',return_value=c), self.assertRaises(M.MailError):
                M.mark_read({},'synthetic',9,5,mid)
            if c.untagged_responses['UIDVALIDITY']==[b'10']:
                c.uid.assert_not_called()
        c=client()
        with patch.object(M,'imap_login',return_value=c):
            self.assertTrue(M.mark_read({},'synthetic',9,5,mid))
        self.assertEqual(c.uid.call_args.args,('STORE','5','+FLAGS.SILENT',r'(\Seen)'))
        c.logout.assert_called_once()

class SignedDowngrade(unittest.TestCase):
    def test_395_nonce_signature_and_promotion_are_still_enforced(self):
        import time
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from agentj import controls, wire
        sk=Ed25519PrivateKey.generate();pub=sk.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        st=Mock();st.sign_key.return_value=pub;st.passkey_of_device.return_value=None
        before=M.validate({**M.blank_config(),'allow':[{'addr':'owner@example.com','role':'owner'}]})
        target={'kind':'mail_set','before':before,'cfg':{'allow':[{'addr':'owner@example.com','role':'guest'}]}}
        nonces=controls.Nonces();digest=controls.object_digest('host_write',target)
        n=nonces.issue_mail('device',digest);ts=int(time.time()*1000)
        auth={'n':n,'ts':ts,'sig':wire.b64u(sk.sign(controls.signed_message('channel','device','host_write',n,ts,digest)))}
        self.assertIsNone(controls.check(st,nonces,'channel','device',auth,'host_write',target))
        self.assertIsNotNone(controls.check(st,nonces,'channel','device',auth,'host_write',target))
        changed=copy.deepcopy(target);changed['cfg']['auth']='off'
        self.assertIsNotNone(controls.check(st,nonces,'channel','device',auth,'host_write',changed))
        target['before']['allow'][0]['role']='guest';target['cfg']['allow'][0]['role']='owner'
        digest=controls.object_digest('host_write',target);n=nonces.issue_mail('device',digest)
        auth.update(n=n,sig=wire.b64u(sk.sign(controls.signed_message('channel','device','host_write',n,ts,digest))))
        self.assertEqual(controls.check(st,nonces,'channel','device',auth,'host_write',target),'no_passkey')

from test_p116 import Picker
class EveryNativeRow(Picker):
    def test_403_every_native_fixture_row_has_confirmed_status(self):
        self.set(static=True)
        a,_=self.agent()
        a.learn_picker_rows(['Default (Opus 5.5 (1M context))','Opus 5.5 (1M context)','Fable 5.1','Sonnet 5','Haiku 4.5'])
        asyncio.run(a.refresh_models())
        models=list(a.models_cache)
        self.assertEqual(len(models),len({x['id'] for x in models}))
        for model in models:
            with self.subTest(model=model['id']):
                self.assertIsNone(asyncio.run(a.model_set({'model':model['id'],'effort':None})))
                self.assertTrue(ms.matches(model['id'],ms.key_of(self.tui_()['model'])))

    def test_403_unconfirmed_receipt_is_localized_and_actionable(self):
        a,host=self.agent()
        for lang,lead in [('zh','没切成'),('en','Switch not confirmed')]:
            host.lang=lang
            with patch.object(a,'apply_model',new=AsyncMock(return_value='unconfirmed')), \
                 patch.object(a,'_fresh_native',new=AsyncMock(return_value={'model_name':'Opus 5.5','effort':'medium'})):
                res=asyncio.run(a._model_command('sonnet'))
            self.assertIn(lead,res.text)
            self.assertIn('网页终端' if lang == 'zh' else 'web terminal',res.text)
            self.assertIn('当前显示' if lang == 'zh' else 'Currently displayed',res.text)
            self.assertNotIn('主机上',res.text)
            self.assertNotIn('still',res.text)
            self.assertNotIn('按键已发',res.text)
