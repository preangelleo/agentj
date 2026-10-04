"""PROTOCOL.md §7 end to end against a fake control plane on 127.0.0.1: the real `agentj login` (subprocess) to bound,
`agentj report` with and without a running serve, 409 replay self-healing, 403 not_bound, and a real `agentj serve`
that keeps running (and keeps answering its control socket) while the control plane fails or hangs."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import cloud, wire  # noqa: E402
from agentj.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402


class Flow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-cf-", dir="/tmp")
        self.env = {**os.environ, "AGENTJ_STATE_DIR": self.dir, "PYTHONPATH": str(HOST)}
        self.env.pop("AGENTJ_API_URL", None)
        self.st = State(pathlib.Path(self.dir))
        self.st.init(relay="ws://127.0.0.1:1")  # a relay that refuses: serve keeps retrying it, never crashes
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            if p.stderr:
                p.stderr.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def agentj(self, *args, env=None, timeout=60, input=""):
        """stdin is the human at this terminal: empty (EOF) unless a test types something."""
        return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env={**self.env, **(env or {})},
                              capture_output=True, text=True, timeout=timeout, input=input)

    def log(self):
        return [json.loads(line) for line in self.st.log_path.read_text().splitlines()]

    def link(self, cp):
        cloud.write_cloud(self.st, {"api": cp.url, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": int(time.time()), "last_seq": 0})

    def test_login_to_bound_then_report(self):
        did = self.st.add_device(os.urandom(32), "Leo 的手机")
        before = self.st.devices_path.read_bytes()
        with FakeCP(interval=4, bound_after=2) as cp:
            r = self.agentj("login", "--api", cp.url, env={"AGENTJ_APP_URL": cp.url}, input="y\n")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f"这台电脑的通道号：{self.st.config()['channel']}（账号后台里显示的应该一样）", r.stdout)
            self.assertIn("BCDF-2345", r.stdout)
            self.assertIn(cp.url + "/link", r.stdout, "the server's URI is shown: it is on the configured Dashboard origin")
            self.assertIn("加到 Agent J 账号 acme-co（Acme [2J Co）？[y/N]", r.stdout)
            self.assertIn("acme-co", r.stdout)
            self.assertNotIn("\x1b", r.stdout)
            gaps = [b - a for a, b in zip(cp.poll_times, cp.poll_times[1:])]
            self.assertEqual(len(cp.poll_times), 2)
            self.assertTrue(all(g >= 4 for g in gaps), gaps)
            self.assertEqual(cp.rejected, [])
            self.assertEqual(self.st.devices_path.read_bytes(), before, "a malicious bound answer changes nothing")
            link = json.loads(self.st.cloud_path.read_text())
            self.assertEqual(set(link), {"api", "host_id", "tenant", "linked_at", "last_seq", "via"})
            self.assertEqual(link["via"], "code", "the 8-character code path records how it was linked")
            self.assertEqual(os.stat(self.st.cloud_path).st_mode & 0o777, 0o600)
            self.assertEqual(len(cp.reports), 1, "login sends a first report")

            again = self.agentj("login", "--api", cp.url, "--yes", "--account", "acme-co")
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("agentj unlink", again.stderr)

            r = self.agentj("report")  # serve not running: devices.json, everything offline
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = cp.reports[-1]
            self.assertEqual(rep["devices"], [{"id": did, "name": "Leo 的手机", "paired_at": rep["devices"][0]["paired_at"],
                                               "online": False}])
            self.assertEqual(rep["pending"], {"count": 0, "since": None})
            self.assertEqual(rep["agent"], "agentj/0.13.0a1")
            self.assertGreater(rep["seq"], cp.reports[0]["seq"])
            self.assertEqual(cp.rejected, [])
            self.assertIn("acme-co", self.agentj("devices").stdout)
            self.assertIn("acme-co", self.agentj("status").stdout)

            r = self.agentj("unlink")
            self.assertEqual(r.returncode, 0)
            self.assertFalse(self.st.cloud_path.exists())
            self.assertIn("Agent J 账号：还没加入", self.agentj("status").stdout)
            self.assertNotEqual(self.agentj("report").returncode, 0)
        text = self.st.log_path.read_text()
        self.assertNotIn("BCDF-2345", text)
        self.assertNotIn("http://", text)

    def test_login_declined_writes_nothing(self):
        """F5: the human answers N (or just Enter / EOF) → no cloud.json, no report, told to unbind in the Dashboard."""
        before = self.st.devices_path.read_bytes()
        for answer in ("n\n", "", "\n", "yes please\n"):
            with FakeCP(interval=4, bound_after=1) as cp:
                r = self.agentj("login", "--api", cp.url, input=answer)
                self.assertNotEqual(r.returncode, 0, answer)
                self.assertIn("加到 Agent J 账号 acme-co", r.stdout)
                self.assertIn("这台电脑什么都没写", r.stderr)
                self.assertIn("把这台电脑移除", r.stderr)
                self.assertFalse(self.st.cloud_path.exists(), answer)
                self.assertEqual(cp.reports, [], "nothing reported")
        self.assertEqual(self.st.devices_path.read_bytes(), before)
        self.assertNotIn("cloud_linked", self.st.log_path.read_text())

    def test_login_yes_flag_and_foreign_verification_uri(self):
        """--yes (with --account) for scripts; F7: a verification_uri off the configured Dashboard origin is not printed."""
        with FakeCP(interval=4, bound_after=1) as cp:
            r = self.agentj("login", "--api", cp.url, "--yes", "--account", "acme-co", env={"AGENTJ_APP_URL": "https://agentj.app/account"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("在已登录的账号后台里打开 https://agentj.app/account/\n", r.stdout)
            self.assertNotIn(cp.url, r.stdout)
            self.assertIn("y（--yes --account）", r.stdout)
            self.assertTrue(self.st.cloud_path.exists())

    def test_already_bound_login(self):
        with FakeCP() as cp:
            cp.login_mode = "already_bound"
            r = self.agentj("login", env={"AGENTJ_API_URL": cp.url})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("先在账号后台把它移除", r.stderr)
            self.assertFalse(self.st.cloud_path.exists())

    def test_login_refuses_plain_http_to_a_remote_host(self):
        r = self.agentj("login", "--api", "http://example.com")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("https://", r.stderr)

    def test_replay_is_retried_with_a_bigger_seq(self):
        with FakeCP() as cp:
            self.link(cp)
            cp.script = ["replay"]
            res = cloud.send_report(self.st, set(), {})
            self.assertEqual(res.kind, "ok")
            self.assertEqual(len(cp.reports), 2)
            self.assertGreater(cp.reports[1]["seq"], cp.reports[0]["seq"])
            self.assertEqual(cloud.read_cloud(self.st)["last_seq"], cp.reports[1]["seq"])

    def test_not_bound(self):
        with FakeCP() as cp:
            self.link(cp)
            cp.script = ["unbound"]
            r = self.agentj("report")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("已经把这台电脑移除了", r.stderr)
            self.assertTrue(any(e["ev"] == "report_unbound" for e in self.log()))
            self.assertTrue(self.st.cloud_path.exists())

    def _serve(self):
        env = {**self.env, "AGENTJ_TEST_REPORT_DEBOUNCE": "0.1", "AGENTJ_TEST_HEARTBEAT": "1"}
        p = subprocess.Popen([sys.executable, "-m", "agentj.cli", "serve", "--events", "jsonl", "--no-stdin"], cwd=HOST,
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.procs.append(p)
        deadline = time.time() + 15
        while not self.st.sock_path.exists():
            self.assertIsNone(p.poll(), "serve died")
            self.assertLess(time.time(), deadline)
            time.sleep(0.05)
        return p

    def _wait(self, pred, secs=15):
        deadline = time.time() + secs
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.05)
        self.fail("condition not reached")

    def test_serve_reports_and_survives_failures(self):
        did = self.st.add_device(os.urandom(32), "phone")
        with FakeCP() as cp:
            self.link(cp)
            cp.script = [500, 500, ("hang", 3), 500]
            p = self._serve()
            # start report + heartbeats keep coming although the control plane fails / hangs
            self._wait(lambda: len(cp.reports) >= 5, 20)
            self.assertIsNone(p.poll(), "serve still running")
            st = self.agentj("status")
            self.assertEqual(st.returncode, 0)
            self.assertEqual(json.loads(st.stdout)["dashboard"], "acme-co")
            self._wait(lambda: any(e["ev"] == "report_ok" for e in self.log()))
            fails = [e["status"] for e in self.log() if e["ev"] == "report_fail"]
            self.assertIn("http_5xx", fails)
            self.assertEqual(cp.rejected, [])
            for rep in cp.reports:
                self.assertEqual(rep["devices"][0]["id"], did)
                self.assertFalse(rep["devices"][0]["online"])
            seqs = [r["seq"] for r in cp.reports]
            self.assertEqual(seqs, sorted(set(seqs)), "seq strictly increasing")

            # `agentj report` while serve runs asks serve for the live view
            n = len(cp.reports)
            r = self.agentj("report")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertGreater(len(cp.reports), n)

            # 403 → logged once, reporting stops for this binding; serve keeps running
            cp.script = ["unbound"] * 50
            self._wait(lambda: any(e["ev"] == "report_unbound" for e in self.log()))
            time.sleep(2.5)
            self.assertEqual(sum(e["ev"] == "report_unbound" for e in self.log()), 1)
            self.assertIsNone(p.poll())
        # control plane gone entirely → network failures, still alive
        self.assertIsNone(p.poll())
        for rec in self.log():
            self.assertLessEqual(set(rec), {"ts", "ev", "channel", "cid", "device", "name", "reason", "kind", "bytes",
                                            "code_ok", "seq", "status", "trigger", "tenant"})
            if rec["ev"].startswith("report_"):
                self.assertNotIn("name", rec)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(10), 0)

    def test_report_view_over_control_socket(self):
        import asyncio
        from agentj.cli import _ctl_call
        with FakeCP() as cp:
            self.link(cp)
            self._serve()
            v = asyncio.run(_ctl_call(self.st, {"cmd": "report_view"}))
            self.assertEqual(v, {"ok": True, "online": [], "pending": {"count": 0, "since": None}})


if __name__ == "__main__":
    unittest.main()
