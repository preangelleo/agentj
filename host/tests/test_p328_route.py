"""P328 (Leo, 2026-10-16): who `agentj agent route` recommends first where Claude Code and Codex are not offered.

  mainland China, starting from zero → Volcano Ark Coding Plan, Lite first (same models as Pro, Pro = 5x the quota)
  Hong Kong / Macau / overseas        → a Kimi membership (billed in USD outside the mainland)
  already paying for one of them      → keep it
  a kimi.com key works on api.kimi.com AND api.kimi.ai — the old "keys are not shared" wording is gone everywhere.

Same wording as the plaza skill cn-coding-plans 1.0.1. No network: signals are passed in, the zone is patched."""
import _hermetic  # noqa: F401,I001
import json
import pathlib
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import ai_route, ark  # noqa: E402

BLOCKED = {"anthropic": "region", "openai": "region", "kimi": "ok"}
HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[3]


def _run(zone, signals=BLOCKED):
    with mock.patch.object(ai_route, "_zone", return_value=zone), mock.patch.object(ai_route, "_locale", return_value=None):
        return ai_route.run(None, environ={}, signals=signals, use_platform=False)


class Primary(unittest.TestCase):
    def test_mainland_from_zero_is_ark_lite(self):
        r = _run("Asia/Shanghai")
        self.assertEqual((r["route"], r["primary"]), ("kimi", "ark"))
        self.assertEqual(r["ark"]["start_tier"], "Lite")
        self.assertEqual([t["name"] for t in r["ark"]["tiers"]], ["Lite", "Pro"])
        self.assertEqual(r["ark"]["model"], "ark-coding-plan/doubao-seed-2.1-pro")
        zh, en = ai_route.render(r, "zh"), ai_route.render(r, "en")
        self.assertIn("中国大陆、从零开始：首推 **火山方舟 Coding Plan**，先买 **Lite**", zh)
        self.assertIn("和 Pro 能力一样，只差 5 倍用量", zh)
        self.assertIn("first choice **Volcano Ark Coding Plan** — buy **Lite** first", en)
        self.assertIn("Pro only gives 5x the quota", en)
        for text in (zh, en):
            self.assertIn("agentj provider ark", text)
            self.assertIn("ark-coding-plan/doubao-seed-2.1-pro", text)
            self.assertIn("¥40", text)
            self.assertLess(text.index("agentj provider ark"), text.index(ai_route.data()["invite_url"]),
                            "mainland: Ark's steps come before Kimi's")
            self.assertEqual(text.count("`agentj provider ark`"), 1, "no duplicate Ark line in the alternatives")

    def test_hong_kong_macau_is_kimi(self):
        for zone in ("Asia/Hong_Kong", "Asia/Macau"):
            r = _run(zone)
            self.assertEqual((r["route"], r["primary"]), ("kimi", "kimi"), zone)
            zh, en = ai_route.render(r, "zh"), ai_route.render(r, "en")
            self.assertIn("香港 / 海外：**Kimi 会员**", zh)
            self.assertIn("海外访问按美元收费", zh)
            self.assertIn("Hong Kong / overseas: a **Kimi membership**", en)
            self.assertIn("billed in USD", en)
            # P356: P335's later rule (Jarvis 10-16 17:1x) — Hong Kong / Macau list Ark too, after Kimi and Bailian
            self.assertLess(zh.index("阿里百炼 Token Plan 个人版（中国大陆"), zh.index(ai_route.ARK_HEAD[0]), "Hong Kong: Ark comes last")
            self.assertLess(zh.index(ai_route.KIMI_HEAD[0]), zh.index("阿里百炼 Token Plan 个人版（中国大陆"))

    def test_unknown_region_names_both_with_where(self):
        r = _run(None)
        self.assertIsNone(r["primary"])
        zh = ai_route.render(r, "zh")
        self.assertIn(ai_route.ARK_HEAD[0], zh)
        self.assertIn(ai_route.KIMI_HEAD[0], zh)
        self.assertIn("中国大陆首推", zh)
        self.assertIn("香港 / 海外首推", zh)

    def test_already_on_one_keeps_it(self):
        for zone in ("Asia/Shanghai", "Asia/Hong_Kong", None):
            r = _run(zone)
            self.assertIn("已经有其中某一家的：继续用那一家", ai_route.render(r, "zh"))
            self.assertIn("Already paying for one of them: keep using it", ai_route.render(r, "en"))

    def test_official_route_recommends_no_plan(self):
        r = _run("Asia/Shanghai", {"anthropic": "ok", "openai": "region", "kimi": "ok"})
        self.assertEqual(r["route"], "official")
        self.assertIsNone(r["primary"])
        self.assertIsNone(r["ark"])
        self.assertNotIn("火山方舟", ai_route.render(r, "zh"))

    def test_unsure_still_offers_the_rule(self):
        r = _run("Asia/Shanghai", {"anthropic": "unknown", "openai": "unknown", "kimi": "ok"})
        self.assertEqual(r["route"], "kimi")      # mainland zone, nothing measured: the hint
        r = _run("Europe/Berlin", {"anthropic": "unknown", "openai": "unknown", "kimi": "ok"})
        self.assertEqual(r["route"], "unsure")
        self.assertIn("按下面选", ai_route.render(r, "zh"))
        self.assertIn(ai_route.PICK[0][0], ai_route.render(r, "zh"))

    def test_json_carries_primary_and_ark(self):
        r = _run("Asia/Shanghai")
        got = json.loads(json.dumps(r, ensure_ascii=False))
        self.assertEqual(got["primary"], "ark")
        self.assertEqual(got["ark"]["key_env"], "ARK_CODING_PLAN_API_KEY")
        self.assertEqual(got["ark"]["harness_options"], ["opencode", "claude"])


