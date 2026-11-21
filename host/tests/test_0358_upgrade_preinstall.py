"""Prepared nightly jobs report the real failure and only harden owned cache dirs."""
import hashlib
import io
import os
from pathlib import Path
import stat
import tempfile
from unittest import TestCase, mock

from agentj import auto_update, support, update, upgrade_diag, upgrade_wheels as wheels
from test_0334_upgrade_retry import _Env
from test_r0018_offline_upgrade import wheel


class NightlyInstallStore(_Env):
    def test_private_mode_recovers_existing_owned_readonly_store(self):
        folder = self.st.root / auto_update.ARTIFACTS
        folder.mkdir(mode=0o700)
        folder.chmod(0o755)
        result = self.attempt([0])
        self.assertEqual((result["result"], result["reason"]), ("ok", "upgraded"))
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_unsafe_store_refusal_reports_install_and_closed_evidence(self):
        outside = self.root / "other"
        outside.mkdir(mode=0o700)
        folder = self.st.root / auto_update.ARTIFACTS
        folder.symlink_to(outside, target_is_directory=True)
        result = self.attempt([])
        self.assertEqual((result["result"], result["reason"]), ("failed", "install_artifact_changed"))
        self.assertEqual((result["step"], result["failure_stage"], result["phase"]), ("install", "install", "prepared"))
        self.assertEqual((self.installs, self.rollbacks), ([], []))
        body = upgrade_diag.metadata_body(self.sent[0])
        self.assertTrue(support.metadata_ok(body))
        for expected in ("step: install", "failure_stage: install", "artifact_stage: install_store",
                         "subreason: unsafe_store"):
            self.assertIn(expected, body.splitlines())
        self.assertNotIn(str(outside), body)
        self.assertEqual(list(outside.iterdir()), [])

    def test_shared_writable_store_remains_untouched(self):
        folder = self.st.root / auto_update.ARTIFACTS
        folder.mkdir(mode=0o700)
        folder.chmod(0o777)
        result = self.attempt([])
        self.assertEqual((result["reason"], self.installs, self.rollbacks), ("install_artifact_changed", [], []))
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o777)

    def test_private_state_root_policy_is_not_repaired_by_install(self):
        self.st.root.chmod(0o755)
        result = self.attempt([])
        self.assertEqual((result["reason"], self.installs), ("install_artifact_changed", []))
        self.assertEqual(stat.S_IMODE(self.st.root.stat().st_mode), 0o755)

    def test_store_acl_must_be_proven_absent(self):
        folder = self.st.root / auto_update.ARTIFACTS
        folder.mkdir(mode=0o700)
        folder.chmod(0o755)
        with mock.patch.object(auto_update, "_no_extended_store_acl", return_value=False):
            result = self.attempt([])
        self.assertEqual((result["reason"], self.installs), ("install_artifact_changed", []))
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o755)

    def test_nonempty_store_needs_independent_repair(self):
        folder = self.st.root / auto_update.ARTIFACTS
        folder.mkdir(mode=0o755)
        marker = folder / "existing"
        marker.write_bytes(b"preserve")
        folder.chmod(0o755)
        result = self.attempt([])
        self.assertEqual((result["reason"], self.installs), ("install_artifact_changed", []))
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o755)
        self.assertEqual(marker.read_bytes(), b"preserve")

    def test_parent_acl_check_runs_only_for_recovery(self):
        folder = self.st.root / auto_update.ARTIFACTS
        folder.mkdir(mode=0o755)
        folder.chmod(0o755)
        parent_inode = self.st.root.stat().st_ino
        checked = []

        def no_acl(fd):
            inode = os.fstat(fd).st_ino
            checked.append(inode)
            return inode != parent_inode

        with mock.patch.object(auto_update, "_no_extended_store_acl", side_effect=no_acl):
            with self.assertRaisesRegex(ValueError, "install_artifact_changed"):
                auto_update.pin_artifact(b"fixture", "1.0.0", self.st.root)
            self.assertEqual(checked, [parent_inode])
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o755)
            folder.chmod(0o700)
            self.assertEqual(auto_update.pin_artifact(b"fixture", "1.0.0", self.st.root).path.read_bytes(), b"fixture")
            self.assertEqual(checked, [parent_inode])


