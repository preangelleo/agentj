"""P497: real automation resume path with local Ed25519 fixtures; no model/network."""
import _hermetic  # noqa: F401
import asyncio
import copy
import json
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from agentj import provenance, tasks, wf_resume as wf, wire, wfrun
from test_p426_wfresume import Base

NOW = 1792812000


class SignedResume(Base):
    def setUp(self):
        super().setUp()
        self.h.st.init()
        self.sk = Ed25519PrivateKey.generate()
        pub = self.sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.device = self.h.st.pair_device(b'F'*32, "fixture phone", pub)["device"]
        self.rid = self.run_once()["run_id"]
        self.channel = "fixture-noise-channel"
        self.source = provenance.phone()

    def challenge(self):
        return wf.challenge(self.h, self.channel, self.device, self.rid, source=self.source, now=NOW)

    def sign(self, obj):
        return wire.b64u(self.sk.sign(wf.signing_bytes(obj)))

    def execute(self, obj, sig=None, **kw):
        return asyncio.run(wf.execute(self.h, obj, sig or self.sign(obj), channel=kw.pop("channel", self.channel),
                                      device=kw.pop("device", self.device), source=kw.pop("source", self.source),
                                      now=kw.pop("now", NOW+1), **kw))

    def test_valid_signature_real_resume_still_confirms_and_reuses(self):
        obj = self.challenge()
        out = self.execute(obj)
        self.assertEqual(out["result"], "queued", out)
        self.assertEqual(len(self.h.asked), 1)  # non-idempotent draft still asks the owner
        self.calls.clear()
        self.failing.clear()
        res = self.run_once("resume")
        self.assertEqual(self.calls, ["draft", "publish"])
        self.assertEqual(res["verdict"], "ok")
        self.assertEqual(self.execute(obj)["why"], "replay")
        self.assertEqual((self.h.st.root/wf.FILE).stat().st_mode & 0o777, 0o600)

    def test_decline_does_not_queue_and_nonce_stays_consumed(self):
        obj = self.challenge()
        self.h.answer = "deny"
        self.assertEqual(self.execute(obj)["result"], "declined")
        self.assertNotIn("daily", self.h.scheduler.plans)
        self.assertEqual(self.execute(obj)["why"], "replay")

    def test_bad_signature_other_device_channel_and_friend_refused(self):
        obj = self.challenge()
        other = Ed25519PrivateKey.generate()
        self.assertEqual(self.execute(obj, wire.b64u(other.sign(wf.signing_bytes(obj))))["why"], "bad_signature")
        self.assertEqual(self.execute(obj, channel="another")["why"], "target_changed")
        for source in (None, {"kind": "same_owner", "role": "task"}, {"kind": "friend_task", "role": "task"}, provenance.system()):
            self.assertEqual(self.execute(obj, source=source)["why"], "owner_required")
        self.assertEqual(self.execute(obj, device="unknown")["why"], "unpaired")
        self.assertFalse(self.h.asked)

    def test_expiry_future_tampering_unknown_nonce(self):
        obj = self.challenge()
        self.assertEqual(self.execute(obj, now=NOW+wf.TTL)["why"], "expired")
        self.assertEqual(self.execute(obj, now=NOW-1)["why"], "expired")
        forged = dict(obj, nonce="a"*32)
        self.assertEqual(self.execute(forged)["why"], "replay")
        forged = dict(obj, tsha="b"*64)
        self.assertEqual(self.execute(forged, self.sign(obj))["why"], "bad_signature")
        for changed in (dict(obj, expires=obj["expires"]+1), dict(obj, same_owner=True), dict(obj, action="resume")):
            out = asyncio.run(wf.execute(self.h, changed, self.sign(obj), channel=self.channel, device=self.device,
                                         source=self.source, now=NOW))
            self.assertEqual(out["why"], "shape")

    def test_contract_change_and_run_change_rejected(self):
        obj = self.challenge()
        (self.wf/"S2.md").write_text("changed after phone displayed it")
        self.assertEqual(self.execute(obj)["why"], "contract_changed")
        (self.wf/"S2.md").write_text("do draft\n")
        rec = wfrun.load(self.h.st, self.rid)
        rec["failed"]["why"] = "changed progress"
        wfrun.save(self.h.st, rec)
        self.assertEqual(self.execute(obj)["why"], "run_changed")

    def test_contract_change_during_confirmation_refused_at_enqueue(self):
        obj = self.challenge()
        original = self.h.ask
        async def ask(*args, **kw):
            ans = await original(*args, **kw)
            (self.wf/"S3.md").write_text("changed while card open")
            return ans
        self.h.ask = ask
        out = self.execute(obj)
        self.assertEqual(out.get("why"), "changed", out)
        self.assertNotIn("daily", self.h.scheduler.plans)

    def test_key_revoked_while_confirmation_open_refuses_enqueue(self):
        obj = self.challenge()
        original = self.h.ask
        async def ask(*args, **kw):
            ans = await original(*args, **kw)
            self.h.st.write_private(self.h.st.devices_path, b"{}")
            return ans
        self.h.ask = ask
        self.assertEqual(self.execute(obj).get("why"), "changed")
        self.assertNotIn("daily", self.h.scheduler.plans)

    def test_revoked_sign_key_and_duplicate_after_reconstructed_engine(self):
        obj = self.challenge()
        with patch.object(self.h.st, "sign_key", return_value=None):
            self.assertEqual(self.execute(obj)["why"], "unpaired")
        self.execute(obj)
        self.assertEqual(self.execute(copy.deepcopy(obj))["why"], "replay")

    def test_concurrent_signed_calls_one_confirmation(self):
        obj = self.challenge()
        async def both():
            return await asyncio.gather(*(wf.execute(self.h, obj, self.sign(obj), channel=self.channel,
                            device=self.device, source=self.source, now=NOW+1) for _ in range(2)))
        results = asyncio.run(both())
        self.assertEqual(sum(r["result"] == "queued" for r in results), 1)
        self.assertEqual(sum(r.get("why") == "replay" for r in results), 1)
        self.assertEqual(len(self.h.asked), 1)

    def test_corrupt_challenge_store_refuses_instead_of_erasing_replay_state(self):
        self.challenge()
        self.h.st.write_private(self.h.st.root/wf.FILE, b"broken")
        with self.assertRaises(wf.Refused):
            self.challenge()
