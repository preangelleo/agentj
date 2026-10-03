"""The Agent name, host side (A3.2 contract §1, §3): one rename path shared by `agentj name` and `agentj admin`, and a tiny
blocking control-socket client (both run outside serve's event loop).

The name is a display string: it is never interpolated into a shell command, a file path, an agent prompt or HTML.
Linked → the Dashboard decides first (unique per company) and the local copy is written only on its 200; unlinked → local only.
"""
from __future__ import annotations

import json
import socket

from . import cloud
from .text import agent_name_problem, normalise_agent_name

CTL_TIMEOUT = 5


class ServeBusy(Exception):
    """serve's control socket exists and accepted, but did not answer (timeout, dropped connection, garbage). That is NOT
    "serve not running": callers must refuse, never fall back to editing state directly (review A32-02)."""


def ctl_call(st, req: dict, timeout: float | None = None) -> dict | None:
    """One request/answer on serve's control socket. None only when serve is certainly not running (no socket file, or
    connection refused); raises ServeBusy when it is there but does not answer in time."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(CTL_TIMEOUT if timeout is None else timeout)
    try:
        try:
            s.connect(str(st.sock_path))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        except OSError as e:
            raise ServeBusy(type(e).__name__) from None
        try:
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < 64 * 1024:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            obj = json.loads(buf)
        except (OSError, ValueError) as e:
            raise ServeBusy(type(e).__name__) from None
        if not isinstance(obj, dict):
            raise ServeBusy("bad_answer")
        return obj
    finally:
        s.close()


def announce(st) -> None:
    """After a local name change: a running serve sends a report soon (debounced); without serve, a linked host reports now."""
    try:
        if ctl_call(st, {"cmd": "agent_name_changed"}) is not None:
            return
    except ServeBusy:
        return   # serve is there: its heartbeat report carries the name
    if cloud.read_cloud(st):
        res = cloud.send_report(st, set(), {"count": 0, "since": None})
        if res.kind == "ok":
            st.log("report_ok", seq=res.seq, trigger="agent_name")
        elif res.kind == "fail":
            st.log("report_fail", status=res.status, trigger="agent_name")


def rename(st, raw, *, post=None) -> dict:
    """→ {"ok": True, "name", "linked"} or {"ok": False, "error", "suggestions": [...]}.
    error ∈ name_required · bad_name · name_taken · unreachable · not_bound · rate_limited · failed."""
    err = agent_name_problem(raw)
    if err:
        return {"ok": False, "error": err, "suggestions": []}
    name = normalise_agent_name(raw)
    linked = cloud.read_cloud(st) is not None
    if linked:
        res = cloud.rename(st, name, **({"post": post} if post else {}))
        if res.kind == "unlinked":       # `agentj unlink` ran in between: local only
            linked = False
        elif res.kind != "ok":
            st.log("agent_name_rename_refused", reason=res.kind, status=res.status)
            error = res.kind if res.kind in ("name_taken", "bad_name", "unreachable", "not_bound", "rate_limited") else "failed"
            return {"ok": False, "error": error, "suggestions": list(res.suggestions)}
        else:
            name = res.name
    name = st.set_agent_name(name)
    st.log("agent_name_set", kind="dashboard" if linked else "local")
    announce(st)
    return {"ok": True, "name": name, "linked": linked}


RENAME_MESSAGES = {
    "name_required": "名字不能是空的",
    "bad_name": "名字不合规：1–32 个字，不能有控制字符、不可见字符或换行（首尾空格会去掉，连续空格合成一个）",
    "name_taken": "这个账号里已经有叫这个名字的 Agent 了",
    "unreachable": "连不上账号后台，名字没改",
    "not_bound": "账号后台那边已经把这台电脑移除了，名字没改（运行 `agentj unlink` 清掉这台电脑上的账号记录后，可以只改这台电脑上的名字）",
    "rate_limited": "改名太频繁（每小时最多 20 次），名字没改，稍后再试",
    "failed": "账号后台没接受这次改名，名字没改",
}
