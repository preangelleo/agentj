"""P423 #49: the secret card was filled in on the phone, but the Agent never read the outcome back and asked the owner again.

`agentj secret request` waited the card's whole 10 minutes, past a harness's usual shell timeout: the CLI was killed, the
Agent saw nothing and sent another card. Now: the request waits at most `wait` s and answers `pending` (with the next step
in its own output); a card that ends after its CLI stopped listening is handed to the Agent as an 〔Agent J 系统提示〕 turn
(not when a `secret result --wait` is reading it); `agentj secret status <NAME>` says saved / pending / missing; a request
whose destination already holds the key answers `already_saved` without a card (`--replace` asks again). Never a value,
its length or fingerprint in any of these.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agentj import elevate  # noqa: E402

from test_f17 import answer, enroll  # noqa: E402
from test_l1 import Phone, _owner_host as _host, _ready, _state  # noqa: E402

KEY = "p423-test-key-DO-NOT-LEAK-0123456789abcdef"
MAIN = "import sys; from agentj.cli import main; sys.exit(main())"
HOST_DIR = str(pathlib.Path(__file__).resolve().parents[1])


def cli_env(st) -> dict:
    return dict(os.environ, PYTHONPATH=HOST_DIR, AGENTJ_STATE_DIR=str(st.root))


class Units(unittest.TestCase):
    def test_dest_has_value(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            env = d / ".env"
            spec = {"mode": "env", "path": str(env), "key": "K"}
            self.assertFalse(elevate.dest_has_value(spec))                    # no file
            env.write_text("OTHER=1\nK=\n")
            self.assertFalse(elevate.dest_has_value(spec))                    # empty
            env.write_text('K=""\n')
            self.assertFalse(elevate.dest_has_value(spec))
            env.write_text("OTHER=1\nexport K=abc\n")
            self.assertTrue(elevate.dest_has_value(spec))
            env.write_text("K=abc\nK=\n")                                     # the last line wins
            self.assertFalse(elevate.dest_has_value(spec))
            f = d / "k.txt"
            fs = {"mode": "file", "path": str(f)}
            self.assertFalse(elevate.dest_has_value(fs))
            f.write_text("")
            self.assertFalse(elevate.dest_has_value(fs))
            f.write_text("x")
            self.assertTrue(elevate.dest_has_value(fs))
            (d / "ln").symlink_to(f)
            self.assertFalse(elevate.dest_has_value({"mode": "file", "path": str(d / "ln")}))
            self.assertFalse(elevate.dest_has_value({}))

    def test_notice_and_words_carry_no_value(self):
        res = {"result": "saved", "id": "a" * 32,
               "receipt": {"name": "K", "dest": "~/.env", "length": 41, "fingerprint": "sha256:deadbeef", "verify": "ok"}}
        zh, en = elevate.secret_notice(res, "zh"), elevate.secret_notice(res, "en")
        self.assertIn("〔Agent J 系统提示〕", zh)
        self.assertIn("不要再让主人填", zh)
        self.assertIn("do not ask the owner for it again", en)
        for t in (zh, en):
            self.assertNotIn("41", t)
            self.assertNotIn("deadbeef", t)
        self.assertIn("不提供", elevate.secret_notice({"result": "denied", "name": "K", "id": "b" * 32}))
        said = elevate._say({"result": "pending", "secs": 500, "id": "c" * 32, "name": "K"}, "secret")
        self.assertIn("agentj secret status K", said)
        self.assertIn("不要再发卡", said)
        self.assertIn("--replace", elevate._say({"result": "already_saved", "name": "K", "dest": "~/.env"}, "secret"))


class Flows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.host.agent = Mock(spec=["submit"])
        self.host.agent.native_source = {"kind":"terminal","role":"owner"}   # a plain Mock would read as a group member's turn
        self.ph = enroll(self.st, Phone(self.st))
        self.s = _ready(self.host, self.ph)
        self.env = self.d / ".env"
        self.env.write_text("OTHER=1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def of(self, t):
        return [o for _, o in self.sent if o["t"] == t]

    async def wait_for(self, t, n=1, secs=10):
        for _ in range(int(secs * 100)):
            if len(self.of(t)) >= n:
                return self.of(t)[n - 1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"no {t}")

    def req(self, **kw):
        return {"t": "secret", "name": "K", "purpose": "配音", "dest": f"env:{self.env}#K", **kw}

    def no_leak(self, *texts):
        files = [self.st.log_path, elevate.log_path(self.st), elevate.results_path(self.st)]
        blob = "".join(f.read_text() for f in files if f.exists()) + "".join(texts)
        self.assertNotIn(KEY, blob)

    def test_bounded_wait_then_saved_is_handed_to_the_agent_and_status_says_saved(self):
        async def go():
            await self.host.elevate.start()
            try:
                status = await self.host.elevate.status_of({"name": "K"})
                self.assertEqual(status["status"], "missing")
                res = await asyncio.to_thread(elevate.client_request, self.st, self.req(wait=0.3), 30)
                self.assertEqual(res["result"], "pending")
                card = self.of("elev")[0]
                self.assertEqual(res["id"], card["id"])
                self.assertGreater(res["secs"], 500)
                self.assertIn(card["id"], self.host.elevate.cards, "the card stays on the phone")
                status = await self.host.elevate.status_of({"name": "K"})
                self.assertEqual((status["status"], status["id"]), ("pending", card["id"]))
                self.host.agent.submit.assert_not_called()
                await self.host._app(self.s, answer(self.ph, card, True, KEY))
                await self.wait_for("elev_done")
                for _ in range(200):
                    if self.host.agent.submit.called:
                        break
                    await asyncio.sleep(0.01)
                self.host.agent.submit.assert_called_once()
                snd = self.host.agent.submit.call_args[0][0]
                self.assertEqual((snd.device, snd.sid), ("agentj-secret", card["id"]))
                self.assertIn("K 已保存到", snd.text)
                self.assertIn("不要再让主人填", snd.text)
                self.assertEqual(snd.source, __import__("agentj.provenance", fromlist=["system"]).system())
                status = await self.host.elevate.status_of({"name": "K"})
                self.assertEqual((status["status"], status["id"]), ("saved", card["id"]))
                self.assertNotIn("length", status)
                self.assertNotIn("fingerprint", status)
                lst = await self.host.elevate.result_of({})
                self.assertNotIn("where", lst["items"][0])
                # the key is there now → asking again sends no card
                again = await asyncio.to_thread(elevate.client_request, self.st, self.req(wait=0.3), 30)
                self.assertEqual(again["result"], "already_saved")
                self.assertEqual(len(self.of("elev")), 1, "no second card")
                return snd.text, status
            finally:
                await self.host.elevate.stop()
        text, status = asyncio.run(go())
        self.assertEqual(self.env.read_text(), f"OTHER=1\nK={KEY}\n")
        self.no_leak(text, repr(status))

    def test_reader_with_wait_gets_it_no_handover_and_replace_asks_again(self):
        self.env.write_text("K=old\n")

        async def go():
            await self.host.elevate.start()
            try:
                self.assertEqual((await asyncio.to_thread(elevate.client_request, self.st, self.req(wait=0.3), 30))["result"],
                                 "already_saved")
                self.assertEqual(self.of("elev"), [])
                # a key whose last host-side check failed is asked for again without --replace
                elevate.remember(self.st, "e" * 32, {"kind": "secret", "name": "K", "result": "saved", "until": None,
                                                     "receipt": {"name": "K", "dest": "x", "verify": "fail"}})
                res = await asyncio.to_thread(elevate.client_request, self.st, self.req(wait=0.3), 30)
                self.assertEqual(res["result"], "pending")
                first = self.of("elev")[0]
                await self.host._app(self.s, answer(self.ph, first, False))
                await self.wait_for("elev_done")
                await asyncio.sleep(0.1)
                self.host.agent.submit.assert_called_once()              # nobody was waiting: 「不提供」 handed over
                self.assertIn("不提供", self.host.agent.submit.call_args[0][0].text)
                self.host.agent.submit.reset_mock()
                self.sent.clear()
                elevate.remember(self.st, "e" * 32, {"receipt": {"name": "K", "dest": "x", "verify": "ok"}})
                res = await asyncio.to_thread(elevate.client_request, self.st, self.req(wait=0.3, replace=True), 30)
                self.assertEqual(res["result"], "pending")
                card = self.of("elev")[0]
                reader = asyncio.create_task(self.host.elevate.result_of({"id": card["id"], "wait": 30}))
                await asyncio.sleep(0.2)
                await self.host._app(self.s, answer(self.ph, card, True, KEY))
                got = await asyncio.wait_for(reader, 10)
                self.assertEqual(got["result"], "saved")
                await asyncio.sleep(0.2)
                self.host.agent.submit.assert_not_called()               # the reader got it: no host turn too
            finally:
                await self.host.elevate.stop()
        asyncio.run(go())
        self.assertEqual(self.env.read_text(), f"K={KEY}\n")

    def test_status_with_dest_and_gone_from_dest(self):
        async def go():
            await self.host.elevate.start()
            try:
                st = await self.host.elevate.status_of({"name": "K", "dest": f"env:{self.env}#K"})
                self.assertEqual(st["status"], "missing")
                self.env.write_text("K=set-by-hand\n")
                st = await self.host.elevate.status_of({"name": "K", "dest": f"env:{self.env}#K"})
                self.assertEqual(st["status"], "saved")
                self.assertIn(".env", st["dest"])
                elevate.remember(self.st, "d" * 32, {"kind": "secret", "name": "K", "result": "saved", "until": None,
                                                     "where": {"mode": "env", "path": str(self.env), "key": "K"}})
                self.env.write_text("OTHER=1\n")
                st = await self.host.elevate.status_of({"name": "K"})
                self.assertEqual((st["status"], st.get("gone_from_dest")), ("missing", True))
                self.assertEqual((await self.host.elevate.status_of({"name": "../x"}))["result"], "refused")
            finally:
                await self.host.elevate.stop()
        asyncio.run(go())

    def test_killed_cli_then_saved_is_handed_over_and_cli_status_reads_it(self):
        work = self.d / "proj"
        work.mkdir()

        async def go():
            await self.host.elevate.start()
            try:
                p = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", MAIN, "secret", "request", "--name", "K", "--purpose", "配音",
                    "--dest", f"env:{self.env}#K", env=cli_env(self.st), cwd=str(work),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                line = (await asyncio.wait_for(p.stderr.readline(), 30)).decode()
                card = await self.wait_for("elev")
                p.send_signal(signal.SIGTERM)
                await asyncio.wait_for(p.wait(), 10)
                await asyncio.sleep(0.3)
                self.assertIn(card["id"], self.host.elevate.cards)
                await self.host._app(self.s, answer(self.ph, card, True, KEY))
                for _ in range(500):
                    if self.host.agent.submit.called:
                        break
                    await asyncio.sleep(0.01)
                self.host.agent.submit.assert_called_once()
                out = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", MAIN, "secret", "status", "K"],
                                              env=cli_env(self.st), capture_output=True, text=True, timeout=30)
                miss = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", MAIN, "secret", "status", "NOPE"],
                                               env=cli_env(self.st), capture_output=True, text=True, timeout=30)
                return line, out, miss
            finally:
                await self.host.elevate.stop()
        line, out, miss = asyncio.run(go())
        self.assertIn("〔Agent J 系统提示〕", line)
        self.assertIn("不要再发卡", line)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("SECRET_STATUS: saved — K", out.stdout)
        self.assertEqual(miss.returncode, elevate.EXIT_MISSING, miss.stderr)
        self.assertIn("SECRET_STATUS: missing — NOPE", miss.stdout)
        self.no_leak(line, out.stdout, out.stderr)


if __name__ == "__main__":
    unittest.main()
