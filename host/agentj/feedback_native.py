"""Exact native-call feedback transport. A CLI ticket is never sending authority."""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import shlex
import shutil

TTL = 300
MAX_BODY = 65536


def command(tool, inp, cwd):
    """Separate from the secret/file parser: existing harness permission hooks keep their contract."""
    if tool != 'Bash' or not isinstance(inp, dict) or not isinstance(inp.get('command'), str):
        return None
    raw = inp['command']
    if any(c in raw for c in '$`<>\r\n'):
        return None
    from .static_shell import _statements
    statements = _statements(raw)
    if not statements or len(statements) != 1 or statements[0][1] or any(w[3] for w in statements[0][0]):
        return None
    try: words = shlex.split(raw)
    except ValueError: return None
    if len(words) < 3 or not (words[0] == 'agentj' or shutil.which('agentj') and os.path.realpath(words[0]) == os.path.realpath(shutil.which('agentj'))):
        return None
    return parse(words, cwd)


def parse(words, cwd):
    if words[1:2] != ['support'] or len(words) < 3 or words[2] not in ('ask', 'report', 'feedback'):
        return None
    p = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    p.add_argument('text', nargs='*')
    p.add_argument('--kind', choices=('bug', 'report'), default='report' if words[2] == 'feedback' else 'bug')
    p.add_argument('--file')
    p.add_argument('--thread')
    p.add_argument('--wait', type=int, default=180)
    p.add_argument('--lang', choices=('zh', 'en'))
    p.add_argument('--attach-doctor', action='store_true')
    p.add_argument('--owner-confirmed', action='store_true')
    flags = [w.split('=', 1)[0] for w in words[3:] if w.startswith('--')]
    if len(flags) != len(set(flags)) or words[2] == 'ask' and '--kind' in flags:
        return None
    try:
        a, extra = p.parse_known_args(words[3:])
    except (argparse.ArgumentError, SystemExit):
        return None
    if extra or a.file and a.text or a.wait != 0:
        return None
    return {'t': 'support_native', 'cwd': os.path.abspath(cwd), 'file': a.file,
            'fields': {'kind': 'question' if words[2] == 'ask' else a.kind,
                       'text': ' '.join(a.text), 'thread': a.thread, 'wait': a.wait,
                       'lang': a.lang, 'attach_doctor': a.attach_doctor, 'owner_confirmed': a.owner_confirmed}}


