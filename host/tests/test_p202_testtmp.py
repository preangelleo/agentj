"""P202: test fixtures live in memory (tmpfs), not on the system disk; an explicit AGENTJ_TEST_TMP wins; disk only as a fallback."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _testtmp as T

HERE = Path(__file__).resolve().parent


class TestTmpRoot(unittest.TestCase):
    def test_explicit_choice_wins_and_is_checked(self):
        with tempfile.TemporaryDirectory(dir="/tmp" if os.path.isdir("/tmp") else None) as d:
            if len(d) <= T.MAX_ROOT:
                self.assertEqual(T.test_tmp_root({"AGENTJ_TEST_TMP": d}), d)
        for bad in ("relative/dir", "/definitely/not/here", "/" + "x" * 60):
            with self.assertRaises(ValueError):
                T.test_tmp_root({"AGENTJ_TEST_TMP": bad})

    def test_memory_first_then_runtime_dir_then_disk(self):
        mem = {"/tmp": False, "/run/user/7": False}
        with patch.object(T, "in_memory", side_effect=lambda d, need=T.NEED: mem.get(d, False)):
            mem["/tmp"] = True
            self.assertEqual(T.test_tmp_root({"XDG_RUNTIME_DIR": "/run/user/7"}), "/tmp")
            mem["/tmp"] = False; mem["/run/user/7"] = True
            self.assertEqual(T.test_tmp_root({"XDG_RUNTIME_DIR": "/run/user/7"}), "/run/user/7")
            mem["/run/user/7"] = False
            fallback = T.test_tmp_root({"XDG_RUNTIME_DIR": "/run/user/7"})
            self.assertEqual(fallback, "/var/tmp" if os.path.isdir("/var/tmp") else tempfile.gettempdir())

    def test_full_or_disk_backed_tmp_is_not_memory(self):
        with patch.object(T, "_fstype", return_value="tmpfs"), patch.object(T, "free_bytes", return_value=T.NEED - 1):
            self.assertFalse(T.in_memory("/tmp"))                      # never fill memory to make room
        with patch.object(T, "_fstype", return_value="btrfs"), patch.object(T, "free_bytes", return_value=1 << 40):
            self.assertFalse(T.in_memory("/tmp"))
        with patch.object(T, "_fstype", return_value="tmpfs"), patch.object(T, "free_bytes", return_value=1 << 40):
            self.assertEqual(T.in_memory("/tmp"), os.access("/tmp", os.W_OK | os.X_OK))

    def test_quota_headroom_counts(self):
        with patch.object(T, "_quota_left", return_value=5), patch("os.statvfs") as sv:
            sv.return_value.f_bavail, sv.return_value.f_frsize = 1 << 20, 4096
            self.assertEqual(T.free_bytes("/tmp"), 5)

    def test_no_test_writes_fixtures_to_var_tmp_directly(self):
        """The P202 rule for new tests: fixture folders take dir=TEST_TMP (or the default tempfile dir), never dir='/var/tmp'."""
        pat = re.compile(r"""(TemporaryDirectory|mkdtemp|mkstemp|NamedTemporaryFile)\([^)]*dir\s*=\s*['"]/var/tmp""")
        bad = [p.name for p in HERE.glob("*.py") if pat.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
