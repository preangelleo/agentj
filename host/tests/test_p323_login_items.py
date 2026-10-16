"""P323: macOS System Settings → Login Items → "App Background Activity" listed 11 identical "agentj · Item from unidentified
developer" rows on the owner's Mac (10-15). Each was a finished one-shot `net.agentj.switch.<uuid>` job plist left in
~/Library/LaunchAgents (0.21.1a1's cleanup did not know `switch`), every one RunAtLoad — re-run at each login — and each new
one raised a "Background Items Added" notice. Now one-shot jobs are bootstrapped from ~/Library/Application Support/Agent J/jobs
(never a Login Items row, never loaded at login), and install / serve start / uninstall sweep finished leftovers.
Everything runs in a throw-away HOME with launchctl mocked; the real ~/Library is never touched.
"""
import _hermetic  # noqa: F401
import os
import pathlib
import plistlib
import sys
import tempfile
import time
import unittest
from unittest import mock

HOST = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from agentj import service, service_recovery  # noqa: E402
from agentj.state import State  # noqa: E402

OLD = time.time() - 3600


def _ctl(running=()):
    """A fake launchctl: `print gui/<uid>/<label>` says running for the labels in `running`."""
    calls = []

    def run(*args, timeout=30):
        calls.append(args)
        if args[0] == "print":
            label = args[1].rsplit("/", 1)[-1]
            if label in running:
                return mock.Mock(returncode=0, stdout="\tstate = running\n", stderr="")
            return mock.Mock(returncode=113, stdout="", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")
    return run, calls


class LoginItems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.st = State(self.home / "state")
        self.st.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        for p in (mock.patch.dict(os.environ, {"HOME": str(self.home), "AGENTJ_STATE_DIR": str(self.st.root)}),
                  mock.patch.object(service, "platform", return_value="macos"),
                  mock.patch.object(service, "_gui", return_value="gui/501")):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("AGENTJ_JOB_LABEL", None)
        self.addCleanup(self.tmp.cleanup)

    def leftover(self, d, purpose, n, mtime=OLD):
        label = f"net.agentj.{purpose}.{n:032x}"
        p = pathlib.Path(d) / (label + ".plist")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["agentj", "agent", "switch", "--worker"], "RunAtLoad": True}))
        os.utime(p, (mtime, mtime))
        return label, p

    def test_job_plist_is_never_in_launch_agents(self):
        run, calls = _ctl()
        with mock.patch.object(service, "_launchctl", side_effect=run):
            for purpose in ("recovery", "update", "autoupdate", "switch"):
                service_recovery.launch_job(self.st, ["agentj", "x"], purpose)
        self.assertEqual(list(self.agents.iterdir()), [], "a LaunchAgents plist is a Login Items row")
        boots = [c for c in calls if c[0] == "bootstrap"]
        self.assertEqual(len(boots), 4)
        jobs = pathlib.Path(service.job_dir())
        self.assertEqual(jobs, self.home / "Library" / "Application Support" / "Agent J" / "jobs")
        for c in boots:
            p = pathlib.Path(c[-1])
            self.assertEqual(p.parent, jobs)
            self.assertFalse(p.is_relative_to(self.st.root), "a fenced caller cannot write the state dir")
            job = plistlib.loads(p.read_bytes())
            self.assertTrue(service.JOB_RX.fullmatch(job["Label"]))
            self.assertEqual(job["EnvironmentVariables"]["AGENTJ_JOB_PLIST"], str(p))
            with mock.patch.object(service, "_launchctl", side_effect=run), mock.patch.dict(os.environ, job["EnvironmentVariables"]):
                service_recovery.cleanup_job(self.st)
            self.assertFalse(p.exists())

    def test_cleanup_removes_a_job_an_older_version_wrote_into_launch_agents(self):
        label, p = self.leftover(self.agents, "switch", 7)
        run, calls = _ctl()
        with mock.patch.object(service, "_launchctl", side_effect=run), \
                mock.patch.dict(os.environ, {"AGENTJ_JOB_LABEL": label, "AGENTJ_JOB_PLIST": str(p)}):
            service_recovery.cleanup_job(self.st)
        self.assertFalse(p.exists())
        self.assertIn(("bootout", "gui/501/" + label), calls)

    def test_sweep_removes_the_eleven_leftovers_and_nothing_else(self):
        labels = [self.leftover(self.agents, "switch", i)[0] for i in range(11)]
        running, _ = self.leftover(self.agents, "update", 100)
        young, _ = self.leftover(self.agents, "recovery", 101, mtime=time.time())
        own, _ = self.leftover(service.job_dir(), "autoupdate", 102)
        dead, _ = self.leftover(service.job_dir(), "switch", 103)
        keep = {"net.agentj.host.plist": b"host", "net.agentj.browser.plist": b"browser", "net.agentj.host.env": b"",
                "net.agentj.host.harness-scan.json": b"{}", "com.example.other.plist": b"x",
                "net.agentj.switch.notahexlabel.plist": b"x", "net.agentj.switch.%032x.plist.bak" % 5: b"x"}
        for n, b in keep.items():
            (self.agents / n).write_bytes(b)
            os.utime(self.agents / n, (OLD, OLD))
        run, calls = _ctl(running={running})
        with mock.patch.object(service, "_launchctl", side_effect=run), mock.patch.dict(os.environ, {"AGENTJ_JOB_LABEL": own}):
            removed = service.sweep_jobs()
        self.assertEqual(sorted(removed), sorted(labels + [dead]))
        left = sorted(p.name for p in self.agents.iterdir())
        self.assertEqual(left, sorted(list(keep) + [running + ".plist", young + ".plist"]))
        self.assertTrue(pathlib.Path(service.job_plist_path(own)).exists())
        for label in labels + [dead]:
            self.assertIn(("bootout", "gui/501/" + label), calls)
        self.assertFalse(any(c[0] == "bootout" and c[1].endswith(("/" + running, "/" + own, "/net.agentj.host")) for c in calls))

    def test_uninstall_takes_every_job_but_its_own(self):
        a, _ = self.leftover(self.agents, "switch", 1)
        b, _ = self.leftover(service.job_dir(), "update", 2, mtime=time.time())
        own, own_p = self.leftover(service.job_dir(), "recovery", 3)
        (self.agents / "net.agentj.host.plist").write_bytes(b"x")
        run, _ = _ctl(running={a, b})
        with mock.patch.object(service, "_launchctl", side_effect=run), \
                mock.patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "net.agentj.host", "AGENTJ_JOB_LABEL": own}):
            r = service.uninstall(self.st)
        self.assertTrue(r["removed"])
        self.assertEqual(r["jobs_removed"], 2)
        self.assertEqual(list(self.agents.iterdir()), [])
        self.assertEqual([p.name for p in pathlib.Path(service.job_dir()).iterdir()], [own_p.name])

    def test_uninstall_removes_the_empty_job_dir(self):
        self.leftover(service.job_dir(), "switch", 1)
        run, _ = _ctl()
        with mock.patch.object(service, "_launchctl", side_effect=run), \
                mock.patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "net.agentj.host"}):
            service.uninstall(self.st)
        self.assertFalse(os.path.exists(service.job_dir()))

    def test_install_sweeps_leftovers(self):
        labels = [self.leftover(self.agents, "switch", i)[0] for i in range(3)]
        run, _ = _ctl()
        with mock.patch.object(service, "_launchctl", side_effect=run), \
                mock.patch.object(service, "_bootstrap", return_value=mock.Mock(returncode=0)), \
                mock.patch.object(service, "prepare_binary_env", return_value=({"PATH": "/usr/bin"}, [])), \
                mock.patch.object(service, "previous_binary_selection", return_value={}), \
                mock.patch.object(service, "remember_binary_selection"), \
                mock.patch.object(service, "remove_legacy", return_value=None), \
                mock.patch.object(service, "launchd_disabled", return_value=None), \
                mock.patch.object(service, "_register_pair_protocol"), \
                mock.patch.object(service, "agentj_argv", return_value=["/x/agentj"]), \
                mock.patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "net.agentj.host"}):
            service.install(self.st)
        # P332: the rescue watchdog is a resident LaunchAgent too (it must come back after a login); no one-shot job remains
        self.assertEqual(sorted(p.name for p in self.agents.iterdir()), ["net.agentj.host.plist", "net.agentj.rescue.plist"])
        self.assertIn("launchd_jobs_swept", self.st.log_path.read_text())
        for label in labels:
            self.assertNotIn(label, self.st.log_path.read_text(), "the log carries the count only")

    def test_serve_start_sweeps(self):
        src = (HOST / "agentj" / "serve.py").read_text()
        body = src.split("async def upgraded_notice", 1)[1].split("\n    async def ", 1)[0]
        self.assertIn("service.sweep_jobs", body)

    def test_linux_sweep_is_a_no_op(self):
        self.leftover(self.agents, "switch", 1)
        with mock.patch.object(service, "platform", return_value="linux"), mock.patch.object(service, "_launchctl") as ctl:
            self.assertEqual(service.sweep_jobs(), [])
        ctl.assert_not_called()
        self.assertEqual(len(list(self.agents.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
