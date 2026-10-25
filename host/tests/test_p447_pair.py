"""P447 default output, installation branches, live success hint; P221 guards stay intact."""
import _hermetic  # noqa: F401
import argparse
import asyncio
import contextlib
import io
from pathlib import Path
from unittest.mock import patch
from test_p127_pair import Base, FakeServe
from test_p72_onboarding import Serve, PHONE, PC
from agentj import cli, gate, main_identity, onboarding

class DefaultOutput(Base):
    def test_default_and_compat_link_show_same_qr_and_link(self):
        self.bind()
        gate.set_passphrase(self.st, "test-passphrase-p447")
        for link, no_qr in [(False, False), (True, False), (False, True)]:
            fs = FakeServe(self.st)
            out = io.StringIO()
            try:
                # This in-process output test must not invoke the real CLI's
                # os._exit(0) fallback while a synthetic stdin thread is winding down.
                # Run the actual pairing coroutine; P153 separately owns process exit.
                with patch.object(cli, "_run_prompting", side_effect=asyncio.run), patch.object(cli, "State", return_value=self.st), patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out), patch.object(cli, "_print_qr") as qr:
                    cli.cmd_pair(argparse.Namespace(no_qr=no_qr, link=link, check=False))
                self.assertEqual(qr.call_count, 0 if no_qr else 1)
                self.assertEqual(out.getvalue().count("https://m.agentj.app/#p=TEST"), 1)
                for text in ["方法二", "Way 2", "电脑浏览器地址栏", "works once"]:
                    self.assertIn(text, out.getvalue())
                if not no_qr:
                    self.assertIn("方法一", out.getvalue())
                    self.assertIn("Way 1", out.getvalue())
            finally:
                fs.close()

class FirstOnly(Serve):
    def test_success_hint_matches_device_then_clears_when_second_pairs(self):
        self.host.agent = None
        async def go():
            await self._pair(7, PHONE, p33=True)
            hints = lambda: [m for _, m in self.sent if m.get("t") == "pair_guide"]
            self.assertEqual(hints()[-1], {"t": "pair_guide", "kind": "phone"})
            await self._pair(8, PC, p33=True)
            self.assertIsNone(hints()[-1]["kind"])
            self.assertEqual(onboarding.read(self.st)["welcomed"]["kind"], "phone")
        asyncio.run(go())

    def test_computer_first_success_without_agent(self):
        self.host.agent = None
        async def go():
            await self._pair(7, PC, p33=True)
            self.assertIn({"t": "pair_guide", "kind": "computer"}, [m for _, m in self.sent])
        asyncio.run(go())

class Copy(Base):
    def test_both_identity_languages_have_three_routes_and_password_boundary(self):
        for lang in ("zh", "en"):
            s = main_identity.PAIRING_LINE[lang]
            for text in (["有账号", "只有席位", "账号是别人开的", "AI不得代设、读取或代输", "至少8位"] if lang == "zh" else ["Own account", "Seat only", "Someone else opened", "AI must never set, read or type", "8+ characters"]):
                self.assertIn(text, s)
    def test_install_and_skill_and_installer_explain_three_people(self):
        product = Path(__file__).resolve().parents[2]
        private = product.parent / "documentation/product/install.md"
        paths = [private if private.is_file() else product / "install.md",
                 product / "host/agentj/skills/agentj-pair/SKILL.md",
                 product / "support/installation/installer.py" if private.is_file() else product / "host/tests/fixtures/p385/installer.py"]
        for path in paths:
            s = path.read_text()
            for token in ["有账号", "只有席位", "账号是别人开的", "Own account" if path.suffix == ".md" else "own account", "Seat only", "Someone else opened", "agentj pair"]:
                self.assertIn(token, s, str(path))
