"""0.30.8 #1: Agent J's own trashbin — `agentj delete|rm`, `agentj trash list|restore|review|confirm|sweep`, the daily
sweep's review, the disk-watermark purge order and the heartbeat hand-off. Every test runs with a throw-away HOME, XDG_*
and TMPDIR under one temporary directory; nothing calls systemctl (collect's probe is stubbed)."""
import _hermetic  # noqa: F401
import contextlib
import errno
import importlib.util
import io
import json
import os
import pathlib
import pwd
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from agentj import trash

REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
AJ = pathlib.Path(__file__).resolve().parents[2]
DAY = 24 * 3600


class Base(unittest.TestCase):
    def setUp(self):
        top = os.environ.get("TEST_TMP") or None
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="p0308-", dir=top)).resolve()
        self.addCleanup(self._rm)
        self.home = self.tmp / "home"
        for d in ("", ".config", ".local/state", ".local/share", "tmp"):
            (self.home / d).mkdir(parents=True, exist_ok=True)
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_STATE_HOME": str(self.home / ".local/state"), "XDG_DATA_HOME": str(self.home / ".local/share"),
               "TMPDIR": str(self.home / "tmp"), "AGENTJ_STATE_DIR": str(self.home / ".local/state/agentj")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("AGENTJ_ORDER_ID", None)
        self.assertNotEqual(os.path.realpath(os.path.expanduser("~")), REAL_HOME)
        # the passwd home (state_paths.account_home: trashbin location + guard's fixed inventory) is the fake one too —
        # never stat the developer's real ~/.claude or ~/coding/.env
        p = mock.patch("agentj.state_paths.account_home", return_value=str(self.home))
        p.start()
        self.addCleanup(p.stop)
        self.assertTrue(str(trash.root()).startswith(str(self.home)))
        self.base = trash.root()
        self.work = self.home / "work"
        self.work.mkdir()
        # who(): never read this test process's own cgroup (a developer shell sits in user@.service)
        p = mock.patch("agentj.busy.current_unit", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _rm(self):
        for dirpath, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                with contextlib.suppress(OSError):
                    os.chmod(os.path.join(dirpath, d), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def file(self, name, text="x", age=None):
        p = self.work / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if age is not None:
            t = time.time() - age
            os.utime(p, (t, t))
        return p

    def meta(self, eid):
        return json.loads((self.base / trash.ENTRIES / eid / trash.META).read_text())


class Put(Base):
    def test_location_mode_and_metadata(self):
        p = self.file("a.txt", "hello")
        r = trash.put(str(p))
        self.assertEqual(r["status"], "trashed")
        self.assertEqual(r["method"], "rename")
        self.assertFalse(p.exists())
        self.assertEqual(self.base, self.home / ".local/share/agentj-trash")
        self.assertEqual(stat.S_IMODE(os.stat(self.base).st_mode), 0o700)
        self.assertTrue((self.base / trash.MARK).is_file())
        m = self.meta(r["id"])
        self.assertEqual(stat.S_IMODE(os.stat(self.base / trash.ENTRIES / r["id"] / trash.META).st_mode), 0o600)
        self.assertEqual((m["path"], m["kind"], m["size"], m["status"], m["by"]), (str(p), "file", 5, "held", "unknown"))
        self.assertLessEqual(abs(m["deleted_at"] - time.time()), 5)
        self.assertEqual((self.base / trash.ENTRIES / r["id"] / trash.ITEM).read_text(), "hello")

    def test_directory_and_list(self):
        d = self.work / "d"
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "f").write_text("abc")
        r = trash.put(str(d))
        self.assertEqual(r["status"], "trashed")
        rows = trash.entries()
        self.assertEqual([(x["id"], x["kind"], x["size"]) for x in rows], [(r["id"], "dir", 3)])

    def test_leaf_symlink_moves_the_link_only(self):
        target = self.file("target.txt", "keep")
        link = self.work / "link"
        link.symlink_to(target)
        r = trash.put(str(link))
        self.assertEqual(r["status"], "trashed")
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(target.read_text(), "keep")
        item = self.base / trash.ENTRIES / r["id"] / trash.ITEM
        self.assertTrue(item.is_symlink())
        self.assertEqual(self.meta(r["id"])["kind"], "symlink")
        self.assertEqual(trash.restore(r["id"])["status"], "restored")
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(target))

    def test_cross_device_copy_verify_remove(self):
        d = self.work / "tree"
        (d / "x").mkdir(parents=True)
        (d / "x" / "f.bin").write_bytes(os.urandom(4096))
        (d / "ln").symlink_to("x/f.bin")
        before = trash._fingerprint(str(d))
        real = trash._rename

        def exdev(src, dest):
            if str(src) == str(d):
                raise OSError(errno.EXDEV, "cross-device")
            return real(src, dest)
        with mock.patch.object(trash, "_rename", side_effect=exdev):
            r = trash.put(str(d))
        self.assertEqual((r["status"], r["method"]), ("trashed", "copy"))
        self.assertFalse(d.exists())
        item = self.base / trash.ENTRIES / r["id"] / trash.ITEM
        self.assertEqual(trash._fingerprint(str(item)), before)
        self.assertTrue((item / "ln").is_symlink())
        self.assertFalse([n for n in os.listdir(item.parent) if n.startswith(trash.PARTIAL)])

    def test_cross_device_verify_failure_keeps_source(self):
        p = self.file("v.txt", "original")
        with mock.patch.object(trash, "_rename", side_effect=OSError(errno.EXDEV, "x")), \
             mock.patch.object(trash, "_fingerprint", side_effect=[[("", "file", 1, "a")], [("", "file", 1, "b")]]):
            r = trash.put(str(p))
        self.assertEqual(r["status"], "refused")
        self.assertEqual(p.read_text(), "original")
        self.assertEqual(trash.entries(), [])

    def test_cross_device_source_left_is_reported(self):
        p = self.file("s.txt", "data")
        with mock.patch.object(trash, "_rename", side_effect=OSError(errno.EXDEV, "x")), \
             mock.patch.object(trash, "_remove", side_effect=[PermissionError("no")]):
            r = trash.put(str(p))
        self.assertEqual(r["status"], "source_left")
        self.assertTrue(self.meta(r["id"])["source_left"])
        self.assertEqual((self.base / trash.ENTRIES / r["id"] / trash.ITEM).read_text(), "data")

    def test_refusals(self):
        state = pathlib.Path(os.environ["AGENTJ_STATE_DIR"])
        state.mkdir(parents=True)
        trash.ensure()
        cases = {"/": "root", str(self.home): "home", str(self.base): "trashbin",
                 str(self.base / trash.ENTRIES): "trashbin", str(self.home / ".local/share"): "trashbin",
                 str(state): "state_dir", str(self.home / ".local/state"): "state_dir",
                 str(self.work / "missing"): "missing", "": "invalid"}
        for raw, why in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(trash.put(raw), {"status": "refused", "why": why})
        locked = self.work / "locked"
        locked.mkdir()
        (locked / "f").write_text("x")
        os.chmod(locked, 0)
        try:
            if os.access(locked, os.R_OK):
                self.skipTest("running as root")
            self.assertEqual(trash.put(str(locked / "f")), {"status": "refused", "why": "parent_unreadable"})
        finally:
            os.chmod(locked, 0o700)
        self.assertTrue((self.home).is_dir() and state.is_dir())

    def test_special_file_refused(self):
        fifo = self.work / "pipe"
        os.mkfifo(fifo)
        self.assertEqual(trash.put(str(fifo)), {"status": "refused", "why": "unsupported_kind"})
        self.assertTrue(stat.S_ISFIFO(os.lstat(fifo).st_mode))

    def test_who_from_host_env_only(self):
        self.assertEqual(trash.who({"AGENTJ_ORDER_ID": "ord-12"}), "dispatch:ord-12")
        self.assertEqual(trash.who({"AGENTJ_ORDER_ID": "bad id; rm -rf"}), "unknown")
        self.assertEqual(trash.who({"AGENTJ_PERM_CLAIM": "/x/agentperm/task-claim-abc", "AGENTJ_PERM_SOCK": "/s"}),
                         "scheduled_task")
        self.assertEqual(trash.who({"AGENTJ_PERM_SOCK": "/x/agentperm/perm.sock"}), "main_session")
        self.assertEqual(trash.who({}), "unknown")
        with mock.patch("agentj.busy.current_unit", return_value="agentj-busy-k1.service"), \
             mock.patch.object(trash.sys, "platform", "linux"):
            self.assertEqual(trash.who({"AGENTJ_PERM_SOCK": "/s"}), "job:agentj-busy-k1.service")
        with mock.patch("agentj.busy.current_unit", return_value="user@1000.service"), \
             mock.patch.object(trash.sys, "platform", "linux"):
            self.assertEqual(trash.who({}), "unknown")


