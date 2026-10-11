"""P127 (Leo's 10-10 switch-over): the session mode and the shared Claude session are chosen, not remembered.

`agent.session_mode` defaults to "shared" (defaults.json5 / schema). A user override "independent" is honoured when the
owner chose it — on this state that is recorded in `session-mode-choice.json` (0600, written by preferences.commit_files on
every committed change of the key: `agentj config set|unset|reset|rollback`, the phone's Settings, and by the flags below).
An override with no such record (an older install, a test, a config file left behind by an earlier state) is never flipped
silently: `agentj agent <kind>` asks in a terminal (y switches back to the shared default, Enter keeps it and asks again
next time) and otherwise prints the two commands (`--shared` / `--independent`); doctor shows the same row.

Shared Claude: `candidates()` lists the live Claude Code sessions in the work folder from Claude Code's own registry
(`$CLAUDE_CONFIG_DIR/sessions/*.json`, the same reader as shared.claude_sessions) with name, last activity and the first
owner message (redacted, shortened). Two or more and no live pin → a terminal picks one (`agent.shared_session_id`), anything
else prints the list and the `agentj config set` line. One → attach picks it by itself; no pin is written (a pin outlives
the session and would block the next one). Nothing here adds a phone control.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

from . import preferences

KEY = "agent.session_mode"
PIN = "agent.shared_session_id"
MARK = "session-mode-choice.json"
LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


# ------------------------------------------------------------------ the owner's recorded choice
def record(st, mode: str, via: str = "config") -> None:
    if mode not in ("shared", "independent") or not st.root.exists():
        return
    st.write_private(st.root / MARK, json.dumps({"mode": mode, "via": via, "at": int(time.time() * 1000)}).encode())


def recorded(st) -> str | None:
    try:
        d = json.loads((st.root / MARK).read_text())
    except (OSError, ValueError):
        return None
    m = d.get("mode") if isinstance(d, dict) else None
    return m if m in ("shared", "independent") else None


def user_value() -> str | None:
    """The override in the owner's config.json5 (None = the factory default)."""
    try:
        v = preferences.get(preferences.read()[1], KEY)
    except (preferences.ConfigError, OSError):
        return None
    return v if v in ("shared", "independent") else None


def unconfirmed_independent(st) -> bool:
    return user_value() == "independent" and recorded(st) != "independent"


def note_commit(st, old_raw: str, new_raw: str, candidate: dict) -> None:
    """preferences.commit_files: a committed change of the key (set, unset, reset, rollback) is the owner's choice."""
    try:
        before = preferences.get(preferences.parse(old_raw), KEY) if old_raw.strip() else None
        after = preferences.get(preferences.parse(new_raw), KEY)
    except (preferences.ConfigError, ValueError):
        return
    if before != after:
        record(st, preferences.get(candidate, KEY, "shared"), "config")


def _lock(st):
    st.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(st.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def set_mode(st, mode: str, via: str = "agent") -> None:
    """shared = remove the override (follow the default); independent = write it. Same path as `agentj config set`."""
    fd = _lock(st)
    try:
        raw, _ = preferences.read()
        new = preferences.edit(raw, KEY, None, remove=True) if mode == "shared" else preferences.edit(raw, KEY, "independent")
        if new != raw:
            preferences.transact(st, new)
    finally:
        os.close(fd)
    record(st, mode, via)


def set_pin(st, sid: str) -> None:
    fd = _lock(st)
    try:
        raw, _ = preferences.read()
        new = preferences.edit(raw, PIN, sid)
        if new != raw:
            preferences.transact(st, new)
    finally:
        os.close(fd)


def interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def stale_text(kind: str, lang: str, ask: bool) -> str:
    cmd = f"agentj agent {kind}"
    if lang == "en":
        head = ("Session mode: your settings file (" + preferences.path().name + ") says \"independent\", but there is no "
                "record of you choosing it on this computer — probably left by an older version or a test. The default is "
                f"\"shared\": the phone attaches to the {LABEL.get(kind, kind)} session you already have open on this computer.")
        return head + (" Switch back to shared? [y/N] (Enter keeps independent and asks again next time; to keep it for good: "
                       f"{cmd} --independent) " if ask else
                       f" To use the default: {cmd} --shared · to keep independent on purpose: {cmd} --independent")
    head = ("会话模式：你的设置文件（" + preferences.path().name + "）里写着「独立」（independent），但这台电脑上没有你选过它的记录"
            f"——多半是旧版本或测试留下的。默认是「共享」：手机接上你电脑上正开着的 {LABEL.get(kind, kind)} 会话。")
    return head + ("改回共享？[y/N]（回车 = 先保持独立，下次还会问；确定要独立就运行 " + cmd + " --independent）" if ask else
                   f"用默认的共享：{cmd} --shared · 确定要独立：{cmd} --independent")


def resolve(st, kind: str, flag: str | None, lang: str = "zh", ask=None, out=None) -> str:
    """`agentj agent <kind>`: apply --shared / --independent, or settle an unconfirmed "independent". → the mode in effect."""
    out = out or sys.stdout
    if flag in ("shared", "independent"):
        set_mode(st, flag, "flag")
        return flag
    if not unconfirmed_independent(st):
        return preferences.get(preferences.effective(st), KEY, "shared")
    ask = interactive() if ask is None else ask
    if not ask:
        print("! " + stale_text(kind, lang, False), file=out, flush=True)
        return "independent"
    answer = input(stale_text(kind, lang, True)).strip().lower()
    if answer in ("y", "yes", "是", "好"):
        set_mode(st, "shared", "prompt")
        return "shared"
    if answer in ("n", "no", "否"):
        record(st, "independent", "prompt")
    return "independent"


# ------------------------------------------------------------------ shared Claude: which live session
def _first_owner_message(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f):
                if n > 400:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "user" or d.get("isMeta"):
                    continue
                c = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
                    return c
    except OSError:
        pass
    return ""


def _short(text: str, n: int = 48) -> str:
    from . import privacy
    t = re.sub(r"\s+", " ", privacy.redact(text, paths=True)).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def candidates(directory: str) -> list[dict]:
    """Live Claude Code sessions in exactly this folder, most recently active first. Metadata only."""
    from .shared import claude_sessions
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(directory))
    rows = []
    for d in claude_sessions(directory):
        sid = d.get("sessionId")
        if not isinstance(sid, str) or not re.fullmatch(r"[0-9A-Za-z-]{8,64}", sid):
            continue
        rows.append({"id": sid, "name": _short(d.get("name") or "", 32) if isinstance(d.get("name"), str) else "",
                     "updated": int(d.get("updatedAt") or d.get("startedAt") or 0),
                     "status": d.get("status") if isinstance(d.get("status"), str) else "",
                     "first": _short(_first_owner_message(root / "projects" / slug / (sid + ".jsonl"))),
                     "cwd": d.get("cwd", "")})
    return rows


