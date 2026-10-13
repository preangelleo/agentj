"""P214: `agentj login --seat … / --install-code-file` takes the code out of whatever was pasted (the whole prompt or
command, spaces, CRLF, full-width hyphen); a text with no code still reaches the local refusal."""
import _hermetic  # noqa: F401,I001
import argparse
import pathlib
import secrets
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import cli  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk

AJI = "AJI-" + "0123456789abcdef" * 2


def read(text):
    return cli._read_seat_code(argparse.Namespace(seat=text, seat_file=None, install_code_file=None))


class PastedCode(unittest.TestCase):
    def test_install_code_in_a_whole_command_or_prompt(self):
        self.assertEqual(read("curl -fsSL https://agentj.app/install-assistant.sh | sh -s -- " + AJI + "\r\n"), AJI)
        self.assertEqual(read("安装码是：" + AJI + "（24 小时内有效）"), AJI)
        self.assertEqual(read("  " + AJI + "  "), AJI)

    def test_full_width_and_dash_variants(self):
        fw = "ＡＪＩ－" + "".join(chr(ord(c) + 0xFEE0) for c in AJI[4:])
        self.assertEqual(read(fw), AJI)
        self.assertEqual(read("AJI—" + AJI[4:]), AJI)

    def test_setup_code_in_the_seat_sentence(self):
        tok = "ajt_" + secrets.token_urlsafe(32)
        self.assertEqual(read(f"这台电脑用的是 Agent J 账号「acme」的一个席位，设置码是：{tok}（7 天内有效，只能用一次）"), tok)
        self.assertEqual(read(f"The seat setup code is {tok} (valid 7 days)."), tok)
        self.assertEqual(read(tok + "\n"), tok)

    def test_no_code_is_left_for_the_shape_check(self):
        self.assertEqual(read("  hello  "), "hello")
        self.assertEqual(read("ajt_short"), "ajt_short")


class PasteLogin(unittest.TestCase):
    """`agentj login` at a terminal (or --paste): the pasted text → the seat login; Enter → the binding-code flow."""
    def run_paste(self, text, *, name=None, forced=False, kept=None, typed=""):
        from unittest import mock
        st = mock.Mock(); st.agent_name.return_value = kept
        a = argparse.Namespace(seat=None, seat_file=None, install_code_file=None, name=name, paste=forced, api=None)
        seen = {}
        with mock.patch.object(cli, "_read_paste", return_value=text), \
                mock.patch.object(cli, "_seat_login", side_effect=lambda st_, a_: seen.update(seat=a_.seat, name=a_.name)), \
                mock.patch("builtins.input", return_value=typed):
            used = cli._paste_login(st, a, forced)
        return used, seen

    def test_enter_falls_back_to_the_binding_code(self):
        self.assertEqual(self.run_paste("\n"), (False, {}))

    def test_whole_prompt_with_install_code_keeps_the_name(self):
        used, seen = self.run_paste("请安装 Agent J。\n安装码是：" + AJI + "\n", kept="小J")
        self.assertTrue(used)
        self.assertEqual(seen, {"seat": AJI, "name": "小J"})

    def test_setup_code_leaves_the_name_to_the_seat_login(self):
        tok = "ajt_" + secrets.token_urlsafe(32)
        used, seen = self.run_paste(f"设置码是：{tok}（7 天内有效）\r\n")
        self.assertEqual(seen, {"seat": tok, "name": None})


    def test_no_code_is_refused_and_names_the_buttons(self):
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.run_paste("ABCD-EFGH")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("复制安装码", err.getvalue() + str(cm.exception))

    def test_forced_paste_with_nothing_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_paste("", forced=True)

    def test_login_help_and_device_text(self):
        src = (HERE.parent / "agentj" / "cli.py").read_text()
        self.assertIn('"--paste"', src)
        self.assertIn("电脑绑定码：", src)
        self.assertIn("#add-agent", src)
        self.assertIn("不是通行密钥转移码", src)



