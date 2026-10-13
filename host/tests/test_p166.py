"""P166: any file from the phone (≤ 200 MiB), audio / video transcribed at say time like Telegram (long files in 10-minute
segments), and Telegram's attachment refusals say which reason it was, in the owner's language, with a reason-code log line."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import compose, diskq, inbox, preferences as prefs, serve, telegram as tg, uploads  # noqa: E402
from agentj.state import State  # noqa: E402
from test_l1 import _state  # noqa: E402
from test_p33 import b64, bid, png  # noqa: E402

FF = shutil.which("ffmpeg")


def ffmpeg(*args) -> None:
    subprocess.run([FF, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


class Types(unittest.TestCase):
    def test_any_well_formed_mime_and_scrubbed_extension(self):
        for m in ("audio/x-m4a", "application/octet-stream", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/zip", "video/quicktime", "application/x-sh"):
            self.assertTrue(inbox.accepts(m), m)
        for m in ("", "Audio/M4A", "audio", "audio/x m4a", "a/b;c=d", None, 3):
            self.assertFalse(inbox.accepts(m), m)
        self.assertEqual(inbox.ext_for("audio/x-m4a", "x.bin"), ".m4a", "a known MIME decides")
        self.assertEqual(inbox.ext_for("application/octet-stream", "新录音 3.M4A"), ".m4a")
        self.assertEqual(inbox.ext_for("application/x-foo", "../../etc/passwd"), ".bin")
        self.assertEqual(inbox.ext_for("application/x-foo", "a.tar.gz"), ".gz")
        self.assertEqual(inbox.ext_for("application/x-foo", "evil.sh/../x"), ".bin")
        self.assertEqual(inbox.ext_for("application/x-foo", "x.verylongextension"), ".bin")
        self.assertTrue(inbox.media_kind("video/mp4") and inbox.media_kind("audio/x-m4a") and not inbox.media_kind("image/png"))

    def test_limits_cover_an_hour_of_audio(self):
        self.assertGreaterEqual(uploads.MAX_SIZE, 100 * 1024 * 1024)
        self.assertGreaterEqual(uploads.DEVICE_BYTES, 3 * uploads.MAX_SIZE)
        self.assertGreaterEqual(serve.FF_SEG_SECS * serve.FF_MAX_SEGS, 3600)
        self.assertLessEqual(serve.FF_SEG_SECS, serve.FF_MAX_SECS, "each segment stays inside the decoder's cap")


class Pick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.up = uploads.Uploads(self.st, str(self.work))
        self.dev = "D" * 16

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, data, mime, name, origin="file"):
        b = bid()
        m = {"bid": b, "purpose": "att", "name": name, "mime": mime, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "origin": origin}
        r = self.up.open(self.dev, m)
        self.assertEqual(r[0]["t"], "blob_ack", r)
        for o in range(0, len(data), uploads.CHUNK):
            self.up.chunk(self.dev, {"bid": b, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
        out, _ = self.up.end(self.dev, b)
        self.assertTrue(out[-1]["ok"], out)
        return self.up.blobs[(self.dev, b)]

    def test_large_files_open_and_any_type_lands_with_its_extension(self):
        big = {"bid": bid(), "purpose": "att", "name": "1h.m4a", "mime": "audio/x-m4a", "size": 150 * 1024 * 1024,
               "sha256": "0" * 64, "origin": "file"}
        self.assertEqual(self.up.open(self.dev, big)[0]["t"], "blob_ack", "a 150 MiB recording opens")
        self.up.drop(self.dev, big["bid"])
        over = {**big, "bid": bid(), "size": uploads.MAX_SIZE + 1}
        self.assertEqual(self.up.open(self.dev, over)[0]["why"], "too_big")
        doc = self.put(b"PK\x03\x04" + os.urandom(300), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "报告.docx")
        self.assertTrue(doc.path.endswith("-报告.docx"), doc.path)
        any_ = self.put(os.urandom(64), "application/octet-stream", "notes.sketch")
        self.assertTrue(any_.path.endswith("-notes.sketch"), any_.path)
        self.assertEqual(os.stat(any_.path).st_mode & 0o777, 0o600, "never executable")
        self.assertFalse(doc.voice, "a document gets no private transcription copy")
        jpeg = {"bid": bid(), "purpose": "att", "name": "x.jpg", "mime": "image/jpeg", "size": 100,
                "sha256": "0" * 64, "origin": "file"}
        self.up.open(self.dev, jpeg)
        self.assertEqual(self.up.chunk(self.dev, {"bid": jpeg["bid"], "o": 0, "d": b64(b"MZ" + os.urandom(98))})[-1]["why"], "type",
                         "images keep their magic check")

    def test_a_picked_audio_file_keeps_a_private_copy_for_transcription(self):
        a = self.put(b"\x00\x00\x00\x20ftypM4A " + os.urandom(200), "audio/x-m4a", "Voice memo", origin="file")
        self.assertTrue(a.voice and pathlib.Path(a.voice).is_relative_to(self.st.root), "like a recording (P33-C02)")
        self.assertTrue(a.path.endswith(".m4a"))


class _Engine:
    """The 'transcript' says how long each WAV it got was (16 kHz mono PCM16) — so the test sees the segments."""

    def __init__(self, pad=0):
        self.secs, self.pad = [], pad

    def ready_state(self, _root, engine_override=None):
        return "ready"

    def transcribe(self, path, timeout_s=60, state_dir=None, engine_override=None):
        s = round((os.path.getsize(path) - 44) / 32000)
        self.secs.append(s)
        return {"ok": True, "text": f"[{s}s]" + "字" * self.pad}


@unittest.skipUnless(FF, "needs ffmpeg")
class SayTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        d = pathlib.Path(cls.media.name)
        cls.m4a = d / "long.m4a"          # 21 min 40 s of tone, AAC in MP4 (what iPhone Voice Memos writes)
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:duration=1300", "-ac", "1", "-c:a", "aac", "-b:a", "16k",
               str(cls.m4a))
        cls.silent = d / "silent.mp4"     # a screen recording without sound
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=2", "-c:v", "libx264" if cls._has("libx264") else "mpeg4",
               "-pix_fmt", "yuv420p", str(cls.silent))

    @staticmethod
    def _has(enc):
        out = subprocess.run([FF, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        return f" {enc} " in out

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.host = serve.Host(self.st, events="quiet", read_stdin=False)
        self.host.uploads = self.up = uploads.Uploads(self.st, str(self.work))
        self.host.asr = self.eng = _Engine()
        self.host.lang = "zh"
        self.dev = "D" * 16

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, path, mime, name):
        data = pathlib.Path(path).read_bytes()
        b = bid()
        m = {"bid": b, "purpose": "att", "name": name, "mime": mime, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "origin": "file"}
        self.assertEqual(self.up.open(self.dev, m)[0]["t"], "blob_ack")
        for o in range(0, len(data), uploads.CHUNK):
            self.up.chunk(self.dev, {"bid": b, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
        out, _ = self.up.end(self.dev, b)
        self.assertTrue(out[-1]["ok"], out)
        return self.up.claim(self.dev, [b])[0]

    def say(self, blobs, text="帮我总结这段录音"):
        files = [{"path": b.path, "mime": b.mime, "bytes": b.size, "origin": b.origin, "secs": b.secs, "voice": b.voice}
                 for b in blobs]
        voice = [f for f in files if f["voice"] or (f["origin"] == "recording" and f["mime"].startswith("audio/"))]
        send = compose.Send(self.dev, "s" * 22, 1, blobs=blobs)
        send.onboarding_note = ""
        asyncio.run(self.host._prep_say(send, text, files, voice, None))
        return send.text, files

    def test_a_22_minute_m4a_is_transcribed_whole_in_segments(self):
        blob = self.stage(self.m4a, "audio/x-m4a", "新录音")
        text, _ = self.say([blob])
        self.assertEqual(self.eng.secs, [600, 600, 100], "three segments, nothing cut at 630 s")
        self.assertIn("[600s]\n[600s]\n[100s]", text)
        self.assertIn(blob.path, text, "the original stays attached and named")
        self.assertIn("帮我总结这段录音", text)
        self.assertNotIn("转写不完整", text)
        self.assertEqual(list((self.up.root / self.dev).glob("conv-*")), [], "segment WAVs are deleted")

    def test_a_long_transcript_goes_to_a_text_file_next_to_the_audio(self):
        self.host.asr = self.eng = _Engine(pad=compose.ASR_INLINE // 2)
        blob = self.stage(self.m4a, "audio/x-m4a", "meeting")
        text, files = self.say([blob])
        path = files[0]["asr"]["file"]
        self.assertTrue(path.endswith("-meeting-transcript.txt"), path)
        self.assertIn(path, text)
        self.assertEqual(len(pathlib.Path(path).read_text()), 3 * (compose.ASR_INLINE // 2) + 3 * 6 + 2)
        self.assertLess(len(text), compose.ASR_INLINE + 2000, "the message keeps only the beginning")

    def test_a_failed_later_segment_keeps_the_earlier_text(self):
        calls = []

        def flaky(path, timeout_s=60, state_dir=None, engine_override=None):
            calls.append(path)
            return {"ok": True, "text": "第一段"} if len(calls) == 1 else {"ok": False, "reason": "engine_failed"}
        self.eng.transcribe = flaky
        blob = self.stage(self.m4a, "audio/x-m4a", "x")
        text, files = self.say([blob])
        self.assertEqual(files[0]["asr"]["partial"], "broken")
        self.assertIn("第一段", text)
        self.assertIn("转写不完整", text)

    def test_a_video_without_sound_is_just_a_file(self):
        blob = self.stage(self.silent, "video/mp4", "screen")
        text, files = self.say([blob], "看看这个录屏")
        self.assertNotIn("asr", files[0])
        self.assertNotIn("转写", text)
        self.assertIn(blob.path, text)

    def test_unknown_container_says_format_and_still_attaches(self):
        blob = self.stage(self.m4a, "audio/x-unknown", "odd")
        text, _ = self.say([blob])
        self.assertIn("转写失败（格式）", text)
        self.assertIn(blob.path, text)


class TelegramWhy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p166-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.st = State(self.root / "state")
        self.st.init()
        self.cfg = {"owner_id": 123, "key_env": "AJ_TEST_TG_KEY", "generation": "test"}
        self.group = {"id": "-100", "members": [456, 123], "profile": "family"}
        self.host = SimpleNamespace(st=self.st, preferences=prefs.defaults(), agent=object(), agent_cfg={"dir": str(self.root)},
                                    stopped=lambda: False, _accept=AsyncMock(return_value=SimpleNamespace(turn=1)), lang="zh",
                                    _transcribe_file=AsyncMock(return_value={"ok": True, "text": "会议纪要"}),
                                    stop_turn=AsyncMock())
        self.host.preferences["channels"]["items"] = [{"id": "tg", "type": "telegram"}]
        self.host.preferences["telegram"]["groups"] = [self.group]
        self.t = tg.Telegram(self.host)
        self.t.bot_id, self.t.username = 99, "test_bot"
        p = patch("agentj.telegram.configuration", return_value=self.cfg)
        p.start()
        self.addCleanup(p.stop)
        self.api = patch("agentj.telegram.api", return_value={})
        self.sent = self.api.start()
        self.addCleanup(self.api.stop)

    def msg(self, text="", uid=123, cid=123, **extra):
        return {"message": {"from": {"id": uid}, "chat": {"id": cid, "type": "private" if cid > 0 else "supergroup"}, "text": text, **extra}}

    def replies(self):
        return [c.args[2]["text"] for c in self.sent.call_args_list if c.args[1] == "sendMessage"]

    def logged(self):
        diskq.flush()
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines()] if self.st.log_path.exists() else []

    async def test_over_20mb_says_so_in_chinese_with_the_web_route_and_logs_a_code(self):
        audio = {"file_id": "rec", "file_size": 23_000_000, "duration": 1367, "mime_type": "audio/x-m4a", "file_name": "新录音.m4a"}
        with patch("agentj.telegram.download") as dl:
            self.assertFalse(await self.t.incoming(self.msg(audio=audio), self.cfg))
            dl.assert_not_called()
        r = self.replies()[-1]
        self.assertIn("超过 20 MB", r)
        self.assertIn("m.agentj.app", r)
        self.assertIn("「文件」", r)
        self.assertIn("200 MB", r)
        self.assertIn("快捷指令", r)
        self.assertNotIn("Attachment unavailable", r)
        rows = [x for x in self.logged() if x.get("ev") == "telegram_media_refused"]
        self.assertEqual(rows[-1].get("reason"), "size")
        self.assertNotIn("新录音", self.st.log_path.read_text(), "no name or content in the log")

    async def test_english_follows_the_language(self):
        self.host.lang = "en"
        audio = {"file_id": "rec", "file_size": 23_000_000, "mime_type": "audio/mp4"}
        await self.t.incoming(self.msg(audio=audio), self.cfg)
        self.assertIn("over 20 MB", self.replies()[-1])

    async def test_type_download_and_local_reasons(self):
        await self.t.incoming(self.msg(document={"file_id": "x", "mime_type": "Bad Mime"}), self.cfg)
        self.assertIn("类型无法识别", self.replies()[-1])

        def broken(cfg, media, path):
            raise ValueError("Telegram download failed")
        with patch("agentj.telegram.download", side_effect=broken):
            await self.t.incoming(self.msg(document={"file_id": "x", "mime_type": "application/zip", "file_size": 10}), self.cfg)
        self.assertIn("从 Telegram 下载附件失败", self.replies()[-1])

        def ok(cfg, media, path):
            pathlib.Path(path).write_bytes(b"PK\x03\x04data")
            return path
        with patch("agentj.telegram.download", side_effect=ok), patch("agentj.inbox.place", side_effect=OSError("disk")):
            await self.t.incoming(self.msg(document={"file_id": "x", "mime_type": "application/zip", "file_size": 10}), self.cfg)
        self.assertIn("本地处理", self.replies()[-1])
        reasons = [x.get("reason") for x in self.logged() if x.get("ev") == "telegram_media_refused"]
        self.assertEqual(reasons, ["type", "download", "local"])

    async def test_owner_any_file_and_audio_documents_are_transcribed_with_a_duration_budget(self):
        def ok(cfg, media, path):
            pathlib.Path(path).write_bytes(b"\x00\x00\x00\x20ftypM4A data")
            return path
        doc = {"file_id": "x", "mime_type": "audio/x-m4a", "file_size": 30, "file_name": "memo.m4a"}
        with patch("agentj.telegram.download", side_effect=ok):
            self.assertTrue(await self.t.incoming(self.msg(document=doc), self.cfg))
        self.host._transcribe_file.assert_awaited_once()
        self.assertIn("会议纪要", self.host._accept.call_args.args[1])
        self.assertTrue(self.host._accept.call_args.kwargs["blobs"][0].path.endswith(".m4a"))
        audio = {"file_id": "y", "mime_type": "audio/mpeg", "file_size": 30, "duration": 1200}
        with patch("agentj.telegram.download", side_effect=ok):
            await self.t.incoming(self.msg(audio=audio), self.cfg)
        self.assertEqual(self.host._transcribe_file.call_args.args[1], compose.asr_budget(1200), "not a fixed 120 s")

    async def test_failed_transcription_tells_the_owner_and_still_attaches(self):
        self.host._transcribe_file = AsyncMock(return_value={"ok": False, "why": "timeout"})

        def ok(cfg, media, path):
            pathlib.Path(path).write_bytes(b"OggS" + b"\0" * 20)
            return path
        with patch("agentj.telegram.download", side_effect=ok):
            self.assertTrue(await self.t.incoming(self.msg(voice={"file_id": "v", "duration": 3, "file_size": 24}), self.cfg))
        self.assertIn("语音没能在本地转写（处理超时）", self.host._accept.call_args.args[1])
        self.assertIn("原件已交给 Agent", self.replies()[-1])
        self.assertEqual([x.get("reason") for x in self.logged() if x.get("ev") == "telegram_media_asr"], ["timeout"])

    async def test_group_members_keep_the_allowlist(self):
        with patch("agentj.telegram.download") as dl:
            self.assertFalse(await self.t.incoming(
                self.msg("看看", uid=456, cid=-100, document={"file_id": "x", "mime_type": "application/x-executable", "file_size": 9}), self.cfg))
            dl.assert_not_called()
        self.assertIn("群里不支持", self.replies()[-1])


if __name__ == "__main__":
    unittest.main()
