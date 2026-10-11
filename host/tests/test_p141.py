"""P141 installer signatures, installer handoff, and HTTPS downgrade boundaries (offline)."""
import _hermetic  # noqa: F401
import base64
import hashlib
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from agentj import auto_update, minisign, update

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER_SOURCE = ROOT / 'support/installation/installer.py'
installer = None
if INSTALLER_SOURCE.is_file():
    spec = importlib.util.spec_from_file_location('p141_installer', INSTALLER_SOURCE)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

@unittest.skipUnless(installer is not None, 'private installer/site assembly; tested in private host-full')
class Signatures(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()  # disposable signing fixture, not the release key
        self.kid = b'P141test'
        pub = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.ring = {self.kid[::-1].hex().upper(): base64.b64encode(b'Ed' + self.kid + pub).decode()}
        self.data = b'wheel fixture'
        self.name = 'agentj-9.8.7a1-py3-none-any.whl'
        self.comment = 'agentj-host/v1 version=9.8.7a1 sha256=' + hashlib.sha256(self.data).hexdigest()

    def signature(self, *, comment=None, kid=None, alg=b'ED'):
        sig = self.key.sign(hashlib.blake2b(self.data, digest_size=64).digest())
        tc = comment or self.comment
        return ('untrusted comment: fixture\n' + base64.b64encode(alg + (kid or self.kid) + sig).decode() +
                '\ntrusted comment: ' + tc + '\n' + base64.b64encode(self.key.sign(sig + tc.encode())).decode() + '\n').encode()

    def test_valid_signature_is_required_before_wheel_write(self):
        with tempfile.TemporaryDirectory() as d, patch.object(installer, 'TRUSTED_SIGNERS', self.ring), patch.object(installer, 'fetch', side_effect=[self.data, self.signature()]) as get:
            out = pathlib.Path(d) / 'wheel'
            installer.download(installer.SITE + '/dl/' + self.name, out, hashlib.sha256(self.data).hexdigest())
            self.assertEqual(out.read_bytes(), self.data)
            self.assertEqual(get.call_args_list[1].args, (installer.SITE + '/dl/' + self.name + '.minisig', 1024))

    def test_same_origin_rehashed_malware_and_signature_negatives_do_not_write(self):
        cases = [(b'changed bytes', self.signature()), (self.data, b''), (self.data, b'x' * 1025),
                 (self.data, self.signature(kid=b'unknown!')), (self.data, self.signature(alg=b'Ed')),
                 (self.data, self.signature(comment=self.comment.replace('9.8.7a1', '9.8.6a1'))),
                 (self.data, self.signature(comment=self.comment.replace('agentj-host', 'another-package'))),
                 (self.data, self.signature(comment=self.comment.replace(hashlib.sha256(self.data).hexdigest(), '0' * 64))),
                 (self.data, self.signature().replace(b'trusted comment: agentj', b'trusted comment: other', 1))]
        # Corrupt the global signature without altering the message signature.
        lines = self.signature().splitlines(); lines[3] = base64.b64encode(bytes(64)); cases.append((self.data, b'\n'.join(lines)))
        for data, sig in cases:
            with self.subTest(sig=len(sig)), tempfile.TemporaryDirectory() as d, patch.object(installer, 'TRUSTED_SIGNERS', self.ring), patch.object(installer, 'fetch', side_effect=[data, sig]):
                out = pathlib.Path(d) / 'wheel'
                with self.assertRaisesRegex(installer.InstallError, '^wheel_signature_failed$'):
                    installer.download(installer.SITE + '/dl/' + self.name, out, hashlib.sha256(data).hexdigest())
                self.assertFalse(out.exists())

    def test_production_signer_matches_host_and_existing_release(self):
        self.assertEqual(installer.TRUSTED_SIGNERS, minisign.TRUSTED_SIGNERS)
        dl = ROOT / 'site/public/dl'
        name = (dl / 'latest.txt').read_text().strip()
        installer.verify_wheel((dl / name).read_bytes(), (dl / (name + '.minisig')).read_bytes(), name)
        self.assertEqual((ROOT / 'site/public/dl/install-assistant.py').read_bytes(), (ROOT / 'support/installation/installer.py').read_bytes())

    def test_missing_signature_and_missing_verifier_fail_closed(self):
        with tempfile.TemporaryDirectory() as d, patch.object(installer, 'fetch', side_effect=[self.data, installer.InstallError('missing_signature')]):
            out = pathlib.Path(d) / 'wheel'
            with self.assertRaises(installer.InstallError):
                installer.download(installer.SITE + '/dl/' + self.name, out, hashlib.sha256(self.data).hexdigest())
            self.assertFalse(out.exists())
        import builtins
        real_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith('cryptography'): raise ImportError('unavailable')
            return real_import(name, *args, **kwargs)
        with patch.object(builtins, '__import__', side_effect=missing):
            with self.assertRaisesRegex(installer.InstallError, '^wheel_signature_failed$'):
                installer.verify_wheel(self.data, self.signature(), self.name)

@unittest.skipUnless(installer is not None, 'private setup/bootstrap assembly; tested in private host-full')
class Handoff(unittest.TestCase):
    def test_setup_handoff_executes_hash_check_on_good_and_bad_script(self):
        source = (ROOT / 'setup/setup-agents.sh').read_text()
        start = source.index('    curl --proto', source.index('AJTMP=$(mktemp'))
        end = source.index('    sh "$AJTMP/install-assistant.sh"', start)
        block = source[start:end]
        self.assertIn("--proto '=https' --proto-redir '=https'", block)
        bootstrap = (ROOT / 'site/public/install-assistant.sh').read_bytes()
        for data, expected in [(bootstrap, 0), (b'malicious-script', 1)]:
            with tempfile.TemporaryDirectory() as d:
                p = pathlib.Path(d); (p / 'install-assistant.sh').write_bytes(data)
                run = subprocess.run(['bash', '-c', 'set -e\ncurl() { :; }\nAJTMP="$1"\n' + block, 'fixture', d], capture_output=True)
                self.assertEqual(run.returncode, expected, run.stderr.decode())
        self.assertEqual(source, (ROOT / 'site/public/setup-agents.sh').read_text())
        self.assertIn('--with "cryptography>=44,<49"', bootstrap.decode())

class Redirects(unittest.TestCase):
    def test_https_redirect_allowed_but_http_downgrade_rejected_before_request(self):
        handler = update.SecureRedirect(); req = urllib.request.Request('https://agentj.app/start')
        good = handler.redirect_request(req, None, 302, 'Found', {}, 'https://cdn.example.invalid/new')
        self.assertEqual(good.full_url, 'https://cdn.example.invalid/new')
        for target in ['http://agentj.app/new', 'http://127.0.0.1/new']:
            with self.assertRaisesRegex(ValueError, '^insecure_update_redirect$'):
                handler.redirect_request(req, None, 302, 'Found', {}, target)

    def test_manual_and_automatic_paths_use_secure_opener(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return b'__version__ = "9.8.7a1"'
        with patch.object(update.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = Response()
            with patch.object(update, 'latest_url', return_value='https://agentj.app/latest'):
                self.assertEqual(update.fetch_latest(), ('9.8.7a1', 'ok'))
            self.assertIsInstance(build.call_args.args[0], update.SecureRedirect)
            self.assertEqual(auto_update.download('https://agentj.app/wheel', 100), b'__version__ = "9.8.7a1"')
            self.assertIsInstance(build.call_args.args[0], update.SecureRedirect)
        with patch.object(update, 'open_download', side_effect=ValueError('insecure_update_redirect')):
            self.assertEqual(update.fetch_latest(), (None, 'network'))
            with self.assertRaisesRegex(ValueError, 'insecure_update_redirect'): auto_update.download('https://agentj.app/wheel', 100)
