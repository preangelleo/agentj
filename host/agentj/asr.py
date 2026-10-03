"""Voice → text on the customer's own computer (PROTOCOL §10.9). `agentj asr install|status|test|engine|remove`.

Engine `sherpa`: sherpa-onnx 1.13.8 (Apache-2.0) + SenseVoice-Small int8 2024-07-17 (FunAudioLLM, FunASR Model License 1.1
— attribution required; see host/ASR.md). Everything lives in `<state>/asr/` (a venv of its own + the model): the state
dir is invisible to the fenced Agent (Invariant 14), so the code this host runs *outside* the fence cannot be rewritten by
it; and `uv tool install --force` (every agentj upgrade) recreates agentj's own venv, which would drop a wheel put there.
The model runs in a resident worker (`asr_worker.py`: one model load, idle exit) so a crash never takes `serve` down.

Engine `voxtype` is opt-in only (`agentj asr engine voxtype`), never picked automatically: voxtype follows the human's own
configuration, which may be a remote service — automatic use could send audio off the machine (PR1).

Downloads are pinned by SHA-256 (wheels per platform × CPython, the model tarball, every extracted file) and come from
PyPI / GitHub or — mainland China — the Tsinghua / Aliyun PyPI mirrors and the same model files on hf-mirror.com or
ModelScope; a byte that does not hash-match is never used. Nothing else leaves the machine: no audio, no text, no telemetry.
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import functools
import hashlib
import json
import os
import pathlib
import platform
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import asr_worker
from .asr_worker import BadAudio

# ------------------------------------------------------------------ pins
SHERPA_VERSION = "1.13.8"
MODEL_ID = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
# (os, arch, python tag) → (path under …/packages/, bytes, sha256). Source: https://pypi.org/pypi/sherpa-onnx/1.13.8/json
# and …/sherpa-onnx-core/1.13.8/json (2026-10-03). sherpa-onnx per CPython 3.11–3.14 (agentj needs ≥ 3.11); core = py3.
WHEELS = {
    ("darwin", "arm64", "cp311"): ("a9/74/dafb3c1c1ff82fc00abd098ade44811e48f3af88b4e3c2f1628567d0e80b/sherpa_onnx-1.13.8-cp311-cp311-macosx_11_0_arm64.whl",
        2141306, "8bb2b86ce44b5c5bb9949977177ddc36954c51097f81284d5ac48588e821ec07"),
    ("darwin", "arm64", "cp312"): ("78/6e/8d9b92e95896ec500084e706529f5637eff797246e74c498922f6aa13a53/sherpa_onnx-1.13.8-cp312-cp312-macosx_11_0_arm64.whl",
        2146858, "04b9268c348e9f4bd61315754ad06b02a3c185d3d7770acd8e2dcee9c2e039f9"),
    ("darwin", "arm64", "cp313"): ("ce/7b/9829c8e222e6103fd3571523252974c1095c49ae7d1511babf46c4f0d6ea/sherpa_onnx-1.13.8-cp313-cp313-macosx_11_0_arm64.whl",
        2147122, "2c5a1799c326715b1ee69dae3be48883ddc6fb922fb813067606f7e9a29ea234"),
    ("darwin", "arm64", "cp314"): ("33/f5/ff7f45799a1a77f165410fce5080be2cb59cfc98375bf4a56d7d1fe80540/sherpa_onnx-1.13.8-cp314-cp314-macosx_11_0_arm64.whl",
        2149334, "7fa6ec595a8b4bba2b91e6f8e58647777050ac4eb9845f5a5026c20636c0e2be"),
    ("darwin", "arm64", "py3"): ("8c/2a/2a47b423fe009bbbcb6d7eed7bcba755d6f9ae923b5664c7208128c32d06/sherpa_onnx_core-1.13.8-py3-none-macosx_11_0_arm64.whl",
        9552498, "9312bbd46c93e31cecd3abda9cc8e71881d86cc3da3c35c7445ab04025b9fc3e"),
    ("darwin", "x86_64", "cp311"): ("d6/be/c2b2a42ffcb22224fa95c62280cdc1f955f30f21805a2fda7fd182539628/sherpa_onnx-1.13.8-cp311-cp311-macosx_10_15_x86_64.whl",
        2332701, "a00d9ceeb4d9531d2f5bd90d194d53408461c5aa6ff46e65f8776ed37007f3d0"),
    ("darwin", "x86_64", "cp312"): ("36/e6/a19257d7c60bf04c07f8d0772b943b6596b6b10cfb0694b7794d36630d40/sherpa_onnx-1.13.8-cp312-cp312-macosx_10_15_x86_64.whl",
        2350796, "ef218d6545a9dcc2aff7043f39026c7d78d6cf46a3876d450d508feebffc00a9"),
    ("darwin", "x86_64", "cp313"): ("7d/4d/b442afd30eff64b83112b54fa866c5d2e3a5aeec13ee8bd665c7b146fba1/sherpa_onnx-1.13.8-cp313-cp313-macosx_10_15_x86_64.whl",
        2351015, "5f8df54514af5025b9d428403dd9a27f6fe252b5e162311bf5c47228a50d0cc5"),
    ("darwin", "x86_64", "cp314"): ("f5/fb/e8a289419f38ed9eb1882d22c12a2680254e5eb945e8e2517c5b2281dd23/sherpa_onnx-1.13.8-cp314-cp314-macosx_10_15_x86_64.whl",
        2351239, "22f699e1fb2e8dafe02b1521ce74e0928b38cb68ad19f481380acf51e6a6965b"),
    ("darwin", "x86_64", "py3"): ("17/2d/98e309811cff9fec48ae48a051b4caab44046755f6bc58daab0c475f40cb/sherpa_onnx_core-1.13.8-py3-none-macosx_10_15_x86_64.whl",
        10878335, "917422880287baa0f0ae1bd10d9528fa78af8cc7c233d2a953a185a4fe8d83f2"),
    ("linux", "aarch64", "cp311"): ("c6/02/f2300e5cb07a611afcac5699a97c8d6f229f53a7c751882818ed40a7f72f/sherpa_onnx-1.13.8-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        4177610, "b56808d19a79368dcaa507d02a4c1ce537ca713fab9fedd91256b4ec599c6bf7"),
    ("linux", "aarch64", "cp312"): ("1f/5f/22e1571146b2c0b581657562d5919b69da13ed93dc70049f42d45fe9e84c/sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        4176945, "2520b1e7b779a29a493a8cec893612218dfad7304786bf3f64df2ff7ac6c3302"),
    ("linux", "aarch64", "cp313"): ("13/e3/115476caa9f80cd5f55e4e7b777a6c2d01a133d10f8fae574ba869fc48c3/sherpa_onnx-1.13.8-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        4177700, "916ec38242e779ee3a99f7b161bd1bf3b02a7fad34cb073d091021c0469e0b58"),
    ("linux", "aarch64", "cp314"): ("4f/9a/51821829b5735b3d7ce607992a62fe93d5f7215cda324c11191c77d49b9b/sherpa_onnx-1.13.8-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        4182835, "45f16c9fabc3d7ce31ba9722ff8196627ddcaa5ffd02413ad0664ad1952ba419"),
    ("linux", "aarch64", "py3"): ("9c/72/02227b14cd3fb3d504a8814ad144ed321a53331948c893821e02f4abb1d7/sherpa_onnx_core-1.13.8-py3-none-manylinux2014_aarch64.whl",
        13353426, "a51a03d55c376c32e15dd63ea852236042ba91a5cd73e253bbf7cf21f6582ba9"),
    ("linux", "x86_64", "cp311"): ("ae/c6/1fe91047af08b30806f18fac55639037d3cf0247aeec97b7b7884407d6bf/sherpa_onnx-1.13.8-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        4398381, "94fd1476b56ed36b851da8db9bd5144e92f669007ad54e0d8094913e4fbf1418"),
    ("linux", "x86_64", "cp312"): ("13/78/2f712b7408ddbb64614c20388912414705b728516224e737b63f2adec94a/sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        4400575, "6949773017647febc0c3696dffb2c67dd3febd4737b87e3dd135a42773704a06"),
    ("linux", "x86_64", "cp313"): ("8f/eb/b77acde02d9eee359436ade9239d9ade4351b09fbe85387a293f341c19ed/sherpa_onnx-1.13.8-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        4401242, "0e5d870fb0648befb94e260c11de4cf4b293b2a90a63e3a6a2869be698927bc4"),
    ("linux", "x86_64", "cp314"): ("2d/e2/b1af63c1f6a9e0025cbc9aa3b1814ba97ede63db0ad9f297bb9f268fdfc2/sherpa_onnx-1.13.8-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        4403669, "97309e4b5d9850490085da61e35b0fca002a39ec9da93cd9e21d2d0dcc1cc62a"),
    ("linux", "x86_64", "py3"): ("b6/ca/e27c1fb5c54b181d4a5450f3d5c789d9da02eb42dc9b20cfdab5dda4c864/sherpa_onnx_core-1.13.8-py3-none-manylinux2014_x86_64.whl",
        10642497, "4da90acf435373d7b2ba9cc0be806e7e274d28f63f5780880b5dea6721626e36"),
}

TARBALL = {"name": MODEL_ID + ".tar.bz2", "size": 163002883,
           "sha256": "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
           "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/" + MODEL_ID + ".tar.bz2"}
# name in model/ → (path inside the tarball / the HF repo, bytes, sha256). Same bytes in the tarball, on Hugging Face
# (csukuangfj/…-2024-07-17 @ 2365baea, LFS oid) and on ModelScope (pengzhendong/…-yue): checked 2026-10-03.
FILES = {
    "model.int8.onnx": ("model.int8.onnx", 239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
    "tokens.txt": ("tokens.txt", 315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
    "LICENSE": ("LICENSE", 71, "221c6df10b0931a5629adad671ea48fb7747e034c414b6d2bfa275bc3dd4ea17"),
    "README.md": ("README.md", 104, "763991a00edaea534ab36bf1b7cf89e61e911666dcfabbba71f91f9f7c593a63"),
    "zh.wav": ("test_wavs/zh.wav", 178988, "b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373"),
}
REQUIRED = ("model.int8.onnx", "tokens.txt")
SELF_TEST_EXPECT = "开饭时间"            # zh.wav: 「开饭时间早上9点至下午5点。」
HF_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
HF_REV = "2365baeacb507f821a0c8120fcee3d484dba7a07"
PYPI = "https://files.pythonhosted.org/packages/"
TUNA = "https://pypi.tuna.tsinghua.edu.cn/packages/"
ALIYUN = "https://mirrors.aliyun.com/pypi/packages/"
# Each route: where the two wheels come from, and the model as the GitHub tarball or as single files from a model hub.
ROUTES = {
    "github": {"pypi": [PYPI], "tarball": TARBALL["url"]},
    "hf-mirror": {"pypi": [TUNA, ALIYUN], "files": f"https://hf-mirror.com/{HF_REPO}/resolve/{HF_REV}/",
                  "names": ("model.int8.onnx", "tokens.txt", "LICENSE", "README.md", "zh.wav")},
    "modelscope": {"pypi": [ALIYUN, TUNA],
                   "files": "https://www.modelscope.cn/models/pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue/resolve/master/",
                   "names": ("model.int8.onnx", "tokens.txt", "LICENSE")},   # no test wav there: the self-test only loads
}
MIRRORS = ("auto", "github", "hf-mirror", "modelscope")
ENGINES = ("auto", "sherpa", "voxtype", "off")
DISK_MB = 300
DOWNLOADS_ENV = "AGENTJ_ASR_DOWNLOADS"   # a folder of already-downloaded artifacts (offline install, tests); hash-checked

# ------------------------------------------------------------------ runtime limits (PROTOCOL §10.9)
QUEUE_MAX = 3            # waiting takes beyond the one being decoded → "busy"
IDLE_S = 600             # the worker frees its ≈ 400 MB after 10 idle minutes
LOAD_TIMEOUT_S = 60
VOXTYPE_NOISE = ("Loading audio file", "Audio format:", "Processing ", "Using remote", "Configured remote",
                 "Remote transcription")
REASONS = ("not_installed", "off", "bad_audio", "timeout", "busy", "engine_failed", "no_speech")
PROTOCOL_WHY = {"engine_failed": "broken"}   # reason → asr_res `why` (all other reasons are the same word)

OK, WARN, FAIL = "ok", "warn", "fail"        # doctor.py's statuses


def why(reason: str) -> str:
    """The `asr_res` `why` for a transcribe() reason."""
    return PROTOCOL_WHY.get(reason, reason)


# ------------------------------------------------------------------ paths and settings
def _state_root(state_dir=None) -> pathlib.Path:
    if state_dir is None:
        from .state import state_dir as _sd
        return _sd()
    return pathlib.Path(state_dir)


def asr_dir(state_dir=None) -> pathlib.Path:
    return _state_root(state_dir) / "asr"


def _private_dir(p: pathlib.Path) -> pathlib.Path:
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def _venv_python(d: pathlib.Path) -> pathlib.Path:
    return d / "venv" / "bin" / "python"


def default_threads() -> int:
    return max(1, min(2, os.cpu_count() or 1))


def settings(state_dir=None) -> dict:
    """config.json `asr`: {"engine": auto|sherpa|voxtype|off, "threads": n}; defaults auto / min(2, cpus)."""
    try:
        cfg = json.loads((_state_root(state_dir) / "config.json").read_text())
        a = cfg.get("asr") if isinstance(cfg, dict) else None
    except (OSError, ValueError):
        a = None
    a = a if isinstance(a, dict) else {}
    eng = a.get("engine") if a.get("engine") in ENGINES else "auto"
    th = a.get("threads")
    th = th if isinstance(th, int) and not isinstance(th, bool) and 1 <= th <= 8 else default_threads()
    return {"engine": eng, "threads": th}


def _set_setting(state_dir, **kw) -> bool:
    """Read-modify-write config.json under the state's config lock. False when the host is not initialised."""
    from .state import State, _write_private
    st = State(_state_root(state_dir))
    if not st.config_path.exists():
        return False
    with st.config_lock():
        cfg = st.config()
        a = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
        a.update(kw)
        cfg["asr"] = a
        _write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
    return True


