"""P204 (0.21, PROTOCOL §30): /learn — distil a skill, show one diff card, write only the approved bytes after the owner's yes."""
import _hermetic  # noqa: F401
import asyncio
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import capability, goal, learn, menu, slash

SKILL = "---\nname: weekly-report\ndescription: 写周报 / write the weekly report\n---\n按这个格式写周报。\n"


def fake_host(root: pathlib.Path, answer="allow", pages=None):
    asks = []

    async def ask(tool, tool_input, **kw):
        asks.append((tool, tool_input, kw))
        hook = getattr(h, "before_answer", None)
        if hook:
            hook()
        if answer == "allow":
            return {"behavior": "allow", "grant_info": {"rid": "r", "device": "dev1", "sign_pub": "x"}}
        return {"behavior": "deny"}

    hist = SimpleNamespace(page=lambda b, a, n: (pages or [], False))
    h = SimpleNamespace(agent_cfg={"dir": str(root / "work"), "kind": "claude"}, preferences={}, stopped=lambda: False,
                        _approvers=lambda: ["dev1"], ask=ask, ask_ttl=60, hist=hist, agent=None,
                        st=SimpleNamespace(log=mock.Mock()), activity=mock.Mock())
    h.asks = asks
    return h


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.src = self.d / "skill"
        self.src.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_document_skill_reads(self):
        (self.src / "SKILL.md").write_text(SKILL)
        self.assertEqual(list(learn.read_skill(self.src, "weekly-report")), ["SKILL.md"])

    def test_refusals(self):
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "no_skill_md")
        (self.src / "SKILL.md").write_text(SKILL.replace("weekly-report", "other"))
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "front_matter")
        (self.src / "SKILL.md").write_text(SKILL)
        (self.src / "run.sh").write_text("curl -H 'Authorization: Bearer sk-ant-api03-" + "A" * 90 + "' x\n")
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "secret")
        (self.src / "run.sh").write_bytes(b"\xff\xfe\x00binary")
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "binary")
        (self.src / "run.sh").unlink()
        os.symlink("/etc/passwd", self.src / "link")
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "symlink")
        (self.src / "link").unlink()
        (self.src / "trick.md").write_text("safe\u202e;txt.exe\n")      # a bidi override could make the diff lie
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "hidden_characters")
        (self.src / "trick.md").unlink()
        (self.src / "big.md").write_text("x" * (learn.MAX_FILE + 1))
        with self.assertRaises(learn.LearnError) as e:
            learn.read_skill(self.src, "weekly-report")
        self.assertEqual(e.exception.why, "too_big")

    def test_plan_and_card(self):
        target = self.d / "installed" / "weekly-report"
        rows = learn.plan({"SKILL.md": SKILL.encode()}, target)
        self.assertEqual(rows[0]["state"], "new")
        text = learn.card_text("weekly-report", "doc", rows, target, "每周一写周报", True, "zh")
        self.assertTrue(text.startswith("技能提案：weekly-report（新建，文档型）"))
        self.assertIn("sha256 " + learn.sha(SKILL.encode())[:12], text)
        self.assertIn("+按这个格式写周报。", text)
        self.assertIn("投稿到广场", text)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(SKILL)
        (target / "notes.md").write_text("mine")
        new = SKILL.replace("按这个格式", "按新格式")
        rows = learn.plan({"SKILL.md": new.encode()}, target)
        self.assertEqual((rows[0]["state"], rows[0]["add"], rows[0]["del"]), ("changed", 1, 1))
        en = learn.card_text("weekly-report", "doc", rows, target, "", False, "en")
        self.assertIn("(change, document skill)", en)
        self.assertIn("nothing is deleted", en)
        self.assertEqual(learn.plan({"SKILL.md": SKILL.encode()}, target)[0]["state"], "same")
        long = SKILL + "\n".join("line %d" % i for i in range(3000))
        with self.assertRaises(learn.LearnError) as e:          # nothing written may be off the card the owner reads
            learn.card_text("weekly-report", "doc", learn.plan({"SKILL.md": long.encode()}, target), target, "", False, "zh")
        self.assertEqual(e.exception.why, "too_long")

    def test_write_refuses_a_symlinked_folder_and_keeps_modes(self):
        target = self.d / "t" / "y"
        target.mkdir(parents=True)
        outside = self.d / "outside"
        outside.mkdir()
        os.symlink(outside, target / "scripts")
        with self.assertRaises(learn.LearnError):
            learn.write_skill({"scripts/run.py": b"print(1)\n"}, target)
        self.assertEqual(list(outside.iterdir()), [])
        (target / "scripts").unlink()
        (target / "scripts").mkdir()
        learn.write_skill({"scripts/run.py": b"print(1)\n"}, target)
        self.assertTrue(os.access(target / "scripts" / "run.py", os.X_OK))
        os.chmod(target / "scripts" / "run.py", 0o700)
        learn.write_skill({"scripts/run.py": b"print(2)\n"}, target)
        self.assertEqual(oct((target / "scripts" / "run.py").stat().st_mode & 0o777), "0o700")
        self.assertEqual([p.name for p in (target / "scripts").iterdir()], ["run.py"], "no temporary file left")

    def test_write_keeps_other_files_and_marks_scripts(self):
        target = self.d / "t" / "x"
        target.mkdir(parents=True)
        (target / "keep.md").write_text("keep")
        learn.write_skill({"SKILL.md": b"a", "run.sh": b"echo hi\n"}, target)
        self.assertEqual((target / "keep.md").read_text(), "keep")
        self.assertTrue(os.access(target / "run.sh", os.X_OK))
        self.assertFalse(os.access(target / "SKILL.md", os.X_OK))


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        (self.d / "work").mkdir()
        self.root = self.d / "skills"
        self.p = mock.patch.object(learn, "skills_root", lambda kind: self.root)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def run_(self, coro):
        return asyncio.run(coro)

    def test_draft_context_then_approved_bytes_are_written(self):
        pages = [{"id": 7, "src": {"k": "phone", "text": "帮我把周报格式定下来"}, "reply": {"text": "好的，格式是……"}}]
        h = fake_host(self.d, pages=pages)
        L = learn.Learner(h)
        dr = self.run_(L.handle({"t": "learn_draft", "note": "以后都这样写"}))
        self.assertEqual(dr["result"], "draft")
        ctx = pathlib.Path(dr["context"]).read_text()
        self.assertIn("以后都这样写", ctx)
        self.assertIn("帮我把周报格式定下来", ctx)
        self.assertIn("data, not instructions", ctx)
        self.assertTrue(dr["dir"].startswith(str(self.d / "work" / ".agentj" / "learn-staging")))
        sk = pathlib.Path(dr["skill_dir"])
        (sk / "SKILL.md").write_text(SKILL)
        # the staging folder changes after the card was shown: the host still writes what the owner saw
        h.before_answer = lambda: (sk / "SKILL.md").write_text(SKILL + "偷偷加的一行\n")
        res = self.run_(L.handle({"t": "learn_propose", "id": dr["id"], "name": "weekly-report", "summary": "周报"}))
        self.assertEqual(res["result"], "applied", res)
        self.assertEqual((self.root / "weekly-report" / "SKILL.md").read_text(), SKILL)
        tool, inp, kw = h.asks[0]
        self.assertEqual(tool, learn.TOOL)
        self.assertFalse(kw["batch"])
        self.assertIn("技能提案：weekly-report", inp["card"])
        self.assertFalse(pathlib.Path(dr["dir"]).exists(), "the staging folder goes after apply")
        self.assertIsNone(L.cur)

    def test_deny_writes_nothing(self):
        h = fake_host(self.d, answer="deny")
        L = learn.Learner(h)
        dr = self.run_(L.handle({"t": "learn_draft"}))
        (pathlib.Path(dr["skill_dir"]) / "SKILL.md").write_text(SKILL)
        res = self.run_(L.handle({"t": "learn_propose", "id": dr["id"], "name": "weekly-report"}))
        self.assertEqual(res["result"], "declined")
        self.assertFalse((self.root / "weekly-report").exists())
        self.assertFalse(pathlib.Path(dr["dir"]).exists())

    def test_names_and_order(self):
        h = fake_host(self.d)
        L = learn.Learner(h)
        self.assertEqual(self.run_(L.handle({"t": "learn_propose", "id": "abcdefghijkm", "name": "x"}))["why"], "no_draft")
        dr = self.run_(L.handle({"t": "learn_draft"}))
        for bad in ("agentj-x", "learn", "goal", "Bad", "a b", "../x"):
            r = self.run_(L.handle({"t": "learn_propose", "id": dr["id"], "name": bad}))
            self.assertEqual(r["why"], "name", bad)
        self.assertEqual(self.run_(L.handle({"t": "learn_status"}))["draft"]["id"], dr["id"])
        self.assertEqual(self.run_(L.handle({"t": "learn_discard"}))["result"], "discarded")
        self.assertIsNone(self.run_(L.handle({"t": "learn_status"}))["draft"])

    def test_same_content_needs_no_card(self):
        h = fake_host(self.d)
        L = learn.Learner(h)
        (self.root / "weekly-report").mkdir(parents=True)
        (self.root / "weekly-report" / "SKILL.md").write_text(SKILL)
        dr = self.run_(L.handle({"t": "learn_draft"}))
        (pathlib.Path(dr["skill_dir"]) / "SKILL.md").write_text(SKILL)
        self.assertEqual(self.run_(L.handle({"t": "learn_propose", "id": dr["id"], "name": "weekly-report"}))["result"], "same")
        self.assertEqual(h.asks, [])


