"""P478: owner-approved, read-only file import; host-owned grants, never TCC grants.

The existing signed question protocol is the authority. The Agent socket may ask,
never select a decision. Apple Events execute only in serve, outside the fence.
"""
from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import pathlib
import secrets
import stat
import sys
import tempfile
import time
import threading

from . import jcards_files as J, media

MAX_BYTES = 8 * 1024**3
TTL_MAX = 86400
GRANTS_MAX = 32
_STATE_LOCK = threading.RLock()


class Refused(ValueError):
    pass


def path(st):
    return st.root / "file-access.json"


def read(st):
    try:
        data = json.loads(path(st).read_text())
        return data if isinstance(data, dict) and isinstance(data.get("grants"), list) else {"grants": []}
    except (OSError, ValueError):
        return {"grants": []}


def save(st, data):
    st.write_private(path(st), json.dumps(data).encode())


def allowed_path(st, raw, folder=False):
    """Reject dot segments, hidden/credential paths and every symbolic link.

    No existence/stat success is interpreted as proof of macOS authorization.
    macOS's /var alias is deliberately not accepted: use its canonical path.
    """
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or any(ord(c) < 32 for c in raw):
        raise Refused("path")
    p = pathlib.Path(os.path.expanduser(raw))
    if not p.is_absolute() or any(x in (".", "..") for x in raw.split("/")):
        raise Refused("outside")
    parts = p.parts[1:]
    if not parts or any(x.startswith(".") for x in parts) or media._denied(parts) or any(x.lower() in media.DENY_DIRS for x in parts):
        raise Refused("private")
    real = str(p)
    if J._bad_real(st.root, real):
        raise Refused("outside")
    cur = pathlib.Path(p.anchor)
    stamps = []
    for i, part in enumerate(parts):
        cur /= part
        s = cur.lstat()
        if stat.S_ISLNK(s.st_mode):
            raise Refused("symlink")
        if i < len(parts) - 1 or folder:
            if not stat.S_ISDIR(s.st_mode):
                raise Refused("not_a_folder")
        elif not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
            raise Refused("not_a_file")
        stamps.append((s.st_dev, s.st_ino, s.st_mode))
    if not folder and (s.st_size > MAX_BYTES):
        raise Refused("too_big")
    return p, (tuple(stamps), s.st_size, s.st_mtime_ns)


def grants(st):
    now = time.time()
    return [g for g in read(st)["grants"] if isinstance(g, dict) and isinstance(g.get("path"), str)
            and type(g.get("expires")) in (int, float) and (g["expires"] == 0 or now < g["expires"])]


def grant(st, folder, expires):
    checked, _ = allowed_path(st, folder, folder=True)
    folder = str(checked)
    if type(expires) is not int or expires != 0 and not time.time() < expires <= time.time() + TTL_MAX:
        raise Refused("expiry")
    with _STATE_LOCK:
        data = read(st)
        rows = [g for g in grants(st) if g["path"] != folder]
        if len(rows) >= GRANTS_MAX:
            raise Refused("too_many")
        data["grants"] = [*rows, {"path": folder, "expires": expires}]
        save(st, data)


def revoke(st, folder):
    with _STATE_LOCK:
        data = read(st)
        data["grants"] = [g for g in grants(st) if g["path"] != folder]
        save(st, data)


def has_grant(st, source):
    return any(J._under(str(source), g["path"]) for g in grants(st))


def tcc_status(st):
    value = read(st).get("tcc")
    return value if isinstance(value, dict) and isinstance(value.get("folders"), dict) else None


def snapshot(st):
    c = st.agent_config() or {}
    return {"grants": grants(st), "workdir": c.get("dir"),
            "fence": "data_protection" if c.get("fence", True) else "owner_selected_unfenced",
            "tcc": tcc_status(st) if sys.platform == "darwin" else None,
            "mac": sys.platform == "darwin"}


