"""0.30.73: replayed thinking that the prefix check would refuse is removed for good, not per request.

A thinking block of a model with preserved thinking is tied to everything sent before it (system, tools, earlier messages).
The kernel changes that prefix in four places; each is driven here through the real Loop, the real Session file and the real
request builder (`llm.chat` with a scripted transport), and the request bodies that leave are what is asserted:
1. trimming old tool outputs / compaction (manual, automatic) — thinking after the change is dropped in the session file;
2. the 400 `bound to a different conversation` (as a status or inside a 200 stream) — the recovery lasts for later rounds and
   turns; any other 400 about thinking is repeated once without the blocks and changes nothing that is stored;
3. the report round after a stop / budget / step limit keeps `tools` and sends `tool_choice: none`;
4. another model for a while, then the first one again — its still-valid blocks come back, its stale ones never do.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agentj.core import llm, presets, tools
from agentj.core.loop import Loop
from agentj.core.pipeline import Gate
from agentj.core.session import TRIMMED, Session

SENTINEL = "0373" + "-synthetic-value-" * 3
BOUND = ("messages.%d.content.0: Invalid `signature` in `thinking` block. The block is bound to a different conversation. "
         "Remove the block, or set `thinking.block_binding.prefix_mismatch_behavior` to \"drop_block\".")


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def reply(blocks, stop="end_turn"):
    ev = [{"type": "message_start", "message": {"type": "message", "role": "assistant", "content": [],
                                                 "usage": {"input_tokens": 5, "output_tokens": 1}}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}}]
        elif b["type"] == "thinking":
            ev += [{"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": ""}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": b["signature"]}}]
        else:
            ev += [{"type": "content_block_start", "index": i,
                    "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}},
                   {"type": "content_block_delta", "index": i,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}}]
        ev.append({"type": "content_block_stop", "index": i})
    return sse(ev + [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 2}},
                     {"type": "message_stop"}])


def refusal(n=5, kind="invalid_request_error"):
    """The provider's error object for the prefix-check refusal."""
    return {"type": "error", "error": {"type": kind, "message": BOUND % n}}


def think(sig):
    return {"type": "thinking", "thinking": "", "signature": sig}


def use(i, name="ls"):
    return {"type": "tool_use", "id": f"tu{i}", "name": name, "input": {"path": f"d{i}"}}


def step(model, i, sig=None, out="x"):
    """assistant (thinking + tool_use) → its tool output, as the Loop stores them."""
    blocks = ([think(sig)] if sig else []) + [use(i)]
    return [{"role": "assistant", "content": None, "tool_calls": [
                {"id": f"tu{i}", "type": "function", "function": {"name": "ls", "arguments": json.dumps({"path": f"d{i}"})}}],
             "_anthropic_blocks": blocks, "_anthropic_model": model},
            {"role": "tool", "tool_call_id": f"tu{i}", "content": out}]


def answer(model, text, sig=None):
    return {"role": "assistant", "content": text, "_anthropic_model": model,
            "_anthropic_blocks": ([think(sig)] if sig else []) + [{"type": "text", "text": text}]}


def sigs(body):
    """The thinking signatures of one request body, in order."""
    return [b["signature"] for m in body["messages"] if m["role"] == "assistant" for b in m["content"]
            if b.get("type") == "thinking"]


def results(body):
    return [b["content"] for m in body["messages"] if m["role"] == "user" for b in m["content"] if b.get("type") == "tool_result"]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.p = replace(presets.resolve("anthropic/claude-opus-5-5", environ={}), context=30000)
        self.other = replace(presets.resolve("anthropic/claude-sonnet-5-5", environ={}), context=30000)
        self.env = {self.p.key_env: SENTINEL}
        self.bodies = []
        self.sess = Session(self.d / "s.jsonl")

    def seed(self, msgs):
        for m in msgs:
            self.sess.append(m)

    def loop(self, script, preset=None, **kw):
        """A Loop on the session file whose model is `llm.chat` over a transport that answers from `script(n, body)`."""
        def transport(url, headers, body, timeout):
            self.assertNotIn(SENTINEL, body.decode())
            self.bodies.append(json.loads(body))
            return script(len(self.bodies), self.bodies[-1])

        async def ls(args):
            return "listing"
        return Loop(preset=preset or self.p, system="rules", specs=[tools.SPECS["ls"], tools.SPECS["read"]], gate=Gate(self.d),
                    session=self.sess, environ=self.env, extra={"ls": ls},
                    call=lambda *a, **k: llm.chat(*a, **k, transport=transport, sleep=lambda s: None), **kw)

    def ok(self, n=None, body=None):
        return 200, reply([{"type": "text", "text": "done"}])


