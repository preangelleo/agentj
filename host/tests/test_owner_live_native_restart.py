"""Owner live regression: restored launch trust reaches the completion's system source."""
import _hermetic  # noqa: F401
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import background_done, provenance
from agentj.busy import Board

SCOPE = "shared-claude"
SID = "owner-session"
TID = "native-job"
KEY = f"{SCOPE}:{SID}:{TID}"


class NativeRestart(unittest.TestCase):
    def restore(self, root, saved, trusted=True, sid=SID, tid=TID):
        path = root / "busy/native-launches.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved))
        b = Board(root)
        b.trusted = lambda: trusted
        b.chain = lambda: "current-turn-must-not-be-inherited"
        b.hook_event({"hook_event_name": "Stop", "session_id": sid, "background_tasks": [
            {"id": tid, "type": "shell", "status": "running", "description": "fixture"}]}, scope=SCOPE)
        return b, f"{SCOPE}:{sid}:{tid}"

    def notifier(self, root, b):
        a = SimpleNamespace(status="idle", q=asyncio.Queue(), cur_send=None, cur_cmd=None,
                            is_down=lambda: False, submit=mock.Mock())
        h = SimpleNamespace(st=SimpleNamespace(root=root, log=mock.Mock()), busy=b, preferences={}, lang="en", agent=a,
                            stopped=lambda: False, _open_question=lambda: False, asks={}, hist_add=lambda *a: {"id": 1})
        n = background_done.Notifier(h)
        n.ready = lambda: True
        return n

    def test_persisted_owner_launch_survives_stop_reconcile_and_notifies_as_system(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = Board(root)
            live.trusted = lambda: True
            live.start(KEY, None, session=f"{SCOPE}:{SID}")
            self.assertIs(json.loads((root / "busy/native-launches.json").read_text())[KEY], False)
            b, key = self.restore(root, json.loads((root / "busy/native-launches.json").read_text()))
            self.assertIs(b.mem[key].get("foreign"), False)
            self.assertNotIn("chain", b.mem[key])
            b.stop(key, exit_code=0)
            n = self.notifier(root, b)
            send = n.tick()
            self.assertEqual(provenance.normalize(send.source)["kind"], "system")
            self.assertFalse(hasattr(send, "owner_chain"))

    def test_unknown_group_malformed_other_session_and_task_are_never_promoted(self):
        cases = [({}, SID, TID), ({KEY: True}, SID, TID), ({KEY: 0}, SID, TID), ({KEY: None}, SID, TID),
                 ({KEY: "false"}, SID, TID), ({KEY: False}, "another-session", TID),
                 ({KEY: False}, SID, "another-task")]
        for saved, sid, tid in cases:
            with self.subTest(saved=saved, sid=sid, tid=tid), tempfile.TemporaryDirectory() as td:
                b, key = self.restore(Path(td), saved, sid=sid, tid=tid)
                self.assertIs(b.mem[key].get("foreign"), True)
                self.assertNotIn("chain", b.mem[key])

    def test_current_unknown_turn_does_not_erase_historical_owner_launch(self):
        with tempfile.TemporaryDirectory() as td:
            b, key = self.restore(Path(td), {KEY: False}, trusted=False)
            self.assertIs(b.mem[key].get("foreign"), False)
            self.assertNotIn("chain", b.mem[key])

    def test_new_external_launch_overwrites_old_owner_fact(self):
        with tempfile.TemporaryDirectory() as td:
            b, key = self.restore(Path(td), {KEY: False})
            b.mem.clear()
            b.trusted = lambda: False
            b.start(key, None, session=f"{SCOPE}:{SID}")
            self.assertIs(b.mem[key]["foreign"], True)
            self.assertIs(b.launched[key], True)
            b.start(key, None, session=f"{SCOPE}:{SID}", late=True)
            self.assertIs(b.mem[key]["foreign"], True)

    def test_timing_metadata_never_attests_source_and_invalid_timing_is_ignored(self):
        cases = [({}, {"since": 100, "generation": "a"*32}),
                 ({KEY: True}, {"since": 100, "generation": "a"*32}),
                 ({KEY: False}, {"since": "100", "generation": "a"*32}),
                 ({KEY: False}, {"since": True, "generation": "a"*32}),
                 ({KEY: False}, {"since": 100, "generation": "invalid"})]
        for saved, details in cases:
            with self.subTest(saved=saved, details=details), tempfile.TemporaryDirectory() as td:
                root=Path(td); p=root/"busy";p.mkdir()
                (p/"native-starts.json").write_text(json.dumps({KEY:details}))
                b,key=self.restore(root,saved,trusted=False)
                self.assertIs(b.mem[key]["foreign"], saved.get(KEY) is not False)
                if type(details['since']) is not int or details['generation']=='invalid':
                    self.assertNotEqual(b.mem[key]['since'],100)
                self.assertNotIn('chain',b.mem[key])


if __name__ == "__main__":
    unittest.main()
