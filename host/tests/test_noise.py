"""Host Noise + wire: official vectors, noiseprotocol as an independent oracle, negative cases.
Run: host/.venv/bin/python -m unittest discover -s host/tests
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import os
import pathlib
import sys
import unittest
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import wire  # noqa: E402
from agentj.noise import IK, IKPSK2, Handshake, Keypair, NoiseError  # noqa: E402

VECTORS = json.loads((pathlib.Path(__file__).resolve().parents[2] / "protocol/vectors/noise-ik.json").read_text())["vectors"]
H = bytes.fromhex


def party(v, side):
    p = "init" if side == "init" else "resp"
    return Handshake(v["protocol_name"], side == "init", Keypair.from_private(H(v[f"{p}_static"])),
                     prologue=H(v[f"{p}_prologue"]), rs=H(v["init_remote_static"]) if side == "init" else None,
                     psk=H(v[f"{p}_psks"][0]) if v.get(f"{p}_psks") else None,
                     e=Keypair.from_private(H(v[f"{p}_ephemeral"])))


class Vectors(unittest.TestCase):
    def test_official_vectors(self):
        self.assertEqual(len(VECTORS), 3)
        for v in VECTORS:
            with self.subTest(src=v["_source"], name=v["protocol_name"]):
                i, r = party(v, "init"), party(v, "resp")
                it = rt = None
                for n, m in enumerate(v["messages"]):
                    from_i = n % 2 == 0
                    if n < 2:
                        ct = (i if from_i else r).write_message(H(m["payload"]))
                        self.assertEqual(ct.hex(), m["ciphertext"])
                        pt = (r if from_i else i).read_message(ct)
                        if n == 1:
                            it, rt = i.split(), r.split()
                            if v.get("handshake_hash"):
                                self.assertEqual(it[2].hex(), v["handshake_hash"])
                                self.assertEqual(rt[2].hex(), v["handshake_hash"])
                    else:
                        snd, rcv = (it[0], rt[1]) if from_i else (rt[0], it[1])
                        ct = snd.encrypt(b"", H(m["payload"]))
                        self.assertEqual(ct.hex(), m["ciphertext"])
                        pt = rcv.decrypt(b"", ct)
                    self.assertEqual(pt.hex(), m["payload"])


class Oracle(unittest.TestCase):
    """Random keys: our initiator ↔ noiseprotocol responder and vice versa."""

    def _theirs(self, name, initiator, s, rs=None, psk=None, prologue=b""):
        from noise.connection import Keypair as K, NoiseConnection
        c = NoiseConnection.from_name(name.encode())
        c.set_prologue(prologue)
        if psk:
            c.set_psks(psk=psk)
        c.set_keypair_from_private_bytes(K.STATIC, s.private_bytes())
        if rs:
            c.set_keypair_from_public_bytes(K.REMOTE_STATIC, rs)
        c.set_as_initiator() if initiator else c.set_as_responder()
        c.start_handshake()
        return c

    def test_interop_both_directions(self):
        for name in (IK, IKPSK2):
            for ours_init in (True, False):
                with self.subTest(name=name, ours_init=ours_init), warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    a, b = Keypair.generate(), Keypair.generate()
                    psk = os.urandom(32) if name == IKPSK2 else None
                    pro = b"agentjarvis-test"
                    if ours_init:
                        me = Handshake(name, True, a, prologue=pro, rs=b.pub, psk=psk)
                        th = self._theirs(name, False, b, psk=psk, prologue=pro)
                        self.assertEqual(th.read_message(me.write_message(b"hi")), b"hi")
                        self.assertEqual(me.read_message(th.write_message(b"yo")), b"yo")
                        snd, rcv, h = me.split()
                        self.assertEqual(th.decrypt(snd.encrypt(b"", b"x" * 300)), b"x" * 300)
                        self.assertEqual(rcv.decrypt(b"", th.encrypt(b"back")), b"back")
                    else:
                        th = self._theirs(name, True, a, rs=b.pub, psk=psk, prologue=pro)
                        me = Handshake(name, False, b, prologue=pro, psk=psk)
                        self.assertEqual(me.read_message(th.write_message(b"hi")), b"hi")
                        self.assertEqual(me.rs, a.pub)
                        self.assertEqual(th.read_message(me.write_message(b"yo")), b"yo")
                        snd, rcv, h = me.split()
                        self.assertEqual(th.decrypt(snd.encrypt(b"", b"z")), b"z")
                    self.assertEqual(h, th.get_handshake_hash())


class Negative(unittest.TestCase):
    def _pair(self, psk_r=None, same=False):
        host, dev = Keypair.generate(), Keypair.generate()
        psk = os.urandom(32)
        i = Handshake(IKPSK2, True, dev, prologue=b"p", rs=host.pub, psk=psk)
        r = Handshake(IKPSK2, False, host, prologue=b"p", psk=psk_r or psk)
        if same:  # a second responder with the same identity + PSK
            return i, r, Handshake(IKPSK2, False, host, prologue=b"p", psk=psk_r or psk)
        return i, r

    def test_wrong_psk(self):
        i, r = self._pair(psk_r=os.urandom(32))
        r.read_message(i.write_message())
        with self.assertRaises(NoiseError):
            i.read_message(r.write_message())

    def test_tamper_and_replay(self):
        i, r, same = self._pair(same=True)
        good = i.write_message(b"x")
        m1 = bytearray(good)
        m1[40] ^= 1
        with self.assertRaises(NoiseError):  # same host key + PSK: rejected only because of the flipped bit
            same.read_message(bytes(m1))
        self.assertEqual(r.read_message(good), b"x")
        i, r = self._pair()
        r.read_message(i.write_message())
        i.read_message(r.write_message())
        (s, _, h1), (_, rv, h2) = i.split(), r.split()
        ct = s.encrypt(b"", b"abc")
        self.assertEqual(rv.decrypt(b"", ct), b"abc")
        with self.assertRaises(NoiseError):
            rv.decrypt(b"", ct)
        self.assertEqual(wire.safety_code(h1), wire.safety_code(h2))

    def test_initiator_without_responder_key(self):
        with self.assertRaises(NoiseError):
            Handshake(IK, True, Keypair.generate(), prologue=b"p", rs=None)

    def test_wrong_prologue(self):
        host, dev = Keypair.generate(), Keypair.generate()
        i = Handshake(IK, True, dev, prologue=b"a", rs=host.pub)
        r = Handshake(IK, False, host, prologue=b"b")
        with self.assertRaises(NoiseError):
            r.read_message(i.write_message())


class Wire(unittest.TestCase):
    def test_pad(self):
        for s in ["", "x", "x" * 253, "x" * 254, "中" * 500]:
            p = wire.pad_json({"t": "msg", "text": s})
            self.assertEqual(len(p) % 256, 0)
            self.assertEqual(wire.unpad_json(p)["text"], s)
        p = bytearray(wire.pad_json({"t": "msg"}))
        p[-1] = 1
        with self.assertRaises(ValueError):
            wire.unpad_json(bytes(p))
        # receive side enforces the 16 KiB JSON limit (A2 review A2-06)
        j = json.dumps({"t": "msg", "text": "x" * 16 * 1024}).encode()
        big = (len(j).to_bytes(2, "big") + j).ljust(-(-(len(j) + 2) // 256) * 256, b"\x00")
        with self.assertRaises(ValueError):
            wire.unpad_json(big)


if __name__ == "__main__":
    unittest.main()
