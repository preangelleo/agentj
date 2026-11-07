"""P212 (0.21, PROTOCOL §29): interactive forms — 快选 / Quick Pick and 深聊卡 / Deep Dive (agentj/forms.py).

Schema: light / deep / auto (≤ 5 questions, one section, no attachments → light; a lone single-choice → tap), every refusal
names the field, no password type, credential-shaped text refused. The shared vectors (protocol/vectors/forms.json) pin
show_if and answer checks to the same results as the phone's forms.js. Templates: all 14 valid in zh and en, fill limited
to the template's own fields. Serve: `agentj ask` puts the form on the running page (or a page of its own); a `say` with
`form` is re-checked against the stored form, becomes the owner's message with the answers (+ answer JSON in the inbox),
first submission wins, a withdrawn answer re-opens the form, expired / cancelled are refused; drafts on this computer.
"""
import asyncio
import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import forms as F, serve, uploads, wire  # noqa: E402
from agentj.state import State  # noqa: E402

VECTORS = json.loads((pathlib.Path(__file__).resolve().parents[2] / "protocol/vectors/forms.json").read_text(encoding="utf-8"))


def q(i, typ="single", **kw):
    base = {"id": f"q{i}", "type": typ, "label": f"Question {i}"}
    if typ in ("single", "buttons", "multi", "dropdown", "rank"):
        base["options"] = ["a", "b", "c"]
    if typ == "slider":
        base.update(min=0, max=10)
    base.update(kw)
    return base


