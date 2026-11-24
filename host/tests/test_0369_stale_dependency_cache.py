"""0.30.69: a wheelhouse an old updater (0.30.65 and earlier) left group-writable under umask 0002.

The old updater wrote dependency wheels with the process umask (0664 on Ubuntu) and then refused its own files as
cache_entry_invalid, leaving the directory behind for every later attempt. A current updater must not be stopped
by that leftover: a provably private group is tightened in place, anything else that is only group-writable is
discarded and rebuilt from verified bytes. Really unsafe entries still stop, untouched, with one repair line.
"""
import hashlib
import os
from pathlib import Path
import shlex
import stat
import tempfile
from unittest import TestCase, mock

from _testtmp import TEST_TMP
from agentj import auto_update, upgrade_wheels as wheels
from test_r0018_offline_upgrade import wheel

PRIVATE = 'agentj.install_permissions._private_group'


class StaleDependencyCache(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP, prefix='aj0369-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dep = wheel('dep', '1')
        self.name = 'dep-1-py3-none-any.whl'
        url = 'https://files.pythonhosted.org/packages/00/00/' + '0' * 60 + '/' + self.name
        self.lock = ('[[package]]\nname="dep"\nwheels=[{url="' + url + '", hash="sha256:'
                     + hashlib.sha256(self.dep).hexdigest() + '", size=' + str(len(self.dep)) + '}]').encode()
        self.pin = auto_update.pin_artifact(wheel('agentj', '1.0.0', 'dep==1', self.lock), '1.0.0', self.root, signed=True)
        for patcher in (mock.patch.object(wheels, 'selected_wheels', return_value=[self.name]),
                        mock.patch.object(wheels, 'run_resolver')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.downloads = 0
        self.house = self.build()
        self.entry = self.house / self.name
        self.downloads = 0

    def fetch(self, row, timeout):
        self.downloads += 1
        return self.dep

    def build(self, *, prepare=True):
        with mock.patch.object(wheels, 'download_dependency', side_effect=self.fetch):
            return wheels.wheelhouse(self.pin, self.lock, 'uv', prepare=prepare)

    def old_updater_leftover(self, mode=0o664):
        """What 0.30.65 leaves: Path.write_bytes under umask 0002."""
        self.entry.chmod(mode)

    def mode(self):
        return stat.S_IMODE(self.entry.lstat().st_mode)

    def failure(self, *, prepare=True):
        with self.assertRaises(wheels.PreparationFailure) as caught:
            self.build(prepare=prepare)
        return caught.exception

    def test_private_group_leftover_is_tightened_in_place(self):
        self.old_updater_leftover()
        inode = self.entry.stat().st_ino
        with mock.patch(PRIVATE, return_value=True):
            self.assertEqual(self.build(), self.house)
        self.assertEqual((self.mode(), self.entry.stat().st_ino, self.downloads), (0o644, inode, 0))

    def test_unproven_group_leftover_is_discarded_and_rebuilt(self):
        self.old_updater_leftover()
        # Keep the old inode live while rebuilding so the filesystem cannot
        # immediately recycle it for the new directory entry.
        with self.entry.open('rb') as old_entry, mock.patch(PRIVATE, return_value=False):
            self.assertEqual(self.build(), self.house)
            self.assertNotEqual(self.entry.stat().st_ino, os.fstat(old_entry.fileno()).st_ino)
        self.assertEqual((self.mode(), self.downloads), (0o600, 1))
        self.assertEqual(self.entry.read_bytes(), self.dep)
        self.assertEqual(sorted(p.name for p in self.house.parent.iterdir()), [self.house.name])

    def test_rollback_never_rebuilds_and_names_one_repair_line(self):
        self.old_updater_leftover()
        with mock.patch(PRIVATE, return_value=False):
            error = self.failure(prepare=False)
        self.assertEqual((str(error), error.fields['artifact_stage'], error.fields['subreason']),
                         ('install_artifact_changed', 'dependency_cache', 'unsafe_store'))
        self.assertEqual(error.fields['fix'], 'rm -rf -- ' + shlex.quote(str(self.house)))
        self.assertEqual((self.mode(), self.downloads), (0o664, 0))

    def test_other_writable_entry_stops_untouched(self):
        self.old_updater_leftover(0o666)
        for private in (True, False):
            with mock.patch(PRIVATE, return_value=private):
                error = self.failure()
            self.assertEqual(error.fields['subreason'], 'unsafe_store')
            self.assertEqual(error.fields['fix'], 'rm -rf -- ' + shlex.quote(str(self.house)))
            self.assertEqual((self.mode(), self.downloads), (0o666, 0))

    def test_acl_entry_stops_untouched(self):
        self.old_updater_leftover()
        entry = self.entry.stat().st_ino
        acl = lambda fd: os.fstat(fd).st_ino == entry
        with mock.patch(PRIVATE, return_value=False), mock.patch('agentj.install_permissions._linux_acl', side_effect=acl):
            error = self.failure()
        self.assertEqual(error.fields['subreason'], 'unsafe_store')
        self.assertEqual((self.mode(), self.downloads), (0o664, 0))

    def test_link_out_of_the_cache_stops_and_is_never_followed_or_removed(self):
        outside = self.root / 'outside.whl'
        outside.write_bytes(self.dep)
        outside.chmod(0o664)
        self.entry.unlink()
        self.entry.symlink_to(outside)
        with mock.patch(PRIVATE, return_value=False):
            error = self.failure()
        self.assertEqual((error.fields['artifact_stage'], error.fields['subreason']),
                         ('dependency_cache', 'cache_entry_invalid'))
        self.assertEqual(error.fields['fix'], 'rm -rf -- ' + shlex.quote(str(self.house)))
        self.assertTrue(self.entry.is_symlink())
        self.assertEqual((stat.S_IMODE(outside.stat().st_mode), outside.read_bytes(), self.downloads), (0o664, self.dep, 0))

    def test_discard_refuses_an_entry_holding_anything_but_own_files(self):
        self.old_updater_leftover()
        (self.house / 'nested').mkdir()
        (self.house / 'nested' / 'keep').write_text('keep')
        with mock.patch(PRIVATE, return_value=False):
            error = self.failure()
        self.assertEqual(error.fields['subreason'], 'cache_entry_invalid')
        self.assertEqual(((self.house / 'nested' / 'keep').read_text(), self.mode(), self.downloads), ('keep', 0o664, 0))

    def test_foreign_owner_entry_is_never_discarded(self):
        self.old_updater_leftover()
        with mock.patch(PRIVATE, return_value=False), mock.patch.object(wheels, '_own_file', return_value=False):
            error = self.failure()
            with self.assertRaises(wheels.PreparationFailure) as direct:
                wheels.discard_entry(self.house.parent, self.house.name)
        for found in (error, direct.exception):
            self.assertEqual(found.fields['subreason'], 'cache_entry_invalid')
            self.assertEqual(found.fields['fix'], 'rm -rf -- ' + shlex.quote(str(self.house)))
        self.assertEqual((self.mode(), self.entry.read_bytes(), self.downloads), (0o664, self.dep, 0))

    def test_discard_stays_inside_a_private_store(self):
        self.old_updater_leftover()
        self.house.chmod(0o750)
        with self.assertRaises(wheels.PreparationFailure) as caught:
            wheels.discard_entry(self.house.parent, self.house.name)
        self.assertEqual(caught.exception.fields['subreason'], 'unsafe_store')
        link = self.house.parent / 'link'
        link.symlink_to(self.house, target_is_directory=True)
        with self.assertRaises(OSError):
            wheels.discard_entry(self.house.parent, 'link')
        self.assertEqual(self.entry.read_bytes(), self.dep)

    def test_rebuild_is_recorded_in_the_host_log(self):
        self.old_updater_leftover()
        seen = []
        with mock.patch(PRIVATE, return_value=False), mock.patch.object(wheels, 'permission_log', return_value=seen.append):
            self.build()
        self.assertEqual(len(seen), 1)
        self.assertIn(str(self.house), seen[0])
