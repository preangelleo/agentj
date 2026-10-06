"""P59 lane `telegram` (0.15.3, ADR-A165): F21 files over Telegram + F24 /compact from Telegram. Never a real bot.

A local fake Bot API (FakeBot: an HTTP server on 127.0.0.1, `telegram.BASE` points at it) answers getMe / getUpdates /
sendMessage (JSON) and sendPhoto / sendAudio / sendVideo / sendDocument (multipart, parsed back into fields + file bytes).
F21: the owner's reply → one upload per file with the method for its kind, the refusal / oversize lines, nothing for a
group-sourced reply, ≤ 8 files, a photo past Telegram's 10 MB goes as a document, a file changed after the check is not sent,
a refused photo is retried as a document, no names in the log. F24: the owner's /compact and 「压缩」 go through the same
handover-then-compact path as the phone (the stand-ins fakeclaude / fakecodex / fakeopencode) and the result line comes back
to Telegram; a group member's /compact is refused and never reaches the Agent.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import email.parser
import email.policy
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import compactprep, preferences as prefs, telegram as tg, tg_media  # noqa: E402
from agentj import media as media_mod  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p57_agent import _Prep  # noqa: E402

KEY_ENV = "AJ_TEST_TG_KEY"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
MP3 = b"ID3" + b"\0" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\0" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64


# A fake key built at run time: the repo hygiene scan (bridge test_security) must not see a key-shaped literal here.
FAKE_KEY = "sk-" + "test-placeholder-not-real"


class FakeBot:
    """The Bot API, locally: records every call, multipart parsed. `fail` = methods answered {"ok": false}."""

    def __init__(self):
        self.calls, self.updates, self.fail = [], [], set()
        self.lock = threading.Lock()
        bot = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                method = self.path.rsplit("/", 1)[-1]
                ct = self.headers.get("Content-Type", "")
                fields, files = {}, {}
                if ct.startswith("multipart/form-data"):
                    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                        b"Content-Type: " + ct.encode() + b"\r\n\r\n" + body)
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        if part.get_filename() is not None:
                            files[name] = {"filename": part.get_filename(), "mime": part.get_content_type(),
                                           "data": part.get_payload(decode=True)}
                        else:
                            fields[name] = part.get_payload(decode=True).decode()
                else:
                    fields = json.loads(body or b"{}")
                with bot.lock:
                    bot.calls.append({"method": method, "fields": fields, "files": files, "path": self.path})
                if method == "getMe":
                    res = {"ok": True, "result": {"id": 99, "username": "test_bot"}}
                elif method == "getUpdates":
                    time.sleep(0.1)
                    with bot.lock:
                        off = fields.get("offset", 0)
                        ups = [u for u in bot.updates if u["update_id"] >= off]
                        bot.updates = []
                    res = {"ok": True, "result": ups}
                elif method in bot.fail:
                    res = {"ok": False, "description": "refused by the fake"}
                else:
                    res = {"ok": True, "result": {"message_id": len(bot.calls)}}
                data = json.dumps(res).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def sent(self, *methods):
        with self.lock:
            return [c for c in self.calls if c["method"] in methods]

    def texts(self):
        return [c["fields"].get("text", "") for c in self.sent("sendMessage")]


class _Bot:
    """Mixin: a fake Bot API for the test, the bot key a placeholder in the environment, configuration fixed."""

    def start_bot(self):
        self.bot = FakeBot()
        self.addCleanup(self.bot.close)
        self.cfg = {"owner_id": 123, "key_env": KEY_ENV, "generation": "test"}
        for p in (patch.object(tg, "BASE", self.bot.url), patch.dict(os.environ, {KEY_ENV: "test-placeholder"}),
                  patch("agentj.telegram.configuration", return_value=self.cfg), patch.object(tg_media, "GAP", 0)):
            p.start()
            self.addCleanup(p.stop)


def msg(text, uid=123, cid=123, **extra):
    return {"message": {"from": {"id": uid}, "chat": {"id": cid, "type": "private" if cid > 0 else "supergroup"},
                        "text": text, **extra}}


# ------------------------------------------------------------------ F21 over Telegram
class MediaOut(_Bot, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.start_bot()
        self.tmp = tempfile.TemporaryDirectory(prefix="p59tg-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.st = State(root / "state")
        self.st.init()
        self.work = root / "work"
        self.work.mkdir()
        self.host = SimpleNamespace(st=self.st, preferences=prefs.defaults(), agent=object(), agent_cfg={"dir": str(self.work)},
                                    stopped=lambda: False, _accept=AsyncMock(return_value=SimpleNamespace(turn=1)),
                                    stop_turn=AsyncMock(), on_slash=AsyncMock(), lang="zh",
                                    media=media_mod.Media(self.st, str(self.work)))
        self.host.preferences["channels"]["items"] = [{"id": "tg", "type": "telegram"}]
        self.host.preferences["telegram"]["groups"] = [{"id": "-100", "members": [456], "profile": "proxy"}]
        self.t = tg.Telegram(self.host)
        self.t.bot_id, self.t.username = 99, "test_bot"

    def put(self, name, data):
        (self.work / name).write_bytes(data)
        return self.work / name

    def complete(self, text, route=(123, 123, "telegram:123:123")):
        """The reply of a Telegram-sourced turn ends (serve.agent_turn_end → Telegram.completed); returns the queue."""
        self.t.turn_enrollment[7] = dict(self.cfg)
        self.t.turn_routes[7] = route
        self.t.completed({"id": 7, "src": {"k": "telegram", "dev": route[2]}, "reply": {"text": text}})
        out = []
        while not self.t.out.empty():
            out.append(self.t.out.get_nowait()[1])
        return out

    async def deliver(self, out):
        for item in out:
            if isinstance(item, tg.MediaOut):
                await self.t.send_media(self.cfg, item.text)

    async def test_each_kind_goes_with_its_method_and_refusals_are_one_line_each(self):
        self.put("photo.png", PNG)
        self.put("song.mp3", MP3)
        self.put("clip.mp4", MP4)
        self.put("report.pdf", PDF)
        self.put(".env", b"OPENAI_API_KEY=" + FAKE_KEY.encode() + b"\n")
        (self.work / "notes.txt").write_text("token ghp_" + "a1B2c3D4" * 5 + "\n")
        big = self.put("huge.mp4", MP4)
        os.truncate(big, 50_500_000)                     # under the phone's 50 MiB, over Telegram's 50 MB
        text = ("看图 ![图](photo.png)，听 [歌](song.mp3)，视频 `clip.mp4`，报告 [PDF](report.pdf)，"
                "配置 [env](.env)，笔记 [n](notes.txt)，大的 [h](huge.mp4)，网上的 ![x](https://example.com/a.png)")
        out = self.complete(text)
        self.assertIsInstance(out[1], tg.MediaOut)
        await self.deliver(out)
        ups = self.bot.sent("sendPhoto", "sendAudio", "sendVideo", "sendDocument")
        self.assertEqual([(c["method"], list(c["files"])[0], list(c["files"].values())[0]["filename"]) for c in ups],
                         [("sendPhoto", "photo", "photo.png"), ("sendAudio", "audio", "song.mp3"),
                          ("sendVideo", "video", "clip.mp4"), ("sendDocument", "document", "report.pdf")])
        self.assertEqual(ups[0]["files"]["photo"]["data"], PNG, "the bytes on disk, multipart intact")
        self.assertEqual(ups[0]["files"]["photo"]["mime"], "image/png")
        self.assertTrue(all(c["fields"]["chat_id"] == "123" for c in ups), "the owner's private chat only")
        [note] = self.bot.texts()
        self.assertEqual(note.splitlines(), [".env 可能含密钥，没有发。", "notes.txt 可能含密钥，没有发。",
                                             "huge.mp4 太大（48.2 MB），没有发到 Telegram。"])
        log = self.st.log_path.read_text()
        self.assertIn('"status": "4/3"', log)
        for leak in ("photo.png", "report.pdf", FAKE_KEY[:19], "ghp_", str(self.work)):
            self.assertNotIn(leak, log, "metadata only in the log")

    async def test_group_sourced_reply_gets_no_files(self):
        self.put("photo.png", PNG)
        out = self.complete("看 ![](photo.png)", route=(-100, 456, "telegram:-100:456"))
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], dict, "the group's text only")
        self.assertFalse(any(isinstance(x, tg.MediaOut) for x in out))

    async def test_no_files_without_a_working_folder_or_when_nothing_is_referenced(self):
        self.assertEqual(len(self.complete("没有文件")), 2)
        await self.deliver(self.complete("没有文件"))
        self.assertEqual(self.bot.calls, [], "nothing found: nothing sent, not even a note")
        self.host.media = media_mod.Media(self.st, None)
        self.assertEqual(len(self.complete("看 ![](photo.png)")), 1)

    async def test_caps_eight_per_reply_and_big_photo_as_document(self):
        for i in range(10):
            self.put(f"p{i}.png", PNG + bytes([i]))
        await self.deliver(self.complete(" ".join(f"![](p{i}.png)" for i in range(10))))
        self.assertEqual(len(self.bot.sent("sendPhoto")), media_mod.MAX_ITEMS)
        self.bot.calls.clear()
        big = self.put("poster.png", PNG)
        os.truncate(big, 10_200_000)                     # under the phone's 10 MiB, over sendPhoto's 10 MB
        await self.deliver(self.complete("![](poster.png)"))
        self.assertEqual([c["method"] for c in self.bot.sent("sendPhoto", "sendDocument")], ["sendDocument"])

    async def test_refused_photo_is_retried_as_a_document_and_a_failed_upload_is_one_line(self):
        self.put("odd.png", PNG)
        self.put("doc.pdf", PDF)
        self.bot.fail = {"sendPhoto"}
        await self.deliver(self.complete("![](odd.png)"))
        self.assertEqual([c["method"] for c in self.bot.sent("sendPhoto", "sendDocument")], ["sendPhoto", "sendDocument"])
        self.bot.calls.clear()
        self.bot.fail = {"sendDocument"}
        await self.deliver(self.complete("[d](doc.pdf)"))
        self.assertEqual(self.bot.texts(), ["doc.pdf 没发出去。"])

    async def test_file_changed_after_the_check_is_not_sent(self):
        p = self.put("photo.png", PNG)
        items, skips = tg_media.collect(self.host.media, "![](photo.png)")
        self.assertEqual((len(items), skips), (1, []))
        self.assertEqual(tg_media.read_checked(items[0]), PNG)
        time.sleep(0.01)
        p.write_bytes(PNG + b"swapped")
        with self.assertRaises(media_mod.Gone):
            tg_media.read_checked(items[0])
        q = self.put("link.png", PNG)
        q.unlink()
        q.symlink_to(p)
        items, _ = tg_media.collect(self.host.media, "![](link.png)")   # a link inside the folder resolves to its target …
        os.unlink(p)
        os.symlink("/etc/hostname", p)                                  # … which is then swapped for a link out
        with self.assertRaises(media_mod.Gone):
            tg_media.read_checked(items[0])

    async def test_revoked_before_upload_sends_nothing(self):
        self.put("photo.png", PNG)
        out = self.complete("![](photo.png)")
        self.host.preferences["channels"]["items"] = []
        await self.deliver(out)
        self.assertEqual(self.bot.calls, [])

    # ---------------------------------------------------------------- F24: who may compact
    async def test_group_compact_is_refused_and_never_reaches_the_agent(self):
        for text in ("@test_bot /compact", "/compact@test_bot", "@test_bot 压缩"):
            self.assertFalse(await self.t.incoming(msg(text, uid=456, cid=-100), self.cfg), text)
        self.host.on_slash.assert_not_called()
        self.host._accept.assert_not_called()
        self.assertEqual(len(self.bot.texts()), 3)
        self.assertTrue(all("只有机主" in t for t in self.bot.texts()))
        self.assertIn('"result": "refused"', self.st.log_path.read_text())

    async def test_owner_compact_words_are_the_command_not_a_message(self):
        self.host.on_slash.return_value = 5
        self.host.hist = SimpleNamespace(get=lambda t: {"id": t, "end": "open", "reply": {"text": ""}})
        for text in ("/compact", "/compact@test_bot", "压缩", "压缩一下。", "compact"):
            self.assertTrue(await self.t.incoming(msg(text), self.cfg), text)
        self.assertEqual(self.host.on_slash.await_count, 5)
        s, name, arg = self.host.on_slash.call_args.args
        self.assertEqual((name, arg, s.source_kind, s.device), ("compact", "", "telegram", "telegram:123:123"))
        self.host._accept.assert_not_called()
        self.assertTrue(await self.t.incoming(msg("把这个文件压缩一下"), self.cfg))
        self.host._accept.assert_awaited_once()
        self.assertIn("Telegram owner private chat.", self.host._accept.call_args.args[1])
        # the result line comes back when the command's card is written (serve.cmd_card → cmd_result)
        self.t.cmd_result(5, "已压缩：21.3k → 1.3k tokens\n压缩前已写好交接：/x/.agentj/handover/claude-s.md")
        self.assertTrue(self.t.out.get_nowait()[1].startswith("/compact：已压缩"))
        self.t.cmd_result(5, "again")
        self.assertTrue(self.t.out.empty(), "one result per request")

    async def test_owner_compact_answered_at_once_comes_back_too(self):
        self.host.on_slash.return_value = 9
        self.host.hist = SimpleNamespace(get=lambda t: {"id": t, "end": "done", "reply": {"text": "已急停：恢复之后再用这个命令。"}})
        self.assertTrue(await self.t.incoming(msg("压缩"), self.cfg))
        self.assertEqual(self.t.out.get_nowait()[1], "/compact：已急停：恢复之后再用这个命令。")


# ------------------------------------------------------------------ F24 from Telegram with the stand-ins
class _TgPrep(_Bot, _Prep):
    def setUp(self):
        super().setUp()
        self.start_bot()

    def flow_telegram_compact(self):
        async def script(c):
            host = c["host"]
            host.preferences["channels"]["items"] = [{"id": "tg", "type": "telegram"}]
            await c["wait"](lambda: host.telegram is not None and host.telegram.bot_id == 99)
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            self.bot.updates.append({"update_id": 1, **msg("/compact")})
            await c["wait"](lambda: any(t.startswith("/compact：") for t in self.bot.texts()), 30000)
            [res] = [t for t in self.bot.texts() if t.startswith("/compact：")]
            [ho] = self.handovers()
            self.assertTrue(res.startswith("/compact：已压缩"), res)
            self.assertIn(f"压缩前已写好交接：{ho}", res)
            self.assertEqual([m["text"] for m in self.prep_seen(c)], ["交接写好了"], "the preparation ran first")
            page = [t for t in self.hist_lines() if t["src"].get("k") == "cmd"]
            self.assertIn(compactprep.PROGRESS["zh"], [t["reply"]["text"] for t in page])
            self.assertFalse(any("Telegram owner private chat" in str(x) for x in self.logged()),
                             "the command never reached the harness as a message with the envelope")
            # 「压缩」: a new epoch since → prepared afresh, then compacted, the result back again
            self.bot.updates.append({"update_id": 2, **msg("压缩")})
            await c["wait"](lambda: len([t for t in self.bot.texts() if t.startswith("/compact：")]) == 2, 30000)
            self.assertEqual(len(self.prep_seen(c)), 2)
            self.assertIn("压缩前已写好交接", [t for t in self.bot.texts() if t.startswith("/compact：")][1])
            # a group member's /compact: refused, nothing compacted
            host.preferences["telegram"]["groups"] = [{"id": "-100", "members": [456], "profile": "proxy"}]
            n = len(c["cards"]("compact"))
            self.bot.updates.append({"update_id": 3, **msg("@test_bot /compact", uid=456, cid=-100)})
            await c["wait"](lambda: any("只有机主" in t for t in self.bot.texts()))
            await asyncio.sleep(0.5)
            self.assertEqual(len(c["cards"]("compact")), n)
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "telegram_compact"', log)
        for leak in ("Handover", "交接写好了", ".agentj/handover"):
            self.assertNotIn(leak, log)


class ClaudeTelegramCompact(_TgPrep):
    KIND = "claude"

    def test_telegram_compact_prepares_then_compacts(self):
        self.flow_telegram_compact()


class CodexTelegramCompact(_TgPrep):
    KIND = "codex"

    def test_telegram_compact_prepares_then_compacts(self):
        self.flow_telegram_compact()
        lg = self.logged()
        prep = [i for i, x in enumerate(lg) if x.get("method") == "turn/start"
                and any(str(p.get("text", "")).startswith(compactprep.MARK) for p in x["params"]["input"])]
        comp = [i for i, x in enumerate(lg) if x.get("method") == "thread/compact/start"]
        self.assertEqual((len(prep), len(comp)), (2, 2))
        self.assertLess(prep[0], comp[0])


class OpenCodeTelegramCompact(_TgPrep):
    KIND = "opencode"

    def test_telegram_compact_prepares_then_compacts(self):
        self.flow_telegram_compact()


if __name__ == "__main__":
    unittest.main()
