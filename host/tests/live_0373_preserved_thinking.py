"""Opt-in real-request check for 0.30.73 (preserved thinking); never part of a gate (the name is not test_*.py).

    AJ_LIVE_KEY_FILE=<env file> python -m unittest live_0373_preserved_thinking        (from tests/, with a throw-away HOME)

Reads variables by name only (the key_env of each preset asked) from that file; a case whose variable or file is missing
is skipped. The value goes to the kernel's `environ` argument only: not into os.environ, argv, logs or failures.
Every request is the kernel's own (Loop → llm.chat → the real transport); the wrapper below only adds what makes the
provider's answer deterministic on every model — the beta header and `prefix_mismatch_behavior: "error"` — and caps max_tokens.
Spend: an upper estimate at AJ_LIVE_USD_IN / AJ_LIVE_USD_OUT per million tokens (defaults are deliberately far above any small
model's price); the run stops asking once the estimate passes AJ_LIVE_USD_STOP (0.8). The last line printed is the estimate.

Gateways (security review T4): `Gateways` sends ONE report-round request (`tools` + `tool_choice: none`) to every other
anthropic-messages preset in presets.json, or to those named in AJ_LIVE_GATEWAYS (comma-separated preset names). The body is
the kernel's own, only max_tokens is capped; no beta header and no `thinking` field are added. Same spend estimate and stop line.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from agentj.core import llm, presets, tools
from agentj.core.loop import Loop
from agentj.core.pipeline import Gate
from agentj.core.session import TRIMMED, Session

MODEL = os.environ.get("AJ_LIVE_MODEL", "anthropic/claude-haiku-5-5")
BETA = "thinking-binding-controls-2026-08-01"
RATE = (float(os.environ.get("AJ_LIVE_USD_IN", "30")), float(os.environ.get("AJ_LIVE_USD_OUT", "150")))
STOP = float(os.environ.get("AJ_LIVE_USD_STOP", "0.8"))
MAX_TOKENS = 700
ASK = ("Use the ls tool on the folder `src` once, then tell me in one short sentence how many entries it has. "
       "Before calling the tool, reason about whether 391 is prime.")


def key_from(path: str, name: str) -> str:
    """The value of `name` in an env file; '' when the file or the variable is missing. Nothing else is read out."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, eq, v = line.strip().removeprefix("export ").partition("=")
                if eq and k.strip() == name:
                    return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


SPENT = [0, 0]              # input, output tokens of the whole run (every class)


def cost() -> float:
    return (SPENT[0] * RATE[0] + SPENT[1] * RATE[1]) / 1e6


def tearDownModule():
    print(f"\nlive 0373: {SPENT[0]} input + {SPENT[1]} output tokens, upper estimate ${cost():.4f}")


def gateways() -> list[str]:
    """Preset names to ask: AJ_LIVE_GATEWAYS, else every anthropic-messages preset but the one of MODEL."""
    asked = [x.strip() for x in os.environ.get("AJ_LIVE_GATEWAYS", "").split(",") if x.strip()]
    return asked or [n for n, p in presets.providers().items()
                     if p.get("protocol") == "anthropic-messages" and n != MODEL.split("/")[0]]


