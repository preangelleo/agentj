"""#455 F3 option B + #469 (0.26.8a2, Jarvis ruling 11-04, chairman-authorized).

- every fence binds the claude / codex / opencode install trees (mise / asdf `installs/<name>`, ~/.opencode/bin,
  ~/.local/share/claude/versions) and an installed agentj read-only; ~/.local/bin stays writable (real bwrap when it runs);
- the host registry (peer_origin.register, outside the fence) records the harness files inside those trees; "sealed" =
  root-owned chain OR inside / on the pinned way to a read-only tree, and the stat identity equals the registry record;
- bare words in today's real dispatch command resolve through PATHs the HOST reads (the shared session's /proc environ,
  serve's own PATH, the user manager's environment) — proven → no card (owner turn and same_host); not proven → the card
  names its class (opaque, never 「改凭据」) and why; negative controls: a fake claude earlier on PATH, a writable
  directory before the hit (fenced caller), owner / hash / new-version mismatch, a mise shim, BASH_ENV → card;
- a harness's own update failing on the read-only tree inside the fence says so (zh / en), never silent.
No real unit, user manager, state or live fence is touched (systemctl / systemd-run spawns fail the test).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import approvals, binaries, fence, peer_origin, provenance  # noqa: E402
from agentj.compose import Send  # noqa: E402
from agentj.state import State  # noqa: E402
import test_p455_same_host as sh  # noqa: E402
from test_p455_security import BINS, SYS, seal  # noqa: E402

MISE = f"{SYS}/usr/bin/mise"
pathlib.Path(MISE).write_bytes(b"\x7fELF synthetic mise")
os.chmod(MISE, 0o755)
# the chairman's real dispatch (approvals.log 11-04), bare words, relative report redirect inside bash -c
# the same remainder without `agentj busy run --`: bash -c runs in the unit with the manager's PATH, nothing pins it
BARE = "systemd-run --user --collect --unit=agentj-p469-ceo -p WorkingDirectory={ws} bash -c \"claude -p go > .jarvis/run-x.log 2>&1\""
TODAY = ("systemd-run --user --collect --unit={unit} -p WorkingDirectory={ws} agentj busy run --who 掌枢 --kind ceo --label x -- "
         "bash -c \"claude -p --permission-mode bypassPermissions '读 .jarvis/PROMPT-x.md 并按其执行' > .jarvis/run-x.log 2>&1\"")


def layout(root):
    """A HOME like TensorBook's: mise installs (two claude versions + `latest`), codex, shims → mise, an empty ~/.local/bin."""
    home = pathlib.Path(root) / "home"
    tree = home / ".local/share/mise/installs/claude"
    for v in ("2.1.296", "2.1.316"):
        (tree / v).mkdir(parents=True)
        (tree / v / "claude").write_bytes(b"\x7fELF claude " + v.encode())
        (tree / v / "claude").chmod(0o755)
    os.symlink("2.1.316", tree / "latest")
    cx = home / ".local/share/mise/installs/codex/0.162.1/bin"
    cx.mkdir(parents=True)
    (cx / "codex").write_bytes(b"\x7fELF codex")
    (cx / "codex").chmod(0o755)
    (home / ".local/share/mise/shims").mkdir(parents=True)
    os.symlink(MISE, home / ".local/share/mise/shims/claude")
    (home / ".local/bin").mkdir(parents=True)
    return home, os.path.realpath(tree)


