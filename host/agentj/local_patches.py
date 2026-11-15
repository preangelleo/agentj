"""Bounded, read-only inspection; inventory metadata grants no execution authority."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

LIMIT = 256 * 1024
MAX_ROWS = 100
IDENT = re.compile(r'[pf][1-9][0-9]{0,9}\Z')
UNAVAILABLE = ['reapply', 'official_fixed_version_verification']


def _read(root: Path, relative: str) -> bytes:
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith('/') or any(p in ('.', '..') for p in parts) or '\\' in relative:
        raise ValueError('unsafe_path')
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise OSError('no_safe_reader')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    fd = os.open(root, flags)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(leaf)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
                raise ValueError('not_bounded_regular_file')
            data = os.read(leaf, LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('too_large')
            return data
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def _target(value) -> bool:
    if not isinstance(value, str) or not value or '\\' in value:
        return False
    p = PurePosixPath(value)
    return not p.is_absolute() and not any(x in ('.', '..', '.git', '.claude', '.codex', 'credentials')
        or x.startswith('.env') or x.endswith(('.pem', '.key')) for x in p.parts)


def _patch(root: Path, row: dict, index) -> tuple[str, str]:
    files = row.get('files')
    if not isinstance(files, list) or not files or len(files) > MAX_ROWS or not all(_target(f) for f in files):
        return 'conflict', 'invalid_targets'
    relative = row.get('patch')
    if not isinstance(relative, str) or not relative.startswith('patches/') or not relative.endswith('/fix.patch'):
        return 'conflict', 'invalid_patch_path'
    try:
        text = _read(root, relative).decode('utf-8')
    except FileNotFoundError:
        return 'conflict', 'patch_missing'
    except (OSError, ValueError, UnicodeError):
        return 'conflict', 'patch_unreadable'
    from . import privacy
    if '\x00' in text or 'GIT binary patch' in text or privacy.secret_at(text):
        return 'conflict', 'unsafe_patch'
    paths = []
    for line in text.splitlines():
        if line.startswith(('--- ', '+++ ')):
            path = line[4:].split('\t', 1)[0]
            if path == '/dev/null':
                continue
            if path.startswith(('a/', 'b/')):
                path = path[2:]
            if not _target(path) or path not in files:
                return 'conflict', 'patch_target_mismatch'
            paths.append(path)
    if not paths:
        return 'conflict', 'not_unified_patch'
    try:
        index.require_complete()
    except (OSError, AttributeError):
        return 'conflict', 'secret_scan_unavailable'
    if index.hits(text):
        return 'conflict', 'unsafe_patch'
    return 'pending_review', 'official_fix_unverified'


def _feature(row: dict) -> tuple[str, str]:
    source, entry = row.get('path'), row.get('entry')
    if row.get('kind') != 'symlink' or not all(isinstance(x, str) and x and '\x00' not in x for x in (source, entry)):
        return 'conflict', 'invalid_feature'
    source, entry = Path(source).expanduser(), Path(entry).expanduser()
    if not source.is_absolute() or not entry.is_absolute() or '..' in source.parts or '..' in entry.parts:
        return 'conflict', 'invalid_feature_paths'
    try:
        if not source.exists():
            return 'broken_link', 'source_missing'
        if not entry.is_symlink():
            return 'broken_link', 'entry_not_link'
        if entry.resolve(strict=True) != source.resolve(strict=True):
            return 'conflict', 'link_target_mismatch'
    except (OSError, RuntimeError):
        return 'broken_link', 'target_unreadable'
    return 'present_unverified', 'compatibility_unverified'


def check(root: Path | None = None) -> dict:
    root = root or Path.home() / '.agentj-local'
    out = {'status': 'ok', 'patches': [], 'features': [], 'counts': {}, 'unavailable': UNAVAILABLE[:]}
    try:
        raw = _read(root, 'MANIFEST.json')
    except FileNotFoundError:
        out['status'] = 'empty' if not root.exists() else 'missing_manifest'
        return out
    except (OSError, ValueError):
        out['status'] = 'unreadable_manifest'
        return out
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
            raise ValueError
        index = None
        if data.get('patches'):
            from .peer_guard import SecretIndex
            try:
                index = SecretIndex.build(workdir=os.getcwd())
                index.require_complete()
            except OSError:
                index = None
        for kind in ('patches', 'features'):
            rows = data.get(kind, [])
            if not isinstance(rows, list) or len(rows) > MAX_ROWS:
                raise ValueError
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not IDENT.fullmatch(row['id']) or row['id'] in seen:
                    raise ValueError
                seen.add(row['id'])
                status, reason = _patch(root, row, index) if kind == 'patches' else _feature(row)
                out[kind].append({'id': row['id'], 'status': status, 'reason': reason})
    except (ValueError, TypeError, UnicodeError, RecursionError):
        out.update(status='invalid_manifest', patches=[], features=[])
        return out
    for row in out['patches'] + out['features']:
        out['counts'][row['status']] = out['counts'].get(row['status'], 0) + 1
    return out


def cmd(a):
    if a.action == 'record':
        from . import local_record
        raise SystemExit(local_record.command(a))
    if a.action == 'relink':
        from . import local_relink
        raise SystemExit(local_relink.command(a))
    result = check()
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    print('本机补丁只读检查 / Local patch inspection: ' + result['status'])
    for row in result['patches'] + result['features']:
        print(f"  {row['id']}: {row['status']} ({row['reason']})")
    print('没有应用任何补丁；官方修复与兼容性仍待核实。 / No patch applied; official fixes and compatibility remain unverified.')
    print('reapply 尚未实现；relink 仅支持已登记 Codex owner-skill / reapply unavailable; relink supports registered Codex owner skills only.')


def add_parser(sub):
    parser = sub.add_parser('local', help='本机记录、检查与自定义技能链接 / local records, inspection and owner-skill links')
    parser.add_argument('action', choices=['check','record','relink'])
    parser.add_argument('id', nargs='?', help='relink: registered feature fN / 已登记功能编号')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--contract', help='record: local baseline/feature JSON contract / 本地基线或功能合同')
    parser.set_defaults(fn=cmd)
