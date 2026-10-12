"""P189: 「转发到邮箱」 from the reply card — the whole turn (input + reply) as one mail, HTML + plain text, local fake SMTP only.

Serve side (contacts_serve.forward): the input that started the page (sender remote, time, quote, files sent with it) is
on top unless `input: false`; subject 「[Agent J] <the input's first 40 characters>」 (the reply's first line without the
input); the reply's files attached within FILES_MAX and the rest listed 「文件太大未附上」 (never read when over); redaction.
Template (mail_template): Markdown → mail HTML (headings, nested lists, ordered start, tables with alignment, fenced code,
quotes, links — only http(s) / mailto), every text escaped, only Leo's palette colours, table layout + inline styles,
dark-mode hooks, footer 「Powered by Agent J」 → agentj.app; zh / en.
"""
import _hermetic  # noqa: F401,I001
import email
import email.policy
import pathlib
import re
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import contacts_serve, mail_template as MT  # noqa: E402
from test_p184_contacts import OWNER, _Svc  # noqa: E402

QUESTION = "帮我把这周的竞品动态整理成一张表，按重要性排序，顺便列出下周要盯的三件事，再附上原始报告"
SRC = {"k": "phone", "dev": "dev1", "name": "Leo 的 iPhone", "text": QUESTION,
       "quote": {"id": 6, "who": "Agent J", "ts": 1, "text": "上周的简报在这里", "ex": False},
       "att": [{"name": "截图.png", "mime": "image/png", "bytes": 1200, "kind": "image"}]}
REPLY = ("## 本周竞品\n| 公司 | 动态 | 重要性 |\n|---|---|:-:|\n| A | 发布 2.0 | 高 |\n| B | 降价 <script>alert(1)</script> | 中 |\n\n"
         "下周要盯：\n1. 定价\n2. 渠道\n   - 线上\n   - 线下\n3. 招聘\n\n```bash\nagentj mail send --to 张三\n```\n"
         "> 结论：**保持节奏**，见 [报告](javascript:alert(1)) 和 https://agentj.app/docs/email/。\n\n原始报告见 report.pdf")


class Forward(_Svc):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pages[7].update(src=SRC, ts=1760274300000)
        self.pages[7]["reply"]["text"] = REPLY
        self.mailbox()

    def last(self):
        frm, to, raw = self.smtp.sent[-1]
        return email.message_from_bytes(raw, policy=email.policy.default)

    async def test_whole_turn_html_and_text(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7})
        self.assertEqual((res["ok"], res["sent"], res["files"]), (True, 1, 2))
        self.assertNotIn("skipped", res)
        msg = self.last()
        self.assertEqual(str(msg["Subject"]), "[Agent J] " + QUESTION[:40] + "…")
        plain = msg.get_body(("plain",)).get_content()
        html = msg.get_body(("html",)).get_content()
        for s in ("你问", "来自 Leo 的 iPhone", "引用 Agent J：上周的简报在这里", QUESTION, "Agent J 答", "本周竞品", "截图.png",
                  "Powered by Agent J · https://agentj.app"):
            self.assertIn(s, plain, s)
        self.assertLess(plain.index(QUESTION), plain.index("本周竞品"), "input above the reply")
        for s in ("你问", "来自 Leo 的 iPhone", "Agent J 答", "<table", "<th ", "<ol", "<ul", "<pre", "<blockquote",
                  'href="https://agentj.app"', "Powered by Agent J", "report.pdf", "people.vcf", "prefers-color-scheme:dark"):
            self.assertIn(s, html, s)
        self.assertNotIn("<script>", html, "the reply's own text is escaped")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('href="javascript:', html, "only http(s) / mailto links")
        self.assertIn('href="https://agentj.app/docs/email/"', html)
        self.assertEqual(sorted(p.get_filename() for p in msg.iter_attachments()), ["people.vcf", "report.pdf"])
        self.assertEqual(msg["X-AgentJ"], "forward")

    async def test_without_the_input(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7, "input": False})
        self.assertTrue(res["ok"])
        msg = self.last()
        plain, html = msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content()
        for part in (str(msg["Subject"]), plain, html):
            self.assertNotIn("竞品动态整理", part, "the input is left out everywhere, the subject too")
            self.assertNotIn("Leo 的 iPhone", part)
        self.assertEqual(str(msg["Subject"]), "[Agent J] 本周竞品")
        self.assertNotIn("你问", html)

    async def test_too_big_files_are_listed_not_read(self):
        old = contacts_serve.FILES_MAX
        contacts_serve.FILES_MAX = 2030          # report.pdf (2009 B) fits, people.vcf (52 B) then does not
        self.addCleanup(setattr, contacts_serve, "FILES_MAX", old)
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7})
        self.assertEqual((res["ok"], res["files"], res["skipped"]), (True, 1, 1))
        msg = self.last()
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["report.pdf"])
        self.assertIn("people.vcf", msg.get_body(("plain",)).get_content())
        self.assertIn("文件太大未附上", msg.get_body(("plain",)).get_content())
        self.assertIn("文件太大未附上", msg.get_body(("html",)).get_content())

    async def test_secrets_in_the_input_are_redacted(self):
        self.pages[7]["src"] = {**SRC, "text": "key 是 sk-ant-api03-" + "A" * 90 + " 帮我看看"}
        await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7})
        msg = self.last()
        for part in (str(msg["Subject"]), msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content()):
            self.assertNotIn("A" * 40, part)


