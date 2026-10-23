"""P258: AI 登录救援 — re-log a harness whose SUBSCRIPTION login expired, from the paired phone, without touching the Agent J host.

The whole chain is deterministic serve code; the (logged-out) Agent is never asked to help itself.

  detect    every harness failure line goes through `classify()`: login (expired / revoked / not logged in) vs balance
            (top up on the vendor's site — no rescue) vs rate_limit (wait — no rescue). Only `login` lights the phone's dot.
  Codex     `codex login --device-auth` (official, beta: the owner turns on "device code login" in ChatGPT → Security once).
            The link + one-time code go to the phone card; the owner finishes on ANY signed-in device; the CLI itself
            polls and writes auth.json. Nothing is pasted back.
  Kimi      (P305) `kimi login --region mainland-cn|global` — Kimi Code CLI's official device-code flow, same shape as Codex:
            link + code to the phone, the owner approves on any device signed in to kimi.com / kimi.ai, the CLI writes
            ~/.kimi-code/credentials itself. Its files are backed up first and put back if the live check fails.
  Claude    route A (default) `claude setup-token` runs HERE in a private PTY → the official authorize URL goes to the phone →
            the owner approves in any browser signed in to claude.ai → the code it shows is pasted into the sealed secret
            card (elevate.py: E2E, passkey) → the PTY prints the 1-year token → live-checked (`claude -p`) → written to the
            service env file (0600) as CLAUDE_CODE_OAUTH_TOKEN. Route B: paste a token made with `claude setup-token` on
            any other computer (same check, same file). Route C: `claude auth login` (the /login flow; refreshing
            credentials in ~/.claude/.credentials.json — what the owner's own terminal sessions read).
  restart   harness_setup.prepare() re-answers the first-run screens (a logout resets onboarding), the env is refreshed in
            this process, an owned harness restarts, an Agent-J-started shared terminal session is replaced.
  verify    `screen_check()` opens the harness's own interactive screen in a PTY for a few seconds and walks the known
            first-run / trust / login screens (BLOCKERS): safe ones are answered by fixed keys, the rest are reported by
            name (the phone can press the one fixed answer: 「代按确认」); then a real message must get a reply
            (harness.probe) — only then 「已恢复」.

P288 (到期前主动续期): the 1-year Claude token's expiry is only known because Agent J records the day it stored it (no API
tells). From 7 days before, one light reminder per expiry on day 7, 3 and 1 (a missed day fires the smallest mark crossed,
once) — text + push + a 「现在续期」 card on the phone that starts route A. Everything else has no fixed expiry (/login and
Codex ChatGPT sign-ins refresh themselves, provider keys do not expire), so once a day serve makes ONE real tool-free call
per signed-in harness (`health_check`, the bots' no-tool contract): a login failure lights the rescue at once (a token can
die early: "log out of all devices", revoked). A new credential replaces the old one the moment its few-second live check
passes (owner ruling: no "wait for the old one to run out"); a failed check keeps the old one and asks again. The
authorize link goes only to paired phones (the view), never into a notice / Telegram; it lives 10 minutes, then a new
round opens by itself (RENEW_ROUNDS).

Red lines: the code / token never reaches a log, history page, notice, relay plaintext or the Agent; PTY text is scanned in
memory and dropped. Starting a rescue needs a paired phone AND the owner's passkey (serve.ask require_passkey). Only official
CLIs and official flows: no cookies, no automation of anyone's web page.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import select
import hashlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KINDS = ("claude", "codex", "opencode", "kimi")
META = "auth-rescue.json"           # state dir, 0600: dates and outcomes only — never a code or token
TOKEN_DAYS = 365                     # `claude setup-token`: "long-lived (1-year) auth token" (2.1.296 banner, official docs)
WARN_DAYS = 7                        # P288: the phone shows 「还有 N 天」 + 现在续期 from this many days before the expiry
MARKS = (7, 3, 1)                    # P288: one reminder per expiry on each of these days left (smallest crossed mark only)
RENEW_ROUNDS = 3                     # P288: an authorize link that expired / a refused code opens a new round, this many in all
HEALTH_TIMEOUT = 60.0                # P288: one daily tool-free call per harness, bounded
HEALTH_DELAY = float(os.environ.get("AGENTJ_TEST_AUTH_HEALTH_DELAY") or 600)   # s after serve start before the first one
ALERT_GAP = 600                      # one phone alert per harness per 10 minutes
CODE_WAIT = 600                      # the Claude flow keeps its PTY (and PKCE verifier) for 10 minutes
URL_WAIT = 45
SCREEN_SECS = 25.0
TOKEN_RE = re.compile(r"sk-ant-oat01-[A-Za-z0-9_\-]{40,}")
CLAUDE_URL_RE = re.compile(r"https://(?:claude\.(?:com|ai)|console\.anthropic\.com|platform\.claude\.com)/\S*oauth/authorize\?\S+")
CODEX_URL_RE = re.compile(r"https://auth\.openai\.com/\S+")
CODEX_CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b")
# P305: `kimi login` (2.2.0, apps/kimi-code/src/cli/sub/login-flow.ts) prints on stderr "Opening browser for Kimi device login:
# <url>" / "… enter code: <code>" / "Code expires in <n>s."; the URL is on the region's auth host (auth.kimi.com · auth.kimi.ai)
KIMI_URL_RE = re.compile(r"https://(?:[a-z0-9-]+\.)*kimi\.(?:com|ai)/\S+")
KIMI_CODE_RE = re.compile(r"enter code:\s*([A-Za-z0-9-]{4,16})")
ANSI_RE = re.compile(r"\x1b\[[0-9;?<>=]*[a-zA-Z~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>78]")
CLAUDE_STRIP = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_SESSION_ID",
                "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_EFFORT", "CLAUDE_CODE_EXECPATH")
OUTRANK = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")   # official precedence: both win over CLAUDE_CODE_OAUTH_TOKEN


# ------------------------------------------------------------------ text helpers
def plain(text: str) -> str:
    """ANSI-free text (cursor-forward → space)."""
    text = re.sub(r"\x1b\[(\d*)C", lambda m: " " * min(int(m.group(1) or 1), 8), text)
    return ANSI_RE.sub("", text)


def squash(text: str) -> str:
    """Lowercase, no ANSI, no whitespace: Claude Code's TUI draws spaces as cursor moves, so needles are written without
    spaces ("doyoutrustthefilesinthisfolder")."""
    return re.sub(r"\s+", "", plain(text)).lower()


def scrub(text: str) -> str:
    """Remove anything token-shaped before a line may be shown (a blocker's last line)."""
    text = TOKEN_RE.sub("sk-ant-…", text)
    return re.sub(r"[A-Za-z0-9_\-]{32,}", "…", text)


# ------------------------------------------------------------------ classification
_BALANCE = ("creditbalanceistoolow", "insufficient_quota", "insufficientquota", "insufficientbalance", "outofcredits",
            "credits_depleted", "quotaexceeded", "paymentrequired", "checkyourplanandbilling", "balanceistoolow",
            "余额不足", "额度用完", "充值")
_RATE = ("ratelimit", "rate_limit", "toomanyrequests", "usagelimit", "usage_limit", "limitreached", "限流",
         "weeklylimit", "5-hourlimit")
_LOGIN = ("notloggedin", "pleaserun/login", "loginexpired", "authenticationrequired", "oauthtokenhasexpired",
          "tokenhasexpired", "tokenexpired", "invalidapikey", "invalidbearertoken", "authentication_error",
          "unauthorized", "unauthorised", "refreshtoken", "signinagain", "loginagain", "pleaselogin", "loggedout",
          "revoked", "登录已失效", "还没登录", "没有claude登录", "未登录")


# P423 #138: a plan's usage / quota used up is never a login, whatever status it comes with (Kimi Code 2.2.0 over ACP sends
# auth_required "[provider.auth_error] 403 You've reached your monthly usage limit for this billing cycle"). One test for every
# harness's probe and runtime; OpenCode's provider_error_reason reads USAGE_LIMIT_RE too.
USAGE_LIMIT_RE = re.compile(r"(?:weekly|monthly|daily|usage)[ _-]+(?:usage[ _-]+)?limit", re.I)
_QUOTA_USED = ("quota", "额度", "用量上限", "billingcycle", "hityourlimit", "limitreached", "limitwillreset")


def quota_used(text) -> bool:
    """A used-up plan window / quota / balance (Kimi's monthly limit, Claude's 5-hour / weekly limit, Codex's usage limit,
    insufficient_quota, a no-credit 402) — not a login, not a momentary 429."""
    if not isinstance(text, str) or not text:
        return False
    s = squash(text)
    return bool(USAGE_LIMIT_RE.search(text) or any(w in s for w in _QUOTA_USED + _BALANCE) or re.search(r"\b402\b", text)
                or re.search(r"\b403\b.*\blimit\b", text, re.I | re.S))


def classify(kind: str, text) -> str | None:
    """→ "balance" | "rate_limit" | "login" | None. Balance and rate limits are checked FIRST: a 429 / no-credit reply also
    says "please upgrade" or mentions the account, and must never start a re-login (it would not help and would cost the
    owner a sign-in)."""
    if not isinstance(text, str) or not text:
        return None
    s = squash(text)
    if any(w in s for w in _BALANCE) or re.search(r"\b402\b", text):
        return "balance"
    if any(w in s for w in _RATE) or re.search(r"\b429\b", text):
        return "rate_limit"
    if quota_used(text):                 # P423 #138: any other used-up quota wording, before the login words
        return "balance"
    if any(w in s for w in _LOGIN) or re.search(r"\b401\b", text):
        return "login"
    return None


# ------------------------------------------------------------------ the interactive screens a restart can stop at
# action: auto = fixed keys, always safe (or the same decision harness_setup already pre-answers) · confirm = the phone may
# press these fixed keys (「代按确认」) · fail = cannot be answered by keys (the login is not effective) · info = never blocks.
# Keys were measured on Claude Code 2.1.296 / Codex 0.162.1 (the trust screen's default is 「No, exit」: Enter alone exits).
DOWN, ENTER, ESC = "\x1b[B", "\r", "\x1b"
BLOCKERS = (
    # harness, id, needles (any, squashed), action, keys, zh, en
    ("claude", "trust", ("doyoutrustthefilesinthisfolder", "isthisaprojectyoucreatedoroneyoutrust", "yes,itrustthisfolder",
                         "quicksafetycheck"), "auto", DOWN + ENTER,
     "信任此文件夹（Trust this folder）", "Trust this folder"),
    ("claude", "theme", ("choosethetextstyle", "let'sgetstarted"), "auto", ENTER,
     "首次引导：选择主题", "first run: choose a theme"),
    ("claude", "login_success", ("loginsuccessful",), "auto", ENTER,
     "登录成功，按回车继续", "Login successful, press Enter"),
    ("claude", "security_notes", ("securitynotes",), "auto", ENTER,
     "首次引导：安全须知", "first run: security notes"),
    ("claude", "terminal_setup", ("useclaudecode'sterminalsetup", "recommendedsettingsforthisterminal"), "auto", ESC,
     "首次引导：终端设置（跳过）", "first run: terminal setup (skipped)"),
    ("claude", "login_method", ("selectloginmethod", "claudeaccountwithsubscription", "anthropicconsoleaccount"), "fail", "",
     "停在「选择登录方式」：新的登录没有生效", "stopped at 'Select login method': the new login is not in effect"),
    ("claude", "login_expired", ("loginexpired", "pleaserun/login", "oauthtokenhasexpired", "invalidapikey·"), "fail", "",
     "仍提示登录过期（Please run /login）", "still says the login expired (Please run /login)"),
    ("claude", "api_key", ("detectedacustomapikey", "doyouwanttousethisapikey"), "confirm", DOWN + ENTER,
     "检测到自定义 API key（代按：不用它，改用订阅）", "custom API key found (press: do not use it, use the subscription)"),
    ("claude", "bypass", ("bypasspermissionsmode", "yes,iaccept"), "confirm", DOWN + ENTER,
     "危险模式确认（Bypass Permissions，主人自己开的）", "Bypass Permissions confirmation (the owner's own setting)"),
    ("claude", "mcp", ("newmcpserverfoundin", "mcpserversfoundin.mcp.json", "usethisandallfuturemcpservers"), "confirm", ESC,
     "项目 .mcp.json 里的 MCP 服务器（代按：这次不启用）", "MCP servers in the project's .mcp.json (press: not this time)"),
    ("claude", "settings_error", ("settingserror", "invalidsettings"), "confirm", ENTER,
     "设置文件有错误（代按：忽略继续）", "settings file error (press: continue without it)"),
    ("claude", "update", ("updateavailable", "auto-updatefailed", "autoupdatefailed"), "info", "",
     "有新版本提示（不阻塞）", "update notice (does not block)"),
    ("claude", "expiring", ("yourloginexpiresin",), "info", "",
     "登录即将到期提示（不阻塞）", "login expiring notice (does not block)"),
    ("codex", "trust", ("doyoutrustthecontentsofthisdirectory", "allowcodextoworkinthisfolder",
                        "doyoutrustthisdirectory"), "auto", ENTER,
     "信任此目录（Codex）", "trust this directory (Codex)"),
    ("codex", "exec_trust", ("notinsideatrusteddirectory",), "fail", "",
     "不在受信任目录（已预写 trust_level，仍被拒）", "not inside a trusted directory (trust_level written, still refused)"),
    ("codex", "update", ("updatenow", "skipuntilnextversion"), "auto", ESC,
     "更新提示（跳过）", "update prompt (skipped)"),
    ("codex", "login", ("signinwithchatgpt", "providemyownapikey", "signinwithdevicecode"), "fail", "",
     "停在 Codex 登录界面：新的登录没有生效", "stopped at Codex sign-in: the new login is not in effect"),
    ("codex", "approval", ("chooseapermissionmode", "allowcodextoworkwithoutaskingforapproval"), "confirm", ENTER,
     "审批/沙箱模式确认（代按：默认选项）", "approval / sandbox mode prompt (press: the default)"),
    ("opencode", "login", ("opencodeauthlogin", "noproviders"), "fail", "",
     "OpenCode 没有可用的服务商登录", "OpenCode has no provider login"),
)
READY = {"claude": ("shift+tabtocycle", "forshortcuts", "❯try\""), "codex": ("askcodextodoanything", "forshortcuts"),
         "opencode": ()}


def scan(kind: str, text: str) -> list[dict]:
    """Every catalogued screen `text` shows for `kind`, in catalogue order."""
    s = squash(text)
    out = []
    for h, bid, needles, action, keys, zh, en in BLOCKERS:
        if h == kind and any(n in s for n in needles):
            out.append({"id": bid, "action": action, "keys": keys, "zh": zh, "en": en})
    return out


def confirm_keys(kind: str, bid: str) -> str | None:
    for h, b, _n, action, keys, _z, _e in BLOCKERS:
        if h == kind and b == bid and action == "confirm":
            return keys
    return None


# ------------------------------------------------------------------ binaries / env
def binary(kind: str) -> str | None:
    from .binaries import resolve
    try:
        return resolve(kind)["path"]
    except Exception:  # noqa: BLE001
        return None


def service_env_path() -> str | None:
    try:
        from . import service
        return service.env_file(service.name())
    except Exception:  # noqa: BLE001
        return None


def effective_auth_env() -> dict:
    """Read only exact Claude auth names. Loaded environment.d and set-environment are reflected by the user manager;
    the service EnvironmentFile overrides manager values. Values stay in memory, never in views/logs or metadata."""
    from . import service
    keys = (*OUTRANK, "CLAUDE_CODE_OAUTH_TOKEN")
    result = {k: os.environ[k] for k in keys if k in os.environ}
    from . import keys_found
    # Reuse the existing no-follow/owner/size-checked source reader; no new credential parser or storage.
    try:
        folder = Path(keys_found.envd_dir())
        fd = keys_found.open_dir(str(folder))
        try:
            names = sorted(n for n in os.listdir(fd) if n.endswith(".conf") and not n.startswith("."))[:64]
        finally:
            os.close(fd)
        for n in names:
            try:
                values = keys_found.parse(keys_found.read_file(str(folder / n)))
                result.update({k: values[k] for k in keys if k in values})
            except (keys_found.Unsafe, OSError):
                pass
    except (keys_found.Unsafe, OSError):
        pass
    manager = keys_found.manager_env()
    result.update({k: manager[k] for k in keys if k in manager})
    path = service_env_path()
    if path:
        result.update(service.private_env(path, keys, "bad_claude_env"))
    return result


def harness_env(kind: str, extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not (kind == "claude" and k in CLAUDE_STRIP)}
    if kind == "claude":
        env.update(effective_auth_env())
    env["TERM"] = "xterm-256color"
    env.update(extra or {})
    return env


def conflicts(environ=None) -> list[str]:
    """Names (never values) of credentials that outrank CLAUDE_CODE_OAUTH_TOKEN for Claude Code."""
    e = effective_auth_env() if environ is None else environ
    names = [k for k in OUTRANK if e.get(k)]
    p = service_env_path()
    if p:
        try:
            from . import service
            names += [k for k in OUTRANK if k not in names and service.env_file_has(p, k)]
        except Exception:  # noqa: BLE001
            pass
    return names


# ------------------------------------------------------------------ PTY driver (blocking; runs in a worker thread)
class Pty:
    """One child in a private PTY. Output is kept only in memory (bounded) for pattern scans."""

    def __init__(self, argv, cwd, env, cols=1000, rows=50):
        import pty, fcntl, termios, struct
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        try:
            self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                         start_new_session=True, close_fds=True)
        finally:
            os.close(slave)
        self.buf = ""
        self.mark = 0

    @property
    def pid(self):
        return self.proc.pid

    def pump(self, secs: float) -> bool:
        """Read for up to `secs`; False once the child closed the PTY."""
        end = time.monotonic() + secs
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return True
            try:
                r, _, _ = select.select([self.master], [], [], min(left, .2))
            except (OSError, ValueError):
                return False
            if r:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    return False
                if not data:
                    return False
                self.buf = (self.buf + data.decode("utf-8", "replace"))[-200_000:]

    def wait_for(self, pattern: re.Pattern, secs: float, text_fn=plain):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            m = pattern.search(text_fn(self.buf))
            if m:
                return m
            if not self.pump(.3) and self.proc.poll() is not None:
                return pattern.search(text_fn(self.buf))
        return None

    def send(self, text: str) -> None:
        os.write(self.master, text.encode())

    def since_mark(self) -> str:
        return self.buf[self.mark:]

    def close(self) -> None:
        if self.proc.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    self.proc.wait(timeout=3)
                    break
                except subprocess.TimeoutExpired:
                    continue
        try:
            os.close(self.master)
        except OSError:
            pass
        self.buf = ""


