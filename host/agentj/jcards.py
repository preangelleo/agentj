"""P360 Agent J Cards: cards 6, 7, 8 … in the phone's logo menu, below the five system cards (spec
agentjarvis/docs/specs/AGENTJ-CARDS-SPEC.md, PROTOCOL §34).

A card is one folder with `card.json` (schema cardpacks/card.schema.json, also https://agentj.app/schema/agentj-card-1.json).
Phase 1 opens **template cards only**: the manifest names one of the built-in templates (`notes`, `checklist`), one data
file and a few buttons; there is no card code at all — the page draws the template from the data this computer reads.
Everything is computed here; the phone is the remote control:

- data: one file under <state>/agentperm/cards-data/<id>/ (a plain name, no path, no symlink, no credential-looking name,
  ≤ maxBytes); read on request, notes and items pass privacy.redact, a checklist is parsed and bounded. serve writes it
  only once: the sample, created (never over data) when an official card is first switched on.
- buttons only ever become a **chat message the owner confirms on the phone** (or a draft in the message box): the host
  renders each prompt from the manifest with whitelisted variables, quoted and cut, behind a 「来自卡片」 label. A card
  has no other way to act — no command, no approval, no network, no credential (the manifest has no field for one).
- switches: <state>/agentperm/cards/state.json `{"on": {id: bool}}` — every card defaults on (P393 #129: official cards too,
  palette is the explicit default-off exception (#178). The owner turns them off; a switch the owner never touched follows the default), the five system pages are never switches.

Storage: official cards ship read-only in cardpacks/<id>/ (card.json + sample/); the owner's cards are applied to
<state>/agentperm/cards/active/<id>/ (the previous version of the same id sleeps in dormant/<when>-<id>/, newest
DORMANT_KEEP kept); data survives upgrades, apply and remove. agentperm = the part of the state dir a fenced Agent sees,
so the owner's Agent runs export → validate → apply and edits the data file itself.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import time
import unicodedata
import urllib.parse

PKG = pathlib.Path(__file__).resolve().parent / "cardpacks"
SCHEMA_PATH = PKG / "card.schema.json"
SCHEMA_ID = "https://agentj.app/schema/agentj-card-1.json"
CAP = "jcards1"
PHONE_TYPES = ("jcard_list", "jcard_get", "jcard_set", "jcard_note", "jcard_color", "jcard_cal", "jpalette", "jcard_call", "jcard_files", "jcard_file", "jcard_perm")
SUBCOMMANDS = ("list", "validate", "apply", "on", "off", "remove", "export", "open", "note", "todo", "cal", "automation", "files", "obsidian", "pack", "rollback", "schema")
OFFICIAL = ("notes", "todo", "automation", "calendar", "official-palette", "files", "obsidian")
DEFAULT_OFF = ("official-palette", "files", "obsidian")
PLANNED = ()
SYSTEM = ("chat", "workflows", "skills", "memory", "agentj")   # the five system cards of the logo menu
PAGE_IDS = ("cards", "settings")         # the card wall and Settings: page names, never a card id (card/<id> must stay unambiguous)
RESERVED_PREFIX = ("agentj-", "official-")
TEMPLATES = {"notes": ("md", "txt"), "checklist": ("json",)}
ICONS = ("note", "checklist", "calendar", "folder", "board", "book", "star", "bolt")
# the deck card's fill (the system cards' five, from Leo's closed palette) → the ink the cards CSS writes on it. A card names
# the fill, never the ink.
# P393 #132 (Leo 10-19: 「背景色是可以改的，但字体颜色统一就是 Lotion。然后 logo 那个 icon 也是 Lotion」): the ink is Lotion on
# every fill, exactly like the five system cards of the logo menu (same fills, same Lotion words).
ACCENTS = {"emerald-oasis": ("#629887", "#faf9f5"), "windstorm": ("#6a9bcc", "#faf9f5"), "magic-carpet": ("#827dbd", "#faf9f5"),
           "peachy-feeling": ("#d97757", "#faf9f5"), "noir": ("#2c2b26", "#faf9f5")}
ID_RE = re.compile(r"[a-z][a-z0-9-]{1,31}")
FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
VERSION_RE = re.compile(r"\d{1,4}(\.\d{1,4}){0,2}")
# a data file never carries a credential-looking name (the same rule as PROTOCOL §30.1 for the five pages)
DENY_RE = re.compile(r"(credential|secret|token|password|passwd|private|\.env|\.pem|\.key|id_rsa|id_ed25519|keychain|vault)", re.I)
VAR_RE = re.compile(r"\{\{\s*([a-z.]+)\s*\}\}")
CARD_VARS = ("card.name", "data.path")
ITEM_VARS = CARD_VARS + ("item.id", "item.text", "item.done")
MANIFEST_MAX = 16 * 1024
DATA_DEFAULT, DATA_MAX = 64 * 1024, 256 * 1024
NAME_MAX = {"zh": 12, "en": 24}
DESC_MAX = {"zh": 60, "en": 120}
LABEL_MAX = {"zh": 8, "en": 20}
PROMPT_MAX = 2000
ACTIONS_MAX, ITEM_ACTIONS_MAX = 4, 2
ITEMS_MAX, ITEM_TEXT_MAX, ITEM_VAR_MAX = 200, 300, 200
USER_MAX = 100                            # the deck has no count limit; this only bounds one list message
DORMANT_KEEP = 20
TOP_KEYS = {"$schema", "schemaVersion", "id", "version", "name", "description", "author", "icon", "accent", "template",
            "data", "actions", "itemActions", "plus", "links", "anchors", "server"}
REQUIRED = ("schemaVersion", "id", "version", "name", "description", "icon", "accent", "template", "data")
LABEL = {"zh": "〔来自卡片：{name}〕", "en": "[From card: {name}] "}
DONE_WORD = {"zh": ("未完成", "已完成"), "en": ("open", "done")}
# The header's + on a card's page (Leo 10-17: 「每个 Card 指定这个加号指到哪个 Skill」): `plus` names one slash skill; the phone
# puts `/<plus> ` in the message box (nothing is sent). No `plus` → the card skill itself.
PLUS_DEFAULT = "agentj-cards"
SKILL_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
# `links` (≤ LINKS_MAX): `href` = a web page in a new tab (http / https only, no user:password@, no credential-looking
# parameter; the phone shows the address before it opens it), `jlink` = an Agent J internal link — one address form for
# everything the phone can open (spec §「内部链接与导航」): skill/<name> · workflow/<folder> · doc/[<folder>/]<name>.md ·
# card/<id>[#<anchor>] · page/<page>. A card's address is its id (unique on this computer; the name may repeat).
LINKS_MAX, URL_MAX = 6, 512
OPEN_GAP = 2.0                            # serve pushes at most one jcard_open per 2 s (an Agent calls it with no approval)
PAGES = SYSTEM + ("cards",)
ANCHOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
CARD_ADDR_RE = re.compile(r"card/([a-z][a-z0-9-]{1,31})(?:#(" + ANCHOR_RE.pattern + r"))?")
JLINK_RE = re.compile(r"(skill/[a-z0-9][a-z0-9-]{1,63}|workflow/[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
                      r"|doc/(?:[A-Za-z0-9][A-Za-z0-9._-]{0,79}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.md"
                      r"|card/[a-z][a-z0-9-]{1,31}(?:#" + ANCHOR_RE.pattern + r")?"
                      r"|page/(?:" + "|".join(PAGES) + "))")
# `anchors` (≤ ANCHORS_MAX): places inside the card that card/<id>#<anchor> scrolls to. Every card has top · actions · links
# (BUILTIN_ANCHORS); a checklist's item ids are anchors by themselves; a manifest adds named ones: {id, label, block} with
# block one of ANCHOR_BLOCKS, or — notes only — {id, label, heading}: the first Markdown heading with that text.
ANCHORS_MAX, HEADING_MAX = 20, 80
BUILTIN_ANCHORS = ("top", "actions", "links")
ANCHOR_BLOCKS = ("top", "content", "actions", "links")
CRED_PARAM_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|apikey|access[_-]?key|auth|session|signature|sig|credential)", re.I)
CRED_NAMES = {"key", "pwd", "pass", "code", "access", "bearer", "jwt", "sid", "otp", "pin", "private"}   # whole-name matches
NAME_EQ_RE = re.compile(r"([A-Za-z0-9_.-]+)=")             # every name= in the address: query, fragment, ;path parameters
BOT_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")     # Telegram / webhook style token in the path
# privacy.redact's secret rules (key prefixes, JWT, bearer …) also judge the whole address; these kinds alone are fine in a link
BENIGN_HITS = {"high_entropy", "private_ip", "email", "phone", "hostname", "username"}


def _m(code: str, path: str, zh: str, en: str) -> dict:
    return {"code": code, "path": path, "zh": zh, "en": en}


# ---------------------------------------------------------------- validate
def _bad_char(c: str) -> bool:
    """Names and labels: no control / invisible format characters (RTL overrides, zero-width), no brackets that could forge
    or close the 〔来自卡片〕 label or the 「…」 quoting of data."""
    return ord(c) < 32 or c in "〔〕「」\u2028\u2029" or unicodedata.category(c) == "Cf"


def _text2(doc, key: str, limits: dict, errs: list, path: str) -> None:
    v = doc.get(key)
    if not isinstance(v, dict) or set(v) - {"zh", "en"} or not v:
        errs.append(_m("text", path, f"{path} 要写成 {{\"zh\": …, \"en\": …}}", f"{path} must be {{\"zh\": …, \"en\": …}}"))
        return
    for lang in ("zh", "en"):
        s = v.get(lang)
        if not isinstance(s, str) or not s.strip() or len(s) > limits[lang] or any(_bad_char(c) for c in s):
            errs.append(_m("text", f"{path}.{lang}", f"{path}.{lang} 必填，单行，≤ {limits[lang]} 个字，不含 〔〕「」 和隐形控制字符",
                           f"{path}.{lang} is required, one line, ≤ {limits[lang]} characters, no 〔〕「」 or invisible control characters"))


def _prompt_ok(p, allowed: tuple, errs: list, path: str) -> None:
    variants = p if isinstance(p, dict) else {"": p}
    if isinstance(p, dict) and (set(p) - {"zh", "en"} or not p):
        errs.append(_m("prompt", path, f"{path} 是一段文字，或 {{\"zh\", \"en\"}}", f"{path} is a string or {{\"zh\", \"en\"}}"))
        return
    for lang, s in variants.items():
        where = f"{path}.{lang}" if lang else path
        if not isinstance(s, str) or not s.strip() or len(s) > PROMPT_MAX:
            errs.append(_m("prompt", where, f"{where} 必填，≤ {PROMPT_MAX} 个字", f"{where} is required, ≤ {PROMPT_MAX} characters"))
            continue
        for v in VAR_RE.findall(s):
            if v not in allowed:
                errs.append(_m("var", where, f"{where} 不能用变量 {{{{{v}}}}}；只能用 " + "、".join(allowed),
                               f"{where} cannot use {{{{{v}}}}}; allowed: " + ", ".join(allowed)))
        if "{{" in VAR_RE.sub("", s) or "}}" in VAR_RE.sub("", s):
            errs.append(_m("var", where, f"{where} 有没写完的 {{{{ }}}}", f"{where} has an unfinished {{{{ }}}}"))


def _actions(doc, key: str, most: int, allowed: tuple, errs: list) -> None:
    from . import jcards_server
    acts, routes = doc.get(key, []), jcards_server.route_ids(doc)
    if not isinstance(acts, list) or len(acts) > most:
        errs.append(_m("actions", key, f"{key} 是列表，最多 {most} 个", f"{key} is a list of at most {most}"))
        return
    seen = set()
    for i, a in enumerate(acts):
        p = f"{key}[{i}]"
        if not isinstance(a, dict) or set(a) - {"id", "label", "prompt", "draft", "call"} or ("prompt" in a) == ("call" in a):
            errs.append(_m("actions", p, f"{p} 只能有 id、label，加 prompt（可带 draft）或 call 二选一",
                           f"{p} has id, label and either prompt (with draft?) or call"))
            continue
        if not isinstance(a.get("id"), str) or not ID_RE.fullmatch(a["id"]) or a["id"] in seen:
            errs.append(_m("actions", f"{p}.id", f"{p}.id 小写字母开头、只含小写字母数字和 -，不重复",
                           f"{p}.id: lower-case letters, digits and -, starting with a letter, unique"))
        if isinstance(a.get("id"), str):
            seen.add(a["id"])
        _text2(a, "label", LABEL_MAX, errs, f"{p}.label")
        if "call" in a:      # P432: a button that asks the card's own server (declared route, POST, the owner confirms)
            if a["call"] not in routes or "draft" in a:
                errs.append(_m("call", f"{p}.call", f"{p}.call 写 server.routes 里的一个 id（不和 draft 一起用）",
                               f"{p}.call names one id of server.routes (not with draft)"))
            continue
        _prompt_ok(a.get("prompt"), allowed, errs, f"{p}.prompt")
        if "draft" in a and type(a["draft"]) is not bool:
            errs.append(_m("actions", f"{p}.draft", f"{p}.draft 是 true / false", f"{p}.draft is true / false"))


def href_ok(url) -> bool:
    """A card's web link: http(s), a host, no credentials in it (user:password@, a token-like parameter), one plain line."""
    if not isinstance(url, str) or not 0 < len(url) <= URL_MAX or any(ord(c) <= 32 or ord(c) == 127 or c in "\\\"<>`" or unicodedata.category(c) == "Cf" for c in url):
        return False
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not host or u.username is not None or u.password is not None or "@" in u.netloc:
        return False
    if not u.netloc.isascii():          # a look-alike host (Cyrillic а in аpple.com): write the punycode xn--… form
        return False
    for k in NAME_EQ_RE.findall(urllib.parse.unquote(url)):
        if CRED_PARAM_RE.search(k) or k.lower() in CRED_NAMES:
            return False
    if BOT_TOKEN_RE.search(url):
        return False
    from . import privacy
    return not (set(privacy.redact_with_report(urllib.parse.unquote(url))[1]) - BENIGN_HITS)


