"""Real control-v1 crypto against a temporary table (unit scope, not Host qualification)."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import controls, owner_hosts as oh, wire
from test_owner_hosts_pure import row


class SignedTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.key = Ed25519PrivateKey.generate()
        self.pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.keys = {'paired-phone': self.pub}
        self.st = SimpleNamespace(root=Path(self.tmp.name), sign_key=self.keys.get)
        self.desk = oh.Desk(SimpleNamespace(st=self.st, peers=None))
        self.scope = {'channel': 'test-channel', 'host': 'test-public-host', 'seat': 'test-seat'}
        self.scope_patch = patch.object(oh, 'scope', return_value=self.scope)
        self.scope_patch.start(); self.addCleanup(self.scope_patch.stop)
        self.target = {'op': 'replace', 'rev': '', 'scope': self.scope.copy(), 'rows': [row()]}

    def signed(self):
        n, ts = 'a' * 32, int(time.time() * 1000)
        msg = controls.signed_message(self.scope['channel'], 'paired-phone', oh.ACTION, n, ts,
                                      controls.object_digest(oh.ACTION, self.target))
        return {'n': n, 'ts': ts, 'sig': wire.b64u(self.key.sign(msg))}

    def fixture(self):
        signed = self.signed()
        rec = {'device': 'paired-phone', 'target': copy.deepcopy(self.target), **signed}
        self.write(rec)
        return rec

    def write(self, rec):
        path, mark = self.desk._paths()
        path.write_text(oh.canonical(rec))
        mark.write_text(hashlib.sha256(oh.canonical(rec).encode()).hexdigest())

    def test_real_signature_and_device_rechecked_on_each_read(self):
        self.fixture(); self.assertIsNotNone(self.desk.read())
        self.keys.clear(); self.assertIsNone(self.desk.read())
        self.assertIsNone(oh.Desk(self.desk.host).read())

    def test_tampering_table_and_marker_cannot_forge_signature(self):
        rec = self.fixture()
        for field, changed in [('hostname', 'outside.example'), ('port', 2222), ('peer', 'other'), ('expires', int(time.time()) + 9999)]:
            modified = copy.deepcopy(rec); modified['target']['rows'][0][field] = changed
            self.write(modified)
            self.assertIsNone(oh.Desk(self.desk.host).read())

    def test_scope_seat_host_and_channel_are_bound(self):
        rec = self.fixture()
        for field in self.scope:
            modified = copy.deepcopy(rec); modified['target']['scope'][field] = 'other'
            self.write(modified)
            self.assertIsNone(oh.Desk(self.desk.host).read())

    def test_key_rotation_and_foreign_device_invalidate(self):
        rec = self.fixture()
        new = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.keys['paired-phone'] = new
        self.assertIsNone(self.desk.read())
        self.keys['paired-phone'] = self.pub; self.keys['other'] = self.pub
        rec['device'] = 'other'; self.write(rec)
        self.assertIsNone(oh.Desk(self.desk.host).read())

    def test_nonce_replay_stale_and_parameter_change(self):
        auth = self.signed(); nonces = controls.Nonces()
        check = lambda target: controls.check(self.st, nonces, self.scope['channel'], 'paired-phone', auth, oh.ACTION, target)
        self.assertIsNone(check(self.target)); self.assertEqual(check(self.target), 'replay')
        changed = copy.deepcopy(self.target); changed['rows'][0]['user'] = 'other'
        self.assertEqual(check(changed), 'bad_signature')
        auth['ts'] -= 300000
        self.assertEqual(check(self.target), 'stale')

    def test_missing_marker_and_in_memory_rollback_invalidate(self):
        old = self.fixture(); self.assertIsNotNone(self.desk.read())
        _, mark = self.desk._paths(); mark.unlink()
        self.assertIsNone(self.desk.read())
        self.target['rev'] = 'new-revision'; self.target['rows'] = []
        self.fixture()
        fresh = oh.Desk(self.desk.host); self.assertIsNotNone(fresh.read())
        self.write(old)
        self.assertIsNone(fresh.read())

    def test_symlink_table_or_marker_is_not_followed(self):
        self.fixture()
        path, mark = self.desk._paths()
        original = path.read_text()
        target = Path(self.tmp.name) / 'other-metadata'; target.write_text(original)
        path.unlink(); path.symlink_to(target)
        self.assertIsNone(self.desk.read())
        path.unlink(); path.write_text(original)
        original_mark = mark.read_text()
        other = Path(self.tmp.name) / 'other-marker'; other.write_text(original_mark)
        mark.unlink(); mark.symlink_to(other)
        self.assertIsNone(self.desk.read())

    def test_js_python_control_bytes_match(self):
        import subprocess
        protocol = (Path(__file__).resolve().parents[2] / 'protocol' / 'wire.js').as_uri()
        script = (f"import {{controlMessage}} from {json.dumps(protocol)};"
                  f"console.log(Buffer.from(await controlMessage('CH','DEV','{oh.ACTION}','{'a'*32}',1790000000123,{json.dumps(self.target)})).toString('hex'))")
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = controls.signed_message('CH', 'DEV', oh.ACTION, 'a' * 32, 1790000000123, controls.object_digest(oh.ACTION, self.target))
        self.assertEqual(result.stdout.strip(), expected.hex())


if __name__ == '__main__': unittest.main()
