"""P330: one shared-session pin per harness (agent.json `<kind>.pin`), the global `agent.shared_session_id` only the owner's
choice credited to its harness; FEEDBACK 26: a pinned Codex thread whose rollout is gone follows the desktop's thread (or
asks), a held thread is named after the terminal codex behind a managed app-server, and a slow open is retried once."""
import _hermetic
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import codex_procs, doctor, shared_codex, shared_pin
from agentj.shared_codex import SharedCodexAgent, candidates, heal_pick
from agentj.state import State

CLAUDE = '0b6f3e2a-1c4d-4e5f-8a9b-0c1d2e3f4a5b'     # random v4 (a Claude Code session id)
CODEX = '01a13a75-9b02-7132-95a9-31ad9d45caff'      # UUIDv7 (a Codex thread id)
CODEX2 = '01a14204-0000-7000-8000-000000000002'
DEAD = 'cc99e6c3-0000-4000-8000-000000000000'       # the Mac's pin whose rollout is gone
OC = 'ses_p330owner'


class St(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.st = State(self.root / 'state'); self.st.root.mkdir(parents=True, exist_ok=True)
        self.st.log = Mock()
        self.dir = str(self.root / 'proj')
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.root / 'claude')}); env.start(); self.addCleanup(env.stop)

    def transcript(self, sid):
        import re
        d = self.root / 'claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', self.dir); d.mkdir(parents=True, exist_ok=True)
        (d / (sid + '.jsonl')).write_text('{}\n')


class Guess(St):
    def test_formats(self):
        self.assertEqual(shared_pin.guess(OC), 'opencode')
        self.assertEqual(shared_pin.guess(CODEX), 'codex')
        self.assertIsNone(shared_pin.guess(CLAUDE, self.dir))        # v4 with no transcript here: either
        self.transcript(CLAUDE)
        self.assertEqual(shared_pin.guess(CLAUDE, self.dir), 'claude')
        self.assertIsNone(shared_pin.guess('not-an-id'))
        self.assertFalse(shared_pin.plausible('claude', CODEX))
        self.assertFalse(shared_pin.plausible('codex', CLAUDE, self.dir))
        self.assertFalse(shared_pin.plausible('opencode', CLAUDE))
        self.assertTrue(shared_pin.plausible('opencode', OC))


