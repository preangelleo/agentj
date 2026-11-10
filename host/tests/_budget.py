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
# One named row may pass above its 10 % band only up to an exact byte cap Jarvis approved for one release.
# P0268_BUDGET_WAIVER (贾维斯 11-03 定，0.26.8 具名豁免，瘦身另单): combined measured 4,440,764 B on the 0.26.8 integration
# (0dac0b0b1 base 4,382,675 B, +10.0 %); cap = measured + 8 KiB. Remove this row in the slimming ticket.
# P0268_BUDGET_WAIVER (0.26.8a2 hotfix row, 贾维斯 11-04 定，瘦身另单): same named row, combined measured 4,480,053 B at 78c6fa0fb9 (Olares, test_l3 -k
# test_wheel_contents; 0.26.8a1 head 7d49d4339 4,441,609 B); new exact cap = measured + 8 KiB = 4,488,245 B.
# (0.26.8a2 row superseded on the same label by P0269_BUDGET_WAIVER below once both branches merged.)
# P0269_BUDGET_WAIVER (supersedes the 0.26.8 cap for the same row): 0.26.9 selfheal (#461 watchdog, #463 full PTY terminal,
# #462/#464 login rescue, #468 signed-task assist, #471 Codex steer) per 贾维斯 2026-11-04; slimming is a separate ticket.
# Combined measured 4,523,248 B on the merged selfheal-0269 tree ec144c4771 (wave-1 base d2020a1155 4,499,229 B);
# re-measured after merging hotfix-0268a2: 4,550,642 B at 0da33f33d9 (Olares, l0-budgets);
# cap = measured + 8 KiB (8,192 B) = 4,558,834 B.
# Re-measured after all lanes (0.26.9 selfheal #461 #462 #463 #464 #468 #471 #472 #473 #469 per 贾维斯 2026-11-04;
# slimming separate ticket): 4,572,169 B at d269abbdda (Olares, l0-budgets); cap = measured + 8 KiB = 4,580,361 B.
# Re-measured at release (0.26.9 selfheal #461 #462 #463 #464 #468 #471 #472 #473 #469 + #476 #477 + paid #475/#459/#478/#465/#466/
# #479/#480 + #482 per 贾维斯 2026-11-04; slimming separate ticket): the 0.26.9 freeze wheel (version 0.26.9, on 0164eeb023) is
# 4,587,732 B (combined = wheel bytes, hatchling reproducible); cap = measured + 8 KiB = 4,595,924 B. Final tip after the four
# regression fixes (w3-policy rm card, approval_policy.py): 4,587,777 B → cap = measured + 8 KiB = 4,595,969 B.
# Remove this row in the slimming ticket.
# P0306_BUDGET_WAIVER (贾维斯 2026-11-10 追认，0.30.6 集成单): P170 mail row carries the 0.30.5 A4 signed mail-settings fix,
# Gmail auto-pin and servid_status (a6ff2dfaaa, 92503677f9): measured 43,151 B on release/0306-integrate 448637c8f4 (+17.05 %
# over 36,864); cap = measured + 1 KiB = 44,175 B. Remove in the slimming ticket.
WIDER = {'combined bounded allocations': 4595969,   # P0269_BUDGET_WAIVER
         'P170 e-mail channel allocation (+ P206 A mail expect) is bounded': 44175}   # P0306_BUDGET_WAIVER


def judge(measured: int, ceiling: int, strict: bool, label: str = '') -> str:
    """'pass' | 'soft' | 'fail' for measured against ceiling (strict: measured must be < ceiling, else ≤)."""
    if measured < ceiling or (not strict and measured == ceiling):
        return 'pass'
    cap = next((v for k, v in WIDER.items() if label == k), None)
    return 'soft' if measured <= ceiling * (1 + SOFT) or (cap is not None and measured <= cap) else 'fail'


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
