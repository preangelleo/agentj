"""P171 (PROTOCOL §24, ADR-P171): the phone's 遥控器管理 page — list, rename, unbind and add remotes from a paired remote.

Signatures: the four new actions sign the same bytes in Python and JS. Serve: dev_list (metadata only, this device marked,
limit 10); dev_name / dev_unbind / dev_add / dev_confirm do nothing unsigned, wrongly signed or re-pointed; a rename changes
only the name; an unbind revokes exactly like `agentj revoke` (also of the asking remote itself, answered before it is
detached); an add makes a one-time pairing whose link + QR go only to the asking remote, the new device's code typed there
approves it (one try: a wrong code ends the pairing), `agentj remote-pair off` / a relay down / another pairing in progress
refuse it; a full list makes room exactly as at the terminal. Every write leaves controls.log (+ approvals.log for unbind /
add) and activity lines, adds and unbinds are a notice to every remote. Only p33 sessions are answered. MAX_DEVICES = 10.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import activity, controls, remote_pair, remotes, serve, wire  # noqa: E402
from agentj.state import MAX_DEVICES  # noqa: E402

from test_controls import sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


class Signatures(unittest.TestCase):
    def test_limit_is_ten(self):
        self.assertEqual(MAX_DEVICES, 10)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_sign_the_same_bytes(self):
        cases = [("dev_name", {"device": "Kx3pQ9wLm2Vb7Tz1", "name": "我的 Pixel"}), ("dev_unbind", {"device": "Kx3pQ9wLm2Vb7Tz1"}),
                 ("dev_add", {}), ("dev_confirm", {"device": "Kx3pQ9wLm2Vb7Tz1", "code": "012345"})]
        for wire_js in (HERE.parents[1] / "protocol" / "wire.js", HERE.parents[1] / "web" / "public" / "proto" / "wire.js"):
            js = (f"import {{ controlMessage }} from {json.dumps(wire_js.as_uri())};\n"
                  f"const out = [];\nfor (const [a, o] of {json.dumps(cases, ensure_ascii=False)}) out.push(Buffer.from(await "
                  f"controlMessage('CH', 'DEV', a, '{'b' * 32}', 1790000000123, o)).toString('hex'));\nconsole.log(JSON.stringify(out));")
            r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = [controls.signed_message("CH", "DEV", a, "b" * 32, 1790000000123, controls.object_digest(a, o)).hex() for a, o in cases]
            self.assertEqual(json.loads(r.stdout), want, wire_js)


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def got(self, t, r=None):
        return [o for _, o in self.sent if o["t"] == t and (r is None or o.get("r") == r)]

    def res(self, r):
        return self.got("ctl_res", r)[-1]

    def setup_host(self, n_other=1):
        ph = Phone(self.st, "iPhone")
        others = [Phone(self.st, f"别的{i}") for i in range(n_other)]
        host = _host(self.st, self.sent)
        host.relay_up = True
        s = _ready(host, ph)
        s.p33 = True
        return ph, others, host, s

    def controls_log(self):
        return [json.loads(x) for x in self.st.controls_path.read_text().splitlines()] if self.st.controls_path.exists() else []

    def test_list_is_metadata_only_and_marks_this_remote(self):
        ph, (other,), host, s = self.setup_host()
        old = serve.Session(cid=12, state="ready", device=ph.did, name="x", pub=ph.pub)    # an older page (no p33): no answer
        host.sessions[12] = old

        async def go():
            await host._app(s, {"t": "dev_list", "r": "l1"})
            await host._app(old, {"t": "dev_list", "r": "l2"})
        asyncio.run(go())
        m = self.got("devs", "l1")[0]
        self.assertEqual((m["limit"], m["can_add"], m["busy"]), (10, True, False))
        self.assertEqual({d["id"]: d["me"] for d in m["items"]}, {ph.did: True, other.did: False})
        self.assertEqual(set(m["items"][0]), {"id", "name", "paired_at", "seen", "online", "me", "source", "named_by", "self_name"})   # P165: + P179 name provenance
        self.assertNotIn("pub", json.dumps(m))
        self.assertEqual(self.got("devs", "l2"), [])
        self.assertEqual(host.host_info()["limit"], 10)

    def test_rename_signed_only(self):
        ph, (other,), host, s = self.setup_host()
        tgt = {"device": other.did, "name": "  Pixel 9 Pro  "}

        async def go():
            await host._app(s, {"t": "dev_name", "r": "u", **tgt})                                   # unsigned
            await host._app(s, sign(ph, "dev_name", tgt, {"t": "dev_name", "r": "k", **tgt}, key=other.sk))   # other key
            await host._app(s, sign(ph, "dev_name", {**tgt, "device": ph.did}, {"t": "dev_name", "r": "p", **tgt}))  # re-pointed
            await host._app(s, sign(ph, "dev_name", tgt, {"t": "dev_name", "r": "ok", **tgt}))
            bad = {"device": other.did, "name": "a\x07b"}
            await host._app(s, sign(ph, "dev_name", bad, {"t": "dev_name", "r": "bad", **bad}))
            gone = {"device": "A" * 16, "name": "x"}
            await host._app(s, sign(ph, "dev_name", gone, {"t": "dev_name", "r": "gone", **gone}))
        asyncio.run(go())
        self.assertEqual([self.res(r).get("why") for r in ("u", "k", "p")], ["shape", "bad_signature", "bad_signature"])
        self.assertTrue(self.res("ok")["ok"])
        self.assertEqual(self.res("bad")["why"], "bad_name")
        self.assertEqual(self.res("gone")["why"], "gone")
        devs = self.st.devices()
        self.assertEqual(devs[other.did]["name"], "Pixel 9 Pro")
        self.assertEqual(devs[ph.did]["name"], "iPhone")
        self.assertTrue(self.got("devs"), "every remote gets the fresh list")
        self.assertEqual([c["result"] for c in self.controls_log()],
                         ["refused:shape", "refused:bad_signature", "refused:bad_signature", "ok", "bad_name", "gone"])
        self.assertIn("Pixel 9 Pro", [r.get("text") for r in activity.all_since(self.st) if r["k"] == "decision"])

    def test_unbind_another_and_itself(self):
        ph, (other, third), host, s = self.setup_host(2)
        so = _ready(host, other, cid=21)
        so.p33 = True
        tgt = {"device": other.did}

        async def go():
            await host._app(s, {"t": "dev_unbind", "r": "u", **tgt})
            await host._app(s, sign(ph, "dev_unbind", tgt, {"t": "dev_unbind", "r": "ok", **tgt}))
            gone = {"device": "B" * 16}
            await host._app(s, sign(ph, "dev_unbind", gone, {"t": "dev_unbind", "r": "g", **gone}))
            me = {"device": ph.did}
            await host._app(s, sign(ph, "dev_unbind", me, {"t": "dev_unbind", "r": "me", **me}))
        asyncio.run(go())
        self.assertEqual(self.res("u")["why"], "shape")
        self.assertTrue(self.res("ok")["ok"])
        self.assertEqual(self.res("g")["why"], "gone")
        self.assertTrue(self.res("me")["ok"], "the answer reaches the remote before it is detached")
        devs = self.st.devices()
        self.assertNotIn(other.did, devs)
        self.assertNotIn(ph.did, devs)
        self.assertIn(third.did, devs)
        self.assertNotIn(21, host.sessions, "the unbound remote's session is gone at once")
        self.assertNotIn(11, host.sessions)
        self.assertEqual(self.st.removed_why(other.did), "revoked")
        log = self.st.approvals_path.read_text()
        self.assertEqual(log.count('"tool": "dev_unbind"'), 2)
        notices = [o for _, o in self.sent if o["t"] in ("hist_turn", "msg")]
        self.assertTrue(any("解绑了遥控器" in json.dumps(o, ensure_ascii=False) for o in notices), "a notice to every remote")

    def test_add_link_then_code_one_try_then_the_right_code(self):
        ph, (other,), host, s = self.setup_host()

        async def go():
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "a1"}))
            m = self.res("a1")
            self.assertTrue(m["ok"], m)
            self.assertTrue(m["qr"].startswith("data:image/png;base64,"))
            self.assertIn("#p=", m["link"])
            p = host.pairing
            self.assertEqual(p.phone, ph.did)
            # another remote cannot take over or approve this pairing
            so = _ready(host, other, cid=22)
            so.p33 = True
            await host._app(so, sign(other, "dev_add", {}, {"t": "dev_add", "r": "b1"}))
            self.assertEqual(self.res("b1")["why"], "busy")
            # the new device reaches its code
            pend = self.pending(host, p, cid=31)
            await host.remotes.pending(p, pend)
            ev = self.got("dev_pending")[-1]
            self.assertEqual((ev["device"], ev["name"], ev["limit"]), (pend.device, "Galaxy", 10))
            self.assertEqual([c for c, o in self.sent if o["t"] == "dev_pending"], [11], "only the asking remote")
            t = {"device": pend.device, "code": wire.safety_code(pend.h)}
            await host._app(so, sign(other, "dev_confirm", t, {"t": "dev_confirm", "r": "x", **t}))
            self.assertEqual(self.res("x")["why"], "gone")
            wrong = {"device": pend.device, "code": "000000" if t["code"] != "000000" else "111111"}
            await host._app(s, sign(ph, "dev_confirm", wrong, {"t": "dev_confirm", "r": "w", **wrong}))
            self.assertEqual(self.res("w")["why"], "code_mismatch")
            self.assertIsNone(host.pairing, "one try: the pairing ended")
            self.assertNotIn(31, host.sessions)
            self.assertNotIn(pend.device, self.st.devices())
            await asyncio.sleep(0)
            self.assertEqual([o.get("reason") for o in self.got("dev_pair_end")], ["code_mismatch"])
            # a fresh link, the right code
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "a2"}))
            p2 = host.pairing
            pend2 = self.pending(host, p2, cid=32, name="Galaxy 2")
            await host.remotes.pending(p2, pend2)
            ok = {"device": pend2.device, "code": wire.safety_code(pend2.h)}
            await host._app(s, sign(ph, "dev_confirm", ok, {"t": "dev_confirm", "r": "c", **ok}))
            r = self.res("c")
            self.assertTrue(r["ok"], r)
            self.assertEqual((r["device"], r["name"]), (pend2.device, "Galaxy 2"))
        asyncio.run(go())
        rec = self.st.devices()
        new = [k for k, v in rec.items() if v.get("name") == "Galaxy 2"]
        self.assertEqual(len(new), 1)
        self.assertEqual(rec[new[0]]["source"], "phone:" + ph.did)
        self.assertIsNone(host.pairing)
        self.assertIn('"tool": "dev_confirm"', self.st.approvals_path.read_text())
        self.assertTrue(any("经遥控器" in json.dumps(o, ensure_ascii=False) for _, o in self.sent))
        self.assertIn("phone_pair_begin", self.st.log_path.read_text())
        # the new remote shows as "added from a remote" in the list
        rows = remotes.rows(self.st, set(), ph.did)
        self.assertEqual(next(x for x in rows if x["id"] == new[0])["source"], "phone")
        # nothing secret in the logs
        for f in (self.st.log_path, self.st.controls_path):
            self.assertNotIn("#p=", f.read_text())

    @staticmethod
    def pending(host, p, cid, name="Galaxy"):
        pub = os.urandom(32)
        s = serve.Session(cid=cid, state="pending", device=wire.device_id(pub), name=name, pub=pub, h=os.urandom(32))
        s.deadline = time.monotonic() + 60
        host.sessions[cid] = s
        p.cid = cid
        return s

    def test_add_refusals_and_cancel(self):
        ph, _, host, s = self.setup_host()

        async def go():
            remote_pair.set_enabled(self.st, False)
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "d"}))
            remote_pair.set_enabled(self.st, True)
            host.relay_up = False
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "o"}))
            host.relay_up = True
            host.pairing = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, None)   # agentj pair
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "b"}))
            host.pairing = None
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "ok"}))
            first = host.pairing
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "again"}))   # the same remote asks again
            self.assertIsNot(host.pairing, first)
            await host._app(s, {"t": "dev_cancel", "r": "c"})
        asyncio.run(go())
        self.assertEqual([self.res(r).get("why") for r in ("d", "o", "b")], ["disabled", "offline", "busy"])
        self.assertTrue(self.res("ok")["ok"] and self.res("again")["ok"] and self.res("c")["ok"])
        self.assertIsNone(host.pairing)

    def test_full_list_makes_room_like_the_terminal(self):
        ph, others, host, s = self.setup_host(MAX_DEVICES - 1)

        async def go():
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "a"}))
            self.assertTrue(self.res("a")["full"])
            p = host.pairing
            pend = self.pending(host, p, cid=40)
            await host.remotes.pending(p, pend)
            self.assertIn("evict", self.got("dev_pending")[-1])
            t = {"device": pend.device, "code": wire.safety_code(pend.h)}
            await host._app(s, sign(ph, "dev_confirm", t, {"t": "dev_confirm", "r": "c", **t}))
        asyncio.run(go())
        r = self.res("c")
        self.assertTrue(r["ok"])
        self.assertIn(r["evicted"]["id"], [o.did for o in others])
        self.assertEqual(len(self.st.devices()), MAX_DEVICES)


if __name__ == "__main__":
    unittest.main()
