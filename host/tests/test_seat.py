"""Seat setup (reports/design/seat-setup/CONTRACT.md §4, §4.1) host side: `jarvis login --seat | --seat-file | --seat -`
against the fake control plane (every answer: 200 / 404 / 409 name_taken / 402 / 429 / already_bound / 400 / 5xx / a
garbled 200), the local refusals (code shape, Agent name — nothing is sent), cloud.json `via`, status / doctor showing it,
the code never in any output, log or file, and `jarvis agent detect` with a fake HOME + fake binaries on PATH."""
import json
import os
import pathlib
import secrets
import shutil
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
from jarvis_host import cloud, doctor, harness  # noqa: E402
from jarvis_host.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402


def new_code() -> str:
    return "ajt_" + secrets.token_urlsafe(32)   # 32 bytes → 43 base64url characters


class Shape(unittest.TestCase):
    def test_code_shape(self):
        c = new_code()
        self.assertEqual(len(c), 47)
        self.assertTrue(cloud.seat_code_ok(c))
        for bad in (c[:-1], c + "A", "ajx_" + c[4:], " " + c, c + "\n", c[:10] + "+" + c[11:], c[:10] + "=" + c[11:],
                    "AJT_" + c[4:], "", None, 123, "ajt_" + "é" * 43):
            self.assertFalse(cloud.seat_code_ok(bad), repr(bad))


