"""OpenCode adapter (ADR-A55 – A58; PROTOCOL §8): `opencode serve` + SSE + phone approvals, our permission rules.

Units: the session ruleset (ours last, the human's denies after it, never "always"), OpenCode's matcher (ported), the
read-only bash list agrees with the danger list (no dangerous command of test_danger's table is allowed without a card),
OPENCODE_CONFIG_CONTENT / environment, request → card mapping, harness detection, `jarvis agent opencode --model`.
Chain (a stand-in `opencode serve`, tests/fakeopencode.py, started by serve inside the fence): message → reply, rm → danger
card → reject (with the reason) → file stays, approve → once → runs, batch for low risk / never for danger, the human's own
deny stays deny, a wrong password is refused, the event stream drops and comes back (missed text caught up), a reply
serve did not send stops OpenCode, resume re-applies our rules, the process exiting → notice. The real OpenCode runs in
`tests/e2e_agent.mjs --opencode`.
"""
import asyncio
import base64
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jarvis_host import agent as agents  # noqa: E402
from jarvis_host import agent_opencode as oc  # noqa: E402
from jarvis_host import approvals, danger, fence, harness  # noqa: E402

from test_danger import CASES, _sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402
import fakeopencode  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def _parts(cmd: str) -> list[str]:
    """Sub-commands roughly as OpenCode's parser splits a command line (lists, pipelines; substitutions kept in place AND
    extracted). Conservative for this test: more parts can only make "allowed without a card" harder."""
    out = [p.strip() for p in re.split(r"\|\||&&|[;|\n&]", cmd) if p.strip()]
    out += [m.strip() for m in re.findall(r"\$\(([^)]*)\)|`([^`]*)`", cmd) for m in m if m.strip()]
    return out or [cmd]


def runs_without_card(cmd: str, rules) -> bool:
    agent_rules = fakeopencode.DEFAULT_RULES
    pats = _parts(cmd)
    return all(oc.evaluate("bash", p, agent_rules, rules) == "allow" for p in pats) and \
        oc.evaluate("bash", cmd, agent_rules, rules) == "allow"


