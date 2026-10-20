"""P298: OpenCode's free models as the harness — no login, no key (Mac report 2026-10-14, re-measured on 1.18.34).

- Login: a key store / key variable, OR the model is one of OpenCode's free models (`opencode models opencode --verbose`:
  zero cost, not deprecated — never a list kept in the code; offline the `-free` suffix). not_logged_in only when neither.
- Probe: a free model goes through the formal chat path (`opencode serve` + Agent J's read-only session rules; the session
  is deleted); a FreeTierError from the bots' tool-free agent falls back to it; FreeTierError / 429 is retried with growing
  waits (warm-up) inside a bound, then reported as `free_tier`.
- Privacy: known free-model families whose provider keeps / trains on the conversation get one plain sentence (switch card,
  /model card, `agentj agent opencode --model`).
- Read-back: the switch job and `agentj agent <harness>` check config.json after writing it.
A stand-in `opencode` (AGENTJ_OPENCODE_BIN) answers --version, `models`, `run` and a small `serve` HTTP server.
Run (in host): .venv/bin/python -m unittest discover -s tests -p 'test_p298_opencode_free.py'
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import agent_opencode as oc, harness, opencode_free as of, switch_harness as sw  # noqa: E402
from agentj.bots import native  # noqa: E402
from agentj.bots.store import BotError  # noqa: E402

CANARY = "native-output-canary-p298"

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(sys.argv[0]))
def cfg(name, default=""):
    try: return open(os.path.join(here, name)).read().strip()
    except OSError: return default
def log(x): open(os.path.join(here, "opencode.log"), "a").write(json.dumps(x) + "\n")
args = sys.argv[1:]
if args == ["--version"]:
    print("1.18.34"); sys.exit(0)
log(args)
FREE_TIER = {"type": "error", "error": {"type": "FreeTierError", "message": "free tier ''' + CANARY + r'''"}}
if args[:2] == ["models", "opencode"]:
    for mid, cost, status in (("big-pickle", 0, "active"), ("mimo-v2.6-flash-free", 0, "active"),
                              ("space-bunny-free", 0, "active"), ("longcat-2.5-preview-free", 0, "active"), ("exo-free", 0, "deprecated"), ("gpt-6", 3, "active")):
        print("opencode/" + mid)
        print(json.dumps({"id": mid, "providerID": "opencode", "status": status,
                          "cost": {"input": cost, "output": cost, "cache": {"read": 0, "write": 0}}}, indent=2))
    sys.exit(0)
if args[:1] == ["run"]:
    sys.stdin.read()
    if cfg("run.mode") == "free_tier":
        print(json.dumps({"type": "error", "error": {"name": "APIError", "data": {"statusCode": 403,
              "responseBody": json.dumps(FREE_TIER)}}})); sys.exit(1)
    if cfg("run.mode") == "fail":
        sys.exit(1)
    print(json.dumps({"type": "text", "part": {"text": "Hello from run"}})); sys.exit(0)
if args[:1] == ["serve"]:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    port = int(args[args.index("--port") + 1])
    want = "Basic " + __import__("base64").b64encode(("opencode:" + os.environ["OPENCODE_SERVER_PASSWORD"]).encode()).decode()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"null")
        def do_POST(self):
            if self.headers.get("Authorization") != want: return self.send(401, {})
            b = self.body()
            if self.path == "/session":
                log({"session": b}); return self.send(200, {"id": "ses_1"})
            if self.path == "/session/ses_1/message":
                n = int(cfg("serve.count", "0")) + 1
                open(os.path.join(here, "serve.count"), "w").write(str(n))
                log({"message": b, "n": n, "cwd": os.getcwd()})
                fails = int(cfg("serve.fails", "0"))
                if n <= fails:
                    err = {"name": "APIError", "data": {"statusCode": 403, "message": "free tier ''' + CANARY + r'''",
                                                        "responseBody": json.dumps(FREE_TIER)}}
                    return self.send(200, {"info": {"role": "assistant", "error": err}, "parts": []})
                if cfg("serve.mode") == "no_key":
                    return self.send(200, {"info": {"role": "assistant", "error": {"name": "ProviderAuthError", "data": {}}}, "parts": []})
                return self.send(200, {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Hi ''' + CANARY + r'''"}]})
            self.send(404, {})
        def do_DELETE(self):
            log({"delete": self.path}); self.send(200, True)
    srv = HTTPServer(("127.0.0.1", port), H)
    print("opencode server listening on http://127.0.0.1:%d" % port, flush=True)
    srv.serve_forever()
sys.exit(2)
'''

VERBOSE = """opencode/space-bunny-free
{"id": "space-bunny-free", "status": "active", "cost": {"input": 0, "output": 0}}
opencode/exo-free
{
  "id": "exo-free", "status": "deprecated", "cost": {"input": 0, "output": 0}
}
opencode/gpt-6
{"id": "gpt-6", "cost": {"input": 1.25, "output": 10}}
garbage line
opencode/broken
{not json
"""


class Lists(unittest.TestCase):
    def test_free_is_zero_cost_and_not_deprecated(self):
        self.assertEqual(of.parse_verbose(VERBOSE), ["opencode/space-bunny-free"])

    def test_pick_prefers_zero_retention_and_puts_big_pickle_last(self):
        self.assertEqual(of.pick(["opencode/big-pickle", "opencode/mimo-v2.6-flash-free", "opencode/longcat-2.5-preview-free"]),
                         "opencode/longcat-2.5-preview-free")
        self.assertEqual(of.pick(["opencode/big-pickle", "opencode/cat-jam-free"]), "opencode/cat-jam-free")
        self.assertIsNone(of.pick([]))

    def test_privacy_note_only_for_known_families(self):
        for m in ("opencode/mimo-v2.6-flash-free", "opencode/big-pickle", "opencode/muse-spark-1.3-contributor-free",
                  "opencode/nemotron-3-ultra-free", "opencode/nemotron-3.5-lightning-free"):
            zh, en = of.privacy_note(m, "zh"), of.privacy_note(m, "en")
            self.assertIn(m, zh)
            self.assertIn("免费模型", zh)
            self.assertIn("free model", en)
        for m in ("opencode/space-bunny-free", "opencode/longcat-2.5-preview-free", "zhipuai/mimo-x", None):
            self.assertIsNone(of.privacy_note(m))

    def test_free_tier_error_is_its_own_class(self):
        body = json.dumps({"type": "error", "error": {"type": "FreeTierError", "message": "x"}})
        self.assertEqual(oc.provider_error_reason("APIError", {"statusCode": 403, "responseBody": body}), "free_tier")
        self.assertIn("free_tier", oc.PROVIDER_NOTES)
        self.assertEqual(oc.provider_error_reason("APIError", {"statusCode": 403, "message": "forbidden"}), "access")
        self.assertEqual(of.classify({"name": "APIError", "data": {"statusCode": 429}}), "free_tier")
        self.assertEqual(of.classify({"name": "ProviderAuthError", "data": {}}), "native_failed")


class Fake(unittest.TestCase):
    """A stand-in opencode with no key store; the free list on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p298-")
        self.addCleanup(self.tmp.cleanup)
        self.bin = pathlib.Path(self.tmp.name)
        exe = self.bin / "opencode"
        exe.write_text(FAKE)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        self.exe = str(exe)
        env = {"AGENTJ_OPENCODE_BIN": self.exe, "XDG_DATA_HOME": str(self.bin / "data"), "AGENTJ_OC_FREE_LIST": "on",
               "XDG_CONFIG_HOME": str(self.bin / "conf"), "AGENTJ_STATE_DIR": str(self.bin / "state"),
               "AGENTJ_CLAUDE_BIN": str(self.bin / "none"), "AGENTJ_CODEX_BIN": str(self.bin / "none"),
               "AGENTJ_DSH_BIN": str(self.bin / "none"), "AGENTJ_KIMI_BIN": str(self.bin / "none"),
               "AGENTJ_QWEN_BIN": str(self.bin / "none")}   # Only the fixture's OpenCode is installed, regardless of developer PATH.
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in harness.OPENCODE_KEY_ENV + ("OPENCODE_CONFIG_CONTENT",):
            os.environ.pop(k, None)
        of._cache.clear()
        self.addCleanup(of._cache.clear)
        w = mock.patch.object(of, "WARM_WAITS", (0.05, 0.05, 0.05))
        w.start()
        self.addCleanup(w.stop)

    def set(self, name, value):
        (self.bin / name).write_text(str(value))

    def log(self):
        p = self.bin / "opencode.log"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def oc(self, d):
        return next(h for h in d["harnesses"] if h["name"] == "opencode")

    def key_store(self):
        (self.bin / "data" / "opencode").mkdir(parents=True, exist_ok=True)
        (self.bin / "data" / "opencode" / "auth.json").write_text("{}")


class Login(Fake):
    def test_free_model_without_auth_is_usable(self):
        st, where = harness.opencode_login("1.18.34", "opencode/space-bunny-free")
        self.assertEqual(st, "free")
        self.assertIn("opencode/space-bunny-free", where)
        self.assertEqual(harness.opencode_login("1.18.34", "opencode/exo-free")[0], "warn")       # deprecated
        self.assertEqual(harness.opencode_login("1.18.34", "opencode/gpt-6")[0], "warn")          # paid, no key
        self.assertEqual(harness.opencode_login("1.18.34", "zhipuai/glm-5.3")[0], "warn")

    def test_a_key_store_still_counts_first(self):
        self.key_store()
        self.assertEqual(harness.opencode_login("1.18.34", "zhipuai/glm-5.3")[0], "ok")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "x"}):
            os.remove(self.bin / "data" / "opencode" / "auth.json")
            self.assertEqual(harness.opencode_login("1.18.34", "zhipuai/glm-5.3")[0], "env")

    def test_no_model_set_and_free_models_offered(self):
        st, where = harness.opencode_login("1.18.34")
        self.assertEqual(st, "free")
        self.assertIn("opencode/longcat-2.5-preview-free", where)       # Space Bunny is no longer suggested

    def test_offline_list_falls_back_to_the_name(self):
        with mock.patch.dict(os.environ, {"AGENTJ_OC_FREE_LIST": "off"}):
            self.assertEqual(of.status("opencode/cat-jam-free", self.exe), "name")
            self.assertIsNone(of.status("opencode/big-pickle", self.exe))
            self.assertEqual(harness.opencode_login("1.18.34", "opencode/cat-jam-free")[0], "free")
            self.assertEqual(harness.opencode_login("1.18.34")[0], "warn")     # nothing set, nothing known

    def test_model_from_opencode_config(self):
        (self.bin / "conf" / "opencode").mkdir(parents=True)
        (self.bin / "conf" / "opencode" / "opencode.jsonc").write_text(
            '{\n  // my default\n  "model": "opencode/mimo-v2.6-flash-free", /* x */\n  "theme": "https://a//b",\n}\n')
        self.assertEqual(harness.opencode_model(), "opencode/mimo-v2.6-flash-free")
        with mock.patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": '{"model": "zhipuai/glm-5.3"}'}):
            self.assertEqual(harness.opencode_model(), "zhipuai/glm-5.3")
            self.assertEqual(harness.opencode_login("1.18.34")[0], "warn")

    def test_detect_reports_free_and_decides(self):
        d = harness.detect()
        h = self.oc(d)
        self.assertEqual((h["logged_in"], h["login"], h["free_model"]), (True, "free", "opencode/longcat-2.5-preview-free"))
        self.assertEqual(d["decision"], "use:opencode")
        self.assertNotIn("privacy", h)
        self.assertNotIn(CANARY, json.dumps(d))

    def test_no_login_only_when_neither(self):
        self.assertFalse(harness.no_login("opencode", "opencode/space-bunny-free", "1.18.34"))
        self.assertTrue(harness.no_login("opencode", "zhipuai/glm-5.3", "1.18.34"))
        self.assertFalse(harness.no_login("opencode", "zhipuai/glm-5.3", "2.0.23"))      # v2 keeps keys in its database
        self.key_store()
        self.assertFalse(harness.no_login("opencode", "zhipuai/glm-5.3", "1.18.34"))


