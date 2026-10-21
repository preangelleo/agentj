"""P423 #146: OpenCode's agent.model left on a model whose provider nobody set up (a customer had
deepinfra/google/gemma-4-31B-it) → serve answers `UnknownError "Model not found: <p>/<m>"`, which P60 read as `no_key`
(「Key 不可用」, a key card). Now: a provider in none of the owner's set-up places is `model_missing` — its own bilingual line
(never the key card / auth rescue), the existing /model card with the working models right under it, doctor fails on it,
and the switch-harness probe line "Model not found" is no longer 「多半是没登录或 token 失效」. No network, no real keys."""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import agent_opencode as oc  # noqa: E402
from agentj import switch_harness as sw  # noqa: E402

GHOST = "deepinfra/google/gemma-4-31B-it"
NOT_FOUND = {"message": f"Model not found: {GHOST}. Did you mean: google/gemma-4-31B-it"}
PROV = {"providers": [
    {"id": "zhipuai", "name": "Zhipu AI", "models": {"glm-5.3": {"name": "GLM-5.3"}}},
    {"id": "deepinfra", "name": "DeepInfra", "models": {"google/gemma-4-31B-it": {"name": "Gemma"}}},
    {"id": "opencode", "name": "OpenCode Zen", "models": {"big-pickle": {}}}],
    "connected": ["zhipuai", "opencode"]}


class Classify(unittest.TestCase):
    def test_ghost_provider_is_model_missing_not_no_key(self):
        conn = ["opencode", "zhipuai"]
        self.assertEqual(oc.provider_error_reason("UnknownError", NOT_FOUND, conn, "deepinfra", {"zhipuai"}), "model_missing")
        self.assertEqual(oc.provider_error_reason("ProviderModelNotFoundError", {}, conn, "deepinfra", set()), "model_missing")
        # set up somewhere but not in serve's view → still P60's no_key; unknown set-up list → P60's rule unchanged
        self.assertEqual(oc.provider_error_reason("UnknownError", NOT_FOUND, conn, "deepinfra", {"deepinfra"}), "no_key")
        self.assertEqual(oc.provider_error_reason("UnknownError", NOT_FOUND, conn, "deepinfra"), "no_key")
        # connected provider, unknown model → "model"; other classes untouched
        self.assertEqual(oc.provider_error_reason("UnknownError", NOT_FOUND, ["deepinfra"], "deepinfra", set()), "model")
        self.assertEqual(oc.provider_error_reason("APIError", {"statusCode": 401, "message": "x"}, conn, "deepinfra", set()),
                         "login")
        self.assertIn("model_missing", oc.RESTART_AFTER)
        zh, en = oc.PROVIDER_NOTES["model_missing"].split(" / ", 1)
        self.assertNotIn("Key 不可用", zh)
        self.assertIn("不是 Key 的问题", zh)
        self.assertIn("not a key problem", en)

    def test_set_up_providers_reads_names_only(self):
        with tempfile.TemporaryDirectory() as d:
            data, conf = pathlib.Path(d, "data", "opencode"), pathlib.Path(d, "conf", "opencode")
            data.mkdir(parents=True)
            conf.mkdir(parents=True)
            (data / "auth.json").write_text(json.dumps({"anthropic": {"type": "api", "key": "test-fixture-value"}}))
            (conf / "opencode.json").write_text(json.dumps({"provider": {"myrelay": {"npm": "x"}}}))
            env = {"XDG_DATA_HOME": str(data.parent), "XDG_CONFIG_HOME": str(conf.parent),
                   "OPENCODE_CONFIG_CONTENT": json.dumps({"provider": {"inline": {}}})}
            with mock.patch.dict(os.environ, env):
                got = oc.set_up_providers(["fromserve", 3])
            self.assertEqual(got, {"anthropic", "myrelay", "inline", "fromserve"})
            (data / "auth.json").write_text("not json")
            with mock.patch.dict(os.environ, {**env, "OPENCODE_CONFIG_CONTENT": "{bad"}):
                self.assertEqual(oc.set_up_providers(), {"myrelay"})


