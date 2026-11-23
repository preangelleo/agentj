"""Validate the local uv installation before repair or replacement."""

from collections import deque
import contextlib
import grp
import logging
import os
from pathlib import Path
import pwd
import shlex
import stat
import sys


class UnsafeInstallation(ValueError):
    def __init__(self, blocked, *, tree=None):
        super().__init__("unsafe_installation_permissions")
        self.paths = [str(path) for path in blocked]
        args = shlex.join(self.paths)
        if tree is not None and len(blocked) > 1 and all(reason == "mode" for reason in blocked.values()):
            # A whole venv under one umask: one recursive line, not thousands of arguments.
            self.fix = "chmod -R go-w -- " + shlex.quote(str(tree))
        elif all(reason in ("mode", "acl") for reason in blocked.values()):
            commands = [f"chmod go-w -- {args}"]
            acl = [str(path) for path, reason in blocked.items() if reason == "acl"]
            if acl:
                commands.insert(0, "setfacl -b -- " + shlex.join(acl))
                directories = shlex.join(path for path in acl if Path(path).is_dir())
                if directories:
                    commands.insert(1, "setfacl -k -- " + directories)
            self.fix = " && ".join(commands)
        else:
            self.fix = "namei -l -- " + args

    def lines(self):
        shown = self.paths if len(self.paths) <= 11 else self.paths[:10]
        more = [f"... and {len(self.paths) - 10} more"] if len(shown) < len(self.paths) else []
        return [*(f"Unsafe installation path: {path!r}" for path in shown), *more, f"fix: {self.fix}"]


def _private_group(gid):
    """An Ubuntu-style user private group, proven using local NSS identities."""
    if not sys.platform.startswith("linux") or os.getuid() != os.geteuid() or not os.getuid() or not gid:
        return False
    try:
        # Some network identity providers cannot enumerate all of their members.
        sources = {}
        for line in Path("/etc/nsswitch.conf").read_text().splitlines():
            key, sep, value = line.partition("#")[0].partition(":")
            if sep:
                sources[key.strip()] = value.split()
        if any("files" not in sources.get(key, ()) or set(sources[key]) - {"files", "systemd"}
               for key in ("passwd", "group")):
            return False
        if set(sources.get("initgroups", ())) - {"files", "systemd"}:
            return False
        owner = pwd.getpwuid(os.getuid())
        group = grp.getgrgid(gid)
        return (owner.pw_gid == gid and group.gr_name == owner.pw_name
                and all(member == owner.pw_name for member in group.gr_mem)
                and all(other.pw_name == owner.pw_name or
                        (other.pw_gid != gid and gid not in os.getgrouplist(other.pw_name, other.pw_gid))
                        for other in pwd.getpwall()))
    except (KeyError, OSError, ValueError):
        return False


def _linux_acl(fd):
    return any(name.startswith("system.") for name in os.listxattr(fd))


def _write_problem(info, mode, fd, groups):
    if sys.platform.startswith("linux") and _linux_acl(fd):
        return "acl"
    if not mode & 0o022:
        return None
    if mode & 0o002 or info.st_uid != os.getuid():
        return "mode"
    if info.st_gid not in groups:
        groups[info.st_gid] = _private_group(info.st_gid)
    return None if groups[info.st_gid] else "mode"


def _remove_group_write(path, fd, note):
    mode = stat.S_IMODE(os.fstat(fd).st_mode)
    if mode & 0o020:
        os.fchmod(fd, mode & ~0o020)
        if note:
            note(f"Removed group write from {str(path)!r}")


