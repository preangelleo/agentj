"""#0307: a paid macOS host (0.28.6 → 0.30.5) failed at step=download, artifact_stage=cache, PermissionError errno 13 after
both hashes matched — ~/.cache/agentj-upgrade belonged to root (an earlier sudo run). The verified bytes now go to a private
temporary folder; nothing is sudo'd, nothing is checked less; a failure names the folder kind and its owner kind only.
No network, no installer, no real sudo (an unwritable folder is a 0500 mode; root ownership is the lstat seam)."""
import _hermetic  # noqa: F401
import base64
import hashlib
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import auto_update as au, minisign, support, update, upgrade_diag as diag, upgrade_owner
from agentj.state import State

V = '99.0.0a1'


@unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'mode-based refusal needs a non-root POSIX account')
class UnwritableCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-0307-', dir=os.environ.get('TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(os.path.realpath(self.tmp.name)) / 'home'
        self.private_tmp = self.home / 'tmp'
        self.private_tmp.mkdir(parents=True)
        env = patch.dict(os.environ, {'HOME': str(self.home)})
        env.start(); self.addCleanup(env.stop)
        os.environ.pop('XDG_CACHE_HOME', None)
        t = patch.object(tempfile, 'tempdir', str(self.private_tmp))
        t.start(); self.addCleanup(t.stop)
        self.root = self.home / '.cache' / 'agentj-upgrade'
        key, kid = Ed25519PrivateKey.generate(), b'abcdefgh'
        keys = {minisign.key_id_hex(kid): base64.b64encode(b'Ed' + kid + key.public_key().public_bytes_raw()).decode()}
        k = patch.object(minisign, 'keyring', return_value=keys)
        k.start(); self.addCleanup(k.stop)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr(f'agentj-{V}.dist-info/METADATA', f'Name: agentj\nVersion: {V}\n')
        self.body = out.getvalue()
        self.digest = hashlib.sha256(self.body).hexdigest()
        comment = f'agentj-host/v1 version={V} sha256={self.digest}'
        sig = key.sign(hashlib.blake2b(self.body, digest_size=64).digest())
        self.signature = ('untrusted comment: fixture\n' + base64.b64encode(b'ED' + kid + sig).decode() + '\ntrusted comment: '
                          + comment + '\n' + base64.b64encode(key.sign(sig + comment.encode())).decode() + '\n')
        self.stated = self.digest
        self.calls = []
        orig = au.cached_wheel
        c = patch.object(au, 'cached_wheel', lambda st, v, **kw: orig(st, v, fetch=self.fetch, **kw))
        c.start(); self.addCleanup(c.stop)

    def fetch(self, url, limit):
        self.calls.append(url)
        if url.endswith('.sha256'):
            return (self.stated + '\n').encode()
        return self.signature.encode() if url.endswith('.minisig') else self.body

    def lock(self, p):
        p.mkdir(parents=True, exist_ok=True)
        p.chmod(0o500)
        self.addCleanup(p.chmod, 0o700)

    def assert_fallback(self, wheel):
        self.assertTrue(str(wheel).startswith(str(self.private_tmp) + os.sep), wheel)
        self.assertEqual(wheel.read_bytes(), self.body)
        folder = wheel.parent.parent
        meta = folder.lstat()
        self.assertEqual((meta.st_uid, stat.S_IMODE(meta.st_mode)), (os.geteuid(), 0o700))
        # The reinstall path re-reads the sealed bytes from the folder the wheel actually went to.
        self.assertEqual(au.signed_cached_bytes(State(folder), V), self.body)
        self.assertEqual(len(self.calls), 3)

    def test_root_owned_upgrade_root_falls_back(self):
        self.lock(self.root)                                   # the customer's shape: agentj-upgrade/ not writable
        self.assert_fallback(update.verified_wheel(V))
        plan = update.verified_plan(None, {'kind': 'pip', 'where': str(self.home / 'venv')}, V)
        self.assertTrue(plan[-1][-1].startswith(str(self.private_tmp)))

    def test_unwritable_cache_dir_with_stale_wheel_falls_back(self):
        d = self.root / 'upgrade-cache'
        d.mkdir(parents=True)
        (d / f'agentj-{V}-py3-none-any.whl').write_bytes(b'stale')
        (d / f'agentj-{V}-py3-none-any.verified.json').write_text('{}')
        self.lock(d)
        self.assert_fallback(update.verified_wheel(V))

    def test_unwritable_home_cache_falls_back(self):
        self.lock(self.home / '.cache')                        # ~/.cache itself root-owned: agentj-upgrade cannot be made
        self.assert_fallback(update.verified_wheel(V))

    def test_writable_cache_unchanged(self):
        wheel = update.verified_wheel(V)
        self.assertEqual(wheel, self.root / 'upgrade-cache' / f'agentj-{V}-py3-none-any.whl')
        self.calls.clear()
        self.assertEqual(update.verified_wheel(V), wheel)       # second run: verified cache hit, no download
        self.assertEqual(self.calls, [])

    def test_fallback_never_weakens_the_hash_check(self):
        self.lock(self.root)
        self.stated = '0' * 64
        with self.assertRaises(ValueError) as cm:
            update.verified_wheel(V)
        self.assertEqual(diag.plan_fields(cm.exception)['subreason'], 'hash_mismatch')
        self.assertEqual(list(self.private_tmp.iterdir()), [])  # nothing staged for refused bytes

    def test_both_unwritable_reports_kind_and_owner_only(self):
        self.lock(self.root)
        self.private_tmp.chmod(0o500)
        self.addCleanup(self.private_tmp.chmod, 0o700)
        with self.assertRaises(PermissionError) as cm:
            update.verified_wheel(V)
        fields = diag.plan_fields(cm.exception)
        for k, v in {'artifact_stage': 'cache', 'subreason': 'internal_error', 'cache_path': 'home',
                     'cache_level': 'upgrade_root', 'cache_owner': 'self', 'cache_fallback': 'tmp',
                     'artifact_sha256': self.digest}.items():
            self.assertEqual(fields.get(k), v, k)
        res = {'from': '0.28.6', 'to': V, 'result': 'failed', 'reason': 'download_failed', 'step': 'download',
               'exception_type': 'PermissionError', 'errno': 13, **fields}
        body = diag.metadata_body(res, 'uv')
        for line in ('cache_path: home', 'cache_level: upgrade_root', 'cache_owner: self', 'cache_fallback: tmp'):
            self.assertIn(line, body)
        self.assertTrue(support.metadata_ok(body))
        self.assertNotIn(str(self.home), body)
        for bad in ('cache_path: /Users/x', 'cache_owner: catherine', 'cache_level: ../x'):
            self.assertFalse(diag.meta_line_ok(bad), bad)

    def test_xdg_cache_kind(self):
        os.environ['XDG_CACHE_HOME'] = str(self.home / 'xdg')
        self.lock(self.home / 'xdg' / 'agentj-upgrade')
        self.private_tmp.chmod(0o500)
        self.addCleanup(self.private_tmp.chmod, 0o700)
        with self.assertRaises(PermissionError) as cm:
            update.verified_wheel(V)
        self.assertEqual(diag.plan_fields(cm.exception)['cache_path'], 'xdg')


