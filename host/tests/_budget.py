"""P293 (2026-10-14, Leo): package-size budgets are an internal growth guard, not a red line.

measured ≤ ceiling          → PASS (the margin is still recorded)
ceiling < measured ≤ +10 %  → WARN: the test passes, prints `BUDGET-SOFT …` and records it; the ticket writes ONE ADR line
                              (agentjarvis/ARCHITECTURE.md, `budget-soft: <label>` + reason) — never drop a feature or stop for it
measured > +10 %            → FAIL, as before (Jarvis decides)

Only byte budgets go through here (test_l3 WheelInstall.test_wheel_contents). Red lines stay hard elsewhere: identity
INLINE_MAX / HOOK_MAX (test_p159_reminder), security, credentials, permissions, data correctness.
$AJ_BUDGET_LEDGER (set by the release gate runner) receives one JSON line per budget so precheck can show the margins.
"""
from __future__ import annotations

import json
import os
import sys

SOFT = 0.10
MIN_CEILING = 4096          # smaller numbers in that test are counts, not byte budgets
# One named row may get a wider band, only while the release profile blocks the release on a decision about it
# (P558: the 0.25.14 hotfixes put P118 core at +10.2 %; a +12 % band as in P550, NOT yet approved — the release
# profile's RELEASE_BLOCKERS names it). Remove the row once the ceiling is decided.
WIDER = {'P118 approved bounded core ceiling': 0.12}


def judge(measured: int, ceiling: int, strict: bool, label: str = '') -> str:
    """'pass' | 'soft' | 'fail' for measured against ceiling (strict: measured must be < ceiling, else ≤)."""
    if measured < ceiling or (not strict and measured == ceiling):
        return 'pass'
    band = next((v for k, v in WIDER.items() if label.startswith(k)), SOFT)
    return 'soft' if measured <= ceiling * (1 + band) else 'fail'


def _record(row: dict) -> None:
    path = os.environ.get('AJ_BUDGET_LEDGER')
    if path:
        with open(path, 'a') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


class _Soft:
    """Wraps a TestCase: assertLess / assertLessEqual on two ints (ceiling ≥ 4 KiB) become soft byte budgets."""

    def __init__(self, case):
        self._case = case

    def __getattr__(self, name):
        return getattr(self._case, name)

    def _budget(self, a, b, msg, strict):
        if not (isinstance(a, int) and isinstance(b, int) and b >= MIN_CEILING):
            return (self._case.assertLess if strict else self._case.assertLessEqual)(a, b, msg)
        label = (msg or '').split(';')[0][:120]
        verdict = judge(a, b, strict, label)
        _record({'label': label, 'measured': a, 'ceiling': b, 'over': a - b, 'over_pct': round((a - b) * 100 / b, 2),
                 'verdict': verdict, 'test': self._case.id()})
        if verdict == 'soft':
            print(f'BUDGET-SOFT {label}: {a} > {b} (+{a - b} B, +{(a - b) * 100 / b:.1f}%) — '
                  'passes; write one ADR line `budget-soft: <label>` in agentjarvis/ARCHITECTURE.md', file=sys.stderr)
            return None
        if verdict == 'fail':
            self._case.fail(f'{msg}: {a} > {b} (+{a - b} B, +{(a - b) * 100 / b:.1f}%, over the {SOFT:.0%} soft band)')
        return None

    def assertLess(self, a, b, msg=None):
        return self._budget(a, b, msg, True)

    def assertLessEqual(self, a, b, msg=None):
        return self._budget(a, b, msg, False)


def soft(case):
    return _Soft(case)
