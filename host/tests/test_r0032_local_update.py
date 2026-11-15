"""R-0032: `agentj update apply --wheel` stages an owner wheel and runs the /update job unchanged."""
import _hermetic  # noqa: F401
import json
import os
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock

from agentj import local_update, phone_update, update
from agentj.state import State


def wheel(folder, version, name=None):
    p = pathlib.Path(folder) / (name or f"agentj-{version}-py3-none-any.whl")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("agentj/__init__.py", f'__version__ = "{version}"\n')
    return p


class LocalUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name) / "state"
        root.mkdir(mode=0o700)
        self.st = State(root)
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX", "AGENTJ_FENCED"))}
        self.env = mock.patch.dict(os.environ, clean, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_stage_copies_private_and_checks_version(self):
        src = wheel(self.tmp.name, "0.30.23rc0")
        with mock.patch("agentj.fence.inside", return_value=False):
            dst = local_update.stage(self.st, str(src), "0.30.23rc0")
            self.assertEqual(dst.parent, self.st.root / local_update.FOLDER)
            self.assertEqual(dst.stat().st_mode & 0o777, 0o400)
            with self.assertRaises(local_update.Refused):
                local_update.stage(self.st, str(src), "0.30.24")        # name/version mismatch
            other = pathlib.Path(self.tmp.name) / "x"
            other.mkdir()
            lie = wheel(other, "0.30.22", name="agentj-0.30.23rc0-py3-none-any.whl")
            with self.assertRaises(local_update.Refused):
                local_update.stage(self.st, str(lie), "0.30.23rc0")     # file name says one version, content another

    def test_refused_inside_fence_and_from_agent_env(self):
        src = wheel(self.tmp.name, "0.30.23rc0")
        with mock.patch("agentj.fence.inside", return_value=True), self.assertRaises(local_update.Refused):
            local_update.stage(self.st, str(src), "0.30.23rc0")
        with mock.patch("agentj.fence.inside", return_value=False), mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), \
                self.assertRaises(local_update.Refused):
            local_update.stage(self.st, str(src), "0.30.23rc0")

    def test_apply_fn_is_update_apply_with_local_plan(self):
        seen = {}

        def fake_apply(st, target, *, check_fn, plan_fn, **kw):
            seen.update(target=target, check=check_fn(), plan=plan_fn(st, {"kind": "uv", "where": "/x"}, target), kw=kw)
            return {"result": "ok"}
        w = pathlib.Path("/s/agentj-0.30.23rc0-py3-none-any.whl")
        with mock.patch.object(update, "apply", fake_apply), \
                mock.patch("agentj.auto_update.install_commands", lambda info, path: [["uv", "tool", "install", str(path)]]):
            local_update.apply_fn(w, "0.30.23rc0")(self.st, svc_on=False, run=None)
        self.assertEqual(seen["target"], "0.30.23rc0")
        self.assertEqual(seen["check"]["status"], "newer")
        self.assertEqual(seen["check"]["latest"], "0.30.23rc0")
        self.assertEqual(seen["plan"], [["uv", "tool", "install", str(w)]])
        self.assertEqual(seen["kw"], {"svc_on": False, "run": None})

    def test_launch_uses_the_update_job_and_reserves(self):
        calls = []
        with mock.patch("agentj.service_recovery.launch_job", lambda st, argv, purpose, extra_env=None: calls.append((argv, purpose, extra_env))):
            local_update.launch(self.st, self.st.root / local_update.FOLDER / "w.whl", "0.30.23rc0")
            self.assertEqual(calls[0][0][1:], ["-m", "agentj.local_update"])
            self.assertEqual(calls[0][1], "update")
            self.assertEqual(calls[0][2][local_update.TARGET_ENV], "0.30.23rc0")
            with self.assertRaises(local_update.Refused):           # a second one while the first holds the lock
                local_update.launch(self.st, self.st.root / local_update.FOLDER / "w.whl", "0.30.23rc0")

    def test_job_refuses_a_wheel_outside_its_folder(self):
        with mock.patch.dict(os.environ, {local_update.WHEEL_ENV: "/tmp/evil.whl", local_update.TARGET_ENV: "0.30.23rc0",
                                          "AGENTJ_STATE_DIR": str(self.st.root)}), \
                mock.patch("agentj.state.State", lambda *a: self.st), mock.patch.object(phone_update, "run") as run:
            local_update.main()
        run.assert_not_called()
        self.assertEqual(json.loads((self.st.root / phone_update.RESULT).read_text())["reason"], "bad_local_job")


if __name__ == "__main__":
    unittest.main()
