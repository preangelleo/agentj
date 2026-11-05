"""#462: a Claude Code sign-in reaches the main session itself and is proven in its context (`claude auth status`).
Env token > keychain / .credentials.json (2.1.316). Values: memory only, compared by hash. ADR-462 in ARCHITECTURE.md."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile

TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
OUTRANK = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
AUTH = (*OUTRANK, TOKEN)
CTX = ("HOME", "CLAUDE_CONFIG_DIR", "USER", "LOGNAME")
STATUS_WAIT = 20.0
TEXT = {"zh": {"ok": "已在主对话自己的环境里核对：Claude Code 用的就是新登录。",
               "owner": "新登录已存好，但主对话是你在终端里开的 Claude Code，它启动时没有这个登录，还在用旧的。点下面「重开 AI 会话」："
                        "Agent J 用新登录接着同一段对话重开（你终端里那个窗口会退出）。",
               "bad": "主对话没有用上新登录（{why}）：请在 设置 → 紧急修复 → AI 登录救援 里重新检查。",
               "stale": "主对话进程里的登录不是新的", "outranked": "主对话里有优先级更高的凭据", "not_logged_in": "主对话里未登录",
               "unreadable": "读不到主对话进程的环境", "status": "claude auth status 没有结果",
               "context": "主对话用的不是 Agent J 的 Claude 配置目录"},
        "en": {"ok": "Checked in the main conversation's own environment: Claude Code uses the new login.",
               "owner": "The new login is stored, but the main conversation is a Claude Code you started in a terminal and it still "
                        "runs on the old login. Tap Reopen AI session below: Agent J reopens the same conversation with the new login "
                        "(the window in your terminal exits).",
               "bad": "The main conversation is not on the new login ({why}): check again in Settings → Emergency repair → AI login rescue.",
               "stale": "its process holds another login", "outranked": "a higher-priority credential is set there",
               "not_logged_in": "it is not logged in", "unreadable": "its process environment is unreadable",
               "status": "claude auth status gave no answer", "context": "it uses another Claude config directory than Agent J"}}


def text(lang: str, key: str, why: str | None = None) -> str:
    t = TEXT["en" if lang == "en" else "zh"]
    return t[key].format(why=t.get(why or "", why or ""))


def _h(v) -> bytes:
    return hashlib.sha256(v.encode()).digest() if v else b""


def expected(environ=None) -> dict:
    e = os.environ if environ is None else environ
    return {"method": "token", "h": _h(e[TOKEN])} if e.get(TOKEN) else {"method": "login"}


def _pick(env) -> dict:
    return {k: env[k] for k in (*AUTH, *CTX) if env.get(k)}


def process_env(pid) -> dict | None:
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform == "darwin":
        from .term_mac import LIB
        got = LIB.args(pid)
        env = got[1] if got else None
    else:
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().decode(errors="replace").split("\0")
        except OSError:
            env = None
    if env is None:
        return None
    pairs = (kv.partition("=") for kv in env)
    return _pick({k: v for k, s, v in pairs if s})


def where(host):
    a = getattr(host, "agent", None)
    if a is None or getattr(a, "kind", None) != "claude":
        return "none", a
    with contextlib.suppress(Exception):
        if a.owns_harness():
            return "owned", a
    n = getattr(a, "native", None)
    if n is not None and getattr(a, "master", None) is not None and getattr(n, "returncode", None) is None:
        return "native", a
    return "owner", a


def session_env(host, kind: str, a) -> dict | None:
    if kind == "owned":                       # ClaudeAgent._spawn's steps (only auth names matter)
        from .proxy import environment
        env = environment(os.environ, getattr(host, "preferences", None))
        cfg = getattr(a, "cfg", None) or {}
        if cfg.get("fence", True):
            from . import fence
            env = fence.launch_environment(env, cfg.get("docker", False), "claude")
        return _pick(env)
    s = getattr(a, "session", None) or {}
    from .shared import _same_process
    env = process_env(s.get("pid"))
    return env if env is not None and _same_process(s) else None


def _under(p, d) -> bool:
    return p == d or p.startswith(d.rstrip("/") + "/")


def context_ok(env: dict, cwd) -> bool:
    """L-462a: the target's HOME / CLAUDE_CONFIG_DIR must be serve's own (by real path), never a config dir in the workspace."""
    home = os.path.realpath(os.environ.get("HOME") or os.path.expanduser("~"))
    cfg = os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"))
    th = os.path.realpath(env.get("HOME") or home)
    if (th, os.path.realpath(env.get("CLAUDE_CONFIG_DIR") or os.path.join(th, ".claude"))) != (home, cfg):
        return False
    ws = os.path.realpath(cwd) if cwd else None
    return not ws or _under(home, ws) or not _under(cfg, ws)     # a workspace holding the whole home holds ~/.claude too


def auth_status(env: dict) -> dict | None:
    """`claude auth status` with the target's credentials only, serve's own HOME / config, cwd a fresh empty dir."""
    from .auth_rescue import CLAUDE_STRIP, binary
    exe = binary("claude")
    if not exe:
        return None
    run = {k: v for k, v in os.environ.items() if k not in AUTH and k not in CLAUDE_STRIP}
    run.update({k: v for k, v in env.items() if k not in ("HOME", "CLAUDE_CONFIG_DIR")})
    try:
        with tempfile.TemporaryDirectory(prefix="agentj-auth-") as scratch:
            r = subprocess.run([exe, "auth", "status", "--json"], cwd=scratch, env=run, capture_output=True, text=True,
                               timeout=STATUS_WAIT, stdin=subprocess.DEVNULL)
        d = json.loads(r.stdout or "null")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return d if isinstance(d, dict) else None


def check(env: dict | None, exp: dict, cwd=None) -> str | None:
    if env is None:
        return "unreadable"
    if any(env.get(k) for k in OUTRANK):
        return "outranked"
    if exp["method"] == "token" and not hmac.compare_digest(_h(env.get(TOKEN)), exp["h"]):
        return "stale"
    if exp["method"] == "login" and env.get(TOKEN):
        return "stale"
    if not context_ok(env, cwd):
        return "context"
    st = auth_status(env)
    if st is None:
        return "status"
    if not st.get("loggedIn"):
        return "not_logged_in"
    want = "oauth_token" if exp["method"] == "token" else "claude.ai"
    return None if st.get("authMethod") == want and not st.get("apiKeySource") else "outranked"


async def verify(host, kind: str | None = None) -> str | None:
    k, a = where(host)
    kind = kind or k
    cwd = (getattr(a, "cfg", None) or {}).get("dir") or (getattr(host, "agent_cfg", None) or {}).get("dir")
    env = session_env(host, kind, a)
    why = await asyncio.to_thread(check, env, expected(), cwd)
    with contextlib.suppress(Exception):
        host.st.log("claude_live", where=kind, result="ok" if why is None else "failed", reason=why)
    return why


async def reopen(host) -> str | None:
    from . import phone_restart
    res = await phone_restart.run(host)
    if not res.get("ok"):
        return "unreadable"
    return await verify(host, "native")


async def deliver(host, why: str, wait: bool = False) -> dict:
    """The owner's own terminal process is never signalled here: only their signed P435 「重开 AI 会话」."""
    kind, a = where(host)
    if kind == "none":
        return {"restart": "none", "verified": None}
    if kind == "owned":
        restart = a.request_restart(why)
        bad = await verify(host, kind)
    elif kind == "native":
        if not wait:
            bg = getattr(host, "_bg", None)
            coro = _reopen_told(host)
            (bg(coro, "claude_live") if bg else asyncio.ensure_future(coro))
            return {"restart": "reopening", "verified": None}
        restart, bad = "reopened", await reopen(host)
    else:
        bad = await verify(host, kind)
        restart = "none" if bad is None else "owner_session"
        host.__dict__["_claude_live_pending"] = bad is not None
    return {"restart": restart, "verified": bad is None, **({"live": bad} if bad else {})}


def tell(host, res: dict) -> None:
    lang = getattr(host, "lang", "zh")
    if res.get("verified") is None:
        return
    if res["verified"]:
        host.agent_notice(text(lang, "ok"))
    elif res.get("restart") == "owner_session":
        getattr(host, "agent_recovery_notice", host.agent_notice)(text(lang, "owner"))
    else:
        host.agent_notice(text(lang, "bad", res.get("live")))


async def _reopen_told(host) -> None:
    bad = await reopen(host)
    tell(host, {"restart": "reopened", "verified": bad is None, "live": bad})


async def after_reopen(host) -> str | None:
    if not host.__dict__.pop("_claude_live_pending", False):
        return None
    bad = await verify(host)
    if bad is None:
        r = getattr(host, "auth_rescue", None)
        last = (getattr(r, "last", None) or {}).get("claude") or {}
        if last.get("why") == "main_session":
            with contextlib.suppress(Exception):
                r._end("claude", "recovered", None)
                r.clear("claude")
    return text(getattr(host, "lang", "zh"), "ok" if bad is None else "bad", bad)
