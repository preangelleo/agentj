"""P418 (#183 ⑤): Agent J's own browser closed by the owner (or not back after a restart) is started again when an action
needs it — only Agent J's installed service, once (`reset-failed` + `start`, launchd `kickstart`), then the action carries
on with `restarted: true`. An adopted browser (the owner's Chrome) is never started: the answer says to open it or how to
switch. A failed start keeps the action's own error plus one `next` step.

Every service-manager call is a mock: the user manager is not isolated by a fake HOME, so nothing here may reach the real
`systemctl --user`, and no real browser is started."""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import browser, browser_sites as bs, service  # noqa: E402
from agentj.state import State  # noqa: E402

RT = {"pid": 4242, "port": 45555, "ws": "ws://127.0.0.1:45555/devtools/browser/x", "product": "Chrome/156", "headless": False}


def _no_real_process(*a, **k):
    raise AssertionError(f"a real process was about to start: {a[:1]}")


class Wake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p418b-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.st = State(root / "state")
        self.st.root.mkdir(mode=0o700)
        self.units = root / "units"
        self.units.mkdir()
        self.calls: list[tuple] = []
        self.up_after_start = True
        self.start_rc = 0
        self.started = False
        self.adopted = None
        self.adopted_up = False
        self.enabled = True
        self.have = {"version": "t", "platform": "linux-x64", "path": "/nonexistent/chrome"}
        (self.units / f"{browser.svc_name()}.service").write_text("[Unit]\n")

        def systemctl(*args, check=False, timeout=30):
            self.calls.append(("systemctl", *args))
            rc = 0
            if args[0] == "start":
                rc = self.start_rc
                self.started = rc == 0
            return subprocess.CompletedProcess(["systemctl", "--user", *args], rc, "", "")

        def launchctl(*args, timeout=30):
            self.calls.append(("launchctl", *args))
            self.started = True
            return subprocess.CompletedProcess(["launchctl", *args], 0, "", "")

        def runtime(st):
            return dict(RT) if self.started and self.up_after_start else None
        for p in (patch.object(service, "_systemctl", systemctl), patch.object(service, "_launchctl", launchctl),
                  patch.object(service, "_bootstrap", lambda n, path: launchctl("bootstrap", path)),
                  patch.object(service, "unit_dir", lambda environ=None: str(self.units)),
                  patch.object(service, "platform", lambda: "linux"),
                  patch.object(browser.shutil, "which", lambda name: "/usr/bin/" + name),
                  patch.object(browser, "runtime", runtime),
                  patch.object(browser, "installed", lambda st: self.have),
                  patch.object(browser, "settings", lambda st=None: {"enabled": self.enabled}),
                  patch.object(browser, "adopted", lambda st: self.adopted),
                  patch.object(browser, "_probe_adopted", lambda port: {"ws": "ws://x", "product": "p"} if self.adopted_up else None),
                  patch.object(browser, "WAKE_WAIT", 0.6),
                  patch.object(browser.subprocess, "run", _no_real_process),
                  patch.object(browser.subprocess, "Popen", _no_real_process),
                  patch.object(service.subprocess, "run", _no_real_process)):
            p.start()
            self.addCleanup(p.stop)

    def starts(self):
        return [c for c in self.calls if c[1] in ("start", "kickstart", "bootstrap", "restart", "enable")]

    # ---------------------------------------------------------------- Agent J's own browser
    def test_closed_browser_is_started_and_the_action_carries_on(self):
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["restarted"])
        self.assertIn("浏览器刚才没开，已重新打开", res["restarted_note"])
        self.assertEqual(res["endpoint"], f"http://127.0.0.1:{RT['port']}")
        n = browser.svc_name() + ".service"
        self.assertEqual(self.calls, [("systemctl", "reset-failed", n), ("systemctl", "start", n)])

    def test_open_runs_after_the_start(self):
        seen = []

        def open_url(st, url):
            seen.append(browser.runtime(st))
            return {"ok": True, "headless": False}
        with patch.object(bs, "open_url", open_url):
            res = browser.execute({"action": "open", "url": "https://example.com/"}, self.st)
        self.assertTrue(res["ok"] and res["restarted"], res)
        self.assertEqual(seen, [RT])

    def test_check_runs_after_the_start(self):
        with patch.object(bs, "check", lambda st, sites, if_stale=False: {"ok": True, "running": bool(browser.runtime(st))}):
            res = browser.execute({"action": "check"}, self.st)
        self.assertTrue(res["running"] and res["restarted"], res)

    def test_running_browser_is_left_alone(self):
        self.started = True
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertTrue(res["ok"])
        self.assertNotIn("restarted", res)
        self.assertEqual(self.calls, [])

    def test_start_refused_keeps_the_original_error_and_adds_a_next_step(self):
        self.start_rc = 1
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_running")
        self.assertEqual(res["hint"], browser.HINTS["not_running"])
        self.assertEqual(res["next"], browser.WAKE_FAILED)
        self.assertNotIn("restarted", res)

    def test_started_but_never_answers(self):
        self.up_after_start = False
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertFalse(res["ok"])
        self.assertEqual(res["next"], browser.WAKE_FAILED)
        self.assertEqual(len([c for c in self.calls if c[1] == "start"]), 1)    # once, never a loop of starts

    def test_no_installed_service_is_never_started_or_installed(self):
        os.unlink(self.units / f"{browser.svc_name()}.service")
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertFalse(res["ok"])
        self.assertEqual(res["next"], browser.WAKE_FAILED)
        self.assertEqual(self.calls, [])

    def test_disabled_or_not_installed_does_nothing(self):
        for what in ("disabled", "not_installed"):
            with self.subTest(what=what):
                self.calls.clear()
                self.enabled, self.have = (False, self.have) if what == "disabled" else (True, None)
                res = browser.execute({"action": "endpoint"}, self.st)
                self.assertFalse(res["ok"])
                self.assertNotIn("next", res)
                self.assertEqual(self.calls, [])

    def test_actions_without_the_browser_never_start_it(self):
        with patch.object(bs, "summary", lambda st: []), patch.object(bs, "telegram_qr", lambda st, v: {"ok": True}), \
                patch.object(browser, "service_status", lambda: {"kind": "systemd", "active": "inactive"}), \
                patch.object(browser, "source", lambda st: {"kind": "own"}):
            for a in ("status", "telegram_qr", "sites_list"):
                with patch.object(bs, "template_list", lambda st: []):
                    browser.execute({"action": a}, self.st)
        self.assertEqual(self.calls, [])

    def test_macos_kickstarts_the_launch_agent(self):
        home = pathlib.Path(self.tmp.name) / "home"
        la = home / "Library" / "LaunchAgents"
        la.mkdir(parents=True)
        with patch.object(service, "platform", lambda: "macos"), patch.dict(os.environ, {"HOME": str(home)}):
            (la / f"{browser.svc_name()}.plist").write_text("<plist/>")
            res = browser.execute({"action": "endpoint"}, self.st)
        self.assertTrue(res["restarted"], res)
        self.assertEqual([c[:2] for c in self.calls], [("launchctl", "kickstart")])

    # ---------------------------------------------------------------- the owner's own Chrome (adopted)
    def test_adopted_browser_is_never_started(self):
        self.adopted = {"port": 9222, "product": "Chrome", "adopted_at": 1}
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "adopt_not_running")
        self.assertEqual(res["hint"], browser.ADOPT_DOWN)
        self.assertIn("请在电脑上打开它", res["hint"])
        self.assertIn("agentj browser adopt --forget", res["hint"])
        self.assertIn("agentj browser setup --own", res["hint"])
        self.assertEqual(self.calls, [])
        self.assertNotIn("restarted", res)

    def test_adopted_browser_running_is_used_as_is(self):
        self.adopted = {"port": 9222, "product": "Chrome", "adopted_at": 1}
        self.adopted_up = True
        self.started = True
        res = browser.execute({"action": "endpoint"}, self.st)
        self.assertTrue(res["ok"])
        self.assertNotIn("next", res)
        self.assertEqual(self.calls, [])

    def test_words_for_the_owner(self):
        for t in (browser.RESTARTED_NOTE, browser.ADOPT_DOWN, browser.WAKE_FAILED):
            for word in ("relay", "serve", "harness"):
                self.assertNotIn(word, t.lower())


if __name__ == "__main__":
    unittest.main()
