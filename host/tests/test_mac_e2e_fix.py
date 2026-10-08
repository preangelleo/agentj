"""Fixes from a real end-to-end install on a Mac (an AI driving install.md):

- `agentj handover [--lang zh|en]`: the Step 13 note with this computer's facts; read-only (no migration, nothing created).
- `agentj docs-rule --write --harness …`: the program asks y/N itself; a pipe never writes; append once; no symlinks.
- `agentj pair --link`: the iPhone line; the half-block QR fits in 80 columns and maps back to the exact module matrix.
- `agentj feedback check`: the server's required fields / shape before anything is written; a plain exit-3 message.
- macOS service: `launchctl enable` only for a disabled label, and the disabled state put back on uninstall.
- `agentj doctor`: an Agent login that is only an environment variable is a `!` (the background service cannot see it).
Every test runs under a temp HOME / state dir; nothing touches the real ~/.local/state, ~/.claude or launchd.
Run (in host/): .venv/bin/python -m unittest discover -s tests -p test_mac_e2e_fix.py
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import argparse
import contextlib
import io
import json
import os
import pathlib
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import cli, cloud, docsrule, doctor, feedback, handover, privacy, service, wire  # noqa: E402
from agentj.state import DEFAULT_RELAY, DEFAULT_WEB, State  # noqa: E402

SECRET = "sk-" + "ant-oat01-" + "AJMACFIX" + "x" * 20   # fake token shape, assembled so scanners do not flag the source
LABELS_ZH = ("「全部停下」", "「恢复」", "「定时任务」", "≡「全部命令」", "「长按批准」", "「管理账单」")   # P33: the new phone page
LABELS_EN = ('"Stop everything"', '"Resume"', '"Schedules"', '≡ "All commands"', '"Hold to approve"', '"Manage billing"')


def _env(home: str, **extra) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTJ_", "AGENTJARVIS_"))}
    e.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
              "AGENTJ_SERVICE_NAME": f"agentjarvis-test-{secrets.token_hex(4)}", **extra})
    return e


def _cli(home: str, *args, env=None, stdin=subprocess.DEVNULL):
    return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env={**_env(home), **(env or {})},
                          capture_output=True, text=True, timeout=60, stdin=stdin)


def _tree(root: str) -> list[str]:
    return sorted(os.path.relpath(os.path.join(d, f), root) for d, ds, fs in os.walk(root) for f in ds + fs)


def _bind(st: State, slug: str = "lin-ka7q") -> None:
    cloud.write_cloud(st, {"api": "https://agentj.app/api", "host_id": "h_" + "a" * 20,
                           "tenant": {"slug": slug, "name": slug}, "linked_at": 1, "last_seq": 0, "via": "seat"})


# ------------------------------------------------------------------ 1. handover
class Handover(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-ho-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)

    def check_common(self, text: str, lang: str):
        for must in ("m.agentj.app", "agentj passphrase reset", "agentj revoke", "agentj devices", "agentj update check",
                     "agentj update apply", "https://agentj.app/account", "https://agentj.app/docs/", "founder@agentj.app",
                     "agentj pair --link", "agentj resume", "agentj service uninstall"):
            self.assertIn(must, text, must)
        self.assertNotIn("unlink", text.replace('"Unlink"', ""), "never `agentj unlink` (the dashboard's button label only)")
        self.assertNotIn("passphrase set", text)
        self.assertNotIn(SECRET, text)
        for lab in (LABELS_ZH if lang == "zh" else LABELS_EN):
            self.assertIn(lab, text, lab)
        if lang == "zh":
            self.assertIn("7 天", text)
            self.assertIn("不要用 Safari 打开", text)
            for bad in ("公司", "主机", "员工", "客户"):
                self.assertNotIn(bad, text, bad)
        else:
            self.assertIn("7 days", text)
            self.assertIn("not in Safari", text.replace("don't open it in Safari", "not in Safari"))
            self.assertNotIn(" — ", text)
            self.assertNotRegex(text, r"[一-鿿]")

    def test_no_state_still_prints_the_note_and_creates_nothing(self):
        for lang in ("zh", "en"):
            r = _cli(self.home, "handover", "--lang", lang)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.check_common(r.stdout, lang)
            self.assertIn("agentj init", r.stdout)
            self.assertEqual(r.stderr, "")
        self.assertEqual(_tree(self.home), [], "read-only: nothing created under HOME")
        r = _cli(self.home, "handover")
        self.assertIn("Agent J is not set up on this computer yet", r.stdout, "default language = en")

    def test_unmigrated_old_state_is_read_and_not_moved(self):
        old = pathlib.Path(self.home) / ".local" / "state" / "agentjarvis-alpha"
        st = State(old)
        st.init(relay="ws://127.0.0.1:1")
        st.set_agent_name("助理一号")
        before = _tree(self.home)
        r = _cli(self.home, "handover", "--lang", "zh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("「助理一号」", r.stdout)
        self.assertIn("还没加到 Agent J 账号里", r.stdout)
        self.assertIn("已配对的手机：0 台", r.stdout)
        self.check_common(r.stdout, "zh")
        self.assertTrue(old.is_dir() and not old.is_symlink(), "no migration")
        self.assertFalse((pathlib.Path(self.home) / ".local" / "state" / "agentj").exists())
        self.assertEqual(_tree(self.home), before)
        self.assertIn("handover", cli.NO_MIGRATE)

    def test_bound_and_unbound_facts(self):
        st = State(pathlib.Path(self.home) / "st")
        st.init()
        st.set_agent_name("Assistant One")
        work = pathlib.Path(self.home) / "agentj-work"
        work.mkdir()
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            st.set_agent_config("claude", str(work))
        for i in range(2):
            st.add_device(secrets.token_bytes(32), f"phone {i}")
        env = {"AGENTJ_STATE_DIR": str(st.root)}
        r = _cli(self.home, "handover", "--lang", "en", env=env)
        self.check_common(r.stdout, "en")
        self.assertIn("Not in an Agent J account yet", r.stdout)
        self.assertIn("Phones paired: 2 (up to 5)", r.stdout)
        self.assertIn('Agent name: "Assistant One"', r.stdout)
        self.assertIn('Claude Code on this computer; it works in the folder "~/agentj-work"', r.stdout)
        self.assertNotIn('"Unlink"', r.stdout, "the dashboard route only for a computer in an account")
        _bind(st)
        before = _tree(self.home)
        for lang in ("zh", "en"):
            r = _cli(self.home, "handover", "--lang", lang, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.check_common(r.stdout, lang)
            self.assertIn("lin-ka7q", r.stdout)
        zh = _cli(self.home, "handover", "--lang", "zh", env=env).stdout
        en = _cli(self.home, "handover", "--lang", "en", env=env).stdout
        self.assertIn("Agent J 账号 ID：lin-ka7q", zh)
        self.assertIn("Agent J account ID: lin-ka7q", en)
        self.assertIn('"Unlink"', en)
        self.assertIn("「解绑」", zh)
        self.assertIn("已配对的手机：2 台", zh)
        self.assertEqual(_tree(self.home), before, "nothing written, also with a state dir")

    def test_note_function_rejects_other_languages(self):
        with self.assertRaises(ValueError):
            handover.note(handover.facts(pathlib.Path(self.home) / "none"), "fr")


# ------------------------------------------------------------------ 2. docs-rule --write
class Tty(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


class DocsRuleWrite(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-dr-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        self.f = pathlib.Path(self.home) / ".claude" / "CLAUDE.md"

    def run_write(self, tty=True, answer="y", harness="claude", lang="zh"):
        out, asked = io.StringIO(), []

        def read(q):
            asked.append(q)
            return answer
        rc = docsrule.write(argparse.Namespace(harness=harness, lang=lang, write=True), out=out, stdin=Tty(tty), read=read)
        return rc, out.getvalue(), asked

    def test_paths(self):
        self.assertEqual(docsrule.memory_file("claude"), os.path.join(self.home, ".claude", "CLAUDE.md"))
        self.assertEqual(docsrule.memory_file("codex"), os.path.join(self.home, ".codex", "AGENTS.md"))
        self.assertEqual(docsrule.memory_file("opencode"), os.path.join(self.home, ".config", "opencode", "AGENTS.md"))

    def test_pipe_writes_without_a_question(self):
        """F14: no terminal y/N — the Agent may run it itself (a pipe writes, nothing is asked)."""
        rc, out, asked = self.run_write(tty=False, answer="n")
        self.assertEqual(rc, 0, out)
        self.assertEqual(asked, [], "no question")
        self.assertEqual(self.f.read_bytes(), docsrule.block("zh").encode())
        self.assertIn("~/.claude/CLAUDE.md", out)
        self.assertIn(docsrule.BEGIN, out, "the block is shown")

    def test_zh_rule_says_search_yourself(self):
        zh = docsrule.block("zh")
        self.assertIn("自己搜一下广场", zh)
        self.assertNotIn("请主人在电脑上搜", zh)
        self.assertIn("Run it yourself", docsrule.block("en"))

    def test_yes_creates_0600_then_idempotent(self):
        rc, out, _ = self.run_write(answer="Y")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.f.read_bytes(), docsrule.block("zh").encode())
        self.assertEqual(stat.S_IMODE(self.f.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.f.parent.stat().st_mode), 0o700)
        for tty in (True, False):     # already there: no question, exit 0, also on a pipe
            rc, out, asked = self.run_write(tty=tty)
            self.assertEqual((rc, asked), (0, []))
            self.assertIn("already there", out)
        self.assertEqual(self.f.read_bytes(), docsrule.block("zh").encode())

    def test_appends_after_existing_text_and_keeps_the_mode(self):
        self.f.parent.mkdir()
        self.f.write_text("# my rules\nbe brief")
        os.chmod(self.f, 0o644)
        rc, _, _ = self.run_write(lang="en")
        self.assertEqual(rc, 0)
        self.assertEqual(self.f.read_text(), "# my rules\nbe brief\n\n" + docsrule.block("en"))
        self.assertEqual(stat.S_IMODE(self.f.stat().st_mode), 0o644)
        self.f.write_text("<!-- agentj:docs-rule v1 -->\nmy own shorter version\n")
        self.assertEqual(self.run_write()[0], 0)
        self.assertEqual(self.f.read_text(), "<!-- agentj:docs-rule v1 -->\nmy own shorter version\n", "the human's edit wins")

    def test_symlink_is_refused(self):
        target = pathlib.Path(self.home) / "elsewhere.md"
        target.write_text("x\n")
        self.f.parent.mkdir()
        self.f.symlink_to(target)
        rc, out, asked = self.run_write()
        self.assertEqual((rc, asked), (2, []))
        self.assertIn("symlink", out)
        self.assertEqual(target.read_text(), "x\n")

    def test_cli(self):
        r = _cli(self.home, "docs-rule", "--write", "--harness", "codex", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((pathlib.Path(self.home) / ".codex" / "AGENTS.md").read_text(), docsrule.block("en"))
        self.assertEqual(_cli(self.home, "docs-rule", "--write").returncode, 2, "--write needs --harness")
        self.assertEqual(_cli(self.home, "docs-rule", "--harness", "claude").returncode, 2, "--harness needs --write")
        r = _cli(self.home, "docs-rule", "--lang", "zh")
        self.assertEqual((r.returncode, r.stdout), (0, docsrule.block("zh")), "print-only unchanged")
        r = _cli(self.home, "docs-rule", "--write", "--harness", "claude", "--yes")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--yes", r.stderr, "no --yes flag")
        self.assertEqual(_tree(self.home), [".codex", ".codex/AGENTS.md"], "only the written file: no state dir, no migration")
        self.assertIn("docs-rule", cli.NO_MIGRATE)

    def test_real_terminal_writes_without_asking(self):
        """The real CLI on a pseudo-terminal: F14 — it writes at once, no y/N."""
        import pty
        pid, fd = pty.fork()
        if pid == 0:     # child
            os.environ.update(_env(self.home))
            os.chdir(HOST)
            os.execv(sys.executable, [sys.executable, "-m", "agentj.cli", "docs-rule", "--write", "--harness", "opencode"])
        buf = b""
        sent = False
        while True:
            try:
                b = os.read(fd, 4096)
            except OSError:
                break
            if not b:
                break
            buf += b
            if not sent and b"[y/N]" in buf:
                os.write(fd, b"y\n")
                sent = True
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, buf.decode(errors="replace"))
        self.assertFalse(sent, "no question asked")
        f = pathlib.Path(self.home) / ".config" / "opencode" / "AGENTS.md"
        self.assertEqual(f.read_text(), docsrule.block())


# ------------------------------------------------------------------ 3 + 4. pairing link line and the QR
class PairOutput(unittest.TestCase):
    def link(self) -> str:
        return wire.pairing_link(DEFAULT_WEB, DEFAULT_RELAY, wire.channel_id(secrets.token_bytes(32)), secrets.token_bytes(32),
                                 secrets.token_bytes(16), secrets.token_bytes(32), 1_790_000_000)

    def test_link_text(self):
        t = cli.link_text("https://m.agentj.app/#p=X", 5)
        for s in ("5 分钟内有效", "只能用一次", "配对密钥", "valid 5 min", "works once", "pairing key",
                  "iPhone：请把链接粘贴到主屏幕图标打开的页面里，不要用 Safari 打开。",
                  "iPhone: paste the link into the page opened from the home-screen icon, not into Safari.",
                  "https://m.agentj.app/#p=X"):
            self.assertIn(s, t)

    def test_half_block_qr_fits_80_columns_and_maps_back(self):
        import segno
        for _ in range(5):
            qr = segno.make(self.link(), error="m")
            lines = cli.qr_half_blocks(qr)
            self.assertLessEqual(max(len(x) for x in lines), 80)
            self.assertTrue(set("".join(lines)) <= set("█▀▄ "))
            border = 4 if qr.symbol_size(scale=1, border=4)[0] <= 80 else 2
            want = [[bool(m) for m in row] for row in qr.matrix_iter(scale=1, border=border)]
            got = cli.qr_from_half_blocks(lines)
            self.assertEqual(got[:len(want)], want, "exact module matrix")
            self.assertTrue(all(not m for row in got[len(want):] for m in row), "padding row is light")
            n = len(want)
            for i in range(border):     # quiet zone light on every side
                self.assertFalse(any(want[i]) or any(want[n - 1 - i]))
                self.assertFalse(any(r[i] or r[n - 1 - i] for r in want))
        self.assertEqual(len(lines), (n + 1) // 2, "two module rows per text line")

    def test_old_renderers_were_too_wide(self):
        import segno
        qr = segno.make(self.link(), error="m")
        self.assertGreater(len(cli._qr_ascii(qr).splitlines()[0]), 80, "why the half-block form exists")

    def test_print_qr_compact_without_a_tty_has_no_escapes(self):
        with mock.patch.object(cli, "_qr_mode", return_value="compact"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._print_qr(self.link())
        lines = out.getvalue().splitlines()
        self.assertNotIn("\x1b", out.getvalue())
        self.assertLessEqual(max(len(x) for x in lines), 80)

    def test_utf8_terminal_programs(self):
        class Out(io.StringIO):
            encoding = "utf-8"

            def isatty(self):
                return True
        for prog in ("Apple_Terminal", "iTerm.app"):
            self.assertEqual(cli._qr_mode(Out(), {"TERM": "xterm-256color", "TERM_PROGRAM": prog}), "compact", prog)
        self.assertEqual(cli._qr_mode(Out(), {"TERM": "xterm-256color"}), "ansi", "unknown terminal, no locale: as before")


# ------------------------------------------------------------------ 5. feedback check: the server's shape
GOOD = {"stage": "6-host", "host_form": "mac", "os": "macOS 26.6 arm64", "agent_kind": "claude-code",
        "agent_version": "2.1.0", "install_md_version": "0.13.1", "problem": "Step 4: something odd", "resolved": False,
        "owner_informed": True}


class FakeJev:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(body)
        return 200, json.dumps({"result": {"p": 0.01, "reason": "fine"}}).encode()


class FeedbackShape(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def check(self, obj, env=None):
        d = self.tmp / "feedback-report"
        d.write_text(json.dumps(obj))
        out, jev = io.StringIO(), FakeJev()
        rc = feedback.run_check(d, transport=jev, env=env or {}, identity=("devbox", "alice"), out=out)
        return rc, out.getvalue(), jev, self.tmp / "feedback-report.checked.json"

    def test_good_shape_passes(self):
        self.assertEqual(feedback.shape_problems(GOOD), [])
        self.assertEqual(feedback.shape_problems({**GOOD, "resolved": True, "resolution": "retried"}), [])

    def test_missing_fields_are_named_and_nothing_is_written(self):
        obj = {k: v for k, v in GOOD.items() if k not in ("stage", "os", "resolved")}
        rc, out, jev, checked = self.check(obj)
        self.assertEqual(rc, 1)
        self.assertIn("missing: stage, os, resolved", out)
        self.assertIn("nothing written", out)
        self.assertFalse(checked.exists())
        self.assertEqual(jev.calls, [], "no layer-2 call for a report the API would refuse")

    def test_each_server_rule(self):
        cases = {
            "host_form": ({**GOOD, "host_form": "macos"}, "host_form: \"macos\" is not allowed"),
            "stage": ({**GOOD, "stage": "6"}, "stage:"),
            "type": ({**GOOD, "os": 26}, "os: must be text"),
            "long": ({**GOOD, "agent_version": "9" * 101}, "agent_version: too long"),
            "resolution": ({**GOOD, "resolved": True}, "resolution: required when resolved is true"),
            "resolved": ({**GOOD, "resolved": "no"}, "resolved: must be true or false"),
            "owner": ({**GOOD, "owner_informed": False}, "owner_informed: must be true"),
            "unknown": ({**GOOD, "severity": "high"}, "unknown field(s), remove them: severity"),
            "empty": ({**GOOD, "problem": ""}, "missing: problem"),
        }
        for name, (obj, want) in cases.items():
            rc, out, _, _ = self.check(obj)
            self.assertEqual(rc, 1, name)
            self.assertIn(want, out, name)
        # UTF-16 units, like the server: 8193 astral characters = 16386 units > 16384
        self.assertTrue(feedback.shape_problems({**GOOD, "problem": "😀" * 8193}))
        self.assertEqual(feedback.shape_problems({**GOOD, "problem": "😀" * 8192}), [])

    def test_exit_3_says_what_it_means_and_what_to_do(self):
        rc, out, jev, checked = self.check(GOOD, env={})
        self.assertEqual(rc, 3)
        self.assertTrue(checked.exists())
        for s in ("NOT rejected", "OpenRouter", "Do not look for, ask for or set a key yourself", "--owner-confirmed",
                  "If they say no, do not send it"):
            self.assertIn(s, out)

    def test_send_refuses_a_bad_shape_before_the_network(self):
        p = self.tmp / "x.checked.json"
        p.write_text(json.dumps({k: v for k, v in GOOD.items() if k != "agent_kind"}, indent=2) + "\n")
        calls = []
        out = io.StringIO()
        rc = feedback.run_send(p, owner_confirmed=True, env={}, identity=("devbox", "alice"), out=out,
                               http_fn=lambda *a, **k: calls.append(a) or (201, b"{}"))
        self.assertEqual((rc, calls), (1, []))
        self.assertIn("missing: agent_kind", out.getvalue())
        self.assertIn("not sent", out.getvalue())


# ------------------------------------------------------------------ 6. macOS service: no new launchd override record
class FakeLaunchctl:
    def __init__(self, disabled_out: str):
        self.disabled_out, self.calls = disabled_out, []

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        out = self.disabled_out if args[0] == "print-disabled" else ""
        rc = 113 if args[0] == "print" else 0     # P63: `print` of a label that bootout unloaded: "Could not find service"
        return subprocess.CompletedProcess(["launchctl", *args], rc, out, "")

    def verbs(self):
        return [c[0] for c in self.calls]


class LaunchdEnable(unittest.TestCase):
    LABEL = "aj.test.label"

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-ld-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home, "AGENTJ_SERVICE_NAME": self.LABEL})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(service.TOKEN_ENV, None)
        self.st = State(pathlib.Path(self.home) / "st")
        self.st.init(relay="ws://127.0.0.1:1")

    def run_both(self, disabled_out: str) -> FakeLaunchctl:
        fake = FakeLaunchctl(disabled_out)
        with mock.patch.object(service, "platform", return_value="macos"), mock.patch.object(service, "_launchctl", fake):
            service.install(self.st)
            self.assertTrue(os.path.exists(service.plist_path(self.LABEL)))
            self.install_verbs = fake.verbs()
            fake.calls.clear()
            r = service.uninstall(self.st)
        self.assertTrue(r["removed"])
        self.assertFalse(os.path.exists(service.plist_path(self.LABEL)))
        self.uninstall_result = r
        return fake

    def test_no_record_no_enable_no_disable(self):
        out = 'disabled services = {\n\t"com.apple.x" => disabled\n\t"aj.test.labelX" => disabled\n}\n'
        fake = self.run_both(out)
        self.assertNotIn("enable", self.install_verbs)
        self.assertEqual(self.install_verbs[-1], "bootstrap")
        self.assertNotIn("disable", fake.verbs())
        self.assertFalse(self.uninstall_result["restored_disabled"])
        self.assertFalse((self.st.root / "launchd-enabled.json").exists())

    def test_explicit_enabled_record_is_left_alone(self):
        fake = self.run_both(f'disabled services = {{\n\t"{self.LABEL}" => enabled\n}}\n')
        self.assertNotIn("enable", self.install_verbs)
        self.assertNotIn("disable", fake.verbs())

    def test_disabled_label_is_enabled_before_bootstrap_and_restored(self):
        for fmt in ("disabled", "true"):     # current macOS / older macOS
            fake = self.run_both(f'disabled services = {{\n\t"{self.LABEL}" => {fmt}\n}}\n')
            v = self.install_verbs
            self.assertIn("enable", v, fmt)
            self.assertLess(v.index("enable"), v.index("bootstrap"), "a disabled label cannot be bootstrapped")
            self.assertIn(("disable", f"gui/{os.getuid()}/{self.LABEL}"), fake.calls)
            self.assertTrue(self.uninstall_result["restored_disabled"])
            self.assertFalse((self.st.root / "launchd-enabled.json").exists())

    def test_reinstall_keeps_the_memory(self):
        fake = FakeLaunchctl(f'"{self.LABEL}" => disabled\n')
        with mock.patch.object(service, "platform", return_value="macos"), mock.patch.object(service, "_launchctl", fake):
            service.install(self.st)
            fake.disabled_out = f'"{self.LABEL}" => enabled\n'      # our enable is now the record
            service.install(self.st)                                  # upgrade / reinstall
            self.assertTrue((self.st.root / "launchd-enabled.json").exists())
            self.assertEqual(stat.S_IMODE((self.st.root / "launchd-enabled.json").stat().st_mode), 0o600)
            self.assertTrue(service.uninstall(self.st)["restored_disabled"])

    def test_print_disabled_failure_means_no_enable(self):
        with mock.patch.object(service, "_launchctl", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "nope")):
            self.assertIsNone(service.launchd_disabled(self.LABEL))
        with mock.patch.object(service, "_launchctl", FakeLaunchctl(f'"{self.LABEL}.other" => disabled\n')):
            self.assertIsNone(service.launchd_disabled(self.LABEL), "a longer label is not ours")


# ------------------------------------------------------------------ 7. doctor + service install's last line
class DoctorEnvLogin(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-de-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self.st = State(pathlib.Path(self.home) / "st")
        self.st.init(relay="ws://127.0.0.1:1")
        fake = pathlib.Path(self.home) / "bin"
        fake.mkdir()
        (fake / "claude").write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
        (fake / "claude").chmod(0o755)
        self.env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": self.home, "XDG_CONFIG_HOME": f"{self.home}/.config",
                    "CLAUDE_CODE_OAUTH_TOKEN": SECRET, "AGENTJ_SERVICE_NAME": "aj-de-test"}

    def check(self, svc):
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch.object(doctor, "_claude_login", return_value=("env", "env CLAUDE_CODE_OAUTH_TOKEN (name only)")):
            return doctor.check_agent_cli(self.st, svc)

    def test_env_only_login_is_a_warning_with_or_without_the_service(self):
        for svc in ({}, {"installed": True, "name": "aj-de-test"}):
            c = self.check(svc)
            self.assertEqual(c["status"], "warn", svc)
            self.assertIn("service login missing", c["summary"])
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", c["summary"] + c["hint"])
            self.assertIn("claude", c["hint"])
            self.assertNotIn(SECRET, json.dumps(c))

    def test_the_humans_env_file_counts(self):
        f = pathlib.Path(self.home) / ".config" / "systemd" / "user" / "aj-de-test.env"
        f.parent.mkdir(parents=True)
        f.write_text("")
        f.chmod(0o600)
        self.assertEqual(self.check({"installed": True, "name": "aj-de-test"})["status"], "warn")
        f.write_text("CLAUDE_CODE_OAUTH_TOKEN=" + SECRET + "\n")
        self.assertEqual(self.check({"installed": True, "name": "aj-de-test"})["status"], "ok")

    def test_service_install_says_run_doctor_next(self):
        self.assertIn("agentj doctor", cli.SERVICE_NEXT)
        self.assertIn("!", cli.SERVICE_NEXT)
        src = (HOST / "agentj" / "cli.py").read_text()
        self.assertIn("print(SERVICE_NEXT)", src)


# ------------------------------------------------------------------ 8. no `unlink` for a lost phone, no `set` when forgotten
class Advice(unittest.TestCase):
    def test_reset_and_exists_messages(self):
        from agentj import gate
        self.assertIn("agentj passphrase reset", gate.MESSAGES["exists"])
        src = (HOST / "agentj" / "cli.py").read_text()
        i = src.index('elif a.mode == "reset":')
        reset = src[i:src.index("else:", i)]
        self.assertNotIn("passphrase set", reset)
        self.assertIn("agentj pair", reset)

    def test_no_host_string_sends_a_lost_phone_to_unlink(self):
        rx = re.compile(r"(丢|lost)[^\n\"]{0,80}unlink|unlink[^\n\"]{0,80}(丢|lost)", re.I)
        for p in list((HOST / "agentj").rglob("*.py")) + list((HOST / "agentj").rglob("*.json")):
            self.assertIsNone(rx.search(p.read_text(encoding="utf-8")), p)


if __name__ == "__main__":
    unittest.main()
