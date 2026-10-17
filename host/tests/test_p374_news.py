"""P374: Daily AI News — the account's one switch, read / changed from the host.

- cloud.news: one signed `/v1/host/news` (context agentjarvis-host-news-v1, inner keys exactly v/t/channel/ts/on), the
  answer whitelisted (on bool, email ≤ 254 with one '@', sample_url only on our API / Dashboard origin), and every way it can
  go wrong mapped to one word: unlinked · not_bound · rate_limited · unreachable (network / 5xx) · unsupported (404) · fail.
- serve: `news_get` / `news_set` are P33 messages; the call runs off the loop with an 8 s budget; `news_res` goes to the
  asking session only; host.log carries the status word, never the e-mail.
- `agentj news [status|on|off] [--json]`: the same call, through the real CLI against a loopback fake Dashboard.
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

EMAIL = "owner@example.com"
OK = {"ok": True, "on": True, "email": EMAIL, "sample_url": "https://agentj.app/account/news/sample"}


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    return st


def _link(st, api="https://agentj.app/api"):
    cloud.write_cloud(st, {"api": api, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                           "linked_at": int(time.time()), "last_seq": 0})


def _open(env: dict, ctx: str) -> dict:
    """The Dashboard's side of PROTOCOL §7: verify the signature over context + body, return the inner object."""
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


