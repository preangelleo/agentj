"""The same-version repair must keep the running uv tool's Python and installation."""
import _hermetic
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from _testtmp import TEST_TMP
from agentj import auto_update, cli, service, update, upgrade_wheels
from agentj.state import State


class SameVersion(unittest.TestCase):
    @unittest.skipUnless(shutil.which('uv'), 'real uv required')
    def test_reinstall_ignores_unusable_default_and_preserves_original_tool(self):
        version = update.__version__
        uv = shutil.which('uv')
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            home = Path(tmp).resolve()
            state = State(home/'state')
            state.root.mkdir(mode=0o700)
            cache = home/'upgrade-cache'
            cache.mkdir(mode=0o700)
            wheel = cache/auto_update.artifact_name(version)
            dist = f'agentj-{version}.dist-info/'
            lock = b'package = []\n'
            members = {
                'fixture.py': f'def main():\n print("agentj {version}")\n',
                'agentj/upgrade.lock': lock,
                'agentj/repair-sources.json': json.dumps({'host_lock_sha256':hashlib.sha256(lock).hexdigest()}),
                dist+'METADATA': f'Metadata-Version: 2.1\nName: agentj\nVersion: {version}\nRequires-Python: >=3.11\n',
                dist+'WHEEL': 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                dist+'entry_points.txt': '[console_scripts]\nagentj = fixture:main\n',
            }
            with zipfile.ZipFile(wheel,'w') as z:
                for name, data in members.items():
                    z.writestr(name,data)
                z.writestr(dist+'RECORD',''.join(name+',,\n' for name in [*members,dist+'RECORD']))
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            receipt = wheel.with_suffix('.verified.json')
            receipt.write_text(json.dumps({'sha256':digest,'signed':True,'signature':'test'}))
            wheel.chmod(0o600)
            receipt.chmod(0o600)
            tools, bin_dir = home/'original-tools', home/'original-bin'
            env = {**upgrade_wheels.clean_env(), 'HOME':str(home),'UV_TOOL_DIR':str(tools),'UV_TOOL_BIN_DIR':str(bin_dir)}
            base = upgrade_wheels.base_python()
            subprocess.run([uv,'tool','install','--offline','--no-index','--no-config','--no-cache',
                            '--python',base,str(wheel)],env=env,check=True,capture_output=True)
            info = {'kind':'uv','where':str(tools/'agentj')}
            installed = []
            real_run = subprocess.run
            def run(cmd, **kw):
                if 'tool' in cmd:
                    installed.append((list(cmd),kw.get('env') or {}))
                return real_run(cmd, **kw)
            # Different service defaults cannot select either the live tool location or its interpreter.
            with patch.dict(os.environ,{**env,'UV_TOOL_DIR':str(home/'wrong-tools'), 'UV_TOOL_BIN_DIR':str(home/'wrong-bin'),
                                        'UV_PYTHON':str(home/'unusable-python'),'UV_PYTHON_DOWNLOADS':'never',
                                        'AGENTJ_STATE_DIR':str(state.root),'XDG_CONFIG_HOME':str(home/'config')},clear=True), \
                 patch.object(update,'installer_binary',return_value=uv), patch.object(update,'verified_wheel',return_value=wheel), \
                 patch.object(auto_update.minisign,'verify',return_value=f'agentj-host/v1 version={version} sha256={digest}'), \
                 patch.object(update,'new_argv',return_value=[str(bin_dir/'agentj')]), patch.object(update,'install_kind',return_value=info), \
                 patch.object(service,'status',return_value={}), patch.object(service,'legacy_status',return_value={}), \
                 patch.object(subprocess,'run',side_effect=run), redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as done:
                cli.main(['update','apply','--reinstall'])
            self.assertEqual(done.exception.code,0,output.getvalue())
            cmd, command_env = installed[0]
            self.assertEqual(cmd[cmd.index('--python')+1],base)
            self.assertIn('--offline',cmd)
            self.assertIn('--find-links',cmd)
            self.assertNotIn('UV_PYTHON',command_env)
            self.assertEqual(command_env['UV_TOOL_DIR'],str(tools))
            self.assertEqual(command_env['UV_TOOL_BIN_DIR'],str(bin_dir))
            self.assertFalse((home/'wrong-tools').exists())
