"""Classifier snapshots never authorize future mutable script-path contents."""
import _hermetic
import os
import shlex
from pathlib import Path
import tempfile
import unittest
from agentj.routine_uv import proven_routine

class MutableScriptIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def test_existing_shell_stays_held_before_and_after_replacement(self):
        p=self.root/'routine.sh';p.write_text('printf safe')
        cmd='bash '+shlex.quote(str(p))
        self.assertFalse(proven_routine('Bash',{'command':cmd},cwd=str(self.root)))
        p.replace(self.root/'old.sh');p.write_text('rm -rf /synthetic-only-target')
        self.assertFalse(proven_routine('Bash',{'command':cmd},cwd=str(self.root)))
    def test_existing_python_stays_held(self):
        p=self.root/'routine.py';p.write_text('print(1)')
        self.assertFalse(proven_routine('Bash',{'command':'python3 '+shlex.quote(str(p))},cwd=str(self.root)))
    def test_literal_producer_does_not_bind_path_execution(self):
        p=shlex.quote(str(self.root/'routine.sh'))
        cmd="cat > "+p+" <<'SH'\nprintf safe\nSH\nbash "+p
        self.assertFalse(proven_routine('Bash',{'command':cmd},cwd=str(self.root)))
    def test_inline_program_needs_no_future_script_path(self):
        self.assertTrue(proven_routine('Bash',{'command':"bash -c 'printf safe'"},cwd=str(self.root)))
