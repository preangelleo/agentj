"""P277: an upgrade that fails can be diagnosed without the owner's terminal.

A Windows user on WSL2 (0.20.1a1 → 0.20.3a1, 2026-10-14): the Agent said 「安装步骤失败」 and nothing else reached us —
no installer output, no support thread, no Dashboard trace. Now every upgrade attempt (manual `update apply`, the phone's
/update, the authorized e-mail path, the nightly / idle worker):

- names the failed step and a finer reason: download_failed · signature_or_hash_mismatch · uv_failed ·
  service_restart_failed (plus the existing not_upgraded / doctor_failed / …);
- keeps the installer's last ~20 stderr lines, redacted here first (privacy layer 1: keys, tokens, e-mails, phones, home
  paths, this host's and user's names; then any long opaque token shape) — the same text in UPGRADE_RESULT, in the
  support report and nowhere else (never in the heartbeat);
- is remembered as the host's last upgrade (`last-upgrade.json`, 0600): from / to / result / reason / at / step / tail — the
  heartbeat sends only from / to / result / reason / at to the Dashboard;
- after a failure, unless the owner switched it off (`updates.report_failures`, default on), opens one support thread
  (`agentj support ask` kind bug, `--attach-doctor` equivalent): metadata and the redacted tail only, once per
  (target, reason) a day. The block says in one plain sentence what happened and whether support has it.
"""
from __future__ import annotations

import errno
import io
import json
import os
import platform
import re
import subprocess
import sys
import time

from . import __version__

LAST = "last-upgrade.json"
TAIL_LINES = 20
LINE_MAX = 240
TAIL_MAX = 3000
REPORT_ENV = "AGENTJ_UPGRADE_REPORT"       # "off": never report (tests; CI)
REPORT_EVERY = 24 * 3600                   # the same failure (REPORT_KEY) of this host is reported at most once a day
REPORT_KEY = ("from", "to", "reason", "subreason")
# The updater refusing its own store (modes it wrote itself): the nightly retry of a stuck host must not open a ticket a
# day. Integrity findings (hash / size / signature mismatch) are never in this set and keep the daily report.
SELF_INFLICTED = frozenset(("cache_entry_invalid", "unsafe_store"))
SELF_INFLICTED_EVERY = 7 * 24 * 3600


def report_every(rec) -> int:
    own = rec.get("reason") == "install_artifact_changed" and rec.get("subreason") in SELF_INFLICTED
    return SELF_INFLICTED_EVERY if own else REPORT_EVERY
RESULTS = ("ok", "failed", "rolled_back")
REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
STEPS = ("check", "download", "install", "verify", "service", "health")
HASHY = {"artifact_hash_mismatch", "artifact_signature_mismatch", "artifact_identity_mismatch"}
# opaque token shapes privacy layer 1 does not know by prefix: ≥ 32 url-safe chars mixing letters and digits (a 64-hex
# SHA-256 is kept: it names a public artifact and is what support needs to compare)
_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")
_HEX = re.compile(r"^[0-9a-f]{32,128}$")
# auto_update's reasons → the P277 categories (its own reason text stays as it was in the phone notice)
CATEGORY = {"install_failed": "uv_failed", "restart_failed": "service_restart_failed", "version_mismatch": "not_upgraded",
            "artifact_hash_mismatch": "signature_or_hash_mismatch", "artifact_signature_mismatch": "signature_or_hash_mismatch",
            "artifact_identity_mismatch": "signature_or_hash_mismatch", "artifact_refused": "signature_or_hash_mismatch",
            "upgrade_self_check_failed": "doctor_failed", "service_failed": "service_restart_failed",
            "URLError": "download_failed", "HTTPError": "download_failed", "TimeoutError": "download_failed",
            "ConnectionError": "download_failed", "ConnectionResetError": "download_failed", "OSError": "os_error",
            "CalledProcessError": "uv_failed", "SubprocessError": "uv_failed", "FileNotFoundError": "installer_missing",
            "PermissionError": "permission_denied", "TimeoutExpired": "installer_timeout",
            "artifact_too_large": "download_failed", "insecure_update_redirect": "download_failed"}


# #166/#192: fixed needles in the installer's (lower-cased) output → a finer reason than uv_failed; first match wins, so
# the Python-version refusal comes before the general "no solution" one.
UV_STDERR = (
    ("disk_full", ("no space left on device", "os error 28", "disk quota exceeded", "os error 122")),
    ("python_incompatible", ("does not satisfy python", "requires-python", "requires python", "python version is not supported")),
    ("python_missing", ("no interpreter found", "no python installation", "failed to find a python", "python downloads are not allowed")),
    ("dependency_conflict", ("no solution found when resolving", "unsatisfiable", "resolutionimpossible")),
    ("dependency_download_failed", ("failed to fetch", "error sending request", "failed to download", "dns error",
                                    "connection refused", "connection reset", "network is unreachable", "tcp connect error",
                                    "certificate", "request failed after", "operation timed out",
                                    # 0334: resolver / pip (pipx) spellings of the same network failures
                                    "failed to lookup address", "temporary failure in name resolution",
                                    "name or service not known", "nodename nor servname", "failed to establish a new connection",
                                    "read timed out")),
)


# ------------------------------------------------------------------ 0334: dependency failure evidence (fixed fields only)
# A failed dependency install is described by five closed fields, parsed here from the installer's raw output and the
# installer's environment — never the URL, host name, credential, path or any stderr text itself.
DEP_PHASES = ("resolve", "download", "build", "install")
# 0370: which part of the dependency preparation the time went to — choosing the files (network), fetching one, the
# offline check of the finished set, the verified uv / interpreter of a repair (downloads), or a repair's local helper
# (tag selection, interpreter lookup). With whole seconds and file counts these tell a slow link from a local stall;
# nothing here names a host, a path or an account.
PREPARE_STEPS = ("select", "fetch", "probe", "runtime", "python", "local")
PREPARE_COUNTS = {"step_seconds": 9999, "prepare_seconds": 9999, "wheels_fetched": 999, "wheels_needed": 999}
DEP_SOURCES = ("pypi", "mirror", "proxy", "unknown")
UV_CLASSES = ("dns", "connect", "timeout", "tls", "http_status", "other")
# More specific than a transport symptom. Only these words may leave the host.
_RESOLVE_HINTS = (
    ("uv_option", ("unexpected argument", "unrecognized option", "unrecognized arguments", "unrecognized subcommand",
                   "invalid value 'pylock.toml'", "invalid value for --format", "wasn't expected", "unknown option")),
    ("installer_shim", ("uv is not a valid shim", "no version is set for command uv",
                        "no version set for command uv", "pyenv: uv: command not found",
                        "pyenv: uv: no such command")),
    ("requires_python", ("requires-python", "requires python", "does not satisfy python", "python version is not supported")),
    ("interpreter_missing", ("no interpreter found", "no python installation", "failed to find a python",
                             "failed to inspect python interpreter", "python downloads are not allowed")),
    ("wheel_tags", ("matching platform tag", "matching python implementation tag", "matching python abi tag",
                    "compatible wheel", "unsupported wheel", "not compatible with the current platform")),
    ("yanked", ("yanked",)),
    ("temp_directory", ("failed to create a temporary directory", "failed to create temporary directory",
                        "failed to create temp directory")),
    ("cache", ("cache entry", "cache directory", "failed to initialize cache", "failed to read cache")),
    ("version_conflict", ("no solution found", "unsatisfiable", "no matching distribution", "resolutionimpossible")),
)
RESOLVE_DETAILS = frozenset(name for name, _ in _RESOLVE_HINTS) | {
    "unrecognized", "wheelhouse_incomplete", "resolver_output_invalid", "tool_receipt_invalid", "local_prepare_error",
}

