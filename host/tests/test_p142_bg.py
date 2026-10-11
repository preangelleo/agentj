"""P142 item 2 (B13 step 1): serve's fire-and-forget tasks go through `Host._bg` — held by reference, and a failure lands in
host.log as `bg_task_failed` (what + exception type, never the message). No bare `create_task(...)` statement is left."""
import _hermetic  # noqa: F401,I001
import ast
import asyncio
import gc
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import diskq, serve  # noqa: E402
from agentj.state import State  # noqa: E402

SERVE = pathlib.Path(serve.__file__)


class Bg(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="aj-p142bg-"))
        self.addCleanup(shutil.rmtree, d, True)
        self.st = State(d / "st")
        self.st.root.mkdir(parents=True)
        self.host = object.__new__(serve.Host)
        self.host.st = self.st

    def events(self):
        diskq.flush()
        if not self.st.log_path.exists():
            return []
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines()]

    def test_failure_is_logged_without_message(self):
        async def boom():
            await asyncio.sleep(0)
            raise ValueError("secret-ish detail")

        async def main():
            t = self.host._bg(boom(), "tts")
            self.assertIn(t, self.host._bg_tasks)
            gc.collect()                                 # held by reference: survives a collection
            await asyncio.sleep(0.05)
            self.assertNotIn(t, self.host._bg_tasks)
        asyncio.run(main())
        ev = [e for e in self.events() if e["ev"] == "bg_task_failed"]
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["why"], ev[0]["exception_type"]), ("tts", "ValueError"))
        self.assertNotIn("secret-ish", self.st.log_path.read_text())

    def test_success_and_cancel_are_quiet(self):
        async def ok():
            await asyncio.sleep(0)

        async def main():
            self.host._bg(ok(), "ok")
            t = self.host._bg(asyncio.sleep(10), "long")
            await asyncio.sleep(0.01)
            t.cancel()
            await asyncio.sleep(0.01)
            self.assertEqual(self.host._bg_tasks, set())
        asyncio.run(main())
        self.assertEqual([e for e in self.events() if e["ev"] == "bg_task_failed"], [])

    def test_no_bare_create_task_statement_left(self):
        bare = []
        for node in ast.walk(ast.parse(SERVE.read_text())):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                f = node.value.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in ("create_task", "ensure_future"):
                    bare.append(node.lineno)
        self.assertEqual(bare, [], f"bare create_task in serve.py at lines {bare}: use self._bg(coro, what)")


if __name__ == "__main__":
    unittest.main()
