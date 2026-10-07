"""C2 (P73, ADR-A178): the `codex app-server` processes Agent J starts, and who holds a Codex thread's writer.

Codex allows one writer per thread (`thread already has an active writer`). When the owner has quit the Codex App and the
terminal and the phone is still refused, the holder is one of: an app-server Agent J itself left behind (serve killed hard —
SIGKILL after a stop timeout, a crash, a launchd restart that only ends serve: each app-server runs in its own session, so on
macOS nothing else takes it down), the Codex inside the ChatGPT desktop App, the Codex App, an editor's / daemon's
app-server, or a `codex` still open in some terminal.

- `record` / `forget`: every app-server serve spawns is written to `<state>/codex-app-server.json` (0600) with its start
  time (Linux /proc/<pid>/stat field 22, macOS `ps -o lstart=`), so a reused PID is never mistaken for ours.
- `sweep` (serve start, and before a writer retry): recorded processes that are still alive, started at the recorded time
  and still look like a codex app-server are ended (their process group: SIGTERM, then SIGKILL) — they can only be leftovers,
  one serve runs per state directory.
- `who(st, rollout, current)`: first the processes that have this thread's rollout file open (Linux /proc/*/fd, macOS
  `lsof -t`), else the process list; classified as agentj (ours, recorded) / chatgpt_app / codex_app / app_server /
  terminal / unknown. Nothing found = "unknown" — the phone is told exactly that, never a guess presented as fact.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

FILE = "codex-app-server.json"
PS_TIMEOUT = 5


def _path(st):
    return st.root / FILE


def _linux() -> bool:
    return sys.platform.startswith("linux") and os.path.isdir("/proc/self")


def start_token(pid: int) -> str | None:
    """When the process started (an opaque token), None = not running / not readable."""
    if _linux():
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                s = fh.read().decode("utf-8", "replace")
            return s[s.rindex(")") + 2:].split()[19]
        except (OSError, ValueError, IndexError):
            return None
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=PS_TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    t = r.stdout.strip()
    return t or None


def parent(pid: int) -> int | None:
    if _linux():
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                s = fh.read().decode("utf-8", "replace")
            return int(s[s.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    try:
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(int(pid))], capture_output=True, text=True, timeout=PS_TIMEOUT)
        return int(r.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def cmdline(pid: int) -> str:
    if _linux():
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return ""
    try:
        r = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True, timeout=PS_TIMEOUT)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def proc_table() -> list[tuple[int, str]]:
    """(pid, command line) of this user's visible processes; [] when it cannot be read."""
    out: list = []
    if _linux():
        me = os.getuid()
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            with contextlib.suppress(OSError):
                if os.stat(f"/proc/{d}").st_uid != me:
                    continue
                c = cmdline(int(d))
                if c:
                    out.append((int(d), c))
        return out
    try:
        r = subprocess.run(["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True, timeout=PS_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    for ln in r.stdout.splitlines():
        pid, _, c = ln.strip().partition(" ")
        if pid.isdigit() and c.strip():
            out.append((int(pid), c.strip()))
    return out


def fd_holders(path) -> list[int] | None:
    """PIDs with this file open; None = this platform gives no answer (no /proc, no lsof)."""
    real = os.path.realpath(str(path))
    if _linux():
        out = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            fdd = f"/proc/{d}/fd"
            with contextlib.suppress(OSError):
                for fd in os.listdir(fdd):
                    with contextlib.suppress(OSError):
                        if os.readlink(f"{fdd}/{fd}") == real:
                            out.append(int(d))
                            break
        return out
    exe = shutil.which("lsof")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-t", "--", real], capture_output=True, text=True, timeout=PS_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return [int(x) for x in r.stdout.split() if x.isdigit()]


# ------------------------------------------------------------------ the pidfile
def entries(st) -> list[dict]:
    try:
        data = json.loads(_path(st).read_text())
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("pid"), int)] if isinstance(data, list) else []


def _save(st, rows: list) -> None:
    p = _path(st)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(rows[-32:], fh)
    os.replace(tmp, p)


def record(st, pid: int) -> None:
    with contextlib.suppress(Exception):
        rows = [e for e in entries(st) if e["pid"] != pid]
        rows.append({"pid": int(pid), "start": start_token(pid), "ts": int(time.time())})
        _save(st, rows)


def forget(st, pid: int) -> None:
    with contextlib.suppress(Exception):
        rows = entries(st)
        if any(e["pid"] == pid for e in rows):
            _save(st, [e for e in rows if e["pid"] != pid])


def ours_alive(st, exclude=()) -> list[int]:
    """Recorded app-servers still running as the very same process (start time and command line still match) that are no
    longer children of this serve: leftovers of an earlier one."""
    out = []
    for e in entries(st):
        pid = e["pid"]
        if pid in exclude or pid == os.getpid():
            continue
        tok = start_token(pid)
        if tok is None or e.get("start") is None or tok != e["start"]:     # never signal a PID we cannot prove is ours
            continue
        if "app-server" not in cmdline(pid):
            continue
        if parent(pid) == os.getpid():     # still a live child of this serve (e.g. a scheduled run's): not a leftover
            continue
        out.append(pid)
    return out


def _end(pid: int) -> bool:
    """End a process and its group (it is a session leader: start_new_session). True = gone."""
    try:
        pg = os.getpgid(pid)
    except OSError:
        return True
    kill = (lambda s: os.killpg(pid, s)) if pg == pid else (lambda s: os.kill(pid, s))
    for sig, wait in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
        with contextlib.suppress(OSError):
            kill(sig)
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait:
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(pid, os.WNOHANG)
            if start_token(pid) is None:
                return True
            time.sleep(0.05)
    return start_token(pid) is None


