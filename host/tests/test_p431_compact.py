"""P431 C5: compaction as a kernel step (summary in the session file, recent tail verbatim, files touched, one retry after a
context overflow, the handover file through a no-follow chain) and the native slash commands (/compact /context /cost
/usage /model /status) on the same session."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agentj.core import compact, llm, presets, prompt
from agentj.core.loop import Loop
from agentj.core.pipeline import Gate
from agentj.core.session import Session

SENTINEL = "P431" + "-synthetic-value-" * 3


def rep(text="", calls=(), usage=(10, 5), model="fake"):
    return {"text": text, "tool_calls": [{"id": f"c{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(calls)],
            "finish": None, "reasoning": None, "model": model, "usage": {"input": usage[0], "output": usage[1]}}


def convo(turns=6, pad=4000, say=0):
    """user → assistant(tool call read path_i) → tool output (big) → assistant answer, `turns` times."""
    out = []
    for i in range(turns):
        out += [{"role": "user", "content": f"question {i}"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": f"t{i}", "type": "function", "function": {"name": "read", "arguments": json.dumps({"path": f"src/f{i}.py"})}}]},
                {"role": "tool", "tool_call_id": f"t{i}", "content": "x" * pad},
                {"role": "assistant", "content": f"answer {i} " + "y" * say}]
    return out


class Kernel(unittest.TestCase):
    def setUp(self):
        self.p = replace(presets.resolve("agentsrelay", environ={}), context=30000)

    def test_cut_keeps_a_recent_tail_at_a_user_turn(self):
        msgs = convo(8, pad=8000)
        i = compact.cut(msgs, keep=6000)
        self.assertGreater(i, 0)
        self.assertEqual(msgs[i]["role"], "user", "never between a tool call and its result")
        self.assertEqual(compact.cut(convo(1)), 0, "one turn: nothing to cut")
        self.assertEqual(compact.files_touched(convo(3)), ["src/f0.py", "src/f1.py", "src/f2.py"])
        patch = {"role": "assistant", "tool_calls": [{"id": "p", "function": {"name": "apply_patch", "arguments": json.dumps(
            {"patch": "*** Begin Patch\n*** Update File: a.py\n*** Move to: b.py\n@@\n-x\n+y\n*** Add File: c.md\n+z\n*** End Patch"})}}]}
        self.assertEqual(compact.files_touched([patch]), ["a.py", "b.py", "c.md"])

    def test_auto_compaction_summary_session_reload_and_usage(self):
        with tempfile.TemporaryDirectory() as d:
            sess = Session(Path(d) / "s.jsonl")
            for m in convo(6, pad=6000, say=12000):
                sess.append(m)
            seen, handed = [], []

            def call(p, msgs, specs, environ=None):
                seen.append((msgs, specs))
                if specs is None and msgs[0]["content"] == compact.SYSTEM:
                    self.assertIn("question 0", msgs[1]["content"])
                    return rep("## Owner's words\nkeep going " + "ghp_" + "Ab3" * 12, usage=(900, 80))
                return rep("final", usage=(1000, 7))
            lp = Loop(preset=self.p, system="rules", specs=[], gate=Gate(d), session=sess, call=call,
                      on_compact=lambda t, f: handed.append((t, f)))
            r = asyncio.run(lp.run("next"))
            self.assertEqual(r["text"], "final")
            self.assertEqual(len(seen), 2, "one summary call, then the step")
            self.assertTrue(lp.msgs[0]["content"].startswith(compact.MARK))
            self.assertIn("src/f0.py", lp.msgs[0]["content"])
            self.assertNotIn("Ab3Ab3", lp.msgs[0]["content"], "the summary is redacted")
            self.assertLessEqual(compact.size("rules", lp.msgs), lp._limit())
            again = sess.load()
            self.assertEqual(again, lp.msgs, "loading the session IS the compacted conversation")
            self.assertEqual(handed[0][1][:2], ["src/f0.py", "src/f1.py"])
            self.assertEqual(sess.usage(), {"fake": [1900, 87]}, "the summary call is spent too")
            sess.clear()
            self.assertEqual(sess.usage(), {})

    def test_context_overflow_compacts_once_and_retries(self):
        with tempfile.TemporaryDirectory() as d:
            msgs = convo(4, pad=100)
            n = []

            def call(p, m, specs, environ=None):
                n.append(specs)
                if specs is None:
                    return rep("summary")
                if len(n) == 1:
                    raise llm.LLMError("context", 400, "maximum context length exceeded")
                return rep("ok")
            p = replace(self.p, context=1_000_000)
            lp = Loop(preset=p, system="s", specs=[{"type": "function", "function": {"name": "ls", "parameters": {}}}],
                      gate=Gate(d), call=call)
            lp.msgs = list(msgs)
            self.assertEqual(asyncio.run(lp.run("go"))["text"], "ok")
            self.assertEqual([s is None for s in n], [False, True, False])

            def always(p, m, specs, environ=None):
                if specs is None:
                    return rep("summary")
                raise llm.LLMError("context", 400, "too long")
            lp2 = Loop(preset=p, system="s", specs=[{}], gate=Gate(d), call=always)
            lp2.msgs = list(msgs)
            with self.assertRaises(llm.LLMError):
                asyncio.run(lp2.run("go"))
            lp3 = Loop(preset=p, system="s", specs=[{}], gate=Gate(d), call=always)
            with self.assertRaises(llm.LLMError):          # nothing to compact: the error goes up as before
                asyncio.run(lp3.run("go"))

    def test_breakdown_parts(self):
        b = compact.breakdown("sys", [{"x": 1}], convo(2) + [compact.summary_message("s", [], "w")])
        self.assertEqual(b["total"], sum(v for k, v in b.items() if k != "total"))
        self.assertGreater(b["tool_output"], b["assistant"])
        self.assertGreater(b["summary"], 0)


class Commands(unittest.TestCase):
    def setUp(self):
        from agentj import agent
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.work = Path(t.name) / "w"
        self.work.mkdir()
        root = Path(t.name) / "state"

        class St:
            sessions = {}

            def __init__(self):
                self.root = root

            def agent_session(self, k):
                return self.sessions.get(k)

            def set_agent_session(self, k, v):
                self.sessions[k] = v

            def log(self, *a, **k):
                pass

        class Host:
            st = St()
            meters, models = [], []

            def meter_update(self, **kw):
                self.meters.append(kw)

            def set_model(self, m):
                self.models.append(m)
        self.h = Host()
        with mock.patch("agentj.main_identity.working_root", lambda cfg: Path(cfg["dir"])):
            self.a = agent.make(self.h, {"kind": "core", "dir": str(self.work), "model": "agentsrelay", "fence": False})
        self.env = mock.patch.dict(os.environ, {"AGENTSRELAY_OPENAI_KEY": SENTINEL})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ident = mock.patch("agentj.main_identity.prompt", lambda cfg, k=None: "identity")
        self.ident.start()
        self.addCleanup(self.ident.stop)

    def run_cmd(self, name, arg=""):
        return asyncio.run(self.a.command(name, arg))

    def seed(self):
        sess = self.a._session()
        for m in convo(6, pad=30000):
            sess.append(m)
        sess.add_usage("agentsrelay/gpt-5.4", 1200, 300)
        return sess

    def test_compact_writes_the_handover_and_keeps_the_tail(self):
        self.assertEqual(self.run_cmd("compact").kind, "info", "no conversation yet")
        sess = self.seed()
        with mock.patch.object(llm, "chat", lambda p, m, s, environ=None: rep("## Done\nall", model=p.name)):
            r = self.run_cmd("compact", "focus on tests")
        self.assertEqual(r.kind, "ok", r.text)
        self.assertIn("已压缩", r.text)
        f = self.work / ".agentj" / "handover" / "compact.md"
        self.assertIn("## Done", f.read_text())
        self.assertIn("src/f0.py", f.read_text())
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(sess.load()[0]["content"].startswith(compact.MARK))
        self.assertIn({"ctx": None}, self.h.meters, "no estimate on the meter after a compaction")

    def test_handover_never_follows_a_planted_link(self):
        self.seed()
        outside = self.work.parent / "outside"
        outside.mkdir()
        (self.work / ".agentj").mkdir(mode=0o700)
        os.symlink(outside, self.work / ".agentj" / "handover")
        with mock.patch.object(llm, "chat", lambda p, m, s, environ=None: rep("summary")):
            r = self.run_cmd("compact")
        self.assertEqual(r.kind, "ok")
        self.assertNotIn("交接写在", r.text)
        self.assertEqual(list(outside.iterdir()), [])

    def test_context_cost_usage_status(self):
        self.seed()
        c = self.run_cmd("context")
        for part in ("系统提示", "工具定义", "工具输出", "agentsrelay/gpt-5.4"):
            self.assertIn(part, c.text)
        self.assertIn("1.2k", self.run_cmd("cost").text)
        self.assertIn("金额以服务商账单为准", self.run_cmd("cost").text)
        self.assertIn("输入 1.2k", self.run_cmd("usage").text)
        self.assertIn("隔离", self.run_cmd("status").text)

    def test_model_lists_keyed_presets_and_switches(self):
        r = self.run_cmd("model")
        ids = [m["id"] for m in r.models]
        self.assertIn("agentsrelay/gpt-5.4", ids)
        self.assertTrue(all(i.split("/")[0] in ("agentsrelay", "agentsrelay-responses") for i in ids), "only keyed providers")
        self.assertTrue(any(m["cur"] for m in r.models))
        self.assertEqual(self.run_cmd("model", "agentsrelay/gpt-6.1-sol").kind, "ok")
        self.assertEqual(self.h.models[-1], "agentsrelay/gpt-6.1-sol")
        self.assertEqual(self.run_cmd("model", "nope/x").kind, "error")
        r = self.run_cmd("model", "anthropic")
        self.assertEqual(r.kind, "error")
        self.assertIn("ANTHROPIC_API_KEY", r.text)
        self.assertNotIn(SENTINEL, json.dumps([m for m in self.h.meters]))


if __name__ == "__main__":
    unittest.main()
