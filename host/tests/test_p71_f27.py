"""P71 F27 (0.16): a phone's message may be files alone — a picture, a document, a voice note — with no text at all.

What the Agent gets then is the attachment block and the transcripts alone (compose.render), never an empty text part:
Claude Code (stream-json user line), Codex (`turn/start` input text) and OpenCode (v1 `prompt_async` parts / v2 `/prompt`
text) each get a non-empty prompt, checked in the stand-ins' own request logs (fakeclaude / fakecodex / fakeopencode /
fakeopencode2). A voice note alone that cannot be transcribed still goes, with 「转写失败」 and a line telling the Agent to
ask again instead of guessing. Spaces alone without files are still refused (`shape`). Telegram: a photo / voice / file
without a caption is a normal turn. The Agent itself never hands a harness an empty prompt (agent.Agent.run guard).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import agent as agent_mod, compose, fence, preferences as prefs, telegram as tg, wire  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p33 import png, wav  # noqa: E402
from test_p33_chain import FakeASR, _Chain  # noqa: E402
from test_p59_telegram import _Bot, msg  # noqa: E402


class FailASR(FakeASR):
    """Ready, but every recording comes back with nothing said."""
    def transcribe(self, path, *, timeout_s, state_dir=None, engine_override=None):
        self.calls.append((path, timeout_s))
        return {"ok": True, "text": "", "engine": "fake", "ms": 3}


# ------------------------------------------------------------------ compose.render
class Render(unittest.TestCase):
    F = {"path": "/w/.agentj/inbox/a/照片.png", "mime": "image/png", "bytes": 1234, "origin": "photo"}
    V = {"path": "/w/.agentj/inbox/b/语音.wav", "mime": "audio/wav", "bytes": 64000, "origin": "recording"}

    def test_files_alone_make_the_whole_prompt(self):
        for text in ("", "   \n "):
            out = compose.render(text, [self.F], "zh")
            self.assertTrue(out.startswith("附件 1 个"), out)
            self.assertIn(self.F["path"], out)
            self.assertNotIn("没听清", out, "a picture alone needs no 'ask again'")
        self.assertTrue(compose.render("", [self.F], "en").startswith("1 attachment(s)"))

    def test_voice_alone_transcribed(self):
        v = {**self.V, "asr": {"ok": True, "text": "帮我看下日志", "secs": 30}}
        out = compose.render("", [v], "zh")
        self.assertIn("语音转写（30 秒，语音.wav）：\n帮我看下日志", out)
        self.assertNotIn("没听清", out)

    def test_voice_alone_failed_asks_again(self):
        v = {**self.V, "asr": {"ok": False, "why": "no_speech", "secs": 30}}
        out = compose.render("", [v], "zh")
        self.assertIn("转写失败（没有听到说话）", out)
        self.assertTrue(out.endswith(compose.T["zh"]["voice_only_fail"]))
        self.assertTrue(compose.render("", [v], "en").endswith(compose.T["en"]["voice_only_fail"]))
        self.assertNotIn("没听清", compose.render("看看这个", [v], "zh"), "with words the Agent has something to go on")
        self.assertNotIn("没听清", compose.render("", [v, self.F], "zh"), "a picture came too")


# ------------------------------------------------------------------ the Agent never sends an empty prompt
class EmptyGuard(unittest.TestCase):
    def test_empty_send_text_is_not_delivered(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        notices, turns = [], []
        host = SimpleNamespace(agent_status=lambda s: None, agent_turn_start=lambda *a: None, agent_turn_end=lambda: None,
                               turn_failed=lambda: None, agent_notice=notices.append, turn_lock=None,
                               st=SimpleNamespace(log=lambda *a, **k: None))

        class A(agent_mod.Agent):
            kind = "fake"

            async def turn(self, text):
                turns.append(text)

        a = A(host, {"dir": tmp.name})

        async def go():
            s = compose.Send("dev", "sid-empty", 1)
            s.text = "  "
            ok = compose.Send("dev", "sid-ok", 2)
            ok.text = "附件 1 个……"
            a.submit(s)
            a.submit(ok)
            a.start()
            for _ in range(200):
                if turns:
                    break
                await asyncio.sleep(0.01)
            a.task.cancel()
            return s
        s = asyncio.run(go())
        self.assertEqual(turns, ["附件 1 个……"], "only the non-empty one reached the harness")
        self.assertEqual(s.state, "failed")
        self.assertTrue(any("空的" in n for n in notices))


# ------------------------------------------------------------------ end to end with the stand-ins
class _F27:
    """Mixed into _Chain per harness (a mixin, so the runner never collects a harness-less case)."""
    def prompts(self):
        """What the harness was handed for each user turn, from the stand-in's own log (None = this stand-in logs none)."""
        log = self.logged()
        if self.KIND == "codex":
            return [x.get("text") for r in log if r.get("method") == "turn/start" for x in (r["params"].get("input") or [])]
        if self.KIND == "opencode":
            return [p.get("text") for r in log if r.get("path", "").endswith("/prompt_async")
                    for p in (r.get("body") or {}).get("parts") or [] if p.get("type") == "text"]
        return None

    def test_files_alone_voice_alone_voice_failed(self):
        asr = FakeASR()

        async def script(c):
            # a picture alone
            bid = await c["upload"](png(500), name="图.png")
            r, t = await c["said"]("", att=[bid])
            self.assertTrue(r["ok"], r)
            self.assertEqual((t["src"]["text"], t["end"], len(t["src"]["att"])), ("", "done", 1))
            self.assertIn("ECHO: 附件 1 个", t["reply"]["text"], "the prompt starts with the files, no blank text part")
            # spaces + a document: the spaces are not sent as words
            bid = await c["upload"](b"hello F27\n", mime="text/plain", name="note.txt")
            r, t = await c["said"]("   ", att=[bid])
            self.assertIn("ECHO: 附件 1 个", t["reply"]["text"])
            # a voice note alone, transcribed at send time
            att = await c["upload"](wav(2.0), mime="audio/wav", purpose="att", origin="recording", secs=2, name="v1.wav")
            r, t = await c["said"]("", att=[att])
            self.assertIn("语音转写（2 秒，", t["reply"]["text"])
            self.assertIn("听到了", t["reply"]["text"])
            self.assertNotIn("没听清", t["reply"]["text"])
            # a voice note alone, nothing heard → still a turn, the Agent is told to ask again
            c["host"].asr = FailASR()
            att = await c["upload"](wav(2.0), mime="audio/wav", purpose="att", origin="recording", secs=2, name="v2.wav")
            r, t = await c["said"]("", att=[att])
            self.assertEqual(t["end"], "done")
            self.assertIn("转写失败（没有听到说话）", t["reply"]["text"])
            self.assertIn("没听清", t["reply"]["text"])
            # ASR not installed: same
            c["host"].asr = FakeASR("not_installed")
            att = await c["upload"](wav(2.0), mime="audio/wav", purpose="att", origin="recording", secs=2, name="v3.wav")
            r, t = await c["said"]("", att=[att])
            self.assertIn("转写失败（电脑上还没装语音转写）", t["reply"]["text"])
            # nothing at all, or spaces alone: refused as before
            for text in ("", "  \n"):
                sid = wire.b64u(os.urandom(16))
                await c["host"]._app(c["s"], {"t": "say", "sid": sid, "text": text, "ts": 0})
                self.assertEqual(c["res"](sid)["why"], "shape")
            await c["idle"]()
        self.run_chain(script, asr=asr)
        got = self.prompts()
        if got is not None:
            user = [p for p in got if "附件 1 个" in (p or "")]
            self.assertEqual(len(user), 5, got)
            self.assertTrue(all(isinstance(p, str) and p.strip() for p in got), f"an empty text part reached {self.KIND}")


