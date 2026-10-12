"""P188 (PROTOCOL §28): chat search. hsearch mirrors protocol/search.js (same T2S table, fold, terms, base score — the shared
vectors); search() skips silent and withdrawn pages, ranks by score (ties newest first) or by time, carries who + snippets
only; serve answers hist_find to a p33 session (shape errors answered, never logged with the words) and announces `hfind`;
`agentj history search` prints the same hits on the computer."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import history, hsearch  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VEC = json.loads((ROOT / "protocol" / "vectors" / "p188-search.json").read_text(encoding="utf-8"))


class Mirror(unittest.TestCase):
    def test_t2s_is_the_js_table(self):
        js = (ROOT / "protocol" / "search.js").read_text(encoding="utf-8")
        body = js.split("export const T2S =", 1)[1].split(";", 1)[0]
        self.assertEqual("".join(re.findall(r"'([^']*)'", body)), hsearch.T2S)
        self.assertEqual(len(hsearch.T2S) % 2, 0)

    def test_vectors(self):
        for v in VEC["fold"]:
            self.assertEqual(hsearch.fold(v["s"]), v["f"], v["s"])
        for v in VEC["terms"]:
            self.assertEqual(hsearch.terms(v["q"]), v["t"], v["q"])
        turns = {t["id"]: t for t in VEC["turns"]}
        for case in VEC["scores"]:
            ts = hsearch.terms(case["q"])
            self.assertEqual(ts, case["terms"])
            for x in case["turns"]:
                t = turns[x["id"]]
                self.assertEqual(hsearch.score_fields(hsearch.fields_of(t["src"], t["reply"]), ts), x["r"], (case["q"], x["id"]))

    def test_search_skips_silent_and_withdrawn_and_ranks(self):
        turns = VEC["turns"]
        hits, n = hsearch.search(turns, "部署")
        ids = [h["id"] for h in hits]
        self.assertNotIn(5, ids)                       # 〔不回群〕
        self.assertNotIn(6, ids)                       # withdrawn
        self.assertEqual(n, len(ids))
        scores = [h["score"] for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(ids[0], 4, "the page whose input IS the query ranks first")
        by_time, _ = hsearch.search(turns, "部署", "time")
        self.assertEqual([h["id"] for h in by_time], sorted(ids, reverse=True))
        one, n1 = hsearch.search(turns, "部署", "score", 1)
        self.assertEqual((len(one), n1), (1, n))
        self.assertEqual(hsearch.search(turns, "   ")[0], [])
        h = next(x for x in hits if x["id"] == 4)
        self.assertEqual(set(h), {"id", "ts", "src", "score", "where", "snip"})
        self.assertEqual(h["src"], {"k": "phone", "dev": "d2", "name": "iPad"})          # who, never the text
        self.assertEqual(set(h["snip"]), set(h["where"]))
        self.assertEqual(hsearch.search(turns, "telegram")[1], 0, "the Telegram source line is not the owner's words")

    def test_snippet_window(self):
        s = hsearch.snippet("x" * 100 + "說話" + "y" * 300, ["说话"])
        self.assertTrue(s.startswith("…") and s.endswith("…") and "說話" in s)
        self.assertEqual(len(s), hsearch.SNIP_MAX + 2)
        self.assertEqual(hsearch.snippet("短 說話", ["说话"]), "短 說話")


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def got(self, r):
        return [o for _, o in self.sent if o["t"] == "hist_found" and o.get("r") == r]

    def test_hist_find(self):
        ph = Phone(self.st, "iPhone")
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        s.p33 = True
        old = __import__("agentj.serve", fromlist=["Session"]).Session(cid=12, state="ready", device=ph.did, name="x", pub=ph.pub)
        host.sessions[12] = old
        self.assertIn("hfind", host._caps(s)["caps"])
        a = host.hist.add({"k": "phone", "dev": ph.did, "text": "帮我部署网站"}, "已部署。", "done")
        b = host.hist.add({"k": "phone", "dev": ph.did, "text": "別的事"}, "部署好了嗎？部署 部署", "done")
        host.hist.add({"k": "phone", "text": "部署"}, "〔不回群〕", "done")

        async def go():
            await host._app(s, {"t": "hist_find", "r": "a1", "q": "部署"})
            await host._app(s, {"t": "hist_find", "r": "a2", "q": "部署", "sort": "time", "limit": 1})
            await host._app(s, {"t": "hist_find", "r": "a3", "q": "x" * 201})
            await host._app(s, {"t": "hist_find", "r": "a4", "q": "部署", "sort": "random"})
            await host._app(s, {"t": "hist_find", "r": "a5", "q": " "})
            await host._app(old, {"t": "hist_find", "r": "a6", "q": "部署"})
            await host._app(s, {"t": "hist_find", "r": "a7", "q": "部署", "limit": 51})
        asyncio.run(go())
        m = self.got("a1")[0]
        self.assertEqual((m["n"], m["more"], m["sort"], m["epoch"]), (2, False, "score", host.hist.epoch))
        self.assertEqual([h["id"] for h in m["hits"]], [a["id"], b["id"]])   # the input (×3) beats three hits in a reply
        m = self.got("a2")[0]
        self.assertEqual(([h["id"] for h in m["hits"]], m["more"]), ([b["id"]], True))
        for r in ("a3", "a4", "a5", "a7"):
            self.assertEqual(self.got(r)[0], {"t": "hist_found", "r": r, "error": "shape"}, r)
        self.assertEqual(self.got("a6"), [], "an older page (no p33) gets no answer")
        log = self.st.log_path.read_text(encoding="utf-8") if self.st.log_path.exists() else ""
        self.assertNotIn("部署", log)


class Cli(unittest.TestCase):
    def test_history_search(self):
        with tempfile.TemporaryDirectory() as d:
            sd = pathlib.Path(d) / "s"
            cfg_home = str(pathlib.Path(d) / ".config")
            env = {**os.environ, "AGENTJ_STATE_DIR": str(sd), "HOME": d, "XDG_CONFIG_HOME": cfg_home, "PYTHONPATH": str(HERE.parent)}
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": cfg_home}):
                run = lambda *a: subprocess.run([sys.executable, "-m", "agentj.cli", *a], env=env, capture_output=True, text=True,  # noqa: E731
                                                timeout=60, cwd=str(HERE.parent))
                self.assertEqual(run("init", "--relay", "ws://127.0.0.1:1").returncode, 0)
                st = __import__("agentj.state", fromlist=["State"]).State(sd)
                h = history.History(st)
                t = h.add({"k": "phone", "text": "上線 Deploy"}, "好的", "done")
                h.add({"k": "phone", "text": "无关"}, "无关", "done")
                r = run("history", "search", "上线", "DEPLOY")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"#{t['id']}", r.stdout)
                self.assertIn("找到 1 页", r.stdout)
                j = json.loads(run("history", "search", "deploy", "--json").stdout)
                self.assertEqual([x["id"] for x in j["hits"]], [t["id"]])
                self.assertNotEqual(run("history", "search").returncode, 0)
                self.assertIn("好的", run("history", "show", str(t["id"])).stdout)


if __name__ == "__main__":
    unittest.main()
