"""P547 §B (seat_transfer.py, PROTOCOL "P547"): move the seat to another Agent J host.

Unit: the code shape (`ajh_`) / mask, the handover (AES-GCM sealed + HMAC) and arrival HMACs (right code verifies; a changed
blob, relay, channel, key or another code does not), apply_handover (adds only after the HMAC, source "transfer"; never beside
an account-page `ajt_` code — review B1), the relay rule, the prompt (24 hours). Dashboard calls against the fake control plane
(strict envelopes + keys, only sha256(code) travels). CLI (in-process with `admin_check` replaced; one subprocess for the
real-terminal refusal — there is no environment switch): unbound and wrong-password refusals send nothing; the code is masked
by default, `--show` shows it, `--out` creates a NEW 0600 file (never over an existing one / a symlink); `--cancel`,
`--status`; an expired code is dropped. serve: the signed control needs this remote's own Face ID (none →
passkey_needed; two steps), the code goes only to the asking remote, every remote gets the notice; cancel; the state read;
the watcher's host_moved after a verified arrival (and never after a forged one, whose notice names nobody), also to a remote
that connects later."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

import test_p57_passkey as p57  # noqa: E402
from agentj import cloud, controls, passkey, seat_transfer as xfer, serve, wire  # noqa: E402
from agentj.noise import Keypair  # noqa: E402
from agentj.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402


def bind(st, api):
    cloud.write_cloud(st, {"api": api, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"}, "linked_at": 1,
                           "last_seq": 0, "via": "seat"})


class Unit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "old")
        self.st.init(relay="wss://relay.example.test")
        self.new = State(pathlib.Path(self.tmp.name) / "new")
        self.new.init(relay="wss://relay2.example.test")
        self.token = xfer.new_token()

    def tearDown(self):
        self.tmp.cleanup()

    def remote(self, st, name, auth=None):
        kp, sk = Keypair.generate(), ed25519.Ed25519PrivateKey.generate()
        did = st.pair_device(kp.pub, name, sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))["device"]
        if auth:
            st.set_passkey(kp.pub, p57.pk_record(auth))
        return did

    def test_code_shape_and_mask(self):
        self.assertTrue(cloud.seat_code_ok(self.token))
        self.assertRegex(self.token, r"^ajh_[A-Za-z0-9_-]{43}$", "host-issued codes are ajh_ (the account page mints ajt_)")
        self.assertTrue(xfer.host_issued(self.token))
        self.assertFalse(xfer.host_issued("ajt_" + self.token[4:]))
        self.assertNotEqual(xfer.new_token(), self.token)
        m = xfer.mask(self.token)
        self.assertEqual(m, "ajh_••••…" + self.token[-4:])
        self.assertNotIn(self.token[4:-4], m)

    def test_handover_hmac_and_apply(self):
        a = p57.Authenticator()
        d1, d2 = self.remote(self.st, "iPhone", a), self.remote(self.st, "Mac Chrome")
        ho = xfer.handover(self.st, self.token)
        self.assertEqual(json.loads(xfer.unseal(self.token, ho["b"]))["from"], cloud.channel_of(self.st))
        for clear in ("iPhone", "Mac Chrome", cloud.channel_of(self.st), self.st.devices()[d1]["pub"]):
            self.assertNotIn(clear, ho["b"], "the Dashboard stores the blob sealed (review minor 2)")
        self.assertLessEqual(len(ho["b"]), xfer.MAX_B)
        self.assertIsNone(xfer.unseal(xfer.new_token(), ho["b"]), "another code cannot open it")
        self.assertEqual(len(xfer.verify_handover(self.token, ho)), 2)
        flipped = ho["b"][:20] + ("A" if ho["b"][20] != "A" else "B") + ho["b"][21:]
        junk = wire.b64u(os.urandom(80))
        for bad in ({**ho, "b": flipped}, {**ho, "mac": xfer.mac(self.token, "x", ho["b"])},
                    {**ho, "mac": ""}, None, {"b": ho["b"]},
                    {**ho, "b": flipped, "mac": xfer.mac(self.token, xfer.HANDOVER_LABEL, flipped)},   # mac ok, GCM fails
                    {"b": junk, "mac": xfer.mac(self.token, xfer.HANDOVER_LABEL, junk)}):
            self.assertIsNone(xfer.verify_handover(self.token, bad))
        self.assertIsNone(xfer.verify_handover(xfer.new_token(), ho), "another code")
        before = self.new.devices()
        self.assertEqual(xfer.apply_handover(self.new, xfer.new_token(), ho), 0)
        self.assertEqual(self.new.devices(), before, "an unverified handover adds nothing")
        self.assertEqual(xfer.apply_handover(self.new, self.token, ho), 2)
        got = self.new.devices()
        self.assertEqual(set(got), {d1, d2}, "same keys → same device ids on the new host")
        self.assertEqual({v["source"] for v in got.values()}, {"transfer"})
        self.assertEqual(got[d1]["pk"]["id"], wire.b64u(a.id))
        self.assertEqual(got[d1]["sk"], self.st.devices()[d1]["sk"])

    def test_account_page_code_never_carries_a_handover(self):
        """Review B1: the Dashboard mints ajt_ codes itself, so it can make a 'valid' handover for one — ignored."""
        self.remote(self.st, "Evil Phone")
        ajt = "ajt_" + wire.b64u(os.urandom(32))
        forged = {"b": xfer.seal(ajt, xfer.handover_blob(self.st)), "mac": None}
        forged["mac"] = xfer.mac(ajt, xfer.HANDOVER_LABEL, forged["b"])
        self.assertIsNone(xfer.verify_handover(ajt, forged))
        self.assertEqual(xfer.apply_handover(self.new, ajt, forged), 0)
        self.assertEqual(self.new.devices(), {}, "0 devices added")
        self.assertIn('"seat_handover"', self.new.log_path.read_text())
        self.assertIn('"ignored"', self.new.log_path.read_text())
        with FakeCP() as cp:                      # and cloud.seat_bind neither sends an arrival nor returns the handover
            cp.seat_tokens.add(ajt)
            cp.bound_extra = {"handover": forged}
            res = cloud.seat_bind(self.new, cp.url, ajt, "New Agent")
            self.assertEqual(res["status"], "bound")
            self.assertNotIn("handover", res)
            self.assertNotIn("arrival", cp.seat_binds[-1])
        self.assertEqual(self.new.devices(), {})

    def test_relay_rule(self):
        for ok in ("wss://relay.example.test", "wss://relay.example.test:8443/x", "ws://127.0.0.1:8787", "ws://localhost"):
            self.assertTrue(xfer.relay_ok(ok), ok)
        for bad in ("ws://relay.example.test", "ws://127.0.0.1.evil.test", "ws://localhost.evil.test", "wss://u:p@relay.example.test",
                    "wss://u@relay.example.test", "ws://evil@127.0.0.1", "https://relay.example.test", "wss://", "wss://a b",
                    "wss://relay.example.test:99999", "WSS:relay", "", None, "wss://x\n.test"):
            self.assertFalse(xfer.relay_ok(bad), bad)
        ch, x = cloud.channel_of(self.new), wire.b64u(self.new.host_keypair().pub)
        for relay in ("ws://evil.example", "ws://127.0.0.1.evil.test", "wss://user@relay.example.test"):
            a = {"x25519": x, "relay": f"{relay}#c={ch}", "mac": xfer.mac(self.token, xfer.ARRIVAL_LABEL, f"{ch}\n{x}\n{relay}")}
            self.assertIsNone(xfer.verify_arrival(self.token, a), "a valid mac does not make a bad relay acceptable")

    def test_arrival_hmac(self):
        a = xfer.arrival(self.new, self.token)
        to = xfer.verify_arrival(self.token, a)
        self.assertEqual(to, {"relay": "wss://relay2.example.test", "channel": cloud.channel_of(self.new),
                              "hostPub": wire.b64u(self.new.host_keypair().pub)})
        self.assertEqual(set(a), {"x25519", "relay", "mac"}, "exactly the Dashboard's arrival keys")
        other = cloud.channel_of(self.st)
        for bad in ({**a, "relay": a["relay"].replace("relay2", "evil")}, {**a, "relay": a["relay"].rsplit("#c=", 1)[0] + "#c=" + other},
                    {**a, "x25519": wire.b64u(os.urandom(32))}, {**a, "mac": wire.b64u(os.urandom(32))}, {}, None):
            self.assertIsNone(xfer.verify_arrival(self.token, bad))
        self.assertIsNone(xfer.verify_arrival(xfer.new_token(), a))

    def test_prompt_says_24_hours(self):
        zh, en = xfer.prompt("acme-co", self.token)
        self.assertIn(self.token, zh)
        self.assertIn("24 小时内有效", zh)
        self.assertIn("valid 24 hours", en)
        self.assertIn("「acme-co」", zh)
        self.assertIn(xfer.INSTALL_MD, en)
        self.assertIn("「—」", xfer.prompt("Evil Name\n", self.token)[0], "only a slug is ever put in the sentence")

    def test_dashboard_calls_only_carry_the_hash(self):
        self.remote(self.st, "iPhone")
        with FakeCP() as cp:
            bind(self.st, cp.url)
            self.assertEqual(xfer.cancel(self.st), "none")
            with self.assertRaises(xfer.TransferError) as e:
                xfer.issue(self.st, "iPhone")
            self.assertEqual(e.exception.why, "not_found")
            cp.xfer_hosts.add(cloud.channel_of(self.st))
            res = xfer.issue(self.st, "iPhone")
            body = cp.xfers[-1]
            self.assertEqual(body["token_hash"], hashlib.sha256(res["token"].encode()).hexdigest())
            self.assertNotIn(res["token"], json.dumps(cp.xfers))
            self.assertIsNotNone(xfer.verify_handover(res["token"], body["handover"]))
            self.assertEqual(cp.rejected, [])
            self.assertEqual(os.stat(xfer.path(self.st)).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(xfer.path(self.st).parent).st_mode & 0o777, 0o700)
            self.assertNotIn(res["token"], self.st.log_path.read_text())
            self.assertEqual(xfer.view(self.st)["state"], "issued")
            self.assertIsNone(xfer.poll(self.st), "still issued")
            self.assertEqual(xfer.cancel(self.st), "cancelled")
            self.assertNotIn(res["token"], xfer.path(self.st).read_text(), "the code is forgotten once settled")
            self.assertEqual(xfer.view(self.st)["state"], "cancelled")

    def test_seat_bind_answer_with_handover(self):
        """New host: the bound answer's handover is only returned by cloud.py; the CLI applies it after the HMAC."""
        self.remote(self.st, "iPhone")
        code = xfer.new_token()
        ho = xfer.handover(self.st, code)
        with FakeCP() as cp:
            cp.seat_tokens.add(code)
            cp.bound_extra = {"handover": ho}
            res = cloud.seat_bind(self.new, cp.url, code, "New Agent")
            self.assertEqual(res["handover"], ho)
            self.assertEqual(self.new.devices(), {}, "cloud.seat_bind never touches the allowlist")
            self.assertIsNotNone(xfer.verify_arrival(code, cp.seat_binds[-1]["arrival"]))


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-p547-", dir=TEST_TMP)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home)
        self.env = {**os.environ, "AGENTJ_STATE_DIR": self.dir + "/s", "PYTHONPATH": str(HOST), "HOME": self.home}
        self.env.pop("AGENTJ_API_URL", None)
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.cp = FakeCP().__enter__()
        self.cp.xfer_hosts.add(cloud.channel_of(self.st))
        self.admin = "ok"

    def tearDown(self):
        self.cp.__exit__()
        shutil.rmtree(self.dir, ignore_errors=True)

    def agentj(self, *args, admin=None):
        """In-process `agentj seat …` with admin_check replaced (ok | bad | a TransferError why) → (rc, stdout, stderr)."""
        p = argparse.ArgumentParser()
        xfer.add_parser(p.add_subparsers(dest="cmd"))
        a = p.parse_args(["seat", *args])
        mode = admin or self.admin

        def check(_prompt):
            if mode not in ("ok", "bad"):
                raise xfer.TransferError(mode)
            return mode == "ok"
        out, err, rc = io.StringIO(), io.StringIO(), 0
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": self.dir + "/s"}), mock.patch.object(xfer, "admin_check", check), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                a.fn(a)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
                if isinstance(e.code, str):
                    err.write(e.code)
        return rc, out.getvalue(), err.getvalue()

    def token(self):
        return xfer.read(self.st).get("token")

    def test_unbound_and_wrong_password_send_nothing(self):
        rc, _, err = self.agentj("transfer")
        self.assertNotEqual(rc, 0)
        self.assertIn("没有绑定席位", err)
        bind(self.st, self.cp.url)
        rc, _, err = self.agentj("transfer", admin="bad")
        self.assertNotEqual(rc, 0)
        self.assertIn("管理员密码不对", err)
        self.assertEqual(self.cp.xfers, [])

    def test_real_cli_without_a_terminal_refuses_and_has_no_switch(self):
        """Review M4: no environment variable can stand in for the administrator password — not even under a test HOME."""
        bind(self.st, self.cp.url)
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "seat", "transfer"], cwd=HOST, capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL,
                           env={**self.env, "AGENTJ_TEST": "1", "AGENTJ_TEST_SEAT_ADMIN": "ok"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("真正的终端", r.stderr, "no terminal → refused before sudo is ever started")
        self.assertEqual(self.cp.xfers, [])
        self.assertNotIn("AGENTJ_TEST_SEAT_ADMIN", (HOST / "agentj" / "seat_transfer.py").read_text())
        with mock.patch.dict(os.environ, {"AGENTJ_TEST": "1", "AGENTJ_TEST_SEAT_ADMIN": "ok"}), \
                mock.patch.object(sys.stdin, "isatty", return_value=False), mock.patch("subprocess.run") as run:
            with self.assertRaises(xfer.TransferError) as e:
                xfer.admin_check("p")
            self.assertEqual(e.exception.why, "no_tty")
            run.assert_not_called()

    def test_masked_show_out_cancel_status(self):
        bind(self.st, self.cp.url)
        rc, out, err = self.agentj("transfer")
        self.assertEqual(rc, 0, err)
        tok = self.token()
        self.assertTrue(xfer.host_issued(tok))
        self.assertNotIn(tok, out + err)
        self.assertIn(xfer.mask(tok), out)
        self.assertIn("24 小时内有效", out)
        rc, out, _ = self.agentj("setup-code", "--show")
        self.assertEqual(out.strip(), tok, "setup-code reuses the waiting code")
        path = os.path.join(self.dir, "prompt.txt")
        rc, out, _ = self.agentj("transfer", "--out", path)
        tok2 = self.token()
        self.assertNotEqual(tok2, tok, "transfer issues a new code (the Dashboard voids the old one)")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertIn(tok2, pathlib.Path(path).read_text())
        self.assertNotIn(tok2, out)
        rc, out, _ = self.agentj("transfer", "--show")
        self.assertIn(self.token(), out)
        self.assertIn("issued", self.agentj("transfer", "--status")[1])
        rc, out, err = self.agentj("transfer", "--cancel", admin="no_tty")
        self.assertEqual(rc, 0, err)
        self.assertIn("已作废", out)
        self.assertIn("cancelled", self.agentj("transfer", "--status")[1])
        self.assertIsNone(self.token())
        self.assertEqual(self.cp.rejected, [])

    def test_out_never_overwrites_or_follows_a_link(self):
        """Review minor 6: O_CREAT|O_EXCL|O_NOFOLLOW, 0600 — refused before anything is issued."""
        bind(self.st, self.cp.url)
        path = pathlib.Path(self.dir) / "exists.txt"
        path.write_text("keep me")
        rc, _, err = self.agentj("transfer", "--out", str(path))
        self.assertNotEqual(rc, 0)
        self.assertIn("已存在", err)
        self.assertEqual(path.read_text(), "keep me")
        target = pathlib.Path(self.dir) / "target.txt"
        link = pathlib.Path(self.dir) / "link.txt"
        link.symlink_to(target)
        rc, _, _ = self.agentj("transfer", "--out", str(link))
        self.assertNotEqual(rc, 0)
        self.assertFalse(target.exists(), "never written through a symlink")
        self.assertEqual(self.cp.xfers, [], "nothing issued")
        fresh = pathlib.Path(self.dir) / "fresh.txt"
        rc, _, _ = self.agentj("transfer", "--out", str(fresh), admin="bad")
        self.assertNotEqual(rc, 0)
        self.assertFalse(fresh.exists(), "a refused password leaves no empty file behind")

    def test_expired_code_is_dropped(self):
        """Review minor 5: reading transfer.json past expires_at settles it `expired` and forgets the code."""
        bind(self.st, self.cp.url)
        xfer.write(self.st, {"v": 1, "state": "issued", "token": xfer.new_token(), "expires_at": int(time.time()) - 5})
        rc, out, _ = self.agentj("transfer", "--status")
        self.assertEqual(out.split()[0], "expired")
        self.assertNotIn("token", xfer.read(self.st))
        self.assertEqual(xfer.read(self.st)["state"], "expired")


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.sent = []

        async def send_app(s, obj):
            if self.host.sessions.get(s.cid) is not s:
                return False
            self.sent.append((s.cid, obj))
            return True
        self.host.send_app = send_app
        self.loop = asyncio.new_event_loop()
        self.cid = 0
        self.cp = FakeCP().__enter__()
        self.cp.xfer_hosts.add(cloud.channel_of(self.st))
        bind(self.st, self.cp.url)

    def tearDown(self):
        self.cp.__exit__()
        for t in asyncio.all_tasks(self.loop):
            t.cancel()
        pending = asyncio.all_tasks(self.loop)
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return self.loop.run_until_complete(coro)

    def to(self, s, t=None):
        return [o for c, o in self.sent if c == s.cid and (t is None or o["t"] == t)]

    def remote(self, name, auth=None, online=True):
        kp, sk = Keypair.generate(), ed25519.Ed25519PrivateKey.generate()
        did = self.st.pair_device(kp.pub, name, sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))["device"]
        if auth:
            self.st.set_passkey(kp.pub, p57.pk_record(auth))
        s = self.connect(kp, did) if online else None
        return kp, sk, did, s

    def connect(self, kp, did):
        self.cid += 1
        s = serve.Session(cid=self.cid, state="ready", device=did, pub=kp.pub, p33=True, name=self.st.devices()[did]["name"])
        self.host.sessions[self.cid] = s
        return s

    def signed(self, sk, did, action, extra=None):
        n, ts = secrets.token_hex(16), int(time.time() * 1000)
        sig = sk.sign(controls.signed_message(self.host.channel, did, action, n, ts, controls.object_digest(action, {})))
        return {"t": action, "r": "r1", "n": n, "ts": ts, "sig": wire.b64u(sig), **(extra or {})}

    def ctl(self, s):
        return self.to(s, "ctl_res")[-1]

    def issue(self, s, sk, did, auth):
        first = self.signed(sk, did, "seat_transfer")
        self.run_(self.host._app(s, first))
        res = self.ctl(s)
        self.assertEqual((res["ok"], res["why"], res["fa"]), (False, "passkey_required", wire.b64u(auth.id)))
        ch = passkey.elevate_challenge(self.host.channel, did, first["n"], "seat_transfer", first["n"], controls.object_digest("seat_transfer", {}))
        self.assertEqual(res["fa_ch"], wire.b64u(ch))
        a = auth.assert_(ch, b"u", ts=0, nonce=b"\0" * 16)
        self.run_(self.host._app(s, {**first, "fa": {k: a[k] for k in ("id", "cd", "ad", "sig")}}))
        return self.ctl(s)

    def test_no_passkey_gets_passkey_needed(self):
        _, sk, did, s = self.remote("iPad")
        self.run_(self.host._app(s, self.signed(sk, did, "seat_transfer")))
        self.assertEqual(self.ctl(s), {"t": "ctl_res", "r": "r1", "action": "seat_transfer", "ok": False, "why": "passkey_needed"})
        self.assertEqual(self.cp.xfers, [])

    def test_issue_with_face_id_code_only_to_the_asker(self):
        auth = p57.Authenticator()
        _, sk, did, s = self.remote("iPhone", auth)
        _, _, _, other = self.remote("Mac Chrome")
        res = self.issue(s, sk, did, auth)
        self.assertEqual(set(res), {"t", "r", "action", "ok", "prompt_zh", "prompt_en", "token", "expires_at"})
        self.assertTrue(res["ok"])
        tok = res["token"]
        self.assertIn(tok, res["prompt_zh"])
        self.assertNotIn(tok, json.dumps(self.to(other), ensure_ascii=False), "other remotes never see the code")
        self.assertTrue(any(o["t"] == "hist_turn" for o in self.to(other)), "… they get the notice")
        self.assertEqual(self.to(other, "seat_xfer_state")[-1]["state"], "issued")
        for f in (self.st.log_path, self.st.controls_path, self.st.approvals_path):
            self.assertNotIn(tok, f.read_text())
        line = json.loads(self.st.approvals_path.read_text().splitlines()[-1])
        from agentj import approvals
        self.assertEqual((line["tool"], approvals.check_record(self.st, line)), ("seat_transfer", "ok"))
        # the state read, then cancel (signed, no Face ID)
        self.run_(self.host._app(other, {"t": "seat_xfer_state", "r": "r2"}))
        self.assertEqual(self.to(other, "seat_xfer_state")[-1]["state"], "issued")
        self.run_(self.host._app(s, self.signed(sk, did, "seat_transfer_cancel")))
        self.assertEqual(self.ctl(s), {"t": "ctl_res", "r": "r1", "action": "seat_transfer_cancel", "ok": True, "status": "cancelled"})
        self.assertEqual(self.to(other, "seat_xfer_state")[-1]["state"], "cancelled")

    def test_bad_face_id_and_bad_signature(self):
        auth = p57.Authenticator()
        _, sk, did, s = self.remote("iPhone", auth)
        first = self.signed(sk, did, "seat_transfer")
        a = p57.Authenticator().assert_(os.urandom(32), b"u", ts=0, nonce=b"\0" * 16)
        self.run_(self.host._app(s, {**first, "fa": {k: a[k] for k in ("id", "cd", "ad", "sig")}}))
        self.assertEqual(self.ctl(s)["why"], "passkey_failed")
        bad = self.signed(ed25519.Ed25519PrivateKey.generate(), did, "seat_transfer_cancel")
        self.run_(self.host._app(s, bad))
        self.assertEqual(self.ctl(s)["why"], "bad_signature")
        self.assertEqual(self.cp.xfers, [])

    def test_watcher_host_moved_only_after_a_verified_arrival(self):
        auth = p57.Authenticator()
        _, sk, did, s = self.remote("iPhone", auth)
        kp2, _, did2, _ = self.remote("Mac Chrome", online=False)
        tok = self.issue(s, sk, did, auth)["token"]
        new = State(pathlib.Path(self.tmp.name) / "new")
        new.init(relay="wss://relay2.example.test")
        good = xfer.arrival(new, tok)
        self.cp.xfer_answer = {"status": "bound", "expires_at": 1, "agent_name": "Office Box",
                               "arrival": {**good, "mac": xfer.mac(xfer.new_token(), xfer.ARRIVAL_LABEL, "x")}}
        self.run_(self.host.seat_xfer.tick())
        self.assertEqual(self.to(s, "host_moved"), [], "a forged arrival moves nobody")
        self.assertEqual(xfer.view(self.st)["state"], "bound")
        said = json.dumps(self.to(s, "hist_turn")[-1], ensure_ascii=False)
        self.assertIn("席位转移码已被使用", said)
        self.assertNotIn("Office Box", said, "an unverified Dashboard name is never repeated (review minor 3)")
        self.assertNotIn("Office Box", xfer.path(self.st).read_text())
        # a fresh transfer, this time with the right arrival
        tok = self.issue(s, sk, did, auth)["token"]
        good = xfer.arrival(new, tok)
        self.cp.xfer_answer = {"status": "bound", "expires_at": 1, "agent_name": "Office Box", "arrival": good}
        self.run_(self.host.seat_xfer.tick())
        got = self.to(s, "host_moved")
        self.assertEqual(len(got), 1)
        self.assertIsInstance(got[0]["at"], int)
        self.assertLessEqual(abs(got[0]["at"] - time.time()), 60)
        moved = {"t": "host_moved", "relay": "wss://relay2.example.test", "channel": cloud.channel_of(new),
                 "hostPub": wire.b64u(new.host_keypair().pub), "name": "Office Box", "at": got[0]["at"]}
        self.assertEqual(got, [moved])
        self.assertEqual(xfer.view(self.st)["state"], "moved")
        self.assertNotIn(tok, xfer.path(self.st).read_text())
        later = self.connect(kp2, did2)                    # a remote that was away hears it when it connects
        self.run_(self.host.seat_xfer.on_ready(later))
        self.assertEqual(self.to(later, "host_moved"), [moved])

    def test_terminal_notice_is_announced_once(self):
        _, _, _, s = self.remote("iPhone")
        xfer.issue(self.st, "")
        self.run_(self.host.seat_xfer.from_terminal())
        self.run_(self.host.seat_xfer.from_terminal())
        self.assertEqual(len([o for o in self.to(s, "hist_turn")]), 1)
        self.assertEqual(self.to(s, "seat_xfer_state")[-1]["state"], "issued")


if __name__ == "__main__":
    unittest.main()
