"""P280: the Agent's own words after a state change (new conversation, compaction, undo, update, queue …).

All of it lives in humane.json (zh/en), never in the call sites. `{title}` is how the owner wants to be called —
`owner.address` (the P205 setting the phone's working line uses too), empty → 「董事长」 / "Chairman". A list rotates by the minute, an object picks by time of day,
so the greeting after /clear changes between morning / afternoon / evening / night. Warm and short, never a lie:
a failure is still said as a failure.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

DATA = json.loads(Path(__file__).with_name("humane.json").read_text(encoding="utf-8"))
LINES = DATA["lines"]
TITLE_MAX = 16
_FIELD = re.compile(r"\{(\w+)\}")


def _lang(lang) -> str:
    return "en" if lang == "en" else "zh"


def title(prefs=None, lang="zh") -> str:
    """The owner's form of address: owner.address (trimmed, one line, ≤ 16 chars) or the language default."""
    t = ""
    if isinstance(prefs, dict):
        from . import preferences
        t = preferences.get(prefs, "owner.address", "") or ""
    t = " ".join(str(t).split())[:TITLE_MAX] if isinstance(t, str) else ""
    return t or DATA["default_title"][_lang(lang)]


def period(now: dt.datetime | None = None) -> str:
    h = (now or dt.datetime.now()).hour
    for name, (a, b) in DATA["periods"].items():
        if a <= h < b:
            return name
    return "night"


def template(key: str, lang="zh", now: dt.datetime | None = None) -> str:
    v = LINES[key][_lang(lang)]
    now = now or dt.datetime.now()
    if isinstance(v, dict):
        v = v[period(now)]
    if isinstance(v, list):
        v = v[(now.toordinal() * 24 * 60 + now.hour * 60 + now.minute) % len(v)]
    return v


def say(key: str, lang="zh", prefs=None, now: dt.datetime | None = None, **kw) -> str:
    """The line for `key` in the owner's language; {title} filled from prefs, other {fields} from kw (missing → "")."""
    kw.setdefault("title", title(prefs, lang))
    return _FIELD.sub(lambda m: str(kw.get(m.group(1), "")), template(key, lang, now))


def of(host, key: str, **kw) -> str:
    """say() for an object that has the serve host's `lang` / `preferences` (the host itself, or an Agent's .host)."""
    h = getattr(host, "host", None) if not hasattr(host, "preferences") else host
    h = h if h is not None else host
    return say(key, getattr(h, "lang", "zh"), getattr(h, "preferences", None), **kw)


def variants(key: str, lang: str) -> list[str]:
    """Every template of a key (tests: length limits, no stray placeholders)."""
    v = LINES[key][lang]
    if isinstance(v, dict):
        return [x for p in v.values() for x in (p if isinstance(p, list) else [p])]
    return list(v) if isinstance(v, list) else [v]
