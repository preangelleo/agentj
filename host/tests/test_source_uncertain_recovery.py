import _hermetic
import asyncio
import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace as N
import tempfile
import unittest
from unittest.mock import Mock, patch
from agentj import background_done, busy, provenance

class CompletionRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='source-recovery-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = self.root / 'report.md'
        self.report.write_text('VERDICT: attention — check result\n')
        self.a = N(status='idle', q=asyncio.Queue(), is_down=lambda:False, native_busy=lambda:False)
        self.a.submit = self.a.q.put_nowait
        self.h = N(st=N(root=self.root / 'state', log=Mock(), config=lambda:{'channel':'seat-a'}),
            lang='en', agent=self.a, agent_cfg={'dir':str(self.root)}, preferences={}, busy=busy.Board(self.root),
            stopped=lambda:False, _open_question=lambda:False, hist_add=Mock(return_value={'id':1}))
        self.n = background_done.Notifier(self.h)

    def finish(self, key, **kw):
        self.h.busy.finish({'key':key, 'label':key, 'since':100, 'report':str(self.report), **kw}, 7, 125)

    def test_missing_foreign_is_not_system_authority(self):
        self.finish('missing')
        self.n.collect()
        for key, value in self.n.pending.items():
            self.n.recovery.capture(key, value)
        send = self.n.tick(now=1000)
        self.assertEqual(provenance.normalize(send.source)['kind'], 'unknown')

    def test_mixed_batch_keeps_known_completion_system(self):
        self.finish('foreign', foreign=True)
        self.finish('known', foreign=False)
        send = self.n.tick(now=1000)
        self.assertEqual(provenance.normalize(send.source)['kind'], 'system')
        self.assertEqual(len(send.background_done_ids), 1)
        self.assertNotIn("'foreign'", send.text)

    def test_board_attests_real_trusted_launch_only(self):
        b = self.h.busy
        b.trusted = lambda:True
        b.start('own:1', 'worker', session='own')
        b.stop('own:1')
        self.assertIs(b.finished[-1].get('foreign'), False)
        b.start('own:2', 'worker', session='own', late=True)
        b.stop('own:2')
        self.assertIsNot(b.finished[-1].get('foreign'), False)

    def register(self, **kw):
        self.finish('original:job', **kw)
        self.n.collect()
        key, value = next(iter(self.n.pending.items()))
        self.n.recovery.capture(key, value)
        return key

    def test_missing_read_bound_report_once_then_replay_denied(self):
        key = self.register()
        result = self.n.recovery.recover(key)
        self.assertEqual(result['result'], 'reconciled')
        self.assertEqual(result['task_id'], 'original:job')
        self.assertFalse(result['action_replayed'])
        self.n.recovery.acknowledge(key)
        self.assertEqual(self.n.recovery.recover(key)['why'], 'already_reconciled')

    def test_foreign_does_not_get_read_capability(self):
        key = self.register(foreign=True)
        self.assertNotIn('report_sha256', self.n.recovery.rows[key])
        self.assertEqual(self.n.recovery.recover(key)['why'], 'reconcile_only')

    def test_report_drift_keeps_recovery_point(self):
        key = self.register()
        self.report.write_text('changed')
        self.assertEqual(self.n.recovery.recover(key)['result'], 'awaiting_owner_verification')
        self.assertEqual(self.n.recovery.rows[key]['state'], 'awaiting_owner_verification')

    def test_cross_seat_and_root_refused(self):
        key = self.register()
        self.h.st.config = lambda:{'channel':'seat-b'}
        self.assertEqual(self.n.recovery.recover(key)['why'], 'binding_changed')
        self.assertEqual(self.n.recovery.recover()['items'], [])
        self.assertEqual(self.n.recovery.notice(), '')

    def test_symlink_and_secret_targets_refused(self):
        for name in ('alias.md', '.env', 'secret.md'):
            with self.subTest(name=name):
                p = self.root / name
                if name == 'alias.md': p.symlink_to(self.report)
                elif name == '.env': p.write_text('ordinary')
                else: p.write_text('api_key=sk-live-1234567890abcdefghijklmno')
                with self.assertRaises((ValueError, OSError)):
                    self.n.recovery.read(str(p))

    def test_jarvis_report_allowed_but_other_hidden_data_refused(self):
        p = self.root / '.jarvis' / 'REPORT-example.md'
        p.parent.mkdir()
        p.write_text('Report says attention')
        self.assertIn(b'attention', self.n.recovery.read(str(p)))
        p = p.parent / 'private.md'
        p.write_text('private')
        with self.assertRaises(ValueError): self.n.recovery.read(str(p))

    def test_delivery_completion_preserves_unverified_recovery(self):
        key = self.register()
        send = self.n.tick(now=1000)
        self.n.complete(send)
        restored = background_done.Notifier(self.h)
        self.assertEqual(restored.recovery.rows[key]['state'], 'awaiting_owner_verification')
        self.assertIsNone(restored.recovery.rows[key]['original_error'])

    def test_actual_source_gate_error_and_child_request_retained(self):
        self.a.cur_send = N(source=provenance.phone())
        self.h.cur_turn = 17
        self.a.sid = 'host-native-session'
        with provenance.request_source(None):
            self.n.recovery.refusal('Read', {'path':'report.md'}, 'group_source', 'native-child')
        row = next(iter(self.n.recovery.rows.values()))
        self.assertEqual(row['original_error'], 'group_source')
        self.assertEqual(row['task_id'], 17)
        self.assertEqual(row['child_id'], 'native-child')
        self.assertNotIn('path', row['action'])
        self.assertEqual(row['recovery_point'], 'reconcile_only')

    def test_different_tasks_do_not_merge_original_identity(self):
        self.a.cur_send = N(source=None)
        for turn in (10, 11):
            self.h.cur_turn = turn
            self.n.recovery.refusal('Read', {'path':'report.md'}, 'group_source')
        self.assertEqual({r['task_id'] for r in self.n.recovery.rows.values()}, {10,11})

    def test_group_source_does_not_create_recovery_authority(self):
        self.a.cur_send = N(source=provenance.telegram_group(-123, 456, 'proxy', 456))
        self.n.recovery.refusal('Read', {'path':'report.md'}, 'group_source')
        self.assertFalse(self.n.recovery.rows)

    def test_disabled_notifications_preserve_unknown(self):
        self.h.preferences = {'background_done':{'enabled':False}}
        self.finish('unknown')
        self.n.collect()
        self.assertEqual(len(self.n.recovery.rows), 1)
        self.assertIsNone(self.n.tick(now=1000))

    def test_unprepared_unknown_does_not_queue_or_drop(self):
        self.finish('new')
        send = self.n.tick(now=1000)
        self.assertIsNotNone(send)
        key = send.background_done_ids[0]
        self.assertEqual(self.n.recovery.rows[key]['recovery_point'], 'reconcile_only')
        self.n.complete(send)
        self.assertIn(key, self.n.recovery.rows)

    def test_capacity_reclaims_only_reconciled_and_exposes_pending(self):
        from agentj import completion_recovery
        self.h.background_done = self.n
        key = self.register()
        book = self.n.recovery
        with patch('agentj.completion_recovery.MAX', 1):
            self.finish('overflow')
            self.n.collect()
            overflow_key = next(k for k in self.n.pending if k != key)
            book.capture(overflow_key, self.n.pending[overflow_key])
            self.assertNotIn(overflow_key, book.rows)
            self.assertIn('capacity_wait', [x['recovery_point'] for x in book.recover()['items']])
            self.assertTrue(book.notice())
            self.assertFalse(book.notice())
            book.recover(key)
            book.acknowledge(key)
            book.capture(overflow_key, self.n.pending[overflow_key])
            self.assertNotIn(key, book.rows)
            self.assertIn(overflow_key, book.rows)

    def test_scan_and_returned_bytes_must_match_even_with_same_metadata(self):
        from agentj import media
        original = media._hash_and_scan
        first = self.report.stat()
        def swap(fd, size, *args):
            result = original(fd, size, *args)
            self.report.write_bytes(b'X' * size)
            import os
            os.utime(self.report, ns=(first.st_atime_ns, first.st_mtime_ns))
            return result
        with patch.object(media, '_hash_and_scan', side_effect=swap):
            with self.assertRaises(ValueError): self.n.recovery.read(str(self.report))

    def test_corrupt_report_and_binding_shapes_preserve_evidence(self):
        key = self.register()
        original = dict(self.n.recovery.rows[key])
        for bad in ({'report':7}, {'binding':[]}, {'report_sha256':17}):
            with self.subTest(bad=bad):
                self.n.recovery.path.write_text(json.dumps({key:{**original, **bad}}))
                saved = self.n.recovery.path.read_bytes()
                other = type(self.n.recovery)(self.h)
                self.assertEqual(other.recover(key)['why'], 'corrupt_state')
                other.save()
                self.assertEqual(saved, self.n.recovery.path.read_bytes())

    def test_corrupt_rows_fail_closed_preserving_bytes(self):
        book = self.n.recovery
        book.path.parent.mkdir(parents=True)
        book.path.write_text('{"abc":17}')
        old = book.path.read_bytes()
        other = type(book)(self.h)
        self.assertEqual(other.recover()['why'], 'corrupt_state')
        other.save()
        self.assertEqual(book.path.read_bytes(), old)


