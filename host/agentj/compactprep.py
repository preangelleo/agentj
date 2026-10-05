"""Compaction preparation (F24, 0.15.2; PROTOCOL §15): a `/compact` never throws away what the main Agent was holding.

Leo (2026-10-05): "如果用户直接发来一个压缩，没有准备，那这个压缩前我们应该主动先执行一下准备工作". So when `/compact` (the
phone's menu, typed `/compact`, or a bare 「压缩」 — slash.parse) reaches the main Agent's queue and this conversation is not
prepared yet, the host first runs one ordinary turn with a host-built instruction: write the handover file, then the real
compaction runs exactly as before (Claude Code `/compact`, Codex `thread/compact/start`, OpenCode `summarize`). Same code for
all three harnesses: the preparation is `agent.turn(text)` — the adapter's own normal turn.

Where the handover lives: `<working root>/.agentj/handover/<session key>.md`, NOT the state directory. The fenced Agent
cannot see the state directory (an empty tmpfs, fence.py), while `.agentj/` in its own folder is already ours: 0700 with a
`.gitignore` of `*` (inbox.py, PROTOCOL §10.4), so a handover never enters the customer's git. The host only stats and hashes
the file through the same O_NOFOLLOW dir-fd chain the inbox uses — it never follows a link the Agent planted.

Prepared = the handover file was written after the last compaction of this conversation AND within FRESH seconds (an old
handover would lose everything said since), either by our preparation turn or by the Agent on its own (the user asked for a
"compact-prepare"). Verification of our own turn: the file exists, is non-empty and its (mtime, size, sha256) changed during
the turn. Timeout PREP_TIMEOUT, the stop switch / `/stop` abort the whole `/compact` (nothing compacted), a failed or timed
out preparation still compacts and the card says so in one line.

After a compaction (ours, or Claude Code's own automatic one — compact_boundary outside a command) the next user message
carries one host line in front: 「（压缩前的交接在 <path>，先读它再继续。）」 — once, and only when the file was written in
the epoch that just ended. Context reminder: a user turn that starts with the phone's context meter (`ctx`, the water
level) above REMIND_AT gets one host line appended asking the Agent to say, at the end of its reply, that the context is past
half — at most once per compaction epoch. Both lines start with 「（Agent J：」 / "(Agent J: " so they read as host notes.

State (`<state>/compactprep.json`, 0600, ≤ KEEP_KEYS conversations): per session key {n: compactions, at: last compaction
(unix), prep_at, prep_n, note (path due for the next message), rem_n (epoch reminded in), ctx_at (meter `used` right after
the compaction: a meter that has not moved since is stale, never a reason to remind)}. /clear drops the conversation's
record (a new conversation starts clean). Metadata only in the log: event names + result classes, never text or paths.

P59 (ADR-A165): `proactive` — Codex compacts by itself inside a turn with no hook before it, so a meter ≥ AUTO_PREP_AT at
the end of a user turn runs the preparation turn early (once per epoch, reply hidden); its auto-compaction event calls
`compacted(auto=True)`. The owner's /compact from Telegram arrives through serve.on_slash like the phone's.

Not here: shared sessions (the owner's own harness; nothing is injected there), workflow CEO sessions and scheduled runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import stat
import time

from . import inbox, main_identity

DIR = "handover"
PREP_TIMEOUT = 300.0        # s: the preparation turn (an approval card for the write may be in it)
FRESH = 900.0               # s: a handover older than this is not "prepared" (the Agent updates it instead)
REMIND_AT = 0.5             # ctx used / max above this → the reminder line (once per epoch)
AUTO_PREP_AT = 0.85         # P59: ≥ this at the end of a turn → prepare before the harness's own compaction (proactive)
AUTO_KINDS = ("codex",)     # P59: adapters whose own compaction gets the proactive preparation (see proactive)
KEEP_KEYS = 50
READ_MAX = 1024 * 1024
STATE_FILE = "compactprep.json"
MARK = "[agentj:compact-prepare]"   # first line of the preparation instruction (logs, tests, the fakes)

PREP = {
    "zh": (MARK + "\n（Agent J：用户要压缩上下文。压缩前先把交接写进 `{path}`（Markdown，覆盖旧内容），让压缩后的你读它就能接着干：\n"
           "1. 最初的目标，尽量用用户的原话；\n2. 已完成 / 进行中 / 下一步；\n3. 做过的决定和原因；\n4. 重要的绝对路径；\n"
           "5. 还没解决的问题。\n不要写任何密钥、token、密码或其他凭据。写完只回一句「交接写好了」，不要做别的事。）"),
    "en": (MARK + "\n(Agent J: the user is compacting the context. Before that, write the handover to `{path}` (Markdown, replace "
           "what is there) so that you can carry on after the compaction by reading it:\n1. the original goal, in the user's own "
           "words where possible;\n2. done / in progress / next steps;\n3. decisions made and why;\n4. important absolute paths;\n"
           "5. open questions.\nNo keys, tokens, passwords or other credentials. When it is written, reply with one line "
           "\"Handover written\" and do nothing else.)"),
}
NOTE = {"zh": "（Agent J：压缩前的交接在 {path}，先读它再继续。）",
        "en": "(Agent J: the handover written before the compaction is at {path}; read it first, then carry on.)"}
REMIND = {"zh": "（Agent J：上下文已经用了一半多。请在这次回复的最后加一句：上下文过半了，方便时说「压缩」或发 /compact，"
                "压缩前会自动先写好交接。）",
          "en": "(Agent J: the context is more than half full. At the end of this reply add one sentence: the context is past "
                "half; say \"compact\" or send /compact when convenient — the handover is written automatically first.)"}
PREP_AUTO = {   # P59 (ADR-A165): the context is nearly full and the harness will compact by itself soon
    "zh": (MARK + "\n（Agent J：上下文快满了，Codex 很快会自动压缩。先把交接写进 `{path}`（Markdown，覆盖旧内容），让压缩后的你读它"
           "就能接着干：\n1. 最初的目标，尽量用用户的原话；\n2. 已完成 / 进行中 / 下一步；\n3. 做过的决定和原因；\n4. 重要的绝对路径；\n"
           "5. 还没解决的问题。\n不要写任何密钥、token、密码或其他凭据。写完只回一句「交接写好了」，不要做别的事。）"),
    "en": (MARK + "\n(Agent J: the context is nearly full and Codex will compact it by itself soon. Write the handover to "
           "`{path}` first (Markdown, replace what is there) so that you can carry on after the compaction by reading it:\n"
           "1. the original goal, in the user's own words where possible;\n2. done / in progress / next steps;\n"
           "3. decisions made and why;\n4. important absolute paths;\n5. open questions.\nNo keys, tokens, passwords or other "
           "credentials. When it is written, reply with one line \"Handover written\" and do nothing else.)"),
}
FAILED = {"zh": "压缩前没能先写好交接，已经直接压缩了。",
          "en": "The handover could not be written before compacting; compacted anyway."}
DONE = {"zh": "压缩前已写好交接：{path}", "en": "Handover written before compacting: {path}"}
PROGRESS = {"zh": "先写交接，再压缩……", "en": "Writing the handover first, then compacting…"}
STOPPED = {"zh": "已停下：没有压缩。", "en": "Stopped: nothing was compacted."}
_KEY = re.compile(r"[^A-Za-z0-9._-]+")


# ------------------------------------------------------------------ who this applies to
def applies(agent) -> bool:
    """The main Agent's own conversation on one of the three adapters — never a shared session (the owner's harness), a
    workflow CEO or a scheduled run (persist=False)."""
    cfg = getattr(agent, "cfg", None) or {}
    return (getattr(agent, "kind", None) in ("claude", "codex", "opencode") and not cfg.get("_workflow_ceo")
            and cfg.get("session_mode") != "shared" and getattr(agent, "persist", True)
            and callable(getattr(agent, "cmd_compact", None)))


def lang(agent) -> str:
    return main_identity.language_of(getattr(agent, "cfg", None) or {})


def session_key(agent) -> str | None:
    """`<harness>-<conversation id>` (file-name safe); None while there is no conversation yet."""
    try:
        sid = agent.host.st.agent_session(agent.kind)
    except Exception:  # noqa: BLE001 — a host without state (units)
        return None
    if not isinstance(sid, str) or not sid:
        return None
    return (agent.kind + "-" + _KEY.sub("_", sid).strip("._-"))[:96]


def handover_path(agent, key: str) -> str:
    return os.path.join(agent.cfg["dir"], inbox.DOT, DIR, key + ".md")


# ------------------------------------------------------------------ the handover file (never through a link)
def ensure_dir(workdir: str) -> bool:
    """`<workdir>/.agentj/handover/` (0700), made through the inbox's O_NOFOLLOW chain so the Agent finds it ready."""
    try:
        with inbox.dot_dir(workdir) as d:
            fd = inbox._open_dir(DIR, d, True)
            os.fchmod(fd, 0o700)
            os.close(fd)
        return True
    except (OSError, inbox.Unsafe):
        return False


