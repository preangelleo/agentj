"""The Agent name, host side (A3.2 contract §1, §3): one rename path shared by `agentj name` and `agentj admin`, and a tiny
blocking control-socket client (both run outside serve's event loop).

The name is a display string: it is never interpolated into a shell command, a file path, an agent prompt or HTML.
Linked → the Dashboard decides first (unique per company) and the local copy is written only on its 200; unlinked → local only.
"""
from __future__ import annotations

import errno
import json
import socket

from . import cloud
from .text import NEW_NAME_UNITS, agent_name_units, new_agent_name_problem, normalise_agent_name

CTL_TIMEOUT = 5


class ServeBusy(Exception):
    """Control transport failed or returned no usable response; the host state is unknown.
    Callers must refuse, never edit state directly. response() distinguishes permission/connect failures from timeout."""


    def response(self) -> dict:
        cause = str(self)
        code = 'sandbox_blocked' if cause == 'PermissionError' else ('serve_busy' if cause in ('TimeoutError', 'ValueError', '') else 'host_unreachable')
        return transport_failure(code)


def transport_failure(code: str) -> dict:
    """Fixed diagnostics only: never echo exception paths, requests or credentials."""
    detail = ("隔离层或文件权限阻止连接主机；请在已配对遥控器「设置 → 沟通渠道 → 邮件通道」设置授权码，"
              "其它操作请在 Agent J 主机终端重试（agentj service status）。 / The sandbox or file permissions blocked the host connection. "
              "Set the app password on the paired remote in Settings → Channels → E-mail; retry other operations in the computer terminal (agentj service status).") if code == 'sandbox_blocked' else (
              "主机通道没有响应，无法确认操作结果；请在 Agent J 主机终端运行 agentj service status，再按提示处理。 / "
              "The host channel did not answer; the operation outcome is unknown. Run agentj service status in the computer terminal and follow its instructions.")
    return {'ok': False, 'error': code, 'detail': detail}


def ctl_call(st, req: dict, timeout: float | None = None, max_bytes: int = 64 * 1024, progress=None) -> dict | None:
    """One request/answer on serve's control socket. None only when serve is certainly not running (no socket file, or
    connection refused); raises ServeBusy when it is there but does not answer in time. A4 (P139): a command that changes
    safety state (ctl_auth.SENSITIVE) first asks for a one-time claim on the same connection; serve refuses it to an Agent."""
    from .ctl_auth import SENSITIVE
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(CTL_TIMEOUT if timeout is None else timeout)
    buf = [b""]

    def line() -> dict:
        while b"\n" not in buf[0] and len(buf[0]) < max_bytes:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf[0] += chunk
        one, _, buf[0] = buf[0].partition(b"\n")
        obj = json.loads(one)
        if not isinstance(obj, dict):
            raise ValueError("bad_answer")
        return obj

    try:
        try:
            s.connect(str(st.sock_path))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        except OSError as e:
            raise ServeBusy('PermissionError' if e.errno in (errno.EPERM, errno.EACCES) else type(e).__name__) from None
        try:
            if req.get("cmd") in SENSITIVE and "claim" not in req:
                s.sendall(b'{"cmd": "claim"}\n')
                claim = line()
                if claim.get("ok") and isinstance(claim.get("claim"), str):
                    req = {**req, "claim": claim["claim"]}
            s.sendall((json.dumps(req) + "\n").encode())
            obj = line()
            while progress and obj.get("sent"):     # P570b QA L3: serve's card is out, the answer follows
                progress()
                obj = line()
        except (OSError, ValueError) as e:
            raise ServeBusy('PermissionError' if isinstance(e, OSError) and e.errno in (errno.EPERM, errno.EACCES) else type(e).__name__) from None
        return obj
    finally:
        s.close()


def control_unavailable() -> dict:
    """A missing socket inside isolation cannot establish whether the host is running."""
    from . import fence
    if fence.inside():
        return {"ok": False, "error": "host_unreachable", "detail":
                "隔离会话内看不到主机控制通道，无法确认运行状态；请在已配对手机上重新加载或重新绑定 Telegram。 / "
                "The host control channel is hidden in this isolated session; its running state is unknown. "
                "Use Reload or Rebind Telegram on the paired phone."}
    return {"ok": False, "error": "not_running", "detail":
            "Agent J is not running: start it (agentj service start), then try again."}


