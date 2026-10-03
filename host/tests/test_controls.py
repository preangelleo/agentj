"""Phone controls (PROMPT-26 item 3; ADR-A50 – A54; PROTOCOL §8 "phone controls").

Signatures: the same bytes in Python and JS; every field bound; stale / replayed / unsigned / wrongly signed commands do
nothing. Memory: sources per harness (Claude Code auto memory under the git root's slug, measured with 2.1.285), Markdown split
into items, delete with an optimistic lock, trash + restore, a memory file's index lines go and come back with it, symlinks not
followed, size limits. Activity log: pages newest first with cursors, off switch, 30 days / 20 MiB / 8 MiB a day. Tasks: the
contract is validated, enabling is bound to the contract hash, cron. Serve: list / delete / undo / enable over the E2E session,
stop everything (open requests denied, grants ended, messages refused, persisted across a restart), resume; the scheduler runs
a task through the stand-in Claude Code inside the fence (approvals reach the phone with the task's name, a research run is
read-only, VERDICT → one notice), a stop ends a running task and a running chat turn at once.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import activity, approvals, controls, memory, serve, tasks, wire  # noqa: E402
from agentj.state import State  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def sign(ph: Phone, action: str, target: dict, extra: dict, *, ts=None, n=None, key=None) -> dict:
    n = n or os.urandom(16).hex()
    ts = int(time.time() * 1000) if ts is None else ts
    msg = controls.signed_message(ph.channel, ph.did, action, n, ts, controls.object_digest(action, target))
    return {**extra, "n": n, "ts": ts, "sig": wire.b64u((key or ph.sk).sign(msg))}


def task_dir(work: pathlib.Path, tid: str, run: str, mode="research", schedule="0 8 * * *", **kw) -> pathlib.Path:
    d = work / "workflows" / tid
    d.mkdir(parents=True, exist_ok=True)
    t = {"v": 1, "id": tid, "title": {"zh": f"任务{tid}", "en": f"Task {tid}"}, "schedule": schedule, "tz": "local",
         "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md", "mode": mode, "enabled": False,
         "needs": ["订单数据 / orders"], "outputs": ["reports/YYYY-MM-DD.md"], **kw}
    (d / "task.json").write_text(json.dumps(t, ensure_ascii=False))
    (d / "RUN.md").write_text(run)
    (d / "DRYRUN.md").write_text("dry\n")
    return d


# ------------------------------------------------------------------ signatures
class Signatures(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_sign_the_same_bytes(self):
        cases = [("mem_rm", {"src": "claude.project", "file": "", "fsha": "ab" * 32, "iid": "0123456789abcdef~1"}),
                 ("mem_undo", {"id": "0123456789abcdef"}), ("estop", {}), ("resume", {}),
                 ("task_on", {"id": "daily-report", "tsha": "cd" * 32}), ("task_off", {"id": "x", "tsha": ""}),
                 ("mem_rm", {"src": "claude.auto", "file": "用户 偏好.md", "fsha": "ef" * 32, "iid": "file"})]
        wire_js = (HERE.parents[1] / "protocol" / "wire.js").as_uri()
        js = (f"import {{ controlMessage }} from {json.dumps(wire_js)};\n"
              f"const out = [];\nfor (const [a, o] of {json.dumps(cases, ensure_ascii=False)}) out.push(Buffer.from(await "
              f"controlMessage('CH', 'DEV', a, '{'a' * 32}', 1790000000123, o)).toString('hex'));\nconsole.log(JSON.stringify(out));")
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        want = [controls.signed_message("CH", "DEV", a, "a" * 32, 1790000000123, controls.object_digest(a, o)).hex() for a, o in cases]
        self.assertEqual(got, want)

    def test_check_refuses_everything_but_a_fresh_valid_signature(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            ph, other = Phone(st), Phone(st, "别的")
            nonces = controls.Nonces()
            tgt = {"src": "s", "file": "", "fsha": "f" * 64, "iid": "i"}
            ok = sign(ph, "mem_rm", tgt, {})
            self.assertIsNone(controls.check(st, nonces, ph.channel, ph.did, ok, "mem_rm", tgt))
            self.assertEqual(controls.check(st, nonces, ph.channel, ph.did, ok, "mem_rm", tgt), "replay")
            for obj, act, t, why in [
                (sign(ph, "mem_rm", tgt, {}), "mem_rm", {**tgt, "iid": "other"}, "bad_signature"),     # re-pointed target
                (sign(ph, "mem_rm", tgt, {}), "mem_rm", {**tgt, "fsha": "0" * 64}, "bad_signature"),   # other content
                (sign(ph, "estop", {}, {}), "resume", {}, "bad_signature"),                             # other action
                (sign(ph, "estop", {}, {}, key=other.sk), "estop", {}, "bad_signature"),               # someone else's key
                (sign(ph, "estop", {}, {}, ts=int(time.time() * 1000) - 300_000), "estop", {}, "stale"),
                ({"n": "x", "ts": 1, "sig": "y"}, "estop", {}, "shape"),
                ({}, "estop", {}, "shape"),
            ]:
                self.assertEqual(controls.check(st, nonces, ph.channel, ph.did, obj, act, t), why, (act, why))
            nokey = Phone(st, "旧手机", with_key=False)
            self.assertEqual(controls.check(st, nonces, nokey.channel, nokey.did, sign(ph, "estop", {}, {}), "estop", {}),
                             "no_key")

    def test_estop_switch_persists_and_a_broken_file_reads_as_on(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            self.assertFalse(controls.estop_state(st)["on"])
            controls.set_estop(st, True, "terminal")
            self.assertTrue(controls.estop_state(st)["on"])
            self.assertEqual(oct(st.estop_path.stat().st_mode & 0o777), "0o600")
            controls.set_estop(st, False, "terminal")
            self.assertFalse(controls.estop_state(st)["on"])
            st.estop_path.write_text("{garbage")
            self.assertTrue(controls.estop_state(st)["on"], "fail closed")


# ------------------------------------------------------------------ memory
MD = """---
name: x
---
# 规则

