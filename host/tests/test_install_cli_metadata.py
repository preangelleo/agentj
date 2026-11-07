import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import cli, first_run, latency

class MetadataCLI(unittest.TestCase):
    def test_doctor_latency_reads_only_scratch_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            st = SimpleNamespace(root=Path(td))
            args = SimpleNamespace(latency=True, json=True)
            output = io.StringIO()
            with patch.object(cli, 'State', return_value=st), contextlib.redirect_stdout(output):
                cli.cmd_doctor(args)
            result = json.loads(output.getvalue())
            self.assertEqual(result['turns'], 0)
            self.assertIsNone(result['metrics']['ttft_first']['p95'])
            self.assertEqual(list(Path(td).iterdir()), [])
    def test_guide_does_not_create_missing_journal(self):
        with tempfile.TemporaryDirectory() as td:
            st = SimpleNamespace(root=Path(td))
            output = io.StringIO()
            with patch('agentj.state.State', return_value=st), contextlib.redirect_stdout(output):
                self.assertEqual(first_run.cmd(SimpleNamespace(guide=True,json=True)), 0)
            self.assertEqual(json.loads(output.getvalue())['start'], 'agentj setup checklist --resume')
            self.assertEqual(list(Path(td).iterdir()), [])
