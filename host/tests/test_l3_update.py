"""L3: upgrades the human decides (`agentj update`), the macOS fence (sandbox-exec), the cloud-server form.

- Update: version ordering, the latest version from a (local stand-in for the) public repo, network failure = "unknown"
  (never an error), install-kind detection (uv tool / pipx / checkout / pip) → the matching command, `apply` refused without
  an interactive terminal (no --yes exists), refused when agentj's files are read-only (inside the fence), the daily check
  (once per 24 h, persisted, one phone notice per version, switch off) and serve's loop delivering that notice to a phone.
- macOS fence as text (runs on Linux too): the SBPL profile denies the state dir (files + unix sockets) except agentperm/,
  keeps agentj's code / start-up files / LaunchAgents read-only, pins the directories above them, limits signals and process
  info to the sandbox, refuses launchd jobs / Apple Events / LaunchServices / tmux + launchd sockets; paths only as -D
  parameters. Linux: ancestors of protected paths are bind-pinned (a rename would let the Agent plant code).
- Doctor rows: update (offline / unreachable = !, never ✗), linger, the macOS fence row.
- Cloud form: the pairing QR picks Unicode / ANSI / plain ASCII by terminal; SSH sessions get the `--link` hint and the
  `ssh -L` tunnel line for `agentj admin`.
- Real macOS runs (skipped elsewhere): a LaunchAgent installed / running / removed under a throwaway label; opt-in
  (AJ_REAL_CLAUDE=1) the real Claude Code inside the sandbox through serve + the permission tool + a phone approval.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import contextlib
import http.server
import io
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
import agentj  # noqa: E402
from agentj import cli, doctor, fence, names, serve, service, update  # noqa: E402
from agentj.state import State  # noqa: E402


def _cli(*args, env=None, timeout=60, stdin=subprocess.DEVNULL):
    return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env={**os.environ, **(env or {})},
                          capture_output=True, text=True, timeout=timeout, stdin=stdin)


class _Repo:
    """A local stand-in for raw.githubusercontent.com: serves `body` with `code` at /host/agentj/__init__.py."""

    def __init__(self, body: str = "", code: int = 200):
        self.body, self.code, self.hits = body, code, 0
        repo = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                repo.hits += 1
                b = repo.body.encode()
                self.send_response(repo.code)
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/host/agentj/__init__.py"

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _init_text(v: str) -> str:
    return f'"""agentj host."""\n__version__ = "{v}"\nDIST = "agentj"\n'


def _closed_port_url() -> str:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/x"


# ------------------------------------------------------------------ update
class Versions(unittest.TestCase):
    def test_order(self):
        seq = ["0.7.0a1", "0.7.0", "0.8.0.dev1", "0.8.0a1", "0.8.0a2", "0.8.0b1", "0.8.0rc1", "0.8.0", "0.8.1", "0.10.0", "1.0"]
        for a, b in zip(seq, seq[1:]):
            self.assertEqual(update.compare(a, b), -1, (a, b))
            self.assertEqual(update.compare(b, a), 1, (b, a))
        self.assertEqual(update.compare("0.8.0a1", "0.8.0a1"), 0)
        self.assertEqual(update.compare("1.0", "1.0.0"), 0)
        for bad in ("", "latest", "0.8.0-beta", "v0.8.0", "0.8.0a"):
            self.assertIsNone(update.parse(bad), bad)
            self.assertIsNone(update.compare(bad, "0.8.0"))

    def test_version_is_this_round(self):
        self.assertEqual(agentj.__version__, "0.15.3a1")


class Fetch(unittest.TestCase):
    def test_latest_from_the_repo_file(self):
        with _Repo(_init_text("9.9.9")) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(update.fetch_latest(3), ("9.9.9", "ok"))
            res = update.check(3)
            self.assertEqual((res["status"], res["latest"], res["current"]), ("newer", "9.9.9", agentj.__version__))
        with _Repo(_init_text(agentj.__version__)) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(update.check(3)["status"], "current")
        with _Repo(_init_text("0.0.1")) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(update.check(3)["status"], "ahead")

    def test_failures_are_unknown_not_errors(self):
        with mock.patch.dict(os.environ, {update.URL_ENV: _closed_port_url()}):
            self.assertEqual(update.fetch_latest(2), (None, "network"))
            res = update.check(2)
            self.assertEqual((res["status"], res["why"]), ("unknown", "network"))
            self.assertTrue(res["command"])
        with _Repo("nope", code=404) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(update.fetch_latest(2), (None, "http_404"))
        with _Repo("__version__ = os.system('x')\n") as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(update.fetch_latest(2), (None, "unparsable"), "the file is parsed as text, never run")
        with _Repo('__version__ = "latest-and-greatest"\n') as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(update.fetch_latest(2)[1], "unparsable")
        for off in ("off", "http://example.com/x", "file:///etc/passwd"):     # plain http only to loopback; never a file
            with mock.patch.dict(os.environ, {update.URL_ENV: off}):
                self.assertEqual(update.fetch_latest(2), (None, "off"), off)
        self.assertTrue(update.LATEST_URL.startswith("https://raw.githubusercontent.com/preangelleo/agentj/main/host/"))


class InstallKind(unittest.TestCase):
    def test_kinds_and_commands(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "uv-receipt.toml").write_text("[tool]\n")
            k = update.install_kind(d)
            self.assertEqual(k["kind"], "uv")
            self.assertEqual(update.commands(k)[0][1:], ["tool", "upgrade", "agentj"])
        with tempfile.TemporaryDirectory() as d:
            spec = "git+file:///srv/mirror/agentj#subdirectory=host"
            (pathlib.Path(d) / "pipx_metadata.json").write_text(json.dumps({"main_package": {"package_or_url": spec}}))
            k = update.install_kind(d)
            self.assertEqual((k["kind"], update.commands(k)[0][1:]), ("pipx", ["install", "--force", spec]),
                             "pipx reinstalls from the spec it was installed from")
        with tempfile.TemporaryDirectory() as d:
            k = update.install_kind(d)       # this test runs from the source checkout: no installer files in that prefix
            self.assertEqual(k["kind"], "checkout")
            c = update.commands(k)
            self.assertEqual(c[0][:2] + c[0][3:], ["git", "-C", "pull", "--ff-only"])
            self.assertEqual(c[1][1:3], ["sync", "--project"])
            with mock.patch("agentj.agent.source_root", return_value=None):
                k = update.install_kind(d)
                self.assertEqual(k["kind"], "pip")
                self.assertEqual(update.commands(k)[0][-1], update.SPEC)
        self.assertIn(" && ", update.command_text({"kind": "checkout", "where": "/x y"}))
        self.assertIn("'/x y'", update.command_text({"kind": "checkout", "where": "/x y"}), "quoted for a shell")


class Apply(unittest.TestCase):
    def test_cli_noninteractive_with_fake_installer(self):
        from agentj import cli
        with mock.patch.object(update, "apply", return_value={"result":"ok", "reason":"upgraded", "from":"0.14.0a1", "to":"0.15.0a1", "service":"restarted", "exit":0}) as apply, mock.patch.object(update,"check",return_value={"status":"newer"}), mock.patch("sys.stdout",new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as e:
                cli.main(["update", "apply", "--yes"])
            self.assertEqual(e.exception.code,0)
            apply.assert_called_once()

    def test_preflight(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        with tempfile.TemporaryDirectory() as d:
            update.preflight(TTY(), TTY(), prefix=d)               # a human at a terminal, writable install: ok
            update.preflight(io.StringIO(), io.StringIO(), prefix=d)
            os.chmod(d, 0o500)
            try:
                if not os.access(d, os.W_OK):                         # (root ignores modes)
                    with self.assertRaises(update.Refused) as e:
                        update.preflight(TTY(), TTY(), prefix=d)
                    self.assertEqual(e.exception.reason, "read_only", "inside the fence the code is read-only")
            finally:
                os.chmod(d, 0o700)

    def test_unknown_and_current(self):
        res = _cli("update", "apply", env={update.URL_ENV: _closed_port_url()})
        self.assertEqual(res.returncode, 1)
        self.assertIn("UPGRADE_RESULT failed", res.stdout)
        with _Repo(_init_text(agentj.__version__)) as r:
            res = _cli("update", "apply", env={update.URL_ENV: r.url})
            self.assertEqual(res.returncode, 0)
            self.assertIn("UPGRADE_RESULT ok", res.stdout)

    def test_check_cli(self):
        with _Repo(_init_text("9.9.9")) as r:
            res = _cli("update", "check", "--json", env={update.URL_ENV: r.url})
            d = json.loads(res.stdout)
            self.assertEqual((d["status"], d["latest"], d["install"]), ("newer", "9.9.9", "checkout"))
            plain = _cli("update", "check", env={update.URL_ENV: r.url}).stdout
            self.assertIn("agentj update apply", plain)
            self.assertIn("git -C", plain)
        res = _cli("update", "check", "--json", env={update.URL_ENV: _closed_port_url()})
        self.assertEqual(res.returncode, 0, "offline is not an error")
        self.assertEqual(json.loads(res.stdout)["status"], "unknown")
        self.assertEqual(sorted(json.loads(res.stdout)), ["command", "current", "install", "latest", "status", "why"],
                         "--json shape unchanged")

    def test_check_explains_every_status_in_plain_words(self):
        """S-8 / B-2 (PROMPT-29): newer → tell your human, they run apply; current / ahead / unknown → nothing to do."""
        cases = [("9.9.9", "newer", ["Agent upgrades", "agentj update apply"]),
                 (agentj.__version__, "current", ["Up to date. Nothing to do."]),
                 ("0.0.1", "ahead", ["Not an error, nothing to do", "newest public release is 0.0.1"])]
        for ver, status, needles in cases:
            with _Repo(_init_text(ver)) as r:
                res = _cli("update", "check", env={update.URL_ENV: r.url})
                self.assertEqual(res.returncode, 0)
                self.assertIn(status, res.stdout)
                for n in needles:
                    self.assertIn(n, res.stdout, status)
                if status != "newer":
                    self.assertNotIn("it runs:", res.stdout, f"{status}: no upgrade command to act on")
        res = _cli("update", "check", env={update.URL_ENV: _closed_port_url()})
        self.assertEqual(res.returncode, 0)
        for n in ("unknown", "Not an error, nothing to do", "no network"):
            self.assertIn(n, res.stdout)

    def test_upgrade_names_the_new_tag(self):
        """A copy installed from a pinned tag (or our wheel) does not move with `uv tool upgrade`: install v<latest>."""
        self.assertEqual(update.spec_at("0.10.2a1"), "git+https://github.com/preangelleo/agentj@v0.10.2a1#subdirectory=host")
        self.assertEqual(update.spec_at("garbage"), update.SPEC)
        uv = update.commands({"kind": "uv", "where": "/x", "legacy": False}, "0.10.2a1")
        self.assertEqual(uv[0][1:], ["tool", "install", "--force", update.spec_at("0.10.2a1")])
        self.assertEqual(update.commands({"kind": "uv", "where": "/x", "legacy": False})[0][1:], ["tool", "upgrade", "agentj"])
        px = update.commands({"kind": "pipx", "where": "/x", "spec": "git+old@v0.10.1a1", "legacy": False}, "0.10.2a1")
        self.assertEqual(px[0][-1], update.spec_at("0.10.2a1"))
        self.assertEqual(update.commands({"kind": "pip", "where": "/x", "legacy": False}, "0.10.2a1")[0][-1],
                         update.spec_at("0.10.2a1"))
        with _Repo(_init_text("9.9.9")) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}), \
                mock.patch.object(update, "install_kind", return_value={"kind": "uv", "where": "/x", "legacy": False}):
            self.assertIn("@v9.9.9#subdirectory=host", update.check(3)["command"])


class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_once_a_day_once_per_version(self):
        calls = []

        def fetch(v):
            def f():
                calls.append(v)
                return v, "ok"
            return f
        t0 = 1_800_000_000
        with mock.patch.dict(os.environ, {update.URL_ENV: "https://example.invalid/x"}):
            note = update.daily(self.st, t0, fetch("9.9.9"))
            self.assertIn("9.9.9", note)
            self.assertIn("agentj update apply", note)
            self.assertIsNone(update.daily(self.st, t0 + 3600, fetch("9.9.9")))
            self.assertEqual(calls, ["9.9.9"], "within 24 h: no second request")
            self.assertIsNone(update.daily(self.st, t0 + update.DAY + 1, fetch("9.9.9")), "same version: told once")
            self.assertIn("9.9.10", update.daily(self.st, t0 + 2 * update.DAY + 2, fetch("9.9.10")))
            self.assertEqual(oct((self.st.root / "update.json").stat().st_mode & 0o777), "0o600")
            rec = update.read_rec(self.st)
            self.assertEqual(set(rec), {"checked", "latest", "why", "notified"}, "a version and a time, nothing else")
            self.assertEqual(_cli("update", "auto", "off", env={"AGENTJ_STATE_DIR": str(self.st.root)}).returncode, 0)
            self.assertFalse(update.auto_enabled(self.st))
            self.assertIsNone(update.daily(self.st, t0 + 9 * update.DAY, fetch("9.9.11")))
            self.assertEqual(len(calls), 3, "switched off: no request at all")

    def test_serve_tells_the_phone_and_installs_nothing(self):
        from test_l1 import Phone, _host, _ready
        sent = []
        host = _host(self.st, sent)
        ph = Phone(self.st)

        async def go():
            with mock.patch.object(serve, "UPDATE_FIRST", 0), mock.patch.object(serve, "UPDATE_WAKE", 3600), \
                    mock.patch.object(update, "daily", lambda st: update.notice_text("9.9.9")), \
                    mock.patch.object(update, "read_rec", lambda st: {"latest": "9.9.9"}):
                run = asyncio.create_task(host.run())
                _ready(host, ph)
                t0 = time.monotonic()
                while not any(o.get("from") == "notice" for _, o in sent) and time.monotonic() - t0 < 10:
                    await asyncio.sleep(0.02)
                host.stopping.set()
                await run
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(go())
        notes = [o for _, o in sent if o.get("t") == "msg" and o.get("from") == "notice"]
        self.assertEqual(len(notes), 1)
        self.assertIn("9.9.9", notes[0]["text"])
        self.assertIn('"ev": "update_available"', self.st.log_path.read_text())


# ------------------------------------------------------------------ macOS fence as text
class _St:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.perm_dir = self.root / "agentperm"


class Sbpl(unittest.TestCase):
    def test_profile_rules(self):
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d) / "Users" / "jane"
            (home / ".local" / "state" / "agentj" / "agentperm").mkdir(parents=True)
            (home / "Library" / "LaunchAgents").mkdir(parents=True)
            (home / ".zshrc").write_text("")
            st = _St(home / ".local" / "state" / "agentj")
            prof, params = fence.sbpl_profile(st, str(home / "work"), home=str(home), uid=501)
            lines = [x.strip() for x in prof.splitlines()]
            self.assertEqual(lines[:2], ["(version 1)", "(allow default)"])
            for rule in ('(deny file-read* file-write* (subpath (param "STATE")))',
                         '(deny network-outbound (remote unix-socket (subpath (param "STATE"))))',
                         '(allow file-read* file-write* (subpath (param "PERM")))',
                         '(allow network-outbound (remote unix-socket (subpath (param "PERM"))))',
                         "(deny signal)", "(allow signal (target same-sandbox))", "(deny process-info*)",
                         "(allow process-info* (target same-sandbox))", "(deny appleevent-send)",
                         "(deny lsopen)", '(deny network-outbound (remote unix-socket (subpath (param "TMUX"))))'):
                self.assertIn(rule, lines)
            # the permission folder is allowed back AFTER the state dir is denied (later rules win in SBPL)
            self.assertLess(lines.index('(deny file-read* file-write* (subpath (param "STATE")))'),
                            lines.index('(allow file-read* file-write* (subpath (param "PERM")))'))
            self.assertNotIn(str(home), prof, "paths only as -D parameters, never spliced into the rules")
            ro = {v for k, v in params.items() if k.startswith("RO_")}
            for p in (home / ".ssh" / "authorized_keys", home / ".Xauthority"):
                self.assertIn(os.path.realpath(p) if p.exists() else str(p), ro, f"{p} read-only (also when absent)")
            for p in (home / ".zshrc", home / ".zlogin", home / "Library" / "LaunchAgents"):   # F14: the owner's general config
                self.assertNotIn(os.path.realpath(p) if p.exists() else str(p), ro, f"F14: {p} writable")
            self.assertNotIn("(deny job-creation)", lines, "F14: launchd jobs are the Agent's own to manage")
            for c in fence.code_paths():
                self.assertNotIn(c, ro, "F14: agentj code is writable")
            pin = {v for k, v in params.items() if k.startswith("PIN_")}
            for a in (home / ".local", home / ".local" / "state"):
                self.assertIn(os.path.realpath(a), pin, f"{a} cannot be renamed away")
            self.assertEqual(params["STATE"], os.path.realpath(st.root))
            self.assertEqual(params["TMUX"], "/private/tmp/tmux-501")
            self.assertIn('(literal (param "PIN_0"))', prof)

    def test_control_sockets_rules(self):
        """G-A56 on macOS: herdr / screen / zellij / wezterm / emacs / nvim / Jupyter folders and (unless allowed) the
        container engines' sockets: no read, no write, no unix-socket connect; HERDR_* / DOCKER_HOST unset."""
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d) / "Users" / "jane"
            st = _St(home / ".local" / "state" / "agentj")
            env = {"TMPDIR": "/private/var/folders/ab/cd/T/", "USER": "jane", "HERDR_SOCKET_PATH": "/opt/h/herdr.sock",
                   "HERDR_PANE_ID": "3", "DOCKER_HOST": "unix:///x"}
            prof, params = fence.sbpl_profile(st, str(home / "work"), home=str(home), uid=501, environ=env)
            lines = [x.strip() for x in prof.splitlines()]
            ctl = {v for k, v in params.items() if k.startswith("CTL_")}
            r = os.path.realpath
            for p in (home / ".config" / "herdr", home / "Library" / "Application Support" / "herdr", home / ".screen",
                      home / ".local" / "share" / "wezterm", home / ".emacs.d" / "server", home / "Library" / "Jupyter" / "runtime",
                      home / ".docker" / "run", home / ".colima", home / ".orbstack" / "run"):
                self.assertIn(r(p), ctl, p)
            for p in ("/opt/h", "/private/tmp/uscreens", "/private/tmp/zellij-501", "/private/var/folders/ab/cd/T/zellij-501",
                      "/private/var/folders/ab/cd/T/nvim.jane", "/private/tmp/emacs501"):
                self.assertIn(r(p), ctl, p)
            self.assertTrue(any(v.endswith("/run/docker.sock") for v in ctl))
            i = lines.index("(deny network-outbound (remote unix-socket")
            self.assertIn('(subpath (param "CTL_0"))', lines[i + 1:i + 2])
            self.assertIn("(deny file-read* file-write*", lines)
            self.assertTrue(any("vscode-ipc-" in x and "kitty" in x for x in lines))
            self.assertNotIn(str(home), prof)
            # allowed: the container engine's sockets are not in the list, DOCKER_HOST is kept
            _, p2 = fence.sbpl_profile(st, str(home / "work"), home=str(home), uid=501, environ=env, allow_docker=True)
            c2 = {v for k, v in p2.items() if k.startswith("CTL_")}
            self.assertNotIn(r(home / ".docker" / "run"), c2)
            self.assertFalse(any(v.endswith("docker.sock") for v in c2))
            self.assertIn(r(home / ".config" / "herdr"), c2, "herdr stays hidden")
            with mock.patch.dict(os.environ, env, clear=False):
                a = fence.sandbox_argv(st, str(home / "work"), home=str(home), environ=env)
            unset = a[a.index("/usr/bin/env") + 1:][1::2]
            for k in ("HERDR_SOCKET_PATH", "HERDR_PANE_ID", "DOCKER_HOST"):
                self.assertIn(k, unset)

    def test_argv_and_wrap_on_darwin(self):
        with tempfile.TemporaryDirectory() as d:
            st = _St(pathlib.Path(d) / "s")
            st.perm_dir.mkdir(parents=True)
            a = fence.sandbox_argv(st, d)
            self.assertEqual(a[:2], [fence.SANDBOX_EXEC, "-p"])
            i = a.index("/usr/bin/env")
            self.assertTrue(all(x == "-D" for x in a[3:i:2]), "every path is a -D KEY=VALUE")
            unset = a[i + 1:]
            for k in ("TMUX", "SSH_AUTH_SOCK", "DISPLAY", "AGENTJ_STATE_DIR"):
                self.assertIn(k, unset[1::2])
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", unset[1::2])
            with mock.patch.object(sys, "platform", "darwin"):
                w = fence.wrap(st, ["claude", "-p"], d)
                self.assertEqual((w[0], w[-2:]), (fence.SANDBOX_EXEC, ["claude", "-p"]))
                self.assertEqual(fence.kind(), "sandbox-exec")
                fence._probe_cache.clear()
                with mock.patch.object(fence, "SANDBOX_EXEC", "/nonexistent/sandbox-exec"):
                    self.assertEqual(fence.problem(st, d), "no_sandbox_exec")
            with mock.patch.object(sys, "platform", "freebsd13"):
                self.assertEqual(fence.problem(st, d), "unsupported_os")
            self.assertIn("sandbox-exec", fence.REASONS["no_sandbox_exec"])


