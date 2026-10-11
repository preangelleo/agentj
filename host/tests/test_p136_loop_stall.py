"""P136: the event loop answers phones while Telegram is slow, and a `loop_stall` line names what was running.

(a) A Bot API that takes 20 s to answer getUpdates: the real Telegram poll loop keeps the event loop free — a frame
    arriving from another thread (what a phone's ping / resume is to serve) is handled in < 1 s, the whole time.
(b) Every Bot API call is bounded at ≤ 15 s.
(c) A handler that blocks the loop: the `loop_stall` line carries the call site (basename:line:function), the running
    task and the thread's kernel state — never a path."""
import _hermetic  # noqa: F401,I001
import asyncio
import inspect
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import preferences as prefs, serve, telegram as tg  # noqa: E402
from agentj.state import State  # noqa: E402

KEY_ENV = "AGENTJ_TEST_P136_BOT"
OWNER = 4242
SLOW = 20.0


class SlowBot:
    """getUpdates holds the request for SLOW seconds (or until released)."""

    def __init__(self):
        self.release, self.polling = threading.Event(), threading.Event()
        bot = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                method = self.path.rsplit("/", 1)[-1]
                if method == "getUpdates":
                    bot.polling.set()
                    bot.release.wait(SLOW)
                    res = {"ok": True, "result": []}
                elif method == "getMe":
                    res = {"ok": True, "result": {"id": 99, "username": "test_bot"}}
                else:
                    res = {"ok": True, "result": True}
                data = json.dumps(res).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.srv.shutdown()
        self.srv.server_close()


class _Host:
    def __init__(self, root):
        self.st = State(root)
        self.st.init()
        self.preferences = prefs.defaults()
        self.preferences["channels"]["items"] = [{"id": "tg", "type": "telegram"}]
        self.agent, self.media, self.lang = object(), None, "zh"
        self.stopping = asyncio.Event()
        self.hist = SimpleNamespace(get=lambda tid: None)

    def stopped(self):
        return False


class SlowTelegram(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p136-")
        self.addCleanup(self.tmp.cleanup)
        self.bot = SlowBot()
        self.addCleanup(self.bot.close)
        cfg = {"owner_id": OWNER, "key_env": KEY_ENV, "generation": "g1", "at": 1_000}
        for p in (patch.object(tg, "BASE", self.bot.url), patch.dict(os.environ, {KEY_ENV: "test-placeholder"}),
                  patch("agentj.telegram.configuration", side_effect=lambda st=None: cfg)):
            p.start()
            self.addCleanup(p.stop)
        self.host = _Host(pathlib.Path(self.tmp.name) / "st")

    async def test_loop_answers_while_getupdates_hangs(self):
        t = tg.Telegram(self.host)
        task = asyncio.create_task(t.run())
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.bot.polling.wait, 10)
        self.assertTrue(self.bot.polling.is_set(), "the poller never reached getUpdates")
        lags = []
        try:
            end = time.monotonic() + 3.0
            while time.monotonic() < end:           # a phone's ping / resume frame arriving from the network side
                got = asyncio.Event()
                sent = time.monotonic()
                threading.Thread(target=lambda: loop.call_soon_threadsafe(got.set), daemon=True).start()
                await asyncio.wait_for(got.wait(), 5)
                lags.append(time.monotonic() - sent)
                await asyncio.sleep(0.1)
            self.assertFalse(self.bot.release.is_set())
        finally:
            self.host.stopping.set()
            self.bot.release.set()
            await asyncio.wait_for(task, 20)
        self.assertGreater(len(lags), 10)
        self.assertLess(max(lags), 1.0)

    def test_every_bot_api_call_is_bounded(self):
        self.assertLessEqual(inspect.signature(tg.api).parameters["timeout"].default, 15)


class StallSite(unittest.TestCase):
    def test_loop_stall_names_the_blocking_call(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "st")
            st.init()
            host = serve.Host(st, events="jsonl", read_stdin=False)

            def blocking_handler():
                time.sleep(2.0)

            async def handle_frame():
                blocking_handler()

            async def main():
                t = asyncio.create_task(host.loop_watch())
                await asyncio.sleep(0.1)
                await asyncio.create_task(handle_frame(), name="frame")
                await asyncio.sleep(1.3)
                host.stopping.set()
                t.cancel()
            with patch.object(serve, "LOOP_STALL", 0.5):
                asyncio.run(main())
            ev = [json.loads(x) for x in st.log_path.read_text().splitlines()]
            ev = [e for e in ev if e["ev"] == "loop_stall"]
            self.assertTrue(ev and ev[0]["ms"] >= 500)
            self.assertIn("blocking_handler", ev[0]["where"])
            self.assertIn("test_p136_loop_stall.py:", ev[0]["where"])
            self.assertNotIn("/", ev[0]["where"])
            self.assertIn("handle_frame", ev[0]["task"])
            if sys.platform.startswith("linux"):
                self.assertIn(ev[0]["pstate"], {"S", "R", "D"})
                self.assertIsInstance(ev[0]["majflt"], int)


if __name__ == "__main__":
    unittest.main()
