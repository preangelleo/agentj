"""Untrusted text → safe for this machine's terminal (and for metadata reports). Shared by serve and cloud."""
from __future__ import annotations

import re
import unicodedata

LABEL_MAX, LABEL_DIGITS = 64, 4
_UNSAFE = ("Cc", "Cf", "Cs", "Co", "Cn")
_LINEBREAK = ("Zl", "Zp")


def clean(text: str, limit: int) -> str:
    """Untrusted text from a device → safe for a terminal: drop control/format chars (ANSI, bidi), keep newlines."""
    out = []
    for ch in text[:limit]:
        if ch == "\n" or unicodedata.category(ch) not in _UNSAFE + _LINEBREAK:
            out.append(ch)
    return "".join(out)


_FFFD_LINE = re.compile(r"^[ \t]*\ufffd+[ \t]*(?:\n|$)", re.M)
_FFFD_EDGE = re.compile(r"^\s*\ufffd[\s\ufffd]*|[\s\ufffd]*\ufffd\s*$")


def drop_replacement(text: str) -> str:
    """P281: U+FFFD (the browser's 「◆?」) is never meant for the owner. A model now and then opens its turn with a text
    block that is nothing but U+FFFD (Opus 5.5 before a /switch-harness Skill call, 2026-10-14: the byte is already in
    Claude Code's own transcript), and the page kept it as the reply's first line. Lines made only of it and any at the
    very start or end go; one inside a sentence (a quote of broken text) stays."""
    if not isinstance(text, str) or "\ufffd" not in text:
        return text
    return _FFFD_LINE.sub("", _FFFD_EDGE.sub("", text))


def clean_line(text: str, limit: int) -> str:
    """One line, no control/format chars, whitespace collapsed (for short server-supplied strings we print)."""
    out = []
    for ch in text[:limit]:
        cat = unicodedata.category(ch)
        out.append(" " if cat in _UNSAFE or cat in _LINEBREAK or cat.startswith("Z") else ch)
    return " ".join("".join(out).split())


def clean_label(text: str) -> str:
    """A device-chosen label is printed on the terminal where the human types the safety code, so it must not be able
    to imitate host output: one line (no line breaks of any kind), no control/format chars, and at most 4 digits in
    total — it can never show a 6-digit code, however it is spaced."""
    out, digits = [], 0
    for ch in text[:LABEL_MAX]:
        cat = unicodedata.category(ch)
        if cat in _UNSAFE or cat in _LINEBREAK or cat.startswith("Z"):
            ch = " "
        elif cat.startswith("N"):
            digits += 1
            if digits > LABEL_DIGITS:
                continue
        out.append(ch)
    return " ".join("".join(out).split()) or "未命名设备"


def text_units(text: str) -> int:
    """Message length as PROTOCOL.md counts it: UTF-16 code units (what a browser's maxlength / String.length count)."""
    return len(text.encode("utf-16-le")) // 2


