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


def note_change(root, cid, op, entry=None, revision=None, text=None, title=None, date=None):
    c = cards.find(root, cid)
    fail = lambda why: {'ok': False, 'id': cid, 'why': why}
    if not c or not c['on'] or c['doc']['template'] != 'notes' or cid == 'automation':
        return fail('not_found')
    if c and cid == 'notes' and c['official'] and c['on']:
        from . import jcards_notes
        return jcards_notes.change(root, c, op, entry, revision, text, title, date)
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


def phone_change(root, cid, op, entry=None, revision=None, text=None, title=None, date=None):
    """Paired card mutations only; never dispatch a prompt or accept a storage path."""
    fail = lambda why: {'ok': False, 'id': cid, 'why': why}
    if op not in ('add', 'edit', 'delete'):
        return fail('bad_op')
    if op != 'delete':
        if not isinstance(text, str) or not text.strip() or '\x00' in text or len(text.encode()) > 16384:
            return fail('bad_text')
        if privacy.redact(text, owner=True) != text or (cid == 'notes' and isinstance(title, str) and privacy.redact(title, owner=True) != title):
            return fail('looks_like_a_secret')
    c = cards.find(root, cid)
    if not c or not c['on']:
        return fail('not_found')
    if op == 'add' and (cid not in ('notes', 'todo') or not c['official']):
        return fail('bad_op')
    if op != 'add' and not isinstance(revision, str):
        return fail('conflict')
    if c['doc']['template'] == 'notes':
        return note_change(root, cid, op, entry, revision, text, title, date)
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
    spent = {}
    def run(argv):
        # A busy manager cannot spend the whole tool budget before later families.
        family = (argv[0], '--user' in argv if argv[0] == 'systemctl' else False)
        started = time.monotonic()
        left = min(deadline - started, 1.5 - spent.get(family, 0))
        if left <= 0:
            return ''
        try:
            return _run(argv, min(2, left))
        finally:
            spent[family] = spent.get(family, 0) + time.monotonic() - started
    native = systasks.scan(run=run, aggregate=True)
    controller = None
    if root is not None:
        from .native_scheduler_control import Controller, Backend
        from .state import State
        controller = Controller(State(root), Backend(run=run))
        # restore_rows has a legacy global MAX slice. Ask only for its bounded
        # recovery rows, then merge without discarding later scanned families.
        recovery = controller.restore_rows([])
        key = lambda r: (r['src'], r.get('scope', 'user'), r.get('control_id', r['id']))
        recovered = {key(r) for r in recovery}
        native = recovery + [r for r in native if key(r) not in recovered]
    out, counts = [], {}
    for row in native:
        # Cron's scanner ids can repeat for the same executable: include schedule + occurrence.
        basis = str(row['control_id']) if row.get('control_id') else str(row['id']) if row['src'] != 'cron' else hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:20]
        base = row['src'] + ':' + row.get('scope', 'user') + ':' + basis
        counts[base] = counts.get(base, 0) + 1
        unique = _safe_job_name(base + (f'-{counts[base]}' if row['src'] == 'cron' or counts[base] > 1 else ''))
        schedule = [{'k': x.get('k'), 'v': privacy.redact(str(x.get('v', '')), owner=True) if isinstance(x.get('v'), str) else x.get('v')} for x in row.get('when', []) if isinstance(x, dict)]
        out.append({'id': unique, 'name': unique, 'source': row['src'], 'schedule': schedule,
                    'when': schedule, 'repeat': schedule, 'next': row.get('next') or row.get('next_s'), 'harness': None, 'model': None,
                    'description': privacy.redact(row.get('name', ''), owner=True) or None,
                    'scope': row.get('scope', 'user'), 'owner': row.get('owner', 'unknown'),
                    'execution': privacy.redact(row['execution'], owner=True) if isinstance(row.get('execution'), str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*\.service', row['execution']) else '[masked]', 'last': row.get('last') or row.get('last_s'),
                    'last_result': row.get('last_result', 'unknown'), 'state': row.get('state'),
                    'visibility': row.get('visibility', 'visible'),
                    'control': _native_control(controller, row),
                    'prompt': '/agentj-automation @' + unique})
    if root is not None and cfg:
        from . import tasks, sysmigrate
        from .state import State
        state = State(root)
        try:
            migrated = sysmigrate.migrated(state)
        except (OSError, ValueError, TypeError):
            migrated = None  # Unresolved migration state cannot offer an enable button.
        for row in tasks.rows(state, cfg.get('dir')):
            base = 'agentj:' + row['id']
            counts[base] = counts.get(base, 0) + 1
            unique = _safe_job_name(base + (f'-{counts[base]}' if counts[base] > 1 else ''))
            out.append({'id': unique, 'name': unique, 'source': 'agentj', 'schedule': row.get('schedule'),
                        'repeat': row.get('schedule'), 'next': row.get('next'), 'harness': privacy.redact(str(cfg['kind']), owner=True) if cfg.get('kind') else None,
                        'owner': 'AgentJ', 'last_result': (row.get('last') or {}).get('status', 'unknown'), 'last': (row.get('last') or {}).get('at'),
                        'model': privacy.redact(str(cfg['model']), owner=True) if cfg.get('model') else None, 'enabled': row.get('enabled'), 'tz': row.get('tz'),
                        'description': privacy.redact(row['title'].get(lang) or row['title'].get('en') or '', owner=True),
                        'control': _task_control(row, migrated),
                        'scope': 'Agent J task scheduler',
                        'prompt': '/agentj-automation @' + unique})
    return _owner_metadata(out)


def _native_control(controller, row):
    if row['src'] not in ('systemd', 'launchd', 'schtasks', 'cron', 'cron-system', 'anacron'):
        return {'supported': False, 'reason': 'read_only'}
    if (controller and row.get('scope') in ('user', 'system', 'all-users')
            and privacy.redact(row['id'], owner=True) == row['id']):
        control = controller.describe(row)
    else:
        control = row.get('control', {'supported': False, 'reason': 'read_only'})
    if control.get('target', {}).get('kind') == 'agentj-task':
        return {'supported': False, 'reason': 'invalid_target'}
    return control


def _task_control(row, migrated):
    """Projection only: existing signed task_set remains the sole Agent J route."""
    tid, tsha = row.get('id'), row.get('tsha')
    reason = ('unresolved' if migrated is None else
              'migrated' if isinstance(tid, str) and tid in migrated else
              'invalid_contract' if row.get('problems') or not isinstance(tid, str)
              or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', tid)
              or not isinstance(tsha, str) or not re.fullmatch(r'[0-9a-f]{64}', tsha)
              or type(row.get('enabled')) is not bool else
              'stale_contract' if row.get('stale') else
              'uncertain' if row.get('uncertain') else None)
    if reason:
        return {'supported': False, 'reason': reason}
    return {'supported': True, 'target': {'kind': 'agentj-task', 'id': tid,
                                        'tsha': tsha, 'enabled': row['enabled']}}


def automation_page(root=None, cfg=None, lang='zh', page=0):
    """Read-only bounded page over the current native + Agent J scan."""
    if type(page) is not int or page < 0:
        return {'ok': False, 'why': 'bad_automation_page'}
    from . import wire
    items = automations(root, cfg, lang)
    # Count limits alone are insufficient: 200 normal native rows exceed p33.
    # Reserve the unchanged response envelope, request id and pagination metadata;
    # measure with the actual compact UTF-8 wire encoder, never character counts.
    budget = wire.MAX_JSON_P33 - 2048
    groups, used = [[]], 2
    for item in items:
        size = len(wire.dumps(item))
        if size + 2 > budget:
            return {'ok': False, 'why': 'automation_row_too_large'}
        separator = bool(groups[-1])
        if len(groups[-1]) >= cards.ITEMS_MAX or used + size + separator > budget:
            groups.append([])
            used, separator = 2, False
        groups[-1].append(item)
        used += size + separator
    pagination = {'page': page, 'total': len(items), 'page_size': cards.ITEMS_MAX,
                  'pages': len(groups), 'scan_limited': True}
    if page >= len(groups):
        return {'ok': False, 'why': 'bad_automation_page', 'pagination': pagination}
    return {'ok': True, 'items': groups[page], 'pagination': pagination}
