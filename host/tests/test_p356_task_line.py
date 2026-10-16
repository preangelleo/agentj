"""P356 × P348: scheduled tasks may see coding-plan keys in their environment, so the VERDICT line a run ends with is
masked before it reaches the run record, activity, the phone's notice / task list and the readback (privacy.owner_text)."""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import privacy, tasks  # noqa: E402

KEY = privacy.FAKE_SECRETS["openrouter"]
TOKEN = privacy.FAKE_SECRETS["keyseg"]


class VerdictLine(unittest.TestCase):
    def run_finish(self, line):
        seen = {}
        host = types.SimpleNamespace(task_finished=lambda res: seen.setdefault("finished", dict(res)))
        t = tasks.Scheduler(host)
        t._end_run = lambda rid, res: seen.setdefault("record", dict(res))
        t._finish({"id": "daily"}, {"run_id": "r1", "id": "daily", "trigger": "schedule", "verdict": "ok", "line": line})
        return seen

    def test_key_shapes_are_masked_everywhere_the_line_goes(self):
        seen = self.run_finish(f"ok — env: KIMI_API_KEY={KEY} ARK_CODING_PLAN_API_KEY={TOKEN}")
        for where in ("record", "finished"):
            with self.subTest(where):
                line = seen[where]["line"]
                self.assertNotIn(KEY, line)
                self.assertNotIn(TOKEN, line)
                self.assertTrue(line.startswith("ok — env:"))
                self.assertLessEqual(len(line), 300)

    def test_plain_lines_and_contacts_unchanged(self):
        line = "ok — 周报已发到 boss@owner.example，见 ~/work/report.md"
        self.assertEqual(self.run_finish(line)["finished"]["line"], line)


if __name__ == "__main__":
    unittest.main()
