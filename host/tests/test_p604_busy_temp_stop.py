"""Regression: temporary busy workers must not inherit the host stop boundary.

The main Agent may restart/stop its harness while an explicitly registered temp
worker continues. CEO dispatches already run in their own unit; this protects
only the temp worker scope created by the resource-policy trampoline.
"""
import _hermetic  # noqa: F401
import pathlib
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE.parent))
from agentj import resource_policy  # noqa: E402


class TemporaryBusyLifecycle(unittest.TestCase):
    def test_temp_scope_is_not_bound_to_host_service_stop(self):
        calls = []

        class Result:
            returncode = 0

        def run(argv, **kwargs):
            calls.append(argv)
            return Result()

        with patch.dict("os.environ", {"AGENTJ_BUSY_KIND": "temp"}), \
             patch.object(resource_policy.os, "access", return_value=True), \
             patch.object(resource_policy.subprocess, "run", side_effect=run), \
             patch.object(resource_policy.Path, "read_text", return_value="agentj.service\n"), \
             patch.object(resource_policy.time, "monotonic", side_effect=[0, 2]), \
             patch.object(resource_policy.time, "sleep"):
            self.assertFalse(resource_policy._work_scope())

        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertNotIn("BindsTo", argv)
        self.assertNotIn("After", argv)
        self.assertIn("Description", argv)
        self.assertIn("Agent J background work", argv)

    def test_ceo_dispatch_keeps_its_existing_independent_unit(self):
        from agentj.busy_dispatch import manager_command
        command = manager_command("linux", "agentj-p604-ceo", ["/bin/true"], "/tmp", "/dev/null")
        self.assertIn("--unit=agentj-p604-ceo", command)
        self.assertNotIn("BindsTo", command)

    def test_ceo_scope_retains_host_service_binding(self):
        calls = []

        class Result:
            returncode = 0

        with patch.dict("os.environ", {"AGENTJ_BUSY_KIND": "ceo"}), \
             patch.object(resource_policy.os, "access", return_value=True), \
             patch.object(resource_policy.subprocess, "run", side_effect=lambda argv, **kw: (calls.append(argv) or Result())), \
             patch.object(resource_policy.Path, "read_text", return_value="/user.slice/agentj.service"), \
             patch.object(resource_policy.time, "monotonic", side_effect=[0, 2]), \
             patch.object(resource_policy.time, "sleep"):
            resource_policy._work_scope()
        self.assertIn("BindsTo", calls[0])
        self.assertIn("agentj.service", calls[0])
