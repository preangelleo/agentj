"""P429 C4: strict Codex patch grammar, independently implemented (no shell/patch dependency).

The host parses only paths/text for approval; all file access happens in the fenced toolbox.
Exact contexts must be unique; no whitespace fuzz. All edits are prepared before any file
replacement. Files replace atomically, but a multi-file patch is not an OS transaction.
"""
from __future__ import annotations

import os
import secrets
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .tools import ToolError

MAX_PATCH = 256 * 1024
MAX_FILES = 64
MAX_FILE = 2 * 1024 * 1024


@dataclass
class Change:
    kind: str
    path: str
    dest: str | None = None
    lines: list[str] = field(default_factory=list)


def _path(p: str) -> str:
    if not p or p.startswith('/') or '\\' in p or any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise ToolError('Patch paths must be relative project paths.')
    if any(x in ('', '.', '..') for x in p.split('/')) or str(PurePosixPath(p)) != p:
        raise ToolError('Patch paths cannot contain empty, . or .. components.')
    return p


def parse(text: str) -> list[Change]:
    if not isinstance(text, str) or len(text.encode()) > MAX_PATCH or '\x00' in text:
        raise ToolError('patch must be text, at most 256 KiB, without NUL.')
    lines = text.splitlines()
    if not lines or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch':
        raise ToolError('Use *** Begin Patch and *** End Patch, without surrounding text.')
    out, used, i = [], set(), 1
    while i < len(lines) - 1:
        h = lines[i]
        kind = next((k for k in ('Add', 'Update', 'Delete') if h.startswith(f'*** {k} File: ')), None)
        if not kind:
            raise ToolError(f'Invalid patch header at line {i + 1}.')
        c = Change(kind, _path(h[len(f'*** {kind} File: '):]))
        i += 1
        if kind == 'Update' and i < len(lines) - 1 and lines[i].startswith('*** Move to: '):
            c.dest = _path(lines[i][13:])
            i += 1
        while i < len(lines) - 1:
            line = lines[i]
            if line.startswith(('*** Add File: ', '*** Update File: ', '*** Delete File: ')):
                break
            if kind == 'Delete' or (kind == 'Add' and not line.startswith('+')) or (kind == 'Update' and not (
                    line.startswith((' ', '+', '-', '@@ ')) or line in ('@@', '*** End of File'))):
                raise ToolError(f'Invalid {kind} patch line {i + 1}.')
            c.lines.append(line)
            i += 1
        if kind != 'Delete' and not c.lines and not c.dest:
            raise ToolError(f'{kind} patch needs content.')
        for p in (c.path, c.dest) if c.dest else (c.path,):
            if p in used:
                raise ToolError(f'Duplicate patch path {p}; combine changes into one hunk.')
            used.add(p)
        out.append(c)
        if len(out) > MAX_FILES:
            raise ToolError('At most 64 files per patch.')
    if not out:
        raise ToolError('Patch is empty.')
    return out


def paths(changes):
    """Each effect's original classifier shape, including both sides of a move."""
    for c in changes:
        yield ('Write' if c.kind == 'Add' else 'Delete' if c.kind == 'Delete' or c.dest else 'Edit'), c.path
        if c.dest:
            yield 'Write', c.dest


def updated(text: str, lines: list[str]) -> str:
    src = text.splitlines()
    newline = text.endswith('\n')
    pos, i = 0, 0
    while i < len(lines):
        marker = lines[i]
        if marker != '@@' and not marker.startswith('@@ '):
            raise ToolError('Update sections must start with @@ or @@ <context>.')
        i += 1
        if marker.startswith('@@ '):
            hits = [n for n in range(pos, len(src)) if src[n] == marker[3:]]
            if len(hits) != 1:
                raise ToolError('Section context must be unique; read the file and add context.')
            pos = hits[0] + 1
        old, new, eof, changed = [], [], False, False
        while i < len(lines) and not lines[i].startswith('@@'):
            s = lines[i]
            i += 1
            if s == '*** End of File':
                eof = True
                break
            if s[0] in ' -':
                old.append(s[1:])
            if s[0] in ' +':
                new.append(s[1:])
            changed = changed or s[0] in '+-'
        if not changed:
            raise ToolError('Update section must change at least one line.')
        hits = [n for n in range(pos, len(src) - len(old) + 1) if src[n:n + len(old)] == old
                and (not eof or n + len(old) == len(src))]
        if not old:
            hits = [len(src)]  # Codex insertion-only section appends
        if len(hits) != 1:
            raise ToolError('Old lines must match one unique location; read the file and add exact context.')
        start = hits[0]
        src[start:start + len(old)] = new
        pos = start + len(new)
    return '\n'.join(src) + ('\n' if src and newline else '')


