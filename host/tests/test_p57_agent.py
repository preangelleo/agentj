"""P57 lane `agent` (0.15.2): F22 as main-Agent skills + F24 compaction preparation (PROTOCOL §15).

Skills: every folder under agentj/skills/ is linked into the four harness skill folders (a foreign copy is kept), doctor
reports them, the wheel carries them. Recall: `agentj recall` finds pages by keyword / date in the current conversation and
the archives, redacts, and works from INSIDE the fence through serve's socket (the state dir is a tmpfs there). Identity:
core v4 carries the new rules, pinned hashes = manifest. F24, for Claude Code, Codex and OpenCode with the stand-ins
(fakeclaude / fakecodex / fakeopencode): /compact without preparation → the preparation turn first (the stand-in writes the
handover) → the compaction → the next message carries the "read the handover" note once; already prepared → no preparation
turn; the stand-in writes nothing / times out → compacted anyway + the one-line notice; /stop during the preparation → not
compacted; the context meter past half → the reminder once per epoch, again after a compaction, fresh after /clear.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import compactprep, doctor, fence, history, main_identity, personalize, recall, slash  # noqa: E402
from test_slash import _Chain  # noqa: E402


def _secret():
    return "sk-ant-" + "api03-" + "Q" * 20 + "w" * 20      # built at run time: no key-shaped literal in the repo


# ------------------------------------------------------------------ skills
class Skills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.p = patch.object(pathlib.Path, "home", classmethod(lambda cls: self.home))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def run_cmd(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = personalize.command(["skill", *args])
        return code, json.loads(out.getvalue())

    def test_serve_links_free_targets_and_never_touches_a_foreign_one(self):
        """P57: `serve` calls personalize.ensure() before the harness starts — no `agentj skill install` needed."""
        foreign = self.home / ".claude/skills/agentj-recall"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("the user's own")
        n = len(personalize.bundled()) * len(personalize.HARNESS_DIRS)
        # a source checkout (this one) and tests never link by themselves — only an installed package does
        self.assertEqual(personalize.ensure(), {"linked": 0, "conflicts": 0, "off": True})
        self.assertFalse((self.home / ".codex/skills").exists())
        self.assertEqual(personalize.ensure(force=True), {"linked": n - 1, "conflicts": 1})
        self.assertEqual((foreign / "SKILL.md").read_text(), "the user's own")
        self.assertTrue((self.home / ".codex/skills/agentj-manual/manual.zh.md").is_file())
        self.assertEqual(personalize.ensure(force=True), {"linked": 0, "conflicts": 1})      # idempotent

    def test_every_bundled_skill_lands_in_the_four_harnesses(self):
        names = personalize.bundled()
        for must in ("agentj-config", "agentj-recall", "agentj-manual", "agentj-friends"):
            self.assertIn(must, names)
        code, r = self.run_cmd("install")
        self.assertEqual(code, 0, r)
        self.assertEqual(r["skills"], names)
        for name in names:
            for h in personalize.HARNESS_DIRS:
                t = self.home / h / name
                self.assertTrue(t.is_symlink(), t)
                self.assertEqual(t.resolve(), (personalize.SKILLS / name).resolve())
                self.assertTrue((t / "SKILL.md").is_file())
        self.assertEqual(doctor.check_skills(self.home)["status"], "ok")
        code, r = self.run_cmd("uninstall")
        self.assertEqual(code, 0)
        self.assertFalse(any((self.home / h / n).exists() for h in personalize.HARNESS_DIRS for n in names))

    def test_new_skill_folder_ships_itself_and_a_foreign_copy_stays(self):
        pkg = pathlib.Path(self.tmp.name) / "skills"
        for n in ("agentj-config", "zz-new"):
            (pkg / n).mkdir(parents=True)
            (pkg / n / "SKILL.md").write_text(f"---\nname: {n}\ndescription: x\n---\n")
        (pkg / "no-skill-md").mkdir()
        mine = self.home / ".claude/skills/zz-new"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("the owner's own")
        with patch.object(personalize, "SKILLS", pkg):
            self.assertEqual(personalize.bundled(), ["agentj-config", "zz-new"])
            code, r = self.run_cmd("install")
            self.assertEqual(code, 1, "a foreign copy is a conflict")
            self.assertEqual((mine / "SKILL.md").read_text(), "the owner's own", "never replaced")
            self.assertTrue((self.home / ".codex/skills/zz-new").is_symlink())
            row = doctor.check_skills(self.home)
            self.assertEqual(row["status"], "warn")
            self.assertIn("zz-new", row["summary"])
            self.assertNotIn("not linked", row["summary"])

    def test_doctor_hint_when_missing(self):
        row = doctor.check_skills(self.home)
        self.assertEqual((row["status"], row["hint"]), ("warn", "agentj skill install"))
        self.assertIn("agentj-recall", row["summary"])

    def test_skill_text(self):
        t = (personalize.SKILLS / "agentj-recall" / "SKILL.md").read_text()
        for needle in ("name: agentj-recall", "agentj recall", "接着昨天那件事", "那个 xx 项目", "重来", "换个说法",
                       "continue yesterday", "ONE line", "equally likely"):
            self.assertIn(needle, t)
        self.assertIn("name: agentj-manual", (personalize.SKILLS / "agentj-manual" / "SKILL.md").read_text())   # F23: generated from the guide
        for f in ("manual.zh.md", "manual.en.md"):
            self.assertIn("agentj.app/docs/manual/", (personalize.SKILLS / "agentj-manual" / f).read_text())

    @unittest.skipUnless(shutil.which("uv"), "uv not installed")
    def test_wheel_carries_skills_identity_and_modules(self):
        out = pathlib.Path(tempfile.mkdtemp(prefix="p57-wheel-", dir="/tmp"))
        try:
            r = subprocess.run(["uv", "build", "--wheel", "-o", str(out), str(HOST)], capture_output=True, text=True,
                               timeout=300, cwd="/tmp")
            if r.returncode != 0 and ("network" in r.stderr.lower() or "resolve" in r.stderr.lower()):
                self.skipTest("uv build needs hatchling from the network")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            whl = next(out.glob("*.whl"))
            names = set(zipfile.ZipFile(whl).namelist())
            for skill in personalize.bundled():
                self.assertIn(f"agentj/skills/{skill}/SKILL.md", names)
            for f in ("agentj/identity/core.zh.md", "agentj/identity/core.en.md", "agentj/identity/manifest.json",
                      "agentj/compactprep.py", "agentj/recall.py"):
                self.assertIn(f, names)
        finally:
            shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------------------------------ recall
def _fill(st):
    h = history.History(st, on=True)
    old = int((time.time() - 3 * 86400) * 1000)
    h.add({"k": "phone", "name": "手机", "text": "给官网加使用说明，配截图"}, "好的，先做截图。", "done", ts=old)
    h.add({"k": "phone", "name": "手机", "text": "我的密钥是 " + _secret()}, "收到，不会存。", "done", ts=old + 1000)
    h.reset("clear")                                         # → archive
    h.add({"k": "phone", "name": "手机", "text": "今天修 relay 的配对"}, "配对修好了：PROTOCOL §4。", "done")
    return h


class Recall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from test_l1 import _state
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_keyword_date_archive_redaction(self):
        _fill(self.st)
        turns = lambda: recall.from_files(self.st.root)  # noqa: E731
        hits = recall.search(turns(), "使用说明 截图")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["archived"])
        self.assertEqual(hits[0]["who"], "phone:手机")
        self.assertIn("先做截图", hits[0]["reply"])
        self.assertEqual([h["said"] for h in recall.search(turns(), "relay")], ["今天修 relay 的配对"])
        self.assertEqual(len(recall.search(turns(), "", days=1)), 1, "--days 1: only today's page")
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400))
        self.assertEqual(len(recall.search(turns(), "", date=day)), 2)
        key = recall.search(turns(), "密钥")
        self.assertEqual(len(key), 1)
        self.assertNotIn(_secret(), json.dumps(key, ensure_ascii=False))
        self.assertIn("<redacted>", key[0]["said"])
        self.assertEqual(recall.search(turns(), "没有这个词"), [])
        long = recall.search([({"id": 9, "ts": 1, "src": {"k": "agent", "text": "x" * 900}, "reply": {"text": ""}}, False)])
        self.assertLessEqual(len(long[0]["said"]), recall.EXCERPT + 1)
        for bad in ({"t": "x"}, {"t": "recall", "q": "a" * 300}, {"t": "recall", "days": -1}, {"t": "recall", "date": "x"},
                    {"t": "recall", "limit": 999}):
            with self.assertRaises(ValueError):
                recall.check(bad)

    def test_cli_reads_files_when_no_serve(self):
        _fill(self.st)
        env = {**os.environ, "AGENTJ_STATE_DIR": str(self.st.root), "PYTHONPATH": str(HOST)}
        env.pop(recall.ENV, None)
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "recall", "使用说明", "--json"], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout)
        self.assertEqual((res["via"], len(res["hits"])), ("files", 1))
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "recall", "密钥"], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertIn("<redacted>", r.stdout)
        self.assertNotIn(_secret(), r.stdout)

    def test_from_inside_the_fence_through_serve(self):
        work = pathlib.Path(self.tmp.name) / "work"
        work.mkdir()
        self.st.set_agent_config("claude", str(work))
        if fence.problem(self.st, str(work)) is not None:
            self.skipTest("no fence on this computer: " + str(fence.problem(self.st, str(work))))
        _fill(self.st)
        from test_l1 import _host
        host = _host(self.st, [])
        host.hist = history.History(self.st)                 # what serve loaded at start
        host.hist.add({"k": "phone", "name": "手机", "text": "只在内存里的一页 recall-memory"}, "", "done")

        async def go():
            run = asyncio.create_task(host.run())
            for _ in range(200):
                if os.environ.get(recall.ENV) and pathlib.Path(os.environ[recall.ENV]).exists():
                    break
                await asyncio.sleep(0.02)
            sock = os.environ[recall.ENV]
            self.assertTrue(sock.startswith(str(self.st.perm_dir)))
            env = {**os.environ, "PYTHONPATH": str(HOST)}
            outs = {}
            for name, argv in (("hit", [sys.executable, "-m", "agentj.cli", "recall", "使用说明", "--json"]),
                               ("mem", [sys.executable, "-m", "agentj.cli", "recall", "recall-memory", "--json"]),
                               ("peek", [sys.executable, "-c", "import os,sys; print(os.path.exists(sys.argv[1]))",
                                         str(self.st.root / "history" / "current.jsonl")])):
                p = await asyncio.create_subprocess_exec(*fence.wrap(self.st, argv, str(work)), cwd=str(work), env=env,
                                                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                o, e = await asyncio.wait_for(p.communicate(), 60)
                outs[name] = (p.returncode, o.decode(), e.decode())
            host.stopping.set()
            await run
            return outs
        outs = asyncio.run(go())
        self.assertEqual(outs["peek"][1].strip(), "False", "inside the fence the state dir is hidden")
        for k, n in (("hit", 1), ("mem", 1)):
            code, o, e = outs[k]
            self.assertEqual(code, 0, e)
            res = json.loads(o)
            self.assertEqual((res["via"], len(res["hits"])), ("serve", n), res)
        self.assertNotIn(recall.ENV, os.environ, "serve stopping takes its variable back")
        self.assertIn('"ev": "recall"', self.st.log_path.read_text())
        self.assertNotIn("使用说明", self.st.log_path.read_text(), "metadata only")


# ------------------------------------------------------------------ identity
class Identity(unittest.TestCase):
    def test_core_v4_rules_and_hashes(self):
        self.assertEqual(main_identity.verify_core()["version"], 8)
        man = json.loads((main_identity.DATA / "manifest.json").read_text())
        self.assertEqual(man, {"version": main_identity.VERSION, "hashes": main_identity.HASHES})
        zh = main_identity.prompt({"language": "zh"})
        en = main_identity.prompt({"language": "en"})
        for needle in ("3/5", "重来", "换个说法", "接着昨天那件事", "agentj recall", "那个 xx 项目", "/compact", "交接", "不要刷工具过程"):
            self.assertIn(needle, zh)
        for needle in ("3/5", "redo", "say it differently", "agentj-recall", "agentj recall", "That xx project", "/compact",
                       "handover", "never a tool stream"):
            self.assertIn(needle, en)
        with tempfile.TemporaryDirectory() as td:
            from test_l1 import _state
            rows = doctor.check_main_identity(_state(td))
        self.assertEqual((rows[0]["id"], rows[0]["status"]), ("main-core", "ok"))
        self.assertIn("core v8:", rows[0]["summary"])

    def test_bare_word_compact_is_the_command_only_when_alone(self):
        for t in ("压缩", "压缩一下", " 压缩。", "compact", "Compact"):
            self.assertEqual(slash.parse(t), ("compact", ""), t)
        for t in ("把这个文件压缩一下", "压缩 data 目录", "compact this file", "看看 /compact"):
            self.assertIsNone(slash.parse(t), t)


# ------------------------------------------------------------------ F24 with the stand-ins
class _Prep(_Chain):
    """Shared scripts; each harness subclass runs them against its stand-in."""
    ENV: dict = {}

    def setUp(self):
        for k, v in self.ENV.items():
            os.environ[k] = v
        super().setUp()
        self.p_to = patch.object(compactprep, "PREP_TIMEOUT", 300.0)
        self.p_to.start()

    def tearDown(self):
        self.p_to.stop()
        for k in ("FAKE_NO_HANDOVER", "FAKE_PREP_SLEEP", "FAKE_CTX_USED"):
            os.environ.pop(k, None)
        super().tearDown()

    def notes(self):
        return [x.get("host_note") for x in self.logged() if x.get("host_note")]

    def handovers(self):
        return sorted((self.work / ".agentj" / "handover").glob("*.md"))

    def prep_seen(self, c):
        return [o for _, o in c["sent"] if o["t"] == "msg" and o.get("from") == "agent" and o["text"] in ("交接写好了", "没写交接")]

    def hist_lines(self):
        p = self.st.root / "history" / "current.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def flow_prepare_then_compact(self):
        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.handovers(), [])
            # 1. not prepared → the preparation turn, then the compaction (one page: progress, then the result)
            comp = await c["cmd"]("compact")
            self.assertTrue(comp["text"].startswith("已压缩"), comp["text"])
            [ho] = self.handovers()
            self.assertIn(f"压缩前已写好交接：{ho}", comp["text"])
            self.assertTrue(ho.read_text().startswith("# Handover"))
            self.assertEqual(oct(ho.parent.stat().st_mode & 0o777), "0o700")
            self.assertEqual((self.work / ".agentj" / ".gitignore").read_text(), "*\n")
            prep = self.prep_seen(c)
            self.assertEqual([m["text"] for m in prep], ["交接写好了"])
            seq = [o for _, o in c["sent"] if o["t"] == "msg"]
            self.assertLess(seq.index(prep[0]), seq.index(comp), "the preparation before the compaction")
            page = [t for t in self.hist_lines() if t["src"].get("k") == "cmd" and t["src"]["text"] == "/compact"]
            self.assertIn(compactprep.PROGRESS["zh"], [t["reply"]["text"] for t in page], "「先写交接，再压缩」 on the page")
            self.assertTrue(page[-1]["reply"]["text"].startswith("已压缩"))
            self.after_compact_order(ho)
            # 2. the next message carries the note once
            await c["say"]("二")
            await c["wait"](lambda: any(m["text"] == "ECHO: 二" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.notes(), [compactprep.NOTE["zh"].format(path=ho)])
            await c["say"]("三")
            await c["wait"](lambda: any(m["text"] == "ECHO: 三" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(len(self.notes()), 1, "once")
            # 3. the Agent wrote the handover itself since (compact-prepare on request) → no preparation turn
            time.sleep(0.05)
            ho.write_text("# Handover\nwritten by the Agent on request\n")
            n = len(self.prep_seen(c))
            comp2 = await c["cmd"]("compact")
            self.assertTrue(comp2["text"].startswith("已压缩"), comp2["text"])
            self.assertIn("压缩前已写好交接", comp2["text"])
            self.assertEqual(len(self.prep_seen(c)), n, "already prepared: no preparation turn")
            self.assertEqual(ho.read_text(), "# Handover\nwritten by the Agent on request\n")
            # 4. /clear: a new conversation starts clean (no note for its first message)
            await c["cmd"]("clear", confirm=True)
            await c["say"]("四")
            await c["wait"](lambda: any(m["text"] == "ECHO: 四" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(len(self.notes()), 1)
            # 5. typed 「压缩」 is /compact too (a new conversation: prepared afresh into its own file)
            await c["say"]("压缩")
            await c["wait"](lambda: len(c["cards"]("compact")) == 3 and "已压缩" in c["cards"]("compact")[-1]["text"])
            self.assertEqual(len(self.handovers()), 2)
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "compact_prep"', log)
        for secret_ish in ("Handover", "交接写好了", ".agentj/handover", "ECHO"):
            self.assertNotIn(secret_ish, log, "metadata only in the log")
        self.assert_activity_has_no_text()

    def after_compact_order(self, ho):
        """Per harness: the preparation reached the harness before its compaction call."""

    def flow_prepare_fails(self, timeout=False):
        if timeout:
            compactprep.PREP_TIMEOUT = 1.5

        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            comp = await c["cmd"]("compact")
            self.assertTrue(comp["text"].startswith("已压缩"), comp["text"])
            self.assertTrue(comp["text"].endswith(compactprep.FAILED["zh"]), comp["text"])
            self.assertEqual(comp["text"].count("\n"), 1, "one line")
            self.assertEqual(self.handovers(), [])
            await c["say"]("二")
            await c["wait"](lambda: any(m["text"] == "ECHO: 二" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.notes(), [], "no handover: no note")
        self.run_chain(script)
        self.assertIn('"result": "timeout"' if timeout else '"result": "missing"', self.st.log_path.read_text())

    def flow_reminder(self):
        async def script(c):
            async def talk(t):
                await c["say"](t)
                await c["wait"](lambda: any(m["text"] == f"ECHO: {t}" for m in c["msgs"]()))
                await c["idle"]()
            remind = compactprep.REMIND["zh"]
            await talk("一")                       # the meter reads past half after this turn
            self.assertGreater(c["host"].meter_state["ctx"]["used"] / c["host"].meter_state["ctx"]["max"], 0.5)
            await talk("二")
            self.assertEqual(self.notes(), [remind])
            await talk("三")
            self.assertEqual(self.notes(), [remind], "≤ once per epoch")
            await c["cmd"]("compact")
            await talk("四")                       # the meter is low right after the compaction: the handover note only
            self.assertEqual(self.notes()[1:], [compactprep.NOTE["zh"].format(path=self.handovers()[0])])
            await talk("五")
            self.assertEqual(self.notes()[2:], [remind], "reset by the compaction")
            await c["cmd"]("clear", confirm=True)
            await talk("六")                       # a new conversation: nothing yet (its first turn sets the meter)
            self.assertEqual(len(self.notes()), 3)
            await talk("七")
            self.assertEqual(self.notes()[3:], [remind], "/clear: a fresh conversation can be reminded")
        self.run_chain(script)

    def flow_stop_during_preparation(self):
        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            await c["host"]._app(c["s"], {"t": "slash", "cmd": "compact", "arg": ""})
            await c["wait"](lambda: c["host"].agent.status == "working")
            await asyncio.sleep(0.5)
            stop = await c["cmd"]("stop")
            self.assertTrue(stop["text"].startswith("已停下这一轮"), stop)
            await c["wait"](lambda: c["cards"]("compact") and c["cards"]("compact")[-1]["text"] == compactprep.STOPPED["zh"])
            await c["idle"]()
            await c["say"]("还在")
            await c["wait"](lambda: any(m["text"] == "ECHO: 还在" for m in c["msgs"]()))
        self.run_chain(script)
        self.assertIn('"result": "stopped"', self.st.log_path.read_text())


class ClaudePrep(_Prep):
    KIND = "claude"

    def test_preparation_accepts_native_interrupt_before_sleep_finishes(self):
        """The native stand-in must consume controls during a slow preparation, like Claude does."""
        os.environ["FAKE_PREP_SLEEP"] = "6"

        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            a = c["host"].agent
            key = compactprep.session_key(a)
            compactprep.ensure_dir(str(self.work))
            old_done = a.turn_done
            task = asyncio.create_task(a.turn(compactprep.PREP["zh"].format(
                path=compactprep.handover_path(a, key))))
            try:
                await c["wait"](lambda: a.turn_done is not old_done)
                await asyncio.wait_for(a.control("interrupt"), 2)
                await asyncio.wait_for(task, 2)
                self.assertEqual(self.handovers(), [], "interrupted preparation cannot write the handover")
            finally:
                if not task.done():
                    await a.halt(clear_queue=False)
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        self.run_chain(script)

    def test_prepare_then_compact(self):
        self.flow_prepare_then_compact()

    def after_compact_order(self, ho):
        self.assertIn(".agentj", str(ho))

    def test_stop_during_preparation(self):
        os.environ["FAKE_PREP_SLEEP"] = "20"
        self.flow_stop_during_preparation()


class ClaudePrepFails(_Prep):
    KIND = "claude"
    ENV = {"FAKE_NO_HANDOVER": "1"}

    def test_prepare_fails(self):
        self.flow_prepare_fails()


class ClaudePrepTimeout(_Prep):
    KIND = "claude"
    ENV = {"FAKE_PREP_SLEEP": "6"}

    def test_prepare_times_out(self):
        self.flow_prepare_fails(timeout=True)


class ClaudeRemind(_Prep):
    KIND = "claude"
    ENV = {"FAKE_CTX_USED": "150000"}

    def test_reminder(self):
        self.flow_reminder()


class CodexPrep(_Prep):
    KIND = "codex"

    def test_prepare_then_compact(self):
        self.flow_prepare_then_compact()
        lg = self.logged()
        idx = [i for i, x in enumerate(lg) if x.get("method") == "turn/start"
               and any(str(p.get("text", "")).startswith(compactprep.MARK) for p in x["params"]["input"])]
        comp = [i for i, x in enumerate(lg) if x.get("method") == "thread/compact/start"]
        self.assertEqual(len(idx), 2, "two preparations (the second /compact was already prepared)")
        self.assertEqual(len(comp), 3)
        self.assertLess(idx[0], comp[0])
        self.assertLess(idx[1], comp[2])


class CodexPrepFails(_Prep):
    KIND = "codex"
    ENV = {"FAKE_NO_HANDOVER": "1"}

    def test_prepare_fails(self):
        self.flow_prepare_fails()
        self.assertEqual(sum(1 for x in self.logged() if x.get("method") == "thread/compact/start"), 1, "compacted anyway")


class CodexPrepTimeout(_Prep):
    KIND = "codex"
    ENV = {"FAKE_PREP_SLEEP": "6"}

    def test_prepare_times_out(self):
        self.flow_prepare_fails(timeout=True)


class CodexRemind(_Prep):
    KIND = "codex"
    ENV = {"FAKE_CTX_USED": "200000"}

    def test_reminder(self):
        self.flow_reminder()


class OpenCodePrep(_Prep):
    KIND = "opencode"

    def test_prepare_then_compact(self):
        self.flow_prepare_then_compact()
        lg = self.logged()
        idx = [i for i, x in enumerate(lg) if x.get("method") == "POST" and x["path"].endswith("/prompt_async")
               and str(((x.get("body") or {}).get("parts") or [{}])[0].get("text", "")).startswith(compactprep.MARK)]
        comp = [i for i, x in enumerate(lg) if x.get("method") == "POST" and x["path"].endswith("/summarize")]
        self.assertEqual((len(idx), len(comp)), (2, 3))
        self.assertLess(idx[0], comp[0])

    def test_stop_during_preparation(self):
        os.environ["FAKE_PREP_SLEEP"] = "20"
        self.flow_stop_during_preparation()
        self.assertFalse(any(x.get("path", "").endswith("/summarize") for x in self.logged()), "not compacted")


class OpenCodePrepFails(_Prep):
    KIND = "opencode"
    ENV = {"FAKE_NO_HANDOVER": "1"}

    def test_prepare_fails(self):
        self.flow_prepare_fails()


class OpenCodePrepTimeout(_Prep):
    KIND = "opencode"
    ENV = {"FAKE_PREP_SLEEP": "6"}

    def test_prepare_times_out(self):
        self.flow_prepare_fails(timeout=True)


class OpenCodeRemind(_Prep):
    KIND = "opencode"
    ENV = {"FAKE_CTX_USED": "150000"}

    def test_reminder(self):
        self.flow_reminder()


# ------------------------------------------------------------------ units
class Units(unittest.TestCase):
    def test_not_for_shared_ceo_or_scheduled_runs(self):
        a = SimpleNamespace(kind="claude", cfg={"dir": "/tmp"}, cmd_compact=lambda: None)
        self.assertTrue(compactprep.applies(a))
        for cfg in ({"dir": "/tmp", "session_mode": "shared"}, {"dir": "/tmp", "_workflow_ceo": True}):
            self.assertFalse(compactprep.applies(SimpleNamespace(kind="claude", cfg=cfg, cmd_compact=lambda: None)))
        self.assertFalse(compactprep.applies(SimpleNamespace(kind="codex", cfg={"dir": "/tmp"}, persist=False,
                                                             cmd_compact=lambda: None)))

    def test_handover_is_never_read_through_a_link(self):
        with tempfile.TemporaryDirectory() as td:
            work = pathlib.Path(td)
            self.assertTrue(compactprep.ensure_dir(td))
            outside = work / "outside.md"
            outside.write_text("secret-ish")
            (work / ".agentj/handover/k.md").symlink_to(outside)
            self.assertIsNone(compactprep.probe(td, "k"))
            (work / ".agentj/handover/k.md").unlink()
            (work / ".agentj/handover/k.md").write_text("   \n")
            self.assertIsNone(compactprep.probe(td, "k"), "blank is not a handover")
            (work / ".agentj/handover/k.md").write_text("# x\n")
            self.assertIsNotNone(compactprep.probe(td, "k"))
            shutil.rmtree(work / ".agentj")
            (work / ".agentj").symlink_to(work / "elsewhere", target_is_directory=True)
            (work / "elsewhere").mkdir()
            self.assertFalse(compactprep.ensure_dir(td), "a linked .agentj is refused")

    def test_session_key_is_file_safe(self):
        st = SimpleNamespace(agent_session=lambda k: "../../a b/c")
        a = SimpleNamespace(kind="codex", host=SimpleNamespace(st=st), cfg={"dir": "/w"})
        k = compactprep.session_key(a)
        self.assertNotIn("/", k)
        self.assertTrue(k.startswith("codex-"))
        self.assertIsNone(compactprep.session_key(SimpleNamespace(kind="codex", host=SimpleNamespace(
            st=SimpleNamespace(agent_session=lambda k: None)), cfg={})))


if __name__ == "__main__":
    unittest.main()