def _links(doc, errs: list) -> None:
    links = doc.get("links", [])
    if not isinstance(links, list) or len(links) > LINKS_MAX:
        errs.append(_m("links", "links", f"links 是列表，最多 {LINKS_MAX} 个", f"links is a list of at most {LINKS_MAX}"))
        return
    seen = set()
    for i, a in enumerate(links):
        p = f"links[{i}]"
        if not isinstance(a, dict) or set(a) - {"id", "label", "href", "jlink"} or ("href" in a) == ("jlink" in a):
            errs.append(_m("links", p, f"{p} 是 {{id, label, href}}（外链）或 {{id, label, jlink}}（内链），二选一",
                           f"{p} is {{id, label, href}} (a web link) or {{id, label, jlink}} (an internal link), one of the two"))
            continue
        if not isinstance(a.get("id"), str) or not ID_RE.fullmatch(a["id"]) or a["id"] in seen:
            errs.append(_m("links", f"{p}.id", f"{p}.id 小写字母开头、只含小写字母数字和 -，不重复",
                           f"{p}.id: lower-case letters, digits and -, starting with a letter, unique"))
        if isinstance(a.get("id"), str):
            seen.add(a["id"])
        _text2(a, "label", LABEL_MAX, errs, f"{p}.label")
        if "href" in a and not href_ok(a["href"]):
            errs.append(_m("href", f"{p}.href", f"{p}.href 只收 http:// 或 https:// 网址（≤ {URL_MAX} 字，英文域名，中文域名写 xn-- 形式），不带 用户名:密码@、"
                           "不带 key / token / password 之类的参数，也不能含任何像密钥的字符串",
                           f"{p}.href takes an http:// or https:// address (≤ {URL_MAX}, ASCII host — punycode for others) with no user:password@, "
                           "no key / token / password parameter and nothing that looks like a key"))
        if "jlink" in a and not (isinstance(a["jlink"], str) and JLINK_RE.fullmatch(a["jlink"])):
            errs.append(_m("jlink", f"{p}.jlink", f"{p}.jlink 写成 skill/<技能> · workflow/<文件夹> · doc/[<文件夹>/]<文件>.md · card/<id>[#<位置>] · page/" + "|".join(PAGES),
                           f"{p}.jlink is skill/<name> · workflow/<folder> · doc/[<folder>/]<file>.md · card/<id>[#<anchor>] · page/" + "|".join(PAGES)))


