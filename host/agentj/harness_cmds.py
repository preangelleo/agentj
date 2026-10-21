"""P412 (#175): the current harness's own slash commands, mirrored into the phone's `/` list — and, since #175 定稿, usable.

Leo (10-20 18:44): 「把下层 Claude Code、Codex、OpenCode 用的 slash command 动态镜像出来，切换的时候这一部分是动态加载……重复的部分只
显示一次」. Leo (20:2x, 定稿): 「用户你还是让他输入，只不过你在后端把这些危险的截获了以后，让 agent 自己去执行……不危险的直接发到后边执行」.
The `menu` answer carries `native` = this harness's commands that Agent J's own list does not already have, each with a route:

    {"kind": "claude", "native": [{"cmd": "/init", "desc": "…", "state": "run"}, {"cmd": "/mcp", "state": "agent"}, …]}

- `run`   — changes no setting and the harness's headless interface runs it itself: the typed text goes to the harness as is.
            A fixed list only (Claude Code's prompt commands /init /review /security-review …) — never "whatever init reports":
            a security review (P412) found reported built-ins such as /auto-mode-setup (permission rules), /design-consent
            (grants access) and /heapdump (writes the heap, tokens included) that a default-allow would have run.
- `agent` — changes a setting or needs the terminal screen (/config /mcp /effort /permissions …, and every Codex / OpenCode
            TUI command): the host catches it and the main Agent gets 「〔菜单命令 /x〕主人想执行 /x <参数>」 (`agent_text`); the
            Agent does it through its own normal path (Agent J's settings writer, the phone's signature, an approval card)
            and says what it did. Any other name Claude Code reports (the owner's custom commands, a built-in this table does
            not know yet) goes this way too. ADR-A71 is unchanged — bridge ≤ session: nothing reaches the harness that the session's
            own permission flow would not see, and nothing is turned on behind the owner's back.
- `none`  — cannot be done from a web page at all (/exit, /login, /vim …): one sentence says why (`none_note`).

Sources: Claude Code reports its own list in the stream-json `system/init` (`slash_commands`, the skills taken out) — before
the first turn a built-in table stands in; Codex and OpenCode: their documented TUI commands. Names Agent J already answers
(slash.WHITELIST + ALIAS, AGENT_CMDS, /goal, the friend commands, /reload, /reset) are left out — the page shows Agent J's
one-tap entry for them (Agent J first, 「同名只显示一次」). Skills stay in `skills`.
"""
from __future__ import annotations

import re

from . import slash

CMD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$")
MAX_NATIVE = 120
# Agent J's own names (menu.py imports the friend commands from here would be circular: listed by name)
OWN = set(slash.WHITELIST) | set(slash.ALIAS) | set(slash.AGENT_CMDS) | {"goal", "add-friend", "my-agent-id", "reload",
                                                                         "reset", "customize", "meeting-notes"}