class PerHarness(St):
    def test_legacy_claude_pin_never_followed_by_codex_and_back(self):
        """Mac 10-16: cc99e6c3 (Claude's) was taken over by Codex; Codex's id rode back to Claude. Now each keeps its own."""
        self.transcript(CLAUDE)
        self.assertEqual(shared_pin.sync(self.st, 'claude', CLAUDE, self.dir), CLAUDE)      # pre-P330 config: absorbed
        self.assertEqual(self.st.agent_session('claude.pin'), CLAUDE)
        self.assertEqual(self.st.agent_session('pin.owner_kind'), 'claude')
        self.assertEqual(shared_pin.sync(self.st, 'codex', CLAUDE, self.dir), '')           # switch → Codex: not its pin
        shared_pin.write(self.st, 'codex', CODEX)                                           # Codex discovered its own
        self.assertEqual(shared_pin.sync(self.st, 'codex', CLAUDE, self.dir), CODEX)
        self.assertEqual(shared_pin.sync(self.st, 'opencode', CLAUDE, self.dir), '')
        shared_pin.write(self.st, 'opencode', OC)
        self.assertEqual(shared_pin.sync(self.st, 'claude', CLAUDE, self.dir), CLAUDE)      # back → Claude: untouched
        self.assertEqual(shared_pin.sync(self.st, 'codex', CLAUDE, self.dir), CODEX)
        self.assertEqual(shared_pin.sync(self.st, 'opencode', CLAUDE, self.dir), OC)
        self.assertEqual(shared_pin.resolve(self.st, 'claude', CLAUDE, self.dir), CLAUDE)

    def test_claude_repin_moves_only_claude_pin(self):
        self.transcript(CLAUDE)
        shared_pin.sync(self.st, 'claude', CLAUDE, self.dir)
        shared_pin.write(self.st, 'claude', 'aaaaaaaa-1111-4111-8111-111111111111')         # P147 heal
        self.assertEqual(shared_pin.resolve(self.st, 'claude', CLAUDE, self.dir), 'aaaaaaaa-1111-4111-8111-111111111111')
        self.assertEqual(shared_pin.resolve(self.st, 'codex', CLAUDE, self.dir), '')

    def test_owner_codex_pin_credited_to_codex_even_when_set_under_claude(self):
        self.assertEqual(shared_pin.sync(self.st, 'claude', CODEX, self.dir), '')
        self.assertEqual(self.st.agent_session('pin.owner_kind'), 'codex')
        self.assertEqual(shared_pin.resolve(self.st, 'codex', CODEX, self.dir), CODEX)

    def test_new_owner_choice_wins_over_a_repin_and_same_value_again_after_config_set(self):
        shared_pin.sync(self.st, 'codex', CODEX, self.dir)
        shared_pin.write(self.st, 'codex', CODEX2)                    # Codex re-pinned itself
        self.assertEqual(shared_pin.resolve(self.st, 'codex', CODEX, self.dir), CODEX2)
        shared_pin.forget_owner(self.st)                              # `agentj config set agent.shared_session_id <CODEX>` again
        self.assertEqual(shared_pin.resolve(self.st, 'codex', CODEX, self.dir), CODEX)
        self.assertEqual(shared_pin.sync(self.st, 'codex', CODEX, self.dir), CODEX)

    def test_unset_frees_only_the_owners_harness(self):
        self.transcript(CLAUDE)
        shared_pin.sync(self.st, 'claude', CLAUDE, self.dir)
        shared_pin.write(self.st, 'codex', CODEX)
        self.assertEqual(shared_pin.resolve(self.st, 'claude', '', self.dir), '')
        self.assertEqual(shared_pin.sync(self.st, 'claude', '', self.dir), '')
        self.assertIsNone(self.st.agent_session('claude.pin'))
        self.assertEqual(self.st.agent_session('codex.pin'), CODEX)

    def test_agent_config_upgrade_and_switch(self):
        """A pre-P330 config.json5 keeps working: its id reaches the harness it belongs to; nothing new in the schema."""
        from agentj import preferences
        self.transcript(CLAUDE)
        prefs = {'agent': {'shared_session_id': CLAUDE, 'session_mode': 'shared'}}
        for kind, want in (('claude', CLAUDE), ('codex', ''), ('opencode', ''), ('claude', CLAUDE)):
            self.st.config_path.write_text(json.dumps({'agent': {'kind': kind, 'dir': self.dir}}))
            with patch.object(preferences, 'effective', return_value=prefs):
                c = self.st.agent_config()
            self.assertEqual(c['shared_session_id'], want, kind)
            self.assertEqual(c['shared_pin_owner'], CLAUDE)
        self.assertFalse([k for k in preferences.SCHEMA if 'pin' in k.split('.')[-1]])


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / 'codex'; self.proj = root / 'proj'; self.proj.mkdir()
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.home)}); env.start(); self.addCleanup(env.stop)
        held = patch.object(codex_procs, 'held', return_value=set()); self.held = held.start(); self.addCleanup(held.stop)
        self.st = State(root / 'state'); self.st.root.mkdir(parents=True, exist_ok=True)
        self.host = Mock(); self.host.st = self.st; self.host.lang = 'zh'; self.host.preferences = None
        self.host.set_pref = AsyncMock()
        self.calls = []

    def rollout(self, sid, age=0):
        d = self.home / 'sessions' / '2026' / '10' / '16'; d.mkdir(parents=True, exist_ok=True)
        items = [{'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(self.proj)}},
                 {'type': 'turn_context', 'payload': {'cwd': str(self.proj), 'approval_policy': 'on-request',
                                                      'sandbox_policy': {'type': 'danger-full-access'}}}]
        path = d / f'rollout-2026-10-16T00-00-00-{sid}.jsonl'
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    def agent(self, pin=''):
        a = SharedCodexAgent(self.host, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                         'session_mode': 'shared', 'shared_session_id': pin})

        async def call(method, params, timeout=60):
            self.calls.append((method, params))
            if method == 'thread/start':
                return {'thread': {'id': CODEX2}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}
            if method == 'hooks/list':
                return {'data': []}
            raise AssertionError(method)
        a.call = AsyncMock(side_effect=call)
        return a

    def notices(self):
        return ' '.join(c.args[0] for c in self.host.agent_notice.call_args_list)


