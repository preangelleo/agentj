"""P586 cache corruption recovery and safe diagnostics, no network or installer."""
import _hermetic  # noqa: F401
import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import auto_update as au, minisign, update, upgrade_diag as diag
from agentj.state import State

V = '99.0.0a1'


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name)/'state')
        self.st.init(relay="ws://127.0.0.1:1")
        self.path = self.st.root/'upgrade-cache'/f'agentj-{V}-py3-none-any.whl'
        self.path.parent.mkdir()
        self.receipt = self.path.with_suffix('.verified.json')
        self.key = Ed25519PrivateKey.generate()
        self.kid = b'abcdefgh'
        self.keys = {minisign.key_id_hex(self.kid): base64.b64encode(b'Ed'+self.kid+self.key.public_key().public_bytes_raw()).decode()}
        self.body = self.wheel()
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.signature = self.sign(self.body)
        p = patch.object(minisign, 'keyring', return_value=self.keys)
        p.start(); self.addCleanup(p.stop)
        self.calls = []

    def wheel(self, version=V):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr(f'agentj-{V}.dist-info/METADATA', f'Name: agentj\nVersion: {version}\n')
        return out.getvalue()

    def sign(self, body, comment=None, kid=None):
        comment = comment or f'agentj-host/v1 version={V} sha256={hashlib.sha256(body).hexdigest()}'
        sig = self.key.sign(hashlib.blake2b(body, digest_size=64).digest())
        return 'untrusted comment: fixture\n'+base64.b64encode(b'ED'+(kid or self.kid)+sig).decode()+'\ntrusted comment: '+comment+'\n'+base64.b64encode(self.key.sign(sig+comment.encode())).decode()+'\n'

    def fetch(self, url, limit):
        self.calls.append(url)
        return (self.digest+'\n').encode() if url.endswith('.sha256') else self.signature.encode() if url.endswith('.minisig') else self.body

    def good_receipt(self):
        return {'sha256': self.digest, 'signed': True, 'signature': self.signature}

    def test_receipt_matrix_recovers(self):
        bad = [None, [], 'text', 42, True, {}, {'sha256': []},
               {**self.good_receipt(), 'sha256': None}, {**self.good_receipt(), 'sha256': 'x'},
               {**self.good_receipt(), 'signature': None}, {**self.good_receipt(), 'signature': []},
               {**self.good_receipt(), 'signed': 'true'}, {**self.good_receipt(), 'signed': 1}]
        for rec in bad:
            with self.subTest(receipt=rec):
                self.path.write_bytes(self.body)
                self.receipt.write_text(json.dumps(rec))
                self.calls.clear()
                self.assertEqual(au.cached_wheel(self.st, V, fetch=self.fetch).read_bytes(), self.body)
                self.assertEqual(len(self.calls), 3)
        self.calls.clear()
        au.cached_wheel(self.st, V, fetch=self.fetch)
        self.assertEqual(self.calls, [])

    def test_corrupt_bytes_and_receipt_redownload(self):
        for body, receipt in [(self.body[:20], json.dumps(self.good_receipt())),
                              (self.wheel('98.0.0a1'), json.dumps(self.good_receipt())),
                              (self.body, '{broken'), (self.body, json.dumps({**self.good_receipt(), 'signature': 'bad'}))]:
            with self.subTest(body=body[:10], receipt=receipt[:10]):
                self.path.write_bytes(body); self.receipt.write_text(receipt); self.calls.clear()
                au.cached_wheel(self.st, V, fetch=self.fetch)
                self.assertEqual(len(self.calls), 3)

    def test_bad_downloads_no_installer(self):
        cases = [('hash_mismatch', 'sha256'), ('signature_invalid', 'signature'), ('untrusted_key', 'signature'),
                 ('comment_mismatch', 'signature'), ('identity_mismatch', 'identity'), ('bad_zip', 'identity'), ('bad_zip_crc', 'identity')]
        for reason, stage in cases:
            with self.subTest(reason=reason):
                self.path.unlink(missing_ok=True); self.receipt.write_text('null')
                body = self.wheel('98.0.0a1') if reason == 'identity_mismatch' else b'half wheel' if reason == 'bad_zip' else self.wheel()
                if reason == 'bad_zip_crc':
                    body = body.replace(b'Name: agentj', b'Name: broken', 1)  # Keep size; stale ZIP CRC, valid release signature below.
                digest = hashlib.sha256(body).hexdigest()
                signature = self.sign(body, comment='other' if reason == 'comment_mismatch' else None,
                                      kid=b'unknown!' if reason == 'untrusted_key' else None)
                if reason == 'signature_invalid': signature = 'broken'
                def fetch(url, limit):
                    return (('0'*64 if reason == 'hash_mismatch' else digest)+'\n').encode() if url.endswith('.sha256') else signature.encode() if url.endswith('.minisig') else body
                def cache(st, version, **kwargs):
                    return au.cached_wheel(st, version, fetch=fetch) if kwargs.get('require_signature', True) else self.path
                installer = Mock()
                with patch.object(update, 'writable', return_value=True), patch.object(update, 'install_kind', return_value={'kind': 'pip', 'where': self.tmp.name}):
                    res = update.apply(Mock(), check_fn=lambda: {'status': 'newer', 'latest': V},
                                       run=installer, svc_on=False,
                                       plan_fn=lambda st, info, target: au.install_commands(info, cache(self.st, target)))
                installer.assert_not_called()
                self.assertEqual(res['reason'], 'signature_or_hash_mismatch')
                for key, expected in [('subreason', 'bad_zip' if reason == 'bad_zip_crc' else reason), ('artifact_stage', stage), ('artifact_sha256', digest)]:
                    self.assertEqual(res[key], expected)
                with patch.object(update, 'install_kind', return_value={'kind': 'pip', 'where': self.tmp.name}), patch.object(update, 'new_argv', return_value=['fixture-cli']):
                    auto = au.run(self.st, check_fn=lambda: {'status': 'newer', 'latest': V}, cache=cache,
                                  runner=installer, restart=Mock())
                installer.assert_not_called()
                for key in ('reason', 'detail', 'subreason', 'artifact_stage', 'artifact_sha256'):
                    self.assertEqual(auto[key], res[key], key)
                last = json.loads((self.st.root/diag.LAST).read_text())
                self.assertEqual(last['subreason'], 'bad_zip' if reason == 'bad_zip_crc' else reason)
                self.assertEqual(last['artifact_stage'], stage)
                self.assertFalse(self.path.exists())

    def test_safe_fields_and_detail(self):
        self.assertEqual(diag.plan_detail(ValueError('private_internal_hostname')), 'ValueError')
        self.assertEqual(diag.plan_fields(AttributeError('/private/path'))['subreason'], 'internal_error')
        res = {'subreason': 'private', 'artifact_stage': '/tmp/private', 'artifact_sha256': 'private'}
        self.assertEqual(diag.artifact_fields(res), {})
        res = {'from': '0.26.0a1', 'to': V, 'result': 'failed', 'reason': 'signature_or_hash_mismatch', 'step': 'download', 'service': 'none',
               'subreason': 'hash_mismatch', 'artifact_stage': 'sha256', 'artifact_sha256': self.digest}
        self.assertEqual(diag.entry(res)['subreason'], 'hash_mismatch')
        self.assertIn('artifact_stage: sha256', diag.body(res))
        self.assertIn('subreason: hash_mismatch', update.result_block(res))
