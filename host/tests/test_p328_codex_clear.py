"""P328: the phone's /clear in Codex shared mode opens a new Codex conversation (no more "use desktop /new"), 「撤销清空」
until the next message opened it, and the Codex thread id never lands in the one global `agent.shared_session_id`."""
import _hermetic
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import doctor, humane, preferences, shared_codex
from agentj.shared_codex import SharedCodexAgent, effective_pin, clear_pending
from agentj.state import State

OLD = '11111111-1111-1111-1111-111111111111'
NEW = '22222222-2222-2222-2222-222222222222'
OWNER = '33333333-3333-3333-3333-333333333333'


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / 'codex'; self.proj = root / 'proj'; self.proj.mkdir()
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.home)}); env.start(); self.addCleanup(env.stop)
        self.st = State(root / 'state'); self.st.root.mkdir(parents=True, exist_ok=True)
        self.host = Mock(); self.host.st = self.st; self.host.lang = 'zh'; self.host.preferences = None
        self.host.set_pref = AsyncMock()
        self.calls = []

    def rollout(self, sid, active=False):
        d = self.home / 'sessions' / '2026' / '10' / '16'; d.mkdir(parents=True, exist_ok=True)
        items = [{'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(self.proj)}},
                 {'type': 'turn_context', 'payload': {'cwd': str(self.proj), 'approval_policy': 'on-request',
                                                      'sandbox_policy': {'type': 'danger-full-access'}}}]
        if active:
            items.append({'type': 'event_msg', 'payload': {'type': 'task_started'}})
        path = d / f'rollout-2026-10-16T00-00-00-{sid}.jsonl'
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        return path

    def agent(self, pin=''):
        a = SharedCodexAgent(self.host, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                         'session_mode': 'shared', 'shared_session_id': pin})

        async def call(method, params, timeout=60):
            self.calls.append((method, params))
            if method == 'thread/start':
                return {'thread': {'id': NEW}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'approvalPolicy': params.get('approvalPolicy', 'on-request'),
                        'approvalsReviewer': params.get('approvalsReviewer', 'user')}
            if method == 'hooks/list':
                return {'data': []}
            raise AssertionError(method)
        a.call = AsyncMock(side_effect=call)
        return a

    def notices(self):
        return ' '.join(c.args[0] for c in self.host.agent_notice.call_args_list)

    def agent_json(self):
        return json.loads(self.st.agent_path.read_text()) if self.st.agent_path.exists() else {}