class Template(unittest.TestCase):
    def render(self, **kw):
        args = {"lang": "zh", "agent": "Agent J", "src": dict(SRC, ts=1760274300000), "reply": REPLY, "files": [("a.pdf", 2048)],
                "skipped": [("big.zip", 41943040, "too_big"), ("old.txt", 10, "gone")]}
        args.update(kw)
        return MT.render(**args)

    def test_only_palette_colours(self):
        _, _, html = self.render()
        used = {c.lower() for c in re.findall(r"(?<![&\w])#[0-9a-fA-F]{6}\b", html)}
        self.assertTrue(used)
        self.assertLessEqual(used, set(MT.PALETTE), used - set(MT.PALETTE))
        # Leo's closed palette (leo-design-language references/leo-palette.json, 37 colours, 2026-10-12)
        leo = {"#141414", "#2c2b26", "#629887", "#6a9bcc", "#71bbde", "#78dedc", "#827dbd", "#858585", "#888888", "#acacac",
               "#b7e396", "#c1e1f0", "#c4f0f0", "#c7dedc", "#cacadb", "#d1a7e8", "#d97757", "#dbb293", "#dff3d1", "#e3dacc",
               "#e5e5e5", "#ebc9b7", "#ebd8f5", "#ededed", "#efddcf", "#f0aad4", "#f0eee6", "#f2f2f2", "#f7827e", "#f7e17c",
               "#f8daec", "#faf9f5", "#fbc8c6", "#fbf2c5", "#fcbe7c", "#fee2c5", "#ffe292"}
        self.assertLessEqual(set(MT.PALETTE), leo)
        self.assertNotIn("#ffffff", html.lower())
        self.assertNotIn("#000000", html.lower())

    def test_mail_client_shape(self):
        _, _, html = self.render()
        self.assertIn('<meta name="color-scheme" content="light dark">', html)
        self.assertIn('role="presentation"', html)
        self.assertIn('width="600"', html)
        self.assertIn("[data-ogsc]", html, "Outlook.com dark hooks")
        self.assertNotIn("<link", html, "no external stylesheet or web font")
        self.assertNotIn("<img", html, "no remote image: nothing to block, nothing that tracks")
        self.assertNotIn("display:flex", html)
        self.assertNotIn("display:grid", html)

    def test_markdown_blocks(self):
        h = MT.markdown("1. a\n2. b\n   - c\n   - d\n3. e")
        self.assertEqual(h.count("<ol"), 1)
        self.assertEqual(h.count("<ul"), 1)
        self.assertLess(h.index("<ul"), h.index("e</li>"), "the nested list is inside item 2")
        self.assertIn('start="4"', MT.markdown("4. four\n5. five"))
        t = MT.markdown("| a | b |\n|:-|-:|\n| 1 | 2 |")
        self.assertIn('text-align:left;white-space:nowrap">a', t)
        self.assertIn('text-align:right;white-space:nowrap">2', t)
        c = MT.markdown("```\n<b>x</b>\n  indented\n```")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;\n  indented", c)
        self.assertIn("<h2", MT.markdown("## T"))
        self.assertIn("<hr", MT.markdown("a\n\n---\n\nb"))
        i = MT.markdown("**b** *i* ~~s~~ `c` [x](mailto:a@b.c) [y](ftp://z) snake_case_word")
        for s in ("<strong>b</strong>", "<em>i</em>", "<s>s</s>", ">c</code>", 'href="mailto:a@b.c"', "snake_case_word"):
            self.assertIn(s, i, s)
        self.assertNotIn("ftp://", i.split("y")[0])

    def test_subject_and_text_and_english(self):
        s, text, html = self.render(lang="en", agent="Jarvis")
        self.assertTrue(s.startswith("[Agent J] 帮我把"))
        self.assertLessEqual(len(s), len("[Agent J] ") + 41)
        self.assertIn("Jarvis answered", text)
        self.assertIn("You asked", html)
        self.assertIn("Too big, not attached", text)
        self.assertIn("No longer there, not attached", text)
        self.assertIn('lang="en"', html)
        self.assertEqual(MT.subject("  短\n问题  ", "x"), "[Agent J] 短 问题")
        self.assertEqual(MT.subject(None, "\n## 标题行\n正文"), "[Agent J] 标题行")
        s2, _, h2 = self.render(src={"k": "task", "text": "每周简报", "ts": 1})
        self.assertIn("定时任务", h2)
        self.assertEqual(s2, "[Agent J] 每周简报")

    def test_deterministic(self):
        self.assertEqual(self.render(), self.render())


if __name__ == "__main__":
    unittest.main()
