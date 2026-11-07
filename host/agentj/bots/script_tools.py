"""Owner-approved immutable Python objects, never executable host paths.

Linux only. No host filesystem roots or network are delegated. Network-enabled
scripts fail closed; broker calls remain host-side named HTTPS tool calls.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from .store import BotError, canonical


def definition(raw, domains):
    from .tools import definition as http_definition
    keys = {'kind', 'code', 'sha256', 'visible_roots', 'network_domains', 'input_bytes'}
    if not isinstance(raw, dict) or raw.get('kind') != 'script':
        raise BotError('invalid_script')
    code = raw.get('code')
    if not isinstance(code, str) or not 1 <= len(code.encode()) <= 32768 or '\x00' in code:
        raise BotError('invalid_script_code')
    from .. import privacy
    if any(pattern.search(code) for _,pattern,_ in privacy._SECRETS):
        raise BotError('script_contains_secret')
    if raw.get('sha256') != hashlib.sha256(code.encode()).hexdigest():
        raise BotError('script_digest_mismatch')
    if raw.get('visible_roots', []) != []:
        raise BotError('script_roots_unsupported')
    if raw.get('network_domains', []) != []:
        raise BotError('script_network_unsupported')
    limit = raw.get('input_bytes', 8192)
    if type(limit) is not int or not 1 <= limit <= 16384:
        raise BotError('invalid_script_limit')
    # Reuse the public bounded schema/identity/output/quota validators.
    common = {k: v for k, v in raw.items() if k not in keys}
    if set(common) & {'url', 'method', 'auth'}:
        raise BotError('invalid_script')
    d = http_definition({**common, 'url': 'https://script.invalid/', 'method': 'POST', 'level': 'write'}, ['script.invalid'])
    if raw.get('level', 'write') != 'write':
        raise BotError('script_requires_write_approval')
    for k in ('url', 'method', 'auth'):
        d.pop(k)
    result = {**d, 'kind': 'script', 'code': code, 'sha256': raw['sha256'],
            'visible_roots': [], 'network_domains': [], 'input_bytes': limit}
    if len(canonical(result).encode()) > 65536:
        raise BotError('invalid_script_limit')
    return result


# Hard limits are set inside the new namespace before owner code starts. The
# parent additionally bounds wall time and pipe reads, and kills its own group.
_LAUNCH = '''import resource,runpy
for r,v in ((resource.RLIMIT_AS,268435456),(resource.RLIMIT_FSIZE,1048576),(resource.RLIMIT_NOFILE,32),(resource.RLIMIT_NPROC,32),(resource.RLIMIT_CPU,30),(resource.RLIMIT_CORE,0)):
 resource.setrlimit(r,(v,v))
runpy.run_path('/code.py',run_name='__main__')
'''


def execute(d, args):
    from .tools import validate
    d = definition(d, [])
    validate(d['parameters'], args)
    data = canonical(args).encode()
    if len(data) > d['input_bytes']:
        raise BotError('script_input_too_large')
    if sys.platform != 'linux' or not Path('/usr/bin/bwrap').is_file():
        raise BotError('script_isolation_unavailable')
    # Explicit runtime mounts only. No /, /home, /etc, /proc, devices, host tmp,
    # sockets, config, credentials or caller-specified paths enter the namespace.
    # The sandbox uses the fixed system interpreter, which may differ from the
    # Host venv. Discover only that interpreter's public stdlib version.
    try:
        probe = subprocess.run(['/usr/bin/python3', '-I', '-S', '-c',
                                'import json,sys,sysconfig; print(json.dumps([str(sys.version_info.major)+"."+str(sys.version_info.minor),sysconfig.get_config_var("LIBDIR"),sysconfig.get_config_var("LDLIBRARY")]))'],
                               env={}, capture_output=True, timeout=2, check=True)
        version, libdir, libname = json.loads(probe.stdout)
        if not __import__('re').fullmatch(r'3\.[0-9]{1,2}', version):
            raise ValueError('runtime')
        # A dynamically linked system Python may use /usr/lib via its RPATH.
        # Bind only its selected public libpython, never a new library directory.
        library = None
        if libname and __import__('re').fullmatch(r'libpython3\.[0-9]{1,2}\.so(?:\.[0-9]+)*', libname):
            candidate = (Path(libdir) / libname).resolve()
            if candidate.parent not in (Path('/usr/lib'), Path('/usr/lib64')) or not candidate.is_file():
                raise ValueError('runtime')
            library = str(candidate)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BotError('script_isolation_unavailable') from None
    runtime = ['/usr/bin/python3', '/usr/lib/python'+version]
    if library:
        runtime.append(library)
    if not all(Path(p).exists() for p in runtime):
        raise BotError('script_isolation_unavailable')
    with tempfile.TemporaryDirectory(prefix='bot-script-') as tmp:
        code = Path(tmp)/'code.py'
        code.write_text(d['code'])
        cmd = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session',
               '--uid', '65534', '--gid', '65534', '--cap-drop', 'ALL', '--clearenv']
        for p in runtime + ['/lib', '/lib64']:
            if Path(p).exists():
                cmd += ['--ro-bind', str(Path(p).resolve()), p]
        cmd += ['--ro-bind', str(code), '/code.py', '--size', '16777216', '--tmpfs', '/scratch',
                '--remount-ro', '/', '--chdir', '/scratch', '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'LD_LIBRARY_PATH', '/lib:/lib64',
                '/usr/bin/python3', '-I', '-S', '-c', _LAUNCH]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env={}, start_new_session=True)
        except OSError:
            raise BotError('script_isolation_unavailable') from None
        out = bytearray()
        deadline = time.monotonic()+d['timeout']
        try:
            with selectors.DefaultSelector() as sel:
                os.set_blocking(proc.stdin.fileno(), False)
                os.set_blocking(proc.stdout.fileno(), False)
                sel.register(proc.stdin, selectors.EVENT_WRITE)
                sel.register(proc.stdout, selectors.EVENT_READ)
                pending = memoryview(data)
                while sel.get_map():
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise BotError('script_timeout_unknown')
                    for key, _ in sel.select(min(remaining, .1)):
                        if key.fileobj is proc.stdin:
                            try:
                                n = os.write(proc.stdin.fileno(), pending)
                                pending = pending[n:]
                            except BrokenPipeError:
                                pending = pending[:0]
                            if not pending:
                                sel.unregister(proc.stdin); proc.stdin.close()
                        else:
                            chunk = os.read(proc.stdout.fileno(), 4096)
                            if not chunk:
                                sel.unregister(proc.stdout)
                            elif len(out)+len(chunk) > 32768:
                                raise BotError('script_output_truncated')
                            else:
                                out.extend(chunk)
                try:
                    status = proc.wait(timeout=max(.001, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise BotError('script_timeout_unknown') from None
                if status:
                    raise BotError('script_failed_or_isolation_unavailable')
        finally:
            # Descendants may keep running after the leader exits. The sandbox's
            # PID namespace is also torn down when its init (bwrap) exits.
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
            if not proc.stdin.closed: proc.stdin.close()
            proc.stdout.close()
        try:
            value = json.loads(out)
        except (ValueError, UnicodeError):
            raise BotError('invalid_script_output') from None
        if not isinstance(value, dict):
            raise BotError('invalid_script_output')
        return value


class HostBroker:
    """No keys, URL, headers or transport override accepted from script/model.

    This host-only adapter uses normal tool identity, approval, quotas, secret
    references and DNS-pinned HTTPS. It is deliberately not mounted in scripts.
    """
    def __init__(self, registry, names):
        self.registry = registry
        self.names = frozenset(names)

    def execute(self, vid, rid, name, args, approval_id=None):
        if name not in self.names or self.registry.load(name).get('kind') == 'script':
            raise BotError('unknown_broker_tool')
        return self.registry.execute(vid, rid, name, args, approval_id)
