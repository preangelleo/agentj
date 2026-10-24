"""P160 (PROTOCOL §22): Codex / OpenCode sub-agents in the same busy list as Claude's (drawn as P154's robots), and the
read-only detail a tap on one row asks for (`busy_get` → `busy_info`).

- Codex app-server: collabAgentToolCall spawnAgent opens one item per receiver thread (prompt = label), the child thread's
  turn/started / turn/completed re-open / close it, agentsStates final / closeAgent / subAgentActivity close it, the child's
  items are its detail, thread/started's agent_role names it, the app-server ending clears it;
- OpenCode v1: the parent's `task` tool part opens (pending/running) and closes (completed/error); the child session's tool
  parts (metadata.sessionId or session.created parentID) are its detail;
- OpenCode v2: a child session (session.created parentID) opens, session.execution.* end closes, session.tool.* is detail;
- Claude: a sub-agent's own tool calls (stream parent_tool_use_id / shared PreToolUse with agent_id) are its detail;
- detail: who / label / last / report (systemd unit → REPORT first line; item file --report), redacted; unknown id → None;
- the same lost-close expiry (TEMP_TTL) for all three harnesses; serve answers busy_get to a ready device only.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import busy, serve  # noqa: E402
from agentj.state import State  # noqa: E402


def spawn(method, iid, prompt, recv=(), status="inProgress", states=None, th="main"):
    return method, {"threadId": th, "item": {"type": "collabAgentToolCall", "id": iid, "tool": "spawnAgent",
                                             "prompt": prompt, "receiverThreadIds": list(recv), "senderThreadId": th,
                                             "status": status, "agentsStates": states or {}}}


class Codex(unittest.TestCase):
    def setUp(self):
        self.changes = 0
        self.b = busy.Board(None, on_change=self._ch)

    def _ch(self):
        self.changes += 1

    def ev(self, method, p):
        self.b.codex_event(method, p, "codex")

    def test_spawn_child_turn_close(self):
        self.ev(*spawn("item/started", "c1", "检查依赖版本\n细节……"))
        self.assertEqual([t["label"] for t in self.b.items()], ["检查依赖版本 细节……"])
        self.ev(*spawn("item/completed", "c1", "检查依赖版本\n细节……", ["T1"], "completed", {"T1": {"status": "running"}}))
        items = self.b.items()
        self.assertEqual(len(items), 1, "rekeyed onto the child thread, not a second robot")
        self.assertIn("codex:th:T1", self.b.mem)
        self.assertEqual(items[0]["kind"], "temp")
        self.assertIsNone(items[0]["who"])
        # the child's own work → detail only (no new message)
        n = self.changes
        self.ev("item/started", {"threadId": "T1", "item": {"type": "commandExecution", "id": "x",
                                                             "command": "/bin/bash -lc 'pip list --outdated'"}})
        self.assertEqual(self.changes, n)
        self.assertEqual(self.b.last["codex:th:T1"]["text"], "Bash · pip list --outdated")
        self.ev("turn/completed", {"threadId": "T1", "turn": {"id": "u", "status": "completed"}})
        self.assertEqual(self.b.items(), [])
        # the parent sends it more work: the same robot, the same label
        self.ev("turn/started", {"threadId": "T1", "turn": {"id": "u2"}})
        self.assertEqual([t["label"] for t in self.b.items()], ["检查依赖版本 细节……"])
        self.ev("item/completed", {"threadId": "main", "item": {"type": "collabAgentToolCall", "id": "w", "tool": "wait",
                                                                "receiverThreadIds": ["T1"], "status": "completed",
                                                                "agentsStates": {"T1": {"status": "completed"}}}})
        self.assertEqual(self.b.items(), [])

    def test_main_thread_turn_is_not_an_item(self):
        self.ev("turn/started", {"threadId": "main", "turn": {"id": "t"}})
        self.ev("item/started", {"threadId": "main", "item": {"type": "commandExecution", "command": "ls"}})
        self.assertEqual(self.b.items(), [])

    def test_failed_spawn_and_close_agent(self):
        self.ev(*spawn("item/started", "c2", "x"))
        self.ev(*spawn("item/completed", "c2", "x", (), "failed"))
        self.assertEqual(self.b.items(), [])
        self.ev(*spawn("item/completed", "c3", "y", ["T3"], "completed"))
        self.assertEqual(len(self.b.items()), 1)
        self.ev("item/completed", {"threadId": "main", "item": {"type": "collabAgentToolCall", "id": "k", "tool": "closeAgent",
                                                                "receiverThreadIds": ["T3"], "status": "completed",
                                                                "agentsStates": {}}})
        self.assertEqual(self.b.items(), [])

    def test_role_names_it_and_activity(self):
        self.ev("thread/started", {"thread": {"id": "T4", "source": {"subAgent": {"thread_spawn": {
            "parent_thread_id": "main", "depth": 1, "agent_role": "reviewer", "agent_nickname": "Ada"}}}}})
        self.ev(*spawn("item/completed", "c4", "审一遍 diff", ["T4"], "completed"))
        self.assertEqual(self.b.items()[0]["who"], "reviewer")
        self.ev("item/completed", {"threadId": "main", "item": {"type": "subAgentActivity", "id": "a", "agentThreadId": "T4",
                                                                "agentPath": "/root/reviewer", "kind": "completed"}})
        self.assertEqual(self.b.items(), [])
        # a generic built-in role stays 「临时助理」
        self.ev("thread/started", {"thread": {"id": "T5", "source": {"subAgent": {"thread_spawn": {
            "parent_thread_id": "main", "depth": 1, "agent_role": "worker"}}}}})
        self.ev("item/completed", {"threadId": "main", "item": {"type": "subAgentActivity", "id": "b", "agentThreadId": "T5",
                                                                "agentPath": "/root/worker", "kind": "started"}})
        self.assertEqual([(t["who"], t["label"]) for t in self.b.items()], [(None, "worker")])

    def test_real_codex_0159_capture(self):
        """Replayed from a live `codex app-server` 0.159.2 run (one spawn_agent): the spawn is only a subAgentActivity
        started item, the child thread sends its own turn/item events, then subAgentActivity completed."""
        seen, notes = [], []
        for line in (HERE / "fixtures" / "p160_codex_0159_subagent.jsonl").read_text(encoding="utf-8").splitlines():
            m = json.loads(line)
            self.ev(m["method"], m["params"])
            seen.append([t["label"] for t in self.b.items()])
            notes.append(dict(self.b.last))
        self.assertIn(["file count"], seen, "one robot while the sub-agent works")
        self.assertTrue(any(v.get("text") == "Bash · ls /tmp/p160live" for n in notes for v in n.values()))
        self.assertEqual(seen[-1], [], "gone when the sub-agent finished")
        # more work later for the same child (send_input): its turn re-opens the same robot with the same label
        child = next(k for k in self.b.child)
        self.ev("turn/started", {"threadId": child.split(":th:")[1], "turn": {"id": "again"}})
        self.assertEqual([t["label"] for t in self.b.items()], ["file count"])

    def test_child_turn_before_spawn_completed_gets_the_label(self):
        self.ev(*spawn("item/started", "c6", "整理日志"))
        self.ev("thread/started", {"thread": {"id": "T6", "source": {"subAgent": {"thread_spawn": {"parent_thread_id": "main",
                                                                                                    "depth": 1}}}}})
        self.ev("turn/started", {"threadId": "T6", "turn": {"id": "z"}})
        self.ev(*spawn("item/completed", "c6", "整理日志", ["T6"], "completed"))
        self.assertEqual([t["label"] for t in self.b.items()], ["整理日志"])

    def test_app_server_end_and_expiry(self):
        self.ev(*spawn("item/completed", "c7", "a", ["T7"], "completed"))
        self.b.session_gone("codex")
        self.assertEqual(self.b.items(), [])
        self.ev(*spawn("item/completed", "c8", "b", ["T8"], "completed"))
        import time
        self.assertEqual(self.b.items(now=time.time() + busy.TEMP_TTL + 1), [], "a lost close expires like Claude's")

    def test_agent_hook(self):
        """CodexAgent._busy feeds the host's Board (owned scope `codex`, shared `shared-codex`), skips workflow runs."""
        from agentj.agent_codex import CodexAgent
        b = busy.Board(None)
        a = CodexAgent.__new__(CodexAgent)
        a.host, a.cfg, a.research = SimpleNamespace(busy=b), {}, False
        a._busy(*spawn("item/completed", "c9", "跑测试", ["T9"], "completed"))
        self.assertIn("codex:th:T9", b.mem)
        a._busy(None, None)
        self.assertEqual(b.items(), [])
        a.cfg = {"_workflow_ceo": True}
        a._busy(*spawn("item/completed", "c10", "x", ["T10"], "completed"))
        self.assertEqual(b.items(), [])
        from agentj.shared_codex import SharedCodexAgent
        sa = SharedCodexAgent.__new__(SharedCodexAgent)
        self.assertEqual(sa.busy_scope(), "shared-codex")