# ------------------------------------------------------------------ units
class Rules(unittest.TestCase):
    def test_matcher_is_opencodes(self):
        w = oc.wildcard
        self.assertTrue(w("ls", "ls *"))              # a trailing " *" also matches the bare command
        self.assertTrue(w("ls -la", "ls *"))
        self.assertFalse(w("lsof", "ls *"))
        self.assertTrue(w("git status", "git status *"))
        self.assertFalse(w("FOO=1 ls", "ls *"))
        self.assertTrue(w("a.b", "a.b") and not w("aXb", "a.b"))   # regex characters are literal
        self.assertTrue(w("x\ny", "*\n*"))
        self.assertTrue(w("bash", "*") and w("anything", "*"))
        self.assertEqual(oc.evaluate("bash", "rm x", [{"permission": "*", "pattern": "*", "action": "allow"}],
                                     [{"permission": "bash", "pattern": "*", "action": "ask"}]), "ask")   # last wins
        self.assertEqual(oc.evaluate("edit", "a", []), "ask")    # no rule → ask

    def test_our_rules_ask_by_default_and_never_widen(self):
        r = oc.our_rules()
        self.assertEqual(r[0], {"permission": "*", "pattern": "*", "action": "ask"})
        self.assertNotIn("always", json.dumps(r))
        ev = lambda p, s: oc.evaluate(p, s, fakeopencode.DEFAULT_RULES, r)  # noqa: E731
        for perm, pat, want in [("bash", "rm -rf x", "ask"), ("edit", "a.txt", "ask"), ("external_directory", "/x/*", "ask"),
                                ("doom_loop", "bash", "ask"), ("websearch", "q", "ask"), ("gmail_send", "*", "ask"),
                                ("task", "general", "deny"), ("question", "*", "deny"), ("read", "a.py", "allow"),
                                ("read", "x/.env", "ask"), ("read", ".env.local", "ask"), ("read", ".env.example", "allow"),
                                ("webfetch", "https://x", "allow"), ("glob", "*", "allow"), ("bash", "ls -la", "allow"),
                                ("bash", "git status", "allow"), ("bash", "cat a > b", "ask"), ("bash", "ls $(rm x)", "ask"),
                                ("bash", "git diff --output=/x", "ask"), ("bash", "pwd", "allow"), ("bash", "pwd -P", "ask")]:
            self.assertEqual(ev(perm, pat), want, (perm, pat))

    def test_read_only_bash_list_agrees_with_the_danger_list(self):
        rules = oc.session_rules(fakeopencode.DEFAULT_RULES)
        for cmd in ["ls", "ls -la src", "pwd", "cat README.md", "git status", "git status --short", "git diff", "git diff HEAD~1",
                    "git log --oneline -5"]:
            self.assertTrue(runs_without_card(cmd, rules), cmd)
            self.assertFalse(danger.classify("Bash", {"command": cmd}).danger, cmd)
        n = 0
        for tool, inp, cats in CASES:
            if tool != "Bash" or not cats:
                continue
            n += 1
            self.assertFalse(runs_without_card(inp, rules), f"dangerous command would run without a card: {inp}")
        self.assertGreater(n, 80)

    def test_session_rules_keep_the_humans_denies_and_opencodes_own_folders(self):
        human = fakeopencode.DEFAULT_RULES + [
            {"permission": "bash", "pattern": "*", "action": "allow"}, {"permission": "bash", "pattern": "rm *", "action": "allow"},
            {"permission": "bash", "pattern": "cat *", "action": "deny"}, {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "external_directory", "pattern": "/h/.local/share/opencode/tool-output/*", "action": "allow"},
            {"permission": "external_directory", "pattern": "/srv/u/projects/*", "action": "allow"}]
        r = oc.session_rules(human)
        self.assertEqual(r[0], {"permission": "*", "pattern": "*", "action": "ask"})
        ev = lambda p, s: oc.evaluate(p, s, human, r)  # noqa: E731
        self.assertEqual(ev("bash", "rm x"), "ask", "the human's allow becomes a question to the phone")
        self.assertEqual(ev("bash", "cat a"), "deny", "the human's deny stays deny (although cat is on our read-only list)")
        self.assertEqual(ev("edit", "a"), "ask", "the human's '*': allow does not survive")
        self.assertEqual(ev("external_directory", "/h/.local/share/opencode/tool-output/x"), "allow")
        self.assertEqual(ev("external_directory", "/srv/u/projects/x"), "ask", "only OpenCode's own folders stay allowed")
        self.assertEqual(ev("plan_enter", "*"), "deny")
        self.assertTrue(oc.ends_with_ours([{"permission": "bash", "pattern": "*", "action": "allow"}] + r, r))
        self.assertFalse(oc.ends_with_ours(r + [{"permission": "bash", "pattern": "*", "action": "allow"}], r))
        self.assertFalse(oc.ends_with_ours([], r))

    def test_config_and_environment(self):
        c = json.loads(oc.config_content('{"model":"zhipuai/glm-5.3","autoupdate":true,"share":"auto"}'))
        self.assertEqual(c, {"model": "zhipuai/glm-5.3", "autoupdate": False, "share": "disabled"})
        self.assertEqual(json.loads(oc.config_content("not json")), {"autoupdate": False, "share": "disabled"})
        self.assertNotIn("permission", oc.config_content(None), "permissions go on the session (ADR-A56)")
        e = oc.opencode_env({"PATH": "/bin"}, "pw")
        for k in ("OPENCODE_DISABLE_MODELS_FETCH", "OPENCODE_DISABLE_LSP_DOWNLOAD", "OPENCODE_DISABLE_AUTOUPDATE",
                  "OPENCODE_DISABLE_SHARE"):
            self.assertEqual(e[k], "1")
        self.assertEqual((e["OPENCODE_SERVER_PASSWORD"], e["OPENCODE_SERVER_USERNAME"]), ("pw", "opencode"))
        a = oc.OpenCodeAgent(None, {"kind": "opencode", "dir": "/tmp", "model": None}).argv(4321)
        self.assertEqual(a[1:], ["serve", "--hostname", "127.0.0.1", "--port", "4321"])
        self.assertEqual(oc.split_model("zhipuai/glm-5.3"), {"providerID": "zhipuai", "modelID": "glm-5.3"})
        self.assertEqual(oc.split_model("openrouter/qwen/qwen3-coder"), {"providerID": "openrouter", "modelID": "qwen/qwen3-coder"})
        self.assertIsNone(oc.split_model("glm-5.3"))

    def test_requests_map_to_cards_the_danger_list_understands(self):
        t = oc.to_tool
        self.assertEqual(t({"permission": "bash", "patterns": ["rm x"], "metadata": {"command": "rm x"}}, "/w"),
                         ("Bash", {"command": "rm x"}))
        tool, inp = t({"permission": "edit", "patterns": ["w/.env"], "metadata": {"filepath": "/w/.env",
                        "diff": "--- a\n+++ b\n@@ -1,1 +1,1 @@\n-A=1\n+A=2\n"}}, "/")
        self.assertEqual((tool, inp["file_path"], inp["old_string"], inp["new_string"]), ("Edit", "/w/.env", "A=1", "A=2"))
        self.assertIn("credentials", danger.classify(tool, inp).cats)
        tool, inp = t({"permission": "edit", "patterns": ["w/n.txt"], "metadata": {"filepath": "/w/n.txt",
                        "diff": "--- a\n+++ b\n@@ -0,0 +1,1 @@\n+hello\n"}}, "/")
        self.assertEqual((tool, inp), ("Write", {"file_path": "/w/n.txt", "content": "hello"}))
        tool, inp = t({"permission": "edit", "patterns": ["w/a.py", "h/.ssh/config"], "metadata": {"diff": "x"}}, "/")
        self.assertEqual(inp["file_path"], "/h/.ssh/config", "a patch over several files is judged by its riskiest path")
        self.assertEqual(t({"permission": "read", "patterns": ["w/.env"], "metadata": {}}, "/"), ("Read", {"file_path": "/w/.env"}))
        self.assertEqual(t({"permission": "external_directory", "patterns": ["/o/*"], "metadata": {"parentDir": "/o"}}, "/")[0],
                         "ExternalDirectory")
        tool, inp = t({"permission": "gmail_send_email", "patterns": ["*"], "metadata": {}}, "/")
        self.assertEqual(tool, "mcp__gmail_send_email")
        self.assertIn("send", danger.classify(tool, inp).cats)


