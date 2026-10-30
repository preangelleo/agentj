"""P156 (owner 2026-10-12): Agent J's platform heartbeat — the host wakes its own main Agent after an idle spell, on every
harness, through the queue every adapter already takes; plus 追加 1, `agentj busy capacity`.

- interval 5m–24h (default 60m, on); the prompt at ~/.config/agentj/heartbeat.md (default template, 0600, no secrets);
- a beat waits while the Agent works, a message is queued, a card is open, a compaction runs, the stop switch is on, the
  nightly upgrade window is open, or no phone is paired; any turn / phone message pushes it back; each conversation keeps
  its own clock and its own "prompt already seen";
- the message: 〔Agent J 心跳〕 + sha8 + "unchanged: no need to re-read"; the full prompt the first time in a serve process,
  after the hash changed and after a compaction / clear;
- its page is `via: heartbeat`; a 〔不回群〕 reply stays silent (no push);
- own Claude Code / Codex / OpenCode stand-ins receive it end to end (fenced, like production); shared Claude gets the
  〔Agent J 系统提示〕 envelope; `agentj heartbeat …` works through heartbeat.sock and offline.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import busy, heartbeat as hb, preferences, provenance, serve, shared  # noqa: E402
from agentj.state import State  # noqa: E402
from test_slash import _Chain  # noqa: E402


class Pure(unittest.TestCase):
    def test_interval(self):
        for raw, n in (("30m", 30), ("2h", 120), ("1h30m", 90), ("90", 90), ("1d", 1440), ("5m", 5), ("2小时", 120),
                       ("45分钟", 45), ("24h", 1440)):
            self.assertEqual(hb.parse_interval(raw), n, raw)
        for bad in ("4m", "25h", "", "abc", "0", "2x", "-30m"):
            with self.assertRaises(ValueError, msg=bad):
                hb.parse_interval(bad)
        self.assertEqual(hb.fmt_interval(90), "1 小时30 分钟")
        self.assertEqual(hb.fmt_interval(30, "en"), "30m")

    def test_schema_defaults_and_settable(self):
        d = preferences.defaults()
        self.assertEqual(d["heartbeat"], {"enabled": True, "interval_minutes": 60, "daily_at": ""})   # + P207 daily_at
        m = preferences.SCHEMA["heartbeat.interval_minutes"]
        self.assertEqual((m["minimum"], m["maximum"], m["tier"]), (5, 1440, "user"))
        self.assertIn("heartbeat.enabled", serve.PREF_SET_KEYS)
        self.assertIn("heartbeat.interval_minutes", serve.PREF_SET_KEYS)
        with self.assertRaises(preferences.ConfigError):
            preferences.validate({"heartbeat": {"interval_minutes": 4}})
        from agentj import ctl_auth
        self.assertFalse(any("heartbeat.enabled".startswith(p) for p in ctl_auth.SECURITY_PREFIXES),
                         "the main Agent may change it at the owner's word without a card")

    def test_prompt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "agentj" / "heartbeat.md"
            self.assertTrue(hb.ensure_prompt("zh", p))
            self.assertFalse(hb.ensure_prompt("zh", p), "never overwrites")
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
            text, digest = hb.read_prompt(p)
            self.assertIn("〔不回群〕", text)
            self.assertEqual(digest, hb.sha8(hb.TEMPLATE["zh"]))
            self.assertEqual(len(digest), 8)
            with self.assertRaises(ValueError):
                hb.write_prompt("用这个 key：sk-ant-api03-" + "A" * 90, p)
            with self.assertRaises(ValueError):
                hb.write_prompt("x" * (hb.PROMPT_MAX + 1), p)
            with self.assertRaises(ValueError):
                hb.write_prompt("   ", p)
            self.assertEqual(hb.read_prompt(p)[1], digest, "a refused write changes nothing")
            link = pathlib.Path(d) / "agentj" / "link.md"
            link.symlink_to(p)
            self.assertIsNone(hb.read_prompt(link), "never read through a link")
            with self.assertRaises(ValueError):
                hb.write_prompt("新提示", link)

    def test_message(self):
        full = hb.message("zh", "做这些事", "abcd1234", True, 61)
        self.assertTrue(full.startswith("〔Agent J 心跳〕 提示 abcd1234"))
        self.assertIn("做这些事", full)
        self.assertIn("〔不回群〕", full)
        self.assertIn("~/.config/agentj/heartbeat.md", full.replace(str(hb.prompt_path().parent), "~/.config/agentj")
                      if not str(hb.prompt_path()).startswith(os.path.expanduser("~")) else full)
        short = hb.message("zh", "做这些事", "abcd1234", False, 61)
        self.assertNotIn("做这些事", short)
        self.assertIn("不用重读", short)
        self.assertIn("1 小时1 分钟", short)
        en = hb.message("en", "do this", "abcd1234", False, 30)
        self.assertTrue(en.startswith("[Agent J heartbeat] prompt abcd1234"))
        self.assertIn("no need to re-read", en)

    def test_hook_context_names_the_host(self):
        ctx = provenance.hook_context(provenance.system())
        self.assertIn("Agent J host itself", ctx)
        self.assertIn("grants no new permission", ctx)
        self.assertNotIn("paired phone", ctx)
        self.assertIn("ScheduleWakeup", ctx)


class FakeAgent:
    native_source = {"kind": "terminal", "role": "owner"}
    kind = "claude"

    def __init__(self):
        self.cfg = {"session_mode": "independent"}
        self.status, self.halting, self.cur_send, self.cur_cmd = "idle", False, None, None
        self.q = asyncio.Queue()
        self.got = []

    def submit(self, x):
        self.got.append(x)

    def is_down(self):
        return False


class Decide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.st.allow(b"\x01" * 32, "phone") if hasattr(self.st, "allow") else None
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.host._post = lambda *a, **k: None
        self.agent = FakeAgent()
        self.host.agent = self.agent
        self.h = self.host.heartbeat
        p = patch.object(hb, "_night", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(type(self.st), "devices", return_value={"d1": {"name": "手机"}})
        p.start()
        self.addCleanup(p.stop)
        self.cfg = pathlib.Path(self.tmp.name) / "cfg"
        p = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.cfg)})
        p.start()
        self.addCleanup(p.stop)

    def due(self):
        return self.h.next_due() + 1

    def test_due_after_interval_and_pushed_back_by_activity(self):
        now = time.time()
        self.assertIsNone(asyncio.run(self.h.tick(now)), "not due right after start")
        self.h.touch()
        t = self.due()
        self.assertGreaterEqual(t - now, 60 * 60)
        r = asyncio.run(self.h.tick(t))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["full"], "first beat in this serve process: the full prompt")
        [send] = self.agent.got
        self.assertIsInstance(send, hb.HeartbeatSend)
        self.assertEqual(provenance.from_name(send.source), provenance.SYSTEM)
        self.assertTrue(send.text.startswith("〔Agent J 心跳〕"))
        self.assertIn("醒来以后按顺序做", send.text)
        page = self.host.hist.get(send.turn)
        self.assertEqual(page["src"], {"k": "agent", "via": "heartbeat", "text": ""})
        self.assertTrue(hb.prompt_path().exists(), "the default template is written on the first beat")
        send.state = "delivered"
        self.assertIsNone(asyncio.run(self.h.tick(t + 60)), "not due again within the interval")
        r2 = asyncio.run(self.h.tick(self.due()))
        self.assertFalse(r2["full"], "unchanged prompt: the short form")
        self.assertNotIn("醒来以后按顺序做", self.agent.got[-1].text)
        self.assertIn("不用重读", self.agent.got[-1].text)

    def test_running_goal_line_rides_on_the_beat(self):
        """P165 (P158's ask): a goal still running adds goal.heartbeat_line() at the end of the beat."""
        from agentj import goal as goal_mod
        self.h.touch()
        with patch.object(goal_mod, "heartbeat_line", return_value="〔目标进行中，截止 明天 08:00〕发版——先运行 `agentj goal status`，接着推进。"):
            r = asyncio.run(self.h.tick(self.due()))
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.agent.got[-1].text.rstrip().endswith("接着推进。"))
        self.agent.got[-1].state = "delivered"
        with patch.object(goal_mod, "heartbeat_line", return_value=""):
            asyncio.run(self.h.tick(self.due()))
        self.assertNotIn("目标进行中", self.agent.got[-1].text)

    def test_hash_change_and_forget_bring_the_full_text(self):
        self.agent.got.clear()
        asyncio.run(self.h.tick(self.due()))
        self.agent.got[-1].state = "delivered"
        hb.write_prompt("# 新提示\n只看备份任务。\n")
        r = asyncio.run(self.h.tick(self.due()))
        self.assertTrue(r["full"])
        self.assertIn("只看备份任务", self.agent.got[-1].text)
        self.assertIn(hb.sha8("# 新提示\n只看备份任务。\n"), self.agent.got[-1].text)
        self.agent.got[-1].state = "delivered"
        self.assertFalse(asyncio.run(self.h.tick(self.due()))["full"])
        self.agent.got[-1].state = "delivered"
        from agentj import slash
        self.host.cmd_done(SimpleNamespace(name="compact", by="手机", turn=None, cancelled=False), slash.Result("已压缩", "ok"))
        self.assertTrue(asyncio.run(self.h.tick(self.due()))["full"], "after a compaction the prompt is sent again")

    def test_waits(self):
        t = self.due()
        cases = []
        self.agent.status = "working"
        cases.append(("busy", asyncio.run(self.h.tick(t))))
        self.agent.status = "idle"
        self.agent.q.put_nowait("x")
        cases.append(("busy", asyncio.run(self.h.tick(t))))
        self.agent.q.get_nowait()
        self.agent.control_name = "compact"
        cases.append(("compacting", asyncio.run(self.h.tick(t))))
        self.agent.control_name = None
        self.agent.native_busy = lambda: True             # a shared session the owner types in at the desktop
        cases.append(("busy", asyncio.run(self.h.tick(t))))
        del self.agent.native_busy
        self.host.asks["r"] = SimpleNamespace(fut=asyncio.Future(loop=asyncio.new_event_loop()))
        cases.append(("approval", asyncio.run(self.h.tick(t))))
        self.host.asks.clear()
        with patch.object(self.host, "stopped", return_value=True):
            cases.append(("stopped", asyncio.run(self.h.tick(t))))
        with patch.object(hb, "_night", return_value=True):
            cases.append(("night_update", asyncio.run(self.h.tick(t))))
        with patch.object(type(self.st), "devices", return_value={}):
            cases.append(("no_phone", asyncio.run(self.h.tick(t))))
        for want, got in cases:
            self.assertEqual(got, {"ok": False, "why": want})
        self.assertEqual(self.agent.got, [], "nothing was queued while waiting")
        self.assertIn('"ev": "heartbeat_wait"', self.st.log_path.read_text())

    def test_off_and_interval_from_preferences(self):
        self.host.preferences = preferences.merge(self.host.preferences or preferences.defaults(), {"heartbeat": {"enabled": False}})
        self.assertIsNone(asyncio.run(self.h.tick(time.time() + 10 ** 6)))
        self.host.preferences = preferences.merge(self.host.preferences, {"heartbeat": {"enabled": True, "interval_minutes": 5}})
        self.assertLess(self.h.next_due() - max(self.h.started, self.h.last_activity), 5 * 60 + 1)

    def test_each_conversation_keeps_its_own_clock(self):
        r = asyncio.run(self.h.tick(self.due()))
        self.agent.got[-1].state = "delivered"
        own_key = r["key"]
        self.assertTrue(own_key.startswith("own-claude-"))
        other = FakeAgent()
        other.cfg = {"session_mode": "shared"}
        other.session = {"sessionId": "11111111-2222-3333-4444-555555555555"}
        self.host.agent = other
        self.assertEqual(hb.session_key(other), "shared-claude-11111111-2222-3333-4444-555555555555")
        r2 = asyncio.run(self.h.tick(self.h.next_due() + 1))
        self.assertTrue(r2["full"], "a different conversation has not seen the prompt")
        beats = json.loads((self.st.root / hb.STATE_FILE).read_text())["beats"]
        self.assertEqual(set(beats), {own_key, r2["key"]})

    def test_silent_reply_no_push_and_a_real_reply_pushes(self):
        pushes = []
        self.host.push_notify = pushes.append
        asyncio.run(self.h.tick(self.due()))
        send = self.agent.got[-1]
        self.agent.cur_send = None
        self.host.agent_turn_start(send.text, send)
        self.host.agent_text("〔不回群〕")
        self.host.agent_turn_end()
        page = self.host.hist.get(send.turn)
        self.assertEqual(page["reply"]["text"], "〔不回群〕")
        self.assertEqual(pushes, [], "silent: no push, no unread")
        send.state = "delivered"
        asyncio.run(self.h.tick(self.due()))
        send2 = self.agent.got[-1]
        self.host.agent_turn_start(send2.text, send2)
        self.host.agent_text("备份任务昨晚失败了，已派给守望重跑。")
        self.host.agent_turn_end()
        self.assertEqual(pushes, ["reply"])
        self.assertEqual(self.host.hist.get(send2.turn)["src"].get("via"), "heartbeat")

    def test_delivery_and_drop_bookkeeping(self):
        asyncio.run(self.h.tick(self.due()))
        send = self.agent.got[-1]
        posted = []
        self.host._post = lambda *a: posted.append(a)
        self.host.say_delivered(send)
        self.host.say_uncertain(send)
        self.assertEqual(posted, [], "no say_state to a phone for a heartbeat")
        self.host.queue_dropped(send)
        self.assertEqual(self.host.hist.get(send.turn)["end"], "stopped")
        self.assertEqual(send.state, "cancelled")

    def test_socket_ops(self):
        async def go():
            set_pref = AsyncMock(return_value={"ok": True})
            self.host.set_pref = set_pref
            r = await self.h.handle({"op": "set", "interval": "30m"})
            self.assertEqual(r, {"ok": True, "interval_minutes": 30})
            set_pref.assert_awaited_with("heartbeat.interval_minutes", 30)
            self.assertFalse((await self.h.handle({"op": "set", "interval": "3m"}))["ok"])
            await self.h.handle({"op": "off"})
            set_pref.assert_awaited_with("heartbeat.enabled", False)
            r = await self.h.handle({"op": "prompt_set", "text": "# 心跳\n看看待办。\n"})
            self.assertTrue(r["ok"])
            self.assertEqual((await self.h.handle({"op": "prompt_show"}))["text"], "# 心跳\n看看待办。\n")
            self.assertFalse((await self.h.handle({"op": "prompt_set", "text": "token: ghp_" + "a" * 36}))["ok"])
            st = await self.h.handle({"op": "status"})
            self.assertEqual((st["enabled"], st["interval_minutes"]), (True, 60))
            self.agent.status = "working"
            r = await self.h.handle({"op": "now"})
            self.assertEqual(r["why"], "busy")
            self.assertTrue((await self.h.handle({"op": "now", "queue": True}))["ok"], "--queue: behind the current turn")
            self.agent.status = "idle"
            # a group member's message is being handled: nothing may be changed
            self.agent.cur_send = SimpleNamespace(source={"kind": "telegram", "role": "group", "sender": 5, "chat": -9})
            self.assertEqual((await self.h.handle({"op": "prompt_set", "text": "改掉"}))["why"], "untrusted_turn")
            self.assertEqual((await self.h.handle({"op": "set", "interval": "5m"}))["why"], "untrusted_turn")
            self.assertTrue((await self.h.handle({"op": "status"}))["ok"], "reading is fine")
        asyncio.run(go())

    def test_socket_round_trip(self):
        async def go():
            short = tempfile.mkdtemp(prefix="hb", dir="/tmp")
            self.addCleanup(lambda: __import__("shutil").rmtree(short, True))
            self.st.perm_dir.mkdir(parents=True, exist_ok=True)
            with patch.object(type(self.h), "sock", property(lambda s: pathlib.Path(short) / "hb.sock")):
                await self.h.start()
                try:
                    with patch.dict(os.environ, {hb.ENV: str(pathlib.Path(short) / "hb.sock")}):
                        res = await asyncio.to_thread(hb.ask, {"op": "status"})
                    self.assertTrue(res["ok"])
                    self.assertEqual(oct((pathlib.Path(short) / "hb.sock").stat().st_mode & 0o777), "0o600")
                finally:
                    await self.h.stop()
        asyncio.run(go())