# (name, zh, en). Claude Code: code.claude.com/docs/en/slash-commands; Codex: developers.openai.com/codex/cli/slash-commands;
# OpenCode: opencode.ai/docs/tui — 2026-10-20.
_CLAUDE_BASE = (
    ("init", "生成本项目的 CLAUDE.md", "Write this project's CLAUDE.md"),
    ("review", "审查一个 PR", "Review a pull request"),
    ("security-review", "对当前改动做安全审查", "Security review of the pending changes"),
    ("pr-comments", "读取 PR 的评论", "Fetch the comments of a pull request"),
    ("release-notes", "Claude Code 更新说明", "Claude Code release notes"),
    ("recap", "回顾这次会话", "Recap this session"),
    ("insights", "会话使用洞察", "Session insights"),
)
_CLAUDE_DESC = {name: (zh, en) for name, zh, en in _CLAUDE_BASE} | {
    "advisor": ("请一个顾问模型给意见", "Ask an advisor model"), "effort": ("调整思考力度", "Set the reasoning effort"),
    "fast": ("切换快速模式", "Toggle fast mode"), "rename": ("给会话改名", "Rename the session"),
    "ultrareview": ("云端多 Agent 深度审查（计费）", "Deep multi-agent cloud review (billed)"),
    "output-style": ("换输出风格", "Change the output style"), "autocompact": ("自动压缩设置", "Auto-compact setting"),
    "reload-skills": ("重新加载技能", "Reload skills"), "reload-plugins": ("重新加载插件", "Reload plugins"),
    "focus": ("专注模式", "Focus mode"), "import": ("导入", "Import"), "extra-usage": ("额外用量", "Extra usage"),
    "usage-credits": ("用量额度", "Usage credits"), "list-agents": ("列出子 Agent", "List subagents"),
}
_CLAUDE_TUI = (
    ("config", "设置（终端界面）", "Settings (terminal screen)"), ("agents", "管理子 Agent", "Manage subagents"),
    ("mcp", "管理 MCP 服务器", "Manage MCP servers"), ("permissions", "权限规则", "Permission rules"),
    ("hooks", "管理 hooks", "Manage hooks"), ("memory", "编辑记忆文件", "Edit memory files"),
    ("resume", "恢复旧会话", "Resume a conversation"), ("rewind", "回退到之前的一步", "Rewind the conversation"),
    ("add-dir", "添加工作目录", "Add a working directory"), ("export", "导出对话", "Export the conversation"),
    ("doctor", "检查安装", "Check the installation"), ("login", "登录", "Log in"), ("logout", "退出登录", "Log out"),
    ("ide", "连接 IDE", "Connect an IDE"), ("terminal-setup", "终端换行键设置", "Terminal newline setup"),
    ("vim", "Vim 编辑模式", "Vim editing mode"), ("statusline", "状态栏设置", "Status line setup"),
    ("plugin", "管理插件", "Manage plugins"), ("todos", "任务清单", "Todo list"), ("bug", "报告问题", "Report a bug"),
    ("exit", "退出", "Exit"),
)
_CODEX_TUI = (
    ("init", "生成 AGENTS.md", "Create AGENTS.md"), ("review", "审查当前改动", "Review the current changes"),
    ("approvals", "审批方式", "Approval mode"), ("permissions", "权限", "Permissions"),
    ("resume", "恢复旧会话", "Resume a session"), ("fork", "分叉会话", "Fork the session"),
    ("diff", "看改动 diff", "Show the diff"), ("mention", "引用文件", "Mention a file"), ("mcp", "MCP 工具", "MCP tools"),
    ("skills", "技能", "Skills"), ("personality", "说话风格", "Personality"), ("experimental", "实验功能", "Experimental features"),
    ("statusline", "状态栏", "Status line"), ("feedback", "反馈给 OpenAI", "Feedback to OpenAI"),
    ("logout", "退出登录", "Log out"), ("quit", "退出", "Quit"), ("exit", "退出", "Exit"),
)
_OPENCODE_TUI = (
    ("init", "生成 AGENTS.md", "Create AGENTS.md"), ("review", "审查改动", "Review changes"),
    ("sessions", "会话列表", "Sessions"), ("undo", "撤销上一步", "Undo the last step"), ("redo", "重做", "Redo"),
    ("share", "分享会话", "Share the session"), ("unshare", "取消分享", "Unshare"), ("export", "导出对话", "Export"),
    ("details", "工具详情开关", "Tool details"), ("thinking", "思考过程开关", "Thinking blocks"),
    ("editor", "外部编辑器", "External editor"), ("themes", "主题", "Themes"), ("connect", "连接服务商", "Connect a provider"),
    ("exit", "退出", "Exit"),
)
TUI = {"claude": _CLAUDE_TUI, "codex": _CODEX_TUI, "opencode": _OPENCODE_TUI}
_TUI_DESC = {n: (zh, en) for n, zh, en in _CLAUDE_TUI}
# Claude Code's prompt commands: headless Claude Code runs them itself and they change no setting → straight through
RUN = {"claude": frozenset(n for n, _, _ in _CLAUDE_BASE)}
# Claude Code built-ins the Agent does on the owner's behalf (they change settings / state, or show a terminal screen)
_CLAUDE_AGENT = frozenset(_CLAUDE_DESC) - RUN["claude"] | {n for n, _, _ in _CLAUDE_TUI}
_NONE_ALL = {
    "exit": ("网页里不用退出：要停下这一轮用 /stop，要新对话用 /clear", "No need to exit on the web: /stop ends this turn, /clear starts anew"),
    "login": ("登录要在电脑上的终端里做（账号凭据不经过手机）", "Log in at the computer's terminal (credentials never pass through the phone)"),
    "vim": ("Vim 编辑模式只在终端里有；网页有全屏编辑器（Ctrl+G）", "Vim mode is a terminal thing; the web has the full-screen editor (Ctrl+G)"),
}
_NONE_ALL |= {"quit": _NONE_ALL["exit"], "logout": _NONE_ALL["login"]}
NONE = {
    "claude": _NONE_ALL | {
        "ide": ("连接 IDE 只对电脑上的终端有意义", "Connecting an IDE only means something in the computer's terminal"),
        "terminal-setup": ("这是电脑终端的换行键设置；网页里 Shift+Enter 就是换行", "That sets up the computer's terminal; on the web Shift+Enter is a new line"),
        "ultrareview": ("云端深度审查要主人在电脑终端里亲自启动（计费）", "The billed cloud review is started by the owner at the computer's terminal"),
        "bug": ("向 Anthropic 报问题会附上对话记录，请在电脑终端里亲自发", "A bug report to Anthropic carries the conversation; send it yourself at the terminal"),
        "heapdump": ("内存转储会把登录凭据写到磁盘，网页里不做", "A heap dump writes login tokens to disk; not from the web"),
        "design-consent": ("授权访问要主人在电脑终端里亲自确认", "Granting access is confirmed by the owner at the computer's terminal"),
        "workflow-launch-exec": ("这是 Claude Code 内部用的命令，网页里不做", "An internal Claude Code command; not from the web"),
    },
    "codex": _NONE_ALL | {
        "mention": ("网页里直接在消息里写文件路径，或用附件", "On the web, write the file's path in the message, or attach it"),
        "feedback": ("给 OpenAI 的反馈会附上对话记录，请在电脑终端里亲自发", "Feedback to OpenAI carries the conversation; send it yourself at the terminal"),
    },
    "opencode": _NONE_ALL | {
        "editor": ("网页自带全屏编辑器（Ctrl+G）", "The web has its own full-screen editor (Ctrl+G)"),
        "themes": ("网页的外观在设置里换，或说「换个主题」", "Change the web's look in Settings, or say \"change the theme\""),
        "details": ("这是终端的显示开关，网页不适用", "A terminal display switch; not on the web"),
        "thinking": ("这是终端的显示开关，网页不适用", "A terminal display switch; not on the web"),
        "connect": ("连接服务商要在电脑上做（密钥不经过手机），或用 Agent J 的服务商设置", "Connect a provider at the computer (keys never pass through the phone), or in Agent J's provider settings"),
    },
}
HARNESS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def _kind(kind):
    return kind if kind in TUI else ("opencode" if isinstance(kind, str) and kind.startswith("opencode") else kind)


