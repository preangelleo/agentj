"""Agent bridge (PROTOCOL §8): drives the customer's own, already-logged-in agent through its official headless
interfaces only — Claude Code: `claude -p` with stream-json in/out and `--permission-prompt-tool` (our MCP tool, see
permtool.py); Codex: `codex app-server` (agent_codex.py); OpenCode: `opencode serve` (agent_opencode.py). No private sockets,
no terminal injection.

Slash commands (slash.py, ADR-A70 – A72) ride the same queue as messages (`Cmd`): one at a time, after the turn that is
running; each adapter implements `cmd_<name>`. Claude Code's stream-json control requests are limited to
`CONTROL_SUBTYPES` (none of them changes a permission, a mode or a setting).

Bridge ≤ session: the agent runs as this OS user, in the directory the human chose, with the human's own login, settings
and permission rules. We never pass a bypass / skip-permissions / allowed-tools flag; the phone only answers the questions
the agent itself would have asked, and an approval returns the tool input unchanged.

Danger list (ADR-A47): Claude Code also gets one PreToolUse hook through `--settings` (`python -P -m agentj.danger hook`)
that answers "ask" for the five dangerous categories, so they reach the phone card by card even when the human's own rules
allow them; it never answers "allow" (only ever stricter). `disableAllHooks: false` in the same flag keeps a settings file the
Agent writes later from switching it off; a human who disabled hooks in their own settings gets a notice instead of a start.

L2 / L3: the agent runs inside the fence (fence.py: bubblewrap on Linux, sandbox-exec on macOS) unless the owner chose `--unfenced`; if the fence cannot start, the agent
refuses startup. The owner can approve one launch without isolation on the phone with a passkey; no grant is persisted.
Claude Code gets its first message only after our permission tool has claimed serve's socket.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import sys
import time
from dataclasses import dataclass

from . import bg, compactprep, danger, fence, slash, main_identity
from .slash import Result
from .envcompat import getenv
from .text import clean, clean_line, text_units, pick

MAX_TEXT = 4000               # = wire.MAX_TEXT (UTF-16 units per app message)
LINE_LIMIT = 64 * 1024 * 1024  # stream-json lines can carry whole tool results
STATUSES = ("none", "idle", "working", "compacting", "waiting", "down")
PERM_TOOL = "mcp__agentj__approve"
CLAIM_WAIT = 30               # s: the permission tool must claim perm.sock this soon after Claude Code starts
# the only stream-json control requests serve ever sends to Claude Code (test_slash checks): reading state, choosing the
# model, stopping a turn. Never set_permission_mode / apply_flag_settings / update_settings / mcp_* (bridge ≤ session).
CONTROL_SUBTYPES = ("interrupt", "get_context_usage", "get_status", "list_models", "set_model")
CONTROL_WAIT = 20
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")   # `claude --effort` (2.1.285), "for the current session"
# P219: never a silent wait. No progress from the harness this long after a message (no phone card open) → the host looks
# at why (a terminal prompt / text the harness printed / nothing) and tells the phone once per turn. STALL_QUIET_SECS: the
# "still nothing" card when no reason is visible, only on the first message of a freshly started process (where one-time
# setup screens and dead connections show up; a later long think is not news).
STALL_SECS = 45
STALL_QUIET_SECS = 90
STALL_TICK = 1.0
RETRY_NOTICE_AT = 5           # Claude Code's api_retry attempt (≈ 9 s in; a brief blip retries 2–3 times) that puts the "cannot reach the model" card up


@dataclass
class Cmd:
    """A slash command in the Agent's queue (slash.py): runs after the turn in front of it, never in parallel."""
    name: str
    arg: str = ""
    by: str | None = None
    turn: int | None = None          # its history page (PROTOCOL §10.5 `cmd` turn), filled in when it ran
    data: dict | None = None         # model_set (§10.11): {"r", "model", "effort", "default", "device"}
    cancelled: bool = False
    native_started: bool = False


UNCERTAIN = object()        # Agent.deliver: fn's answer "maybe delivered" (e.g. a 5xx after the request was sent, P33-X08)


