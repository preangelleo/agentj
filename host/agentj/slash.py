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

Anything else that looks like a command is not run: 「这个命令请在Agent J 主机上执行」. Usage figures are only what the harness reports
exactly; when there is none the phone shows "—" (never an estimate of what is left).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

WHITELIST = ("clear", "compact", "model", "context", "cost", "usage", "status", "help", "stop", "update")
# P341 (#50): /new is /clear under the name Codex users know — same card, same confirm, same harness path (P328's new
# thread on a shared Codex session). Folded in here, so nothing downstream ever sees "new".
ALIAS = {"new": "clear"}
INTERNAL = ("undo_clear",)                      # the phone's 「撤销清空」 button (never typed)
# P155: not run by the host — always handed to the main Agent (any harness), rewritten by switch_harness.agent_text
AGENT_CMDS = ("switch-harness", "feedback-to-agentj-father")
LABEL = {"clear": "清空", "compact": "压缩", "model": "换模型", "context": "上下文", "cost": "花费", "usage": "用量",
         "status": "状态", "help": "帮助", "stop": "停止", "undo_clear": "撤销清空", "skill": "技能", "refused": "命令", "goal": "目标", "reload": "重新加载",
         "reset": "重置连接"}   # P332 (§33)
ARG_MAX = 200
_CMD = re.compile(r"^/([A-Za-z][\w:.-]{0,63})(?:[ \t]+(.*))?$", re.S)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]@+-]{0,99}$")
# P529: plain Markdown list; the phone upgrades only the host's refused card.
PHONE_COMMANDS = (
    ('clear', '开新对话（先确认）', 'Start a new conversation (asks first)'),
    ('new', '开新对话，同 /clear', 'Start a new conversation, like /clear'),
    ('compact', '压缩上下文', 'Compact the context'),
    ('model', '选择模型', 'Choose a model'),
    ('context', '查看上下文用量', 'Show context usage'),
    ('cost', '查看本会话花费', 'Show conversation cost'),
    ('usage', '查看套餐用量', 'Show plan usage'),
    ('status', '查看当前状态', 'Show current status'),
    ('help', '查看命令帮助', 'Show command help'),
    ('stop', '停下这一轮', 'Stop this turn'),
    ('my-agent-id', '查看 Agent 号码', 'Show your Agent number'),
    ('add-friend', '添加 Agent 好友', 'Add an Agent friend'),
    ('telegram-pair', '重新绑定 Telegram', 'Pair Telegram again'),
    ('update', '升级 Agent J（先确认）', 'Upgrade Agent J (asks first)'),
    ('reload', '重新加载设置', 'Reload settings'),
    ('reset', '逐步修复连接（照常确认）', 'Repair the connection (normal confirmation)'),
    ('switch-harness', '检查并切换 AI', 'Check and switch the AI'),
    ('goal', '设置要完成的目标', 'Set a goal'),
)


def refuse(lang='zh') -> str:
    head = ('Run this command on the Agent J host. Commands available on the phone:' if lang == 'en' else
            '这个命令请在Agent J 主机上执行。手机上能用的：')
    return head + '\n\n' + '\n'.join('- /' + cmd + ' — ' + (en if lang == 'en' else zh)
                                              for cmd, zh, en in PHONE_COMMANDS)


REFUSE = refuse()

NONE = "—"
# F24: the whole message, nothing else (「把这个文件压缩一下」 stays a message)
SAY_COMPACT = ("压缩", "压缩一下", "压缩吧", "压缩上下文", "compact", "compact now", "compact the context")


def parse(text) -> tuple[str, str] | None:
    """("name", "arg") for a message that is a command (`/name` or `/name args`), else None. `/srv/x …` is not a command
    (the name must end at a space or the end), so a path at the start of a message stays a message. F24: a message that is
    nothing but 「压缩」 (SAY_COMPACT) is `/compact` — the word the context reminder teaches (PROTOCOL §15)."""
    if not isinstance(text, str):
        return None
    if text.strip().rstrip("。.!！").strip().lower() in SAY_COMPACT:
        return "compact", ""
    m = _CMD.match(text.strip())
    if not m:
        return None
    name = m.group(1).lower()
    return ALIAS.get(name, name), (m.group(2) or "").strip()[:ARG_MAX]


DONE = {"compact": "compact.done", "clear": "clear.greet"}   # P133 → P280: the Agent's own words (humane.json)


def done_line(name, lang="zh", prefs=None, now=None) -> str:
    from . import humane
    return humane.say(DONE[name], lang, prefs, now) if name in DONE else ""


def with_done(name, text, lang="zh", prefs=None, now=None) -> str:
    """The success line of /clear and /compact. /clear's greeting comes first (a new conversation opens with it);
    /compact's goes under the result's first line."""
    line = done_line(name, lang, prefs, now)
    if not line or line in (text or ""):
        return text
    if name == "clear":
        return "\n".join(x for x in (line, (text or "").strip()) if x)
    head, _, rest = (text or "").strip().partition("\n")
    return "\n".join(x for x in (head, line, rest) if x)


@dataclass
class Result:
    """What the phone's command card shows. kind: ok | error | refused | info."""
    text: str
    kind: str = "ok"
    models: list = field(default_factory=list)     # [{"id", "name", "cur"}] → buttons (≤ 40)
    undo: bool = False                             # 「撤销清空」 offered on this card
    sep: bool = False                              # a divider in the chat (clear / undo)
    open: str = ""                                 # P73: a page button — "fr_card" | "fr_add" | "fr_add:<Agent ID>"


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
        "/compact 压缩上下文 · /clear（或 /new）开新对话（发消息前可撤销） · /model [名字] 换模型 · /context 上下文用了多少 · "
        "/cost 本会话花费 · /usage 套餐用量 · /status 状态 · /stop 停下这一轮 · /help 这条说明\n"
        "/update 升级 Agent J（发送即授权安装、重启、自检）\n"
        "/reload 重新加载配置（不重启后台服务；改了设置后用）\n"
        "/reset 重置连接（手机连不上Agent J 主机上的 Agent J 时用，逐步修复）\n"
        "/switch-harness [切换到 Codex] 换底层 harness：交给主 Agent，先检查、能用才切\n"
        "好友：/my-agent-id 我的 Agent ID 和分享链接 · /add-friend <ID> [附言] 加好友\n"
        "Telegram：/telegram-pair 重新绑定 Telegram（手机上出一次性配对码）\n"
        "别的命令（比如改设置的）请在Agent J 主机上执行。")
