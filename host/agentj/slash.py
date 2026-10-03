"""Slash commands from the phone (PROMPT-26 item 7a, ADR-A70 – A72; PROTOCOL §8 "slash commands").

The product host runs every harness headless, so a `/compact` typed on the phone cannot be "typed into a terminal". serve
interprets a short whitelist itself and carries each one out through the harness's own official headless interface:

    /clear  /compact  /model [name]  /context  /cost  /usage  /status  /help  /stop       (+ the phone's 「撤销清空」)

- Claude Code (stream-json): `/compact` and `/cost` are sent as the text Claude Code itself executes (no model call for
  `/cost`; `/compact` ends with a `compact_boundary`), `/context` / `/model` / `/status` use the stream-json control requests
  `get_context_usage` / `list_models` + `set_model` / `get_status` (only the subtypes in `agent.CONTROL_SUBTYPES` are ever
  sent), `/usage` is the last `rate_limit_event`. A `/name` that is one of Claude Code's skills (its init `skills`) is passed
  through as an ordinary message — the skill runs under the usual permission flow.
- Codex (`codex app-server`): `thread/compact/start`, `model/list`, `account/rateLimits/read`, `thread/tokenUsage/updated`.
- OpenCode (`opencode serve`): `POST /session/{id}/summarize`, `GET /config/providers`, `GET /session/{id}`, the messages'
  token counts.
- `/clear` is the host's own for all three: the conversation id is put aside (kept for 「撤销清空」) and the next message
  starts a new one; `/stop` = the stop switch's interrupt, for this turn only (nothing is paused).

Anything else that looks like a command is not run: 「这个命令请在电脑上执行」. Usage figures are only what the harness reports
exactly; when there is none the phone shows "—" (never an estimate of what is left).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

WHITELIST = ("clear", "compact", "model", "context", "cost", "usage", "status", "help", "stop")
INTERNAL = ("undo_clear",)                      # the phone's 「撤销清空」 button (never typed)
LABEL = {"clear": "清空", "compact": "压缩", "model": "换模型", "context": "上下文", "cost": "花费", "usage": "用量",
         "status": "状态", "help": "帮助", "stop": "停止", "undo_clear": "撤销清空", "skill": "技能", "refused": "命令"}
ARG_MAX = 200
_CMD = re.compile(r"^/([A-Za-z][\w:.-]{0,63})(?:[ \t]+(.*))?$", re.S)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]@+-]{0,99}$")
REFUSE = "这个命令请在电脑上执行。手机上能用的：/clear /compact /model /context /cost /usage /status /help /stop"
NONE = "—"


def parse(text) -> tuple[str, str] | None:
    """("name", "arg") for a message that is a command (`/name` or `/name args`), else None. `/srv/x …` is not a command
    (the name must end at a space or the end), so a path at the start of a message stays a message."""
    if not isinstance(text, str):
        return None
    m = _CMD.match(text.strip())
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()[:ARG_MAX]


@dataclass
class Result:
    """What the phone's command card shows. kind: ok | error | refused | info."""
    text: str
    kind: str = "ok"
    models: list = field(default_factory=list)     # [{"id", "name", "cur"}] → buttons (≤ 40)
    undo: bool = False                             # 「撤销清空」 offered on this card
    sep: bool = False                              # a divider in the chat (clear / undo)


def tokens(n) -> str:
    """21262 → "21.3k"; 1344 → "1.3k"; 999 → "999"; None → "—"."""
    if not isinstance(n, (int, float)) or isinstance(n, bool) or n < 0:
        return NONE
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def pct(used, total) -> str:
    try:
        if not total or used is None or used < 0:
            return ""
        return f"（{round(100 * used / total)}%）"
    except TypeError:
        return ""


def when(ts) -> str:
    """unix seconds → local "10/09 14:00" (the host's clock); "" when not a number."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts <= 0:
        return ""
    if ts > 10_000_000_000:          # milliseconds
        ts /= 1000
    return dt.datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")


def window(mins) -> str:
    if not isinstance(mins, int) or isinstance(mins, bool) or mins <= 0:
        return ""
    if mins % 1440 == 0:
        return f"{mins // 1440} 天窗口"
    if mins % 60 == 0:
        return f"{mins // 60} 小时窗口"
    return f"{mins} 分钟窗口"


def money(v) -> str:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
        return NONE
    return f"${v:.4f}"


def secs(ms) -> str:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms < 0:
        return ""
    return f"（{ms / 1000:.0f} 秒）" if ms >= 1000 else f"（{ms / 1000:.1f} 秒）"


HELP = ("手机上能用的命令（也可以点输入框左边的 ≡「全部命令」）：\n"
        "/compact 压缩上下文 · /clear 清空对话（可撤销） · /model [名字] 换模型 · /context 上下文用了多少 · "
        "/cost 本会话花费 · /usage 套餐用量 · /status 状态 · /stop 停下这一轮 · /help 这条说明\n"
        "别的命令（比如改设置的）请在电脑上执行。")