class DependencyStore(TestCase):
    def test_owned_readonly_directory_is_hardened_only_during_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = b"package=[]\n"
            pin = auto_update.pin_artifact(wheel("agentj", "1.0.0", lock=lock), "1.0.0", root)
            cache = pin.path.parent / "dependencies"
            cache.mkdir(mode=0o700)
            cache.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "install_artifact_changed"):
                wheels.wheelhouse(pin, lock, "uv", prepare=False)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o755)
            with mock.patch.object(wheels, 'run_resolver'), mock.patch.object(wheels, 'selected_wheels', return_value=set()):
                house = wheels.wheelhouse(pin, lock, "uv", prepare=True)
            self.assertTrue(house.is_dir())
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)

    def test_dependency_cache_links_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = b"package=[]\n"
            pin = auto_update.pin_artifact(wheel("agentj", "1.0.0", lock=lock), "1.0.0", root)
            outside = root / "other"
            outside.mkdir(mode=0o700)
            (pin.path.parent / "dependencies").symlink_to(outside, target_is_directory=True)
            with mock.patch.object(wheels, "run_resolver") as resolver:
                with self.assertRaises(wheels.PreparationFailure) as caught:
                    wheels.wheelhouse(pin, lock, "uv", prepare=True)
            self.assertEqual((str(caught.exception), caught.exception.fields["artifact_stage"],
                              caught.exception.fields["subreason"]),
                             ("install_artifact_changed", "dependency_cache", "unsafe_store"))
            resolver.assert_not_called()
            self.assertEqual(list(outside.iterdir()), [])

    def test_dependency_cache_acl_bound_to_open_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = b"package=[]\n"
            pin = auto_update.pin_artifact(wheel("agentj", "1.0.0", lock=lock), "1.0.0", root)
            parent_inode = pin.path.parent.stat().st_ino
            cache = pin.path.parent / "dependencies"
            cache.mkdir(mode=0o700)
            cache.chmod(0o755)
            checked = []

            def no_acl(fd):
                inode = os.fstat(fd).st_ino
                checked.append(inode)
                return inode != parent_inode

            with mock.patch.object(auto_update, "_no_extended_store_acl", side_effect=no_acl):
                with self.assertRaisesRegex(wheels.PreparationFailure, "install_artifact_changed"):
                    wheels.wheelhouse(pin, lock, "uv", prepare=True)
            self.assertEqual(checked, [parent_inode])
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o755)
            self.assertEqual(list(cache.iterdir()), [])

    def test_transport_truncation_retries_with_or_without_declared_length(self):
        payload = b"good"
        row = {"url": "https://files.pythonhosted.org/packages/00/00/" + "0" * 60 + "/dep-1.whl",
               "size": len(payload), "hash": "sha256:" + hashlib.sha256(payload).hexdigest()}
        for headers in ({}, {"Content-Length": "0004"}):
            with self.subTest(headers=headers):
                broken, complete = io.BytesIO(payload[:-1]), io.BytesIO(payload)
                broken.headers, complete.headers = headers, {}
                waits = []
                with mock.patch("agentj.update.open_download", side_effect=[broken, complete]) as fetch:
                    retry = wheels.PreparationRetry(clock=lambda: 0, sleep=waits.append)
                    body = retry.call(lambda timeout: wheels.download_dependency(row, timeout), 90, phase="download")
                self.assertEqual((body, retry.count, len(waits), fetch.call_count), (payload, 1, 1, 2))

    def test_complete_wrong_bytes_and_short_wrong_length_stop_before_install(self):
        filename = "dep-1-py3-none-any.whl"
        url = "https://files.pythonhosted.org/packages/00/00/" + "0" * 60 + "/" + filename
        digest = hashlib.sha256(b"good").hexdigest()
        lock = (f'[[package]]\nname="dep"\nwheels=[{{url="{url}", hash="sha256:{digest}", size=4}}]\n').encode()
        for payload, reason in ((b"bad", "size_mismatch"), (b"evil", "hash_mismatch")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pins = [auto_update.pin_artifact(wheel("agentj", v, lock=lock), v, root, signed=True)
                        for v in ("1.0.0", "2.0.0")]
                cmd = auto_update.Command(["uv", "tool", "install", str(pins[1].path)], {})
                response = io.BytesIO(payload)
                response.headers = {"Content-Length": str(len(payload))}
                run = mock.Mock()
                with mock.patch.object(update, "_pin_plan_wheel", return_value=(cmd, pins[1])), \
                     mock.patch.object(wheels, "selected_wheels", return_value={filename}), \
                     mock.patch.object(wheels, "run_resolver") as resolver, \
                     mock.patch.object(update, "open_download", return_value=response) as fetch:
                    rc, _, fields = update.install_step(run, cmd, lambda _: None, {"from": "1.0.0"},
                                                        "2.0.0", root, sealed=True)
                self.assertEqual((rc, fields["reason"], fields["artifact_stage"], fields["subreason"],
                                  fields["retry_count"], fields["_untouched"]),
                                 (1, "install_artifact_changed", "dependency_wheel", reason, 0, True))
                run.assert_not_called()
                resolver.assert_not_called()
                self.assertEqual(fetch.call_count, 1)
