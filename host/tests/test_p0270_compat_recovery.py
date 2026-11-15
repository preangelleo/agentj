"""Recovered hint and honest hidden-state behavior; no production state or permissions."""
import _hermetic  # noqa: F401
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import danger, doctor, fence, handover, library, plaza, seat_transfer, term_cli, wizard


class WaitHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP', '/tmp'))
        self.addCleanup(self.tmp.cleanup)
        os.chmod(self.tmp.name, 0o700)
        patch = mock.patch.dict(os.environ, {'XDG_RUNTIME_DIR': self.tmp.name})
        patch.start(); self.addCleanup(patch.stop)

    def run_hook(self, command, *, session='one', args=()):
        event = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'session_id': session}
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(json.dumps(event))), contextlib.redirect_stdout(out):
            code = danger.hook_main(list(args))
        return code, json.loads(out.getvalue()) if out.getvalue() else {}

    def test_long_wait_hint_once_and_no_permission_decision(self):
        code, result = self.run_hook('sleep 120')
        self.assertEqual(code, 0)
        hook = result['hookSpecificOutput']
        self.assertIn('agentj busy run', hook['additionalContext'])
        self.assertNotIn('permissionDecision', hook)
        self.assertEqual(self.run_hook('sleep 120'), (0, {}))

    def test_hint_preserves_native_delete_denial_and_research_denial(self):
        _, result = self.run_hook('rm -rf fixture; sleep 120')
        hook = result['hookSpecificOutput']
        self.assertEqual(hook['permissionDecision'], 'deny')
        self.assertIn('trash', hook['permissionDecisionReason'].lower())
        self.assertIn('additionalContext', hook)
        _, result = self.run_hook('printf x > ./research-output; sleep 120', session='research', args=('', 'research'))
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_broken_hint_never_blocks_or_changes_hook(self):
        hints = importlib.import_module('agentj.waithint')
        with mock.patch.object(hints, 'hint', side_effect=OSError('no state')):
            self.assertEqual(self.run_hook('sleep 120'), (0, {}))
        self.assertEqual(self.run_hook('echo short'), (0, {}))

    def test_marker_private_and_background_not_hinted(self):
        hints = importlib.import_module('agentj.waithint')
        d = Path(hints._marker_dir())
        self.assertEqual(d.stat().st_mode & 0o777, 0o700)
        self.assertEqual(d.stat().st_uid, os.getuid())
        self.assertEqual(self.run_hook('agentj busy run -- sleep 120'), (0, {}))
        self.assertEqual(self.run_hook('sleep 120 &'), (0, {}))
        with mock.patch.object(hints, '_marker_dir', return_value=None):
            self.assertEqual(self.run_hook('sleep 120'), (0, {}))


class HiddenState(unittest.TestCase):
    def setUp(self):
        self.st = SimpleNamespace(exists=lambda: False)

    def test_missing_fenced_library_plaza_wizard_are_not_init_advice(self):
        with mock.patch('agentj.state.State', return_value=self.st), mock.patch.object(fence, 'inside', return_value=True):
            for mod, error in ((library, library.LibraryError), (plaza, plaza.PlazaError), (wizard, wizard.WizardError)):
                with self.subTest(module=mod.__name__), self.assertRaises(error) as ctx:
                    mod._state()
                self.assertEqual(str(ctx.exception), fence.hidden_state())

    def test_missing_fenced_handover_is_unknown_in_both_languages(self):
        with mock.patch.object(handover, 'State', return_value=self.st), mock.patch.object(fence, 'inside', return_value=True):
            facts = handover.facts()
        self.assertFalse(facts['set_up'])
        self.assertTrue(facts['fenced'])
        for render in (handover._zh, handover._en):
            self.assertEqual(render(facts).splitlines()[0], fence.hidden_state())

    def test_missing_fenced_doctor_rows_never_infer_absence(self):
        with mock.patch.object(doctor, '_fenced', return_value=True):
            for name in ('agent', 'passphrase', 'bound', 'seat', 'onboarding', 'estop', 'tasks'):
                with self.subTest(check=name):
                    row = getattr(doctor, 'check_' + name)(self.st)
                    self.assertTrue(row['unverifiable'])
                    self.assertEqual(row['status'], doctor.WARN)

    def test_missing_fenced_terminal_and_seat_are_not_init_advice(self):
        with mock.patch('agentj.state.State', return_value=self.st), mock.patch.object(fence, 'inside', return_value=True):
            for call in (lambda: term_cli.cmd_terminal(SimpleNamespace(action='status')), lambda: seat_transfer.cmd(SimpleNamespace())):
                with self.subTest(call=call), self.assertRaises(SystemExit) as ctx:
                    call()
                self.assertIn(fence.hidden_state(), str(ctx.exception))

    def test_outside_fence_keeps_genuine_uninitialized_message(self):
        with mock.patch('agentj.state.State', return_value=self.st), mock.patch.object(fence, 'inside', return_value=False):
            with self.assertRaises(library.LibraryError) as ctx:
                library._state()
        self.assertIn('agentj init', str(ctx.exception))
        self.assertNotIn('hidden inside', str(ctx.exception))
