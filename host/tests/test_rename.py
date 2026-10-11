"""0.10 rename (Agent Jarvis → Agent J): compatibility and migration.

- jarvis_host/__init__.py keeps the version literal (≤ 0.9 hosts read it by path) and equal to agentj's.
- AGENTJ_* env vars, with AGENTJARVIS_* read for one version cycle.
- the state move ~/.local/state/agentjarvis-alpha → ~/.local/state/agentj through the REAL CLI with a throw-away HOME:
  same channel, same devices, passphrase still verifies, symlink + record, URL defaults replaced (custom ones kept), refused
  while a serve answers on the old directory, rollback round trip, env override = no move.
- the wizard's work-folder state `.agentjarvis/` → `.agentj/`, and the old skill folder.
- the short command `aj` (temp PATH): installed only when no `aj` exists; never shadows; remove only our own link.
- the old service name: doctor warns, `service install` replaces it (real systemd, throw-away names, temp state).
- the fence hides the state directory under the legacy path too (bwrap for real; SBPL rules).
Every test sets HOME (or AGENTJ_STATE_DIR) to a temp directory: the real ~/.local/state is never touched.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import contextlib
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
import agentj  # noqa: E402
from agentj import alias, cloud, envcompat, fence, gate, migrate, service, state, wizard  # noqa: E402
from agentj.state import State  # noqa: E402

PASS = "correct horse battery"


def _clean_env(home: str, **extra) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTJ_", "AGENTJARVIS_"))}
    e.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"), **extra})
    return e


def _cli(home: str, *args, env=None, timeout=60):
    return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env={**_clean_env(home), **(env or {})},
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


class Version(unittest.TestCase):
    def test_the_old_path_still_tells_old_hosts_the_version(self):
        import jarvis_host
        self.assertEqual(jarvis_host.__version__, agentj.__version__)
        text = (HOST / "jarvis_host" / "__init__.py").read_text()
        # the exact regex a 0.9 host's update check uses on that file
        m = re.search(r'^__version__\s*=\s*["\']([0-9A-Za-z.+-]{1,32})["\']\s*$', text, re.M)
        self.assertEqual(m.group(1), agentj.__version__)

    def test_jarvis_compat_entry(self):
        r = subprocess.run([sys.executable, "-m", "jarvis_host.cli", "--version"], cwd=HOST, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), f"agentj {agentj.__version__}")
        self.assertIn("`jarvis` 已改名为 `agentj`", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, "one notice line")
        r = subprocess.run([str(HOST / "jarvis"), "--version"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), f"agentj {agentj.__version__}", r.stderr)
        self.assertIn("`jarvis` is now `agentj`", r.stderr)
        r = subprocess.run([str(HOST / "bin" / "agentj"), "--version"], capture_output=True, text=True)
        self.assertEqual((r.stdout.strip(), r.stderr), (f"agentj {agentj.__version__}", ""))


class Env(unittest.TestCase):
    def test_new_name_wins_old_name_is_read(self):
        e = {"AGENTJARVIS_API_URL": "https://old.example"}
        self.assertEqual(envcompat.getenv("AGENTJ_API_URL", environ=e), "https://old.example")
        e["AGENTJ_API_URL"] = "https://new.example"
        self.assertEqual(envcompat.getenv("AGENTJ_API_URL", environ=e), "https://new.example")
        self.assertEqual(envcompat.getenv("AGENTJ_X", "d", environ={"AGENTJ_X": ""}), "d", "empty = unset")
        self.assertEqual(envcompat.getenv("OTHER", environ={"OTHER": "1", "AGENTJARVIS_OTHER": "2"}), "1")
        self.assertIsNone(envcompat.getenv("OTHER", environ={"AGENTJARVIS_OTHER": "2"}), "only our own names fall back")

    def test_legacy_names_reach_every_reader(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENTJARVIS_STATE_DIR": d + "/s",
                                                                             "AGENTJARVIS_API_URL": "https://x.example",
                                                                             "AGENTJARVIS_SERVICE_NAME": "agentj-test-x",
                                                                             "AGENTJARVIS_TEST_PAIR_TTL": "1"}):
            for k in ("AGENTJ_STATE_DIR", "AGENTJ_API_URL", "AGENTJ_SERVICE_NAME"):
                os.environ.pop(k, None)
            self.assertEqual(state.state_dir(), pathlib.Path(d + "/s"))
            st = State()
            st.init(relay="ws://127.0.0.1:1")
            self.assertEqual(cloud.api_url(st), "https://x.example")
            self.assertEqual(service.name(), "agentj-test-x")
            from agentj import serve
            self.assertEqual(serve._ttl("AGENTJ_TEST_PAIR_TTL", 300), 1)
            env, _ = service.service_env()
            self.assertEqual(env.get("AGENTJ_STATE_DIR"), d + "/s", "the unit gets the NEW name")
            self.assertNotIn("AGENTJARVIS_STATE_DIR", env)

    def test_children_get_the_new_names(self):
        from agentj import serve
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            h = serve.Host(st, events="quiet", read_stdin=False)
            self.assertEqual(sorted(h.new_perm_env()), ["AGENTJ_PERM_SOCK", "AGENTJ_PERM_TOKEN", "AGENTJ_PERM_WAIT"])
        self.assertIn("AGENTJARVIS_STATE_DIR", fence._HIDE_ENV, "the fenced Agent does not learn the state path either way")


def _old_state(home: str, relay=state.OLD_DEFAULT_RELAY, web=state.OLD_DEFAULT_WEB, api=migrate.OLD_API) -> dict:
    """A 0.9-shaped state directory at the old path: keys, 2 devices, passphrase, config with the old defaults, Dashboard link."""
    st = State(state.legacy_state(home))
    cfg = st.init(relay=relay, web=web)
    for i in (1, 2):
        st.add_device(bytes([i]) * 32, f"phone {i}", sign_pub=bytes([i + 10]) * 32)
    gate.set_passphrase(st, PASS)
    st.write_private(st.cloud_path, json.dumps({"api": api, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                                "linked_at": 1, "last_seq": 3}).encode())
    st.log("serve_start", channel=cfg["channel"])
    return {"channel": cfg["channel"], "devices": st.devices(), "key": st.ed25519_path.read_bytes(),
            "log": st.log_path.read_bytes()}


class _Listener:
    """Something answering on <dir>/control.sock, like a running serve."""

    def __init__(self, d):
        self.path = os.path.join(str(d), "control.sock")
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(self.path)
        self.s.listen(8)

    def close(self):
        self.s.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)


class StateMove(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-home-", dir="/tmp")
        self.old, self.new = state.legacy_state(self.home), state.default_state(self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_upgrade_keeps_everything(self):
        before = _old_state(self.home)
        # P127: the owner's answer (a terminal asks y/N; without one the move needs --migrate-legacy, see test_p127_setup)
        r = _cli(self.home, "--migrate-legacy", "devices", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(json.loads(r.stdout)), sorted(before["devices"]), "stdout is pure JSON (notices go to stderr)")
        self.assertIn("~/.local/state/agentj", r.stderr)
        self.assertTrue(self.old.is_symlink() and os.path.realpath(self.old) == os.path.realpath(self.new))
        self.assertEqual(os.readlink(self.old), "agentj", "relative link: survives a moved home")
        self.assertEqual(self.new.stat().st_mode & 0o777, 0o700)
        st = State(self.new)
        st.check_perms()
        self.assertEqual(st.config()["channel"], before["channel"])
        self.assertEqual(st.devices(), before["devices"])
        self.assertEqual(st.ed25519_path.read_bytes(), before["key"])
        self.assertTrue(st.log_path.read_bytes().startswith(before["log"]))
        gate.verify(st, PASS)            # raises unless the approval passphrase still verifies
        with self.assertRaises(gate.GateError):
            gate.verify(st, "wrong one")
        rec = json.loads(st.migrated_path.read_text())
        self.assertEqual((rec["migrated_from"], rec["version"], rec["symlink"]), (str(self.old), agentj.__version__, True))
        cfg = st.config()
        self.assertEqual((cfg["relay"], cfg["web"]), (state.DEFAULT_RELAY, state.DEFAULT_WEB))
        self.assertEqual(json.loads(st.cloud_path.read_text())["api"], cloud.DEFAULT_API)
        self.assertEqual(cloud.read_cloud(st)["tenant"]["slug"], "acme-co", "the Dashboard link survives")
        self.assertEqual(rec["urls"], {"config.relay": state.OLD_DEFAULT_RELAY, "config.web": state.OLD_DEFAULT_WEB,
                                       "cloud.api": migrate.OLD_API})
        r = _cli(self.home, "status")
        self.assertIn(before["channel"], r.stdout)
        self.assertNotIn("搬到", r.stderr, "only once")
        # an old (rolled back) binary looking at the old path finds everything through the link
        self.assertEqual(State(self.old).devices(), before["devices"])

    def test_custom_urls_are_left_alone(self):
        _old_state(self.home, relay="ws://127.0.0.1:9", web="https://web.example", api="https://api.example")
        self.assertEqual(_cli(self.home, "--migrate-legacy", "status").returncode, 0)
        st = State(self.new)
        self.assertEqual((st.config()["relay"], st.config()["web"]), ("ws://127.0.0.1:9", "https://web.example"))
        self.assertEqual(json.loads(st.cloud_path.read_text())["api"], "https://api.example")
        self.assertNotIn("urls", json.loads(st.migrated_path.read_text()))

    def test_refused_while_a_serve_runs_on_the_old_directory(self):
        before = _old_state(self.home)
        lis = _Listener(self.old)
        try:
            r = _cli(self.home, "devices", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("serve", r.stderr)
            self.assertEqual(r.stderr.count("旧状态目录"), 1, "said once")
            self.assertTrue(self.old.is_dir() and not self.old.is_symlink(), "the old directory stays in use")
            self.assertFalse(os.path.lexists(self.new))
            self.assertEqual(len(json.loads(r.stdout)), 2, "commands keep working on the old directory")
            d = json.loads(_cli(self.home, "migrate", "status", "--json").stdout)
            self.assertEqual(d["pending"], "serve_running")
        finally:
            lis.close()
        r = _cli(self.home, "--migrate-legacy", "status")
        self.assertTrue(self.old.is_symlink())
        self.assertIn(before["channel"], r.stdout)

    def test_rollback_round_trip(self):
        before = _old_state(self.home)
        _cli(self.home, "--migrate-legacy", "status")
        lis = _Listener(self.new)
        try:
            r = _cli(self.home, "migrate", "rollback")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("serve", r.stderr)
            self.assertTrue(self.old.is_symlink(), "nothing moved while a serve runs")
        finally:
            lis.close()
        r = _cli(self.home, "migrate", "rollback")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
        self.assertFalse(os.path.lexists(self.new))
        st = State(self.old)
        self.assertEqual((st.config()["relay"], st.config()["web"]), (state.OLD_DEFAULT_RELAY, state.OLD_DEFAULT_WEB))
        self.assertEqual(json.loads(st.cloud_path.read_text())["api"], migrate.OLD_API)
        self.assertFalse(st.migrated_path.exists())
        self.assertEqual((st.devices(), st.config()["channel"]), (before["devices"], before["channel"]))
        self.assertNotEqual(_cli(self.home, "migrate", "rollback").returncode, 0, "nothing left to roll back")
        _cli(self.home, "--migrate-legacy", "status")       # moved again on the owner's answer
        self.assertTrue(self.old.is_symlink())
        self.assertEqual(State(self.new).devices(), before["devices"])

    def test_env_override_means_no_move(self):
        _old_state(self.home)
        other = os.path.join(self.home, "elsewhere")
        for var in ("AGENTJ_STATE_DIR", "AGENTJARVIS_STATE_DIR"):
            r = _cli(self.home, "init", env={var: other})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(self.old.is_dir() and not self.old.is_symlink(), var)
            self.assertFalse(os.path.lexists(self.new), var)
            shutil.rmtree(other)
        r = _cli(self.home, "devices", "--json", env={"AGENTJARVIS_STATE_DIR": str(self.old)})
        self.assertEqual(len(json.loads(r.stdout)), 2, "the old variable still points at the old directory")
        self.assertFalse(os.path.lexists(self.new))

    def test_fresh_install_and_both_present(self):
        r = _cli(self.home, "init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.new / "config.json").exists())
        self.assertFalse(os.path.lexists(self.old))
        self.assertEqual(State(self.new).config()["relay"], state.DEFAULT_RELAY)
        # an old directory appearing next to an existing new one is never merged or moved
        _old_state(self.home)
        _cli(self.home, "status")
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())


class Wizard(unittest.TestCase):
    def test_legacy_folder_read_then_renamed_on_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / ".agentjarvis"
            old.mkdir()
            (old / "wizard-manifest.json").write_text(json.dumps({"v": 1, "files": {"x.md": {"sha256": "0" * 64}}, "harness": ["codex"]}))
            self.assertEqual(wizard.read_manifest(root)["harness"], ["codex"], "read the old one while there is no new one")
            self.assertFalse((root / ".agentj").exists(), "reading moves nothing")
            # a ≤ 0.9 install of the skill, untouched since, plus one file the customer edited
            files = {f".claude/skills/{wizard.LEGACY_SKILL_NAME}/SKILL.md": b"old skill\n",
                     f".claude/skills/{wizard.LEGACY_SKILL_NAME}/references/files.md": b"old ref\n"}
            m = wizard.read_manifest(root)
            for rel, data in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
                m["files"][rel] = {"sha256": wizard.sha256(data), "source": "skill", "at": 1}
            (old / "wizard-manifest.json").write_text(json.dumps(m))
            (root / f".claude/skills/{wizard.LEGACY_SKILL_NAME}/references/files.md").write_bytes(b"edited by the human\n")
            rows = wizard.install(root, ["claude"])
            self.assertTrue((root / ".agentj" / "wizard-manifest.json").exists())
            self.assertFalse(old.exists(), "renamed, not copied")
            self.assertTrue((root / f".claude/skills/{wizard.SKILL_NAME}/SKILL.md").exists())
            self.assertFalse((root / f".claude/skills/{wizard.LEGACY_SKILL_NAME}/SKILL.md").exists(), "our untouched old copy goes")
            self.assertTrue((root / f".claude/skills/{wizard.LEGACY_SKILL_NAME}/references/files.md").exists(), "an edit stays")
            self.assertIn({"path": f".claude/skills/{wizard.LEGACY_SKILL_NAME}/SKILL.md", "status": "removed"}, rows)
            m = wizard.read_manifest(root)
            self.assertEqual(m["harness"], ["claude", "codex"])
            self.assertNotIn(f".claude/skills/{wizard.LEGACY_SKILL_NAME}/SKILL.md", m["files"])

    def test_staging_written_by_an_old_skill(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            stg = root / ".agentjarvis" / "wizard-staging"
            (stg / "documentation").mkdir(parents=True)
            (stg / "CLAUDE.md").write_text("# entry\n")
            rows = wizard.apply(root, root / wizard.STAGING_REL)
            self.assertEqual([r["path"] for r in rows], ["CLAUDE.md"])
            self.assertTrue((root / "CLAUDE.md").exists())
            self.assertFalse((root / ".agentjarvis").exists())

    def test_a_symlinked_legacy_folder_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as elsewhere:
            root = pathlib.Path(d)
            os.symlink(elsewhere, root / ".agentjarvis")
            wizard.install(root, ["claude"])
            self.assertTrue((root / ".agentj").is_dir() and not (root / ".agentj").is_symlink())
            self.assertEqual(os.listdir(elsewhere), [])


class Alias(unittest.TestCase):
    """A fake installed layout: <tmp>/venv/bin/{python,agentj}, a bin dir with agentj → venv, PATH only what we put there."""

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp(prefix="aj-alias-", dir="/tmp"))
        vb = self.t / "venv" / "bin"
        vb.mkdir(parents=True)
        for n in ("python", "agentj"):
            (vb / n).write_text("#!/bin/sh\n")
            (vb / n).chmod(0o755)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        os.symlink(vb / "agentj", self.bin / "agentj")
        self.other = self.t / "usr-bin"
        self.other.mkdir()
        self.patches = [mock.patch.object(alias.sys, "executable", str(vb / "python")),
                        mock.patch("agentj.agent.source_root", return_value=None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.t, ignore_errors=True)

    def path(self, *dirs):
        return os.pathsep.join(str(d) for d in dirs)

    def test_installs_only_when_free(self):
        path = self.path(self.bin, self.other)
        self.assertEqual(alias.status(path)["state"], "absent")
        r = alias.install(path)
        self.assertTrue(r["changed"])
        link = self.bin / "aj"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(link), os.path.realpath(self.t / "venv" / "bin" / "agentj"))
        self.assertEqual(alias.status(path)["state"], "installed")
        self.assertFalse(alias.install(path)["changed"], "idempotent")
        self.assertTrue(alias.remove(path)["changed"])
        self.assertFalse(os.path.lexists(link))

    def test_never_shadows_an_existing_aj(self):
        foreign = self.other / "aj"
        foreign.write_text("#!/bin/sh\necho theirs\n")
        foreign.chmod(0o755)
        for path in (self.path(self.bin, self.other), self.path(self.other, self.bin)):   # before or after us on PATH
            r = alias.install(path)
            self.assertEqual((r["state"], r["changed"]), ("taken", False))
            self.assertFalse(os.path.lexists(self.bin / "aj"))
            self.assertIn(f"`aj` 已被 {foreign} 占用，没有装短命令；继续用 agentj", alias.line(r))
            self.assertFalse(alias.remove(path)["changed"], "remove never deletes what is not ours")
            self.assertTrue(foreign.exists())

    def test_a_foreign_link_in_our_bin_is_not_removed(self):
        os.symlink("/bin/true", self.bin / "aj")
        path = self.path(self.bin)
        self.assertEqual(alias.status(path)["state"], "taken")
        self.assertFalse(alias.remove(path)["changed"])
        self.assertTrue(os.path.islink(self.bin / "aj"))

    def test_not_on_path_not_writable_checkout(self):
        r = alias.install(self.path(self.other))
        self.assertEqual(r["state"], "no_agentj")
        self.assertFalse(r["changed"])
        if os.geteuid() != 0:
            self.bin.chmod(0o555)
            try:
                r = alias.install(self.path(self.bin))
                self.assertEqual(r["why"], "not_writable")
            finally:
                self.bin.chmod(0o755)
        with mock.patch("agentj.agent.source_root", return_value=str(HOST)):
            r = alias.install(self.path(self.bin))
            self.assertEqual((r["state"], r["changed"]), ("checkout", False))
            self.assertIn("源码", alias.line(r))
        self.assertFalse(os.path.lexists(self.bin / "aj"))

    def test_jarvis_only_on_a_migrated_computer_and_never_shadowing(self):
        path = self.path(self.bin, self.other)
        with mock.patch.object(alias, "migrated", return_value=False):
            rs = alias.install_all(path)
            self.assertEqual([r["name"] for r in rs], ["aj"], "a fresh install never gets `jarvis`")
            self.assertFalse(os.path.lexists(self.bin / "jarvis"))
            self.assertEqual([r["name"] for r in alias.status_all(path)], ["aj"])
        with mock.patch.object(alias, "migrated", return_value=True):
            rs = alias.install_all(path)
            self.assertEqual([(r["name"], r["state"]) for r in rs], [("aj", "installed"), ("jarvis", "installed")])
            self.assertEqual(os.path.realpath(self.bin / "jarvis"), os.path.realpath(self.t / "venv" / "bin" / "agentj"))
            self.assertIn("下个版本移除", alias.line(rs[1]))
            self.assertEqual([r["name"] for r in alias.remove_all(path) if r["changed"]], ["aj", "jarvis"])
            self.assertFalse(os.path.lexists(self.bin / "jarvis"))
            foreign = self.other / "jarvis"           # somebody else's jarvis anywhere on PATH: never ours to replace
            foreign.write_text("#!/bin/sh\n")
            foreign.chmod(0o755)
            r = alias.install_all(path)[1]
            self.assertEqual((r["state"], r["changed"]), ("taken", False))
            self.assertIn(f"`jarvis` 已被 {foreign} 占用", alias.line(r))
            self.assertFalse(os.path.lexists(self.bin / "jarvis"))
            self.assertFalse(alias.remove(path, "jarvis")["changed"])
            self.assertTrue(foreign.exists())

    def test_cli_notice_when_invoked_as_jarvis(self):
        from agentj import cli
        err = __import__("io").StringIO()
        with mock.patch.object(cli.sys, "argv", ["/x/bin/jarvis", "--version"]), mock.patch.object(cli.sys, "stderr", err), \
                mock.patch.object(cli.sys, "stdout", __import__("io").StringIO()), self.assertRaises(SystemExit):
            cli.main()
        self.assertIn("`jarvis` is now `agentj`", err.getvalue())
        err = __import__("io").StringIO()
        with mock.patch.object(cli.sys, "argv", ["/x/bin/agentj", "--version"]), mock.patch.object(cli.sys, "stderr", err), \
                mock.patch.object(cli.sys, "stdout", __import__("io").StringIO()), self.assertRaises(SystemExit):
            cli.main()
        self.assertNotIn("is now `agentj`", err.getvalue())   # only the notice matters: GC may print an unrelated ResourceWarning here

    def test_doctor_row(self):
        from agentj import doctor
        with mock.patch.dict(os.environ, {"PATH": self.path(self.bin)}):
            self.assertEqual(doctor.check_alias()["status"], doctor.WARN)
            alias.install()
            row = doctor.check_alias()
            self.assertEqual((row["id"], row["status"]), ("alias", doctor.OK))
            self.assertIn("aj → agentj", row["summary"])
            self.assertNotIn("jarvis", row["summary"])
            with mock.patch.object(alias, "migrated", return_value=True):
                alias.install_all()
                row = doctor.check_alias()
                self.assertEqual(row["status"], doctor.OK)
                self.assertIn("jarvis → agentj", row["summary"], "the row reports both names")


class StatusName(unittest.TestCase):
    """PROTOCOL §8 status carries the Agent name; a rename (terminal / admin page via the control socket, or a Dashboard
    rename adopted in sync) sends a fresh status to every ready phone."""

    def test_name_in_status_and_on_every_change(self):
        import asyncio
        from test_l1 import Phone, _host, _ready
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            sent = []
            host = _host(st, sent)
            ph = Phone(st)

            class W:
                def write(self, b): pass
                async def drain(self): pass
                def close(self): pass
                async def wait_closed(self): pass
                def get_extra_info(self, *a): return None

            async def go():
                s = _ready(host, ph)
                await host.on_ready(s, 0)
                st.set_agent_name("小森")                         # `agentj name` / admin page → names.announce → ctl
                r = asyncio.StreamReader()
                r.feed_data(b'{"cmd":"agent_name_changed"}\n')
                r.feed_eof()
                await host.on_ctl(r, W())
                await asyncio.sleep(0.05)
                host.adopt_name(("ok", "Assistant One"))         # a Dashboard rename adopted in sync
                await asyncio.sleep(0.05)
            asyncio.run(go())
            names_ = [o["name"] for _, o in sent if o["t"] == "status"]
            self.assertEqual(names_, [None, "小森", "Assistant One"])


class Update(unittest.TestCase):
    def test_a_tool_installed_under_the_old_name_is_reinstalled(self):
        from agentj import update
        with tempfile.TemporaryDirectory() as d:
            pre = pathlib.Path(d) / "agentjarvis-host"
            pre.mkdir()
            (pre / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "agentjarvis-host", git = '
                                                 '"https://github.com/preangelleo/agentjarvis?subdirectory=host" }]\n')
            k = update.install_kind(str(pre))
            self.assertEqual((k["kind"], k["legacy"]), ("uv", True))
            c = update.commands(k)
            self.assertEqual([x[1:] for x in c], [["tool", "uninstall", "agentjarvis-host"],
                                                  ["tool", "install", "git+https://github.com/preangelleo/agentj#subdirectory=host"]])
            self.assertIn(" && ", update.command_text(k))
            new = pathlib.Path(d) / "agentj"
            new.mkdir()
            (new / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "agentj" }]\n')
            k = update.install_kind(str(new))
            self.assertEqual((k["legacy"], [x[1:] for x in update.commands(k)]), (False, [["tool", "upgrade", "agentj"]]))
            px = pathlib.Path(d) / "px"
            px.mkdir()
            (px / "pipx_metadata.json").write_text(json.dumps({"main_package": {
                "package": "agentjarvis-host", "package_or_url": "git+https://github.com/preangelleo/agentjarvis#subdirectory=host"}}))
            k = update.install_kind(str(px))
            self.assertEqual([x[1:] for x in update.commands(k)], [["uninstall", "agentjarvis-host"], ["install", update.SPEC]])
        self.assertEqual(update.LATEST_URL, "https://raw.githubusercontent.com/preangelleo/agentj/main/host/agentj/__init__.py")
        self.assertIn("agentj update apply", update.notice_text("9.9.9"))
        self.assertIn("Agent J", update.notice_text("9.9.9"))


class ServiceName(unittest.TestCase):
    def test_legacy_name_only_with_the_default_name(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTJ_SERVICE", "AGENTJARVIS_SERVICE"))}
        with mock.patch.dict(os.environ, env, clear=True):
            if service.platform() == "linux":
                self.assertEqual((service.name(), service.legacy_name()), ("agentj", "agentjarvis"))
            os.environ["AGENTJ_SERVICE_NAME"] = "agentj-test-1"
            self.assertIsNone(service.legacy_name(), "a test service never touches a real old one")
            os.environ["AGENTJ_SERVICE_LEGACY_NAME"] = "agentjarvis-test-old"
            self.assertEqual(service.legacy_name(), "agentjarvis-test-old")

    def test_doctor_warns_while_only_the_old_one_exists(self):
        from agentj import doctor
        row = doctor.check_service({"kind": "systemd", "name": "agentj", "installed": False},
                                   {"name": "agentjarvis", "installed": True, "active": "active"})
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("agentj service install", row["hint"])
        ok = doctor.check_service({"kind": "systemd", "name": "agentj", "installed": True, "active": "active"},
                                  {"name": "agentjarvis", "installed": False})
        self.assertEqual(ok["status"], doctor.OK)


def _user_systemd() -> bool:
    # A live user manager reads its real HOME; isolated tests must opt in explicitly.
    if os.environ.get("AGENTJ_TEST_SYSTEMD") != "1": return False
    if not sys.platform.startswith("linux") or not shutil.which("systemctl") or os.environ.get("AJ_SKIP_SYSTEMD"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(_user_systemd(), "no systemd user manager here")
class RealServiceRename(unittest.TestCase):
    """A real old-name unit (throw-away name, temp state dir, running `python -m jarvis_host.cli serve` like a 0.9 unit
    would after the upgrade) → `agentj service install` stops and deletes it and installs the new one."""

    def setUp(self):
        tag = secrets.token_hex(4)
        self.old_name, self.new_name = f"agentjarvis-test-old-{tag}", f"agentjarvis-test-new-{tag}"
        self.dir = tempfile.mkdtemp(prefix="aj-svc-", dir="/tmp")
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.env = {"AGENTJ_STATE_DIR": str(self.st.root), "AGENTJ_SERVICE_NAME": self.new_name,
                    "AGENTJ_SERVICE_LEGACY_NAME": self.old_name}
        ud = pathlib.Path(service.unit_dir())
        ud.mkdir(parents=True, exist_ok=True)
        self.units = [ud / f"{self.old_name}.service", ud / f"{self.new_name}.service"]
        self.units[0].write_text("[Service]\nType=simple\n"
                                 f'ExecStart="{sys.executable}" -P -m jarvis_host.cli serve --events quiet --no-stdin\n'
                                 f'Environment="PYTHONPATH={HOST}" "AGENTJ_STATE_DIR={self.st.root}"\n'
                                 "StandardInput=null\n[Install]\nWantedBy=default.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "--user", "start", f"{self.old_name}.service"], capture_output=True, timeout=30)

    def tearDown(self):
        for n, u in zip((self.old_name, self.new_name), self.units):
            subprocess.run(["systemctl", "--user", "disable", "--now", f"{n}.service"], capture_output=True, timeout=30)
            with contextlib.suppress(FileNotFoundError):
                u.unlink()
            subprocess.run(["systemctl", "--user", "reset-failed", f"{n}.service"], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=30)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _cli(self, *a):
        return subprocess.run([sys.executable, "-m", "agentj.cli", *a], cwd=HOST, env={**os.environ, **self.env},
                              capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)

    def _wait_serve(self) -> bool:
        from agentj import names
        deadline = time.time() + 20
        while time.time() < deadline:
            with contextlib.suppress(names.ServeBusy):
                if names.ctl_call(self.st, {"cmd": "status"}, 3) is not None:
                    return True
            time.sleep(0.2)
        return False

    def test_install_replaces_the_old_unit(self):
        self.assertTrue(self._wait_serve(), "the old-name unit runs serve through the jarvis_host shim")
        d = json.loads(self._cli("doctor", "--json", "--offline").stdout)
        row = {c["id"]: c for c in d["checks"]}["service"]
        self.assertEqual(row["status"], "warn")
        self.assertIn(self.old_name, row["summary"])
        r = self._cli("service", "install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.old_name, r.stdout)
        self.assertFalse(self.units[0].exists(), "old unit deleted")
        act = subprocess.run(["systemctl", "--user", "is-active", f"{self.old_name}.service"], capture_output=True,
                             text=True).stdout.strip()
        self.assertNotEqual(act, "active")
        self.assertTrue(self.units[1].exists())
        self.assertIn('"serve" "--events" "quiet"', self.units[1].read_text())
        self.assertTrue(self._wait_serve(), "the new unit serves the same state")
        d = json.loads(self._cli("doctor", "--json", "--offline").stdout)
        self.assertEqual({c["id"]: c for c in d["checks"]}["service"]["status"], "ok")


class FenceLegacyPath(unittest.TestCase):
    def _home(self, d):
        home = pathlib.Path(d) / "home"
        st = State(state.default_state(str(home)))
        st.init(relay="ws://127.0.0.1:1")
        st.perm_dir.mkdir(mode=0o700)
        os.symlink("agentj", state.legacy_state(str(home)))
        return home, st

    def test_sbpl_denies_the_legacy_path_too(self):
        with tempfile.TemporaryDirectory() as d:
            home, st = self._home(d)
            prof, params = fence.sbpl_profile(st, str(home / "work"), home=str(home), uid=501)
            alts = {v for k, v in params.items() if k.startswith("STATE_ALT_")}
            self.assertIn(str(state.legacy_state(str(home))), alts, "the link itself (literal path) is denied")
            lines = [x.strip() for x in prof.splitlines()]
            i = [n for n, x in enumerate(lines) if 'param "STATE_ALT_0"' in x]
            self.assertTrue(i and max(i) < lines.index('(allow file-read* file-write* (subpath (param "PERM")))'))
            # a REAL old directory next to the new one (a second identity) is denied as well
            os.unlink(state.legacy_state(str(home)))
            State(state.legacy_state(str(home))).init(relay="ws://127.0.0.1:1")
            _, params = fence.sbpl_profile(st, str(home / "work"), home=str(home), uid=501)
            self.assertIn(os.path.realpath(state.legacy_state(str(home))),
                          {v for k, v in params.items() if k.startswith("STATE_ALT_")})
            self.assertIn(os.path.realpath(state.legacy_state(str(home))), fence.protected_paths(st)
                          if str(home) == os.path.expanduser("~") else
                          [*fence.other_states(st, str(home)), *fence.protected_paths(st)])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bwrap"), "bubblewrap")
    def test_bwrap_hides_the_state_under_both_paths(self):
        with tempfile.TemporaryDirectory() as d:
            fence._probe_cache.clear()
            home, st = self._home(d)
            work = pathlib.Path(d) / "work"
            work.mkdir()
            if fence.problem(st, str(work)) is not None:
                self.skipTest("bubblewrap cannot start here")
            old_real = pathlib.Path(d) / "second"     # a real old directory elsewhere is covered by other_states too
            probe = ("import os, sys\nout = []\nfor p in sys.argv[1:]:\n"
                     "    try:\n        out.append(sorted(os.listdir(p)))\n    except OSError as e:\n        out.append(type(e).__name__)\n"
                     "    try:\n        open(os.path.join(p, 'host_ed25519.key'), 'rb').read(); out.append('KEY READ')\n"
                     "    except OSError:\n        out.append('no key')\nprint(out)\n")
            paths = [str(state.legacy_state(str(home))), str(state.default_state(str(home)))]
            argv = fence.bwrap_argv(st, str(work), home=str(home), runtime="") + [sys.executable, "-c", probe, *paths]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), str([["agentperm"], "no key", ["agentperm"], "no key"]))
            # the legacy path as a REAL directory beside the new one (not moved): hidden whole
            os.unlink(paths[0])
            State(pathlib.Path(paths[0])).init(relay="ws://127.0.0.1:1")
            argv = fence.bwrap_argv(st, str(work), home=str(home), runtime="") + [sys.executable, "-c", probe, *paths]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.stdout.strip(), str([[], "no key", ["agentperm"], "no key"]), r.stderr)
            del old_real
        fence._probe_cache.clear()


if __name__ == "__main__":
    unittest.main()