def _read_json(p: pathlib.Path) -> dict:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(p: pathlib.Path, obj: dict) -> None:
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def _tree_bytes(p: pathlib.Path) -> int:
    n = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                n += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return n


def _tilde(p) -> str:
    p = str(p)
    h = os.path.expanduser("~").rstrip("/")
    return "~" + p[len(h):] if h and (p == h or p.startswith(h + "/")) else p


# ------------------------------------------------------------------ platform
def platform_key() -> tuple[str, str, str] | None:
    """(os, arch, cpython tag) when a pinned wheel exists for this interpreter, else None."""
    osn = "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else sys.platform
    m = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64", "arm64": "arm64"}.get(m, m)
    if osn == "linux" and arch == "arm64":
        arch = "aarch64"
    if osn == "darwin" and arch == "aarch64":
        arch = "arm64"
    if sys.implementation.name != "cpython":
        return None
    tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    if osn == "linux":
        libc, ver = platform.libc_ver()
        if libc != "glibc":
            return None                     # musl (Alpine): no manylinux wheel
        try:
            if tuple(int(x) for x in ver.split(".")[:2]) < (2, 17):
                return None
        except ValueError:
            return None
    key = (osn, arch, tag)
    return key if key in WHEELS and (osn, arch, "py3") in WHEELS else None


