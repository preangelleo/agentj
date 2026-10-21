"""P423 #160 (owner 2026-10-20): `agentj update apply` refused with 「3 个后台任务（在办事项）」 would be interrupted, but the
three were `systemd-run --user` units (busy items with --pid) — no child of serve; after the upgrade they were still active.

- the upgrade card's 'bg' count = only items a serve restart really ends: in-process items (sub-agents, workflow runs),
  serve's descendants, pids in serve's control group (serve under systemd), fenced pids, anything unprovable;
- an independent worker (a dispatch unit, a pid whose parent chain misses serve and outside serve's cgroup) is not counted;
- macOS: one `ps` call for the parent chain; a pid in serve's process group counts (launchd stops the group).
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import auto_update, busy  # noqa: E402

SERVE = 500
SERVE_CG = "/user.slice/user-1000.slice/user@1000.service/app.slice/agentj.service"
UNIT_CG = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-u7.service"


def fake_proc(d, procs, own_cg=SERVE_CG):
    """procs: pid → (ppid, cgroup). Writes <d>/<pid>/stat|cgroup and <d>/self/{stat,cgroup}."""
    root = pathlib.Path(d)
    for pid, (ppid, cg) in procs.items():
        p = root / str(pid)
        p.mkdir(parents=True)
        (p / "stat").write_text(f"{pid} (py (x) thon) S {ppid} {pid} {pid} 0 -1\n")
        (p / "cgroup").write_text(f"0::{cg}\n")
    (root / "self").mkdir()
    (root / "self" / "stat").write_text(f"{SERVE} (agentj) S 1 1 1\n")
    (root / "self" / "cgroup").write_text(f"0::{own_cg}\n")
    return root


class Survives(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.proc = fake_proc(self.d.name, {
            SERVE: (1, SERVE_CG),
            600: (SERVE, SERVE_CG),         # serve's child
            601: (600, SERVE_CG),           # its grandchild
            700: (1, SERVE_CG),             # double-forked away, still in serve's cgroup
            800: (1, UNIT_CG),              # `systemd-run --user` worker (#160)
            801: (800, UNIT_CG),
        })

    def s(self, it):
        return busy.survives_restart(it, serve_pid=SERVE, proc=self.proc)

    def test_independent_worker_survives(self):
        self.assertTrue(self.s({"key": "p423-x", "pid": 801}))
        self.assertTrue(self.s({"key": "unit:agentj-p400.service"}), "a systemd unit / launchd job")

    def test_serve_tree_and_cgroup_count(self):
        for pid in (SERVE, 600, 601, 700):
            self.assertFalse(self.s({"key": f"k{pid}", "pid": pid}), pid)

    def test_unprovable_counts(self):
        self.assertFalse(self.s({"key": "k"}), "no pid")
        self.assertFalse(self.s({"key": "k", "pid": 801, "ns_other": True}), "fenced pid")
        self.assertFalse(self.s({"key": "k", "pid": 999}), "unreadable /proc entry")
        self.assertFalse(self.s({"key": "k", "pid": "801"}), "not an int")

    def test_serve_not_a_service_only_the_tree_counts(self):
        with tempfile.TemporaryDirectory() as d:
            proc = fake_proc(d, {SERVE: (1, "/user.slice/session-2.scope"), 700: (1, "/user.slice/session-2.scope")},
                             own_cg="/user.slice/session-2.scope")
            self.assertTrue(busy.survives_restart({"key": "k", "pid": 700}, serve_pid=SERVE, proc=proc))

    def test_macos_uses_ps_and_process_group(self):
        nolinux = pathlib.Path(self.d.name, "none")
        parents = {801: 800, 800: 1, 600: SERVE}
        with mock.patch.object(busy.os, "getpgid", side_effect=lambda p: 77 if p == SERVE else p):
            self.assertTrue(busy.survives_restart({"key": "k", "pid": 801}, SERVE, nolinux, parents))
            self.assertFalse(busy.survives_restart({"key": "k", "pid": 600}, SERVE, nolinux, parents))
        with mock.patch.object(busy.os, "getpgid", return_value=77):
            self.assertFalse(busy.survives_restart({"key": "k", "pid": 801}, SERVE, nolinux, parents), "serve's group")
        self.assertFalse(busy.survives_restart({"key": "k", "pid": 801}, SERVE, nolinux, {}), "ps saw nothing")

    def test_ps_parents(self):
        ok = lambda *a, **k: SimpleNamespace(stdout="  1     0\n 801   800\nbad line\n")  # noqa: E731
        self.assertEqual(busy.ps_parents(ok), {1: 0, 801: 800})
        boom = lambda *a, **k: (_ for _ in ()).throw(OSError())  # noqa: E731
        self.assertIsNone(busy.ps_parents(boom))


class UpgradeCard(unittest.TestCase):
    def board(self):
        b = busy.Board(None)
        b.start("main:task:1", "Explore", "temp", "读代码")                          # an in-process sub-agent
        b.outside = [busy.item("unit:agentj-p400.service", "掌枢", "ceo", "P400"),
                     busy.item("p401", "掌枢", "ceo", "P401", pid=801),
                     busy.item("p402", "掌枢", "ceo", "P402", pid=600)]
        return b

    def test_split_and_impact(self):
        b = self.board()
        alive = {"unit:agentj-p400.service", "p401"}
        self.assertEqual(b.restart_split(lambda v, **_: v["key"] in alive), (2, 2))
        host = SimpleNamespace(agent=None, asks={}, questions={}, peers=None, bots=None, busy=b,
                               elevate=SimpleNamespace(cards={}, out=None, login=None, contacts=None),
                               capabilities=SimpleNamespace(cards={}),
                               scheduler=SimpleNamespace(current_id=None, waiting=[], workdir=lambda: None))
        with mock.patch.object(busy.Board.restart_split, "__defaults__", (lambda v, **_: v["key"] in alive,)):
            got = {i["k"]: i["n"] for i in auto_update.impact(host)}
        self.assertEqual(got, {"bg": 2}, "only the sub-agent and serve's child")
        b.outside = b.outside[:2]
        b.mem.clear()
        with mock.patch.object(busy.Board.restart_split, "__defaults__", (lambda v, **_: True,)):
            self.assertEqual(auto_update.impact(host), [], "nothing would be interrupted: upgrade at once")


if __name__ == "__main__":
    unittest.main()
