"""P184: the Agent-kept address book, the contact card, 「转发到邮箱」 and `agentj mail send` — local fake SMTP only
(tests/mailfake.py), never a real mailbox.

Store: vCard 2.1 / 3.0 / 4.0, Google (old + new), Outlook, Chinese (GBK) CSV, JSON in; vCard / CSV / JSON out; dedupe by e-mail /
phone, same-name rules; 0600 file in a 0700 folder. Search: homophones (张珊 → 张三), polyphone surnames (单 shan/dan), initials,
fuzzy pinyin (n/l, f/h, -ng), Latin typos, e-mail prefixes, phone digits; sorts recent / frequent / name.
Serve side: the phone's list (with the owner's own addresses and whether mail can go out), 新增, forward of a reply page with
its files and of one file, counting + remembering recipients, refusals (no mailbox, bad address, rate); the contact card
(save / validation / cancel / update); the Agent's mail: unknown names refused, owner approval first, files only from its
folder, recipients remembered with source agent. Item 6: the new file-card kinds and inbox types.
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import argparse
import asyncio
import email
import email.policy
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import contacts as C, mail as M, media as media_mod, inbox  # noqa: E402
from agentj.contacts_serve import ContactService  # noqa: E402
from agentj.state import State  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402

AGENT = "agent@example.cn"
CODE = "right-code"
OWNER = "leo@corp.example"

APPLE_VCF = """BEGIN:VCARD\r
VERSION:3.0\r
N:张;三;;;\r
FN:张三\r
NICKNAME:张总,老张\r
ORG:宏远科技;销售部\r
TITLE:总经理\r
item1.EMAIL;type=INTERNET;type=WORK;type=pref:zhangsan@hongyuan.cn\r
EMAIL;type=INTERNET;type=HOME:zs1980@qq.com\r
TEL;type=CELL;type=VOICE;type=pref:+86 138-0013-8000\r
item2.ADR;type=WORK:;;科技园 8 栋\\, 3 层;深圳;广东;518000;中国\r
NOTE:去年在展会上认识\\n喜欢喝茶\r
END:VCARD\r
BEGIN:VCARD\r
VERSION:4.0\r
FN:John Smith\r
EMAIL;TYPE=work:john.smith@example.com\r
TEL;VALUE=uri;TYPE="voice,cell":tel:+1-555-555-0100\r
END:VCARD\r
"""
V21_QP = ("BEGIN:VCARD\nVERSION:2.1\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E6=9D=8E;=E5=9B=9B;;;\n"
          "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E6=9D=8E=E5=9B=9B\nTEL;CELL;PREF:13900001111\nEND:VCARD\n")
GOOGLE_NEW = ("First Name,Middle Name,Last Name,Nickname,Notes,E-mail 1 - Label,E-mail 1 - Value,E-mail 2 - Label,E-mail 2 - Value,"
              "Phone 1 - Label,Phone 1 - Value,Address 1 - Label,Address 1 - Formatted,Organization Name,Organization Title\n"
              "三,,张,,,* Work,zhangsan@hongyuan.cn,,,Mobile,+8613800138000,,,,\n"
              "Wei,,Wang,Vivian,VIP,* Home,vivian@mail.example ::: wangwei@work.cn,,,Mobile,+1 415 555 0101,Home,\"1 Main St\nSF\",Acme,CFO\n")
GOOGLE_OLD = ("Name,Given Name,Family Name,Nickname,E-mail 1 - Type,E-mail 1 - Value,Phone 1 - Type,Phone 1 - Value\n"
              "赵六,六,赵,小六,* Other,zhaoliu@163.com,Mobile,13700001234\n")
OUTLOOK = ("First Name,Middle Name,Last Name,Title,Suffix,Nickname,E-mail Address,E-mail 2 Address,E-mail 3 Address,Home Phone,"
           "Mobile Phone,Business Phone,Business Street,Business City,Business Postal Code,Business Country/Region,Company,Job Title,Notes\n"
           "Anna,,Lee,,,,anna@contoso.com,,,,(425) 555-0102,,1 Microsoft Way,Redmond,98052,USA,Contoso,PM,\n")
CHINESE_GBK = "姓名,昵称,电子邮件,手机,公司,备注\n钱七,七哥,qianqi@sina.com,13600005678,七号铺子,老同学\n".encode("gb18030")


def book(*rows):
    b = {"v": 1, "contacts": []}
    for r in rows:
        C.add(b, r)
    return b


class _St(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p184-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init()


class Store(_St):
    def test_vcard_apple_and_v4_and_v21_quoted_printable(self):
        res = C.import_data(self.st, APPLE_VCF.encode(), "contacts.vcf")
        self.assertEqual((res["format"], res["added"]), ("vcf", 2))
        b = C.read(self.st)
        z = C.by_email(b, "ZhangSan@HongYuan.cn")
        self.assertEqual(z["name"], "张三")
        self.assertEqual(z["nicks"], ["张总", "老张"])
        self.assertEqual([e["v"] for e in z["emails"]], ["zhangsan@hongyuan.cn", "zs1980@qq.com"])
        self.assertEqual(z["emails"][0]["label"], "work")
        self.assertEqual(z["phones"][0]["v"], "+86 138-0013-8000")
        self.assertIn("科技园 8 栋, 3 层", z["addresses"][0]["v"])
        self.assertEqual((z["org"], z["title"]), ("宏远科技 销售部", "总经理"))
        self.assertEqual(z["note"], "去年在展会上认识\n喜欢喝茶")
        self.assertEqual(z["source"], "import")
        j = C.by_email(b, "john.smith@example.com")
        self.assertEqual(j["phones"][0]["v"], "+1-555-555-0100")
        res = C.import_data(self.st, V21_QP.encode(), "old.vcf")
        self.assertEqual(res["added"], 1)
        self.assertEqual(C.find(C.read(self.st), "李四")[0][1]["phones"][0]["v"], "13900001111")

    def test_csv_google_outlook_chinese_and_json(self):
        r = C.import_data(self.st, GOOGLE_NEW.encode("utf-8-sig"), "google.csv")
        self.assertEqual((r["format"], r["added"]), ("csv", 2))
        w = C.by_email(C.read(self.st), "wangwei@work.cn")
        self.assertEqual((w["name"], w["nicks"], w["org"], w["title"]), ("Wei Wang", ["Vivian"], "Acme", "CFO"))
        self.assertEqual([e["v"] for e in w["emails"]], ["vivian@mail.example", "wangwei@work.cn"])
        self.assertEqual(w["addresses"][0]["v"], "1 Main St, SF")
        self.assertEqual(C.by_email(C.read(self.st), "zhangsan@hongyuan.cn")["name"], "张三")
        r = C.import_data(self.st, GOOGLE_OLD.encode(), "contacts.csv")
        z = C.by_email(C.read(self.st), "zhaoliu@163.com")
        self.assertEqual((r["added"], z["name"], z["nicks"], z["phones"][0]["label"]), (1, "赵六", ["小六"], "mobile"))
        r = C.import_data(self.st, OUTLOOK.encode(), "outlook.csv")
        a = C.by_email(C.read(self.st), "anna@contoso.com")
        self.assertEqual((a["name"], a["org"], a["phones"][0]["v"], a["phones"][0]["label"]), ("Anna Lee", "Contoso", "(425) 555-0102", "cell"))
        self.assertIn("Redmond", a["addresses"][0]["v"])
        r = C.import_data(self.st, CHINESE_GBK, "qq导出.csv")
        q = C.by_email(C.read(self.st), "qianqi@sina.com")
        self.assertEqual((q["name"], q["nicks"], q["org"], q["note"]), ("钱七", ["七哥"], "七号铺子", "老同学"))
        r = C.import_data(self.st, json.dumps([{"name": "孙八", "email": "sunba@x.cn", "phone": "13500000000"}]).encode(), "a.json")
        self.assertEqual((r["format"], r["added"]), ("json", 1))

    def test_dedupe_merge_rules_and_dry_run(self):
        C.import_data(self.st, APPLE_VCF.encode(), "a.vcf")
        # the same person from Google: same e-mail → merged (phone with +86 = without), nothing duplicated
        r = C.import_data(self.st, GOOGLE_NEW.encode(), "g.csv", dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertEqual(len(C.read(self.st)["contacts"]), 2, "dry run writes nothing")
        r = C.import_data(self.st, GOOGLE_NEW.encode(), "g.csv")
        self.assertEqual((r["added"], r["merged"] + r["same"]), (1, 1))
        z = C.by_email(C.read(self.st), "zhangsan@hongyuan.cn")
        self.assertEqual(len(z["phones"]), 1, "+8613800138000 == +86 138-0013-8000")
        b = book({"name": "张伟", "email": "zw1@a.cn"})
        _, how = C.add(b, {"name": "张伟", "email": "zw2@b.cn"})
        self.assertEqual((how, len(b["contacts"])), ("added", 2), "two 张伟 with different e-mails stay two people")
        _, how = C.add(b, {"name": "李雷", "phone": "13800000001"})
        _, how = C.add(b, {"name": "李雷", "email": "lilei@x.cn"})
        self.assertEqual(how, "merged", "same name, nothing to tell them apart → one person")
        with self.assertRaises(C.ContactError) as e:
            C.add(b, {"name": "只有名字"})
        self.assertEqual(e.exception.why, "no_contact")
        # another spelling of the name the owner used becomes a nickname
        o, how = C.add(b, {"name": "Lei Li", "email": "lilei@x.cn"})
        self.assertIn("Lei Li", o["nicks"])

    def test_export_round_trips_and_file_modes(self):
        C.import_data(self.st, APPLE_VCF.encode(), "a.vcf")
        C.import_data(self.st, GOOGLE_NEW.encode(), "g.csv")
        b = C.read(self.st)
        vcf = C.export_text(b, "vcf")
        self.assertIn("VERSION:3.0", vcf)
        self.assertTrue(all(len(line.encode()) <= 75 for line in vcf.split("\r\n")), "folded at 75 octets")
        again = {"v": 1, "contacts": []}
        for r in C.parse_vcard(vcf):
            C.add(again, r)
        self.assertEqual(sorted(c["name"] for c in again["contacts"]), sorted(c["name"] for c in b["contacts"]))
        self.assertEqual(C.by_email(again, "zs1980@qq.com")["note"], "去年在展会上认识\n喜欢喝茶")
        csv_text = C.export_text(b, "csv")
        self.assertTrue(csv_text.startswith("﻿Name,"))
        again2 = {"v": 1, "contacts": []}
        for r in C.parse_csv(csv_text.lstrip("﻿")):
            C.add(again2, r)
        self.assertEqual(len(again2["contacts"]), len(b["contacts"]))
        self.assertEqual({e["v"] for e in C.by_email(again2, "vivian@mail.example")["emails"]}, {"vivian@mail.example", "wangwei@work.cn"})
        self.assertIn("VERSION:4.0", C.export_text(b, "vcf", "4.0"))
        d = pathlib.Path(self.st.root) / "contacts"
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((d / "contacts.json").stat().st_mode), 0o600)

    def test_record_sent_remembers_and_counts(self):
        out = C.record_sent(self.st, [{"addr": "New.Person@x.cn", "name": "新同事"}, "zhangsan@hongyuan.cn"], "agent")
        self.assertEqual([x["added"] for x in out], [True, True])
        C.record_sent(self.st, ["new.person@x.cn"], "phone")
        b = C.read(self.st)
        n = C.by_email(b, "new.person@x.cn")
        self.assertEqual((n["name"], n["source"], n["send_count"]), ("新同事", "agent", 2))
        self.assertIsNotNone(n["last_sent_at"])


class Search(unittest.TestCase):
    B = book({"name": "张三", "emails": ["zhangsan@qq.com"]}, {"name": "李四", "email": "l4@163.com", "nick": "老李"},
             {"name": "单田芳", "email": "dtf@x.cn"}, {"name": "John Smith", "email": "john@x.com"},
             {"name": "曾小明", "email": "xm@a.com"}, {"name": "王芳", "email": "wf@b.cn"}, {"name": "刘能", "email": "ln@c.cn"},
             {"name": "Anna Lee", "email": "anna@x.com", "phone": "+1 425 555 0102"})

    def top(self, q):
        hits = C.find(self.B, q, 3)
        return hits[0][1]["name"] if hits else None

    def test_homophones_polyphones_initials_fuzzy(self):
        for q, want in (("张珊", "张三"), ("zhangsan", "张三"), ("zhang san", "张三"), ("Zhang San", "张三"), ("李思", "李四"),
                        ("老李", "李四"), ("shantianfang", "单田芳"), ("dantianfang", "单田芳"), ("dtf", "单田芳"),
                        ("cengxiaoming", "曾小明"), ("zengxiaoming", "曾小明"), ("小明", "曾小明"), ("zxm", "曾小明"),
                        ("wanghuang", "王芳"), ("liuleng", "刘能"), ("niuneng", "刘能"), ("王", "王芳")):
            self.assertEqual(self.top(q), want, q)

    def test_latin_email_phone_and_nothing(self):
        for q, want in (("Jon", "John Smith"), ("Smyth", "John Smith"), ("john@x.com", "John Smith"), ("zhangsan@", "张三"),
                        ("5550102", "Anna Lee"), ("anna", "Anna Lee")):
            self.assertEqual(self.top(q), want, q)
        self.assertIsNone(self.top("欧阳锋"))
        self.assertIsNone(self.top("xyzzy"))

    def test_sorts(self):
        b = json.loads(json.dumps(self.B))
        for c in b["contacts"]:
            c["send_count"], c["last_sent_at"] = 0, None
        C.get(b, C.find(b, "王芳")[0][1]["id"]).update(send_count=9, last_sent_at=100)
        C.get(b, C.find(b, "刘能")[0][1]["id"]).update(send_count=2, last_sent_at=200)
        self.assertEqual([c["name"] for c in C.ordered(b, "recent")[:2]], ["刘能", "王芳"])
        self.assertEqual([c["name"] for c in C.ordered(b, "frequent")[:2]], ["王芳", "刘能"])
        alpha = [c["name"] for c in C.ordered(b, "name")]
        self.assertEqual(alpha, ["Anna Lee", "John Smith", "李四", "刘能", "单田芳", "王芳", "曾小明", "张三"])
        self.assertEqual(C.letter(C.find(b, "曾小明")[0][1]), "Z", "曾 sorts as the surname zeng")


# ------------------------------------------------------------------ serve side
class _Svc(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.smtp = FakeSMTP(AGENT, CODE)
        self.addCleanup(self.smtp.close)
        self.tmp = tempfile.TemporaryDirectory(prefix="p184-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.st = State(root / "state"); self.st.init()
        self.st.sign_key = lambda d: b"k" * 32 if d == "dev1" else None
        self.work = root / "work"; self.work.mkdir()
        (self.work / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 2000)
        (self.work / "people.vcf").write_text("BEGIN:VCARD\nVERSION:3.0\nFN:A\nEMAIL:a@x.cn\nEND:VCARD\n")
        self.media = media_mod.Media(self.st, str(self.work))
        items, _ = self.media.scan("周报在这里：[报告](report.pdf)，通讯录 [vcf](people.vcf)", 7)
        self.mids = {i["name"]: i["mid"] for i in items}
        self.pages = {7: {"id": 7, "end": "done", "reply": {"text": "## 周报\n本周完成三件事。见 report.pdf"}, "media": items}}
        self.sent, self.bcast = [], []

        async def send_app(s, obj):
            self.sent.append(obj)
            return True

        async def send_ready(fn):
            self.bcast.append(fn(None))
        self.ask = AsyncMock(return_value={"behavior": "allow"})
        self.host = SimpleNamespace(st=self.st, lang="zh", agent=SimpleNamespace(kind="claude",native_source={"kind":"terminal","role":"owner"}), agent_cfg={"dir": str(self.work)},
                                    media=self.media, hist=SimpleNamespace(get=lambda i: self.pages.get(i)), stopped=lambda: False,
                                    send_app=send_app, _send_ready=send_ready, ask=self.ask)
        self.svc = ContactService(SimpleNamespace(host=self.host, st=self.st))
        self.s = SimpleNamespace(device="dev1")

    def mailbox(self, owner=True):
        cfg = M.blank_config()
        cfg.update(address=AGENT, username=AGENT, allow=[{"addr": OWNER, "role": "owner"}] if owner else [],
                   imap={"host": "127.0.0.1", "port": 1, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        M.save_config(self.st, M.validate(cfg))
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)

    async def phone(self, obj, dev="dev1"):
        self.sent.clear()
        await self.svc.on_phone(SimpleNamespace(device=dev), {"r": "r" * 16, **obj})
        return self.sent[-1] if self.sent else None


class Phone(_Svc):
    async def test_list_says_whether_mail_can_go_and_who_i_am(self):
        res = await self.phone({"t": "ct_list"})
        self.assertEqual((res["t"], res["ok"], res["me"], res["mail"]), ("ct_res", True, [], {"ready": False, "why": "mail_not_configured"}))
        self.mailbox()
        res = await self.phone({"t": "ct_list", "sort": "name"})
        self.assertEqual((res["me"], res["mail"]), ([OWNER], {"ready": True}))
        self.assertIsNone(await self.phone({"t": "ct_list"}, dev="stranger"), "an unapproved device gets nothing")

    async def test_add_from_dialog_then_search(self):
        res = await self.phone({"t": "ct_save", "contact": {"name": "张总", "nicks": ["张总"], "emails": ["Zhang@HongYuan.cn"]}})
        self.assertEqual((res["ok"], res["result"], res["contact"]["source"]), (True, "added", "phone"))
        res = await self.phone({"t": "ct_save", "contact": {"name": "x", "emails": ["not-an-address"]}})
        self.assertEqual((res["ok"], res["why"]), (False, "no_contact"))
        res = await self.phone({"t": "ct_list", "q": "zhangzong"})
        self.assertEqual(res["contacts"][0]["emails"][0]["v"], "zhang@hongyuan.cn")

    async def test_forward_reply_with_files_and_one_file(self):
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7})
        self.assertEqual((res["t"], res["ok"], res["why"]), ("mail_fwd_res", False, "mail_not_configured"))
        self.mailbox()
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7})
        self.assertEqual((res["ok"], res["sent"], res["files"]), (True, 1, 2))
        frm, to, raw = self.smtp.sent[-1]
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        self.assertEqual((frm, to), (AGENT, [OWNER]))
        self.assertEqual(msg["X-AgentJ"], "forward", "a copy that lands in the Agent's own inbox is never a task")
        self.assertIn("周报", str(msg["Subject"]))
        self.assertIn("本周完成三件事", msg.get_body(("plain",)).get_content())
        self.assertEqual(sorted(p.get_filename() for p in msg.iter_attachments()), ["people.vcf", "report.pdf"])
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": "wang@x.cn", "name": "王总"}, {"addr": "li@y.cn"}],
                                "mid": self.mids["report.pdf"]})
        self.assertEqual((res["ok"], res["sent"], res["files"]), (True, 2, 1))
        frm, to, raw = self.smtp.sent[-1]
        self.assertEqual(to, ["wang@x.cn", "li@y.cn"], "exactly the addresses picked in the dialog")
        b = C.read(self.st)
        self.assertEqual((C.by_email(b, "wang@x.cn")["name"], C.by_email(b, "wang@x.cn")["source"]), ("王总", "phone"))
        self.assertEqual(C.by_email(b, OWNER)["send_count"], 1)
        log = (pathlib.Path(self.st.root) / "host.log").read_text() if (pathlib.Path(self.st.root) / "host.log").exists() else ""
        self.assertNotIn("wang@x.cn", log, "logs carry counts, never an address")

    async def test_forward_refusals(self):
        self.mailbox()
        for obj, why in (({"to": [{"addr": "bad"}], "turn": 7}, "address"), ({"to": [], "turn": 7}, "to"),
                         ({"to": [{"addr": OWNER}]}, "what"), ({"to": [{"addr": OWNER}], "turn": 99}, "not_found"),
                         ({"to": [{"addr": OWNER}], "mid": "A" * 22}, "not_found")):
            res = await self.phone({"t": "mail_fwd", **obj})
            self.assertEqual((res["ok"], res["why"]), (False, why), obj)
        (self.work / "report.pdf").write_bytes(b"%PDF-1.4\nchanged")
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "mid": self.mids["report.pdf"]})
        self.assertEqual(res["why"], "not_found", "a file changed since the page is not sent")
        self.svc.rate["dev1"].extend([__import__("time").monotonic()] * 30)
        res = await self.phone({"t": "mail_fwd", "to": [{"addr": OWNER}], "turn": 7})
        self.assertEqual(res["why"], "rate_limited")
        self.assertEqual(len(self.smtp.sent), 0)


class Card(_Svc):
    async def card(self, req):
        task = asyncio.ensure_future(self.svc.card(req))
        for _ in range(50):
            await asyncio.sleep(0)
            if self.bcast:
                break
        return task, self.bcast[-1]

    async def test_new_card_validation_then_save(self):
        task, m = await self.card({"op": "card", "contact": {"name": "张三", "emails": ["zs@qq.com"]}, "why": "主人说记下张三"})
        self.assertEqual((m["t"], m["mode"], m["contact"]["name"], m["why"]), ("ct_card", "new", "张三", "主人说记下张三"))
        await self.svc.on_ready(self.s)
        self.assertEqual(self.sent[-1]["id"], m["id"], "re-sent to a phone that comes back")
        self.sent.clear()
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": True, "contact": {"name": "张三"}})
        self.assertEqual(self.sent[-1], {"t": "ct_card_err", "id": m["id"], "why": "no_contact"}, "≥ one e-mail or phone")
        self.assertFalse(task.done())
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": "wrong", "ok": True, "contact": {}})
        self.assertEqual(self.sent[-1]["why"], "gone")
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": True,
                                         "contact": {"name": "张三", "nicks": ["张总"], "emails": [{"v": "zs@qq.com"}], "phones": [{"v": "13800138000"}]}})
        res = await task
        self.assertEqual((res["ok"], res["result"], res["contact"]["source"]), (True, "saved", "owner"))
        self.assertEqual(self.bcast[-1], {"t": "ct_card_done", "id": m["id"], "result": "saved"})
        self.assertEqual(C.by_email(C.read(self.st), "zs@qq.com")["phones"][0]["v"], "13800138000")

    async def test_update_card_and_cancel(self):
        c, _ = C.add(b := C.read(self.st), {"name": "李四", "email": "l4@163.com"})
        C.write(self.st, b)
        task, m = await self.card({"op": "card", "id": c["id"], "contact": {"phones": ["13900001111"]}})
        self.assertEqual((m["mode"], m["base"]["name"], m["contact"]["phones"][0]["v"]), ("update", "李四", "13900001111"))
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": True,
                                         "contact": {"name": "李四", "emails": [{"v": "l4@163.com"}], "phones": [{"v": "13900001111"}]}})
        res = await task
        self.assertEqual(res["result"], "saved")
        self.assertEqual(len(C.read(self.st)["contacts"]), 1)
        task, m = await self.card({"op": "card", "contact": {"name": "王五", "emails": ["w5@x.cn"]}})
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": False})
        self.assertEqual((await task)["result"], "cancelled")
        self.assertIsNone(C.by_email(C.read(self.st), "w5@x.cn"), "nothing saved without the owner's tap")
        res = await self.svc.card({"op": "card", "id": "c000000000000", "contact": {}})
        self.assertEqual(res["result"], "not_found")


class AgentMail(_Svc):
    async def test_unknown_name_is_refused_known_name_goes_after_approval(self):
        self.mailbox()
        res = await self.svc.agent_send({"to": ["张三"], "subject": "周报", "body": "见附件"})
        self.assertEqual((res["ok"], res["why"], res["who"]), (False, "not_found", ["张三"]))
        self.ask.assert_not_called()
        await self.svc.agent({"op": "add", "contact": {"name": "张三", "emails": ["zs@qq.com"]}})
        res = await self.svc.agent_send({"to": ["张珊", "new@x.cn"], "subject": "周报", "body": "见附件",
                                         "files": [str(self.work / "report.pdf")]})
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["sent"], res["files"], res["new_contacts"]), (["zs@qq.com", "new@x.cn"], ["report.pdf"], ["new@x.cn"]))
        tool, inp = self.ask.call_args.args[:2]
        self.assertEqual(tool, "agentj mail send")
        self.assertIn("张三 <zs@qq.com>", inp["summary"])
        self.assertIn("report.pdf", inp["summary"])
        self.assertFalse(self.ask.call_args.kwargs["batch"])
        frm, to, raw = self.smtp.sent[-1]
        self.assertEqual(to, ["zs@qq.com", "new@x.cn"])
        self.assertEqual(email.message_from_bytes(raw, policy=email.policy.default)["X-AgentJ"], "agent")
        b = C.read(self.st)
        self.assertEqual((C.by_email(b, "new@x.cn")["source"], C.by_email(b, "zs@qq.com")["send_count"]), ("agent", 1))

    async def test_denied_and_files_outside_the_folder(self):
        self.mailbox()
        self.ask.return_value = {"behavior": "deny"}
        res = await self.svc.agent_send({"to": ["zs@qq.com"], "subject": "x", "body": "y"})   # P296: not the owner → a card
        self.assertEqual(res["why"], "denied")
        self.assertEqual(self.smtp.sent, [])
        self.ask.return_value = {"behavior": "allow"}
        secret = pathlib.Path(self.st.root) / "host.key"
        for p in (str(secret), "/etc/passwd", str(self.work / ".env")):
            (self.work / ".env").write_text("K=v")
            res = await self.svc.agent_send({"to": [OWNER], "subject": "x", "body": "y", "files": [p]})
            self.assertEqual((res["ok"], res["why"]), (False, "file"), p)
        self.assertEqual(self.smtp.sent, [])

    async def test_agent_ops_and_no_mailbox(self):
        res = await self.svc.agent_send({"to": [OWNER], "subject": "x"})
        self.assertEqual(res["why"], "mail_not_configured")
        res = await self.svc.agent({"op": "import", "data": __import__("base64").b64encode(APPLE_VCF.encode()).decode(), "name": "a.vcf"})
        self.assertEqual(res["added"], 2)
        res = await self.svc.agent({"op": "find", "q": "laozhang"})
        self.assertEqual(res["contacts"][0]["name"], "张三")
        self.assertEqual((await self.svc.agent({"op": "nope"}))["why"], "shape")


class Cli(_St):
    def run_cli(self, *argv):
        from agentj import contacts
        p = argparse.ArgumentParser()
        contacts.add_parser(p.add_subparsers(dest="cmd"))
        a = p.parse_args(["contacts", *argv])
        os.environ["AGENTJ_STATE_DIR"] = str(self.st.root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = contacts.cmd(a)
        return code, buf.getvalue()

    def test_import_find_export_without_serve(self):
        old = os.environ.get("AGENTJ_STATE_DIR")
        self.addCleanup(lambda: os.environ.__setitem__("AGENTJ_STATE_DIR", old) if old else os.environ.pop("AGENTJ_STATE_DIR", None))
        f = pathlib.Path(self.tmp.name) / "in.vcf"
        f.write_text(APPLE_VCF)
        code, out = self.run_cli("import", str(f), "--json")
        self.assertEqual((code, json.loads(out)["added"]), (0, 2))
        code, out = self.run_cli("find", "张珊")
        self.assertEqual(code, 0)
        self.assertIn("张三", out)
        code, out = self.run_cli("find", "欧阳锋")
        self.assertIn("没找到叫「欧阳锋」的人", out)
        dest = pathlib.Path(self.tmp.name) / "out.csv"
        code, out = self.run_cli("export", str(dest))
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertIn("zs1980@qq.com", dest.read_text(encoding="utf-8"))
        code, out = self.run_cli("edit", "张三", "--phone", "010-12345678", "--json")
        self.assertEqual(json.loads(out)["contact"]["phones"][-1]["v"], "010-12345678")
        code, out = self.run_cli("list", "--sort", "name", "--json")
        self.assertEqual([c["name"] for c in json.loads(out)["contacts"]], ["John Smith", "张三"])


class Item6(unittest.TestCase):
    def test_file_cards_and_inbox_types(self):
        for ext, mime in ((".vcf", "text/vcard"), (".ics", "text/calendar"), (".eml", "message/rfc822"), (".mbox", "application/mbox"),
                          (".doc", "application/msword"), (".xls", "application/vnd.ms-excel"), (".7z", "application/x-7z-compressed")):
            self.assertEqual(media_mod.KINDS[ext][1], mime)
        self.assertIn("text/vcard", media_mod.TEXTY, "contacts go through the secret scan")
        self.assertNotIn("application/msword", media_mod.TEXTY)
        self.assertTrue(media_mod._sniff("application/msword", ".doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8))
        self.assertFalse(media_mod._sniff("application/msword", ".doc", b"PK\x03\x04"))
        self.assertTrue(media_mod._sniff("text/vcard", ".vcf", b"BEGIN:VCARD"))
        for mime, ext in (("text/vcard", ".vcf"), ("text/calendar", ".ics"), ("message/rfc822", ".eml"), ("application/mbox", ".mbox")):
            self.assertEqual(inbox.ALLOWED[mime], ext)


class RealHost(unittest.IsolatedAsyncioTestCase):
    async def test_agent_socket_phone_routing_and_cap(self):
        from test_l1 import _owner_host as _host, _state
        from agentj import elevate
        with tempfile.TemporaryDirectory(prefix="p184r-", dir=TEST_TMP) as d:
            st = _state(d); host = _host(st, [])
            self.assertIn("contacts", host._caps()["caps"])
            await host.elevate.start()
            self.addAsyncCleanup(host.elevate.stop)
            req = lambda r, timeout=10: asyncio.to_thread(elevate.client_request, st, {"t": "contacts", **r}, timeout)  # noqa: E731
            self.assertEqual(await req({"op": "ping"}), {"ok": True})
            res = await req({"op": "add", "contact": {"name": "张三", "emails": ["zs@qq.com"]}})
            self.assertEqual((res["result"], res["contact"]["source"]), ("added", "agent"))
            self.assertEqual((await req({"op": "find", "q": "张珊"}))["contacts"][0]["name"], "张三")
            got = []
            cards = []

            async def send_ready(fn):
                cards.append(fn(None))
            host._send_ready = send_ready
            early = []
            fut = asyncio.ensure_future(asyncio.to_thread(elevate.client_request, st, {"t": "contacts", "op": "card",
                                        "contact": {"name": "李四", "phones": ["13900001111"]}}, 20, early.append))
            for _ in range(200):
                if cards:
                    break
                await asyncio.sleep(0.02)
            m = cards[-1]
            self.assertEqual((m["t"], early[0]["id"]), ("ct_card", m["id"]), "the CLI learns the card id first")
            device = "dev-p184"
            st.sign_key = lambda dv: b"k" * 32 if dv == device else None

            async def send_app(s, obj):
                got.append(obj)
                return True
            host.send_app = send_app
            sess = SimpleNamespace(device=device)
            await host.elevate.on_phone(sess, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": True,
                                               "contact": {"name": "李四", "phones": [{"v": "13900001111"}]}})
            res = await asyncio.wait_for(fut, 10)
            self.assertEqual(res["result"], "saved")
            await host.elevate.on_phone(sess, {"t": "ct_list", "r": "q" * 16, "sort": "name"})
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual([c["name"] for c in got[-1]["contacts"]], ["李四", "张三"])
            self.assertEqual(got[-1]["mail"]["why"], "mail_not_configured")


if __name__ == "__main__":
    unittest.main()
