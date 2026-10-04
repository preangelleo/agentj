"""L1: the Agent bridge (PROTOCOL §8) and Web Push (§9) on the host.

Units: reply splitting, request summaries, the approval signature, RFC 8291 / RFC 8292, the push endpoint allowlist, the
permission tool failing closed. Flows (no relay; a fake ready session records what would be sent): signed answers, every
refusal path, default deny on timeout, the approvals log, and the whole chain with a stand-in `claude` (fakeclaude.py) that
starts the real permission tool and asks it before running a command.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import socket
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from agentj import agent as agents  # noqa: E402
from agentj import approvals, permtool, serve, webpush, wire  # noqa: E402
from agentj.state import State  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
MARK = "AJ-L1-明文-cmd-7f3a"


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    return st


def _raw(pub) -> bytes:
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)



# RFC 8291 Appendix A, in the RFC's order (published example values).
RFC8291_APPENDIX_A = (
    "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "BTBZMqHH6r4Tts7J_aSIgg",
    "DGv6ra1nlYgDCS1FRnbzlw",
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
)

class Phone:
    """A paired device as the host sees it: X25519 static (only its public half matters here) + Ed25519 approval key."""
    def __init__(self, st: State, name="手机", with_key=True):
        self.pub = os.urandom(32)
        self.sk = Ed25519PrivateKey.generate()
        self.did = st.add_device(self.pub, name, _raw(self.sk.public_key()) if with_key else None)
        self.channel = st.config()["channel"]

    def answer(self, ask: dict, ok: bool, *, tool=None, summary=None, rid=None) -> dict:
        d = approvals.shown_digest(tool or ask["tool"], summary if summary is not None else ask["summary"])
        rid = rid or ask["id"]
        sig = self.sk.sign(approvals.signed_message(self.channel, self.did, rid, "allow" if ok else "deny", d))
        return {"t": "answer", "id": rid, "ok": ok, "sig": wire.b64u(sig)}


def _host(st, sent):
    host = serve.Host(st, events="jsonl", read_stdin=False)
    # These SDK/stream-json fixtures exercise the still-supported independent
    # fence, not interactive shared sessions. Select their mode explicitly;
    # production default/upgrade and native shared paths have separate tests.
    if host.agent_cfg:
        host.agent_cfg['session_mode']='independent'

    async def no_relay():           # no relay in these units (a failed reconnect would drop the fake sessions)
        await host.stopping.wait()
    host.relay_loop = no_relay
    # These legacy transport fixtures set ask_ttl directly and do not exercise
    # user configuration. A watcher must not import another test's HOME file.
    # Real preference activation is covered by test_preferences and CLI e2e.
    host.preferences_loop = no_relay

    async def send_app(s, obj):
        sent.append((s.cid, obj))
        return True
    host.send_app = send_app
    return host


def _ready(host, phone: Phone, cid=11):
    s = serve.Session(cid=cid, state="ready", device=phone.did, name="手机", pub=phone.pub)
    host.sessions[cid] = s
    return s


# ------------------------------------------------------------------ units
class Units(unittest.TestCase):
    def test_split_never_drops_text(self):
        t = "长" * 9000
        parts = agents.split_text(t)
        self.assertEqual("".join(parts), t)
        self.assertTrue(all(len(p) <= 4000 for p in parts))
        emoji = "😀" * 2500                        # 2 UTF-16 units each
        parts = agents.split_text(emoji)
        self.assertEqual("".join(parts), emoji)
        self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 4000 for p in parts))
        self.assertEqual(agents.split_text("a\n" * 10), ["a\n" * 10])

    def test_summary_shows_the_command_and_is_bounded(self):
        s = agents.summarize("Bash", {"command": "rm -rf build\x1b[31m", "description": "clean"})
        self.assertIn("rm -rf build", s)
        self.assertNotIn("\x1b", s)
        self.assertIn("clean", s)
        self.assertLessEqual(len(agents.summarize("Bash", {"command": "x" * 9000})), 2000)
        self.assertIn("/tmp/a.txt", agents.summarize("Write", {"file_path": "/tmp/a.txt", "content": "hi"}))
        self.assertIn('"url"', agents.summarize("Mystery", {"url": "u"}))

    def test_approval_signature_binds_every_field(self):
        sk = Ed25519PrivateKey.generate()
        pub = _raw(sk.public_key())
        d = approvals.shown_digest("Bash", "rm x")
        sig = sk.sign(approvals.signed_message("CH", "DEV", "r" * 32, "allow", d))
        self.assertTrue(approvals.verify(pub, sig, "CH", "DEV", "r" * 32, "allow", d))
        for args in (("CH2", "DEV", "r" * 32, "allow", d), ("CH", "DEV2", "r" * 32, "allow", d),
                     ("CH", "DEV", "s" * 32, "allow", d), ("CH", "DEV", "r" * 32, "deny", d),
                     ("CH", "DEV", "r" * 32, "allow", approvals.shown_digest("Bash", "rm y"))):
            self.assertFalse(approvals.verify(pub, sig, *args), args)
        self.assertFalse(approvals.verify(pub, sig[:-1], "CH", "DEV", "r" * 32, "allow", d))
        with self.assertRaises(ValueError):
            approvals.signed_message("CH", "DEV", "r", "maybe", d)

    def test_rfc8291_vector(self):
        # Public test vector from RFC 8291 Appendix A (not a secret): AS private, UA public, auth, salt, expected body.
        v = RFC8291_APPENDIX_A
        asp = ec.derive_private_key(int.from_bytes(webpush.unb64u(v[0]), "big"), ec.SECP256R1())
        body = webpush.encrypt(b"When I grow up, I want to be a watermelon", webpush.unb64u(v[1]),
                               webpush.unb64u(v[2]), as_private=asp, salt=webpush.unb64u(v[3]))
        self.assertEqual(webpush.b64u(body), v[4])

    def test_push_payload_has_no_content_and_one_length(self):
        self.assertEqual(len(webpush.payload("reply")), len(webpush.payload("ask")))
        self.assertEqual(json.loads(webpush.payload("ask")), {"k": "ask"})
        with self.assertRaises(ValueError):
            webpush.payload("text")

    def test_vapid_jwt_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            k = webpush.load_vapid(st)
            self.assertEqual(webpush.load_vapid(st).private_numbers().private_value, k.private_numbers().private_value)
            self.assertEqual(stat.S_IMODE(st.vapid_path.stat().st_mode), 0o600)
            jwt = webpush.vapid_jwt(k, "https://fcm.googleapis.com/fcm/send/abc", now=1000)
            h, c, s = jwt.split(".")
            sig = webpush.unb64u(s)
            k.public_key().verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                                  f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))
            claims = json.loads(webpush.unb64u(c))
            self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
            self.assertEqual(claims["exp"], 1000 + 12 * 3600)

    def test_endpoint_allowlist(self):
        ok = ["https://fcm.googleapis.com/fcm/send/x", "https://web.push.apple.com/abc", "https://api.push.apple.com/x",
              "https://updates.push.services.mozilla.com/wpush/v2/x", "https://db5p.notify.windows.com/w/?token=x"]
        bad = ["http://fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x", "https://evil.com/x",
               "https://fcm.googleapis.com.evil.com/x", "https://u:p@fcm.googleapis.com/x", "https://push.apple.com/x",
               "https://127.0.0.1/x", "https://169.254.169.254/latest", "file:///etc/passwd", "https://x" + "a" * 1100, 5]
        for e in ok:
            self.assertTrue(webpush.endpoint_ok(e), e)
        for e in bad:
            self.assertFalse(webpush.endpoint_ok(e), e)

    def test_parse_sub(self):
        ua = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        good = {"endpoint": "https://fcm.googleapis.com/x", "p256dh": webpush.b64u(ua), "auth": webpush.b64u(os.urandom(16))}
        self.assertIsNotNone(webpush.parse_sub(good))
        self.assertIsNone(webpush.parse_sub({**good, "p256dh": webpush.b64u(b"\x04" + os.urandom(64))}))   # not on the curve
        self.assertIsNone(webpush.parse_sub({**good, "auth": webpush.b64u(os.urandom(8))}))
        self.assertIsNone(webpush.parse_sub({**good, "endpoint": "https://evil.com/x"}))

    def test_permtool_fails_closed(self):
        os.environ["AGENTJ_PERM_SOCK"] = "/nonexistent/perm.sock"
        os.environ["AGENTJ_PERM_TOKEN"] = "t"
        try:
            init = permtool.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
            self.assertEqual(init["result"]["capabilities"], {"tools": {}})
            tools = permtool.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
            self.assertEqual([t["name"] for t in tools], ["approve"])
            r = permtool.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": "rm x"}}}})
            self.assertEqual(json.loads(r["result"]["content"][0]["text"])["behavior"], "deny")
            r = permtool.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "other", "arguments": {}}})
            self.assertEqual(json.loads(r["result"]["content"][0]["text"])["behavior"], "deny")
            self.assertIsNone(permtool.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        finally:
            os.environ.pop("AGENTJ_PERM_SOCK")
            os.environ.pop("AGENTJ_PERM_TOKEN")

    def test_claude_argv_never_widens_permissions(self):
        a = agents.ClaudeAgent(None, {"kind": "claude", "dir": "/tmp", "model": None}).argv("sid-1")
        # The identity is prose (e.g. "skipped is not success"), not a permission flag.
        # Exclude only its value; keep every flag, settings object and tool argument audited.
        prompt_index = a.index("--append-system-prompt")
        joined = " ".join(a[:prompt_index + 1] + a[prompt_index + 2:])
        for bad in ("dangerously", "bypass", "--allowedTools", "--allowed-tools", "--permission-mode", "skip"):
            self.assertNotIn(bad, joined)
        self.assertEqual(a[a.index("--permission-prompt-tool") + 1], agents.PERM_TOOL)
        self.assertEqual(a[a.index("--disallowedTools") + 1], agents.PERM_TOOL)   # the model itself cannot call it
        self.assertEqual(a[a.index("--resume") + 1], "sid-1")
        from agentj.agent_codex import CodexAgent
        cx = CodexAgent(None, {"kind": "codex", "dir": "/tmp", "model": None})
        c = " ".join(cx.argv())
        for bad in ("dangerously", "bypass", "--sandbox", "-s ", "approval", "-c", "--enable", "--disable"):
            self.assertNotIn(bad, c)
        # app-server (ADR-A70): the only thread settings serve adds make Codex ask more, never less (Invariant 11)
        self.assertEqual(cx.policy(), {"approvalsReviewer": "user", "approvalPolicy": "untrusted"})
        cx.human = {"approval_policy": {"granular": {"rules": False, "sandbox_approval": False, "mcp_elicitations": False}}}
        self.assertEqual(cx.policy(), {"approvalsReviewer": "user"})        # a granular policy of theirs stays theirs
        self.assertEqual(CodexAgent(None, {"kind": "codex", "dir": "/tmp", "model": None}, research=True).policy()["sandbox"],
                         "read-only")


# ------------------------------------------------------------------ approval flows (no relay)
class Flows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def asks(self):
        return [o for _, o in self.sent if o["t"] == "ask"]

    def run_ask(self, host, script, tool_input=None, ttl=5):
        host.ask_ttl = ttl
        tool_input = tool_input or {"command": f"echo {MARK}"}

        async def go():
            t = asyncio.create_task(host.ask("Bash", tool_input))
            while not self.asks() and not t.done():
                await asyncio.sleep(0.01)
            if self.asks():
                await script(self.asks()[-1])
            return await t
        return asyncio.run(go())

    def test_no_paired_phone_denies_at_once(self):
        host = _host(self.st, self.sent)
        t0 = time.monotonic()
        res = self.run_ask(host, None)
        self.assertEqual(res["behavior"], "deny")
        self.assertLess(time.monotonic() - t0, 1)
        self.assertEqual(approvals.read_log(self.st)[-1]["reason"], "no_device")

    def test_signed_allow_returns_the_input_unchanged_and_is_logged(self):
        ph = Phone(self.st)
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        inp = {"command": f"echo {MARK}", "description": "d"}

        async def script(ask):
            self.assertIn(MARK, ask["summary"])
            self.assertLessEqual(ask["ttl"], 5)
            await host._app(s, ph.answer(ask, True))
        res = self.run_ask(host, script, inp)
        self.assertEqual(res, {"behavior": "allow", "updatedInput": inp})
        rec = approvals.read_log(self.st)[-1]
        self.assertEqual((rec["decision"], rec["reason"], rec["device"]), ("allow", "device", ph.did))
        self.assertEqual(approvals.check_record(self.st, rec), "ok")
        self.assertNotIn(MARK, self.st.approvals_path.read_text(), "the log never holds the tool input")
        self.assertNotIn(MARK, self.st.log_path.read_text(), "host.log never holds the tool input")
        self.assertEqual(stat.S_IMODE(self.st.approvals_path.stat().st_mode), 0o600)
        self.assertIn({"t": "ask_done", "id": rec["id"], "result": "allow"}, [o for _, o in self.sent])

    def test_signed_deny(self):
        ph = Phone(self.st)
        host = _host(self.st, self.sent)
        s = _ready(host, ph)

        async def script(ask):
            await host._app(s, ph.answer(ask, False))
        res = self.run_ask(host, script)
        self.assertEqual(res["behavior"], "deny")
        self.assertIn("拒绝", res["message"])
        self.assertEqual(approvals.read_log(self.st)[-1]["decision"], "deny")

    def test_bad_answers_are_ignored_until_timeout(self):
        """Wrong signature, signature over other text, wrong id, flipped decision, device without a key, unpaired
        session — none decides; the request is denied when its time is up."""
        ph = Phone(self.st)
        nokey = Phone(self.st, "旧手机", with_key=False)
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        s2 = _ready(host, nokey, cid=12)
        stranger = Phone(self.st, "陌生")
        s3 = _ready(host, stranger, cid=13)
        self.st.remove_device(stranger.did)

        async def script(ask):
            bad = ph.answer(ask, True)
            bad["sig"] = wire.b64u(os.urandom(64))
            await host._app(s, bad)
            await host._app(s, ph.answer(ask, True, summary="something else"))    # signed what it did not show
            flipped = ph.answer(ask, False)
            flipped["ok"] = True                                                  # signature says deny
            await host._app(s, flipped)
            await host._app(s, ph.answer(ask, True, rid="0" * 32))
            await host._app(s, {"t": "answer", "id": ask["id"], "ok": "yes", "sig": "x"})
            await host._app(s2, nokey.answer(ask, True))
            await host._app(s3, stranger.answer(ask, True))                       # removed from the allowlist
        t0 = time.monotonic()
        res = self.run_ask(host, script, ttl=1.5)
        self.assertEqual(res["behavior"], "deny")
        self.assertGreaterEqual(time.monotonic() - t0, 1.4)
        rec = approvals.read_log(self.st)[-1]
        self.assertEqual((rec["decision"], rec["reason"], rec["sig"]), ("deny", "timeout", None))
        log = self.st.log_path.read_text()
        self.assertIn('"reason": "bad_signature"', log)
        self.assertIn('"reason": "no_key"', log)

    def test_first_valid_answer_wins_and_later_ones_change_nothing(self):
        a, b = Phone(self.st, "A"), Phone(self.st, "B")
        host = _host(self.st, self.sent)
        sa, sb = _ready(host, a), _ready(host, b, cid=12)

        async def script(ask):
            await host._app(sa, a.answer(ask, False))
            await host._app(sb, b.answer(ask, True))
        res = self.run_ask(host, script)
        self.assertEqual(res["behavior"], "deny")
        self.assertEqual(approvals.read_log(self.st)[-1]["device"], a.did)
        self.assertEqual(len(self.asks()), 2, "the request went to both phones")

    def test_serve_stop_denies_pending(self):
        ph = Phone(self.st)
        host = _host(self.st, self.sent)
        _ready(host, ph)

        async def script(ask):
            host.stopping.set()
            for x in host.asks.values():
                x.fut.set_result(("deny", None, None, None))
        res = self.run_ask(host, script)
        self.assertEqual(res["behavior"], "deny")
        self.assertEqual(approvals.read_log(self.st)[-1]["reason"], "serve_stop")

    def test_old_device_registers_its_key_once(self):
        ph = Phone(self.st, with_key=False)
        k1, k2 = os.urandom(32), os.urandom(32)
        self.assertTrue(self.st.set_sign_key_if_absent(ph.pub, k1))
        self.assertFalse(self.st.set_sign_key_if_absent(ph.pub, k2), "never replaced")
        self.assertEqual(self.st.sign_key(ph.did), k1)
        self.assertFalse(self.st.set_sign_key_if_absent(os.urandom(32), k1), "unknown device")

    def test_approvals_verify_detects_a_forged_line(self):
        ph = Phone(self.st)
        host = _host(self.st, self.sent)
        s = _ready(host, ph)

        async def script(ask):
            await host._app(s, ph.answer(ask, True))
        self.run_ask(host, script)
        rec = approvals.read_log(self.st)[-1]
        forged = {**rec, "decision": "allow", "id": "f" * 32}
        self.assertEqual(approvals.check_record(self.st, forged), "bad")
        other = Ed25519PrivateKey.generate()
        swapped = {**rec, "sk": wire.b64u(_raw(other.public_key())),
                   "sig": wire.b64u(other.sign(approvals.signed_message(rec["channel"], rec["device"], rec["id"], "allow", rec["shown_sha256"])))}
        self.assertEqual(approvals.check_record(self.st, swapped), "bad", "valid signature, but not the paired device's key")

    def test_backlog_replay_and_rendering(self):
        a, b = Phone(self.st, "A"), Phone(self.st, "B")
        host = _host(self.st, self.sent)
        sa = _ready(host, a)
        host._remember("device", "from A", device=a.did, name="A")
        host._remember("agent", "reply")
        sb = _ready(host, b, cid=12)

        async def go():
            await host.on_ready(sb, 0)
            n = len(self.sent)
            await host.on_ready(sa, 1)       # A already saw seq 1
            return n
        n = asyncio.run(go())
        to_b = [o for c, o in self.sent[:n] if c == 12]
        self.assertEqual([o["t"] for o in to_b], ["status", "estop_state", "push_key", "msg", "msg"])
        self.assertEqual((to_b[3]["from"], to_b[3]["name"]), ("device", "A"))
        self.assertEqual(to_b[4]["from"], "agent")
        to_a = [o for c, o in self.sent[n:] if c == 11]
        self.assertEqual([o.get("seq") for o in to_a if o["t"] == "msg"], [2])
        self.assertEqual(to_a[0], {"t": "status", "s": "none", "agent": None, "name": None})


# ------------------------------------------------------------------ the whole chain with a stand-in claude
class Chain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.argv_log = self.work / ".argv.jsonl"   # inside the agent's folder: the fence hides the rest of /tmp (L2)
        wrapper = pathlib.Path(self.tmp.name) / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeclaude.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = {"AGENTJ_CLAUDE_BIN": str(wrapper), "FAKE_CLAUDE_LOG": str(self.argv_log)}
        os.environ.update(self.env)
        self.st.set_agent_config("claude", str(self.work))

    def tearDown(self):
        for k in self.env:
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def test_message_reply_approve_deny_timeout_crash_resume(self):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = 3
        (self.work / "victim.txt").write_text("keep me")

        def msgs():
            return [o for _, o in sent if o["t"] == "msg" and o.get("from") in ("agent", "notice")]

        def statuses():
            return [o["s"] for _, o in sent if o["t"] == "status"]

        async def wait(pred, ms=15000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-6:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = _ready(host, ph)
            self.assertEqual(stat.S_IMODE(self.st.perm_sock_path.stat().st_mode), 0o600)
            # 1. plain message → reply; working → idle
            await host._app(s, {"t": "msg", "id": "a" * 16, "text": "列出当前目录", "ts": 0})
            await wait(lambda: any(m["text"] == "ECHO: 列出当前目录" for m in msgs()))
            await wait(lambda: statuses()[-1:] == ["idle"])
            self.assertIn("working", statuses())
            # 2. the agent wants rm → card → phone denies → file stays, agent told
            await host._app(s, {"t": "msg", "id": "b" * 16, "text": "RUN: rm victim.txt", "ts": 0})
            await wait(lambda: any(o["t"] == "ask" for _, o in sent))
            self.assertEqual(statuses()[-1], "waiting")
            ask = [o for _, o in sent if o["t"] == "ask"][-1]
            self.assertEqual((ask["tool"], ask["summary"]), ("Bash", "rm victim.txt"))
            await host._app(s, ph.answer(ask, False))
            await wait(lambda: any(m["text"].startswith("被拒绝") for m in msgs()))
            self.assertTrue((self.work / "victim.txt").exists(), "denied rm did not run")
            # 3. approve → runs
            await host._app(s, {"t": "msg", "id": "c" * 16, "text": "RUN: touch made.txt", "ts": 0})
            await wait(lambda: len([o for _, o in sent if o["t"] == "ask"]) == 2)
            ask2 = [o for _, o in sent if o["t"] == "ask"][-1]
            await host._app(s, ph.answer(ask2, True))
            await wait(lambda: any(m["text"].startswith("已执行") for m in msgs()))
            self.assertTrue((self.work / "made.txt").exists())
            # 4. nobody answers → default deny after ask_ttl
            t0 = time.monotonic()
            await host._app(s, {"t": "msg", "id": "d" * 16, "text": "RUN: rm victim.txt", "ts": 0})
            await wait(lambda: sum(m["text"].startswith("被拒绝") for m in msgs()) == 2, 10000)
            self.assertGreaterEqual(time.monotonic() - t0, 2.5)
            self.assertTrue((self.work / "victim.txt").exists())
            self.assertIn({"t": "ask_done", "id": [o for _, o in sent if o["t"] == "ask"][-1]["id"], "result": "timeout"},
                          [o for _, o in sent])
            # 5. a long reply is split, nothing lost
            await host._app(s, {"t": "msg", "id": "e" * 16, "text": "LONG", "ts": 0})
            await wait(lambda: sum(len(m["text"]) for m in msgs() if set(m["text"]) == {"长"}) == 9000)
            # 6. the agent process dies mid-turn → notice; the next message resumes the same session
            await host._app(s, {"t": "msg", "id": "f" * 16, "text": "CRASH", "ts": 0})
            await wait(lambda: any("退出了" in m["text"] for m in msgs()))
            await host._app(s, {"t": "msg", "id": "g" * 16, "text": "还在吗", "ts": 0})
            await wait(lambda: any(m["text"] == "ECHO: 还在吗" for m in msgs()))
            host.stopping.set()
            await run
        asyncio.run(go())
        starts = [s for s in (json.loads(x) for x in self.argv_log.read_text().splitlines()) if "argv" in s]   # control lines: §10.10 meters
        self.assertEqual(len(starts), 2, "one process, restarted once after the crash")
        sid = self.st.agent_session("claude")
        self.assertTrue(sid)
        self.assertNotIn("--resume", starts[0]["argv"])
        self.assertEqual(starts[1]["argv"][starts[1]["argv"].index("--resume") + 1], sid)
        self.assertEqual(starts[0]["cwd"], str(self.work.resolve()))
        log = approvals.read_log(self.st)
        self.assertEqual([(r["decision"], r["reason"]) for r in log], [("deny", "device"), ("allow", "device"), ("deny", "timeout")])
        self.assertFalse(self.st.perm_sock_path.exists(), "perm.sock removed on stop")
        for f in (self.st.log_path, self.st.approvals_path):
            self.assertNotIn("victim", f.read_text())
            self.assertNotIn("列出当前目录", f.read_text())

    def test_perm_socket_refuses_a_wrong_token(self):
        host = _host(self.st, [])

        async def go():
            run = asyncio.create_task(host.run())
            while not self.st.perm_sock_path.exists():
                await asyncio.sleep(0.02)
            def call():
                with socket.socket(socket.AF_UNIX) as c:
                    c.settimeout(5)
                    c.connect(str(self.st.perm_sock_path))
                    try:
                        c.sendall(json.dumps({"t": "claim", "token": "nope"}).encode() + b"\n")
                        c.sendall(json.dumps({"t": "ask", "id": 1, "tool": "Bash", "input": {"command": "x"}}).encode() + b"\n")
                        return c.recv(4096)
                    except (ConnectionResetError, BrokenPipeError):   # closed before the second line: nothing either
                        return b""
            ans = await asyncio.to_thread(call)
            host.stopping.set()
            await run
            return ans
        self.assertEqual(asyncio.run(go()), b"", "a wrong claim gets nothing, the connection is closed (L2)")
        self.assertIn('"ev": "perm_refused"', self.st.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
