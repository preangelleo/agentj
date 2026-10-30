"""P270 OWNER documents vs credentials; no mail leaves local SMTP fake."""
import _hermetic  # noqa: F401
import asyncio
import email
import email.policy
import unittest
from types import SimpleNamespace
from agentj import taskspec, media, owner_route, mail
from test_p57_media import _Base, KEY
from test_p211_owner_files import Box
import test_p184_contacts as P184

DOCUMENTS = [
    "| 客户凭据 | APIFY_TOKEN，secret=true；credentials 说明从 Apify Console 获取… |",
    '→ 201 { "key_id": "key_abc", "secret": "<只显示一次>", "created_at": "..." }',
    "Idempotency-Key: 6f1c...-agent...",
    'rm -- "$HOME/models/local-rescue-model/Qwen3.5-2B-Q4_K_M.gguf"',
    "secret=false; token=null; password=undefined",
    "APIFY_TOKEN=APIFY_TOKEN; API_KEY=YOUR_API_KEY",
    'client_secret="请从控制台获取，仅显示一次"',
    'token="<your-token>"; password="replace_me"',
    "token=${APIFY_TOKEN}; secret=$APIFY_TOKEN",
    '{"secret": "client_secret", "credentials": "client_credentials"}',
    "API_TOKEN=/srv/demo/models/Qwen3.5-2B-Q4_K_M.gguf",
    "API_TOKEN=models/Qwen3.5-2B-Q4_K_M.gguf",
    "Authorization: Bearer APIFY_TOKEN",
    "Authorization: Bearer MY_VERY_LONG_APIFY_TOKEN",
    "Authorization: Bearer true",
    '{"secret": "clientSecret", "credentials": "clientToken"}',
]

class Scan(_Base):
    def test_report_and_mail_reply_are_ordinary_files(self):
        for i, doc in enumerate(DOCUMENTS):
            with self.subTest(i=i):
                self.assertFalse(taskspec.secret_like(doc, owner=True))
                self.put(f"out/report{i}.md", doc.encode())
        links = " ".join(f"[report{i}](out/report{i}.md)" for i in range(8))
        items, skips, keys = self.m.scan_keys(links, 1)
        self.assertEqual(len(items), 8)
        self.assertEqual((skips, keys), ([], []))
        m = mail.Mail.__new__(mail.Mail); m.host = SimpleNamespace(media=self.m)
        files, note = m._files(links, True)
        self.assertEqual(len(files), 8)
        self.assertEqual(note, {"skips": [], "keys": []})

    def test_real_shapes_remain_held_and_carded(self):
        values = [KEY, "q7Zr" + "2Lx9Vb4Nk8Pw3Hs6", "ab12cd34" * 4, "Q7DZ_F9HX_" + "2L8NK4PW3HS6TD1F", "abcdefghijklmnopqrstuvwxyz" * 2]
        for i, value in enumerate(values):
            text = f"API_TOKEN={value}"
            self.assertTrue(taskspec.secret_like(text, owner=True))
            for marked in (text + "；请不要外传", text + "，说明", f"API_TOKEN=`{value}`", "API_TOKEN=/" + value, "Authorization: Bearer " + value):
                self.assertTrue(taskspec.secret_like(marked, owner=True))
            self.put(f"out/key{i}.md", text.encode())
        box = Box(); h = SimpleNamespace(st=self.st, elevate=SimpleNamespace(out=box), stopped=lambda: False, _approvers=lambda: ["dev1"])
        items, skips, keys = self.m.scan_keys(" ".join(f"[k](out/key{i}.md)" for i in range(len(values))), 2)
        # P573 (#386, replaces the whole-file cards here): masked copies go as cards, one value card each, ≤ MAX_CARDS
        self.assertEqual(len(items), len(values))
        self.assertEqual(skips, [])
        self.assertEqual(len(keys), len(values))
        self.assertEqual(asyncio.run(owner_route.offer_files(h, keys, "zh")), 3)
        self.assertEqual([r["value"] for r in box.reqs], values[:3])

    def test_strict_direction_unchanged_and_chunk_boundary(self):
        self.assertTrue(taskspec.secret_like('token="replace_me"'))
        doc = DOCUMENTS[0]
        self.put("out/large.md", (" " * (media.SCAN_SLICE - 9) + doc).encode())
        self.assertIsNone(self.m.check(media.Ref("out/large.md", "out/large.md", True), owner=True)[1])
        self.put("out/large.md", (" " * (media.SCAN_SLICE - 9) + "API_TOKEN=" + KEY).encode())
        self.assertEqual(self.m.check(media.Ref("out/large.md", "out/large.md", True), owner=True)[1], "key_content")

class OwnerMail(P184._Svc):
    async def test_owner_mail_attachments_and_real_key_refusal(self):
        self.mailbox()
        for i, doc in enumerate(DOCUMENTS):
            (self.work / f"report{i}.md").write_text(doc)
            res = await self.svc.agent_send({"to": [P184.OWNER], "subject": "report", "body": "附件", "files": [f"report{i}.md"]})
            self.assertTrue(res["ok"], res)
            msg = email.message_from_bytes(self.smtp.sent[-1][2], policy=email.policy.default)
            self.assertEqual(len(list(msg.iter_attachments())), 1)
        (self.work / "key.md").write_text("API_TOKEN=" + KEY)
        before = len(self.smtp.sent)
        res = await self.svc.agent_send({"to": [P184.OWNER], "subject": "report", "body": "附件", "files": ["key.md"]})
        # P573 (#386, replaces the refusal here): to the owner the masked copy is attached; the value never rides the mail
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(self.smtp.sent), before + 1)
        msg = email.message_from_bytes(self.smtp.sent[-1][2], policy=email.policy.default)
        [att] = list(msg.iter_attachments())
        self.assertNotIn(KEY.encode(), att.get_content() if isinstance(att.get_content(), bytes) else att.get_content().encode())
        self.assertNotIn(KEY.encode(), self.smtp.sent[-1][2])
        (self.work / "env.pem").write_text("x")
        res = await self.svc.agent_send({"to": [P184.OWNER], "subject": "report", "body": "附件", "files": ["env.pem"]})
        self.assertEqual((res["ok"], res["why"]), (False, "file"))
        self.assertIn("secret send", res["detail"])
