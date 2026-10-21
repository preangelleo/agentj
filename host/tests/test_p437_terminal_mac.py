"""P437 (#202): the web terminal on a macOS host — unit tests with a simulated macOS backend (this suite runs on Linux).

- term_mac's process reader: KERN_PROCARGS2 parsing, the kinfo_proc fields, proc_info / in_session through term_askpass
  when the real "/proc" is asked for on macOS; check_peer passes the same sudo → helper tree and refuses the same forgeries.
- LOCAL_PEERCRED uid; the Command Line Tools' Python (never the /usr/bin stub); platform_ok (macOS yes, Windows no).
- the shell: zsh unless the login shell is bash; zsh's setup (space-led, #184 HISTORY_IGNORE, sudo -k -A, the markers);
  launchd's PATH / locale; the history scrub keeps zsh's metafied bytes and drops key / home-path entries whole.
- `agentj terminal enable` on macOS: a standard account and Touch ID in sudo's PAM are refused in plain words.
- #184 on the Linux bash path (real bash) and the borrowed-computer flow: unpairing a remote closes its terminal at once,
  takes it off the list, deletes its remembered password and refuses its next open.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import socket
import struct
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import elevate, term_askpass, term_cli, term_mac, term_shell, term_store, terminal  # noqa: E402

from test_p408_terminal import GOOD, R, Rig, tmpdir  # noqa: E402

ME = os.getuid()


class FakeLib:
    """A process table standing in for libproc / sysctl. procs: pid → {ppid, euid, name, sid, argv, env}."""

    def __init__(self, procs):
        self.procs = procs

    def bsd(self, pid):
        p = self.procs.get(pid)
        return {"ppid": p["ppid"], "euid": p["euid"], "name": p["name"]} if p else None

    def args(self, pid):
        p = self.procs.get(pid)
        if not p or p["euid"] != ME:          # the kernel gives argv / environ for this user's processes only
            return None
        return list(p["argv"]), list(p.get("env", []))

    def ps_argv(self, pid):
        p = self.procs.get(pid)
        return " ".join(p["argv"]).split() if p else []

    def sid(self, pid):
        p = self.procs.get(pid)
        return p["sid"] if p else None

    def pids(self):
        return list(self.procs)


def mac(lib=None):
    """Patch term_mac to 'running on macOS' with a fake backend."""
    stack = [mock.patch.object(term_mac, "DARWIN", True)]
    if lib is not None:
        stack.append(mock.patch.object(term_mac, "LIB", lib))
    return stack


class Mac(unittest.TestCase):
    def patched(self, lib=None):
        for p in mac(lib):
            p.start()
            self.addCleanup(p.stop)


class Procs(Mac):
    def test_procargs_layout(self):
        raw = struct.pack("i", 3) + b"/usr/bin/sudo\0\0\0\0" + b"sudo\0-A\0true\0HOME=/h/o\0A=1\0\0\0garbage"
        self.assertEqual(term_mac.parse_procargs(raw), (["sudo", "-A", "true"], ["HOME=/h/o", "A=1"]))
        self.assertIsNone(term_mac.parse_procargs(struct.pack("i", 5) + b"/x\0a\0b\0"))     # argc says more than there is
        self.assertIsNone(term_mac.parse_procargs(b"\1\0"))

    def test_kinfo_offsets(self):
        b = bytearray(term_mac.KINFO_SIZE)
        b[243:243 + 5] = b"sudo\0"
        struct.pack_into("I", b, 420, 0)
        struct.pack_into("i", b, 560, 4242)
        self.assertEqual(term_mac.kinfo_fields(bytes(b)), {"ppid": 4242, "euid": 0, "name": "sudo"})

    def test_proc_info_through_term_askpass_on_macos(self):
        self.patched(FakeLib({700: {"ppid": 1, "euid": 0, "name": "sudo", "sid": 500, "argv": ["/usr/bin/sudo", "-k", "-A", "ls"]},
                              701: {"ppid": 700, "euid": ME, "name": "Python", "sid": 500, "argv": ["py", "-IS", "/h.py"]}}))
        self.assertEqual(term_askpass.proc_info(701), {"ppid": 700, "sid": 500, "name": "Python", "euid": ME,
                                                       "argv": ["py", "-IS", "/h.py"]})
        root = term_askpass.proc_info(700)
        self.assertEqual((root["name"], root["euid"]), ("sudo", 0))
        self.assertEqual(root["argv"], ["/usr/bin/sudo", "-k", "-A", "ls"])    # ps words: display only
        self.assertIsNone(term_askpass.proc_info(999))

    def test_a_test_proc_dir_is_still_read_as_proc(self):
        self.patched(FakeLib({}))
        with tmpdir() as d:
            p = pathlib.Path(d) / "1"
            p.mkdir()
            (p / "stat").write_text("1 (x) S 0 1 1 0 -1 0")
            (p / "status").write_text(f"Name:\tx\nUid:\t{ME}\t{ME}\t{ME}\t{ME}\n")
            (p / "cmdline").write_bytes(b"x\0")
            self.assertEqual(term_askpass.proc_info(1, d)["name"], "x")

    def test_in_session_sweep(self):
        tok = "T" * 10
        self.patched(FakeLib({
            500: {"ppid": 1, "euid": ME, "name": "zsh", "sid": 500, "argv": ["/bin/zsh"]},
            501: {"ppid": 500, "euid": ME, "name": "sleep", "sid": 500, "argv": ["sleep"]},
            502: {"ppid": 1, "euid": ME, "name": "nohup", "sid": 777, "argv": ["x"], "env": [f"{term_askpass.TOKEN_ENV}={tok}"]},
            503: {"ppid": 500, "euid": 0, "name": "sudo", "sid": 500, "argv": ["sudo"]},       # root: not ours to kill
            504: {"ppid": 1, "euid": ME, "name": "other", "sid": 888, "argv": ["y"], "env": ["X=1"]},
            os.getpid(): {"ppid": 1, "euid": ME, "name": "me", "sid": 500, "argv": ["me"]}}))
        self.assertEqual(sorted(term_askpass.in_session(500, tok)), [500, 501, 502])
        self.assertEqual(sorted(term_askpass.in_session(500, "")), [500, 501])


class Peer(Mac):
    """check_peer on the simulated macOS tree: the same rules as the Linux /proc test (test_p408 Askpass)."""

    def setUp(self):
        self.tmp = tmpdir()
        base = pathlib.Path(self.tmp.name)
        (base / "t").mkdir()
        self.helper = term_askpass.Helper(base / "t", base / "t" / "askpass.sock", sys.executable)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, *, parent="sudo", peuid=0, sid=500, argv=None, token="tok", uid=ME):
        lib = FakeLib({600: {"ppid": 500, "euid": peuid, "name": parent, "sid": 500, "argv": ["/usr/bin/sudo", "-k", "-A", "true"]},
                       601: {"ppid": 600, "euid": ME, "name": "Python", "sid": sid,
                             "argv": argv or ["/Library/Developer/CommandLineTools/…/Python", "-IS", str(self.helper.path), "pw"]}})
        with mock.patch.object(term_mac, "DARWIN", True), mock.patch.object(term_mac, "LIB", lib):
            return term_askpass.check_peer(601, uid, token, "tok", 500, self.helper)

    def test_the_sudo_child_passes(self):
        self.assertEqual(self.check(), (None, 600))

    def test_forgeries(self):
        self.assertEqual(self.check(parent="zsh")[0], "parent")
        self.assertEqual(self.check(peuid=ME or 501)[0], "parent")          # a user process named sudo
        self.assertEqual(self.check(sid=999)[0], "session")
        self.assertEqual(self.check(argv=["py", "-IS", "/elsewhere.py"])[0], "helper")
        self.assertEqual(self.check(token="nope")[0], "token")
        self.assertEqual(self.check(uid=None)[0], "uid")                    # macOS LOCAL_PEERPID alone: no uid → refused

    def test_local_peercred_uid(self):
        class S:
            def getsockopt(self, level, opt, size):
                assert (level, opt) == (0, 1)
                return struct.pack("IIh", 0, 501, 1) + b"\0" * 66
        self.assertEqual(term_mac.peer_uid(S()), 501)

        class Bad:
            def getsockopt(self, *a):
                raise OSError(42, "no")
        self.assertIsNone(term_mac.peer_uid(Bad()))
        if sys.platform == "darwin":                                        # L1 on a Mac: a real socketpair
            a, b = socket.socketpair()
            with a, b:
                self.assertEqual(term_mac.peer_uid(a), ME)


class Platform(Mac):
    def test_platform_ok(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertTrue(term_shell.platform_ok())
        with mock.patch.object(sys, "platform", "win32"):
            self.assertFalse(term_shell.platform_ok())

    def test_host_block_says_supported_on_macos(self):
        desk = terminal.Desk.__new__(terminal.Desk)
        desk.os_ok = None
        with mock.patch.object(terminal, "platform_ok", return_value=True):
            self.assertTrue(desk.supported())

    def test_command_line_tools_python_never_the_stub(self):
        self.patched()
        seen = []
        with mock.patch.object(term_askpass, "python_ok", side_effect=lambda c: seen.append(c)):
            self.assertIsNone(term_askpass.safe_python())
        self.assertEqual(tuple(seen), term_mac.PYTHONS)
        self.assertNotIn("/usr/bin/python3", seen)          # Xcode's stub: admin-writable target, or the install dialog

    def test_cli_message_names_macos_as_supported(self):
        self.assertIn("macOS", term_cli.ERR["os"])
        self.assertNotIn("macOS / Windows 下一版", term_cli.ERR["os"])


class Shell(Mac):
    def test_pick_shell(self):
        yes = lambda p: True  # noqa: E731
        self.assertEqual(term_mac.pick_shell("/bin/zsh", yes), "zsh")
        self.assertEqual(term_mac.pick_shell("/opt/homebrew/bin/fish", yes), "zsh")
        self.assertEqual(term_mac.pick_shell("/bin/bash", yes), "bash")
        self.assertEqual(term_mac.pick_shell("/bin/zsh", lambda p: p != "/bin/zsh"), "bash")

    def test_zsh_setup(self):
        s = term_mac.zsh_setup("/st/terminal/history", "/usr/bin/sudo", True, "ab12", term_shell.HIST_IGNORE, "nopy")
        self.assertTrue(all(ln.startswith(" ") for ln in s))                # HIST_IGNORE_SPACE: none of it is history
        text = "\n".join(s)
        for want in ("NO_PROMPT_SP", "NO_BANG_HIST", "HIST_IGNORE_SPACE", "INC_APPEND_HISTORY", "HISTFILE=/st/terminal/history",
                     "sudo() { /usr/bin/sudo -k -A \"$@\"; }", "\\e]133;A;ab12\\a", "printf '\\033]133;R;ab12\\007'"):
            self.assertIn(want, text)
        self.assertIn("HISTORY_IGNORE='(*[Tt][Oo][Kk][Ee][Nn]*=*|", text)  # bash's HISTIGNORE, `|`-joined for zsh
        nopy = "\n".join(term_mac.zsh_setup("/h", "/usr/bin/sudo", False, "ab12", term_shell.HIST_IGNORE, "nopy"))
        self.assertIn("printf '%s\\n' nopy >&2; return 1", nopy)
        self.assertEqual(term_mac.ZSH_ARGV, ("/bin/zsh", "-f", "+m", "-o", "HIST_IGNORE_SPACE", "-i"))
        self.assertEqual(meta("文"), b"\xe6\x83\xb6\x83\xa7")             # as zsh 5.9 wrote it on the Mac M1

    def test_launchd_env(self):
        with tmpdir() as d:
            os.makedirs(os.path.join(d, "paths.d"))
            pathlib.Path(d, "paths").write_text("/usr/local/bin\n/usr/bin\n/bin\n")
            pathlib.Path(d, "paths.d", "10-cryptex").write_text("/System/Cryptexes/App/usr/bin\n")
            env = term_mac.fix_env({"PATH": "/h/o/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}, d, "/h/o")
        path = env["PATH"].split(":")
        self.assertEqual(path[:5], ["/h/o/.local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])   # the service's own first
        for p in ("/opt/homebrew/bin", "/usr/local/bin", "/System/Cryptexes/App/usr/bin"):
            self.assertIn(p, path)
        self.assertEqual(len(path), len(set(path)))
        self.assertEqual((env["LANG"], env["LC_CTYPE"]), ("en_US.UTF-8", "en_US.UTF-8"))
        self.assertNotIn("LC_ALL", env)
        kept = term_mac.fix_env({"PATH": "/usr/bin", "LANG": "zh_CN.UTF-8"}, "/nonexistent", "/h/o")
        self.assertEqual(kept["LANG"], "zh_CN.UTF-8")


def meta(s: str) -> bytes:
    """zsh's metafy: NUL and 0x83–0xa2 → 0x83, byte ^ 32 (on macOS 「文」 e6 96 87 is written e6 83 b6 83 a7)."""
    out = bytearray()
    for c in s.encode():
        out += bytes([0x83, c ^ 32]) if c == 0 or 0x83 <= c <= 0xa2 else bytes([c])
    return bytes(out)