def resolve_detail(text):
    low = bound_evidence(text).lower()
    # uv also reports only ENOTDIR + its randomized temporary path. Compare locally;
    # neither the configured directory nor the path from stderr becomes metadata.
    if "not a directory" in low or "permission denied" in low or "no such file or directory" in low:
        for key in ("TMPDIR", "TMP", "TEMP"):
            parent = os.environ.get(key)
            if parent and (os.path.join(parent, ".tmp").lower() in low):
                return "temp_directory"
    return next((name for name, phrases in _RESOLVE_HINTS if any(p in low for p in phrases)), "unrecognized")

RETRY_MAX = 9
_PKG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# agentj's dependency closure (pyproject + their install-time requirements on every supported platform): the only names the
# metadata-only form may carry (a shape alone is not a closed vocabulary). test_0334 ties it to pyproject.
DEPENDENCY_NAMES = frozenset(("cryptography", "cffi", "pycparser", "typing-extensions", "websockets", "python-socks",
                              "async-timeout", "pypinyin", "tomlkit", "json5", "pypdf", "segno", "importlib-metadata",
                              "zipp", "zxing-cpp"))
_UV_CLASS_NEEDLES = (
    ("dns", ("dns error", "failed to lookup address", "name or service not known", "temporary failure in name resolution",
             "nodename nor servname", "no such host", "name resolution")),
    ("tls", ("certificate", "tls handshake", "ssl", "invalid peer")),
    ("timeout", ("timed out", "network timeout", "read timeout", "connect timeout", "timeout error", "deadline has elapsed")),
    ("http_status", ("http status", "status code", "403 forbidden", "404 not found", "502 bad gateway",
                     "503 service unavailable")),
    ("connect", ("connection refused", "connection reset", "connection closed", "connection aborted", "tcp connect error",
                 "network is unreachable", "failed to establish a new connection", "broken pipe")),
)
_IN_TICKS = re.compile(r"(?i)failed to (download and build|download|build|install|fetch|prepare)[^`\n]{0,40}`([^`\n]{1,400})`")
_NOT_FOUND = re.compile(r"(?i)because ([A-Za-z0-9][A-Za-z0-9._-]{0,63}) was not found in the package registry")
_PIP_REQ = re.compile(r"(?i)(?:satisfies the requirement|matching distribution found for) ([A-Za-z0-9][A-Za-z0-9._-]{0,63})")
EVIDENCE_MAX = 64 * 1024         # 0334 r1 (F6): only the last 64 KiB of installer output is parsed, each line ≤ 4 KiB
EVIDENCE_LINE_MAX = 4096
_URL_TAIL = re.compile(r"[^\s`'\")]{1,2048}")
INDEX_ENV = ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS",
             "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL")