def tool_part(sid, call, tool, status, inp=None, meta=None):
    return {"type": "message.part.updated", "properties": {"part": {
        "type": "tool", "id": "p" + call, "callID": call, "sessionID": sid, "tool": tool,
        "state": {"status": status, "input": inp or {}, **({"metadata": meta} if meta else {})}}}}


class OpenCodeV1(unittest.TestCase):
    def setUp(self):
        self.b = busy.Board(None)

    def ev(self, e):
        self.b.opencode_event(e, "S", "opencode")

    def test_task_lifecycle(self):
        inp = {"description": "查找调用点", "subagent_type": "general", "prompt": "…"}
        self.ev(tool_part("S", "k1", "task", "pending", inp))
        self.assertEqual([(t["who"], t["label"]) for t in self.b.items()], [(None, "查找调用点")])
        self.ev(tool_part("S", "k1", "task", "running", inp, {"sessionId": "C1"}))
        self.assertEqual(len(self.b.items()), 1)
        self.ev(tool_part("C1", "g1", "grep", "running", {"pattern": "busy_get"}))
        self.assertEqual(self.b.last["opencode:task:k1"]["text"], "grep · busy_get")
        self.ev(tool_part("S", "k1", "task", "completed", inp, {"sessionId": "C1"}))
        self.assertEqual(self.b.items(), [])

    def test_named_agent_and_error(self):
        self.ev(tool_part("S", "k2", "task", "running", {"description": "审代码", "subagent_type": "code-reviewer"}))
        self.assertEqual(self.b.items()[0]["who"], "code-reviewer")
        self.ev(tool_part("S", "k2", "task", "error", {}))
        self.assertEqual(self.b.items(), [])

    def test_other_session_and_other_tools_ignored(self):
        self.ev(tool_part("OTHER", "k3", "task", "running", {"description": "x"}))
        self.ev(tool_part("S", "k4", "bash", "running", {"command": "ls"}))
        self.assertEqual(self.b.items(), [])

    def test_child_by_session_created(self):
        self.ev(tool_part("S", "k5", "task", "running", {"description": "写文档"}))
        self.ev({"type": "session.created", "properties": {"info": {"id": "C5", "parentID": "S", "title": "写文档 (@general subagent)"}}})
        self.ev(tool_part("C5", "r", "read", "running", {"filePath": "/tmp/a.md"}))
        self.assertEqual(self.b.last["opencode:task:k5"]["text"], "read · /tmp/a.md")

    def test_real_opencode_1_18_capture(self):
        """Replayed from a live `opencode serve` 1.18.34 run (one task call, DeepSeek): task pending → session.created
        (parentID) → task running (metadata.sessionId) → the child's bash parts → task completed."""
        lines = (HERE / "fixtures" / "p160_opencode_1_18_task.jsonl").read_text(encoding="utf-8").splitlines()
        evs = [json.loads(x) for x in lines]
        sid = next(e["properties"]["info"]["id"] for e in evs if e["type"] == "session.created")
        seen, notes = [], []
        for e in evs:
            self.b.opencode_event(e, sid, "opencode")
            seen.append([(t["who"], t["label"]) for t in self.b.items()])
            notes.append({k: v["text"] for k, v in self.b.last.items()})
        self.assertIn([(None, "count files")], seen)
        self.assertTrue(any("bash · ls /tmp/p160live | wc -l" in n.values() for n in notes))
        self.assertEqual(seen[-1], [])

    def test_agent_hook(self):
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.agent_opencode2 import OpenCodeV2Agent
        b = busy.Board(None)
        a = OpenCodeAgent.__new__(OpenCodeAgent)
        a.host, a.cfg, a.research, a.sid = SimpleNamespace(busy=b), {}, False, "S"
        a._busy(tool_part("S", "k6", "task", "running", {"description": "v1"}))
        self.assertIn("opencode:task:k6", b.mem)
        a._busy(None)
        self.assertEqual(b.items(), [])
        a2 = OpenCodeV2Agent.__new__(OpenCodeV2Agent)
        a2.host, a2.cfg, a2.research, a2.sid = SimpleNamespace(busy=b), {}, False, "S"
        a2._busy({"type": "session.created", "data": {"info": {"id": "C9", "parentID": "S", "title": "v2 (@explore subagent)"}}})
        self.assertEqual([t["label"] for t in b.items()], ["v2"])
        from agentj.shared import SharedOpenCodeAgent
        from agentj.shared_opencode2 import OwnerOpenCodeV2Agent
        self.assertEqual(SharedOpenCodeAgent.__new__(SharedOpenCodeAgent).busy_scope(), "shared-opencode")
        self.assertEqual(OwnerOpenCodeV2Agent.__new__(OwnerOpenCodeV2Agent).busy_scope(), "shared-opencode")


