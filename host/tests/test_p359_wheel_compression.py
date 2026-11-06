"""Build-only wheel ZIP fidelity controls; Hatch integration is verified by uv build."""
import copy
import importlib.util
import sys
import tempfile
import tomllib
import types
import unittest
import warnings
import zipfile
import zlib
from pathlib import Path
from unittest import mock

HOST = Path(__file__).resolve().parents[1]


class WheelCompression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Build-only interfaces/encoder are substituted here; ZIP reads/writes are real.
        # The actual declared zopfli algorithm is exercised by the uv-build fidelity integration.
        interface = types.ModuleType('hatchling.builders.hooks.plugin.interface')
        interface.BuildHookInterface = object
        spec = importlib.util.spec_from_file_location('p359_wheel_hook', HOST / 'hatch_build.py')
        self.hook = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {interface.__name__: interface}):
            spec.loader.exec_module(self.hook)
        encoder = mock.patch.object(self.hook, "_dense_compress", lambda data: zlib.compress(data, 9)[2:-4])
        encoder.start()
        self.addCleanup(encoder.stop)

    def wheel(self, name='candidate.whl', method=zipfile.ZIP_DEFLATED):
        p = self.root / name
        with zipfile.ZipFile(p, 'w', compression=method, compresslevel=6) as z:
            z.comment = b'archive comment'
            for filename, data in [('agentj/中文.py', b'print("fixture")\n' * 700),
                                   ('agentj-1.dist-info/RECORD', b'original RECORD bytes\n')]:
                i = zipfile.ZipInfo(filename, (2020, 2, 3, 4, 5, 6))
                i.compress_type = method
                i.create_system = 3
                i.external_attr = 0o100644 << 16
                i.comment = b'entry comment'
                i.extra = b'\xfe\xca\x02\x00ok'
                z.writestr(i, data, compresslevel=6)
        p.chmod(0o640)
        return p

    def test_finalize_preserves_all_zip_semantics_and_record(self):
        p = self.wheel()
        original = self.root / 'original.whl'
        original.write_bytes(p.read_bytes())
        write = zipfile.ZipFile.writestr
        levels = []
        def record(z, info, data, *args, **kwargs):
            levels.append(kwargs.get('compresslevel'))
            return write(z, info, data, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, 'writestr', record):
            self.hook.CustomBuildHook().finalize('standard', {}, str(p))
        self.assertEqual(levels, [9, 9])
        self.hook._verify_equivalent(original, p)
        self.assertEqual(p.stat().st_mode & 0o777, 0o640)
        with zipfile.ZipFile(p) as z:
            self.assertEqual(z.read('agentj-1.dist-info/RECORD'), b'original RECORD bytes\n')
            self.assertEqual(z.testzip(), None)
        self.assertFalse(list(self.root.glob('*.compression-*')))

    def test_repeat_is_deterministic_and_stored_entries_stay_stored(self):
        p = self.wheel('one.whl', zipfile.ZIP_STORED)
        q = self.root / 'two.whl'
        q.write_bytes(p.read_bytes())
        for f in (p, q):
            self.hook.CustomBuildHook().finalize('standard', {}, str(f))
        self.assertEqual(p.read_bytes(), q.read_bytes())
        with zipfile.ZipFile(p) as z:
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist()))

    def test_corrupted_rewrite_fails_without_replacing_original_or_leaking_temp(self):
        p = self.wheel()
        original = p.read_bytes()
        write = zipfile.ZipFile.writestr
        def corrupt_content(z, info, data, *args, **kwargs):
            return write(z, info, data + b'corruption', *args, **kwargs)
        def corrupt_metadata(z, info, data, *args, **kwargs):
            changed = copy.copy(info)
            changed.external_attr ^= 0o100 << 16
            return write(z, changed, data, *args, **kwargs)
        for corrupt, reason in ((corrupt_content, 'content'), (corrupt_metadata, 'metadata')):
            with mock.patch.object(zipfile.ZipFile, 'writestr', corrupt):
                with self.assertRaisesRegex(ValueError, 'wheel member ' + reason):
                    self.hook.CustomBuildHook().finalize('standard', {}, str(p))
            self.assertEqual(p.read_bytes(), original)
            self.assertEqual(list(self.root.iterdir()), [p])

    def test_duplicate_or_unsupported_method_is_rejected_without_replacement(self):
        for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2):
            p = self.wheel('reject.whl', method)
            if method == zipfile.ZIP_DEFLATED:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(p, 'a') as z:
                        z.writestr('agentj-1.dist-info/RECORD', b'duplicate')
            original = p.read_bytes()
            with self.assertRaisesRegex(ValueError, 'duplicate|compression method'):
                self.hook.CustomBuildHook().finalize('standard', {}, str(p))
            self.assertEqual(p.read_bytes(), original)
            p.unlink()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_dense_encoder_fails_closed_without_global_zip_changes(self):
        p = self.wheel()
        original = p.read_bytes()
        compressor = zipfile._get_compressor
        with mock.patch.object(self.hook, '_dense_compress', lambda data: zlib.compress(data + b'wrong', 9)[2:-4]):
            with self.assertRaisesRegex(ValueError, 'DEFLATE content changed'):
                self.hook.CustomBuildHook().finalize('standard', {}, str(p))
        self.assertIs(zipfile._get_compressor, compressor)
        self.assertEqual(p.read_bytes(), original)
        self.assertFalse(list(self.root.glob('*.compression-*')))

    def test_official_recipe_uses_wheel_hook_and_ships_it_in_sdist(self):
        data = tomllib.loads((HOST / 'pyproject.toml').read_text())
        targets = data['tool']['hatch']['build']['targets']
        self.assertEqual(targets['wheel']['hooks']['custom'], {'path': 'hatch_build.py'})
        self.assertIn('hatch_build.py', targets['sdist']['include'])
        self.assertIn('zopfli==0.4.3', data['build-system']['requires'])


if __name__ == '__main__':
    unittest.main()