class ClearRepins(Base):
    async def test_clear_then_next_message_opens_new_thread_persisted_per_harness(self):
        self.rollout(OLD)
        a = self.agent()
        await a._thread()                                        # following the desktop's newest thread
        self.assertEqual(self.st.agent_session('codex'), OLD)
        self.calls.clear()
        res = await a.command('clear', '')
        self.assertEqual(res.kind, 'ok'); self.assertTrue(res.undo); self.assertTrue(res.sep)
        self.assertIn(humane.say('clear.kept', 'zh'), res.text); self.assertIn('新的 Codex 对话', res.text)
        self.assertNotIn('没清成', res.text)
        self.assertEqual(self.calls, [], 'nothing native happens at /clear (a started thread is not on disk until its first message)')
        self.assertEqual(self.st.agent_session('codex.prev'), OLD); self.assertIsNone(self.st.agent_session('codex'))
        self.assertIsNone(a.tid); self.assertTrue(a.cleared)
        a.read_desktop()                                         # the observer never re-discovers the old thread
        self.assertIsNone(a.rollout); self.assertEqual(a.cfg['shared_session_id'], '')
        await a._thread()                                        # the next phone message
        self.assertEqual([m for m, _ in self.calls], ['thread/start'])
        self.assertEqual(self.calls[0][1], {'cwd': str(self.proj)})
        self.assertEqual(a.tid, NEW); self.assertEqual(self.st.agent_session('codex'), NEW); self.assertFalse(a.cleared)
        self.assertIn(f'`codex resume {NEW}`', self.notices()); self.assertIn('不会自动跟过来', self.notices())
        self.host.set_pref.assert_not_called()                   # the global pin is never written with a Codex id
        b = self.agent()                                         # serve restarted: still the new thread
        self.assertEqual(b.cfg['shared_session_id'], NEW); self.assertFalse(b.cleared)

    async def test_clear_off_an_owner_pin_survives_restart_without_touching_the_pin(self):
        self.rollout(OWNER)
        a = self.agent(OWNER)
        res = await a.command('clear', '')
        self.assertEqual(res.kind, 'ok')
        self.assertEqual(self.st.agent_session('codex.clear_from'), OWNER)
        b = self.agent(OWNER)                                    # restart before the next message: still cleared
        self.assertEqual(b.cfg['shared_session_id'], ''); self.assertTrue(b.cleared)
        await b._thread()
        self.assertEqual(self.calls[-1][0], 'thread/start'); self.assertEqual(self.st.agent_session('codex'), NEW)
        c = self.agent(OWNER)                                    # restart after: the phone's new thread, not the owner pin
        self.assertEqual(c.cfg['shared_session_id'], NEW)
        self.assertEqual(effective_pin(self.st, OWNER), NEW)
        d = self.agent(OLD)                                      # the owner picks another thread later: honoured
        self.assertEqual(d.cfg['shared_session_id'], OLD)
        self.host.set_pref.assert_not_called()

    async def test_clear_releases_a_kept_app_server(self):
        self.rollout(OLD)
        a = self.agent(OLD)
        proc = Mock(returncode=None); a.proc = proc; a.kept = True; a.tid = OLD
        a._kill = AsyncMock(side_effect=lambda p: setattr(a, 'proc', None))
        await a.command('clear', '')
        a._kill.assert_awaited_once_with(proc); self.assertFalse(a.kept); self.assertIsNone(a.tid)

    async def test_nothing_to_clear_is_noop_and_second_clear_is_noop(self):
        a = self.agent()
        res = await a.command('clear', '')
        self.assertEqual(res.kind, 'info'); self.assertFalse(res.undo)
        self.assertEqual(self.agent_json(), {})
        self.rollout(OLD)
        b = self.agent()
        self.assertTrue((await b.command('clear', '')).undo)
        self.assertEqual((await b.command('clear', '')).kind, 'info')    # already a fresh conversation
        self.assertEqual(self.st.agent_session('codex.prev'), OLD)


class Undo(Base):
    async def test_undo_before_next_message_restores_old_thread(self):
        self.rollout(OWNER)
        a = self.agent(OWNER)
        await a.command('clear', '')
        res = await a.command('undo_clear', '')
        self.assertEqual(res.kind, 'ok'); self.assertEqual(res.text, humane.say('undo.done', 'zh'))
        self.assertEqual(self.st.agent_session('codex'), OWNER); self.assertIsNone(self.st.agent_session('codex.prev'))
        self.assertIsNone(self.st.agent_session('codex.clear_from'))
        self.assertEqual(a.cfg['shared_session_id'], OWNER); self.assertFalse(a.cleared)
        await a._thread()
        self.assertEqual(self.calls[-1][0], 'thread/resume'); self.assertEqual(self.calls[-1][1]['threadId'], OWNER)
        self.assertEqual(self.agent(OWNER).cfg['shared_session_id'], OWNER)
        self.assertEqual((await a.command('undo_clear', '')).text, humane.say('undo.none', 'zh'))

    async def test_undo_after_the_new_conversation_started_is_late(self):
        self.rollout(OLD)
        a = self.agent(OLD)
        await a.command('clear', '')
        await a._thread()                                        # the next message opened the new thread
        res = await a.command('undo_clear', '')
        self.assertEqual(res.kind, 'error'); self.assertEqual(res.text, humane.say('undo.late', 'zh'))
        self.assertIsNone(self.st.agent_session('codex.prev')); self.assertEqual(self.st.agent_session('codex'), NEW)
        self.assertEqual(a.cfg['shared_session_id'], NEW)

    async def test_undo_survives_a_restart(self):
        self.rollout(OLD)
        await self.agent().command('clear', '')
        b = self.agent()
        self.assertTrue(b.cleared)
        self.assertEqual((await b.command('undo_clear', '')).kind, 'ok')
        self.assertEqual(b.cfg['shared_session_id'], OLD)