class OpenCodeV2(unittest.TestCase):
    def setUp(self):
        self.b = busy.Board(None)

    def ev(self, t, **data):
        self.b.opencode2_event({"type": t, "data": data}, "S", "opencode")

    def test_child_session(self):
        self.ev("session.created", info={"id": "C1", "parentID": "S", "title": "整理 CHANGELOG (@docs-writer subagent)"})
        self.assertEqual([(t["who"], t["label"]) for t in self.b.items()], [("docs-writer", "整理 CHANGELOG")])
        self.ev("session.tool.started", sessionID="C1", tool="shell", input={"command": "git log -5"})
        self.assertEqual(self.b.last["opencode:s:C1"]["text"], "shell · git log -5")
        self.ev("session.execution.succeeded", sessionID="C1")
        self.assertEqual(self.b.items(), [])
        self.ev("session.execution.started", sessionID="C1")
        self.assertEqual(len(self.b.items()), 1, "more work for the same child: back")
        self.ev("session.execution.interrupted", sessionID="C1")
        self.assertEqual(self.b.items(), [])

    def test_not_ours(self):
        self.ev("session.created", info={"id": "X", "parentID": "OTHER", "title": "x"})
        self.ev("session.created", info={"id": "S2", "title": "a root session"})
        self.ev("session.execution.started", sessionID="S")
        self.assertEqual(self.b.items(), [])


