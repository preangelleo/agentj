"""P127 / P174: the session mode and the shared Claude session are chosen, not remembered.

P174 (Leo 10-12): `agent.session_mode` defaults to "independent" (defaults.json5 / schema) — Agent J's own fenced session,
which needs no change to the owner's terminal tools and cannot get stuck on a desktop setting. "shared" (attach to the
owner's own open Claude Code / Codex / OpenCode session) is the advanced choice the owner turns on: `agentj agent <kind>
--shared`, `agentj config set agent.session_mode shared` or the phone's Settings. Every committed change of the key is
recorded in `session-mode-choice.json` (0600, written by preferences.commit_files and by the flags below).

Installs from before P174 ran shared as the factory default. They are never switched silently: config migration
`202610120174` writes an explicit "shared" into their config file, and until it has run `factory_default()` keeps such an
install on "shared" (`legacy_shared()`), so a failed or pending migration cannot move a running owner either.

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
LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "kimi": "Kimi Code", "qwen": "Qwen Code", "dsh": "DeepSeek Harness"}


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


LEGACY_MIGRATION = "202610120174"


def logged_mode(st) -> str | None:
    """The session mode the last Agent start ran with (host.log `agent_on` / `agent_start`, metadata only), or None."""
    try:
        with open(st.log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - (1 << 20)))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    found = re.findall(r'"ev": "agent_(?:on|start)"[^\n]*?"session_mode": "(shared|independent)"', tail)
    return found[-1] if found else None


def legacy_shared(st) -> bool:
    """An install from before P174 that was on the shared factory default and whose keep-shared migration has not run.
    Evidence, strongest first: the owner's recorded choice; the mode the last Agent start actually ran with (host.log);
    otherwise a configured Agent on a state that already ran config migrations (0.12+; shared was the default 0.13–0.19)."""
    try:
        if (st.root / "config-migrations" / (LEGACY_MIGRATION + ".json")).exists() or not st.exists():
            return False
        if user_value() is not None or recorded(st) == "independent" or not st.config().get("agent"):
            return False
        last = logged_mode(st)
        if last is not None:
            return last == "shared"
        return (st.root / "config-migrations").is_dir()
    except (OSError, ValueError):
        return False


def factory_default(st=None) -> str:
    return "shared" if st is not None and legacy_shared(st) else "independent"


def mode_of(st) -> str:
    """The mode in effect: the owner's value, else the factory default (shared only for a not-yet-migrated old install)."""
    v = user_value()
    if v in ("shared", "independent"):
        return v
    return factory_default(st)


def mode_in(st, doc: dict) -> str:
    """The mode in effect for an effective preferences document (state.agent_config, serve): P174 legacy guard included."""
    v = preferences.get(doc, KEY, "independent")
    if v != "shared" and legacy_shared(st):
        return "shared"
    return v if v in ("shared", "independent") else "independent"


def note_commit(st, old_raw: str, new_raw: str, candidate: dict) -> None:
    """preferences.commit_files: a committed change of the key (set, unset, reset, rollback) is the owner's choice."""
    try:
        before = preferences.get(preferences.parse(old_raw), KEY) if old_raw.strip() else None
        after = preferences.get(preferences.parse(new_raw), KEY)
    except (preferences.ConfigError, ValueError):
        return
    if before != after:
        record(st, preferences.get(candidate, KEY, "independent"), "config")


def explain(new: str, old: str | None = None, lang: str = "zh") -> dict:
    """P174: what a session-mode switch means and how to undo it (`agentj config set|unset`, `agentj agent --shared|...`)."""
    back = "independent" if new == "shared" else "shared"
    rollback = f"agentj config set {KEY} {back}" if old in ("shared", "independent") and old != new else ""
    if new == "shared":
        zh = ("共享会话（高级）：手机接入你在这台电脑上正开着的 Claude Code / Codex / OpenCode 会话，按那个工具自己的权限运行，"
              "不在 Agent J 的隔离里；Claude Code 会被设置成接收手机消息（先备份设置）。电脑上没开会话时，手机第一条消息会新开一个。"
              "重启服务后生效：agentj service restart。")
        en = ("Shared session (advanced): the phone attaches to the Claude Code / Codex / OpenCode session you have open on this "
              "computer, with that tool's own permissions, outside Agent J's fence; Claude Code is set to accept phone messages "
              "(settings backed up first). With no open session the first phone message starts one. Takes effect after "
              "agentj service restart.")
    else:
        zh = ("独立会话（默认）：Agent J 自己开一个会话、在隔离里运行，不改你终端里工具的设置，出问题也不会卡住你正在用的会话。"
              "重启服务后生效：agentj service restart。")
        en = ("Independent session (the default): Agent J runs its own fenced session and changes nothing in your terminal tools' "
              "settings, so a problem there cannot block the session you are using. Takes effect after agentj service restart.")
    if rollback:
        zh += f"想改回去：{rollback}"
        en += f" To undo: {rollback}"
    return {"mode": new, "text": en if lang == "en" else zh, "text_zh": zh, "text_en": en, "rollback": rollback,
            "restart": "agentj service restart"}


def _lock(st):
    st.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(st.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def set_mode(st, mode: str, via: str = "agent") -> None:
    """P174: write the owner's choice explicitly (also "independent", so it can never be read as an old install's shared
    default). Same path as `agentj config set`."""
    fd = _lock(st)
    try:
        raw, _ = preferences.read()
        new = preferences.edit(raw, KEY, mode)
        if new != raw:
            preferences.transact(st, new)
    finally:
        os.close(fd)
    record(st, mode, via)


def set_pin(st, sid: str) -> None:
    from . import shared_pin
    shared_pin.forget_owner(st)         # P330: the owner's pick in the terminal: absorbed at the next serve start
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


def resolve(st, kind: str, flag: str | None, lang: str = "zh", ask=None, out=None) -> str:
    """`agentj agent <kind>`: apply --shared / --independent. → the mode in effect. P174: nothing is asked; an unset value is
    the factory default (independent), an explicit value is the owner's."""
    if flag in ("shared", "independent"):
        set_mode(st, flag, "flag")
        return flag
    return mode_of(st)


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
    t = re.sub(r"\s+", " ", privacy.owner_text(text)).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def candidates(directory: str) -> list[dict]:
    """Live Claude Code sessions in exactly this folder, most recently active first. Metadata only."""
    from .shared import claude_sessions
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(directory))
    rows = []
    for d in claude_sessions(directory, "", True):     # P147: never a headless `claude -p` / background job
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
    from . import shared_pin           # P330: Claude's own pin (a Codex / OpenCode id in the config is not Claude's)
    pinned = shared_pin.resolve(st, "claude", preferences.get(preferences.effective(st), PIN, "") or "", directory) or ""
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
