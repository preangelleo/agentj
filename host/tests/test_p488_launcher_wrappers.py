"""#488 (baseline red-line hole): launchers that run a command must not hide it from the danger check.
`agentj busy run [opts] [--] CMD` (+ `python3 -m agentj`, `--instance N`), nested through bash -c, `runuser`, `env -S` and
systemd-run --user are judged by their inner command in classify, harness_keeps and main_requirement alike. A wrapper chain
past the cap fails closed. The #469 roster-dispatch shapes keep exactly their old verdict (rules == {cred.service})."""
import _hermetic  # noqa: F401
import json
import unittest

from agentj import confirm_tier, danger, policy_registry as pr

DANGEROUS = ("git push --force origin main", "rm -rf ~/x")
WRAPPERS = ("agentj busy run --label x -- {c}", "agentj busy run -- bash -c \"{c}\"", "agentj busy run --label=x --who a {c}",
            "agentj busy run --who 掌枢 --kind ceo --report .jarvis/r.md --harness claude --model m --workflow w --role r "
            "--parent p -- {c}",
            "python3 -m agentj busy run -- {c}", "agentj --instance 2 busy run -- {c}",
            "agentj busy run -- bash -c \"agentj busy run -- {c}\"", "/home/u/.local/bin/agentj busy run -- zsh -c '{c}'",
            "runuser -u leo -- {c}", "runuser leo -c \"{c}\"", "runuser -l leo -c \"{c}\"", "env -S \"{c}\"", "env --split-string='{c}'")


def verdict(cmd):
    inp = {"command": cmd}
    return danger.classify("Bash", inp).danger, confirm_tier.harness_keeps("Bash", inp), pr.main_requirement("Bash", inp)


class Launchers(unittest.TestCase):
    def test_wrapped_dangerous_command_gets_the_bare_verdict(self):
        for c in DANGEROUS:
            bare = verdict(c)
            self.assertEqual(bare[0], True)
            for w in WRAPPERS:
                with self.subTest(cmd=w.format(c=c)):
                    self.assertEqual(verdict(w.format(c=c)), bare)

    def test_secret_command_behind_busy_run_keeps_face_id(self):
        for w in WRAPPERS[:8]:
            cmd = w.format(c="agentj secret get k")
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd)[1:], ("secret_out", "uv"))

    def test_systemd_run_user_around_busy_run_sees_the_inner_command(self):
        for c in DANGEROUS:
            for pre in ("systemd-run --user --collect --unit=u2", "systemd-run --user --scope -p Nice=5 --setenv A=b --unit u1"):
                cmd = f"{pre} agentj busy run -- {c}"
                v = danger.classify("Bash", {"command": cmd})
                with self.subTest(cmd=cmd):
                    self.assertTrue(set(v.rules) > {"cred.service"}, v.rules)     # never the #469 dispatch shape
                    self.assertIsNotNone(confirm_tier.harness_keeps("Bash", {"command": cmd}))
                    self.assertEqual(pr.main_requirement("Bash", {"command": cmd}), "uv")

    def test_harmless_inner_command_stays_without_a_card(self):
        for w in WRAPPERS[:8] + ("env -S \"{c}\"",):
            cmd = w.format(c="ls -la")
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), (False, None, "allow"))
        self.assertEqual(verdict("agentj busy run --help"), (False, None, "allow"))
        self.assertEqual(verdict("agentj busy start --pid 123 --label x"), (False, None, "allow"))

    def test_nesting_and_wrapper_chains_past_the_cap_fail_closed(self):
        deep = "rm -rf ~/x"
        for _ in range(9):
            deep = "agentj busy run -- bash -c " + json.dumps(deep)
        for cmd in (deep, "nohup " * 20 + "ls", "env -S \"rm -rf 'unbalanced\""):
            with self.subTest(cmd=cmd[:60]):
                d, keep, req = verdict(cmd)
                self.assertTrue(d)
                self.assertIsNotNone(keep)
                self.assertEqual(req, "uv")

    def test_469_roster_dispatch_shapes_unchanged(self):
        direct = ("systemd-run --user --collect --unit=agentj-p469b-ceo -p WorkingDirectory=/tmp/ws agentj busy run --who 掌枢 "
                  "--kind ceo --label x -- claude -p --permission-mode bypassPermissions '读 .jarvis/PROMPT-x.md 并按其执行'")
        today = ("systemd-run --user --collect --unit=agentj-p469-ceo -p WorkingDirectory=/tmp/ws agentj busy run --who 掌枢 "
                 "--kind ceo --label x -- bash -c \"claude -p --permission-mode bypassPermissions '读 .jarvis/PROMPT-x.md "
                 "并按其执行' > .jarvis/run-x.log 2>&1\"")
        for cmd in (direct, today):
            v = danger.classify("Bash", {"command": cmd})
            self.assertEqual(set(v.rules), {"cred.service"})      # serve's proven-dispatch check still decides
            self.assertIsNone(confirm_tier.harness_keeps("Bash", {"command": cmd}))
            # 1125/1140 ordinary-owner dispatch no longer opens an approval;
            # the dangerous inner-command controls above still require UV.
            self.assertEqual(pr.main_requirement("Bash", {"command": cmd}), "allow")


if __name__ == "__main__":
    unittest.main()
