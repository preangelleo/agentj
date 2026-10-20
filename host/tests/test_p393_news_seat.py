"""P393 #128: Daily AI News belongs to the seat's own person (host part; contract reports/qa/p393/news-128-contract.md).

- cloud.news: a 200 with `need_email: true` (no e-mail) → need_email; 409 need_email / account_email → those words.
- cloud.news_email / news_verify: signed calls (contexts agentjarvis-host-news-{email,verify}-v1, inner keys exactly
  v/t/channel/ts/email/lang and v/t/channel/ts/email/code), checked locally first (a bad e-mail / code never leaves the
  host), answers whitelisted (verify's like news), every error mapped to one word.
- serve: `news_email` / `news_verify` are P33 messages → `news_res` to the asking session only; host.log never has the
  e-mail or the code.
- CLI: `agentj news email <address>` / `agentj news verify <address> <code>` / status in the need-email state.
Every test uses a temporary state directory and HOME; systemctl is a stub on PATH.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402

from agentj import cli, cloud, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402

EMAIL = "seat.person@example.com"
CODE = "042917"
SAMPLE = "https://agentj.app/account/news/sample"
OK = {"ok": True, "on": False, "email": EMAIL, "sample_url": SAMPLE, "queued": None, "seats": 500}
NEED = {"ok": True, "on": False, "queued": None, "seats": 500, "need_email": True, "sample_url": SAMPLE}


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    return st


def _link(st, api="https://agentj.app/api"):
    cloud.write_cloud(st, {"api": api, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                           "linked_at": int(time.time()), "last_seq": 0})


def _open(env: dict, ctx: str) -> dict:
    pk, sig = wire.unb64u(env["pk"]), wire.unb64u(env["sig"])
    Ed25519PublicKey.from_public_bytes(pk).verify(sig, f"{ctx}\n{env['body']}".encode("ascii"))
    return json.loads(wire.unb64u(env["body"]))


class FakePost:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, payload, **kw):
        self.calls.append((url, payload))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


class CloudNews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        p = mock.patch.dict(os.environ, {k: "" for k in ("AGENTJ_API_URL", "AGENTJ_APP_URL")})
        p.start()
        self.addCleanup(p.stop)
        _link(self.st)

    def test_need_email_read(self):
        r = cloud.news(self.st, None, post=FakePost((200, NEED)))
        self.assertEqual((r.kind, r.on, r.email, r.sample_url, r.seats, r.queued), ("need_email", False, None, SAMPLE, 500, None))
        # an e-mail riding along with need_email is never taken
        r = cloud.news(self.st, None, post=FakePost((200, {**NEED, "email": "owner@example.com"})))
        self.assertEqual((r.kind, r.email), ("need_email", None))
        # need_email with on true is incoherent → fail; need_email must be exactly true
        self.assertEqual(cloud.news(self.st, None, post=FakePost((200, {**NEED, "on": True}))).kind, "fail")
        self.assertEqual(cloud.news(self.st, None, post=FakePost((200, {**NEED, "need_email": 1}))).kind, "fail")
        self.assertEqual(cloud.news(self.st, None, post=FakePost((200, {**NEED, "ok": False}))).kind, "fail")
        self.assertEqual(cloud.news(self.st, None, post=FakePost((200, OK))).kind, "ok", "an ordinary answer still works")

    def test_409_words(self):
        for err in ("need_email", "account_email"):
            r = cloud.news(self.st, True, post=FakePost((409, {"error": err})))
            self.assertEqual(r.kind, err)
            self.assertEqual(cloud.news_problem(r.kind), err)
        self.assertEqual(cloud.news(self.st, True, post=FakePost((409, {"error": "other"}))).kind, "fail")
        self.assertEqual(cloud.news(self.st, True, post=FakePost((403, {"error": "not_bound"}))).kind, "not_bound")

    def test_email_signed_inner_and_answer(self):
        for lang in ("zh", "en", "fr"):
            post = FakePost((200, {"ok": True, "expires_in": 600}))
            r = cloud.news_email(self.st, f"  {EMAIL} ", lang, post=post, now=lambda: 1_800_000_000.7)
            self.assertEqual((r.kind, r.expires_in, r.email), ("sent", 600, None))
            url, env = post.calls[0]
            self.assertEqual(url, "https://agentj.app/api/v1/host/news/email")
            inner = _open(env, "agentjarvis-host-news-email-v1")
            self.assertEqual(list(inner), ["v", "t", "channel", "ts", "email", "lang"])
            self.assertEqual(inner, {"v": 1, "t": "news_email", "channel": cloud.channel_of(self.st), "ts": 1_800_000_000,
                                     "email": EMAIL, "lang": "en" if lang == "en" else "zh"})
            for other in (cloud.CTX_NEWS, cloud.CTX_NEWS_VERIFY):
                with self.assertRaises(Exception):
                    _open(env, other)
        self.assertEqual(cloud.CTX_NEWS_EMAIL, "agentjarvis-host-news-email-v1")
        for bad in ({}, {"ok": True}, {"ok": True, "expires_in": "600"}, {"ok": True, "expires_in": 0},
                    {"ok": 1, "expires_in": 600}, {"ok": True, "expires_in": True}):
            self.assertEqual(cloud.news_email(self.st, EMAIL, "zh", post=FakePost((200, bad))).kind, "fail", bad)

    def test_email_checked_locally(self):
        post = FakePost((200, {"ok": True, "expires_in": 600}))
        for bad in ("", "no-at", "a@b@c", "a b@c.d", "<a@c.d>", "a@c.d\x00", None, 42, "a" * 260 + "@x.io"):
            r = cloud.news_email(self.st, bad, "zh", post=post)
            self.assertEqual((r.kind, r.status), ("bad_email", "local"), bad)
            r = cloud.news_verify(self.st, bad, CODE, post=post)
            self.assertEqual(r.kind, "bad_email", bad)
        for bad in ("", "12345", "1234567", "12345a", "１２３４５６", "123 456", None, 123456, "123456\n7"):
            r = cloud.news_verify(self.st, EMAIL, bad, post=post)
            self.assertEqual((r.kind, r.status), ("invalid_code", "local"), bad)
        self.assertEqual(post.calls, [], "nothing left the host")

    def test_email_errors(self):
        for answer, kind in [((400, {"error": "bad_email"}), "bad_email"), ((409, {"error": "account_email"}), "account_email"),
                             ((429, {"error": "rate_limited"}), "rate_limited"), ((502, {"error": "mail_failed"}), "mail_failed"),
                             ((502, {}), "unreachable"), ((503, {"error": "unavailable"}), "unreachable"),
                             ((404, {}), "unsupported"), ((403, {"error": "not_bound"}), "not_bound"),
                             ((400, {"error": "bad_request"}), "fail"), (cloud.CloudError("timeout"), "unreachable")]:
            r = cloud.news_email(self.st, EMAIL, "zh", post=FakePost(answer))
            self.assertEqual(r.kind, kind, answer)
        r = cloud.news_email(self.st, EMAIL, "zh", post=FakePost((429, {"error": "rate_limited", "retry_after": 120})))
        self.assertEqual(r.retry_after, 120)
        for ra in ("120", 0, -1, 10**9, True):
            r = cloud.news_email(self.st, EMAIL, "zh", post=FakePost((429, {"error": "rate_limited", "retry_after": ra})))
            self.assertIsNone(r.retry_after, ra)
        self.assertEqual([cloud.news_problem(k) for k in ("bad_email", "mail_failed", "invalid_code", "sent", "unsupported")],
                         ["bad_email", "mail_failed", "invalid_code", "fail", "fail"])

    def test_unlinked(self):
        self.st = _state(pathlib.Path(self.tmp.name) / "fresh")
        post = FakePost((200, {"ok": True, "expires_in": 600}))
        self.assertEqual(cloud.news_email(self.st, EMAIL, "zh", post=post).kind, "unlinked")
        self.assertEqual(cloud.news_verify(self.st, EMAIL, CODE, post=post).kind, "unlinked")
        self.assertEqual(post.calls, [])

    def test_verify_signed_inner_and_answer(self):
        post = FakePost((200, {**OK, "on": False}))
        r = cloud.news_verify(self.st, EMAIL, f" {CODE} ", post=post, now=lambda: 1_800_000_001)
        self.assertEqual((r.kind, r.on, r.email, r.sample_url, r.seats), ("ok", False, EMAIL, SAMPLE, 500))
        url, env = post.calls[0]
        self.assertEqual(url, "https://agentj.app/api/v1/host/news/verify")
        inner = _open(env, "agentjarvis-host-news-verify-v1")
        self.assertEqual(list(inner), ["v", "t", "channel", "ts", "email", "code"])
        self.assertEqual(inner, {"v": 1, "t": "news_verify", "channel": cloud.channel_of(self.st), "ts": 1_800_000_001,
                                 "email": EMAIL, "code": CODE})
        with self.assertRaises(Exception):
            _open(env, cloud.CTX_NEWS_EMAIL)
        # parsed like news: sample_url only on our origin, a bad e-mail → fail
        r = cloud.news_verify(self.st, EMAIL, CODE, post=FakePost((200, {**OK, "sample_url": "https://evil.example/s"})))
        self.assertEqual(r.sample_url, SAMPLE)
        self.assertEqual(cloud.news_verify(self.st, EMAIL, CODE, post=FakePost((200, {**OK, "email": "x"}))).kind, "fail")
        for answer, kind in [((401, {"error": "invalid_code"}), "invalid_code"), ((401, {"error": "bad_signature"}), "fail"),
                             ((409, {"error": "account_email"}), "account_email"), ((429, {}), "rate_limited"),
                             ((500, {}), "unreachable")]:
            self.assertEqual(cloud.news_verify(self.st, EMAIL, CODE, post=FakePost(answer)).kind, kind, answer)


class ServeNewsSeat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        for p in (mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "cfg")}),
                  mock.patch.object(type(self.st), "is_allowed", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.sent = []

        async def send_app(s, m):
            self.sent.append((s.device, m))
            return True
        self.host.send_app = send_app

    def session(self, cid=1, device="dev-a"):
        s = SimpleNamespace(cid=cid, device=device, state="ready", pub=b"x" * 32, p33=True, name="手机")
        self.host.sessions[cid] = s
        return s

    def run_msg(self, s, t, obj):
        async def go():
            await self.host.on_p33(s, t, obj)
            pending = list(self.host.__dict__.get("_bg_tasks", ()))
            if pending:
                await asyncio.gather(*pending)
        asyncio.run(go())

    def log_text(self):
        p = self.st.root / "host.log"
        return p.read_text() if p.exists() else ""

    def test_messages_are_p33(self):
        self.assertIn("news_email", serve.P33_ONLY)
        self.assertIn("news_verify", serve.P33_ONLY)

    def test_need_email_read(self):
        s = self.session()
        with mock.patch.object(cloud, "news", return_value=cloud.NewsResult("need_email", False, None, SAMPLE, "200", None, 500)):
            self.run_msg(s, "news_get", {"t": "news_get", "r": "r0"})
        self.assertEqual(self.sent, [("dev-a", {"t": "news_res", "r": "r0", "ok": True, "on": False, "email": None,
                                                "sample_url": SAMPLE, "queued": None, "seats": 500, "need_email": True})])

    def test_email_then_verify_round_trip(self):
        a, _b = self.session(1, "dev-a"), self.session(2, "dev-b")
        calls = []

        def fake_email(st, email, lang, *, post=None, now=None):
            calls.append(("email", email, lang, threading.current_thread() is threading.main_thread()))
            return cloud.NewsResult("sent", status="200", expires_in=600)

        def fake_verify(st, email, code, *, post=None, now=None):
            calls.append(("verify", email, code, threading.current_thread() is threading.main_thread()))
            return cloud.NewsResult("ok", False, email, SAMPLE, "200", None, 500)
        with mock.patch.object(cloud, "news_email", fake_email), mock.patch.object(cloud, "news_verify", fake_verify):
            self.run_msg(a, "news_email", {"t": "news_email", "r": "r1", "email": EMAIL})
            self.run_msg(a, "news_email", {"t": "news_email", "email": EMAIL, "lang": "en"})
            self.run_msg(a, "news_verify", {"t": "news_verify", "r": "r2", "email": EMAIL, "code": CODE})
        self.assertEqual(calls, [("email", EMAIL, "zh", False), ("email", EMAIL, "en", False), ("verify", EMAIL, CODE, False)])
        self.assertEqual(self.sent[0], ("dev-a", {"t": "news_res", "r": "r1", "ok": True, "sent": True, "expires_in": 600}))
        self.assertEqual(self.sent[2], ("dev-a", {"t": "news_res", "r": "r2", "ok": True, "on": False, "email": EMAIL,
                                                  "sample_url": SAMPLE, "queued": None, "seats": 500}))
        self.assertTrue(all(d == "dev-a" for d, _ in self.sent), "the asking device only")
        log = self.log_text()
        self.assertIn("news_email", log)
        self.assertIn("news_verify", log)
        for secret in (EMAIL, "example.com", CODE):
            self.assertNotIn(secret, log)

    def test_real_cloud_path_logs_no_secrets(self):
        """Through the real cloud functions (fake HTTP): the e-mail and code reach only the signed body."""
        _link(self.st)
        s = self.session()
        answers = iter([(200, {"ok": True, "expires_in": 600}), (401, {"error": "invalid_code"})])
        with mock.patch.object(cloud, "post_json", lambda url, payload, **kw: next(answers)):
            self.run_msg(s, "news_email", {"t": "news_email", "email": EMAIL})
            self.run_msg(s, "news_verify", {"t": "news_verify", "email": EMAIL, "code": CODE})
        self.assertEqual([m for _, m in self.sent], [{"t": "news_res", "ok": True, "sent": True, "expires_in": 600},
                                                     {"t": "news_res", "ok": False, "problem": "invalid_code"}])
        log = self.log_text()
        self.assertNotIn(EMAIL, log)
        self.assertNotIn(CODE, log)

    def test_malformed_messages_are_dropped(self):
        s = self.session()
        with mock.patch.object(cloud, "news_email", side_effect=AssertionError("must not call")), \
                mock.patch.object(cloud, "news_verify", side_effect=AssertionError("must not call")):
            for t, bad in (("news_email", {}), ("news_email", {"email": 1}), ("news_verify", {"email": EMAIL}),
                           ("news_verify", {"code": CODE}), ("news_verify", {"email": EMAIL, "code": 42917})):
                self.run_msg(s, t, {"t": t, **bad})
        self.assertEqual(self.sent, [])

    def test_problems(self):
        s = self.session()
        for kind in ("need_email", "invalid_code", "bad_email", "account_email", "rate_limited", "mail_failed",
                     "unreachable", "unsupported", "fail"):
            self.sent.clear()
            with mock.patch.object(cloud, "news_email", return_value=cloud.NewsResult(kind, status="http_4xx")):
                self.run_msg(s, "news_email", {"t": "news_email", "email": EMAIL})
            self.assertEqual(self.sent, [("dev-a", {"t": "news_res", "ok": False, "problem": cloud.news_problem(kind)})], kind)
        self.sent.clear()
        with mock.patch.object(cloud, "news_verify", return_value=cloud.NewsResult("rate_limited", status="http_4xx",
                                                                                     retry_after=90)):
            self.run_msg(s, "news_verify", {"t": "news_verify", "email": EMAIL, "code": CODE})
        self.assertEqual(self.sent[-1][1], {"t": "news_res", "ok": False, "problem": "rate_limited", "retry_after": 90})
        self.sent.clear()
        with mock.patch.object(cloud, "news", return_value=cloud.NewsResult("need_email", status="http_4xx")):
            self.run_msg(s, "news_set", {"t": "news_set", "on": True})
        self.assertEqual(self.sent[-1][1], {"t": "news_res", "ok": False, "problem": "need_email"})

    def test_shared_inflight_cap(self):
        s = self.session()
        self.host._news_inflight = serve.NEWS_INFLIGHT
        with mock.patch.object(cloud, "news_email", side_effect=AssertionError("must not call")):
            self.run_msg(s, "news_email", {"t": "news_email", "email": EMAIL})
        self.assertEqual(self.sent[-1][1], {"t": "news_res", "ok": False, "problem": "rate_limited"})


class FakeDashboard(BaseHTTPRequestHandler):
    seen: list = []
    script: list = []
    CTX = {"/api/v1/host/news": "agentjarvis-host-news-v1", "/api/v1/host/news/email": "agentjarvis-host-news-email-v1",
           "/api/v1/host/news/verify": "agentjarvis-host-news-verify-v1"}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        env = json.loads(self.rfile.read(n))
        ctx = self.CTX.get(self.path)
        if not ctx:
            return self._send(404, {"error": "not_found"})
        try:
            inner = _open(env, ctx)
        except Exception:  # noqa: BLE001
            return self._send(401, {"error": "bad_signature"})
        type(self).seen.append(inner)
        if type(self).script:
            return self._send(*type(self).script.pop(0))
        host = self.headers.get("host")
        sample = f"http://{host}/account/news/sample"
        if inner["t"] == "news_email":
            return self._send(200, {"ok": True, "expires_in": 600})
        if inner["t"] == "news_verify":
            if inner["code"] != CODE:
                return self._send(401, {"error": "invalid_code"})
            return self._send(200, {"ok": True, "on": False, "email": inner["email"], "sample_url": sample, "seats": 500})
        if inner["on"]:
            return self._send(409, {"error": "need_email"})
        self._send(200, {"ok": True, "on": False, "queued": None, "seats": 500, "need_email": True, "sample_url": sample})

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CliNewsSeat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeDashboard)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        stub = root / "bin"
        stub.mkdir()
        (stub / "systemctl").write_text("#!/bin/sh\nexit 1\n")
        (stub / "systemctl").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTJ_", "AGENTJARVIS_"))}
        self.env.update({"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"), "AGENTJ_TEST": "1",
                         "AGENTJ_STATE_DIR": str(root / "state"), "AGENTJ_SKILL_LINK": "off", "LANG": "C.UTF-8",
                         "AGENTJ_APP_URL": self.base + "/account", "PATH": f"{stub}:{os.environ.get('PATH', '')}"})
        self.st = State(root / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        _link(self.st, self.base + "/api")
        FakeDashboard.seen[:], FakeDashboard.script[:] = [], []

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "agentj.cli", "news", *args], cwd=HOST, env=self.env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_need_email_flow_json(self):
        r = self.cli("status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"ok": True, "on": False, "email": None, "need_email": True, "queued": None,
                                                "seats": 500, "sample_url": self.base + "/account/news/sample"})
        r = self.cli("on", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)["problem"]), (1, "need_email"))
        r = self.cli("email", EMAIL, "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"ok": True, "sent": True, "expires_in": 600}), r.stderr)
        r = self.cli("verify", EMAIL, "000000", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)["problem"]), (1, "invalid_code"))
        r = self.cli("verify", EMAIL, CODE, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["email"], EMAIL)
        self.assertEqual([i["t"] for i in FakeDashboard.seen], ["news", "news", "news_email", "news_verify", "news_verify"])
        self.assertEqual(FakeDashboard.seen[2]["lang"], "zh")
        log = (self.st.root / "host.log").read_text()
        self.assertNotIn(EMAIL, log)
        self.assertNotIn(CODE, log)

    def test_human_zh(self):
        r = self.cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Daily AI News：关", r.stdout)
        self.assertIn("agentj news email", r.stdout)
        r = self.cli("email", EMAIL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"验证码已发到 {EMAIL}", r.stdout)
        self.assertIn("10 分钟", r.stdout)
        r = self.cli("verify", EMAIL, CODE)
        self.assertIn(f"发到 {EMAIL}", r.stdout)
        FakeDashboard.script[:] = [(409, {"error": "account_email"})]
        r = self.cli("email", EMAIL)
        self.assertEqual(r.returncode, 1)
        self.assertIn("账号后台", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_usage_and_local_checks(self):
        for args in (("email",), ("verify", EMAIL), ("on", "x"), ("email", EMAIL, "extra")):
            r = self.cli(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertNotIn("Traceback", r.stderr)
        r = self.cli("email", "not-an-email", "--json")
        self.assertEqual(json.loads(r.stdout)["problem"], "bad_email")
        r = self.cli("verify", EMAIL, "12ab56", "--json")
        self.assertEqual(json.loads(r.stdout)["problem"], "invalid_code")
        self.assertEqual(FakeDashboard.seen, [], "local checks: nothing sent")

    def test_english(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), \
                mock.patch.object(cli, "_lang", return_value="en"), contextlib.redirect_stdout(out), \
                mock.patch.object(cloud, "news", return_value=cloud.NewsResult("need_email", False, None, None, "200")):
            cli.cmd_news(SimpleNamespace(mode="status", json=False, args=[]))
        self.assertIn("Daily AI News: off", out.getvalue())
        self.assertIn("agentj news email <your email>", out.getvalue())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), \
                mock.patch.object(cli, "_lang", return_value="en"), contextlib.redirect_stdout(out), \
                mock.patch.object(cloud, "news_email", return_value=cloud.NewsResult("sent", status="200", expires_in=600)) as m:
            cli.cmd_news(SimpleNamespace(mode="email", json=False, args=[EMAIL]))
        self.assertEqual(m.call_args.args[2], "en")
        self.assertIn(f"Code sent to {EMAIL} (good for 10 minutes)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
