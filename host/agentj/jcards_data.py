"""P386 card data: bounded read-only automation discovery, user colors, optimistic notebook edits.
All paths remain under the existing no-follow card storage boundary; no shell commands from card data.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import re
import subprocess
import stat
import time
import threading
import uuid
from . import jcards as cards
from . import privacy


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _spans(raw):
    """Keep exact original byte spans. A list item includes its indented continuation;
    non-list paragraphs are entries too. Headings and separator whitespace stay untouched.
    """
    lines = raw.splitlines(keepends=True)
    spans, start, pos = [], None, 0
    for line in lines:
        stripped = line.strip()
        boundary = (not stripped and not (start is not None and line.startswith((b'  ', b'\t')))) or re.match(rb'#{1,6}\s', line) or re.match(rb'\s*([-*_])(?:\s*\1){2,}\s*$', line)
        bullet = re.match(rb'(?:[-+*]|\d+[.)])\s+', line)
        if boundary or bullet:
            if start is not None:
                spans.append((start, pos))
                start = None
        if not boundary and start is None:
            start = pos
        pos += len(line)
    if start is not None:
        spans.append((start, pos))
    out, counts = [], {}
    for a, b in spans:
        block = raw[a:b]
        digest = hashlib.sha256(block).hexdigest()[:20]
        counts[digest] = counts.get(digest, 0) + 1
        out.append((f'n-{digest}-{counts[digest]}', a, b))
    return out


def note_snapshot(raw):
    entries = []
    for eid, a, b in _spans(raw)[:cards.ITEMS_MAX]:
        original = raw[a:b].decode('utf-8', 'replace').rstrip('\r\n')
        text = re.sub(r'^(?:[-+*]|\d+[.)])\s+', '', original)
        safe = privacy.redact(text, owner=True)
        entries.append({'id': eid, 'text': safe, 'editable': safe == text})
    return {'revision': _revision(raw), 'entries': entries}


def note_change(root, cid, op, entry=None, revision=None, text=None):
    c = cards.find(root, cid)
    fail = lambda why: {'ok': False, 'id': cid, 'why': why}
    if not c or not c['on'] or c['doc']['template'] != 'notes' or cid == 'automation':
        return fail('not_found')
    if op not in ('add', 'edit', 'delete'):
        return fail('bad_op')
    if op != 'delete' and (not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 16384 or '\x00' in text):
        return fail('bad_text')
    try:
        with cards._dirfd(root, ('cards-data', cid), create=True) as d:
            lockfd = os.open('.notes-write.lock', os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | cards._NOFOLLOW, 0o600, dir_fd=d)
            try:
                if not stat.S_ISREG(os.fstat(lockfd).st_mode):
                    return fail('refused')
                import fcntl  # supported POSIX host; lazy so read-only discovery remains importable elsewhere
                fcntl.flock(lockfd, fcntl.LOCK_EX)
                raw, problem, cut = cards._read(root, c)
                if problem not in (None, 'empty') or cut:
                    return fail('refused')
                raw = raw or b''
                if op != 'add' and revision != _revision(raw):
                    return fail('conflict')
                spans = _spans(raw)
                if op == 'add':
                    # Insert before the first item, leaving the title/preamble and every old byte untouched.
                    at = spans[0][1] if spans else len(raw)
                    block = ('- ' + text.strip().replace('\n', '\n  ') + '\n').encode()
                    if at and raw[at-1:at] != b'\n':
                        block = b'\n' + block
                    body = raw[:at] + block + raw[at:]
                else:
                    target = next((x for x in spans if x[0] == entry), None)
                    if not target:
                        return fail('not_found')
                    _, a, b = target
                    original = raw[a:b].decode('utf-8', 'replace')
                    if privacy.redact(original, owner=True) != original and op == 'edit':
                        return fail('redacted')
                    replacement = b'' if op == 'delete' else ('- ' + text.strip().replace('\n', '\n  ') + '\n').encode()
                    body = raw[:a] + replacement + raw[b:]
                if len(body) > c['doc']['data'].get('maxBytes', cards.DATA_DEFAULT):
                    return fail('too_large')
                # An Agent may edit independently; refuse if it changed since the initial read.
                current, issue, truncated = cards._read(root, c)
                if issue not in (None, 'empty') or truncated or (current or b'') != raw:
                    return fail('conflict')
                name = c['doc']['data']['file']
                tmp = '.note-' + uuid.uuid4().hex + '.tmp'
                try:
                    cards._write_at(d, tmp, body)
                    os.replace(tmp, name, src_dir_fd=d, dst_dir_fd=d)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(tmp, dir_fd=d)
                return {'ok': True, 'id': cid, **note_snapshot(body)}
            finally:
                os.close(lockfd)
    except OSError:
        return fail('refused')


def phone_change(root, cid, op, entry=None, revision=None, text=None):
    """Paired card mutations only; never dispatch a prompt or accept a storage path."""
    fail = lambda why: {'ok': False, 'id': cid, 'why': why}
    if op not in ('add', 'edit', 'delete'):
        return fail('bad_op')
    if op != 'delete':
        if not isinstance(text, str) or not text.strip() or '\x00' in text or len(text.encode()) > 16384:
            return fail('bad_text')
        if privacy.redact(text, owner=True) != text:
            return fail('looks_like_a_secret')
    c = cards.find(root, cid)
    if not c or not c['on']:
        return fail('not_found')
    if op == 'add' and (cid not in ('notes', 'todo') or not c['official']):
        return fail('bad_op')
    if op != 'add' and not isinstance(revision, str):
        return fail('conflict')
    if c['doc']['template'] == 'notes':
        if op == 'add':
            import datetime
            text = datetime.date.today().isoformat() + ' ' + text.strip()
        return note_change(root, cid, op, entry, revision, text)
    if cid != 'todo' or not c['official']:
        return fail('bad_op')
    from . import jcards_todo
    return jcards_todo.change(root, op, entry, revision=revision,
                             **({'text': text, 'by': 'owner'} if op != 'delete' else {}))


def colors(root):
    try:
        with cards._dirfd(root, ('cards',)) as d:
            raw, cut = cards._read_fd(os.open('colors.json', os.O_RDONLY | cards._NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=d), 16384)
        obj = json.loads(raw) if raw and not cut else {}
        return {k: v for k, v in obj.items() if isinstance(k, str) and isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v)} if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def set_color(root, cid, color):
    if not isinstance(cid, str) or (cid not in cards.SYSTEM and not cards.find(root, cid)):
        return {'ok': False, 'why': 'not_found'}
    if color is not None and (not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color)):
        return {'ok': False, 'why': 'bad_color'}
    from .state import State
    try:
        with State(root).file_lock("card-colors"):
            values = colors(root)
            if color is None:
                values.pop(cid, None)
            else:
                values[cid] = color.lower()
            cards._write_json(root, ("cards",), "colors.json", values)
        return {"ok": True, "id": cid, "color": color}
    except OSError:
        return {"ok": False, "why": "refused"}


def _run(argv, timeout=2):
    """Bound stdout before allocation, kill on the deadline, always reap. No stderr/commands are returned."""
    try:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL) as p:
            watchdog = threading.Timer(max(0.01, timeout), p.kill)
            watchdog.start()
            try:
                raw = p.stdout.read(262145)
                if len(raw) > 262144:
                    p.kill()
                p.wait()
                return raw.decode('utf-8', 'replace') if p.returncode == 0 and len(raw) <= 262144 else ''
            finally:
                watchdog.cancel()
    except (OSError, subprocess.SubprocessError):
        return ''


def _safe_job_name(name):
    # Preserve ordinary source-qualified names. Secret-shaped identifiers become stable opaque handles.
    return name if privacy.redact(name, owner=True) == name else 'job-' + hashlib.sha256(name.encode()).hexdigest()[:20]


def _owner_metadata(value):
    """Final outbound gate includes scheduler fields even when their upstream schema normally constrains them."""
    if isinstance(value, str):
        return privacy.redact(value, owner=True)
    if isinstance(value, list):
        return [_owner_metadata(item) for item in value]
    if isinstance(value, dict):
        return {key: _owner_metadata(item) for key, item in value.items()}
    return value


def automations(root=None, cfg=None, lang='zh'):
    """Reuse existing native scanner plus Agent J task catalogue. No parallel registry or inferred models."""
    from . import systasks
    deadline = time.monotonic() + 6
    def run(argv):
        left = deadline - time.monotonic()
        return _run(argv, min(2, left)) if left > 0 else ''
    native = systasks.scan(run=run)
    out, counts = [], {}
    for row in native:
        # Cron's scanner ids can repeat for the same executable: include schedule + occurrence.
        basis = str(row['id']) if row['src'] != 'cron' else hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:20]
        base = row['src'] + ':' + basis
        counts[base] = counts.get(base, 0) + 1
        unique = _safe_job_name(base + (f'-{counts[base]}' if row['src'] == 'cron' or counts[base] > 1 else ''))
        schedule = [{'k': x.get('k'), 'v': privacy.redact(str(x.get('v', '')), owner=True) if isinstance(x.get('v'), str) else x.get('v')} for x in row.get('when', []) if isinstance(x, dict)]
        out.append({'id': unique, 'name': unique, 'source': row['src'], 'schedule': schedule,
                    'when': schedule, 'repeat': schedule, 'next': row.get('next') or row.get('next_s'), 'harness': None, 'model': None,
                    'description': privacy.redact(row.get('name', ''), owner=True) or None,
                    'prompt': '/agentj-automation @' + unique})
    if root is not None and cfg:
        from . import tasks
        from .state import State
        for row in tasks.rows(State(root), cfg.get('dir')):
            unique = _safe_job_name('agentj:' + row['id'])
            out.append({'id': unique, 'name': unique, 'source': 'agentj', 'schedule': row.get('schedule'),
                        'repeat': row.get('schedule'), 'next': row.get('next'), 'harness': privacy.redact(str(cfg['kind']), owner=True) if cfg.get('kind') else None,
                        'model': privacy.redact(str(cfg['model']), owner=True) if cfg.get('model') else None, 'enabled': row.get('enabled'), 'tz': row.get('tz'),
                        'description': privacy.redact(row['title'].get(lang) or row['title'].get('en') or '', owner=True),
                        'prompt': '/agentj-automation @' + unique})
    return _owner_metadata(out[:cards.ITEMS_MAX])
