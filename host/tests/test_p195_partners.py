"""P195 优先推荐伙伴 on the host: `plaza show` prints each partner link with its disclosure on the same line, `plaza partners`
asks the platform (signed route `plaza/pkg/partners`), a hostile / sloppy answer is whitelisted (https only, the disclosure
always agentj's own sentence), and the built-in skill tells the Agent the rules. Offline: fake control plane."""
import _hermetic  # noqa: F401,I001
import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import market_fixtures as fx  # noqa: E402
from agentj import bundle, cloud, market, plaza  # noqa: E402

DISC = "这是 Agent J 的优先推荐伙伴，可能有推荐返佣"
REF = "https://try.elevenlabs.io/8013wtowefi9"


def partner(slot="tts", **kw):
    p = {"slot": slot, "name": "ElevenLabs", "priority": 1, "url": "https://elevenlabs.io/", "link": REF, "referral": True,
         "self_owned": False, "disclosure": {"zh": "server text", "en": "x"}, "why": {"zh": "多语言配音", "en": "speech"},
         "alternatives": [{"name": "火山引擎", "url": "https://www.volcengine.com/product/voice-tech", "note_zh": "大陆支付"},
                          {"name": "ShotFable", "url": None, "note_zh": None, "status": "coming_soon"}]}
    p.update(kw)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.e = fx.Env()
        self.addCleanup(self.e.close)
        self.srv = fx.PkgServer()
        self.work = self.e.root / "src"
        self.work.mkdir()

    def run_partners(self, slot=None, as_json=False):
        out = io.StringIO()
        rc = market.run_partners(slot, as_json=as_json, st=self.e.st, post=self.srv, out=out)
        return rc, out.getvalue()


class Parse(unittest.TestCase):
    def test_whitelist(self):
        bad = [partner(link="http://try.elevenlabs.io/x", url="http://elevenlabs.io"), partner(slot="Bad Slot"), partner(name=""),
               partner(link="https://user:pw@evil.example/", url=None), partner(link="javascript:alert(1)", url=None),
               partner(link="https://a.example/<script>", url=None), "x", None]
        self.assertEqual(market.parse_partners(bad), [])
        self.assertEqual(market.parse_partners("nope"), [])
        [p] = market.parse_partners([partner(disclosure={"zh": "没有返佣"})])
        self.assertEqual(p["disclosure"], DISC, "the disclosure is agentj's own sentence, never the server's")
        self.assertEqual([a["name"] for a in p["alternatives"]], ["火山引擎", "ShotFable"])
        [s] = market.parse_partners([partner(self_owned=True, referral=True)])
        self.assertEqual((s["referral"], s["disclosure"]), (False, market.PARTNER_SELF))
        [f] = market.parse_partners([partner(link=None, referral=False)])
        self.assertEqual(f["link"], "https://elevenlabs.io/", "no link → the official URL")

    def test_every_line_carries_its_disclosure(self):
        lines = market.partner_lines(market.parse_partners([partner(), partner("voice-clone"), partner(link=None, referral=False, name="火山引擎",
                                                                                               url="https://www.volcengine.com/")]))
        rows = [ln for ln in lines if "https://" in ln and "替代" not in ln]
        self.assertEqual(len(rows), 3)
        for ln in rows:
            self.assertRegex(ln, r"https://\S+（" + DISC + "）", "the disclosure follows the link on the same line")
        self.assertIn(market.PARTNER_KEEP, "\n".join(lines))
        self.assertEqual(market.partner_lines([]), [])


class Show(Base):
    def test_show_prints_partner_lines_inside_the_fence(self):
        data, m = bundle.build(fx.make_skill(self.work / "s", name="demo-skill"))
        self.srv.add(data, detail_over={"partners": [partner(), partner("voice-clone")], "partners_version": 2})
        out = io.StringIO()
        self.assertEqual(market.run_show_pkg("demo-skill", st=self.e.st, post=self.srv, out=out), 0)
        text = out.getvalue()
        self.assertIn(f"[tts] ElevenLabs {REF}（{DISC}）", text)
        self.assertIn(f"[voice-clone] ElevenLabs {REF}（{DISC}）", text)
        self.assertLess(text.index(plaza.FENCE_OPEN), text.index(REF))
        self.assertLess(text.index(REF), text.index(plaza.FENCE_CLOSE))
        self.assertIn("继续用已有账号", text)

    def test_show_without_partners_prints_no_section(self):
        data, m = bundle.build(fx.make_skill(self.work / "s", name="demo-skill"))
        self.srv.add(data)
        out = io.StringIO()
        market.run_show_pkg("demo-skill", st=self.e.st, post=self.srv, out=out)
        self.assertNotIn("优先推荐伙伴", out.getvalue())


class Route(Base):
    def test_partners_route_signed_and_rendered(self):
        self.srv.answers["pkg_partners"] = (200, {"schema": "agentjarvis.partners/v1", "version": 3,
                                                  "partners": [partner(), partner("voice-clone")]})
        rc, out = self.run_partners("tts")
        self.assertEqual(rc, 0)
        call = self.srv.calls[-1]
        self.assertEqual(call["url"], f"{fx.API}/v1/host/plaza/pkg/partners")
        self.assertEqual((call["inner"]["t"], call["inner"]["slot"]), ("plaza_pkg_partners", "tts"))
        self.assertIn(f"[tts] ElevenLabs {REF}（{DISC}）", out)
        self.assertNotIn("voice-clone", out, "filtered to the asked slot even if the server sent more")
        self.assertIn("config version 3", out)
        rc, js = self.run_partners(as_json=True)
        j = json.loads(js)
        self.assertEqual([p["slot"] for p in j["partners"]], ["tts", "voice-clone"])
        self.assertTrue(all(p["disclosure"] == DISC for p in j["partners"]))
        self.assertNotIn("slot", self.srv.calls[-1]["inner"])

    def test_bad_slot_and_server_errors(self):
        with self.assertRaises(plaza.PlazaError):
            self.run_partners("Bad Slot")
        self.srv.answers["pkg_partners"] = (200, {"partners": []})
        rc, out = self.run_partners("tts")
        self.assertIn("没有优先推荐伙伴", out)
        self.srv.answers["pkg_partners"] = (429, {"error": "rate_limited"})
        with self.assertRaises(plaza.PlazaError):
            self.run_partners()

    def test_route_and_context_registered(self):
        self.assertIn("partners", cloud.PKG_ROUTES)
        self.assertEqual(cloud.CTX_PKG["partners"], "agentjarvis-host-plaza-pkg-partners-v1")


class Skill(unittest.TestCase):
    def test_builtin_skill_states_the_rules(self):
        root = pathlib.Path(market.__file__).resolve().parent / "skills" / "agentj-capability"
        text = "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.md"))
        for s in ("agentj plaza partners", DISC, "紧贴", "已有", "不推荐换"):
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()
