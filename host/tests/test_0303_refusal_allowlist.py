"""#0303 anti-regression gate (chairman 2026-11-09 15:3x: 「那些卡顿实在是非常的愚蠢。非常的自说自话」).

Every source / identity / ingress-type refusal code in host code must be on the reviewed allowlist
(tests/refusal_allowlist.json) with a category and a reason. A NEW code that is not listed fails this test: adding a gate
that can refuse the owner needs an explicit, reviewed entry (and Jarvis' OK), never a silent `return {'why': ...}`.
An allowlist entry whose code no longer exists also fails, so the list stays the truth.

Categories (the only reasons a source-type refusal may exist after 0.30.3):
  uv              — fingerprint-list (UV) action needs the owner's fresh fingerprint card
  anti_injection  — friend / visitor / group / forwarded / external-channel / webhook text posing as the owner
  frozen          — signing / pairing / passkey / card envelope / signed owner grant (frozen layer, not changed here)
  state           — not a source judgement: an owner/peer/device state or availability fact (offline, revoked, paused …)
  not_a_refusal   — the literal is a label, status or input-validation code matched by the scanner's name filter
"""
import _hermetic  # noqa: F401
import json
import pathlib
import re
import unittest

HOST = pathlib.Path(__file__).resolve().parents[1]
PKG = HOST / 'agentj'
ALLOW = pathlib.Path(__file__).with_name('refusal_allowlist.json')
CATEGORIES = {'uv', 'anti_injection', 'frozen', 'state', 'not_a_refusal'}

# A code is "source-type" when its name says so.
SOURCE_WORDS = re.compile(r'(unverified|untrusted|source|origin|ingress|caller|owner_|group_|peer_|not_owner|task_plan'
                          r'|level_harness|main_thread|provenance|external)')
PATTERNS = (
    re.compile(r'''["'](?:why|reason|error|result)["']\s*:\s*["']([a-z][a-z0-9_]{2,60})["']'''),
    re.compile(r'''\b(?:why|reason)\s*=\s*["']([a-z][a-z0-9_]{2,60})["']'''),
    re.compile(r'''\b(?:refused|refuse|_refuse|Expired|PlanError|CapError|Refused|failure|gate_refused)\(\s*["']([a-z][a-z0-9_]{2,60})["']'''),
)


def scan():
    found = {}
    for path in sorted(PKG.rglob('*.py')):
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            for pattern in PATTERNS:
                for match in pattern.finditer(line):
                    code = match.group(1)
                    if SOURCE_WORDS.search(code):
                        found.setdefault(code, []).append(f'{path.relative_to(HOST)}:{number}')
    return found


class RefusalAllowlist(unittest.TestCase):
    def setUp(self):
        self.allow = json.loads(ALLOW.read_text(encoding='utf-8'))['codes']

    def test_every_source_type_refusal_is_reviewed(self):
        new = {code: where[:3] for code, where in scan().items() if code not in self.allow}
        self.assertEqual(new, {}, 'New source/identity refusal code(s) not on tests/refusal_allowlist.json. Outside the UV '
                         'list Agent J must not block the owner; add an entry only with a category, a reason and Jarvis\' OK.')

    def test_allowlist_has_no_stale_or_unexplained_entries(self):
        present = scan()
        stale = sorted(code for code in self.allow if code not in present)
        self.assertEqual(stale, [], 'allowlist codes no longer in host code: remove them')
        for code, row in self.allow.items():
            self.assertIn(row.get('category'), CATEGORIES, code)
            self.assertTrue(len(str(row.get('why', ''))) >= 12, code)

    def test_removed_owner_blocking_codes_do_not_come_back(self):
        # Gates the chairman ordered removed (#0302/#0303) must not reappear under their old names.
        for code in json.loads(ALLOW.read_text(encoding='utf-8')).get('removed', []):
            self.assertNotIn(code, scan(), code)


if __name__ == '__main__':
    unittest.main()
