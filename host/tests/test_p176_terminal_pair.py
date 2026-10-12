"""P176: a pairing started in the terminal can be finished in the terminal — the phone's 6-digit safety code typed there.

Leo 10-12 (over SSH on the Huawei laptop): `agentj pair --link` on an account-bound host without an approval passphrase only
said "approve on the account page"; typing the phone's code went to the shell, 120 s later the phone was refused. Now the
terminal offers both ways at once (account-page passkey OR the code typed here, first one wins); serve accepts the code alone
only from a human's terminal (ctl_auth: not an Agent, stdin a terminal) on that bound host; a passphrase, when set, is still
required; a wrong code may be retried (3 tries); without a terminal it stays as before, with a hint.
"""
import _hermetic  # noqa: F401,I001
import argparse
import asyncio
import contextlib
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentj import cli, cloud, ctl_auth, gate, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402

PASS = "test-passphrase-p176"
LINK = {"api": "https://agentj.app/api", "host_id": "h_p176", "tenant": {"slug": "acme-co", "name": "Acme"},
        "linked_at": 1, "last_seq": 0, "via": "seat"}


class Base:
    def mkstate(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p176-", dir="/tmp")
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init()

    def bind(self):
        cloud.write_cloud(self.st, LINK)


# ---------------------------------------------------------------------------------------------------------------- serve
class ServeSide(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mkstate()
        self.bind()
        self.h = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.h.relay_up = True
        for name in ("send_app", "_op", "on_ready", "onboarding_paired", "_pk_offer", "_bulk", "broadcast"):
            setattr(self.h, name, AsyncMock(return_value=True))
        self.closed = []

        async def close_cid(cid, why, res=None):
            self.closed.append(why)
            self.h.sessions.pop(cid, None)
        self.h.close_cid = close_cid

    async def asyncTearDown(self):
        for s in self.h.sessions.values():
            if s.timer:
                s.timer.cancel()
        if self.h.pairing and self.h.pairing.timer:
            self.h.pairing.timer.cancel()
        self.tmp.cleanup()

    def pending(self):
        p = self.h.pairing      # the one `_ctl_pair` opened, else a fresh one
        if p is None:
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, None)
            self.h.pairing = p
        pub = os.urandom(32)
        s = serve.Session(cid=7, state="pending", pub=pub, device=wire.device_id(pub), name="P176 phone", h=os.urandom(32),
                          deadline=time.monotonic() + 60)
        p.cid = 7
        self.h.sessions[7] = s
        return p, s, wire.safety_code(s.h)

    def wrong(self, code):
        return f"{(int(code) + 1) % 1_000_000:06d}"

    async def test_terminal_code_ok_only_for_a_human_terminal_on_a_bound_host_without_passphrase(self):
        with patch.object(ctl_auth, "has_tty", return_value=True), patch.object(ctl_auth, "refusal", return_value=None):
            self.assertIs(self.h.terminal_code_ok(123), True)
        with patch.object(ctl_auth, "has_tty", return_value=False), patch.object(ctl_auth, "refusal", return_value=None):
            self.assertIs(self.h.terminal_code_ok(123), False, "no terminal on stdin (a pipe, a script)")
        with patch.object(ctl_auth, "has_tty", return_value=True), patch.object(ctl_auth, "refusal", return_value="agent"):
            self.assertIs(self.h.terminal_code_ok(123), False, "an Agent's process tree / harness never qualifies")
        self.h.relay_up = False
        self.assertIsNone(self.h.terminal_code_ok(123), "not the account route: the passphrase rules apply")
        self.h.relay_up = True
        gate.set_passphrase(self.st, PASS)
        self.assertIsNone(self.h.terminal_code_ok(123), "a passphrase is set: it is still required")
        self.st.approver_path.unlink()
        self.st.cloud_path.unlink()
        self.assertIsNone(self.h.terminal_code_ok(123), "unbound hosts always need the passphrase")

    async def test_terminal_code_alone_approves_and_is_logged(self):
        p, s, code = self.pending()
        res = await self.h.decide(p, code, None, terminal=True, retry=True)
        self.assertEqual(s.state, "ready", res)
        self.assertIn(s.device, self.st.devices())
        self.assertIn("pair_terminal_code", self.st.log_path.read_text())

    async def test_without_the_terminal_proof_no_passphrase_still_denies(self):
        p, s, code = self.pending()
        res = await self.h.decide(p, code, None)
        self.assertEqual(res, {"ev": "denied", "reason": "pass_not_set"})
        self.assertEqual(self.st.devices(), {})

    async def test_terminal_never_skips_a_set_passphrase(self):
        gate.set_passphrase(self.st, PASS)
        p, s, code = self.pending()
        self.assertEqual((await self.h.decide(p, code, None, terminal=True))["ev"], "pass_wrong")
        self.assertEqual(s.state, "pending")
        res = await self.h.decide(p, code, PASS, retry=True)
        self.assertEqual(s.state, "ready", res)

    async def test_wrong_code_retries_three_tries_then_denied(self):
        p, s, code = self.pending()
        bad = self.wrong(code)
        self.assertEqual(await self.h.decide(p, bad, None, terminal=True, retry=True), {"ev": "code_wrong", "left": 2})
        self.assertEqual(await self.h.decide(p, bad, None, terminal=True, retry=True), {"ev": "code_wrong", "left": 1})
        self.assertEqual(s.state, "pending")
        res = await self.h.decide(p, bad, None, terminal=True, retry=True)
        self.assertEqual(res, {"ev": "denied", "reason": "code_mismatch"})
        self.assertEqual(self.closed, ["code_mismatch"])
        self.assertEqual(self.st.devices(), {})

    async def test_without_retry_a_wrong_code_ends_at_once(self):
        """The admin page (no `retry`) keeps its one-shot rule."""
        gate.set_passphrase(self.st, PASS)
        p, s, code = self.pending()
        self.assertEqual((await self.h.decide(p, self.wrong(code), PASS))["reason"], "code_mismatch")

    async def test_timeout_still_ends_a_terminal_answer(self):
        p, s, code = self.pending()
        s.deadline = time.monotonic() - 1
        self.assertEqual(await self.h.decide(p, code, None, terminal=True), {"ev": "denied", "reason": "timeout"})
        self.assertEqual(self.st.devices(), {})

    # -------- the control stream itself (what `agentj pair` talks to)
    async def ctl(self):
        path = str(pathlib.Path(self.tmp.name) / "c.sock")
        srv = await asyncio.start_unix_server(lambda r, w: self.h._ctl_pair(r, w, 4242), path)
        r, w = await asyncio.open_unix_connection(path)

        async def send(obj):
            w.write((json.dumps(obj) + "\n").encode())
            await w.drain()

        async def take():
            return json.loads(await asyncio.wait_for(r.readline(), 5))
        first = await take()
        self.assertEqual(first["ev"], "link")
        return srv, w, send, take

    async def hang_up(self, srv, w):
        w.close()
        await asyncio.sleep(0.1)      # `_ctl_pair` sees the hang-up and ends before the loop closes
        srv.close()

    async def test_ctl_terminal_flag_from_a_real_terminal_approves(self):
        srv, w, send, take = await self.ctl()
        try:
            p, s, code = self.pending()
            with patch.object(ctl_auth, "has_tty", return_value=True), patch.object(ctl_auth, "refusal", return_value=None):
                await send({"cmd": "code", "code": self.wrong(code), "terminal": True, "retry": True})
                self.assertEqual(await take(), {"ev": "code_wrong", "left": 2})
                await send({"cmd": "code", "code": code, "terminal": True, "retry": True})
                self.assertEqual((await take())["ev"], "approved")
            self.assertEqual(s.state, "ready")
        finally:
            await self.hang_up(srv, w)

    async def test_ctl_terminal_flag_without_a_terminal_keeps_the_phone_waiting_for_the_account_page(self):
        srv, w, send, take = await self.ctl()
        try:
            p, s, code = self.pending()
            with patch.object(ctl_auth, "has_tty", return_value=False), patch.object(ctl_auth, "refusal", return_value=None):
                await send({"cmd": "code", "code": code, "terminal": True, "retry": True})
                self.assertEqual(await take(), {"ev": "code_refused", "why": "terminal"})
            self.assertEqual((s.state, self.st.devices()), ("pending", {}), "not approved, not refused")
            res = await self.h._grant_pair(s, source="account")     # the account page's passkey approval wins the race
            self.h._pair_finish(p, res)
            self.assertEqual((await take())["ev"], "approved")
            self.assertEqual(self.st.devices()[s.device]["source"], "account")
        finally:
            await self.hang_up(srv, w)

    async def test_ctl_without_the_terminal_flag_is_unchanged(self):
        """The admin page sends no `terminal`: a code without passphrase is refused as before (pass_not_set)."""
        srv, w, send, take = await self.ctl()
        try:
            p, s, code = self.pending()
            with patch.object(ctl_auth, "has_tty", return_value=True), patch.object(ctl_auth, "refusal", return_value=None):
                await send({"cmd": "code", "code": code})
                self.assertEqual(await take(), {"ev": "denied", "reason": "pass_not_set"})
            self.assertEqual(self.st.devices(), {})
        finally:
            await self.hang_up(srv, w)


class HasTty(unittest.TestCase):
    def test_macos_falls_back_to_the_controlling_terminal(self):
        def ps(out):
            return patch.object(ctl_auth.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, out, ""))
        with patch.object(ctl_auth.os, "readlink", side_effect=OSError), patch.object(ctl_auth.sys, "platform", "darwin"):
            with ps("ttys003\n"):
                self.assertTrue(ctl_auth.has_tty(4242))
            for out in ("??\n", "", "-\n"):
                with ps(out):
                    self.assertFalse(ctl_auth.has_tty(4242), out)
            self.assertFalse(ctl_auth.has_tty(None))
        with patch.object(ctl_auth.os, "readlink", side_effect=OSError), patch.object(ctl_auth.sys, "platform", "linux"), \
                ps("pts/1\n"):
            self.assertFalse(ctl_auth.has_tty(4242), "Linux trusts /proc only")

    def test_a_real_pty_counts_and_a_pipe_does_not(self):
        import pty
        mfd, sfd = pty.openpty()
        try:
            p = subprocess.Popen(["sleep", "5"], stdin=sfd)
            try:
                self.assertTrue(ctl_auth.has_tty(p.pid))
            finally:
                p.kill()
                p.wait()
        finally:
            os.close(mfd)
            os.close(sfd)
        p = subprocess.Popen(["sleep", "5"], stdin=subprocess.PIPE)
        try:
            self.assertFalse(ctl_auth.has_tty(p.pid))
        finally:
            p.kill()
            p.wait()