class Probe(Fake):
    def probe(self, model, timeout=20):
        return self.oc(harness.probe(only=("opencode",), model=model, timeout=timeout))

    def test_free_model_takes_the_formal_path(self):
        h = self.probe("opencode/mimo-v2.6-flash-free")
        self.assertTrue(h["callable"], h)
        log = self.log()
        self.assertFalse(any(x[:1] == ["run"] for x in log if isinstance(x, list)))     # the refused agent is not tried
        self.assertTrue(any(x[:1] == ["serve"] for x in log if isinstance(x, list)))
        ses = next(x["session"] for x in log if isinstance(x, dict) and "session" in x)
        rules = ses["permission"]
        self.assertEqual(rules[0], {"permission": "*", "pattern": "*", "action": "deny"})     # research: ask → deny
        self.assertIn({"permission": "read", "pattern": "*", "action": "allow"}, rules)    # tools still defined
        self.assertIn({"permission": "webfetch", "pattern": "*", "action": "deny"}, rules)
        self.assertFalse(any(r["action"] == "ask" for r in rules))
        msg = next(x for x in log if isinstance(x, dict) and "message" in x)
        self.assertEqual(msg["message"]["model"], {"providerID": "opencode", "modelID": "mimo-v2.6-flash-free"})
        self.assertIn("agentj-oc-probe-", msg["cwd"])
        self.assertIn({"delete": "/session/ses_1"}, log)
        self.assertNotIn(CANARY, json.dumps(h))

    def test_free_tier_from_the_native_agent_falls_back(self):
        self.key_store()
        self.set("run.mode", "free_tier")
        h = self.probe("opencode/gpt-6")                     # paid, keyed: native first, refused as FreeTierError
        self.assertTrue(h["callable"], h)
        kinds = [x[0] for x in self.log() if isinstance(x, list)]
        self.assertEqual(kinds[-2:], ["run", "serve"])

    def test_warm_up_retries_then_answers(self):
        self.set("serve.fails", 2)
        t0 = time.monotonic()
        h = self.probe("opencode/space-bunny-free")
        self.assertTrue(h["callable"], h)
        self.assertEqual(len([x for x in self.log() if isinstance(x, dict) and "message" in x]), 3)
        self.assertLess(time.monotonic() - t0, 15)

    def test_still_refused_after_the_warm_up_is_free_tier(self):
        self.set("serve.fails", 99)
        h = self.probe("opencode/space-bunny-free")
        self.assertEqual((h["callable"], h["probe_reason"]), (False, "free_tier"))
        self.assertEqual(len([x for x in self.log() if isinstance(x, dict) and "message" in x]), 4)   # 1 + 3 retries
        self.assertIn("免费通道", sw.reason_text("free_tier", "opencode", "zh"))
        self.assertIn("free tier", sw.reason_text("free_tier", "opencode", "en"))

    def test_retries_stay_inside_the_bound(self):
        clock = [0.0]
        with mock.patch.object(of, "_port", side_effect=OSError("no")):
            with self.assertRaises(OSError):
                of.probe(self.exe, None, dict(os.environ), 1, clock=lambda: clock[0])
        self.set("serve.fails", 99)
        slept = []
        t = [0.0]

        def sleep(s):
            slept.append(s)
            t[0] += s
        with self.assertRaises(of.ProbeError) as e:
            of.probe(self.exe, "opencode/space-bunny-free", dict(os.environ), 5, sleep=sleep,
                     clock=lambda: t[0], waits=(10, 20, 40))
        self.assertEqual(e.exception.args[0], "free_tier")
        self.assertEqual(slept, [10, 20, 40])

    def test_paid_model_without_a_key_is_not_logged_in(self):
        self.set("run.mode", "fail")
        h = self.probe("zhipuai/glm-5.3")
        self.assertEqual((h["callable"], h["probe_reason"]), (False, "not_logged_in"))
        self.assertIn("opencode auth login", sw.reason_text("not_logged_in", "opencode", "zh"))

    def test_other_failures_on_the_formal_path_keep_a_fixed_code(self):
        self.set("serve.mode", "no_key")
        h = self.probe("opencode/space-bunny-free")
        self.assertEqual((h["callable"], h["probe_reason"]), (False, "native_failed"))