class ClaudeF27(_F27, _Chain):
    KIND = "claude"


class CodexF27(_F27, _Chain):
    KIND = "codex"


class OpenCodeF27(_F27, _Chain):
    KIND = "opencode"


class OpenCodeV2F27(_F27, _Chain):
    """OpenCode 2.x (agent_opencode2): the prompt is POST /api/session/{id}/prompt {"text"}."""
    KIND = "opencode"

    def setUp(self):
        super().setUp()
        root = pathlib.Path(self.tmp.name)
        wrapper = root / "opencode"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeopencode2.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        extra = {"FAKE_OC2_CONNECTED": "opencode", "XDG_DATA_HOME": str(root / "data"), "XDG_CONFIG_HOME": str(root / "cfg")}
        self.env.update(extra)
        os.environ.update(extra)
        fenced = fence.problem(self.st, str(self.work)) is None
        self.st.set_agent_config("opencode", str(self.work), model="deepseek/chat", fence=fenced)

    def prompts(self):
        return [(r.get("b") or {}).get("text") for r in self.logged() if r.get("p", "").endswith("/prompt")]


# ------------------------------------------------------------------ Telegram: media without a caption
class TelegramNoCaption(_Bot, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.start_bot()
        self.tmp = tempfile.TemporaryDirectory(prefix="p71tg-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.st = State(root / "state")
        self.st.init()
        self.work = root / "work"
        self.work.mkdir()
        self.host = SimpleNamespace(st=self.st, preferences=prefs.defaults(), agent=object(), agent_cfg={"dir": str(self.work)},
                                    stopped=lambda: False, _accept=AsyncMock(return_value=SimpleNamespace(turn=1)),
                                    stop_turn=AsyncMock(), on_slash=AsyncMock(), lang="zh",
                                    _transcribe_file=AsyncMock(return_value={"ok": True, "text": "帮我看下日志", "secs": 3}))
        self.host.preferences["channels"]["items"] = [{"id": "tg", "type": "telegram"}]
        self.t = tg.Telegram(self.host)
        self.t.bot_id, self.t.username = 99, "test_bot"

        def fake_download(cfg, media, path):
            pathlib.Path(path).write_bytes({"image/jpeg": b"\xff\xd8\xff" + b"\0" * 64,
                                            "audio/ogg": b"OggS" + b"\0" * 64}.get(media["mime"], b"%PDF-1.7\n" + b"x" * 64))
            return path
        p = patch.object(tg, "download", side_effect=fake_download)
        p.start()
        self.addCleanup(p.stop)

    def nocap(self, **media):
        m = msg("", **media)
        del m["message"]["text"]
        return m

    async def test_photo_voice_file_without_caption(self):
        cases = [({"photo": [{"file_id": "p1", "width": 10, "height": 10, "file_size": 67}]}, "photo.jpg", None),
                 ({"voice": {"file_id": "v1", "duration": 3, "mime_type": "audio/ogg", "file_size": 68}}, None, "帮我看下日志"),
                 ({"document": {"file_id": "d1", "file_name": "r.pdf", "mime_type": "application/pdf", "file_size": 73}}, "r.pdf", None)]
        for media, name, words in cases:
            self.host._accept.reset_mock()
            self.assertTrue(await self.t.incoming(self.nocap(**media), self.cfg), media)
            self.host._accept.assert_awaited_once()
            session, text = self.host._accept.call_args.args[:2]
            blobs = self.host._accept.call_args.kwargs.get("blobs") or []
            self.assertEqual(session.source_kind, "telegram")
            self.assertTrue(text.strip(), "never an empty text")
            self.assertEqual(len(blobs), 1)
            if name:
                self.assertEqual(blobs[0].name, name)
            if words:
                self.assertIn(words, text)
            # what the Agent then gets (serve._accept → compose.render): the envelope line, the file, never blank
            out = compose.render(text, [{"path": b.path, "mime": b.mime, "bytes": b.size, "origin": b.origin} for b in blobs])
            self.assertIn("附件 1 个", out)
        self.assertEqual(self.bot.texts(), [], "no refusal line")

    async def test_voice_without_caption_transcription_unavailable(self):
        self.host._transcribe_file = AsyncMock(return_value={"ok": False, "why": "not_installed"})
        m = self.nocap(voice={"file_id": "v2", "duration": 4, "mime_type": "audio/ogg", "file_size": 68})
        self.assertTrue(await self.t.incoming(m, self.cfg))
        text = self.host._accept.call_args.args[1]
        self.assertIn("transcription unavailable", text)
        self.assertEqual(len(self.host._accept.call_args.kwargs["blobs"]), 1)


if __name__ == "__main__":
    unittest.main()