PROXY_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
PYPI_HOSTS = frozenset(("pypi.org", "files.pythonhosted.org", "pypi.python.org"))
_CFG_URL = re.compile(r"(?m)^\s*(?:index-url|extra-index-url|url|find-links)\s*=\s*\[?\s*[\"']([^\"']+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_ok(value) -> bool:
    """A package-name-shaped token only (never a URL / host / path / opaque token)."""
    return (isinstance(value, str) and bool(_PKG.fullmatch(value)) and not _TOKEN.fullmatch(value)
            and ".." not in value)


def _urls(text: str):
    """URLs by `://` anchors — linear: a bounded scheme backwards, a bounded run forwards (no backtracking regex scan)."""
    i = text.find("://")
    while i != -1:
        j = i
        while j > 0 and i - j < 32 and (text[j - 1].isalnum() or text[j - 1] in "+.-"):
            j -= 1
        m = _URL_TAIL.match(text, i + 3)
        if j < i and m:
            yield text[j:m.end()]
        i = text.find("://", i + 3)


def _hosts(text: str) -> set:
    from urllib.parse import urlsplit
    out = set()
    for u in _urls(text):
        try:
            h = urlsplit(u).hostname
        except ValueError:
            continue
        if h:
            out.add(h.lower())
    return out


def bound_evidence(raw) -> str:
    if isinstance(raw, bytes):
        raw = raw[-EVIDENCE_MAX:].decode("utf-8", "replace")
    raw = raw[-EVIDENCE_MAX:] if isinstance(raw, str) else ""
    return "\n".join(ln[:EVIDENCE_LINE_MAX] for ln in raw.split("\n"))


def _name_from_ref(ref: str):
    """`pydantic-core==2.27.1` / `pkg @ file:///…` / a distribution URL → the distribution name, or None."""
    ref = ref.strip()
    if " @ " in ref:
        return ref.split(" @ ", 1)[0]
    if "://" in ref:
        from urllib.parse import unquote, urlsplit
        try:
            path = unquote(urlsplit(ref).path or "")
        except ValueError:
            return None
        m = re.search(r"/simple/([^/]+)/?$", path)
        if m:
            return m.group(1)
        leaf = path.rstrip("/").rsplit("/", 1)[-1]
        if leaf.endswith(".whl"):
            return leaf.split("-", 1)[0]
        for ext in (".tar.gz", ".zip", ".tar.bz2", ".tgz"):
            if leaf.endswith(ext):
                return leaf[: -len(ext)].rsplit("-", 1)[0]
        return None
    return re.split(r"[=<>~!@ ;\[(,]", ref, maxsplit=1)[0]


def dependency_package(text: str):
    """The first dependency the installer named as failing, normalised (PEP 503) — or None. Raw text stays here."""
    if not isinstance(text, str):
        return None
    hosts = _hosts(text)
    cands = [_name_from_ref(m.group(2)) for m in _IN_TICKS.finditer(text)]
    cands += [m.group(1) for rx in (_NOT_FOUND, _PIP_REQ) for m in rx.finditer(text)]
    for cand in cands:
        if not cand or not package_ok(cand) or cand.lower() in hosts:
            continue
        cand = _norm(cand)
        if cand != "agentj" and package_ok(cand):
            return cand
    return None


def uv_failure_class(text: str) -> str:
    low = text.lower() if isinstance(text, str) else ""
    for cls, needles in _UV_CLASS_NEEDLES:
        if any(n in low for n in needles):
            return cls
    return "other"


def dependency_phase(text: str):
    low = text.lower() if isinstance(text, str) else ""
    if "no solution found" in low or "was not found in the package registry" in low or "resolving dependencies" in low \
            or "could not find a version that satisfies" in low:
        return "resolve"
    if "failed to build" in low or ("download and build" in low and uv_failure_class(low) == "other"):
        return "build"
    m = re.search(r"failed to fetch[^`\n]{0,40}`([^`\n]{1,400})`", low)
    if m and "/simple/" in m.group(1):
        return "resolve"            # the index page: still choosing versions
    if "failed to download" in low or "failed to fetch" in low or "download and build" in low:
        return "download"
    if "failed to install" in low or "failed to prepare" in low:
        return "install"
    return None


def _config_urls(env) -> list:
    """Index URLs from uv's user / system configuration files (read only; never leaves the host)."""
    if env.get("UV_NO_CONFIG"):
        return []
    files = []
    if env.get("UV_CONFIG_FILE"):
        files.append(env["UV_CONFIG_FILE"])
    else:
        home = env.get("HOME") or os.path.expanduser("~")
        files.append(os.path.join(env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "uv", "uv.toml"))
        for d in (env.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":"):
            if d:
                files.append(os.path.join(d, "uv", "uv.toml"))
        files.append("/etc/uv/uv.toml")
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                out += _CFG_URL.findall(fh.read(65536))
        except OSError:
            continue
    return out


def source_category(env=None) -> str:
    """pypi / mirror / proxy / unknown — from the configured index hosts and whether a proxy variable is set."""
    from urllib.parse import urlsplit
    env = dict(os.environ if env is None else env)
    try:
        urls = []
        for k in INDEX_ENV:
            for part in re.split(r"[\s,]+", env.get(k) or ""):
                if "://" in part:
                    urls.append(part.split("=", 1)[1] if "=" in part.split("://", 1)[0] else part)
        urls += _config_urls(env)
        hosts = set()
        for u in urls:
            if "://" not in u:
                continue            # a local find-links directory is not a network source
            h = urlsplit(u).hostname
            if not h:
                return "unknown"
            hosts.add(h.lower())
    except Exception:  # noqa: BLE001 — cannot tell
        return "unknown"
    if hosts - PYPI_HOSTS:
        return "mirror"
    if any(env.get(k) for k in PROXY_ENV):
        return "proxy"
    return "pypi"


def dependency_evidence(raw, env=None) -> dict:
    """The installer's raw output (stdout + stderr, never stored) → the closed dependency fields."""
    raw = bound_evidence(raw)
    try:
        phase, symptom = dependency_phase(raw), uv_failure_class(raw)
        detail = resolve_detail(raw) if phase == "resolve" and symptom == "other" else None
        return dependency_fields({"dependency_package": dependency_package(raw), "dependency_phase": phase,
                                  "dependency_source": source_category(env), "uv_failure_class": symptom,
                                  "uv_resolve_detail": detail})
    except Exception:  # noqa: BLE001 — evidence never changes the upgrade's own outcome
        return {}


DEPENDENCY_KEYS = ("dependency_package", "dependency_phase", "dependency_source", "uv_failure_class", "uv_resolve_detail", "retry_count",
                   "prepare_step", *PREPARE_COUNTS)


def dependency_fields(res) -> dict:
    """Project only valid values: one of agentj's own dependency names (anything else parsed → `other`; 0334 r1 F2/F5 — a
    package-shaped word can still be a host, an IP, a private package or a short credential), closed enums, a bounded int.
    Every output (result, last-upgrade.json, support bodies, result_block, phone result, host record) goes through here."""
    out = {}
    if not isinstance(res, dict):
        return out
    pkg = res.get("dependency_package")
    if isinstance(pkg, str) and pkg:
        out["dependency_package"] = pkg if pkg in DEPENDENCY_NAMES else "other"
    for key, allowed in (("dependency_phase", DEP_PHASES), ("dependency_source", DEP_SOURCES),
                         ("uv_failure_class", UV_CLASSES), ("uv_resolve_detail", RESOLVE_DETAILS)):
        if isinstance(res.get(key), str) and res[key] in allowed:
            out[key] = res[key]
    value = res.get("retry_count")
    if type(value) is int and 0 <= value <= RETRY_MAX:
        out["retry_count"] = value
    if res.get("prepare_step") in PREPARE_STEPS:
        out["prepare_step"] = res["prepare_step"]
    for key, most in PREPARE_COUNTS.items():
        if type(res.get(key)) is int and 0 <= res[key] <= most:
            out[key] = res[key]
    return out


def category(reason) -> str:
    if isinstance(reason, OSError) and reason.errno == errno.EROFS:
        return "read_only_filesystem"
    r = type(reason).__name__ if isinstance(reason, BaseException) else str(reason or "unknown")
    r = CATEGORY.get(r, r)
    return r if REASON_RE.fullmatch(r) else "unknown"


def installer_reason(exc=None, stderr="") -> str:
    """Fixed reasons from errno or installer output; never expose exception messages/argv."""
    if isinstance(exc, (FileNotFoundError, PermissionError, subprocess.TimeoutExpired)):
        return category(exc)
    text = stderr.lower() if isinstance(stderr, str) else ""
    if getattr(exc, "errno", None) == errno.EROFS or "read-only file system" in text or "os error 30" in text:
        return "read_only_filesystem"
    if "permission denied" in text or "os error 13" in text:
        return "permission_denied"
    # #166/#192: a uv / pipx exit with output is named by what the output says, not one catch-all
    for reason, needles in UV_STDERR:
        if any(n in text for n in needles):
            return reason
    reason = category(exc) if exc is not None else "uv_failed"
    return "uv_failed" if reason == "unknown" else reason


def installer_fields(res):
    """Only known command templates and scalar metadata may leave the host. No owner paths/argv."""
    out = {}
    if res.get("command") in (
        "uv tool install --force <wheel>", "uv tool install --force <wheel> --python <python>",
        "pipx install --force <wheel>", "python -m pip install --no-deps --force-reinstall <wheel>",
    ):
        out["command"] = res["command"]
    if type(res.get("exit")) is int:
        out["exit"] = res["exit"]
    if res.get("exception") in CATEGORY:
        out["exception"] = res["exception"]
    return out


# Fixed exception evidence only: no message, traceback, filenames or command arguments.
EXCEPTION_TYPES = frozenset((
    "Exception", "OSError", "FileNotFoundError", "PermissionError", "TimeoutError",
    "ConnectionError", "ConnectionResetError", "BrokenPipeError", "BlockingIOError",
    "IsADirectoryError", "NotADirectoryError", "InterruptedError", "ProcessLookupError",
    "ChildProcessError", "FileExistsError", "SubprocessError", "CalledProcessError",
    "TimeoutExpired", "ValueError", "TypeError", "KeyError", "AttributeError",
    "URLError", "HTTPError", "BadZipFile", "MinisignError", "UnicodeDecodeError"))


def exception_fields(res):
    """Project only allowlisted classes/stages, OS errno and Unix UTC seconds."""
    out = {}
    for key, allowed in (("exception_type", EXCEPTION_TYPES), ("failure_stage", STEPS)):
        value = res.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
    value = res.get("errno")
    if type(value) is int and value in errno.errorcode:
        out["errno"] = value
    value = res.get("failure_at")
    if type(value) is int and 0 <= value < 253402300800:  # UTC through year 9999
        out["failure_at"] = value
    return out


def exception_evidence(exc, stage):
    return exception_fields({"exception_type": type(exc).__name__ if type(exc).__name__ in EXCEPTION_TYPES else "Exception",
                             "errno": getattr(exc, "errno", None), "failure_stage": stage,
                             "failure_at": int(time.time())})


def plan_reason(e: BaseException) -> str:
    """verified_plan's exception → the result reason. Getting the bytes (network, HTTP, timeouts, a redirect refused, too
    large) is download_failed; the installation kind auto-update cannot handle is unsupported_install; every other refusal
    of the bytes (hash, minisign, wheel identity, a broken archive) is signature_or_hash_mismatch."""
    import http.client
    if isinstance(e, (OSError, http.client.IncompleteRead)) or (isinstance(e, ValueError) and str(e) in ("artifact_too_large", "insecure_update_redirect")):
        return "download_failed"
    if isinstance(e, ValueError) and str(e) == "unsupported_install":
        return "unsupported_install"
    return "signature_or_hash_mismatch"


# P586: immutable metadata vocabulary, never arbitrary exception messages or receipt content.
SUBREASONS = frozenset(('truncated', 'hash_mismatch', 'signature_invalid', 'untrusted_key', 'comment_mismatch',
                       'identity_mismatch', 'bad_zip', 'cache_receipt_invalid', 'internal_error',
                       'unsafe_store', 'cache_entry_invalid', 'cache_entry_missing', 'size_mismatch',
                       'invalid_manifest', 'selection_mismatch'))
ARTIFACT_STAGES = frozenset(('wheel', 'sha256', 'signature', 'identity', 'cache', 'install_store',
                            'dependency_cache', 'dependency_lock', 'dependency_wheel', 'dependency_resolver'))
PLAN_CODES = frozenset(('artifact_hash_mismatch', 'artifact_signature_mismatch', 'artifact_identity_mismatch',
                        'artifact_too_large', 'insecure_update_redirect', 'unsupported_install',
                        'upgrade_cache_unreadable', 'bad_version'))
PLAN_EXCEPTIONS = frozenset(('ValueError', 'MinisignError', 'BadZipFile', 'KeyError', 'TypeError', 'AttributeError',
                            'UnicodeDecodeError', 'URLError', 'HTTPError', 'TimeoutError', 'OSError',
                            'ConnectionError', 'ConnectionResetError', 'FileNotFoundError', 'PermissionError'))


def artifact_fields(res):
    """Only bounded enums and public artifact SHA256 digests may leave the verification boundary."""
    out = {}
    for key, allowed in (('subreason', SUBREASONS), ('artifact_stage', ARTIFACT_STAGES),
                         ('cache_status', ('miss', 'invalid'))):
        value = res.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
    for key in ('artifact_sha256', 'stated_sha256'):
        value = res.get(key)
        if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value):
            out[key] = value
    value = res.get('download_attempts')            # #475: bounded same-target retries actually made
    if type(value) is int and 1 <= value <= 9:
        out['download_attempts'] = value
    if res.get('owner_kind') in OWNER_KINDS:        # #475: never the paths or the account name
        out['owner_kind'] = res['owner_kind']
    for key, allowed in CACHE_FIELDS.items():        # #0307: the kind of cache folder that refused and who owns it
        if isinstance(res.get(key), str) and res[key] in allowed:
            out[key] = res[key]
    return out