class OwnerIngress(unittest.IsolatedAsyncioTestCase):
    async def test_real_paired_owner_native_slash_and_redacted_feedback_receipt(self):
        from unittest.mock import patch
        from agentj import serve, completion_recovery, host_diagnostics, support
        from agentj.state import State
        with tempfile.TemporaryDirectory(prefix='source-owner-') as td:
            st = State(Path(td)/'state')
            st.init()
            pub = b'paired-test-public'
            session = N(cid=1, state='ready', p33=True, pub=pub, device='test-owner', name='Owner', provenance=provenance.phone())
            h = N(st=st, sessions={1:session}, agent_cfg={'dir':td}, cmd_turn=Mock(return_value=9), cmd_card=Mock(),
                  agent=N(cur_send=N(source=None)), cur_turn=8)
            h.background_done = N(recovery=completion_recovery.Book(h))
            p = Path(td)/'report.md'
            p.write_text('VERDICT: attention — retained result')
            key='a'*64
            h.background_done.recovery.capture(key, {'task_id':'host-job', 'report_path':str(p), 'foreign':None})
            with patch.object(st, 'is_allowed', side_effect=lambda x:x == pub):
                turn = await serve.Host.on_slash(h, session, 'recover-background', key, False, True)
                self.assertEqual(turn, 9)
                result = json.loads(h.cmd_card.call_args.args[1].text)
                self.assertEqual(result['result'], 'reconciled')
                self.assertIn('retained result', result['report'])
                self.assertEqual(h.background_done.recovery.rows[key]['state'], 'reconciled')
                denied = N(**{**vars(session), 'cid':2})
                self.assertEqual((await completion_recovery.owner_command(h, denied, '/recover-background'))['why'], 'paired_owner_required')
                sent=[]
                class Channel:
                    signed=True
                    def __init__(self): self.st=st
                    def send(self, fields):
                        sent.append(fields)
                        return 201, {'thread':'st_'+'a'*22, 'seq':1}
                secret='sk-live-'+'a1b2c3d4'*6
                with provenance.paired_request(lambda:True), patch.object(support, 'channel_for', return_value=Channel()), \
                     patch.object(support.privacy, 'layer2', return_value=N(status='ok')), \
                     patch('agentj.report_privacy.privacy.local_identity', return_value=('', '')):
                    result = host_diagnostics.execute(st, {'op':'support', 'kind':'report',
                        'text':'Owner asks to report source failure. api_key='+secret, 'wait':0})
                self.assertIn('st_'+'a'*22, result['output'])
                self.assertNotIn(secret, result['output']+json.dumps(sent))
                self.assertEqual(len(sent), 1)
                self.assertTrue((st.root/'support'/'threads.json').exists())

if __name__ == '__main__': unittest.main()