class Restore(Base):
    def set_path(self, eid, path):
        m = self.meta(eid)
        m["path"] = str(path)
        (self.base / trash.ENTRIES / eid / trash.META).write_text(json.dumps(m))

    def test_restore_refuses_overwrite_and_has_no_other_destination(self):
        p = self.file("r.txt", "v1")
        eid = trash.put(str(p))["id"]
        p.write_text("new")
        self.assertEqual(trash.restore(eid), {"status": "refused", "why": "target_exists"})
        self.assertEqual(p.read_text(), "new")
        with self.assertRaises(TypeError):                       # security r1 H1: `--to` removed
            trash.restore(eid, str(self.work / "elsewhere"))
        p.unlink()
        self.assertEqual(trash.restore(eid), {"status": "restored", "path": str(p)})
        self.assertEqual(p.read_text(), "v1")
        self.assertEqual(trash.entries(), [])
        self.assertEqual(trash.restore(eid)["why"], "no_such_id")
        self.assertEqual(trash.restore("../../etc")["why"], "bad_id")

    def test_restore_into_trashbin_refused(self):
        eid = trash.put(str(self.file("q.txt")))["id"]
        self.set_path(eid, self.base / "x")
        self.assertEqual(trash.restore(eid)["why"], "trashbin")

    def test_edited_meta_path_cannot_create_a_protected_path(self):
        # security r1 H1 probe: write a file → agentj delete → point meta.path at a hooks / settings file → restore
        project = self.work / "proj"
        (project / ".claude").mkdir(parents=True)
        (self.home / ".claude").mkdir()
        state = pathlib.Path(os.environ["AGENTJ_STATE_DIR"])
        state.mkdir(parents=True)
        payload = self.file("evil.json", '{"hooks": {}}')
        eid = trash.put(str(payload))["id"]
        cases = {project / ".claude/settings.local.json": "protected",
                 self.home / ".claude/settings.json": "protected",
                 self.home / ".config/opencode/opencode.json": "protected",
                 state / "estop.json": "state_dir", state / "x.txt": "state_dir",
                 self.home / "coding/.env": "credential"}
        (self.home / ".config/opencode").mkdir(parents=True)
        (self.home / "coding").mkdir()
        with contextlib.chdir(project):
            for target, why in cases.items():
                with self.subTest(target=str(target)):
                    self.set_path(eid, target)
                    self.assertEqual(trash.restore(eid), {"status": "refused", "why": why})
                    self.assertFalse(os.path.lexists(target))
        self.assertTrue((self.base / trash.ENTRIES / eid / trash.ITEM).exists())
        self.set_path(eid, payload)                                 # the real original path still works
        self.assertEqual(trash.restore(eid)["status"], "restored")

    def test_cli_restore_has_no_to(self):
        eid = trash.put(str(self.file("cli.txt")))["id"]
        from agentj import cli
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            cli.main(["trash", "restore", eid, "--to", str(self.work / "x.txt")])
        self.assertIn("--to", str(cm.exception.code))
        self.assertFalse((self.work / "x.txt").exists())
        self.assertEqual(self.meta(eid)["status"], "held")

    def race(self, eid, target, make):
        """Something appears at the target between the existence check and the move (security r1 L1)."""
        real = trash._noreplace

        def late(src, dest):
            if dest == str(target) and not os.path.lexists(target):
                make()
            return real(src, dest)
        with mock.patch.object(trash, "_noreplace", side_effect=late):
            return trash.restore(eid)

    def test_restore_race_never_replaces(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback), mock.patch.object(trash, "_renameat2", False if fallback else None):
                f = self.file(f"race{fallback}.txt", "binned")
                fid = trash.put(str(f))["id"]
                self.assertEqual(self.race(fid, f, lambda: f.write_text("theirs")),
                                 {"status": "refused", "why": "target_exists"})
                self.assertEqual(f.read_text(), "theirs")
                self.assertEqual((self.base / trash.ENTRIES / fid / trash.ITEM).read_text(), "binned")
                d = self.work / f"raced{fallback}"
                (d / "in").mkdir(parents=True)
                did = trash.put(str(d))["id"]
                self.assertEqual(self.race(did, d, lambda: d.mkdir()), {"status": "refused", "why": "target_exists"})
                self.assertEqual(os.listdir(d), [])
                self.assertTrue((self.base / trash.ENTRIES / did / trash.ITEM / "in").is_dir())
                d.rmdir()
                self.assertEqual(trash.restore(did)["status"], "restored")
                self.assertTrue((d / "in").is_dir())

    def test_restore_across_devices_copies_without_replacing(self):
        f = self.file("xdev.txt", "payload")
        eid = trash.put(str(f))["id"]
        real = trash._noreplace

        def exdev(src, dest):
            if src.endswith(trash.ITEM):
                raise OSError(errno.EXDEV, "cross-device")
            return real(src, dest)
        with mock.patch.object(trash, "_noreplace", side_effect=exdev):
            self.assertEqual(trash.restore(eid)["status"], "restored")
        self.assertEqual(f.read_text(), "payload")
        self.assertEqual(trash.entries(), [])
        self.assertFalse([n for n in os.listdir(self.work) if n.startswith(".agentj-restore-")])


