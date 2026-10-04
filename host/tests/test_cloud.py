"""PROTOCOL.md §7 host side, in-process: envelope vectors, report schema, seq, URL policy, whitelisted parsing, the
"cloud code never touches the allowlist" invariant, login polling/back-off, and the serve report scheduler."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import ast
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from agentj import cloud, reporter, wire  # noqa: E402
from agentj.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parents[1] / "agentj"
VEC = json.loads((pathlib.Path(__file__).resolve().parents[2] / "protocol/vectors/host-envelope.json").read_text())


def _state(tmp) -> State:
    st = State(pathlib.Path(tmp) / "s")
    st.init(relay="ws://127.0.0.1:1")
    return st


def _link(st, api, last_seq=0):
    cloud.write_cloud(st, {"api": api, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                           "linked_at": int(time.time()), "last_seq": last_seq})


async def _until(pred, secs: float = 10.0) -> None:
    """Wait for a condition instead of a fixed sleep (the suites also run under CPU load)."""
    deadline = time.monotonic() + secs
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


class Envelope(unittest.TestCase):
    def test_vectors_byte_exact(self):
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(VEC["ed25519_seed_hex"]))
        for c in VEC["cases"]:
            env = cloud.envelope(c["context"], c["inner"], sk)
            self.assertEqual(env["body"], c["envelope"]["body"], c["context"])
            self.assertEqual(env["sig"], c["envelope"]["sig"], c["context"])
            self.assertEqual(env["pk"], c["envelope"]["pk"])
        # the vector covers the contexts this host actually signs with (seat-bind included, seat setup §4; plaza post, P2; package publish)
        self.assertEqual(sorted(c["context"] for c in VEC["cases"]), sorted([cloud.CTX_LOGIN, cloud.CTX_REPORT, cloud.CTX_SEAT_BIND, cloud.CTX_PLAZA_POST, cloud.CTX_PKG["publish"]]))

    def test_channel_derivation_matches_vector(self):
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(VEC["ed25519_seed_hex"]))
        pub = wire.unb64u(cloud.envelope("x", {}, sk)["pk"])
        self.assertEqual(wire.b64u(pub), VEC["pk"])
        self.assertEqual(wire.channel_id(pub), VEC["channel"])

    def test_contexts_distinct_from_relay(self):
        self.assertEqual(len({cloud.CTX_LOGIN, cloud.CTX_POLL, cloud.CTX_REPORT, "agentjarvis-relay-auth-v1"}), 4)
        self.assertEqual(cloud.AGENT, "agentj/0.12.1a1")


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema(self):
        # 70 entries: more than add_device allows (5) — a hand-edited / pre-A3.1 allowlist — to pin the report's own 64 cap
        ids, raw = [], {}
        for i in range(70):
            evil = f"手机{i}\x1b[2J\n‮1234567890" + "名" * 100
            pub = os.urandom(32)
            ids.append(wire.device_id(pub))
            raw[ids[-1]] = {"pub": wire.b64u(pub), "name": evil, "paired_at": 1789990000 + i}
        self.st.write_private(self.st.devices_path, json.dumps(raw, ensure_ascii=False).encode())
        inner = cloud.build_report(self.st, {ids[-1], "not-a-device"}, {"count": 1, "since": 1789999990.7, "x": 1},
                                   seq=5, ts=1790000000)
        self.assertEqual(list(inner), ["v", "t", "channel", "ts", "seq", "agent", "devices", "pending",
                                      "agent_name", "machine"])
        self.assertEqual(inner["channel"], self.st.config()["channel"])
        self.assertLessEqual(len(inner["devices"]), 64)
        self.assertEqual(len(inner["devices"]), 64)
        for d in inner["devices"]:
            self.assertEqual(set(d), {"id", "name", "paired_at", "online"})
            self.assertRegex(d["id"], r"^[A-Za-z0-9_-]{16}$")
            self.assertIsInstance(d["paired_at"], int)
            self.assertIs(type(d["online"]), bool)
            self.assertLessEqual(len(d["name"]), 64)
            self.assertFalse(any(ord(c) < 32 or c == "‮" for c in d["name"]), d["name"])
        self.assertEqual(sum(d["online"] for d in inner["devices"]), 1)
        self.assertEqual(inner["pending"], {"count": 1, "since": 1789999990})
        self.assertEqual(cloud.norm_pending({"count": 0, "since": 5}), {"count": 0, "since": None})
        self.assertEqual(cloud.norm_pending({"count": True}), {"count": 0, "since": None})
        env = cloud._report_envelope(self.st, inner)
        self.assertLessEqual(len(json.dumps(env, separators=(",", ":"))), cloud.MAX_ENVELOPE)

    def test_seq_strictly_increasing_and_persisted(self):
        self.assertEqual(cloud.next_seq(10**15, 1.0), 10**15 + 1)  # clock behind last_seq → still increases
        self.assertEqual(cloud.next_seq(5, 1790000000), 1790000000000)
        _link(self.st, "http://127.0.0.1:9")
        seqs = []

        def post(url, env):
            seqs.append(json.loads(wire.unb64u(env["body"]))["seq"])
            return 200, {"ok": True}
        for _ in range(5):
            self.assertEqual(cloud.send_report(self.st, set(), {}, post=post, now=lambda: 1790000000.0).kind, "ok")
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertEqual(cloud.read_cloud(self.st)["last_seq"], seqs[-1])

    def test_unlink_during_report_does_not_resurrect_cloud_json(self):
        _link(self.st, "http://127.0.0.1:9")

        def post(url, env):
            cloud.delete_cloud(self.st)
            return 200, {"ok": True}
        cloud.send_report(self.st, set(), {}, post=post)
        cloud._store_seq(self.st, "h_1", 10**13)
        self.assertFalse(self.st.cloud_path.exists())


    def test_unlink_between_seq_read_and_replace_waits_for_the_lock(self):
        """A3-04: `agentj unlink` (another process / thread) lands exactly between _store_seq's re-read and its replace."""
        _link(self.st, "http://127.0.0.1:9")
        real_read = cloud.read_cloud
        unlinker: list[threading.Thread] = []

        def read_then_unlink(st):
            cur = real_read(st)
            if not unlinker:                     # first read inside _store_seq: start the unlink now and give it time
                t = threading.Thread(target=cloud.delete_cloud, args=(st,))
                unlinker.append(t)
                t.start()
                t.join(0.3)                      # with the lock it is still waiting here; without, it has already unlinked
            return cur
        cloud.read_cloud = read_then_unlink
        try:
            cloud._store_seq(self.st, "h_1", 10**13)
        finally:
            cloud.read_cloud = real_read
        unlinker[0].join(5)
        self.assertFalse(unlinker[0].is_alive())
        self.assertFalse(self.st.cloud_path.exists(), "unlink wins in the end; the seq write never resurrects cloud.json")
        self.assertEqual(os.stat(self.st.cloud_lock_path).st_mode & 0o777, 0o600)
        self.st.check_perms()

    def test_store_seq_never_creates_a_missing_file(self):
        cloud._store_seq(self.st, "h_1", 5)
        self.assertFalse(self.st.cloud_path.exists())

    def test_lock_is_exclusive_across_processes(self):
        import subprocess
        _link(self.st, "http://127.0.0.1:9")
        code = ("import sys, pathlib; sys.path.insert(0, sys.argv[1]); from agentj import cloud; from agentj.state import State;"
                "print(cloud.delete_cloud(State(pathlib.Path(sys.argv[2]))))")
        with cloud.cloud_lock(self.st):
            p = subprocess.Popen([sys.executable, "-c", code, str(PKG.parent), str(self.st.root)], stdout=subprocess.PIPE, text=True)
            time.sleep(0.5)
            self.assertIsNone(p.poll(), "the other process waits for the lock")
            self.assertTrue(self.st.cloud_path.exists())
        self.assertEqual(p.communicate(timeout=10)[0].strip(), "True")
        self.assertFalse(self.st.cloud_path.exists())


