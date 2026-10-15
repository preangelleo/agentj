"""P289: the harness-switch handoff (switch.md) — the compaction handoff's protocol, reused for /switch-harness.

- The job (fake control socket / restart / clock), for Claude Code · Codex · OpenCode × independent · shared: after the gate
  and the way-back check, before the config changes, the harness being left gets one host turn (`harness_handoff` write) and
  writes switch.md where the host said — `<working root>/.agentj/handover/switch.md` (independent) or
  `~/.local/state/agentj/handoff/switch.md` (shared); the new harness's 「自报家门」 names that file first (handoff "ok"),
  then one more host turn (`resume`) carries on from it. Switching back (A → B → A) works the same way.
- Not written (missing / unchanged / timed out / refused by an older serve): the switch still happens, the owner gets the
  「这次没写成交接」 line, the new harness is told there is none, no resume turn, and an old switch.md is never pointed at.
- Dry run / a failed gate / no way back: no handoff turn at all.
- serve: `harness_handoff` takes kinds only, the running harness must be the right side, the path is the host's own; the
  writing instruction carries the agentj-handoff sections, the length limit and the no-credentials rule.
- The real adapters with the stand-ins (fakeclaude / fakecodex / fakeopencode, fenced): the host's writing turn reaches the
  harness, it writes switch.md inside the work folder, and the job's own check sees it as freshly written.
Run (in host): .venv/bin/python -m unittest discover -s tests -p 'test_p289_switch_handoff.py'
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import handoff, serve, switch_harness as sw  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p155_switch_harness import UP, Clock, FakeAgent, FakeServe, fake_probe, mk_state  # noqa: E402
from test_slash import _Chain  # noqa: E402

PATH_IN = re.compile(r"`([^`]+switch\.md)`")


def shared(st):
    from agentj import preferences
    preferences.path().parent.mkdir(parents=True, exist_ok=True)
    preferences.path().write_text(preferences.edit(preferences.read()[0], "agent.session_mode", "shared"))
    assert st.agent_config()["session_mode"] == "shared"


# P282 / P289 phases (P281 adds probing / waiting / switching / switched around them once merged)
OWN_PHASES = ("probe_back", "handoff", "handoff_ok", "handoff_failed")

class HandoffServe(FakeServe):
    """FakeServe that also knows P289's `harness_handoff`, using the host's own texts (hello_prompt / handoff_prompt with
    the running config) and a stand-in harness that follows them: `write` → writes the file named in backticks."""

    def __init__(self, st, kind, *, writes=True, answers_handoff=True, progress=True):
        super().__init__(st, kind)
        self.writes, self.answers_handoff, self.progress = writes, answers_handoff, progress
        self.handoffs, self.texts, self.phases, self.order = [], [], [], []

    def __call__(self, req):
        c = req["cmd"]
        if c == "harness_progress":
            self.calls.append(req)
            self.phases.append(req["phase"])
            return {"ok": True, "shown": True} if self.progress else {"ok": False, "error": "unknown command"}
        if c == "harness_hello":
            cfg = self.st.agent_config()
            self.texts.append(("hello", sw.hello_prompt(self.kind, req, "zh", handoff.switch_where(cfg))))
        if c == "harness_handoff":
            self.calls.append(req)
            cfg = self.st.agent_config()
            text = sw.handoff_prompt(self.kind, req, cfg, "zh")
            if text is None:
                return {"ok": False, "error": "bad_request"}
            self.handoffs.append((self.kind, req["step"], cfg["kind"]))
            self.texts.append((req["step"], text))
            if req["step"] == "write":
                handoff.switch_ensure(cfg)
                if self.writes:
                    p = pathlib.Path(os.path.expanduser(PATH_IN.search(text).group(1)))
                    old = p.read_text() if p.exists() else ""
                    p.write_text("# Handoff\n## 1. The task (owner's own words)\n> 「做 P289」\n## 6. Next\n1. tests\n"
                                 + ("\n<!-- merged -->\n" + old if old else ""))
                if self.answers_handoff:
                    self.said = 2000 + len(self.handoffs)
            return {"ok": True, "at": 2000}
        return super().__call__(req)


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p289-")
        self.addCleanup(self.tmp.cleanup)
        unit = pathlib.Path(self.tmp.name) / "units"
        for p in (mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name)),
                  mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(pathlib.Path(self.tmp.name) / "state")})):
            p.start()
            self.addCleanup(p.stop)
        self.clock = Clock()
        from agentj import preferences
        preferences.path().unlink(missing_ok=True)          # no shared mode left over from another test
        self.addCleanup(preferences.path().unlink, missing_ok=True)

    def run_job(self, st, fake, target, probe=None, **kw):
        return sw.worker(st, target, None, probe=probe or fake_probe(), ctl=fake, restart=fake.restart, status=UP,
                         ready=lambda st, limit: "idle", sleep=self.clock.sleep, clock=self.clock, **kw)

    def where(self, st):
        return handoff.switch_path(st.agent_config())

    def flow(self, frm, to, is_shared):
        st = mk_state(self.tmp.name, frm)
        if is_shared:
            shared(st)
        fake = HandoffServe(st, frm)
        self.assertEqual(self.run_job(st, fake, to), 0)
        f = self.where(st)
        if is_shared:
            self.assertEqual(f, st.root / "handoff" / "switch.md")
            self.assertEqual(handoff.switch_where(st.agent_config()), handoff.shown(f))
        else:
            self.assertEqual(f, pathlib.Path(self.tmp.name) / "work" / ".agentj" / "handover" / "switch.md")
            self.assertEqual(oct(f.parent.stat().st_mode & 0o777), "0o700")
        self.assertTrue(f.read_text().startswith("# Handoff"))
        # the writing turn ran on the harness being left, while the config still named it; the resume on the new one
        self.assertEqual(fake.handoffs, [(frm, "write", frm), (to, "resume", to)])
        hello = [r for r in fake.calls if r["cmd"] == "harness_hello"]
        self.assertEqual(hello, [{"cmd": "harness_hello", "outcome": "ok", "from": frm, "to": to, "handoff": "ok"}])
        write = dict(fake.texts)["write"]
        self.assertTrue(write.startswith(handoff.SWITCH_MARK + "\n〔Agent J 系统提示〕"))
        self.assertIn(handoff.switch_where(st.agent_config()), write)
        hello_text = dict(fake.texts)["hello"]
        self.assertIn("先完整读交接文件 " + handoff.switch_where(st.agent_config()), hello_text)
        self.assertIn(handoff.switch_where(st.agent_config()), dict(fake.texts)["resume"])
        self.assertEqual([p for p in fake.phases if p in OWN_PHASES], ["probe_back", "handoff", "handoff_ok"])
        # P290 merge with P281: one ordered stream of phases for the owner
        self.assertEqual(fake.phases, ["probing", "probe_back", "waiting", "handoff", "handoff_ok", "switching", "switched"])
        # order: gate → way back → handoff → config change → restart → hello → resume
        cmds = [r["cmd"] + ":" + r.get("step", r.get("phase", "")) for r in fake.calls if r["cmd"] != "status"
                and not (r["cmd"] == "harness_progress" and r.get("phase") not in OWN_PHASES)]   # P281's phases: its own test
        self.assertEqual(cmds, ["harness_progress:probe_back", "harness_progress:handoff", "harness_handoff:write", "harness_progress:handoff_ok",
                                "harness_hello:", "harness_handoff:resume"])
        log = st.log_path.read_text()
        self.assertIn('"ev": "harness_handoff"', log)
        self.assertNotIn("做 P289", log)
        return st, fake

    def test_three_harnesses_independent_and_shared(self):
        for frm, to in (("claude", "codex"), ("codex", "opencode"), ("opencode", "claude")):
            for is_shared in (False, True):
                with self.subTest(frm=frm, to=to, shared=is_shared):
                    self.tearDown_state()
                    self.flow(frm, to, is_shared)

    def tearDown_state(self):
        import shutil
        from agentj import preferences
        preferences.path().unlink(missing_ok=True)          # the previous subtest's shared mode
        for d in ("state", "work", "home"):
            shutil.rmtree(pathlib.Path(self.tmp.name) / d, ignore_errors=True)

    def test_switching_back_reads_and_merges_the_same_file(self):
        st, fake = self.flow("claude", "opencode", False)
        first = self.where(st).read_text()
        time.sleep(0.02)
        self.assertEqual(self.run_job(st, fake, "claude"), 0)        # back: OpenCode writes, Claude Code reads
        self.assertEqual(fake.handoffs[2:], [("opencode", "write", "opencode"), ("claude", "resume", "claude")])
        self.assertIn("<!-- merged -->", self.where(st).read_text())
        self.assertIn(first, self.where(st).read_text())
        self.assertEqual([r for r in fake.calls if r["cmd"] == "harness_hello"][-1]["handoff"], "ok")

    def test_undo_and_force_get_the_handoff_too(self):
        st = mk_state(self.tmp.name, "claude")
        fake = HandoffServe(st, "claude")
        self.assertEqual(self.run_job(st, fake, "codex", force=True), 0)
        self.assertEqual(self.run_job(st, fake, None, undo=True), 0)
        self.assertEqual([h[:2] for h in fake.handoffs], [("claude", "write"), ("codex", "resume"),
                                                          ("codex", "write"), ("claude", "resume")])

    def check_not_written(self, st, fake, result):
        self.assertEqual(st.agent_config()["kind"], "codex", "switched anyway")
        hello = [r for r in fake.calls if r["cmd"] == "harness_hello"]
        self.assertEqual(hello[-1]["handoff"], "none")
        text = sw.hello_prompt("codex", hello[-1], "zh", handoff.switch_where(st.agent_config()))
        self.assertIn("这次 Claude Code 没写成交接", text)
        self.assertNotIn("switch.md", text, "an old or missing file is never pointed at")
        self.assertNotIn("resume", [h[1] for h in fake.handoffs])
        self.assertIn(f'"result": "{result}"', st.log_path.read_text())

    def test_not_written_switches_anyway_and_says_so(self):
        st = mk_state(self.tmp.name, "claude")
        fake = HandoffServe(st, "claude", writes=False)
        self.assertEqual(self.run_job(st, fake, "codex"), 0)
        self.assertEqual([p for p in fake.phases if p in OWN_PHASES], ["probe_back", "handoff", "handoff_failed"])
        self.check_not_written(st, fake, "missing")

    def test_an_old_switch_md_left_unchanged_does_not_count(self):
        st = mk_state(self.tmp.name, "claude")
        handoff.switch_ensure(st.agent_config())
        self.where(st).write_text("# Handoff\nfrom an earlier switch\n")
        fake = HandoffServe(st, "claude", writes=False)
        self.assertEqual(self.run_job(st, fake, "codex"), 0)
        self.check_not_written(st, fake, "unchanged")

    def test_timeout_switches_anyway(self):
        st = mk_state(self.tmp.name, "claude")
        fake = HandoffServe(st, "claude", writes=False, answers_handoff=False)
        fake.status_ = "working"
        t0 = self.clock.t
        real = fake.restart

        def restart():
            real()
            fake.status_ = "idle"
        fake.restart = restart
        self.assertEqual(self.run_job(st, fake, "codex"), 0)
        self.assertGreaterEqual(self.clock.t - t0, handoff.SWITCH_WAIT)
        self.check_not_written(st, fake, "timeout")

    def test_written_but_still_busy_at_the_limit_counts(self):
        st = mk_state(self.tmp.name, "claude")
        fake = HandoffServe(st, "claude", answers_handoff=False)
        fake.status_ = "waiting"                     # e.g. an approval card for the write is still open
        real = fake.restart

        def restart():
            real()
            fake.status_ = "idle"
        fake.restart = restart
        self.assertEqual(self.run_job(st, fake, "codex"), 0)
        self.assertEqual([r for r in fake.calls if r["cmd"] == "harness_hello"][-1]["handoff"], "ok")

    def test_an_older_serve_refuses_and_the_owner_still_gets_the_line(self):
        st = mk_state(self.tmp.name, "claude")
        fake = FakeServe(st, "claude")               # no harness_handoff, no harness_progress
        self.assertEqual(self.run_job(st, fake, "codex"), 0)
        self.assertEqual(st.agent_config()["kind"], "codex")
        self.assertEqual(len(fake.sent), 1)
        self.assertTrue(fake.sent[0].startswith("这次没写成交接"), fake.sent)
        self.assertIn('"result": "refused"', st.log_path.read_text())

    def test_with_progress_pages_no_extra_message(self):
        st = mk_state(self.tmp.name, "claude")
        fake = HandoffServe(st, "claude", writes=False)
        self.run_job(st, fake, "codex")
        self.assertEqual(fake.sent, [], "P281's page said it; no duplicate phone message")

    def test_dry_run_failed_gate_and_no_way_back_ask_for_nothing(self):
        st = mk_state(self.tmp.name, "claude")
        fake = HandoffServe(st, "claude")
        self.assertEqual(self.run_job(st, fake, "codex", dry_run=True), 0)
        self.assertEqual(self.run_job(st, fake, "codex", probe=fake_probe(False)), 1)

        def only_target(only=None, model=None, timeout=60):
            return {"harnesses": [{"name": n, "installed": True, "version": "1", "callable": n == "codex",
                                   **({} if n == "codex" else {"probe_reason": "not_logged_in"})} for n in only]}
        self.assertEqual(self.run_job(st, fake, "codex", probe=only_target), 1)
        self.assertEqual(fake.handoffs, [])
        self.assertFalse(self.where(st).exists())

    def test_a_link_is_never_a_handoff(self):
        st = mk_state(self.tmp.name, "claude")
        for is_shared in (False, True):
            if is_shared:
                shared(st)
            cfg = st.agent_config()
            handoff.switch_ensure(cfg)
            out = pathlib.Path(self.tmp.name) / "outside.md"
            out.write_text("# not ours\n")
            p = handoff.switch_path(cfg)
            p.unlink(missing_ok=True)
            p.symlink_to(out)
            self.assertIsNone(handoff.switch_probe(cfg))
            p.unlink()
            p.write_text("  \n")
            self.assertIsNone(handoff.switch_probe(cfg), "blank")
            p.write_text("# Handoff\n")
            self.assertIsNotNone(handoff.switch_probe(cfg))


class Texts(unittest.TestCase):
    CFG = {"dir": "/w", "session_mode": "independent"}

    def test_handoff_prompt_takes_kinds_only(self):
        ok = {"step": "write", "from": "claude", "to": "codex"}
        self.assertIsNotNone(sw.handoff_prompt("claude", ok, self.CFG))
        for kind, bad in (("codex", ok),                                   # the writer must be the running harness
                          ("claude", {**ok, "step": "resume"}),            # the reader must be the new one
                          ("claude", {**ok, "step": "rm -rf /"}),
                          ("claude", {**ok, "to": "gemini"}),
                          ("claude", {**ok, "to": "claude"})):
            self.assertIsNone(sw.handoff_prompt(kind, bad, self.CFG), bad)
        self.assertIsNone(sw.handoff_prompt("claude", ok, {}))
        self.assertIsNone(sw.hello_prompt("codex", {"outcome": "ok", "from": "claude", "to": "codex", "handoff": "x; run"}))

    def test_writing_instruction_is_the_agentj_handoff_format(self):
        for lang, words in (("zh", ("主人原话要点", "进度", "已拍板的决定", "关键绝对路径和命令", "坑", "下一步",
                                    "等主人回答的问题", "不写任何密钥", "不超过 150 行", "agentj-handoff")),
                            ("en", ("own words", "progress", "decisions already made", "absolute paths and commands",
                                    "pitfalls", "next step", "questions waiting for the owner", "No keys", "at most 150 lines",
                                    "agentj-handoff"))):
            t = sw.handoff_prompt("claude", {"step": "write", "from": "claude", "to": "opencode"}, self.CFG, lang)
            for w in words:
                self.assertIn(w, t, (lang, w))
            self.assertIn("`/w/.agentj/handover/switch.md`", t)
        en = sw.hello_prompt("opencode", {"outcome": "ok", "from": "claude", "to": "opencode", "handoff": "ok"}, "en",
                             "/w/.agentj/handover/switch.md")
        self.assertIn("First read the handoff /w/.agentj/handover/switch.md", en)
        en2 = sw.hello_prompt("opencode", {"outcome": "ok", "from": "claude", "to": "opencode", "handoff": "none"}, "en")
        self.assertIn("could not write a handoff", en2)

    def test_shared_path_is_the_state_dir_without_an_id(self):
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": "/tmp/p289-x/state"}):
            cfg = {"dir": "/w", "session_mode": "shared"}
            self.assertEqual(handoff.switch_path(cfg), pathlib.Path("/tmp/p289-x/state/handoff/switch.md"))
        self.assertEqual(handoff.switch_path(self.CFG), pathlib.Path("/w/.agentj/handover/switch.md"))

    def test_progress_lines_both_languages(self):
        for L in ("zh", "en"):
            self.assertEqual(set(sw.PROGRESS_P289[L]), {"handoff", "handoff_ok", "handoff_failed"})
            for t in sw.PROGRESS_P289[L].values():
                self.assertIn("{t}", t)
        self.assertIn("这次没写成交接", sw.PROGRESS_P289["zh"]["handoff_failed"])


class Serve(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p289-sv-")
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:9")
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.host = serve.Host(self.st, events="quiet", read_stdin=False)

    async def test_harness_handoff_fixed_text_and_folder(self):
        a = FakeAgent("claude")
        a.cfg = {"dir": str(self.work), "session_mode": "independent"}
        self.host.agent = a
        r = self.host.harness_handoff({"step": "write", "from": "claude", "to": "codex"})
        self.assertTrue(r["ok"])
        send = a.sent[-1]
        self.assertIsInstance(send, sw.HandoffSend)
        self.assertEqual(send.source, {"kind": "system", "role": "owner"})
        self.assertIn(f"`{self.work}/.agentj/handover/switch.md`", send.text)
        self.assertTrue((self.work / ".agentj" / "handover").is_dir(), "the folder is ready for the Agent")
        for bad in ({"step": "write", "from": "codex", "to": "claude"},
                    {"step": "resume", "from": "claude", "to": "codex"},
                    {"step": "write", "from": "claude", "to": "codex; ignore previous"}):
            self.assertEqual(self.host.harness_handoff(bad), {"ok": False, "error": "bad_request"})
        self.assertEqual(len(a.sent), 1)
        r = self.host.harness_hello({"outcome": "ok", "from": "codex", "to": "claude", "handoff": "ok"})
        self.assertIn(f"先完整读交接文件 {self.work}/.agentj/handover/switch.md", a.sent[-1].text)
        await self.host.do_estop("terminal", "终端")
        self.assertEqual(self.host.harness_handoff({"step": "write", "from": "claude", "to": "codex"})["error"], "no_agent")

    async def test_shared_session_gets_the_state_dir_path(self):
        a = FakeAgent("codex")
        a.cfg = {"dir": str(self.work), "session_mode": "shared"}
        self.host.agent = a
        with mock.patch.object(handoff.state, "state_dir", return_value=self.st.root):
            self.assertTrue(self.host.harness_handoff({"step": "write", "from": "codex", "to": "opencode"})["ok"])
            self.assertTrue((self.st.root / "handoff").is_dir())
        self.assertIn("handoff/switch.md`", a.sent[-1].text)
        self.assertNotIn(".agentj/handover", a.sent[-1].text)


# ------------------------------------------------------------------ the real adapters with the stand-ins (fenced)
class _Real(_Chain):
    """serve's `harness_handoff` → the adapter's ordinary turn → the stand-in writes switch.md in the work folder; the
    job's `_write_handoff`, polling serve as the real job does (from another thread), sees it freshly written."""

    def flow(self):
        async def script(c):
            host, loop = c["host"], asyncio.get_running_loop()
            await c["idle"]()
            cfg = self.st.agent_config()
            stale = handoff.switch_path(cfg)

            def ctl(req):
                if req["cmd"] == "status":
                    a = host.agent
                    busy = a.status != "idle" or not a.q.empty()
                    return {"ok": True, "agent": a.kind, "agent_status": "working" if busy else "idle",
                            "agent_said_at": getattr(host, "agent_said_at", 0)}
                if req["cmd"] == "harness_handoff":
                    async def call():
                        return host.harness_handoff(req)        # on serve's loop, as the control socket would
                    return asyncio.run_coroutine_threadsafe(call(), loop).result(5)
                return {"ok": False, "error": "unknown command"}
            to = {"claude": "codex", "codex": "opencode", "opencode": "claude"}[self.KIND]
            out = {}
            th = threading.Thread(target=lambda: out.setdefault("r", sw._write_handoff(
                self.st, ctl, cfg, self.KIND, to, time.sleep, time.monotonic, limit=30)))
            th.start()
            await c["wait"](lambda: not th.is_alive(), ms=40000)
            self.assertEqual(out["r"], "ok")
            self.assertEqual(stale, self.work / ".agentj" / "handover" / "switch.md")
            self.assertTrue(stale.read_text().startswith("# Handover"))
            self.assertTrue(any(m["text"] == "交接写好了" for m in c["msgs"]()))
        self.run_chain(script)
        self.assertIn('"result": "ok"', self.st.log_path.read_text())


class ClaudeReal(_Real):
    KIND = "claude"

    def test_switch_handoff_written_by_the_harness(self):
        self.flow()


class CodexReal(_Real):
    KIND = "codex"

    def test_switch_handoff_written_by_the_harness(self):
        self.flow()


class OpenCodeReal(_Real):
    KIND = "opencode"

    def test_switch_handoff_written_by_the_harness(self):
        self.flow()


if __name__ == "__main__":
    unittest.main()
