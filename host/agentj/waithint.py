"""0.26.10 (RESEARCH-infinity ②): a long foreground wait in a Bash call → one PreToolUse additionalContext hint per
session to run it in the background via `agentj busy run`. Hint only: never a decision, never raises, never blocks."""
import hashlib
import os
import re
import stat

_SLEEP = re.compile(r"(?:^|[;&|(\s])sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")
_LOOP = re.compile(r"\b(?:while|until)\b[^\n]*?\bdo\b.*?\bsleep\b", re.S)
_FOLLOW = re.compile(r"(?:^|[;&|(\s])(?:watch\s|tail\s+(?:-\S*f|--follow)|journalctl\s.*?(?:-f\b|--follow))")
_BG = re.compile(r"agentj\s+busy\s+(?:run|start)|\bnohup\b|\bsetsid\b|\bsystemd-run\b|&\s*$")
UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
LONG_S = 120
HINT = ("Agent J：这像是前台长等待（sleep / 轮询 / 长命令），会占住这一轮，主人的新消息要排队。改用 Bash 后台运行 "
        "`agentj busy run --label <一句话> -- <命令>`，结束本轮；需要结果时再查看后台任务。只是提示，照样可以运行。 / "
        "Agent J: this looks like a long foreground wait; it holds the turn and the owner's messages queue. Run "
        "`agentj busy run --label <title> -- <command>` in the background and end the turn; check the background job when you need its result. "
        "A hint only — it still runs.")


def long_wait(tool: str, inp) -> bool:
    if tool != "Bash" or not isinstance(inp, dict) or inp.get("run_in_background") is True:
        return False
    cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""
    if not cmd or _BG.search(cmd.strip()):
        return False
    t = inp.get("timeout")
    secs = sum(float(n) * UNIT[u] for n, u in _SLEEP.findall(cmd))
    return secs >= LONG_S or bool(_LOOP.search(cmd) or _FOLLOW.search(cmd)) or (type(t) is int and t >= LONG_S * 2500)


def _private(d: str) -> bool:
    st = os.lstat(d)
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077


def _marker_dir() -> str | None:
    """#488 review: the user's runtime dir (else ~/.cache), a 0700 dir owned by this uid — never a shared /tmp path."""
    rt = os.environ.get("XDG_RUNTIME_DIR") or ""
    base = rt if rt.startswith("/") and os.path.isdir(rt) and _private(rt) else os.path.expanduser("~/.cache")
    d = os.path.join(base, "agentj-waithint")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d if _private(d) else None


def hint(ev) -> str | None:
    """The hint text the first time in this session, else None. Any error → None."""
    try:
        if not isinstance(ev, dict) or not long_wait(ev.get("tool_name"), ev.get("tool_input")):
            return None
        sid = str(ev.get("session_id") or "")
        d = _marker_dir()
        if d is None:
            return None
        try:
            os.close(os.open(os.path.join(d, hashlib.sha256(sid.encode()).hexdigest()[:24]),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600))
        except FileExistsError:
            return None
        return HINT
    except Exception:  # noqa: BLE001 — a hint never costs a tool call
        return None
