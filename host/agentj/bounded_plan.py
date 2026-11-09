"""#541 host-owned exact plan adapters. Metadata seals never grant authority by themselves."""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from pathlib import Path
import shlex
import stat

MAX_FILES = 1024
MAX_BYTES = 64 * 1024 * 1024


class PlanError(ValueError):
    pass


def _stat(s):
    return [str(s.st_dev), str(s.st_ino), stat.S_IMODE(s.st_mode), stat.S_IFMT(s.st_mode), s.st_uid, s.st_gid,
            s.st_size if stat.S_ISREG(s.st_mode) else None, str(s.st_mtime_ns) if stat.S_ISREG(s.st_mode) else None,
            str(s.st_ctime_ns) if stat.S_ISREG(s.st_mode) else None]


def path_seal(raw, cwd=None, *, content=True, key=None):
    """Capture every existing ancestor and exact target without following a symlink.

    Directory timestamps are not identity: sibling activity cannot change an approved target.
    Regular file data is hashed through its no-follow descriptor; links and special files are refused.
    """
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise PlanError('invalid_path')
    path = os.path.abspath(os.path.join(cwd or os.getcwd(), os.path.expanduser(raw)))
    if path.startswith(('/proc/', '/sys/', '/dev/')) or path in ('/', '/proc', '/sys', '/dev'):
        raise PlanError('protected_path')
    pieces = Path(path).parts
    chain, missing = [], False
    current = '/'
    for i, piece in enumerate(pieces):
        if i:
            current = os.path.join(current, piece)
        try:
            s = os.lstat(current)
        except FileNotFoundError:
            missing = True
            chain.append({'path': current, 'exists': False})
            break
        if stat.S_ISLNK(s.st_mode):
            raise PlanError('symlink')
        if not stat.S_ISREG(s.st_mode) and not stat.S_ISDIR(s.st_mode):
            raise PlanError('special_file')
        chain.append({'path': current, 'exists': True, 'stat': _stat(s)})
    result = {'path': path, 'chain': chain, 'exists': not missing}
    if not missing and stat.S_ISREG(s.st_mode) and content:
        if s.st_size > MAX_BYTES:
            raise PlanError('file_too_large')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if _stat(before) != _stat(s):
                raise PlanError('object_changed')
            h = hmac.new(key, digestmod=hashlib.sha256) if key else hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise PlanError('file_too_large')
                h.update(chunk)
            if _stat(os.fstat(fd)) != _stat(before):
                raise PlanError('object_changed')
            result['content_digest'] = h.hexdigest()
        finally:
            os.close(fd)
    if not missing and stat.S_ISDIR(s.st_mode) and content:
        result['tree'] = tree_seal(path, _stat(s), key)
    return result


def tree_seal(path, expected, key=None):
    entries, total = [], [0]
    root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if _stat(os.fstat(root)) != expected:
            raise PlanError('object_changed')
        def walk(fd, prefix=''):
            names = sorted(os.listdir(fd))
            for name in names:
                rel = prefix + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise PlanError('symlink_or_special')
                if len(entries) >= MAX_FILES:
                    raise PlanError('too_many_objects')
                item = {'name': rel, 'stat': _stat(info)}
                entries.append(item)
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if stat.S_ISDIR(info.st_mode):
                    flags |= os.O_DIRECTORY
                child = os.open(name, flags, dir_fd=fd)
                try:
                    if _stat(os.fstat(child)) != _stat(info):
                        raise PlanError('object_changed')
                    if stat.S_ISDIR(info.st_mode):
                        walk(child, rel + '/')
                    else:
                        h = hmac.new(key, digestmod=hashlib.sha256) if key else hashlib.sha256()
                        while True:
                            data = os.read(child, 65536)
                            if not data:
                                break
                            total[0] += len(data)
                            if total[0] > MAX_BYTES:
                                raise PlanError('tree_too_large')
                            h.update(data)
                        item['content_digest'] = h.hexdigest()
                    if _stat(os.fstat(child)) != _stat(info):
                        raise PlanError('object_changed')
                finally:
                    os.close(child)
            if sorted(os.listdir(fd)) != names:
                raise PlanError('object_changed')
        walk(root)
    finally:
        os.close(root)
    return entries


def shell_seal(tool, inp, cwd=None, key=None):
    """Existing exact shell plans cover only static local object actions; remote/API use structured adapters."""
    from . import danger
    if tool != 'Bash':
        paths = [inp[k] for k in ('file_path','path','source','destination') if isinstance(inp.get(k), str)]
        return {'cwd': os.path.abspath(cwd or os.getcwd()), 'paths': [path_seal(p, cwd, key=key) for p in paths]}
    text = inp.get('command')
    if not isinstance(text, str) or any(x in text for x in ('$','`','*','?','[','{','\n')):
        raise PlanError('dynamic_operand')
    commands = danger.lex(text)
    if len(commands) != 1 or commands[0].outs or commands[0].subst or commands[0].piped:
        raise PlanError('compound_program')
    words = shlex.split(text)
    if not words or os.path.basename(words[0]) not in ('rm','rmdir','cp','mv','mkdir','rsync'):
        raise PlanError('structured_adapter_required')
    paths = []
    for word in words[1:]:
        if word.startswith('-'):
            if '=' in word or word in ('-t','--target-directory','--backup-dir','--files-from','--rsync-path'):
                raise PlanError('dynamic_path_option')
            continue
        if ':' in word:
            raise PlanError('remote_target_requires_individual_uv')
        paths.append(path_seal(word, cwd, key=key))
    if not paths:
        raise PlanError('object_missing')
    return {'cwd': os.path.abspath(cwd or os.getcwd()), 'paths': paths}

# New descriptors are interpreted here by a fixed host executor. They are never translated to shell source.
import asyncio
import copy
import http.client
import ipaddress
import json
import re
import secrets
import shutil
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field

TOOL = 'AgentJBoundedPlan'
_EXECUTOR = object()