def no_platform_reason() -> str:
    return (f"没有适合这台电脑的 sherpa-onnx {SHERPA_VERSION} / no sherpa-onnx wheel for {sys.platform} "
            f"{platform.machine()} Python {sys.version_info[0]}.{sys.version_info[1]}"
            + (f" ({platform.libc_ver()[0] or 'unknown libc'})" if sys.platform.startswith("linux") else ""))


# ------------------------------------------------------------------ status / doctor
def _installed_ok(d: pathlib.Path) -> tuple[bool, str | None]:
    """(installed, problem). installed.json is written last by install; a missing or resized file = broken."""
    rec = _read_json(d / "installed.json")
    if not rec:
        return False, None
    if not _venv_python(d).exists():
        return True, "ASR venv missing (agentj's Python moved?)"
    for n in REQUIRED:
        p = d / "model" / n
        try:
            if p.is_symlink() or p.stat().st_size != FILES[n][1]:
                return True, f"model file {n} changed"
        except OSError:
            return True, f"model file {n} missing"
    return True, None


def ready_state(state_dir=None) -> str:
    """Just the `ready.asr` word (ready | not_installed | off | broken) — cheap: no disk walk, for every `ready`."""
    s = settings(state_dir)
    if s["engine"] == "off":
        return "off"
    if s["engine"] == "voxtype":
        return "ready" if shutil.which("voxtype") else "not_installed"
    installed, problem = _installed_ok(asr_dir(state_dir))
    return ("broken" if problem else "ready") if installed else "not_installed"


def status(state_dir=None) -> dict:
    """What `ready.asr` / `agentj asr status` / doctor show. `state` ∈ ready | not_installed | off | broken (PROTOCOL
    §10.0); `active` = the engine a take would use now (None when none)."""
    d = asr_dir(state_dir)
    s = settings(state_dir)
    installed, problem = _installed_ok(d)
    rec = _read_json(d / "installed.json")
    vox = shutil.which("voxtype")
    eng = s["engine"]
    if eng == "off":
        state, active = "off", None
    elif eng == "voxtype":
        state, active = ("ready", "voxtype") if vox else ("not_installed", None)
    elif installed:
        state, active = ("broken", None) if problem else ("ready", "sherpa")
    else:
        state, active = "not_installed", None
    w = _RESIDENTS.get(str(d))
    out = {"state": state, "engine": eng, "active": active, "installed": installed, "problem": problem,
           "sherpa_version": rec.get("sherpa_version"), "model": rec.get("model"), "route": rec.get("route"),
           "dir": str(d), "disk_bytes": _tree_bytes(d) if d.exists() else 0,
           "model_bytes": _tree_bytes(d / "model") if (d / "model").exists() else 0,
           "threads": s["threads"], "voxtype": vox, "platform_ok": platform_key() is not None,
           "worker": {"running": bool(w and w.alive()), "pid": w.proc.pid if w and w.alive() else None},
           "last_error": _read_json(d / "last_error.json").get("detail")}
    return out


