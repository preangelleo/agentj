"""Offline test fixture for the R-0055 journal and staged roster from d61aad7f23."""
import hashlib
import json
import os
import re
import stat
import tempfile

MARKER = "terminal_revoke_generation"
OP_ID = re.compile(r"orv-[a-f0-9]{16}")
HEX = re.compile(r"[a-f0-9]{32}")


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(path, data):
    fd, tmp = tempfile.mkstemp(prefix=".orv-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(data, ensure_ascii=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        sync(path.parent)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def raw(st):
    records = json.loads(st.devices_path.read_bytes())
    if not isinstance(records, dict) or not all(isinstance(v, dict) for v in records.values()):
        raise ValueError("invalid roster")
    return records


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stage_path(st, iid):
    if not OP_ID.fullmatch(iid):
        raise ValueError("invalid operation")
    return st.root / (".orv-roster-" + iid)


def prepare(st, iid, records):
    path = stage_path(st, iid)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(json.dumps(records, ensure_ascii=False).encode())
        stream.flush()
        os.fsync(stream.fileno())
        ident = os.fstat(stream.fileno())
    sync(st.root)
    return [ident.st_dev, ident.st_ino]


def witness(st, it):
    path = stage_path(st, it["id"])
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "consumed"
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or [info.st_dev, info.st_ino] != it["stage"]:
        return "unknown"
    return "present"


def valid(iid, it):
    return (isinstance(iid, str) and OP_ID.fullmatch(iid) and isinstance(it, dict)
            and it.get("id") == iid and it.get("v") == 2 and isinstance(it.get("device"), str)
            and isinstance(it.get("marker"), str) and HEX.fullmatch(it["marker"])
            and isinstance(it.get("digest"), str) and re.fullmatch(r"[a-f0-9]{64}", it["digest"])
            and isinstance(it.get("stage"), list) and len(it["stage"]) == 2
            and all(type(i) is int and i >= 0 for i in it["stage"])
            and it.get("phase") in ("prepared", "removed", "aborted")
            and type(it.get("used")) in (int, float) and it["used"] > 0
            and isinstance(it.get("recipients"), dict) and isinstance(it.get("pending"), list)
            and all(isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v)
                    for k, v in it["recipients"].items())
            and all(isinstance(k, str) and k in it["recipients"] for k in it["pending"])
            and isinstance(it.get("done"), list) and isinstance(it.get("name"), str)
            and all(isinstance(step, str) for step in it["done"]))
