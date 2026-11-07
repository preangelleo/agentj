"""Offline preparation tests; no elevation, scheduler or external state access."""
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from agentj import native_scheduler_control as native


class ReadonlyPreparation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.path = self.root / 'crontab'
        self.raw = b'# untouched\n0 1 * * * root /bin/fixture\n'
        self.path.write_bytes(self.raw)
        self.backend = native.Backend(platform='linux', system_directory=temporary.name,
                                      uid=os.geteuid(),
                                      run=lambda argv: self.fail('OS execution forbidden'))
        self.target = dict(platform='linux', source='cron-system', scope='system',
                           id='crontab:2', snapshot='0' * 64, op='disable')

    def test_readonly_snapshot_does_not_require_parent_write_access(self):
        with patch.object(native.os, 'access', return_value=False):
            snap = self.backend.source_snapshot(self.target, readonly=True)
        self.assertTrue(snap['enabled'])
        self.assertEqual(self.path.read_bytes(), self.raw)
        with patch.object(native.os, 'access', return_value=False):
            with self.assertRaisesRegex(native.Refused, 'os_privilege_required'):
                self.backend.source_snapshot(self.target)

    def test_readonly_does_not_accept_arbitrary_non_root_owner(self):
        # This fixture's real owner is admitted only when it is root or the configured uid.
        self.backend.uid = os.geteuid() + 1
        actual = os.fstat
        def foreign(fd):
            info = actual(fd)
            if info.st_ino == self.path.stat().st_ino:
                values = list(info)
                values[4] = self.backend.uid + 1
                return os.stat_result(values)
            return info
        with patch.object(native.os, 'fstat', side_effect=foreign):
            with self.assertRaisesRegex(native.Refused, 'os_privilege_required'):
                self.backend.source_snapshot(self.target, readonly=True)

    def test_readonly_preserves_symlink_rejection(self):
        self.path.unlink()
        other = self.root / 'other'
        other.write_bytes(self.raw)
        self.path.symlink_to(other)
        with self.assertRaises(OSError):
            self.backend.source_snapshot(self.target, readonly=True)


class ExactPlan(ReadonlyPreparation):
    def prepare(self, guard=lambda: None):
        from agentj import native_scheduler_plan as plans
        from test_native_scheduler_control369 import FakeState
        controller = native.Controller(FakeState(str(self.root)), self.backend)
        self.target['snapshot'] = self.backend.source_snapshot(self.target, readonly=True)['snapshot']
        return plans.prepare(controller, self.target, guard)

    def test_plan_binds_exact_delta_without_writing(self):
        import base64
        plan = self.prepare()
        self.assertEqual(plan['path'], str(self.path))
        self.assertEqual(base64.b64decode(plan['original_bytes']), self.raw)
        desired = base64.b64decode(plan['desired_bytes'])
        self.assertEqual(desired.splitlines()[0], b'# untouched')
        self.assertTrue(desired.splitlines()[1].startswith(b'# agentj-disabled:'))
        self.assertTrue(desired.endswith(self.raw.splitlines(keepends=True)[1]))
        self.assertEqual(plan['stat'][6], self.path.stat().st_uid)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse((self.root / 'native-scheduler-controls.json').exists())

    def test_plan_checks_live_guard_after_read(self):
        calls = []
        def guard():
            calls.append(None)
            if len(calls) == 2:
                raise native.Refused('no_device')
        with self.assertRaisesRegex(native.Refused, 'no_device'):
            self.prepare(guard)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_source_replacement_during_preparation_refused(self):
        calls = []
        def guard():
            calls.append(None)
            if len(calls) == 2:
                self.path.write_bytes(self.raw + b'# third party\n')
        with self.assertRaisesRegex(native.Refused, 'changed'):
            self.prepare(guard)
        self.assertEqual(self.path.read_bytes(), self.raw + b'# third party\n')

    def test_unknown_target_rejected_before_read(self):
        self.target['source'] = 'unknown'
        with self.assertRaisesRegex(native.Refused, 'read_only'):
            self.prepare()

    def test_restore_plan_keeps_exact_original_and_current_preimage(self):
        import base64
        from agentj import native_scheduler_plan as plans
        from test_native_scheduler_control369 import FakeState
        controller = native.Controller(FakeState(str(self.root)), self.backend)
        self.target['snapshot'] = self.backend.snapshot(self.target)['snapshot']
        controller.apply(self.target, lambda: None)
        disabled = self.path.read_bytes()
        self.target.update(op='restore', snapshot=self.backend.snapshot(self.target)['snapshot'])
        plan = plans.prepare(controller, self.target, lambda: None)
        self.assertEqual(base64.b64decode(plan['preimage_bytes']), disabled)
        self.assertEqual(base64.b64decode(plan['desired_bytes']), self.raw)
        self.assertEqual(self.path.read_bytes(), disabled)
        self.assertTrue(controller.journal())

    def test_marker_without_journal_is_refused(self):
        self.path.write_bytes(b'# untouched\n# agentj-disabled:spoof 0 1 * * * root /bin/fixture\n')
        with self.assertRaisesRegex(native.Refused, 'marker_spoof'):
            self.prepare()

    def test_canonical_plan_binds_bytes_and_journal(self):
        from agentj import native_scheduler_plan as plans
        plan = self.prepare()
        encoded = plans.canonical(plan)
        self.assertEqual(encoded, plans.canonical(dict(reversed(list(plan.items())))))
        changed = dict(plan, desired_bytes='')
        self.assertNotEqual(encoded, plans.canonical(changed))
        self.assertIn(b'journal_digest', encoded)
