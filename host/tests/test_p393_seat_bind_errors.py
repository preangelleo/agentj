"""P393 (#119): `agentj login --seat` used to tell the owner "this Agent J host is still listed elsewhere — have it removed"
for EVERY server-side failure, the account's billing lock included, and the name rule was written "1–32 characters" while the
program refused 「张小明的 AgentJ@Lenovo」 by the 13-unit width rule. Here: the host maps 409 billing_busy and 500 internal to
their own results / messages / exit codes, a too-long name is refused with its own length against the limit, and the CLI
help, the account dashboard hint and the docs all state the same width rule."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
AJ = HERE.parents[1]                 # agentjarvis/
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import cloud, names, text  # noqa: E402
from agentj.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402

LONG = "张小明的 AgentJ@Lenovo"          # same width as the name in the screenshot: 4 × 1.6 + 14 × 1 = 20.4


def new_code() -> str:
    return "ajt_" + secrets.token_urlsafe(32)


class Mapping(unittest.TestCase):
    """cloud.seat_bind: the answer decides the result — billing_busy / server_error are no longer folded into others."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_answers(self):
        code = new_code()
        for status, body, want in ((409, {"error": "billing_busy"}, "billing_busy"),
                                   (409, {"error": "already_bound"}, "already_bound"),
                                   (500, {"error": "internal"}, "server_error"),
                                   (409, {"error": "name_taken", "suggestions": ["Wren 2"]}, "name_taken")):
            res = cloud.seat_bind(self.st, "https://agentj.app/api", code, "Wren", post=lambda url, env, s=status, b=body: (s, b))
            self.assertEqual(res["status"], want, (status, body))
            self.assertFalse(self.st.cloud_path.exists(), "nothing written")
        self.assertIn("billing_busy", cloud.SEAT_RESULTS)
        self.assertIn("server_error", cloud.SEAT_RESULTS)
        self.assertNotIn(code, self.st.log_path.read_text())


