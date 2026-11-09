"""Explicit owner-registered UV commands; execution stays in the caller's sandbox."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import threading

MAX_BYTES = 65536
MAX_FUNCTIONS = 64
_LOCK = threading.Lock()
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")


class Refused(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def record(name, argv, cwd):
    from . import privacy
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise Refused("invalid_name")
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 128 or
            any(not isinstance(s, str) or not s or any(ord(c) < 32 for c in s) for s in argv) or
            len(json.dumps(argv).encode()) > 8192):
        raise Refused("invalid_argv")
    sensitive_flag = re.compile(r"--?(?:password|passwd|passphrase|token|secret|api[-_]?key|authorization)\Z", re.I)
    labelled = any(sensitive_flag.fullmatch(arg.split("=", 1)[0]) and
                   (("=" in arg and bool(arg.split("=", 1)[1])) or i + 1 < len(argv))
                   for i, arg in enumerate(argv))
    if labelled or privacy.secret_at(" ".join(argv)) is not None:
        raise Refused("secret_in_argv_use_environment_or_file")
    if not isinstance(cwd, str) or not pathlib.Path(cwd).is_absolute():
        raise Refused("invalid_working_directory")
    value = {"name": name, "argv": list(argv), "cwd": str(pathlib.Path(cwd).resolve())}
    return {**value, "digest": digest(value)}


def _load(st):
    path = st.root / "functions.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Refused("registry_permissions")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Refused("registry_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"version", "functions"} or value["version"] != 1:
        raise Refused("registry_shape")
    rows = value["functions"]
    if not isinstance(rows, dict) or len(rows) > MAX_FUNCTIONS:
        raise Refused("registry_shape")
    for name, row in rows.items():
        if not isinstance(row, dict) or set(row) != {"name", "argv", "cwd", "digest"} or record(name, row["argv"], row["cwd"]) != row:
            raise Refused("registry_changed_or_invalid")
    return rows


def _save(st, rows):
    from . import diskq
    st.check_perms()
    raw = json.dumps({"version": 1, "functions": rows}, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > MAX_BYTES:
        raise Refused("registry_too_large")
    diskq.write(st.root / "functions.json", raw)


def _finish(st, op, name, before, proposed):
    with _LOCK:
        rows = _load(st)
        if rows.get(name) != before:
            raise Refused("function_changed_during_approval")
        if op == "add":
            if len(rows) >= MAX_FUNCTIONS:
                raise Refused("registry_full")
            rows[name] = proposed
            _save(st, rows)
        elif op == "remove":
            del rows[name]
            _save(st, rows)
        return proposed


async def handle(host, req: dict, *, peer=None) -> dict:
    """Called only by the existing same-UID elevate socket; request identities are ignored."""
    try:
        if peer is None:
            raise Refused("peer_unverified")
        op = req.get("op")
        if op == "list" and set(req) == {"t", "op"}:
            rows = await asyncio.to_thread(_load, host.st)
            return {"ok": True, "functions": [{"name": n, "digest": row["digest"]} for n, row in sorted(rows.items())]}
        if op not in ("add", "run", "remove") or set(req) != ({"t", "op", "name", "argv", "cwd"} if op == "add" else {"t", "op", "name"}):
            raise Refused("request_shape")
        name = req.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise Refused("invalid_name")
        rows = await asyncio.to_thread(_load, host.st)
        before = rows.get(name)
        if op == "add":
            if before is not None:
                raise Refused("already_registered_remove_first")
            proposed = record(name, req["argv"], req["cwd"])
        else:
            if before is None:
                raise Refused("not_registered")
            proposed = before
        answer = await host.ask("agentj fn " + op, proposed, require_passkey=True,
                                always_ask=True, grant_info=True, batch=False)
        grant = answer.get("grant_info") or {}
        if answer.get("behavior") != "allow":
            raise Refused(answer.get("reason") or "not_approved")
        if (grant.get("uv") is not True or not grant.get("rid") or not grant.get("device") or
                not grant.get("sign_pub") or host.st.sign_key(grant["device"]) != grant["sign_pub"] or
                host.stopped() or host.stopping.is_set()):
            raise Refused("fresh_owner_uv_required")
        approved = await asyncio.to_thread(_finish, host.st, op, name, before, proposed)
        host.st.log("uv_function", operation=op, name=name, digest=approved["digest"], rid=grant["rid"])
        return {"ok": True, "operation": op, "function": approved}
    except (Refused, OSError, ValueError, KeyError, TypeError) as error:
        why = str(error) if isinstance(error, Refused) else "registry_unavailable"
        return {"ok": False, "why": why}


def command(a) -> int:
    from . import elevate, names
    from .state import State
    req = {"t": "fn", "op": a.action}
    if a.action != "list":
        req["name"] = a.name
    if a.action == "add":
        argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
        try:
            value = record(a.name, argv, os.getcwd())
        except (Refused, OSError) as error:
            print(str(error))
            return 2
        req.update(argv=value["argv"], cwd=value["cwd"])
    st = State()
    res = elevate.client_request(st, req, timeout=900, path=names.elevate_sock(st))
    if not res.get("ok"):
        print("Function refused: " + str(res.get("why") or res.get("result") or "unavailable"))
        return 2
    if a.action == "list":
        print(json.dumps(res["functions"], ensure_ascii=False, indent=2))
        return 0
    if a.action == "run":
        try:
            approved = res["function"]
            if record(a.name, approved["argv"], approved["cwd"]) != approved:
                raise Refused("invalid_approved_command")
            return subprocess.run(approved["argv"], cwd=approved["cwd"], shell=False).returncode
        except (Refused, OSError, KeyError, TypeError):
            print("Function execution unavailable; not retried.")
            return 2
    print("Function " + a.action + ": " + a.name)
    return 0


def uv_command(a) -> int:
    from . import elevate, names, uv_list
    from .state import State
    st = State()
    res = elevate.client_request(st, {"t": "fn", "op": "list"}, timeout=10, path=names.elevate_sock(st))
    rows = [{**row, "kind": "built_in"} for row in uv_list.entries()]
    if res.get("ok"):
        rows += [{**row, "command": "agentj fn run " + row["name"], "kind": "registered",
                  "reason": "Owner-registered high-risk function; fresh owner verification on every execution."}
                 for row in res["functions"]]
    print(json.dumps({"entries": rows, "protected_paths": uv_list.protected_paths(cwd=os.getcwd()), "registered_functions_available": bool(res.get("ok"))}, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 2


def add_parser(sub):
    parser = sub.add_parser("fn", help="高危函数：注册/执行每次验证本人 / registered functions require owner verification")
    modes = parser.add_subparsers(dest="action", required=True)
    add = modes.add_parser("add", help="注册当前目录下的命令 / register a command in this directory")
    add.add_argument("name")
    add.add_argument("argv", nargs=argparse.REMAINDER)
    add.set_defaults(fn=lambda a: sys.exit(command(a)))
    for action in ("run", "remove"):
        child = modes.add_parser(action)
        child.add_argument("name")
        child.set_defaults(fn=lambda a: sys.exit(command(a)))
    modes.add_parser("list").set_defaults(fn=lambda a: sys.exit(command(a)))
    uv = sub.add_parser("uv", help="指纹清单 / explicit verification list")
    uv.add_subparsers(dest="action", required=True).add_parser("list").set_defaults(fn=lambda a: sys.exit(uv_command(a)))