class ProtectedSet(Base):
    """Security r1 H1 / M3: agentj delete cannot move what `rm` / `mv` is carded or refused for."""
    def test_harness_settings_hooks_and_plugins_refused(self):
        project = self.work / "proj"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude/settings.json").write_text("{}")
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text("{}")
        plug = self.home / ".config/opencode/plugins/agentj-dispatch"
        plug.mkdir(parents=True)
        (plug / "index.js").write_text("x")
        alias = self.work / "alias.json"
        os.link(self.home / ".claude/settings.json", alias)          # a hardlink of a protected file
        with contextlib.chdir(project):
            for raw in (str(self.home / ".claude/settings.json"), "~/.claude/settings.json", ".claude/settings.json",
                        ".claude", str(self.home / ".claude"), str(plug), str(plug / "index.js"), str(alias)):
                with self.subTest(raw=raw):
                    self.assertEqual(trash.put(raw), {"status": "refused", "why": "protected"})
        self.assertTrue((project / ".claude/settings.json").exists() and alias.exists() and plug.is_dir())
        self.assertEqual(trash.entries(), [])

    def test_passwd_home_counts_when_home_is_changed(self):
        other = self.tmp / "account"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude/settings.json").write_text("{}")
        with mock.patch.object(trash, "_account_home", return_value=str(other)):
            self.assertEqual(trash.put(str(other / ".claude/settings.json")), {"status": "refused", "why": "protected"})

    def test_credential_inventory_refused(self):
        cred = self.home / "coding/.env"
        cred.parent.mkdir()
        cred.write_text("K=v\n")
        d = self.work / "launder"
        d.mkdir()
        os.link(cred, d / "copy")
        os.link(cred, self.work / "alias.env")
        for raw in (str(cred), str(self.home / "coding"), str(self.work / "alias.env"), str(d)):
            with self.subTest(raw=raw):
                self.assertEqual(trash.put(raw), {"status": "refused", "why": "credential"})
        self.assertEqual(cred.read_text(), "K=v\n")
        self.assertEqual(trash.entries(), [])

    def test_anything_under_a_state_root_refused(self):
        state = pathlib.Path(os.environ["AGENTJ_STATE_DIR"])
        (state / "agentperm").mkdir(parents=True)
        for name in ("devices.json", "approver.json", "estop.json", "notes.txt", "agentperm"):
            p = state / name
            if not p.exists():
                p.write_text("{}")
            with self.subTest(name=name):
                self.assertEqual(trash.put(str(p)), {"status": "refused", "why": "state_dir"})
                self.assertTrue(p.exists())
        second = self.home / ".local/state/agentj-2"
        second.mkdir()
        (second / "devices.json").write_text("{}")
        self.assertEqual(trash.put(str(second / "devices.json")), {"status": "refused", "why": "state_dir"})

    def test_ordinary_files_still_trashed(self):
        project = self.work / "proj"
        (project / ".claude/skills").mkdir(parents=True)
        (project / "notes.md").write_text("n")
        with contextlib.chdir(project):
            self.assertEqual(trash.put("notes.md")["status"], "trashed")
            self.assertEqual(trash.put(".claude/skills")["status"], "trashed")