class OwnerFixLine(unittest.TestCase):
    """The #475 one-step fix line now also hands back a root-owned upgrade cache (the 0.28.6 customer's leftover)."""

    def test_root_owned_upgrade_cache_in_fix_line(self):
        with tempfile.TemporaryDirectory(prefix='aj-0307o-', dir=os.environ.get('TEST_TMP')) as t:
            home = Path(os.path.realpath(t)) / 'Users' / 'catherine'
            tool = home / '.local/share/uv/tools/agentj'
            tool.mkdir(parents=True)
            cache = home / '.cache' / 'agentj-upgrade'
            (cache / 'upgrade-cache').mkdir(parents=True)

            def lstat(p):
                f = list(os.lstat(p))
                p = os.path.normpath(str(p))
                f[4] = 0 if p == str(cache) or p.startswith(str(cache) + os.sep) else os.getuid()
                return os.stat_result(f)
            with patch.object(upgrade_owner, 'LSTAT', lstat), patch.object(upgrade_owner, 'HOME_OF', lambda uid: str(home)), \
                    patch.object(upgrade_owner, '_user', return_value='catherine'):
                own = upgrade_owner.blocked(str(tool), uid=os.getuid() or 501, environ={})
        self.assertEqual(own['owner_kind'], 'root')
        self.assertEqual(own['paths'], [str(cache)])
        self.assertIn('chown -R -h catherine', own['fix'])


if __name__ == '__main__':
    unittest.main()