class News(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        p = mock.patch.dict(os.environ, {k: "" for k in ("AGENTJ_API_URL", "AGENTJ_APP_URL")})
        p.start()
        self.addCleanup(p.stop)

    def test_unlinked_sends_nothing(self):
        post = FakePost((200, OK))
        self.assertEqual(cloud.news(self.st, None, post=post).kind, "unlinked")
        self.assertEqual(post.calls, [])

    def test_signed_envelope_and_exact_inner(self):
        _link(self.st)
        for on in (None, True, False):
            post = FakePost((200, {**OK, "on": bool(on)}))
            r = cloud.news(self.st, on, post=post, now=lambda: 1_800_000_000.5)
            self.assertEqual((r.kind, r.on, r.email, r.sample_url, r.status),
                             ("ok", bool(on), EMAIL, OK["sample_url"], "200"))
            url, env = post.calls[0]
            self.assertEqual(url, "https://agentj.app/api/v1/host/news")
            inner = _open(env, "agentjarvis-host-news-v1")
            self.assertEqual(list(inner), ["v", "t", "channel", "ts", "on"])
            self.assertEqual(inner, {"v": 1, "t": "news", "channel": cloud.channel_of(self.st), "ts": 1_800_000_000, "on": on})
            with self.assertRaises(Exception):
                _open(env, cloud.CTX_RENAME)    # one context per route: never valid elsewhere
        self.assertEqual(cloud.CTX_NEWS, "agentjarvis-host-news-v1")
        with self.assertRaises(ValueError):
            cloud.news(self.st, "yes", post=FakePost((200, OK)))

    def test_status_words(self):
        _link(self.st)
        for answer, kind in [((403, {"error": "not_bound"}), "not_bound"), ((403, {"error": "forbidden"}), "fail"),
                             ((429, {"error": "rate_limited"}), "rate_limited"), ((500, {}), "unreachable"),
                             ((503, {}), "unreachable"), ((404, {}), "unsupported"), ((400, {"error": "bad_request"}), "fail"),
                             (cloud.CloudError("network"), "unreachable"), (cloud.CloudError("timeout"), "unreachable")]:
            r = cloud.news(self.st, True, post=FakePost(answer))
            self.assertEqual(r.kind, kind, answer)
            self.assertIsNone(r.email)
        self.assertEqual(cloud.news(self.st, None, post=FakePost((400, {"error": "bad_request"}))).status, "bad_request")
        self.assertEqual([cloud.news_problem(k) for k in ("unlinked", "not_bound", "unreachable", "rate_limited",
                                                           "unsupported", "fail")],
                         ["unlinked", "not_bound", "unreachable", "rate_limited", "fail", "fail"])

    def test_bad_answers_are_fail(self):
        _link(self.st)
        long_mail = "a" * 250 + "@x.io"
        for bad in [{}, {**OK, "ok": False}, {**OK, "ok": 1}, {**OK, "on": "true"}, {**OK, "on": 1}, {**OK, "email": None},
                    {**OK, "email": "no-at-sign"}, {**OK, "email": "a@b@c"}, {**OK, "email": long_mail},
                    {**OK, "email": "a b@c.d"}, {**OK, "email": "a@c.d\n"}, {**OK, "email": "a‮@c.d"},
                    {**OK, "email": "<a@c.d>"}, {k: v for k, v in OK.items() if k != "email"}]:
            r = cloud.news(self.st, None, post=FakePost((200, bad)))
            self.assertEqual((r.kind, r.status), ("fail", "bad_response"), bad)

    def test_queue_place_and_seats(self):
        """P374 追加: all seats taken → on + queued (int ≥ 1); anything malformed or a queue place while off is dropped."""
        _link(self.st)
        r = cloud.news(self.st, True, post=FakePost((200, {**OK, "queued": 3, "seats": 500})))
        self.assertEqual((r.kind, r.on, r.queued, r.seats), ("ok", True, 3, 500))
        for q, s, want in [(None, 500, (None, 500)), ("3", 500, (None, 500)), (0, 500, (None, 500)), (True, 500, (None, 500)),
                           (2.5, "500", (None, None)), (10**8, -1, (None, None))]:
            r = cloud.news(self.st, None, post=FakePost((200, {**OK, "queued": q, "seats": s})))
            self.assertEqual((r.kind, r.queued, r.seats), ("ok", *want), (q, s))
        r = cloud.news(self.st, None, post=FakePost((200, {**OK, "on": False, "queued": 2, "seats": 500})))
        self.assertIsNone(r.queued, "a place in the queue only while on")
        self.assertEqual(cloud.news(self.st, None, post=FakePost((200, OK))).queued, None, "an older Dashboard: no queue fields")

    def test_sample_url_only_on_our_origin(self):
        _link(self.st)
        fallback = "https://agentj.app/account/news/sample"
        for url, want in [("https://agentj.app/account/news/sample", "https://agentj.app/account/news/sample"),
                          ("https://agentj.app/news/sample", "https://agentj.app/news/sample"),
                          ("https://evil.example/news/sample", fallback), ("http://agentj.app/account/news/sample", fallback),
                          ("https://agentj.app.evil.example/x", fallback), ("https://u:p@agentj.app/x", fallback),
                          ("https://agentj.app/x?a=1", fallback), ("https://agentj.app/x#f", fallback),
                          ("https://agentj.app:8443/x", fallback), ("javascript:alert(1)", fallback), (None, fallback),
                          (42, fallback), ("https://agentj.app/x y", fallback)]:
            r = cloud.news(self.st, None, post=FakePost((200, {**OK, "sample_url": url})))
            self.assertEqual((r.kind, r.sample_url), ("ok", want), url)
        with mock.patch.dict(os.environ, {"AGENTJ_APP_URL": "https://app.example.org/account"}):
            r = cloud.news(self.st, None, post=FakePost((200, {**OK, "sample_url": "https://app.example.org/account/news/sample"})))
            self.assertEqual(r.sample_url, "https://app.example.org/account/news/sample", "the Dashboard origin too")
            r = cloud.news(self.st, None, post=FakePost((200, {**OK, "sample_url": "https://evil.example/s"})))
            self.assertEqual(r.sample_url, "https://app.example.org/account/news/sample")

    def test_refused_api_url_is_fail(self):
        _link(self.st)
        with mock.patch.dict(os.environ, {"AGENTJ_API_URL": "http://not-loopback.example"}):
            r = cloud.news(self.st, None, post=FakePost((200, OK)))
        self.assertEqual((r.kind, r.status), ("fail", "refused_url"))


class ServeNews(unittest.TestCase):
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

    def log_lines(self):
        p = self.st.root / "host.log"
        return p.read_text() if p.exists() else ""

    def test_messages_are_p33(self):
        self.assertIn("news_get", serve.P33_ONLY)
        self.assertIn("news_set", serve.P33_ONLY)
        self.assertEqual(serve.NEWS_TIMEOUT, 8.0)

    def test_get_and_set_round_trip(self):
        a, b = self.session(1, "dev-a"), self.session(2, "dev-b")
        calls = []

        def fake(st, on, *, post=cloud.post_json, now=time.time):
            calls.append((on, threading.current_thread() is threading.main_thread()))
            return cloud.NewsResult("ok", bool(on), EMAIL, OK["sample_url"], "200")
        with mock.patch.object(cloud, "news", fake):
            self.run_msg(a, "news_get", {"t": "news_get"})
            self.run_msg(a, "news_set", {"t": "news_set", "on": True, "r": "r1"})
        self.assertEqual(calls, [(None, False), (True, False)], "off the loop's thread; get reads with on=None")
        self.assertEqual(self.sent[0], ("dev-a", {"t": "news_res", "ok": True, "on": False, "email": EMAIL,
                                                  "sample_url": OK["sample_url"], "queued": None, "seats": None}))
        self.assertEqual(self.sent[1], ("dev-a", {"t": "news_res", "r": "r1", "ok": True, "on": True, "email": EMAIL,
                                                  "sample_url": OK["sample_url"], "queued": None, "seats": None}))
        self.assertTrue(all(d == "dev-a" for d, _ in self.sent), "the asking device only")
        self.assertIsNot(b, None)
        log = self.log_lines()
        self.assertIn("news_set", log)
        self.assertNotIn(EMAIL, log)
        self.assertNotIn("example.com", log)

    def test_set_needs_a_bool(self):
        s = self.session()
        with mock.patch.object(cloud, "news", side_effect=AssertionError("must not call")):
            for bad in ({}, {"on": "true"}, {"on": 1}, {"on": None}):
                self.run_msg(s, "news_set", {"t": "news_set", **bad})
        self.assertEqual(self.sent, [])

    def test_problems(self):
        s = self.session()
        for kind, word in (("unlinked", "unlinked"), ("not_bound", "not_bound"), ("rate_limited", "rate_limited"),
                           ("unreachable", "unreachable"), ("unsupported", "fail"), ("fail", "fail")):
            self.sent.clear()
            with mock.patch.object(cloud, "news", return_value=cloud.NewsResult(kind, status="http_4xx")):
                self.run_msg(s, "news_get", {"t": "news_get"})
            self.assertEqual(self.sent, [("dev-a", {"t": "news_res", "ok": False, "problem": word})], kind)
        self.sent.clear()
        with mock.patch.object(cloud, "news", side_effect=OSError("key file")):
            self.run_msg(s, "news_get", {"t": "news_get"})
        self.assertEqual(self.sent[-1][1]["problem"], "fail")

    def test_slow_cloud_is_unreachable_after_the_budget(self):
        s = self.session()
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow(*a, **kw):
            gate.wait(5)
            return cloud.NewsResult("ok", True, EMAIL, None, "200")
        at = []
        orig = self.host.send_app

        async def timed(s2, m):
            at.append(time.monotonic())
            gate.set()         # let the abandoned thread end (asyncio.run waits for its executor on exit)
            return await orig(s2, m)
        self.host.send_app = timed
        with mock.patch.object(serve, "NEWS_TIMEOUT", 0.2), mock.patch.object(cloud, "news", slow):
            t0 = time.monotonic()
            self.run_msg(s, "news_set", {"t": "news_set", "on": False})
        self.assertLess(at[0] - t0, 3, "answered at the budget, not when the call ends")
        self.assertEqual(self.sent, [("dev-a", {"t": "news_res", "ok": False, "problem": "unreachable"})])

    def test_gone_session_gets_nothing(self):
        s = self.session()

        def fake(*a, **kw):
            self.host.sessions.pop(1, None)    # the phone left while the call ran
            return cloud.NewsResult("ok", True, EMAIL, None, "200")
        with mock.patch.object(cloud, "news", fake):
            self.run_msg(s, "news_get", {"t": "news_get"})
        self.assertEqual(self.sent, [])

    def test_burst_is_capped(self):
        s = self.session()
        self.host._news_inflight = serve.NEWS_INFLIGHT
        with mock.patch.object(cloud, "news", side_effect=AssertionError("must not call")):
            self.run_msg(s, "news_get", {"t": "news_get"})
        self.assertEqual(self.sent[-1][1], {"t": "news_res", "ok": False, "problem": "rate_limited"})


class FakeDashboard(BaseHTTPRequestHandler):
    state = {"on": False}
    seen: list = []
    script: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        env = json.loads(self.rfile.read(n))
        if self.path != "/api/v1/host/news":
            return self._send(404, {"error": "not_found"})
        try:
            inner = _open(env, "agentjarvis-host-news-v1")
        except Exception:  # noqa: BLE001
            return self._send(401, {"error": "bad_signature"})
        type(self).seen.append(inner)
        if type(self).script:
            return self._send(*type(self).script.pop(0))
        if inner["on"] is not None:
            type(self).state["on"] = inner["on"]
        host = self.headers.get("host")
        self._send(200, {"ok": True, "on": type(self).state["on"], "email": EMAIL,
                         "sample_url": f"http://{host}/account/news/sample"})

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CliNews(unittest.TestCase):
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
        FakeDashboard.state["on"], FakeDashboard.seen[:], FakeDashboard.script[:] = False, [], []

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "agentj.cli", "news", *args], cwd=HOST, env=self.env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_unlinked(self):
        r = self.cli("--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["problem"], "unlinked")
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("agentj login", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_status_on_off_json_through_a_signed_call(self):
        _link(self.st, self.base + "/api")
        r = self.cli("status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"ok": True, "on": False, "email": EMAIL,
                                                "sample_url": self.base + "/account/news/sample", "queued": None, "seats": None})
        r = self.cli("on", "--json")
        self.assertEqual(json.loads(r.stdout)["on"], True)
        r = self.cli("off", "--json")
        self.assertEqual(json.loads(r.stdout)["on"], False)
        self.assertEqual([i["on"] for i in FakeDashboard.seen], [None, True, False])
        self.assertTrue(all(i["channel"] == cloud.channel_of(self.st) for i in FakeDashboard.seen))
        self.assertNotIn(EMAIL, (self.st.root / "host.log").read_text(), "the e-mail never reaches the log")

    def test_human_output_zh_and_problems(self):
        _link(self.st, self.base + "/api")
        r = self.cli("on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"发到 {EMAIL}", r.stdout)
        self.assertIn("看一封样例", r.stdout)
        r = self.cli()
        self.assertIn("Daily AI News：开", r.stdout)
        FakeDashboard.script[:] = [(403, {"error": "not_bound"})]
        r = self.cli("off")
        self.assertEqual(r.returncode, 1)
        self.assertIn("agentj unlink", r.stderr)
        FakeDashboard.script[:] = [(429, {"error": "rate_limited"})]
        r = self.cli("off", "--json")
        self.assertEqual(json.loads(r.stdout)["problem"], "rate_limited")
        FakeDashboard.script[:] = [(404, {})]
        r = self.cli("--json")
        self.assertEqual(json.loads(r.stdout)["problem"], "fail")

    def test_english(self):
        _link(self.st, self.base + "/api")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), \
                mock.patch.object(cli, "_lang", return_value="en"), contextlib.redirect_stdout(out), \
                mock.patch.object(cloud, "news", return_value=cloud.NewsResult("ok", False, EMAIL, "https://agentj.app/s", "200")):
            cli.cmd_news(SimpleNamespace(mode="status", json=False))
        self.assertIn("Daily AI News: off", out.getvalue())
        self.assertIn("See a sample: https://agentj.app/s", out.getvalue())
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), \
                mock.patch.object(cli, "_lang", return_value="en"), \
                mock.patch.object(cloud, "news", return_value=cloud.NewsResult("unreachable", status="timeout")):
            with self.assertRaises(SystemExit) as cm:
                cli.cmd_news(SimpleNamespace(mode="on", json=False))
        self.assertIn("Could not reach", str(cm.exception.code))

    def test_queued_output(self):
        """P374 追加: all seats taken → the CLI says which place in the waiting list (zh / en) and --json carries it."""
        for lang, want in (("zh", "名额 500 个已满，你在候补第 7 位"), ("en", "all 500 seats are taken; you are number 7 on the waiting list")):
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), \
                    mock.patch.object(cli, "_lang", return_value=lang), contextlib.redirect_stdout(out), \
                    mock.patch.object(cloud, "news", return_value=cloud.NewsResult("ok", True, EMAIL, None, "200", 7, 500)):
                cli.cmd_news(SimpleNamespace(mode="on", json=False))
            self.assertIn(want, out.getvalue())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), contextlib.redirect_stdout(out), \
                mock.patch.object(cloud, "news", return_value=cloud.NewsResult("ok", True, EMAIL, None, "200", 7, 500)):
            cli.cmd_news(SimpleNamespace(mode="status", json=True))
        self.assertEqual({k: json.loads(out.getvalue())[k] for k in ("on", "queued", "seats")}, {"on": True, "queued": 7, "seats": 500})


if __name__ == "__main__":
    unittest.main()
