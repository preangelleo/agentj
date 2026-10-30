"""P569 (#368 / #369): identity v23 — check for an update before reporting a malfunction; recurring automations default to
the system scheduler running the AI coding tool headless; the agentj-automation skill carries the per-OS recipe and the
`agentj tasks` migration, phrased as the owner's decision (no "bypass" wording, no extra restriction layer)."""
import hashlib
import json
import pathlib
import re
import sys
import unittest

AJ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AJ))

from agentj import main_identity   # noqa: E402

SKILLS = AJ / "agentj" / "skills"


def core(lang):
    return (main_identity.DATA / f"core.{lang}.md").read_text()


class IdentityV23(unittest.TestCase):   # P571: the core is v24 now; the v23 rules below must survive it
    def test_manifest_version_and_hashes(self):
        man = json.loads((main_identity.DATA / "manifest.json").read_text())
        self.assertEqual(man["version"], 24)
        self.assertEqual(main_identity.VERSION, 24)
        self.assertEqual(main_identity.verify_core()["version"], 24)
        for lang in ("zh", "en"):
            digest = hashlib.sha256((main_identity.DATA / f"core.{lang}.md").read_bytes()).hexdigest()
            self.assertEqual(man["hashes"][lang], digest)
            self.assertEqual(main_identity.HASHES[lang], digest)
            self.assertTrue(core(lang).splitlines()[0].endswith("v24"))

    def test_update_check_comes_before_support_and_father(self):
        for lang, persist in (("zh", "仍在"), ("en", "If it persists")):
            text = core(lang)
            for cmd in ("agentj update check", "agentj update apply", "agentj support ask", "--attach-doctor", "host.log"):
                self.assertIn(cmd, text)
            self.assertLess(text.index("agentj update check"), text.index("agentj update apply"))
            self.assertLess(text.index("agentj update apply"), text.index(persist))
            self.assertLess(text.index(persist), text.index("agentj support ask"))
        self.assertIn("落后告知主人", core("zh"))
        self.assertIn("同意即发", core("zh"))
        self.assertIn("if behind, with the owner's OK run `agentj update apply`", core("en"))
        self.assertIn("落后告知主人，同意后 `agentj update apply`", core("zh"))
        self.assertIn("Send only after agreement", core("en"))

    def test_scheduler_default_is_in_the_core(self):
        zh, en = core("zh"), core("en")
        for needle in ("定时任务默认", "系统调度", "无头全权限", "agentj-automation", "手动跑通", "预先认可", "不再请示"):
            self.assertIn(needle, zh)
        for needle in ("recurring jobs default to the OS scheduler", "headless with full access", "agentj-automation",
                       "run once by hand", "pre-approved"):
            self.assertIn(needle, en)

    def test_core_never_adds_a_restriction_or_bypass_wording(self):
        for lang in ("zh", "en"):
            text = core(lang).lower()
            for bad in ("bypass", "绕过", "draft only", "draft-only", "只出草稿", "仅草稿"):
                self.assertNotIn(bad, text)


class FeedbackSkill(unittest.TestCase):
    def test_version_first_then_evidence_then_ask(self):
        full = (SKILLS / "feedback-to-agentj-father" / "SKILL.md").read_text()
        text = full[full.index("## 出故障先查版本 / A malfunction: check the version first"):]
        steps = ("agentj update check", "agentj update apply", "agentj support ask", "agentj doctor --json",
                 "agentj support feedback --kind bug")
        idx = [text.index(s) for s in steps]
        self.assertEqual(idx, sorted(idx))
        for needle in ("agentj --version", "host.log", "--attach-doctor", "这个问题升级后还在", "This still happens after the upgrade",
                       "新版可能已经修好了"):
            self.assertIn(needle, text)


class AutomationSkill(unittest.TestCase):
    text = (SKILLS / "agentj-automation" / "SKILL.md").read_text()

    def test_per_os_schedulers(self):
        for needle in ("Type=oneshot", "OnCalendar=", "Persistent=true", "systemctl --user daemon-reload",
                       "systemctl --user enable --now <name>.timer", "loginctl show-user", "loginctl enable-linger",
                       "~/Library/LaunchAgents/", "StartCalendarInterval", "launchctl bootstrap gui/$(id -u)",
                       "schtasks /Create", "Register-ScheduledTask"):
            self.assertIn(needle, self.text)

    def test_headless_commands_per_tool(self):
        for needle in ("codex exec --sandbox danger-full-access -c approval_policy=never -C",
                       "claude -p --dangerously-skip-permissions", "opencode run --auto", "qwen --approval-mode yolo",
                       "PROMPT.md", "run.sh", "runs/<YYYY-MM-DD>/", "VERDICT: PASS", "VERDICT: FAIL"):
            self.assertIn(needle, self.text)

    def test_designed_together_counts_as_pre_approved(self):
        for needle in ("running it once by hand", "pre-approved", "Do not\nadd approval steps or limits the owner did not ask for",
                       "手动跑通一次", "预先认可", "`agentj tasks` keeps working", "no longer the first choice"):
            self.assertIn(needle, self.text)

    def test_migration_steps_in_order_both_languages(self):
        steps = ("agentj tasks list", "agentj tasks show <id>", "PROMPT.md", "run.sh", "<name>.timer",
                 "agentj tasks disable <id>", "systemctl --user start <name>.service", "systemctl --user list-timers | grep <name>",
                 "systemctl --user disable --now <name>.timer", "agentj tasks enable <id>")
        part = self.text[self.text.index("## Move an `agentj tasks` task to the system scheduler"):]
        part = part[part.index("By hand where `migrate` is missing"):]     # P570b: `agentj tasks migrate` first, the 5 steps as fallback
        idx = [part.index(s) for s in steps]
        self.assertEqual(idx, sorted(idx))
        zh = part[part.index("中文：主人决定改用系统定时器"):]
        for needle in ("PROMPT.md", "run.sh", "<name>.timer", "agentj tasks disable <id>", "agentj tasks enable <id>"):
            self.assertIn(needle, zh)
        self.assertIn("do not delete it", self.text)
        self.assertIn("不删除", self.text)

    def test_owner_decision_wording_without_bypass_or_restriction(self):
        low = self.text[self.text.index("## Make a scheduled automation"):].lower()   # the P569 sections
        for bad in ("bypass", "evade", "circumvent", "绕过", "规避", "draft only", "draft-only", "只出草稿", "仅草稿"):
            self.assertNotIn(bad, low)
        self.assertIn("as Agent J recommends", self.text)
        self.assertIn("the owner's decision", self.text)

    def test_cited_tasks_subcommands_exist(self):
        cli = (AJ / "agentj" / "cli.py").read_text()
        m = re.search(r'tk\.add_argument\("mode", choices=\[([^\]]*)\]\)', cli)
        modes = set(re.findall(r'"(\w+)"', m.group(1)))
        cited = set(re.findall(r"agentj tasks ([a-z]+)", self.text))
        self.assertTrue(cited)
        self.assertLessEqual(cited, modes)

    def test_secrets_only_by_location(self):
        self.assertIn("only where they are stored and the variable names, never a value", self.text)


class OtherSkillsPointToTheDefault(unittest.TestCase):
    def test_capability_skill(self):
        text = (SKILLS / "agentj-capability" / "SKILL.md").read_text()
        self.assertIn("A recurring schedule defaults to the system scheduler (agentj-automation)", text)


if __name__ == "__main__":
    unittest.main()
