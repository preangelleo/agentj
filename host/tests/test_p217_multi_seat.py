"""P217 (0.20.2): multi-instance on one OS user (P180) is public — the bundled skill agentj-multi-seat, `--instance`,
`instances` and `label` in `agentj --help`, and the main identity's multi-user line naming both plans and the skill."""
from __future__ import annotations

import io
import pathlib
import sys
import unittest
from contextlib import redirect_stdout

HOST = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))

from agentj import cli, main_identity, personalize  # noqa: E402

SKILL = HOST / "agentj/skills/agentj-multi-seat/SKILL.md"


class P217(unittest.TestCase):
    def test_bundled_and_linked_like_every_other_skill(self):
        self.assertIn("agentj-multi-seat", personalize.bundled())
        text = SKILL.read_text()
        self.assertTrue(text.startswith("---\nname: agentj-multi-seat\ndescription: "))
        for word in ("内部", "不公开", "保密", "创始人", "founder", "internal"):
            self.assertNotIn(word, text)

    def test_help_shows_instance_instances_and_label(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["--help"])
        h = out.getvalue()
        self.assertIn("--instance N", h)
        self.assertRegex(h, r"\n\s+instances\s+\S")
        self.assertRegex(h, r"\n\s+label\s+\S")

    def test_instance_still_selected_before_parsing(self):
        from agentj import instance
        self.assertEqual(instance.split_argv(["--instance", "2", "doctor"]), (2, ["doctor"]))
        self.assertEqual(instance.split_argv(["status", "--instance=3"]), (3, ["status"]))
        with self.assertRaises(instance.InstanceError):
            instance.split_argv(["--instance", "11", "doctor"])

    def test_identity_line_names_both_plans_and_the_skill(self):
        main_identity.verify_core()          # the hashed core is unchanged; this is a host line
        for lang, plans in (("zh", ("方案 A", "方案 B")), ("en", ("plan A", "plan B"))):
            text = main_identity.prompt({"language": lang})
            line = main_identity.MULTI_USER_LINE[lang]
            self.assertIn(line, text)
            for s in plans + ("agentj-multi-seat", "agentj --instance N", f"/docs/multi-seat/{lang}.md"):
                self.assertIn(s, line)
            self.assertLess(len(line), 450)


if __name__ == "__main__":
    unittest.main()
