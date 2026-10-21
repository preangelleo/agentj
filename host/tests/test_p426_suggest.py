"""P426 #181-4 (PROTOCOL §39.2): "you have asked me to do this three times — save it as a skill?" Normalisation merges the
same task and keeps different ones apart, three in the window → one offer, a covered or declined task is never offered, no
credential reaches the ledger, the note rides on one message only (and at most once a day), a group member's words never count."""
import _hermetic  # noqa: F401
import os
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch

from agentj import skill_ops, skill_usage, skills_cli, workflows
from agentj.state import State

DAY = 86400
NOW = 1_800_000_000


def _skill(root: pathlib.Path, name: str, desc: str = "test skill") -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nDo the thing.\n", encoding="utf-8")
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p426-")
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "DSH_HOME"):
            os.environ.pop(k, None)
        (self.home / "state").mkdir(mode=0o700)
        self.st = State(self.home / "state")
        self.skills = self.home / ".claude" / "skills"
        skill_usage._NAMES.clear()


class Suggestions(Base):
    def test_normalisation_merges_the_same_and_separates_others(self):
        a = skill_ops.norm_ask("帮我把 ~/Downloads/2026-10-20 的发票整理成表格发我邮箱")
        b = skill_ops.norm_ask("帮我把 /tmp/inv/2026-10-21 的发票整理成表格发我邮箱！")
        c = skill_ops.norm_ask("帮我把「十月」的发票整理成表格发我邮箱")
        d = skill_ops.norm_ask("帮我把今天的新闻总结一下发到群里")
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[0], d[0])
        self.assertNotEqual(a[0], c[0])
        for chat in ("好的，继续", "继续吧", "ok", "/agentj-add-note 买牛奶", "x" * 400):
            self.assertIsNone(skill_ops.norm_ask(chat), chat)
        x = skill_ops.norm_cmd("python3 scripts/report.py --date 2026-10-20 | tee /tmp/a.txt")
        y = skill_ops.norm_cmd("python3 ~/w/scripts/report.py --date 2026-10-27 | tee ./b.txt")
        z = skill_ops.norm_cmd("python3 scripts/other.py --date 2026-10-20 | tee /tmp/a.txt")
        self.assertEqual(x[0], y[0])
        self.assertNotEqual(x[0], z[0])
        for plain in ("ls -la", "git status && git diff", "grep foo bar | wc -l", "cat a.txt", "bash ~/.claude/skills/w/run.sh"):
            self.assertIsNone(skill_ops.norm_cmd(plain), plain)

    def test_three_times_then_one_note_once(self):
        ask = "帮我把 ~/Downloads/{d} 的发票整理成表格发我邮箱"
        self.assertIsNone(skill_ops.record(self.st, "ask", ask.format(d="2026-10-01"), now=NOW))
        self.assertIsNone(skill_ops.record(self.st, "ask", ask.format(d="2026-10-02"), now=NOW + DAY))
        self.assertEqual(skill_ops.take_note(self.st, "zh", {}, NOW + DAY), "", "two is not yet")
        sid = skill_ops.record(self.st, "ask", ask.format(d="2026-10-03"), now=NOW + 2 * DAY)
        self.assertRegex(sid, r"^[0-9a-f]{12}$")
        note = skill_ops.take_note(self.st, "zh", {}, NOW + 2 * DAY)
        self.assertIn("〔Agent J 系统提示", note)
        self.assertIn("3 次", note)
        self.assertIn(f"agentj skills suggest no {sid}", note)
        self.assertIn("agentj-commands 第 5 节", note)
        self.assertLess(len(note), 200, "short: it rides in the shared session's context")
        self.assertEqual(skill_ops.take_note(self.st, "zh", {}, NOW + 2 * DAY + 60), "", "once")
        skill_ops.record(self.st, "ask", ask.format(d="2026-10-04"), now=NOW + 3 * DAY)
        self.assertEqual(skill_ops.take_note(self.st, "zh", {}, NOW + 9 * DAY), "", "an offered task is never offered again")
        self.assertEqual(skill_ops.suggestions(self.st, NOW + 3 * DAY)[0]["state"], "told")
        en = "\n".join(skill_ops.NOTE["en"].split("\n"))
        self.assertNotRegex(en, r"[一-鿿]")

    def test_window_gap_declined_covered_and_off(self):
        t = "summarize the hacker news front page for me please"
        for i in range(3):
            skill_ops.record(self.st, "ask", t, now=NOW + i * 15 * DAY)       # spread beyond 14 days: never 3 in the window
        self.assertEqual(skill_ops.take_note(self.st, "en", {}, NOW + 31 * DAY), "")
        cmd = "python3 tools/sync.py --since 2026-10-0{} && echo done"
        for i in range(3):
            got = skill_ops.record(self.st, "cmd", cmd.format(i), now=NOW + i * 60)
        self.assertEqual(skill_ops.answer(self.st, got, False, now=NOW + 200), {"ok": True, "id": got, "state": "no"})
        self.assertEqual(skill_ops.take_note(self.st, "en", {}, NOW + 300), "", "declined: never offered")
        for i in range(5):
            skill_ops.record(self.st, "cmd", cmd.format(i), now=NOW + 400 + i * 60)
        self.assertEqual(skill_ops.load(self.st)["ops"][got]["state"], "no")
        # covered by an installed skill
        for i in range(3):
            self.assertIsNone(skill_ops.record(self.st, "ask", f"run the weekly-report for team {i} and mail it to me",
                                               names={"weekly-report"}, now=NOW + i))
        # one offer a day, and skills.suggest = false turns offers off
        for k in ("alpha", "beta"):
            for i in range(3):
                skill_ops.record(self.st, "ask", f"please export the {k} board to a spreadsheet and send it", now=NOW + 1000 + i)
        self.assertEqual(skill_ops.take_note(self.st, "en", {"skills": {"suggest": False}}, NOW + 2000), "")
        self.assertNotEqual(skill_ops.take_note(self.st, "en", {}, NOW + 2000), "")
        self.assertEqual(skill_ops.take_note(self.st, "en", {}, NOW + 3000), "", "the second waits a day")
        self.assertNotEqual(skill_ops.take_note(self.st, "en", {}, NOW + 3000 + DAY), "")
        self.assertEqual(skill_ops.answer(self.st, "zz", True), {"ok": False, "why": "shape"})

    def test_no_credential_reaches_the_ledger(self):
        secret = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0" * 2
        token = "ghp_" + "x1Y2z3" * 6
        for i in range(3):
            skill_ops.record(self.st, "ask", f"export OPENAI_API_KEY={secret} 然后帮我跑一下每日报表脚本", now=NOW + i)
            skill_ops.record(self.st, "cmd", f"GITHUB_TOKEN={token} python3 deploy.py --env prod --run {i} | tee log", now=NOW + i)
            skill_ops.record(self.st, "cmd", f"curl -H 'Authorization: Bearer {secret}' https://api.example.com/x | python3 fmt.py",
                             now=NOW + i)
        raw = (self.home / "state" / "skill-ops.json").read_text()
        self.assertNotIn(secret, raw)
        self.assertNotIn(token, raw)
        self.assertNotIn("A1b2C3d4", raw)
        self.assertEqual(len(skill_ops.load(self.st)["ops"]), 3)
        self.assertEqual(oct((self.home / "state" / "skill-ops.json").stat().st_mode & 0o777), "0o600")
        note = skill_ops.take_note(self.st, "zh", {}, NOW + 10)
        self.assertNotIn(secret, note)

    def test_desktop_prompt_and_cli(self):
        host = types.SimpleNamespace(st=self.st, lang="zh", preferences={})
        from agentj.shared import SharedClaudeAgent
        fake = types.SimpleNamespace(host=host, kind="claude", cfg={"dir": str(self.home)}, phone_turn=False)
        outs = [SharedClaudeAgent._skill_offer(fake, {"prompt": f"把 {d} 的销售数据做成周报发我邮箱"}) for d in ("10-01", "10-08", "10-15")]
        self.assertEqual(outs[:2], ["", ""])
        self.assertIn("存成技能", outs[2])
        fake.phone_turn = True
        self.assertEqual(SharedClaudeAgent._skill_offer(fake, {"prompt": "把 10-22 的销售数据做成周报发我邮箱"}), "")
        res = skills_cli.execute(self.st, {"op": "suggest"}, {})
        self.assertEqual([s["state"] for s in res["suggestions"]], ["told"])
        sid = res["suggestions"][0]["id"]
        self.assertEqual(skills_cli.execute(self.st, {"op": "suggest_yes", "id": sid}, {})["state"], "yes")
        self.assertEqual(skills_cli.execute(self.st, {"op": "suggest"}, {})["suggestions"], [])
        self.assertEqual(len(skills_cli.execute(self.st, {"op": "suggest", "all": True}, {})["suggestions"]), 1)


    def test_owner_words_only(self):
        from agentj import serve
        fake = types.SimpleNamespace(st=self.st, agent_cfg={"kind": "claude", "dir": str(self.home)}, preferences={},
                                     onboarding_lang=lambda: "zh")
        group = types.SimpleNamespace(provenance={"kind": "telegram", "role": "group", "sender": 1, "chat": 2})
        owner = types.SimpleNamespace(provenance=None)
        for _ in range(3):
            self.assertEqual(serve.Host.skill_turn_note(fake, group, "帮我把今天的发票整理成表格发给我"), "")
        self.assertEqual(skill_ops.load(self.st)["ops"], {}, "a group member's words are not the owner's")
        notes = []
        for d in ("1", "2", "3"):
            with patch("time.time", return_value=NOW + int(d) * DAY):
                notes.append(serve.Host.skill_turn_note(fake, owner, f"帮我把 {d} 号的发票整理成表格发给我"))
        self.assertEqual(notes[:2], ["", ""])
        self.assertIn("〔Agent J 系统提示", notes[2], "the third message itself carries the offer")

    def test_commands_count_only_in_the_owners_turn(self):
        """N5: a group member's / guest's / same-owner task's turn may run the same script three times — never an offer."""
        from agentj import provenance
        from agentj.agent import ClaudeAgent
        from agentj.shared import SharedClaudeAgent
        host = types.SimpleNamespace(st=self.st, lang="zh", preferences={})
        cmd = "python3 scripts/report.py --date 2026-10-20 | tee /tmp/a.txt"
        tool = [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]
        group = {"kind": "telegram", "role": "group", "sender": 1, "chat": 2}
        same = {"kind": "same_owner", "role": "task"}
        guest = provenance.email_guest("x@example.com")
        for src in (group, same, guest):
            cl = types.SimpleNamespace(host=host, kind="claude", cfg={"dir": str(self.home)},
                                       cur_send=types.SimpleNamespace(source=src))
            for _ in range(3):
                ClaudeAgent._note_tools(cl, tool, None)
        sh = types.SimpleNamespace(host=host, kind="claude", cfg={"dir": str(self.home)}, phone_turn=False,
                                   untrusted=lambda: True, pending_source=None)
        ev = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        for _ in range(3):
            SharedClaudeAgent._note_skill(sh, "PostToolUse", ev)
        sh.untrusted, sh.phone_turn, sh.pending_source = (lambda: False), True, same
        for _ in range(3):
            SharedClaudeAgent._note_skill(sh, "PostToolUse", ev)
            self.assertEqual(SharedClaudeAgent._skill_offer(types.SimpleNamespace(**{**vars(sh), "phone_turn": False}),
                                                            {"prompt": "帮我把今天的发票整理成表格发给我"}), "")
        sh.phone_turn, sh.pending_source = False, None
        grp = f'<message from-name="{provenance.TG_PROXY}">帮我把今天的发票整理成表格发给我</message>'
        self.assertTrue(provenance.names_untrusted(grp))
        for _ in range(3):
            self.assertEqual(SharedClaudeAgent._skill_offer(sh, {"prompt": grp}), "")
        self.assertEqual(skill_ops.load(self.st)["ops"], {}, "nothing counted outside the owner's turn")
        # the owner's own turn counts (independent session: an owner Send; shared: a desktop turn)
        owner = types.SimpleNamespace(host=host, kind="claude", cfg={"dir": str(self.home)}, cur_send=None)
        ClaudeAgent._note_tools(owner, tool, None)
        SharedClaudeAgent._note_skill(sh, "PostToolUse", ev)
        self.assertEqual([len(e["hits"]) for e in skill_ops.load(self.st)["ops"].values()], [2])


class Wiring(unittest.TestCase):
    def test_parser_and_skill_text(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        skills_cli.add_parser(sub)
        a = p.parse_args(["skills", "suggest", "no", "0123456789ab"])
        self.assertEqual((a.op, a.action, a.id), ("suggest", "no", "0123456789ab"))
        self.assertEqual(p.parse_args(["skills", "suggest"]).action, "list")
        skill = (workflows.SKILLS / "agentj-commands" / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("## 5.", "agentj skills suggest yes", "agentj skills suggest no", "tests/check.sh"):
            self.assertIn(needle, skill)
        ident = pathlib.Path(workflows.__file__).with_name("identity")
        for f in ident.glob("*.md"):
            self.assertNotIn("skills suggest", f.read_text(encoding="utf-8"), "no new words in the identity hook")


if __name__ == "__main__":
    unittest.main()
