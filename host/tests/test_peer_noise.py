"""Noise XX / KK for agent friends (PROTOCOL §17.4): official cacophony vectors, agentj vectors (prologues + the pinned
handshake hash a §17.4 signature binds), noiseprotocol as an independent oracle both ways, negative cases, and IK / IKpsk2
untouched (tests/test_noise.py keeps running their vectors).
Run (in host/): .venv/bin/python -m unittest discover -s tests -p test_peer_noise.py
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import pathlib
import sys
import unittest
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj.noise import IK, KK, XX, Handshake, Keypair, NoiseError  # noqa: E402

VECTORS = json.loads((pathlib.Path(__file__).resolve().parents[2]
                      / "protocol/vectors/noise-xx-kk.json").read_text())["vectors"]
H = bytes.fromhex


def party(v, side):
    p = "init" if side == "init" else "resp"
    rs = v.get(f"{p}_remote_static")
    return Handshake(v["protocol_name"], side == "init", Keypair.from_private(H(v[f"{p}_static"])),
                     prologue=H(v[f"{p}_prologue"]), rs=H(rs) if rs else None,
                     e=Keypair.from_private(H(v[f"{p}_ephemeral"])))


def theirs(name, initiator, s, rs=None, prologue=b"", e=None):
    from noise.connection import Keypair as K, NoiseConnection
    c = NoiseConnection.from_name(name.encode())
    c.set_prologue(prologue)
    c.set_keypair_from_private_bytes(K.STATIC, s.private_bytes())
    if rs:
        c.set_keypair_from_public_bytes(K.REMOTE_STATIC, rs)
    if e:
        c.set_keypair_from_private_bytes(K.EPHEMERAL, e.private_bytes())
    c.set_as_initiator() if initiator else c.set_as_responder()
    c.start_handshake()
    return c


class Vectors(unittest.TestCase):
    def test_vectors(self):
        self.assertEqual(sorted((v["_source"].split()[0], v["protocol_name"]) for v in VECTORS),
                         [("agentj", KK), ("agentj", XX), ("cacophony", KK), ("cacophony", XX)])
        for v in VECTORS:
            with self.subTest(src=v["_source"][:9], name=v["protocol_name"]):
                i, r = party(v, "init"), party(v, "resp")
                n_hs = len(i.msgs)
                it = rt = None
                for n, m in enumerate(v["messages"]):
                    from_i = n % 2 == 0
                    if n < n_hs:
                        w, rd = (i, r) if from_i else (r, i)
                        ct = w.write_message(H(m["payload"]))
                        self.assertEqual(ct.hex(), m["ciphertext"])
                        pt = rd.read_message(ct)
                        self.assertEqual(w.h_before_payload, rd.h_before_payload)  # both sides sign / verify the same h
                        if "h_before_payload" in m:
                            self.assertEqual(w.h_before_payload.hex(), m["h_before_payload"])
                        if n == n_hs - 1:
                            it, rt = i.split(), r.split()
                            self.assertEqual(it[2].hex(), v["handshake_hash"])
                            self.assertEqual(rt[2].hex(), v["handshake_hash"])
                    else:
                        snd, rcv = (it[0], rt[1]) if from_i else (rt[0], it[1])
                        ct = snd.encrypt(b"", H(m["payload"]))
                        self.assertEqual(ct.hex(), m["ciphertext"])
                        pt = rcv.decrypt(b"", ct)
                    self.assertEqual(pt.hex(), m["payload"])

    def test_agentj_vectors_against_oracle(self):
        """The generated agentj entries are reproduced byte for byte by noiseprotocol (fixed ephemerals)."""
        for v in VECTORS:
            if not v["_source"].startswith("agentj"):
                continue
            with self.subTest(name=v["protocol_name"]), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                kk = v["protocol_name"] == KK
                i = theirs(v["protocol_name"], True, Keypair.from_private(H(v["init_static"])),
                           rs=H(v["init_remote_static"]) if kk else None, prologue=H(v["init_prologue"]),
                           e=Keypair.from_private(H(v["init_ephemeral"])))
                r = theirs(v["protocol_name"], False, Keypair.from_private(H(v["resp_static"])),
                           rs=H(v["resp_remote_static"]) if kk else None, prologue=H(v["resp_prologue"]),
                           e=Keypair.from_private(H(v["resp_ephemeral"])))
                n_hs = 3 if not kk else 2
                for n, m in enumerate(v["messages"]):
                    w, rd = (i, r) if n % 2 == 0 else (r, i)
                    ct = w.write_message(H(m["payload"])) if n < n_hs else w.encrypt(H(m["payload"]))
                    self.assertEqual(ct.hex(), m["ciphertext"])
                    self.assertEqual((rd.read_message(ct) if n < n_hs else rd.decrypt(ct)).hex(), m["payload"])
                self.assertEqual(i.get_handshake_hash().hex(), v["handshake_hash"])


class Oracle(unittest.TestCase):
    """Random keys: our side ↔ noiseprotocol, both roles, both patterns."""

    def test_interop_both_directions(self):
        for name in (XX, KK):
            for ours_init in (True, False):
                with self.subTest(name=name, ours_init=ours_init), warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    a, b = Keypair.generate(), Keypair.generate()
                    pro = b"agentj/v1/peer-test"
                    kk = name == KK
                    if ours_init:
                        me = Handshake(name, True, a, prologue=pro, rs=b.pub if kk else None)
                        th = theirs(name, False, b, rs=a.pub if kk else None, prologue=pro)
                    else:
                        th = theirs(name, True, a, rs=b.pub if kk else None, prologue=pro)
                        me = Handshake(name, False, b, prologue=pro, rs=a.pub if kk else None)
                    for n in range(len(me.msgs)):
                        p = f"m{n}".encode()
                        if (n % 2 == 0) == ours_init:
                            self.assertEqual(th.read_message(me.write_message(p)), p)
                        else:
                            self.assertEqual(me.read_message(th.write_message(p)), p)
                    self.assertEqual(me.rs, b.pub if ours_init else a.pub)  # XX: learned in the handshake
                    snd, rcv, h = me.split()
                    self.assertEqual(h, th.get_handshake_hash())
                    self.assertEqual(th.decrypt(snd.encrypt(b"", b"x" * 300)), b"x" * 300)
                    self.assertEqual(rcv.decrypt(b"", th.encrypt(b"back")), b"back")


class Negative(unittest.TestCase):
    def test_static_key_requirements(self):
        k = Keypair.generate()
        with self.assertRaises(NoiseError):
            Handshake(KK, True, k, rs=None)
        with self.assertRaises(NoiseError):
            Handshake(KK, False, k, rs=None)  # KK: the responder needs the initiator's static too
        with self.assertRaises(NoiseError):
            Handshake(IK, True, k, rs=None)
        Handshake(IK, False, k)
        Handshake(XX, True, k)
        Handshake(XX, False, k)

    def test_kk_from_a_stranger_does_not_decrypt(self):
        """§17.4: a KK msg1 from a static that is not a friend does not decrypt (the ss token)."""
        me, friend, stranger = Keypair.generate(), Keypair.generate(), Keypair.generate()
        i = Handshake(KK, True, stranger, prologue=b"p", rs=me.pub)
        r = Handshake(KK, False, me, prologue=b"p", rs=friend.pub)
        with self.assertRaises(NoiseError):
            r.read_message(i.write_message(b""))

    def test_payload_fn_signs_h_before_payload(self):
        a, b = Keypair.generate(), Keypair.generate()
        i, r = Handshake(XX, True, a, prologue=b"x"), Handshake(XX, False, b, prologue=b"x")
        r.read_message(i.write_message())
        seen = []
        m2 = r.write_message(lambda h: (seen.append(h), h)[1])
        self.assertEqual(i.read_message(m2), seen[0])
        self.assertEqual(i.h_before_payload, seen[0])
        self.assertNotEqual(i.h, seen[0])  # h moved on after the payload

    def test_xx_tamper(self):
        a, b = Keypair.generate(), Keypair.generate()
        i, r = Handshake(XX, True, a, prologue=b"x"), Handshake(XX, False, b, prologue=b"x")
        r.read_message(i.write_message())
        m2 = bytearray(r.write_message(b"hi"))
        m2[40] ^= 1
        with self.assertRaises(NoiseError):
            i.read_message(bytes(m2))

    def test_wrong_prologue(self):
        a, b = Keypair.generate(), Keypair.generate()
        i, r = Handshake(KK, True, a, prologue=b"a", rs=b.pub), Handshake(KK, False, b, prologue=b"b", rs=a.pub)
        with self.assertRaises(NoiseError):
            r.read_message(i.write_message())


if __name__ == "__main__":
    unittest.main()
