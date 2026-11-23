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

    def test_dense_path_covers_lock_and_top_level_dist_info_members_only(self):
        # Selection scope of the reviewed hook (0.30.71): text sources, *.lock and everything under the wheel's
        # top-level *.dist-info/ take the dense encoder; binaries and a nested look-alike directory do not.
        p = self.root / 'scope.whl'
        members = ['agentj/cli.py', 'agentj/upgrade.lock', 'agentj-1.dist-info/METADATA', 'agentj-1.dist-info/RECORD',
                   'agentj-1.dist-info/licenses/LICENSE', 'agentj-1.dist-info/WHEEL', 'agentj/admin/font.woff2',
                   'agentj/config/en.phone.xz', 'agentj/data.dist-info/blob.bin', 'agentj/lockfile.bin']
        with zipfile.ZipFile(p, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for name in members:
                z.writestr(zipfile.ZipInfo(name, (2020, 2, 3, 4, 5, 6)), b'fixture line\n' * 300,
                           compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        original = self.root / 'scope-original.whl'
        original.write_bytes(p.read_bytes())
        dense = []
        opened = self.hook._DenseZipFile._open_to_write
        def spy(archive, zinfo, force_zip64=False):
            writer = opened(archive, zinfo, force_zip64=force_zip64)
            if isinstance(writer._compressor, self.hook._DenseDeflater):
                dense.append(zinfo.filename)
            return writer
        with mock.patch.object(self.hook._DenseZipFile, '_open_to_write', spy):
            self.hook.CustomBuildHook().finalize('standard', {}, str(p))
        self.assertEqual(dense, ['agentj/cli.py', 'agentj/upgrade.lock', 'agentj-1.dist-info/METADATA',
                                 'agentj-1.dist-info/RECORD', 'agentj-1.dist-info/licenses/LICENSE',
                                 'agentj-1.dist-info/WHEEL'])
        self.hook._verify_equivalent(original, p)

    def test_official_recipe_uses_wheel_hook_and_ships_it_in_sdist(self):
        data = tomllib.loads((HOST / 'pyproject.toml').read_text())
        targets = data['tool']['hatch']['build']['targets']
        self.assertEqual(targets['wheel']['hooks']['custom'], {'path': 'hatch_build.py'})
        self.assertIn('hatch_build.py', targets['sdist']['include'])
        self.assertIn('zopfli==0.4.3', data['build-system']['requires'])


if __name__ == '__main__':
    unittest.main()

class CutoverWheelPayload(unittest.TestCase):
    """Actual built wheel; opt-in artifact inputs avoid rebuilding in source-unit runs."""
    @classmethod
    def setUpClass(cls):
        import os
        wheel=os.environ.get('AJ_CUTOVER_WHEEL')
        if not wheel:
            raise unittest.SkipTest('actual built wheel not supplied')
        cls.wheel=Path(wheel)

    def test_actual_cutover_members_fit_and_preserve_exact_source_and_record(self):
        import base64,csv,hashlib,io
        with zipfile.ZipFile(self.wheel) as archive:
            infos=[i for i in archive.infolist() if i.filename in ('agentj/provenance.py','agentj/tg_cursor.py')]
            self.assertEqual(len(infos),2)
            total=sum(i.compress_size+76+2*len(i.filename.encode())+2*len(i.extra)+len(i.comment) for i in infos)
            print('P117 actual wheel members:',[(i.filename,i.file_size,i.compress_size) for i in infos], 'allocation=',total)
            self.assertIsNone(archive.testzip())
            record=next(n for n in archive.namelist() if n.endswith('.dist-info/RECORD'))
            hashes={r[0]:r[1:] for r in csv.reader(io.StringIO(archive.read(record).decode()))}
            for member in infos:
                data=archive.read(member)
                self.assertEqual(data,(HOST/member.filename).read_bytes())
                self.assertEqual(hashes[member.filename],["sha256="+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(),str(len(data))])
            self.assertLessEqual(total,12288,'P117 cut-over allocation is bounded')

    def test_only_comment_tokens_changed_including_all_ast_locations(self):
        import ast,io,os,subprocess,tokenize
        baseline=os.environ.get('AJ_CUTOVER_SOURCE_BASE')
        if not baseline:
            self.skipTest('source baseline not supplied')
        for member in ('agentj/provenance.py','agentj/tg_cursor.py'):
            prefix=subprocess.run(['git','rev-parse','--show-prefix'],cwd=HOST,capture_output=True,check=True,text=True).stdout.strip()
            before=subprocess.run(['git','show',baseline+':'+prefix+member],cwd=HOST,capture_output=True,check=True).stdout.decode()
            after=(HOST/member).read_text()
            self.assertEqual(ast.dump(ast.parse(before),include_attributes=True),ast.dump(ast.parse(after),include_attributes=True))
            def tokens(source):
                return [(t.type,t.string,t.start,t.end) for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type!=tokenize.COMMENT]
            self.assertEqual(tokens(before),tokens(after))

class CutoverCommentUpperBound(unittest.TestCase):
    def test_nonexecuting_comment_only_compression_upper_bound(self):
        import ast,io,tokenize
        try:
            from zopfli.zlib import compress
        except ImportError:
            self.skipTest('build-only zopfli is not installed in the host runtime')
        original_total=0; stripped_total=0
        for member in ('agentj/provenance.py','agentj/tg_cursor.py'):
            source=(HOST/member).read_text()
            lines=source.splitlines(keepends=True)
            comments=[t for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type==tokenize.COMMENT]
            for token in reversed(comments):
                row=token.start[0]-1
                lines[row]=lines[row][:token.start[1]]+lines[row][token.end[1]:]
            stripped=''.join(lines)
            self.assertEqual(ast.dump(ast.parse(source),include_attributes=True),ast.dump(ast.parse(stripped),include_attributes=True))
            def packed(text):
                data=text.encode()
                return min(len(zlib.compress(data,9)[2:-4]),len(compress(data,numiterations=5)[2:-4]))
            before,after=packed(source),packed(stripped)
            overhead=76+2*len(member.encode())
            original_total+=before+overhead;stripped_total+=after+overhead
            print('P117 comment upper bound:',member,'source=',len(source.encode()),'comment_tokens=',len(comments),'packed_before=',before,'packed_after=',after)
        print('P117 all-comments-removed allocation=',stripped_total,'original=',original_total,'cap=',12288,'remaining_over=',stripped_total-12288)