def prepare(st):
    """Explicit local onboarding only. Runs via serve for the service's TCC identity.

    Directory enumeration may prompt; status is an observation, not a TCC DB read.
    The public AppleEvents preflight requests Automation without copying any file.
    """
    if sys.platform != "darwin":
        return {"ok": True, "why": "not_macos"}
    result = {"at": int(time.time()), "folders": {}, "automation": "unknown", "fda": "optional_unknown"}
    for name in ("Downloads", "Desktop", "Documents"):
        try:
            with os.scandir(pathlib.Path.home() / name) as entries:
                next(entries, None)
            result["folders"][name] = "readable"
        except FileNotFoundError:
            result["folders"][name] = "missing"
        except OSError:
            result["folders"][name] = "blocked_or_unknown"
    from . import mac_file_copy
    result["automation"] = "available" if mac_file_copy.automation(ask=True) else "blocked_or_unknown"
    with _STATE_LOCK:
        data = read(st)
        data["tcc"] = result
        save(st, data)
    return {"ok": True, "tcc": result}


def finder_copy(st, source, dest, before):
    from . import mac_file_copy as M
    def validate(resolved):
        if str(source) != resolved or allowed_path(st, resolved)[1] != before:
            raise Refused("changed")
    try:
        M.duplicate(source, dest, before[0][-1], validate)
    except (M.NativeError, OSError, AttributeError) as e:
        raise Refused(str(e) if isinstance(e, M.NativeError) else "finder_unavailable") from None