class TrimAndCompact(Case):
    def big(self):
        """One step whose output was trimmed before, then five big outputs: the window (14k tokens) forces the oldest out."""
        m = self.p.name
        msgs = [{"role": "user", "content": "start"}] + step(m, 0, "s0", out=TRIMMED)       # cut in an earlier turn
        for i in range(1, 6):
            msgs += step(m, i, f"s{i}", out="x" * 16000)
        return msgs + [answer(m, "so far", "s6")]

    def test_trimming_drops_the_thinking_after_the_first_cut_and_the_session_keeps_that(self):
        self.seed(self.big())
        lp = self.loop(self.ok)
        self.assertEqual(asyncio.run(lp.run("next"))["text"], "done")
        b = self.bodies[0]
        out = results(b)
        self.assertEqual(out[:2], [TRIMMED, TRIMMED], "the oldest big output was trimmed now")
        self.assertEqual(sigs(b), ["s0", "s1"], "thinking produced before the first newly trimmed output stays; after it, none")
        self.assertEqual([x["type"] for x in b["messages"][3]["content"]], ["thinking", "tool_use"])
        again = self.sess.load()
        self.assertEqual(again, lp.msgs, "the trim and the dropped thinking are in the session file")
        self.assertEqual(again[4]["content"], TRIMMED)
        # the next turn is a new Loop on the file: the same prefix leaves again, byte for byte
        lp2 = self.loop(self.ok)
        asyncio.run(lp2.run("and then"))
        first, second = self.bodies[0]["messages"], self.bodies[1]["messages"]
        self.assertEqual(second[:len(first)], first, "the earlier messages are sent as they were sent before")
        self.assertEqual(sigs(self.bodies[1]), ["s0", "s1"])

    def test_auto_compaction_drops_the_kept_tail_thinking_for_good(self):
        m = self.p.name
        msgs = []
        for i in range(10):
            msgs += [{"role": "user", "content": f"question {i}"}] + step(m, i, f"s{i}", out="x" * 6000) \
                + [answer(m, f"answer {i} " + "y" * 4000, f"a{i}")]
        self.seed(msgs)
        p = replace(self.p, stream=False)

        def script(n, body):
            text = "## Done\nsummary" if n == 1 else "done"
            return 200, json.dumps({"type": "message", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                    "usage": {"input_tokens": 9, "output_tokens": 3}}).encode()
        lp = self.loop(script, preset=p)
        self.assertEqual(asyncio.run(lp.run("next"))["text"], "done")
        self.assertNotIn("tools", self.bodies[0], "the summary call is its own tool-less conversation")
        b = self.bodies[1]
        self.assertGreater(len(b["messages"]), 2, "a recent tail was kept")
        self.assertIn("tool_use", json.dumps(b["messages"]), "the kept tail still holds its tool round")
        self.assertEqual(sigs(b), [], "the kept turns' thinking followed messages that are gone")
        self.assertEqual(self.sess.load(), lp.msgs)
        self.assertNotIn('"thinking"', json.dumps(self.sess.load()))

    def test_manual_compact_and_a_compact_record_written_by_an_older_version(self):
        m = self.p.name
        msgs = []
        for i in range(3):
            msgs += [{"role": "user", "content": f"q{i}"}] + step(m, i, f"s{i}", out="x" * 30000) + [answer(m, f"a{i}", f"a{i}")]
        self.seed(msgs)
        lp = self.loop(lambda n, b: (200, reply([{"type": "text", "text": "## Done\nsummary"}])))
        r = asyncio.run(lp.compact())
        self.assertTrue(r and r["kept"] > 0)
        self.assertNotIn('"thinking"', json.dumps(lp.msgs))
        self.assertEqual(self.sess.load(), lp.msgs)
        # 0.30.72 and earlier wrote the same record and left the tail's thinking in the file
        old = Session(self.d / "old.jsonl")
        for x in msgs:
            old.append(x)
        old.compact({"role": "user", "content": "summary"}, 4)
        got = old.load()
        self.assertEqual(len(got), 5)
        self.assertNotIn('"thinking"', json.dumps(got))
        self.assertEqual(got[2]["_anthropic_blocks"], [use(2)], "every other block stays")