class OperandAdapter(Base):
    """`agentj delete|rm|trash <paths>` / `agentj trash restore <id>` are write operands for the danger hook, RiskGuard
    and NativePathGuard (uv_list.protected_targets) exactly like `rm <path>`; ordinary paths add no card."""
    def hits(self, command, cwd):
        from agentj import uv_list
        return uv_list.protected_targets("Bash", {"command": command}, cwd=str(cwd), env=dict(os.environ))

    def test_agentj_shapes_reach_protected_targets(self):
        project = self.work / "proj"
        (project / ".claude").mkdir(parents=True)
        (self.home / ".claude").mkdir()
        home_s = os.path.realpath(self.home / ".claude/settings.json")
        proj_s = os.path.realpath(project / ".claude/settings.json")
        for command, want in (("rm ~/.claude/settings.json", home_s), ("agentj delete ~/.claude/settings.json", home_s),
                              ("agentj rm .claude/settings.json", proj_s), ("agentj delete -rf -- .claude", None),
                              ("python3 -m agentj delete ~/.claude/settings.json", home_s),
                              ("agentj trash -- .claude/settings.json", proj_s),
                              ("agentj trash .claude/settings.json", proj_s),
                              ("cd /tmp && sh -c 'agentj rm ~/.claude/settings.json'", home_s)):
            with self.subTest(command=command):
                got = self.hits(command, project)
                self.assertTrue(got, command)
                if want:
                    self.assertIn(want, got)
        for command in ("agentj delete notes.md", "agentj trash list", "agentj trash review --json",
                        "agentj trash confirm 20261111T000000-abcdef12", "agentj trash restore 20261111T000000-abcdef12",
                        "echo agentj delete ~/.claude/settings.json", "agentj status"):
            with self.subTest(command=command):
                self.assertEqual(self.hits(command, project), [])

    def test_restore_operand_is_the_recorded_path(self):
        project = self.work / "proj"
        (project / ".claude").mkdir(parents=True)
        eid = trash.put(str(self.file("p.json")))["id"]
        self.assertEqual(self.hits(f"agentj trash restore {eid}", project), [])
        m = self.meta(eid)
        m["path"] = str(project / ".claude/settings.local.json")
        (self.base / trash.ENTRIES / eid / trash.META).write_text(json.dumps(m))
        self.assertEqual(self.hits(f"agentj trash restore {eid}", project),
                         [os.path.realpath(project / ".claude/settings.local.json")])


