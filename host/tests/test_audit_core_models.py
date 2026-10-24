"""P497: route and entitlement boundaries for the officially verified Kimi catalog."""
import _hermetic  # noqa: F401
import unittest
from agentj import provider_profiles as profiles


class Models(unittest.TestCase):
    def test_native_ids_do_not_cross_coding_and_platform_routes(self):
        all_ids = ["kimi-k3.1-pro", "k3.1-pro", "kimi-k3", "k3", "kimi-for-coding", "kimi-k2.7-code"]
        self.assertEqual(profiles.model_candidates("kimi-code-cn", all_ids), ["k3.1-pro", "k3", "kimi-for-coding"])
        self.assertEqual(profiles.model_candidates("kimi-api-global", all_ids), ["kimi-k3.1-pro", "kimi-k3"])
        self.assertEqual(profiles.model_candidates("opencode-go", all_ids), ["kimi-k3.1-pro", "kimi-k3", "kimi-k2.7-code"])

    def test_missing_or_restricted_model_no_speculative_default(self):
        self.assertEqual(profiles.model_candidates("kimi-code-cn", ["kimi-for-coding"]), ["kimi-for-coding"])
        for missing in (None, [], {}, "kimi-k3.1-pro"):
            self.assertEqual(profiles.model_candidates("opencode-go", missing), [])
        self.assertEqual(profiles.model_candidates("unknown", ["kimi-k3.1-pro"]), [])
        self.assertEqual(profiles.model_candidates("kimi-api-cn", ["k3.1-pro"]), [])

    def test_regions_endpoints_and_no_response_preset(self):
        routes = profiles.VERIFIED_MODEL_ROUTES
        self.assertIn("moonshot.cn", routes["kimi-api-cn"]["base_url"])
        self.assertIn("moonshot.ai", routes["kimi-api-global"]["base_url"])
        self.assertIn("api.kimi.com", routes["kimi-code-cn"]["base_url"])
        self.assertIn("api.kimi.ai", routes["kimi-code-global"]["base_url"])
        self.assertTrue(all(r["api"] == "chat-completions" for r in routes.values()))
        self.assertNotIn("opencode-go", profiles.PRESETS)
