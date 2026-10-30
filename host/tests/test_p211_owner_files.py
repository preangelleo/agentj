"""P211 (Leo, 10-13): two TTS mp3 files the Agent named as plain paths under `~/coding/.agentj/outbox/` got
「…mp3 可能含密钥，没有发。」 on the Web App page. Root cause: the `.agentj` path component (agentj's own per-folder
workspace) was on media.DENY_DIRS and every deny-list hit was reported as `secret` — neither the Chinese / underscore file
name nor the mp3 bytes were involved (audio is typed by its leading bytes and never read as text).

Fix, aligned with P194 (owner-directed outbound): toward the owner (the Web App page, the owner bot's private chat, mail to
the owner's own address) a file goes as it is. The deterministic floor stays — a credential file by name / folder or a text
file with a real key shape is never sent as a file; an explicit link to one opens a secret pickup card, else a note says why
and what to do. Everyone else (a contact's mail) keeps the full scan and the `.agentj` / `.git` rule.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import email
import email.policy
import json
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import media, owner_route, tg_media  # noqa: E402

from test_p33 import png  # noqa: E402
from test_p57_media import KEY, _Base  # noqa: E402
import test_p184_contacts as P184  # noqa: E402

NAME = "张首晟教授致Barbara_FishAudio长样本版"
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(3000) + KEY.encode() + os.urandom(3000)   # a key-shaped run in the frames
WAV = b"RIFF\x24\x10\x00\x00WAVEfmt " + os.urandom(4000)
MP4 = b"\x00\x00\x00\x18ftypisom" + os.urandom(4000)
ENTROPY = "备份校验串 " + "q7Zr2Lx9Vb4Nk8Pw3Hs6Td1Fg5Jm0Yc" * 2 + "\n"     # high entropy, no key shape: a guess, not a key


class Repro(_Base):
    """The screenshot, reproduced: plain `~/…` paths, Chinese + underscore + long names, in `.agentj/outbox` and a normal folder."""

    def setUp(self):
        super().setUp()
        self.home = patch.dict(os.environ, {"HOME": str(self.work.parent)})
        self.home.start()
        self.addCleanup(self.home.stop)

    def files(self, folder):
        return [self.put(f"{folder}/{NAME}{ext}", data) for ext, data in
                ((".mp3", MP3), ("_带语气.mp3", MP3[:-7]), (".wav", WAV), (".mp4", MP4), (".png", png(5000)))]

    def test_long_chinese_media_names_become_cards_in_outbox_and_any_folder(self):
        for folder in (".agentj/outbox", "out", "音频产出/2026-10-13"):
            paths = self.files(folder)
            text = "两个版本都在：\n" + "\n".join(f"~/work/{p.relative_to(self.work)}" for p in paths) + "\n"
            items, skips = self.scan(text)
            self.assertEqual(skips, [], folder)
            self.assertEqual([i["name"] for i in items], [p.name for p in paths], folder)
            self.assertEqual([i["kind"] for i in items], ["audio", "audio", "audio", "video", "image"])

    def test_before_the_fix_it_was_the_folder_rule_not_the_name_or_the_bytes(self):
        [mp3, *_] = self.files(".agentj/outbox")
        ref = media.Ref(str(mp3), str(mp3), True)
        self.assertEqual(self.m.check(ref, owner=False)[1], "private", "others: agentj's folder is private, not 'secret'")
        self.assertIsNone(self.m.check(ref, owner=True)[1])
        [mp3o, *_] = self.files("out")
        self.assertIsNone(self.m.check(media.Ref(str(mp3o), str(mp3o), True), owner=False)[1],
                          "the same name + the same bytes in a normal folder pass even the strict rules")

    def test_binary_media_is_never_read_as_text(self):
        p = self.put("out/a.mp3", MP3)
        for owner in (True, False):
            rec, why = self.m.check(media.Ref("out/a.mp3", "out/a.mp3", True), owner=owner)
            self.assertIsNone(why, owner)
            self.assertEqual(rec["kind"], "audio")
        # a text file pretending to be an mp3 is not audio (leading bytes, not the extension)
        p.write_bytes(KEY.encode())
        self.assertEqual(self.m.check(media.Ref("out/a.mp3", "out/a.mp3", True), owner=True)[1], "type")

    def test_env_and_key_text_still_held_back_in_both_directions(self):
        self.put(".env", b"OPENAI_API_KEY=" + KEY.encode() + b"\n")
        self.put("out/key.txt", f"用这个 {KEY}\n".encode())
        self.put("out/guess.txt", ENTROPY.encode())
        items, skips, keys = self.m.scan_keys("[e](.env) [k](out/key.txt) [g](out/guess.txt) `.env`", 3)
        # P573 (#386, replaces key_content here): a key in a text file → the file goes as its masked copy, the value carded
        self.assertEqual(skips, [{"name": ".env", "why": "key_file"}])
        self.assertEqual([i["name"] for i in items], ["key.txt", "guess.txt"], "owner: the high-entropy guess never blocks")
        self.assertEqual([k[1]["name"] for k in keys], [".env", "key.txt"], "explicit links → pickup-card candidates")
        self.assertEqual(keys[1][0], None)
        self.assertEqual([v for _k, v in keys[1][1]["values"]], [KEY])
        for ref in media.refs("[e](.env) [k](out/key.txt) [g](out/guess.txt)"):
            self.assertEqual(self.m.check(ref, owner=False)[1], "secret", ref.path)
        # strict() re-checks an owner record before it goes to someone else
        rec = next(r for r in self.m.table.values() if r["name"] == "guess.txt")
        self.assertEqual(self.m.strict(rec), "secret")

    def test_telegram_collect_is_owner_direction_and_reports_keys(self):
        [mp3, *_] = self.files(".agentj/outbox")
        self.put(".env", b"A=" + KEY.encode())
        keys = []
        items, skips = tg_media.collect(self.m, f"{mp3} [e](.env)", keys)
        self.assertEqual([i["name"] for i in items], [mp3.name])
        self.assertEqual(skips, [{"name": ".env", "why": "key_file"}])
        self.assertEqual(len(keys), 1)

    def test_mail_reply_to_the_owner_reports_held_keys(self):
        from agentj import mail
        [mp3, *_] = self.files(".agentj/outbox")
        self.put(".env", b"A=" + KEY.encode())
        m = mail.Mail.__new__(mail.Mail)
        m.host = SimpleNamespace(media=self.m)
        files, note = m._files(f"{mp3} [e](.env)", True)
        self.assertEqual([f[0] for f in files], [mp3.name])
        self.assertEqual(note["skips"], [{"name": ".env", "why": "key_file"}])
        self.assertEqual(len(note["keys"]), 1)
        self.assertEqual([f[0] for f in m._files(f"{mp3}")], [mp3.name], "the old one-value form still works")

    def test_every_why_has_a_reason_and_a_way_out(self):
        web = pathlib.Path(__file__).resolve().parents[2] / "web" / "i18n"
        for lang in ("zh", "en"):
            d = json.loads((web / f"web.{lang}.json").read_text())["media"]["skip"]
            for why in media.WHYS:
                self.assertIn(why, d, (lang, why))
                self.assertIn("{name}", d[why])
            for why in ("too_big", "outside", "secret", "type", "gone", "masked", "private", "key_file", "key_content", "key_card"):
                self.assertIn(why, tg_media.SKIP[lang], (lang, why))
        js = (web.parent / "public" / "js" / "mediawire.js").read_text()
        import re
        whys = re.search(r"export const WHYS = \[(.*?)\];", js, re.S).group(1)
        self.assertEqual(set(re.findall(r"'(\w+)'", whys)), set(media.WHYS), "the phone keeps every reason the host sends")
        self.assertNotIn("可能含密钥", tg_media.SKIP["zh"]["secret"])
        self.assertIn("改个名", tg_media.SKIP["zh"]["secret"])
        self.assertIn("领取卡", tg_media.SKIP["zh"]["key_file"])


class Box:
    def __init__(self, result="sent"):
        self.result, self.reqs = result, []

    async def send(self, req):
        self.reqs.append(req)
        return {"result": self.result}


class Cards(_Base):
    def host(self, box):
        return SimpleNamespace(st=self.st, elevate=SimpleNamespace(out=box), stopped=lambda: False, _approvers=lambda: ["dev1"])

    def test_explicit_key_file_becomes_one_card_and_the_same_file_is_not_carded_twice(self):
        self.put(".env", b"A=" + KEY.encode() + b"\n")
        box = Box()
        h = self.host(box)
        _, skips, keys = self.m.scan_keys("配置在 [这里](.env)", 1)
        self.assertEqual(asyncio.run(owner_route.offer_files(h, keys, "zh")), 1)
        self.assertEqual(skips, [{"name": ".env", "why": "key_card"}])
        [req] = box.reqs
        self.assertEqual((req["kind"], req["filename"]), ("file", ".env"))
        _, skips2, keys2 = self.m.scan_keys("配置在 [这里](.env)", 2)      # e.g. the Telegram copy of the same reply
        self.assertEqual(asyncio.run(owner_route.offer_files(h, keys2, "zh")), 1)
        self.assertEqual((len(box.reqs), skips2[0]["why"]), (1, "key_card"))
        self.assertNotIn(KEY, self.st.log_path.read_text() if self.st.log_path.exists() else "")

    def test_no_device_or_a_refused_card_keeps_the_note(self):
        self.put("out/key.txt", f"{KEY}\n".encode())
        for h in (SimpleNamespace(st=self.st, elevate=None), self.host(Box("no_device"))):
            items, skips, keys = self.m.scan_keys("[k](out/key.txt)", 1)
            self.assertEqual(asyncio.run(owner_route.offer_files(h, keys, "zh")), 0)
            # P573: no card possible → the masked copy still goes, the value stays hidden behind the placeholder
            self.assertEqual(skips, [])
            got = b"".join(d for _, d, _ in self.m.window(items[0]["mid"], 0))
            self.assertNotIn(KEY.encode(), got)
            self.assertIn(b"<redacted>", got)
        self.assertIn("领取卡", tg_media.skip_lines([{"name": "key.txt", "why": "key_content"}], "zh"))


class Contacts(P184._Svc):
    """Mail through the contacts service: to the owner's own address owner rules; to anyone else the strict ones."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.work / ".agentj" / "outbox").mkdir(parents=True)
        (self.work / ".agentj" / "outbox" / f"{NAME}.mp3").write_bytes(MP3)
        (self.work / "guess.txt").write_text(ENTROPY)
        items, _ = self.media.scan(f"音频 `.agentj/outbox/{NAME}.mp3`，校验 [g](guess.txt)", 8)
        self.mids.update({i["name"]: i["mid"] for i in items})
        self.assertEqual(len(items), 2)
        self.pages[8] = {"id": 8, "end": "done", "reply": {"text": "好了"}, "media": items}
        self.mailbox()

    async def test_page_files_to_the_owner_go_and_to_others_only_what_passes_the_strict_rules(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": P184.OWNER}], "turn": 8})
        self.assertEqual((res["ok"], res["files"]), (True, 2))
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": "wang@x.cn"}], "turn": 8})
        self.assertEqual((res["ok"], res["files"], res.get("skipped")), (True, 0, 2))
        msg = email.message_from_bytes(self.smtp.sent[-1][2], policy=email.policy.default)
        self.assertEqual(list(msg.iter_attachments()), [])
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": "wang@x.cn"}], "mid": self.mids["guess.txt"]})
        self.assertEqual((res["ok"], res["why"]), (False, "secret"))

    async def test_agent_send_files(self):
        p = f".agentj/outbox/{NAME}.mp3"
        res = await self.svc.agent_send({"to": [P184.OWNER], "subject": "音频", "body": "附件", "files": [p]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["files"], [f"{NAME}.mp3"])
        res = await self.svc.agent_send({"to": ["wang@x.cn"], "subject": "音频", "body": "附件", "files": [p]})
        self.assertEqual((res["ok"], res["why"]), (False, "file"))
        self.assertIn("private", res["detail"])
        (self.work / ".env").write_text("A=" + KEY)
        res = await self.svc.agent_send({"to": [P184.OWNER], "subject": "x", "body": "y", "files": [".env"]})
        self.assertEqual((res["ok"], res["why"]), (False, "file"))
        self.assertIn("secret send", res["detail"])


if __name__ == "__main__":
    unittest.main()