# ---------------------------------------------------------------------------------------------------------------- terminal
class TTYIn:
    """A keyboard: lines are handed out one by one; `hold()` blocks the next read until release (a human not typing)."""

    def __init__(self, lines=(), hold=False):
        self.lines, self.ev = list(lines), threading.Event()
        if not hold:
            self.ev.set()

    def isatty(self):
        return True

    def readline(self):
        self.ev.wait(10)
        return (self.lines.pop(0) + "\n") if self.lines else ""

    def fileno(self):
        raise io.UnsupportedOperation


class ScriptServe:
    """A control socket: `status`, then one `pair` stream — link, pending, then `script(sent_line) → [events]` for each line
    the CLI sends; `later` = events pushed by serve on its own after a delay (account approval, timeout)."""

    def __init__(self, st, script=None, later=None, relay_up=True):
        self.st, self.script, self.later, self.relay_up, self.sent = st, script, later, relay_up, []
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
        lock = threading.Lock()

        def put(ev):
            with lock:
                f.write(json.dumps(ev).encode() + b"\n")
                f.flush()
        req = json.loads(f.readline() or b"{}")
        if req.get("cmd") == "status":
            put({"ok": True, "relay_up": self.relay_up, "sessions": []})
            c.close()
            return
        put({"ev": "link", "link": "https://m.agentj.app/#p=TEST", "expires": int(time.time()) + 300})
        put({"ev": "pending", "name": "Phone", "device": "d" * 16, "limit": 10, "deadline_in": 120})
        if self.later:
            delay, pushed = self.later

            def push():
                time.sleep(delay)
                with contextlib.suppress(OSError, ValueError):
                    put(pushed)
            threading.Thread(target=push, daemon=True).start()
        c.settimeout(5)
        try:
            while line := f.readline():
                req = json.loads(line)
                self.sent.append(req)
                for answer in (self.script(req) if self.script else []):
                    put(answer)
        except (OSError, ValueError):
            pass
        with contextlib.suppress(OSError):
            c.close()

    def close(self):
        self.sock.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.st.sock_path)


