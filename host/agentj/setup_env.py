"""Existing P224 fixed service environment editor."""
import os, re, stat, tempfile, contextlib
from .setup_ops import OpError
def env_edit(path: str, set_: dict | None = None, drop: tuple = ()) -> None:
    """Set / remove NON-secret lines (a base URL, a binary path) in the service env file: owner's regular file, never a
    symlink, 0600, every other byte kept, atomic rename."""
    set_ = set_ or {}
    for k, v in set_.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", k) or any(c in v for c in "\n\r\0'"):
            raise OpError("io")
    old = ""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    except OSError:
        raise OpError("io") from None
    else:
        with os.fdopen(fd, "r", errors="replace") as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise OpError("io")
            old = f.read()
    names = set(set_) | set(drop)
    out, seen = [], set()
    for ln in old.splitlines():
        k = ln.strip().removeprefix("export ").partition("=")[0].strip()
        if k in names:
            if k in set_ and k not in seen:
                out.append(f"{k}='{set_[k]}'")
                seen.add(k)
            continue
        out.append(ln)
    out += [f"{k}='{v}'" for k, v in set_.items() if k not in seen]
    data = ("\n".join(out) + "\n").encode() if out else b""
    d = os.path.dirname(path)
    tfd, tmp = tempfile.mkstemp(dir=d, prefix=".agentj-env-")
    try:
        with os.fdopen(tfd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
    os.chmod(path, 0o600)