# ------------------------------------------------------------------ Claude: setup-token / auth login in a PTY
class ClaudeFlow:
    """route "setup_token" → `claude setup-token` (prints a 1-year token) · "login" → `claude auth login --claudeai`
    (writes ~/.claude/.credentials.json itself). Both print the official authorize URL and wait for the pasted code."""

    def __init__(self, route: str, cwd: str | None = None):
        self.route = route
        self.cwd = cwd or tempfile.gettempdir()
        self.pty: Pty | None = None
        self.url: str | None = None
        self.started = time.time()

    def start(self) -> str:
        exe = binary("claude")
        if not exe:
            raise RescueError("no_binary")
        argv = [exe, "setup-token"] if self.route == "setup_token" else [exe, "auth", "login", "--claudeai"]
        env = harness_env("claude")
        for k in ("CLAUDE_CODE_OAUTH_TOKEN", *OUTRANK):
            env.pop(k, None)          # the flow mints a new login; an old credential must not short-circuit it
        env["BROWSER"] = "true"       # no browser on a remote host: the URL is the only way, never open one here
        self.pty = Pty(argv, self.cwd, env)
        m = self.pty.wait_for(CLAUDE_URL_RE, URL_WAIT)
        if not m:
            self.close()
            raise RescueError("no_url")
        self.url = m.group(0)
        return self.url

    def submit(self, code: str) -> bytearray | None:
        """Paste the code. setup_token → the token (bytearray; caller wipes it) · login → None on success. Raises on failure."""
        if not self.pty or self.pty.proc.poll() is not None:
            raise RescueError("expired")
        code = code.strip()
        if not code or len(code) > 512 or any(ord(c) < 33 for c in code):
            raise RescueError("bad_code")
        self.pty.mark = len(self.pty.buf)
        self.pty.send(code)
        time.sleep(.3)
        self.pty.send(ENTER)
        end = time.monotonic() + 90
        while time.monotonic() < end:
            alive = self.pty.pump(.4)
            txt = plain(self.pty.since_mark())
            if self.route == "setup_token":
                m = TOKEN_RE.search(txt)
                if m:
                    tok = bytearray(m.group(0).encode())
                    self.pty.buf = ""          # the token leaves the buffer at once
                    return tok
            else:
                if "loginsuccessful" in squash(txt):
                    self.pty.send(ENTER)
                    self.pty.pump(1.0)
                    return None
            low = squash(txt)
            if any(w in low for w in ("invalidcode", "invalid_grant", "oautherror", "authorizationfailed", "error:")):
                raise RescueError("rejected")
            if not alive and self.pty.proc.poll() is not None:
                if self.route == "login" and self.pty.proc.returncode == 0:
                    return None
                raise RescueError("rejected")
        raise RescueError("timeout")

    def close(self):
        if self.pty:
            self.pty.close()
            self.pty = None


