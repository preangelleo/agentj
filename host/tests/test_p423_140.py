"""P423 #140: `agentj service import-key NAME` with NAME absent from this terminal printed Jev's OpenRouter template
(「Jev（手机审批第二层）不生效 … --purpose 'OpenRouter key（Jev 审批第二层）' … --verify-url https://openrouter.ai/…」) whatever NAME
was — e.g. AGENTSRELAY_CLAUDE_KEY. Now the hint follows the name: Jev only for OPENROUTER_API_KEY, the vendor for a few known
names, the neutral phone-card route (+ `agentj provider list` to check OpenCode's referenced name) for any other.
No real key, no network, no systemd: the env file lives under a temporary XDG_CONFIG_HOME."""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import os
import shlex
import sys
import tempfile
import unittest
from unittest import mock

from agentj import cli, service

NAMES = ("AGENTSRELAY_CLAUDE_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


class AbsentKeyHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        for k in NAMES:
            os.environ.pop(k, None)
        plat = mock.patch.object(service, "platform", return_value="linux")
        plat.start()
        self.addCleanup(plat.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["agentj", *argv]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            try:
                cli.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, str(code) + out.getvalue()

    def test_any_name_gets_no_jev_or_openrouter(self):
        code, out = self.run_cli("service", "import-key", "AGENTSRELAY_CLAUDE_KEY")
        self.assertNotIn(code, (0, None))
        self.assertIn("这个终端里没有 AGENTSRELAY_CLAUDE_KEY", out)
        for bad in ("Jev", "OpenRouter", "openrouter", "OPENROUTER_API_KEY"):
            self.assertNotIn(bad, out)
        self.assertIn("agentj secret request --name AGENTSRELAY_CLAUDE_KEY --purpose", out)
        self.assertIn("#AGENTSRELAY_CLAUDE_KEY", out)
        self.assertIn("agentj provider list", out, "how to check the name OpenCode's config references")

    def test_openrouter_keeps_jev(self):
        code, out = self.run_cli("service", "import-key", "OPENROUTER_API_KEY")
        self.assertNotIn(code, (0, None))
        self.assertIn("Jev", out)
        self.assertIn("https://openrouter.ai/api/v1/key", out)
        code, out = self.run_cli("service", "import-key")     # the default name is still Jev's
        self.assertIn("Jev", out)

    def test_known_vendors(self):
        n = service.name()
        o = service.absent_key_note(n, "OPENAI_API_KEY")
        self.assertIn("OpenAI", o)
        self.assertIn("--verify-url https://api.openai.com/v1/models", o)
        c = service.absent_key_note(n, "CLAUDE_CODE_OAUTH_TOKEN")
        self.assertIn("claude setup-token", c)
        a = service.absent_key_note(n, "ANTHROPIC_API_KEY")
        self.assertIn("Anthropic", a)
        for t in (o, c, a):
            self.assertNotIn("Jev", t)
            self.assertNotIn("openrouter", t.lower())

    def test_the_card_command_parses(self):
        """The suggested command is real: `agentj secret request` with these flags parses (no phone, nothing sent)."""
        for key in ("AGENTSRELAY_CLAUDE_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            note = service.absent_key_note(service.name(), key)
            tail = note.split("`agentj secret request", 1)[1].split("`", 1)[0]
            argv = shlex.split("secret request" + tail)
            with mock.patch("agentj.elevate.cmd_secret", return_value=0) as m, \
                    mock.patch.object(sys, "argv", ["agentj", *argv]):
                with contextlib.suppress(SystemExit):
                    cli.main()
            a = m.call_args[0][0]
            self.assertEqual((a.action, a.name), ("request", key))
            self.assertTrue(a.purpose and a.dest.endswith("#" + key))


if __name__ == "__main__":
    unittest.main()
