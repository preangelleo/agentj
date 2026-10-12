"""P165 (support 20261012-220807, P186 acceptance): `agentj capability prepare --workflow X` refused `unknown` with no clue when X
was created by `agentj wizard apply --dir <another folder>`. The refusal now names the working root it looked in, and the CLI adds
a hint when the caller's own folder holds the workflow."""
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import capability


class Elsewhere(unittest.TestCase):
    def test_plan_card_names_the_root(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(capability.CapError) as e:
                capability.plan_card(None, {"working_root": root}, "competitor-brief", [])
            self.assertEqual(e.exception.why, "unknown")
            self.assertIn(str(pathlib.Path(root).resolve()), e.exception.detail)

    def test_cli_hint_when_the_workflow_is_in_the_callers_folder(self):
        with tempfile.TemporaryDirectory() as work:
            wf = pathlib.Path(work) / "competitor-brief"
            wf.mkdir()
            (wf / "brief.json").write_text("{}")
            cwd = os.getcwd()
            os.chdir(work)
            try:
                with mock.patch.object(capability, "client", return_value={"result": "refused", "why": "unknown",
                                                                           "detail": "no workflow competitor-brief"}), \
                        mock.patch("builtins.print") as out:
                    rc = capability.cmd_capability(mock.Mock(cap_cmd="prepare", workflow="competitor-brief", asks=None, json=True))
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 2)
            res = json.loads(out.call_args[0][0])
            self.assertIn("hint", res)
            self.assertIn("wizard apply", res["hint"])
            self.assertIn(pathlib.Path(work).name, res["hint"])

    def test_no_hint_when_nothing_is_here(self):
        self.assertEqual(capability._elsewhere_hint("does-not-exist-p165"), {})


if __name__ == "__main__":
    unittest.main()
