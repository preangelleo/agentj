"""P295 (Leo, 10-15 00:2x): 「转发邮件」 to his wife said a 15 KB report was 「太大，没有附上」 and she never got it.

Root cause (measured on the real file and the Agent's Gmail 「已发送」): the reply page's .md was attached to Leo's own copy;
the copy to someone else ran the strict scan, which read four notebookcheck.net review links (`Asus-ExpertBook-…-review`)
as high-entropy keys and held the file back — then the phone said 「太大」 for every file left out, whatever the reason, and
the outsider's mail said 「文件已不在」. Fixed: a title slug is words (privacy._sluglike); the phone hears the real reason
(skips + limit); the outsider's mail says only 「未附上」; mail_fwd logs recipient domains, our Message-ID and the SMTP
server's queue id; the same page to the same people again within DUP_WINDOW asks first; a Chinese reply's leading English
work notes stay out of the mail. Local fake SMTP only — nothing leaves this computer.
"""
import _hermetic  # noqa: F401,I001
import email
import email.policy
import json
import pathlib
import secrets
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import contacts_serve, diskq, privacy, taskspec  # noqa: E402
from test_p184_contacts import OWNER, _Svc  # noqa: E402

WIFE = "wife@example.org"
SLUGS = ["https://www.notebookcheck.net/Asus-ExpertBook-B9-OLED-laptop-review-Ultralight-business-champion.912345.0.html",
         "https://www.notebookcheck.net/Flagship-Lenovo-ThinkPad-X1-Carbon-G13-review-Lighter-faster-and-with-OLED.923456.0.html",
         "https://www.notebookcheck.com/Lenovo-Yoga-Slim-7i-Aura-Edition-im-Test-Lunar-Lake-mit-OLED.934567.0.html",
         "https://www.notebookcheck.net/Lenovo-ThinkPad-T14s-Gen-6-review-Snapdragon-X-Elite-business-laptop.945678.0.html"]


def report_md() -> str:
    rows = "\n".join(f"| 机型 {i} | 1.{i} kg | 来源：{SLUGS[i % 4]} |" for i in range(60))
    text = "# 轻薄本调研 2026-10\n\n| 机型 | 重量 | 来源 |\n|---|---|---|\n" + rows + "\n"
    return text + "补充说明。" * ((15_735 - len(text.encode())) // len("补充说明。".encode()))


class Slug(unittest.TestCase):
    def test_review_links_are_not_keys(self):
        self.assertFalse(taskspec.secret_like(report_md()), "notebookcheck title slugs are words")
        for u in SLUGS:
            self.assertEqual(privacy.redact(u), u)

    def test_real_keys_still_caught(self):
        for k in ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "Zx9" + secrets.token_urlsafe(32), "sk-ant-api03-" + "Ab9" * 30):
            self.assertTrue(taskspec.secret_like(f"token {k}"), k[:8])
        hits = sum(privacy._sluglike(secrets.token_urlsafe(n)) for n in (30, 40) for _ in range(20000))
        self.assertEqual(hits, 0, "a random base64url key is not a slug")


