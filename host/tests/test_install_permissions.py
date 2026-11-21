"""The updater and website share the same permission guard."""
import _hermetic
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from agentj import install_permissions as permissions


@unittest.skipUnless(os.name == "posix" and hasattr(os, "listxattr"), "Linux ACLs")
class InstallationPermissions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefix = self.root / "uv/tools/agentj"
        self.bin = self.prefix / "bin"
        self.bin.mkdir(parents=True)
        self.launcher = self.bin / "agentj"
        self.launcher.write_text("fixture")
        self.launcher.chmod(0o775)

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_private_group_hardens_launcher_ancestors_and_package_tree(self):
        self.prefix.parent.chmod(0o775)
        package = self.prefix / "packages"
        package.mkdir(mode=0o700)
        module = package / "example.py"
        module.write_text("fixture")
        module.chmod(0o664)
        notes = []
        with patch.object(permissions, "_private_group", return_value=True):
            permissions.secure_paths([(self.launcher, True)], note=notes.append)
            permissions.secure_tree(self.prefix, self.launcher, note=notes.append)
        self.assertEqual(self.mode(self.prefix.parent), 0o755)
        self.assertEqual(self.mode(self.launcher), 0o755)
        self.assertEqual(self.mode(module), 0o644)
        self.assertEqual(len(notes), 3)

    def test_shared_group_is_refused_with_exact_path_and_fix(self):
        with patch.object(permissions, "_private_group", return_value=False):
            with self.assertRaises(permissions.UnsafeInstallation) as fail:
                permissions.secure_paths([(self.launcher, True)])
        self.assertEqual(fail.exception.paths, [str(self.launcher)])
        self.assertEqual(fail.exception.fix, f"chmod go-w -- {self.launcher}")
        self.assertEqual(self.mode(self.launcher), 0o775)

    def test_world_write_and_acl_are_refused_even_with_a_private_group(self):
        with patch.object(permissions, "_private_group", return_value=True):
            self.launcher.chmod(0o777)
            with self.assertRaisesRegex(permissions.UnsafeInstallation, "unsafe_installation_permissions"):
                permissions.secure_paths([(self.launcher, True)])
            self.assertEqual(self.mode(self.launcher), 0o777)
            self.launcher.chmod(0o775)
            ino = self.launcher.stat().st_ino
            with patch.object(permissions, "_linux_acl", side_effect=lambda fd: os.fstat(fd).st_ino == ino):
                with self.assertRaises(permissions.UnsafeInstallation) as fail:
                    permissions.secure_paths([(self.launcher, True)])
            self.assertIn(f"setfacl -b -- {self.launcher}", fail.exception.fix)
            self.assertEqual(self.mode(self.launcher), 0o775)

    def test_does_not_follow_an_unexpected_venv_symlink(self):
        outside = self.root / "other"
        outside.write_text("fixture")
        outside.chmod(0o666)
        link = self.bin / "unrelated"
        link.symlink_to(outside)
        with patch.object(permissions, "_private_group", return_value=True):
            with self.assertRaises(permissions.UnsafeInstallation) as fail:
                permissions.secure_tree(self.prefix, self.launcher)
        self.assertEqual(fail.exception.paths, [str(link)])
        self.assertEqual(self.mode(outside), 0o666)

    def test_launch_symlink_chain_through_writable_parent_is_refused(self):
        shared = self.root / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        (shared / "python").symlink_to(self.launcher)
        link = self.bin / "python"
        link.symlink_to(shared / "python")
        self.assertEqual(link.resolve(), self.launcher)
        with self.assertRaises(permissions.UnsafeInstallation) as fail:
            permissions.resolve_trusted(link)
        self.assertEqual(fail.exception.paths, [str(shared)])

    def test_symlink_dotdot_does_not_skip_an_untrusted_parent(self):
        shared = self.root / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        link = self.bin / "python"
        link.symlink_to(shared / ".." / self.launcher.relative_to(self.root))
        with self.assertRaises(permissions.UnsafeInstallation) as fail:
            permissions.resolve_trusted(link)
        self.assertEqual(fail.exception.paths, [str(shared)])