def _anchors(doc, tpl, errs: list) -> None:
    anchors = doc.get("anchors", [])
    if not isinstance(anchors, list) or len(anchors) > ANCHORS_MAX:
        errs.append(_m("anchors", "anchors", f"anchors 是列表，最多 {ANCHORS_MAX} 个", f"anchors is a list of at most {ANCHORS_MAX}"))
        return
    seen = set(BUILTIN_ANCHORS)
    for i, a in enumerate(anchors):
        p = f"anchors[{i}]"
        if not isinstance(a, dict) or set(a) - {"id", "label", "block", "heading"} or ("block" in a) == ("heading" in a):
            errs.append(_m("anchors", p, f"{p} 是 {{id, label, block}} 或 {{id, label, heading}}，二选一",
                           f"{p} is {{id, label, block}} or {{id, label, heading}}, one of the two"))
            continue
        if not isinstance(a.get("id"), str) or not ANCHOR_RE.fullmatch(a["id"]) or a["id"] in seen:
            errs.append(_m("anchors", f"{p}.id", f"{p}.id：字母数字 _ -，不重复，不用 " + " · ".join(BUILTIN_ANCHORS) + "（每张卡自带）",
                           f"{p}.id: letters, digits, _ -, unique, not " + " · ".join(BUILTIN_ANCHORS) + " (every card has them)"))
        if isinstance(a.get("id"), str):
            seen.add(a["id"])
        _text2(a, "label", LABEL_MAX, errs, f"{p}.label")
        if "block" in a and a["block"] not in ANCHOR_BLOCKS:
            errs.append(_m("anchors", f"{p}.block", f"{p}.block 是 " + " · ".join(ANCHOR_BLOCKS), f"{p}.block is one of " + " · ".join(ANCHOR_BLOCKS)))
        if "heading" in a and (tpl != "notes" or not isinstance(a["heading"], str) or not 0 < len(a["heading"]) <= HEADING_MAX
                               or any(_bad_char(c) for c in a["heading"])):
            errs.append(_m("anchors", f"{p}.heading", f"{p}.heading 只用于 notes 模板：一行标题文字（≤ {HEADING_MAX} 字）",
                           f"{p}.heading is for the notes template only: one line of heading text (≤ {HEADING_MAX})"))


def check(doc, official: bool = False) -> list:
    """Every rule of card.json (the schema and the ones it cannot say). → list of {code, path, zh, en}; [] = passes."""
    errs: list = []
    if not isinstance(doc, dict):
        return [_m("json", "", "card.json 必须是一个 JSON 对象", "card.json must be a JSON object")]
    for k in sorted(set(doc) - TOP_KEYS):
        errs.append(_m("unknown", k, f"不认识的字段 {k}（卡片不能带别的东西，尤其是代码、网址和密钥）",
                       f"unknown field {k} (a card carries nothing else — no code, URLs or keys)"))
    for k in REQUIRED:
        if k not in doc:
            errs.append(_m("required", k, f"缺少 {k}", f"{k} is missing"))
    if "$schema" in doc and doc["$schema"] != SCHEMA_ID:
        errs.append(_m("schema", "$schema", f"$schema 应为 {SCHEMA_ID}", f"$schema should be {SCHEMA_ID}"))
    if doc.get("schemaVersion", 1) != 1:
        errs.append(_m("schema", "schemaVersion", "这一版只认 schemaVersion 1", "this version reads schemaVersion 1 only"))
    cid = doc.get("id")
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        errs.append(_m("id", "id", "id：2–32 个小写字母、数字或 -，字母开头；装上后永久不变",
                       "id: 2–32 lower-case letters, digits or -, starting with a letter; permanent once applied"))
    elif not official and (cid in OFFICIAL + PLANNED + SYSTEM + PAGE_IDS or cid.startswith(RESERVED_PREFIX)):
        errs.append(_m("reserved", "id", f"id「{cid}」留给官方卡片，换一个", f"id \"{cid}\" is reserved for official cards — pick another"))
    if not isinstance(doc.get("version"), str) or not VERSION_RE.fullmatch(doc.get("version", "")):
        errs.append(_m("version", "version", "version 写成 1、1.2 或 1.2.3", "version: 1, 1.2 or 1.2.3"))
    if "name" in doc:
        _text2(doc, "name", NAME_MAX, errs, "name")
    if "description" in doc:
        _text2(doc, "description", DESC_MAX, errs, "description")
    if "author" in doc and (not isinstance(doc["author"], str) or not 0 < len(doc["author"]) <= 40):
        errs.append(_m("author", "author", "author ≤ 40 个字", "author: ≤ 40 characters"))
    if "icon" in doc and not (isinstance(doc["icon"], str) and doc["icon"] in ICONS):
        errs.append(_m("icon", "icon", "icon 只能是 " + "、".join(ICONS), "icon is one of " + ", ".join(ICONS)))
    if "accent" in doc and not (isinstance(doc["accent"], str) and doc["accent"] in ACCENTS):
        errs.append(_m("accent", "accent", "accent 只能用调色板里的 " + "、".join(ACCENTS) + "（不收自定义颜色）",
                       "accent is one of the palette's " + ", ".join(ACCENTS) + " (no custom colours)"))
    tpl = doc.get("template") if isinstance(doc.get("template"), str) else None
    if "template" in doc and tpl not in TEMPLATES:
        errs.append(_m("template", "template", "第一期只开放模板卡：template 是 " + "、".join(TEMPLATES),
                       "phase 1 opens template cards only: template is one of " + ", ".join(TEMPLATES)))
    data = doc.get("data")
    from . import jcards_server
    jcards_server.check(doc, errs)
    if "data" in doc:
        if not isinstance(data, dict) or set(data) - {"file", "get", "maxBytes"} or ("file" in data) == ("get" in data):
            errs.append(_m("data", "data", "data 写成 {\"file\": \"文件名\"} 或 {\"get\": \"/路径\"}（本机 server），二选一，可加 \"maxBytes\"",
                           "data is {\"file\": \"name\"} or {\"get\": \"/path\"} (the card's server), one of the two, with \"maxBytes\"?"))
        else:
            f = data.get("file")
            if "get" in data:   # P432: the card's own server on this computer answers GET http://127.0.0.1:<port><get>
                if not isinstance(doc.get("server"), dict) or not jcards_server.path_ok(data["get"]):
                    errs.append(_m("data", "data.get", "data.get 是一个路径（/开头，不带 ? 和 ..），而且要写 server",
                                   "data.get is a path (starting with /, no ? or ..) and needs server"))
            elif not isinstance(f, str) or not FILE_RE.fullmatch(f) or ".." in f:
                errs.append(_m("data", "data.file", "data.file 只写一个文件名（不带目录），字母数字和 . _ -",
                               "data.file is one file name (no folder): letters, digits, . _ -"))
            elif DENY_RE.search(f):
                errs.append(_m("credential", "data.file", "data.file 的名字像密钥文件，卡片不能读", "data.file looks like a credential file; a card never reads one"))
            elif tpl in TEMPLATES and f.rsplit(".", 1)[-1].lower() not in TEMPLATES[tpl]:
                errs.append(_m("data", "data.file", f"模板 {tpl} 读 ." + " / .".join(TEMPLATES[tpl]) + " 文件",
                               f"template {tpl} reads a ." + " / .".join(TEMPLATES[tpl]) + " file"))
            mb = data.get("maxBytes", DATA_DEFAULT)
            if type(mb) is not int or not 1024 <= mb <= DATA_MAX:
                errs.append(_m("data", "data.maxBytes", f"data.maxBytes 在 1024 到 {DATA_MAX} 之间", f"data.maxBytes is 1024 … {DATA_MAX}"))
    _actions(doc, "actions", ACTIONS_MAX, CARD_VARS, errs)
    if "itemActions" in doc and tpl != "checklist":
        errs.append(_m("actions", "itemActions", "只有 checklist 模板有逐条按钮", "only the checklist template has item buttons"))
    _actions(doc, "itemActions", ITEM_ACTIONS_MAX, ITEM_VARS, errs)
    if "plus" in doc and not (isinstance(doc["plus"], str) and SKILL_RE.fullmatch(doc["plus"])):
        errs.append(_m("plus", "plus", "plus 写一个技能名（不带 /），如 agentj-add-note", "plus is one skill name (no /), e.g. agentj-add-note"))
    _links(doc, errs)
    _anchors(doc, tpl, errs)
    return errs


