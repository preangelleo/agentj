"""P281 (owner, 2026-10-14): the 「◆?」 on a /switch-harness reply + what a running switch is doing.

- Root cause, reproduced from the owner's Mac: Claude Code's own transcript held an assistant text block that was exactly
  U+FFFD (Opus 5.5, right before its Skill call); the host streamed it and stored it as the reply's first line. Not a
  chunk-boundary decode — the host reads whole JSON lines. So the host drops it at the one funnel every harness's reply
  passes (serve.agent_text), in the shared readers' public_text (it is no streamed text either) and in the Telegram
  formatter (stored history too); a U+FFFD inside a sentence stays.
- /switch-harness: the scan / switch job reports fixed phase codes over the control socket (`harness_progress`); serve turns
  them into host lines — on the asking page while the turn runs (card.progress), as pages of their own once Agent J restarts
  (card.switch). A wait note does not hide a progress line; the busy-session queue line is marked card.queued (the phone's
  robot), and a progress line survives the wait note's own card.
Run (in host/tests): ../.venv/bin/python -m unittest test_p281_wait_robot
"""
import _hermetic  # noqa: F401,I001
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import switch_harness as sw
from agentj.text import drop_replacement

from test_p155_switch_harness import UP, Clock, FakeServe, fake_probe, mk_state

FFFD = "�"


class Drop(unittest.TestCase):
    def test_lines_and_edges_go_inside_stays(self):
        self.assertEqual(drop_replacement(FFFD), "")
        self.assertEqual(drop_replacement(FFFD + "\n\n董事长，先用大白话说一下"), "董事长，先用大白话说一下")   # the owner's page
        self.assertEqual(drop_replacement(FFFD + FFFD + " 好"), "好")
        self.assertEqual(drop_replacement("好的\n" + FFFD + "\n继续"), "好的\n继续")
        self.assertEqual(drop_replacement("结尾" + FFFD), "结尾")
        self.assertEqual(drop_replacement("a" + FFFD + "b"), "a" + FFFD + "b")      # a quote of broken text stays
        for same in ("", "plain", "😀 emoji 与中文", None):
            self.assertEqual(drop_replacement(same), same)

    def test_cross_chunk_utf8_is_whole_after_a_stream_decoder(self):
        """The host's line readers decode whole lines; an incremental decoder never makes U+FFFD at a chunk edge."""
        import codecs
        raw = "董事长😀，切换中".encode()
        for cut in range(1, len(raw)):
            d = codecs.getincrementaldecoder("utf-8")()
            self.assertEqual(d.decode(raw[:cut]) + d.decode(raw[cut:], final=True), "董事长😀，切换中")


class Channels(unittest.TestCase):
    def test_agent_text_drops_a_lone_block_and_strips_a_leading_one(self):
        from agentj.serve import Host
        h = mock.MagicMock()
        h.jev_plan, h.cur_turn, h.wait_note = "", None, None
        Host.agent_text(h, FFFD)
        h.emit.assert_not_called()
        h.hist_add.assert_not_called()
        Host.agent_text(h, FFFD + "\n\n董事长，好")
        h.emit.assert_called_once_with("agent_msg", text="董事长，好")
        self.assertEqual(h.hist_add.call_args.args[1], "董事长，好")

    def test_shared_public_text_and_telegram(self):
        from agentj import shared, shared_opencode2, tg_format
        self.assertEqual(shared.public_text(FFFD).strip(), "")      # no streamed text, no 「已收到」 withdrawn for it
        self.assertEqual(shared_opencode2.public_text(FFFD + "\n\nhi"), "hi")
        for rendered, plain in tg_format.parts(FFFD + "\n\n**董事长**，好"):
            self.assertNotIn(FFFD, rendered + plain)


