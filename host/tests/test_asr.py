"""asr.py + asr_worker.py (PROTOCOL §10.9): WAV rules, text cleanup, resampling, segmentation, engine choice (voxtype never
automatic), busy / timeout / off / not_installed, safe extraction, resumable hash-checked downloads, install refusals,
doctor row shape — and, with the real model, transcripts, the resident worker (reuse, idle exit, crash, timeout) and
remove.

The real-engine class needs an installed engine: either AGENTJ_ASR_TEST_STATE=<a state dir where `agentj asr install`
ran>, or AGENTJ_ASR_DOWNLOADS=<folder with the model tarball + the two wheels> (the test installs from it into a temp
state, offline, ≈ 15 s). Otherwise it is skipped with that reason. No audio here is a person's voice of ours: the model's
own test_wavs, silence and synthesised tones only.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import array
import http.server
import io
import json
import math
import os
import pathlib
import shutil
import stat
import struct
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import wave
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import asr, asr_worker  # noqa: E402
from agentj.state import State  # noqa: E402

RATE = 16000


def write_wav(path, frames: bytes, rate=RATE, ch=1):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(ch)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(frames)
    return str(path)


def tone(seconds, freq=440.0, rate=RATE, amp=0.3):
    n = int(seconds * rate)
    return array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))).tobytes()


def silence(seconds, rate=RATE):
    return b"\x00\x00" * int(seconds * rate)


def raw_wav(fmt_tag=1, ch=1, rate=RATE, bits=16, data=b"\x00\x00" * 160, riff_fix=0, data_fix=0, extra=b"", fmt_ext=b""):
    align = ch * bits // 8
    fmt = struct.pack("<HHIIHH", fmt_tag, ch, rate, rate * align, align, bits) + fmt_ext
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", len(data) + data_fix) + data
    return b"RIFF" + struct.pack("<I", len(body) + riff_fix) + body


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-asr-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "state"
        State(self.state).init()
        self.addCleanup(asr.shutdown)

    def put(self, name, data: bytes) -> str:
        p = self.tmp / name
        p.write_bytes(data)
        return str(p)


# ------------------------------------------------------------------ pure parts
class WavCheck(Tmp):
    def test_good_16k_mono_strict(self):
        p = write_wav(self.tmp / "a.wav", silence(1))
        info = asr.check_wav(p, strict=True)
        self.assertEqual((info["rate"], info["channels"], info["frames"], info["offset"]), (16000, 1, 16000, 44))

    def test_other_layouts_accepted_but_not_strict(self):
        for rate, ch in ((48000, 2), (44100, 1), (8000, 1), (16000, 2)):
            p = write_wav(self.tmp / f"{rate}{ch}.wav", silence(0.5, rate) * ch, rate, ch)
            self.assertEqual(asr.check_wav(p)["rate"], rate)
            with self.assertRaises(asr.BadAudio):
                asr.check_wav(p, strict=True)

    def test_rejections(self):
        bad = {
            "notriff": b"RIFX" + raw_wav()[4:],
            "riffsize": raw_wav(riff_fix=2),
            "datalen": raw_wav(data_fix=2),
            "8bit": raw_wav(bits=8, data=b"\x80" * 100),
            "float": raw_wav(fmt_tag=3, bits=32, data=b"\x00" * 400),
            "3ch": raw_wav(ch=3, data=b"\x00" * 600),
            "96k": raw_wav(rate=96000),
            "partial_frame": raw_wav(ch=2, data=b"\x00" * 402),
            "two_fmt": raw_wav(extra=b"fmt " + struct.pack("<I", 16) + b"\x00" * 16),
            "empty": b"",
            "short": b"RIFF\x04\x00\x00\x00WAVE",
        }
        for name, data in bad.items():
            with self.subTest(name):
                with self.assertRaises(asr.BadAudio):
                    asr.check_wav(self.put(name + ".wav", data))
        with self.assertRaises(asr.BadAudio):
            asr.check_wav(str(self.tmp / "missing.wav"))

    def test_data_before_fmt_and_trailing_chunk(self):
        d = b"\x00\x00" * 10
        body = b"WAVE" + b"data" + struct.pack("<I", len(d)) + d
        with self.assertRaises(asr.BadAudio):
            asr.check_wav(self.put("x.wav", b"RIFF" + struct.pack("<I", len(body)) + body))
        trailing = raw_wav() + b"LIST" + struct.pack("<I", 4) + b"INFO"
        trailing = b"RIFF" + struct.pack("<I", len(trailing) - 8) + trailing[8:]
        with self.assertRaises(asr.BadAudio):
            asr.check_wav(self.put("t.wav", trailing))

    def test_list_chunk_before_data_ok_and_extensible_pcm(self):
        p = self.put("l.wav", raw_wav(extra=b"LIST" + struct.pack("<I", 4) + b"INFO"))
        self.assertEqual(asr.check_wav(p)["channels"], 1)
        with self.assertRaises(asr.BadAudio):
            asr.check_wav(p, strict=True)
        ext = struct.pack("<HHI", 22, 16, 4) + b"\x01\x00" + asr_worker.PCM_GUID_TAIL
        self.assertEqual(asr.check_wav(self.put("e.wav", raw_wav(fmt_tag=0xFFFE, fmt_ext=ext)))["rate"], RATE)
        ext_float = struct.pack("<HHI", 22, 16, 4) + b"\x03\x00" + asr_worker.PCM_GUID_TAIL
        with self.assertRaises(asr.BadAudio):
            asr.check_wav(self.put("f.wav", raw_wav(fmt_tag=0xFFFE, fmt_ext=ext_float)))

    def test_too_long(self):
        p = write_wav(self.tmp / "long.wav", b"\x00\x00" * (8000 * 631), rate=8000)
        with self.assertRaises(asr.BadAudio) as cm:
            asr.check_wav(p)
        self.assertIn("too long", str(cm.exception))


class Text(unittest.TestCase):
    def test_sensevoice_tags_stripped(self):
        self.assertEqual(asr_worker.clean_text("<|zh|><|NEUTRAL|><|Speech|><|woitn|>开饭时间早上9点"), "开饭时间早上9点")
        self.assertEqual(asr_worker.clean_text("<|en|><|HAPPY|><|BGM|><|withitn|> Hello  world. "), "Hello world.")
        self.assertEqual(asr_worker.clean_text("a <b> c"), "a <b> c")

    def test_join(self):
        self.assertEqual(asr_worker.join_texts(["你好。", "再见。"]), "你好。再见。")
        self.assertEqual(asr_worker.join_texts(["Hello.", "你好", "", "world"]), "Hello. 你好 world")

    def test_voxtype_noise_filtered(self):
        out = ("\x1b[2m2026-10-03T10:00:00Z\x1b[0m \x1b[32m INFO\x1b[0m loading model\nLoading audio file: /x.wav\n"
               "Audio format: 16000 Hz, 1 ch\nProcessing 1.2s of audio\nUsing remote engine\n<|zh|><|NEUTRAL|>你好世界\n")
        self.assertEqual(asr.voxtype_clean(out), "你好世界")
        self.assertEqual(asr.voxtype_clean("Loading audio file: a\n"), "")


class Resample(Tmp):
    def _freq(self, samples, rate):
        zc = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
        return zc / 2 / (len(samples) / rate)

    def test_48k_stereo_and_44k_and_8k_to_16k(self):
        for rate, ch in ((48000, 2), (44100, 1), (8000, 1), (32000, 1)):
            with self.subTest(rate=rate, ch=ch):
                mono = array.array("h", tone(1.0, 440, rate))
                if ch == 2:
                    st = array.array("h", bytes(len(mono) * 4))
                    st[0::2] = mono
                    st[1::2] = mono
                    mono = st
                p = write_wav(self.tmp / f"r{rate}.wav", mono.tobytes(), rate, ch)
                s = asr_worker.load_samples(p)
                self.assertAlmostEqual(len(s), RATE, delta=3)
                self.assertAlmostEqual(self._freq(s, RATE), 440, delta=5)
                self.assertLess(max(map(abs, s)), 0.31)
                self.assertGreater(max(map(abs, s)), 0.25)

    def test_16k_passthrough_scale(self):
        p = write_wav(self.tmp / "s.wav", struct.pack("<3h", 0, 16384, -32768))
        self.assertEqual(list(asr_worker.load_samples(p)), [0.0, 0.5, -1.0])


class Segments(unittest.TestCase):
    def test_silence_only(self):
        self.assertEqual(asr_worker.segments(array.array("f", bytes(4 * RATE * 30))), [])

    def test_short_take_whole(self):
        s = array.array("f", (0.3 * math.sin(2 * math.pi * 300 * i / RATE) for i in range(RATE * 12)))
        s[RATE * 3:RATE * 5] = array.array("f", bytes(4 * RATE * 2))     # a 2 s pause: still one piece (≤ 20 s)
        self.assertEqual(asr_worker.segments(s), [(0, len(s))])

    def test_long_take_cut_at_pauses(self):
        burst = [0.3 * math.sin(2 * math.pi * 300 * i / RATE) for i in range(4 * RATE)]
        gap = [0.0] * RATE
        s = array.array("f", (burst + gap) * 15)               # 75 s: 15 × (4 s speech + 1 s pause)
        segs = asr_worker.segments(s)
        self.assertGreater(len(segs), 4)
        for a, b in segs:
            self.assertLessEqual(b - a, asr_worker.PIECE_JOIN_S * RATE + RATE)
        covered = sum(b - a for a, b in segs)
        self.assertGreater(covered, 60 * RATE)                # every burst is in some piece
        for a, b in zip(segs, segs[1:]):
            self.assertLessEqual(a[1], b[0])

    def test_long_take_without_pause_capped(self):
        s = array.array("f", (0.3 * math.sin(2 * math.pi * 300 * i / RATE) for i in range(RATE * 70)))
        segs = asr_worker.segments(s)
        self.assertTrue(all(b - a <= asr_worker.PIECE_JOIN_S * RATE + RATE for a, b in segs) or
                        all(b - a <= asr_worker.PIECE_MAX_S * RATE for a, b in segs))
        self.assertEqual(segs[0][0], 0)
        self.assertEqual(segs[-1][1], len(s))


# ------------------------------------------------------------------ engine choice, gate, voxtype (fake)
FAKE_VOXTYPE = """#!/bin/sh
# fake voxtype: progress noise on stdout, then the words from $FAKE_VOX_TEXT; sleeps $FAKE_VOX_SLEEP first
[ "$1" = transcribe ] || exit 64
[ -n "$FAKE_VOX_SLEEP" ] && sleep "$FAKE_VOX_SLEEP"
echo "Loading audio file: $2"
echo "Processing 1.0s of audio"
printf '%s\\n' "$FAKE_VOX_TEXT"
exit "${FAKE_VOX_RC:-0}"
"""


class Engines(Tmp):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.wav = write_wav(self.tmp / "a.wav", silence(1))

    def fake_voxtype(self, text="你好", sleep="", rc="0"):
        p = self.bin / "voxtype"
        p.write_text(FAKE_VOXTYPE)
        p.chmod(0o755)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_VOX_TEXT": text, "FAKE_VOX_SLEEP": sleep, "FAKE_VOX_RC": rc}
        m = mock.patch.dict(os.environ, env)
        m.start()
        self.addCleanup(m.stop)

    def cli(self, *argv):
        lines = []
        rc = asr.cli_main(list(argv), state_dir=self.state, out=lines.append, inp=io.StringIO(""))
        return rc, "\n".join(lines)

    def test_default_not_installed(self):
        r = asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)
        self.assertEqual((r["ok"], r["reason"]), (False, "not_installed"))
        self.assertEqual(asr.status(self.state)["state"], "not_installed")
        self.assertEqual(asr.ready_state(self.state), "not_installed")
        self.assertEqual(asr.settings(self.state), {"engine": "auto", "threads": asr.default_threads()})

    def test_auto_never_picks_voxtype(self):
        self.fake_voxtype()
        r = asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)
        self.assertEqual(r["reason"], "not_installed")
        self.assertIsNone(asr.status(self.state)["active"])

    def test_engine_voxtype_opt_in(self):
        self.fake_voxtype("<|zh|><|NEUTRAL|>你好世界")
        rc, out = self.cli("engine", "voxtype")
        self.assertEqual(rc, 0)
        self.assertIn("voxtype", out)
        self.assertEqual(json.loads((self.state / "config.json").read_text())["asr"]["engine"], "voxtype")
        r = asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)
        self.assertEqual((r["ok"], r["text"], r["engine"]), (True, "你好世界", "voxtype"))
        self.assertIsInstance(r["ms"], int)
        s = asr.status(self.state)
        self.assertEqual((s["state"], s["active"]), ("ready", "voxtype"))

    def test_voxtype_missing_no_speech_failure_timeout(self):
        self.cli("engine", "voxtype")
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertEqual(asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)["reason"], "not_installed")
            self.assertEqual(asr.status(self.state)["state"], "not_installed")
        self.fake_voxtype(text="")
        self.assertEqual(asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)["reason"], "no_speech")
        os.environ["FAKE_VOX_RC"] = "3"
        r = asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)
        self.assertEqual(r["reason"], "engine_failed")
        self.assertEqual(asr.why(r["reason"]), "broken")
        os.environ.update({"FAKE_VOX_RC": "0", "FAKE_VOX_SLEEP": "3", "FAKE_VOX_TEXT": "x"})
        t0 = time.monotonic()
        self.assertEqual(asr.transcribe(self.wav, timeout_s=0.5, state_dir=self.state)["reason"], "timeout")
        self.assertLess(time.monotonic() - t0, 2.5)

    def test_bad_audio_before_engine(self):
        self.fake_voxtype()
        self.cli("engine", "voxtype")
        r = asr.transcribe(self.put("bad.wav", b"not a wav"), timeout_s=5, state_dir=self.state)
        self.assertEqual(r["reason"], "bad_audio")

    def test_off(self):
        rc, _ = self.cli("engine", "off")
        self.assertEqual(rc, 0)
        self.assertEqual(asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)["reason"], "off")
        self.assertEqual(asr.status(self.state)["state"], "off")
        self.assertEqual(asr.ready_state(self.state), "off")
        self.assertEqual(asr.doctor_rows(self.state)[0]["status"], "ok")

    def test_busy_beyond_queue(self):
        self.fake_voxtype(text="ok", sleep="1.5")
        self.cli("engine", "voxtype")
        results = []
        ths = [threading.Thread(target=lambda: results.append(asr.transcribe(self.wav, timeout_s=20, state_dir=self.state)))
               for _ in range(asr.QUEUE_MAX + 3)]
        for t in ths:
            t.start()
            time.sleep(0.05)
        for t in ths:
            t.join()
        reasons = sorted(r.get("reason", "ok") for r in results)
        self.assertEqual(reasons.count("busy"), 2, reasons)
        self.assertEqual(reasons.count("ok"), asr.QUEUE_MAX + 1, reasons)

    def test_queue_wait_counts_against_timeout(self):
        self.fake_voxtype(text="ok", sleep="2")
        self.cli("engine", "voxtype")
        first = threading.Thread(target=asr.transcribe, args=(self.wav,), kwargs={"timeout_s": 10, "state_dir": self.state})
        first.start()
        time.sleep(0.2)
        r = asr.transcribe(self.wav, timeout_s=0.5, state_dir=self.state)
        first.join()
        self.assertEqual(r["reason"], "timeout")

    def test_engine_needs_init_and_valid_value(self):
        self.assertEqual(asr.cli_main(["engine", "sherpa"], state_dir=self.tmp / "nostate", out=lambda *_: None), 1)
        rc, _ = self.cli("engine", "bogus")
        self.assertEqual(rc, 2)

    def test_doctor_rows_shape(self):
        for eng in ("auto", "voxtype", "off"):
            self.cli("engine", eng)
            rows = asr.doctor_rows(self.state)
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), {"id", "status", "summary", "hint"})
            self.assertEqual(rows[0]["id"], "asr")
            self.assertIn(rows[0]["status"], ("ok", "warn"))     # optional feature: never a ✗
        self.cli("engine", "auto")
        self.assertIn("agentj asr install", asr.doctor_rows(self.state)[0]["hint"])

    def test_status_cli_json(self):
        rc, out = self.cli("status", "--json")
        self.assertEqual(rc, 0)
        s = json.loads(out)
        self.assertEqual(s["state"], "not_installed")
        self.assertIn("threads", s)

    def test_broken_install_detected(self):
        d = asr.asr_dir(self.state)
        (d / "model").mkdir(parents=True)
        (d / "venv" / "bin").mkdir(parents=True)
        (d / "venv" / "bin" / "python").write_text("")
        (d / "model" / "tokens.txt").write_text("short")
        (d / "installed.json").write_text(json.dumps({"v": 1, "sherpa_version": asr.SHERPA_VERSION}))
        s = asr.status(self.state)
        self.assertEqual(s["state"], "broken")
        self.assertEqual(asr.ready_state(self.state), "broken")
        self.assertEqual(asr.transcribe(self.wav, timeout_s=5, state_dir=self.state)["reason"], "engine_failed")
        self.assertEqual(asr.doctor_rows(self.state)[0]["status"], "warn")


# ------------------------------------------------------------------ install pieces (no network)
class _RangeHandler(http.server.BaseHTTPRequestHandler):
    blobs: dict = {}
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = self.blobs.get(self.path)
        rng = self.headers.get("Range")
        self.seen.append((self.path, rng))
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        start = 0
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Install(Tmp):
    def setUp(self):
        super().setUp()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        _RangeHandler.blobs, _RangeHandler.seen = {}, []

    def test_fetch_resumes_and_verifies(self):
        import hashlib
        data = os.urandom(300_000)
        _RangeHandler.blobs["/f"] = data
        dest = self.tmp / "f.bin"
        (self.tmp / "f.bin.part").write_bytes(data[:120_000])
        used = asr._fetch([self.base + "/f"], dest, len(data), hashlib.sha256(data).hexdigest(), lambda *_: None, "f")
        self.assertEqual(used, self.base + "/f")
        self.assertEqual(dest.read_bytes(), data)
        self.assertEqual(_RangeHandler.seen[-1], ("/f", "bytes=120000-"))
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertEqual(asr._fetch([], dest, len(data), hashlib.sha256(data).hexdigest(), lambda *_: None, "f"), "cache")

    def test_fetch_hash_mismatch_never_used_then_fallback(self):
        import hashlib
        good = b"good bytes" * 1000
        _RangeHandler.blobs.update({"/evil": b"evil bytes" * 1000, "/good": good})
        dest = self.tmp / "g.bin"
        used = asr._fetch([self.base + "/evil", self.base + "/missing", self.base + "/good"], dest, len(good),
                          hashlib.sha256(good).hexdigest(), lambda *_: None, "g")
        self.assertEqual(used, self.base + "/good")
        self.assertEqual(dest.read_bytes(), good)
        with self.assertRaises(asr.InstallError) as cm:
            asr._fetch([self.base + "/evil"], self.tmp / "h.bin", len(good), hashlib.sha256(good).hexdigest(),
                       lambda *_: None, "h")
        self.assertIn("SHA-256", str(cm.exception))
        self.assertFalse((self.tmp / "h.bin").exists())
        self.assertFalse((self.tmp / "h.bin.part").exists())
        with self.assertRaises(asr.InstallError):            # no plain http to anywhere but localhost
            asr._fetch(["http://example.com/x"], self.tmp / "i.bin", 1, "0" * 64, lambda *_: None, "i")

    def _tar(self, members):
        p = self.tmp / "m.tar.bz2"
        with tarfile.open(p, "w:bz2") as t:
            for name, kind, data in members:
                ti = tarfile.TarInfo(name)
                if kind == "sym":
                    ti.type, ti.linkname = tarfile.SYMTYPE, data
                    t.addfile(ti)
                else:
                    ti.size = len(data)
                    t.addfile(ti, io.BytesIO(data))
        return p

    LICENSE = b"Ref to https://github.com/modelscope/FunASR?tab=readme-ov-file#license\n"

    def test_safe_extract_only_pinned_regular_files(self):
        self.assertEqual(len(self.LICENSE), asr.FILES["LICENSE"][1])
        pre = asr.MODEL_ID + "/"
        tp = self._tar([("../../escape.txt", "file", b"x"), ("/abs.txt", "file", b"x"), (pre + "../up.txt", "file", b"x"),
                        (pre + "evil-link", "sym", "/etc/passwd"), (pre + "LICENSE", "file", self.LICENSE)])
        dest = self.tmp / "out"
        dest.mkdir()
        asr._safe_extract(tp, dest, ["LICENSE"])
        self.assertEqual(sorted(os.listdir(dest)), ["LICENSE"])
        self.assertFalse((self.tmp / "escape.txt").exists())
        self.assertFalse(pathlib.Path("/abs.txt").exists())

    def test_safe_extract_rejects_link_wrong_hash_missing(self):
        pre = asr.MODEL_ID + "/"
        for name, members in {
            "link": [(pre + "LICENSE", "sym", "/etc/passwd")],
            "hash": [(pre + "LICENSE", "file", self.LICENSE.replace(b"Ref", b"REF"))],
            "missing": [(pre + "README.md", "file", b"x")],
            "twice": [(pre + "LICENSE", "file", self.LICENSE), (pre + "LICENSE", "file", self.LICENSE)],
        }.items():
            with self.subTest(name):
                dest = self.tmp / f"o-{name}"
                dest.mkdir()
                with self.assertRaises(asr.InstallError):
                    asr._safe_extract(self._tar(members), dest, ["LICENSE"])

    def test_refuses_without_yes_off_terminal(self):
        lines = []
        with mock.patch.object(asr, "route_order", return_value=["github"]):
            rc = asr.install(self.state, "auto", yes=False, out=lines.append, inp=io.StringIO("y\n"))
        self.assertEqual(rc, 1)
        self.assertIn("--yes", "\n".join(lines))
        self.assertIn(f"{asr.download_mb('github')} MB", "\n".join(lines))
        self.assertFalse(asr.asr_dir(self.state).exists())

    def test_unsupported_platform_fallback_text(self):
        lines = []
        with mock.patch.object(asr, "platform_key", return_value=None):
            rc = asr.install(self.state, "auto", yes=True, out=lines.append)
        self.assertEqual(rc, 1)
        self.assertIn("agentj asr engine voxtype", "\n".join(lines))
        self.assertIn("听写", "\n".join(lines))

    def test_failed_install_leaves_nothing_half_installed(self):
        lines = []
        with mock.patch.object(asr, "ROUTES", {"github": {"pypi": [self.base + "/pypi/"], "tarball": self.base + "/none"}}), \
                mock.patch.dict(os.environ, {asr.DOWNLOADS_ENV: ""}):
            rc = asr.install(self.state, "github", yes=True, out=lines.append)
        self.assertEqual(rc, 1)
        d = asr.asr_dir(self.state)
        for sub in ("venv", "model", "model.staging", "installed.json"):
            self.assertFalse((d / sub).exists(), sub)
        self.assertIn("voxtype", "\n".join(lines))
        self.assertEqual(asr.status(self.state)["state"], "not_installed")
        self.assertTrue(asr.status(self.state)["last_error"])

    def test_wheel_table_complete(self):
        for osn, arch in (("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64"), ("darwin", "x86_64")):
            self.assertIn((osn, arch, "py3"), asr.WHEELS)
            for py in ("cp311", "cp312", "cp313", "cp314"):
                path, size, sha = asr.WHEELS[(osn, arch, py)]
                self.assertTrue(path.endswith(".whl") and f"-{py}-" in path and asr.SHERPA_VERSION in path)
                self.assertEqual(len(sha), 64)
        self.assertEqual(asr.download_mb("github", ("linux", "x86_64", "cp313")), 178)

    def test_route_order(self):
        with mock.patch.dict(os.environ, {"UV_DEFAULT_INDEX": "https://pypi.tuna.tsinghua.edu.cn/simple"}):
            self.assertEqual(asr.route_order("auto")[0], "hf-mirror")
        self.assertEqual(asr.route_order("modelscope"), ["modelscope"])
        with mock.patch.dict(os.environ, {"UV_DEFAULT_INDEX": "", "UV_INDEX_URL": "", "PIP_INDEX_URL": "", asr.DOWNLOADS_ENV: ""}), \
                mock.patch.object(asr, "_reachable", return_value=False):
            self.assertEqual(asr.route_order("auto")[0], "hf-mirror")
        with mock.patch.dict(os.environ, {"UV_DEFAULT_INDEX": "", "UV_INDEX_URL": "", "PIP_INDEX_URL": "", asr.DOWNLOADS_ENV: ""}), \
                mock.patch.object(asr, "_reachable", return_value=True):
            self.assertEqual(asr.route_order("auto"), ["github", "hf-mirror", "modelscope"])


# ------------------------------------------------------------------ the real engine
def _engine_state():
    """(state dir, owned) with an installed engine, or (None, reason)."""
    s = os.environ.get("AGENTJ_ASR_TEST_STATE")
    if s:
        return (pathlib.Path(s), False) if asr.status(s)["installed"] else (None, f"AGENTJ_ASR_TEST_STATE={s}: not installed")
    dl = os.environ.get(asr.DOWNLOADS_ENV)
    if not dl or not (pathlib.Path(dl) / asr.TARBALL["name"]).exists():
        return None, ("no engine: set AGENTJ_ASR_TEST_STATE=<state dir with `agentj asr install` done> or "
                      f"{asr.DOWNLOADS_ENV}=<folder with {asr.TARBALL['name']} + the two wheels>")
    if asr.platform_key() is None:
        return None, asr.no_platform_reason()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-asr-real-"))
    State(tmp / "state").init()
    lines = []
    rc = asr.install(tmp / "state", "github", yes=True, out=lines.append)
    if rc != 0:
        shutil.rmtree(tmp, True)
        raise RuntimeError("install from downloads failed: " + "\n".join(lines[-3:]))
    return tmp / "state", True


class RealEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state, cls.owned = _engine_state()
        if cls.state is None:
            raise unittest.SkipTest(cls.owned)
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-asr-clips-"))
        cls.zh = str(asr.asr_dir(cls.state) / "model" / "zh.wav")
        with wave.open(cls.zh) as f:
            cls.zh_pcm = f.readframes(f.getnframes())

    @classmethod
    def tearDownClass(cls):
        asr.shutdown()
        shutil.rmtree(cls.tmp, True)
        if cls.owned:
            shutil.rmtree(cls.state.parent, True)

    def tx(self, wav, timeout=60):
        return asr.transcribe(wav, timeout_s=timeout, state_dir=self.state)

    def test_a_zh(self):
        r = self.tx(self.zh)
        self.assertTrue(r["ok"], r)
        self.assertIn("开饭时间", r["text"])
        self.assertIn("下午5点", r["text"])
        self.assertEqual(r["engine"], "sherpa")
        self.assertNotIn("<|", r["text"])
        self.assertIsInstance(r["ms"], int)
        s = asr.status(self.state)
        self.assertEqual((s["state"], s["active"], s["sherpa_version"]), ("ready", "sherpa", asr.SHERPA_VERSION))
        self.assertEqual(asr.ready_state(self.state), "ready")
        self.assertTrue(s["worker"]["running"])
        self.assertEqual(asr.doctor_rows(self.state)[0]["status"], "ok")

    def test_b_en_from_tarball(self):
        dl = os.environ.get(asr.DOWNLOADS_ENV)
        tp = pathlib.Path(dl or "/nonexistent") / asr.TARBALL["name"]
        if not tp.exists():
            self.skipTest("en.wav comes from the model tarball in AGENTJ_ASR_DOWNLOADS")
        with tarfile.open(tp, "r|bz2") as t:
            for m in t:
                if m.name == asr.MODEL_ID + "/test_wavs/en.wav" and m.isreg():
                    (self.tmp / "en.wav").write_bytes(t.extractfile(m).read())
                    break
        r = self.tx(str(self.tmp / "en.wav"))
        self.assertTrue(r["ok"], r)
        self.assertIn("tribal chieftain", r["text"].lower())

    def test_c_48k_stereo_resampled(self):
        a = array.array("h", self.zh_pcm)
        st = array.array("h", bytes(len(a) * 12))
        for c in range(6):
            st[c::6] = a
        r = self.tx(write_wav(self.tmp / "zh48.wav", st.tobytes(), 48000, 2))
        self.assertTrue(r["ok"], r)
        self.assertIn("开饭时间", r["text"])

    def test_d_silence_is_no_speech(self):
        r = self.tx(write_wav(self.tmp / "sil.wav", silence(2)))
        self.assertEqual(r.get("reason"), "no_speech", r)

    def test_e_worker_reused_then_crash_recovers(self):
        self.tx(self.zh)
        w = asr._RESIDENTS[str(asr.asr_dir(self.state))]
        pid = w.proc.pid
        self.assertTrue(self.tx(self.zh)["ok"])
        self.assertEqual(w.proc.pid, pid)                       # model loaded once
        os.kill(pid, 9)
        time.sleep(0.2)
        r = self.tx(self.zh)
        self.assertTrue(r["ok"], r)                             # a crash costs one reload, not serve
        self.assertNotEqual(w.proc.pid, pid)

    def test_f_timeout_then_next_take_works(self):
        long = write_wav(self.tmp / "long.wav", (self.zh_pcm + silence(0.5)) * 6)
        self.tx(self.zh)                                        # warm
        r = self.tx(long, timeout=0.05)
        self.assertEqual(r.get("reason"), "timeout", r)
        r = self.tx(self.zh)
        self.assertTrue(r["ok"], r)

    def test_g_idle_exit_and_respawn(self):
        asr.shutdown()
        with mock.patch.object(asr, "IDLE_S", 1):
            self.assertTrue(self.tx(self.zh)["ok"])
            w = asr._RESIDENTS[str(asr.asr_dir(self.state))]
            p = w.proc
            p.wait(timeout=10)                                  # it leaves by itself, freeing the memory
            self.assertEqual(p.returncode, 0)
            self.assertTrue(self.tx(self.zh)["ok"])
        asr.shutdown()

    def test_h_60s_take(self):
        clip = (self.zh_pcm + silence(0.6)) * 10                # ≈ 62 s: pieces cut at the pauses
        r = self.tx(write_wav(self.tmp / "m60.wav", clip[:60 * 2 * RATE]))
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(r["text"].count("9点"), 8)

    def test_i_cli_test_and_status(self):
        lines = []
        self.assertEqual(asr.cli_main(["test"], state_dir=self.state, out=lines.append), 0)
        self.assertIn("开饭时间", "\n".join(lines))
        lines = []
        self.assertEqual(asr.cli_main(["status"], state_dir=self.state, out=lines.append), 0)
        self.assertIn("sherpa-onnx " + asr.SHERPA_VERSION, "\n".join(lines))
        self.assertEqual(asr.cli_main(["install", "--yes"], state_dir=self.state, out=lines.append), 0)   # idempotent

    def test_z_remove(self):
        if not self.owned:
            self.skipTest("never removes an engine this test did not install")
        lines = []
        self.assertEqual(asr.cli_main(["remove"], state_dir=self.state, out=lines.append), 0)
        self.assertFalse(asr.asr_dir(self.state).exists())
        self.assertEqual(asr.status(self.state)["state"], "not_installed")
        self.assertEqual(asr.settings(self.state)["engine"], "auto")


if __name__ == "__main__":
    unittest.main()
