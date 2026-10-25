"""P515: wrapper ancestry and the real systemd-run → run-px.sh → Codex exec chain.
The opt-in live test holds stdin open in an empty HOME; no model request or user state.
"""
import _hermetic
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
import test_p508_busy_provenance as fixtures
from agentj import busy, busy_probe as probe


class WrapperChain(unittest.TestCase):
    setUp = fixtures.Provenance.setUp
    add = fixtures.Provenance.add
    def test_systemd_bash_pipeline_node_launcher_native_codex(self):
        self.add(10, 1, 'bash', ['bash', '/fixture/run-px.sh', '515', 'fixture', 'codex'])
        self.add(11, 10, 'node', ['node', '/fixture/codex.js', 'exec', '-m', 'gpt-6.1-sol', '-'])
        self.add(12, 11, 'codex', ['codex', 'exec', '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort=medium', '-'])
        b=busy.Board(None,verify_sources=True)
        b.outside=[busy.item('cli:fixture','Fixture','ceo','Task',pid=10)|{'proc_start':'123'}]
        b.refresh_sources(probe.Processes(self.proc))
        row=b.msg()['tasks'][0]
        self.assertEqual((row['detected']['harness'],row['detected']['model'],row['detected']['effort']),('codex','gpt-6.1-sol','medium'))
        self.assertEqual(row['live_pids'],[11,12])
        self.assertFalse(row['conflict'])


class RealSystemdChain(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('P515_REAL_CHAIN')=='1','opt-in host user-manager evidence')
    def test_real_systemd_run_px_codex_exec(self):
        located=subprocess.run(['mise','where','codex'],capture_output=True,text=True,timeout=5)
        self.assertEqual(located.returncode,0,'installed Codex must be available')
        executable=Path(located.stdout.strip())/'bin/codex'
        unit='agentj-p515-probe-'+uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory(prefix='p515-chain-') as folder:
            root=Path(folder);fifo=root/'input';os.mkfifo(fifo)
            # The production launcher's same bash pipeline and codex exec argument positions.
            # Do not execute its real prompt, logs, credentials or repository.
            script=root/'run-px.sh'
            script.write_text('set -eu\ncat "$1" | "$2" exec -C "$3" --skip-git-repo-check -m gpt-6.1-sol -c model_reasoning_effort=medium - >/dev/null 2>&1\n')
            try:
                result=subprocess.run(['systemd-run','--user','--quiet','--unit',unit,
                    '--setenv=HOME='+folder,'--setenv=CODEX_HOME='+folder,
                    'bash',str(script),str(fifo),str(executable),folder],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,'temporary unit must start')
                found=None
                for _ in range(100):
                    result=subprocess.run(['systemctl','--user','show',unit,'-p','MainPID','--value'],capture_output=True,text=True,timeout=5)
                    pid=int(result.stdout.strip() or '0')
                    if pid:
                        found=probe.fields({'pid':pid},probe.Processes())
                        if found and found['harness']=='codex':break
                    time.sleep(.1)
                self.assertIsNotNone(found)
                self.assertEqual((found['harness'],found['model'],found['detected']['effort']),('codex','gpt-6.1-sol','medium'))
                self.assertIsNone(found['detected']['account'])
                self.assertIsNone(found['detected']['subscription'])
            finally:
                subprocess.run(['systemctl','--user','stop',unit],capture_output=True,timeout=10)
                subprocess.run(['systemctl','--user','reset-failed',unit],capture_output=True,timeout=10)
