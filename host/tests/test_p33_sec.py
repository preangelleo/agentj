"""PROMPT-33 security review fixes (reports/security/P33-triage.md) — one regression test per finding, each written to fail on
the code before the fix (positive control, run once at fix time) and pass after:

  C01 / X03  inbox cleanup never follows a link at `.agentj`, `.agentj/inbox` or a day folder
  C02        say-time transcription reads the host-private copy, never the Agent-writable inbox file
  C04 / X09  say-time ffmpeg: pinned demuxer, file-only protocols, duration / size caps, one at a time, killed on withdraw
  C03 / X06  the 17th waiting send is refused (no eviction), sids are remembered, claimed blobs count toward the caps
  X08        a write that failed part-way is "maybe delivered": a withdraw answers already_delivered
  X07        a device revoked while a `frag` message is being written gets no further fragment
  X04        Codex: the signed option index maps to exactly the array shown; duplicate ids / malformed options → no card
  X05        OpenCode: a question reply serve did not send (or a different answer) stops OpenCode
  X10        offline ASR import installs exactly the bytes it hashed
  C06        history file and memory stay bounded in bytes
  C07        `history off` says what stays on disk; `history clear --all` deletes current + archives
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import hashlib
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
from agentj import agent as agentmod, agent_codex, agent_opencode, asr, compose, history, inbox, serve, uploads, wire  # noqa: E402
from test_l1 import Phone, _state  # noqa: E402
from test_p33 import b64, bid, png, wav  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def _tree(root: pathlib.Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


class InboxLinks(unittest.TestCase):
    """P33-C01 / X03: retention / clear / listing / staged deletes through a planted link delete nothing outside."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.victim = self.d / "victim"
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 90 * 86400))
        for day in (old, "2025-01-01", time.strftime("%Y-%m-%d")):
            (self.victim / day).mkdir(parents=True)
            (self.victim / day / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 100)
        self.before = _tree(self.victim)

    def tearDown(self):
        self.tmp.cleanup()

    def planted(self, where: str) -> pathlib.Path:
        w = self.d / f"w-{where.replace('/', '_')}"
        w.mkdir()
        if where == ".agentj":
            holder = self.d / f"holder-{where.replace('/', '_')}"
            holder.mkdir()
            (holder / "inbox").symlink_to(self.victim)
            (w / ".agentj").symlink_to(holder)              # .agentj → a folder whose inbox → victim
        else:
            (w / ".agentj").mkdir()
            (w / ".agentj" / "inbox").symlink_to(self.victim)
        return w

    def test_retain_clear_days_never_follow_a_linked_parent(self):
        for where in (".agentj", ".agentj/inbox"):
            w = self.planted(where)
            logged = []
            gone = inbox.retain(str(w), log=lambda ev, **kw: logged.append((ev, kw)))
            self.assertEqual(gone, [], where)
            self.assertEqual(_tree(self.victim), self.before, f"retention deleted nothing through {where}")
            self.assertIn(("inbox_unsafe", {"op": "retain"}), logged, "refused + logged")
            q = inbox.QUOTA
            try:
                inbox.QUOTA = 1                            # the quota rule would take every day but today's
                self.assertEqual(inbox.retain(str(w)), [])
            finally:
                inbox.QUOTA = q
            self.assertEqual(_tree(self.victim), self.before)
            with self.assertRaises(inbox.Unsafe):
                inbox.clear(str(w))
            self.assertEqual(_tree(self.victim), self.before, f"`inbox clear` deleted nothing through {where}")
            self.assertEqual(inbox.days(str(w)), [], "nothing listed through a link")
            self.assertEqual(inbox.usage(str(w)), 0)
            p = os.path.join(str(w), ".agentj", "inbox", "2025-01-01", "photo.jpg")
            self.assertFalse(inbox.unlink_placed(p), "a staged-file delete never goes through the link")
            self.assertEqual(_tree(self.victim), self.before)

    def test_links_inside_a_real_inbox_are_removed_not_followed(self):
        w = self.d / "w"
        base = w / ".agentj" / "inbox"
        (base / "2020-01-01").mkdir(parents=True)
        (base / "2020-01-01" / "f").write_bytes(b"x")
        (base / "2020-01-01" / "link").symlink_to(self.victim / "2025-01-01")      # a link inside an old day
        (base / "2020-01-01" / "sub").mkdir()
        (base / "2020-01-01" / "sub" / "deep").symlink_to(self.victim)
        (base / "2020-01-02").symlink_to(self.victim)                               # a linked day folder
        self.assertEqual(inbox.retain(str(w)), ["2020-01-01"])
        self.assertFalse((base / "2020-01-01").exists())
        self.assertTrue((base / "2020-01-02").is_symlink(), "a linked day is skipped, not followed")
        self.assertEqual(_tree(self.victim), self.before)
        src = self.d / "src.png"
        src.write_bytes(png())
        p = inbox.place(str(w), str(src), "a", "image/png", bid())
        self.assertTrue(inbox.unlink_placed(p))
        self.assertFalse(os.path.exists(p))