class Live(unittest.TestCase):
    spent = SPENT

    @classmethod
    def setUpClass(cls):
        cls.p = presets.resolve(MODEL, environ={})
        key = key_from(os.environ.get("AJ_LIVE_KEY_FILE", ""), cls.p.key_env)
        if not key:
            raise unittest.SkipTest(f"{cls.p.key_env} is not in AJ_LIVE_KEY_FILE: no real request was made")
        cls.env = {cls.p.key_env: key}

    cost = staticmethod(cost)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sess = Session(Path(self.tmp.name) / "s.jsonl")
        self.sent, self.status = [], []

    def transport(self, url, headers, body, timeout):
        if self.cost() > STOP:
            self.skipTest("the spend estimate passed the stop line")
        b = json.loads(body)
        b["max_tokens"] = MAX_TOKENS
        b["thinking"] = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}}
        self.sent.append(b)
        status, raw = llm._stream_transport(url, {**headers, "anthropic-beta": BETA}, json.dumps(b).encode(), 120)
        self.status.append(status)
        return status, raw

    def loop(self, **kw):
        async def ls(args):
            return "a.py\nb.py\nc.py"

        def call(*a, **k):
            r = llm.chat(*a, **k, transport=self.transport, sleep=lambda s: None)
            self.spent[0] += r["usage"]["input"]
            self.spent[1] += r["usage"]["output"]
            return r
        return Loop(preset=self.p, system="You are a careful assistant.", specs=[tools.SPECS["ls"]],
                    gate=Gate(self.tmp.name), session=self.sess, environ=self.env, extra={"ls": ls}, call=call, **kw)

    def thinking(self):
        return [b for m in self.sess.load() for b in m.get("_anthropic_blocks") or [] if b["type"] == "thinking"]

    def first_turn(self):
        asyncio.run(self.loop().run(ASK))
        if not self.thinking():
            self.skipTest("the model produced no thinking block: nothing to check")

    def test_1_an_unchanged_conversation_replays_its_thinking(self):
        self.first_turn()
        asyncio.run(self.loop().run("Thanks. Say OK."))
        self.assertEqual(set(self.status), {200}, "append-only turns pass the prefix check")
        self.assertIn('"thinking"', json.dumps(self.sent[-1]["messages"]))

    def test_2_a_kernel_trim_leaves_nothing_the_provider_refuses(self):
        self.first_turn()
        lp = self.loop()
        i = next(i for i, m in enumerate(lp.msgs) if m.get("role") == "tool")
        lp.msgs[i]["content"] = TRIMMED                      # what Loop._trim does, through the same persisting step
        lp._stale(len(lp.msgs) - i, [len(lp.msgs) - i])
        n = len(self.status)
        asyncio.run(lp.run("Thanks. Say OK."))
        self.assertEqual(self.status[n:], [200], "one request, accepted: no stale block left to refuse")

    def test_3_a_refused_replay_is_recovered_once_and_stays_recovered(self):
        self.first_turn()
        lines = self.sess.path.read_text(encoding="utf-8").replace("a.py\\nb.py\\nc.py", "changed behind the kernel's back")
        self.sess.path.write_text(lines, encoding="utf-8")
        n = len(self.status)
        asyncio.run(self.loop().run("Thanks. Say OK."))
        self.assertEqual(self.status[n:], [400, 200], "refused once, repeated without the stale blocks")
        m = len(self.status)
        asyncio.run(self.loop().run("And once more: say OK."))
        self.assertEqual(self.status[m:], [200], "the next turn does not send them again")

    def test_4_the_report_round_keeps_tools(self):
        r = asyncio.run(self.loop(max_steps=1).run(ASK))
        if r["reason"] != "max_steps":
            self.skipTest("the model answered without the tool")
        self.assertEqual(set(self.status), {200})
        self.assertEqual(self.sent[-1]["tool_choice"], {"type": "none"})
        self.assertEqual(self.sent[-1]["tools"], self.sent[0]["tools"])


class Gateways(unittest.TestCase):
    """One report round per third-party Messages gateway: does it accept `tool_choice: none` beside `tools`, and obey it?"""

    def test_each_gateway_answers_the_report_round_without_a_tool_call(self):
        names, asked = gateways(), 0
        for name in names:
            with self.subTest(preset=name):
                try:
                    p = presets.resolve(name, environ={})
                except presets.PresetError:
                    self.fail("no such preset")
                self.assertEqual(p.protocol, "anthropic-messages")
                key = key_from(os.environ.get("AJ_LIVE_KEY_FILE", ""), p.key_env)
                if not key:
                    continue                                # reported below: no request was made for this preset
                if cost() > STOP:
                    self.skipTest("the spend estimate passed the stop line")
                asked += 1
                sent, status = [], []

                def transport(url, headers, body, timeout):
                    b = json.loads(body)
                    b["max_tokens"] = MAX_TOKENS
                    sent.append(b)
                    st, raw = llm._stream_transport(url, headers, json.dumps(b).encode(), 120)
                    status.append(st)
                    return st, raw

                def call(*a, **k):
                    r = llm.chat(*a, **k, transport=transport, sleep=lambda s: None)
                    SPENT[0] += r["usage"]["input"]
                    SPENT[1] += r["usage"]["output"]
                    return r
                with tempfile.TemporaryDirectory() as d:
                    try:        # max_steps=0: the first and only request is the report round
                        r = asyncio.run(Loop(preset=p, system="You are a careful assistant.", specs=[tools.SPECS["ls"]],
                                             gate=Gate(d), environ={p.key_env: key}, call=call, max_steps=0).run(ASK))
                    except llm.LLMError as e:               # kind and status only: the detail is the gateway's text
                        self.fail(f"refused or not obeyed: {e.kind} (HTTP {e.status}) after {len(status)} request(s)")
                self.assertEqual(status, [200], "one request, accepted")
                self.assertEqual(sent[0]["tool_choice"], {"type": "none"})
                self.assertTrue(sent[0]["tools"])
                self.assertTrue(r["text"].strip(), "a text report came back")
        if asked < len(names):
            self.skipTest(f"{asked} of {len(names)} gateway presets had a key in AJ_LIVE_KEY_FILE; the others were not asked")


if __name__ == "__main__":
    unittest.main()
