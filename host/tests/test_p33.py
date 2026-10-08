"""Relay parity (PROTOCOL §10, PROMPT-33) — host units: limits and text rules, `frag` (both directions with the JS side), the
question signature (vectors shared with protocol/test/p33.test.mjs), the history store, the inbox (symlink attacks,
retention), blobs (resume, integrity, limits, staging), the Agent's composed message, the menu file, the approvals log for
questions, the CLI (`agentj history|inbox|config history`), the doctor's asr row, and `send_app` cutting a big message into
`frag` frames that each fit the relay. Chains with the stand-in harnesses: tests/test_p33_chain.py.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import base64
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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentj import approvals, compose, doctor, history, inbox, menu, serve, uploads, wire  # noqa: E402
from test_l1 import Phone, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
V = json.loads((ROOT / "protocol" / "vectors" / "p33.json").read_text())
NODE = shutil.which("node")


def png(n=100) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + os.urandom(n)


def wav(secs=1.0, rate=16000, ch=1, bits=16) -> bytes:
    n = int(secs * rate)
    data = b"\x00\x01" * n * ch
    fmt = (1).to_bytes(2, "little") + ch.to_bytes(2, "little") + rate.to_bytes(4, "little") + \
        (rate * ch * bits // 8).to_bytes(4, "little") + (ch * bits // 8).to_bytes(2, "little") + bits.to_bytes(2, "little")
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + b"data" + len(data).to_bytes(4, "little") + data
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def bid() -> str:
    return wire.b64u(os.urandom(16))


def node(js: str) -> str:
    return subprocess.run([NODE, "--input-type=module", "-e", js], capture_output=True, text=True, check=True,
                          cwd=str(ROOT)).stdout


class Wire(unittest.TestCase):
    def test_text_rules_match_the_shared_vectors(self):
        for c in V["text"]:
            self.assertEqual(wire.text_problem(c["s"], c["limit"]), c["want"], repr(c["s"][:30]))
        self.assertEqual(wire.normalize_newlines("a\r\nb\rc"), "a\nb\nc")
        self.assertEqual(wire.well_formed("a\ud800b\r\nc\x07"), "a�b\nc")
        self.assertEqual(wire.text_problem(5), "shape")

    def test_limits_both_directions_and_the_relay_payload(self):
        self.assertEqual({k: getattr(wire, k) for k in V["limits"]}, V["limits"])
        big = {"t": "x", "d": "a" * (wire.MAX_JSON_P33 - 16)}
        pt = wire.pad_json(big, wire.MAX_JSON_P33)
        self.assertLessEqual(1 + len(pt) + 16, 65536)
        self.assertEqual(wire.unpad_json(pt, wire.MAX_JSON_P33)["t"], "x")
        with self.assertRaises(ValueError):
            wire.unpad_json(pt)                      # a peer without p33: §3's 16 KiB, enforced by the receiver too
        with self.assertRaises(ValueError):
            wire.pad_json(big)
        with self.assertRaises(ValueError):
            wire.pad_json({"t": "x", "d": "a" * (wire.MAX_JSON_P33 - 15)}, wire.MAX_JSON_P33)

    def _inner(self):
        r = V["frag_recipe"]
        return {"t": "hist_turn", "epoch": 1, "turn": {"id": 1, "ts": 0, "src": {"k": "agent", "text": ""},
                                                       "reply": {"text": r["unit"] * r["repeat"]}, "end": "done"}}, r["fid"]

    def test_frag_split_reassemble_and_broken_sequences(self):
        inner, fid = self._inner()
        fr = wire.frag_split(inner, fid)
        self.assertGreater(len(fr), 1)
        self.assertTrue(all(len(wire.dumps(f)) <= wire.MAX_JSON_P33 for f in fr))
        d = wire.Defrag()
        out = [d.feed(f) for f in fr]
        self.assertEqual(out[-1], inner)
        self.assertTrue(all(x is None for x in out[:-1]))
        d = wire.Defrag()
        d.feed(fr[0])
        self.assertEqual(d.feed({"t": "status"}), {"t": "status"}, "another message is handled, the partial dropped")
        self.assertTrue(all(d.feed(f) is None for f in fr[1:]))
        d = wire.Defrag()
        d.feed(fr[0])
        self.assertIsNone(d.feed(fr[2]), "a wrong i drops it")
        self.assertIsNone(d.feed(fr[1]))
        self.assertEqual(wire.frag_split({"t": "s"}, "f"), [{"t": "s"}])
        with self.assertRaises(ValueError):
            wire.frag_split({"t": "x", "d": "a" * (wire.FRAG_MAX_TOTAL + 1)}, fid)

    @unittest.skipUnless(NODE, "node not installed")
    def test_frag_interop_with_protocol_wire_js(self):
        inner, fid = self._inner()
        py = wire.frag_split(inner, fid)
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "frags.json"
            p.write_text(json.dumps(py, ensure_ascii=False))
            js = ("import { Defrag, fragSplit } from './protocol/wire.js'; import { readFileSync } from 'node:fs';\n"
                  f"const fr = JSON.parse(readFileSync({json.dumps(str(p))}, 'utf8')); const d = new Defrag(); let o = null;\n"
                  "for (const f of fr) o = d.feed(f) ?? o;\n"
                  "const mine = fragSplit(o, 'fedcba9876543210');\n"
                  "console.log(JSON.stringify({ ok: o.turn.reply.text.length, frags: mine }));")
            res = json.loads(node(js))
        self.assertEqual(res["ok"], len(inner["turn"]["reply"]["text"].encode("utf-16-le")) // 2, "JS reassembled Python's")
        d = wire.Defrag()
        got = [d.feed(f) for f in res["frags"]]
        self.assertEqual(got[-1], inner, "Python reassembled JS's")


class Questions(unittest.TestCase):
    def test_vectors_digest_message_signature(self):
        q = V["question"]
        self.assertEqual(approvals.question_text(q["qs"]), q["q_text"])
        self.assertEqual(approvals.question_digest(q["qs"]), q["digest"])
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(V["seed_hex"]))
        pub = wire.unb64u(V["pub"])
        for c in q["cases"]:
            msg = approvals.question_message(c["channel"], c["device"], c["id"], c["action"], q["qs"], c["picks"])
            self.assertEqual(msg.decode(), c["message"])
            self.assertEqual(wire.b64u(sk.sign(msg)), c["sig"])
            self.assertTrue(approvals.verify_question(pub, wire.unb64u(c["sig"]), c["channel"], c["device"], c["id"],
                                                      c["action"], q["qs"], c["picks"]))
            other = json.loads(json.dumps(q["qs"]))
            other[0]["o"][1]["d"] = "x"
            self.assertFalse(approvals.verify_question(pub, wire.unb64u(c["sig"]), c["channel"], c["device"], c["id"],
                                                       c["action"], other, c["picks"]), "bound to the exact text shown")
        self.assertFalse(approvals.verify_question(pub, wire.unb64u(q["cases"][0]["sig"]), "C", "D", "a" * 32, "answer",
                                                   q["qs"], [[2], [1, 3]]), "bound to the picks")

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_signs_what_python_verifies(self):
        q = V["question"]
        js = ("import { questionMessage } from './protocol/wire.js';\n"
              f"const qs = {json.dumps(q['qs'], ensure_ascii=False)};\n"
              "const m = await questionMessage('CH', 'DEV', 'b'.repeat(32), 'answer', qs, [[1], [2, 3]]);\n"
              "console.log(Buffer.from(m).toString('hex'));")
        self.assertEqual(bytes.fromhex(node(js).strip()),
                         approvals.question_message("CH", "DEV", "b" * 32, "answer", q["qs"], [[1], [2, 3]]))

    def test_picks_and_card_bounds(self):
        qs = V["question"]["qs"]
        self.assertTrue(approvals.check_picks(qs, [[1], [1, 2, 3]]))
        for bad in ([[1]], [[1, 2], [1]], [[0], [1]], [[4], [1]], [[1], [3, 1]], [[1], [1, 1]], [[True], [1]], "x", None):
            self.assertFalse(approvals.check_picks(qs, bad), bad)
        ok = [{"question": "Q?", "header": "H", "options": [{"label": "A", "description": "a"}, {"label": "B"}]}]
        self.assertEqual(approvals.norm_questions(ok), [{"q": "Q?", "h": "H", "m": False,
                                                         "o": [{"l": "A", "d": "a"}, {"l": "B", "d": ""}]}])
        for bad in ([], [{"question": "Q", "options": []}], [{"question": "Q", "options": [{"label": "A"}, {"label": "A"}]}],
                    [{"question": "x" * 1001, "options": [{"label": "A"}]}], [{"question": "Q", "options": [{"label": "y" * 201}]}],
                    [{"question": "Q\x07", "options": [{"label": "A"}]}], ok * 9,
                    [{"question": "Q", "options": [{"label": str(i)} for i in range(17)]}],
                    [{"question": "Q", "options": [{"label": "A"}]}, {"question": "Q", "options": [{"label": "A"}]}]):
            self.assertIsNone(approvals.norm_questions(bad), str(bad)[:60])

    def test_log_line_has_no_text_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            ph = Phone(st)
            qs = V["question"]["qs"]
            ch = st.config()["channel"]
            sig = ph.sk.sign(approvals.question_message(ch, ph.did, "c" * 32, "answer", qs, [[2], [1]]))
            rec = approvals.record_question(st, qid="c" * 32, agent="claude", q_sha256=approvals.question_digest(qs),
                                            decision="answer", reason="device", picks="2;1", device=ph.did,
                                            sign_pub=bytes(ph.sk.public_key().public_bytes_raw()), sig=sig)
            line = st.approvals_path.read_text()
            for text in ("Which color", "Green", "周一", "蓝色"):
                self.assertNotIn(text, line)
            self.assertEqual(approvals.check_record(st, rec), "ok")
            self.assertEqual(approvals.check_record(st, {**rec, "picks": "1;1"}), "bad")
            to = approvals.record_question(st, qid="d" * 32, agent="claude", q_sha256="0" * 64, decision="timeout",
                                           reason="timeout")
            self.assertEqual(approvals.check_record(st, to), "unsigned")


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistent_paging_reset_undo(self):
        h = history.History(self.st)
        self.assertTrue(h.on)
        t1 = h.add({"k": "phone", "text": "一"})
        h.update(t1["id"], append="回复 A")
        h.update(t1["id"], append="回复 B", end="done")
        self.assertEqual(h.get(t1["id"])["reply"]["text"], "回复 A\n\n回复 B", "one page per turn, joined by a blank line")
        for i in range(60):
            h.add({"k": "sys", "text": ""}, f"n{i}", "done")
        self.assertEqual(oct((h.dir).stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct(h.cur_path.stat().st_mode & 0o777), "0o600")
        h2 = history.History(self.st)                         # a host restart loses nothing
        self.assertEqual(h2.meta(), h.meta())
        self.assertEqual(h2.get(t1["id"])["reply"]["text"], "回复 A\n\n回复 B")
        newest, more = h2.page(limit=50)
        self.assertEqual((len(newest), more, newest[-1]["reply"]["text"]), (50, True, "n59"))
        older, more = h2.page(before=newest[0]["id"], limit=50)
        self.assertEqual((len(older), more), (11, False))
        after, more = h2.page(after=newest[-3]["id"])
        self.assertEqual([t["reply"]["text"] for t in after], ["n58", "n59"])
        ep, nid = h2.epoch, h2.next_id
        name = h2.reset("clear")
        self.assertTrue(name and (h2.arch_dir / name).exists())
        self.assertEqual((h2.epoch, h2.meta()["count"], h2.next_id), (ep + 1, 0, nid), "ids keep growing")
        x = h2.add({"k": "phone", "text": "清空之后"})
        self.assertGreater(x["id"], newest[-1]["id"])
        self.assertTrue(h2.undo_reset())
        self.assertEqual(h2.epoch, ep + 2)
        self.assertIsNotNone(h2.get(t1["id"]), "the archive is current again")
        self.assertIsNone(h2.get(x["id"]))
        self.assertTrue(any(h2.arch_dir.glob("*.jsonl")), "what was said after the clear is archived, not lost")
        self.assertFalse(h2.undo_reset(), "one undo per clear")
        self.assertIsNone(history.History(self.st).reset("x") if False else None)

    def test_compaction_parts_archive_cap_and_off(self):
        h = history.History(self.st)
        t = h.add({"k": "phone", "text": "x"})
        for _ in range(650):
            h.update(t["id"], end="open")
        self.assertLessEqual(h.lines, history.COMPACT_AT)
        self.assertEqual(len(h.cur_path.read_text().splitlines()), h.lines)
        for i in range(history.KEEP + 20):
            h.add({"k": "sys", "text": ""}, str(i), "done")
        h3 = history.History(self.st)
        self.assertLessEqual(h3.meta()["count"], history.COMPACT_AT)
        long = h.add({"k": "phone", "text": "long"})
        changed = h.update(long["id"], append="长" * (history.PART_MAX + 10))
        self.assertEqual([c["reply"]["part"] for c in changed], [[1, 2], [2, 2]], "nothing truncated: a second page")
        self.assertEqual(sum(len(c["reply"]["text"]) for c in changed), history.PART_MAX + 10)
        for i in range(12):
            h.add({"k": "sys", "text": ""}, "a", "done")
            h.reset(f"r{i}")
        self.assertLessEqual(len(h.archives()), history.ARCHIVE_KEEP)
        history.set_enabled(self.st, False)
        off = history.History(self.st)
        self.assertFalse(off.on)
        for i in range(150):
            off.add({"k": "sys", "text": ""}, str(i), "done")
        self.assertEqual(off.meta()["count"], history.MEM_KEEP)
        self.assertNotEqual(off.epoch, h.epoch)


class Inbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.src = pathlib.Path(self.tmp.name) / "src.png"
        self.src.write_bytes(png())

    def tearDown(self):
        self.tmp.cleanup()

    def test_place_names_modes_gitignore(self):
        p = inbox.place(str(self.work), str(self.src), "../../.ssh/authorized_keys", "image/png", bid())
        self.assertTrue(p.startswith(str(self.work.resolve()) + "/.agentj/inbox/"))
        self.assertTrue(p.endswith("-authorized_keys.png"), p)
        self.assertEqual(pathlib.Path(p).read_bytes(), self.src.read_bytes())
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
        self.assertEqual(oct((self.work / ".agentj").stat().st_mode & 0o777), "0o700")
        self.assertEqual((self.work / ".agentj" / ".gitignore").read_text(), "*\n")
        self.assertEqual(inbox.slugify("\x00..//"), "upload")
        self.assertEqual(inbox.slugify("报告 2026.final.PDF"), "报告_2026.final")
        self.assertFalse(list((self.work / ".agentj" / "inbox").rglob(".part-*")))

    def test_every_planted_link_is_refused(self):
        evil = pathlib.Path(self.tmp.name) / "evil"
        evil.mkdir()
        for planted in (".agentj", ".agentj/inbox", "day"):
            w = pathlib.Path(self.tmp.name) / f"w-{planted.replace('/', '_')}"
            w.mkdir()
            if planted == ".agentj":
                (w / ".agentj").symlink_to(evil)
            elif planted == ".agentj/inbox":
                (w / ".agentj").mkdir()
                (w / ".agentj" / "inbox").symlink_to(evil)
            else:
                (w / ".agentj" / "inbox").mkdir(parents=True)
                (w / ".agentj" / "inbox" / time.strftime("%Y-%m-%d")).symlink_to(evil)
            with self.assertRaises(inbox.Unsafe, msg=planted):
                inbox.place(str(w), str(self.src), "a", "image/png", bid())
            self.assertEqual(list(evil.iterdir()), [], f"nothing written through {planted}")
        w = pathlib.Path(self.tmp.name) / "w-git"
        (w / ".agentj").mkdir(parents=True)
        (w / ".agentj" / ".gitignore").symlink_to(evil / "x")
        inbox.place(str(w), str(self.src), "a", "image/png", bid())     # O_EXCL: never written through the link
        self.assertFalse((evil / "x").exists())

    def test_retention_days_and_quota_never_today(self):
        base = self.work / ".agentj" / "inbox"
        now = time.time()
        old = time.strftime("%Y-%m-%d", time.localtime(now - 40 * 86400))
        mid = time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400))
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        for d in (old, mid, today):
            (base / d).mkdir(parents=True)
            (base / d / "f").write_bytes(b"x" * 10)
        self.assertEqual(inbox.retain(str(self.work), now=now), [old])
        q = inbox.QUOTA
        try:
            inbox.QUOTA = 15
            self.assertEqual(inbox.retain(str(self.work), now=now), [mid], "over quota: the oldest day, never today")
            (base / mid).mkdir()
            (base / mid / "f").write_bytes(b"x" * 10)
            held = {str(base / mid / "f")}
            self.assertEqual(inbox.retain(str(self.work), keep=held, now=now), [], "a day holding a staged file stays")
        finally:
            inbox.QUOTA = q
        self.assertTrue((base / today).exists())
        self.assertEqual(inbox.clear(str(self.work)), 2)


class Blobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.now = [time.time()]
        self.up = uploads.Uploads(self.st, str(self.work), clock=lambda: self.now[0])
        self.dev = "D" * 16

    def tearDown(self):
        self.tmp.cleanup()

    def open(self, data, mime="image/png", purpose="att", origin="file", dev=None, b=None, **kw):
        b = b or bid()
        m = {"bid": b, "purpose": purpose, "name": "photo.png", "mime": mime, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "origin": origin, **kw}
        return b, m, self.up.open(dev or self.dev, m)

    def send(self, b, data, start=0, dev=None):
        out = []
        for o in range(start, len(data), uploads.CHUNK):
            out += self.up.chunk(dev or self.dev, {"bid": b, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
        return out

    def test_upload_resume_after_restart_integrity_and_staging(self):
        data = png(200_000)
        b, m, r = self.open(data)
        self.assertEqual(r, [{"t": "blob_ack", "bid": b, "next": 0}])
        acks = self.send(b, data[:uploads.CHUNK * 3])
        self.assertEqual(acks, [], "acks every 4th chunk")
        self.assertEqual(oct((self.st.root / "uploads" / self.dev).stat().st_mode & 0o777), "0o700")
        up2 = uploads.Uploads(self.st, str(self.work), clock=lambda: self.now[0])    # serve restarted
        self.up = up2
        self.assertEqual(up2.open(self.dev, m), [{"t": "blob_ack", "bid": b, "next": uploads.CHUNK * 3}], "resume where it stopped")
        self.assertEqual(up2.chunk(self.dev, {"bid": b, "o": 0, "d": b64(data[:10])}),
                         [{"t": "blob_ack", "bid": b, "next": uploads.CHUNK * 3}], "a chunk at another offset → resync")
        self.send(b, data, start=uploads.CHUNK * 3)
        out, take = up2.end(self.dev, b)
        self.assertIsNone(take)
        self.assertEqual(out[-1], {"t": "blob_done", "bid": b, "ok": True, "kind": "image", "bytes": len(data)})
        blob = up2.blobs[(self.dev, b)]
        self.assertTrue(blob.path.startswith(str(self.work.resolve())))
        self.assertEqual(pathlib.Path(blob.path).read_bytes(), data)
        self.assertEqual(up2.open(self.dev, m)[0]["t"], "blob_done", "a lost blob_done is said again")
        with self.assertRaises(uploads.Refused) as e:
            up2.claim("E" * 16, [b])
        self.assertEqual(e.exception.why, "att_gone", "another device's id reads as gone")
        got = up2.claim(self.dev, [b])
        with self.assertRaises(uploads.Refused):
            up2.claim(self.dev, [b])
        self.assertEqual(up2.drop(self.dev, b)[0]["why"], "dropped")
        self.assertTrue(os.path.exists(blob.path), "a claimed file is never deleted by a drop")
        up2.release(got)
        self.assertEqual(up2.claim(self.dev, [b])[0].bid, b, "released → staged again under the same id")
        up2.commit(got)
        self.assertTrue(os.path.exists(blob.path), "delivered: the file is the Agent's")
        self.assertNotIn((self.dev, b), up2.blobs)

    def test_refusals(self):
        data = png(1000)
        b, m, r = self.open(data, mime="application/x-sh")
        self.assertEqual(r[0]["why"], "type")
        b, m, r = self.open(data, mime="image/png", purpose="asr")
        self.assertEqual(r[0]["why"], "type", "asr takes only WAV")
        b, m, r = self.open(b"")
        self.assertEqual(r[0]["why"], "empty")
        self.assertEqual(self.up.open(self.dev, {**m, "size": uploads.MAX_SIZE + 1, "bid": bid()})[0]["why"], "too_big")
        w = wav(1)
        b, m, r = self.open(w, mime="audio/wav", purpose="asr", origin="recording", secs=1)
        self.assertEqual(r[0]["t"], "blob_ack")
        self.assertEqual(self.up.open(self.dev, {**m, "bid": bid(), "size": uploads.ASR_MAX + 1})[0]["why"], "too_big")
        b2, _, _ = self.open(data)
        self.assertEqual(self.open(data)[2][0]["why"], "too_many", "≤ 2 open per device")
        self.up.drop(self.dev, b)
        self.up.drop(self.dev, b2)
        # sha mismatch, size mismatch, magic bytes
        bad = png(500)
        b, m, _ = self.open(bad)
        self.send(b, bad[:-1] + b"\x00")
        self.assertEqual(self.up.end(self.dev, b)[0][-1]["why"], "sha_mismatch")
        self.assertFalse((self.st.root / "uploads" / self.dev / f"{b}.part").exists(), "the partial is deleted")
        b, m, _ = self.open(bad)
        self.send(b, bad[:100])
        self.assertEqual(self.up.end(self.dev, b)[0][-1]["why"], "size_mismatch")
        fake = b"MZ" + os.urandom(100)
        b, m, _ = self.open(fake, mime="image/jpeg")
        self.assertEqual(self.send(b, fake)[-1]["why"], "type", "an .exe declared as a JPEG")
        # staged TTL: the never-announced inbox file goes
        b, m, _ = self.open(data)
        self.send(b, data)
        self.up.end(self.dev, b)
        path = self.up.blobs[(self.dev, b)].path
        self.now[0] += uploads.STAGED_TTL + 1
        self.up.sweep()
        self.assertFalse(os.path.exists(path))
        with self.assertRaises(uploads.Refused) as e:
            self.up.claim(self.dev, [b])
        self.assertEqual((e.exception.why, e.exception.att), ("att_gone", [b]))
        with self.assertRaises(uploads.Refused) as e:
            self.up.claim(self.dev, [bid() for _ in range(11)])
        self.assertEqual(e.exception.why, "too_many_att")
        # asr off → refused at open
        off = uploads.Uploads(self.st, str(self.work), asr_off=lambda: True)
        self.assertEqual(off.open(self.dev, {**m, "bid": bid(), "purpose": "asr", "mime": "audio/wav", "size": len(w),
                                             "sha256": hashlib.sha256(w).hexdigest()})[0]["why"], "asr_off")
        # an unsafe inbox: nothing written
        evil = pathlib.Path(self.tmp.name) / "evil"
        evil.mkdir()
        w2 = pathlib.Path(self.tmp.name) / "w2"
        w2.mkdir()
        (w2 / ".agentj").symlink_to(evil)
        up3 = uploads.Uploads(self.st, str(w2))
        self.assertEqual(up3.open("F" * 16, {**m, "bid": bid()})[0]["why"], "unsafe_inbox")

    def test_wav_strict(self):
        d = pathlib.Path(self.tmp.name)
        for name, data, ok in (("ok", wav(1), True), ("stereo", wav(1, ch=2), False), ("44k", wav(1, rate=44100), False),
                               ("8bit", wav(1, bits=8), False), ("trunc", wav(1)[:-10], False), ("junk", os.urandom(200), False)):
            (d / name).write_bytes(data)
            self.assertEqual(uploads.wav_ok(str(d / name)), ok, name)


class Compose(unittest.TestCase):
    def test_render_zh_en(self):
        files = [{"path": "/w/.agentj/inbox/2026-10-03/120000-abcdef-a.png", "mime": "image/png", "bytes": 12345, "origin": "file"},
                 {"path": "/w/.agentj/inbox/2026-10-03/120001-abcdef-v.wav", "mime": "audio/wav", "bytes": 32044,
                  "origin": "recording", "asr": {"ok": True, "text": "你好世界", "secs": 1.2}},
                 {"path": "/w/.agentj/inbox/2026-10-03/120002-abcdef-x.webm", "mime": "audio/webm", "bytes": 999,
                  "origin": "recording", "asr": {"ok": False, "why": "format"}}]
        turn = {"id": 7, "ts": 0, "src": {"k": "agent"}, "reply": {"text": "第一行\n第二行" + "长" * 400}}
        q = compose.quote_block(turn, None, "zh")
        out = compose.render("看看这个", files, "zh", q)
        self.assertTrue(out.startswith("【回复 #7 · Agent · "))
        self.assertIn("> 第一行\n> 第二行", out)
        self.assertEqual(compose.quote_info(turn, None, "zh")["text"], ("第一行\n第二行" + "长" * 400)[:300])
        self.assertIn("附件 3 个（已在你的工作目录里，内容没有经过聊天通道——需要时自己读这些路径）：", out)
        self.assertIn("- /w/.agentj/inbox/2026-10-03/120000-abcdef-a.png（image/png，12,345 字节）", out)
        self.assertIn("语音转写（1 秒，120001-abcdef-v.wav）：\n你好世界", out)
        self.assertIn("转写失败（格式）——音频仍在上面的路径。", out)
        ex = compose.quote_block(turn, "只这一句", "zh")
        self.assertIn("> 只这一句\n> （摘录）", ex)
        en = compose.render("look", files[:1], "en")
        self.assertIn("1 attachment(s)", en)

    def test_withdraw_lock(self):
        async def go():
            ss = compose.Sends()
            s = compose.Send("D", "s" * 22, 1)
            ss.add(s)
            self.assertTrue(ss.used("D", "s" * 22))
            self.assertEqual((await ss.cancel("E", "s" * 22))[0], "not_found")
            async with s.lock:                    # a write in progress: the cancel waits, then reads delivered
                t = asyncio.create_task(ss.cancel("D", "s" * 22))
                await asyncio.sleep(0.05)
                self.assertFalse(t.done())
                s.state = "delivered"
            self.assertEqual((await t)[0], "already_delivered")
            s2 = compose.Send("D", "t" * 22, 2)
            ss.add(s2)
            self.assertEqual((await ss.cancel("D", "t" * 22))[0], "cancelled")
            self.assertEqual(s2.state, "cancelled")
            added = [ss.add(compose.Send("D", f"{i:022d}", 3)) for i in range(20)]
            self.assertEqual(added, [True] * compose.MAX_SENDS + [False] * 4, "≤ 16 waiting; more are refused (P33-C03)")
            self.assertEqual(sum(1 for x in ss.items.values() if x.device == "D" and x.state in compose.UNSENT),
                             compose.MAX_SENDS)
        asyncio.run(go())


class Menu(unittest.TestCase):
    def test_file_default_problems_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            w = pathlib.Path(d)
            r = menu.served(str(w), ["fake-skill", "clear"], "zh")
            self.assertEqual(r["source"], "default")
            self.assertEqual([i["cmd"] for i in r["items"]], ["/" + c for c in ("clear", "compact", "model", "context", "cost",
                                                                              "usage", "status", "help", "stop", "update",
                                                                              "add-friend", "my-agent-id")])   # + P73 friends
            self.assertEqual(r["skills"], [{"cmd": "/fake-skill", "desc": ""}])
            self.assertIn("compact", r["cmds"])
            (w / ".agentj").mkdir()
            doc = {"version": 1, "items": [{"cmd": "/b", "desc": "B", "group": "g2", "order": 2},
                                           {"cmd": "/a", "desc": "A", "group": "g1", "order": 1}, {"cmd": "/c", "desc": "C", "group": "g2"}]}
            (w / ".agentj" / "menu.json").write_text(json.dumps(doc))
            r = menu.served(str(w), [], "en")
            self.assertEqual((r["source"], [i["cmd"] for i in r["items"]]), ("file", ["/a", "/b", "/c"]))
            (w / ".agentj" / "menu.json").write_text(json.dumps({"version": 1, "items": [{"cmd": "rm -rf", "desc": ""}]}))
            r = menu.served(str(w), [], "zh")
            self.assertEqual(r["source"], "default")
            self.assertTrue(r["problems"])
            (w / ".agentj" / "menu.json").unlink()
            (w / ".agentj" / "menu.json").symlink_to("/etc/hostname")
            r = menu.served(str(w), [], "zh")
            self.assertEqual(r["source"], "default")
            self.assertIn("link", r["problems"][0])
            self.assertEqual(menu.validate({"version": 1, "items": [{"cmd": "/x", "desc": "y" * 201}]})[0][:12], "items[0].des")


class SendApp(unittest.TestCase):
    def test_a_big_message_goes_as_frag_frames_each_under_the_relay_cap(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            ph = Phone(st)
            host = serve.Host(st, events="quiet", read_stdin=False)
            frames = []

            async def op(o, cid, payload=b""):
                frames.append((o, cid, payload))
            host._op = op
            from agentj.noise import CipherState
            s33 = serve.Session(cid=5, state="ready", device=ph.did, pub=ph.pub, p33=True, send=CipherState(None))
            old = serve.Session(cid=6, state="ready", device=ph.did, pub=ph.pub, send=CipherState(None))
            host.sessions = {5: s33, 6: old}
            big = {"t": "hist_page", "r": "r", "turns": [{"reply": {"text": "长" * 100_000}}]}

            async def go():
                self.assertTrue(await host.send_app(s33, big))
                await host._bulk(s33)
                await host._bulk(old)
            asyncio.run(go())
            data = [p for o, c, p in frames if o == wire.OP_DATA]
            self.assertGreater(len(data), 1)
            self.assertTrue(all(len(p) <= 65536 for p in data))
            df = wire.Defrag()
            got = [df.feed(wire.unpad_json(p[1:], wire.MAX_JSON_P33)) for p in data]
            self.assertEqual(got[-1], big)
            self.assertEqual([(o, c) for o, c, _ in frames if o == wire.OP_BULK], [(wire.OP_BULK, 5)],
                             "BULK only for the ready p33 session")
            self.assertEqual(host._caps()["caps"], ["p33", "heartbeat"])
            self.assertIn(host._caps()["asr"], ("ready", "not_installed", "off", "broken"))


class Cli(unittest.TestCase):
    def run_cli(self, env, *args):
        return subprocess.run([sys.executable, "-m", "agentj.cli", *args], env=env, capture_output=True, text=True, timeout=60,
                              cwd=str(HERE.parent))

    def test_history_inbox_config_asr(self):
        with tempfile.TemporaryDirectory() as d:
            sd = pathlib.Path(d) / "s"
            # P64: the in-process State calls below (set_agent_config → working_root.record) and the CLI must read ONE preferences
            # file — before, the CLI used HOME=d's (init's default root d/coding) and this process wrote its own.
            cfg_home = str(pathlib.Path(d) / ".config")
            env = {**os.environ, "AGENTJ_STATE_DIR": str(sd), "HOME": d, "XDG_CONFIG_HOME": cfg_home, "PYTHONPATH": str(HERE.parent)}
            xdg = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": cfg_home})
            xdg.start()
            self.addCleanup(xdg.stop)
            self.assertEqual(self.run_cli(env, "init", "--relay", "ws://127.0.0.1:1").returncode, 0)
            st = __import__("agentj.state", fromlist=["State"]).State(sd)
            h = history.History(st)
            t = h.add({"k": "phone", "text": "秘密的问题"}, "秘密的回答", "done")
            r = self.run_cli(env, "history")
            self.assertIn("history on", r.stdout)
            self.assertIn(f"#{t['id']}", r.stdout)
            r = self.run_cli(env, "history", "show", str(t["id"]))
            self.assertIn("秘密的回答", r.stdout)
            r = self.run_cli(env, "history", "clear")
            self.assertIn("archived", r.stdout)
            self.assertEqual(history.History(st).meta()["count"], 0)
            r = self.run_cli(env, "config", "history", "off")
            self.assertIn("history off", r.stdout)
            self.assertEqual(st.config()["history"], "off")
            self.assertIn("history on", self.run_cli(env, "history", "on").stdout)
            self.assertNotEqual(self.run_cli(env, "inbox").returncode, 0, "no Agent folder yet")
            work = pathlib.Path(d) / "w"
            work.mkdir()
            st.set_agent_config("claude", str(work))
            self.assertEqual(self.run_cli(env, "inbox", "path").stdout.strip(), str(work.resolve() / ".agentj" / "inbox"))
            src = pathlib.Path(d) / "x.png"
            src.write_bytes(png())
            inbox.place(str(work), str(src), "x", "image/png", bid())
            r = self.run_cli(env, "inbox", "--json")
            self.assertEqual(json.loads(r.stdout)["days"][0]["files"], 1)
            self.assertIn("1", self.run_cli(env, "inbox", "clear").stdout)
            r = self.run_cli(env, "asr", "status")
            self.assertIsInstance(r.returncode, int)            # wired to asr.cli_main (its own tests live with it)
            log = (sd / "host.log").read_text()
            for secret in ("秘密", "x.png"):
                self.assertNotIn(secret, log)

    def test_doctor_shows_the_asr_rows(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            rows = doctor.check_asr(st)
            self.assertTrue(rows and rows[0]["id"] == "asr", rows)


if __name__ == "__main__":
    unittest.main()