- 永远用中文回答
  （包括代码注释）
- 部署在周五

第一段第一行
第一段第二行

```bash
rm -rf /
```

- 永远用中文回答
"""


class Memory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.cfg = self.root / "claudecfg"
        self.cfg.mkdir()
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cfg), "CODEX_HOME": str(self.root / "codexhome"),
                                                "XDG_CONFIG_HOME": str(self.root / "xdg")})
        self.env.start()
        self.repo = self.root / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.work = self.repo / "sub dir_x.y"
        self.work.mkdir()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def src(self, res, sid):
        return next(s for s in res["sources"] if s["id"] == sid)

    def test_split_items(self):
        items = memory.split_items(MD)
        kinds = [(i["kind"], i["text"].split("\n")[0]) for i in items]
        self.assertEqual(kinds, [("heading", "# 规则"), ("list", "- 永远用中文回答"), ("list", "- 部署在周五"),
                                 ("para", "第一段第一行"), ("code", "```bash"), ("list", "- 永远用中文回答")])
        self.assertIn("（包括代码注释）", items[1]["text"], "an indented line belongs to its list item")
        rep = memory.split_items("- a\n- b\n- a\n")
        self.assertEqual([i["iid"] for i in rep], [memory.sha(b"- a")[:16],
                                                   memory.sha(b"- b")[:16], memory.sha(b"- a")[:16] + "~1"], "a repeat gets ~k")
        self.assertNotIn("name: x", " ".join(i["text"] for i in items), "front matter is not an item")

    def test_claude_sources_slug_is_the_git_root(self):
        mem = self.cfg / "projects" / memory.slug(str(self.repo.resolve())) / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("- [偏好](user_prefs.md) — 喜欢 teal\n- [部署](deploy.md) — 周五\n")
        (mem / "user_prefs.md").write_text("---\nname: user_preferences\ndescription: 偏好\n---\n\n**颜色：** teal\n")
        (mem / "deploy.md").write_text("周五部署\n")
        (self.work / "CLAUDE.md").write_text(MD)
        (self.cfg / "CLAUDE.md").write_text("- 全局规则\n")
        res = memory.scan("claude", str(self.work))
        self.assertEqual([s["id"] for s in res["sources"]], ["claude.project", "claude.local", "claude.dot", "claude.user", "claude.auto"])
        self.assertEqual(memory.slug("/tmp/ajprobe/work/sub dir_x.y"), "-tmp-ajprobe-work-sub-dir-x-y", "measured 2.1.285")
        auto = self.src(res, "claude.auto")
        files = {f["file"]: f for f in auto["files"]}
        self.assertEqual(list(files), ["MEMORY.md", "deploy.md", "user_prefs.md"], "MEMORY.md (the index) first")
        self.assertEqual(len(files["MEMORY.md"]["items"]), 2, "the index is split into lines")
        up = files["user_prefs.md"]["items"]
        self.assertEqual((len(up), up[0]["iid"], up[0]["title"], up[0]["text"]), (1, "file", "user_preferences", "**颜色：** teal"))
        self.assertEqual(self.src(res, "claude.local")["problem"], "not_found")
        self.assertEqual(len(self.src(res, "claude.project")["files"][0]["items"]), 6)

    def test_remove_item_trash_restore_roundtrip(self):
        p = self.work / "CLAUDE.md"
        p.write_text(MD)
        p.chmod(0o640)
        f = self.src(memory.scan("claude", str(self.work)), "claude.project")["files"][0]
        it = f["items"][2]                                    # - 部署在周五
        rec = memory.remove(self.st, "claude", str(self.work), "claude.project", "", f["fsha"], it["iid"])
        self.assertNotIn("部署在周五", p.read_text())
        self.assertIn("永远用中文回答", p.read_text())
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o640", "the file keeps its mode")
        tp = self.st.root / "memory_trash" / f"{rec['id']}.json"
        self.assertEqual(oct(tp.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(tp.parent.stat().st_mode & 0o777), "0o700")
        self.assertIn("部署在周五", tp.read_text())
        self.assertEqual([t["id"] for t in memory.trash(self.st)], [rec["id"]])
        memory.restore(self.st, rec["id"])
        self.assertEqual(p.read_text(), MD, "restored exactly where it was")
        self.assertFalse(tp.exists())
        with self.assertRaises(memory.MemoryError_) as e:
            memory.restore(self.st, rec["id"])
        self.assertEqual(e.exception.reason, "not_found")

    def test_restore_after_the_file_moved_on_finds_its_neighbours(self):
        p = self.work / "CLAUDE.md"
        p.write_text("- a\n- b\n- c\n")
        f = self.src(memory.scan("claude", str(self.work)), "claude.project")["files"][0]
        rec = memory.remove(self.st, "claude", str(self.work), "claude.project", "", f["fsha"], f["items"][1]["iid"])
        p.write_text("- new first\n- a\n- c\n")
        memory.restore(self.st, rec["id"])
        self.assertEqual(p.read_text(), "- new first\n- a\n- b\n- c\n")

    def test_changed_file_is_refused(self):
        p = self.work / "CLAUDE.md"
        p.write_text(MD)
        f = self.src(memory.scan("claude", str(self.work)), "claude.project")["files"][0]
        p.write_text(MD + "\n- 有人刚加了一条\n")
        before = p.read_text()
        for fsha, iid, why in [(f["fsha"], f["items"][1]["iid"], "changed"), (memory.sha(before.encode()), "nope", "unknown_item")]:
            with self.assertRaises(memory.MemoryError_) as e:
                memory.remove(self.st, "claude", str(self.work), "claude.project", "", fsha, iid)
            self.assertEqual(e.exception.reason, why)
        self.assertEqual(p.read_text(), before)
        self.assertEqual(memory.trash(self.st), [])
        with self.assertRaises(memory.MemoryError_) as e:
            memory.remove(self.st, "claude", str(self.work), "claude.nope", "", f["fsha"], "x")
        self.assertEqual(e.exception.reason, "unknown_source")

    def test_memory_file_delete_takes_its_index_lines_and_undo_brings_them_back(self):
        mem = self.cfg / "projects" / memory.slug(str(self.repo.resolve())) / "memory"
        mem.mkdir(parents=True)
        idx = "# 索引\n- [偏好](user_prefs.md) — teal\n- [部署](deploy.md) — 周五\n"
        (mem / "MEMORY.md").write_text(idx)
        (mem / "deploy.md").write_text("周五部署\n")
        auto = self.src(memory.scan("claude", str(self.work)), "claude.auto")
        f = next(x for x in auto["files"] if x["file"] == "deploy.md")
        rec = memory.remove(self.st, "claude", str(self.work), "claude.auto", "deploy.md", f["fsha"], "file")
        self.assertFalse((mem / "deploy.md").exists())
        self.assertNotIn("deploy.md", (mem / "MEMORY.md").read_text())
        memory.restore(self.st, rec["id"])
        self.assertEqual((mem / "deploy.md").read_text(), "周五部署\n")
        self.assertEqual((mem / "MEMORY.md").read_text(), idx)
        for bad in ("../x.md", ".hidden.md", "a/b.md", "x.txt"):
            with self.assertRaises(memory.MemoryError_):
                memory.remove(self.st, "claude", str(self.work), "claude.auto", bad, f["fsha"], "file")

    def test_symlinks_are_not_followed_and_size_is_bounded(self):
        secret = self.root / "secret.md"
        secret.write_text("- 机密\n")
        (self.work / "CLAUDE.md").symlink_to(secret)
        (self.work / ".claude").symlink_to(self.root)
        mem = self.cfg / "projects" / memory.slug(str(self.repo.resolve())) / "memory"
        mem.mkdir(parents=True)
        (mem / "link.md").symlink_to(secret)
        (mem / "big.md").write_bytes(b"x" * (memory.MAX_FILE + 1))
        res = memory.scan("claude", str(self.work))
        self.assertEqual(self.src(res, "claude.project")["problem"], "symlink")
        self.assertEqual(self.src(res, "claude.dot")["problem"], "symlink")
        probs = {f["file"]: f.get("problem") for f in self.src(res, "claude.auto")["files"]}
        self.assertEqual(probs, {"big.md": "too_large", "link.md": "symlink"})
        self.assertNotIn("机密", json.dumps(res, ensure_ascii=False))
        with self.assertRaises(memory.MemoryError_) as e:
            memory.remove(self.st, "claude", str(self.work), "claude.project", "", memory.sha(secret.read_bytes()), "x")
        self.assertEqual(e.exception.reason, "symlink")
        self.assertEqual(secret.read_text(), "- 机密\n")

    def test_codex_and_opencode_sources(self):
        ch = self.root / "codexhome"
        (ch / "memories").mkdir(parents=True)
        (ch / "AGENTS.md").write_text("- codex 全局\n")
        (ch / "memories" / "memory_summary.md").write_text("总结\n")
        (self.work / "AGENTS.md").write_text("- 项目\n")
        res = memory.scan("codex", str(self.work))
        self.assertEqual([s["id"] for s in res["sources"]], ["codex.user", "codex.user_override", "codex.project",
                                                              "codex.project_override", "codex.memories"])
        self.assertEqual(self.src(res, "codex.memories")["files"][0]["file"], "memory_summary.md")
        res = memory.scan("opencode", str(self.work))
        ids = [s["id"] for s in res["sources"]]
        self.assertEqual(ids, ["opencode.project", "opencode.user", "opencode.user_claude"], "no global AGENTS.md → ~/.claude fallback")
        self.assertEqual(memory.scan(None, str(self.work))["sources"], [])

    def test_trash_is_kept_seven_days(self):
        (self.work / "CLAUDE.md").write_text("- a\n- b\n")
        f = self.src(memory.scan("claude", str(self.work)), "claude.project")["files"][0]
        memory.remove(self.st, "claude", str(self.work), "claude.project", "", f["fsha"], f["items"][0]["iid"])
        self.assertEqual(memory.purge_trash(self.st, time.time() + 6 * 86400), 0)
        self.assertEqual(memory.purge_trash(self.st, time.time() + 8 * 86400), 1)
        self.assertEqual(memory.trash(self.st), [])


# ------------------------------------------------------------------ activity log
class Activity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_newest_first_with_cursors(self):
        for i in range(120):
            activity.record(self.st, "ask", id=str(i), tool="Bash", summary=f"echo {i}")
        d = self.st.root / "activity"
        self.assertEqual(oct(d.stat().st_mode & 0o777), "0o700")
        f = next(d.iterdir())
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        p1, c1 = activity.page(self.st)
        self.assertEqual((len(p1), p1[0]["id"], p1[-1]["id"]), (50, "119", "70"))
        p2, c2 = activity.page(self.st, c1)
        p3, c3 = activity.page(self.st, c2)
        self.assertEqual((p2[0]["id"], len(p3), p3[-1]["id"], c3), ("69", 20, "0", None))
        activity.record(self.st, "estop", by="手机")             # appends after the cursor change nothing
        self.assertEqual(activity.page(self.st, c1)[0][0]["id"], "69")
        self.assertEqual(len(activity.all_since(self.st)), 121)
        self.assertEqual(activity.page(self.st, n=500)[0].__len__(), 50, "≤ 50 per page")

    def test_off_switch_and_retention(self):
        activity.set_enabled(self.st, False)
        self.assertIsNone(activity.record(self.st, "estop"))
        self.assertEqual(activity.days(self.st), [])
        activity.set_enabled(self.st, True)
        d = self.st.root / "activity"
        d.mkdir(mode=0o700, exist_ok=True)
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 31 * 86400))
        recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 2 * 86400))
        (d / f"{old}.jsonl").write_text('{"ts":1,"k":"estop"}\n')
        (d / f"{recent}.jsonl").write_bytes(b"x" * 10)
        activity.record(self.st, "resume")
        activity.purge(self.st)
        self.assertNotIn(f"{old}.jsonl", activity.days(self.st))
        self.assertIn(f"{recent}.jsonl", activity.days(self.st))
        with mock.patch.object(activity, "MAX_TOTAL", 5):
            activity.purge(self.st)
        self.assertEqual(len(activity.days(self.st)), 1, "over the total cap the oldest days go, never today")
        self.assertEqual(activity.clear(self.st), 1)

    def test_one_day_is_capped(self):
        with mock.patch.object(activity, "MAX_DAY", 2000):
            for i in range(40):
                activity.record(self.st, "ask", summary="长" * 100)
            items = activity.all_since(self.st)
        self.assertEqual(items[-1]["k"], "truncated")
        self.assertEqual(sum(1 for r in items if r["k"] == "truncated"), 1)
        self.assertLessEqual(sum((self.st.root / "activity" / n).stat().st_size for n in activity.days(self.st)), 2000 + 400)


# ------------------------------------------------------------------ tasks (contract, enabling, cron)
class Tasks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_discover_validates_and_enabling_is_the_humans_bound_to_the_contract(self):
        task_dir(self.work, "daily-report", "SAY: VERDICT: ok — 好", enabled=True)
        task_dir(self.work, "bad", "x", mode="yolo")
        (self.work / "workflows" / "Not_An_Id").mkdir()
        rows = {r["id"]: r for r in tasks.rows(self.st, str(self.work))}
        self.assertEqual(set(rows), {"daily-report", "bad", "Not_An_Id"})
        self.assertTrue(rows["daily-report"]["suggested"] and not rows["daily-report"]["enabled"],
                        "task.json enabled:true is only a suggestion")
        self.assertIn("mode must be normal or research", rows["bad"]["problems"])
        with self.assertRaises(tasks.TaskError):
            tasks.set_enabled(self.st, str(self.work), "bad", True, "terminal")
        with self.assertRaises(tasks.TaskError) as e:
            tasks.set_enabled(self.st, str(self.work), "daily-report", True, "phone", tsha="0" * 64)
        self.assertEqual(e.exception.reason, "changed")
        tasks.set_enabled(self.st, str(self.work), "daily-report", True, "terminal")
        r = next(r for r in tasks.rows(self.st, str(self.work)) if r["id"] == "daily-report")
        self.assertTrue(r["enabled"] and r["next"])
        self.assertEqual(json.loads((self.work / "workflows/daily-report/task.json").read_text())["enabled"], True,
                         "task.json is never written")
        self.assertEqual(oct(self.st.tasks_path.stat().st_mode & 0o777), "0o600")
        (self.work / "workflows/daily-report/RUN.md").write_text("SAY: 被 Agent 改过的说明")
        r = next(r for r in tasks.rows(self.st, str(self.work)) if r["id"] == "daily-report")
        self.assertTrue(r["stale"] and not r["enabled"], "a changed prompt needs the human again")

    def test_symlinked_workflows_are_ignored(self):
        other = pathlib.Path(self.tmp.name) / "elsewhere"
        task_dir(other, "x", "SAY: hi")
        (self.work / "workflows").mkdir()
        (self.work / "workflows" / "x").symlink_to(other / "workflows" / "x")
        self.assertEqual(tasks.discover(str(self.work)), [])

    def test_cron(self):
        z = dt.timezone.utc
        n = dt.datetime(2026, 10, 2, 14, 3, tzinfo=z)          # a Friday
        self.assertTrue(tasks.matches("3 14 * * *", "UTC", n))
        self.assertTrue(tasks.matches("*/3 * * * 5", "UTC", n))
        self.assertTrue(tasks.matches("3 14 1 * 5", "UTC", n), "dom and dow both restricted: either matches")
        self.assertFalse(tasks.matches("3 14 1 * 1", "UTC", n))
        self.assertTrue(tasks.matches("3 23 * * *", "Asia/Tokyo", n))
        self.assertTrue(tasks.matches("3 14 * * 7", "UTC", dt.datetime(2026, 10, 4, 14, 3, tzinfo=z)), "7 = Sunday")
        self.assertEqual(tasks.next_run("0 8 * * *", "Asia/Tokyo", n).isoformat(), "2026-10-03T08:00:00+09:00")
        self.assertEqual(tasks.next_run("0 9 * * 1", "UTC", n).isoformat(), "2026-10-05T09:00:00+00:00")
        self.assertEqual(tasks.parse_verdict("x\nVERDICT: attention — 两个异常\n"), ("attention", "两个异常"))
        self.assertEqual(tasks.parse_verdict("**VERDICT:** ok — fine"), ("ok", "fine"))
        self.assertIsNone(tasks.parse_verdict("必须写 `VERDICT: ok|attention|fail — 一句话`"))


# ------------------------------------------------------------------ the terminal side (serve not running)
class Cli(unittest.TestCase):
    def test_memory_tasks_stop_resume_activity_at_the_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            work = d / "work"
            work.mkdir()
            env = {**os.environ, "AGENTJ_STATE_DIR": str(d / "state"), "CLAUDE_CONFIG_DIR": str(d / "cfg")}
            agentj = str(HERE.parent / "bin" / "agentj")

            def run(*a, inp=None, ok=True):
                r = subprocess.run([agentj, *a], env=env, input=inp, capture_output=True, text=True, timeout=60)
                if ok:
                    self.assertEqual(r.returncode, 0, (a, r.stdout, r.stderr))
                return r
            run("init", "--relay", "ws://127.0.0.1:1")
            run("passphrase", "set", inp="pass-phrase-1\npass-phrase-1\n")
            run("agent", "claude", "--dir", str(work))
            (work / "CLAUDE.md").write_text("- a\n- b 第二条\n\n段落\n")
            task_dir(work, "daily", "SAY: VERDICT: ok — 好")
            self.assertIn("3 条", run("memory", "list").stdout)
            out = run("memory", "rm", "1", "2").stdout
            self.assertNotIn("第二条", (work / "CLAUDE.md").read_text())
            tid = out.split("restore ")[1].split("`")[0]
            run("memory", "restore", tid)
            self.assertEqual((work / "CLAUDE.md").read_text(), "- a\n- b 第二条\n\n段落\n")
            r = run("tasks", "enable", "daily", inp="wrong-pass\n", ok=False)
            self.assertNotEqual(r.returncode, 0, "enable needs the passphrase")
            self.assertIn("· 未启用", run("tasks", "list").stdout)
            run("tasks", "enable", "daily", inp="pass-phrase-1\n")
            self.assertIn("✓ 已启用", run("tasks", "list").stdout)
            self.assertIn('"read_only": true', run("tasks", "run", "daily", "--dry-run").stdout)
            self.assertNotEqual(run("tasks", "run", "daily", ok=False).returncode, 0, "a real run needs serve")
            run("stop")
            self.assertIn("已急停（", run("status").stdout)
            self.assertIn("! estop", run("doctor", "--offline", ok=False).stdout)
            self.assertNotEqual(run("resume", inp="wrong-pass\n", ok=False).returncode, 0)
            st = State(d / "state")
            self.assertTrue(controls.estop_state(st)["on"], "a wrong passphrase does not resume")
            run("resume", inp="pass-phrase-1\n")
            self.assertFalse(controls.estop_state(st)["on"])
            acts = run("activity").stdout
            for w in ("删除记忆（终端）", "恢复记忆（终端）", "启用定时任务（终端） daily", "⛔ 急停（终端）", "恢复（终端）"):
                self.assertIn(w, acts)
            run("config", "activity", "off")
            self.assertFalse(activity.enabled(st))
            self.assertIn("已删除", run("activity", "--clear").stdout)


# ------------------------------------------------------------------ serve: the controls over the E2E session
class ServeControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.cfg = pathlib.Path(self.tmp.name) / "claudecfg"
        self.cfg.mkdir()
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cfg)})
        self.env.start()
        self.st.set_agent_config("claude", str(self.work))
        self.sent = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def got(self, t, r=None):
        return [o for _, o in self.sent if o["t"] == t and (r is None or o.get("r") == r)]

    def test_memory_list_signed_delete_undo_and_refusals(self):
        (self.work / "CLAUDE.md").write_text(MD)
        ph, host = Phone(self.st), _host(self.st, self.sent)
        s = _ready(host, ph)

        async def go():
            await host._app(s, {"t": "mem_list", "r": "m1"})
            srcs = self.got("mem_sources", "m1")[0]
            items = [i for o in self.got("mem_items", "m1") for i in o["items"]]
            self.assertEqual(srcs["harness"], "claude")
            self.assertEqual(self.got("mem_items", "m1")[-1]["more"], False)
            it = next(i for i in items if i["text"] == "- 部署在周五")
            tgt = {k: it[k] for k in ("src", "file", "fsha", "iid")}
            # unsigned / wrongly signed / signed over another item: refused, nothing changes
            await host._app(s, {"t": "mem_rm", "r": "x1", **tgt})
            other = Phone(self.st, "别的")
            await host._app(s, sign(ph, "mem_rm", tgt, {"t": "mem_rm", "r": "x2", **tgt}, key=other.sk))
            await host._app(s, sign(ph, "mem_rm", {**tgt, "iid": items[1]["iid"]}, {"t": "mem_rm", "r": "x3", **tgt}))
            self.assertEqual([(o["ok"], o["why"]) for o in self.got("ctl_res")], [(False, "shape"), (False, "bad_signature"),
                                                                                  (False, "bad_signature")])
            self.assertEqual((self.work / "CLAUDE.md").read_text(), MD)
            # signed → deleted, undo id back; the log lines hold no text
            await host._app(s, sign(ph, "mem_rm", tgt, {"t": "mem_rm", "r": "ok1", **tgt}))
            res = self.got("ctl_res", "ok1")[0]
            self.assertTrue(res["ok"], res)
            self.assertNotIn("部署在周五", (self.work / "CLAUDE.md").read_text())
            # the same signed command again (replay) → refused
            # a second delete with the old file hash → changed
            await host._app(s, sign(ph, "mem_rm", tgt, {"t": "mem_rm", "r": "ch", **tgt}))
            self.assertEqual(self.got("ctl_res", "ch")[0]["why"], "changed")
            await host._app(s, sign(ph, "mem_undo", {"id": res["undo"]}, {"t": "mem_undo", "r": "u1", "id": res["undo"]}))
            self.assertTrue(self.got("ctl_res", "u1")[0]["ok"])
            self.assertEqual((self.work / "CLAUDE.md").read_text(), MD)
        asyncio.run(go())
        for f in (self.st.log_path, self.st.controls_path):
            self.assertNotIn("部署在周五", f.read_text())
        cl = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertEqual([c["result"] for c in cl], ["refused:shape", "refused:bad_signature", "refused:bad_signature", "ok",
                                                     "changed", "ok"])
        self.assertTrue(all(len(c.get("object_sha256", "x" * 64)) == 64 for c in cl))
        kinds = [r["k"] for r in activity.all_since(self.st)]
        self.assertIn("mem_rm", kinds)
        self.assertIn("mem_undo", kinds)
        self.assertIn("control_refused", kinds)

    def test_activity_and_task_list_pages_fit_the_wire(self):
        for i in range(60):
            activity.record(self.st, "ask", id=str(i), tool="Bash", summary="长" * 290)
        task_dir(self.work, "daily-report", "SAY: VERDICT: ok — 好")
        ph, host = Phone(self.st), _host(self.st, self.sent)
        s = _ready(host, ph)
        real = []

        async def send_app(sess, obj):
            wire.pad_json(obj)                     # raises if a message would not fit (MAX_JSON)
            real.append(obj)
            self.sent.append((sess.cid, obj))
            return True
        host.send_app = send_app

        async def go():
            await host._app(s, {"t": "act_list", "r": "a1"})
            pages = self.got("act_page", "a1")
            self.assertGreater(len(pages), 1, "split into several messages")
            self.assertEqual(sum(len(p["items"]) for p in pages), 50)
            self.assertEqual(pages[-1]["more"], False)
            await host._app(s, {"t": "act_list", "r": "a2", "before": pages[-1]["next"]})
            self.assertEqual(sum(len(p["items"]) for p in self.got("act_page", "a2")), 10)
            await host._app(s, {"t": "task_list", "r": "t1"})
            rows = self.got("tasks", "t1")[0]["items"]
            self.assertEqual((rows[0]["id"], rows[0]["enabled"]), ("daily-report", False))
            tgt = {"id": "daily-report", "tsha": rows[0]["tsha"]}
            await host._app(s, sign(ph, "task_on", {**tgt, "tsha": "0" * 64}, {"t": "task_set", "r": "e0", "on": True, "id": tgt["id"], "tsha": "0" * 64}))
            self.assertEqual(self.got("ctl_res", "e0")[0]["why"], "changed")
            await host._app(s, sign(ph, "task_on", tgt, {"t": "task_set", "r": "e1", "on": True, **tgt}))
            self.assertTrue(self.got("ctl_res", "e1")[0]["ok"])
            self.assertTrue(tasks.rows(self.st, str(self.work))[0]["enabled"])
            await host._app(s, sign(ph, "task_off", tgt, {"t": "task_set", "r": "e2", "on": False, **tgt}))
            self.assertFalse(tasks.rows(self.st, str(self.work))[0]["enabled"])
        asyncio.run(go())

    def test_stop_everything_and_resume(self):
        ph, host = Phone(self.st), _host(self.st, self.sent)
        s = _ready(host, ph)
        submitted = []

        class FakeAgent:
            kind, status, halting = "claude", "working", False

            def submit(self, text):
                submitted.append(text)

            async def halt(self, clear_queue=True):
                self.halted = clear_queue
                return True

            async def stop(self):
                pass
        host.agent = FakeAgent()
        host.ask_ttl = 30

        async def go():
            t = asyncio.create_task(host.ask("Bash", {"command": "touch x"}))
            while not self.got("ask"):
                await asyncio.sleep(0.01)
            host.grants["g" * 32] = serve.Grant("g" * 32, "Bash", ("bash", ("ls",)), "Bash：ls", ph.did, 5, time.monotonic() + 60)
            await host._app(s, sign(ph, "estop", {}, {"t": "estop", "r": "s1"}))
            ans = await asyncio.wait_for(t, 5)
            self.assertEqual(ans["behavior"], "deny")
            self.assertTrue(self.got("ctl_res", "s1")[0]["ok"])
            self.assertTrue(host.agent.halted, "the turn is interrupted and the queue dropped")
            self.assertEqual(host.grants, {})
            self.assertEqual(host.eff_status(), "stopped")
            # a new message: refused with a notice, never queued
            await host._app(s, {"t": "msg", "id": "a" * 16, "text": "删掉所有东西", "ts": 0})
            self.assertEqual(submitted, [])
            self.assertTrue(any("已急停" in o.get("text", "") for o in self.got("msg")))
            # a request that arrives while stopped: denied at once, the phone is not asked
            n = len(self.got("ask"))
            self.assertEqual((await host.ask("Bash", {"command": "ls"}))["behavior"], "deny")
            self.assertEqual(len(self.got("ask")), n)
            await asyncio.sleep(0.05)
        asyncio.run(go())
        al = approvals.read_log(self.st)
        self.assertEqual([r["reason"] for r in al], ["estop", "estop"])
        self.assertIn('"ev": "estop"', self.st.log_path.read_text())
        # serve restarts: still stopped; resume needs a signature
        sent2 = []
        host2 = _host(self.st, sent2)
        self.assertTrue(host2.stopped())
        s2 = _ready(host2, ph, cid=12)

        async def go2():
            await host2.on_ready(s2, None)
            self.assertEqual([o for _, o in sent2 if o["t"] == "estop_state"][0]["on"], True)
            await host2._app(s2, {"t": "resume", "r": "r0"})
            self.assertTrue(host2.stopped(), "unsigned resume does nothing")
            await host2._app(s2, sign(ph, "resume", {}, {"t": "resume", "r": "r1"}))
            self.assertFalse(host2.stopped())
            await asyncio.sleep(0.05)
        asyncio.run(go2())
        self.assertFalse(controls.estop_state(self.st)["on"])
        kinds = [r["k"] for r in activity.all_since(self.st)]
        for k in ("ask", "estop", "message_refused", "resume"):
            self.assertIn(k, kinds)


# ------------------------------------------------------------------ the scheduler + stop inside a real serve (stand-in Claude Code, fenced)
class SchedulerChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.argv_log = self.work / ".argv.jsonl"
        wrapper = pathlib.Path(self.tmp.name) / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeclaude.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = mock.patch.dict(os.environ, {"AGENTJ_CLAUDE_BIN": str(wrapper), "FAKE_CLAUDE_LOG": str(self.argv_log)})
        self.env.start()
        self.st.set_agent_config("claude", str(self.work))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_tasks_run_fenced_with_phone_approvals_readonly_and_stop(self):
        from agentj import fence
        if fence.problem(self.st, str(self.work)):
            self.skipTest("no fence on this machine")
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = 20
        now = dt.datetime.now()
        if now.second > 40:                            # this minute only: never a second run when the minute turns
            time.sleep(61 - now.second)
            now = dt.datetime.now()
        task_dir(self.work, "research-one", "RUN: touch research-wrote.txt\nSAY: 读完了\nSAY: VERDICT: attention — 两个异常",
                 schedule=f"{now.minute} {now.hour} * * *")
        task_dir(self.work, "normal-one", "RUN: touch normal-wrote.txt\nSAY: VERDICT: ok — 一切正常", mode="normal")
        task_dir(self.work, "slow-one", "SLEEP: 30\nRUN: touch slow-wrote.txt\nSAY: VERDICT: ok — 不该到这里", mode="normal")
        tasks.set_enabled(self.st, str(self.work), "research-one", True, "terminal")

        def notices():
            return [o["text"] for _, o in sent if o["t"] == "msg" and o.get("from") == "notice"]

        async def wait(pred, ms=30000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; notices={notices()[-4:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.05)

        async def go():
            with mock.patch.object(tasks, "TICK", 0.3):
                run = asyncio.create_task(host.run())
                await wait(lambda: host.agent is not None)
                s = _ready(host, ph)
                # 1. cron (this minute) → runs by itself; research = read-only: the write is denied by the hook, no card
                await wait(lambda: any("任务research-one" in n for n in notices()))
                n = [x for x in notices() if "任务research-one" in x][0]
                self.assertEqual(n, "定时任务「任务research-one」：attention — 两个异常（只读运行）")
                self.assertFalse((self.work / "research-wrote.txt").exists(), "read-only run: the write never ran")
                self.assertEqual([o for _, o in sent if o["t"] == "ask"], [], "denied by the hook, not asked")
                reports = list((self.work / "workflows/research-one/reports").glob("run-*.md"))
                self.assertEqual(len(reports), 1)
                self.assertIn("只读运行", reports[0].read_text())
                self.assertIn("被 hook 拒绝", reports[0].read_text())
                # once per minute only
                await asyncio.sleep(1.2)
                self.assertEqual(sum("任务research-one" in x for x in notices()), 1)
                # 2. a normal task run by hand: the write asks the phone, with the task's name on the card
                host.scheduler.request("normal-one")
                await wait(lambda: any(o["t"] == "ask" for _, o in sent))
                ask = [o for _, o in sent if o["t"] == "ask"][-1]
                self.assertEqual((ask["summary"], ask["task"]), ("touch normal-wrote.txt", "任务normal-one"))
                await host._app(s, ph.answer(ask, True))
                await wait(lambda: any("任务normal-one" in x for x in notices()))
                self.assertTrue((self.work / "normal-wrote.txt").exists())
                self.assertIn("ok — 一切正常", [x for x in notices() if "normal-one" in x][0])
                # 3. a chat turn still works afterwards (the chat process stepped aside and comes back)
                await host._app(s, {"t": "msg", "id": "a" * 16, "text": "还在吗", "ts": 0})
                await wait(lambda: any(o.get("text") == "ECHO: 还在吗" for _, o in sent if o["t"] == "msg"))
                # 4. stop everything while a task runs → ended at once, nothing after the sleep runs
                host.scheduler.request("slow-one")
                await wait(lambda: host.scheduler.current_id == "slow-one")
                await asyncio.sleep(1.5)
                t0 = time.monotonic()
                await host._app(s, sign(ph, "estop", {}, {"t": "estop", "r": "s1"}))
                await wait(lambda: any("被急停中断" in x for x in notices()), 10000)
                self.assertLess(time.monotonic() - t0, 10)
                # 5. a chat turn in the middle of a long step is ended too
                await host._app(s, sign(ph, "resume", {}, {"t": "resume", "r": "r1"}))
                await host._app(s, {"t": "msg", "id": "b" * 16, "text": "RUNSEQ: @sleep 30 ;; touch late.txt", "ts": 0})
                await wait(lambda: host.agent.status == "working")
                await asyncio.sleep(1.5)
                t1 = time.monotonic()
                await host._app(s, sign(ph, "estop", {}, {"t": "estop", "r": "s2"}))
                await wait(lambda: host.agent.status != "working", 10000)
                self.assertLess(time.monotonic() - t1, 9)
                host.stopping.set()
                await run
        asyncio.run(go())
        time.sleep(1)
        self.assertFalse((self.work / "slow-wrote.txt").exists(), "the stopped task never got past its sleep")
        self.assertFalse((self.work / "late.txt").exists(), "the stopped chat turn never got past its sleep")
        last = tasks.load(self.st)["last"]
        self.assertEqual((last["research-one"]["verdict"], last["normal-one"]["verdict"]), ("attention", "ok"))
        kinds = [r["k"] for r in activity.all_since(self.st)]
        for k in ("task_run", "task_done", "ask", "decision", "estop", "resume", "turn_start", "turn_end"):
            self.assertIn(k, kinds)
        starts = [s for s in (json.loads(x) for x in self.argv_log.read_text().splitlines()) if "argv" in s]   # control lines: §10.10 meters
        research = [a for a in starts if any("research" in x for x in a["argv"] if "danger hook" in x)]
        self.assertEqual(len(research), 1, "the research run's hook carries the read-only flag")
        self.assertTrue(all("--resume" not in a["argv"] for a in starts if any("danger hook" in x and "research" in x for x in a["argv"])))


if __name__ == "__main__":
    unittest.main()
