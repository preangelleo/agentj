"""P153 追加 3: `agentj pair` exits when the new device leaves — it never hangs on its own keyboard prompt.

Leo 10-12 09:5x: Chrome opened the link, the relay dropped it (4029, the P141 eviction fixed in the relay hotfix) and the terminal
printed 「✗ 手机断开了」 — then sat there until Ctrl-C. The 6-digit prompt runs input() on a daemon thread; on a real terminal a
thread still blocked in input() / getpass keeps Python from finishing sys.exit. These tests run the real CLI on a pseudo-terminal
against a fake serve that drops the device while a prompt is open: the process must exit by itself, non-zero, with the reason,
and the terminal must have its echo back (getpass turns it off).
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import pty
import select
import socket
import sys
import tempfile
import termios
import threading
import time
import unittest
import warnings

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))

from agentj import gate  # noqa: E402
from agentj.state import State  # noqa: E402

PASS = "test-passphrase-p153"
LIMIT = 15.0   # seconds; the fixed CLI exits in about one; the old one waited for Ctrl-C


class FakeServe:
    """status → relay up; pair → link, pending, then (once the CLI is at the prompt) `last` — the device leaves."""

    def __init__(self, st, last, after_line=False):
        self.st, self.last, self.after_line = st, last, after_line
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(st.sock_path))
        self.sock.listen(8)
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.one, args=(c,), daemon=True).start()

    def one(self, c):
        f = c.makefile("rwb")
        req = json.loads(f.readline() or b"{}")
        if req.get("cmd") == "status":
            f.write(json.dumps({"ok": True, "relay_up": True, "sessions": []}).encode() + b"\n")
        elif req.get("cmd") == "pair":
            for ev in ({"ev": "link", "link": "https://m.agentj.app/#p=TEST", "expires": int(time.time()) + 300},
                       {"ev": "pending", "name": "Chrome", "device": "d" * 16, "limit": 5, "deadline_in": 300}):
                f.write(json.dumps(ev).encode() + b"\n")
                f.flush()
            time.sleep(1.0)                      # the CLI is now blocked at the 6-digit prompt (or, after_line, the passphrase)
            f.write(json.dumps(self.last).encode() + b"\n")
            f.flush()
            time.sleep(0.5)
        f.flush()
        c.close()

    def close(self):
        self.sock.close()
        try:
            os.unlink(self.st.sock_path)
        except FileNotFoundError:
            pass


def run_on_pty(state_dir: str, typed: bytes = b"") -> tuple[int | None, str, float, bool]:
    """→ (exit status or None when it hung, output, seconds, terminal echo on afterwards)."""
    env = {**os.environ, "AGENTJ_STATE_DIR": state_dir, "PYTHONPATH": str(HOST)}
    with warnings.catch_warnings():               # the child only execs: forking beside the fake serve's threads is fine
        warnings.simplefilter("ignore", DeprecationWarning)
        pid, fd = pty.fork()
    if pid == 0:
        os.chdir(str(HOST))
        os.execvpe(sys.executable, [sys.executable, "-c", "from agentj.cli import main; main()", "pair", "--no-qr", "--link"], env)
    t0, out, status, sent = time.monotonic(), b"", None, False
    while time.monotonic() - t0 < LIMIT:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                d = b""
            out += d
        if typed and not sent and "6 位安全码".encode() in out:
            os.write(fd, typed)
            sent = True
        w, st = os.waitpid(pid, os.WNOHANG)
        if w:
            status = os.waitstatus_to_exitcode(st)
            break
    echo = True
    try:
        echo = bool(termios.tcgetattr(fd)[3] & termios.ECHO)
    except termios.error:
        pass
    if status is None:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    os.close(fd)
    return status, out.decode(errors="replace"), time.monotonic() - t0, echo


class PairExits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p153x-", dir="/tmp")
        self.dir = str(pathlib.Path(self.tmp.name) / "s")
        self.st = State(pathlib.Path(self.dir))
        self.st.init(relay="ws://127.0.0.1:9")   # P165: a loopback relay is P185's local/test seat exemption (route unchanged)
        gate.set_passphrase(self.st, PASS)     # unbound + passphrase: the local route, which prompts for the 6 digits

    def tearDown(self):
        self.tmp.cleanup()

    def pair(self, last, typed=b""):
        fs = FakeServe(self.st, last)
        try:
            return run_on_pty(self.dir, typed)
        finally:
            fs.close()

    def test_device_gone_at_the_code_prompt_exits_nonzero_with_the_reason(self):
        status, out, secs, _ = self.pair({"ev": "gone"})
        self.assertIsNotNone(status, f"agentj pair hung after the device left ({secs:.1f} s):\n{out}")
        self.assertNotEqual(status, 0, out)
        self.assertIn("手机断开了", out)
        self.assertIn("重新运行 agentj pair", out)
        self.assertLess(secs, 8, out)

    def test_denied_gone_reads_as_a_reason_not_a_dict(self):
        status, out, _, _ = self.pair({"ev": "denied", "reason": "gone"})
        self.assertIsNotNone(status, out)
        self.assertNotEqual(status, 0, out)
        self.assertIn("手机断开了", out)
        self.assertNotIn("'reason'", out)

    def test_device_gone_at_the_passphrase_prompt_exits_and_gives_the_echo_back(self):
        status, out, secs, echo = self.pair({"ev": "gone"}, typed=b"123456\r")
        self.assertIn("批准口令", out, "reached the passphrase prompt (getpass: echo off)")
        self.assertIsNotNone(status, f"agentj pair hung at the passphrase prompt ({secs:.1f} s):\n{out}")
        self.assertNotEqual(status, 0, out)
        self.assertIn("手机断开了", out)
        self.assertTrue(echo, "the terminal was left with echo off")

    def test_approved_still_exits_zero(self):
        status, out, _, _ = self.pair({"ev": "approved", "name": "Chrome", "device": "d" * 16})
        self.assertEqual(status, 0, out)
        self.assertIn("✓ 已批准", out)


if __name__ == "__main__":
    unittest.main()