@dataclass
class SealedPlan:
    owner: object
    steps: list
    task: str
    session: str
    host_id: str
    expires: float
    display: str
    effects: tuple
    states: list = field(default_factory=list)
    rid: str = ''
    device: str = ''
    signing_key: bytes = b''
    token_values: dict = field(default_factory=dict)
    approved: bool = False

    def public(self):
        return {'actions': [self.owner.public_step(step) for step in self.steps], 'display': self.display,
                'display_en': self.display, 'expires': int(self.expires), 'max_steps': len(self.steps),
                'max_seconds': 600, 'host_id': self.host_id, 'each_step_once': True,
                'expected_confirmations': 1+sum(bool(step.get('individual_uv')) for step in self.steps)}


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, ip):
        super().__init__(hostname, timeout=20, context=ssl.create_default_context())
        self.pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def resolve_public(hostname):
    ips = sorted({row[4][0] for row in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise PlanError('non_public_endpoint')
    return ips


def transport_https(step, auth, body):
    parsed = urllib.parse.urlsplit(step['url'])
    conn = _PinnedHTTPS(parsed.hostname, step['resolved_ips'][0])
    try:
        headers = {'Authorization': 'Bearer ' + auth.decode('utf-8'), 'Content-Type': step['content_type'],
                   'User-Agent': 'agentj-bounded-plan'}
        conn.request(step['method'], parsed.path + ('?' + parsed.query if parsed.query else ''), body=body, headers=headers)
        res = conn.getresponse()
        data = res.read(65537)
        if len(data) > 65536:
            raise PlanError('response_too_large')
        if 300 <= res.status < 400:
            raise PlanError('redirect_refused')
        return res.status, data
    finally:
        conn.close()


def _url(value):
    from .approval_review import safe_text
    if isinstance(value, str) and safe_text(value, 24000) != value:
        raise PlanError('credential_in_url')
    p = urllib.parse.urlsplit(value) if isinstance(value, str) else None
    if (p is None or p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.fragment or p.port not in (None, 443) or '\\' in value or any(ord(x) < 32 for x in value)):
        raise PlanError('https_endpoint_required')
    if p.path.startswith(('/admin', '/internal')):
        raise PlanError('unknown_api')
    return p


def _credential(st, ref):
    from . import secret_xfer, owner_grant
    if not isinstance(ref, str) or not ref.startswith('secret:') or not owner_grant.NAME_RE.fullmatch(ref[7:]):
        raise PlanError('managed_credential_reference_required')
    spec = secret_xfer.registry_dest(st, ref[7:])
    if spec is None:
        raise PlanError('credential_not_registered')
    value = secret_xfer.read_value(spec)
    if not value:
        raise PlanError('credential_unavailable')
    return spec, value


def _body(raw, content_type):
    from .approval_review import canonical, safe_text
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or len(canonical(raw).encode()) > 8192:
        raise PlanError('bounded_body_required')
    def numbers(value):
        if isinstance(value,float) or type(value) is int and abs(value)>9007199254740991:
            raise PlanError('non_canonical_body_number')
        if isinstance(value,dict):
            for child in value.values():numbers(child)
        elif isinstance(value,list):
            for child in value:numbers(child)
    numbers(raw)
    text = canonical(raw)
    if safe_text(text, 24000) != text:
        raise PlanError('inline_secret_refused')
    if content_type == 'application/json':
        return text.encode()
    if content_type == 'multipart/form-data' and all(isinstance(v,(str,int,bool)) for v in raw.values()):
        return text.encode()
    if content_type == 'application/x-www-form-urlencoded' and all(isinstance(v, (str, int, bool)) for v in raw.values()):
        return urllib.parse.urlencode({k:('true' if v else 'false') if type(v) is bool else v for k,v in raw.items()}).encode()
    raise PlanError('unsupported_body_type')


def _http_effects(kind, parsed, method, body):
    # Explicit service adapters, not a model's declared effect. Unknown mutations remain individual UV.
    if kind in ('token_create', 'token_revoke'):
        return ('sensitive_storage', 'approval_root'), True
    if parsed.hostname == 'api.stripe.com':
        match = re.fullmatch(r'/v1/(customers|subscriptions|promotion_codes|coupons)(?:/([A-Za-z0-9_]+))?', parsed.path)
        if not match or method not in ('GET', 'POST'):
            raise PlanError('unsupported_stripe_operation')
        if method == 'POST':
            allowed = {'promotion_codes': {'coupon','code','active','max_redemptions','expires_at','customer','promotion'},
                       'coupons': {'duration','percent_off','amount_off','currency','name','max_redemptions','redeem_by'},
                       'subscriptions': {'cancel_at_period_end'}}.get(match[1], set())
            if not body or set(body) - allowed:
                raise PlanError('unsupported_stripe_mutation')
        return ('sensitive_storage',), False
    if kind=='token_use' and parsed.hostname=='api.cloudflare.com' and parsed.path=='/client/v4/user/tokens/verify' and method=='GET' and not body:
        return ('sensitive_storage',), False
    if method in ('GET', 'HEAD') and not body:
        return ('sensitive_storage','sensitive_delivery'), True
    # Paid model/transcription or an unknown mutation never inherits one task signature.
    return ('sensitive_storage', 'sensitive_delivery', 'money'), True


class OperationOnce:
    """Private durable reservations. SQL runs in callers' worker threads, never a phone callback."""
    def __init__(self, path):
        self.path = Path(path)
        self._terminal = set()
        self._pending = set()
        self._failed = False

    @staticmethod
    def _valid(info):
        return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and (os.name != 'posix' or (info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)))

    def _connect(self):
        import sqlite3
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
        fd = os.open(self.path, flags, 0o600)
        db = None
        try:
            info = os.fstat(fd)
            if not self._valid(info):
                raise PlanError('operation_ledger_file_boundary')
            current = self.path.lstat()
            if (not self._valid(current) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)):
                raise PlanError('operation_ledger_file_boundary')
            db = sqlite3.connect(str(self.path), timeout=.1)
            after = self.path.lstat()
            if (not self._valid(after) or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino)):
                db.close()
                raise PlanError('operation_ledger_file_changed')
            db.execute('PRAGMA busy_timeout=100')
            db.execute('CREATE TABLE IF NOT EXISTS operation_once (operation TEXT PRIMARY KEY, rid TEXT NOT NULL, state TEXT NOT NULL)')
            return db
        except sqlite3.Error:
            if db is not None:
                db.close()
            self._failed = True
            raise PlanError('operation_ledger_unavailable') from None
        except BaseException:
            if db is not None:
                db.close()
            raise
        finally:
            os.close(fd)

    def reserve(self, operation, rid):
        if self._failed or operation in self._terminal:
            return False
        db = self._connect()
        try:
            with db:
                row = db.execute("INSERT OR IGNORE INTO operation_once VALUES (?, ?, 'executing')", (operation, rid))
                return row.rowcount == 1
        except sqlite3.Error:
            self._failed = True
            raise PlanError('operation_ledger_unavailable') from None
        finally:
            db.close()

    def finish(self, operation, rid, state):
        if state not in ('executing', 'completed', 'unknown', 'denied', 'revoked'):
            raise ValueError('invalid operation terminal state')
        self._terminal.add(operation)
        db = self._connect()
        try:
            with db:
                db.execute('INSERT OR IGNORE INTO operation_once VALUES (?, ?, ?)', (operation, rid, state))
                db.execute('UPDATE operation_once SET state=? WHERE operation=? AND rid=?', (state, operation, rid))
        except sqlite3.Error:
            self._failed = True
            raise PlanError('operation_ledger_unavailable') from None
        finally:
            db.close()

    def release(self, operation, rid):
        """P0: undo this rid's own still-executing reservation when its answer was refused before it resolved."""
        if operation in self._terminal:
            return
        db = self._connect()
        try:
            with db:
                db.execute("DELETE FROM operation_once WHERE operation=? AND rid=? AND state='executing'", (operation, rid))
        except sqlite3.Error:
            self._failed = True
            raise PlanError('operation_ledger_unavailable') from None
        finally:
            db.close()

    def consumed(self, operation):
        if self._failed or operation in self._terminal:
            return True
        db = self._connect()
        try:
            return db.execute('SELECT 1 FROM operation_once WHERE operation=?', (operation,)).fetchone() is not None
        except sqlite3.Error:
            self._failed = True
            raise PlanError('operation_ledger_unavailable') from None
        finally:
            db.close()

    def defer_finish(self, operation, rid, state):
        # Revocation is immediate in memory; it can never authorize execution while metadata flushes.
        self._terminal.add(operation)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self.finish(operation, rid, state)
        task = loop.create_task(asyncio.to_thread(self.finish, operation, rid, state))
        self._pending.add(task)
        def done(future):
            self._pending.discard(future)
            if future.cancelled() or future.exception() is not None:
                self._failed = True  # unreadable/unwritten safety metadata never restores automatic execution
        task.add_done_callback(done)


