"""P394 (#130) the To-do card kept by the Agent and the owner together: the version-2 item fields (old files read without
loss), `agentj card todo …` (atomic changes, the due list), what the phone gets (owner, status, start / end, marks, order)
and the rules every wake-up carries (identity, heartbeat, compaction, recovery, morning skill). Throw-away state dirs only."""
import _hermetic  # noqa: F401  (first: a throw-away AGENTJ_STATE_DIR for this process)
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import jcards, jcards_todo as todo

AJ = pathlib.Path(__file__).resolve().parents[2]
FAKE_KEY = "AKIA" + "Q3EGRKX7" + "PLM2VN4B"   # an access-key shape privacy.redact must hide
NOW = dt.datetime(2026, 10, 19, 12, 0, tzinfo=dt.timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p394-")
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name) / "state"
        self.root.mkdir()
        self.file = jcards.data_dir(self.root, "todo") / "todo.json"
        # P393 #129 turns official cards on by default (the sample lands once); these cases start from a card the owner
        # switched off, so ids and order are only what each test writes. DefaultOn covers the shipped default.
        jcards._save_switches(self.root, {"todo": False})

    def write(self, doc):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def stored(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def add(self, text, **kw):
        r = todo.change(self.root, "add", now=NOW, text=text, **kw)
        self.assertTrue(r["ok"], r)
        return r["item"]["id"]

    def cli(self, *args):
        out = io.StringIO()
        with mock.patch("agentj.state.state_dir", return_value=self.root), contextlib.redirect_stdout(out):
            code = jcards.command(["todo", *args])
        return code, out.getvalue()


class Fields(Base):
    def test_old_file_reads_without_loss_and_keeps_ids_text_order_when_rewritten(self):
        old = {"items": [{"id": "t1", "text": "交房租", "done": False}, {"id": "t2", "text": "看牙", "done": True}, "裸字符串",
                         {"text": ""}, 7]}
        self.write(old)
        items, bad = todo.load(self.file.read_bytes())
        self.assertIsNone(bad)
        self.assertEqual([(i["id"], i["text"], i["status"], i["owner"], i["by"], i["done"]) for i in items],
                         [("t1", "交房租", "pending", "owner", "owner", False), ("t2", "看牙", "done", "owner", "owner", True),
                          ("i3", "裸字符串", "pending", "owner", "owner", False)])
        self.assertTrue(all(i["start"] is None and i["end"] is None for i in items))
        new = self.add("新的一条", owner="agent")
        s = self.stored()
        self.assertEqual(s["schema"], 2)
        self.assertEqual([(i["id"], i["text"]) for i in s["items"] if isinstance(i, dict) and "id" in i],
                         [("t1", "交房租"), ("t2", "看牙"), ("i3", "裸字符串"), (new, "新的一条")])
        self.assertEqual(s["items"][3:5], [{"text": ""}, 7])         # not items: kept as they were
        self.assertEqual(new, "t3")
        self.assertEqual(s["items"][1]["status"], "done")

    def test_conditions_from_words(self):
        c = todo.condition
        self.assertIsNone(c(""))
        self.assertEqual(c("2026-10-20"), {"at": "2026-10-20"})
        self.assertEqual(c("+2h", NOW), {"at": todo._iso(NOW + dt.timedelta(hours=2))})
        self.assertEqual(c("+1d", NOW)["at"][:10], (NOW + dt.timedelta(days=1)).astimezone().isoformat()[:10])
        self.assertEqual(c("2026-10-20T09:00+09:00"), {"at": "2026-10-20T09:00+09:00"})
        self.assertEqual(c("after:t3"), {"after": "t3"})
        self.assertEqual(c("等 t3"), {"after": "t3"})
        self.assertEqual(c("主人回了邮件"), {"when": "主人回了邮件"})
        for bad, why in (("2026-13-40T99:00", "bad_time"), ("x" * 201, "too_long"), ("用 " + FAKE_KEY, "looks_like_a_secret")):
            with self.assertRaises(ValueError) as e:
                c(bad)
            self.assertEqual(str(e.exception), why)

    def test_add_stores_every_field(self):
        self.add("明天跟进报价", owner="agent", start="2026-10-20T09:00+09:00", end="2026-10-21", by="owner",
                 source="10-19 语音", note="报价单在邮箱")
        x = self.stored()["items"][0]
        self.assertEqual({k: x[k] for k in ("id", "text", "owner", "status", "done", "start", "end", "by", "source", "note", "finished")},
                         {"id": "t1", "text": "明天跟进报价", "owner": "agent", "status": "pending", "done": False,
                          "start": {"at": "2026-10-20T09:00+09:00"}, "end": {"at": "2026-10-21"}, "by": "owner",
                          "source": "10-19 语音", "note": "报价单在邮箱", "finished": None})
        self.assertTrue(x["created"].startswith("2026-10-19"))

    def test_refusals_change_nothing(self):
        self.add("a")
        before = self.file.read_bytes()
        for kw, why in (({"text": "rotate " + FAKE_KEY}, "looks_like_a_secret"), ({"text": " "}, "bad_text"),
                        ({"text": "x", "owner": "boss"}, "bad_owner"), ({"text": "x", "start": "after:t9"}, "bad_after")):
            self.assertEqual(todo.change(self.root, "add", now=NOW, **kw)["why"], why)
        self.assertEqual(todo.change(self.root, "done", "t9")["why"], "not_found")
        self.assertEqual(todo.change(self.root, "edit", "t1", start="after:t1")["why"], "bad_after")
        self.assertEqual(self.file.read_bytes(), before)

    def test_unreadable_file_is_never_overwritten(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{ not json")
        self.assertEqual(todo.change(self.root, "add", text="x")["why"], "bad_data")
        self.assertEqual(self.file.read_text(), "{ not json")

    def test_close_reopen_toggle_edit(self):
        self.add("a", owner="agent")
        r = todo.change(self.root, "done", "t1", now=NOW, note="发了")
        self.assertEqual((r["item"]["status"], r["item"]["note"]), ("done", "发了"))
        x = self.stored()["items"][0]
        self.assertTrue(x["done"] and x["finished"])
        todo.change(self.root, "reopen", "t1")
        x = self.stored()["items"][0]
        self.assertEqual((x["status"], x["done"], x["finished"]), ("pending", False, None))
        todo.change(self.root, "toggle", "t1")
        self.assertEqual(self.stored()["items"][0]["status"], "done")
        todo.change(self.root, "toggle", "t1")
        todo.change(self.root, "cancel", "t1")
        x = self.stored()["items"][0]
        self.assertEqual((x["status"], x["done"]), ("cancelled", False))
        todo.change(self.root, "edit", "t1", status="active", owner="owner", text="b", end="2026-10-30", note="")
        x = self.stored()["items"][0]
        self.assertEqual((x["status"], x["owner"], x["text"], x["end"], x["note"], x["finished"]),
                         ("active", "owner", "b", {"at": "2026-10-30"}, None, None))
        todo.change(self.root, "edit", "t1", end="")
        self.assertIsNone(self.stored()["items"][0]["end"])

    def test_a_rewrite_keeps_rows_and_fields_it_does_not_know(self):
        self.write({"title": "mine", "items": [{"id": "t1", "text": "a", "done": False, "color": "red"}, 7, {"text": ""}, "裸"]})
        todo.change(self.root, "done", "t1")
        s = self.stored()
        self.assertEqual(s["title"], "mine")
        self.assertEqual(s["items"][0]["color"], "red")
        self.assertEqual(s["items"][0]["status"], "done")
        self.assertEqual(s["items"][1:3], [7, {"text": ""}])
        self.assertEqual(s["items"][3]["text"], "裸")

    def test_more_than_200_rows_are_never_cut_by_a_rewrite(self):
        self.write({"items": [{"id": f"t{n}", "text": str(n), "done": False} for n in range(1, 202)]})
        before = self.file.read_bytes()
        self.assertEqual(todo.change(self.root, "done", "t1")["why"], "full")
        self.assertEqual(self.file.read_bytes(), before)

    def test_card_off_still_keeps_the_item_and_says_so(self):
        r = todo.change(self.root, "add", text="记下", owner="agent")
        self.assertEqual((r["ok"], r["on"], r["address"]), (True, False, "card/todo#t1"))
        jcards.set_on(self.root, "todo", True)          # the sample never lands over data
        self.assertEqual([i["text"] for i in self.stored()["items"]], ["记下"])


class DefaultOn(unittest.TestCase):
    def test_a_never_switched_card_is_on_and_the_first_write_keeps_the_sample_before_it(self):
        with tempfile.TemporaryDirectory(prefix="p394-") as t:
            root = pathlib.Path(t) / "state"
            root.mkdir()
            r = todo.change(root, "add", now=NOW, text="记下", owner="agent")
            self.assertEqual((r["ok"], r["on"]), (True, True))
            doc = json.loads((jcards.data_dir(root, "todo") / "todo.json").read_text(encoding="utf-8"))
            texts = [i["text"] for i in doc["items"] if isinstance(i, dict)]
            self.assertEqual(texts[-1], "记下")
            self.assertGreater(len(texts), 1)             # the sample, created once and never over data
            self.assertEqual(r["address"], "card/todo#" + r["item"]["id"])


class Due(Base):
    def setUp(self):
        super().setUp()
        self.add("过期的", owner="agent", end="2026-10-18")                                 # t1 overdue
        self.add("快到期", owner="agent", end=(NOW + dt.timedelta(hours=3)).isoformat())   # t2 due soon
        self.add("等 t1 做完", owner="agent", start="after:t1")                             # t3 waiting
        self.add("下周才开始", owner="agent", start="2026-10-26")                           # t4 waiting
        self.add("看条件", owner="agent", start="主人回了邮件")                              # t5 check
        self.add("主人的", owner="owner", end="2026-10-18")                                 # t6 owner overdue
        self.add("没条件", owner="agent")                                                   # t7 open
        self.add("开始时间到了", owner="agent", start="2026-10-19T08:00+00:00")              # t8 ready

    def test_due_lists_what_the_agent_should_handle_now(self):
        rows = todo.listing(self.root, "due", now=NOW)["items"]
        self.assertEqual([(r["id"], r["state"]) for r in rows],
                         [("t1", "overdue"), ("t2", "due"), ("t8", "ready"), ("t5", "open"), ("t7", "open")])
        self.assertEqual(rows[0]["flags"], ["overdue"])
        self.assertIn("check", next(r for r in rows if r["id"] == "t5")["flags"])
        self.assertEqual([r["id"] for r in todo.listing(self.root, "due", "owner", now=NOW)["items"]], ["t6"])

    def test_a_dependency_done_makes_the_next_ready(self):
        todo.change(self.root, "done", "t1", now=NOW)
        rows = {r["id"]: r for r in todo.listing(self.root, "due", now=NOW)["items"]}
        self.assertNotIn("t1", rows)
        self.assertEqual((rows["t3"]["state"], rows["t3"]["flags"]), ("ready", ["ready"]))
        todo.change(self.root, "start", "t3", now=NOW)
        rows = {r["id"]: r for r in todo.listing(self.root, "due", now=NOW)["items"]}
        self.assertEqual((rows["t3"]["state"], rows["t3"]["flags"]), ("active", []))

    def test_end_after_another_item(self):
        self.add("收尾", owner="agent", end="after:t7")
        todo.change(self.root, "done", "t7", now=NOW)
        r = next(x for x in todo.listing(self.root, "due", now=NOW)["items"] if x["text"] == "收尾")
        self.assertEqual((r["state"], r["flags"]), ("due", ["end_met"]))

    def test_list_order_and_owner_filter(self):
        todo.change(self.root, "done", "t7", now=NOW)
        todo.change(self.root, "cancel", "t5", now=NOW)
        ids = [r["id"] for r in todo.listing(self.root, "list", now=NOW)["items"]]
        self.assertEqual(ids, ["t1", "t6", "t2", "t8", "t3", "t4", "t7", "t5"])
        self.assertEqual([r["id"] for r in todo.listing(self.root, "list", "owner", now=NOW)["items"]], ["t6"])

    def test_cli(self):
        jcards.set_on(self.root, "todo", True)
        code, out = self.cli("due", "--json")
        r = json.loads(out)
        self.assertEqual((code, r["ok"], r["on"]), (0, True, True))
        self.assertTrue(all(x["owner"] == "agent" for x in r["items"]))
        code, out = self.cli("add", "--text", "跟进合同", "--owner", "agent", "--end", "+1d", "--source", "对话", "--json")
        r = json.loads(out)
        self.assertEqual((code, r["item"]["id"], r["item"]["by"], r["address"]), (0, "t9", "agent", "card/todo#t9"))
        code, out = self.cli("done", "--entry", "t9")
        self.assertEqual(code, 0)
        self.assertIn("t9 done", out)
        code, out = self.cli("done", "--entry", "t99")
        self.assertEqual(code, 1)
        self.assertIn("not_found", out)
        code, out = self.cli("add", "--text", "rotate " + FAKE_KEY)
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_KEY, out)
        self.assertIn("todo", jcards.SUBCOMMANDS)


class Phone(Base):
    def test_the_card_gets_owner_status_conditions_marks_counts_in_order(self):
        jcards.set_on(self.root, "todo", True)
        self.write({"items": [{"id": "t1", "text": "旧的已完成", "done": True}, {"id": "t2", "text": "旧的未完成", "done": False}]})
        self.add("Agent 的，过期", owner="agent", end="2026-10-18", note="备注")
        self.add("等 t3", owner="agent", start="after:t3")
        self.add("取消的", owner="owner")
        todo.change(self.root, "cancel", "t5")
        s = self.stored()
        s["items"][2]["note"] = "备注 " + FAKE_KEY          # a hand edit: the phone still never sees it
        self.write(s)
        with mock.patch.object(todo, "now_utc", return_value=NOW):
            g = jcards.phone_get(self.root, "todo", "zh")
        self.assertEqual(g["todo"]["counts"], {"all": 5, "agent": 2, "owner": 3})
        rows = g["items"]
        self.assertEqual([r["id"] for r in rows], ["t3", "t2", "t4", "t1", "t5"])
        t3, t4 = rows[0], rows[2]
        self.assertEqual((t3["owner"], t3["state"], t3["flags"], t3["end"]["kind"], t3["end"]["date"]),
                         ("agent", "overdue", ["overdue"], "at", True))
        self.assertNotIn(FAKE_KEY, json.dumps(g))
        self.assertEqual((t4["start"]["kind"], t4["start"]["met"], t4["start"]["text"], t4["state"]),
                         ("after", False, "Agent 的，过期", "waiting"))
        self.assertEqual([(r["id"], r["done"], r["status"]) for r in rows[3:]], [("t1", True, "done"), ("t5", True, "cancelled")])
        self.assertTrue(all(r["acts"][0]["prompt"] == f"/agentj-add-todo toggle {r['id']}" for r in rows))

    def test_a_hand_edited_key_never_leaves_the_host(self):
        """Security review P394: every field of a hand-edited file — a non-time `at`, created / finished, an `after` id,
        the text — is redacted before the phone or the Agent's conversation sees it."""
        jcards.set_on(self.root, "todo", True)
        gh = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        self.write({"items": [{"id": "t1", "text": "rotate " + FAKE_KEY, "start": {"at": FAKE_KEY}, "end": {"after": FAKE_KEY},
                               "created": gh, "finished": gh, "note": gh, "source": gh, "done": False},
                              {"id": "t2", "text": "x", "start": {"when": "after " + gh}, "done": False}]})
        g = jcards.phone_get(self.root, "todo", "zh")
        for leak in (FAKE_KEY, gh):
            self.assertNotIn(leak, json.dumps(g, ensure_ascii=False))
            for op in ("list", "due"):
                self.assertNotIn(leak, json.dumps(todo.listing(self.root, op, now=NOW), ensure_ascii=False))
                self.assertNotIn(leak, self.cli(op)[1])
        t1 = next(i for i in g["items"] if i["id"] == "t1")
        self.assertEqual((t1["start"]["kind"], t1["created"], t1["finished"]), ("when", None, None))

    def test_other_checklist_cards_are_unchanged(self):
        doc = json.loads((jcards.official_dir("todo") / "card.json").read_text(encoding="utf-8"))
        doc.update({"id": "reading-list", "name": {"zh": "书单", "en": "Reading list"}})
        d = pathlib.Path(tempfile.mkdtemp(prefix="p394-card-", dir=self.root.parent))
        (d / "card.json").write_text(json.dumps(doc, ensure_ascii=False))
        self.assertTrue(jcards.apply(self.root, d)["ok"])
        jcards.data_dir(self.root, "reading-list").mkdir(parents=True, exist_ok=True)
        (jcards.data_dir(self.root, "reading-list") / "todo.json").write_text(json.dumps({"items": [{"id": "a", "text": "x", "done": False}]}))
        g = jcards.phone_get(self.root, "reading-list")
        self.assertNotIn("todo", g)
        self.assertEqual(set(g["items"][0]) - {"acts"}, {"id", "text", "done"})

    def test_sample_is_version_2_and_validates(self):
        doc, errs = jcards.load(jcards.official_dir("todo"))
        self.assertEqual(errs, [])
        s = json.loads((jcards.official_dir("todo") / "sample" / "todo.json").read_text(encoding="utf-8"))
        self.assertEqual(s["schema"], 2)
        self.assertEqual({i["owner"] for i in s["items"]}, {"agent", "owner"})


class Rules(unittest.TestCase):
    """Rule 4: a promise goes on the card; a new session, a compaction, a heartbeat and the morning brief check what is due."""

    def test_identity_line_on_every_harness(self):
        from agentj import main_identity as m
        for lang in ("zh", "en"):
            for harness in (None, "codex", "claude"):
                text = m.prompt({"language": lang}, harness=harness)
                self.assertIn((m.TODO_LINE_CLAUDE if harness == "claude" else m.TODO_LINE)[lang], text)
                self.assertIn("agentj card todo due", text)
                self.assertIn("agentj-add-todo" if harness == "claude" else "--owner agent", text)
            self.assertIn("不是指令" if lang == "zh" else "never instructions", m.TODO_LINE[lang])

    def test_heartbeat_compaction_recovery_morning(self):
        from agentj import heartbeat as hb, handoff, compactprep
        for lang in ("zh", "en"):
            for full in (True, False):
                self.assertIn("agentj card todo due", hb.message(lang, "x", "abcd1234", full, 60))
            self.assertIn("agentj card todo due", hb.TEMPLATE[lang])
            self.assertIn("agentj card todo due", compactprep.NOTE[lang])
            for kind, has in (("compact", True), ("compact", False), ("clear", True)):
                self.assertIn("agentj card todo due", handoff.recover_text(lang, kind, has))
        for skill in ("agentj-good-morning", "agentj-heartbeat", "agentj-handoff", "agentj-add-todo"):
            self.assertIn("agentj card todo due", (AJ / f"host/agentj/skills/{skill}/SKILL.md").read_text(encoding="utf-8"), skill)

    def test_skill_names_every_operation(self):
        text = (AJ / "host/agentj/skills/agentj-add-todo/SKILL.md").read_text(encoding="utf-8")
        for w in ("--owner agent", "after:t3", "+1d", "--by owner", "--source", "done|cancel|reopen|start|toggle", "todo edit",
                  "todo due --json", "--owner owner", "agentj card open todo --at"):
            self.assertIn(w, text)

    def test_docs_explain_fields_and_filter(self):
        for lang, words in (("en", ("Agent J's", "Mine", "Overdue", "after:t3", "agentj card todo due")),
                            ("zh", ("Agent J 负责", "我负责", "已过期", "after:t3", "agentj card todo due"))):
            for path in (AJ / f"site/content/docs/49-cards/{lang}.md", AJ / f"host/agentj/skills/agentj-manual/cards.{lang}.md"):
                if "site" in path.parts and not path.exists():
                    continue                                    # the public export ships the manual copy, not the site docs
                text = path.read_text(encoding="utf-8")
                for w in words:
                    self.assertIn(w, text, f"{path.name}: {w}")


if __name__ == "__main__":
    unittest.main()
