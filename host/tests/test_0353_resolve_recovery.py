"""Metadata-only resolver diagnostics and bounded recovery before replacing an installation."""
import ast
import io
import os
import zipfile
import shutil
import tarfile
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentj import auto_update, support, upgrade_diag, upgrade_uv, upgrade_wheels as wheels
from test_r0018_offline_upgrade import wheel


def cli_wheel(version, lock):
    buffer = io.BytesIO(wheel('agentj', version, lock=lock))
    with zipfile.ZipFile(buffer, 'a') as whl:
        whl.writestr(f'agentj-{version}.dist-info/entry_points.txt', '[console_scripts]\nagentj=agentj.cli:main\n')
        whl.writestr('agentj/cli.py', 'def main(): return 0\n')
    return buffer.getvalue()


class ResolveRecovery(unittest.TestCase):
    def test_standalone_website_and_packaged_selector_are_identical(self):
        from agentj import upgrade_select
        here = Path(__file__).resolve()
        source = here.parents[2] / 'support/installation/installer.py'
        if not source.is_file():
            source = here.parent / 'fixtures/p385/installer.py'
        def selector(path):
            tree = ast.parse(path.read_text())
            return ast.dump(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'repair_artifacts'))
        self.assertEqual(selector(source), selector(Path(upgrade_select.__file__)))

    def test_support_only_accepts_closed_constraint_categories(self):
        for message, category in (
            ('the current Python version (3.10) does not satisfy Python>=3.11', 'requires_python'),
            ('has no wheels with a matching platform tag', 'wheel_tags'),
            ('all available releases are yanked', 'yanked'),
            ('mise uv is not a valid shim', 'installer_shim'),
            ('error: invalid value for --format', 'uv_option'),
            ('Failed to read cache entry /Users/fixture/sensitive', 'cache'),
            ('requirements are unsatisfiable', 'version_conflict'),
            ('some future diagnostic', 'unrecognized'),
        ):
            with self.subTest(category=category):
                failure = wheels.resolver_failure(message + ' https://private.invalid/secret')
                report = upgrade_diag.metadata_body(dict(failure.fields, reason=str(failure), result='failed',
                                                         step='install', **{'from':'0.30.50', 'to':'0.30.53'}))
                self.assertIn('uv_resolve_detail: ' + category, report.splitlines())
                self.assertTrue(support.metadata_ok(report))
                self.assertNotIn('secret', report)
        self.assertFalse(upgrade_diag.meta_line_ok('uv_resolve_detail: a-private-name'))

    def attempt(self, message, second_error=None, *, remaining=399):
        with tempfile.TemporaryDirectory() as temp:
            pin = auto_update.pin_artifact(b'fixture', '2.0.0', Path(temp))
            retry = wheels.PreparationRetry(clock=lambda:0)
            retry.deadline = remaining
            cmd = auto_update.Command(['old', 'tool', 'install'], {})
            replacement = auto_update.Command(['pinned', 'tool', 'install'], {})
            outcome = second_error or replacement
            with patch.object(wheels, '_prepare_update', side_effect=[wheels.resolver_failure(message), outcome]) as stage, \
                 patch.object(upgrade_uv, 'runtime', return_value='pinned') as repair, \
                 patch.object(upgrade_uv, 'repair_python', return_value='/private/test/python') as python:
                try:
                    result = wheels.prepare_update(cmd, pin, '1.0.0', retry=retry)
                except wheels.PreparationFailure as error:
                    result = error
                return result, retry.count, stage, repair, python

    def test_unknown_exit_uses_verified_uv_once(self):
        result, count, stage, repair, python = self.attempt('new uv error')
        self.assertFalse(getattr(result, 'uv_repaired', False))
        self.assertEqual((count, stage.call_count, repair.call_count, python.call_count), (1, 2, 1, 0))
        self.assertEqual(stage.call_args.args[0][0], 'pinned')

    def test_platform_constraint_gets_supported_interpreter(self):
        result, count, stage, repair, python = self.attempt('package has no wheels with a matching platform tag')
        self.assertFalse(getattr(result, 'uv_repaired', False))
        self.assertEqual((count, repair.call_count, python.call_count), (1, 1, 1))
        self.assertEqual(stage.call_args.kwargs['python'], '/private/test/python')

    def test_second_error_and_short_lease_stop_before_install(self):
        last = wheels.resolver_failure('failed to read cache')
        result, count, stage, _, _ = self.attempt('first unknown problem', last)
        self.assertIs(result, last)
        self.assertEqual((count, stage.call_count), (1, 2))
        result, count, stage, repair, _ = self.attempt('first unknown problem', remaining=1)
        self.assertEqual((str(result), count, stage.call_count, repair.call_count), ('uv_failed', 0, 1, 0))

    @unittest.skipUnless(shutil.which('uv'), 'requires uv')
    def test_invalid_resolver_output_uses_verified_lock_but_wrong_digest_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            url = 'https://files.pythonhosted.org/packages/00/00/' + '0'*60 + '/dep-1-py3-none-any.whl'
            lock = (f'[[package]]\nname="dep"\nwheels=[{{url="{url}", hash="sha256:{"0"*64}", size=1}}]\n').encode()
            pins = [auto_update.pin_artifact(cli_wheel(v, lock), v, root, signed=True)
                    for v in ('1.0.0', '2.0.0')]
            uv = shutil.which('uv')
            cmd = auto_update.Command([uv, 'tool', 'install', '--force', str(pins[1].path)],
                                      {'UV_TOOL_DIR': str(root/'tools')})
            original = wheels.run_resolver
            invalid = 'private/path = ['
            mismatch = f'[[packages]]\nname="dep"\n[[packages.wheels]]\nurl="{url}"\nhashes={{sha256="{"f"*64}"}}\n'

            def resolver(args, scratch, timeout, **kwargs):
                if args[2] == 'compile':
                    (Path(scratch)/'pylock.toml').write_text(invalid)
                else:
                    original(args, scratch, timeout, **kwargs)

            with patch.object(wheels, 'run_resolver', side_effect=resolver), \
                 patch.object(upgrade_uv, 'runtime', return_value=uv) as repair:
                retry = wheels.PreparationRetry()
                prepared = wheels.prepare_update(cmd, pins[1], pins[0].version, retry=retry)
                self.assertIn('--offline', prepared)
                self.assertEqual((retry.count, repair.call_count), (1, 1))
                invalid = mismatch
                with self.assertRaisesRegex(wheels.PreparationFailure, 'install_artifact_changed'):
                    wheels.prepare_update(cmd, pins[1], pins[0].version)
                self.assertEqual(repair.call_count, 1)

    @unittest.skipUnless(shutil.which('uv'), 'requires uv')
    def test_malformed_receipt_reuses_verified_custom_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pins = [auto_update.pin_artifact(cli_wheel(v, b'package=[]\n'), v, root, signed=True)
                    for v in ('1.0.0', '2.0.0')]
            uv = shutil.which('uv')
            env = {'HOME': str(root), 'UV_TOOL_DIR': str(root/'tools'), 'UV_TOOL_BIN_DIR': str(root/'custom-bin')}
            prefix = root/'tools/agentj'
            entry = root/'custom-bin/agentj'
            subprocess.run([uv, 'tool', 'install', '--no-cache', '--no-index', '--no-config',
                            '--python', wheels.base_python(), str(pins[0].path)],
                           env={**wheels.clean_env(), **env}, check=True, capture_output=True)
            receipt = prefix/'uv-receipt.toml'
            receipt.write_text('[tool\n')
            alias = root/'tools-alias'
            alias.symlink_to(prefix.parent, target_is_directory=True)
            cmd = auto_update.Command([uv, 'tool', 'install', '--force', str(pins[1].path)],
                                      {'UV_TOOL_DIR': str(alias)})
            other = root/'other-bin'
            other.mkdir()
            (other/'agentj').symlink_to(prefix/'bin/agentj')
            with patch.dict(os.environ, {'HOME': str(root), 'PATH': os.pathsep.join((str(other), str(entry.parent)))}):
                os.environ.pop('UV_TOOL_BIN_DIR', None)
                with self.assertRaises(wheels.PreparationFailure) as caught:
                    wheels.prepare_update(cmd, pins[1], pins[0].version)
                failure = caught.exception
                report = upgrade_diag.metadata_body(dict(failure.fields, reason=str(failure), result='failed',
                                                         step='install', **{'from':'1.0.0', 'to':'2.0.0'}))
                self.assertIn('uv_resolve_detail: tool_receipt_invalid', report)
                self.assertIn('reason: dependency_preparation_failed', report)
                self.assertTrue(support.metadata_ok(report))
                self.assertNotIn(str(root), report)
                (other/'agentj').unlink()
                entry.parent.chmod(0o777)
                with self.assertRaisesRegex(wheels.PreparationFailure, 'install_artifact_changed') as caught:
                    wheels.prepare_update(cmd, pins[1], pins[0].version)
                self.assertEqual(caught.exception.fields['permission_paths'], [str(entry.parent)])
                self.assertIn('chmod go-w --', caught.exception.fields['fix'])
                entry.parent.chmod(0o700)
                fixed = wheels.prepare_update(cmd, pins[1], pins[0].version)
                self.assertEqual(fixed.env['UV_TOOL_DIR'], str(prefix.parent.resolve()))
                self.assertEqual(fixed.env['UV_TOOL_BIN_DIR'], str(entry.parent))
                alias.unlink()
                alias.symlink_to(root/'elsewhere', target_is_directory=True)
                subprocess.run(fixed, env=auto_update.run_env(fixed), check=True, capture_output=True)
            self.assertEqual(entry.resolve(), prefix/'bin/agentj')
            actual = subprocess.check_output([str(prefix/'bin/python'), '-I', '-c',
                                              'import importlib.metadata as m; print(m.version("agentj"))'])
            self.assertEqual(actual.strip(), b'2.0.0')

    @unittest.skipUnless(shutil.which('uv'), 'requires uv')
    def test_repaired_python_is_used_for_offline_install_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = b'package=[]\n'
            pins = [auto_update.pin_artifact(cli_wheel(v, lock), v, root, signed=True)
                    for v in ('1.0.0', '2.0.0')]
            (pins[0].path.parent/'upgrade-runtime'/'python').mkdir(parents=True, mode=0o700)
            uv = shutil.which('uv')
            py = wheels.base_python()
            cmd = auto_update.Command([uv, 'tool', 'install', str(pins[1].path)], {'UV_TOOL_DIR': str(root/'tools')})
            with patch.object(upgrade_uv, 'repair_python', return_value=py), \
                 patch.object(upgrade_uv, 'runtime', return_value=uv):
                fixed = wheels._prepare_update(cmd, pins[1], pins[0].version, python=py)
                self.assertEqual(fixed[fixed.index('--python') + 1], py)
                old = auto_update.Command([uv, 'tool', 'install', str(pins[0].path), '--python', '/unavailable'], cmd.env)
                rollback = wheels.for_rollback(old, pins[0])
                self.assertEqual(rollback[rollback.index('--python') + 1], py)
                self.assertEqual(rollback[rollback.index('--find-links')+1], fixed[fixed.index('--find-links')+1])


    @unittest.skipUnless(upgrade_uv.target_platform() in upgrade_uv.PINS, 'no pinned uv for this platform')
    def test_bad_launchd_tmpdir_real_resolver_install_and_offline_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / 'home/.local/share/agentj/upgrade-bin/uv'
            seed.parent.mkdir(parents=True)
            target = upgrade_uv.target_platform()
            archive = Path(__file__).resolve().parents[3] / f'agentjarvis/site/public/dl/uv-{target}.tar.gz'
            with tarfile.open(archive) as bundle, bundle.extractfile(f'uv-{target}/uv') as src:
                seed.write_bytes(src.read())
            seed.chmod(0o700)
            poisoned = root / 'unusable-temp'
            poisoned.touch()
            lock = b'package=[]\n'
            pins = [auto_update.pin_artifact(cli_wheel(v, lock), v, root, signed=True)
                    for v in ('1.0.0', '2.0.0')]
            info = {'UV_TOOL_DIR': str(root/'tools'), 'UV_TOOL_BIN_DIR': str(root/'bin')}
            env = {'HOME':str(root/'home'), 'TMPDIR':str(poisoned)}
            attempted = []
            original = wheels.resolver_failure
            def capture(raw):
                error = original(raw)
                attempted.append((str(error), dict(error.fields)))
                return error
            cmd = auto_update.Command([str(seed), 'tool', 'install', '--force', str(pins[1].path)], info)
            with patch.dict(os.environ, env), patch.object(wheels, 'resolver_failure', side_effect=capture), \
                 patch.object(upgrade_uv, 'fetch_archive', side_effect=AssertionError('bootstrap uv already on disk')):
                retry = wheels.PreparationRetry()
                install = wheels.prepare_update(cmd, pins[1], '1.0.0', retry=retry)
                self.assertEqual((retry.count, attempted[0][0]), (1, 'uv_failed'))
                self.assertEqual(attempted[0][1]['uv_resolve_detail'], 'temp_directory')
                self.assertEqual(attempted[0][1]['uv_failure_class'], 'other')
                self.assertNotEqual(install[0], str(seed))
                actual = subprocess.run(install, env=auto_update.run_env(install), capture_output=True)
                self.assertEqual(actual.returncode, 0, actual.stderr)
                old = auto_update.Command([str(seed), 'tool', 'install', '--force', str(pins[0].path)], info)
                undo = wheels.for_rollback(old, pins[0])
                actual = subprocess.run(undo, env=auto_update.run_env(undo), capture_output=True)
                self.assertEqual(actual.returncode, 0, actual.stderr)
                self.assertEqual(os.environ['TMPDIR'], str(poisoned))
            self.assertEqual(poisoned.read_bytes(), b'')
