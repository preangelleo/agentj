"""F8: every task.json in the closed template library passes the host's own task contract (taskspec), so a template the
Dashboard serves is one `agentj wizard add-template` accepts and `agentj tasks` can schedule. The library is not part of
the public export; outside the private tree this test skips."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import unittest
from pathlib import Path

from agentj import taskspec

LIB = Path(__file__).resolve().parents[2] / "templates"


@unittest.skipUnless((LIB / "catalog.json").is_file(), "closed template library not in this tree")
class FactoryTemplates(unittest.TestCase):
    def test_catalog_task_specs_are_valid_and_dormant(self):
        catalog = json.loads((LIB / "catalog.json").read_text(encoding="utf-8"))["templates"]
        self.assertGreaterEqual(len(catalog), 15)
        for entry in catalog:
            with self.subTest(template=entry["id"]):
                task = json.loads((LIB / entry["id"] / "task.json").read_text(encoding="utf-8"))
                self.assertEqual(taskspec.problems(task, entry["id"]), [])
                self.assertIs(task["enabled"], False)
                self.assertFalse(taskspec.secret_like(json.dumps(task, ensure_ascii=False)))


if __name__ == "__main__":
    unittest.main()
