"""P503: isolated regressions for the stolen paired-device trust boundary."""
import _hermetic  # noqa: F401
import asyncio
import copy
import hashlib
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import mail, passkey, serve, wire, elevate
from agentj.noise import Keypair
from agentj.state import State
from test_p57_passkey import Authenticator, pk_record



class TrustRoot(unittest.TestCase):
    def test_incomplete_registration_is_rejected(self):
        a = Authenticator()
        with self.assertRaises(passkey.PasskeyError):
            passkey.verify_registration({k:v for k,v in a.register(b'x' * 32).items() if k != 'att'}, b'x' * 32)

    def test_state_cannot_replace_existing_key_without_old_uv(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d)); st.init()
            kp = Keypair.generate()
            st.pair_device(kp.pub, 'fixture', b'k' * 32)
            old, new = Authenticator(), Authenticator()
            self.assertTrue(st.set_passkey(kp.pub, pk_record(old)))
            self.assertFalse(st.set_passkey(kp.pub, pk_record(new)))
            self.assertEqual(st.passkey_of_device(wire.device_id(kp.pub))['id'], wire.b64u(old.id))


class Destination(unittest.TestCase):
    def test_every_credential_destination_change_forgets_old_password(self):
        for leaf, value in [('smtp.host', 'other.example'), ('imap.port', 995),
                            ('smtp.port', 587), ('smtp.security', 'starttls'), ('username', 'other@example.com')]:
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as d:
                st = State(pathlib.Path(d)); st.init()
                h = SimpleNamespace(st=st, lang='zh', agent=None)
                m = mail.Mail(h)
                cfg = mail.blank_config()
                cfg.update(address='a@example.com', username='a@example.com',
                           imap={'host':'imap.example.com', 'port':993, 'security':'ssl'},
                           smtp={'host':'smtp.example.com', 'port':465, 'security':'ssl'})
                cfg = m.set_config(cfg)
                mail.store_secret(st, b'fixture-password', cfg['address'])
                self.assertEqual(mail.load_secret(st, cfg['address']), 'fixture-password')
                new = copy.deepcopy(cfg)
                if '.' in leaf:
                    k, p = leaf.split('.'); new[k][p] = value
                else:
                    new[leaf] = value
                m.set_config(new)
                self.assertIsNone(mail.load_secret(st, cfg['address']))


class NoDowngrade(unittest.TestCase):
    def test_admin_and_secret_without_passkey_are_rejected(self):
        manager = SimpleNamespace(st=SimpleNamespace(passkey_of_device=lambda _:None))
        for kind in ('sudo','secret','login','vshare'):
            self.assertIsNotNone(elevate.Elevator._check_passkey(manager, SimpleNamespace(device='stolen'), {'kind':kind}, {}))


class Registration(unittest.TestCase):
    def test_registration_binds_flags_rp_credential_and_cose(self):
        from test_p57_passkey import cbor
        a=Authenticator(); nonce=b'n'*32
        self.assertEqual(passkey.verify_registration(a.register(nonce),nonce)['id'],wire.b64u(a.id))
        for flags in (0x40,0x41,0x44,0x55):
            with self.subTest(flags=flags), self.assertRaises(passkey.PasskeyError):
                passkey.verify_registration(a.register(nonce,flags=flags),nonce)
        other=Authenticator()
        for field, value in (('id',wire.b64u(other.id)),('spki',wire.b64u(other.spki()))):
            with self.subTest(field=field), self.assertRaises(passkey.PasskeyError):
                passkey.verify_registration({**a.register(nonce),field:value},nonce)
        with self.assertRaises(passkey.PasskeyError):
            passkey.verify_registration({**a.register(nonce),'att':wire.b64u(cbor({'fmt':'none','authData':b'a'*70,'attStmt':{}}))},nonce)

    def test_old_uv_authorizes_exact_new_credential_and_not_another(self):
        with tempfile.TemporaryDirectory() as d:
            st=State(pathlib.Path(d)); st.init(); kp=Keypair.generate(); st.pair_device(kp.pub,'fixture',b'k'*32)
            old,new,other=Authenticator(),Authenticator(),Authenticator()
            self.assertTrue(st.set_passkey(kp.pub,pk_record(old)))
            nonce=b'n'*32; newrec=pk_record(new)
            challenge=passkey.replacement_offer_challenge(nonce,pk_record(old))
            f=old.assert_(challenge,b'0',nonce=b'0')
            bad=passkey.replacement_offer_challenge(b"z"*32,pk_record(old))
            self.assertFalse(st.set_passkey(kp.pub,newrec,replacement=f,challenge=bad))
            self.assertTrue(st.set_passkey(kp.pub,newrec,replacement=f,challenge=challenge))
            self.assertEqual(st.passkey_of_device(wire.device_id(kp.pub))['id'],newrec['id'])
            self.assertFalse(st.set_passkey(kp.pub,pk_record(other),replacement=f,challenge=challenge))


