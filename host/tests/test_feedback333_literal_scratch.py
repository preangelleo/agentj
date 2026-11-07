"""Literal initialized error-on-unset syntax has the same scratch operand."""
import _hermetic
import unittest
from agentj import approval_level, danger, policy_registry, routine_uv

class LiteralScratch(unittest.TestCase):
    def floor(self,text):
        inp={'command':text}; requirement=policy_registry.main_requirement('Bash',inp)
        return approval_level.full_access_keep('Bash',inp,danger.classify('Bash',inp),requirement=requirement)
    def test_initialized_literal_scratch_assertion_has_no_extra_uv(self):
        for suffix in ('$D','${D}','${D:?}'):
            self.assertIsNone(self.floor('D=/tmp/agentj-feedback333-fixture; rm -rf "'+suffix+'"'))
    def test_unknown_rebound_non_scratch_and_secret_targets_stay_uv(self):
        for text in ('rm -rf "${D:?}"',
                     'D=/home/owner/documents; rm -rf "${D:?}"',
                     'D=/tmp/agentj-feedback333-fixture; D=/home/owner/documents; rm -rf "${D:?}"',
                     'D=/tmp/.env; rm -rf "${D:?}"',
                     'D=$(unknown); rm -rf "${D:?}"',
                     'D=/tmp/agentj-feedback333-fixture; mv /home/owner/docs "$D"; rm -rf "${D:?}"'):
            with self.subTest(text=text): self.assertEqual(self.floor(text),'uv')
    def test_unproven_service_controls_are_not_routine(self):
        for text in ('agentj service restart','systemctl --user restart other.service','systemctl --user stop other.service'):
            self.assertFalse(routine_uv.proven_routine('Bash',{'command':text}))
