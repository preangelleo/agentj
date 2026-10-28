"""P569 (#371): an official support reply must reach the customer's isolated main Agent.

0.25.15 → 0.25.19a1: host_diagnostics passed `doctor_fn=` to support.run_thread (which has no such parameter); the TypeError
was swallowed into "background request did not complete; try later", so `agentj support thread <id>` inside the Codex /
OpenCode / Claude isolation could never show a reply. Offline: a local stand-in for the Worker's support routes
(worker/src/support.ts: signed host message + thread read, bearer admin reply), the support desk's own AdminAPI.reply when
the private desk source is present (the same wire call otherwise), a real elevate socket and a separate denied-state Agent
process running exactly the commands the identity / feedback-to-agentj-father skill tells it to run.

Also: every host_diagnostics op binds against its target's real signature, and an unexpected exception is reported by
class name (log + reason), never as "no reply yet" and never with its message text.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentj import cloud, elevate, feedback, field_tools, host_diagnostics as hd, layout_diag, names, reconnect, support, wire
from agentj.state import State

AJ = Path(__file__).resolve().parents[2]
DESK = AJ / "support" / "support_desk.py"      # private tree only; the public export uses the same wire call below
ADMIN = "adm_" + "p569" * 8                     # fake admin bearer of the stand-in server; not a real credential
REPLY = "P569 official answer: run agentj service restart, then send a test message."
_TID = re.compile(r"st_[A-Za-z0-9_-]{22}")
MODES = ("codex-macos", "codex-linux", "opencode", "claude")


class FakeWorker:
    """In-memory stand-in for the Worker's support routes (only what the host and the desk call)."""

    def __init__(self):
        self.threads, self.cv = {}, threading.Condition()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                status, obj = outer.route(self.path, self.headers, json.loads(body or b"{}"))
                raw = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def signed(self, ctx, env):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pk = wire.unb64u(env["pk"])
        Ed25519PublicKey.from_public_bytes(pk).verify(wire.unb64u(env["sig"]), f"{ctx}\n{env['body']}".encode())
        inner = json.loads(wire.unb64u(env["body"]))
        assert inner["channel"] == wire.channel_id(pk)
        return inner

    def add(self, tid, author, kind, body):
        with self.cv:
            t = self.threads[tid]
            seq = len(t["messages"]) + 1
            t["messages"].append({"seq": seq, "id": f"sm_{seq}", "author": author, "kind": kind, "body": body,
                                  "created_at": "2026-10-29T00:00:00Z"})
            if author == "support":
                t["status"] = "answered"
            self.cv.notify_all()
            return seq

    def route(self, path, headers, obj):
        if path == "/v1/support/messages":
            inner = self.signed(support.CTX_MESSAGE, obj)
            tid = inner.get("thread") or "st_" + secrets.token_urlsafe(16)[:22]
            with self.cv:
                self.threads.setdefault(tid, {"owner": inner["channel"], "status": "open", "messages": []})
            return 201, {"thread": tid, "message": "sm_x", "seq": self.add(tid, "agent", inner["kind"], inner["body"])}
        m = re.fullmatch(r"/v1/support/threads/(st_[A-Za-z0-9_-]{22})", path)
        if m:
            inner = self.signed(support.CTX_THREAD, obj)
            t = self.threads.get(m[1])
            if not t or t["owner"] != inner["channel"]:
                return 404, {"error": "no_such_thread"}
            after, until = inner["after"], time.monotonic() + min(inner["wait"], 25)
            with self.cv:   # long poll: news = a non-agent message after the cursor
                self.cv.wait_for(lambda: any(x["seq"] > after and x["author"] != "agent" for x in t["messages"])
                                 or time.monotonic() >= until, max(0, until - time.monotonic()))
                msgs = [x for x in t["messages"] if x["seq"] > after]
            return 200, {"thread": {"id": m[1], "status": t["status"], "fixed_in": None}, "messages": msgs,
                         "cursor": msgs[-1]["seq"] if msgs else after}
        if path == "/v1/admin/support/reply":
            if headers.get("authorization") != "Bearer " + ADMIN:
                return 401, {"error": "unauthorized"}
            if obj.get("thread") not in self.threads:
                return 404, {"error": "no_such_thread"}
            return 200, {"ok": True, "seq": self.add(obj["thread"], "support", "reply", obj["body"])}
        return 404, {"error": "not_found"}