def elevate_sock(st):
    """P339: the Agent-facing elevate.sock — reachable inside the fence, where control.sock is hidden. Inside a fence the
    path serve exported wins (the fence unsets AGENTJ_STATE_DIR, so State() differs there); outside, the state's."""
    import os
    from pathlib import Path
    from . import elevate, fence
    env = os.environ.get(elevate.ENV)
    return Path(env) if env and fence.inside() else st.perm_dir / elevate.SOCK_NAME


def serve_answers(st, timeout: float = 5) -> bool:
    """P339: is serve running? Asked of serve itself — control.sock, then (inside the Agent fence, where control.sock is
    hidden and launchd / systemd are out of sight) the Agent-facing elevate.sock. Any answer there counts: only serve listens
    on it (an older serve refuses the unknown request, which still proves it runs)."""
    try:
        if ctl_call(st, {"cmd": "status"}, timeout) is not None:
            return True
    except ServeBusy:
        pass                   # busy, or (macOS fence) not connectable: ask the other socket
    try:
        from .elevate import client_request
        return client_request(st, {"t": "serve_ping"}, timeout=timeout, path=elevate_sock(st)).get("result") != "unavailable"
    except Exception:  # noqa: BLE001 — a status helper, never a crash
        return False


def seen_status(st, svc: dict | None = None, answering: bool | None = None) -> dict:
    """P339: service.status() + what serve's own sockets say. When the manager query cannot see the service (inside the fence:
    launchd's gui domain / the systemd user manager are invisible — `not_loaded` / `unknown`) but serve answers, the service
    IS running: active = "active", seen_by = "socket" (manager_active keeps the manager's word). Always adds "serve"."""
    from . import fence, service
    svc = dict(service.status() if svc is None else svc)
    ans = serve_answers(st) if answering is None else answering
    svc["serve"] = "answering" if ans else "silent"
    if ans and svc.get("active") != "active" and (fence.inside() or svc.get("active") in ("not_loaded", "unknown")):
        svc.update(manager_active=svc.get("active"), active="active", seen_by="socket")
    return svc


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
            from . import preferences
            preferences.adopt_account_language(st, res.account)   # A1: a newer account language
        elif res.kind == "fail":
            st.log("report_fail", status=res.status, trigger="agent_name")


def rename(st, raw, *, post=None, notify=True) -> dict:
    """→ {"ok": True, "name", "linked"} or {"ok": False, "error", "suggestions": [...]}.
    error ∈ name_required · bad_name · name_too_long · name_taken · unreachable · not_bound · rate_limited · failed.
    P154: a new name is held to the short-name rule (text.new_agent_name_problem); the stored one is never re-checked.
    P181: serve itself (a rename from the phone) passes notify=False and tells the Dashboard / phones directly."""
    err = new_agent_name_problem(raw)
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
    if notify:
        announce(st)
    return {"ok": True, "name": name, "linked": linked}


def too_long_message(name) -> str:
    """P393: the too-long refusal with the given name's own length against the limit (both languages, one line)."""
    n = normalise_agent_name(name) if isinstance(name, str) else None
    n = n if n is not None else str(name)
    u = f"{round(agent_name_units(n), 1):g}"
    return (f"名字太长：「{n}」算下来是 {u}，最多 {NEW_NAME_UNITS}（中文每个字算 1.6，英文字母、数字、空格和符号每个算 1；"
            f"纯中文最多 8 个字，纯英文最多 13 个字母） / Name too long: \"{n}\" counts {u}, the limit is {NEW_NAME_UNITS} "
            "(each Chinese character counts 1.6, each letter, digit, space or symbol 1: up to 8 Chinese characters or 13 letters)")


RENAME_MESSAGES = {
    "name_required": "名字不能是空的",
    "bad_name": "名字不合规：不能有控制字符、不可见字符或换行（首尾空格会去掉，连续空格合成一个）",
    "name_too_long": "名字太长：长度最多 13，中文每个字算 1.6，其他字符每个算 1（纯中文最多 8 个字，纯英文最多 13 个字母）",
    "name_taken": "这个账号里已经有叫这个名字的 Agent 了",
    "unreachable": "连不上账号后台，名字没改",
    "not_bound": "账号后台那边已经把这台Agent J 主机移除了，名字没改（运行 `agentj unlink` 清掉这台Agent J 主机上的账号记录后，可以只改这台Agent J 主机上的名字）",
    "rate_limited": "改名太频繁（每小时最多 20 次），名字没改，稍后再试",
    "failed": "账号后台没接受这次改名，名字没改",
}
