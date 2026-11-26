"""Repeated --name uses one sealed transaction. Independent live cards retain their own authority."""
import _hermetic  # noqa: F401
import argparse
import asyncio
import json
import stat
import time
import unittest
from unittest.mock import patch

from agentj import approval_material, elevate
import test_0335_secret_validation as previous
from test_f17 import answer

FIRST = 'synthetic first'
SECOND = 'synthetic second "with quotes"'
NAMES = ['TEST_CLIENT_ID', 'TEST_CLIENT_SECRET']


class SecretCards(unittest.IsolatedAsyncioTestCase):
    setUp = previous.SecretValidation.setUp
    asyncSetUp = previous.SecretValidation.asyncSetUp
    asyncTearDown = previous.SecretValidation.asyncTearDown
    of = previous.SecretValidation.of
    wait_for = previous.SecretValidation.wait_for
    _turn = previous.SecretValidation._turn
    wire_card = previous.SecretValidation.wire_card

    def req(self, **extra):
        return {'t': 'secret', 'names': NAMES, 'dest': f'env:{self.dest}',
                'purpose': 'Synthetic credentials', 'early': True, 'wait': 0.2, **extra}

    async def request(self, **extra):
        return await asyncio.to_thread(elevate.client_request, self.st, self.req(**extra), 10)

    def signed(self, shown, values, *, version=2, **kw):
        frame = answer(self.ph, shown, True, json.dumps(values), **kw)
        if version is not None:
            frame['values_v'] = version
        return frame

    async def complete(self, shown, frame):
        fut = self.el.cards[shown['id']]['fut']
        await self.host._app(self.s, frame)
        return await asyncio.wait_for(fut, 5)

    async def test_batch_saves_once_with_one_uv_and_preserves_other_keys(self):
        shown = await self.wire_card()
        self.assertEqual(shown['names'], NAMES)
        self.assertEqual(shown['name'], ', '.join(NAMES))
        self.assertEqual(shown['dest'], str(self.dest))
        self.host.agent.cur_send = None
        frame = self.signed(shown, [FIRST, SECOND])
        saved = await self.complete(shown, frame)
        self.assertEqual(saved['result'], 'saved')
        self.assertEqual(saved['receipt']['names'], NAMES)
        self.assertNotIn('length', saved['receipt'])
        self.assertNotIn('fingerprint', saved['receipt'])
        before = self.dest.read_bytes()
        self.assertEqual(before, (f'OTHER=1\n{NAMES[0]}="synthetic first"\n'
                                  f'{NAMES[1]}="synthetic second \\"with quotes\\""\n').encode())
        self.assertEqual(stat.S_IMODE(self.dest.stat().st_mode), 0o600)
        await self.host._app(self.s, frame)
        self.assertEqual(self.dest.read_bytes(), before)
        self.assertEqual(sum(row['result'] == 'saved' for row in elevate.read_log(self.st)), 1)
        if not self.of('elev_done'):
            await self.wait_for('elev_done')
        for name in NAMES:
            self.assertEqual((await self.el.status_of({'name': name}))['status'], 'saved')
        paths = [self.st.log_path, elevate.log_path(self.st), elevate.results_path(self.st)]
        public = json.dumps([self.sent, saved]) + ''.join(p.read_text() for p in paths if p.exists())
        self.assertNotIn(FIRST, public)
        self.assertNotIn(SECOND, public)

    async def test_legacy_answer_missing_uv_modified_names_and_expired_are_rejected(self):
        shown = await self.wire_card()
        legacy = self.signed(shown, [FIRST, SECOND], version=None)
        await self.host._app(self.s, legacy)
        self.assertEqual(self.of('elev_refused')[-1]['why'], 'shape')
        no_uv = self.signed(shown, [FIRST, SECOND])
        no_uv.pop('fa')
        await self.host._app(self.s, no_uv)
        self.assertEqual(self.of('elev_refused')[-1]['why'], 'passkey')
        wrong = {**shown, 'name': ', '.join(reversed(NAMES))}
        await self.host._app(self.s, self.signed(wrong, [FIRST, SECOND]))
        self.assertEqual(self.of('elev_refused')[-1]['why'], 'bad_signature')
        self.el.cards[shown['id']]['deadline'] = time.monotonic() - 1
        await self.host._app(self.s, self.signed(shown, [FIRST, SECOND]))
        self.assertEqual(self.of('elev_refused')[-1]['why'], 'late')
        self.assertEqual(self.dest.read_text(), 'OTHER=1\n')

    async def test_invalid_batch_value_leaves_every_key_unwritten(self):
        for values in ([FIRST], [FIRST, SECOND, 'extra'], [FIRST, None], [FIRST, chr(10)], [FIRST, chr(0x2028)], [FIRST, '   '], {'one': FIRST}):
            shown = await self.wire_card()
            result = await self.complete(shown, self.signed(shown, values))
            self.assertEqual((result['result'], result['why']), ('failed', 'value'))
            self.assertEqual(self.dest.read_text(), 'OTHER=1\n')
            await self.wait_for('elev_done', len(self.of('elev')))

    async def test_batch_and_independent_legacy_card_do_not_invalidate_each_other(self):
        shown = await self.wire_card()
        req = self.req()
        req.pop('names')
        with patch.object(self, 'req', return_value={**req, 'name': 'TEST_OTHER'}):
            single = await self.wire_card()
        self.assertNotIn('names', single)
        self.host.agent.cur_send = None
        batch, old = await asyncio.gather(
            self.complete(shown, self.signed(shown, [FIRST, SECOND])),
            self.complete(single, answer(self.ph, single, True, 'single-fixture')),
        )
        self.assertEqual((batch['result'], old['result']), ('saved', 'saved'))
        self.assertEqual(old['receipt']['length'], len('single-fixture'))
        self.assertIn('TEST_OTHER=single-fixture', self.dest.read_text())
        self.assertIn('TEST_CLIENT_ID="synthetic first"', self.dest.read_text())

    async def test_a_changed_batch_target_refuses_all_assignments(self):
        shown = await self.wire_card()
        self.dest.write_text(f'OTHER=1\n{NAMES[1]}=owner-edited\n')
        result = await self.complete(shown, self.signed(shown, [FIRST, SECOND]))
        self.assertEqual((result['result'], result['why']), ('unknown', 'authority_changed'))
        self.assertEqual(self.dest.read_text(), f'OTHER=1\n{NAMES[1]}=owner-edited\n')

    async def test_batch_replaces_prefilled_keys_only_with_replace(self):
        self.dest.write_text(f'OTHER=1\n{NAMES[0]}=owner-choice\n')
        with patch.object(approval_material, 'verified_phone_chat', return_value=(self.host.agent, self.origin)):
            result = await self.request()
            self.assertEqual((result['result'], result['why']), ('refused', 'replace_required'))
        self.assertEqual(self.of('elev'), [])
        shown = await self.wire_card(replace=True)
        result = await self.complete(shown, self.signed(shown, [FIRST, SECOND]))
        self.assertEqual(result['result'], 'saved')
        self.assertNotIn('owner-choice', self.dest.read_text())

    async def test_scope_or_owner_refusal_precedes_delivery(self):
        requests = [{'names': ['A', 'A']}, {'names': ['A', 'SUDO_PASSWORD']},
                    {'dest': f'env:{self.dest}#OTHER'}, {'dest': f'file:{self.dest}'},
                    {'verify_url': 'https://example.invalid/check'}]
        with patch.object(approval_material, 'verified_phone_chat', return_value=(self.host.agent, self.origin)):
            for request in requests:
                self.assertEqual((await self.request(**request))['result'], 'refused')
        self.assertEqual((await self.request())['result'], 'refused')
        self.assertEqual(self.of('elev'), [])

    def test_repeated_cli_name_uses_batch_but_one_name_keeps_legacy_request(self):
        p = argparse.ArgumentParser()
        elevate.add_parser(p.add_subparsers())
        for names in (NAMES, NAMES[:1]):
            argv = ['secret', 'request', '--purpose', 'Test', '--dest', f'env:{self.dest}', '--json']
            for name in names:
                argv += ['--name', name]
            with patch.object(elevate, 'client_request', return_value={'result': 'refused'}) as request, \
                    patch('builtins.print'):
                elevate.cmd_secret(p.parse_args(argv))
            self.assertEqual(request.call_args.args[1].get('names'), names if len(names) > 1 else None)
            self.assertEqual(request.call_args.args[1].get('name'), names[0] if len(names) == 1 else None)
