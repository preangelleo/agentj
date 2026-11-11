"""0.30.7 ADR-0307-offlist test support: put a harness tool on the card list for ONE test.

Since 0.30.7 an off-list request never opens a card. Tests whose subject is the card machinery itself (display,
redaction, signing, receipts, parking, resume, races) used an off-list native `Bash` / `Read` request only as a vehicle
for a card. They call `listed(self, 'Bash', ...)` to keep testing that machinery on a (test-only) listed tool; tests
whose subject was "does this off-list request card" must instead assert the ADR-0307-offlist outcome (no card).
"""
from unittest import mock

from agentj import approval_offlist


def listed(testcase, *tools):
    tools = frozenset(tools) or frozenset({'Bash', 'Read', 'Write', 'Edit'})
    for name in ('NATIVE_CARDS', 'CARD_LIST'):
        patcher = mock.patch.object(approval_offlist, name, getattr(approval_offlist, name) | tools)
        patcher.start()
        testcase.addCleanup(patcher.stop)