def _ago(ms: int, lang: str) -> str:
    if not ms:
        return "?"
    s = max(0, int(time.time() - ms / 1000))
    if s < 90:
        return f"{s} s ago" if lang == "en" else f"{s} 秒前"
    if s < 5400:
        return f"{s // 60} min ago" if lang == "en" else f"{s // 60} 分钟前"
    if s < 172800:
        return f"{s // 3600} h ago" if lang == "en" else f"{s // 3600} 小时前"
    return f"{s // 86400} d ago" if lang == "en" else f"{s // 86400} 天前"


def describe(i: int, r: dict, lang: str) -> str:
    bits = [f"{i}. {r['id']}"]
    if r["name"]:
        bits.append(f"「{r['name']}」" if lang != "en" else f"\"{r['name']}\"")
    bits.append(("active " if lang == "en" else "最近活跃 ") + _ago(r["updated"], lang))
    if r["first"]:
        bits.append(("first message: " if lang == "en" else "首条消息：") + r["first"])
    return "  " + " · ".join(bits)


def pick(st, directory: str, lang: str = "zh", ask=None, out=None) -> str | None:
    """After `agentj agent claude` in shared mode. → the pinned / chosen id, or None (attach decides by itself)."""
    out = out or sys.stdout
    try:
        rows = candidates(directory)
    except OSError:
        return None
    pinned = preferences.get(preferences.effective(st), PIN, "") or ""
    en = lang == "en"
    if pinned and any(r["id"] == pinned for r in rows):
        print(("Shared session: pinned to " if en else "共享会话：已指定 ") + pinned, file=out, flush=True)
        return pinned
    if pinned:
        print(("! The pinned shared session " + pinned + " is not open in this folder now; to let Agent J pick the open one: "
               "agentj config unset agent.shared_session_id") if en else
              ("! 指定的共享会话 " + pinned + " 现在没在这个目录里开着；想让 Agent J 自己接正开着的那个：agentj config unset "
               "agent.shared_session_id"), file=out, flush=True)
        if len(rows) < 2:
            return pinned
    if not rows:
        print("Shared session: no Claude Code open in this folder now; the first phone message starts one here (or open "
              "`claude` in this folder yourself)." if en else
              "共享会话：这个目录里现在没有开着的 Claude Code；手机第一条消息会在这里新开一个（也可以自己在这个目录里运行 claude）。",
              file=out, flush=True)
        return None
    if len(rows) == 1:
        print(("Shared session: the phone attaches to the only open one —\n" if en else "共享会话：手机会接上这里唯一开着的那个 ——\n")
              + describe(1, rows[0], lang), file=out, flush=True)
        return rows[0]["id"]
    print(("Shared session: " + str(len(rows)) + " Claude Code sessions are open in this folder; the phone needs exactly one:")
          if en else f"共享会话：这个目录里开着 {len(rows)} 个 Claude Code 会话，手机只能接其中一个：", file=out, flush=True)
    for i, r in enumerate(rows, 1):
        print(describe(i, r, lang), file=out, flush=True)
    ask = interactive() if ask is None else ask
    if ask:
        answer = input(f"Which one? [1-{len(rows)}, Enter = later] " if en else f"接哪一个？[1-{len(rows)}，回车 = 以后再说] ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            sid = rows[int(answer) - 1]["id"]
            set_pin(st, sid)
            print(("✓ Shared session pinned: " if en else "✓ 已指定共享会话：") + sid, file=out, flush=True)
            return sid
    print(("Pick one later: agentj config set agent.shared_session_id <session ID>" if en else
           "之后指定：agentj config set agent.shared_session_id <会话 ID>"), file=out, flush=True)
    return None
