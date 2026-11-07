"""#481: `agentj news --version [X]` reads the shipped owner-facing release note (plain words, owner language, one honest
line when missing, never a traceback); a reply linking an Agent J internal file never shows its internal name."""
import _hermetic  # noqa: F401
import argparse
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentj import cli, media, news_notes, tg_media
from agentj.state import State
from _testtmp import TEST_TMP

FIX = Path(__file__).with_name("fixtures") / "news"


class News(unittest.TestCase):
    def test_render_both_languages(self):
        res = news_notes.load("0.26.9", FIX)
        self.assertTrue(res["ok"], res)
        zh = news_notes.render(res, "zh")
        self.assertTrue(zh.startswith("Agent J 0.26.9 更新说明："))
        self.assertIn("1. 派活时会按每天还能用多少额度", zh)
        self.assertIn("（你要做：不用做什么。）", zh)
        en = news_notes.render(res, "en")
        self.assertIn("2. Heartbeats no longer stop", en)
        self.assertIn("what you do: Nothing to do.", en)

    def test_missing_bad_and_broken_are_one_honest_line(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as td:
            miss = news_notes.load("0.26.9", Path(td))
            self.assertEqual(miss["why"], "missing")
            self.assertEqual(news_notes.render(miss, "zh"), "这台主机上没有 Agent J 0.26.9 的更新说明；如实告诉主人，不要猜。")
            (Path(td) / "0.26.9.json").write_text("{not json")
            self.assertEqual(news_notes.load("0.26.9", Path(td))["why"], "unreadable")
            (Path(td) / "0.26.9.json").write_text(json.dumps({"version": "0.26.8", "items": [{"zh": {"what": "x"}}]}))
            self.assertEqual(news_notes.load("0.26.9", Path(td))["why"], "unreadable")
        for bad in ("../../etc/passwd", "0.26.9/../x", "", "latest"):
            self.assertEqual(news_notes.load(bad or "x", FIX)["why"], "bad_version", bad)

    def test_default_is_the_installed_version(self):
        with patch.object(news_notes, "installed", return_value="0.26.9"):
            self.assertTrue(news_notes.load(None, FIX)["ok"])

    def test_cli_routes_without_init_or_network(self):
        a = argparse.Namespace(mode="status", args=[], json=False, release="0.26.9")
        with patch.object(news_notes, "DIR", FIX), patch.object(cli, "State", side_effect=AssertionError("no init")), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            cli.cmd_news(a)
        self.assertIn("Agent J 0.26.9 更新说明：", out.getvalue())
        a = argparse.Namespace(mode="status", args=[], json=True, release="9.9.9")
        with patch.object(news_notes, "DIR", FIX), patch("sys.stdout", new_callable=io.StringIO) as out, \
             self.assertRaises(SystemExit) as err:
            cli.cmd_news(a)
        self.assertEqual(err.exception.code, 1)
        self.assertEqual(json.loads(out.getvalue()), {"ok": False, "version": "9.9.9", "why": "missing"})
        a = argparse.Namespace(mode="status", args=[], json=False, release="0.26.9")
        with patch.object(news_notes, "load", side_effect=RuntimeError("boom")), \
             patch("sys.stdout", new_callable=io.StringIO) as out, self.assertRaises(SystemExit):
            cli.cmd_news(a)                                   # never a traceback
        self.assertIn("没有 Agent J 0.26.9 的更新说明", out.getvalue())

    def test_parser_accepts_version_flag(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, "-c", "from agentj import cli; cli.main(['news', '--help'])"],
                           capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]), timeout=60)
        self.assertIn("--version", r.stdout + r.stderr)


class InternalFileLeak(unittest.TestCase):
    """Mac@M1 0.26.8: 「依据：release-summary.json 不在工作目录里，没有发。」 + a lone 「。」 — the reply linked a packaged file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.st = State(root / "st")
        self.st.init()
        self.work = root / "work"
        self.work.mkdir()
        self.pkg_file = os.path.join(media.PACKAGE, "release-summary.json")
        self.assertTrue(os.path.isfile(self.pkg_file))
        self.reply = f"依据：[release-summary.json]({self.pkg_file})。"

    def test_phone_page_skip_has_no_internal_name(self):
        m = media.Media(self.st, str(self.work))
        items, skips = m.scan(self.reply, 1)
        self.assertEqual(items, [])
        self.assertEqual(skips, [{"name": "这份资料", "why": "private"}])
        m.lang = lambda: "en"
        self.assertEqual(m.scan(self.reply, 2)[1], [{"name": "This file", "why": "private"}])

    def test_channel_skip_lines_have_no_internal_name(self):
        m = media.Media(self.st, str(self.work))
        _items, skips = tg_media.collect(m, self.reply)
        line = tg_media.skip_lines(skips, "zh")
        self.assertNotIn("release-summary", line)
        self.assertTrue(line.startswith("这份资料"), line)

    def test_ordinary_outside_file_keeps_its_name(self):
        other = Path(self.tmp.name) / "elsewhere"
        other.mkdir()
        f = other / "photo.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        _items, skips = media.Media(self.st, str(self.work)).scan(f"[photo.png]({f})", 1)
        self.assertEqual(skips, [{"name": "photo.png", "why": "outside"}])


if __name__ == "__main__":
    unittest.main()
