"""Synthetic public-format vectors; no credentials or machine configuration."""
from pathlib import Path
import ast
import re
import unittest
import subprocess
import sys
from agentj import privacy, peer_guard, term_mask, keyhint, taskspec

ROOT = Path(__file__).resolve().parent / 'fixtures' / 'p580'
TOKENS = ['ajh_' + 'x' * 43, 'AJV-' + '-'.join(['A2BC'] * 2),
          'AJUP-' + '-'.join(['A2BCD'] * 4), 'AJ-' + '-'.join(['A2BC'] * 4)]


class Prefixes(unittest.TestCase):
    def test_owner_and_stream_every_split(self):
        for token in TOKENS:
            self.assertNotIn(token, privacy.redact('code: ' + token))
            self.assertNotIn(token, privacy.owner_text('code: ' + token, mask_access_codes=True))
            for split in range(len(token) + 1):
                masker = term_mask.Masker()
                result = masker.feed(token[:split]) + masker.feed(token[split:] + '\n') + masker.close()
                self.assertNotIn(token, result)

    def test_peer_guard_reuses_privacy(self):
        for i, token in enumerate(TOKENS[:-1]):
            self.assertEqual(peer_guard.credential_shape(token), 'agentjarvis_id' if i == 0 else 'agentj_access_code')
        # A public friend ID is redacted as personal data but grants no authority.
        self.assertIsNone(peer_guard.credential_shape(TOKENS[-1]))

    def test_owner_transfer_preserves_codes_but_never_keys(self):
        for code in TOKENS[1:3]:
            for text in (code, 'https://example.org/?code=' + code):
                self.assertEqual(privacy.owner_text(text), text)
                self.assertEqual(privacy.credentials(text), [])
                self.assertTrue(taskspec.secret_like(text))
                self.assertNotIn(code, privacy.redact(text))
        key = privacy.FAKE_SECRETS['openrouter']
        self.assertNotIn(key, privacy.owner_text(TOKENS[2] + ' ' + key))
        self.assertEqual([k for k, *_ in privacy.credentials(TOKENS[2] + ' ' + key)], ['openrouter_key'])

    def test_public_id_is_private_but_not_a_credential(self):
        token = TOKENS[-1]
        self.assertNotIn(token, privacy.redact(token))
        self.assertFalse(taskspec.secret_like(token))
        self.assertFalse(taskspec.secret_like(token, owner=True))
        self.assertIsNone(taskspec.secret_where(token))
        self.assertEqual(privacy.credentials(token), [])

    def test_support_outbound_patterns(self):
        for file, name in [('snapshot.py', 'SECRET_RE'), ('support_desk.py', '_SECRET_PATTERNS')]:
            tree = ast.parse((ROOT / 'agentjarvis/support' / file).read_text())
            node = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets))
            patterns = node.generators[0].iter if isinstance(node, ast.ListComp) else node
            rules = [re.compile(p) for p in ast.literal_eval(patterns)]
            for token in TOKENS:
                self.assertTrue(any(rule.search(token) for rule in rules), file)

    def test_installer_wrong_code_detection(self):
        for file in ['install.sh.in', 'install.ps1.in', 'setup-agents.sh']:
            source = (ROOT / 'agentjarvis/setup' / file).read_text()
            self.assertIn('aj[th]_[A-Za-z0-9_-]{43}', source)
            self.assertIn('ajt_ / ajh_', source)

    def test_real_shell_extraction_refuses_both_setup_prefixes(self):
        source = (ROOT / 'agentjarvis/setup/install.sh.in').read_text()
        function = re.search(r'extract_code\(\) \{.*?^\}', source, re.S | re.M).group()
        setup = (ROOT / 'agentjarvis/setup/setup-agents.sh').read_text()
        code = setup.split("<<'EXTRACT_CODE'\n", 1)[1].split('\nEXTRACT_CODE', 1)[0]
        for prefix in ['ajt_', 'ajh_']:
            token = prefix + 'x' * 43
            result = subprocess.check_output(['sh', '-c', function + '\nextract_code'], input=token.encode())
            self.assertEqual(result.strip(), b'ajt')
            result = subprocess.check_output([sys.executable, '-c', code], input=token.encode())
            self.assertEqual(result.strip(), b'ajt')

    def test_keyhint_is_not_a_redactor(self):
        self.assertIsNone(keyhint.provider_of(value=TOKENS[0]))
        self.assertFalse(keyhint.looks_short(len(TOKENS[0]), value=TOKENS[0]))


if __name__ == '__main__':
    unittest.main()
