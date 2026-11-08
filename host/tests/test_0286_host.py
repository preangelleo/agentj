"""0286: bounded diagnostics, neutral reports and original trigger input projections."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentj import diagnostic_log, history, hooks, notify, provenance, serve
from agentj.state import State
from test_p366_hooks import svc


class Diagnostics(unittest.TestCase):
    def test_correlation_survives_complex_fields_exhausting_node_budget(self):
        fields = {"action": [[["x"] * 16] * 16] * 16, "status": "failed", "cid": "case-0286", "id": "job-0286"}
        row = diagnostic_log.record("critical_failure", fields, State.LOG_FIELDS)
        self.assertEqual({k: row[k] for k in ("status", "cid", "id")}, {k: fields[k] for k in ("status", "cid", "id")})
        self.assertEqual(diagnostic_log.record("failure", {**fields, "status": fields["action"]}, State.LOG_FIELDS)["cid"], "case-0286")

    def test_bounded_before_serializer_and_no_object_hooks(self):
        class Trap:
            def __repr__(self):
                raise AssertionError("repr must not run")
            def __deepcopy__(self, memo):
                raise AssertionError("deepcopy must not run")
        data = {"reason": "x" * (10 * 1024 * 1024), "count": 12, "event": Trap(),
                "events": {"inner": [Trap()] * 100000}, "ignored": Trap()}
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            st = State(Path(tmp))
            captured = []
            original = json.dumps
            def checked(obj, **kw):
                raw = original(obj, **kw)
                self.assertLess(len(raw), 65536)
                self.assertNotIn("ignored", obj)
                return raw
            with patch("agentj.state.json.dumps", side_effect=checked), patch("agentj.state.diskq.append", side_effect=lambda p, b: captured.append((p, b))):
                st.log("diagnostic", **data)
            row = json.loads(captured[0][1])
            self.assertEqual(captured[0][0], st.log_path)
            self.assertEqual(row["count"], 12)
            self.assertTrue(row["diagnostic_truncated"])
            self.assertFalse(st.approvals_path.exists())

    def test_cycles_depth_count_huge_integer_and_unicode(self):
        cycle = {}; cycle["self"] = cycle
        row = diagnostic_log.record("event", {"events": [cycle] * 100000, "code": 1 << 100000,
                                              "reason": "雪 " * 1000000}, State.LOG_FIELDS)
        self.assertTrue(row["diagnostic_truncated"])
        self.assertLess(len(json.dumps(row)), 65536)

    def test_secrets_in_values_keys_events_and_cutoff_are_removed(self):
        secret = "sk-" + "proj-" + "Ab3Cd4Ef5Gh6Ij7Kl8Mn9Op0" * 3
        boundary = "safe " * 203 + secret
        row = diagnostic_log.record(secret, {"events": {secret: secret, "password": "tiny", "nested": [secret]},
                                              "reason": boundary}, State.LOG_FIELDS)
        text = json.dumps(row)
        self.assertNotIn(secret, text)
        self.assertNotIn("tiny", text)
        self.assertNotIn("sk-proj-", text)
        self.assertIn("redacted", text)

    def test_normal_metadata_preserved_and_disallowed_dropped(self):
        self.assertEqual(diagnostic_log.record("relay_down", {"code": 1006, "reason": "network", "body": "private"}, State.LOG_FIELDS),
                         {"ev": "relay_down", "code": 1006, "reason": "network"})

    def test_sustained_deltas_with_stalled_writer_keep_existing_queue_budget(self):
        from agentj import diskq
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            st = State(Path(tmp)); key = str(st.log_path)
            with diskq._llk:
                diskq._writing.add(key)  # this file's writer is deliberately stalled, even if another test started it
            async def load():
                with patch.object(diskq, "_start_log_thread"), patch.object(diskq, "_guard_loop"), \
                        patch.object(diskq, "_buffer_until", float("inf")):
                    for i in range(5000):
                        st.log("diagnostic_delta", count=i, reason="readable " * 200)
                with diskq._llk:
                    self.assertLessEqual(len(diskq._pending[key]), diskq.LOG_MAX_LINES)
                    self.assertLessEqual(diskq._pbytes[key], diskq.LOG_MAX_BYTES)
                    self.assertGreater(diskq._dropped.get(key, 0), 0)
                    self.assertTrue(all(len(raw) < 65536 for _, raw in diskq._pending[key]))
            try:
                asyncio.run(load())
            finally:
                with diskq._llk:
                    diskq._writing.discard(key)
                    for mapping in (diskq._pending, diskq._pbytes, diskq._dropped, diskq._seq, diskq._done):
                        mapping.pop(key, None)


class Reports(unittest.TestCase):
    def test_neutral_report_defaults_and_schema(self):
        h = hooks.build({"preset": "automation-report", "name": "Nightly build"}, "owner")
        self.assertEqual((h["target"], h["life"], h["auth"]["kind"]), ("main", "resident", "bearer"))
        schema = json.loads(Path(hooks.__file__).with_name("hooks.schema.json").read_text())
        self.assertIn("automation-report", schema["properties"]["preset"]["enum"])
        for status in ("success", "failure", "partial", "custom"):
            rec = {"id": "d012345678901", "hook": h["id"], "enc": "utf8", "body": json.dumps({"status": status}), "headers": {}}
            for lang in ("zh", "en"):
                envelope = hooks.envelope(lang, h, rec, "local")
                self.assertIn(status, envelope)
                self.assertIn("FYI", envelope)
                self.assertNotIn("With nothing to add", envelope)
                self.assertNotIn("没有要补充的", envelope)
            self.assertEqual(hooks.input_source(h, rec)["text"], rec["body"])

    def test_hook_input_is_complete_privacy_filtered_and_not_a_reply(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            host, service = svc(tmp)
            h = hooks.build({"preset": "automation-report", "name": "Nightly build"}, "owner")
            rec = {"id": "d012345678901", "hook": h["id"], "enc": "utf8", "body": "success " * 900, "headers": {}}
            service._triage(h, rec, "local", False)
            src = host.pages[-1]
            self.assertEqual(src["text"], rec["body"])
            self.assertEqual(src["name"], "Nightly build")
            self.assertEqual(src["via"], "hook")
            send = host.agent.sent[-1]
            self.assertTrue(send.hook_notice)
            self.assertEqual(send.source, provenance.system())
            self.assertIsNone(provenance.unattended_turn(SimpleNamespace(cur_send=send)))

    def test_notify_uses_one_input_turn_and_real_receipt_not_external_reply(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            st = State(Path(tmp)); hist = history.History(st, on=False); sent = []
            def add(src, reply="", end="done"):
                return hist.add(src, reply, end)
            host = SimpleNamespace(st=st, preferences={"notify": {"enabled": True, "automations": [{"id": "build", "allow": True}]}},
                stopped=lambda: False, hist_add=add, hist_update=hist.update, sessions={}, push_notify=lambda _: None,
                _remember=lambda *a: {}, _post=lambda *a: None, _send_legacy=lambda *a: None,
                agent=SimpleNamespace(kind="codex", submit=sent.append), lang="zh")
            with patch.object(notify, "prune"):
                result = asyncio.run(notify.post(host, {"from": "build", "title": "Build", "text": "status: success", "files": []}, None))
            self.assertEqual(result["agent"], "queued")
            self.assertEqual(len(hist.turns), 1)
            turn = hist.get(sent[0].turn)
            self.assertIn("status: success", turn["src"]["text"])
            self.assertEqual(turn["reply"]["text"], "")
            self.assertGreater(turn["ts"], 0)
            self.assertEqual(sent[0].source, provenance.automation("build"))
            self.assertIn("FYI", sent[0].text)
            self.assertNotIn("没有要补充的", sent[0].text)
            self.assertNotIn("不要先写过程说明", sent[0].text)
            hist.update(turn["id"], append="Build finished. FYI, no action needed.", end="done")
            reloaded = json.loads(json.dumps(turn))
            self.assertEqual(reloaded["src"], turn["src"])
            self.assertIn("FYI", reloaded["reply"]["text"])

    def test_native_loop_input_display_does_not_grant_authority(self):
        host = SimpleNamespace(proactive_via="loop", proactive_input="ScheduleWakeup: inspect yesterday's run")
        self.assertEqual(serve.Host._agent_src(host)["text"], host.proactive_input)
        host.proactive_via = None
        self.assertEqual(serve.Host._agent_src(host)["text"], "")


if __name__ == "__main__":
    unittest.main()
