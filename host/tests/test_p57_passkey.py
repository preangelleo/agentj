"""P57 / F20 (0.15.2, PROTOCOL §12): a passkey proves "the same already-approved phone" — a fresh browser context (Home
Screen app, private tab, cleared data) reconnects with Face ID instead of a second pairing, taking its own record's slot.

Unit: the verifier with real keys made here (P-256, Ed25519, RSA) — every check that must refuse. Host level (the P55
harness: send_app / _op captured): pk_offer → pk_reg on an approved session; a restore replaces the SAME record (count
stays 1, the old id is told `replaced`, its sessions close, the new key resumes normally afterwards); revoke → refused;
an assertion replayed from another session key → refused; the rate limit."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa  # noqa: E402

from agentj import gate, passkey, serve, wire  # noqa: E402
from agentj.noise import IK, Handshake, Keypair  # noqa: E402
from agentj.state import State  # noqa: E402

PASS = "test-passphrase-P57"
ORIGIN = "https://m.agentj.app"
RP = "m.agentj.app"
# Shared with the web test p57_passkey.test.mjs: the page and the host must derive the same restore challenge
VEC_X, VEC_ED, VEC_TS, VEC_NONCE = bytes(range(32)), bytes(range(32, 64)), 1790000000000, bytes(range(64, 80))
VEC_CHALLENGE = "e6f34fdfcd8009b4fa0f382e8295bd9153b5f470f0d1143d7dc9f1460c5f640a"
VEC_CHANNEL, VEC_HOSTPUB = "AAECAwQFBgcICQoLDA0ODw", bytes(range(100, 132))
VEC_HANDLE = "0102000102030405060708090a0b0c0d0e0f6465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f80818283"


class Authenticator:
    """A platform authenticator stand-in: one credential, the WebAuthn byte layouts a browser produces."""
    def __init__(self, alg=passkey.ALG_ES256, count=0):
        self.alg, self.count = alg, count
        self.key = {passkey.ALG_ES256: lambda: ec.generate_private_key(ec.SECP256R1()),
                    passkey.ALG_EDDSA: ed25519.Ed25519PrivateKey.generate,
                    passkey.ALG_RS256: lambda: rsa.generate_private_key(65537, 2048)}[alg]()
        self.id = os.urandom(32)

    def spki(self) -> bytes:
        return self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    @staticmethod
    def cd(typ, challenge, origin=ORIGIN, **extra) -> bytes:
        return json.dumps({"type": typ, "challenge": wire.b64u(challenge), "origin": origin, "crossOrigin": False, **extra}).encode()

    def register(self, nonce, origin=ORIGIN) -> dict:
        return {"t": "pk_reg", "id": wire.b64u(self.id), "alg": self.alg, "spki": wire.b64u(self.spki()),
                "cd": wire.b64u(self.cd("webauthn.create", nonce, origin))}

    def sign(self, data: bytes) -> bytes:
        if self.alg == passkey.ALG_ES256:
            return self.key.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == passkey.ALG_EDDSA:
            return self.key.sign(data)
        return self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    def assert_(self, challenge, uh, *, origin=ORIGIN, typ="webauthn.get", rp=RP, flags=0x05, ts=None, nonce=None,
                count=None) -> dict:
        if count is None:
            if self.count:
                self.count += 1
            count = self.count
        cd = self.cd(typ, challenge, origin)
        ad = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
        sig = self.sign(ad + hashlib.sha256(cd).digest())
        return {"t": "pk_restore", "id": wire.b64u(self.id), "cd": wire.b64u(cd), "ad": wire.b64u(ad), "sig": wire.b64u(sig),
                "uh": wire.b64u(uh), "ts": ts, "nonce": wire.b64u(nonce)}


def pk_record(a: Authenticator, rp=RP, **kw) -> dict:
    return {"id": wire.b64u(a.id), "alg": a.alg, "spki": wire.b64u(a.spki()), "rp": rp, "at": 1, **kw}


class Vectors(unittest.TestCase):
    def test_restore_challenge_vector(self):
        self.assertEqual(passkey.restore_challenge(VEC_X, VEC_ED, VEC_TS, VEC_NONCE).hex(), VEC_CHALLENGE)

    def test_user_handle_layout(self):
        h = passkey.user_handle(2, VEC_CHANNEL, VEC_HOSTPUB)
        self.assertEqual((h.hex(), len(h)), (VEC_HANDLE, 50))
        self.assertTrue(passkey.handle_matches(h, VEC_CHANNEL, VEC_HOSTPUB))
        self.assertTrue(passkey.handle_matches(passkey.user_handle(0, VEC_CHANNEL, VEC_HOSTPUB), VEC_CHANNEL, VEC_HOSTPUB),
                        "the relay index is the phone's business")
        self.assertFalse(passkey.handle_matches(b"\x02" + h[1:], VEC_CHANNEL, VEC_HOSTPUB), "version ≠ 1")
        self.assertFalse(passkey.handle_matches(h[:-1] + b"\x00", VEC_CHANNEL, VEC_HOSTPUB), "another computer")
        self.assertFalse(passkey.handle_matches(h + b"\x00", VEC_CHANNEL, VEC_HOSTPUB))


class Verifier(unittest.TestCase):
    """verify_assertion / verify_registration with real keys of all three algorithms."""
    def setUp(self):
        self.x, self.ed = os.urandom(32), os.urandom(32)
        self.ts, self.nonce = int(time.time() * 1000), os.urandom(16)
        self.ch = passkey.restore_challenge(self.x, self.ed, self.ts, self.nonce)
        self.uh = passkey.user_handle(0, VEC_CHANNEL, VEC_HOSTPUB)

    def check(self, a, pk=None, **kw):
        obj = a.assert_(kw.pop("challenge", self.ch), self.uh, ts=self.ts, nonce=self.nonce, **kw)
        return passkey.verify_assertion(pk or pk_record(a), passkey.restore_fields(obj), self.ch)

    def refused(self, detail, a, pk=None, **kw):
        with self.assertRaises(passkey.PasskeyError) as cm:
            self.check(a, pk, **kw)
        self.assertEqual((cm.exception.why, cm.exception.detail), ("bad", detail))

    def test_good_assertion_every_alg(self):
        for alg in passkey.ALGS:
            with self.subTest(alg=alg):
                self.assertEqual(self.check(Authenticator(alg)), 0)

    def test_wrong_origin_type_rp_flags_sig(self):
        a = Authenticator()
        self.refused("cd_origin", a, origin="https://evil.example")
        self.refused("cd_origin", a, origin="http://127.0.0.1:8080")   # loopback only under the test switch
        self.refused("cd_type", a, typ="webauthn.create")
        self.refused("rp_hash", a, rp="evil.example")
        self.refused("flags", a, flags=0x01)       # UP without UV
        self.refused("flags", a, flags=0x04)       # UV without UP
        self.refused("cd_challenge", a, challenge=os.urandom(32))
        other = Authenticator()
        self.refused("signature", a, pk=pk_record(other) | {"id": wire.b64u(a.id)})   # signed by another key
        self.refused("spki_alg", a, pk=pk_record(a) | {"alg": passkey.ALG_EDDSA})       # stored alg ≠ key

    def test_loopback_origin_only_with_the_test_switch(self):
        a = Authenticator()
        with mock.patch.dict(os.environ, {passkey.TEST_ENV: "loopback"}):
            self.assertEqual(self.check(a, pk=pk_record(a, rp="localhost"), origin="http://localhost:5173", rp="localhost"), 0)
            self.assertFalse(passkey.origin_allowed("https://localhost:5173"))
            self.assertFalse(passkey.origin_allowed("http://localhost.evil.example"))
            self.assertFalse(passkey.origin_allowed("http://127.0.0.1:1/x"))
        self.assertFalse(passkey.origin_allowed("http://localhost:5173"))

    def test_sign_count(self):
        a = Authenticator(count=5)
        n = self.check(a, pk=pk_record(a, sc=5))
        self.assertEqual(n, 6)
        self.refused("sign_count", a, pk=pk_record(a, sc=9), count=7)       # went backwards: a cloned authenticator
        self.assertEqual(self.check(a, pk=pk_record(a, sc=9), count=0), 0)  # a synced passkey says 0: not tracked

    def test_shape(self):
        a = Authenticator()
        obj = a.assert_(self.ch, self.uh, ts=self.ts, nonce=self.nonce)
        for k, v in (("ts", "1"), ("ts", True), ("nonce", wire.b64u(b"x" * 15)), ("id", ""), ("sig", "!!"),
                     ("id", wire.b64u(b"x" * 1024))):
            with self.subTest(k=k, v=v), self.assertRaises(passkey.PasskeyError):
                passkey.restore_fields({**obj, k: v})

    def test_registration(self):
        for alg in passkey.ALGS:
            a, n = Authenticator(alg), os.urandom(32)
            rec = passkey.verify_registration(a.register(n), n)
            self.assertEqual((rec["id"], rec["alg"], rec["rp"]), (wire.b64u(a.id), alg, RP))
        a, n = Authenticator(), os.urandom(32)
        bad = [(a.register(os.urandom(32)), "cd_challenge"), (a.register(n, "https://evil.example"), "cd_origin"),
               ({**a.register(n), "alg": passkey.ALG_EDDSA}, "spki_alg"), ({**a.register(n), "alg": -35}, "alg"),
               ({**a.register(n), "spki": wire.b64u(b"\x30\x03junk")}, "spki"),
               ({**a.register(n), "cd": wire.b64u(Authenticator.cd("webauthn.get", n))}, "cd_type")]
        small = rsa.generate_private_key(65537, 1024).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        bad.append(({**a.register(n), "alg": passkey.ALG_RS256, "spki": wire.b64u(small)}, "spki_alg"))
        for obj, why in bad:
            with self.subTest(why=why), self.assertRaises(passkey.PasskeyError) as cm:
                passkey.verify_registration(obj, n)
            self.assertEqual(cm.exception.detail, why)

    def test_guard(self):
        t = [0.0]
        g = passkey.Guard(clock=lambda: t[0])
        self.assertTrue(all(g.attempt() for _ in range(passkey.RATE_N)))
        self.assertFalse(g.attempt())
        t[0] += passkey.RATE_WINDOW
        self.assertTrue(g.attempt())
        self.assertTrue(g.fresh(b"n" * 16))
        self.assertFalse(g.fresh(b"n" * 16), "replay")
        t[0] += passkey.NONCE_KEEP
        self.assertTrue(g.fresh(b"n" * 16))
        self.assertTrue(passkey.ts_ok(1000, 1000 + passkey.TS_WINDOW_MS))
        self.assertFalse(passkey.ts_ok(1000, 1001 + passkey.TS_WINDOW_MS))


class HostLevel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        gate.set_passphrase(self.st, PASS)
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.sent, self.ops = [], []

        async def send_app(s, obj):
            if self.host.sessions.get(s.cid) is not s:
                return False
            self.sent.append((s.cid, obj))
            return True

        async def op(o, cid, payload=b""):
            self.ops.append((o, cid, payload))

        async def on_ready(s, since):
            pass
        self.host.send_app, self.host._op, self.host.on_ready = send_app, op, on_ready
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return self.loop.run_until_complete(coro)

    def to(self, cid):
        return [o for c, o in self.sent if c == cid]

    # -------------------------------------------------------------- helpers
    def paired(self, auth=None):
        """Phone A, paired (an X25519 key + approval key on the allowlist), its passkey registered if auth is given."""
        kp, sk = Keypair.generate(), ed25519.Ed25519PrivateKey.generate()
        skpub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        did = self.st.pair_device(kp.pub, "网页 · iOS Safari", skpub, iid=wire.b64u(b"\x01" * 16))["device"]
        if auth:
            self.assertTrue(self.st.set_passkey(kp.pub, pk_record(auth)))
        return kp, skpub, did

    def handshake(self, cid, kp, payload: dict):
        hs = Handshake(IK, True, kp, prologue=wire.resume_prologue(self.host.channel), rs=self.host.kp.pub)
        s = serve.Session(cid=cid)
        self.host.sessions[cid] = s
        self.run_(self.host._resume_init(s, hs.write_message(json.dumps(payload).encode())))
        hs.read_message(self.ops[-1][2][1:])
        return s

    def restore(self, cid, auth, kp=None, *, sk=None, chal_kp=None, chal_sk=None, ts=None, nonce=None, **kw):
        """A fresh context: a new key kp + approval key, `pk:1` RESUME, hello, then one pk_restore."""
        kp = kp or Keypair.generate()
        sk = sk or os.urandom(32)
        s = self.handshake(cid, kp, {"v": 1, "sk": wire.b64u(sk), "pk": 1})
        self.assertEqual((s.mode, s.state), ("pk", "hello"))
        self.run_(self.host._app(s, {"t": "hello", "caps": ["p33"]}))
        self.assertEqual(self.to(cid), [], "nothing before the pk_restore: no ready, no removed")
        ts = int(time.time() * 1000) if ts is None else ts
        nonce = nonce or os.urandom(16)
        ch = passkey.restore_challenge((chal_kp or kp).pub, chal_sk or sk, ts, nonce)
        uh = kw.pop("uh", passkey.user_handle(0, self.host.channel, self.host.kp.pub))
        self.run_(self.host._app(s, auth.assert_(ch, uh, ts=ts, nonce=nonce, **kw)))
        return kp, sk, self.to(cid)

    def resume_plain(self, cid, kp):
        s = self.handshake(cid, kp, {"v": 1})
        self.run_(self.host._app(s, {"t": "hello", "caps": ["p33"]}))
        return self.to(cid)

    # -------------------------------------------------------------- registration
    def test_register_after_pairing_offer_then_pk_reg(self):
        kp, _, did = self.paired()
        s = serve.Session(cid=3, state="ready", device=did, pub=kp.pub, p33=True)
        self.host.sessions[3] = s
        self.run_(self.host._pk_offer(s))
        offer = self.to(3)[-1]
        self.assertEqual(offer["t"], "pk_offer")
        a = Authenticator()
        self.run_(self.host._app(s, a.register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(3)[-1], {"t": "pk_reg_res", "ok": True})
        pk = self.st.devices()[did]["pk"]
        self.assertEqual((pk["id"], pk["alg"], pk["rp"]), (wire.b64u(a.id), -7, RP))
        # the nonce is single use
        self.run_(self.host._app(s, a.register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(3)[-1], {"t": "pk_reg_res", "ok": False, "why": "expired"})
        # a later request for an offer (the Settings row) gets a fresh one; a wrong nonce / bad SPKI is refused
        self.run_(self.host._app(s, {"t": "pk_offer_req"}))
        n2 = wire.unb64u(self.to(3)[-1]["n"])
        self.run_(self.host._app(s, a.register(os.urandom(32))))
        self.assertEqual(self.to(3)[-1]["ok"], False)
        self.run_(self.host._app(s, {"t": "pk_offer_req"}))
        n3 = wire.unb64u(self.to(3)[-1]["n"])
        self.assertNotEqual(n2, n3)
        self.run_(self.host._app(s, {**a.register(n3), "spki": wire.b64u(b"not a key")}))
        self.assertEqual(self.to(3)[-1], {"t": "pk_reg_res", "ok": False, "why": "bad"})
        self.assertEqual(self.st.devices()[did]["pk"]["id"], wire.b64u(a.id), "the stored passkey is untouched")

    def test_offer_is_sent_after_approval_only_to_p33(self):
        pub = os.urandom(32)

        async def go(p33):
            s = serve.Session(cid=7, state="pending", device=wire.device_id(pub), name="phone", pub=pub, h=os.urandom(32), p33=p33)
            s.deadline = time.monotonic() + 60
            self.host.sessions[7] = s
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
            self.host.pairing = p
            return await self.host.decide(p, wire.safety_code(s.h), PASS)
        self.run_(go(True))
        self.assertEqual([o["t"] for o in self.to(7)], ["approved", "pk_offer"])
        self.sent.clear()
        self.st.remove_device(wire.device_id(pub))
        self.host.sessions.clear()
        self.run_(go(False))
        self.assertEqual([o["t"] for o in self.to(7)], ["approved"])

    # -------------------------------------------------------------- restore
    def test_restore_replaces_the_same_record(self):
        a = Authenticator()
        kpA, _, idA = self.paired(a)
        self.host.sessions[5] = serve.Session(cid=5, state="ready", device=idA, pub=kpA.pub, p33=True)   # A is online
        kpB, skB, got = self.restore(9, a)
        self.assertEqual([m["t"] for m in got], ["pk_ok", "ready"])
        devs = self.st.devices()
        idB = wire.device_id(kpB.pub)
        self.assertEqual(list(devs), [idB], "still exactly one remote")
        rec = devs[idB]
        self.assertEqual((rec["pub"], rec["sk"], rec["name"], rec["pk"]["id"]),
                         (wire.b64u(kpB.pub), wire.b64u(skB), "网页 · iOS Safari", wire.b64u(a.id)))
        self.assertNotIn("iid", rec, "the old browser's install hash goes")
        self.assertNotIn(5, self.host.sessions, "A's live session ended")
        self.assertIn((wire.OP_CLOSE, 5, b""), self.ops)
        self.assertEqual(self.st.removed_why(idA), "replaced")
        self.assertNotIn(wire.b64u(a.id), self.st.removed_path.read_text(), "no credential outside the device record")
        self.assertEqual((self.host.sessions[9].state, self.host.sessions[9].device), ("ready", idB))
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "pk_restore"', log)
        self.assertNotIn(wire.b64u(a.id), log, "host.log never holds the credential (or any assertion field)")
        # A comes back: told replaced; B resumes normally afterwards (plain RESUME, no passkey)
        self.sent.clear()
        self.assertEqual(self.resume_plain(11, kpA), [{"t": "removed", "why": "replaced"}])
        self.assertEqual(self.resume_plain(12, kpB)[0]["t"], "ready")
        # a third context C with the same Face ID credential: still one record
        kpC, _, got = self.restore(13, a)
        self.assertEqual([m["t"] for m in got], ["pk_ok", "ready"])
        self.assertEqual(list(self.st.devices()), [wire.device_id(kpC.pub)])
        self.assertNotIn(12, self.host.sessions, "B's session ended when C took the slot")

    def test_restore_after_revoke_is_refused(self):
        a = Authenticator()
        _, _, idA = self.paired(a)
        self.run_(self.host.revoke(idA))
        self.assertTrue(all(set(record) == {"why", "at"} for record in json.loads(self.st.removed_path.read_text()).values()),
                        "removed ledger contains metadata only; random device IDs may contain pk")
        kp, _, got = self.restore(9, a)
        self.assertEqual(got, [{"t": "pk_fail", "why": "unknown"}])
        self.assertNotIn(9, self.host.sessions)
        self.assertEqual(self.st.devices(), {})

    def test_restore_bound_to_the_session_key(self):
        """An assertion made for another key (stolen / relayed) is useless: the challenge covers THIS session's key."""
        a = Authenticator()
        _, _, idA = self.paired(a)
        _, _, got = self.restore(9, a, chal_kp=Keypair.generate())
        self.assertEqual(got, [{"t": "pk_fail", "why": "bad"}])
        _, _, got = self.restore(10, a, chal_sk=os.urandom(32))
        self.assertEqual(got, [{"t": "pk_fail", "why": "bad"}])
        self.assertEqual(list(self.st.devices()), [idA], "nothing changed")

    def test_restore_refusals(self):
        a = Authenticator()
        _, _, idA = self.paired(a)
        self.assertEqual(self.restore(9, a, ts=int(time.time() * 1000) - 6 * 60_000)[2], [{"t": "pk_fail", "why": "expired"}])
        n = os.urandom(16)
        self.assertEqual([m["t"] for m in self.restore(10, Authenticator(), nonce=n)[2]], ["pk_fail"])   # unknown credential
        self.assertEqual(self.restore(11, a, nonce=n)[2], [{"t": "pk_fail", "why": "bad"}], "nonce replay")
        other = passkey.user_handle(0, self.host.channel, os.urandom(32))
        self.assertEqual(self.restore(12, a, uh=other)[2], [{"t": "pk_fail", "why": "bad"}], "another computer's handle")
        self.assertEqual(self.restore(14, a, origin="https://evil.example")[2], [{"t": "pk_fail", "why": "bad"}])
        self.assertEqual(self.restore(15, a, flags=0x01)[2], [{"t": "pk_fail", "why": "bad"}], "no user verification")
        self.assertEqual(list(self.st.devices()), [idA])
        log = self.st.log_path.read_text()
        for r in ("ts", "credential", "replay", "user_handle", "cd_origin", "flags"):
            self.assertIn(f'"reason": "{r}"', log)

    def test_sign_count_regression_refused_at_host_level(self):
        a = Authenticator(count=10)
        _, _, idA = self.paired(a)
        d = self.st.devices()
        d[idA]["pk"]["sc"] = 10
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        self.assertEqual(self.restore(9, a, count=3)[2], [{"t": "pk_fail", "why": "bad"}])
        kp, _, got = self.restore(10, a, count=11)
        self.assertEqual(got[0], {"t": "pk_ok"})
        self.assertEqual(self.st.devices()[wire.device_id(kp.pub)]["pk"]["sc"], 11)

    def test_anything_but_pk_restore_ends_it(self):
        a = Authenticator()
        self.paired(a)
        s = self.handshake(9, Keypair.generate(), {"v": 1, "sk": wire.b64u(os.urandom(32)), "pk": 1})
        self.run_(self.host._app(s, {"t": "hello", "caps": ["p33"]}))
        self.run_(self.host._app(s, {"t": "msg", "text": "hi"}))
        self.assertEqual(self.to(9), [{"t": "pk_fail", "why": "bad"}])
        self.assertNotIn(9, self.host.sessions)

    def test_without_pk_flag_or_sk_an_unknown_key_is_removed_as_before(self):
        s = self.handshake(9, Keypair.generate(), {"v": 1, "pk": 1})        # no approval key → no restore
        self.assertEqual(s.mode, "gone")
        s = self.handshake(10, Keypair.generate(), {"v": 1, "sk": wire.b64u(os.urandom(32))})
        self.assertEqual(s.mode, "gone")

    def test_listed_key_with_pk_flag_just_resumes(self):
        a = Authenticator()
        kp, skpub, did = self.paired(a)
        s = self.handshake(9, kp, {"v": 1, "sk": wire.b64u(skpub), "pk": 1})
        self.run_(self.host._app(s, {"t": "hello", "caps": ["p33"]}))
        self.assertEqual(self.to(9)[0]["t"], "ready")

    def test_rate_limit(self):
        a = Authenticator()
        _, _, idA = self.paired(a)
        for i in range(passkey.RATE_N):
            self.restore(20 + i, a, ts=1)                               # stale: refused, but counted
        _, _, got = self.restore(40, a)
        self.assertEqual(got, [{"t": "pk_fail", "why": "bad"}], "the 11th attempt in a minute is refused even if valid")
        self.assertIn('"reason": "rate"', self.st.log_path.read_text())
        self.assertEqual(list(self.st.devices()), [idA])

    def test_timeout(self):
        a = Authenticator()
        self.paired(a)
        with mock.patch.object(serve, "HS_TTL", 0.01):
            s = self.handshake(9, Keypair.generate(), {"v": 1, "sk": wire.b64u(os.urandom(32)), "pk": 1})

            async def go():
                await self.host._app(s, {"t": "hello", "caps": ["p33"]})
                await asyncio.sleep(0.1)
            self.run_(go())
        self.assertEqual(self.to(9), [{"t": "pk_fail", "why": "expired"}])

    def test_revoke_and_eviction_take_the_passkey(self):
        a = Authenticator()
        kp, _, did = self.paired(a)
        self.assertEqual(self.st.passkey_of(wire.b64u(a.id))[0], did)
        self.st.remove_device(did)
        self.assertIsNone(self.st.passkey_of(wire.b64u(a.id)))
        # a re-pair of the same browser (iid) replaces the record — its passkey goes with it
        kp, _, did = self.paired(a)
        self.st.pair_device(os.urandom(32), "again", iid=wire.b64u(b"\x01" * 16))
        self.assertIsNone(self.st.passkey_of(wire.b64u(a.id)))


if __name__ == "__main__":
    unittest.main()
