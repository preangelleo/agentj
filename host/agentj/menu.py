"""The phone's ≡ command menu and `/` completion as data (PROTOCOL §10.12, PROMPT-33; relay `bridge/menu.py`, schema
`contracts/menu.schema.json`, unchanged).

File: `<agent folder>/.agentj/menu.json` — in the Agent's folder on purpose: "add this skill to my menu" is something the
Agent may do, and an item only ever INSERTS text into the composer (the human still sends it). Re-read on every `menu_get`:
edit, reload the page, done. A file that fails the schema is ignored whole (the answer carries `problems`); it is read
without following links (inbox.read_file), capped at 64 KiB. The page renders it with text nodes only.

Default items (no file): the one-tap whitelist commands with their descriptions — never Leo's private skills.
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import inbox, slash

NAME = "menu.json"
VERSION = 1
MAX_ITEMS = 60
CMD_MAX = 64
DESC_MAX = 200
GROUP_MAX = 32
ORDER_MIN, ORDER_MAX = -100_000, 100_000
MAX_FILE_BYTES = 64 * 1024
CMD_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$")
ITEM_KEYS = {"cmd", "desc", "group", "order"}
TOP_KEYS = {"version", "items", "$schema", "comment"}

DEFAULT_DESC = {
    "zh": {"compact": "压缩上下文", "clear": "开新对话", "model": "换模型", "context": "上下文用了多少",
           "cost": "本会话花费", "usage": "套餐用量", "status": "状态", "help": "能用哪些命令", "stop": "停下这一轮", "update": "升级 Agent J（发送即授权）"},
    "en": {"compact": "Compact the context", "clear": "Start a new conversation", "model": "Switch model",
           "context": "How much context is used", "cost": "Cost of this session", "usage": "Plan usage", "status": "Status",
           "help": "Which commands work here", "stop": "Stop this turn", "update": "Upgrade Agent J (sending authorizes it)"},
}
DEFAULT_GROUP = {"zh": "命令", "en": "Commands"}
# P73 (ADR-A176): the two friend commands the host answers itself (friend_cmds.py); inserted like every menu item —
# `/add-friend ` waits for the ID, the page then sends the signed fr_add.
FRIEND_CMDS = ("add-friend", "my-agent-id")
FRIEND_DESC = {
    "zh": {"add-friend": "加好友：/add-friend AJ-XXXX-XXXX-XXXX-XXXX [附言]", "my-agent-id": "我的 Agent ID 和分享链接"},
    "en": {"add-friend": "Add a friend: /add-friend AJ-XXXX-XXXX-XXXX-XXXX [note]", "my-agent-id": "My Agent ID and share link"},
}
FRIEND_GROUP = {"zh": "好友", "en": "Friends"}
# P155: /switch-harness goes to the main Agent (inserted like every menu item; the owner may add words before sending).
# Also appended to an owner's own menu.json that lacks it, so the item never disappears.
AGENT_DESC = {
    "zh": {"feedback-to-agentj-father": "反馈给 Agent J Father", "switch-harness": "换底层 harness（Claude Code / Codex / OpenCode）：交给主 Agent，先检查、能用才切"},
    "en": {"feedback-to-agentj-father": "Feedback to Agent J Father", "switch-harness": "Switch the harness (Claude Code / Codex / OpenCode): your main Agent checks first, switches only if it works"},
}
AGENT_GROUP = {"zh": "主 Agent", "en": "Main Agent"}


def agent_items(lang: str = "zh") -> list[dict]:
    d = AGENT_DESC.get(lang, AGENT_DESC["zh"])
    return [{"cmd": "/" + c, "desc": d[c], "group": AGENT_GROUP.get(lang, "主 Agent")} for c in slash.AGENT_CMDS]
# P158: `/goal` — inserted (it needs the goal after it); status / stop are answered by the host, the rest goes to the Agent.
# Also added to an owner's own menu.json that lacks it, so goal mode is always one tap away.
GOAL_ITEM = {"zh": {"cmd": "/goal", "desc": "目标模式：/goal <要做成什么，可含截止时间> · /goal status · /goal stop", "group": "命令"},
             "en": {"cmd": "/goal", "desc": "Goal mode: /goal <what to finish, maybe by when> · /goal status · /goal stop",
                    "group": "Commands"}}


def _bad_text(s: str) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in s)


def validate(doc) -> list[str]:
    """Every problem with a parsed menu document; [] = valid (relay menu.validate, same rules and limits)."""
    errs = []
    if not isinstance(doc, dict):
        return ['top level must be an object {"version": 1, "items": [...]}']
    extra = set(doc) - TOP_KEYS
    if extra:
        errs.append(f"unknown top-level key(s): {sorted(extra)}")
    if doc.get("version") != VERSION:
        errs.append(f"version must be {VERSION}")
    items = doc.get("items")
    if not isinstance(items, list):
        return errs + ["items must be a list"]
    if len(items) > MAX_ITEMS:
        errs.append(f"{len(items)} items > {MAX_ITEMS}")
    seen = set()
    for i, it in enumerate(items):
        where = f"items[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{where}: must be an object")
            continue
        extra = set(it) - ITEM_KEYS
        if extra:
            errs.append(f"{where}: unknown key(s) {sorted(extra)}")
        cmd, desc = it.get("cmd"), it.get("desc")
        if not isinstance(cmd, str) or not CMD_RE.match(cmd):
            errs.append(f"{where}.cmd: must match {CMD_RE.pattern} (≤ {CMD_MAX} chars)")
        elif cmd in seen:
            errs.append(f"{where}.cmd: duplicate {cmd}")
        else:
            seen.add(cmd)
        if not isinstance(desc, str) or not desc.strip() or len(desc) > DESC_MAX or _bad_text(desc):
            errs.append(f"{where}.desc: 1–{DESC_MAX} printable characters")
        if "group" in it:
            g = it["group"]
            if not isinstance(g, str) or not g.strip() or len(g) > GROUP_MAX or _bad_text(g):
                errs.append(f"{where}.group: 1–{GROUP_MAX} printable characters")
        if "order" in it:
            o = it["order"]
            if isinstance(o, bool) or not isinstance(o, int) or not ORDER_MIN <= o <= ORDER_MAX:
                errs.append(f"{where}.order: integer in [{ORDER_MIN}, {ORDER_MAX}]")
    return errs


def arrange(items: list[dict]) -> list[dict]:
    """Stable order: by `order` (absent = its position), groups together in the order their first member appears."""
    keyed = sorted(enumerate(items), key=lambda p: (p[1].get("order", p[0]), p[0]))
    groups: dict[str, list] = {}
    for _, it in keyed:
        groups.setdefault(it.get("group", ""), []).append(it)
    return [it for g in groups.values() for it in g]


def defaults(lang: str = "zh") -> list[dict]:
    d = DEFAULT_DESC.get(lang, DEFAULT_DESC["zh"])
    fd = FRIEND_DESC.get(lang, FRIEND_DESC["zh"])
    return ([{"cmd": "/" + c, "desc": d[c], "group": DEFAULT_GROUP.get(lang, "命令")} for c in slash.WHITELIST]
            + [dict(GOAL_ITEM.get(lang, GOAL_ITEM["zh"]))]
            + [{"cmd": "/" + c, "desc": fd[c], "group": FRIEND_GROUP.get(lang, "好友")} for c in FRIEND_CMDS]
            + agent_items(lang))


def read(workdir: str | None) -> tuple[dict | None, list[str]]:
    """(document, problems). Missing → (None, []); invalid → (None, [problems])."""
    if not workdir:
        return None, []
    try:
        raw = inbox.read_file(workdir, NAME, MAX_FILE_BYTES)
    except inbox.Unsafe:
        return None, ["menu.json is a link or not a plain file of this user: ignored"]
    except OSError as e:
        return None, [f"unreadable: {type(e).__name__}"]
    if raw is None:
        return None, []
    if len(raw) > MAX_FILE_BYTES:
        return None, [f"file larger than {MAX_FILE_BYTES} bytes"]
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return None, [f"not valid JSON: {type(e).__name__}"]
    errs = validate(doc)
    return (doc if not errs else None), errs


def served(workdir: str | None, skills: list | None, lang: str = "zh") -> dict:
    """The `menu` answer minus t / r: source, items, skills (Claude Code's own; [] for Codex / OpenCode), cmds (the
    whitelist the one-tap buttons execute), problems (only when the file was rejected)."""
    doc, errs = read(workdir)
    sk = []
    for s in skills or []:
        if isinstance(s, str) and CMD_RE.match("/" + s) and s not in slash.WHITELIST + FRIEND_CMDS + slash.AGENT_CMDS and s != "goal":
            sk.append({"cmd": "/" + s, "desc": ""})
    out = {"skills": sk[:200], "cmds": list(slash.WHITELIST)}
    if doc is None:
        out.update(source="default", items=defaults(lang))
        if errs:
            out["problems"] = [e[:200] for e in errs[:10]]
    else:
        out.update(source="file", items=complete([{k: it[k] for k in ("cmd", "desc", "group") if k in it}
                                                  for it in arrange(doc["items"])], lang))
    return out


def complete(items: list[dict], lang: str = "zh") -> list[dict]:
    """An owner's own list (menu.json, or P238 `menu.items` in preferences) + `/goal` and the main-Agent items it lacks."""
    items = list(items)
    if not any(it.get("cmd") == "/goal" for it in items) and len(items) < MAX_ITEMS:
        items.append(dict(GOAL_ITEM.get(lang, GOAL_ITEM["zh"])))
    have = {it.get("cmd") for it in items}
    return items + [it for it in agent_items(lang) if it["cmd"] not in have]


def for_phone(workdir: str | None, skills: list | None, lang: str = "zh", configured: list | None = None) -> dict:
    """serve's `menu_get` answer minus t / r / upgrade. P238: owner commands set through `agentj menu add` (preferences
    `menu.items`) replace menu.json / the defaults, but never drop `/switch-harness` or `/goal` — before 0.20.3 they did,
    so an owner with one own command (/good-morning) never saw `/switch-harness` on the phone."""
    res = served(workdir, skills, lang)
    rows = [x for x in configured or [] if isinstance(x, dict) and not x.get("disabled")]
    if rows:
        res.update(source="config", items=complete([{k: v for k, v in x.items() if k in ("cmd", "desc", "group")}
                                                    for x in arrange(rows)], lang))
    return res