class DefaultName(unittest.TestCase):
    """P214 追加 5: `agentj login --seat -` without --name asks (Enter = kept name, else host name); never exits."""
    def choose(self, *, kept=None, host="devbox", typed=None, name=None):
        from unittest import mock
        st = mock.Mock(); st.agent_name.return_value = kept
        a = argparse.Namespace(name=name)
        with mock.patch("agentj.text.machine_name", return_value=host), \
                mock.patch("builtins.input", return_value=typed or ""):
            auto = cli._choose_name(st, a, ask=typed is not None)
        return a.name, auto

    def test_enter_takes_the_host_name(self):
        self.assertEqual(self.choose(typed=""), ("devbox", True))

    def test_enter_keeps_an_existing_name(self):
        self.assertEqual(self.choose(kept="小J", typed=""), ("小J", True))

    def test_typed_name_wins(self):
        self.assertEqual(self.choose(typed="  运营部 "), ("运营部", False))

    def test_not_a_terminal_uses_the_default(self):
        self.assertEqual(self.choose(), ("devbox", True))
        self.assertEqual(self.choose(host=None), ("Agent J", True))

    def test_given_name_untouched(self):
        self.assertEqual(self.choose(name="Wren"), ("Wren", False))

    def test_long_host_name_is_cut_and_numbering_fits(self):
        from agentj.text import new_agent_name_problem
        name, _ = self.choose(host="desktop-very-long-hostname-01")
        self.assertIsNone(new_agent_name_problem(name))
        self.assertTrue("desktop-very-long-hostname-01".startswith(name))
        n = cli._numbered(name, 2)
        self.assertIsNone(new_agent_name_problem(n))
        self.assertTrue(n.endswith(" 2"))


class SeatLoginNoName(unittest.TestCase):
    """The real CLI: `--seat -` with no --name binds under the host name; a clash on a name nobody typed is numbered."""
    def setUp(self):
        import os, tempfile
        sys.path.insert(0, str(HERE))
        from agentj.state import State
        self.dir = tempfile.mkdtemp(prefix="aj-p214n-", dir=TEST_TMP)
        self.env = {**os.environ, "AGENTJ_STATE_DIR": self.dir + "/s", "PYTHONPATH": str(HERE.parent)}
        self.env.pop("AGENTJ_API_URL", None); self.env.pop("AGENTJ_SERVICE_NAME", None)
        State(pathlib.Path(self.dir) / "s").init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def login(self, cp, tok):
        import subprocess
        return subprocess.run([sys.executable, "-m", "agentj.cli", "login", "--api", cp.url, "--seat", "-"], cwd=HERE.parent,
                              env=self.env, input=tok + "\n", capture_output=True, text=True, timeout=60)

    def test_no_name_binds_with_the_host_name(self):
        from fakecp import FakeCP
        from agentj.text import machine_name
        tok = "ajt_" + secrets.token_urlsafe(32)
        with FakeCP() as cp:
            cp.seat_tokens.add(tok)
            r = self.login(cp, tok)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("--name", r.stderr)
        self.assertEqual(cp.seat_binds[-1]["name"], cli._default_name(type("S", (), {"agent_name": lambda self: None})()))
        self.assertIn("添加遥控器", r.stdout, "the next step is said right after the bind")
        self.assertNotIn("后台服务已启动", r.stdout, "a test state directory never touches the real service")
        self.assertTrue(machine_name() is None or cp.seat_binds[-1]["name"])

    def test_clash_on_a_default_name_is_numbered(self):
        from fakecp import FakeCP
        tok = "ajt_" + secrets.token_urlsafe(32)
        with FakeCP() as cp:
            cp.seat_tokens.add(tok)
            cp.seat_script = ["taken"]
            r = self.login(cp, tok)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(cp.seat_binds), 2)
        self.assertNotEqual(cp.seat_binds[0]["name"], cp.seat_binds[1]["name"])


