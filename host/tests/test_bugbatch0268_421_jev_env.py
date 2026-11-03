"""#421: layer 2 (support feedback / plaza gates) must read the Jev key the same way doctor does — from the shell OR the
Agent J service environment file — and the refusal must name the key of the chosen route, not a hard-coded OPENROUTER."""
import _hermetic  # noqa: F401
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import preferences, privacy, provider_runtime

DRAFT = {"body": "the install step failed at the venv"}
KEYS = ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY")


class Layer2ServiceEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.envfile = self.tmp / "agentj.env"
        clean = {k: v for k, v in os.environ.items() if k not in KEYS}
        for p in (mock.patch.dict(os.environ, clean, clear=True),
                  mock.patch.object(provider_runtime, "env_path", lambda: str(self.envfile)),
                  mock.patch.object(provider_runtime, "configured", lambda: [])):
            p.start(); self.addCleanup(p.stop)

    def mode(self, m):
        p = mock.patch.object(preferences, "effective", lambda st: {"jev": {"mode": m}})
        p.start(); self.addCleanup(p.stop)

    def test_key_only_in_service_env_file_is_used(self):
        self.mode("typesafe")
        self.envfile.write_text("TYPESAFE_API_KEY=fixture-typesafe\n")
        seen = []

        def fake(url, headers, body, timeout):
            seen.append((url, headers.get("Authorization")))
            return 200, json.dumps({"answers": {"exposure": {"noul": 0.01}}}).encode()
        v = privacy.layer2(DRAFT, env=None, transport=fake, gate="feedback")
        self.assertEqual(v.status, "ok", v.reason)
        self.assertEqual(seen, [("https://api.typesafe.ai/v1/systemone", "Bearer fixture-typesafe")])

    def test_regression_old_behaviour_would_have_refused(self):
        # an explicit env mapping is still honoured as-is (callers/tests that pass one keep their semantics)
        self.mode("typesafe")
        self.envfile.write_text("TYPESAFE_API_KEY=fixture-typesafe\n")
        v = privacy.layer2(DRAFT, env={}, transport=lambda *a: self.fail("no key → no call"), gate="feedback")
        self.assertEqual(v.status, "unavailable")

    def test_reason_names_the_chosen_route_key(self):
        self.mode("typesafe")
        v = privacy.layer2(DRAFT, env=None, transport=lambda *a: self.fail("no key → no call"), gate="plaza")
        self.assertEqual(v.status, "unavailable")
        self.assertIn("TYPESAFE_API_KEY", v.reason)
        self.assertNotIn("OPENROUTER", v.reason)
        self.assertIn("jev.mode=typesafe", v.reason)

    def test_reason_without_a_route_names_both_keys(self):
        self.mode("")
        v = privacy.layer2(DRAFT, env=None, transport=lambda *a: self.fail("no key → no call"))
        self.assertEqual(v.status, "unavailable")
        for k in KEYS:
            self.assertIn(k, v.reason)


if __name__ == "__main__":
    unittest.main()