class History(unittest.TestCase):
    KEY = "export GH_TOKEN=ghp_" + "a" * 36

    def test_zsh_file_keeps_metafied_bytes_and_drops_whole_entries(self):
        with tmpdir() as d:
            p = pathlib.Path(d) / "history"
            body = [b"ls -la", meta("echo 中文"), self.KEY.encode(), b"cd /" + b"home/someone/x", b"echo one \\", b"Bearer " +
                    b"abcDEF123" * 4, b"git status"]
            p.write_bytes(b"\n".join(body) + b"\n")
            os.chmod(p, 0o644)
            self.assertEqual(term_mac.scrub_history(p, zsh=True), 3)
            self.assertEqual(p.read_bytes(), b"ls -la\n" + meta("echo 中文") + b"\ngit status\n")
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
            self.assertEqual(term_mac.scrub_history(p, zsh=True), 0)          # clean: untouched
            self.assertEqual(term_mac.scrub_history(pathlib.Path(d) / "none"), 0)

    def test_bash_file(self):
        with tmpdir() as d:
            p = pathlib.Path(d) / "history"
            p.write_text(f"echo hi\n{self.KEY}\necho 中文\n")
            self.assertEqual(term_mac.scrub_history(p), 1)
            self.assertEqual(p.read_text(), "echo hi\necho 中文\n")

    def test_unmetafy(self):
        self.assertEqual(term_mac.unmetafy(meta("中文 ok")).decode(), "中文 ok")