def official_reply(base, thread, body):
    """The official side: support_desk.AdminAPI.reply (private tree) — else the identical bearer POST."""
    if DESK.exists():
        spec = importlib.util.spec_from_file_location("p569_support_desk", DESK)
        sd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sd)
        return sd.AdminAPI(base=base, token=ADMIN).reply(thread, body)
    import urllib.request
    req = urllib.request.Request(base + "/v1/admin/support/reply", data=json.dumps({"thread": thread, "body": body}).encode(),
                                 method="POST", headers={"authorization": "Bearer " + ADMIN, "content-type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
        return json.loads(r.read())


CHILD = '''
import os, pathlib, sys
from agentj import cli
from agentj.state import State
root = os.environ["DENIED_ROOT"]
old_open = pathlib.Path.open
def denied(self, *a, **kw):
    if str(self).startswith(root):
        raise PermissionError("sandbox denied state")
    return old_open(self, *a, **kw)
pathlib.Path.open = denied
State.exists = lambda self: (_ for _ in ()).throw(PermissionError("sandbox denied state"))
cli.main(["support", *sys.argv[1:]])
'''


class OfficialReplyReachesIsolatedAgent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p569-e2e-")
        self.st = State(Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")     # loopback relay → seat exempt (seatcert.exempt)
        self.worker = FakeWorker()
        cloud.write_cloud(self.st, {"api": self.worker.base, "host_id": "h_p569", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": 1, "last_seq": 0})
        self.env = patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        self.env.start()
        # Jev (layer 2) is an external call: offline here; layer 1 still runs for real.
        self.jev = patch.object(support.privacy, "layer2", return_value=SimpleNamespace(status="ok"))
        self.jev.start()
        self.el = elevate.Elevator(SimpleNamespace(st=self.st))
        await self.el.start()

    async def asyncTearDown(self):
        await self.el.stop()
        self.jev.stop()
        self.env.stop()
        await asyncio.to_thread(self.worker.close)
        self.tmp.cleanup()

    async def agent(self, mode, *argv):
        """The customer's main Agent: separate process, host state denied, only the elevate socket reachable."""
        env = dict(os.environ, AGENTJ_STATE_DIR=str(self.st.root), DENIED_ROOT=str(self.st.root),
                   AGENTJ_FENCED="1" if mode in ("opencode", "claude") else "0",
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", CHILD, *argv, env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 120)
        return p.returncode, out.decode(), err.decode()

    async def test_feedback_then_official_reply_then_support_thread_shows_it(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                # skill feedback-to-agentj-father: `agentj support feedback "<redacted draft>" --wait 0` → exit 5 + ticket id
                rc, out, err = await self.agent(mode, "feedback", f"Reply never shows up ({mode})", "--wait", "0")
                self.assertEqual(rc, support.EXIT_PENDING, (out, err))
                tid = _TID.search(out)[0]
                rc, out, err = await self.agent(mode, "thread", tid)
                self.assertEqual(rc, 0, (out, err))
                self.assertIn("status open", out)                # read works before the reply too
                self.assertNotIn(REPLY, out)
                await asyncio.to_thread(official_reply, self.worker.base, tid, f"{REPLY} [{mode}]")
                # skill: "read it later with `agentj support thread <id>`"
                rc, out, err = await self.agent(mode, "thread", tid)
                self.assertEqual(rc, 0, (out, err))
                self.assertIn(support.FENCE_OPEN, out)
                self.assertIn(support.TEXT + f"{REPLY} [{mode}]", out)
                self.assertIn(f"Reply never shows up ({mode})", out)       # mine_too: the thread as a whole
                self.assertNotIn("Background request", out)
                rc, out, err = await self.agent(mode, "list")
                self.assertEqual(rc, 0, (out, err))
                self.assertIn(tid, out)

    async def test_ask_waits_and_reads_the_reply_written_meanwhile(self):
        """identity core: `agentj support ask "<question>" --attach-doctor` waits for the answer through the same path."""
        before = set(self.worker.threads)

        def desk():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                new = set(self.worker.threads) - before
                if new:
                    return official_reply(self.worker.base, new.pop(), REPLY)
                time.sleep(0.05)
        with patch.object(hd, "feedback_snapshot", return_value="Agent J test\nok"):
            desk_job = asyncio.create_task(asyncio.to_thread(desk))
            rc, out, err = await self.agent("codex-linux", "ask", "Why is my phone grey?", "--attach-doctor", "--wait", "30")
            await desk_job
        self.assertEqual(rc, support.EXIT_OK, (out, err))
        self.assertIn(support.TEXT + REPLY, out)


class EveryOpBindsItsTarget(unittest.TestCase):
    """A dispatched call whose arguments do not fit the target's signature fails here, not silently in production."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p569-sig-")
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    CASES = {
        "diag": ({}, (field_tools, "collect", {})),
        "layout_last": ({}, (layout_diag, "last", 0)),
        "status": ({}, (names, "ctl_call", {"ok": True})),
        "reconnect": ({"session": None, "caller_session": "c", "as_json": True}, (reconnect, "run", 0)),
        "support": ({"kind": "question", "text": "q", "thread": None, "attach_doctor": True, "wait": 0, "lang": "en"},
                    (support, "run_send", 0)),
        "thread": ({"thread": "st_" + "a" * 22, "wait": 5}, (support, "run_thread", 0)),
        "list": ({}, (support, "run_list", 0)),
        "feedback_send": ({"raw": "{}", "owner_confirmed": False}, (feedback, "run_send", 0)),
        "feedback_replies": ({"as_json": True}, (feedback, "run_replies", 0)),
    }

    def test_cases_cover_every_allowed_op(self):
        self.assertEqual(set(self.CASES), set(hd.ALLOWED))

    def test_every_op_call_binds_to_the_real_signature(self):
        for op, (fields, (mod, name, ret)) in self.CASES.items():
            with self.subTest(op=op):
                real = getattr(mod, name)
                with patch.object(mod, name, autospec=True, return_value=ret) as target, \
                     patch.object(support, "channel_for", autospec=True, return_value=object()):
                    res = hd.execute(self.st, {"t": "host_diagnostics", "op": op, **fields})
                self.assertEqual(res["exit"], 0, res)
                self.assertNotIn("Background request", res["output"])
                self.assertEqual(target.call_count, 1)
                inspect.signature(real).bind(*target.call_args.args, **target.call_args.kwargs)

    def test_unexpected_error_names_its_class_in_log_and_reason_never_its_text(self):
        secret_text = "private-body-p569 ticket text"
        with patch.object(support, "channel_for", return_value=object()), \
             patch.object(support, "run_thread", side_effect=TypeError(secret_text)):
            res = hd.execute(self.st, {"op": "thread", "thread": "st_" + "a" * 22, "wait": 0})
        self.assertEqual(res["exit"], 1)
        self.assertIn("TypeError", res["output"])
        self.assertIn("not 'no reply yet'", res["output"])
        self.assertNotIn(secret_text, res["output"])
        log = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
        hit = [x for x in log if x.get("ev") == "host_diagnostics_failed"]
        self.assertEqual(hit[-1]["exception_type"], "TypeError")
        self.assertEqual(hit[-1]["action"], "thread")
        self.assertNotIn(secret_text, self.st.log_path.read_text())

    def test_unreachable_background_names_the_transport_code(self):
        with patch.dict(os.environ, {elevate.ENV: str(Path(self.tmp.name) / "missing.sock"), "AGENTJ_FENCED": "1"}), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(hd.request("thread", thread="st_" + "a" * 22, wait=0), 1)
        self.assertRegex(out.getvalue(), r"^\((host_unreachable|sandbox_blocked|serve_busy|[A-Za-z]+Error)\) ")

    def test_install_session_network_error_names_the_class_not_the_message(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("secret-path-p569")):
            with self.assertRaises(support.SupportError) as cm:
                support.bearer_call("GET", "http://127.0.0.1:9/v1/support/threads/x", "aji_x", None)
        self.assertIn("ConnectionRefusedError", str(cm.exception))
        self.assertNotIn("secret-path-p569", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