class Withdrawn(Exception):
    """The phone took the message back (`say_cancel`) before it reached the harness: nothing was written."""


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Split a reply into chunks of ≤ limit UTF-16 units, preferring line breaks. Never drops text."""
    out, cur, n = [], [], 0
    for ch in text:
        u = 2 if ord(ch) > 0xFFFF else 1
        if n + u > limit:
            s = "".join(cur)
            cut = s.rfind("\n")
            if cut > len(s) // 2:
                out.append(s[:cut])
                cur, n = list(s[cut + 1:]), text_units(s[cut + 1:])
            else:
                out.append(s)
                cur, n = [], 0
        cur.append(ch)
        n += u
    if cur:
        out.append("".join(cur))
    return [c for c in out if c.strip()]


def summarize(tool: str, tool_input) -> str:
    """What the phone shows for a permission request (and what the device's signature covers, §8)."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    s = None
    if tool == "Bash" and isinstance(inp.get("command"), str):
        s = inp["command"]
        if isinstance(inp.get("description"), str) and inp["description"].strip():
            s = f"{s}\n— {inp['description'].strip()}"
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read") and isinstance(inp.get("file_path") or inp.get("notebook_path"), str):
        path = inp.get("file_path") or inp.get("notebook_path")
        if tool == "Write" and isinstance(inp.get("content"), str):
            s = f"{path}\n（写入 {len(inp['content'].encode())} 字节）\n{inp['content'][:600]}"
        elif tool == "Edit" and isinstance(inp.get("old_string"), str):
            s = f"{path}\n- {inp['old_string'][:400]}\n+ {str(inp.get('new_string', ''))[:400]}"
        else:
            s = path
    elif tool in ("WebFetch",) and isinstance(inp.get("url"), str):
        s = inp["url"]
    elif tool == "Delete" and isinstance(inp.get("file_path"), str):          # Codex: a patch that deletes a file
        s = "删除 " + inp["file_path"] + (("\n" + inp["detail"]) if isinstance(inp.get("detail"), str) else "")
    elif tool == "CodexPermissions" and isinstance(inp.get("permissions"), str):   # Codex asks for more than its sandbox
        s = "Codex 要求本轮放宽沙箱：" + inp["permissions"] + (f"\n— {inp['reason']}" if isinstance(inp.get("reason"), str) else "")
    elif tool == "agentj mail send" and isinstance(inp.get("summary"), str):   # P184: recipients, subject, files, body
        s = inp["summary"]
    if s is None:
        s = json.dumps(inp, ensure_ascii=False, separators=(", ", ": "))
    s = clean(s, 4000)
    return s if text_units(s) <= 2000 else s[:1990] + " …"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _unix(v) -> int | None:
    """A reset time in unix seconds (harnesses report seconds or milliseconds); None when it is not a number."""
    if not _num(v) or v <= 0:
        return None
    return int(v / 1000) if v > 10_000_000_000 else int(v)


def _signal_tree(p, sig: int) -> None:
    """The Agent runs in its own session (start_new_session): signal its whole process group, so what it started in the
    background goes with it. Linux's fence also ends the PID namespace; macOS has none (G-A53)."""
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            p.send_signal(sig)


# P175: Codex 0.162 failure wording (codex-rs error Display strings and app-server `codexErrorInfo`, read from the 0.162.1
# binary; samples in tests/test_p175_codex_errors.py). Order matters: the most specific class wins, `other` is the fallback.
_CODEX_REGION = ("unsupported_country", "country, region, or territory", "not available in your country",
                 "not available in your region", "region is not supported", "location is not supported")
_CODEX_USAGE = ("usage limit", "usage_limit_reached", "usagelimitexceeded", "rate limit", "rate_limit_exceeded",
                "too many requests", "workspace_owner_rate_limit")
_CODEX_QUOTA = ("quota exceeded", "insufficient_quota", "check your plan and billing", "credits_depleted", "quotaexceeded",
                "out of credits")
_CODEX_PLAN = ("usage not included", "usage_not_included", "usagenotincluded", "to use codex with your chatgpt plan",
               "plan does not include")
_CODEX_WORKSPACE = ("not enabled for your workspace", "login is restricted to workspace", "workspace administrator",
                    "must use one of workspace", "lack a workspace identifier", "account has been deactivated",
                    "account_deactivated")
_CODEX_CONTEXT = ("context window", "context_length_exceeded", "contextwindowexceeded", "ran out of room",
                  "maximum context length", "prompt is too long")
_CODEX_POLICY = ("cyberpolicy", "flagged for possible", "content_filter", "misalignment policy")
_CODEX_SERVER = ("at capacity", "server overloaded", "serveroverloaded", "server_overloaded", "internalservererror",
                 "internal server error", "bad gateway", "service unavailable", "gateway timeout")
_CODEX_SANDBOX = ("sandbox denied", "sandboxerror", "landlock", "seccomp", "bwrap", "bubblewrap",
                  "sandbox helper failed")
_CODEX_NETWORK = ("workspace routing discovery failed", "network", "connection", "timed out", "timeout", "proxy", "dns",
                  "failed to fetch", "stream disconnected", "httpconnectionfailed", "responsestreamconnectionfailed",
                  "responsestreamdisconnected", "certificate", "tls handshake", "error sending request")
CODEX_FEEDBACK = "me@agentj.app"   # the site's one feedback mailbox (site tests: founder@ / me@ / noreply@ only)


def _codex_retry_at(raw: str) -> str | None:
    """`… or try again at 3:05 PM.` / `try again in 2 days 3 hours` → the time, as Codex wrote it (never invented)."""
    import re
    m = re.search(r"try again (at|in) ([^.\n()]{1,60}?)\s*(?:[.\n(]|$)", raw, re.I)
    return clean_line(m.group(2), 60) if m else None


def _codex_exit(raw: str) -> str | None:
    """Our own wording for a Codex process that died ("Codex 退出了（137）…" / "没有启动") → the exit code, or "start"."""
    import re
    m = re.search(r"Codex 退出了（(-?\d+)）", raw)
    if m:
        return m.group(1)
    return "start" if "Codex（app-server）没有启动" in raw else None


def codex_failure(raw, model: str | None, lang: str = "zh") -> tuple[str, str, str | None]:
    """P78/P175: classify native failures into a line the owner can act on, retain bounded redacted details, never log
    the body. Kinds: model · region · usage · quota · plan · workspace · auth · context · policy · server · sandbox ·
    access · network · crash · missing · other."""
    import re
    from .privacy import redact
    if isinstance(raw, dict):
        raw = json.dumps(raw, ensure_ascii=False)
    raw = clean(str(raw or ""), 4000)
    low = raw.lower()
    en = lang == "en"
    blocked = None
    has = lambda words: any(w in low for w in words)  # noqa: E731
    status = re.search(r"\b(?:status|statusCode|http_status_code|httpStatusCode)\W{0,3}(\d{3})\b|\b(\d{3}) (?:Forbidden|Too Many|Unauthori|Internal|Bad Gateway|Service Unavailable)", raw, re.I)
    code = int(status.group(1) or status.group(2)) if status else None
    exit_code = _codex_exit(raw)
    if "model" in low and ("not supported" in low or "unsupported model" in low):
        match = re.search(r"['\"]([A-Za-z0-9][A-Za-z0-9._/-]{0,99})['\"] model", raw)
        blocked = match.group(1) if match else model
        name = clean_line(blocked or model or "?", 100)
        text = (f"This account cannot use {name}. Tap the model name at the top to choose another."
                if en else f"这个账号用不了 {name}，点顶部模型名换一个。")
        kind = "model"
    elif has(_CODEX_REGION) or code == 451:
        text = ("ChatGPT is not available from where you are (OpenAI does not serve this region). Codex needs a working "
                "proxy/VPN: on this computer run `agentj config set proxy.https http://127.0.0.1:7890` (your own proxy "
                "address), then resend. Or switch to Claude Code, or OpenCode with a model that works here: "
                "`agentj agent opencode --dir <folder> --model zhipuai/glm-5.3` (install guide Step 3a)."
                if en else
                "你所在地区连不上 ChatGPT（OpenAI 不支持这个地区）。Codex 需要可用的代理/VPN：在电脑上运行 "
                "`agentj config set proxy.https http://127.0.0.1:7890`（换成你自己的代理地址）后重发。"
                "或者改用 Claude Code，或 OpenCode + 国内模型：`agentj agent opencode --dir <文件夹> --model zhipuai/glm-5.3`"
                "（安装说明第 3a 步）。")
        kind = "region"
    elif has(_CODEX_QUOTA):
        text = ("The OpenAI account behind Codex is out of quota/credits. Top up or check billing at "
                "https://platform.openai.com/settings/organization/billing (API key) or ChatGPT → Settings → Usage, then resend."
                if en else
                "Codex 背后的 OpenAI 账户额度/余额用完了。到 https://platform.openai.com/settings/organization/billing（API key）"
                "或 ChatGPT 设置 → 用量 里充值或查看，再重发。")
        kind = "quota"
    elif has(_CODEX_USAGE) or code == 429:
        when = _codex_retry_at(raw)
        text = ((f"Codex usage limit reached — it resets {'at' if re.search(r'try again at', raw, re.I) else 'in'} {when}. "
                 if when else "Codex usage limit reached (Codex did not say when it resets; ChatGPT → Settings → Usage shows it). ")
                + "Until then: tap the model name at the top for a lighter model, or switch to Claude Code / OpenCode on this "
                  "computer (`agentj agent claude` / `agentj agent opencode`)."
                if en else
                (f"Codex 用量到上限了，恢复时间：{when}（Codex 给的时间）。" if when else
                 "Codex 用量到上限了（原文没给恢复时间，可在 ChatGPT 设置 → 用量 里看）。")
                + "等不及的话：点顶部模型名换个更省的模型，或在电脑上改用 Claude Code / OpenCode"
                  "（`agentj agent claude` / `agentj agent opencode`）。")
        kind = "usage"
    elif has(_CODEX_PLAN):
        text = ("This ChatGPT account's plan does not include Codex (free plan, or Plus has expired). Renew/upgrade at "
                "https://chatgpt.com/explore/plus, then on this computer run `codex login` again and resend."
                if en else
                "这个 ChatGPT 账号的套餐不含 Codex（免费版，或 Plus 已过期）。到 https://chatgpt.com/explore/plus 续费/升级，"
                "再在电脑上运行 `codex login` 重新登录后重发。")
        kind = "plan"
    elif has(_CODEX_WORKSPACE):
        text = ("Your ChatGPT workspace/organisation does not allow this login to use Codex. Ask your workspace admin to "
                "enable Codex, or run `codex login` on this computer with a personal account, then resend."
                if en else
                "你的 ChatGPT 工作区/组织不允许这个登录使用 Codex。请工作区管理员开通 Codex，"
                "或在电脑上运行 `codex login` 换成个人账号登录后重发。")
        kind = "workspace"
    elif re.search(r"\b401\b|unauthori[sz]ed|authentication.*(?:failed|required)|token.*(?:expired|rejected)|"
                   r"refresh token|sign in again|log ?in again", low):
        from . import harness
        logged_before = harness.codex_login()[0] != "warn"
        expired = logged_before and bool(re.search(r"\b401\b|refresh token|token.*(?:expired|rejected)", low))
        text = (("Codex login has expired. Run `codex login` on this computer, then resend."
                 if en else "Codex 登录已失效，在这台电脑上运行 `codex login` 重新登录后再发。") if expired else
                ("Codex is not logged in yet. Run `codex login` on this computer, then resend."
                 if en else "还没登录 Codex，在这台电脑上运行 `codex login` 后再发。"))
        kind = "auth"
    elif has(_CODEX_CONTEXT):
        text = ("This conversation is too long for the model. Send /compact to shrink it (or /clear to start fresh), then resend."
                if en else "这段对话太长，模型装不下了。发 /compact 压缩（或 /clear 重新开始）后再发。")
        kind = "context"
    elif has(_CODEX_POLICY):
        text = ("OpenAI's safety filter stopped this request. Rephrase it and resend, or switch to Claude Code for this task."
                if en else "OpenAI 的安全策略拦下了这条请求。换个说法重发，或这件事改用 Claude Code。")
        kind = "policy"
    elif has(_CODEX_SERVER) or (code is not None and code >= 500):
        text = ("OpenAI's servers are busy or failing right now (not your setup). Wait a few minutes and resend, or tap the "
                "model name at the top to pick another model."
                if en else "OpenAI 服务器这会儿繁忙或出错（不是你这边的问题）。等几分钟再发，或点顶部模型名换个模型。")
        kind = "server"
    elif has(_CODEX_SANDBOX):
        text = ("The sandbox blocked a step Codex tried (a safety fence, not a fault). Ask the Agent to do it another way, "
                "or approve the wider permission when the card asks; `agentj doctor` on this computer shows the fence state."
                if en else
                "沙箱拦下了 Codex 的某一步（这是安全隔离，不是故障）。让 Agent 换个做法，或在卡片弹出时批准放宽权限；"
                "电脑上运行 `agentj doctor` 可看隔离状态。")
        kind = "sandbox"
    elif code == 403 or re.search(r"\b403\b|forbidden", low):
        text = ("ChatGPT refused access (403). Most often this region is not served or the proxy node is blocked: switch "
                "proxy/VPN node and resend. If it persists, your account/workspace may not have Codex — run `codex login` again."
                if en else
                "ChatGPT 拒绝了访问（403）。最常见是所在地区不受支持或代理节点被封：换个代理/VPN 节点后重发；"
                "一直这样的话，可能是账号/工作区没开通 Codex，在电脑上重新运行 `codex login`。")
        kind = "access"
    elif any(w in re.sub(r"（[a-z]*error）", "", low) for w in _CODEX_NETWORK):   # not our own "（TimeoutError）" start wrapper
        text = ("Cannot connect to ChatGPT. Check your proxy/VPN, then resend (the proxy Agent J uses: "
                "`agentj config get proxy.https` on this computer)."
                if en else "连不上 ChatGPT，检查代理/VPN 后重发（Agent J 用的代理：电脑上运行 `agentj config get proxy.https`）。")
        kind = "network"
    elif exit_code is not None:
        sig = {"-9": "SIGKILL", "137": "SIGKILL", "-15": "SIGTERM", "143": "SIGTERM", "-11": "SIGSEGV", "139": "SIGSEGV"}.get(exit_code)
        if exit_code == "start":
            text = ("Codex did not start. On this computer run `codex --version` and `agentj doctor`, then `agentj agent restart`."
                    if en else "Codex 没能启动。在电脑上运行 `codex --version` 和 `agentj doctor`，再运行 `agentj agent restart`。")
        else:
            why = (" — killed by the system, often out of memory" if en else "，被系统强制结束，常见是内存不足") if sig == "SIGKILL" else ""
            text = (f"Codex stopped unexpectedly (exit {exit_code}{why}). Just resend once; if it happens again, run "
                    "`agentj doctor` then `agentj agent restart` on this computer."
                    if en else
                    f"Codex 进程意外退出（退出码 {exit_code}{why}）。先直接重发一次；再出现就在电脑上运行 `agentj doctor`，"
                    "再运行 `agentj agent restart`。")
        kind = "crash"
    elif "没找到 Codex" in raw or "codex: command not found" in low:
        text = ("Codex is not installed on this computer (or not on the service's PATH). Install it, run `codex login`, then "
                "`agentj service install`, and resend."
                if en else "这台电脑上找不到 Codex（或后台服务的 PATH 里没有）。装好并运行 `codex login`，再运行 "
                           "`agentj service install` 后重发。")
        kind = "missing"
    else:
        text = ("Codex could not finish this request and the reason was not recognised. Resend once; if it fails again, "
                f"screenshot the Details below and send it to this Agent J's owner or {CODEX_FEEDBACK}."
                if en else
                "Codex 没能完成这条请求，原因没认出来。先直接重发一次；还不行，把下面的「详情」截图，"
                f"发给这台 Agent J 的主人或客服 {CODEX_FEEDBACK}。")
        kind = "other"
    detail = redact(raw).replace("```", "` ` `")
    return kind, text + ("\n\n" + ("Details" if en else "详情") + ":\n```text\n" + detail + "\n```" if detail else ""), blocked


HARNESS_CMD = {"claude": "claude", "codex": "codex", "opencode": "opencode"}
_CLAUDE_ID = re.compile(r"claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(\[1m\])?", re.I)


def pretty_model(mid) -> str | None:
    """P219: a concrete Claude model id → the name with its version, as the phone shows it: claude-opus-5-5 → "Opus 5.5"
    (an alias such as "opus" alone never says which Opus). None for anything else."""
    m = _CLAUDE_ID.fullmatch(mid.strip()) if isinstance(mid, str) else None
    if not m:
        return None
    return f"{m.group(1).capitalize()} {m.group(2)}{'.' + m.group(3) if m.group(3) else ''}{' (1M)' if m.group(4) else ''}"


def tty_waiting(pid: int) -> bool:
    """Linux: some process of the Agent's session (it runs in its own, start_new_session) is blocked reading a terminal.
    Read-only /proc look (same user); False wherever that cannot be seen."""
    try:
        names = os.listdir("/proc")
    except OSError:
        return False
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open(f"/proc/{n}/stat", "rb") as f:
                rest = f.read().rsplit(b")", 1)[1].split()
            if int(rest[3]) != pid:          # field 6: session id
                continue
            with open(f"/proc/{n}/wchan") as f:
                wchan = f.read()
        except (OSError, IndexError, ValueError):
            continue
        if "tty" in wchan:                   # n_tty_read / tty_read: waiting for someone to type
            return True
    return False


def stall_text(kind: str, reason: str, excerpt: str, secs: int, lang: str = "zh") -> str:
    """The phone's card for a stalled turn (P219): where it is stuck, the harness's own words, what to do at the computer."""
    cmd = HARNESS_CMD.get(kind, kind)
    en = lang == "en"
    shown = ("\n```text\n" + excerpt.replace("```", "` ` `") + "\n```") if excerpt else ""
    if reason in ("tty", "prompt"):
        return (f"The Agent has been waiting {secs} s for an answer in a terminal on the computer — the phone cannot answer it."
                f"{' It printed:' if shown else ''}{shown}\nAt the computer: run `agentj agent {kind}` (answers the one-time "
                f"setup ahead), or run `{cmd}` once in the working folder and answer it there; then send the message again."
                if en else
                f"Agent 卡在电脑终端里的一个确认上（已等 {secs} 秒），手机上没法回答。{'它显示的是：' if shown else ''}{shown}\n"
                f"在电脑上处理：运行 `agentj agent {kind}`（预先完成首次设置），或在工作目录里运行一次 `{cmd}` 把它答掉；然后在手机上重发这条消息。")
    return (f"No answer from the Agent for {secs} s; its process is still running. It may be thinking for a long time, or the "
            f"connection to the model service is very slow. If nothing changes: at the computer run `agentj doctor`, or tap Stop "
            f"and send again." if en else
            f"Agent 已经 {secs} 秒没有任何回应，进程还在运行：可能在长时间思考，也可能连模型服务很慢。"
            f"一直没有动静的话：在电脑上运行 `agentj doctor` 查看，或在手机上按「停止」后重发。")


class Agent:
    """Base: a queue of user messages, one turn at a time, status reported to the host."""
    kind = "?"

    def __init__(self, host, cfg: dict):
        # Main adapters share the host model/effort configuration; workflow CEOs
        # receive their own configuration and must not mutate the main session.
        self.host, self.cfg = host, dict(cfg) if cfg.get("_workflow_ceo") else cfg
        if not cfg.get("_workflow_ceo"):
            self.cfg["dir"] = str(main_identity.working_root(cfg))
        self.q: asyncio.Queue[str] = asyncio.Queue()
        self.status = "idle"
        self.task: asyncio.Task | None = None
        self.halting = False
        self.ended: set = set()       # processes serve ended on purpose: their exit is not news (no notice)
        self.turn_proc = None         # the process the running turn talks to (an older one's exit cannot end it)
        self.cur_send = None          # the phone's `say` this turn delivers (compose.Send), None for anything else
        self.cur_cmd = None           # the command (Cmd) running now — F24 shows its progress on that page
        self.models_cache: list = []  # §10.11: [{"id", "name", "efforts"}] as the harness listed them
        self.touched = False          # P219: the running turn has seen progress from the harness (stall watch)
        self.fresh = False            # P219: the next turn is the first of a freshly started process

    def set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            self.host.agent_status(s)

    def submit(self, text) -> None:
        self.q.put_nowait(text)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    def meter(self, **kw) -> None:
        """§10.10: a number the harness reported exactly (never an estimate) → the phones' meters."""
        fn = getattr(self.host, "meter_update", None)
        if fn:
            fn(**kw)

    def fail_notice(self, text: str) -> None:
        """A notice that means this turn did not finish normally (its page ends `failed`)."""
        fn = getattr(self.host, "turn_failed", None)
        if fn:
            fn()
        self.host.agent_notice(text)

    def local_fail(self, text: str) -> None:
        """A turn that cannot run until the human changes something on the computer (fence / hooks): failed + 「在电脑上处理」."""
        fn = getattr(self.host, "turn_failed", None)
        if fn:
            fn()
        self.local_only(text)

    def local_only(self, text: str) -> None:
        """§10.8: something only the human at the computer can answer — a page with relay's 「在电脑上处理」 card."""
        fn = getattr(self.host, "local_notice", None)
        (fn or self.host.agent_notice)(text)

    async def deliver(self, fn):
        """The "delivered" moment (§10.2): fn = the write to the harness (Claude Code's stdin line, Codex `turn/start`, OpenCode
        `prompt_async`). Run under the say's lock, so a `say_cancel` either wins before it (Withdrawn, nothing written) or
        waits and reads already_delivered. fn returning False = refused by the harness (the say is `failed`)."""
        s = self.cur_send
        if s is None:
            return await fn()
        async with s.lock:
            if getattr(s, 'official_notice_id', None):
                from . import notices
                if not notices.active(self.host.st, s.official_notice_id):
                    self.host.official_pending.pop(s.official_notice_id, None)
                    raise Withdrawn()
            if s.state == "cancelled":
                raise Withdrawn()
            if s.state == "delivered":          # a retry of the same message after the process died
                return await fn()
            s.state = "delivering"
            try:
                r = await fn()
            except BaseException:
                # P33-X08: the write raised part-way — the harness may already have the message. Never `failed` (that
                # would let a withdraw answer `cancelled` and release attachments the Agent may be reading).
                s.state = "uncertain"
                fn3 = getattr(self.host, "say_uncertain", None)
                if fn3:
                    fn3(s)
                raise
            s.state = "uncertain" if r is UNCERTAIN else "failed" if r is False else "delivered"
        if s.state == "uncertain":
            fn3 = getattr(self.host, "say_uncertain", None)
            if fn3:
                fn3(s)
            return r
        fn2 = getattr(self.host, "say_delivered", None)
        if fn2 and s.state == "delivered":
            fn2(s)
        return r

    async def run(self) -> None:
        while True:
            text = await self.q.get()
            if getattr(text, "sid", None) is not None:      # a phone's say (compose.Send): wait for its transcripts
                send = text
                if not await send.wait_ready() or send.state == "cancelled":     # withdrawn while it waited
                    if self.q.empty() and self.status == "working":
                        self.set_status("down" if self.is_down() else "idle")
                    continue
                async with getattr(self.host, "turn_lock", None) or contextlib.nullcontext():
                    if send.state == "cancelled":
                        if self.q.empty() and self.status == "working":
                            self.set_status("down" if self.is_down() else "idle")
                        continue
                    self.halting = False
                    self.cur_send = send
                    self.set_status("working")
                    if hasattr(self.host, "agent_turn_start"):
                        self.host.agent_turn_start(send.text, send)
                    try:
                        await self._identity_refresh()
                        if not (send.text or "").strip():   # F27: never an empty prompt (compose.render always names the files)
                            send.state = "failed"
                            self.fail_notice("这条消息是空的（没有文字，附件也没整理出来），没有发给 Agent。")
                        else:
                            await self.watched(self.turn(compactprep.decorate(self, send)))   # F24 handover; P219 stall watch
                    except asyncio.CancelledError:
                        raise
                    except Withdrawn:
                        pass
                    except Exception as e:  # noqa: BLE001 — an adapter bug costs one turn, never serve
                        self.fail_notice(f"Agent 出错（{type(e).__name__}），这条没有完成。")
                    finally:
                        self.cur_send = None
                    self.host.agent_turn_end()
                    try:                                    # P59: context nearly full → the handover before the harness
                        await compactprep.proactive(self)   # compacts by itself (Codex; ADR-A165)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — never costs the queue
                        self.host.st.log("compact_auto_prep", agent=self.kind, result="error")
                    if self.q.empty():
                        self.set_status("down" if self.is_down() else "idle")
                continue
            if isinstance(text, Cmd):
                if text.cancelled:
                    if self.q.empty() and self.status == "working":
                        self.set_status("down" if self.is_down() else "idle")
                    continue
                async with getattr(self.host, "turn_lock", None) or contextlib.nullcontext():
                    if text.cancelled:
                        if self.q.empty() and self.status == "working":
                            self.set_status("down" if self.is_down() else "idle")
                        continue
                    self.halting = False
                    if text.name == "model_set":            # the phone's pill (§10.11): no card, a model_res + meter
                        try:
                            why = await self.model_set(text.data or {})
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001
                            why = "unsupported"
                        self.host.model_set_done(text, why)
                        if self.q.empty():
                            self.set_status("down" if self.is_down() else "idle")
                        continue
                    self.cur_cmd = text              # F24: compactprep shows its progress on this command's page
                    try:
                        res = await self.command(text.name, text.arg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 — a command bug costs one card, never serve
                        res = Result(f"命令出错（{type(e).__name__}）。", "error")
                    finally:
                        self.cur_cmd = None
                    self.host.cmd_done(text, res)
                    if self.q.empty():
                        self.set_status("down" if self.is_down() else "idle")
                continue
            # one user of the Agent at a time: a scheduled task run holds the same lock (tasks.py, ADR-A53)
            async with getattr(self.host, "turn_lock", None) or contextlib.nullcontext():
                self.halting = False
                self.set_status("working")
                if hasattr(self.host, "agent_turn_start"):
                    self.host.agent_turn_start(text)
                try:
                    await self._identity_refresh()
                    await self.watched(self.turn(text))
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — an adapter bug costs one turn, never serve
                    self.fail_notice(f"Agent 出错（{type(e).__name__}），这条没有完成。")
                self.host.agent_turn_end()
                if self.q.empty():
                    self.set_status("down" if self.is_down() else "idle")

    # ------------------------------------------------ P219: never a silent wait
    def touch(self) -> None:
        """The harness made progress on this turn (a reply, a tool step, the turn's end)."""
        self.touched = True

    async def watched(self, coro):
        """Run one turn with the stall watch beside it."""
        self.touched = False
        w = asyncio.create_task(self._stall_watch())
        try:
            return await coro
        finally:
            w.cancel()

    def _asks_open(self) -> bool:
        asks = getattr(self.host, "asks", None)
        return isinstance(asks, dict) and any(not a.fut.done() for a in asks.values() if hasattr(a, "fut"))

    async def _stall_watch(self) -> None:
        start = time.monotonic()
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(STALL_TICK)
                if self.touched or self.halting:
                    return
                if self._asks_open():            # a card is on the phone: that card is what everyone waits for
                    start = time.monotonic()
                    continue
                waited = time.monotonic() - start
                if waited < STALL_SECS:
                    continue
                found = self.stall_probe()
                if found == "skip":              # already told (e.g. the model-service retry card)
                    return
                if found is None and not (self.fresh and waited >= STALL_QUIET_SECS):   # fresh: set by the spawn
                    continue
                reason, excerpt = found or ("quiet", "")
                self.host.st.log("agent_stall", agent=self.kind, reason=reason, ms=int(waited * 1000))
                self.local_only(stall_text(self.kind, reason, excerpt, int(waited), getattr(self.host, "lang", "zh")))
                return

    def stall_probe(self):
        """Why the harness has said nothing: ("tty" | "prompt", excerpt), "skip" (already on the phone) or None (no sign)."""
        p = getattr(self, "proc", None)
        if p is None or p.returncode is not None:
            return None
        return ("tty", self.printed_tail()) if tty_waiting(p.pid) else None

    def printed_tail(self) -> str:
        """What the harness printed outside its protocol (stdout that is not protocol, stderr): the last lines, redacted."""
        raw = (getattr(self, "raw_tail", "") or "") + "\n" + (getattr(self, "err_tail", "") or "")
        lines = [ln for ln in raw.splitlines() if ln.strip()][-6:]
        if not lines:
            return ""
        from .privacy import redact
        return clean(redact("\n".join(lines)), 600)

    async def halt(self, clear_queue: bool = True) -> bool:
        """Stop everything (ADR-A51) — or make room for a scheduled task run (clear_queue=False, called while holding
        turn_lock, so no turn is running): drop the messages still queued (they never ran and never will), ask the harness
        to interrupt the turn (`interrupt_request`), then end the Agent's whole process tree. The conversation itself is
        kept (Claude Code --resume / Codex thread id). Returns True when a turn was running."""
        if clear_queue:
            while not self.q.empty():
                item = self.q.get_nowait()
                fn = getattr(self.host, "queue_dropped", None)
                if fn:
                    fn(item)
        busy = self.status == "working"
        p = getattr(self, "proc", None)
        if p is None or p.returncode is not None:
            return busy
        self.halting = True                  # the adapter says nothing about the process it was told to end
        p._agentj_requested_stop = True      # survives watcher cleanup while startup HTTP calls unwind
        self.ended.add(p)
        with contextlib.suppress(Exception):
            await self.interrupt_request(p)
        td = getattr(self, "turn_done", None)
        if td is not None:
            td.set()
        if getattr(self, "proc", None) is p:
            self.proc = None
        _signal_tree(p, signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(p.wait(), 5)
        _signal_tree(p, signal.SIGKILL)
        return busy

    async def interrupt_request(self, p) -> None:
        """The harness's own "stop this turn" (none by default; the process tree is ended right after anyway)."""

    def is_down(self) -> bool:
        return False

    def perm_lost(self) -> None:
        """The permission tool's connection to serve dropped (ClaudeAgent restarts; others have none)."""

    def launch_argv(self, argv: list[str]) -> list[str] | None:
        """Fail closed. Only an explicit owner --unfenced choice skips isolation."""
        self._fence_failure = None
        if not self.cfg.get("_workflow_ceo"):
            try:
                main_identity.verify_core()
                main_identity.validate_working_root(self.cfg, self.host.st)
            except (main_identity.IdentityError, OSError):
                self.host.st.log("agent_identity_fail", agent=self.kind, reason="core_or_root_invalid")
                self.local_fail("主 Agent 核心或工作根目录校验失败，未启动。Core or working root validation failed; Agent was not started. Run agentj doctor.")
                return None
        self.isolation_effective = False
        if not self.cfg.get("fence", True):
            return argv
        why = fence.problem(self.host.st, self.cfg["dir"])
        if why:
            self._fence_failure = why
            self.host.st.log("agent_fence_fail", agent=self.kind, reason=why, status="refused")
            return None
        wrapped = fence.wrap(self.host.st, argv, self.cfg["dir"], allow_docker=self.cfg.get("docker", False))
        self.isolation_effective = True
        return wrapped

    async def authorized_launch_argv(self, argv: list[str]) -> list[str] | None:
        wrapped = self.launch_argv(argv)
        why = getattr(self, "_fence_failure", None)
        if wrapped is not None or not why or self.cfg.get("_workflow_ceo"):
            return wrapped
        # Keep the phone available when the harness is down. This grant is consumed
        # by THIS spawn only: no configuration write and no remembered permission.
        answer = await self.host.ask("SandboxOnce", {"reason": why}, batch=False, require_passkey=True)
        if answer.get("behavior") == "allow":
            self.host.st.log("agent_unfenced_once", agent=self.kind, reason=why)
            return argv
        self.fail_notice(pick(getattr(self.host, "lang", "zh"),
                              "沙箱不可用，未启动。修复隔离后再试，或在手机上用通行密钥批准本次不隔离运行。",
                              "Sandbox unavailable; not started. Repair isolation or approve one launch on the phone with a passkey."))
        return None

    async def turn(self, text: str) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task

    # ------------------------------------------------ slash commands (slash.py)
    def passthrough(self, name: str) -> bool:
        """May `/name` go to the harness as an ordinary message? (Claude Code: one of its skills.)"""
        return False

    async def command(self, name: str, arg: str) -> Result:
        fn = getattr(self, "cmd_" + name, None) if name in slash.WHITELIST + slash.INTERNAL else None
        if fn is None:
            return Result(slash.REFUSE, "refused")
        if name == "compact" and compactprep.applies(self):    # F24: the handover first, then the compaction as before
            return await compactprep.compact(self, fn, arg)
        if name == "clear" and compactprep.applies(self):
            key = compactprep.session_key(self)
            res = await fn(arg)
            if res.undo:
                compactprep.cleared(self, key)
            return res
        return await fn(arg)

    async def drop_conversation(self) -> None:
        """Forget the loaded conversation (the next turn opens the one agent.json names)."""

    # ------------------------------------------------ A1 (P44): the injected main identity changed (the owner's language)
    identity_stale = False
    _private_key = None

    def identity_changed(self) -> None:
        """serve: the text main_identity.prompt(self.cfg) yields changed. Applied before the next turn (hot, never mid-turn)."""
        if not self.cfg.get("_workflow_ceo"):
            self.identity_stale = True

    async def _identity_refresh(self) -> None:
        if self.restart_pending:              # P59 (A167): an owned-harness restart asked for while a turn ran (or idle)
            self.restart_pending = False
            p = getattr(self, "proc", None)
            if p is not None and p.returncode is None and self.owns_harness():
                self.host.st.log("agent_restart", agent=self.kind, reason=self.restart_why)
                await self._end_proc()        # the next spawn resumes the same conversation with the new environment
                self.halting = False
        # P116 (B11): the owner edited the private instructions file → reload like a language change (between turns only)
        if not self.cfg.get("_workflow_ceo") and (self.cfg.get("private_instructions_file") or self._private_key is not None):
            from .private_instructions import load, generation
            got = load(self.cfg.get("private_instructions_file") or "")
            key = got["digest"] if got["ok"] else got["reason"]
            if self._private_key is not None and key != self._private_key:
                self.identity_stale = True
            if self._private_key != key:
                generation(self.host.st, got)     # metadata-only log; never fails the turn
            self._private_key = key
        if self.identity_stale:
            self.identity_stale = False
            await self.reload_identity()
            self.halting = False

    # ------------------------------------------------ P59 (A167): restart the harness Agent J owns, conversation kept
    restart_pending = False
    restart_why = ""

    def owns_harness(self) -> bool:
        """Is the harness process Agent J's own child (independent mode)? A shared session is the owner's process."""
        return self.cfg.get("session_mode") != "shared" and hasattr(self, "proc")

    def request_restart(self, why: str) -> str:
        """Before the next turn (never mid-turn: the main Agent may be the one asking). → "next_turn" | "next_start"."""
        p = getattr(self, "proc", None)
        if p is None or p.returncode is not None:
            return "next_start"
        self.restart_pending, self.restart_why = True, why[:32]
        return "next_turn"

    async def reload_identity(self) -> None:
        """Default: nothing (OpenCode sends `system` with every prompt; a shared session is the owner's own harness — we
        inject nothing there and never restart it). ClaudeAgent / CodexAgent override this."""

    async def _end_proc(self) -> None:
        p = getattr(self, "proc", None)
        if p is not None and p.returncode is None:
            self.halting = True
            self.ended.add(p)
            if getattr(self, "proc", None) is p:
                self.proc = None
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    async def cmd_clear(self, arg: str) -> Result:
        st = self.host.st
        old = st.agent_session(self.kind)
        if not old:
            return Result("现在就是一段新对话，不用清空。", "info", sep=True)
        await self.drop_conversation()
        st.set_agent_session(self.kind + ".prev", old)
        st.set_agent_session(self.kind, None)
        return Result("已清空：Agent 不再记得上面的对话。旧对话仍保存在这台电脑上，可以撤销。", sep=True, undo=True)

    async def cmd_undo_clear(self, arg: str) -> Result:
        st = self.host.st
        prev = st.agent_session(self.kind + ".prev")
        if not prev:
            return Result("没有可以撤销的清空。", "error")
        await self.drop_conversation()
        st.set_agent_session(self.kind, prev)
        st.set_agent_session(self.kind + ".prev", None)
        return Result("已撤销清空：接着之前的对话（清空之后说的话不在这段对话里）。", sep=True)

    async def cmd_help(self, arg: str) -> Result:
        return Result(slash.HELP)

    # ------------------------------------------------ model and effort pill (PROTOCOL §10.11)
    EFFORTS: tuple | None = None      # the harness's effort levels when its model list does not say (None = no effort)

    def cur_model(self) -> str | None:
        return self.cfg.get("model")

    def cur_effort(self) -> str | None:
        return self.cfg.get("effort")

    async def list_models(self) -> list[dict]:
        """[{"id", "name", "efforts": [...] | None}] — what the harness offers (cached; [] when it gave no list)."""
        if not self.models_cache:
            with contextlib.suppress(Exception):
                await self.refresh_models()
        return self.models_cache

    def efforts_for(self, model: str | None, models: list[dict]):
        for m in models:
            if m["id"] == model:
                return m.get("efforts")
        return list(self.EFFORTS) if self.EFFORTS else None

    async def model_set(self, d: dict) -> str | None:
        """None = applied; else why not: unknown_model | unknown_effort | unsupported."""
        if d.get("default") is True:
            await self.apply_model(None, None, default=True)
            return None
        model, effort = d.get("model"), d.get("effort")
        models = await self.list_models()
        if model is not None and (not isinstance(model, str) or not slash.MODEL_RE.match(model)
                                  or (models and model not in {m["id"] for m in models})):
            return "unknown_model"
        if effort is not None:
            eff = self.efforts_for(model or self.cur_model(), models)
            if not eff:
                return "unsupported"
            if effort not in eff:
                return "unknown_effort"
        if model is None and effort is None:
            return None
        return await self.apply_model(model, effort)

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        """Store the choice (config.json agent.model / agent.effort) so the next turn uses it; adapters add what the harness
        needs on top."""
        if default:
            self.host.set_model(None)
            self.host.set_effort(None)
        if model is not None:
            self.host.set_model(model)
        if effort is not None:
            self.host.set_effort(effort)
        self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()), effort=self.cur_effort())
        return None

    def model_name(self, model: str | None) -> str | None:
        for m in self.models_cache:
            if m["id"] == model:
                return m["name"]
        return None

    async def refresh_models(self) -> None:
        """Ask the harness for its list once it runs (adapters); the host sends `models` to the phones."""


def source_root() -> str | None:
    """The directory holding `agentj/` when it is a source checkout; None when the package is installed in a venv's
    site-packages (`uv tool` / pipx / pip): then the venv's interpreter finds it alone and nothing extra goes into the
    agent's environment (L3)."""
    import site
    root = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    installed = {os.path.realpath(p) for p in site.getsitepackages() + [site.getusersitepackages()]}
    return None if root in installed else root


def hook_settings(extra: list | None = None, research: bool = False) -> dict:
    """The only settings serve adds to Claude Code (`--settings`, flag level — above user / project / local, below managed
    policy): one PreToolUse hook for every tool, and hooks kept on. No permission rule, no mode (Invariant 11).
    research (a scheduled `mode: research` task, ADR-A53): the same hook also denies every call that is not read-only."""
    cmd = (f"{shlex.quote(sys.executable)} -P -m agentj.danger hook {danger.encode_extra(extra or [])}"
           + (" research" if research else "")
           + " || exit 2")          # the hook cannot start → a blocking error: the call does not run (fail closed)
    return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]},
            "disableAllHooks": False}


