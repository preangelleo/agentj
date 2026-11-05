"""Recoverable local deletion through the operating system's trash, never rm.

This primitive does not intercept arbitrary shell commands or grant permissions.
The native trash owns retention/restore; no automatic emptying is installed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

FINDER_SCRIPT = '''on run argv
    tell application "Finder"
        delete (POSIX file (item 1 of argv))
    end tell
end run'''


def move(raw: str) -> dict:
    """One path per native operation: report partial/unknown outcomes explicitly."""
    if sys.platform not in ('linux', 'darwin'):
        return {'status': 'unsupported'}
    if not raw or '\0' in raw:
        return {'status': 'refused'}
    # abspath normalizes dot segments without following the leaf symbolic link.
    path = Path(os.path.abspath(os.path.expanduser(raw)))
    if path == Path(path.anchor) or path == Path.home():
        return {'status': 'refused'}
    if sys.platform == 'darwin' and path.is_symlink():
        # Finder alias resolution is deliberately outside this first primitive.
        return {'status': 'refused'}
    argv = (['/usr/bin/gio', 'trash', '--', str(path)] if sys.platform == 'linux' else
            ['/usr/bin/osascript', '-e', FINDER_SCRIPT, '--', str(path)])
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'unknown'}  # it may already have moved; inspect native trash before retrying
    except OSError:
        return {'status': 'unavailable'}
    return {'status': 'trashed' if result.returncode == 0 else 'failed'}


def command(args):
    results = [move(raw) for raw in args.paths]
    if args.json:
        print(json.dumps({'results': results}, ensure_ascii=False))
    else:
        labels = {'trashed': '已移入系统废纸篓 / moved to system trash',
                  'unknown': '结果未知，请先查看系统废纸篓 / outcome unknown: inspect system trash first',
                  'unavailable': '系统废纸篓不可用，没有改用永久删除 / native trash unavailable; no permanent-delete fallback',
                  'unsupported': '此系统尚不支持 / unsupported platform',
                  'refused': '路径不支持，没有操作 / unsupported path; no operation',
                  'failed': '系统未确认成功，请查看原路径和废纸篓 / native operation failed; inspect source and trash'}
        for i, result in enumerate(results, 1):
            print(f"{i}: {labels[result['status']]}")
    if any(r['status'] != 'trashed' for r in results):
        raise SystemExit(1)


def add_parser(sub):
    p = sub.add_parser('trash', help='移入系统废纸篓（在系统废纸篓恢复）；不永久删除 / move to system trash, restore there')
    p.add_argument('paths', nargs='+', help='本机路径；-- 后可放以 - 开头的文件名 / local paths, use -- before option-like names')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=command)