APPROVED = {"ev": "approved", "name": "Phone", "device": "d" * 16}


class Terminal(Base, unittest.TestCase):
    def setUp(self):
        self.mkstate()

    def tearDown(self):
        self.tmp.cleanup()

    def run_pair(self, stdin, serve_obj, secrets=()):
        out, err = io.StringIO(), io.StringIO()
        a = argparse.Namespace(no_qr=True, link=True, check=False, json=False)
        pw = list(secrets)
        try:
            # P165: P153's _run_prompting leaves with os._exit while a prompt still waits (a real terminal); in-process here
            # that would end the whole test run, so it becomes the SystemExit it stands for.
            def _exit(code):
                raise SystemExit(code)
            with patch.object(cli, "State", return_value=self.st), patch("sys.stdin", stdin), \
                    patch("getpass.getpass", side_effect=lambda prompt="": pw.pop(0) if pw else ""), \
                    patch.object(cli.os, "_exit", side_effect=_exit), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    cli.cmd_pair(a)
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            serve_obj.close()
        return code, out.getvalue(), str(code or "") + err.getvalue()

    def test_bound_no_passphrase_offers_both_ways_and_the_code_approves(self):
        self.bind()
        fs = ScriptServe(self.st, lambda req: [APPROVED])
        code, out, err = self.run_pair(TTYIn(["123456"]), fs)
        self.assertIn(code, (0, None), out + err)
        self.assertIn("两条路任选其一", out)
        self.assertIn("6 位安全码", out)
        self.assertIn("Either way", out)
        self.assertIn(cloud.app_url(self.st), out)
        self.assertIn("✓ 已批准", out)
        self.assertEqual(fs.sent, [{"cmd": "code", "code": "123456", "terminal": True, "retry": True}])
        self.assertFalse(gate.is_set(self.st))

    def test_wrong_code_retries_then_succeeds(self):
        self.bind()
        answers = iter([[{"ev": "code_wrong", "left": 2}], [{"ev": "code_wrong", "left": 1}], [APPROVED]])
        fs = ScriptServe(self.st, lambda req: next(answers))
        code, out, err = self.run_pair(TTYIn(["111111", "abc", "222222", "333333"]), fs)
        self.assertIn(code, (0, None), out + err)
        self.assertIn("还能再试 2 次", out)
        self.assertIn("还能再试 1 次", out)
        self.assertIn("这次不算错", out, "a non-6-digit line is asked again, not sent")
        self.assertEqual([r["code"] for r in fs.sent], ["111111", "222222", "333333"])

    def test_three_wrong_codes_end_with_how_to_start_again(self):
        self.bind()
        answers = iter([[{"ev": "code_wrong", "left": 2}], [{"ev": "code_wrong", "left": 1}],
                        [{"ev": "denied", "reason": "code_mismatch"}]])
        fs = ScriptServe(self.st, lambda req: next(answers))
        code, out, err = self.run_pair(TTYIn(["111111", "222222", "333333"]), fs)
        self.assertNotIn(code, (0, None))
        self.assertIn("安全码不一致", err)
        self.assertIn("agentj pair --link", err)

    def test_timeout_explains_how_to_start_again(self):
        self.bind()
        kb = TTYIn(hold=True)
        fs = ScriptServe(self.st, later=(0.3, {"ev": "denied", "reason": "timeout"}))
        code, out, err = self.run_pair(kb, fs)
        kb.ev.set()
        self.assertNotIn(code, (0, None))
        self.assertIn("120 秒内没有批准", err)
        self.assertIn("agentj pair", err)
        self.assertIn("open the new link", err)
        self.assertEqual(fs.sent, [])

    def test_account_page_approval_wins_while_the_terminal_waits_for_the_code(self):
        self.bind()
        kb = TTYIn(hold=True)
        fs = ScriptServe(self.st, later=(0.3, APPROVED))
        code, out, err = self.run_pair(kb, fs)
        kb.ev.set()
        self.assertIn(code, (0, None), out + err)
        self.assertIn("✓ 已批准", out)
        self.assertEqual(fs.sent, [], "the code prompt ended by itself; nothing sent")

    def test_serve_refusing_the_terminal_keeps_waiting_for_the_account_page(self):
        self.bind()
        fs = ScriptServe(self.st, lambda req: [{"ev": "code_refused", "why": "terminal"}], later=(0.5, APPROVED))
        code, out, err = self.run_pair(TTYIn(["123456"]), fs)
        self.assertIn(code, (0, None), out + err)
        self.assertIn("这个终端不能直接批准", out)
        self.assertIn("✓ 已批准", out)

    def test_enter_alone_refuses(self):
        self.bind()
        fs = ScriptServe(self.st, lambda req: [{"ev": "denied", "reason": "denied"}])
        code, out, err = self.run_pair(TTYIn([""]), fs)
        self.assertNotIn(code, (0, None))
        self.assertEqual(fs.sent, [{"cmd": "code", "code": ""}])

    def test_passphrase_set_on_a_bound_host_still_asks_for_it(self):
        self.bind()
        gate.set_passphrase(self.st, PASS)
        answers = iter([[{"ev": "pass_wrong", "left": 4}], [{"ev": "code_wrong", "left": 2}], [APPROVED]])
        fs = ScriptServe(self.st, lambda req: next(answers))
        code, out, err = self.run_pair(TTYIn(["123456", "654321"]), fs, secrets=["nope-nope", PASS])
        self.assertIn(code, (0, None), out + err)
        self.assertNotIn("两条路任选其一", out)
        self.assertIn("也可以直接到账户页批准", out)
        self.assertIn("批准口令不对", out)
        self.assertEqual(fs.sent, [{"cmd": "code", "code": "123456", "pass": "nope-nope", "retry": True},
                                   {"cmd": "code", "code": "123456", "pass": PASS, "retry": True},
                                   {"cmd": "code", "code": "654321", "pass": PASS, "retry": True}])
        self.assertTrue(all("terminal" not in r for r in fs.sent), "with a passphrase the terminal flag is never used")

    def test_unbound_with_passphrase_is_refused_for_the_seat(self):
        # P165 (P185): an unbound computer cannot pair at all — a passphrase is no seat; nothing reaches serve's pair stream
        gate.set_passphrase(self.st, PASS)
        fs = ScriptServe(self.st, lambda req: [APPROVED])
        code, out, err = self.run_pair(TTYIn(["123456"]), fs, secrets=[PASS])
        self.assertNotIn(code, (0, None), out + err)
        self.assertIn("席位", out + err)
        self.assertEqual(fs.sent, [])

    def test_no_terminal_falls_back_to_the_account_page_with_a_hint(self):
        self.bind()
        fs = ScriptServe(self.st, later=(0.3, APPROVED))
        code, out, err = self.run_pair(io.StringIO("123456\n"), fs)
        self.assertIn(code, (0, None), out + err)
        self.assertIn("通行密钥确认", out)
        self.assertIn("不是交互终端", out)
        self.assertEqual(fs.sent, [], "a pipe never sends a code")


