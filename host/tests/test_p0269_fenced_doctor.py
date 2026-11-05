"""0.26.9 selfheal h-fixes (ADR-0269-fenced-doctor): `agentj doctor` inside the macOS fence.

Real-machine red (matrix, Mac Intel, 0.26.7a1 → 0.26.8a1): the Agent runs `agentj update apply` inside sandbox-exec; the
upgrade installs, then `agentj doctor --json` crashes with `PermissionError: [Errno 1] Operation not permitted:
…/agentj-9/host_x25519.key` (check_wechat → _serve_status → State.exists()). bwrap hides the state directory with an empty
tmpfs (ENOENT, tolerated); sandbox-exec denies it (EPERM, not tolerated by Path.exists) → `doctor_failed / doctor_unparsable`
and the owner is told the upgrade failed although it succeeded.

Simulated here without macOS: every file operation under the state directory (agentperm/ excepted, as in the fence)
raises PermissionError(EPERM).
- fenced: no check may crash; state / serve and every row that could not read are ! "unverifiable", never ✗; `doctor
  --json` prints the {"checks": [...]} document and the upgrade blockers are empty;
- unfenced (negative control): the same denial is NOT hidden as "unverifiable" — the state row is ✗ with a permission hint;
- update apply in the fence: a local doctor that still cannot produce its document is `unverifiable_in_fence` (exit 0,
  the version was already verified) with `doctor_unparsable` among the pending checks; unfenced it stays `doctor_failed`;
  with the host channel the host's doctor decides (unchanged).
"""
import _hermetic  # noqa: F401,I001
import builtins
import contextlib
import errno
import io
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
from agentj import doctor, fence, update  # noqa: E402
from agentj.state import State  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

NO_SVC = {"installed": False, "kind": "none", "active": "", "name": "aj.p0269.test"}


@contextlib.contextmanager
def denied(root: pathlib.Path):
    """What sandbox-exec does to the host's state directory: EPERM on stat / open / list, agentperm/ still visible."""
    base = os.path.realpath(root)
    keep = os.path.join(base, "agentperm")

    def hit(p) -> bool:
        if isinstance(p, int):
            return False
        try:
            s = os.path.abspath(os.fsdecode(p))
        except TypeError:
            return False
        return (s == base or s.startswith(base + os.sep)) and not (s == keep or s.startswith(keep + os.sep))

    def wrap(real):
        def f(p, *a, **k):
            if hit(p):
                raise PermissionError(errno.EPERM, "Operation not permitted", os.fsdecode(p))
            return real(p, *a, **k)
        return f

    patches = [patch.object(os, n, wrap(getattr(os, n))) for n in ("stat", "lstat", "open", "listdir", "scandir")]
    patches += [patch.object(io, "open", wrap(io.open)), patch.object(builtins, "open", wrap(builtins.open))]
    with contextlib.ExitStack() as es:
        for p in patches:
            es.enter_context(p)
        yield


