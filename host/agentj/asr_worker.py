"""Resident speech-to-text worker (PROTOCOL §10.9). Started by `agentj.asr` as
`<state>/asr/venv/bin/python -I <this file> --model <dir> --threads N --idle S`, outside the fence.

Standard library + `sherpa_onnx` only: it runs in the ASR venv, where agentj itself is not installed. `agentj.asr` imports
this module too (for `parse_wav` / `clean_text`), so the WAV rules live in one place; nothing here imports sherpa_onnx
at module level.

Wire: one JSON object per line. After loading the model once it prints `{"t":"ready","load_ms":n,"version":v}` (or
`{"t":"fatal","detail":…}` and exits 2). Then per request `{"id":n,"wav":"<path>"}` → `{"id":n,"ok":true,"text":…,
"ms":n,"audio_ms":n}` or `{"id":n,"ok":false,"why":"bad_audio"|"engine_failed","detail":…}`; `{"id":n,"op":"ping"}` →
`{"id":n,"ok":true,"rss_kb":n}`. It exits on EOF and after `--idle` seconds without a request. Transcripts are never
written anywhere but stdout.
"""
from __future__ import annotations

import argparse
import array
import json
import os
import re
import select
import struct
import sys
import time

RATE = 16000
MAX_SECONDS = 630           # a take is ≤ 10 min (relay LOCK_MAX_MS); a little slack for the WAV writer
PAUSE_S = 0.4               # a pause this long separates two pieces (segments())
PIECE_MAX_S = 20            # no piece longer than this: cut at the quietest 300 ms between PIECE_MIN_S and PIECE_MAX_S
PIECE_MIN_S = 8
PIECE_JOIN_S = 12           # neighbouring pieces are joined while together ≤ this
WHOLE_S = 20                # a take up to this long is decoded whole
MIN_SPEECH_FRAMES = 10      # a piece with < 100 ms above the silence line is not decoded
SILENCE_ABS = 0.003        # mean |sample| of a 10 ms frame below this is silence whatever the noise floor (≈ −50 dBFS)
PCM_GUID_TAIL = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"   # KSDATAFORMAT_SUBTYPE_PCM after its 2-byte tag
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TAG = re.compile(r"<\|[^|<>]{0,32}\|>")   # SenseVoice language / emotion / event / ITN tokens, e.g. <|zh|><|NEUTRAL|>


class BadAudio(ValueError):
    pass


def parse_wav(path: str, strict: bool = False) -> dict:
    """Read only the header. Returns {"rate","channels","offset","frames","seconds"}; raises BadAudio(reason).

    Always: RIFF/WAVE, the RIFF size equals the file size, exactly one `fmt ` (PCM 16-bit: format 1, or 0xFFFE with the PCM
    sub-format), exactly one `data` chunk that ends where the file ends (other chunks such as LIST may precede it), whole
    frames, ≤ 10.5 min. `strict` (PROTOCOL §10.9): also 1 channel, 16 000 Hz and the data chunk directly after `fmt `.
    Otherwise 1–2 channels at 8–48 kHz are accepted and converted by the worker (mono average, resampling in Python)."""
    try:
        size = os.path.getsize(path)
        f = open(path, "rb")
    except OSError as e:
        raise BadAudio(f"cannot read: {e.strerror or e}") from None
    with f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise BadAudio("not a RIFF/WAVE file")
        if struct.unpack("<I", head[4:8])[0] != size - 8:
            raise BadAudio("RIFF size does not match the file length")
        fmt = None
        pos = 12
        chunks = []
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise BadAudio("no data chunk")
            cid, clen = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            chunks.append(cid)
            body = pos + 8
            if cid == b"data":
                if fmt is None:
                    raise BadAudio("data chunk before fmt chunk")
                if body + clen != size:
                    raise BadAudio("data chunk length does not match the file")
                break
            if cid == b"fmt ":
                if fmt is not None:
                    raise BadAudio("two fmt chunks")
                if clen not in (16, 18, 40):
                    raise BadAudio("odd fmt chunk")
                fmt = f.read(clen)
                if len(fmt) != clen:
                    raise BadAudio("truncated fmt chunk")
            else:
                if body + clen + (clen & 1) > size:
                    raise BadAudio("truncated chunk")
                f.seek(clen + (clen & 1), 1)
            pos = body + clen + (clen & 1)
    tag, ch, rate, brate, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE:
        if len(fmt) != 40 or fmt[26:40] != PCM_GUID_TAIL or fmt[24:26] != b"\x01\x00":
            raise BadAudio("not PCM")
    elif tag != 1:
        raise BadAudio(f"not PCM (format {tag})")
    if bits != 16:
        raise BadAudio(f"{bits}-bit samples (need 16-bit)")
    if ch not in (1, 2) or not 8000 <= rate <= 48000 or align != ch * 2 or brate != rate * align:
        raise BadAudio(f"unsupported layout ({ch} ch, {rate} Hz)")
    if strict and (ch != 1 or rate != RATE or tag != 1 or chunks != [b"fmt ", b"data"]):
        raise BadAudio("not 16 kHz mono PCM16")
    if clen % align:
        raise BadAudio("partial sample frame")
    frames = clen // align
    seconds = frames / rate
    if seconds > MAX_SECONDS:
        raise BadAudio(f"too long ({seconds:.0f} s > {MAX_SECONDS} s)")
    return {"rate": rate, "channels": ch, "offset": size - clen, "frames": frames, "seconds": seconds}


