"""Official Notebook v2: readable Markdown, separate title/body/date, byte-exact legacy backup."""
from __future__ import annotations
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from . import jcards as cards, privacy

MAGIC = b'<!-- agentj-notes-v2 -->\n'
HEADER = b'## Note\n'


def encode(rows):
    out = [MAGIC]
    for row in rows:
        out.append(HEADER + ('Title: ' + json.dumps(row['title'], ensure_ascii=False) +
                   '\nDate: ' + json.dumps(row['date']) + '\nContent: |\n').encode())
        out.append((''.join('    ' + line + '\n' for line in row['text'].split('\n'))).encode())
    return b''.join(out)


def decode(raw):
    if not raw.startswith(MAGIC):
        raise ValueError('not_v2')
    rows = []
    for block in re.split(rb'(?m)^## Note\n', raw[len(MAGIC):])[1:]:
        title, date, marker, *body = [x + '\n' for x in block.decode('utf-8').split('\n')[:-1]]
        if not title.startswith('Title: ') or not date.startswith('Date: ') or marker != 'Content: |\n' or not body or any(not x.startswith('    ') or not x.endswith('\n') for x in body):
            raise ValueError('bad_data')
        row = {'title': json.loads(title[7:]), 'date': json.loads(date[6:]),
               'text': ''.join(x[4:] for x in body)[:-1]}
        if not all(isinstance(v, str) for v in row.values()):
            raise ValueError('bad_data')
        rows.append(row)
    if encode(rows) != raw:
        raise ValueError('bad_data')
    return rows


def legacy(raw):
    from .jcards_data import _spans
    rows = []
    for _, a, b in _spans(raw):
        text = re.sub(r'^(?:[-+*]|\d+[.)])\s+', '', raw[a:b].decode('utf-8', 'strict').rstrip('\r\n'))
        text = re.sub(r'\r?\n(?:  |\t)', '\n', text)
        match = re.match(r'^(\d{4}-\d{2}-\d{2}) (?:· )?', text)
        rows.append({'title': '', 'date': match[1] if match else '', 'text': text[match.end():] if match else text})
    return rows


def snapshot(raw):
    rows, counts, entries = decode(raw), {}, []
    for row in rows[:cards.ITEMS_MAX]:
        digest = hashlib.sha256(encode([row])).hexdigest()[:20]
        counts[digest] = counts.get(digest, 0) + 1
        safe = {k: privacy.redact(v, owner=True) for k, v in row.items()}
        entries.append({'id': f'n-{digest}-{counts[digest]}', **safe, 'editable': safe == row})
    return {'revision': hashlib.sha256(raw).hexdigest(), 'entries': entries}


def _replace(d, name, body):
    tmp = '.note-' + uuid.uuid4().hex + '.tmp'
    try:
        cards._write_at(d, tmp, body)
        os.replace(tmp, name, src_dir_fd=d, dst_dir_fd=d)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=d)


def change(root, c, op='list', entry=None, revision=None, text=None, title=None, date=None):
    fail = lambda why, **extra: {'ok': False, 'id': 'notes', 'why': why, 'problem': why, 'text': '', 'entries': [], **extra}
    if op not in ('list', 'add', 'edit', 'delete'):
        return fail('bad_op')
    if op in ('add', 'edit'):
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16384 or '\x00' in text:
            return fail('bad_text')
        if title is not None and (not isinstance(title, str) or len(title.encode()) > 1024 or any(x in title for x in '\r\n\x00')):
            return fail('bad_title')
        if date is not None:
            try:
                if not isinstance(date, str) or datetime.date.fromisoformat(date).isoformat() != date:
                    return fail('bad_date')
            except ValueError:
                return fail('bad_date')
    try:
        with cards._dirfd(root, ('cards-data', 'notes'), create=True) as d:
            fd = os.open('.notes-write.lock', os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | cards._NOFOLLOW, 0o600, dir_fd=d)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return fail('refused')
                fcntl.flock(fd, fcntl.LOCK_EX)
                raw, problem, cut = cards._read(root, c)
                if problem not in (None, 'empty') or cut:
                    return fail('refused', **({'cut': True} if cut else {}))
                raw = raw or b''
                old = raw
                migrating = not raw.startswith(MAGIC)
                rows = legacy(raw) if migrating else decode(raw)
                # Mutations of a pre-migration snapshot must refresh, never target a guessed new id.
                snap = snapshot(encode(rows))
                if op in ('edit', 'delete') and (migrating or revision != snap['revision']):
                    return fail('conflict')
                if op == 'add':
                    rows.insert(0, {'title': title or '', 'text': text, 'date': date or datetime.date.today().isoformat()})
                elif op in ('edit', 'delete'):
                    i = next((i for i, row in enumerate(snap['entries']) if row['id'] == entry), None)
                    if i is None:
                        return fail('not_found')
                    if op == 'edit':
                        if not snap['entries'][i]['editable']:
                            return fail('redacted')
                        rows[i] = {'title': rows[i]['title'] if title is None else title, 'text': text,
                                   'date': rows[i]['date'] if date is None else date}
                    else:
                        rows.pop(i)
                body = encode(rows)
                if len(body) > c['doc']['data'].get('maxBytes', cards.DATA_DEFAULT):
                    return fail('too_large')
                if body != old:
                    current, issue, truncated = cards._read(root, c)
                    if issue not in (None, 'empty') or truncated or (current or b'') != old:
                        return fail('conflict')
                    name = c['doc']['data']['file']
                    if migrating:
                        # O_EXCL never overwrites a prior backup; verify a previous interrupted migration's bytes.
                        backup = name + '.pre-v2-' + hashlib.sha256(old).hexdigest() + '.bak'
                        try:
                            cards._write_at(d, backup, old)
                        except FileExistsError:
                            saved, cut = cards._read_fd(os.open(backup, os.O_RDONLY | cards._NOFOLLOW | os.O_NONBLOCK, dir_fd=d), len(old) + 1)
                            if cut or saved != old:
                                return fail('refused')
                        backup_fd = os.open(backup, os.O_RDONLY | cards._NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
                        try:
                            os.fsync(backup_fd)
                        finally:
                            os.close(backup_fd)
                        os.fsync(d)
                    _replace(d, name, body)
                result = snapshot(body)
                return {'ok': True, 'id': 'notes', 'noteFields': True, **result,
                        'text': '\n\n'.join(row['text'] for row in result['entries']),
                        **({'problem': 'empty'} if not rows else {})}
            finally:
                os.close(fd)
    except (OSError, ValueError, UnicodeError):
        return fail('refused')