class Fixture:
    def build(self, case, root):
        self.home, self.tree = layout(root)
        self.ws = pathlib.Path(root) / "ws"
        (self.ws / ".jarvis").mkdir(parents=True, exist_ok=True)
        for p in (mock.patch.dict(peer_origin._REG, {"boot": None, "trees": (), "harness": (), "files": {}}),
                  mock.patch.object(peer_origin, "_MANAGER", [0.0, None]),
                  mock.patch.object(binaries, "SYS_DIRS", (f"{SYS}/usr/bin",))):
            p.start()
            case.addCleanup(p.stop)
        self.reg = peer_origin.register({"HOME": str(self.home), "PATH": "/usr/bin"})
        local = f"{self.home}/.local/bin"
        self.caller = f"{local}:{SYS}/usr/bin:{SYS}/venv/bin"                  # the dispatching shell (unfenced)
        self.unit = {"HOME": str(self.home), "PATH": f"{local}:{self.tree}/latest:{SYS}/usr/bin"}   # the user manager's env
        return self

    def ctx(self, **kw):
        return {"caller": self.caller, "strict": False, "unit": dict(self.unit), **kw}

    def today(self, unit="agentj-p469-ceo"):
        return TODAY.format(unit=unit, ws=self.ws)

    def bare(self):
        return BARE.format(ws=self.ws)


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        seal(self, HOME=None)
        self.f = Fixture().build(self, self.tmp.name)

    def check(self, cmd=None, **kw):
        return peer_origin.check(cmd or self.f.today(), str(self.f.ws), None, self.f.ctx(**kw))

    def plant(self, rel, body=b"#!/bin/sh\nexec evil\n"):
        p = self.f.home / ".local/bin" / rel
        p.write_bytes(body)
        p.chmod(0o755)
        self.addCleanup(p.unlink)
        return p

    def test_registry_trees_and_files(self):
        self.assertEqual(sorted(self.f.reg["trees"]), sorted(os.path.realpath(self.f.home / f".local/share/mise/installs/{n}")
                                                             for n in ("claude", "codex")))
        files = peer_origin._REG["files"]
        self.assertEqual({v[0] for v in files.values()}, {"claude", "codex"})
        self.assertIn(os.path.join(self.f.tree, "2.1.316", "claude"), files)
        self.assertFalse(any(k.startswith(str(self.f.home / ".local/bin")) for k in files), "~/.local/bin is not a tree")

    def test_today_command_is_proven(self):
        self.assertEqual(self.check(), ("agentj-p469-ceo.service", "ok"))
        self.assertEqual(peer_origin.dispatch(self.f.today(), str(self.f.ws), None, self.f.ctx()), "agentj-p469-ceo.service")
        for tail in ("claude -p go", "codex exec go"):          # the harness directly (caller's PATH via a mise install dir)
            cmd = f"systemd-run --user --collect --unit=agentj-p469-ceo {tail}"
            caller = f"{self.f.tree}/latest:{self.f.home}/.local/share/mise/installs/codex/0.162.1/bin:{SYS}/usr/bin"
            self.assertEqual(self.check(cmd, caller=caller), ("agentj-p469-ceo.service", "ok"), tail)

    def test_without_host_paths_bare_words_stay_unproven(self):
        unit, why = peer_origin.check(self.f.today(), str(self.f.ws), None, None)
        self.assertEqual((unit, why), ("agentj-p469-ceo.service", "systemd-run:path_unknown"))
        self.assertEqual(self.check(unit=None)[1], "bash:unit_env_unknown")

    def test_fake_claude_earlier_on_path(self):
        self.plant("claude")
        self.assertEqual(self.check(self.f.bare())[1], "claude:not_registered", "no busy run: unit PATH ~/.local/bin/claude first")
        self.assertEqual(self.check()[1], "ok", "busy run pins the install folder before ~/.local/bin")
        self.plant("systemd-run", b"\x7fELF fake")
        self.assertEqual(self.check()[1], "systemd-run:not_sealed")

    def test_same_named_non_executable_earlier_is_refused(self):
        p = self.plant("bash")
        p.chmod(0o644)
        self.assertEqual(self.check(self.f.bare())[1], "bash:path_shadow", "caller side")
        self.assertEqual(self.check()[1], "ok", "busy run's bash: sealed system dirs come first")

    def test_fenced_caller_needs_closed_path_dirs(self):
        self.assertEqual(self.check(strict=True)[1], "systemd-run:path_writable", "~/.local/bin is writable in the fence")
        self.assertEqual(self.check(strict=True, caller=f"{SYS}/usr/bin:{SYS}/venv/bin"), ("agentj-p469-ceo.service", "ok"))
        tree_first = f"{self.f.tree}/latest:{SYS}/usr/bin:{SYS}/venv/bin"   # a read-only tree dir before the hit: closed
        self.assertEqual(self.check(strict=True, caller=tree_first)[1], "ok")

    def test_mise_shim_is_not_the_registered_binary(self):
        self.f.unit["PATH"] = f"{self.f.home}/.local/share/mise/shims:{self.f.tree}/latest:{SYS}/usr/bin"
        self.assertEqual(self.check(self.f.bare())[1], "claude:not_registered", "the shim runs mise, which reads fence-writable config")

    def test_changed_or_new_harness_file_after_registration(self):
        f = pathlib.Path(self.f.tree) / "2.1.316" / "claude"
        orig = f.read_bytes()
        f.write_bytes(orig + b" swapped")
        self.assertEqual(self.check()[1], "claude:not_registered", "hash / stat identity differs from the record")
        f.write_bytes(orig)
        self.assertEqual(self.check()[1], "claude:not_registered", "same bytes, new ctime: still not the record")
        peer_origin.register({"HOME": str(self.f.home)})   # a host-run update re-registers
        self.assertEqual(self.check()[1], "ok")
        new = pathlib.Path(self.f.tree) / "2.1.317"
        new.mkdir()
        (new / "claude").write_bytes(b"\x7fELF new")
        (new / "claude").chmod(0o755)
        os.remove(pathlib.Path(self.f.tree) / "latest")
        os.symlink("2.1.317", pathlib.Path(self.f.tree) / "latest")
        self.assertEqual(self.check()[1], "claude:not_registered", "installed after registration")

    def test_owner_mismatch_and_writable_modes(self):
        f = os.path.join(self.f.tree, "2.1.316", "claude")
        real = peer_origin._LSTAT

        def other(p):
            s = real(p)
            if os.path.normpath(p) == f:
                return types.SimpleNamespace(**{k: getattr(s, k) for k in peer_origin._KEY if k != "st_uid"}, st_uid=4242)
            return s
        with mock.patch.object(peer_origin, "_LSTAT", other):
            self.assertEqual(self.check()[1], "claude:not_registered", "another uid owns the file")
        os.chmod(f, 0o775)
        self.addCleanup(os.chmod, f, 0o755)
        peer_origin.register({"HOME": str(self.f.home)})
        self.assertEqual(self.check()[1], "claude:not_registered", "group-writable inside the tree")
        os.chmod(f, 0o755)
        os.chmod(os.path.dirname(f), 0o777)
        self.addCleanup(os.chmod, os.path.dirname(f), 0o755)
        peer_origin.register({"HOME": str(self.f.home)})
        self.assertEqual(self.check()[1], "bash:path_writable", "a world-writable pinned folder: bash's strict walk refuses it")
        self.assertEqual(self.check(self.f.bare())[1], "claude:not_registered", "a world-writable directory on the way")

    def test_unit_environment_that_changes_bash(self):
        for k in ("BASH_ENV", "LD_PRELOAD", "PYTHONPATH"):
            self.f.unit[k] = "/x"
            self.assertEqual(self.check(self.f.bare())[1], "claude:unit_env", k)
            self.assertEqual(self.check()[1], "ok" if k == "BASH_ENV" else "bash:unit_env", k + ": agentj itself reads LD_* / PYTHON*")
            del self.f.unit[k]
        self.f.unit["MISE_DATA_DIR"] = None   # a quoted value the host cannot read: busy run's tree is unknown
        self.assertEqual(self.check()[1], "bash:unit_env_unknown")

    def test_today_with_mise_shims_first_is_proven(self):
        """TensorBook 11-04: the user manager's PATH has mise shims FIRST; busy run pins the registered install folder."""
        shims = f"{self.f.home}/.local/share/mise/shims"
        self.f.unit["PATH"] = f"{shims}:{self.f.home}/.local/bin:{SYS}/usr/bin"
        self.plant("claude")
        self.assertEqual(self.check(), ("agentj-p469-ceo.service", "ok"))
        self.assertEqual(self.check(self.f.bare())[1], "claude:not_registered", "without busy run the shim still decides")
        env = binaries.pinned("claude", self.f.unit)
        self.assertEqual(env["PATH"].split(":"), [f"{self.f.tree}/latest", f"{SYS}/usr/bin", shims, f"{self.f.home}/.local/bin"],
                         "only the order changes")
        for tail in ("codex exec go", "bash -c \"codex exec go\""):
            cmd = f"systemd-run --user --collect --unit=agentj-p469-ceo agentj busy run -- {tail}"
            self.assertEqual(self.check(cmd)[1], "ok", tail)
        self.plant("opencode")   # no opencode install tree: nothing to pin, the strict walk meets ~/.local/bin
        self.assertEqual(self.check("systemd-run --user --collect --unit=agentj-p469-ceo agentj busy run -- opencode run go")[1],
                         "opencode:path_writable", "the strict walk: shims (writable) before the hit")

    def test_pin_shape_and_env(self):
        tail = ["bash", "-c", "claude -p --permission-mode bypassPermissions 'x' > .jarvis/run-x.log 2>&1"]
        for argv, want in ((tail, "claude"), (["claude", "-p", "x"], "claude"), (["/usr/bin/bash", "-c", "codex exec x"], "codex"),
                           (["opencode", "run"], "opencode"), (["claude", "--version"], None), (["make"], None), ([], None),
                           (["bash", "-c", "claude -p x; rm -rf ~"], None), (["bash", "-lc", "claude -p x"], None),
                           (["bash", "-c", "claude -p $X"], None)):
            self.assertEqual(peer_origin.pin_name(argv), want, argv)
        env = binaries.pinned("claude", {"HOME": str(self.f.home), "PATH": "/a::/b", "FOO": "keep", "BASH_ENV": "/e", "ENV": "/e",
                                         "SHELLOPTS": "x", "BASHOPTS": "x", "BASH_FUNC_claude%%": "() { evil; }",
                                         "LD_PRELOAD": "/l", "LD_AUDIT": "/l", "PYTHONPATH": "/p", "PYTHONSTARTUP": "/p"})
        self.assertEqual(sorted(env), ["FOO", "HOME", "PATH"])
        self.assertEqual(env["PATH"], f"{self.f.tree}/latest:{SYS}/usr/bin:/a::/b", "old entries kept as they were, after")
        self.assertEqual(binaries.pin_dir("codex", {"HOME": str(self.f.home)}),
                         os.path.realpath(self.f.home / ".local/share/mise/installs/codex") + "/0.162.1/bin", "highest version, bin/")
        os.remove(pathlib.Path(self.f.tree) / "latest")
        self.assertEqual(binaries.pin_dir("claude", {"HOME": str(self.f.home)}), f"{self.f.tree}/2.1.316", "no latest: highest")
        self.assertIsNone(binaries.pin_dir("opencode", {"HOME": str(self.f.home)}))

    def test_tree_first_seen_after_start_is_not_trusted(self):
        oc = self.f.home / ".opencode/bin"
        oc.mkdir(parents=True)
        (oc / "opencode").write_bytes(b"\x7fELF oc")
        (oc / "opencode").chmod(0o755)
        peer_origin.register({"HOME": str(self.f.home)})
        self.assertNotIn(os.path.realpath(oc), peer_origin._REG["trees"], "older fences lack this bind until restart")
        self.assertIn(os.path.realpath(oc), fence.ro_trees(str(self.f.home), {"HOME": str(self.f.home)}), "new fences bind it")

    def test_card_names_the_class_not_credentials(self):
        self.plant("claude")
        unit, why = self.check(self.f.bare())
        v = peer_origin.unproven(why)
        self.assertEqual((v.cats, v.rules), (["opaque"], ["opaque.dispatch", "cred.service"]))
        self.assertIn("不是主机登记的 claude", v.why)
        self.assertIn("not the claude this host registered", peer_origin.unproven(why, "en").why_en)
        self.assertNotIn("credentials", v.cats)

    def test_manager_env_parse_and_cache(self):
        out = "PATH=/usr/bin:/bin\nHOME=/h\nODD=$'a\\nb'\n"
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout=out))
        env = peer_origin.manager_env(run=run)
        self.assertEqual((env["PATH"], env["ODD"]), ("/usr/bin:/bin", None))
        peer_origin.manager_env(run=run)
        self.assertEqual(run.call_count, 1, "cached")
        self.assertEqual(run.call_args[0][0], ["systemctl", "--user", "show-environment"])

    def test_session_path_requires_the_verified_pid(self):
        proc = pathlib.Path(self.tmp.name) / "proc"
        (proc / "77").mkdir(parents=True)
        (proc / "77" / "stat").write_text("77 (x) S 1 77 77 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 555 0 0\n")
        (proc / "77" / "environ").write_bytes(b"HOME=/h\0PATH=/a:/b\0")
        self.assertEqual(peer_origin.session_path({"pid": 77, "procStart": "555"}, proc), "/a:/b")
        self.assertIsNone(peer_origin.session_path({"pid": 77, "procStart": "556"}, proc), "pid reused")


