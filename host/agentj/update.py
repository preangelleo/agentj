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


def latest_url() -> str | None:
    u = getenv(URL_ENV) or LATEST_URL
    if u == "off":
        return None
    p = urllib.parse.urlparse(u)
    if p.scheme == "https" or (p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost")):
        return u
    return None


def fetch_latest(timeout: float = TIMEOUT) -> tuple[str | None, str]:
    """(version, "ok") or (None, why): "off" · "network" · "http_<code>" · "unparsable"."""
    url = latest_url()
    if not url:
        return None, "off"
    req = urllib.request.Request(url, headers={"user-agent": f"{DIST}/{__version__} (update check)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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


def write_marker(st, frm: str, to: str, now: float | None = None) -> None:
    st.write_private(st.root / UPGRADED, json.dumps({"from": frm, "to": to, "at": int(time.time() if now is None else now)}).encode())


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


def upgraded_text(rec: dict, counts: tuple[int, int, int] | None, lang: str = "zh") -> str:
    """The one phone line after an authorized upgrade (C3), in the owner's language (`appearance.language`)."""
    doc = f"doctor: {counts[0]} ✓ / {counts[1]} ! / {counts[2]} ✗" if counts else "doctor: ?"
    if rec["to"] == __version__:
        return f"Upgraded to {rec['to']} ({doc})" if lang == "en" else f"已升级到 {rec['to']}（{doc}）"
    if lang == "en":
        return (f"The upgrade to {rec['to']} did not take effect: this computer runs {__version__} ({doc}). Run "
                "`agentj doctor` on it.")
    return f"升级到 {rec['to']} 没有生效：这台电脑现在运行的是 {__version__}（{doc}）。在电脑上运行 `agentj doctor`。"


def doctor_counts(rows) -> tuple[int, int, int] | None:
    """(✓, !, ✗) of `agentj doctor` rows, or None."""
    if not isinstance(rows, list):
        return None
    st = [r.get("status") for r in rows if isinstance(r, dict)]
    return st.count("ok"), st.count("warn"), st.count("fail")


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
}


def authorized_apply(st, code: str | None, target: str | None, *, mail: dict | None = None, prefix: str | None = None,
                     check_fn=None, auth_fn=None, run=None, svc_on=None, say=None) -> dict:
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
        ex = AUTH_REASONS[reason][0]
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
    for c in commands(info, target):
        say("$ " + shlex.join(c))
        try:
            rc = run(c, stdin=subprocess.DEVNULL).returncode
        except OSError:
            rc = 127
        if rc != 0:
            st.log("upgrade_failed", status=str(rc))
            return done("install_failed")
    argv = new_argv(info)
    try:
        v = run(argv + ["--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        now_v = ((v.stdout or "") + " " + (v.stderr or "")).split()
    except OSError:
        now_v = []
    if target not in now_v:
        st.log("upgrade_failed", status="version")
        return done("not_upgraded")
    write_marker(st, __version__, target)
    st.log("upgrade_done", status=target)
    if svc_on is None:
        from . import service
        try:
            svc_on = bool(service.status().get("installed") or service.legacy_status().get("installed"))
        except Exception:  # noqa: BLE001 — no service manager: nothing to restart
            svc_on = False
    if not svc_on:
        return {**out, "result": "ok", "reason": "upgraded", "service": "not_installed", "exit": 0}
    say(f"✓ 已安装 {target}，正在重启服务。如果这段对话在这里中断，Agent J 回来后会在手机上说「已升级到 {target}」。/ installed "
        f"{target}; restarting the service now — if this conversation stops here, the phone hears \"Upgraded to {target}\" once "
        "Agent J is back.")
    try:
        rc = run(argv + ["service", "install"], stdin=subprocess.DEVNULL).returncode
    except OSError:
        rc = 127
    if rc != 0:
        return {**out, "result": "failed", "reason": "service_failed", "service": "failed", "exit": 1}
    return {**out, "result": "ok", "reason": "upgraded", "service": "restarted", "exit": 0}


def apply(st, target=None, *, prefix=None, check_fn=None, run=None, say=None, svc_on=None):
    """Explicit caller-requested upgrade. No terminal or cloud grant is required.
    Native filesystem restrictions and exact published target checks still apply.
    """
    run, say = run or subprocess.run, say or (lambda line: None)
    r = (check_fn or check)()
    target = target or r.get("latest")
    out = {"from": __version__, "to": target, "service": "unchanged"}
    def done(reason, result=None, **kw):
        ex = AUTH_REASONS[reason][0]
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
        svc_on = bool(service.status().get("installed") or service.legacy_status().get("installed"))
    for cmd in commands(info, target):
        say("$ " + shlex.join(cmd))
        try:
            rc = run(cmd, stdin=subprocess.DEVNULL).returncode
        except OSError:
            rc = 127
        if rc:
            return done("install_failed")
    argv = new_argv(info)
    try:
        v = run(argv + ["--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if v.returncode or target not in ((v.stdout or "") + " " + (v.stderr or "")).split():
            return done("not_upgraded")
        if st.exists():
            write_marker(st, __version__, target)
        if svc_on:
            # install updates legacy launchers; restart explicitly uses the new launcher.
            for action in ("install", "restart"):
                if run(argv + ["service", action], stdin=subprocess.DEVNULL).returncode:
                    return done("service_failed", service="failed")
            out["service"] = "restarted"
        else:
            out["service"] = "not_installed"
        if run(argv + ["doctor"], stdin=subprocess.DEVNULL).returncode:
            return {**out, "result": "failed", "reason": "doctor_failed", "exit": 1}
    except OSError:
        return {**out, "result": "failed", "reason": "self_check_failed", "exit": 1}
    return {**out, "result": "ok", "reason": "upgraded", "exit": 0}


def result_block(res: dict) -> str:
    """The fixed block the Agent copies back to its human (install.md section U)."""
    lines = [f"UPGRADE_RESULT {res['result']}", f"reason: {res['reason']}", f"from: {res['from']}",
             f"to: {res.get('to') or '?'}", f"service: {res['service']}"]
    if res.get("latest"):
        lines.append(f"latest: {res['latest']}")
    if res.get("granted"):
        lines.append(f"authorized_version: {res['granted']}")
    zh, en = AUTH_REASONS[res["reason"]][1:] if res["reason"] in AUTH_REASONS else REASON_EXTRA.get(res["reason"], ("", ""))
    if zh:
        lines.append(f"why: {zh} / {en}")
    lines.append("next: " + ("agentj doctor" if res["result"] == "ok" else
                             "agentj doctor; tell your human this block" if res["result"] == "failed" else "tell your human this block"))
    return "\n".join(lines)


REASON_EXTRA = {
    "upgraded": ("已升级", "upgraded"),
    "service_failed": ("程序已升级，但服务没能重新安装：运行 `agentj service install`", "upgraded, but the service was not re-installed: run `agentj service install`"),
}
