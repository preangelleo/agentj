"""#461 (0.26.8a2): a shared Codex rollout whose turn never closed (its Codex was killed mid-turn: SIGKILL leaves
`task_started` without `turn_aborted`, measured on codex-cli 0.162.1) must not hold every phone message behind
「Codex 正在回答」 forever, and /stop must not send the owner to an Esc nobody can press. A live writer (the rollout is open
in a process) still waits as before. Plus the auth-rescue screens Codex 0.162.1 shows: 「Hooks need review」 is skipped
with Esc (never 「Trust all」) and the new folder-trust wording is recognised."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import auth_rescue as ar  # noqa: E402
from agentj import codex_procs, shared_codex  # noqa: E402
from agentj.shared_codex import SharedCodexAgent, Refusal  # noqa: E402

SID = '46146146-1461-4614-6146-146146146146'
PROFILE = {'type': 'managed', 'file_system': {'type': 'restricted', 'entries': [
    {'path': {'type': 'special', 'value': {'kind': 'root'}}, 'access': 'read'}]}, 'network': 'restricted'}
OLD = time.time() - 3 * 3600          # the customer's rollout: untouched for hours


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p461-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home, self.proj = root / 'codex', root / 'proj'
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
                 {'type': 'turn_context', 'payload': {'cwd': str(self.proj), 'approval_policy': 'on-request',
                  'approvals_reviewer': 'user', 'sandbox_policy': {'type': 'read-only'}, 'permission_profile': PROFILE,
                  'active_permission_profile': {'id': ':read-only'}}},
                 {'type': 'event_msg', 'payload': {'type': 'task_started'}}]
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def agent(self):
        a = SharedCodexAgent(self.host, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                         'shared_session_id': SID})

        async def call(method, params, timeout=60):
            self.calls.append((method, params))
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'approvalPolicy': params.get('approvalPolicy', 'on-request'),
                        'approvalsReviewer': params.get('approvalsReviewer', 'user'),
                        'activePermissionProfile': {'id': params.get('permissions')}}
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

    def holder(self, path):
        """A real process that has the rollout open, as a live Codex writer does (measured: held while the thread is live)."""
        p = subprocess.Popen([sys.executable, '-c', 'import sys,time\nf=open(sys.argv[1],"rb")\nprint("ok",flush=True)\n'
                              'time.sleep(60)', str(path)], stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), b'ok')
        return p


@unittest.skipUnless(sys.platform.startswith('linux'), 'holders are read from /proc in this test')
class OrphanTurn(Base):
    async def test_orphaned_turn_is_not_waited_for_and_message_is_delivered(self):
        self.rollout()
        a = self.agent()
        await asyncio.wait_for(a._thread(), 10)
        self.assertEqual(self.calls[-1][0], 'thread/resume')
        self.assertEqual(self.calls[-1][1]['threadId'], SID)
        self.assertNotIn('等它说完', self.notices())
        self.assertIn('上一轮没有正常结束', self.notices())
        self.assertEqual(self.events('shared_orphan_turn')[0]['reason'], 'desktop turn writer gone')
        self.assertFalse(a.desktop_wait)

    async def test_live_writer_still_waits(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 1):
            with self.assertRaises(Refusal) as r:
                await asyncio.wait_for(a._thread(), 10)
        self.assertEqual(r.exception.code, 'desktop turn did not finish')
        self.assertIn('等它说完', self.notices())
        self.assertFalse(self.events('shared_orphan_turn'))

    async def test_fresh_rollout_without_writer_still_waits(self):
        self.rollout(mtime=None)            # just written: a turn between two writes is not called dead
        a = self.agent()
        with patch.object(shared_codex, 'DESKTOP_WAIT', 1):
            with self.assertRaises(Refusal):
                await asyncio.wait_for(a._thread(), 10)
        self.assertFalse(self.events('shared_orphan_turn'))

    async def test_unknown_platform_answer_keeps_waiting(self):
        self.rollout()
        a = self.agent()
        with patch('agentj.codex_procs.fd_holders', return_value=None), patch.object(shared_codex, 'DESKTOP_WAIT', 1):
            with self.assertRaises(Refusal):
                await asyncio.wait_for(a._thread(), 10)
        self.assertFalse(self.events('shared_orphan_turn'))

    async def test_writer_dying_mid_wait_releases_the_message(self):
        path = self.rollout()
        p = self.holder(path)
        a = self.agent()
        with patch.object(shared_codex, 'ORPHAN_CHECK', 0.5):
            t = asyncio.create_task(a._thread())
            await asyncio.sleep(1.5)
            self.assertFalse(t.done())
            p.kill()
            p.wait()
            await asyncio.wait_for(t, 10)
        self.assertEqual(self.calls[-1][0], 'thread/resume')
        self.assertEqual(len(self.events('shared_orphan_turn')), 1)

    async def test_stop_on_orphaned_turn_does_not_send_owner_to_esc(self):
        self.rollout()
        a = self.agent()
        a.rollout = self.rollout()
        a.status, a.phone_turn = 'idle', False
        a.interrupt_request = AsyncMock()
        await a.halt(clear_queue=True)
        self.assertNotIn('按 Esc', self.notices())

    async def test_stop_on_live_desktop_turn_still_says_esc(self):
        a = self.agent()
        # 0269 (ADR-0269-watchdog): a held rollout untouched for STUCK_IDLE with no tool child is a stuck turn (that case
        # is test_p0269_selfheal); a live desktop turn keeps writing — fresh file, held.
        a.rollout = self.rollout(mtime=None)
        self.holder(a.rollout)
        a.status, a.phone_turn = 'idle', False
        self.assertFalse(await a.halt(clear_queue=True))
        self.assertIn('按 Esc', self.notices())


class RescueScreens(unittest.TestCase):
    # squashed screens captured from codex-cli 0.162.1 in a pty (isolated CODEX_HOME, #461)
    HOOKS = ("hooksneedreview1hookisneworchanged.hookscanrunoutsidethesandboxafteryoutrustthem.›1.reviewhooks"
             "2.trustallandcontinue3.continuewithouttrusting(hookswon'trun)enterconfirm·escskip")
    HOOKS3 = "Hooks need review\n3 hooks are new or changed.\n› 1. Review hooks\n  2. Trust all and continue"
    FOLDER = ("trustthisfolder?codexcanread,edit,andrunfileshere,subjecttoyourpermissionsettings.›1.trustandcontinue"
              "2.backtoagentcommandcenterentercontinue·escback")

    def test_hooks_review_is_skipped_never_trusted(self):
        for text in (self.HOOKS, self.HOOKS3):
            found = ar.scan('codex', text)
            self.assertEqual([b['id'] for b in found], ['hooks_review'], text)
            self.assertEqual(found[0]['keys'], ar.ESC)           # Esc = skip; Down+Enter would be 「Trust all」 (measured)
            self.assertNotIn(ar.DOWN, found[0]['keys'])
            self.assertIn('hook', found[0]['zh'])
            self.assertIn('not trusted', found[0]['en'])

    def test_new_folder_trust_wording(self):
        self.assertEqual([b['id'] for b in ar.scan('codex', self.FOLDER)], ['trust'])

    def test_hooks_review_wins_over_trust_in_the_accumulated_view(self):
        # round 3: trust (Enter) and the hooks screen both in the text since the last key → the first actionable is Esc
        found = [b for b in ar.scan('codex', self.FOLDER + self.HOOKS) if b['action'] != 'info']
        self.assertEqual((found[0]['id'], found[0]['keys']), ('hooks_review', ar.ESC))
        found = [b for b in ar.scan('codex', self.HOOKS3 + "\n" + self.FOLDER) if b['action'] != 'info']
        self.assertEqual(found[0]['id'], 'hooks_review')


GONE = 'desktop turn writer gone'   # #461f: orphaned() names why the turn is dead (a reason string) or None = not known to be


class UnknownHolders(unittest.TestCase):
    """#461 round 3: a Codex whose /proc/<pid>/fd is unreadable, or lsof failing, is "unknown" → never orphaned."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p461u-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'rollout.jsonl'
        self.path.write_text('{}\n')
        os.utime(self.path, (OLD, OLD))

    def linux(self, cmd, mine=True):
        real_listdir = os.listdir

        def listdir(d):
            if d == '/proc':
                return ['4242', 'self']
            if d == '/proc/4242/fd':
                raise PermissionError(13, 'Permission denied')
            return real_listdir(d)
        return [patch.object(codex_procs, '_linux', return_value=True), patch.object(codex_procs.os, 'listdir', listdir),
                patch.object(codex_procs, 'cmdline', return_value=cmd), patch.object(codex_procs, '_mine', return_value=mine)]

    def run_with(self, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return codex_procs.fd_holders(self.path), shared_codex.orphaned(self.path)

    def test_unreadable_codex_fds_are_unknown(self):
        self.assertEqual(self.run_with(self.linux('/usr/lib/node_modules/@openai/codex/bin/codex app-server')), (None, None))

    def test_unreadable_unrelated_process_is_skipped(self):
        self.assertEqual(self.run_with(self.linux('/usr/bin/sshd -D')), ([], GONE))
        self.assertEqual(self.run_with(self.linux('/usr/bin/python3 -m pytest test_codex.py')), ([], GONE), "not a Codex command")

    def test_another_users_codex_is_skipped(self):
        self.assertEqual(self.run_with(self.linux('/usr/local/bin/codex app-server', mine=False)), ([], GONE))

    def test_lsof_error_is_unknown_and_no_holder_is_an_answer(self):
        for rc, want in ((1, ([], GONE)), (0, ([], GONE)), (2, (None, None)), (127, (None, None))):
            done = subprocess.CompletedProcess(['lsof'], rc, '', 'lsof: err')
            with patch.object(codex_procs, '_linux', return_value=False), \
                    patch.object(codex_procs, '_lsof_exe', return_value='/usr/bin/lsof'), \
                    patch.object(codex_procs.subprocess, 'run', return_value=done):
                self.assertEqual((codex_procs.fd_holders(self.path), shared_codex.orphaned(self.path)), want, rc)


if __name__ == '__main__':
    unittest.main()
