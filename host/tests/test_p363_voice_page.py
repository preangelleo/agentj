"""P363 (backlog #72): a voice-only message's transcript goes back onto the owner's own page. Once say-time transcription is
done the host sets src.att[i].asr ({ok, text} or {ok: false, why}) on that history turn and sends the changed page to every
paired remote (hist_turn); the history file keeps it, so a later read-back (hist_page) carries it too. The Agent's message is
unchanged (the transcript already reached it through compose.render)."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import history, serve, wire  # noqa: E402

from test_l1 import _state  # noqa: E402
from test_p33 import wav  # noqa: E402
from test_p33_chain import FakeASR, _Chain  # noqa: E402


class PageAsr(unittest.TestCase):
    def test_ok_failed_and_skipped(self):
        files = [{"asr": {"ok": True, "text": " 你好 ", "secs": 2.04}}, {"path": "x.png"},
                 {"asr": {"ok": False, "why": "not_installed"}}, {"asr": {"ok": True, "text": "  "}}]
        self.assertEqual(serve.page_asr(files), {0: {"ok": True, "text": "你好", "secs": 2.0},
                                                 2: {"ok": False, "why": "not_installed"},
                                                 3: {"ok": False, "why": "no_speech"}})

    def test_long_is_cut_to_the_wire_limit(self):
        long = "字" * (wire.MAX_TEXT_P33 + 5)
        r = serve.page_asr([{"asr": {"ok": True, "text": long}}])[0]
        self.assertTrue(r["cut"])
        self.assertEqual(len(r["text"]), wire.MAX_TEXT_P33)
        r = serve.page_asr([{"asr": {"ok": True, "text": "短", "file": "/inbox/t.txt", "partial": "timeout"}}])[0]
        self.assertEqual((r["cut"], r["partial"]), (True, "timeout"), "a transcript the Agent got as a file is marked cut")


class HistoryAttAsr(unittest.TestCase):
    def test_update_sets_asr_on_the_attachment_and_survives_a_reload(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            h = history.History(st, on=True)
            t = h.add({"k": "phone", "text": "", "att": [{"name": "a.webm", "kind": "audio"}, {"name": "p.png", "kind": "image"}]})
            ch = h.update(t["id"], att_asr={0: {"ok": True, "text": "转写"}, 5: {"ok": True, "text": "x"}, "1": {"ok": False}})
            self.assertEqual(len(ch), 1)
            self.assertEqual(ch[0]["src"]["att"][0]["asr"], {"ok": True, "text": "转写"})
            self.assertNotIn("asr", ch[0]["src"]["att"][1], "out of range / non-int index ignored")
            again = history.History(st, on=True)
            self.assertEqual(again.get(t["id"])["src"]["att"][0]["asr"]["text"], "转写", "on disk: read-back carries it")
            self.assertEqual(h.update(t["id"] + 99, att_asr={0: {}}), [])


class VoicePageChain(_Chain):
    KIND = "claude"

    def test_voice_only_page_gets_its_transcript_on_every_remote(self):
        asr = FakeASR()

        async def script(c):
            take = wav(2.0)
            att = await c["upload"](take, mime="audio/wav", purpose="att", origin="recording", secs=2, name="voice.wav")
            pages = lambda: [o for _, o in c["sent"] if o["t"] == "hist_turn"]   # noqa: E731
            sid = await c["say"]("", att=[att])
            await c["wait"](lambda: c["res"](sid) is not None)
            tid = c["res"](sid)["turn"]
            await c["wait"](lambda: any(o["turn"]["id"] == tid and o["turn"]["src"]["att"][0].get("asr") for o in pages()))
            got = [o["turn"]["src"]["att"][0]["asr"] for o in pages() if o["turn"]["id"] == tid and o["turn"]["src"]["att"][0].get("asr")]
            self.assertEqual(got[0], {"ok": True, "text": f"听到了 {len(take)} 字节", "secs": 2.0})
            self.assertEqual(c["host"].hist.get(tid)["src"]["att"][0]["asr"]["text"], f"听到了 {len(take)} 字节")
            await c["wait"](lambda: c["turns"]().get(tid, {}).get("end") == "done")
            self.assertIn(f"听到了 {len(take)} 字节", c["turns"]()[tid]["reply"]["text"], "the Agent still got the words")
            # a failed transcription: the reason, never an empty transcript
            asr.state = "not_installed"
            att2 = await c["upload"](take, mime="audio/wav", purpose="att", origin="recording", secs=2, name="v2.wav")
            sid2 = await c["say"]("", att=[att2])
            await c["wait"](lambda: c["res"](sid2) is not None)
            t2 = c["res"](sid2)["turn"]
            await c["wait"](lambda: c["host"].hist.get(t2) and c["host"].hist.get(t2)["src"]["att"][0].get("asr"))
            self.assertEqual(c["host"].hist.get(t2)["src"]["att"][0]["asr"], {"ok": False, "why": "not_installed", "secs": 2.0})
        self.run_chain(script, asr=asr)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
