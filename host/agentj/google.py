"""P91 / ADR-A195 (0.18.0 stage): Google tools foundation — `agentj google status|check|install|plan|on|off`.

What 0.18.0 does (Leo approved the five P91 defaults, 2026-10-09):
- Pre-installs ONE pinned Google CLI, gog (openclaw/gogcli v0.43.0, MIT), SHA-256 checked, into `<state>/google/tools/`, with
  its own isolated home `<state>/google/gog-home/` (GOG_HOME). It never touches a gog / gcloud / gws the owner already has,
  never inherits ADC or token environment variables. gcloud is installed only when a 0.18.1 setup needs it; gws is optional.
- A failed or skipped install never blocks binding, pairing or chat (the owner can say "I won't use Google": `agentj google off`).
- Reads (never changes) what already exists on the computer: gog / gcloud / gws on PATH and whether their config files exist.
  It never runs the owner's own tools and never opens a keyring, so no token, email or client secret is read.
- Lists Google purposes with the minimum scope each needs, API and website sign-in as separate rows. In 0.18.0 no purpose is
  authorized by Agent J: the own-project / In production / PKCE / local token-owner flow is 0.18.1 (approved staging), so API
  purposes stay `unavailable` (check `unsupported`) in the P86 projection — never a green "Google ready".

Nothing here prints or stores a token, authorization code, callback URL, client JSON or e-mail address. State is
`<state>/google/state.json` (0600) in a 0700 directory, outside the Agent fence; the fenced Agent reaches it through
elevate.sock (`{"t": "google", "op": ...}`), like keep-awake.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pathlib
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time

GOG_VERSION = "0.43.0"
GOG_REPO = "openclaw/gogcli"
# (sys.platform family, machine) → (asset, bytes, sha256). Source: GitHub release v0.43.0 asset digests and checksums.txt,
# cross-checked 2026-10-10; linux_amd64 also downloaded and verified (P91 evidence/install.json). Windows is not a host OS
# (WSL runs the Linux build).
GOG_ASSETS = {
    ("darwin", "arm64"): ("gogcli_0.43.0_darwin_arm64.tar.gz", 13624865,
                          "e93c2aef60b9a5c14f8f320c9554ed86776920a49afc51b684635aec2f5ca050"),
    ("darwin", "x86_64"): ("gogcli_0.43.0_darwin_amd64.tar.gz", 15124778,
                           "b736ef888ab8c56bfc2d2ad83e5ab73d5aea983672b9f69052c8713e9daddd5c"),
    ("linux", "x86_64"): ("gogcli_0.43.0_linux_amd64.tar.gz", 14820807,
                          "a16d4b8b917e36b96b09b30ecb7a5049d06ff1e88b856a101eec12b86b33fe05"),
    ("linux", "aarch64"): ("gogcli_0.43.0_linux_arm64.tar.gz", 13348998,
                           "f66e3c9ab7664b7633d57d2d5303e0db75deb4045e1b32c3493c0d8ba68a70f7"),
}
_ARCH = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "aarch64"}
# Same bytes first from Agent J's own download host (mainland-friendly), then GitHub; the SHA-256 pin decides, not the host.
MIRRORS = ("https://agentj.app/dl/tools/gog/{v}/{a}", "https://github.com/" + GOG_REPO + "/releases/download/v{v}/{a}")
URLS_ENV = "AGENTJ_GOOGLE_URLS"          # tests / offline: comma-separated URL templates with {v} {a}
PREINSTALL_ENV = "AGENTJ_GOOGLE_PREINSTALL"  # "off" → serve never pre-installs (tests, CI)
RETRY_SECS = 86_400                      # a failed background pre-install is retried at most once a day
CLI_TTL = 7 * 86_400                     # P86: CLI / skill probe result is fresh for 7 days (and until the version changes)
PROBE_TIMEOUT = 20
# Environment variables that could hand gog someone else's credentials or a different config root.
_STRIP = ("GOG_HOME", "GOG_ACCESS_TOKEN", "GOG_QUOTA_PROJECT", "GOG_ACCOUNT", "GOG_CLIENT", "GOOGLE_APPLICATION_CREDENTIALS",
          "GOOGLE_OAUTH_ACCESS_TOKEN", "CLOUDSDK_CONFIG", "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE")

# Purposes (P91 ADR §Scope plan): the minimum scope each needs; never all / user / full. `api` = the API to enable.
PURPOSES = {
    "gmail-read": {"zh": "读取邮件", "en": "Read Gmail", "api": "gmail.googleapis.com",
                   "scopes": ["https://www.googleapis.com/auth/gmail.readonly"], "class": "restricted",
                   "gog": ["--services", "gmail", "--gmail-scope", "readonly"]},
    "gmail-send": {"zh": "代发邮件（每封仍要批准）", "en": "Send Gmail (each send still approved)", "api": "gmail.googleapis.com",
                   "scopes": ["https://www.googleapis.com/auth/gmail.send"], "class": "sensitive",
                   "gog": ["--services", "gmail", "--gmail-scope", "send"]},
    "calendar-read": {"zh": "查看日历", "en": "Read Calendar", "api": "calendar-json.googleapis.com",
                      "scopes": ["https://www.googleapis.com/auth/calendar.readonly"], "class": "sensitive",
                      "gog": ["--services", "calendar", "--readonly"]},
    "calendar-write": {"zh": "写入日历日程", "en": "Write Calendar events", "api": "calendar-json.googleapis.com",
                       "scopes": ["https://www.googleapis.com/auth/calendar.events"], "class": "sensitive",
                       "gog": None},   # gog's calendar default is the full calendar scope: exact events-only in 0.18.1
    "drive-file": {"zh": "云盘里它建/你选的文件", "en": "Drive files it creates or you pick", "api": "drive.googleapis.com",
                   "scopes": ["https://www.googleapis.com/auth/drive.file"], "class": "non-sensitive",
                   "gog": ["--services", "drive", "--drive-scope", "file"]},
    "youtube-publish": {"zh": "YouTube 上传与读回", "en": "YouTube upload and read-back", "api": "youtube.googleapis.com",
                        "scopes": ["https://www.googleapis.com/auth/youtube.upload",
                                   "https://www.googleapis.com/auth/youtube.readonly"], "class": "sensitive",
                        "gog": None},   # gog 0.43.0 has no upload: a local official-library adapter in 0.18.1
    "search-console-read": {"zh": "Search Console 只读", "en": "Search Console read-only", "api": "searchconsole.googleapis.com",
                            "scopes": ["https://www.googleapis.com/auth/webmasters.readonly"], "class": "sensitive",
                            "gog": None},   # gog's default is the write scope: exact read-only needs the 0.18.1 adapter
}
API_STAGE = "0.18.1"
# The 0.18.1 own-project flow (ADR-A195 §flow); 0.18.0 shows it as the plan, nothing here executes it.
STEPS = ("owner_accepted", "console_login_required", "project_ready", "apis_ready", "consent_ready", "client_saved",
         "production_verified", "oauth_consent_required", "token_verified", "ready")


class GoogleError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


# ------------------------------------------------------------------ paths and the private record
def root(st) -> pathlib.Path:
    return st.root / "google"


def _private_dir(p: pathlib.Path) -> pathlib.Path:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.is_symlink() or not p.is_dir():
        raise GoogleError("storage_attention", "not a directory")
    os.chmod(p, 0o700)
    return p


def tool_dir(st) -> pathlib.Path:
    return root(st) / "tools" / f"gog-{GOG_VERSION}"


def binary(st) -> pathlib.Path:
    return tool_dir(st) / "gog"


def gog_home(st) -> pathlib.Path:
    return root(st) / "gog-home"


def _state_path(st) -> pathlib.Path:
    return root(st) / "state.json"


def _blank() -> dict:
    return {"v": 1, "preinstall": "on", "install": None, "attempt": None, "check": None}


def read(st) -> dict:
    try:
        rec = json.loads(_state_path(st).read_text())
    except FileNotFoundError:
        return _blank()
    except (OSError, ValueError, UnicodeDecodeError):
        return {**_blank(), "corrupt": True}
    base = _blank()
    if isinstance(rec, dict) and rec.get("v") == 1:
        base.update({k: rec[k] for k in base if k in rec})
    else:
        base["corrupt"] = True
    if base["preinstall"] not in ("on", "off"):
        base["preinstall"] = "on"
    return base


def _write(st, rec: dict) -> None:
    _private_dir(root(st))
    clean = {k: rec[k] for k in _blank() if k in rec}
    st.write_private(_state_path(st), json.dumps(clean, ensure_ascii=False, sort_keys=True).encode())


@contextlib.contextmanager
def _lock(st, wait: bool = True):
    _private_dir(root(st))
    fd = os.open(root(st) / "google.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise GoogleError("busy", "another install is running")
        yield
    finally:
        os.close(fd)


def _now() -> int:
    return int(time.time())


def _iso(ts: int | float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))


# ------------------------------------------------------------------ platform and environment
def asset() -> tuple[str, int, str] | None:
    fam = "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else sys.platform
    return GOG_ASSETS.get((fam, _ARCH.get(platform.machine().lower(), platform.machine().lower())))


def urls(name: str) -> list[str]:
    tpl = [u.strip() for u in os.environ.get(URLS_ENV, "").split(",") if u.strip()] or list(MIRRORS)
    return [u.format(v=GOG_VERSION, a=name) for u in tpl]


def env(st) -> dict:
    """gog's environment: our own home only, nobody else's token or config root; PATH/HOME/keyring bus kept."""
    e = {k: v for k, v in os.environ.items() if k not in _STRIP and not k.startswith("GOG_")}
    e["GOG_HOME"] = str(gog_home(st))
    return e


def _run(st, argv: list[str], timeout: int = PROBE_TIMEOUT) -> tuple[int, str]:
    _private_dir(gog_home(st))
    p = subprocess.run([str(binary(st)), *argv], capture_output=True, text=True, timeout=timeout, env=env(st),
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout


def _sha256(p: pathlib.Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ install / remove
def _extract(archive: pathlib.Path, dest: pathlib.Path) -> None:
    """Only the single regular file `gog` (./gog) leaves the archive; links, devices, paths and anything else are refused."""
    with tarfile.open(archive, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name.lstrip("./") == "gog"]
        if len(members) != 1 or not members[0].isreg() or members[0].size <= 0 or members[0].size > 200 << 20:
            raise GoogleError("bad_archive", "expected one regular file gog")
        src = tf.extractfile(members[0])
        tmp = dest.with_name(dest.name + f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o700)
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def installed(st, rec: dict | None = None) -> bool:
    rec = read(st) if rec is None else rec
    b = binary(st)
    try:
        info = b.lstat()
    except OSError:
        return False
    inst = rec.get("install") or {}
    return stat.S_ISREG(info.st_mode) and bool(info.st_mode & 0o100) and inst.get("version") == GOG_VERSION


def install(st, *, out=lambda s: None, fetch=None, force: bool = False, background: bool = False) -> dict:
    """Pinned download → SHA-256 → extract gog → `gog --version` must say v0.43.0 → record. Idempotent; never raises."""
    rec = read(st)
    if rec["preinstall"] == "off" and not force:
        return {"ok": True, "result": "disabled"}
    a = asset()
    if a is None:
        return _attempt(st, {"ok": False, "result": "unsupported_platform"}, record=not background)
    if installed(st, rec) and not force:
        return {"ok": True, "result": "already", "version": GOG_VERSION}
    if fetch is None:
        from . import asr
        fetch = asr._fetch
    name, size, sha = a
    try:
        with _lock(st, wait=not background):
            dl = _private_dir(root(st) / "download")
            _private_dir(root(st) / "tools")
            _private_dir(tool_dir(st))
            archive = dl / name
            try:
                used = fetch(urls(name), archive, size, sha, out, "gog")
            except Exception as e:          # asr.InstallError / network: one reason word, never a body
                return _attempt(st, {"ok": False, "result": "failed", "reason": "download", "detail": str(e)[:200]})
            if _sha256(archive) != sha:
                archive.unlink(missing_ok=True)
                return _attempt(st, {"ok": False, "result": "failed", "reason": "sha256"})
            _extract(archive, binary(st))
            archive.unlink(missing_ok=True)
            code, txt = _run(st, ["--version"])
            if code != 0 or f"v{GOG_VERSION}" not in txt:
                binary(st).unlink(missing_ok=True)
                return _attempt(st, {"ok": False, "result": "failed", "reason": "version_probe"})
            rec = read(st)
            rec["install"] = {"version": GOG_VERSION, "asset": name, "sha256": sha, "at": _now(),
                              "source": "mirror" if "agentj.app" in str(used) else "cache" if used in ("cache", "downloads") else "github"}
            rec["attempt"] = {"at": _now(), "result": "installed"}
            rec["check"] = {"at": _now(), "result": "ok", "version": GOG_VERSION, "accounts": 0}
            _write(st, rec)
            return {"ok": True, "result": "installed", "version": GOG_VERSION, "source": rec["install"]["source"]}
    except GoogleError as e:
        return _attempt(st, {"ok": False, "result": "failed", "reason": e.reason}, record=e.reason != "busy")
    except (OSError, tarfile.TarError, subprocess.SubprocessError) as e:
        return _attempt(st, {"ok": False, "result": "failed", "reason": "io", "detail": type(e).__name__})


def _attempt(st, res: dict, record: bool = True) -> dict:
    if record:
        with contextlib.suppress(OSError, GoogleError):
            rec = read(st)
            rec["attempt"] = {"at": _now(), "result": res["result"], **({"reason": res["reason"]} if res.get("reason") else {})}
            _write(st, rec)
    return res


def set_preinstall(st, on: bool) -> dict:
    """`agentj google on|off`. off = "I won't use Google tools": removes only OUR pinned binary (never gog-home, never the
    owner's own gog/gcloud/gws, never a Google project); on = pre-install again (now, if called by the CLI)."""
    with _lock(st):
        rec = read(st)
        rec["preinstall"] = "on" if on else "off"
        if not on:
            shutil.rmtree(root(st) / "tools", ignore_errors=True)
            shutil.rmtree(root(st) / "download", ignore_errors=True)
            rec["install"] = None
            rec["check"] = None
        _write(st, rec)
    return {"ok": True, "result": "on" if on else "off"}


def wanted_background(st, now: int | None = None) -> bool:
    """serve: pre-install in the background? Not when off / unsupported / done / tried within a day / disabled by env."""
    if os.environ.get(PREINSTALL_ENV, "").lower() in ("off", "0", "no"):
        return False
    # Like skill linking (personalize.ensure): a source checkout — tests, e2e suites, development — never downloads by itself.
    if (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").is_file() and \
            os.environ.get(PREINSTALL_ENV, "").lower() != "on":
        return False
    rec = read(st)
    if rec["preinstall"] == "off" or asset() is None or installed(st, rec):
        return False
    last = (rec.get("attempt") or {}).get("at") or 0
    return (now or _now()) - last >= RETRY_SECS


def ensure_background(st, delay: float = 120.0) -> threading.Thread | None:
    """Started once by serve: a quiet daemon thread, after `delay` s, installs gog when wanted. Never blocks serve."""
    if not wanted_background(st):
        return None

    def go():
        time.sleep(delay)
        with contextlib.suppress(Exception):
            if wanted_background(st):
                res = install(st, background=True)
                st.log("google_preinstall", result=res.get("result"), reason=res.get("reason"))
    t = threading.Thread(target=go, name="google-preinstall", daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------ read-only detection and probes
def _config_roots() -> list[pathlib.Path]:
    h = pathlib.Path.home()
    xdg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or h / ".config")
    out = [xdg]
    if sys.platform == "darwin":
        out.append(h / "Library" / "Application Support")
    return out


def detect_existing() -> dict:
    """What the owner already has — presence only (PATH entries and whether a config file exists). Never runs those tools,
    never opens a keyring, never reads a file's contents, so no token / e-mail / client id is read."""
    roots = _config_roots()

    def any_file(*rel):
        return any((r / x).is_file() for r in roots for x in rel)
    return {
        "gog": {"on_path": shutil.which("gog") is not None,
                "config": any_file("gogcli/config.json", "gogcli/credentials.json")},
        "gcloud": {"on_path": shutil.which("gcloud") is not None,
                   "config": any_file("gcloud/credentials.db", "gcloud/application_default_credentials.json")},
        "gws": {"on_path": shutil.which("gws") is not None, "config": any_file("gws/config.json", "gws/credentials.json")},
    }


def check(st) -> dict:
    """Live, side-effect-free probe of OUR gog: version and how many accounts its own isolated store has (a count only)."""
    rec = read(st)
    if not installed(st, rec):
        res = {"result": "absent" if rec["preinstall"] == "on" else "disabled"}
    else:
        try:
            code, txt = _run(st, ["--version"])
            if code != 0 or f"v{GOG_VERSION}" not in txt:
                res = {"result": "attention", "reason": "version_probe"}
            else:
                code, txt = _run(st, ["--json", "auth", "list"])
                n = None
                with contextlib.suppress(ValueError, TypeError, AttributeError):
                    n = len(json.loads(txt).get("accounts") or []) if code == 0 else None
                res = {"result": "ok", "version": GOG_VERSION, "accounts": n}
        except (OSError, subprocess.SubprocessError) as e:
            res = {"result": "attention", "reason": "timeout" if isinstance(e, subprocess.TimeoutExpired) else "io"}
        res["at"] = _now()
        with contextlib.suppress(OSError, GoogleError):
            with _lock(st):
                rec = read(st)
                rec["check"] = res
                _write(st, rec)
    return status(st)


def purposes(existing: dict | None = None) -> list[dict]:
    """One row per purpose: API authorization and website sign-in are separate rows, never one green "Google ready"."""
    existing = existing if existing is not None else detect_existing()
    # A gog / gws config may hold the owner's own Workspace grants (unverified, never read); gcloud alone says nothing about
    # Gmail / Calendar / YouTube, so it is listed under `existing` only.
    seen = existing["gog"]["config"] or existing["gws"]["config"]
    rows = []
    for pid, p in PURPOSES.items():
        rows.append({"id": pid, "title": {"zh": p["zh"], "en": p["en"]}, "api": p["api"], "scopes": list(p["scopes"]),
                     "class": p["class"], "api_state": "existing_unverified" if seen else "not_configured",
                     "authorize_in": API_STAGE, "browser": "separate"})
    return rows


def status(st, existing: dict | None = None) -> dict:
    rec = read(st)
    existing = detect_existing() if existing is None else existing
    inst = installed(st, rec)
    a = asset()
    tool = ("disabled" if rec["preinstall"] == "off" else "installed" if inst else
            "unsupported_platform" if a is None else "absent")
    chk = rec.get("check") or {}
    fresh = bool(tool == "installed" and inst and chk.get("result") == "ok" and _now() - int(chk.get("at") or 0) < CLI_TTL)
    return {
        "ok": True, "stage": "0.18.0", "tool": {"name": "gog", "version": GOG_VERSION, "state": tool, "fresh": fresh,
                                               "checked_at": _iso(chk.get("at")), "check": chk.get("result"),
                                               "accounts": chk.get("accounts") if inst else None,
                                               "last_attempt": rec.get("attempt"), "corrupt": bool(rec.get("corrupt"))},
        "existing": existing,
        "purposes": purposes(existing),
        "api_authorization": {"available_in": API_STAGE, "state": "not_available_yet"},
        "browser_signin": "separate: the browser login health check, not this command",
    }


# ------------------------------------------------------------------ P86 projection (proposed registry schema, closed fields)
def registry_entries(st, s: dict | None = None) -> list[dict]:
    """Entries in the P86 registry shape (schemas/registry.schema.json): one `cli` row for gog and one `credential` row per
    purpose. A purpose is never `ready` in 0.18.0: the host cannot authorize it yet (check `unsupported`)."""
    s = s or status(st)
    t = s["tool"]
    at = t["checked_at"]
    # Expiry belongs to the actual check, never to each inventory read.
    from datetime import datetime
    exp = _iso(datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() + CLI_TTL) if t["fresh"] and at else None
    cli_status = ("ready" if t["fresh"] else "stale" if t["state"] == "installed" else
                  "unavailable" if t["state"] in ("disabled", "unsupported_platform") else "missing")
    out = [{"id": "google-cli", "kind": "cli", "title": {"zh": "Google 工具（gog）", "en": "Google CLI (gog)"},
            "source_ref": "google/tools/gog", "scope": [], "provides": ["google.runtime"], "depends_on": [],
            "status": cli_status, "credential_name": None,
            "check": {"status": "ok" if t["fresh"] else "not_found" if t["state"] == "absent" else "unsupported",
                      "adapter_id": "google-purpose-v1", "last_verified_at": at if t["fresh"] else None,
                      "expires_at": exp, "source_digest": GOG_ASSETS.get(_key(), ("", 0, None))[2]}}]
    for row in s["purposes"]:
        out.append({"id": "google-" + row["id"], "kind": "credential", "title": row["title"],
                    "source_ref": "google/connections/" + row["id"], "scope": row["scopes"],
                    "provides": ["google." + row["id"].replace("-", ".")], "depends_on": ["google-cli"],
                    "status": "discovered" if row["api_state"] == "existing_unverified" else "unavailable",
                    "credential_name": "GOOGLE_" + row["id"].upper().replace("-", "_"),
                    "check": {"status": "unsupported", "adapter_id": "google-purpose-v1", "last_verified_at": None,
                              "expires_at": None, "source_digest": None}})
    return out


def _key():
    fam = "darwin" if sys.platform == "darwin" else "linux"
    return (fam, _ARCH.get(platform.machine().lower(), platform.machine().lower()))


def plan(ids: list[str]) -> dict:
    """The setup plan for chosen purposes (what the owner would confirm): APIs, scopes, steps. Read-only; executes nothing."""
    bad = [i for i in ids if i not in PURPOSES]
    if bad or not ids:
        raise GoogleError("purpose", "choose from: " + ", ".join(PURPOSES))
    chosen = {i: PURPOSES[i] for i in ids}
    return {"ok": True, "purposes": ids, "apis": sorted({p["api"] for p in chosen.values()}),
            "scopes": sorted({s for p in chosen.values() for s in p["scopes"]}),
            "classes": sorted({p["class"] for p in chosen.values()}),
            "owner_steps": ["sign in to Google on this computer (password / 2-step there, never in chat)",
                            "accept Google Cloud terms yourself if asked",
                            "on the unverified-app warning: check it is YOUR new project, then Advanced → Continue",
                            "approve exactly the listed permissions"],
            "agent_steps": list(STEPS), "automated_in": API_STAGE,
            "production": "In production (not Google verification); a Testing grant is re-authorized once after the switch",
            "never": ["all / user / full scopes", "tokens, codes, callback URLs or client JSON in chat",
                      "clicking Google's risk warning for the owner", "Submit for verification"]}


def doctor_row(st) -> tuple[str, str, str]:
    """(status, summary, hint) — optional tool, so warn at most, never fail."""
    rec = read(st)
    if rec["preinstall"] == "off":
        return "ok", "Google 工具：主人选择不用 / Google tools: off by the owner's choice", ""
    if asset() is None:
        return "ok", "Google 工具：此平台不预装 / Google tools: not pre-installed on this platform", ""
    if installed(st, rec):
        chk = rec.get("check") or {}
        if chk.get("result") not in (None, "ok"):
            return "warn", f"Google 工具 gog {GOG_VERSION} 自检未通过 / gog self-check failed", "agentj google check"
        return "ok", f"Google 工具 gog {GOG_VERSION}（API 授权 {API_STAGE} 提供）/ gog {GOG_VERSION} (API authorization in {API_STAGE})", ""
    att = rec.get("attempt") or {}
    hint = "agentj google install"
    if att.get("result") == "failed":
        return "warn", f"Google 工具未装好（{att.get('reason', 'failed')}），其余功能照常 / gog not installed; everything else works", hint
    return "warn", "Google 工具尚未预装，其余功能照常 / gog not pre-installed yet; everything else works", hint


# ------------------------------------------------------------------ host side of elevate.sock and the CLI
OPS = ("status", "check", "install", "on", "off", "registry")


def execute(op: str, st, purposes_: list[str] | None = None) -> dict:
    if op == "status":
        return status(st)
    if op == "check":
        return check(st)
    if op == "install":
        return install(st)          # respects "off": the owner turns it back on with `agentj google on`
    if op == "on":
        res = set_preinstall(st, True)
        return {**res, "install": install(st)}
    if op == "off":
        return set_preinstall(st, False)
    if op == "registry":
        return {"ok": True, "entries": registry_entries(st)}
    raise GoogleError("shape", "op: " + " | ".join(OPS))


def _say(res: dict) -> str:
    if "tool" in res:
        t = res["tool"]
        line = {"installed": f"✓ gog {GOG_VERSION} 已预装 / installed", "absent": "· gog 尚未预装 / not pre-installed yet",
                "disabled": "· 主人选择不用 Google 工具 / off by the owner's choice",
                "unsupported_platform": "· 此平台不预装 / not available on this platform"}[t["state"]]
        rows = [line, f"  API 授权：{API_STAGE} 提供（自有项目 + In production + 本机授权）/ API authorization arrives in {API_STAGE}",
                "  网页登录与 API 授权分开检查 / website sign-in is checked separately"]
        ex = res["existing"]
        found = [k for k, v in ex.items() if v["on_path"] or v["config"]]
        if found:
            rows.append("  已有工具（只看是否存在，不读内容）/ already on this computer (presence only): " + ", ".join(found))
        rows.append("  用途 / purposes: " + ", ".join(r["id"] for r in res["purposes"]))
        return "\n".join(rows)
    return json.dumps(res, ensure_ascii=False)


def cmd(a) -> int:
    from . import elevate
    from .state import State
    st = State()
    if a.action == "plan":
        try:
            res = plan(a.purpose or [])
        except GoogleError as e:
            res = {"ok": False, "why": e.reason, "detail": e.detail}
    else:
        res = elevate.client_request(st, {"t": "google", "op": a.action}, timeout=900)
        if res.get("result") == "unavailable":          # serve not running: the CLI owns the state itself
            try:
                if a.action == "install":
                    res = install(st, out=(lambda s: None) if a.json else (lambda s: print(s, file=sys.stderr)))
                else:
                    res = execute(a.action, st)
            except GoogleError as e:
                res = {"ok": False, "why": e.reason, "detail": e.detail}
            except PermissionError:
                res = {"ok": False, "why": "fenced", "detail": "Agent J is not running; ask the owner to start it (agentj service status)"}
    print(json.dumps(res, ensure_ascii=False, indent=None if a.json else 2) if a.json or a.action not in ("status", "check")
          else _say(res))
    return 0 if res.get("ok") else 1


def add_parser(sub) -> None:
    p = sub.add_parser("google", help="Google 工具：预装 gog、只读状态、用途与权限计划 / Google tools: pinned gog, read-only status, purpose plan")
    p.add_argument("action", nargs="?", choices=["status", "check", "install", "plan", "on", "off", "registry"], default="status",
                   help="status 只读 · check 实测 gog · install 预装 · plan 某些用途的建置计划 · on/off 用不用 / "
                        "status read-only · check probes gog · install pre-installs · plan shows a setup plan · on/off use it or not")
    p.add_argument("--purpose", action="append", choices=sorted(PURPOSES), help="plan：用途，可重复 / plan: purpose, repeatable")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd(a)))
