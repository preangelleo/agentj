"""P190 (PROTOCOL §27): reverse pairing — a paired remote scans the code a NEW device shows and adds it.

Host side only adds to §24: `dev_add` may carry `rv` (the scanned code's rendezvous channel), signed as "rv\\n<channel>"
(JS and Python sign the same bytes; an older host signs "new" and so refuses it with bad_signature, which the phone turns
into 「请升级」). A reverse pairing answers the link without a QR, and its `dev_pending` carries the new device's safety code
(`sas`) so both screens show the same 6 digits; `dev_confirm` is the same signed, one-try check. A forged / malformed `rv`
is refused before any pairing exists; the cap still evicts exactly as at the terminal; logs never hold the link, the
channel only as a short hash; the notice says 扫码.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import activity, controls, wire  # noqa: E402
from agentj.state import MAX_DEVICES  # noqa: E402

from test_controls import sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402
from test_p171_remotes import Serve as P171  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RV = "AbCdEfGhIjKlMnOpQrStUv"


class Signatures(unittest.TestCase):
    def test_object_text(self):
        self.assertEqual(controls.object_text("dev_add", {}), "new")
        self.assertEqual(controls.object_text("dev_add", {"rv": RV}), "rv\n" + RV)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_sign_the_same_bytes(self):
        cases = [("dev_add", {}), ("dev_add", {"rv": RV})]
        for wire_js in (HERE.parents[1] / "protocol" / "wire.js", HERE.parents[1] / "web" / "public" / "proto" / "wire.js"):
            js = (f"import {{ controlMessage }} from {json.dumps(wire_js.as_uri())};\n"
                  f"const out = [];\nfor (const [a, o] of {json.dumps(cases)}) out.push(Buffer.from(await "
                  f"controlMessage('CH', 'DEV', a, '{'c' * 32}', 1790000000123, o)).toString('hex'));\nconsole.log(JSON.stringify(out));")
            r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = [controls.signed_message("CH", "DEV", a, "c" * 32, 1790000000123, controls.object_digest(a, o)).hex() for a, o in cases]
            self.assertEqual(json.loads(r.stdout), want, wire_js)


class Serve(unittest.TestCase):
    got = P171.got
    res = P171.res
    pending = staticmethod(P171.pending)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def setup_host(self, n_other=1):
        ph = Phone(self.st, "iPhone")
        others = [Phone(self.st, f"别的{i}") for i in range(n_other)]
        host = _host(self.st, self.sent)
        host.relay_up = True
        s = _ready(host, ph)
        s.p33 = True
        return ph, others, host, s

    def test_reverse_add_shows_the_code_on_the_remote_then_confirms(self):
        ph, _, host, s = self.setup_host()
        tgt = {"rv": RV}

        async def go():
            await host._app(s, sign(ph, "dev_add", tgt, {"t": "dev_add", "r": "a", **tgt}))
            m = self.res("a")
            self.assertTrue(m["ok"], m)
            self.assertTrue(m["rv"])
            self.assertIn("#p=", m["link"])
            self.assertNotIn("qr", m, "the remote seals the link itself: no QR to draw")
            p = host.pairing
            self.assertEqual((p.phone, p.rv), (ph.did, RV))
            pend = self.pending(host, p, cid=31, name="MacBook · Chrome")
            await host.remotes.pending(p, pend)
            ev = self.got("dev_pending")[-1]
            self.assertEqual(ev["rv"], RV)
            self.assertEqual(ev["sas"], wire.safety_code(pend.h), "both screens show the same 6 digits")
            t = {"device": pend.device, "code": ev["sas"]}
            await host._app(s, sign(ph, "dev_confirm", t, {"t": "dev_confirm", "r": "c", **t}))
            r = self.res("c")
            self.assertTrue(r["ok"], r)
            return pend
        pend = asyncio.run(go())
        rec = self.st.devices()[pend.device]
        self.assertEqual((rec["name"], rec["source"]), ("MacBook · Chrome", "phone:" + ph.did))
        self.assertIsNone(host.pairing)
        self.assertTrue(any("扫码添加遥控器" in json.dumps(o, ensure_ascii=False) for _, o in self.sent), "the notice says 扫码")
        self.assertIn("扫码添加遥控器", [r.get("tool") for r in activity.all_since(self.st) if r["k"] == "decision"])
        log = self.st.log_path.read_text()
        self.assertIn(hashlib.sha256(RV.encode()).hexdigest()[:16], log)
        self.assertNotIn(RV, log, "host.log has the channel as a short hash only")
        for f in (self.st.log_path, self.st.controls_path):
            self.assertNotIn("#p=", f.read_text(), "the pairing link is never logged")

    def test_forward_add_never_reveals_the_code(self):
        ph, _, host, s = self.setup_host()

        async def go():
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "a"}))
            p = host.pairing
            self.assertIsNone(p.rv)
            pend = self.pending(host, p, cid=33)
            await host.remotes.pending(p, pend)
        asyncio.run(go())
        ev = self.got("dev_pending")[-1]
        self.assertNotIn("sas", ev)
        self.assertNotIn("rv", ev)

    def test_forged_or_resigned_rv_is_refused(self):
        ph, (other,), host, s = self.setup_host()

        async def go():
            for r, rv in (("bad1", "x"), ("bad2", "A" * 23), ("bad3", 5), ("bad4", "AAAAAAAAAAAAAAAAAAAA/=")):
                await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": r, "rv": rv}))
            # signed as a plain add, sent as a reverse one (or the other way round): the signature does not cover it
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "swap", "rv": RV}))
            await host._app(s, sign(ph, "dev_add", {"rv": RV}, {"t": "dev_add", "r": "swap2"}))
            # another remote's key
            await host._app(s, sign(ph, "dev_add", {"rv": RV}, {"t": "dev_add", "r": "k", "rv": RV}, key=other.sk))
            # an unsigned one
            await host._app(s, {"t": "dev_add", "r": "u", "rv": RV})
        asyncio.run(go())
        self.assertEqual([self.res(r)["why"] for r in ("bad1", "bad2", "bad3", "bad4")], ["shape"] * 4)
        self.assertEqual([self.res(r)["why"] for r in ("swap", "swap2", "k")], ["bad_signature"] * 3)
        self.assertEqual(self.res("u")["why"], "shape")
        self.assertIsNone(host.pairing, "nothing was started")

    def test_replayed_request_is_refused(self):
        ph, _, host, s = self.setup_host()
        msg = sign(ph, "dev_add", {"rv": RV}, {"t": "dev_add", "r": "one", "rv": RV})

        async def go():
            await host._app(s, msg)
            await host._app(s, {**msg, "r": "two"})
        asyncio.run(go())
        self.assertTrue(self.res("one")["ok"])
        self.assertEqual(self.res("two")["why"], "replay")

    def test_full_list_evicts_like_the_terminal(self):
        ph, others, host, s = self.setup_host(MAX_DEVICES - 1)

        async def go():
            await host._app(s, sign(ph, "dev_add", {"rv": RV}, {"t": "dev_add", "r": "a", "rv": RV}))
            self.assertTrue(self.res("a")["full"])
            p = host.pairing
            pend = self.pending(host, p, cid=40)
            await host.remotes.pending(p, pend)
            ev = self.got("dev_pending")[-1]
            self.assertIn("evict", ev)
            t = {"device": pend.device, "code": ev["sas"]}
            await host._app(s, sign(ph, "dev_confirm", t, {"t": "dev_confirm", "r": "c", **t}))
        asyncio.run(go())
        r = self.res("c")
        self.assertTrue(r["ok"])
        self.assertIn(r["evicted"]["id"], [o.did for o in others])
        self.assertEqual(len(self.st.devices()), MAX_DEVICES)

    def test_wrong_code_still_ends_it(self):
        ph, _, host, s = self.setup_host()

        async def go():
            await host._app(s, sign(ph, "dev_add", {"rv": RV}, {"t": "dev_add", "r": "a", "rv": RV}))
            p = host.pairing
            pend = self.pending(host, p, cid=41)
            await host.remotes.pending(p, pend)
            sas = self.got("dev_pending")[-1]["sas"]
            wrong = {"device": pend.device, "code": "000000" if sas != "000000" else "111111"}
            await host._app(s, sign(ph, "dev_confirm", wrong, {"t": "dev_confirm", "r": "w", **wrong}))
            return pend
        pend = asyncio.run(go())
        self.assertEqual(self.res("w")["why"], "code_mismatch")
        self.assertIsNone(host.pairing)
        self.assertNotIn(pend.device, self.st.devices())


if __name__ == "__main__":
    unittest.main()
