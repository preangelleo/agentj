"""P127: pairing pre-check (one source: agentj/pairprep.py) — terminal, admin page, onboarding, skill and install.md agree.

Leo 10-10: a bound seat still had `agentj pair --link` force an approval passphrase, and the admin page only said
"passphrase not set" at the very last step. Now: account-bound + online → the account-page passkey route, passphrase optional;
unbound → the passphrase is still required, and every surface says so before any QR exists.
"""
import _hermetic  # noqa: F401,I001
import argparse
import contextlib
import io
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))

from agentj import admin, cli, cloud, gate, onboarding, pairprep, remote_pair  # noqa: E402
from agentj.state import State  # noqa: E402

PASS = "test-passphrase-p127"
LINK = {"api": "https://agentj.app/api", "host_id": "h_p127", "tenant": {"slug": "acme-co", "name": "Acme"},
        "linked_at": 1, "last_seq": 0, "via": "seat"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p127-", dir="/tmp")
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init()

    def tearDown(self):
        self.tmp.cleanup()

    def bind(self):
        cloud.write_cloud(self.st, LINK)


class Precheck(Base):
    def test_unbound_without_passphrase_must_set_it(self):
        c = pairprep.precheck(self.st, relay_up=True)
        self.assertEqual((c["route"], c["bound"], c["account"], c["passphrase_set"]), ("set_passphrase", False, False, False))
        self.assertIn("agentj passphrase set", pairprep.message(c, "zh"))
        self.assertIn("agentj passphrase set", pairprep.message(c, "en"))

    def test_unbound_with_passphrase_is_local(self):
        gate.set_passphrase(self.st, PASS)
        self.assertEqual(pairprep.precheck(self.st, relay_up=True)["route"], "passphrase")

    def test_bound_online_is_account_route_and_passphrase_optional(self):
        self.bind()
        c = pairprep.precheck(self.st, relay_up=True)
        self.assertEqual((c["route"], c["account"], c["passphrase_set"]), ("account", True, False))
        self.assertEqual(c["account_url"], cloud.app_url(self.st))
        zh, en = pairprep.message(c, "zh"), pairprep.message(c, "en")
        self.assertIn("添加遥控器", zh)
        self.assertIn("可选", zh)
        self.assertIn(c["account_url"], zh)
        self.assertIn("optional", en)
        gate.set_passphrase(self.st, PASS)
        self.assertEqual(pairprep.precheck(self.st, relay_up=True)["route"], "account", "passkey route stays the default")

    def test_bound_but_off_or_offline_falls_back_with_the_reason(self):
        self.bind()
        c = pairprep.precheck(self.st, relay_up=False)
        self.assertEqual((c["route"], c["why"]), ("set_passphrase", "offline"))
        remote_pair.set_enabled(self.st, False)
        c = pairprep.precheck(self.st, relay_up=True)
        self.assertEqual((c["route"], c["why"]), ("set_passphrase", "off"))
        self.assertIn("agentj remote-pair on", pairprep.message(c, "zh"))
        gate.set_passphrase(self.st, PASS)
        self.assertEqual(pairprep.precheck(self.st, relay_up=True)["route"], "passphrase")


class FakeServe:
    """A control socket that answers `status` and plays one `pair` stream: link → pending → (after the human's
    account-page passkey) approved. Records every request line the CLI sends on the pair stream."""

    def __init__(self, st, relay_up=True):
        self.st, self.relay_up, self.sent = st, relay_up, []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(st.sock_path))
        self.sock.listen(8)
        self.stop = False
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while not self.stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.one, args=(c,), daemon=True).start()

    def one(self, c):
        f = c.makefile("rwb")
        req = json.loads(f.readline() or b"{}")
        if req.get("cmd") == "status":
            f.write(json.dumps({"ok": True, "relay_up": self.relay_up, "sessions": []}).encode() + b"\n")
            f.flush()
        elif req.get("cmd") == "pair":
            for ev in ({"ev": "link", "link": "https://m.agentj.app/#p=TEST", "expires": int(time.time()) + 300},
                       {"ev": "pending", "name": "Phone", "device": "d" * 16, "limit": 5, "deadline_in": 120}):
                f.write(json.dumps(ev).encode() + b"\n")
                f.flush()
            c.settimeout(0.6)
            try:
                line = f.readline()
                if line:
                    self.sent.append(json.loads(line))
            except (OSError, ValueError):
                pass
            f.write(json.dumps({"ev": "approved", "name": "Phone", "device": "d" * 16}).encode() + b"\n")
            f.flush()
        c.close()

    def close(self):
        self.stop = True
        self.sock.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.st.sock_path)


