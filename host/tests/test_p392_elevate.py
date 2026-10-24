"""P392: the actual Host._app lane must answer while a real sudo child runs."""
import _hermetic  # noqa: F401,I001
import asyncio
import fcntl
import json
import os
import pathlib
import subprocess
import threading
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import elevate
from test_controls import sign
from test_f17 import PW, answer, fake_sudo, enroll
from test_l1 import Phone, _host, _ready, _state


class Channel(unittest.TestCase):
    def test_long_command_keeps_actual_phone_lane_responsive(self):
        async def go(root):
            st, sent = _state(str(root)), []
            host = _host(st, sent)
            ph = enroll(st, Phone(st))
            session = _ready(host, ph)
            session.p33 = True
            host.elevate.sudo = str(fake_sudo(root, PW))
            seconds = float(os.environ.get('P392_LONG_SECONDS', '0.8'))
            req = elevate.norm_sudo({'argv': [sys.executable, '-c', f'import time; time.sleep({seconds}); print("finished")'],
                                    'why': 'Verify a long backup operation', 'timeout': 120, 'cwd': str(root)})
            task = asyncio.create_task(host.elevate.request(req))
            while not sent:
                await asyncio.sleep(.01)
            card = next(obj for _, obj in sent if obj['t'] == 'elev')
            approved = answer(ph, card, True, PW)
            async def lane():
                # serve's receive loop awaits these serially; concurrent direct calls would hide the bug.
                await host._app(session, approved)
                await host._app(session, {'t': 'ping', 'r': 'p392-probe'})
                await host._app(session, {'t': 'msg', 'text': 'backup progress please'})
            accepted = []
            async def accept(*args, **kwargs):
                accepted.append(args[1])
            host._accept = accept
            start = time.monotonic()
            await lane()
            elapsed = time.monotonic() - start
            try:
                self.assertLess(elapsed, .4, 'sudo approval blocks serve receive lane and encrypted ping')
                self.assertFalse(task.done(), 'the command is still running')
                self.assertEqual(accepted, ['backup progress please'])
                self.assertTrue(any(obj['t'] == 'pong' for _, obj in sent))
                running = host.elevate.open_msgs(session)
                self.assertEqual(running[0]['state'], 'running')
                self.assertEqual(running[0]['ttl'], 0)
                # Replay cannot execute the consumed nonce, even while the child is running.
                await host._app(session, approved)
                while not task.done():
                    sent.clear()
                    await host._app(session, {'t': 'ping', 'r': 'p392-probe'})
                    self.assertTrue(any(obj['t'] == 'pong' for _, obj in sent))
                    await asyncio.sleep(.1)
                result = await task
                self.assertEqual((result['result'], result['code'], result['stdout']), ('done', 0, 'finished\n'))
                self.assertEqual(host.elevate.open_msgs(session), [])
                # Drop the live result, then reconnect: recovery is request-bound and output-free.
                sent.clear()
                await host.elevate.on_ready(session)
                done = [obj for _, obj in sent if obj['t'] == 'elev_done']
                self.assertEqual(done[-1]['id'], card['id'])
                self.assertEqual(done[-1]['code'], 0)
                self.assertNotIn('stdout', done[-1])
                sent.clear()
                await host._app(session, sign(ph, 'cards_get', {}, {'t': 'cards_get', 'r': 'p392-snapshot', 'have': [card['id']]}))
                for _ in range(100):
                    if any(obj['t'] == 'cards' for _, obj in sent):
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(any(obj['t'] == 'elev_done' and obj['id'] == card['id'] for _, obj in sent))
                self.assertNotIn(card['id'], next(obj['open'] for _, obj in sent if obj['t'] == 'cards'))
                self.assertNotIn(PW, json.dumps(sent))
                records = (root / 'sudo-record.jsonl').read_text().splitlines()
                self.assertEqual(len(records), 1)
            finally:
                await task
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(go(pathlib.Path(folder)))


