"""Seat API workflow executor (ADR-A193 §1, P126): one OS-sandboxed process per job, never a main-session.

A workflow is an owner-authored directory `<root>/<name>/` = workflow.json + main.py (+ optional knowledge/). Each job
runs `/usr/bin/python3 -I -S main.py` inside bubblewrap: every namespace unshared (no network, no host PIDs/IPC, no
nested user namespaces), an empty environment, a read-only root made of /usr only, private size-capped /tmp and /home,
the workflow read-only at /workflow with knowledge/ hidden there; knowledge/ at /knowledge only with knowledge_read and a
private job dir at /out only with output_files. rlimits (CPU, address space, processes, file size, fds, core) are set
inside the sandbox before main.py starts. bwrap itself gets an empty environment (its own /proc/<pid>/environ would
otherwise be readable from inside) and reads its options from a pipe (`--args`); the job is PID 1 of its own PID
namespace (`--as-pid-1`, no bwrap reaper inside), so killing the bwrap process group tears down every descendant.

First batch, fail closed: `tools` and `network` must be empty (no deterministic broker / egress proxy exists yet) —
anything else is `workflow_permission_unsupported`. Only Linux with bubblewrap that passes a live isolation probe is
`supported`; macOS and everything else is `no_verified_sandbox` (a prompt is not isolation).
Known limits: /proc/self/mountinfo inside still names the host source paths of the binds; output file contents are
neither scanned nor kept (metadata only; the job dir is always deleted); the API freezes published workflow bytes into a private digest-checked stage before any model review await.
Standalone run remains a local owner primitive; Service never binds the mutable owner source directory.

Error codes (BotError): invalid_name, unknown_workflow, invalid_workflow, unsafe_workflow, workflow_too_large,
workflow_permission_unsupported, invalid_schema, schema_mismatch, invalid_input, workflow_unsupported, deadline,
workflow_output_limit, workflow_failed, workflow_bad_output, unsafe_output, safety_refused.
"""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import json
import math
import re
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from ..bots.store import BotError, canonical, slug
from .. import privacy

MAX_FILE = 1 << 20
MAX_FILES = 200
MAX_SCHEMA = 16384
MAX_DEPTH = 8
MAX_INPUT = 256 * 1024
OUT_FILES = 20
OUT_TOTAL = 1 << 20
TMPFS = 16 << 20
RLIMITS = {'NPROC': 64, 'FSIZE': 16 << 20, 'NOFILE': 64, 'CORE': 0}
GRACE = 0.5
PYTHON = '/usr/bin/python3'
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/home/job', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
LIMITS = {'timeout_seconds': (1, 600), 'cpu_seconds': (1, 600), 'memory_mb': (64, 1024), 'output_bytes': (1, 65536)}
_TOP = {'title', 'input_schema', 'output_schema', 'permissions', 'limits'}
_PERMS = {'knowledge_read', 'output_files', 'tools', 'network'}
_KEYS = {'object': {'properties', 'required', 'additionalProperties'}, 'array': {'items', 'minItems', 'maxItems'},
         'string': {'minLength', 'maxLength'}, 'integer': {'minimum', 'maximum'}, 'number': {'minimum', 'maximum'},
         'boolean': set(), 'null': set()}
_BWRAP_NEEDS = ('--size', '--args', '--disable-userns', '--as-pid-1')

# ---------------------------------------------------------------- schema subset

def _nat(v): return type(v) is int and 0 <= v <= 1 << 31
def _num(v): return type(v) in (int, float) and math.isfinite(v)

