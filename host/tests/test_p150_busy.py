"""P150 (PROTOCOL §22): the phone's 「在办事项」 dots — who works in the background under the Agent's watch.

- labels are the whole description on one line, capped at LABEL_MAX (P154; no more 5-character squeeze);
- the owned Claude session (stream-json): an Agent/Task tool_use opens an item, its tool_result closes it, a background
  launch stays until its task_notification; the process ending clears everything of it;
- shared Claude sessions (hooks): PreToolUse(Agent) gives the description, SubagentStart/Stop open/close, SessionEnd and
  Stop.background_tasks reconcile; a SubagentStart without an Agent call of ours and without a type is not shown;
- a named worker keeps its colour across runs, items on screen never share one (≤ 9);
- item files (agentj busy start/run): pruned when their pid ends; systemd `agentj-p*` units are parsed "who · what";
- serve: on_ready sends the list (also empty), a change goes to every ready phone once, a workflow run is its CEO's item.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import busy, serve  # noqa: E402
from agentj.state import State  # noqa: E402


def tool_use(tid, desc, typ="general-purpose"):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Agent",
                                                          "input": {"description": desc, "subagent_type": typ, "prompt": "x"}}]}}


def tool_result(tid, text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}]}]}}


class Labels(unittest.TestCase):
    def test_short(self):
        # P154: the whole description, one line, capped at LABEL_MAX (no more 5-character squeeze)
        self.assertEqual(busy.short("转录视频，然后发给我"), "转录视频，然后发给我")
        self.assertEqual(busy.short("Transcribe the video for Leo"), "Transcribe the video for Leo")
        self.assertEqual(busy.short("  部署 relay\n热修  到生产\t"), "部署 relay 热修 到生产")
        self.assertEqual(busy.short(""), "")
        self.assertEqual(busy.short(None), "")
        self.assertEqual(busy.short("\x1b[31m巡检\x00代理"), "巡检代理")
        long = "很长的标题" * 40
        out = busy.short(long)
        self.assertEqual(len(out), busy.LABEL_MAX)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(busy.short("x" * busy.LABEL_MAX), "x" * busy.LABEL_MAX)

    def test_who(self):
        self.assertIsNone(busy.who_of("general-purpose"))
        self.assertIsNone(busy.who_of("Explore"))
        self.assertEqual(busy.who_of("browser-debugger"), "browser-debugger")


class OwnedStream(unittest.TestCase):
    def setUp(self):
        self.n = 0
        self.b = busy.Board(None, on_change=self._ch)

    def _ch(self):
        self.n += 1

    def test_foreground_open_close(self):
        self.b.stream_event(tool_use("tu1", "转录视频，再总结"))
        it = self.b.items()
        self.assertEqual([(x["who"], x["kind"], x["label"]) for x in it], [(None, "temp", "转录视频，再总结")])
        self.b.stream_event(tool_result("tu1", "done: the summary"))
        self.assertEqual(self.b.items(), [])
        self.assertEqual(self.n, 2)

    def test_subagent_traffic_is_not_a_new_item(self):
        ev = tool_use("tu9", "inner")
        ev["parent_tool_use_id"] = "tu1"
        self.b.stream_event(ev)
        self.assertEqual(self.b.items(), [])

    def test_background_stays_until_notification(self):
        self.b.stream_event(tool_use("tu2", "Proxy scan", "browser-debugger"))
        self.b.stream_event(tool_result("tu2", "Async agent launched successfully. agentId: a1b2c3d4 (status: async_launched)"))
        it = self.b.items()
        self.assertEqual([(x["who"], x["label"]) for x in it], [("browser-debugger", "Proxy scan")])
        self.b.stream_event({"type": "system", "subtype": "task_started", "task_id": "a1b2c3d4", "description": "dup"})
        self.assertEqual(len(self.b.items()), 1, "the launch and its task_started are one item")
        self.b.stream_event({"type": "system", "subtype": "task_notification", "task_id": "a1b2c3d4", "status": "completed"})
        self.assertEqual(self.b.items(), [])

    def test_process_end_clears(self):
        self.b.stream_event(tool_use("tu3", "a"))
        self.b.start("wf:x", "巡检", "ceo")
        self.b.session_gone("own")
        self.assertEqual([x["who"] for x in self.b.items()], ["巡检"])

    def test_lost_close_expires(self):
        self.b.stream_event(tool_use("tu4", "a"))
        self.assertEqual(self.b.items(now=busy.time.time() + busy.TEMP_TTL + 5), [])


class SharedHooks(unittest.TestCase):
    def setUp(self):
        self.b = busy.Board(None)

    def ev(self, name, **kw):
        return {"hook_event_name": name, "session_id": "S1", **kw}

    def test_lifecycle(self):
        self.b.hook_event(self.ev("PreToolUse", tool_name="Agent", tool_input={"description": "备份日志到 NAS", "subagent_type": "general-purpose"}))
        self.b.hook_event(self.ev("SubagentStart", agent_id="A1", agent_type="general-purpose"))
        self.assertEqual([(x["who"], x["label"]) for x in self.b.items()], [(None, "备份日志到 NAS")])
        self.b.hook_event(self.ev("SubagentStop", agent_id="A1", agent_type="general-purpose"))
        self.assertEqual(self.b.items(), [])

    def test_unmatched_untyped_start_is_hidden(self):
        self.b.hook_event(self.ev("SubagentStart", agent_id="Z", agent_type=""))
        self.assertEqual(self.b.items(), [])

    def test_named_without_description(self):
        self.b.hook_event(self.ev("SubagentStart", agent_id="A2", agent_type="cloudflare-operator"))
        self.assertEqual([x["who"] for x in self.b.items()], ["cloudflare-operator"])

    def test_session_end_and_stop_reconcile(self):
        for a in ("A1", "A2"):
            self.b.hook_event(self.ev("SubagentStart", agent_id=a, agent_type="x-" + a))
        self.b.hook_event(self.ev("Stop", background_tasks=[{"id": "A2", "type": "subagent"}]))
        self.assertEqual([x["who"] for x in self.b.items()], ["x-A2"])
        self.b.hook_event(self.ev("SessionEnd"))
        self.assertEqual(self.b.items(), [])


class Colors(unittest.TestCase):
    def test_stable_and_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            b = busy.Board(d)
            for i, w in enumerate(["掌枢", "玄枢", "守望"]):
                b.start(f"k{i}", w, "ceo", "x")
            first = {x["who"]: x["c"] for x in b.items()}
            self.assertEqual(len(set(first.values())), 3)
            busy.save_colors(d, b.take_colors())
            self.assertIsNone(b.take_colors(), "saved once per change")
            b2 = busy.Board(d)                       # a restart: the same CEO keeps its colour, alone or not
            b2.start("q", "守望", "ceo", "y")
            self.assertEqual(b2.items()[0]["c"], first["守望"])
            for i in range(9):
                b2.start(f"t{i}", None, "temp", "t")
            cs = [x["c"] for x in b2.items()]
            self.assertEqual(len(cs), 10)
            self.assertEqual(len(set(cs[:9])), 9, "nine on screen, nine colours")
            self.assertTrue(all(0 <= c < busy.COLORS for c in cs))
            self.assertNotEqual(cs[8], cs[9])

    def test_wire_shape(self):
        b = busy.Board(None)
        b.start("wf:a", "日报", "ceo", "", lk="schedule")
        m = b.msg("AgentJ-Father")
        self.assertEqual(m["t"], "busy")
        self.assertEqual(m["by"], "AgentJ-Father")
        self.assertEqual(set(m["tasks"][0]), {"id", "who", "kind", "label", "lk", "since", "c"})


class Outside(unittest.TestCase):
    def test_item_files_and_dead_pid(self):
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.Popen([sys.executable, "-c", "pass"])
            p.wait()
            busy.write_item(d, busy.item("cli:dead", "旧", "ceo", "x", pid=p.pid))
            busy.write_item(d, busy.item("cli:live", "掌枢", "ceo", "发布收尾", pid=os.getpid()))
            (busy.items_dir(d) / "junk.json").write_text("{not json")
            got = busy.read_items(d)
            self.assertEqual([x["who"] for x in got], ["掌枢"])
            self.assertEqual(sorted(os.listdir(busy.items_dir(d))), ["cli_live.json"])
            self.assertEqual(oct(os.stat(busy.items_dir(d) / "cli_live.json").st_mode & 0o777), "0o600")

    def test_other_pid_namespace_lives_on_its_heartbeat(self):
        with tempfile.TemporaryDirectory() as d:
            f = busy.write_item(d, busy.item("run:fenced", "掌枢", "ceo", "x", pid=5, ns="pid:[1]"))
            self.assertEqual([x["who"] for x in busy.read_items(d)], ["掌枢"], "a fenced pid is not checked against ours")
            self.assertNotIn("ns", busy.read_items(d)[0])
            os.utime(f, (1, 1))
            self.assertEqual(busy.read_items(d), [], "no heartbeat for STALE seconds = gone")

    def test_units(self):
        out = ("agentj-p150.service loaded active running 掌枢 · 发布收尾\n"
               "agentj-p151-x.service loaded active running Review the P151 candidate\n"
               "agentj.service loaded active running Agent J host\n"
               "agentj-serve.service loaded active running Agent J\n")
        run = lambda *a, **k: SimpleNamespace(stdout=out)   # noqa: E731
        got = busy.scan_units(run)
        self.assertEqual([(x["who"], x["label"]) for x in got], [("掌枢", "发布收尾"), ("P151", "Review the P151 candidate")])
        self.assertEqual(busy.scan_units(lambda *a, **k: (_ for _ in ()).throw(OSError())), [])

    def test_cli_run_registers_while_running(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, AGENTJ_STATE_DIR=d, PYTHONPATH=str(HERE.parent))
            code = "import json,os,sys;n=[f for f in os.listdir(os.path.join(sys.argv[1],'agentperm','busy')) if f.startswith('run_')];" \
                   "print(json.load(open(os.path.join(sys.argv[1],'agentperm','busy',n[0])))['who'])"
            r = subprocess.run([sys.executable, "-c", "from agentj.cli import main; main()", "busy", "run", "--who", "掌枢", "--label", "发布收尾", "--",
                                sys.executable, "-c", code, d], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "掌枢")
            self.assertEqual([f for f in os.listdir(os.path.join(d, "agentperm", "busy")) if f.endswith(".json")], [], "removed at exit")


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_change_goes_out_once(self):
        sent = []
        self.host._post = lambda fn, obj_fn: sent.append(obj_fn(None))
        self.host.busy.set_context("own", pid=os.getpid())
        self.host.busy.start("own:tu:1", None, "temp", "转录视频")
        sent.clear()  # registration waits for the kernel sample
        self.host.busy.refresh_sources()
        self.host.busy_changed()                     # the same list again: nothing more
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["t"], "busy")
        self.assertEqual(sent[0]["tasks"][0]["label"], "转录视频")
        self.host.busy.stop("own:tu:1")
        self.assertEqual(sent[-1]["tasks"], [])
        self.assertEqual(len(sent), 2)

    def test_on_ready_sends_the_list(self):
        got = []

        async def send_app(s, obj):
            got.append(obj)
            return True
        self.host.send_app = send_app
        s = SimpleNamespace(cid="c", p33=True, state="ready", pub=b"", device="d")
        self.host.sessions = {"c": s}
        asyncio.run(self.host.on_ready(s, 0))
        busy_msgs = [m for m in got if m.get("t") == "busy"]
        self.assertEqual(len(busy_msgs), 1)
        self.assertEqual(busy_msgs[0]["tasks"], [])
        got.clear()
        s.p33 = False                                # a §8-only phone: nothing new in its sequence
        asyncio.run(self.host.on_ready(s, 0))
        self.assertNotIn("busy", [m.get("t") for m in got])

    def test_poll_reads_item_files(self):
        busy.write_item(self.st.root, busy.item("cli:x", "守望", "ceo", "备份日志", pid=os.getpid()))
        sent = []
        self.host._post = lambda fn, obj_fn: sent.append(obj_fn(None))
        self.host.busy.refresh_outside(units=False)
        self.host.busy.refresh_sources()
        self.host.busy_changed()
        self.assertEqual([t["who"] for t in sent[-1]["tasks"]], ["守望"])


if __name__ == "__main__":
    unittest.main()
