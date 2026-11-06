"""#550: intentional-stop grace is independent of completion batching and grants."""
import _hermetic  # noqa: F401
import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from agentj import busy, background_done


def label(s):
    return hashlib.sha256(s.encode()).hexdigest()


class Restart(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        agent = SimpleNamespace(status='idle', q=asyncio.Queue(), is_down=lambda:False, native_busy=lambda:False)
        agent.submit = agent.q.put_nowait
        self.h = SimpleNamespace(st=SimpleNamespace(root=self.root, log=Mock()), lang='zh', agent=agent,
            busy=busy.Board(self.root), preferences={}, stopped=lambda:False, _open_question=lambda:False,
            hist_add=Mock(return_value={'id':1}))
        self.n = background_done.Notifier(self.h)

    def end(self, key='run:old', reason='sigterm', name='任务的完整名字', unit=None, since=100, started=100.25, ended=200, code=-15):
        v = busy.item(key, '掌枢', 'ceo', name, since=since)
        v.update(foreign=False, started=started, restart_label=label(name))
        if reason:
            v['stop_reason'] = reason
        if unit:
            v['unit'] = unit
        self.h.busy.finish(v, code, ended)
        return v

    def start(self, name='任务的完整名字', unit=None, at=220, key='run:new'):
        v = busy.item(key, '掌枢', 'ceo', name, since=at)
        v.update(started=at, restart_label=label(name), foreign=False)
        if unit:
            v['unit'] = unit
        self.h.busy.outside = [v]
        return v

    def test_stop_then_same_label_restart_is_silent(self):
        self.end()
        self.assertIsNone(self.n.tick(now=200))
        self.start()
        self.assertIsNone(self.n.tick(now=220))
        self.assertFalse(self.n.pending)
        self.assertTrue(self.h.agent.q.empty())
        self.assertFalse(self.n.recovery.rows)

    def test_stop_without_restart_notifies_at_sixty_seconds(self):
        self.end()
        self.assertIsNone(self.n.tick(now=259.999))
        self.assertIsNotNone(self.n.tick(now=260))

    def test_same_unit_new_label_suppresses(self):
        self.end(unit='agentj-p550.service')
        self.start(name='追加要求后的名字', unit='agentj-p550.service')
        self.assertIsNone(self.n.tick(now=261))
        self.assertFalse(self.n.pending)

    def test_collision_and_out_of_window_do_not_suppress(self):
        for name, at in [('任务的完整名字改了', 220), ('任务的完整名字', 260.001), ('任务的完整名字', 199.999)]:
            with self.subTest(name=name,at=at):
                self.setUp()
                self.end()
                self.start(name=name, at=at)
                self.assertIsNotNone(self.n.tick(now=261))

    def test_exact_sixty_second_restart_suppresses(self):
        self.end()
        self.start(at=260)
        self.assertIsNone(self.n.tick(now=261))
        self.assertFalse(self.n.pending)

    def test_timeout_failure_unknown_exit_and_legacy_always_notify(self):
        for reason, code in [('timeout',-15), (None,1), (None,None), (None,-9), (None,124)]:
            with self.subTest(reason=reason,code=code):
                self.setUp()
                self.end(reason=reason,code=code)
                self.start()
                self.assertIsNotNone(self.n.tick(now=220))

    def test_restart_finished_between_polls(self):
        self.end()
        self.end(key='run:new', reason=None, started=210, since=210, ended=211, code=0)
        send = self.n.tick(now=250)
        self.assertEqual(len(send.background_done_ids),1)
        self.assertIn('退出码 0', send.text)
        self.assertNotIn('退出码 -15', send.text)

    def test_pending_and_observed_start_survive_serve_restart(self):
        self.end()
        self.n.tick(now=200)
        self.n = background_done.Notifier(self.h)
        self.start(at=230)
        self.n.collect()
        self.n = background_done.Notifier(self.h)
        self.h.busy.outside = []
        self.assertIsNone(self.n.tick(now=261))
        self.assertFalse(self.n.pending)
        self.assertEqual(self.n.path.stat().st_mode & 0o777, 0o600)

    def test_receipt_keeps_exact_identity_and_stop_reason(self):
        v = self.end()
        busy.write_finished(self.root, v, -15)
        got = busy.finished_items(self.root)[0]
        self.assertEqual(got['restart_label'],label('任务的完整名字'))
        self.assertEqual(got['started'],100.25)
        self.assertEqual(got['stop_reason'],'sigterm')

    def test_unit_timeout_vs_sigterm_metadata(self):
        for result, reason in [('timeout','timeout'),('success','sigterm'),('signal',None)]:
            with self.subTest(result=result):
                run = Mock(return_value=SimpleNamespace(returncode=0, stdout='ActiveState=inactive\nLoadState=loaded\nExecMainCode=2\nExecMainStatus=15\nResult='+result+'\n'))
                self.assertEqual(busy.unit_end('agentj-p550.service',run).get('stop_reason'),reason)

    def test_duplicate_receipt_not_followed_by_unknown_disappearance(self):
        v = self.end()
        self.h.busy.finished.clear()
        self.h.busy.outside = [v]
        busy.write_finished(self.root,v,-15)
        self.h.busy.refresh_outside(units=False)
        self.assertEqual(len(self.h.busy.finished),1)
        self.assertEqual(self.h.busy.finished[0]['stop_reason'],'sigterm')

    def test_non_sigterm_exit_never_suppresses(self):
        for code in (1, 7, 124, 137):
            with self.subTest(code=code):
                self.setUp()
                self.end(code=code)
                self.start()
                self.assertIsNotNone(self.n.tick(now=220))

    def test_foreign_replacement_cannot_hide_known_stop(self):
        self.end()
        self.start()['foreign'] = True
        self.assertIsNone(self.n.tick(now=220))
        self.assertIsNotNone(self.n.tick(now=261))

    def test_host_service_cgroup_is_not_a_shared_dispatch_unit(self):
        with patch.object(busy, '_own_cg', return_value='/user@1000.service/app.slice/agentj.service'):
            self.assertIsNone(busy.current_unit())

    def test_graceful_zero_requires_wrapper_signal_evidence(self):
        self.end(code=0)
        self.start()
        self.assertIsNotNone(self.n.tick(now=220))
        self.setUp()
        self.end(code=0)
        self.h.busy.finished[-1]['stop_signal'] = 15
        self.start()
        self.assertIsNone(self.n.tick(now=261))
        self.assertFalse(self.n.pending)

    def test_verified_launch_freezes_identity_across_restart(self):
        from agentj.busy_launch import Launch
        launch = Launch(self.root)
        launch.jobs['run:new'] = dict(who='掌枢',label='标题',report='',restart_label=label('标题'),unit='agentj-p550.service',started=220)
        launch.dirty = True
        launch.save()
        launch = Launch(self.root)
        self.h.busy.launch = launch
        self.start(name='later rewrite',at=999)['unit'] = 'forged.service'
        self.n.collect()
        observed = self.n.starts['run:new']
        self.assertEqual(observed['restart_label'],label('标题'))
        self.assertEqual(observed['unit'],'agentj-p550.service')
        self.assertEqual(observed['started'],220)

    def test_verified_launch_does_not_trust_later_identity_on_legacy_job(self):
        from agentj.busy_launch import Launch
        launch = Launch(self.root)
        launch.jobs['run:new'] = dict(who='掌枢',label='标题',report='')
        self.h.busy.launch = launch
        self.start(name='later rewrite',at=999)['unit'] = 'forged.service'
        self.n.collect()
        observed = self.n.starts['run:new']
        self.assertNotIn('unit',observed)
        self.assertNotIn('restart_label',observed)
