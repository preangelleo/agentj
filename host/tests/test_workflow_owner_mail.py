"""Isolated workflow mail contract: real Ed25519/WebAuthn, OS process evidence, local SMTP only."""
import _hermetic  # noqa: F401
import asyncio
import copy
import hashlib
import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ed25519
from agentj import workflow_mail as wm, approvals, contacts, passkey, wire, provenance
from test_p194_owner_direct import _Svc, ME
from test_p57_passkey import Authenticator, pk_record


class Delivery(_Svc):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.set_me()
        self.auth = Authenticator()
        self.ed = ed25519.Ed25519PrivateKey.generate()
        self.st.sign_key = lambda d: self.ed.public_key().public_bytes_raw() if d == 'dev1' else None
        self.st.passkey_of_device = lambda d: pk_record(self.auth) if d == 'dev1' else None
        self.contract = {'workflow_id': 'story', 'root': str(self.work), 'tsha': 'a' * 64}
        self.host.scheduler = SimpleNamespace(workdir=lambda: str(self.work))
        self.e = {'id': 'story', 'dir': str(self.work), 'tsha': 'a' * 64, 'problems': []}
        self.service = wm.Service(self.svc)
        self.src = patch.object(wm, 'source', return_value=self.contract)
        self.src.start(); self.addCleanup(self.src.stop)
        self.find = patch('agentj.tasks.find', return_value=self.e)
        self.find.start(); self.addCleanup(self.find.stop)
        self.journal = {'runs': {'rid': {'status':'running','id':'story','tsha':'a'*64,'instance_id':'instance'}},
                        'on': {'story': {'tsha':'a'*64}}}
        self.load_patch = patch('agentj.tasks.load', return_value=self.journal)
        self.load_patch.start(); self.addCleanup(self.load_patch.stop)
        self.ask.side_effect = self.sign
        self.file = self.work / 'cover.txt'; self.file.write_text('approved cover material')
        self.req = {'to_owner': True, 'to': [], 'cc': [], 'bcc': [], 'subject': 'Published artifact',
                    'body': 'Your published artifacts https://example.test/download', 'format': 'text', 'files': [str(self.file)]}
        self.req['delivery_manifest'] = {'v': 1, 'workflow_id': 'story', 'purpose': 'artifact_delivery',
            'idempotency_key': 'episode-1', 'binding_sha256': 'b' * 64,
            'subject_sha256': hashlib.sha256(self.req['subject'].encode()).hexdigest(),
            'body_sha256': hashlib.sha256(self.req['body'].encode()).hexdigest(), 'format': 'text',
            'attachments': [{'path': str(self.file), 'sha256': hashlib.sha256(self.file.read_bytes()).hexdigest(),
                             'size': self.file.stat().st_size, 'role': 'cover'}]}

    async def sign(self, tool, inp, **kw):
        self.assertEqual(tool, wm.TOOL)
        self.assertTrue(kw['always_ask'] and kw['require_passkey'] and kw['grant_info'])
        summary = inp['summary']; channel = self.st.config()['channel']; rid = 'request-id'
        d = approvals.shown_digest(tool, summary)
        ch = passkey.elevate_challenge(channel, 'dev1', rid, 'sandbox', rid, d)
        fa = self.auth.assert_(ch, b'', nonce=b'')
        return {'behavior': 'allow', 'grant_info': {'device': 'dev1', 'rid': rid, 'channel': channel,
            'summary': summary, 'kind': 'sandbox', 'sign_pub': self.ed.public_key().public_bytes_raw(),
            'sig': wire.b64u(self.ed.sign(approvals.signed_message(channel, 'dev1', rid, 'allow', d))),
            'fa': {k: fa[k] for k in ('id', 'cd', 'ad', 'sig')}}}

    async def grant(self, **kw):
        res = await self.service.control({'op': 'grant', 'workflow_id': 'story', **kw}, os.getpid())
        self.assertTrue(res['ok'], res)
        return res

    async def test_signed_grant_delivers_exact_snapshot_once(self):
        await self.grant()
        res = await self.service.send(self.req, os.getpid())
        self.assertTrue(res['ok'], res); self.assertEqual(res['sent'], [ME])
        self.assertTrue(res['provider_acceptance']); self.assertEqual(len(self.smtp.sent), 1)
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'duplicate')
        self.assertEqual(len(self.smtp.sent), 1); self.ask.assert_awaited_once()

    async def test_tampered_body_and_file_and_recipient_never_send(self):
        await self.grant()
        for field, value in [('body', 'different body'), ('to', ['outsider@example.test']), ('cc', [ME]), ('bcc', [ME]), ('to_owner', False)]:
            req = copy.deepcopy(self.req); req[field] = value
            self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'content_hash' if field == 'body' else 'recipient')
        self.file.write_text('changed bytes')
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'attachment_hash')
        self.assertEqual(self.smtp.sent, [])

    async def test_owner_primary_or_task_or_device_change_invalidates(self):
        await self.grant()
        for mutate in ('owner', 'task', 'device'):
            with self.subTest(change=mutate):
                row = self.service.load()['grants']['story']
                if mutate == 'owner':
                    contacts.local(self.st, {'op': 'owner_set', 'owner_ok': True, 'contact': {'name': 'Changed', 'emails': ['changed@example.test']}})
                elif mutate == 'task': self.contract['tsha'] = 'c' * 64
                else: self.st.sign_key = lambda d: b'x' * 32
                self.assertFalse(self.service.live(row, self.contract))
        self.assertEqual(self.smtp.sent, [])

    async def test_expiry_revoke_and_unsigned_do_not_send(self):
        await self.grant()
        data = self.service.load(); row = data['grants']['story']
        row['core']['expires'] = 0; self.service.save(data)
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'grant_required')
        await self.grant()
        await self.service.control({'op': 'revoke', 'workflow_id': 'story'}, None)
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'grant_required')
        self.assertEqual(self.smtp.sent, [])

    async def test_unknown_smtp_consumes_delivery_and_count(self):
        await self.grant()
        with patch.object(self.svc, 'smtp_send', side_effect=OSError('unknown')):
            res = await self.service.send(self.req, os.getpid())
        self.assertEqual(res['why'], 'send_unknown'); self.assertIsNone(res['provider_acceptance'])
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'duplicate')
        self.assertEqual(self.smtp.sent, [])

    async def test_status_verifies_current_native_source(self):
        await self.grant()
        req = {'op': 'status', 'workflow_id': 'story'}
        self.assertTrue((await self.service.control(req, os.getpid()))['source_verified'])
        self.src.stop()
        self.assertFalse((await self.service.control(req, None))['source_verified'])
        with patch.object(wm, 'source', return_value={**self.contract, 'tsha': 'b'*64}):
            self.assertFalse((await self.service.control(req, os.getpid()))['source_verified'])

    async def test_status_exhaustion_prevents_upload(self):
        await self.grant(max=1)
        self.assertTrue((await self.service.send(self.req, os.getpid()))['ok'])
        res = await self.service.control({'op': 'status', 'workflow_id': 'story'}, None)
        self.assertEqual(res['grant']['status'], 'active')

    async def test_revoke_while_card_waits_cannot_resurrect(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*a, **kw):
            entered.set(); await release.wait(); return await self.sign(*a, **kw)
        self.ask.side_effect = delayed
        pending = asyncio.create_task(self.service.control({'op': 'grant', 'workflow_id': 'story'}, os.getpid()))
        await asyncio.wait_for(entered.wait(), 2)
        await self.service.control({'op': 'revoke', 'workflow_id': 'story'}, None)
        release.set(); self.assertFalse((await pending)['ok'])
        self.assertNotIn('story', self.service.load()['grants'])

    async def test_no_real_source_ignores_owner_main_and_json_forgery(self):
        with patch.object(wm, 'source', return_value=None):
            for fake in ({'kind':'phone','role':'owner'}, {'kind':'scheduled_task','role':'task'}, {'kind':'email','role':'group'}):
                self.req['source'] = fake
                self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'workflow_source')
                self.assertEqual((await self.service.control({'op':'grant','workflow_id':'story','source':fake},os.getpid()))['why'],'workflow_source')
        self.ask.assert_not_awaited(); self.assertEqual(self.smtp.sent, [])

    async def test_actual_kernel_pid_scope_and_expiry(self):
        self.src.stop()
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)')
        self.addAsyncCleanup(self.kill, proc)
        with provenance.task_run() as scope:
            scope['mail_contract'] = self.contract
            scope['mail_admission'] = {'run_id':'rid','instance_id':'instance','research':False,'scheduled':True}
            wm.pin_current(proc); self.host.scheduler.mail_scope = scope
            self.assertEqual(wm.source(self.host, proc.pid), self.contract)
            scope['mail_admission']['research'] = True
            self.assertIsNone(wm.source(self.host, proc.pid))
            scope['mail_admission']['research'] = False
            self.journal['on'].clear()
            self.assertIsNone(wm.source(self.host, proc.pid))
            self.journal['on']['story'] = {'tsha':'a'*64}
            self.journal['runs']['rid']['status'] = 'interrupted'
            self.assertIsNone(wm.source(self.host, proc.pid))
            self.journal['runs']['rid']['status'] = 'running'
            self.assertEqual(wm.source(self.host, proc.pid), self.contract)
            self.assertIsNone(wm.source(self.host, os.getpid()))
            proc.terminate(); await proc.wait()
            self.assertIsNone(wm.source(self.host, proc.pid))
        self.assertIsNone(wm.source(self.host, proc.pid))


    async def test_archive_secret_hidden_in_zip_is_refused(self):
        import io, zipfile
        for member, payload in [('.env', 'safe=fixture'), ('notes.txt', 'password="hunter2hunter"'), ('nested.zip', 'anything')]:
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, 'w') as z: z.writestr(member, payload)
            with self.assertRaises(ValueError): wm.check_archive('materials.zip', blob.getvalue())
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w') as z: z.writestr('notes.txt', 'safe material')
        wm.check_archive('materials.zip', blob.getvalue())

    async def test_account_rebind_invalidates_existing_grant(self):
        await self.grant()
        row = self.service.load()['grants']['story']
        with patch('agentj.cloud.read_cloud', return_value={'api':'https://api.example.test','host_id':'new-host',
                       'tenant':{'slug':'other-owner'},'linked_at':123}):
            self.assertFalse(self.service.live(row, self.contract))

    async def test_contract_change_and_resign_do_not_reset_unknown_dedupe(self):
        await self.grant()
        with patch.object(self.svc, 'smtp_send', side_effect=OSError('unknown')):
            self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'send_unknown')
        self.contract['tsha'] = self.e['tsha'] = 'f' * 64
        await self.grant()
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'duplicate')

    async def test_uv_and_each_signed_field_are_verified(self):
        await self.grant()
        row = self.service.load()['grants']['story']
        for field in ('max','per_day','max_bytes','expires','artifact_root','purpose'):
            changed = copy.deepcopy(row)
            changed['core'][field] = changed['core'][field] + 1 if isinstance(changed['core'][field], int) else 'changed'
            self.assertFalse(self.service.live(changed, self.contract), field)
        changed = copy.deepcopy(row)
        changed['proof']['sig'] = wire.b64u(b'x' * 64)
        self.assertFalse(self.service.live(changed, self.contract))
        other = Authenticator()
        self.st.passkey_of_device = lambda d: pk_record(other)
        self.assertFalse(self.service.live(row, self.contract))

    async def test_artifact_folder_is_separate_from_task_root(self):
        import tempfile
        from _testtmp import TEST_TMP
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as other:
            artifact = __import__('pathlib').Path(other)
            self.file = artifact / 'cover.txt'; self.file.write_text('scoped artifact')
            req = copy.deepcopy(self.req); req['files'] = [str(self.file)]
            req['delivery_manifest']['attachments'] = [{'path':str(self.file),'size':self.file.stat().st_size,
                'sha256':hashlib.sha256(self.file.read_bytes()).hexdigest(),'role':'cover'}]
            await self.grant(artifact_root=str(artifact))
            self.assertTrue((await self.service.send(req, os.getpid()))['ok'])
        for bad in ('/', str(__import__('pathlib').Path.home())):
            with self.assertRaises(ValueError): wm.checked_artifact_root(bad)

    async def test_smtp_success_then_ledger_failure_reports_unknown(self):
        await self.grant()
        original = self.service.save
        count = 0
        def save(data):
            nonlocal count
            count += 1
            if count == 2: raise OSError('disk')
            return original(data)
        with patch.object(self.service, 'save', side_effect=save):
            res = await self.service.send(self.req, os.getpid())
        self.assertEqual(res['why'], 'send_unknown'); self.assertIsNone(res['provider_acceptance'])
        self.assertEqual(len(self.smtp.sent), 1)
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'duplicate')


    async def test_real_elevate_socket_binds_native_child_not_owner_main(self):
        import pathlib
        from agentj.elevate import Elevator
        await self.grant()
        self.src.stop()
        elevator = Elevator.__new__(Elevator)
        elevator.host, elevator.st, elevator.contacts = self.host, self.st, self.svc
        elevator.workflow_mail = self.service
        sock = str(pathlib.Path(self.tmp.name) / 'mail.sock')
        server = await asyncio.start_unix_server(elevator.on_client, path=sock)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        code = ('import sys,socket,json; req=json.loads(sys.stdin.readline()); '
                's=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(sys.argv[1]); '
                's.sendall((json.dumps(req)+"\\n").encode()); print(s.recv(65536).decode())')
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', code, sock,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.addAsyncCleanup(self.kill, proc)
        with provenance.task_run() as scope:
            scope['mail_contract'] = self.contract
            scope['mail_admission'] = {'run_id':'rid','instance_id':'instance','research':False,'scheduled':True}; wm.pin_current(proc); self.host.scheduler.mail_scope = scope
            raw = {**self.req, 't':'mail_send', 'source':{'kind':'phone','role':'owner'}}
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(raw).encode() + b'\n'), 5)
            self.assertEqual(proc.returncode, 0, err)
            self.assertTrue(json.loads(out)['ok'], out)
        self.assertEqual(len(self.smtp.sent), 1)
        # The owner-looking main session cannot authorize a separate OS caller.
        reader, writer = await asyncio.open_unix_connection(sock)
        writer.write(json.dumps(raw).encode() + b'\n'); await writer.drain()
        self.assertEqual(json.loads(await reader.readline())['why'], 'workflow_source')
        writer.close(); await writer.wait_closed()

    async def test_real_cli_manifest_parser_matches_service(self):
        import argparse, pathlib
        from agentj import mail
        p = argparse.ArgumentParser(); mail.add_parser(p.add_subparsers(dest='command'))
        manifest = pathlib.Path(self.tmp.name) / 'delivery.json'; manifest.write_text(json.dumps(self.req['delivery_manifest']))
        body = pathlib.Path(self.tmp.name) / 'body.txt'; body.write_text(self.req['body'])
        args = p.parse_args(['mail','send','--to-owner','--delivery-manifest',str(manifest), '--format','text',
                             '--subject',self.req['subject'],'--body-file',str(body),'--attach',str(self.file)])
        args.json = True
        captured = []
        with patch('agentj.state.State', return_value=self.st), patch('agentj.elevate.client_request',
                side_effect=lambda st, req, **kw: captured.append(req) or {'ok':True}):
            self.assertEqual(mail.cmd(args), 0)
        self.service.snapshot(captured[0], self.contract)
        status = p.parse_args(['mail','workflow-status','--workflow','story'])
        self.assertEqual(status.workflow, 'story')


    async def test_real_host_ask_returns_reverifiable_uv_proof(self):
        from test_l1 import Phone, _host, _ready
        from agentj import policy_registry
        await self.grant()
        core = self.service.load()['grants']['story']['core']
        del self.st.sign_key
        del self.st.passkey_of_device
        before = set(asyncio.all_tasks())
        host = _host(self.st, [])
        phone = Phone(self.st, 'Workflow owner', with_passkey=True)
        session = _ready(host, phone)
        host.agent = SimpleNamespace(kind='codex', native_source=None, status='idle')
        host.agent_cfg = {'dir':str(self.work),'kind':'codex'}
        pending = asyncio.create_task(host.ask(wm.TOOL, {'summary':wm.shown(core)}, batch=False,
            grant_info=True, always_ask=True, require_passkey=True,
            _company_proposal=policy_registry.HOST_CARD))
        for _ in range(100):
            if host.asks: break
            await asyncio.sleep(.005)
        self.assertTrue(host.asks)
        ask = next(iter(host.asks.values()))
        msg = host._ask_msg(ask, session)
        self.assertTrue(msg['passkey_required'])
        await host._answer(session, phone.answer(msg, True))
        ans = await asyncio.wait_for(pending, 2)
        self.assertTrue(wm.proof_ok(self.st, core, ans['grant_info']), ans)
        host.stopping.set()
        tasks = set(asyncio.all_tasks()) - before - {asyncio.current_task()}
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)


    async def test_malformed_artifact_root_is_refused_without_card(self):
        for value in (True, {}, ['bad']):
            res = await self.service.control({'op':'grant','workflow_id':'story','artifact_root':value},os.getpid())
            self.assertFalse(res['ok'], res)
        self.ask.assert_not_awaited()

    async def kill(self, proc):
        if proc.returncode is None: proc.kill(); await proc.wait()
