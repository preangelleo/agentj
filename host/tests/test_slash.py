"""Slash commands from the phone + Codex app-server (PROMPT-26 item 7a; ADR-A70 – A72; PROTOCOL §8 "slash commands").

Units: parsing (a path is not a command), the whitelist, Claude Code's control-request allowlist (never a subtype that changes
a permission, a mode or a setting), number formatting, Codex request → card mapping (shell wrapper, patches incl. delete).
Chains with stand-ins inside the fence (tests/fakeclaude.py, fakecodex.py, fakeopencode.py) — for each harness:
/context /compact /cost /usage /status /model (list → buttons, set → config.json) /clear (confirm, new conversation, undo
→ the old one again) /stop (this turn only, nothing paused) /help, a refused command; Claude Code: a skill passes through,
the compaction replay is not pushed; Codex: the thread asks for `untrusted` + reviewer `user`, every command / patch /
permission request becomes a phone card (danger one by one, batch for low risk, deny → declined, never acceptForSession),
the stop switch and /stop interrupt the turn, an `exec`-era thread id is resumed, scheduled runs (research = read-only
sandbox + non-read-only requests declined; normal = card with the task's name); OpenCode: summarize (the summary is not a
reply), abort on /stop and the stop switch, a scheduled run in its own session with deny-instead-of-ask rules.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import agent as agents  # noqa: E402
from agentj import agent_codex as cx  # noqa: E402
from agentj import agent_opencode as oc  # noqa: E402
from agentj import activity, approvals, danger, fence, slash, tasks  # noqa: E402

from test_danger import _sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402
from test_controls import sign as _signed, task_dir  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


# ------------------------------------------------------------------ units
class Units(unittest.TestCase):
    def test_parse_and_whitelist(self):
        self.assertEqual(slash.parse("/compact"), ("compact", ""))
        self.assertEqual(slash.parse("  /Model  claude-haiku-4-5 "), ("model", "claude-haiku-4-5"))
        self.assertEqual(slash.parse("/fake-skill do it"), ("fake-skill", "do it"))
        for not_cmd in ("/srv/www/x 这个路径", "看看 /compact", "/", "/ compact", "//x", "/中文", None, 3):
            self.assertIsNone(slash.parse(not_cmd), not_cmd)
        self.assertEqual(set(slash.WHITELIST), {"clear", "compact", "model", "context", "cost", "usage", "status", "help", "stop"})
        self.assertEqual(slash.parse("/x " + "a" * 500)[1], "a" * slash.ARG_MAX)

    def test_control_requests_never_widen(self):
        self.assertEqual(set(agents.CONTROL_SUBTYPES), {"interrupt", "get_context_usage", "get_status", "list_models", "set_model"})
        for bad in ("set_permission_mode", "apply_flag_settings", "update_settings", "mcp_set_servers", "mcp_toggle",
                    "initialize", "can_use_tool", "rewind_files"):
            self.assertNotIn(bad, agents.CONTROL_SUBTYPES)
            a = agents.ClaudeAgent(None, {"kind": "claude", "dir": "/tmp"})
            with self.assertRaises(ValueError):
                asyncio.run(a.control(bad))

    def test_formatting(self):
        self.assertEqual((slash.tokens(21262), slash.tokens(1344), slash.tokens(999), slash.tokens(None)),
                         ("21.3k", "1.3k", "999", "—"))
        self.assertEqual(slash.pct(20761, 200000), "（10%）")
        self.assertEqual(slash.window(10080), "7 天窗口")
        self.assertEqual(slash.window(300), "5 小时窗口")
        self.assertEqual(slash.money(0.0123), "$0.0123")
        self.assertEqual(slash.money(None), "—")

    def test_codex_never_more_power_than_its_own_sandbox(self):
        b = lambda sb, cmd, **p: cx.beyond_sandbox(sb, "item/commandExecution/requestApproval", "Bash", {"command": cmd}, p, None, "/w")  # noqa: E731
        ro, ww, full = {"type": "readOnly"}, {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False}, {"type": "dangerFullAccess"}
        self.assertIsNone(b(full, "rm -rf /"))                         # full access already: an approval adds nothing
        self.assertIsNone(b(ro, "stat a.txt"))
        self.assertIsNone(b(ro, "git log --oneline"))
        for cmd in ("rm a", "touch a", "cat a > b", "git commit -m x", "python3 x.py", "curl -s https://x", "sed -i s/a/b/ f"):
            self.assertIsNotNone(b(ro, cmd), cmd)
            self.assertIsNotNone(b(ww, cmd), cmd)
        self.assertIsNone(b({**ww, "networkAccess": True}, "curl -s https://x"))
        self.assertIsNotNone(b(None, "touch a"), "unknown sandbox = read-only")
        self.assertIsNotNone(b(ro, "ls", networkApprovalContext={"host": "x"}))
        self.assertIsNotNone(b(ro, "ls", kind="writeStdin"))
        self.assertIsNotNone(b(ro, "ls", additionalPermissions={"network": {"enabled": True}}))
        p = lambda sb, path: cx.beyond_sandbox(sb, "item/fileChange/requestApproval", "Write", {}, {}, [{"path": path}], "/w")  # noqa: E731
        self.assertIsNone(p(ww, "/w/a.txt"))
        self.assertIsNotNone(p(ro, "/w/a.txt"))
        for bad in ("/etc/passwd", "/w/../x", "/w/.git/config", "/w/.codex/config.toml"):
            self.assertIsNotNone(p(ww, bad), bad)
        self.assertIsNone(p({**ww, "writableRoots": ["/srv/data"]}, "/srv/data/x"))
        self.assertIsNotNone(cx.beyond_sandbox(ww, "item/permissions/requestApproval", "CodexPermissions", {}, {}, None, "/w"))

    def test_codex_requests_map_to_cards_the_danger_list_understands(self):
        self.assertEqual(cx.unwrap_shell("/usr/bin/bash -lc 'rm -rf build'"), "rm -rf build")
        self.assertEqual(cx.unwrap_shell("ls -la"), "ls -la")
        t, i = cx.patch_tool([{"path": "/w/a.txt", "kind": {"type": "add"}, "diff": "hi\n"}])
        self.assertEqual((t, i["content"]), ("Write", "hi\n"))
        t, i = cx.patch_tool([{"path": "/w/a.txt", "kind": {"type": "update"}, "diff": "@@\n-old\n+new\n"}])
        self.assertEqual((t, i["old_string"], i["new_string"]), ("Edit", "old", "new"))
        t, i = cx.patch_tool([{"path": "/w/a.txt", "kind": {"type": "delete"}, "diff": ""}])
        self.assertEqual(t, "Delete")
        self.assertEqual(danger.classify(t, i).cats, ["delete"])
        t, i = cx.patch_tool([{"path": "/w/a.txt", "kind": {"type": "update"}}, {"path": "/w/.env", "kind": {"type": "update"}}])
        self.assertEqual(i["file_path"], "/w/.env")
        self.assertIn("credentials", danger.classify(t, i).cats)
        self.assertIn("/w/a.txt", i["new_string"])
        t, i = cx.patch_tool([{"path": "/w/a.txt", "kind": {"type": "update"}}, {"path": "/w/b.txt", "kind": {"type": "delete"}}])
        self.assertEqual((t, i["file_path"]), ("Delete", "/w/b.txt"))
        self.assertIsNone(danger.batch_scope("Delete", {"file_path": "/w/b.txt"}, "/w"), "a delete never batches")
        self.assertIn("删除 /w/a.txt", agents.summarize("Delete", {"file_path": "/w/a.txt"}))
        self.assertIn("写入：/etc", agents.summarize("CodexPermissions", {"permissions": cx.perm_text({"fileSystem": {"write": ["/etc"]}})}))


# ------------------------------------------------------------------ chains (stand-in harnesses inside the fence)
class _Chain(unittest.TestCase):
    KIND = "?"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        (self.work / "victim.txt").write_text("keep me\n")
        self.log = self.work / ".fake.jsonl"          # inside the agent's folder: the fence hides the rest of /tmp
        fake = {"claude": "fakeclaude.py", "codex": "fakecodex.py", "opencode": "fakeopencode.py"}[self.KIND]
        wrapper = pathlib.Path(self.tmp.name) / self.KIND
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / fake} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = {"claude": {"AGENTJ_CLAUDE_BIN": str(wrapper), "FAKE_CLAUDE_LOG": str(self.log)},
                    "codex": {"AGENTJ_CODEX_BIN": str(wrapper), "FAKE_CX_LOG": str(self.log)},
                    "opencode": {"AGENTJ_OPENCODE_BIN": str(wrapper), "FAKE_OC_LOG": str(self.log),
                                 "AGENTJ_TEST_OC_WATCH": "1"}}[self.KIND]
        os.environ.update(self.env)
        self.fenced = fence.problem(self.st, str(self.work)) is None
        if sys.platform.startswith("linux") and shutil.which("bwrap"):
            self.assertTrue(self.fenced, "the stand-in runs inside the fence where bubblewrap works")
        self.st.set_agent_config(self.KIND, str(self.work), fence=self.fenced)
        from agentj import preferences
        preferences.path().parent.mkdir(parents=True, exist_ok=True)
        preferences.path().write_text(preferences.edit(preferences.read()[0], "agent.high_risk_warnings", True))

    def tearDown(self):
        for k in list(self.env) + ["FAKE_CX_THREADS", "FAKE_CX_POLICY", "FAKE_CX_SANDBOX", "FAKE_OC_SESSIONS"]:
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def logged(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_chain(self, script, ttl=6):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = ttl
        oc.WATCH = 1.0

        def msgs(frm=("agent", "notice")):
            return [o for _, o in sent if o["t"] == "msg" and o.get("from") in frm]

        def cards(name=None):
            return [o for _, o in sent if o["t"] == "msg" and o.get("from") == "cmd" and (name is None or o.get("cmd") == name)]

        async def wait(pred, ms=20000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-6:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = _ready(host, ph)
            n = [0]

            async def say(text):
                n[0] += 1
                await host._app(s, {"t": "msg", "id": f"{n[0]:016x}", "text": text, "ts": 0})

            async def cmd(name, arg="", confirm=False, want=None):
                k = len(cards(want or name))
                await host._app(s, {"t": "slash", "cmd": name, "arg": arg, **({"confirm": True} if confirm else {})})
                await wait(lambda: len(cards(want or name)) > k and cards(want or name)[-1]["text"] != "这一轮结束后执行。")
                return cards(want or name)[-1]

            async def idle():
                await wait(lambda: host.agent.status == "idle" and host.agent.q.empty())
            ctx = dict(host=host, sent=sent, msgs=msgs, cards=cards, wait=wait, say=say, cmd=cmd, idle=idle, s=s, ph=ph,
                       asks=lambda: [o for _, o in sent if o["t"] == "ask"],
                       statuses=lambda: [o["s"] for _, o in sent if o["t"] == "status"])
            try:
                await script(ctx)
            finally:
                host.stopping.set()
                await run
        asyncio.run(go())

    def assert_activity_has_no_text(self, *needles):
        recs = [json.loads(x) for f in (self.st.root / "activity").glob("*.jsonl") for x in f.read_text().splitlines()]
        sl = [r for r in recs if r.get("k") == "slash"]
        self.assertTrue(sl, "every command is a line in the activity record")
        self.assertTrue(all(set(r) <= {"k", "ts", "cmd", "result", "by"} for r in sl), sl[:3])
        log = self.st.log_path.read_text()
        for n in needles:
            self.assertNotIn(n, log)
        return sl


class ClaudeChain(_Chain):
    KIND = "claude"

    def test_commands(self):
        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            first = self.st.agent_session("claude")
            # /context /usage /status /cost: cards, no reply, no model turn
            ctx = await c["cmd"]("context")
            self.assertTrue(ctx["text"].startswith("上下文 20.8k / 200.0k（10%）"), ctx["text"])
            self.assertEqual((ctx["ok"], ctx["kind"]), (True, "ok"))
            u = await c["cmd"]("usage")
            self.assertIn("5 小时窗口已用 45%", u["text"])
            self.assertIn("7 天窗口已用 47%", u["text"])
            st = await c["cmd"]("status")
            self.assertIn("2.1.285-fake", st["text"])
            for hidden in ("Auth token", "CLAUDE_CODE_OAUTH_TOKEN", "Peer address", "uds:"):
                self.assertNotIn(hidden, st["text"])
            cost = await c["cmd"]("cost")
            self.assertIn("$0.0123", cost["text"])
            # /compact: a card with the numbers; the kept synthetic message Claude Code re-sends is NOT pushed as a reply
            n0 = len(c["msgs"]())
            comp = await c["cmd"]("compact")
            self.assertTrue(comp["text"].startswith("已压缩：21.3k → 1.3k tokens"), comp["text"])
            await c["idle"]()
            self.assertFalse(any("Set model to" in m["text"] or "Total cost" in m["text"] for m in c["msgs"]()[n0:]),
                             c["msgs"]()[n0:])
            self.assertIn("compacting", c["statuses"]())
            # /model: the list as buttons, then a choice → set_model + config.json
            ml = await c["cmd"]("model")
            self.assertEqual([m["id"] for m in ml["models"]], ["default", "haiku"])
            mset = await c["cmd"]("model", "haiku")
            self.assertTrue(mset["ok"], mset)
            self.assertEqual(self.st.agent_config()["model"], "haiku")
            self.assertEqual(c["host"].agent_cfg["model"], "haiku")
            bad = await c["cmd"]("model", "x; rm -rf /")
            self.assertEqual(bad["kind"], "error")
            # typed: a skill passes through; a built-in that is not on the list is refused (never reaches Claude Code)
            await c["say"]("/fake-skill")
            await c["wait"](lambda: any(m["text"] == "SKILL fake-skill ran" for m in c["msgs"]()))
            await c["say"]("/config")
            await c["wait"](lambda: c["cards"]("refused"))
            self.assertEqual(c["cards"]("refused")[-1]["text"], slash.REFUSE)
            await c["say"]("/compact")                 # typed works like the button
            await c["wait"](lambda: len(c["cards"]("compact")) == 2)
            await c["idle"]()
            # /clear: typed needs the phone's confirmation; confirmed → a new conversation, undo → the old one again
            await c["say"]("/clear")
            await c["wait"](lambda: c["cards"]("clear"))
            self.assertEqual(self.st.agent_session("claude"), first)
            cl = await c["cmd"]("clear", confirm=True)
            self.assertTrue(cl["sep"] and cl["undo"], cl)
            self.assertIsNone(self.st.agent_session("claude"))
            self.assertEqual(self.st.agent_session("claude.prev"), first)
            await c["say"]("新的")
            await c["wait"](lambda: any(m["text"] == "ECHO: 新的" for m in c["msgs"]()))
            self.assertNotEqual(self.st.agent_session("claude"), first)
            un = await c["cmd"]("undo_clear")
            self.assertTrue(un["sep"] and not un.get("undo"), un)
            self.assertEqual(self.st.agent_session("claude"), first)
            await c["say"]("回来了")
            await c["wait"](lambda: any(m["text"] == "ECHO: 回来了" for m in c["msgs"]()))
            argvs = [x["argv"] for x in self.logged() if "argv" in x]
            self.assertEqual(argvs[-1][argvs[-1].index("--resume") + 1], first, "undo: Claude Code resumed the old session")
            self.assertEqual(argvs[-1][argvs[-1].index("--model") + 1], "haiku", "the chosen model survives a restart")
            again = await c["cmd"]("undo_clear")
            self.assertEqual(again["kind"], "error")
            # /stop during a turn: this turn only; the next message works; nothing paused
            await c["say"]("SLOW")
            await c["wait"](lambda: c["host"].agent.status == "working")
            stop = await c["cmd"]("stop")
            self.assertTrue(stop["text"].startswith("已停下这一轮"), stop)
            self.assertFalse(c["host"].stopped())
            await c["say"]("还在吗")
            await c["wait"](lambda: any(m["text"] == "ECHO: 还在吗" for m in c["msgs"]()))
            await c["wait"](lambda: c["host"].agent.status == "idle")   # the reply lands just before the turn's end
            nothing = await c["cmd"]("stop")
            self.assertEqual(nothing["kind"], "info")
            hp = await c["cmd"]("help")
            self.assertIn("/fake-skill", hp["text"])
            # a command while a turn runs waits for it (queued), then runs
            await c["say"]("SLOW")
            await c["host"]._app(c["s"], {"t": "slash", "cmd": "context"})
            await c["wait"](lambda: c["cards"]("context")[-1]["text"] == "这一轮结束后执行。")
            await c["wait"](lambda: c["cards"]("context")[-1]["text"].startswith("上下文"))
            await c["wait"](lambda: any(m["text"] == "慢回复" for m in c["msgs"]()))
            # stopped: commands other than /help are refused
            await c["host"].do_estop("terminal", "终端")
            r = await c["cmd"]("compact")
            self.assertEqual(r["kind"], "refused")
        self.run_chain(script)
        subs = {x["control"] for x in self.logged() if "control" in x}
        self.assertTrue(subs <= set(agents.CONTROL_SUBTYPES), subs)
        self.assertTrue({"get_context_usage", "get_status", "list_models", "set_model"} <= subs, subs)
        sl = self.assert_activity_has_no_text("fake-skill ran", "21262")
        self.assertIn(("compact", "ok"), {(r["cmd"], r["result"]) for r in sl})
        self.assertIn(("other", "refused"), {(r["cmd"], r["result"]) for r in sl})


class CodexChain(_Chain):
    KIND = "codex"

    def test_approvals_on_the_phone(self):
        async def script(c):
            host, ph, s = c["host"], c["ph"], c["s"]
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            # rm → a danger card (Codex's bash wrapper removed) → deny → declined, file stays
            await c["say"]("RUN: rm victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 1)
            a = c["asks"]()[-1]
            self.assertEqual((a["tool"], a["summary"], a["cat"]), ("Bash", "rm victim.txt", ["delete"]))
            self.assertNotIn("batch", a)
            await c["wait"](lambda: c["statuses"]()[-1:] == ["waiting"])
            await host._app(s, ph.answer(a, False))
            await c["wait"](lambda: any(m["text"] == "declined: rm victim.txt" for m in c["msgs"]()))
            self.assertTrue((self.work / "victim.txt").exists())
            # approve → runs
            await c["say"]("RUN: touch made.txt")
            await c["wait"](lambda: len(c["asks"]()) == 2)
            await host._app(s, ph.answer(c["asks"]()[-1], True))
            await c["wait"](lambda: any(m["text"] == "ran: touch made.txt" for m in c["msgs"]()))
            self.assertTrue((self.work / "made.txt").exists())
            # batch for low risk; rm in the same turn still asks
            await c["say"]("RUNSEQ: touch b1.txt ;; touch b2.txt ;; rm victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 3)
            low = c["asks"]()[-1]
            self.assertTrue(low.get("batch", "").startswith("Bash：touch"), low)
            await host._app(s, _sign(ph, low, "allow_batch", low["batch"]))
            await c["wait"](lambda: len(c["asks"]()) == 4)
            self.assertTrue((self.work / "b2.txt").exists())
            self.assertTrue(any(o["t"] == "auto" for _, o in c["sent"]))
            self.assertEqual(c["asks"]()[-1]["cat"], ["delete"])
            await host._app(s, ph.answer(c["asks"]()[-1], False))
            await c["idle"]()
            # a read-only command Codex knows: no card
            await c["say"]("RUN: ls")
            await c["wait"](lambda: any(m["text"] == "ran: ls" for m in c["msgs"]()))
            self.assertEqual(len(c["asks"]()), 4)
            # patches: delete = a danger card; add = low risk
            await c["say"]("PATCH: delete victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 5)
            d = c["asks"]()[-1]
            self.assertEqual((d["tool"], d["cat"]), ("Delete", ["delete"]))
            await host._app(s, ph.answer(d, False))
            await c["wait"](lambda: any(m["text"] == "patch declined: victim.txt" for m in c["msgs"]()))
            self.assertTrue((self.work / "victim.txt").exists())
            await c["say"]("PATCH: add new.txt")
            await c["wait"](lambda: len(c["asks"]()) == 6)
            w = c["asks"]()[-1]
            self.assertEqual((w["tool"], w["cat"]), ("Write", []))
            await host._app(s, ph.answer(w, True))
            await c["wait"](lambda: (self.work / "new.txt").exists())
            # a permission escalation: a card that never batches; deny → nothing granted
            await c["say"]("PERM: /etc")
            await c["wait"](lambda: len(c["asks"]()) == 7)
            p = c["asks"]()[-1]
            self.assertEqual(p["tool"], "CodexPermissions")
            self.assertNotIn("batch", p)
            await host._app(s, ph.answer(p, False))
            await c["wait"](lambda: any(m["text"] == "granted: {}" for m in c["msgs"]()))
            # a network request: a card without batch
            await c["say"]("NET: curl -s https://example.com")
            await c["wait"](lambda: len(c["asks"]()) == 8)
            self.assertNotIn("batch", c["asks"]()[-1])
            self.assertIn("要联网", c["asks"]()[-1]["summary"])
            await host._app(s, ph.answer(c["asks"]()[-1], False))
            # nobody answers → declined after the ttl
            await c["say"]("RUN: touch late.txt")
            await c["wait"](lambda: len(c["asks"]()) == 9)
            await c["wait"](lambda: any(m["text"] == "declined: touch late.txt" for m in c["msgs"]()), 15000)
            await c["idle"]()
        self.run_chain(script, ttl=3)
        lg = self.logged()
        starts = [x["params"] for x in lg if x.get("method") == "thread/start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual((starts[0]["approvalPolicy"], starts[0]["approvalsReviewer"]), ("untrusted", "user"))
        self.assertNotIn("sandbox", starts[0], "the human's sandbox stays theirs")
        answers = [x["answer"] for x in lg if "answer" in x]
        decisions = [a.get("decision") for a in answers if "decision" in a]
        self.assertEqual(decisions, ["decline", "accept", "accept", "accept", "decline", "decline", "accept", "decline", "decline"])
        self.assertTrue(all(d in ("accept", "decline") for d in decisions), "never acceptForSession / an amendment")
        self.assertIn({"permissions": {}, "scope": "turn"}, answers)
        self.assertEqual([(x["decision"], x["reason"]) for x in approvals.read_log(self.st)],
                         [("deny", "device"), ("allow", "device"), ("allow_batch", "device"), ("allow", "batch"),
                          ("deny", "device"), ("deny", "device"), ("allow", "device"), ("deny", "device"), ("deny", "device"),
                          ("deny", "timeout")])
        self.assertNotIn("victim", self.st.log_path.read_text())

    def test_commands_clear_undo_stop_estop_and_migration(self):
        os.environ["FAKE_CX_THREADS"] = json.dumps(["exec-era-thread"])
        self.st.set_agent_session("codex", "exec-era-thread")     # a conversation `codex exec` started (L1 – item 6)

        async def script(c):
            host = c["host"]
            await c["say"]("一")
            await c["wait"](lambda: any(m["text"] == "ECHO: 一" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.st.agent_session("codex"), "exec-era-thread")
            ctx = await c["cmd"]("context")
            self.assertTrue(ctx["text"].startswith("上下文 21.0k / 258.4k（8%）"), ctx["text"])
            comp = await c["cmd"]("compact")
            self.assertTrue(comp["text"].startswith("已压缩：21.0k → 4.7k tokens"), comp["text"])
            self.assertIn("compacting", c["statuses"]())
            u = await c["cmd"]("usage")
            self.assertIn("主额度已用 20%（7 天窗口", u["text"])
            cost = await c["cmd"]("cost")
            self.assertIn("美元：—", cost["text"])
            st = await c["cmd"]("status")
            self.assertIn("untrusted", st["text"])
            self.assertIn("0.159.2-fake", st["text"])
            ml = await c["cmd"]("model")
            self.assertEqual([m["id"] for m in ml["models"]], ["gpt-fake", "gpt-fake-mini"], "hidden models are not offered")
            bad = await c["cmd"]("model", "gpt-nope")
            self.assertEqual(bad["kind"], "error")
            ok = await c["cmd"]("model", "gpt-fake-mini")
            self.assertTrue(ok["ok"])
            self.assertEqual(self.st.agent_config()["model"], "gpt-fake-mini")
            await c["say"]("二")
            await c["wait"](lambda: any(m["text"] == "ECHO: 二" for m in c["msgs"]()))
            await c["idle"]()
            # /clear → a new thread; undo → the old one is resumed
            await c["cmd"]("clear", confirm=True)
            await c["say"]("三")
            await c["wait"](lambda: any(m["text"] == "ECHO: 三" for m in c["msgs"]()))
            await c["idle"]()
            fresh = self.st.agent_session("codex")
            self.assertNotEqual(fresh, "exec-era-thread")
            await c["cmd"]("undo_clear")
            await c["say"]("四")
            await c["wait"](lambda: any(m["text"] == "ECHO: 四" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.st.agent_session("codex"), "exec-era-thread")
            # /stop: turn/interrupt, nothing paused
            await c["say"]("SLEEP: 20")
            await c["wait"](lambda: host.agent.status == "working")
            await asyncio.sleep(0.3)
            stop = await c["cmd"]("stop")
            self.assertTrue(stop["text"].startswith("已停下这一轮"), stop)
            self.assertFalse(host.stopped())
            await c["say"]("五")
            await c["wait"](lambda: any(m["text"] == "ECHO: 五" for m in c["msgs"]()))
            # the stop switch interrupts too
            await c["say"]("SLEEP: 20")
            await c["wait"](lambda: host.agent.status == "working")
            await asyncio.sleep(0.3)
            await host._app(c["s"], _signed(c["ph"], "estop", {}, {"t": "estop", "r": "e1"}))
            await c["wait"](lambda: host.stopped() and host.agent.status != "working")
            self.assertFalse(any(m["text"] == "slept" for m in c["msgs"]()))
        self.run_chain(script)
        lg = self.logged()
        resumes = [x["params"]["threadId"] for x in lg if x.get("method") == "thread/resume"]
        self.assertEqual(resumes[0], "exec-era-thread", "an exec-era conversation goes on")
        self.assertIn("exec-era-thread", resumes[1:], "undo resumed the old thread")
        self.assertEqual(sum(1 for x in lg if x.get("method") == "turn/interrupt"), 2, "/stop and the stop switch")
        models = [x["params"].get("model") for x in lg if x.get("method") == "turn/start"]
        self.assertIn("gpt-fake-mini", models)
        self.assertEqual(sum(1 for x in lg if x.get("method") == "thread/compact/start"), 1)
        self.assert_activity_has_no_text()

    def test_read_only_sandbox_never_more_power(self):
        """ADR-A73: under `untrusted` an accepted command runs outside Codex's sandbox, so with a read-only sandbox only a
        command that just reads (and no network) may become a card; the rest is declined at once with a notice."""
        os.environ["FAKE_CX_SANDBOX"] = "read-only"

        async def script(c):
            host, ph, s = c["host"], c["ph"], c["s"]
            notices = lambda: [m["text"] for m in c["msgs"](("notice",))]  # noqa: E731
            for text, want in [("RUN: rm victim.txt", "declined: rm victim.txt"), ("RUN: touch made.txt", "declined: touch made.txt"),
                               ("RUN: curl -s https://example.com", "declined: curl -s https://example.com"),
                               ("PATCH: add new.txt", "patch declined: new.txt"), ("PERM: /etc", "granted: {}")]:
                n0 = len(notices())
                await c["say"](text)
                await c["wait"](lambda: any(m["text"] == want for m in c["msgs"]()))
                await c["idle"]()
                self.assertEqual(c["asks"](), [], f"{text}: no card")
                self.assertTrue(any(n.startswith("这一步超出了你 Codex 自己的沙箱设置，已拒绝；要放开请在电脑上改 Codex 的设置")
                                    for n in notices()[n0:]), (text, notices()[n0:]))
            self.assertTrue((self.work / "victim.txt").exists())
            self.assertFalse((self.work / "made.txt").exists() or (self.work / "new.txt").exists())
            # a command that only reads stays within a read-only sandbox: the usual card
            await c["say"]("RUN: stat victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 1)
            a = c["asks"]()[-1]
            self.assertEqual((a["summary"], a["cat"]), ("stat victim.txt", []))
            await host._app(s, ph.answer(a, True))
            await c["wait"](lambda: any(m["text"] == "ran: stat victim.txt" for m in c["msgs"]()))
        self.run_chain(script)
        log = approvals.read_log(self.st)
        self.assertEqual([(x["decision"], x["reason"]) for x in log], [("deny", "policy")] * 5 + [("allow", "device")])
        self.assertTrue(all(x["sig"] is None for x in log[:5]), "refused by policy: nobody was asked")
        acts = [json.loads(x) for f in (self.st.root / "activity").glob("*.jsonl") for x in f.read_text().splitlines()]
        self.assertEqual(sum(1 for r in acts if r.get("k") == "decision" and r.get("result") == "policy"), 5)
        self.assertNotIn("victim", self.st.log_path.read_text())
        answers = [x["answer"] for x in self.logged() if "answer" in x]
        self.assertEqual([a.get("decision") for a in answers if "decision" in a],
                         ["decline", "decline", "decline", "decline", "accept"])
        self.assertIn({"permissions": {}, "scope": "turn"}, answers)

    def test_workspace_write_sandbox_cards_only_what_stays_inside(self):
        os.environ["FAKE_CX_SANDBOX"] = "workspace-write"

        async def script(c):
            host, ph, s = c["host"], c["ph"], c["s"]
            # a patch inside the work folder: within the sandbox → the usual card (a delete is still red)
            await c["say"]("PATCH: add new.txt")
            await c["wait"](lambda: len(c["asks"]()) == 1)
            w = c["asks"]()[-1]
            self.assertEqual((w["tool"], w["cat"]), ("Write", []))
            await host._app(s, ph.answer(w, True))
            await c["wait"](lambda: (self.work / "new.txt").exists())
            await c["say"]("PATCH: delete victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 2)
            self.assertEqual(c["asks"]()[-1]["cat"], ["delete"])
            await host._app(s, ph.answer(c["asks"]()[-1], False))
            await c["wait"](lambda: any(m["text"] == "patch declined: victim.txt" for m in c["msgs"]()))
            # outside the writable roots, or Codex's protected .git: declined without a card
            for text in ("PATCH: add /srv/aj-outside-never-written.txt", "PATCH: add .git/config"):
                await c["say"](text)
                await c["wait"](lambda: any(m["text"].startswith("patch declined") and text.split()[-1] in m["text"] for m in c["msgs"]()))
            # a shell command that writes runs OUTSIDE the sandbox once accepted: declined, even inside the work folder
            await c["say"]("RUN: touch inside.txt")
            await c["wait"](lambda: any(m["text"] == "declined: touch inside.txt" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(len(c["asks"]()), 2)
            self.assertFalse((self.work / "inside.txt").exists())
            self.assertFalse(os.path.exists("/srv/aj-outside-never-written.txt"))
            await c["say"]("RUN: stat new.txt")
            await c["wait"](lambda: len(c["asks"]()) == 3)
            await host._app(s, ph.answer(c["asks"]()[-1], False))
        self.run_chain(script)
        self.assertEqual([(x["decision"], x["reason"]) for x in approvals.read_log(self.st)],
                         [("allow", "device"), ("deny", "device"), ("deny", "policy"), ("deny", "policy"), ("deny", "policy"),
                          ("deny", "device")])

    def test_granular_policy_stays_theirs(self):
        os.environ["FAKE_CX_POLICY"] = json.dumps({"granular": {"rules": False, "sandbox_approval": False, "mcp_elicitations": False}})

        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
        self.run_chain(script)
        start = [x["params"] for x in self.logged() if x.get("method") == "thread/start"][0]
        self.assertNotIn("approvalPolicy", start)
        self.assertEqual(start["approvalsReviewer"], "user")

    def test_scheduled_runs(self):
        task_dir(self.work, "look", "RUN: touch research-wrote.txt\nRUN: ls\nSAY: VERDICT: ok — 看完了")
        task_dir(self.work, "work", "RUN: touch normal-wrote.txt\nSAY: VERDICT: ok — 写好了", mode="normal")

        async def script(c):
            host = c["host"]
            os.environ["FAKE_CX_LOG"] = str(self.work / "workflows" / "look" / ".fake.jsonl")
            res = await host.scheduler.run("look", "manual")
            self.assertEqual((res["verdict"], res["readonly"]), ("ok", True), res)
            self.assertFalse((self.work / "workflows" / "look" / "research-wrote.txt").exists(), "read-only run: declined without a card")
            self.assertEqual(c["asks"](), [])
            os.environ["FAKE_CX_LOG"] = str(self.work / "workflows" / "work" / ".fake.jsonl")
            t = asyncio.create_task(host.scheduler.run("work", "manual"))
            await c["wait"](lambda: c["asks"]())
            a = c["asks"]()[-1]
            self.assertEqual((a["summary"], a["task"]), ("touch normal-wrote.txt", "任务work"))
            await host._app(c["s"], c["ph"].answer(a, True))
            res = await t
            self.assertEqual(res["verdict"], "ok", res)
            self.assertFalse((self.work / "normal-wrote.txt").exists(), "CEO writes stay in its own folder")
            self.assertTrue((self.work / "workflows" / "work" / "normal-wrote.txt").exists())
            self.assertIsNone(self.st.agent_session("codex"), "a scheduled run never becomes the chat's conversation")
        self.run_chain(script)
        rows = [json.loads(line) for log in (self.work / "workflows").glob("*/.fake.jsonl") for line in log.read_text().splitlines()]
        starts = [x["params"] for x in rows if x.get("method") == "thread/start"]
        starts.sort(key=lambda x: x.get("sandbox") != "read-only")
        self.assertEqual([(s.get("sandbox"), s.get("ephemeral"), s.get("approvalPolicy")) for s in starts],
                         [("read-only", True, "untrusted"), (None, True, "untrusted")])


class OpenCodeChain(_Chain):
    KIND = "opencode"

    def test_commands_stop_and_tasks(self):
        task_dir(self.work, "look", "RUN: touch r.txt\nSAY: VERDICT: ok — 看完了")

        async def script(c):
            host = c["host"]
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            first = self.st.agent_session("opencode")
            ctx = await c["cmd"]("context")
            self.assertTrue(ctx["text"].startswith("上下文 8.1k / 200.0k（4%）"), ctx["text"])
            n0 = len(c["msgs"]())
            comp = await c["cmd"]("compact")
            self.assertTrue(comp["text"].startswith("已压缩：8.1k → 694 tokens"), comp["text"])
            await asyncio.sleep(0.5)
            self.assertFalse(any("SUMMARY" in m["text"] for m in c["msgs"]()[n0:]), "the summary is not a reply")
            cost = await c["cmd"]("cost")
            self.assertIn("$0.0000", cost["text"])
            u = await c["cmd"]("usage")
            self.assertEqual(u["kind"], "info")
            st = await c["cmd"]("status")
            self.assertIn("1.18.32-fake", st["text"])
            ml = await c["cmd"]("model")
            self.assertEqual([m["id"] for m in ml["models"]], ["opencode/big-pickle", "opencode/fake-two"])
            ok = await c["cmd"]("model", "opencode/fake-two")
            self.assertTrue(ok["ok"])
            bad = await c["cmd"]("model", "fake-two")
            self.assertEqual(bad["kind"], "error")
            await c["say"]("二")
            await c["wait"](lambda: any(m["text"] == "ECHO: 二" for m in c["msgs"]()))
            await c["idle"]()
            await c["cmd"]("clear", confirm=True)
            await c["say"]("三")
            await c["wait"](lambda: any(m["text"] == "ECHO: 三" for m in c["msgs"]()))
            await c["idle"]()
            self.assertNotEqual(self.st.agent_session("opencode"), first)
            await c["cmd"]("undo_clear")
            await c["say"]("四")
            await c["wait"](lambda: any(m["text"] == "ECHO: 四" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.st.agent_session("opencode"), first)
            # /stop → abort; nothing paused
            await c["say"]("SLEEP: 20")
            await c["wait"](lambda: host.agent.status == "working")
            await asyncio.sleep(0.3)
            stop = await c["cmd"]("stop")
            self.assertTrue(stop["text"].startswith("已停下这一轮"), stop)
            await c["say"]("五")
            await c["wait"](lambda: any(m["text"] == "ECHO: 五" for m in c["msgs"]()))
            # a scheduled research run: its own session, not stored, deny-instead-of-ask rules
            chat = self.st.agent_session("opencode")
            task_log = self.work / "workflows" / "look" / ".fake.jsonl"
            os.environ["FAKE_OC_LOG"] = str(task_log)
            try:
                res = await host.scheduler.run("look", "manual")
            finally:
                os.environ["FAKE_OC_LOG"] = str(self.log)
            self.assertEqual((res["verdict"], res["readonly"]), ("fail", True), res)   # the fake answers no VERDICT itself
            self.assertFalse((self.work / "workflows" / "look" / "r.txt").exists())
            self.assertEqual(c["asks"](), [])
            self.assertEqual(self.st.agent_session("opencode"), chat, "a scheduled run never becomes the chat's conversation")
            # the stop switch → abort
            await c["say"]("SLEEP: 20")
            await c["wait"](lambda: host.agent.status == "working")
            await asyncio.sleep(0.3)
            await host._app(c["s"], _signed(c["ph"], "estop", {}, {"t": "estop", "r": "e1"}))
            await c["wait"](lambda: host.stopped() and host.agent.status != "working")
            # the stand-in logs the abort request when it gets it: wait for it rather than race the end of the chain
            await c["wait"](lambda: sum(1 for r in self.logged() if r.get("path", "").endswith("/abort")) >= 2)
        self.run_chain(script)
        lg = self.logged()
        task_log = self.work / "workflows" / "look" / ".fake.jsonl"
        lg += [json.loads(x) for x in task_log.read_text().splitlines()]
        self.assertEqual(sum(1 for r in lg if r.get("path", "").endswith("/abort")), 2, "/stop and the stop switch")
        summ = [r for r in lg if r.get("path", "").endswith("/summarize")]
        self.assertEqual(summ[0]["body"], {"providerID": "opencode", "modelID": "big-pickle"})
        prompts = [r["body"] for r in lg if r.get("path", "").endswith("/prompt_async")]
        self.assertIn({"providerID": "opencode", "modelID": "fake-two"}, [p.get("model") for p in prompts])
        made = [r["body"] for r in lg if r["method"] == "POST" and r["path"] == "/session"]
        task = [m for m in made if m["title"] == "Agent J 定时任务"]
        self.assertEqual(len(task), 1)
        self.assertNotIn("ask", {r["action"] for r in task[0]["permission"]}, "research: deny instead of ask")
        self.assert_activity_has_no_text()


if __name__ == "__main__":
    unittest.main()
