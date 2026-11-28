"""Native admission API → real CLI subprocess → UDS → Host → loopback Tencent seam."""
import _hermetic  # noqa: F401
import asyncio
import base64
import json
import os
from pathlib import Path
import threading
import unittest
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import test_shared_file_show as file_fixture
from test_p501_wechat import ACCOUNT
from agentj import file_show, owner_broker, provenance, wechat_protocol as protocol
from agentj.wechat import WeChat


class Send(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await file_fixture.NativeFlows.asyncSetUp(self)
        self.received, self.uploads, self.requests, self.key = [], [], [], None
        self.on_upload_url = None
        self.on_upload = None
        self.on_send = None
        self.fail_send = False
        self.fail_upload = False
        test = self
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                test.requests.append(self.path)
                if self.path.endswith('getuploadurl'):
                    body = json.loads(data)
                    test.key = bytes.fromhex(body['aeskey'])
                    test.target = body['to_user_id']
                    if test.on_upload_url:
                        test.on_upload_url()
                    result = {'upload_full_url': protocol.CDN+'/upload'}
                elif self.path.endswith('/upload'):
                    test.uploads.append(protocol.decrypt(data, test.key))
                    if test.fail_upload:
                        self.send_response(500); self.end_headers(); return
                    if test.on_upload:
                        test.on_upload()
                    self.send_response(200)
                    self.send_header('x-encrypted-param', 'fixture-down')
                    self.end_headers()
                    return
                elif self.path.endswith('sendmessage'):
                    test.received.append(json.loads(data)['msg'])
                    if test.on_send:
                        test.on_send()
                    if test.fail_send:
                        self.send_response(500); self.end_headers(); return
                    result = {}
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(result).encode())
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        class Transport:
            def request(_, method, url, headers=None, body=None, timeout=20, limit=0):
                # Official URL validation is still performed by API; the test transport is the sole seam.
                endpoint = f'http://127.0.0.1:{test.server.server_port}'+urllib.parse.urlsplit(url).path
                req = urllib.request.Request(endpoint, data=body, headers=headers or {}, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as reply:
                        return reply.status, {k.lower(): v for k,v in reply.headers.items()}, reply.read()
                except urllib.error.HTTPError as exc:
                    return exc.code, {}, exc.read()
        self.host.wechat = WeChat(self.host, api_factory=lambda account: protocol.API(account, Transport()))
        self.wc = self.host.wechat
        self.wc.store.data.update(account=dict(ACCOUNT), context='fixture-context', generation='fixture-epoch')
        self.wc.error = None
        self.addAsyncCleanup(self.close_server)

    async def close_server(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=2)
        self.wc.store.close()

    async def admit_file(self, path):
        import shlex
        command = f'agentj file send {shlex.quote(str(path))} --to wechat --json'
        inp = {'command': command}
        broker = self.host.peers.xfer.broker
        native = ('fixture-native-session', 'fixture-native-turn')
        broker.register('file-send-call', 'Bash', inp, str(self.work), native=native)
        answer = await broker.execute('Bash', inp, 'file-send-call')
        return answer, broker, native, inp

    async def deliver_file(self, path, *, mutate=None, refused=None):
        import sys
        answer, broker, native, inp = await self.admit_file(path)
        self.assertEqual(answer['behavior'], 'allow', answer)
        env = {**os.environ, 'AGENTJ_ELEVATE_SOCK': str(self.host.elevate.sock_path),
               'AGENTJ_STATE_DIR': str(self.st.root), 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'agentj.cli', 'file', 'send',
                    str(path), '--to', 'wechat', '--json', cwd=str(self.work), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 10)
        if refused:
            self.assertEqual(proc.returncode, 1, err.decode())
            result = json.loads(out)
            self.assertEqual(result['result'], 'failure')
            self.assertEqual(result['why'], refused)
            self.assertEqual(self.requests, [])
            self.assertFalse(self.notices)
            self.assertEqual(self.wc.store.data['counts'].get('send_failed', 0), 0)
            return result
        self.assertEqual(proc.returncode, 0, err.decode())
        queued = json.loads(out)
        self.assertEqual(queued['result'], 'queued')
        self.assertEqual(self.requests, [], 'CLI receipt alone cannot send')
        if mutate:
            mutate()
        broker.complete('file-send-call', True, queued, 'Bash', inp, str(self.work), native=native)
        await asyncio.wait_for(broker.drain(), 8)
        return json.loads(self.notices[-1].removeprefix('FILE_SEND_RESULT: ')) if self.notices else None

    async def test_png_exact_bytes_and_bound_destination(self):
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'sent_to_wechat')
        self.assertTrue(result['platform_accepted']); self.assertFalse(result['client_downloaded'])
        self.assertEqual(self.uploads, [self.image.read_bytes()])
        self.assertEqual(self.target, ACCOUNT['ilink_user_id'])
        self.assertEqual(self.received[0]['to_user_id'], ACCOUNT['ilink_user_id'])
        self.assertEqual(self.received[0]['item_list'][0]['type'], 2)
        self.assertEqual(len(self.received), 1)
        self.assertFalse(self.page.get('media'), 'sending is not implicit page attachment')

    async def test_document_exact_bytes_and_filename(self):
        doc = self.work/'report.txt'; doc.write_text('An ordinary short report.\n')
        result = await self.deliver_file(doc)
        self.assertEqual(result['result'], 'sent_to_wechat')
        self.assertEqual(self.uploads, [doc.read_bytes()])
        item = self.received[0]['item_list'][0]
        self.assertEqual(item['type'], 4)
        self.assertEqual(item['file_item']['file_name'], doc.name)

    async def receive_owner(self, **fields):
        from unittest.mock import AsyncMock
        msg = {'message_type': 1, 'from_user_id': ACCOUNT['ilink_user_id'], 'message_id': 47,
               'context_token': 'incoming-context', 'item_list': [{'type': 1, 'text_item': {'text': 'Send my file back'}}],
               **fields}

        def accept(session, *_args, **_kwargs):
            self.send.source = session.provenance
            self.send.wechat_file_binding = getattr(session, 'wechat_file_binding', None)
            return self.send

        with patch.object(self.host, '_accept', new=AsyncMock(side_effect=accept)):
            await self.wc.receive(self.wc.api_factory(ACCOUNT), msg, self.wc.store.data['generation'])

    async def test_wechat_origin_uses_bound_incoming_context(self):
        await self.receive_owner()
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'sent_to_wechat')
        self.assertEqual(self.uploads, [self.image.read_bytes()])
        self.assertEqual(self.target, ACCOUNT['ilink_user_id'])
        self.assertEqual(self.received[0]['to_user_id'], ACCOUNT['ilink_user_id'])
        self.assertEqual(self.received[0]['context_token'], 'incoming-context')
        self.assertFalse(self.page.get('media'))

    async def test_wechat_origin_unknown_send_is_not_retried_as_reply_attachment(self):
        await self.receive_owner()
        doc = self.work / '演示稿 终版.pdf'
        doc.write_bytes(b'%PDF-1.4\nsynthetic\n')
        self.fail_send = True
        sent = await self.deliver_file(doc)
        self.assertEqual(sent['result'], 'failure')
        self.assertTrue(sent['delivery_unknown'])
        self.assertEqual(len(self.uploads), 1)
        self.fail_send = False

        record = {'generation': self.wc.store.data['generation'], 'turn': self.send.turn}
        await self.wc.answer_main(record, f'已发送，请查收。[报告](<{doc}>)。备用 https://example.com/download', False)
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual([msg['item_list'][0]['type'] for msg in self.received], [4, 1])
        reply = self.received[-1]['item_list'][0]['text_item']['text']
        self.assertIn('尚未确认', reply)
        self.assertIn('https://example.com/download', reply)
        self.assertNotIn('已发送，请查收', reply)
        self.assertEqual(record['delivery'], 'unknown')

    async def test_wechat_origin_keeps_other_reply_attachments(self):
        await self.receive_owner()
        first = self.work / '第一份报告.pdf'
        second = self.work / '另一份报告.pdf'
        first.write_bytes(b'%PDF-1.4\nfirst\n')
        second.write_bytes(b'%PDF-1.4\nsecond\n')
        self.assertEqual((await self.deliver_file(first))['result'], 'sent_to_wechat')

        record = {'generation': self.wc.store.data['generation'], 'turn': self.send.turn}
        await self.wc.answer_main(record, f'[第一份](<{first}>) [另一份](<{second}>)', False)
        items = [msg['item_list'][0] for msg in self.received]
        self.assertEqual([item['type'] for item in items], [4, 4, 1])
        self.assertEqual([item['file_item']['file_name'] for item in items[:2]], [first.name, second.name])
        self.assertEqual(self.uploads, [first.read_bytes(), second.read_bytes()])
        self.assertEqual(record['delivery'], 'sent')

    async def test_wechat_origin_cannot_attach_to_page(self):
        await self.receive_owner()
        inp = {'command': f'agentj file show {self.image} --json'}
        broker = self.host.peers.xfer.broker
        broker.register('show-only', 'Bash', inp, str(self.work), native=('session', 'turn'))
        answer = await broker.execute('Bash', inp, 'show-only')
        self.assertEqual(answer['behavior'], 'deny')
        self.assertIn('同一个绑定微信', answer['message'])
        self.assertFalse(self.page.get('media'))

    async def test_reference_item_has_no_file_admission(self):
        await self.receive_owner(item_list=[{'type': 1, 'text_item': {'text': 'Send my file back'},
                                            'ref_msg': {'svr_id': 'fixture-original'}}])
        answer, _broker, _native, _inp = await self.admit_file(self.image)
        self.assertEqual(answer['behavior'], 'deny')
        self.assertIn('转发', answer['message'])
        self.assertEqual(self.requests, [])

    async def test_wechat_origin_binding_cannot_be_refreshed_at_admission(self):
        await self.receive_owner()
        self.wc.store.data.update(account={**ACCOUNT, 'ilink_user_id': 'other-owner'}, generation='new-generation',
                                  context='another-context')
        answer, _broker, _native, _inp = await self.admit_file(self.image)
        self.assertEqual(answer['behavior'], 'deny')
        self.assertEqual(self.requests, [])

    async def test_wechat_source_marker_alone_cannot_use_current_binding(self):
        self.send.source = provenance.wechat_owner()
        answer, _broker, _native, _inp = await self.admit_file(self.image)
        self.assertEqual(answer['behavior'], 'deny')
        self.assertEqual(self.requests, [])

    async def test_wechat_origin_context_changes_before_cdn_upload(self):
        await self.receive_owner()
        self.on_upload_url = lambda: self.wc.store.data.update(context='replacement-context')
        result = await self.deliver_file(self.image)
        self.assertEqual(result['why'], 'binding_changed')
        self.assertIn('发一条新消息', result['hint'])
        self.assertFalse(result.get('delivery_unknown', False))
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.received, [])

    async def test_wechat_origin_old_response_cannot_clear_replacement_context_error(self):
        await self.receive_owner()
        self.on_send = lambda: self.wc.store.data.update(context='replacement-context', context_stale=True)
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'sent_to_wechat')
        self.assertEqual(self.received[0]['context_token'], 'incoming-context')
        self.assertTrue(self.wc.store.data['context_stale'])

    async def test_binding_changes_during_upload_never_send_to_new_account(self):
        def change():
            self.wc.store.data.update(generation='new-epoch', account={**ACCOUNT, 'ilink_user_id': 'other-owner'})
        self.on_upload = change
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'failure')
        self.assertEqual(self.received, [])
        self.assertEqual(len(self.uploads), 1)

    async def test_context_changes_during_upload_stop_before_message(self):
        self.on_upload = lambda: self.wc.store.data.update(context='different-context')
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'failure'); self.assertEqual(self.received, [])

    async def test_http_unknown_result_is_not_retried(self):
        self.fail_send = True
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'failure')
        self.assertTrue(result['delivery_unknown'])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(sum(x.endswith('sendmessage') for x in self.requests), 1)

    async def test_guest_source_cannot_send(self):
        result = await self.deliver_file(self.image, mutate=lambda: setattr(self.send, 'source', provenance.email_guest('synthetic@example.test')))
        self.assertEqual(result['result'], 'failure'); self.assertEqual(self.requests, [])

    async def test_known_secret_in_raw_file_content_does_not_upload(self):
        secret = 'knownrawfixturecredentialvalue'
        (self.work/'.env').write_text('CUSTOM_SECRET='+secret+'\n')
        doc = self.work/'report.txt'; doc.write_text('Nothing executable: '+secret[:8]+'\n')
        result = await self.deliver_file(doc)
        self.assertEqual(result['result'], 'failure'); self.assertEqual(self.requests, [])
        self.assertNotIn(secret[:8], json.dumps(result))

    async def test_credential_file_does_not_upload(self):
        doc = self.work/'.env'; doc.write_text('CUSTOM_SECRET=synthetic-only-value\n')
        answer, _broker, _native, _inp = await self.admit_file(doc)
        self.assertEqual(answer['behavior'], 'deny')
        self.assertEqual(answer['reason'], 'off_list_credential')
        self.assertEqual(self.requests, [])

    async def test_outside_root_does_not_upload(self):
        doc = self.root/'outside.txt'; doc.write_text('outside fixture')
        result = await self.deliver_file(doc)
        self.assertEqual(result['result'], 'failure'); self.assertEqual(self.requests, [])

    async def test_native_queued_request_binding_change_before_completion_refuses(self):
        result = await self.deliver_file(self.image, mutate=lambda: self.wc.store.data.update(generation='changed'))
        self.assertEqual(result['result'], 'failure')
        self.assertEqual(self.requests, [])

    async def test_symlink_file_does_not_upload(self):
        link = self.work/'linked.png'; link.symlink_to(self.image)
        result = await self.deliver_file(link)
        self.assertEqual(result['result'], 'failure')
        self.assertEqual(self.requests, [])

    async def test_over_25MiB_document_does_not_upload(self):
        doc = self.work/'large.txt'
        with doc.open('wb') as stream:
            stream.write(b'A'*8192)
            stream.truncate(protocol.MAX_MEDIA+1)
        result = await self.deliver_file(doc, refused='too_big')
        self.assertEqual(result['max_bytes'], protocol.MAX_MEDIA)
        self.assertIn('25 MiB', result['hint'])

    async def test_unsupported_type_is_rejected_by_the_cli(self):
        doc = self.work / '演示稿.pptx'
        doc.write_text('This is text, not a presentation.')
        result = await self.deliver_file(doc, refused='type')
        self.assertIn('unsupported', result['hint'])

    async def test_raw_unicode_secret_inside_binary_file_does_not_upload(self):
        secret = '日本語の合成secretvalue'
        (self.work/'.env').write_text('CUSTOM_SECRET='+secret+'\n')
        self.image.write_bytes(self.image.read_bytes()+secret.encode())
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'failure')
        self.assertEqual(self.requests, [])
        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))

    async def test_raw_unicode_secret_path_rejected_before_cli(self):
        secret = '日本語の合成secretvalue'
        (self.work/'.env').write_text('CUSTOM_SECRET='+secret+'\n')
        path = self.work/(secret+'.png'); path.write_bytes(self.image.read_bytes())
        answer, _broker, _native, _inp = await self.admit_file(path)
        self.assertEqual(answer['behavior'], 'deny')
        self.assertEqual(self.requests, [])
        self.assertNotIn(secret, json.dumps(answer, ensure_ascii=False))

    async def test_incomplete_secret_source_refuses_before_cli(self):
        (self.work/'.env').mkdir()
        answer, _broker, _native, _inp = await self.admit_file(self.image)
        self.assertEqual(answer['behavior'], 'deny')
        self.assertEqual(self.requests, [])


    async def test_show_admission_does_not_authorize_send_cli(self):
        import sys
        inp = {'command': f'agentj file show {self.image} --json'}
        broker = self.host.peers.xfer.broker
        broker.register('show-only', 'Bash', inp, str(self.work), native=('session', 'turn'))
        self.assertEqual((await broker.execute('Bash', inp, 'show-only'))['behavior'], 'allow')
        env = {**os.environ, 'AGENTJ_ELEVATE_SOCK': str(self.host.elevate.sock_path),
               'AGENTJ_STATE_DIR': str(self.st.root), 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'agentj.cli', 'file', 'send',
                    str(self.image), '--to', 'wechat', '--json', cwd=str(self.work), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _err = await asyncio.wait_for(proc.communicate(), 10)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(out)['result'], 'failure')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.notices, [])

    async def test_native_policy_denial_remains_authoritative(self):
        from unittest.mock import AsyncMock
        with patch.object(self.host, 'ask', new=AsyncMock(return_value={'behavior': 'deny', 'message': 'native-ceiling'})) as policy:
            answer, _broker, _native, _inp = await self.admit_file(self.image)
        self.assertEqual(answer['behavior'], 'deny')
        policy.assert_awaited_once()
        self.assertEqual(self.requests, [])

    async def test_current_page_media_count_ceiling_is_preserved(self):
        from agentj import media
        self.page['media'] = [{'mid': str(n), 'ref': f'old-{n}', 'bytes': 1} for n in range(media.MAX_ITEMS)]
        result = await self.deliver_file(self.image)
        self.assertEqual(result['why'], 'too_many')
        self.assertEqual(self.requests, [])


    async def test_queued_then_expired_native_execution_emits_no_send(self):
        result = await self.deliver_file(self.image, mutate=self.host.peers.xfer.end_execution)
        self.assertIsNone(result, 'an expired original native tool cannot create a new Agent notice')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.notices, [])


    async def test_unknown_cdn_upload_result_is_not_retried(self):
        self.fail_upload = True
        result = await self.deliver_file(self.image)
        self.assertEqual(result['result'], 'failure')
        self.assertEqual(self.received, [])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(sum(path.endswith('/upload') for path in self.requests), 1)



class Intent(unittest.TestCase):
    def test_show_intent_cannot_authorize_send_or_arbitrary_destination(self):
        show = owner_broker.parse('Bash', {'command': 'agentj file show report.txt --json'}, '/work')
        send = owner_broker.parse('Bash', {'command': 'agentj file send report.txt --to wechat --json'}, '/work')
        self.assertIsNotNone(send)
        self.assertNotEqual(owner_broker.key(show), owner_broker.key(send))
        from types import SimpleNamespace as NS
        broker = owner_broker.Broker(NS(execution=NS(live=True, send=object())))
        broker.calls['show'] = {'request': show, 'admitted': True, 'check': lambda: True, 'ticket': 'fixture'}
        self.assertEqual(broker.receipt(send)['result'], 'failure')
        for command in ('agentj file send report.txt --to wechat --receiver other',
                        'agentj file send report.txt --to https://example.test',
                        'agentj file send report.txt --to telegram',
                        'agentj file send a.txt b.txt --to wechat'):
            self.assertIsNone(owner_broker.parse('Bash', {'command': command}, '/work'))
