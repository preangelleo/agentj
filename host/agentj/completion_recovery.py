"""Bounded local reconciliation; never an authority to replay an action."""
import asyncio
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

from . import approvals, privacy
from .state import _write_private

COMMAND = re.compile(r'/recover-background(?:\s+([a-f0-9]{64}))?\s*')
MAX = 128
LIMIT = 65536


def valid_row(row):
    if not isinstance(row, dict):
        return False
    binding = row.get('binding')
    if not isinstance(binding, dict) or set(binding) != {'channel', 'root'} or not all(v is None or isinstance(v, str) for v in binding.values()):
        return False
    if row.get('state') not in ('awaiting_owner_verification', 'reading', 'reconciled') or row.get('recovery_point') not in ('read_report', 'reconcile_only'):
        return False
    if row.get('source') != 'unknown' or type(row.get('notified')) is not bool:
        return False
    strings = ('report', 'generation', 'original_error', 'origin', 'artifact', 'task_label', 'native_session', 'child_id', 'stage', 'report_error')
    if any(row.get(k) is not None and (not isinstance(row[k], str) or len(row[k]) > 4096) for k in strings):
        return False
    if row.get('task_id') is not None and type(row['task_id']) not in (str, int):
        return False
    if any(row.get(k) is not None and (type(row[k]) not in (int, float) or not math.isfinite(row[k])) for k in ('since', 'ended', 'created_at', 'exit_code')):
        return False
    if 'report_sha256' in row and (not isinstance(row['report_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', row['report_sha256'])):
        return False
    action = row.get('action')
    return action is None or (isinstance(action, dict) and set(action) == {'tool', 'input_sha256'} and isinstance(action['tool'], str)
        and isinstance(action['input_sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', action['input_sha256']))


def native_ids(item, enqueued=None):
    """Task ids of Claude's own finished-task notification: delivered on the main thread (record-level origin only), or a
    queue removal whose identical enqueue this reader saw (`enqueued`: the caller's set of content digests; None → never)."""
    if not isinstance(item, dict) or item.get('isSidechain'):
        return []
    from .transcript_input import proactive_origin
    if item.get('type') == 'queue-operation' and item.get('operation') in ('enqueue', 'remove'):
        text = item.get('content')
        if not isinstance(text, str) or not text.lstrip().startswith('<task-notification>') or enqueued is None:
            return []
        h = hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()
        if item['operation'] == 'enqueue':
            enqueued.add(h)
            while len(enqueued) > 256:
                enqueued.pop()
            return []
        if h not in enqueued:
            return []
        enqueued.discard(h)
    elif item.get('type') == 'user':
        c = (item.get('message') or {}).get('content') if isinstance(item.get('message'), dict) else None
        text = c if isinstance(c, str) else ''.join(b.get('text', '') for b in c if isinstance(b, dict)) if isinstance(c, list) else ''
        if proactive_origin(item) != 'task':      # M2: Claude Code's record markers, never the words
            return []
    else:
        return []
    if not isinstance(text, str) or not text.lstrip().startswith('<task-notification>') or re.search(r'<status>\s*(running|pending)\b', text):
        return []
    return re.findall(r'<task-id>\s*([^<\s]+)\s*</task-id>', text)


class Book:
    def __init__(self, host):
        self.host = host
        self.path = Path(host.st.root) / 'busy' / 'recovery.json'
        self.lock = threading.RLock()
        self.corrupt = False
        try:
            if self.path.is_symlink():
                raise ValueError('unsafe_state')
            raw = self.path.read_bytes()
            if len(raw) > 512 * 1024:
                raise ValueError('oversized_state')
            self.rows = json.loads(raw) if len(raw) <= 512 * 1024 else {}
            if not isinstance(self.rows, dict):
                raise ValueError('invalid_state')
            if not all(isinstance(k, str) and re.fullmatch(r'[a-f0-9]{64}', k) and valid_row(v) for k,v in self.rows.items()):
                raise ValueError('invalid_rows')
            for row in self.rows.values():
                if row.get('state') == 'reading':
                    row['state'] = 'awaiting_owner_verification'
        except FileNotFoundError:
            self.rows = {}
        except (OSError, ValueError):
            self.rows = {}
            self.corrupt = True
        self.native = {}
        if self._settle():
            with contextlib.suppress(OSError):
                self.save()

    def _settle(self):
        # 0.26.8a2: no reminder where the owner has nothing to verify; rows are kept and stay listed
        changed = False
        for row in self.rows.values():
            if row.get('state') != 'awaiting_owner_verification' or row.get('recovery_point') != 'reconcile_only' or row.get('report_sha256'):
                continue
            at = self.native.get(row.get('task_id'))
            if at is not None and str(row['task_id']).startswith('shared-'):
                row.update(state='reconciled', notified=True, settled={'by':'native_task_notification', 'at':at})
                changed = True
            elif row.get('stage') == 'source_gate' and row.get('original_error') == 'group_source' and not row.get('quiet'):
                row.update(notified=True, quiet='group_source')
                changed = True
        return changed

    def native_done(self, keys, at=None):
        """Claude's own delivered task notification for its native background task (host-read main transcript)."""
        with self.lock:
            for k in keys:
                self.native[k] = str(at)[:40] if at else int(time.time())
            while len(self.native) > 256:
                self.native.pop(next(iter(self.native)))
            if self._settle():
                with contextlib.suppress(OSError):
                    self.save()

    def scan_native(self, path, scope):
        """Once per row: the served transcript is the only evidence read; nothing else is kept."""
        with self.lock:
            rows = [r for r in self.rows.values() if str(r.get('task_id')).startswith(scope + ':') and not r.get('native_scan')
                    and r.get('state') == 'awaiting_owner_verification' and r.get('recovery_point') == 'reconcile_only']
            want = {str(r['task_id'])[len(scope) + 1:] for r in rows}
        if not want:
            return
        found, enq = {}, set()
        with contextlib.suppress(OSError), open(path, 'rb') as f:
            for line in f:
                if b'<task-id>' in line:
                    with contextlib.suppress(ValueError):
                        item = json.loads(line)
                        found.update((t, item.get('timestamp')) for t in want.intersection(native_ids(item, enq)))
        with self.lock:
            for r in rows:
                r['native_scan'] = int(time.time())
            for t, at in found.items():
                self.native_done([scope + ':' + t], at if isinstance(at, str) else None)
            with contextlib.suppress(OSError):
                self.save()

    def binding(self):
        h = self.host
        root = (getattr(h, 'agent_cfg', None) or {}).get('dir')
        return {'channel': h.st.config()['channel'] if callable(getattr(h.st, 'config', None)) else None, 'root': os.path.realpath(root) if root else None}

    def save(self):
        if self.corrupt:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(self.path, json.dumps(self.rows, ensure_ascii=False).encode())

    def add(self, key, row):
        with self.lock:
            if self.corrupt:
                return
            if key in self.rows:
                return
            # Never evict an unresolved recovery point to make room for a flood.
            if len(self.rows) >= MAX:
                settled = next((k for k,v in self.rows.items() if v.get('state') == 'reconciled'), None)
                if settled is not None:
                    del self.rows[settled]
            if len(self.rows) >= MAX:
                self.host.st.log('recovery_capacity', result='awaiting_owner_verification')
                return
            self.rows[key] = {**row, 'binding':self.binding(), 'state':'awaiting_owner_verification', 'notified':False, 'created_at':int(time.time())}
            self._settle()
            self.save()

    def capture(self, key, value, snapshot=True):
        if key in self.rows:
            return
        row = {'task_id':value.get('task_id'), 'generation':value.get('generation'), 'since':value.get('since'), 'ended':value.get('ended'), 'original_error':None, 'exit_code':value.get('exit_code'),
               'source':'unknown', 'recovery_point':'read_report', 'report':value.get('report_path') or '',
               'origin': 'foreign' if value.get('foreign') is True else 'missing_launch_evidence',
               'artifact':privacy.redact(value.get('report') or '', paths=True)}
        if value.get('foreign') is True or not snapshot:
            row['recovery_point'] = 'reconcile_only'
            row['report'] = ''
        else:
            try:
                data = self.read(row['report'])
                row['report_sha256'] = hashlib.sha256(data).hexdigest()
            except (OSError, ValueError):
                row['report_error'] = 'unsafe_or_unavailable'
                row['report'] = ''
        self.add(key, row)

    def read(self, value):
        from . import danger
        root = self.binding()['root']
        if not root:
            raise ValueError('no_root')
        path = os.path.abspath(os.path.join(root, os.path.expanduser(value)))
        relative = os.path.relpath(path, root)
        parts = Path(relative).parts
        jarvis_report = len(parts) == 2 and parts[0] == '.jarvis' and re.fullmatch(r'REPORT-[A-Za-z0-9._-]+\.md', parts[1])
        if (not value or not root or not path.startswith(root + os.sep) or danger.cred_path(path)
                or any((p.startswith('.') and not (jarvis_report and p == '.jarvis')) or p.lower() in ('credentials', 'secrets') for p in parts)
                or Path(path).suffix.lower() not in ('.md', '.txt', '.json', '.log')):
            raise ValueError('unsafe_target')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in Path(path).parts[1:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            f = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(f, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > LIMIT:
                    raise ValueError('unsafe_target')
                from .media import _hash_and_scan
                from .peer_guard import SecretIndex
                scanned_sha, secret = _hash_and_scan(stream.fileno(), before.st_size, True, False)
                data = stream.read(LIMIT + 1)
                if hashlib.sha256(data).hexdigest() != scanned_sha or secret or SecretIndex.build(workdir=root).hits(path + '\n' + data.decode('utf-8')):
                    raise ValueError('secret_content')
                after = os.fstat(stream.fileno())
                if len(data) > LIMIT or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('report_changed')
                return data
        finally:
            os.close(fd)

    def refusal(self, tool, inp, reason, child_id=None):
        from . import provenance
        a = getattr(self.host, 'agent', None)
        source = provenance.turn_source(a)
        request = provenance._REQUEST.get()
        if request is not provenance._UNSET:
            source = provenance.stricter(source, request)
        if source.get('kind') != 'unknown':
            return
        ids = getattr(getattr(a, 'cur_send', None), 'background_done_ids', [])
        try:
            digest = approvals.input_digest(inp)
        except (ValueError, TypeError):
            digest = hashlib.sha256(tool.encode()).hexdigest()
        identity = [self.binding(), getattr(self.host, 'cur_turn', None), getattr(a, 'sid', None) or getattr(a, 'tid', None), child_id, tool, digest]
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self.lock:
            for tid in ids:
                if tid in self.rows:
                    self.rows[tid]['original_error'] = reason
                    self.rows[tid]['action'] = {'tool':tool, 'input_sha256':digest}
            self.add(key, {'task_id':getattr(self.host, 'cur_turn', None), 'source':'unknown',
                          'original_error':reason, 'action':{'tool':tool, 'input_sha256':digest},
                          'recovery_point':'reconcile_only', 'task_label':getattr(self.host, 'task_label', None),
                          'native_session':getattr(a, 'sid', None) or getattr(a, 'tid', None), 'child_id':child_id, 'stage':'source_gate'})
            self.save()

    def overflow(self):
        pending = getattr(getattr(self.host, 'background_done', None), 'pending', {})
        return [(k,v) for k,v in list(pending.items()) if k not in self.rows and v.get('foreign') is not False]

    def notice(self):
        with self.lock:
            ids = [k for k,v in self.rows.items() if v.get('binding') == self.binding() and v.get('state') == 'awaiting_owner_verification' and not v.get('notified')]
            overflow = [(k,v) for k,v in self.overflow() if not v.get('capacity_notified')]
            if not ids and not overflow:
                return ''
            for k,v in overflow:
                v['capacity_notified'] = True
            if overflow:
                self.host.background_done.save()
            for k in ids:
                self.rows[k]['notified'] = True
            self.save()
            return ('有后台结果待本人核对，原操作未重试。发送 /recover-background 查看，再用 /recover-background <编号> 读取固定报告。'
                    ' / Background results await your verification; no action was replayed. Send /recover-background to list them, '
                    'then /recover-background <id> to read the bound report.')

    def acknowledge(self, key):
        with self.lock:
            if key in self.rows and self.rows[key].get('state') == 'reading':
                self.rows[key]['state'] = 'reconciled'
                self.save()

    def recover(self, key=None, verify=lambda:True):
        with self.lock:
            if self.corrupt:
                return {'result':'awaiting_owner_verification', 'why':'corrupt_state'}
            if not verify():
                return {'result':'refused', 'why':'paired_owner_required'}
            if key is None:
                return {'result':'awaiting_owner_verification', 'items':[
                    {'id':k, 'task_id':v.get('task_id'), 'state':v.get('state'), 'original_error':v.get('original_error'),
                     'recovery_point':v.get('recovery_point'), 'report_available':bool(v.get('report_sha256')),
                     'artifact':v.get('artifact'), 'report_sha256':v.get('report_sha256'), 'exit_code':v.get('exit_code'), 'created_at':v.get('created_at'),
                     'settled':v.get('settled') or v.get('quiet')}
                    for k,v in self.rows.items() if v.get('binding') == self.binding() and (v.get('state') == 'awaiting_owner_verification' or v.get('settled'))] + [
                    {'id':k, 'task_id':v.get('task_id'), 'state':'awaiting_owner_verification', 'recovery_point':'capacity_wait', 'report_available':False}
                    for k,v in self.overflow()]}
            row = self.rows.get(key)
            if not row or row.get('binding') != self.binding():
                return {'result':'refused', 'why':'binding_changed'}
            if row.get('state') != 'awaiting_owner_verification':
                return {'result':'refused', 'why':'already_reconciled'}
            if row.get('recovery_point') != 'read_report' or not row.get('report_sha256'):
                return {'result':'awaiting_owner_verification', 'why':'reconcile_only', 'original_error':row.get('original_error')}
            try:
                data = self.read(row['report'])
                if hashlib.sha256(data).hexdigest() != row['report_sha256']:
                    raise ValueError('report_changed')
                text = privacy.owner_text(data.decode('utf-8'))
            except (OSError, ValueError):
                return {'result':'awaiting_owner_verification', 'why':'report_changed_or_unavailable'}
            if not verify():
                return {'result':'awaiting_owner_verification', 'why':'delivery_unconfirmed'}
            row['state'] = 'reading'
            self.save()
            return {'result':'reconciled', 'id':key, 'task_id':row['task_id'], 'original_error':row.get('original_error'),
                    'report':text, 'action_replayed':False}


async def owner_command(host, session, text):
    """Only the live paired ingress may select a saved result, never an Agent call."""
    from .report_actions import phone_source
    match = COMMAND.fullmatch(text.strip())
    if not match:
        return None
    if phone_source(host, session) is None:
        return {'result':'refused', 'why':'paired_owner_required'}
    result = await asyncio.to_thread(host.background_done.recovery.recover, match[1], lambda:phone_source(host, session) is not None)
    from . import diskq
    if phone_source(host, session) is None or not await diskq.aflush():
        if match[1] and result.get('result') == 'reconciled':
            book = host.background_done.recovery
            with book.lock:
                book.rows[match[1]]['state'] = 'awaiting_owner_verification'
                book.save()
        return {'result':'awaiting_owner_verification', 'why':'delivery_unconfirmed'}
    return result
