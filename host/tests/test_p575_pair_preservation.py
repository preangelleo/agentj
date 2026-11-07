"""#575: updates keep paired remotes; an already approved key repairs in place.

All identity and credentials below are disposable fixture state. Installation is
stubbed at the subprocess boundary; update.apply and Host initialization are real.
"""
import _hermetic  # noqa: F401
import copy
import json
import os
import time
import unittest
from unittest.mock import patch

from agentj import remote_pair, serve, wire
from agentj.state import State
from test_p277_upgrade_diag import _Env, Runner
import test_p57_passkey as passkey_fixture
import test_p122_renewal as renewal_fixture


class PairPreservation(_Env):
    def seeded(self):
        pub, sk = os.urandom(32), os.urandom(32)
        did = self.st.pair_device(pub, "macOS Chrome", sk, iid="fixture-install")["device"]
        records = self.st.devices()
        records[did].update(pk={"id": wire.b64u(os.urandom(32)), "alg": -7, "dev": did},
                            rt={"i": wire.b64u(os.urandom(16)), "k": wire.b64u(os.urandom(32))},
                            paired_at=1234, seen=5678, pair_origin="fixture-origin",
                            pk_ancestors=["m.agentj.app:" + "a" * 64], owner_name="Owner's Mac", name="Owner's Mac",
                            owner_named_at=1235, owner_named_via="phone")
        self.st.write_private(self.st.devices_path, json.dumps(records).encode())
        return pub, sk, did, copy.deepcopy(records[did])

    def test_same_key_repair_keeps_credentials_history_and_revocation_lineage(self):
        pub, sk, did, before = self.seeded()
        other = self.st.add_device(os.urandom(32), "other")
        other_before = self.st.devices()[other]
        result = self.st.pair_device(pub, "macOS Chrome again", sk, iid="fixture-install", evict=True)
        after = self.st.devices()[did]
        for field in ("pk", "rt", "paired_at", "seen", "pair_origin", "pk_ancestors", "owner_name", "owner_named_at"):
            self.assertEqual(after.get(field), before[field], field)
        self.assertEqual(after["self_name"], "macOS Chrome again")
        self.assertEqual(after["name"], "Owner's Mac")
        self.assertEqual(self.st.devices()[other], other_before)
        self.assertEqual((result["replaced"], result["evicted"]), (None, None))

    def test_same_key_explicit_new_signing_key_is_approved_without_losing_passkey(self):
        pub, _, did, before = self.seeded()
        new_signing_key = os.urandom(32)
        self.st.pair_device(pub, "same browser", new_signing_key)
        after = self.st.devices()[did]
        self.assertEqual(after["sk"], wire.b64u(new_signing_key))
        self.assertEqual(after.get("pk"), before["pk"])
        self.assertEqual(after.get("pair_origin"), before["pair_origin"])

    def test_legacy_same_key_without_signing_key_keeps_existing_approval_key(self):
        pub, _, did, before = self.seeded()
        self.st.pair_device(pub, "legacy browser")
        self.assertEqual(self.st.devices()[did].get("sk"), before["sk"])

    def test_different_key_same_install_never_inherits_credentials(self):
        _, _, old, before = self.seeded()
        result = self.st.pair_device(os.urandom(32), "fresh browser", os.urandom(32), iid="fixture-install")
        after = self.st.devices()[result["device"]]
        self.assertNotIn(old, self.st.devices())
        for field in ("pk", "rt", "seen", "pair_origin", "pk_ancestors"):
            self.assertNotIn(field, after, field)
        self.assertEqual(after["owner_name"], before["owner_name"])
        self.assertEqual(self.st.removed_why(old), "replaced")

    def test_revoked_key_cannot_restore_old_credentials_by_pairing_again(self):
        pub, sk, did, _ = self.seeded()
        self.st.remove_device(did)
        self.st.pair_device(pub, "approved anew", sk)
        after = self.st.devices()[did]
        self.assertNotIn("pk", after)
        self.assertNotIn("rt", after)

    def test_upgrade_and_restart_preserve_allowlist_keys_and_remote_pair_policy(self):
        self.seeded()
        paths = (self.st.x25519_path, self.st.ed25519_path, self.st.devices_path)
        for value in (None, True, False):
            with self.subTest(remote_pair=value):
                cfg = self.st.config()
                cfg.pop("remote_pair", None)
                if value is not None:
                    cfg["remote_pair"] = value
                self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
                before = {p: p.read_bytes() for p in paths + (self.st.config_path,)}
                self.assertEqual(self.apply(Runner(), svc_on=False)["result"], "ok")
                # Same upgrade start path, with a new Host and fresh State objects.
                with patch("agentj.userdata.heal", return_value=[]), patch("agentj.userdata.version_snapshot"):
                    host = serve.Host(State(self.st.root), events="jsonl", read_stdin=False)
                self.assertEqual({p: p.read_bytes() for p in before}, before)
                self.assertEqual(remote_pair.enabled(host.st), value is not False)
                self.assertTrue(all(host.st.is_allowed(wire.unb64u(v["pub"])) for v in host.st.devices().values()))

    def test_same_key_keeps_rotation_origin_and_ancestor_revocation_applies(self):
        from agentj import credential_revoke, security_rotation
        pub, sk, did, before = self.seeded()
        self.st.pair_device(pub, "repair", sk)
        after = self.st.devices()[did]
        self.assertTrue(security_rotation.is_old({"old": [], "old_origins": [before["pair_origin"]]}, did, after))
        journal = credential_revoke.read(self.st)
        journal["fingerprints"] = before["pk_ancestors"]
        journal["devices"] = [did]
        self.st.write_private(self.st.root / "credential_revocations.json", json.dumps(journal).encode())
        self.assertNotIn(did, self.st.devices())
        self.st.pair_device(pub, "new approval", sk)
        self.assertNotIn(did, self.st.devices(), "a durable revoked device id cannot be resurrected")


