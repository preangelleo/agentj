"""P242 (support st_obn0Pxl): `agentj approvals --verify` said ✗ for every add (`dev_confirm`) / unbind (`dev_unbind`) made on
the phone's 遥控器管理 page — remotes.py wrote those approvals.log lines with reason "device" but without the signature, so the
check had nothing to verify. The action itself was always gated by a valid control signature (controls.log has it): a false
alarm, not a forged approval.

Now: the line carries the control's signature, nonce, ts and object hash + the signer's key and verifies as that control
(tampered / another key → ✗, signer unbound since → ✓ removed); a line written before the fix is checked against its own
controls.log line (✓ when it verifies, ✗ when that line does not, "legacy" — never ✗ — when it is gone); the P153 pair_device
card that folds because the asking remote's code already approved the device says so (reason "elsewhere", not "gone").
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile  # noqa: F401  (p171.Serve.setUp)
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import approvals, cli, wire  # noqa: E402

from test_controls import sign  # noqa: E402
from test_l1 import Phone, _ready  # noqa: E402
import test_p171_remotes as p171  # noqa: E402


class ControlLines(unittest.TestCase):
    # the P171 harness, without re-running its tests here
    setUp, tearDown, got, res = p171.Serve.setUp, p171.Serve.tearDown, p171.Serve.got, p171.Serve.res
    setup_host, controls_log = p171.Serve.setup_host, p171.Serve.controls_log
    pending = staticmethod(p171.Serve.pending)

    def lines(self, tool=None):
        return [r for r in approvals.read_log(self.st) if tool is None or r.get("tool") == tool]

    def verdicts(self, tool):
        every = approvals.read_log(self.st)
        return [approvals.check_record(self.st, r, {}) for r in every if r.get("tool") == tool]

    def add_one(self, card=False):
        ph, (other,), host, s = self.setup_host()

        async def go():
            await host._app(s, sign(ph, "dev_add", {}, {"t": "dev_add", "r": "a"}))
            p = host.pairing
            pend = self.pending(host, p, cid=31)
            pend.pending_since = time.monotonic()
            if card:                       # the P153 card every pair1 remote gets for a waiting device (serve hello path)
                s.pair1 = True
                host._pair_card_open(p, pend)
            await host.remotes.pending(p, pend)
            ok = {"device": pend.device, "code": wire.safety_code(pend.h)}
            await host._app(s, sign(ph, "dev_confirm", ok, {"t": "dev_confirm", "r": "c", **ok}))
            for _ in range(5):
                await asyncio.sleep(0)
            return pend
        pend = asyncio.run(go())
        self.assertTrue(self.res("c")["ok"])
        return ph, other, host, s, pend

    def test_add_line_carries_the_control_signature_and_verifies(self):
        ph, *_ = self.add_one()
        (r,) = self.lines("dev_confirm")
        self.assertEqual((r["kind"], r["reason"], r["device"]), ("control", "device", ph.did))
        self.assertEqual(wire.unb64u(r["sk"]), self.st.sign_key(ph.did))
        self.assertEqual(self.verdicts("dev_confirm"), ["ok"])
        c = [x for x in self.controls_log() if x["action"] == "dev_confirm"][-1]
        self.assertEqual((r["n"], r["sig_ts"], r["sig"], r["object_sha256"]), (c["n"], c["sig_ts"], c["sig"], c["object_sha256"]))
        for k, v in (("object_sha256", "0" * 64), ("device", "B" * 16), ("n", "c" * 32), ("sig_ts", r["sig_ts"] + 1)):
            self.assertEqual(approvals.check_record(self.st, {**r, k: v}), "bad", k)
        other_key = Phone(self.st, "x")   # a valid signature under a key that is not this device's
        self.assertEqual(approvals.check_record(self.st, {**r, "sk": wire.b64u(other_key.sk.public_key().public_bytes_raw())}),
                         "bad")
        a = types.SimpleNamespace(last=0, verify=True, json=False)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), contextlib.redirect_stdout(out):
            cli.cmd_approvals(a)          # no SystemExit: nothing invalid
        self.assertIn("dev_confirm", out.getvalue())
        self.assertIn("✓签名有效", out.getvalue())
        self.assertNotIn("✗", out.getvalue())

    def test_the_folded_pair_card_says_elsewhere(self):
        """The ticket's second line: `deny pair_device gone` in the same second — the card the remote also had, folded
        because the code typed on the remote approved the device. It is unsigned and now says why."""
        ph, other, host, s, pend = self.add_one(card=True)
        (pc,) = self.lines("pair_device")
        self.assertEqual((pc["decision"], pc["reason"], pc["sig"]), ("deny", "elsewhere", None))
        self.assertEqual(approvals.check_record(self.st, pc), "unsigned")
        self.assertEqual(self.verdicts("dev_confirm"), ["ok"])
        self.assertIn(pend.device, self.st.devices())

    def test_unbind_lines_verify_also_of_the_remote_itself(self):
        ph, (other,), host, s = self.setup_host()
        so = _ready(host, other, cid=21)
        so.p33 = True

        from unittest import mock
        from agentj import passkey
        fa = {"id": wire.b64u(b"\x07" * 16), "cd": "e30", "ad": "AA", "sig": "AA"}   # P551: another remote → Face ID
        self.st.set_passkey(ph.pub, {"id": fa["id"], "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                                     "rp": "m.agentj.app", "at": int(time.time())})

        async def go():
            t = {"device": other.did}
            with mock.patch.object(passkey, "verify_assertion", return_value=1):
                await host._app(s, sign(ph, "dev_unbind", t, {"t": "dev_unbind", "r": "o", **t, "fa": fa}))
            me = {"device": ph.did}
            await host._app(s, sign(ph, "dev_unbind", me, {"t": "dev_unbind", "r": "m", **me}))
        asyncio.run(go())
        self.assertEqual(self.verdicts("dev_unbind"), ["ok_removed", "ok_removed"], "the signer unbound itself since")

    def test_lines_from_before_the_fix(self):
        ph, *_ = self.add_one()
        (r,) = self.lines("dev_confirm")
        old = {k: v for k, v in r.items() if k not in ("kind", "n", "sig_ts", "object_sha256")}
        old["sk"] = old["sig"] = None                      # exactly what 0.20.2a1 wrote
        self.assertEqual(approvals.check_record(self.st, old), "ok", "checked against its controls.log line")
        self.assertEqual(approvals.check_record(self.st, {**old, "ts": old["ts"] + 60}), "legacy", "no line to check")
        lines = self.st.controls_path.read_text().splitlines()
        forged = [json.dumps({**json.loads(x), "object_sha256": "0" * 64}) if json.loads(x)["action"] == "dev_confirm" else x
                  for x in lines]
        self.st.controls_path.write_text("\n".join(forged) + "\n")
        self.assertEqual(approvals.check_record(self.st, old), "bad", "its controls.log line does not verify")
        self.st.controls_path.unlink()
        self.assertEqual(approvals.check_record(self.st, old), "legacy")
        # a host-side line of another tool keeps its old verdict
        self.assertEqual(approvals.check_record(self.st, {**old, "tool": "Bash", "agent": "claude"}), "bad")


if __name__ == "__main__":
    unittest.main()