def load_samples(path: str) -> array.array:
    """16 kHz mono float32 samples in [-1, 1) as array('f'). Memory stays ≈ 6 bytes per input sample (no Python lists)."""
    info = parse_wav(path)
    with open(path, "rb") as f:
        f.seek(info["offset"])
        raw = f.read()
    pcm = array.array("h")
    pcm.frombytes(raw)
    del raw
    if sys.byteorder == "big":
        pcm.byteswap()
    if info["channels"] == 2:
        pcm = array.array("h", map(lambda a, b: (a + b) >> 1, pcm[0::2], pcm[1::2]))
    if info["rate"] != RATE:
        return resample(pcm, info["rate"], RATE)
    return array.array("f", map((1.0 / 32768.0).__mul__, pcm))


def resample(pcm, src: int, dst: int) -> array.array:
    """→ array('f') at dst. An integer ratio (32 / 48 kHz → 16 kHz) averages each block of src/dst samples; any other
    ratio (8 / 11.025 / 22.05 / 44.1 kHz) interpolates linearly between box-averaged neighbours (width int(src/dst) when
    downsampling). Enough for speech into an ASR whose features stop at 8 kHz. Pure Python, in the worker only:
    measured ≈ 0.08 s per 5.6 s of 48 kHz stereo (i7-12800H)."""
    k = 1.0 / 32768.0
    n = len(pcm)
    out = array.array("f")
    if n == 0:
        return out
    if src % dst == 0:
        w = src // dst
        inv = k / w
        out.extend(sum(pcm[j:j + w]) * inv for j in range(0, n - w + 1, w))
        return out
    ratio = src / dst
    w = max(1, int(ratio))
    half = w // 2

    def box(i):
        lo, hi = max(0, i - half), min(n, i - half + w)
        return sum(pcm[lo:hi]) / (hi - lo)

    last = n - 1
    for j in range(int(n / ratio)):
        p = j * ratio
        i = int(p)
        if i >= last:
            out.append(box(last) * k)
            continue
        a = box(i) if w > 1 else pcm[i]
        b = box(i + 1) if w > 1 else pcm[i + 1]
        out.append((a + (b - a) * (p - i)) * k)
    return out