class Bounds(unittest.TestCase):
    @unittest.skipUnless(hasattr(fcntl, 'F_SETPIPE_SZ'), 'Linux pipe-pressure fixture')
    def test_stdin_pressure_obeys_timeout_for_password_and_helper(self):
        from agentj import elevate_helper
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            sudo = root / 'not-reading-sudo'
            sudo.write_text(f'#!{sys.executable}\nimport time; time.sleep(1.2)\n')
            sudo.chmod(0o700)
            original = subprocess.Popen
            def small_pipe(*args, **kwargs):
                p = original(*args, **kwargs)
                fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
                return p
            for kind in ('password', 'helper'):
                with self.subTest(kind=kind), mock.patch.object(subprocess, 'Popen', small_pipe):
                    start = time.monotonic()
                    if kind == 'password':
                        result = elevate.run_sudo(['true'], bytearray(b'x' * elevate.MAX_VALUE), '/', .1, str(sudo))
                    else:
                        result = elevate_helper.run({'argv': ['x' * 4096] * 24}, .1, str(sudo))
                    self.assertEqual(result['result'], 'timeout')
                    self.assertLess(time.monotonic() - start, .7, 'stdin must share the process deadline')

    def test_timeout_kills_term_ignoring_child_and_caps_output(self):
        code = 'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.write(1,b"x"*900000); time.sleep(90)'
        p = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
        start = time.monotonic()
        out, err, timed_out, overflow = elevate.collect_command(p, .2)
        self.assertTrue(timed_out)
        self.assertTrue(overflow)
        self.assertLessEqual(len(out), elevate.MAX_OUT + 256)
        self.assertLess(time.monotonic() - start, 2)
        self.assertIsNotNone(p.poll())

    def test_worker_exception_resolves_and_wipes_password(self):
        async def go(root):
            st, sent = _state(str(root)), []
            host = _host(st, sent)
            ph = enroll(st, Phone(st))
            session = _ready(host, ph)
            req = elevate.norm_sudo({'argv': ['true'], 'why': 'Verify execution error handling'})
            request = asyncio.create_task(host.elevate.request(req))
            while not sent:
                await asyncio.sleep(.01)
            card = next(obj for _, obj in sent if obj['t'] == 'elev')
            values = []
            def fail(argv, value, *args):
                values.append(value)
                raise RuntimeError('private child failure text')
            with mock.patch.object(elevate, 'run_sudo', fail):
                await host._app(session, answer(ph, card, True, PW))
                result = await asyncio.wait_for(request, 2)
                if host.elevate.bg:
                    await asyncio.gather(*list(host.elevate.bg))
                await asyncio.sleep(0)  # run task completion callbacks before checking the strong-reference set
            self.assertEqual(result['result'], 'failed')
            self.assertTrue(all(v == bytearray(len(v)) for v in values))
            self.assertEqual(host.elevate.open_msgs(session), [])
            self.assertFalse(host.elevate.bg)
            self.assertNotIn('private child failure text', st.log_path.read_text())
            self.assertNotIn(PW, json.dumps(sent))
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(go(pathlib.Path(folder)))

    def test_stop_cancels_child_without_waiting_for_command_deadline(self):
        async def go(root):
            st, sent = _state(str(root)), []
            host = _host(st, sent)
            ph = enroll(st, Phone(st))
            session = _ready(host, ph)
            host.elevate.sudo = str(fake_sudo(root, PW))
            req = elevate.norm_sudo({'argv': [sys.executable, '-c', 'import time; time.sleep(90)'],
                                    'why': 'Verify shutdown cancels running child', 'timeout': 120})
            request = asyncio.create_task(host.elevate.request(req))
            while not sent:
                await asyncio.sleep(.01)
            card = next(obj for _, obj in sent if obj['t'] == 'elev')
            await host._app(session, answer(ph, card, True, PW))
            await asyncio.sleep(.1)
            await asyncio.wait_for(host.elevate.stop(), 2)
            self.assertEqual((await request)['result'], 'timeout')
            self.assertFalse(host.elevate.bg)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(go(pathlib.Path(folder)))


if __name__ == '__main__':
    unittest.main()
