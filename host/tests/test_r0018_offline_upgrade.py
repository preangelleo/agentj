"""A cold, indexless uv install needs the entire validated rollback closure before any live change."""
import _hermetic  # noqa: F401
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from agentj import auto_update as au, upgrade_wheels as uw


def wheel(project, version, requirement=None, lock=None):
    buffer = io.BytesIO()
    dist = f'{project}-{version}.dist-info'
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(f'{dist}/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        metadata = f'Metadata-Version: 2.1\nName: {project}\nVersion: {version}\n'
        if requirement:
            metadata += f'Requires-Dist: {requirement}\n'
        archive.writestr(f'{dist}/METADATA', metadata)
        archive.writestr(f'{dist}/RECORD', '')
        archive.writestr(f'{project}/__init__.py', '')
        if lock:
            archive.writestr(uw.LOCK, lock)
            archive.writestr('agentj/repair-sources.json', json.dumps({'host_lock_sha256': hashlib.sha256(lock).hexdigest()}))
    return buffer.getvalue()


@unittest.skipUnless(shutil.which('uv'), 'requires uv')
class OfflineDependencies(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dep = wheel('dep', '1')
        self.name = 'dep-1-py3-none-any.whl'
        url = 'https://files.pythonhosted.org/packages/00/00/' + '0' * 60 + '/' + self.name
        self.lock = ('[[package]]\nname="dep"\nwheels=[{url="' + url + '", hash="sha256:'
                     + hashlib.sha256(self.dep).hexdigest() + '", size=' + str(len(self.dep)) + '}]').encode()
        self.pins = [au.pin_artifact(wheel('agentj', version, 'dep==1', self.lock), version, self.root, signed=True)
                     for version in ('1.0.0', '2.0.0')]
        new = self.pins[1]
        self.cmd = au.Command([shutil.which('uv'), 'tool', 'install', '--force', str(new.path)],
                              {'UV_TOOL_DIR': str(self.root / 'tools')})
        self.source = patch.object(uw, 'selected_wheels', return_value=[self.name])
        self.selector = self.source.start()
        self.addCleanup(self.source.stop)

    def prepare(self):
        with patch.object(au, 'download', return_value=self.dep):
            return uw.prepare_update(self.cmd, self.pins[1], self.pins[0].version)

    def test_cold_stage_reused_for_rollback_without_index_or_default_cache(self):
        with patch.dict(os.environ, {'UV_INDEX':'http://127.0.0.1:9', 'UV_CACHE_DIR':str(self.root/'empty'),
                                     'UV_TOOL_BIN_DIR':str(self.root/'launchers')}):
            cmd = self.prepare()
            self.assertIn('--offline', cmd)
            self.assertNotIn('UV_INDEX', au.run_env(cmd))
            self.assertEqual(au.run_env(cmd)['UV_TOOL_BIN_DIR'], str(self.root/'launchers'))
            with patch.object(au, 'download', side_effect=AssertionError('rollback downloaded')), \
                    patch.object(uw, 'selected_wheels', side_effect=AssertionError('rollback used online resolver')):
                cmd = uw.for_rollback(self.cmd, self.pins[0])
            self.assertIn('--no-cache', cmd)
            self.assertEqual(self.selector.call_count, 1)

    def test_missing_or_tampered_dependency_rejected_before_live_install(self):
        cmd = self.prepare()
        staged = Path(cmd[cmd.index('--find-links') + 1]) / self.name
        staged.unlink()
        with self.assertRaisesRegex(ValueError, 'install_artifact_unavailable'):
            self.prepare()
        staged.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'install_artifact_changed'):
            self.prepare()
        self.assertFalse((self.root/'tools').exists())

    def test_hash_failure_precedes_uv_and_writes_no_completed_dependency_set(self):
        with patch.object(au, 'download', return_value=b'changed'):
            with self.assertRaisesRegex(ValueError, 'install_artifact_changed'):
                uw.prepare_update(self.cmd, self.pins[1], self.pins[0].version)
        self.assertFalse((self.root/'tools').exists())
        self.assertEqual(list((self.pins[0].path.parent/'dependencies').iterdir()), [])
