"""P361 (PROTOCOL §34): Settings 「执行」 — a whitelisted `agentj` command run from the phone.

- the whitelist is one constant; every entry says why it is safe; nothing that stops / deletes / revokes / sets / installs.
- a run needs a signed `cmd_run` AND a passkey assertion every time: no passkey → `no_passkey`; without `fa` →
  `passkey_required` + the challenge over (n, cmd); a bad assertion / signature / replay / unknown name → refused, nothing runs.
- the argv is fixed by the host (the phone sends a name only); one run at a time (`busy`); `ctl_pending` first.
- the output is ANSI-free, credential-shaped text masked (privacy.owner_text), capped; controls.log / host.log hold the
  name and the result, never the output.
- the JS signs the same bytes.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import cmdrun, controls, passkey, wire  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
CRED = "id=" + "-".join(("ghp", "abcdefghijklmnopqrstuvwxyz0123456789AB"))   # assembled: no literal secret in the file
FA = {"id": wire.b64u(b"\x07" * 16), "cd": "e30", "ad": "AA", "sig": "AA"}


def sign(ph, cmd, *, n=None, ts=None, key=None):
    n = n or os.urandom(16).hex()
    ts = int(time.time() * 1000) if ts is None else ts
    msg = controls.signed_message(ph.channel, ph.did, "cmd_run", n, ts, controls.object_digest("cmd_run", {"cmd": cmd}))
    return {"t": "cmd_run", "r": "0123456789abcdef", "cmd": cmd, "n": n, "ts": ts, "sig": wire.b64u((key or ph.sk).sign(msg))}


class Done:
    def __init__(self, out="", err="", code=0):
        self.stdout, self.stderr, self.returncode = out, err, code


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.phone = Phone(self.st)
        self.s = _ready(self.host, self.phone)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def passkey(self):
        self.st.set_passkey(self.s.pub, {"id": FA["id"], "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                                         "rp": "m.agentj.app", "at": int(time.time())})

    def runner(self, out="✓ ok\n", code=0):
        def fake(argv, **kw):
            self.calls.append((argv, kw))
            return Done(out, "", code)
        return fake

    def send(self, obj, *, verified=True, out="✓ ok\n", code=0):
        async def go():
            with mock.patch.object(passkey, "verify_assertion", return_value=1) if verified else mock.MagicMock(), \
                    mock.patch.object(cmdrun.subprocess, "run", self.runner(out, code)):
                await self.host.cmd_desk.on_phone(self.s, obj)
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    if not self.host.cmd_desk.busy:
                        break
                await asyncio.sleep(0.02)
        asyncio.run(go())
        return [o for _, o in self.sent if o["t"] in ("ctl_res", "ctl_pending")]

    def clog(self):
        return [json.loads(x) for x in self.st.controls_path.read_text().splitlines()] if self.st.controls_path.exists() else []


class Whitelist(unittest.TestCase):
    def test_one_constant_each_with_a_reason_and_nothing_dangerous(self):
        self.assertEqual(set(cmdrun.RUN_CMDS), {"doctor", "reconnect", "status", "service_status", "rescue_status", "update_check", *["switch_"+h for h in ("claude","codex","opencode","kimi","qwen","dsh")]})
        bad = {"stop", "uninstall", "revoke", "unlink", "reset", "restart", "apply", "set", "remove", "clear", "rm",
               "install", "import-key", "passphrase", "pair", "rollback", "undo", "send", "config", "serve"}
        for name, (args, why) in cmdrun.RUN_CMDS.items():
            self.assertGreater(len(why), 20, name)
            self.assertFalse(bad & set(args), name)
            self.assertTrue(all(isinstance(a, str) and a for a in args))

    def test_argv_is_fixed_by_the_host(self):
        for name, (args, _) in cmdrun.RUN_CMDS.items():
            argv = cmdrun.argv(name)
            self.assertEqual(argv[-len(args):], list(args))
        with self.assertRaises(KeyError):
            cmdrun.argv("doctor; rm -rf ~")

    def test_output_is_masked_cleaned_and_capped(self):
        out, cut = cmdrun.clean("\x1b[32m✓\x1b[0m serve\r\n" + CRED + "\x07")
        self.assertNotIn("\x1b", out)
        self.assertNotIn(CRED.split("=", 1)[1], out)
        self.assertIn("✓ serve", out)
        self.assertFalse(cut)
        out, cut = cmdrun.clean("x" * (cmdrun.OUT_MAX + 50))
        self.assertTrue(cut)
        self.assertEqual(len(out), cmdrun.OUT_MAX)

    def test_run_never_raises(self):
        def slow(*a, **kw):
            raise subprocess.TimeoutExpired("agentj", 1)
        self.assertEqual(cmdrun.run("doctor", runner=slow)["code"], -1)

        def boom(*a, **kw):
            raise OSError("no")
        self.assertEqual(cmdrun.run("doctor", runner=boom)["code"], -2)

    def test_js_signs_the_same_bytes(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        out = []
        for f in ("protocol/wire.js", "web/public/proto/wire.js"):
            js = (f"import {{ controlMessage }} from {json.dumps((HERE.parents[1] / f).as_uri())};\n"
                  f"console.log(Buffer.from(await controlMessage('CH', 'DEV', 'cmd_run', '{'a' * 32}', 1790000000123, "
                  "{cmd: 'doctor'})).toString('hex'));")
            r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            out.append(r.stdout.strip())
        want = controls.signed_message("CH", "DEV", "cmd_run", "a" * 32, 1790000000123,
                                       controls.object_digest("cmd_run", {"cmd": "doctor"})).hex()
        self.assertEqual(out, [want, want])


class Serve(Base):
    def test_host_info_lists_the_names(self):
        self.host._cmdrun_available = (time.monotonic(), list(cmdrun.RUN_CMDS))
        self.assertEqual(self.host.host_info()["run_cmds"], list(cmdrun.RUN_CMDS))

    def test_no_passkey_runs_with_the_signed_control(self):
        # P573 (#386 ⑤): reconnect is recoverable — the signed control is the owner's button (no Face ID any more)
        res = self.send(sign(self.phone, "reconnect"), verified=False)
        self.assertTrue(res[-1]["ok"], res[-1])
        self.assertEqual(len(self.calls), 1)

    def test_face_id_every_time_then_it_runs(self):
        self.passkey()
        obj = sign(self.phone, "reconnect")
        res = self.send(obj, out="✓ serve\n" + CRED + "\n", code=0)
        self.assertEqual(res[-2]["t"], "ctl_pending")
        self.assertEqual(res[-2]["timeout_ms"], cmdrun.PENDING_MS)
        last = res[-1]
        self.assertEqual((last["ok"], last["cmd"], last["code"]), (True, "reconnect", 0))
        self.assertIn("✓ serve", last["out"])
        self.assertNotIn(CRED.split("=", 1)[1], last["out"])
        argv, kw = self.calls[-1]
        self.assertEqual(argv[-2:], ["doctor", "--reconnect"])
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["timeout"], cmdrun.TIMEOUT)
        # the same signed object again: a replay, nothing runs
        n = len(self.calls)
        res = self.send(obj)
        self.assertEqual(res[-1]["why"], "replay")
        self.assertEqual(len(self.calls), n)
        log = self.clog()
        self.assertEqual([x["result"] for x in log[-3:]], ["start", "exit:0", "refused:replay"])
        text = self.st.controls_path.read_text() + (self.st.root / "host.log").read_text()
        self.assertNotIn("✓ serve", text)
        self.assertNotIn(CRED, text)

    def test_a_failing_command_still_shows_its_output(self):
        self.passkey()
        res = self.send({**sign(self.phone, "reconnect"), "fa": FA}, out="✗ relay\n", code=1)
        self.assertEqual((res[-1]["ok"], res[-1]["code"], res[-1]["out"]), (False, 1, "✗ relay\n"))
        self.assertEqual(self.calls[-1][0][-2:], ["doctor", "--reconnect"])

    def test_refusals(self):
        self.passkey()
        other = Phone(self.st, "别的")
        for obj, why in (({**sign(self.phone, "doctor", key=other.sk), "fa": FA}, "bad_signature"),
                         ({**sign(self.phone, "doctor", ts=1), "fa": FA}, "stale"),
                         ({**sign(self.phone, "doctor"), "cmd": "status", "fa": FA}, "bad_signature"),
                         ({**sign(self.phone, "rm -rf ~"), "fa": FA}, "shape"),
                         ({**sign(self.phone, "service_restart"), "fa": FA}, "shape"),
                         ({**sign(self.phone, "doctor"), "n": "zz", "fa": FA}, "shape")):
            res = self.send(obj)
            self.assertEqual((res[-1]["ok"], res[-1]["why"]), (False, why), obj)
        self.assertEqual(self.calls, [])

    def test_a_dropped_link_never_leaves_it_busy(self):
        self.passkey()
        real = self.host.send_app

        async def drop(s, m):
            if m.get("t") == "ctl_pending":
                raise ConnectionError("relay down")
            return await real(s, m)
        self.host.send_app = drop
        res = self.send({**sign(self.phone, "doctor"), "fa": FA})
        self.host.send_app = real
        self.assertIsNone(self.host.cmd_desk.busy)
        self.assertEqual((res[-1]["t"], res[-1]["ok"]), ("ctl_res", True))
        self.assertEqual([x["result"] for x in self.clog()[-2:]], ["start", "exit:0"])

    def test_one_run_at_a_time(self):
        self.passkey()
        self.host.cmd_desk.busy = "doctor"
        res = self.send({**sign(self.phone, "status"), "fa": FA})
        self.host.cmd_desk.busy = None
        self.assertEqual(res[-1]["why"], "busy")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
