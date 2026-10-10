"""Shared Claude model / effort for THIS session only, through the native `/model` picker (P116, B5; Relay ADR-035).

Why the picker: in Claude Code `/model <name>` and `/effort <level>` save the pick as the default for new sessions (a
settings write). The interactive picker changes model AND effort for the current session only (`s` = "use this session
only"). This module drives it through Herdr on the exact pane of the served session, reading the screen after every step
and never sending a key it has not just verified the screen for.

Red lines, checked in this order (same as Relay's modelswitch):
  * only a quiet pane (Herdr idle / done) — `busy` otherwise;
  * nothing on screen may look like a permission / question box — `blocked` (an Enter there would answer it);
  * the prompt line must be empty — the owner's draft is never typed into: `busy`;
  * the only text sent is the literal "/model"; everything else is a fixed key (up, down, right, s, enter, esc);
  * the one dialog answered is Claude Code's own "Switch model?" / "Change effort level?" warning our switch raised.
Truth is not what we typed: success is claimed only when the session's own status line reports the target (shared.py).
"""
from __future__ import annotations
import re
import time

from .shared_interrupt import herdr_bin, locate, _run, _QUIET, _BLOCKED

# Picker rows are built by Claude Code from the account's list and change between versions (2.1.280: "Opus", "Fable",
# "Opus (1M context)"; 2.1.289, probed by P116: "Opus 5.5", "Fable 5.1", "Sonnet 5.5" … with a scrolling list). So a model
# id is the slug of a row label (or of the status line's display name: the same text), rows are learned from the picker
# itself, and a row the picker does not show fails honestly (`row_missing`) — never guessed.
# Before any picker was seen, the 2.1.280 rows Relay verified are offered (LEGACY).
LEGACY = ("Opus (1M context)", "Sonnet", "Fable", "Opus", "Haiku")
FAMILIES = ("opus", "sonnet", "fable", "haiku")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")


def slug(label):
    return re.sub(r"[^a-z0-9.]+", "-", str(label).lower()).strip("-")


EFFORTS = ("low", "medium", "high", "xhigh", "max")
RING = ("low", "medium", "high", "xhigh", "max", "ultracode")   # the picker's → ring (wraps); ultracode is not an effort

SENT, BUSY, BLOCKED, NO_PANE, UNAVAILABLE, FAILED = "sent", "busy", "blocked", "no_pane", "unavailable", "failed"
STEP_S = 0.35
OPEN_TRIES = 20
SETTLE_TRIES = 20

_ROW = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(\S.*?)(?:\s+✔)?(?:\s{2,}(\S.*?))?\s*$")
_EFFORT = re.compile(r"\b(Low|Medium|High|xHigh|Max|Ultracode) effort\b")
_DIALOG = re.compile(r"Do you want|\(y/n\)|Allow once|Don.t allow|Yes, and don|❯ *1\.")
_INPUT = re.compile(r"^\s*❯(?:[  ](.*))?$")


def key_of(display_name):
    """Model id for a status-line display name ("Opus 5.5 (1M context)" → "opus-5.5-1m-context"), or None."""
    if not isinstance(display_name, str) or not display_name.strip():
        return None
    return slug(display_name) or None


def valid_id(model):
    return model == "default" or (isinstance(model, str) and bool(ID_RE.match(model)))


def resolve(word, rows):
    """`/model` words → id: an exact id / label, a family name (first row of that family as the picker orders them),
    `opus1m` (the 1M row) or `default`. None when unknown."""
    w = slug(word)
    labels = [r for r in (rows or LEGACY) if not r.startswith("Default")]
    if w == "default":
        return "default"
    for lab in labels:
        if slug(lab) == w:
            return w
    if w in ("opus1m", "opus-1m"):
        return next((slug(l) for l in labels if l.startswith("Opus") and "1M" in l), "opus-1m-context")
    if w in FAMILIES:
        return next((slug(l) for l in labels if slug(l).startswith(w) and "1m" not in slug(l)), None)
    return w if re.fullmatch(r"(opus|sonnet|fable|haiku)-\d+(\.\d+)?(-1m-context)?", w) else None


def matches(model, key):
    """Does the status line's model id `key` satisfy the requested `model`? A version-less LEGACY row ("fable") is met by
    any version of that family with the same context size; everything else must be exact."""
    if not key or not model:
        return False
    if model == key:
        return True
    fam = model.split("-")[0]
    return model in {slug(l) for l in LEGACY} and key.startswith(fam + "-") and ("1m" in model) == ("1m" in key)


