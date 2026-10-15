"""Host upgrades. F14: explicit apply needs no interactive terminal.
F12 codes remain an optional compatibility path; native filesystem limits remain.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import DIST, __version__
from .envcompat import getenv

REPO = "https://github.com/preangelleo/agentj"
SPEC = f"git+{REPO}#subdirectory=host"


def spec_at(version: str | None) -> str:
    """The install spec pinned to the public export's tag `v<version>` (0.10.1+: every export is tagged). A copy installed
    from a pinned tag (or from our site's wheel) would not move with `uv tool upgrade`, so the upgrade names the new tag.
    Unknown / unparsable version → the unpinned `main` spec."""
    if version and parse(version) is not None:
        return f"git+{REPO}@v{version}#subdirectory=host"
    return SPEC
LATEST_URL = "https://raw.githubusercontent.com/preangelleo/agentj/main/host/agentj/__init__.py"
URL_ENV = "AGENTJ_UPDATE_URL"     # tests / mirrors; "off" disables every check
TIMEOUT = 6
MAX_BYTES = 64 * 1024
DAY = 24 * 3600
_VER_RE = re.compile(r'^__version__\s*=\s*["\']([0-9A-Za-z.+-]{1,32})["\']\s*$', re.M)
_PARSE_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?(?:\.dev(\d+))?$")


def parse(v: str) -> tuple | None:
    """PEP 440 subset used by this project: 0.8.0, 0.8.0a1, 0.8.0b2, 0.8.0rc1, 0.8.0.dev3. None = not understood."""
    m = _PARSE_RE.match((v or "").strip())
    if not m:
        return None
    major, minor, patch, pre, pre_n, dev = m.groups()
    rank = {"a": 0, "b": 1, "rc": 2, None: 3}[pre]
    return (int(major), int(minor), int(patch or 0), -1 if dev is not None and pre is None else rank,
            int(pre_n or 0), int(dev) if dev is not None else 1 << 30)


def compare(a: str, b: str) -> int | None:
    """-1 / 0 / 1, or None when either version is not understood."""
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return None
    return (pa > pb) - (pa < pb)


LOOPBACK = ("127.0.0.1", "localhost")


def latest_url() -> str | None:
    u = getenv(URL_ENV) or LATEST_URL
    if u == "off":
        return None
    p = urllib.parse.urlparse(u)
    if p.scheme == "https" or (p.scheme == "http" and p.hostname in LOOPBACK):
        return u
    return None


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect to an unencrypted endpoint (refused before any request). Only HTTPS targets are followed;
    the one exception is the local development origin (http://localhost → http://localhost, see latest_url)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlparse(req.full_url), urllib.parse.urlparse(newurl)
        local = old.scheme == new.scheme == "http" and old.hostname in LOOPBACK and new.hostname in LOOPBACK
        if new.scheme != "https" and not local:
            raise ValueError("insecure_update_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_download(req, *, timeout):
    return urllib.request.build_opener(SecureRedirect()).open(req, timeout=timeout)


def fetch_latest(timeout: float = TIMEOUT) -> tuple[str | None, str]:
    """(version, "ok") or (None, why): "off" · "network" · "http_<code>" · "unparsable"."""
    url = latest_url()
    if not url:
        return None, "off"
    req = urllib.request.Request(url, headers={"user-agent": f"{DIST}/{__version__} (update check)"})
    try:
        with open_download(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        e.close()
        return None, f"http_{e.code}"
    except Exception:  # noqa: BLE001 — offline, DNS, TLS, proxy, timeout: all mean "could not find out"
        return None, "network"
    m = _VER_RE.search(body[:MAX_BYTES].decode("utf-8", "replace"))
    if not m or parse(m.group(1)) is None:
        return None, "unparsable"
    return m.group(1), "ok"


# ------------------------------------------------------------------ how this copy was installed
LEGACY_DIST = "agentjarvis-host"     # ≤ 0.9 distribution name: a tool env under that name is re-installed as agentj


def _uv_tool_name(prefix: str) -> str | None:
    """The name the uv tool was installed under (receipt's first requirement), else the tool folder's name."""
    try:
        import tomllib
        with open(os.path.join(prefix, "uv-receipt.toml"), "rb") as f:
            req = (tomllib.load(f).get("tool") or {}).get("requirements") or []
        if req and isinstance(req[0], dict) and isinstance(req[0].get("name"), str):
            return req[0]["name"]
    except (OSError, ValueError, ImportError, AttributeError):
        pass
    return os.path.basename(os.path.normpath(prefix)) or None


def install_kind(prefix: str | None = None) -> dict:
    """{"kind": "uv" | "pipx" | "checkout" | "pip", "where": path, "legacy": bool, ...}. Decided by files the installers
    leave in the venv (uv-receipt.toml / pipx_metadata.json), else a source checkout (host/ with bin/agentj), else a plain
    pip venv. legacy = the uv / pipx tool is still registered under the ≤ 0.9 name `agentjarvis-host`."""
    prefix = prefix or sys.prefix
    if os.path.isfile(os.path.join(prefix, "uv-receipt.toml")):
        return {"kind": "uv", "where": prefix, "legacy": _uv_tool_name(prefix) == LEGACY_DIST}
    if os.path.isfile(os.path.join(prefix, "pipx_metadata.json")):
        spec, pkg = SPEC, None
        try:
            with open(os.path.join(prefix, "pipx_metadata.json")) as f:
                m = json.load(f).get("main_package") or {}
            if isinstance(m.get("package_or_url"), str) and m["package_or_url"]:
                spec = m["package_or_url"]
            pkg = m.get("package") if isinstance(m.get("package"), str) else None
        except (OSError, ValueError, AttributeError):
            pass
        legacy = pkg == LEGACY_DIST or os.path.basename(os.path.normpath(prefix)) == LEGACY_DIST
        return {"kind": "pipx", "where": prefix, "spec": SPEC if legacy else spec, "legacy": legacy}
    from .agent import source_root
    src = source_root()
    if src and os.path.isfile(os.path.join(src, "bin", "agentj")) and os.path.isfile(os.path.join(src, "pyproject.toml")):
        return {"kind": "checkout", "where": src, "legacy": False}
    return {"kind": "pip", "where": prefix, "legacy": False}


def commands(info: dict, latest: str | None = None) -> list[list[str]]:
    """The upgrade, as argv lists run one after the other (stop at the first failure). With `latest` known, uv / pipx / pip
    install exactly the tag `v<latest>` (spec_at) — the version the check reported, also for a copy pinned to an older tag."""
    k = info["kind"]
    pinned = spec_at(latest) if latest else None
    if k == "uv":
        uv = shutil.which("uv") or "uv"
        if info.get("legacy"):         # registered as agentjarvis-host: `upgrade` cannot rename a tool — reinstall it
            return [[uv, "tool", "uninstall", LEGACY_DIST], [uv, "tool", "install", pinned or SPEC]]
        if pinned:
            return [[uv, "tool", "install", "--force", pinned]]
        return [[uv, "tool", "upgrade", DIST]]
    if k == "pipx":
        px = shutil.which("pipx") or "pipx"
        if info.get("legacy"):
            return [[px, "uninstall", LEGACY_DIST], [px, "install", pinned or SPEC]]
        return [[px, "install", "--force", pinned or info.get("spec") or SPEC]]
    if k == "checkout":
        src = info["where"]
        return [["git", "-C", src, "pull", "--ff-only"], [shutil.which("uv") or "uv", "sync", "--project", src]]
    return [[os.path.join(info["where"], "bin", "python"), "-m", "pip", "install", "--upgrade", pinned or SPEC]]


def verified_plan(st, info, target):
    """Shared /update and nightly published-wheel signature/hash boundary.
    Stage public artifacts outside host secret state so an authorized fenced
    Agent can still upgrade its writable package without accessing host keys.
    """
    from pathlib import Path
    from .state import State
    from . import auto_update
    root = Path(os.environ.get('XDG_CACHE_HOME') or Path.home()/'.cache') / 'agentj-upgrade'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    wheel = auto_update.cached_wheel(State(root), target, require_signature=True)
    return auto_update.install_commands(info, wheel)


def new_argv(info: dict) -> list[str]:
    """How to start the freshly installed agentj after `commands(info)` ran. A legacy uv / pipx reinstall deleted the venv
    this process runs from, so the new `agentj` is looked up on PATH; otherwise this very venv (upgraded in place)."""
    from .service import agentj_argv
    if info.get("legacy"):
        exe = shutil.which("agentj")
        if exe:
            return [exe]
    return agentj_argv()


def command_text(info: dict, latest: str | None = None) -> str:
    return " && ".join(shlex.join(c) for c in commands(info, latest))


def check(timeout: float = TIMEOUT) -> dict:
    """{"current", "latest", "status": "newer" | "current" | "ahead" | "unknown", "why", "install", "command"}."""
    latest, why = fetch_latest(timeout)
    info = install_kind()
    c = compare(latest, __version__) if latest else None
    status = "unknown" if c is None else {1: "newer", 0: "current", -1: "ahead"}[c]
    out = {"current": __version__, "latest": latest, "status": status, "why": why if status == "unknown" else "ok",
           "install": info["kind"], "command": command_text(info, latest if status == "newer" else None)}
    return out


# ------------------------------------------------------------------ what each status means, in plain words (C-10 / S-8)
EXPLAIN = {
    "newer": "有新版本，Agent 自己运行 `agentj update apply`，重启并自检后回报。/ A newer version is available; the Agent upgrades, restarts and self-checks.",
    "current": "已经是最新版，不用做什么。/ Up to date. Nothing to do.",
    "ahead": "这台电脑上的版本比公开仓库里最新的发布版还新。这不是错误，不用做什么：通常是从 agentj.app 下载安装的版本比 GitHub "
             "上的副本先更新了。/ This computer runs a newer build than the public repo's newest release. Not an error, nothing "
             "to do: usually it was installed from agentj.app before the GitHub copy caught up.",
    "unknown": "这次没查到最新版本。这不是错误，不用做什么：多半是网络不通或 GitHub 暂时连不上，过一会儿再运行 `agentj update check` "
               "就行。/ Could not check this time. Not an error, nothing to do: usually there is no network or GitHub could not "
               "be reached; run `agentj update check` again later.",
}
WHY = {"off": "检查已关闭 / checking is switched off (AGENTJ_UPDATE_URL=off)", "network": "网络不通 / no network",
       "unparsable": "看不懂返回的版本号 / the answer had no version"}


# ------------------------------------------------------------------ daily check in `serve` (state: update.json, 0600)
def _rec_path(st):
    return st.root / "update.json"


def read_rec(st) -> dict:
    try:
        d = json.loads(_rec_path(st).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def installed_version() -> str | None:
    """P238: the version on disk right now (the package's own __init__.py, read afresh). After `apply` installs a new copy
    the running serve still says its old __version__ until the recovery worker restarts it when idle."""
    try:
        m = _VER_RE.search(Path(__file__).with_name("__init__.py").read_text("utf-8", "replace")[:MAX_BYTES])
    except OSError:
        return None
    return m.group(1) if m else None


def phone_view(st) -> dict:
    """The phone's ⬆ row (serve's menu answer): running / on disk / latest published. `installed` differs from `current`
    only between an upgrade's install and serve's restart — the row then says it is installed, never 「authorize」 again."""
    out = {"current": __version__, "latest": read_rec(st).get("latest")}
    disk = installed_version()
    if disk and disk != __version__:
        out["installed"] = disk
    return out


def auto_enabled(st) -> bool:
    try:
        return st.config().get("update_check", True) is not False and latest_url() is not None
    except (OSError, ValueError):
        return False


def daily(st, now: float | None = None, fetch=fetch_latest) -> str | None:
    """At most one check per 24 h (persisted, so restarts do not re-check). Returns the phone notice text when a newer
    version shows up that the phones were not told about yet, else None."""
    now = time.time() if now is None else now
    rec = read_rec(st)
    if not auto_enabled(st) or now - float(rec.get("checked") or 0) < DAY:
        return None
    latest, why = fetch()
    rec.update({"checked": int(now), "latest": latest, "why": why})
    note = None
    if latest and compare(latest, __version__) == 1 and rec.get("notified") != latest:
        rec["notified"] = latest
        note = notice_text(latest)
    st.write_private(_rec_path(st), json.dumps(rec).encode())
    return note


def notice_text(latest: str) -> str:
    return (f"Agent J 有新版本 {latest}（本机 {__version__}）。请 Agent 自己运行 `agentj update apply`，重启并自检后回报。/ "
            f"Agent J {latest} is available (current {__version__}); ask the Agent to upgrade, restart and self-check.")


# ------------------------------------------------------------------ apply (human at a terminal only)
class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def preflight(stdin=None, stdout=None, prefix: str | None = None) -> None:
    """Only actual filesystem permissions can refuse an explicit apply."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if not os.access(prefix or sys.prefix, os.W_OK):
        raise Refused("read_only")


REFUSED = {
    "read_only": "程序目录不可写，Agent 先检查当前权限和安装位置。/ Program directory is not writable; inspect permissions and installation location.",
}


# ------------------------------------------------------------------ authorized apply (F12, contract C1 / C3)
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_RE = re.compile(r"^AJUP-[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}$")
PLACEHOLDER = "AJUP-XXXXX-XXXXX-XXXXX-XXXXX"          # the dry-run preview's stand-in (C4): never a real code
_SEP = r"[\s\-‐-―­]*"                  # spaces, line breaks, hyphens and dashes a mail client may put in
_CODE_FIND = re.compile(r"AJUP" + _SEP + _SEP.join([r"([0-9A-Z]{5})"] * 4) + r"(?![0-9A-Z])", re.I)
_TARGET_FIND = re.compile(r"(?:目标版本|target\s+version)\s*[:：]\s*v?(\d{1,4}\.\d{1,4}(?:\.\d{1,4})?(?:(?:a|b|rc)\d{1,4})?(?:\.dev\d{1,6})?)",
                          re.I)
MAX_MAIL = 256 * 1024
UPGRADED = "upgraded.json"        # serve's post-restart notice (state dir, 0600)
AUTH_REC = "upgrade-auth.json"    # the authorization this host spent: version + SHA-256 of the code, never the code
MARKER_TTL = 14 * DAY


def normalise_code(text) -> str | None:
    """`AJUP-…` in any case, with spaces / hyphens anywhere → the canonical code (C1), or None. Crockford reading: O → 0,
    I / L → 1 (the alphabet has none of them, so this only fixes a misread); U is never valid."""
    if not isinstance(text, str) or len(text) > 200:
        return None
    m = _CODE_FIND.fullmatch(text.strip())
    if not m:
        return None
    groups = [g.upper().translate(str.maketrans("OIL", "011")) for g in m.groups()]
    code = "AJUP-" + "-".join(groups)
    return code if CODE_RE.fullmatch(code) and code != PLACEHOLDER else None


def parse_email(text: str) -> dict:
    """The pasted upgrade email (zh or en, any client's formatting) → {"code", "version", "problem"}. problem: None ·
    "no_code" · "ambiguous_code" (two different codes) · "ambiguous_version". version is None when no target line was found
    (then --version or the latest release decides)."""
    codes, versions = set(), set()
    for m in _CODE_FIND.finditer(text or ""):
        c = normalise_code(m.group(0))
        if c:
            codes.add(c)
    for m in _TARGET_FIND.finditer(text or ""):
        v = m.group(1)
        if parse(v) is not None:
            versions.add(v)
    problem = ("no_code" if not codes else "ambiguous_code" if len(codes) > 1 else
               "ambiguous_version" if len(versions) > 1 else None)
    return {"code": next(iter(codes)) if len(codes) == 1 else None,
            "version": next(iter(versions)) if len(versions) == 1 else None, "problem": problem}


def read_email(src: str, stdin=None) -> str:
    """`--from-email <file>` / `-` (stdin). Bounded; undecodable bytes are replaced, never an error."""
    if src == "-":
        data = (stdin or sys.stdin).read(MAX_MAIL + 1)
        data = data.encode("utf-8", "replace") if isinstance(data, str) else data
    else:
        with open(os.path.expanduser(src), "rb") as f:
            data = f.read(MAX_MAIL + 1)
    if len(data) > MAX_MAIL:
        raise ValueError("too_large")
    return data.decode("utf-8", "replace")


def writable(prefix: str | None = None) -> bool:
    """Can this copy of agentj be replaced from here? (Inside the fence its venv and package are read-only.)"""
    if not os.access(prefix or sys.prefix, os.W_OK):
        return False
    info = install_kind(prefix)
    return info["kind"] != "checkout" or os.access(info["where"], os.W_OK)


def _code_hash(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode()).hexdigest()


def _spent_here(st, code: str, version: str) -> bool:
    """This host already spent this code for this version (a rerun after a failed install or a restart that cut it short)."""
    try:
        d = json.loads((st.root / AUTH_REC).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("version") == version and d.get("code_sha256") == _code_hash(code)


NO_MANAGER_EXIT = 78    # = service.NO_MANAGER_EXIT: the new CLI's `service install --deferred` found no systemd (P278)
NO_MANAGER_WHY = ("新版已装好，但这台电脑没有可用的 systemd，后台服务没法自动重启接管；没有回滚（回滚也一样没法重启）。按 fix 行开启 "
                  "systemd，或在运行 `agentj serve` 的终端里 Ctrl-C 后重新运行一次",
                  "the new version is installed, but this computer has no usable systemd, so the background service could not "
                  "restart on it; no rollback (the old version could not restart either). Turn systemd on as the fix line says, or "
                  "Ctrl-C the terminal running `agentj serve` and run it again")


def no_manager(tail: str) -> dict:
    """P278: the result fields for 'installed, but no systemd to restart the service': which case (from the new CLI's own
    words) + its fix line; never a rollback."""
    from . import service
    reason = next((r for r in ("wsl_systemd_restart", "wsl_systemd_off", "no_systemd") if service.MESSAGES[r][:24] in (tail or "")), None)
    reason = reason or service.systemd_problem() or "no_systemd"
    reason = reason if reason in service.NO_MANAGER else "no_systemd"
    return {"service": "no_systemd", "rollback": "skipped", "detail": reason, "fix": service.MESSAGES[reason], "stderr_tail": tail}


def write_marker(st, frm: str, to: str, now: float | None = None) -> None:
    st.write_private(st.root / UPGRADED, json.dumps({"from": frm, "to": to, "at": int(time.time() if now is None else now)}).encode())


PENDING = "upgrade-pending.json"   # P142 (A12/B15): apply → recovery worker: from/to + the cached old wheel for rollback


def write_pending(st, frm: str, to: str, info: dict | None, old_wheel) -> None:
    """The recovery worker writes the phones' marker from this right before the restart (the marker is the last claim
    made), and rolls back to `old` when the new service does not come up. Paths and versions only."""
    st.write_private(st.root / PENDING, json.dumps({"from": frm, "to": to, "at": int(time.time()), "install": info,
                                                    "old": str(old_wheel) if old_wheel else None}).encode())


def read_pending(st, target: str) -> dict | None:
    try:
        rec = json.loads((st.root / PENDING).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("to") != target or time.time() - rec.get("at", 0) > DAY:
        return None
    return rec


def _logged(st, ev, e) -> None:
    """P192 (B18): an upgrade-path error that is swallowed still leaves its type in host.log (never the message)."""
    try:
        if st.exists():
            st.log(ev, exception_type=type(e).__name__)
    except Exception:  # noqa: BLE001 — logging never breaks the upgrade
        pass


def rollback_wheel(st):
    """The running version's wheel, verified and cached (auto_update's: published bytes or the RECORD-verified installed
    files). None when it cannot be prepared (a source checkout, an editable install): the upgrade then has no rollback."""
    try:
        from . import auto_update
        return auto_update.cached_wheel(st, __version__, require_signature=False)
    except Exception as e:  # noqa: BLE001 — best effort: never blocks the upgrade itself (P192: but says why: rollback unavailable)
        _logged(st, "rollback_wheel_failed", e)
        return None


def roll_back(st, info: dict, wheel, frm: str, run) -> bool:
    """Reinstall the cached old wheel (auto_update.install_commands, hash re-checked) and confirm `--version`. The marker
    and the pending record go: nothing may announce the failed version."""
    try:
        (st.root / UPGRADED).unlink(missing_ok=True)
        (st.root / PENDING).unlink(missing_ok=True)
        if not wheel:
            return False
        import hashlib
        from pathlib import Path
        from . import auto_update
        p = Path(wheel)
        rec = json.loads(p.with_suffix(".verified.json").read_text())
        if hashlib.sha256(p.read_bytes()).hexdigest() != rec["sha256"]:
            return False
        for cmd in auto_update.install_commands(info, p):
            if run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True).returncode:
                return False
        v = run(new_argv(info) + ["--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return not v.returncode and frm in ((v.stdout or "") + " " + (v.stderr or "")).split()
    except Exception as e:  # noqa: BLE001 — a failed rollback is reported, never raised
        _logged(st, "rollback_failed", e)
        return False


def recover_of(info: dict, wheel, rb: str) -> dict:
    """P277 (container check of the WSL case): a failed `uv tool install --force` can delete the tool's own environment, so
    `agentj` itself is gone and the automatic rollback fails with it. Then the block names the one command that puts the
    cached previous version back (a local path on this computer; never sent anywhere)."""
    if rb != "failed" or not wheel or wheel == HOST_WHEEL:
        return {}
    try:
        from . import auto_update
        cmds = auto_update.install_commands(info, wheel)
    except Exception:  # noqa: BLE001
        return {}
    return {"recover": " && ".join(shlex.join(c) for c in cmds)} if cmds else {}


def take_marker(st, now: float | None = None) -> dict | None:
    """The authorized-upgrade marker, removed as it is read (so the phones hear it once). None when absent, malformed or
    older than 14 days."""
    p = st.root / UPGRADED
    try:
        d = json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        d = None
    try:
        p.unlink()
    except OSError:
        return None                                  # cannot remove it: stay quiet rather than repeat on every start
    now = time.time() if now is None else now
    if not (isinstance(d, dict) and all(isinstance(d.get(k), str) and parse(d[k]) for k in ("from", "to"))
            and isinstance(d.get("at"), int) and now - d["at"] <= MARKER_TTL):
        return None
    return d


def web_refresh_text(lang="zh") -> str:
    if lang == "en":
        return ('The phone/web page updates separately: when its new version is out, the page shows "Server upgraded" — tap '
                'Refresh (or the settings icon at the top right → "Update and reload"). Until then a small line under the header says the new page is on its way.')
    # P240: the page's new version can lag the computer (batch B after A); telling the owner to refresh now sent them round a
    # refresh loop on the old page (Leo 10-13 23:13). The page itself now offers a refresh only when one would help.
    return '手机/网页的新版会单独上线：上线后页面会弹「服务器已升级」，点「刷新」就行（也可以点右上角设置图标 →「更新并刷新」）；在那之前页眉下有一行小字「电脑已升级，新版网页稍后上线」。'


def health_text(counts, lang="zh", rows=None) -> str:
    if not counts:
        return ("Self-check could not finish; run `agentj doctor` on the computer." if lang == "en" else
                "自检未能完成，请在电脑运行 `agentj doctor`。")
    if counts[2]:
        # Only known row IDs get user-facing labels; arbitrary detail can contain private data.
        labels = {"version": ("程序版本", "program version"), "python": ("运行环境", "runtime"),
                  "service": ("后台服务", "background service"), "serve": ("后台连接", "host connection"),
                  "relay": ("中继连接", "relay connection"), "main-core": ("Agent 身份", "Agent identity"),
                  "main-inject": ("Agent 身份加载", "Agent identity loading"), "state": ("本机数据目录", "local data folder"),
                  "upgrade-restart": ("升级重启", "upgrade restart"), "dashboard": ("账户连接", "account connection"),
                  "platform": ("操作系统", "operating system"), "config": ("程序配置", "configuration"),
                  "agent": ("Agent 设置", "Agent settings"), "agent_cli": ("Agent 程序", "Agent executable"),
                  "alias": ("命令入口", "command shortcuts"), "asr": ("语音识别", "speech recognition"),
                  "auto-update": ("自动升级", "automatic upgrades"), "bound": ("席位绑定", "seat binding"),
                  "codex_perm": ("Codex 权限", "Codex permissions"), "codex_shared": ("Codex 共享会话", "shared Codex session"),
                  "danger": ("Agent 权限设置", "Agent permissions"), "estop": ("暂停状态", "pause state"),
                  "fence": ("Agent 隔离", "Agent isolation"), "friends": ("Agent 好友", "Agent friends"),
                  "hardware": ("电脑硬件", "computer hardware"), "harness": ("Agent 接入", "Agent connection"),
                  "keep-awake": ("防休眠", "keep-awake"), "linger": ("后台常驻", "background persistence"),
                  "login-session": ("电脑登录会话", "computer login session"), "migrate": ("本机数据迁移", "local data migration"),
                  "onboard": ("安装初始化", "installation setup"), "passphrase": ("配对口令", "pairing passphrase"),
                  "root-migrate": ("工作目录迁移", "working-folder migration"), "shared_inbound": ("手机消息接入", "phone message delivery"),
                  "skills": ("内置技能", "built-in skills"), "tasks": ("定时任务", "scheduled tasks"),
                  "update": ("升级检查", "upgrade check"), "voice-command": ("语音命令", "voice commands"),
                  "voice-migration": ("语音设置迁移", "voice settings migration"), "voice-model": ("语音模型", "voice model"),
                  "work-root": ("工作目录", "working folder"), "activity": ("操作记录", "activity records")}
        names = [labels[r.get("id")][lang == "en"] for r in rows or []
                 if isinstance(r, dict) and r.get("status") == "fail" and r.get("id") in labels]
        detail = "、".join(dict.fromkeys(names)) if lang != "en" else ", ".join(dict.fromkeys(names))
        if lang == "en":
            return f"Self-check found {counts[2]} failed check{'s' if counts[2] != 1 else ''}{': ' + detail if detail else ''}; run `agentj doctor` on the computer and follow its repair guidance."
        return f"自检有 {counts[2]} 项未通过{'：' + detail if detail else ''}；请在电脑运行 `agentj doctor`，按提示修复。"
    if lang == "en":
        return f"Self-check passed ({counts[1]} reminder{'s' if counts[1] != 1 else ''}, no failed checks)." if counts[1] else "All self-checks passed."
    return f"自检通过（有 {counts[1]} 项提醒，没有未通过项）。" if counts[1] else "自检全部通过。"


def completion_text(rec, counts, lang="zh", rows=None) -> str:
    if lang == "en":
        head = f"Agent J on your computer has been upgraded to {rec['to']} (previously {rec.get('from', '—')})."
    else:
        head = f"电脑上的 Agent J 程序已升级到 {rec['to']}（原 {rec.get('from', '—')}）。"
    return head + " " + health_text(counts, lang, rows) + "\n\n" + web_refresh_text(lang)


def upgraded_text(rec: dict, counts: tuple[int, int, int] | None, lang: str = "zh", rows=None) -> str:
    if rec["to"] == __version__:
        return completion_text(rec, counts, lang, rows)
    if lang == "en":
        return f"The upgrade to {rec['to']} did not take effect: this computer still runs {__version__}. " + health_text(counts, lang, rows)
    return f"升级到 {rec['to']} 没有生效：这台电脑仍运行 {__version__}。" + health_text(counts, lang, rows)


def doctor_counts(rows) -> tuple[int, int, int] | None:
    """(✓, !, ✗) of `agentj doctor` rows, or None."""
    if not isinstance(rows, list):
        return None
    st = [r.get("status") for r in rows if isinstance(r, dict)]
    return st.count("ok"), st.count("warn"), st.count("fail")


# Installation/runtime integrity failures block an upgrade. Owner workflow/config
# findings remain visible but must not turn a verified installation into failure.
UPGRADE_CHECKS = frozenset(("version", "python", "platform", "state", "relay", "dashboard", "service", "serve", "upgrade-restart", "main-core", "main-inject"))

def upgrade_health(rows):
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) or r.get("status") not in ("ok", "warn", "fail") for r in rows):
        return True, []
    failures = [r for r in rows if r["status"] == "fail"]
    # An unknown/unnamed failed row is not safe to dismiss.
    blocking = any(r.get("id") in UPGRADE_CHECKS or not r.get("id") for r in failures)
    return blocking, [r["id"] for r in failures if r.get("id") and r["id"] not in UPGRADE_CHECKS]


# result block reasons: (exit code, 中文, English)
AUTH_REASONS = {
    "bad_code": (2, "授权码格式不对（应为 AJUP- 加 4 组、每组 5 个字符）。把升级邮件整封交给 `--from-email`，不要手抄",
                 "the authorization code is malformed (AJUP- and 4 groups of 5 characters); pass the whole upgrade email to --from-email instead of retyping it"),
    "no_code": (2, "邮件里没有找到授权码（AJUP-…）", "no authorization code (AJUP-…) found in the email"),
    "ambiguous_code": (2, "邮件里有两个不同的授权码：请主人只转发一封升级邮件", "the email holds two different authorization codes: forward one upgrade email only"),
    "ambiguous_version": (2, "邮件里有两个不同的目标版本：请主人只转发一封升级邮件", "the email names two different target versions: forward one upgrade email only"),
    "bad_version": (2, "目标版本号看不懂", "the target version is not a version number"),
    "version_conflict": (2, "--version 和邮件里的目标版本不一致", "--version differs from the email's target version"),
    "bad_email": (2, "读不了邮件文件（不存在或超过 256 KB）", "cannot read the email file (missing or larger than 256 KB)"),
    "read_only": (2, "程序目录是只读的：在 Agent 隔离（独立模式的 fence）里升级不了，也看不到主机的密钥。要在隔离外运行：由 Agent 在现有共享会话运行，"
                     "或由共享会话里的 Agent 运行", "agentj's own files are read-only here: inside the Agent fence (independent mode) it cannot "
                     "upgrade and cannot see the host key. Run it outside the fence: the Agent of the existing shared session"),
    "not_initialised": (2, "这台电脑还没有 Agent J 身份（agentj init）", "this computer has no Agent J identity yet (agentj init)"),
    "not_linked": (2, "这台电脑没有加到 Agent J 账号，授权码验证不了。先 `agentj login`（主人在场），或由 Agent 运行 `agentj update apply`",
                   "this computer is not added to an Agent J account, so the code cannot be checked: `agentj login` first (with your human), "
                   "or the Agent runs `agentj update apply`"),
    "unknown": (1, "查不到最新版本（网络不通或 GitHub 连不上），没有升级，授权码也没有用掉。稍后再运行同一条命令",
                "could not find out the latest version (offline or GitHub unreachable); nothing was installed and the code was not used. Run the same command again later"),
    "not_newer": (2, "这台电脑已经不比公开仓库里的最新版旧，没有可升级的", "this computer is not older than the newest public release; nothing to upgrade"),
    "doctor_failed": (1, "升级后自检失败，Agent 查看 doctor 结果并修复", "post-upgrade doctor failed; inspect and repair"),
    "self_check_failed": (1, "升级后命令无法运行，Agent 先诊断并修复", "post-upgrade command could not run; diagnose and repair"),
    "not_latest": (2, "邮件授权的版本不是公开仓库里的最新发布版，没有升级，授权码没有用掉。请主人确认邮件是不是最新的一封",
                   "the authorized version is not the newest public release; nothing installed, the code was not used. Ask your human whether this is the newest upgrade email"),
    "invalid_authorization": (3, "授权码无效：不存在、不属于这台电脑所在的账号，或这台电脑没有绑定。不要再试别的码；请主人确认",
                              "invalid authorization: unknown, from another account, or this computer is not bound. Do not try other codes; ask your human"),
    "not_bound": (3, "这台电脑在 Agent J 账号里已不是绑定状态", "this computer is no longer bound in the Agent J account"),
    "version_mismatch": (4, "这个授权码是给另一个版本的", "this authorization code is for another version"),
    "expired": (5, "授权码已过期（14 天）。请主人等下一封升级邮件，或自己在终端运行 `agentj update apply`",
                "the authorization code expired (14 days); wait for the next upgrade email, or the Agent runs `agentj update apply`"),
    "already_used": (6, "这台电脑已经用过这个授权码", "this computer already used this authorization code"),
    "rate_limited": (7, "试得太频繁，一小时后再试", "too many tries; try again in an hour"),
    "unsupported": (8, "Agent J 服务器还不支持升级授权（服务器比这台电脑的 agentj 旧），没有升级，授权码没有用掉。稍后再运行同一条命令，"
                       "或由 Agent 运行 `agentj update apply`",
                    "the Agent J server does not support upgrade authorization yet (it is older than this agentj); nothing installed, "
                    "the code was not used. Run the same command again later, or the Agent runs `agentj update apply`"),
    "unreachable": (1, "连不上 Agent J 服务器，没有升级，授权码没有用掉。稍后再运行同一条命令",
                    "could not reach the Agent J server; nothing installed, the code was not used. Run the same command again later"),
    "fail": (1, "服务器的回答不对，没有升级", "unexpected answer from the server; nothing installed"),
    "install_failed": (1, "升级命令失败。授权已经记在这台电脑上：网络恢复后再运行同一条命令即可继续",
                       "the upgrade command failed. The authorization is recorded on this computer: run the same command again to retry"),
    "not_upgraded": (1, "命令跑完了，但程序版本没有变成目标版本", "the commands ran but the program is not at the target version"),
    "already_current": (0, "已经是目标版本，不用再升级", "already at the target version; nothing to do"),
    # P63: a formal failure (apply() used to hit KeyError here and print a traceback instead of UPGRADE_RESULT)
    # P203: a restart now would interrupt something — nothing was installed; the owner decides
    "confirm_on_phone": (10, "现在升级会打断上面列出的事：手机上出了一张升级确认卡，主人点「现在升级」后电脑自己升级并回报。不要再运行这条命令，"
                             "也不要加 --yes 绕过（除非主人明确说不管这些、直接升级）",
                         "upgrading now would interrupt the items above: an upgrade confirmation card is on the phone; when your human taps "
                         "Upgrade now the computer upgrades and reports. Do not run this command again or bypass it with --yes (unless your "
                         "human explicitly says to upgrade anyway)"),
    "not_confirmed": (10, "没有确认，没有升级", "not confirmed; nothing was installed"),
    "service_failed": (1, "程序已升级，但服务没能重新安装：运行 `agentj service install`",
                       "upgraded, but service recovery failed: run `agentj service start`"),
    # P277: the failed step, finer (install_failed / artifact_refused / service_failed above stay for older records)
    "download_failed": (1, "下载正式发布包失败（网络不通或 agentj.app 暂时连不上），什么都没安装。稍后再运行同一条命令",
                        "downloading the published release failed (offline or agentj.app unreachable); nothing was installed. "
                        "Run the same command again later"),
    "signature_or_hash_mismatch": (1, "下载到的发布包签名或哈希校验不对，拒绝安装，什么都没装。不要绕过校验；稍后重试，仍失败就交给客服",
                                   "the downloaded release failed its signature/hash check and was refused; nothing was installed. "
                                   "Never bypass the check; retry later, and leave it to support if it keeps failing"),
    "uv_failed": (1, "安装命令失败，原文见 stderr 行；已尝试回滚到原版本（见 rollback 行）。修好原因后再运行同一条命令",
                  "the install command failed (its own words are on the stderr lines); a rollback to the previous version was "
                  "tried (see rollback). Fix the cause, then run the same command again"),
    "service_restart_failed": (1, "新版已装好，但后台服务没能重启接管；已尝试回滚。运行 `agentj doctor`，再按提示 `agentj service start`",
                               "the new version installed but the background service did not restart; a rollback was tried. "
                               "Run `agentj doctor`, then `agentj service start` as it says"),
    "unsupported_install": (1, "这台电脑的安装方式（源码目录或旧名安装）不能自动升级，什么都没装；按 install.md 重新安装",
                            "this installation (a source checkout or the old package name) cannot be upgraded automatically; "
                            "nothing was installed — reinstall as install.md says"),
}


def _exit_of(reason: str) -> int:
    """Exit code of a result reason; one not in AUTH_REASONS is a plain failure (1), never a KeyError."""
    return AUTH_REASONS.get(reason, (1,))[0]


def authorized_apply(st, code: str | None, target: str | None, **kw) -> dict:
    """Contract C3 (see _authorized_apply). P277: an attempt that got as far as the download is remembered (last upgrade)."""
    from . import upgrade_diag
    res = _authorized_apply(st, code, target, **kw)
    upgrade_diag.remember(st, res)
    return res


def _authorized_apply(st, code: str | None, target: str | None, *, mail: dict | None = None, prefix: str | None = None,
                      check_fn=None, auth_fn=None, run=None, svc_on=None, say=None, plan_fn=None, rollback_fn=None) -> dict:
    """Contract C3. Order: local checks (code shape, read-only, identity, account link) → check() says newer and latest ==
    target → signed upgrade-auth → only on 200: install pinned to target (no y/N, no terminal), verify the new version,
    marker for serve's phone notice, service re-install. → {"result": "ok" | "refused" | "failed", "reason", "from", "to",
    "service", "exit", "latest"?}. `say` prints progress lines (the restart may end the caller's process)."""
    from . import cloud
    check_fn = check_fn or check
    auth_fn = auth_fn or cloud.upgrade_auth
    run = run or subprocess.run
    say = say or (lambda line: None)
    out = {"from": __version__, "to": target, "service": "unchanged"}

    def done(reason: str, result: str | None = None, **kw) -> dict:
        ex = _exit_of(reason)
        return {**out, **kw, "result": result or ("ok" if ex == 0 else "failed" if ex == 1 else "refused"), "reason": reason,
                "exit": ex}
    if mail is not None:
        if mail.get("problem"):
            return done(mail["problem"])
        code = mail["code"]
        if target and mail.get("version") and mail["version"] != target:
            return done("version_conflict")
        target = target or mail.get("version")
        out["to"] = target
    code = normalise_code(code)
    if not code:
        return done("bad_code")
    if target is not None and parse(target) is None:
        return done("bad_version")
    if not writable(prefix):
        return done("read_only")
    if not st.exists():
        return done("not_initialised")
    if cloud.read_cloud(st) is None:
        return done("not_linked")
    r = check_fn()
    target = target or r.get("latest")
    out["to"] = target
    if r["status"] == "unknown":
        return done("unknown", latest=None)
    if target and compare(__version__, target) == 0:
        return done("already_current")
    if r["status"] != "newer":
        return done("not_newer", latest=r.get("latest"))
    if r["latest"] != target:
        return done("not_latest", latest=r["latest"])
    a = auth_fn(st, code, target)
    if a["status"] == "already_used" and _spent_here(st, code, target):
        say("· 这台电脑之前已用这个授权码授权过这个版本，继续安装 / this computer already holds this authorization; installing")
    elif a["status"] != "ok":
        reason = a["status"] if a["status"] in AUTH_REASONS else "fail"
        return done(reason, **({"granted": a["version"]} if a.get("version") else {}))
    else:
        st.write_private(st.root / AUTH_REC, json.dumps({"version": target, "code_sha256": _code_hash(code),
                                                          "at": int(time.time())}).encode())
        st.log("upgrade_authorized", status=target)
    info = install_kind(prefix)
    from .upgrade_diag import plan_detail, plan_reason, run_step
    out["step"] = "download"
    try:
        plan = (plan_fn or verified_plan)(st, info, target)
    except Exception as e:  # noqa: BLE001 — P277: download vs signature/hash, the exception type kept (never its text)
        return done(plan_reason(e), detail=plan_detail(e))
    _pre_snapshot(st)
    # P192 (B15): the same cached-wheel rollback as `apply` — this path used to leave a half-installed version behind
    old = (rollback_fn or rollback_wheel)(st)
    def back(reason, **kw):
        ok = roll_back(st, info, old, __version__, run)
        rb = "ok" if ok else "failed" if old else "unavailable"
        st.log("upgrade_rollback", status=rb, reason=reason)
        return done(reason, rollback=rb, **recover_of(info, old, rb), **kw)
    out["step"] = "install"
    for c in plan:
        say("$ " + shlex.join(c))
        rc, tl = run_step(run, c, say)
        if rc != 0:
            st.log("upgrade_failed", status=str(rc))
            return back("uv_failed", stderr_tail=tl)
    argv = new_argv(info)
    out["step"] = "verify"
    try:
        v = run(argv + ["--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        now_v = ((v.stdout or "") + " " + (v.stderr or "")).split()
    except OSError:
        now_v = []
    if target not in now_v:
        st.log("upgrade_failed", status="version")
        return back("not_upgraded")
    st.log("upgrade_done", status=target)
    if svc_on is None:
        from . import service
        try:
            svc_on = bool(service.status().get("installed") or service.legacy_status().get("installed"))
        except Exception:  # noqa: BLE001 — no service manager: nothing to restart
            svc_on = False
    if not svc_on:
        write_marker(st, __version__, target)
        return {**out, "result": "ok", "reason": "upgraded", "service": "not_installed", "exit": 0}
    write_pending(st, __version__, target, info, old)    # P142: the recovery worker writes the marker right before restart
    #                                                       (P192: and has the old wheel to roll back to, as after `apply`)
    out["step"] = "service"
    say(f"✓ 已安装 {target}，正在重启服务。如果这段对话在这里中断，Agent J 回来后会在手机上说「已升级到 {target}」。/ installed "
        f"{target}; restarting the service now — if this conversation stops here, the phone hears \"Upgraded to {target}\" once "
        "Agent J is back.")
    rc, tl = run_step(run, argv + ["service", "install", "--deferred", target], say)
    if rc == NO_MANAGER_EXIT:      # P278: no systemd here — the new version stays (a rollback could not restart either)
        (st.root / PENDING).unlink(missing_ok=True)
        write_marker(st, __version__, target)
        return done("service_restart_failed", **no_manager(tl))
    if rc != 0:
        return back("service_restart_failed", service="failed", stderr_tail=tl)
    return {**out, "result": "ok", "reason": "upgraded", "service": "restart_scheduled", "exit": 0}


# P205: `update apply` run by the Agent inside the fence (independent mode). The host's state directory is an empty mount
# there: no cached old wheel, no pending record / marker for the recovery worker, and doctor cannot see `state` / `serve`.
# The host (serve, outside the fence, same version as the caller) does those parts over `<state>/agentperm/elevate.sock`
# `{"t": "upgrade", "op": ping | prepare | pending | marker | clear | rollback | health}` (host_op below). An older serve
# answers `refused` → the fenced apply still installs, says `handoff: not_recorded` and returns `unverifiable_in_fence`
# (result ok, exit 0) when nothing else failed: the owner confirms on the phone / runs doctor outside the fence.
HOST_WHEEL = "host"          # apply's `old` when the host holds the cached old wheel (fenced path)
HEALTH_TIMEOUT = 300


def _host(st, op: str, timeout: float = 60, **kw) -> dict | None:
    """One upgrade op on the host over elevate.sock; None when the host cannot do it (no serve, an older serve, an error)."""
    try:
        from .elevate import client_request
        res = client_request(st, {"t": "upgrade", "op": op, **kw}, timeout)
    except Exception as e:  # noqa: BLE001 — the channel is optional; the fenced apply reports what it could not do
        _logged(st, "upgrade_host_failed", e)
        return None
    return res if isinstance(res, dict) and res.get("ok") is True else None


def host_channel(st) -> bool:
    """The host answers upgrade ops and runs this very version (from = the host's version, its wheel is the rollback)."""
    r = _host(st, "ping", 10)
    return bool(r) and r.get("version") == __version__


def _pre_snapshot(st) -> None:
    """P207: the owner's files (friends, card, remote names, address book, theme, preferences) are copied before anything
    is installed; a failure here never blocks the upgrade (serve's start snapshot and self-heal are the second net)."""
    try:
        from . import userdata
        userdata.snapshot(st, "pre-upgrade-v" + __version__)
    except Exception:  # noqa: BLE001
        pass


def apply(st, target=None, *, fenced=None, **kw):
    """Explicit caller-requested upgrade (see _apply). P277: an attempt that got as far as the download is remembered as the
    last upgrade — in the state directory, or inside the fence by the host (elevate.sock op `record`)."""
    from . import upgrade_diag
    if fenced is None:
        from . import fence
        fenced = fence.inside()
    res = _apply(st, target, fenced=fenced, **kw)
    upgrade_diag.remember(st, res, fenced=fenced)
    return res


def _apply(st, target=None, *, prefix=None, check_fn=None, run=None, say=None, svc_on=None, plan_fn=None, rollback_fn=None,
           fenced=None):
    """Explicit caller-requested upgrade. No terminal or cloud grant is required.
    Native filesystem restrictions and exact published target checks still apply.
    P142 (A12/B15): the old version's wheel is cached first; a failed version check or service hand-off rolls back to it
    here, and the recovery worker (a manager job) waits until serve is idle (no sudo/secret card, no turn, ≤ 10 min),
    writes the marker last and rolls back itself when the new service does not come up.
    P205: inside the Agent fence the host does the state-directory parts and the health check (see host_op).
    """
    run, say = run or subprocess.run, say or (lambda line: None)
    if fenced is None:
        from . import fence
        fenced = fence.inside()
    r = (check_fn or check)()
    target = target or r.get("latest")
    out = {"from": __version__, "to": target, "service": "unchanged"}
    def done(reason, result=None, **kw):
        ex = _exit_of(reason)
        return {**out, **kw, "result": result or ("ok" if ex == 0 else "failed" if ex == 1 else "refused"),
                "reason": reason, "exit": ex}
    if target is not None and parse(target) is None:
        return done("bad_version")
    if r["status"] == "unknown":
        return done("unknown")
    if r["status"] != "newer":
        return done("already_current" if r["status"] == "current" else "not_newer")
    if target != r.get("latest"):
        return done("not_latest")
    if not writable(prefix):
        return done("read_only")
    info = install_kind(prefix)
    if svc_on is None:
        from . import service
        try:
            svc_on = bool(service.status().get("installed") or service.legacy_status().get("installed"))
        except Exception:  # noqa: BLE001 — no service manager: nothing to restart (as authorized_apply)
            svc_on = False
    from .upgrade_diag import plan_detail, plan_reason, run_step
    out["step"] = "download"
    try:
        plan = (plan_fn or verified_plan)(st, info, target)
    except Exception as e:  # noqa: BLE001 — P277: download vs signature/hash, the exception type kept (never its text)
        return done(plan_reason(e), detail=plan_detail(e))
    if not fenced:
        _pre_snapshot(st)          # P207 (inside the fence the state directory is an empty mount; serve snapshots at start)
    chan = fenced and host_channel(st)
    if fenced:
        out["health"] = "host" if chan else "unverifiable"
        prep = _host(st, "prepare", 600, to=target) if chan else None
        old = HOST_WHEEL if prep and prep.get("rollback") else None
    else:
        old = (rollback_fn or rollback_wheel)(st) if st.exists() else None
    def back(reason, **kw):
        if fenced:
            res = _host(st, "rollback", 900, to=target) if chan else None
            rb = res["rollback"] if res and res.get("rollback") in ("ok", "failed", "unavailable") else (
                "failed" if old else "unavailable")
            return done(reason, rollback=rb, **kw)
        ok = roll_back(st, info, old, __version__, run) if st.exists() else False
        rb = "ok" if ok else "failed" if old else "unavailable"
        if st.exists():
            st.log("upgrade_rollback", status=rb, reason=reason)
        return done(reason, rollback=rb, **recover_of(info, old, rb), **kw)
    out["step"] = "install"
    for cmd in plan:
        say("$ " + shlex.join(cmd))
        rc, tl = run_step(run, cmd, say)
        if rc:
            if not fenced and st.exists():
                st.log("upgrade_failed", status=str(rc))
            return back("uv_failed", stderr_tail=tl)
    out["step"] = "verify"
    argv = new_argv(info)
    handed = False      # P192 (B15): until the service hand-off / marker, any failure rolls back (an OSError included)

    def record(kind: str) -> None:
        """pending (with a service) or marker (without): in the state directory, or by the host when fenced (P205)."""
        if fenced:
            if not (chan and _host(st, kind, 30, to=target)):
                out["handoff"] = "not_recorded"
        elif st.exists():
            if kind == "pending":
                write_pending(st, __version__, target, info, old)
            else:
                write_marker(st, __version__, target)
    try:
        v = run(argv + ["--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if v.returncode or target not in ((v.stdout or "") + " " + (v.stderr or "")).split():
            return back("not_upgraded")
        if svc_on:
            record("pending")
            out["step"] = "service"
            # The new CLI hands off before touching the calling service tree.
            rc, tl = run_step(run, argv + ["service", "install", "--deferred", target], say, catch=False)
            if rc == NO_MANAGER_EXIT:   # P278: no systemd — keep the new version, the marker instead of the pending record
                if not fenced and st.exists():
                    (st.root / PENDING).unlink(missing_ok=True)
                record("marker")
                return done("service_restart_failed", **no_manager(tl))
            if rc:
                return back("service_restart_failed", service="failed", stderr_tail=tl)
            out["service"] = "restart_scheduled"
        else:
            record("marker")
            out["service"] = "not_installed"
        handed = True
        out["step"] = "health"
        rows = None
        if chan:
            h = _host(st, "health", HEALTH_TIMEOUT + 30)
            rows = h.get("checks") if h and isinstance(h.get("checks"), list) else None
            if rows is None:
                out["health"] = "unverifiable"
        if rows is not None:
            failed, existing = upgrade_health(rows)
        else:
            health = run(argv + ["doctor", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True)
            try:
                rows = json.loads(health.stdout)["checks"]
                failed, existing = upgrade_health(rows)
            except (ValueError, TypeError, KeyError, AttributeError):
                failed, existing = bool(health.returncode), []
        out["existing_issues"] = existing
        if failed:
            return {**out, "result": "failed", "reason": "doctor_failed", "exit": 1}
    except OSError:
        if not handed:
            return back("self_check_failed", **({"service": "failed"} if svc_on else {}))
        return {**out, "result": "failed", "reason": "self_check_failed", "exit": 1}
    if out.get("health") == "unverifiable":
        return {**out, "result": "ok", "reason": "unverifiable_in_fence", "exit": 0}
    return {**out, "result": "ok", "reason": "upgraded", "exit": 0}


def host_op(st, req: dict, mem: dict, run=None) -> dict:
    """Serve's side of the fenced apply (elevate.sock `{"t": "upgrade", "op": …}`; serve runs outside the fence). Only what
    the unfenced apply would do itself: cache this version's wheel, write the pending record / marker naming this version as
    `from`, roll back to that wheel, run the new `agentj doctor --json`. The target must be a version newer than this host;
    the install kind and the wheel are the host's own (nothing path-like is taken from the request). Logs: op + result."""
    run = run or subprocess.run
    op, to = req.get("op"), req.get("to")
    if op == "ping":
        return {"ok": True, "version": __version__}
    if op == "clear":
        (st.root / UPGRADED).unlink(missing_ok=True)
        (st.root / PENDING).unlink(missing_ok=True)
        return {"ok": True}
    if op == "record":                     # P277: the fenced apply's last-upgrade record (validated; never a path)
        from . import upgrade_diag
        rec = req.get("rec")
        if not isinstance(rec, dict) or not upgrade_diag.valid(rec):
            return {"ok": False, "why": "record"}
        keep = {k: rec[k] for k in ("v", "from", "to", "result", "reason", "at", "step") if k in rec}
        if isinstance(rec.get("detail"), str):
            keep["detail"] = rec["detail"][:120]
        if isinstance(rec.get("stderr_tail"), str):
            keep["stderr_tail"] = upgrade_diag.redact(rec["stderr_tail"])[-upgrade_diag.TAIL_MAX:]
        if keep.get("step") not in (None, *upgrade_diag.STEPS):
            keep["step"] = "install"
        keep["at"] = int(time.time())
        ok = upgrade_diag.write(st, keep)
        st.log("upgrade_host", op=op, result="ok" if ok else "refused")
        return {"ok": ok}
    if op == "health":
        info = install_kind()
        try:
            h = run(new_argv(info) + ["doctor", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=HEALTH_TIMEOUT)
            rows = json.loads(h.stdout)["checks"]
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as e:
            _logged(st, "upgrade_host_health_failed", e)
            return {"ok": False, "why": "doctor"}
        if not isinstance(rows, list):
            return {"ok": False, "why": "doctor"}
        keep = ("id", "status", "summary", "hint", "unverifiable")
        st.log("upgrade_host", op=op, result="ok")
        return {"ok": True, "checks": [{k: r[k] for k in keep if k in r} for r in rows if isinstance(r, dict)]}
    if op not in ("prepare", "pending", "marker", "rollback"):
        return {"ok": False, "why": "op"}
    if not isinstance(to, str) or parse(to) is None or compare(__version__, to) != -1:
        return {"ok": False, "why": "version"}
    if op == "prepare":
        old = rollback_wheel(st)
        mem["old"] = (to, str(old) if old else None)
        st.log("upgrade_host", op=op, result="ok" if old else "no_wheel")
        return {"ok": True, "rollback": bool(old)}
    old = mem.get("old", (None, None))[1] if mem.get("old", (None,))[0] == to else None
    if op == "pending":
        write_pending(st, __version__, to, install_kind(), old)
    elif op == "marker":
        write_marker(st, __version__, to)
    else:
        ok = roll_back(st, install_kind(), old, __version__, run)
        rb = "ok" if ok else "failed" if old else "unavailable"
        st.log("upgrade_rollback", status=rb, reason="fenced_apply")
        return {"ok": True, "rollback": rb}
    st.log("upgrade_host", op=op, result="ok")
    return {"ok": True}


def result_block(res: dict) -> str:
    """The fixed block the Agent copies back to its human (install.md section U)."""
    lines = [f"UPGRADE_RESULT {res['result']}", f"reason: {res['reason']}", f"from: {res['from']}",
             f"to: {res.get('to') or '?'}", f"service: {res['service']}"]
    if res.get("rollback"):
        lines.append(f"rollback: {res['rollback']}" + (f" (back on {res['from']})" if res["rollback"] == "ok" else ""))
    if res.get("health"):
        lines.append("health: " + res["health"] + {"host": " (checked by the host outside the Agent fence)",
                                                   "unverifiable": " (cannot be checked inside the Agent fence)"}.get(res["health"], ""))
    if res.get("handoff"):
        lines.append("handoff: " + res["handoff"] + " — 升级记录没能交给主机（隔离环境里看不到主机数据），重启后手机上的「已升级」通知可能不准、"
                     "没有自动回滚 / the upgrade record could not reach the host (its data is hidden inside the fence): the phone's "
                     "\"upgraded\" notice may be off and there is no automatic rollback")
    if res.get("existing_issues"):
        lines.append("existing_issues: " + ", ".join(res["existing_issues"]) + " — 升级已完成，这些其他问题请另行检查 / upgrade completed; review these other issues separately")
    if res.get("result") != "ok" and res.get("step"):        # P277: where it failed, the installer's own words (redacted)
        lines.append(f"step: {res['step']}")
    if res.get("detail") and res.get("result") != "ok":
        lines.append(f"detail: {res['detail']}")
    if res.get("stderr_tail") and res.get("result") != "ok":
        lines += ["stderr: " + ln for ln in str(res["stderr_tail"]).splitlines()]
    if res.get("latest"):
        lines.append(f"latest: {res['latest']}")
    for line in res.get("interrupts") or []:           # P203: what a restart now would interrupt
        lines.append(f"interrupts: {line}")
    if res.get("granted"):
        lines.append(f"authorized_version: {res['granted']}")
    zh, en = AUTH_REASONS[res["reason"]][1:] if res["reason"] in AUTH_REASONS else REASON_EXTRA.get(res["reason"], ("", ""))
    if res.get("service") == "no_systemd":        # P278: installed, no systemd to restart it — no rollback was tried
        zh, en = NO_MANAGER_WHY
    if zh:
        lines.append(f"why: {zh} / {en}")
    if res.get("fix"):
        lines.append(f"fix: {res['fix']}")
    if res.get("recover"):
        lines.append(f"recover: {res['recover']} — 回滚没成功、agentj 命令可能已经不在了：运行这一条装回原版本 / the rollback failed and "
                     "`agentj` may be gone: run this to put the previous version back")
    if res.get("support"):
        lines.append(f"support: {res['support']}")
    if res.get("tell_owner"):
        lines.append(f"tell_owner: {res['tell_owner']}")
    lines.append("next: " + (UNVERIFIABLE_NEXT if res["reason"] == "unverifiable_in_fence" else "agentj doctor" if res["result"] == "ok" else
                             "agentj doctor; tell your human this block" if res["result"] == "failed" else "tell your human this block"))
    return "\n".join(lines)


UNVERIFIABLE_NEXT = ("tell your human: the program is upgraded; please confirm the version on the phone (Settings), or run "
                     "`agentj doctor` in a terminal outside the Agent fence / 请主人在手机「设置」里确认版本，或在电脑终端（隔离外）运行 agentj doctor")
REASON_EXTRA = {
    "unverifiable_in_fence": ("程序已升级并通过版本核对；但在 Agent 的隔离环境里看不到主机状态，后台服务和本机数据这两项无法在这里核对，"
                              "也没有失败项", "upgraded and the version verified; inside the Agent fence the host's state is hidden, "
                              "so the service connection and local data could not be checked here — nothing failed"),
    "artifact_refused": ("发布包签名或哈希校验失败，未安装；请检查正式发布工件", "published artifact signature/hash verification failed; nothing installed; check the official release artifacts"),
    "upgraded": ("已升级", "upgraded"),
}