class _Base(unittest.TestCase):
    def setUp(self):
        from test_l1 import _state
        self.tmp = tempfile.TemporaryDirectory(prefix="p0269d-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        (self.st.root / "agentperm").mkdir(exist_ok=True)
        for p in (patch.object(doctor.service, "status", return_value=dict(NO_SVC)),
                  patch.object(doctor.service, "legacy_status", return_value={}),
                  patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root), "AGENTJ_SERVICE_NAME": "aj.p0269.test",
                                          "XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "xdg")})):
            p.start()
            self.addCleanup(p.stop)

    def fenced(self, on: bool):
        if on:
            return patch.dict(os.environ, {fence.FENCED_ENV: "1"})
        os.environ.pop(fence.FENCED_ENV, None)
        return contextlib.nullcontext()


class FencedDoctor(_Base):
    def test_the_matrix_crash_path(self):
        """check_wechat → _serve_status → State.exists(): the exact red of the Mac Intel run."""
        with self.fenced(True), denied(self.st.root):
            self.assertIsNone(doctor.check_wechat(self.st))
            self.assertIsNone(doctor._serve_status(self.st))
            row = doctor.check_state(self.st)
        self.assertEqual((row["id"], row["status"], row.get("unverifiable")), ("state", "warn", True))

    def test_every_row_survives_eperm(self):
        with self.fenced(True), denied(self.st.root):
            rows = doctor.run(self.st, offline=True)
            ro = doctor.run_read_only(self.st)
            up = doctor.run_upgrade(self.st)
        for r in rows + ro + up:
            self.assertIn(r["status"], ("ok", "warn", "fail"), r)
        by = {r["id"]: r for r in rows}
        for cid in ("state", "serve"):
            self.assertEqual((by[cid]["status"], by[cid].get("unverifiable")), ("warn", True), cid)
        self.assertEqual(update.upgrade_blockers(rows)[0], [], [r for r in rows if r["status"] == "fail"])
        self.assertEqual(update.upgrade_blockers(up)[0], [], [r for r in up if r["status"] == "fail"])

    def test_doctor_json_document(self):
        out = io.StringIO()
        with self.fenced(True), denied(self.st.root), contextlib.redirect_stdout(out):
            rc = doctor.main(as_json=True, offline=True)
        doc = json.loads(out.getvalue())
        self.assertTrue(doc["checks"])
        # what a fenced `update apply` judges: no upgrade blocker (rc / "ok" also count unrelated owner findings)
        self.assertEqual(update.upgrade_blockers(doc["checks"])[0], [], (rc, [r for r in doc["checks"] if r["status"] == "fail"]))
        self.assertNotIn("Traceback", out.getvalue())

    def test_unfenced_denial_is_not_hidden(self):
        """Negative control: outside the fence a denied state directory is a real fault, never 'unverifiable'."""
        with self.fenced(False), denied(self.st.root):
            row = doctor.check_state(self.st)
            rows = doctor.run(self.st, offline=True)
        self.assertEqual(row["status"], "fail")
        self.assertIn("chmod", row["hint"])
        self.assertFalse(any(r.get("unverifiable") for r in rows), [r["id"] for r in rows if r.get("unverifiable")])
        self.assertIn("state", update.upgrade_blockers(rows)[0])


class Runner:
    """The new CLI: `--version` answers the target; `doctor --json` crashes (a traceback on stderr, rc 1)."""
    def __init__(self, doctor_rc=1, doctor_out=""):
        self.calls, self.doctor_rc, self.doctor_out = [], doctor_rc, doctor_out

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "agentj 9.9.9", "")
        if argv[-2:] == ["doctor", "--json"]:
            return subprocess.CompletedProcess(argv, self.doctor_rc, self.doctor_out, "Traceback …\nPermissionError: [Errno 1]")
        return subprocess.CompletedProcess(argv, 0, "", "")


class FencedApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p0269a-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.view = State(pathlib.Path(self.tmp.name) / "fenced-view")
        self.view.root.mkdir()
        for p in (patch.object(update, "install_kind", return_value={"kind": "uv", "where": "/fake", "legacy": False}),
                  patch.object(update, "new_argv", return_value=["agentj"]),
                  patch.object(update, "writable", return_value=True),
                  patch.object(update, "rollback_wheel", return_value=None),
                  patch.object(update, "roll_back", return_value=False),
                  patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "aj.p0269.test"})):
            p.start()
            self.addCleanup(p.stop)

    def apply(self, run, fenced=True):
        return update.apply(self.view, "9.9.9", plan_fn=lambda st, info, target: update.commands(info, target),
                            check_fn=lambda: {"status": "newer", "latest": "9.9.9"}, run=run, svc_on=False, fenced=fenced)

    def test_fenced_unparsable_doctor_is_unverifiable(self):
        with patch.object(update, "host_channel", return_value=False):
            res = self.apply(Runner())
        self.assertEqual((res["result"], res["reason"], res["exit"], res["health"]), ("ok", "unverifiable_in_fence", 0, "unverifiable"))
        self.assertIn(update.DOCTOR_UNPARSABLE, res["pending_checks"])
        self.assertIn("UPGRADE_RESULT ok", update.result_block(res))

    def test_unfenced_unparsable_still_fails(self):
        res = self.apply(Runner(), fenced=False)
        self.assertEqual((res["result"], res["reason"], res["failed_checks"]), ("failed", "doctor_failed", [update.DOCTOR_UNPARSABLE]))

    def test_fenced_real_failure_still_fails(self):
        """A parsable fenced doctor with a real ✗ (the version row) is still a failure — only 'could not see' is excused."""
        bad = json.dumps({"checks": [{"id": "version", "status": "fail", "summary": "", "hint": ""}]})
        with patch.object(update, "host_channel", return_value=False):
            res = self.apply(Runner(doctor_rc=1, doctor_out=bad))
        self.assertEqual((res["result"], res["reason"]), ("failed", "doctor_failed"))

    def test_host_channel_decides(self):
        """With the host channel the host's doctor answers; a host that cannot answer → unverifiable (unchanged)."""
        with patch.object(update, "host_channel", return_value=True), \
             patch.object(update, "_host", side_effect=lambda st, op, t=60, **kw: (
                 {"ok": True, "checks": [{"id": "serve", "status": "fail", "summary": "", "hint": ""}]} if op == "health" else {"ok": True})):
            res = self.apply(Runner())
        self.assertEqual((res["result"], res["reason"], res["failed_checks"]), ("failed", "doctor_failed", ["serve"]))


if __name__ == "__main__":
    unittest.main()