def screen(binary, pane):
    """What the pane shows right now (Herdr's visible snapshot, plain text) — Relay's proven route."""
    import subprocess
    try:
        p = subprocess.run([binary, "agent", "read", pane, "--source", "visible"], capture_output=True, text=True,
                           timeout=4.0, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def keys(binary, pane, *names):
    return bool(names) and _run(binary, "agent", "send-keys", pane, *names) is not None


def prompt(binary, pane, text):
    return _run(binary, "agent", "prompt", pane, text) is not None


def picker_open(text):
    return "Select model" in text and "use this session only" in text


def confirm_open(text):
    return ("Switch model?" in text or "Change effort level?" in text) and "Yes, switch to" in text


def _picker_part(text):
    i = text.rfind("Select model")
    return text[i:] if i >= 0 else ""


def rows(text):
    out = []
    for line in _picker_part(text).splitlines():
        m = _ROW.match(line)
        if m:
            out.append((m.group(3).strip(), bool(m.group(1)), (m.group(4) or "").strip()))
    return out


def row_index(text, model):
    for i, (lab, _, _) in enumerate(rows(text)):
        if not lab.startswith("Default") and slug(lab) == model:
            return i
    return None


def default_index(text):
    return next((i for i, (lab, _, _) in enumerate(rows(text)) if lab.startswith("Default")), None)


def cursor_index(text):
    return next((i for i, (_, cur, _) in enumerate(rows(text)) if cur), None)


def effort_on_screen(text):
    found = _EFFORT.findall(_picker_part(text))
    return found[-1].lower() if found else None


def input_text(text):
    last = None
    for line in text.splitlines():
        m = _INPUT.match(line)
        if m and not _ROW.match(line):
            last = (m.group(1) or "").strip()
    return last


def dialog_on_screen(text):
    return bool(_DIALOG.search("\n".join(text.splitlines()[-16:])))


def _pick(binary, pane, find, effort, sleep, seen):
    def look():
        sleep(STEP_S)
        return screen(binary, pane) or ""

    def bail(why):
        t = screen(binary, pane) or ""
        if picker_open(t) or confirm_open(t):
            keys(binary, pane, "esc")
        return why

    if not prompt(binary, pane, "/model"):
        return "prompt_refused"
    for _ in range(OPEN_TRIES):
        text = look()
        if picker_open(text):
            break
    else:
        return bail("no_picker")
    seen[:] = [lab for lab, _, _ in rows(text)]
    want, at = find(text), cursor_index(text)
    if want is None:
        return bail("row_missing")
    if at is None:
        return bail("no_cursor")
    if want != at:
        keys(binary, pane, *(["down"] * (want - at) if want > at else ["up"] * (at - want)))
        text = look()
        if cursor_index(text) != want or find(text) != want:
            return bail("cursor_missed")
    if effort is not None:
        for _ in range(len(RING) + 1):
            cur = effort_on_screen(text)
            if cur == effort:
                break
            if cur is None:
                return bail("no_effort")
            keys(binary, pane, "right")
            text = look()
        else:
            return bail("effort_missed")
    if not picker_open(text) or cursor_index(text) != want:
        return bail("picker_moved")
    keys(binary, pane, "s")
    for _ in range(SETTLE_TRIES):
        text = look()
        if confirm_open(text):
            keys(binary, pane, "enter")
            continue
        if picker_open(text):
            continue
        return None
    return bail("did_not_settle")


def apply(session, model, effort, sleep=time.sleep):
    """Switch `session` to (model id | "default", effort | None) for this session only.
    → {"result": sent|busy|blocked|no_pane|unavailable|failed, "why", "rows"}; nothing half-typed stays behind."""
    if not valid_id(model) or (effort is not None and effort not in EFFORTS):
        return {"result": FAILED, "why": "not_in_catalogue", "rows": None}
    binary = herdr_bin()
    if not binary:
        return {"result": UNAVAILABLE, "why": "no_herdr", "rows": None}
    pane, status = locate(session, binary)
    if not pane:
        return {"result": NO_PANE, "why": None, "rows": None}
    if status in _BLOCKED:
        return {"result": BLOCKED, "why": "herdr_blocked", "rows": None}
    if status not in _QUIET:
        return {"result": BUSY, "why": f"herdr_{status}", "rows": None}
    text = screen(binary, pane)
    if text is None:
        return {"result": UNAVAILABLE, "why": "unreadable", "rows": None}
    if dialog_on_screen(text) or picker_open(text) or confirm_open(text):
        return {"result": BLOCKED, "why": "dialog_on_screen", "rows": None}
    draft = input_text(text)
    if draft is None:
        return {"result": BUSY, "why": "no_prompt_line", "rows": None}
    if draft:
        return {"result": BUSY, "why": "input_not_empty", "rows": None}
    seen = []
    if model == "default":
        why = _pick(binary, pane, default_index, effort, sleep, seen)
    else:
        why = _pick(binary, pane, lambda t: row_index(t, model), effort, sleep, seen)
        if why == "row_missing" and model.endswith("1m-context"):
            # The 1M row may only be offered while the session runs its default model: Default + `s` (still
            # session-only) first; the status line receipt decides whether it landed.
            why = _pick(binary, pane, default_index, None, sleep, seen)
            if why is None:
                why = _pick(binary, pane, lambda t: row_index(t, model), effort, sleep, seen)
                if why == "row_missing":
                    why = None if effort is None else _pick(binary, pane, default_index, effort, sleep, seen)
    return {"result": SENT if why is None else FAILED, "why": why, "rows": seen or None}