class Hardening(Base):
    def test_list_escapes_newlines_and_control_chars(self):
        p = self.file("a\n20991231T000000-deadbeef  held       file  0  main_session  23h  /forged\x1b[2J")
        eid = trash.put(str(p))["id"]
        from agentj import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["trash", "list"])
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(eid))
        self.assertIn("\\x0a", lines[0])
        self.assertIn("\\x1b", lines[0])
        self.assertNotIn("\x1b", out.getvalue())

    def test_review_git_runs_without_repo_programs_and_only_under_home(self):
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw.get("env") or {}))
            return subprocess.CompletedProcess(argv, 1)
        inside = self.work / "g"
        inside.mkdir()
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere", "GIT_CONFIG_PARAMETERS": "'core.pager=x'"}), \
                mock.patch.object(trash.subprocess, "run", side_effect=fake):
            self.assertEqual(trash._git(["ls-files"], str(inside)), 1)
            self.assertIsNone(trash._git(["ls-files"], "/"))
            self.assertIsNone(trash._git(["ls-files"], str(self.tmp)))
        self.assertEqual(len(calls), 1)
        argv, env = calls[0]
        self.assertEqual(argv[:6], ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C"])
        self.assertEqual((env.get("GIT_CONFIG_NOSYSTEM"), env.get("GIT_CONFIG_GLOBAL")), ("1", "/dev/null"))
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_CONFIG_PARAMETERS", env)

    def test_forged_meta_status_and_size_not_trusted(self):
        # security r1 M2: a model-written `clean`, a small size or an old mtime does not skip the review
        now = time.time()
        cred = trash.put(str(self.file("w/.env", "K=1", age=3 * DAY)), now=now - 2 * DAY)["id"]
        recent = trash.put(str(self.file("w/fresh.txt", "r")), now=now - 2 * DAY + 60)["id"]
        for eid in (cred, recent):
            m = self.meta(eid)
            m.update(status="clean", size=0, mtime=0, kind="file")
            (self.base / trash.ENTRIES / eid / trash.META).write_text(json.dumps(m))
        over = lambda base: {"over": True, "need": 10 ** 9}   # noqa: E731
        with mock.patch.object(trash, "_temp", return_value=False):
            r = trash.pressure(now=now, disk_fn=over)
        self.assertEqual((r["purged"], r["suspicious"]), (0, 2))
        left = {m["id"]: m for m in trash.entries()}
        self.assertIn("credential", left[cred]["reasons"])
        self.assertIn("recent_use", left[recent]["reasons"])
        with mock.patch.object(trash, "_temp", return_value=False):
            self.assertEqual(trash.sweep(now=now)["purged"], 0)


class Sweep(Base):
    def setUp(self):
        super().setUp()
        self.now = time.time()

    def old_put(self, p, age=DAY + 3600):
        r = trash.put(str(p), now=self.now - age)
        self.assertEqual(r["status"], "trashed")
        if p.parent.exists():
            t = self.now - age - 60
            os.utime(p.parent, (t, t))
        return r["id"]

    def test_clean_purged_suspicious_kept(self):
        clean = self.old_put(self.file("scratch/old.log", "x", age=3 * DAY))
        cred = self.old_put(self.file("proj/.env", "K=v", age=3 * DAY))
        ident = self.old_put(self.file("notes/CLAUDE.md", "id", age=3 * DAY))
        young = trash.put(str(self.file("young.txt", age=3 * DAY)), now=self.now - 3600)["id"]
        with mock.patch.object(trash, "_temp", return_value=False):
            r = trash.sweep(now=self.now)
        self.assertEqual(r["ids"], [clean])
        self.assertEqual((r["purged"], r["suspicious"], r["kept"]), (1, 2, 1))
        left = {m["id"]: m for m in trash.entries()}
        self.assertNotIn(clean, left)
        self.assertEqual(left[cred]["status"], "suspicious")
        self.assertIn("credential", left[cred]["reasons"])
        self.assertIn("identity", left[ident]["reasons"])
        self.assertEqual(left[young]["status"], "held")
        # a second sweep never deletes a suspicious entry; confirm → deleted by the next sweep
        self.assertEqual(trash.sweep(now=self.now)["purged"], 0)
        self.assertEqual(trash.confirm(cred)["status"], "confirmed")
        self.assertEqual(trash.sweep(now=self.now)["ids"], [cred])

    def test_git_tracked_recent_and_parent_active(self):
        repo = self.work / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        tracked = repo / "kept.py"
        tracked.write_text("print(1)")
        subprocess.run(["git", "-C", str(repo), "add", "kept.py"], check=True)
        t_id = self.old_put(tracked)
        recent = self.file("fresh/report.txt", "r")                 # modified right before its deletion
        r_id = self.old_put(recent, age=DAY + 60)
        meta = self.meta(r_id)
        meta["mtime"] = meta["deleted_at"] - 30
        (self.base / trash.ENTRIES / r_id / trash.META).write_text(json.dumps(meta))
        busy = self.file("busy/cache.tmp", "c", age=3 * DAY)
        b_id = self.old_put(busy)
        (busy.parent / "newer").write_text("activity after the deletion")
        with mock.patch.object(trash, "_temp", return_value=False):
            trash.sweep(now=self.now)
        left = {m["id"]: m.get("reasons") for m in trash.entries()}
        self.assertIn("git_tracked", left[t_id])
        self.assertIn("recent_use", left[r_id])
        self.assertIn("parent_active", left[b_id])

    def test_dry_run_changes_nothing(self):
        eid = self.old_put(self.file("dry/x.log", age=3 * DAY))
        with mock.patch.object(trash, "_temp", return_value=False):
            r = trash.sweep(now=self.now, dry_run=True)
        self.assertEqual(r["ids"], [eid])
        self.assertEqual(self.meta(eid)["status"], "held")

    def test_purge_never_follows_a_planted_symlink(self):
        outside = self.home / "outside"
        outside.mkdir()
        (outside / trash.ITEM).write_text("precious")
        eid = self.old_put(self.file("p/x.log", age=3 * DAY))
        entry = self.base / trash.ENTRIES / eid
        shutil.rmtree(entry)
        entry.symlink_to(outside)
        self.assertEqual(trash.entries(), [])
        with self.assertRaises(OSError):
            trash._purge(self.base, eid)
        self.assertEqual((outside / trash.ITEM).read_text(), "precious")

    def test_watermark_purges_oldest_reviewed_first(self):
        sus = self.old_put(self.file("w/.env", "K=1", age=3 * DAY), age=10 * 3600)
        a = self.old_put(self.file("w/a.log", "a" * 100, age=3 * DAY), age=9 * 3600)
        b = self.old_put(self.file("w/b.log", "b" * 100, age=3 * DAY), age=8 * 3600)
        c = self.old_put(self.file("w/c.log", "c" * 100, age=3 * DAY), age=7 * 3600)
        os.utime(self.work / "w", (self.now - DAY, self.now - DAY))
        over = lambda base: {"over": True, "need": 150}   # noqa: E731
        with mock.patch.object(trash, "_temp", return_value=False):
            r = trash.pressure(now=self.now, disk_fn=over)
        left = {m["id"]: m["status"] for m in trash.entries()}
        self.assertEqual((r["purged"], r["suspicious"]), (2, 1))
        self.assertNotIn(a, left)
        self.assertNotIn(b, left)
        self.assertEqual(left, {sus: "suspicious", c: "held"})
        calm = lambda base: {"over": False, "need": 0}   # noqa: E731
        self.assertEqual(trash.pressure(now=self.now, disk_fn=calm)["purged"], 0)

    def test_disk_line_scales_with_small_disks(self):
        class V:
            f_frsize = 1
            def __init__(self, total, free):
                self.f_blocks, self.f_bfree, self.f_bavail = total, free, free
        with mock.patch.object(trash.os, "statvfs", return_value=V(2000 * trash.GIB, 1500 * trash.GIB)):
            self.assertFalse(trash.disk(self.base)["over"])
        with mock.patch.object(trash.os, "statvfs", return_value=V(2000 * trash.GIB, 300 * trash.GIB)):
            self.assertTrue(trash.disk(self.base)["over"])
        with mock.patch.object(trash.os, "statvfs", return_value=V(256 * trash.GIB, 200 * trash.GIB)):
            self.assertFalse(trash.disk(self.base)["over"])
        with mock.patch.object(trash.os, "statvfs", return_value=V(256 * trash.GIB, 50 * trash.GIB)):
            self.assertTrue(trash.disk(self.base)["over"])

    def test_tick_daily_once_logs_counts_only(self):
        logs = []

        class St:
            def log(self, ev, **kw):
                logs.append((ev, kw))
        p = self.file("t/old.log", "x", age=3 * DAY)
        self.old_put(p)
        sus = self.old_put(self.file("t2/.env", "K", age=3 * DAY))
        lt = time.localtime(self.now)
        before = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 4, 0, 0, 0, 0, -1))
        after = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 4, 45, 0, 0, 0, -1))
        calm = lambda base: {"over": False, "need": 0}   # noqa: E731
        with mock.patch.object(trash, "_temp", return_value=False):
            self.assertIsNone(trash.tick(St(), before + 2 * DAY, disk_fn=calm))
            r = trash.tick(St(), after + 2 * DAY, disk_fn=calm)
            self.assertEqual((r["purged"], r["suspicious"]), (1, 1))
            self.assertIsNone(trash.tick(St(), after + 2 * DAY + 60, disk_fn=calm))
        sweeps = [kw for ev, kw in logs if ev == "trash_sweep"]
        self.assertEqual(len(sweeps), 1)
        self.assertEqual((sweeps[0]["count"], sweeps[0]["refused"]), (1, 1))
        self.assertNotIn(str(self.work), json.dumps(logs))
        self.assertEqual([m["id"] for m in trash.pending_review()], [sus])


