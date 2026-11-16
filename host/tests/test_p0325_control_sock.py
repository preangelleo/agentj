"""0.30.25 P0 (Olares 11-16): a 0-byte, mode 0444 regular file on <state>/control.sock stopped every serve start
(PermissionError from the "already running?" probe; systemd gave up after 20 restarts, the rollback too).

Source: a fence built for another state (a host test's throw-away state on the same uid; a second instance) lists every
listening same-uid socket from /proc/net/unix and hid this serve's control.sock with `--ro-bind /dev/null <path>`. When
the serve had just removed its socket (restart), bwrap made the missing mount point on the real disk: 0 bytes, 0444.

- serve: anything left on the path that no live serve answers is residue — moved aside (or removed) and serve starts;
  a live serve still refuses a second one.
- fence: another Agent J state's sockets are hidden by an empty tmpfs over that state folder (no file mount point),
  so a vanished socket leaves nothing behind; other programs' sockets keep the /dev/null bind.
Every case runs under a throw-away HOME / XDG dirs / state (never the real ~/.local/state).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import glob
import json
import os
import pathlib
import shutil
import signal
import socket
import stat
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
from agentj import fence, names, serve  # noqa: E402
from agentj.state import State  # noqa: E402


class _Home(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-ctlsock-")
        self.home = pathlib.Path(self.dir) / "home"
        (self.home / ".config").mkdir(parents=True)
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "XDG_STATE_HOME": str(self.home / ".local/state"), "XDG_DATA_HOME": str(self.home / ".local/share"),
                    "XDG_CACHE_HOME": str(self.home / ".cache"), "AGENTJ_STATE_DIR": str(self.st.root)}
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(5)
            for f in (p.stdout, p.stderr):
                if f:
                    f.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self):
        p = subprocess.Popen([sys.executable, "-m", "agentj.cli", "serve", "--events", "quiet", "--no-stdin"], cwd=HOST,
                             env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def wait_up(self, p, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if p.poll() is not None:
                self.fail(f"serve exited rc={p.returncode}: {p.stderr.read()[-2000:]}")
            try:
                if stat.S_ISSOCK(os.lstat(self.st.sock_path).st_mode) and names.ctl_call(self.st, {"cmd": "status"}, 3):
                    return
            except (OSError, names.ServeBusy):
                pass
            time.sleep(0.1)
        self.fail("serve did not answer on control.sock")

    def stop(self, p):
        p.send_signal(signal.SIGTERM)
        p.wait(20)

    def log_events(self, ev):
        out = []
        try:
            for ln in self.st.log_path.read_text().splitlines():
                with __import__("contextlib").suppress(ValueError):
                    d = json.loads(ln)
                    if d.get("ev") == ev:
                        out.append(d)
        except FileNotFoundError:
            pass
        return out


class ServeStartsOverResidue(_Home):
    def test_readonly_empty_file_is_moved_aside_and_serve_starts(self):
        """The Olares shape: 0 bytes, 0444, regular file → serve starts; the file is kept as control.sock.stale-*."""
        sock = self.st.sock_path
        sock.write_bytes(b"")
        os.chmod(sock, 0o444)
        p = self.serve()
        self.wait_up(p)
        aside = glob.glob(str(sock) + ".stale-*")
        self.assertEqual(len(aside), 1, aside)
        a = os.lstat(aside[0])
        self.assertTrue(stat.S_ISREG(a.st_mode) and a.st_size == 0 and stat.S_IMODE(a.st_mode) == 0o444)
        self.assertEqual(stat.S_IMODE(os.lstat(sock).st_mode), 0o600)
        self.stop(p)
        self.assertEqual(p.returncode, 0, p.stderr.read()[-2000:])
        self.assertEqual([(e.get("kind"), e.get("status")) for e in self.log_events("control_sock_stale")], [("file", "moved")])

    def test_dead_socket_is_removed_and_serve_starts(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.st.sock_path))
        s.close()                                   # the file stays, nobody listens → ECONNREFUSED
        p = self.serve()
        self.wait_up(p)
        self.assertEqual(glob.glob(str(self.st.sock_path) + ".stale-*"), [])
        self.stop(p)

    def test_unreachable_socket_is_moved_aside(self):
        """A socket we may not connect to (mode 0000) is no serve of ours: moved aside, serve starts."""
        if os.getuid() == 0:
            self.skipTest("root ignores the socket mode")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.st.sock_path))
        s.listen(1)
        try:
            os.chmod(self.st.sock_path, 0)
            p = self.serve()
            self.wait_up(p)
            self.assertEqual(len(glob.glob(str(self.st.sock_path) + ".stale-*")), 1)
            self.stop(p)
        finally:
            s.close()

    def test_live_serve_still_refuses_a_second_one(self):
        """Negative control: a running serve keeps its socket; a second start exits with "already running"."""
        first = self.serve()
        self.wait_up(first)
        ino = os.lstat(self.st.sock_path).st_ino
        second = self.serve()
        second.wait(30)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already running", second.stderr.read())
        self.assertEqual(os.lstat(self.st.sock_path).st_ino, ino)
        self.assertEqual(glob.glob(str(self.st.sock_path) + ".stale-*"), [])
        self.assertIsNotNone(names.ctl_call(self.st, {"cmd": "status"}, 5))
        self.stop(first)


class ClaimUnit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-ctlsock-u-")
        self.sock = pathlib.Path(self.dir) / "control.sock"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def claim(self):
        seen = []
        asyncio.run(serve.claim_control_sock(self.sock, lambda ev, **kw: seen.append((ev, kw))))
        return seen

    def test_missing_path_is_untouched(self):
        self.assertEqual(self.claim(), [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_directory_and_symlink_are_moved_aside(self):
        for make in (lambda: self.sock.mkdir(), lambda: self.sock.symlink_to("/dev/null")):
            make()
            seen = self.claim()
            self.assertFalse(os.path.lexists(self.sock))
            self.assertEqual(seen[-1][1]["kind"], "file")
            for x in glob.glob(str(self.sock) + ".stale-*"):
                (os.rmdir if os.path.isdir(x) and not os.path.islink(x) else os.unlink)(x)

    def test_live_listener_raises_system_exit(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.sock))
        s.listen(1)
        try:
            with self.assertRaises(SystemExit):
                self.claim()
            self.assertTrue(stat.S_ISSOCK(os.lstat(self.sock).st_mode))
        finally:
            s.close()


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap"), "needs bubblewrap")
class FenceLeavesNoMountPoint(unittest.TestCase):
    """The source: another state's control.sock is listed, then vanishes before bwrap mounts (a serve restart)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-ctlsock-f-")
        self.home = os.path.join(self.dir, "home")
        self.work = os.path.join(self.home, "work")
        os.makedirs(self.work)
        self.st = State(pathlib.Path(self.dir) / "own")
        self.st.init(relay="ws://127.0.0.1:1")
        self.st.perm_dir.mkdir(mode=0o700, exist_ok=True)
        # another Agent J state, visible from this fence (inside the bound-back HOME, not under /tmp's tmpfs)
        self.other = os.path.join(self.home, ".local", "state", "agentj-other")
        os.makedirs(os.path.join(self.other, "agentperm"), mode=0o700)
        self.other_sock = os.path.join(self.other, "control.sock")
        self.plain_sock = os.path.join(self.home, "plain.sock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def argv(self, socks):
        with mock.patch.object(fence, "control_sockets", return_value=[fence._real(p) for p in socks]), \
                mock.patch.dict(os.environ, {"HOME": self.home}):
            return fence.bwrap_argv(self.st, self.work, home=self.home)

    def test_other_state_is_a_tmpfs_not_a_file_bind(self):
        a = self.argv([self.other_sock, self.plain_sock])
        pairs = list(zip(a, a[1:], a[2:]))
        other, plain = fence._real(self.other), fence._real(self.plain_sock)
        self.assertIn(("--tmpfs", other), list(zip(a, a[1:])))
        self.assertNotIn(("--ro-bind", "/dev/null", fence._real(self.other_sock)), pairs)
        self.assertIn(("--ro-bind", "/dev/null", plain), pairs, "other programs' sockets keep the /dev/null bind")

    def test_vanished_socket_leaves_no_file_on_disk(self):
        """Listed while there, gone when bwrap runs: before 0.30.25 bwrap left a 0-byte 0444 file at the path."""
        a = self.argv([self.other_sock])            # the socket is not on disk at mount time
        r = subprocess.run(a + ["/bin/true"], capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": self.home})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(self.other_sock), "no mount point left where the other serve binds next")

    def test_own_state_and_ancestors_are_not_foreign(self):
        self.assertIsNone(fence._foreign_state(str(self.st.sock_path), fence._real(str(self.st.root)), self.home, self.work))
        self.assertIsNone(fence._foreign_state(os.path.join(self.home, "x.sock"), "/nonexistent", self.home, self.work))
        self.assertEqual(fence._foreign_state(os.path.join(self.other, "agentperm", "perm.sock"), "/nonexistent", self.home,
                                              self.work), self.other)


if __name__ == "__main__":
    unittest.main()


class RecoveryAcknowledgedAfterStart(unittest.TestCase):
    """0.30.25 (Olares 11-16, second half): healed by `service restart` / the watchdog, the host kept the morning's
    `failed` recovery record; the next upgrade's health step then failed on `upgrade-restart`. A serve that is up under
    the service manager ends a completed failure; a rollback and a recovery in flight are left alone."""

    def setUp(self):
        from agentj import service_recovery
        self.sr = service_recovery
        self.dir = tempfile.mkdtemp(prefix="aj-ack-")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home)
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "XDG_CONFIG_HOME": os.path.join(self.home, ".config")})
        self.env.start()
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, status, age, reason=None):
        rec = {"status": status, "target": "0.30.25", "at": time.time() - age, **({"reason": reason} if reason else {})}
        (self.st.root / self.sr.RECORD).write_text(json.dumps(rec))
        (self.st.root / self.sr.NOTICE).write_text(json.dumps(rec))

    def test_failed_record_blocks_health_until_a_started_service_acknowledges_it(self):
        self.put("failed", 3600)
        self.assertEqual(self.sr.health(self.st, {"active": "active"})[0], "fail")      # the Olares upgrade blocker
        self.assertIsNotNone(self.sr.acknowledge_after_start(self.st, True))
        self.assertIsNone(self.sr.health(self.st, {"active": "active"}))
        self.assertEqual(self.sr.read(self.st)["status"], "ok")
        self.assertFalse((self.st.root / self.sr.NOTICE).exists())

    def test_stale_running_record_is_acknowledged(self):
        self.put("running", 600)
        self.assertIsNotNone(self.sr.acknowledge_after_start(self.st, True))
        self.assertEqual(self.sr.read(self.st)["status"], "ok")

    def test_left_alone_inactive_inflight_or_rolled_back(self):
        for status, age, reason, active in (("failed", 3600, None, False),       # not up under the service manager
                                            ("running", 10, None, True),         # a recovery worker still in flight
                                            ("rolled_back", 3600, "rolled_back", True)):   # stays a visible warning
            self.put(status, age, reason)
            self.assertIsNone(self.sr.acknowledge_after_start(self.st, active), status)
            self.assertEqual(self.sr.read(self.st)["status"], status)
