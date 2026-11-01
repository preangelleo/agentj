import _hermetic  # noqa: F401
import asyncio
import json
import os
import sys
import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from test_l1 import _host, _state
from agentj import file_show, owner_broker, owner_call, provenance
from agentj.peer_service import PeerService
from test_inline_media_stream import PNG

class FileShow(unittest.TestCase):
    def test_socket_native_completion_and_guards(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp) / 'work'; root.mkdir()
                image = root / 'a.png'; image.write_bytes(PNG)
                (root / 'link.png').symlink_to(image)
                (root / '.env').write_text('SYNTHETIC_TEST_VALUE=not-a-credential\n')
                secret_name = root / ('sk-' + 'or-v1-' + 'b' * 48 + '.png'); secret_name.write_bytes(PNG)
                outside = pathlib.Path(tmp) / 'outside.png'; outside.write_bytes(PNG)
                st = _state(tmp)
                with mock.patch('agentj.names.ctl_call', return_value=None):
                    st.set_agent_config('codex', str(root), fence=False)
                h = _host(st, []); h._post = lambda *a, **kw: None
                h.peers = PeerService(h)
                self.assertIsNone(h.peers.keys, 'default friends-off must work')
                page = h.hist_add({'k': 'you'}, '', 'open'); h.cur_turn = page['id']
                send = SimpleNamespace(turn=page['id'], source=provenance.phone())
                h.agent = SimpleNamespace(cur_send=send, native_source=provenance.phone())
                x = h.peers.xfer; x.begin_execution(send)
                notices = []; h.agent_notice = notices.append
                broker = x.broker
                server = await asyncio.start_unix_server(h.elevate.on_client, path=str(pathlib.Path(tmp) / 'socket'))
                async def socket(req):
                    r,w = await asyncio.open_unix_connection(str(pathlib.Path(tmp) / 'socket'))
                    w.write((json.dumps(req)+'\n').encode()); await w.drain()
                    result = json.loads(await r.readline()); w.close(); await w.wait_closed()
                    return result
                async def admitted(path, label='', expire=False):
                    command = 'agentj file show ' + str(path) + (' --label ' + label if label else '') + ' --json'
                    inp = {'command': command}
                    call = str(len(notices)) + str(path)
                    broker.register(call, 'Bash', inp, str(root))
                    verdict = await broker.execute('Bash', inp, call)
                    self.assertEqual(verdict['behavior'], 'allow')
                    req = file_show.request(str(path), label, str(root))
                    forged = await socket({**req, 'device': 'forged'})
                    self.assertEqual(forged['result'], 'failure')
                    env = {**os.environ, 'AGENTJ_ELEVATE_SOCK': str(pathlib.Path(tmp) / 'socket'),
                           'AGENTJ_STATE_DIR': str(st.root), 'PYTHONPATH': str(pathlib.Path(__file__).resolve().parents[1])}
                    proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'agentj.cli', 'file', 'show', str(path),
                               '--label', label, '--json', cwd=str(root), env=env,
                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    out, err = await asyncio.wait_for(proc.communicate(), 10)
                    self.assertEqual(proc.returncode, 0, err.decode())
                    queued = json.loads(out)

                    self.assertEqual(queued['result'], 'queued'); self.assertFalse(queued['client_downloaded'])
                    if expire:
                        entered, release = threading.Event(), threading.Event()
                        original = h.media.check
                        def wait_check(*args, **kw):
                            entered.set(); release.wait(2); return original(*args, **kw)
                        with mock.patch.object(h.media, 'check', side_effect=wait_check):
                            broker.complete(call, True, queued, 'Bash', inp, str(root))
                            await asyncio.to_thread(entered.wait, 2)
                            if expire == 'root': h.agent_cfg = {**h.agent_cfg, 'dir': str(root.parent)}
                            else: x.end_execution()
                            release.set(); await broker.drain()
                    else:
                        broker.complete(call, True, queued, 'Bash', inp, str(root))
                        await broker.drain()
                    return json.loads(notices[-1].removeprefix('FILE_SHOW_RESULT: '))
                try:
                    req = file_show.request(str(image), '', str(root))
                    self.assertEqual((await socket(req))['result'], 'failure')
                    self.assertEqual((await socket({**req, 'device': 'forged'}))['result'], 'failure')
                    attached = await admitted(image, 'preview')
                    self.assertEqual(attached['result'], 'attached_to_page'); self.assertFalse(attached['client_downloaded'])
                    self.assertEqual(page['end'], 'open'); self.assertEqual(len(page['media']), 1)
                    mid = attached['mid']
                    self.assertEqual((await admitted(image))['mid'], mid)
                    self.assertEqual(len(page['media']), 1)
                    h.media_later(page['id']); await asyncio.gather(*list(h.media_jobs))
                    self.assertEqual(page['media'][0]['mid'], mid)
                    for method in ('check', '_persist'):
                        for error in (OSError, RuntimeError):
                            before = json.dumps(page, sort_keys=True)
                            with mock.patch.object(h.media, method, side_effect=error('synthetic failure')):
                                failed = await admitted(image)
                            self.assertEqual(failed, {'result': 'failure', 'why': 'failed', 'client_downloaded': False})
                            self.assertEqual(json.dumps(page, sort_keys=True), before)
                    for path in (root / 'link.png', root / '.env', outside, secret_name):
                        result = await admitted(path)
                        self.assertEqual(result['result'], 'failure', str(path))
                    from agentj import history
                    h.hist_update(page['id'], text='x' * (history.PART_MAX - 10))
                    attached_long = await admitted(image)
                    self.assertNotEqual(attached_long['page'], page['id'])
                    self.assertEqual(h.cur_turn, attached_long['page'])
                    last = h.hist.get(h.cur_turn)
                    self.assertIn('[a.png]', last['reply']['text'])
                    self.assertEqual(last['media'][0]['mid'], mid)
                    h.media_later(h.cur_turn); await asyncio.gather(*list(h.media_jobs))
                    self.assertEqual(last['media'][0]['mid'], mid)
                    self.assertTrue(h.media.window(mid, 0))
                    with mock.patch('agentj.media.MAX_ITEMS', 0):
                        self.assertEqual((await admitted(root / 'a.png'))['why'], 'too_many')
                    with mock.patch('agentj.media.PAGE_BYTES', 0):
                        self.assertEqual((await admitted(image))['why'], 'too_big')
                    result = await admitted(image, expire='root')
                    self.assertEqual(result['result'], 'failure')
                    self.assertEqual(result['why'], 'expired')
                    h.agent_cfg = {**h.agent_cfg, 'dir': str(root)}
                    with mock.patch.object(h.media, 'check', side_effect=OSError('synthetic failure')):
                        result = await admitted(image, expire=True)
                    self.assertEqual(result['result'], 'failure')
                    self.assertEqual(result['why'], 'expired')
                    self.assertEqual((await socket(req))['result'], 'failure')
                    self.assertIsNone(owner_broker.parse('Bash', {'command': 'agentj file show a.png --device forged'}, str(root)))
                    self.assertIsNone(owner_broker.parse('Bash', {'command': 'agentj file show a.png; echo forged'}, str(root)))
                    self.assertEqual(len(page['media']), 1)
                finally:
                    server.close(); await server.wait_closed(); h.stopping.set()
        loop = asyncio.new_event_loop()
        try: loop.run_until_complete(run())
        finally: loop.close()
