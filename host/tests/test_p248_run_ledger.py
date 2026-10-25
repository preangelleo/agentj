"""P248: crash cuts use killed isolated scheduler processes, never real tools or services."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import capability, tasks  # noqa: E402
from agentj.state import State  # noqa: E402
from test_controls import task_dir  # noqa: E402


class Host:
    def __init__(self, root):
        self.root = Path(root)
        self.st = State(self.root / 'st')
        self.st.root.mkdir(exist_ok=True)
        self.agent_cfg = {'dir': str(self.root / 'work'), 'kind': 'fake'}
        self.turn_lock = asyncio.Lock()
        self.agent = None
        self.task_label = self.brief_scope = None
        self.notices = []
        self.paused = False
        self.capabilities = capability.Capabilities(self)
        self.scheduler = tasks.Scheduler(self)

    def stopped(self):
        return self.paused

    def activity(self, *args, **kwargs):
        pass

    def task_finished(self, res):
        self.notices.append(res)


async def child(root, cut):
    h = Host(root)
    sch = h.scheduler
    rid = sch.request('daily', 'schedule', minute=tasks.minute_key('local'))
    h.st.write_private(h.st.root / 'rid', rid.encode())

    def mark():
        (h.root / 'cut').write_text(cut)

    async def execute(*args):
        # A fake publish/send: count the irreversible call independently of the ledger.
        p = h.root / 'calls'
        p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))
        if cut == 'running':
            mark()
            await asyncio.Event().wait()
        return 'VERDICT: ok — completed', 0

    sch._exec = execute
    if cut == 'queued':
        from agentj import diskq
        await diskq.aflush()
        mark()
        await asyncio.Event().wait()
    if cut == 'report':
        save = sch._save
        def save_cut(*args):
            out = save(*args)
            mark()
            while True:
                time.sleep(.1)
            return out
        sch._save = save_cut
    await sch.run('daily', 'schedule')


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p248-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wf = task_dir(self.root / 'work', 'daily', 'fake', mode='normal', schedule='* * * * *')
        self.h = Host(self.root)
        tasks.set_enabled(self.h.st, self.h.agent_cfg['dir'], 'daily', True, 'terminal')

    def test_crash_matrix_no_replay_and_report_survives(self):
        for cut in ('queued', 'running', 'report'):
            with self.subTest(cut=cut), tempfile.TemporaryDirectory(prefix='aj-p248-cut-') as raw:
                root = Path(raw)
                task_dir(root / 'work', 'daily', 'fake', mode='normal', schedule='* * * * *')
                h = Host(root)
                tasks.set_enabled(h.st, h.agent_cfg['dir'], 'daily', True, 'terminal')
                p = subprocess.Popen([sys.executable, __file__, '--child', str(root), cut],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 15
                    while not (root / 'cut').exists() and p.poll() is None and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((root / 'cut').exists(), p.stderr.read().decode() if p.poll() is not None else 'cut timeout')
                    p.kill()
                    p.wait(5)
                finally:
                    if p.poll() is None:
                        p.kill()
                    p.communicate(timeout=5)
                rid = (root / 'st' / 'rid').read_text()
                reopened = Host(root)
                reopened.scheduler.recover()
                rec = tasks.load(reopened.st)['runs'][rid]
                self.assertEqual(rec['status'], 'interrupted')
                self.assertEqual(rec['uncertain'], cut != 'queued')
                self.assertEqual(rec['verdict'], 'attention')
                self.assertEqual(rec['report_available'], cut == 'report')
                self.assertEqual(len(reopened.notices), 1)
                self.assertEqual(reopened.scheduler.waiting, [])
                reopened.scheduler._due()  # persisted minute fence: no second execution this minute
                self.assertEqual(reopened.scheduler.waiting, [])
                self.assertEqual(int((root / 'calls').read_text()) if (root / 'calls').exists() else 0,
                                 0 if cut == 'queued' else 1)
                last = tasks.rows(reopened.st, reopened.agent_cfg['dir'])[0]['last']
                self.assertEqual(last['run_id'], rid)
                reopened.scheduler.recover()
                Host(root).scheduler.recover()  # terminal states are never reclassified
                self.assertEqual(len(reopened.notices), 1)
                if cut == 'report':
                    self.assertIn('VERDICT: ok', (root / 'work' / rec['report']).read_text())
                    self.assertIn(rec['report'], rec['line'])

    def test_new_run_id_and_verdict_rules(self):
        async def go():
            h, s = self.h, self.h.scheduler
            seen = []
            async def exe(*args):
                rec = list(tasks.load(h.st)['runs'].values())[-1]
                self.assertEqual(rec['status'], 'running')
                seen.append(rec['run_id'])
                return 'no verdict', 0
            s._exec = exe
            for _ in range(2):
                r = await s.run('daily', 'manual')
                self.assertEqual(r['verdict'], 'fail')
                self.assertEqual(tasks.load(h.st)['runs'][r['run_id']]['status'], 'finished')
            self.assertNotEqual(*seen)
            self.assertEqual(len(list((self.wf / 'reports').glob('run-*.md'))), 2)
        asyncio.run(go())

    def test_contract_change_disable_and_estop_do_not_execute(self):
        async def go():
            s = self.h.scheduler
            s._exec = mock.AsyncMock(return_value=('VERDICT: ok — done', 0))
            rid = s.request('daily', 'dispatch')
            (self.wf / 'RUN.md').write_text('changed')
            await s.run('daily', 'dispatch')
            self.assertEqual(tasks.load(self.h.st)['runs'][rid]['status'], 'finished')
            self.assertIn('Contract changed', tasks.load(self.h.st)['last']['daily']['line'])
            tasks.set_enabled(self.h.st, s.workdir(), 'daily', True, 'terminal')
            s.request('daily', 'schedule')
            tasks.set_enabled(self.h.st, s.workdir(), 'daily', False, 'terminal')
            await s.run('daily', 'schedule')
            self.h.paused = True
            await s.run('daily', 'manual')
            self.assertEqual(tasks.load(self.h.st)['last']['daily']['status'], 'interrupted')
            s._exec.assert_not_awaited()
        asyncio.run(go())

    def test_contract_rechecked_after_waiting_for_chat(self):
        async def go():
            h, s = self.h, self.h.scheduler
            s._exec = mock.AsyncMock(return_value=('VERDICT: ok — done', 0))
            await h.turn_lock.acquire()
            job = asyncio.create_task(s.run('daily', 'manual'))
            await asyncio.sleep(.03)
            (self.wf / 'RUN.md').write_text('changed during wait')
            h.turn_lock.release()
            res = await job
            self.assertEqual(res['verdict'], 'fail')
            self.assertIn('Contract changed', res['line'])
            s._exec.assert_not_awaited()
        asyncio.run(go())

    def test_recovery_reaches_real_host_history(self):
        from test_l1 import _state, _host
        async def go():
            st = _state(str(self.root / 'real-host'))
            sent = []
            h = _host(st, sent)
            h.agent_cfg = dict(self.h.agent_cfg)
            rid = h.scheduler.request('daily')
            h2 = _host(st, [])
            h2.agent_cfg = dict(self.h.agent_cfg)
            h2.scheduler.recover()
            pages, _ = h2.hist.page()
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0]['src']['k'], 'task')
            self.assertIn('中断', json.dumps(pages[0], ensure_ascii=False))
            self.assertNotIn('VERDICT: ok', json.dumps(pages[0]))
            self.assertEqual(tasks.load(st)['last']['daily']['run_id'], rid)
        asyncio.run(go())

    def test_terminal_acceptance_and_uncertain_refusal(self):
        from test_l1 import _state, _host
        from agentj import diskq

        class Writer:
            def __init__(self):
                self.data = bytearray()
            def write(self, data):
                self.data.extend(data)
            async def drain(self):
                pass
            def close(self):
                pass

        async def go():
            st = _state(str(self.root / 'ctl-host'))
            h = _host(st, [])
            h.agent_cfg = dict(self.h.agent_cfg)

            async def request():
                reader, writer = asyncio.StreamReader(), Writer()
                reader.feed_data(b'{"cmd":"task_run","id":"daily"}\n')
                reader.feed_eof()
                await h.on_ctl(reader, writer)
                return json.loads(writer.data)

            with mock.patch.object(diskq, 'aflush', new=mock.AsyncMock(return_value=True)) as barrier:
                res = await request()
                self.assertTrue(res['ok'])
                rid = res['run_id']
                self.assertEqual(tasks.load(st)['runs'][rid]['status'], 'queued')
                barrier.assert_awaited_once()
            # The terminal reports a refusal instead of closing its control connection.
            tasks.update(st, lambda d: d['runs'][rid].update(status='running'))
            h = _host(st, [])
            h.agent_cfg = dict(self.h.agent_cfg)
            h.scheduler.recover()
            res = await request()
            self.assertFalse(res['ok'])
            self.assertEqual(res['error'], 'busy')
            self.assertIn('Old execution is uncertain', res['detail'])
            self.assertEqual(h.scheduler.waiting, [])
            tasks.set_enabled(st, h.agent_cfg['dir'], 'daily', True, 'terminal')
            with mock.patch.object(st, 'write_private', side_effect=OSError('disk full')):
                res = await request()
            self.assertFalse(res['ok'])
            self.assertEqual(res['error'], 'journal_unavailable')
            self.assertEqual(h.scheduler.waiting, [])
            with mock.patch.object(diskq, 'aflush', new=mock.AsyncMock(return_value=False)):
                res = await request()
            self.assertFalse(res['ok'])
            self.assertIn('inspect state before retrying', res['detail'])
        asyncio.run(go())

    def test_stop_queue_has_terminal_state(self):
        s = self.h.scheduler
        rid = s.request('daily')
        asyncio.run(s.stop_current())
        self.assertEqual(tasks.load(self.h.st)['runs'][rid]['status'], 'interrupted')
        self.assertEqual(s.waiting, [])

    def test_uncertain_effects_need_existing_owner_enable(self):
        h, s = self.h, self.h.scheduler
        rid = s.request('daily')
        tasks.update(h.st, lambda d: d['runs'][rid].update(status='running'))
        reopened = Host(self.root)
        reopened.scheduler.recover()
        self.assertTrue(tasks.uncertain_run(h.st, 'daily'))
        row = tasks.rows(h.st, s.workdir())[0]
        self.assertFalse(row['enabled'])
        self.assertFalse(row['stale'])
        self.assertTrue(row['uncertain'])
        with self.assertRaises(tasks.TaskError):
            reopened.scheduler.request('daily', 'dispatch')
        reopened.scheduler._due()
        self.assertEqual(reopened.scheduler.waiting, [])
        tasks.set_enabled(h.st, s.workdir(), 'daily', True, 'terminal')
        self.assertFalse(tasks.uncertain_run(h.st, 'daily'))
        new = reopened.scheduler.request('daily', 'manual')
        self.assertNotEqual(new, rid)
        self.assertEqual(tasks.load(h.st)['runs'][rid]['status'], 'interrupted')

    def test_stop_still_cancels_when_journal_write_fails(self):
        async def go():
            s = self.h.scheduler
            s.request('daily')
            s.current = asyncio.create_task(asyncio.sleep(60))
            await asyncio.sleep(0)
            with mock.patch.object(s, '_end_run', side_effect=OSError('disk full')):
                self.assertTrue(await s.stop_current())
            self.assertTrue(s.current.cancelled())
            self.assertEqual(s.waiting, [])
        asyncio.run(go())

    def test_atomic_acceptance_and_failure(self):
        s = self.h.scheduler
        with mock.patch.object(self.h.st, 'write_private', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                s.request('daily')
        self.assertEqual(s.waiting, [])
        self.assertEqual(s.queued, {})
        rid = s.request('daily')
        self.assertEqual(s.request('daily'), rid)
        self.assertEqual(len(tasks.load(self.h.st)['runs']), 1)
        self.assertEqual(self.h.st.tasks_path.stat().st_mode & 0o777, 0o600)

    def test_legacy_and_projection_and_retention(self):
        st = self.h.st
        st.tasks_path.write_text(json.dumps({'v': 1, 'on': {'daily': {'tsha': 'old'}},
                                             'ran': {'daily': 'old-minute'}, 'last': {'daily': {'verdict': 'ok'}}}))
        capability._write(st, 'dispatch.json', {'daily': {'status': 'queued', 'pid': os.getpid()}})
        self.assertEqual(tasks.load(st)['last']['daily']['verdict'], 'ok')
        self.h.scheduler.recover()
        d = tasks.load(st)
        self.assertEqual(d['ran']['daily'], 'old-minute')
        self.assertEqual(d['on']['daily']['tsha'], 'old')
        self.assertEqual(d['last']['daily']['verdict'], 'attention')
        self.assertTrue(tasks.uncertain_run(st, 'daily'))
        proj = capability._read(st, 'dispatch.json', {})
        self.assertEqual(proj['daily']['status'], 'interrupted')
        self.assertTrue(proj['daily']['uncertain'])  # current live PID is never proof
        def many(d):
            d['runs'] = {str(i): {'status': 'finished', 'ended_at': int(time.time())} for i in range(600)}
            d['runs']['old'] = {'status': 'interrupted', 'ended_at': 1}
            d['runs']['pending'] = {'status': 'queued'}
        tasks.update(st, many)
        self.assertEqual(len(tasks.load(st)['runs']), 501)
        self.assertIn('pending', tasks.load(st)['runs'])
        self.assertNotIn('old', tasks.load(st)['runs'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        asyncio.run(child(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) > 1 and sys.argv[1] == '--snapshot':
        with tempfile.TemporaryDirectory(prefix='aj-p248-shot-') as raw:
            root = Path(raw)
            wf = task_dir(root / 'work', 'daily', 'fake', mode='normal')
            h = Host(root)
            tasks.set_enabled(h.st, h.agent_cfg['dir'], 'daily', True, 'terminal')
            rid = h.scheduler.request('daily', 'dispatch')
            tasks.update(h.st, lambda d: d['runs'][rid].update(status='running'))
            h.scheduler._save(tasks.find(h.agent_cfg['dir'], 'daily'), 'VERDICT: ok — partial report', False, rid)
            h2 = Host(root)
            h2.lang = sys.argv[2] if len(sys.argv) > 2 else 'zh'
            h2.scheduler.recover()
            print(json.dumps(tasks.rows(h2.st, h2.agent_cfg['dir']), ensure_ascii=False))
    else:
        unittest.main()
