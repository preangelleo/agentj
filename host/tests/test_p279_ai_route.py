"""P279: the AI route at install / first run — where Claude Code and Codex are not offered (Hong Kong, mainland China, …)
recommend a Kimi membership Plus or higher through OpenCode, with Agent J's official invite link and its disclosure right
next to it, plus the existing alternatives. Decided by what answers from this computer (no key is ever sent or read);
the time zone / locale only break a tie, locally. The invite link lives in one file (ai_route.json)."""
import _hermetic  # noqa: F401,I001
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock
from unittest.mock import Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import ai_route, first_run, harness  # noqa: E402
from test_l1 import _state  # noqa: E402

HOST = pathlib.Path(__file__).resolve().parents[1]
ANTHROPIC_REGION = '{"type":"error","error":{"type":"forbidden","message":"Request not allowed"}}'
OPENAI_REGION = '{"error":{"code":"unsupported_country_region_territory","message":"Country, region, or territory not supported"}}'
SENTINEL = "sk-" + "p279-SENTINEL-never-printed-0123456789"  # split: tracked-file secret scan


def _http_error(code, body=""):
    return urllib.error.HTTPError("https://x.test", code, "x", {}, io.BytesIO(body.encode()))


class _Resp:
    def __init__(self, status): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Decide(unittest.TestCase):
    """The matrix: anthropic × openai × kimi × local region."""

    def test_matrix(self):
        R = ai_route.decide
        cases = [
            # any official service answers → the old rule, whatever the zone says
            (("ok", "ok", "ok", None), "official", "measured"),
            (("ok", "region", "ok", "HK"), "official", "measured"),
            (("region", "ok", "ok", "CN"), "official", "measured"),
            (("ok", "skipped", "skipped", "HK"), "official", "measured"),
            # Hong Kong: both refused by region
            (("region", "region", "ok", "HK"), "kimi", "measured"),
            (("region", "region", "ok", None), "kimi", "measured"),
            # mainland China: both unreachable, Kimi answers
            (("unreachable", "unreachable", "ok", "CN"), "kimi", "measured"),
            (("unreachable", "region", "unknown", None), "kimi", "measured"),
            # nothing answers at all → offline, recommend nothing
            (("unreachable", "unreachable", "unreachable", "CN"), "offline", "measured"),
            # one refused by region, the other not measured
            (("region", "unknown", "ok", None), "kimi", "partial"),
            (("unknown", "region", "ok", None), "kimi", "partial"),
            (("unreachable", "unknown", "ok", "MO"), "kimi", "partial"),
            (("unreachable", "unknown", "ok", None), "unsure", "partial"),
            # nothing measured (offline tests, odd answers): the zone only
            (("skipped", "skipped", "skipped", "HK"), "kimi", "hint"),
            (("unknown", "unknown", "unknown", "CN"), "kimi", "hint"),
            (("skipped", "skipped", "skipped", "JP"), "unsure", "none"),
            (("skipped", "skipped", "skipped", None), "unsure", "none"),
        ]
        for args, route, conf in cases:
            got = R(*args)
            self.assertEqual((got["route"], got["confidence"]), (route, conf), args)
        self.assertEqual(R("ok", "region")["available"], ["claude"])
        self.assertEqual(R("ok", "ok")["available"], ["claude", "codex"])

    def test_hint_is_local_zone_first(self):
        self.assertEqual(ai_route.hint({}, zone="Asia/Hong_Kong", locale="en_US")["region"], "HK")
        self.assertEqual(ai_route.hint({}, zone="Asia/Shanghai", locale=None)["region"], "CN")
        self.assertEqual(ai_route.hint({}, zone="Asia/Macau", locale=None)["region"], "MO")
        self.assertEqual(ai_route.hint({}, zone="Asia/Tokyo", locale="zh_CN")["region"], None, "a zone that says something wins")
        self.assertEqual(ai_route.hint({}, zone="", locale="zh_HK")["region"], "HK", "no zone readable: the locale")
        self.assertEqual(ai_route.hint({}, zone="Asia/Taipei", locale="zh_TW")["region"], None)
        self.assertEqual(ai_route.hint({"TZ": ":Asia/Hong_Kong", "LANG": "C.UTF-8"})["zone"], "Asia/Hong_Kong")
        self.assertEqual(ai_route.hint({"TZ": "UTC", "LANG": "zh_HK.UTF-8"})["locale"], "zh_HK")