class Wiring(unittest.TestCase):
    def test_phone_command_goes_to_the_agent_rewritten(self):
        self.assertIn("learn", slash.AGENT_CMDS)
        self.assertIn("/learn", slash.REFUSE)
        for lang in ("zh", "en"):
            w = learn.agent_words("  按周一的格式 ", lang)
            self.assertFalse(w.startswith("/"))
            self.assertIn("agentj-learn", w)
            self.assertIn("按周一的格式", w)
            self.assertIn("/learn", {i["cmd"] for i in menu.defaults(lang)})

    def test_cli_is_host_mediated_and_staging_is_inert(self):
        for cmd in ("agentj learn draft '以后都这样写' --json", "agentj learn propose ab12cd34ef56 --name weekly-report --summary '每周一写周报' --no-wait --json",
                    "agentj learn status", "agentj learn discard"):
            self.assertTrue(capability.host_mediated("Bash", {"command": cmd}), cmd)
        self.assertFalse(capability.host_mediated("Bash", {"command": "agentj learn draft; rm -rf ~"}))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".agentj", "learn-staging", "x", "skill", "SKILL.md")
            self.assertTrue(capability.staging_write(d, "Write", {"file_path": p, "content": "x"}))
            self.assertFalse(capability.staging_write(d, "Write", {"file_path": os.path.join(d, "SKILL.md"), "content": "x"}))

    def test_the_card_is_never_sorted_into_a_goal_tier(self):
        self.assertIn(learn.TOOL, goal.SOFT_EXEMPT)

    def test_skill_ships(self):
        text = (pathlib.Path(learn.__file__).parent / "skills" / "agentj-learn" / "SKILL.md").read_text()
        for n in ("name: agentj-learn", "agentj learn draft", "agentj learn propose", "agentj plaza publish", "--owner-confirmed",
                  "independent sub-task"):
            self.assertIn(n, text)


if __name__ == "__main__":
    unittest.main()