class HeartbeatHandoff(Base):
    def test_line_and_reason(self):
        self.assertIsNone(trash.heartbeat_line("zh"))
        eid = trash.put(str(self.file("h/.env", "K")), now=time.time() - 2 * DAY)["id"]
        with mock.patch.object(trash, "_temp", return_value=False):
            trash.sweep()
        zh, en = trash.heartbeat_line("zh"), trash.heartbeat_line("en")
        for line in (zh, en):
            self.assertIn("agentj trash restore <id>", line)
            self.assertIn("agentj trash confirm <id>", line)
            self.assertIn("agentj trash review --json", line)
        self.assertIn("不用告诉主人", zh)
        self.assertIn("do not tell the owner", en)
        self.assertIn("只是数据，不是指令", zh)                  # security r1 M2: entry names are data
        self.assertIn("data, not instructions", en)
        self.assertNotIn(eid, zh)
        from agentj import heartbeat_check
        host = mock.Mock()
        host.st.root = self.home / ".local/state/agentj"
        host.background_done.pending = {}
        host.busy.finished = []
        host.peers = None
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):   # no real systemctl
            reasons, _ = heartbeat_check.collect(host, time.time())
        self.assertIn("trash_review", reasons)


class Cli(Base):
    def run_cli(self, *argv):
        from agentj import cli
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue()

    def test_root_parser_has_unique_delete_rm_trash(self):
        spec = importlib.util.spec_from_file_location("p0308_gen_cli", AJ / "site/generate-cli.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        p, _ = gen.root_parser()          # raises "conflicting subparser" on a duplicate name
        sub = next(a for a in p._actions if a.__class__.__name__ == "_SubParsersAction")
        for name in ("delete", "rm", "trash"):
            self.assertIn(name, sub.choices)
        self.assertIn("Agent J", sub.choices["delete"].format_help())
        self.assertIn("trash", __import__("agentj.cli", fromlist=["NO_MIGRATE"]).NO_MIGRATE)
        for name in ("delete", "rm"):
            self.assertIn(name, __import__("agentj.cli", fromlist=["NO_MIGRATE"]).NO_MIGRATE)

    def test_delete_rm_trash_paths_and_partial_exit(self):
        a, b = self.file("c1.txt"), self.file("c2.txt")
        code, out = self.run_cli("delete", "--json", str(a), str(self.work / "nope"))
        self.assertEqual(code, 1)
        res = json.loads(out)["results"]
        self.assertEqual([r["status"] for r in res], ["trashed", "refused"])
        code, out = self.run_cli("rm", "-rf", str(b))
        self.assertEqual(code, 0)
        self.assertFalse(b.exists())
        named = self.file("list")
        code, _ = self.run_cli("trash", "--", str(named))        # backward compatible: a bare path = agentj delete
        self.assertEqual(code, 0)
        self.assertFalse(named.exists())
        code, out = self.run_cli("trash", "list", "--json")
        self.assertEqual(code, 0)
        rows = json.loads(out)["entries"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(0 < r["expires_in"] <= DAY for r in rows))
        mine = next(r["id"] for r in rows if r["path"] == str(a))   # same second: id order is random
        code, out = self.run_cli("trash", "restore", mine)
        self.assertEqual(code, 0)
        self.assertTrue(a.exists())
        code, out = self.run_cli("trash", "review", "--json")
        self.assertEqual(json.loads(out), {"entries": []})
        code, out = self.run_cli("trash", "sweep", "--dry-run", "--json")
        self.assertEqual((code, json.loads(out)["dry_run"]), (0, True))

    def test_docs_and_skill_say_agentj_delete(self):
        skill = (AJ / "host/agentj/skills/agentj-config/SKILL.md").read_text()
        self.assertIn("agentj delete", skill)
        self.assertIn("agentj trash restore", skill)
        cli_md = (AJ / "docs/CLI.md").read_text()
        for word in ("### agentj delete", "### agentj rm", "### agentj trash"):
            self.assertIn(word, cli_md)


class InventoryR2(Base):
    """Security r2 H1/H2: the guard inventory is the union of the passwd-home environment of every instance and the
    caller's own — a caller variable can only add rows. Each override var is probed for put and for restore."""
    def setUp(self):
        super().setUp()
        self.evil = self.tmp / "evil"
        self.evil.mkdir()
        h = self.home
        for rel in (".claude/settings.json", ".codex/config.toml", ".config/agentj/config.json5",
                    ".config/agentj-2/config.json5", ".config/opencode/plugins/agentj-shared.js",
                    ".local/state/agentj/devices.json", ".config/systemd/user/agentj.env",
                    ".config/systemd/user/agentj-2.env"):
            (h / rel).parent.mkdir(parents=True, exist_ok=True)
            (h / rel).write_text("{}")

    def restore_to(self, target):
        eid = trash.put(str(self.file("carrier-%s.txt" % os.urandom(3).hex(), "payload")))["id"]
        m = self.meta(eid)
        m["path"] = str(target)
        (self.base / trash.ENTRIES / eid / trash.META).write_text(json.dumps(m))
        return trash.restore(eid)

    CASES = (("HOME", None, ".claude/settings.json", "protected"),
             ("XDG_CONFIG_HOME", None, ".config/agentj/config.json5", "protected"),
             ("XDG_CONFIG_HOME", None, ".config/opencode/plugins/agentj-shared.js", "protected"),
             ("AGENTJ_INSTANCE", "2", ".config/agentj/config.json5", "protected"),
             ("AGENTJ_STATE_DIR", None, ".local/state/agentj/devices.json", "state_dir"),
             ("AGENTJ_SERVICE_NAME", "evil-unit", ".config/systemd/user/agentj.env", "credential"),
             ("CLAUDE_CONFIG_DIR", None, ".claude/settings.json", "protected"),
             ("CODEX_HOME", None, ".codex/config.toml", "protected"))

    def test_override_vars_hide_nothing_for_put(self):
        for var, value, rel, why in self.CASES:
            with self.subTest(var=var, rel=rel), mock.patch.dict(os.environ, {var: value or str(self.evil)}):
                self.assertEqual(trash.put(str(self.home / rel)), {"status": "refused", "why": why})
                self.assertTrue((self.home / rel).exists())
        self.assertEqual(trash.entries(), [])

    def test_override_vars_hide_nothing_for_restore(self):
        for var, value, rel, why in self.CASES:
            target = self.home / rel
            target.unlink()
            with self.subTest(var=var, rel=rel), mock.patch.dict(os.environ, {var: value or str(self.evil)}):
                self.assertEqual(self.restore_to(target), {"status": "refused", "why": why})
                self.assertFalse(target.exists())
            target.write_text("{}")

    def test_every_instance_service_env_file_is_a_credential(self):
        self.assertEqual(trash.put(str(self.home / ".config/systemd/user/agentj-2.env")),
                         {"status": "refused", "why": "credential"})
        n10 = self.home / ".config/systemd/user/agentj-10.env"
        n10.write_text("K=v")
        self.assertEqual(trash.put(str(n10)), {"status": "refused", "why": "credential"})
        self.assertEqual(trash.put(str(self.home / ".config/agentj-2/config.json5")),
                         {"status": "refused", "why": "protected"})

    def test_owner_extra_env_files_from_the_passwd_home_preferences(self):
        extra, extra2 = self.home / "secrets/extra.env", self.home / "secrets/seat2.env"
        extra.parent.mkdir()
        extra.write_text("K=v")
        extra2.write_text("K=v")
        (self.home / ".config/agentj/config.json5").write_text(
            '{api_keys: {extra_env_files: ["~/secrets/extra.env"]}}')
        (self.home / ".config/agentj-2/config.json5").write_text(json.dumps({"api_keys": {"extra_env_files": [str(extra2)]}}))
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.evil), "HOME": str(self.evil)}):
            for f in (extra, extra2):
                with self.subTest(f=f.name):
                    self.assertEqual(trash.put(str(f)), {"status": "refused", "why": "credential"})
            self.assertEqual(self.restore_to(self.home / "secrets/new.env")["status"], "restored")   # not listed
        self.assertTrue(extra.exists() and extra2.exists())

    def test_trash_dir_ignores_the_environment(self):
        with mock.patch.dict(os.environ, {"AGENTJ_TRASH_DIR": str(self.evil), "HOME": str(self.evil)}):
            self.assertEqual(trash.root(), self.home / ".local/share/agentj-trash")
        from agentj import state_paths
        with mock.patch("agentj.state_paths.account_home", side_effect=state_paths._passwd_home), \
                self.assertRaises(RuntimeError):                  # a test process never gets the real trashbin
            state_paths.trash_dir()


