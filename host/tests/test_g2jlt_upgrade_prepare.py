"""A resolver can fail before uv touches the live tool. Exercise both sealed update entry points."""
import _hermetic  # noqa: F401
import hashlib
import io
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from agentj import auto_update as au, support, update, upgrade_diag, upgrade_wheels
from test_0334_upgrade_retry import CONFLICT_ERR, NET_ERR, _Env
from test_r0018_offline_upgrade import wheel

OLD = "0.30.47"
NEW = "0.30.48"


@unittest.skipUnless(shutil.which("uv"), "requires uv")
class ResolverPreparation(_Env):
    def setUp(self):
        super().setUp()
        self.sleeps = []
        self.now = 100.0
        self.calls = self.root / "resolver-calls"
        self.uv = self.root / "uv"
        self.real_uv = shutil.which("uv")
        self.cache = self.st.root / "upgrade-cache"
        self.cache.mkdir(mode=0o700, exist_ok=True)
        self.info = {"kind": "uv", "where": str(self.root / "tools" / "agentj")}
        lock = b"package = []\n"
        self.files = {}
        for version in (OLD, NEW):
            blob = wheel("agentj", version, lock=lock)
            self.put_wheel(version, blob)

        # Use the normal cache reader and pinning. Cryptographic receipt verification has its own suite.
        def verify(data, signature, keys):
            for version, path in self.files.items():
                if signature == "fixture" and data == path.read_bytes():
                    return f"agentj-host/v1 version={version} sha256={hashlib.sha256(data).hexdigest()}"
            return None

        patcher = patch.object(au.minisign, "verify", side_effect=verify)
        patcher.start()
        self.addCleanup(patcher.stop)

    def put_wheel(self, version, blob):
        path = self.cache / au.artifact_name(version)
        self.st.write_private(path, blob)
        receipt = {"signed": True, "sha256": hashlib.sha256(blob).hexdigest(), "signature": "fixture"}
        self.st.write_private(path.with_suffix(".verified.json"), json.dumps(receipt).encode())
        self.files[version] = path

    def resolver(self, errors):
        # Only the selected uv subprocess failures are injected. Successful resolution and
        # the offline dry run use the actual uv binary, against dependency-free fixture wheels.
        self.uv.write_text(f"""#!{sys.executable}
import os, sys
from pathlib import Path

if sys.argv[1:3] == ['pip', 'compile']:
    calls = Path({str(self.calls)!r})
    n = int(calls.read_text()) if calls.exists() else 0
    calls.write_text(str(n + 1))
    errors = {errors!r}
    if n < len(errors):
        sys.stderr.write(errors[n])
        sys.exit(1)
os.execv({self.real_uv!r}, [{self.real_uv!r}, *sys.argv[1:]])
""")
        self.uv.chmod(0o700)

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay

    def manual(self, lease_end=None):
        self.launched = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        out = {"from": OLD, "to": NEW}
        cmd = au.Command([str(self.uv), "tool", "install", "--force", str(self.files[NEW])], au.install_env(self.info))
        with patch.object(update, "_install_sleep", self.sleep), patch.object(update, "_install_clock", lambda: self.now):
            rc, _, fields = update.install_step(self.launched, cmd, lambda s: None, out, NEW, self.st.root,
                                                   sealed=True, lease_end=lease_end)
        return rc, {**out, **fields}

    def test_manual_transient_resolver_recovers_then_launches_offline(self):
        self.resolver([NET_ERR])
        rc, fields = self.manual()
        self.assertEqual((rc, fields.get("retry_count")), (0, 1))
        self.assertEqual(self.sleeps, [au.INSTALL_BACKOFF[0]])
        self.assertIn("--offline", self.launched.call_args.args[0])

    def test_manual_solver_conflict_is_not_a_download_failure(self):
        self.resolver([CONFLICT_ERR])
        rc, fields = self.manual()
        self.assertNotEqual(rc, 0)
        self.assertEqual(fields["reason"], "dependency_conflict")
        self.assertEqual(fields["retry_count"], 0)
        self.assertEqual(int(self.calls.read_text()), 1)
        self.launched.assert_not_called()

    def test_tampered_cache_never_reaches_resolver(self):
        self.resolver([])
        self.files[NEW].write_bytes(b"changed")
        rc, fields = self.manual()
        self.assertNotEqual(rc, 0)
        self.assertEqual((fields["reason"], fields["retry_count"]), (au.ARTIFACT_CHANGED, 0))
        self.assertFalse(self.calls.exists())
        self.launched.assert_not_called()

    def test_short_lease_refuses_resolver_retry_and_preserves_old_install(self):
        self.resolver([NET_ERR])
        rc, fields = self.manual(lease_end=self.now + au.INSTALL_TIMEOUT + au.INSTALL_MIN_ATTEMPT - 1)
        self.assertNotEqual(rc, 0)
        self.assertEqual(fields["retry_count"], 0)
        self.assertEqual(self.sleeps, [])
        self.launched.assert_not_called()

    def test_lease_expires_during_backoff_without_launching_a_second_resolver(self):
        self.resolver([NET_ERR])

        def slow_sleep(delay):
            self.sleeps.append(delay)
            self.now += 180.0

        with patch.object(self, "sleep", slow_sleep):
            rc, fields = self.manual(lease_end=self.now + au.INSTALL_TIMEOUT + 120)
        self.assertNotEqual(rc, 0)
        self.assertEqual(fields["retry_count"], 0)
        self.assertEqual(self.sleeps, [au.INSTALL_BACKOFF[0]])
        self.assertEqual(int(self.calls.read_text()), 1)
        self.launched.assert_not_called()

    def failed_worker(self):
        au.write(self.st, {"attempt": "fixture", "from": OLD, "to": NEW, "install": self.info,
                           "argv": ["agentj"], "old": str(self.files[OLD]), "new": str(self.files[NEW]),
                           "phase": "prepared", "when": "night"}, au.JOB)
        runner = Mock(side_effect=AssertionError("live tool must remain untouched"))
        with patch.object(update, "installer_binary", return_value=str(self.uv)), \
                patch.object(au, "enabled", return_value=True), patch.object(upgrade_diag, "after_failure") as report:
            result = au.run(self.st, runner=runner, restart=lambda _: None, sleep=self.sleep, clock=lambda: self.now)
        self.assertEqual(result["result"], "failed")
        runner.assert_not_called()
        return result, report.call_args.args[1]

    def test_worker_persistent_resolver_dns_only_reports_closed_metadata(self):
        self.resolver([NET_ERR] * (au.INSTALL_RETRIES + 1))
        _, failure = self.failed_worker()
        self.assertEqual(self.sleeps, list(au.INSTALL_BACKOFF))
        metadata = upgrade_diag.metadata_body(failure, "uv")
        self.assertTrue(support.metadata_ok(metadata))
        lines = metadata.splitlines()
        for field in [f"from: {OLD}", f"to: {NEW}", "reason: dependency_download_failed", "step: install",
                      "dependency_phase: resolve", "uv_failure_class: dns", f"retry_count: {au.INSTALL_RETRIES}"]:
            self.assertIn(field, lines)
        self.assertNotIn("mirror.corp-example", metadata)
        self.assertNotIn("custuser", metadata)

    def test_http_403_with_500_in_url_is_still_permanent(self):
        self.resolver(["error: Failed to fetch https://example.invalid/500/wheel.whl: 403 Forbidden"])
        rc, fields = self.manual()
        self.assertNotEqual(rc, 0)
        self.assertEqual((fields["retry_count"], fields.get("uv_failure_class")), (0, "http_status"))
        self.assertEqual(int(self.calls.read_text()), 1)
        self.assertFalse(self.sleeps)
        self.launched.assert_not_called()

    def test_locked_dependency_uses_capped_transport_and_offline_dry_run(self):
        dep = wheel("dep", "1")
        name = "dep-1-py3-none-any.whl"
        url = "https://files.pythonhosted.org/packages/00/00/" + "0" * 60 + "/" + name
        lock = (f'[[package]]\nname="dep"\nwheels=[{{url="{url}", hash="sha256:{hashlib.sha256(dep).hexdigest()}", '
                f'size={len(dep)}}}]').encode()
        for version in (OLD, NEW):
            self.put_wheel(version, wheel("agentj", version, "dep==1", lock))
        self.resolver([])
        with patch.object(upgrade_wheels, "selected_wheels", return_value=[name]), \
                patch.object(update, "open_download", return_value=io.BytesIO(dep)) as transport:
            rc, _ = self.manual(lease_end=self.now + au.INSTALL_TIMEOUT + 70)
        self.assertEqual(rc, 0)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.call_args.args[0].full_url, url)
        self.assertLessEqual(transport.call_args.kwargs["timeout"], 70)
        self.assertIn("--offline", self.launched.call_args.args[0])

    def final_recheck_failure(self, failure):
        self.resolver([NET_ERR])
        offline_command = upgrade_wheels.offline_command
        check_artifact = au.check_artifact
        ready = False
        injected = False

        def staged(*args):
            nonlocal ready
            ready = True
            return offline_command(*args)

        def final_check(pin):
            nonlocal injected
            if ready and not injected and pin.version == NEW:
                injected = True
                if failure == "tamper":
                    pin.path.chmod(0o600)
                    pin.path.write_bytes(b"changed after staging")
                else:
                    self.now += au.INSTALL_BUDGET
            return check_artifact(pin)

        with patch.object(upgrade_wheels, "offline_command", side_effect=staged), \
                patch.object(au, "check_artifact", side_effect=final_check):
            result, _ = self.failed_worker()
        self.assertTrue(injected)
        self.assertEqual(self.sleeps, [au.INSTALL_BACKOFF[0]])
        return result

    def test_worker_final_pin_rejection_never_launches_live_install_or_rollback(self):
        result = self.final_recheck_failure("tamper")
        self.assertEqual(result["reason"], au.ARTIFACT_CHANGED)

    def test_worker_lease_expiry_during_final_recheck_keeps_live_tool(self):
        result = self.final_recheck_failure("expire")
        self.assertEqual(result["reason"], "installer_timeout")