# ------------------------------------------------------------------ Codex: device code
class CodexFlow:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.url = self.code = None
        self.expires = 15 * 60
        self.backup: bytes | None = None
        self.out = ""
        self.auth = Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")) / "auth.json"

    def start(self) -> tuple[str, str, int]:
        exe = binary("codex")
        if not exe:
            raise RescueError("no_binary")
        self.had = self.auth.exists() or self.auth.is_symlink()
        try:
            self.backup = self.auth.read_bytes() if self.auth.is_file() and not self.auth.is_symlink() else None
        except OSError:
            self.backup = None
        env = harness_env("codex")
        env["NO_COLOR"] = "1"
        self.proc = subprocess.Popen([exe, "login", "--device-auth"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=env, start_new_session=True, cwd=tempfile.gettempdir())
        os.set_blocking(self.proc.stdout.fileno(), False)
        end = time.monotonic() + URL_WAIT
        while time.monotonic() < end:
            self._read(.3)
            txt = plain(self.out)
            u, c = CODEX_URL_RE.search(txt), CODEX_CODE_RE.search(txt)
            if u and c:
                self.url, self.code = u.group(0), c.group(0)
                m = re.search(r"expires in (\d+) minutes", txt)
                if m:
                    self.expires = int(m.group(1)) * 60
                return self.url, self.code, self.expires
            if self.proc.poll() is not None:
                self._read(.2)
                break
        why = "device_disabled" if re.search(r"device[^\n]{0,60}(not enabled|disabled|enable)", plain(self.out), re.I) else "no_code"
        self.close(restore=True)
        raise RescueError(why)

    def restore(self) -> None:
        """P288: put the previous auth.json back after a login whose live check failed (no previous one → remove)."""
        try:
            if self.backup is not None:
                fd, tmp = tempfile.mkstemp(dir=self.auth.parent, prefix=".auth-")
                with os.fdopen(fd, "wb") as f:
                    f.write(self.backup)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.auth)
            elif not getattr(self, "had", True) and self.auth.is_file() and not self.auth.is_symlink():
                self.auth.unlink()
        except OSError:
            pass
        self.backup = None

    def _read(self, secs):
        try:
            r, _, _ = select.select([self.proc.stdout], [], [], secs)
        except (OSError, ValueError):
            return
        if r:
            try:
                data = self.proc.stdout.read(65536)
            except (OSError, TypeError):
                data = None
            if data:
                self.out = (self.out + data.decode("utf-8", "replace"))[-50_000:]

    def wait(self, cancel: threading.Event | None = None) -> str:
        """→ "ok" | "timeout" | "failed" | "device_disabled" | "cancelled". Restores the previous auth.json unless "ok"."""
        end = time.monotonic() + self.expires + 30
        while time.monotonic() < end:
            if cancel is not None and cancel.is_set():
                self.close(restore=True)
                return "cancelled"
            self._read(.5)
            rc = self.proc.poll()
            if rc is not None:
                self._read(.1)
                if rc == 0:
                    self.close(restore=False, keep=True)     # P288: the backup stays until the live check passed
                    return "ok"
                why = "device_disabled" if re.search(r"device[^\n]{0,60}(not enabled|disabled|enable)", plain(self.out), re.I) else "failed"
                self.close(restore=True)
                return why
        self.close(restore=True)
        return "timeout"

    def close(self, restore: bool, keep: bool = False):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=3)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if self.proc and self.proc.stdout:
            try:
                self.proc.stdout.close()
            except OSError:
                pass
        if restore and self.backup is not None:
            try:
                cur = self.auth.read_bytes() if self.auth.is_file() else None
            except OSError:
                cur = None
            if cur != self.backup:
                fd, tmp = tempfile.mkstemp(dir=self.auth.parent, prefix=".agentj-p258-")
                with os.fdopen(fd, "wb") as f:
                    f.write(self.backup)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.auth)
        self.out = ""
        if not keep:
            self.backup = None


# ------------------------------------------------------------------ Kimi Code: device code (P305)
class KimiFlow(CodexFlow):
    """`kimi login --region <r>`: the same device-code shape as Codex. Kimi writes ~/.kimi-code/credentials/<slot>.json and
    may touch config.toml (its oauth ref): both are backed up as bytes and put back unless the live check passes."""

    def __init__(self, region: str | None = None):
        super().__init__()
        from . import agent_kimi
        self.region = region if region in agent_kimi.REGIONS else "mainland-cn"
        self.home = agent_kimi.home()
        self.saved: dict[Path, bytes | None] | None = None

    def _files(self) -> list[Path]:
        d = self.home / "credentials"
        try:
            creds = [p for p in d.iterdir() if p.suffix == ".json" and p.is_file() and not p.is_symlink()]
        except OSError:
            creds = []
        return [self.home / "config.toml", *creds]

    def _snapshot(self) -> None:
        self.saved = {}
        for p in self._files():
            try:
                self.saved[p] = p.read_bytes() if p.is_file() and not p.is_symlink() else None
            except OSError:
                self.saved[p] = None

    def start(self) -> tuple[str, str, int]:
        exe = binary("kimi")
        if not exe:
            raise RescueError("no_binary")
        self._snapshot()
        env = harness_env("kimi")
        env["NO_COLOR"] = "1"
        for k in ("DISPLAY", "WAYLAND_DISPLAY"):      # a remote rescue: the link is for the phone, no browser opens here
            env.pop(k, None)
        env["BROWSER"] = "true"
        self.proc = subprocess.Popen([exe, "login", "--region", self.region], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, start_new_session=True,
                                     cwd=tempfile.gettempdir())
        os.set_blocking(self.proc.stdout.fileno(), False)
        end = time.monotonic() + URL_WAIT
        while time.monotonic() < end:
            self._read(.3)
            txt = plain(self.out)
            u, c = KIMI_URL_RE.search(txt), KIMI_CODE_RE.search(txt)
            if u and c:
                self.url, self.code = u.group(0).rstrip(".,;)"), c.group(1)
                stop = time.monotonic() + 2                # the expiry is the next line: give it a moment
                while not re.search(r"expires in \d+s", txt) and time.monotonic() < stop and self.proc.poll() is None:
                    self._read(.2)
                    txt = plain(self.out)
                m = re.search(r"expires in (\d+)s", txt)
                if m:
                    self.expires = min(int(m.group(1)), 30 * 60)
                return self.url, self.code, self.expires
            if self.proc.poll() is not None:
                self._read(.2)
                break
        self.close(restore=True)
        raise RescueError("no_code_kimi")

    def restore(self) -> None:
        """Every file `kimi login` may have written goes back to its bytes before the flow (new ones are removed)."""
        if self.saved is None:
            return
        for p in self._files():
            if p not in self.saved:
                try:
                    p.unlink()
                except OSError:
                    pass
        for p, data in self.saved.items():
            try:
                if data is None:
                    if p.is_file() and not p.is_symlink():
                        p.unlink()
                    continue
                if p.is_file() and p.read_bytes() == data:
                    continue
                fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".agentj-p305-")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.chmod(tmp, 0o600)
                os.replace(tmp, p)
            except OSError:
                pass
        self.saved = None

    def close(self, restore: bool, keep: bool = False):
        self.backup = None                            # CodexFlow's single-file backup is not used here
        super().close(restore=False, keep=keep)
        if restore:
            self.restore()
        elif not keep:
            self.saved = None


