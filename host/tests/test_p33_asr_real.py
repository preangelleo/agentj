"""serve ↔ the REAL local speech-to-text engine (PROTOCOL §10.9, PROMPT-33 integration): an `asr` blob → `asr_res` with
SenseVoice's own words, a long recording attached to a `say` → its transcript in the Agent's message, the per-device queue
(≤ 3 in flight, then `busy`; results in recording order; another device not held back), and every `why` a phone can get.

The engine is installed offline from AGENTJ_ASR_DOWNLOADS (the model tarball + the two pinned wheels — same code path as
`agentj asr install` after the download) into a temp state once per run, or taken from AGENTJ_ASR_TEST_STATE; without
either the class is skipped with that reason. Audio: the model's own test WAV (`zh.wav`), cut and repeated with pauses,
and digital silence — never a person's voice of ours.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import hashlib
import os
import pathlib
import shutil
import sys
import unittest
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import asr, doctor, serve, uploads, wire  # noqa: E402

from test_asr import _engine_state, silence, write_wav  # noqa: E402
from test_l1 import Phone  # noqa: E402
from test_p33 import b64  # noqa: E402
from test_p33_chain import _Chain, ready33  # noqa: E402

_ENGINE: dict = {}


def setUpModule():
    try:
        st, owned = _engine_state()
    except RuntimeError as e:           # the offline install itself failed: a real failure, not a skip
        _ENGINE["error"] = str(e)
        return
    _ENGINE.update(state=st, owned=owned)


def tearDownModule():
    asr.shutdown()
    if _ENGINE.get("owned") and _ENGINE.get("state"):
        shutil.rmtree(_ENGINE["state"].parent, True)


def wav_bytes(path) -> bytes:
    return pathlib.Path(path).read_bytes()


class RealEngineServe(_Chain):
    KIND = "claude"

    def setUp(self):
        if _ENGINE.get("error"):
            self.fail(_ENGINE["error"])
        if not _ENGINE.get("state"):
            self.skipTest(f"no engine: set AGENTJ_ASR_TEST_STATE or {asr.DOWNLOADS_ENV}")
        super().setUp()
        self.engine_dir = asr.asr_dir(_ENGINE["state"])
        os.symlink(self.engine_dir, self.st.root / "asr")          # this host's <state>/asr = the installed engine
        zh = self.engine_dir / "model" / "zh.wav"
        with wave.open(str(zh)) as f:
            self.assertEqual((f.getframerate(), f.getnchannels(), f.getsampwidth()), (16000, 1, 2))
            self.zh_pcm = f.readframes(f.getnframes())
        self.clips = pathlib.Path(self.tmp.name) / "clips"
        self.clips.mkdir()
        self.take_ttl = serve.ASR_TAKE

    def tearDown(self):
        serve.ASR_TAKE = self.take_ttl
        asr.shutdown()
        super().tearDown()

    def clip(self, name, pcm) -> bytes:
        return wav_bytes(write_wav(self.clips / name, pcm))

    @staticmethod
    async def upload_as(c, s, data, purpose="asr", origin="recording", secs=None, name="take.wav", mime="audio/wav"):
        bid = wire.b64u(os.urandom(16))
        await c["host"]._app(s, {"t": "blob_open", "bid": bid, "purpose": purpose, "name": name, "mime": mime,
                                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "origin": origin,
                                 **({"secs": secs} if secs else {})})
        for o in range(0, len(data), uploads.CHUNK):
            await c["host"]._app(s, {"t": "blob_chunk", "bid": bid, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
        await c["host"]._app(s, {"t": "blob_end", "bid": bid})
        return bid

    @staticmethod
    def res_of(c, bid):
        r = [o for o in c["of"]("asr_res") if o["bid"] == bid]
        return r[-1] if r else None

    # ------------------------------------------------------------ asr-local
    def test_take_and_long_recording_transcribed_on_this_computer(self):
        zh = self.clip("zh.wav", self.zh_pcm)
        long = self.clip("long.wav", (self.zh_pcm + silence(0.6)) * 5)           # ≈ 31 s: a long take, cut at the pauses

        async def script(c):
            self.assertEqual(c["host"]._caps()["asr"], "ready", "ready.asr comes from the real engine")
            bid = await c["upload"](zh, mime="audio/wav", purpose="asr", origin="recording", secs=5.6, name="zh.wav")
            await c["wait"](lambda: self.res_of(c, bid), ms=60000)
            r = self.res_of(c, bid)
            self.assertTrue(r["ok"], r)
            self.assertIn("开饭时间", r["text"])
            self.assertIn("下午5点", r["text"])
            self.assertEqual(r["engine"], "sherpa")
            self.assertIsInstance(r["ms"], int)
            self.assertNotIn("<|", r["text"])
            self.assertFalse(list((self.st.root / "uploads").rglob("*.part")), "the take is deleted after")
            inbox = self.work / ".agentj" / "inbox"
            self.assertFalse(list(inbox.rglob("*.wav")) if inbox.exists() else [], "an asr take never reaches the inbox")
            # a long recording rides along as an attachment and is transcribed at send time
            att = await c["upload"](long, mime="audio/wav", purpose="att", origin="recording", secs=31, name="长语音.wav")
            sid = await c["say"]("听这段", att=[att])
            await c["wait"](lambda: any(o["sid"] == sid and o["s"] == "transcribing" for o in c["of"]("say_state")))
            await c["wait"](lambda: c["res"](sid) is not None)
            turn = c["res"](sid)["turn"]
            await c["wait"](lambda: c["turns"]().get(turn, {}).get("end") == "done", ms=60000)
            rep = c["turns"]()[turn]["reply"]["text"]
            self.assertIn("语音转写（31 秒，", rep)
            self.assertGreaterEqual(rep.count("9点"), 4, rep)
            files = list(inbox.rglob("*.wav"))
            self.assertEqual(len(files), 1, "the recording itself stays in the Agent's inbox")
            self.assertEqual(files[0].read_bytes(), long)
            self.assertIn(str(files[0]), rep)
            self.assertTrue(any(o["sid"] == sid and o["s"] == "delivered" for o in c["of"]("say_state")))
        self.run_chain(script)
        row = doctor.check_asr(self.st)
        self.assertEqual((len(row), row[0]["id"], row[0]["status"]), (1, "asr", "ok"), row)
        log = self.st.log_path.read_text()
        self.assertNotIn("开饭", log, "no transcript in the host log (Invariant 5)")

    # ------------------------------------------------------------ asr-queue
    def test_queue_per_device_order_and_busy(self):
        take = self.clip("q.wav", (self.zh_pcm + silence(0.4)) * 3)             # ≈ 18 s each, ~0.7 s to decode

        async def script(c):
            ph2 = Phone(self.st, "第二台")
            s2 = ready33(c["host"], ph2, cid=12)
            bids = [await self.upload_as(c, c["s"], take, secs=18, name=f"t{i}.wav") for i in range(5)]
            other = await self.upload_as(c, s2, take, secs=18, name="other.wav")
            await c["wait"](lambda: all(self.res_of(c, b) for b in bids + [other]), ms=90000)
            rs = [self.res_of(c, b) for b in bids]
            self.assertEqual([r["ok"] for r in rs], [True, True, True, False, False], rs)
            self.assertEqual([r.get("why") for r in rs[3:]], ["busy", "busy"], "≤ 3 takes in flight per device")
            self.assertTrue(self.res_of(c, other)["ok"], "another device is not held back by this one's queue")
            order = [o["bid"] for o in c["of"]("asr_res") if o.get("ok") and o["bid"] in bids]
            self.assertEqual(order, bids[:3], "transcribed in recording order (FIFO)")
            for r in rs[:3]:
                self.assertGreaterEqual(r["text"].count("9点"), 2, r)   # the model's words (「开饭/开放时间」 varies by context)
            # after the queue drained, the same device is served again
            again = await self.upload_as(c, c["s"], take, secs=18, name="again.wav")
            await c["wait"](lambda: self.res_of(c, again), ms=60000)
            self.assertTrue(self.res_of(c, again)["ok"])
            self.assertFalse(list((self.st.root / "uploads").rglob("*.part")), "busy takes are deleted too")
        self.run_chain(script)

    # ------------------------------------------------------------ asr-errors
    def test_every_why_from_the_real_engine(self):
        zh = self.clip("zh.wav", self.zh_pcm)
        sil = self.clip("sil.wav", silence(2))
        stereo = wav_bytes(write_wav(self.clips / "st.wav", self.zh_pcm * 2, 16000, 2))
        hz48 = wav_bytes(write_wav(self.clips / "48k.wav", self.zh_pcm, 48000, 1))
        link = self.st.root / "asr"

        async def script(c):
            async def take(data, name):
                bid = await self.upload_as(c, c["s"], data, name=name, secs=2)
                await c["wait"](lambda: self.res_of(c, bid), ms=60000)
                return self.res_of(c, bid)
            # bad_audio: the protocol format is strict (16 kHz mono) even though the engine could resample
            self.assertEqual((await take(stereo, "st.wav"))["why"], "bad_audio")
            self.assertEqual((await take(hz48, "48k.wav"))["why"], "bad_audio")
            # no_speech: silence is never decoded into an invented word
            self.assertEqual((await take(sil, "sil.wav"))["why"], "no_speech")
            # timeout: the take is over its time → the next one still works
            serve.ASR_TAKE = 0.01
            self.assertEqual((await take(zh, "slow.wav"))["why"], "timeout")
            serve.ASR_TAKE = self.take_ttl
            self.assertTrue((await take(zh, "ok.wav"))["ok"])
            # off: ready.asr off, a new asr blob refused at open; a long recording goes untranscribed
            asr._set_setting(self.st.root, engine="off")
            self.assertEqual(c["host"]._caps()["asr"], "off")
            bid = wire.b64u(os.urandom(16))
            await c["host"]._app(c["s"], {"t": "blob_open", "bid": bid, "purpose": "asr", "name": "x.wav",
                                          "mime": "audio/wav", "size": len(zh), "sha256": hashlib.sha256(zh).hexdigest(),
                                          "origin": "recording"})
            self.assertEqual([o["why"] for o in c["of"]("blob_err") if o["bid"] == bid], ["asr_off"])
            att = await c["upload"](zh, mime="audio/wav", purpose="att", origin="recording", secs=6, name="off.wav")
            r, t = await c["said"]("关了转写", att=[att])
            self.assertIn("转写失败（语音转写已关闭）", t["reply"]["text"])
            asr._set_setting(self.st.root, engine="auto")
            self.assertEqual(c["host"]._caps()["asr"], "ready")
            # broken: an install whose model changed (here: the venv is gone) → broken, never a crash
            os.unlink(link)
            (link / "model").mkdir(parents=True)
            (link / "installed.json").write_text('{"sherpa_version": "1.13.8"}')
            self.assertEqual(c["host"]._caps()["asr"], "broken")
            self.assertEqual((await take(zh, "broken.wav"))["why"], "broken")
            # not_installed: nothing there
            shutil.rmtree(link)
            self.assertEqual(c["host"]._caps()["asr"], "not_installed")
            self.assertEqual((await take(zh, "none.wav"))["why"], "not_installed")
            att = await c["upload"](zh, mime="audio/wav", purpose="att", origin="recording", secs=6, name="none.wav")
            r, t = await c["said"]("没装", att=[att])
            self.assertIn("转写失败（电脑上还没装语音转写）", t["reply"]["text"])
        self.run_chain(script)


if __name__ == "__main__":
    unittest.main()