class Plans:
    def __init__(self, host, *, resolver=None, transport=None):
        self.host = host
        self.key = secrets.token_bytes(32)
        self.resolver = resolver or resolve_public
        self.transport = transport or transport_https
        self.plans = {}
        self.requests = {}
        self.operation_once = OperationOnce(host.st.root / "approval-operation-once.sqlite3")
        from .token_recovery import Ledger
        self.recovery = Ledger(host)

    def public_step(self, step):
        from .approval_review import canonical
        out=copy.deepcopy({k:v for k,v in step.items() if not k.startswith("_")})
        for field in ('source_seal','destination_seal','credential_object','file_seal','other_seal'):
            if field in out:
                value=out.pop(field)
                out[field+'_commitment']=hmac.new(self.key,canonical(value).encode(),hashlib.sha256).hexdigest()
                out[field+'_object']={'path':value['path'],'exists':value['exists'],
                                     'objects':len(value.get('tree',[]))+1}
        return out

    def _authority(self, plan):
        h = self.host
        return (not h.stopped() and h.channel == plan.host_id and str(h.hist.epoch) == plan.session
                and time.time() < plan.expires and h._task_id() == plan.task
                and h.cur_turn is not None and (not plan.approved or h.st.sign_key(plan.device) == plan.signing_key))

    def seal(self, descriptors):
        from .approval_review import canonical, safe_text
        from . import elevate
        if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= 12:
            raise PlanError('1_to_12_steps_required')
        steps, effects, aliases = [], set(), set()
        for raw in descriptors:
            if not isinstance(raw, dict) or raw.get('kind') not in ('https_auth','backup','clean','migrate','token_create','token_use','token_revoke','token_destroy','inspect','token_recovery'):
                raise PlanError('unsupported_descriptor')
            step = copy.deepcopy(raw)
            kind = step['kind']
            if any(key in step for key in ('command','cmd','tool','tool_input')):
                raise PlanError('executable_descriptor_not_supported')
            credential_index=None
            if kind=='token_recovery':
                step=self.recovery.prepare(step)
                spec,value=_credential(self.host.st,step['credential'])
                try:
                    if self.recovery.identity(step['credential'],spec,value)!=step['recovery_identity']:
                        raise PlanError('recovery_credential_identity_changed')
                    step['credential_epoch']=hmac.new(self.key,bytes(value),hashlib.sha256).hexdigest()
                    step['credential_object']=path_seal(spec['path'],key=self.key)
                finally:elevate.wipe(value)
                step['resolved_ips']=self.resolver('api.cloudflare.com')
            elif kind in ('https_auth', 'token_create', 'token_use', 'token_revoke'):
                allowed = {'kind','credential','url','method','body','content_type','alias','token_field','id_field','token_id','file'}
                if set(step) - allowed:
                    raise PlanError('unsupported_http_field')
                parsed = _url(step.get('url'))
                step['method'] = step.get('method','GET').upper()
                if step['method'] not in ('GET','HEAD','POST','DELETE'):
                    raise PlanError('unsupported_method')
                step['content_type'] = step.get('content_type','application/x-www-form-urlencoded' if parsed.hostname=='api.stripe.com' else 'multipart/form-data' if step.get('file') else 'application/json')
                step['body'] = step.get('body',{})
                if kind == 'token_create':
                    import datetime
                    if parsed.hostname != 'api.cloudflare.com' or parsed.path != '/client/v4/user/tokens' or step['method'] != 'POST':
                        raise PlanError('bounded_temporary_token_adapter_required')
                    step['body']['expires_on'] = datetime.datetime.fromtimestamp(time.time()+590, datetime.timezone.utc).isoformat().replace('+00:00','Z')
                if kind == 'token_revoke':
                    if step.get('token_id') not in aliases or parsed.hostname != 'api.cloudflare.com' or parsed.path != '/client/v4/user/tokens/' or step['method'] != 'DELETE':
                        raise PlanError('exact_created_token_revoke_required')
                _body(step['body'],step['content_type'])
                ref = step.get('credential')
                if isinstance(ref,str) and ref.startswith('plan:'):
                    if ref[5:] not in aliases:
                        raise PlanError('uncreated_token_reference')
                    step['credential_epoch'] = 'created-by-plan:' + ref[5:]
                else:
                    spec,value=_credential(self.host.st,ref)
                    try:
                        from .peer_guard import SecretIndex
                        import base64
                        literal=bytes(value).decode('utf-8')
                        variants={literal,base64.b64encode(value).decode(),base64.urlsafe_b64encode(value).decode(),bytes(value).hex(),urllib.parse.quote(literal,safe=''),urllib.parse.quote_plus(literal)}
                        public=canonical(descriptors)
                        credential_index=SecretIndex(variants,fragment_size=8)
                        if any(variant and variant in public for variant in variants) or credential_index.hits(public):
                            raise PlanError('managed_credential_in_public_scope')
                        if b'\r' in value or b'\n' in value:
                            raise PlanError('invalid_authentication_value')
                        step['credential_epoch']=hmac.new(self.key,bytes(value),hashlib.sha256).hexdigest()
                        step['credential_object']=path_seal(spec['path'],key=self.key)
                    finally:
                        elevate.wipe(value)
                step['resolved_ips'] = self.resolver(parsed.hostname)
                if step.get('file'):
                    from . import danger
                    upload=step['file']
                    if (not isinstance(upload,dict) or set(upload)!={'path','field'}
                            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',upload['field'])
                            or step['method']!='POST' or step['content_type']!='multipart/form-data'):
                        raise PlanError('fixed_upload_required')
                    file_seal=path_seal(upload['path'],key=self.key)
                    if not file_seal['exists'] or file_seal.get('content_digest') is None or danger.cred_path(file_seal['path']):
                        raise PlanError('ordinary_upload_file_required')
                    from . import media
                    fd=os.open(file_seal['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    try:
                        if _stat(os.fstat(fd))!=file_seal['chain'][-1]['stat']:raise PlanError('upload_changed')
                        _,contains_secret=media._hash_and_scan(fd,os.fstat(fd).st_size,True,False,owner=True)
                        if contains_secret:raise PlanError('secret_upload_refused')
                        offset=0;tail=''
                        while credential_index is not None:
                            part=os.pread(fd,65536,offset)
                            if not part:break
                            offset+=len(part)
                            text=tail+part.decode('utf-8','replace')
                            if credential_index.hits(text):raise PlanError('managed_credential_in_upload')
                            tail=text[-256:]
                        if _stat(os.fstat(fd))!=file_seal['chain'][-1]['stat']:raise PlanError('upload_changed')
                    finally:os.close(fd)
                    step['file_seal']=file_seal
                actual, individual = _http_effects(kind,parsed,step['method'],step['body'])
                if step.get('file'):
                    actual=tuple(sorted(set(actual)|{'sensitive_delivery'}))
                step['effects'],step['individual_uv']=list(actual),False  # Display effects never escalate UV.
                if kind == 'token_create':
                    alias=step.get('alias')
                    if not isinstance(alias,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}',alias) or alias in aliases:
                        raise PlanError('token_alias')
                    aliases.add(alias)
                    if step.get('token_field') not in ('token','value','result.value') or step.get('id_field') not in ('id','result.id'):
                        raise PlanError('token_response_projection')
                if kind == 'token_revoke' and not isinstance(ref,str):
                    raise PlanError('token_reference')
            elif kind == 'token_destroy':
                if set(step) != {'kind','alias'} or step['alias'] not in aliases:
                    raise PlanError('token_alias')
                step['effects']=['sensitive_storage']
            elif kind == 'inspect':
                if (set(step) != {'kind','path','projection'} and not (set(step)=={'kind','path','other','projection'} and step.get('projection')=='digest-compare')) or step['projection'] not in ('metadata','keys','url-metadata','digest-compare'):
                    raise PlanError('safe_projection_required')
                step['source_seal']=path_seal(step['path'],key=self.key)
                if not step['source_seal']['exists']:
                    raise PlanError('missing_source')
                if step.get('other'):
                    step['other_seal']=path_seal(step['other'],key=self.key)
                    if not step['other_seal']['exists']:raise PlanError('missing_comparison')
                from . import danger
                step['individual_uv'] = False  # Explicit Agent J command executors own UV, never path guesses.
                step['effects']=['sensitive_storage']
            else:
                if set(step) != {'kind','source','destination'}:
                    raise PlanError('file_operation_shape')
                step['source_seal']=path_seal(step['source'],key=self.key)
                step['destination_seal']=path_seal(step['destination'],key=self.key)
                if not step['source_seal']['exists'] or step['destination_seal']['exists']:
                    raise PlanError('source_exists_destination_absent_required')
                from . import danger
                src,dst=step['source_seal']['path'],step['destination_seal']['path']
                paths = [src,dst]
                for item in step['source_seal'].get('tree', []):
                    paths.extend((os.path.join(src,item['name']),os.path.join(dst,item['name'])))
                state_root = str(self.host.st.root)
                if any(danger.hook_path(p) or danger.service_path(p) for p in paths) or any(p == state_root or p.startswith(state_root+'/') or state_root.startswith(p.rstrip('/')+'/') for p in (src,dst)):
                    raise PlanError('approval_root_requires_individual_operation')
                if dst.startswith(src.rstrip('/')+'/') or src.startswith(dst.rstrip('/')+'/'):
                    raise PlanError('overlapping_objects')
                step['individual_uv'] = False  # No effect-based UV inside a sealed ordinary plan.
                step['source'],step['destination']=src,dst
                step['effects']=['sensitive_storage'] + (['irreversible_destroy'] if kind in ('clean','migrate') else [])
            effects.update(step['effects'])
            steps.append(step)
        language='en' if self.host.lang=='en' else 'zh'
        view = '\n'.join(f'{i+1}. '+describe_step(step,language) for i,step in enumerate(steps))
        count=1+sum(bool(step.get('individual_uv')) for step in steps)
        display = (f'Up to 12 steps / 10 minutes / each once. Expect {count} confirmations.\n' if language=='en' else f'最多12步 / 10分钟 / 每步一次；预计确认{count}次。\n')+view
        if len(display.encode()) > 20000:
            raise PlanError('plan_display_too_large')
        h=self.host
        return SealedPlan(self,steps,h._task_id(),str(h.hist.epoch),h.channel,int(time.time())+600,display,tuple(sorted(effects)),['pending']*len(steps))

    async def approve(self, descriptors):
        # Reserve one operation lineage before any seal/network await. A second caller joins the same ledger.
        from .approval_review import digest
        key=(self.host._task_id(),digest(descriptors))
        locks=self.__dict__.setdefault('_approval_locks',{})
        lock=locks.setdefault(key,asyncio.Lock())
        async with lock:
            return await self._approve(descriptors)

    async def _approve(self, descriptors):
        from . import policy_registry, approvals
        from .approval_review import snapshot
        origin=snapshot(self.host) or {}
        if not origin.get('verified') or origin.get('suspicious') or self.host.cur_turn is None:
            return {'ok':False,'why':'trusted_owner_task_required'}
        from . import approval_review
        request_key=(self.host._task_id(),approval_review.digest(descriptors))
        operation = approval_review.digest({'channel':self.host.channel, 'session':origin.get('session'),
                                           'root':origin.get('root_message_id'), 'descriptors':descriptors})
        if await asyncio.to_thread(lambda: any(self.operation_once.consumed(operation+':'+str(i)) for i in range(len(descriptors)))):
            return {'ok':False,'why':'operation_lineage_already_consumed_or_terminal'}
        prior=self.requests.get(request_key)
        if prior is not None:
            if any(state!='pending' for state in prior.states):
                return {'ok':False,'why':'operation_lineage_already_consumed_or_terminal'}
            if prior.approved and self._authority(prior):
                return {'ok':True,'plan':prior.rid,'steps':len(prior.steps),'expires':int(prior.expires)}
            old=self.host.__dict__.get('_approval_finished',{}).get(prior.rid)
            if time.time()<prior.expires and not (old is not None and old.review_state=='expired'):
                return {'ok':False,'why':'review_pending','plan':prior.rid}
            send=getattr(self.host.agent,'cur_send',None)
            intent=getattr(send,'approval_review',None) or {}
            old=self.host.__dict__.get('_approval_finished',{}).get(prior.rid)
            if (intent.get('op')!='renew' or intent.get('id')!=prior.rid or old is None
                    or old.review_state!='expired' or not old.review_waiter_ended):
                return {'ok':False,'why':'explicit_safe_renewal_required'}
        try:
            plan=await asyncio.to_thread(self.seal,descriptors)
        except (ValueError,OSError,TypeError):
            return {'ok':False,'why':'invalid_bounded_plan'}
        if prior is not None:
            plan.renewed_from=prior.rid
        individual=sum(bool(step.get('individual_uv')) for step in plan.steps)
        count=1+individual
        self.host.agent_notice((f'Expect {count} confirmation(s); {individual} step(s) must each be verified separately. Scope changes may require further confirmation.' if self.host.lang=='en' else f'这件事预计需要你确认{count}次；其中{individual}步必须分别验证。范围变化时会再请你确认。'))
        plan.operation_once_key = operation
        self.requests[request_key]=plan
        answer=await self.host.ask(TOOL,{'scope':plan.public(),'summary':plan.display+'\nCommand details:\n'+approval_review.canonical(plan.public()['actions'])},batch=False,always_ask=True,require_passkey=True,
                                  grant_info=True,_company_proposal=policy_registry.HOST_CARD,_bounded_context=plan)
        info=answer.get('grant_info') or {}
        if answer.get('review_pending'):
            plan.rid=answer['request_id']
            return {'ok':False,'why':'review_pending','plan':plan.rid}
        if answer.get('behavior')!='allow' or not self._authority(plan):
            ended=self.host.__dict__.get('_approval_finished',{}).get(plan.rid)
            if ended is None or ended.review_state!='expired':
                plan.states=['denied']*len(plan.steps)
                for i in range(len(plan.steps)):
                    await asyncio.to_thread(self.operation_once.finish,plan.operation_once_key+':'+str(i),plan.rid,'denied')
            return {'ok':False,'why':'not_approved'}
        plan.rid,plan.device,plan.signing_key=info['rid'],info['device'],info['sign_pub']
        plan.approved=True
        self.plans[plan.rid]=plan
        return {'ok':True,'plan':plan.rid,'steps':len(plan.steps),'expires':int(plan.expires)}

    def finish_approval(self,plan,rid,device,signing_key,allowed):
        # The original controller received the fresh UV; no work was executed while the request was questioned.
        if not allowed or time.time()>=plan.expires or any(state!='pending' for state in plan.states):
            plan.approved=False
            ended=self.host.__dict__.get('_approval_finished',{}).get(rid)
            if not allowed and (ended is None or ended.review_state!='expired'):
                plan.states=['denied']*len(plan.steps)
                for i in range(len(plan.steps)):
                    self.operation_once.defer_finish(plan.operation_once_key+':'+str(i),rid,'denied')
            return
        plan.rid,plan.device,plan.signing_key=rid,device,signing_key
        plan.approved=True
        self.plans[rid]=plan

    def _credential(self,plan,step):
        from . import elevate
        ref=step['credential']
        if ref.startswith('plan:'):
            item=plan.token_values.get(ref[5:])
            if item is None or item.get('revoked'):
                raise PlanError('token_unavailable')
            if urllib.parse.urlsplit(step['url']).netloc != item['origin']:
                raise PlanError('token_destination_changed')
            return bytearray(item['value'])
        spec,value=_credential(self.host.st,ref)
        if (hmac.new(self.key,bytes(value),hashlib.sha256).hexdigest()!=step['credential_epoch']
                or path_seal(spec['path'],key=self.key)!=step['credential_object']):
            elevate.wipe(value)
            raise PlanError('credential_epoch_changed')
        return value

    async def execute(self,rid,index):
        from . import elevate,policy_registry,danger
        plan=self.plans.get(rid)
        if (plan is None or not plan.approved or not self._authority(plan) or type(index) is not int
                or not 0<=index<len(plan.steps) or plan.states[index]!='pending'):
            return {'ok':False,'why':'plan_not_live_or_step_used'}
        step=copy.deepcopy(plan.steps[index])
        # Approval cannot turn an unknown executable descriptor into authority.
        if (step.get('kind') not in ('https_auth','backup','clean','migrate','token_create','token_use','token_revoke','token_destroy','inspect','token_recovery')
                or any(key in step for key in ('command','cmd','tool','tool_input'))
                or danger.unresolved_execution(step.get('tool', ''), step.get('tool_input', step))):
            plan.states[index]='denied'
            return {'ok':False,'why':'unresolved_execution'}
        if step['kind']=='token_revoke':
            token=plan.token_values.get(step['token_id'])
            if token is None:
                return {'ok':False,'why':'created_token_missing'}
            step['url'] += urllib.parse.quote(token['id'],safe='')
        operation = plan.operation_once_key+':'+str(index)
        plan.states[index]='reserving'  # atomic in-memory claim before the disk await
        if not await asyncio.to_thread(self.operation_once.reserve,operation,rid):
            plan.states[index]='unknown'
            return {'ok':False,'why':'operation_already_consumed'}
        if not self._authority(plan):
            plan.states[index]='revoked'
            await asyncio.to_thread(self.operation_once.finish,operation,rid,'revoked')
            return {'ok':False,'why':'authority_changed'}
        plan.states[index]='reserved'
        # No heuristic per-step UV layer. Named Agent J commands enforce their
        # own list checks at their actual executor; this plan keeps its seals.
        try:
            if not self._authority(plan):
                raise PlanError('authority_changed')
            result=await asyncio.to_thread(self._execute,plan,step)
        except Exception:
            await asyncio.to_thread(self.operation_once.finish,operation,rid,'unknown')
            plan.states[index]='unknown'  # never turn an uncertain external mutation back into a retryable step
            result={'ok':False,'why':'execution_failed_or_unknown'}
            record=step.get('record') if step['kind']=='token_recovery' else step.get('_recovery_record')
            if step['kind']=='token_revoke':record=(plan.token_values.get(step['token_id']) or {}).get('recovery_record')
            if record:
                result['recovery']=self.recovery.display(self.recovery.get(record),'en' if self.host.lang=='en' else 'zh')
                result['message']=result['recovery']['message']
            return result
        await asyncio.to_thread(self.operation_once.finish,operation,rid,'completed')
        plan.states[index]='completed'
        self.host.st.log('bounded_plan_step',id=rid,step=index,result='completed')
        return {'ok':True,'plan':rid,'step':index,'result':result}

    def _execute(self,plan,step):
        from . import elevate
        kind=step['kind']
        if kind=='token_recovery':
            if self.resolver('api.cloudflare.com')!=step['resolved_ips']:raise PlanError('endpoint_changed')
            from . import secret_xfer
            spec=secret_xfer.registry_dest(self.host.st,step['credential'][7:])
            auth=self._credential(plan,step)
            try:
                return self.recovery.perform(step,auth,spec,self.transport,plan.rid,'en' if self.host.lang=='en' else 'zh')
            finally:elevate.wipe(auth)
        if kind in ('https_auth','token_create','token_use','token_revoke'):
            parsed=_url(step['url'])
            if self.resolver(parsed.hostname)!=step['resolved_ips']:
                raise PlanError('endpoint_changed')
            auth=self._credential(plan,step)
            try:
                if not self._authority(plan):
                    raise PlanError('authority_changed')
                transport_step=copy.deepcopy(step)
                body=_body(step['body'],step['content_type'])
                if step.get('file'):
                    if path_seal(step['file']['path'],key=self.key)!=step['file_seal']:
                        raise PlanError('upload_changed')
                    fd=os.open(step['file']['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    try:
                        if _stat(os.fstat(fd))!=step['file_seal']['chain'][-1]['stat']:raise PlanError('upload_changed')
                        chunks=[];total=0
                        while True:
                            chunk=os.read(fd,65536)
                            if not chunk:break
                            total+=len(chunk)
                            if total>MAX_BYTES:raise PlanError('upload_too_large')
                            chunks.append(chunk)
                        payload=b''.join(chunks)
                        if hmac.new(self.key,payload,hashlib.sha256).hexdigest()!=step['file_seal']['content_digest']:raise PlanError('upload_changed')
                    finally:os.close(fd)
                    boundary='agentj-'+secrets.token_hex(24)
                    fields=[]
                    for name,value in step['body'].items():
                        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',name):raise PlanError('multipart_field')
                        fields.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+str(value)+'\r\n').encode())
                    fields.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+step['file']['field']+'"; filename="upload.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+payload+b'\r\n')
                    fields.append(('--'+boundary+'--\r\n').encode())
                    body=b''.join(fields)
                    transport_step['content_type']='multipart/form-data; boundary='+boundary
                if kind=='token_create':
                    from . import secret_xfer
                    spec=secret_xfer.registry_dest(self.host.st,step['credential'][7:])
                    step['_recovery_record']=self.recovery.begin(plan,step,step['credential'],spec,auth)
                elif kind=='token_revoke':
                    original=plan.token_values[step['token_id']]
                    if original.get('recovery_record'):
                        self.recovery.change(original['recovery_record'],state='revocation_unknown',last_approval=plan.rid)
                status,data=self.transport(transport_step,auth,body)
            finally:
                elevate.wipe(auth)
            if kind in ('token_create','token_revoke'):
                parsed_data=json.loads(data)
                if not 200<=status<300 or not isinstance(parsed_data,dict) or parsed_data.get('success') is not True or parsed_data.get('errors'):
                    raise PlanError('token_operation_not_confirmed')
            if kind=='token_create':
                def extract(field):
                    value=parsed_data
                    for key in field.split('.'):value=value[key]
                    return value
                identifier=extract(step['id_field'])
                if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identifier):raise PlanError('bad_token_identifier')
                self.recovery.created(step['_recovery_record'],identifier,parsed_data['result'])
                token=extract(step['token_field'])
                if not isinstance(token,str) or not 1<=len(token)<=16384:raise PlanError('bad_token_response')
                plan.token_values[step['alias']]={'value':bytearray(token.encode()),'id':identifier,'origin':parsed.netloc,'revoked':False,'recovery_record':step['_recovery_record']}
            if kind=='token_revoke':
                token=plan.token_values[step['token_id']]
                receipt=parsed_data.get('result')
                if not isinstance(receipt,dict) or receipt.get('id')!=token['id']:
                    raise PlanError('token_revocation_receipt_mismatch')
                if token.get('recovery_record'):self.recovery.change(token['recovery_record'],state='revoked_confirmed',last_approval=plan.rid)
                token['revoked']=True
            # Never return the provider's raw response or a returned credential to an Agent.
            result={'status':status,'response_bytes':len(data),'untrusted_data':True}
            record=step.get('_recovery_record') if kind=='token_create' else plan.token_values[step['token_id']].get('recovery_record') if kind=='token_revoke' else None
            if record:result['recovery']=self.recovery.display(self.recovery.get(record),'en' if self.host.lang=='en' else 'zh')
            return result
        if kind=='token_destroy':
            token=plan.token_values.get(step['alias'])
            if token is None or not token.get('revoked'):
                raise PlanError('revoke_before_destroy')
            elevate.wipe(token['value']);token['value']=bytearray()
            return {'destroyed':True}
        if kind=='inspect':
            if path_seal(step['path'],key=self.key)!=step['source_seal']:
                raise PlanError('object_changed')
            if step.get('other'):
                current=path_seal(step['other'],key=self.key)
                if current!=step['other_seal']:raise PlanError('comparison_changed')
                return {'equal':hmac.compare_digest(step['source_seal'].get('content_digest','x'),current.get('content_digest','y'))}
            info=os.lstat(step['path'])
            # Safe metadata projection cannot leak URL userinfo, hook commands or raw configuration values.
            return {'exists':True,'regular_file':stat.S_ISREG(info.st_mode),'bytes':info.st_size,'mode':stat.S_IMODE(info.st_mode)}
        return copy_then_remove(step,self.key,remove=kind in ('clean','migrate'))

    async def recovery_view(self):
        from .approval_review import snapshot
        source=snapshot(self.host) or {}
        if source.get('verified') is not True or source.get('suspicious') or self.host.cur_turn is None:
            return {'ok':False,'why':'trusted_owner_task_required'}
        records=await asyncio.to_thread(self.recovery.list,'en' if self.host.lang=='en' else 'zh')
        return {'ok':True,'records':records,'authority_restored':False}

    def end_all(self):
        from . import elevate
        for plan in self.plans.values():
            plan.approved=False
            for i, state in enumerate(plan.states):
                if state == 'pending' and getattr(plan,'operation_once_key',None):
                    self.operation_once.defer_finish(plan.operation_once_key+':'+str(i),plan.rid,'revoked')
            plan.states=["revoked" if state=="pending" else state for state in plan.states]
            for item in plan.token_values.values():
                # Durable ID + the original named management credential can reconcile/revoke without the child value.
                # A legacy value with no durable identifier is kept private until it can be reconciled.
                if item.get('revoked') or item.get('recovery_record') in self.recovery.records:elevate.wipe(item['value'])
        self.plans.clear()


def manager(host):
    if '_bounded_plan_manager' not in host.__dict__:host._bounded_plan_manager=Plans(host)
    return host._bounded_plan_manager


def _parent_fd(path,seal=None):
    parts=Path(path).parts
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        approved={entry['path']:entry.get('stat') for entry in (seal or {}).get('chain',[]) if entry.get('exists')}
        current='/'
        if seal is not None and _stat(os.fstat(fd))!=approved.get(current):raise PlanError('ancestor_changed')
        for piece in parts[1:-1]:
            nxt=os.open(piece,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
            current=os.path.join(current,piece)
            if seal is not None and _stat(os.fstat(fd))!=approved.get(current):raise PlanError('ancestor_changed')
        return fd,parts[-1]
    except Exception:
        os.close(fd);raise


def _rename_exclusive(src_fd,src,dst_fd,dst):
    import ctypes
    lib=ctypes.CDLL(None,use_errno=True)
    call=getattr(lib,'renameat2',None)
    if call is None:
        raise PlanError('atomic_no_replace_unavailable')
    call.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    call.restype=ctypes.c_int
    if call(src_fd,os.fsencode(src),dst_fd,os.fsencode(dst),1)!=0:
        raise PlanError('object_or_destination_changed')


def copy_then_remove(step,key,remove=False):
    """Bound local file operation. Moves preserve original bytes in the approved recovery/destination.

    No arbitrary shell, no network copy, no symlink following and no irreversible recursive unlink executor.
    Cross-filesystem moves require separate owner handling rather than deleting a source after an uncertain copy.
    """
    if path_seal(step['source'],key=key)!=step['source_seal'] or path_seal(step['destination'],key=key)!=step['destination_seal']:
        raise PlanError('object_changed')
    sp,sn=_parent_fd(step['source'],step['source_seal'])
    try:dp,dn=_parent_fd(step['destination'],step['destination_seal'])
    except Exception:
        os.close(sp);raise
    try:
        expected=step['source_seal']['chain'][-1]['stat']
        if _stat(os.stat(sn,dir_fd=sp,follow_symlinks=False))!=expected:
            raise PlanError('object_changed')
        if remove:
            if os.fstat(sp).st_dev!=os.fstat(dp).st_dev:
                raise PlanError('cross_device_move_requires_individual_uv')
            _rename_exclusive(sp,sn,dp,dn)
            moved=path_seal(step['destination'],key=key)
            # Compare the exact moved inode and recursive approved contents, not its old pathname.
            if moved['chain'][-1].get('stat')!=expected or moved.get('tree')!=step['source_seal'].get('tree') or moved.get('content_digest')!=step['source_seal'].get('content_digest'):
                try:_rename_exclusive(dp,dn,sp,sn)
                except PlanError:pass  # never overwrite a concurrently created source to "roll back"
                raise PlanError('moved_object_changed_preserved')
            return {'moved':True,'source_removed':True,'recovery':step['destination'],'bytes_preserved':True}
        temporary='.agentj-copy-'+secrets.token_hex(16)
        tree={item['name']:item for item in step['source_seal'].get('tree',[])}
        def copy_node(sfd,name,dfd,outname,rel,meta):
            info=os.stat(name,dir_fd=sfd,follow_symlinks=False)
            if _stat(info)!=meta['stat'] or stat.S_ISLNK(info.st_mode):
                raise PlanError('object_changed')
            src=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_DIRECTORY if stat.S_ISDIR(info.st_mode) else 0),dir_fd=sfd)
            try:
                if _stat(os.fstat(src))!=meta['stat']:raise PlanError('object_changed')
                if stat.S_ISDIR(info.st_mode):
                    os.mkdir(outname,0o700,dir_fd=dfd)
                    dest=os.open(outname,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd)
                    try:
                        names=sorted(os.listdir(src))
                        expected_names=sorted(p[len(rel):] for p in tree if p.startswith(rel) and '/' not in p[len(rel):])
                        if names!=expected_names:raise PlanError('object_changed')
                        for child in names:
                            cr=rel+child
                            copy_node(src,child,dest,child,cr+'/' if stat.S_ISDIR(tree[cr]['stat'][3]) else cr,tree[cr])
                        if sorted(os.listdir(src))!=names:raise PlanError('object_changed')
                    finally:os.close(dest)
                else:
                    target=os.open(outname,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dfd)
                    try:
                        h=hmac.new(key,digestmod=hashlib.sha256)
                        while True:
                            data=os.read(src,65536)
                            if not data:break
                            h.update(data)
                            view=memoryview(data)
                            while view:view=view[os.write(target,view):]
                        os.fsync(target)
                        if h.hexdigest()!=meta.get('content_digest') or _stat(os.fstat(src))!=meta['stat']:
                            raise PlanError('object_changed')
                    finally:os.close(target)
            finally:os.close(src)
        rootmeta={'stat':expected,'content_digest':step['source_seal'].get('content_digest')}
        try:
            copy_node(sp,sn,dp,temporary,'' if stat.S_ISDIR(expected[3]) else sn,rootmeta)
            if path_seal(step['source'],key=key)!=step['source_seal']:
                raise PlanError('object_changed')
            _rename_exclusive(dp,temporary,dp,dn)
        except Exception:
            # Remove only our randomly named staging artifact; never overwrite/delete the destination.
            try:
                st=os.stat(temporary,dir_fd=dp,follow_symlinks=False)
                if stat.S_ISREG(st.st_mode):os.unlink(temporary,dir_fd=dp)
            except OSError:pass
            raise
        return {'copied':True,'destination':step['destination'],'source_retained':True}
    finally:
        os.close(sp);os.close(dp)


