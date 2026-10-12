"""P191 (audit B2): per-purpose signer keyrings, pre-announced backup keys and revocation — backward compatible with the
one key every 0.18 host trusts. Disposable fixture keys only; nothing here touches the release key."""
import _hermetic  # noqa: F401,I001
import base64
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentj import auto_update as au, minisign, notices, state, wire


class Key:
    def __init__(self, kid: bytes):
        self.sk = Ed25519PrivateKey.generate()
        self.kid = kid
        self.raw = self.sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.id = kid[::-1].hex().upper()
        self.pub = base64.b64encode(b'Ed' + kid + self.raw).decode()

    def minisig(self, data: bytes, comment: str) -> str:
        sig = self.sk.sign(hashlib.blake2b(data, digest_size=64).digest())
        return ('untrusted comment: fixture\n' + base64.b64encode(b'ED' + self.kid + sig).decode() + '\ntrusted comment: ' + comment
                + '\n' + base64.b64encode(self.sk.sign(sig + comment.encode())).decode() + '\n')


class Split:
    """Patch the compiled-in tables as a future release would ship them: legacy + one new key per role."""
    def __init__(self, roles, keys, revoked=frozenset()):
        self.p = [patch.object(minisign, 'SIGNERS', keys), patch.object(minisign, 'ROLES', roles), patch.object(minisign, 'REVOKED', revoked)]

    def __enter__(self):
        for p in self.p:
            p.start()

    def __exit__(self, *a):
        for p in self.p:
            p.stop()


class Compatible(unittest.TestCase):
    def test_every_role_still_trusts_exactly_the_legacy_key(self):
        for role in ('release', 'plaza', 'control'):
            self.assertEqual(minisign.keyring(role), {minisign.LEGACY_KEY_ID: minisign.SIGNERS[minisign.LEGACY_KEY_ID]}, role)
        self.assertEqual(minisign.TRUSTED_SIGNERS, {minisign.LEGACY_KEY_ID: minisign.SIGNERS[minisign.LEGACY_KEY_ID]})
        self.assertEqual(minisign.REVOKED, frozenset())

    def test_notice_pin_is_the_control_ring(self):
        self.assertEqual(notices.official_keys(), [minisign.parse_pubkey(minisign.SIGNERS[minisign.LEGACY_KEY_ID])[1]])

    def test_unknown_role_is_an_error_not_an_empty_trust(self):
        with self.assertRaises(KeyError):
            minisign.keyring('anything')


class Separated(unittest.TestCase):
    def setUp(self):
        self.release, self.plaza, self.control = Key(b'REL191aa'), Key(b'PLZ191bb'), Key(b'CTL191cc')
        self.keys = {k.id: k.pub for k in (self.release, self.plaza, self.control)}
        self.roles = {'release': (self.release.id,), 'plaza': (self.plaza.id,), 'control': (self.control.id,)}
        self.data = b'wheel bytes'
        self.comment = 'agentj-host/v1 version=99.1.0a1 sha256=' + hashlib.sha256(self.data).hexdigest()

    def test_release_key_signs_wheels_only(self):
        with Split(self.roles, self.keys):
            self.assertEqual(minisign.verify(self.data, self.release.minisig(self.data, self.comment), minisign.keyring('release')), self.comment)
            # Positive for the right key, negative for the other two purposes' keys.
            for k in (self.plaza, self.control):
                with self.subTest(k.id), self.assertRaisesRegex(minisign.MinisignError, 'not trusted'):
                    minisign.verify(self.data, k.minisig(self.data, self.comment), minisign.keyring('release'))

    def test_a_compromised_control_key_cannot_certify_a_plaza_package(self):
        bundle = b'pkg'
        man = {'name': 'demo-skill', 'version': '1.0.0', 'type': 'skill'}
        tc = minisign.trusted_comment('demo-skill', '1.0.0', 'skill', hashlib.sha256(bundle).hexdigest())
        with Split(self.roles, self.keys):
            self.assertEqual(minisign.verify_package(bundle, self.plaza.minisig(bundle, tc), man)['name'], 'demo-skill')
            with self.assertRaisesRegex(minisign.MinisignError, 'not trusted'):
                minisign.verify_package(bundle, self.control.minisig(bundle, tc), man)

    def test_notices_follow_the_control_key(self):
        body = wire.b64u(json.dumps({'x': 1}).encode())
        msg = (notices.CONTEXT + '\n' + body).encode()
        with Split(self.roles, self.keys):
            self.assertEqual(notices.opened({'body': body, 'sig': wire.b64u(self.control.sk.sign(msg))}, notices.CONTEXT), {'x': 1})
            self.assertIsNone(notices.opened({'body': body, 'sig': wire.b64u(self.release.sk.sign(msg))}, notices.CONTEXT))

    def test_auto_update_uses_the_release_ring(self):
        with tempfile.TemporaryDirectory() as d, Split(self.roles, self.keys):
            st = state.State(pathlib.Path(d)); st.init()
            def fetch(sig):
                return lambda url, _: (hashlib.sha256(self.data).hexdigest() + '\n').encode() if url.endswith('.sha256') else sig.encode() if url.endswith('.minisig') else self.data
            # The control key (online, in the cloud) must not be able to ship a host upgrade.
            with self.assertRaises(minisign.MinisignError):
                au.cached_wheel(st, '99.1.0a1', fetch=fetch(self.control.minisig(self.data, self.comment)))


class BackupAndRevocation(unittest.TestCase):
    def setUp(self):
        self.old, self.backup = Key(b'OLD191dd'), Key(b'BAK191ee')
        self.keys = {self.old.id: self.old.pub, self.backup.id: self.backup.pub}
        self.data = b'x'
        self.comment = 'agentj-host/v1 version=99.2.0 sha256=' + hashlib.sha256(self.data).hexdigest()

    def test_pre_announced_backup_verifies_before_it_is_ever_used(self):
        with Split({'release': (self.old.id, self.backup.id), 'plaza': (self.old.id,), 'control': (self.old.id,)}, self.keys):
            ring = minisign.keyring('release')
            for k in (self.old, self.backup):
                self.assertEqual(minisign.verify(self.data, k.minisig(self.data, self.comment), ring), self.comment)

    def test_revoked_key_fails_everywhere_even_when_injected(self):
        with Split({'release': (self.old.id, self.backup.id), 'plaza': (self.old.id,), 'control': (self.old.id,)}, self.keys,
                   frozenset({self.old.id})):
            self.assertNotIn(self.old.id, minisign.keyring('release'))
            self.assertEqual(minisign.keyring('plaza'), {})
            self.assertNotIn(self.old.id, minisign.TRUSTED_SIGNERS)
            with self.assertRaisesRegex(minisign.MinisignError, 'revoked'):
                minisign.verify(self.data, self.old.minisig(self.data, self.comment), {self.old.id: self.old.pub})
            self.assertEqual(minisign.verify(self.data, self.backup.minisig(self.data, self.comment), minisign.keyring('release')), self.comment)


if __name__ == '__main__':
    unittest.main()