def doctor_rows(state_dir=None) -> list[dict]:
    """Rows in doctor.py's shape ({"id","status","summary","hint"}, status ok|warn|fail). ASR is optional: never a ✗."""
    s = status(state_dir)
    if s["state"] == "ready" and s["active"] == "sherpa":
        row = (OK, f"本机转写可用 / local speech-to-text ready · SenseVoice-Small · sherpa-onnx {s['sherpa_version']} · "
                   f"{s['threads']} 线程 / threads · {s['disk_bytes'] // 1_000_000} MB", "")
    elif s["state"] == "ready":
        row = (OK, f"用 voxtype 转写（你自己选的；它按你的 voxtype 设置运行）/ voxtype (your choice; runs with your voxtype "
                   f"settings) · {_tilde(s['voxtype'])}", "")
    elif s["state"] == "off":
        row = (OK, "语音转写已关闭 / speech-to-text switched off", "agentj asr engine auto")
    elif s["state"] == "broken":
        row = (WARN, f"本机转写坏了 / local speech-to-text is broken: {s['problem']}",
               "agentj asr remove && agentj asr install")
    elif s["engine"] == "voxtype":
        row = (WARN, "选了 voxtype，但这台电脑上找不到 voxtype / engine voxtype, but voxtype is not on PATH",
               "agentj asr engine auto")
    elif not s["platform_ok"]:
        row = (WARN, "没装语音转写；这台电脑装不了本地转写 / no speech-to-text; " + no_platform_reason(),
               "手机输入法自带的听写可以用 / use the phone keyboard's dictation")
    else:
        row = (WARN, "没装语音转写（手机录音不能转成文字）/ speech-to-text not installed (voice takes stay untranscribed)",
               f"agentj asr install（约 {download_mb('github')} MB 下载 / ≈ {download_mb('github')} MB download）")
    return [{"id": "asr", "status": row[0], "summary": row[1], "hint": row[2]}]


# ------------------------------------------------------------------ WAV check
def check_wav(path: str, strict: bool = False) -> dict:
    """Header-only check (asr_worker.parse_wav). Raises BadAudio."""
    return asr_worker.parse_wav(path, strict=strict)


# ------------------------------------------------------------------ the resident worker
_RESIDENTS: dict[str, "_Resident"] = {}
_GATE = threading.Lock()          # guards _PENDING
_PENDING = [0]                    # takes admitted (running + waiting), across engines: one decode per host
_RUN = threading.Lock()           # held while one take is decoded


def _worker_env() -> dict:
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT")
    return {k: os.environ[k] for k in keep if k in os.environ}


