import _hermetic  # noqa: F401
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from agentj.serve import Host
from agentj import asr


class VoiceInstall(unittest.IsolatedAsyncioTestCase):
    async def run_install(self, installer, state='ready', send=None):
        host = SimpleNamespace(asr=SimpleNamespace(install=installer),
            st=SimpleNamespace(root=Path('/tmp/p387-asr-test'), log=mock.Mock()),
            send_app=send or mock.AsyncMock(), asr_state=lambda:state)
        host._bg = lambda coro, what='': Host._bg(host, coro, what)
        await Host.on_p33(host, SimpleNamespace(device='paired-test-device'), 'asr_install', {'r':'p387-asr'})
        for _ in range(100):
            if not host._asr_installing and any(c.args[1]['t'] == 'asr_install_res' for c in host.send_app.call_args_list):
                return host
            await asyncio.sleep(.01)
        self.fail('installer did not finish')

    async def test_progress_is_bounded_request_scoped_and_precedes_ready(self):
        delivered = {stage: threading.Event() for stage in ('download','extract','install','test')}
        async def send(session, message):
            if message['t'] == 'asr_install_progress':
                delivered[message['stage']].set()
        def install(**kw):
            for stage in ['download','extract','install','test']:
                kw['progress'](stage)
                self.assertTrue(delivered[stage].wait(1), 'unblocked progress was not consumed')
            kw['progress']('private-path-and-error')
            return 0
        host = await self.run_install(install, send=mock.AsyncMock(side_effect=send))
        messages = [call.args[1] for call in host.send_app.call_args_list]
        self.assertEqual([m.get('stage') for m in messages[:-1]], ['download','extract','install','test'])
        self.assertTrue(all(set(m) == {'t','r','stage'} for m in messages[:-1]))
        self.assertTrue(all(m['r'] == 'p387-asr' for m in messages))
        self.assertTrue(messages[-1]['ok'])
        self.assertEqual(messages[-1]['state'], 'ready')

    async def test_blocked_burst_is_bounded_and_retains_latest_progress_and_result(self):
        release, entered = asyncio.Event(), asyncio.Event()
        entered_thread = threading.Event()
        sent, queues, tasks = [], [], {}
        real_queue = asyncio.Queue
        class ObservedQueue(real_queue):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.max_seen = 0
                queues.append(self)
            def put_nowait(self, value):
                super().put_nowait(value)
                self.max_seen = max(self.max_seen, self.qsize())
        async def send(session, message):
            if not entered.is_set():
                entered.set()
                entered_thread.set()
                await release.wait()
            sent.append(message)
        def install(**kw):
            kw['progress']('download')
            self.assertTrue(entered_thread.wait(1), 'reporter never entered the controlled stall')
            for _ in range(20):
                for stage in ('download','extract','install','test'):
                    kw['progress'](stage)
            return 0
        host = SimpleNamespace(asr=SimpleNamespace(install=install),
            st=SimpleNamespace(root=Path('/tmp/p387-asr-test'), log=mock.Mock()),
            send_app=send, asr_state=lambda:'ready')
        def background(coro, what=''):
            tasks[what] = Host._bg(host, coro, what)
            return tasks[what]
        host._bg = background
        with mock.patch('agentj.serve.asyncio.Queue', ObservedQueue):
            await Host.on_p33(host, SimpleNamespace(device='paired-test-device'), 'asr_install', {'r':'p387-burst'})
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.wait_for(tasks['install_asr'], 1)
                self.assertFalse(host._asr_installing)
                self.assertEqual(sent, [])
                self.assertEqual(len(queues), 1)
                self.assertEqual(queues[0].maxsize, 4)
                self.assertEqual(queues[0].max_seen, 4)
                self.assertEqual(queues[0].qsize(), 4)
                self.assertFalse(tasks['asr_install_progress'].done())
            finally:
                release.set()
                await asyncio.gather(*tasks.values())
        self.assertEqual(sent[-1], {'t':'asr_install_res','r':'p387-burst','ok':True,'state':'ready'})
        self.assertEqual(sent[-2]['stage'], 'test')
        self.assertTrue(all(message['r'] == 'p387-burst' for message in sent))
        self.assertLessEqual(len(sent), 5)  # the blocked write plus at most four queued messages

    async def test_failure_is_safe_stage_reason_and_never_terminal_output(self):
        def install(**kw):
            kw['out']('private-path-and-error')
            kw['progress']('download')
            return 1
        host = await self.run_install(install, 'not_installed')
        result = host.send_app.call_args.args[1]
        self.assertEqual(result['why'], 'download_failed')
        self.assertNotIn('private-path', str(host.send_app.call_args_list))

    async def test_disconnected_progress_does_not_abort_installer(self):
        async def send(session, message):
            if message['t'] == 'asr_install_progress':
                raise ConnectionError('remote gone')
        installer = mock.Mock(side_effect=lambda **kw: (kw['progress']('download'), 0)[1])
        host = await self.run_install(installer, send=mock.AsyncMock(side_effect=send))
        self.assertTrue(host.send_app.call_args.args[1]['ok'])

    async def test_stalled_progress_never_blocks_activation_or_busy_cleanup(self):
        release, entered = asyncio.Event(), asyncio.Event()
        sent = []
        async def send(session, message):
            if message['t'] == 'asr_install_progress':
                entered.set()
                await release.wait()
            sent.append(message)
        def install(**kw):
            kw['progress']('download')
            return 0
        host = SimpleNamespace(asr=SimpleNamespace(install=mock.Mock(side_effect=install)),
            st=SimpleNamespace(root=Path('/tmp/p387-asr-test'), log=mock.Mock()),
            send_app=send, asr_state=lambda:'ready', set_pref=mock.AsyncMock())
        host._bg = lambda coro, what='': Host._bg(host, coro, what)
        await Host.on_p33(host, SimpleNamespace(device='paired-test-device'), 'asr_install', {'r':'p387-stalled','enable':True})
        try:
            await asyncio.wait_for(entered.wait(), 1)
            for _ in range(100):
                if not host._asr_installing:
                    break
                await asyncio.sleep(.01)
            self.assertFalse(host._asr_installing)
            self.assertEqual(host.set_pref.await_args_list, [mock.call('voice.asr.mode','local'),mock.call('voice.asr.engine','auto')])
            self.assertEqual(sent, [])  # transport is genuinely still stalled; no cancellation after encryption
            self.assertFalse(host._asr_install_delivery.done())
            # A retry cannot accumulate another blocked reporter on this same transport.
            for i in range(10):
                await Host.on_p33(host, SimpleNamespace(device='paired-test-device'), 'asr_install', {'r':'p387-retry-' + str(i)})
            self.assertEqual(host.asr.install.call_count, 1)
            self.assertEqual(len(host._bg_tasks), 1)
            self.assertTrue(all(m.get('why') == 'busy' for m in sent))
        finally:
            release.set()
            await asyncio.gather(*host._bg_tasks)
        self.assertTrue(sent[-1]['ok'])
        self.assertEqual(sent[-1]['t'], 'asr_install_res')

    async def test_busy_does_not_start_second_installer(self):
        host = SimpleNamespace(_asr_installing=True, send_app=mock.AsyncMock())
        await Host.on_p33(host, SimpleNamespace(), 'asr_install', {'r':'p387-asr'})
        self.assertEqual(host.send_app.call_args.args[1]['why'], 'busy')

    async def test_zero_exit_never_claims_ready_when_engine_off(self):
        host = await self.run_install(mock.Mock(return_value=0), 'off')
        self.assertFalse(host.send_app.call_args.args[1]['ok'])
        self.assertEqual(host.send_app.call_args.args[1]['why'], 'off')


class InstallerStages(unittest.TestCase):
    def test_real_installer_reports_stages_without_percent_or_paths(self):
        with tempfile.TemporaryDirectory() as root:
            stages = []
            def fetch(urls, dest, *args):
                dest.touch()
                return 'cache'
            def venv(d, *args):
                (d / 'venv').mkdir()
            with mock.patch.multiple(asr,
                    platform_key=mock.Mock(return_value=('linux','x86_64','cp313')),
                    _installed_ok=mock.Mock(return_value=(False,None)),
                    _fetch=mock.Mock(side_effect=fetch),
                    _safe_extract=mock.Mock(), _make_venv=mock.Mock(side_effect=venv),
                    _self_test=mock.Mock(), _set_setting=mock.Mock(),
                    settings=mock.Mock(return_value={'engine':'auto','threads':2})):
                self.assertEqual(asr.install(root, mirror='github', yes=True, out=lambda _:None, progress=stages.append), 0)
            self.assertEqual(stages, ['download','extract','install','test'])
