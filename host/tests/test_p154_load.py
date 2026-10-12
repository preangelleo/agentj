"""P154 追加 4: the computer's load for the line under the reply card's header (PROTOCOL §22 `busy.load`).

- sys_load = 1-minute load average ÷ cores, clamped 0..1, rounded to 0.01; None where there is none / it cannot be read;
- Board.sample_load reads at most every LOAD_EVERY s and only reports a change > LOAD_STEP (or appearing / going away);
- the busy message carries `load` only when known, and serve sends a load change to the phones once.
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import busy, serve  # noqa: E402
from agentj.state import State  # noqa: E402


class SysLoad(unittest.TestCase):
    def test_per_core_clamped_and_rounded(self):
        self.assertEqual(busy.sys_load(lambda: (2.0, 1, 1), lambda: 8), 0.25)
        self.assertEqual(busy.sys_load(lambda: (1.0, 1, 1), lambda: 3), 0.33)
        self.assertEqual(busy.sys_load(lambda: (40.0, 1, 1), lambda: 4), 1.0)
        self.assertEqual(busy.sys_load(lambda: (0.0, 0, 0), lambda: None), 0.0)

    def test_none_when_unavailable(self):
        self.assertIsNone(busy.sys_load(None))

        def boom():
            raise OSError("no loadavg")
        self.assertIsNone(busy.sys_load(boom, lambda: 4))
        self.assertIsNone(busy.sys_load(lambda: (float("nan"), 0, 0), lambda: 4))
        self.assertIsNone(busy.sys_load(lambda: (-1.0, 0, 0), lambda: 4))

    def test_real_reading_is_in_range(self):
        v = busy.sys_load()
        self.assertTrue(v is None or 0.0 <= v <= 1.0, v)


class Sample(unittest.TestCase):
    def test_rate_and_step(self):
        b = busy.Board(None)
        vals = iter([0.30, 0.33, 0.40, 0.40, None, 0.5])
        nxt = lambda: next(vals)  # noqa: E731
        b_sample = lambda now: b.sample_load(now, nxt, lambda: None)  # noqa: E731 — no capacity here (see Cap)
        self.assertTrue(b_sample(100.0))                     # first value appears
        self.assertEqual(b.msg()["load"], 0.30)
        self.assertFalse(b_sample(101.0))                    # within LOAD_EVERY: not read at all
        self.assertFalse(b_sample(100.0 + busy.LOAD_EVERY))  # 0.33: moved ≤ LOAD_STEP
        self.assertEqual(b.load, 0.30)
        self.assertTrue(b_sample(100.0 + 2 * busy.LOAD_EVERY))   # 0.40: > LOAD_STEP
        self.assertFalse(b_sample(100.0 + 3 * busy.LOAD_EVERY))
        self.assertTrue(b_sample(100.0 + 4 * busy.LOAD_EVERY))   # gone
        self.assertNotIn("load", b.msg())
        self.assertTrue(b_sample(100.0 + 5 * busy.LOAD_EVERY))   # back

    def test_no_load_no_field(self):
        b = busy.Board(None)
        self.assertNotIn("load", b.msg())


class Cap(unittest.TestCase):
    """追加 11: `cap` = running + P156 capacity()'s can_start; absent until that function exists."""

    def test_cap_from_capacity(self):
        b = busy.Board(None)
        b.mem["a"] = busy.item("a", "Bob", "ceo", "x")
        b.mem["b"] = busy.item("b", None, "temp", "y")
        old = busy.__dict__.get("capacity")
        try:
            busy.capacity = lambda running: {"can_start": 7 if running == 2 else -99}
            self.assertEqual(b.sample_cap(), 9)
            self.assertTrue(b.sample_load(100.0, lambda: None))           # no load, but cap appeared → a change
            self.assertEqual(b.msg()["cap"], 9)
            self.assertNotIn("load", b.msg())
            self.assertFalse(b.sample_load(100.0 + busy.LOAD_EVERY, lambda: None))
            busy.capacity = lambda running: 1 / 0                          # failing → no field
            self.assertTrue(b.sample_load(100.0 + 2 * busy.LOAD_EVERY, lambda: None))
            self.assertNotIn("cap", b.msg())
            busy.capacity = lambda running: {"can_start": 500}
            self.assertEqual(b.sample_cap(), 64)
        finally:
            if old is None:
                busy.__dict__.pop("capacity", None)
            else:
                busy.capacity = old

    def test_without_capacity_no_field(self):
        b = busy.Board(None)
        if "capacity" not in busy.__dict__:
            self.assertIsNone(b.sample_cap())
        self.assertNotIn("cap", b.msg())

    def test_wire_keeps_32_items(self):
        b = busy.Board(None)
        for i in range(40):
            b.mem[f"k{i:02}"] = busy.item(f"k{i:02}", None, "temp", f"t{i}")
        self.assertEqual(len(b.items()), busy.MAX_ITEMS)
        self.assertEqual(busy.MAX_ITEMS, 32)


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_load_change_goes_out_once(self):
        sent = []
        self.host._post = lambda fn, obj_fn: sent.append(obj_fn(None))
        self.host.busy.load = 0.2
        self.host.busy_changed()
        self.host.busy_changed()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["load"], 0.2)
        self.host.busy.load = 0.9
        self.host.busy_changed()
        self.assertEqual([m["load"] for m in sent], [0.2, 0.9])
        self.host.busy.cap = 10                                     # 追加 11: a cap change goes out too
        self.host.busy_changed()
        self.assertEqual(sent[-1]["cap"], 10)
        self.assertEqual(len(sent), 3)


if __name__ == "__main__":
    unittest.main()