class BusyRunLaunch(unittest.TestCase):
    """The real `agentj busy run` child: the registered install folder's harness runs, startup-injection vars gone."""

    def test_child_runs_the_install_folder_binary(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        home, ws, tree = root / "home", root / "ws", root / "home/.local/share/mise/installs/claude"
        stub = ('#!/bin/sh\necho "{w}|${{BASH_ENV-}}|${{ENV-}}|${{PYTHONSTARTUP-}}|${{PYTHONPATH-}}|${{FOO-}}|$1"\n'
                'echo "${{PATH%%:*}}"\n')
        for d, w in ((tree / "2.1.316", "tree"), (home / ".local/share/mise/shims", "shim"), (home / ".local/bin", "local")):
            d.mkdir(parents=True)
            (d / "claude").write_text(stub.format(w=w))
            (d / "claude").chmod(0o755)
        os.symlink("2.1.316", tree / "latest")
        (ws / ".jarvis").mkdir(parents=True)
        evil = root / "evil.sh"
        evil.write_text(f"touch {root}/startup-ran\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MISE_", "ASDF_", "BASH_FUNC_"))}
        env.update(HOME=str(home), PATH=f"{home}/.local/share/mise/shims:{home}/.local/bin:/usr/bin:/bin", BASH_ENV=str(evil),
                   ENV=str(evil), PYTHONSTARTUP=str(evil), FOO="keep", AGENTJ_STATE_DIR=str(root / "state"),
                   PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1]))

        def run(inner):
            r = subprocess.run([sys.executable, "-c", "from agentj.cli import main; main()", "busy", "run", "--who", "掌枢",
                                "--kind", "ceo", "--label", "x", "--", "bash", "-c", inner + " > .jarvis/run-x.log 2>&1"],
                               cwd=ws, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return (ws / ".jarvis/run-x.log").read_text().split("\n")
        log = run("claude -p --permission-mode bypassPermissions '读 .jarvis/PROMPT-x.md 并按其执行'")
        self.assertEqual(log[:2], ["tree|||||keep|-p", os.path.join(os.path.realpath(tree), "latest")])
        self.assertFalse((root / "startup-ran").exists(), "BASH_ENV / ENV never reached bash")
        self.assertEqual(run("claude --version")[0].split("|")[0], "shim", "not the dispatch shape: busy run leaves PATH alone")
        self.assertTrue((root / "startup-ran").exists(), "control: the same env does inject when unpinned")


class FenceBinds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.tree = layout(self.tmp.name)
        self.ws = pathlib.Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.st.perm_dir.mkdir(exist_ok=True)

    def argv(self):
        return fence.bwrap_argv(self.st, str(self.ws), home=str(self.home), runtime="",
                                environ={"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin")})

    @unittest.skipUnless(sys.platform.startswith("linux"), "bubblewrap layout")
    def test_trees_read_only_local_bin_writable(self):
        a = self.argv()
        self.assertEqual(a[1:4], ["--dev-bind", "/", "/"])
        ro = [a[i + 2] for i, x in enumerate(a) if x == "--ro-bind"]
        for n in ("claude", "codex"):
            self.assertIn(os.path.realpath(self.home / f".local/share/mise/installs/{n}"), ro, n)
        self.assertFalse(any(str(self.home / ".local/bin").startswith(r.rstrip("/") + "/") or r == str(self.home / ".local/bin")
                             for r in ro), "~/.local/bin stays writable (pip / uv / cargo installers)")
        binds = [a[i + 1] for i, x in enumerate(a) if x == "--bind"]
        self.assertIn(os.path.realpath(self.home / ".local/share/mise/installs"), binds, "the way to the tree is pinned")
        at = min(i for i, x in enumerate(a) if x == "--ro-bind" and a[i + 2] == self.tree)
        later = [a[i + 2] for i, x in enumerate(a) if i > at and x in ("--bind", "--tmpfs", "--dev-bind")]
        self.assertFalse([p for p in later if p == self.tree or self.tree.startswith(p.rstrip("/") + "/")
                          or p.startswith(self.tree + "/")], "nothing mounted over the read-only tree afterwards")

    def test_code_ro_is_empty_for_a_source_checkout(self):
        self.assertEqual(fence.code_ro(str(self.home)), [], "tests run from the checkout: it stays writable")

    def test_code_ro_for_an_installed_venv(self):
        venv = self.home / ".local/share/uv/tools/agentj"
        pkg = venv / "lib/python3.13/site-packages/agentj"
        pkg.mkdir(parents=True)
        base = self.home / ".local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu"
        base.mkdir(parents=True)
        with mock.patch.object(fence, "__file__", str(pkg / "fence.py")), mock.patch.object(sys, "prefix", str(venv)), \
                mock.patch.object(sys, "base_prefix", str(base)):
            self.assertEqual(fence.code_ro(str(self.home)), sorted([os.path.realpath(venv), os.path.realpath(base.parent)]))

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bwrap"), "real bubblewrap")
    def test_real_bwrap_cannot_write_or_rename_the_tree(self):
        a = self.argv()
        if subprocess.run(a + ["/bin/sh", "-c", "exit 0"], capture_output=True, timeout=20).returncode != 0:
            self.skipTest("bwrap cannot start here")
        f, local = os.path.join(self.tree, "2.1.316", "claude"), self.home / ".local/bin"
        script = (f'echo x >> "{f}" 2>/dev/null && echo W1; mv "{self.tree}" "{self.tree}.x" 2>/dev/null && echo W2; '
                  f'mv "{self.home}/.local/share/mise" "{self.home}/.local/share/m2" 2>/dev/null && echo W3; '
                  f'touch "{local}/ok" && echo W4')
        r = subprocess.run(a + ["/bin/sh", "-c", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.split(), ["W4"], r.stderr)
        self.assertTrue((local / "ok").exists())
        self.assertEqual(pathlib.Path(f).read_bytes(), b"\x7fELF claude 2.1.316")


class SelfUpdate(unittest.TestCase):
    def test_note_texts(self):
        for line in ("Auto-update failed: EROFS: read-only file system, open '/h/.local/share/claude/versions/2.1.317'",
                     "Error: EROFS: read-only file system, mkdir '/h/.local/share/mise/installs/claude/2.1.317'",
                     "opencode upgrade: permission denied"):
            self.assertIn("在隔离环境里不能自更新", fence.self_update_note("claude", line) or "", line)
        self.assertIn("/update", fence.self_update_note("opencode", "self-update ... EACCES", "en"))
        self.assertIsNone(fence.self_update_note("claude", "Error: rate limit"))

    def test_fenced_agent_tells_the_owner_once(self):
        from agentj.agent import ClaudeAgent
        notes, logs = [], []
        host = types.SimpleNamespace(lang="zh", agent_notice=notes.append, st=types.SimpleNamespace(log=lambda *a, **k: logs.append(a)))
        lines = [b"Checking for updates\n", b"Auto-update failed: EROFS: read-only file system, rename x\n", b"again EROFS update\n", b""]

        class Err:
            async def readline(self):
                return lines.pop(0)
        for fenced, want in ((True, 1), (False, 0)):
            notes.clear()
            lines[:] = [b"Auto-update failed: EROFS: read-only file system, rename x\n", b"update failed EROFS\n", b""]
            me = types.SimpleNamespace(cfg={"fence": fenced}, kind="claude", host=host, err_tail="")
            asyncio.run(ClaudeAgent._drain_err(me, types.SimpleNamespace(stderr=Err())))
            self.assertEqual(len(notes), want, fenced)
        self.assertIn("Claude Code", notes[0] if notes else "在隔离环境里不能自更新 Claude Code")


@unittest.skipUnless(sh.LINUX, "launch evidence is read from /proc and cgroup v2")
class Dispatch469(sh.SameHost):
    """#469 regression on the real Host.ask: today's exact command shape, bare words, no card in either tier."""

    def setUp(self):
        super().setUp()
        self.f = Fixture().build(self, tempfile.mkdtemp(dir=self.tmp.name))
        self.f.ws = self.work
        (self.work / ".jarvis").mkdir(exist_ok=True)
        (self.k.proc / "900" / "environ").write_bytes(b"PATH=" + self.f.caller.encode() + b"\0")   # the Herdr session
        for p in (mock.patch.object(peer_origin, "_PROC", self.k.proc),
                  mock.patch.object(peer_origin, "manager_env", lambda: dict(self.f.unit))):
            p.start()
            self.addCleanup(p.stop)

    def ask_card(self, command):
        """An owner turn whose card (if any) is captured, then allowed by the paired phone."""
        async def go():
            self.native.cur_send = Send(self.phone.did, "开工", 0, source=provenance.phone())
            task = asyncio.ensure_future(self.host.ask("Bash", {"command": command}, batch=False))
            card = None
            for _ in range(200):
                await asyncio.sleep(0.01)
                if self.host.asks or task.done():
                    break
            if not task.done():
                card = next(iter(self.host.asks.values()))
                card.fut.set_result(("allow", self.phone.did, self.phone.pub, b"sig"))
            res = await task
            self.native.cur_send = None
            return res, card
        return asyncio.run(go())

    def test_owner_turn_today_command_no_card(self):
        self.harness()
        before = len(self.cards())
        res, card = self.ask_card(self.f.today())
        self.assertEqual((res["behavior"], card), ("allow", None))
        self.assertEqual(len(self.cards()), before, "no card at all")
        self.assertEqual(self.last()["cats"], [], "not 「改凭据」")
        self.assertIn("agentj-p469-ceo.service", json.loads((self.st.root / "busy" / "peer_launch.json").read_text())["units"])

    def test_same_host_today_command_no_card(self):
        self.owner_launch(self.f.today())
        self.away()
        self.peer_message(self.ceo_unit("agentj-p469-ceo")[1])
        self.assertEqual(provenance.unattended_turn(self.native), "same_host")
        before = len(self.cards())
        d, _ = self.permission(self.f.today("agentj-p470-ceo"))
        self.assertEqual((d.get("behavior"), self.last()["reason"], self.last()["cats"]), ("allow", "same_host", []))
        self.assertEqual(len(self.cards()), before)
        self.assertIn("agentj-p470-ceo.service", json.loads((self.st.root / "busy" / "peer_launch.json").read_text())["units"])

    def test_unproven_dispatch_card_names_class_and_why(self):
        self.harness()
        p = pathlib.Path(self.f.tree) / "2.1.316" / "claude"
        p.write_bytes(p.read_bytes() + b" swapped")
        res, card = self.ask_card(self.f.today())
        self.assertIsNotNone(card, "the pinned folder's claude is not the registered file → the card")
        self.assertTrue(card.require_passkey, "Face ID, as before")
        self.assertEqual(card.cats, ["opaque"])
        self.assertIn("不是主机登记的 claude", card.why)
        self.assertNotIn("credentials", approvals.read_log(self.st)[-1]["cats"])
        self.assertFalse((self.st.root / "busy" / "peer_launch.json").exists()
                         and "agentj-p469-ceo.service" in (self.st.root / "busy" / "peer_launch.json").read_text(),
                         "an unproven launch is not recorded")


def load_tests(loader, tests, pattern):   # Dispatch469 inherits SameHost's tests: run only its own here
    suite = unittest.TestSuite()
    for cls in (Registry, BusyRunLaunch, FenceBinds, SelfUpdate, Dispatch469):
        names = [n for n in loader.getTestCaseNames(cls) if cls is not Dispatch469 or n in Dispatch469.__dict__]
        suite.addTests(cls(n) for n in names)
    return suite


if __name__ == "__main__":
    unittest.main()
