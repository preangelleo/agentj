"""P157 layer 2 of the approval card: Jev may only make FEWER cards, never vouch for a clear danger.

Layer 1 is danger.py — code, deterministic. A clear hit of the five categories (spend / delete / send / credentials / price:
git push, a deploy, a payment API, an S3 delete, a write to a credential path …) is a card, always; Jev is never asked
(a command's text can be shaped by injected content, so a model is never the last gate). What layer 1 can only call
"can't tell" — `opaque.dynamic_name` (`$PY -m tests`), `opaque.too_deep`, `too_long` — and nothing else, is "gray": with the
owner's Jev available, Jev is asked one calibrated question — given the owner's latest words, what the Agent said it is
doing this turn, and this command: is the command consistent with that task and outside the five categories? p ≥
`approval.jev_threshold` (default 0.70, 0.50–0.95 in steps of 0.05) → allowed without a card, logged as `jev` with p and a
one-line reason; anything else → the card, as before. Encoded / piped-into-a-shell / ANSI forms (`curl … | sh`,
`base64 -d | bash`, `$'\\x72m'`) are not gray: they stay cards.

Fail closed: no OPENROUTER_API_KEY, a timeout (TIMEOUT_S), an HTTP error or an odd answer → "unavailable" → the card.
What goes to Jev passes privacy.redact first (keys, tokens, home paths, host / user names); the key itself is only an
Authorization header to ENDPOINT, never logged. Stdlib only.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import shlex
from dataclasses import dataclass

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "~typesafe/jev-latest"
KEY_ENV = "OPENROUTER_API_KEY"
TIMEOUT_S = 2.0
DEFAULT_THRESHOLD = 0.70
MIN_THRESHOLD, MAX_THRESHOLD, STEP = 0.50, 0.95, 0.05
SOFT_RULES = frozenset({"opaque.dynamic_name", "opaque.too_deep", "too_long"})
MAX_TEXT = 1500
MAX_COMMAND = 4000
UA = "agentj (approval layer 2)"

QUESTION = ("`command` 是一个 AI 编程助手在主人的电脑上准备执行的一条命令。结合 `owner_request`（主人最近的原话）"
            "和 `agent_plan`（助手这一回合自己说要做的事），这条命令是否与任务一致，并且不属于下面五类危险动作："
            "花钱（付款、下单、交易、开计费资源）、删除（删文件、分支、云资源、数据库数据）、对外发送（发邮件消息、"
            "发帖、git push、部署上线、上传到别处）、改凭据（改密码、密钥、登录、权限、钩子设置）、改价（价格、优惠、预算）？")
CRITERIA = {
    "true": ("命令明显是在完成主人交代或助手声明的任务，而且只是在本机读文件、跑测试、构建、查看状态、写普通笔记或代码、"
             "起停自己派的用户级后台任务、等待轮询；命令里的变量（如 $PY、$D）只是路径或程序的简写。"),
    "false": ("命令和任务对不上；或者属于五类危险动作之一；或者在下载并执行远程代码、解码后执行、读取并外发凭据、"
              "改系统设置；或者看不出它要做什么。拿不准时判 false。"),
}


@dataclass
class Result:
    status: str                  # "allow" | "ask" | "unavailable"
    p: float | None = None
    reason: str = ""             # one line for the log / the phone history (no command text, no key)


def gray(v) -> bool:
    """A verdict layer 2 may look at: its only rules are "can't tell" ones, nothing decoded behind them."""
    rules = set(getattr(v, "rules", []) or [])
    return bool(rules) and rules <= SOFT_RULES and not getattr(v, "seen", "")


def threshold(prefs) -> float:
    """approval.jev_threshold from the preferences, clamped to 0.50–0.95; anything odd → the default."""
    try:
        t = float((prefs or {}).get("approval", {}).get("jev_threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_THRESHOLD
    if not math.isfinite(t):
        return DEFAULT_THRESHOLD
    return min(MAX_THRESHOLD, max(MIN_THRESHOLD, t))


def available(env=None) -> bool:
    env = os.environ if env is None else env
    return bool((env.get(KEY_ENV) or "").strip())


def _command_text(tool: str, tool_input) -> str:
    if isinstance(tool_input, dict):
        for k in ("command", "cmd"):
            if isinstance(tool_input.get(k), str):
                return tool_input[k]
    try:
        return tool + " " + json.dumps(tool_input, ensure_ascii=False)
    except (TypeError, ValueError):
        return tool


def _clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n // 2] + "\n…\n" + text[-n // 2:]


def request_body(tool: str, tool_input, context: dict | None) -> dict:
    from .privacy import redact
    ctx = context or {}
    state = {"owner_request": redact(_clip(str(ctx.get("owner") or "（没有）"), MAX_TEXT)),
             "agent_plan": redact(_clip(str(ctx.get("plan") or "（没有）"), MAX_TEXT)),
             "command": redact(_clip(_command_text(tool, tool_input), MAX_COMMAND))}
    return {"model": MODEL, "state": state,
            "questions": {"consistent": {"type": "noul", "instructions": QUESTION, "criteria": CRITERIA}}}


def urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as r:
            return r.status, r.read(65537)
    except urllib.error.HTTPError as e:
        return e.code, b""


def judge(tool: str, tool_input, context: dict | None, *, threshold: float = DEFAULT_THRESHOLD, env=None,
          transport=None, timeout: float = TIMEOUT_S) -> Result:
    """Never raises. "allow" only for p ≥ threshold; every failure is "unavailable" (the caller shows the card)."""
    env = os.environ if env is None else env
    key = (env.get(KEY_ENV) or "").strip()
    if not key:
        return Result("unavailable", reason=f"no {KEY_ENV}")
    try:
        body = json.dumps(request_body(tool, tool_input, context), ensure_ascii=False).encode()
    except Exception as e:  # noqa: BLE001
        return Result("unavailable", reason=f"request ({type(e).__name__})")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA}
    try:
        status, raw = (transport or urllib_transport)(ENDPOINT, headers, body, timeout)
    except Exception as e:  # noqa: BLE001 — the class name only (a message could carry anything)
        return Result("unavailable", reason=f"network ({type(e).__name__})")
    if status != 200:
        return Result("unavailable", reason=f"HTTP {status}")
    try:
        p = float(json.loads(raw)["answers"]["consistent"]["noul"])
        if not 0.0 <= p <= 1.0 or math.isnan(p):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        return Result("unavailable", reason="unexpected answer")
    if p >= threshold:
        return Result("allow", p, f"Jev 判断与当前任务一致（p={p:.2f} ≥ {threshold:.2f}）")
    return Result("ask", p, f"Jev 不够确定（p={p:.2f} < {threshold:.2f}）")


def transcript_context(path, limit: int = 512 * 1024) -> dict:
    """Claude Code transcript (JSONL) → {"owner": the last real user prompt, "plan": the assistant's text after it}.
    Only a .jsonl under ~/.claude/projects is read (the path comes from the hook event); anything else → {}."""
    try:
        from pathlib import Path
        p = Path(str(path)).expanduser().resolve()
        root = (Path.home() / ".claude" / "projects").resolve()
        if p.suffix != ".jsonl" or root not in p.parents or not p.is_file():
            return {}
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, ValueError):
        return {}
    plan: list[str] = []
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if msg.get("role") == "assistant" and isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if any(t.strip() for t in texts):
                plan.insert(0, "\n".join(texts))
        elif msg.get("role") == "user" and not ev.get("isMeta"):
            if isinstance(content, str) and content.strip():
                return {"owner": content, "plan": "\n".join(plan)[-MAX_TEXT:]}
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if any(t.strip() for t in texts) and not any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                             for b in content):
                    return {"owner": "\n".join(texts), "plan": "\n".join(plan)[-MAX_TEXT:]}
    return {"plan": "\n".join(plan)[-MAX_TEXT:]} if plan else {}


# P198 (P164 D): layer 1's "not dangerous" for the harnesses without a PreToolUse hook. Claude Code's danger hook asks only on
# a hit, so a low-risk call never reaches the phone. Independent OpenCode / Codex with agent.high_risk_warnings ask Agent J
# for EVERY call that is not a plain read (our "*" ask rule / approvalPolicy=untrusted): `ls … 2>&1; echo`, `rg`, a heredoc
# note were each a card (35 in one /goal). Their adapters now pass such a call without a card, as Claude's hook would; a hit
# (gray included: layer 2 / the card) and every special request still go through Host.ask.
ALWAYS_ASK = frozenset({"ExternalDirectory", "DoomLoop", "task", "CodexPermissions", "SandboxOnce", "AskUserQuestion", "agentj goal"})


def low_risk_pass(host, cfg, tool, tool_input, batch: bool = True) -> bool:
    """True = run it without a card (logged `perm_low`). Only for an independent session with high_risk_warnings on, a
    batchable request of an ordinary tool, nothing stopped, the owner's own turn, no live goal (the goal path already lets
    low risk through and counts it), and layer 1 finds none of the five categories and nothing it cannot read.
    cfg = the asking adapter's own config (a scheduled task's adapter is not host.agent)."""
    try:
        cfg = cfg if isinstance(cfg, dict) else {}
        if (not batch or not cfg.get("high_risk_warnings") or cfg.get("session_mode") == "shared"
                or not isinstance(tool, str) or tool in ALWAYS_ASK or not isinstance(tool_input, dict)):
            return False
        stopped = getattr(host, "stopped", None)
        if callable(stopped) and stopped():
            return False
        from . import provenance
        if provenance.untrusted_turn(getattr(host, "agent", None)):
            return False
        goals = getattr(host, "goals", None)
        if goals is not None and goals.current() is not None:
            return False
        v = host._classify(tool, tool_input)
        if v.danger or v.cats:
            return False
        host.st.log("perm_low", agent=cfg.get("kind") or "?", tool=tool[:64])
        return True
    except Exception:  # noqa: BLE001 — fail closed: the ordinary path (Host.ask)
        return False


def with_cwd(command: str, cwd, home: str | None) -> str:
    """P198 (P164 G): the command as the card and goal.tier read it when the harness reports the folder it runs in (Codex's
    request `cwd`, OpenCode's bash `workdir`) and that is not the Agent folder: `cd <cwd> && <command>` — what really runs,
    so a relative path (`git rm old.log`) is matched against the goal grant where it is deleted. Anything odd → unchanged."""
    if not isinstance(command, str) or not isinstance(cwd, str) or not os.path.isabs(cwd) or "\n" in cwd or "\x00" in cwd:
        return command
    with contextlib.suppress(OSError, ValueError, TypeError):
        if home and os.path.realpath(cwd) == os.path.realpath(home):
            return command
        return "cd " + shlex.quote(cwd) + " && " + command
    return command
