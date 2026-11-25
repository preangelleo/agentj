from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentj import browser_ego as ego
from agentj import browser_login
from agentj.browser_cdp import CDPError


RT = {"backend": "ego-lite", "cli": "/official/ego-browser", "profile_id": "approved-profile"}
TARGET = "7:Agent J 19fd822279c854f8:p1:page-123"


class EgoBrowserTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Agent J processes the local CLI on macOS or WSL")
    def test_vendor_call_uses_stdin_and_never_surfaces_untrusted_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = pathlib.Path(folder) / "ego-browser"
            cli.write_text("#!" + sys.executable + "\n"
                           "import os, sys\n"
                           "code = sys.stdin.read()\n"
                           "assert sys.argv[1:] == ['nodejs']\n"
                           "assert code.startswith('async function agentjOperation() {\\nreturn [1,2];\\n}\\n')\n"
                           "assert code.endswith('JSON.stringify(agentjResult));\\n')\n"
                           "assert 'OPENAI_API_KEY' not in os.environ\n"
                           "print('private diagnostic')\n"
                           "print('AGENTJ_EGO [1,2]', file=sys.stderr)\n")
            cli.chmod(0o700)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}):
                self.assertEqual(ego._invoke(str(cli), "return [1,2];"), [1, 2])

    @unittest.skipUnless(os.name == "posix", "Agent J processes the local CLI on macOS or WSL")
    def test_multiple_markers_errors_and_large_output_never_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = pathlib.Path(folder) / "ego-browser"
            for code, error in (
                ("print('AGENTJ_EGO true'); print('AGENTJ_EGO false', file=sys.stderr)",
                 "Ego Lite did not answer"),
                ("print('private error'); sys.exit(1)", "Ego Lite did not answer"),
                ("print('x' * 2 * 1024 * 1024)", "Ego Lite reply too large"),
            ):
                with self.subTest(error=error):
                    cli.write_text("#!" + sys.executable + "\nimport sys\nsys.stdin.read()\n" + code)
                    cli.chmod(0o700)
                    with self.assertRaisesRegex(CDPError, f"^{error}$"):
                        ego._invoke(str(cli), "return true;")

    def test_cdp_is_page_scoped_and_uses_known_target(self):
        conn = ego.EgoCDP(RT)
        result = {"result": {"value": ["https://example.com", "complete"]}}
        with patch.object(ego, "_invoke", return_value=result) as invoke:
            self.assertEqual(conn.location(conn.attach(TARGET)), ("https://example.com", "complete"))
        _, script = invoke.call_args.args
        self.assertIn("taskSpace(7)", script)
        self.assertIn('task.name !== "Agent J 19fd822279c854f8"', script)
        self.assertIn('p.targetId === "page-123"', script)
        self.assertIn('p.openedBy === "agent"', script)
        with self.assertRaises(CDPError):
            conn.call("Network.getAllCookies", session=TARGET)
        with self.assertRaises(CDPError):
            conn.attach("7:Some other task:p1:page-123")

    def test_only_clip_from_same_approved_document_can_reach_capture(self):
        conn = ego.EgoCDP(RT, qr_origins=["https://example.com"])
        clip = {"x": 0, "y": 5, "width": 100, "height": 100, "scale": 2}
        args = {"clip": clip, "format": "png", "captureBeyondViewport": False, "fromSurface": True}
        with patch.object(ego, "_invoke") as invoke:
            with self.assertRaises(CDPError):
                conn.call("Page.captureScreenshot", args, session=TARGET)
        invoke.assert_not_called()

        frame = {"origin": "https://example.com", "loader": "abc", "frame": "frame-1"}
        with patch.object(ego, "_invoke", side_effect=[frame, {"data": "fragment"}]) as invoke:
            self.assertTrue(conn.begin_qr(TARGET))
            self.assertEqual(conn.call("Page.captureScreenshot", args, session=TARGET), {"data": "fragment"})
        _, script = invoke.call_args.args
        self.assertEqual(script.count("await sameDocument()"), 2)
        self.assertIn("frame.loader === expected.loader", script)
        self.assertIn('await page.cdp("Page.captureScreenshot"', script)
        clip["width"] = 1000
        with self.assertRaises(CDPError):
            conn.call("Page.captureScreenshot", args, session=TARGET)

    def test_login_path_never_reads_qr_from_offsite_page(self):
        cards = browser_login.LoginCards.__new__(browser_login.LoginCards)
        cards.tabs = {}
        template = {"login_url": "https://example.com/login", "origins": ["https://example.com"],
                    "qr": {"selectors": ["#qr"]}}
        with (patch.object(ego, "EgoCDP") as factory,
              patch.object(browser_login, "capture_qr") as capture,
              patch.object(browser_login.time, "monotonic", side_effect=[0, 0, 60]),
              patch.object(browser_login.time, "sleep")):
            conn = factory.return_value
            conn.location.return_value = ("https://elsewhere.invalid/", "complete")
            result = cards._open_and_capture(RT, "example", template)
        self.assertEqual(result, {"state": "none", "reason": "page_not_ready"})
        conn.begin_qr.assert_not_called()
        capture.assert_not_called()
        conn.close.assert_called_once()

    def test_fresh_login_probe_stays_in_original_profile_and_closes_only_its_page(self):
        conn = ego.EgoCDP(RT)
        conn.bind_login(TARGET)
        with patch.object(ego, "_invoke", side_effect=[["p2", "fresh-456"], True]) as invoke:
            probe = conn.new_tab("about:blank")
            self.assertEqual(probe, "7:Agent J 19fd822279c854f8:p2:fresh-456")
            conn.close_tab(probe)
        _, create = invoke.call_args_list[0].args
        _, cleanup = invoke.call_args_list[1].args
        self.assertIn("taskSpace(7)", create)
        self.assertIn('p.targetId === "page-123"', create)
        self.assertIn("await task.newPage()", create)
        self.assertIn("taskSpace(7)", cleanup)
        self.assertIn('p.targetId === "fresh-456"', cleanup)
        self.assertIn("await page.close()", cleanup)
        self.assertNotIn("task.finish", cleanup)

    def test_fresh_login_reapplies_network_controls_in_navigation_invocation(self):
        conn = ego.EgoCDP(RT)
        with patch.object(ego, "_invoke", return_value={}) as invoke:
            conn.call("Network.setBypassServiceWorker", {"bypass": True}, session=TARGET)
            conn.call("Page.reload", {"ignoreCache": True}, session=TARGET)
        _, script = invoke.call_args.args
        self.assertLess(script.index("Network.setCacheDisabled"), script.index("Page.reload"))
        self.assertLess(script.index("Network.setBypassServiceWorker"), script.index("Page.reload"))

    def test_close_never_touches_page_merely_observed(self):
        conn = ego.EgoCDP(RT)
        with patch.object(ego, "_invoke") as invoke:
            conn.close_tab(TARGET)
        invoke.assert_not_called()

    def test_no_probe_occurs_without_owner_profile_binding(self):
        with (patch.object(ego, "_transport", return_value=(RT, None, True)),
              patch.object(ego, "binding_id", return_value=None),
              patch.object(ego, "_invoke") as invoke):
            self.assertIsNone(ego.runtime(object()))
            self.assertEqual(ego.setup(object())["reason"], "ego_profile_required")
        invoke.assert_not_called()

    def test_install_hints_delegate_to_agent_and_keep_phone_confirmation(self):
        for reason in ("ego_install", "ego_wsl_interop"):
            with self.subTest(reason=reason), patch.object(ego, "_state", return_value=(None, reason, False)):
                hint = ego.setup(object())["hint"]
                self.assertIn("可以直接让 Agent 代装 Ego Lite", hint)
                self.assertIn("https://lite.ego.app/", hint)
                self.assertIn("agentj browser connect", hint)
                self.assertNotIn("agentj browser setup", hint)
                self.assertIn("paired phone", hint)

    def test_connect_binds_only_exact_owner_selected_profile_and_does_not_echo_it(self):
        with tempfile.TemporaryDirectory() as folder:
            from agentj.state import State
            state = State(root=pathlib.Path(folder))
            with (patch.object(ego, "_transport", return_value=(RT, None, True)),
                  patch.object(ego, "_invoke", return_value="selected-id") as invoke):
                profile, reason = ego.resolve_profile(state, "Agent J work")
                self.assertEqual((profile, reason), ("selected-id", None))
                self.assertIsNone(ego.binding_id(state))
                ego.bind_profile(state, profile)
                self.assertIn('p.name === "Agent J work"', invoke.call_args.args[1])
                self.assertEqual(ego.binding_id(state), "selected-id")
                self.assertEqual(ego._profile_file(state).stat().st_mode & 0o777, 0o600)
            with patch.object(ego, "_transport", return_value=(RT, None, True)), patch.object(ego, "_invoke", return_value=None):
                self.assertEqual(ego.resolve_profile(state, "Ambiguous"), (None, "ego_profile_unknown"))
            self.assertEqual(ego.binding_id(state), "selected-id")
            ego.disconnect(state)
            self.assertIsNone(ego.binding_id(state))

    def test_new_space_explicitly_requests_and_verifies_approved_profile_before_navigation(self):
        conn = ego.EgoCDP(RT)
        with patch.object(ego, "_invoke", return_value=[7, "page-123"]) as invoke:
            conn.new_tab("https://example.com")
        _, script = invoke.call_args.args
        self.assertIn('{profileId: "approved-profile"}', script)
        self.assertLess(script.index('info?.profileId !== "approved-profile"'), script.index("Page.navigate"))

    def test_profile_revocation_blocks_an_already_prepared_qr_from_delivery(self):
        cards = browser_login.LoginCards.__new__(browser_login.LoginCards)
        cards.st = object()
        card = {"site": "bilibili", "ego_profile": "old-profile"}
        with patch.object(browser_login.browser, "settings", return_value={"enabled": True}):
            with patch.object(ego, "binding_id", return_value="new-profile"):
                self.assertFalse(cards._visible(card, object()))
            with patch.object(ego, "binding_id", return_value="old-profile"):
                self.assertTrue(cards._visible(card, object()))

    def test_disabled_ego_browser_blocks_pending_qr_and_stops_watcher(self):
        cards = browser_login.LoginCards.__new__(browser_login.LoginCards)
        cards.st = object()
        card = {"id": "existing", "site": "bilibili", "ego_profile": "approved"}
        cards.cards = {card["id"]: card}
        with (patch.object(browser_login, "POLL", 0),
              patch.object(browser_login.browser, "settings", return_value={"enabled": False}),
              patch.object(ego, "binding_id", return_value="approved"),
              patch.object(cards, "_tab") as tab, patch.object(cards, "_end") as end):
            self.assertFalse(cards.claim_delivery(object(), {"id": card["id"]}))
            asyncio.run(cards._watch(card))
        tab.assert_not_called()
        end.assert_called_once_with(card, "gone")

    def test_mac_uses_the_signed_helper_in_the_installed_app(self):
        with tempfile.TemporaryDirectory() as folder:
            app = pathlib.Path(folder) / "ego lite.app"
            helper = app / ego.MAC_HELPER
            helper.parent.mkdir(parents=True)
            helper.touch(mode=0o755)
            with (patch.object(ego, "MAC_APP", app),
                  patch.object(ego.subprocess, "run") as verify):
                verify.return_value.returncode = 1
                self.assertIsNone(ego._executable("macos"))
                verify.return_value.returncode = 0
                self.assertEqual(ego._executable("macos"), str(helper))
                args = verify.call_args.args[0]
                self.assertEqual(args[0], "/usr/bin/codesign")
                self.assertIn(f"-R={ego.MAC_REQUIREMENT}", args)
                helper.unlink()
                helper.symlink_to("/usr/bin/true")
                verify.reset_mock()
                self.assertIsNone(ego._executable("macos"))
                verify.assert_not_called()

    def test_windows_skips_untrusted_discovery_before_running_the_vendor_cli(self):
        shim = r"C:\Users\Example\ego-browser.exe"
        native = r"C:\Program Files\Ego Lite\ego-browser.exe"
        with patch.object(ego, "_output", side_effect=[shim + "\n" + native, ""]) as system:
            self.assertIsNone(ego._executable("wsl2"))
        self.assertEqual([call.args[0] for call in system.call_args_list], [
            [ego.WINDOWS_TOOLS + "where.exe", "ego-browser.exe"], ["/usr/bin/wslpath", "-u", native],
        ])

    def test_other_seats_do_not_resolve_or_execute_vendor_cli(self):
        with (patch("agentj.browser.settings", return_value={"enabled": True}),
              patch.object(ego.instance, "current", return_value=2), patch.object(ego, "_executable") as exe):
            self.assertIsNone(ego.runtime(object()))
            self.assertEqual(ego.setup(object())["reason"], "ego_instance")
        exe.assert_not_called()