class Recovery(Case):
    def history(self):
        m = self.p.name
        return [{"role": "user", "content": "start"}] + step(m, 0, "s0") + [answer(m, "first", "s1"),
                                                                             {"role": "user", "content": "more"}] \
            + step(m, 2, "s2") + [answer(m, "second", "s3")]

    def refused(self, in_stream):
        err = refusal()

        def script(n, body):
            if n == 1:
                return (200, sse([err])) if in_stream else (400, json.dumps(err).encode())
            if n == 2:
                return 200, reply([think("s4"), use(9)], "tool_use")
            return self.ok()
        return script

    def check(self, in_stream):
        self.seed(self.history())
        lp = self.loop(self.refused(in_stream))
        self.assertEqual(asyncio.run(lp.run("next"))["text"], "done")
        self.assertEqual(sigs(self.bodies[0]), ["s0", "s1", "s2", "s3"])
        self.assertEqual(sigs(self.bodies[1]), [], "the repeat goes without the stored thinking")
        self.assertEqual(self.bodies[1]["tools"], self.bodies[0]["tools"])
        self.assertEqual(sigs(self.bodies[2]), ["s4"],
                         "the round after the recovery: no stale block again, the open tool round keeps its new thinking")
        self.assertEqual(len(self.bodies), 3, "one refusal, one repeat, one tool round — no refusal per round")
        self.assertEqual(self.sess.load(), lp.msgs, "the recovery is in the session file")
        asyncio.run(self.loop(self.ok).run("next turn"))
        self.assertEqual(sigs(self.bodies[3]), ["s4"], "a later turn (a new Loop on the file) does not send them")

    def test_a_400_is_recovered_once_and_stays_recovered(self):
        self.check(in_stream=False)

    def test_the_same_error_inside_a_200_stream_takes_the_same_path(self):
        self.check(in_stream=True)

    def left(self, sess=None):
        return [b["signature"] for m in (sess or self.sess).load() for b in m.get("_anthropic_blocks") or []
                if b["type"] == "thinking"]

    def test_a_second_refusal_is_reported_and_the_file_holds_no_block_to_send_again(self):
        self.seed(self.history())
        err = json.dumps(refusal()).encode()
        with self.assertRaises(llm.LLMError) as e:
            asyncio.run(self.loop(lambda n, b: (400, err)).run("next"))
        self.assertEqual((e.exception.kind, e.exception.status, len(self.bodies)), ("bad_request", 400, 2))
        self.assertEqual(sigs(self.bodies[1]), [])
        self.assertEqual(self.left(), [])

    def test_an_unrelated_400_that_mentions_thinking_is_repeated_once_and_nothing_stored_changes(self):
        """T3: 0.30.72 repeated such a request once without the blocks; that stays, and it never reaches the session."""
        self.seed(self.history())
        before = self.sess.path.read_bytes()
        err = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                     "message": "max_tokens must exceed thinking.budget_tokens"}}).encode()
        with self.assertRaises(llm.LLMError) as e:
            asyncio.run(self.loop(lambda n, b: (400, err)).run("next"))
        self.assertEqual((e.exception.status, len(self.bodies)), (400, 2))
        self.assertEqual([sigs(b) for b in self.bodies], [["s0", "s1", "s2", "s3"], []], "the one repeat went without them")
        self.assertEqual(self.left(), ["s0", "s1", "s2", "s3"])
        self.assertNotIn(b'"stale"', self.sess.path.read_bytes())
        self.assertTrue(self.sess.path.read_bytes().startswith(before), "the stored messages are untouched")
        # the repeat can succeed: the turn is answered, the blocks are still stored and sent the next turn
        self.bodies = []
        lp = self.loop(lambda n, b: (400, err) if n == 1 else self.ok())
        self.assertEqual(asyncio.run(lp.run("again"))["text"], "done")
        self.assertEqual(len([m for m in lp.msgs if m.get("_anthropic_blocks") and m["_anthropic_blocks"][0]["type"] == "thinking"]),
                         4)
        asyncio.run(self.loop(self.ok).run("later"))
        self.assertEqual(sigs(self.bodies[-1]), ["s0", "s1", "s2", "s3"])
        self.assertNotIn(b'"stale"', self.sess.path.read_bytes())

    def test_the_refusal_text_alone_is_not_enough(self):
        """T3: the text must come as a 400 (or in a 200 stream) with the type invalid_request_error to change the session."""
        raw = (BOUND % 5).encode()
        cases = {
            "another status": lambda n, b: (403, json.dumps(refusal()).encode()),
            "another type in the stream": lambda n, b: (200, sse([refusal(kind="overloaded_error")])),
            "another type as a 400": lambda n, b: (400, json.dumps(refusal(kind="api_error")).encode()),
            "no type at all": lambda n, b: (400, raw),
            "a server error": lambda n, b: (529, json.dumps(refusal()).encode()) if n == 1 else self.ok(),
        }
        for i, (name, script) in enumerate(cases.items()):
            with self.subTest(name):
                self.sess, self.bodies = Session(self.d / f"c{i}.jsonl"), []
                self.seed(self.history())
                try:
                    asyncio.run(self.loop(script).run("next"))
                except llm.LLMError:
                    pass
                self.assertEqual(sigs(self.bodies[0]), ["s0", "s1", "s2", "s3"])
                self.assertEqual(self.left(), ["s0", "s1", "s2", "s3"], "nothing was removed from the session")
                self.assertNotIn(b'"stale"', self.sess.path.read_bytes())
        self.sess, self.bodies = Session(self.d / "s529.jsonl"), []
        self.seed(self.history())
        asyncio.run(self.loop(cases["a server error"]).run("next"))
        self.assertEqual([sigs(b) for b in self.bodies], [["s0", "s1", "s2", "s3"]] * 2, "an ordinary retry, blocks included")

    def test_other_errors_are_not_taken_for_it(self):
        self.seed(self.history())
        with self.assertRaises(llm.LLMError):
            asyncio.run(self.loop(lambda n, b: (400, b'{"error":{"message":"max_tokens is too large"}}')).run("next"))
        self.assertEqual(len(self.bodies), 1)
        self.assertEqual(self.left(), ["s0", "s1", "s2", "s3"])