class Detect(unittest.TestCase):
    def test_opencode_login_is_existence_only_and_counts_as_a_harness(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"XDG_DATA_HOME": d, "PATH": os.environ["PATH"]}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(harness.opencode_login()[0], "warn")
                os.environ["ZHIPU_API_KEY"] = "x"
                st, where = harness.opencode_login()
                self.assertEqual(st, "env")
                self.assertIn("ZHIPU_API_KEY", where)
                self.assertNotIn("x)", where.replace("(name only)", ""))
                del os.environ["ZHIPU_API_KEY"]
                os.makedirs(os.path.join(d, "opencode"))
                p = pathlib.Path(d, "opencode", "auth.json")
                p.write_text('{"zhipuai":{"type":"api","key":"SECRET-NEVER-READ"}}')
                p.chmod(0)                                   # unreadable: existence is all we look at
                self.assertEqual(harness.opencode_login()[0], "ok")
                bins = pathlib.Path(d, "bin")
                bins.mkdir()
                for n in ("opencode", "claude"):
                    f = bins / n
                    f.write_text("#!/bin/sh\necho 1.0\n")
                    f.chmod(0o700)
                os.environ.update(AGENTJARVIS_OPENCODE_BIN=str(bins / "opencode"), AGENTJARVIS_CLAUDE_BIN="/nonexistent",
                                  AGENTJARVIS_CODEX_BIN="/nonexistent", HOME=d)
                r = harness.detect()
                oc_rec = next(h for h in r["harnesses"] if h["name"] == "opencode")
                self.assertTrue(oc_rec["supported"] and oc_rec["installed"] and oc_rec["logged_in"])
                self.assertEqual(r["decision"], "use:opencode")
                os.environ["AGENTJARVIS_CLAUDE_BIN"] = str(bins / "claude")
                pathlib.Path(d, ".claude").mkdir()
                pathlib.Path(d, ".claude", ".credentials.json").write_text("{}")
                self.assertEqual(harness.detect()["decision"], "ask_owner")
                p.chmod(0o600)

    def test_cli_agent_opencode_model_shape(self):
        from jarvis_host import cli
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            work = pathlib.Path(d, "w")
            work.mkdir()
            with mock.patch.dict(os.environ, {"AGENTJARVIS_STATE_DIR": str(st.root)}):
                with self.assertRaises(SystemExit) as e, redirect_stdout(io.StringIO()):
                    cli.main(["agent", "opencode", "--dir", str(work), "--model", "glm-5.3"])
                self.assertIn("服务商/模型", str(e.exception))
                with redirect_stdout(io.StringIO()) as out:
                    cli.main(["agent", "opencode", "--dir", str(work), "--model", "zhipuai/glm-5.3"])
                self.assertIn("OpenCode", out.getvalue())
                c = st.agent_config()
                self.assertEqual((c["kind"], c["model"], c["fence"]), ("opencode", "zhipuai/glm-5.3", True))


