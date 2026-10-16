"""P355 (Leo, 10-16): `agentj mail send` sends formatted mail, Cc and Bcc.

- default Markdown → mail_template's look (title + body + footer, no 「你问 / Agent J 答」) as multipart/alternative with a
  plain-text twin; `--format html` / an .html body file → the page as is, scripts / forms / on* / javascript: out, CSS
  custom properties resolved and inlined (Gmail), local images → alt text, a plain-text copy; `--format text` → plain only;
- an outsider's HTML mail is masked text node by text node, the CSS stays intact;
- --cc / --bcc: Cc is a header, Bcc only the envelope; every recipient counts for the no-card rule (owner / allowlist) and
  for the 10-recipient ceiling; the card shows 抄送 / 密送;
- mail_template.render_send is what agent_send actually calls (P189's template must not go dead again).
Local fake SMTP only (tests/mailfake.py).
"""
import _hermetic  # noqa: F401,I001
import contextlib
import email
import email.policy
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import mail as M, mail_html as MH, mail_template as MT  # noqa: E402
from test_p194_owner_direct import ALLOW_ME, _Svc  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

FRIEND = "friend@partner.example"
OTHER = "stranger@else.example"
MD = "## 今日\n- 销售 **1,200** 元\n- [看板](https://example.com/d)\n\n| 平台 | 单量 |\n|--|--:|\n| 淘宝 | 12 |"
REPORT = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'"><title>周报</title>
<style>:root{--bg:#faf9f5;--ink:#141414;--line:#e3dacc;--font:"Ubuntu",sans-serif}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 var(--font)}
h1{font-size:clamp(30px,5vw,48px)}.card{border:1px solid var(--line);padding:12px}.card p{color:var(--ink)}
.wrap > .card{margin:8px 0}a:hover{color:red}@media (max-width:640px){body{font-size:15px}}</style>
<script>alert(1)</script><script type="application/ld+json">{"a":1}</script><link rel="stylesheet" href="x.css"></head>
<body><main class="wrap"><h1 onclick="steal()">周报</h1><div class="card"><p>电话 13812345678 &amp; 总结</p>
<img src="assets/chart.png" alt="销售图"><img src="https://example.com/a.png" alt="ok">
<a href="javascript:alert(1)">坏链接</a> <a href="https://example.com/r">报告</a></div>
<form action="https://evil.example/x"><input name="pw"><button>提交</button></form>
<iframe src="https://evil.example"></iframe></main></body></html>"""


def parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


class Template(unittest.TestCase):
    def test_markdown_becomes_the_template_mail(self):
        text, page, _ = MT.render_send(lang="zh", agent="小J", subject="日报 10-16", body=MD)
        self.assertIn("<h1", page)
        self.assertIn("日报 10-16", page)
        self.assertIn("<strong>1,200</strong>", page)
        self.assertIn('<table', page)
        self.assertIn('href="https://example.com/d"', page)
        self.assertIn("Powered by Agent J", page)
        self.assertIn("这封邮件由 小J 发出。", page)
        for chip in ("你问", "答</span>"):
            self.assertNotIn(chip, page, "the send mail has no question / answer pair")
        self.assertTrue(text.startswith("## 今日") and "Powered by Agent J" in text, text)
        pre = page.split('opacity:0;', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("**", pre, "the inbox preview shows words, not Markdown marks")
        import re
        colours = set(c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b", page))
        self.assertLessEqual(colours, set(MT.PALETTE), "only Leo's palette")

    def test_markdown_escapes_html_in_the_body(self):
        _, page, _ = MT.render_send(lang="en", agent="", subject="<b>x</b>", body="<script>alert(1)</script>")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_text_has_no_html_part(self):
        self.assertEqual(MT.render_send(lang="zh", agent="J", subject="s", body="plain", fmt="text"), ("plain", None, {}))

    def test_html_is_cleaned_and_inlined(self):
        text, page, info = MT.render_send(lang="zh", agent="J", subject="s", body=REPORT, fmt="html")
        low = page.lower()
        for bad in ("<script", "alert(1)", "onclick", "javascript:", "<form", "<input", "<iframe", "evil.example",
                    "http-equiv", "<link", "var(", "clamp("):
            self.assertNotIn(bad, low, bad)
        self.assertIn("提交", page, "a button's words stay")
        self.assertIn("[销售图]", page, "a local image becomes its alt text")
        self.assertIn('src="https://example.com/a.png"', page)
        self.assertIn('href="https://example.com/r"', page)
        self.assertEqual(info["local_images"], 1)
        self.assertIn('style="margin:0;background:#faf9f5;color:#141414;', page, "body rules inlined, vars resolved")
        self.assertIn('class="aj-body"', page, "body style repeated on a wrapper div (Gmail drops <body>)")
        self.assertIn("font-size:30px", page, "clamp() → its minimum")
        self.assertRegex(page, r'<div class="card" style="[^"]*border:1px solid #e3dacc;padding:12px;margin:8px 0;')
        self.assertRegex(page, r'<p style="color:#141414;">电话')
        self.assertIn("@media (max-width:640px)", page, "the sheet stays for clients that honour it")
        self.assertNotIn("<main", page, "Gmail drops sectioning tags with their style → div")
        self.assertIn('<div class="wrap"', page)
        self.assertIn("Powered by Agent J", page)
        self.assertIn("电话 13812345678 & 总结", text)
        self.assertNotIn("alert", text)

    def test_outsider_html_is_masked_in_text_only(self):
        from agentj.privacy import redact
        text, page, _ = MT.render_send(lang="zh", agent="J", subject="s", body=REPORT, fmt="html", text_fn=redact)
        self.assertNotIn("13812345678", page)
        self.assertNotIn("13812345678", text)
        self.assertIn("#faf9f5", page, "CSS untouched")

    def test_looks_html(self):
        self.assertTrue(MH.looks_html("  <!DOCTYPE html><html>"))
        self.assertTrue(MH.looks_html("<html lang=zh>"))
        self.assertFalse(MH.looks_html("# 标题\n<b>x</b>"))


class Send(_Svc):
    def allow(self, *rows):
        cfg = M.read_config(self.st)
        cfg["allow"] = cfg["allow"] + [{"addr": a, "role": "guest"} for a in rows]
        M.save_config(self.st, M.validate(cfg))

    def last(self):
        return parse(self.smtp.sent[-1][2])

    async def test_default_is_formatted_with_a_plain_twin(self):
        with mock.patch.object(MT, "render_send", wraps=MT.render_send) as spy:
            res = await self.svc.agent_send({"to_owner": True, "subject": "日报", "body": MD})
        self.assertTrue(res["ok"] and res["format"] == "markdown", res)
        spy.assert_called_once()                       # P189's template is what send uses
        m = self.last()
        self.assertEqual(m.get_content_type(), "multipart/alternative")
        self.assertIn("<strong>1,200</strong>", m.get_body(("html",)).get_content())
        self.assertIn("**1,200**", m.get_body(("plain",)).get_content())

    async def test_html_body_goes_as_html(self):
        res = await self.svc.agent_send({"to_owner": True, "subject": "周报", "body": REPORT, "format": "html"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["local_images"], 1)
        h = self.last().get_body(("html",)).get_content()
        self.assertNotIn("<script", h.lower())
        self.assertIn("#faf9f5", h)
        self.assertIn("13812345678", h, "to the owner: as written")
        self.assertIn("总结", self.last().get_body(("plain",)).get_content())

    async def test_text_stays_plain(self):
        await self.svc.agent_send({"to_owner": True, "subject": "s", "body": "**x**", "format": "text"})
        m = self.last()
        self.assertEqual(m.get_content_type(), "text/plain")
        self.assertEqual(m.get_content().strip(), "**x**")

    async def test_bad_format(self):
        res = await self.svc.agent_send({"to_owner": True, "subject": "s", "body": "b", "format": "pdf"})
        self.assertEqual((res["ok"], res["why"]), (False, "format"))

    async def test_cc_is_a_header_bcc_only_the_envelope(self):
        self.allow(FRIEND, OTHER)
        res = await self.svc.agent_send({"to_owner": True, "cc": [FRIEND], "bcc": [OTHER], "subject": "s", "body": "b"})
        self.assertTrue(res["ok"] and res["no_card"], res)
        self.assertEqual((res["cc"], res["bcc"]), ([FRIEND], [OTHER]))
        raw = self.smtp.sent[-1][2]
        m = parse(raw)
        self.assertEqual(m["To"], ALLOW_ME)
        self.assertEqual(m["Cc"], FRIEND)
        self.assertIsNone(m["Bcc"])
        self.assertNotIn(OTHER.encode(), raw, "Bcc never in the message")
        self.assertEqual(sorted(self.smtp.sent[-1][1]), sorted([ALLOW_ME, FRIEND, OTHER]))
        self.ask.assert_not_called()

    async def test_an_outsider_on_cc_or_bcc_brings_the_card(self):
        for key in ("cc", "bcc"):
            self.ask.reset_mock()
            res = await self.svc.agent_send({"to_owner": True, key: [OTHER], "subject": "s", "body": "电话 13812345678"})
            self.assertTrue(res["ok"] and not res["no_card"] and not res["owner_direct"], res)
            self.ask.assert_awaited_once()
            self.assertEqual(self.ask.await_args.kwargs.get("allow_offer"), [OTHER])
            summary = self.ask.await_args.args[1]["summary"]
            line = next(x for x in summary.splitlines() if x.startswith("抄送：" if key == "cc" else "密送："))
            self.assertIn(OTHER, line)   # the second round shows the name it was remembered under
            self.assertIn("格式：带格式", summary)
            self.assertNotIn("13812345678", self.last().get_body(("html",)).get_content(), "outward: masked")

    async def test_one_address_one_place_and_ten_in_all(self):
        self.allow(FRIEND)
        res = await self.svc.agent_send({"to": [FRIEND], "cc": [FRIEND], "bcc": [FRIEND], "subject": "s", "body": "b"})
        self.assertEqual(res["sent"], [FRIEND])
        self.assertIsNone(self.last()["Cc"])
        many = [f"p{i}@x.example" for i in range(6)]
        res = await self.svc.agent_send({"to": many, "cc": [f"c{i}@x.example" for i in range(5)], "subject": "s", "body": "b"})
        self.assertEqual((res["ok"], res["why"]), (False, "to"), res)
        res = await self.svc.agent_send({"to": [FRIEND], "bcc": [f"c{i}@x.example" for i in range(11)], "subject": "s", "body": "b"})
        self.assertEqual(res["why"], "to")

    async def test_html_card_shows_text_not_tags(self):
        await self.svc.agent_send({"to": [OTHER], "subject": "s", "body": REPORT, "format": "html"})
        summary = self.ask.await_args.args[1]["summary"]
        self.assertIn("格式：HTML", summary)
        self.assertNotIn("<style", summary)
        self.assertIn("周报", summary)


class Cli(unittest.TestCase):
    def run_cli(self, argv):
        from agentj import cli
        outb = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="p355c-", dir=TEST_TMP) as d, \
                mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(pathlib.Path(d) / "s")}), \
                contextlib.redirect_stdout(outb), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch("agentj.elevate.client_request", return_value={"ok": True}) as cr, \
                mock.patch.object(sys, "argv", ["agentj", *argv]):
            f = pathlib.Path(d) / "r.html"
            f.write_text(REPORT, encoding="utf-8")
            argv[:] = [str(f) if x == "@html" else x for x in argv]
            with mock.patch.object(sys, "argv", ["agentj", *argv]):
                with contextlib.suppress(SystemExit):
                    cli.main()
        return cr.call_args.args[1]

    def test_default_markdown_and_cc_bcc_pass_through(self):
        req = self.run_cli(["mail", "send", "--to-owner", "--cc", "王总", "--cc", "b@x.example", "--bcc", "c@x.example",
                            "--subject", "s", "--body", "**b**", "--json"])
        self.assertEqual((req["format"], req["cc"], req["bcc"]), ("markdown", ["王总", "b@x.example"], ["c@x.example"]))

    def test_html_file_picks_html(self):
        self.assertEqual(self.run_cli(["mail", "send", "--to-owner", "--subject", "s", "--body-file", "@html", "--json"])["format"],
                         "html")

    def test_explicit_format_wins(self):
        req = self.run_cli(["mail", "send", "--to-owner", "--subject", "s", "--body-file", "@html", "--format", "text", "--json"])
        self.assertEqual(req["format"], "text")


if __name__ == "__main__":
    unittest.main()