def _check(s, depth):
    if depth > MAX_DEPTH or not isinstance(s, dict) or type(s.get('type')) is not str or s['type'] not in _KEYS:raise BotError('invalid_schema')
    t = s['type']
    if set(s) - {'type', 'enum'} - _KEYS[t]:raise BotError('invalid_schema')
    if t == 'object':
        props = s.get('properties', {}); req = s.get('required', [])
        if s.get('additionalProperties', True) is not False or not isinstance(props, dict) or 'enum' in s:raise BotError('invalid_schema')
        if not isinstance(req, list) or len(set(map(str, req))) != len(req) or any(type(k) is not str or k not in props for k in req):
            raise BotError('invalid_schema')
        for k, v in props.items():
            if not isinstance(k, str) or not 0 < len(k) <= 128:raise BotError('invalid_schema')
            _check(v, depth+1)
    elif t == 'array':
        if 'items' not in s or 'enum' in s:raise BotError('invalid_schema')
        _check(s['items'], depth+1); _bounds(s, 'minItems', 'maxItems', _nat)
    elif t == 'string':_bounds(s, 'minLength', 'maxLength', _nat)
    elif t in ('integer', 'number'):_bounds(s, 'minimum', 'maximum', _num)
    if 'enum' in s:
        e = s['enum']
        if not isinstance(e, list) or not 0 < len(e) <= 256:raise BotError('invalid_schema')
        bare = {k: v for k, v in s.items() if k != 'enum'}
        for item in e:
            try:_validate(bare, item)
            except BotError:raise BotError('invalid_schema') from None

def _bounds(s, lo, hi, ok):
    for k in (lo, hi):
        if k in s and not ok(s[k]):raise BotError('invalid_schema')
    if lo in s and hi in s and s[lo] > s[hi]:raise BotError('invalid_schema')

def check_schema(schema):
    """Raise BotError('invalid_schema') unless `schema` is inside the strict subset (objects closed, depth ≤ 8, ≤ 16 KiB)."""
    try:size = len(canonical(schema).encode())
    except (TypeError, ValueError, RecursionError):raise BotError('invalid_schema') from None
    if size > MAX_SCHEMA:raise BotError('invalid_schema')
    _check(schema, 1)
    return schema

def _no_constant(_):raise ValueError('non-finite number')

def _same(a, b): return a == b and (type(a) is bool) == (type(b) is bool) and (a is None) == (b is None)

def _validate(s, v):
    t = s['type']
    ok = {'object': type(v) is dict, 'array': type(v) is list, 'string': type(v) is str, 'integer': type(v) is int,
          'number': _num(v), 'boolean': type(v) is bool, 'null': v is None}[t]
    if not ok or ('enum' in s and not any(_same(v, e) for e in s['enum'])):raise BotError('schema_mismatch')
    if t == 'string':n = len(v)
    elif t == 'array':
        n = len(v)
        for item in v:_validate(s['items'], item)
    elif t in ('integer', 'number'):n = v
    else:n = None
    lo, hi = {'string': ('minLength', 'maxLength'), 'array': ('minItems', 'maxItems'),
              'integer': ('minimum', 'maximum'), 'number': ('minimum', 'maximum')}.get(t, (None, None))
    if (lo in s and n < s[lo]) or (hi in s and n > s[hi]):raise BotError('schema_mismatch')
    if t == 'object':
        props = s.get('properties', {})
        if any(k not in props for k in v) or any(k not in v for k in s.get('required', [])):raise BotError('schema_mismatch')
        for k, item in v.items():_validate(props[k], item)

def validate(schema, value):
    """Raise BotError('schema_mismatch') (never echoing the value) unless `value` matches the checked schema."""
    check_schema(schema)
    try:_validate(schema, value)
    except RecursionError:raise BotError('schema_mismatch') from None

# ---------------------------------------------------------------- definitions

def _locate(root, name):
    name = slug(name)
    try:base = Path(os.path.realpath(root))
    except (TypeError, ValueError):raise BotError('unknown_workflow') from None
    d = base / name
    try:st = os.lstat(d)
    except OSError:raise BotError('unknown_workflow') from None
    if stat.S_ISLNK(st.st_mode):raise BotError('unsafe_workflow')
    if not stat.S_ISDIR(st.st_mode):raise BotError('unknown_workflow')
    if os.path.realpath(d) != str(d) or os.path.commonpath([base, os.path.realpath(d)]) != str(base):raise BotError('unsafe_workflow')
    return name, d

def _scan(d):
    """Every regular file (relative posix path, absolute path); refuse symlinks, specials, > 1 MiB, > 200 files."""
    files = []
    def fail(_):raise BotError('unsafe_workflow')
    for top, dirs, names in os.walk(d, followlinks=False, onerror=fail):
        for n in dirs + names:
            p = os.path.join(top, n); st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):raise BotError('unsafe_workflow')
            if stat.S_ISREG(st.st_mode):
                if st.st_size > MAX_FILE:raise BotError('workflow_too_large')
                files.append((Path(p).relative_to(d).as_posix(), p))
                if len(files) > MAX_FILES:raise BotError('workflow_too_large')
    return sorted(files)