class _Resident:
    """One `asr_worker.py` process per ASR dir. Used under _RUN only (one request at a time)."""

    def __init__(self, d: pathlib.Path, threads: int, idle_s: float | None = None):
        self.d, self.threads, self.idle_s = d, threads, IDLE_S if idle_s is None else idle_s
        self.proc: subprocess.Popen | None = None
        self.buf = b""
        self.seq = 0
        self.load_ms = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _readline(self, deadline: float) -> dict | None:
        """The next JSON line, or None on EOF; raises TimeoutError at the deadline."""
        fd = self.proc.stdout.fileno()
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            r, _, _ = select.select([fd], [], [], left)
            if not r:
                raise TimeoutError
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        try:
            v = json.loads(line)
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def start(self, deadline: float) -> None:
        self.stop()
        log = os.open(self.d / "worker.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            self.proc = subprocess.Popen(
                [str(_venv_python(self.d)), "-I", os.path.abspath(asr_worker.__file__), "--model", str(self.d / "model"),
                 "--threads", str(self.threads), "--idle", str(self.idle_s)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=_worker_env(), cwd=str(self.d),
                start_new_session=True)
        finally:
            os.close(log)
        self.buf = b""
        try:
            msg = self._readline(min(deadline, time.monotonic() + LOAD_TIMEOUT_S))
        except TimeoutError:
            self.stop()
            raise
        if not msg or msg.get("t") != "ready":
            detail = (msg or {}).get("detail") or "worker exited while loading"
            self.stop()
            raise RuntimeError(f"model load failed: {detail}")
        self.load_ms = msg.get("load_ms")

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                p.kill()
            except OSError:
                pass
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except OSError:
                pass
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def request(self, req: dict, deadline: float) -> dict:
        """Send one request (respawning the worker if it idled out or died); raises TimeoutError / RuntimeError."""
        for attempt in (0, 1):
            if not self.alive():
                self.start(deadline)
            self.seq += 1
            req = {**req, "id": self.seq}
            try:
                self.proc.stdin.write((json.dumps(req) + "\n").encode())
                self.proc.stdin.flush()
                msg = self._readline(deadline)
            except TimeoutError:
                self.stop()          # a decode cannot be interrupted: the worker goes, the next take reloads (0.6 s)
                raise
            except (BrokenPipeError, OSError):
                msg = None
            if msg is None:          # it idled out just now, or crashed: once more with a fresh one
                self.stop()
                if attempt == 0 and time.monotonic() < deadline:
                    continue
                raise RuntimeError("ASR worker exited")
            if msg.get("id") == self.seq:
                return msg
            self.stop()
            raise RuntimeError("ASR worker answered out of order")
        raise RuntimeError("ASR worker exited")


def _resident(d: pathlib.Path, threads: int) -> _Resident:
    w = _RESIDENTS.get(str(d))
    if w is None or w.threads != threads:
        if w:
            w.stop()
        w = _RESIDENTS[str(d)] = _Resident(d, threads)
    return w


def shutdown() -> None:
    """Stop every resident worker (serve exit; tests)."""
    for w in list(_RESIDENTS.values()):
        w.stop()
    _RESIDENTS.clear()


atexit.register(shutdown)


# ------------------------------------------------------------------ transcribe
def _fail(reason: str, detail: str) -> dict:
    return {"ok": False, "reason": reason, "detail": detail}


def _voxtype(exe: str, wav: str, deadline: float) -> dict:
    t0 = time.monotonic()
    try:
        p = subprocess.Popen([exe, "transcribe", wav], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True, stdin=subprocess.DEVNULL)
    except OSError as e:
        return _fail("engine_failed", f"voxtype: {e.strerror or e}")
    try:
        out, err = p.communicate(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        p.communicate()
        return _fail("timeout", "voxtype took too long")
    text = voxtype_clean(out)
    if p.returncode != 0:
        last = (err or "").strip().splitlines()[-1:] or [f"exit {p.returncode}"]
        return _fail("engine_failed", "voxtype: " + asr_worker.ANSI.sub("", last[0])[:200])
    if not text:
        return _fail("no_speech", "no words in this audio")
    return {"ok": True, "text": text, "engine": "voxtype", "ms": int((time.monotonic() - t0) * 1000)}


def voxtype_clean(stdout: str) -> str:
    """voxtype's transcript without its progress lines (relay bridge/transcribe.py `_clean`) and SenseVoice tags."""
    lines = []
    for raw in (stdout or "").splitlines():
        line = asr_worker.ANSI.sub("", raw).strip()
        if not line or line.startswith(VOXTYPE_NOISE):
            continue
        if " INFO " in line or " WARN " in line or " ERROR " in line or " DEBUG " in line:
            continue
        lines.append(line)
    return asr_worker.clean_text("\n".join(lines))


def transcribe(wav_path: str, *, timeout_s: float, state_dir=None) -> dict:
    """→ {"ok": True, "text", "engine", "ms"} or {"ok": False, "reason", "detail"}; reason ∈ REASONS (`why(reason)` is the
    asr_res word). The WAV is checked here (header only; 16 kHz mono PCM16, or 8–48 kHz mono/stereo PCM16 that the worker
    converts). timeout_s covers waiting in the queue too. Thread-safe; never raises for audio or engine problems."""
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    s = settings(state_dir)
    if s["engine"] == "off":
        return _fail("off", "speech-to-text is switched off (agentj asr engine auto)")
    d = asr_dir(state_dir)
    vox = shutil.which("voxtype") if s["engine"] == "voxtype" else None
    if s["engine"] == "voxtype" and not vox:
        return _fail("not_installed", "engine voxtype, but voxtype is not on PATH")
    if not vox:
        installed, problem = _installed_ok(d)
        if not installed:
            return _fail("not_installed", "run `agentj asr install` on this computer")
        if problem:
            return _fail("engine_failed", problem)
    try:
        check_wav(wav_path)
    except BadAudio as e:
        return _fail("bad_audio", str(e))
    with _GATE:
        if _PENDING[0] >= 1 + QUEUE_MAX:
            return _fail("busy", f"{_PENDING[0]} takes already queued")
        _PENDING[0] += 1
    try:
        if not _RUN.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return _fail("timeout", "waited too long in the queue")
        try:
            if vox:
                return _voxtype(vox, wav_path, deadline)
            t0 = time.monotonic()
            try:
                r = _resident(d, s["threads"]).request({"wav": os.path.abspath(wav_path)}, deadline)
            except TimeoutError:
                return _fail("timeout", f"no transcript within {timeout_s:g} s")
            except (RuntimeError, OSError) as e:
                return _fail("engine_failed", str(e)[:300])
            if not r.get("ok"):
                return _fail(r.get("why") if r.get("why") in REASONS else "engine_failed", str(r.get("detail") or "")[:300])
            text = asr_worker.clean_text(r.get("text") or "")
            if not text:
                return _fail("no_speech", "no words in this audio")
            return {"ok": True, "text": text[:20000], "engine": "sherpa", "ms": int((time.monotonic() - t0) * 1000)}
        finally:
            _RUN.release()
    finally:
        with _GATE:
            _PENDING[0] -= 1


# ------------------------------------------------------------------ install: downloads
class InstallError(Exception):
    pass


def _wheels_for(key) -> list[tuple[str, int, str]]:
    return [WHEELS[key], WHEELS[(key[0], key[1], "py3")]]


def download_mb(route: str, key=None) -> int:
    key = key or platform_key() or ("linux", "x86_64", "cp313")
    n = sum(w[1] for w in _wheels_for(key))
    r = ROUTES[route]
    n += TARBALL["size"] if "tarball" in r else sum(FILES[x][1] for x in r["names"])
    return round(n / 1_000_000)


def _sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class _Progress:
    def __init__(self, out, label: str, total: int):
        self.out, self.label, self.total, self.last = out, label, total, 0

    def __call__(self, done: int) -> None:
        pct = int(done * 100 / self.total) if self.total else 100
        step = pct // 10 if self.total >= 20_000_000 else pct // 100    # small files: one line when done
        if step != self.last:
            self.last = step
            self.out(f"  {self.label}: {done / 1e6:6.1f} / {self.total / 1e6:.1f} MB ({pct}%)")


def _from_downloads(name: str, size: int, sha: str, dest: pathlib.Path) -> bool:
    src = os.environ.get(DOWNLOADS_ENV)
    if not src:
        return False
    p = pathlib.Path(src) / name
    try:
        if not p.is_file() or p.stat().st_size != size:
            return False
    except OSError:
        return False
    # P33-X10: copy first, then check the PRIVATE copy — the bytes that get installed are the bytes that were hashed
    # (hashing the source and copying after let a writer of that folder swap the file in between)
    tmp = dest.with_name(dest.name + ".copy")
    try:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out, open(p, "rb") as inp:
            shutil.copyfileobj(inp, out, 1 << 20)
        if tmp.stat().st_size != size or _sha256(tmp) != sha:
            tmp.unlink()
            return False
        os.replace(tmp, dest)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


def _fetch(urls: list[str], dest: pathlib.Path, size: int, sha: str, out, label: str) -> str:
    """Download to dest (resuming dest.part with a Range request), verify size + SHA-256; return the URL used.
    A finished, verified dest is reused. Raises InstallError after every URL failed."""
    if dest.exists() and dest.stat().st_size == size and _sha256(dest) == sha:
        return "cache"
    if _from_downloads(dest.name, size, sha, dest):
        out(f"  {label}: {_tilde(os.environ[DOWNLOADS_ENV])}")
        return "downloads"
    part = dest.with_name(dest.name + ".part")
    errors = []
    for url in urls:
        if not _url_ok(url):
            continue
        for _try in range(3):
            have = part.stat().st_size if part.exists() else 0
            if have > size:
                part.unlink()
                have = 0
            hdr = {"User-Agent": "agentj-asr-install"}
            if 0 < have < size:
                hdr["Range"] = f"bytes={have}-"
            prog = _Progress(out, label, size)
            try:
                if have < size:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
                        if have and r.status != 206:
                            have = 0          # the server ignored Range: start over
                        mode = "ab" if have else "wb"
                        fd = os.open(part, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if have else os.O_TRUNC), 0o600)
                        os.fchmod(fd, 0o600)
                        with os.fdopen(fd, mode) as f:
                            done = have
                            while True:
                                b = r.read(1 << 20)
                                if not b:
                                    break
                                done += len(b)
                                if done > size:
                                    raise InstallError("more bytes than expected")
                                f.write(b)
                                prog(done)
                if part.stat().st_size != size:
                    raise InstallError(f"incomplete ({part.stat().st_size} of {size} bytes)")
                if _sha256(part) != sha:
                    part.unlink()
                    raise InstallError("SHA-256 mismatch — not used")
                os.replace(part, dest)
                return url
            except urllib.error.HTTPError as e:
                e.close()
                if e.code == 416:
                    part.unlink(missing_ok=True)
                    continue
                errors.append(f"{_host(url)}: HTTP {e.code}")
                break
            except InstallError as e:
                errors.append(f"{_host(url)}: {e}")
                if "SHA-256" in str(e) or "more bytes" in str(e):
                    part.unlink(missing_ok=True)
                    break
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                errors.append(f"{_host(url)}: {getattr(e, 'reason', None) or type(e).__name__}")
                # network hiccup: try this URL again (resuming), then the next one
    raise InstallError(f"{label}: " + "; ".join(errors[-4:] or ["no source"]))


def _url_ok(url: str) -> bool:
    """https only — plain http just for a local test server (as update.py)."""
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or url


def _reachable(url: str, timeout: float = 5.0) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "agentj-asr-install"})
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as e:
        e.close()
        return e.code < 500
    except Exception:  # noqa: BLE001
        return False


