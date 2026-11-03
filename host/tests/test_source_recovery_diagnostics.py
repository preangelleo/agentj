"""Offline upgrade exception evidence; no installer/service/network is exercised."""
import _hermetic  # noqa: F401
import errno
import json
import subprocess
import unittest
from unittest import mock

from agentj import __version__, auto_update, host_diagnostics, update, upgrade_diag
from test_p277_upgrade_diag import _Env, INFO, NEW, Runner


class UpgradeEvidence(_Env):
    def test_service_oserror_survives_result_receipt_and_host_status(self):
        secret = 'provider-secret-message /private/owner argv'
        runner = Runner()
        def run(argv, **kw):
            if 'service' in argv:
                raise OSError(errno.ENXIO, secret)
            return runner(argv, **kw)
        with mock.patch('agentj.upgrade_diag.time.time', return_value=1780000000):
            result = self.apply(run)
        want = {'exception_type': 'OSError', 'errno': errno.ENXIO,
                'failure_stage': 'service', 'failure_at': 1780000000}
        for evidence in (result, upgrade_diag.read(self.st)):
            self.assertEqual({k: evidence.get(k) for k in want}, want)
            self.assertNotIn(secret, json.dumps(evidence))
        with mock.patch('agentj.names.ctl_call', return_value={'ok': True}):
            res = host_diagnostics.execute(self.st, {'op': 'status'})
        exported = json.loads(res['output'])['last_upgrade']
        self.assertEqual({k: exported[k] for k in want}, want)
        self.assertNotIn(secret, res['output'])

    def test_download_oserror_has_original_stage(self):
        with mock.patch('agentj.upgrade_diag.time.time', return_value=1780000001):
            result = self.apply(Runner(), plan_fn=mock.Mock(side_effect=OSError(errno.EROFS, 'private-provider')))
        self.assertEqual(result.get('errno'), errno.EROFS)
        self.assertEqual(result.get('failure_stage'), 'download')
        self.assertEqual(result.get('failure_at'), 1780000001)

    def test_installer_oserror_keeps_install_stage(self):
        result = self.apply(mock.Mock(side_effect=OSError(errno.ENOENT, 'private-provider')))
        self.assertEqual(result.get('exception_type'), 'FileNotFoundError')
        self.assertEqual(result.get('errno'), errno.ENOENT)
        self.assertEqual(result.get('failure_stage'), 'install')

    def test_legacy_and_malformed_evidence_export_unknown(self):
        legacy = {'step': 'service', 'from': '0.26.5a1', 'to': '0.26.6a1',
                  'result': 'failed', 'reason': 'service_restart_failed'}
        upgrade_diag.remember(self.st, legacy)
        for bad in ({}, {'exception_type': 'secret-provider', 'errno': True,
                         'failure_stage': '/private/owner', 'failure_at': -1}):
            rec = upgrade_diag.read(self.st)
            rec.update(bad)
            self.st.write_private(self.st.root / upgrade_diag.LAST, json.dumps(rec).encode())
            with mock.patch('agentj.names.ctl_call', return_value={'ok': True}):
                res = host_diagnostics.execute(self.st, {'op': 'status'})
            last = json.loads(res['output'])['last_upgrade']
            self.assertEqual({k: last[k] for k in ('exception_type', 'errno', 'failure_stage', 'failure_at')},
                             dict.fromkeys(('exception_type', 'errno', 'failure_stage', 'failure_at'), 'unknown'))
            self.assertNotIn('secret-provider', res['output'])
            self.assertNotIn('/private/owner', res['output'])

    def test_auto_worker_preserves_original_exception_through_rollback(self):
        import hashlib
        old = self.st.root / "old.whl"
        old.write_bytes(b"w")
        old.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"w").hexdigest()}))
        job = {"attempt": "a", "from": __version__, "to": NEW, "install": INFO,
               "argv": ["agentj"], "old": str(old), "phase": "installed"}
        auto_update.write(self.st, job, auto_update.JOB)
        rolled_back = []
        def runner(argv):
            stdout = "agentj " + ((__version__ if rolled_back else NEW) if argv[-1] == "--version" else "")
            if "doctor" in argv:
                stdout = '{"checks": [{"id": "version", "status": "ok"}]}'
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        def restart(version):
            if version == NEW:
                rolled_back.append(True)
                raise OSError(errno.ENXIO, "provider-secret-message")
        with mock.patch.object(upgrade_diag, "after_failure"), \
                mock.patch("agentj.service_recovery.service.status", return_value={}), \
                mock.patch("agentj.phone_update.release"), mock.patch("agentj.service_recovery.cleanup_job"), \
                mock.patch('agentj.upgrade_diag.time.time', return_value=1780000002):
            result = auto_update.run(self.st, runner=runner, restart=restart)
        self.assertEqual(result["result"], "rolled_back")
        want = {'exception_type': 'OSError', 'errno': errno.ENXIO,
                'failure_stage': 'service', 'failure_at': 1780000002}
        for evidence in (result, upgrade_diag.read(self.st)):
            self.assertEqual({k: evidence.get(k) for k in want}, want)
            self.assertNotIn("provider-secret-message", json.dumps(evidence))

    def test_fenced_receipt_filters_metadata_before_host_persistence(self):
        rec = upgrade_diag.entry({'from': '0.26.5a1', 'to': '0.26.6a1', 'step': 'service',
                                  'result': 'failed', 'reason': 'service_restart_failed',
                                  'exception_type': 'OSError', 'errno': errno.ENXIO,
                                  'failure_stage': 'service', 'failure_at': 1780000000})
        rec['message'] = 'provider-secret-message'
        result = update.host_op(self.st, {'op': 'record', 'rec': rec}, {})
        self.assertTrue(result['ok'])
        stored = upgrade_diag.read(self.st)
        self.assertEqual(stored.get('errno'), errno.ENXIO)
        self.assertNotIn('provider-secret-message', json.dumps(stored))
        report = upgrade_diag.body({**rec, 'errno': True})
        self.assertIn('exception_type: OSError', report)
        self.assertNotIn('errno:', report)
        self.assertNotIn('provider-secret-message', report)

    def test_rollback_exception_is_not_misattributed_to_first_failure(self):
        def rollback(st, info, old, version, run):
            try:
                run(['rollback'])
            except OSError:
                return False
        def runner(argv, **kw):
            if argv == ['rollback']:
                raise OSError(errno.EACCES, 'private-rollback')
            return subprocess.CompletedProcess(argv, 1, '', '')
        with mock.patch.object(update, 'roll_back', side_effect=rollback):
            result = self.apply(runner)
        self.assertNotIn('exception_type', result)
        self.assertNotIn('errno', upgrade_diag.read(self.st))

    def test_projection_rejects_unbounded_fields(self):
        for value in (True, -1, 999999999999, '6', None):
            self.assertNotIn('errno', upgrade_diag.exception_fields({'errno': value}))
        for value in (True, -1, 253402300800, '1780000000', None):
            self.assertNotIn('failure_at', upgrade_diag.exception_fields({'failure_at': value}))
        self.assertEqual(upgrade_diag.exception_fields({'exception_type': 'ProviderSecret',
                         'failure_stage': 'private', 'message': 'private', 'argv': ['private']}), {})


if __name__ == '__main__':
    unittest.main()
