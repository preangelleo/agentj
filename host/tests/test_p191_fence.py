"""P191 (audit B3 / B6): pin the facts the privacy page and 12_ZERO_ACCESS §8b ③ state about the fence and Agent J's own
browser, so the disclosure cannot drift from the code. No behaviour change in P191: the session bus and the systemd user
manager stay bound back in (F14), the browser's debugging port stays random and loopback-only. All I/O hermetic."""
import _hermetic  # noqa: F401
import os
import pathlib
import re
import tempfile
import unittest

from agentj import browser, fence
from agentj.state import State

HOST = pathlib.Path(__file__).resolve().parents[1]
DOCS = HOST.parent / "site" / "content" / "docs" / "07-privacy"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p191-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        d = pathlib.Path(self.tmp.name)
        self.home, self.rt = d / "home", d / "run"
        (self.home / ".ssh").mkdir(parents=True)
        self.rt.mkdir()
        self.st = State(d / "state")
        self.st.init()

    def argv(self):
        env = {"XDG_RUNTIME_DIR": str(self.rt), "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.rt}/bus"}
        return fence.bwrap_argv(self.st, str(self.home), home=str(self.home), runtime=str(self.rt), environ=env)

    @staticmethod
    def after(a, flag):
        return [a[i + 1] for i, x in enumerate(a) if x == flag]


class BusAndServiceManager(Fixture):
    """B3: bound back (F14), the keyring's own control folder is not; nothing is bound when it does not exist."""

    def test_bus_and_systemd_bound_back_keyring_folder_hidden(self):
        (self.rt / "systemd").mkdir()
        (self.rt / "bus").write_text("")
        (self.rt / "keyring").mkdir()
        (self.rt / "gnupg").mkdir()
        a = self.argv()
        rt = os.path.realpath(self.rt)
        self.assertIn(rt, self.after(a, "--tmpfs"), "the runtime dir itself stays private")
        binds = self.after(a, "--bind")
        self.assertIn(os.path.join(rt, "bus"), binds)
        self.assertIn(os.path.join(rt, "systemd"), binds)
        for hidden in ("keyring", "gnupg"):
            self.assertNotIn(os.path.join(rt, hidden), binds + self.after(a, "--ro-bind"), hidden)
        self.assertLess(a.index("--tmpfs", a.index("--new-session")), a.index(os.path.join(rt, "bus")),
                        "the bus is put back on top of the private runtime dir, not under it")

    def test_absent_bus_and_systemd_are_not_invented(self):
        a = self.argv()
        rt = os.path.realpath(self.rt)
        binds = self.after(a, "--bind")
        self.assertNotIn(os.path.join(rt, "bus"), binds)
        self.assertNotIn(os.path.join(rt, "systemd"), binds)

    def test_only_the_two_documented_entries_come_back(self):
        self.assertEqual(fence._RUNTIME_BACK, ("systemd", "bus"))


class BrowserEndpoint(Fixture):
    """B6: random port, loopback only, profile and runtime record inside the hidden state folder."""

    def test_port_is_random_and_loopback_only(self):
        args = browser._launch_args("/x/chrome", pathlib.Path("/p"), False)
        ports = [a for a in args if a.startswith("--remote-debugging-port=")]
        self.assertEqual(ports, ["--remote-debugging-port=0"], "the browser picks the port; never a fixed one")
        self.assertIn(f"--remote-debugging-address={browser.LOOPBACK}", args)
        self.assertEqual(browser.LOOPBACK, "127.0.0.1")
        for bad in ("0.0.0.0", "--remote-allow-origins", "--remote-debugging-address=::"):
            self.assertFalse(any(bad in a for a in args), bad)

    def test_profile_and_runtime_record_are_hidden_from_the_agent(self):
        root = os.path.realpath(self.st.root)
        for p in (browser.profile_dir(self.st), browser.runtime_path(self.st)):
            self.assertTrue(os.path.realpath(p).startswith(root + os.sep), p)
        a = self.argv()
        self.assertIn(root, self.after(a, "--tmpfs"))
        perm = os.path.realpath(self.st.perm_dir)
        self.assertEqual([x for x in self.after(a, "--bind") if x.startswith(root + os.sep)], [perm],
                         "only the permission folder returns from the state dir; never browser/")

    def test_runtime_refuses_a_record_that_does_not_prove_ownership(self):
        browser.ensure_dir(self.st)
        browser._write_json(self.st, browser.runtime_path(self.st), {"pid": os.getpid(), "port": 1,
                                                                     "starttime": "not-this-process"})
        self.assertIsNone(browser.runtime(self.st), "a record that does not prove ownership is not our browser")


class Disclosure(unittest.TestCase):
    """The public privacy page says both things plainly (both languages)."""

    @unittest.skipUnless(DOCS.is_dir(), "the site sources are not in the public tree")
    def test_privacy_page_names_keyring_service_manager_and_browser_port(self):
        en = (DOCS / "en.md").read_text()
        zh = (DOCS / "zh.src.md").read_text()
        for needle in ("keyring for secrets over the session bus", "runs outside the fence", "picked at random",
                       "127.0.0.1 only", "no password"):
            self.assertIn(needle, en)
        for needle in ("钥匙串", "在隔离之外运行", "随机选", "没有密码"):
            self.assertIn(needle, zh)
        self.assertFalse(re.search(r"absolutely secure|zero-knowledge", en.split("We won't call it")[0]))


if __name__ == "__main__":
    unittest.main()