class Forward(_Svc):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.mailbox()
        (self.work / "laptop-research-2026-10.md").write_text(report_md())
        # no known key shape (the owner's copy carries it) but a random-looking token the strict scan holds back
        (self.work / "keys.md").write_text("部署笔记\n会话 Zx9kQ2mV8pL4rT7wY1nB6cF3hJ5sD0gA\n")
        reply = ("Need to create worktree first then relaunch P294.\n\n董事长，两件事一起报。\n\n"
                 "完整报告：[laptop-research-2026-10.md](laptop-research-2026-10.md)，笔记 [keys.md](keys.md)")
        items, _ = self.media.scan(reply, 8)
        self.pages[8] = {"id": 8, "end": "done", "reply": {"text": reply}, "media": items}
        self.smtp.data_reply = b"250 2.0.0 OK  1791991713 d2e1a3b4c5f6.123-gsmtp"

    def mails(self):
        return [email.message_from_bytes(raw, policy=email.policy.default) for _, _, raw in self.smtp.sent]

    def logs(self):
        diskq.flush()
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines() if '"mail_fwd"' in x]

    async def test_small_report_goes_to_someone_else(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8})
        self.assertTrue(res["ok"])
        self.assertEqual((res["sent"], res["files"]), (1, 1))
        self.assertEqual(res["skips"], [{"name": "keys.md", "bytes": (self.work / "keys.md").stat().st_size, "why": "secret"}],
                         "the owner's phone hears the real reason; the 15 KB report is attached")
        self.assertEqual(res["limit"], contacts_serve.FILES_MAX)
        msg = self.mails()[-1]
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["laptop-research-2026-10.md"])
        plain = msg.get_body(("plain",)).get_content()
        self.assertIn("keys.md", plain)
        self.assertNotIn("文件太大", plain)
        self.assertNotIn("文件已不在", plain, "an outsider's mail never says why — and never says it is gone when it is not")
        self.assertIn("未附上", plain)

    async def test_owner_copy_attaches_both(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 8})
        self.assertEqual((res["ok"], res["files"]), (True, 2))
        self.assertNotIn("skips", res)

    async def test_too_big_says_size_and_limit(self):
        old = contacts_serve.FILES_MAX
        contacts_serve.FILES_MAX = 10_000
        self.addCleanup(setattr, contacts_serve, "FILES_MAX", old)
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8})
        big = [s for s in res["skips"] if s["why"] == "too_big"]
        self.assertEqual(big, [{"name": "laptop-research-2026-10.md", "bytes":
                                (self.work / "laptop-research-2026-10.md").stat().st_size, "why": "too_big"}])
        self.assertEqual(res["limit"], 10_000)
        self.assertIn("文件太大未附上", self.mails()[-1].get_body(("plain",)).get_content())

    async def test_work_notes_stay_out(self):
        await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8})
        msg = self.mails()[-1]
        for part in (str(msg["Subject"]), msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content()):
            self.assertNotIn("Need to create worktree", part)
        self.assertIn("董事长，两件事一起报", msg.get_body(("plain",)).get_content())

    def test_drop_notes_only_leading_english_sentences(self):
        svc = self.svc
        self.assertEqual(svc.drop_notes("Let me check the logs first.\n\n日志没问题。"), "日志没问题。")
        for keep in ("https://agentj.app/docs is the page.\n\n见上。", "All done here.", "OK.\n\n好的。",
                     "Need to check this first.\n\nAll good, nothing else."):
            self.assertEqual(svc.drop_notes(keep), keep, keep)
        self.host.lang = "en"
        self.assertEqual(svc.drop_notes("Let me check the logs first.\n\n日志没问题。"), "Let me check the logs first.\n\n日志没问题。")

    async def test_double_tap_asks_first(self):
        first = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8})
        again = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8})
        self.assertEqual((first["ok"], again["ok"], again["why"]), (True, False, "dup"))
        self.assertGreaterEqual(again["ago"], 0)
        self.assertEqual(len(self.smtp.sent), 1, "the second tap sent nothing")
        other = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 8})
        self.assertTrue(other["ok"], "someone else (Leo himself) is not a duplicate")
        yes = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8, "again": True})
        self.assertTrue(yes["ok"])
        self.assertEqual(len(self.smtp.sent), 3)
        self.svc.recent = {k: v - contacts_serve.DUP_WINDOW for k, v in self.svc.recent.items()}
        self.assertTrue((await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}], "turn": 8}))["ok"], "after the window: no ask")

    async def test_log_has_domains_ids_and_counts_never_an_address(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}, {"addr": OWNER}], "turn": 8})
        self.assertNotIn("_log", res, "the log fields never travel to the phone")
        rec = self.logs()[-1]
        self.assertEqual((rec["result"], rec["to"], rec["files"], rec["skipped"], rec["refused"]), ("ok", 2, 1, 1, 0))
        self.assertEqual(rec["domains"], ["corp.example", "example.org"])
        self.assertEqual(rec["smtp"], "d2e1a3b4c5f6.123-gsmtp")
        self.assertEqual(rec["msgid"], str(self.mails()[-1]["Message-ID"]).strip("<>"))
        line = self.st.log_path.read_text()
        self.assertNotIn(WIFE, line)
        self.assertNotIn(OWNER, line)

    async def test_refused_recipient_is_reported(self):
        self.smtp.refuse = {WIFE}
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": WIFE}, {"addr": OWNER}], "turn": 8})
        self.assertEqual((res["ok"], res["sent"], res["refused"]), (True, 1, 1))
        self.assertEqual(self.smtp.sent[-1][1], [OWNER])


if __name__ == "__main__":
    unittest.main()
