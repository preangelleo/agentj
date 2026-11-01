import _hermetic  # noqa: F401
import asyncio
import pathlib
import tempfile
import unittest
from unittest import mock
from test_l1 import _host, _state

PNG = b'\x89PNG\r\n\x1a\n' + bytes(64)

class ActiveMedia(unittest.TestCase):
    def test_active_desktop_and_phone(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp) / 'work'; root.mkdir()
                (root / 'image.png').write_bytes(PNG)
                st = _state(tmp)
                with mock.patch('agentj.names.ctl_call', return_value=None):
                    st.set_agent_config('codex', str(root), fence=False)
                h = _host(st, [])
                h._post = lambda *a, **k: None
                for mode in ('desktop', 'phone'):
                    page = h.hist_add({'k':'agent'}, '', 'open')
                    if mode == 'desktop': h.desktop_turn = page['id']
                    else: h.cur_turn = page['id']
                    text = f'![scan]({root}/image.png)'
                    with mock.patch.object(h.media, 'scan_keys', wraps=h.media.scan_keys) as scan:
                        (h.desktop_text if mode == 'desktop' else h.agent_text)(text)
                        await asyncio.sleep(.3)
                        await asyncio.wait_for(asyncio.gather(*list(h.media_jobs)), 3)
                        self.assertTrue(scan.called, mode + ': no scan before task_complete')
                    current = h.hist.get(page['id'])
                    self.assertEqual(current['end'], 'open')
                    self.assertEqual(len(current.get('media', [])), 1)
                h.stopping.set()
                for task in asyncio.all_tasks():
                    if task is not asyncio.current_task(): task.cancel()
                await asyncio.sleep(.01)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

if __name__ == '__main__': unittest.main()

class ScanRegression(unittest.TestCase):
    def test_incremental_versions_race_and_failure(self):
        import threading
        from agentj import media_live
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp) / 'work'; root.mkdir()
                first = root / 'one.png'; first.write_bytes(PNG)
                second = root / 'two.png'; second.write_bytes(PNG + b'2')
                st = _state(tmp)
                with mock.patch('agentj.names.ctl_call', return_value=None):
                    st.set_agent_config('codex', str(root), fence=False)
                h = _host(st, []); h._post = lambda *a, **kw: None
                async def settle():
                    await asyncio.wait_for(asyncio.gather(*list(h.media_jobs)), 3)
                try:
                    for image in ('', '!'):
                        for tilde in (False, True):
                            with mock.patch.dict('os.environ', {'HOME': str(root)}):
                                p = h.hist_add({'k':'agent'}, '', 'open'); h.desktop_turn = p['id']
                                dest = '~/one.png' if tilde else str(first)
                                h.desktop_text(f'{image}[one]({dest})')
                                await settle()
                                self.assertEqual(len(p.get('media', [])), 1)
                    p = h.hist_add({'k':'agent'}, '', 'open'); h.desktop_turn = p['id']
                    h.desktop_text(f'![one]({first}')
                    await settle(); self.assertEqual(p.get('media', []), [])
                    h.desktop_text(')'); await settle()
                    mid = p['media'][0]['mid']
                    with mock.patch('agentj.media._hash_and_scan', wraps=__import__('agentj.media',fromlist=['_hash_and_scan'])._hash_and_scan) as hashed:
                        h.desktop_text(f'![one]({first})'); await settle()
                        self.assertEqual(p['media'][0]['mid'], mid)
                        self.assertEqual(hashed.call_count, 0)
                        h.desktop_text(f'[two]({second})'); await settle()
                        self.assertEqual(len(p['media']), 2)
                        for item in p['media']:
                            self.assertTrue(h.media.window(item['mid'], 0))
                        first.write_bytes(PNG + b'new')
                        h.desktop_text('new version'); await settle()
                        self.assertNotEqual(p['media'][0]['mid'], mid)
                    mids = [x['mid'] for x in p['media']]
                    h.desktop_end(); await settle()
                    self.assertEqual([x['mid'] for x in p['media']], mids)
                    self.assertEqual(media_live.explicit(f'```\n![one]({first})\n```'), '')
                    started, release = threading.Event(), threading.Event()
                    orig = h.media.scan_keys
                    def slow(*args):
                        started.set(); release.wait(2); return orig(*args)
                    p = h.hist_add({'k':'agent'}, '', 'open'); h.desktop_turn = p['id']
                    with mock.patch.object(h.media, 'scan_keys', side_effect=slow):
                        h.desktop_text(f'[one]({first})')
                        await asyncio.to_thread(started.wait, 2)
                        h.desktop_text(f'[two]({second})'); release.set()
                        await settle()
                    self.assertEqual(len(p['media']), 2)
                    p = h.hist_add({'k':'agent'}, '', 'open'); h.desktop_turn = p['id']
                    with mock.patch.object(h.media, 'scan_keys', side_effect=RuntimeError):
                        h.desktop_text(f'[one]({first})'); await settle()
                    self.assertEqual(p['media_skip'][0]['why'], 'failed')
                finally:
                    h.stopping.set()
        loop = asyncio.new_event_loop()
        try: loop.run_until_complete(run())
        finally: loop.close()