def secure_paths(targets, *, note=None):
    """Check the owner/root path chains and strip g+w on proven private Linux paths.

    Targets are already canonical. Inspect with no-follow descriptors, and validate
    all chains before any chmod. The root-owned sticky temp directory exception
    applies to ancestors only.
    """
    uid = os.getuid()
    blocked, checked, tighten, groups = {}, set(), {}, {}
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with contextlib.ExitStack() as repairs:
        for target, require_owner in targets:
            target = Path(target)
            if not target.is_absolute():
                blocked[target] = "identity"
                continue
            with contextlib.ExitStack() as chain:
                parent = None
                for path in (*reversed(target.parents), target):
                    if path in blocked:
                        break
                    try:
                        fd = os.open(path if parent is None else path.name, flags, dir_fd=parent)
                        chain.callback(os.close, fd)
                        parent = fd
                        info = os.fstat(fd)
                        mode = stat.S_IMODE(info.st_mode)
                        if (info.st_uid not in (uid, 0) or (path == target and require_owner and info.st_uid != uid)
                                or not (stat.S_ISDIR(info.st_mode) or (path == target and stat.S_ISREG(info.st_mode)))):
                            blocked[path] = "identity"
                            break
                        sticky = path != target and info.st_uid == 0 and mode & stat.S_ISVTX
                        if sticky or path in checked:
                            continue
                        if problem := _write_problem(info, mode, fd, groups):
                            blocked[path] = problem
                            if problem == "mode" and info.st_uid == uid and not mode & 0o002 and stat.S_ISDIR(info.st_mode):
                                # Refused all the same. Only the report goes on, so that one fix line names
                                # every directory of this chain instead of one per run.
                                continue
                            break
                        if mode & 0o020:
                            saved = os.dup(fd)
                            repairs.callback(os.close, saved)
                            tighten[path] = saved
                        checked.add(path)
                    except OSError:
                        blocked[path] = "identity"
                        break
        if blocked:
            raise UnsafeInstallation(blocked)
        for path, fd in tighten.items():
            _remove_group_write(path, fd, note or logging.getLogger(__name__).info)


def resolve_trusted(path, *, note=None):
    """Resolve only symlinks whose containing path and owner have been checked."""
    path = Path(path).absolute()
    cursor = Path(path.anchor)
    parts = deque(path.parts[1:])
    links = 0
    while parts:
        part = parts.popleft()
        if part == "..":
            secure_paths([(cursor, False)], note=note)
            if not cursor.is_dir():
                raise UnsafeInstallation({cursor: "identity"})
            cursor = cursor.parent
            continue
        current = cursor / part
        if not current.is_symlink():
            cursor = current
            continue
        links += 1
        secure_paths([(cursor, False)], note=note)
        info = current.lstat()
        if links > 40 or not stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise UnsafeInstallation({current: "identity"})
        target = current.readlink()
        if target.is_absolute():
            cursor = Path(target.anchor)
        parts.extendleft(reversed(target.parts[1:] if target.is_absolute() else target.parts))
    secure_paths([(cursor, False)], note=note)
    return cursor


def secure_tree(prefix, python, *, note=None):
    """Check actual venv files as well as the launch paths. Never follow a tree symlink."""
    if not sys.platform.startswith("linux"):
        return
    python = resolve_trusted(python, note=note)
    blocked, groups, changed = {}, {}, 0
    uid = os.getuid()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    def fail(err):
        raise UnsafeInstallation({Path(err.filename or prefix): "identity"}) from None

    for root, dirs, files, parent in os.fwalk(prefix, follow_symlinks=False, onerror=fail):
        for name in [*dirs, *files]:
            path = Path(root) / name
            try:
                old = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(old.st_mode):
                    dest = path.resolve(strict=True)
                    if (old.st_uid != uid or (not dest.is_relative_to(prefix) and dest != python)
                            or resolve_trusted(path, note=note) != dest):
                        blocked[path] = "identity"
                elif stat.S_ISDIR(old.st_mode) or stat.S_ISREG(old.st_mode):
                    fd = os.open(name, flags, dir_fd=parent)
                    try:
                        info = os.fstat(fd)
                        mode = stat.S_IMODE(info.st_mode)
                        if info.st_uid != uid or not os.path.samestat(info, old):
                            blocked[path] = "identity"
                        elif problem := _write_problem(info, mode, fd, groups):
                            blocked[path] = problem
                        elif mode & 0o020:
                            _remove_group_write(path, fd, None)
                            changed += 1
                    finally:
                        os.close(fd)
                else:
                    blocked[path] = "identity"
            except (OSError, RuntimeError) as exc:
                if isinstance(exc, FileNotFoundError) and path.suffix == ".pyc":
                    continue  # Python can discard its own transient bytecode cache.
                blocked[path] = "identity"
            if path in blocked and name in dirs:
                dirs.remove(name)
    if changed and note:
        note(f"Removed group write from {changed} entries under {str(prefix)!r}")
    if blocked:
        raise UnsafeInstallation(blocked, tree=prefix)