class SharedClaude(unittest.IsolatedAsyncioTestCase):
    """A beat into the owner's shared Claude Code session: the 〔Agent J 系统提示〕 envelope, the reply, the compaction."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p156s-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        work = root / "work"
        work.mkdir()
        cc = root / "claude"
        (cc / "sessions").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(cc), "AGENTJ_STATE_DIR": str(root / "state")})
        env.start()
        self.addCleanup(env.stop)
        sid = "15615615-1561-1561-1561-15615615aaaa"
        self.sid = sid
        sess = {"pid": os.getpid(), "cwd": str(work), "sessionId": sid, "kind": "interactive",
                "messagingSocketPath": str(root / "peer.sock"), "status": "idle", "updatedAt": 10}
        (cc / "sessions" / "owner.json").write_text(json.dumps(sess))
        self.host = Mock()
        self.host.lang = "zh"
        self.host.stopped.return_value = False
        self.texts = []
        self.host.agent_text.side_effect = self.texts.append
        p = patch.object(shared.SharedClaudeAgent, "prepare_channel", AsyncMock())
        p.start()
        self.addCleanup(p.stop)
        self.sent = []
        p = patch.object(shared, "claude_send", side_effect=lambda s, text, src=None: self.sent.append((text, src)))
        p.start()
        self.addCleanup(p.stop)
        self.a = shared.SharedClaudeAgent(self.host, {"kind": "claude", "dir": str(work), "_workflow_ceo": True,
                                                      "session_mode": "shared", "high_risk_warnings": False})
        self.a.session = dict(sess)
        self.a.loop = asyncio.get_running_loop()

    async def test_beat_goes_in_as_a_system_note_and_the_reply_comes_back(self):
        send = hb.HeartbeatSend(7, hb.message("zh", "看待办", "abcd1234", True, 60), "shared-claude-x", "abcd1234")
        self.a.cur_send = send
        t = asyncio.create_task(self.a.turn(send.text))
        for _ in range(100):
            if self.sent:
                break
            await asyncio.sleep(0.01)
        [(text, src)] = self.sent
        self.assertEqual(provenance.from_name(src), provenance.SYSTEM)
        env = provenance.envelope("x", text, src)
        self.assertIn("Agent J 系统提示", env)
        self.assertIn("〔Agent J 心跳〕", env)
        await self.a.hook_event({"hook_event_name": "UserPromptSubmit", "session_id": self.sid, "prompt": text})
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": self.sid, "last_assistant_message": "〔不回群〕"})
        await asyncio.wait_for(t, 5)
        self.assertEqual(self.texts, ["〔不回群〕"])

    async def test_compaction_makes_the_next_beat_full(self):
        await self.a.hook_event({"hook_event_name": "PostCompact", "session_id": self.sid})
        self.host.heartbeat.forget.assert_called_with("compact")


@unittest.skipUnless(sys.platform.startswith(("linux", "darwin")), "stand-ins are shell scripts")
class _Beat(_Chain):
    """The real adapter + its stand-in (fenced where bubblewrap works): two beats and a prompt change, end to end."""

    def test_heartbeat_end_to_end(self):
        hb.prompt_path().unlink(missing_ok=True)      # the test process shares one config home

        async def script(c):
            host = c["host"]
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            h = host.heartbeat
            with patch.object(hb, "_night", return_value=False):
                r1 = await h.tick(h.next_due() + 1)
            self.assertTrue(r1 and r1["ok"], r1)
            self.assertTrue(r1["full"])
            await c["wait"](lambda: any(m["text"].startswith("ECHO: 〔Agent J 心跳〕") for m in c["msgs"]()))
            await c["idle"]()
            first = [m["text"] for m in c["msgs"]() if m["text"].startswith("ECHO: 〔Agent J 心跳〕")]
            self.assertIn("醒来以后按顺序做", first[-1], "the first beat carries the prompt")
            page = host.hist.get(r1["page"])
            self.assertEqual(page["src"].get("via"), "heartbeat")
            self.assertEqual(page["end"], "done")
            with patch.object(hb, "_night", return_value=False):
                r2 = await h.tick(h.next_due() + 1)
            self.assertFalse(r2["full"])
            await c["wait"](lambda: len([m for m in c["msgs"]() if m["text"].startswith("ECHO: 〔Agent J 心跳〕")]) == 2)
            await c["idle"]()
            second = [m["text"] for m in c["msgs"]() if m["text"].startswith("ECHO: 〔Agent J 心跳〕")][-1]
            self.assertIn("不用重读", second)
            self.assertNotIn("醒来以后按顺序做", second)
            hb.write_prompt("# 心跳\n只看备份。\n")
            with patch.object(hb, "_night", return_value=False):
                r3 = await h.tick(h.next_due() + 1)
            self.assertTrue(r3["full"])
            self.assertNotEqual(r3["sha"], r1["sha"])
            await c["wait"](lambda: any("只看备份" in m["text"] for m in c["msgs"]()))
            await c["idle"]()
            # a phone message while a beat would be due: the conversation pushes it back
            before = h.last_activity
            await c["say"]("在吗")
            await c["wait"](lambda: any(m["text"] == "ECHO: 在吗" for m in c["msgs"]()))
            self.assertGreater(h.last_activity, before)
        with patch.object(type(self.st), "devices", return_value={"d": {"name": "手机"}}):
            self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertEqual(log.count('"ev": "heartbeat_sent"'), 3)
        self.assertNotIn("醒来以后按顺序做", log, "the prompt text never goes into host.log")


class ClaudeBeat(_Beat):
    KIND = "claude"


class CodexBeat(_Beat):
    KIND = "codex"


class OpenCodeBeat(_Beat):
    KIND = "opencode"


del _Beat


class CLI(unittest.TestCase):
    def run_cli(self, *args, env=None, stdin=None):
        e = dict(os.environ, PYTHONPATH=str(HERE.parent), **(env or {}))
        e.pop(hb.ENV, None)
        return subprocess.run([sys.executable, "-c", "from agentj.cli import main; main()", *args], env=e, input=stdin,
                              capture_output=True, text=True, timeout=60)

    def test_offline_cli(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"XDG_CONFIG_HOME": d + "/cfg", "AGENTJ_STATE_DIR": d + "/state"}
            r = self.run_cli("heartbeat", "status", "--json", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            j = json.loads(r.stdout)
            self.assertEqual((j["enabled"], j["interval_minutes"], j["serve"]), (True, 60, False))
            r = self.run_cli("heartbeat", "set", "30m", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("记下了", r.stdout)
            self.assertEqual(json.loads(self.run_cli("heartbeat", "status", "--json", env=env).stdout)["interval_minutes"], 30)
            self.assertNotEqual(self.run_cli("heartbeat", "set", "2m", env=env).returncode, 0)
            r = self.run_cli("heartbeat", "prompt", "set", env=env, stdin="# 心跳\n看看待办。\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(pathlib.Path(d, "cfg", "agentj", "heartbeat.md").read_text(), "# 心跳\n看看待办。\n")
            self.assertIn("看看待办", self.run_cli("heartbeat", "prompt", "show", env=env).stdout)
            self.assertNotEqual(self.run_cli("heartbeat", "now", env=env).returncode, 0, "no serve: nothing to wake")
            self.assertEqual(self.run_cli("heartbeat", "off", env=env).returncode, 0)
            self.assertFalse(json.loads(self.run_cli("heartbeat", "status", "--json", env=env).stdout)["enabled"])


class Capacity(unittest.TestCase):
    def test_rule(self):
        big = busy.capacity(running=2, cores=32, load=4.0, avail_mib=64 * 1024)
        self.assertEqual(big["can_start"], 16, "capped")
        mid = busy.capacity(running=1, cores=8, load=2.0, avail_mib=4096)
        self.assertEqual(mid["can_start"], 4)          # memory: (4096-1024)/700 = 4.4; cpu: 6.4-2 = 4.4
        tight = busy.capacity(running=6, cores=4, load=3.9, avail_mib=8192)
        self.assertEqual((tight["can_start"], tight["limited_by"]), (0, "cpu"))
        self.assertIn("排队", tight["advice_zh"])
        low_mem = busy.capacity(running=0, cores=16, load=0.5, avail_mib=1500)
        self.assertEqual((low_mem["can_start"], low_mem["limited_by"]), (0, "memory"))
        no_load = busy.capacity(running=1, cores=8, load=None, avail_mib=None)    # Windows: no load average
        self.assertEqual(no_load["can_start"], 3)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            e = dict(os.environ, PYTHONPATH=str(HERE.parent), AGENTJ_STATE_DIR=d + "/state")
            r = subprocess.run([sys.executable, "-c", "from agentj.cli import main; main()", "busy", "capacity", "--json"],
                               env=e, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            j = json.loads(r.stdout)
            self.assertEqual(set(j) >= {"cores", "load1", "mem_available_mib", "running", "can_start", "advice_zh"}, True)
            self.assertGreaterEqual(j["cores"], 1)


if __name__ == "__main__":
    unittest.main()
