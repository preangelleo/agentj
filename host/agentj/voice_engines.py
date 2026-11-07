"""P208: `agentj voice` — choose the voice that reads replies, documented in the bundled manual and configuration skills.

A tap on 播报 reads a reply with the phone's own voices (free, the default). A long press sends the main Agent a preset
request; the skill then runs these commands:

  agentj voice status [--json]          engine, voice, spoken rewrite (setting + effective), this month's estimated spend
  agentj voice engines [--json]         what this computer can use: phone · system · local models (installed / runnable on
                                        this hardware, licence) · ElevenLabs / OpenAI / Volcengine (key set? — by NAME only)
  agentj voice voices --engine <e>      the voices of one engine (ElevenLabs: the owner's own clones first)
  agentj voice set --engine <e> [--voice V] [--spoken auto|on|off] [--model mid|<id>] [--max-chars N]
                   [--tts-model M] [--key-env NAME] [--app-id ID] [--rate R] [--command '<argv JSON>']
  agentj voice test --say '<text>' [--out file.wav]   a sample in the chosen voice (the Agent links the file in its reply:
                                        the phone shows it as an audio card); phone engine → tap 播报 on any reply
  agentj voice reset                    back to the default (the phone's own voices, spoken rewrite auto)
  agentj voice price [<provider> --per-1k-chars X | --remove]   the TTS price table (USD per 1 000 characters)

Engines map onto the existing preference keys (one transactional write, validated like `agentj config set`):
phone → voice.tts.mode=phone · system → mode=host (say / espeak-ng) · local → mode=host + provider=command (a local model's
argv: UTF-8 text on stdin, a PCM WAV written to {output}) · elevenlabs / openai / volcengine → mode=cloud + provider.

Keys: never read, printed or asked for in chat. `engines` asks the running service whether each variable NAME is set in its
environment (yes / no only); without serve it checks this process's environment and the service env file by name. A missing
key → `agentj secret request` (the skill gives the exact command with a read-only --verify-url).

Spend: each host-side reading appends one line to `<state>/usage/YYYY-MM-DD.jsonl` (kind `voice`, P204's format: seconds,
rewrite tokens, characters spoken, dollars when known, `est` when estimated) — `agentj usage` counts it, `agentj voice status`
sums this month. Nothing leaves this computer.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

from . import preferences as p

ENGINES = ("phone", "system", "local", "elevenlabs", "openai", "volcengine")
CLOUD = ("elevenlabs", "openai", "volcengine")
KEY_ENV = {"elevenlabs": "ELEVENLABS_API_KEY", "openai": "OPENAI_API_KEY", "volcengine": "VOLCENGINE_TTS_ACCESS_KEY"}
# Read-only endpoints for `agentj secret request --verify-url` (GET, 2xx = the key works). Volcengine has no read-only key
# check: its key is verified by the test reading.
VERIFY = {"elevenlabs": ("https://api.elevenlabs.io/v1/user", "xi-api-key: {value}"),
          "openai": ("https://api.openai.com/v1/models", "Authorization: Bearer {value}")}
SIGNUP = {"elevenlabs": "https://elevenlabs.io/", "openai": "https://platform.openai.com/api-keys",
          "volcengine": "https://console.volcengine.com/speech/app"}
OPENAI_VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")
VOLC_VOICES = ("zh_female_vv_uranus_bigtts", "zh_male_m191_uranus_bigtts", "zh_female_xiaohe_uranus_bigtts",
               "en_female_dacey_uranus_bigtts")

# TTS list prices, USD — checked 2026-10-13 from public price pages / summaries; an ESTIMATE, the owner's account tier decides.
# ElevenLabs: API ≈ $0.10 / 1 000 characters for the multilingual models (Flash ≈ $0.05; subscription credits: Creator overage
# ≈ $0.22, Pro ≈ $0.18). OpenAI: ≈ $0.015 per spoken minute (gpt-4o-mini-tts / realtime-mini audio tokens) → per character by
# language. Volcengine: no public per-character list price found → no estimate ("—") until the owner sets one.
PRICES_CHECKED = "2026-10-13"
PER_1K = {"elevenlabs": 0.10}
OPENAI_PER_MIN = 0.015
CHARS_PER_MIN = {"zh": 250, "ja": 300, "en": 900}

LOCAL_MODELS = (
    # name, executables that mean "installed", licence, minimum CPU cores / available MiB on CPU, GPU wanted, languages
    {"name": "piper", "bins": ("piper",), "license": "GPL-3.0 program (piper1-gpl); each voice has its own licence on its model card",
     "cpu": 2, "mem": 512, "gpu": False, "langs": "zh en ja …", "speed": "faster than real time on CPU",
     "command": '["piper","--model","<voice>.onnx","--output_file","{output}"]'},
    {"name": "kokoro", "bins": ("kokoro-tts",), "license": "Apache-2.0 (Kokoro-82M weights and code)",
     "cpu": 4, "mem": 2048, "gpu": False, "langs": "en zh ja …", "speed": "about real time on 4 CPU cores, fast on a GPU",
     "command": "a 5-line wrapper script that reads stdin and writes {output}"},
    {"name": "cosyvoice", "bins": ("cosyvoice",), "license": "Apache-2.0 (CosyVoice 2 code and 0.5B weights)",
     "cpu": 8, "mem": 8192, "gpu": True, "langs": "zh en ja ko yue", "speed": "needs an NVIDIA GPU (≥ 6 GB) to keep up",
     "command": "a wrapper script around its Python API"},
)


# ------------------------------------------------------------------ what is configured
def engine_of(prefs) -> str:
    mode, provider = p.get(prefs, "voice.tts.mode"), p.get(prefs, "voice.tts.provider")
    if mode == "phone":
        return "phone"
    if mode == "host":
        return "local" if provider == "command" else "system"
    return provider if provider in CLOUD else "openai"


def _price_file(st):
    return st.root / "voice-prices.json"


def owner_prices(st) -> dict:
    try:
        d = json.loads(_price_file(st).read_text())
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in (d.items() if isinstance(d, dict) else ())
            if k in CLOUD and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100}


def tts_usd(st, engine: str, chars: int, lang: str = "zh"):
    """(usd, est) for reading `chars` characters; free engines → (0.0, False); unknown price → (None, False)."""
    if engine not in CLOUD:
        return 0.0, False
    own = owner_prices(st).get(engine)
    if own is not None:
        return round(chars / 1000 * own, 6), True
    if engine in PER_1K:
        return round(chars / 1000 * PER_1K[engine], 6), True
    if engine == "openai":
        return round(chars / CHARS_PER_MIN.get(lang, 900) * OPENAI_PER_MIN, 6), True
    return None, False


# ------------------------------------------------------------------ spend (P204's local log, kind "voice")
def record(st, *, seconds: float, engine: str, chars: int, lang: str, rewrite: dict | None = None) -> dict:
    """One reading on the host → one line in the usage log. Never raises."""
    rw = rewrite or {}
    usd_t, est_t = (0.0, False) if engine in ("phone", "system", "local") else (None, False)
    usd_r = rw.get("usd") if not rw.get("cached") else 0.0
    parts = [x for x in (usd_t, usd_r if rw.get("spoken") else 0.0) if x is not None]
    known = usd_t is not None and (not rw.get("spoken") or rw.get("cached") or rw.get("usd") is not None)
    use = {"s": round(max(0.0, seconds), 1), "i": int(rw.get("i") or 0) if not rw.get("cached") else 0,
           "o": int(rw.get("o") or 0) if not rw.get("cached") else 0, "ch": int(chars), "eng": engine,
           "usd": round(sum(parts), 6) if known else None, "est": bool(est_t or rw.get("est"))}
    if rw.get("model"):
        use["model"] = str(rw["model"])[:80]
    if rw.get("spoken"):
        use["rw"] = "cache" if rw.get("cached") else "model"
    if use["usd"] is None:
        use.pop("usd")
    try:
        from . import usage        # P204 (0.21) owns the log when present
        usage.record(st, "voice", use)
        return use
    except ImportError:
        pass
    try:
        d = st.root / "usage"
        d.mkdir(mode=0o700, exist_ok=True)
        f = d / f"{time.strftime('%Y-%m-%d')}.jsonl"
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps({"ts": int(time.time() * 1000), "k": "voice", **use}, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError):
        pass
    return use


def month(st, now: float | None = None) -> dict:
    """This calendar month's readings on the host: count, characters, rewrite tokens, dollars (+ `est`, `no_usd`)."""
    ym = time.strftime("%Y-%m", time.localtime(now or time.time()))
    out = {"month": ym, "n": 0, "chars": 0, "i": 0, "o": 0, "usd": 0.0, "est": False, "no_usd": 0, "rewrites": 0, "cache_hits": 0}
    d = st.root / "usage"
    if not d.is_dir():
        return out
    for f in sorted(d.glob(f"{ym}-*.jsonl")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict) or r.get("k") != "voice":
                continue
            out["n"] += 1
            out["chars"] += r.get("ch") if type(r.get("ch")) is int else 0
            out["i"] += r.get("i") if type(r.get("i")) is int else 0
            out["o"] += r.get("o") if type(r.get("o")) is int else 0
            out["rewrites"] += r.get("rw") == "model"
            out["cache_hits"] += r.get("rw") == "cache"
            if isinstance(r.get("usd"), (int, float)):
                out["usd"] = round(out["usd"] + r["usd"], 6)
                out["est"] = out["est"] or bool(r.get("est"))
            else:
                out["no_usd"] += 1
    return out


