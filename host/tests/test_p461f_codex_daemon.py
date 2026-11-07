"""#461 field evidence (0.26.8a2, customer on Linux, Codex CLI 0.160.0, shared daemon 0.166.0): host.log showed
「desktop turn active; alternate turns」 → 「desktop turn did not finish」 again and again, plus `bg_task_failed why=cmd_detect
exception_type=TypeError`.

Measured on codex-cli 0.166.0 (isolated CODEX_HOME, fake Responses server; ~/.cache/rel0268a2/tmp/461f/scripts): `codex
app-server daemon stop` with a turn running ends the daemon hard after ~60 s and leaves `task_started` without
`turn_aborted`; a daemon that later resumes that thread keeps the rollout OPEN while idle (also after its client left) and
answers `thread/read` status `idle`. The earlier #461 check (no process holds the rollout) therefore never fired: the
daemon holds it. Here the daemon is asked; its answer decides, conservatively.

The fake daemon below is a separate process that holds the rollout open and serves the measured WebSocket JSON-RPC shape
on `$CODEX_HOME/app-server-control/app-server-control.sock` (synthetic data only)."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import agent as agents  # noqa: E402
from agentj import codex_daemon, doctor, shared_codex  # noqa: E402
from agentj.serve import Host  # noqa: E402
from agentj.shared_codex import SharedCodexAgent, Refusal  # noqa: E402

SID = '46146146-1461-4614-6146-1461461461f0'
OLD = time.time() - 3 * 3600

FAKE_DAEMON = r'''
import base64, hashlib, json, os, socket, struct, sys
rollout, sock_path, status, agent = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
hold = open(rollout, 'rb') if rollout != '-' else None      # a loaded thread keeps its rollout open (measured)
os.makedirs(os.path.dirname(sock_path), exist_ok=True)
srv = socket.socket(socket.AF_UNIX); srv.bind(sock_path); srv.listen(4)
print('ok', flush=True)
def take(c, buf, n):
    while len(buf) < n:
        d = c.recv(65536)
        if not d: raise EOFError
        buf += d
    return buf[:n], buf[n:]
def send(c, obj):
    data = json.dumps(obj).encode(); n = len(data)
    c.sendall(bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack('>H', n)) + data)
while True:
    c, _ = srv.accept(); buf = b''
    try:
        while b'\r\n\r\n' not in buf: buf += c.recv(4096)
        head, buf = buf.split(b'\r\n\r\n', 1)
        key = [l.split(b':', 1)[1].strip() for l in head.split(b'\r\n') if l.lower().startswith(b'sec-websocket-key')][0]
        acc = base64.b64encode(hashlib.sha1(key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest()).decode()
        c.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                   'Sec-WebSocket-Accept: %s\r\n\r\n' % acc).encode())
        init = False
        while True:
            h, buf = take(c, buf, 2); n = h[1] & 127
            if n == 126: x, buf = take(c, buf, 2); n = struct.unpack('>H', x)[0]
            mask, buf = take(c, buf, 4); data, buf = take(c, buf, n)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if h[0] & 15 == 8: break
            m = json.loads(data)
            if 'id' not in m: continue
            if m['method'] == 'initialize':
                init = True
                send(c, {'id': m['id'], 'result': {'userAgent': '%s/0.166.0 (Linux; x86_64) unknown (x; 0)' % agent,
                                                   'platformFamily': 'unix'}})
            elif not init:
                send(c, {'id': m['id'], 'error': {'code': -32600, 'message': 'Not initialized'}})
            elif m['method'] == 'thread/read' and status == 'notLoaded':
                send(c, {'id': m['id'], 'error': {'code': -32600, 'message': 'thread not loaded: ' + m['params']['threadId']}})
            elif m['method'] == 'thread/read':
                st = {'type': status, 'activeFlags': []} if status == 'active' else {'type': status}
                send(c, {'id': m['id'], 'result': {'thread': {'id': m['params']['threadId'], 'status': st}}})
            else:
                send(c, {'id': m['id'], 'error': {'code': -32601, 'message': 'unexpected ' + m['method']}})
    except (EOFError, OSError, IndexError):
        pass
    c.close()
'''


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='q', dir=TEST_TMP)      # AF_UNIX path ≤ 108 bytes
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home, self.proj = root / 'c', root / 'p'
        self.proj.mkdir()
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.host = Mock()
        self.host.st.agent_session.return_value = None
        self.host.lang = 'zh'
        self.host.preferences = None
        self.calls = []

    def rollout(self, mtime=OLD):
        d = self.home / 'sessions' / '2026' / '11' / '04'
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'rollout-2026-11-04T11-38-00-{SID}.jsonl'
        items = [{'type': 'session_meta', 'payload': {'id': SID, 'cwd': str(self.proj)}},
                 {'type': 'turn_context', 'payload': {'cwd': str(self.proj), 'approval_policy': 'never',
                  'approvals_reviewer': 'user', 'sandbox_policy': {'type': 'danger-full-access'}}},
                 {'type': 'event_msg', 'payload': {'type': 'task_started'}},
                 {'type': 'event_msg', 'payload': {'type': 'item_completed'}}]
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def daemon(self, path, status, agent='codex-tui'):
        p = subprocess.Popen([sys.executable, '-c', FAKE_DAEMON, str(path) if path else '-',
                              str(codex_daemon.socket_path()), status, agent], stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), b'ok')
        return p

    def other_holder(self, path):
        p = subprocess.Popen([sys.executable, '-c', 'import sys,time\nf=open(sys.argv[1],"rb")\nprint("ok",flush=True)\n'
                              'time.sleep(60)', str(path)], stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), b'ok')
        return p

    def agent(self):
        a = SharedCodexAgent(self.host, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                         'shared_session_id': SID})

        async def call(method, params, timeout=60):
            self.calls.append((method, params))
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'approvalPolicy': params.get('approvalPolicy', 'never'),
                        'approvalsReviewer': params.get('approvalsReviewer', 'user')}
            if method == 'hooks/list':
                return {'data': []}
            raise AssertionError(method)
        a.call = AsyncMock(side_effect=call)
        a.turn_done = asyncio.Event()
        return a

    def notices(self):
        return ' '.join(c.args[0] for c in self.host.agent_notice.call_args_list)

    def events(self, name):
        return [c.kwargs for c in self.host.emit.call_args_list if c.args and c.args[0] == name]


@unittest.skipUnless(sys.platform.startswith('linux'), 'holders are read from /proc; the peer pid from SO_PEERCRED')
class DaemonHeldTurn(Base):
    async def test_idle_daemon_holding_the_open_turn_delivers(self):
        path = self.rollout()
        self.daemon(path, 'idle')
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 3):
            await asyncio.wait_for(a._thread(), 10)
        self.assertEqual(self.calls[-1][0], 'thread/resume')
        self.assertEqual(self.events('shared_orphan_turn')[0]['reason'], 'daemon shows no running turn')
        self.assertNotIn('等它说完', self.notices())
        self.assertIn('后台服务', self.notices())
        self.assertFalse(a.desktop_wait)

    async def test_idle_daemon_refusing_the_writer_names_daemon_stop(self):
        """Measured 0.166.0: the daemon that keeps the idle thread loaded answers every other app-server's thread/resume with
        「already has an active writer」. The phone gets that fact and the one command that frees it, not an editor guess."""
        from agentj.agent_codex import RPCError
        path = self.rollout()
        d = self.daemon(path, 'idle')
        a = self.agent()

        async def call(method, params, timeout=60):
            if method == 'thread/resume':
                raise RPCError({'code': -32600, 'message': f'thread {SID} already has an active writer'})
            return {'data': []}
        a.call = AsyncMock(side_effect=call)
        a.find_writer = AsyncMock(return_value={'kind': 'app_server', 'pid': d.pid, 'how': 'fd'})
        a.read_desktop = Mock()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 3), self.assertRaises(Refusal) as r:
            await asyncio.wait_for(a._thread(), 10)
        self.assertEqual(r.exception.code, 'desktop writer active')
        self.assertIn('codex app-server daemon stop', a.writer_text())
        self.assertIn(str(d.pid), a.writer_text())

    async def test_daemon_that_has_not_loaded_the_thread_delivers(self):
        path = self.rollout()
        self.daemon(path, 'notLoaded')
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 3):
            await asyncio.wait_for(a._thread(), 10)
        self.assertEqual(self.calls[-1][0], 'thread/resume')

    async def test_active_turn_in_the_daemon_still_waits(self):
        path = self.rollout()
        self.daemon(path, 'active')
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 1):
            with self.assertRaises(Refusal) as r:
                await asyncio.wait_for(a._thread(), 10)
        self.assertEqual(r.exception.code, 'desktop turn did not finish')
        self.assertFalse(self.events('shared_orphan_turn'))

    async def test_another_holder_besides_the_daemon_still_waits(self):
        path = self.rollout()
        self.daemon(path, 'idle')
        self.other_holder(path)              # e.g. a terminal codex with its own in-process server
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 1):
            with self.assertRaises(Refusal):
                await asyncio.wait_for(a._thread(), 10)
        self.assertFalse(self.events('shared_orphan_turn'))

    async def test_fresh_rollout_is_never_asked_about(self):
        path = self.rollout(mtime=None)
        self.daemon(path, 'idle')
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 1), patch.object(codex_daemon, 'thread_status') as ask:
            with self.assertRaises(Refusal):
                await asyncio.wait_for(a._thread(), 10)
        ask.assert_not_called()

    async def test_holder_without_a_daemon_socket_still_waits(self):
        path = self.rollout()
        self.other_holder(path)
        self.assertIsNone(codex_daemon.thread_status(SID))
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 1):
            with self.assertRaises(Refusal):
                await asyncio.wait_for(a._thread(), 10)

    async def test_clear_on_a_daemon_dead_turn_clears(self):
        path = self.rollout()
        self.daemon(path, 'idle')
        a = self.agent()
        a.rollout, a.rollout_sid = path, SID
        a.drop_conversation = AsyncMock()
        with patch('agentj.shared_codex.shared_pin.write'):
            res = await a.cmd_clear('')
        self.assertNotIn('没清空', res.text)
        self.assertTrue(a.cleared)

    async def test_clear_on_a_live_daemon_turn_is_still_refused(self):
        # A live turn is fresh; OLD deliberately models the separate stale-healing cases.
        path = self.rollout(mtime=None)
        self.daemon(path, 'active')
        a = self.agent()
        a.rollout, a.rollout_sid = path, SID
        res = await a.cmd_clear('')
        self.assertIn('没清空', res.text)

    async def test_stop_on_daemon_dead_turn_does_not_send_owner_to_esc(self):
        path = self.rollout()
        self.daemon(path, 'idle')
        a = self.agent()
        a.rollout, a.rollout_sid = path, SID
        a.status, a.phone_turn = 'idle', False
        a.interrupt_request = AsyncMock()
        await a.halt(clear_queue=True)
        self.assertNotIn('按 Esc', self.notices())


class ClearPreemptsWait(Base):
    async def test_clear_ends_the_wait_and_runs_first(self):
        """#461 12:44: /clear sent while a message waits behind a desktop turn must not queue behind it."""
        a = self.agent()
        a.desktop_wait, a.phone_turn = True, False
        a.status = 'working'
        a.q.put_nowait('an earlier queued message')
        cmd = agents.Cmd('clear', '', 'phone', 7)
        a.submit(cmd)
        self.assertTrue(a.turn_done.is_set(), 'the wait is ended (wait_desktop raises Stopped)')
        self.assertIs(a.q.get_nowait(), cmd, '/clear runs before what queued behind the dead wait')
        self.assertIn('撤回', self.notices())

    async def test_clear_without_a_wait_queues_as_before(self):
        a = self.agent()
        a.desktop_wait = False
        a.q.put_nowait('m')
        cmd = agents.Cmd('clear', '', 'phone', 7)
        a.submit(cmd)
        self.assertFalse(a.turn_done.is_set())
        self.assertEqual(a.q.get_nowait(), 'm')

    async def test_other_commands_still_queue_behind_the_wait(self):
        a = self.agent()
        a.desktop_wait = True
        a.submit(agents.Cmd('model', 'x', 'phone', 7))
        self.assertFalse(a.turn_done.is_set())


