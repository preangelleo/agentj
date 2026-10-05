"""F17 (PROTOCOL §11): the sudo card and the secret card.

Crypto: the seal opens only with the card's own host key, device, id, kind, nonce and shown digest; the signature binds the
decision, nonce, ts, digest and ciphertext. Flows over a real `serve` Host (fake phone sessions, a real unix socket for the
Agent's CLI): approve → the command's output only; expired / replayed / changed-command / unpaired / unsigned answers do
nothing; a wrong password re-arms the card with a new nonce + host key, three wrong lock sudo; a secret is written 0600, the
receipt has its length + fingerprint only, the check runs host-side. The password / secret never appears in any log, in the
messages to the phone, in the Agent's result, or in the (stand-in) sudo's argv or environment.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from agentj import elevate, wire  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402

PW = "Pw-明文-F17-s3cr3t!"
KEY = "f17-test-key-DO-NOT-LEAK-0123456789abcdef"
AJ = pathlib.Path(__file__).resolve().parents[2]   # agentjarvis/ here; the repo root in the public export
NODE = shutil.which("node") or str(pathlib.Path.home() / ".local/share/mise/installs/node/26.7.0/bin/node")


def fake_sudo(d: pathlib.Path, password: str) -> pathlib.Path:
    """A stand-in for sudo: honours -S -k -p <prompt> --, reads one password line, asks again after a wrong one (like
    sudo), records its own argv + environment + what it read (hash only) to a file, then runs the command."""
    rec = d / "sudo-record.jsonl"
    p = d / "fakesudo"
    p.write_text(f"""#!{sys.executable}
import hashlib, json, os, subprocess, sys
a = sys.argv[1:]
assert a[:2] == ["-S", "-k"] and a[2] == "-p" and a[4] == "--", a
prompt, argv = a[3], a[5:]
want = {hashlib.sha256(password.encode()).hexdigest()!r}
def rd():
    sys.stderr.write(prompt); sys.stderr.flush()
    return sys.stdin.buffer.readline()
line = rd()
ok = hashlib.sha256(line.rstrip(b"\\n")).hexdigest() == want
with open({str(rec)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv, "env": dict(os.environ), "ok": ok, "read_sha": hashlib.sha256(line).hexdigest()}}) + "\\n")
if not ok:
    sys.stderr.write("Sorry, try again.\\n")
    if not rd():
        sys.stderr.write("sudo: 1 incorrect password attempt\\n"); sys.exit(1)
    sys.exit(1)
sys.exit(subprocess.run(argv).returncode)
""")
    p.chmod(0o700)
    return p


def answer(ph: Phone, card: dict, ok: bool, value: str | None = None, *, nonce=None, ts=None, digest=None, sk=None,
           channel=None, rid=None) -> dict:
    """What the phone sends: seal the value to the card's host key, sign the decision (protocol/wire.js does the same)."""
    rid = rid or card["id"]
    nonce = nonce or card["n"]
    digest = digest or elevate.shown_digest(card["kind"], elevate.shown_fields(card))
    ts = int(time.time() * 1000) if ts is None else ts
    ch = channel or ph.channel
    m = {"t": "elev_answer", "id": rid, "ok": ok, "n": nonce, "ts": ts}
    ct_sha = None
    if ok:
        epk, blob = elevate.seal(wire.unb64u(card["epk"]), (value or "").encode(),
                                 elevate.seal_aad(ch, ph.did, rid, card["kind"], nonce, digest))
        m["epk"], m["ct"] = wire.b64u(epk), wire.b64u(blob)
        ct_sha = hashlib.sha256(blob).hexdigest()
    msg = elevate.signed_message(ch, ph.did, rid, card["kind"], "allow" if ok else "deny", nonce, ts, digest, ct_sha)
    m["sig"] = wire.b64u((sk or ph.sk).sign(msg))
    return m


