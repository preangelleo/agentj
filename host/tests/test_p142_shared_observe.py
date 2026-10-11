"""P142 item 5a (B17): the shared Claude observe loop no longer goes down for good on one OSError (shared.py observe).
A transient error is counted and retried with backoff (`shared_observe_retry`); OBSERVE_RETRIES in a row → down
(`shared_observe_down`); a later pass that succeeds brings it back (`shared_observe_recovered`)."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentj import diskq, shared
from agentj.state import State

_sleep = asyncio.sleep


class H:
    def __init__(self, root):
        self.st = State(Path(root))
        self.st.root.mkdir(parents=True, exist_ok=True)
        self.statuses = []

    def agent_status(self, s):
        self.statuses.append(s)


class Observe(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, failures, passes):
        d = tempfile.mkdtemp(prefix='aj-p142obs-')
        self.addCleanup(shutil.rmtree, d, True)
        h = H(Path(d) / 'st')
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": "/nonexistent", "_workflow_ceo": True, "language": "zh"})
        a.session = {"sessionId": "x"}
        a.transcript = Path(d) / 't.jsonl'
        a.transcript.write_text('')
        a.status = 'idle'
        calls = {'n': 0}

        def read():
            calls['n'] += 1
            if calls['n'] <= failures:
                raise OSError(5, 'EIO')

        async def fast(_s):
            await _sleep(0)
        with patch.object(a, 'read_transcript', read), patch.object(a, 'read_statusline', lambda: None), \
                patch.object(a, 'sync_compacting', lambda: None), patch.object(shared.asyncio, 'sleep', fast):
            task = asyncio.ensure_future(a.observe())
            while calls['n'] < failures + passes:
                await _sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await diskq.aflush()
        evs = [json.loads(x)['ev'] for x in h.st.log_path.read_text().splitlines()] if h.st.log_path.exists() else []
        return a, h, evs

    async def test_transient_error_is_retried_not_down(self):
        a, h, evs = await self.run_with(failures=2, passes=3)
        self.assertNotIn('down', h.statuses)
        self.assertEqual(evs.count('shared_observe_retry'), 2)
        self.assertEqual(a._observe_errors, 0)
        self.assertFalse(a.failed_start)

    async def test_persistent_error_goes_down_then_recovers(self):
        a, h, evs = await self.run_with(failures=shared.OBSERVE_RETRIES + 2, passes=2)
        self.assertEqual(h.statuses, ['down', 'idle'])
        self.assertEqual(evs.count('shared_observe_down'), 1)
        self.assertIn('shared_observe_recovered', evs)
        self.assertFalse(a.failed_start)


if __name__ == '__main__':
    unittest.main()