def _agent(owned=True, lang="zh", persist=True):
    a = object.__new__(oc.OpenCodeAgent)
    a.cfg = {"model": GHOST, "language": lang, "session_mode": "independent" if owned else "shared"}
    a.sid, a.connected_at_start, a.provider_fail_noted, a.restart_after_turn = "ses_1", ["opencode", "zhipuai"], False, False
    a.config_providers, a.persist = [], persist
    a.notices, a.cards = [], []
    a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()), agent_notice=a.notices.append,
                             auth_rescue=mock.Mock(), cmd_turn=mock.Mock(return_value=7),
                             cmd_card=lambda name, res, by=None, turn=None: a.cards.append((name, res, turn)))
    with mock.patch("agentj.provider_runtime.configured", return_value={}):
        a._set_models(PROV)
    return a


def _fail(a):
    with mock.patch.object(oc, "set_up_providers", return_value={"zhipuai", "opencode"}):
        a.on_event({"type": "session.error", "properties": {"sessionID": "ses_1", "error": {
            "name": "UnknownError", "data": NOT_FOUND}}})


class Session(unittest.TestCase):
    def test_usable_models_are_the_connected_providers(self):
        a = _agent()
        ids = [m["id"] for m in a._usable_models()]
        self.assertEqual(ids, ["zhipuai/glm-5.3", "opencode/big-pickle"])
        a.cfg["model"] = "zhipuai/glm-5.3"
        self.assertEqual([m["id"] for m in a._usable_models()], ["opencode/big-pickle"])

    def test_owned_ghost_model_notice_and_model_card(self):
        for lang in ("zh", "en"):
            a = _agent(lang=lang)
            _fail(a)
            self.assertEqual(a.last_provider_fail["reason"], "model_missing")
            self.assertTrue(a.restart_after_turn)
            a.host.auth_rescue.signal.assert_not_called()          # never the key card
            text = a.notices[0]
            self.assertIn(GHOST, text)
            self.assertNotIn("Key 不可用", text)
            self.assertNotIn("API Key 不可用", text)
            self.assertIn("不是 Key 失效" if lang == "zh" else "not an invalid key", text)
            self.assertIn("模型和 API Key" if lang == "zh" else "Models & API keys", text)
            self.assertEqual(len(a.cards), 1)
            name, res, turn = a.cards[0]
            self.assertEqual((name, turn, res.kind), ("model", 7, "info"))
            self.assertEqual([m["id"] for m in res.models], ["zhipuai/glm-5.3", "opencode/big-pickle"])
            a.host.cmd_turn.assert_called_once_with(None, "model")
            _fail(a)                                                # one notice / card per turn
            self.assertEqual((len(a.notices), len(a.cards)), (1, 1))

    def test_no_card_when_attached_scheduled_or_nothing_works(self):
        for a in (_agent(owned=False), _agent(persist=False)):
            _fail(a)
            self.assertEqual(a.last_provider_fail["reason"], "model_missing")
            self.assertEqual(a.cards, [])
        a = _agent()
        a.usable_models = []
        _fail(a)
        self.assertEqual(a.cards, [])
        self.assertNotIn("点下面", a.notices[0])
        self.assertIn("模型和 API Key", a.notices[0])

    def test_set_up_provider_keeps_the_key_path(self):
        a = _agent()
        with mock.patch.object(oc, "set_up_providers", return_value={"deepinfra"}), \
                mock.patch("agentj.provider_runtime.key_name", return_value=None):
            a.on_event({"type": "session.error", "properties": {"sessionID": "ses_1", "error": {
                "name": "UnknownError", "data": NOT_FOUND}}})
        self.assertEqual(a.last_provider_fail["reason"], "no_key")
        self.assertEqual(a.cards, [])


class Probe(unittest.TestCase):
    def test_model_not_found_probe_line_is_not_a_login_hint(self):
        line = f"Error: Model not found: {GHOST}."
        for lang, want, never in (("zh", "找不到所选的模型", "没登录"), ("en", "cannot find the selected model", "not signed in")):
            text = sw.reason_text("native_failed", "opencode", lang, line=line)
            self.assertIn(want, text)
            self.assertNotIn(never, text)
            self.assertIn(GHOST, text)                              # the harness's own line is still shown
        self.assertIn("没登录", sw.reason_text("native_failed", "opencode", "zh", line="401 Unauthorized"))


if __name__ == "__main__":
    unittest.main()
