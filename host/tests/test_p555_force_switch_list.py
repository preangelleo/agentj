"""#555: registry-driven command facts, no availability-based hiding."""
import _hermetic  # noqa: F401
import unittest
from unittest import mock
from agentj import cmdrun, harness

class RegistryCommands(unittest.TestCase):
    def test_all_supported_switches_even_when_absent(self):
        with mock.patch.object(harness, 'detect', return_value={'harnesses': []}):
            names = cmdrun.available_commands()
        self.assertEqual({n[7:] for n in names if n.startswith('switch_')}, set(harness.SUPPORTED))

    def test_public_metadata_projects_safe_fields_only(self):
        rows = [{'name': n, 'installed': False, 'logged_in': False, 'supported': True,
                 'executable': '/private/native', 'note': 'private native diagnostic'} for n in harness.SUPPORTED]
        with mock.patch.object(harness, 'detect', return_value={'harnesses': rows}):
            facts = cmdrun.command_harnesses(detect=True)
        self.assertEqual([r['name'] for r in facts], list(harness.SUPPORTED))
        for r in facts:
            self.assertEqual(set(r), {'name','label','installed','logged_in'})
            self.assertEqual(r['label'], harness.LABEL[r['name']])
            self.assertIs(r['installed'], False)
            self.assertIs(r['logged_in'], False)

    def test_initial_metadata_is_complete_without_native_calls(self):
        with mock.patch.object(harness, 'detect', side_effect=AssertionError('must be asynchronous')):
            facts = cmdrun.command_harnesses()
        self.assertEqual([r['name'] for r in facts], list(harness.SUPPORTED))
        self.assertTrue(all(r['installed'] is None and r['logged_in'] is None for r in facts))

    def test_registry_drives_fixed_argv(self):
        for name in harness.SUPPORTED:
            self.assertEqual(cmdrun.RUN_CMDS['switch_'+name][0], ('agent','switch',name,'--force'))
