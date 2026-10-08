"""Owner-controlled Claude peer ingress. Never print settings or peer credentials."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid


class SettingsError(ValueError):
    pass


def path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def _load() -> tuple[dict, bytes | None]:
    p = path()
    if p.is_symlink():
        raise SettingsError("Claude settings is a symlink; edit its target yourself")
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return {}, None
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise SettingsError("Claude settings is not valid JSON; it was not changed") from None
    if not isinstance(doc, dict):
        raise SettingsError("Claude settings must be a JSON object; it was not changed")
    return doc, raw


def read() -> dict:
    return _load()[0]


def value() -> str:
    v = read().get("crossSessionInbound")
    return v if v in ("accept", "hold", "refuse") else "unset" if v is None else "invalid"


def update(change) -> bool:
    """Serialize our writers, back up original bytes 0600, fsync + atomic replace.

    The callback mutates only its owned keys. A no-op creates no backup.
    Original settings bytes remain local; contents are never printed.
    """
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(p.parent / ".agentj-inbound.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc, original = _load()
        before = json.dumps(doc, sort_keys=True)
        change(doc)
        if json.dumps(doc, sort_keys=True) == before:
            return False
        if original is not None:
            backup = p.with_name("settings.json.agentj-backup-" + uuid.uuid4().hex)
            with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                f.write(original)
                f.flush()
                os.fsync(f.fileno())
        fd, tmp = tempfile.mkstemp(prefix=".agentj-inbound-", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            # Detect an outside writer before replacing; leave its edits intact.
            current = p.read_bytes() if p.exists() else None
            if p.is_symlink() or current != original:
                raise SettingsError("Claude settings changed concurrently; retry after checking it")
            os.replace(tmp, p)
            dir_fd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return True


def set_enabled(enabled: bool) -> bool:
    return update(lambda doc: doc.update(crossSessionInbound="accept" if enabled else "hold"))

def command(args) -> int:
    if len(args) != 1 or args[0] not in ("on", "off", "status"):
        print("Usage: agentj config claude-inbound on|off|status")
        return 2
    try:
        if args[0] != "status":
            set_enabled(args[0] == "on")
        print("Claude crossSessionInbound: " + value())
        print("on 接受持本机 peer token 的进程消息；off 等你批准。组织/项目策略仍优先。 / "
              "on accepts messages authenticated with a local peer token; off holds them for approval. "
              "Managed/repository policies still apply.")
        return 0
    except (OSError, SettingsError):
        print("Claude settings 未修改或无法读取；检查 JSON、文件权限及并发编辑。 / "
              "Could not safely update/read Claude settings; check JSON, permissions and concurrent edits.")
        return 1