class KimiBothAddresses(unittest.TestCase):
    def test_route_says_both_addresses_work(self):
        r = _run("Asia/Hong_Kong")
        zh, en = ai_route.render(r, "zh"), ai_route.render(r, "en")
        self.assertIn("api.kimi.com 和 api.kimi.ai 两个地址都能用", zh)
        self.assertIn("works on both api.kimi.com and api.kimi.ai", en)
        self.assertNotIn("不通用", zh)
        self.assertNotIn("different keys", en)


class ArkLiteWording(unittest.TestCase):
    def test_k3_is_suggested_for_pro_not_pro_only(self):
        d = ark.data()
        self.assertNotIn("pro_only", d)
        self.assertIn("kimi-k3", d["pro_suggested"])
        for lang in ("zh", "en"):
            note = ark.model_note("ark-coding-plan/kimi-k3", lang)
            self.assertIn("Lite", note)
        self.assertNotIn("Pro 套餐里", ark.model_note("ark-coding-plan/kimi-k3", "zh"))
        self.assertNotIn("Pro only", ark.model_note("ark-coding-plan/kimi-k3", "en"))
        self.assertNotIn("Pro 套餐", d["block"]["models"]["kimi-k3"]["name"])


class Docs(unittest.TestCase):
    """The public texts carry the same rule; the wrong Kimi wording is gone."""
    FILES = ["documentation/product/install.md",
             "agentjarvis/site/content/docs/17-setup-agents/en.md",
             "agentjarvis/site/content/docs/17-setup-agents/zh.src.md",
             "agentjarvis/site/content/docs/17-setup-agents/zh.md",
             "agentjarvis/site/content/docs/25-first-run/en.md",
             "agentjarvis/site/content/docs/25-first-run/zh.src.md",
             "agentjarvis/site/content/docs/25-first-run/zh.md",
             "agentjarvis/site/content/docs/29-switch-harness/en.md",
             "agentjarvis/site/content/docs/29-switch-harness/zh.src.md",
             "agentjarvis/site/content/docs/29-switch-harness/zh.md",
             # the built-in skills, found from this file (the export rewrite must stay a no-op on host/)
             str(HERE.parents[1] / "agentj/skills/agentj-first-run/SKILL.md"),
             str(HERE.parents[1] / "agentj/skills/agentj-switch-harness/SKILL.md")]

    def test_no_kimi_keys_not_shared(self):
        for rel in self.FILES:
            p = ROOT / rel
            if not p.is_file():
                continue          # private docs are not in the public export
            t = p.read_text(encoding="utf-8")
            for bad in ("两边账号不通用", "两套 key", "key 不通用", "different keys", "separate accounts", "Pro plan only",
                        "只在 Pro 套餐里", "stay the first recommendations"):
                self.assertNotIn(bad, t, (rel, bad))

    def test_mainland_ark_first(self):
        pairs = (("documentation/product/install.md", "buy **Lite** first"),
                 ("agentjarvis/site/content/docs/17-setup-agents/en.md", "Buy Lite first"),
                 ("agentjarvis/site/content/docs/17-setup-agents/zh.src.md", "先买 Lite"),
                 ("agentjarvis/site/content/docs/17-setup-agents/zh.md", "先买 Lite"))
        for rel, s in pairs:
            p = ROOT / rel
            if p.is_file():
                self.assertIn(s, p.read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