class Native(unittest.TestCase):
    def test_run_and_parse_name_free_tier(self):
        with tempfile.TemporaryDirectory() as d:
            exe = pathlib.Path(d) / "x"
            # Consume the prompt before exiting, like OpenCode: avoid a fast-exit stdin BrokenPipe race.
            exe.write_text('#!/bin/sh\ncat >/dev/null\necho \'{"type":"error","error":{"data":{"responseBody":"FreeTierError"}}}\'\nexit 1\n')
            exe.chmod(0o755)
            with self.assertRaises(BotError) as e:
                native.run([str(exe)], "hi", dict(os.environ), d, timeout=10)
            self.assertEqual(e.exception.args[0], "free_tier")
        with self.assertRaises(BotError) as e:
            native.parse("opencode", json.dumps({"type": "error", "error": {"name": "APIError", "data": {
                "responseBody": "{\"error\":{\"type\":\"FreeTierError\"}}"}}}))
        self.assertEqual(e.exception.args[0], "free_tier")
        with self.assertRaises(BotError) as e:
            native.parse("opencode", json.dumps({"type": "tool_use"}))
        self.assertEqual(e.exception.args[0], "native_tool_refused")


class Switch(unittest.TestCase):
    def setUp(self):
        from test_p155_switch_harness import mk_state
        self.tmp = tempfile.TemporaryDirectory(prefix="p298-sw-")
        self.addCleanup(self.tmp.cleanup)
        self.mk_state = mk_state

    def test_switch_card_carries_the_privacy_line(self):
        st = self.mk_state(self.tmp.name, "claude")

        def launch(st_, argv):
            sw.write_receipt(st_, {"status": "probe_ok", "target": "opencode", "from": "claude",
                                   "job": argv[argv.index("--job") + 1], "model": "opencode/mimo-v2.6-flash-free"})
        r = sw.request(st, "opencode", "opencode/mimo-v2.6-flash-free", dry_run=True, launch=launch, sleep=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["model"], "opencode/mimo-v2.6-flash-free")
        self.assertIn("免费模型", r["privacy"])
        text = sw._say(r, "zh")
        self.assertIn("检查通过", text)
        self.assertIn("opencode/mimo-v2.6-flash-free 是 OpenCode 的免费模型", text)
        r2 = sw.request(st, "opencode", "opencode/space-bunny-free", dry_run=True, sleep=lambda s: None,
                        launch=lambda st_, argv: sw.write_receipt(st_, {"status": "probe_ok", "target": "opencode",
                                                                         "job": argv[argv.index("--job") + 1],
                                                                         "model": "opencode/space-bunny-free"}))
        self.assertNotIn("privacy", r2)

    def test_the_job_reads_the_config_back(self):
        st = self.mk_state(self.tmp.name, "claude", model="claude-opus-5-5")
        self.assertTrue(sw._reads_back(st, "claude", "claude-opus-5-5"))
        self.assertFalse(sw._reads_back(st, "claude", None))
        self.assertFalse(sw._reads_back(st, "opencode", "claude-opus-5-5"))
        from test_p155_switch_harness import FakeServe, fake_probe
        fake = FakeServe(st, "claude")
        before = (st.root / "config.json").read_bytes()

        def apply_wrong(st_, target, directory, model, effort, raw):     # something rewrites the entry in between
            st_.set_agent_config(target, directory, "opencode/other-free")
        with mock.patch.object(sw, "_apply", apply_wrong):
            code = sw.worker(st, "opencode", "opencode/space-bunny-free", job="j1", probe=fake_probe(), ctl=fake,
                             restart=lambda: None, ready=lambda st_, limit: "ready",
                             status=lambda: {"installed": True, "active": "active"}, sleep=lambda s: None)
        self.assertEqual(code, 1)
        rec = sw.read_receipt(st)
        self.assertEqual((rec["status"], rec["reason"]), ("failed", "config_mismatch"))
        self.assertEqual(json.loads((st.root / "config.json").read_bytes())["agent"], json.loads(before)["agent"])
        self.assertIn("读回来对不上", sw.reason_text("config_mismatch", "opencode", "zh"))

    def test_waits_cover_the_warm_up(self):
        self.assertGreaterEqual(sw.PROBE_WAIT, 2 * harness.PROBE_TIMEOUT + sum(of.WARM_WAITS))


if __name__ == "__main__":
    unittest.main()