class MailConfigRace(unittest.IsolatedAsyncioTestCase):
    async def test_late_card_after_destination_aba_is_stale_without_changing_cipher_format(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            st=State(pathlib.Path(d));st.init();captured={}
            async def request(card):captured.update(card);return {'result':'pending'}
            m=mail.Mail(SimpleNamespace(st=st,lang='en',agent=None,elevate=SimpleNamespace(request=request)))
            cfg=mail.blank_config();cfg.update(address='fixture@example.com',username='fixture@example.com',
                imap={'host':'imap.example.com','port':993,'security':'ssl'},
                smtp={'host':'smtp.example.com','port':465,'security':'ssl'})
            a=m.set_config(cfg);await m.secret_card()
            b=copy.deepcopy(a);b['smtp']['port']=587;b['smtp']['security']='starttls';m.set_config(b)
            again=m.set_config(a)
            self.assertNotEqual(a['generation'],again['generation'])
            with patch.object(mail,'test_connection') as test:
                self.assertEqual(captured['sink'](bytearray(b'fixture-password'))['why'],'stale')
                test.assert_not_called()
            self.assertIsNone(mail.load_secret(st,a['address']))

    async def test_destination_change_cannot_interleave_secret_store(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            st=State(pathlib.Path(d));st.init();captured={}
            async def request(card):captured.update(card);return {'result':'pending'}
            m=mail.Mail(SimpleNamespace(st=st,lang='en',agent=None,elevate=SimpleNamespace(request=request)))
            cfg=mail.blank_config();cfg.update(address='fixture@example.com',username='fixture@example.com',
                imap={'host':'imap.example.com','port':993,'security':'ssl'},
                smtp={'host':'smtp.example.com','port':465,'security':'ssl'})
            a=m.set_config(cfg);await m.secret_card();b=copy.deepcopy(a);b['smtp']['host']='other.example.com'
            entered=threading.Event();release=threading.Event();original=mail.store_secret
            def paused(*args,**kw):
                entered.set()
                if not release.wait(2):raise RuntimeError('fixture timeout')
                original(*args,**kw)
            with ThreadPoolExecutor(2) as pool, patch.object(mail,'store_secret',side_effect=paused), \
                 patch.object(mail,'test_connection',return_value={'ok':True,'imap':'ok','smtp':'ok'}):
                saving=pool.submit(captured['sink'],bytearray(b'fixture-password'))
                self.assertTrue(entered.wait(2))
                changing=pool.submit(m.set_config,b)
                try:
                    from concurrent.futures import TimeoutError
                    with self.assertRaises(TimeoutError):changing.result(.05)
                finally:release.set()
                saving.result(2);changed=changing.result(2)
            self.assertEqual(changed['smtp']['host'],'other.example.com')
            self.assertIsNone(mail.load_secret(st,changed['address']))


class HostExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_l1 import _owner_host as _host, _ready, Phone
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=State(pathlib.Path(self.tmp.name));self.st.init()
        self.sent=[];self.h=_host(self.st,self.sent);self.ph=Phone(self.st)
        self.auth=Authenticator();self.st.set_passkey(self.ph.pub,pk_record(self.auth))
        self.h.mail=mail.Mail(self.h)
        self.session=_ready(self.h,self.ph);self.session.p33=True


class MailFinalExecution(HostExecution):
    async def test_final_smtp_requires_this_actions_long_press_and_replay_sends_once(self):
        # P551 (#318): the Agent's mail is ② reversible — a signed long-press allow, no Face ID; one send per allow
        from mailfake import FakeSMTP
        smtp=FakeSMTP('agent@example.com','fixture-password'); self.addCleanup(smtp.close)
        cfg=mail.blank_config(); cfg.update(address='agent@example.com',username='agent@example.com',
            imap={'host':'127.0.0.1','port':1,'security':'plain'},
            smtp={'host':'127.0.0.1','port':smtp.port,'security':'plain'},
            allow=[{'addr':'owner@example.com','role':'owner'}])
        mail.save_config(self.st,mail.validate(cfg));mail.store_secret(self.st,b'fixture-password',cfg['address'])
        # P573: mail only to the owner goes with no card now — a recipient off the allowlist keeps this action's one-tap card
        task=asyncio.create_task(self.h.elevate.contacts.agent_send({'to':['colleague@example.com'], 'subject':'fixture', 'body':'hello'}))
        try:
            for _ in range(200):
                if self.h.asks:break
                await asyncio.sleep(.005)
            self.assertTrue(self.h.asks)
            a=next(iter(self.h.asks.values()));card=self.h._ask_msg(a,self.session)
            self.assertNotIn('passkey_required',card);self.assertIn('colleague@example.com',card['summary'])
            self.assertFalse(task.done());self.assertFalse(smtp.sent)
            obj=self.ph.answer(card,True);obj.pop('fa',None)
            await self.h._answer(self.session,obj)
            res=await asyncio.wait_for(task,5)
            self.assertTrue(res['ok'],res);self.assertEqual(len(smtp.sent),1)
            await self.h._answer(self.session,obj)
            self.assertEqual(len(smtp.sent),1)
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)


class MailPhoneUV(HostExecution):
    def authorize(self, kind, cfg=None, *, flags=5):
        from agentj import controls
        from test_controls import sign
        obj={"kind":kind, **({"cfg":cfg} if kind=="mail_set" else {})}
        target=self.h._policy_target(kind,obj)
        n=self.h.nonces.issue_mail(self.ph.did,controls.object_digest("host_write",target))
        # test_controls.sign supports the final nonce only as its generated field, so sign explicitly.
        import time
        ts=int(time.time()*1000);digest=controls.object_digest("host_write",target)
        auth={"n":n,"ts":ts,"sig":wire.b64u(self.ph.sk.sign(controls.signed_message(self.h.channel,self.ph.did,"host_write",n,ts,digest)))}
        ch=passkey.elevate_challenge(self.h.channel,self.ph.did,n,"host_write",n,digest)
        auth["fa"]=self.auth.assert_(ch,b"0",nonce=b"0",flags=flags)
        return auth

    async def test_mail_set_missing_zero_uv_changed_target_and_replay(self):
        before=mail.read_config(self.st);cfg={"address":"fixture@example.com"};r="a"*16
        for auth in (None,self.authorize("mail_set",cfg,flags=1)):
            await self.h.on_mail(self.session,"mail_set",{"r":r,"cfg":cfg,"authorization":auth})
            self.assertFalse(self.sent[-1][1]["ok"]);self.assertEqual(mail.read_config(self.st),before)
        good=self.authorize("mail_set",cfg)
        await self.h.on_mail(self.session,"mail_set",{"r":r,"cfg":{"address":"other@example.com"},"authorization":good})
        self.assertFalse(self.sent[-1][1]["ok"])
        await self.h.on_mail(self.session,"mail_set",{"r":r,"cfg":cfg,"authorization":good})
        self.assertTrue(self.sent[-1][1]["ok"])
        await self.h.on_mail(self.session,"mail_set",{"r":r,"cfg":cfg,"authorization":good})
        self.assertFalse(self.sent[-1][1]["ok"])

    async def test_mail_test_does_not_load_password_or_connect_without_current_uv(self):
        from unittest.mock import patch
        r="b"*16
        for auth in (None,self.authorize("mail_test",flags=1)):
            with patch.object(mail,"load_secret") as load,patch.object(mail,"test_connection") as connect:
                await self.h.on_mail(self.session,"mail_test",{"r":r,"authorization":auth})
                self.assertFalse(self.sent[-1][1]["ok"]);load.assert_not_called();connect.assert_not_called()
        good=self.authorize("mail_test")
        with patch.object(mail,"test_connection") as connect:
            cfg=mail.read_config(self.st);cfg["address"]="changed@example.com";self.h.mail.set_config(cfg)
            await self.h.on_mail(self.session,"mail_test",{"r":r,"authorization":good})
            self.assertFalse(self.sent[-1][1]["ok"]);connect.assert_not_called()

    async def test_mail_uv_requires_unexpired_host_offer_not_just_device_selected_nonce(self):
        from unittest.mock import patch
        good=self.authorize("mail_test")
        with patch("agentj.controls.time.monotonic",return_value=__import__('time').monotonic()+121):
            self.assertIsNotNone(self.h._policy_write(self.session,"mail_test",{"authorization":good}))
        self.h.nonces.mail_offers.clear()
        self.assertIsNotNone(self.h._policy_write(self.session,"mail_test",{"authorization":good}))


class FinalMailPaths(unittest.IsolatedAsyncioTestCase):
    async def test_notes_and_reply_long_press_forward_no_card_and_refuse_denied(self):
        # P551 (#318): the Agent's notes / reply ask a long-press card (no passkey); the owner's own phone forward asks none
        from test_p184_contacts import _Svc
        fixture=_Svc();await fixture.asyncSetUp()
        try:
            fixture.mailbox();h=fixture.host;h.mail=mail.Mail(h)
            h.ask.return_value={"behavior":"deny"}
            res=await fixture.svc.forward(fixture.s,{"to":["owner@example.com"],"turn":7})
            # P549 (#316) / P551 (#318): the remote's own forward tap is the owner's intent — no card, no passkey
            self.assertTrue(res["ok"],res);h.ask.assert_not_awaited()
            cfg=mail.read_config(fixture.st);cfg["enabled"]=True;cfg["allow"]=[{"addr":"owner@example.com","role":"owner"}];cfg=h.mail.set_config(cfg)
            mail.store_secret(fixture.st,b"fixture-password",cfg["address"])
            sent=len(fixture.smtp.sent)
            res=await h.mail.send_notes(["owner@example.com"],"fixture","body",quiet=True)
            self.assertEqual(res["why"],"denied");self.assertFalse(h.ask.await_args.kwargs.get("require_passkey",False))
            state=mail.read_state(fixture.st);state["routes"]["1"]={"to":"owner@example.com","gen":cfg["generation"],"subject":"fixture","msgid":"<fixture@example.com>","refs":""};mail.save_state(fixture.st,state)
            res=await h.mail.reply(1,"body")
            self.assertEqual(res,"denied");self.assertFalse(h.ask.await_args.kwargs.get("require_passkey",False))
            self.assertEqual(len(fixture.smtp.sent),sent)
        finally:fixture.doCleanups()


class RegistrationHost(HostExecution):
    async def test_offer_once_replacement_requires_old_uv_and_preserves_old_on_failure(self):
        import time
        oldid=self.st.passkey_of_device(self.ph.did)["id"]
        for mode in ("missing","uv0","wrong_offer","valid"):
            nonce=__import__('os').urandom(32);self.session.pk_offer=(nonce,time.monotonic())
            new=Authenticator();obj=new.register(nonce)
            if mode!="missing":
                ch=passkey.replacement_offer_challenge(nonce if mode!="wrong_offer" else b"q"*32,pk_record(self.auth))
                obj["replace_fa"]=self.auth.assert_(ch,b"0",nonce=b"0",flags=1 if mode=="uv0" else 5)
            await self.h._pk_reg(self.session,"pk_reg",obj)
            rec=self.st.passkey_of_device(self.ph.did)
            self.assertEqual(rec["id"],wire.b64u(new.id) if mode=="valid" else oldid)
            self.assertIsNone(self.session.pk_offer)
            if mode=="valid":
                await self.h._pk_reg(self.session,"pk_reg",obj)
                self.assertEqual(self.st.passkey_of_device(self.ph.did)["id"],wire.b64u(new.id))


class LegacyEnvelope(unittest.TestCase):
    def test_legacy_password_is_never_opened_or_deleted_implicitly(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            st=State(pathlib.Path(d));st.init();folder=st.root/mail.DIR;folder.mkdir(parents=True,exist_ok=True)
            blob=b"AJM1"+b"x"*64;(folder/mail.SECRET).write_bytes(blob)
            with patch.object(mail,"_kek",side_effect=AssertionError("legacy decryption forbidden")):
                self.assertIsNone(mail.load_secret(st,"fixture@example.com"))
            self.assertTrue(mail.secret_upgrade_required(st));self.assertEqual((folder/mail.SECRET).read_bytes(),blob)


class RealMailEffectsUV(unittest.IsolatedAsyncioTestCase):
    async def test_notes_reply_long_press_and_phone_forward_without_card(self):
        # P551 (#318): Agent notes / reply → one signed long-press allow sends once (no Face ID); the owner's phone forward
        # sends at once without any card (the signed tap is the instruction)
        from test_p184_contacts import _Svc
        from test_l1 import _owner_host as _host,_ready,Phone
        for effect in ("notes","reply"):     # P549 (#316): a phone forward asks nothing (test_p549_mail_cred_cards)
            with self.subTest(effect=effect):
                fixture=_Svc();await fixture.asyncSetUp()
                task=None
                try:
                    fixture.mailbox();fixture.st.sign_key=State.sign_key.__get__(fixture.st,State)
                    sent=[];h=_host(fixture.st,sent)
                    ph=Phone(fixture.st,with_passkey=True);session=_ready(h,ph);session.p33=True
                    h.mail=mail.Mail(h);h.media=fixture.media;h.hist.get=fixture.host.hist.get
                    fixture.svc.host=h
                    cfg=mail.read_config(fixture.st);cfg["enabled"]=True;cfg=h.mail.set_config(cfg)
                    from test_p184_contacts import CODE
                    mail.store_secret(fixture.st,CODE.encode(),cfg["address"])
                    owner=cfg["allow"][0]["addr"]
                    if effect=="notes":
                        call=h.mail.send_notes([owner],"fixture","body",quiet=True)
                    else:
                        state=mail.read_state(fixture.st);state["routes"]["1"]={"to":owner,"gen":cfg["generation"],"subject":"fixture","msgid":"<fixture@example.com>","refs":""};mail.save_state(fixture.st,state)
                        call=h.mail.reply(1,"body")
                    task=asyncio.create_task(call)
                    if effect=="forward":
                        result=await asyncio.wait_for(task,5)
                        self.assertTrue(result.get("ok"),result);self.assertFalse(h.asks)
                        self.assertEqual(len(fixture.smtp.sent),1)
                        continue
                    for _ in range(200):
                        if h.asks:break
                        await asyncio.sleep(.005)
                    self.assertTrue(h.asks)
                    a=next(iter(h.asks.values()));card=h._ask_msg(a,session)
                    self.assertNotIn("passkey_required",card)
                    self.assertFalse(task.done());self.assertFalse(fixture.smtp.sent)
                    obj=ph.answer(card,True);obj.pop("fa",None)
                    await h._answer(session,obj)
                    result=await asyncio.wait_for(task,5)
                    self.assertTrue(result=="sent" if effect=="reply" else result.get("ok"),result)
                    self.assertEqual(len(fixture.smtp.sent),1)
                    await h._answer(session,obj);self.assertEqual(len(fixture.smtp.sent),1)
                finally:
                    if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
                    fixture.doCleanups()


class CustomerServiceMailUV(unittest.IsolatedAsyncioTestCase):
    async def test_both_signed_bot_actions_send_without_another_card(self):
        # P551 (#318): the owner's signed 客服 tap sends the owner's own text / the fixed code mail — ② reversible, no card,
        # no Face ID (with or without a passkey on this remote); the signature still admits it and a replay never resends
        from unittest.mock import AsyncMock, patch
        from test_l1 import _host, _ready, Phone
        from test_controls import sign
        from agentj import controls
        from agentj.bots.owner import Manager
        from agentj.bots.contact import contact
        from mailfake import FakeSMTP
        for op in ('human_email_code', 'human_email_reply'):
            for mode in ('no_passkey', 'passkey'):
                with self.subTest(op=op, mode=mode), tempfile.TemporaryDirectory() as d:
                    st=State(pathlib.Path(d));st.init();sent=[];h=_host(st,sent)
                    ph=Phone(st,with_passkey=mode!='no_passkey');session=_ready(h,ph);session.p33=True
                    manager=Manager(h);store=manager.state()
                    self.addCleanup(store.close)
                    bid=store.create({'slug':'support-fixture','handoff':{'email_enabled':True}})['id']
                    vid,_=store.visitor(bid);contact(store,bid,vid,'visitor@example.com')
                    hid='d'*32;store.db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?,?)',(hid,bid,vid,'e'*32,'pending',store.now()))
                    if op=='human_email_reply':store.db.execute('UPDATE bot_contacts SET verified=1')
                    manager.runtime=SimpleNamespace(engine=lambda:SimpleNamespace(human_reply=AsyncMock(return_value={'text':'safe final reply'})))
                    smtp=FakeSMTP('agent@example.com','fixture-password');self.addCleanup(smtp.close)
                    cfg=mail.blank_config();cfg.update(enabled=True,address='agent@example.com',username='agent@example.com',
                        imap={'host':'127.0.0.1','port':1,'security':'plain'},
                        smtp={'host':'127.0.0.1','port':smtp.port,'security':'plain'})
                    mail.save_config(st,mail.validate(cfg));mail.store_secret(st,b'fixture-password',cfg['address'])
                    req={'op':op,'id':bid,'handoff':hid,'to':'visitor@example.com','send_id':'1'*32,'text':'fixture reply'}
                    obj=sign(ph,'bots_write',{'request':req},{'t':'bots_write','r':'fixture','request':req})
                    with patch.object(controls,'check',wraps=controls.check) as check:
                        await asyncio.wait_for(manager.phone(session,obj),5)
                        check.assert_called_once()
                    self.assertFalse(h.asks)
                    self.assertTrue(sent[-1][1]['ok'],sent[-1]);self.assertEqual(len(smtp.sent),1)
                    await manager.phone(session,obj)
                    self.assertEqual(sent[-1][1]['why'],'replay');self.assertEqual(len(smtp.sent),1)


class IntegratedMailIdentity(unittest.TestCase):
    def test_identity_requires_final_uv_for_owner_and_allowlisted_mail(self):
        from agentj.main_identity import MAIL_LINE
        self.assertIn("每次发送", MAIL_LINE["zh"])
        self.assertIn("审批通行密钥", MAIL_LINE["zh"])
        self.assertNotIn("白名单直接发", MAIL_LINE["zh"])
        self.assertIn("every outgoing message", MAIL_LINE["en"])
        self.assertIn("approval passkey", MAIL_LINE["en"])
        self.assertNotIn("owner / allowlisted: at once", MAIL_LINE["en"])