class Lost(Base):
    async def test_gone_pin_follows_the_only_thread_and_says_so(self):
        self.rollout(CODEX)
        a = self.agent(DEAD)
        self.assertTrue(await a._thread())
        self.assertEqual(a.tid, CODEX)
        self.assertEqual(self.st.agent_session('codex.pin'), CODEX)
        self.assertIn('共享会话找不到了', self.notices()); self.assertIn(DEAD[:8], self.notices())
        self.host.set_pref.assert_not_called()                     # never the global pin
        self.assertFalse(any(m == 'thread/start' for m, _ in self.calls))

    async def test_newest_by_a_clear_margin_is_followed(self):
        self.rollout(CODEX, age=0); self.rollout(CODEX2, age=3600)
        a = self.agent(DEAD)
        await a._thread()
        self.assertEqual(a.tid, CODEX)

    async def test_two_alike_ask_on_the_phone(self):
        self.rollout(CODEX, age=0); self.rollout(CODEX2, age=60)
        self.host.question = AsyncMock(return_value=('answer', [[2]]))
        a = self.agent(DEAD)
        await a._thread()
        card = self.host.question.call_args.args[0]
        self.assertIn(DEAD[:8], card[0]['q']); self.assertEqual(len(card[0]['o']), 3)
        self.assertEqual(a.tid, CODEX2)
        self.assertIn('改接', self.notices())

    async def test_unanswered_card_refuses_with_a_named_reason(self):
        self.rollout(CODEX, age=0); self.rollout(CODEX2, age=60)
        self.host.question = AsyncMock(return_value=('timeout', None))
        a = self.agent(DEAD)
        with self.assertRaises(shared_codex.Refusal) as r:
            await a._thread()
        self.assertEqual(r.exception.code, 'shared session lost')
        self.assertIn('共享会话找不到了', shared_codex.REASONS['shared session lost'][0])

    async def test_owner_picks_new_conversation(self):
        self.rollout(CODEX, age=0); self.rollout(CODEX2, age=60)
        self.host.question = AsyncMock(return_value=('answer', [[3]]))
        a = self.agent(DEAD)
        await a._thread()
        self.assertTrue(any(m == 'thread/start' for m, _ in self.calls))
        self.assertIn('codex resume', self.notices())

    async def test_nothing_here_opens_new_and_says_lost(self):
        a = self.agent(DEAD)
        await a._thread()
        self.assertEqual(a.tid, CODEX2)
        self.assertIn('共享会话找不到了', self.notices()); self.assertIn('codex resume ' + CODEX2, self.notices())

    async def test_observer_follows_an_unambiguous_successor(self):
        self.rollout(CODEX)
        self.host.turn_lock = asyncio.Lock()
        a = self.agent(DEAD)
        await a._observe_heal(DEAD)
        self.assertEqual(a.cfg['shared_session_id'], CODEX)
        self.assertEqual(self.st.agent_session('codex.pin'), CODEX)

    def test_doctor_names_the_lost_session(self):
        self.rollout(CODEX)
        st = Mock(); st.exists.return_value = True; st.agent_session.return_value = None
        st.agent_config.return_value = {'kind': 'codex', 'session_mode': 'shared', 'dir': str(self.proj), 'shared_session_id': DEAD}
        row = doctor.check_codex_shared(st)
        self.assertIn('共享会话找不到了', json.dumps(row, ensure_ascii=False))
        self.assertIn(CODEX[:8], json.dumps(row, ensure_ascii=False))


class Busy(Base):
    def test_default_thread_skips_one_another_client_holds(self):
        new = self.rollout(CODEX, age=0); self.rollout(CODEX2, age=600)
        self.held.return_value = {os.path.realpath(new)}
        self.assertEqual(shared_codex.discover(str(self.proj)), CODEX2)
        self.held.return_value = {os.path.realpath(p) for p in (self.home / 'sessions').rglob('*.jsonl')}
        self.assertEqual(shared_codex.discover(str(self.proj)), CODEX)       # all held: the newest still
        self.assertEqual(heal_pick([]), (None, 'none'))
        self.assertEqual(heal_pick([(CODEX, 1000), (CODEX2, 990)]), (None, 'ambiguous'))

    async def test_slow_open_is_retried_once(self):
        self.rollout(CODEX)
        a = self.agent(CODEX)
        a._spawn = AsyncMock(return_value=True)
        a._kill = AsyncMock()
        a.proc = Mock(pid=4242)
        n = {'k': 0}
        real = a._thread

        async def flaky():
            n['k'] += 1
            if n['k'] == 1:
                raise asyncio.TimeoutError()
            return await real()
        a._thread = flaky
        a.fail_notice = Mock()
        self.assertTrue(await a._ready())
        self.assertEqual(n['k'], 2); a.fail_notice.assert_not_called()

    async def test_two_timeouts_report_once(self):
        a = self.agent(CODEX)
        a._spawn = AsyncMock(return_value=True); a._kill = AsyncMock(); a.proc = Mock(pid=1)
        a._thread = AsyncMock(side_effect=asyncio.TimeoutError())
        a.fail_notice = Mock()
        self.assertFalse(await a._ready())
        self.assertEqual(a._thread.await_count, 2); a.fail_notice.assert_called_once()