class PreToolR2(Base):
    """Security r2 H1/L2/L3: interpreter flags and --instance do not hide the verb; `agentj delete <protected>` is a
    hook DENY (not a card); `rm`/`mv` unchanged."""
    def refusal(self, command, cwd=None):
        from agentj import uv_list
        return uv_list.agentj_delete_refusal("Bash", {"command": command}, cwd=str(cwd or self.work))

    def test_args_strip_instance_and_interpreter_flags(self):
        from agentj import uv_list
        for name, args, want in (
                ("agentj", ["--instance", "2", "delete", "x"], ["delete", "x"]),
                ("agentj", ["--instance=3", "rm", "x"], ["rm", "x"]),
                ("agentj", ["delete", "--instance", "2", "x"], ["delete", "x"]),
                ("agentj", ["delete", "--", "--instance", "x"], ["delete", "--", "--instance", "x"]),
                ("python3", ["-I", "-m", "agentj", "delete", "x"], ["delete", "x"]),
                ("python3", ["-I", "-B", "-E", "-s", "-S", "-u", "-O", "-OO", "-X", "utf8", "-W", "ignore", "-m",
                             "agentj", "--instance", "2", "trash", "x"], ["trash", "x"]),
                ("python3.13", ["-IBm", "agentj.cli", "rm", "x"], ["rm", "x"]),
                ("python", ["-Xdev", "-magentj", "delete", "x"], ["delete", "x"]),
                ("python3", ["-c", "print(1)", "-m", "agentj"], None),
                ("python3", ["-m", "other", "delete", "x"], None)):
            with self.subTest(args=args):
                self.assertEqual(uv_list._agentj_args(name, args), want)

    def test_protected_and_credential_operands_are_denied(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text("{}")
        cfg = self.home / ".config/agentj/config.json5"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{}")
        cred = self.home / "coding/.env"
        cred.parent.mkdir()
        cred.write_text("K=v")
        env2 = self.home / ".config/systemd/user/agentj-2.env"
        env2.parent.mkdir(parents=True)
        env2.write_text("K=v")
        from agentj import uv_list
        for command in (f"agentj --instance 2 delete {cfg}", "python3 -I -m agentj delete ~/.claude/settings.json",
                        "python3 -X utf8 -Im agentj rm ~/.claude/settings.json",
                        f"AGENTJ_INSTANCE=2 agentj delete {cfg}", f"env XDG_CONFIG_HOME=/x agentj delete {cfg}",
                        "agentj delete ~/coding/.env", f"agentj trash -- {env2}", "agentj rm -rf ~/coding",
                        f"agentj delete {os.environ['AGENTJ_STATE_DIR']}/devices.json"):
            with self.subTest(command=command):
                self.assertEqual(self.refusal(command), uv_list.AGENTJ_DELETE_HINT)
        self.assertIn("Agent J will not move it; the owner removes it locally", uv_list.AGENTJ_DELETE_HINT)
        for command in ("agentj delete notes.md", "rm ~/.claude/settings.json", "mv ~/.claude/settings.json /tmp/x",
                        "agentj trash list", "echo agentj delete ~/coding/.env"):
            with self.subTest(command=command):
                self.assertIsNone(self.refusal(command))
        # rm keeps its card (protected_targets), unchanged
        self.assertTrue(uv_list.protected_targets("Bash", {"command": "rm ~/.claude/settings.json"}, cwd=str(self.work),
                                                  env=dict(os.environ)))

    def test_restore_to_a_forged_protected_path_is_denied(self):
        (self.home / ".claude").mkdir()
        eid = trash.put(str(self.file("p.json")))["id"]
        m = self.meta(eid)
        m["path"] = str(self.home / ".claude/settings.local.json")
        (self.base / trash.ENTRIES / eid / trash.META).write_text(json.dumps(m))
        from agentj import uv_list
        self.assertEqual(self.refusal(f"agentj trash restore {eid}"), uv_list.AGENTJ_DELETE_HINT)

    def test_risk_guard_denies_without_a_card(self):
        import asyncio
        from agentj import shared_risk, uv_list
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text("{}")
        a = mock.Mock(kind="claude", cfg={"dir": str(self.work)})
        a.host.stopped.return_value = False
        a.host.ask = mock.AsyncMock(return_value={"behavior": "allow"})
        a.host.preferences = {}
        a.host.st.root = pathlib.Path(os.environ["AGENTJ_STATE_DIR"])
        g = shared_risk.RiskGuard(a)
        ok = asyncio.run(g.check("Bash", {"command": "python3 -I -m agentj delete ~/.claude/settings.json"}))
        self.assertFalse(ok)
        self.assertEqual(g.reason(), uv_list.AGENTJ_DELETE_HINT)
        a.host.ask.assert_not_called()
        n = shared_risk.NativePathGuard(a)
        out = asyncio.run(n.hook_event({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(self.work),
                                        "tool_input": {"command": "agentj --instance 2 rm ~/.claude/settings.json"}}))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        a.host.ask.assert_not_called()


class EntriesUnreadableR2(Base):
    """Security r2 H2: the payloads under entries/ are never read through a tool."""
    def test_reads_under_entries_denied(self):
        from agentj import uv_list
        eid = trash.put(str(self.file("note.txt", "x")))["id"]
        item = self.base / trash.ENTRIES / eid / trash.ITEM
        env = dict(os.environ)
        for tool, inp in (("Read", {"file_path": str(item)}), ("Bash", {"command": f"cat {item}"}),
                          ("Bash", {"command": f"head ~/.local/share/agentj-trash/entries/{eid}/meta.json"}),
                          ("Bash", {"command": f"cd {self.base}/entries/{eid} && cat item"})):
            with self.subTest(inp=inp):
                self.assertTrue(uv_list.credential_reads(tool, inp, cwd=str(self.work), env=env))
        self.assertFalse(uv_list.credential_reads("Read", {"file_path": str(self.file("other.txt"))},
                                                  cwd=str(self.work), env=env))
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp / "evil")}):   # the passwd home's bin still counts
            self.assertTrue(uv_list.credential_reads("Read", {"file_path": str(item)}, cwd=str(self.work),
                                                     env=dict(os.environ)))


