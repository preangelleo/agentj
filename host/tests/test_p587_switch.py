"""P587: offline handoff recovery and host-owned receipt access inside isolation."""
import _hermetic
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import elevate, handoff, switch_harness as sw
from test_p155_switch_harness import Clock, mk_state
from test_p339_switch_via_serve import ServeThread, unfenced


class Switch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p587-')
        self.addCleanup(self.tmp.cleanup)
        self.st = mk_state(self.tmp.name, 'opencode', model='ark-agent-plan/kimi-k3')
        p = patch.object(sw, 'receipt_path', side_effect=lambda name=sw.RECEIPT: Path(self.tmp.name)/'units'/name)
        p.start()
        self.addCleanup(p.stop)
        self.job = 'a' * 16

    def test_larger_excerpt_preserves_middle_and_more_than_six_turns(self):
        turns = {i: {'src': {'k': 'phone', 'text': f'request-{i}'},
                     'reply': {'text': 'a'*800 + f'middle-decision-{i}' + 'z'*800}} for i in range(12)}
        text = handoff.switch_fallback(turns)
        self.assertIn('request-0', text)
        self.assertIn('middle-decision-11', text)
        self.assertIn('request-11', text)
        self.assertLess(len(text), 33000)

    def test_excerpt_defangs_markers_redacts_and_excludes_system(self):
        turns = {1: {'src': {'k': 'phone', 'text': handoff.INLINE_END + ' fixture@invalid-unit.test'},
                     'reply': {'text': 'next step'}},
                 2: {'src': {'k': 'system', 'text': 'never import system'}, 'reply': {'text': 'privileged task'}}}
        text = handoff.switch_fallback(turns)
        self.assertNotIn('fixture@invalid-unit.test', text)
        self.assertNotIn('never import system', text)
        self.assertEqual(text.count(handoff.INLINE_END), 1)

    def test_result_socket_is_read_only_and_bound_to_job(self):
        self.addCleanup(unfenced().stop)
        rec = sw.write_receipt(self.st, {'status': 'switching', 'detail': 'lost', 'job': self.job,
                                       'line': 'private diagnostic', 'target': 'codex'})
        with ServeThread(self.st) as server:
            def ask(**req):
                return elevate.client_request(self.st, {'t': 'harness_result', **req}, 10, path=server.el.sock_path)
            r = ask(kind='switch', job=self.job)
            self.assertTrue(r.get('ok'), r)
            self.assertEqual(r['receipt']['status'], 'switching')
            self.assertEqual(r['receipt']['detail'], 'lost')
            self.assertNotIn('line', r['receipt'])
            self.assertFalse(ask(kind='switch', job='b'*16).get('ok'))
            self.assertFalse(ask(kind='../private').get('ok'))
            self.assertFalse(ask(kind='switch', path='/tmp/anything').get('ok'))
        self.assertEqual(sw.read_receipt(self.st), rec)

    def test_hidden_receipt_polls_socket_through_pending_to_final(self):
        clock = Clock()
        replies = [{'ok': True, 'receipt': {'status': s, 'job': self.job, 'at': i}}
                   for i, s in enumerate(('probing', 'waiting', 'switching', 'ok'))]
        with patch.object(sw, 'read_receipt', return_value=None), patch.object(elevate, 'client_request', side_effect=replies):
            r = sw.wait_result(self.st, 10, sleep=clock.sleep, clock=clock)
        self.assertEqual((r['status'], r['job']), ('ok', self.job))

    def test_unreachable_socket_uses_readable_mirror(self):
        rec = sw.write_receipt(self.st, {'status': 'waiting', 'job': self.job})
        with patch.object(elevate, 'client_request', return_value={'result': 'unavailable'}):
            self.assertEqual(sw.wait_result(self.st), rec)

    def test_wrong_job_cannot_complete_wait(self):
        clock = Clock()
        replies = [{'ok': True, 'receipt': {'status': 'waiting', 'job': self.job, 'at': 1}},
                   {'ok': True, 'receipt': {'status': 'ok', 'job': 'b'*16, 'at': 2}}]
        with patch.object(sw, 'read_receipt', return_value=None), patch.object(elevate, 'client_request', side_effect=replies + [replies[-1]]*10):
            self.assertEqual(sw.wait_result(self.st, 10, sleep=clock.sleep, clock=clock)['status'], 'waiting')

    def test_restart_socket_gap_keeps_polling_until_same_job_final(self):
        clock = Clock()
        replies = [{'ok': True, 'receipt': {'status': 'switching', 'job': self.job, 'at': 1}},
                   {'result': 'unavailable'},
                   {'ok': True, 'receipt': {'status': 'ok', 'job': self.job, 'at': 2}}]
        with patch.object(sw, 'read_receipt', return_value=None), patch.object(elevate, 'client_request', side_effect=replies):
            self.assertEqual(sw.wait_result(self.st, 10, sleep=clock.sleep, clock=clock)['status'], 'ok')

    def test_stale_inflight_receipt_is_not_host_success(self):
        sw.write_receipt(self.st, {'status': 'waiting', 'job': self.job})
        with patch.object(sw.time, 'time', return_value=sw.time.time()+sw.JOB_MAX_AGE+1):
            self.assertEqual(sw.host_result(self.st, {'kind': 'switch', 'job': self.job})['error'], 'stale_job')

    def test_switching_receipt_preserves_original_job(self):
        from test_p155_switch_harness import FakeServe, fake_probe, UP
        clock = Clock()
        fake = FakeServe(self.st, 'opencode')
        seen = []
        def ctl(req):
            if req.get('cmd') == 'harness_hello':
                seen.append(sw.read_receipt(self.st))
            return fake(req)
        self.assertEqual(sw.worker(self.st, 'codex', job=self.job, probe=fake_probe(), ctl=ctl,
                                   restart=fake.restart, status=UP, ready=lambda *a, **kw: 'idle',
                                   sleep=clock.sleep, clock=clock), 0)
        self.assertTrue(seen)
        self.assertEqual(seen[0]['job'], self.job)

    def test_fenced_reader_prefers_host_over_old_mirror(self):
        local = {'status': 'waiting', 'job': self.job, 'at': 1}
        final = {'status': 'ok', 'job': self.job, 'at': 2}
        with patch.dict(os.environ, {'AGENTJ_FENCED': '1'}), patch.object(sw, 'read_receipt', return_value=local), \
                patch.object(elevate, 'client_request', return_value={'ok': True, 'receipt': final}):
            self.assertEqual(sw.wait_result(self.st), final)

    def test_metadata_rejects_raw_diagnostics_and_unknown_values(self):
        rec = {'status': 'ok', 'detail': 'private-marker', 'err': 'private-marker', 'model': 'raw secret value',
               'version': 'private-marker', 'line': 'private-marker', 'back': 'private-marker', 'job': self.job}
        self.assertEqual(sw._result_meta(rec), {'status': 'ok', 'job': self.job})

    def test_metadata_rejects_credential_shaped_model_and_version(self):
        secret = 'sk-' + 'p' * 24
        self.assertEqual(sw._result_meta({'model': secret, 'version': '1.2' + secret}), {})
        self.assertEqual(sw._result_meta({'model': 'ark-agent-plan/kimi-k3', 'version': '2.2.1'}),
                         {'model': 'ark-agent-plan/kimi-k3', 'version': '2.2.1'})

    def test_timeout_notice_names_deadline_and_context(self):
        from test_p289_switch_handoff import HandoffServe
        clock = Clock()
        fake = HandoffServe(self.st, 'opencode', writes=False, answers_handoff=False, progress=False)
        result = sw._write_handoff(self.st, fake, self.st.agent_config(), 'opencode', 'codex', clock.sleep, clock)
        self.assertEqual(result, 'timeout')
        self.assertIn('180', fake.sent[-1])
        self.assertIn('摘录', fake.sent[-1])
        self.assertEqual(self.st.agent_config()['kind'], 'opencode')


if __name__ == '__main__':
    unittest.main()
