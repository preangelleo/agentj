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
    "zh": {"compact": "压缩上下文", "clear": "清空对话（可撤销）", "model": "换模型", "context": "上下文用了多少",
           "cost": "本会话花费", "usage": "套餐用量", "status": "状态", "help": "能用哪些命令", "stop": "停下这一轮"},
    "en": {"compact": "Compact the context", "clear": "Clear the conversation (undoable)", "model": "Switch model",
           "context": "How much context is used", "cost": "Cost of this session", "usage": "Plan usage", "status": "Status",
           "help": "Which commands work here", "stop": "Stop this turn"},
}
DEFAULT_GROUP = {"zh": "命令", "en": "Commands"}


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
    return [{"cmd": "/" + c, "desc": d[c], "group": DEFAULT_GROUP.get(lang, "命令")} for c in slash.WHITELIST]


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
        if isinstance(s, str) and CMD_RE.match("/" + s) and s not in slash.WHITELIST:
            sk.append({"cmd": "/" + s, "desc": ""})
    out = {"skills": sk[:200], "cmds": list(slash.WHITELIST)}
    if doc is None:
        out.update(source="default", items=defaults(lang))
        if errs:
            out["problems"] = [e[:200] for e in errs[:10]]
    else:
        out.update(source="file", items=[{k: it[k] for k in ("cmd", "desc", "group") if k in it} for it in arrange(doc["items"])])
    return out