def parse(raw: bytes | None, official: bool = False) -> tuple[dict | None, list]:
    """card.json bytes → (doc, errors)."""
    if raw is None:
        return None, [_m("size", "card.json", f"card.json 读不了：不是普通文件、是链接，或超过 {MANIFEST_MAX // 1024} KiB",
                         f"card.json unreadable: not a regular file, a link, or over {MANIFEST_MAX // 1024} KiB")]
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        return None, [_m("json", "card.json", f"card.json 不是合法 JSON：{e}", f"card.json is not valid JSON: {e}")]
    return doc, check(doc, official)


def load(folder) -> tuple[dict | None, list]:
    """card.json of a folder → (doc, errors). Also refuses anything but card.json, README.md and sample/<data file>."""
    folder = pathlib.Path(folder)
    p = folder / "card.json"
    if not os.path.lexists(p):
        return None, [_m("missing", "card.json", f"{folder} 里没有 card.json", f"no card.json in {folder}")]
    doc, errs = parse(_read_file(p, MANIFEST_MAX), official=folder.resolve().parent == PKG.resolve())
    if doc is None:
        return None, errs
    want = doc.get("data", {}).get("file") if isinstance(doc, dict) and isinstance(doc.get("data"), dict) else None
    for x in sorted(folder.iterdir()):
        if x.name in ("card.json", "README.md"):
            continue
        if x.name == "sample" and x.is_dir() and not x.is_symlink():
            for y in x.iterdir():
                if y.name != want or y.is_symlink() or not y.is_file() or y.stat().st_size > DATA_MAX:
                    errs.append(_m("extra", f"sample/{y.name}", f"sample/ 里只能放数据文件 {want}（样例，≤ {DATA_MAX // 1024} KiB）",
                                   f"sample/ holds only the data file {want} (a sample, ≤ {DATA_MAX // 1024} KiB)"))
            continue
        errs.append(_m("extra", x.name, f"{x.name}：模板卡只有 card.json、README.md 和 sample/（第一期不收代码和网页）",
                       f"{x.name}: a template card is card.json, README.md and sample/ only (phase 1 takes no code or pages)"))
    return doc, errs


# ---------------------------------------------------------------- storage
# Everything under <state>/agentperm/ is reachable by a fenced Agent, which may plant symlinks there; serve runs outside the
# fence. So every read / write below walks agentperm/<…> one directory at a time with O_NOFOLLOW (a link at any level →
# refused), opens files with O_NOFOLLOW | O_NONBLOCK and checks fstat (a FIFO or device never blocks or leaks), and creates
# with O_EXCL. Moves (apply / remove / prune) are renames relative to those directory fds and only touch names this module made.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRFLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
DORMANT_RE = re.compile(r"\d{8}-\d{6}-[a-z][a-z0-9-]{1,31}(-\d+)?")


def home(root) -> pathlib.Path:
    return pathlib.Path(root) / "agentperm" / "cards"


def data_dir(root, cid: str) -> pathlib.Path:
    return pathlib.Path(root) / "agentperm" / "cards-data" / cid


@contextlib.contextmanager
def _dirfd(root, parts: tuple, create: bool = False):
    """An fd of <root>/agentperm/<parts…>, opened level by level without following a link at any level (OSError if one is)."""
    base = pathlib.Path(root) / "agentperm"
    if create:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    fds = [os.open(base, _DIRFLAGS)]
    try:
        for part in parts:
            try:
                fds.append(os.open(part, _DIRFLAGS, dir_fd=fds[-1]))
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fds[-1])
                fds.append(os.open(part, _DIRFLAGS, dir_fd=fds[-1]))
        yield fds[-1]
    finally:
        for fd in fds:
            os.close(fd)


def _read_fd(fd: int, most: int) -> tuple[bytes | None, bool]:
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None, False
        raw = fh.read(most + 1)
    return raw[:most], len(raw) > most


def _read_file(path: pathlib.Path, most: int) -> bytes | None:
    """A regular file outside agentperm (the card folder given to apply, the package's samples), never through a link."""
    try:
        raw, cut = _read_fd(os.open(path, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0)), most)
    except OSError:
        return None
    return None if cut else raw


def _write_json(root, parts: tuple, name: str, obj) -> None:
    body = (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    with _dirfd(root, parts, create=True) as d:
        tmp = f".{name}.{os.getpid()}.tmp"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=d)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=d)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, name, src_dir_fd=d, dst_dir_fd=d)


def switches(root) -> dict:
    try:
        with _dirfd(root, ("cards",)) as d:
            raw, _cut = _read_fd(os.open("state.json", os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=d), 64 * 1024)
        on = json.loads((raw or b"").decode("utf-8")).get("on", {})
        return {k: v for k, v in on.items() if isinstance(k, str) and type(v) is bool}
    except (OSError, ValueError, AttributeError):
        return {}


def _save_switches(root, s: dict) -> None:
    _write_json(root, ("cards",), "state.json", {"on": s})


def set_on(root, cid: str, on: bool, seen: str | None = None) -> bool:
    c = find(root, cid)
    if not c:
        return False
    if on and not c["official"]:              # P432: a plaza card goes on only with the digest of the summary the owner read
        from . import jcards_pkg
        if jcards_pkg.source(root, cid):
            if seen != jcards_pkg.digest(c["doc"]):
                return False
            if not jcards_pkg.approve(root, cid, c["doc"]):   # outside agentperm: the switch alone never turns a plaza card on
                return False
    if on and c["official"] and c["doc"]["data"].get("file"):   # first switch-on: the sample data, so the card is never blank
        _seed(root, cid, c["doc"], _read_file(c["dir"] / "sample" / c["doc"]["data"]["file"], DATA_MAX))
    s = switches(root)
    s[cid] = bool(on)
    try:
        _save_switches(root, s)
    except OSError:                           # a link on the way (or a full disk): not saved, and said so
        return False
    return True


def official_dir(cid: str) -> pathlib.Path:
    return PKG / cid


