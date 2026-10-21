"""#184 (P417): the web terminal's bash history (<state>/terminal/history, 0600) keeps no key-shaped line.

- HIST_SETUP: a line with a leading space and a repeat never go in; HISTIGNORE drops TOKEN= / KEY= / PASS / SECRET / Bearer /
  URL userinfo lines — checked with the real bash the terminal starts (`--noprofile --norc --noediting -i`).
- scrub_history drops what agent_mask / the owner's masker would still change, keeps the rest, stays 0600, follows no symlink.
- end to end: a terminal opened and closed through serve leaves only the harmless lines (no setup line, nothing from
  ~/.bash_history); an older file is cleaned at open.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import term_store  # noqa: E402
from agentj.term_shell import HIST_SETUP, scrub_history  # noqa: E402

from test_p408_terminal import Rig  # noqa: E402

TOK = "ghp_" + "Z9" * 18                                  # a token shape, not a real key
LINES = ["echo kept", " echo spaced", "echo dup", "echo dup", f"export GH_TOKEN={TOK}", "export OPENAI_API_KEY=abc123def",
         "mysql -u me --password=hunter22", "curl -H 'Authorization: Bearer abc.def'", "git clone https://me:pw9@example.com/r",
         "MY_SECRET=x ./run", "echo last"]


class Bash(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash")
    def test_hist_setup_in_the_real_shell(self):
        with tempfile.TemporaryDirectory() as d:
            hist = pathlib.Path(d) / "history"
            script = "\n".join([f"HISTFILE={hist}; HISTSIZE=2000; HISTFILESIZE=2000; {HIST_SETUP}", "set -o history", *LINES,
                                "exit"]) + "\n"
            subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "--noediting", "-i"], input=script, text=True,
                           capture_output=True, timeout=20, env={"PATH": os.environ.get("PATH", ""), "TERM": "dumb", "HOME": d})
            got = hist.read_text().splitlines()
        self.assertEqual([x for x in got if x.startswith("echo")], ["echo kept", "echo dup", "echo last"])
        for bad in ("TOKEN", "API_KEY", "password", "Bearer", "pw9@", "SECRET", "spaced"):
            self.assertFalse(any(bad in x for x in got), (bad, got))


class Scrub(unittest.TestCase):
    def test_drops_key_shapes_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            hist = pathlib.Path(d) / "history"
            fd = os.open(hist, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, "".join(x + "\n" for x in LINES).encode())
            os.close(fd)
            self.assertEqual(scrub_history(hist), 6)
            self.assertEqual(hist.read_text().splitlines(), ["echo kept", " echo spaced", "echo dup", "echo dup", "echo last"])
            self.assertEqual(oct(hist.stat().st_mode & 0o777), "0o600")
            self.assertEqual(scrub_history(hist), 0)               # nothing left to drop: not rewritten
            self.assertEqual(sorted(p.name for p in pathlib.Path(d).iterdir()), ["history"])
            self.assertEqual(scrub_history(pathlib.Path(d) / "missing"), 0)

    def test_follows_no_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            real = pathlib.Path(d) / "elsewhere"
            real.write_text(f"export GH_TOKEN={TOK}\n")
            link = pathlib.Path(d) / "history"
            link.symlink_to(real)
            with self.assertRaises(OSError):
                scrub_history(link)
            self.assertIn(TOK, real.read_text())


class EndToEnd(Rig):
    def test_open_and_close_leave_no_key_line(self):
        self.passkey()
        self.enable()
        hist = term_store.root(self.st) / "history"
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(f"echo old\nexport GH_TOKEN={TOK}\n")     # what an older version left behind
        (self.home / ".bash_history").write_text("echo from-my-own-terminal\n")   # never copied into the web one

        async def go():
            ready = await self.open()
            self.assertEqual(ready["t"], "term_ready", ready)
            self.assertNotIn(TOK, hist.read_text())               # cleaned before bash reads it
            await self.line("echo one", "one")
            await self.line(" echo two", "two")
            await self.line("echo one", "one")
        self.run_async(go)
        got = hist.read_text().splitlines()
        self.assertEqual(got, ["echo old", "echo one"], got)
        self.assertEqual(oct(hist.stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
