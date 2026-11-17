"""R-0055 (chairman 11-17, 0.30.29 on Linux): the owner lost the only remote with Face ID.

`agentj revoke <lost>` waited silently; `agentj faceid approve` said "approve on the other Face ID remote" (the lost one).
Words only — who may decide is unchanged:
- revoke names the remotes that can confirm, what to tap and how long, before it waits; counts down; on a refusal / timeout
  names the way out (account dashboard unlink, or `agentj admin` with Agent J stopped);
- faceid approve's approver_exists names those remotes and the same way out;
- the account-dashboard unlink (serve.remote_unbind) really releases the terminal approval (its passkey leaves with it).
Fake state dir under a fake HOME; no network, no serve process: names.ctl_call is stubbed."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import argparse
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

import test_p57_passkey as p57  # noqa: E402
from agentj import cli, gate, names, serve  # noqa: E402
from agentj.noise import Keypair  # noqa: E402
from agentj.state import State  # noqa: E402


def _sign_pub():
    return ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP") or None)
        home = pathlib.Path(self.tmp.name) / "home"
        (home / ".config").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                                           "AGENTJ_STATE_DIR": str(home / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.tmp.cleanup()

    def device(self, name, faceid=False):
        kp = Keypair.generate()
        did = self.st.pair_device(kp.pub, name, _sign_pub())["device"]
        if faceid:
            self.assertTrue(self.st.set_passkey(kp.pub, p57.pk_record(p57.Authenticator())))
        return did

    def run_cli(self, fn, ns, ctl, lang="zh"):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with mock.patch.object(names, "ctl_call", ctl), mock.patch.object(cli, "_lang", lambda st: lang), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                fn(ns)
            except SystemExit as e:
                code = e.code
        return out.getvalue(), str(code or "") + err.getvalue()


class RevokeSays(Base):
    def ctl(self, answer, online=(), delay=0.0, seen=None):
        def call(st, req, timeout=None, **kw):
            if req["cmd"] == "status":
                return {"ok": True, "sessions": [{"device": d, "state": "ready"} for d in online]}
            if seen is not None:
                seen.append((req, sys.stdout.getvalue()))
            time.sleep(delay)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return call

    def test_names_the_confirming_remotes_before_it_waits(self):
        lost = self.device("iPhone Air", faceid=True)
        pad = self.device("iPad", faceid=True)
        self.device("Chrome")
        seen = []
        out, _ = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost),
                              self.ctl({"ok": True, "closed": 1}, online=[pad], seen=seen))
        self.assertEqual(seen[0][0], {"cmd": "revoke", "device": lost})
        before = seen[0][1]                                      # what the owner saw while serve was asked
        for word in ("iPad", pad, "Face ID", "120 秒", "在线", "iPhone Air"):
            self.assertIn(word, before)
        self.assertNotIn("Chrome", before, "a remote without Face ID cannot confirm")
        self.assertIn("就是要解绑的这台", before)
        self.assertNotIn("agentj admin", before, "an online Face ID remote: no recovery wall of text")
        self.assertIn(f"已吊销 {lost}", out)

    def test_no_other_face_id_remote_online_gives_the_way_out_up_front(self):
        lost = self.device("iPhone Air", faceid=True)
        chrome = self.device("Chrome")
        seen = []
        _, err = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost),
                              self.ctl({"ok": False, "error": "uv_required", "closed": 0}, online=[chrome], seen=seen))
        before = seen[0][1]
        self.assertIn("没有别的设了 Face ID 的遥控器在线", before)
        self.assertIn("agentj admin", before)
        self.assertIn("agentj service stop", before)
        self.assertNotIn("没有这台设备", err, "uv_required is not 'no such device' (base said so)")
        self.assertIn("没有解绑", err)
        self.assertIn("agentj admin", err)

    def test_account_path_named_when_linked(self):
        lost = self.device("iPhone Air", faceid=True)
        with mock.patch("agentj.cloud.read_cloud", lambda st: {"host_id": "h"}):
            _, err = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost),
                                  self.ctl({"ok": False, "error": "uv_required", "closed": 0}))
        self.assertIn("agentj.app/account", err)
        self.assertIn("「解绑」", err)
        self.st.set_remote_unbind(False)
        with mock.patch("agentj.cloud.read_cloud", lambda st: {"host_id": "h"}):
            _, err = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost),
                                  self.ctl({"ok": False, "error": "uv_required", "closed": 0}))
        self.assertIn("agentj remote-unbind on", err)

    def test_counts_down_while_it_waits(self):
        lost = self.device("iPhone Air", faceid=True)
        from agentj import lost_remote
        with mock.patch.object(lost_remote, "TICK", 0.05):
            out, _ = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost),
                                  self.ctl({"ok": True, "closed": 0}, delay=0.3))
        self.assertGreaterEqual(out.count("还在等 Face ID 确认"), 2, out)

    def test_timeout_says_what_next(self):
        lost = self.device("iPhone Air", faceid=True)
        _, err = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost), self.ctl(names.ServeBusy("timeout")))
        self.assertIn("确认未完成", err)
        self.assertIn("agentj admin", err)

    def test_unknown_device_asks_no_card(self):
        self.device("iPhone Air", faceid=True)
        seen = []
        _, err = self.run_cli(cli.cmd_revoke, argparse.Namespace(device="nosuchdevice0000"), self.ctl({"ok": True}, seen=seen))
        self.assertIn("没有这台设备", err)
        self.assertEqual(seen, [], "no Face ID card for a device that is not listed")

    def test_english(self):
        lost = self.device("iPhone Air", faceid=True)
        seen = []
        _, err = self.run_cli(cli.cmd_revoke, argparse.Namespace(device=lost),
                              self.ctl({"ok": False, "error": "uv_required", "closed": 0}, seen=seen), lang="en")
        self.assertIn("Remotes that can confirm", seen[0][1])
        self.assertIn("120 s", seen[0][1])
        self.assertIn("Not unbound", err)
        self.assertNotRegex(seen[0][1] + err, "[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]", "one language per message (P128)")


class FaceIdApproveSays(Base):
    def test_approver_exists_names_the_remotes_and_the_way_out(self):
        self.device("iPhone Air", faceid=True)
        self.device("Chrome")
        gate.set_passphrase(self.st, "correct horse battery")

        def ctl(st, req, timeout=None, **kw):
            if req["cmd"] == "status":
                return {"ok": True, "sessions": []}
            return {"ok": False, "error": "approver_exists"}
        with mock.patch.object(cli, "_secret", lambda prompt: "correct horse battery"):
            _, err = self.run_cli(cli.cmd_faceid, argparse.Namespace(action="approve", code="123456"), ctl)
        for word in ("iPhone Air", "离线", "agentj admin", "在本机设置"):
            self.assertIn(word, err)
        self.assertNotIn("Chrome", err)


class AccountUnlinkReleasesTerminal(unittest.TestCase):
    """(b) verified on the real code path: the Dashboard unlink → serve.remote_unbind → State.remove_device takes the pk record
    with it, so pkpeer.nobody_else turns true and `agentj faceid approve` may approve the remaining remote."""

    def test_dashboard_unlink_releases_faceid_approve(self):
        import asyncio
        tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP") or None)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name) / "s")
        st.init(relay="ws://127.0.0.1:1")
        host = serve.Host(st, events="jsonl", read_stdin=False)
        sent = []

        async def send_app(s, obj):
            sent.append((s.cid, obj))
            return host.sessions.get(s.cid) is s

        async def broadcast(text, frm="notice", **kw):
            pass
        host.send_app, host.broadcast = send_app, broadcast
        kp_l = Keypair.generate()
        lost = st.pair_device(kp_l.pub, "iPhone Air", _sign_pub())["device"]
        st.set_passkey(kp_l.pub, p57.pk_record(p57.Authenticator()))
        kp_c = Keypair.generate()
        chrome = st.pair_device(kp_c.pub, "Chrome", _sign_pub())["device"]
        s = serve.Session(cid=1, state="ready", device=chrome, pub=kp_c.pub, p33=True, name="Chrome")
        host.sessions[1] = s
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(host._app(s, {"t": "pk_peer_req", "r": "r1"}))
            code = [o for c, o in sent if o.get("t") == "pk_peer_res"][-1]["code"]
            self.assertEqual(loop.run_until_complete(host.pkpeer.host_approve(code)), {"ok": False, "error": "approver_exists"})
            self.assertEqual(loop.run_until_complete(host.remote_unbind("u" * 16, lost)), "revoked")
            self.assertNotIn(lost, st.devices())
            self.assertTrue(host.pkpeer.nobody_else(chrome))
            self.assertTrue(loop.run_until_complete(host.pkpeer.host_approve(code))["ok"], "the same open request, now approvable here")
            # and with no other keyed remote and no other remote online, a new request needs no approval at all
            loop.run_until_complete(host._app(s, {"t": "pk_peer_req", "r": "r2"}))
            self.assertEqual([o for c, o in sent if o.get("t") == "pk_peer_res"][-1].get("mode"), "direct")
        finally:
            for t in asyncio.all_tasks(loop):
                t.cancel()
            loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
            loop.close()


class Constants(unittest.TestCase):
    def test_card_figure_matches_serve(self):
        from agentj import lost_remote
        self.assertGreaterEqual(lost_remote.CARD_MAX, serve.ASK_TTL)
        self.assertEqual(lost_remote.CARD_MAX, 120)


if __name__ == "__main__":
    unittest.main()
