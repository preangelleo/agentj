"""P573 (#386 ⑤): a sudo card is the admin password + one tap — no Face ID on top — and always says `helper_uv` 0|1, so the
page knows this host speaks the new rule (the helper's approve-only path, no password, still needs Face ID)."""
import _hermetic  # noqa: F401,I001
import time
import unittest

from agentj import serve  # noqa: E402
import test_p57_passkey as P57  # noqa: E402


class SudoCard(unittest.TestCase):
    setUp, tearDown = P57.HostLevel.setUp, P57.HostLevel.tearDown
    run_, to, paired = P57.HostLevel.run_, P57.HostLevel.to, P57.HostLevel.paired

    def card(self, helper):
        kp, _, did = self.paired(P57.Authenticator())
        s = serve.Session(cid=5, state="ready", device=did, pub=kp.pub, p33=True)
        c = {"id": "c1", "kind": "sudo", "nonce": "n", "epk": b"\0" * 32, "deadline": time.monotonic() + 60, "fails": 0,
             "cmd": "sudo apt-get install -y jq", "why": "install jq", "argv": ["apt-get", "install", "-y", "jq"],
             "helper": [did] if helper else []}
        return self.host.elevate.card_msg(c, s)

    def test_password_card_needs_no_face_id(self):
        m = self.card(False)
        self.assertIs(m["passkey_required"], False)
        self.assertEqual(m["helper_uv"], 0)

    def test_helper_path_still_needs_face_id(self):
        m = self.card(True)
        self.assertIs(m["passkey_required"], False)
        self.assertEqual(m["helper_uv"], 1)

    def test_key_cards_keep_face_id(self):
        kp, _, did = self.paired(P57.Authenticator())
        s = serve.Session(cid=6, state="ready", device=did, pub=kp.pub, p33=True)
        c = {"id": "c2", "kind": "login", "term": "t1", "nonce": "n", "epk": b"\0" * 32,
             "deadline": time.monotonic() + 60, "fails": 0}
        m = self.host.elevate.card_msg(c, s)
        self.assertIs(m["passkey_required"], True)
        self.assertNotIn("helper_uv", m)


if __name__ == "__main__":
    unittest.main()
