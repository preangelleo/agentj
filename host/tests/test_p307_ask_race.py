"""P307: shared Claude session, AskUserQuestion on phone + terminal at once (Leo 10-15 11:0x).

Root cause (probed live, Claude Code 2.1.296): our PermissionRequest hook turned the host's answer
`{"behavior": "allow", "updatedInput": {..., "answers": {...}}}` into a bare `{"behavior": "allow"}`. A bare allow for
AskUserQuestion leaves the terminal's question dialog open and the phone's answer is lost; allow + updatedInput.answers
closes it and the tool returns the phone's answer. Deny (phone cancel / timeout) closes it too.

Also: a terminal answer's PostToolUse carries `answers` + `annotations` in tool_input (the card digest missed it), and a
terminal Esc sends no PostToolUse(Failure) and no Stop — only the transcript's tool_result shows it.

L1 (always): the four outcomes (phone answers, terminal answers, timeout, cancel) + Esc + rescue on hook path / stall.
L2 (AGENTJ_P307_LIVE=1, a logged-in `claude`): a real interactive Claude Code in a pty, the real installed hook script,
the real Channel and SharedClaudeAgent; only the phone (Host.question) is simulated.
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import serve, shared, shared_hook

QS = {"questions": [{"question": "Pick a color", "header": "Color", "multiSelect": False,
                     "options": [{"label": "Red", "description": "Choose red"},
                                 {"label": "Blue", "description": "Choose blue"}]}]}


def phone(outcome, picks=None, wait_gone=False):
    """A Host whose AskUserQuestion path is the real serve.Host._ask_user_question; only the phone is simulated."""
    fake = Mock()
    fake.task_label = None

    async def question(card, gone, task=None):
        fake.card = card
        if wait_gone:
            await asyncio.wait_for(gone, 120)
            return "gone", None
        return outcome, picks
    fake.question = question

    async def ask(tool, inp, gone=None, **kw):
        assert tool == "AskUserQuestion"
        return await serve.Host._ask_user_question(fake, inp, gone)
    return fake, ask


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p307-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        (self.root / "work/.claude").mkdir(parents=True)

    def agent(self, ask):
        h = Mock()
        h.st.root = self.root
        h.stopped.return_value = False
        h.lang = "zh"
        h.ask = ask
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": str(self.root / "work"), "_workflow_ceo": True})
        a.session = {"sessionId": "owner", "pid": os.getpid()}
        a.issue_hook_token()
        return h, a

    @staticmethod
    def ev(name, inp=None, **kw):
        return {"hook_event_name": name, "session_id": "owner", "tool_name": "AskUserQuestion",
                "tool_input": json.loads(json.dumps(inp or QS)), **kw}

    @staticmethod
    def logged(h, name):
        return [c for c in h.st.log.call_args_list if c.args and c.args[0] == name]


class PhoneSide(Base):
    def test_phone_answer_reaches_claude_as_updated_input(self):
        _, ask = phone("answer", [[2]])
        h, a = self.agent(ask)
        out = asyncio.run(a.hook_event(self.ev("PermissionRequest")))
        d = out["hookSpecificOutput"]["decision"]
        self.assertEqual(d["behavior"], "allow")
        self.assertEqual(d["updatedInput"]["answers"], {"Pick a color": "Blue"})
        self.assertEqual(d["updatedInput"]["questions"], QS["questions"])     # the input as received + answers only
        self.assertEqual(len(a.ask_answered), 1)
        # the session took it: PostToolUse carries the answers + annotations — the watch is cleared, no rescue
        asyncio.run(a.hook_event(self.ev("PostToolUse", inp={**QS, "answers": {"Pick a color": "Blue"}, "annotations": {}},
                                         tool_response={})))
        self.assertEqual(a.ask_answered, {})

    def test_multi_select_answers_join_labels(self):
        qs = json.loads(json.dumps(QS))
        qs["questions"][0]["multiSelect"] = True
        _, ask = phone("answer", [[1, 2]])
        h, a = self.agent(ask)
        d = asyncio.run(a.hook_event(self.ev("PermissionRequest", inp=qs)))["hookSpecificOutput"]["decision"]
        self.assertEqual(d["updatedInput"]["answers"], {"Pick a color": "Red, Blue"})

    def test_phone_cancel_and_timeout_close_the_dialog_with_the_hosts_message(self):
        for outcome, msg in (("cancel", serve.Q_CANCEL), ("timeout", serve.Q_TIMEOUT), ("no_device", serve.Q_TIMEOUT)):
            with self.subTest(outcome=outcome):
                _, ask = phone(outcome)
                h, a = self.agent(ask)
                d = asyncio.run(a.hook_event(self.ev("PermissionRequest")))["hookSpecificOutput"]["decision"]
                self.assertEqual(d, {"behavior": "deny", "message": msg})
                self.assertEqual(a.ask_answered, {})

    def test_allow_without_answers_leaves_the_dialog_to_the_computer(self):
        h, a = self.agent(AsyncMock(return_value={"behavior": "allow"}))
        self.assertEqual(asyncio.run(a.hook_event(self.ev("PermissionRequest"))), {})


class TerminalSide(Base):
    def _race(self, follow):
        fake, ask = phone(None, wait_gone=True)
        h, a = self.agent(ask)

        async def go():
            card = asyncio.ensure_future(a.hook_event(self.ev("PermissionRequest")))
            for _ in range(200):
                if a.open_cards:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(len(a.open_cards), 1)
            await follow(a)
            return await asyncio.wait_for(card, 5)
        return h, a, asyncio.run(go())

    def test_terminal_answer_withdraws_the_phone_card(self):
        async def follow(a):     # live 2.1.296: the finished call's input has answers + annotations
            await a.hook_event(self.ev("PostToolUse", inp={**QS, "answers": {"Pick a color": "Red"}, "annotations": {}},
                                       tool_response={}))
        h, a, out = self._race(follow)
        self.assertEqual(out, {})                    # no late decision from us
        self.assertEqual(a.open_cards, {})

    def test_terminal_esc_withdraws_the_card_from_the_transcript(self):
        """Esc on the terminal's question: no PostToolUse(Failure), no Stop — only the transcript's tool_result."""
        async def follow(a):
            a._turn_output({"id": "m1"}, [{"type": "tool_use", "id": "toolu_1", "name": "AskUserQuestion",
                                           "input": json.loads(json.dumps(QS))}])
            self.assertEqual(len(a.open_cards), 1)   # the tool_use alone withdraws nothing
            a._tool_results([{"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
                              "content": "User declined to answer questions"}])
        h, a, out = self._race(follow)
        self.assertEqual(out, {})
        self.assertEqual(a.open_cards, {})
        self.assertTrue(self.logged(h, "shared_card_withdrawn"))

    def test_unrelated_tool_result_keeps_the_card(self):
        fake, ask = phone(None, wait_gone=True)
        h, a = self.agent(ask)

        async def go():
            card = asyncio.ensure_future(a.hook_event(self.ev("PermissionRequest")))
            await asyncio.sleep(.05)
            a._turn_output({"id": "m1"}, [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}])
            a._tool_results([{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}])
            self.assertEqual(len(a.open_cards), 1)
            await a.hook_event({"hook_event_name": "Stop", "session_id": "owner", "last_assistant_message": "x"})
            return await asyncio.wait_for(card, 5)
        self.assertEqual(asyncio.run(go()), {})


class Rescue(Base):
    def test_answer_the_session_never_takes_is_sent_as_text_once(self):
        _, ask = phone("answer", [[2]])
        h, a = self.agent(ask)
        send = Mock()

        async def go():
            d = await a.hook_event(self.ev("PermissionRequest"))
            self.assertIn("updatedInput", d["hookSpecificOutput"]["decision"])
            await asyncio.sleep(.3)
        with patch.object(shared, "ASK_RESCUE_S", .05), patch.object(shared, "claude_send", send):
            asyncio.run(go())
        send.assert_called_once()
        self.assertIn("Pick a color → Blue", send.call_args.args[1])
        h.agent_notice.assert_called_once()
        rec = self.logged(h, "shared_ask_rescue")
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0].kwargs["result"], "sent")
        self.assertNotIn("Blue", json.dumps(rec[0].kwargs))            # the log never keeps the answer
        self.assertEqual(a.ask_answered, {})

    def test_answer_taken_in_time_is_not_resent(self):
        _, ask = phone("answer", [[1]])
        h, a = self.agent(ask)
        send = Mock()

        async def go():
            await a.hook_event(self.ev("PermissionRequest"))
            a._turn_output({"id": "m"}, [{"type": "tool_use", "id": "u", "name": "AskUserQuestion",
                                          "input": {**QS, "answers": {"Pick a color": "Red"}}}])
            a._tool_results([{"type": "tool_result", "tool_use_id": "u", "content": "Red"}])
            await asyncio.sleep(.3)
        with patch.object(shared, "ASK_RESCUE_S", .05), patch.object(shared, "claude_send", send):
            asyncio.run(go())
        send.assert_not_called()

    def test_turn_stall_rescues_at_once(self):
        _, ask = phone("answer", [[2]])
        h, a = self.agent(ask)
        send = Mock()
        a._idle_reply = AsyncMock(return_value=False)

        async def go():
            await a.hook_event(self.ev("PermissionRequest"))
            with self.assertRaises(asyncio.TimeoutError):
                await a._await_turn_end()
        with patch.object(shared, "ASK_RESCUE_S", 60), patch.object(shared, "TURN_WAIT", .1), \
                patch.object(shared, "claude_send", send):
            asyncio.run(go())
        send.assert_called_once()
        self.assertEqual(self.logged(h, "shared_ask_rescue")[0].kwargs["why"], "stall")


# ------------------------------------------------------------------ L2: real Claude Code
CLAUDE = shutil.which("claude")


@unittest.skipUnless(os.environ.get("AGENTJ_P307_LIVE") == "1" and CLAUDE, "live: AGENTJ_P307_LIVE=1 + a logged-in claude")
class LiveClaude(unittest.TestCase):
    PROMPT = ("Use the AskUserQuestion tool exactly once: question 'Pick a color', header 'Color', options 'Red' and "
              "'Blue' (single select). After you get the result, reply with one line: GOT=<the exact tool result text>.")

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p307live-", dir=os.environ.get("AGENTJ_P307_DIR", "/tmp"))
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.work = self.root / "work"
        (self.work / ".claude").mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir()

    def run_case(self, ask, keys=None, settle=None):
        """keys: what the person types into the terminal's question dialog. → (agent, host mock, transcript records)."""
        h = Mock()
        h.st.root = self.state
        h.stopped.return_value = False
        h.lang = "en"
        h.ask = ask
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True})
        a.attach = AsyncMock(return_value=False)
        env = {k: v for k, v in os.environ.items() if k not in
               ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_SESSION_ID",
                "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_EFFORT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CONFIG_DIR")}
        env["TERM"] = "xterm-256color"

        async def go():
            import fcntl, pty, struct, termios
            chan = shared_hook.Channel(a, shared_hook.channel_path(self.state, "claude"))
            await chan.start()
            shared_hook.install(self.work, chan.path)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 140, 0, 0))
            proc = await asyncio.create_subprocess_exec(CLAUDE, "--model", os.environ.get("AGENTJ_P307_MODEL", "haiku"),
                                                        cwd=self.work, env=env, stdin=slave, stdout=slave, stderr=slave,
                                                        start_new_session=True)
            os.close(slave)
            os.set_blocking(master, False)
            buf = bytearray()
            loop = asyncio.get_running_loop()

            def drain():
                try:
                    buf.extend(os.read(master, 65536))
                except OSError:
                    loop.remove_reader(master)
            loop.add_reader(master, drain)

            def screen():
                return re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b.", "",
                                                  buf.decode("utf8", "replace")))

            def claude_up(pid):
                return any(d["pid"] == pid for d in shared.claude_sessions(str(self.work)))

            async def until(pred, secs):
                end = time.monotonic() + secs
                while time.monotonic() < end:
                    if pred():
                        return True
                    await asyncio.sleep(.2)
                return False
            try:
                await until(lambda: "trustthisfolder" in screen(), 10)
                for _ in range(20):          # the folder trust prompt: move to 「Yes」 until it is selected, then Enter
                    sc = screen()
                    if "trustthisfolder" not in sc or claude_up(proc.pid):
                        break
                    if sc.rfind("❯Yes,Itrust") > sc.rfind("❯No,exit"):
                        os.write(master, b"\r")
                    else:
                        os.write(master, b"\x1b[B")
                    await asyncio.sleep(1)
                # the served session: registry entry of this very process (real ~/.claude)
                found = []
                await until(lambda: bool(found.extend(d for d in shared.claude_sessions(str(self.work))
                                                      if d["pid"] == proc.pid) or found), 30)
                self.assertTrue(found, "claude registry entry; screen: " + screen()[-600:])
                a.session = found[0]
                a.issue_hook_token()
                shared_hook.write_token(self.state, proc.pid, a.hook_token)
                slug = re.sub(r"[^A-Za-z0-9]", "-", str(self.work))
                a.transcript = Path.home() / ".claude/projects" / slug / (a.session["sessionId"] + ".jsonl")
                await asyncio.sleep(1.5)
                del buf[:]
                os.write(master, self.PROMPT.encode())
                await asyncio.sleep(1.5)
                os.write(master, b"\r")
                pressed = False
                end = time.monotonic() + float(os.environ.get("AGENTJ_P307_WAIT", 90))
                done = False
                while time.monotonic() < end and not done:
                    await asyncio.sleep(.25)
                    if a.transcript.is_file():
                        a.read_transcript()
                    if keys and not pressed and a.open_cards and "Esctocancel" in screen()[-3000:]:
                        await asyncio.sleep(1)
                        for k in keys:
                            os.write(master, k)
                            await asyncio.sleep(.5)
                        pressed = True
                    recs = self.records(a.transcript)
                    done = any(r.get("type") == "system" and r.get("subtype") == "turn_duration" for r in recs) or \
                        any("GOT=" in t for t in self.texts([r for r in recs if r.get("type") == "assistant"]))
                if settle:
                    await settle(a)
                await asyncio.sleep(1.5)
                if a.transcript.is_file():
                    a.read_transcript()
                return self.records(a.transcript), screen()
            finally:
                loop.remove_reader(master)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), 10)
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                os.close(master)
                await chan.stop()
        recs, scr = asyncio.run(go())
        return a, h, recs, scr

    @staticmethod
    def records(path):
        try:
            return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
        except (OSError, ValueError):
            return []

    @staticmethod
    def texts(recs):
        for r in recs:
            c = (r.get("message") or {}).get("content")
            if isinstance(c, str):
                yield c
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            yield b.get("text") or ""
                        elif b.get("type") == "tool_result":
                            v = b.get("content")
                            yield v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

    def ask_result(self, recs):
        uses = {b["id"] for r in recs for b in ((r.get("message") or {}).get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion"}
        res = [b for r in recs for b in ((r.get("message") or {}).get("content") or [])
               if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in uses]
        self.assertTrue(uses, "the model called AskUserQuestion")
        self.assertTrue(res, "AskUserQuestion has a result: the dialog closed")
        v = res[-1].get("content")
        return (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)), res[-1].get("is_error")

    def test_live_phone_answers(self):
        _, ask = phone("answer", [[2]])
        a, h, recs, scr = self.run_case(ask)
        text, err = self.ask_result(recs)
        self.assertIn("Blue", text)
        self.assertFalse(err)
        self.assertEqual(a.ask_answered, {})                       # taken in time: no rescue
        self.assertFalse([c for c in h.st.log.call_args_list if c.args[0] == "shared_ask_rescue"])

    def test_live_terminal_answers_first(self):
        fake, ask = phone(None, wait_gone=True)
        a, h, recs, scr = self.run_case(ask, keys=[b"\r"])        # Enter on the first option (Red)
        text, err = self.ask_result(recs)
        self.assertIn("Red", text)
        self.assertEqual(a.open_cards, {}, "the phone card was withdrawn")

    def test_live_terminal_esc(self):
        fake, ask = phone(None, wait_gone=True)
        a, h, recs, scr = self.run_case(ask, keys=[b"\x1b"])
        text, err = self.ask_result(recs)
        self.assertEqual(a.open_cards, {}, "the phone card was withdrawn")

    def test_live_phone_cancel(self):
        _, ask = phone("cancel")
        a, h, recs, scr = self.run_case(ask)
        text, err = self.ask_result(recs)
        self.assertTrue(err)
        self.assertIn(serve.Q_CANCEL[:12], text)

    def test_live_phone_timeout(self):
        _, ask = phone("timeout")
        a, h, recs, scr = self.run_case(ask)
        text, err = self.ask_result(recs)
        self.assertTrue(err)
        self.assertIn(serve.Q_TIMEOUT[:8], text)


if __name__ == "__main__":
    unittest.main()