def segments(samples, rate: int = RATE) -> list[tuple[int, int]]:
    """Speech pieces to decode one by one: an energy VAD on 10 ms frames. SenseVoice is trained on short utterances —
    measured: a 60 s take decoded whole, or in 25 s pieces, drops and garbles words; cut at the pauses it is exact. So:
    cut in the middle of every pause ≥ PAUSE_S, cut a piece longer than PIECE_MAX_S at its quietest 300 ms, and skip a
    piece that is silence only (no decode, no made-up words). Short takes (≤ PIECE_MAX_S, no pause) stay whole."""
    n = len(samples)
    hop = rate // 100
    if n < hop:
        return []
    energy = [sum(map(abs, samples[i:i + hop])) / hop for i in range(0, n - hop + 1, hop)]
    srt = sorted(energy)
    floor, p95 = srt[len(srt) // 10], srt[min(len(srt) - 1, len(srt) * 95 // 100)]
    thr = min(max(SILENCE_ABS, floor * 3), max(p95 * 0.25, SILENCE_ABS))
    loud = [e >= thr for e in energy]
    if sum(loud) < MIN_SPEECH_FRAMES:
        return []                                 # silence only: SenseVoice would invent a word ("그.")
    if n <= WHOLE_S * rate:
        return [(0, n)]                           # a short take decodes best whole (zh.wav exact only this way)
    cuts, run = [], 0
    pause = int(PAUSE_S * 100)
    for i, is_loud in enumerate(loud + [True]):
        if not is_loud:
            run += 1
            continue
        if run >= pause:
            cuts.append(i - run // 2)            # middle of the pause (frame index)
        run = 0
    bounds = [0, *cuts, len(energy)]
    pieces = []
    smooth = 30                                   # 300 ms window for "quietest point"
    for a, b in zip(bounds, bounds[1:]):
        while b - a > PIECE_MAX_S * 100:
            lo, hi = a + PIECE_MIN_S * 100, a + PIECE_MAX_S * 100
            acc = [sum(energy[j:j + smooth]) for j in range(lo, hi - smooth)]
            cut = lo + acc.index(min(acc)) + smooth // 2
            pieces.append((a, cut))
            a = cut
        pieces.append((a, b))
    merged = []                                   # tiny pieces alone are guessed at ("Yeah.", "Okay."): join neighbours
    for a, b in pieces:
        if merged and b - merged[-1][0] <= PIECE_JOIN_S * 100:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    out = []
    for a, b in merged:
        if sum(loud[a:b]) >= MIN_SPEECH_FRAMES:
            out.append((a * hop, n if b >= len(energy) else b * hop))
    return out


_CJK_END = re.compile(r"[　-〿㐀-鿿가-힯＀-￯]$")
_CJK_START = re.compile(r"^[　-〿㐀-鿿가-힯＀-￯]")


def clean_text(s: str) -> str:
    """Drop SenseVoice's <|…|> tokens and collapse whitespace."""
    return " ".join(TAG.sub(" ", s or "").split())


def join_texts(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not p:
            continue
        if out and not (_CJK_END.search(out) and _CJK_START.search(p)):
            out += " "
        out += p
    return out


class Engine:
    def __init__(self, model_dir: str, threads: int, language: str = "auto"):
        import sherpa_onnx  # only in the ASR venv
        self.version = getattr(sherpa_onnx, "__version__", "?")
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(model_dir, "model.int8.onnx"), tokens=os.path.join(model_dir, "tokens.txt"),
            num_threads=threads, use_itn=True, language=language, debug=False)

    def decode(self, samples) -> str:
        texts = []
        for a, b in segments(samples):
            if b - a < RATE // 10:
                continue
            s = self.rec.create_stream()
            s.accept_waveform(RATE, samples[a:b])
            self.rec.decode_stream(s)
            texts.append(clean_text(s.result.text))
        return join_texts(texts)


def _rss_kb() -> int:
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v // 1024 if sys.platform == "darwin" else v
    except Exception:  # noqa: BLE001
        return 0


def _say(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(engine: Engine, req: dict) -> dict:
    rid = req.get("id")
    if req.get("op") == "ping":
        return {"id": rid, "ok": True, "rss_kb": _rss_kb()}
    path = req.get("wav")
    if not isinstance(path, str):
        return {"id": rid, "ok": False, "why": "bad_audio", "detail": "no path"}
    try:
        samples = load_samples(path)
    except BadAudio as e:
        return {"id": rid, "ok": False, "why": "bad_audio", "detail": str(e)}
    except (OSError, MemoryError) as e:
        return {"id": rid, "ok": False, "why": "bad_audio", "detail": type(e).__name__}
    t0 = time.monotonic()
    try:
        text = engine.decode(samples)
    except Exception as e:  # noqa: BLE001 — the engine's own failure, reported, never fatal for the worker
        return {"id": rid, "ok": False, "why": "engine_failed", "detail": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"id": rid, "ok": True, "text": text, "ms": int((time.monotonic() - t0) * 1000),
            "audio_ms": int(len(samples) * 1000 / RATE)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="asr_worker")
    ap.add_argument("--model", required=True)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--idle", type=float, default=600.0)
    ap.add_argument("--language", default="auto")
    ap.add_argument("--nice", type=int, default=5)
    a = ap.parse_args(argv)
    if a.nice:
        try:
            os.nice(a.nice)          # a long take must not make the human's computer feel slow
        except OSError:
            pass
    t0 = time.monotonic()
    try:
        engine = Engine(a.model, max(1, min(a.threads, 8)), a.language)
    except Exception as e:  # noqa: BLE001
        _say({"t": "fatal", "detail": f"{type(e).__name__}: {str(e)[:300]}"})
        return 2
    _say({"t": "ready", "load_ms": int((time.monotonic() - t0) * 1000), "version": engine.version, "pid": os.getpid()})
    buf = b""
    fd = sys.stdin.fileno()
    while True:
        r, _, _ = select.select([fd], [], [], a.idle)
        if not r:
            return 0                                   # idle: free the ≈ 400 MB until the next take
        chunk = os.read(fd, 65536)
        if not chunk:
            return 0                                   # parent went away
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError
            except ValueError:
                _say({"id": None, "ok": False, "why": "engine_failed", "detail": "bad request"})
                continue
            _say(handle(engine, req))


if __name__ == "__main__":
    sys.exit(main())