class Who(unittest.TestCase):
    def test_managed_daemon_reported_as_the_terminal_codex(self):
        st = Mock()
        cmds = {93912: '/opt/homebrew/bin/codex app-server --listen unix', 6287: 'codex'}
        with patch.object(codex_procs, 'ours_alive', return_value=[]), \
             patch.object(codex_procs, 'fd_holders', return_value=[93912]), \
             patch.object(codex_procs, 'cmdline', side_effect=lambda p: cmds.get(p, '')), \
             patch.object(codex_procs, 'socket_clients', return_value=[6287]):
            w = codex_procs.who(st, '/x/rollout.jsonl')
        self.assertEqual(w, {'kind': 'terminal_daemon', 'pid': 6287, 'via': 93912, 'how': 'socket'})
        t = codex_procs.text(w['kind'], w['pid'], True, w['via'])
        self.assertIn('终端里的 codex（PID 6287）', t); self.assertIn('kill 93912', t)
        self.assertNotIn('{', codex_procs.text('terminal_daemon', 1, False, 2))

    def test_daemon_without_a_terminal_client_stays_app_server(self):
        with patch.object(codex_procs, 'ours_alive', return_value=[]), \
             patch.object(codex_procs, 'fd_holders', return_value=[93912]), \
             patch.object(codex_procs, 'cmdline', return_value='codex app-server'), \
             patch.object(codex_procs, 'socket_clients', return_value=[]):
            self.assertEqual(codex_procs.who(Mock(), '/x')['kind'], 'app_server')

    def test_lsof_retried_after_a_timeout_and_found_off_path(self):
        runs = []

        def run(argv, **kw):
            runs.append(argv)
            if len(runs) == 1:
                raise subprocess.TimeoutExpired(argv, kw.get('timeout'))
            return Mock(stdout='93912\n')
        with patch.object(codex_procs, '_linux', return_value=False), \
             patch.object(codex_procs.shutil, 'which', return_value=None), \
             patch.object(codex_procs.os, 'access', side_effect=lambda p, m: p == '/usr/sbin/lsof'), \
             patch.object(codex_procs.subprocess, 'run', side_effect=run):
            self.assertEqual(codex_procs.fd_holders('/x'), [93912])
        self.assertEqual(len(runs), 2); self.assertEqual(runs[0][0], '/usr/sbin/lsof')
        self.assertGreater(codex_procs.LSOF_TIMEOUT, codex_procs.PS_TIMEOUT)

    def test_mac_socket_clients_match_peer_addresses(self):
        out = 'p93912\nf5\nd0xaaa\nn/tmp/codex.sock\np6287\nf9\nd0xbbb\nn->0xaaa\np1\nf1\nd0xccc\nn->0xddd\n'
        with patch.object(codex_procs, '_linux', return_value=False), \
             patch.object(codex_procs, '_lsof', return_value=out):
            self.assertEqual(codex_procs.socket_clients(93912), [6287])

    def test_linux_socket_clients_via_ss(self):
        rows = [('100', '200', [93912]), ('200', '100', [6287]), ('300', '400', [1])]
        with patch.object(codex_procs, '_linux', return_value=True), patch.object(codex_procs, '_ss_unix', return_value=rows):
            self.assertEqual(codex_procs.socket_clients(93912), [6287])

    def test_held_finds_an_open_file_here(self):
        with tempfile.NamedTemporaryFile(dir=TEST_TMP) as fh:
            child = subprocess.Popen([sys.executable, '-c', 'import sys,time;f=open(sys.argv[1]);time.sleep(5)', fh.name])
            try:
                t0 = time.time()
                while time.time() - t0 < 3 and not codex_procs.held([fh.name]):
                    time.sleep(0.05)
                self.assertEqual(codex_procs.held([fh.name]), {os.path.realpath(fh.name)})
                self.assertEqual(codex_procs.held([fh.name], {child.pid}), set())
            finally:
                child.kill(); child.wait()


if __name__ == '__main__':
    unittest.main()