class _Engine:
    """Stand-in ASR engine: the 'transcript' is the SHA-256 of the bytes it was given (what was actually read)."""

    def __init__(self):
        self.paths = []

    def ready_state(self, _root, engine_override=None):
        return "ready"

    def transcribe(self, path, timeout_s=60, state_dir=None, engine_override=None):
        self.paths.append(path)
        return {"ok": True, "text": hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()}


class SayVoice(unittest.TestCase):
    """P33-C02 / C04 / X09."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.work = self.d / "work"
        self.work.mkdir()
        self.host = serve.Host(self.st, events="quiet", read_stdin=False)
        self.host.uploads = self.up = uploads.Uploads(self.st, str(self.work))
        self.host.asr = self.eng = _Engine()
        self.dev = "D" * 16

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, data: bytes, mime="audio/wav") -> uploads.Blob:
        b = bid()
        m = {"bid": b, "purpose": "att", "name": "rec", "mime": mime, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "origin": "recording", "secs": 1}
        self.assertEqual(self.up.open(self.dev, m)[0]["t"], "blob_ack")
        for o in range(0, len(data), uploads.CHUNK):
            self.up.chunk(self.dev, {"bid": b, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
        out, _ = self.up.end(self.dev, b)
        self.assertTrue(out[-1]["ok"], out)
        return self.up.claim(self.dev, [b])[0]

    def file_of(self, blob) -> dict:      # exactly what serve._accept builds for a say
        return {"path": blob.path, "mime": blob.mime, "bytes": blob.size, "origin": blob.origin, "secs": blob.secs,
                "voice": getattr(blob, "voice", "")}

    def test_transcribes_the_private_copy_not_the_swapped_inbox_file(self):
        mine = wav(1.0)
        blob = self.stage(mine)
        self.assertTrue(blob.voice and pathlib.Path(blob.voice).is_relative_to(self.st.root), "a copy in the state dir")
        secret = self.d / "secret-memo.wav"                  # a host file outside the Agent's folder
        secret.write_bytes(wav(2.0))
        os.unlink(blob.path)                                 # the Agent swaps its inbox copy for a link …
        os.symlink(secret, blob.path)
        r = asyncio.run(self.host._transcribe_file(self.file_of(blob), 30, self.dev))
        self.assertEqual(r.get("text"), hashlib.sha256(mine).hexdigest(), "the bytes the phone sent, not the link target")
        self.assertNotIn(str(secret), self.eng.paths)
        pathlib.Path(blob.path).unlink()
        pathlib.Path(blob.path).write_bytes(b"#EXTM3U\n#EXTINF:1,\nfile:" + str(secret).encode() + b"\n")   # … or rewrites it
        r = asyncio.run(self.host._transcribe_file(self.file_of(blob), 30, self.dev))
        self.assertEqual(r.get("text"), hashlib.sha256(mine).hexdigest())
        voice = blob.voice
        self.up.commit([blob])
        self.assertFalse(os.path.exists(voice), "the private copy goes once the say is delivered")

    def test_withdraw_release_restart_keep_the_private_copy(self):
        blob = self.stage(wav(0.5))
        self.up.release([blob])
        up2 = uploads.Uploads(self.st, str(self.work))       # serve restarted while staged
        b2 = up2.blobs[(self.dev, blob.bid)]
        self.assertEqual(b2.voice, blob.voice)
        up2.device_gone(self.dev)
        self.assertFalse(os.path.exists(blob.voice), "a revoked device's private copy goes too")

    def fake_ffmpeg(self, sleep: float) -> pathlib.Path:
        bindir = self.d / "bin"
        bindir.mkdir(exist_ok=True)
        rec = self.d / "ff.jsonl"
        ff = bindir / "ffmpeg"
        ff.write_text(f"#!{sys.executable}\nimport json, os, sys, time\n"
                      f"open({str(rec)!r}, 'a').write(json.dumps({{'pid': os.getpid(), 'argv': sys.argv[1:], "
                      f"'t': time.time()}}) + '\\n')\ntime.sleep({sleep})\n")
        ff.chmod(0o755)
        return rec

    def records(self, rec):
        return [json.loads(x) for x in rec.read_text().splitlines()] if rec.exists() else []

    def test_ffmpeg_is_pinned_capped_serial_and_killed_on_withdraw(self):
        rec = self.fake_ffmpeg(30)
        blobs = [self.stage(b"\x1aE\xdf\xa3" + os.urandom(400), "audio/webm") for _ in range(2)]

        async def go():
            with mock.patch.dict(os.environ, {"PATH": str(self.d / "bin") + os.pathsep + os.environ["PATH"]}):
                t1 = asyncio.create_task(self.host._transcribe_file(self.file_of(blobs[0]), 40, self.dev))
                t2 = asyncio.create_task(self.host._transcribe_file(self.file_of(blobs[1]), 40, self.dev))
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if self.records(rec):
                        break
                await asyncio.sleep(0.3)
                self.assertEqual(len(self.records(rec)), 1, "one conversion at a time")
                t1.cancel()                                  # a withdraw cancels the say's prep task
                with self.assertRaises(asyncio.CancelledError):
                    await t1
                first = self.records(rec)[0]
                with self.assertRaises(ProcessLookupError, msg="the child is killed and reaped on withdraw"):
                    os.kill(first["pid"], 0)
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if len(self.records(rec)) == 2:
                        break
                t2.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await t2
                second = self.records(rec)[1]
                with self.assertRaises(ProcessLookupError):
                    os.kill(second["pid"], 0)
                return first
        first = asyncio.run(go())
        a = first["argv"]
        for flag, val in (("-protocol_whitelist", "file"), ("-f", "matroska"), ("-t", str(serve.FF_MAX_SECS)),
                          ("-fs", str(uploads.ASR_MAX))):
            self.assertIn(flag, a)
            self.assertEqual(a[a.index(flag) + 1], val, flag)
        src = a[a.index("-i") + 1]
        self.assertEqual(src, "file:" + blobs[0].voice, "the private copy, as a plain file URL")
        self.assertLess(a.index("-f"), a.index("-i"), "the demuxer is pinned for the INPUT (no probing)")

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_a_playlist_declared_as_webm_is_not_opened_as_a_playlist(self):
        secret = self.d / "secret.wav"
        secret.write_bytes(wav(1.0))
        playlist = (f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:1.0,\nfile:{secret}\n#EXT-X-ENDLIST\n").encode()
        blob = self.stage(playlist, "audio/webm")
        r = asyncio.run(self.host._transcribe_file(self.file_of(blob), 30, self.dev))
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("why"), "format")
        self.assertEqual(self.eng.paths, [], "the engine never got the secret's audio")


class SendQueue(unittest.TestCase):
    """P33-C03 / X06 / X08."""

    def test_the_17th_waiting_send_is_refused_none_evicted_sids_remembered(self):
        async def go():
            ss = compose.Sends()
            sends = [compose.Send("D", f"{i:022d}", i) for i in range(compose.MAX_SENDS)]
            for s in sends:
                self.assertTrue(ss.add(s))
            self.assertTrue(ss.full("D"))
            self.assertFalse(ss.add(compose.Send("D", "x" * 22, 99)), "the 17th is refused …")
            self.assertTrue(all(ss.get("D", s.sid) is s for s in sends), "… and none of the 16 was evicted")
            self.assertEqual((await ss.cancel("D", sends[0].sid))[0], "cancelled", "the oldest is still withdrawable")
            self.assertFalse(ss.full("D"))
            self.assertFalse(ss.full("E"), "per device")
            for s in sends[1:]:
                s.state = "delivered"
                s.created -= compose.SEND_TTL + 1          # finished long ago: the table forgets them …
            ss._sweep()
            self.assertIsNone(ss.get("D", sends[1].sid))
            self.assertTrue(ss.used("D", sends[1].sid), "… but a retry with the same sid is still `dup`")
        asyncio.run(go())

    def test_uncertain_delivery_is_never_cancelled(self):
        class H:
            def __init__(self):
                self.unc, self.dlv = [], []

            def say_uncertain(self, s):
                self.unc.append(s)

            def say_delivered(self, s):
                self.dlv.append(s)

        async def go():
            h = H()
            a = agentmod.Agent(h, {})
            ss = compose.Sends()
            s = compose.Send("D", "u" * 22, 1)
            ss.add(s)
            a.cur_send = s

            async def half_written():
                raise ConnectionResetError("drain failed after the line was written")
            with self.assertRaises(ConnectionResetError):
                await a.deliver(half_written)
            self.assertEqual(s.state, "uncertain")
            self.assertEqual(h.unc, [s], "attachments committed, not released")
            self.assertEqual((await ss.cancel("D", s.sid))[0], "already_delivered", "too late, never `cancelled`")
            s2 = compose.Send("D", "v" * 22, 2)
            ss.add(s2)
            a.cur_send = s2

            async def refused():
                return False                                   # the harness answered "no": provably not delivered
            await a.deliver(refused)
            self.assertEqual((await ss.cancel("D", s2.sid))[0], "cancelled")
            s3 = compose.Send("D", "w" * 22, 3)
            ss.add(s3)
            a.cur_send = s3

            async def maybe():
                return agentmod.UNCERTAIN                      # e.g. OpenCode answered 5xx after the request went out
            await a.deliver(maybe)
            self.assertEqual((await ss.cancel("D", s3.sid))[0], "already_delivered")
        asyncio.run(go())

    def test_claimed_blobs_count_toward_the_device_caps(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            work = pathlib.Path(d) / "w"
            work.mkdir()
            up = uploads.Uploads(st, str(work))
            dev = "D" * 16
            ids = []
            for _ in range(uploads.MAX_STAGED):
                data = png(200)
                b = bid()
                up.open(dev, {"bid": b, "purpose": "att", "name": "p", "mime": "image/png", "size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(), "origin": "file"})
                up.chunk(dev, {"bid": b, "o": 0, "d": b64(data)})
                up.end(dev, b)
                ids.append(b)
            up.claim(dev, ids[:10])                            # a queued say holds 10 of them
            data = png(200)
            r = up.open(dev, {"bid": bid(), "purpose": "att", "name": "p", "mime": "image/png", "size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(), "origin": "file"})
            self.assertEqual(r[0].get("why"), "too_many", "claimed blobs still count toward ≤ 20")
            dv = uploads.DEVICE_BYTES
            try:
                uploads.DEVICE_BYTES = sum(b.size for b in up.blobs.values() if b.state == "claimed") + 10
                for b in [b for b in up.blobs.values() if b.state == "staged"]:
                    up.drop(dev, b.bid)
                r = up.open(dev, {"bid": bid(), "purpose": "att", "name": "p", "mime": "image/png", "size": 100,
                                  "sha256": "0" * 64, "origin": "file"})
                self.assertEqual(r[0].get("why"), "too_many", "claimed bytes count toward the 200 MiB")
            finally:
                uploads.DEVICE_BYTES = dv


class FragRevoke(unittest.TestCase):
    """P33-X07."""

    def test_revoked_between_fragments_gets_nothing_more(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            ph = Phone(st)
            host = serve.Host(st, events="quiet", read_stdin=False)
            from agentj.noise import CipherState
            s33 = serve.Session(cid=5, state="ready", device=ph.did, pub=ph.pub, p33=True, send=CipherState(None))
            host.sessions = {5: s33}
            frames = []

            async def op(o, cid, payload=b""):
                frames.append((o, cid))
                if o == wire.OP_DATA and len(frames) == 1:
                    await host.revoke(ph.did)              # the human revokes while fragment 1 is on its way
            host._op = op
            big = {"t": "hist_page", "r": "r", "turns": [{"reply": {"text": "长" * 100_000}}]}
            self.assertGreater(len(wire.frag_split(big, "0" * 16)), 2)
            ok = asyncio.run(host.send_app(s33, big))
            self.assertFalse(ok)
            self.assertEqual([f for f in frames if f[0] == wire.OP_DATA], [(wire.OP_DATA, 5)], "only fragment 1")


class CodexQuestion(unittest.TestCase):
    """P33-X04."""

    def test_malformed_options_and_duplicate_ids_get_no_card(self):
        qs = [{"id": "q1", "question": "删哪个？", "options": [None, {"label": "Delete"}, {"label": "Keep"}]}]
        self.assertIsNone(agent_codex.question_card(qs), "a dropped entry would shift every option number")
        dup = [{"id": "q1", "question": "A?", "options": [{"label": "x"}]},
               {"id": "q1", "question": "B?", "options": [{"label": "y"}]}]
        self.assertIsNone(agent_codex.question_card(dup))

    def test_keep_means_keep(self):
        qs = [{"id": "q1", "question": "这个文件？", "options": [{"label": "Delete", "description": "删掉"},
                                                              {"label": "Keep", "description": "留着"}]},
              {"id": "q2", "question": "再确认？", "options": [{"label": "Yes"}, {"label": "No"}]}]
        card = agent_codex.question_card(qs)
        self.assertEqual([o["l"] for o in card[0]["o"]], ["Delete", "Keep"])
        sent = []

        class H:
            task_label = None

            async def question(self, c, gone, task=None):
                self.shown = c
                return "answer", [[2], [1]]                   # the human signed "2: Keep" and "1: Yes"

        async def go():
            a = agent_codex.CodexAgent(H(), {"kind": "codex", "dir": "/tmp"})
            a._answer = lambda rid, result=None, error=None: sent.append(result)
            await a._ask_user(7, qs, card)
        asyncio.run(go())
        self.assertEqual(sent, [{"answers": {"q1": {"answers": ["Keep"]}, "q2": {"answers": ["Yes"]}}}])


class OpenCodeQuestion(unittest.TestCase):
    """P33-X05."""

    def agent(self):
        a = agent_opencode.OpenCodeAgent(None, {"kind": "opencode", "dir": "/tmp", "model": None})
        a.tampered = []
        a._tamper = lambda why: a.tampered.append(why)
        return a

    def test_a_reply_serve_did_not_send_stops_opencode(self):
        a = self.agent()
        a.on_event({"type": "question.replied", "properties": {"sessionID": "s", "requestID": "que_1",
                                                               "answers": [["Delete"]]}})
        self.assertEqual(a.tampered, ["foreign_question_reply"])

    def test_a_different_answer_than_the_signed_one_stops_opencode(self):
        a = self.agent()
        a.q_sent["que_2"] = [["Keep"]]
        a.on_event({"type": "question.replied", "properties": {"sessionID": "s", "requestID": "que_2",
                                                               "answers": [["Delete"]]}})
        self.assertEqual(a.tampered, ["foreign_question_reply"])

    def test_our_own_reply_and_any_reject_are_fine(self):
        a = self.agent()
        a.q_sent["que_3"] = [["Keep"]]
        a.on_event({"type": "question.replied", "properties": {"sessionID": "s", "requestID": "que_3",
                                                               "answers": [["Keep"]]}})
        a.on_event({"type": "question.rejected", "properties": {"sessionID": "s", "requestID": "que_4"}})
        self.assertEqual(a.tampered, [])
        self.assertNotIn("que_3", a.q_sent)


class AsrImport(unittest.TestCase):
    """P33-X10: the bytes installed are the bytes hashed."""

    def test_a_swap_after_the_check_is_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            dl = pathlib.Path(d) / "dl"
            dl.mkdir()
            good, evil = os.urandom(4096), os.urandom(4096)
            src = dl / "model.bin"
            src.write_bytes(good)
            sha = hashlib.sha256(good).hexdigest()
            dest = pathlib.Path(d) / "inst" / "model.bin"
            dest.parent.mkdir()
            real = asr._sha256

            def racing(p):
                h = real(p)
                if pathlib.Path(p) == src:                    # the source is swapped right after it was hashed
                    src.write_bytes(evil)
                return h
            with mock.patch.dict(os.environ, {asr.DOWNLOADS_ENV: str(dl)}), mock.patch.object(asr, "_sha256", racing):
                ok = asr._from_downloads("model.bin", len(good), sha, dest)
            if ok:
                self.assertEqual(dest.read_bytes(), good, "installed bytes = hashed bytes")
            else:
                self.assertFalse(dest.exists())
            src.write_bytes(evil)                              # pinned size, wrong bytes from the start
            dest.unlink(missing_ok=True)
            with mock.patch.dict(os.environ, {asr.DOWNLOADS_ENV: str(dl)}):
                self.assertFalse(asr._from_downloads("model.bin", len(good), sha, dest))
            self.assertFalse(dest.exists())
            self.assertEqual([p.name for p in dest.parent.iterdir()], [], "no copy left behind")


class HistoryBytes(unittest.TestCase):
    """P33-C06 / C07."""

    def test_a_long_streamed_reply_keeps_the_file_and_memory_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            with mock.patch.object(history, "FILE_MAX", 400_000), mock.patch.object(history, "KEEP_BYTES", 200_000):
                h = history.History(st)
                for _ in range(5):
                    h.add({"k": "sys", "text": ""}, "x" * 30_000, "done")
                t = h.add({"k": "phone", "text": "长回复"})
                biggest = 0
                for _ in range(200):                          # 200 appends of 500 units to one page (< PART_MAX)
                    h.update(t["id"], append="字" * 500)
                    biggest = max(biggest, h.cur_path.stat().st_size)
                line = len(json.dumps(h.get(t["id"]), ensure_ascii=False).encode()) + 1
                self.assertLessEqual(biggest, 400_000 + line, "compacted on bytes, not only on 600 lines")
                self.assertLessEqual(h.mem, max(200_000, line) + line)
                self.assertEqual(h.mem, sum(len(json.dumps(x, ensure_ascii=False).encode()) + 1 for x in h.turns.values()))
                self.assertIsNotNone(h.get(t["id"]), "the page being written stays")
                h2 = history.History(st)                      # what is on disk reads back the same
                self.assertEqual(h2.get(t["id"])["reply"]["text"], h.get(t["id"])["reply"]["text"])

    def test_purge_deletes_current_and_archives(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            h = history.History(st)
            for i in range(3):
                h.add({"k": "phone", "text": f"秘密 {i}"}, "回答", "done")
                h.reset("clear")
            h.add({"k": "phone", "text": "当前"}, "", "done")
            self.assertEqual(h.on_disk()[0], 4)
            ep = h.epoch
            self.assertEqual(h.purge(), 4)
            self.assertEqual((h.on_disk(), h.meta()["count"], h.archives()), ((0, 0), 0, []))
            self.assertGreater(h.epoch, ep)
            self.assertFalse(h.undo_reset(), "not undoable")
            for p in (st.root / "history").rglob("*.jsonl"):
                self.fail(f"left behind: {p}")

    def test_cli_off_says_what_stays_and_clear_all_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            sd = pathlib.Path(d) / "s"
            env = {**os.environ, "AGENTJ_STATE_DIR": str(sd), "HOME": d, "PYTHONPATH": str(HERE.parent)}

            def cli(*a):
                return subprocess.run([sys.executable, "-m", "agentj.cli", *a], env=env, capture_output=True, text=True,
                                      timeout=60, cwd=str(HERE.parent))
            self.assertEqual(cli("init", "--relay", "ws://127.0.0.1:1").returncode, 0)
            st = __import__("agentj.state", fromlist=["State"]).State(sd)
            h = history.History(st)
            h.add({"k": "phone", "text": "秘密"}, "回答", "done")
            h.reset("clear")
            h.add({"k": "phone", "text": "当前"}, "", "done")
            r = cli("config", "history", "off")
            self.assertIn("已有的记录没有删", r.stdout)
            self.assertIn("agentj history clear --all", r.stdout)
            r = cli("history", "clear", "--all")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("已删除", r.stdout)
            self.assertEqual(list((sd / "history").rglob("*.jsonl")), [])
            self.assertNotIn("已有的记录没有删", cli("config", "history", "status").stdout)
            self.assertNotIn("秘密", (sd / "host.log").read_text())


class Wording(unittest.TestCase):
    """The host's texts name the new phone page's ≡「全部命令」 button, not the old 「命令」 one."""

    def test_no_old_commands_button(self):
        from agentj import slash
        self.assertIn("≡「全部命令」", slash.HELP)
        src = (HERE.parent / "agentj" / "serve.py").read_text()
        self.assertNotIn("点输入框旁的「命令」", src)
        self.assertIn("≡「全部命令」", src)


if __name__ == "__main__":
    unittest.main()