def route_order(mirror: str) -> list[str]:
    """`auto`: GitHub first unless the PyPI index points at a mainland mirror or github.com does not answer in 5 s;
    then the other routes as fallbacks. A named mirror is tried alone."""
    if mirror != "auto":
        return [mirror]
    idx = " ".join(os.environ.get(k, "") for k in ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL")).lower()
    china = any(x in idx for x in ("tuna", "aliyun", "tencent", "huaweicloud", "ustc", "douban", ".cn/"))
    if not china and not os.environ.get(DOWNLOADS_ENV) and not _reachable("https://github.com/"):
        china = True
    return ["hf-mirror", "modelscope", "github"] if china else ["github", "hf-mirror", "modelscope"]


# ------------------------------------------------------------------ install: steps
def _safe_extract(tar_path: pathlib.Path, dest: pathlib.Path, names: list[str]) -> None:
    """Stream the tarball and copy only the pinned regular files to fixed names in dest — no path from the archive is
    ever used to write, links and every other member are skipped, sizes and SHA-256 are checked."""
    want = {MODEL_ID + "/" + FILES[n][0]: n for n in names}
    seen = set()
    with tarfile.open(tar_path, "r|bz2") as t:
        for m in t:
            n = want.get(m.name)
            if n is None:
                continue
            if not m.isreg() or n in seen or m.size != FILES[n][1]:
                raise InstallError(f"unexpected archive member {m.name!r}")
            seen.add(n)
            src = t.extractfile(m)
            h = hashlib.sha256()
            fd = os.open(dest / n, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                for b in iter(functools.partial(src.read, 1 << 20), b""):
                    h.update(b)
                    f.write(b)
            if h.hexdigest() != FILES[n][2]:
                raise InstallError(f"{n}: SHA-256 mismatch")
    missing = set(names) - seen
    if missing:
        raise InstallError(f"archive lacks {sorted(missing)}")


def _run(argv: list[str], timeout: float = 300) -> None:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise InstallError(f"{os.path.basename(argv[0])}: {type(e).__name__}") from None
    if p.returncode != 0:
        tail = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-2:]
        raise InstallError(f"{os.path.basename(argv[0])} {' '.join(argv[1:3])}: " + " | ".join(tail)[:300])


def _make_venv(d: pathlib.Path, wheels: list[pathlib.Path], shas: list[str]) -> None:
    """A venv with agentj's own Python; the two verified wheels installed offline with --require-hashes."""
    venv = d / "venv"
    req = d / "dl" / "requirements.txt"
    req.write_text("".join(f"{w.as_uri()} --hash=sha256:{h}\n" for w, h in zip(wheels, shas)))
    exe = sys.executable
    try:
        _run([exe, "-m", "venv", str(venv)], 180)
        _run([str(_venv_python(d)), "-m", "pip", "install", "--no-index", "--no-deps", "--only-binary=:all:",
              "--require-hashes", "--disable-pip-version-check", "--no-cache-dir", "-q", "-r", str(req)], 300)
        return
    except InstallError as first:
        uv = shutil.which("uv")
        if not uv:
            raise
        shutil.rmtree(venv, ignore_errors=True)
        try:   # Python without ensurepip (Debian without python3-venv): uv builds the venv instead
            _run([uv, "venv", "-q", "--python", exe, str(venv)], 180)
            _run([uv, "pip", "install", "-q", "--python", str(_venv_python(d)), "--no-index", "--no-deps",
                  "--require-hashes", "--no-cache", "-r", str(req)], 300)
        except InstallError as second:
            raise InstallError(f"{first}; uv: {second}") from None


def _clean_partial(d: pathlib.Path) -> None:
    shutil.rmtree(d / "venv", ignore_errors=True)
    shutil.rmtree(d / "model", ignore_errors=True)
    shutil.rmtree(d / "model.staging", ignore_errors=True)
    (d / "installed.json").unlink(missing_ok=True)


def _confirm(prompt: str, inp, out) -> bool:
    if not (inp.isatty() if hasattr(inp, "isatty") else False):
        return False
    out(prompt)
    try:
        ans = inp.readline()
    except (OSError, KeyboardInterrupt):
        return False
    return ans.strip().lower() in ("y", "yes", "是", "好")


FALLBACK = ("这台电脑装不了本地转写（{reason}）。可以：① 装 voxtype 后运行 `agentj asr engine voxtype`；② 先用手机输入法自带的听写。"
            " / Local speech-to-text could not be installed here ({reason}). Instead: ① install voxtype, then run "
            "`agentj asr engine voxtype`; ② use the phone keyboard's dictation for now.")


def install(state_dir=None, mirror: str = "auto", yes: bool = False, out=print, inp=None) -> int:
    inp = inp or sys.stdin
    d = asr_dir(state_dir)
    key = platform_key()
    if key is None:
        out(FALLBACK.format(reason=no_platform_reason()))
        return 1
    installed, problem = _installed_ok(d)
    if installed and not problem:
        out(f"✓ 已经装好了 / already installed ({_tilde(d)}); `agentj asr test` 试一下 / to try it")
        return 0
    if mirror not in MIRRORS:
        out(f"✗ --mirror 只能是 / must be one of {', '.join(MIRRORS)}")
        return 2
    order = route_order(mirror)
    first = order[0]
    mb = download_mb(first, key)
    src = "github.com + pypi.org" if first == "github" else (
        "hf-mirror.com + pypi.tuna.tsinghua.edu.cn" if first == "hf-mirror" else "modelscope.cn + mirrors.aliyun.com")
    out(f"本机语音转写：SenseVoice-Small（中/英/日/韩/粤）+ sherpa-onnx {SHERPA_VERSION}，全在这台电脑上运行，录音和文字不出这台电脑。\n"
        f"Local speech-to-text: SenseVoice-Small (zh/en/ja/ko/yue) + sherpa-onnx {SHERPA_VERSION}; runs on this computer only.\n"
        f"  下载 / download ≈ {mb} MB from {src}（每个文件都核对 SHA-256 / every file SHA-256-checked）\n"
        f"  占用磁盘 / disk ≈ {DISK_MB} MB in {_tilde(d)} · 转写时内存 / memory while in use ≈ 400 MB\n"
        f"  模型许可 / model licence: FunASR Model License 1.1 (attribution: FunAudioLLM SenseVoice)")
    if not yes and not _confirm("继续？/ Continue? [y/N] ", inp, out):
        out("没有安装（需要确认：在终端里回答 y，或加 --yes）/ not installed (answer y in a terminal, or pass --yes)")
        return 1
    _private_dir(d)
    dl = _private_dir(d / "dl")
    (d / "last_error.json").unlink(missing_ok=True)
    _clean_partial(d)
    try:
        # 1. wheels — every route's PyPI hosts, the chosen route's first
        pypi = []
        for r in order + [x for x in ROUTES if x not in order]:
            pypi += [u for u in ROUTES[r]["pypi"] if u not in pypi]
        wpaths, wshas = [], []
        for path, size, sha in _wheels_for(key):
            p = dl / path.rsplit("/", 1)[1]
            _fetch([u + path for u in pypi], p, size, sha, out, p.name.split("-")[0] + " wheel")
            wpaths.append(p)
            wshas.append(sha)
        # 2. model
        staging = _private_dir(d / "model.staging")
        used, errors = None, []
        for r in order:
            route = ROUTES[r]
            try:
                if "tarball" in route:
                    tp = dl / TARBALL["name"]
                    _fetch([route["tarball"]], tp, TARBALL["size"], TARBALL["sha256"], out, "model")
                    out("  解压 / extracting …")
                    _safe_extract(tp, staging, list(FILES))
                else:
                    for n in route["names"]:
                        if (staging / n).exists():
                            continue
                        _fetch([route["files"] + FILES[n][0]], dl / n, FILES[n][1], FILES[n][2], out, n)
                        os.replace(dl / n, staging / n)
                used = r
                break
            except InstallError as e:
                errors.append(f"{r}: {e}")
                for n in list(FILES):        # files from one route are the same bytes on every route: keep verified ones
                    p = staging / n
                    if p.exists() and (p.stat().st_size != FILES[n][1] or _sha256(p) != FILES[n][2]):
                        p.unlink()
        if used is None:
            raise InstallError("model download failed — " + " · ".join(errors))
        # 3. venv + wheels
        out("  安装 sherpa-onnx / installing sherpa-onnx …")
        _make_venv(d, wpaths, wshas)
        os.replace(staging, d / "model")
        os.chmod(d / "model", 0o700)
        os.chmod(d / "venv", 0o700)
        # 4. self-test in a fresh worker
        out("  自测 / self-test …")
        _self_test(d, settings(state_dir)["threads"])
        rec = {"v": 1, "sherpa_version": SHERPA_VERSION, "model": MODEL_ID, "route": used, "installed": int(time.time()),
               "python": sys.executable, "files": {n: FILES[n][2] for n in FILES if (d / "model" / n).exists()}}
        _write_json(d / "installed.json", rec)
    except (InstallError, OSError) as e:
        _clean_partial(d)
        reason = str(e) if isinstance(e, InstallError) else f"{type(e).__name__}: {e}"
        _write_json(d / "last_error.json", {"at": int(time.time()), "detail": reason[:500]})
        out("✗ " + FALLBACK.format(reason=reason[:300]))
        out("  已下载的部分留在 / partial downloads kept in " + _tilde(dl) + "，再运行一次会接着下 / rerun to resume")
        return 1
    shutil.rmtree(dl, ignore_errors=True)
    cur = settings(state_dir)["engine"]
    if cur != "voxtype":
        _set_setting(state_dir, engine="sherpa")
    out(f"✓ 装好了 / installed: {_tilde(d)} ({_tree_bytes(d) // 1_000_000} MB)"
        + ("；引擎仍是你选的 voxtype（换用：agentj asr engine sherpa）/ engine stays voxtype (switch: agentj asr engine sherpa)"
           if cur == "voxtype" else ""))
    return 0


def _self_test(d: pathlib.Path, threads: int) -> None:
    w = _Resident(d, threads, idle_s=30)
    try:
        zh = d / "model" / "zh.wav"
        if zh.exists():
            r = w.request({"wav": str(zh)}, time.monotonic() + LOAD_TIMEOUT_S)
            if not r.get("ok") or SELF_TEST_EXPECT not in (r.get("text") or ""):
                raise InstallError(f"self-test failed: {r.get('detail') or 'wrong transcript'}")
        else:   # the ModelScope copy has no sample: prove the model loads and decodes one second of silence
            tmp = d / "selftest.wav"
            _write_silence(tmp, 1.0)
            try:
                r = w.request({"wav": str(tmp)}, time.monotonic() + LOAD_TIMEOUT_S)
            finally:
                tmp.unlink(missing_ok=True)
            if not r.get("ok"):
                raise InstallError(f"self-test failed: {r.get('detail')}")
    except (TimeoutError, RuntimeError) as e:
        raise InstallError(f"self-test failed: {e or type(e).__name__}") from None
    finally:
        w.stop()


def _write_silence(p: pathlib.Path, seconds: float) -> None:
    import wave
    with wave.open(str(p), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * int(16000 * seconds))


def remove(state_dir=None, out=print) -> int:
    d = asr_dir(state_dir)
    w = _RESIDENTS.pop(str(d), None)
    if w:
        w.stop()
    if not d.exists():
        out("没有装 / nothing installed")
        return 0
    n = _tree_bytes(d)
    shutil.rmtree(d, ignore_errors=True)
    if settings(state_dir)["engine"] == "sherpa":
        _set_setting(state_dir, engine="auto")
    out(f"✓ 删掉了模型和 sherpa-onnx（{n // 1_000_000} MB）/ removed the model and sherpa-onnx ({n // 1_000_000} MB)")
    return 0


# ------------------------------------------------------------------ CLI
def _print_status(s: dict, out) -> None:
    label = {"ready": "可用 / ready", "not_installed": "没装 / not installed", "off": "关闭 / off", "broken": "坏了 / broken"}
    out(f"语音转写 / speech-to-text: {label[s['state']]}")
    out(f"  引擎设置 / engine setting: {s['engine']}" + (f" → {s['active']}" if s["active"] else ""))
    if s["installed"]:
        out(f"  sherpa-onnx {s['sherpa_version']} · {s['model']} · {s['threads']} 线程 / threads · route {s['route']}")
        out(f"  {_tilde(s['dir'])}: {s['disk_bytes'] / 1e6:.0f} MB (model {s['model_bytes'] / 1e6:.0f} MB)")
    if s["problem"]:
        out(f"  问题 / problem: {s['problem']} → agentj asr remove && agentj asr install")
    if s["voxtype"]:
        out(f"  voxtype: {_tilde(s['voxtype'])}" + ("" if s["engine"] == "voxtype" else " （要用：agentj asr engine voxtype / to use it)"))
    if s["worker"]["running"]:
        out(f"  worker pid {s['worker']['pid']}")
    if s["last_error"] and not s["installed"]:
        out(f"  上次安装失败 / last install failed: {s['last_error']}")
    if s["state"] == "not_installed" and s["engine"] != "voxtype":
        out(f"  安装 / install: agentj asr install（≈ {download_mb('github')} MB）")


def cli_main(argv, *, state_dir=None, out=print, inp=None) -> int:
    ap = argparse.ArgumentParser(prog="agentj asr", description="本机语音转写 / local speech-to-text (PROTOCOL §10.9)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help=f"装 sherpa-onnx + SenseVoice（≈ {download_mb('github')} MB）/ install")
    i.add_argument("--mirror", choices=MIRRORS, default="auto")
    i.add_argument("--yes", action="store_true")
    st = sub.add_parser("status", help="状态 / status")
    st.add_argument("--json", action="store_true")
    t = sub.add_parser("test", help="转写一个 WAV（默认用模型自带的 zh.wav）/ transcribe a WAV")
    t.add_argument("wav", nargs="?")
    e = sub.add_parser("engine", help="auto · sherpa · voxtype · off")
    e.add_argument("engine", choices=ENGINES)
    sub.add_parser("remove", help="删掉模型和 sherpa-onnx / remove the model and sherpa-onnx")
    try:
        a = ap.parse_args(argv)
    except SystemExit as x:
        return int(x.code or 0)
    if a.cmd == "install":
        return install(state_dir, a.mirror, a.yes, out, inp)
    if a.cmd == "status":
        s = status(state_dir)
        if a.json:
            out(json.dumps(s, ensure_ascii=False, indent=1))
        else:
            _print_status(s, out)
        return 0
    if a.cmd == "engine":
        if not _set_setting(state_dir, engine=a.engine):
            out("✗ 这台电脑还没初始化 / not initialised: agentj init")
            return 1
        s = status(state_dir)
        out(f"✓ engine = {a.engine} → {s['state']}" + (
            "（voxtype 按你自己的 voxtype 设置运行：如果它用的是远程服务，录音会发到那里 / voxtype runs with your own voxtype "
            "settings: if they name a remote service, audio goes there）" if a.engine == "voxtype" else ""))
        return 0
    if a.cmd == "remove":
        return remove(state_dir, out)
    wav = a.wav or str(asr_dir(state_dir) / "model" / "zh.wav")
    if not os.path.exists(wav):
        out("✗ 没有这个文件 / no such file" + ("" if a.wav else "（这份模型没带示例，请给一个 WAV / this model copy has no sample: "
                                                          "pass a WAV)"))
        return 2
    r = transcribe(wav, timeout_s=120, state_dir=state_dir)
    shutdown()
    if r["ok"]:
        out(f"✓ [{r['engine']}, {r['ms']} ms] {r['text']}")
        return 0
    out(f"✗ {r['reason']}: {r['detail']}")
    return 1