class SeatBindUnit(unittest.TestCase):
    """cloud.seat_bind in-process against the fake control plane."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.tmp.cleanup()

    def leaked(self, code: str) -> list[str]:
        return [p.name for p in self.st.root.iterdir() if p.is_file() and code.encode() in p.read_bytes()]

    def test_bound_writes_cloud_json_via_seat(self):
        did = self.st.add_device(os.urandom(32), "phone")
        before = self.st.devices_path.read_bytes()
        code = new_code()
        with FakeCP() as cp:
            cp.seat_tokens.add(code)
            res = cloud.seat_bind(self.st, cp.url, code, "  市场部   Agent ")
            self.assertEqual(res["status"], "bound")
            self.assertEqual(res["tenant"], {"slug": "acme-co", "name": "Acme [2J Co"})
            self.assertEqual(res["agent_name"], "市场部 Agent")
            body = cp.seat_binds[0]
            self.assertEqual(set(body), {"v", "t", "channel", "ts", "token", "name"})
            self.assertEqual((body["t"], body["name"], body["channel"]), ("seat_bind", "市场部 Agent", cloud.channel_of(self.st)))
            self.assertEqual(cp.rejected, [])
        link = json.loads(self.st.cloud_path.read_text())
        self.assertEqual(set(link), {"api", "host_id", "tenant", "linked_at", "last_seq", "via"})
        self.assertEqual((link["via"], link["host_id"], link["last_seq"]), ("seat", "h_seat", 0))
        self.assertEqual(os.stat(self.st.cloud_path).st_mode & 0o777, 0o600)
        self.assertEqual(cloud.read_cloud(self.st)["via"], "seat")
        self.assertEqual(self.st.devices_path.read_bytes(), before, "the bound answer's devices / approve keys change nothing")
        self.assertIn(did, self.st.devices())
        log = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
        self.assertIn({"ev": "cloud_linked", "tenant": "acme-co", "kind": "seat"}, [{k: v for k, v in r.items() if k != "ts"} for r in log])
        self.assertEqual(self.leaked(code), [], "the code is in no file of the state dir")
        self.assertIsNone(self.st.agent_name(), "cloud.py never sets the Agent name; the CLI does")

    def test_every_answer(self):
        code = new_code()
        cases = [("invalid", "invalid_setup"), ("payment", "payment_required"), ("rate", "rate_limited"),
                 ("already_bound", "already_bound"), ("bad_name", "bad_name"), ("garbled", "error"), (500, "error"),
                 ("taken", "name_taken")]
        with FakeCP() as cp:
            cp.seat_tokens.add(code)
            for step, want in cases:
                cp.seat_script = [step]
                res = cloud.seat_bind(self.st, cp.url, code, "Wren2")
                self.assertEqual(res["status"], want, step)
                self.assertFalse(self.st.cloud_path.exists(), f"{step}: nothing written")
            self.assertEqual(res["suggestions"], ("Wren2 2", "Wren2 3"), "only §1-valid suggestions, ≤ 3")
            # unknown code (never issued) → 404 → invalid_setup
            self.assertEqual(cloud.seat_bind(self.st, cp.url, new_code(), "Wren2")["status"], "invalid_setup")
            # then the real one binds; a second use of the same code is invalid (single use)
            self.assertEqual(cloud.seat_bind(self.st, cp.url, code, "Wren2")["status"], "bound")
            cloud.delete_cloud(self.st)
            cp.seat_channel_bound.clear()
            self.assertEqual(cloud.seat_bind(self.st, cp.url, code, "Wren3")["status"], "invalid_setup")
        log = self.st.log_path.read_text()
        self.assertNotIn(code, log)
        self.assertIn('"ev": "seat_bind", "result": "invalid_setup", "status": "http_4xx"', log)
        self.assertEqual(self.leaked(code), [])

    def test_local_refusals_send_nothing(self):
        with FakeCP() as cp:
            for code, name, want in ((new_code()[:-1], "Wren", "bad_code"), ("ajt_" + "!" * 43, "Wren", "bad_code"),
                                     (new_code(), "", "name_required"), (new_code(), "   ", "name_required"),
                                     (new_code(), "a‮b", "bad_name"), (new_code(), "x" * 33, "bad_name"),
                                     (new_code(), None, "name_required")):
                self.assertEqual(cloud.seat_bind(self.st, cp.url, code, name)["status"], want, (code[:6], name))
            self.assertEqual(cp.seat_binds, [])
            self.assertEqual(cp.rejected, [])

    def test_unreachable(self):
        res = cloud.seat_bind(self.st, "http://127.0.0.1:1", new_code(), "Wren")
        self.assertEqual(res["status"], "error")
        self.assertFalse(self.st.cloud_path.exists())

    def test_refuses_plain_http_remote(self):
        with self.assertRaises(cloud.CloudError):
            cloud.seat_bind(self.st, "http://example.com", new_code(), "Wren")

    def test_legacy_cloud_json_reads_as_code(self):
        self.st.write_private(self.st.cloud_path, json.dumps({"api": "https://api.agentjarvis.net", "host_id": "h_1",
                                                               "tenant": {"slug": "acme-co", "name": "A"}, "linked_at": 1,
                                                               "last_seq": 5}).encode())
        self.assertEqual(cloud.read_cloud(self.st)["via"], "code")
        cloud._store_seq(self.st, "h_1", 9)   # a seq update keeps (and now writes) via
        self.assertEqual(json.loads(self.st.cloud_path.read_text())["via"], "code")

    def test_seq_update_keeps_via_seat(self):
        cloud.write_cloud(self.st, {"api": "http://127.0.0.1:9", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "A"},
                                    "linked_at": 1, "last_seq": 0, "via": "seat"})
        cloud._store_seq(self.st, "h_1", 7)
        d = json.loads(self.st.cloud_path.read_text())
        self.assertEqual((d["via"], d["last_seq"]), ("seat", 7))


class SeatLeaveUnit(unittest.TestCase):
    """cloud.seat_leave in-process (review SS-02): signed with its own context, every answer mapped, never raises."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_answers(self):
        code = new_code()
        with FakeCP() as cp:
            self.assertEqual(cloud.seat_leave(self.st, cp.url)["status"], "not_found", "not bound")
            cp.seat_tokens.add(code)
            self.assertEqual(cloud.seat_bind(self.st, cp.url, code, "Ann")["status"], "bound")
            self.assertEqual(cloud.seat_bind(self.st, cp.url, code, "Ann")["status"], "bound", "replay → the same 200")
            for step, want in (("rate", "rate_limited"), (500, "fail"), ("not_found", "not_found")):
                cp.leave_script = [step]
                self.assertEqual(cloud.seat_leave(self.st, cp.url)["status"], want, step)
            self.assertEqual(cloud.seat_leave(self.st, cp.url), {"status": "left", "http": "http_2xx"})
            self.assertEqual(cp.rejected, [], "context and schema accepted by the verifying fake")
            self.assertEqual(cloud.seat_leave(self.st, cp.url)["status"], "not_found", "already out")
            self.assertEqual(cloud.seat_bind(self.st, cp.url, code, "Ann")["status"], "invalid_setup", "the code is dead after leaving")
        self.assertEqual(cloud.seat_leave(self.st, cp.url)["status"], "fail", "unreachable: no exception")
        log = self.st.log_path.read_text()
        self.assertNotIn(code, log)


