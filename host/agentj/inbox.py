"""Inbox — where the fenced Agent finds what the phone uploaded (PROTOCOL §10.4, PROMPT-33; relay `bridge/inbox.py`).

Path: `<agent folder>/.agentj/inbox/<YYYY-MM-DD>/<HHMMSS>-<6 hex>-<slug>.<ext>`. Inside the Agent's own folder on purpose:
the fence replaces the state dir with an empty tmpfs, and a path outside the folder would make Claude Code / OpenCode ask a
card for every read (or need a flag that widens the session, Invariant 11). In its folder all three harnesses read it under
their normal rules.

The Agent can write in its folder, so it could plant a symlink where we are about to write. Every component is therefore
opened relative to the previous one with O_NOFOLLOW | O_DIRECTORY, must be a directory owned by this user, and the file is
created with O_CREAT | O_EXCL | O_NOFOLLOW (0600) as `.part-<bid>` and renamed inside the same directory fd. Anything else →
`unsafe_inbox`, nothing written. `.agentj/` is 0700 and holds `.gitignore` = `*`, so uploads never enter the customer's git.
The client's file name never chooses a path: the slug is a scrubbed display fragment, the extension comes from the MIME.

Retention: day folders older than RETAIN_DAYS go at serve start and daily; over QUOTA the oldest day goes first, never today's.
Cleanup walks and deletes through the same O_NOFOLLOW dir-fd chain as the write (P33-C01): agentj runs unfenced, so a link
the Agent planted at .agentj, inbox or below must never steer a delete outside — a linked .agentj / inbox removes nothing.
The Agent may read, move or delete its inbox files — they are the human's gift to it, nothing of agentj's.
"""
from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import shutil
import stat
import time
import unicodedata

DOT = ".agentj"
INBOX = "inbox"
RETAIN_DAYS = 30
QUOTA = 2 * 1024 ** 3
SLUG_MAX = 48
_SLUG_DROP = re.compile(r"[^A-Za-z0-9一-鿿._-]+")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

# MIME → stored extension (= relay inbox.ALLOWED). The name the phone sends never decides the extension.
ALLOWED = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic",
    "image/heif": ".heif", "application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md", "text/csv": ".csv",
    "application/json": ".json", "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "audio/aac": ".aac", "audio/wav": ".wav", "audio/x-wav": ".wav",
}


class Unsafe(Exception):
    """A component of the inbox path is a symlink, not a directory or not ours: nothing is written (`unsafe_inbox`)."""


def kind_of(mime: str) -> str:
    return "image" if mime.startswith("image/") else "audio" if mime.startswith("audio/") else "file"


def slugify(name) -> str:
    """A display fragment of the client's name — never a path (relay inbox.slugify)."""
    n = str(name or "").replace("\x00", "")
    n = unicodedata.normalize("NFKC", n).replace("\\", "/").split("/")[-1]
    n = os.path.splitext(n)[0]
    n = _SLUG_DROP.sub("_", n).strip("._-")
    if not n or set(n) <= {".", "_", "-"}:
        n = "upload"
    return n[:SLUG_MAX]


def _own_dir(fd: int) -> None:
    s = os.fstat(fd)
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid():
        raise Unsafe("not a directory of this user")


def _open_dir(name: str, parent: int | None, create: bool, mode: int = 0o700) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode, dir_fd=parent)
        except FileExistsError:
            pass
        fd = os.open(name, flags, dir_fd=parent)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise Unsafe(name) from None
        raise
    try:
        _own_dir(fd)
    except Unsafe:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def dot_dir(workdir: str, create: bool = True):
    """fd of `<workdir>/.agentj` (0700, `.gitignore` = `*`), opened without following links."""
    root = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        _own_dir(root)
        d = _open_dir(DOT, root, create)
    finally:
        os.close(root)
    try:
        if create:
            os.fchmod(d, 0o700)
            try:
                g = os.open(".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
                with os.fdopen(g, "w") as f:
                    f.write("*\n")
            except FileExistsError:
                pass
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise Unsafe(".gitignore") from None
                raise
        yield d
    finally:
        os.close(d)


def read_file(workdir: str, name: str, limit: int) -> bytes | None:
    """`<workdir>/.agentj/<name>` (a plain file, no link anywhere), at most limit + 1 bytes; None when missing.
    Raises Unsafe when a component is a link or not ours."""
    try:
        with dot_dir(workdir, create=False) as d:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=d)
            except FileNotFoundError:
                return None
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise Unsafe(name) from None
                raise
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise Unsafe(name)
                return f.read(limit + 1)
    except FileNotFoundError:
        return None