def with_reminder(settings: dict, cfg: dict) -> dict:
    """P159: the main Agent's hidden per-turn line (main_identity.turn_reminder) as a UserPromptSubmit hook in the same
    `--settings`: a fixed `printf` of the hook answer (ASCII-escaped JSON, no interpreter start per turn). Claude Code
    keeps it as a hook_additional_context attachment — not a user message, not shown in its view, not in phone history.
    Adds no permission and never re-enables hooks the human switched off (no disableAllHooks key here)."""
    if cfg.get("_workflow_ceo"):
        return settings
    from . import main_identity
    answer = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                "additionalContext": main_identity.turn_reminder(cfg)}})
    hooks = dict(settings.get("hooks") or {})
    hooks["UserPromptSubmit"] = [{"hooks": [{"type": "command", "command": "printf '%s\\n' " + shlex.quote(answer), "timeout": 10}]}]
    return {**settings, "hooks": hooks}


_MANAGED = ("/etc/claude-code/managed-settings.json", "/Library/Application Support/ClaudeCode/managed-settings.json")


def hooks_blocked(workdir: str) -> str | None:
    """Why the danger hook would not run (or would re-enable hooks the human switched off), else None. Read once per start:
    managed policy `allowManagedHooksOnly` / `disableAllHooks`; the human's or the folder's `disableAllHooks: true`."""
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    files = [(p, "managed") for p in _MANAGED] + [(os.path.join(cfg_dir, "settings.json"), "user")] + [
        (os.path.join(workdir, ".claude", n), "project") for n in ("settings.json", "settings.local.json")]
    for path, level in files:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if level == "managed" and d.get("allowManagedHooksOnly") is True:
            return f"管理员策略（{path}）只允许受管 hooks"
        if d.get("disableAllHooks") is True:
            return f"{'管理员策略' if level == 'managed' else '你的 Claude Code 设置'}（{path.replace(os.path.expanduser('~'), '~')}）关掉了所有 hooks（disableAllHooks）"
    return None


