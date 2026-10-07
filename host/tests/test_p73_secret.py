"""P73 (ADR-A180): F32 outbound secret pickup card + the secret card that outlives its CLI.

E2 (support ticket 20261007-183234, `gone [unsigned]` ×2 then `denied`): a real `agentj secret request` subprocess is killed
(SIGTERM / SIGKILL) while the card is on the phone → the owner's later submission is still written to --dest, and the outcome
is readable through the socket (`agentj secret result <id>`) without a value; the card expires after its 600 s; a serve restart
marks a card it never finished `gone (restart)`; the CLI's wording for gone / denied says what really happened.

E1 / F32: `agentj secret send` → a card with NO value to every paired session (`fa` = only that device's own credential); the
value leaves the host only after a valid passkey assertion over (card, nonce, shown digest, kind "secret_out"), only to that
session; one pickup, then `secret_out_done picked` everywhere, value wiped, one history line, audit without the value. A device
without a passkey, a wrong / replayed / other-kind assertion, an expired / declined / stopped card never get the value. The same
SS link / proxy YAML is still refused by the Telegram filters, the friends outbound gate and the attachment scan.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import base64
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agentj import elevate, passkey, secret_out, wire  # noqa: E402

from test_f17 import answer  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402
from test_p57_passkey import Authenticator, pk_record  # noqa: E402

KEY = "p73-test-key-DO-NOT-LEAK-0123456789abcdef"
SS = "ss://" + base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:P73-SS-PASS-not-real-7Qx9").decode().rstrip("=") + \
     "@203.0.113.9:8388#P73-test"
YAML = ("proxies:\n  - name: p73\n    type: ss\n    server: 203.0.113.9\n    port: 8388\n    cipher: chacha20-ietf-poly1305\n"
        "    pass" + "word: \"P73-YAML-PASS-not-real-4Kd8\"\n")   # split: a fixture, not a credential
MAIN = "import sys; from agentj.cli import main; sys.exit(main())"
HOST_DIR = str(pathlib.Path(__file__).resolve().parents[1])


def cli_env(st) -> dict:
    return dict(os.environ, PYTHONPATH=HOST_DIR, AGENTJ_STATE_DIR=str(st.root))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.sent = []
        os.environ.pop(passkey.TEST_ENV, None)

    def tearDown(self):
        self.tmp.cleanup()

    def of(self, t):
        return [o for _, o in self.sent if o["t"] == t]

    async def wait_for(self, t, n=1, secs=10):
        for _ in range(int(secs * 100)):
            if len(self.of(t)) >= n:
                return self.of(t)[n - 1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"no {t}")

    def no_leak(self, *values, extra=""):
        files = [self.st.log_path, elevate.log_path(self.st), elevate.results_path(self.st)]
        blob = "".join(f.read_text() for f in files if f.exists()) + extra
        for v in values:
            self.assertNotIn(v, blob)


# ====================================================================== E2: the secret card outlives its CLI
class OutlivesCli(Base):
    def _run(self, sig, ok=True, value=KEY):
        """serve in-process; a REAL `agentj secret request` subprocess; kill it with `sig` once the card id is printed;
        then the phone answers. → (stderr of the killed CLI, the card, host)."""
        host = _host(self.st, self.sent)
        ph = Phone(self.st)
        s = _ready(host, ph)
        work = self.d / "proj"
        work.mkdir(exist_ok=True)
        (work / ".env").write_text("OTHER=1\n")

        async def go():
            await host.elevate.start()
            try:
                p = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", MAIN, "secret", "request", "--name", "ELEVENLABS_API_KEY", "--purpose", "配音",
                    "--dest", f"env:{work / '.env'}#ELEVENLABS_API_KEY", env=cli_env(self.st), cwd=str(work),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                line = await asyncio.wait_for(p.stderr.readline(), 30)
                card = await self.wait_for("elev")
                p.send_signal(sig)
                await asyncio.wait_for(p.wait(), 10)
                await asyncio.sleep(0.3)                           # the host notices the hang-up — and keeps the card
                self.assertIn(card["id"], host.elevate.cards, "the card is still open after the CLI died")
                self.assertEqual(self.of("elev_done"), [], "nothing told the phone the card ended")
                await host._app(s, answer(ph, card, ok, value if ok else None))
                for _ in range(500):
                    if self.of("elev_done"):
                        break
                    await asyncio.sleep(0.01)
                return line.decode(), card
            finally:
                await host.elevate.stop()
        line, card = asyncio.run(go())
        return line, card, work

    def test_sigterm_then_submit_is_saved_and_readable(self):
        line, card, work = self._run(signal.SIGTERM)
        self.assertIn("SECRET_CARD: " + card["id"], line)
        self.assertIn(f"agentj secret result {card['id']}", line)
        self.assertEqual((work / ".env").read_text(), f"OTHER=1\nELEVENLABS_API_KEY={KEY}\n")
        self.assertEqual(stat.S_IMODE((work / ".env").stat().st_mode), 0o600)
        r = elevate.read_results(self.st)[card["id"]]
        self.assertEqual((r["result"], r["kind"], r["receipt"]["length"]), ("saved", "secret", len(KEY)))
        self.assertEqual(stat.S_IMODE(elevate.results_path(self.st).stat().st_mode), 0o600)
        self.assertEqual(self.of("elev_done")[-1]["result"], "saved")
        self.no_leak(KEY)
        # the Agent reads it afterwards with the CLI (through the socket — a fenced Agent does not see the state folder)
        host = _host(self.st, self.sent)

        async def read():
            await host.elevate.start()
            try:
                return await asyncio.to_thread(subprocess.run, [sys.executable, "-c", MAIN, "secret", "result", card["id"]],
                                               env=cli_env(self.st), capture_output=True, text=True, timeout=30)
            finally:
                await host.elevate.stop()
        out = asyncio.run(read())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("SECRET_SAVED: ELEVENLABS_API_KEY", out.stdout)
        self.assertNotIn(KEY, out.stdout + out.stderr)

    def test_sigkill_then_submit_is_saved(self):
        _, card, work = self._run(signal.SIGKILL)
        self.assertIn(f"ELEVENLABS_API_KEY={KEY}", (work / ".env").read_text())
        self.assertEqual(elevate.read_results(self.st)[card["id"]]["result"], "saved")

    def test_killed_then_declined_reads_denied_with_the_right_words(self):
        _, card, work = self._run(signal.SIGTERM, ok=False)
        self.assertEqual((work / ".env").read_text(), "OTHER=1\n")
        self.assertEqual(elevate.read_results(self.st)[card["id"]]["result"], "denied")
        said = elevate._say({"result": "denied"}, "secret")
        self.assertIn("不提供", said)
        self.assertIn("Don't provide", said)
        self.assertIn("Agent J 服务停止或重启", elevate._say({"result": "gone"}, "secret"))
        self.assertIn("命令提前结束", elevate._say({"result": "gone"}, "sudo"))

    def test_expiry_restart_pending_and_wait(self):
        host = _host(self.st, self.sent)
        ph = Phone(self.st)
        s = _ready(host, ph)
        req = {"t": "secret", "name": "K", "purpose": "p", "dest": str(self.d / "k.txt")}

        async def go():
            await host.elevate.start()
            try:
                t = asyncio.create_task(asyncio.to_thread(elevate.client_request, self.st, req, 30))
                card = await self.wait_for("elev")
                self.assertGreater(card["ttl"], 590, "a secret card lasts 10 minutes")
                pending = await host.elevate.result_of({"id": card["id"]})
                self.assertEqual(pending["result"], "pending")
                self.assertGreater(pending["secs"], 590)
                self.assertIn("还剩", elevate._say({**pending}, "secret"))
                waiter = asyncio.create_task(host.elevate.result_of({"id": card["id"], "wait": True}))
                host.elevate.cards[card["id"]]["deadline"] = time.monotonic() + 0.2
                res = await t
                self.assertEqual(res["result"], "timeout")
                self.assertEqual((await asyncio.wait_for(waiter, 5))["result"], "timeout")
                await host._app(s, answer(ph, card, True, KEY))         # too late: nothing is written
                self.assertFalse((self.d / "k.txt").exists())
                self.assertEqual((await host.elevate.result_of({"id": "0" * 32}))["result"], "unknown")
                self.assertEqual((await host.elevate.result_of({"id": "../x"}))["result"], "refused")
                lst = await host.elevate.result_of({})
                self.assertEqual([r["id"] for r in lst["items"]], [card["id"]])
            finally:
                await host.elevate.stop()
        asyncio.run(go())
        self.assertEqual(elevate.read_results(self.st)[next(iter(elevate.read_results(self.st)))]["result"], "timeout")
        # a pending record left by a serve that died → `gone (restart)` when the next serve starts
        elevate.remember(self.st, "a" * 32, {"kind": "secret", "name": "K", "result": "pending", "until": int(time.time()) + 300})
        host2 = _host(self.st, [])

        async def restart():
            await host2.elevate.start()
            await host2.elevate.stop()
        asyncio.run(restart())
        self.assertEqual(elevate.read_results(self.st)["a" * 32]["result"], "gone")
        self.assertEqual(elevate.read_results(self.st)["a" * 32]["why"], "restart")

    def test_sudo_card_is_still_withdrawn_when_its_cli_leaves(self):
        host = _host(self.st, self.sent)
        _ready(host, Phone(self.st))

        async def go():
            await host.elevate.start()
            try:
                r, w = await asyncio.open_unix_connection(str(host.elevate.sock_path))
                w.write((json.dumps({"t": "sudo", "argv": ["true"], "why": "w", "early": True}) + "\n").encode())
                await w.drain()
                first = json.loads(await asyncio.wait_for(r.readline(), 5))
                self.assertEqual((first["t"], first["kind"]), ("card", "sudo"))
                w.close()
                await self.wait_for("elev_done")
            finally:
                await host.elevate.stop()
        asyncio.run(go())
        self.assertEqual(self.of("elev_done")[-1]["result"], "gone")


# ====================================================================== E1: F32 pickup card
def fa_out(a: Authenticator, ph, card: dict, kind=secret_out.KIND, n=None) -> dict:
    digest = secret_out.shown_digest(secret_out.shown_fields(card))
    ch = passkey.elevate_challenge(ph.channel, ph.did, card["id"], kind, n or card["n"], digest)
    x = a.assert_(ch, b"\0", nonce=b"\0")
    return {k: x[k] for k in ("id", "cd", "ad", "sig")}


class Units(unittest.TestCase):
    def test_norm(self):
        c = secret_out.norm({"name": "SS 链接", "kind": "text", "value": SS})
        self.assertEqual((c["size"], c["ttl"], bytes(c["value"]).decode()), (len(SS), 600, SS))
        f = secret_out.norm({"name": "代理配置", "kind": "file", "filename": "proxy.yaml",
                             "data": base64.b64encode(YAML.encode()).decode(), "ttl": 120})
        self.assertEqual((f["filename"], f["size"], f["ttl"]), ("proxy.yaml", len(YAML), 120))
        for bad in ({"kind": "text", "value": "x"}, {"name": "n", "kind": "text", "value": ""},
                    {"name": "n", "kind": "nope", "value": "x"}, {"name": "a\nb", "kind": "text", "value": "x"},
                    {"name": "n", "kind": "file", "filename": "../x", "data": "eA=="},
                    {"name": "n", "kind": "file", "filename": "x", "data": "not base64!"},
                    {"name": "n", "kind": "text", "value": "x", "ttl": 601},
                    {"name": "n", "kind": "text", "value": "x" * (secret_out.MAX_TEXT + 1)},
                    {"name": "n", "kind": "file", "filename": "x",
                     "data": base64.b64encode(b"x" * (secret_out.MAX_FILE + 1)).decode()}):
            with self.assertRaises(elevate.Refused, msg=str(bad)[:80]):
                secret_out.norm(bad)

    def test_digest_covers_every_shown_field(self):
        base = {"name": "n", "purpose": "p", "kind": "file", "filename": "a.yaml", "size": 10}
        d = secret_out.shown_digest(secret_out.shown_fields(base))
        for k, v in (("name", "m"), ("purpose", "q"), ("kind", "text"), ("filename", "b.yaml"), ("size", 11)):
            self.assertNotEqual(d, secret_out.shown_digest(secret_out.shown_fields({**base, k: v})), k)
        # shared with web/test/p73_secretout.test.mjs
        self.assertEqual(d, DIGEST_VEC)


DIGEST_VEC = "1666e5517932f328f0a6fd71d4f69e86fe47e796fe2a7438af7cdc71dc3625ee"   # = web/test/p73_secretout.test.mjs


class Pickup(Base):
    def setUp(self):
        super().setUp()
        self.a = Authenticator()

    def run_out(self, req, script, *, passkey_device=True, second=False):
        host = _host(self.st, self.sent)
        ph = Phone(self.st)
        if passkey_device:
            self.assertTrue(self.st.set_passkey(ph.pub, pk_record(self.a)))
        s = _ready(host, ph)
        other = s2 = None
        if second:
            other = Phone(self.st, name="平板")
            s2 = _ready(host, other, cid=12)

        async def go():
            await host.elevate.start()
            try:
                res = await asyncio.to_thread(elevate.client_request, self.st, {"t": "secret_out", **req}, 30)
                if script:
                    await script(host, s, ph, res, s2, other)
                await asyncio.sleep(0.05)
                return res
            finally:
                await host.elevate.stop()
        return asyncio.run(go()), host

    def val_to(self, cid=None):
        return [(c, o) for c, o in self.sent if o["t"] == "secret_out_val" and (cid is None or c == cid)]

    def test_text_pickup_with_face_id(self):
        async def script(host, s, ph, res, s2, other):
            self.assertEqual(res["result"], "sent")
            self.assertEqual((res["devices"], res["faceid"]), (2, 1))
            cards = self.of("secret_out")
            self.assertEqual(len(cards), 2, "every ready paired session sees the card")
            mine = [o for c, o in self.sent if o["t"] == "secret_out" and c == 11][0]
            theirs = [o for c, o in self.sent if o["t"] == "secret_out" and c == 12][0]
            self.assertEqual(mine["fa"], wire.b64u(self.a.id))
            self.assertNotIn("fa", theirs, "another device never learns this credential")
            self.assertEqual((mine["name"], mine["kind"], mine["size"]), ("Shadowrocket SS 链接", "text", len(SS)))
            self.assertGreater(mine["ttl"], 590)
            self.assertNotIn(SS, json.dumps(self.sent, ensure_ascii=False), "no value before Face ID")
            # the device without a passkey: refused, told to set Face ID up, no value
            await host._app(s2, {"t": "secret_out_open", "id": res["id"], "n": theirs["n"], "fa": fa_out(self.a, other, theirs)})
            self.assertEqual(self.of("secret_out_err")[-1]["why"], "no_passkey")
            # an assertion for another kind (a F17 secret card) / another nonce / none at all: refused
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": mine["n"], "fa": fa_out(self.a, ph, mine, kind="secret")})
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": "0" * 32, "fa": fa_out(self.a, ph, mine, n="0" * 32)})
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": mine["n"]})
            self.assertEqual(self.val_to(), [])
            self.assertIn(res["id"], host.elevate.out.cards, "the card stays open")
            # Face ID → the value, to this session only
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": mine["n"], "fa": fa_out(self.a, ph, mine)})
            vals = self.val_to()
            self.assertEqual([c for c, _ in vals], [11])
            self.assertEqual((vals[0][1]["value"], vals[0][1]["kind"]), (SS, "text"))
            await asyncio.sleep(0.05)
            done = self.of("secret_out_done")
            self.assertEqual({d["result"] for d in done}, {"picked"})
            self.assertEqual(len(done), 2, "both sessions are told it was picked up")
            self.assertNotIn(res["id"], host.elevate.out.cards)
            # once only
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": mine["n"], "fa": fa_out(self.a, ph, mine)})
            self.assertEqual(len(self.val_to()), 1)
            self.assertEqual(self.of("secret_out_err")[-1]["why"], "gone")
            st = await host.elevate.result_of({"id": res["id"]})
            self.assertEqual((st["result"], st["kind"]), ("picked", "secret_out"))
        res, host = self.run_out({"name": "Shadowrocket SS 链接", "purpose": "手机翻墙", "kind": "text", "value": SS}, script,
                                 second=True)
        log = elevate.read_log(self.st)
        out = [r for r in log if r.get("kind") == "secret_out"]
        self.assertEqual([r["result"] for r in out], ["sent", "refused", "refused", "refused", "refused", "picked"])
        self.assertEqual(out[-1]["passkey"], "uv")
        self.assertEqual(elevate.check_record(self.st, out[-1]), "passkey")
        self.assertTrue(out[-1]["device"])
        # everything the host kept or logged, and the history line, carry no value
        hist = json.dumps([o for _, o in self.sent if o["t"] != "secret_out_val"], ensure_ascii=False)
        self.no_leak(SS, "P73-SS-PASS", extra=hist)
        self.assertNotIn(wire.b64u(SS.encode()), hist)
        self.assertEqual(stat.S_IMODE(elevate.log_path(self.st).stat().st_mode), 0o600)
        turns = getattr(host, "hist", None)
        if turns is not None:
            texts = json.dumps(turns.page(None, 20) if hasattr(turns, "page") else [], ensure_ascii=False, default=str)
            self.assertNotIn(SS, texts)

    def test_file_pickup_and_value_is_wiped(self):
        kept = {}

        async def script(host, s, ph, res, s2, other):
            card = self.of("secret_out")[0]
            self.assertEqual((card["kind"], card["filename"], card["size"]), ("file", "proxy.yaml", len(YAML)))
            kept["c"] = host.elevate.out.cards[res["id"]]
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": card["n"], "fa": fa_out(self.a, ph, card)})
            v = self.val_to()[0][1]
            self.assertEqual((base64.b64decode(v["data"]).decode(), v["filename"]), (YAML, "proxy.yaml"))
        self.run_out({"name": "代理配置", "kind": "file", "filename": "proxy.yaml",
                      "data": base64.b64encode(YAML.encode()).decode()}, script)
        self.assertEqual(bytes(kept["c"]["value"]), b"", "the in-memory copy is gone")
        self.no_leak("P73-YAML-PASS")

    def test_expired_declined_stopped_failed_send(self):
        old = secret_out.TTL_MIN
        secret_out.TTL_MIN = 1
        try:
            async def expire(host, s, ph, res, s2, other):
                card = self.of("secret_out")[0]
                await asyncio.sleep(1.4)
                self.assertNotIn(res["id"], host.elevate.out.cards)
                await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": card["n"], "fa": fa_out(self.a, ph, card)})
                self.assertEqual(self.val_to(), [])
                self.assertEqual(self.of("secret_out_done")[-1]["result"], "expired")
            self.run_out({"name": "n", "kind": "text", "value": SS, "ttl": 1}, expire)
        finally:
            secret_out.TTL_MIN = old
        self.sent.clear()

        async def decline(host, s, ph, res, s2, other):
            await host._app(s, {"t": "secret_out_decline", "id": res["id"]})
            await asyncio.sleep(0.02)
            self.assertEqual(self.of("secret_out_done")[-1]["result"], "declined")
        self.run_out({"name": "n", "kind": "text", "value": SS}, decline)
        self.sent.clear()

        async def stop(host, s, ph, res, s2, other):
            host.elevate.cancel_all("stopped")
            await asyncio.sleep(0.02)
            self.assertEqual(self.of("secret_out_done")[-1]["result"], "stopped")
            self.assertEqual(host.elevate.out.cards, {})
        self.run_out({"name": "n", "kind": "text", "value": SS}, stop)
        self.sent.clear()

        async def failed_send(host, s, ph, res, s2, other):
            card = self.of("secret_out")[0]
            orig = host.send_app

            async def refuse_val(sess, obj):
                if obj["t"] == "secret_out_val":
                    return False                          # e.g. an old session that cannot take a big message
                return await orig(sess, obj)
            host.send_app = refuse_val
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": card["n"], "fa": fa_out(self.a, ph, card)})
            self.assertIn(res["id"], host.elevate.out.cards, "not delivered → still open")
            self.assertEqual(self.of("secret_out_err")[-1]["why"], "send")
            again = self.of("secret_out")[-1]
            self.assertNotEqual(again["n"], card["n"], "a fresh nonce")
            host.send_app = orig
            await host._app(s, {"t": "secret_out_open", "id": res["id"], "n": again["n"], "fa": fa_out(self.a, ph, again)})
            self.assertEqual(len(self.val_to()), 1)
        self.run_out({"name": "n", "kind": "text", "value": SS}, failed_send)
        self.no_leak(SS)

    def test_refusals(self):
        res, _ = self.run_out({"name": "", "kind": "text", "value": SS}, None)
        self.assertEqual(res["result"], "refused")
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        st = _state(empty.name)
        host = _host(st, self.sent)

        async def none():
            await host.elevate.start()
            try:
                return await asyncio.to_thread(elevate.client_request, st,
                                               {"t": "secret_out", "name": "n", "kind": "text", "value": SS}, 10)
            finally:
                await host.elevate.stop()
        self.assertEqual(asyncio.run(none())["result"], "no_device")
        self.no_leak(SS)

    def test_cli_send_prints_no_value(self):
        f = self.d / "proxy.yaml"
        f.write_text(YAML)
        host = _host(self.st, self.sent)
        ph = Phone(self.st)
        self.st.set_passkey(ph.pub, pk_record(self.a))
        _ready(host, ph)

        async def go():
            await host.elevate.start()
            try:
                env = cli_env(self.st)
                env["P73_SS"] = SS
                r1 = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", MAIN, "secret", "send", "--name", "SS 链接",
                                                              "--value-from", "env:P73_SS"], env=env, capture_output=True,
                                             text=True, timeout=30)
                r2 = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", MAIN, "secret", "send", "--name", "代理配置",
                                                              "--file", str(f), "--json"], env=env, capture_output=True,
                                             text=True, timeout=30)
                r3 = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", MAIN, "secret", "send", "--name", "x",
                                                              "--value-from", SS], env=env, capture_output=True, text=True,
                                             timeout=30)
                return r1, r2, r3
            finally:
                await host.elevate.stop()
        r1, r2, r3 = asyncio.run(go())
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("SECRET_OUT: sent", r1.stdout)
        self.assertIn("agentj secret result", r1.stdout)
        self.assertEqual(json.loads(r2.stdout)["result"], "sent")
        self.assertNotEqual(r3.returncode, 0, "a value on the command line is refused")
        for r in (r1, r2, r3):
            self.assertNotIn("P73-SS-PASS", r.stdout + r.stderr)
            self.assertNotIn(SS, r.stdout + r.stderr)
            self.assertNotIn("P73-YAML-PASS", r.stdout + r.stderr)


# ====================================================================== the boundaries that do not move
class StillBlocked(unittest.TestCase):
    """The same SS link / proxy YAML outside the pickup card: Telegram group filters, the friends outbound gate and the
    attachment secret scan (Telegram media, `<name> 可能含密钥，没有发`) all keep refusing it."""

    def test_telegram_friends_and_attachments(self):
        from agentj import media, peer_guard, telegram
        for text in (SS, "这是你的链接 " + SS, YAML):
            self.assertFalse(peer_guard.outbound(text, peer_guard.SecretIndex()).ok, text[:30])
            self.assertTrue(media._secret(text, False), text[:30])
            for profile in ("proxy", "family"):
                out = telegram.group_filter(text, profile)
                self.assertNotIn("P73-SS-PASS", out)
                self.assertNotIn(SS, out)
        self.assertNotIn("P73-YAML-PASS", peer_guard.privacy.redact(YAML))


class Docs(unittest.TestCase):
    def test_skill_and_identity_say_use_the_pickup_card(self):
        from agentj import main_identity
        main_identity.verify_core()
        for lang in ("zh", "en"):
            text = main_identity.prompt({"language": lang})
            self.assertIn("agentj secret send", text)
            self.assertIn("agentj secret result", text)
        skill = (pathlib.Path(HOST_DIR) / "agentj/skills/agentj-config/SKILL.md").read_text()
        self.assertIn("agentj secret send", skill)
        self.assertIn("agentj secret result", skill)


if __name__ == "__main__":
    unittest.main()
