"""P73 C2 (ADR-A178): "another writer" on a shared Codex thread — who holds it, and our own leftovers cleaned up.

- every app-server serve starts is recorded with its start time; a recorded process that outlived its serve is ended at the
  next serve start (process group), a reused PID or a still-live child of this serve never;
- an `already has an active writer` refusal names the holder: Agent J's own leftover (ended, resume retried once), the ChatGPT
  App's Codex, the Codex App, another app-server, a terminal `codex`, or "could not find out".
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import codex_procs  # noqa: E402
from agentj.shared_codex import Refusal  # noqa: E402

from test_p51_codex_shared import Base as SharedBase, SID  # noqa: E402


def _st(root):
    return SimpleNamespace(root=pathlib.Path(root))


def _orphan(tmp, tag="app-server"):
    """A detached `… app-server` process (its own session, parent = not us): what a killed serve leaves behind."""
    out = pathlib.Path(tmp) / "pid"
    subprocess.run(["sh", "-c", f"setsid {sys.executable} -c 'import time; time.sleep(120)' {tag} >/dev/null 2>&1 & echo $! > {out}"],
                   check=True)
    for _ in range(100):
        if out.exists() and out.read_text().strip():
            break
        time.sleep(0.02)
    pid = int(out.read_text())
    for _ in range(100):
        if tag in codex_procs.cmdline(pid):
            return pid
        time.sleep(0.02)
    return pid


class Pidfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _st(self.tmp.name)
        self.pids = []
        self.addCleanup(self._reap)

    def _reap(self):
        for pid in self.pids:
            with __import__("contextlib").suppress(OSError):
                os.kill(pid, 9)

    def test_record_forget_private(self):
        codex_procs.record(self.st, os.getpid())
        e = codex_procs.entries(self.st)
        self.assertEqual(e[0]["pid"], os.getpid())
        self.assertIsNotNone(e[0]["start"])
        self.assertEqual(oct((self.st.root / codex_procs.FILE).stat().st_mode & 0o777), "0o600")
        codex_procs.forget(self.st, os.getpid())
        self.assertEqual(codex_procs.entries(self.st), [])

    @unittest.skipUnless(sys.platform.startswith(("linux", "darwin")), "POSIX process table")
    def test_sweep_ends_our_leftover_only(self):
        mine = _orphan(self.tmp.name)
        self.pids.append(mine)
        codex_procs.record(self.st, mine)
        stranger = _orphan(tempfile.mkdtemp(dir=self.tmp.name))      # a codex-looking process nobody recorded
        self.pids.append(stranger)
        reused = _orphan(tempfile.mkdtemp(dir=self.tmp.name))        # recorded, but with another start time: a reused PID
        self.pids.append(reused)
        rows = codex_procs.entries(self.st) + [{"pid": reused, "start": "1", "ts": 0}]
        codex_procs._save(self.st, rows)
        self.assertEqual(codex_procs.ours_alive(self.st), [mine])
        self.assertEqual(codex_procs.sweep(self.st), [mine])
        self.assertIsNone(codex_procs.start_token(mine), "the leftover is gone")
        self.assertIsNotNone(codex_procs.start_token(stranger), "never a process we did not start")
        self.assertIsNotNone(codex_procs.start_token(reused), "never a reused PID")
        self.assertEqual(codex_procs.entries(self.st), [], "the pidfile is pruned")

    def test_live_child_of_this_serve_is_not_a_leftover(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "app-server"], start_new_session=True)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        time.sleep(0.2)
        codex_procs.record(self.st, p.pid)
        self.assertEqual(codex_procs.ours_alive(self.st), [], "a scheduled run's app-server is still ours and running")
        self.assertEqual(codex_procs.sweep(self.st), [])
        self.assertIsNone(p.poll())


class Classify(unittest.TestCase):
    def test_kinds(self):
        c = codex_procs.classify
        self.assertEqual(c("/Applications/ChatGPT.app/Contents/Resources/codex app-server --listen stdio"), "chatgpt_app")
        self.assertEqual(c("/Applications/Codex.app/Contents/Resources/codex app-server"), "codex_app")
        self.assertEqual(c("/srv/u/.codex/packages/app-server-daemon/releases/0.160.1/bin/codex app-server"), "app_server")
        self.assertEqual(c("/usr/local/bin/codex resume 0199"), "terminal")
        self.assertEqual(c("node /opt/homebrew/bin/codex"), "terminal")
        self.assertEqual(c("codex"), "terminal")
        self.assertIsNone(c("python3 /usr/share/distro/bin/distro-agent-usage-codex"))
        self.assertIsNone(c("/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"))
        self.assertIsNone(c("vim notes-about-codex.md"))


class Who(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _st(self.tmp.name)

    def test_open_file_names_the_holder(self):
        with patch.object(codex_procs, "fd_holders", return_value=[4242]), \
                patch.object(codex_procs, "cmdline", return_value="/Applications/ChatGPT.app/Contents/Resources/codex app-server"):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl"), {"kind": "chatgpt_app", "pid": 4242, "how": "fd"})
        with patch.object(codex_procs, "fd_holders", return_value=[4243]), \
                patch.object(codex_procs, "cmdline", return_value="codex resume x"):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl")["kind"], "terminal")

    def test_current_app_server_is_never_the_holder(self):
        with patch.object(codex_procs, "fd_holders", return_value=[77]), patch.object(codex_procs, "proc_table", return_value=[]):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl", current=77)["kind"], "unknown")

    def test_our_leftover_first(self):
        with patch.object(codex_procs, "fd_holders", return_value=[]), patch.object(codex_procs, "ours_alive", return_value=[555]):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl"), {"kind": "agentj", "pid": 555, "how": "ps"})
        with patch.object(codex_procs, "fd_holders", return_value=[555]), patch.object(codex_procs, "ours_alive", return_value=[555]):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl")["how"], "fd")

    def test_process_list_fallback_and_unknown(self):
        table = [(10, "/usr/bin/zsh"), (11, "/usr/local/bin/codex"), (12, "/Applications/Codex.app/Contents/Resources/codex app-server")]
        with patch.object(codex_procs, "fd_holders", return_value=None), patch.object(codex_procs, "proc_table", return_value=table):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl"), {"kind": "codex_app", "pid": 12, "how": "ps"})
        with patch.object(codex_procs, "fd_holders", return_value=None), patch.object(codex_procs, "proc_table", return_value=[]):
            self.assertEqual(codex_procs.who(self.st, "/r.jsonl")["kind"], "unknown")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc/*/fd")
    def test_real_open_file_is_found(self):
        f = pathlib.Path(self.tmp.name) / "rollout-x.jsonl"
        f.write_text("{}\n")
        p = subprocess.Popen([sys.executable, "-c", f"f=open({str(f)!r}); import time; time.sleep(30)", "codex", "app-server"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        for _ in range(100):
            if p.pid in (codex_procs.fd_holders(f) or []):
                break
            time.sleep(0.05)
        self.assertIn(p.pid, codex_procs.fd_holders(f))

    def test_texts(self):
        for kind in ("agentj", "chatgpt_app", "codex_app", "terminal", "app_server", "unknown"):
            zh, en = codex_procs.text(kind, 9, True), codex_procs.text(kind, 9, False)
            self.assertIn("只读", zh)
            self.assertIn("read-only", en)
        self.assertIn("ChatGPT App", codex_procs.text("chatgpt_app"))
        self.assertIn("终端里的 codex", codex_procs.text("terminal", 9))
        self.assertIn("查不到是谁", codex_procs.text("unknown"))
        self.assertIn("已自动清理", codex_procs.text("agentj_cleaned"))


class SharedWriter(SharedBase):
    def setUp(self):
        super().setUp()
        self.host.st.root = pathlib.Path(self.tmp.name) / "state"
        self.host.st.root.mkdir()

    async def test_our_leftover_is_ended_and_resume_retried(self):
        self.rollout(SID)
        a = self.agent(SID, language="zh")
        a.human = {}
        n = {"resume": 0}
        orig = a.call.side_effect

        async def call(method, params, timeout=60):
            if method == "thread/resume":
                n["resume"] += 1
                if n["resume"] == 1:
                    self.calls.append((method, params))
                    from agentj.agent_codex import RPCError
                    raise RPCError({"message": "thread already has an active writer"})
            return await orig(method, params)
        a.call.side_effect = call
        with patch.object(codex_procs, "who", return_value={"kind": "agentj", "pid": 999, "how": "fd"}), \
                patch.object(codex_procs, "ours_alive", return_value=[999]), \
                patch.object(codex_procs, "_end", return_value=True) as end:
            self.assertTrue(await a._thread())
        end.assert_called_once_with(999)
        self.assertEqual([m for m, _ in self.calls], ["thread/resume", "thread/resume"])
        self.assertEqual(a.tid, SID)
        self.assertIsNone(a.shared_status)
        self.assertIn("已自动清理", self.notices())
        self.assertTrue(self.events("shared_writer_cleaned"))

    async def test_other_holders_are_named_and_never_killed(self):
        for kind, needle in (("chatgpt_app", "ChatGPT App"), ("terminal", "终端里的 codex（PID 31）"), ("unknown", "查不到是谁"),
                             ("codex_app", "Codex App")):
            self.calls.clear()
            self.host.reset_mock()
            self.host.st.root = pathlib.Path(self.tmp.name) / "state"
            self.host.st.agent_session.return_value = None
            self.rollout(SID)
            a = self.agent(SID, language="zh")
            self.resume_error = "thread already has an active writer"
            with patch.object(codex_procs, "who", return_value={"kind": kind, "pid": 31, "how": "fd"}), \
                    patch.object(codex_procs, "_end") as end:
                with self.assertRaises(Refusal) as r:
                    await a._thread()
            end.assert_not_called()
            self.assertEqual(r.exception.code, "desktop writer active")
            self.assertEqual(a.shared_status, "desktop_writer")
            self.assertIn(needle, self.notices())
            self.assertEqual([m for m, _ in self.calls], ["thread/resume"], "no replacement thread")
            self.assertEqual(self.events("shared_writer")[0]["reason"], kind)

    async def test_leftover_that_will_not_die_is_said_so(self):
        self.rollout(SID)
        a = self.agent(SID, language="en")
        self.resume_error = "thread already has an active writer"
        with patch.object(codex_procs, "who", return_value={"kind": "agentj", "pid": 999, "how": "fd"}), \
                patch.object(codex_procs, "ours_alive", return_value=[999]), patch.object(codex_procs, "_end", return_value=False):
            with self.assertRaises(Refusal):
                await a._thread()
        self.assertIn("agentj service restart", self.notices())
        self.assertEqual([m for m, _ in self.calls], ["thread/resume"])


if __name__ == "__main__":
    unittest.main()
