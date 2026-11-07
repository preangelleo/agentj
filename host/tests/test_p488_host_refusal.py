"""#488: the original launcher inputs must reach a real Host refusal, even in full access."""
import _hermetic  # noqa: F401
import json
from unittest import mock

from test_p551_confirm_levels import Base


class LauncherHostRefusal(Base):
    def test_unresolved_execution_cannot_be_approved_or_executed(self):
        deep = "rm -rf ~/x"
        for _ in range(9):
            deep = "agentj busy run -- bash -c " + json.dumps(deep)
        commands = (deep, "nohup " * 20 + "ls", "env -S \"rm -rf 'unbalanced\"")
        for full in (False, True):
            if full:
                self.harness()
            for command in commands:
                with self.subTest(full=full, command=command[:60]):
                    before = len(self.cards())
                    receipt = {}
                    with mock.patch("subprocess.Popen", side_effect=AssertionError("must not execute")) as execute:
                        result = self.ask(command, grant_info=True, _receipt=receipt)
                    self.assertEqual(result["behavior"], "deny")
                    self.assertEqual(result["reason"], "unresolved_execution")
                    self.assertEqual(receipt["approval_card_shown"], False)
                    self.assertEqual(len(self.cards()), before)
                    execute.assert_not_called()

    def test_known_inner_command_retains_actual_uv_and_plain_command_is_automatic(self):
        self.harness()
        self.assertEqual(self.ask('env -S "ls -la"')["behavior"], "allow")
        for command in ("git push --force origin main", "env --split-string='git push --force origin main'"):
            with self.subTest(command=command):
                before = len(self.cards())
                with mock.patch("subprocess.Popen", side_effect=AssertionError("classification only")) as execute:
                    result = self.ask(command, batch=False)
                self.assertEqual(result["behavior"], "deny")  # no signed UV answer
                self.assertEqual(len(self.cards()), before + 1)
                self.assertTrue(self.cards()[-1]["passkey_required"])
                execute.assert_not_called()
