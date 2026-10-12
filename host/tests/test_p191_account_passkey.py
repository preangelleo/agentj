"""P191 (audit B1) preparation: the host verifying an account passkey assertion bound to one exact signed request.
Disposable P-256 / Ed25519 keys; no network, no state; remote_pair.py is not wired to it yet."""
import _hermetic  # noqa: F401,I001
import hashlib
import json
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from agentj import account_passkey as ap, wire
from agentj.passkey import PasskeyError


def jwk_ec(k):
    n = k.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": wire.b64u(n.x.to_bytes(32, "big")), "y": wire.b64u(n.y.to_bytes(32, "big"))}


class Assertion(unittest.TestCase):
    def setUp(self):
        self.sk = ec.generate_private_key(ec.SECP256R1())
        self.pins = [{"id": wire.b64u(b"cred-1"), "alg": -7, "jwk": jwk_ec(self.sk)}]
        self.body = wire.b64u(json.dumps({"op": "start", "id": "x" * 22}).encode())
        self.want = ap.challenge("agentj-remote-pair-v1", self.body)

    def make(self, *, origin=ap.ACCOUNT_ORIGIN, rp=ap.ACCOUNT_RP, flags=0x05, challenge=None, typ="webauthn.get", cred=b"cred-1", sk=None):
        cd = json.dumps({"type": typ, "challenge": wire.b64u(challenge or self.want), "origin": origin}).encode()
        ad = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + (0).to_bytes(4, "big")
        sig = (sk or self.sk).sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": wire.b64u(cred), "cd": wire.b64u(cd), "ad": wire.b64u(ad), "sig": wire.b64u(sig)}

    def test_owner_assertion_over_this_request_verifies(self):
        self.assertEqual(ap.verify(self.pins, self.make(), self.want), wire.b64u(b"cred-1"))

    def test_challenge_binds_context_and_exact_body(self):
        self.assertNotEqual(ap.challenge("agentj-remote-pair-v1", self.body + "A"), self.want)
        self.assertNotEqual(ap.challenge("agentj-official-notice-v1", self.body), self.want)
        other = ap.challenge("agentj-remote-pair-v1", wire.b64u(b'{"op":"resume"}'))
        with self.assertRaisesRegex(PasskeyError, "cd_challenge"):
            ap.verify(self.pins, self.make(challenge=other), self.want)

    def test_refusals(self):
        cases = {
            "cd_origin": self.make(origin="https://m.agentj.app"),
            "rp_hash": self.make(rp="evil.example"),
            "flags": self.make(flags=0x01),                                   # presence without verification
            "cd_type": self.make(typ="webauthn.create"),
            "unknown_credential": self.make(cred=b"cred-2"),
            "signature": self.make(sk=ec.generate_private_key(ec.SECP256R1())),  # the control plane's own key
        }
        for why, a in cases.items():
            with self.subTest(why), self.assertRaisesRegex(PasskeyError, why):
                ap.verify(self.pins, a, self.want)
        tampered = self.make(); tampered["ad"] = wire.b64u(wire.unb64u(tampered["ad"])[:-1] + b"\x07")
        with self.assertRaisesRegex(PasskeyError, "signature"):
            ap.verify(self.pins, tampered, self.want)
        for pins in ([], None, [{}] * 21):
            with self.subTest(pins=type(pins)), self.assertRaises(PasskeyError):
                ap.verify(pins, self.make(), self.want)

    def test_ed25519_pin_and_bad_jwks(self):
        sk = ed25519.Ed25519PrivateKey.generate()
        x = sk.public_key().public_bytes_raw()
        pins = [{"id": wire.b64u(b"ed"), "alg": -8, "jwk": {"kty": "OKP", "crv": "Ed25519", "x": wire.b64u(x)}}]
        cd = json.dumps({"type": "webauthn.get", "challenge": wire.b64u(self.want), "origin": ap.ACCOUNT_ORIGIN}).encode()
        ad = hashlib.sha256(b"agentj.app").digest() + b"\x05" + b"\0\0\0\0"
        a = {"id": wire.b64u(b"ed"), "cd": wire.b64u(cd), "ad": wire.b64u(ad), "sig": wire.b64u(sk.sign(ad + hashlib.sha256(cd).digest()))}
        self.assertEqual(ap.verify(pins, a, self.want), wire.b64u(b"ed"))
        for jwk, alg in (({"kty": "EC", "crv": "P-384", "x": "AA", "y": "AA"}, -7), ({"kty": "OKP", "crv": "Ed25519"}, -8), (jwk_ec(self.sk), -8)):
            with self.subTest(jwk=jwk.get("crv"), alg=alg), self.assertRaises(PasskeyError):
                ap.jwk_spki(jwk, alg)


if __name__ == "__main__":
    unittest.main()
