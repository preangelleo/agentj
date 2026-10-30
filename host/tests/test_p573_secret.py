"""P573 (#386): a report that TALKS about keys is not a key; a real key toward the owner is masked, not the whole file.

One verdict for the three owner paths — file (media.check / taskspec.secret_like(owner=True)), mail (privacy.credentials →
owner_route) and Telegram / inbound owner text (owner_route.plan, privacy.owner_text).
Synthetic values only: every credential-shaped value is built at run time.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import email
import email.policy
import os
import pathlib
import secrets
import shutil
import string
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import media, owner_route, privacy, taskspec, tg_media  # noqa: E402

from test_p57_media import _Base  # noqa: E402
from test_p211_owner_files import Box  # noqa: E402
import test_p194_owner_direct as P194  # noqa: E402
import test_p182_meeting as P182  # noqa: E402
from agentj import mail as M  # noqa: E402


def rand(n, alphabet=string.ascii_letters + string.digits):
    return "".join(secrets.choice(alphabet) for _ in range(n))


def real_key():
    """A provider-shaped key, built at run time (never a literal key shape in the source)."""
    return "sk-" + "or-" + "v1-" + rand(48, "0123456789abcdef")


def labelled_key():
    return "API" + "_KEY=" + rand(6) + "7" + rand(30)   # a digit for sure


REPORT = "## 工具\n- 查密钥：CredSweeper（MIT，ONNX 模型）\n- 结论：0 命中\n"
TALK = [
    ("① verbatim", "查密钥：CredSweeper（MIT，ONNX 模型"),
    ("② half-width", "查密钥: CredSweeper (MIT, ONNX 模型"),
    ("③ names", "OPENROUTER_API_KEY=OPENROUTER_API_KEY\nexport ANTHROPIC_API_KEY\napi_key: OPENAI_API_KEY\n"),
    ("④ prefixes", "前缀 sk-or-、sk-ant- 开头\nAPI key: sk-or-v1-\ntoken: sk-ant-\n"),
    ("⑤ placeholders", "token: <redacted>\nTOKEN=${TOKEN}\napi_key = \"<redacted>\"\n"),
    ("⑥ examples", "api_key=your_api_key_here\napi_key: <your-key>\nsecret = 'your-secret-here'\n"),
]


class Verdict(unittest.TestCase):
    def test_bearer_header_value_stays_masked(self):
        # integration regression: a 20+ character value after Bearer / Authorization is a token even when it reads like a slug
        t = "Authorization: Bearer " + "-".join(["fixture", "value"] * 4)
        self.assertTrue(privacy.credentials(t))
        self.assertNotIn("fixture-value", privacy.redact(t, owner=True))
        self.assertTrue(taskspec.secret_like(t, owner=True))
        for ref in ('curl -H "Authorization: Bearer $TOKEN"', "Authorization: Bearer <redacted>"):
            self.assertFalse(privacy.credentials(ref), ref)

    """①–⑥, ⑧: the same answer on every owner path; strict outbound gates unchanged."""

    def test_talking_about_keys_is_no_key_on_any_owner_path(self):
        for name, text in TALK:
            with self.subTest(name):
                self.assertEqual(privacy.credentials(text), [], name)                       # mail
                self.assertFalse(taskspec.secret_like(text, owner=True), name)              # file
                self.assertIsNone(taskspec.secret_where(text, owner=True), name)
                self.assertEqual(privacy.owner_text(text), text, name)                      # inbound owner text
                out, values = owner_route.plan(text, "telegram", "zh", True)                # Telegram
                self.assertEqual((out, values), (text, []), name)

    def test_placeholders_are_never_replaced_twice(self):
        text = "token: <redacted>\nTOKEN=${TOKEN}\n"
        self.assertEqual(privacy.owner_text(privacy.owner_text(text)), text)
        self.assertEqual(privacy.redact(text, paths=True), privacy.redact(privacy.redact(text, paths=True), paths=True))

    def test_real_key_masks_only_its_span(self):
        key = real_key()
        text = f"{REPORT}新的 key：{key}\n其余照常。"
        found = privacy.credentials(text)
        self.assertEqual([v for _k, v, _s, _e in found], [key])
        shown = privacy.owner_text(text)
        self.assertNotIn(key, shown)
        self.assertEqual(shown, text.replace(key, privacy.R))
        out, values = owner_route.plan(text, "telegram", "zh", True)
        self.assertEqual([v for _k, v in values], [key])
        self.assertNotIn(key, out)
        self.assertIn("CredSweeper（MIT，ONNX 模型）", out)
        lab = labelled_key()
        self.assertTrue(taskspec.secret_like(lab, owner=True))
        self.assertEqual(len(privacy.credentials(lab)), 1)

    def test_placeholder_words_inside_real_values_stay_secret(self):
        for word in ("sample", "fake", "example", "dummy", "placeholder", "redacted", "xxxx"):
            value = rand(8) + word + "!7" + rand(8)
            for label in ("password", "API_KEY", "Authorization: Bearer"):
                text = f"{label}: {value}" if label != "Authorization: Bearer" else f"{label} {value}"
                with self.subTest(word=word, label=label):
                    self.assertEqual([v for _, v, _, _ in privacy.credentials(text)], [value])
                    self.assertNotIn(value, privacy.owner_text(text))
                    self.assertTrue(taskspec.secret_like(text, owner=True))
                    self.assertNotIn(value, owner_route.plan(text, "mail", "en", True)[0])

    def test_real_password_is_still_masked(self):
        pw = rand(5, string.ascii_lowercase) + "2" + rand(5, string.ascii_lowercase)
        for text in (f"password: {pw}", f"密码：{pw}", f"DB_PASS='{pw}'", f"口令={pw}(x)"):
            with self.subTest(text=text[:6]):
                self.assertTrue(privacy.credentials(text))
                self.assertNotIn(pw, privacy.owner_text(text))
                self.assertTrue(taskspec.secret_like(text, owner=True))
        letters = rand(12, string.ascii_lowercase)        # a password label keeps its strength: letters only still count
        self.assertNotIn(letters, privacy.owner_text("password: " + letters))

    def test_strict_outbound_gates_unchanged(self):
        for _name, text in TALK[:2]:
            self.assertTrue(taskspec.secret_like(text), "feedback / plaza / friends keep their rules")
            self.assertIn(privacy.R, privacy.redact(text))


class Files(_Base):
    """⑦ ⑨ ⑩ ⑬ on the owner's page (media.scan_keys → owner_route.offer_files), plus Telegram's collect."""

    def host(self, box):
        return SimpleNamespace(st=self.st, elevate=SimpleNamespace(out=box), stopped=lambda: False, _approvers=lambda: ["dev1"])

    def body(self, mid):
        return b"".join(d for _, d, _ in self.m.window(mid, 0))

    def test_report_talking_about_keys_is_an_ordinary_card(self):
        self.put("out/REPORT.md", REPORT.encode())
        items, skips, keys = self.m.scan_keys("[r](out/REPORT.md)", 1)
        self.assertEqual(([i["name"] for i in items], skips, keys), (["REPORT.md"], [], []))
        self.assertEqual(self.body(items[0]["mid"]), REPORT.encode(), "no mask, the report as written")

    def test_password_with_placeholder_word_masks_only_value(self):
        for word in ("sample", "fake"):
            value = rand(8) + word + "!7" + rand(8)
            text = REPORT + f"password: {value}\n"
            self.put(f"out/{word}.md", text.encode())
            items, skips, keys = self.m.scan_keys(f"[r](out/{word}.md)", 1)
            self.assertEqual(skips, [])
            self.assertEqual(self.body(items[0]["mid"]).decode(), text.replace(value, privacy.R))
            self.assertEqual(keys[0][1]["values"], [("credential_assignment", value)])

    def test_real_key_file_gets_masked_copy_and_one_card(self):
        key = real_key()
        text = REPORT + f"key：{key}\n"
        self.put("out/REPORT.md", text.encode())
        box = Box()
        items, skips, keys = self.m.scan_keys("[r](out/REPORT.md)", 1)
        self.assertEqual(([i["name"] for i in items], skips), (["REPORT.md"], []))
        got = self.body(items[0]["mid"]).decode()
        self.assertEqual(got, text.replace(key, privacy.R))
        self.assertEqual(asyncio.run(owner_route.offer_files(self.host(box), keys, "zh")), 1)
        self.assertEqual([(r["kind"], r["value"]) for r in box.reqs], [("text", key)])
        self.assertNotIn("values", keys[0][1], "the value leaves the list once the card is offered")
        self.assertNotIn(key, (self.st.root / "media.json").read_text())
        store = self.st.root / media.STORE_DIR
        self.assertEqual(store.stat().st_mode & 0o777, 0o700)
        for f in store.iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(key.encode(), f.read_bytes())

    def test_more_than_three_values_open_three_cards(self):
        ks = [real_key() for _ in range(5)]
        self.put("out/many.md", "\n".join(f"key {i}: {k}" for i, k in enumerate(ks)).encode())
        box = Box()
        items, _skips, keys = self.m.scan_keys("[m](out/many.md)", 1)
        self.assertEqual(asyncio.run(owner_route.offer_files(self.host(box), keys, "zh")), owner_route.MAX_CARDS)
        self.assertEqual([r["value"] for r in box.reqs], ks[:3])
        got = self.body(items[0]["mid"]).decode()
        self.assertEqual(got.count(privacy.R), 5, "the rest stay behind the placeholder")
        for k in ks:
            self.assertNotIn(k, got)

    def test_env_and_pem_still_go_whole_as_a_card(self):
        self.put(".env", ("A=" + real_key() + "\n").encode())
        label = "PRIVATE" + " KEY"
        pem = f"-----BEGIN {label}-----\nabc\n-----END {label}-----\n"
        self.put("out/server.pem", pem.encode())
        box = Box()
        items, skips, keys = self.m.scan_keys("[e](.env) [p](out/server.pem)", 1)
        self.assertEqual(items, [])
        self.assertEqual(asyncio.run(owner_route.offer_files(self.host(box), keys, "zh")), 2)
        self.assertEqual([s["why"] for s in skips], ["key_card", "key_card"])
        self.assertEqual([r["kind"] for r in box.reqs], ["file", "file"])

    def test_card_still_opens_after_24h_and_after_the_source_changed(self):
        now = [1_000_000.0]
        m = media.Media(self.st, str(self.work), clock=lambda: now[0])
        p = self.put("out/REPORT.md", REPORT.encode())
        [item], _ = m.scan("[r](out/REPORT.md)", 1)
        p.write_text("改过了\n")
        now[0] += media.KEEP_SECS + 60
        again = media.Media(self.st, str(self.work), clock=lambda: now[0])          # a restart too
        self.assertEqual(b"".join(d for _, d, _ in again.window(item["mid"], 0)), REPORT.encode())
        now[0] += media.STORE_SECS
        with self.assertRaises(media.Gone):
            again.window(item["mid"], 0)

    def test_store_is_bounded(self):
        self.put("out/a.md", b"a" * 4000)
        self.put("out/b.md", b"b" * 4000)
        old = media.STORE_BYTES
        media.STORE_BYTES = 5000
        try:
            [a], _ = self.m.scan("[a](out/a.md)", 1)
            [b], _ = self.m.scan("[b](out/b.md)", 2)
        finally:
            media.STORE_BYTES = old
        names = {f.name for f in (self.st.root / media.STORE_DIR).iterdir()}
        self.assertEqual(names, {b["sha256"]}, "the oldest copy goes first")
        self.assertNotIn("blob", self.m.table[a["mid"]])
        self.assertEqual(self.body(a["mid"]), b"a" * 4000, "without its copy it falls back to the unchanged source")

    def test_masked_copy_never_falls_back_to_the_source(self):
        key = real_key()
        self.put("out/k.md", f"key: {key}\n".encode())
        [item], _ = self.m.scan("[k](out/k.md)", 1)
        shutil.rmtree(self.st.root / media.STORE_DIR)
        with self.assertRaises(media.Gone):
            self.m.window(item["mid"], 0)
        rec = self.m.get(item["mid"])
        with self.assertRaises(media.Gone):
            tg_media.read_checked(rec)

    def test_telegram_collect_sends_the_masked_copy(self):
        key = real_key()
        self.put("out/k.md", f"# 报告\nkey: {key}\n".encode())
        keys = []
        items, skips = tg_media.collect(self.m, "[k](out/k.md)", keys, mask=True)
        self.assertEqual(([i["name"] for i in items], skips), (["k.md"], []))
        data = tg_media.read_checked(items[0])
        self.assertNotIn(key.encode(), data)
        self.assertEqual([v for _k, v in keys[0][1]["values"]], [key])
        self.put("out/r.md", REPORT.encode())
        keys = []
        items, skips = tg_media.collect(self.m, "[r](out/r.md)", keys, mask=True)
        self.assertEqual((len(items), skips, keys), (1, [], []))


class AgentMail(P194._Svc):
    """Mail to the owner (`agentj mail send --to-owner`): no card; masked attachments; cards only after the send."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.set_me()

    async def test_report_mail_is_not_a_key_and_needs_no_card(self):
        res = await self.svc.agent_send({"to_owner": True, "subject": "报告", "body": REPORT})
        self.assertTrue(res["ok"] and res["owner_direct"] and res["no_card"], res)
        self.ask.assert_not_awaited()
        self.out.send.assert_not_called()
        self.assertIn("CredSweeper（MIT，ONNX 模型）", P194.body_of(self.smtp.sent[-1][2]))

    async def test_key_in_body_and_file_masked_cards_after_send(self):
        key, fkey = real_key(), real_key()
        (self.work / "REPORT.md").write_text(REPORT + f"key: {fkey}\n")
        order = []
        sent0 = self.smtp.sent.copy()

        async def card(req):
            order.append(("card", len(self.smtp.sent) - len(sent0)))
            return {"result": "sent"}
        self.out.send = AsyncMock(side_effect=card)
        res = await self.svc.agent_send({"to_owner": True, "subject": "报告", "body": f"新 key：{key}", "files": ["REPORT.md"]})
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["secret_cards"], res["withheld"]), (2, 0))
        self.assertEqual(order, [("card", 1), ("card", 1)], "cards only after the mail went out")
        raw = self.smtp.sent[-1][2]
        self.assertNotIn(key.encode(), raw)
        self.assertNotIn(fkey.encode(), raw)
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        [att] = list(msg.iter_attachments())
        content = att.get_payload(decode=True).decode("utf-8")
        self.assertIn("CredSweeper（MIT，ONNX 模型）", content)
        self.assertIn(privacy.R, content)
        self.assertEqual(sorted(c.args[0]["value"] for c in self.out.send.call_args_list), sorted([key, fkey]))

    async def test_placeholder_word_password_is_masked_in_mail_and_attachment(self):
        for word in ("sample", "fake"):
            value = rand(8) + word + "!7" + rand(8)
            text = REPORT + f"password: {value}\n"
            (self.work / "REPORT.md").write_text(text)
            res = await self.svc.agent_send({"to_owner": True, "subject": "Report", "body": text, "files": ["REPORT.md"]})
            self.assertTrue(res["ok"], res)
            msg = email.message_from_bytes(self.smtp.sent[-1][2], policy=email.policy.default)
            self.assertNotIn(value, P194.body_of(self.smtp.sent[-1][2]))
            [att] = list(msg.iter_attachments())
            self.assertEqual(att.get_payload(decode=True).decode(), text.replace(value, privacy.R))
            self.assertEqual(res["secret_cards"], 2)

    async def test_a_card_that_fails_after_the_send_is_reported(self):
        self.out.send = AsyncMock(return_value={"result": "busy"})
        res = await self.svc.agent_send({"to_owner": True, "subject": "k", "body": f"key: {real_key()}"})
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["card_failed"], res["withheld"]), (1, 1))
        self.assertIn("没能弹出", res["detail"])

    async def test_mail_to_someone_else_keeps_its_card(self):
        res = await self.svc.agent_send({"to": ["someone@else.example"], "subject": "x", "body": "hi"})
        self.assertTrue(res["ok"] and not res["no_card"], res)
        self.ask.assert_awaited_once()