class ClaudeDetail(unittest.TestCase):
    def test_stream_subagent_tool_is_detail(self):
        b = busy.Board(None)
        b.stream_event({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Agent",
                                                                      "input": {"description": "转录"}}]}})
        b.stream_event({"type": "assistant", "parent_tool_use_id": "tu1", "message": {"content": [
            {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "ffmpeg -i a.mp3 a.wav",
                                                                      "description": "转成 WAV"}}]}})
        self.assertEqual(b.last["own:tu:tu1"]["text"], "Bash · 转成 WAV")
        # background launch: the rekeyed item keeps its detail and receives new ones
        b.stream_event({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                                 "content": "Async agent launched agentId: abc123"}]}})
        self.assertIn("own:task:abc123", b.last)
        b.stream_event({"type": "assistant", "parent_tool_use_id": "tu1", "message": {"content": [
            {"type": "tool_use", "id": "y", "name": "Read", "input": {"file_path": "/tmp/a.txt"}}]}})
        self.assertEqual(b.last["own:task:abc123"]["text"], "Read · /tmp/a.txt")

    def test_shared_hook_subagent_tool_is_detail(self):
        b = busy.Board(None)
        b.hook_event({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Agent",
                      "tool_input": {"description": "巡检", "subagent_type": "general-purpose"}})
        b.hook_event({"hook_event_name": "SubagentStart", "session_id": "s", "agent_id": "a1", "agent_type": "general-purpose"})
        b.hook_event({"hook_event_name": "PreToolUse", "session_id": "s", "agent_id": "a1", "tool_name": "Grep",
                      "tool_input": {"pattern": "TODO"}})
        self.assertEqual(b.last["shared:s:a1"]["text"], "Grep · TODO")
        b.hook_event({"hook_event_name": "SubagentStop", "session_id": "s", "agent_id": "a1"})
        b.items()
        self.assertEqual(b.last, {}, "detail goes with the item")