class Docs(unittest.TestCase):
    """Every surface tells the same SSH story: start and finish in the same terminal, never through an Agent."""

    def test_help_skill_install_site_and_identity_describe_the_terminal_route(self):
        from agentj import main_identity
        host = HERE.parent
        skill = (host / "agentj/skills/agentj-pair/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("agentj pair --link", skill)
        self.assertIn("6-digit code at the terminal prompt", skill)
        guide = host.parent / "install.md"
        if not guide.is_file():
            guide = host.parents[1] / "documentation/product/install.md"
        install = guide.read_text(encoding="utf-8")
        self.assertIn("Recommended over SSH (0.20.0)", install)
        self.assertIn("终端里发起、终端里完成配对", install)
        site = host.parent / "site/content/docs/04-phone"
        if site.is_dir():   # the public export ships host/ without site/
            self.assertIn("在终端里发起，也在终端里完成", (site / "zh.md").read_text(encoding="utf-8"))
            self.assertIn("start in the terminal, finish in the terminal", (site / "en.md").read_text(encoding="utf-8"))
        self.assertIn("6 位安全码", main_identity.ONBOARDING_LINE["zh"])
        self.assertIn("6-digit", main_identity.ONBOARDING_LINE["en"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(sys, "argv", ["agentj", "pair", "--help"]):
            with self.assertRaises(SystemExit):
                cli.main()
        self.assertIn("6 位安全码", out.getvalue())
        self.assertIn("--link", out.getvalue())


if __name__ == "__main__":
    unittest.main()