class RescueError(Exception):
    """args[0]: a fixed reason word."""


# ------------------------------------------------------------------ live checks
def live_claude(token: bytearray | None, cwd: str | None = None, timeout: float = 90) -> bool:
    """A real one-line request with exactly this token (or the current login when None). Only "PONG seen" is kept."""
    exe = binary("claude")
    if not exe:
        return False
    env = harness_env("claude")
    if token is not None:
        for k in OUTRANK:
            env.pop(k, None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token.decode()
    with tempfile.TemporaryDirectory(prefix="agentj-p258-") as d:
        try:
            r = subprocess.run([exe, "-p", "Reply with exactly: PONG"], cwd=cwd or d, env=env, capture_output=True,
                               text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return False
    ok = r.returncode == 0 and "PONG" in (r.stdout or "")
    r = None
    return ok


def screen_check(kind: str, workdir: str, press: tuple = (), secs: float = SCREEN_SECS) -> dict:
    """Open the harness's own interactive screen in `workdir` for up to `secs`, answer the `auto` screens (and the `confirm`
    ids in `press`) by their fixed keys, never send a message. → {"ready": bool, "answered": [ids], "blocker": {..} | None,
    "info": [ids], "last": scrubbed last line}. OpenCode has no interactive first-run screen (serve) → ready."""
    if kind not in ("claude", "codex"):
        return {"ready": True, "answered": [], "blocker": None, "info": [], "last": ""}
    exe = binary(kind)
    if not exe:
        return {"ready": False, "answered": [], "blocker": {"id": "no_binary", "action": "fail", "zh": "找不到程序",
                                                             "en": "binary not found"}, "info": [], "last": ""}
    from . import shared
    p = Pty([exe], workdir, harness_env(kind), cols=200, rows=50)
    shared.PROBE_PIDS.add(p.pid)      # never attached to as the owner's session while it is on screen
    answered, info, presses = [], set(), 0
    out = {"ready": False, "answered": answered, "blocker": None, "info": [], "last": ""}
    try:
        end = time.monotonic() + secs
        while time.monotonic() < end:
            alive = p.pump(.5)
            view = p.since_mark()
            found = scan(kind, view)
            for b in found:
                if b["action"] == "info":
                    info.add(b["id"])
            act = next((b for b in found if b["action"] != "info"), None)
            if act:
                if act["action"] == "auto" or (act["action"] == "confirm" and act["id"] in press):
                    if presses >= 8 or answered.count(act["id"]) >= 2:
                        out["blocker"] = {k: act[k] for k in ("id", "action", "zh", "en")}
                        break
                    p.mark = len(p.buf)
                    p.send(act["keys"])
                    answered.append(act["id"])
                    presses += 1
                    continue
                out["blocker"] = {k: act[k] for k in ("id", "action", "zh", "en")}
                break
            if any(n in squash(view) for n in READY[kind]):
                p.pump(1.0)                      # a screen that pops up right after the prompt still counts
                late = [b for b in scan(kind, p.since_mark()) if b["action"] not in ("info",)]
                if late and late[0]["action"] != "auto":
                    out["blocker"] = {k: late[0][k] for k in ("id", "action", "zh", "en")}
                else:
                    out["ready"] = True
                break
            if not alive and p.proc.poll() is not None:
                out["blocker"] = {"id": "exited", "action": "fail", "zh": "程序启动后立即退出", "en": "exited right after start"}
                break
        else:
            lines = [ln.strip() for ln in re.split(r"[\r\n]+", plain(p.since_mark())) if ln.strip()]
            out["last"] = scrub(lines[-1])[:120] if lines else ""
            out["blocker"] = {"id": "unknown", "action": "fail", "zh": "停在一个没有识别的界面", "en": "stopped at an unrecognised screen"}
        out["info"] = sorted(info)
        return out
    finally:
        p.close()
        shared.PROBE_PIDS.discard(p.pid)


# ------------------------------------------------------------------ metadata (no secrets)
def _meta_path(st) -> Path:
    return Path(st.root) / META


def read_meta(st) -> dict:
    try:
        d = json.loads(_meta_path(st).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(st, d: dict) -> None:
    p = _meta_path(st)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".auth-rescue-")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def token_due(meta: dict, today: date | None = None) -> dict | None:
    """{"minted", "expires", "days_left"} for a recorded setup-token, else None."""
    c = meta.get("claude") if isinstance(meta.get("claude"), dict) else {}
    m = c.get("minted")
    if not isinstance(m, str):
        return None
    try:
        minted = date.fromisoformat(m)
    except ValueError:
        return None
    exp = minted + timedelta(days=TOKEN_DAYS)
    today = today or date.today()
    return {"minted": minted.isoformat(), "expires": exp.isoformat(), "days_left": (exp - today).days}


def next_mark(days_left: int, sent) -> int | None:
    """P288: the reminder to send today for `days_left`, given the marks already sent for this expiry. Only the smallest
    crossed mark fires (serve off on day 7 → day 5 sends the 7-day one late; nothing sent until day 2 → only the 3-day one);
    each mark once."""
    sent = set(sent or ())
    due = [m for m in MARKS if days_left <= m and m not in sent and not any(x < m for x in sent)]
    return min(due) if due else None


def has_oauth_token() -> bool:
    """A CLAUDE_CODE_OAUTH_TOKEN is in effect (this process or the service env file) — names only, never the value."""
    if effective_auth_env().get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    p = service_env_path()
    if not p:
        return False
    try:
        from . import service
        return bool(service.env_file_has(p, "CLAUDE_CODE_OAUTH_TOKEN"))
    except Exception:  # noqa: BLE001
        return False


def expiry_kind(kind: str, meta: dict) -> str:
    """P288 inventory, per harness: "known" (a setup-token Agent J stored: day + 365) · "unknown" (a setup-token stored
    elsewhere — no date to count from) · "auto" (/login, Codex ChatGPT: refreshed by the CLI itself) · "none" (provider /
    API keys: no expiry). Everything but "known" relies on the daily health check."""
    if kind == "claude":
        if token_due(meta):
            return "known"
        return "unknown" if has_oauth_token() else "auto"
    return "auto" if kind in ("codex", "kimi") else "none"


_HEALTH_ENV_DROP = ("AGENTJ_", "AGENTJARVIS_", "HERDR_", "VIBE_REMOTE_", "TMUX", "SSH_")


def health_check(kind: str, timeout: float = HEALTH_TIMEOUT) -> dict:
    """P288: ONE real tool-free call with the harness's current login (the bots' no-tool command: no tools, no MCP, no
    session saved). → {"ok": bool, "cls": "login" | "balance" | "rate_limit" | None, "why": fixed word}. The output is
    classified in memory and dropped; nothing of it is logged or returned."""
    from types import SimpleNamespace
    from .bots import native
    exe = binary(kind)
    if not exe:
        return {"ok": False, "cls": None, "why": "no_binary"}
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_HEALTH_ENV_DROP) and not (kind == "claude" and k in CLAUDE_STRIP)}
    if kind == "claude":
        env.update(effective_auth_env())
    if kind == "kimi":                    # P305: no one-shot print mode — one ACP session in plan mode (agent_kimi.probe)
        from . import agent_kimi
        try:
            ok = bool(agent_kimi.probe(exe, env, timeout).strip())
        except agent_kimi.ProbeError as e:
            why = str(e.args[0]) if e.args else "failed"
            cls = {"not_logged_in": "login", "quota_exhausted": "balance", "rate_limited": "rate_limit"}.get(why)
            return {"ok": False, "cls": cls, "why": cls or ("timeout" if why == "native_timeout" else "failed")}
        return {"ok": ok, "cls": None, "why": "ok" if ok else "failed"}
    out = err = ""
    with tempfile.TemporaryDirectory(prefix="agentj-p288-") as d:
        root = Path(d)
        (root / "system.md").write_text("Reply to the supplied greeting only. No tools are available.")
        try:
            cmd = native.command(SimpleNamespace(name=kind, model=None), root, env, selected=exe)
        except Exception:  # noqa: BLE001 — a BotError (provider config) is not a login problem
            return {"ok": False, "cls": None, "why": "config"}
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                    cwd=d, start_new_session=True, text=True)
        except OSError:
            return {"ok": False, "cls": None, "why": "spawn"}
        try:
            out, err = proc.communicate("Hello. Reply with a short greeting only.", timeout=timeout)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                    proc.wait(timeout=3)
                    break
                except (ProcessLookupError, PermissionError):
                    break
                except subprocess.TimeoutExpired:
                    continue
            return {"ok": False, "cls": None, "why": "timeout"}
    out, err = (out or "")[-200_000:], (err or "")[-50_000:]
    ok = False
    if proc.returncode == 0:
        try:
            reply, _ = native.parse(kind, out)
            ok = isinstance(reply, str) and bool(reply.strip())
        except Exception:  # noqa: BLE001
            ok = False
    cls = None if ok else classify(kind, out + "\n" + err)
    out = err = ""
    return {"ok": ok, "cls": cls, "why": "ok" if ok else (cls or "failed")}


