"""P142 item 5b (B19): every tasks.json read-modify-write (enable/disable from terminal / phone card / account page, the
scheduler's `ran`, a run's `last`) goes through tasks.update under its own flock — concurrent writers never lose a change.
Negative control: the same race without the lock loses updates."""
import _hermetic  # noqa: F401,I001
import contextlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import tasks  # noqa: E402
from agentj.state import State  # noqa: E402

N = 8


class TasksLock(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix='aj-p142tl-')
        self.addCleanup(shutil.rmtree, d, True)
        self.st = State(Path(d) / 'st')
        self.st.root.mkdir(parents=True)

    def race(self):
        slow = tasks.load

        def load(st):
            d = slow(st)
            time.sleep(0.03)                            # widen the read → write window
            return d
        with patch.object(tasks, 'load', load):
            ts = [threading.Thread(target=tasks.update, args=(self.st, lambda d, i=i: d['ran'].__setitem__(f't{i}', i)))
                  for i in range(N)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        return tasks.load(self.st)['ran']

    def test_concurrent_updates_all_land(self):
        self.assertEqual(sorted(self.race()), sorted(f't{i}' for i in range(N)))
        self.assertTrue((self.st.root / 'tasks.lock').exists())

    def test_without_the_lock_updates_are_lost(self):
        with patch.object(State, 'file_lock', lambda self, name: contextlib.nullcontext()):
            self.assertLess(len(self.race()), N)

    def test_other_process_waits_for_the_lock(self):
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from agentj import tasks\nfrom agentj.state import State\nfrom pathlib import Path\n"
                "tasks.update(State(Path(%r)), lambda d: d['ran'].__setitem__('child', 1))\n"
                % (str(Path(tasks.__file__).parents[1]), str(self.st.root)))
        with self.st.file_lock('tasks'):
            p = subprocess.Popen([sys.executable, '-c', code])
            self.addCleanup(lambda: p.poll() is None and (p.kill(), p.wait()))
            time.sleep(0.5)
            self.assertIsNone(p.poll(), 'the child is blocked on tasks.lock')
            d = tasks.load(self.st)
            d['ran']['parent'] = 1
            tasks.save(self.st, d)
        self.assertEqual(p.wait(10), 0)
        self.assertEqual(sorted(tasks.load(self.st)['ran']), ['child', 'parent'])


if __name__ == '__main__':
    unittest.main()
