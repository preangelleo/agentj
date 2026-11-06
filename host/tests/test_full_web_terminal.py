"""#468/#375: paired mobile PTY, safe command prefill and UV-bound reconnect.

The rig owns its temporary shell/HOME/state; no live credentials or services are read.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import test_p0269_pty as pty_tests
from test_l1 import Phone, _ready
from test_p408_terminal import FA, R, signed
from agentj import controls, passkey, term_store, terminal


class FullWebTerminal(pty_tests.Pty):
    def mobile(self):
        d = self.st.devices()
        d[self.phone.did]["self_name"] = "网页 · iOS Safari"
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        self.st._devices_cache = None
        self.passkey()
        self.enable()

    async def prefill(self, cmd, seq=1, s=None):
        await self.desk.on_phone(s or self.s, {"t": "term_prefill", "id": self.desk.term.id, "cmd": cmd, "seq": seq})
        return self.msgs("term_prefill_res")[-1]

    async def resume(self, s, ph=None, target=None, fa=FA):
        target = target or {"mode": "pty", "resume": self.desk.term.id}
        obj = {"t": "term_open", "r": R, "cols": 100, "rows": 30, **target}
        await self.desk.on_phone(s, obj)
        c = self.msgs("ctl_res")[-1]
        if c.get("why") != "passkey_required":
            return c
        await self.desk.on_phone(s, {**obj, "fa": fa, **signed(ph or self.phone, "term_open", target, c["n"])})
        return self.msgs()[-1]

    def test_mobile_pty_and_prefill_never_runs_without_enter(self):
        self.mobile()
        async def go():
            await self.open_pty(cols=38, rows=18)
            x = self.desk.term
            self.assertTrue(self.desk.info(self.phone.did)["mobile"])
            self.assertTrue((await self.prefill("touch ./owner-entered"))["ok"])
            await asyncio.sleep(0.15)
            self.assertFalse((self.work / "owner-entered").exists())
            self.assertEqual(x.edit, "touch ./owner-entered")
            self.assertFalse((await self.prefill("echo should-not-append"))["ok"])
            await self.typed("\r")
            await self.until(lambda: (self.work / "owner-entered").exists())
        self.run_async(go)

    def test_prefill_refuses_control_unicode_program_and_password(self):
        self.mobile()
        async def go():
            await self.open_pty()
            x = self.desk.term
            for cmd in ("echo ok\nwhoami", "echo x\r", "echo x\t", "\x1b[A", "x\x7f", "x\x85", "x\x9b", "x\u2028y", "x\u2029y", "x\u200by"):
                self.assertEqual((await self.prefill(cmd))["why"], "shape")
                self.assertEqual(x.edit, "")
            with mock.patch.object(self.desk, "shell_front", return_value=False):
                self.assertEqual((await self.prefill("touch ./bad"))["why"], "busy")
            with mock.patch.object(terminal, "password_mode", return_value=True):
                self.assertEqual((await self.prefill("touch ./bad"))["why"], "busy")
            with mock.patch("agentj.term_pty.proc_name", return_value=None):
                self.assertEqual((await self.prefill("touch ./bad"))["why"], "busy")
            with mock.patch("agentj.term_pty.os.tcgetpgrp", side_effect=OSError()):
                self.assertEqual((await self.prefill("touch ./bad"))["why"], "busy")
            x.at_prompt = False
            self.assertEqual((await self.prefill("touch ./bad"))["why"], "busy")
            self.assertFalse((self.work / "bad").exists())
        self.run_async(go)

    def test_prefill_checks_expiry_revocation_estop_before_tick(self):
        self.mobile()
        async def go():
            await self.open_pty()
            x = self.desk.term
            old = x.expires
            x.expires = self.desk.clock() - 1
            self.assertEqual((await self.prefill("echo no"))["why"], "timeout")
            x.expires = old
            with mock.patch.object(self.host, "stopped", return_value=True):
                self.assertEqual((await self.prefill("echo no"))["why"], "estop")
            term_store.write_config(self.st, {"on": False, "entries": [], "minutes": 15})
            self.assertEqual((await self.prefill("echo no"))["why"], "disabled")
            self.assertEqual(x.edit, "")
        self.run_async(go)

    def test_reconnect_keeps_process_and_expiry_but_requires_fresh_bound_uv(self):
        self.mobile()
        async def go():
            await self.open_pty()
            await self.run_line("echo BEFORE-DROP", "BEFORE-DROP")
            x = self.desk.term
            pid, end = x.proc.pid, x.expires
            self.host._detach(self.s.cid)
            self.assertIsNotNone(x.disconnected_at)
            self.assertEqual(x.viewers, [])
            self.assertFalse(self.desk._pty_write(x, "touch ./disconnected-command\r"))
            self.assertEqual(x.pending, "")
            await self.typed("touch ./disconnected-command\r")
            self.assertFalse((self.work / "disconnected-command").exists())
            s2 = _ready(self.host, self.phone, cid=13)
            s2.p33 = True
            obj = {"t": "term_open", "r": R, "mode": "pty", "resume": x.id, "cols": 90, "rows": 25}
            await self.desk.on_phone(s2, obj)
            c = self.msgs("ctl_res")[-1]
            self.assertEqual(c["why"], "passkey_required")
            # A normal open's signature cannot authorize attachment to an existing PTY.
            await self.desk.on_phone(s2, {**obj, "fa": FA, **signed(self.phone, "term_open", {"mode": "pty"}, c["n"])})
            self.assertEqual(self.msgs("ctl_res")[-1]["why"], "bad_signature")
            with mock.patch.object(passkey, "verify_assertion", side_effect=passkey.PasskeyError("bad", "sig")):
                await self.resume(s2)
            self.assertEqual(self.msgs("ctl_res")[-1]["why"], "passkey_failed")
            n0 = len(self.sent)
            await self.resume(s2)
            self.assertIs(self.desk.term, x)
            self.assertEqual((x.proc.pid, x.expires), (pid, end))
            self.assertIs(x.s, s2)
            self.assertEqual(self.msgs("term_ready")[-1]["resumed"], 1)
            self.assertIn("BEFORE-DROP", self.out(n0))
            self.assertEqual(x.writers, {self.phone.did})
            self.s = s2
            await self.run_line("echo AFTER-DROP", "AFTER-DROP")
        self.run_async(go)

    def test_reconnect_wrong_device_expired_and_revoked_refused(self):
        self.mobile()
        other = Phone(self.st, name="网页 · Android Chrome")
        so = _ready(self.host, other, cid=12)
        so.p33 = True
        self.passkey(other)
        self.enable(self.phone, other)
        async def go():
            await self.open_pty()
            x = self.desk.term
            self.host._detach(self.s.cid)
            self.assertEqual((await self.resume(so, ph=other))["why"], "not_open")
            s2 = _ready(self.host, self.phone, cid=13)
            s2.p33 = True
            x.disconnected_at = self.desk.clock() - terminal.RECONNECT_S - 1
            self.assertEqual((await self.resume(s2))["why"], "stale")
            await self.desk.tick()
            self.assertIsNone(self.desk.term)
            self.assertEqual(self.msgs("term_close")[-1]["why"], "relay_lost")
        self.run_async(go)

    def test_resume_nonce_cannot_change_terminal_id_and_bounded_replay(self):
        self.mobile()
        async def go():
            await self.open_pty()
            x = self.desk.term
            for _ in range(40):
                await self.desk._frame(x, "z" * 4000, False)
            self.assertLessEqual(x.replay_chars, terminal.REPLAY_CHARS)
            self.host._detach(self.s.cid)
            s2 = _ready(self.host, self.phone, cid=13)
            s2.p33 = True
            target = {"mode": "pty", "resume": x.id}
            obj = {"t": "term_open", "r": R, "cols": 80, "rows": 24, **target}
            await self.desk.on_phone(s2, obj)
            c = self.msgs("ctl_res")[-1]
            changed = {"mode": "pty", "resume": "0" * 16}
            await self.desk.on_phone(s2, {**obj, **changed, "fa": FA, **signed(self.phone, "term_open", changed, c["n"])})
            self.assertIn(self.msgs("ctl_res")[-1]["why"], ("not_open", "shape"))
            self.assertIsNone(x.viewers[0] if x.viewers else None)
            self.assertNotEqual(controls.object_digest("term_open", target), controls.object_digest("term_open", changed))
        self.run_async(go)

    def test_direct_program_yes_no_password_and_arrow_menu(self):
        self.mobile()
        self.script("questions.py", "import getpass, os, tty, termios\n"
                    "answer=input('CONTINUE y/n? ')\nprint('ANSWER', answer, flush=True)\n"
                    "secret=getpass.getpass('PASSWORD? ')\nprint('PASSWORD-LENGTH', len(secret), flush=True)\n"
                    "old=termios.tcgetattr(0); tty.setraw(0)\n"
                    "try:\n os.write(1,b'MENU-READY\\r\\n'); key=os.read(0,3)\n"
                    "finally:\n termios.tcsetattr(0,termios.TCSANOW,old)\n"
                    "print('MENU-DOWN', key == bytes([27,91,66]), flush=True)\n")
        async def go():
            await self.open_pty(cols=38, rows=18)
            x = self.desk.term
            n0 = await self.run_line("./questions.py", "CONTINUE y/n?")
            await self.typed("y\r")
            await self.until(lambda: "PASSWORD?" in self.out(n0))
            await self.until(lambda: terminal.password_mode(x.io.fd))
            hidden = "only-a-local-test-answer"
            await self.typed(hidden + "\r")
            await self.until(lambda: "MENU-READY" in self.out(n0))
            await self.typed("\x1b[B")
            await self.until(lambda: "MENU-DOWN True" in self.out(n0))
            self.assertIn("ANSWER y", self.out(n0))
            self.assertIn("PASSWORD-LENGTH " + str(len(hidden)), self.out(n0))
            self.assertNotIn(hidden, self.out(n0))
            await self.until(lambda: x.at_prompt)
            casts = list((term_store.root(self.st) / "casts").glob("*.cast"))
            self.assertTrue(casts)
            self.assertNotIn(hidden, casts[0].read_text())
        self.run_async(go)

    def test_native_nvim_queries_keep_color_and_secret_env_scrubbing(self):
        self.mobile()
        self.script("remote-env.py", "import os\n"
                    "assert os.environ.get('NVIM_NOTTYFAST') == '0'\n"
                    "assert os.environ.get('TERM') == 'xterm-256color'\n"
                    "assert os.environ.get('COLORTERM') == 'truecolor'\n"
                    "assert 'OPENAI_API_KEY' not in os.environ\n"
                    "print('REMOTE-NVIM-ENV-OK', flush=True)\n")
        async def go():
            await self.open_pty()
            await self.run_line("./remote-env.py", "REMOTE-NVIM-ENV-OK")
        with mock.patch.dict(os.environ, {"NVIM_NOTTYFAST": "0", "OPENAI_API_KEY": "clearly-fake-env-test-value"}):
            self.run_async(go)
            self.assertEqual(os.environ['NVIM_NOTTYFAST'], '0')

    @unittest.skipUnless(shutil.which("nvim") or shutil.which("vim"), "requires a Vim-family editor")
    def test_real_vim_family_insert_save_and_resize(self):
        self.mobile()
        editor = shutil.which("vim") or shutil.which("nvim")
        async def go():
            await self.open_pty(cols=80, rows=24)
            x = self.desk.term
            n0 = len(self.sent)
            await self.typed(editor + " -u NONE -i NONE -n ./editor-proof.txt\r")
            await self.until(lambda: x.alt)
            if os.path.basename(editor) == "nvim":
                # This host-only fixture has no browser: act as its native OSC11/status responder.
                import re
                colors = statuses = 0
                for _ in range(100):
                    out = self.out(n0)
                    want_colors = len(re.findall(r"\x1b\]11;\?(?:\x07|\x1b\\)", out))
                    want_statuses = out.count("\x1b[5n")
                    while colors < want_colors:
                        await self.desk.on_phone(self.s, {"t": "term_in", "id": x.id, "reply": True,
                                                        "d": "\x1b]11;rgb:2c2c/2b2b/2626\x1b\\"})
                        colors += 1
                    while statuses < want_statuses:
                        await self.desk.on_phone(self.s, {"t": "term_in", "id": x.id, "reply": True, "d": "\x1b[0n"})
                        statuses += 1
                    if colors and statuses:
                        break
                    await asyncio.sleep(.005)
            await self.desk.on_phone(self.s, {"t": "term_resize", "id": x.id, "cols": 38, "rows": 18})
            self.assertEqual((x.cols, x.rows), (38, 18))
            await self.typed("iPTY editor proof\x1b:wq\r")
            await self.until(lambda: (self.work / "editor-proof.txt").exists())
            await self.until(lambda: x.at_prompt and not x.alt)
            self.assertEqual((self.work / "editor-proof.txt").read_text().strip(), "PTY editor proof")
            self.assertIn("\x1b[", self.out(n0))
            self.assertNotIn("E1568", self.out(n0))
        self.run_async(go)

    @unittest.skipUnless(shutil.which("top"), "requires top")
    def test_real_top_interactive_quit(self):
        self.mobile()
        async def go():
            await self.open_pty(cols=80, rows=24)
            x = self.desk.term
            n0 = await self.run_line("top -d 0.2", "Tasks:")
            self.assertFalse(self.desk.shell_front(x))
            await self.typed("q")
            await self.until(lambda: x.at_prompt and self.desk.shell_front(x))
            self.assertIn("\x1b[", self.out(n0))
        self.run_async(go)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(FullWebTerminal(name) for name in FullWebTerminal.__dict__ if name.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
