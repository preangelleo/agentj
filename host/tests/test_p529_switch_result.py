"""P529: a scheduled switch is not a completed switch; history fallback stays local."""
import _hermetic
import contextlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import switch_harness as sw, slash, handoff, host_diagnostics, feedback_father


class Result(unittest.TestCase):
    def test_poll_waits_for_same_job_final_receipt(self):
        from test_p155_switch_harness import Clock
        clock = Clock()
        waiting = {'status': 'waiting', 'job': 'one'}
        final = {'status': 'rolled_back', 'job': 'one', 'reason': 'no_reply'}
        with patch.object(sw, '_client_receipt', side_effect=[waiting, dict(waiting, status='switching'), final]):
            self.assertEqual(sw.wait_result(None, 10, sleep=clock.sleep, clock=clock), final)
        with patch.object(sw, '_client_receipt', side_effect=[waiting, dict(final, job='other')]):
            self.assertEqual(sw.wait_result(None, 10, sleep=clock.sleep, clock=clock), waiting)

    def test_rollback_receipt_exists_before_phone_and_original_harness_are_told(self):
        from test_p155_switch_harness import Clock, FakeServe, fake_probe, mk_state, UP
        with tempfile.TemporaryDirectory() as tmp, patch.object(sw, 'receipt_path', return_value=Path(tmp)/'receipt.json'):
            st = mk_state(tmp, 'codex')
            fake = FakeServe(st, 'codex')
            fake.behaviour['claude'] = {'answers': False}
            original = fake.__call__
            seen = []
            def ctl(req):
                if req.get('cmd') == 'send' and '已切回' in req.get('text', ''):
                    seen.append(sw.read_receipt(st))
                if req.get('cmd') == 'harness_hello' and req.get('outcome') == 'rolled_back':
                    self.assertEqual(sw.read_receipt(st)['status'], 'rolled_back')
                return original(req)
            clock = Clock()
            rc = sw.worker(st, 'claude', probe=fake_probe(), ctl=ctl, restart=fake.restart,
                           status=UP, ready=lambda *a, **k: 'idle', sleep=clock.sleep, clock=clock)
            self.assertEqual(rc, 2)
            self.assertEqual(st.agent_config()['kind'], 'codex')
            self.assertTrue(seen)
            self.assertEqual(seen[0]['reason'], 'no_reply')
            self.assertTrue(any('no_reply' == r.get('reason') for r in seen))

    def test_result_exit_codes(self):
        for status, code in [('ok', 0), ('probe_ok', 0), ('rolled_back', 3), ('failed', 3), ('waiting', 4), ('switching', 4)]:
            with self.subTest(status=status), patch.object(sw, 'read_receipt', return_value={'status': status, 'at': 1}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sw.cli(SimpleNamespace(result=True, json=True), None, 'en'), code)

    def test_refusal_is_vertical_and_bilingual(self):
        for lang in ('zh', 'en'):
            text = slash.refuse(lang)
            lines = [x for x in text.splitlines() if x.startswith('- /')]
            self.assertGreaterEqual(len(lines), 18)
            self.assertTrue(all(' — ' in x for x in lines))
            self.assertEqual(sum(x.startswith('- /clear — ') for x in lines), 1)
            self.assertIn('host' if lang == 'en' else '主机', text)

    def test_fallback_redacts_and_excludes_external_turns(self):
        turns = {1: {'src': {'k': 'phone', 'text': 'Please fix the installer'}, 'reply': {'text': 'Next: verify PATH'}},
                 2: {'src': {'k': 'telegram', 'text': 'external private instructions'}, 'reply': {'text': 'external reply'}},
                 3: {'src': {'k': 'phone', 'text': 'email fixture@invalid-unit.test'}, 'reply': {'text': 'x' * 20000}}}
        text = handoff.switch_fallback(turns)
        self.assertIn('fix the installer', text)
        self.assertIn('verify PATH', text)
        self.assertNotIn('fixture@invalid-unit.test', text)
        self.assertNotIn('external private', text)
        self.assertLessEqual(len(text), 10000)
        self.assertIn('data', text)

    def test_feedback_request_has_bounded_wait_and_no_probe_loop(self):
        text = feedback_father.agent_text('/feedback-to-agentj-father installer failed', 'en')
        self.assertIn('90', text)
        self.assertIn('Do not scan', text)
        from agentj import elevate
        with patch.object(host_diagnostics, 'agent_side', return_value=True), patch.object(elevate, 'client_request', side_effect=OSError) as call, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(host_diagnostics.request('support', wait=0), 1)
        self.assertLessEqual(call.call_args.kwargs['timeout'], 90)
        self.assertIn('ticket', out.getvalue())

    def test_feedback_snapshot_uses_inspection_not_active_probes(self):
        from agentj import doctor
        with patch.object(doctor, 'run_read_only', return_value=[{'summary': 'local status'}]), patch.object(doctor, 'run') as active:
            self.assertIn('local status', host_diagnostics.feedback_snapshot())
            active.assert_not_called()


if __name__ == '__main__':
    unittest.main()