def artifact_exception_fields(error):
    return artifact_fields(getattr(error, 'artifact_diagnostic', {}))


CACHE_FIELDS = {'cache_path': ('xdg', 'home'), 'cache_level': ('cache_dir', 'upgrade_root', 'cache_base', 'file'),
                'cache_owner': ('self', 'root', 'other'), 'cache_fallback': ('tmp',)}


OWNER_KINDS = ('root', 'other')


def plan_fields(e):
    evidence = getattr(e, 'artifact_diagnostic', {})
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    evidence.setdefault('download_attempts', getattr(e, 'download_attempts', None))     # #475
    out = artifact_fields(evidence)
    import http.client
    if isinstance(e, http.client.IncompleteRead):
        out['subreason'] = 'truncated'
    if 'subreason' not in out:
        codes = {'artifact_hash_mismatch': 'hash_mismatch', 'artifact_signature_mismatch': 'comment_mismatch',
                 'artifact_identity_mismatch': 'identity_mismatch', 'upgrade_cache_unreadable': 'cache_receipt_invalid'}
        out['subreason'] = codes.get(str(e), 'internal_error') if isinstance(e, ValueError) else 'internal_error'
    return out


def plan_detail(e: BaseException) -> str:
    """Fixed exception classes / codes only; no exception text, paths, URLs or receipt content."""
    code = getattr(e, 'code', None)
    name = type(e).__name__
    name = name if name in PLAN_EXCEPTIONS else 'Exception'
    if isinstance(e, ValueError) and str(e) in PLAN_CODES:
        return f"{name}: {e}"
    return name + (f" HTTP {code}" if type(code) is int and 100 <= code <= 599 else '')


def redact(text: str, identity=None) -> str:
    from . import privacy
    try:
        host, user = identity if identity is not None else privacy.local_identity()
        out = privacy.redact(text, host, user)
    except Exception:  # noqa: BLE001 — never send unredacted text: on any failure send nothing
        return ""
    return _TOKEN.sub(lambda m: m.group(0) if _HEX.fullmatch(m.group(0)) else "<token>", out)