class Crypto(unittest.TestCase):
    def test_seal_round_trip_and_binding(self):
        priv = X25519PrivateKey.generate()
        pub = elevate._raw_pub(priv)
        aad = elevate.seal_aad("ch", "dev", "a" * 32, "sudo", "n1", "d1")
        epk, blob = elevate.seal(pub, PW.encode(), aad)
        self.assertEqual(bytes(elevate.open_sealed(priv, epk, blob, aad)), PW.encode())
        self.assertNotIn(PW.encode(), blob)
        for bad in (elevate.seal_aad("ch2", "dev", "a" * 32, "sudo", "n1", "d1"),
                    elevate.seal_aad("ch", "dev2", "a" * 32, "sudo", "n1", "d1"),
                    elevate.seal_aad("ch", "dev", "b" * 32, "sudo", "n1", "d1"),
                    elevate.seal_aad("ch", "dev", "a" * 32, "secret", "n1", "d1"),
                    elevate.seal_aad("ch", "dev", "a" * 32, "sudo", "n2", "d1"),
                    elevate.seal_aad("ch", "dev", "a" * 32, "sudo", "n1", "d2")):
            with self.assertRaises(ValueError):
                elevate.open_sealed(priv, epk, blob, bad)
        with self.assertRaises(ValueError):                 # another card's host key
            elevate.open_sealed(X25519PrivateKey.generate(), epk, blob, aad)
        tampered = bytearray(blob)
        tampered[20] ^= 1
        with self.assertRaises(ValueError):
            elevate.open_sealed(priv, epk, bytes(tampered), aad)

    def test_digest_covers_every_shown_field(self):
        base = {"kind": "sudo", "cmd": "apt install x", "why": "w", "effect": "e"}
        d = elevate.shown_digest("sudo", elevate.shown_fields(base))
        for k in ("cmd", "why", "effect"):
            self.assertNotEqual(d, elevate.shown_digest("sudo", elevate.shown_fields({**base, k: base[k] + "!"})))
        s = {"kind": "secret", "name": "A", "purpose": "p", "dest": "/x", "verify": ""}
        ds = elevate.shown_digest("secret", elevate.shown_fields(s))
        for k in ("name", "purpose", "dest", "verify"):
            self.assertNotEqual(ds, elevate.shown_digest("secret", elevate.shown_fields({**s, k: s[k] + "!"})))

    def test_wipe(self):
        b = bytearray(b"abc")
        elevate.wipe(b)
        self.assertEqual(b, bytearray(3))
        x = bytes(bytearray(b"wipe-me-please"))
        elevate.wipe(x)
        self.assertEqual(x, bytes(14))

    @unittest.skipUnless(os.path.exists(NODE), "node not installed")
    def test_js_seal_and_signature_open_in_python(self):
        """protocol/wire.js sealValue / elevateMessage / elevateDigest produce what the host checks."""
        priv = X25519PrivateKey.generate()
        card = {"id": "c" * 32, "kind": "sudo", "n": "f" * 32, "epk": wire.b64u(elevate._raw_pub(priv)),
                "cmd": "apt-get install -y ffmpeg", "why": "转码要用", "effect": "装一个包"}
        js = f"""
import {{ sealValue, elevateDigest, elevateMessage, elevateFields, b64u, unb64u }} from {json.dumps((AJ / 'protocol/wire.js').as_uri())};
const card = {json.dumps(card)};
const digest = await elevateDigest(card.kind, elevateFields(card));
const s = await sealValue(unb64u(card.epk), new TextEncoder().encode({json.dumps(PW)}), 'ch', 'dev', card.id, card.kind, card.n, digest);
const kp = await crypto.subtle.generateKey({{ name: 'Ed25519' }}, true, ['sign', 'verify']);
const msg = await elevateMessage('ch', 'dev', card.id, card.kind, 'allow', card.n, 1234, digest, s.ctSha);
const sig = new Uint8Array(await crypto.subtle.sign({{ name: 'Ed25519' }}, kp.privateKey, msg));
const pub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
console.log(JSON.stringify({{ digest, epk: b64u(s.epk), ct: b64u(s.ct), sig: b64u(sig), pub: b64u(pub) }}));
"""
        out = subprocess.run([NODE, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        digest = elevate.shown_digest("sudo", elevate.shown_fields(card))
        self.assertEqual(r["digest"], digest)
        v = elevate.open_sealed(priv, wire.unb64u(r["epk"]), wire.unb64u(r["ct"]),
                                elevate.seal_aad("ch", "dev", card["id"], "sudo", card["n"], digest))
        self.assertEqual(bytes(v).decode(), PW)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        ct_sha = hashlib.sha256(wire.unb64u(r["ct"])).hexdigest()
        Ed25519PublicKey.from_public_bytes(wire.unb64u(r["pub"])).verify(
            wire.unb64u(r["sig"]), elevate.signed_message("ch", "dev", card["id"], "sudo", "allow", card["n"], 1234, digest, ct_sha))


class Units(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sudo_request_shape(self):
        c = elevate.norm_sudo({"argv": ["apt-get", "install", "-y", "a b"], "why": "x", "cwd": str(self.d)})
        self.assertEqual(c["cmd"], "apt-get install -y 'a b'")
        for bad in ({"argv": [], "why": "x"}, {"argv": ["ls"], "why": ""}, {"argv": ["ls", ""], "why": "x"},
                    {"argv": ["ls"], "why": "x\x07"}, {"argv": ["a" * 2100], "why": "x"},
                    {"argv": ["ls"], "why": "x", "timeout": 99999}, {"argv": "ls", "why": "x"}):
            with self.assertRaises(elevate.Refused):
                elevate.norm_sudo(bad)

    def test_dest_rules(self):
        work = self.d / "proj"
        work.mkdir()
        d = elevate.parse_dest(".env", "K", str(work), self.st.root)
        self.assertEqual((d["mode"], d["path"], d["key"]), ("env", str(work / ".env"), "K"))
        d = elevate.parse_dest("env:conf/x.txt#OTHER", "K", str(self.d), self.st.root) if (self.d / "conf").mkdir() is None else None
        self.assertEqual((d["mode"], d["key"]), ("env", "OTHER"))
        self.assertEqual(elevate.parse_dest("file:key.txt", "K", str(work), self.st.root)["mode"], "file")
        for dest in (str(self.st.root / "x.env"), "file:" + str(self.st.root / "agentperm" / "k"), str(self.d / "nope" / "k"),
                     "env:" + str(work / ".env") + "#1bad"):
            with self.assertRaises(elevate.Refused, msg=dest):
                elevate.parse_dest(dest, "K", str(work), self.st.root)
        (work / "link").symlink_to(self.d / "elsewhere")
        with self.assertRaises(elevate.Refused):
            elevate.parse_dest("file:link", "K", str(work), self.st.root)
        (self.st.root / "sub").mkdir()
        (work / "into-state").symlink_to(self.st.root / "sub")
        with self.assertRaises(elevate.Refused):                       # a symlinked folder into the state dir
            elevate.parse_dest("file:into-state/k", "K", str(work), self.st.root)

    def test_write_secret_env_keeps_the_rest_and_is_0600(self):
        p = self.d / ".env"
        p.write_text("# top\nA=1\nexport ELEVEN=old\nB=2\nELEVEN=dup\n")
        p.chmod(0o644)
        spec = elevate.parse_dest(str(p), "ELEVEN", None, self.st.root)
        elevate.write_secret(spec, bytearray(KEY.encode()))
        self.assertEqual(p.read_text(), f"# top\nA=1\nexport ELEVEN={KEY}\nB=2\n")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        elevate.write_secret(spec, bytearray(b'has space "q" $x'))
        self.assertIn('export ELEVEN="has space \\"q\\" \\$x"', p.read_text())
        with self.assertRaises(elevate.Refused):
            elevate.write_secret(spec, bytearray(b"two\nlines"))
        f = elevate.parse_dest("file:" + str(self.d / "k.pem"), "K", None, self.st.root)
        elevate.write_secret(f, bytearray(b"-----BEGIN-----\nabc\n"))
        self.assertEqual((self.d / "k.pem").read_bytes(), b"-----BEGIN-----\nabc\n")
        self.assertEqual(stat.S_IMODE((self.d / "k.pem").stat().st_mode), 0o600)
        self.assertEqual(sorted(x.name for x in self.d.iterdir() if ".agentj-" in x.name), [])

    def test_run_sudo_password_only_on_stdin(self):
        sudo = fake_sudo(self.d, PW)
        r = elevate.run_sudo(["sh", "-c", "echo out; echo err >&2; exit 3"], bytearray(PW.encode()), str(self.d), 30, str(sudo))
        self.assertEqual((r["result"], r["code"], r["stdout"], r["stderr"]), ("done", 3, "out\n", "err\n"))
        r = elevate.run_sudo(["true"], bytearray(b"wrong"), str(self.d), 30, str(sudo))
        self.assertEqual(r["result"], "bad_password")
        r = elevate.run_sudo(["sleep", "5"], bytearray(PW.encode()), str(self.d), 1, str(sudo))
        self.assertEqual(r["result"], "timeout")
        for ln in (self.d / "sudo-record.jsonl").read_text().splitlines():
            rec = json.loads(ln)
            self.assertNotIn(PW, json.dumps(rec["argv"], ensure_ascii=False))
            self.assertNotIn(PW, json.dumps(rec["env"], ensure_ascii=False))
            self.assertEqual(rec["env"].get("LC_ALL"), "C")
        self.assertEqual(elevate.run_sudo(["true"], bytearray(b"x"), "/", 5, str(self.d / "missing"))["result"], "no_sudo")

    def test_lock_ledger(self):
        self.assertEqual(elevate.note_password(self.st, False), 0)
        self.assertEqual(elevate.note_password(self.st, False), 0)
        self.assertGreater(elevate.note_password(self.st, False), 50)
        self.assertGreater(elevate.locked_for(self.st), 50)
        self.assertEqual(stat.S_IMODE(elevate.ledger_path(self.st).stat().st_mode), 0o600)


class Http(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200 if self.headers.get("xi-api-key") == KEY else 401)
        self.end_headers()

    def log_message(self, *a):
        pass


class Flows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.sent = []
        self.sudo = fake_sudo(self.d, PW)

    def tearDown(self):
        self.tmp.cleanup()

    def cards(self):
        return [o for _, o in self.sent if o["t"] == "elev"]

    def done(self):
        return [o for _, o in self.sent if o["t"] == "elev_done"]

    def run_flow(self, req: dict, script, *, phone=True, with_key=True):
        """Start a real elevate socket, send `req` like the CLI does, let `script(host, session, phone)` play the phone."""
        host = _host(self.st, self.sent)
        host.elevate.sudo = str(self.sudo)
        ph = Phone(self.st, with_key=with_key) if phone else None
        s = _ready(host, ph) if ph else None

        async def go():
            await host.elevate.start()
            try:
                t = asyncio.create_task(asyncio.to_thread(elevate.client_request, self.st, req, 30))
                if script:
                    await script(host, s, ph)
                return await t
            finally:
                await host.elevate.stop()
        return asyncio.run(go()), host, ph

    async def wait_card(self, n=1):
        for _ in range(500):
            if len(self.cards()) >= n:
                return self.cards()[n - 1]
            await asyncio.sleep(0.01)
        raise AssertionError("no card")

    def assert_no_leak(self, *values, result=None):
        files = [self.st.log_path, elevate.log_path(self.st), self.st.approvals_path]
        for f in files:
            if f.exists():
                for v in values:
                    self.assertNotIn(v, f.read_text(), f)
        blob = json.dumps(self.sent, ensure_ascii=False) + json.dumps(result or {}, ensure_ascii=False)
        for v in values:
            self.assertNotIn(v, blob)
            self.assertNotIn(wire.b64u(v.encode()), blob)

    REQ = {"t": "sudo", "argv": ["sh", "-c", "echo installed; id -un >/dev/null"], "why": "装 ffmpeg 转码", "effect": "装一个系统包",
           "timeout": 20}

    def test_sudo_approve_returns_only_the_output(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            self.assertEqual((c["kind"], c["cmd"], c["why"], c["effect"]), ("sudo", "sh -c 'echo installed; id -un >/dev/null'",
                                                                             "装 ffmpeg 转码", "装一个系统包"))
            self.assertLessEqual(c["ttl"], 120)
            await host._app(s, answer(ph, c, True, PW))
        res, host, ph = self.run_flow({**self.REQ, "cwd": str(self.d)}, script)
        self.assertEqual((res["result"], res["code"], res["stdout"]), ("done", 0, "installed\n"))
        self.assertEqual(self.done()[-1]["result"], "done")
        log = elevate.read_log(self.st)
        self.assertEqual(log[-1]["result"], "done")
        self.assertEqual(elevate.check_record(self.st, log[-1]), "ok")
        self.assertEqual(stat.S_IMODE(elevate.log_path(self.st).stat().st_mode), 0o600)
        self.assert_no_leak(PW, result=res)

    def test_deny_expire_replay_changed_command_unpaired(self):
        other = None

        async def script(host, s, ph):
            nonlocal other
            c = await self.wait_card()
            good = answer(ph, c, True, PW)
            # a different command than the one shown (digest of another text) → bad signature / seal, nothing runs
            await host._app(s, answer(ph, {**c, "cmd": "rm -rf /"}, True, PW))
            # a stale timestamp, a wrong nonce, another channel, a key that is not this device's
            await host._app(s, answer(ph, c, True, PW, ts=int(time.time() * 1000) - 600_000))
            await host._app(s, answer(ph, c, True, PW, nonce="0" * 32))
            await host._app(s, answer(ph, c, True, PW, channel="AAAAAAAAAAAAAAAAAAAAAA"))
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            await host._app(s, answer(ph, c, True, PW, sk=Ed25519PrivateKey.generate()))
            # an unpaired device's session is closed before anything is acted on
            other = Phone(self.st)
            s2 = _ready(host, other, cid=12)
            self.st.remove_device(other.did)
            await host._app(s2, answer(other, c, True, PW))
            self.assertFalse(host.elevate.cards[c["id"]]["fut"].done())
            await host._app(s, answer(ph, c, False))                         # deny
            await asyncio.sleep(0.05)
            await host._app(s, good)                                          # too late: the card is gone
        res, host, ph = self.run_flow({**self.REQ}, script)
        self.assertEqual(res["result"], "denied")
        reasons = [r.get("reason") for r in elevate.read_log(self.st) if r.get("result") == "refused"]
        self.assertIn("bad_signature", reasons)
        self.assertIn("stale", reasons)
        self.assertIn("replay", reasons)
        self.assertFalse((self.d / "sudo-record.jsonl").exists(), "sudo never ran")
        self.assert_no_leak(PW, result=res)

    def test_timeout(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            host.elevate.cards[c["id"]]["deadline"] = time.monotonic() + 0.2
            await asyncio.sleep(0.4)
            await host._app(s, answer(ph, c, True, PW))
        res, host, _ = self.run_flow({**self.REQ}, script)
        self.assertEqual(res["result"], "timeout")
        self.assertFalse((self.d / "sudo-record.jsonl").exists())

    def test_no_approver_and_unavailable(self):
        res, _, _ = self.run_flow({**self.REQ}, None, phone=False)
        self.assertEqual(res["result"], "no_device")
        res, _, _ = self.run_flow({**self.REQ}, None, with_key=False)
        self.assertEqual(res["result"], "no_device")
        self.assertEqual(elevate.client_request(self.st, self.REQ, 2)["result"], "unavailable")

    def test_wrong_password_rearms_then_locks(self):
        async def script(host, s, ph):
            c1 = await self.wait_card(1)
            await host._app(s, answer(ph, c1, True, "wrong-1"))
            c2 = await self.wait_card(2)
            self.assertEqual(c2["id"], c1["id"])
            self.assertNotEqual((c2["n"], c2["epk"]), (c1["n"], c1["epk"]))
            self.assertEqual(c2["bad"], 1)
            await host._app(s, answer(ph, c1, True, PW))        # the old nonce: refused as a replay
            self.assertFalse(host.elevate.cards[c1["id"]]["fut"].done())
            await host._app(s, answer(ph, c2, True, PW))
        res, _, _ = self.run_flow({**self.REQ}, script)
        self.assertEqual(res["result"], "done")

        self.sent.clear()

        async def script2(host, s, ph):
            for i in range(1, 4):
                c = await self.wait_card(i)
                await host._app(s, answer(ph, c, True, f"wrong-{i}"))
        res, _, _ = self.run_flow({**self.REQ}, script2)
        self.assertEqual(res["result"], "locked")
        self.sent.clear()
        res, _, _ = self.run_flow({**self.REQ}, None)
        self.assertEqual(res["result"], "locked")
        self.assertEqual(self.cards(), [])
        self.assert_no_leak(PW, "wrong-1", "wrong-2", "wrong-3", result=res)

    def test_estop_and_withdraw(self):
        async def script(host, s, ph):
            await self.wait_card()
            host.elevate.cancel_all("stopped")
        res, _, _ = self.run_flow({**self.REQ}, script)
        self.assertEqual(res["result"], "stopped")

        self.sent.clear()
        host = _host(self.st, self.sent)
        _ready(host, Phone(self.st))

        async def go():
            await host.elevate.start()
            try:
                r, w = await asyncio.open_unix_connection(str(host.elevate.sock_path))
                w.write((json.dumps(self.REQ) + "\n").encode())
                await w.drain()
                await self.wait_card()
                w.close()                                          # the Agent's CLI went away
                for _ in range(200):
                    if self.done():
                        break
                    await asyncio.sleep(0.01)
            finally:
                await host.elevate.stop()
        asyncio.run(go())
        self.assertEqual(self.done()[-1]["result"], "gone")

    def test_secret_saved_receipt_and_checks(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), Http)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/v1/user"
        work = self.d / "proj"
        work.mkdir()
        (work / ".env").write_text("OTHER=1\n")
        req = {"t": "secret", "name": "ELEVENLABS_API_KEY", "purpose": "配音", "dest": ".env", "cwd": str(work),
               "verify_url": url, "verify_header": "xi-api-key: {value}"}
        try:
            async def script(host, s, ph):
                c = await self.wait_card()
                self.assertEqual((c["kind"], c["name"], c["purpose"]), ("secret", "ELEVENLABS_API_KEY", "配音"))
                self.assertIn(str(work / ".env"), c["dest"])
                self.assertIn(url, c["verify"])
                await host._app(s, answer(ph, c, True, KEY))
            res, _, _ = self.run_flow(req, script)
            self.assertEqual(res["result"], "saved")
            r = res["receipt"]
            self.assertEqual((r["name"], r["length"], r["verify"], r["detail"]), ("ELEVENLABS_API_KEY", len(KEY), "ok", "HTTP 200"))
            self.assertEqual(r["fingerprint"], "sha256:" + hashlib.sha256(KEY.encode()).hexdigest()[:8])
            self.assertEqual((work / ".env").read_text(), f"OTHER=1\nELEVENLABS_API_KEY={KEY}\n")
            self.assertEqual(stat.S_IMODE((work / ".env").stat().st_mode), 0o600)
            self.assertEqual(self.done()[-1]["verify"], "ok")
            self.assert_no_leak(KEY, result=res)

            self.sent.clear()

            async def script2(host, s, ph):
                c = await self.wait_card()
                await host._app(s, answer(ph, c, True, "not-the-key"))
            res, _, _ = self.run_flow(req, script2)
            self.assertEqual((res["result"], res["receipt"]["verify"], res["receipt"]["detail"]), ("saved", "fail", "HTTP 401"))
        finally:
            srv.shutdown()
            srv.server_close()

        self.sent.clear()
        cmd_req = {"t": "secret", "name": "TOK", "purpose": "p", "dest": "file:tok.txt", "cwd": str(work),
                   "verify_cmd": 'test "$TOK" = "' + KEY + '"'}

        async def script3(host, s, ph):
            c = await self.wait_card()
            self.assertIn("$TOK", c["verify"])
            await host._app(s, answer(ph, c, True, KEY))
        res, _, _ = self.run_flow(cmd_req, script3)
        self.assertEqual((res["receipt"]["verify"], res["receipt"]["detail"]), ("ok", "exit 0"))
        self.assertEqual((work / "tok.txt").read_text(), KEY)

    def test_secret_refusals(self):
        for req in ({"t": "secret", "name": "1X", "purpose": "p", "dest": "/tmp/x"},
                    {"t": "secret", "name": "X", "purpose": "", "dest": "/tmp/x"},
                    {"t": "secret", "name": "X", "purpose": "p", "dest": str(self.st.root / "host_x25519.key")},
                    {"t": "secret", "name": "X", "purpose": "p", "dest": "/tmp/x", "verify_url": "http://example.com/"},
                    {"t": "secret", "name": "X", "purpose": "p", "dest": "/tmp/x", "verify_url": "https://a/", "verify_cmd": "true"},
                    {"t": "nope"}):
            res, _, _ = self.run_flow(req, None, phone=False)
            self.assertEqual(res["result"], "refused", req)
        self.assertEqual(self.cards(), [])


def sandbox_helper(d: pathlib.Path, keys: dict) -> list[str]:
    """The installed helper's exact source with only its root-owned paths moved into `d` and the euid check lifted (a test
    cannot be root) — everything that decides (signature, digest, ts, nonce ledger) is the shipped code."""
    from agentj import elevate_helper
    src = elevate_helper.HELPER_SOURCE
    for a, b in ((elevate_helper.KEYS, str(d / "keys.json")), (elevate_helper.STATE, str(d / "hstate"))):
        assert src.count(f'"{a}"') == 1
        src = src.replace(f'"{a}"', repr(b))
    assert src.count("if os.geteuid() != 0:") == 1
    src = src.replace("if os.geteuid() != 0:", "if False:")
    (d / "keys.json").write_text(json.dumps(keys))
    h = d / "helper.py"
    h.write_text(src)
    return [sys.executable, "-I", "-S", str(h)]


def fake_sudo_n(d: pathlib.Path) -> pathlib.Path:
    """`sudo -n -- cmd…`: stdin passed through, nothing read as a password; records its argv / env."""
    p = d / "fakesudo-n"
    p.write_text(f"""#!{sys.executable}
import json, os, subprocess, sys
a = sys.argv[1:]
assert a[:2] == ["-n", "--"], a
with open({str(d / 'sudo-n-record.jsonl')!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv, "env": dict(os.environ)}}) + "\\n")
sys.exit(subprocess.run(a[2:]).returncode)
""")
    p.chmod(0o700)
    return p


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def ns(self):
        from agentj import elevate_helper
        g = {"__name__": "agentj_elevate_under_test"}
        exec(compile(elevate_helper.HELPER_SOURCE, "agentj-elevate", "exec"), g)
        return g

    def test_pure_python_ed25519_matches_openssl(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        g = self.ns()
        for i in range(6):
            k = Ed25519PrivateKey.generate()
            pub = k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            msg = os.urandom(i * 37)
            sig = k.sign(msg)
            self.assertTrue(g["ed25519_verify"](pub, msg, sig))
            self.assertFalse(g["ed25519_verify"](pub, msg + b"x", sig))
            bad = bytearray(sig); bad[i] ^= 1
            self.assertFalse(g["ed25519_verify"](pub, msg, bytes(bad)))
            self.assertFalse(g["ed25519_verify"](pub, msg, sig[:32] + (int.from_bytes(sig[32:], "little") + g["L"]).to_bytes(33, "little")[:32]))

    def test_install_script_and_sudoers(self):
        from agentj import elevate_helper as eh
        Phone(self.st)
        fs = eh.files(self.st, "someone")
        hashes = {n: eh.sha(v) for n, v in fs.items()}
        for mac in (False, True):
            sc = eh.install_script("/x/agentperm/helper-abc", hashes, mac=mac)
            c = elevate.norm_sudo({"argv": ["sh", "-c", sc], "why": "w"})       # fits on a card
            self.assertLessEqual(len(c["cmd"]), 2000)
            # root-owned snapshot first, then the hash check, then install; sudoers only after visudo
            order = [sc.index(x) for x in ("install -m 0600 -o root", "-c --quiet -", eh.HELPER, "visudo -cqf sudoers", eh.SUDOERS)]
            self.assertEqual(order, sorted(order))
            for n, h in hashes.items():
                self.assertIn(f"{h}  {n}", sc)
            self.assertIn("-g wheel" if mac else "-g root", sc)
            self.assertIn("shasum -a 256 -c" if mac else "sha256sum -c", sc)
        self.assertEqual(eh.sudoers_text("someone").splitlines()[-1],
                         "someone ALL=(root) NOPASSWD: /usr/bin/python3 -I -S /usr/local/libexec/agentj-elevate")
        with self.assertRaises(ValueError):
            eh.sudoers_text("a b")
        keys = json.loads(fs["elevate-keys.json"])
        self.assertEqual(keys["channel"], self.st.config()["channel"])
        self.assertEqual(len(keys["devices"]), 1)
        if shutil.which("visudo"):
            f = self.d / "sudoers"
            f.write_bytes(fs["sudoers"])
            self.assertEqual(subprocess.run(["visudo", "-cqf", str(f)], capture_output=True).returncode, 0)
        # the shipped helper is standalone: it parses with -I -S and imports only the standard library
        src = fs["agentj-elevate"].decode()
        self.assertTrue(src.startswith("#!/usr/bin/python3 -I -S"))
        self.assertNotIn("agentj", "".join(l for l in src.splitlines() if l.startswith(("import", "from"))))

    def helper_req(self, ph, argv, *, ts=None, n=None, why="w", effect="", device=None, sk=None):
        ch = self.st.config()["channel"]
        n = n or os.urandom(16).hex()
        ts = int(time.time() * 1000) if ts is None else ts
        card = {"kind": "sudo", "cmd": shlex_join(argv), "why": why, "effect": effect}
        dg = elevate.shown_digest("sudo", elevate.shown_fields(card))
        rid = os.urandom(16).hex()
        sig = (sk or ph.sk).sign(elevate.signed_message(ch, device or ph.did, rid, "sudo", "allow", n, ts, dg, None))
        return {"channel": ch, "device": device or ph.did, "id": rid, "n": n, "ts": ts, "sig": wire.b64u(sig), "argv": argv,
                "why": why, "effect": effect, "cwd": str(self.d)}

    def test_helper_runs_only_signed_fresh_unreplayed(self):
        from agentj import elevate_helper as eh
        ph = Phone(self.st)
        cmd = sandbox_helper(self.d, eh.keys_doc(self.st))
        run = lambda req: subprocess.run(cmd, input=json.dumps(req).encode(), capture_output=True, timeout=60)  # noqa: E731
        req = self.helper_req(ph, ["sh", "-c", "echo as-root"])
        r = run(req)
        self.assertEqual((r.returncode, r.stdout), (0, b"as-root\n"), r.stderr)
        self.assertIn(b"replay", run(req).stderr)
        bad = dict(self.helper_req(ph, ["sh", "-c", "echo as-root"]), argv=["sh", "-c", "echo other"])
        self.assertIn(b"signature", run(bad).stderr)                         # another command than the phone signed
        self.assertIn(b"stale", run(self.helper_req(ph, ["true"], ts=int(time.time() * 1000) - 600_000)).stderr)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.assertIn(b"signature", run(self.helper_req(ph, ["true"], sk=Ed25519PrivateKey.generate())).stderr)
        self.assertIn(b"shape", run(self.helper_req(ph, ["true"], device="AAAAAAAAAAAAAAAA")).stderr)   # not in the allowlist
        self.assertIn(b"channel", run(dict(self.helper_req(ph, ["true"]), channel="x")).stderr)
        log = (self.d / "hstate" / "log").read_text()
        self.assertEqual(len(log.splitlines()), 1)
        self.assertNotIn("as-root", log)

    def test_serve_approve_only_through_helper(self):
        from agentj import elevate_helper as eh
        ph = Phone(self.st)
        cmd = sandbox_helper(self.d, eh.keys_doc(self.st))
        host = _host(self.st, self.sent)
        host.elevate.sudo = str(fake_sudo_n(self.d))
        host.elevate.helper_cmd = cmd
        host.elevate.helper_status = lambda: {"installed": True, "channel": self.st.config()["channel"], "devices": [ph.did]}
        s = _ready(host, ph)
        other = Phone(self.st, name="另一台")
        s2 = _ready(host, other, cid=12)

        async def go():
            await host.elevate.start()
            try:
                t = asyncio.create_task(asyncio.to_thread(elevate.client_request, self.st,
                                                          {"t": "sudo", "argv": ["sh", "-c", "echo helper-ok"], "why": "w"}, 30))
                for _ in range(500):
                    cards = [o for _, o in self.sent if o["t"] == "elev"]
                    if cards:
                        break
                    await asyncio.sleep(0.01)
                c = cards[0]
                self.assertEqual(c["helper"], [ph.did])
                # approve-only from a phone the helper does not list → refused, nothing runs
                await host._app(s2, approve_only(other, c))
                self.assertFalse(host.elevate.cards[c["id"]]["fut"].done())
                await host._app(s, approve_only(ph, c))
                return await t
            finally:
                await host.elevate.stop()
        res = asyncio.run(go())
        self.assertEqual((res["result"], res["code"], res["stdout"]), ("done", 0, "helper-ok\n"), res)
        rec = json.loads((self.d / "sudo-n-record.jsonl").read_text().splitlines()[0])
        self.assertEqual(rec["argv"][1:3], ["-n", "--"])
        self.assertEqual(elevate.read_log(self.st)[-1]["reason"], "helper")
        self.assertEqual(elevate.check_record(self.st, elevate.read_log(self.st)[-1]), "ok")


def approve_only(ph: Phone, card: dict) -> dict:
    ts = int(time.time() * 1000)
    dg = elevate.shown_digest("sudo", elevate.shown_fields(card))
    sig = ph.sk.sign(elevate.signed_message(ph.channel, ph.did, card["id"], "sudo", "allow", card["n"], ts, dg, None))
    return {"t": "elev_answer", "id": card["id"], "ok": True, "n": card["n"], "ts": ts, "sig": wire.b64u(sig)}


def shlex_join(argv):
    import shlex
    return shlex.join(argv)


class Identity(unittest.TestCase):
    def test_agent_is_told_to_use_the_cards(self):
        from agentj import main_identity
        main_identity.verify_core()                      # the hashed core is untouched; the line is host-generated
        for lang in ("zh", "en"):
            text = main_identity.prompt({"language": lang})
            self.assertIn("agentj sudo --why", text)
            self.assertIn("agentj secret request --name", text)
            self.assertLess(text.index("agentj sudo"), len(text))
        skill = (pathlib.Path(__file__).resolve().parents[1] / "agentj/skills/agentj-config/SKILL.md").read_text()
        self.assertIn("agentj secret request", skill)
        self.assertIn("agentj sudo --why", skill)


MAIN = "import sys; from agentj.cli import main; sys.exit(main())"


class Cli(unittest.TestCase):
    def test_cli_parses_and_reports_unavailable(self):
        env = dict(os.environ, PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1]))
        with tempfile.TemporaryDirectory() as d:
            env["AGENTJ_STATE_DIR"] = str(pathlib.Path(d) / "s")
            r = subprocess.run([sys.executable, "-c", MAIN, "sudo", "--why", "w", "--", "ls", "-la"], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, elevate.EXIT_CARD, r.stderr)
            self.assertIn("SUDO_RESULT: unavailable", r.stderr)
            r = subprocess.run([sys.executable, "-c", MAIN, "secret", "request", "--name", "K", "--purpose", "p",
                                "--dest", ".env", "--json"], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(json.loads(r.stdout)["result"], "unavailable")


if __name__ == "__main__":
    unittest.main()
