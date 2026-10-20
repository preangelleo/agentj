"""P395 (#144 P0, with #139) contract: the switch gate's probe speaks the installed OpenCode's own version.

Before: every probe ran the bots' v1 one-shot `opencode run --pure --agent agentj-bot`; OpenCode 2.x refuses `--pure`
("Unrecognized flag") and never sees an agent defined in OPENCODE_CONFIG_CONTENT, so no 2.x owner could switch to
OpenCode, and the owner only ever read "could not complete a call". Now:
- 2.x is probed through `opencode serve` on its /api protocol (session with Agent J's read-only rules in v2's shape →
  prompt → the session's messages until idle), never `run`; the probe session is deleted;
- 1.x keeps the tool-free `run` probe;
- a failure keeps its fixed code AND carries OpenCode's own error as one scrubbed line (probe_error) that reaches the
  switch gate, the receipt and the owner's text — tokens never;
measured on real 2.0.24 / 1.19.3 / 1.18.34 in reports/qa/p395/. No network, no real keys.
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from agentj import agent_opencode2 as oc2, harness, opencode_free as of, switch_harness as sh
from agentj.bots import native

HERE = pathlib.Path(__file__).resolve().parent
TOKEN = "sk-" + "a1B2c3D4" * 6

V1_SCRIPT = r'''import json, os, sys
a = sys.argv[1:]
if a[:1] == ["--version"]:
    print("1.18.34"); sys.exit(0)
if a[:1] == ["run"]:
    open(os.environ["FAKE_V1_LOG"], "a").write(" ".join(a) + "\n")
    sys.stdin.read()
    if os.environ.get("FAKE_V1_FAIL"):
        print(json.dumps({"type": "error", "error": {"name": "APIError", "data": {"message": "Incorrect API key " + os.environ["FAKE_V1_FAIL"], "statusCode": 401}}}))
        print("Error: request failed", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"type": "text", "part": {"text": "Hi there"}}))
    sys.exit(0)
sys.exit(2)
'''


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p395-oc2-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.log = root / "oc.jsonl"
        self.v1log = root / "v1.log"
        v2 = root / "opencode2"
        v2.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeopencode2.py'} \"$@\"\n")
        v1 = root / "opencode1"
        (root / "v1.py").write_text(V1_SCRIPT)
        v1.write_text(f"#!/bin/sh\nexec {sys.executable} {root / 'v1.py'} \"$@\"\n")
        for x in (v1, v2):
            x.chmod(0o700)
        self.v1, self.v2 = str(v1), str(v2)
        none = str(root / "none")
        env = {"FAKE_OC_LOG": str(self.log), "FAKE_V1_LOG": str(self.v1log), "XDG_DATA_HOME": str(root / "data"),
               "XDG_CONFIG_HOME": str(root / "cfg"), "AGENTJ_STATE_DIR": str(root / "state"), "AGENTJ_OC_FREE_LIST": "off",
               "AGENTJ_CLAUDE_BIN": none, "AGENTJ_CODEX_BIN": none, "AGENTJ_DSH_BIN": none, "AGENTJ_KIMI_BIN": none,
               "AGENTJ_QWEN_BIN": none, "FAKE_OC2_CONNECTED": "deepseek"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in harness.OPENCODE_KEY_ENV + ("OPENCODE_CONFIG_CONTENT", "FAKE_OC2_FAIL", "FAKE_OC2_ERROR_MSG", "FAKE_V1_FAIL"):
            os.environ.pop(k, None)
        of._cache.clear()
        self.addCleanup(of._cache.clear)
        w = mock.patch.object(oc2, "PROBE_POLL", 0.05)
        w.start()
        self.addCleanup(w.stop)

    def probe(self, exe, model="deepseek/chat"):
        os.environ["AGENTJ_OPENCODE_BIN"] = exe
        d = harness.probe(only=("opencode",), model=model, timeout=20)
        return next(h for h in d["harnesses"] if h["name"] == "opencode")

    def requests(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


class V2(_Base):
    def test_v2_is_probed_on_its_api_never_run(self):
        h = self.probe(self.v2)
        self.assertTrue(h["callable"], h)
        reqs = self.requests()
        self.assertFalse([r for r in reqs if r["m"] == "RUN"], "2.x must never get `run --pure`")
        made = next(r for r in reqs if r["m"] == "POST" and r["p"] == "/api/session")
        self.assertEqual(made["b"]["model"], {"id": "chat", "providerID": "deepseek"})
        rules = made["b"]["permissions"]
        self.assertTrue(rules and all(set(r) == {"action", "resource", "effect"} for r in rules), rules)
        acts = {r["action"] for r in rules}
        self.assertNotIn("bash", acts)                   # v2's names (rules_to_v2): a v1 name would match no v2 tool
        self.assertEqual(rules[-1]["effect"], "deny")     # read-only: what is not allowed is refused, never asked
        self.assertTrue(any(r["m"] == "POST" and r["p"].endswith("/prompt") for r in reqs))
        self.assertTrue(any(r["m"] == "DELETE" and r["p"].startswith("/api/session/") for r in reqs))

    def test_v2_failure_carries_opencodes_own_line(self):
        os.environ["FAKE_OC2_FAIL"] = "BADKEY"
        h = self.probe(self.v2)
        self.assertFalse(h["callable"])
        self.assertEqual(h["probe_reason"], "native_failed")
        self.assertEqual(h["probe_error"], "provider.auth 401 User not found.")

    def test_v2_line_never_carries_a_token(self):
        os.environ["FAKE_OC2_FAIL"] = "BADKEY"
        os.environ["FAKE_OC2_ERROR_MSG"] = f"Incorrect API key provided: {TOKEN}\n\nsee   docs"
        h = self.probe(self.v2)
        self.assertNotIn(TOKEN, h["probe_error"])
        self.assertNotIn("\n", h["probe_error"])
        self.assertIn("Incorrect API key provided", h["probe_error"])

    def test_v2_no_key_is_its_own_line(self):
        os.environ["FAKE_OC2_FAIL"] = "NOKEY"
        h = self.probe(self.v2)
        self.assertFalse(h["callable"])
        self.assertIn("Model unavailable: deepseek/chat", h["probe_error"])


class V1(_Base):
    def test_v1_keeps_the_tool_free_run(self):
        h = self.probe(self.v1)
        self.assertTrue(h["callable"], h)
        line = self.v1log.read_text()
        self.assertIn("run --pure --agent agentj-bot", line)
        self.assertFalse([r for r in self.requests() if r["p"].startswith("/api/")])

    def test_v1_failure_line_from_its_error_event(self):
        os.environ["FAKE_V1_FAIL"] = TOKEN
        h = self.probe(self.v1)
        self.assertFalse(h["callable"])
        self.assertIn("APIError 401 Incorrect API key", h["probe_error"])
        self.assertNotIn(TOKEN, h["probe_error"])

    def test_bots_never_read_stderr(self):
        """The visitor-facing bots call run() without `why`: stderr stays /dev/null, nothing is collected."""
        with mock.patch.object(native.subprocess, "Popen", wraps=native.subprocess.Popen) as pop:
            native.run([sys.executable, "-c", "import sys; sys.stdout.write('x')"], "", dict(os.environ), self.tmp.name, timeout=10)
        self.assertEqual(pop.call_args.kwargs["stderr"], native.subprocess.DEVNULL)


class Owner(_Base):
    def test_gate_and_owner_text_show_the_line(self):
        os.environ["FAKE_OC2_FAIL"] = "BADKEY"
        os.environ["AGENTJ_OPENCODE_BIN"] = self.v2
        g = sh.gate("opencode", "deepseek/chat")
        self.assertEqual((g["ok"], g["reason"], g["line"]), (False, "native_failed", "provider.auth 401 User not found."))
        zh = sh.reason_text(g["reason"], "opencode", "zh", line=g["line"])
        en = sh.reason_text(g["reason"], "opencode", "en", line=g["line"])
        self.assertIn("OpenCode 原话：provider.auth 401 User not found.", zh)
        self.assertIn("OpenCode said: provider.auth 401 User not found.", en)
        self.assertEqual(sh.reason_text("native_failed", "opencode", "zh"),
                         sh.REASON["zh"]["native_failed"].format(x="OpenCode"))     # no line → as before

    def test_receipt_and_scan_texts_carry_the_line(self):
        import time as _t
        line = "provider.auth 401 User not found."
        r = {"ok": False, "reason": "native_failed", "target": "opencode", "from": "claude", "line": line}
        self.assertIn("OpenCode 原话：" + line, sh._say(r, "zh"))
        self.assertIn("OpenCode said: " + line, sh._say(r, "en"))
        rec = {"status": "failed", "target": "opencode", "from": "claude", "reason": "native_failed", "line": line,
               "at": _t.time()}
        self.assertIn(line, sh._say_receipt(rec, "zh"))
        scan = {"ok": True, "current": "claude", "harnesses": [{"name": "opencode", "installed": True, "callable": False,
                                                                "version": "2.0.24", "reason": "native_failed", "line": line}]}
        self.assertIn(line, sh._say_scan(scan, "zh"))
        self.assertNotIn("原话", sh.reason_text("not_logged_in", "opencode", "zh", line=line))

    def test_v2_detection_by_version(self):
        self.assertTrue(harness.opencode_v2("opencode v2.0.24"))
        self.assertTrue(harness.opencode_v2("2.0.31"))
        self.assertFalse(harness.opencode_v2("1.18.34"))
        self.assertFalse(harness.opencode_v2(None))


if __name__ == "__main__":
    unittest.main()