def sweep(st, exclude=()) -> list[int]:
    """End Agent J's own leftover app-servers; prune the pidfile. → the PIDs ended."""
    ended = []
    try:
        live = ours_alive(st, exclude)
        for pid in live:
            if _end(pid):
                ended.append(pid)
        keep = [e for e in entries(st) if e["pid"] in exclude or (e["pid"] in live and e["pid"] not in ended)]
        _save(st, keep)
    except Exception:  # noqa: BLE001 — never keeps serve from starting
        pass
    return ended


# ------------------------------------------------------------------ who holds the writer
def classify(cmd: str) -> str | None:
    """A process command line → a holder kind, None = not a Codex process."""
    c = cmd or ""
    low = c.lower()
    if "chatgpt.app/" in low or "/chatgpt " in low or low.startswith("chatgpt"):
        return "chatgpt_app" if "codex" in low or "app-server" in low else None
    if "codex.app/" in low:
        return "codex_app"
    words = c.split()
    first = os.path.basename(words[0]) if words else ""
    has_codex = any(os.path.basename(w) == "codex" or os.path.basename(w).startswith("codex-") for w in words[:3])
    if not has_codex and first != "codex":
        return None
    if "app-server" in words:
        return "app_server"
    if first in ("codex", "node") or has_codex:
        return "terminal"
    return None


def who(st, rollout=None, current=None) -> dict:
    """{"kind", "pid", "how": "fd"|"ps"|None}. `current` = the app-server that was just refused (never the holder)."""
    exclude = {current} if current else set()
    ours = set(ours_alive(st, exclude))
    pids = None
    if rollout is not None:
        with contextlib.suppress(Exception):
            pids = fd_holders(rollout)
    if pids:
        for pid in pids:
            if pid in exclude or pid == os.getpid():
                continue
            if pid in ours:
                return {"kind": "agentj", "pid": pid, "how": "fd"}
            kind = classify(cmdline(pid)) or "unknown"
            return {"kind": kind, "pid": pid, "how": "fd"}
    if ours:
        return {"kind": "agentj", "pid": sorted(ours)[0], "how": "ps"}
    seen: dict = {}
    for pid, c in proc_table():
        if pid in exclude or pid == os.getpid():
            continue
        k = classify(c)
        if k and k not in seen:
            seen[k] = pid
    for k in ("chatgpt_app", "codex_app", "terminal", "app_server"):
        if k in seen:
            return {"kind": k, "pid": seen[k], "how": "ps"}
    return {"kind": "unknown", "pid": None, "how": None}


TEXT = {
    "agentj_cleaned": ("是 Agent J 上次留下的后台进程（codex app-server）占着这个会话，已自动清理，接着这个会话。",
                       "An Agent J leftover background process (codex app-server) held this session; it was cleaned up automatically "
                       "and the session goes on."),
    "agentj": ("是 Agent J 上次留下的后台进程（codex app-server，PID {pid}）占着这个会话，自动清理没成功，手机现在只读。"
               "电脑上运行 `agentj service restart` 后重发；还不行就重启电脑。",
               "An Agent J leftover background process (codex app-server, PID {pid}) holds this session and could not be ended; "
               "the phone is read-only. Run `agentj service restart` on the computer and resend; restart the computer if it persists."),
    "chatgpt_app": ("是电脑上 ChatGPT App 里的 Codex 正在使用这个会话，手机现在只读。完全退出 ChatGPT App（不只是关窗口）后重发。",
                    "The Codex inside the ChatGPT App on your computer is using this session; the phone is read-only. Quit the "
                    "ChatGPT App completely (not just its window), then resend."),
    "codex_app": ("电脑上的 Codex App 正在使用这个会话，手机现在只读。完全退出 Codex App 后重发，就能接着同一个会话。",
                  "The Codex App on your computer is using this session; the phone is read-only. Quit the Codex App completely, "
                  "then resend to continue this same session."),
    "terminal": ("是终端里的 codex（PID {pid}）正在使用这个会话，手机现在只读。在那个终端里完全退出 codex（/quit 或 Ctrl+C）后重发。",
                 "A `codex` in a terminal (PID {pid}) is using this session; the phone is read-only. Quit that codex completely "
                 "(/quit or Ctrl+C), then resend."),
    "app_server": ("是另一个 Codex 后台进程（codex app-server，PID {pid}，可能是编辑器插件或 Codex App 的后台）正在使用这个会话，"
                   "手机现在只读。完全退出它（或它所属的程序）后重发。",
                   "Another Codex background process (codex app-server, PID {pid}; an editor extension or the Codex App's "
                   "background) is using this session; the phone is read-only. Quit it completely (or its app), then resend."),
    "unknown": ("有别的 Codex 程序正在使用这个会话，但查不到是谁，手机现在只读。完全退出电脑上的 Codex App、ChatGPT App 和终端里的 "
                "codex 后重发；还不行就重启电脑。",
                "Another Codex program is using this session, but Agent J could not find which; the phone is read-only. Quit the "
                "Codex App, the ChatGPT App and any terminal codex completely, then resend; restart the computer if it persists."),
}


def text(kind: str, pid=None, zh: bool = True) -> str:
    zh_t, en_t = TEXT.get(kind) or TEXT["unknown"]
    return (zh_t if zh else en_t).format(pid=pid if pid else "?")