class Schema(unittest.TestCase):
    def test_light_auto_and_tap(self):
        spec, conv = F.normalize({"title": "Pick", "questions": [q(1, "buttons")]})
        self.assertEqual((spec["mode"], spec.get("tap"), spec["n"], conv), ("light", True, 1, []))
        spec, _ = F.normalize({"title": "Pick", "questions": [q(1), q(2, "text"), q(3, "rating")]})
        self.assertEqual(spec["mode"], "light")
        self.assertNotIn("tap", spec, "several questions → fill in, then 提交")
        spec, _ = F.normalize({"title": "Pick", "questions": [q(1, other=True)]})
        self.assertNotIn("tap", spec, "「其他」 needs typing → no tap-to-submit")

    def test_over_the_light_limit_becomes_deep_with_reasons(self):
        spec, conv = F.normalize({"title": "Big", "questions": [q(i, "text") for i in range(7)]}, "zh", "light")
        self.assertEqual(spec["mode"], "deep")
        self.assertEqual(conv, ["7 题"])
        spec, conv = F.normalize({"title": "Files", "questions": [q(1, "file")]}, "en")
        self.assertEqual((spec["mode"], conv), ("deep", ["attachments"]))
        spec, conv = F.normalize({"title": "Rank", "questions": [q(1, "rank")]})
        self.assertEqual(spec["mode"], "deep")
        spec, conv = F.normalize({"title": "Two", "sections": [{"questions": [q(1)]}, {"questions": [q(2)]}]})
        self.assertEqual(spec["mode"], "deep")
        spec, conv = F.normalize({"title": "x", "mode": "deep", "questions": [q(1)]})
        self.assertEqual((spec["mode"], conv), ("deep", []), "asked for deep → deep, no reasons")
        self.assertNotIn("tap", spec)

    def test_light_never_preselects(self):
        spec, _ = F.normalize({"title": "x", "preselect": True, "questions": [q(1, recommend="b")]})
        self.assertNotIn("preselect", spec)
        self.assertEqual(spec["sections"][0]["questions"][0]["rec"], "b")
        spec, _ = F.normalize({"title": "x", "mode": "deep", "preselect": True, "questions": [q(1)], "recommend": {"q1": "c"}})
        self.assertTrue(spec["preselect"])
        self.assertEqual(spec["sections"][0]["questions"][0]["rec"], "c")

    def test_refusals_name_the_field(self):
        bad = [
            ({"questions": [q(1)]}, "missing", "title"),
            ({"title": "x", "questions": [{"id": "Q!", "type": "single", "label": "l", "options": ["a", "b"]}]}, "id", "id"),
            ({"title": "x", "questions": [q(1), q(1)]}, "id", "duplicate"),
            ({"title": "x", "questions": [{"id": "p", "type": "password", "label": "pw"}]}, "type", "password"),
            ({"title": "x", "questions": [q(1, options=["only"])]}, "options", "2–12"),
            ({"title": "x", "questions": [q(1, "buttons", options=list("abcde"))]}, "options", "2–4"),
            ({"title": "x", "questions": [q(1, show_if={"q": "q2", "eq": "a"}), q(2)]}, "show_if", "earlier"),
            ({"title": "x", "questions": [q(1, "slider", min=None, max=None)]}, "range", "min"),
            ({"title": "x", "questions": [q(1, "multi", min=3, max=2)]}, "range", "min"),
            ({"title": "x", "questions": [q(1, "text", pattern="regex")]}, "pattern", "email"),
            ({"title": "x", "questions": [q(1, "file", max_files=11)]}, "range", "max_files"),
            ({"title": "x", "questions": [{"id": "n", "type": "note", "text": "hi"}]}, "empty", "至少"),
            ({"title": "x", "questions": [q(1)], "recommend": {"zz": "a"}}, "recommend", "zz"),
            ({"title": "x", "questions": [q(1, recommend="nope")]}, "answer", "options"),
            ({"title": "x" * 81, "questions": [q(1)]}, "too_long", "80"),
            ({"title": "x", "questions": [q(i, "text") for i in range(61)]}, "too_many", "60"),
            ({"title": "x", "questions": [q(1, label="x" * 70000)]}, "too_big", "KB"),
            ({"title": "x", "schema": "other/v9", "questions": [q(1)]}, "schema", "agentj.form/v1"),
        ]
        for raw, why, word in bad:
            with self.subTest(why=why, word=word):
                with self.assertRaises(F.FormError) as cm:
                    F.normalize(raw)
                self.assertEqual(cm.exception.why, why)
                self.assertIn(word, cm.exception.detail)

    def test_credentials_in_the_form_are_refused(self):
        key = "sk-ant-api03-" + "Abc123XyZ987" * 6
        for raw in ({"title": "x", "intro": "use " + key, "questions": [q(1)]},
                    {"title": "x", "questions": [q(1, options=["a", "OPENAI_API_KEY=sk-" + "Q" * 40])]}):
            with self.assertRaises(F.FormError) as cm:
                F.normalize(raw)
            self.assertEqual(cm.exception.why, "secret")
            self.assertIn("agentj secret", cm.exception.detail)

    def test_bilingual_strings_pick_the_form_language(self):
        raw = {"title": {"zh": "报名", "en": "Sign-up"}, "questions": [q(1, label={"zh": "方式", "en": "How"},
                                                                         options=[{"value": "a", "label": {"zh": "现场", "en": "On site"}}, "b"])]}
        self.assertEqual(F.normalize(raw, "zh")[0]["title"], "报名")
        en = F.normalize(raw, "en")[0]
        self.assertEqual((en["title"], en["sections"][0]["questions"][0]["options"][0]["label"]), ("Sign-up", "On site"))
        self.assertEqual(F.normalize({**raw, "lang": "en"}, "zh")[0]["title"], "Sign-up", "the form's own lang wins")

    def test_text_is_plain(self):
        spec, _ = F.normalize({"title": "x\x1b[31mred", "questions": [q(1, label="<b>hi</b>‮")]})
        self.assertEqual(spec["title"], "x[31mred")
        self.assertEqual(spec["sections"][0]["questions"][0]["label"], "<b>hi</b>", "kept as text (the phone never parses it)")


class Vectors(unittest.TestCase):
    """The same table runs in web/test/p212.test.mjs against forms.js."""

    def test_show_if(self):
        for cond, ans, want in VECTORS["holds"]:
            with self.subTest(cond=cond, ans=ans):
                self.assertIs(F.holds(cond, ans), want)

    def test_answers(self):
        spec, _ = F.normalize(VECTORS["form"])
        for ans, other, want, clean in VECTORS["check"]:
            with self.subTest(ans=ans):
                try:
                    got, _ = F.check(spec, ans, other)
                    why = "ok"
                except F.FormError as e:
                    got, why = None, e.why
                self.assertEqual(why, want)
                if clean is not None:
                    self.assertEqual(got, clean)

    def test_hidden_questions_are_neither_checked_nor_returned(self):
        spec, _ = F.normalize(VECTORS["form"])
        got, skipped = F.check(spec, {"mode": "online", "people": 99})
        self.assertNotIn("people", got)
        self.assertIn("people", skipped)

    def test_draft_needs_nothing(self):
        spec, _ = F.normalize(VECTORS["form"])
        self.assertEqual(F.check(spec, {}, draft=True)[0], {})
        with self.assertRaises(F.FormError):
            F.check(spec, {"mode": "nope"}, draft=True)


