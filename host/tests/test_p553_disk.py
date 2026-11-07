"""#553: free space follows the Agent's working volume, with honest home fallback."""
import _hermetic  # noqa: F401
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from agentj import resources


class WorkingVolume(unittest.TestCase):
    def sampler(self, get):
        return resources.Sampler(platform="other", home="/home/test", workdir=get)

    def test_workdir_not_home_and_total_decimal_gb(self):
        with mock.patch.object(resources.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(2_000_000_000_000, 500_000_000_000, 1_500_000_000_000)) as usage:
            disk = self.sampler(lambda: "/data/work")._disk(None)
        usage.assert_called_once_with("/data/work")
        self.assertEqual(disk, {"free": .75, "free_gb": 1500., "total_gb": 2000., "scope": "workdir", "busy": None})
        self.assertNotIn("path", disk)

    def test_folder_changes_during_open_watch(self):
        current = ["/disk-a"]
        s = self.sampler(lambda: current[0])
        with mock.patch.object(resources.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(100, 50, 50)) as usage:
            s._disk(None)
            current[0] = "/disk-b"
            s._disk(None)
        self.assertEqual(usage.call_args_list, [mock.call("/disk-a"), mock.call("/disk-b")])

    def test_missing_workdir_falls_back_explicitly(self):
        with mock.patch.object(resources.shutil, "disk_usage", side_effect=[FileNotFoundError(), shutil._ntuple_diskusage(100, 20, 80)]):
            disk = self.sampler(lambda: "/gone")._disk(None)
        self.assertEqual(disk["scope"], "home")
        self.assertEqual(disk["free"], .8)

    def test_no_workdir_and_unreadable_never_invents_space(self):
        with mock.patch.object(resources.shutil, "disk_usage", side_effect=PermissionError()):
            self.assertIsNone(self.sampler(lambda: None)._disk(None))
        with mock.patch.object(resources.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(100, 50, 50)) as usage:
            self.assertEqual(self.sampler(lambda: None)._disk(None)["scope"], "home")
            usage.assert_called_once_with("/home/test")

    def test_real_symlink_follows_destination_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp)/"work"
            dest.mkdir()
            link = pathlib.Path(tmp)/"coding"
            link.symlink_to(dest, target_is_directory=True)
            disk = self.sampler(lambda: str(link))._disk(None)
            actual = shutil.disk_usage(dest)
            self.assertEqual(disk["total_gb"], round(actual.total / 1e9, 1))
            self.assertEqual(disk["scope"], "workdir")


if __name__ == "__main__":
    unittest.main()
