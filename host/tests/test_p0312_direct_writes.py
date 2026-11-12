"""Prevent new, unreviewed conversation-feed writers.

The baseline is a closed inventory of existing call shapes, not approval of legacy
writers. Removing a writer is allowed; adding or changing one requires a reviewed
exception in agentjarvis/ARCHITECTURE.md and an explicit inventory update.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "agentj"
ALLOW = Path(__file__).with_name("p0312_direct_writes_allowlist.json")
WRITERS = frozenset({"hist_add", "agent_notice", "local_notice", "channel_notice", "task_notice", "_send_legacy"})


def is_writer(node):
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
    return (name in WRITERS
            or name == "add" and isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Attribute) and fn.value.attr == "hist"
            or name == "emit" and node.args and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "agent_notice"
            or name == "_remember" and node.args and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "notice")


def inventory():
    found = {}
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = Counter()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not is_writer(node):
                continue
            shape = ast.dump(node, annotate_fields=True, include_attributes=False)
            hits[hashlib.sha256(shape.encode()).hexdigest()[:20]] += 1
        if hits:
            found[str(path.relative_to(ROOT))] = hits
    return found


class DirectWriteAllowlist(unittest.TestCase):
    def test_no_unreviewed_direct_message_writer(self):
        expected = json.loads(ALLOW.read_text(encoding="utf-8"))
        additions = {}
        for path, hits in inventory().items():
            for fingerprint, count in hits.items():
                previous = expected.get(path, {}).get(fingerprint, 0)
                if count > previous:
                    additions.setdefault(path, {})[fingerprint] = count - previous
        self.assertFalse(additions, f"New direct message writers need a documented exception: {additions}")

    def test_inventory_detects_new_writer(self):
        sample = ast.parse("host.agent_notice('new background message')")
        shape = ast.dump(sample.body[0].value, annotate_fields=True, include_attributes=False)
        fingerprint = hashlib.sha256(shape.encode()).hexdigest()[:20]
        self.assertNotIn(fingerprint, json.loads(ALLOW.read_text(encoding="utf-8")).get("serve.py", {}))


if __name__ == "__main__":
    unittest.main()
