"""Workflow design wizard (L3.5, ARCHITECTURE ADR-A59–A62): `agentj wizard install | apply | diff | resolve | doctor |
templates | add-template | dry-run` and the task contract (`taskspec`). Install never overwrites; the manifest decides what
counts as "the customer changed it"; doctor checks each item and exits 0 only when nothing is ✗; template download is signed,
refused when unbound / unpaid, and every SHA-256 is checked before anything is written (fake Dashboard over loopback HTTP);
dry-run reads the VERDICT line of a stand-in harness, fenced when bubblewrap works here. Templates here are synthetic: the
real ones are closed and never in this package."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import base64
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
from agentj import cloud, fence, taskspec, wire, wizard  # noqa: E402
from agentj.state import State  # noqa: E402

BOOT = wizard.BOOT_SET
FAKE_KEY = "sk-ant-" + "api03-" + "Q" * 12 + "w7" * 12   # assembled at runtime so repository scanners see no key


def fm(kind: str, stale: str = "2099-01-01") -> str:
    return f"---\ntype: {kind}\nstatus: draft\ngenerated: {{ by: agentj-workflow-wizard/claude-code, at: 2026-10-02T10:00:00Z }}\nverified: []\nstale_after: {stale}\n---\n"


def good_files(entry=("CLAUDE.md",), workflows=()) -> dict[str, bytes]:
    body = ("# 森野户外 — 小森\n\n新对话先读：documentation/CONSTITUTION.md、documentation/IDENTITY.md、documentation/SOUL.md、"
            "documentation/WORKFLOW.md、documentation/ROLES.md、documentation/NEXT_SESSION.md、documentation/MEMORY.md。\n")
    files = {e: body.encode() for e in entry}
    for d in BOOT:
        files[f"documentation/{d}"] = (fm(d[:-3].title()) + f"# {d}\n\n内容：待定\n").encode()
    files["documentation/STRUCTURE.json"] = json.dumps({
        "project": "森野户外", "agent_name": "小森", "language": "zh", "generated_by": "agentj-workflow-wizard", "version": "0.1.0",
        "entry": list(entry), "boot_set": [f"documentation/{d}" for d in BOOT], "documents": [],
        "workflows": [{"id": w, "dir": f"workflows/{w}", "status": "dormant", "level": "report"} for w in workflows]},
        ensure_ascii=False).encode()
    return files


def task(tid="daily-report", **over) -> dict:
    t = {"v": 1, "id": tid, "title": {"zh": "日报", "en": "Daily report"}, "schedule": "0 8 * * *", "tz": "local",
         "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md", "mode": "research", "enabled": False,
         "needs": ["orders: Amazon SP-API or an ERP export"], "outputs": ["reports/YYYY-MM-DD.md"]}
    t.update(over)
    return t


def stage(root: pathlib.Path, files: dict[str, bytes]) -> pathlib.Path:
    s = root / wizard.STAGING_REL
    for rel, data in files.items():
        (s / rel).parent.mkdir(parents=True, exist_ok=True)
        (s / rel).write_bytes(data)
    return s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aj-wiz-")
        self.root = pathlib.Path(self.tmp) / "work"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def manifest(self):
        return json.loads((self.root / wizard.MANIFEST_REL).read_text())["files"]


class MainAgentRoot(Base):
    def test_bootstrap_seeds_root_and_preserves_existing_files(self):
        mine = "# personal entry\n"
        (self.root / "CLAUDE.md").write_text(mine)
        rows = wizard.bootstrap_root(self.root, lang="zh")
        self.assertEqual((self.root / "CLAUDE.md").read_text(), mine)
        self.assertNotIn("CLAUDE.md", {r["path"] for r in rows})
        agent_entry = (self.root / "AGENTS.md").read_text()
        self.assertIn(wizard.CORE_REF, agent_entry)
        self.assertIn("agentj/identity/core.zh.md", agent_entry)
        self.assertIn("董事长助理", agent_entry)
        workflow = (self.root / "documentation/WORKFLOW.md").read_text()
        self.assertIn("退出码不能证明成功", workflow)
        self.assertIn("所属 CEO 修复并重跑", workflow)
        self.assertIn("晨报", workflow)
        snapshot = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(wizard.bootstrap_root(self.root), [])
        self.assertEqual(snapshot, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_apply_places_ceo_entries_only_in_one_level_workflow_folders(self):
        wizard.apply(self.root, stage(self.root, {"daily-report/AGENTS.md": b"CEO of daily-report only\n"}))
        self.assertTrue((self.root / "daily-report/AGENTS.md").is_file())
        for bad in ("Daily-report/AGENTS.md", "nested/daily-report/AGENTS.md", "daily_report/AGENTS.md"):
            with self.assertRaises(wizard.WizardError):
                wizard.apply(self.root, stage(self.root, {bad: b"CEO\n"}))
            shutil.rmtree(self.root / wizard.STAGING_REL)

    def test_new_structure_requires_core_and_ceo_routing(self):
        wizard.bootstrap_root(self.root)
        self.assertFalse([c for c in wizard.doctor(self.root) if c["status"] == "fail"])
        f = self.root / "documentation/STRUCTURE.json"
        doc = json.loads(f.read_text())
        doc["workflows"] = [{"id": "daily-report", "dir": "daily-report", "status": "planned"}]
        f.write_text(json.dumps(doc))
        self.assertEqual(next(c["status"] for c in wizard.doctor(self.root) if c["name"] == "structure"), "fail")
        (self.root / "daily-report").mkdir()
        (self.root / "daily-report/AGENTS.md").write_text("CEO of daily-report only\n")
        doc["workflows"][0]["ceo_entry"] = "daily-report/AGENTS.md"
        f.write_text(json.dumps(doc))
        self.assertEqual(next(c["status"] for c in wizard.doctor(self.root) if c["name"] == "structure"), "ok")
        doc["workflows"][0]["dir"] = "somewhere/deep/daily-report"
        f.write_text(json.dumps(doc))
        self.assertEqual(next(c["status"] for c in wizard.doctor(self.root) if c["name"] == "structure"), "fail")

    def test_existing_legacy_workflow_stays_in_place(self):
        legacy = self.root / "workflows/daily-report"
        legacy.mkdir(parents=True)
        (legacy / "task.json").write_text(json.dumps(task()))
        self.assertEqual(wizard.workflow_dir(self.root, "daily-report"), legacy)
        self.assertEqual(wizard._workflow_dirs(self.root), [legacy])
        self.assertFalse((self.root / "daily-report").exists())
        self.assertEqual(wizard.workflow_dir(self.root, "ads-review"), self.root / "ads-review")


# ================================================================== task contract
class TaskSpec(unittest.TestCase):
    def test_valid_and_each_violation(self):
        self.assertEqual(taskspec.problems(task(), "daily-report"), [])
        self.assertEqual(taskspec.problems(task(tz="Asia/Shanghai", schedule="*/30 9-21 * * 1-5", enabled=True)), [])
        bad = [task(v=2), task(id="Daily"), task(title={"zh": "x"}), task(schedule="0 8 * *"), task(schedule="0 25 * * *"),
               task(schedule="@daily"), task(schedule="0 8 * * MON"), task(tz="Mars/Base"), task(prompt_file="../RUN.md"),
               task(mode="auto"), task(enabled="false"), task(needs=["x" * 121]), task(outputs=["/etc/passwd"]),
               task(outputs=["reports/../x"]), task(outputs=[]), task(needs=[f"OPENAI_API_KEY={FAKE_KEY}"]),
               {**task(), "extra": 1}, {k: v for k, v in task().items() if k != "enabled"}, [], "x"]
        for b in bad:
            self.assertTrue(taskspec.problems(b), repr(b)[:80])
        self.assertEqual(taskspec.problems(task(), "listing"), ["id does not match its folder"])

    def test_secret_kinds_cover_every_privacy_secret_rule(self):
        from agentj import privacy
        self.assertTrue({k for k, _, _ in privacy._SECRETS} | {"high_entropy"} <= taskspec.SECRET_KINDS)


# ================================================================== install / place
class Install(Base):
    def test_install_all_three_then_rerun_is_unchanged(self):
        rows = wizard.install(self.root, list(wizard.HARNESSES))
        n = len(wizard.skill_files())
        self.assertEqual(len(rows), 3 * n + 2, "three skill copies + the two entry files (docs rule)")
        self.assertTrue(all(r["status"] == "created" for r in rows))
        for d in (".claude/skills", ".agents/skills", ".opencode/skills"):
            sk = self.root / d / wizard.SKILL_NAME / "SKILL.md"
            self.assertTrue(sk.is_file(), d)
            self.assertTrue(sk.read_text().startswith("---\nname: agentj-workflow-wizard\n"))
        self.assertTrue(all(r["status"] == "unchanged" for r in wizard.install(self.root, list(wizard.HARNESSES))))
        self.assertEqual(json.loads((self.root / wizard.MANIFEST_REL).read_text())["harness"], sorted(wizard.HARNESSES))

    # ---- PROMPT-29 C-5: the "look it up first" block in the entry file
    def test_docs_rule_created_when_no_entry_file(self):
        from agentj import docsrule
        rows = {r["path"]: r["status"] for r in wizard.install(self.root, ["claude"])}
        self.assertEqual(rows["CLAUDE.md"], "created")
        self.assertNotIn("AGENTS.md", rows, "only the entry file of the chosen harness")
        self.assertIn(docsrule.block(), (self.root / "CLAUDE.md").read_text())
        self.assertIn(wizard.CORE_REF, (self.root / "CLAUDE.md").read_text())
        self.assertIn("CLAUDE.md", self.manifest(), "a file we created is ours: a later apply may replace it")
        rows = {r["path"]: r["status"] for r in wizard.install(self.root, ["codex", "opencode"])}
        self.assertEqual(rows["AGENTS.md"], "created")
        self.assertEqual(rows.get("CLAUDE.md"), None)

    def test_docs_rule_appended_once_to_the_humans_file_and_nothing_else_changes(self):
        from agentj import docsrule
        mine = "# 我自己的规矩\n\n别动我的文件。"          # no trailing newline on purpose
        (self.root / "AGENTS.md").write_text(mine)
        rows = {r["path"]: r["status"] for r in wizard.install(self.root, ["codex"], "en")}
        self.assertEqual(rows["AGENTS.md"], "added")
        text = (self.root / "AGENTS.md").read_text()
        self.assertTrue(text.startswith(mine + "\n\n"), "the human's text is kept byte for byte, block after one blank line")
        self.assertIn(docsrule.block("en"), text)
        self.assertEqual(text.count(docsrule.BEGIN), 1)
        self.assertNotIn("AGENTS.md", self.manifest(), "the human's file stays theirs")
        for _ in range(2):                                  # idempotent, also with another language asked for
            rows = {r["path"]: r["status"] for r in wizard.install(self.root, ["codex"], "zh")}
            self.assertEqual(rows["AGENTS.md"], "unchanged")
            self.assertEqual((self.root / "AGENTS.md").read_text(), text)

    def test_docs_rule_the_human_edited_is_left_alone(self):
        from agentj import docsrule
        edited = "# mine\n\n" + docsrule.BEGIN + "\n先问我，再查文档。\n" + docsrule.END + "\n"
        (self.root / "CLAUDE.md").write_text(edited)
        wizard.install(self.root, ["claude"])
        self.assertTrue((self.root / "CLAUDE.md").read_text().startswith(edited.rstrip("\n")))

    def test_docs_rule_never_follows_a_symlinked_entry_file(self):
        outside = pathlib.Path(self.tmp) / "elsewhere.md"
        outside.write_text("x\n")
        (self.root / "CLAUDE.md").symlink_to(outside)
        rows = {r["path"]: r["status"] for r in wizard.install(self.root, ["claude"])}
        self.assertEqual(rows["CLAUDE.md"], "skipped")
        self.assertEqual(outside.read_text(), "x\n")

    def test_docs_rule_survives_apply_and_keeps_the_block_on_disk(self):
        from agentj import docsrule
        wizard.install(self.root, ["claude"], "zh")
        st = self.root / wizard.STAGING_REL
        for rel, data in good_files().items():
            (st / rel).parent.mkdir(parents=True, exist_ok=True)
            (st / rel).write_bytes(data)
        rows = {r["path"]: r["status"] for r in wizard.apply(self.root, st)}
        self.assertEqual(rows["CLAUDE.md"], "updated", "our stub is replaced by the generated entry file")
        text = (self.root / "CLAUDE.md").read_text()
        self.assertTrue(text.startswith(good_files()["CLAUDE.md"].decode()))
        self.assertTrue(text.endswith(docsrule.block("zh")), "the same block (language) as before")
        self.assertEqual(text.count(docsrule.BEGIN), 1)
        self.assertEqual(wizard.doctor(self.root)[0]["status"], "ok")
        # a re-run where the human kept their own entry file: the proposal carries the block too
        (self.root / "CLAUDE.md").write_text("# 我改过\n" + docsrule.block("zh"))
        for rel, data in good_files().items():
            (st / rel).parent.mkdir(parents=True, exist_ok=True)
            (st / rel).write_bytes(data)
        rows = {r["path"]: r["status"] for r in wizard.apply(self.root, st)}
        self.assertEqual(rows["CLAUDE.md"], "kept")
        self.assertIn(docsrule.BEGIN, (self.root / ("CLAUDE.md" + wizard.NEW)).read_text())

    def test_docs_rule_command_prints_exactly_the_block(self):
        from agentj import docsrule
        env = {**os.environ, "HOME": self.tmp, "AGENTJ_STATE_DIR": str(pathlib.Path(self.tmp) / "nostate")}
        for args, lang in (([], None), (["--lang", "zh"], "zh"), (["--lang", "en"], "en")):
            r = subprocess.run([sys.executable, "-m", "agentj.cli", "docs-rule", *args], cwd=HOST, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual((r.returncode, r.stderr), (0, ""))
            self.assertEqual(r.stdout, docsrule.block(lang))
        self.assertFalse((pathlib.Path(self.tmp) / "nostate").exists(), "no side effects")
        for lang in ("zh", "en"):
            b = docsrule.block(lang)
            self.assertIn("https://agentj.app/llms.txt", b)
            self.assertIn(f"https://agentj.app/docs/<slug>/{lang}.md", b)
            self.assertIn("agentj plaza search", b)
            self.assertIn("agentj feedback", b)
            self.assertNotIn("公司", b)
            self.assertNotRegex(b, r"(?i)company")

    def test_customer_edit_is_never_overwritten(self):
        wizard.install(self.root, ["claude"])
        sk = self.root / ".claude/skills" / wizard.SKILL_NAME / "SKILL.md"
        sk.write_text("my own notes\n")
        rows = {r["path"]: r for r in wizard.install(self.root, ["claude"])}
        rel = f".claude/skills/{wizard.SKILL_NAME}/SKILL.md"
        self.assertEqual(rows[rel]["status"], "kept")
        self.assertEqual(sk.read_text(), "my own notes\n")
        self.assertEqual((self.root / (rel + wizard.NEW)).read_bytes(), wizard.skill_files()["SKILL.md"])

    def test_a_file_that_was_there_before_is_kept(self):
        (self.root / ".agents/skills" / wizard.SKILL_NAME).mkdir(parents=True)
        (self.root / ".agents/skills" / wizard.SKILL_NAME / "SKILL.md").write_text("pre-existing")
        rows = {r["path"]: r["status"] for r in wizard.install(self.root, ["codex"])}
        self.assertEqual(rows[f".agents/skills/{wizard.SKILL_NAME}/SKILL.md"], "kept")

    def test_our_untouched_file_is_updated(self):
        wizard.place(self.root, {"documentation/MEMORY.md": b"v1"}, "wizard")
        self.assertEqual(wizard.place(self.root, {"documentation/MEMORY.md": b"v2"}, "wizard")[0]["status"], "updated")
        self.assertEqual((self.root / "documentation/MEMORY.md").read_bytes(), b"v2")
        self.assertEqual(self.manifest()["documentation/MEMORY.md"]["sha256"], hashlib.sha256(b"v2").hexdigest())

    def test_symlinks_and_escapes_refused(self):
        outside = pathlib.Path(self.tmp) / "outside"
        outside.mkdir()
        (self.root / "documentation").symlink_to(outside)
        with self.assertRaises(wizard.WizardError):
            wizard.place(self.root, {"documentation/X.md": b"x"}, "wizard")
        self.assertEqual(list(outside.iterdir()), [])
        for rel in ("../x.md", "/etc/x", "a/../../x", ".agentjarvis/wizard-manifest.json", ".agentj/wizard-manifest.json", "CLAUDE.md.wizard-new", ""):
            with self.assertRaises(wizard.WizardError, msg=rel):
                wizard.place(self.root, {rel: b"x"}, "wizard")
        # a symlinked target file is replaced by a proposal, never followed
        (self.root / "documentation").unlink()
        (self.root / "documentation").mkdir()
        victim = outside / "victim.md"
        victim.write_text("keep")
        (self.root / "documentation/MEMORY.md").symlink_to(victim)
        self.assertEqual(wizard.place(self.root, {"documentation/MEMORY.md": b"new"}, "wizard")[0]["status"], "kept")
        self.assertEqual(victim.read_text(), "keep")

    def test_cli_install_defaults_and_hint(self):
        env = {**os.environ, "AGENTJ_STATE_DIR": self.tmp + "/state", "PYTHONPATH": str(HOST)}
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "wizard", "install", "--dir", str(self.root)],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("帮我设计工作流", r.stdout)
        self.assertTrue((self.root / ".opencode/skills" / wizard.SKILL_NAME / "references/questions.md").is_file())
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "wizard", "install"], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--dir", r.stderr)


class Apply(Base):
    def test_apply_create_update_keep_resolve(self):
        rows = wizard.apply(self.root, stage(self.root, good_files()))
        self.assertTrue(all(r["status"] == "created" for r in rows))
        self.assertFalse((self.root / wizard.STAGING_REL).exists(), "staging cleared after a successful apply")
        # the customer edits two files; a re-run changes all three
        (self.root / "CLAUDE.md").write_text("mine\n")
        (self.root / "documentation/SOUL.md").write_text("my soul\n")
        new = good_files()
        for k in ("CLAUDE.md", "documentation/SOUL.md", "documentation/MEMORY.md"):
            new[k] = new[k] + b"\nv2\n"
        st = {r["path"]: r["status"] for r in wizard.apply(self.root, stage(self.root, new))}
        self.assertEqual((st["CLAUDE.md"], st["documentation/SOUL.md"], st["documentation/MEMORY.md"]), ("kept", "kept", "updated"))
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "mine\n")
        self.assertEqual(wizard.pending(self.root), ["CLAUDE.md", "documentation/SOUL.md"])
        self.assertIn("+v2", wizard.diff_text(self.root, "CLAUDE.md"))
        # keep mine → proposal gone, and the NEXT run still protects the customer's file
        self.assertEqual(wizard.resolve(self.root, "CLAUDE.md", "mine"), "kept your version")
        self.assertFalse((self.root / "CLAUDE.md.wizard-new").exists())
        new["CLAUDE.md"] += b"v3\n"
        self.assertEqual({r["path"]: r["status"] for r in wizard.apply(self.root, stage(self.root, new))}["CLAUDE.md"], "kept")
        # keep new → the file becomes ours again and later runs may update it
        wizard.resolve(self.root, "documentation/SOUL.md", "new")
        self.assertEqual((self.root / "documentation/SOUL.md").read_bytes(), new["documentation/SOUL.md"])
        new["documentation/SOUL.md"] += b"v4\n"
        self.assertEqual({r["path"]: r["status"] for r in wizard.apply(self.root, stage(self.root, new))}["documentation/SOUL.md"], "updated")
        with self.assertRaises(wizard.WizardError):
            wizard.resolve(self.root, "documentation/IDENTITY.md", "new")

    def test_restaging_the_customers_own_text_does_not_make_it_ours(self):
        wizard.apply(self.root, stage(self.root, good_files()))
        (self.root / "documentation/ROLES.md").write_text("customer's roles\n")
        again = {**good_files(), "documentation/ROLES.md": b"customer's roles\n"}     # the Agent copied it as it is
        st = {r["path"]: r["status"] for r in wizard.apply(self.root, stage(self.root, again))}
        self.assertEqual(st["documentation/ROLES.md"], "unchanged")
        later = {**good_files(), "documentation/ROLES.md": b"wizard v2\n"}
        st = {r["path"]: r["status"] for r in wizard.apply(self.root, stage(self.root, later))}
        self.assertEqual(st["documentation/ROLES.md"], "kept", "still the customer's file: never silently replaced")
        self.assertEqual((self.root / "documentation/ROLES.md").read_text(), "customer's roles\n")

    def test_refusals_write_nothing(self):
        files = good_files()
        files["documentation/MEMORY.md"] += f"\nOpenAI key: {FAKE_KEY}\n".encode()
        with self.assertRaises(wizard.WizardError) as cm:
            wizard.apply(self.root, stage(self.root, files))
        self.assertIn("documentation/MEMORY.md", str(cm.exception))
        self.assertNotIn(FAKE_KEY, str(cm.exception))
        self.assertFalse((self.root / "documentation").exists())
        shutil.rmtree(self.root / wizard.STAGING_REL)
        for rel in ("scripts/run.sh", "workflows/x/task.json", ".claude/settings.json", "notes.txt"):
            s = stage(self.root, {**good_files(), rel: b"x"})
            with self.assertRaises(wizard.WizardError, msg=rel):
                wizard.apply(self.root, s)
            shutil.rmtree(s)
        s = stage(self.root, good_files())
        (s / "documentation/LINK.md").symlink_to("/etc/hostname")
        with self.assertRaises(wizard.WizardError):
            wizard.apply(self.root, s)
        shutil.rmtree(s)
        s = stage(self.root, {"documentation/BIG.md": b"x" * (wizard.MAX_FILE + 1)})
        with self.assertRaises(wizard.WizardError):
            wizard.apply(self.root, s)
        self.assertFalse((self.root / "documentation").exists())


# ================================================================== doctor
class Doctor(Base):
    def setUp(self):
        super().setUp()
        wizard.apply(self.root, stage(self.root, good_files()))

    def status(self, name):
        return {c["name"]: c["status"] for c in wizard.doctor(self.root, today="2026-10-02")}[name]

    def test_green(self):
        checks = wizard.doctor(self.root, today="2026-10-02")
        self.assertFalse([c for c in checks if c["status"] == "fail"], checks)

    def test_each_check_fails_on_its_own(self):
        cases = [
            ("entry", lambda: (self.root / "CLAUDE.md").unlink()),
            ("entry", lambda: (self.root / "CLAUDE.md").write_text("# no routing here\n")),
            ("boot_set", lambda: (self.root / "documentation/SOUL.md").unlink()),
            ("frontmatter", lambda: (self.root / "documentation/ROLES.md").write_text("# ROLES\nno frontmatter\n")),
            ("structure", lambda: (self.root / "documentation/EXTRA.md").write_text(fm("Playbook") + "x\n")),
            ("structure", lambda: (self.root / "documentation/STRUCTURE.json").write_text("{not json")),
            ("placeholders", lambda: (self.root / "documentation/IDENTITY.md").write_text(fm("Identity") + "名字：{{Agent name}}\n")),
            ("secrets", lambda: (self.root / "documentation/MEMORY.md").write_text(fm("Memory") + f"token = {FAKE_KEY}\n")),
        ]
        for name, breaker in cases:
            snap = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
            breaker()
            self.assertEqual(self.status(name), "fail", name)
            for p in list(self.root.rglob("*")):
                if p.is_file() and p not in snap:
                    p.unlink()
            for p, b in snap.items():
                p.write_bytes(b)
            self.assertEqual(self.status(name), "ok", f"{name} restored")

    def test_structure_must_list_installed_workflows_and_tasks_must_follow_the_contract(self):
        wf = self.root / "daily-report"
        wf.mkdir(parents=True)
        (wf / "task.json").write_text(json.dumps(task()))
        (wf / "RUN.md").write_text("run")
        (wf / "DRYRUN.md").write_text("dry")
        self.assertEqual(self.status("structure"), "fail")          # installed but not listed
        s = json.loads((self.root / "documentation/STRUCTURE.json").read_text())
        s["workflows"] = [{"id": "daily-report", "status": "dormant"}, {"id": "listing", "status": "planned"}]
        (self.root / "documentation/STRUCTURE.json").write_text(json.dumps(s))
        self.assertEqual(self.status("structure"), "ok")
        self.assertEqual(self.status("tasks"), "ok")
        (wf / "task.json").write_text(json.dumps(task(schedule="every morning")))
        self.assertEqual(self.status("tasks"), "fail")
        (wf / "task.json").write_text(json.dumps(task()))
        (wf / "DRYRUN.md").unlink()
        self.assertEqual(self.status("tasks"), "fail")

    def test_pending_and_stale_are_warnings_and_cli_exit_codes(self):
        (self.root / "CLAUDE.md.wizard-new").write_text("proposal")
        p = self.root / "documentation/NEXT_SESSION.md"
        p.write_text(p.read_text().replace("2099-01-01", "2026-01-01"))
        st = {c["name"]: c["status"] for c in wizard.doctor(self.root, today="2026-10-02")}
        self.assertEqual((st["pending"], st["frontmatter"]), ("warn", "warn"))
        env = {**os.environ, "PYTHONPATH": str(HOST), "AGENTJ_STATE_DIR": self.tmp + "/state"}
        cmd = [sys.executable, "-m", "agentj.cli", "wizard", "doctor", "--dir", str(self.root), "--json"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["ok"])
        (self.root / "documentation/WORKFLOW.md").unlink()
        r = subprocess.run(cmd[:-1], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ boot_set", r.stdout)


# ================================================================== templates (fake Dashboard over loopback HTTP)
def make_package(tid="daily-report", version="1.0.0", **task_over) -> dict:
    contents = {"TEMPLATE.md": "# Daily report\n", "RUN.md": "run it\nVERDICT: ok|attention|fail — x\n",
                "DRYRUN.md": "dry run it\n", "task.json": json.dumps(task(tid, **task_over), ensure_ascii=False),
                "samples/orders.csv": "date,sku,units\n2026-10-01,KS-1,3\n"}
    files = [{"path": p, "sha256": hashlib.sha256(c.encode()).hexdigest(), "content": c} for p, c in contents.items()]
    d = wizard.package_digest([(f["path"], f["sha256"]) for f in files])
    return {"id": tid, "version": version, "title": {"zh": "日报", "en": "Daily report"}, "sha256": d, "files": files}


class FakeDashboard(http.server.BaseHTTPRequestHandler):
    """Checks the §7 envelope (context, derivation, signature, nonce once) like the Worker; answers from `server.cfg`."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        cfg = self.server.cfg
        env = json.loads(self.rfile.read(int(self.headers["content-length"])))
        tpl = self.path == "/v1/host/templates"
        ctx = wizard.CTX_TEMPLATES if tpl else wizard.CTX_TEMPLATE
        d = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        pk = d(env["pk"])
        try:
            Ed25519PublicKey.from_public_bytes(pk).verify(d(env["sig"]), f"{ctx}\n{env['body']}".encode())
        except Exception:
            return self.answer(401, {"error": "bad_signature"})
        inner = json.loads(d(env["body"]))
        if inner["channel"] != wire.channel_id(pk) or inner["nonce"] in cfg["nonces"]:
            return self.answer(409, {"error": "replay"})
        cfg["nonces"].add(inner["nonce"])
        cfg["seen"].append((self.path, inner))
        if cfg.get("status"):
            return self.answer(cfg["status"], {"error": cfg["error"]})
        if tpl:
            return self.answer(200, {"templates": [{k: p[k] for k in ("id", "version", "title", "sha256")} | {"files": len(p["files"])}
                                                   for p in cfg["listing"]]})
        tid = self.path.rsplit("/", 1)[1]
        if inner.get("id") != tid:
            return self.answer(400, {"error": "bad_request"})
        pkg = cfg["packages"].get(tid)
        return self.answer(200, pkg) if pkg else self.answer(404, {"error": "unknown_template"})

    def answer(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class Templates(Base):
    def setUp(self):
        super().setUp()
        self.st = State(pathlib.Path(self.tmp) / "state")
        self.st.init()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDashboard)
        pkg = make_package()
        self.srv.cfg = {"nonces": set(), "seen": [], "listing": [pkg], "packages": {"daily-report": pkg}}
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.api = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def bind(self):
        cloud.write_cloud(self.st, {"api": self.api, "host_id": "h1", "tenant": {"slug": "acme", "name": "Acme"},
                                    "linked_at": 1, "last_seq": 0, "via": "code"})

    def test_unbound_host_sends_nothing(self):
        with self.assertRaises(wizard.WizardError) as cm:
            wizard.list_templates(self.st)
        self.assertEqual(str(cm.exception), "not_bound")
        self.assertEqual(self.srv.cfg["seen"], [])

    def test_list_and_install_dormant_with_hashes_checked(self):
        self.bind()
        rows = wizard.list_templates(self.st)
        self.assertEqual([r["id"] for r in rows], ["daily-report"])
        want, placed = wizard.add_template(self.st, self.root, "daily-report")
        self.assertEqual(want["version"], "1.0.0")
        self.assertTrue(all(r["status"] == "created" for r in placed))
        t = json.loads((self.root / "daily-report/task.json").read_text())
        self.assertIs(t["enabled"], False)
        self.assertTrue((self.root / "daily-report/samples/orders.csv").is_file())
        self.assertEqual(self.manifest()["daily-report/RUN.md"]["source"], "template:daily-report@1.0.0")
        # every request: a fresh nonce, the host's channel, the right `t`
        ts = [(p, i["t"], i["channel"]) for p, i in self.srv.cfg["seen"]]
        self.assertEqual(ts[-1], ("/v1/host/templates/daily-report", "template", cloud.channel_of(self.st)))
        self.assertEqual(len(self.srv.cfg["nonces"]), len(self.srv.cfg["seen"]))
        # re-install over a customer edit keeps the edit
        (self.root / "daily-report/RUN.md").write_text("my run")
        st = {r["path"]: r["status"] for r in wizard.add_template(self.st, self.root, "daily-report")[1]}
        self.assertEqual(st["daily-report/RUN.md"], "kept")

    def test_download_registers_new_workflow_ceo_and_preserves_legacy_location(self):
        self.bind()
        wizard.bootstrap_root(self.root)
        wizard.add_template(self.st, self.root, "daily-report")
        for e in wizard.ENTRY_FILES:
            self.assertIn("workflow CEO", (self.root / "daily-report" / e).read_text())
        doc = json.loads((self.root / "documentation/STRUCTURE.json").read_text())
        self.assertEqual(doc["workflows"][0]["dir"], "daily-report")
        self.assertEqual(doc["workflows"][0]["ceo_entry"], "daily-report/AGENTS.md")
        self.assertIn("daily-report/AGENTS.md", (self.root / "documentation/ROLES.md").read_text())
        self.assertFalse([c for c in wizard.doctor(self.root) if c["status"] == "fail"])
        # An existing root never relocates legacy workflow files during installation.
        legacy_root = pathlib.Path(self.tmp) / "legacy-work"
        legacy = legacy_root / "workflows/daily-report"
        legacy.mkdir(parents=True)
        (legacy / "RUN.md").write_text("customer run\n")
        wizard.add_template(self.st, legacy_root, "daily-report")
        self.assertEqual((legacy / "RUN.md").read_text(), "customer run\n")
        self.assertTrue((legacy / "RUN.md.wizard-new").is_file())
        self.assertFalse((legacy_root / "daily-report").exists())

    def test_tampered_packages_are_refused_before_writing(self):
        self.bind()
        good = make_package()
        bad_file = json.loads(json.dumps(good))
        bad_file["files"][1]["content"] += "rm -rf ~\n"                      # content no longer matches its sha256
        bad_digest = make_package()
        bad_digest["files"][1]["content"] += "x"
        bad_digest["files"][1]["sha256"] = hashlib.sha256(bad_digest["files"][1]["content"].encode()).hexdigest()  # listing digest differs
        enabled = make_package(enabled=True)
        missing = make_package()
        missing["files"] = [f for f in missing["files"] if f["path"] != "DRYRUN.md"]
        missing["sha256"] = wizard.package_digest([(f["path"], f["sha256"]) for f in missing["files"]])
        escape = make_package()
        escape["files"].append({"path": "../escape.md", "sha256": hashlib.sha256(b"x").hexdigest(), "content": "x"})
        for name, pkg, listed in (("file hash", bad_file, good), ("package digest", bad_digest, good), ("enabled", enabled, enabled),
                                  ("missing DRYRUN", missing, missing), ("path", escape, escape)):
            self.srv.cfg["listing"], self.srv.cfg["packages"] = [listed], {"daily-report": pkg}
            with self.assertRaises(wizard.WizardError, msg=name) as cm:
                wizard.add_template(self.st, self.root, "daily-report")
            self.assertIn("bad_package", str(cm.exception), name)
            self.assertFalse((self.root / "workflows").exists(), name)
            self.assertFalse((self.root / "daily-report").exists(), name)

    def test_server_refusals_are_reported_by_code(self):
        self.bind()
        for status, error in ((402, "payment_required"), (403, "not_bound"), (429, "rate_limited")):
            self.srv.cfg.update(status=status, error=error)
            with self.assertRaises(wizard.WizardError) as cm:
                wizard.list_templates(self.st)
            self.assertEqual(str(cm.exception), error)
        self.srv.cfg.pop("status")
        with self.assertRaises(wizard.WizardError):
            wizard.add_template(self.st, self.root, "listing")            # not in the listing
        with self.assertRaises(wizard.WizardError):
            wizard.add_template(self.st, self.root, "../x")

    def test_cli_templates_and_add_template(self):
        self.bind()
        env = {**os.environ, "AGENTJ_STATE_DIR": str(self.st.root), "PYTHONPATH": str(HOST)}
        base = [sys.executable, "-m", "agentj.cli", "wizard"]
        r = subprocess.run(base + ["templates", "--json"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["templates"][0]["id"], "daily-report")
        r = subprocess.run(base + ["add-template", "daily-report", "--dir", str(self.root)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("休眠", r.stdout)
        self.srv.cfg.update(status=402, error="payment_required")
        r = subprocess.run(base + ["templates"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual((r.returncode, r.stderr.strip()), (1, "✗ payment_required"))


# ================================================================== dry run
FAKE_HARNESS = r'''#!/usr/bin/env python3
import os, sys
prompt = sys.stdin.read() if "-p" in sys.argv or "exec" in sys.argv else sys.argv[-1]
open(os.environ["WIZ_ARGV"], "w").write("\n".join(sys.argv[1:]) + "\n----\n" + prompt)
mode = os.environ.get("WIZ_MODE", "attention")
print("# 经营日报（演练）\n\n## 一句话\n库存偏低。\n")
if mode == "probe":                     # what the fenced process can see
    sd = os.environ.get("WIZ_STATE")
    print("STATE_VISIBLE" if sd and os.path.exists(os.path.join(sd, "host_ed25519.key")) else "STATE_HIDDEN")
if mode != "none":
    print(f"**VERDICT:** {'attention' if mode in ('attention', 'probe') else mode} — KS-MUG-01 只够卖 6 天")
'''


class DryRun(Base):
    def setUp(self):
        super().setUp()
        self.st = State(pathlib.Path(self.tmp) / "state")
        self.st.init()
        wf = self.root / "daily-report"
        (wf / "samples").mkdir(parents=True)
        (wf / "task.json").write_text(json.dumps(task()))
        (wf / "RUN.md").write_text("run")
        (wf / "DRYRUN.md").write_text("演练说明 DRYRUN-MARKER\n")
        (wf / "samples/orders.csv").write_text("date,sku\n2026-10-01,SAMPLE-MARKER\n")
        bindir = pathlib.Path(self.tmp) / "bin"
        bindir.mkdir()
        self.fake = bindir / "claude"
        self.fake.write_text(FAKE_HARNESS)
        self.fake.chmod(0o755)
        self.argv_file = self.root / "argv.txt"      # inside the work folder: writable inside the fence too
        self.env = {"AGENTJ_CLAUDE_BIN": str(self.fake), "WIZ_ARGV": str(self.argv_file), "WIZ_STATE": str(self.st.root)}

    def run_dry(self, mode="attention", **kw):
        old = {k: os.environ.get(k) for k in [*self.env, "WIZ_MODE"]}
        os.environ.update(self.env, WIZ_MODE=mode)
        try:
            return wizard.dry_run(self.st, self.root, "daily-report", **kw)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def unfenced(self):
        cfg = self.st.config()
        cfg["agent"] = {"kind": "claude", "dir": str(self.root), "model": None, "fence": False}
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())

    def test_verdict_read_prompt_has_samples_argv_narrowed_report_written(self):
        self.unfenced()
        r = self.run_dry()
        self.assertEqual((r["verdict"], r["harness"], r["fenced"]), ("attention", "claude", False))
        self.assertIn("KS-MUG-01", r["summary"])
        sent = self.argv_file.read_text()
        argv, prompt = sent.split("\n----\n", 1)
        self.assertIn("DRYRUN-MARKER", prompt)
        self.assertIn("SAMPLE-MARKER", prompt)
        self.assertIn("--disallowedTools", argv)
        self.assertIn("Bash,Edit,Write", argv)
        self.assertIn("--strict-mcp-config", argv)
        for widening in ("--dangerously-skip-permissions", "--permission-mode", "--allowedTools", "bypass"):
            self.assertNotIn(widening, argv)
        self.assertTrue((self.root / r["report"]).is_file())

    def test_no_verdict_is_a_failure_and_missing_harness_is_skipped(self):
        self.unfenced()
        self.assertIsNone(self.run_dry(mode="none")["verdict"])
        self.assertEqual(self.run_dry(mode="fail")["verdict"], "fail")
        self.env["AGENTJ_CLAUDE_BIN"] = str(pathlib.Path(self.tmp) / "nope")
        old = os.environ.get("PATH")
        os.environ["PATH"] = "/nonexistent"
        try:
            r = self.run_dry(harness="opencode")
        finally:
            os.environ["PATH"] = old
        self.assertIn("not installed", r["skipped"])

    def test_cli_exit_codes(self):
        self.unfenced()
        env = {**os.environ, **self.env, "AGENTJ_STATE_DIR": str(self.st.root), "PYTHONPATH": str(HOST)}
        cmd = [sys.executable, "-m", "agentj.cli", "wizard", "dry-run", "daily-report", "--dir", str(self.root), "--json"]
        for mode, code, verdict in (("attention", 0, "attention"), ("ok", 0, "ok"), ("fail", 1, "fail"), ("none", 1, None)):
            r = subprocess.run(cmd, capture_output=True, text=True, env={**env, "WIZ_MODE": mode}, timeout=60)
            self.assertEqual(r.returncode, code, mode + r.stderr)
            self.assertEqual(json.loads(r.stdout)["verdict"], verdict)

    def test_parse_verdict_variants(self):
        P = wizard.parse_verdict
        self.assertEqual(P("x\nVERDICT: ok — fine"), ("ok", "fine"))
        self.assertEqual(P("**VERDICT:** attention — two low SKUs"), ("attention", "two low SKUs"))
        self.assertEqual(P("VERDICT：fail - 数据源未配置"), ("fail", "数据源未配置"))
        self.assertEqual(P("VERDICT: ok — first\nVERDICT: fail — last"), ("fail", "last"))
        self.assertIsNone(P("end with `VERDICT: ok|attention|fail — <one sentence>`"))
        self.assertIsNone(P("VERDICT: good — x"))
        self.assertIsNone(P(""))

    @unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap"), "bubblewrap fence (Linux)")
    def test_fenced_dry_run_cannot_see_the_state_dir(self):
        if fence.problem(self.st, str(self.root)):
            self.skipTest("bubblewrap cannot start here")
        r = self.run_dry(mode="probe")
        self.assertTrue(r["fenced"])
        self.assertEqual(r["verdict"], "attention")
        self.assertIn("STATE_HIDDEN", r["output"])
        self.assertTrue((self.root / r["report"]).is_file())

    def test_refuses_without_init_degrades_without_fence(self):
        bare = State(pathlib.Path(self.tmp) / "nostate")
        os.environ.update(self.env)
        try:
            with self.assertRaises(wizard.WizardError):
                wizard.dry_run(bare, self.root, "daily-report")
            from unittest import mock
            with mock.patch.object(fence, "problem", return_value="no_bwrap"):   # F14: degraded, said in the result
                r = self.run_dry()
            self.assertFalse(r["fenced"])
            self.assertEqual(r["fence_unavailable"], "no_bwrap")
            os.environ.update(self.env)
            with self.assertRaises(wizard.WizardError):
                wizard.dry_run(self.st, self.root, "listing")            # not installed
        finally:
            for k in self.env:
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
