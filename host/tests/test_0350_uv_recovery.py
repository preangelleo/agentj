"""Old uv recovery runs before replacement and shares the signed bootstrap runtime."""
import errno
import hashlib
from pathlib import Path
import socket
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from agentj import auto_update, update, upgrade_diag, upgrade_uv, upgrade_wheels


class UvRecovery(unittest.TestCase):
    def test_pins_match_every_public_installer_archive(self):
        repo = Path(__file__).resolve().parents[3]
        bootstrap = (repo/'agentjarvis/support/installation/bootstrap.sh.in').read_text()
        for target, (archive_hash, binary_hash) in upgrade_uv.PINS.items():
            self.assertIn(f'target={target}; expected={archive_hash}', bootstrap)
            archive = repo/f'agentjarvis/site/public/dl/uv-{target}.tar.gz'
            with archive.open('rb') as stream:
                self.assertEqual(hashlib.file_digest(stream, 'sha256').hexdigest(), archive_hash)
            with tarfile.open(archive) as bundle, bundle.extractfile(f'uv-{target}/uv') as stream:
                self.assertEqual(hashlib.file_digest(stream, 'sha256').hexdigest(), binary_hash)

    def test_confirmed_incompatibility_attempts_one_repair_then_reports_doctor_and_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin = auto_update.pin_artifact(b'wheel', '1.2.3', root)
            def stage(cmd, *args, **kw):
                raise upgrade_wheels.resolver_failure('unexpected argument --format')
            for error, reason in [(PermissionError(errno.EACCES, 'private path'), 'permission_denied'),
                                  (upgrade_wheels.download_failure(socket.timeout()), 'dependency_download_failed')]:
                with patch.object(upgrade_wheels, '_prepare_update', side_effect=stage), \
                     patch.object(upgrade_uv, 'runtime', side_effect=error) as repair, \
                     patch('agentj.doctor.run_upgrade', return_value=[{'status':'ok'}, {'status':'warn'}]):
                    with self.assertRaises(upgrade_wheels.PreparationFailure) as got:
                        upgrade_wheels.prepare_update(auto_update.Command(['old-uv','tool','install'], {}), pin, '1.2.2')
                    repair.assert_called_once()
                    self.assertEqual(str(got.exception), reason)
                    fields = got.exception.fields
                    self.assertEqual(fields['uv_repair_doctor'], [1,1,0])
                    res = {**fields, 'from':'1.2.2', 'to':'1.2.3', 'reason':reason, 'phase':'prepared'}
                    for lang in ('en', 'zh'):
                        msg = upgrade_diag.owner_sentence(res, {'status':'off'}, lang)
                        self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, msg)
                        self.assertIn('doctor' if lang=='en' else '自检', msg)
                    self.assertNotIn('private path', repr(fields))

    def test_completion_shown_only_after_success(self):
        ok = {'from':'1.2.2','to':'1.2.3','uv_repaired':True, 'result':'ok'}
        for lang in ('zh','en'):
            self.assertIn('uv', auto_update.notice(ok, lang))
            self.assertIn('uv', update.completion_text(ok, (1,0,0), lang))
            self.assertNotIn('along with uv', update.completion_text({**ok,'result':'failed'}, (1,0,1), lang))