def _reported(init) -> set[str]:
    rep = (init or {}).get("slash_commands")
    skills = (init or {}).get("skills")
    return {n for n in rep if isinstance(n, str)} - ({x for x in skills if isinstance(x, str)} if isinstance(skills, list) else set()) \
        if isinstance(rep, list) else set()


def route(kind, name, init=None):
    """"run" | "agent" | "none" for a typed `/name` of this harness, or None (not one of its commands: as before — a skill
    passes through, anything else is refused)."""
    kind = _kind(kind)
    if kind not in TUI or not isinstance(name, str) or name in OWN:
        return None
    if name in NONE[kind]:
        return "none"
    if kind == "claude":
        if name in RUN["claude"]:
            return "run"
        if name in _CLAUDE_AGENT or name in _reported(init):   # settings / screens, the owner's custom commands, the unknown
            return "agent"
        return None
    return "agent" if name in {n for n, _, _ in TUI[kind]} else None


def none_note(kind, name, lang="zh") -> str:
    pair = NONE.get(_kind(kind), {}).get(name)
    return _d(pair, lang) if pair else ""


def agent_text(text, kind, init=None, lang: str = "zh") -> str | None:
    """The message the main Agent gets for a typed `agent` command (None for anything else). Never starts with "/"."""
    p = slash.parse(text)
    if not p or route(kind, p[0], init) != "agent":
        return None
    name, arg = p
    h = HARNESS.get(_kind(kind), str(kind))
    if lang == "en":
        return (f"[Menu command /{name}] The owner sent {h}'s /{name}" + (f" {arg}" if arg else "") + " from the phone. That "
                f"command needs {h}'s terminal screen or changes a setting, so do what the owner means yourself, through your "
                "own normal path (Agent J's settings: the agentj-config skill — a permission change waits for the owner's "
                "signature on the phone; anything else that needs the owner's yes goes through the approval card), then say "
                "in a sentence or two what you did and how it went. Never go around an approval, and never edit a config file "
                "to give yourself more permissions.")
    return (f"〔菜单命令 /{name}〕主人从手机发来 {h} 的 /{name}" + (f" {arg}" if arg else "") + f"。这条命令要用 {h} 的终端界面或会改设置，"
            "请你代为完成主人的意图：走你自己的正常路径（Agent J 的设置用 agentj-config 技能，权限类改动要等主人在手机上签名确认；其他需要"
            "主人同意的走审批卡），办完用一两句话告诉主人做了什么、结果如何。不要绕过审批，也不要直接改配置文件给自己加权限。")


def _d(pair: tuple[str, str] | None, lang: str) -> str:
    return "" if not pair else pair[1] if lang == "en" else pair[0]


def for_menu(kind: str | None, init: dict | None, skills: list | None, lang: str = "zh") -> list[dict]:
    """The `native` rows for this harness (see the module docstring). `init` = Claude Code's stream-json init (or None),
    `skills` = what the answer already lists as skills (never repeated here)."""
    kind = _kind(kind)
    if kind not in TUI:
        return []
    skip = OWN | {s for s in skills or [] if isinstance(s, str)}
    out: list[dict] = []
    seen: set[str] = set()

    def add(name, desc):
        if not isinstance(name, str) or not CMD_RE.match(name) or name.startswith("_") or name in skip or name in seen:
            return
        state = route(kind, name, init) or "agent"
        why = none_note(kind, name, lang) if state == "none" else ""
        seen.add(name)
        out.append({"cmd": "/" + name, "desc": " · ".join(x for x in (desc, why) if x), "state": state})

    if kind == "claude":
        reported = (init or {}).get("slash_commands")
        for n in reported if isinstance(reported, list) else [n for n, _, _ in _CLAUDE_BASE]:   # the CLI's own list
            add(n, _d(_CLAUDE_DESC.get(n) or _TUI_DESC.get(n), lang))
    for n, zh, en in TUI[kind]:
        add(n, en if lang == "en" else zh)
    return out[:MAX_NATIVE]