def copy_file(st, raw, stopped=lambda: False, authorized=lambda: True):
    """Stage outside the fence, validate, then publish a new copy to the workdir.

    This does not grant TCC or alter the source. Finder is attempted only on a
    permission refusal, never for a missing file or arbitrary I/O error.
    """
    source, before = allowed_path(st, raw)
    work, _ = allowed_path(st, (st.agent_config() or {}).get("dir"), folder=True)
    if stopped():
        raise Refused("stopped")
    method = "direct"
    with tempfile.TemporaryDirectory(prefix="fetch-", dir=st.root) as stage:
        staged = pathlib.Path(stage) / source.name
        try:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except PermissionError as e:
            if sys.platform != "darwin" or e.errno not in (errno.EACCES, errno.EPERM):
                raise Refused("os_permission") from None
            finder_copy(st, source, pathlib.Path(stage), before)
            method = "finder"
        else:
            with os.fdopen(fd, "rb") as src, staged.open("xb") as dst:
                s = os.fstat(src.fileno())
                if (s.st_dev, s.st_ino, s.st_mode) != before[0][-1] or not stat.S_ISREG(s.st_mode):
                    raise Refused("changed")
                remaining = before[1]
                while remaining:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise Refused("changed")
                    dst.write(chunk)
                    remaining -= len(chunk)
                if src.read(1):
                    raise Refused("changed")
        if allowed_path(st, raw)[1] != before or stopped():
            raise Refused("changed_or_stopped")
        fd = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as src:
            s = os.fstat(src.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size != before[1] or s.st_size > MAX_BYTES:
                raise Refused("changed")
            _, secret = media._hash_and_scan(src.fileno(), s.st_size, True, False, owner=True)
            if secret:
                raise Refused("key_content")
            src.seek(0)
            # Anchor the output directory by fd; no symlink destination or overwrite.
            wd = os.open(work, J._DIRFLAGS)
            try:
                if not J._same(wd, str(work)) or stopped() or not authorized():
                    raise Refused("changed_or_stopped")
                name = "import-" + secrets.token_hex(12)
                os.mkdir(name, mode=0o700, dir_fd=wd)
                outdir = os.open(name, J._DIRFLAGS, dir_fd=wd)
                try:
                    out = os.open(source.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=outdir)
                    try:
                        with os.fdopen(out, "wb") as dst:
                            while chunk := src.read(1024 * 1024):
                                if stopped() or not authorized():
                                    raise Refused("changed_or_stopped")
                                dst.write(chunk)
                            if stopped() or not authorized():
                                raise Refused("changed_or_stopped")
                    except BaseException:
                        with contextlib.suppress(OSError):
                            os.unlink(source.name, dir_fd=outdir)
                        with contextlib.suppress(OSError):
                            os.rmdir(name, dir_fd=wd)
                        raise
                finally:
                    os.close(outdir)
            finally:
                os.close(wd)
    return {"ok": True, "path": str(work / name / source.name), "method": method}


class Files:
    def __init__(self, host):
        self.host, self.st = host, host.st
        self.lock = asyncio.Lock()

    async def fetch(self, req):
        if self.lock.locked():
            return {"ok": False, "why": "busy"}
        async with self.lock:
            cancelled = threading.Event()
            try:
                from . import provenance
                if provenance.untrusted_turn(getattr(self.host, "agent", None)):
                    raise Refused("owner_turn_required")
                source, stamp = await asyncio.to_thread(allowed_path, self.st, req.get("path"))
                if self.host.stopped():
                    raise Refused("stopped")
                persistent = has_grant(self.st, source)
                if not persistent:
                    from .approvals import norm_questions
                    en = self.host.lang == "en"
                    qs = norm_questions([{"question": ("Copy this file to the work folder, keeping the original?\n" if en else
                          "将这个文件复制到工作文件夹，保留原件？\n") + str(source) + "\n" +
                          ("Folder: " if en else "文件夹：") + str(source.parent),
                          "options": [{"label": x} for x in (("Allow once", "Always allow folder", "Deny") if en else
                                                                   ("本次允许", "以后这个文件夹都允许", "拒绝"))]}])
                    if qs is None:
                        raise Refused("path_too_long")
                    decision, picks = await self.host.question(qs)
                    if decision != "answer" or picks not in ([[1]], [[2]]):
                        raise Refused("denied")
                    if self.host.stopped() or (await asyncio.to_thread(allowed_path, self.st, str(source)))[1] != stamp:
                        raise Refused("changed_or_stopped")
                    if picks == [[2]]:
                        grant(self.st, str(source.parent), 0)
                        persistent = True
                res = await asyncio.to_thread(copy_file, self.st, str(source), lambda: cancelled.is_set() or self.host.stopped(),
                                              lambda: not persistent or has_grant(self.st, source))
            except asyncio.CancelledError:
                # to_thread cannot stop the worker: withdraw its authority to publish.
                cancelled.set()
                raise
            except (Refused, OSError, media.Gone) as e:
                res = {"ok": False, "why": str(e) if isinstance(e, Refused) else "io"}
            from .approvals import input_digest
            self.st.log("files_fetch", result="ok" if res["ok"] else res["why"], input_sha256=input_digest(req.get("path")))
            return res

    async def on_phone(self, s, obj):
        from . import controls
        action = obj.get("t")
        if action == "files_get":
            await self.host.send_app(s, {"t": "files_state", "r": obj.get("r"), **snapshot(self.st)})
            return
        target = {k: obj.get(k) for k in ("op", "path", "expires")}
        why = controls.check(self.st, self.host.nonces, self.host.channel, s.device, obj, "files_write", target)
        if not why:
            try:
                if target["op"] == "add" and not self.host.stopped():
                    grant(self.st, target["path"], target["expires"])
                elif target["op"] == "remove":
                    revoke(self.st, target["path"])
                else:
                    raise Refused("stopped_or_invalid")
            except (OSError, Refused):
                why = "invalid"
        controls.log(self.st, action="files_write", device=s.device, result=why or "ok", obj=target,
                     sig=obj.get("sig"), n=obj.get("n"), ts=obj.get("ts"))
        await self.host.send_app(s, {"t": "ctl_res", "r": obj.get("r"), "ok": why is None, "why": why})


def cmd_files(a):
    from .elevate import client_request
    from .state import State
    req = {"t": "files", "op": a.action, "path": getattr(a, "path", None)}
    # No local fallback: an Agent cannot turn a local CLI flag into owner approval.
    if a.action == "prepare":
        from . import fence
        if fence.inside() or not a.at_computer or not sys.stdin.isatty():
            res = {"ok": False, "why": "owner_at_computer_required"}
        else:
            from .names import ctl_call
            res = ctl_call(State(), {"cmd": "files_prepare"}, timeout=260) or {"ok": False, "why": "not_running"}
    else:
        res = client_request(State(), req, timeout=320)
    print(json.dumps(res, ensure_ascii=False))
    raise SystemExit(0 if res.get("ok") else 1)


def add_parser(sub):
    p = sub.add_parser("files", help="本机文件访问 / owner-approved local file access")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("status", help="只读状态 / read-only status")
    prep = commands.add_parser("prepare", help="主人在电脑前时准备 Mac 权限 / prepare Mac permissions while at the computer")
    prep.add_argument("--at-computer", action="store_true")
    commands.add_parser("fetch", help="经手机批准复制文件 / copy a file after phone approval").add_argument("path")
    p.set_defaults(fn=cmd_files)
