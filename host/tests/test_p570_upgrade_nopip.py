"""A plain uv-created venv has no pip: verified installs and rollback still work."""
import _hermetic
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

from agentj import auto_update, update


class PlainVenvInstaller(unittest.TestCase):
    def test_uv_targets_the_existing_venv_without_dependencies(self):
        info = {'kind': 'pip', 'where': '/tmp/target venv', 'legacy': False}
        with patch('agentj.auto_update.shutil.which', return_value='/tools/uv'):
            self.assertEqual(auto_update.install_commands(info, '/cache/new.whl'), [[
                '/tools/uv', 'pip', 'install', '--python', '/tmp/target venv/bin/python',
                '--no-deps', '--reinstall', '/cache/new.whl']])

    def test_without_uv_retains_pip_venv_support(self):
        with patch('agentj.auto_update.shutil.which', return_value=None):
            self.assertEqual(auto_update.install_commands({'kind': 'pip', 'where': '/venv'}, '/cache/new.whl'), [[
                '/venv/bin/python', '-m', 'pip', 'install', '--no-deps', '--force-reinstall', '/cache/new.whl']])

    def test_tool_managers_keep_their_own_environment(self):
        for kind in ('uv', 'pipx'):
            with self.subTest(kind=kind), patch('agentj.auto_update.shutil.which', return_value='/tools/'+kind):
                cmd = auto_update.install_commands({'kind': kind, 'where': '/venv'}, '/cache/new.whl')[0]
                self.assertEqual(cmd, ['/tools/uv', 'tool', 'install', '--force', '/cache/new.whl'] if kind == 'uv' else
                                 ['/tools/pipx', 'install', '--force', '/cache/new.whl'])

    @unittest.skipUnless(shutil.which('uv'), 'requires the installer used by local-install')
    def test_real_pipless_venv_installs_and_rolls_back_offline(self):
        with tempfile.TemporaryDirectory(prefix='p570-') as tmp:
            root = Path(tmp)
            py = root/'venv/bin/python'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(py.parents[1])], check=True)
            self.assertNotEqual(subprocess.run([str(py), '-m', 'pip', '--version'], capture_output=True).returncode, 0)
            info = {'kind': 'pip', 'where': str(py.parents[1]), 'legacy': False}
            def wheel(version):
                path = root/f'agentj-{version}-py3-none-any.whl'
                meta = f'agentj-{version}.dist-info'
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr('agentj/__init__.py', '')
                    z.writestr('agentj/__main__.py', f'print("agentj {version}")\n')
                    z.writestr(meta+'/METADATA', f'Metadata-Version: 2.1\nName: agentj\nVersion: {version}\n')
                    z.writestr(meta+'/WHEEL', 'Wheel-Version: 1.0\nGenerator: p570-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                    z.writestr(meta+'/RECORD', '')
                path.with_suffix('.verified.json').write_text(json.dumps({'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
                return path
            old, new = wheel('1.0'), wheel('1.1')
            def run(argv, **kw):
                env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
                return subprocess.run(argv, cwd=root, env={**env, 'UV_OFFLINE': '1', 'UV_CACHE_DIR': str(root/'cache')}, **kw)
            for path in (old, new):
                for argv in auto_update.install_commands(info, path):
                    result = run(argv, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('1.1', run([str(py), '-m', 'agentj'], capture_output=True, text=True).stdout)
            with patch('agentj.update.new_argv', return_value=[str(py), '-m', 'agentj']):
                self.assertTrue(update.roll_back(SimpleNamespace(root=root), info, old, '1.0', run))
            self.assertIn('1.0', run([str(py), '-m', 'agentj'], capture_output=True, text=True).stdout)
            self.assertNotEqual(run([str(py), '-m', 'pip', '--version'], capture_output=True).returncode, 0)
