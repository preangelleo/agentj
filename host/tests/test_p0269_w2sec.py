"""0.26.9 selfheal wave 2, lane w2-sec (ADR-0269-w2-sec): defensive fixes for security review round 1
(reports/qa/selfheal-0269/security-r1.md). Every test here failed on the base d2020a1155 (negative controls kept):

- C-1  doctor never runs a binary named by another process's command line (a planted `codex-x … app-server`);
- H-1  keys that make the shell run the line (Ctrl+O, Ctrl+X Ctrl+E, vi `v` after ESC) are held and classified like Enter;
       history expansion is off; `fc` / `r` / a leading `^` need a confirm;
- H-2  the PTY shell reads Agent J's own start-up file only (no ~/.bashrc / ~/.profile / ~/.inputrc), INPUTRC=/dev/null;
- H-3  a fenced Agent without a private /dev → no PTY mode (host_info / caps / term_open), the page is told why;
- M-1  `has_tty` reads the controlling terminal: `</dev/tty` inside the web terminal is not "a terminal at the computer";
- M-2  input typed ahead while the shell itself is busy waits for the prompt (checked there);
- M-3  `sudo -k` on every prompt (a direct `/usr/bin/sudo` leaves no ticket for the next line);
- M-4  nothing on the alternate screen (vim, less, top) reaches the transcript the Agent can read;
- M-5  the login proof runs `claude -p` in a scratch folder, never the Agent-writable project folder;
- L-1  end_turn signals a child only while it is still the same process (start time + parent re-read);
- L-3  a whole line (「!」 / page line) into the PTY only when nothing is typed at the prompt;
- L-4  a refused term_in / term_resize is logged once per minute, not once per frame;
- L-7  the fence's private /dev does not bind input / uinput / hidraw back.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _testtmp import TEST_TMP  # noqa: E402
from agentj import ctl_auth, doctor, fence, login_flow, term_pty, turn_heal  # noqa: E402

import test_p0269_pty as base  # noqa: E402
from test_p408_terminal import R, Rig  # noqa: E402

PY = sys.executable
LINUX = sys.platform.startswith("linux")


def _kill_quiet(pid):
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


# ------------------------------------------------------------ C-1 doctor
@unittest.skipUnless(LINUX, "/proc process table")
class DoctorNeverRunsForeignBinaries(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        self.marker = self.d / "RAN"
        self.planted = self.d / "codex-x"
        self.planted.write_text(f"#!/bin/sh\ntouch {self.marker}\necho 'codex-cli 9.9.9'\n")
        self.planted.chmod(0o755)
        self.cli = self.d / "codex"
        self.cli.write_text("#!/bin/sh\necho 'codex-cli 0.160.0'\n")
        self.cli.chmod(0o755)
        # a process whose argv[0] names the planted file and that says app-server (what a fenced Agent can start)
        self.p = subprocess.Popen(["bash", "-c", f'exec -a {self.planted} {PY} -c "import time; time.sleep(60)" app-server'])
        for _ in range(100):
            if str(self.planted) in pathlib.Path(f"/proc/{self.p.pid}/cmdline").read_bytes().decode(errors="replace"):
                break
            time.sleep(0.02)
        self.st = mock.Mock(root=self.d)
        self.st.exists.return_value = True
        self.st.agent_config.return_value = {"kind": "codex"}

    def tearDown(self):
        self.p.kill()
        self.p.wait()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_planted_app_server_binary_is_never_executed(self):
        calls = []
        real = doctor._version_of

        def spy(exe):
            calls.append(exe)
            return real(exe)
        with mock.patch.object(doctor, "_agent_bin", return_value=str(self.cli)), mock.patch.object(doctor, "_version_of", spy):
            row = doctor.check_turn(self.st, None)
        self.assertFalse(self.marker.exists(), "doctor ran the planted binary")
        self.assertNotIn(str(self.planted), calls)
        self.assertEqual(calls, [str(self.cli)])                      # only Agent J's own resolved CLI is asked
        codex = row["data"]["codex"]
        self.assertEqual(codex["cli"], "0.160.0")
        mine = [s for s in codex["servers"] if s["pid"] == self.p.pid]
        self.assertEqual(len(mine), 1)
        self.assertIsNone(mine[0]["version"])                        # shown as "?"
        self.assertFalse(mine[0]["ours"])

    def test_server_running_agent_js_own_binary_gets_the_cli_version_without_running_it(self):
        table = [(4242, f"{self.cli} app-server --listen stdio://")]
        calls = []
        with mock.patch.object(doctor, "_agent_bin", return_value=str(self.cli)), \
                mock.patch.object(doctor, "_version_of", side_effect=lambda e: calls.append(e) or "codex-cli 0.160.0"), \
                mock.patch("agentj.codex_procs.proc_table", return_value=table):
            codex = doctor.codex_servers(st=self.st)
        self.assertEqual(calls, [str(self.cli)])
        self.assertEqual(codex["servers"], [{"pid": 4242, "version": "0.160.0", "daemon": False, "ours": False, "other": False}])
        self.assertFalse(codex["mismatch"])


# ------------------------------------------------------------ the live PTY (bash)
@unittest.skipUnless(term_pty.pty_ok() and shutil.which("bash"), "needs a PTY platform and bash")
class SecPty(base.Pty):
    """The h-pty rig; only the tests below run here (the inherited ones run in test_p0269_pty)."""

    async def held(self, keys, victim, settle=0.8):
        n0 = len(self.sent)
        await self.typed(keys)
        c = await self.until(lambda: self.confirm(n0), 6)
        await asyncio.sleep(settle)
        self.assertTrue(victim.exists(), "the line ran before the answer")
        return c

    def victim(self, name):
        v = self.work / name
        v.mkdir()
        return v

    # -------------------------------------------------------- H-1
    def test_ctrl_o_is_held_like_enter(self):
        self.passkey()
        self.enable()
        v = self.victim("j-ctrl-o")

        async def go():
            await self.open_pty()
            c = await self.held("rm -rf ./j-ctrl-o\x0f", v)
            self.assertEqual(c["line"], "rm -rf ./j-ctrl-o")
        self.run_async(go)

    def test_ctrl_x_ctrl_e_editor_is_held(self):
        self.passkey()
        self.enable()
        v = self.victim("j-cxce")

        async def go():
            await self.open_pty()
            await self.held("rm -rf ./j-cxce\x18\x05", v)
        with mock.patch.dict(os.environ, {"VISUAL": "true", "EDITOR": "true", "FCEDIT": "true"}):   # an editor that saves as is
            self.run_async(go)

    def test_escape_then_vi_v_is_held(self):
        self.passkey()
        self.enable()
        v = self.victim("j-vi")

        async def go():
            await self.open_pty()
            await self.run_line("set -o vi; echo vi-on", "vi-on")
            await self.held("rm -rf ./j-vi\x1bv", v)
        with mock.patch.dict(os.environ, {"VISUAL": "true", "EDITOR": "true", "FCEDIT": "true"}):
            self.run_async(go)

    def test_allowed_editing_keys_are_not_held(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            n0 = len(self.sent)
            await self.typed("echo edit-okX\x7f\x01\x05\x1b[D\x1b[C\t")    # BS-DEL, C-a, C-e, arrows, TAB
            await asyncio.sleep(0.5)
            self.assertIsNone(self.confirm(n0))
            self.assertIsNone(self.desk.term.hold)
            await self.typed("\x15")
        self.run_async(go)

    def test_history_expansion_is_off(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            await self.run_line("true touch ./hist-bang; echo h-1", "h-1")
            await asyncio.sleep(0.3)
            n0 = len(self.sent)
            await self.typed("!!:1-2\r")
            await self.until(lambda: "not found" in self.out(n0) or "event" in self.out(n0), 6)
            await asyncio.sleep(0.3)
            self.assertFalse((self.work / "hist-bang").exists(), "!! re-ran an altered line")
        self.run_async(go)

    def test_fc_r_and_caret_need_a_confirm(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            for line in ("fc -s", "r", "^a^b", " fc -e - ls"):
                x = self.desk.term
                await self.until(lambda: x.at_prompt and x.hold is None, 6)
                n0 = len(self.sent)
                await self.typed(line + "\r")
                c = await self.until(lambda: self.confirm(n0), 6)
                self.assertEqual(c["line"].strip(), line.strip())
                await self.desk.on_phone(self.s, {"t": "term_confirm_no", "id": x.id, "n": c["n"]})
        self.run_async(go)

    # -------------------------------------------------------- H-2
    def test_owner_start_up_files_are_not_read(self):
        self.passkey()
        self.enable()
        for f in (".bashrc", ".bash_profile", ".bash_login", ".profile", ".bash_completion"):
            (self.home / f).write_text(f'touch "$HOME/ran{f}"\n')
        (self.home / ".inputrc").write_text('"\\C-xq": "touch ./inputrc-ran\\n"\n')
        # w2-sec N-1: what /etc/bash.bashrc → bash-completion would load (Arch-style SYS_BASHRC builds): the user's
        # completion file and directory — planted, and must never run (the test does not depend on the image's bash.bashrc:
        # bash must be started with --norc, which skips SYS_BASHRC on every build)
        comp = self.home / ".local/share/bash-completion/completions"
        comp.mkdir(parents=True)
        for c in ("ls", "echo", "cat", "_ls"):
            (comp / c).write_text(f'touch "$HOME/ran.compl-{c}"\n')
        n0 = [0]

        async def go():
            await self.open_pty()
            await self.run_line('echo "IR=$INPUTRC"', "IR=/dev/null")
            n0[0] = await self.run_line("tr '\\0' ' ' < /proc/$$/cmdline; echo; echo \"BC=$BASH_COMPLETION_USER_FILE CP=$(complete -p | wc -l)\"",
                                        "CP=")
            await self.typed("ls \t\t")                      # a completion attempt: nothing of the owner's is loaded
            await asyncio.sleep(0.6)
            await self.typed("\x15")
            await asyncio.sleep(0.3)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / ".local/share")}):
            self.run_async(go)
        out = self.out(n0[0])
        if LINUX:
            self.assertIn("--norc", out)
        self.assertIn("BC=/dev/null CP=0", out)
        ran = sorted(p.name for p in self.home.iterdir() if p.name.startswith("ran."))
        self.assertEqual(ran, [], "an owner start-up file ran in the web terminal")

    # -------------------------------------------------------- N-3
    def _escape(self, prefix):
        """From inside the web terminal: a new session with a NEW terminal (script) — the shape of `setsid -f script -qc
        'agentj resume'`. → the pid of the program inside it."""
        self.passkey()
        self.enable()
        pidf = self.work / "n3.pid"

        async def go():
            await self.open_pty()
            await self.run_line(f"{prefix}setsid -f script -qfc 'echo $$ > n3.pid; exec sleep 300' /dev/null; echo n3-ok", "n3-ok")
            await self.until(lambda: pidf.exists() and pidf.read_text().strip().isdigit(), 8)
            pid = int(pidf.read_text())
            self.addCleanup(_kill_quiet, pid)
            await asyncio.sleep(0.3)
            self.assertTrue(os.readlink(f"/proc/{pid}/fd/0").startswith("/dev/pts/"))   # a real terminal on stdin
            self.assertFalse(ctl_auth.has_tty(pid), "a terminal opened from the web terminal counted as local presence")
            self.assertEqual(ctl_auth.refusal(pid, os.getpid(), []), "terminal")
            await self.desk.close("owner")
            await self.until(lambda: not _alive(pid), 10)                 # and the close sweep ends it
        self.run_async(go)

    @unittest.skipUnless(LINUX and shutil.which("script") and shutil.which("setsid"), "Linux, util-linux script")
    def test_setsid_script_from_the_web_terminal_is_not_local_presence(self):
        self._escape("")

    @unittest.skipUnless(LINUX and shutil.which("script") and shutil.which("setsid"), "Linux, util-linux script")
    def test_without_the_token_the_cgroup_still_tells(self):
        procs = term_pty.cg_make("probe-" + os.urandom(4).hex())
        if procs is None:
            self.skipTest("no writable cgroup v2 subtree here (e.g. an ssh session): the environment token is the only tag")
        term_pty.cg_drop(procs)
        self._escape("env -u AGENTJ_TERM_TOKEN ")

    # -------------------------------------------------------- M-2
    def test_typeahead_while_the_shell_is_busy_waits_for_the_prompt(self):
        self.passkey()
        self.enable()
        v = self.victim("j-ahead")

        async def go():
            await self.open_pty()
            n0 = len(self.sent)
            await self.typed("sleep 1.2 & wait; echo waited-ok\rrm -rf ./j-ahead\r")
            try:
                await self.until(lambda: "waited-ok" in self.out(n0).replace("echo waited-ok", ""), 8)
                c = await self.until(lambda: self.confirm(n0), 6)
            except AssertionError:
                self.fail(f"out={self.out(n0)[-600:]!r} victim={v.exists()} hold={self.desk.term.hold} "
                          f"pending={self.desk.term.pending!r} at_prompt={self.desk.term.at_prompt}")
            self.assertEqual(c["line"], "rm -rf ./j-ahead")
            await asyncio.sleep(0.5)
            self.assertTrue(v.exists())
        self.run_async(go)

    # -------------------------------------------------------- M-3
    def test_sudo_ticket_is_dropped_on_every_prompt(self):
        self.passkey()
        self.enable()
        log = pathlib.Path(self.desk.sudo).parent / "args.log"

        async def go():
            await self.open_pty()
            k0 = log.read_text().splitlines().count("-k")
            await self.run_line("echo p-1", "p-1")
            await self.run_line("echo p-2", "p-2")
            await asyncio.sleep(0.3)
            self.assertGreaterEqual(log.read_text().splitlines().count("-k") - k0, 2)
        self.run_async(go)

    # -------------------------------------------------------- M-4
    def test_alternate_screen_never_reaches_the_transcript(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            x = self.desk.term
            n0 = len(self.sent)
            await self.desk._pty_out(x, "before-alt\r\n\x1b[?1049hALT-ONE", False)
            await self.desk._pty_out(x, "ALT-TWO\x1b[?1049l", False)
            await self.desk._pty_out(x, "after-alt\x1b[?47hALT-THREE\x1b[?47lend-alt\r\n", False)
            tr = "".join(x.tr)
            for s in ("ALT-ONE", "ALT-TWO", "ALT-THREE"):
                self.assertNotIn(s, tr)
                self.assertIn(s, self.out(n0))                       # the phone still sees the screen
            for s in ("before-alt", "after-alt", "end-alt"):
                self.assertIn(s, tr)
        self.run_async(go)

    # -------------------------------------------------------- L-3
    def test_whole_line_refused_while_something_is_typed(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            x = self.desk.term
            await self.typed("echo half")
            await asyncio.sleep(0.3)
            with mock.patch.object(self.desk.st, "log", wraps=self.desk.st.log) as lg:
                await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 50, "line": "touch ./l3-ran"})
                await asyncio.sleep(0.6)
            self.assertFalse((self.work / "l3-ran").exists())
            reasons = [c.kwargs.get("reason") for c in lg.call_args_list if c.args and c.args[0] == "control_refused"]
            self.assertIn("edited", reasons)
            await self.typed("\x15")
            await asyncio.sleep(0.2)
            await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 51, "line": "touch ./l3-ok"})
            await self.until(lambda: (self.work / "l3-ok").exists(), 6)      # control: an empty prompt takes the line
        self.run_async(go)

    # -------------------------------------------------------- L-4
    def test_refusals_are_logged_once_per_window(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            x = self.desk.term
            with mock.patch.object(self.desk.st, "log", wraps=self.desk.st.log) as lg:
                for k in range(30):
                    await self.desk.on_phone(self.s, {"t": "term_resize", "id": x.id, "cols": 9999, "rows": 5})
                    await self.desk.on_phone(self.s, {"t": "term_in", "id": x.id, "seq": 100 + k, "d": ""})
            n = [c for c in lg.call_args_list if c.args and c.args[0] == "control_refused"]
            self.assertLessEqual(len(n), 2, n)
            self.assertGreaterEqual(len(n), 1)
        self.run_async(go)


class SecPtyR3(SecPty):
    """Round 3 (security-r3.md INFO items) on the live PTY."""

    def test_ps0_and_mail_never_reach_the_shell(self):
        self.passkey()
        self.enable()
        env = {"PS0": "$(touch ./ps0-ran)", "MAILPATH": "/nonexistent?$(touch ./mail-ran)", "MAIL": "/nonexistent",
               "MAILCHECK": "0"}

        async def go():
            await self.open_pty()
            await self.confirmed_line("echo r3-1; env | grep -c -E '^(PS0|MAILPATH|MAIL|MAILCHECK)=' ; echo end-r3", "end-r3")
            await self.run_line("echo after", "after")
        with mock.patch.dict(os.environ, env):
            self.run_async(go)
        self.assertFalse((self.work / "ps0-ran").exists(), "PS0 from serve's environment ran")
        self.assertFalse((self.work / "mail-ran").exists())
        self.assertIn("r3-1\r\n0\r\nend-r3", self.out())

    def test_system_dirs_come_before_user_dirs_on_path(self):
        self.passkey()
        self.enable()
        ub = self.home / "bin"
        ub.mkdir()
        (ub / "ls").write_text("#!/bin/sh\necho SHADOWED\n")
        (ub / "ls").chmod(0o755)
        (ub / "only-mine").write_text("#!/bin/sh\necho MINE-OK\n")
        (ub / "only-mine").chmod(0o755)

        async def go():
            await self.open_pty()
            n0 = await self.run_line("type -P ls; only-mine; echo path-done", "path-done")
            out = self.out(n0)
            self.assertNotIn(str(ub / "ls"), out)
            self.assertNotIn("SHADOWED", out)
            self.assertIn("MINE-OK", out)                                 # user dirs stay, after the system ones
        with mock.patch.dict(os.environ, {"PATH": f"{ub}:/usr/bin:/bin"}):
            self.run_async(go)

    for _n in [n for n in dir(SecPty) if n.startswith("test_")]:
        locals()[_n] = None
    del _n


class CgroupHousekeeping(unittest.TestCase):
    def fake_fs(self):
        fs = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, fs, True)
        (fs / "cgroup.controllers").write_text("")
        root = fs / "svc"
        root.mkdir()
        (root / "cgroup.procs").write_text("")
        return fs, root

    @unittest.skipUnless(LINUX, "cgroup v2 is Linux")
    def test_leftover_cleanup_spares_fresh_and_live_cgroups(self):
        fs, root = self.fake_fs()
        old, fresh, live = (root / (term_pty.CG_PREFIX + n) for n in ("old", "fresh", "live"))
        for d in (old, fresh, live):
            d.mkdir()
        t = time.time() - 3600
        os.utime(old, (t, t))
        os.utime(live, (t, t))
        term_pty.LIVE_CG.add(str(live / "cgroup.procs"))
        self.addCleanup(term_pty.LIVE_CG.discard, str(live / "cgroup.procs"))
        with mock.patch.object(term_pty, "_cg_path", return_value="/svc"):
            term_pty.cg_make("new1", fs=str(fs))
        self.assertFalse(old.exists())                                   # an old empty leftover goes
        self.assertTrue(fresh.exists(), "a concurrent session's fresh cgroup was removed")
        self.assertTrue(live.exists(), "this instance's live session cgroup was removed")

    def test_sweep_uses_cgroup_kill_when_present(self):
        fs, root = self.fake_fs()
        d = root / (term_pty.CG_PREFIX + "k")
        d.mkdir()
        (d / "cgroup.procs").write_text("")
        (d / "cgroup.kill").write_text("")
        self.assertTrue(term_pty.cg_kill(str(d / "cgroup.procs")))
        self.assertEqual((d / "cgroup.kill").read_text(), "1")
        (d / "cgroup.kill").unlink()
        victim = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
        self.addCleanup(_kill_quiet, victim.pid)
        (d / "cgroup.procs").write_text(f"{victim.pid}\n")
        self.assertFalse(term_pty.cg_kill(str(d / "cgroup.procs")))      # older kernel: the pid loop
        self.assertEqual(victim.wait(5), -9)


class SteerLogFields(unittest.TestCase):
    def test_msg_steered_uncertain_reaches_host_log(self):
        from agentj import serve
        from agentj.state import State
        st = mock.Mock()
        fake = NS(st=st, cur_turn=7, hist=NS(get=lambda t: None), hist_update=mock.Mock(), _post=mock.Mock(),
                  _send_p33=None, _send_device=None)
        send = NS(device="dev1", turn=9, sid="s1")
        with mock.patch.object(serve.humane, "of", return_value="x"):
            serve.Host.msg_steered(fake, send, uncertain=True)
        (ev, kw), = [(c.args[0], c.kwargs) for c in st.log.call_args_list]
        self.assertEqual(ev, "msg_steered")
        self.assertTrue(kw["uncertain"])
        self.assertLessEqual(set(kw), State.LOG_FIELDS, "every field reaches host.log")


for _n in dir(base.Pty):          # the inherited contract runs in test_p0269_pty; here only the w2-sec tests
    if _n.startswith("test_") and _n not in SecPty.__dict__:
        setattr(SecPty, _n, None)


# ------------------------------------------------------------ H-3
class FencedWithoutPrivateDev(Rig):
    def test_no_pty_mode_when_the_fence_has_no_private_dev(self):
        self.passkey()
        self.enable()
        self.desk.pty_ok = True
        old = fence.PRIVATE_DEV[0]
        self.addCleanup(fence.PRIVATE_DEV.__setitem__, 0, old)
        with mock.patch.object(sys, "platform", "linux"):
            fence.PRIVATE_DEV[0] = True                                  # control
            self.assertEqual(self.desk.info(self.phone.did)["modes"], ["line", "pty"])
            fence.PRIVATE_DEV[0] = False
            info = self.desk.info(self.phone.did)
            self.assertEqual(info["modes"], ["line"])
            self.assertIn("pty_why", info)
            self.assertNotIn("term_pty1", self.host._caps(self.s)["caps"])

            async def go():
                n0 = len(self.sent)
                await self.desk.on_phone(self.s, {"t": "term_open", "r": R, "mode": "pty", "cols": 80, "rows": 24})
                res = [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]
                self.assertEqual(res["why"], "fence_dev")
            self.run_async(go)
            self.host.agent_cfg["fence"] = False                         # isolation off: the Agent is the owner anyway
            self.assertEqual(self.desk.info(self.phone.did)["modes"], ["line", "pty"])


# ------------------------------------------------------------ M-1
@unittest.skipUnless(LINUX and term_pty.pty_ok(), "Linux /proc")
class ControllingTerminal(unittest.TestCase):
    def test_dev_tty_inside_the_web_terminal_is_not_local_presence(self):
        m, sl = term_pty.open_pair(80, 24)
        shim = term_pty.SHIM
        p = subprocess.Popen([PY, "-I", "-S", "-c", shim, "/bin/sh", "-c", "exec sleep 30 </dev/tty"], stdin=sl,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            for _ in range(200):
                with __import__("contextlib").suppress(OSError):
                    if os.readlink(f"/proc/{p.pid}/fd/0") == "/dev/tty":
                        break
                time.sleep(0.02)
            self.assertEqual(os.readlink(f"/proc/{p.pid}/fd/0"), "/dev/tty")
            self.assertTrue(ctl_auth.has_tty(p.pid))                     # control: an ordinary terminal counts
            term_pty.LIVE[os.fstat(sl).st_rdev] = os.path.basename(os.ttyname(sl))
            self.assertFalse(ctl_auth.has_tty(p.pid))                    # its controlling tty is the web terminal's PTY
        finally:
            term_pty.LIVE.pop(os.fstat(sl).st_rdev, None)
            p.kill()
            p.wait()
            os.close(m)
            os.close(sl)

    def test_dev_tty_without_a_controlling_terminal_is_not_a_terminal(self):
        p = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            with mock.patch.object(ctl_auth.os, "readlink", return_value="/dev/tty"):
                self.assertFalse(ctl_auth.has_tty(p.pid))
        finally:
            p.kill()
            p.wait()


# ------------------------------------------------------------ M-5
class LoginProofCwd(unittest.TestCase):
    def test_claude_proof_never_runs_in_the_project_folder(self):
        project = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, project, True)
        seen = {}

        def run(cmd, **kw):
            seen["cwd"] = kw.get("cwd")
            return NS(returncode=0, stdout='{"result": "hi"}', stderr="")
        with mock.patch.object(login_flow.ar, "binary", return_value="/bin/true"), \
                mock.patch("agentj.bots.native.command", return_value=["/bin/true"]), \
                mock.patch("agentj.bots.native.parse", return_value=("hi", None)), \
                mock.patch.object(login_flow.subprocess, "run", side_effect=run):
            self.assertIsNone(login_flow.main_reply({"HOME": str(project)}, str(project)))
        self.assertTrue(seen["cwd"])
        self.assertNotEqual(os.path.realpath(seen["cwd"]), os.path.realpath(project))
        self.assertFalse(os.path.exists(seen["cwd"]), "a scratch folder, removed afterwards")


# ------------------------------------------------------------ L-1
@unittest.skipUnless(LINUX, "/proc process table")
class EndTurnPidReuse(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.victim = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
        self.harness = await asyncio.create_subprocess_exec(PY, "-c", "import time; time.sleep(60)")
        self.addCleanup(self._clean)
        self.heal = turn_heal.TurnHeal(NS(st=NS(root=pathlib.Path(TEST_TMP)), turn_t0=time.monotonic(), log=mock.Mock()))
        self.a = NS(proc=self.harness, kind="codex")

    def _clean(self):
        for p in (self.victim, self.harness):
            with __import__("contextlib").suppress(ProcessLookupError):
                p.kill()
        self.victim.wait()

    async def go(self, rows):
        with mock.patch.object(turn_heal, "proc_rows", return_value=rows), \
                mock.patch.object(turn_heal, "turn_children", return_value=[self.victim.pid]), \
                mock.patch.object(turn_heal, "may_stop", return_value=True):
            return await self.heal._escalate(self.a, "end_turn")

    async def test_a_reused_pid_is_not_signalled(self):
        real = turn_heal.proc_rows()[self.victim.pid]
        res = await self.go({self.victim.pid: (real[0], real[1] - 3600)})        # the snapshot saw another process
        self.assertEqual(res, ("children", None))
        await asyncio.sleep(0.3)
        self.assertIsNone(self.victim.poll())
        res = await self.go({self.victim.pid: (real[0] + 1 if real[0] > 1 else 2, real[1])})   # another parent
        await asyncio.sleep(0.3)
        self.assertIsNone(self.victim.poll())
        res = await self.go({self.victim.pid: real})                             # control: the same process is ended
        for _ in range(50):
            if self.victim.poll() is not None:
                break
            await asyncio.sleep(0.05)
        self.assertIsNotNone(self.victim.poll())


# ------------------------------------------------------------ L-7
class FenceDevNoInput(unittest.TestCase):
    def test_input_and_hid_are_not_bound_back(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("input", "dri", "snd", "kvm"):
            (d / n).mkdir() if n in ("input", "dri", "snd") else (d / n).write_text("")
        for n in ("uinput", "hidraw0", "hidraw12", "hiddev0", "video0"):
            (d / n).write_text("")
        old = fence.PRIVATE_DEV[0]
        fence.PRIVATE_DEV[0] = True
        try:
            a = fence.dev_args(str(d))
        finally:
            fence.PRIVATE_DEV[0] = old
        bound = {a[i + 2] for i, w in enumerate(a) if w == "--dev-bind-try"}
        for n in ("input", "uinput", "hidraw0", "hidraw12", "hiddev0"):
            self.assertNotIn("/dev/" + n, bound, n)
        for n in ("dri", "snd", "kvm", "video0"):
            self.assertIn("/dev/" + n, bound, n)                          # control: the rest still comes back


if __name__ == "__main__":
    unittest.main()