class Enable(Mac):
    def run_check(self, *, admin=True, risk=(), stderr="sudo: a password is required\n"):
        r = subprocess.CompletedProcess([], 1, "", stderr)
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("shutil.which", return_value="/usr/bin/sudo"), \
                mock.patch.object(subprocess, "run", return_value=r), mock.patch.object(term_mac, "is_admin", return_value=admin), \
                mock.patch.object(elevate, "local_auth_risk", return_value=list(risk)):
            return term_cli._password_sudo()

    def test_admin_with_a_password_sudo_passes(self):
        self.assertIsNone(self.run_check())

    def test_standard_account_is_refused(self):
        self.assertEqual(self.run_check(admin=False), term_mac.NOT_ADMIN)

    def test_touch_id_in_sudo_pam_is_refused(self):
        self.assertEqual(self.run_check(risk=["sudo_local: auth sufficient pam_tid.so"]), elevate.LOCAL_AUTH_HINT)

    def test_nopasswd_still_refused_first(self):
        r = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("shutil.which", return_value="/usr/bin/sudo"), \
                mock.patch.object(subprocess, "run", return_value=r):
            self.assertIn("NOPASSWD", term_cli._password_sudo())

    def test_is_admin_reads_the_admin_group(self):
        import grp
        import pwd
        pw = pwd.getpwuid(os.getuid())
        fake = grp.struct_group(("admin", "*", 80, []))
        with mock.patch.object(grp, "getgrnam", return_value=fake), mock.patch.object(grp, "getgrgid", return_value=fake), \
                mock.patch.object(os, "getgrouplist", return_value=[20, 80]):
            self.assertTrue(term_mac.is_admin(pw.pw_name))
        with mock.patch.object(grp, "getgrnam", return_value=fake), mock.patch.object(grp, "getgrgid", return_value=fake), \
                mock.patch.object(os, "getgrouplist", return_value=[20]):
            self.assertEqual(term_mac.is_admin(pw.pw_name), pw.pw_gid == 80)
        with mock.patch.object(grp, "getgrnam", side_effect=KeyError("admin")):
            self.assertFalse(term_mac.is_admin(pw.pw_name))