class AfterBind(unittest.TestCase):
    def run_after(self, env, status):
        import contextlib, io, os
        from unittest import mock
        from agentj import service
        calls = []
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(service, "status", return_value=status), \
                mock.patch.object(service, "start", side_effect=lambda st: calls.append("start") or {"name": "agentj"}), \
                mock.patch.object(service, "restart", side_effect=lambda: calls.append("restart") or {"name": "agentj"}), \
                mock.patch.object(cli.cloud, "app_url", return_value="https://agentj.app/account"), \
                contextlib.redirect_stdout(out):
            for k in [k for k in ("AGENTJ_STATE_DIR", "AGENTJ_SERVICE_NAME") if k not in env]:
                os.environ.pop(k, None)
            cli._after_bind(object())
        return calls, out.getvalue()

    def test_not_installed_is_installed_and_started(self):
        calls, out = self.run_after({}, {"kind": "systemd", "installed": False, "active": "inactive"})
        self.assertEqual(calls, ["start"])
        self.assertIn("https://agentj.app/account/", out)
        self.assertIn("agentj pair", out)

    def test_running_serve_is_restarted(self):
        calls, _ = self.run_after({}, {"kind": "systemd", "installed": True, "active": "active"})
        self.assertEqual(calls, ["restart"])

    def test_no_service_manager_says_the_command(self):
        calls, out = self.run_after({}, {"kind": "none", "installed": False, "active": "no_systemctl"})
        self.assertEqual(calls, [])
        self.assertIn("agentj service install", out)

    def test_custom_state_dir_never_touches_the_service(self):
        calls, out = self.run_after({"AGENTJ_STATE_DIR": "/var/tmp/x"}, {"kind": "systemd", "installed": True, "active": "active"})
        self.assertEqual(calls, [])
        self.assertIn("添加遥控器", out)


class PasteLoginPty(unittest.TestCase):
    """The real `agentj login` on a pseudo-terminal: the owner pastes the whole seat prompt (several lines, CRLF), types a
    name, and the computer joins — no --seat-file, no device code, the code never echoed."""
    def test_paste_whole_prompt_at_a_terminal(self):
        import os, pty, select, shutil, subprocess, tempfile, time
        sys.path.insert(0, str(HERE))
        from fakecp import FakeCP
        from agentj.state import State
        d = tempfile.mkdtemp(prefix="aj-p214-", dir=TEST_TMP)
        try:
            env = {**os.environ, "AGENTJ_STATE_DIR": d + "/s", "PYTHONPATH": str(HERE.parent)}
            env.pop("AGENTJ_API_URL", None)
            State(pathlib.Path(d) / "s").init(relay="ws://127.0.0.1:1")
            tok = "ajt_" + secrets.token_urlsafe(32)
            with FakeCP() as cp:
                cp.seat_tokens.add(tok)
                master, slave = pty.openpty()
                p = subprocess.Popen([sys.executable, "-m", "agentj.cli", "login", "--api", cp.url], cwd=HERE.parent, env=env,
                                     stdin=slave, stdout=slave, stderr=slave, close_fds=True)
                os.close(slave)
                out = b""

                def until(needle, t=30):
                    nonlocal out
                    end = time.time() + t
                    while needle not in out and time.time() < end:
                        if select.select([master], [], [], 0.2)[0]:
                            try:
                                out += os.read(master, 4096)
                            except OSError:
                                break
                    return needle in out
                self.assertTrue(until("> ".encode()), out.decode(errors="replace"))
                os.write(master, f"请用 curl 下载 install.md……\r这台电脑用的是一个席位，设置码是：{tok}（7 天内有效）\r需要我动手的时候跟我说。\r".encode())
                self.assertTrue(until("起个名字".encode()), out.decode(errors="replace"))   # P214 追加 5: asked after the code
                os.write(master, "运营部\r".encode())
                until("首次上报".encode())
                p.wait(30)
                until(b"\0", 1)
                os.close(master)
                text = out.decode(errors="replace")
                self.assertEqual(p.returncode, 0, text)
                self.assertNotIn(tok, text, "the pasted code is never echoed")
                self.assertNotIn("电脑绑定码：", text, "no device code when a code was pasted")
                self.assertIn("Agent 名「运营部」", text)
                self.assertEqual(cp.seat_binds[0]["token"], tok)
                self.assertEqual(len(cp.seat_binds), 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