class Progress(unittest.TestCase):
    def test_fixed_lines_only(self):
        self.assertIn("正在探测", sw.progress_text({"phase": "scanning"}))
        self.assertEqual(sw.progress_text({"phase": "scan_one", "to": "codex", "k": 2, "n": 3}), "正在探测 Codex（2/3），每个最多 1 分钟…")
        self.assertIn("正在切换到 OpenCode", sw.progress_text({"phase": "switching", "to": "opencode", "from": "claude"}))
        self.assertIn("正在切回 Claude Code", sw.progress_text({"phase": "back", "to": "opencode", "from": "claude"}))
        self.assertTrue(sw.progress_text({"phase": "back_ok", "to": "codex", "from": "claude"}).startswith("已切回 Claude Code"))
        self.assertTrue(sw.progress_text({"phase": "probing", "to": "codex"}, "en").startswith("Trying Codex"))
        for bad in ({"phase": "nope"}, {"phase": "probing"}, {"phase": "probing", "to": "rm -rf"}, {"phase": "back", "to": "codex"},
                    {"phase": "scan_one", "to": "codex", "k": 4, "n": 3}, {"phase": "scan_one", "to": "codex"}, {}):
            self.assertIsNone(sw.progress_text(bad), bad)

    def _job(self, root, fake, target, probe=None):
        clock = Clock()
        return sw.worker(fake.st, target, probe=probe or fake_probe(), ctl=fake, restart=fake.restart, status=UP,
                         ready=lambda st, limit: "idle", sleep=clock.sleep, clock=clock)

    def _phases(self, fake):
        # P281's own phases; P282's probe_back and P289's handoff pages sit between them once merged (their own tests)
        return [(c["phase"], c.get("to"), c.get("from")) for c in fake.calls if c["cmd"] == "harness_progress"
                and c["phase"] not in ("probe_back", "handoff", "handoff_ok", "handoff_failed")]

    def test_the_job_says_what_it_does(self):
        with tempfile.TemporaryDirectory() as root:
            unit = pathlib.Path(root) / "units"
            with mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name)):
                st = mk_state(root, "claude")
                fake = FakeServe(st, "claude")
                self.assertEqual(self._job(root, fake, "opencode"), 0)
                self.assertEqual(self._phases(fake), [("probing", "opencode", None), ("waiting", "opencode", None),
                                                      ("switching", "opencode", "claude"), ("switched", "opencode", "claude")])
                fake = FakeServe(st, "opencode")
                fake.behaviour["codex"] = {"answers": False}            # the new Agent never introduces itself → back
                self.assertEqual(self._job(root, fake, "codex"), 2)
                self.assertEqual(self._phases(fake)[-3:], [("switched", "codex", "opencode"), ("back", "codex", "opencode"),
                                                           ("back_ok", "codex", "opencode")])
                fake = FakeServe(st, "opencode")
                self.assertEqual(self._job(root, fake, "codex", probe=fake_probe(False)), 1)
                self.assertEqual(self._phases(fake), [("probing", "codex", None)])   # failed gate: nothing more to say

    def test_the_scan_says_each_harness(self):
        with tempfile.TemporaryDirectory() as root:
            st = mk_state(root, "claude")
            calls = []
            sw.scan_worker(st, "j1", probe=fake_probe(), ctl=lambda req: calls.append(req) or {"ok": True})
            self.assertEqual([(c["phase"], c.get("to"), c.get("k")) for c in calls],
                             [("scanning", None, None), ("scan_one", "claude", 1), ("scan_one", "codex", 2), ("scan_one", "opencode", 3),
                              ("scan_one", "kimi", 4), ("scan_one", "qwen", 5)])   # P305, P310


class ServeLines(unittest.TestCase):
    def host(self, root):
        from agentj.history import History
        from agentj.serve import Host
        from agentj.state import State

        class H:
            harness_progress = Host.harness_progress
            turn_wait_note = Host.turn_wait_note
            turn_queued_note = Host.turn_queued_note

            def __init__(self):
                self.lang, self.wait_note = "zh", None
                self.st = mock.MagicMock()
                self.hist = History(State(pathlib.Path(root)), on=True)

            def hist_update(self, tid, **kw):
                return self.hist.update(tid, **kw)

            def hist_add(self, src, reply="", end="done", card=None):
                return self.hist.add(src, reply, end, card)
        h = H()
        h.cur_turn = h.hist.add({"k": "phone", "text": "/switch-harness"}, "", "open", None)["id"]
        return h

    def test_in_turn_on_the_page_then_pages_of_their_own(self):
        with tempfile.TemporaryDirectory() as root:
            h = self.host(root)
            self.assertEqual(h.harness_progress({"phase": "scan_one", "to": "codex", "k": 2, "n": 3}), {"ok": True, "shown": True})
            page = h.hist.get(h.cur_turn)
            self.assertEqual(page["reply"]["text"], "", "the line is the card's, never the Agent's reply")
            self.assertEqual(page["card"], {"progress": "正在探测 Codex（2/3），每个最多 1 分钟…"})
            h.turn_wait_note("正在等待 Bash（已 1 分钟）")
            self.assertEqual(h.hist.get(h.cur_turn)["reply"]["text"], "", "a wait note does not hide the progress line")
            h.turn_wait_note("共享 Claude 还没结束这一回合：…", stall=True)
            self.assertEqual(h.hist.get(h.cur_turn)["card"], {"progress": "正在探测 Codex（2/3），每个最多 1 分钟…", "wait": True, "stall": True})
            h.turn_wait_note(None)
            self.assertEqual(h.hist.get(h.cur_turn)["card"], {"progress": "正在探测 Codex（2/3），每个最多 1 分钟…"})
            self.assertEqual(h.harness_progress({"phase": "bogus"}), {"ok": False, "error": "bad_request"})
            h.hist.update(h.cur_turn, append="能切过去的只有 OpenCode。", end="done")
            self.assertEqual(h.harness_progress({"phase": "probing", "to": "opencode"}), {"ok": True, "shown": False})
            h.harness_progress({"phase": "switching", "to": "opencode", "from": "claude"})
            last = h.hist.get(h.cur_turn + 1)
            self.assertEqual((last["src"]["k"], last["end"], last["card"]), ("sys", "done", {"switch": "switching"}))
            self.assertTrue(last["reply"]["text"].startswith("正在切换到 OpenCode"))

    def test_queue_line_is_marked(self):
        with tempfile.TemporaryDirectory() as root:
            h = self.host(root)
            h.turn_queued_note(True)
            page = h.hist.get(h.cur_turn)
            self.assertEqual((page["reply"]["text"], page["card"]), (Host_note(h), {"queued": True}))
            h.turn_queued_note(False)
            self.assertEqual((h.hist.get(h.cur_turn)["reply"]["text"], h.hist.get(h.cur_turn)["card"]), ("", {}))


def Host_note(h):
    from agentj import humane     # P280: the queued line lives in humane.json (P290)
    return humane.of(h, "shared.busy_queued")


if __name__ == "__main__":
    unittest.main()
