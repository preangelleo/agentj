"""Source-only uLz0 journey: native process, elevate socket, signed phone and loopback YouTube."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest import mock

from agentj import approval_review, approvals, codex_procs, doctor, main_identity, preferences, provenance
from _p541_device import Phone, _owner_host, finish_phone_touch
from test_l1 import _ready
from test_p436_google_all import Adapter, Base


class UploadApproval(Adapter):
    def test_each_upload_needs_a_fresh_signed_phone_answer(self):
        _, connected = self.full('youtube-publish')
        self.assertEqual(connected['step'], 'ready')
        asyncio.run(self.upload_journey())

    async def upload_journey(self):
        host = _owner_host(self.st, [])
        host.agent_cfg = {'kind': 'claude', 'dir': str(self.work)}
        host.ask_ttl = 1.0
        phone = Phone(self.st)
        session = _ready(host, phone)
        original = self.work / 'fixture.mp4'
        video = b'\x00\x00\x00\x18ftypmp42' + b'fixture-frame' * 4096
        original.write_bytes(video)
        socket_path = str(self.d / 'elevate.sock')
        server = await asyncio.start_unix_server(host.elevate.on_client, path=socket_path)
        evidence = {}
        prior = None
        request_nonces = set()
        async with server:
            for decision in ('deny', 'allow', 'timeout', 'replaced'):
                title = 'Private fixture deny'
                entry = host.hist_add({'k': 'phone', 'name': 'Owner', 'text': 'Upload ' + title}, '', 'done')
                origin = approval_review.remember(host, entry, provenance.phone())
                host.desktop_turn = origin['message_id']
                child = "import sys; from agentj.cli import main; sys.argv=sys.argv[1:]; sys.exit(main())"
                anchor = (
                    "import subprocess,sys; sys.stdin.readline(); "
                    "sys.exit(subprocess.call([sys.executable, '-c', sys.argv[1], *sys.argv[2:]]))"
                )
                env = {**os.environ, 'AGENTJ_STATE_DIR': str(self.st.root), 'AGENTJ_ELEVATE_SOCK': socket_path,
                       'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, '-c', anchor, child, 'agentj', 'google', 'youtube', 'upload', str(original),
                    '--title', title, '--json', env=env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                host.agent.native_source = provenance.system()
                host.agent.cfg = host.agent_cfg
                host.agent.session = {'sessionId': 'ulz0', 'pid': proc.pid,
                                      'procStart': codex_procs.start_token(proc.pid)}
                done = asyncio.create_task(proc.communicate(b'go\n'))
                before = list(self.srv.calls)
                try:
                    for _ in range(300):
                        if host.asks or done.done():
                            break
                        await asyncio.sleep(.01)
                    if not host.asks:
                        out, err = await asyncio.wait_for(done, 5)
                        evidence['baseline_no_card'] = not host.asks
                        self.write_evidence(evidence)
                        self.fail(f'Upload produced no paired card; response={out!r}; stderr={err!r}; '
                                  f'provider_request_changed={self.srv.calls != before}')
                    ask, = host.asks.values()
                    nonce = ask.tool_input.get('request_nonce')
                    self.assertRegex(nonce, r'^[a-f0-9]{32}$')
                    self.assertNotIn(nonce, request_nonces, 'same visible upload cannot reuse a questioned card')
                    request_nonces.add(nonce)
                    self.assertEqual(self.srv.calls, before, 'no provider write while the card is pending')
                    frame = host._ask_msg(ask, session)
                    self.assertIn('private', frame['summary'])
                    self.assertIn('fixture.mp4', frame['summary'])
                    self.assertIn(title, frame['summary'])
                    self.assertEqual(frame['tool'], 'agentj google youtube upload')
                    self.assertEqual(frame['envelope']['title'], '确认 YouTube 上传')
                    self.assertIn('fixture.mp4', frame['envelope']['reason'])
                    self.assertIn('private', frame['envelope']['reason'])
                    self.assertIn(title, frame['envelope']['reason'])
                    evidence.setdefault('paired_card', {k: v for k, v in frame.items() if k in ('id', 't', 'agent', 'tool', 'summary', 'ttl', 'approval_v', 'envelope')})
                    if prior:
                        self.assertNotEqual(prior['id'], frame['id'])
                        await host._answer(session, prior)
                        self.assertFalse(ask.fut.done(), 'an earlier signed answer cannot authorize the new upload')
                    if decision == 'replaced':
                        original.write_bytes(video[:-1] + b'!')
                    if decision != 'timeout':
                        prior = phone.answer(frame, decision != 'deny')
                        await host._answer(session, prior)
                    out, err = await asyncio.wait_for(done, 8)
                    result = json.loads(out)
                    if decision == 'allow':
                        self.assertEqual((proc.returncode, result['ok'], result['privacy']), (0, True, 'private'), err)
                        self.assertEqual(self.srv.uploads['bytes'], video)
                        from agentj.google_api import run
                        read_back = await asyncio.to_thread(run, self.st, {'op': 'youtube_get', 'id': result['id']})
                        self.assertEqual(read_back['video']['privacy'], 'private')
                        self.assertEqual(read_back['video']['processing'], 'succeeded')
                    else:
                        self.assertEqual((proc.returncode, result['ok']), (1, False), err)
                        self.assertEqual(result['why'], 'file_changed' if decision == 'replaced' else 'denied')
                        self.assertEqual(self.srv.calls, before, decision)
                    evidence[decision] = {'ok': result['ok'], 'why': result.get('why'), 'new_provider_calls': len(self.srv.calls)-len(before)}
                finally:
                    if proc.returncode is None:
                        proc.kill()
                    await asyncio.gather(done, return_exceptions=True)
            records = [r for r in approvals.read_log(self.st) if r.get('tool') == 'agentj google youtube upload']
            self.assertEqual([r['reason'] for r in records], ['device', 'device', 'timeout', 'device'])
            self.assertTrue(all(approvals.check_record(self.st, row) == 'ok' for row in records if row['reason'] == 'device'))
            self.write_evidence(evidence)
            await finish_phone_touch(host)

    @staticmethod
    def write_evidence(evidence):
        if path := os.environ.get('AGENTJ_ULZ0_EVIDENCE'):
            Path(path).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


class UploadReconciliation(Adapter):
    def test_lost_reply_is_read_back_without_resending_bytes(self):
        self.check_lost_reply('status_confirms', True)

    def test_unavailable_status_reports_unknown_without_resending_bytes(self):
        self.check_lost_reply('status_unavailable', False)

    def check_lost_reply(self, mode, confirmed):
        from agentj.google_api import open_file, run
        _, connected = self.full('youtube-publish')
        self.assertEqual(connected['step'], 'ready')
        clip = self.work / 'clip.mp4'
        payload = b'\x00\x00\x00\x18ftypmp42' + b'frames' * 20
        clip.write_bytes(payload)
        self.srv.mode['upload_reply'] = mode
        fd, why = open_file(self.st, str(self.work), str(clip), video=True)
        self.assertIsNone(why)
        try:
            result = run(self.st, {'op': 'youtube_upload', 'title': 'Private recovery fixture'}, fd)
        finally:
            os.close(fd[0])
        self.assertEqual(result['ok'], confirmed, result)
        if confirmed:
            self.assertEqual(result['id'], 'VID436abc')
            self.assertEqual(result['privacy'], 'private')
        else:
            self.assertEqual(result['why'], 'upload_outcome_unknown')
        self.assertEqual(self.srv.uploads['bytes'], payload)
        self.assertEqual([call[2] for call in self.srv.calls if call[0] == 'PUT'], [len(payload), 0])


class IdentityDiagnosis(Base):
    def test_core_failure_and_root_configuration_failure_are_distinct(self):
        with mock.patch.object(preferences, 'effective', side_effect=preferences.ConfigError('fixture', 'invalid')):
            rows = doctor.check_main_identity(self.st)
        self.assertEqual([(r['id'], r['status']) for r in rows], [('main-core', 'ok'), ('main-config', 'fail')])
        with mock.patch.object(main_identity, 'DATA', self.d / 'missing-package-assets'):
            rows = doctor.check_main_identity(self.st)
        self.assertEqual([(r['id'], r['status']) for r in rows], [('main-core', 'fail')])
        self.assertIn('trusted package', rows[0]['hint'])