class Urls(unittest.TestCase):
    def test_plain_http_only_to_loopback(self):
        for bad in ("http://example.com", "http://10.0.0.1:8787", "http://agentj.app/api", "ftp://x",
                    "https://u:p@agentj.app/api", "https://agentj.app/api/?x=1", "javascript:alert(1)", "",
                    "https://a\x1b[2J.net"):
            with self.assertRaises(cloud.CloudError, msg=bad):
                cloud.check_url(bad)
        for ok in ("https://agentj.app/api", "http://127.0.0.1:1234", "http://localhost:9/"):
            cloud.check_url(ok)
        with self.assertRaises(cloud.CloudError) as e:
            cloud.post_json("http://192.0.2.1/v1/host/report", {"x": 1})
        self.assertEqual(e.exception.kind, "refused_url")

    def test_api_url_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            old = os.environ.pop("AGENTJ_API_URL", None)
            try:
                self.assertEqual(cloud.api_url(st), cloud.DEFAULT_API)
                self.assertEqual(cloud.api_url(st, {"api": "http://127.0.0.1:2"}), "http://127.0.0.1:2")
                os.environ["AGENTJ_API_URL"] = "http://127.0.0.1:3/"
                self.assertEqual(cloud.api_url(st, {"api": "http://127.0.0.1:2"}), "http://127.0.0.1:3")
                self.assertEqual(cloud.api_url(st, override="https://x.example"), "https://x.example")
                os.environ["AGENTJ_API_URL"] = "http://evil.example"
                with self.assertRaises(cloud.CloudError):
                    cloud.api_url(st)
            finally:
                os.environ.pop("AGENTJ_API_URL", None)
                if old is not None:
                    os.environ["AGENTJ_API_URL"] = old


    def test_dashboard_uri_only_on_the_configured_origin(self):
        """F7: the human is sent to the configured Dashboard; a server-chosen URL only if it is on that origin."""
        app = "https://agentj.app/account"     # 0.10: the Dashboard lives under a path; the check compares the origin only
        self.assertEqual(cloud.dashboard_uri(app, "https://agentj.app/account/"), "https://agentj.app/account/")
        self.assertEqual(cloud.dashboard_uri(app, "https://agentj.app/account/link"), "https://agentj.app/account/link")
        self.assertEqual(cloud.dashboard_uri(app, "https://agentj.app:443/account/link"), "https://agentj.app:443/account/link")
        for evil in ("https://agentj.app.evil.example/account/", "https://evil.example/agentj.app/account",
                     "http://agentj.app/account/", "https://agentj.app:8443/account/", "https://u@agentj.app/account/",
                     "https://alpha-app.agentjarvis.net/link", None, "", "https://agentj.app/account/\x1b[2J"):
            self.assertEqual(cloud.dashboard_uri(app, evil), app + "/", evil)
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            old = os.environ.pop("AGENTJ_APP_URL", None)
            try:
                self.assertEqual(cloud.app_url(st), cloud.DEFAULT_APP)
                os.environ["AGENTJ_APP_URL"] = "http://localhost:8123/"
                self.assertEqual(cloud.app_url(st), "http://localhost:8123")
                os.environ["AGENTJ_APP_URL"] = "http://evil.example"
                with self.assertRaises(cloud.CloudError):
                    cloud.app_url(st)
            finally:
                os.environ.pop("AGENTJ_APP_URL", None)
                if old is not None:
                    os.environ["AGENTJ_APP_URL"] = old