def _read(p, limit=MAX_FILE):
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:raise BotError('unsafe_workflow') from None
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise BotError('unsafe_workflow')
        data = f.read(limit+1)
    if len(data) > limit:raise BotError('workflow_too_large')
    return data

def _definition(name, d):
    files = dict(_scan(d))
    if 'workflow.json' not in files or 'main.py' not in files:raise BotError('invalid_workflow')
    k = d / 'knowledge'
    if os.path.lexists(k) and not stat.S_ISDIR(os.lstat(k).st_mode):raise BotError('invalid_workflow')
    try:raw = json.loads(_read(d/'workflow.json').decode('utf-8'), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):raise BotError('invalid_workflow') from None
    if not isinstance(raw, dict) or set(raw) != _TOP:raise BotError('invalid_workflow')
    title, perms, limits = raw['title'], raw['permissions'], raw['limits']
    if type(title) is not str or not 0 < len(title) <= 80:raise BotError('invalid_workflow')
    if not isinstance(perms, dict) or set(perms) != _PERMS or type(perms['knowledge_read']) is not bool \
            or type(perms['output_files']) is not bool or type(perms['tools']) is not list or type(perms['network']) is not list:
        raise BotError('invalid_workflow')
    if perms['tools'] or perms['network']:raise BotError('workflow_permission_unsupported')   # no broker / proxy yet
    if not isinstance(limits, dict) or set(limits) != set(LIMITS) or \
            any(type(limits[k]) is not int or not lo <= limits[k] <= hi for k, (lo, hi) in LIMITS.items()):
        raise BotError('invalid_workflow')
    for key in ('input_schema', 'output_schema'):
        check_schema(raw[key])
        if raw[key]['type'] != 'object':raise BotError('invalid_schema')
    return {'name': name, **raw, 'knowledge': os.path.isdir(k)}, files

def load(root, name):
    """The validated definition: workflow.json fields + name + knowledge (whether knowledge/ exists)."""
    name, d = _locate(root, name)
    return _definition(name, d)[0]

def digest(root, name):
    """sha256 over the canonical definition and every file's relative path + sha256 (the contract snapshot)."""
    name, d = _locate(root, name)
    definition, files = _definition(name, d)
    h = hashlib.sha256(b'agentj-workflow-v1\0' + canonical(definition).encode() + b'\0')
    for rel, p in sorted(files.items()):
        h.update(rel.encode() + b'\0' + hashlib.sha256(_read(p)).hexdigest().encode() + b'\n')
    return h.hexdigest()

