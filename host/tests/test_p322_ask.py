"""P322: `agentj ask` — the Agent J question card under any harness (Codex / OpenCode / Kimi / Qwen), Leo 10-15 22:3x.

Codex's request_user_input is refused outside Plan mode, so the Agent only listed options in text — and first claimed the
card had popped up. L1: the card builder (single / multiple / Other / open / preview, never truncated), the answer mapping,
serve's holder (answered / cancelled / no card / pending → late answer handed to the Agent as a host turn / --wait), the
CLI (not running = shown false, exit 1) and the identity line (every harness but the shared-Claude hook path).
"""
import _hermetic  # noqa: F401
import asyncio
import io
import json
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from agentj import ask, main_identity


def q(text="Pick one", opts=("A", "B"), **kw):
    return {"question": text, "options": [{"label": o} for o in opts], **kw}


class Build(unittest.TestCase):
    def test_single_adds_other_last(self):
        card, meta = ask.build([q()], "zh")
        self.assertEqual([o["l"] for o in card[0]["o"]], ["A", "B", ask.OTHER["zh"]])
        self.assertFalse(card[0]["m"])
        self.assertEqual(meta, [{"open": False, "other": 3}])

    def test_multi_no_other_header(self):
        card, meta = ask.build([q(multiSelect=True, other=False, header="Pick")], "en")
        self.assertTrue(card[0]["m"])
        self.assertEqual(card[0]["h"], "Pick")
        self.assertEqual(len(card[0]["o"]), 2)
        self.assertIsNone(meta[0]["other"])

    def test_open_question(self):
        card, meta = ask.build([{"question": "What should the title be?"}], "en")
        self.assertEqual([o["l"] for o in card[0]["o"]], [ask.OPEN["en"]])
        self.assertFalse(card[0]["m"])
        self.assertTrue(meta[0]["open"])

    def test_preview_goes_under_description(self):
        card, _ = ask.build([{"question": "Layout?", "options": [
            {"label": "Grid", "description": "3 columns", "preview": "[ ][ ][ ]"}, {"label": "List", "preview": "---"}]}])
        self.assertEqual(card[0]["o"][0]["d"], "3 columns\n[ ][ ][ ]")
        self.assertEqual(card[0]["o"][1]["d"], "---")

    def test_single_dict_and_string_options(self):
        card, _ = ask.build({"question": "Go?", "options": ["Yes", "No"], "other": False})
        self.assertEqual([o["l"] for o in card[0]["o"]], ["Yes", "No"])

    def test_refused_never_truncated(self):
        bad = [
            [],                                                   # no question
            [q()] * 9,                                            # over 8
            [q(opts=("A", "A"))],                                 # duplicate label
            [q(text="x" * 1001)],                                 # too long
            [q(opts=[str(i) for i in range(16)])],                # 16 + Other > 16
            [{"question": "Q", "options": [{"label": "A", "description": "d" * 400, "preview": "p" * 200}]}],
            [q(text="bad\x07")],                                  # control character
            [q(), q()],                                           # duplicate question
            "nope",
        ]
        for b in bad:
            with self.subTest(b=str(b)[:40]), self.assertRaises(ask.AskError):
                ask.build(b)
        ask.build([q(opts=[str(i) for i in range(15)])])          # 15 + Other = 16 fits

    def test_answers(self):
        card, meta = ask.build([q(), q("Many", ("x", "y", "z"), multiSelect=True), {"question": "Open?"}], "zh")
        res = ask.answers(card, meta, [[2], [1, 3, 4], [1]])
        self.assertEqual(res[0], {"question": "Pick one", "header": "", "picked": ["B"], "typed_in_chat": False})
        self.assertEqual(res[1]["picked"], ["x", "z"])
        self.assertTrue(res[1]["typed_in_chat"])                 # option 4 = Other
        self.assertEqual(res[2]["picked"], [])
        self.assertTrue(res[2]["typed_in_chat"])


class FakeHost:
    """serve.Host's surface CliAsks uses; question() behaves like serve's: registers the card before its first await."""

    def __init__(self, outcome="wait"):
        self.lang = "zh"
        self.task_label = None
        self.questions = {}
        self.outcome = outcome
        self.agent = Mock()
        self.st = Mock()
        self.n = 0

    async def question(self, card, gone, task=None):
        if self.outcome in ("no_device", "stopped"):
            return self.outcome, None
        self.n += 1
        qid = f"{self.n:032x}"
        fut = asyncio.get_running_loop().create_future()
        self.questions[qid] = types.SimpleNamespace(fut=fut, qs=card)
        self.card = card
        try:
            return await fut
        finally:
            self.questions.pop(qid, None)

    def answer(self, decision, picks=None):
        for x in list(self.questions.values()):
            x.fut.set_result((decision, picks))