class Cli(unittest.TestCase):
    """The real `agentj login --seat …` against the fake control plane."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-p393-")
        self.env = {**os.environ, "AGENTJ_STATE_DIR": self.dir + "/s", "PYTHONPATH": str(HOST)}
        self.env.pop("AGENTJ_API_URL", None)
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.code = new_code()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def agentj(self, *args):
        r = subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env=self.env, capture_output=True,
                           text=True, timeout=60, input="")
        self.assertNotIn(self.code, r.stdout + r.stderr, "the setup code is never printed")
        return r

    def test_billing_busy_and_server_error_are_said_as_such(self):
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            cp.seat_script = ["billing_busy"]
            r = self.agentj("login", "--api", cp.url, "--seat", self.code, "--name", "Kestrel")
            self.assertEqual(r.returncode, 6, r.stderr)
            self.assertIn("账单正在处理中", r.stderr)
            self.assertIn("a billing change is being processed", r.stderr)
            self.assertIn("设置码没有用掉", r.stderr)
            self.assertNotIn("还挂在别的地方", r.stderr, "not the already-bound advice")
            cp.seat_script = [500]
            r = self.agentj("login", "--api", cp.url, "--seat", self.code, "--name", "Kestrel")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("服务器这次出错了", r.stderr)
            self.assertNotIn("还挂在别的地方", r.stderr)
            cp.seat_script = ["already_bound"]
            r = self.agentj("login", "--api", cp.url, "--seat", self.code, "--name", "Kestrel")
            self.assertIn("还挂在别的地方", r.stderr, "a real duplicate binding keeps its message")
            self.assertFalse(self.st.cloud_path.exists())
            # then the same code still binds: none of the above used it up
            r = self.agentj("login", "--api", cp.url, "--seat", self.code, "--name", "Kestrel")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_too_long_name_says_how_long(self):
        with FakeCP() as cp:
            cp.seat_tokens.add(self.code)
            for name, units in ((LONG, "20.4"), ("x" * 40, "40")):
                r = self.agentj("login", "--api", cp.url, "--seat", self.code, "--name", name)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(f"算下来是 {units}，最多 13", r.stderr)
                self.assertIn(f"counts {units}, the limit is 13", r.stderr)
                self.assertNotIn("1–32", r.stderr)
            self.assertEqual(cp.seat_binds, [], "refused before anything is sent")

    def test_help_states_the_width_rule(self):
        r = self.agentj("login", "-h")
        flat = " ".join(r.stdout.split())
        self.assertIn("长度最多 13", flat)
        self.assertIn("1.6", flat)
        self.assertIn("6 账单处理中", flat)
        self.assertNotIn("1–32", flat)


class Rule(unittest.TestCase):
    def test_length_alone_reads_too_long(self):
        self.assertEqual(text.new_agent_name_problem(LONG), "name_too_long")
        self.assertEqual(text.new_agent_name_problem("x" * 40), "name_too_long", "over 32 code points, only the length is wrong")
        self.assertEqual(text.new_agent_name_problem("a‮b" * 20), "bad_name", "a bad character still says bad_name")
        self.assertEqual(text.new_agent_name_problem("八个中文字八个字"), None)
        self.assertEqual(round(text.agent_name_units(LONG), 1), 20.4)
        self.assertIn("20.4", names.too_long_message(LONG))
        self.assertNotIn("1–32", names.RENAME_MESSAGES["bad_name"])


OLD = re.compile(r"1\s*[–-]\s*32|1 到 32|1 to 32")


@unittest.skipUnless((AJ / "dashboard/i18n/app.zh.json").exists() and (AJ / "site/content/docs/02-install/en.md").exists(),
                     "the account dashboard and the docs are not part of this tree")
class Consistency(unittest.TestCase):
    """CLI help, the dashboard's name hint and the docs carry the same numbers (13, 1.6, 8) as the program."""

    def test_same_numbers_everywhere(self):
        self.assertEqual(text.NEW_NAME_UNITS, 13)
        js = (AJ / "dashboard/public/agentname.js").read_text()
        self.assertRegex(js, rf"NEW_NAME_UNITS = {text.NEW_NAME_UNITS};")
        from agentj import cli  # noqa: F401  (the parser's help is what users read)
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "login", "-h"], cwd=HOST, capture_output=True, text=True,
                           timeout=60, env={**os.environ, "PYTHONPATH": str(HOST), "AGENTJ_STATE_DIR": tempfile.mkdtemp(prefix="aj-p393h-")})
        texts = {"cli help": " ".join(r.stdout.split())}
        for lang in ("zh", "en"):
            d = json.loads((AJ / f"dashboard/i18n/app.{lang}.json").read_text())
            texts[f"dashboard hint {lang}"] = d["app"]["name_hint"]
            texts[f"dashboard too long {lang}"] = d["app"]["name_too_long"]
        for page in ("02-install", "05-account"):
            for f in ("en.md", "zh.md", "zh.src.md"):
                body = (AJ / "site/content/docs" / page / f).read_text()
                texts[f"docs {page}/{f}"] = body
        guide = AJ.parent / "documentation/product/install.md"
        if guide.exists():
            texts["install.md"] = guide.read_text()
        for where, s in texts.items():
            self.assertIn("13", s, where)
            self.assertIn("1.6", s, where)
            if "too long" not in where:
                self.assertTrue(re.search(r"\b8\b|8 个", s), where)
        for where in [k for k in texts if k.startswith("docs 02-install") or k.startswith("dashboard hint") or k == "cli help"]:
            self.assertIsNone(OLD.search(texts[where]), f"{where} still says 1–32")
        if "install.md" in texts:
            step = texts["install.md"].split("Ask your human what this Agent should be called", 1)[1][:300]
            self.assertIsNone(OLD.search(step), "install.md naming step still says 1–32")


if __name__ == "__main__":
    unittest.main()
