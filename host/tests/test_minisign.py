"""minisign.py (protocol/PLAZA_PACKAGES.md §2): the protocol vector, every bad case, the plaza trusted comment, legacy mode,
untrusted keys, and a cross-check against the real `minisign` binary (throw-away key in a temp dir; skipped without it)."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import market_fixtures as fx  # noqa: E402
from agentj import bundle, minisign  # noqa: E402

VEC = json.loads((pathlib.Path(__file__).resolve().parents[2] / "protocol" / "vectors" / "minisign.json").read_text())
MINISIGN = shutil.which("minisign") or (os.path.expanduser("~/.local/bin/minisign") if os.path.exists(os.path.expanduser("~/.local/bin/minisign")) else None)


class Vector(unittest.TestCase):
    def setUp(self):
        kid, _ = minisign.parse_pubkey(VEC["pubkey"])
        self.ring = {kid: VEC["pubkey"]}
        self.msg = base64.b64decode(VEC["message_b64"])

    def test_good_vector(self):
        self.assertEqual(minisign.verify(self.msg, VEC["minisig"], self.ring), VEC["trusted_comment"])
        tc = minisign.verify_package(self.msg, VEC["minisig"], {"name": "demo-skill", "version": "1.0.0", "type": "skill"}, self.ring)
        self.assertEqual(tc, {"name": "demo-skill", "version": "1.0.0", "type": "skill", "sha256": VEC["message_sha256"]})

    def test_every_bad_case_refused(self):
        self.assertGreaterEqual(len(VEC["bad"]), 5)
        for b in VEC["bad"]:
            with self.subTest(b["why"]), self.assertRaises(minisign.MinisignError):
                minisign.verify(base64.b64decode(b["message_b64"]), b["minisig"], self.ring)

    def test_trusted_comment_must_name_this_package(self):
        for m in ({"name": "other-skill", "version": "1.0.0", "type": "skill"}, {"name": "demo-skill", "version": "1.0.1", "type": "skill"},
                  {"name": "demo-skill", "version": "1.0.0", "type": "workflow"}):
            with self.subTest(m), self.assertRaisesRegex(minisign.MinisignError, "another package"):
                minisign.verify_package(self.msg, VEC["minisig"], m, self.ring)

    def test_not_in_the_compiled_keyring(self):
        with self.assertRaisesRegex(minisign.MinisignError, "not trusted"):
            minisign.verify(self.msg, VEC["minisig"])

    def test_compiled_key(self):
        self.assertEqual(list(minisign.TRUSTED_SIGNERS), ["B79F925B0585F9D4"])
        kid, pk = minisign.parse_pubkey(minisign.TRUSTED_SIGNERS["B79F925B0585F9D4"])
        self.assertEqual((kid, len(pk)), ("B79F925B0585F9D4", 32))

    def test_malformed(self):
        for bad in ("", "x", VEC["minisig"].replace("trusted comment", "trusted kommentar"), VEC["minisig"] * 2,
                    "untrusted comment: x\n" + "A" * 2000, None):
            with self.subTest(repr(bad)[:40]), self.assertRaises(minisign.MinisignError):
                minisign.verify(self.msg, bad, self.ring)


class OwnKey(unittest.TestCase):
    def test_legacy_mode_and_keyring_mismatch(self):
        k = fx.TestKey()
        msg = b"bundle bytes"
        tc = minisign.trusted_comment("demo-skill", "1.0.0", "skill", bundle.sha256(msg))
        self.assertEqual(minisign.verify(msg, k.sign(msg, tc), k.keyring), tc)
        with self.assertRaisesRegex(minisign.MinisignError, "prehashed"):
            minisign.verify(msg, k.sign(msg, tc, alg=b"Ed"), k.keyring)
        other = fx.TestKey()
        with self.assertRaisesRegex(minisign.MinisignError, "does not match"):
            minisign.verify(msg, k.sign(msg, tc), {k.key_id: other.pub_line})
        with self.assertRaisesRegex(minisign.MinisignError, "global"):
            minisign.verify(msg, k.sign(msg, tc, global_tc=tc + " x"), k.keyring)


@unittest.skipUnless(MINISIGN, "no minisign binary")
class RealMinisign(unittest.TestCase):
    def test_cross_check_with_minisign_0_12(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = pathlib.Path(tmp)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(t)}
            subprocess.run([MINISIGN, "-G", "-W", "-p", str(t / "k.pub"), "-s", str(t / "k.key")], check=True, env=env,
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
            data, m = bundle.build(fx.make_skill(t / "pkg"))
            (t / "b.ajpkg").write_bytes(data)
            tc = minisign.trusted_comment(m["name"], m["version"], m["type"], bundle.sha256(data))
            subprocess.run([MINISIGN, "-S", "-W", "-s", str(t / "k.key"), "-m", str(t / "b.ajpkg"), "-x", str(t / "b.minisig"),
                            "-t", tc], check=True, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
            pub = (t / "k.pub").read_text().splitlines()[1]
            kid, _ = minisign.parse_pubkey(pub)
            self.assertIn(kid, (t / "k.pub").read_text().splitlines()[0], "our key id = the id minisign prints")
            sig = (t / "b.minisig").read_text()
            ring = {kid: pub}
            self.assertEqual(minisign.verify_package(data, sig, m, ring)["sha256"], bundle.sha256(data))
            with self.assertRaises(minisign.MinisignError):
                minisign.verify_package(data + b"x", sig, m, ring)
            # and the other way round: what we verify, minisign verifies
            r = subprocess.run([MINISIGN, "-V", "-p", str(t / "k.pub"), "-m", str(t / "b.ajpkg"), "-x", str(t / "b.minisig")],
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