def health_targets() -> list[str]:
    """The harnesses worth a daily call: installed, supported, and not known to be signed out (a harness the owner never
    signed in to is not checked — no alert for something never used)."""
    from . import harness
    try:
        d = harness.detect()
    except Exception:  # noqa: BLE001
        return []
    return [h["name"] for h in d.get("harnesses", []) if h.get("name") in KINDS and h.get("installed")
            and h.get("supported") and h.get("logged_in") is not False]


def heartbeat_line(st, lang: str) -> str | None:
    """P288: one line at the end of a heartbeat while the Claude token is inside its last WARN_DAYS."""
    due = token_due(read_meta(st))
    if not due or due["days_left"] > WARN_DAYS:
        return None
    d = max(due["days_left"], 0)
    if lang == "en":
        return (f"Agent J: the Claude Code login expires in {d} days ({due['expires']}). Tell the owner: Settings → Emergency repair → AI login "
                "rescue → Renew now (one sign-in on the phone; never ask for the token in chat).")
    return (f"Agent J 提示：Claude Code 登录还有 {d} 天到期（{due['expires']}）。请提醒主人：设置 → 紧急修复 → AI 登录救援 → 现在续期"
            "（手机上授权一次即可；绝不在对话里索要 token）。")


# ------------------------------------------------------------------ the manager serve owns
TEXT = {
    "zh": {
        "alert_login": "{h} 的订阅登录已过期，Agent J 暂时不能回复。打开 设置 → 紧急修复 → AI 登录救援，在手机上一键重新登录（不用回Agent J 主机）。",
        "alert_balance": "{h} 的余额 / 额度不足：到服务商网站充值即可，不需要重新登录。",
        "warn_token": "Claude Code 登录还有 {d} 天到期（{e}）。打开 设置 → 紧急修复 → AI 登录救援 → 现在续期：在手机上授权一次就换上新的，不用回Agent J 主机。",
        "warn_today": "Claude Code 登录今天到期（{e}）。打开 设置 → 紧急修复 → AI 登录救援 → 现在续期：在手机上授权一次就换上新的，不用回Agent J 主机。",
        "health_login": "每日体检：{h} 的登录已失效（可能在网页上退出了全部设备，或被官方撤销）。打开 设置 → 紧急修复 → AI 登录救援，在手机上重新登录。",
        "recovered": "{h} 已重新登录并恢复：重启后没有待确认的界面，测试消息已拿到回复。",
        "partial": "{h} 新登录已写入，但自检没有通过：{why}",
        "confirm_title": "AI 登录救援：重新登录 {h}",
    },
    "en": {
        "alert_login": "{h}'s subscription login expired; Agent J cannot reply. Open Settings → Emergency repair → AI login rescue to sign in "
                       "again from the phone (no need to go back to the Agent J host).",
        "alert_balance": "{h} is out of balance / quota: top up on the provider's site — no new sign-in needed.",
        "warn_token": "The Claude Code login expires in {d} days ({e}). Settings → Emergency repair → AI login rescue → Renew now: approve once on "
                      "the phone and the new one is in place — no need to go back to the Agent J host.",
        "warn_today": "The Claude Code login expires today ({e}). Settings → Emergency repair → AI login rescue → Renew now: approve once on the "
                      "phone and the new one is in place — no need to go back to the Agent J host.",
        "health_login": "Daily check: {h}'s login no longer works (signed out of all devices on the web, or revoked). Open "
                        "Settings → Emergency repair → AI login rescue to sign in again from the phone.",
        "recovered": "{h} is signed in again: no prompt waiting after the restart, and a test message got a reply.",
        "partial": "{h}'s new login is written, but the self-check failed: {why}",
        "confirm_title": "AI login rescue: sign in to {h} again",
    },
}
LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "kimi": "Kimi Code"}
WHY = {
    "zh": {"no_binary": "这台Agent J 主机上找不到这个程序", "no_url": "官方登录命令没有给出授权链接", "no_code": "Codex 没有给出设备码",
           "device_disabled": "Codex 设备码登录未开启：先在 ChatGPT 设置 → 安全 里打开「设备码登录」（官方要求），再点一次",
           "expired": "这次登录已超时，请重新开始", "bad_code": "授权码格式不对（只粘贴浏览器给出的那一串）",
           "rejected": "官方拒绝了这个授权码（过期或已用过），请重新开始", "timeout": "等待超时，请重新开始",
           "bad_token": "这不是 Claude 长期 token（应以 sk-ant-oat01- 开头）", "verify": "用这个 token 发测试消息没有拿到回复（token 无效或已撤销）",
           "conflict": "这台Agent J 主机还设置了 {n}，它的优先级高于订阅 token：请删掉它（在服务 env 文件或环境变量里）",
           "cancelled": "已取消", "failed": "官方登录命令失败", "busy": "已有一次救援在进行", "passkey": "需要主人通行密钥确认",
           "probe": "测试消息没有拿到回复", "io": "写入失败", "unsupported": "这个 AI 不支持这种方式",
           "verify_codex": "新的 Codex 登录发测试消息没有拿到回复：已保留原来的登录，请重新开始",
           "no_code_kimi": "Kimi Code 没有给出设备码（kimi 版本太旧或连不上 kimi.com / kimi.ai）",
           "verify_kimi": "新的 Kimi Code 登录发测试消息没有拿到回复：已恢复原来的登录文件，请重新开始",
           "quota": "登录本身没问题，是额度 / 用量已用完（例如 Kimi 本月额度）：不用再重新登录，续费或加量后再试，或先切到别的 Agent",
           "owner_session": "你在Agent J 主机上自己开的 {h} 窗口要重开一次才会用到新登录（Agent J 自己的会话已恢复）"},
    "en": {"no_binary": "this program is not installed here", "no_url": "the official login did not print an authorize link",
           "no_code": "Codex did not print a device code",
           "device_disabled": "Codex device-code login is off: turn on 'device code login' in ChatGPT → Settings → Security (official requirement), then retry",
           "expired": "this sign-in timed out, start again", "bad_code": "that is not an authorization code (paste only the code the browser shows)",
           "rejected": "the official service refused the code (expired or used), start again", "timeout": "timed out, start again",
           "bad_token": "not a long-lived Claude token (starts with sk-ant-oat01-)", "verify": "a test message with this token got no reply (invalid or revoked)",
           "conflict": "the Agent J host also sets {n}, which outranks the subscription token: remove it (service env file or environment)",
           "cancelled": "cancelled", "failed": "the official login command failed", "busy": "a rescue is already running",
           "passkey": "the owner's passkey is required", "probe": "the test message got no reply", "io": "could not write",
           "verify_codex": "a test message with the new Codex login got no reply: the previous login is kept, start again",
           "no_code_kimi": "Kimi Code did not print a device code (kimi too old, or kimi.com / kimi.ai unreachable)",
           "verify_kimi": "a test message with the new Kimi Code login got no reply: the previous login files are back, start again",
           "quota": "the login is fine — the plan's quota / usage is used up (e.g. Kimi's monthly limit): no new sign-in needed; renew or add usage, then retry, or switch to another Agent for now",
           "unsupported": "not supported for this AI",
           "owner_session": "the {h} window you opened yourself on the Agent J host must be reopened to use the new login (Agent J's own session is back)"},
}


