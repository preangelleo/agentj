"""A3.2: Agent name rules (Python mirror of dashboard/public/agentname.js), the name over
the control plane (poll / sync / report / rename), `agentj name`, and the host-local Agent admin page (`agentj admin`):
loopback only, one-time token (URL fragment) → bearer session (no cookie), Host / Origin / JSON / size checks, and approval only through the typed code — driven
against a real `agentj serve` on a test relay with a Python device."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import http.client
import json
import os
import pathlib
import re
import shutil
import signal
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import gate, admin, cloud, names, serve, text, wire  # noqa: E402
from agentj.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402
from fakerelay import FakeRelay, PyDevice  # noqa: E402

# The same cases the JS side (dashboard/public/agentname.js) must pass: (input, normalised | None if refused).
NAME_CASES = [
    ("助理一号", "助理一号"), ("Wren", "Wren"), ("市场部 Agent", "市场部 Agent"), ("  Wren  ", "Wren"),
    ("市场部    Agent", "市场部 Agent"), ("\t Wren \n", "Wren"), ("\u3000Wren\u3000", "Wren"), ("\ufeffWren", None),
    ("\x85Wren\x85", "Wren"), ("\x1cWren", None), ("\u2028Wren\u2029", "Wren"), ("\u00a0 Wren \u202f", "Wren"),
    ("a" * 32, "a" * 32), ("名" * 32, "名" * 32), ("😀" * 32, "😀" * 32), ("Ｗｒｅｎ", "Ｗｒｅｎ"), ("A  B  C", "A B C"),
    ("", None), ("   ", None), ("a" * 33, None), ("名" * 33, None), ("😀" * 33, None),
    ("Wr\u200ben", None), ("Wr\u200den", None), ("bad\u202ename", None), ("bad\u2066x", None), ("a\u00adb", None),
    ("a\nb", None), ("a\rb", None), ("a\tb", None), ("a\x00b", None), ("a\x1b[2Jb", None), ("a\x7fb", None), ("a\x85b", None),
    ("a\u2028b", None), ("a\u2029b", None), ("a\u00a0b", None), ("a\u3000b", None), ("a\u2003b", None), ("a\ud800b", None),
]


PASS = "test-passphrase-L2"


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    return st


def _link(st, url):
    cloud.write_cloud(st, {"api": url, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                           "linked_at": int(time.time()), "last_seq": 0})


def _non_loopback_ipv4() -> str | None:
    cands = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # TEST-NET-1: no packet is sent; the kernel just picks the source address
        cands.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=5).stdout
        cands += [ln.split()[3].split("/")[0] for ln in out.splitlines() if len(ln.split()) > 3]
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError):
        pass
    return next((ip for ip in cands if ip and not ip.startswith("127.") and ip != "0.0.0.0"), None)


# ------------------------------------------------------------------ §1 name rules
class NameRules(unittest.TestCase):
    def test_mirror_cases(self):
        for raw, want in NAME_CASES:
            with self.subTest(raw=raw):
                prob = text.agent_name_problem(raw)
                if want is None:
                    self.assertIsNotNone(prob)
                else:
                    self.assertIsNone(prob)
                    self.assertEqual(text.normalise_agent_name(raw), want)
        self.assertEqual(text.agent_name_problem(""), "name_required")
        self.assertEqual(text.agent_name_problem("  "), "name_required")
        self.assertEqual(text.agent_name_problem("a" * 33), "bad_name")
        self.assertEqual(text.agent_name_problem(None), "name_required")
        self.assertEqual(text.agent_name_problem(5), "bad_name")
        self.assertEqual(text.normalise_agent_name("Wr\u200ben"), "Wr\u200ben", "refused, never silently cleaned")

    def test_same_results_as_agentname_js(self):
        """The one JS copy (dashboard/public/agentname.js, owned by the dashboard) and this mirror agree on the cases above,
        odd inputs, and every BMP code point alone, inside a name, and at both ends (plus a few astral ones)."""
        js = HOST.parent / "dashboard" / "public" / "agentname.js"
        node = shutil.which("node")
        if not node or not js.exists():
            self.skipTest("node or dashboard/public/agentname.js not available")
        cases = [c for c, _ in NAME_CASES] + ["a" * 200, " " * 600, "x" * 513, chr(0xFB03) * 32, chr(0x01C5) * 32,
                                              chr(0x1D400) * 32, chr(0x0130) * 32, chr(0x2160) * 32, chr(0x337F) * 32,
                                              None, 5, ["Wren"]]
        for cp in list(range(0x10000)) + [0x1F600, 0xE0001, 0xE0020, 0xF0000, 0x10FFFF, 0x1D173, 0x110BD, 0x13430]:
            ch = chr(cp)
            cases += [ch, "a" + ch + "b", ch + "Wren" + ch]
        script = ("import { normaliseAgentName, agentNameProblem, agentNameKey, isCleanAgentName } from " + json.dumps(js.as_uri())
                  + "; let d = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', (c) => { d += c; }).on('end', () => {"
                  " const cs = JSON.parse(d); process.stdout.write(JSON.stringify(cs.map((c) => [normaliseAgentName(c),"
                  " agentNameProblem(c), typeof c === 'string' ? agentNameKey(c) : '', isCleanAgentName(c),"
                  " typeof c === 'string' && /[\\p{Co}\\p{Cn}]/u.test(c)]))); });")
        r = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(cases), capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(len(got), len(cases))
        import unicodedata
        mismatches, newer, js_pending = [], 0, []
        for c, row in zip(cases, got):
            want, js_co_cn = row[:4], row[4]
            py = [text.normalise_agent_name(c), text.agent_name_problem(c), text.agent_name_key(c) if isinstance(c, str) else "",
                  text.is_clean_agent_name(c)]
            if py == want:
                continue
            py_cn = isinstance(c, str) and any(unicodedata.category(ch) == "Cn" for ch in c)
            # Characters assigned after this Python's Unicode version (3.13: 15.1; Node 26: 17): the uniqueness key may
            # differ (the key is the Dashboard's alone), and the host — seeing them as Cn — refuses names JS accepts.
            # Both are tolerated only in that direction: the host may be stricter, never looser.
            if py_cn and not js_co_cn and (py[0::3] + [py[1]] == want[0::3] + [want[1]]
                                           or (py[1] == "bad_name" and want[1] is None and py[0] == want[0])):
                newer += 1
                continue
            if js_co_cn and py[1] == "bad_name" and want[1] is None:
                js_pending.append(ascii(c))   # review A32-10: agentname.js has not refused Co / Cn yet
                continue
            mismatches.append((ascii(c), py, want))
        self.assertEqual(mismatches[:10], [], f"{len(mismatches)} mismatches of {len(cases)}")
        self.assertLess(newer, 400, "only post-15.1 characters may differ, and only with the host stricter")
        self.assertEqual(js_pending[:5], [], f"{len(js_pending)} cases: agentname.js still accepts Co / Cn (A32-10) — "
                                             "re-run once the dashboard side refuses them")

    def test_machine_name(self):
        m = text.machine_name()
        self.assertTrue(m is None or (0 < len(m) <= 64 and "\n" not in m))
        import unittest.mock as um
        with um.patch("socket.gethostname", return_value="evil\x1b[2J\nhost\u202e" + "x" * 100):
            m = text.machine_name()
        self.assertLessEqual(len(m), 64)
        self.assertNotIn("\x1b", m)
        self.assertNotIn("\n", m)
        with um.patch("socket.gethostname", return_value=""):
            self.assertIsNone(text.machine_name())

    def test_state_stores_only_valid_names(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            self.assertIsNone(st.agent_name())
            self.assertEqual(st.set_agent_name("  市场部   Agent "), "市场部 Agent")
            self.assertEqual(State(st.root).agent_name(), "市场部 Agent")
            with self.assertRaises(ValueError):
                st.set_agent_name("bad\u202ename")
            self.assertEqual(st.agent_name(), "市场部 Agent")
            cfg = st.config()
            cfg["agent_name"] = "hand\u200bedited"
            st.write_private(st.config_path, json.dumps(cfg).encode())
            self.assertIsNone(st.agent_name(), "a hand-edited invalid name is treated as unset")
            self.assertEqual(os.stat(st.config_path).st_mode & 0o777, 0o600)
            st.check_perms()
            self.assertEqual(os.stat(st.config_lock_path).st_mode & 0o777, 0o600)

    def test_hostname_cleaner(self):
        self.assertEqual(text.clean_hostname("ip-172-31-4-12"), "ip-172-31-4-12", "no digit cap for hostnames")
        self.assertEqual(text.clean_hostname("  a\x1b[2J\nb\u202ec\ue000d  "), "a [2J b c d")
        self.assertEqual(len(text.clean_hostname("h" * 300)), 64)
        self.assertIsNone(text.clean_hostname("\x00\u200b"))
        self.assertIsNone(text.clean_hostname(None))
        self.assertIsNone(text.agent_name_problem("ok"))
        for co_cn in ("a\ue000b", "a\U000f0000b", "a\u0378b"):
            self.assertEqual(text.agent_name_problem(co_cn), "bad_name", ascii(co_cn))

    def test_concurrent_offline_revokes_all_stick(self):
        """Review A32-02: unlocked read-modify-writes let one revoke resurrect another's device."""
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            for _ in range(10):
                ids = [st.add_device(os.urandom(32), f"d{i}") for i in range(5)]
                barrier = threading.Barrier(5)

                def rm(did):
                    barrier.wait()
                    st.remove_device(did)
                ts = [threading.Thread(target=rm, args=(i,)) for i in ids]
                [t.start() for t in ts]
                [t.join() for t in ts]
                self.assertEqual(st.devices(), {}, "every revoke stays revoked")
            # and across processes
            ids = [st.add_device(os.urandom(32), f"p{i}") for i in range(5)]
            code = ("import sys, pathlib; sys.path.insert(0, %r); from agentj.state import State; "
                    "State(pathlib.Path(%r)).remove_device(sys.argv[1])") % (str(HOST), str(st.root))
            ps = [subprocess.Popen([sys.executable, "-c", code, i]) for i in ids]
            self.assertEqual([p.wait(30) for p in ps], [0] * 5)
            self.assertEqual(st.devices(), {})
            self.assertEqual(os.stat(st.devices_lock_path).st_mode & 0o777, 0o600)
            st.check_perms()

    def test_concurrent_config_writers_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            errs = []

            def names_():
                for i in range(40):
                    st.set_agent_name(f"Agent {i}")

            def switch():
                for i in range(40):
                    st.set_remote_unbind(i % 2 == 0)
            ts = [threading.Thread(target=names_), threading.Thread(target=switch)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            cfg = st.config()
            self.assertEqual(cfg["agent_name"], "Agent 39")
            self.assertIs(cfg["remote_unbind"], False)
            self.assertIn("channel", cfg)
            self.assertEqual(errs, [])


# ------------------------------------------------------------------ §3 control plane
class ControlPlane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_poll_name_whitelist(self):
        base = {"status": "bound", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"}}
        self.assertEqual(cloud.parse_poll({**base, "agent_name": "  Wren "})["agent_name"], "Wren")
        for bad in ("bad\u202ename", "", "a" * 40, 7, ["Wren"], None):
            self.assertIsNone(cloud.parse_poll({**base, "agent_name": bad})["agent_name"], bad)

    def test_sync_name_parse(self):
        self.assertEqual(cloud.parse_sync_name({"unbind": []}), ("none", None))
        self.assertEqual(cloud.parse_sync_name({"unbind": [], "agent_name": None}), ("none", None))
        self.assertEqual(cloud.parse_sync_name({"unbind": [], "agent_name": "市场部 Agent"}), ("ok", "市场部 Agent"))
        self.assertEqual(cloud.parse_sync_name({"unbind": [], "agent_name": "x\u2028y"}), ("bad", None))
        self.assertEqual(cloud.parse_sync_name({"unbind": [], "agent_name": 5}), ("bad", None))

    def test_report_hostname_switch(self):
        self.assertTrue(self.st.report_machine())
        self.st.set_report_machine(False)
        self.assertIsNone(cloud.build_report(self.st, set(), {}, seq=1, ts=1790000000)["machine"])
        self.st.set_report_machine(True)
        self.assertEqual(cloud.build_report(self.st, set(), {}, seq=1, ts=1790000000)["machine"], text.machine_name())

    def test_report_carries_agent_name_and_machine(self):
        inner = cloud.build_report(self.st, set(), {}, seq=1, ts=1790000000)
        self.assertIsNone(inner["agent_name"])
        self.assertEqual(inner["machine"], text.machine_name())
        self.st.set_agent_name("Wren")
        with FakeCP() as cp:
            _link(self.st, cp.url)
            self.assertEqual(cloud.send_report(self.st, set(), {}).kind, "ok")
            self.assertEqual(cp.rejected, [])
            rep = cp.reports[-1]
            self.assertEqual(rep["agent_name"], "Wren")
            self.assertEqual(rep["machine"], text.machine_name())
            self.assertEqual(rep["agent"], "agentj/0.11.0a1")

    def test_rename_signed_and_answers_whitelisted(self):
        with FakeCP() as cp:
            _link(self.st, cp.url)
            r = cloud.rename(self.st, "  市场部  Agent ")
            self.assertEqual(r.kind, "ok")
            self.assertEqual(r.name, "市场部 Agent")
            self.assertEqual(cp.renames[-1]["name"], "市场部 Agent")
            self.assertEqual(set(cp.renames[-1]), {"v", "t", "channel", "ts", "name"})
            self.assertEqual(cp.rejected, [])
            r = cloud.rename(self.st, "WREN")
            self.assertEqual(r.kind, "name_taken")
            self.assertEqual(r.suggestions, ("WREN 2", "WREN 3", "WREN 4"), "invalid / non-string suggestions dropped, ≤ 3")
            for step, kind in (("unbound", "not_bound"), ("rate", "rate_limited"), ("bad", "bad_name"), (500, "unreachable")):
                cp.rename_script = [step]
                self.assertEqual(cloud.rename(self.st, "Nova").kind, kind, step)
            cp.rename_script = [("echo", "bad\u202e")]
            self.assertEqual(cloud.rename(self.st, "Nova 2").name, "Nova 2", "an unusable echo falls back to ours")
            n = len(cp.renames)
            self.assertEqual(cloud.rename(self.st, "bad\u200bname").kind, "bad_name")
            self.assertEqual(len(cp.renames), n, "a bad name is refused before anything is sent")
        self.assertEqual(cloud.rename(self.st, "Nova").kind, "unreachable", "control plane gone")
        self.assertEqual(cloud.CTX_RENAME, "agentjarvis-host-rename-v1")

    def _sync_host(self, cp):
        _link(self.st, cp.url)
        host = serve.Host(self.st, events="jsonl", read_stdin=False)
        triggers = []
        host.reporter.trigger = triggers.append
        return host, triggers

    def _run_syncs(self, host, cp, n):
        import asyncio
        old = serve.SYNC_EVERY
        serve.SYNC_EVERY = 0.2

        async def go():
            t = asyncio.create_task(host.sync_loop())
            for _ in range(200):
                await asyncio.sleep(0.05)
                if len(cp.syncs) >= n:
                    break
            host.stopping.set()
            await asyncio.wait_for(t, 15)
        import contextlib
        import io
        try:
            with contextlib.redirect_stdout(io.StringIO()):   # serve's jsonl event lines
                asyncio.run(go())
        finally:
            serve.SYNC_EVERY = old

    def test_sync_adopts_a_valid_name(self):
        self.st.set_agent_name("旧名")
        before = self.st.devices_path.read_bytes()
        with FakeCP() as cp:
            cp.sync_name = "市场部 Agent"
            host, triggers = self._sync_host(cp)
            self._run_syncs(host, cp, 3)
        self.assertEqual(self.st.agent_name(), "市场部 Agent")
        self.assertEqual(triggers, ["agent_name"], "one report = the acknowledgement; no repeat once equal")
        evs = [json.loads(x)["ev"] for x in self.st.log_path.read_text().splitlines()]
        self.assertEqual(evs.count("agent_name_synced"), 1)
        self.assertEqual(self.st.devices_path.read_bytes(), before)

    def test_sync_refuses_a_bad_name_and_keeps_the_local_one(self):
        self.st.set_agent_name("Wren")
        with FakeCP() as cp:
            cp.sync_name = "Wr\u202een"
            host, triggers = self._sync_host(cp)
            self._run_syncs(host, cp, 3)
            self.assertGreaterEqual(len(cp.syncs), 3)
        self.assertEqual(self.st.agent_name(), "Wren")
        self.assertEqual(triggers, [])
        evs = [json.loads(x)["ev"] for x in self.st.log_path.read_text().splitlines()]
        self.assertEqual(evs.count("agent_name_refused"), 1, "logged once per refused streak, not per sync")

    def test_sync_null_name_changes_nothing(self):
        self.st.set_agent_name("Wren")
        with FakeCP() as cp:
            cp.sync_name = "NULL"
            host, triggers = self._sync_host(cp)
            self._run_syncs(host, cp, 2)
        self.assertEqual(self.st.agent_name(), "Wren")
        self.assertEqual(triggers, [])

    def test_cloud_py_still_never_writes_config_or_devices(self):
        import ast
        tree = ast.parse((HOST / "agentj" / "cloud.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("set_agent_name", "set_remote_unbind", "config_path", "add_device", "remove_device"))


# ------------------------------------------------------------------ CLI: agentj name / login / status
class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-a32-", dir="/tmp")
        self.env = {**os.environ, "AGENTJ_STATE_DIR": self.dir, "PYTHONPATH": str(HOST)}
        self.env.pop("AGENTJ_API_URL", None)
        self.st = State(pathlib.Path(self.dir))
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def agentj(self, *args, input="", env=None):
        return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env={**self.env, **(env or {})},
                              capture_output=True, text=True, timeout=60, input=input)

    def test_revoke_cli_refuses_when_serve_is_silent(self):
        did = self.st.add_device(os.urandom(32), "手机")
        lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lsock.bind(str(self.st.sock_path))
        lsock.listen(8)
        try:
            r = self.agentj("revoke", did)
        finally:
            lsock.close()
            self.st.sock_path.unlink()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("没有响应", r.stderr)
        self.assertIn(did, self.st.devices())
        r = self.agentj("revoke", did)          # no socket file = serve not running → direct, locked edit
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(did, self.st.devices())

    def test_report_hostname_command_and_login_notice(self):
        r = self.agentj("report-hostname")
        self.assertIn("agentj report-hostname off", r.stdout)
        r = self.agentj("report-hostname", "off")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.st.report_machine())
        with FakeCP(interval=4, bound_after=1) as cp:
            r = self.agentj("login", "--api", cp.url, "--yes", "--account", "acme-co", env={"AGENTJ_APP_URL": cp.url})
            self.assertIn("账号后台不显示这台电脑的名字", r.stdout)
            self.assertIsNone(cp.reports[-1]["machine"])
        self.agentj("unlink")
        self.agentj("report-hostname", "on")
        with FakeCP(interval=4, bound_after=1) as cp:
            r = self.agentj("login", "--api", cp.url, "--yes", "--account", "acme-co", env={"AGENTJ_APP_URL": cp.url})
            self.assertIn("账号后台会显示这台电脑的名字", r.stdout)
            self.assertIn("agentj report-hostname off", r.stdout)
            self.assertEqual(cp.reports[-1]["machine"], text.machine_name())

    def test_admin_url_file(self):
        path = os.path.join(self.dir, "admin-url.jsonl")
        p = subprocess.Popen([sys.executable, "-m", "agentj.cli", "admin", "--events", "jsonl", "--url-file", path],
                             cwd=HOST, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ev = json.loads(p.stdout.readline())
            self.assertEqual(set(ev), {"ev", "port", "url_file", "expires_in"}, "no secret on stdout")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            line = json.loads(pathlib.Path(path).read_text().splitlines()[0])
            self.assertRegex(line["url"], rf"^http://127\.0\.0\.1:{ev['port']}/#t=[A-Za-z0-9_-]{{43}}$")
            p.stdin.write("\n")
            p.stdin.flush()
            json.loads(p.stdout.readline())
            self.assertEqual(len(pathlib.Path(path).read_text().splitlines()), 2, "a fresh link is appended")
        finally:
            p.send_signal(signal.SIGTERM)
            p.wait(10)
            for f in (p.stdin, p.stdout, p.stderr):
                f.close()
        r = self.agentj("admin", "--url-file", path)
        self.assertNotEqual(r.returncode, 0, "an existing file is never reused")
        self.assertIn("--url-file", r.stderr)

    def test_name_unlinked_is_local_only(self):
        r = self.agentj("name")
        self.assertIn("还没起名", r.stdout)
        self.assertIn("助理一号", r.stdout)
        r = self.agentj("name", "  市场部   Agent ")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("「市场部 Agent」", r.stdout)
        self.assertEqual(self.st.agent_name(), "市场部 Agent")
        r = self.agentj("name", "bad\u202ename")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("不合规", r.stderr)
        self.assertEqual(self.st.agent_name(), "市场部 Agent")
        self.assertIn("市场部 Agent", self.agentj("status").stdout)
        self.assertIn("Agent：「市场部 Agent」", self.agentj("devices").stdout)
        self.assertNotIn("agent_name", json.loads(self.agentj("devices", "--json").stdout or "{}"))

    def test_name_linked_goes_to_the_dashboard_first(self):
        with FakeCP() as cp:
            _link(self.st, cp.url)
            r = self.agentj("name", "Nova")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(cp.renames[-1]["name"], "Nova")
            self.assertEqual(self.st.agent_name(), "Nova")
            self.assertIn("账号后台和这台电脑都改好了", r.stdout)
            self.assertTrue(any(rep.get("agent_name") == "Nova" for rep in cp.reports), "a report acknowledges the new name")
            r = self.agentj("name", "wren")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("已经有叫这个名字", r.stderr)
            self.assertIn("「wren 2」", r.stderr)
            self.assertEqual(self.st.agent_name(), "Nova", "409 → nothing written locally")
            cp.rename_script = ["unbound"]
            r = self.agentj("name", "Orion")
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.st.agent_name(), "Nova")
        r = self.agentj("name", "Orion")   # control plane gone
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("连不上账号后台，名字没改", r.stderr)
        self.assertEqual(self.st.agent_name(), "Nova")

    def test_login_confirm_shows_and_sets_the_agent_name(self):
        with FakeCP(interval=4, bound_after=1) as cp:
            cp.bound_name = "助理一号"
            r = self.agentj("login", "--api", cp.url, input="n\n", env={"AGENTJ_APP_URL": cp.url})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("加到 Agent J 账号 acme-co（Acme [2J Co），Agent 名「助理一号」？[y/N]", r.stdout)
            self.assertIsNone(self.st.agent_name(), "N writes nothing — not the name either")
            self.assertFalse(self.st.cloud_path.exists())
        with FakeCP(interval=4, bound_after=1) as cp:
            cp.bound_name = "助理一号"
            r = self.agentj("login", "--api", cp.url, input="y\n", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.st.agent_name(), "助理一号")
            self.assertEqual(cp.reports[-1]["agent_name"], "助理一号")
            self.assertIsNotNone(cp.reports[-1]["machine"])

    def test_login_with_a_bad_bound_name_keeps_the_old_prompt(self):
        with FakeCP(interval=4, bound_after=1) as cp:
            cp.bound_name = "bad\u202ename"
            r = self.agentj("login", "--api", cp.url, "--yes", "--account", "acme-co", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("加到 Agent J 账号 acme-co（Acme [2J Co）？[y/N] y（--yes --account）", r.stdout)
            self.assertIsNone(self.st.agent_name())

    def test_pair_without_serve_fails_fast_with_what_to_do(self):
        """S-10 (PROMPT-29): no running serve → one plain message (service install / status), before any prompt."""
        for args in (("pair",), ("pair", "--link"), ("pair", "--no-qr")):
            r = self.agentj(*args)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("agentj service install", r.stderr)
            self.assertIn("agentj service status", r.stderr)
            self.assertNotIn("批准口令", r.stdout + r.stderr, "no passphrase question before the serve check")
        lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)     # a socket that never answers = busy, not "not running"
        lsock.bind(str(self.st.sock_path))
        lsock.listen(8)
        try:
            r = self.agentj("pair", "--link")
            self.assertEqual(r.returncode, 1)
            self.assertIn("没有响应", r.stderr)
        finally:
            lsock.close()
            os.unlink(self.st.sock_path)

    def test_login_yes_needs_the_matching_account(self):
        """C-10 (PROMPT-29): --yes is hidden, only valid with --account, and a different account binds nothing."""
        r = self.agentj("login", "--help")
        self.assertNotIn("--yes", r.stdout)
        self.assertIn("--account", r.stdout)
        with FakeCP(interval=4, bound_after=1) as cp:
            r = self.agentj("login", "--api", cp.url, "--yes", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("--yes 只能和 --account", r.stderr)
            self.assertIn("--yes works only together with --account", r.stderr)
            self.assertEqual(cp.logins, {}, "refused before anything was sent")
            r = self.agentj("login", "--api", cp.url, "--yes", "--account", "Not_An_ID", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 2)
            self.assertEqual(cp.logins, {})
            r = self.agentj("login", "--seat-file", "/nonexistent", "--name", "x", "--yes", "--account", "acme-co",
                            env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 2, "--account is for the code login only")
        self.assertFalse(self.st.cloud_path.exists())
        with FakeCP(interval=4, bound_after=1) as cp:
            cp.bound_name = "助理一号"
            r = self.agentj("login", "--api", cp.url, "--yes", "--account", "other-co", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("acme-co", r.stderr)
            self.assertIn("other-co", r.stderr)
            self.assertIn("Nothing was written", r.stderr)
            self.assertNotIn("[y/N]", r.stdout, "no prompt for an account that is not the named one")
            self.assertFalse(self.st.cloud_path.exists(), "nothing bound")
            self.assertIsNone(self.st.agent_name())
            self.assertEqual(cp.reports, [])
        with FakeCP(interval=4, bound_after=1) as cp:      # --account without --yes: still checked, then the human's y/N
            r = self.agentj("login", "--api", cp.url, "--account", "other-co", input="y\n", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 2)
            self.assertFalse(self.st.cloud_path.exists())
        with FakeCP(interval=4, bound_after=1) as cp:
            r = self.agentj("login", "--api", cp.url, "--account", "ACME-CO", input="y\n", env={"AGENTJ_APP_URL": cp.url})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[y/N]", r.stdout)
            self.assertTrue(self.st.cloud_path.exists())


# ------------------------------------------------------------------ §4 the page: admission and request checks
class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.st.set_agent_name("Wren")
        self.srv = admin.AdminServer(self.st).start()
        self.port = self.srv.port

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, host=None, raw=None):
        c = http.client.HTTPConnection(host or "127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if body is not None and raw is None:
            raw = json.dumps(body).encode()
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def token(self, url=None) -> str:
        url = url or self.srv.new_link()
        self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/#t=[A-Za-z0-9_-]{43}$")
        return url.split("#t=", 1)[1]

    def redeem(self, token, **kw):
        return self.req("POST", "/api/session", {"token": token},
                        headers={"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json", **kw})

    def login(self, url=None) -> str:
        """→ the Authorization header value for a fresh session."""
        r, data = self.redeem(self.token(url))
        self.assertEqual(r.status, 200)
        self.assertIsNone(r.getheader("Set-Cookie"))
        sid = json.loads(data)["session"]
        self.assertRegex(sid, r"^[A-Za-z0-9_-]{43}$")
        return "Bearer " + sid

    def post(self, path, body, auth, **kw):
        h = {"Authorization": auth, "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json",
             **kw.pop("headers", {})}
        return self.req("POST", path, body, headers=h, **kw)

    def state_status(self, auth):
        return self.req("GET", "/api/state", headers={"Authorization": auth})[0].status

    def test_bound_to_loopback_only(self):
        self.assertEqual(self.srv.httpd.socket.getsockname(), ("127.0.0.1", self.port))
        self.assertEqual(admin.BIND, "127.0.0.1")
        if socket.has_ipv6:
            s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s6.settimeout(3)
            try:
                with self.assertRaises(OSError, msg="nothing listens on ::1"):
                    s6.connect(("::1", self.port))
            finally:
                s6.close()
        ip = _non_loopback_ipv4()
        if ip is None:
            self.skipTest("this machine has no non-loopback IPv4 address to try")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            with self.assertRaises(OSError, msg=f"the page must not answer on {ip}"):
                s.connect((ip, self.port))
        finally:
            s.close()

    def test_assets_are_public_api_is_a_bare_404_without_a_session(self):
        for p in ("/", "/app.js", "/app.css"):
            r, data = self.req("GET", p)
            self.assertEqual(r.status, 200, p)
            self.assertTrue(data)
            self.assertIsNone(r.getheader("Set-Cookie"))
            self.assertEqual(r.getheader("Content-Security-Policy"), admin.SECURITY_HEADERS[0][1])
            for k in ("X-Frame-Options", "Cache-Control", "Referrer-Policy", "X-Content-Type-Options"):
                self.assertTrue(r.getheader(k), k)
        self.assertNotIn(b"Wren", self.req("GET", "/")[1], "assets carry no data")
        for path in ("/index.html", "/x", "/?t=" + "A" * 43, "/app.js?x=1", "/api/state", "/api/state?t=x"):
            r, data = self.req("GET", path)
            self.assertEqual((r.status, data), (404, b""), path)
        token = self.token()
        r, _ = self.req("GET", "/?t=" + token)
        self.assertEqual(r.status, 404, "the old query-string flow is gone")
        good = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        for path in ("/api/pair/start", "/api/name", "/api/remote-unbind", "/api/session/end", "/api/devices/AAAAAAAAAAAAAAAA/revoke"):
            r, data = self.req("POST", path, {}, headers=good)
            self.assertEqual((r.status, data), (404, b""), path)
            r, data = self.req("POST", path, {}, headers={**good, "Authorization": "Bearer " + "A" * 43})
            self.assertEqual((r.status, data), (404, b""), path)
        for m in ("PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"):
            r, _ = self.req(m, "/api/state")
            self.assertEqual(r.status, 404, m)
        self.assertEqual(self.redeem(token)[0].status, 200, "none of the above consumed the token")

    def test_token_redeems_once_into_a_bearer_session(self):
        token = self.token()
        r, data = self.redeem(token)
        self.assertEqual(r.status, 200)
        self.assertIsNone(r.getheader("Set-Cookie"))
        auth = "Bearer " + json.loads(data)["session"]
        self.assertEqual(self.redeem(token)[0].status, 404, "single use")
        r, body = self.req("GET", "/api/state", headers={"Authorization": auth})
        self.assertEqual(r.status, 200)
        self.assertIsNone(r.getheader("Set-Cookie"))
        st = json.loads(body)
        self.assertEqual(st["agent_name"], "Wren")
        self.assertEqual(set(st), {"agent_name", "machine", "channel", "version", "serve", "dashboard", "remote_unbind", "limit",
                                   "devices", "pairing", "passphrase_set"})
        self.assertEqual((st["limit"], st["version"], st["serve"]["running"]), (5, "0.11.0a1", False))
        for bad in (auth.replace("Bearer ", "bearer "), auth + "x", "Basic " + auth[7:], auth[7:]):
            self.assertEqual(self.state_status(bad), 404, bad)
        r, _ = self.req("GET", "/api/state", headers={"Cookie": f"aj_admin_{self.port}={auth[7:]}"})
        self.assertEqual(r.status, 404, "a cookie is no credential")
        # two Authorization headers → refused
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("GET", "/api/state", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{self.port}")
        c.putheader("Authorization", auth)
        c.putheader("Authorization", auth)
        c.endheaders()
        self.assertEqual(c.getresponse().status, 404)
        c.close()
        # redeeming needs our Origin + JSON like any write
        tok = self.token()
        self.assertEqual(self.req("POST", "/api/session", {"token": tok}, headers={"Content-Type": "application/json"})[0].status, 403)
        self.assertEqual(self.redeem(tok, Origin="http://evil.example")[0].status, 403)
        r, _ = self.post("/api/session/end", {}, auth)
        self.assertEqual(r.status, 404, "a fresh link revoked the earlier session")

    def test_expired_and_superseded_tokens(self):
        t = self.token()
        self.srv.auth.token = (t, time.monotonic() - 1)
        self.assertEqual(self.redeem(t)[0].status, 404, "expired")
        old, new = self.token(), self.token()
        self.assertEqual(self.redeem(old)[0].status, 404, "a fresh link (Enter) supersedes the previous one")
        self.assertEqual(self.redeem(new)[0].status, 200)
        self.assertEqual(self.redeem("A" * 43)[0].status, 404)
        self.assertEqual(self.redeem(None)[0].status, 404)

    def test_session_limits_and_logout(self):
        url = self.srv.new_link()
        a = self.login(url)
        # a new link logs every open session out
        b = self.login()
        self.assertEqual(self.state_status(a), 404)
        self.assertEqual(self.state_status(b), 200)
        # at most 4 at once: plant one-time tokens directly (a printed link would log everyone out) and redeem 5
        sids = []
        for _ in range(5):
            t = secrets.token_urlsafe(32)
            self.srv.auth.token = (t, time.monotonic() + 60)
            sids.append(self.srv.auth.redeem(t))
        self.assertEqual(len(self.srv.auth.sessions), admin.MAX_SESSIONS)
        self.assertEqual(self.state_status(b), 404, "the oldest session was dropped")
        self.assertEqual(self.state_status("Bearer " + sids[-1]), 200)
        # idle and absolute expiry
        live = "Bearer " + sids[-1]
        with self.srv.auth.lock:
            self.srv.auth.sessions[sids[-1]][1] -= admin.SESSION_IDLE + 1
        self.assertEqual(self.state_status(live), 404, "idle 30 min")
        live = "Bearer " + sids[-2]
        with self.srv.auth.lock:
            self.srv.auth.sessions[sids[-2]][0] -= admin.SESSION_MAX_AGE + 1
        self.assertEqual(self.state_status(live), 404, "8 h absolute, however active")
        self.assertEqual((admin.SESSION_IDLE, admin.SESSION_MAX_AGE, admin.MAX_SESSIONS), (1800, 8 * 3600, 4))
        # logout
        c = self.login()
        self.assertEqual(self.post("/api/session/end", {}, c)[0].status, 200)
        self.assertEqual(self.state_status(c), 404)

    def test_host_header_must_be_ours(self):
        auth = self.login()
        for host in ("evil.example", f"evil.example:{self.port}", f"127.0.0.1:{self.port + 1}", "127.0.0.1", "",
                     f"localhost.evil.example:{self.port}", f"[::1]:{self.port}"):
            for path in ("/", "/api/state"):
                r, data = self.req("GET", path, headers={"Authorization": auth, "Host": host})
                self.assertEqual((r.status, data), (404, b""), (host, path))
        tok = self.token()
        auth = self.login()
        r, _ = self.redeem(self.token(), Host=f"rebind.example:{self.port}")
        self.assertEqual(r.status, 404, "even a valid token through a rebinding name")
        self.assertEqual(self.redeem(tok)[0].status, 404)
        auth = self.login()
        r, _ = self.req("GET", "/api/state", headers={"Authorization": auth, "Host": f"localhost:{self.port}"})
        self.assertEqual(r.status, 200)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)   # two Host headers
        c.putrequest("GET", "/api/state", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{self.port}")
        c.putheader("Host", "evil.example")
        c.putheader("Authorization", auth)
        c.endheaders()
        self.assertEqual(c.getresponse().status, 404)
        c.close()

    def _raw(self, data: bytes, timeout=10) -> bytes:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        try:
            s.sendall(data)
            out = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    return out
                out += chunk
        finally:
            s.close()

    def test_writes_need_our_origin_json_and_a_small_body(self):
        auth = self.login()
        base = {"Authorization": auth, "Content-Type": "application/json"}
        r, _ = self.req("POST", "/api/remote-unbind", {"on": False}, headers=base)
        self.assertEqual(r.status, 403, "no Origin")
        for o in ("http://evil.example", "null", f"http://127.0.0.1:{self.port + 1}", f"https://127.0.0.1:{self.port}"):
            r, _ = self.req("POST", "/api/remote-unbind", {"on": False}, headers={**base, "Origin": o})
            self.assertEqual(r.status, 403, o)
        for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", ""):
            r, _ = self.req("POST", "/api/remote-unbind", raw=b'{"on":false}',
                            headers={"Authorization": auth, "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": ct})
            self.assertEqual(r.status, 403, ct)
        r, _ = self.post("/api/name", None, auth, raw=b'{"name":"' + b"x" * 5000 + b'"}')
        self.assertEqual(r.status, 413)
        r, data = self.post("/api/name", None, auth, raw=b"not json")
        self.assertEqual(r.status, 400)
        r, data = self.post("/api/name", None, auth, raw=b"[1]")
        self.assertEqual(r.status, 400)
        head = (f"POST /api/remote-unbind HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: {auth}\r\n"
                f"Origin: http://127.0.0.1:{self.port}\r\nContent-Type: application/json\r\n").encode()
        self.assertTrue(self._raw(head + b"Transfer-Encoding: chunked\r\n\r\nc\r\n{\"on\":false}\r\n0\r\n\r\n").startswith(b"HTTP/1.0 411"))
        self.assertTrue(self._raw(head + b"Content-Length: -5\r\n\r\n").startswith(b"HTTP/1.0 413"))
        self.assertTrue(self._raw(head + b"\r\n").startswith(b"HTTP/1.0 411"), "no length")
        self.assertTrue(self._raw(head + b"Content-Length: 12\r\nContent-Length: 12\r\n\r\n{\"on\":false}").startswith(b"HTTP/1.0 411"))
        self.assertTrue(self.st.remote_unbind(), "none of the refused writes changed anything")
        r, data = self.post("/api/remote-unbind", {"on": False}, auth, headers={"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(r.status, 200)
        self.assertFalse(self.st.remote_unbind())
        r, _ = self.post("/api/remote-unbind", {"on": "yes"}, auth)
        self.assertEqual(r.status, 400)
        r, _ = self.post("/api/nope", {}, auth)
        self.assertEqual(r.status, 404)

    def test_slow_drip_is_cut_and_handlers_are_bounded(self):
        old = admin.REQUEST_DEADLINE
        try:
            # a fresh server with a short deadline (the class reads it per connection)
            admin.REQUEST_DEADLINE = 1.0
            srv = admin.AdminServer(self.st).start()
            try:
                s = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
                s.sendall(b"GET / HTTP/1.1\r\n")
                t0, cut = time.monotonic(), False
                try:
                    for _ in range(8):           # one header byte every 0.4 s: never idle long, never finished
                        time.sleep(0.4)
                        s.sendall(b"X")
                    s.settimeout(3)
                    cut = s.recv(100) == b""
                except OSError:
                    cut = True
                s.close()
                self.assertTrue(cut, "the server closed a request that dripped past the deadline")
                self.assertLess(time.monotonic() - t0, 6)
            finally:
                srv.stop()
        finally:
            admin.REQUEST_DEADLINE = old
        # 16 idle connections hold every slot; the 17th is closed at once; afterwards the page answers again
        idle = [socket.create_connection(("127.0.0.1", self.port), timeout=5) for _ in range(admin.MAX_HANDLERS)]
        try:
            time.sleep(0.3)
            extra = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            extra.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
            try:
                self.assertEqual(extra.recv(100), b"", "over the limit: closed without an answer")
            except ConnectionResetError:
                pass
            extra.close()
        finally:
            for s in idle:
                s.close()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if self.req("GET", "/")[0].status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        self.assertEqual(self.req("GET", "/")[0].status, 200)

    def test_revoke_refuses_when_serve_is_silent(self):
        """Review A32-02: a control socket that accepts but never answers is not "serve not running"."""
        auth = self.login()
        did = self.st.add_device(os.urandom(32), "手机")
        lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lsock.bind(str(self.st.sock_path))
        lsock.listen(8)
        old = names.CTL_TIMEOUT
        names.CTL_TIMEOUT = 0.5
        try:
            r, data = self.post(f"/api/devices/{did}/revoke", {}, auth)
            self.assertEqual((r.status, json.loads(data)["error"]), (503, "serve_busy"))
            self.assertIn(did, self.st.devices(), "no direct edit behind a running serve")
            with self.assertRaises(names.ServeBusy):
                names.ctl_call(self.st, {"cmd": "status"})
        finally:
            names.CTL_TIMEOUT = old
            lsock.close()
            self.st.sock_path.unlink()
        self.assertIsNone(names.ctl_call(self.st, {"cmd": "status"}), "no socket file = not running")

    def test_rename_and_local_revoke_without_serve(self):
        auth = self.login()
        did = self.st.add_device(os.urandom(32), "手机\x1b[2J")
        r, data = self.post("/api/name", {"name": "  内容制作部  Agent "}, auth)
        self.assertEqual((r.status, json.loads(data)), (200, {"ok": True, "name": "内容制作部 Agent"}))
        self.assertEqual(self.st.agent_name(), "内容制作部 Agent")
        r, data = self.post("/api/name", {"name": "a\u202eb"}, auth)
        self.assertEqual(r.status, 400)
        self.assertEqual(json.loads(data)["error"], "bad_name")
        r, data = self.post("/api/name", {"name": 5}, auth)
        self.assertEqual(r.status, 400)
        st = json.loads(self.req("GET", "/api/state", headers={"Authorization": auth})[1])
        self.assertEqual(st["devices"][0]["id"], did)
        self.assertNotIn("\x1b", st["devices"][0]["name"])
        r, data = self.post("/api/pair/start", {}, auth)
        self.assertEqual((r.status, json.loads(data)["error"]), (409, "serve_not_running"))
        r, _ = self.post(f"/api/devices/{did}/revoke", {}, auth)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.st.devices(), {})
        r, _ = self.post(f"/api/devices/{did}/revoke", {}, auth)
        self.assertEqual(r.status, 409)
        r, _ = self.post("/api/devices/../revoke", {}, auth)
        self.assertEqual(r.status, 404)

    def test_rename_linked_409_shows_suggestions(self):
        auth = self.login()
        with FakeCP() as cp:
            _link(self.st, cp.url)
            r, data = self.post("/api/name", {"name": "wren"}, auth)
            self.assertEqual(r.status, 409)
            d = json.loads(data)
            self.assertEqual((d["error"], d["suggestions"]), ("name_taken", ["wren 2", "wren 3", "wren 4"]))
            self.assertEqual(self.st.agent_name(), "Wren")
            r, data = self.post("/api/name", {"name": "Nova"}, auth)
            self.assertEqual(r.status, 200)
            self.assertEqual(self.st.agent_name(), "Nova")
        r, data = self.post("/api/name", {"name": "Orion"}, auth)
        self.assertEqual((r.status, json.loads(data)["error"]), (502, "unreachable"))
        self.assertEqual(self.st.agent_name(), "Nova")

    def test_page_assets_never_use_html_sinks(self):
        js = (admin.ASSET_DIR / "app.js").read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "document.cookie"):
            self.assertNotIn(sink, js)
        html = (admin.ASSET_DIR / "index.html").read_text()
        self.assertNotIn("style=", html)
        self.assertNotIn("<script>", html)
        for f in ("app.js", "app.css"):
            self.assertNotRegex((admin.ASSET_DIR / f).read_text(), r"https?://(?!127\.0\.0\.1)[a-z]", f"{f}: no third-party resources")
        # index.html: only plain footer navigations to the official site, never a resource from another origin
        self.assertNotRegex(html, r"(?:src|srcset|action|formaction)\s*=\s*\"?https?:", "no third-party resources")
        self.assertNotRegex(html, r"<link[^>]+href=\"https?:", "no third-party stylesheets / icons / preloads")
        self.assertEqual(sorted(set(re.findall(r'href="(https?://[^"]+)"', html))),
                         [f"https://agentj.app/{p}/" for p in ("contact", "docs", "privacy", "security")])
        zh = json.loads((admin.ASSET_DIR / "i18n/admin.zh.json").read_text())
        words = "\n".join(zh.values())
        for needle in ("建议一个人用一个 Agent", "一个 Agent 就是一个计费席位，只能运行在一台电脑或服务器上。它的名字只是个标签，就像给宠物起名，叫什么都行。",
                       "127.0.0.1", "agentj pair", "看着手机，输入手机上的 6 位码", "显示链接", "从账号后台解绑", "给这个 Agent 起个名字",
                       "6 位码只显示在手机上，这个页面永远不会显示它", "正在添加手机遥控器", "只在这台电脑上能打开"):
            self.assertIn(needle, words, needle)
        for needle in ("history.replaceState", "sessionStorage", "Authorization", "/api/session/end", "review A32-04"):
            self.assertIn(needle, js, needle)


# ------------------------------------------------------------------ §4 the page against a real serve: approval = the code
class PagePairing(unittest.TestCase):
    """`agentj serve` (subprocess) on a test relay, `agentj admin --events jsonl` (subprocess), a Python device."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-a32p-", dir="/tmp")
        self.relay = FakeRelay().__enter__()
        self.env = {**os.environ, "AGENTJ_STATE_DIR": self.dir, "PYTHONPATH": str(HOST),
                    "AGENTJ_TEST_REPORT_DEBOUNCE": "0.1"}
        self.env.pop("AGENTJ_API_URL", None)
        self.st = State(pathlib.Path(self.dir))
        self.st.init(relay=self.relay.url, web="https://m.agentj.app")
        gate.set_passphrase(self.st, PASS)   # L2: the human's approval passphrase
        self.procs, self.devices = [], []
        self.serve = self.spawn("serve", "--events", "jsonl", "--no-stdin", stdin=subprocess.DEVNULL)
        self.wait(lambda: (names.ctl_call(self.st, {"cmd": "status"}) or {}).get("relay_up"), "relay up")
        self.adm = self.spawn("admin", "--events", "jsonl", stdin=subprocess.PIPE)
        ev = json.loads(self.adm.stdout.readline())
        self.assertEqual(ev["ev"], "admin")
        self.port, url = ev["port"], ev["url"]
        self.assertRegex(url, rf"^http://127\.0\.0\.1:{self.port}/#t=[A-Za-z0-9_-]{{43}}$")
        self.adm.stdin.write("\n")          # Enter prints a fresh link; the first one is superseded
        self.adm.stdin.flush()
        ev2 = json.loads(self.adm.stdout.readline())
        self.assertNotEqual(ev2["url"], url)
        self.assertEqual(self.redeem(url.split("#t=")[1])[0], 404, "superseded")
        s, data = self.redeem(ev2["url"].split("#t=")[1])
        self.assertEqual(s, 200)
        self.auth = "Bearer " + json.loads(data)["session"]

    def tearDown(self):
        for d in self.devices:
            d.close()
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            for f in (p.stdin, p.stdout, p.stderr):
                if f:
                    f.close()
        self.relay.__exit__()
        shutil.rmtree(self.dir, ignore_errors=True)

    def spawn(self, *args, stdin):
        p = subprocess.Popen([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env=self.env, stdin=stdin,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def wait(self, pred, what, secs=20):
        deadline = time.time() + secs
        while time.time() < deadline:
            v = pred()
            if v:
                return v
            time.sleep(0.05)
        self.fail(f"timeout: {what}")

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}", **({"Authorization": self.auth} if hasattr(self, "auth") else {})})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body, r.getheader("Set-Cookie") or ""

    def redeem(self, token):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("POST", "/api/session", body=json.dumps({"token": token}), headers={
            "Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("POST", path, body=json.dumps(body), headers={"Host": f"127.0.0.1:{self.port}", "Authorization": self.auth,
                                                                "Origin": f"http://127.0.0.1:{self.port}",
                                                                "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def state(self):
        s, body, _ = self.get("/api/state")
        self.assertEqual(s, 200)
        return json.loads(body)

    def start_and_pair(self, name="测试手机"):
        s, d = self.post("/api/pair/start", {})
        self.assertEqual(s, 200, d)
        p = d["pairing"]
        self.assertEqual(p["phase"], "waiting")
        self.assertTrue(p["qr_svg"].startswith("data:image/svg+xml"))
        self.assertIn("#p=", p["link"])
        dev = PyDevice(name)
        self.devices.append(dev)
        code = dev.pair(p["link"])
        st = self.wait(lambda: (lambda x: x if x["pairing"] and x["pairing"]["phase"] in ("pending", "full") else None)(self.state()),
                       "pending")
        return dev, code, st

    def wrong(self, code):
        return f"{(int(code) + 1) % 1_000_000:06d}"

    def test_right_code_approves_wrong_code_denies(self):
        dev, code, st = self.start_and_pair("Leo 的手机\x1b[2J 123456")
        p = st["pairing"]
        self.assertEqual(p["phase"], "pending")
        self.assertEqual(p["device"]["id"], dev.id)
        self.assertNotIn("\x1b", p["device"]["name"])
        self.assertNotIn("123456", p["device"]["name"].replace(" ", ""), "a device label cannot show a 6-digit code")
        self.assertGreater(p["deadline_in"], 0)
        self.assertNotIn(code, json.dumps(st), "the page never knows the code")
        s, d = self.post("/api/pair/code", {"code": self.wrong(code), "passphrase": PASS})
        self.assertEqual((s, d["pairing"]["phase"], d["pairing"]["reason"]), (200, "denied", "code_mismatch"))
        self.assertTrue(dev.closed(), "denied device is closed, never sent an app message")
        self.assertEqual(self.st.devices(), {})
        s, d = self.post("/api/pair/code", {"code": code, "passphrase": PASS})
        self.assertEqual((s, d["error"]), (409, "no_pending_device"), "one try: the pairing is over")

        dev2, code2, _ = self.start_and_pair("平板")
        s, d = self.post("/api/pair/code", {"code": code2, "passphrase": PASS})
        self.assertEqual((s, d["pairing"]["phase"]), (200, "approved"))
        self.assertEqual(dev2.app(), {"t": "approved"})
        self.assertIn(dev2.id, self.st.devices())
        st = self.state()
        self.assertEqual([x["id"] for x in st["devices"]], [dev2.id])
        self.assertTrue(self.wait(lambda: self.state()["devices"][0]["online"], "online"))
        log = self.st.log_path.read_text()
        self.assertIn('"code_ok": true', log)
        self.assertNotIn(code2, log)

    def test_deny_and_cancel_and_no_other_endpoint_approves(self):
        dev, code, _ = self.start_and_pair()
        # everything except /api/pair/code with the right code — none of it may approve
        attempts = [("/api/pair/code", {}), ("/api/pair/code", {"code": int(code)}), ("/api/pair/code", {"code": [code]}),
                    ("/api/pair/unbind", {"device": dev.id}), ("/api/remote-unbind", {"on": True}),
                    ("/api/name", {"name": code}), ("/api/pair/approve", {"device": dev.id}), ("/api/approve", {"code": code}),
                    (f"/api/devices/{dev.id}/approve", {})]
        for path, body in attempts:
            self.post(path, body)
            self.assertNotIn(dev.id, self.st.devices(), path)
        self.assertEqual(self.state()["pairing"]["phase"], "pending", "still waiting for the human's code")
        s, d = self.post("/api/pair/code", {"code": ""})
        self.assertEqual((d["pairing"]["phase"], d["pairing"]["reason"]), ("denied", "denied"), "empty = deny")
        self.assertTrue(dev.closed())
        # revoking a device that is still waiting ends its pairing (serve detaches every session of that id) — never approves
        devr, coder, _ = self.start_and_pair()
        s, d = self.post(f"/api/devices/{devr.id}/revoke", {})
        self.assertEqual(self.wait(lambda: self.state()["pairing"]["phase"] != "pending" and self.state()["pairing"], "ended")["phase"],
                         "denied")
        s, d = self.post("/api/pair/code", {"code": coder, "passphrase": PASS})
        self.assertEqual(s, 409)
        self.assertNotIn(devr.id, self.st.devices())
        dev2, _, _ = self.start_and_pair()
        s, _ = self.post("/api/pair/cancel", {})
        self.assertEqual(s, 200)
        self.assertIsNone(self.state()["pairing"])
        self.assertTrue(dev2.closed(), "cancel = abandoned: serve denies and closes the waiting device")
        self.assertEqual(self.st.devices(), {})

    def test_full_list_unbind_then_approve(self):
        old = [self.st.add_device(os.urandom(32), f"旧遥控器{i}") for i in range(5)]
        dev, code, st = self.start_and_pair("第六台")
        p = st["pairing"]
        self.assertEqual(p["phase"], "full")
        self.assertEqual(len(st["devices"]), 5)
        s, d = self.post("/api/pair/code", {"code": code, "passphrase": PASS})
        self.assertEqual((s, d["error"]), (409, "unbind_first"), "the right code cannot approve while full")
        s, d = self.post("/api/pair/unbind", {"device": dev.id})
        self.assertEqual(s, 409, "the waiting device itself is not an unbind target")
        s, d = self.post("/api/pair/unbind", {"device": "NOPENOPENOPENOPE"})
        self.assertEqual(s, 409)
        s, d = self.post("/api/pair/unbind", {"device": old[2]})
        self.assertEqual(s, 200, d)
        self.assertTrue(d["ok"])
        self.assertEqual(d["pairing"]["phase"], "pending")
        self.assertNotIn(old[2], self.st.devices())
        s, d = self.post("/api/pair/code", {"code": code, "passphrase": PASS})
        self.assertEqual(d["pairing"]["phase"], "approved")
        self.assertEqual(dev.app(), {"t": "approved"})
        self.assertEqual(len(self.st.devices()), 5)
        self.assertIn(dev.id, self.st.devices())


if __name__ == "__main__":
    unittest.main()
