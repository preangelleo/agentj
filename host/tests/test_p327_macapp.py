"""P327: Agent J's macOS LaunchAgents start through a Developer ID–signed, notarized "Agent J.app" launcher so System
Settings → Login Items → App Background Activity shows "Agent J" + icon + developer instead of "agentj · unidentified
developer". The app is downloaded from /dl against the SHA-256 pinned in agentj/macapp.json; every failure falls back to the
unsigned LaunchAgent exactly as before. Throw-away HOME; codesign / ditto / launchctl / network all faked — the real
~/Library and ~/Applications are never touched. The real-macOS run (ad-hoc build, launchd, test HOME) is in REPORT-327."""
import _hermetic  # noqa: F401
import hashlib
import io
import json
import os
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HOST = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from agentj import browser, macapp, service  # noqa: E402
from agentj.state import State  # noqa: E402

TEAM = "44A2RNHQ5G"


def app_zip(version="3", bundle_id="app.agentj.agent"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Agent J.app/Contents/MacOS/Agent J", b"\xcf\xfa\xed\xfe launcher")
        z.writestr("Agent J.app/Contents/Info.plist", plistlib.dumps({"CFBundleIdentifier": bundle_id, "CFBundleVersion": version}))
    return buf.getvalue()


def pin_for(data, version="3"):
    return {"version": version, "file": f"AgentJ-macos-{version}.zip", "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "team": TEAM}


class FakeMac:
    """subprocess.run stand-in: ditto extracts with zipfile; `codesign --verify -R=<req>` passes when the bundle is intact
    (`good`) and was signed by `team` — i.e. the requirement names that Team ID; lsregister ok."""
    def __init__(self, good=True, team=TEAM):
        self.good, self.team, self.calls = good, team, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[0] == "ditto":
            zipfile.ZipFile(argv[-2]).extractall(argv[-1])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "codesign" and "--verify" in argv:
            req = next(a for a in argv if a.startswith("-R="))
            ok = self.good and req.startswith("-R=anchor apple generic") and f'leaf[subject.OU] = "{self.team}"' in req
            return subprocess.CompletedProcess(argv, 0 if ok else 3, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


class MacApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        self.dl = self.home / "dl"
        self.dl.mkdir()
        for p in (mock.patch.dict(os.environ, {"HOME": str(self.home)}),
                  mock.patch.object(macapp, "MIRRORS", (self.dl.as_uri() + "/{f}",)),
                  mock.patch.object(macapp.sys, "platform", "darwin")):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(macapp.OFF_ENV, None)
        self.data = app_zip()
        (self.dl / "AgentJ-macos-3.zip").write_bytes(self.data)
        self.pin = pin_for(self.data)

    def ensure(self, fake=None, pin=None):
        self.fake = fake or FakeMac()
        with mock.patch.object(macapp, "pin", return_value=pin or self.pin), mock.patch.object(macapp.subprocess, "run", self.fake):
            return macapp.ensure()

    def test_shipped_pin_is_well_formed(self):
        p = json.loads((HOST / "agentj" / "macapp.json").read_text())
        self.assertEqual(p["team"], TEAM)
        if p["version"]:       # once sign.py has pinned a notarized build
            self.assertRegex(p["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(p["file"], f"AgentJ-macos-{p['version']}.zip")

    def test_downloads_verifies_installs_into_user_applications(self):
        exe = self.ensure()
        self.assertEqual(exe, self.home / "Applications" / "Agent J.app" / "Contents" / "MacOS" / "Agent J")
        self.assertTrue(exe.is_file())
        self.assertFalse([p for p in (self.home / "Applications").iterdir() if p.name.startswith(".agentj-app-")], "temp dir cleaned")
        self.assertIn("-f", next(c for c in self.fake.calls if c[0].endswith("lsregister")))

    def test_reuses_a_verified_app_without_downloading(self):
        self.ensure()
        (self.dl / "AgentJ-macos-3.zip").unlink()
        self.assertIsNotNone(self.ensure())
        self.assertFalse([c for c in self.fake.calls if c[0] == "ditto"])

    def test_wrong_bytes_never_installed(self):
        (self.dl / "AgentJ-macos-3.zip").write_bytes(app_zip(version="4"))
        self.assertIsNone(self.ensure())
        self.assertFalse((self.home / "Applications" / "Agent J.app").exists())

    def test_broken_signature_or_other_team_falls_back(self):
        self.assertIsNone(self.ensure(FakeMac(good=False)))
        self.assertIsNone(self.ensure(FakeMac(team="ZZZZZZZZZZ")))
        self.assertFalse((self.home / "Applications" / "Agent J.app").exists())

    def test_tampered_installed_app_is_replaced(self):
        exe = self.ensure()
        class Once(FakeMac):          # the installed copy fails verification once (tampered), the fresh one passes
            n = 0
            def __call__(s, argv, **kw):
                if argv[0] == "codesign" and "--verify" in argv and Once.n == 0:
                    Once.n += 1
                    s.calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, "", "")
                return super().__call__(argv, **kw)
        self.assertEqual(self.ensure(Once()), exe)
        self.assertTrue([c for c in self.fake.calls if c[0] == "ditto"], "re-downloaded")

    def test_no_pin_not_macos_offline_or_switched_off_is_unsigned(self):
        self.assertIsNone(self.ensure(pin={"version": None, "sha256": None, "team": TEAM}))
        with mock.patch.object(macapp.sys, "platform", "linux"):
            self.assertIsNone(self.ensure())
        with mock.patch.object(macapp, "MIRRORS", ((self.home / "nowhere").as_uri() + "/{f}",)):
            self.assertIsNone(self.ensure())
        with mock.patch.dict(os.environ, {macapp.OFF_ENV: "off"}):
            self.assertIsNone(self.ensure())

    def test_wrap(self):
        with mock.patch.object(macapp, "ensure", return_value=None):
            self.assertEqual(macapp.wrap(["/v/bin/agentj"]), (["/v/bin/agentj"], {}))
        exe = macapp.exe_path()
        ok, refused = (mock.Mock(return_value=subprocess.CompletedProcess([], rc)) for rc in (0, 64))
        with mock.patch.object(macapp, "ensure", return_value=exe), mock.patch.object(macapp.subprocess, "run", ok):
            self.assertEqual(macapp.wrap(["/v/bin/agentj"]),
                             ([str(exe), "/v/bin/agentj"], {"AssociatedBundleIdentifiers": ["app.agentj.agent"]}))
        self.assertEqual(ok.call_args[0][0], [str(exe), "/v/bin/agentj", "--version"], "probed through the launcher first")
        with mock.patch.object(macapp, "ensure", return_value=exe), mock.patch.object(macapp.subprocess, "run", refused):
            self.assertEqual(macapp.wrap(["/v/bin/agentj"]), (["/v/bin/agentj"], {}), "launcher refuses → unsigned, service still starts")

    def test_requirement_is_apple_anchored_and_team_pinned(self):
        r = macapp.requirement({"team": TEAM})
        self.assertTrue(r.startswith("anchor apple generic and "))
        self.assertIn('identifier "app.agentj.agent"', r)
        self.assertIn(f'certificate leaf[subject.OU] = "{TEAM}"', r)
        self.assertFalse(macapp.verified(macapp.app_path(), dict(self.pin, team='X" or true')), "no requirement injection")
        self.assertFalse(hasattr(macapp, "URLS_ENV"), "no environment override of the download URL")
        self.assertIn('MIRRORS = ("https://agentj.app/dl/{f}",)', (HOST / "agentj" / "macapp.py").read_text(), "https only")

    def test_launcher_source_only_execs_agentj(self):
        src = HOST.parents[0] / "release" / "macos" / "launcher.c"
        if not src.exists():
            self.skipTest("launcher source is private (not in the public tree)")
        c = src.read_text()
        self.assertIn('strcmp(base, "agentj")', c)
        self.assertIn('strcmp(argv[4], "agentj.cli")', c, "python only as `-P -m agentj.cli`, never -c")
        self.assertIn("target[0] == '/'", c)
        self.assertIn('"/../pyvenv.cfg"', c)
        self.assertIn("S_IWGRP | S_IWOTH", c)
        self.assertIn("execv(target, argv + 1)", c)


class ServicePlist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        self.st = State(self.home / "state")
        self.st.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for p in (mock.patch.dict(os.environ, {"HOME": str(self.home), "AGENTJ_STATE_DIR": str(self.st.root),
                                               "AGENTJ_SERVICE_NAME": "net.agentj.host"}),
                  mock.patch.object(service, "platform", return_value="macos"),
                  mock.patch.object(service, "_gui", return_value="gui/501"),
                  mock.patch.object(service, "_launchctl", return_value=mock.Mock(returncode=113, stdout="", stderr="")),
                  mock.patch.object(service, "_bootstrap", return_value=mock.Mock(returncode=0)),
                  mock.patch.object(service, "prepare_binary_env", return_value=({"PATH": "/usr/bin"}, [])),
                  mock.patch.object(service, "previous_binary_selection", return_value={}),
                  mock.patch.object(service, "remember_binary_selection"),
                  mock.patch.object(service, "remove_legacy", return_value=None),
                  mock.patch.object(service, "launchd_disabled", return_value=None),
                  mock.patch.object(service, "_register_pair_protocol"),
                  mock.patch.object(service, "agentj_argv", return_value=["/v/bin/agentj"]),
                  mock.patch.object(macapp.subprocess, "run", FakeMac())):      # the launcher probe (`--version`) passes
            p.start()
            self.addCleanup(p.stop)
        self.exe = macapp.exe_path()

    def plist(self):
        return plistlib.loads(pathlib.Path(service.plist_path("net.agentj.host")).read_bytes())

    def test_install_through_the_signed_app(self):
        with mock.patch.object(macapp, "ensure", return_value=self.exe):
            r = service.install(self.st)
        p = self.plist()
        self.assertEqual(p["ProgramArguments"], [str(self.exe), "/v/bin/agentj", *service.SERVE_ARGS])
        self.assertEqual(p["AssociatedBundleIdentifiers"], ["app.agentj.agent"])
        self.assertEqual(r["argv"][0], str(self.exe))
        for k in ("RunAtLoad", "KeepAlive", "EnvironmentVariables", "StandardOutPath"):
            self.assertIn(k, p, "everything else as before")

    def test_install_unsigned_fallback_is_the_old_plist(self):
        with mock.patch.object(macapp, "ensure", return_value=None):
            service.install(self.st)
        p = self.plist()
        self.assertEqual(p["ProgramArguments"], ["/v/bin/agentj", *service.SERVE_ARGS])
        self.assertNotIn("AssociatedBundleIdentifiers", p)

    def test_browser_service_through_the_signed_app(self):
        data = browser.plist_bytes("net.agentj.browser", [str(self.exe), "/v/bin/agentj"], "/l",
                                   extra={"AssociatedBundleIdentifiers": ["app.agentj.agent"]})
        p = plistlib.loads(data)
        self.assertEqual(p["ProgramArguments"][-2:], ["browser", "run"])
        self.assertEqual(p["AssociatedBundleIdentifiers"], ["app.agentj.agent"])
        src = (HOST / "agentj" / "browser.py").read_text()
        self.assertIn("service.macapp.wrap(argv)", src)

    def test_uninstall_removes_the_app_only_when_unused(self):
        with mock.patch.object(macapp, "ensure", return_value=self.exe):
            service.install(self.st)
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"x")
        other = pathlib.Path(service.plist_path("net.agentj.browser"))      # still starts through the app
        other.write_bytes(browser.plist_bytes("net.agentj.browser", [str(self.exe), "/v/bin/agentj"], "/l"))
        with mock.patch.object(macapp.subprocess, "run", FakeMac()):
            self.assertFalse(service.uninstall(self.st)["app_removed"])
            self.assertTrue(macapp.app_path().exists())
            other.unlink()
            self.assertTrue(macapp.remove_if_unused())
        self.assertFalse(macapp.app_path().exists())

    def test_linux_untouched(self):
        with mock.patch.object(macapp.sys, "platform", "linux"):
            self.assertIsNone(macapp.ensure())


if __name__ == "__main__":
    unittest.main()
