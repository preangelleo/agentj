"""P161: `agentj pair` and `agentj admin` say that a waiting device can also be confirmed on any paired remote (P153 card).

- Terminal: the line shows while a device waits — on the account route (passkey only) and on the local code + passphrase
  route — but only when this computer already has a paired remote to confirm on.
- P444b admin page only links to the account page; the legacy pending block stays hidden. Terminal paired-remote guidance remains bilingual.
"""
import _hermetic  # noqa: F401,I001
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_p127_pair import Base, FakeServe, Terminal  # noqa: E402
from agentj import admin, gate, remote_pair  # noqa: E402

ZH = "也可以在任一已配对的遥控器上确认"
EN = "You can also confirm on any paired remote"


class TerminalLine(Base):
    run_pair = Terminal.run_pair

    def pair(self):
        fs = FakeServe(self.st)
        try:
            return self.run_pair()
        finally:
            fs.close()

    def test_account_route_with_a_paired_remote(self):
        self.bind()
        gate.set_passphrase(self.st, "test-passphrase-p161")   # P221: terminal pairing needs the remote pairing password
        self.st.add_device(b"\x01" * 32, "Leo iPhone")
        code, out, err = self.pair()
        self.assertIn(code, (0, None), out + err)
        self.assertIn(ZH, out)
        self.assertIn(EN, out)
        self.assertLess(out.index(ZH), out.index("✓ 已批准"), "said while the device waits, before the result")

    def test_local_route_with_a_paired_remote(self):
        # P165: since P185 an unbound host cannot pair; the local code + passphrase route is a bound host whose account route is off
        self.bind()
        remote_pair.set_enabled(self.st, False)
        gate.set_passphrase(self.st, "test-passphrase-p161")
        self.st.add_device(b"\x02" * 32, "Leo iPhone")
        code, out, err = self.pair()
        self.assertIn(ZH, out, out + err)
        self.assertIn(EN, out)

    def test_no_line_without_a_paired_remote(self):
        self.bind()
        gate.set_passphrase(self.st, "test-passphrase-p161")
        code, out, err = self.pair()
        self.assertIn(code, (0, None), out + err)
        self.assertNotIn(ZH, out, "the first remote has nothing to confirm on")
        self.assertNotIn(EN, out)


class AdminPage(unittest.TestCase):
    def test_local_page_redirects_and_keeps_legacy_pending_hidden(self):
        html = (admin.ASSET_DIR / "index.html").read_text(encoding="utf-8")
        js = (admin.ASSET_DIR / "app.js").read_text(encoding="utf-8")
        pending = html[html.index('id="pair-pending"'):html.index('id="pair-local-approve"')]
        self.assertIn('id="pair-remote-confirm" data-i18n="pair.remote_confirm" hidden', pending)
        self.assertIn('id="pair-precheck-open" href="https://agentj.app/account/"', html)
        renderer = js.split('function renderPairing(s) {', 1)[1].split('function passkeyOnly', 1)[0]
        self.assertNotIn('s.pairing', renderer)
        self.assertIn("'pair-pending'", renderer)
        self.assertIn('show(id,false)', renderer)
        zh = json.loads((admin.ASSET_DIR / "i18n/admin.zh.json").read_text(encoding="utf-8"))["pair.remote_confirm"]
        en = json.loads((admin.ASSET_DIR / "i18n/admin.en.json").read_text(encoding="utf-8"))["pair.remote_confirm"]
        self.assertIn("已配对的遥控器", zh)
        self.assertIn("确认", zh)
        self.assertIn(EN, en)


if __name__ == "__main__":
    unittest.main()
