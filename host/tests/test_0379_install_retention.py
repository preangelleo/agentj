"""Five public `update apply` calls using real uv and locally signed wheels."""
import _hermetic  # noqa: F401
import base64
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import auto_update, minisign


DRIVER = '''
import json, os, sys
sys.path.append(os.environ['QA_TEST_SITE_PACKAGES'])
import agentj
agentj.__version__ = os.environ['QA_CURRENT_VERSION']
from agentj import auto_update, cli, minisign, service, update
from agentj.state import State
auto_update.ORIGIN = os.environ['QA_DOWNLOAD_ORIGIN']
minisign.SIGNERS.update(json.loads(os.environ['QA_KEYRING']))
minisign.ROLES['release'] = tuple(json.loads(os.environ['QA_KEYRING']))
service.status = service.legacy_status = lambda: {'installed': False}
st = State()
if not st.exists():
    st.init(relay='ws://127.0.0.1:1', web='http://127.0.0.1:1')
    auto_update.cached_wheel(st, update.__version__)
cli.main(sys.argv[1:])
'''


def encode(data):
    return base64.b64encode(data).decode()


def build_wheel(folder, version, key, kid):
    path = folder / f'agentj-{version}-py3-none-any.whl'
    meta = f'agentj-{version}.dist-info'
    doctor = json.dumps({'checks': [{'id': 'version', 'status': 'ok'}]})
    files = {
        'agentj/__init__.py': f'__version__ = {version!r}\n',
        'fixture_agentj.py': ('import sys\ndef main():\n'
                              f'    print({("agentj " + version)!r} if sys.argv[1] == "--version" else {doctor!r})\n'),
        meta + '/METADATA': f'Metadata-Version: 2.1\nName: agentj\nVersion: {version}\n',
        meta + '/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        meta + '/entry_points.txt': '[console_scripts]\nagentj = fixture_agentj:main\n',
    }
    records = []
    with zipfile.ZipFile(path, 'w') as wheel:
        for name, content in files.items():
            data = content.encode()
            wheel.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            records.append(f'{name},sha256={digest},{len(data)}')
        wheel.writestr(meta + '/RECORD', '\n'.join([*records, meta + '/RECORD,,']) + '\n')
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    signature = key.sign(hashlib.blake2b(data).digest())
    comment = f'agentj-host/v1 version={version} sha256={digest}'
    Path(str(path) + '.sha256').write_text(digest + '\n')
    Path(str(path) + '.minisig').write_text('\n'.join((
        'untrusted comment: upgrade fixture', encode(b'ED' + kid + signature),
        'trusted comment: ' + comment, encode(key.sign(signature + comment.encode())), '')))
    return path


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class PublicUpgradeRetention(unittest.TestCase):
    def test_malformed_legacy_pending_version_does_not_affect_recovery(self):
        auto_update.prune_upgrade_downloads(None, '91.0.1', 5)

    def test_deep_legacy_receipt_is_preserved_without_affecting_upgrade(self):
        with tempfile.TemporaryDirectory(prefix='aj-retention-malformed-') as work:
            root = Path(work)
            cache = root / 'upgrade-cache'
            cache.mkdir(mode=0o700)
            wheel = cache / 'agentj-91.0.0-py3-none-any.whl'
            wheel.touch()
            receipt = wheel.with_suffix('.verified.json')
            receipt.write_text('[' * 1010 + '0' + ']' * 1010)
            with patch.object(auto_update.update, 'upgrade_cache_root', return_value=(root / 'xdg', 'xdg')):
                auto_update.prune_upgrade_downloads(SimpleNamespace(root=root), '91.0.2', '91.0.1')
            self.assertTrue(wheel.is_file())
            self.assertTrue(receipt.is_file())

    @unittest.skipUnless(shutil.which('uv'), 'requires real uv')
    def test_five_upgrades_keep_one_environment_and_a_working_rollback(self):
        with tempfile.TemporaryDirectory(prefix='aj-install-0379-') as work:
            root = Path(work)
            files = root / 'downloads'
            files.mkdir()
            versions = [f'91.0.{step}' for step in range(6)]
            key = Ed25519PrivateKey.generate()
            kid = b'upgrade!'
            wheels = [build_wheel(files, version, key, kid) for version in versions]
            keyring = {minisign.key_id_hex(kid): encode(b'Ed' + kid + key.public_key().public_bytes_raw())}
            listener = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=files))
            with listener:
                thread = threading.Thread(target=listener.serve_forever, daemon=True)
                thread.start()
                try:
                    self.run_journey(root, files, versions, wheels[0], listener.server_port, keyring)
                finally:
                    listener.shutdown()
                    thread.join()

    def run_journey(self, root, files, versions, initial_wheel, port, keyring):
        origin = f'http://127.0.0.1:{port}/'
        state, cache, tools = root/'state', root/'cache', root/'tools'
        env = {'HOME': str(root), 'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'AGENTJ_TEST': '1',
               'AGENTJ_STATE_DIR': str(state), 'XDG_CACHE_HOME': str(cache), 'XDG_CONFIG_HOME': str(root/'config'),
               'XDG_DATA_HOME': str(root/'data'), 'UV_TOOL_DIR': str(tools), 'UV_TOOL_BIN_DIR': str(root/'bin'),
               'UV_NO_CONFIG': '1', 'UV_NO_INDEX': '1', 'UV_PYTHON_DOWNLOADS': 'never', 'AGENTJ_UPGRADE_REPORT': 'off',
               'AGENTJ_UPDATE_URL': origin + 'latest.py', 'QA_DOWNLOAD_ORIGIN': origin, 'QA_KEYRING': json.dumps(keyring),
               'PYTHONDONTWRITEBYTECODE': '1'}

        def run(argv, extra=None):
            result = subprocess.run(argv, cwd=root, env={**env, **(extra or {})}, capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.strip()

        run([shutil.which('uv'), 'tool', 'install', '--offline', '--python', sys._base_executable, str(initial_wheel)])
        launcher = root / 'bin/agentj'
        driver = str(tools / 'agentj/bin/python')
        host_source = Path(__file__).resolve().parents[1]
        source_env = {'PYTHONPATH': str(host_source), 'QA_TEST_SITE_PACKAGES': sysconfig.get_path('purelib')}
        counts, cache_counts = [], []
        for old, new in zip(versions, versions[1:]):
            (files / 'latest.py').write_text(f'__version__ = {new!r}\n')
            output = run([driver, '-c', DRIVER, 'update', 'apply', '--yes'],
                         {**source_env, 'QA_CURRENT_VERSION': old})
            self.assertIn('UPGRADE_RESULT ok', output)
            self.assertEqual(run([str(launcher), '--version']), f'agentj {new}')
            counts.append(len([path for path in tools.iterdir() if path.is_dir()]))
            cache_counts.append(tuple(len(list(folder.glob('agentj-*.whl'))) for folder in (
                state/'install-artifacts', state/'upgrade-cache', cache/'agentj-upgrade/upgrade-cache')))
        self.assertEqual(counts, [1, 1, 1, 1, 1])
        self.assertEqual(cache_counts, [(2, 1, 1), (2, 2, 2), (2, 2, 2), (2, 2, 2), (2, 2, 2)])
        output = run([driver, '-c', DRIVER, 'update', 'rollback'],
                     {**source_env, 'QA_CURRENT_VERSION': versions[-1]})
        self.assertIn('rollback: ok', output)
        self.assertEqual(run([str(launcher), '--version']), f'agentj {versions[0]}')
