"""No real state is used: mock passwd home inside a temporary fixture."""
import _hermetic
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from agentj import busy

class BusyTestRoot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']);self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'passwd-home';self.home.mkdir()
        self.root=self.home/'.local/state/agentj'
    def test_explicit_real_root_is_refused_before_any_directory_or_file(self):
        with patch('pwd.getpwuid',return_value=SimpleNamespace(pw_dir=str(self.home))),patch.dict(os.environ,{'AGENTJ_TEST':'1'}):
            for root in (self.root,self.home/'.local/state/agentj-2',self.home/'.local/state/agentjarvis-alpha'):
                with self.assertRaisesRegex(RuntimeError,'isolated state root'):busy.write_item(root,{'key':'synthetic'})
                self.assertFalse(root.exists())
    def test_alias_to_real_root_is_refused(self):
        alias=Path(self.tmp.name)/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with patch('pwd.getpwuid',return_value=SimpleNamespace(pw_dir=str(self.home))):
            with self.assertRaises(RuntimeError):busy.items_dir(alias)
    def test_isolated_state_still_writes_and_removes(self):
        isolated=Path(self.tmp.name)/'isolated'
        with patch('pwd.getpwuid',return_value=SimpleNamespace(pw_dir=str(self.home))):
            path=busy.write_item(isolated,{'key':'synthetic'})
            self.assertTrue(path.is_file());self.assertTrue(busy.remove_item(isolated,'synthetic'))
    def test_remove_never_deletes_simulated_real_state(self):
        target=self.root/'agentperm/busy/synthetic.json';target.parent.mkdir(parents=True);target.write_text('{}')
        with patch('pwd.getpwuid',return_value=SimpleNamespace(pw_dir=str(self.home))):
            with self.assertRaises(RuntimeError):busy.remove_item(self.root,'synthetic')
        self.assertEqual(target.read_text(),'{}')