def place(workdir: str, src_path: str, name: str, mime: str, bid: str, now: float | None = None) -> str:
    """Copy the finished upload at src_path into the inbox; returns the absolute path the Agent is told. Raises Unsafe /
    OSError; on any failure nothing is left behind but the temporary `.part-<bid>` (removed)."""
    ext = ALLOWED[mime]
    t = time.localtime(now if now is not None else time.time())
    day = time.strftime("%Y-%m-%d", t)
    final = f"{time.strftime('%H%M%S', t)}-{secrets.token_hex(3)}-{slugify(name)}{ext}"
    with dot_dir(workdir) as d:
        ib = _open_dir(INBOX, d, True)
        try:
            dd = _open_dir(day, ib, True)
        finally:
            os.close(ib)
        tmp = f".part-{bid}"
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp, dir_fd=dd)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600,
                         dir_fd=dd)
            try:
                with os.fdopen(fd, "wb") as out, open(src_path, "rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                os.rename(tmp, final, src_dir_fd=dd, dst_dir_fd=dd)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp, dir_fd=dd)
                raise
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise Unsafe(tmp) from None
            raise
        finally:
            os.close(dd)
    return os.path.join(os.path.realpath(workdir), DOT, INBOX, day, final)


def inbox_dir(workdir: str) -> str:
    return os.path.join(workdir, DOT, INBOX)


@contextlib.contextmanager
def _inbox_fd(workdir: str):
    """fd of `<workdir>/.agentj/inbox` through the same O_NOFOLLOW chain as `place` (every component a directory of this
    user); None when it does not exist yet. Raises Unsafe when `.agentj` or `inbox` is a link / not ours (P33-C01)."""
    try:
        with dot_dir(workdir, create=False) as d:
            try:
                ib = _open_dir(INBOX, d, False)
            except FileNotFoundError:
                yield None
                return
            try:
                yield ib
            finally:
                os.close(ib)
    except FileNotFoundError:
        yield None


def _scan_day(ib: int, name: str) -> tuple[int, int] | None:
    """(files, bytes) of one day folder opened under ib without following links; None when it is not a plain folder."""
    try:
        dd = _open_dir(name, ib, False)
    except (OSError, Unsafe):
        return None
    files = size = 0
    try:
        with os.scandir(dd) as it:
            for e in it:
                with contextlib.suppress(OSError):
                    s = e.stat(follow_symlinks=False)
                    if stat.S_ISREG(s.st_mode):
                        files += 1
                        size += s.st_size
    finally:
        os.close(dd)
    return files, size


def _days_fd(ib: int) -> list[tuple[str, int, int]]:
    try:
        names = sorted(n for n in os.listdir(ib) if _DAY.fullmatch(n))
    except OSError:
        return []
    out = []
    for n in names:
        r = _scan_day(ib, n)
        if r is not None:
            out.append((n, *r))
    return out


def _log_unsafe(log, op: str) -> None:
    if log:
        with contextlib.suppress(Exception):
            log("inbox_unsafe", op=op)


def days(workdir: str, log=None) -> list[tuple[str, int, int]]:
    """[(day, files, bytes)] oldest first — day folders reached through the O_NOFOLLOW chain only (a linked `.agentj`,
    `inbox` or day folder is never followed: [] / skipped)."""
    try:
        with _inbox_fd(workdir) as ib:
            return [] if ib is None else _days_fd(ib)
    except (Unsafe, OSError):
        _log_unsafe(log, "list")
        return []


def usage(workdir: str) -> int:
    return sum(b for _, _, b in days(workdir))


def _rmtree_fd(parent: int, name: str) -> None:
    """Remove `name` under the directory fd `parent` without following any link: links and files are unlinked (never their
    targets), folders are opened O_NOFOLLOW and emptied first — the walk never goes through a path string."""
    try:
        fd = _open_dir(name, parent, False)
    except FileNotFoundError:
        return
    except (Unsafe, OSError):
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=parent)
        return
    try:
        with os.scandir(fd) as it:
            entries = list(it)
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                _rmtree_fd(fd, e.name)
            else:
                with contextlib.suppress(OSError):
                    os.unlink(e.name, dir_fd=fd)
    finally:
        os.close(fd)
    with contextlib.suppress(OSError):
        os.rmdir(name, dir_fd=parent)