class Whitelist(unittest.TestCase):
    def test_parsers_keep_only_named_fields(self):
        p = cloud.parse_poll({"status": "bound", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "A\x1b]52;\nB",
                              "devices": [1]}, "devices": [{"id": "x"}], "approve": True})
        self.assertEqual(p, {"status": "bound", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "A ]52; B"},
                             "agent_name": None})
        self.assertIsNone(cloud.parse_poll({"status": "approved"}))
        self.assertIsNone(cloud.parse_poll({"status": "bound", "host_id": "h", "tenant": {"slug": "Bad Slug", "name": ""}}))
        good = {"login_id": "A" * 22, "user_code": "BCDF-2345", "verification_uri": "https://agentj.app/account/link",
                "expires_in": 600, "interval": 1, "devices": []}
        self.assertEqual(set(cloud.parse_login(good)), {"login_id", "user_code", "verification_uri", "expires_in", "interval"})
        self.assertEqual(cloud.parse_login(good)["interval"], 4)  # never poll faster than 4 s
        for k, v in (("verification_uri", "http://evil.example/x"), ("verification_uri", "https://x\x1b[2J"),
                     ("user_code", "AEIO-0000"), ("login_id", "short"), ("expires_in", True)):
            self.assertIsNone(cloud.parse_login({**good, k: v}), k)
        self.assertIsNone(cloud.parse_error({"error": "x" * 99}))

    def test_malicious_poll_answer_changes_nothing_in_devices_json(self):
        with tempfile.TemporaryDirectory() as d, FakeCP(interval=4, bound_after=1) as cp:
            st = _state(d)
            st.add_device(os.urandom(32), "mine")
            before = st.devices_path.read_bytes()
            slept = []
            res = cloud.login(st, cp.url, show=lambda lg: None, confirm=lambda t, n=None: True, sleep=lambda s: slept.append(s))
            self.assertEqual(res["status"], "bound")
            self.assertEqual(st.devices_path.read_bytes(), before)
            raw = json.loads(st.cloud_path.read_text())
            self.assertEqual(set(raw), {"api", "host_id", "tenant", "linked_at", "last_seq", "via"})
            self.assertEqual(set(raw["tenant"]), {"slug", "name"})
            self.assertNotIn("\x1b", raw["tenant"]["name"])
            self.assertEqual(os.stat(st.cloud_path).st_mode & 0o777, 0o600)
            st.check_perms()

    def test_cloud_code_never_touches_the_allowlist(self):
        forbidden = {"add_device", "remove_device", "devices_path", "_write_private", "serve"}
        for name in ("cloud.py", "reporter.py"):
            tree = ast.parse((PKG / name).read_text())
            for node in ast.walk(tree):
                ident = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                         else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
                self.assertNotIn(ident, forbidden, f"{name}:{getattr(node, 'lineno', '?')}")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                    self.assertFalse(any(m in ("serve", "state") or m.endswith(".serve") for m in mods), name)
            body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            self.assertNotIn("devices.json", "\n".join(ast.unparse(n) for n in body), name)  # code, not the docstring

    def test_write_private_only_from_the_cloud_json_writer(self):
        """F11: the private-file writer is reachable from cloud code only inside the one cloud.json writer, and that writer
        only ever writes st.cloud_path; reporter.py never writes files at all."""
        for name in ("cloud.py", "reporter.py"):
            tree = ast.parse((PKG / name).read_text())
            calls = []
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Attribute) and node.attr in ("write_private", "write_bytes", "write_text"):
                            calls.append((fn.name, node.attr))
            self.assertEqual(calls, [("_write_cloud_file", "write_private")] if name == "cloud.py" else [], name)
        tree = ast.parse((PKG / "cloud.py").read_text())
        writer = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_write_cloud_file")
        call = next(n for n in ast.walk(writer) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "write_private")
        self.assertEqual(ast.unparse(call.args[0]), "st.cloud_path")
        for n in ast.walk(tree):   # no other file-writing primitive in cloud.py (the lock file is opened O_RDWR, never written)
            if isinstance(n, ast.Call) and ast.unparse(n.func) in ("open", "os.write", "os.replace", "os.rename", "shutil.copy"):
                self.fail(ast.unparse(n))


