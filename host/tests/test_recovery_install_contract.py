"""Unchanged historical pure Schema/Plan cases; integration cases remain unrecovered, see REPORT.md."""
import unittest
from agentj import setup_ops

class Schema(unittest.TestCase):
    GOOD = {"sys.check": {}, "ai.plan": {}, "ai.install": {"cli": "claude", "source": "mirror"}, "ai.key": {"provider": "agentsrelay"},
            "ai.login": {"cli": "codex"}, "ai.probe": {"cli": "claude", "model": "claude-opus-5-5"},
            "ai.connect": {"cli": "opencode", "model": "zhipuai/glm-5.3"}, "browser.setup": {}, "sys.repair_hint": {"issue": "linger"}}


    def test_every_op_accepts_its_schema_and_nothing_else(self):
        self.assertEqual(set(self.GOOD), set(setup_ops.OPS))
        for op, p in self.GOOD.items():
            self.assertEqual(setup_ops.validate(op, p), p, op)
            for evil in ("cmd", "command", "argv", "path", "dest", "url", "shell", "script", "env"):
                with self.assertRaises(setup_ops.OpError) as e:
                    setup_ops.validate(op, {**p, evil: "rm -rf ~"})
                self.assertEqual(e.exception.code, "unknown_field", (op, evil))


    def test_values_are_choices_never_free_text(self):
        bad = [("ai.install", {"cli": "bash"}), ("ai.install", {"cli": "claude", "source": "https://evil.example"}),
               ("ai.key", {"provider": "anthropic; cat ~/.ssh/id_rsa"}), ("ai.login", {"cli": "opencode"}),
               ("ai.probe", {"cli": "claude", "model": "x y"}), ("ai.probe", {"cli": "claude", "model": ["a"]}),
               ("ai.connect", {"cli": "opencode", "model": "glm-5.3"}), ("ai.connect", {"cli": {"k": "v"}}),
               ("sys.repair_hint", {"issue": "apt-get install x"})]
        for op, p in bad:
            with self.assertRaises(setup_ops.OpError) as e:
                setup_ops.validate(op, p)
            self.assertEqual(e.exception.code, "bad_param", (op, p))
        for op, p, code in (("rm", {}, "unknown_op"), (None, {}, "unknown_op"), ("ai.key", {}, "missing_param"),
                            ("ai.key", [], "shape"), ("ai.key", {"provider": "openai", "x" * 600: 1}, "shape")):
            with self.assertRaises(setup_ops.OpError) as e:
                setup_ops.validate(op, p)
            self.assertEqual(e.exception.code, code, (op, code))


class Plan(unittest.TestCase):
    def check(self, a, o, clis=()):
        return {"region": {"anthropic": a, "openai": o},
                "clis": [{"name": n, "installed": n in [c[0] for c in clis], "logged_in": dict(clis).get(n)} for n in setup_ops.CLIS]}


    def test_hong_kong_like_recommends_agents_relay_with_opus(self):
        for a, o in (("403", "403"), ("000", "000"), ("403", "000")):
            p = setup_ops.plan(self.check(a, o))
            self.assertEqual((p["route"], p["cli"], p["model"]), ("relay", "claude", "claude-opus-5-5"))
            self.assertEqual([s["op"] for s in p["steps"]], ["ai.install", "ai.key", "ai.probe", "ai.connect"])
            self.assertEqual(p["steps"][1]["params"], {"provider": "agentsrelay"})
            for s in p["steps"]:
                setup_ops.validate(s["op"], s["params"])      # every recommended step is a valid op call


    def test_official_reachable_and_reuse(self):
        p = setup_ops.plan(self.check("401", "000"))
        self.assertEqual((p["route"], p["cli"]), ("official", "claude"))
        self.assertIn("ai.login", [s["op"] for s in p["steps"]])
        self.assertEqual(setup_ops.plan(self.check("403", "401"))["cli"], "codex")
        p = setup_ops.plan(self.check("403", "403", clis=[("codex", True)]))
        self.assertEqual((p["route"], p["cli"], [s["op"] for s in p["steps"]]), ("reuse", "codex", ["ai.probe", "ai.connect"]))
        p = setup_ops.plan(self.check("401", "401", clis=[("claude", None)]))       # installed but not signed in
        self.assertEqual((p["route"], [s["op"] for s in p["steps"]][0]), ("official", "ai.login"))