class Probe(unittest.TestCase):
    def get(self, outcome):
        opener = Mock()
        if isinstance(outcome, BaseException):
            opener.open.side_effect = outcome
        else:
            opener.open.return_value = outcome
        with mock.patch("agentj.chatgpt_reach._opener", return_value=opener):
            r = ai_route._get(ai_route.PROBES["anthropic"], {}, 1)
        req = opener.open.call_args[0][0]
        self.assertFalse({k.lower() for k in req.headers} & {"authorization", "x-api-key"}, "never a key")
        self.assertEqual(req.get_method(), "GET")
        return r

    def test_typical_answers(self):
        self.assertEqual(self.get(_http_error(401, '{"type":"error","error":{"type":"authentication_error"}}')), "ok")
        self.assertEqual(self.get(_http_error(403, ANTHROPIC_REGION)), "region")
        self.assertEqual(self.get(_http_error(403, OPENAI_REGION)), "region")
        self.assertEqual(self.get(_http_error(451)), "region")
        self.assertEqual(self.get(_http_error(403, '{"error":"invalid key"}')), "ok")
        self.assertEqual(self.get(urllib.error.URLError("timed out")), "unreachable")
        self.assertEqual(self.get(TimeoutError()), "unreachable")
        self.assertEqual(self.get(_Resp(200)), "ok")
        self.assertEqual(self.get(_http_error(502)), "unknown")

    def test_tests_never_touch_the_network(self):
        self.assertEqual(set(ai_route.reach().values()), {"skipped"})

    def test_reach_probes_three_services(self):
        with mock.patch.dict(os.environ, {"AGENTJ_REACH_PROBE": "on"}), \
                mock.patch.object(ai_route, "_get", side_effect=lambda url, e, t: "region" if "anthropic" in url else "ok") as g, \
                mock.patch("agentj.chatgpt_reach.probe", return_value="region"):
            got = ai_route.reach({})
        self.assertEqual(got, {"anthropic": "region", "openai": "region", "kimi": "ok"})
        self.assertEqual(sorted(c.args[0] for c in g.call_args_list),
                         ["https://api.anthropic.com/v1/models", ai_route.data()["verify_url"]])


class Text(unittest.TestCase):
    def res(self, signals, zone):
        with mock.patch.object(ai_route, "_zone", return_value=zone), mock.patch.object(ai_route, "_locale", return_value=None):
            return ai_route.run(None, environ={}, signals=signals, use_platform=False)

    def test_kimi_route_link_and_disclosure_together(self):
        r = self.res({"anthropic": "region", "openai": "region", "kimi": "ok"}, "Asia/Hong_Kong")
        self.assertEqual(r["route"], "kimi")
        for lang in ("zh", "en"):
            text = ai_route.render(r, lang)
            line = next(l for l in text.splitlines() if ai_route.data()["invite_url"] in l)
            self.assertIn(ai_route.DISCLOSURE[lang], line, "the disclosure sits on the link's own line")
            self.assertTrue(line.index(ai_route.DISCLOSURE[lang]) > line.index(ai_route.data()["invite_url"]))
            self.assertIn("Plus", text)
            self.assertIn("Go", text)
            self.assertIn("kimi-code-plan-cn/k3", text)
            self.assertIn("agentj secret request --name KIMI_API_KEY", text)
            self.assertIn("qwen-in-opencode-cn", text)
            self.assertIn("openrouter.ai", text)
        zh = ai_route.render(r, "zh")
        self.assertIn("这是 Agent J 官方的邀请链接，您和我们各得一次抽奖", zh)
        self.assertIn("Go 档没有编程额度", zh)
        self.assertLess(zh.index("OpenRouter"), zh.index("千问"), "Hong Kong: international alternatives first")

    def test_mainland_lists_mainland_alternatives_first(self):
        r = self.res({"anthropic": "unreachable", "openai": "unreachable", "kimi": "ok"}, "Asia/Shanghai")
        zh = ai_route.render(r, "zh")
        self.assertEqual(r["route"], "kimi")
        self.assertLess(zh.index("千问"), zh.index("OpenRouter"))

    def test_official_route_pushes_nothing(self):
        r = self.res({"anthropic": "ok", "openai": "ok", "kimi": "ok"}, "Asia/Hong_Kong")
        for lang in ("zh", "en"):
            text = ai_route.render(r, lang)
            self.assertNotIn("kimi", text.lower().replace("kimi 能连通", "").replace("kimi reachable", ""))
            self.assertNotIn(ai_route.DISCLOSURE[lang], text)

    def test_hint_only_says_so(self):
        r = self.res({"anthropic": "skipped", "openai": "skipped", "kimi": "skipped"}, "Asia/Hong_Kong")
        self.assertEqual((r["route"], r["confidence"]), ("kimi", "hint"))
        self.assertIn("只按本机时区判断", ai_route.render(r, "zh"))

    def test_offline_recommends_nothing(self):
        r = self.res({"anthropic": "unreachable", "openai": "unreachable", "kimi": "unreachable"}, None)
        self.assertNotIn(ai_route.data()["invite_url"], ai_route.render(r, "zh"))