class Holder(unittest.IsolatedAsyncioTestCase):
    async def test_answered_while_waiting(self):
        h = FakeHost()
        c = ask.CliAsks(h)
        t = asyncio.create_task(c.open({"questions": [q()], "wait": 5}))
        await asyncio.sleep(0.01)
        h.answer("answer", [[1]])
        res = await t
        self.assertEqual(res["result"], "answered")
        self.assertTrue(res["shown"] and res["ok"])
        self.assertEqual(res["answers"][0]["picked"], ["A"])
        await asyncio.sleep(0.01)
        h.agent.submit.assert_not_called()                       # the waiter got it: no host turn too

    async def test_other_means_typed_in_chat(self):
        h = FakeHost()
        c = ask.CliAsks(h)
        t = asyncio.create_task(c.open({"questions": [q()], "wait": 5}))
        await asyncio.sleep(0.01)
        h.answer("answer", [[3]])
        res = await t
        self.assertTrue(res["answers"][0]["typed_in_chat"])
        self.assertEqual(res["note"], ask.NOTE["typed"]["zh"])

    async def test_cancelled(self):
        h = FakeHost()
        c = ask.CliAsks(h)
        t = asyncio.create_task(c.open({"questions": [q()], "wait": 5}))
        await asyncio.sleep(0.01)
        h.answer("cancel")
        res = await t
        self.assertEqual((res["result"], res["shown"]), ("cancelled", True))
        self.assertNotIn("answers", res)

    async def test_no_card_is_never_shown(self):
        for outcome in ("no_device", "stopped"):
            res = await ask.CliAsks(FakeHost(outcome)).open({"questions": [q()], "wait": 5})
            self.assertEqual(res["result"], outcome)
            self.assertFalse(res["shown"] or res["ok"])
            self.assertNotIn("id", res)

    async def test_bad_card(self):
        h = FakeHost()
        res = await ask.CliAsks(h).open({"questions": [q(opts=("A", "A"))]})
        self.assertEqual(res["result"], "bad")
        self.assertFalse(res["shown"])
        self.assertEqual(h.questions, {})

    async def test_pending_then_late_answer_is_handed_over(self):
        h = FakeHost()
        c = ask.CliAsks(h)
        res = await c.open({"questions": [q()], "wait": 0.01})
        self.assertEqual(res["result"], "pending")
        self.assertTrue(res["shown"])
        self.assertIn(res["id"], res["note"])
        h.answer("answer", [[2]])
        for _ in range(5):
            await asyncio.sleep(0)
        h.agent.submit.assert_called_once()
        send = h.agent.submit.call_args[0][0]
        self.assertIn("〔Agent J 系统提示〕", send.text)
        self.assertIn("Pick one → B", send.text)
        self.assertEqual(send.by, "Agent J")
        again = await c.wait({"id": res["id"], "wait": 1})             # still readable with --wait
        self.assertEqual(again["answers"][0]["picked"], ["B"])

    async def test_wait_by_id(self):
        h = FakeHost()
        c = ask.CliAsks(h)
        res = await c.open({"questions": [q()], "wait": 0})
        t = asyncio.create_task(c.wait({"id": res["id"], "wait": 5}))
        await asyncio.sleep(0.01)
        h.answer("answer", [[1]])
        self.assertEqual((await t)["result"], "answered")
        await asyncio.sleep(0.01)
        h.agent.submit.assert_not_called()
        self.assertEqual((await c.wait({"id": "f" * 32}))["result"], "unknown")

    async def test_timeout_reported_not_answered(self):
        h = FakeHost()
        c = ask.CliAsks(h)
        t = asyncio.create_task(c.open({"questions": [q()], "wait": 5}))
        await asyncio.sleep(0.01)
        h.answer("timeout")
        res = await t
        self.assertEqual(res["result"], "timeout")
        self.assertFalse(res["ok"])


class RealServeQuestion(unittest.IsolatedAsyncioTestCase):
    """CliAsks over the real serve.Host.question: the card is registered before its first await (the id CliAsks learns),
    the phone message is the §10.7 card, a signed-looking answer resolves it and approvals.log gets one question line."""

    async def test_real_question_path(self):
        from agentj import serve
        h = Mock()
        h.lang, h.task_label, h.questions, h.estop = "en", None, {}, None
        h.stopped.return_value = False
        h._approvers.return_value = ["dev1"]
        h.agent.kind = "codex"
        h.stopping = asyncio.Event()
        h.st.config.return_value = {"channel": "c"}
        h.question = lambda card, gone, task=None: serve.Host.question(h, card, gone, task)
        h._question_msg = lambda qq: serve.Host._question_msg(h, qq)
        c = ask.CliAsks(h)
        t = asyncio.create_task(c.open({"questions": [q(), {"question": "Why?"}], "wait": 5}))
        await asyncio.sleep(0.01)
        self.assertEqual(len(h.questions), 1)
        (qid, pending), = h.questions.items()
        self.assertEqual([o["l"] for o in pending.qs[1]["o"]], [ask.OPEN["en"]])
        pending.fut.set_result(("answer", [[2], [1]], "dev1", b"k" * 32, b"s" * 64))
        res = await t
        self.assertEqual(res["id"], qid)
        self.assertEqual([a["picked"] for a in res["answers"]], [["B"], []])
        self.assertTrue(res["answers"][1]["typed_in_chat"])
        line = json.loads(h.st.append_private.call_args[0][1])
        self.assertEqual((line["kind"], line["decision"], line["picks"]), ("question", "answer", "2;1"))
        self.assertNotIn("Pick one", json.dumps(line))                 # the log keeps hashes, never the text