def cards(root) -> list:
    """Every card this computer knows, in deck order: official (notes, todo) then the owner's own by apply time.
    → [{id, doc, official, on, dir}] — a broken user card is left out (the deck never shows half a card)."""
    s, out = switches(root), []
    for cid in OFFICIAL:
        doc, errs = load(official_dir(cid))
        if doc and not errs:
            on = s.get(cid, cid not in DEFAULT_OFF)
            if on and cid not in s:               # on by default, never switched: the sample once, so it is never blank
                _seed(root, cid, doc, _read_file(official_dir(cid) / "sample" / doc["data"]["file"], DATA_MAX))
            out.append({"id": cid, "doc": doc, "official": True, "on": on, "dir": official_dir(cid)})
    mine = []
    try:
        with _dirfd(root, ("cards", "active")) as afd:
            for name in os.listdir(afd):
                if not ID_RE.fullmatch(name):
                    continue
                try:                                  # one odd card is skipped, never the whole list
                    cfd = os.open(name, _DIRFLAGS, dir_fd=afd)
                    try:
                        when = os.fstat(cfd).st_mtime
                        raw, cut = _read_fd(os.open("card.json", os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=cfd), MANIFEST_MAX)
                    finally:
                        os.close(cfd)
                    doc, errs = parse(None if cut else raw)
                    if doc and not errs and doc["id"] == name:
                        mine.append((when, name, doc))
                except (OSError, ValueError, TypeError):
                    continue
    except OSError:
        pass
    from . import jcards_pkg
    plaza = jcards_pkg.sources(root) if mine else {}
    for _t, cid, doc in sorted(mine)[:USER_MAX]:
        on = s.get(cid, True) and (cid not in plaza or jcards_pkg.approved(root, cid, doc, plaza))   # P432: plaza = approved digest
        out.append({"id": cid, "doc": doc, "official": False, "on": on, "dir": home(root) / "active" / cid})
    return out


def find(root, cid) -> dict | None:
    return next((c for c in cards(root) if c["id"] == cid), None) if isinstance(cid, str) else None


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _sleep_name(dfd: int, cid: str) -> str:
    name, n = f"{_stamp()}-{cid}", 1
    while True:
        try:
            os.lstat(name, dir_fd=dfd)
        except FileNotFoundError:
            return name
        n += 1
        name = f"{_stamp()}-{cid}-{n}"


def _prune(dfd: int) -> None:
    """Keep the newest DORMANT_KEEP — only folders this module named (never a link), removed relative to the dormant fd."""
    names = []
    for n in os.listdir(dfd):
        try:
            if DORMANT_RE.fullmatch(n) and stat.S_ISDIR(os.lstat(n, dir_fd=dfd).st_mode):
                names.append(n)
        except FileNotFoundError:
            continue
    for n in sorted(names, reverse=True)[DORMANT_KEEP:]:
        shutil.rmtree(n, dir_fd=dfd, ignore_errors=True)


def _put_to_sleep(afd: int, dfd: int, cid: str) -> str | None:
    """active/<cid> (whatever it is — a planted link moves as a link, nothing is followed) → dormant/<when>-<cid>."""
    try:
        os.lstat(cid, dir_fd=afd)
    except FileNotFoundError:
        return None
    name = _sleep_name(dfd, cid)
    os.replace(cid, name, src_dir_fd=afd, dst_dir_fd=dfd)
    _prune(dfd)
    return name


def _write_at(dfd: int, name: str, body: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=dfd)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)


def _seed(root, cid: str, doc: dict, sample: bytes | None) -> None:
    """First time only: the sample data goes into the card's data folder — created, never over the owner's data."""
    if not sample:
        return
    try:
        with _dirfd(root, ("cards-data", cid), create=True) as d:
            fd = os.open(doc["data"]["file"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=d)
            with os.fdopen(fd, "wb") as fh:
                fh.write(sample)
    except OSError:                           # exists already (the owner's data), or a link somewhere on the way
        pass


def _refused(path: str) -> dict:
    return {"ok": False, "errors": [_m("link", path, f"{path} 是符号链接，卡片目录里不收链接", f"{path} is a symbolic link; card folders take none")]}


def apply(root, folder, on: bool = True) -> dict:
    """validate → copy into active/<id>/ (the old version of the same id sleeps) → switched on. → {ok, id?, errors?, dormant?}
    The bytes that are checked are the bytes that are installed; every step below agentperm/cards is relative to a directory
    fd opened without following links, so a link planted under agentperm can never redirect a write or a move."""
    folder = pathlib.Path(folder).expanduser()
    doc, errs = load(folder)
    if errs or doc is None:
        return {"ok": False, "errors": errs}
    if folder.resolve().parent == PKG.resolve():
        return {"ok": False, "errors": [_m("official", "", "官方卡片不用 apply，在手机 设置 → Cards 打开就行（或 agentj card on <id>）",
                                           "official cards are not applied: switch them on in Settings → Cards (or agentj card on <id>)")]}
    manifest = _read_file(folder / "card.json", MANIFEST_MAX)
    doc2, errs2 = parse(manifest)
    sample_path = folder / "sample" / (doc["data"].get("file") or "-")
    sample = _read_file(sample_path, DATA_MAX) if doc["data"].get("file") and os.path.lexists(sample_path) else None
    if errs2 or doc2 != doc or (doc["data"].get("file") and os.path.lexists(sample_path) and sample is None):
        return {"ok": False, "errors": errs2 or [_m("changed", "", "文件在检查时变了（或样例是链接），再试一次",
                                                  "the folder changed while checking (or the sample is a link); try again")]}
    cid, stage = doc["id"], f".stage-{os.getpid()}"
    try:
        with _dirfd(root, ("cards",), create=True) as hfd, _dirfd(root, ("cards", "active"), create=True) as afd, \
                _dirfd(root, ("cards", "dormant"), create=True) as dfd:
            shutil.rmtree(stage, dir_fd=hfd, ignore_errors=True)
            os.mkdir(stage, 0o700, dir_fd=hfd)
            sfd = os.open(stage, _DIRFLAGS, dir_fd=hfd)
            try:
                _write_at(sfd, "card.json", manifest)
                if sample is not None:
                    os.mkdir("sample", 0o700, dir_fd=sfd)
                    xfd = os.open("sample", _DIRFLAGS, dir_fd=sfd)
                    try:
                        _write_at(xfd, doc["data"]["file"], sample)
                    finally:
                        os.close(xfd)
            finally:
                os.close(sfd)
            dormant = _put_to_sleep(afd, dfd, cid)
            os.replace(stage, cid, src_dir_fd=hfd, dst_dir_fd=afd)
    except OSError as e:
        return {"ok": False, "errors": [_m("link", "agentperm/cards", f"装不上：{e.strerror}（卡片目录里有链接或权限不对）",
                                           f"cannot apply: {e.strerror} (a link or wrong permissions under the cards folder)")]}
    _seed(root, cid, doc, sample)
    s = switches(root)
    s[cid] = bool(on)                         # P432: a plaza card arrives switched off
    with contextlib.suppress(OSError):        # a user card is on by default anyway (switches() falls back to on)
        _save_switches(root, s)
    return {"ok": True, "id": cid, **({"dormant": dormant} if dormant else {})}


def remove(root, cid: str) -> dict:
    """The owner's card goes to sleep (dormant/); its data folder stays. Official cards are switched off instead."""
    c = find(root, cid)
    if not c:
        return {"ok": False, "why": "not_found"}
    if c["official"]:
        set_on(root, cid, False)
        return {"ok": True, "id": cid, "off": True}
    try:
        with _dirfd(root, ("cards", "active")) as afd, _dirfd(root, ("cards", "dormant"), create=True) as dfd:
            name = _put_to_sleep(afd, dfd, cid)
    except OSError:
        return {"ok": False, "why": "link"}
    return {"ok": True, "id": cid, "dormant": name} if name else {"ok": False, "why": "not_found"}


def export(cid: str, dest) -> dict:
    """An official card (card.json + sample/) into a new, empty folder — the starting point for the owner's own card."""
    if cid not in OFFICIAL:
        return {"ok": False, "why": "not_official"}
    dest = pathlib.Path(dest).expanduser()
    if dest.exists() and any(dest.iterdir()):
        return {"ok": False, "why": "not_empty"}
    shutil.copytree(official_dir(cid), dest, dirs_exist_ok=True)
    return {"ok": True, "dest": str(dest)}


# ---------------------------------------------------------------- what the phone gets (§34)
def _pick(v, lang: str) -> str:
    if isinstance(v, dict):
        return v.get(lang) or v.get("en") or v.get("zh") or ""
    return str(v)


def _short(p: pathlib.Path) -> str:
    try:
        return "~/" + str(p.relative_to(pathlib.Path.home()))
    except ValueError:
        return str(p)


def _clean(s: str, most: int) -> str:
    s = "".join(" " if ord(c) < 32 or c in "\u2028\u2029" else c for c in str(s) if unicodedata.category(c) != "Cf")
    s = re.sub(r"\s+", " ", s).strip().replace("「", "“").replace("」", "”").replace("{{", "{ {").replace("}}", "} }")
    return s if len(s) <= most else s[:most - 1] + "…"


def render(card: dict, tpl, lang: str, root, item: dict | None = None) -> str:
    """One button's chat message: the 「来自卡片」 label + the manifest's text with its variables filled in.
    Item text is data (it may come from anyone): quoted in 「」, one line, cut — never markup, never a second variable."""
    doc = card["doc"]
    name = _pick(doc["name"], lang)
    vals = {"card.name": name, "data.path": data_where(root, card)}
    if item is not None:
        vals.update({"item.id": item["id"], "item.text": "「" + _clean(item["text"], ITEM_VAR_MAX) + "」",
                     "item.done": DONE_WORD[lang if lang in DONE_WORD else "en"][int(item["done"])]})
    body = VAR_RE.sub(lambda m: vals.get(m.group(1), ""), _pick(tpl, lang))
    return body[:PROMPT_MAX] if body.startswith("/") else (LABEL.get(lang, LABEL["en"]).format(name=name) + body)[:PROMPT_MAX + 64]


def data_where(root, card: dict) -> str:
    """Where a card's data lives: its file (~/…) or, P432, its server's GET address."""
    d = card["doc"]["data"]
    if "get" in d:
        from . import jcards_server
        return jcards_server.where(card["doc"], d["get"]).split(" ", 1)[1]
    return _short(data_dir(root, card["id"]) / d["file"])


def _read(root, card: dict) -> tuple[bytes | None, str | None, bool]:
    """→ (bytes, problem, cut). Only the card's own data folder, level by level, no link anywhere, a regular file only."""
    doc = card["doc"]
    f, most = doc["data"].get("file"), doc["data"].get("maxBytes", DATA_DEFAULT)
    if not isinstance(f, str) or not FILE_RE.fullmatch(f) or DENY_RE.search(f) or not ID_RE.fullmatch(card["id"]):
        return None, "refused", False
    try:
        with _dirfd(root, ("cards-data", card["id"])) as d:
            raw, cut = _read_fd(os.open(f, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=d), most)
    except FileNotFoundError:
        return None, "empty", False
    except OSError as e:
        return None, ("refused" if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK) else "unreadable"), False
    if raw is None:
        return None, "refused", False
    return raw, None, cut


