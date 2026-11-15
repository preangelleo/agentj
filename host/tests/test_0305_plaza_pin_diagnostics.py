"""Real loopback HTTP -> verified community install -> pending upgrade diagnostic, strict pin retained."""
import _hermetic
import asyncio
import http.server
import json
from pathlib import Path
import threading
from unittest import mock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import application_ingress,bundle,cloud,market,market_guard,plaza_host,plaza_trust
from test_plaza_install_card import CardFixture
from market_fixtures import TestKey,URL_PART


class UpgradeJourney(CardFixture):
    def setUp(self):
        super().setUp()
        from agentj import plaza
        gate=mock.patch.object(plaza,'seat_gate',return_value=None);gate.start();self.addCleanup(gate.stop)
        self.review=TestKey();self.publisher=Ed25519PrivateKey.generate()
        self.host.ask_ttl=1
        self.target=self.e.home/'installed/demo-flow'
        self.http=[]
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                envelope=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                status,value=owner.srv(owner.api+self.path,envelope)
                owner.http.append(('POST',self.path))
                raw=json.dumps(value).encode();self.send_response(status)
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                status,raw=owner.srv.get(owner.api+self.path)
                owner.http.append(('GET',self.path))
                self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_http)
        self.api='http://127.0.0.1:'+str(self.server.server_address[1])
        self.srv.download_url=self.api+'/v1/plaza/dl/'+URL_PART
        config=cloud.read_cloud(self.st);config['api']=self.api;cloud.write_cloud(self.st,config)

    def close_http(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def package_at(self,version,reviewed=True,publisher='same'):
        data,manifest=self.package(version=version)
        detail=self.srv.packages['demo-flow']['detail']
        # Community review is not evidence that Agent J published this package.
        detail.update(track='community',certified=reviewed,signature=self.review.sign_package(data,manifest) if reviewed else None)
        if publisher!='missing':
            key=Ed25519PrivateKey.generate() if publisher=='changed' else self.publisher
            fields=plaza_trust.sign(key,manifest['name'],manifest['type'],manifest['version'],bundle.sha256(data),1794614400)
            if publisher=='bad':
                fields['host_sig']=plaza_trust.sign(key,manifest['name'],manifest['type'],manifest['version'],'0'*64,1794614400)['host_sig']
            detail.update(fields)
        return data,manifest

    def request(self):
        self.sent.clear()
        async def go():
            async def call():
                writer=mock.Mock();writer.is_closing.return_value=False
                with application_ingress._enter(self.host,writer,{},lambda:True,'terminal'):
                    return await plaza_host.handle(self.host,{'name':'demo-flow','harness':'codex',
                        'workspace':str(self.target.parent),'replace':True},keyring=self.review.keyring)
            task=asyncio.create_task(call())
            for _ in range(500):
                await asyncio.sleep(.005)
                if self.cards() or task.done():break
            if self.cards():await self.host._app(self.s,self.phone.answer(self.cards()[-1],True))
            return await asyncio.wait_for(task,4)
        return asyncio.run(go())

    def installed_base(self,publisher='same'):
        self.package_at('1.2.0',publisher=publisher)
        result=self.request();self.assertTrue(result['ok'],result);self.assertEqual(len(self.cards()),1)
        self.assertTrue(market.read_installed(self.st)[0]['certified'])
        self.before={str(p.relative_to(self.target)):p.read_bytes() for p in self.target.rglob('*') if p.is_file()}
        self.registry=(self.st.root/'plaza/installed.json').read_bytes()
        self.pin=(self.st.root/'plaza/publishers.json').read_bytes()
        self.sent.clear();self.http.clear()

    def pending_refusal(self,publisher,expected):
        self.package_at('1.3.0',reviewed=False,publisher=publisher)
        result=self.request();self.assertEqual(result['rc'],market.EXIT_SIGNATURE,result)
        self.assertFalse(self.cards());self.assertEqual(market.read_installed(self.st)[0]['version'],'1.2.0')
        self.assertEqual(self.before,{str(p.relative_to(self.target)):p.read_bytes() for p in self.target.rglob('*') if p.is_file()})
        self.assertEqual((self.st.root/'plaza/installed.json').read_bytes(),self.registry)
        self.assertEqual((self.st.root/'plaza/publishers.json').read_bytes(),self.pin)
        self.assertTrue(any(method=='POST' for method,_ in self.http));self.assertTrue(any(method=='GET' for method,_ in self.http))
        text=result['out'];self.assertIn('previously carried an Agent J review signature',text)
        self.assertIn(expected,text)
        self.assertNotIn('officially signed copy',text);self.assertNotIn(self.api,text)
        self.assertNotIn(plaza_trust.b64u(self.publisher.public_key().public_bytes_raw()),text)
        return result

    def test_pending_same_verified_publisher_explains_missing_review_without_relaxing_pin(self):
        self.installed_base();self.pending_refusal('same','verified publisher matches the local record')

    def test_pending_changed_verified_publisher_does_not_claim_it_is_only_waiting_review(self):
        self.installed_base();self.pending_refusal('changed','verified publisher differs from the local record')

    def test_bad_publisher_binding_is_unverifiable_not_same_publisher(self):
        self.installed_base();self.pending_refusal('bad','publisher signature cannot be verified')

    def test_missing_publisher_signature_cannot_infer_identity_or_review_status(self):
        self.installed_base();self.pending_refusal('missing','no verifiable publisher signature')

    def test_unavailable_old_publisher_identity_does_not_guess_it_was_official(self):
        self.installed_base(publisher='missing');self.pending_refusal('same','cannot establish a matching publisher identity')

    def test_new_review_signed_version_still_uses_the_existing_card_and_installs(self):
        self.installed_base();self.package_at('1.3.0')
        result=self.request();self.assertTrue(result['ok'],result);self.assertEqual(len(self.cards()),1)
        self.assertEqual(market.read_installed(self.st)[0]['version'],'1.3.0')
        self.assertEqual((self.st.root/'plaza/publishers.json').read_bytes(),self.pin)