class Templates(unittest.TestCase):
    def test_fourteen_templates_valid_in_both_languages(self):
        ts = F.templates()
        self.assertEqual(len(ts), 14)
        names = {t["name"] for t in ts}
        self.assertTrue({"decide-3", "grill-startup", "event-signup", "requirement-clarify", "bug-report"} <= names)
        for t in ts:
            full = F.template(t["name"])
            self.assertTrue(full["title"]["zh"] and full["title"]["en"] and full["about"]["zh"] and full["about"]["en"])
            for lang in ("zh", "en"):
                with self.subTest(name=t["name"], lang=lang):
                    spec, conv = F.normalize(F.apply(t["name"], F.example(t["name"], lang)), lang)
                    self.assertEqual(spec["mode"], t["mode"])
                    self.assertNotIn("{{", json.dumps(spec, ensure_ascii=False), "every placeholder filled")

    def test_grill_startup_shape(self):
        spec, _ = F.normalize(F.apply("grill-startup", F.example("grill-startup")), "zh")
        self.assertEqual([s["id"] for s in spec["sections"]], ["user", "value", "money", "scope", "risk"])
        self.assertTrue(spec["preselect"])
        recs = [x for x in F.questions(spec) if "rec" in x]
        self.assertGreaterEqual(len(recs), 6, "Grill me: questions carry a recommendation")
        self.assertIn("disagree", [x["id"] for x in F.questions(spec)], "the 「哪一条你其实不同意」 guard")

    def test_fill_limited_to_the_templates_fields(self):
        ex = F.example("decide-3")
        with self.assertRaises(F.FormError) as cm:
            F.apply("decide-3", {**ex, "sections": []})
        self.assertEqual(cm.exception.why, "fill_field")
        with self.assertRaises(F.FormError) as cm:
            F.apply("decide-3", {k: v for k, v in ex.items() if k != "a"})
        self.assertEqual(cm.exception.why, "fill")
        raw = F.apply("decide-3", {**ex, "recommend.choice": "b"})
        spec, _ = F.normalize(raw)
        self.assertEqual(spec["sections"][0]["questions"][0]["rec"], "b")
        with self.assertRaises(F.FormError):
            F.apply("event-signup", {**F.example("event-signup"), "drop": ["mode"]})   # kept question
        raw = F.apply("event-signup", {**F.example("event-signup"), "drop": ["diet"]})
        self.assertNotIn("diet", [x["id"] for x in F._questions_of(raw)])
        with self.assertRaises(F.FormError):
            F.template("../etc/passwd")
        with self.assertRaises(F.FormError):
            F.template("nope")

    def test_cli_set_and_validate(self):
        a = SimpleNamespace(template="yes-no", fill=None, set=["question=上线吗？", "context=都测过了"], file=None)
        raw, tpl = F.build(a)
        self.assertEqual((raw["title"], tpl), ("上线吗？", "yes-no"))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"title": "x", "questions": [q(i, "text") for i in range(6)]}, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = F.cmd(SimpleNamespace(action="validate", target=None, file=f.name, template=None, fill=None, set=None,
                                         mode="auto", ttl=None, en=False, json=False))
        self.assertEqual(code, 0)
        self.assertIn("会转为深聊卡：6 题", out.getvalue())
        pathlib.Path(f.name).unlink()

    def test_cli_templates_lists_all(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            F.cmd(SimpleNamespace(action="templates", target=None, en=True, json=True))
        self.assertEqual(len(json.loads(out.getvalue())), 14)

    def test_cli_send_goes_through_elevate_and_says_end_the_turn(self):
        seen = []

        def fake(st, req, timeout=0):
            seen.append(req)
            return {"ok": True, "id": "f_" + "0" * 16, "mode": "light", "n": 1, "ttl": 7 * 86400, "converted": [], "turn": 3}
        out = io.StringIO()
        with mock.patch("agentj.elevate.client_request", fake), contextlib.redirect_stdout(out):
            code = F.cmd(SimpleNamespace(action="send", target=None, file=None, template="yes-no", fill=None,
                                         set=["question=Ship it?"], mode="auto", ttl="2d", en=True, json=False))
        self.assertEqual(code, 0)
        self.assertEqual((seen[0]["t"], seen[0]["op"], seen[0]["template"], seen[0]["ttl"]), ("form", "send", "yes-no", 2 * 86400))
        self.assertIn("End this turn now", out.getvalue())


class Service(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        self.st = State(root / "s")
        self.st.init(relay="ws://127.0.0.1:9")          # loopback: no seat certificate needed (seatcert.exempt)
        self.host = serve.Host(self.st, events="quiet", read_stdin=False)
        self.host.uploads = uploads.Uploads(self.st, str(self.work))
        self.sent = []
        self.submitted = []

        async def send_app(s, obj):
            self.sent.append(obj)
            return True
        self.host.send_app = send_app
        self.host.agent = SimpleNamespace(passthrough=lambda c: True, submit=self.submitted.append, kind="claude", cur_send=None)

    def session(self, device="d" * 16, name="iPhone"):
        return SimpleNamespace(cid=1, device=device, name=name, state="ready", p33=True, pub=b"x")

    async def send(self, raw, **kw):
        return await self.host.forms.agent({"op": "send", "form": raw, **kw})

    async def say(self, s, form, att=None, sid=None):
        sid = sid or wire.b64u(hashlib.sha256(json.dumps(form).encode() + str(time.time()).encode()).digest()[:16])
        await self.host.on_say(s, {"sid": sid, "text": "表单回复", "ts": 0, "form": form, **({"att": att} if att else {})})
        await asyncio.sleep(0)
        res = [m for m in self.sent if m.get("t") == "say_res" and m.get("sid") == sid]
        while not res:                 # say_res goes through the post queue
            await asyncio.sleep(0.01)
            res = [m for m in self.sent if m.get("t") == "say_res" and m.get("sid") == sid]
        return res[0], sid

    async def test_send_attaches_to_the_running_page_or_its_own(self):
        t = self.host.hist_add({"k": "phone", "text": "报名吗"}, "", "open")
        self.host.cur_turn = t["id"]
        res = await self.send({"title": "沙龙", "questions": [q(1, "buttons")]})
        self.assertTrue(res["ok"])
        self.assertEqual(res["turn"], t["id"])
        page = self.host.hist.get(t["id"])
        self.assertEqual((page["form"]["id"], page["form"]["mode"], page["form"]["tap"], page["form"]["state"]),
                         (res["id"], "light", True, "open"))
        self.assertIn("spec", page["form"], "a Quick Pick carries its whole spec on the page")
        res2 = await self.send({"title": "第二张", "mode": "deep", "questions": [q(1)]})
        self.assertNotEqual(res2["turn"], t["id"], "one form per page: the second gets a page of its own")
        p2 = self.host.hist.get(res2["turn"])
        self.assertEqual(p2["src"]["k"], "agent")
        self.assertNotIn("spec", p2["form"], "a Deep Dive's spec is fetched with form_get")
        self.assertEqual(p2["form"]["exp"] // 1000 - int(time.time()), 14 * 86400, "deep: 14 days")
        self.assertEqual(page["form"]["exp"] // 1000 - int(time.time()), 7 * 86400, "light: 7 days")

    async def test_refused_when_stopped_from_a_group_or_malformed(self):
        self.host.estop = {"on": True}
        self.assertEqual((await self.send({"title": "x", "questions": [q(1)]}))["result"], "stopped")
        self.host.estop = {"on": False}
        self.host.agent.cur_send = SimpleNamespace(source={"kind": "telegram_group", "from": "someone"})
        self.assertEqual((await self.send({"title": "x", "questions": [q(1)]}))["result"], "group_source")
        self.host.agent.cur_send = None
        res = await self.send({"title": "x", "questions": [{"id": "p", "type": "password", "label": "pw"}]})
        self.assertEqual((res["result"], res["why"]), ("refused", "type"))
        res = await self.send({"title": "x", "questions": [q(1)]}, ttl=60)
        self.assertEqual(res["why"], "ttl")

    async def test_tap_submit_becomes_the_owners_message(self):
        res = await self.send({"title": "首页先做哪个？", "questions": [q(1, "buttons", label="选一个", options=[
            {"value": "a", "label": "简洁版"}, {"value": "b", "label": "视频版"}], recommend="a")]})
        r, _ = await self.say(self.session(), {"id": res["id"], "answers": {"q1": "a"}})
        self.assertTrue(r["ok"], r)
        text = self.submitted[-1].text
        self.assertIn(f"〔表单回复 · {res['id']} · 快选 · 首页先做哪个？ · 1/1 题〕", text)
        self.assertIn("1. 选一个 → 简洁版（接受推荐）", text)
        self.assertIn("完整答案 JSON：", text)
        rec = self.host.forms.store.get(res["id"])
        self.assertEqual((rec["state"], rec["by"]), ("answered", "iPhone"))
        aj_path = pathlib.Path(rec["answer_path"])
        self.assertTrue(aj_path.is_file() and str(aj_path).startswith(str(self.work.resolve())), "answer JSON in the Agent's inbox")
        aj = json.loads(aj_path.read_text(encoding="utf-8"))
        self.assertEqual((aj["schema"], aj["form"], aj["answers"]["q1"]), ("agentj.form-answer/v1", res["id"],
                                                                           {"value": "a", "label": "简洁版", "recommended": True}))
        page = self.host.hist.get(res["turn"])
        self.assertEqual((page["form"]["state"], page["form"]["rows"]), ("answered", [["选一个", "简洁版", True]]))
        mine = self.host.hist.get(r["turn"])
        self.assertEqual(mine["src"]["form"], {"id": res["id"], "title": "首页先做哪个？"})

    async def test_first_submission_wins(self):
        res = await self.send({"title": "x", "questions": [q(1), q(2, "text")]})
        r1, _ = await self.say(self.session(), {"id": res["id"], "answers": {"q1": "a", "q2": "hi"}})
        r2, _ = await self.say(self.session("e" * 16, "Pixel"), {"id": res["id"], "answers": {"q1": "b"}})
        self.assertTrue(r1["ok"])
        self.assertEqual((r2["ok"], r2["why"]), (False, "form_answered"))
        self.assertEqual(len(self.submitted), 1)

    async def test_host_rechecks_answers(self):
        res = await self.send({"title": "x", "questions": [q(1, required=True), q(2, "number", min=1, max=5)]})
        for form, why in (({"id": res["id"], "answers": {"q2": 3}}, "form_required"),
                          ({"id": res["id"], "answers": {"q1": "zz"}}, "form_answer"),
                          ({"id": res["id"], "answers": {"q1": "a", "q2": 9}}, "form_answer"),
                          ({"id": "f_" + "1" * 16, "answers": {}}, "form_gone"),
                          ({"id": res["id"]}, "form_shape")):
            r, _ = await self.say(self.session(), form)
            self.assertEqual((r["ok"], r["why"]), (False, why))
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.host.forms.store.get(res["id"])["state"], "open")

    async def test_expired_and_cancelled(self):
        res = await self.send({"title": "x", "questions": [q(1)]})
        rec = self.host.forms.store.get(res["id"])
        rec["expires"] = time.time() - 1
        self.host.forms.store.save(rec)
        r, _ = await self.say(self.session(), {"id": res["id"], "answers": {"q1": "a"}})
        self.assertEqual(r["why"], "form_expired")
        res = await self.send({"title": "y", "questions": [q(1)]})
        self.assertEqual((await self.host.forms.agent({"op": "cancel", "id": res["id"]}))["result"], "cancelled")
        self.assertEqual(self.host.hist.get(res["turn"])["form"]["state"], "cancelled")
        r, _ = await self.say(self.session(), {"id": res["id"], "answers": {"q1": "a"}})
        self.assertEqual(r["why"], "form_cancelled")
        self.assertFalse((await self.host.forms.agent({"op": "cancel", "id": res["id"]}))["ok"])

    async def test_withdrawn_answer_reopens_the_form(self):
        res = await self.send({"title": "x", "questions": [q(1, "buttons")]})
        s = self.session()
        r, sid = await self.say(s, {"id": res["id"], "answers": {"q1": "b"}})      # queued (the fake Agent never delivers)
        self.assertEqual(self.host.forms.store.get(res["id"])["state"], "answered")
        await self.host.on_p33(s, "say_cancel", {"t": "say_cancel", "sid": sid})
        self.assertEqual([m["r"] for m in self.sent if m.get("t") == "say_cancel_res"], ["cancelled"])
        rec = self.host.forms.store.get(res["id"])
        self.assertEqual(rec["state"], "open")
        self.assertNotIn("answer", rec)
        self.assertEqual(self.host.hist.get(res["turn"])["form"]["state"], "open")
        r2, _ = await self.say(s, {"id": res["id"], "answers": {"q1": "a"}})
        self.assertTrue(r2["ok"], "answerable again after the withdraw")

    async def test_form_get_and_drafts(self):
        res = await self.send({"title": "深聊", "mode": "deep", "sections": [{"questions": [q(1, required=True)]},
                                                                           {"questions": [q(2, "longtext")]}]})
        s = self.session()
        await self.host.forms.on_phone(s, {"t": "form_get", "r": "r1", "id": res["id"]})
        got = self.sent[-1]
        self.assertEqual((got["t"], got["ok"], got["state"], len(got["spec"]["sections"])), ("form_res", True, "open", 2))
        self.assertNotIn("draft", got)
        await self.host.forms.on_phone(s, {"t": "form_draft", "id": res["id"], "answers": {"q1": "b"}, "sec": 1})
        self.assertTrue(self.sent[-1]["ok"])
        await self.host.forms.on_phone(self.session("e" * 16, "Mac"), {"t": "form_get", "r": "r2", "id": res["id"]})
        self.assertEqual((self.sent[-1]["draft"]["answers"], self.sent[-1]["draft"]["sec"], self.sent[-1]["draft"]["by"]),
                         ({"q1": "b"}, 1, "iPhone"), "another device continues from the draft on this computer")
        await self.host.forms.on_phone(s, {"t": "form_draft", "id": res["id"], "answers": {"q1": "zz"}})
        self.assertEqual((self.sent[-1]["ok"], self.sent[-1]["why"]), (False, "answer"))
        r, _ = await self.say(s, {"id": res["id"], "answers": {"q1": "a", "q2": "长\n答案"}})
        self.assertTrue(r["ok"])
        self.assertIsNone(self.host.forms.store.draft(res["id"]), "the draft goes once answered")
        await self.host.forms.on_phone(s, {"t": "form_get", "r": "r3", "id": res["id"]})
        self.assertEqual((self.sent[-1]["state"], self.sent[-1]["rows"][1][1]), ("answered", "长\n答案"))
        await self.host.forms.on_phone(s, {"t": "form_draft", "id": res["id"], "answers": {}})
        self.assertEqual(self.sent[-1]["why"], "answered")
        self.assertIn("   长\n   答案", self.submitted[-1].text, "a multi-line answer is indented under its question")

    async def test_attachments_go_with_the_submission(self):
        res = await self.send({"title": "Bug", "questions": [q(1, "file", accept=["image"], max_files=2), q(2, "text")]})
        s = self.session()
        data = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        bid = wire.b64u(b"b" * 16)
        self.host.uploads.open(s.device, {"bid": bid, "purpose": "att", "name": "shot.png", "mime": "image/png", "size": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest(), "origin": "file"})
        self.host.uploads.chunk(s.device, {"bid": bid, "o": 0, "d": wire.b64u(data)})
        out, _ = self.host.uploads.end(s.device, bid)
        self.assertTrue(out[-1]["ok"], out)
        r, _ = await self.say(s, {"id": res["id"], "answers": {"q1": [bid], "q2": "看图"}}, att=[bid])
        self.assertTrue(r["ok"], r)
        rec = self.host.forms.store.get(res["id"])
        path = rec["answer"]["answers"]["q1"]["files"][0]
        self.assertTrue(path.endswith(".png") and pathlib.Path(path).is_file())
        self.assertIn(path, self.submitted[-1].text, "compose.render lists the attachment for the Agent")
        self.assertEqual(self.host.hist.get(res["turn"])["form"]["files"], 1)
        res2 = await self.send({"title": "Bug2", "questions": [q(1, "file"), q(2, "text")]})
        r, _ = await self.say(s, {"id": res2["id"], "answers": {"q2": "x"}}, att=[bid])
        self.assertEqual(r["why"], "form_answer", "an attachment that belongs to no question is refused")

    async def test_list_and_show(self):
        res = await self.send({"title": "x", "questions": [q(1)]}, template="decide-3")
        ls = await self.host.forms.agent({"op": "list"})
        self.assertEqual((ls["forms"][0]["id"], ls["forms"][0]["template"], ls["forms"][0]["state"]), (res["id"], "decide-3", "open"))
        await self.say(self.session(), {"id": res["id"], "answers": {"q1": "c"}})
        sh = await self.host.forms.agent({"op": "show", "id": res["id"]})
        self.assertEqual(sh["answer"]["answers"]["q1"]["value"], "c")
        self.assertEqual((await self.host.forms.agent({"op": "show", "id": "f_" + "9" * 16}))["result"], "not_found")

    async def test_store_is_private(self):
        res = await self.send({"title": "x", "questions": [q(1)]})
        p = self.st.root / "forms" / f"{res['id']}.json"
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.st.root / "forms").stat().st_mode & 0o777, 0o700)
        logs = self.st.log_path.read_text(encoding="utf-8") if self.st.log_path.exists() else ""
        self.assertNotIn("Question 1", logs, "logs carry ids and counts, never text")


if __name__ == "__main__":
    unittest.main()
