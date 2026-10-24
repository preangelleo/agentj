"""P406 #173: a sudo card closes once the password was accepted and the command runs; a status strip gets the masked output,
a signed Stop, and the result (PROTOCOL §11.2)."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import elevate, elevate_live
from test_controls import sign
from test_f17 import PW, answer, fake_sudo, enroll
from test_l1 import Phone, _owner_host as _host, _ready, _state

TOKEN = "sk-proj-" + "Ab3" * 16


def frames(sent, t, rid=None):
    return [o for _, o in sent if o.get("t") == t and (rid is None or o.get("id") == rid)]


async def until(pred, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(.02)
    return False


def everything_written(root: pathlib.Path) -> bytes:
    out = b""
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and p.name not in ("fakesudo",):
            try:
                out += p.read_bytes()
            except OSError:
                pass
    return out


class Running(unittest.TestCase):
    def run_case(self, script: str, steps):
        async def go(root):
            st, sent = _state(str(root)), []
            host = _host(st, sent)
            ph = enroll(st, Phone(st))
            session = _ready(host, ph)
            session.p33 = True
            self.other = Phone(st, name="平板")                  # paired with an approval key, not watching
            self.other_s = _ready(host, self.other, cid=12)
            self.nokey = Phone(st, name="旧手机", with_key=False)  # paired without an approval key
            self.nokey_s = _ready(host, self.nokey, cid=13)
            host.elevate.sudo = str(fake_sudo(root, PW))
            req = elevate.norm_sudo({"argv": [sys.executable, "-c", script], "why": "Restore the nightly backup archive",
                                     "timeout": 120, "cwd": str(root)})
            task = asyncio.create_task(host.elevate.request(req))
            self.assertTrue(await until(lambda: frames(sent, "elev")))
            card = frames(sent, "elev")[0]
            try:
                await steps(host, session, ph, card, sent, task, root)
            finally:
                for c in list(host.elevate.cards.values()):
                    if c.get("cancel") is not None:
                        c["cancel"].set()
                res = await task
            self.assertNotIn(PW, json.dumps(sent, ensure_ascii=False))
            self.assertNotIn(PW.encode(), everything_written(root))
            self.assertNotIn(PW.encode("unicode_escape"), everything_written(root))
            return res
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP") or None) as folder:
            return asyncio.run(go(pathlib.Path(folder)))

    def test_card_closes_once_running_output_only_to_a_signed_watcher(self):
        script = ("import sys,time; print('copying 1/3', flush=True); print('export OPENAI_API_KEY=" + TOKEN + "', flush=True);"
                  "print('psk=Wifi12chars', flush=True); print('Password: hunter2', flush=True);"
                  "sys.stderr.write('warn: slow disk\\n'); sys.stderr.flush(); time.sleep(2.5); print('all done', flush=True)")

        async def steps(host, session, ph, card, sent, task, root):
            t0 = time.monotonic()
            await host._app(session, answer(ph, card, True, PW))
            self.assertTrue(await until(lambda: any(m.get("phase") == "started" for m in frames(sent, "elev_progress", card["id"])), 2.0),
                            "started reaches the phone within 2 s of the approval")
            self.assertLess(time.monotonic() - t0, 2.0)
            self.assertFalse(task.done(), "the command still runs")
            snap = host.elevate.open_msgs(session)
            self.assertEqual((snap[0]["state"], snap[0]["phase"]), ("running", "started"))
            await asyncio.sleep(1.0)
            self.assertFalse(frames(sent, "elev_out"), "no output on any phone without a signed watch")
            # unsigned / wrong-object / no-approval-key watches change nothing
            await host._app(session, {"t": "elev_watch", "id": card["id"], "on": True, "n": "b" * 32,
                                      "ts": int(time.time() * 1000), "sig": "AAAA"})
            await host._app(session, sign(ph, "elev_watch", {"id": card["id"], "on": False},
                                          {"t": "elev_watch", "id": card["id"], "on": True}))
            await host._app(self.nokey_s, sign(self.nokey, "elev_watch", {"id": card["id"], "on": True},
                                               {"t": "elev_watch", "id": card["id"], "on": True}, key=ph.sk))
            await asyncio.sleep(.6)
            self.assertFalse(frames(sent, "elev_out"))
            on = sign(ph, "elev_watch", {"id": card["id"], "on": True}, {"t": "elev_watch", "id": card["id"], "on": True})
            await host._app(session, on)
            self.assertTrue(await until(lambda: "copying 1/3" in "".join(o["text"] for c, o in sent if o.get("t") == "elev_out"), 3))
            first = next(o for c, o in sent if o.get("t") == "elev_out")
            self.assertTrue(first.get("full"), "a new watcher gets the masked tail so far first")
            self.assertEqual({c for c, o in sent if o.get("t") == "elev_out"}, {session.cid}, "output only to the watcher")
            off = sign(ph, "elev_watch", {"id": card["id"], "on": False}, {"t": "elev_watch", "id": card["id"], "on": False})
            await host._app(session, off)
            await host._app(session, on)            # replayed: refused (nonce taken), so the watch stays off
            self.assertEqual(host.elevate.cards[card["id"]]["watch"], {})
            await host._app(session, sign(ph, "elev_watch", {"id": card["id"], "on": True}, {"t": "elev_watch", "id": card["id"], "on": True}))
            res = await task
            self.assertEqual((res["result"], res["code"]), ("done", 0))
            self.assertIn(TOKEN, res["stdout"], "the Agent's own output is unchanged (CLI path)")
            dones = {c: o for c, o in sent if o.get("t") == "elev_done" and o["id"] == card["id"]}
            self.assertEqual((dones[session.cid]["result"], dones[session.cid]["code"]), ("done", 0))
            self.assertIn("all done", dones[session.cid]["tail"])
            for cid in (self.other_s.cid, self.nokey_s.cid):
                self.assertEqual(set(dones[cid]), {"t", "id", "result", "code"}, "status + exit code only for non-watchers")
            phone_text = json.dumps([o for c, o in sent if o.get("t") in ("elev_out", "elev_done")])
            self.assertIn("warn: slow disk", phone_text)
            for leak in (TOKEN, "Wifi12chars", "hunter2", "[agentj-"):
                self.assertNotIn(leak, phone_text)
            self.assertIn("<redacted>", phone_text)
            self.assertNotIn("tail", host.elevate.completed[card["id"]], "the replayed receipt stays output-free")
        self.run_case(script, steps)

    def test_wrong_password_keeps_the_card(self):
        async def steps(host, session, ph, card, sent, task, root):
            await host._app(session, answer(ph, card, True, "not-the-password"))
            mine = lambda: [o for c, o in sent if c == session.cid and o.get("t") == "elev" and o["id"] == card["id"]]
            self.assertTrue(await until(lambda: len(mine()) >= 2, 5), "the card is shown again")
            again = mine()[-1]
            self.assertEqual(again["bad"], 1)
            self.assertNotEqual(again["n"], card["n"])
            self.assertFalse(any(m.get("phase") == "started" for m in frames(sent, "elev_progress")), "never started")
            self.assertFalse(frames(sent, "elev_out"))
            self.assertFalse(task.done())
            self.assertNotIn("state", host.elevate.open_msgs(session)[0])
            await host._app(session, answer(ph, again, False))
            self.assertEqual((await task)["result"], "denied")
        self.run_case("print('never')", steps)

    def test_signed_stop_ends_the_command(self):
        async def steps(host, session, ph, card, sent, task, root):
            await host._app(session, answer(ph, card, True, PW))
            self.assertTrue(await until(lambda: any(m.get("phase") == "started" for m in frames(sent, "elev_progress")), 2.0))
            # an unsigned / wrongly signed stop changes nothing
            await host._app(session, {"t": "elev_stop", "id": card["id"], "n": "a" * 32, "ts": int(time.time() * 1000),
                                      "sig": "AAAA"})
            bad = sign(ph, "elev_stop", {"id": "f" * 32}, {"t": "elev_stop", "id": card["id"]})
            await host._app(session, bad)
            await asyncio.sleep(.5)
            self.assertFalse(task.done(), "a stop without a valid signature over this card does nothing")
            await host._app(session, sign(ph, "elev_watch", {"id": card["id"], "on": True},
                                          {"t": "elev_watch", "id": card["id"], "on": True}))
            self.assertTrue(await until(lambda: frames(sent, "elev_out"), 3))
            t0 = time.monotonic()
            stop = sign(ph, "elev_stop", {"id": card["id"]}, {"t": "elev_stop", "id": card["id"]})
            await host._app(session, stop)
            res = await asyncio.wait_for(asyncio.shield(task), 5)
            self.assertLess(time.monotonic() - t0, 5)
            self.assertEqual(res["result"], "stopped")
            done = next(o for c, o in sent if c == session.cid and o.get("t") == "elev_done")
            self.assertEqual((done["result"], done.get("stop")), ("stopped", True))
            self.assertIn("before sleep", done["tail"])
            st = host.st
            recs = [r for r in elevate.read_log(st) if r.get("id") == card["id"]]
            stops = [r for r in recs if r.get("result") == "stop"]
            self.assertEqual(len(stops), 1)
            self.assertEqual(elevate.check_record(st, stops[0]), "ok", "the stop is a re-checkable signed record")
            self.assertEqual(recs[-1]["result"], "stopped")
            await host._app(session, stop)       # replayed: the card is gone, nothing happens
        self.run_case("import time; print('before sleep', flush=True); time.sleep(60)", steps)


class Units(unittest.TestCase):
    def test_second_prompt_is_bad_never_started(self):
        live = elevate_live.Live()
        live.gate = b"[agentj-0011223344556677]"
        live.data(1, live.gate[:9])
        live.data(1, live.gate[9:] + b"Sorry, try again.\n" + live.gate)
        self.assertEqual(live.prompts, 2)
        self.assertTrue(live.bad)
        live.data(0, b"x\n")
        self.assertFalse(live.started)
        self.assertEqual(live.take(), ("", ""))

    def started(self, gate=b"[agentj-0011223344556677]"):
        live = elevate_live.Live(started=True)
        live.gate = gate
        return live

    def test_split_marker_never_reaches_the_phone(self):
        live = self.started()
        g = live.gate
        live.data(1, b"ok " + g[:5])
        live.data(1, g[5:17])
        live.data(1, g[17:] + b" next\n")
        live.close()
        text = live.take()[1]
        self.assertEqual(live.prompts, 1)
        self.assertNotIn("agentj", text)
        self.assertNotIn("[", text)
        self.assertIn("ok ", text)
        self.assertIn("next", text)

    def test_credential_lines_and_split_keys_are_masked(self):
        live = self.started()
        for chunk in (b"[connection]\npsk=Wifi12chars\n", b"PassPhrase : open sesame now\n", b"Pass" + b"word: hunt",   # split literal: same bytes, no scanner-shaped source line
                      b"er2\n", b"api_token=abc", b"def123\n", b"AUTH=xy\n", b"plain line 1/3\n"):
            live.data(0, chunk)
        live.close()
        text = live.take()[1]
        for leak in ("Wifi12chars", "open sesame", "hunter2", "hunt", "abcdef123", "abc", "xy\n"):
            self.assertNotIn(leak, text)
        self.assertIn("psk=<redacted>", text)
        self.assertIn("plain line 1/3", text)

    def test_idle_never_releases_a_key_prefix(self):
        live = self.started()
        live.data(0, b"token ghp_abc")       # a held run: the start of a key, no delimiter yet
        for _ in range(30):
            live.tick(os.getpid())
        live.next_look = 0
        time.sleep(.6)
        live.tick(os.getpid())
        self.assertNotIn("ghp", live.take()[1], "no idle flush on this stream")

    def test_child_seen_twice_starts_and_drops_pre_start_stderr(self):
        live = elevate_live.Live()
        live.gate = b"[agentj-8899aabbccddeeff]"
        live.data(1, live.gate + b"lecture line\n")
        p = subprocess.Popen([sys.executable, "-c", "import subprocess,sys; subprocess.run([sys.executable,'-c','import time; time.sleep(3)'])"],
                             start_new_session=True)
        try:
            self.assertTrue(elevate_live.has_child(os.getpid()))
            end = time.monotonic() + 3
            while not live.started and time.monotonic() < end:
                live.tick(p.pid)
                time.sleep(.05)
            self.assertTrue(live.started)
            self.assertEqual(live.take(), ("", ""), "stderr from before the start is dropped")
        finally:
            os.killpg(p.pid, 9)
            p.wait()

    def test_fit_caps_the_frame(self):
        text = "密" * 20000
        self.assertLessEqual(len(json.dumps(elevate_live.fit(text))), elevate_live.SEND_MAX)
        self.assertTrue(elevate_live.fit(text).endswith("密"))

    def test_stop_is_a_control_action(self):
        from agentj import controls
        self.assertIn("elev_stop", controls.ACTIONS)
        self.assertIn("elev_stop", elevate.PHONE_TYPES)
        self.assertIn("elev_watch", controls.ACTIONS)
        self.assertIn("elev_watch", elevate.PHONE_TYPES)
        self.assertEqual(controls.object_text("elev_stop", {"id": "a" * 32}), "a" * 32)
        self.assertEqual(controls.object_text("elev_watch", {"id": "a" * 32, "on": True}), "a" * 32 + "\non")
        self.assertEqual(controls.object_text("elev_watch", {"id": "a" * 32, "on": False}), "a" * 32 + "\noff")
        wire_js = (pathlib.Path(__file__).resolve().parents[2] / "protocol/wire.js").read_text()
        self.assertIn("'elev_stop'", wire_js)
        self.assertIn("if (action === 'elev_stop') return String(o.id);", wire_js)
        self.assertIn("'elev_watch'", wire_js)


if __name__ == "__main__":
    unittest.main()