class Terminal(Base):
    def run_pair(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        a = argparse.Namespace(no_qr=True, link=True, check=False, json=False)
        for k in argv:
            setattr(a, k, True)
        with patch.object(cli, "State", return_value=self.st), patch("sys.stdin", io.StringIO("")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.cmd_pair(a)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_bound_online_pairs_without_forcing_a_passphrase(self):
        self.bind()
        fs = FakeServe(self.st)
        try:
            code, out, err = self.run_pair()
        finally:
            fs.close()
        self.assertIn(code, (0, None), out + err)
        self.assertFalse(gate.is_set(self.st), "no passphrase was set")
        self.assertNotIn("配对前先设置批准口令", out)
        self.assertIn(cloud.app_url(self.st), out)
        self.assertIn("可选", out)
        self.assertIn("✓ 已批准", out)
        self.assertEqual(fs.sent, [], "with no passphrase the terminal never asks for / sends a code: the account page approves")

    def test_unbound_still_needs_the_passphrase_before_any_link(self):
        fs = FakeServe(self.st)
        try:
            code, out, err = self.run_pair()
        finally:
            fs.close()
        self.assertNotIn(code, (0, None))
        self.assertIn("agentj passphrase set", str(code) + err)
        self.assertNotIn("#p=", out, "no link before the pre-check passes")

    def test_check_flag_prints_the_route_without_starting_a_pairing(self):
        self.bind()
        fs = FakeServe(self.st)
        try:
            code, out, _ = self.run_pair("check", "json")
        finally:
            fs.close()
        self.assertIn(code, (0, None))
        c = json.loads(out)
        self.assertEqual((c["route"], c["passphrase_set"]), ("account", False))
        self.assertNotIn("#p=", out)
        code, out, _ = self.run_pair("check")      # no serve at all: still answers, from local state
        self.assertIn(code, (0, None))
        self.assertIn("添加遥控器", out)


class Page(Base):
    def admin(self, relay_up=True):
        a = admin.Admin(self.st)
        a.serve_status = lambda: {"ok": True, "relay_up": relay_up, "sessions": []}
        return a

    def test_state_carries_the_precheck(self):
        s = self.admin().state()
        self.assertEqual(s["pair_check"]["route"], "set_passphrase")
        self.bind()
        s = self.admin().state()
        self.assertEqual((s["pair_check"]["route"], s["pair_check"]["account_url"]), ("account", cloud.app_url(self.st)))

    def test_start_refuses_before_a_qr_when_the_passphrase_is_missing(self):
        with patch.object(admin, "Pairing", side_effect=AssertionError("no pairing may start")):
            code, body = self.admin().pair_start()
        self.assertEqual((code, body["error"]), (409, "passphrase_not_set"))

    def test_start_is_allowed_on_a_bound_online_host_without_a_passphrase(self):
        self.bind()
        sentinel = AssertionError("pairing reached")
        with patch.object(admin, "Pairing", side_effect=sentinel):
            with self.assertRaises(AssertionError) as cm:
                self.admin().pair_start()
        self.assertIs(cm.exception, sentinel, "the pre-check let the account route through")
        a = self.admin(relay_up=False)
        with patch.object(admin, "Pairing", side_effect=AssertionError("no pairing may start")):
            code, body = a.pair_start()
        self.assertEqual((code, body["error"]), (409, "passphrase_not_set"), "offline + no passphrase: refused before a QR")

    def test_page_shows_the_precheck_in_the_idle_state(self):
        js = (admin.ASSET_DIR / "app.js").read_text(encoding="utf-8")
        html = (admin.ASSET_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="pair-precheck"', html)
        self.assertIn("pair_check", js)
        self.assertIn('id="pair-pending-account"', html)   # passkey-only pending: no code / passphrase fields on this page
        self.assertIn('id="pair-local-approve"', html)
        self.assertIn("passkeyOnly", js)
        self.assertIn("!running || !canStart", js, "the button stays off until the pre-check passes")
        for lang in ("zh", "en"):
            d = json.loads((admin.ASSET_DIR / f"i18n/admin.{lang}.json").read_text(encoding="utf-8"))
            for k in ("pair.pre_set", "pair.pre_account", "pair.pre_account_open", "pair.pending_account",
                      "err.passphrase_not_set"):
                self.assertIn(k, d, f"{lang}:{k}")


class Guides(Base):
    def test_onboarding_hint_follows_the_precheck(self):
        st, _, hint = onboarding.doctor_row(self.st)
        self.assertIn("agentj passphrase set", hint, "unbound: say the passphrase comes first")
        self.bind()
        _, _, hint = onboarding.doctor_row(self.st)
        self.assertIn("添加遥控器", hint)

    def test_skill_and_install_use_the_same_check(self):
        skill = (HOST / "agentj/skills/agentj-pair/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("agentj pair --check", skill)
        guide = HOST.parent / "install.md"
        if not guide.is_file():
            guide = HOST.parents[1] / "documentation/product/install.md"
        install = guide.read_text(encoding="utf-8")
        self.assertIn("agentj pair --check", install)


if __name__ == "__main__":
    unittest.main()