# ------------------------------------------------------------------ the chain with a stand-in `opencode serve`
class Chain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.log = self.work / ".oc.jsonl"          # inside the agent's folder: the fence hides the rest of /tmp
        self.envlog = self.work / ".oc-env.json"
        wrapper = pathlib.Path(self.tmp.name) / "opencode"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeopencode.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = {"AGENTJARVIS_OPENCODE_BIN": str(wrapper), "FAKE_OC_LOG": str(self.log), "FAKE_OC_ENV_LOG": str(self.envlog),
                    "AGENTJARVIS_TEST_OC_WATCH": "1"}
        os.environ.update(self.env)
        self.fenced = fence.problem(self.st, str(self.work)) is None
        if sys.platform.startswith("linux") and shutil.which("bwrap"):
            self.assertTrue(self.fenced, "the stand-in runs inside the fence where bubblewrap works")
        self.st.set_agent_config("opencode", str(self.work), fence=self.fenced)

    def tearDown(self):
        for k in list(self.env) + ["FAKE_OC_AGENT_RULES", "FAKE_OC_SESSIONS"]:
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def reqs(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_chain(self, script, ttl=4):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = ttl
        oc.WATCH = 1.0

        def msgs():
            return [o for _, o in sent if o["t"] == "msg" and o.get("from") in ("agent", "notice")]

        async def wait(pred, ms=20000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-6:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = _ready(host, ph)
            n = [0]

            async def say(text):
                n[0] += 1
                await host._app(s, {"t": "msg", "id": f"{n[0]:016x}", "text": text, "ts": 0})
            ctx = dict(host=host, sent=sent, msgs=msgs, wait=wait, say=say, s=s, ph=ph,
                       asks=lambda: [o for _, o in sent if o["t"] == "ask"],
                       statuses=lambda: [o["s"] for _, o in sent if o["t"] == "status"])
            try:
                await script(ctx)
            finally:
                host.stopping.set()
                await run
        asyncio.run(go())

    def test_message_danger_card_reject_approve_batch_and_rules(self):
        (self.work / "victim.txt").write_text("keep me")
        (self.work / "secret.txt").write_text("s")
        os.environ["FAKE_OC_AGENT_RULES"] = json.dumps(fakeopencode.DEFAULT_RULES + [
            {"permission": "bash", "pattern": "rm *", "action": "allow"},          # the human allows rm …
            {"permission": "bash", "pattern": "cat secret*", "action": "deny"}])   # … and denies reading one file

        async def script(c):
            # 1. message → reply; working → idle
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["wait"](lambda: c["statuses"]()[-1:] == ["idle"])
            self.assertIn("working", c["statuses"]())
            # 2. rm (allowed by the human's own rules) → a danger card anyway → reject → file stays, the model is told why
            await c["say"]("RUN: rm victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 1)
            ask = c["asks"]()[-1]
            self.assertEqual((ask["tool"], ask["summary"], ask["cat"]), ("Bash", "rm victim.txt", ["delete"]))
            self.assertNotIn("batch", ask, "a dangerous card offers no batch approval")
            self.assertEqual(c["statuses"]()[-1], "waiting")
            await c["host"]._app(c["s"], c["ph"].answer(ask, False))
            await c["wait"](lambda: any(m["text"].startswith("被拒绝: 用户在手机上拒绝") for m in c["msgs"]()))
            self.assertTrue((self.work / "victim.txt").exists())
            # 3. approve → once → runs
            await c["say"]("RUN: touch made.txt")
            await c["wait"](lambda: len(c["asks"]()) == 2)
            await c["host"]._app(c["s"], c["ph"].answer(c["asks"]()[-1], True))
            await c["wait"](lambda: any(m["text"] == "ran: touch made.txt" for m in c["msgs"]()))
            self.assertTrue((self.work / "made.txt").exists())
            # 4. low risk: batch → the second touch of the turn runs without a card; rm in the same turn still asks
            await c["say"]("RUNSEQ: touch b1.txt ;; touch b2.txt ;; rm victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 3)
            low = c["asks"]()[-1]
            self.assertTrue(low.get("batch", "").startswith("Bash：touch"))
            await c["host"]._app(c["s"], _sign(c["ph"], low, "allow_batch", low["batch"]))
            await c["wait"](lambda: len(c["asks"]()) == 4)
            self.assertTrue((self.work / "b2.txt").exists())
            self.assertTrue(any(o["t"] == "auto" for _, o in c["sent"]))
            self.assertEqual(c["asks"]()[-1]["cat"], ["delete"])
            await c["host"]._app(c["s"], c["ph"].answer(c["asks"]()[-1], False))
            await c["wait"](lambda: c["statuses"]()[-1:] == ["idle"])
            # 5. read-only: no card; the human's deny: no card, refused by OpenCode itself
            await c["say"]("RUN: ls")
            await c["wait"](lambda: any(m["text"] == "ran: ls" for m in c["msgs"]()))
            await c["say"]("RUN: cat secret.txt")
            await c["wait"](lambda: any(m["text"] == "被规则拒绝: cat secret.txt" for m in c["msgs"]()))
            self.assertEqual(len(c["asks"]()), 4)
            # 6. nobody answers → deny after the ttl
            await c["say"]("EDIT: victim.txt")
            await c["wait"](lambda: len(c["asks"]()) == 5)
            self.assertEqual(c["asks"]()[-1]["tool"], "Edit")
            await c["wait"](lambda: any(m["text"] == "被拒绝: edit" for m in c["msgs"]()), 15000)
            self.assertEqual((self.work / "victim.txt").read_text(), "keep me")
        self.run_chain(script)
        rq = self.reqs()
        replies = [r["body"] for r in rq if r.get("path", "").startswith("/permission/") and r["method"] == "POST"]
        self.assertEqual([r["reply"] for r in replies], ["reject", "once", "once", "once", "reject", "reject"])
        self.assertTrue(all(r["reply"] != "always" for r in replies), "never always")
        self.assertIn("用户在手机上拒绝", replies[0]["message"])
        self.assertFalse(any("/permissions/" in r.get("path", "") for r in rq), "only the documented reply endpoint")
        self.assertFalse(any(r.get("status") == 401 for r in rq), "serve always had the password")
        made = next(r for r in rq if r["method"] == "POST" and r["path"] == "/session")
        rules = json.loads(os.environ.get("FAKE_OC_AGENT_RULES") or "null") or fakeopencode.DEFAULT_RULES
        self.assertEqual(made["body"]["permission"], oc.session_rules(rules))
        self.assertEqual(made["body"]["permission"][-1], {"permission": "bash", "pattern": "cat secret*", "action": "deny"})
        env = json.loads(self.envlog.read_text())
        self.assertEqual(env["OPENCODE_SERVER_PASSWORD"], "set")
        self.assertEqual((env["OPENCODE_DISABLE_MODELS_FETCH"], env["OPENCODE_DISABLE_AUTOUPDATE"]), ("1", "1"))
        self.assertEqual(json.loads(env["OPENCODE_CONFIG_CONTENT"]), {"autoupdate": False, "share": "disabled"})
        log = self.st.log_path.read_text()
        self.assertNotIn("victim", log)
        self.assertEqual([(x["decision"], x["reason"]) for x in approvals.read_log(self.st)],
                         [("deny", "device"), ("allow", "device"), ("allow_batch", "device"), ("allow", "batch"),
                          ("deny", "device"), ("deny", "timeout")])

    def test_wrong_password_reconnect_foreign_reply_resume_and_exit(self):
        async def script(c):
            await c["say"]("一")
            await c["wait"](lambda: any(m["text"] == "ECHO: 一" for m in c["msgs"]()))
            a = c["host"].agent
            port, sid = a.client.port, a.sid
            # a wrong password (or none) is refused by the server
            for auth in (None, "Basic " + base64.b64encode(b"opencode:guess").decode()):
                r = urllib.request.Request(f"http://127.0.0.1:{port}/session/status", headers={"Authorization": auth} if auth else {})
                with self.assertRaises(urllib.error.HTTPError) as e:
                    await asyncio.to_thread(urllib.request.urlopen, r, None, 5)
                self.assertEqual(e.exception.code, 401)
            # the event stream drops mid-turn; the text that went by is caught up, the turn ends
            await c["say"]("DROPSSE")
            await c["wait"](lambda: any(m["text"] == "after drop" for m in c["msgs"]()))
            await c["wait"](lambda: c["statuses"]()[-1:] == ["idle"])
            log = self.st.log_path.read_text()
            self.assertIn('"status": "lost"', log)
            # a reply serve did not send (someone else used the password) → OpenCode is stopped, notice
            await c["say"]("FOREIGN: touch evil.txt")
            await c["wait"](lambda: any("绕过手机" in m["text"] for m in c["msgs"]()))
            await c["wait"](lambda: a.proc is None)
            self.assertIn('"ev": "agent_tamper"', self.st.log_path.read_text())
            await c["wait"](lambda: c["statuses"]()[-1:] == ["idle"])
            # the next message starts OpenCode again and resumes the same conversation with our rules re-applied
            os.environ["FAKE_OC_SESSIONS"] = json.dumps([sid])
            await c["say"]("二")
            await c["wait"](lambda: any(m["text"] == "ECHO: 二" for m in c["msgs"]()))
            self.assertEqual(a.sid, sid)
            # the process exits while idle → notice
            a.proc.send_signal(9)
            await c["wait"](lambda: any("OpenCode 退出了" in m["text"] for m in c["msgs"]()))
        self.run_chain(script)
        rq = self.reqs()
        self.assertEqual(sum(r["method"] == "POST" and r["path"] == "/session" for r in rq), 1)
        patch = [r for r in rq if r["method"] == "PATCH"]
        self.assertEqual(len(patch), 1)
        self.assertEqual(patch[0]["body"]["permission"], oc.session_rules(fakeopencode.DEFAULT_RULES))
        self.assertEqual(sum(1 for r in rq if r.get("path") == "/event" and r["method"] == "GET") >= 3, True)

    def test_no_opencode_and_bad_model_are_notices(self):
        os.environ["AGENTJARVIS_OPENCODE_BIN"] = str(pathlib.Path(self.tmp.name) / "missing")
        with mock.patch("shutil.which", return_value=None):
            async def script(c):
                await c["say"]("在吗")
                await c["wait"](lambda: any("没找到 OpenCode" in m["text"] for m in c["msgs"]()))
                await c["wait"](lambda: c["statuses"]()[-1:] == ["down"])
            os.environ["AGENTJARVIS_OPENCODE_BIN"] = ""
            self.run_chain(script)


if __name__ == "__main__":
    unittest.main()