def tail(text, identity=None) -> str:
    """The last TAIL_LINES non-empty lines (each ≤ LINE_MAX), redacted; '' when there is nothing."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str) or not text.strip():
        return ""
    # Redact the complete input first: truncating can remove a private-key header or split a credential value.
    safe = redact(text.replace("\r", "\n"), identity)
    lines = [ln.rstrip()[:LINE_MAX] for ln in safe.splitlines() if ln.strip()][-TAIL_LINES:]
    return "\n".join(lines)[-TAIL_MAX:]


def run_step(run, cmd, say, catch: bool = True, keep: dict | None = None) -> tuple[int, str]:
    """One installer command: output captured (and still shown through `say`), → (rc, redacted stderr tail). A command
    that cannot start is rc 127 with its exception type (catch=False: the OSError is the caller's). 0334: `keep` (a dict)
    receives the raw output under "raw" for dependency_evidence only — it is never stored or sent."""
    try:
        r = run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except OSError as e:
        if not catch:
            raise
        return 127, f"{type(e).__name__}: {os.path.basename(str(cmd[0]))} could not be started"
    out, err = getattr(r, "stdout", None), getattr(r, "stderr", None)
    if keep is not None:
        keep["raw"] = "\n".join(s for s in (out, err) if isinstance(s, str))
    for s in (out, err):
        if isinstance(s, str) and s.strip():
            for ln in s.rstrip().splitlines()[-200:]:
                say("  " + ln)
    rc = r.returncode
    return rc, (tail(err) or tail(out)) if rc else ""


# ------------------------------------------------------------------ last upgrade (state dir; the heartbeat's source)
def _clean_version(v):
    from .update import parse
    return v if isinstance(v, str) and len(v) <= 32 and parse(v) is not None else None


def entry(res: dict, now: float | None = None) -> dict | None:
    """The record for one finished attempt, or None when nothing was attempted (refusals before the download)."""
    if not isinstance(res, dict) or not res.get("step"):
        return None
    if res.get("result") == "ok":
        result = "ok"
    else:
        result = "rolled_back" if res.get("rollback") == "ok" else "failed"
    rec = {"v": 1, "from": _clean_version(res.get("from")), "to": _clean_version(res.get("to")), "result": result,
           "reason": category(res.get("reason")), "at": int(time.time() if now is None else now)}
    if result == "ok" and type(res.get("retry_count")) is int and 0 < res["retry_count"] <= RETRY_MAX:
        rec["retry_count"] = res["retry_count"]     # 0334: the install succeeded after network retries
    if result != "ok":
        rec["step"] = res["step"] if res.get("step") in STEPS else "install"
        rec.update(installer_fields(res))
        rec.update(dependency_fields(res))
        rec.update(exception_fields(res))
        rec.update(artifact_fields(res))
        if res.get("stderr_tail"):
            rec["stderr_tail"] = str(res["stderr_tail"])[-TAIL_MAX:]
        if res.get("detail"):
            rec["detail"] = str(res["detail"])[:120]
        checks = failed_checks(res)
        if checks:
            rec["failed_checks"] = checks
            if "service" in checks and service_reason(res.get("service_reason")):
                rec["service_reason"] = res["service_reason"]
        rec.update(rollback_fields(res))
    return rec


ROLLBACK_TAIL_MAX = 1200
ROLLBACK_CHECKS = ("version_unchanged",)


def rollback_fields(res) -> dict:
    """#166/#192: why the automatic rollback itself failed — a fixed category, the rollback installer's own redacted tail
    (bounded, kept apart from the first failure's) and, when the installed version was found unchanged, that check's id."""
    out = {}
    if res.get("rollback_failure"):
        out["rollback_failure"] = category(res["rollback_failure"])
    if res.get("rollback_tail"):
        out["rollback_tail"] = str(res["rollback_tail"])[-ROLLBACK_TAIL_MAX:]
    if res.get("rollback_check") in ROLLBACK_CHECKS:
        out["rollback_check"] = res["rollback_check"]
    return out


# R-0045: why the `service` row failed, as a closed word — the doctor sets it, the report carries it; anything else is dropped.
SERVICE_STATES = ("inactive", "failed", "activating", "deactivating", "not_loaded", "unknown")
SERVICE_REASONS = frozenset({*(f"not_active:{s}" for s in SERVICE_STATES), "env_mismatch", "inspect_failed", "legacy_name",
                             "no_manager", "not_installed", "path_missing"})


def service_reason(value) -> str | None:
    return value if isinstance(value, str) and value in SERVICE_REASONS else None


def service_reason_of(rows) -> str | None:
    """The first `service` doctor row's closed reason, or None."""
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("id") == "service":
            return service_reason(r.get("service_reason"))
    return None


def failed_checks(res) -> list:
    """P393 (#121): the self-check ids that blocked the upgrade — fixed ids only (update.CHECK_ID_RE), never a summary."""
    try:
        from .update import check_ids
        return check_ids(res.get("failed_checks")) if isinstance(res, dict) else []
    except Exception:  # noqa: BLE001
        return []


def valid(rec) -> bool:
    return (isinstance(rec, dict) and rec.get("result") in RESULTS and isinstance(rec.get("reason"), str)
            and bool(REASON_RE.fullmatch(rec["reason"])) and type(rec.get("at")) is int and rec["at"] >= 0
            and _clean_version(rec.get("to")) is not None and (rec.get("from") is None or _clean_version(rec["from"]) is not None))


def read(st) -> dict | None:
    try:
        rec = json.loads((st.root / LAST).read_text())
    except (OSError, ValueError):
        return None
    return rec if valid(rec) else None


def write(st, rec: dict) -> bool:
    if not valid(rec):
        return False
    try:
        if not st.exists():
            return False
        old = read(st) or {}
        if old.get("reported") and all(old.get(k) == rec.get(k) for k in REPORT_KEY):
            rec = {**rec, "reported": old["reported"], "reported_at": old.get("reported_at")}
        st.write_private(st.root / LAST, json.dumps(rec, ensure_ascii=False).encode())
        return True
    except Exception:  # noqa: BLE001 — remembering never breaks an upgrade
        return False


def remember(st, res: dict, *, fenced: bool = False) -> dict | None:
    """Write the attempt's record (inside the fence: by the host, over elevate.sock `upgrade` op `record`)."""
    rec = entry(res)
    if rec is None:
        return None
    if fenced:
        try:
            from .update import _host
            _host(st, "record", 15, to=rec["to"], rec=rec)
        except Exception:  # noqa: BLE001
            pass
        return rec
    write(st, rec)
    return rec


def heartbeat_field(st) -> dict | None:
    """What the heartbeat carries: from / to / result / reason / at — never the step's text or the stderr tail."""
    rec = read(st)
    if not rec:
        return None
    return {"from": rec.get("from"), "to": rec["to"], "result": rec["result"], "reason": rec["reason"], "at": rec["at"]}


LATE_CHECKS = frozenset(("service", "serve"))     # = auto_update.TRANSIENT_CHECKS (kept here: no import cycle)


def recover_late(st, service_row) -> dict | None:
    """R-0045: the new serve is up and the last attempt failed only because its service / serve row had not settled when an
    older updater (no re-ask) looked once. When that record is for this very version and `service_row()` (doctor's
    check_service, in-process, no network) is ok now, last-upgrade.json becomes `ok / upgraded, recovered: self_check_late`
    so the heartbeat / Dashboard stop showing a failure. Local only: no upgrade_diag path sends an `ok` follow-up (report()
    skips ok results; the metadata-only body cannot name a thread), so nothing goes out. → the new record, or None."""
    rec = read(st)
    if not rec or rec.get("result") != "failed" or rec.get("reason") != "doctor_failed" or rec.get("to") != __version__:
        return None
    checks = rec.get("failed_checks")
    if not isinstance(checks, list) or not checks or not set(checks) <= LATE_CHECKS:
        return None
    try:
        row = service_row()
    except Exception:  # noqa: BLE001 — cannot tell: leave the failure as it is
        return None
    if not isinstance(row, dict) or row.get("id") != "service" or row.get("status") != "ok":
        return None
    new = {"v": 1, "from": rec.get("from"), "to": rec["to"], "result": "ok", "reason": "upgraded", "at": rec["at"],
           "recovered": "self_check_late",
           **{k: rec[k] for k in ("reported", "reported_at") if k in rec}}
    return new if write(st, new) else None


# ------------------------------------------------------------------ automatic support report
def enabled(st) -> bool:
    from . import support_incident
    if not support_incident.enabled(st):
        return False
    if (os.environ.get(REPORT_ENV) or "").lower() == "off":
        return False
    try:
        from . import preferences
        return preferences.get(preferences.effective(st), "updates.report_failures", True) is not False
    except Exception:  # noqa: BLE001 — unreadable preferences: the default (on)
        return True


def _platform() -> str:
    try:
        from .doctor import _is_wsl
        wsl = _is_wsl()
    except Exception:  # noqa: BLE001
        wsl = False
    return f"{sys.platform} {platform.machine()}" + (" WSL2" if wsl else "") + f" · python {sys.version.split()[0]}"


def body(res: dict, kind: str | None = None) -> str:
    rec = entry(res) or {}
    lines = ["[auto] agentj upgrade failed (P277 automatic report — metadata and the redacted installer output only)",
             f"from: {res.get('from') or '?'} · to: {res.get('to') or '?'} · result: {rec.get('result', res.get('result'))}",
             f"reason: {res.get('reason')} · step: {rec.get('step', res.get('step') or '?')}"
             + (f" · rollback: {res['rollback']}" if res.get("rollback") else "")
             + (f" · service: {res['service']}" if res.get("service") else ""),
             f"install: {kind or '?'} · {_platform()}"]
    for key, value in {**installer_fields(res), **artifact_fields(res), **exception_fields(res),
                       **dependency_fields(res)}.items():
        lines.append(f"{key}: {value}")
    checks = failed_checks(res)
    if checks:
        lines.append("failed checks: " + ", ".join(checks))
        if "service" in checks and service_reason(res.get("service_reason")):
            lines.append("service reason: " + res["service_reason"])
    elif category(res.get("reason")) == "doctor_failed":
        lines.append("failed checks: not recorded")
    if res.get("detail"):
        lines.append("detail: " + str(res["detail"])[:120])
    if res.get("stderr_tail"):
        lines += ["installer stderr (last lines, redacted):", str(res["stderr_tail"])]
    rb = rollback_fields(res)
    if rb.get("rollback_failure"):
        lines.append("rollback failure: " + rb["rollback_failure"]
                     + (" · installed version unchanged: still " + str(res.get("from") or "?")
                        if rb.get("rollback_check") == "version_unchanged" else ""))
    if rb.get("rollback_tail"):
        lines += ["rollback installer stderr (last lines, redacted):", rb["rollback_tail"]]
    return "\n".join(lines)


META_HEAD = ("[auto] agentj upgrade failed - metadata only (privacy check layer 2 unavailable: fixed fields, no installer "
             "output, no doctor)")


def _known_reason(v):
    from .update import AUTH_REASONS
    return v in KNOWN_REASONS or v in AUTH_REASONS


KNOWN_REASONS = frozenset({*CATEGORY.values(), *(r for r, _ in UV_STDERR), "uv_failed", "read_only_filesystem",
                           "permission_denied", "rollback_failed", "unknown", "install_artifact_changed",
                           "install_artifact_unavailable", "installer_incompatible", "dependency_preparation_failed"})


def _metadata_check_ids(value: str) -> bool:
    # A shape-only check is not an enum: an arbitrary owner's slug could pass it.
    from .update import DOCTOR_UNNAMED, DOCTOR_UNPARSABLE, UPGRADE_CHECKS
    ids = value.split(", ")
    allowed = UPGRADE_CHECKS | {DOCTOR_UNNAMED, DOCTOR_UNPARSABLE}
    return 0 < len(ids) <= 10 and len(set(ids)) == len(ids) and all(i in allowed for i in ids)


# 贾维斯 ruling (reports/decisions.jsonl ts 1793803157.003, P573 amended): when Jev is unavailable only this fixed-field
# report may leave — version, stage, error code, hash; no path, user name, body or attachment. LOCKED by test_p475:
# every key has a closed-vocabulary check, and support.metadata_ok re-applies the same checks before sending.
META_FIELDS = {
    "from": lambda v: _clean_version(v) == v, "to": lambda v: _clean_version(v) == v,
    "result": lambda v: v in (*RESULTS, "refused"), "reason": _known_reason, "step": lambda v: v in STEPS,
    "failure_stage": lambda v: v in STEPS, "artifact_stage": lambda v: v in ARTIFACT_STAGES,
    "subreason": lambda v: v in SUBREASONS, "cache_status": lambda v: v in ("miss", "invalid"),
    "exception_type": lambda v: v in EXCEPTION_TYPES,
    "failed_checks": _metadata_check_ids,
    # R-0045 — P573 exception approved by 贾维斯 2026-11-16: why the `service` row failed, one closed word (SERVICE_REASONS,
    # the not_active state from a fixed systemd/launchd table) or `unknown`; never a path, a log line or free text.
    "service_reason": lambda v: v in SERVICE_REASONS or v == "unknown",
    "errno": lambda v: v.isdigit() and int(v) in errno.errorcode,
    "artifact_sha256": lambda v: bool(re.fullmatch(r"[0-9a-f]{64}", v)),
    "stated_sha256": lambda v: bool(re.fullmatch(r"[0-9a-f]{64}", v)),
    # #0307 (贾维斯 dispatch 2026-11-10): which kind of cache folder refused the write and whether this account owns it —
    # closed words, never a path or an account name.
    **{k: (lambda allowed: lambda v: v in allowed)(allowed) for k, allowed in CACHE_FIELDS.items()},
    # 0334 (hotfix brief .local/state/next-0.30.34/HOTFIX-upgrade-0.30.25.md, 贾维斯): which dependency, at which phase, from
    # which kind of source, how many retries and which network failure class — closed words / a bounded int; the package
    # name only when it is one of agentj's own dependencies (DEPENDENCY_NAMES), never a URL, host, path or stderr.
    "dependency_package": lambda v: v in DEPENDENCY_NAMES or v == "other",
    "dependency_phase": lambda v: v in DEP_PHASES, "dependency_source": lambda v: v in DEP_SOURCES,
    "uv_failure_class": lambda v: v in UV_CLASSES, "uv_resolve_detail": lambda v: v in RESOLVE_DETAILS,
    "retry_count": lambda v: len(v) == 1 and v in "0123456789",      # ASCII digits only (0334 r2)
    # 0370 (support ticket: a preparation timeout said only "resolve / other"): the step and how long, bounded ASCII digits.
    "prepare_step": lambda v: v in PREPARE_STEPS,
    **{k: (lambda most: lambda v: bool(re.fullmatch(r"[0-9]{1,4}", v)) and int(v) <= most)(most)
       for k, most in PREPARE_COUNTS.items()},
}
META_MAX_LINES = 20                                # = support.META_MAX_LINES (header included)


def meta_line_ok(line: str) -> bool:
    key, sep, value = line.partition(": ")
    check = META_FIELDS.get(key)
    try:
        return bool(sep) and check is not None and bool(check(value))
    except Exception:  # noqa: BLE001 — anything odd is refused
        return False


def metadata_body(res: dict, kind: str | None = None) -> str:
    """#475 / the ruling above: the report that may leave without Jev — only META_FIELDS keys whose value passes its
    closed check (never the installer tail, doctor attachment, platform, command, paths, names or free text)."""
    from .update import DOCTOR_UNNAMED, DOCTOR_UNPARSABLE, UPGRADE_CHECKS
    allowed = UPGRADE_CHECKS | {DOCTOR_UNNAMED, DOCTOR_UNPARSABLE}
    checks = [cid for cid in failed_checks(res) if cid in allowed]
    rec = entry(res) or {}
    fields = {"from": res.get("from"), "to": res.get("to"), "result": rec.get("result"),
              "reason": category(res.get("reason")), "step": rec.get("step"),
              **({"failed_checks": ", ".join(checks)} if checks and category(res.get("reason")) == "doctor_failed" else {}),
              **({"service_reason": service_reason(res.get("service_reason")) or "unknown"}
                 if "service" in checks and category(res.get("reason")) == "doctor_failed" and "service_reason" in res else {}),
              **artifact_fields(res), **exception_fields(res), **dependency_fields(res)}
    lines = [META_HEAD] + [f"{k}: {fields[k]}" for k in META_FIELDS
                           if fields.get(k) is not None and meta_line_ok(f"{k}: {fields[k]}")]
    return "\n".join(lines[:META_MAX_LINES])       # 0334: the later (dependency) keys give way, never the whole report


def report(st, res: dict, *, send=None, now: float | None = None, kind: str | None = None, lang: str = "zh",
           send_meta=None) -> dict:
    """→ {"status": "sent", "thread"} | {"status": "off"} | {"status": "already", "thread"} | {"status": "not_sent", "why"}.
    `send(text) -> (exit, printed)` is the seam (default: support.run_send kind bug, doctor attached, no waiting)."""
    if not isinstance(res, dict) or res.get("result") == "ok" or not res.get("step") or not res.get("to"):
        return {"status": "skipped"}
    if not enabled(st):
        return {"status": "off"}
    now = time.time() if now is None else now
    last = None
    try:
        last = read(st) if st.exists() else None
    except Exception:  # noqa: BLE001
        last = None
    this = entry(res, int(now)) or {}
    if (last and last.get("reported") and all(last.get(k) == this.get(k) for k in REPORT_KEY)
            and 0 <= now - float(last.get("reported_at") or 0) < report_every(this)):
        return {"status": "already", "thread": last["reported"]}
    text = body(res, kind)
    meta = False
    try:
        if send is None:
            from . import support
            buf = io.StringIO()
            rc = support.run_send("bug", text, attach_doctor=True, wait=0, lang=lang, out=buf)
            printed = buf.getvalue()
        else:
            rc, printed = send(text)
        # #475: layer 2 unavailable holds the full report (P573); the fixed-field metadata still reaches support (PR1)
        if rc == 3 and (send is None or send_meta is not None):
            if send_meta is None:
                from . import support
                buf = io.StringIO()
                rc = support.run_send("bug", metadata_body(res, kind), wait=0, lang=lang, out=buf, metadata_only=True)
                printed = buf.getvalue()
            else:
                rc, printed = send_meta(metadata_body(res, kind))
            meta = True
    except Exception as e:  # noqa: BLE001 — a report that cannot go out never hides the upgrade result
        return {"status": "not_sent", "why": type(e).__name__}
    m = re.search(r"st_[A-Za-z0-9_-]{22}", printed or "")
    if rc not in (0, 5) or not m:
        return {"status": "not_sent", "why": "blocked" if rc == 2 else f"exit_{rc}"}
    if last is not None and last.get("to") == res.get("to"):
        try:
            st.write_private(st.root / LAST, json.dumps({**last, "reported": m.group(0), "reported_at": int(now)}).encode())
        except Exception:  # noqa: BLE001
            pass
    return {"status": "sent", "thread": m.group(0), **({"metadata_only": True} if meta else {})}


REASON_WORDS = {
    "download_failed": ("下载安装包失败，网络或 agentj.app 暂时连不上", "the release download failed: no network or agentj.app unreachable"),
    "signature_or_hash_mismatch": ("安装包的签名或校验值对不上，已拒绝安装", "the package's signature or checksum did not match, so it was refused"),
    "installer_incompatible": ("现有的 uv 版本太旧", "the installed uv version does not support the command needed for this update"),
    "installer_missing": ("找不到安装工具，请先恢复 uv / pipx", "the installer is missing; restore uv / pipx first"),
    "permission_denied": ("安装目录权限不足", "the installation directory is not writable"),
    "read_only_filesystem": ("安装位置是只读文件系统", "the installation is on a read-only filesystem"),
    "installer_timeout": ("安装命令超时", "the installer timed out"),
    "os_error": ("安装遇到系统错误", "an operating-system error prevented installation"),
    "uv_failed": ("安装命令执行失败", "the install command failed"),
    "disk_full": ("磁盘空间不足，安装没能写完", "the disk is full, so the installation could not finish"),
    "python_incompatible": ("这台 Agent J 主机上的 Python 版本不符合新版要求", "this computer's Python version does not meet the new version's requirement"),
    "python_missing": ("安装工具找不到可用的 Python", "the installer could not find a usable Python"),
    "dependency_conflict": ("新版的依赖包在这台 Agent J 主机上装不到一起", "the new version's dependencies could not be resolved on this computer"),
    "dependency_preparation_failed": ("更换程序前未能准备安装所需文件", "preparing the files for installation failed before replacing the program"),
    "dependency_download_failed": ("下载依赖包失败，网络或软件源暂时连不上", "downloading the dependencies failed: no network or the package index is unreachable"),
    "upgrade_cache_unreadable": ("本地升级缓存读不出来", "the local upgrade cache could not be read"),
    "install_artifact_changed": ("已校验的安装包在安装前被改动或替换，已停止安装", "the verified package changed or was replaced before installing, so installation stopped"),
    "install_artifact_unavailable": ("Agent J 的私有安装包目录写不进去（磁盘满、配额或权限），什么都没装", "Agent J's private install-artifact folder could not be written (disk full, quota or permissions); nothing was installed"),
    "service_restart_failed": ("新版装好了但后台服务没能重启", "the new version installed but the background service did not restart"),
    "not_upgraded": ("装完后版本没有变成目标版本", "after installing, the version was not the target"),
    "doctor_failed": ("升级后自检没通过", "the post-upgrade self-check failed"),
    "self_check_failed": ("升级后新程序跑不起来", "the new program could not run after the upgrade"),
    "unsupported_install": ("这台Agent J 主机的安装方式不能自动升级", "this installation cannot be upgraded automatically"),
    "service_status_failed": ("读不出后台服务的状态，所以没有开始安装", "the background service's status could not be read, so nothing was installed"),
    "apply_crashed": ("升级程序自身出错，没有装完", "the upgrader itself crashed before finishing"),
    "phone_update_crashed": ("升级任务在收尾时出错，服务可能还没重启", "the upgrade job crashed while finishing; the service may not have restarted"),
}


_DOWNLOAD_SYMPTOM = {
    "dns": ("域名解析失败", "DNS lookup failed"),
    "tls": ("TLS 安全连接失败", "TLS negotiation failed"),
    "timeout": ("请求超时", "the request timed out"),
    "http_status": ("服务器返回了 HTTP 错误", "the server returned an HTTP error"),
    "connect": ("网络连接失败", "the network connection failed"),
}


def preparation_timeout(res: dict):
    """Which kind of preparation ran out of time, before any change to the program — or None (the installer itself).
    network: a step that reads the package host was cut off. local: the offline check or a local helper was cut off.
    budget: the shared time was used up, by whatever (also older records, which do not say where)."""
    if not isinstance(res, dict) or res.get("reason") != "installer_timeout":
        return None
    fields = dependency_fields(res)
    if fields.get("dependency_phase") not in ("resolve", "download") and not (
            "prepare_seconds" in fields and install_unchanged(res)):
        return None
    step = fields.get("prepare_step")
    if step in ("probe", "local"):
        return "local"
    if step and fields.get("step_seconds"):
        return "network"
    return "budget"


def preparation_timed_out(res: dict) -> bool:
    return preparation_timeout(res) is not None


_PREPARATION_TIMEOUT = {
    "network": ("准备依赖包超时，网络太慢或中途卡住", "preparing the dependencies timed out: the network was too slow or stalled"),
    "local": ("核对安装所需文件超时，这台主机当时太忙或磁盘太慢",
              "checking the files for installation timed out: this computer was too busy or its disk too slow"),
    "budget": ("准备安装所需文件超时，规定时间内没能完成", "preparing the files for installation ran out of time"),
}


def dependency_message(res: dict, lang: str) -> str:
    """Explain a dependency fetch with closed fields only, including older records with no symptom."""
    reason = res.get("reason")
    kind = preparation_timeout(res)
    if kind:
        return _PREPARATION_TIMEOUT[kind][lang == "en"]
    if reason == "rollback_failed":
        reason = res.get("install_failure")
    if reason != "dependency_download_failed" and not (
            reason == "uv_failed" and installer_reason(stderr=res.get("stderr_tail")) == "dependency_download_failed"):
        return ""
    en = lang == "en"
    fields = dependency_fields(res)
    pkg = fields.get("dependency_package")
    what = pkg if pkg in DEPENDENCY_NAMES else ("dependencies" if en else "依赖包")
    msg = f"could not download {what}" if en else f"{what}下载失败"
    symptom = _DOWNLOAD_SYMPTOM.get(fields.get("uv_failure_class"))
    if symptom:
        msg += (", " if en else "，") + symptom[en]
    return msg


UPGRADE_REPAIR_GUIDE = "https://agentj.app/docs/install/#upgrade-failed"


def dependency_repair(lang: str, res: dict | None = None) -> str:
    if preparation_timeout(res) in ("local", "budget"):     # not a network failure: do not tell the owner to wait for one
        return (("\n\nAfter the host is idle, enter /update on your phone. If it fails again, follow the host repair guide: "
                 if lang == "en" else "\n\n主机空闲后，在手机输入 /update 重试。若仍失败，请按主机修复说明操作：") + UPGRADE_REPAIR_GUIDE)
    return (("\n\nAfter connectivity returns, try /update on your phone. If it fails again, follow the host repair guide: " if lang == "en" else
             "\n\n网络恢复后，可在手机输入 /update 再试一次。若仍失败，请按主机修复说明操作：") + UPGRADE_REPAIR_GUIDE)


def cache_failed(res) -> bool:
    """The updater refused its own dependency cache or install store before touching the installed version."""
    return (res.get("reason") == "install_artifact_changed" and res.get("artifact_stage") in ("dependency_cache", "install_store")
            and install_unchanged(res))


def cache_repair(lang: str) -> str:
    return ((" Send /update again; if it fails the same way, run the one repair command in the host repair guide: " if lang == "en" else
             "可以再发一次 /update；若仍是同样的结果，按主机修复说明运行那一条修复命令：") + UPGRADE_REPAIR_GUIDE)


def previous_version_text(previous, target, lang):
    if not _clean_version(previous) or previous == target:
        return ""
    return f" (previously {previous})" if lang == "en" else f"（原 {previous}）"


def uv_repaired_sentence(version, lang, previous=None):
    source = previous_version_text(previous, version, lang)
    return (f"Upgraded to {version}{source} and updated uv." if lang == "en" else
            f"已升级到 {version}{source}，同时更新了 uv。")


def uv_repair_failure(res, lang, report=None):
    en = lang == 'en'
    reason = REASON_WORDS.get(res.get('reason'), REASON_WORDS['installer_incompatible'])[en]
    if res.get('reason') == 'dependency_download_failed':
        reason = 'could not download the verified uv update' if en else '无法下载经过验证的 uv 更新包'
    elif res.get('reason') == 'permission_denied':
        reason = 'permission denied when saving the uv update' if en else '没有权限保存 uv 更新包'
    counts = res.get('uv_repair_doctor')
    if isinstance(counts, list) and len(counts) == 3 and all(type(n) is int and n >= 0 for n in counts):
        ok, warn, fail = counts
        doctor = (f"Host doctor: {ok} passed, {warn} warning{'s' if warn != 1 else ''}, {fail} failed." if en else
                  f"主机自检：{ok} 项通过，{warn} 项提醒，{fail} 项失败。")
    else:
        doctor = "Host doctor could not complete." if en else "主机自检未能完成。"
    reported = ''
    if report:
        status = report.get('status')
        if status in ('sent', 'already'):
            reported = " Support received the report." if en else "客服已收到上报。"
        elif status == 'off':
            reported = " Automatic reporting is off." if en else "自动上报已关闭。"
        else:
            reported = " The report could not be sent automatically." if en else "未能自动发给客服。"
    if res.get('reason') == 'dependency_download_failed' and res.get('uv_auto_retry') is True:
        reported += (" Automatic updates are on. The host will retry at the next eligible idle check." if en else
                     "自动升级已开启，主机下次空闲并检查更新时会自动再试。")
    frm = res.get('from') or __version__
    return (f"The installed uv is too old. Automatic repair failed ({reason}). Agent J remains on {frm}. {doctor}{reported} "
            if en else f"现有的 uv 版本太旧。自动修复失败（{reason}），Agent J 主机仍是 {frm}。{doctor}{reported}") + UPGRADE_REPAIR_GUIDE


def install_unchanged(res: dict) -> bool:
    """Journal phase wins; recognise pre-P487 download records without trusting their spurious recover line."""
    if "phase" in res:
        return res["phase"] in (None, "prepared")
    return category(res.get("reason")) in ("download_failed", "signature_or_hash_mismatch", "unsupported_install")


def resolution_failed(res):
    return res.get("dependency_phase") == "resolve" and res.get("reason") in (
        "uv_failed", "dependency_conflict", "python_incompatible", "python_missing", "installer_incompatible",
    )


def resolve_receipt(res, rep, lang):
    en = lang == "en"
    frm = _clean_version(res.get("from")) or "?"
    to = _clean_version(res.get("to")) or "?"
    text = (f"During the update from {frm} to {to}, the installer could not resolve the required packages."
            if en else f"从 {frm} 升级到 {to} 时，安装工具没能确定可用的依赖包。")
    if res.get("result") == "rolled_back" or res.get("rollback") == "ok":
        text += f" The host returned to {frm}." if en else f"主机已恢复到 {frm}。"
    elif install_unchanged(res):
        text += f" The host is still on {frm}." if en else f"主机仍是 {frm}。"
    else:
        text += " The running version is unconfirmed." if en else "尚未确认主机当前运行的版本。"
    status = rep.get("status")
    thread = rep.get("thread")
    if status in ("sent", "already") and isinstance(thread, str) and re.fullmatch(r"st_[A-Za-z0-9_-]{22}", thread):
        if rep.get("metadata_only"):
            text += (f" Agent J support received the version and error category (no logs) ({thread})." if en else
                     f"版本和错误类别（不含日志）已发给 Agent J 客服（{thread}）。")
        elif status == "already":
            text += f" Support already has a report ({thread})." if en else f"客服已有这次故障的报告（{thread}）。"
        else:
            text += f" Support received the redacted failure report ({thread})." if en else f"脱敏故障报告已发给客服（{thread}）。"
    elif status == "off":
        text += " Automatic reporting is off. No report was sent." if en else "自动上报已关闭，未发给客服。"
    elif status == "not_sent":
        text += " The report could not be sent to support." if en else "故障报告未能发给客服。"
    else:
        text += " Delivery to support is unconfirmed." if en else "尚未确认客服是否收到报告。"
    return text + ("\n\nAfter the host is idle, enter /update on your phone. If it fails again, follow the host repair guide: "
                   if en else "\n\n主机空闲后，在手机输入 /update 重试。若仍失败，请按主机修复说明操作：") + UPGRADE_REPAIR_GUIDE


def owner_sentence(res: dict, rep: dict, lang: str = "zh") -> str:
    """One plain sentence for the owner: what failed, where the Agent J host is now, whether support already has it."""
    en = lang == "en"
    if "uv_repair_doctor" in res:
        return uv_repair_failure(res, lang, rep)
    if resolution_failed(res):
        return resolve_receipt(res, rep, lang)
    if res.get("reason") == "not_owner":            # #475: a refusal, nothing to report — the one fix step
        from .upgrade_owner import sentence
        return sentence(res, lang)
    why = REASON_WORDS.get(res.get("reason"), (f"原因 {res.get('reason')}", f"reason {res.get('reason')}"))[1 if en else 0]
    dependency = dependency_message(res, lang)
    why = dependency or why
    to, frm = res.get("to") or "?", res.get("from") or __version__
    if res.get("rollback") == "ok" or install_unchanged(res):
        where = f"still on {frm}, working as before" if en else f"Agent J 主机仍是 {frm}，照常可用"
    elif res.get("service") == "no_systemd":     # P278
        where = (f"{to} is installed, but the Agent J host has no systemd to restart the background service: turn it on as the "
                 "result's fix line says, or restart `agentj serve` where it runs" if en else
                 f"{to} 已经装好，但这台Agent J 主机没有可用的 systemd 来重启后台服务：按结果里的 fix 行开启，或在运行 agentj serve 的地方重新运行一次")
    elif res.get("recovery_blocked") == "revocation_downgrade_blocked":
        from .update import RECOVERY_BLOCKED_WHY
        where = RECOVERY_BLOCKED_WHY[en]
    elif res.get("recover"):
        where = ("the rollback failed too; I will run the recover command in the result to put the previous version back" if en else
                 "回滚也没成功，我会运行结果里的 recover 命令装回原版本")
    else:
        where = ("the running version is unconfirmed; check Service status in Settings → Emergency repair" if en else
                 "尚未确认主机当前运行的版本，请在设置的紧急修复里查看服务状态")
    s = rep.get("status")
    if s in ("sent", "already"):
        tail_ = (f"Agent J support received the report ({rep['thread']})" if en else
                 f"故障报告已自动发给 Agent J 客服（{rep['thread']}）")
        if rep.get("metadata_only"):
            tail_ = (f"the version and error category (no logs) went to Agent J support automatically ({rep['thread']})" if en else
                     f"版本和错误类别（不含日志）已自动发给 Agent J 客服（{rep['thread']}）")
    elif s == "off":
        tail_ = ("automatic reporting is off, so support was not told" if en else "你关闭了自动上报，没有发给客服")
    elif rep.get("why") == "exit_3":     # #460
        tail_ = ("it was not sent to support: the privacy check was unavailable, so I need you to read the text and say yes first"
                 if en else "没有发给客服：隐私检查这会儿用不了，要你先看过原文、同意了我才发")
    else:
        tail_ = ("it could not be sent to support automatically; I can send it with `agentj support ask --attach-doctor`" if en else
                 "没能自动发给客服，我可以用 agentj support ask --attach-doctor 再发")
    own = dependency_repair(lang, res) if dependency else ""
    if cache_failed(res):
        own = cache_repair(lang)
    if res.get("reason") == "installer_incompatible":
        own = (" Follow the host repair guide: " if en else "请按主机修复说明操作：") + UPGRADE_REPAIR_GUIDE
    if res.get("owner_kind") and res.get("fix"):    # #475: the installer hit files another account owns
        from .upgrade_owner import sentence
        own = " " + sentence(res, lang)
    if en:
        return f"The upgrade to {to} did not go through ({why}); {where}; {tail_}.{own}"
    return f"升级到 {to} 没成功（{why}）；{where}；{tail_}。{own}"


NOT_SENT = {  # #460: support.run_send's exit codes in plain words (the receipt's `support:` line)
    "exit_3": " — 上报前的第 2 层隐私检查（Jev）这会儿用不了，按规矩先不发；主人看过原文、同意后可用 agentj support ask --attach-doctor "
              "--owner-confirmed 发 / privacy check layer 2 (Jev) was unavailable, so nothing was sent; it goes out only after the "
              "owner reads the text and says yes (agentj support ask --attach-doctor --owner-confirmed)",
    "exit_6": " — 这台主机没有可用的 Agent J 席位，发不了 / no usable Agent J seat on this host, so it could not be sent",
    "exit_1": " — 客服通道出错，没发出 / the support channel failed, nothing was sent"}


def after_failure(st, res: dict, *, lang: str = "zh", send=None) -> dict:
    """The CLI / phone / worker step after a finished attempt: report a failure and put the support / tell_owner lines into
    the result (result_block prints them). Never raises."""
    try:
        if isinstance(res, dict) and res.get("reason") == "not_owner":     # #475: refused before anything ran
            res["tell_owner"] = owner_sentence(res, {}, lang)
            return res
        if not isinstance(res, dict) or res.get("result") == "ok" or not res.get("step"):
            return res
        kind = None
        try:
            from .update import install_kind
            kind = install_kind()["kind"]
        except Exception:  # noqa: BLE001
            pass
        rep = report(st, res, send=send, kind=kind, lang=lang)
        res["support"] = {"sent": f"sent ({rep.get('thread')})" + (" metadata only" if rep.get("metadata_only") else ""), "already": f"already sent ({rep.get('thread')})",
                          "off": "off (updates.report_failures = false)"}.get(rep.get("status"), f"not_sent ({rep.get('why', '?')}"
                                                                                 + NOT_SENT.get(rep.get("why"), "") + ")")
        res["tell_owner"] = owner_sentence(res, rep, lang)
    except Exception:  # noqa: BLE001
        pass
    return res
