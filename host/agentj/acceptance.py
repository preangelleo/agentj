"""Executable acceptance: data-bound evidence and read-only, offline research checks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import stat


def regular(root, name):
    root = Path(root).absolute()
    p = Path(name)
    if p.is_absolute() or not p.parts or any(x in ('..', '.') for x in p.parts):
        raise ValueError('artifact_path')
    target = root / p
    if any(x.is_symlink() for x in [root, *root.parents]) or any((root / Path(*p.parts[:i])).is_symlink() for i in range(1, len(p.parts)+1)):
        raise ValueError('artifact_symlink')
    if not target.is_file() or target.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('artifact_unavailable')
    return target


def read_regular(root, name, limit=64 * 1024 * 1024):
    """Pin every directory and the file itself; a raced link cannot redirect a Host read."""
    regular(root, name)
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(root).absolute().parts[1:] + Path(name).parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as f:
            before = os.fstat(f.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('artifact_unavailable')
            raw = f.read(limit + 1)
            after = os.fstat(f.fileno())
            if len(raw) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('evidence_changed')
            return raw, after
    finally:
        os.close(directory)


def head(root):
    """Read the local commit binding without running repository programs, hooks, or git."""
    try:
        metadata = Path(root) / '.git'
        if metadata.is_file():
            pointer = read_regular(root, '.git', 4096)[0].decode().strip()
            if not pointer.startswith('gitdir: '):
                return None
            metadata = Path(pointer[8:])
            if not metadata.is_absolute():
                metadata = Path(root) / metadata
            metadata = metadata.absolute()
            # Only Git's fixed worktree metadata layout, never an arbitrary pointer/file.
            if metadata.parts[-3:-1] != ('.git', 'worktrees') or not re.fullmatch(r'[A-Za-z0-9_-]+', metadata.name):
                return None
        value = read_regular(metadata, 'HEAD', 4096)[0].decode().strip()
        if value.startswith('ref: '):
            ref = value[5:]
            if not re.fullmatch(r'refs/[A-Za-z0-9_./-]+', ref) or '..' in ref:
                return None
            common = metadata.parent.parent if metadata.parent.name == 'worktrees' else metadata
            try:
                value = read_regular(common, ref, 4096)[0].decode().strip()
            except (ValueError, OSError):
                packed = read_regular(common, 'packed-refs', 1024 * 1024)[0].decode()
                value = next((line.split(' ', 1)[0] for line in packed.splitlines()
                              if line.endswith(' ' + ref) and re.fullmatch(r'[0-9a-f]{40,64} ' + re.escape(ref), line)), '')
        return value if re.fullmatch(r'[0-9a-f]{40,64}', value) else None
    except (OSError, ValueError):
        return None


def binding(root, artifacts, since=0, *, repository_root=None):
    rows, stale = {}, []
    for name in artifacts:
        raw, info = read_regular(root, name)
        rows[name] = hashlib.sha256(raw).hexdigest()
        if info.st_mtime < since:
            stale.append(name)
    repo = Path(repository_root or root)
    if repository_root and not Path(root).resolve().is_relative_to(repo.resolve()):
        raise ValueError('repository_scope')
    value = {'artifacts': rows, 'head': head(repo)}
    if (repo / '.git').exists() and not value['head']:
        raise ValueError('head_unavailable')
    return dict(value, artifact_digest=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), stale=stale)


def command(check):
    if not isinstance(check, str) or not check.strip() or len(check) > 4096 or '\0' in check:
        raise ValueError('check_shape')
    return check


def bubblewrap():
    for candidate in ('/usr/bin/bwrap', '/bin/bwrap'):
        p = Path(candidate).resolve()
        try:
            if all(x.lstat().st_uid == 0 and not x.lstat().st_mode & 0o022 for x in (p, *p.parents)) and p.is_file():
                return str(p)
        except OSError:
            continue
    raise ValueError('research_unavailable')


def research_argv(root, check):
    """Use the existing Linux research fence primitive with a minimal read-only filesystem.

    No home, host state, sockets, credentials, network, package helpers or host command execution.
    Unsupported platforms fail closed; no fallback to a host shell.
    """
    from . import fence
    if not sys.platform.startswith('linux') or fence.kind() != 'bubblewrap':
        raise ValueError('research_unavailable')
    executable = bubblewrap()
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or str(root) == '/' or root == Path(os.path.expanduser('~')).resolve():
        raise ValueError('workdir')
    a = [executable, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
         '--tmpfs', '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    for name in ('/usr', '/bin', '/lib', '/lib64'):
        if os.path.exists(name):
            a += ['--ro-bind', name, name]
    a += ['--ro-bind', str(root), '/work', '--chdir', '/work', '--setenv', 'PATH', '/usr/bin:/bin',
          '--setenv', 'HOME', '/tmp', '--setenv', 'TMPDIR', '/tmp']
    # Repository-local dot credentials and control sockets are not research inputs.
    count = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs) + files:
            count += 1
            if count > 100000:
                raise ValueError('research_input_limit')
            p = Path(base) / name
            mode = p.lstat().st_mode
            if name.startswith('.') or stat.S_ISSOCK(mode):
                target = '/work/' + p.relative_to(root).as_posix()
                if stat.S_ISDIR(mode):
                    dirs.remove(name)
                    a += ['--tmpfs', target]
                elif not stat.S_ISLNK(mode):
                    a += ['--ro-bind', '/dev/null', target]
    return a + ['--', '/bin/sh', '-c', command(check)]


def run(root, check):
    try:
        argv = research_argv(root, check)
        # Captured output is deliberately discarded: checks cannot exfiltrate read data in receipts.
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env={}, timeout=600)
        return {'status': 'pass' if p.returncode == 0 else 'fail', 'exit_code': p.returncode}
    except subprocess.TimeoutExpired:
        return {'status': 'fail', 'why': 'timeout'}
    except (OSError, ValueError):
        return {'status': 'unavailable', 'why': 'research_unavailable'}


def check(root, items, artifacts=(), since=0, *, rerun=False, repository_root=None):
    try:
        bound = binding(root, artifacts, since, repository_root=repository_root)
    except (ValueError, OSError):
        return {'result': 'fail', 'why': 'artifact_unavailable', 'acceptance': []}
    rows = []
    for i, item in enumerate(items):
        row = {'id': item.get('id', str(i)) if isinstance(item, dict) else str(i), 'status': 'unchecked'}
        if bound['stale']:
            row['status'] = 'stale'
        elif rerun and isinstance(item, dict) and item.get('check'):
            row.update(run(root, item['check']))
        rows.append(row)
    try:
        if binding(root, artifacts, since, repository_root=repository_root)['artifact_digest'] != bound['artifact_digest']:
            return {**bound, 'result':'fail', 'why':'binding_changed', 'acceptance':rows}
    except (ValueError, OSError):
        return {**bound, 'result':'fail', 'why':'binding_changed', 'acceptance':rows}
    return {**bound, 'acceptance': rows, 'result': 'fail' if any(r['status'] in ('fail', 'stale', 'unavailable') for r in rows) else 'ok'}
