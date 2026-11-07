"""Offline synthetic controls; sandbox tests use the real bwrap binary."""
import _hermetic
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from agentj.bots.store import BotError, Store
from agentj.bots.tools import ToolRegistry
from agentj.bots.script_tools import definition, execute, HostBroker
from agentj.bots import network


def manifest(code, **kw):
    return dict(kind='script',name='script_sample',description='Synthetic test',
                code=code,sha256=hashlib.sha256(code.encode()).hexdigest(),
                parameters={'type':'object','properties':{},'additionalProperties':False},
                response_fields=['ok'],**kw)


class ScriptTests(unittest.TestCase):
    def run_code(self, code, args=None, **kw):
        return execute(definition(manifest(code, **kw), []), args or {})

    def test_real_bwrap_positive_and_private_scratch(self):
        self.assertEqual(self.run_code("import json,os; open('ok','w').write('test'); print(json.dumps({'ok':os.getuid()==65534 and open('ok').read()=='test'}))"), {'ok':True})

    def test_canaries_hidden_environment_keys_roots_and_proc(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            paths=[str(Path(tmp)/n) for n in ('master','sibling','key')]
            for p in paths:Path(p).write_text('synthetic-only')
            code="import json,os; paths="+repr(paths+['/proc/self/environ','/etc/passwd','/work'])+"; print(json.dumps({'ok':not any(os.path.exists(p) for p in paths) and not any(k in os.environ for k in ['SCRIPT_TEST_KEY','HTTP_PROXY','SSH_AUTH_SOCK'])}))"
            with patch.dict(os.environ, {'SCRIPT_TEST_KEY':'synthetic-only','HTTP_PROXY':'synthetic-only','SSH_AUTH_SOCK':'synthetic-only'}):
                self.assertEqual(self.run_code(code), {'ok':True})

    def test_code_readonly_and_no_direct_network(self):
        code='''import json,socket
ok=False
try:
 open('/code.py','w').write('changed')
except OSError: ok=True
s=socket.socket();s.settimeout(.1)
try:
 s.connect(('127.0.0.1',9)); ok=False
except OSError: pass
print(json.dumps({'ok':ok}))'''
        self.assertEqual(self.run_code(code), {'ok':True})

    def test_timeout_with_child_process(self):
        start=time.monotonic()
        with self.assertRaisesRegex(BotError,'script_timeout_unknown'):
            self.run_code('import os,time\nif os.fork()==0: time.sleep(60)\nelse: time.sleep(60)', timeout=1)
        self.assertLess(time.monotonic()-start,4)

    def test_flood_is_bounded(self):
        with self.assertRaisesRegex(BotError,'script_output_truncated'):
            self.run_code("while True: print('x'*4096,flush=True)")

    def test_invalid_output(self):
        with self.assertRaisesRegex(BotError,'invalid_script_output'):
            self.run_code("print('not JSON')")

    def test_changed_code_rejected(self):
        d=manifest("print('{}')");d['code']+='# changed'
        with self.assertRaisesRegex(BotError,'script_digest_mismatch'):definition(d,[])

    def test_paths_symlinks_and_network_registration_refused(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            link=Path(tmp)/'code';link.symlink_to('missing')
            for field,value in [('code',str(link)),('visible_roots',[str(link)]),('network_domains',['example.com']),('path',str(link)),('level','read')]:
                d=manifest("print('{}')");d[field]=value
                with self.subTest(field=field),self.assertRaises(BotError):definition(d,[])

    def test_arguments_are_json_data(self):
        d=manifest('import json,sys; print(json.dumps({"ok":json.load(sys.stdin)["text"]}))')
        d['parameters']['properties']['text']={'type':'string','maxLength':200}
        value='; touch /scratch/injected $(id) --help'
        self.assertEqual(execute(definition(d,[]),{'text':value}),{'ok':value})
        with self.assertRaisesRegex(BotError,'invalid_args'):execute(definition(d,[]),{'unknown':value})

    def test_default_disabled_unknown_tool_and_unsigned_registration(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            store=Store(Path(tmp)/'bots')
            try:
                bid=store.create({'slug':'script-shop'})['id'];reg=ToolRegistry(store,bid)
                self.assertFalse(definition(manifest("print('{}')"),[])['enabled'])
                with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):reg.save(manifest("print('{}')"))
                with self.assertRaisesRegex(BotError,'unknown_tool'):reg.load('missing')
                self.assertEqual(reg.offered(),[])
            finally:store.close()

    def test_signed_registration_still_requires_uv(self):
        from agentj.bots.owner import Manager
        from agentj import provenance
        class Host:
            allowed=False
            calls=[]
            async def ask(self,action,payload,**options):
                self.calls.append((action,payload,options))
                return {'behavior':'allow' if self.allowed else 'deny'}
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            store=Store(Path(tmp)/'bots')
            try:
                bid=store.create({'slug':'script-shop'})['id']
                manager=object.__new__(Manager);manager.host=Host();manager.store=store
                req={'op':'tool_save','id':bid,'tool':manifest("print('{}')")}
                async def request():
                    with provenance.paired_request(lambda:True):return await manager.write(req)
                with self.assertRaisesRegex(BotError,'owner_approval_required'):asyncio.run(request())
                self.assertEqual(ToolRegistry(store,bid).offered(),[])
                self.assertFalse((store.directory(bid)/'tools'/'script_sample.json').exists())
                manager.host.allowed=True
                # Existing owner0342/b3fa gate keeps adapters pending even after real UV.
                with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):asyncio.run(request())
                self.assertFalse((store.directory(bid)/'tools'/'script_sample.json').exists())
                action,payload,options=manager.host.calls[-1]
                self.assertEqual(action,'company.write')
                self.assertTrue(options['require_passkey']);self.assertFalse(options['batch'])
                self.assertEqual(len(payload['digest']),64)
            finally:store.close()

    def test_registry_real_script_approval_consumption_and_filter(self):
        from agentj import provenance
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            store=Store(Path(tmp)/'bots')
            try:
                bid=store.create({'slug':'script-shop'})['id'];vid,_=store.visitor(bid)
                reg=ToolRegistry(store,bid)
                async def register():
                    with provenance.paired_request(lambda:True):
                        reg.save(manifest('import json; print(json.dumps({"ok":"done","undeclared":"private"}))',enabled=True))
                with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):asyncio.run(register())
                path=store.directory(bid)/'tools'/'script_sample.json'
                self.assertFalse(path.exists())
                # A legacy descriptor on disk cannot bypass the same pending gate.
                path.write_text(json.dumps(manifest('print(\'{}\')',enabled=True)))
                path.chmod(0o600)
                self.assertEqual(reg.offered(),[])
                with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):reg.execute(vid,'r','script_sample',{})
                with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):reg.approval(vid,'r','script_sample',{})
                with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):asyncio.run(reg.execute_async(vid,'r','script_sample',{},'old-approval'))
            finally:store.close()

    def test_broker_unknown_name_and_cross_domain(self):
        class Registry:
            def load(self,name):raise AssertionError('must reject before registry lookup')
        with self.assertRaisesRegex(BotError,'unknown_broker_tool'):HostBroker(Registry(),[]).execute('v','r','missing',{})
        with self.assertRaisesRegex(BotError,'domain_not_allowed'):network.request('https://other.example/',domains=['allowed.example'])

    def test_loopback_broker_never_connects(self):
        # Real listening loopback canary, never an external service.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(.1)
            with patch.object(network.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
                with self.assertRaisesRegex(BotError,'unsafe_address'):network.request('https://allowed.example/',domains=['allowed.example'])
            with self.assertRaises(socket.timeout):listener.accept()

    def test_ip_and_metadata_refused(self):
        for address in ('127.0.0.1','169.254.169.254','::1'):
            with self.subTest(address=address),patch.object(network.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]):
                with self.assertRaisesRegex(BotError,'unsafe_address'):network.request('https://allowed.example/',domains=['allowed.example'])

    def test_redirect_refused_before_body(self):
        # Protocol unit control supplements real namespace/loopback tests.
        class Connection:
            def __init__(self,*a):pass
            def request(self,*a,**k):pass
            def getresponse(self):return type('Response',(),{'status':302})()
            def close(self):pass
        with patch.object(network.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]),patch.object(network,'PinnedHTTPS',Connection):
            with self.assertRaisesRegex(BotError,'upstream_http_302'):network.request('https://allowed.example/',domains=['allowed.example'])

if __name__=='__main__':unittest.main()