# ------------------------------------------------------------------ status
def status(st, prefs=None) -> dict:
    from . import spoken
    prefs = prefs if prefs is not None else p.effective(st)
    eng = engine_of(prefs)
    t = p.get(prefs, "voice.tts") or {}
    out = {"ok": True, "engine": eng, "voice": t.get("voice") or "", "phone_voice": t.get("phone_voice") or "",
           "rate": t.get("rate"), "spoken": {"setting": t.get("spoken", "auto"), "effective": spoken.effective(prefs),
                                             "model": t.get("spoken_model", "mid"), "max_chars": t.get("spoken_max_chars", 400)},
           "month": month(st), "prices_checked": PRICES_CHECKED}
    if eng in CLOUD:
        out.update(tts_model=t.get("model"), key_env=t.get("key_env"))
        if eng == "volcengine":
            out["app_id_set"] = bool(t.get("app_id"))
    if eng == "local":
        out["command"] = (t.get("command") or [""])[0]
    return out


# ------------------------------------------------------------------ engines (scan)
def _gpu() -> dict:
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5)
            first = (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
            if first:
                name, _, mem = first.rpartition(",")
                return {"kind": "nvidia", "name": name.strip()[:60], "memory_mib": int(mem.strip()) if mem.strip().isdigit() else None}
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            pass
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        return {"kind": "apple", "name": "Apple silicon", "memory_mib": None}
    return {"kind": None}


def _available_mib() -> int | None:
    try:
        for ln in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def hardware() -> dict:
    from .voice import hardware as base
    h = dict(base())
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    h.update(gpu=_gpu(), available_mib=_available_mib(), load1=round(load1, 2) if load1 is not None else None)
    h["busy"] = load1 is not None and load1 / max(1, h["cpu_logical"]) > 0.8
    return h


def local_models(hw: dict) -> list[dict]:
    out = []
    mem = hw.get("available_mib") or hw.get("memory_mib") or 0
    for m in LOCAL_MODELS:
        path = next((shutil.which(b) for b in m["bins"] if shutil.which(b)), None)
        gpu_ok = hw["gpu"].get("kind") == "nvidia" and (hw["gpu"].get("memory_mib") or 0) >= 6000
        if m["gpu"]:
            ok, why = gpu_ok, ("" if gpu_ok else "needs an NVIDIA GPU with ≥ 6 GB")
        else:
            ok = hw["cpu_logical"] >= m["cpu"] and mem >= m["mem"]
            why = "" if ok else f"needs ≥ {m['cpu']} CPU cores and ≥ {m['mem']} MiB free memory"
        if ok and hw.get("busy"):
            why = "this computer is busy right now (load above 80 %): it would read slowly until the load drops"
        out.append({"name": m["name"], "installed": bool(path), "path": path, "can_run": ok, "why_not": why,
                    "license": m["license"], "languages": m["langs"], "speed": m["speed"], "command": m["command"]})
    return out


def key_status(st, names: list[str]) -> dict:
    """{NAME: True|False|None} — set in the running service's environment? (names only; None = cannot tell)."""
    from .names import ctl_call, ServeBusy
    try:
        r = ctl_call(st, {"cmd": "voice_keys", "names": names}, 5) if st.exists() else None
    except ServeBusy:
        r = None
    if isinstance(r, dict) and r.get("ok") and isinstance(r.get("keys"), dict):
        return {n: bool(r["keys"].get(n)) for n in names}
    from . import service
    try:
        envf = service.env_file(service.name())
    except Exception:
        envf = None
    out = {}
    for n in names:
        has = bool(os.environ.get(n)) or (service.env_file_has(envf, n) if envf else False)
        out[n] = has if has is not None else None
    return out


def engines(st, prefs=None) -> dict:
    prefs = prefs if prefs is not None else p.effective(st)
    hw = hardware()
    t = p.get(prefs, "voice.tts") or {}
    names = sorted(set(KEY_ENV.values()) | ({t.get("key_env")} if t.get("key_env") else set()))
    keys = key_status(st, names)
    host_os = shutil.which("say") or shutil.which("espeak-ng")
    locals_ = local_models(hw)
    rows = [
        {"id": "phone", "available": True, "cost": "free", "spoken_default": False,
         "note": "the phone's own voices; nothing leaves the phone"},
        {"id": "system", "available": bool(host_os), "cost": "free", "spoken_default": False,
         "program": os.path.basename(host_os) if host_os else None,
         "note": "this computer's OS voice (say / espeak-ng), sounds robotic" if host_os else "no say / espeak-ng installed"},
        {"id": "local", "available": any(m["installed"] and m["can_run"] for m in locals_), "cost": "free",
         "spoken_default": True, "models": locals_, "configured": bool(t.get("command"))},
    ]
    for e in CLOUD:
        env = t.get("key_env") if engine_of(prefs) == e and t.get("key_env") else KEY_ENV[e]
        row = {"id": e, "available": bool(keys.get(env)), "key_env": env, "key_set": keys.get(env), "cost": "paid",
               "spoken_default": True, "signup": SIGNUP[e], "price": price_line(st, e)}
        if e in VERIFY:
            row["verify_url"], row["verify_header"] = VERIFY[e]
        if e == "volcengine":
            row["app_id_set"] = bool(t.get("app_id"))
            row["available"] = row["available"] and row["app_id_set"]
        rows.append(row)
    rec = [r["id"] for r in rows if r["id"] == "local" and r["available"]]
    rec += [m for m in ("phone", "system") if next(r for r in rows if r["id"] == m)["available"]]
    rec += [r["id"] for r in rows if r["id"] in CLOUD and r["available"]]
    return {"ok": True, "current": engine_of(prefs), "hardware": hw, "engines": rows, "recommended_order": rec,
            "prices_checked": PRICES_CHECKED}


def price_line(st, e: str) -> str:
    own = owner_prices(st).get(e)
    if own is not None:
        return f"${own:g} / 1000 characters (your own price)"
    if e == "elevenlabs":
        return "≈ $0.10 / 1000 characters on the API (Flash ≈ $0.05); on a subscription your plan's credits decide (Creator overage ≈ $0.22)"
    if e == "openai":
        return "≈ $0.015 per spoken minute (≈ 250 Chinese / 900 English characters)"
    return "no public per-character price found: check your Volcengine console, then agentj voice price volcengine --per-1k-chars <USD>"


# ------------------------------------------------------------------ voices
def voices(st, engine: str) -> dict:
    if engine == "openai":
        return {"ok": True, "engine": engine, "voices": [{"id": v, "name": v} for v in OPENAI_VOICES],
                "note": "a custom voice of your own is voice_<ID>"}
    if engine == "volcengine":
        return {"ok": True, "engine": engine, "voices": [{"id": v, "name": v} for v in VOLC_VOICES],
                "note": "the full list is in your Volcengine console (豆包语音 → 音色); a clone of your own voice is S_… (bills as seed-icl)"}
    if engine == "phone":
        return {"ok": True, "engine": engine, "voices": [],
                "note": "the phone's own voices: ask the owner to open Settings → 播报 on the phone; set one with --voice <name>"}
    if engine == "system":
        return {"ok": True, "engine": engine, "voices": [], "note": "say -v '?' (macOS) or espeak-ng --voices (Linux)"}
    if engine == "local":
        return {"ok": True, "engine": engine, "voices": [], "note": "the voice is the model file in the local command"}
    if engine != "elevenlabs":
        return {"ok": False, "error": "unknown engine"}
    from .names import ctl_call, ServeBusy
    try:
        r = ctl_call(st, {"cmd": "voice_list", "engine": "elevenlabs"}, 30) if st.exists() else None
    except ServeBusy:
        r = None
    if r is None:
        r = list_elevenlabs(os.environ.get(KEY_ENV["elevenlabs"]))
    return r


def list_elevenlabs(key: str | None, opener=None) -> dict:
    """GET /v1/voices with the owner's key (in the process that has it); the owner's clones first. Never echoes the key or
    the provider's error text."""
    import urllib.request, urllib.error
    if not key:
        return {"ok": False, "engine": "elevenlabs", "error": "missing_key", "key_env": KEY_ENV["elevenlabs"]}
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, method="GET")
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=20) as r:
            data = json.loads(r.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as e:
        return {"ok": False, "engine": "elevenlabs", "error": "key_invalid" if e.code in (401, 403) else "provider_error"}
    except (OSError, ValueError):
        return {"ok": False, "engine": "elevenlabs", "error": "network"}
    out = []
    for v in (data.get("voices") or [])[:200] if isinstance(data, dict) else []:
        if not isinstance(v, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", str(v.get("voice_id") or "")):
            continue
        cat = str(v.get("category") or "")
        out.append({"id": v["voice_id"], "name": str(v.get("name") or "")[:60], "own_clone": cat in ("cloned", "professional"),
                    "category": cat[:20]})
    out.sort(key=lambda x: (not x["own_clone"], x["name"].lower()))
    return {"ok": True, "engine": "elevenlabs", "voices": out[:100]}


# ------------------------------------------------------------------ set / reset
def changes_for(a, prefs) -> tuple[dict, list]:
    """argparse namespace → ({key: value}, [keys to remove])."""
    put, drop = {}, []
    e = a.engine
    if e:
        if e not in ENGINES:
            raise p.ConfigError("voice.tts.mode", "engines: " + ", ".join(ENGINES))
        if e == "phone":
            put["voice.tts.mode"] = "phone"
        elif e == "system":
            put["voice.tts.mode"] = "host"
            if p.get(prefs, "voice.tts.provider") == "command":
                drop.append("voice.tts.provider")
        elif e == "local":
            put.update({"voice.tts.mode": "host", "voice.tts.provider": "command"})
            if not a.command and not p.get(prefs, "voice.tts.command"):
                raise p.ConfigError("voice.tts.command", "a local model needs --command '<argv JSON>' (UTF-8 text on stdin, "
                                                         "a PCM WAV written to {output})")
        else:
            put.update({"voice.tts.mode": "cloud", "voice.tts.provider": e})
            if engine_of(prefs) != e:            # another provider's model / key name / voice would be wrong here
                for k in ("voice.tts.model", "voice.tts.key_env", "voice.tts.voice"):
                    drop.append(k)
    if a.voice is not None:
        if (e or engine_of(prefs)) == "phone":
            put["voice.tts.phone_voice"] = a.voice
        else:
            put["voice.tts.voice"] = a.voice
            drop = [k for k in drop if k != "voice.tts.voice"]
    for flag, key in (("spoken", "voice.tts.spoken"), ("model", "voice.tts.spoken_model"), ("max_chars", "voice.tts.spoken_max_chars"),
                      ("tts_model", "voice.tts.model"), ("key_env", "voice.tts.key_env"), ("app_id", "voice.tts.app_id"),
                      ("rate", "voice.tts.rate")):
        v = getattr(a, flag)
        if v is not None:
            put[key] = v
            drop = [k for k in drop if k != key]
    if a.command:
        try:
            argv = json.loads(a.command)
        except ValueError:
            raise p.ConfigError("voice.tts.command", "--command takes a JSON array of strings") from None
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise p.ConfigError("voice.tts.command", "--command takes a JSON array of strings")
        put["voice.tts.command"] = argv
    return put, drop


def write(st, put: dict, drop: list, dry_run=False) -> dict:
    """All changes in one transactional write (the same path as `agentj config set`)."""
    st.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(st.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        p.ensure()
        raw, doc = p.read()
        new = raw
        for k in drop:
            new = p.edit(new, k, None, remove=True)
        for k, v in put.items():
            if k not in p.SCHEMA or p.SCHEMA[k]["tier"] != "user":
                raise p.ConfigError(k, "unknown key")
            new = p.edit(new, k, v)
        candidate = p.validate(p.parse(new))
        if dry_run:
            return {"ok": True, "applied": False, "dry_run": True, "effective_tts": p.get(candidate, "voice.tts")}
        res = p.transact(st, new) if new != raw else {"ok": True, "applied": True, "verify": {"ok": True, "detail": "unchanged"}}
        return {**res, "changed": sorted(set(put) | set(drop))}
    finally:
        os.close(fd)


def reset(st) -> dict:
    raw, doc = p.read()
    keys = [k for k, _ in p.flatten(doc) if k.startswith("voice.tts.")]
    return write(st, {}, keys)


# ------------------------------------------------------------------ test
def test(st, say: str, out: str | None, prefs=None) -> dict:
    prefs = prefs if prefs is not None else p.effective(st)
    eng = engine_of(prefs)
    if eng == "phone":
        return {"ok": False, "engine": "phone", "phone": True,
                "hint": "the phone reads with its own voices: ask the owner to tap 播报 on any reply to hear it"}
    target = pathlib.Path(out or f"agentj-voice-test-{time.strftime('%H%M%S')}.wav").expanduser().absolute()
    if target.suffix.lower() != ".wav" or target.exists() or not target.parent.is_dir():
        return {"ok": False, "error": "out must be a new .wav file in an existing folder"}
    from .names import ctl_call, ServeBusy
    try:
        r = ctl_call(st, {"cmd": "voice_test", "say": say, "out": str(target)}, 180) if st.exists() else None
    except ServeBusy:
        return {"ok": False, "error": "busy"}
    if r is None:
        r = run_test(st, say, str(target), prefs)
    return r


def run_test(st, say: str, out: str, prefs, kind: str | None = None) -> dict:
    """Synthesize `say` (spoken rewrite applied when it is on) into `out` (a new file, never inside the state dir)."""
    from . import spoken, voice
    if not isinstance(say, str) or not say.strip() or len(say) > 2000:
        return {"ok": False, "error": "say: 1..2000 characters"}
    target = pathlib.Path(out)
    root = os.path.realpath(str(st.root))
    if (not target.is_absolute() or target.suffix.lower() != ".wav" or os.path.realpath(str(target.parent)).startswith(root)
            or not target.parent.is_dir()):
        return {"ok": False, "error": "out must be a new .wav file outside Agent J's state folder"}
    t0 = time.monotonic()
    sc = spoken.script(st, say, prefs, kind)
    try:
        audio = voice.synthesize(sc["text"][:4000], prefs)
    except p.ConfigError as e:
        why = getattr(e, "why", None)
        return {"ok": False, "engine": engine_of(prefs), "error": why or e.detail}
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
    except OSError:
        return {"ok": False, "error": "cannot write out"}
    use = record(st, seconds=time.monotonic() - t0, engine=engine_of(prefs), chars=len(sc["text"]), lang=sc["lang"], rewrite=sc)
    return {"ok": True, "engine": engine_of(prefs), "audio": str(target), "spoken": sc["spoken"], "script": sc["text"],
            "usd": use.get("usd"), "est": use.get("est"),
            "needs": ["link this file in your reply (the phone shows an audio card) and ask the owner to listen"]}


# ------------------------------------------------------------------ CLI
def _print(obj, as_json=True) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agentj voice", description="播报用哪个声音（见随包手册与配置说明）/ the voice that reads replies")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="当前引擎、声音、口语化、本月估算花费 / engine, voice, spoken rewrite, this month's spend").add_argument("--json", action="store_true")
    sub.add_parser("engines", help="本机能用的引擎：手机 · 系统 · 本机模型（能否跑、许可）· 第三方（只看密钥变量名）/ what this computer can use").add_argument("--json", action="store_true")
    v = sub.add_parser("voices", help="某个引擎的声音（ElevenLabs 主人克隆的排前面）/ one engine's voices")
    v.add_argument("value", nargs="?", help=argparse.SUPPRESS); v.add_argument("--engine", choices=ENGINES); v.add_argument("--json", action="store_true")
    s = sub.add_parser("set", help="一次写好播报设置（与 agentj config set 同一套校验）/ write the voice settings in one validated step")
    s.add_argument("--engine", choices=ENGINES, help="phone | system | local | elevenlabs | openai | volcengine")
    s.add_argument("--voice", help="声音 ID（phone = 手机音色名）/ voice ID")
    s.add_argument("--spoken", choices=["auto", "on", "off"], help="播报前口语化 / spoken rewrite")
    s.add_argument("--model", help="口语化模型：mid 或模型 ID / spoken rewrite model: mid or a model ID")
    s.add_argument("--max-chars", dest="max_chars", type=int, help="口语稿最长字数 80–2000 / longest spoken script")
    s.add_argument("--tts-model", dest="tts_model", help="第三方语音模型，如 eleven_v4、seed-tts-2.0 / provider speech model")
    s.add_argument("--key-env", dest="key_env", help="密钥的环境变量名（不是密钥）/ the key's variable NAME")
    s.add_argument("--app-id", dest="app_id", help="火山引擎 App ID（不是密钥）/ Volcengine App ID")
    s.add_argument("--rate", type=float, help="语速 / speech rate")
    s.add_argument("--command", help="本机模型的参数数组 JSON，stdin 收文字、{output} 写 WAV / local model argv JSON")
    s.add_argument("--tts", help=argparse.SUPPRESS)            # older `agentj voice set --tts <voice>`
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="只校验不写 / validate only")
    s.add_argument("--json", action="store_true")
    sub.add_parser("reset", help="回到默认：手机自带声音，口语化 auto / back to the default").add_argument("--json", action="store_true")
    te = sub.add_parser("test", help="用当前声音生成一段试听 WAV（回复里贴链接，手机上是音频卡）/ a sample reading")
    te.add_argument("--say", required=True, help="要读的一两句话 / one or two sentences")
    te.add_argument("--out", help="新的 .wav 文件，默认当前目录 / a new .wav file (default: here)")
    te.add_argument("--json", action="store_true")
    pr = sub.add_parser("price", help="语音价目表（美元 / 千字符），可设自己的价 / the TTS price table")
    pr.add_argument("value", nargs="?", choices=list(CLOUD), help="服务商 / provider")
    pr.add_argument("--per-1k-chars", dest="per_1k", type=float, help="每千字符美元 / USD per 1 000 characters")
    pr.add_argument("--remove", action="store_true", help="回到内置价 / back to the built-in estimate")
    pr.add_argument("--json", action="store_true")
    return ap


def command(args) -> int:
    from .state import State
    a = parser().parse_args(args)
    for k in ("value", "engine", "voice", "spoken", "model", "max_chars", "tts_model", "key_env", "app_id", "rate", "command",
              "tts", "say", "out", "per_1k"):
        if not hasattr(a, k):
            setattr(a, k, None)
    for k in ("remove", "dry_run"):
        if not hasattr(a, k):
            setattr(a, k, False)
    st = State()
    try:
        if a.action == "status":
            _print(status(st)); return 0
        if a.action == "engines":
            _print(engines(st)); return 0
        if a.action == "voices":
            r = voices(st, a.engine or a.value or engine_of(p.effective(st))); _print(r); return 0 if r.get("ok") else 1
        if a.action == "reset":
            r = reset(st); _print({**r, "engine": "phone", "spoken": "auto"}); return 0
        if a.action == "test":
            if not a.say:
                raise p.ConfigError("voice.tts", "test needs --say '<text>'")
            r = test(st, a.say, a.out); _print(r); return 0 if r.get("ok") else 1
        if a.action == "price":
            d = owner_prices(st)
            if a.value:
                if a.value not in CLOUD:
                    raise p.ConfigError("voice.tts.provider", "price: " + ", ".join(CLOUD))
                if a.remove:
                    d.pop(a.value, None)
                elif a.per_1k is not None and 0 <= a.per_1k <= 100:
                    d[a.value] = a.per_1k
                else:
                    raise p.ConfigError("voice.tts.provider", "use --per-1k-chars <USD 0..100> or --remove")
                st.write_private(_price_file(st), json.dumps(d, indent=1, sort_keys=True).encode())
            _print({"ok": True, "checked": PRICES_CHECKED, "prices": {e: price_line(st, e) for e in CLOUD}}); return 0
        # set
        if a.tts and a.voice is None:
            a.voice = a.tts
        prefs = p.effective(st)
        put, drop = changes_for(a, prefs)
        if not put and not drop:
            raise p.ConfigError("voice.tts", "nothing to set: --engine, --voice, --spoken, --model, --max-chars …")
        r = write(st, put, drop, dry_run=a.dry_run)
        _print({**r, "status": status(st) if not a.dry_run else None}); return 0
    except p.ConfigError as e:
        _print(e.result()); return e.code
