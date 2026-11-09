"""0287: closed To-do rows, fd-safe and copy-before-remove under the To-do lock.

Each archived row keeps its original ID and unknown fields. Main-file rows win over
staged archive copies: interrupted archive transactions cannot hide current data.
"""
from __future__ import annotations
import contextlib
import json
import os
import uuid
from . import jcards as cards

PARTS = ('cards-data', 'todo', 'archive')


def ids(root):
    from .jcards_todo import ID_RE
    try:
        with cards._dirfd(root, PARTS) as d:
            return {n[:-5] for n in os.listdir(d) if n.endswith('.json') and ID_RE.fullmatch(n[:-5])}
    except FileNotFoundError:
        return set()


def get(root, iid):
    from .jcards_todo import ID_RE, normalize
    if not ID_RE.fullmatch(iid):
        raise OSError('invalid archive id')
    with cards._dirfd(root, PARTS) as d:
        raw, cut = cards._read_fd(os.open(iid + '.json', os.O_RDONLY | os.O_NONBLOCK | cards._NOFOLLOW, dir_fd=d), cards.DATA_MAX)
    if raw is None or cut:
        raise OSError('unreadable archive')
    try:
        row = json.loads(raw)
        n = normalize(row, 0)
        if not isinstance(row, dict) or not n or n['id'] != iid:
            raise ValueError()
        return row
    except (ValueError, TypeError):
        raise OSError('invalid archive') from None


def atomic(d, name, body):
    temp = '.todo-' + uuid.uuid4().hex + '.tmp'
    try:
        cards._write_at(d, temp, body)
        fd = os.open(temp, os.O_RDONLY | cards._NOFOLLOW, dir_fd=d)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, name, src_dir_fd=d, dst_dir_fd=d)
        os.fsync(d)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=d)


def store(root, row):
    from .jcards_todo import ID_RE
    iid = row['id']
    if not ID_RE.fullmatch(iid):
        raise OSError('invalid archive id')
    body = (json.dumps(row, ensure_ascii=False, indent=1) + '\n').encode()
    if len(body) > cards.DATA_MAX:
        raise OSError('archive row too large')
    with cards._dirfd(root, PARTS, create=True) as d:
        atomic(d, iid + '.json', body)


def remove(root, iid):
    from .jcards_todo import ID_RE
    if not ID_RE.fullmatch(iid):
        raise OSError('invalid archive id')
    try:
        with cards._dirfd(root, PARTS) as d:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(iid + '.json', dir_fd=d)
                os.fsync(d)
    except FileNotFoundError:
        pass


def capacity(root, card, raw):
    from .jcards_todo import load
    size, limit = len(raw or b''), card['doc']['data'].get('maxBytes', cards.DATA_DEFAULT)
    active, _ = load(raw)
    archived = ids(root) - {x['id'] for x in active}
    return {'bytes': size, 'maxBytes': limit, 'near_limit': size >= limit * .8 or len(active) >= cards.ITEMS_MAX * .8,
            'archived': len(archived)}