class MetaAndTickR2(Base):
    """Security r2 M1: a hostile meta.json or sweep.json never stops the sweep, the heartbeat line or the hook."""
    def test_nested_meta_is_unreadable_never_purged(self):
        eid = trash.put(str(self.file("deep.txt", "x")), now=time.time() - 3 * DAY)["id"]
        sus = trash.put(str(self.file("k/.env", "K")), now=time.time() - 3 * DAY)["id"]
        with mock.patch.object(trash, "_temp", return_value=False):
            trash.sweep()
        entry = self.base / trash.ENTRIES / eid
        deep = '{"v": 1, "id": "%s", "x": %s1%s}' % (eid, "[" * 30000, "]" * 30000)
        (entry / trash.META).write_text(deep)
        self.assertEqual([m["id"] for m in trash.entries()], [sus])
        self.assertIn("agentj trash review --json", trash.heartbeat_line("en"))
        self.assertIsNone(trash.original_path(eid))
        from agentj import uv_list
        self.assertEqual(uv_list.protected_targets("Bash", {"command": f"agentj trash restore {eid}"},
                                                   cwd=str(self.work), env=dict(os.environ)), [])
        r = trash.sweep(now=time.time() + 2 * DAY)
        self.assertEqual(r["errors"], 0)
        self.assertTrue(entry.is_dir() and (entry / trash.ITEM).exists())     # skipped, never permanently deleted
        self.assertEqual(trash.restore(eid), {"status": "refused", "why": "no_such_id"})
        bad = dict(self.meta(sus), reasons=[[["x"]]])
        (self.base / trash.ENTRIES / sus / trash.META).write_text(json.dumps(bad))
        self.assertNotIn("reasons", trash.entries()[0])

    def test_tick_failure_logs_type_only(self):
        logs = []

        class St:
            def log(self, ev, **kw):
                logs.append((ev, kw))
        with mock.patch.object(trash, "_sweep_due", side_effect=RecursionError("secret /path")):
            self.assertIsNone(trash.tick(St(), disk_fn=lambda b: {"over": False, "need": 0}))
        self.assertEqual(logs, [("trash_tick_failed", {"why": "RecursionError"})])

    def test_future_sweep_marker_is_clamped(self):
        logs = []

        class St:
            def log(self, ev, **kw):
                logs.append((ev, kw))
        trash.ensure()
        lt = time.localtime()
        now = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 5, 0, 0, 0, 0, -1))
        (self.base / trash.SWEEP_FILE).write_text(json.dumps({"day": "2999-12-31", "pressure_at": now + 10 ** 9}))
        seen = []
        r = trash.tick(St(), now, disk_fn=lambda b: seen.append(1) or {"over": False, "need": 0})
        self.assertIsNotNone(r)                                  # the daily sweep ran
        self.assertEqual(seen, [1])                              # and the watermark was checked
        rec = json.loads((self.base / trash.SWEEP_FILE).read_text())
        self.assertEqual((rec["day"], rec["pressure_at"]), (time.strftime("%Y-%m-%d", time.localtime(now)), int(now)))
        (self.base / trash.SWEEP_FILE).write_text(json.dumps({"day": [1], "pressure_at": "x"}))
        self.assertIsNotNone(trash.tick(St(), now, disk_fn=lambda b: {"over": False, "need": 0}))
        self.assertFalse([e for e, _ in logs if e == "trash_tick_failed"])


if __name__ == "__main__":
    unittest.main()
