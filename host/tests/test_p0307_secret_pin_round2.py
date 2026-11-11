"""0.30.7 approval round 2 — P541 leftovers N4b + N5a (real Host, real card, real signed answer; synthetic values only).

N4b: a reviewed secret card whose write the P541 pin refused (a new task started between the accepted answer and the write)
     ends with a typed reason — `new_task_during_validation` once the value passed its check, `new_task` before — and
     `elev_done` / the secret result carry it, so the Settings modal can say so instead of a generic failure.
N5a: reviewed secret cards with a sink are pinned too. A validating sink calls `elevate.sink_write_gate()` after its check
     and right before its write; a live task / source change in between refuses the write (same AuthorityChanged path).
     Answering when nothing moved still saves; outside a card's save the gate is a no-op. The mail-password and the
     Claude setup-token sinks validate first, then pass the gate, then write.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import pathlib
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentj import approval_special, elevate
import test_p541_special as fixture
from test_f17 import answer


class PinRound2(unittest.TestCase):
    setUp = fixture.Special.setUp
    messages = fixture.Special.messages
    wait_card = fixture.Special.wait_card

    def new_task(self):
        self.host.task_epoch = 'new-task-during-validation'

    async def race(self, spec, *, block_in, change=True):
        """Answer SPEC's card; while the worker sits inside BLOCK_IN (a context manager factory taking (entered, release)),
        optionally start a new task; return (caller result, elev_done)."""
        caller = asyncio.create_task(self.host.elevate.request(spec))
        card = await self.wait_card('elev')
        entered, release = threading.Event(), threading.Event()
        with block_in(entered, release):
            task = asyncio.create_task(self.host._app(self.sa, answer(self.a, card, True, 'synthetic-value-0307')))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            if change:
                self.new_task()
            release.set()
            await asyncio.wait_for(task, 2)
            result = await asyncio.wait_for(caller, 3)
        done = [m for m in self.messages('elev_done') if m['id'] == card['id']]
        await self.host.elevate.stop()
        return result, done[-1] if done else None, card

    # ------------------------------------------------------------------ N4b
    def test_n4b_new_task_during_key_validation_is_typed(self):
        target = pathlib.Path(self.tmp.name) / 'synthetic-env'

        def block(entered, release):
            def verify(card, value):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('fixture timeout')
                return {'verify': 'ok'}
            return patch.object(elevate, 'verify_secret', side_effect=verify)

        async def run():
            spec = elevate.norm_secret({'name': 'TEST_ONLY_KEY', 'purpose': 'Synthetic', 'dest': str(target)}, self.st.root)
            spec['verify_before_write'] = True
            result, done, card = await self.race(spec, block_in=block)
            self.assertEqual(result['result'], 'unknown')
            self.assertEqual(result['why'], 'new_task_during_validation')
            self.assertEqual(done['why'], 'new_task_during_validation')
            self.assertFalse(target.exists())
            self.assertEqual(elevate.read_results(self.st)[card['id']]['why'], 'new_task_during_validation')
        asyncio.run(run())

    def test_n4b_new_task_before_any_check_is_typed_new_task(self):
        target = pathlib.Path(self.tmp.name) / 'synthetic-env'
        original = elevate.Elevator._save

        def block(entered, release):
            def save(this, c, value):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('fixture timeout')
                return original(this, c, value)
            return patch.object(elevate.Elevator, '_save', autospec=True, side_effect=save)

        async def run():
            spec = elevate.norm_secret({'name': 'TEST_ONLY_KEY', 'purpose': 'Synthetic', 'dest': str(target)}, self.st.root)
            result, done, _ = await self.race(spec, block_in=block)
            self.assertEqual((result['result'], result['why']), ('unknown', 'new_task'))
            self.assertEqual(done['why'], 'new_task')
            self.assertFalse(target.exists())
        asyncio.run(run())

    def test_n4b_other_authority_changes_keep_the_old_reason(self):
        """Signing-key rotation during validation is not a 'new task': the web keeps its old wording (no typed why)."""
        target = pathlib.Path(self.tmp.name) / 'synthetic-env'
        rotate = patch.object(self.st, 'sign_key', return_value=b'R' * 32)
        self.addCleanup(rotate.stop)

        def block(entered, release):
            def verify(card, value):
                entered.set()
                release.wait(2)
                rotate.start()
                return {'verify': 'ok'}
            return patch.object(elevate, 'verify_secret', side_effect=verify)

        async def run():
            spec = elevate.norm_secret({'name': 'TEST_ONLY_KEY', 'purpose': 'Synthetic', 'dest': str(target)}, self.st.root)
            spec['verify_before_write'] = True
            result, done, _ = await self.race(spec, block_in=block, change=False)
            self.assertEqual((result['result'], result['why']), ('unknown', 'authority_changed'))
            self.assertNotIn('why', done)
            self.assertFalse(target.exists())
        asyncio.run(run())

    # ------------------------------------------------------------------ N5a
    def sink_spec(self, written, entered_release):
        def sink(value):
            entered, release = entered_release
            entered.set()                       # the sink's own network check
            if not release.wait(2):
                raise RuntimeError('fixture timeout')
            elevate.sink_write_gate(validated=True)
            written.append(bytes(value))        # the write
            return {'result': 'saved', 'receipt': {'name': 'TEST_ONLY_SINK', 'dest': 'fixture', 'verify': 'ok'}}
        return {'kind': 'secret', 'name': 'TEST_ONLY_SINK', 'purpose': 'Synthetic sink', 'dest': 'fixture sink',
                'dest_spec': {'mode': 'mail', 'path': '', 'key': 'TEST_ONLY_SINK', 'show': 'fixture'},
                'cwd': '/', 'verify': 'fixture', 'verify_url': '', 'verify_header': '', 'verify_cmd': '', 'sink': sink}

    def sink_case(self, change):
        written, pair = [], [None, None]

        def block(entered, release):
            pair[:] = [entered, release]
            from contextlib import nullcontext
            return nullcontext()

        async def run():
            spec = self.sink_spec(written, pair)
            return await self.race(spec, block_in=block, change=change)
        result, done, _ = asyncio.run(run())
        return result, done, written

    def test_n5a_sink_write_refused_when_a_new_task_started_during_validation(self):
        result, done, written = self.sink_case(change=True)
        self.assertEqual(written, [])
        self.assertEqual((result['result'], result['why']), ('unknown', 'new_task_during_validation'))
        self.assertEqual(done['why'], 'new_task_during_validation')

    def test_n5a_sink_write_kept_when_nothing_moved(self):
        result, done, written = self.sink_case(change=False)
        self.assertEqual(written, [b'synthetic-value-0307'])
        self.assertEqual(result['result'], 'saved')
        self.assertNotIn('why', done)

    def skipping_case(self, validating):
        """r1 B-2: a sink that never reaches its gate (e.g. its write moved into an uncopied thread)."""
        written, pair = [], [None, None]

        def block(entered, release):
            pair[:] = [entered, release]
            from contextlib import nullcontext
            return nullcontext()

        async def run():
            spec = self.sink_spec(written, pair)
            inner = spec['sink']

            def sink(value):
                entered, release = pair
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('fixture timeout')
                written.append(bytes(value))          # no sink_write_gate() call
                return {'result': 'saved', 'receipt': {'name': 'TEST_ONLY_SINK', 'dest': 'fixture', 'verify': 'ok'}}
            del inner
            spec['sink'] = elevate.validating_sink(sink) if validating else sink
            return await self.race(spec, block_in=block, change=False)
        return asyncio.run(run()), written

    def test_r1_validating_sink_that_skips_the_gate_fails_closed(self):
        (result, done, _card), written = self.skipping_case(validating=True)
        self.assertEqual((result['result'], result['why']), ('unknown', 'authority_changed'))
        self.assertNotEqual(done.get('result'), 'saved')
        self.assertIn('secret_sink_gate_skipped', self.host.st.log_path.read_text())
        self.assertNotIn('synthetic-value-0307', self.host.st.log_path.read_text())

    def test_r1_unmarked_sink_without_a_gate_keeps_its_old_behaviour(self):
        (result, done, _card), written = self.skipping_case(validating=False)
        self.assertEqual(result['result'], 'saved')
        self.assertEqual(written, [b'synthetic-value-0307'])

    def test_n5a_pin_scope(self):
        adapter = object()
        sink = lambda v: {}  # noqa: E731
        self.assertTrue(elevate._pins_sink_write({'kind': 'secret', 'sink': sink, 'review_adapter': adapter}))
        self.assertFalse(elevate._pins_live_context({'kind': 'secret', 'sink': sink, 'review_adapter': adapter}))
        for kind in ('login', 'vshare', 'sudo'):   # the 0.30.1 rule: terminal login / password share are never pinned
            self.assertFalse(elevate._pins_sink_write({'kind': kind, 'sink': sink, 'review_adapter': adapter}))
        elevate.sink_write_gate()                    # outside a card's save: no-op


class SinkOrder(unittest.TestCase):
    """The validating sinks check first, then pass the gate, then write — a refusing gate means no write at all."""

    def refusing_gate(self, calls):
        def gate(validated=True):
            calls.append(('gate', validated))
            raise approval_special.LiveContextMoved(validated)
        return gate

    def test_mail_password_sink_validates_then_gates_then_stores(self):
        import tempfile
        from agentj import mail
        from agentj.state import State
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d)); st.init(); captured, calls = {}, []

            async def request(card):
                captured.update(card)
                return {'result': 'pending'}
            m = mail.Mail(SimpleNamespace(st=st, lang='en', agent=None, elevate=SimpleNamespace(request=request)))
            cfg = mail.blank_config()
            cfg.update(address='fixture@example.com', username='fixture@example.com',
                       imap={'host': 'imap.example.com', 'port': 993, 'security': 'ssl'},
                       smtp={'host': 'smtp.example.com', 'port': 465, 'security': 'ssl'})
            m.set_config(cfg)
            asyncio.run(m.secret_card())

            def connect(c, pw):
                calls.append(('check', None))
                return {'ok': True, 'imap': 'ok', 'smtp': 'ok'}
            self.assertTrue(elevate.is_validating_sink(captured['sink']), 'r1 B-2: marked by host code')
            refuse = self.refusing_gate(calls)

            def gate(validated=True):           # r1 B-1: the gate runs under the config lock (after the stale re-check)
                calls.append(('locked', m._config_lock._is_owned()))
                refuse(validated)
            token = elevate._SINK_WRITE_GATE.set(gate)
            try:
                with patch.object(mail, 'test_connection', side_effect=connect), \
                        patch.object(mail, 'store_secret', side_effect=lambda *a, **k: calls.append(('store', None))):
                    with self.assertRaises(approval_special.LiveContextMoved) as e:
                        captured['sink'](bytearray(b'fixture-password'))
            finally:
                elevate._SINK_WRITE_GATE.reset(token)
            self.assertEqual(calls, [('check', None), ('locked', True), ('gate', True)])
            self.assertEqual(e.exception.why, 'new_task_during_validation')

            # a destination change during the check: refused as stale before the gate, nothing stored
            calls.clear()

            def connect_and_move(c, pw):
                calls.append(('check', None))
                moved = mail.read_config(st)
                moved['address'] = 'moved@example.com'
                m.set_config(moved)
                return {'ok': True, 'imap': 'ok', 'smtp': 'ok'}
            token = elevate._SINK_WRITE_GATE.set(gate)
            try:
                with patch.object(mail, 'test_connection', side_effect=connect_and_move), \
                        patch.object(mail, 'store_secret', side_effect=lambda *a, **k: calls.append(('store', None))):
                    self.assertEqual(captured['sink'](bytearray(b'fixture-password')), {'result': 'failed', 'why': 'stale'})
            finally:
                elevate._SINK_WRITE_GATE.reset(token)
            self.assertEqual(calls, [('check', None)])

    def test_claude_token_sink_live_checks_then_gates_then_writes(self):
        from agentj import auth_rescue as ar
        calls, outcome = [], {}
        me = SimpleNamespace(flow={}, lang=lambda: 'en', why=lambda code, **kw: code,
                             host=SimpleNamespace(st=SimpleNamespace(root=pathlib.Path('/nonexistent-fixture'))))
        token = elevate._SINK_WRITE_GATE.set(self.refusing_gate(calls))
        try:
            with patch.object(ar, 'live_claude', side_effect=lambda tok: calls.append(('check', None)) or True), \
                    patch.object(elevate, 'write_secret', side_effect=lambda *a: calls.append(('write', None))):
                with self.assertRaises(approval_special.LiveContextMoved):
                    ar.Rescue._store_token(me, bytearray(b'sk-ant-oat01-' + b'A' * 60), outcome)
        finally:
            elevate._SINK_WRITE_GATE.reset(token)
        self.assertEqual(calls, [('check', None), ('gate', True)])
        self.assertEqual(outcome.get('why'), 'authority_changed')


if __name__ == '__main__':
    unittest.main()