class RealShell(Rig):
    """Real bash (this host): #184 history and the borrowed-computer unpair."""

    def test_history_keeps_no_key_lines(self):
        self.passkey()
        self.enable()

        async def go():
            ready = await self.open()
            self.assertEqual(ready["t"], "term_ready", ready)
            await self.line("echo hi-p437", "hi-p437")
            await self.line("echo pass-p437", "pass-p437")          # HISTIGNORE *[Pp][Aa][Ss][Ss]*
            await self.line("echo hi-p437", "hi-p437")              # ignoredups
        self.run_async(go)
        hist = (term_store.root(self.st) / "history").read_text()
        self.assertIn("echo hi-p437", hist)
        self.assertEqual(hist.count("echo hi-p437"), 1)
        self.assertNotIn("pass-p437", hist)
        self.assertNotIn("HISTFILE", hist)                           # the setup lines are not history
        self.assertNotIn("PS1", hist)

    def test_unpairing_a_borrowed_browser_ends_its_terminal_at_once(self):
        """#202: scan-pair a friend's computer browser → use the terminal → unbind: from that instant the browser has no
        terminal — the open one closes, its entry and its remembered password go, and its next open is refused."""
        self.passkey()
        self.enable(web=True)

        async def go():
            ready = await self.open()
            self.assertEqual(ready["t"], "term_ready", ready)
            self.assertIsNotNone(term_store.meta(self.st, self.phone.did))   # local mode: the password is remembered
            await self.line("echo borrowed", "borrowed")
            x = self.desk.term
            removed, _ = await self.host.revoke(self.phone.did)
            self.assertTrue(removed)
            await self.until(lambda: self.desk.term is None)
            self.assertTrue(x.closing)
            self.assertIsNotNone(x.proc.returncode)                  # the shell is gone
            cfg = term_store.read_config(self.st)
            self.assertEqual([e for e in cfg["entries"] if e["device"] == self.phone.did], [])
            self.assertIsNone(term_store.meta(self.st, self.phone.did))
            n0 = len(self.sent)
            await self.desk.on_phone(self.s, {"t": "term_open", "r": R})
            res = [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"]
            self.assertTrue(not res or res[-1]["ok"] is False, res)
            self.assertTrue(not res or res[-1]["why"] in ("device_not_allowed", "no_passkey"), res)
            self.assertIsNone(self.desk.term)
        self.run_async(go)
        closes = self.msgs("term_close")
        self.assertTrue(closes and closes[-1]["why"] in ("relay_lost", "disabled"), closes)
        files = b"".join(p.read_bytes() for p in self.st.root.rglob("*") if p.is_file())
        self.assertNotIn(GOOD.encode(), files)


if __name__ == "__main__":
    unittest.main()