class FullPairRepair(unittest.TestCase):
    setUp = passkey_fixture.HostLevel.setUp
    tearDown = passkey_fixture.HostLevel.tearDown
    run_ = passkey_fixture.HostLevel.run_
    paired = passkey_fixture.HostLevel.paired
    handshake = passkey_fixture.HostLevel.handshake
    to = passkey_fixture.HostLevel.to
    rt_restore = renewal_fixture.HostLevel.rt_restore

    def test_new_signing_key_after_authorized_pair_keeps_uv_and_rotates_old_ticket(self):
        from agentj import renewal
        auth = passkey_fixture.Authenticator()
        kp, _, did = self.paired(auth)
        old_pk = self.st.devices()[did]["pk"]
        old_rt = renewal.new_ticket()
        self.st.set_ticket(kp.pub, old_rt)
        sk = os.urandom(32)
        s = serve.Session(cid=7, state="pending", device=did, pub=kp.pub,
                          sign_pub=sk, name="Mac again", p33=True, h=os.urandom(32))
        s.deadline = time.monotonic() + 60
        self.host.sessions[7] = s
        self.run_(self.host._grant_pair(s))
        after = self.st.devices()[did]
        self.assertEqual(after["pk"], old_pk)
        offer = next(m for m in self.to(7) if m["t"] == "pk_offer")
        self.assertEqual(offer.get("replace"), old_pk["id"])
        self.assertEqual(after["sk"], wire.b64u(sk))
        self.assertNotEqual(after["rt"], old_rt)
        self.assertIsNone(self.st.ticket_of(old_rt["i"]))
        # Keeping a credential does not allow overwriting it without its UV proof.
        self.assertFalse(self.st.set_passkey(kp.pub, {**old_pk, "id": wire.b64u(os.urandom(32))}))
        self.sent.clear()
        _, _, result = self.rt_restore(9, old_rt)
        self.assertEqual(result, [{"t": "pk_fail", "why": "unknown"}])