def _bin(env: str, name: str) -> str | None:
    from .binaries import resolve
    return resolve(name)["path"]


class ClaudeAgent(Agent):
    """One long-lived `claude -p --input-format stream-json` process; restarted with --resume <session id> if it exits."""
    kind = "claude"

    async def reload_identity(self) -> None:
        """A1: --append-system-prompt is read at start: end the idle process; the next turn starts Claude Code again with
        --resume (same conversation) and the new identity text."""
        await self._end_proc()

    def __init__(self, host, cfg: dict):
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.turn_done: asyncio.Event | None = None
        self.got_init = False
        self.err_tail = ""
        self.failed_start = False
        self.init: dict = {}                       # the last system/init (skills, model, version)
        self.rate: dict | None = None              # the last rate_limit_event.rate_limit_info (/usage)
        self.ctl: dict[str, asyncio.Future] = {}   # control request id → its control_response
        self.capture: list | None = None           # a local command (/compact, /cost): its text is the card, not a reply
        self.last_result: dict = {}
        self.boundary: dict | None = None          # compact_metadata of the last compact_boundary
        self.replay = False                        # after a compact_boundary: Claude Code re-sends a kept synthetic message
        self.native_goal_checked = False           # P198: this process's session checked once for Claude Code's own /goal
        self.raw_tail = ""                         # P219: what Claude Code printed on stdout that is not stream-json
        self.retry_told = False                    # P219: this turn's "cannot reach the model service" card is up
        self.fallback = None                       # P219: (unsupported model, its error) → retry once on the default model
        self.fell_back = False

    def is_down(self) -> bool:
        return self.failed_start

    def argv(self, resume: str | None, research: bool = False) -> list[str]:
        # the running venv's own interpreter + the installed module (L3); -P: the agent's folder (Claude Code's cwd) is
        # never put on sys.path, so a `agentj/` or `json.py` planted there cannot stand in for the permission tool
        mcp = {"mcpServers": {"agentj": {"type": "stdio", "command": sys.executable,
                                              "args": ["-P", "-m", "agentj.permtool"]}}}
        a = [_bin("AGENTJ_CLAUDE_BIN", "claude") or "claude", "-p", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose", "--permission-prompt-tool", PERM_TOOL,
             "--disallowedTools", PERM_TOOL, "--mcp-config", json.dumps(mcp),
             "--settings", json.dumps(with_reminder(hook_settings(self.cfg.get("danger_extra"), research) if research or self.cfg.get("high_risk_warnings", False) else {}, self.cfg), separators=(",", ":"))]
        if not self.cfg.get("_workflow_ceo"):
            a += ["--append-system-prompt", main_identity.prompt(self.cfg)]
        if self.cfg.get("model"):
            a += ["--model", self.cfg["model"]]
        if self.cfg.get("effort") in CLAUDE_EFFORTS:      # §10.11: "for the current session" — no settings write
            a += ["--effort", self.cfg["effort"]]
        from . import harness_setup
        if harness_setup.root_bypass(self.cfg.get("dir")):
            # P219: as root Claude Code refuses bypassPermissions and exits before its first word; the stricter default
            # mode (every tool through the phone) is the only start left — narrower than the owner's setting, never wider
            a += ["--permission-mode", "default"]
        if resume:
            a += ["--resume", resume]
        return a

    async def _spawn(self) -> bool:
        exe = _bin("AGENTJ_CLAUDE_BIN", "claude")
        if not exe:
            self.failed_start = True
            self.fail_notice("这台电脑上没找到 Claude Code（claude 命令）。装好并登录后再试。")
            return False
        why = hooks_blocked(self.cfg["dir"]) if self.cfg.get("high_risk_warnings", False) or self.cfg.get("_research") else None
        if why:
            self.failed_start = True
            self.host.st.log("agent_hooks_off", agent=self.kind)
            self.local_fail(f"{why}。危险动作（花钱、删除、对外发送、改凭据、改价）必须经手机逐条批准，这要靠一个 hook，"
                                   "所以 Agent 没有启动。去掉 disableAllHooks 这一项后再发一条消息。")
            return False
        argv = await self.authorized_launch_argv(self.argv(self.host.st.agent_session(self.kind)))
        if argv is None:
            self.failed_start = True
            return False
        from .proxy import environment
        env = environment(os.environ, self.host.preferences)
        env.update(self.host.new_perm_env())
        src = source_root()
        if src:   # a source checkout (launcher + PYTHONPATH): the tool must import this same package
            env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["MCP_TOOL_TIMEOUT"] = str(int((self.host.ask_ttl + 60) * 1000))   # never cut a pending approval short
        from . import fence
        if self.cfg.get("_workflow_ceo"):
            env = fence.auxiliary_environment(env, self.kind, extra_keep=("AGENTJ_PERM_SOCK", "AGENTJ_PERM_CLAIM", "AGENTJ_PERM_WAIT", "OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME", "OPENCODE_CONFIG_CONTENT"))
        if self.cfg.get("fence", True):
            env = fence.launch_environment(env, self.cfg.get("docker", False), self.kind)
        sid = self.host.st.agent_session(self.kind)
        self.proc = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=env,
                                                         stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
                                                         start_new_session=True)
        self.got_init, self.failed_start, self.err_tail, self.raw_tail = False, False, "", ""
        self.native_goal_checked = False
        self.fresh = True                          # P219: the stall watch may say "still nothing" on this first message
        if "--permission-mode" in argv and not getattr(self, "root_told", False):
            self.root_told = True
            self.host.st.log("agent_root_bypass", agent=self.kind)
            self.host.agent_notice(pick(getattr(self.host, "lang", "zh"),
                                        "Agent J 以 root 运行，Claude Code 不允许 root 跳过权限（bypassPermissions）。这次按普通权限模式启动："
                                        "需要批准的动作会到手机上。想恢复原样，请用普通用户运行 Agent J。",
                                        "Agent J runs as root and Claude Code refuses bypassPermissions for root, so it starts in the "
                                        "default permission mode: actions that need approval come to the phone. Run Agent J as a normal "
                                        "user to keep your setting."))
        self.reader = asyncio.create_task(self._read(self.proc, resumed=bool(sid)))
        bg.spawn(self._drain_err(self.proc), "agent_drain_err", self.host.st.log)   # P192 (B13): held, failure logged
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        # no message before the permission tool holds serve's socket: by then the one-time token is spent (L2)
        proc = self.proc
        try:
            await asyncio.wait_for(self.host.perm_claimed.wait(), CLAIM_WAIT)
        except asyncio.TimeoutError:
            self.host.st.log("agent_no_claim", agent=self.kind)
            self.fail_notice("Claude Code 的批准通道没有接上：为了安全，这条消息没有交给它。")
            await self._kill(proc)
            return False
        if proc.returncode is not None or self.proc is not proc:
            return False
        if not self.cfg.get("_workflow_ceo"):
            main_identity.audit(self.cfg, self.kind, self.host.st, self.host.st.agent_session(self.kind))
        return True

    async def control(self, subtype: str, **kw) -> dict:
        """One stream-json control request (what the Agent SDK sends) → its response. Only CONTROL_SUBTYPES."""
        if subtype not in CONTROL_SUBTYPES:
            raise ValueError("control subtype not allowed: " + subtype)
        p = self.proc
        if p is None or p.stdin is None or p.returncode is not None:
            raise ConnectionError("Claude Code is not running")
        rid = "aj-" + os.urandom(6).hex()
        f = asyncio.get_running_loop().create_future()
        self.ctl[rid] = f
        try:
            p.stdin.write((json.dumps({"type": "control_request", "request_id": rid,
                                       "request": {"subtype": subtype, **kw}}) + "\n").encode())
            await p.stdin.drain()
            r = await asyncio.wait_for(f, CONTROL_WAIT)
        finally:
            self.ctl.pop(rid, None)
        if r.get("subtype") != "success":
            raise RuntimeError(clean(str(r.get("error") or r.get("subtype") or "error"), 200))
        return r.get("response") if isinstance(r.get("response"), dict) else {}

    async def interrupt_request(self, p) -> None:
        """Claude Code's stream-json control request `interrupt` (what the Agent SDK sends), then ≤ 2 s for the turn's
        result. Measured (2.1.285): the turn ends at once but a backgrounded Bash keeps running — so halt() always ends the
        process tree after this."""
        td = self.turn_done
        if td is None or td.is_set() or p.stdin is None:
            return
        req = {"type": "control_request", "request_id": "aj-stop-" + os.urandom(4).hex(), "request": {"subtype": "interrupt"}}
        p.stdin.write((json.dumps(req) + "\n").encode())
        await p.stdin.drain()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(td.wait(), 2)

    def perm_lost(self) -> None:
        p = self.proc
        if p and p.returncode is None:
            self.host.agent_notice("Claude Code 的批准通道断了：为了安全，已重启 Claude Code。")
            bg.spawn(self._kill(p), "agent_perm_lost_kill", self.host.st.log)

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.proc = None
        self.ended.add(p)
        if p.returncode is None:
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    async def _drain_err(self, proc) -> None:
        with contextlib.suppress(Exception):
            while line := await proc.stderr.readline():
                self.err_tail = (self.err_tail + line.decode("utf-8", "replace"))[-2000:]

    async def _read(self, proc, resumed: bool) -> None:
        try:
            while line := await proc.stdout.readline():
                try:
                    ev = json.loads(line)
                except ValueError:
                    if line.strip():                # P219: not stream-json = Claude Code talking to a terminal
                        self.raw_tail = (self.raw_tail + line.decode("utf-8", "replace"))[-2000:]
                    continue
                if isinstance(ev, dict):
                    self._busy(ev)
                    self.on_event(ev)
        except Exception as e:  # noqa: BLE001 — P192 (B18): the harness link ends here; say so in host.log
            bg.swallowed(bg.host_log(self.host), "agent_read_failed", e, why=self.kind)
        code = await proc.wait()
        self._busy(None)
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if self.proc is proc:
            self.proc = None
        for f in list(self.ctl.values()):
            if not f.done():
                f.set_exception(ConnectionError("Claude Code exited"))
        quiet = proc in self.ended
        self.ended.discard(proc)
        if resumed and not self.got_init and not quiet:   # a stale session id: start a fresh conversation next time
            self.host.st.set_agent_session(self.kind, None)
        if self.turn_proc is proc and self.turn_done and not self.turn_done.is_set():
            last = clean(self.err_tail.strip().splitlines()[-1], 200) if self.err_tail.strip() else ""
            if not quiet and not self.halting:
                from . import claude_auth
                self.fail_notice(claude_auth.failure_notice(self.cfg.get("language", "zh")) if claude_auth.login_failure(last) else
                                 f"Claude Code 退出了（{code}）" + (f"：{last}" if last else ""))
            self.turn_done.set()

    def _busy(self, ev) -> None:
        """P150: the chat session's sub-agents → the phone's 「在办事项」 dots (busy.py). A workflow CEO run is one item
        of its own (tasks.py); None = the process ended, so nothing of it is running any more."""
        b = getattr(self.host, "busy", None)
        if b is None or self.cfg.get("_workflow_ceo"):
            return
        with contextlib.suppress(Exception):
            b.session_gone("own") if ev is None else b.stream_event(ev, "own")

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t in ("assistant", "user", "result", "stream_event"):
            self.touch()                           # P219: progress (the stall watch stays quiet)
        if t == "system" and ev.get("subtype") == "api_retry":
            self._api_retry(ev)
        elif t == "system" and ev.get("subtype") == "init":
            first = not self.init
            self.got_init = True
            self.init = ev
            sid = ev.get("session_id")
            if isinstance(sid, str):
                self.host.st.set_agent_session(self.kind, sid)
            if first:
                m = self.cur_model()
                self.meter(model=m, model_name=self.model_name(m), effort=self.cur_effort())
                if not self.models_cache and getattr(self, "_models_task", None) is None:
                    self._models_task = asyncio.get_running_loop().create_task(self.refresh_models())
        elif t == "system" and ev.get("subtype") == "status":
            if ev.get("status") == "compacting":
                self.set_status("compacting")
            elif self.status == "compacting":
                self.set_status("working")
        elif t == "system" and ev.get("subtype") == "compact_boundary":
            meta = ev.get("compact_metadata") if isinstance(ev.get("compact_metadata"), dict) else {}
            self.boundary, self.replay = meta, True
            if self.capture is None:               # Claude Code compacted on its own in the middle of a turn
                self.host.agent_notice(f"Claude Code 自动压缩了上下文：{slash.tokens(meta.get('pre_tokens'))} → "
                                       f"{slash.tokens(meta.get('post_tokens'))} tokens")
                with contextlib.suppress(RuntimeError):      # F24: a new epoch (handover note / reminder) all the same
                    bg.spawn(compactprep.compacted(self, auto=True), "compacted", self.host.st.log)
        elif t == "control_response":
            r = ev.get("response") if isinstance(ev.get("response"), dict) else {}
            f = self.ctl.get(r.get("request_id"))
            if f and not f.done():
                f.set_result(r)
        elif t == "rate_limit_event":
            if isinstance(ev.get("rate_limit_info"), dict):
                self.rate = ev["rate_limit_info"]
                wins = self.rate.get("unifiedWindows") if isinstance(self.rate.get("unifiedWindows"), dict) else {}
                upd = {}
                for k, key in (("five_hour", "h5"), ("seven_day", "week")):
                    w = wins.get(k)
                    if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)) \
                            and not isinstance(w.get("utilization"), bool):
                        upd[key] = {"pct": round(100 * float(w["utilization"]), 1), "reset": _unix(w.get("resetsAt"))}
                if upd:
                    self.meter(**upd)
        elif t == "assistant":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            synthetic = msg.get("model") == "<synthetic>"
            if synthetic and self.replay:          # the kept segment re-sent after a compact_boundary: not a new reply
                return
            if synthetic and self.capture is None and any(
                    isinstance(b, dict) and str(b.get("text", "")).startswith("API Error") for b in msg.get("content") or []):
                return     # P219: the turn's result carries the same error (its notice, or the model fallback) — not a reply
            if not synthetic:
                self.replay = False
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    if self.capture is not None:
                        self.capture.append(block["text"])
                    else:
                        self.host.agent_text(block["text"])
        elif t == "result":
            self.replay = False
            self.last_result = ev
            if (ev.get("is_error") or ev.get("subtype") not in ("success", None)) and not self.halting \
                    and self.capture is None and self._unsupported_model(ev):
                pass                               # P219: turn() switches to the default model and sends it again
            elif (ev.get("is_error") or ev.get("subtype") not in ("success", None)) and not self.halting \
                    and self.capture is None:
                r = ev.get("result")
                from . import claude_auth
                self.fail_notice(claude_auth.failure_notice(self.cfg.get("language", "zh")) if claude_auth.login_failure(r) else
                                 "Agent 这一轮没有正常完成" + (f"：{clean(r, 300)}" if isinstance(r, str) and r else "。"))
            if self.turn_done:
                self.turn_done.set()

    def _api_retry(self, ev: dict) -> None:
        """P219: Claude Code retries a failing model request by itself (measured 2.1.296: 10 attempts, ≈ 3 min when the
        service cannot be reached) and says so only in `api_retry` events — the phone hears about it once per turn."""
        attempt, most = ev.get("attempt"), ev.get("max_retries")
        if self.retry_told or not isinstance(attempt, int) or attempt < RETRY_NOTICE_AT or self.capture is not None:
            return
        self.retry_told = True
        code = ev.get("error_status") if isinstance(ev.get("error_status"), int) else None
        en = getattr(self.host, "lang", "zh") == "en"
        what = (f"HTTP {code}" if code else clean_line(str(ev.get("error") or "unknown"), 40))
        why = (("rate limited" if en else "被限流") if code == 429 else ("the service is busy or failing" if en else "服务繁忙或出错")
               if code and code >= 500 else ("could not connect (network / proxy / address)" if en else "连不上（网络 / 代理 / 地址）")
               if not code else ("the request was refused" if en else "请求被拒绝"))
        self.host.st.log("agent_api_retry", agent=self.kind, code=code, count=attempt)
        self.host.agent_notice(
            f"Claude Code cannot reach its model service ({why}, {what}) and is retrying by itself: attempt {attempt}"
            f"{f' of {most}' if isinstance(most, int) else ''}. Giving up takes about 3 minutes; it continues on its own when the "
            "network or provider recovers. At the computer: `agentj doctor`, and check the provider (e.g. Agents Relay) status."
            if en else
            f"Claude Code 连不上模型服务（{why}，{what}），正在自动重试：第 {attempt}"
            f"{f'/{most}' if isinstance(most, int) else ''} 次。全部失败约需 3 分钟；网络或服务商恢复后会自己接上。"
            "在电脑上可以运行 `agentj doctor`，并检查服务商（例如 Agents Relay）是否正常。")

    def _unsupported_model(self, ev: dict) -> bool:
        """P219: a 400 that says the model is not served (a relay without Fable: 「Claude Fable 5.1 is not supported」) on a
        model other than Agent J's default → remembered for turn() to retry once on the default."""
        from . import harness_setup
        r = ev.get("result") if isinstance(ev.get("result"), str) else ""
        status = ev.get("api_error_status")
        if self.fell_back or self.cfg.get("_workflow_ceo") or not (status == 400 or "400" in r[:40]):
            return False
        if not re.search(r"not (be )?supported|unsupported|not available|does not exist|not[_ ]found|invalid model|"
                         r"unknown model|model[^\n]{0,40}(not|unavailable)", r, re.I):
            return False
        cur = self.cur_model()
        if cur == harness_setup.DEFAULT_CLAUDE_MODEL:
            return False
        self.fallback = (cur, clean(r, 200))
        return True

    def stall_probe(self):
        """P219: a terminal read by Claude Code (or something it started), or text it printed outside stream-json."""
        if self.retry_told:
            return "skip"
        p = self.proc
        if p is None or p.returncode is not None:
            return None
        if tty_waiting(p.pid):
            return "tty", self.printed_tail()
        return ("prompt", self.printed_tail()) if self.raw_tail.strip() else None

    def transcript_path(self) -> str | None:
        sid = self.host.st.agent_session(self.kind)
        if not sid:
            return None
        import re
        root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        for d in dict.fromkeys((self.cfg["dir"], os.path.realpath(self.cfg["dir"]))):
            p = os.path.join(root, "projects", re.sub(r"[^A-Za-z0-9]", "-", d), sid + ".jsonl")
            if os.path.isfile(p):
                return p
        return None

    async def clear_native_goal(self) -> bool:
        """P198: before 0.20.1 the phone's `/goal <what>` reached Claude Code verbatim and set its native goal, whose Stop hook
        then re-asked every turn 「…发 /goal clear」. A session left that way is cleaned once per process: `/goal clear` as a
        local command (no model call, no phone page), before the owner's message. True when one was cleared."""
        from . import goal as goal_mod
        t = self.transcript_path()
        if not t or not await asyncio.to_thread(goal_mod.native_goal_active, t):
            return False
        cur, self.cur_send = self.cur_send, None   # the clear is not the owner's message: it never marks it delivered
        try:
            await self.local("/goal clear")
        finally:
            self.cur_send = cur
        still = await asyncio.to_thread(goal_mod.native_goal_active, t)
        self.host.st.log("native_goal_cleared", agent=self.kind, result="still_set" if still else "ok")
        return not still

    async def turn(self, text: str) -> None:
        if self.capture is None and not self.native_goal_checked:
            self.native_goal_checked = True
            with contextlib.suppress(OSError, ConnectionError, ValueError):
                await self.clear_native_goal()
        self.retry_told, self.fallback, self.fell_back = False, None, False
        retried = False
        for attempt in range(3):
            if self.proc is None and not await self._spawn():
                return
            self.turn_done, self.turn_proc = asyncio.Event(), self.proc
            line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"
            proc = self.proc

            async def write():
                proc.stdin.write(line.encode())
                await proc.stdin.drain()
            try:
                await self.deliver(write)          # §10.2: the stdin line written = delivered
            except (ConnectionError, AttributeError):
                self.proc = None
                continue
            await self.turn_done.wait()
            self.fresh = False
            if self.fallback is not None and not self.fell_back:   # P219: the model is not served → once on the default
                await self._fall_back()
                continue
            if self.got_init or retried:   # a resumed process that died before init is retried once, fresh
                if self.capture is None:
                    await self.context_meter()
                return
            retried = True

    async def _fall_back(self) -> None:
        from . import harness_setup
        old, err = self.fallback
        self.fallback, self.fell_back = None, True
        new = harness_setup.DEFAULT_CLAUDE_MODEL
        self.cfg["model"] = new
        with contextlib.suppress(Exception):   # the next start (and every later one) uses it; the owner can change it back
            self.host.st.set_agent_model(new)
        self.host.st.log("agent_model_fallback", agent=self.kind, model=new)
        p = self.proc
        if p is not None:
            await self._kill(p)
        self.init, self.got_init = {}, False
        nice = pretty_model(new) or new
        self.host.agent_notice(pick(getattr(self.host, "lang", "zh"),
                                    f"模型 {old or '（Claude Code 自选）'} 用不了：{err}\n已自动改用默认模型 {nice} 重发这条消息，以后也用它；"
                                    "想换模型：点手机上的模型标签。",
                                    f"Model {old or '(Claude Code default)'} is not available: {err}\nSent the message again on the default "
                                    f"model {nice}, which stays selected; tap the model label on the phone to change it."))

    async def context_meter(self) -> None:
        """§10.10: `get_context_usage` after each turn end (and compaction) — the exact numbers Claude Code reports."""
        if self.proc is None or self.proc.returncode is not None:
            self.meter(ctx=None)
            return
        selected = self.cur_model()
        try:
            u = await asyncio.wait_for(self.control("get_context_usage"), 5)
        except (RuntimeError, ConnectionError, OSError, ValueError, asyncio.TimeoutError):
            if self.cur_model() == selected:
                self.meter(ctx=None)
            return
        u = u if isinstance(u, dict) else {}
        used, mx = u.get("totalTokens"), u.get("maxTokens")
        from .model_limits import positive
        # Claude exposes no trustworthy OpenAI provider endpoint here; retain
        # measured tokens without substituting an alias or stale percentage.
        ctx = {"used": used, "max": positive(mx)} if type(used) is int and used >= 0 else None
        if self.cur_model() == selected:
            self.meter(ctx=ctx)

    def cur_model(self) -> str | None:
        m = self.cfg.get("model") or self.init.get("model")
        return m if isinstance(m, str) and m else None

    EFFORTS = CLAUDE_EFFORTS

    async def refresh_models(self) -> None:
        try:
            r = await self.control("list_models")
        except (RuntimeError, ConnectionError, OSError, ValueError, asyncio.TimeoutError):
            self._models_task = None
            return
        out = []
        for m in r.get("models") or []:
            if isinstance(m, dict) and isinstance(m.get("value"), str) and slash.MODEL_RE.match(m["value"]):
                name = str(m.get("displayName") or m["value"])
                if m["value"] != "default" and not re.search(r"\d", name):     # P219: "Opus" → "Opus 5.5"
                    name = pretty_model(m.get("resolvedModel")) or name
                out.append({"id": clean_line(m["value"], 100), "name": clean_line(name, 60),
                            "efforts": list(CLAUDE_EFFORTS),
                            "resolved": clean_line(str(m.get("resolvedModel") or ""), 100)})
        self.models_cache = out[:40]
        fn = getattr(self.host, "models_changed", None)
        if fn:
            fn()
        cur = self.cur_model()
        self.meter(model=cur, model_name=self.model_name(cur))

    def model_name(self, model: str | None) -> str | None:
        """P219: always with its version ("Opus 5.5"): a concrete id names itself; an alias / "default" by what it resolves to."""
        if pretty_model(model):
            return pretty_model(model)
        for m in self.models_cache:
            if model and (m["id"] == model or m.get("resolved") == model):
                return pretty_model(m.get("resolved")) or m["name"]
        return None

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        """Model: the `set_model` control request (already allowed, CONTROL_SUBTYPES). Effort: no allowed control request
        exists (apply_flag_settings stays forbidden, Invariant 26), so the idle process is restarted with
        `--resume <id> --effort <level>`. Default (long press): both cleared, the process restarted without the flags."""
        if model is not None and not default and self.proc is not None and self.proc.returncode is None:
            try:
                await self.control("set_model", model=model)
            except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError):
                return "unsupported"
        if default or effort is not None:
            await self._end_proc()                 # the next message starts Claude Code with the new flags
            self.init, self.got_init = {}, False
        return await super().apply_model(model, effort, default)

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        self.proc = None
        if p and p.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                p.stdin.close()
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    # ------------------------------------------------ slash commands (slash.py)
    def passthrough(self, name: str) -> bool:
        skills = self.init.get("skills") if isinstance(self.init.get("skills"), list) else []
        return name in skills and name not in slash.WHITELIST

    async def _ensure(self) -> bool:
        return self.proc is not None or await self._spawn()

    async def local(self, text: str) -> dict:
        """Send a command Claude Code executes itself (`/compact`, `/cost`); its output is collected for the card."""
        self.capture, self.boundary, self.last_result = [], None, {}
        try:
            await self.turn(text)
            return {"texts": self.capture, "result": self.last_result, "boundary": self.boundary}
        finally:
            self.capture = None

    async def drop_conversation(self) -> None:
        await self._end_proc()                     # the next message starts Claude Code with the session agent.json names
        self.init, self.got_init = {}, False

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        t0 = time.monotonic()
        r = await self.local("/compact")
        b = r["boundary"]
        await self.context_meter()
        if b:
            return Result(f"已压缩：{slash.tokens(b.get('pre_tokens'))} → {slash.tokens(b.get('post_tokens'))} tokens"
                          f"{slash.secs(b.get('duration_ms') if isinstance(b.get('duration_ms'), int) else (time.monotonic() - t0) * 1000)}")
        res = r["result"].get("result") if isinstance(r["result"].get("result"), str) else ""
        return Result("没有压缩" + (f"：{clean(res, 300)}" if res.strip() else "（Claude Code 没有报告结果）。"), "error")

    async def cmd_cost(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("本会话花费：$0（还没有对话）", "info")
        r = await self.local("/cost")
        text = r["result"].get("result") if isinstance(r["result"].get("result"), str) else "\n".join(r["texts"])
        if not text.strip():
            return Result("本会话花费：" + slash.NONE, "info")
        return Result("本会话花费（Claude Code 报告）：\n" + clean(text.strip(), 1500))

    async def cmd_context(self, arg: str) -> Result:
        if not await self._ensure():
            return Result("Claude Code 没起来：" + slash.NONE, "error")
        try:
            u = await self.control("get_context_usage")
        except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"上下文：{slash.NONE}（{clean(str(e), 120) or type(e).__name__}）", "error")
        total, mx = u.get("totalTokens"), u.get("maxTokens")
        cats = [c for c in u.get("categories") or [] if isinstance(c, dict) and c.get("kind") == "used"]
        detail = " · ".join(f"{clean_line(str(c.get('name')), 40)} {slash.tokens(c.get('tokens'))}" for c in cats[:6])
        model = clean_line(str(self.init.get("model") or self.cfg.get("model") or ""), 60)
        return Result(f"上下文 {slash.tokens(total)} / {slash.tokens(mx)}{slash.pct(total, mx)}" + (f" · 模型 {model}" if model else "")
                      + (f"\n{detail}" if detail else ""))

    async def cmd_usage(self, arg: str) -> Result:
        r = self.rate if isinstance(self.rate, dict) else {}
        wins = r.get("unifiedWindows") if isinstance(r.get("unifiedWindows"), dict) else {}
        lines = []
        for k, name in (("five_hour", "5 小时窗口"), ("seven_day", "7 天窗口"), ("seven_day_opus", "7 天窗口（Opus）")):
            w = wins.get(k)
            if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
                rs = slash.when(w.get("resetsAt"))
                lines.append(f"{name}已用 {round(100 * w['utilization'])}%" + (f"（{rs} 重置）" if rs else ""))
        if not lines:
            return Result("套餐用量：" + slash.NONE + "（Claude Code 还没报告过：发一条消息之后再看）", "info")
        return Result("套餐用量（Claude Code 报告）：" + "；".join(lines))

    async def cmd_status(self, arg: str) -> Result:
        rows = []
        if await self._ensure():
            with contextlib.suppress(RuntimeError, ConnectionError, OSError, asyncio.TimeoutError):
                st = await self.control("get_status")
                for sec in st.get("sections") or []:
                    for row in (sec.get("rows") if isinstance(sec, dict) else None) or []:
                        if isinstance(row, dict) and row.get("label") in ("Version", "Session ID", "cwd", "Model"):
                            rows.append(f"{clean_line(str(row['label']), 20)}：{clean_line(str(row.get('value')), 200)}")
        if not rows:   # the control request failed: what the host knows itself
            rows = [f"Version：{clean_line(str(self.init.get('claude_code_version') or '?'), 40)}",
                    f"Session ID：{self.host.st.agent_session(self.kind) or '（新对话）'}", f"cwd：{self.cfg['dir']}",
                    f"Model：{clean_line(str(self.init.get('model') or self.cfg.get('model') or '默认'), 80)}"]
        rows += ["审批：Claude Code 问到的都上手机；危险清单生效",
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("Agent：Claude Code\n" + "\n".join(rows))

    async def cmd_model(self, arg: str) -> Result:
        if not await self._ensure():
            return Result("Claude Code 没起来，没法换模型。", "error")
        if not arg:
            try:
                r = await self.control("list_models")
            except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
                return Result(f"没拿到模型列表（{clean(str(e), 120) or type(e).__name__}）。", "error")
            cur = self.cfg.get("model") or self.init.get("model") or ""
            models = []
            for m in r.get("models") or []:
                if isinstance(m, dict) and isinstance(m.get("value"), str):
                    v = clean_line(m["value"], 100)
                    models.append({"id": v, "name": clean_line(str(m.get("displayName") or v), 60),
                                   "desc": clean_line(str(m.get("description") or ""), 120),
                                   "cur": v == cur or m.get("resolvedModel") == cur})
            return Result(f"当前模型：{cur or '默认'}。点一个切换（之后的对话都用它）：", models=models[:40])
        if not slash.MODEL_RE.match(arg):
            return Result(f"模型名不对：{clean_line(arg, 100)}", "error")
        try:
            await self.control("set_model", model=arg)
        except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"没有切换（{clean(str(e), 120) or type(e).__name__}）。", "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=self.model_name(arg))
        return Result(f"已切换到 {arg}：立即生效，写进了 config.json（重启后也用它）。")

    async def cmd_help(self, arg: str) -> Result:
        skills = [s for s in (self.init.get("skills") or []) if isinstance(s, str)][:40]
        return Result(slash.HELP + (("\nClaude Code 的技能（照原文转给它）：" + " ".join("/" + clean_line(s, 40) for s in skills))
                                    if skills else ""))


def make(host, cfg: dict | None):
    if not cfg:
        return None
    if cfg.get("session_mode") == "shared" and not cfg.get("_workflow_ceo"):
        from .shared import make_shared
        return make_shared(host, cfg)
    if cfg["kind"] == "claude":
        return ClaudeAgent(host, cfg)
    if cfg["kind"] == "codex":
        from .agent_codex import CodexAgent        # codex app-server + phone approvals (ADR-A70)
        return CodexAgent(host, cfg)
    if cfg["kind"] == "opencode":
        from .agent_opencode import OpenCodeAgent   # opencode serve + SSE + phone approvals (ADR-A55)
        return OpenCodeAgent(host, cfg)
    return None