# ------------------------------------------------------------------ Agent name (A3.2 contract §1; mirror of dashboard/public/agentname.js)
AGENT_NAME_MAX = 32          # code points, after normalising
AGENT_KEY_MAX = 128          # code points of the uniqueness key
AGENT_INPUT_MAX = 512        # UTF-16 code units of raw input (JS: v.length > 512 → refused)
MACHINE_MAX = 64
STARTER_NAMES = ("助理一号", "Wren", "市场部 Agent", "内容部 Agent")   # P154: each within NEW_NAME_UNITS
# The trim set, spelled out exactly as agentname.js does (Unicode White_Space): U+0009–U+000D, U+0020, U+0085, U+00A0,
# U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F, U+3000. Not str.strip() (which also strips U+001C–U+001F) and not
# JS trim() (which strips U+FEFF): anything else at the ends is refused, not stripped.
_TRIM = frozenset("\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                  "\u2028\u2029\u202f\u205f\u3000")
_NAME_REFUSED = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")   # Co / Cn: review A32-10 (as labels)


def normalise_agent_name(text) -> str | None:
    """Trim (the set above) and collapse runs of U+0020 into one. Non-strings and inputs over 512 UTF-16 units → None.
    Nothing else is cleaned: invalid input is refused by agent_name_problem."""
    if not isinstance(text, str) or len(text.encode("utf-16-le", "surrogatepass")) // 2 > AGENT_INPUT_MAX:
        return None
    i, j = 0, len(text)
    while i < j and text[i] in _TRIM:
        i += 1
    while j > i and text[j - 1] in _TRIM:
        j -= 1
    out, prev_space = [], False
    for ch in text[i:j]:
        if ch == " " and prev_space:
            continue
        prev_space = ch == " "
        out.append(ch)
    return "".join(out)


def agent_name_key(text) -> str:
    """Uniqueness key (the Dashboard decides uniqueness; here only for the length rule and tests): NFKC, lower-cased."""
    n = normalise_agent_name(text)
    return "" if n is None else unicodedata.normalize("NFKC", n).lower()


def agent_name_problem(text) -> str | None:
    """None when `text` is acceptable after normalising, else "name_required" (None / empty) or "bad_name":
    1–32 code points, no Cc/Cf/Cs/Co/Cn/Zl/Zp, no space separator other than U+0020, key ≤ 128 code points.
    Cn follows this Python's Unicode version (3.13: 15.1): a character assigned later is refused here even where a newer
    JS engine accepts it — the host is the stricter side (a synced name is then refused, never cleaned)."""
    if text is None:
        return "name_required"
    n = normalise_agent_name(text)
    if n is None:
        return "bad_name"
    if not n:
        return "name_required"
    if len(n) > AGENT_NAME_MAX:
        return "bad_name"
    for ch in n:
        cat = unicodedata.category(ch)
        if cat in _NAME_REFUSED or (cat == "Zs" and ch != " "):
            return "bad_name"
    if len(agent_name_key(n)) > AGENT_KEY_MAX:
        return "bad_name"
    return None


# P154 追加 6: a NEW name (agentj name / agentj admin / login --seat --name) must fit the reply header on one line — the
# name is local, not unique across the internet, so keep it short. Wide (CJK, East Asian W/F) characters count 1.6 units,
# everything else 1; at most 13 units: 8 CJK characters (12.8), or 13 Latin letters / digits / hyphens / spaces. Integer
# form: 8 × wide + 5 × other ≤ 65. Names already stored, synced or bound are NOT re-checked (no forced rename on upgrade).
NEW_NAME_UNITS = 13


def agent_name_units(text: str) -> float:
    """Display units of a name: 1.6 per wide (W/F) character, 1 per any other."""
    return sum(1.6 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def new_agent_name_problem(text) -> str | None:
    """agent_name_problem, plus "name_too_long" when the normalised name is over NEW_NAME_UNITS units."""
    err = agent_name_problem(text)
    if err:
        return err
    n = normalise_agent_name(text)
    wide = sum(1 for ch in n if unicodedata.east_asian_width(ch) in ("W", "F"))
    return "name_too_long" if 8 * wide + 5 * (len(n) - wide) > 5 * NEW_NAME_UNITS else None


def is_clean_agent_name(text) -> bool:
    """Exactly what normalising gives and valid — what the host sends (the server is strict for host-sent names)."""
    return isinstance(text, str) and normalise_agent_name(text) == text and agent_name_problem(text) is None


def clean_hostname(raw) -> str | None:
    """A hostname for the report's `machine` (review A32-11): not a device label, so no digit cap (`ip-172-31-4-12` stays
    intact). Control / format / private-use / unassigned / separator characters become spaces, whitespace collapses, ≤ 64
    code points. None when nothing is left."""
    if not isinstance(raw, str):
        return None
    out = []
    for ch in raw[:256]:
        cat = unicodedata.category(ch)
        out.append(" " if cat in _UNSAFE or cat in _LINEBREAK or cat.startswith("Z") else ch)
    s = " ".join("".join(out).split())[:MACHINE_MAX].strip()
    return s or None


def machine_name() -> str | None:
    """This machine's hostname as reported to the Dashboard (`machine`), or None if unusable."""
    import socket
    try:
        return clean_hostname(socket.gethostname())
    except OSError:
        return None


def ask_yes(question: str, read=None) -> bool:
    """The one y/N question of the CLI (`agentj login`, `agentj update apply`, `agentj docs-rule --write`): only `y` / `yes`
    (any case) is a yes; Enter, anything else or end of input is a no."""
    try:
        ans = (read or input)(question)
    except EOFError:
        ans = ""
    return ans.strip().lower() in ("y", "yes")


def pick(lang, zh: str, en: str) -> str:
    """P128: one language per message (appearance.language) instead of a "中文 / English" concatenation."""
    return en if str(lang or "").lower().startswith("en") else zh


def one(lang, text: str) -> str:
    """A legacy "中文 / English" string → the half in `lang`; anything else unchanged (P128)."""
    import re
    parts = str(text).split(" / ")
    if len(parts) == 2 and re.search(r"[一-鿿]", parts[0]) and not re.search(r"[一-鿿]", parts[1]) \
            and parts[1][:1].isascii() and parts[1][:1].isalpha():
        return pick(lang, parts[0], parts[1])
    return text


_THINK_BLOCK = re.compile(r"<(think|thinking)>.*?</\1>\s*", re.S | re.I)
_THINK_CLOSE = re.compile(r"</(?:think|thinking)>\s*", re.I)
_THINK_OPEN = re.compile(r"<(?:think|thinking)>", re.I)
_THINK_ANY = re.compile(r"</?(?:think|thinking)>", re.I)


def strip_reasoning(text: str) -> str:
    """P198: a model's raw reasoning inside an Agent's reply text never reaches the phone. OpenCode with some models
    (big-pickle) hands over a text part shaped 「reasoning…</think>answer」 — the opening tag already eaten — or a whole
    <think>…</think> block. Whole blocks go; then everything up to a stray closing tag; then an unclosed opening tag and
    everything after it."""
    if not isinstance(text, str) or not _THINK_ANY.search(text):
        return text
    text = _THINK_BLOCK.sub("", text)
    close = None
    for close in _THINK_CLOSE.finditer(text):
        pass
    if close is not None:
        text = text[close.end():]
    opened = _THINK_OPEN.search(text)
    if opened is not None:
        text = text[:opened.start()]
    return text.strip()
