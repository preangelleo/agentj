"""P444 installation pairing contract, shared by Codex/Claude/OpenCode (no model call or real credentials)."""
import _hermetic  # noqa: F401
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallationPairing(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'tests/p444_pair_route.test.mjs').is_file(), 'private browser fixture not exported')
    def test_shipped_local_page_routes_before_and_after_scan(self):
        r = subprocess.run(['node', '--test', str(ROOT / 'tests/p444_pair_route.test.mjs')],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless((ROOT.parent / 'documentation/product/install.md').is_file(), 'private installation source not exported')
    def test_installers_use_account_url_and_recover_existing_pending_phone(self):
        guide = (ROOT.parent / 'documentation/product/install.md').read_text()
        skill = (ROOT / 'host/agentj/skills/agentj-pair/SKILL.md').read_text()
        for harness in ('Claude Code', 'Codex', 'OpenCode'):
            with self.subTest(harness=harness):
                self.assertIn(harness, guide)
                self.assertIn('agentj pair --check --json', guide)
                self.assertIn('account_url', guide)
                self.assertIn('Do not open the local admin page for installation pairing', guide)
                self.assertIn('approve that waiting phone', guide)
        self.assertIn('P190 reverse pairing', guide)
        self.assertIn('之后的遥控器打开', guide)
        self.assertNotIn('Dashboard 不能批准', guide)
        self.assertNotIn('click Add a remote on this Agent\'s computer to launch `agentj://pair`', guide)
        from agentj import main_identity
        for lang in ('en', 'zh'):
            self.assertIn('account_url', main_identity.PAIRING_LINE[lang])
            self.assertIn('agentj pair --check --json', main_identity.PAIRING_LINE[lang])
        self.assertIn('account_url', skill)
        self.assertIn('不要重新配对', skill)
        for lang in ('en', 'zh'):
            d = json.loads((ROOT / f'host/agentj/admin/i18n/admin.{lang}.json').read_text())
            self.assertTrue(d['pair.pending_account'])
            self.assertTrue(d['pair.pre_account_open'])

    @unittest.skipUnless((ROOT / 'site/public/install.md').is_file(), 'site not exported')
    def test_install_source_and_served_copy_agree(self):
        self.assertEqual((ROOT.parent / 'documentation/product/install.md').read_bytes(),
                         (ROOT / 'site/public/install.md').read_bytes())