class Detail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tool_desc(self):
        self.assertEqual(busy.tool_desc("Bash", {"command": "ls\n-la"}), "Bash · ls -la")
        self.assertEqual(busy.tool_desc("TodoWrite", {}), "TodoWrite")
        self.assertEqual(len(busy.tool_desc("Bash", {"command": "x" * 999})), busy.LAST_MAX)
        self.assertEqual(busy.tool_desc(None, None), "")

    def test_report_head(self):
        p = self.root / "REPORT-7.md"
        p.write_text("\n\n# 完成 3/3 条\n第二行\n", encoding="utf-8")
        r = busy.report_head(str(p))
        self.assertEqual(r["head"], "完成 3/3 条")
        self.assertIsInstance(r["at"], int)
        missing = busy.report_head(str(self.root / "nope.md"))
        self.assertEqual((missing["head"], missing["at"]), (None, None))
        link = self.root / "link.md"
        link.symlink_to(p)
        self.assertIsNone(busy.report_head(str(link))["head"], "a symlink is not followed")

    def test_unit_report(self):
        jar = self.root / ".jarvis"
        jar.mkdir()
        script = jar / "run-px.sh"
        script.write_text("#!/bin/sh\n")

        def run(argv, **kw):
            self.assertEqual(argv[:3], ["systemctl", "--user", "show"])
            return SimpleNamespace(stdout=f"Environment=RESUME=1\nWorkingDirectory=!{self.root}\n"
                                          f"ExecStart={{ path=/usr/bin/bash ; argv[]=/usr/bin/bash {script} 160 wt claude ; "
                                          "ignore_errors=no }\n")
        self.assertEqual(busy.unit_report("agentj-p160.service", run), str(jar / "REPORT-160.md"), "not written yet")
        (self.root / "REPORT-160.md").write_text("x")
        self.assertEqual(busy.unit_report("agentj-p160.service", run), str(self.root / "REPORT-160.md"))

        def run_env(argv, **kw):
            return SimpleNamespace(stdout="Environment=A=1 AGENTJ_REPORT=/srv/r.md\nWorkingDirectory=/\n")
        self.assertEqual(busy.unit_report("agentj-p9.service", run_env), "/srv/r.md")
        self.assertIsNone(busy.unit_report("sshd.service", run_env))

    def test_board_detail(self):
        rep = self.root / "R.md"
        rep.write_text("## P9 完成：token sk-ant-api03-" + "A" * 90 + "\n", encoding="utf-8")
        busy.write_item(self.root, busy.item("cli:x", "守望", "ceo", "备份日志", pid=os.getpid(), report=str(rep)))
        b = busy.Board(self.root)
        b.refresh_outside(units=False)
        b.start("codex:th:T1", None, "temp", "检查依赖", session="codex")
        b.note("codex:th:T1", "Bash · pip list")
        ids = {t["label"]: t["id"] for t in b.items()}
        d = b.detail(ids["备份日志"])
        self.assertEqual((d["who"], d["src"], d["kind"]), ("守望", "file", "ceo"))
        self.assertTrue(d["report"]["head"].startswith("P9 完成"))
        self.assertNotIn("A" * 40, d["report"]["head"], "secrets redacted")
        d2 = b.detail(ids["检查依赖"])
        self.assertEqual((d2["src"], d2["last"]["text"]), ("codex", "Bash · pip list"))
        self.assertNotIn("report", d2)
        b.start("shared-codex:th:T2", None, "temp", "共享线程的子 Agent", session="shared-codex")
        b.start("shared-opencode:task:k", None, "temp", "共享 OpenCode", session="shared-opencode")
        b.start("shared-claude:s:a", None, "temp", "共享 Claude", session="shared-claude:s")
        srcs = {t["label"]: b.detail(t["id"])["src"] for t in b.items()}
        self.assertEqual((srcs["共享线程的子 Agent"], srcs["共享 OpenCode"], srcs["共享 Claude"]), ("codex", "opencode", "claude"))
        self.assertIsNone(b.detail("gone"))
        self.assertIsNone(b.detail(None))
        json.dumps(d)

    def test_unit_detail(self):
        b = busy.Board(None)
        rep = self.root / "REPORT-160.md"
        rep.write_text("完成条目数：3/3\n")

        def run(argv, **kw):
            if argv[2] == "list-units":
                return SimpleNamespace(stdout="agentj-p160.service loaded active running 掌枢 · harness 对等\n")
            return SimpleNamespace(stdout=f"Environment=AGENTJ_REPORT={rep}\n")
        b.refresh_outside(run=run)
        wid = b.items()[0]["id"]
        d = b.detail(wid, run=run)
        self.assertEqual((d["src"], d["who"], d["label"]), ("unit", "掌枢", "harness 对等"))
        self.assertEqual(d["report"]["head"], "完成条目数：3/3")

    def test_cli_show(self):
        from agentj import cli  # noqa: F401 — argparse wiring
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="cmd")
        busy.add_parser(sub)
        a = ap.parse_args(["busy", "show", "abc", "--json", "--no-units"])
        self.assertEqual((a.busy_cmd, a.id, a.json), ("show", "abc", True))
        a = ap.parse_args(["busy", "run", "--who", "掌枢", "--report", "/tmp/R.md", "--", "true"])
        self.assertEqual(a.report, "/tmp/R.md")