def _items(raw: bytes) -> tuple[list, str | None]:
    """A checklist, bounded; item text is redacted BEFORE it is cut (a cut key might no longer look like one)."""
    from . import privacy
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], "bad_data"
    rows = doc.get("items") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return [], "bad_data"
    out = []
    for i, r in enumerate(rows[:ITEMS_MAX]):
        if isinstance(r, str):
            r = {"text": r}
        if not isinstance(r, dict) or not isinstance(r.get("text"), str) or not r["text"].strip():
            continue
        rid = r.get("id") if isinstance(r.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", r["id"]) else f"i{i + 1}"
        out.append({"id": rid, "text": _clean(privacy.redact(r["text"]), ITEM_TEXT_MAX), "done": r.get("done") is True})
    return out, None


def phone_list(root, lang: str = "zh") -> dict:
    """§34 jcard_items: what Settings → Cards and the deck draw. Names / descriptions in both languages (the phone picks)."""
    from . import jcards_pkg
    rows, plaza = [], jcards_pkg.sources(root)
    for c in cards(root):
        d = c["doc"]
        rows.append({"id": c["id"], "name": d["name"], "desc": d["description"], "icon": d["icon"], "accent": d["accent"],
                     "template": d["template"], "official": c["official"], "on": c["on"], "plus": d.get("plus") or PLUS_DEFAULT,
                     **({"src": "plaza"} if not c["official"] and c["id"] in plaza else {})})
    from . import jcards_data
    return {"cards": rows, "colors": jcards_data.colors(root)}


def _act(c: dict, a: dict, lang: str, root, item: dict | None = None) -> dict:
    """One button as the phone gets it: the finished chat message — or, P432, a call of the card's server (where it goes)."""
    if "call" in a:
        from . import jcards_server
        path = next(r["path"] for r in c["doc"]["server"]["routes"] if r["id"] == a["call"])
        return {"id": a["id"], "label": _pick(a["label"], lang), "call": True, "where": jcards_server.where(c["doc"], path, "POST")}
    return {"id": a["id"], "label": _pick(a["label"], lang), "prompt": render(c, a["prompt"], lang, root, item),
            **({"draft": True} if a.get("draft") else {})}


def phone_get(root, cid, lang: str = "zh", agent_cfg=None) -> dict:
    """§34 jcard_get: one card's data, read now, plus every button with its finished chat message."""
    from . import privacy
    lang = lang if lang in ("zh", "en") else "zh"
    c = find(root, cid)
    if not c:
        return {"ok": False, "why": "not_found"}
    if not c["on"]:
        return {"ok": False, "why": "off"}
    d = c["doc"]
    if cid == "calendar":
        from . import jcards_cal
        return {**jcards_cal.listing(root), "template": "calendar", "plus": "agentj-calendar", "actions": [], "links": [], "anchors": []}
    if cid == "official-palette":
        return {"ok": True, "id": cid, "template": "palette", "actions": [], "links": [], "anchors": []}
    if cid == "obsidian" and c["official"]:   # P432: the picked notes of one Obsidian vault (jcards_obsidian)
        from . import jcards_obsidian
        return jcards_obsidian.phone(root, c, lang)
    if cid == "files" and c["official"]:   # P432: the listing comes with jcard_files (jcards_files), one folder at a time
        return {"ok": True, "id": cid, "template": "files", "actions": [], "links": [], "anchors": [], "plus": d.get("plus") or PLUS_DEFAULT}
    if cid == "automation":
        from . import jcards_data
        return {"ok": True, "id": cid, "template": "automation", "items": jcards_data.automations(root, agent_cfg, lang), "actions": [], "links": [], "anchors": [], "plus": "agentj-automation"}
    res = {"ok": True, "id": c["id"], "template": d["template"], "path": data_where(root, c),
           "actions": [_act(c, a, lang, root) for a in d.get("actions", [])],
           "plus": d.get("plus") or PLUS_DEFAULT,
           "links": [{"id": a["id"], "label": _pick(a["label"], lang), **({"href": a["href"]} if "href" in a else {"jlink": a["jlink"]})}
                     for a in d.get("links", [])],
           "anchors": [{"id": a["id"], "label": _pick(a["label"], lang), **({"block": a["block"]} if "block" in a else {"heading": a["heading"]})}
                       for a in d.get("anchors", [])]}
    if "get" in d["data"]:              # P432: the card's own server answers instead of a data file
        from . import jcards_server
        raw, problem, cut = jcards_server.get_data(c)
    else:
        raw, problem, cut = _read(root, c)
    if problem:
        res["problem"] = problem
    if d["template"] == "notes":
        text = raw.decode("utf-8", "replace") if raw else ""
        if cut:   # a cut credential may lose its recognizable shape; omit the entire trailing token
            whitespace = list(re.finditer(r"\s", text))
            text = text[:whitespace[-1].start()] if whitespace else ""
        res["text"] = privacy.redact(text, owner=True)
        from . import jcards_data
        res.update(jcards_data.note_snapshot(text.encode("utf-8") if cut else (raw or b"")))
        if cut or "get" in d["data"]:     # a server's text is its own: shown, never edited here
            res["entries"] = [{**e, "editable": False} for e in res["entries"]]
    else:
        if c["id"] == "todo" and c["official"]:   # P394: owner, status, start / end, marks, the card's order
            from . import jcards_todo
            items, bad = jcards_todo.phone_items(raw, lang)
            from .jcards_data import _revision
            res["revision"] = _revision(raw or b"")
            res["todo"] = {"counts": {w: sum(1 for i in items if w == "all" or i["owner"] == w) for w in ("all", "agent", "owner")}}
        else:
            items, bad = _items(raw) if raw else ([], None)
        if bad:
            res["problem"] = bad
        acts = d.get("itemActions", [])
        safe = [{**it, "id": it["id"] if privacy.redact(it["id"]) == it["id"] else f"i{n + 1}"}
                for n, it in enumerate(items)]   # redacted before anything uses it: the display AND the button message
        res["items"] = [{**it, "acts": [_act(c, a, lang, root, it) for a in acts]} for it in safe]
    if cut:
        res["cut"] = True
    return res


def open_target(root, cid, at=None) -> dict:
    """`agentj card open` (§34 jcard_open): where the phone should go — a system page (chat · workflows · skills · memory ·
    agentj · cards) or a switched-on card, optionally one place in it. Navigation only: nothing is read or changed."""
    if at is not None and not (isinstance(at, str) and ANCHOR_RE.fullmatch(at)):
        return {"ok": False, "why": "bad_anchor"}
    if cid in PAGES:
        return {"ok": True, "id": cid, **({"at": at} if at else {})} if not at else {"ok": False, "why": "bad_anchor"}
    c = find(root, cid) if isinstance(cid, str) and ID_RE.fullmatch(cid) else None
    if not c:
        return {"ok": False, "why": "not_found"}
    if not c["on"]:
        return {"ok": False, "why": "off"}
    return {"ok": True, "id": c["id"], **({"at": at} if at else {})}


def address(cid: str, at=None) -> str:
    """A card's internal address: card/<id> or card/<id>#<anchor> (what an Agent writes in a reply; the phone links it)."""
    return f"card/{cid}" + (f"#{at}" if at else "")


# ---------------------------------------------------------------- CLI: agentj card …
def _say(e: dict) -> str:
    return f"  ✗ {e['path'] or '·'}：{e['zh']} / {e['en']}"


OPEN_WHY = {"not_found": ("没有这张卡（agentj card list）", "no such card (agentj card list)"),
            "off": ("这张卡关着，先 agentj card on <id>（问过主人）", "the card is off — agentj card on <id> first (ask the owner)"),
            "bad_anchor": ("位置写错了：字母数字 _ -，系统页没有位置", "bad anchor: letters, digits, _ -; system pages have none"),
            "no_serve": ("agentj serve 没在运行", "agentj serve is not running"),
            "no_phone": ("现在没有手机连着，下次主人打开手机再说", "no phone is connected right now"),
            "busy": ("serve 没有回答，稍后再试", "serve did not answer; try again"),
            "too_soon": ("刚打开过，隔两秒再开", "opened a moment ago; wait two seconds"),
            "shape": ("这个 Agent J 版本还不会打开卡片（先 agentj update）", "this Agent J does not open cards yet (agentj update)")}


def open_card(root, cid, at=None) -> dict:
    """Asks serve on the Agent-facing elevate.sock (control.sock is hidden inside the Agent fence) to tell every connected
    phone to go there; serve checks the target (a switched-on card or a page) — here only the shape is checked."""
    if not (isinstance(cid, str) and (cid in PAGES or ID_RE.fullmatch(cid))):
        return {"ok": False, "why": "not_found"}
    if at is not None and (cid in PAGES or not ANCHOR_RE.fullmatch(at)):
        return {"ok": False, "why": "bad_anchor"}
    from . import elevate
    from .names import elevate_sock
    from .state import State
    st = State(pathlib.Path(root))
    res = elevate.client_request(st, {"t": "jcard_open", "id": cid, **({"at": at} if at else {})}, timeout=15, path=elevate_sock(st))
    if res.get("result") == "unavailable":
        return {"ok": False, "why": "no_serve"}
    if not res.get("ok"):
        return {"ok": False, "why": str(res.get("why") or "busy")[:40]}
    if not res.get("delivered"):
        return {"ok": False, "why": "no_phone"}
    return {"ok": True, "id": cid, **({"at": at} if at else {}), "delivered": res["delivered"]}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentj card", description="Agent J Cards：手机 logo 菜单里第 6 张起的卡片 / the phone's own cards")
    ps = p.add_subparsers(dest="cmd", required=True)
    x = ps.add_parser("list", help="所有卡片和开关 / every card and its switch")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    for name, h in (("validate", "检查一个卡片文件夹 / check a card folder"), ("apply", "检查后装上，同 id 的旧版休眠 / check, then put it on the phone (the old version sleeps)")):
        x = ps.add_parser(name, help=h)
        x.add_argument("folder", help="卡片文件夹（card.json 所在）/ the card folder (where card.json is)")
        x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    for name, h in (("on", "打开一张卡 / switch a card on"), ("off", "关掉一张卡 / switch a card off"),
                    ("remove", "拿掉自己的卡，数据留着 / take your card off (its data stays)")):
        x = ps.add_parser(name, help=h)
        x.add_argument("id", help="卡片 id（agentj card list）/ a card id (agentj card list)")
        x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("export", help="把官方卡复制出来当模板 / copy an official card to start from")
    x.add_argument("which", help="官方卡 / an official card: " + " · ".join(OFFICIAL))
    x.add_argument("dest", help="新的空文件夹 / a new, empty folder")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("open", help="让手机打开一张卡（或卡里的一个位置），只导航 / make the phone open a card (or a place in it); navigation only")
    x.add_argument("id", help="卡片 id，或系统页 / a card id, or a system page: " + " · ".join(PAGES))
    x.add_argument("--at", metavar="ANCHOR", help="卡里的位置（anchors、待办条目 id、top / actions / links）/ a place in the card")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("note", help="Manage notebook entries atomically")
    x.add_argument("op", choices=("list", "add", "edit", "delete"))
    x.add_argument("--id", default="notes")
    x.add_argument("--entry")
    x.add_argument("--revision")
    x.add_argument("--text")
    x.add_argument("--json", action="store_true")
    # P394 (#130): the To-do card kept by the Agent and the owner together (jcards_todo; flags spelled out here for the docs checker)
    x = ps.add_parser("todo", help="待办卡：加、改、标完成 / 取消，列出现在该处理的 / the To-do card: add, change, close, list what is due")
    x.add_argument("op", choices=["add", "edit", "done", "cancel", "reopen", "start", "toggle", "list", "due"],
                   help="add 新加 · edit 改字段 · done 完成 · cancel 取消 · reopen 撤回 · start 开始做 · toggle 完成/撤回 · list 全部 · due 现在该处理的")
    x.add_argument("--entry", metavar="ID", help="哪一条（t3）/ which item (t3)")
    x.add_argument("--text", help="标题，一行 / the title, one line")
    x.add_argument("--owner", choices=["agent", "owner"], help="责任人：agent = Agent J，owner = 主人 / who is responsible")
    x.add_argument("--start", metavar="WHEN", help="开始：2026-10-20[T09:00] · +2h · after:t3 · 或一句话条件 / a time, after:<id> or a condition")
    x.add_argument("--end", metavar="WHEN", help="截止：同上 / deadline: the same three kinds")
    x.add_argument("--status", choices=["pending", "active", "done", "cancelled"], help="状态 / status")
    x.add_argument("--by", choices=["agent", "owner"], help="谁记的（add 默认 agent）/ who wrote it down (add: agent)")
    x.add_argument("--source", help="来源：哪段对话 / which conversation it came from")
    x.add_argument("--note", help="备注 / a note")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    from . import jcards_cal
    jcards_cal.add_parser(ps)
    x = ps.add_parser("automation", help="Read-only automation catalogue with stable names")
    x.add_argument("op", choices=("list",))
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("files", help="文件管理卡能浏览的文件夹（工作目录 + 主人加的）/ the folders the Files card may browse")
    x.add_argument("op", choices=["list", "add", "remove"], help="list 列出 · add 加一个 · remove 去掉 / list · add · remove")
    x.add_argument("folder", nargs="?", help="文件夹 / a folder (add / remove)")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("obsidian", help="Obsidian 卡：找 vault、设 vault、选要显示的笔记 / the Obsidian card: find / set the vault, pick notes")
    x.add_argument("op", choices=["find", "list", "vault", "pick", "unpick"], help="find 列出 Obsidian 知道的 vault · list 当前设置 · vault 设 vault · pick 加笔记 · unpick 去掉")
    x.add_argument("args", nargs="*", help="vault：一个文件夹；pick / unpick：vault 里的 .md 路径 / a folder, or note paths")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("pack", help="把自己的卡打成广场包文件夹（只带样例，先过上线检查）/ make a plaza package folder from your card (sample only, checked)")
    x.add_argument("id", help="卡片 id / a card id")
    x.add_argument("dest", help="新的空文件夹 / a new, empty folder")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("rollback", help="换回这张卡休眠中的上一版（关着，看过摘要再开）/ bring the previous version back (switched off)")
    x.add_argument("id", help="卡片 id / a card id")
    x.add_argument("--json", action="store_true", help="机器可读输出 / machine-readable output")
    x = ps.add_parser("schema", help="打印 card.json 的 JSON Schema / print the schema")
    x.add_argument("--path", action="store_true", help="只打印文件路径 / only its path")
    return p


def command(args: list) -> int:
    from .state import state_dir
    a = parser().parse_args(args)
    root, js = state_dir(), getattr(a, "json", False)
    out = (lambda o: print(json.dumps(o, ensure_ascii=False))) if js else None
    if a.cmd == "automation":
        from . import jcards_data
        from .state import State
        print(json.dumps({"ok": True, "items": jcards_data.automations(root, State(root).agent_config())}, ensure_ascii=False))
        return 0
    if a.cmd == "cal":
        from . import jcards_cal
        return jcards_cal.command(a, root)
    if a.cmd in ("pack", "rollback"):
        from . import jcards_pkg
        r = jcards_pkg.pack(root, a.id, a.dest) if a.cmd == "pack" else jcards_pkg.rollback(root, a.id)
        if js:
            print(json.dumps(r, ensure_ascii=False))
        elif r["ok"]:
            print(f"✓ → {r['dest']}（下一步 agentj plaza publish {r['dest']}）/ next: agentj plaza publish" if a.cmd == "pack"
                  else f"✓ {a.id} v{r['version']} 回来了，关着：主人在手机上看过摘要再打开 / back, switched off")
        else:
            print(f"✗ {r['why']}" + "".join("\n  " + p for p in r.get("problems", [])))
        return 0 if r["ok"] else 1
    if a.cmd == "obsidian":
        from . import jcards_obsidian
        return jcards_obsidian.command(a, root)
    if a.cmd == "files":
        from . import jcards_files
        from .state import State
        if a.op != "list" and not a.folder:
            print("✗ 写一个文件夹 / name a folder", file=sys.stderr)
            return 2
        r = jcards_files.change_roots(root, a.op, a.folder)
        if a.op == "list":
            r["browse"] = [x["label"] for x in jcards_files.roots(root, State(root).agent_config())]
        print(json.dumps(r, ensure_ascii=False) if js else ("✓ " + " · ".join(r.get("browse") or r.get("roots") or ["—"])) if r["ok"] else f"✗ {r['why']}")
        return 0 if r["ok"] else 1
    if a.cmd == "todo":
        from . import jcards_todo
        return jcards_todo.command(a, root)
    if a.cmd == "note":
        from . import jcards_data
        r = phone_get(root, a.id) if a.op == "list" else jcards_data.note_change(root, a.id, a.op, a.entry, a.revision, a.text)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if a.cmd == "schema":
        print(str(SCHEMA_PATH) if a.path else SCHEMA_PATH.read_text(encoding="utf-8"), end="\n" if a.path else "")
        return 0
    if a.cmd == "open":
        r = open_card(root, a.id, a.at)
        if out:
            out(r)
        elif r["ok"]:
            print(f"✓ 手机已打开 {address(a.id, a.at) if a.id not in PAGES else a.id}（{r['delivered']} 台）/ the phone opened it ({r['delivered']} device(s))")
        else:
            print(f"✗ {r['why']}：" + OPEN_WHY.get(r["why"], ("", ""))[0] + " / " + OPEN_WHY.get(r["why"], ("", ""))[1])
        return 0 if r["ok"] else 1
    if a.cmd == "list":
        rows = [{"id": c["id"], "name": c["doc"]["name"], "template": c["doc"]["template"], "official": c["official"],
                 "on": c["on"], "data": data_where(root, c)} for c in cards(root)]
        if out:
            out({"cards": rows})
        else:
            for r in rows:
                print(f"{'●' if r['on'] else '○'} {r['id']:<20} {r['name']['zh']} / {r['name']['en']}"
                      f"  [{r['template']}{' · 官方 official' if r['official'] else ''}]  {r['data']}")
        return 0
    if a.cmd == "validate":
        doc, errs = load(pathlib.Path(a.folder).expanduser())
        if out:
            out({"ok": not errs, "errors": errs})
        elif errs:
            print("\n".join(_say(e) for e in errs))
            print(f"✗ {len(errs)} 项没通过，改好再 validate / {len(errs)} failed — fix and validate again")
        else:
            print(f"✓ {doc['id']}：全部通过，可以 apply / all checks passed — ready to apply")
        return 0 if not errs else 1
    if a.cmd == "apply":
        r = apply(root, a.folder)
        if out:
            out(r)
        elif r["ok"]:
            print(f"✓ {r['id']} 已装上，手机点左上角 logo 就能看到 / on the phone now (tap the logo)"
                  + (f"；旧版休眠为 {r['dormant']} / the old version sleeps as {r['dormant']}" if r.get("dormant") else ""))
            print(f"  数据文件 / data file: {_short(data_dir(root, r['id']))}/")
        else:
            print("\n".join(_say(e) for e in r["errors"]))
            print("✗ 没装上，什么都没改 / not applied; nothing was changed")
        return 0 if r["ok"] else 1
    if a.cmd in ("on", "off"):
        ok = set_on(root, a.id, a.cmd == "on")
        if not ok and a.cmd == "on" and find(root, a.id):   # P432: a plaza card — the owner turns it on from the phone
            print(json.dumps({"ok": False, "id": a.id, "why": "review"}) if js else
                  "✗ 这是广场卡：主人在手机 设置 → Cards 看过权限摘要再打开 / a plaza card: the owner turns it on in Settings → Cards after reading its summary")
            return 1
        if out:
            out({"ok": ok, "id": a.id, "on": a.cmd == "on"})
        else:
            print(f"✓ {a.id}: {a.cmd}" if ok else f"✗ 没有这张卡 / no such card: {a.id}")
        return 0 if ok else 1
    if a.cmd == "remove":
        r = remove(root, a.id)
        if out:
            out(r)
        else:
            print(f"✓ {a.id} 已拿掉，数据留在原处 / removed; its data stays" if r["ok"] else f"✗ 没有这张卡 / no such card: {a.id}")
        return 0 if r["ok"] else 1
    r = export(a.which, a.dest)
    if out:
        out(r)
    else:
        print(f"✓ → {r['dest']}（改 id 和 name，再 validate）/ change id and name, then validate" if r["ok"]
              else f"✗ {r['why']}: " + (" · ".join(OFFICIAL) if r["why"] == "not_official" else a.dest))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(command(sys.argv[1:]))