class SocketRoundTrip(unittest.IsolatedAsyncioTestCase):
    """The CLI's own ctl_call → a real unix socket → serve.Host.on_ctl → CliAsks → the answer back on the same line."""

    async def test_ctl_round_trip(self):
        import tempfile
        from pathlib import Path
        from _testtmp import TEST_TMP
        from agentj import names, serve
        d = tempfile.TemporaryDirectory(prefix="p322-", dir=TEST_TMP)
        self.addCleanup(d.cleanup)
        sock = Path(d.name) / "ctl.sock"
        fh = FakeHost()
        h = Mock()
        h.cli_asks = ask.CliAsks(fh)
        h._ctl_send = lambda w, obj: serve.Host._ctl_send(h, w, obj)
        server = await asyncio.start_unix_server(lambda r, w: serve.Host.on_ctl(h, r, w), path=str(sock))
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        st = types.SimpleNamespace(sock_path=sock)
        call = asyncio.create_task(asyncio.to_thread(names.ctl_call, st, {"cmd": "ask_open", "questions": [q()], "wait": 5}, 10))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if fh.questions:
                break
        fh.answer("answer", [[2]])
        res = await call
        self.assertEqual((res["result"], res["shown"], res["answers"][0]["picked"]), ("answered", True, ["B"]))
        res = await asyncio.to_thread(names.ctl_call, st, {"cmd": "ask_wait", "id": "0" * 32, "wait": 0}, 10)
        self.assertEqual(res["result"], "unknown")


class Cli(unittest.TestCase):
    def run_cli(self, argv, ctl):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        ask.add_parser(sub)
        a = p.parse_args(["ask", *argv])
        out = io.StringIO()
        with patch("agentj.names.ctl_call", ctl), redirect_stdout(out), self.assertRaises(SystemExit) as ex:
            a.fn(a)
        return ex.exception.code, json.loads(out.getvalue())

    def test_not_running_is_not_shown(self):
        code, res = self.run_cli(["Go?", "-o", "Yes", "-o", "No"], Mock(return_value=None))
        self.assertEqual((code, res["result"], res["shown"]), (1, "not_running", False))

    def test_flags_become_one_question(self):
        ctl = Mock(return_value={"ok": True, "result": "answered", "shown": True})
        code, _ = self.run_cli(["Go?", "-o", "Yes", "-o", "No", "--preview", "pv", "--multi", "--no-other",
                                "--header", "H", "--wait-s", "999"], ctl)
        self.assertEqual(code, 0)
        req = ctl.call_args[0][1]
        self.assertEqual(req["cmd"], "ask_open")
        self.assertEqual(req["wait"], ask.WAIT_MAX)
        self.assertEqual(req["questions"], [{"question": "Go?", "header": "H", "multiSelect": True, "other": False,
                                             "options": [{"label": "Yes", "preview": "pv"}, {"label": "No"}]}])

    def test_json_stdin_and_wait(self):
        ctl = Mock(return_value={"ok": True, "result": "pending", "shown": True})
        with patch("sys.stdin", io.StringIO(json.dumps({"questions": [q()]}))):
            code, _ = self.run_cli(["--json", "-"], ctl)
        self.assertEqual(code, 4)
        self.assertEqual(ctl.call_args[0][1]["questions"], [q()])
        ctl = Mock(return_value={"ok": False, "result": "unknown", "shown": False})
        code, _ = self.run_cli(["--wait", "ab" * 16], ctl)
        self.assertEqual((code, ctl.call_args[0][1]["cmd"]), (5, "ask_wait"))

    def test_bad_json_is_bad(self):
        with patch("sys.stdin", io.StringIO("{")):
            code, res = self.run_cli(["--json", "-"], Mock())
        self.assertEqual((code, res["result"], res["shown"]), (2, "bad", False))


class Identity(unittest.TestCase):
    def test_card_line_every_harness_but_shared_claude_hook(self):
        for lang in ("zh", "en"):
            cfg = {"language": lang, "dir": "/tmp/w"}
            with patch.object(main_identity, "language_of", return_value=lang):
                self.assertIn(main_identity.CARD_LINE[lang], main_identity.prompt(cfg, "codex"))
                self.assertIn(main_identity.CARD_LINE[lang], main_identity.prompt(cfg))
                self.assertNotIn(main_identity.CARD_LINE[lang], main_identity.prompt(cfg, "claude"))
            self.assertIn("agentj ask", main_identity.CARD_LINE[lang])
            self.assertIn("shown=true", main_identity.CARD_LINE[lang])


if __name__ == "__main__":
    unittest.main()
