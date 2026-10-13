"""P205 (6, 追加 12:2x) — a local-file link in a page the Agent wrote on its own reaches the phone as a file card.

Leo's Mac showed `[plan.md](~/coding/proj/.worktrees/w1/reports/plan.md)` as raw Markdown:
that page (src k agent, via task — 「后台任务完成」) came through `desktop_text` with no desktop turn open, which wrote a
finished page and never ran the media scan, so the phone got no file list and its slot kept the source text. Neither the
`~` the owner channel writes for the home folder nor the hidden `.worktrees` folder was the cause; both are covered here.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import media, privacy  # noqa: E402
from agentj.serve import Host  # noqa: E402

from test_l1 import _host, _state  # noqa: E402

MD = "# 邮件策略\n\n一、默认分类，不自动回信。\n".encode()
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(2000)


class DesktopPageIsScanned(unittest.TestCase):
    def fake(self):
        h = SimpleNamespace(added=[], scanned=[], desktop_turn=None, lang='zh', hist_update=lambda *a, **k: [{'id': 7}])
        h.hist_add = lambda src, text, end: (h.added.append((src, text, end)), {'id': len(h.added)})[1]
        h.media_later = h.scanned.append
        for name in ('proactive_turn', '_agent_src', 'desktop_text', 'desktop_end'):
            setattr(h, name, getattr(Host, name).__get__(h))
        return h

    def test_a_page_of_its_own_is_scanned_once(self):
        h = self.fake()
        h.proactive_turn('task')
        h.desktop_text('完整设计稿：[a.md](~/a.md)')
        self.assertEqual(h.added[0][2], 'done')
        self.assertEqual(h.scanned, [1], 'the finished page is offered its files (was: never scanned)')

    def test_an_open_desktop_turn_is_scanned_at_its_end_only(self):
        h = self.fake()
        h.desktop_turn = 5
        h.desktop_text('part')
        self.assertEqual(h.scanned, [])
        h.desktop_end()
        self.assertEqual(h.scanned, [7])


class RealHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / "home" / "someone"
        self.work = self.home / "coding"
        self.st = _state(self.tmp.name)
        self.files = {
            "proj/.worktrees/w1/reports/plan.md": MD,
            "audio/memorial/思念.mp3": MP3,
            "audio/a b 🎵.mp3": MP3[:-1] + b"!",
        }
        for rel, data in self.files.items():
            p = self.work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_text_tilde_and_hidden_folder_still_become_cards(self):
        self.assertEqual(privacy.owner_text("[a.md](/home/someone/coding/.worktrees/a.md)"), "[a.md](~/coding/.worktrees/a.md)",
                         "the owner channel writes ~ for the home folder — the page text the scan and the phone both read")
        shown = "完整设计稿：[plan.md](~/coding/proj/.worktrees/w1/reports/plan.md)\n" \
                "- [思念.mp3](~/coding/audio/memorial/思念.mp3)\n- [歌](<~/coding/audio/a b 🎵.mp3>)"
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            items, skips = media.Media(self.st, str(self.work)).scan(shown, 1)
        self.assertEqual(skips, [])
        self.assertEqual([i["name"] for i in items], ["plan.md", "思念.mp3", "a b 🎵.mp3"])
        self.assertTrue(items[0]["ref"].startswith("~/coding/proj/.worktrees/"), "the ref is the text the phone sees")

    def test_background_task_page_gets_its_file_list(self):
        self.st.set_agent_config("claude", str(self.work), fence=False)
        h = _host(self.st, [])
        text = "完整设计稿：[plan.md](~/coding/proj/.worktrees/w1/reports/plan.md)"

        async def go():
            h.proactive_turn("task")
            with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
                h.desktop_text(text)
                await asyncio.gather(*list(h.media_jobs))
            return h.hist.page()[0][-1]
        page = asyncio.run(go())
        self.assertEqual(page["src"].get("via"), "task")
        self.assertEqual([i["name"] for i in page.get("media", [])], ["plan.md"])


if __name__ == "__main__":
    unittest.main()