class NotesRefused(unittest.IsolatedAsyncioTestCase):
    """⑪ the owner refuses the mail's confirmation → not one pickup card opens."""
    configure = P182.Notes.configure

    async def asyncSetUp(self):
        await P182.Notes.asyncSetUp(self)
        self.out = SimpleNamespace(send=AsyncMock(return_value={"result": "sent"}))
        self.host.elevate = SimpleNamespace(out=self.out)
        self.host._approvers = lambda: ["dev1"]
        self.configure()

    async def test_refused_opens_no_card_allowed_opens_it(self):
        key = real_key()
        self.host.ask = AsyncMock(return_value={"behavior": "deny"})
        r = await self.m.send_notes([P182.BOSS], "纪要", f"# 纪要\nkey: {key}", to_owner=True)
        self.assertEqual(r, {"ok": False, "why": "denied"})
        self.out.send.assert_not_called()
        self.assertEqual(self.smtp.sent, [])
        self.host.ask = AsyncMock(return_value={"behavior": "allow"})
        r = await self.m.send_notes([P182.BOSS], "纪要", f"# 纪要\nkey: {key}", to_owner=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual([c.args[0]["value"] for c in self.out.send.call_args_list], [key])
        self.assertNotIn(key.encode(), self.smtp.sent[-1][2])


class CredSweeperCrossCheck(unittest.TestCase):
    """Development-time second opinion only (never on customer hosts): skipped when credsweeper is not installed."""

    def test_report_is_clean_for_both(self):
        try:
            import credsweeper  # noqa: F401
        except ImportError:
            self.skipTest("credsweeper not installed")
        self.assertFalse(taskspec.secret_like(REPORT, owner=True))


if __name__ == "__main__":
    unittest.main()