class Busy(Base):
    async def test_desktop_turn_running_changes_nothing(self):
        self.rollout(OLD, active=True)
        a = self.agent(OLD)
        res = await a.command('clear', '')
        self.assertEqual(res.kind, 'info'); self.assertFalse(res.undo)
        self.assertEqual(res.text, humane.say('codex.clear_busy', 'zh'))
        self.assertEqual(self.agent_json(), {}); self.assertEqual(a.cfg['shared_session_id'], OLD); self.assertFalse(a.cleared)
        self.assertEqual(self.calls, [])

    async def test_desktop_writer_active_moves_the_phone_to_a_new_thread(self):
        """The Codex App holds the old thread: the phone's new thread is not held by it, so /clear is the way forward."""
        self.rollout(OLD)
        a = self.agent(OLD)
        a.writer = {'kind': 'codex_app', 'pid': 42}; a.follow_status('desktop_writer')
        res = await a.command('clear', '')
        self.assertTrue(res.undo); self.assertIsNone(a.shared_status); self.assertEqual(a.writer, {})
        self.host.meter_update.assert_any_call(shared_status=None, shared_writer=None)
        await a._thread()
        self.assertEqual([m for m, _ in self.calls], ['thread/start'])

    async def test_phone_turn_running_queues_the_clear_after_it(self):
        """serve puts /clear in the Agent queue behind the running turn (cmd.after_turn); the command itself runs under
        turn_lock, so it never interleaves with a phone turn."""
        from agentj import serve
        src = Path(serve.__file__).read_text()
        self.assertIn('if self.agent.status in ("working", "compacting") or not self.agent.q.empty():', src)
        self.assertIn('cmd.after_turn', src)


class NoCrossHarnessPin(Base):
    async def test_only_codex_keys_written(self):
        self.rollout(OLD)
        self.st.set_agent_session('claude', OWNER)
        a = self.agent(OLD)
        await a.command('clear', ''); await a._thread()
        self.assertEqual(set(self.agent_json()), {'claude', 'codex', 'codex.prev', 'codex.clear_from', 'codex.pin'})   # P330: codex.pin = Codex's own
        self.assertEqual(self.st.agent_session('claude'), OWNER)
        self.host.set_pref.assert_not_called()
        self.assertEqual(effective_pin(self.st, OWNER), OWNER)  # another harness's / the owner's pin is not touched


class OldConfig(Base):
    def test_old_config_and_state_unchanged_behaviour(self):
        """A config.json5 / agent.json from before P328 (no .clear_from / .prev): the pin and the stored thread as before."""
        self.st.set_agent_session('codex', OLD)
        self.assertEqual(self.agent(OWNER).cfg['shared_session_id'], OWNER)
        self.assertEqual(self.agent('').cfg['shared_session_id'], OLD)
        self.assertFalse(self.agent('').cleared); self.assertFalse(clear_pending(self.st))
        self.assertIn('agent.shared_session_id', preferences.SCHEMA)
        self.assertNotIn('agent.shared_session_ids', preferences.SCHEMA)       # no schema change: older hosts keep reading it
        preferences.validate({'agent': {'session_mode': 'shared', 'shared_session_id': OWNER}})

    def test_doctor_row_after_phone_clear(self):
        self.st.set_agent_session('codex.prev', OLD)
        c = {'kind': 'codex', 'session_mode': 'shared', 'dir': str(self.proj), 'shared_session_id': ''}
        with patch.object(self.st, 'agent_config', return_value=c), patch.object(self.st, 'exists', return_value=True):
            row = doctor.check_codex_shared(self.st)
        self.assertIn('新对话', json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