class Rescue:
    """serve's one instance: status for the phone, alerts, the flows, recovery and self-check."""

    def __init__(self, host):
        self.host = host
        self.state: dict[str, dict] = {}       # kind → {"cls", "at"} (memory: a restart re-detects)
        self.alerted: dict[str, float] = {}
        self.flow: dict | None = None          # {"kind", "route", "stage", "url"?, "code"?, "expires_at"?, "obj"}
        self.last: dict[str, dict] = {}        # kind → {"result", "why"?, "at", "screen"?}
        self.cancel = threading.Event()
        self.health_task = None
        self.checking = False
        self.credential_stamp = None
        self.health_generation = 0
        self.invalid_health = False

    # ---- helpers
    def lang(self) -> str:
        return "en" if getattr(self.host, "lang", "zh") == "en" else "zh"

    def t(self, key, **kw) -> str:
        return TEXT[self.lang()][key].format(**kw)

    def why(self, code, **kw) -> str:
        return WHY[self.lang()].get(code, code).format(**kw)

    def workdir(self) -> str:
        cfg = getattr(self.host, "agent_cfg", None) or {}
        return cfg.get("dir") or os.path.expanduser("~")

    def current(self) -> str | None:
        a = getattr(self.host, "agent", None)
        return getattr(a, "kind", None) if a is not None else None

    # ---- detection
    def signal(self, kind: str, cls: str | None, text_key: str = "alert_login") -> None:
        """A harness failure was classified. login → dot + one alert (≤ 1 per ALERT_GAP); balance → a top-up hint, no dot."""
        if kind not in KINDS or cls not in ("login", "balance", "rate_limit"):
            return
        self.state[kind] = {"cls": cls, "at": int(time.time())}
        try:
            self.host.st.log("auth_rescue_signal", agent=kind, status=cls)
        except Exception:  # noqa: BLE001
            pass
        now = time.monotonic()
        if cls == "login" and now - self.alerted.get(kind, -1e9) > ALERT_GAP:
            self.alerted[kind] = now
            self.host.agent_notice(self.t(text_key, h=LABEL[kind]))
            self.broadcast()
            push = getattr(self.host, "push_notify", None)
            if push:
                try:
                    push("security")
                except Exception:  # noqa: BLE001
                    pass
        elif cls != "login":
            self.broadcast()

    def text_signal(self, kind: str, text) -> str | None:
        cls = classify(kind, text)
        if cls:
            self.signal(kind, cls)
        return cls

    def clear(self, kind):
        self.state.pop(kind, None)

    # ---- phone view
    def view(self) -> dict:
        from . import harness
        meta = read_meta(self.host.st)
        health = meta.get("health") if isinstance(meta.get("health"), dict) else {}
        rows = []
        for k in KINDS:
            exe = binary(k)
            st = self.state.get(k, {})
            r = {"kind": k, "label": LABEL[k], "installed": bool(exe), "current": self.current() == k,
                 "state": "checking" if self.checking else "unknown" if self.invalid_health else st.get("cls") or "ok", "routes": {"claude": ["setup_token", "paste", "login"],
                                                             "codex": ["device"], "opencode": ["provider_key"],
                                                             "kimi": ["device"]}[k]}
            r["expiry"] = expiry_kind(k, meta)                   # P288: known / unknown / auto / none
            hc = health.get(k)
            if not self.checking and isinstance(hc, dict) and not hc.get("ok") and not st.get("cls"):
                r["state"] = "unknown"
            if isinstance(hc, dict) and not (self.checking or self.invalid_health):
                r["health"] = {x: hc[x] for x in ("at", "ok", "cls") if x in hc}
            if k == "claude":
                due = token_due(meta)
                if due:
                    r["token"] = due
                    r["warn"] = due["days_left"] <= WARN_DAYS
                try:
                    r["login"] = harness.claude_login()[0]
                except Exception:  # noqa: BLE001
                    pass
                r["conflicts"] = conflicts()
            if k in self.last and not (self.checking or self.invalid_health):
                r["last"] = self.last[k]
            rows.append(r)
        f = self.flow
        flow = None
        if f:
            flow = {k: f[k] for k in ("kind", "route", "stage", "url", "code", "expires_at", "why", "round", "rounds",
                                                 "reopened", "region", "saved")
                    if f.get(k) is not None}
        dot = any(r["state"] == "login" or r.get("warn") for r in rows)
        out = {"harnesses": rows, "flow": flow, "dot": dot}
        due = token_due(meta)
        if due and 0 <= due["days_left"] <= WARN_DAYS and rows[0]["installed"]:
            rem = meta["claude"].get("reminded") if isinstance(meta["claude"].get("reminded"), dict) else {}
            marks = rem.get("marks") if rem.get("expires") == due["expires"] and isinstance(rem.get("marks"), list) else []
            out["renew"] = {"kind": "claude", "days_left": max(due["days_left"], 0), "expires": due["expires"],
                            "mark": min(marks) if marks else None}   # P288: the phone's 「现在续期」 card (dismissed per mark)
        return out

    def needs_dot(self) -> bool:
        """Cheap: a login alert in memory, or a flow running, or a recorded token inside its last WARN_DAYS."""
        if any(v.get("cls") == "login" for v in self.state.values()) or (self.flow and self.flow.get("stage") not in ("done", "failed")):
            return True
        due = token_due(read_meta(self.host.st))
        return bool(due and due["days_left"] <= WARN_DAYS)

    def broadcast(self) -> None:
        """Every ready phone gets the fresh view (the view reads files: built in a worker thread, never on the loop)."""
        send = getattr(self.host, "_send_p33", None)
        post = getattr(self.host, "_post", None)
        if not (send and post):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def go():
            v = await asyncio.to_thread(self.view)
            post(send, lambda s, v=v: {"t": "auth_rescue", "view": v})
        bg = getattr(self.host, "_bg", None)
        (bg(go(), "auth_view") if bg else asyncio.ensure_future(go()))

    # ---- start (passkey) and the flows
    async def start(self, kind: str, route: str) -> dict:
        if kind not in KINDS:
            return {"ok": False, "why": "unsupported"}
        allowed = {"claude": ("setup_token", "paste", "login"), "codex": ("device",), "opencode": ("provider_key",),
                   "kimi": ("device",)}[kind]
        if route not in allowed:
            return {"ok": False, "why": "unsupported"}
        if self.flow and self.flow.get("stage") not in ("done", "failed"):
            return {"ok": False, "why": "busy"}
        if kind == "opencode":
            return await self._opencode()
        ok = await self._confirm(kind)
        if not ok:
            return {"ok": False, "why": "passkey"}
        self.cancel.clear()
        self.flow = {"kind": kind, "route": route, "stage": "starting"}
        self.broadcast()
        try:
            self.host.st.log("auth_rescue_start", agent=kind, status=route)
        except Exception:  # noqa: BLE001
            pass
        coro = {"device": self._kimi if kind == "kimi" else self._codex, "setup_token": self._claude_pty,
                "login": self._claude_pty, "paste": self._claude_paste}[route]
        bg = getattr(self.host, "_bg", None)
        (bg(coro(), "auth_rescue") if bg else asyncio.ensure_future(coro()))
        return {"ok": True}

    async def _confirm(self, kind: str) -> bool:
        ask = getattr(self.host, "ask", None)
        if ask is None:
            return False
        try:
            res = await ask("AuthRescue", {"summary": self._summary(kind)}, batch=False, require_passkey=True)
        except Exception:  # noqa: BLE001
            return False
        return isinstance(res, dict) and res.get("behavior") == "allow"

    def _summary(self, kind: str) -> str:
        if self.lang() == "en":
            return (self.t("confirm_title", h=LABEL[kind]) + ".\nAgent J runs the official login command on the Agent J host "
                    "and shows you its link / code. The new credential is stored only on the Agent J host (0600). Owner passkey required.")
        return (self.t("confirm_title", h=LABEL[kind]) + "。\nAgent J 会在这台Agent J 主机上运行官方登录命令，把授权链接 / 设备码给你；"
                "新凭据只存在这台Agent J 主机上（0600）。需主人通行密钥批准。")

    def _end(self, kind, result, why=None, **extra):
        self.credential_stamp = None  # recheck after any active flow that a foreground poll had to skip
        self.flow = {"kind": kind, "route": (self.flow or {}).get("route"), "stage": "done" if result == "recovered" else "failed",
                     **({"why": self.why(why, **extra.pop("fmt", {}))} if why else {})}
        self.last[kind] = {"result": result, "at": int(time.time()), **({"why": why} if why else {}), **extra}
        try:
            self.host.st.log("auth_rescue_end", agent=kind, status=result, reason=why)
        except Exception:  # noqa: BLE001
            pass
        self.broadcast()

    async def _codex(self):
        flow = CodexFlow()
        try:
            url, code, ttl = await asyncio.to_thread(flow.start)
        except RescueError as e:
            return self._end("codex", "failed", e.args[0])
        self.flow.update(stage="device", url=url, code=code, expires_at=int(time.time()) + ttl, round=1, rounds=1)
        self.broadcast()
        res = await asyncio.to_thread(flow.wait, self.cancel)
        if res != "ok":
            return self._end("codex", "failed", res)
        # P288: the CLI already wrote the new auth.json; a few-second live call decides. Failure → the previous bytes back.
        self.flow.update(stage="verifying", saved=True)
        self.broadcast()
        hc = await asyncio.to_thread(health_check, "codex")
        if not hc["ok"]:
            await asyncio.to_thread(flow.restore)
            return self._end("codex", "failed", "quota" if hc.get("cls") in ("balance", "rate_limit") else "verify_codex")
        self._health_mark("codex", hc)
        await self.recover("codex")

    async def _kimi(self):
        """P305: `kimi login --region <the configured site>` — the Codex shape; a failed live check puts the files back."""
        flow = KimiFlow((getattr(self.host, "agent_cfg", None) or {}).get("region"))
        try:
            url, code, ttl = await asyncio.to_thread(flow.start)
        except RescueError as e:
            return self._end("kimi", "failed", e.args[0])
        self.flow.update(stage="device", url=url, code=code, expires_at=int(time.time()) + ttl, region=flow.region)
        self.broadcast()
        res = await asyncio.to_thread(flow.wait, self.cancel)
        if res != "ok":
            return self._end("kimi", "failed", res if res != "device_disabled" else "failed")
        self.flow.update(stage="verifying", saved=True)
        self.broadcast()
        hc = await asyncio.to_thread(health_check, "kimi")
        if not hc["ok"]:
            await asyncio.to_thread(flow.restore)
            return self._end("kimi", "failed", "quota" if hc.get("cls") in ("balance", "rate_limit") else "verify_kimi")
        self._health_mark("kimi", hc)
        await self.recover("kimi")

    async def _claude_pty(self):
        """P288: up to RENEW_ROUNDS rounds. The authorize link (and its PKCE verifier) lives CODE_WAIT; a link that expired
        unused or a code the official service refused opens a fresh round by itself — the phone gets the new link."""
        route = self.flow["route"]
        for rnd in range(1, RENEW_ROUNDS + 1):
            flow = ClaudeFlow(route)
            try:
                url = await asyncio.to_thread(flow.start)
            except RescueError as e:
                return self._end("claude", "failed", e.args[0])
            if self.cancel.is_set():
                await asyncio.to_thread(flow.close)
                return None
            self.flow.update(stage="url", url=url, expires_at=int(time.time()) + CODE_WAIT, round=rnd,
                             rounds=RENEW_ROUNDS)
            self.broadcast()
            outcome: dict = {}
            res = await self._claude_round(flow, route, outcome)
            if res.get("result") == "saved":
                self.flow["saved"] = True
                return await self.recover("claude", route=route)
            if self.cancel.is_set():
                return None                       # cancel_flow already ended it
            why = outcome.get("why") or ("expired" if res.get("result") in ("expired", "gone") else
                                         "cancelled" if res.get("result") in ("denied", "cancelled") else "failed")
            if why not in ("expired", "rejected", "timeout") or rnd == RENEW_ROUNDS:
                return self._end("claude", "failed", why)
            self.flow.update(stage="starting", url=None, expires_at=None, reopened=why)
            try:
                self.host.st.log("auth_rescue_round", agent="claude", status=str(rnd + 1), reason=why)
            except Exception:  # noqa: BLE001
                pass
            self.broadcast()
        return None

    async def _claude_round(self, flow, route, outcome: dict) -> dict:
        def sink(value: bytearray) -> dict:
            try:
                code = bytes(value).decode("utf-8")
            except UnicodeDecodeError:
                return {"result": "failed", "why": "value"}
            try:
                tok = flow.submit(code)
            except RescueError as e:
                outcome["why"] = e.args[0]
                return {"result": "failed", "why": "verify", "detail": self.why(e.args[0])}
            if route == "login":
                outcome["ok"] = True
                return {"result": "saved", "receipt": {"name": "Claude /login", "dest": "~/.claude/.credentials.json",
                                                       "length": 0, "verify": "ok"}}
            return self._store_token(tok, outcome)

        card = self._card("CLAUDE_AUTH_CODE",
                          "Claude 官方授权页给出的授权码（Authentication Code）：只在这张卡里粘贴，不要发到聊天"
                          if self.lang() == "zh" else
                          "The authorization code from Claude's official page — paste it only into this card, never into chat",
                          sink)
        card["auth"] = {k: self.flow.get(k, "") for k in ("kind", "url", "code", "expires_at", "round", "rounds")}
        try:
            return await self.host.elevate.request(card)
        finally:
            await asyncio.to_thread(flow.close)

    async def _claude_paste(self):
        self.flow.update(stage="paste")
        self.broadcast()
        outcome: dict = {}

        def sink(value: bytearray) -> dict:
            tok = bytearray(bytes(value).strip())
            return self._store_token(tok, outcome)

        card = self._card("CLAUDE_CODE_OAUTH_TOKEN",
                          "在任何一台装了 Claude Code 的Agent J 主机运行 `claude setup-token` 得到的长期 token（sk-ant-oat01-…）；先实测再存进本机服务 env 文件（0600）"
                          if self.lang() == "zh" else
                          "The long-lived token from `claude setup-token` on any computer with Claude Code (sk-ant-oat01-…); "
                          "live-checked, then stored in the Agent J host's service env file (0600)", sink)
        res = await self.host.elevate.request(card)
        if res.get("result") != "saved":
            return self._end("claude", "failed", outcome.get("why") or "cancelled")
        self.flow["saved"] = True
        await self.recover("claude", route="paste")

    def _card(self, name, purpose, sink) -> dict:
        zh = self.lang() == "zh"
        return {"kind": "secret", "name": name, "purpose": purpose,
                "dest": "本机 Agent J 服务 env 文件（0600）" if zh else "the Agent J host's Agent J service env file (0600)",
                "dest_spec": {"mode": "auth", "path": service_env_path() or "", "key": name, "show": "auth"},
                "cwd": "/", "verify": "claude -p (live)", "verify_url": "", "verify_header": "", "verify_cmd": "", "sink": sink}

    def _store_token(self, tok: bytearray, outcome: dict) -> dict:
        """Worker thread: check shape → live check with exactly this token → write the service env file → this process's env."""
        from . import elevate
        try:
            s = tok.decode("utf-8", "replace")
            if not re.fullmatch(r"sk-ant-oat01-[A-Za-z0-9_\-]{40,400}", s):
                outcome["why"] = "bad_token"
                return {"result": "failed", "why": "value", "detail": self.why("bad_token")}
            if not live_claude(tok):
                outcome["why"] = "verify"
                return {"result": "failed", "why": "verify", "detail": self.why("verify")}
            path = service_env_path()
            if not path:
                outcome["why"] = "io"
                return {"result": "failed", "why": "io"}
            try:
                Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)   # a foreground serve has no unit folder yet
                elevate.write_secret({"mode": "env", "path": path, "key": "CLAUDE_CODE_OAUTH_TOKEN"}, tok)
            except (elevate.Refused, OSError):
                outcome["why"] = "io"
                return {"result": "failed", "why": "io"}
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = s
            meta = read_meta(self.host.st)
            old = token_due(meta)
            # P288: replaced at once, the expiry recounted from today; the reminder marks start over for the new expiry
            meta["claude"] = {"minted": date.today().isoformat(), "route": "setup_token",
                              **({"replaced": old["expires"]} if old else {})}
            h = meta.get("health") if isinstance(meta.get("health"), dict) else {}
            h["claude"] = {"at": int(time.time()), "ok": True}
            meta["health"] = h
            write_meta(self.host.st, meta)
            outcome["ok"] = True
            return {"result": "saved", "receipt": {"name": "CLAUDE_CODE_OAUTH_TOKEN", "dest": "service env", "length": len(tok),
                                                   "fingerprint": elevate.fingerprint(tok), "verify": "ok"}}
        finally:
            for i in range(len(tok)):
                tok[i] = 0

    async def _opencode(self) -> dict:
        """OpenCode logs in per provider: the existing provider key card (P33 provider_key) is the rescue."""
        from . import provider_runtime
        ps = [k for k in provider_runtime.configured() if provider_runtime.key_name(k)]
        if not ps:
            return {"ok": False, "why": "unsupported"}
        ok = await self._confirm("opencode")
        if not ok:
            return {"ok": False, "why": "passkey"}
        bg = getattr(self.host, "_bg", None)
        coro = provider_runtime.request_key(self.host, ps[0])
        (bg(coro, "auth_rescue") if bg else asyncio.ensure_future(coro))
        return {"ok": True, "provider": ps[0]}

    async def cancel_flow(self) -> dict:
        self.cancel.set()
        if self.flow and self.flow.get("stage") not in ("done", "failed"):
            self._end(self.flow["kind"], "failed", "cancelled")
        return {"ok": True}

    # ---- restart + verify
    async def recover(self, kind: str, route: str | None = None, press: tuple = ()) -> dict:
        """Re-answer first-run screens → restart what Agent J owns → screen check → a real message. Reports to the phone."""
        from . import harness_setup, harness
        self.flow = {**(self.flow or {}), "kind": kind, "stage": "verifying"}
        self.broadcast()
        wd = self.workdir()
        if harness_setup.enabled():
            await asyncio.to_thread(harness_setup.prepare, kind, wd)
        if kind == "claude" and route != "login":
            bad = conflicts()
            if bad:
                self._end(kind, "partial", "conflict", fmt={"n": "、".join(bad) if self.lang() == "zh" else ", ".join(bad)})
                self.host.agent_notice(self.t("partial", h=LABEL[kind], why=self.why("conflict", n=", ".join(bad))))
                return {"ok": False, "why": "conflict"}
        note = self._restart(kind)
        screen = await asyncio.to_thread(screen_check, kind, wd, tuple(press))
        if screen.get("blocker"):
            b = screen["blocker"]
            extra = {"screen": {"id": b["id"], "action": b["action"], "label": b[self.lang()],
                                **({"last": screen["last"]} if screen.get("last") else {})}}
            self._end(kind, "blocked", None, **extra)
            self.flow["why"] = b[self.lang()]
            self.broadcast()
            self.host.agent_notice(self.t("partial", h=LABEL[kind], why=b[self.lang()] +
                                          ("（可在 设置 → 紧急修复 → AI 登录救援 里点「代按确认」）" if b["action"] == "confirm" and self.lang() == "zh"
                                           else " (Settings → Emergency repair → AI login rescue → Press it for me)" if b["action"] == "confirm" else "")))
            return {"ok": False, "why": "blocked", "screen": b["id"]}
        d = await asyncio.to_thread(harness.probe, (kind,))
        row = next((h for h in d.get("harnesses", []) if h.get("name") == kind), {})
        if not row.get("callable"):
            self._end(kind, "partial", "probe", screen={"answered": screen.get("answered", [])})
            self.host.agent_notice(self.t("partial", h=LABEL[kind], why=self.why("probe")))
            return {"ok": False, "why": "probe"}
        self.health_generation += 1  # invalidate any daily/foreground call that began before this real reply
        self._health_mark(kind, {"ok": True})
        self.invalid_health = False
        self.clear(kind)
        extra = {"answered": screen.get("answered", [])}
        if note:
            extra["note"] = note
        self._end(kind, "recovered", None, **extra)
        msg = self.t("recovered", h=LABEL[kind])
        if note:
            msg += "\n" + self.why(note, h=LABEL[kind])
        self.host.agent_notice(msg)
        return {"ok": True}

    def _restart(self, kind: str) -> str | None:
        """Owned harness → restart before its next turn (new env, conversation kept). Shared: a terminal Agent J started is
        replaced on the next turn; the owner's own terminal session is theirs → a note, never killed."""
        a = getattr(self.host, "agent", None)
        if a is None or getattr(a, "kind", None) != kind:
            return None
        try:
            if a.owns_harness():
                a.request_restart("auth_rescue")
                return None
        except Exception:  # noqa: BLE001
            return None
        native = getattr(a, "native", None)
        if native is not None and getattr(a, "master", None) is not None:
            try:
                os.killpg(native.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, AttributeError):
                pass
            return None
        return "owner_session"

    async def press(self, kind: str, bid: str) -> dict:
        """「代按确认」: re-run the restart check allowing exactly this catalogued confirm screen's fixed keys."""
        if confirm_keys(kind, bid) is None:
            return {"ok": False, "why": "unsupported"}
        if not await self._confirm(kind):
            return {"ok": False, "why": "passkey"}
        bg = getattr(self.host, "_bg", None)
        coro = self.recover(kind, press=(bid,))
        (bg(coro, "auth_rescue") if bg else asyncio.ensure_future(coro))
        return {"ok": True}

    # ---- daily (P288: 7 / 3 / 1 reminders + the health check)
    def daily(self, today: date | None = None) -> str | None:
        """Zero-token local check: the recorded token's days left → at most one reminder per mark (MARKS) per expiry.
        → the text sent, or None."""
        meta = read_meta(self.host.st)
        due = token_due(meta, today)
        if not due or due["days_left"] > WARN_DAYS or due["days_left"] < 0:
            return None                   # past the date: the daily health check reports the real failure
        c = meta["claude"]
        rem = c.get("reminded") if isinstance(c.get("reminded"), dict) else {}
        if rem.get("expires") != due["expires"] or not isinstance(rem.get("marks"), list):
            rem = {"expires": due["expires"], "marks": []}
        m = next_mark(due["days_left"], rem["marks"])
        if m is None:
            return None
        rem["marks"] = sorted(set(rem["marks"]) | {x for x in MARKS if x >= m}, reverse=True)
        c["reminded"] = rem
        c.pop("warned", None)
        write_meta(self.host.st, meta)
        d = max(due["days_left"], 0)
        text = self.t("warn_token", d=d, e=due["expires"]) if d > 0 else self.t("warn_today", e=due["expires"])
        try:
            self.host.st.log("auth_renew_remind", agent="claude", status=str(m))
        except Exception:  # noqa: BLE001
            pass
        self.host.agent_notice(text)
        self.broadcast()
        push = getattr(self.host, "push_notify", None)
        if push:
            try:
                push("security")
            except Exception:  # noqa: BLE001
                pass
        return text

    def _health_mark(self, kind: str, res: dict) -> None:
        meta = read_meta(self.host.st)
        h = meta.get("health") if isinstance(meta.get("health"), dict) else {}
        h[kind] = {"at": int(time.time()), "ok": bool(res.get("ok")), **({"cls": res["cls"]} if res.get("cls") else {})}
        meta["health"] = h
        write_meta(self.host.st, meta)

    def _credential_stamp(self):
        # Only a memory-local digest. Login files contribute inode/timestamp metadata, never their contents.
        h = hashlib.sha256(json.dumps(effective_auth_env(), sort_keys=True).encode())
        paths = [Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")) / "auth.json",
                 Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")) / ".credentials.json"]
        paths.append(Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")) / "settings.json")
        if sys.platform == "darwin":
            keychains = Path.home() / "Library/Keychains"
            paths.append(keychains)
            # Native Claude /login uses Keychain; watch bounded metadata only, never read a Keychain value.
            try:
                paths.extend(sorted(keychains.glob("*.keychain*"))[:64])
            except OSError:
                pass
        for p in paths:
            try:
                st = p.stat(follow_symlinks=False)
                h.update(str((st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode)).encode())
            except OSError:
                h.update(b"missing")
        return h.digest()

    async def refresh_health(self, force=True):
        """Opening/manual check tests live; foreground polling detects replacements. One shared flight."""
        current = await asyncio.to_thread(self._credential_stamp)
        if self.health_task and not self.health_task.done():
            return
        if not force and current == self.credential_stamp:
            return
        self.checking = True
        self.health_generation += 1
        self.broadcast()
        async def run():
            nonlocal current
            self.invalid_health = True
            try:
                for _ in range(3):
                    generation = self.health_generation
                    targets = await asyncio.to_thread(health_targets)
                    for k in ("claude", "codex"):
                        if k not in targets and await asyncio.to_thread(binary, k):
                            targets.append(k)  # a stale detector saying signed out cannot suppress the check
                    results = {}
                    for k in targets:
                        if self.flow and self.flow.get("kind") == k and self.flow.get("stage") not in ("done", "failed"):
                            continue
                        results[k] = await asyncio.to_thread(health_check, k)
                    latest = await asyncio.to_thread(self._credential_stamp)
                    if latest != current or generation != self.health_generation:
                        current = latest
                        continue  # stay checking; never paint the old conclusion between retries
                    for k, res in results.items():
                        self._health_mark(k, res)
                        self.state.pop(k, None)
                        if res.get("cls"):
                            self.state[k] = {"cls": res["cls"], "at": int(time.time())}
                    self.credential_stamp = current
                    self.invalid_health = False
                    return
                self.credential_stamp = None  # rapid replacements: unknown until the next foreground poll
            finally:
                self.checking = False
                self.broadcast()
        bg = getattr(self.host, "_bg", None)
        self.health_task = bg(run(), "auth_health") if bg else asyncio.create_task(run())

    async def health_daily(self, today: date | None = None, force: bool = False) -> dict:
        """Once a day: one tool-free call per signed-in harness. login → the rescue alert at once (dot + push);
        balance → the top-up hint; rate limit / a network failure → recorded only. A pass clears a stale login dot.
        → {kind: result} (empty when already done today)."""
        day = (today or date.today()).isoformat()
        meta = read_meta(self.host.st)
        h = meta.get("health") if isinstance(meta.get("health"), dict) else {}
        if h.get("day") == day and not force:
            return {}
        h["day"] = day
        meta["health"] = h
        write_meta(self.host.st, meta)
        out = {}
        for kind in await asyncio.to_thread(health_targets):
            if self.flow and self.flow.get("kind") == kind and self.flow.get("stage") not in ("done", "failed"):
                continue
            revision = await asyncio.to_thread(self._credential_stamp)
            generation = self.health_generation
            res = await asyncio.to_thread(health_check, kind)
            if generation != self.health_generation or revision != await asyncio.to_thread(self._credential_stamp):
                continue  # a late daily call cannot overwrite a newer login/manual check
            out[kind] = res
            self._health_mark(kind, res)
            try:
                self.host.st.log("auth_health", agent=kind, status="ok" if res["ok"] else res["why"])
            except Exception:  # noqa: BLE001
                pass
            if res["ok"]:
                if self.state.get(kind, {}).get("cls") == "login":
                    self.clear(kind)
                    self.broadcast()
            elif res.get("cls"):
                self.signal(kind, res["cls"], "health_login")
        if out:
            self.broadcast()
        return out

    async def loop(self, every: float = 3600):
        """Hourly. Reminders and the health check wait for daytime (browser_login's quiet hours): a push at 00:05 helps no
        one, and a day's mark / check is still sent the same day once the morning comes."""
        from .browser_login import quiet
        started = time.monotonic()
        while True:
            wait = every
            if quiet():
                await asyncio.sleep(every)
                continue
            try:
                await asyncio.to_thread(self.daily)
            except Exception:  # noqa: BLE001 — a reminder must never take serve down
                pass
            left = HEALTH_DELAY - (time.monotonic() - started)
            if left <= 0:
                try:
                    await self.health_daily()
                except Exception:  # noqa: BLE001
                    pass
            else:
                wait = min(every, left + 1)
            await asyncio.sleep(wait)