class DamagedFile(Case):
    """T1: `_anthropic_blocks` that is not a list of objects (a hand-edited or damaged file) never stops a session loading."""
    BAD = ["text", {"type": "thinking"}, [1, 2], [None], 7, None]

    def write(self, name):
        s = Session(self.d / name)
        s.append({"role": "user", "content": "start"})
        for i, bad in enumerate(self.BAD):
            s.append({"role": "assistant", "content": f"a{i}", "_anthropic_model": self.p.name, "_anthropic_blocks": bad})
        s.append({"role": "assistant", "content": "mixed", "_anthropic_model": self.p.name,
                  "_anthropic_blocks": [1, think("s0"), None, {"type": "text", "text": "mixed"}, "x"]})
        return s

    def check(self, got, n):
        self.assertEqual([m.get("_anthropic_blocks") for m in got[-n - 1:-1]], self.BAD[-n:] if n else [], "left as they are")
        self.assertEqual(got[-1]["_anthropic_blocks"], [1, None, {"type": "text", "text": "mixed"}, "x"],
                         "the thinking goes, every other element stays")

    def test_a_stale_record_over_damaged_blocks(self):
        base = self.write("a.jsonl").load()
        self.assertEqual(len(base), 8)
        s = self.write("b.jsonl")
        s.stale(99, [3, 99, "x", None])
        got = s.load()
        self.assertEqual(len(got), 8)
        self.check(got, 6)

    def test_a_compact_record_over_damaged_blocks(self):
        s = self.write("c.jsonl")
        s.compact({"role": "user", "content": "summary"}, 5)
        got = s.load()
        self.assertEqual(len(got), 6)
        self.check(got, 4)

    def test_the_loop_persists_a_trim_over_damaged_blocks(self):
        from agentj.core import session as sessions
        msgs = self.write("d.jsonl").load()
        sessions.stale(msgs, len(msgs), [])
        self.check(msgs, 6)