def probe(workdir: str, key: str) -> tuple | None:
    """(mtime, size, sha256) of the handover, or None (missing, empty, a link, not a regular file, not ours)."""
    try:
        with inbox.dot_dir(workdir, create=False) as d:
            hd = inbox._open_dir(DIR, d, False)
            try:
                fd = os.open(key + ".md", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=hd)
            finally:
                os.close(hd)
            with os.fdopen(fd, "rb") as f:
                s = os.fstat(f.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_size <= 0:
                    return None
                data = f.read(READ_MAX)
        if not data.strip():
            return None
        return s.st_mtime, s.st_size, hashlib.sha256(data).hexdigest()
    except (OSError, inbox.Unsafe):          # missing, a link (ELOOP), not a directory, not ours: not a handover
        return None


# ------------------------------------------------------------------ state
def _path(st):
    return st.root / STATE_FILE


def load(st) -> dict:
    try:
        d = json.loads(_path(st).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(st, d: dict) -> None:
    if len(d) > KEEP_KEYS:      # the conversations touched longest ago go first
        d = dict(sorted(d.items(), key=lambda kv: kv[1].get("seen", 0) if isinstance(kv[1], dict) else 0)[-KEEP_KEYS:])
    with contextlib.suppress(OSError):
        st.write_private(_path(st), json.dumps(d, ensure_ascii=False).encode())


def record(st, key: str) -> tuple[dict, dict]:
    d = load(st)
    r = d.get(key) if isinstance(d.get(key), dict) else {}
    r = {"n": 0, "at": 0, "prep_at": 0, "prep_n": -1, "note": None, "rem_n": -1, "ctx_at": None, **r}
    r["seen"] = int(time.time())
    d[key] = r
    return d, r


def _log(agent, ev: str, **kw) -> None:
    with contextlib.suppress(Exception):
        agent.host.st.log(ev, agent=agent.kind, **kw)


def prepared(agent, key: str, r: dict, now: float | None = None) -> bool:
    """Prepared since the last compaction: the handover was written after it and is still fresh (FRESH)."""
    now = time.time() if now is None else now
    p = probe(agent.cfg["dir"], key)
    return bool(p) and p[0] > (r.get("at") or 0) and now - p[0] <= FRESH


# ------------------------------------------------------------------ /compact
async def compact(agent, fn, arg):
    """Agent.command("compact"): prepare when needed, then `fn(arg)` (the adapter's cmd_compact) exactly as before."""
    st = agent.host.st
    key = session_key(agent)
    if key is None:
        return await fn(arg)                       # no conversation: cmd_compact says so itself
    L = lang(agent)
    d, r = record(st, key)
    path = handover_path(agent, key)
    failed = False
    if prepared(agent, key, r):
        _log(agent, "compact_prep", result="already")
    else:
        ok = await _prepare(agent, key, path, L)
        if ok == "stopped":
            _log(agent, "compact_prep", result="stopped")
            from .slash import Result
            return Result(STOPPED[L], "info")
        failed = not ok
        d, r = record(st, key)
        if ok:
            r["prep_at"], r["prep_n"] = int(time.time()), r["n"]
        save(st, d)
    prev_at = r.get("at") or 0
    res = await fn(arg)
    if getattr(res, "kind", None) == "ok":
        await compacted(agent, prev_at=prev_at)
        p = probe(agent.cfg["dir"], key)
        res.text += "\n" + (FAILED[L] if failed or not p else DONE[L].format(path=path))
    elif failed:
        res.text += "\n" + FAILED[L]
    return res


async def _prepare(agent, key: str, path: str, L: str, text: dict | None = None):
    """One ordinary turn with the preparation instruction. True = the handover was written during it; False = not
    (missing, unchanged, timed out, refused); "stopped" = the stop switch / `/stop` ended it."""
    text = text or PREP
    host = agent.host
    ensure_dir(agent.cfg["dir"])
    before = probe(agent.cfg["dir"], key)
    cmd = getattr(agent, "cur_cmd", None)
    turn = getattr(cmd, "turn", None)
    upd = getattr(host, "hist_update", None)
    if turn is not None and upd:                   # the command's page: one page, 「先写交接，再压缩」 then the result
        with contextlib.suppress(Exception):
            upd(turn, text=PROGRESS[L], end="open")
    had_cur = hasattr(host, "cur_turn")
    old = getattr(host, "cur_turn", None)
    if had_cur and turn is not None:
        host.cur_turn = turn                       # the Agent's one-line answer lands on that page too
    agent.set_status("working")
    _log(agent, "compact_prep", result="start")
    result = None
    try:
        await asyncio.wait_for(agent.turn(text[L].format(path=path)), PREP_TIMEOUT)
    except asyncio.TimeoutError:
        result = "timeout"
        with contextlib.suppress(Exception):
            await agent.halt(clear_queue=False)    # the harness's turn must not keep running under the compaction
        agent.halting = False
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a failed preparation never blocks the compaction
        result = "error"
    finally:
        if had_cur:
            host.cur_turn = old
            with contextlib.suppress(AttributeError):
                host.cur_failed = False
    stopper = getattr(host, "stopped", None)
    if agent.halting or (callable(stopper) and stopper()):
        agent.halting = False
        return "stopped"
    after = probe(agent.cfg["dir"], key)
    ok = result is None and after is not None and after != before
    _log(agent, "compact_prep", result="ok" if ok else (result or ("missing" if after is None else "unchanged")))
    return ok


async def compacted(agent, prev_at: float | None = None, auto: bool = False) -> None:
    """A compaction of this conversation finished (ours, or the harness's own): a new epoch — the handover note is due
    when the file was written in the epoch that ended; the reminder may come again."""
    if not applies(agent):
        return
    key = session_key(agent)
    if key is None:
        return
    st = agent.host.st
    if auto:                                       # Claude Code compacted by itself: let its meter catch up first
        fn = getattr(agent, "context_meter", None)
        if fn:
            with contextlib.suppress(Exception):
                await fn()
    elif getattr(agent, "kind", None) == "opencode":   # summarize does not refresh the water level by itself
        with contextlib.suppress(Exception):
            await agent.context_meter()
    d, r = record(st, key)
    prev = (r.get("at") or 0) if prev_at is None else prev_at
    p = probe(agent.cfg["dir"], key)
    r["n"] += 1
    r["at"] = time.time()
    r["note"] = handover_path(agent, key) if p and p[0] > prev else None
    ctx = _ctx(agent)
    r["ctx_at"] = ctx[0] if ctx else None
    save(st, d)
    _log(agent, "compacted", result="auto" if auto else "ok", status="note" if r["note"] else "no_note")


async def proactive(agent) -> bool:
    """P59 (ADR-A165): the harness compacts by itself when its context is full (Codex inside a turn, with no hook for us to
    run first). So at the END of a user turn whose meter reads ≥ AUTO_PREP_AT, and when this epoch has neither a fresh
    handover nor an earlier attempt, run the preparation turn now (its one-line answer is not shown: collected, not sent).
    Nothing is compacted here. Once per epoch whatever the outcome. Returns True when the handover was written.
    Only for AUTO_KINDS (the adapters that can run a turn without showing it); never for a shared / CEO / scheduled run."""
    if getattr(agent, "kind", None) not in AUTO_KINDS or not applies(agent) or getattr(agent, "halting", False) \
            or not hasattr(agent, "collect") or agent.collect is not None:
        return False
    stopper = getattr(agent.host, "stopped", None)
    if callable(stopper) and stopper():
        return False
    key = session_key(agent)
    ctx = _ctx(agent)
    if key is None or not ctx or ctx[0] / ctx[1] < AUTO_PREP_AT:
        return False
    st = agent.host.st
    d, r = record(st, key)
    if r.get("auto_n") == r["n"] or ctx[0] == r.get("ctx_at"):
        return False                               # tried in this epoch already / the meter has not moved since compacting
    r["auto_n"] = r["n"]
    save(st, d)
    if prepared(agent, key, r):
        _log(agent, "compact_auto_prep", result="already")
        return False
    agent.collect = []                             # the Agent's 「交接写好了」 is not a reply to anything the user said
    try:
        ok = await _prepare(agent, key, handover_path(agent, key), lang(agent), PREP_AUTO)
    finally:
        agent.collect = None
    if ok is True:
        d, r = record(st, key)
        r["prep_at"], r["prep_n"] = int(time.time()), r["n"]
        save(st, d)
    _log(agent, "compact_auto_prep", result="ok" if ok is True else ("stopped" if ok == "stopped" else "failed"))
    return ok is True


def cleared(agent, key: str | None) -> None:
    """/clear: the conversation that was put aside starts clean if it ever comes back (undo)."""
    if key is None:
        return
    d = load(agent.host.st)
    if d.pop(key, None) is not None:
        save(agent.host.st, d)


# ------------------------------------------------------------------ the next user message
def _ctx(agent):
    m = getattr(agent.host, "meter_state", None)
    c = m.get("ctx") if isinstance(m, dict) else None
    if isinstance(c, dict) and isinstance(c.get("used"), (int, float)) and isinstance(c.get("max"), (int, float)) \
            and not isinstance(c.get("used"), bool) and c["max"] > 0:
        return c["used"], c["max"]
    return None


def decorate(agent, send) -> str:
    """The text the harness gets for a user's message: the handover note in front (first message after a compaction), the
    context reminder at the end (meter past REMIND_AT, once per epoch). Anything else → the text as it is."""
    text = getattr(send, "text", send) if not isinstance(send, str) else send
    if not isinstance(text, str) or getattr(send, "official_notice_id", None) or not applies(agent):
        return text
    key = session_key(agent)
    if key is None:
        return text
    try:
        st = agent.host.st
        d, r = record(st, key)
    except Exception:  # noqa: BLE001
        return text
    L = lang(agent)
    head = tail = ""
    changed = False
    if r.get("note"):
        if probe(agent.cfg["dir"], key):
            head = NOTE[L].format(path=r["note"]) + "\n"
        r["note"] = None
        changed = True
    ctx = _ctx(agent)
    if ctx and ctx[0] / ctx[1] > REMIND_AT and r.get("rem_n") != r["n"] and ctx[0] != r.get("ctx_at"):
        tail = "\n\n" + REMIND[L]
        r["rem_n"] = r["n"]
        changed = True
    if changed:
        save(st, d)
        _log(agent, "compact_note", result="+".join(x for x, on in (("note", head), ("remind", tail)) if on) or "note_gone")
    return head + text + tail
