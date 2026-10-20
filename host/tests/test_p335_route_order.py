"""P335 (Leo, 2026-10-16): the code says what the site's /docs/models/ says — where Claude Code and Codex are not offered,
mainland China gets the Volcano Ark Coding Plan first (Lite to start), Hong Kong / Macau get Kimi first; a kimi.com key is
no longer called "not interchangeable" with kimi.ai (measured: it answers on both); Ark only *suggests* kimi-k3 for Pro."""
import _hermetic  # noqa: F401,I001
import pathlib
import re
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import ai_route, ark, first_run  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
AJ = pathlib.Path(__file__).resolve().parents[2]   # agentjarvis/ (the public tree has host/ and web/ at its root)
BLOCKED = {"anthropic": "region", "openai": "region", "kimi": "ok"}


def _res(zone):
    with mock.patch.object(ai_route, "_zone", return_value=zone), mock.patch.object(ai_route, "_locale", return_value=None):
        return ai_route.run(None, environ={}, signals=BLOCKED, use_platform=False)


class Order(unittest.TestCase):
    def test_mainland_lists_ark_first(self):
        for zone in ("Asia/Shanghai", None):            # no zone known: the mainland order (most blocked hosts are there)
            r = _res(zone)
            self.assertEqual(r["route"], "kimi")       # the route keeps its name (first-run journals store it)
            self.assertEqual(r["ark"]["model"], "ark-agent-plan/doubao-seed-2.1-pro")     # P395: the Agent Plan
            for lang, ark_head, kimi_head in (("zh", "火山方舟 Agent Plan（中国大陆首推", "Kimi 会员（香港"),
                                              ("en", "Volcano Ark Agent Plan (first choice", "Kimi membership (first")):
                text = ai_route.render(r, lang)
                self.assertLess(text.index(ark_head), text.index(kimi_head), (zone, lang))
                self.assertIn("agentj provider ark", text)
                self.assertIn("Medium", text)

    def test_hong_kong_lists_kimi_first(self):
        r = _res("Asia/Hong_Kong")
        for lang, ark_head, kimi_head in (("zh", "火山方舟 Agent Plan（中国大陆首推", "Kimi 会员（香港"),
                                          ("en", "Volcano Ark Agent Plan (first choice", "Kimi membership (first")):
            text = ai_route.render(r, lang)
            self.assertLess(text.index(kimi_head), text.index(ark_head), lang)
            self.assertIn(ai_route.DISCLOSURE[lang], text)

    def test_ark_listed_once(self):
        text = ai_route.render(_res("Asia/Shanghai"), "zh")
        self.assertEqual(text.count("agentj provider ark --harness claude"), 1)
        self.assertFalse(any(a["how"] == "agentj provider ark" for a in ai_route.alternatives("CN")))

    def test_official_route_names_no_plan(self):
        with mock.patch.object(ai_route, "_zone", return_value="Asia/Shanghai"):
            r = ai_route.run(None, environ={}, signals={"anthropic": "ok", "openai": "ok", "kimi": "ok"}, use_platform=False)
        self.assertIsNone(r["ark"])
        self.assertNotIn("方舟", ai_route.render(r, "zh"))

    def test_first_run_texts_name_ark(self):
        for reason in ("no_agent_kimi", "agent_not_running_kimi"):
            zh, en = first_run.FACT[reason]
            self.assertIn("火山方舟", zh); self.assertIn("Volcano Ark", en)
            self.assertIn("{kimi}", zh); self.assertIn("{kimi}", en)          # the invite link + disclosure slot stays


class Facts(unittest.TestCase):
    def test_kimi_k3_is_suggested_for_pro_not_limited(self):
        self.assertEqual(ark.data()["pro_suggested"], ["kimi-k3"])
        self.assertNotIn("pro_only", ark.data())
        for lang in ("zh", "en"):
            note = ark.model_note("ark-coding-plan/kimi-k3", lang)
            self.assertIn("Pro", note)
            self.assertNotRegex(note, r"只在 Pro|Pro (plan )?only")

    def test_no_not_interchangeable_claims_left(self):
        """kimi.com vs kimi.ai keys: measured 10-16, a kimi.com key answers on both — no surface may say otherwise."""
        bad = re.compile(r"key 不通用|账号[^。\n]{0,6}不通用|额度不通用|separate accounts|different keys|Pro plan only|只在 Pro 套餐")
        files = [ROOT / "documentation/product/install.md", ROOT / "agentjarvis/docs/CLI.md", ROOT / "agentjarvis/site/cli-data.json",
                 AJ / "web/i18n/web.zh.json", AJ / "web/i18n/web.en.json"]
        files += sorted((AJ / "host/agentj").glob("*.py")) + sorted((AJ / "host/agentj").glob("*.json"))
        files += sorted((AJ / "host/agentj/skills").glob("*/SKILL.md"))
        docs = ROOT / "agentjarvis/site/content/docs"
        if docs.is_dir():
            files += [p for p in docs.glob("*/*.md")] + [p for p in docs.glob("*/zh.override.json")]
        hits = [f"{p.relative_to(ROOT)}: {m.group(0)}" for p in files if p.is_file()
                for m in bad.finditer(p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_setup_agents_anchors_unique(self):
        page = ROOT / "agentjarvis/site/content/docs/17-setup-agents"
        if not page.is_dir():
            self.skipTest("private docs are not in the public export")
        for lang in ("en", "zh.src", "zh"):
            ids = re.findall(r"^#{1,6} .*\{#([\w-]+)\}\s*$", (page / f"{lang}.md").read_text(encoding="utf-8"), re.M)
            self.assertEqual(sorted(i for i in set(ids) if ids.count(i) > 1), [], lang)
            self.assertIn("kimi-cli", ids, lang)


if __name__ == "__main__":
    unittest.main()