class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cap_and_answer(self):
        self.assertIn("busy_get", serve.P33_ONLY)
        s = SimpleNamespace(cid="c", p33=True, state="ready", pub=b"k", device="d")
        self.assertIn("busyget", self.host._caps(s)["caps"])
        got = []

        async def send_app(sess, obj):
            got.append(obj)
            return True
        self.host.send_app = send_app
        self.host.sessions = {"c": s}
        self.st.is_allowed = lambda pub: True
        self.host._post = lambda *a, **k: None
        self.host.busy.set_context("opencode", pid=os.getpid())
        self.host.busy.start("opencode:task:k", "code-reviewer", "temp", "审代码", session="opencode")
        self.host.busy.note("opencode:task:k", "read · a.py")
        self.host.busy.refresh_sources()
        wid = self.host.busy.items()[0]["id"]
        asyncio.run(self.host.on_busy_get(s, "r1", wid))
        self.assertEqual(got[-1]["t"], "busy_info")
        self.assertEqual(got[-1]["r"], "r1")
        self.assertEqual(got[-1]["item"]["last"]["text"], "read · a.py")
        asyncio.run(self.host.on_busy_get(s, "r2", "nope"))
        self.assertIsNone(got[-1]["item"])
        s.state = "pending"                          # not ready any more: nothing is sent
        n = len(got)
        asyncio.run(self.host.on_busy_get(s, "r3", wid))
        self.assertEqual(len(got), n)


if __name__ == "__main__":
    unittest.main()