def apply(root: str, patch: str) -> str:
    changes = parse(patch)
    with ExitStack() as stack:
        dirs = {}

        def directory(parts, create=False):
            key = tuple(parts)
            if key in dirs:
                return dirs[key]
            if not parts:
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            else:
                parent = directory(parts[:-1], create)
                if create:
                    try:
                        os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            dirs[key] = fd
            stack.callback(os.close, fd)
            return fd

        def snapshot(path, create=False):
            parts = path.split('/')
            fd, name = directory(parts[:-1], create), parts[-1]
            try:
                f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except FileNotFoundError:
                return fd, name, None, None
            with os.fdopen(f, 'rb') as stream:
                s = os.fstat(stream.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > MAX_FILE:
                    raise ToolError(f'{path}: use a regular, unlinked text file at most 2 MiB.')
                b = stream.read(MAX_FILE + 1)
                if len(b) > MAX_FILE or b'\0' in b:
                    raise ToolError(f'{path}: not a bounded text file.')
            return fd, name, b, s

        prepared = []
        try:
            for c in changes:
                src = snapshot(c.path, c.kind == 'Add')
                old = src[2]
                if (c.kind == 'Add') != (old is None):
                    raise ToolError(f'{c.path}: Add requires a new file; Update/Delete require an existing file.')
                dst = snapshot(c.dest, True) if c.dest else src
                if c.dest and dst[2] is not None:
                    raise ToolError(f'{c.dest}: move destination already exists.')
                new = None if c.kind == 'Delete' else ('\n'.join(s[1:] for s in c.lines) + '\n' if c.kind == 'Add'
                       else updated(old.decode('utf-8'), c.lines)).encode()
                prepared.append((c, src, dst, new))
            # Prepare temp files before committing any replacement. No in-place writes.
            temps = []
            try:
                for c, src, dst, new in prepared:
                    temp = None
                    if new is not None:
                        temp = '.agentj-patch-' + secrets.token_hex(12)
                        f = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst[0])
                        temps.append((dst[0], temp))
                        with os.fdopen(f, 'wb') as stream:
                            stream.write(new)
                            os.fchmod(stream.fileno(), stat.S_IMODE(src[3].st_mode) if src[3] else 0o600)
                            stream.flush()
                            os.fsync(stream.fileno())
                # Validate every original after staging all outputs, before committing any file.
                def same(a, b):
                    def version(v):
                        return None if v is None else (v.st_dev, v.st_ino, v.st_mode, v.st_size, v.st_mtime_ns, v.st_ctime_ns)
                    return a[2] == b[2] and version(a[3]) == version(b[3])

                for c, src, dst, new in prepared:
                    for path, entry in ((c.path, src), (c.dest, dst)) if c.dest else ((c.path, src),):
                        if not same(snapshot(path), entry):
                            raise ToolError('File changed during patch preparation; read again and retry.')
                for c, src, dst, new in prepared:
                    if new is not None:
                        # Find the staged file in preparation order, including mixed Delete operations.
                        fd, temp = temps[0]
                        os.replace(temp, dst[1], src_dir_fd=fd, dst_dir_fd=dst[0])
                        temps.pop(0)
                    if c.kind == 'Delete' or c.dest:
                        os.unlink(src[1], dir_fd=src[0])
            finally:
                for fd, temp in temps:
                    try:
                        os.unlink(temp, dir_fd=fd)
                    except FileNotFoundError:
                        pass
        except (OSError, UnicodeError) as e:
            raise ToolError(f'Patch not completed ({type(e).__name__}); inspect files before retrying.') from None
    return 'Applied patch: ' + ', '.join(c.kind + ' ' + c.path + (' -> ' + c.dest if c.dest else '') for c in changes)
