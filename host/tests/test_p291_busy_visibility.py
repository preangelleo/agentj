"""P291: background work was running but no robot showed (Leo, Mac shared Claude session, 2026-10-14 23:17).

Root cause on the Mac: the A14 process check walked the hook's ancestry with one `ps` fork per level, on serve's event loop.
On a loaded Mac each fork took seconds (host.log: `loop_stall` 5–8 s in Channel.receive at subprocess.run, next to every
`shared_hook_refused … "process"`), so the served session's own hooks were refused and the loop stalled for everyone.
Fixes covered here:
- ancestry: Linux /proc, macOS libproc (no fork), `ps` only as the last resort; walked off the event loop
  (Channel.receive → asyncio.to_thread), stops at the served pid;
- Stop.background_tasks is Claude Code's own list: it ADDS what is missing (a lost SubagentStart) and a background Bash
  (type "shell") is an item too, shown at once from PostToolUse(backgroundTaskId); a bare command label is scrubbed;
- the owned stream-json: task_started local_bash is an item until task_updated / task_notification;
- macOS: running launchd jobs `agentj-p<N>…` are the systemd `agentj-p*` units' equivalent;
- the main Agent is told what the robots mean and to wrap headless work in `agentj busy run`.
Payloads below are the real ones captured from Claude Code 2.1.299 on macOS and 2.1.296 on Linux (P291 probe).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import busy, main_identity, shared, shared_hook  # noqa: E402

SID = "4e19b953-d291-4b2a-90c3-d28a278cbd88"
AID = "abaee3b327a49117f"
SHELL = "baodrurr8"
SUB = {"id": AID, "type": "subagent", "status": "running", "description": "Probe sleeper", "agent_type": "general-purpose"}
SH = {"id": SHELL, "type": "shell", "status": "running", "description": "Probe bash sleep", "command": "sleep 20"}
# Claude Code 2.1.299 (macOS) hook payloads, in order (transcript_path / cwd / prompt ids dropped)
MAC = [
    {"hook_event_name": "SessionStart", "source": "startup"},
    {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_use_id": "toolu_1",
     "tool_input": {"description": "Probe sleeper", "prompt": "sleep 15", "subagent_type": "general-purpose", "run_in_background": True}},
    {"hook_event_name": "SubagentStart", "agent_id": AID, "agent_type": "general-purpose"},
    {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": "toolu_1",
     "tool_input": {"description": "Probe sleeper", "subagent_type": "general-purpose", "run_in_background": True},
     "tool_response": {"isAsync": True, "status": "async_launched", "agentId": AID, "description": "Probe sleeper"}},
    {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "toolu_2",
     "tool_input": {"command": "sleep 20", "description": "Probe bash sleep", "run_in_background": True}},
    {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_2",
     "tool_input": {"command": "sleep 20", "description": "Probe bash sleep", "run_in_background": True},
     "tool_response": {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": SHELL}},
    {"hook_event_name": "Stop", "stop_hook_active": False, "background_tasks": [SUB, SH], "session_crons": []},
    {"hook_event_name": "SubagentStop", "agent_id": AID, "agent_type": "general-purpose", "background_tasks": [SUB, SH]},
    {"hook_event_name": "Stop", "background_tasks": [SH], "session_crons": []},
    {"hook_event_name": "Stop", "background_tasks": [], "session_crons": []},
    {"hook_event_name": "SessionEnd", "reason": "other"},
]


def ev(e):
    return {"session_id": SID, **e}


def rows(b):
    return sorted((t["who"], t["label"]) for t in b.items())


class SharedHooks(unittest.TestCase):
    def test_mac_probe_sequence_shows_the_sub_agent_and_the_background_bash(self):
        b = busy.Board(None)
        seen = []
        for e in MAC:
            b.hook_event(ev(e), scope="shared-claude")
            seen.append((e["hook_event_name"], rows(b)))
        both = [(None, "Probe bash sleep"), (None, "Probe sleeper")]
        self.assertEqual(seen[2], ("SubagentStart", [(None, "Probe sleeper")]))
        self.assertEqual(seen[5], ("PostToolUse", both), "the background Bash shows as soon as it is launched")
        self.assertEqual(seen[6], ("Stop", both), "the main turn ending keeps background work on the phone")
        self.assertEqual(seen[7], ("SubagentStop", [(None, "Probe bash sleep")]))
        self.assertEqual(seen[8], ("Stop", [(None, "Probe bash sleep")]))
        self.assertEqual(seen[9], ("Stop", []))
        shell = [v for v in b.mem.values()]
        self.assertEqual(shell, [])

    def test_lost_subagent_start_is_caught_up_by_the_launch_result_and_by_stop(self):
        # the Mac case: the SubagentStart never reached the board — the robot must still show
        for src in ("PostToolUse", "Stop"):
            b = busy.Board(None)
            for e in MAC:
                if e["hook_event_name"] == "SubagentStart" or (src == "Stop" and e["hook_event_name"] == "PostToolUse"):
                    continue
                b.hook_event(ev(e), scope="shared-claude")
                if e["hook_event_name"] == src:
                    break
            self.assertIn((None, "Probe sleeper"), rows(b), src)
            it = b.mem[f"shared-claude:{SID}:{AID}"]
            self.assertEqual((it["harness"], it["role"]), ("claude", "general-purpose"))

    def test_named_sub_agent_from_stop_keeps_its_name(self):
        b = busy.Board(None)
        b.hook_event(ev({"hook_event_name": "Stop", "background_tasks": [
            {"id": "a1", "type": "subagent", "status": "running", "description": "竞品简报", "agent_type": "market-ceo"}]}),
            scope="shared-claude")
        self.assertEqual(rows(b), [("market-ceo", "竞品简报")])

    def test_subagent_start_label_wins_over_the_stop_list(self):
        b = busy.Board(None)
        for e in MAC[1:3]:
            b.hook_event(ev(e), scope="shared-claude")
        since = b.mem[f"shared-claude:{SID}:{AID}"]["since"]
        b.hook_event(ev({"hook_event_name": "Stop", "background_tasks": [dict(SUB, description="other")]}), scope="shared-claude")
        it = b.mem[f"shared-claude:{SID}:{AID}"]
        self.assertEqual((it["label"], it["since"]), ("Probe sleeper", since))

    def test_finished_entries_and_task_stop_close(self):
        b = busy.Board(None)
        b.hook_event(ev({"hook_event_name": "Stop", "background_tasks": [SH, dict(SUB, status="completed")]}), scope="s")
        self.assertEqual(rows(b), [(None, "Probe bash sleep")], "a completed entry is not work")
        b.hook_event(ev({"hook_event_name": "PostToolUse", "tool_name": "TaskStop", "tool_input": {"task_id": SHELL},
                         "tool_response": {}}), scope="s")
        self.assertEqual(rows(b), [])

    def test_sub_agent_events_never_open_main_thread_items(self):
        b = busy.Board(None)
        b.hook_event(ev({"hook_event_name": "PostToolUse", "agent_id": AID, "tool_name": "Bash",
                         "tool_input": {"command": "sleep 5", "run_in_background": True},
                         "tool_response": {"backgroundTaskId": "bx"}}), scope="s")
        b.hook_event(ev({"hook_event_name": "Stop", "agent_id": AID, "background_tasks": [SH]}), scope="s")
        self.assertEqual(rows(b), [])

    def test_a_bare_command_label_is_scrubbed(self):
        b = busy.Board(None)
        cmd = 'curl -H "Authorization: Bearer sk-ant-api03-' + "x" * 40 + '" https://example.com/run'
        b.hook_event(ev({"hook_event_name": "Stop", "background_tasks": [{"id": "b1", "type": "shell", "command": cmd}]}),
                     scope="s")
        (label,) = [t["label"] for t in b.items()]
        self.assertNotIn("sk-ant-api03", label)
        self.assertIn("curl", label)

    def test_unknown_background_type_shows_by_its_description(self):
        b = busy.Board(None)
        b.hook_event(ev({"hook_event_name": "Stop", "background_tasks": [
            {"id": "m1", "type": "monitor", "status": "running", "description": "watch CI"}]}), scope="s")
        self.assertEqual(rows(b), [(None, "watch CI")])


class OwnedStream(unittest.TestCase):
    # Claude Code 2.1.296 stream-json for a run_in_background Bash
    START = {"type": "system", "subtype": "task_started", "task_id": "baj9xjz4c", "tool_use_id": "toolu_x",
             "description": "Probe bash sleep", "is_backgrounded": True, "task_type": "local_bash"}

    def test_background_bash_is_an_item_until_it_completes(self):
        for end in ({"type": "system", "subtype": "task_updated", "task_id": "baj9xjz4c",
                     "patch": {"status": "completed", "end_time": 1}},
                    {"type": "system", "subtype": "task_notification", "task_id": "baj9xjz4c", "status": "completed"}):
            b = busy.Board(None)
            b.stream_event(self.START)
            self.assertEqual(rows(b), [(None, "Probe bash sleep")])
            self.assertEqual(b.mem["own:task:baj9xjz4c"]["role"], "shell")
            b.stream_event(end)
            self.assertEqual(rows(b), [])

    def test_progress_update_keeps_it(self):
        b = busy.Board(None)
        b.stream_event(self.START)
        b.stream_event({"type": "system", "subtype": "task_updated", "task_id": "baj9xjz4c", "patch": {"status": "running"}})
        self.assertEqual(len(b.items()), 1)


class Launchd(unittest.TestCase):
    OUT = ("PID\tStatus\tLabel\n"
           "4211\t0\tagentj-p291-busy-fix\n"
           "-\t0\tagentj-p290-idle\n"
           "4300\t0\tcom.apple.Finder\n"
           "4400\t0\tagentj-serve\n"
           "4500\t0\tagentj-p77\n")

    def test_running_agentj_p_jobs_are_dispatches_on_macos(self):
        calls = []

        def run(argv, **k):
            calls.append(argv[0])
            if argv[0] == "systemctl":
                raise FileNotFoundError("systemctl")          # macOS: no systemd
            return SimpleNamespace(stdout=self.OUT)
        got = busy.scan_units(run, platform="darwin")
        self.assertEqual(calls, ["systemctl", "launchctl"])
        self.assertEqual([(x["who"], x["label"], x["kind"]) for x in got],
                         [("P291", "busy fix", "ceo"), ("P77", "agentj-p77", "ceo")])
        self.assertEqual(busy.scan_units(lambda *a, **k: (_ for _ in ()).throw(OSError()), platform="darwin"), [])

    def test_linux_without_systemctl_never_asks_launchd(self):
        calls = []

        def run(argv, **k):
            calls.append(argv[0])
            raise FileNotFoundError(argv[0])
        self.assertEqual(busy.scan_units(run, platform="linux"), [])
        self.assertEqual(calls, ["systemctl"])


class Ancestry(unittest.TestCase):
    def test_stops_at_the_served_pid(self):
        me, parent = os.getpid(), os.getppid()
        self.assertEqual(shared_hook.ancestors(me, 32, parent), [me, parent])
        self.assertEqual(shared_hook.ancestors(me, 32, me), [me])
        self.assertEqual(shared_hook.ancestors(me)[:2], [me, parent])

    def test_libproc_only_on_macos(self):
        with patch.object(shared_hook.sys, "platform", "linux"):
            self.assertIsNone(shared_hook._ppid_libproc(os.getpid()))

    def test_without_procfs_libproc_answers_and_ps_never_forks(self):
        real = {os.getpid(): os.getppid()}
        no_proc = Mock(side_effect=OSError("no procfs"))
        with patch("builtins.open", no_proc), \
                patch.object(shared_hook, "_ppid_libproc", side_effect=lambda pid: real.get(pid)), \
                patch("subprocess.run", side_effect=AssertionError("ps must not run when libproc answers")):
            self.assertEqual(shared_hook.ancestors(os.getpid(), 32, os.getppid()), [os.getpid(), os.getppid()])

    @unittest.skipUnless(sys.platform == "darwin", "macOS libproc")
    def test_real_libproc(self):
        self.assertEqual(shared_hook._ppid_libproc(os.getpid()), os.getppid())


class OffLoop(unittest.TestCase):
    """The Mac stall: a slow ancestry walk must not block serve's loop, and its answer still decides."""

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p291-")
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        self.work = self.root / "work"
        (self.work / ".claude").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)

    def agent(self, board):
        h = Mock()
        h.st.root = self.root
        h.stopped.return_value = False
        h.busy = board
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True})
        a.session = {"sessionId": SID, "pid": 424242}           # the served Claude: not this process
        a.issue_hook_token()
        return a, h

    def run_hook(self, a, event, walk):
        gaps = []

        async def go():
            chan = shared_hook.Channel(a, self.root / "c.sock")
            await chan.start()
            stop = asyncio.Event()

            async def tick():
                last = time.monotonic()
                while not stop.is_set():
                    await asyncio.sleep(.02)
                    now = time.monotonic()
                    gaps.append(now - last)
                    last = now
            ticker = asyncio.ensure_future(tick())
            try:
                r, w = await asyncio.open_unix_connection(str(chan.path))
                w.write(json.dumps(event).encode() + b"\n")
                await w.drain()
                out = json.loads(await r.readline())
                w.close()
                await w.wait_closed()
                return out
            finally:
                stop.set()
                await ticker
                await chan.stop()
        with patch.object(shared_hook, "ancestors", side_effect=walk):
            out = asyncio.run(go())
        return out, max(gaps or [0])

    def test_slow_walk_runs_off_the_loop_and_the_sub_agent_shows(self):
        board = busy.Board(None)
        a, h = self.agent(board)

        def slow(pid, depth=32, until=None):
            time.sleep(.6)                                          # a loaded Mac's `ps`
            return [pid, 424242]
        start = ev({"hook_event_name": "SubagentStart", "agent_id": AID, "agent_type": "general-purpose",
                    "agentj_token": a.hook_token})
        _, gap = self.run_hook(a, start, slow)
        self.assertLess(gap, .4, "the event loop kept running while the ancestry was walked")
        self.assertEqual(rows(board), [(None, "general-purpose")])
        self.assertNotIn("shared_hook_refused", [c.args[0] for c in h.st.log.call_args_list])
        self.assertIn("busy_sub", [c.args[0] for c in h.st.log.call_args_list])

    def test_a_sender_outside_the_served_process_is_still_refused(self):
        board = busy.Board(None)
        a, h = self.agent(board)
        start = ev({"hook_event_name": "SubagentStart", "agent_id": AID, "agent_type": "general-purpose",
                    "agentj_token": a.hook_token})
        self.run_hook(a, start, lambda pid, depth=32, until=None: [pid, 1234])
        self.assertEqual(rows(board), [])
        self.assertEqual([c.kwargs.get("reason") for c in h.st.log.call_args_list if c.args[0] == "shared_hook_refused"],
                         ["process"])


class Identity(unittest.TestCase):
    def test_the_main_agent_knows_what_the_robots_mean(self):
        for lang, robots in (("zh", "跳舞机器人"), ("en", "dancing robots")):
            line = main_identity.HEARTBEAT_LINE[lang]
            self.assertIn(robots, line)
            self.assertIn("agentj busy run --who", line)
            for harness in ("claude", "codex", "opencode"):
                self.assertIn("agentj busy run --who", main_identity.prompt({"language": lang}, harness=harness))
        zh = main_identity.prompt({"language": "zh"}, harness="claude")
        self.assertLessEqual(len(zh), shared.SharedClaudeAgent.INLINE_MAX)

    def test_capability_skill_wraps_headless_work_on_every_os(self):
        text = (HERE.parent / "agentj/skills/agentj-capability/SKILL.md").read_text()
        self.assertIn("agentj busy run --who", text)
        self.assertIn("launchd", text)


if __name__ == "__main__":
    unittest.main()