class LoginPolling(unittest.TestCase):
    def test_declined_tenant_writes_nothing(self):
        """F5: the bound answer names a tenant; if the human says no, cloud.json is never written and nothing is reported."""
        with tempfile.TemporaryDirectory() as d, FakeCP(interval=4, bound_after=1) as cp:
            st = _state(d)
            seen = []
            res = cloud.login(st, cp.url, show=lambda lg: None, confirm=lambda t, n=None: seen.append(t) or False, sleep=lambda s: None)
            self.assertEqual(res["status"], "declined")
            self.assertEqual(seen, [{"slug": "acme-co", "name": "Acme [2J Co"}])
            self.assertFalse(st.cloud_path.exists())
            self.assertEqual(cp.reports, [])


    def test_interval_floor_and_slow_down_backoff(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            answers = iter([(201, {"login_id": "A" * 22, "user_code": "BCDF-2345", "verification_uri": "https://x.example/l",
                                   "expires_in": 600, "interval": 2}),
                            (429, {"error": "slow_down"}), (200, {"status": "pending"}),
                            (200, {"status": "rejected", "host_id": "h", "tenant": {"slug": "acme-co", "name": "x"}})])
            slept = []
            res = cloud.login(st, "https://x.example", show=lambda lg: None, confirm=lambda t, n=None: True, post=lambda u, e: next(answers),
                              sleep=slept.append)
            self.assertEqual(res, {"status": "rejected"})
            self.assertEqual(slept, [4, 9, 9])
            self.assertFalse(st.cloud_path.exists())

    def test_already_bound_and_expired(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            self.assertEqual(cloud.login(st, "https://x.example", show=print, confirm=lambda t, n=None: True,
                                         post=lambda u, e: (409, {"error": "already_bound"}))["status"], "already_bound")
            answers = iter([(201, {"login_id": "A" * 22, "user_code": "BCDF-2345", "verification_uri": "https://x.example/l",
                                   "expires_in": 10, "interval": 5})])
            clock = [1000.0]

            def sleep(s):
                clock[0] += s
            res = cloud.login(st, "https://x.example", show=lambda lg: None, confirm=lambda t, n=None: True,
                              post=lambda u, e: next(answers, (200, {"status": "pending"})), sleep=sleep, now=lambda: clock[0])
            self.assertEqual(res["status"], "expired")


class Scheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        _link(self.st, "http://127.0.0.1:9")

    def tearDown(self):
        self.tmp.cleanup()

    def _log(self):
        return [json.loads(line) for line in self.st.log_path.read_text().splitlines()]

    def test_debounce_coalesces_a_burst(self):
        calls = []

        def send(st, online, pending):
            calls.append((online, pending))
            return cloud.ReportResult("ok", "200", len(calls))

        async def run():
            r = reporter.Reporter(self.st, lambda: ({"D"}, {"count": 1, "since": 5}), send=send, debounce=0.2, heartbeat=60)
            task = asyncio.create_task(r.run())
            for why in ("approve", "online", "pending", "revoke"):
                r.trigger(why)
                await asyncio.sleep(0.02)
            await _until(lambda: r.results)          # the one report has completed …
            await asyncio.sleep(0.4)                  # … and nothing else arrives within 2 more debounce windows
            task.cancel()
            return r
        r = asyncio.run(run())
        self.assertEqual(len(calls), 1, "start + 4 triggers inside the debounce window = one report")
        self.assertEqual(calls[0], ({"D"}, {"count": 1, "since": 5}))
        ok = [e for e in self._log() if e["ev"] == "report_ok"]
        self.assertEqual(ok[0]["seq"], 1)
        self.assertEqual(r.results[0].kind, "ok")

    def test_heartbeat(self):
        calls = []

        async def run():
            r = reporter.Reporter(self.st, lambda: (set(), {}), send=lambda *a: calls.append(1) or cloud.ReportResult("ok", "200", 1),
                                  debounce=0, heartbeat=0.15)
            task = asyncio.create_task(r.run())
            await asyncio.sleep(0.7)
            task.cancel()
        asyncio.run(run())
        self.assertGreaterEqual(len(calls), 3)

    def test_unbound_logged_once_then_silent(self):
        calls = []

        async def run():
            r = reporter.Reporter(self.st, lambda: (set(), {}),
                                  send=lambda *a: calls.append(1) or cloud.ReportResult("unbound", "http_4xx", 1),
                                  debounce=0, heartbeat=0.05)
            task = asyncio.create_task(r.run())
            await asyncio.sleep(0.4)
            task.cancel()
        asyncio.run(run())
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(e["ev"] == "report_unbound" for e in self._log()), 1)
        self.assertTrue(self.st.cloud_path.exists(), "403 does not delete cloud.json")

    def test_hung_http_never_blocks_the_loop_and_one_in_flight(self):
        release = threading.Event()
        calls = []

        def send(st, online, pending):
            calls.append(1)
            release.wait(5)
            return cloud.ReportResult("ok", "200", 1)

        async def run():
            r = reporter.Reporter(self.st, lambda: (set(), {}), send=send, debounce=0, heartbeat=60, hard_timeout=0.2)
            t0 = time.monotonic()
            res = await r.send_once("start")
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(res.status, "timeout")
            ticks = 0
            for _ in range(5):  # the loop is alive
                await asyncio.sleep(0.01)
                ticks += 1
            self.assertIsNone(await r.send_once("approve"))  # previous call still in flight → skipped, not stacked
            release.set()
            await _until(lambda: r.inflight.done())   # the released call settles on the loop (no fixed sleep: CPU load)
            self.assertEqual((await r.send_once("revoke")).kind, "ok")
            return ticks
        self.assertEqual(asyncio.run(run()), 5)
        self.assertEqual(len(calls), 2)
        statuses = [e.get("status") for e in self._log() if e["ev"] == "report_fail"]
        self.assertEqual(statuses, ["timeout", "busy"])

    def test_send_exception_and_unlinked_are_contained(self):
        async def run():
            r = reporter.Reporter(self.st, lambda: (set(), {}), send=lambda *a: 1 / 0, debounce=0, heartbeat=60)
            self.assertEqual((await r.send_once("start")).status, "error")
            cloud.delete_cloud(self.st)
            self.assertIsNone(await r.send_once("heartbeat"))
        asyncio.run(run())

    def test_rate_limit_window(self):
        async def run():
            r = reporter.Reporter(self.st, lambda: (set(), {}), send=lambda *a: cloud.ReportResult("ok", "200", 1),
                                  rate_per_hour=2)
            await r.send_once("a")
            await r.send_once("b")
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(r._rate_wait(), 0.2)
        asyncio.run(run())

    def test_log_records_are_metadata_only(self):
        self.st.log("report_fail", status="http_5xx", trigger="approve", url="https://x/?t=secret", name=None)
        rec = self._log()[-1]
        self.assertNotIn("url", rec)


if __name__ == "__main__":
    unittest.main()