def caller_origin(host,pid):
    """Reuse the host's live kernel/root proof; never borrow a mutable latest owner turn."""
    from .approval_material import verified_caller_origin
    return verified_caller_origin(host,pid)


def continuation_allowed(host,descriptors):
    from .approval_review import digest, _outbox, thread_ref
    plans=host.__dict__.get('_bounded_plan_manager')
    if plans is None:return False
    prior=plans.requests.get((host._task_id(),digest(descriptors)))
    if prior is None:return False
    send=getattr(host.agent,'cur_send',None)
    if getattr(send,'bounded_plan_resume',None)==prior.rid:
        return True
    meta=getattr(send,'approval_review',None) or {}
    event=_outbox(host).get(meta.get('event_id'))
    return bool(event and event.get('target')==thread_ref(host.agent) and meta.get('id')==prior.rid
                and meta.get('op') in ('question','renew'))


def describe_step(step,language='zh'):
    """Fixed host wording from normalized operands; Agent prose never determines the approved scope."""
    from .approval_review import safe_text
    en=language=='en';kind=step['kind']
    if kind=='token_recovery':
        operation=('Verify' if step['operation']=='verify' else 'Revoke') if en else ('核实' if step['operation']=='verify' else '撤销')
        text=(f'{operation} temporary token {step["created_token_id"]} at {step["url"]}; recovery record {step["record"]}; requested expiry {step["requested_expires_at"]}; one new request, no automatic retry.' if en else f'{operation}临时令牌{step["created_token_id"]}；服务{step["url"]}；恢复记录{step["record"]}；请求到期时间{step["requested_expires_at"]}；仅1次新请求，不自动重试。')
        text+=(f' Management credential name {step["credential"][7:]}.' if en else f' 管理认证名称{step["credential"][7:]}。')
    elif kind in ('backup','migrate','clean'):
        source,destination=step['source'],step['destination']
        verbs={'backup':('Back up','备份'),'migrate':('Move','迁移'),'clean':('Move to recovery','移到恢复位置')}
        v=verbs[kind][0 if en else 1]
        count=1+len(step.get('source_seal',{}).get('tree',[]))
        text=(f'{v} {source} → {destination}; {count} sealed object(s), preserve all bytes, no overwrite; once.' if en else
              f'{v}：{source} → {destination}；共{count}个已核对对象，保留全部字节，不覆盖；仅1次。')
    elif kind=='inspect':
        mode=step['projection']
        if mode=='digest-compare':
            text=(f'Compare {step["path"]} with {step.get("other", "the approved reference")}; return equal/not equal only; once.' if en else
                  f'比较{step["path"]}与{step.get("other","已批准参照")}是否一致；只返回一致与否；仅1次。')
        elif mode=='keys':
            text=(f'Inspect configuration file metadata for {step["path"]}; no configuration values; once.' if en else
                  f'核查{step["path"]}的文件信息；不返回配置值；仅1次。')
        elif mode=='url-metadata':
            text=(f'Inspect metadata of the file containing URLs at {step["path"]}; no raw URLs, userinfo or query values; once.' if en else
                  f'核查{step["path"]}的文件信息；不返回原URL、登录信息或查询参数值；仅1次。')
        else:
            text=(f'Inspect file metadata for {step["path"]}; no file contents; once.' if en else
                  f'核查{step["path"]}的文件信息；不返回文件内容；仅1次。')
    elif kind=='token_destroy':
        text=(f'Clear temporary token {step["alias"]} from host memory after confirmed remote revocation; once.' if en else
              f'远端确认撤销后，清除本机内存中的临时令牌{step["alias"]}；仅1次。')
    else:
        p=urllib.parse.urlsplit(step['url']);ref=step['credential'].split(':',1)[-1];body=step.get('body') or {}
        if kind=='token_create':
            scopes=[]
            for policy in body.get('policies',[]) if isinstance(body.get('policies',[]),list) else []:
                if isinstance(policy,dict):
                    scopes.extend(str(group.get('id','?')) for group in policy.get('permission_groups',[]) if isinstance(group,dict))
            text=(f'Create temporary token {step["alias"]} at {step["url"]}, expires {body.get("expires_on")}; permission group IDs: {", ".join(scopes) or "none prelisted"}; once.' if en else
                  f'在{step["url"]}创建临时令牌{step["alias"]}，到期时间{body.get("expires_on")}；权限组标识：{"、".join(scopes) or "未预列"}；仅1次。')
        elif kind=='token_revoke':
            text=(f'Revoke this plan’s created token {step["token_id"]} at {step["url"]}; once.' if en else
                  f'在{step["url"]}撤销本计划创建的临时令牌{step["token_id"]}；仅1次。')
        elif step.get('file'):
            text=(f'Upload the sealed file {step["file"]["path"]} to {step["url"]}; model {body.get("model","provider default")}; one request, no retries.' if en else
                  f'将已核对文件{step["file"]["path"]}上传到{step["url"]}；模型{body.get("model","服务默认")}；仅1次请求，不自动重试。')
        elif p.hostname=='api.stripe.com' and step['method']=='POST':
            ident=p.path.rsplit('/',1)[-1]
            if '/subscriptions/' in p.path and 'cancel_at_period_end' in body:
                operation=('set cancellation at period end' if body['cancel_at_period_end'] else 'remove cancellation at period end') if en else ('设置到期取消' if body['cancel_at_period_end'] else '取消到期取消设置')
                text=f'Subscription {ident}: {operation}; {step["url"]}; once.' if en else f'订阅{ident}：{operation}；服务{step["url"]}；仅1次。'
            elif 'active' in body:
                operation=('enable' if body['active'] else 'disable') if en else ('启用' if body['active'] else '停用')
                text=f'{operation} promotion code {ident}; {step["url"]}; once.' if en else f'{operation}优惠码{ident}；服务{step["url"]}；仅1次。'
            else:
                text=(f'Create promotion code {body.get("code","provider-generated")}, coupon {body.get("coupon","prelisted")}, redemption limit {body.get("max_redemptions","unchanged provider default")}; {step["url"]}; once.' if en else
                      f'创建优惠码{body.get("code","由服务生成")}，对应优惠券{body.get("coupon","按清单")}，使用次数上限{body.get("max_redemptions","服务原默认")}；服务{step["url"]}；仅1次。')
        elif step['method'] in ('GET','HEAD'):
            text=f'Query {step["url"]}; one authenticated request, no retries.' if en else f'查询{step["url"]}；仅1次认证请求，不自动重试。'
        else:
            text=(f'Send one {step["method"]} request to {step["url"]}; model {body.get("model","as listed")}, output token limit {body.get("max_tokens","as listed")}; cost is not verified.' if en else
                  f'向{step["url"]}提交1次{step["method"]}请求；模型{body.get("model","按清单")}，输出Token上限{body.get("max_tokens","按清单")}；费用上限尚未核定。')
        if body:
            def fields(value,prefix=''):
                if isinstance(value,dict):
                    return [part for key,item in value.items() for part in fields(item,prefix+str(key)+'.')]
                if isinstance(value,list):
                    return [part for index,item in enumerate(value) for part in fields(item,prefix+str(index+1)+'.')]
                return [prefix.rstrip('.')+' = '+str(value)]
            scope='; '.join(fields(body))
            text+=(' Request parameters: ' if en else ' 请求范围：')+scope+'.'
        text+=f' Credential name {ref}, authentication only.' if en else f' 认证凭据名称{ref}，仅用于认证。'
    if step.get('individual_uv'):
        text+=' This step requires separate verification.' if en else ' 此步骤须单独验证。'
    return safe_text(text,6000)
