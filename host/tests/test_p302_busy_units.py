"""P302 (owner 2026-10-15 09:47): a Jarvis dispatch unit `agentj-pNNN` belongs to the workflow it works in, not to
「临时帮手 · 不属于任何工作流」 — its place is the deepest cwd of its control group, its title the PROMPT-<N>.md heading;
an `agentj busy run` item inside the same unit is the same job and shows once.
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import busy  # noqa: E402

LIST = "agentj-p302.service loaded active running 掌枢 · 菜单热修\n"
SHOW = "Id=agentj-p302.service\nControlGroup=/user.slice/app.slice/agentj-p302.service\n"


def fake_run(cmd, **_):
    return SimpleNamespace(stdout=SHOW if "show" in cmd else LIST)


class P302Units(unittest.TestCase):
    def test_place_title_and_role(self):
        with tempfile.TemporaryDirectory() as d:
            work = pathlib.Path(d, "vibe-remote", ".worktrees", "p302")
            (pathlib.Path(d, "vibe-remote", ".jarvis")).mkdir(parents=True)
            work.mkdir(parents=True)
            pathlib.Path(d, "vibe-remote", ".jarvis", "PROMPT-302.md").write_text("# P302 菜单页第二轮热修\n\nbody\n")
            cwds = {1: d, 2: str(work)}
            places = lambda units, run: busy.unit_places(units, run, pids_of=lambda cg: [1, 2],  # noqa: E731
                                                         readlink=lambda p: cwds[int(p.split("/")[2])])
            [it] = busy.scan_units(fake_run, places=places)
        self.assertEqual(it["role"], "CEO")
        self.assertEqual(it["cg"], "/user.slice/app.slice/agentj-p302.service")
        self.assertTrue(it["cwd"].endswith("p302"), "the deepest working directory of the unit wins")
        self.assertEqual(it["label"], "P302 菜单页第二轮热修")

    def test_no_place_keeps_the_description(self):
        [it] = busy.scan_units(fake_run, places=lambda units, run: {})
        self.assertEqual((it["who"], it["label"]), ("掌枢", "菜单热修"))
        self.assertNotIn("cwd", it)

    def test_show_failure_is_no_place(self):
        boom = lambda *a, **k: (_ for _ in ()).throw(OSError())  # noqa: E731
        self.assertEqual(busy.unit_places(["agentj-p1.service"], boom), {})
        self.assertEqual(busy.unit_places([], fake_run), {})

    def test_deepest_cwd_skips_unreadable(self):
        def rl(p):
            if "/7/" in p:
                raise OSError
            return {"/proc/1/cwd": "/a", "/proc/2/cwd": "/a/b/c"}[p]
        self.assertEqual(busy.deepest_cwd([1, 7, 2], rl), "/a/b/c")
        self.assertIsNone(busy.deepest_cwd([], rl))

    def test_same_job_by_report(self):
        u = {"key": "unit:agentj-p302.service", "cg": None}
        self.assertTrue(busy.same_job({"report": "/x/notes/REPORT-302.md"}, u))
        self.assertFalse(busy.same_job({"report": "/x/notes/REPORT-303.md"}, u))
        self.assertFalse(busy.same_job({}, u))

    def test_board_shows_one_row_per_job(self):
        with tempfile.TemporaryDirectory() as d:
            busy.write_item(d, busy.item("run:x", "掌枢", "ceo", "P302", report="/r/notes/REPORT-302.md"))
            board = busy.Board.__new__(busy.Board)
            board.root, board.outside = d, []
            board.units = [busy.item("unit:agentj-p302.service", "掌枢", "ceo", "菜单热修")]
            board.refresh_outside(units=False)
            self.assertEqual([x["key"] for x in board.outside], ["run:x"])


if __name__ == "__main__":
    unittest.main()
