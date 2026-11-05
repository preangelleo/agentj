"""0.26.9 (#463, ADR-0269-pty, PROTOCOL §38.15): the web terminal as a real PTY.

- a real PTY round trip: isatty, TERM / UTF-8, a raw-mode program gets the arrow key's bytes, resize → SIGWINCH, Ctrl+C reaches
  the foreground program, Ctrl+Z / jobs; the owner's start-up files are NOT read (w2-sec H-2) and `sudo` is the checked `-k -A`;
- the masker over a VT stream: escapes pass untouched, a key split by colour codes / frames / a PEM block is still hidden,
  OSC 52 dropped; the line-mode masker still strips (negative control);
- the Enter gate: a dangerous line (typed, completed with TAB, or the second line of a paste) waits for the signed answer;
  a wrong / missing signature, another device, typing over it or 120 s erase it; without the shell's report an uncertain line
  fails closed; harmless lines run at once;
- isolation: term_in / term_resize from another device refused; e-stop / the time limit kill the process group, a stopped
  job and a setsid'd child carrying the token; no secret env (with a visible control variable); the cast holds no input bytes
  and an echo-off answer is nowhere; the PTY slave is 0600 and never "a terminal at the computer" (ctl_auth); the Linux fence
  has its own devpts (negative control: the old argv opens the host's PTY).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import shutil
import string
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import ctl_auth, fence, term_askpass, term_pty, term_store, wire  # noqa: E402
from agentj.term_mask import Masker  # noqa: E402
from agentj.term_pty import VtMasker  # noqa: E402

from test_l1 import Phone, _ready  # noqa: E402
from test_p408_terminal import FA, GOOD, R, Rig, signed  # noqa: E402

TOK = "ghp_" + string.ascii_lowercase + string.digits + "AB"      # a stand-in key shape, built (no literal for scanners)
PY = sys.executable
LINUX = sys.platform.startswith("linux")


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


# ------------------------------------------------------------ the VT masker (pure)
class VtMask(unittest.TestCase):
    def run_chunks(self, chunks, flush=False):
        m, out = VtMasker(), ""
        for c in chunks:
            out += m.feed(c)
            if flush:
                out += m.flush()
        return out + m.close()

    def test_escapes_pass_through_untouched(self):
        text = "\x1b[?1049h\x1b[H\x1b[2J\x1b[1;31mred\x1b[0m plain\r\n\x1b]0;my title\x07\x1b[5;10Hx\x1b(B\x08\x07 end\r\n"
        self.assertEqual(self.run_chunks([text]), text)
        for cut in range(1, len(text)):                      # an escape cut by a read is held, never broken
            self.assertEqual(self.run_chunks([text[:cut], text[cut:]]), text, cut)

    def test_line_masker_still_strips_negative_control(self):
        m = Masker()
        self.assertEqual(m.feed("\x1b[1;31mred\x1b[0m\r\n") + m.close(), "red\n")

    def test_token_split_by_colour_codes(self):
        text = "key: " + TOK[:9] + "\x1b[1m" + TOK[9:20] + "\x1b[0m\x1b[32m" + TOK[20:] + "\x1b[0m done\r\n"
        out = self.run_chunks([text])
        for k in range(0, len(TOK) - 8, 4):
            self.assertNotIn(TOK[k:k + 8], out, k)
        self.assertIn("done", out)
        self.assertIn("\x1b[32m", out)                        # the escapes survive (after the masked text)

    def test_token_split_across_frames_and_escapes(self):
        text = "export X=" + TOK[:7] + "\x1b[0m" + TOK[7:30] + "\x1b[K" + TOK[30:] + "\r\nnext\r\n"
        for cut in range(1, len(text), 3):
            for flush in (False, True):
                out = self.run_chunks([text[:cut], text[cut:]], flush)
                self.assertNotIn(TOK[8:24], out, (cut, flush))
                self.assertNotIn(TOK[20:], out, (cut, flush))
                self.assertIn("next", out)

    def test_typed_key_seven_chars_at_a_time(self):
        m, out = VtMasker(), ""
        for i in range(0, len(TOK), 7):
            out += m.feed("\x1b[1m" + TOK[i:i + 7] + "\x1b[0m") + m.flush() + m.flush()
        out += m.feed(" ok\r\n") + m.close()
        self.assertNotIn(TOK[:8], out)
        self.assertTrue(out.endswith(" ok\r\n"), repr(out[-20:]))

    def test_pem_block_with_escapes(self):
        text = ("before\r\n\x1b[2m-----BEGIN OPENSSH PRIV\x1b[0mATE KEY-----\r\nb3BlbnNzaC1rZXktdjEAAAAA\r\n"
                "\x1b[31mQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\x1b[0m\r\n-----END OPENSSH PRIVATE KEY-----\r\nafter\r\n")
        for cut in (5, 30, 60, 100):
            out = self.run_chunks([text[:cut], text[cut:]])
            self.assertNotIn("b3BlbnNz", out)
            self.assertNotIn("QUJDREVG", out)
            self.assertIn("after", out)
            self.assertIn("私钥已隐藏", out)

    def test_osc52_dropped_and_osc_payload_masked(self):
        out = self.run_chunks(["a\x1b]52;c;" + "Zm9v" * 10 + "\x07b\x1b]0;" + TOK + "\x07c\r\n"])
        self.assertNotIn("]52;", out)
        self.assertNotIn(TOK, out)
        self.assertTrue(out.startswith("ab\x1b]0;") and out.endswith("c\r\n"), repr(out))

    def test_plain_for_the_transcript(self):
        self.assertEqual(term_pty.plain("\x1b[1mhi\x1b[0m\r\nyo\rz"), "hi\nyo\nz")


# ------------------------------------------------------------ the live PTY
@unittest.skipUnless(term_pty.pty_ok() and shutil.which("bash"), "needs a PTY platform and bash")
class Pty(Rig):
    def setUp(self):
        super().setUp()
        self.desk.pty_ok = True
        self.desk.pty_shell = "bash"
        (self.home / ".bashrc").write_text("export FROM_RC=1\nalias sudo='/usr/bin/sudo'\n")
        (self.home / ".bash_profile").write_text('[ -f ~/.bashrc ] && . ~/.bashrc\nexport PATH="$HOME/rc-bin:$PATH"\n')
        self.k = 0

    def script(self, name, body):
        p = self.work / name
        p.write_text(f"#!{PY}\n" + body)
        p.chmod(0o755)
        return p

    async def open_pty(self, cols=80, rows=24, s=None, ph=None):
        s, ph = s or self.s, ph or self.phone
        n0 = len(self.sent)
        await self.desk.on_phone(s, {"t": "term_open", "r": R, "mode": "pty", "cols": cols, "rows": rows})
        c1 = [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]
        self.assertEqual(c1["why"], "passkey_required", c1)
        await self.desk.on_phone(s, {"t": "term_open", "r": R, "mode": "pty", "cols": cols, "rows": rows, "fa": FA,
                                     **signed(ph, "term_open", {"mode": "pty"}, c1["n"])})
        while True:
            got = await self.until(lambda: next((o for o in self.sent_after(c1) if o.get("t") == "term_ready" or (
                o.get("t") == "ctl_res" and o.get("why") != "passkey_required") or (
                o.get("t") == "elev" and o.get("kind") == "login" and o["n"] not in self.used)), None), 30)
            if got["t"] == "elev":
                self.used.add(got["n"])
                await self.answer_login(got, GOOD)
                continue
            self.assertEqual(got["t"], "term_ready", got)
            x = self.desk.term
            await self.until(lambda: x.at_prompt, 20)
            return got

    async def typed(self, text, s=None):
        self.k += 1
        x = self.desk.term
        await self.desk.on_phone(s or self.s, {"t": "term_in", "id": x.id, "seq": self.k, "d": text})

    def out(self, n0=0):
        return "".join(o.get("d", "") for _, o in self.sent[n0:] if o.get("t") == "term_out")

    async def run_line(self, line, want, timeout=15):
        x = self.desk.term
        await self.until(lambda: x.at_prompt and x.hold is None, timeout)
        n0 = len(self.sent)
        await self.typed(line + "\r")
        await self.until(lambda: want in self.out(n0), timeout)
        return n0

    def confirm(self, n0):
        return next((o for _, o in self.sent[n0:] if o.get("t") == "term_confirm"), None)

    # -------------------------------------------------------- round trip
    def test_isatty_term_utf8_and_the_owner_files(self):
        self.passkey()
        self.enable()
        self.script("tty.py", "import os, sys\nprint('TTY', sys.stdin.isatty(), sys.stdout.isatty(), os.environ.get('TERM'),"
                              " 'UTF-8' in (os.environ.get('LC_ALL') or os.environ.get('LC_CTYPE') or os.environ.get('LANG') or ''),"
                              " os.environ.get('FROM_RC'), sep='|')\n")

        async def go():
            ready = await self.open_pty()
            self.assertEqual(ready["mode"], "pty")
            info = self.desk.info(self.phone.did)
            self.assertEqual(info["modes"], ["line", "pty"])
            self.assertIn("term_pty1", self.host._caps(self.s)["caps"])
            # w2-sec H-2: the owner's start-up files are NOT read (FROM_RC unset, no rc-bin on PATH, no sudo alias)
            await self.run_line("./tty.py", "TTY|True|True|xterm-256color|True|None")
            # (the owner masking hides some PATH values as an assignment, so test it in the shell)
            n0 = await self.run_line('declare -f sudo; [[ ":$PATH:" == *"/rc-bin:"* ]] && echo HAS-RC""-BIN || echo NO-RC""-BIN',
                                     "NO-RC-BIN")
            self.assertIn("-k -A", self.out(n0))               # the checked function is there
            x = self.desk.term
            mode = os.stat("/dev/" + ("pts/" if LINUX else "") + x.tty[1]).st_mode if LINUX else None
            if mode is not None:
                self.assertEqual(oct(mode & 0o777), "0o600")
        self.run_async(go)
        closes = self.msgs("term_close")
        self.assertEqual(closes[-1]["why"], "owner")

    def test_raw_keys_resize_and_ctrl_c(self):
        self.passkey()
        self.enable()
        self.script("raw.py", "import os, sys, tty\nfd = sys.stdin.fileno()\nold = __import__('termios').tcgetattr(fd)\n"
                              "tty.setraw(fd)\ntry:\n    os.write(1, b'KEY?')\n    b = os.read(fd, 3)\nfinally:\n"
                              "    __import__('termios').tcsetattr(fd, 0, old)\nprint('\\nGOT', ','.join(map(str, b)))\n")
        self.script("winch.py", "import os, signal, sys, time\n"
                                "signal.signal(signal.SIGWINCH, lambda *a: print('WINCH', *os.get_terminal_size(), flush=True))\n"
                                "signal.signal(signal.SIGINT, lambda *a: (print('GOT-INT', flush=True), sys.exit(0)))\n"
                                "print('SIZE', *os.get_terminal_size(), flush=True)\nwhile True: time.sleep(0.1)\n")

        async def go():
            await self.open_pty(cols=90, rows=33)
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed("./raw.py\r")
            await self.until(lambda: "KEY?" in self.out(n0))
            await self.typed("\x1b[A")                        # Up arrow → the program's own bytes
            await self.until(lambda: "GOT 27,91,65" in self.out(n0))
            n0 = await self.run_line("./winch.py", "SIZE 90 33")
            await self.desk.on_phone(self.s, {"t": "term_resize", "id": x.id, "cols": 120, "rows": 40})
            await self.until(lambda: "WINCH 120 40" in self.out(n0))
            self.assertEqual((x.cols, x.rows), (120, 40))
            await self.desk.on_phone(self.s, {"t": "term_resize", "id": x.id, "cols": 9999, "rows": 40})
            self.assertEqual((x.cols, x.rows), (120, 40))      # out of range: refused
            await self.typed("\x03")                           # Ctrl+C: the line discipline → SIGINT to the program
            await self.until(lambda: "GOT-INT" in self.out(n0))
            await self.until(lambda: x.at_prompt)              # the shell lives on
        self.run_async(go)
        cast = next((term_store.root(self.st) / "casts").glob("*.cast"))
        rows = [json.loads(ln) for ln in cast.read_text().splitlines()]
        self.assertEqual((rows[0]["width"], rows[0]["height"], rows[0]["env"]["TERM"]), (90, 33, "xterm-256color"))
        self.assertIn([ "r", "120x40"], [r[1:] for r in rows[1:]])
        self.assertEqual({r[1] for r in rows[1:]} - {"o", "r"}, set())   # no input events at all

    def test_typed_echo_shows_at_once_but_a_slow_key_stays_hidden(self):
        self.passkey()
        self.enable()
        # the program prints a key 7 characters at a time; the owner typed only its reversed body, never the key itself
        self.script("slowtok.py", "import sys, time\ntok = 'ghp_' + sys.argv[1][::-1]\nfor i in range(0, len(tok), 7):\n"
                                  "    sys.stdout.write(tok[i:i + 7]); sys.stdout.flush(); time.sleep(0.4)\nprint(' done')\n")
        word = "Projects/Some_Long-Folder.name/QWERTY"           # (shares no 8-char run with TOK)

        async def go():
            await self.open_pty()
            n0 = len(self.sent)
            for c in "echo " + word:                          # typed one key at a time, no delimiter after the word
                await self.typed(c)
            await self.until(lambda: word in self.out(n0), 3)
            await self.typed("\x15")
            await asyncio.sleep(0.3)
            n0 = len(self.sent)
            await self.typed("./slowtok.py " + TOK[4:][::-1] + "\r")
            await self.until(lambda: "done" in self.out(n0), 15)
            out = self.out(n0)
            self.assertNotIn(TOK[4:12], out)
            self.assertNotIn(TOK[-8:], out)
        self.run_async(go)

    # -------------------------------------------------------- the Enter gate
    def test_dangerous_line_is_held_until_the_signed_answer(self):
        self.passkey()
        self.enable()
        victim = self.work / "junk"
        victim.mkdir()

        async def go():
            await self.open_pty()
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed("rm -rf ./junk\r")
            c = await self.until(lambda: self.confirm(n0))
            self.assertEqual(c["line"], "rm -rf ./junk")
            await asyncio.sleep(0.6)
            self.assertTrue(victim.exists())                   # held
            base = {"t": "term_line", "id": x.id, "seq": 2, "line": c["line"]}
            await self.desk.on_phone(self.s, {**base, **signed(self.phone, "term_line", {"id": x.id, "line": "rm -rf ./x"},
                                                               c["n"]), "fa": FA})          # signed for another line
            await asyncio.sleep(0.6)
            self.assertTrue(victim.exists())
            self.assertIsNone(x.hold)                          # erased (Ctrl+E Ctrl+U), not left half-typed
            n0 = len(self.sent)
            await self.typed("rm -rf ./junk\r")
            c = await self.until(lambda: self.confirm(n0))
            other = Phone(self.st, name="另一台")
            s2 = _ready(self.host, other, cid=12)
            s2.p33 = True
            await self.desk.on_phone(s2, {**base, **signed(other, "term_line", {"id": x.id, "line": c["line"]}, c["n"]), "fa": FA})
            await asyncio.sleep(0.4)
            self.assertTrue(victim.exists())                   # another device's answer does nothing
            self.assertEqual(x.hold, "confirm")
            await self.desk.on_phone(self.s, {**base, **signed(self.phone, "term_line", {"id": x.id, "line": c["line"]},
                                                               c["n"]), "fa": FA})
            await self.until(lambda: not victim.exists())
        self.run_async(go)
        log = self.st.controls_path.read_text()
        self.assertNotIn("rm -rf", log)                        # controls.log: digests, never the line

    def test_completed_line_comes_from_the_shell_and_typing_over_cancels(self):
        self.passkey()
        self.enable()
        victim = self.work / "junk2"
        victim.mkdir()

        async def go():
            await self.open_pty()
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed("rm -rf ./jun\t")
            await asyncio.sleep(0.4)
            await self.typed("\r")
            c = await self.until(lambda: self.confirm(n0))
            self.assertEqual(c["line"].rstrip("/ "), "rm -rf ./junk2")   # the shell's own line, not the keystrokes
            await self.typed("echo after-cancel\r")            # typing over the card = 「取消」
            await self.until(lambda: "after-cancel" in self.out(n0))
            await asyncio.sleep(0.4)
            self.assertTrue(victim.exists())
        self.run_async(go)

    def test_without_the_shell_report_an_uncertain_line_fails_closed(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open_pty()
            n0 = len(self.sent)
            with mock.patch.object(term_pty, "QUERY_KEY", ""), mock.patch.object(term_pty, "QUERY_WAIT", 0.2):
                await self.typed("echo hi\x1b[D\x1b[C\r")      # arrow keys: the host's copy is no longer certain
                c = await self.until(lambda: self.confirm(n0))
                self.assertIn("exact", c["why_en"])
                n0 = len(self.sent)
                await self.typed("\x15echo sure-ok\r")         # (typing over erased it) — a plain line, known exactly
                await self.until(lambda: "sure-ok" in self.out(n0))
                self.assertIsNone(self.confirm(n0))
        self.run_async(go)

    def test_paste_runs_harmless_lines_and_holds_a_dangerous_second_line(self):
        self.passkey()
        self.enable()
        victim = self.work / "junk3"
        victim.mkdir()

        async def go():
            await self.open_pty()
            n0 = len(self.sent)
            await self.typed("echo one-1\recho two-2\r")
            await self.until(lambda: "two-2" in self.out(n0).replace("echo two-2", ""))
            self.assertIsNone(self.confirm(n0))
            n0 = len(self.sent)
            await self.typed("echo three-3\rrm -rf ./junk3\r")
            c = await self.until(lambda: self.confirm(n0))
            self.assertEqual(c["line"], "rm -rf ./junk3")
            self.assertIn("three-3", self.out(n0).replace("echo three-3", ""))
            await asyncio.sleep(0.5)
            self.assertTrue(victim.exists())
        self.run_async(go)

    def test_cancel_erases_the_held_line(self):
        self.passkey()
        self.enable()
        victim = self.work / "junk5"
        victim.mkdir()

        async def go():
            await self.open_pty()
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed("rm -rf ./junk5\r")
            c = await self.until(lambda: self.confirm(n0))
            await self.desk.on_phone(self.s, {"t": "term_confirm_no", "id": x.id, "n": c["n"]})
            self.assertIsNone(x.hold)
            n0 = await self.run_line("echo after-no", "after-no")
            self.assertNotIn("rm -rf", self.out(n0).split("echo after-no")[-1])
            await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 3, "line": c["line"], "fa": FA,
                                              **signed(self.phone, "term_line", {"id": x.id, "line": c["line"]}, c["n"])})
            await asyncio.sleep(0.5)
            self.assertTrue(victim.exists())                   # a late signed answer is a replay
        self.run_async(go)

    def test_unanswered_confirm_expires_and_erases(self):
        self.passkey()
        self.enable()
        victim = self.work / "junk4"
        victim.mkdir()

        async def go():
            await self.open_pty()
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed("rm -rf ./junk4\r")
            c = await self.until(lambda: self.confirm(n0))
            x.confirms[c["n"]]["exp"] = self.desk.clock() - 1
            await self.desk.tick()
            self.assertIsNone(x.hold)
            await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 3, "line": c["line"], "fa": FA,
                                              **signed(self.phone, "term_line", {"id": x.id, "line": c["line"]}, c["n"])})
            await asyncio.sleep(0.5)
            self.assertTrue(victim.exists())
        self.run_async(go)

    # -------------------------------------------------------- isolation
    def test_other_device_input_and_resize_refused(self):
        self.passkey()
        self.enable()
        other = Phone(self.st, name="另一台")

        async def go():
            await self.open_pty()
            x = self.desk.term
            s2 = _ready(self.host, other, cid=12)
            s2.p33 = True
            await self.typed("touch ./pwned\r", s=s2)
            await self.desk.on_phone(s2, {"t": "term_resize", "id": x.id, "cols": 100, "rows": 30})
            await self.desk.on_phone(self.s, {"t": "term_in", "id": "0" * 16, "seq": 9, "d": "touch ./pwned2\r"})
            await self.desk.on_phone(self.s, {"t": "term_in", "id": x.id, "seq": 9, "d": "x" * (8 * 1024 + 1)})
            await self.run_line("echo still-here", "still-here")
            self.assertFalse((self.work / "pwned").exists())
            self.assertFalse((self.work / "pwned2").exists())
            self.assertEqual((x.cols, x.rows), (80, 24))
            self.assertNotIn("x" * 100, self.out())
        with mock.patch.object(self.st, "log", wraps=self.st.log) as lg:
            self.run_async(go)
        reasons = [c.kwargs.get("reason") for c in lg.call_args_list if c.args and c.args[0] == "control_refused"]
        self.assertIn("not_writer", reasons)
        self.assertIn("shape", reasons)

    def test_estop_and_time_limit_kill_group_jobs_and_token_children(self):
        self.passkey()
        self.enable()
        (self.work / "fg.sh").write_text("#!/bin/sh\necho $$ > c.pid\nexec sleep 1002\n")
        (self.work / "fg.sh").chmod(0o755)
        (self.work / "bg.sh").write_text("#!/bin/sh\necho $$ > b.pid\nexec sleep 1001\n")   # setsid: its own session
        (self.work / "bg.sh").chmod(0o755)
        clock = [1000.0]
        self.desk.clock = lambda: clock[0]

        async def start_jobs():
            await self.run_line("sleep 1000 & echo $! > a.pid; setsid ./bg.sh & echo jobs-ok", "jobs-ok")
            await self.until(lambda: (self.work / "b.pid").exists() and (self.work / "b.pid").read_text().strip())
            n0 = len(self.sent)
            await self.typed("./fg.sh\r")
            await self.until(lambda: (self.work / "c.pid").exists() and (self.work / "c.pid").read_text().strip())
            await asyncio.sleep(0.3)
            await self.typed("\x1a")                           # Ctrl+Z: a stopped job (job control is on)
            await self.until(lambda: "Stopped" in self.out(n0) or "stopped" in self.out(n0).lower())
            pids = [int((self.work / f"{k}.pid").read_text()) for k in "abc"]
            self.assertTrue(all(_alive(p) for p in pids), pids)
            for k in "abc":
                (self.work / f"{k}.pid").unlink()
            return pids

        async def go():
            await self.open_pty()
            pids = await start_jobs()
            await self.host.do_estop("terminal", "终端")
            await self.until(lambda: self.desk.term is None)
            await self.until(lambda: not any(_alive(p) for p in pids), 10)
            self.assertEqual(self.msgs("term_close")[-1]["why"], "estop")
        if not LINUX:
            self.skipTest("the sweep is checked through /proc here")
        self.run_async(go)

    def test_time_limit_close_kills_the_group(self):
        self.passkey()
        self.enable()
        clock = [1000.0]
        self.desk.clock = lambda: clock[0]

        async def go():
            await self.open_pty()
            await self.run_line("sleep 1000 & echo $! > a.pid; echo bg-ok", "bg-ok")
            pid = int((self.work / "a.pid").read_text())
            clock[0] += 15 * 60 + 1
            await self.desk.tick()
            await self.until(lambda: self.desk.term is None)
            await self.until(lambda: not _alive(pid), 10)
            self.assertEqual(self.msgs("term_close")[-1]["why"], "timeout")
        if not LINUX:
            self.skipTest("the sweep is checked through /proc here")
        self.run_async(go)

    def test_idle_needs_no_input_and_no_output(self):
        self.passkey()
        self.enable()
        clock = [1000.0]
        self.desk.clock = lambda: clock[0]

        async def go():
            await self.open_pty()
            x = self.desk.term
            await self.run_line("echo idle-1", "idle-1")
            clock[0] += 200
            n0 = len(self.sent)
            x.last = clock[0] - 200
            await self.desk._pty_out(x, "output\r\n", False)   # output alone keeps it open
            clock[0] += 200
            await self.desk.tick()
            self.assertIsNotNone(self.desk.term)
            clock[0] += 301
            await self.desk.tick()
            await self.until(lambda: self.desk.term is None)
            self.assertEqual(self.msgs("term_close")[-1]["why"], "idle")
            self.assertTrue(n0)
        self.run_async(go)

    def test_no_secret_env_but_the_ordinary_one(self):
        self.passkey()
        self.enable()
        fake = {"OPENAI_API_KEY": "sk-fake-for-test", "GH_TOKEN": "ghp-fake", "MY_SECRET": "x", "DB_PASSWORD": "x",
                "AGENTJ_STATE_DIR": "/nope", "LD_PRELOAD": "/nope.so", "AJ_PTY_VISIBLE": "visible-1"}

        async def go():
            await self.open_pty()
            await self.run_line("env > env.txt; echo env-ok", "env-ok")
        with mock.patch.dict(os.environ, fake):
            self.run_async(go)
        env = (self.work / "env.txt").read_text()
        self.assertIn("AJ_PTY_VISIBLE=visible-1", env)         # control: serve's ordinary environment did arrive
        for k in ("OPENAI_API_KEY", "GH_TOKEN", "MY_SECRET", "DB_PASSWORD", "AGENTJ_STATE_DIR", "LD_PRELOAD"):
            self.assertNotIn(k + "=", env, k)
        self.assertIn("AGENTJ_TERM_TOKEN=", env)
        self.assertNotIn("ZDOTDIR", env)

    def test_echo_off_answer_and_input_are_nowhere(self):
        self.passkey()
        self.enable()
        self.script("pw.py", "import getpass\nv = getpass.getpass('Secret: ')\nprint('LEN', len(v))\n")
        typed = "Typed-Answer-" + "q7" * 6

        async def go():
            await self.open_pty()
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed("./pw.py\r")
            await self.until(lambda: "Secret:" in self.out(n0))
            await asyncio.sleep(0.2)
            self.assertTrue(term_pty.password_mode(x.io.fd))
            await self.typed(typed + "\r")
            await self.until(lambda: f"LEN {len(typed)}" in self.out(n0))
            self.assertNotIn(typed, x.edit)
            self.assertNotIn(typed, "".join(x.tr))
        self.run_async(go)
        files = b"".join(p.read_bytes() for p in self.st.root.rglob("*") if p.is_file())
        self.assertNotIn(typed.encode(), files)                # cast, controls.log, host.log, history
        self.assertNotIn(typed, json.dumps([o for _, o in self.sent], ensure_ascii=False))

    def test_sudo_keeps_askpass_with_a_tty(self):
        self.passkey()
        self.enable()
        stub = pathlib.Path(self.desk.sudo)
        stub.write_text(stub.read_text().replace('printf \'%s\\n\' "$*" >> "$(dirname "$0")/args.log"',
                                                 'printf \'%s tty=%s\\n\' "$*" "$([ -t 0 ] && echo y || echo n)" >> "$(dirname "$0")/args.log"'))

        async def go():
            await self.open_pty()
            x = self.desk.term
            with mock.patch.object(term_askpass, "check_peer", return_value=(None, 77)), \
                    mock.patch.object(term_askpass, "proc_info", return_value={"argv": ["/usr/bin/sudo", "-k", "-A", "true"]}):
                n0 = len(self.sent)
                await self.typed("sudo true && echo sudo-ok\r")
                ask = await self.until(lambda: next((o for _, o in self.sent[n0:] if o["t"] == "term_sudo"), None))
                await self.desk.on_phone(self.s, {"t": "term_sudo_ok", "id": x.id, "rid": ask["rid"], "fa": FA,
                                                  **signed(self.phone, "term_sudo", {"id": x.id, "rid": ask["rid"]}, ask["n"])})
                await self.until(lambda: "sudo-ok" in self.out(n0).replace("echo sudo-ok", ""))
        self.run_async(go)
        calls = [ln for ln in (pathlib.Path(self.desk.sudo).parent / "args.log").read_text().splitlines() if "-A" in ln]
        self.assertEqual(calls, ["-k -A true tty=y"])          # a terminal is there, and still -A: the card, not keystrokes

    def test_web_pty_is_never_a_terminal_at_the_computer(self):
        if not LINUX:
            self.skipTest("Linux /proc check")
        m, sl = term_pty.open_pair(80, 24)
        p = subprocess.Popen(["sleep", "30"], stdin=sl, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(ctl_auth.has_tty(p.pid))           # control: an ordinary PTY on stdin counts
            term_pty.LIVE[os.fstat(sl).st_rdev] = os.path.basename(os.ttyname(sl))
            self.assertFalse(ctl_auth.has_tty(p.pid))
        finally:
            term_pty.LIVE.pop(os.fstat(sl).st_rdev, None)
            p.kill()
            p.wait()
            os.close(m)
            os.close(sl)

    @unittest.skipUnless(LINUX and shutil.which("bwrap"), "Linux fence")
    def test_fence_has_its_own_devpts(self):
        m, sl = term_pty.open_pair(80, 24)
        path = os.ttyname(sl)
        probe = [PY, "-I", "-c", "import os, sys\ntry:\n    os.close(os.open(sys.argv[1], os.O_RDWR | os.O_NOCTTY)); print('OPENED')\n"
                                 "except OSError as e:\n    print('REFUSED', e.errno)\nm, s = os.openpty(); print('OWN', os.ttyname(s))\n", path]
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        self.st.perm_dir.mkdir(mode=0o700, exist_ok=True)

        def run(private):
            fence.PRIVATE_DEV[0] = private
            try:
                a = fence.bwrap_argv(self.st, str(self.work), home=str(self.home), runtime="", environ=env)
            finally:
                fence.PRIVATE_DEV[0] = True
            r = subprocess.run(a + probe, capture_output=True, text=True, timeout=30, env=env)
            return r.stdout + r.stderr
        try:
            old = run(False)
            if "OPENED" not in old:
                self.skipTest("bwrap does not start here: " + old[-200:])
            self.assertIn("OPENED", old)                       # negative control: the host /dev lets the Agent open it
            new = run(True)
            self.assertIn("REFUSED", new, new)
            self.assertIn("OWN /dev/pts/", new, new)          # the fence's own PTYs still work
        finally:
            os.close(m)
            os.close(sl)


@unittest.skipUnless(term_pty.pty_ok() and shutil.which("zsh"), "needs zsh")
class ZshPty(Pty):
    """The same contract with zsh (macOS's login shell): ZDOTDIR hop through the owner's files, precmd marker, zle report."""

    def setUp(self):
        super().setUp()
        self.desk.pty_shell = "zsh"
        (self.home / ".zshrc").write_text("export FROM_RC=1\nalias sudo='/usr/bin/sudo'\n")
        (self.home / ".zprofile").write_text('export PATH="$HOME/rc-bin:$PATH"\n')

    test_raw_keys_resize_and_ctrl_c = test_paste_runs_harmless_lines_and_holds_a_dangerous_second_line = None
    test_unanswered_confirm_expires_and_erases = test_other_device_input_and_resize_refused = None
    test_estop_and_time_limit_kill_group_jobs_and_token_children = test_time_limit_close_kills_the_group = None
    test_idle_needs_no_input_and_no_output = test_echo_off_answer_and_input_are_nowhere = None
    test_web_pty_is_never_a_terminal_at_the_computer = test_fence_has_its_own_devpts = None
    test_without_the_shell_report_an_uncertain_line_fails_closed = None


class LineModeUnchanged(Rig):
    def test_pty_refused_when_unsupported_and_term_in_on_a_line_session(self):
        self.passkey()
        self.enable()
        self.desk.pty_ok = False

        async def go():
            n0 = len(self.sent)
            await self.desk.on_phone(self.s, {"t": "term_open", "r": R, "mode": "pty", "cols": 80, "rows": 24})
            self.assertEqual([o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]["why"], "os")
            self.assertEqual(self.desk.info(self.phone.did)["modes"], ["line"])
            self.assertNotIn("term_pty1", self.host._caps(self.s)["caps"])
            ready = await self.open()
            self.assertEqual(ready["mode"], "line")
            x = self.desk.term
            await self.desk.on_phone(self.s, {"t": "term_in", "id": x.id, "seq": 1, "d": "touch ./pwned\n"})
            await self.line("echo line-ok", "line-ok")
            self.assertFalse((self.work / "pwned").exists())
            self.assertTrue(all("data" in o for o in self.msgs("term_out")))
        self.run_async(go)

    def test_bad_sizes_refused(self):
        self.passkey()
        self.enable()
        self.desk.pty_ok = True

        async def go():
            for cols, rows in ((19, 24), (80, 201), ("80", 24), (None, None)):
                n0 = len(self.sent)
                await self.desk.on_phone(self.s, {"t": "term_open", "r": R, "mode": "pty", "cols": cols, "rows": rows})
                self.assertEqual([o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]["why"], "shape", (cols, rows))
        self.run_async(go)


if __name__ == "__main__":
    unittest.main()