def freeze(root, name, expected_digest):
    """Copy a published contract through directory FDs, then verify the exact stage digest.

    No caller-provided path or symlink is followed. The returned TemporaryDirectory
    belongs to the request and must be cleaned after sandbox/model awaits finish.
    """
    name,d=_locate(root,name)
    if not isinstance(expected_digest,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_digest):
        raise BotError('contract_changed')
    stage=tempfile.TemporaryDirectory(prefix='agentj-api-workflow-')
    dest=Path(stage.name)/name
    dest.mkdir(mode=0o700)
    count=0
    def copy_dir(fd,target):
        nonlocal count
        for filename in sorted(os.listdir(fd)):
            st=os.stat(filename,dir_fd=fd,follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child=os.open(filename,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                try:
                    folder=target/filename;folder.mkdir(mode=0o700)
                    copy_dir(child,folder)
                finally:os.close(child)
            elif stat.S_ISREG(st.st_mode):
                count+=1
                if count>MAX_FILES or st.st_size>MAX_FILE:raise BotError('workflow_too_large')
                child=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                with os.fdopen(child,'rb') as f:
                    before=os.fstat(f.fileno())
                    if not stat.S_ISREG(before.st_mode):raise BotError('unsafe_workflow')
                    data=f.read(MAX_FILE+1);after=os.fstat(f.fileno())
                if len(data)>MAX_FILE:raise BotError('workflow_too_large')
                if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                    raise BotError('contract_changed')
                output=os.open(target/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(output,'wb') as f:f.write(data)
            else:raise BotError('unsafe_workflow')
    try:
        fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:copy_dir(fd,dest)
        finally:os.close(fd)
        if digest(stage.name,name)!=expected_digest:raise BotError('contract_changed')
        return stage
    except OSError:
        stage.cleanup()
        raise BotError('unsafe_workflow') from None
    except BaseException:
        stage.cleanup()
        raise

# ---------------------------------------------------------------- sandbox

def _bwrap():
    return '/usr/bin/bwrap' if os.access('/usr/bin/bwrap', os.X_OK) else shutil.which('bwrap')

def _boot(limits):
    """Runs as the sandbox's first Python: lower the rlimits (inside the new user namespace, so RLIMIT_NPROC counts only
    this job's processes), then exec the workflow in a fresh isolated interpreter."""
    lim = {'CPU': (limits['cpu_seconds'], limits['cpu_seconds']+1), 'AS': (limits['memory_mb'] << 20,)*2,
           **{k: (v, v) for k, v in RLIMITS.items()}}
    sets = ';'.join(f'r.setrlimit(r.RLIMIT_{k},{v!r})' for k, v in lim.items())
    return f"import os,resource as r;{sets};os.execv({PYTHON!r},['python3','-I','-S','-B','/workflow/main.py'])"

def _argv(d, out, perms, limits):
    a = ['--unshare-all', '--unshare-user', '--disable-userns', '--die-with-parent', '--new-session', '--as-pid-1', '--clearenv']
    for k, v in ENV.items():a += ['--setenv', k, v]
    a += ['--ro-bind', '/usr', '/usr']
    for p in ('/bin', '/sbin', '/lib', '/lib64'):
        if os.path.islink(p):a += ['--symlink', os.readlink(p), p]
        elif os.path.isdir(p):a += ['--ro-bind', p, p]
    a += ['--proc', '/proc', '--dev', '/dev', '--remount-ro', '/dev', '--size', str(TMPFS), '--tmpfs', '/tmp',
          '--size', str(TMPFS), '--tmpfs', '/home', '--dir', '/home/job', '--ro-bind', str(d), '/workflow']
    if os.path.isdir(os.path.join(d, 'knowledge')):
        a += ['--size', '4096', '--tmpfs', '/workflow/knowledge', '--remount-ro', '/workflow/knowledge']
        if perms['knowledge_read']:a += ['--ro-bind', os.path.join(d, 'knowledge'), '/knowledge']
    if perms['output_files']:a += ['--bind', str(out), '/out']
    return a + ['--remount-ro', '/', '--chdir', '/workflow'], [PYTHON, '-I', '-S', '-B', '-c', _boot(limits)]

def _command(d, out, perms, limits):
    """(argv, fd): bwrap options travel through a pipe (`--args`), only the interpreter command is on argv."""
    opts, cmd = _argv(d, out, perms, limits)
    r = _args_pipe(opts)
    return [_bwrap(), '--args', str(r), '--', *cmd], r

def _args_pipe(args):
    data = b''.join(x.encode() + b'\0' for x in args)
    r, w = os.pipe()
    try:
        if len(data) > 60000:raise BotError('workflow_failed')
        os.write(w, data)
    except BaseException:
        os.close(r);raise
    finally:os.close(w)
    return r

def _job():
    job = Path(tempfile.mkdtemp(prefix='agentj-wf-'))
    os.chmod(job, 0o700); (job/'out').mkdir(mode=0o700)
    return job

def _signal(pid, sig):
    with contextlib.suppress(ProcessLookupError, PermissionError):os.killpg(pid, sig)

# ---------------------------------------------------------------- qualification probe

_PROBE = r'''
import json, os, socket, sys
p = json.load(sys.stdin); r = {}
def t(k, f):
    try: f(); r[k] = True
    except Exception: r[k] = False
def env():
    blobs = [repr(dict(os.environ))]
    for f in ('/proc/self/environ', '/proc/1/environ'):
        try: blobs.append(open(f, 'rb').read().decode('latin-1'))
        except OSError: pass
    if any(p['canary'] in b for b in blobs): return
    raise RuntimeError()
def tcp():
    s = socket.create_connection(('127.0.0.1', p['port']), timeout=2); s.close()
def unix():
    s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(p['unix']); s.close()
def tmp():
    if not os.listdir('/tmp'): raise RuntimeError()
t('home', lambda: open(p['home']).read())
t('env', env)
t('tcp', tcp)
t('unix', unix)
t('write_workflow', lambda: open('/workflow/x', 'w').write('x'))
t('write_host', lambda: open(p['host'], 'w').write('x'))
t('write_root', lambda: open('/x', 'w').write('x'))
t('tmp', tmp)
t('out', lambda: open('/out/ok', 'w').write('x'))
print(json.dumps(r))
'''

_QUALIFIED = None
_LOCK = threading.Lock()

def _probe():
    canary = 'AGENTJ_WF_CANARY_' + os.urandom(8).hex()
    base = Path(tempfile.mkdtemp(prefix='agentj-wf-probe-'))
    marker = None; srv = usrv = None; r = None
    try:
        os.chmod(base, 0o700)
        d = base/'probe'; d.mkdir(); (d/'main.py').write_text(_PROBE)
        out = base/'out'; out.mkdir(mode=0o700)
        (base/'tmp-marker').write_text('x')
        fd, marker = tempfile.mkstemp(prefix='.agentj-wf-probe-', dir=os.path.expanduser('~'))
        os.write(fd, b'secret'); os.close(fd)
        srv = socket.socket(); srv.bind(('127.0.0.1', 0)); srv.listen(4); srv.settimeout(0)
        usrv = socket.socket(socket.AF_UNIX); usrv.bind(str(base/'s.sock')); usrv.listen(4)
        if os.path.isdir('/tmp') and os.access('/tmp', os.W_OK):
            fd2, tmpmark = tempfile.mkstemp(prefix='agentj-wf-probe-', dir='/tmp'); os.close(fd2)
        else:tmpmark = None
        params = {'canary': canary, 'home': marker, 'port': srv.getsockname()[1], 'unix': str(base/'s.sock'),
                  'host': str(base/'written')}
        limits = {'timeout_seconds': 20, 'cpu_seconds': 10, 'memory_mb': 256, 'output_bytes': 4096}
        argv, r = _command(d, out, {'knowledge_read': False, 'output_files': True}, limits)
        try:
            proc = subprocess.run(argv, input=json.dumps(params).encode(), stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env={**ENV, canary: canary}, pass_fds=(r,),
                                  start_new_session=True, timeout=30)
        finally:
            os.close(r); r = None
            if tmpmark:os.unlink(tmpmark)
        if proc.returncode:return 'probe_failed'
        res = json.loads(proc.stdout.decode())
        if not isinstance(res, dict):return 'probe_failed'
        for k in ('home', 'env', 'tcp', 'unix', 'write_workflow', 'write_host', 'write_root', 'tmp'):
            if res.get(k) is not False:return 'probe_' + k
        if res.get('out') is not True or (base/'written').exists() or (d/'x').exists():return 'probe_failed'
        with contextlib.suppress(BlockingIOError):
            srv.accept();return 'probe_tcp'
        return None
    except (OSError, ValueError, subprocess.SubprocessError, BotError):return 'probe_failed'
    finally:
        for s in (srv, usrv):
            if s:s.close()
        if marker:
            with contextlib.suppress(OSError):os.unlink(marker)
        shutil.rmtree(base, ignore_errors=True)

def qualify():
    """{"supported", "platform", "reason"}: Linux + bubblewrap whose live isolation probe passes; cached per process."""
    global _QUALIFIED
    with _LOCK:
        if _QUALIFIED is None:
            reason = None
            if sys.platform != 'linux':reason = 'no_verified_sandbox'
            elif not (bw := _bwrap()):reason = 'bwrap_missing'
            elif not os.access(PYTHON, os.X_OK) or not os.path.realpath(PYTHON).startswith('/usr/'):reason = 'python_missing'
            else:
                try:
                    help_ = subprocess.run([bw, '--help'], capture_output=True, timeout=10, env=ENV).stdout.decode(errors='replace')
                except (OSError, subprocess.SubprocessError):help_ = ''
                reason = 'bwrap_too_old' if any(f not in help_ for f in _BWRAP_NEEDS) else _probe()
            _QUALIFIED = {'supported': reason is None, 'platform': sys.platform, 'reason': reason}
        return dict(_QUALIFIED)

# ---------------------------------------------------------------- run

def _secret(text):
    return any(p.search(text) for _, p, _ in privacy._SECRETS)

def _strings(v):
    if isinstance(v, str):yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k;yield from _strings(x)
    elif isinstance(v, list):
        for x in v:yield from _strings(x)

def _collect(out):
    files = []; total = 0
    for top, dirs, names in os.walk(out, followlinks=False):
        for n in dirs + names:
            p = os.path.join(top, n); st = os.lstat(p)
            if stat.S_ISDIR(st.st_mode):continue
            if not stat.S_ISREG(st.st_mode):raise BotError('unsafe_output')
            total += st.st_size
            if len(files) >= OUT_FILES or total > OUT_TOTAL:raise BotError('workflow_output_limit')
            rel = Path(p).relative_to(out).as_posix()
            if _secret(rel) or not rel.isprintable() or len(rel) > 255:raise BotError('safety_refused')
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, 'rb') as f:data = f.read(OUT_TOTAL+1)
            if len(data) != st.st_size:raise BotError('unsafe_output')
            files.append({'name': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return sorted(files, key=lambda f: f['name'])

def _prepare(root, name, value):
    q = qualify()
    if not q['supported']:raise BotError('workflow_unsupported')
    name, d = _locate(root, name)
    definition = _definition(name, d)[0]
    validate(definition['input_schema'], value)
    if any(_secret(s) for s in _strings(value)):raise BotError('invalid_input')
    data = canonical(value).encode()
    if len(data) > MAX_INPUT:raise BotError('invalid_input')
    return d, definition, data

async def _feed(w, data):
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        w.write(data);await w.drain()
    with contextlib.suppress(Exception):w.close()

async def _stop(proc):
    if proc.returncode is not None:return
    _signal(proc.pid, signal.SIGTERM)
    try:await asyncio.wait_for(proc.wait(), GRACE)
    except TimeoutError:pass
    finally:
        if proc.returncode is None:_signal(proc.pid, signal.SIGKILL)
    if proc.returncode is None:
        with contextlib.suppress(TimeoutError):await asyncio.wait_for(proc.wait(), 5)

async def run(root, name, value, deadline_seconds=None):
    """Run one job; returns {"output": object, "files": [{"name", "bytes", "sha256"}]} or raises a fixed BotError."""
    if deadline_seconds is not None and (not _num(deadline_seconds) or type(deadline_seconds) is bool):raise BotError('invalid_input')
    d, definition, data = await asyncio.to_thread(_prepare, root, name, value)
    limits, perms = definition['limits'], definition['permissions']
    wall = limits['timeout_seconds'] if deadline_seconds is None else min(limits['timeout_seconds'], deadline_seconds)
    if wall <= 0:raise BotError('deadline')
    job = await asyncio.to_thread(_job)
    proc = feeder = None
    try:
        argv, r = _command(d, job/'out', perms, limits)
        try:
            proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                                        env=dict(ENV), pass_fds=(r,), start_new_session=True)
        except OSError:raise BotError('workflow_unsupported') from None
        finally:os.close(r)
        out = bytearray()
        try:
            async with asyncio.timeout(wall):
                feeder = asyncio.create_task(_feed(proc.stdin, data))
                while chunk := await proc.stdout.read(65536):
                    out += chunk
                    if len(out) > limits['output_bytes']:raise BotError('workflow_output_limit')
                code = await proc.wait()
        except TimeoutError:raise BotError('deadline') from None
        if code:raise BotError('workflow_failed')
        try:result = json.loads(out.decode('utf-8'), parse_constant=_no_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):raise BotError('workflow_bad_output') from None
        validate(definition['output_schema'], result)
        if any(_secret(s) for s in _strings(result)):raise BotError('safety_refused')
        files = await asyncio.to_thread(_collect, job/'out') if perms['output_files'] else []
        return {'output': result, 'files': files}
    finally:
        try:
            if feeder:feeder.cancel()
            if proc is not None:await _stop(proc)
        finally:
            shutil.rmtree(job, ignore_errors=True)
