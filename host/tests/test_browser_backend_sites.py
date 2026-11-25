import _hermetic  # noqa: F401
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentj import browser, browser_sites
from agentj.state import State


class BrowserBackendSites(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.st = State(Path(directory.name) / "state")
        browser_sites.add(self.st, "youtube-studio")

    def test_previous_browser_sign_in_requires_a_new_check(self):
        data = browser_sites.load(self.st)
        rec = data["sites"]["youtube-studio"]
        rec.update(adapter_version=1, status="logged_in", reason="ok", checked_at=100)
        browser_sites._save(self.st, data)

        status = browser_sites.summary(self.st)[0]
        self.assertEqual((status["status"], status["checked_at"]), ("unknown", None))
        self.assertTrue(status["stale"])
        with patch.object(browser, "runtime", return_value={"backend": "system-cdp", "ws": "fake"}):
            entries = browser_sites.registry(self.st, now=101)
        self.assertEqual(entries[1]["status"], "unverified")

    def test_ego_checks_and_manual_open_never_use_a_websocket(self):
        rt = {"backend": "ego-lite", "headless": False, "profile_id": "one"}
        with (patch.object(browser, "backend", return_value="ego-lite"),
              patch("agentj.browser_ego.binding_id", return_value="one") as binding,
              patch.object(browser, "runtime", return_value=rt),
              patch.object(browser, "visible_runtime", return_value=rt),
              patch("agentj.browser_ego.EgoCDP") as adapter,
              patch("agentj.browser_ego.host_open") as open_tab,
              patch.object(browser_sites, "CDP") as ws,
              patch.object(browser_sites, "probe", return_value=("logged_in", "ok"))):
            result = browser_sites.check(self.st)
            self.assertEqual(result["sites"][0]["status"], "logged_in")
            adapter.assert_called_once_with(rt)
            adapter.return_value.close.assert_called_once_with()
            self.assertTrue(browser_sites.open_login(self.st, "youtube")["ok"])
            open_tab.assert_called_once_with(rt, browser_sites.templates()["youtube-studio"]["login_url"])
            ws.assert_not_called()
            binding.return_value = "two"
            status = browser_sites.summary(self.st)[0]
            self.assertEqual((status["status"], status["checked_at"]), ("unknown", None))

    def test_connect_proposal_needs_the_existing_owner_action(self):
        from agentj import first_run
        with (patch.object(browser, "backend", return_value="ego-lite"),
              patch.object(browser, "runtime", return_value=None),
              patch("agentj.browser_ego.resolve_profile", return_value=("chosen", None)) as resolve,
              patch("agentj.browser_ego.bind_profile") as bind):
            rec = first_run._blank(self.st)
            rec["items"]["browser"].update(state="waiting_local", reason="browser_not_installed")
            first_run._save(self.st, rec)
            with self.assertRaises(first_run.SetupError):
                first_run.mark(self.st, "browser", "adopt_browser", rec["revision"], "fixture-decision")

            self.assertEqual(browser.execute({"action": "connect"}, self.st)["result"], "confirm_on_phone")
            bind.assert_not_called()
            self.assertEqual(browser.confirm_adopt(self.st, "")["reason"], "no_offer")
            rec["items"]["browser"].update(reason="ego_profile_offer")
            rec["browser_source"] = browser.source(self.st)
            first_run._save(self.st, rec)
            signed = first_run.mark(self.st, "browser", "adopt_browser", rec["revision"], "fixture-decision")
            self.assertTrue(signed["ok"])
            self.assertNotIn("ego_offer", signed["action"])
            self.assertNotIn("ego_offer", signed["browser_source"])
            self.assertEqual(browser.confirm_adopt(self.st, "superseded-decision")["reason"], "no_offer")
            resolve.return_value = ("replacement", None)
            self.assertEqual(browser.confirm_adopt(self.st, "fixture-decision")["reason"], "browser_profile_changed")
            bind.assert_not_called()
            resolve.return_value = ("chosen", None)
            self.assertTrue(browser.confirm_adopt(self.st, "fixture-decision")["ok"])
            self.assertEqual(browser.confirm_adopt(self.st, "fixture-decision")["reason"], "no_offer")
            bind.assert_called_once_with(self.st, "chosen")

    def test_replaced_ego_offer_invalidates_the_phone_snapshot_revision(self):
        from agentj import first_run
        rec = first_run._blank(self.st)
        rec["browser_source"] = {"kind": "offer", "backend": "ego-lite", "ego_offer": "old"}
        revision = rec["revision"]

        def probe(st, item, ctx):
            ctx["browser_source"] = {"kind": "offer", "backend": "ego-lite", "ego_offer": "new"}
            return "waiting_local", "ego_profile_offer"

        with patch.object(first_run, "probe", side_effect=probe):
            first_run.run_checks(self.st, rec, ["browser"])
        self.assertGreater(rec["revision"], revision)
        first_run._save(self.st, rec)
        with self.assertRaises(first_run.SetupError):
            first_run.mark(self.st, "browser", "adopt_browser", revision, "fixture-decision")

    def test_older_ego_action_result_preserves_a_newer_pending_action(self):
        from agentj import first_run
        rec = first_run._blank(self.st)
        rec["items"]["browser"]["owner_decision_ref"] = "new-decision"
        rec["action"] = {"item": "browser", "choice": "adopt_browser", "state": "running", "ego_offer": "new"}
        first_run._save(self.st, rec)

        with (patch.object(browser, "backend", return_value="ego-lite"),
              patch.object(browser, "confirm_adopt", return_value={"ok": False, "reason": "no_offer"})):
            first_run.act(self.st, "browser", "adopt_browser", "old-decision")
        self.assertEqual(first_run.read(self.st)["action"], rec["action"])
