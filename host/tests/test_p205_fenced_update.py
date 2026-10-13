"""P205 item 4 (support st_QtsZnVK2Xd4CWYYVV4bq6Q): `agentj update apply` run by the Agent inside the fence.

The fence hides the host's state directory (an empty tmpfs; only agentperm/ comes back). Before: doctor's `state` / `serve`
rows failed there, so a good upgrade ended `UPGRADE_RESULT failed / doctor_failed`, and apply silently skipped the pending
record / marker (the `st.exists()` guards).

- detection: `AGENTJ_FENCED=1` (set by every fence this version starts), else the Agent socket's state directory being a
  tmpfs mount point (a fence started by an older serve) — checked in a real bubblewrap fence when available;
- doctor in the fence: `state` / `serve` are ! with `"unverifiable": true` (a WARN, so an older apply reads them as non-fail);
- channel reachable (elevate.sock `{"t": "upgrade"}`): the host caches the old wheel, writes the pending record (from = its
  version), rolls back, runs the doctor → `upgraded` with `health: host`, or `doctor_failed` when the host's doctor fails;
- channel unreachable: still installs; `unverifiable_in_fence` (result ok, exit 0), `handoff: not_recorded`, no rollback;
- unfenced: unchanged (pending in the state directory, no health / handoff lines).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import __version__, doctor, elevate, fence, update  # noqa: E402
from agentj.state import State  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk

INFO = {"kind": "uv", "where": "/fake", "legacy": False}
NEW = "9.9.9"


def wheel(st, version):
    p = st.root / "upgrade-cache" / f"agentj-{version}-py3-none-any.whl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"old-wheel-bytes")
    p.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"old-wheel-bytes").hexdigest()}))
    return p


def rows(**over):
    base = {i: "ok" for i in ("version", "python", "platform", "state", "relay", "dashboard", "service", "serve")}
    base.update(over)
    return [{"id": i, "status": s, "summary": i, "hint": ""} for i, s in base.items()]


class Runner:
    """Fake installer + the new CLI: `--version` answers the installed version, `doctor --json` answers `doctor_rows`."""
    def __init__(self, current=NEW, doctor_rows=None, fail=()):
        self.calls, self.current, self.doctor_rows, self.fail = [], current, doctor_rows, fail

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, f"agentj {self.current}", "")
        if argv[-2:] == ["doctor", "--json"]:
            r = self.doctor_rows if self.doctor_rows is not None else rows()
            return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": r}), "")
        if any(f in argv for f in self.fail):
            return subprocess.CompletedProcess(argv, 5, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def fenced_rows():
    """What the new doctor answers inside the fence without the host."""
    with patch.dict(os.environ, {fence.FENCED_ENV: "1"}):
        return [doctor._unverifiable("state"), doctor._unverifiable("serve")] + rows()[:3]


class Detect(unittest.TestCase):
    def test_marker_and_tmpfs_heuristic(self):
        self.assertTrue(fence.inside({fence.FENCED_ENV: "1"}))
        self.assertFalse(fence.inside({}))
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d, "state")
            env = {"AGENTJ_ELEVATE_SOCK": str(root / "agentperm" / "elevate.sock")}
            mi = pathlib.Path(d, "mountinfo")
            mi.write_text(f"1 2 0:1 / / rw - ext4 /dev/x rw\n9 1 0:9 / {os.path.realpath(root)} rw,nosuid - tmpfs tmpfs rw\n")
            with patch.object(fence, "MOUNTINFO", str(mi)), patch.object(sys, "platform", "linux"):
                self.assertTrue(fence.inside(env), "an older serve's fence: the state dir is a tmpfs mount point")
                mi.write_text("1 2 0:1 / / rw - ext4 /dev/x rw\n")
                self.assertFalse(fence.inside(env), "shared / unfenced: the real state directory")
                self.assertFalse(fence.inside({"AGENTJ_ELEVATE_SOCK": "/elsewhere/x.sock"}))

    def test_bwrap_and_sandbox_argv_set_the_marker(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.perm_dir.mkdir(parents=True)
            a = fence.bwrap_argv(st, d, home=d, runtime="", environ={})
            i = a.index("--setenv")
            self.assertEqual(a[i:i + 3], ["--setenv", fence.FENCED_ENV, "1"])
            self.assertLess(i, a.index("--"))
            s = fence.sandbox_argv(st, d, home=d, environ={})
            self.assertEqual(s[-1], f"{fence.FENCED_ENV}=1")


def _fence_ok() -> bool:
    if sys.platform != "linux":
        return False
    with tempfile.TemporaryDirectory() as d:
        st = State(pathlib.Path(d) / "s")
        st.root.mkdir()
        ok = fence.problem(st, d) is None
    fence._probe_cache.clear()
    return ok


@unittest.skipUnless(_fence_ok(), "bubblewrap fence not available on this machine")
class RealFence(unittest.TestCase):
    def test_inside_a_real_fence(self):
        script = ("import json, os, sys\nfrom agentj import fence, doctor\nfrom agentj.state import State\n"
                  "st = State(__import__('pathlib').Path(sys.argv[1]))\n"
                  "marked = fence.inside()\nos.environ.pop(fence.FENCED_ENV, None)\n"
                  "print(json.dumps({'marked': marked, 'heuristic': fence.inside(), 'visible': st.exists(),"
                  " 'rows': [doctor.check_state(st), doctor.check_serve(st)]}))\n")
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            st.perm_dir.mkdir(exist_ok=True)
            work = pathlib.Path(d) / "work"
            work.mkdir()
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "PYTHONPATH": str(HERE.parent),
                   "AGENTJ_ELEVATE_SOCK": str(st.perm_dir / elevate.SOCK_NAME)}
            argv = fence.bwrap_argv(st, str(work), home=d, runtime="", environ=env) + [sys.executable, "-c", script, str(st.root)]
            r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            got = json.loads(r.stdout)
            self.assertEqual((got["marked"], got["heuristic"], got["visible"]), (True, True, False))
            for row, cid in zip(got["rows"], ("state", "serve")):
                self.assertEqual((row["id"], row["status"], row.get("unverifiable")), (cid, "warn", True))
                self.assertIn("隔离环境里看不到主机状态", row["summary"])
            # outside the fence, the same environment and state: not fenced, the real rows
            self.assertFalse(fence.inside(env))
            self.assertEqual(doctor.check_state(st)["status"], "ok")


class DoctorRows(unittest.TestCase):
    def test_rows_and_an_older_apply_reads_them_as_non_fail(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")              # not visible / not initialised
            with patch.dict(os.environ, {fence.FENCED_ENV: "1"}):
                s, v = doctor.check_state(st), doctor.check_serve(st)
            self.assertEqual([(r["id"], r["status"], r["unverifiable"]) for r in (s, v)],
                             [("state", "warn", True), ("serve", "warn", True)])
            self.assertIn("inside the Agent's isolated environment", s["summary"])
            self.assertEqual(update.upgrade_health([s, v] + rows()[:3]), (False, []))
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(fence.FENCED_ENV, None)
                self.assertEqual(doctor.check_state(st)["status"], "fail", "unfenced: unchanged")


class _Apply(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_l1 import _host, _state
        self.tmp = tempfile.TemporaryDirectory(prefix="p205u-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "aj.p205.test"})
        env.start()
        self.addCleanup(env.stop)
        self.host_st = _state(root / "host")                     # the real state (outside the fence)
        self.view = State(root / "fenced-view")                   # what the fenced Agent sees: an empty folder
        self.view.root.mkdir()
        self.host = _host(self.host_st, [])
        self.old = wheel(self.host_st, __version__)
        self.rolled = []
        for p in (patch.object(update, "install_kind", return_value=INFO),
                  patch.object(update, "new_argv", return_value=["agentj"]),
                  patch.object(update, "writable", return_value=True),
                  patch.object(update, "rollback_wheel", side_effect=lambda st: self.old if st.root == self.host_st.root else None),
                  patch.object(update, "roll_back", side_effect=self.fake_roll_back)):
            p.start()
            self.addCleanup(p.stop)

    def fake_roll_back(self, st, info, wheel_path, frm, run):
        self.rolled.append((str(st.root), str(wheel_path) if wheel_path else None, frm))
        (st.root / update.PENDING).unlink(missing_ok=True)
        (st.root / update.UPGRADED).unlink(missing_ok=True)
        return bool(wheel_path)

    async def start_host(self, host_rows=None):
        self.host_runner = Runner(doctor_rows=host_rows)
        real = update.host_op
        patcher = patch.object(update, "host_op", side_effect=lambda st, req, mem, run=None: real(st, req, mem, self.host_runner))
        patcher.start()
        self.addCleanup(patcher.stop)
        await self.host.elevate.start()
        self.addAsyncCleanup(self.host.elevate.stop)

    async def apply(self, run, fenced=True, svc_on=True):
        return await asyncio.to_thread(update.apply, self.view if fenced else self.host_st, NEW,
                                       plan_fn=lambda st, info, target: update.commands(info, target),
                                       check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=svc_on, fenced=fenced)


class FencedWithHost(_Apply):
    async def test_success_host_records_and_checks(self):
        await self.start_host()
        run = Runner(doctor_rows=fenced_rows())
        res = await self.apply(run)
        self.assertEqual((res["result"], res["reason"], res["exit"], res["health"], res["service"]),
                         ("ok", "upgraded", 0, "host", "restart_scheduled"))
        self.assertNotIn("handoff", res)
        pend = json.loads((self.host_st.root / update.PENDING).read_text())
        self.assertEqual((pend["from"], pend["to"], pend["old"]), (__version__, NEW, str(self.old)))
        self.assertEqual(list(self.view.root.iterdir()), [], "nothing written into the fenced view")
        self.assertFalse(any(c[-2:] == ["doctor", "--json"] for c in run.calls), "the host ran the doctor, not the fence")
        self.assertTrue(any(c[-2:] == ["doctor", "--json"] for c in self.host_runner.calls))
        block = update.result_block(res)
        self.assertIn("UPGRADE_RESULT ok", block)
        self.assertIn("health: host", block)

    async def test_no_service_marker_by_host(self):
        await self.start_host()
        res = await self.apply(Runner(), svc_on=False)
        self.assertEqual((res["reason"], res["service"]), ("upgraded", "not_installed"))
        mk = json.loads((self.host_st.root / update.UPGRADED).read_text())
        self.assertEqual((mk["from"], mk["to"]), (__version__, NEW))

    async def test_host_doctor_fail_is_a_failure(self):
        await self.start_host(rows(serve="fail"))
        res = await self.apply(Runner())
        self.assertEqual((res["result"], res["reason"], res["health"]), ("failed", "doctor_failed", "host"))

    async def test_version_check_fails_host_rolls_back(self):
        await self.start_host()
        res = await self.apply(Runner(current="1.0.0"))
        self.assertEqual((res["reason"], res["rollback"]), ("not_upgraded", "ok"))
        self.assertEqual(self.rolled, [(str(self.host_st.root), str(self.old), __version__)])
        self.assertFalse((self.host_st.root / update.PENDING).exists())

    async def test_host_refuses_bad_targets(self):
        mem = {}
        for req in ({"op": "pending", "to": __version__}, {"op": "marker", "to": "x"}, {"op": "pending"}, {"op": "nope"}):
            self.assertFalse(update.host_op(self.host_st, req, mem)["ok"], req)
        self.assertFalse((self.host_st.root / update.PENDING).exists())
        # pending without a prepare: no wheel named (nothing path-like is taken from the request)
        self.assertTrue(update.host_op(self.host_st, {"op": "pending", "to": NEW, "old": "/evil.whl"}, mem)["ok"])
        self.assertIsNone(json.loads((self.host_st.root / update.PENDING).read_text())["old"])


class FencedNoHost(_Apply):
    async def test_unreachable_is_unverifiable_not_failed(self):
        with patch.dict(os.environ, {elevate.ENV: str(self.view.root / "agentperm" / "elevate.sock")}):
            run = Runner(doctor_rows=fenced_rows())
            res = await self.apply(run)
        self.assertEqual((res["result"], res["reason"], res["exit"], res["health"], res["handoff"]),
                         ("ok", "unverifiable_in_fence", 0, "unverifiable", "not_recorded"))
        self.assertTrue(any(c[-2:] == ["doctor", "--json"] for c in run.calls), "the local doctor still runs")
        self.assertFalse((self.host_st.root / update.PENDING).exists())
        block = update.result_block(res)
        for line in ("UPGRADE_RESULT ok", "reason: unverifiable_in_fence", "health: unverifiable", "handoff: not_recorded",
                     "next: tell your human"):
            self.assertIn(line, block)

    async def test_unreachable_other_failures_still_fail(self):
        with patch.dict(os.environ, {elevate.ENV: str(self.view.root / "agentperm" / "elevate.sock")}):
            res = await self.apply(Runner(doctor_rows=fenced_rows() + [{"id": "version", "status": "fail", "summary": "", "hint": ""}]))
            self.assertEqual((res["result"], res["reason"]), ("failed", "doctor_failed"))
            res = await self.apply(Runner(current="1.0.0"))
        self.assertEqual((res["reason"], res["rollback"]), ("not_upgraded", "unavailable"))
        self.assertEqual(self.rolled, [], "nothing to roll back to from inside the fence")

    async def test_older_serve_refuses_the_op(self):
        await self.host.elevate.start()            # a serve without host_op: {"t": "upgrade"} is not a kind it knows
        self.addAsyncCleanup(self.host.elevate.stop)
        with patch.object(update, "host_op", side_effect=elevate.Refused("shape", "t: sudo | secret")):
            res = await self.apply(Runner(doctor_rows=fenced_rows()))
        self.assertEqual((res["reason"], res["handoff"]), ("unverifiable_in_fence", "not_recorded"))


class Unfenced(_Apply):
    async def test_unchanged(self):
        with patch.object(update, "rollback_wheel", return_value=None):
            res = await self.apply(Runner(), fenced=False)
        self.assertEqual((res["result"], res["reason"]), ("ok", "upgraded"))
        self.assertNotIn("health", res)
        self.assertNotIn("handoff", res)
        self.assertEqual(json.loads((self.host_st.root / update.PENDING).read_text())["from"], __version__)
        self.assertNotIn("health:", update.result_block(res))

    def test_autodetect_off_in_a_normal_shell(self):
        os.environ.pop(fence.FENCED_ENV, None)
        self.assertFalse(fence.inside())


if __name__ == "__main__":
    unittest.main()