@unittest.skipUnless(sys.platform.startswith('linux'), 'SO_PEERCRED')
class Probe(Base):
    def test_status_and_version_over_the_socket(self):
        self.daemon(None, 'idle')
        st = codex_daemon.thread_status(SID)
        self.assertEqual(st['status'], 'idle')
        self.assertIsInstance(st['pid'], int)
        self.assertEqual(codex_daemon.server_version(), '0.166.0')

    def test_no_socket_is_unknown(self):
        self.assertIsNone(codex_daemon.thread_status(SID))
        self.assertIsNone(codex_daemon.server_version())

    def test_a_plain_file_is_never_connected(self):
        p = codex_daemon.socket_path()
        p.parent.mkdir(parents=True)
        p.write_text('x')
        self.assertIsNone(codex_daemon.thread_status(SID))

    def test_silent_server_times_out_as_unknown(self):
        import socket
        p = codex_daemon.socket_path()
        p.parent.mkdir(parents=True)
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(p))
        s.listen(1)
        self.addCleanup(s.close)
        t0 = time.monotonic()
        self.assertIsNone(codex_daemon.thread_status(SID, timeout=0.5))
        self.assertLess(time.monotonic() - t0, 3)


class DoctorVersionRow(Base):
    def cfg(self):
        st = Mock()
        st.exists.return_value = True
        st.agent_config.return_value = {'kind': 'codex', 'dir': str(self.proj), 'session_mode': 'shared'}
        return st

    def release(self, version):
        rel = self.home / 'packages' / 'app-server-daemon' / 'releases' / f'{version}-x86_64-unknown-linux-musl'
        rel.mkdir(parents=True)
        (self.home / 'packages' / 'app-server-daemon' / 'current').symlink_to(rel)

    def test_mismatch_is_named_with_a_fix(self):
        self.release('0.166.0')
        with patch.object(doctor, '_codex_cli_version', return_value='0.160.0'):
            row = doctor.check_codex_daemon(self.cfg(), {})
        self.assertEqual(row['id'], 'codex_daemon')
        self.assertEqual(row['status'], doctor.WARN)
        for s in ('0.160.0', '0.166.0', '版本不一致', 'versions differ'):
            self.assertIn(s, row['summary'])
        self.assertIn('codex app-server daemon stop', row['hint'])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'SO_PEERCRED')
    def test_running_daemon_version_wins_over_the_link(self):
        self.release('0.165.0')
        self.daemon(None, 'idle')
        with patch.object(doctor, '_codex_cli_version', return_value='0.166.0'):
            row = doctor.check_codex_daemon(self.cfg(), {})
        self.assertEqual(row['status'], doctor.OK)
        self.assertIn('0.166.0', row['summary'])

    def test_same_version_is_ok_and_no_daemon_is_no_row(self):
        with patch.object(doctor, '_codex_cli_version', return_value='0.166.0'):
            self.assertIsNone(doctor.check_codex_daemon(self.cfg(), {}))
            self.release('0.166.0')
            self.assertEqual(doctor.check_codex_daemon(self.cfg(), {})['status'], doctor.OK)

    def test_other_harness_has_no_row(self):
        st = self.cfg()
        st.agent_config.return_value = {'kind': 'claude', 'dir': str(self.proj)}
        self.release('0.166.0')
        self.assertIsNone(doctor.check_codex_daemon(st, {}))


class CmdDetect(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_command_detection_sends_its_state(self):
        """host.log: `bg_task_failed why=cmd_detect exception_type=TypeError` — `_send_ready` takes a function of the
        session, the detection passed the dict itself ('dict' object is not callable) once a phone was ready."""
        h = types.SimpleNamespace()
        h.st = Mock()
        h.st.is_allowed.return_value = True
        h.sessions = {'c1': types.SimpleNamespace(state='ready', pub='k')}
        h.send_app = AsyncMock()
        h._cmdrun_detecting = True
        h._send_ready = types.MethodType(Host._send_ready, h)
        with patch('agentj.cmdrun.available_commands', return_value=['doctor']), \
                patch('agentj.cmdrun.command_harnesses', return_value=[]) as facts:
            await Host._detect_terminal_commands(h)
        facts.assert_called_once_with(detect=True)
        h.send_app.assert_awaited_once()
        self.assertEqual(h.send_app.await_args.args[1], {'t': 'cmds_state', 'names': ['doctor'], 'harnesses': []})
        self.assertFalse(h._cmdrun_detecting)


if __name__ == '__main__':
    unittest.main()