class SeatCli(unittest.TestCase):
    """The real `jarvis login --seat …` (subprocess) — exit codes, output, the code never printed."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-seat-", dir="/tmp")
        self.env = {**os.environ, "AGENTJARVIS_STATE_DIR": self.dir + "/s", "PYTHONPATH": str(HOST)}
        self.env.pop("AGENTJARVIS_API_URL", None)
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.code = new_code()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def jarvis(self, *args, input=""):
        r = subprocess.run([sys.executable, "-m", "jarvis_host.cli", *args], cwd=HOST, env=self.env, capture_output=True,
                           text=True, timeout=60, input=input)
        self.assertNotIn(self.code, r.stdout + r.stderr, "the setup code is never printed")
        return r

    def seat_file(self, mode=0o600, content=None) -> str:
        p = os.path.join(self.dir, "seat-code")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, (content if content is not None else self.code + "\n").encode())
        os.close(fd)
        os.chmod(p, mode)
        return p

    def test_bind_with_argv_then_status_and_doctor(self):
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "贾维斯一号")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("✓ 已添加到公司账号 acme-co（Acme [2J Co）的席位，Agent 名「贾维斯一号」", r.stdout)
            self.assertIn("Added to a seat of company acme-co", r.stdout)
            self.assertNotIn("[y/N]", r.stdout, "no y/N question on the seat path")
            self.assertNotIn("\x1b", r.stdout)
            self.assertIn("首次上报：成功", r.stdout)
            self.assertEqual(len(cp.reports), 1, "the first report is sent")
            self.assertEqual(cp.reports[0]["agent_name"], "贾维斯一号")
        self.assertEqual(self.st.agent_name(), "贾维斯一号")
        self.assertEqual(json.loads(self.st.cloud_path.read_text())["via"], "seat")
        st = self.jarvis("status")
        self.assertIn("linked via seat setup", st.stdout)
        doc = json.loads(self.jarvis("doctor", "--json", "--offline").stdout)
        bound = next(c for c in doc["checks"] if c["id"] == "bound")
        self.assertIn("via seat setup", bound["summary"])
        for p in self.st.root.iterdir():
            if p.is_file():
                self.assertNotIn(self.code.encode(), p.read_bytes(), p.name)
        again = self.jarvis("login", "--seat", self.code, "--name", "x")
        self.assertEqual(again.returncode, 1)
        self.assertIn("已添加到 Dashboard 公司账号 acme-co", again.stderr)

    def test_seat_file_and_stdin(self):
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            loose = self.seat_file(0o644)
            r = self.jarvis("login", "--api", cp.url, "--seat-file", loose, "--name", "Wren9")
            self.assertEqual(r.returncode, 1)
            self.assertIn("chmod 600", r.stderr)
            self.assertEqual(cp.seat_binds, [], "a group/world-readable file is refused before anything is sent")
            os.unlink(loose)
            os.symlink(self.seat_file(), os.path.join(self.dir, "link"))
            r = self.jarvis("login", "--api", cp.url, "--seat-file", os.path.join(self.dir, "link"), "--name", "Wren9")
            self.assertEqual(r.returncode, 1, "a symlink is not followed")
            self.assertEqual(cp.seat_binds, [])
            r = self.jarvis("login", "--api", cp.url, "--seat-file", self.seat_file(), "--name", "Wren9")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(cp.seat_binds[-1]["token"], self.code)
        os.unlink(self.st.cloud_path)
        self.code = new_code()
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            r = self.jarvis("login", "--api", cp.url, "--seat", "-", "--name", "Wren8", input=self.code + "\n")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(cp.seat_binds[-1]["token"], self.code)

    def test_exit_codes(self):
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            for step, rc, text in (("taken", 3, "已经有了"), ("invalid", 4, "向管理员要一个新的"),
                                   ("payment", 5, "不在付费状态"), ("rate", 1, "太频繁"), ("already_bound", 1, "仍是已绑定"),
                                   (503, 1, "http_5xx")):
                cp.seat_script = [step]
                r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Wren")
                self.assertEqual(r.returncode, rc, (step, r.stderr))
                self.assertIn(text, r.stderr, step)
                self.assertFalse(self.st.cloud_path.exists(), step)
            cp.seat_script = ["taken"]
            r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Wren")
            self.assertIn("「Wren 2」、「Wren 3」", r.stderr, "suggestions are printed")
            self.assertNotIn("‮", r.stderr)
            n = len(cp.seat_binds)
            # local refusals: exit 2, nothing sent
            for args in (("--seat", self.code[:-1], "--name", "W"), ("--seat", self.code, "--name", "a‮b"),
                         ("--seat", self.code, "--name", " "), ("--seat", self.code)):
                r = self.jarvis("login", "--api", cp.url, *args)
                self.assertEqual(r.returncode, 2, (args[1][:6], r.stderr))
            self.assertEqual(len(cp.seat_binds), n)
            r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--seat-file", "/nonexistent", "--name", "W")
            self.assertEqual(r.returncode, 2, "--seat and --seat-file are exclusive (argparse)")
            r = self.jarvis("login", "--api", cp.url, "--name", "W")
            self.assertEqual(r.returncode, 1, "--name alone is not a login")
        log = self.st.log_path.read_text()
        self.assertNotIn(self.code, log)

    def test_retry_after_lost_answer_is_a_replay(self):
        """Review SS-02: the bind committed but the 200 was lost → the same code from the same key binds again (replay)."""
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            cp.seat_script = [503]                    # a failed first try (as a lost answer looks to the host)
            r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Wren7")
            self.assertEqual(r.returncode, 1)
            self.assertFalse(self.st.cloud_path.exists())
            # the next try binds; then, with the local record gone as after a lost 200, the same code replays
            r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Wren7")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            os.unlink(self.st.cloud_path)
            r = self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Wren7")
            self.assertEqual(r.returncode, 0, "replay of the same code from the same key → the same 200")
            self.assertEqual(json.loads(self.st.cloud_path.read_text())["via"], "seat")
            self.assertIn("this also takes this computer out of that company", r.stdout)

    def test_unlink_of_a_seat_link_leaves_the_company(self):
        """Review SS-02: `jarvis unlink` of a seat link sends a signed seat-leave first, then removes cloud.json."""
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            self.assertEqual(self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Leaver").returncode, 0)
            r = self.jarvis("unlink")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("已通知 Dashboard 把本机移出公司 acme-co", r.stdout)
            self.assertNotIn("要在 Dashboard 里解绑本机", r.stdout)
            self.assertFalse(self.st.cloud_path.exists())
            self.assertEqual(len(cp.seat_leaves), 1)
            self.assertEqual(set(cp.seat_leaves[0]), {"v", "t", "channel", "ts"})
            self.assertEqual(cp.seat_leaves[0]["t"], "seat_leave")
            self.assertEqual(cp.rejected, [])
            # the channel is free: a new code binds at once
            code2 = new_code()
            cp.seat_tokens.add(code2)
            self.assertEqual(self.jarvis("login", "--api", cp.url, "--seat", code2, "--name", "Back").returncode, 0)
            # failures: cloud.json still goes, the human is told to ask the owner
            for step, text in (("rate", "太频繁"), (503, "连不上或出错"), ("not_found", "已不在公司 acme-co 的席位里")):
                if not self.st.cloud_path.exists():
                    code3 = new_code()
                    cp.seat_tokens.add(code3)
                    cp.seat_channel_bound.clear()
                    self.assertEqual(self.jarvis("login", "--api", cp.url, "--seat", code3, "--name", f"N{len(cp.seat_leaves)}").returncode, 0)
                cp.leave_script = [step]
                r = self.jarvis("unlink")
                self.assertEqual(r.returncode, 0, (step, r.stderr))
                self.assertIn(text, r.stdout, step)
                self.assertFalse(self.st.cloud_path.exists(), step)
        log = self.st.log_path.read_text()
        self.assertIn('"seat_leave"', log)
        self.assertNotIn(self.code, log)

    def test_unlink_unreachable_still_removes_the_local_link(self):
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            self.assertEqual(self.jarvis("login", "--api", cp.url, "--seat", self.code, "--name", "Gone").returncode, 0)
        # the fake control plane is gone now
        r = self.jarvis("unlink")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("没能通知 Dashboard", r.stdout)
        self.assertIn("收回这个席位", r.stdout)
        self.assertFalse(self.st.cloud_path.exists())

    def test_unlink_of_a_code_link_is_local_only(self):
        with FakeCP() as cp:
            cloud.write_cloud(self.st, {"api": cp.url, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "A"},
                                        "linked_at": 1, "last_seq": 0, "via": "code"})
            r = self.jarvis("unlink")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(cp.seat_leaves, [], "a code-bound link sends nothing")
            self.assertIn("Dashboard 那边的绑定要在 Dashboard 里解绑本机", r.stdout)
            self.assertFalse(self.st.cloud_path.exists())

    def test_help_is_bilingual(self):
        out = self.jarvis("login", "--help").stdout
        for s in ("--seat", "--seat-file", "--name", "设置码", "setup code"):
            self.assertIn(s, out)
        out = " ".join(self.jarvis("agent", "--help").stdout.split())
        self.assertIn("detect", out)
        self.assertIn("which agents are usable", out)


class Detect(unittest.TestCase):
    """`jarvis agent detect` with a fake HOME and fake binaries on PATH: existence checks only, never content."""
    MARK = "CREDMARKER-" + "z" * 12

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-det-", dir="/tmp")
        self.bin = os.path.join(self.home, "bin")
        os.mkdir(self.bin)

    def tearDown(self):
        for root, dirs, files in os.walk(self.home):
            for n in files:
                try:
                    os.chmod(os.path.join(root, n), 0o600)
                except OSError:
                    pass
        shutil.rmtree(self.home, ignore_errors=True)

    def fake(self, name, version="1.2.3 (fake)", rc=0):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write(f"#!/bin/sh\nprintf '%b\\n' '{version}'\nexit {rc}\n")
        os.chmod(p, 0o755)

    def cred(self, rel):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(self.MARK)
        os.chmod(p, 0o000)   # unreadable: an existence check works, any attempt to read it would fail

    def env(self, **extra):
        return {"PATH": self.bin, "HOME": self.home, **extra}

    def run_detect(self, **extra) -> dict:
        with mock.patch.dict(os.environ, self.env(**extra), clear=True):
            return harness.detect()

    def test_none(self):
        d = self.run_detect()
        self.assertEqual((d["usable"], d["decision"]), ([], "none"))
        self.assertEqual([h["name"] for h in d["harnesses"]], ["claude", "codex", "opencode"])
        self.assertTrue(all(set(h) >= {"name", "installed", "logged_in", "supported", "version"} for h in d["harnesses"]))

    def test_installed_but_not_logged_in_is_not_usable(self):
        self.fake("claude")
        self.fake("codex")
        d = self.run_detect()
        self.assertEqual(d["decision"], "none")
        self.assertEqual([(h["installed"], h["logged_in"]) for h in d["harnesses"][:2]], [(True, False), (True, False)])

    def test_exactly_one(self):
        self.fake("claude", "2.1.0 (Claude Code)")
        self.fake("codex")
        self.cred(".claude/.credentials.json")
        d = self.run_detect()
        self.assertEqual((d["usable"], d["decision"]), (["claude"], "use:claude"))
        self.assertEqual(d["harnesses"][0]["version"], "2.1.0 (Claude Code)")

    def test_two_usable_asks_the_owner(self):
        self.fake("claude")
        self.fake("codex", "codex-cli 0.50.0")
        self.cred(".claude/.credentials.json")
        self.cred(".codex/auth.json")
        d = self.run_detect()
        self.assertEqual((d["usable"], d["decision"]), (["claude", "codex"], "ask_owner"))
        # the one running the install changes nothing: still ask
        self.assertEqual(self.run_detect(CLAUDECODE="1")["decision"], "ask_owner")

    def test_opencode_detected_not_supported(self):
        self.fake("opencode", "0.9.0")
        self.fake("codex")
        self.cred(".codex/auth.json")
        d = self.run_detect()
        oc = d["harnesses"][2]
        self.assertEqual((oc["installed"], oc["supported"], oc["note"]), (True, False, "coming in a later version"))
        self.assertEqual(d["decision"], "use:codex")

    def test_version_must_answer(self):
        self.fake("claude", rc=1)
        self.cred(".claude/.credentials.json")
        d = self.run_detect()
        self.assertEqual((d["harnesses"][0]["installed"], d["decision"]), (False, "none"))

    def test_env_login_by_name_and_inside_claude_code(self):
        self.fake("claude")
        self.assertEqual(self.run_detect(**{"CLAUDE_CODE_OAUTH_TOKEN": self.MARK})["decision"], "use:claude")
        d = self.run_detect(CLAUDECODE="1")
        self.assertIsNone(d["harnesses"][0]["logged_in"], "hidden by Claude Code: unknown, not false")
        self.assertEqual(d["decision"], "use:claude")

    def test_control_chars_in_version_are_cleaned(self):
        self.fake("codex", "evil\\033[2J v1")
        self.cred(".codex/auth.json")
        v = self.run_detect()["harnesses"][1]["version"]
        self.assertNotIn("\x1b", v)

    def test_cli_json_and_text_never_show_credentials(self):
        self.fake("claude")
        self.fake("codex")
        self.cred(".claude/.credentials.json")
        self.cred(".codex/auth.json")
        env = {**self.env(**{"CLAUDE_CODE_OAUTH_TOKEN": self.MARK}), "PYTHONPATH": str(HOST)}
        r = subprocess.run([sys.executable, "-m", "jarvis_host.cli", "agent", "detect", "--json"], cwd=HOST, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["decision"], "ask_owner")
        self.assertEqual(set(d), {"harnesses", "usable", "decision"})
        t = subprocess.run([sys.executable, "-m", "jarvis_host.cli", "agent", "detect"], cwd=HOST, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(t.returncode, 0, t.stderr)
        self.assertIn("ask your human which one", t.stdout)
        for out in (r.stdout, t.stdout, r.stderr, t.stderr):
            self.assertNotIn(self.MARK, out)
            self.assertNotIn(self.home, out)

    def test_works_before_init(self):
        env = {**self.env(), "PYTHONPATH": str(HOST), "AGENTJARVIS_STATE_DIR": os.path.join(self.home, "nope")}
        r = subprocess.run([sys.executable, "-m", "jarvis_host.cli", "agent", "detect", "--json"], cwd=HOST, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, json.loads(r.stdout)["decision"]), (0, "none"))
        self.assertFalse(os.path.exists(os.path.join(self.home, "nope")))

    def test_doctor_line(self):
        self.fake("claude")
        self.fake("codex")
        self.cred(".claude/.credentials.json")
        self.cred(".codex/auth.json")
        with mock.patch.dict(os.environ, self.env(), clear=True):
            c = doctor.check_harness()
        self.assertEqual((c["id"], c["status"]), ("harness", "ok"))
        self.assertIn("ask_owner", c["summary"])
        self.assertIn("human decides", c["hint"])
        with mock.patch.dict(os.environ, {"PATH": os.path.join(self.home, "empty"), "HOME": self.home}, clear=True):
            c = doctor.check_harness()
        self.assertEqual((c["status"], c["summary"].endswith("→ none")), ("warn", True))


if __name__ == "__main__":
    unittest.main()