class Invite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)

    def platform(self, partners):
        return mock.patch("agentj.cloud.read_cloud", return_value={"api": "x"}), (lambda st: {"partners": partners})

    def test_bundled_default(self):
        inv = ai_route.invite(self.st)
        self.assertEqual((inv["link"], inv["invite"], inv["source"]), (ai_route.data()["invite_url"], True, "bundled"))

    def test_platform_entry_wins_when_on_kimi_hosts(self):
        url = "https://www.kimi.com/invite?code=NEW1"
        patch, call = self.platform([{"slot": "ai-coding", "name": "Kimi", "link": url, "url": "https://www.kimi.com/",
                                      "referral": True}])
        with patch:
            inv = ai_route.invite(self.st, call)
        self.assertEqual((inv["link"], inv["invite"], inv["source"]), (url, True, "platform"))

    def test_platform_can_turn_the_invite_off(self):
        patch, call = self.platform([{"slot": "ai-coding", "name": "Kimi", "link": "https://www.kimi.com/",
                                      "url": "https://www.kimi.com/", "referral": False}])
        with patch:
            inv = ai_route.invite(self.st, call)
        self.assertEqual((inv["link"], inv["invite"]), ("https://www.kimi.com/", False))
        self.assertEqual(ai_route.link_line(inv, "zh"), "https://www.kimi.com/", "no invite → no invite disclosure")

    def test_platform_link_off_the_hosts_is_ignored(self):
        for bad in ("https://evil.example/kimi", "http://www.kimi.com/x", "https://user:pw@kimi.com/", "https://kimi.com:8443/"):
            patch, call = self.platform([{"slot": "ai-coding", "name": "Kimi", "link": bad, "referral": True}])
            with patch:
                self.assertEqual(ai_route.invite(self.st, call)["source"], "bundled", bad)

    def test_platform_failure_falls_back(self):
        def boom(st):
            raise OSError("down")
        with mock.patch("agentj.cloud.read_cloud", return_value={"api": "x"}):
            self.assertEqual(ai_route.invite(self.st, boom)["source"], "bundled")
        self.assertEqual(ai_route.invite(self.st, boom)["source"], "bundled", "unbound: never asks the platform")

    def test_one_place(self):
        """The invite code appears in exactly one host source file."""
        code = ai_route.data()["invite_url"].rsplit("=", 1)[1]
        hits = [p.name for p in (HOST / "agentj").rglob("*") if p.is_file() and p.suffix in (".py", ".json", ".md", ".json5")
                and code in p.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(hits, ["ai_route.json"])


class NoKey(unittest.TestCase):
    def test_key_never_in_output_cache_or_journal(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"KIMI_API_KEY": SENTINEL}):
            st = _state(d)
            out = io.StringIO()
            with mock.patch.object(ai_route, "reach", return_value={"anthropic": "region", "openai": "region", "kimi": "ok"}):
                ai_route.main(st, as_json=True, out=out)
                ai_route.main(st, as_json=False, out=out)
            self.assertNotIn(SENTINEL, out.getvalue())
            cache = (st.root / "setup" / ai_route.CACHE).read_text()
            self.assertNotIn(SENTINEL, cache)
            self.assertEqual(oct(os.stat(st.root / "setup" / ai_route.CACHE).st_mode & 0o777), "0o600")
            self.assertEqual(json.loads(cache)["route"], "kimi")
            first_run.resume(st)
            self.assertNotIn(SENTINEL, first_run.path(st).read_text())
            log = st.log_path
            self.assertFalse(log.exists() and SENTINEL in log.read_text())