def repair_line(targets, tree):
    """Advice only, never a verdict: one command that removes every group/other write bit this account holds on the
    directories leading to `targets` and inside `tree`, after the ACL removal for each of them that carries one.
    Nothing is changed or trusted here; the checks run again."""
    uid, chain, acl = os.getuid(), {}, {}
    tree = Path(os.path.realpath(tree))

    def has_acl(path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return False
        try:
            return sys.platform.startswith("linux") and _linux_acl(fd)
        except OSError:
            return False
        finally:
            os.close(fd)

    for target in targets:
        real = Path(os.path.realpath(target))
        for path in (*reversed(real.parents), real):
            try:
                info = path.lstat()
            except OSError:
                break
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or path.is_relative_to(tree):
                continue
            if has_acl(path):
                acl[path] = "acl"
            elif info.st_mode & 0o022:
                chain[path] = None
    loose = False
    for root, dirs, files in os.walk(tree):
        for name in (".", *dirs, *files):
            path = Path(os.path.normpath(os.path.join(root, name)))
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != uid:
                continue
            loose = loose or bool(info.st_mode & 0o022)
            if len(acl) < 200 and has_acl(path):
                acl[path] = "acl"
    parts = ([UnsafeInstallation(acl).fix] if acl else []) + \
        (["chmod go-w -- " + shlex.join(str(path) for path in chain)] if chain else []) + \
        (["chmod -R go-w -- " + shlex.quote(str(tree))] if loose else [])
    return " && ".join(parts)


def upgrade_paths(home, entry, prefix, python, *, note=None):
    """Paths trusted by the bootstrap before it runs the installed interpreter."""
    entry, prefix = Path(entry), Path(prefix)
    canonical_entry = resolve_trusted(entry, note=note)
    if canonical_entry != prefix / "bin/agentj" or prefix.name != "agentj":
        raise UnsafeInstallation({entry: "identity"})
    if entry.is_symlink() and entry.lstat().st_uid != os.getuid():
        raise UnsafeInstallation({entry: "identity"})
    return [(resolve_trusted(home, note=note), True), (resolve_trusted(entry.parent, note=note), True),
            (canonical_entry, True), (prefix / "uv-receipt.toml", True),
            (resolve_trusted(python, note=note), False)]


def bootstrap_permissions(argv):
    try:
        home, entry, prefix, python = argv
        def show(line):
            print(line, file=sys.stderr)
        secure_paths(upgrade_paths(home, entry, prefix, python, note=show), note=show)
        secure_tree(Path(prefix), python, note=show)
    except UnsafeInstallation as exc:
        lines = exc.lines()
        if exc.fix.startswith(("chmod ", "setfacl ")):
            try:
                state = os.environ.get("AGENTJ_STATE_DIR") or Path(home) / ".local/state/agentj"
                whole = repair_line([home, Path(entry).parent, prefix, state, Path(home) / ".local/share/agentj"], prefix)
            except (OSError, RuntimeError, ValueError):
                whole = ""
            if whole:
                lines[-1:] = ["fix (everything this repair checks, in one line; then run the same repair command again): "
                              + whole]
        print(*lines, sep="\n", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError):
        print("Unable to verify the existing installation paths.", file=sys.stderr)
        return 1
    return 0
