"""Only a Host-verified phone-chat CLI file request receives the 0.30.35 validation scope."""
import _hermetic
import asyncio
import contextlib
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

from agentj import approval_material, approval_review, compose, elevate, provenance, wire
from test_f17 import answer
import test_secret_card_hotfix_20261110 as fixture


class SecretValidation(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.SecretCardAfterTurn.setUp
    of = fixture.SecretCardAfterTurn.of
    wait_for = fixture.SecretCardAfterTurn.wait_for

    async def asyncSetUp(self):
        self.el = self.host.elevate
        await self.el.start()
        source = provenance.phone()
        turn = self.host.hist_add({'k':'phone', 'dev':self.s.device, 'text':'Set up the test calendar'}, '', 'open')
        self.origin = approval_review.remember(self.host, turn, source)
        self._turn(source, turn['id'], self.origin)

    async def asyncTearDown(self):
        self.el.cancel_all('gone')
        await self.el.stop()

    def _turn(self, source, n, origin=None):
        send = compose.Send(self.s.device if origin else 'system', 'calendar', n, source=source)
        if origin:
            send.approval_origin = origin
        else:
            send.hook_notice = 'calendar'
        self.host.cur_turn = n
        self.host.agent.cur_send = send
        approval_material.turn_start(self.host, send)

    def req(self, **extra):
        return {'t':'secret', 'name':'TEST_ONLY', 'dest':'file:' + str(self.d / 'value'),
                'purpose':'calendar', 'early':True, 'wait':0.05, **extra}

    async def wire_card(self, *, verified=True, **extra):
        # Real framing and normalization. Exact kernel/process proof is covered separately by the bwrap test.
        source = (self.host.agent, self.origin) if verified else None
        with patch.object(approval_material, 'verified_phone_chat', return_value=source):
            r, w = await asyncio.open_unix_connection(str(self.el.sock_path))
            try:
                w.write((json.dumps(self.req(**extra)) + '\n').encode())
                await w.drain()
                receipt = json.loads(await asyncio.wait_for(r.readline(), 5))
                self.assertEqual(receipt['t'], 'card')
                return next(m for m in self.of('elev') if m['id'] == receipt['id'])
            finally:
                w.close()
                await w.wait_closed()

    async def internal_card(self, **extra):
        n = len(self.of('elev'))
        spec = elevate.norm_secret(self.req(), self.st.root)
        with approval_review.origin_scope(self.origin):
            task = asyncio.create_task(self.el.request(dict(spec, **extra)))
        shown = await self.wait_for('elev', n + 1)
        return task, shown

    async def wait_before_save(self, shown, change=None):
        card = self.el.cards[shown['id']]
        entered, finish = threading.Event(), threading.Event()
        def verify(*_):
            entered.set()
            return {'verify':'ok'} if finish.wait(5) else {'verify':'fail'}
        # CLI HTTP is post-commit (.34). Exercise the actual pre-commit window after signing.
        if card.get('_chat_input') is not None:
            synced = elevate.diskq.fsync
            def sync(fd):
                synced(fd)
                verify()
            hold = patch.object(elevate.diskq, 'fsync', side_effect=sync)
            http = patch.object(elevate, 'verify_secret', return_value={'verify':'ok'})
        else:
            hold = patch.object(elevate, 'verify_secret', side_effect=verify)
            http = contextlib.nullcontext()
        with hold, http:
            try:
                await self.host._app(self.s, answer(self.ph, shown, True, 'synthetic-calendar'))
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self._turn(provenance.system(), 47)
                if change is not None:
                    change(card)
            finally:
                finish.set()
            return await asyncio.wait_for(asyncio.shield(card['fut']), 5)

    async def test_verified_chat_survives_wait_before_and_during_validation(self):
        shown = await self.wire_card(verify_url='https://example.invalid/calendar')
        self._turn(provenance.system(), 46)
        result = await self.wait_before_save(shown)
        self.assertEqual(result['result'], 'saved')
        self.assertEqual((self.d / 'value').read_bytes(), b'synthetic-calendar')

    async def test_two_env_keys_share_the_same_file(self):
        for exists in (False, True):
            path = self.d / ('present' if exists else 'new')
            if exists:
                path.write_text('KEEP=old\n')
            cards = [await self.wire_card(name=k, dest='env:'+str(path)+'#'+k, verify_url='https://example.invalid/'+k) for k in ('ID', 'TOKEN')]
            pending = [self.el.cards[m['id']]['fut'] for m in cards]
            with patch.object(elevate, 'verify_secret', return_value={'verify':'ok'}):
                await asyncio.gather(*(self.host._app(self.s, answer(self.ph, m, True, m['name'])) for m in cards))
                results = await asyncio.gather(*pending)
            self.assertEqual([r['result'] for r in results], ['saved', 'saved'])
            self.assertCountEqual(path.read_text().splitlines(), (['KEEP=old'] if exists else []) + ['ID=ID', 'TOKEN=TOKEN'])

    async def test_invalidated_destination_and_invalid_input_never_call_verifier(self):
        for existing in (False, True):
            path = self.d / str(existing)
            value = 'valid' if existing else 'invalid\nvalue'
            m = await self.wire_card(dest='env:'+str(path)+'#KEY', verify_url='https://example.invalid/me')
            card = self.el.cards[m['id']]
            if existing:
                path.write_text('KEY=owner-choice\n')
            with patch.object(elevate, 'verify_secret', return_value={'verify':'ok'}) as verifier:
                await self.host._app(self.s, answer(self.ph, m, True, value))
                self.assertNotEqual((await card['fut'])['result'], 'saved')
                verifier.assert_not_called()
            if existing:
                self.assertEqual(path.read_text(), 'KEY=owner-choice\n')
            else:
                self.assertFalse(path.exists())

    async def test_concurrent_file_operations_see_committed_target(self):
        cards = [await self.wire_card(purpose=purpose) for purpose in ('first', 'second')]
        pending = [self.el.cards[m['id']]['fut'] for m in cards]
        await asyncio.gather(*(self.host._app(self.s, answer(self.ph, m, True, m['purpose'])) for m in cards))
        results = await asyncio.gather(*pending)
        self.assertCountEqual([r['result'] for r in results], ['saved','unknown'])
        saved, = [m['purpose'] for m, r in zip(cards, results) if r['result'] == 'saved']
        self.assertEqual((self.d/'value').read_text(), saved)

    async def test_stop_while_pairing_registry_busy(self):
        m = await self.wire_card()
        card = self.el.cards[m['id']]
        with self.st.devices_lock():
            await self.host._app(self.s, answer(self.ph, m, True, 'synthetic'))
            await asyncio.sleep(0.05)
            self.assertFalse(card['fut'].done())
            self.el.cancel_all('stopped')
        self.assertNotEqual((await card['fut'])['result'], 'saved')
        self.assertFalse((self.d/'value').exists())

    async def test_stop_after_committed_replacement_keeps_saved_receipt(self):
        m = await self.wire_card()
        card = self.el.cards[m['id']]
        async def after_commit(*_):
            self.assertTrue(card.get('_chat_committed'))
            self.el.cancel_all('stopped')
        with patch.object(self.el, '_reload_saved_env', side_effect=after_commit):
            await self.host._app(self.s, answer(self.ph, m, True, 'synthetic'))
            self.assertEqual((await card['fut'])['result'], 'saved')
        self.assertEqual((self.d/'value').read_text(), 'synthetic')

    async def test_chat_display_budgets_and_verifier_delivery(self):
        purpose = '🌿' * 143 + ' password=x'
        one = await self.wire_card(purpose=purpose, verify_url='https://example.invalid/v1/models/{value}?q={value}')
        two = await self.wire_card(verify_url='https://example.invalid/v1/models/lookup?q={value}')
        self.assertLessEqual(wire.units(one['purpose']), 300)
        self.assertEqual(one['purpose'], one['reason'])
        self.assertNotIn('password=x',one['purpose'])
        self.assertNotIn('/v1/models/', one['verify'])
        self.assertIn('header Authorization', one['verify'])
        self.assertIn('URL path', one['verify'])
        self.assertIn('URL query #1', one['verify'])
        self.assertNotEqual(one['verify'], two['verify'])
        for card in (one,two):
            await self.host._app(self.s, answer(self.ph, card, False))

    async def test_optional_http_runs_after_storage_and_reports_actual_status(self):
        path = self.d / 'value'
        shown = await self.wire_card(verify_url='https://example.invalid/service')
        c = self.el.cards[shown['id']]
        def verify(_, value):
            self.assertEqual(path.read_bytes(), value)
            return {'verify':'fail', 'detail':'HTTP 401'}
        with patch.object(elevate, 'verify_secret', side_effect=verify):
            await self.host._app(self.s, answer(self.ph, shown, True, 'synthetic'))
            res = await c['fut']
        self.assertEqual((res['result'],res['receipt']['verify']), ('saved','fail'))
        self.assertEqual(path.read_text(), 'synthetic')

    async def test_env_same_value_replacement_advances_target_generation(self):
        path = self.d / 'settings'
        path.write_text('ID=before\n')
        cards = [await self.wire_card(verify_url='https://example.invalid/'+op,
                    replace=True, dest='env:'+str(path)+'#ID') for op in ('a','b')]
        pending = [self.el.cards[c['id']]['fut'] for c in cards]
        with patch.object(elevate,'verify_secret',return_value={'verify':'ok'}) as verifier:
            for m,f,v in zip(cards,pending,('before','other')):
                await self.host._app(self.s, answer(self.ph, m, True, v))
                await f
            self.assertEqual(verifier.call_count,1)
        self.assertEqual([f.result()['result'] for f in pending], ['saved','unknown'])
        self.assertEqual(path.read_text(),'ID=before\n')

    async def test_reserved_http_header_and_query_delivery_description(self):
        m = await self.wire_card(verify_url='https://example.invalid/verify?raw=0&opaque={value}',
                                 verify_header='uSeR-aGeNt: sensitive {value}')
        self.assertNotIn('header uSeR-aGeNt',m['verify'])
        self.assertNotIn('opaque',m['verify'])
        self.assertNotIn('sensitive',m['verify'])
        self.assertIn('URL query #2 (value)', m['verify'])
        c = self.el.cards[m['id']]
        # Exercise the actual urllib Request/header/template construction with an in-memory receiver.
        class Receiver:
            def open(receiver, request, timeout):
                self.assertEqual((self.d/'value').read_text(), 'fixture-only')
                self.assertEqual(request.get_header('User-agent'), 'agentj')
                self.assertEqual(request.selector, '/verify?raw=0&opaque=fixture-only')
                from types import SimpleNamespace
                return contextlib.nullcontext(SimpleNamespace(status=204))
        with patch.object(elevate.urllib.request, 'build_opener', return_value=Receiver()):
            await self.host._app(self.s, answer(self.ph, m, True, 'fixture-only'))
            result = await c['fut']
        self.assertEqual((result['result'], result['receipt']['verify']), ('saved', 'ok'))

    async def test_normal_optional_http_matches_actual_received_headers(self):
        received = asyncio.get_running_loop().create_future()
        async def receiver(reader, writer):
            data = await reader.readuntil(b'\r\n\r\n')
            received.set_result(data)
            writer.write(b'HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(receiver, '127.0.0.1', 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            m = await self.wire_card(verify_url=f'http://127.0.0.1:{port}/me?k={{value}}#local-note',
                                     verify_header='Connection: {value}')
            self.assertNotIn('header Connection', m['verify'])
            self.assertIn('URL query #1 (value)', m['verify'])
            c = self.el.cards[m['id']]
            with patch.dict(os.environ, {'no_proxy':'127.0.0.1', 'NO_PROXY':'127.0.0.1'}):
                await self.host._app(self.s, answer(self.ph, m, True, 'fixture-only'))
                result = await asyncio.wait_for(c['fut'],5)
            wire_data = await asyncio.wait_for(received, 2)
        self.assertEqual((result['result'], result['receipt']['verify']), ('saved','ok'))
        self.assertIn(b'GET /me?k=fixture-only HTTP/1.1', wire_data)
        self.assertIn(b'Connection: close', wire_data)
        self.assertNotIn(b'local-note', wire_data)

    def test_verifier_preserves_exact_route_and_checks_fixed_transport_text(self):
        base = self.req(verify_url='https://example.invalid/profile;?#client-note')
        normalized = elevate.norm_secret(base, self.st.root)
        elevate._chat_http_target(normalized)
        self.assertEqual(normalized['verify_url'], 'https://example.invalid/profile;?')
        other = dict(normalized, verify_url='https://example.invalid/profile;')
        self.assertNotEqual(elevate._chat_verifier(normalized), elevate._chat_verifier(other))
        for change in ({'verify_url':'https://example.invalid/日程'},
                       {'verify_header':'Authorization: 提交 {value}'},
                       {'verify_header':'X-Calendar: v\x7f {value}'},
                       {'verify_url':'https://example.invalid:nonnumeric/'},
                       {'verify_url':'https://日程.example.invalid/'}):
            config = elevate.norm_secret(dict(base, **change), self.st.root)
            with self.assertRaises(elevate.Refused):
                elevate._chat_http_target(config)
        encoded = elevate.norm_secret(dict(base, verify_url='https://example.invalid/%E6%97%A5%E7%A8%8B',
                                           verify_header='X-Calendar: café {value}'), self.st.root)
        elevate._chat_http_target(encoded)
        for target, expected in (('https://[fe80::1%25eth0]/me','[fe80::1%eth0]'),
                                 ('https://%65xample.invalid/me','example.invalid')):
            config = elevate.norm_secret(dict(base, verify_url=target, verify_header='X-Calendar: café {value}\n'), self.st.root)
            elevate._chat_http_target(config)
            actual = elevate._verification_request(config, 'sample')
            self.assertEqual((actual.host, actual.get_header('X-calendar')), (expected, 'café sample'))
            self.assertIn(expected, elevate._chat_verifier(config))

    async def test_optional_transport_exception_preserves_successful_save(self):
        from http.client import HTTPException
        m = await self.wire_card(verify_url='https://example.invalid/me')
        c = self.el.cards[m['id']]
        with patch.object(elevate, 'verify_secret', side_effect=HTTPException('fixture-only')):
            await self.host._app(self.s, answer(self.ph, m, True, 'stored'))
            result = await c['fut']
        self.assertEqual((result['result'],result['receipt']['verify'],result['receipt']['detail']), ('saved','fail','network'))
        self.assertEqual((self.d/'value').read_text(), 'stored')

    async def test_snapshot_and_version_observe_the_same_committed_env_key(self):
        path = self.d / 'settings'
        dest = 'env:'+str(path)+'#ID'
        first = await self.wire_card(dest=dest, replace=True)
        old = self.el.cards[first['id']]
        entered, release = threading.Event(), threading.Event()
        file_lock = self.st.file_lock
        def hold(name):
            if name == 'chat-env-write' and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError()
            return file_lock(name)
        with patch.object(self.st,'file_lock',side_effect=hold):
            next_card = asyncio.create_task(self.wire_card(purpose='next',dest=dest,replace=True))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 4))
                await self.host._app(self.s, answer(self.ph, first, True, 'first'))
                self.assertEqual((await old['fut'])['result'], 'saved')
            finally:
                release.set()
            second = await next_card
        new = self.el.cards[second['id']]
        await self.host._app(self.s, answer(self.ph, second, True, 'second'))
        self.assertEqual((await new['fut'])['result'],'saved')
        self.assertEqual(path.read_text(),'ID=second\n')

    async def test_expiry_during_chat_validation(self):
        shown = await self.wire_card(verify_url='https://example.invalid/calendar')
        result = await self.wait_before_save(shown, lambda c: c.update(deadline=time.monotonic()-1))
        self.assertEqual(result['result'], 'timeout')
        self.assertFalse((self.d / 'value').exists())

    async def test_device_revocation_during_chat_validation(self):
        shown = await self.wire_card(verify_url='https://example.invalid/calendar')
        await self.wait_before_save(shown, lambda c: self.st.remove_device(self.s.device))
        self.assertFalse((self.d / 'value').exists())

    async def test_stop_during_chat_validation(self):
        shown = await self.wire_card(verify_url='https://example.invalid/calendar')
        await self.wait_before_save(shown, lambda c: self.el.cancel_all('stopped'))
        self.assertFalse((self.d / 'value').exists())

    async def test_new_owner_action_then_hook_during_validation(self):
        shown = await self.wire_card(verify_url='https://example.invalid/calendar')
        def change(_):
            self._turn(provenance.phone(), 48, self.origin)
            self._turn(provenance.system(), 49)
        result = await self.wait_before_save(shown, change)
        self.assertEqual(result['why'], 'new_task')
        self.assertFalse((self.d / 'value').exists())

    async def test_target_filled_by_owner_during_validation_is_preserved(self):
        shown = await self.wire_card(verify_url='https://example.invalid/calendar')
        path = self.d / 'value'
        result = await self.wait_before_save(shown, lambda _: path.write_text('owner update'))
        self.assertNotEqual(result['result'], 'saved')
        self.assertEqual(path.read_text(), 'owner update')

    async def test_questioned_chat_does_not_capture_identical_ordinary_card(self):
        shown = await self.wire_card()
        await self.host._app(self.s, fixture.review_intent(self.ph, shown, 'question'))
        chat = self.el.cards[shown['id']]
        self.assertTrue(chat['review_detached'])
        task, ordinary = await self.internal_card(reason='calendar')
        self.assertNotEqual(ordinary['id'], shown['id'])
        await self.host._app(self.s, answer(self.ph, ordinary, True, 'synthetic-calendar'))
        self.assertEqual((await task)['result'], 'saved')
        self.assertFalse(chat['fut'].done())
        await self.host._app(self.s, answer(self.ph, shown, False))

    async def test_settings_file_keeps_034_validation_pin(self):
        task, shown = await self.internal_card(settings_origin='keys', verify_before_write=True)
        result = await self.wait_before_save(shown)
        self.assertEqual(result['why'], 'new_task_during_validation')
        self.assertEqual((await task)['result'], 'unknown')
        self.assertFalse((self.d / 'value').exists())

    async def test_settings_callback_keeps_034_validation_pin(self):
        entered, finish = threading.Event(), threading.Event()
        written = []
        @elevate.validating_sink
        def sink(value):
            entered.set()
            if not finish.wait(5):
                return {'result':'failed'}
            elevate.sink_write_gate()
            written.append(True)
            return {'result':'saved'}
        task, shown = await self.internal_card(sink=sink, settings_origin='keys', only=self.s.device)
        try:
            await self.host._app(self.s, answer(self.ph, shown, True, 'synthetic-calendar'))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            self._turn(provenance.system(), 47)
        finally:
            finish.set()
        self.assertEqual((await task)['why'], 'new_task_during_validation')
        self.assertFalse(written)

    async def test_unverified_wire_cannot_claim_chat_and_internal_cannot_borrow_mark(self):
        m = await self.wire_card(verified=False, _chat_input=True, _chat_operation='phone')
        self.assertNotIn('_chat_input', self.el.cards[m['id']])
        await self.host._app(self.s, answer(self.ph, m, False))
        owner = await self.wire_card()
        marker = self.el.cards[owner['id']]['_chat_input']
        spec = elevate.norm_secret(self.req(), self.st.root)
        for card in (dict(spec, _chat_input=marker), dict(spec, settings_origin='keys', _chat_operation='phone')):
            self.assertEqual((await self.el.request(card))['result'], 'refused')
        self.assertEqual((await self.el._request(dict(spec), None, None, _chat=marker))['result'], 'refused')
        await self.host._app(self.s, answer(self.ph, owner, False))

    async def test_verifier_and_lane_do_not_share_receipts_or_pending_operations(self):
        url = 'https://example.invalid/path?q=fixture'
        shown = await self.wire_card(verify_url=url, verify_header='X-Fake: literal {value}',
                                     purpose='calendar password="fixture-one"')
        text = json.dumps(shown)
        for private in ('fixture-one', '?q=fixture', '/path', 'literal'):
            self.assertNotIn(private, text)
        b = await self.wire_card(verify_url=url+'-other')
        self.assertNotEqual(shown['envelope']['input_sha256'], b['envelope']['input_sha256'])
        task, settings = await self.internal_card(settings_origin='keys')
        c = self.el.cards[shown['id']]
        with patch.object(elevate, 'verify_secret', return_value={'verify':'ok'}):
            await self.host._app(self.s, answer(self.ph, shown, True, 'synthetic-calendar'))
            self.assertEqual((await c['fut'])['result'], 'saved')
            await self.wait_for('elev_done')
        for other in (b, settings):
            self.assertFalse(self.el.cards[other['id']]['fut'].done())
            await self.host._app(self.s, answer(self.ph, other, False))
        await task
