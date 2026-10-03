# Local speech-to-text (`agentj asr`, PROTOCOL §10.9)

The phone records; **the customer's computer** transcribes. No audio and no text leave the machine; the only network
traffic is the one-time download below, every byte of it checked against a SHA-256 pinned in `agentj/asr.py`.

## Engine and licences

- **sherpa-onnx 1.13.8** (k2-fsa, Apache-2.0): the `sherpa-onnx` + `sherpa-onnx-core` wheels, pinned for Linux x86_64 /
  aarch64 (manylinux2014, glibc ≥ 2.17) and macOS arm64 (11+) / x86_64 (10.15+), CPython 3.11–3.14. No numpy, no ffmpeg,
  no GPU. musl (Alpine), Windows (use WSL2), other CPUs: no wheel → the install says so and offers the fallbacks.
- **SenseVoice-Small int8, 2024-07-17** (`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`): Chinese, English,
  Japanese, Korean, Cantonese. Model by **FunAudioLLM / Alibaba Tongyi Lab — SenseVoice**
  (https://github.com/FunAudioLLM/SenseVoice), converted to ONNX by k2-fsa (sherpa-onnx). Licence: **FunASR Model Open
  Source License 1.1** (https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) — use, modification and
  redistribution allowed, **attribution required**: keep the model's name and its authors' names (this paragraph; the
  model folder keeps its `LICENSE` and `README.md`). Not the `…-int8-2025-09-09` build: that is a Cantonese fine-tune.

## Commands

| command | what it does |
|---|---|
| `agentj asr install [--mirror auto\|github\|hf-mirror\|modelscope] [--yes]` | shows size + sources, asks y/N (no terminal and no `--yes` → refuses), downloads (resumable), verifies, builds `<state>/asr/venv` with agentj's own Python (`python -m venv`; uv as fallback) and installs the two local wheels with `pip --no-index --no-deps --require-hashes`, extracts the model safely, self-tests, sets `engine: sherpa` |
| `agentj asr status [--json]` | ready / not_installed / off / broken, engine, sizes, threads, worker |
| `agentj asr test [file.wav]` | transcribes the file (default: the model's own `zh.wav`) |
| `agentj asr engine auto\|sherpa\|voxtype\|off` | `config.json` `asr.engine`. `auto` (default) = sherpa when installed. **voxtype is never chosen automatically**: it follows the human's own voxtype settings, which may name a remote service |
| `agentj asr remove` | deletes `<state>/asr` (model + venv + downloads); engine back to `auto` |

Layout: `<state>/asr/{venv/, model/{model.int8.onnx,tokens.txt,LICENSE,README.md,zh.wav}, installed.json, worker.log,
dl/ (partial downloads, kept only after a failed install so a rerun resumes)}`, 0700 / 0600. In the state dir because the
host runs this code outside the fence and the fenced Agent cannot see or write the state dir (Invariant 14); not in
agentj's own venv because every upgrade (`uv tool install --force`) recreates that venv.

## Sources and mirrors (`--mirror`; `auto` = GitHub unless `UV_DEFAULT_INDEX` / `PIP_INDEX_URL` is a mainland mirror or github.com does not answer in 5 s, then the others as fallbacks)

| route | wheels | model | download |
|---|---|---|---|
| `github` | files.pythonhosted.org | GitHub release `asr-models` tarball (163.0 MB, sha256 `7d1efa21…347e`), only the 5 pinned files extracted | **178 MB** |
| `hf-mirror` | pypi.tuna.tsinghua.edu.cn, then mirrors.aliyun.com | hf-mirror.com `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` @ `2365baea`, single files | 255 MB |
| `modelscope` | mirrors.aliyun.com, then tuna | modelscope.cn `pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue`, single files (no sample WAV: the self-test only loads + decodes silence) | 255 MB |

Byte identity checked 2026-10-03: the tuna and aliyun copies of the wheel hash-match PyPI; `model.int8.onnx`,
`tokens.txt`, `LICENSE` (and on HF also `README.md`, `test_wavs/zh.wav`) hash-match the GitHub tarball's members.
All three routes were installed end to end on this machine (hf-mirror.com answered with a redirect to huggingface.co
from outside the mainland; the bytes are checked either way). `AGENTJ_ASR_DOWNLOADS=<folder>`: take already-downloaded
artifacts from a folder (offline install, tests) — still hash-checked.

Safe extraction: the tarball is streamed; only members whose name is exactly `<model id>/<pinned path>`, a regular file
of the pinned size, are copied to a fixed file name we choose (no path from the archive is ever used to write; links,
`..`, absolute names and everything else are skipped), each SHA-256-checked. Any failure removes `venv/` and `model/`
(nothing half-installed) and prints the two alternatives (voxtype; the phone keyboard's dictation).

## Runtime

A resident worker `<state>/asr/venv/bin/python -I agentj/asr_worker.py` (stdlib + sherpa_onnx only; JSON lines on
stdin/stdout; `nice +5`) loads the model once and exits after 10 idle minutes or when serve goes away (stdin EOF). One
take is decoded at a time per host; up to 3 more wait, beyond that `busy`; the caller's timeout covers the wait. A decode
past its timeout kills the worker (the next take reloads in 0.6 s); a crash costs a reload, never `serve`.

Input: RIFF/WAVE PCM16, checked header-only before anything runs (`asr_worker.parse_wav`): RIFF size = file size, one
`fmt ` (format 1, or 0xFFFE with the PCM sub-format), one `data` chunk ending at the end of the file, whole frames,
≤ 10.5 min. 16 kHz mono is the protocol format (`strict=True` checks exactly that); 8–48 kHz mono/stereo PCM16 is also
accepted and converted in the worker in pure Python (stereo → mean; integer ratios block-averaged, others linearly
interpolated after a box filter) — 3.8 s for 10 minutes of 48 kHz stereo.

Long takes: SenseVoice is trained on short utterances. Measured: a 60 s take decoded whole, or cut every 25 s, drops and
garbles words. So a take > 20 s is cut in the middle of pauses ≥ 0.4 s (10 ms energy VAD, noise-floor threshold), pieces
joined up to 12 s, none longer than 20 s; silence-only pieces (and silence-only takes) are not decoded — SenseVoice
otherwise invents a word ("그.", "Okay."). `<|zh|><|NEUTRAL|><|Speech|><|withitn|>`-style tokens are stripped.

## Measured (2026-10-03, i7-12800H, Linux x86_64, Python 3.13, the model's own test WAVs)

| | 1 thread | 2 threads (default = min(2, CPUs)) |
|---|---|---|
| model load | 0.62–0.65 s | 0.65–0.74 s |
| `zh.wav` 5.6 s | 0.37 s (RTF 0.065) 「开饭时间早上9点至下午5点。」 exact | 0.22 s (RTF 0.040) |
| `en.wav` 7.2 s | 0.47 s (RTF 0.066) | 0.28 s (RTF 0.040) |
| 60 s (zh ×10 with pauses) | 3.9 s (RTF 0.065) | 2.4 s (RTF 0.040) |
| 60 s (en ×8 with pauses) | 3.8 s (RTF 0.063) | 2.7 s (RTF 0.045) |
| 600 s (10-min cap) | 37.9 s (RTF 0.063) | 24.3 s (RTF 0.040) |

Install: 178 MB download (GitHub route), **295 MB on disk** (model 240 MB + venv 55 MB), 13–17 s end to end on a fast
line (ModelScope route 56 s). Memory: **≈ 470 MB peak RSS** for 16 kHz takes up to 10 min at 1 or 2 threads (670 MB for a
10-min 48 kHz stereo file), freed when the worker idles out. A slower CPU (2018 dual-core) is untested: the 60 s
per-take timeout bounds it.

## API for serve (`agentj.asr`)

- `transcribe(wav_path, *, timeout_s, state_dir=None)` → `{"ok": True, "text", "engine": "sherpa"|"voxtype", "ms"}` or
  `{"ok": False, "reason", "detail"}`; reason ∈ `not_installed off bad_audio timeout busy engine_failed no_speech`;
  `why(reason)` gives the `asr_res` word (`engine_failed` → `broken`, the rest unchanged). Blocking and thread-safe:
  call it from a thread (`asyncio.to_thread`), never on the event loop. Deleting the uploaded WAV stays with the caller.
- `ready_state(state_dir=None)` → `ready|not_installed|off|broken` for `ready.asr` (cheap). `status()` → the full dict.
- `doctor_rows(state_dir)` → `[{"id": "asr", "status": "ok"|"warn", "summary", "hint"}]` (doctor.py's row shape; never
  `fail` — ASR is optional). `cli_main(argv, *, state_dir)` → exit code. `shutdown()` stops the worker.

Tests: `tests/test_asr.py` (46; the real-engine class needs `AGENTJ_ASR_TEST_STATE` or `AGENTJ_ASR_DOWNLOADS`).