class ReportRound(Case):
    def test_the_report_round_keeps_tools_and_sends_tool_choice_none(self):
        def script(n, body):
            if n == 1:
                return 200, reply([think("s0"), use(0)], "tool_use")
            return 200, reply([{"type": "text", "text": "partial report"}])
        lp = self.loop(script, max_steps=1)
        r = asyncio.run(lp.run("go"))
        self.assertEqual((r["reason"], r["text"]), ("max_steps", "partial report"))
        first, last = self.bodies
        self.assertNotIn("tool_choice", first)
        self.assertEqual(last["tools"], first["tools"], "tools are part of the prefix of s0")
        self.assertEqual(last["system"], first["system"])
        self.assertEqual(last["tool_choice"], {"type": "none"})
        self.assertEqual(sigs(last), ["s0"], "the open tool round keeps its thinking")
        self.assertEqual(last["messages"][:len(first["messages"])], first["messages"])

    def test_a_tool_call_in_the_report_round_is_still_refused(self):
        lp = self.loop(lambda n, b: (200, reply([use(0)], "tool_use")), max_steps=0)
        with self.assertRaises(llm.LLMError) as e:
            asyncio.run(lp.run("go"))
        self.assertEqual(e.exception.kind, "bad_reply")
        self.assertEqual(self.bodies[0]["tool_choice"], {"type": "none"})

    def test_other_protocols_get_no_tools_as_before(self):
        ark = presets.resolve("ark", environ={})
        seen = []

        def transport(url, headers, body, timeout):
            seen.append(json.loads(body))
            return 200, ('data: {"choices":[{"index":0,"delta":{"content":"r"},"finish_reason":"stop"}]}\n\n'
                         "data: [DONE]\n\n").encode()
        lp = Loop(preset=ark, system="rules", specs=[tools.SPECS["ls"]], gate=Gate(self.d), environ={ark.key_env: SENTINEL},
                  max_steps=0, call=lambda *a, **k: llm.chat(*a, **k, transport=transport))
        self.assertEqual(asyncio.run(lp.run("go"))["text"], "r")
        self.assertNotIn("tools", seen[0])
        self.assertNotIn("tool_choice", seen[0])


class SwitchAndBack(Case):
    def test_another_model_then_the_first_again_replays_only_what_is_still_valid(self):
        a, b = self.p.name, self.other.name
        self.seed([{"role": "user", "content": "start"}] + step(a, 0, "a0") + [answer(a, "first", "a1")])
        asyncio.run(self.loop(lambda n, body: (200, reply([think("b0"), {"type": "text", "text": "by b"}])),
                              preset=self.other).run("to b"))
        self.assertEqual(sigs(self.bodies[0]), [], "another model gets text and tool_use, never the first one's blocks")
        asyncio.run(self.loop(self.ok).run("back to a"))
        self.assertEqual(sigs(self.bodies[1]), ["a0", "a1"], "nothing before them changed: they are valid and sent")
        self.assertEqual(self.bodies[1]["messages"][:4], self.bodies[0]["messages"][:1] + [
            {"role": "assistant", "content": [think("a0"), use(0)]}, self.bodies[0]["messages"][2],
            {"role": "assistant", "content": [think("a1"), {"type": "text", "text": "first"}]}])
        self.assertEqual(b, self.sess.load()[5]["_anthropic_model"])

    def test_a_change_made_while_the_other_model_runs_is_not_replayed_on_return(self):
        a = self.p.name
        msgs = [{"role": "user", "content": "start"}] + step(a, 0, "a0", out=TRIMMED)
        for i in range(1, 6):
            msgs += step(a, i, f"a{i}", out="x" * 16000)
        self.seed(msgs + [answer(a, "so far", "a6")])
        asyncio.run(self.loop(self.ok, preset=self.other).run("to b"))          # trims while the other model runs
        self.assertEqual(results(self.bodies[0])[:2], [TRIMMED, TRIMMED])
        asyncio.run(self.loop(self.ok).run("back to a"))
        self.assertEqual(sigs(self.bodies[1]), ["a0", "a1"], "the first model's blocks after the trimmed output are gone")
        self.assertEqual(results(self.bodies[1])[:len(results(self.bodies[0]))], results(self.bodies[0]))

    def test_an_ordinary_retry_after_a_recovery_sends_the_recovered_conversation(self):
        """An ordinary retry (5xx) after a recovered refusal sends the recovered conversation, not the refused one."""
        m = self.p.name
        self.seed([{"role": "user", "content": "start"}] + step(m, 0, "s0") + [answer(m, "first", "s1")])

        def script(n, body):
            if n == 1:
                return 400, json.dumps(refusal(1)).encode()
            if n == 2:
                return 529, b'{"type":"error","error":{"type":"overloaded_error"}}'
            return self.ok()
        asyncio.run(self.loop(script).run("next"))
        self.assertEqual([sigs(x) for x in self.bodies], [["s0", "s1"], [], []])
        self.assertNotIn('"thinking"', json.dumps(self.sess.load()))


if __name__ == "__main__":
    unittest.main()