class Ancestors(unittest.TestCase):
    def test_pins_what_could_be_renamed(self):
        with tempfile.TemporaryDirectory() as d:
            deep = pathlib.Path(d) / "a" / "b" / "code"
            deep.mkdir(parents=True)
            got = fence.ancestors([str(deep)])
            self.assertIn(os.path.join(d, "a"), got)
            self.assertIn(os.path.join(d, "a", "b"), got)
            self.assertLess(got.index(os.path.join(d, "a")), got.index(os.path.join(d, "a", "b")), "parents first")
            self.assertNotIn("/", got)

    @unittest.skipUnless(sys.platform.startswith("linux"), "bubblewrap argv")
    def test_bwrap_binds_ancestors_before_the_read_only_code(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            a = fence.bwrap_argv(st, d)
            binds = [a[i + 1] for i, x in enumerate(a) if x == "--bind"]
            ro = [a[i + 1] for i, x in enumerate(a) if x == "--ro-bind"]
            self.assertFalse(set(fence.code_paths()) & set(ro), "F14: no code readonly bind")


# ------------------------------------------------------------------ doctor rows
class DoctorRows(unittest.TestCase):
    def test_update_row(self):
        with _Repo(_init_text("9.9.9")) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            c = doctor.check_update()
            self.assertEqual((c["id"], c["status"]), ("update", "warn"))
            self.assertIn("agentj update apply", c["hint"])
        with _Repo(_init_text(agentj.__version__)) as r, mock.patch.dict(os.environ, {update.URL_ENV: r.url}):
            self.assertEqual(doctor.check_update()["status"], "ok")
        with mock.patch.dict(os.environ, {update.URL_ENV: _closed_port_url()}):
            c = doctor.check_update()
            self.assertEqual(c["status"], "warn", "offline = !, never ✗")
            self.assertIn("could not check", c["summary"])
        self.assertEqual(doctor.check_update(offline=True)["status"], "warn")

    def test_linger_row(self):
        svc = {"kind": "systemd"}
        with mock.patch.object(service, "_linger", return_value="no"):
            c = doctor.check_linger(svc, {"SSH_CONNECTION": "203.0.113.5 50000 198.51.100.7 22"})
            self.assertEqual(c["status"], "warn")
            self.assertIn("enable-linger", c["hint"])
            self.assertEqual(doctor.check_linger(svc, {"WAYLAND_DISPLAY": "wayland-1"})["status"], "ok", "a desktop session")
            self.assertEqual(doctor.check_linger(svc, {})["status"], "warn", "no desktop, no SSH: a headless box")
        with mock.patch.object(service, "_linger", return_value="yes"):
            self.assertEqual(doctor.check_linger(svc, {})["status"], "ok")
        self.assertIsNone(doctor.check_linger({"kind": "launchd"}, {}))

    def test_container_hint(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(doctor, "_in_container", return_value=True), \
                mock.patch.object(doctor.shutil, "which", return_value="/usr/bin/bwrap"), \
                mock.patch.object(fence, "problem", return_value="bwrap_failed"), tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            c = doctor.check_fence(st)
            self.assertIn("container", c["summary"])
            self.assertIn("not inside Docker", c["hint"])


# ------------------------------------------------------------------ cloud server form
class CloudForm(unittest.TestCase):
    def test_qr_mode(self):
        class Out(io.StringIO):
            def __init__(self, enc, tty):
                super().__init__()
                self._enc, self._tty = enc, tty

            @property
            def encoding(self):
                return self._enc

            def isatty(self):
                return self._tty
        utf8 = {"TERM": "xterm", "LANG": "en_US.UTF-8"}
        self.assertEqual(cli._qr_mode(Out("utf-8", True), utf8), "compact")
        self.assertEqual(cli._qr_mode(Out("utf-8", True), {"TERM": "xterm", "LC_ALL": "C.utf8"}), "compact")
        self.assertEqual(cli._qr_mode(Out("ANSI_X3.4-1968", True), utf8), "ansi", "a stream that cannot carry Unicode")
        self.assertEqual(cli._qr_mode(Out("utf-8", True), {"TERM": "xterm", "LANG": "C"}), "ansi",
                         "SSH with LANG=C: Python still writes UTF-8 (PEP 538), the terminal may not show it")
        self.assertEqual(cli._qr_mode(Out("utf-8", True), {"TERM": "xterm"}), "ansi", "no locale at all (a bare container)")
        self.assertEqual(cli._qr_mode(Out("utf-8", True), {"TERM": "xterm", "LANG": "C", "LC_CTYPE": "C.UTF-8"}), "ansi",
                         "LC_CTYPE=C.UTF-8 set by Python's PEP 538 coercion is ignored")
        self.assertEqual(cli._qr_mode(Out("ascii", True), {"TERM": "dumb"}), "ascii")
        self.assertEqual(cli._qr_mode(Out("utf-8", False), {"TERM": "xterm"}), "ascii", "not a terminal, no UTF-8 locale")
        self.assertEqual(cli._qr_mode(Out("utf-8", True), {**utf8, "AGENTJ_QR_ASCII": "1"}), "ascii")

    def test_ascii_qr_is_plain_and_square(self):
        import segno
        qr = segno.make("https://m.agentj.app/#p=" + "A" * 120, error="m")
        txt = cli._qr_ascii(qr)
        rows = txt.splitlines()
        self.assertTrue(set(txt) <= {"#", " ", "\n"})
        self.assertEqual(len(rows), qr.symbol_size(scale=1, border=2)[1])
        self.assertTrue(all(len(r) == 2 * len(rows) for r in rows), "two characters per module: square on screen")
        txt.encode("ascii")

    def test_ssh_hints(self):
        e = {"SSH_CONNECTION": "203.0.113.5 50000 198.51.100.7 22"}
        h = cli.ssh_tunnel_hint(48123, e)
        self.assertIn("ssh -N -L 48123:127.0.0.1:48123 ", h)
        self.assertIn("@198.51.100.7", h)
        self.assertIn("[2001:db8::1]", cli.ssh_tunnel_hint(1, {"SSH_CONNECTION": "2001:db8::2 5 2001:db8::1 22"}))
        self.assertIsNone(cli.ssh_tunnel_hint(48123, {}))
        self.assertTrue(cli._remote_session(e))
        self.assertFalse(cli._remote_session({**e, "DISPLAY": ":0"}))
        self.assertFalse(cli._remote_session({}))


# ------------------------------------------------------------------ real macOS runs
@unittest.skipUnless(sys.platform == "darwin", "macOS LaunchAgent")
class RealLaunchAgent(unittest.TestCase):
    """A real `launchctl bootstrap gui/$UID` under a throwaway label + temp state dir; always removed afterwards."""

    def setUp(self):
        self.name = f"net.agentj.test-{secrets.token_hex(4)}"
        self.dir = tempfile.mkdtemp(prefix="aj-svc-")
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.env = {"AGENTJ_STATE_DIR": str(self.st.root), "AGENTJ_SERVICE_NAME": self.name,
                    update.URL_ENV: "off"}
        self.plist = pathlib.Path(service.plist_path(self.name))

    def tearDown(self):
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{self.name}"], capture_output=True, timeout=30)
        with contextlib.suppress(FileNotFoundError):
            self.plist.unlink()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_install_running_status_uninstall(self):
        self.assertFalse(self.plist.exists())
        r = _cli("service", "install", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        res, deadline = None, time.time() + 20
        while time.time() < deadline:
            with contextlib.suppress(names.ServeBusy):
                res = names.ctl_call(self.st, {"cmd": "status"}, 3)
            if res is not None:
                break
            time.sleep(0.2)
        log = (self.st.root / "service.log").read_text() if (self.st.root / "service.log").exists() else ""
        self.assertIsNotNone(res, log)
        s = json.loads(_cli("service", "status", "--json", env=self.env).stdout)
        self.assertEqual((s["kind"], s["name"], s["installed"], s["active"]), ("launchd", self.name, True, "active"))
        self.assertIn("service mode (--events quiet)", log)
        r = _cli("service", "uninstall", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.plist.exists())
        s = json.loads(_cli("service", "status", "--json", env=self.env).stdout)
        self.assertEqual((s["installed"], s["active"]), (False, "not_loaded"))
        deadline = time.time() + 10
        while self.st.sock_path.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(self.st.sock_path.exists(), "serve stopped cleanly")


@unittest.skipUnless(os.environ.get("AJ_REAL_CLAUDE") == "1", "opt-in: real Claude Code (AJ_REAL_CLAUDE=1)")
class RealClaudeFenced(unittest.TestCase):
    """serve + the real Claude Code inside the fence (bubblewrap / sandbox-exec) + the real permission tool: the phone
    approves a Write; the file appears in the work folder; the state dir stays unreadable from the Agent's side."""

    def test_write_with_a_phone_approval(self):
        from test_l1 import Phone, _host, _ready
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            work = pathlib.Path(d) / "work"
            work.mkdir()
            st.set_agent_config("claude", str(work))
            self.assertIsNone(fence.problem(st, str(work)))
            sent = []
            host = _host(st, sent)
            ph = Phone(st)

            async def wait(pred, s=240):
                t0 = time.monotonic()
                while not pred():
                    if time.monotonic() - t0 > s:
                        raise AssertionError(f"timeout; sent={sent[-4:]}")
                    await asyncio.sleep(0.1)

            async def go():
                run = asyncio.create_task(host.run())
                await wait(lambda: host.agent is not None)
                ses = _ready(host, ph)
                await host._app(ses, {"t": "msg", "id": "c" * 16, "ts": 0, "text":
                                      "Use the Write tool to create fenced.txt in the current folder with the text: fenced ok. "
                                      "Then try to list the folder " + str(st.root) + " with Bash and tell me what you saw."})
                answered, t0 = set(), time.monotonic()
                while time.monotonic() - t0 < 300:            # the phone approves every request (Write, then Bash)
                    for _, o in list(sent):
                        if o["t"] == "ask" and o["id"] not in answered:
                            answered.add(o["id"])
                            await host._app(ses, ph.answer(o, True))
                    done = [o for _, o in sent if o.get("t") == "status"]
                    if (work / "fenced.txt").exists() and done and done[-1].get("s") == "idle" and len(answered) >= 2:
                        break
                    await asyncio.sleep(0.2)
                host.stopping.set()
                await run
            with contextlib.redirect_stdout(io.StringIO()):
                asyncio.run(go())
            self.assertEqual((work / "fenced.txt").read_text().strip(), "fenced ok")
            replies = " ".join(o["text"] for _, o in sent if o.get("t") == "msg" and o.get("from") == "agent")
            self.assertNotIn("host_ed25519.key", replies, "the state dir is not listable from inside")
            if os.environ.get("AJ_EVIDENCE_DIR"):
                pathlib.Path(os.environ["AJ_EVIDENCE_DIR"], "real-claude-chain.json").write_text(json.dumps({
                    "platform": sys.platform, "fence": fence.kind(), "file_written": True,
                    "asks": [o["tool"] for _, o in sent if o["t"] == "ask"],
                    "agent_reply_mentions_state_files": "host_ed25519.key" in replies,
                    "agent_reply_tail": replies[-400:].replace(os.path.expanduser("~"), "~"),
                }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    unittest.main()
