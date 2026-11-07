"""Offline real Host regressions; no ask=allow stubs and no external services."""
import _hermetic
import asyncio
import json
import pathlib
import socket
import tempfile
import time
import unittest
from unittest import mock

from agentj import approvals, forms, learn, provenance, serve, uploads, wire
from test_l1 import Phone, _owner_host, _ready, _state

SKILL = '---\nname: fixture-skill\ndescription: Synthetic local practice\n---\nUse synthetic fixtures.\n'


class HostClosure(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.st = _state(self.tmp.name)
        self.sent = []
        self.h = _owner_host(self.st, self.sent)
        self.h.agent_cfg = {'dir': str(self.work), 'kind': 'claude'}
        self.h.ask_ttl = 1
        self.h.agent.submit = lambda send: None
        self.phone = Phone(self.st, with_passkey=True)
        self.s = _ready(self.h, self.phone)
        self.s.p33 = True
        self.h.uploads = uploads.Uploads(self.st, str(self.work))
        original_connect = socket.socket.connect
        def local_only(sock, address):
            if sock.family != socket.AF_UNIX:
                raise AssertionError('Offline fixture forbids network connections')
            return original_connect(sock, address)
        network = mock.patch.object(socket.socket, 'connect', local_only)
        network.start()
        self.addCleanup(network.stop)
        dns = mock.patch.object(socket, 'getaddrinfo', side_effect=AssertionError('Offline fixture forbids DNS'))
        dns.start()
        self.addCleanup(dns.stop)
        self.skillroot = self.root / 'skills'
        self.skillroot.mkdir()
        patch = mock.patch.object(learn, 'skills_root', return_value=self.skillroot)
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        from agentj import diskq
        await diskq.aflush()

    async def proposal(self):
        d = await self.h.learner.handle({'t': 'learn_draft'})
        self.assertEqual(d['result'], 'draft', d)
        self.src = pathlib.Path(d['skill_dir']) / 'SKILL.md'
        self.src.write_text(SKILL)
        task = asyncio.create_task(self.h.learner.handle({'t': 'learn_propose', 'id': d['id'], 'name': 'fixture-skill'}))
        deadline = time.monotonic() + 2
        while not any(o['t'] == 'ask' for _, o in self.sent):
            if task.done():
                self.fail(str(await task))
            self.assertLess(time.monotonic(), deadline)
            await asyncio.sleep(.005)
        card = next(o for _, o in self.sent if o['t'] == 'ask')
        self.assertIn('Use synthetic fixtures.', card['summary'])
        self.assertIn('Proposal SHA-256:', card['summary'])
        self.assertTrue(card['passkey_required'])
        self.assertEqual(self.h.asks[card['id']].digest, approvals.shown_digest(learn.TOOL, card['summary']))
        return task, card

    async def answer(self, card, obj=None):
        await self.h._app(self.s, obj or self.phone.answer(card, True))

    async def test_voice_config_runtime_and_free_phone_do_not_invoke_engines(self):
        from agentj import preferences, spoken, voice
        prefs = preferences.validate({'voice': {'tts': {'phone_voice': 'fixture-phone', 'app_id': 'fixture-app',
            'spoken': 'auto', 'spoken_model': 'mid', 'spoken_max_chars': 400}}})
        self.assertEqual(preferences.get(prefs, 'voice.tts.phone_voice'), 'fixture-phone')
        self.assertEqual(preferences.get(prefs, 'voice.tts.app_id'), 'fixture-app')
        with mock.patch.object(spoken, '_run_harness', side_effect=AssertionError('model invoked')) as model:
            result = spoken.script(self.st, 'Synthetic reply', prefs, 'claude')
            self.assertFalse(result['spoken'])
            model.assert_not_called()
        declared = preferences.validate({'voice': {'tts': {'mode': 'cloud', 'provider': 'volcengine',
            'voice': 'fixture-voice', 'app_id': 'fixture-app', 'spoken': 'off'}}})
        self.assertEqual(preferences.get(declared, 'voice.tts.app_id'), 'fixture-app')
        self.assertEqual(preferences.SCHEMA['voice.tts.spoken']['apply'], 'hot')

    async def test_actual_signed_uv_installs_exact_bytes(self):
        task, card = await self.proposal()
        await self.answer(card)
        result = await task
        self.assertEqual(result['result'], 'applied', result)
        self.assertEqual((self.skillroot / 'fixture-skill/SKILL.md').read_text(), SKILL)

    async def test_full_access_still_requires_real_signed_uv(self):
        from agentj import approval_level
        with mock.patch.object(approval_level, 'level', return_value='harness'):
            task, card = await self.proposal()
            self.assertFalse(task.done())
            await self.answer(card)
            self.assertEqual((await task)['result'], 'applied')

    async def test_names_and_serialized_card_cannot_register(self):
        for kw in ({}, {'_learn_proposal': {}}, {'_learn_proposal': object()}):
            result = await self.h.ask(learn.TOOL, {'learn': 'abcdefghijkm', 'card': SKILL}, **kw)
            self.assertEqual(result['behavior'], 'deny')
        self.assertEqual((await self.h.ask('unknown-fixture', {}))['behavior'], 'deny')
        self.assertFalse(any(o['t'] == 'ask' for _, o in self.sent))

    async def test_missing_signature_and_wrong_device_do_not_answer(self):
        task, card = await self.proposal()
        await self.answer(card, {'t': 'answer', 'id': card['id'], 'ok': True})
        other = Phone(self.st, with_passkey=True)
        _ready(self.h, other, cid=12)
        wrong = other.answer(card, True)
        await self.answer(card, wrong)  # genuine signature, bound to another device
        self.assertFalse(task.done())
        await self.answer(card, self.phone.answer(card, False))
        self.assertEqual((await task)['result'], 'declined')
        self.assertFalse((self.skillroot / 'fixture-skill').exists())

    async def test_valid_signature_without_uv_does_not_write(self):
        task, card = await self.proposal()
        answer = self.phone.answer(card, True)
        answer.pop('fa')
        await self.answer(card, answer)
        self.assertFalse(task.done())
        self.assertFalse((self.skillroot / 'fixture-skill').exists())
        await self.answer(card, self.phone.answer(card, False))
        self.assertEqual((await task)['result'], 'declined')

    async def test_replaced_target_same_bytes_refused(self):
        dest = self.skillroot / 'fixture-skill'
        dest.mkdir()
        (dest / 'SKILL.md').write_text(SKILL + 'Old version.\n')
        task, card = await self.proposal()
        replacement = dest / 'replacement'
        replacement.write_text(SKILL + 'Old version.\n')
        replacement.replace(dest / 'SKILL.md')
        await self.answer(card)
        self.assertEqual((await task)['result'], 'failed')
        self.assertEqual((dest / 'SKILL.md').read_text(), SKILL + 'Old version.\n')

    async def test_changed_staging_after_signed_review_refused(self):
        task, card = await self.proposal()
        self.src.write_text(SKILL + 'Changed after review.\n')
        await self.answer(card)
        self.assertEqual((await task)['result'], 'failed')
        self.assertFalse((self.skillroot / 'fixture-skill').exists())

    async def test_target_symlink_after_review_refused(self):
        task, card = await self.proposal()
        outside = self.root / 'other'
        outside.mkdir()
        (self.skillroot / 'fixture-skill').symlink_to(outside, target_is_directory=True)
        await self.answer(card)
        self.assertEqual((await task)['result'], 'failed')
        self.assertFalse((outside / 'SKILL.md').exists())

    async def test_expired_proposal_refused(self):
        task, card = await self.proposal()
        self.h.learner.cur['at'] -= learn.DRAFT_TTL
        await self.answer(card)
        self.assertEqual((await task)['result'], 'failed')

    async def test_stopped_proposal_never_writes(self):
        task, card = await self.proposal()
        with mock.patch.object(self.h, 'stopped', return_value=True):
            await self.answer(card)
            self.assertEqual((await task)['result'], 'declined')
        self.assertFalse((self.skillroot / 'fixture-skill').exists())

    async def form_send(self, raw):
        # The real Elevator handler, with kernel peer credentials on an isolated local socket.
        path = str(self.root / 'fixture.sock')
        server = await asyncio.start_unix_server(self.h.elevate.on_client, path)
        try:
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write((json.dumps({'t': 'form', 'op': 'send', 'form': raw}) + '\n').encode())
            await writer.drain()
            result = json.loads(await asyncio.wait_for(reader.readline(), 2))
            writer.close()
            await writer.wait_closed()
            self.assertTrue(result['ok'], result)
            return result
        finally:
            server.close()
            await server.wait_closed()
            pathlib.Path(path).unlink(missing_ok=True)

    async def say(self, session, fid, value='a', attachments=None):
        sid = wire.b64u(__import__('os').urandom(16))
        obj = {'t': 'say', 'sid': sid, 'text': 'Fixture answer', 'ts': 0,
               'form': {'id': fid, 'answers': value if isinstance(value, dict) else {'q': value}}}
        if attachments is not None:
            obj['att'] = attachments
        with provenance.paired_request(lambda: self.h._paired_live(session)):
            await self.h._app(session, obj)
        await asyncio.sleep(.02)
        return next((o for _, o in self.sent if o.get('t') == 'say_res' and o.get('sid') == sid), None)

    async def test_light_deep_grill_over_real_elevate(self):
        for raw in ({'title': 'Fixture', 'questions': [{'id': 'q', 'type': 'single', 'label': 'Pick', 'options': ['a','b']}]},
                    {'title': 'Deep fixture', 'questions': [{'id': 'q', 'type': 'file', 'label': 'File'}]},
                    forms.apply('grill-startup', forms.example('grill-startup', 'en'))):
            result = await self.form_send(raw)
            self.assertEqual(self.h.forms.store.get(result['id'])['state'], 'open')

    async def test_expired_form_refused_by_real_paired_entry(self):
        result = await self.form_send({'title': 'Expiry', 'questions': [{'id': 'q', 'type': 'text', 'label': 'Text'}]})
        rec = self.h.forms.store.get(result['id'])
        rec['expires'] = time.time() - 1
        self.h.forms.store.save(rec)
        self.assertEqual((await self.say(self.s, result['id'], 'fixture'))['why'], 'form_expired')

    async def test_attachment_ownership_and_withdrawal_over_paired_entry(self):
        import hashlib
        form = await self.form_send({'title': 'Attachment', 'questions': [{'id': 'q', 'type': 'file', 'label': 'File'}]})
        data = b'\x89PNG\r\n\x1a\n' + b'x' * 100
        bid = wire.b64u(b'b' * 16)
        self.h.uploads.open(self.s.device, {'bid': bid, 'purpose': 'att', 'name': 'fixture.png',
            'mime': 'image/png', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'origin': 'file'})
        self.h.uploads.chunk(self.s.device, {'bid': bid, 'o': 0, 'd': wire.b64u(data)})
        out, _ = self.h.uploads.end(self.s.device, bid)
        self.assertTrue(out[-1]['ok'], out)
        other = Phone(self.st)
        s2 = _ready(self.h, other, cid=12)
        s2.p33 = True
        bad = await self.say(s2, form['id'], [bid], [bid])
        self.assertEqual(bad['why'], 'att_gone')
        self.assertEqual(self.h.forms.store.get(form['id'])['state'], 'open')
        good = await self.say(self.s, form['id'], [bid], [bid])
        self.assertTrue(good['ok'], good)
        sid = next(o['sid'] for _, o in reversed(self.sent) if o.get('t') == 'say_res' and o.get('ok'))
        await self.h._app(self.s, {'t': 'say_cancel', 'sid': sid})
        rec = self.h.forms.store.get(form['id'])
        self.assertEqual(rec['state'], 'open')
        self.assertNotIn('answer', rec)
        self.assertEqual(self.h.uploads.blobs[(self.s.device, bid)].state, 'staged')

    async def test_grill_answers_reach_real_host(self):
        result = await self.form_send(forms.apply('grill-startup', forms.example('grill-startup', 'en')))
        spec = self.h.forms.store.get(result['id'])['spec']
        answers = {}
        for q in forms.questions(spec):
            if q['type'] == 'note':
                continue
            if q['type'] in ('single', 'buttons', 'dropdown'):
                answers[q['id']] = q.get('rec', q['options'][0]['value'])
            elif q['type'] == 'multi':
                answers[q['id']] = q.get('rec', [q['options'][0]['value']])
            elif q['type'] in ('number', 'slider', 'rating'):
                answers[q['id']] = q.get('rec', q.get('min', 1))
            elif q['type'] in ('text', 'longtext'):
                answers[q['id']] = q.get('rec', 'Synthetic fixture answer')
        forms.check(spec, answers)
        response = await self.say(self.s, result['id'], answers)
        self.assertTrue(response['ok'], response)
        self.assertEqual(self.h.forms.store.get(result['id'])['state'], 'answered')

    async def test_concurrent_first_answer_wins(self):
        self.h.agent.submit = lambda send: None
        raw = {'title': 'Fixture', 'questions': [{'id': 'q', 'type': 'single', 'label': 'Pick', 'options': ['a','b']}]}
        form = await self.form_send(raw)
        other = Phone(self.st)
        s2 = _ready(self.h, other, cid=12)
        s2.p33 = True
        results = await asyncio.gather(self.say(self.s, form['id']), self.say(s2, form['id']))
        self.assertEqual(sum(bool(r and r.get('ok')) for r in results), 1, results)
        self.assertEqual(sum(bool(r and r.get('why') == 'form_answered') for r in results), 1, results)

    async def test_draft_cancel_expiry_and_unpaired_gate(self):
        form = await self.form_send({'title': 'Fixture', 'questions': [{'id': 'q', 'type': 'text', 'label': 'Write'}]})
        await self.h._app(self.s, {'t': 'form_draft', 'id': form['id'], 'answers': {'q': 'draft'}})
        self.assertEqual(self.h.forms.store.draft(form['id'])['answers']['q'], 'draft')
        await self.h.forms.agent({'op': 'cancel', 'id': form['id']})
        self.assertIsNone(self.h.forms.store.draft(form['id']))
        self.assertEqual((await self.say(self.s, form['id'], 'answer'))['why'], 'form_cancelled')
        self.h.forms.store.get(form['id'])
        # An unpaired new session cannot mutate forms through the transport entry.
        pending = serve.Session(cid=99, state='pending', device='f'*16, pub=b'z'*32)
        await self.h._app(pending, {'t': 'form_draft', 'id': form['id'], 'answers': {'q': 'bad'}})
        self.assertIsNone(self.h.forms.store.draft(form['id']))


if __name__ == '__main__':
    unittest.main()