def canonical(req):
    allowed = {'t', 'op', 'kind', 'text', 'thread', 'wait', 'lang', 'attach_doctor', 'owner_confirmed'}
    if not isinstance(req, dict) or set(req) - allowed or req.get('t') != 'host_diagnostics' or req.get('op') != 'support':
        return None
    fields = {k: req.get(k, default) for k, default in (
        ('kind', None), ('text', None), ('thread', None), ('wait', 180), ('lang', None),
        ('attach_doctor', False), ('owner_confirmed', False))}
    if fields['kind'] not in ('question', 'bug', 'report') or not isinstance(fields['text'], str) or not fields['text'].strip() or len(fields['text'].encode()) > MAX_BODY:
        return None
    if type(fields['wait']) is not int or fields['wait'] != 0 or fields['lang'] not in (None, 'zh', 'en'):
        return None
    if any(type(fields[k]) is not bool for k in ('attach_doctor', 'owner_confirmed')):
        return None
    from .support import _THREAD
    if fields['thread'] is not None and (not isinstance(fields['thread'], str) or not _THREAD.fullmatch(fields['thread'])):
        return None
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def payload(spec, root):
    """Only a regular, single-link UTF-8 draft below the served project; never follow links."""
    fields = dict(spec['fields'])
    if spec['file']:
        path = Path(spec['file']).expanduser()
        path = path if path.is_absolute() else Path(spec['cwd']) / path
        parts = path.absolute().relative_to(root).parts
        if not parts or any(x in ('.', '..') for x in parts):
            raise ValueError('draft path')
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = nxt
            filefd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(filefd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BODY:
                    raise ValueError('draft shape')
                raw = stream.read(MAX_BODY + 1)
                after = os.fstat(stream.fileno())
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if len(raw) > MAX_BODY or identity(before) != identity(after):
                raise ValueError('draft changed')
            fields['text'] = raw.decode('utf-8')
        finally:
            os.close(fd)
    value = canonical({'t': 'host_diagnostics', 'op': 'support', **fields})
    if value is None:
        raise ValueError('support shape')
    return value


class Binding:
    """Constructed from Host-observed native call + exact execution Send, never CLI fields."""
    def __init__(self, broker, row):
        from . import approval_review, provenance
        h = broker.xfer.svc.host
        self.broker, self.row, self.host, self.agent = broker, row, h, h.agent
        self.send = row['execution'].send
        self.origin = copy.deepcopy(approval_review.snapshot(h))
        self.source = copy.deepcopy(provenance.turn_source(self.agent))
        if not provenance.is_owner(self.source) or provenance.untrusted_turn(self.agent) or provenance.owner_grant_required(self.agent) or not self.origin or not self.origin.get('verified') or self.origin.get('suspicious') or self.origin.get('material_unknown') or self.origin != getattr(self.send, 'approval_origin', None):
            raise ValueError('native source')
        task = getattr(h, '_task_id', None)
        if callable(task): task()
        self.state, self.epoch, self.task_epoch = h.st, str(h.hist.epoch), getattr(h, 'task_epoch', None)
        self.estop = h.estop
        self.proc = getattr(self.agent, 'proc', None) or getattr(self.agent, 'native', None)
        self.device = getattr(self.send, 'device', None)
        self.key = h.st.sign_key(self.device) if self.device else None
        self.root = os.path.realpath((h.agent_cfg or {}).get('dir') or '')
        self.expires, self.closed, self.body = time.monotonic() + TTL, False, None
        self.body_sha256 = None
        if self.proc is None or not self.key:
            raise ValueError('native identity')
        from .peer_origin import _stat
        self.process_identity = _stat(Path('/proc'), self.proc.pid) if sys.platform == 'linux' else None
        self.native_identity = self.kernel_root() if sys.platform == 'linux' else None
        self.existing_descendants = set()
        self.registered_tick = None
        if sys.platform == 'linux':
            from . import busy_launch
            if self.native_identity is None:
                raise ValueError('native kernel root')
            table = busy_launch.procs()
            anchor = self.native_identity[0]
            if anchor not in table:
                raise ValueError('native process snapshot')
            self.registered_tick = int(time.clock_gettime(time.CLOCK_BOOTTIME) * busy_launch.HZ)
            children = {}
            for pid, item in table.items():
                children.setdefault(item[0], []).append(pid)
            pending = list(children.get(anchor, ()))
            while pending:
                pid = pending.pop()
                identity = (pid, str(table[pid][2]))
                if identity in self.existing_descendants:
                    raise ValueError('native process cycle')
                self.existing_descendants.add(identity)
                if len(self.existing_descendants) > 4096:
                    raise ValueError('native process snapshot limit')
                pending.extend(children.get(pid, ()))

    def bind_body(self, body):
        if self.body is not None and self.body != body:
            raise ValueError('native feedback payload changed')
        self.body = bytes(body)
        self.body_sha256 = hashlib.sha256(self.body).hexdigest()

    def kernel_root(self):
        from . import peer_origin
        proc = Path('/proc'); anchor = self.proc.pid
        try:
            row = peer_origin._stat(proc, anchor)
            if row != self.process_identity:
                return None
            wrapped = Path(os.readlink(proc / str(anchor) / 'exe')).name == 'bwrap'
            if getattr(self.agent, 'cfg', {}).get('fence') and not wrapped:
                return None
            if wrapped:
                children = (proc / str(anchor) / 'task' / str(anchor) / 'children').read_text().split()
                if len(children) != 1:
                    return None
                native = int(children[0]); path = proc / str(native)
                nspid = next(x.split()[1:] for x in (path/'status').read_text().splitlines() if x.startswith('NSpid:'))
                child = peer_origin._stat(proc, native)
                if not child or child[1] != anchor or nspid[-1] != '1' or peer_origin._uid(proc, native) != os.getuid() or os.readlink(path/'ns/pid') == os.readlink(proc/str(anchor)/'ns/pid'):
                    return None
                anchor, row = native, child
            return (anchor, row, os.readlink(proc/str(anchor)/'ns/pid'))
        except (OSError, ValueError, StopIteration, IndexError):
            return None

    def live(self):
        from . import provenance
        h = self.host
        return (not self.closed and time.monotonic() < self.expires and h.st is self.state and h.agent is self.agent
            and self.broker.xfer.execution is self.row['execution'] and self.row['execution'].live
            and self.agent.cur_send is self.send and getattr(self.agent, 'proc', None) is self.proc
            and (self.row['native'] is None or self.row['native'] == (getattr(self.agent,'tid',None),getattr(self.agent,'turn_id',None)))
            and self.proc.returncode is None and str(h.hist.epoch) == self.epoch and not h.stopped()
            and h.estop is self.estop and not provenance.untrusted_turn(self.agent) and not provenance.owner_grant_required(self.agent)
            and not h.stopping.is_set() and getattr(h, 'task_epoch', None) == self.task_epoch
            and self.send.state not in ('cancelled', 'failed') and self.send.approval_origin == self.origin
            and (self.body is None or isinstance(self.body, bytes) and hashlib.sha256(self.body).hexdigest() == self.body_sha256)
            and provenance.normalize(self.send.source) == self.source and h.st.sign_key(self.device) == self.key
            and os.path.realpath((h.agent_cfg or {}).get('dir') or '') == self.root
            and (sys.platform != 'linux' or self.native_identity is not None and self.kernel_root() == self.native_identity))

    def peer(self, pid):
        """Kernel pedigree is only ticket delivery; native call completion still owns sending."""
        from . import approval_material, busy_launch, peer_origin
        if not self.live() or type(pid) is not int:
            return False
        if sys.platform != 'linux':
            return approval_material.verified_caller_origin(self.host, pid) == self.origin
        proc = Path('/proc'); anchor = self.native_identity[0]
        if peer_origin._uid(proc, pid) != os.getuid():
            return False
        try:
            table = busy_launch.procs()
            current, seen = pid, set()
            while current != anchor and current > 1 and current not in seen and len(seen) < 64:
                seen.add(current); item = table.get(current)
                if not item or (current, str(item[2])) in self.existing_descendants or int(item[2]) < self.registered_tick:
                    return False
                current = item[0]
            return bool(seen) and current == anchor and self.live()
        except (OSError, ValueError, StopIteration, IndexError):
            return False

    @contextlib.contextmanager
    def review_scope(self):
        """Private Host callback after exact native result; public privacy_check is unchanged."""
        from . import privacy_host, privacy
        from dataclasses import asdict
        loop = asyncio.get_running_loop()
        async def review(draft, gate):
            if gate != 'feedback' or not self.live():
                return privacy_host.failure('source_denied')
            if len(json.dumps(draft, ensure_ascii=False).encode()) > privacy_host.MAX_DRAFT:
                return privacy_host.failure('draft_limit')
            try:
                result = await asyncio.to_thread(privacy.layer2, draft, gate='feedback', _host=True)
            except Exception:
                result = privacy.Verdict('unavailable', reason='privacy-check/backend_unavailable')
            if not self.live():
                return privacy_host.failure('session_changed')
            if not isinstance(result, privacy.Verdict) or result.status not in ('ok', 'blocked', 'unavailable'):
                return privacy_host.failure('backend_unavailable')
            return asdict(result)
        def request(draft, gate):
            if gate != 'feedback' or not self.live():
                return privacy_host.failure('source_denied')
            if loop.is_closed():
                return privacy_host.failure('host_unavailable')
            try:
                if asyncio.get_running_loop() is loop:
                    return privacy_host.failure('broker_thread_required')
            except RuntimeError:
                pass
            job = asyncio.run_coroutine_threadsafe(review(draft, gate), loop)
            try: return job.result(timeout=30)
            except Exception:
                job.cancel(); return privacy_host.failure('host_unavailable')
        token = privacy_host._BROKER_REVIEW.set(request)
        try: yield
        finally: privacy_host._BROKER_REVIEW.reset(token)

    def channel(self, original):
        binding = self
        class Guarded:
            signed, st = original.signed, original.st
            def send(self, fields):
                from .support import SupportError
                if not binding.live():
                    raise SupportError('NOT SENT — original native feedback task expired.')
                return original.send(fields)
        return Guarded()
