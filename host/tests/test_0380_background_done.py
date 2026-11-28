"""Completion receipt, readback, and recovery backlog are independent."""
import _hermetic  # noqa: F401
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agentj import background_done, busy, compactprep, completion_recovery, provenance, serve


class Completion(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.agent = SimpleNamespace(status="idle", q=asyncio.Queue(), is_down=lambda: False)
        self.agent.submit = self.agent.q.put_nowait
        self.h = SimpleNamespace(st=SimpleNamespace(root=self.root, log=Mock()), lang="en",
                                 agent=self.agent, busy=busy.Board(self.root), stopped=lambda: False,
                                 _open_question=lambda: False, hist_add=Mock(return_value={"id": 1}),
                                 hist_update=Mock())
        self.h.background_done = background_done.Notifier(self.h)
        self.h.background_done_readback = lambda send: serve.Host.background_done_readback(self.h, send)

    def restart(self):
        self.h.background_done = background_done.Notifier(self.h)
        return self.h.background_done

    def receipt(self, trusted=False):
        job = busy.item("run:0123456789ab", "QA", "ceo", "QA completed", since=100,
                        report=str(self.root / "private-fixture.md"))
        if trusted:
            self.h.busy.finish({**job, "foreign": False}, 0, 125)
        else:
            busy.write_finished(self.root, job, 0)
            self.h.busy.refresh_outside(units=False)

    def test_full_recovery_book_survives_restart_and_confirms_notice(self):
        n = self.h.background_done
        for i in range(completion_recovery.MAX):
            n.recovery.add(f"{i:064x}", {"source": "unknown", "recovery_point": "reconcile_only"})
        before = n.recovery.path.read_bytes()
        self.agent.status = "working"
        self.receipt()
        self.assertIsNone(n.tick(now=1000))
        n = self.restart()
        key, = n.pending
        self.assertNotIn(key, n.recovery.rows)
        self.agent.status = "idle"
        send = n.tick(now=1000)
        self.assertIsNotNone(send, "a full recovery book must never swallow a completion")
        self.assertIs(self.agent.q.get_nowait(), send)
        self.assertIn("QA completed", send.text)
        self.assertNotIn("private-fixture.md", send.text)
        self.assertEqual(send.report_paths, [])
        self.assertFalse(getattr(send, "owner_chain", None))
        self.assertEqual(provenance.from_name(send.source), provenance.UNKNOWN)
        serve.Host.say_delivered(self.h, send)
        n = self.restart()
        self.assertIsNone(n.tick(now=1061), "a confirmed notice never repeats")
        self.assertEqual(n.recovery.path.read_bytes(), before)
        self.assertIn(key, n.pending, "overflow remains available for reconciliation")

    def test_uncertain_attempt_retries_metadata_after_restart(self):
        n = self.h.background_done
        self.receipt(trusted=True)
        send = n.tick(now=1000)
        self.assertIn("private-fixture.md", send.text)
        send.state = "uncertain"
        self.agent.q.get_nowait()
        serve.Host.say_uncertain(self.h, send)
        n = self.restart()
        retry = n.tick(now=1061)
        self.assertIsNotNone(retry)
        self.assertIn("QA completed", retry.text)
        self.assertNotIn("private-fixture.md", retry.text)
        self.assertEqual(retry.report_paths, [])
        self.assertEqual(provenance.from_name(retry.source), provenance.UNKNOWN)
        with patch.object(compactprep, "applies", return_value=True) as applies:
            self.assertEqual(compactprep.decorate(self.agent, retry), retry.text)
            applies.assert_not_called()
        self.assertIs(self.agent.q.get_nowait(), retry)
        serve.Host.say_delivered(self.h, retry)
        self.assertFalse(self.restart().pending)

    def test_transport_write_is_not_shared_claude_readback(self):
        n = self.h.background_done
        self.receipt(trusted=True)
        send = n.tick(now=1000)
        send.await_background_readback = True
        serve.Host.say_delivered(self.h, send)
        self.assertTrue(json.loads(n.path.read_text())["pending"])
        serve.Host.background_done_readback(self.h, send)
        self.assertFalse(json.loads(n.path.read_text())["pending"])