def unlink_placed(path: str) -> bool:
    """Delete one file `place` wrote (a staged upload nobody was told about) through the O_NOFOLLOW chain — the absolute
    path `<agent folder>/.agentj/inbox/<day>/<name>` is only parsed, never followed below the agent folder. False when the
    chain is unsafe or the file is gone."""
    p = str(path or "")
    day, name = os.path.basename(os.path.dirname(p)), os.path.basename(p)
    ib_path = os.path.dirname(os.path.dirname(p))
    workdir = os.path.dirname(os.path.dirname(ib_path))
    if not os.path.isabs(p) or os.path.basename(ib_path) != INBOX or os.path.basename(os.path.dirname(ib_path)) != DOT \
            or not _DAY.fullmatch(day) or not name or name.startswith("."):
        return False
    try:
        with _inbox_fd(workdir) as ib:
            if ib is None:
                return False
            dd = _open_dir(day, ib, False)
            try:
                os.unlink(name, dir_fd=dd)
                return True
            finally:
                os.close(dd)
    except (Unsafe, OSError):
        return False


def _remove_days(workdir: str, pick, log, op: str) -> list[tuple[str, int, int]]:
    """Open the inbox once (O_NOFOLLOW chain), list its day folders, remove those pick(days) returns — all relative to that
    one fd. Unsafe → `inbox_unsafe` logged, nothing removed."""
    try:
        with _inbox_fd(workdir) as ib:
            if ib is None:
                return []
            gone = pick(_days_fd(ib))
            for d in gone:
                _rmtree_fd(ib, d[0])
            return gone
    except (Unsafe, OSError):
        _log_unsafe(log, op)
        return []


def retain(workdir: str, keep: set[str] | None = None, now: float | None = None, log=None) -> list[str]:
    """Remove day folders older than RETAIN_DAYS; then, over QUOTA, the oldest days (never today's). keep = paths still
    staged (announced to nobody yet) — a day holding one is not removed by the quota rule. Returns the removed days."""
    now = now if now is not None else time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    cutoff = time.strftime("%Y-%m-%d", time.localtime(now - RETAIN_DAYS * 86400))
    held = {os.path.basename(os.path.dirname(p)) for p in (keep or ())}

    def pick(ds):
        gone = [x for x in ds if x[0] < cutoff]
        rest = [x for x in ds if x[0] >= cutoff]
        total = sum(b for _, _, b in rest)
        for x in rest:
            if total <= QUOTA:
                break
            if x[0] == today or x[0] in held:
                continue
            gone.append(x)
            total -= x[2]
        return gone
    return [d for d, _, _ in _remove_days(workdir, pick, log, "retain")]


def clear(workdir: str, log=None) -> int:
    """`agentj inbox clear`: every day folder; returns how many files went. Raises Unsafe when `.agentj` / `inbox` is a
    link or not ours (nothing removed)."""
    try:
        with _inbox_fd(workdir) as ib:
            if ib is None:
                return 0
            ds = _days_fd(ib)
            for d, _, _ in ds:
                _rmtree_fd(ib, d)
            return sum(f for _, f, _ in ds)
    except Unsafe:
        _log_unsafe(log, "clear")
        raise
