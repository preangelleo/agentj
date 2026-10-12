"""P163: a failed provider check tells the owner "the key may be incomplete" when the key is clearly shorter than its
provider issues — from the length alone; the key itself never appears in any result, message, log or output.

- keyhint: provider recognition (address, public prefix, key name), thresholds.
- provider_profiles: probe failure → key_length_hint 'short_for_provider' + the bilingual sentence; success → no hint.
- elevate: a secret card whose live check fails → elev_done carries key_hint "short"; the CLI report prints KEY_HINT.
"""
import _hermetic  # noqa: F401,I001
import io
import json
import pathlib
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentj import elevate, keyhint  # noqa: E402
from agentj import provider_profiles as profiles  # noqa: E402

CUT = "sk-proj-" + "A" * 10          # 18 characters: the support case (st_3ahq)
FULL_OPENAI = "sk-proj-" + "B" * 156  # 164


class KeyHint(unittest.TestCase):
    def test_provider_recognised(self):
        self.assertEqual(keyhint.provider_of(url="https://api.anthropic.com/v1"), "anthropic")
        self.assertEqual(keyhint.provider_of(url="https://eu.openrouter.ai/api/v1"), "openrouter")
        self.assertEqual(keyhint.provider_of(value=b"sk-ant-api03-xyz"), "anthropic")
        self.assertEqual(keyhint.provider_of(value="sk-or-v1-xyz"), "openrouter")
        self.assertEqual(keyhint.provider_of(name="AGENTSRELAY_TEST_OPENAI_KEY"), "agentsrelay")
        self.assertEqual(keyhint.provider_of(name="OPENAI_API_KEY"), "openai")
        self.assertEqual(keyhint.provider_of(name="GEMINI_API_KEY"), "google")
        self.assertIsNone(keyhint.provider_of(name="MY_TOKEN", url="https://provider.example/v1", value="abc"))
        self.assertIsNone(keyhint.provider_of(name="NOPENAIX_KEY"))          # whole words only

    def test_thresholds(self):
        self.assertTrue(keyhint.looks_short(18))                             # shorter than any provider's
        self.assertTrue(keyhint.looks_short(51, url="https://api.anthropic.com/v1"))
        self.assertFalse(keyhint.looks_short(108, url="https://api.anthropic.com/v1"))
        self.assertFalse(keyhint.looks_short(51, name="OPENAI_API_KEY"))     # legacy sk- keys are 51
        self.assertFalse(keyhint.looks_short(30, url="https://provider.example/v1"))   # unknown provider: no guess
        self.assertTrue(keyhint.looks_short(40, url="https://agentsrelay.net/v1"))
        self.assertFalse(keyhint.looks_short(67, url="https://agentsrelay.net/v1"))


class Profiles(unittest.TestCase):
    def _fail(self, c, key):
        error = urllib.error.HTTPError("https://x.invalid", 401, "PRIVATE", {}, None)
        with patch("agentj.provider_runtime.public_url", return_value=c["base_url"] + "/models"), \
                patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(profiles.ProbeError) as caught:
                profiles.probe(c, key)
        return caught.exception

    def test_short_for_provider(self):
        c = {"base_url": "https://api.anthropic.com/v1", "api": "anthropic", "model": "m"}
        e = self._fail(c, "sk-ant-api03-" + "x" * 40)
        self.assertEqual(e.key_length_hint, "short_for_provider")
        self.assertIsNone(self._fail(c, "sk-ant-api03-" + "x" * 95).key_length_hint)

    def test_execute_result_has_the_sentence_never_the_key(self):
        key = "sk-ant-api03-" + "Q" * 40
        e = profiles.ProbeError("auth", profiles.key_length_hint(key, "https://api.anthropic.com/v1"))
        with patch.object(profiles, "private_dir"), patch("os.open", side_effect=e):
            out = profiles.execute({"action": "use", "id": "p1"})
        self.assertEqual(out["reason"], "provider_probe_auth")
        self.assertEqual(out["key_length_hint"], "short_for_provider")
        self.assertEqual(out["key_hint"], keyhint.HINT)
        self.assertNotIn("QQQQ", json.dumps(out, ensure_ascii=False))

    def test_unknown_provider_normal_length_no_hint(self):
        e = profiles.ProbeError("auth", profiles.key_length_hint("x" * 32, "https://provider.example/v1"))
        with patch.object(profiles, "private_dir"), patch("os.open", side_effect=e):
            out = profiles.execute({"action": "use", "id": "p1"})
        self.assertNotIn("key_hint", out)
        self.assertNotIn("key_length_hint", out)


class SecretCard(unittest.TestCase):
    def card(self, **kw):
        return {"id": "a" * 32, "kind": "secret", "name": "OPENAI_API_KEY", "dest": "env:/x#OPENAI_API_KEY",
                "verify_url": "https://api.openai.com/v1/models", "verify_header": "Authorization: Bearer {value}", **kw}

    def test_failed_check_short_key(self):
        with patch.object(elevate, "verify_secret", return_value={"verify": "fail", "detail": "HTTP 401"}):
            res = elevate.Elevator._save(object.__new__(elevate.Elevator), self.card(verify_before_write=True),
                                       bytearray(CUT.encode()))
        self.assertEqual(res, {"result": "failed", "why": "verify", "detail": "HTTP 401", "key_hint": "short"})

    def test_failed_check_full_key_no_hint(self):
        with patch.object(elevate, "verify_secret", return_value={"verify": "fail", "detail": "HTTP 401"}):
            res = elevate.Elevator._save(object.__new__(elevate.Elevator), self.card(verify_before_write=True),
                                       bytearray(FULL_OPENAI.encode()))
        self.assertNotIn("key_hint", res)

    def test_no_live_check_no_hint(self):
        self.assertEqual(elevate._key_hint({"name": "OPENAI_API_KEY"}, bytearray(b"short")), {})

    def test_cli_report_prints_hint_not_key(self):
        res = {"result": "failed", "why": "verify", "detail": "HTTP 401", "key_hint": "short"}
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            elevate._secret_report(res, False)
        self.assertIn("KEY_HINT:", err.getvalue())
        self.assertIn("复制不完整", err.getvalue())


if __name__ == "__main__":
    unittest.main()