class Checklist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)

    def harness_row(self):
        snap = first_run.resume(self.st)
        return next(i for i in snap["items"] if i["id"] == "harness")

    def test_no_route_cached_plain_row(self):
        row = self.harness_row()
        self.assertEqual(row["reason"], "no_agent")
        self.assertNotIn(ai_route.data()["invite_url"], json.dumps(row, ensure_ascii=False))

    def test_kimi_route_row_carries_link_and_disclosure(self):
        with mock.patch.object(ai_route, "reach", return_value={"anthropic": "region", "openai": "region", "kimi": "ok"}):
            ai_route.run(self.st, use_platform=False)
        row = self.harness_row()
        self.assertEqual((row["state"], row["reason"]), ("waiting_local", "no_agent_kimi"))
        for lang in ("zh", "en"):
            fact = row["explain"]["fact"][lang]
            self.assertIn(ai_route.data()["invite_url"], fact)
            self.assertIn(ai_route.DISCLOSURE[lang], fact)
            self.assertIn("Plus", fact)
        self.assertEqual(first_run.read(self.st)["ai_route"]["route"], "kimi")

    def test_official_route_clears_it(self):
        with mock.patch.object(ai_route, "reach", return_value={"anthropic": "region", "openai": "region", "kimi": "ok"}):
            ai_route.run(self.st, use_platform=False)
        self.harness_row()
        with mock.patch.object(ai_route, "reach", return_value={"anthropic": "ok", "openai": "ok", "kimi": "ok"}):
            ai_route.run(self.st, use_platform=False)
        self.assertEqual(self.harness_row()["reason"], "no_agent")
        self.assertIsNone(first_run.read(self.st)["ai_route"])

    def test_tampered_journal_link_is_dropped(self):
        self.assertIsNone(ai_route.journal_entry({"route": "kimi", "link": {"link": "https://evil.example/", "invite": True}}))
        self.assertIsNone(ai_route.journal_entry({"route": "official"}))
        self.assertIsNone(ai_route.journal_entry("x"))


class Wiring(unittest.TestCase):
    def test_kimi_key_is_an_opencode_key_name(self):
        self.assertIn("KIMI_API_KEY", harness.OPENCODE_KEY_ENV, "launchd loads only listed names from the env file")

    def test_cli_route_before_init(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "AGENTJ_STATE_DIR": os.path.join(d, "none"), "TZ": "Asia/Hong_Kong", "LANG": "zh_HK.UTF-8"}
            r = subprocess.run([sys.executable, "-m", "agentj.cli", "agent", "route", "--json"], cwd=HOST, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = json.loads(r.stdout)
            self.assertEqual((got["route"], got["confidence"], got["hint"]["region"]), ("kimi", "hint", "HK"))
            self.assertEqual(got["disclosure"], ai_route.DISCLOSURE)
            self.assertFalse(os.path.exists(os.path.join(d, "none")), "before init: nothing is written")
            r = subprocess.run([sys.executable, "-m", "agentj.cli", "agent", "route"], cwd=HOST, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertIn(ai_route.DISCLOSURE["zh"], r.stdout)


if __name__ == "__main__":
    unittest.main()
