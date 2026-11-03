"""Fixed offline report operation on the existing same-user host socket."""
from __future__ import annotations
import base64
import json
import pathlib
import os
import stat
import subprocess
import tempfile
import threading

MAX_WIRE = 1500000
MAX_FILES, MAX_DEPTH = 128, 8
MAX_FILE, MAX_BYTES = 512 * 1024, 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
_JOB = threading.Lock()


class LocalFailure(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def failure(code):
    return {'ok': False, 'code': code, 'receipt': 'report-local/' + code,
            'approval_card_shown': False, 'recovery': 'Return this receipt to the workflow owner; repair host helpers/service and resume original inputs. Do not request another mail grant.'}


def _parts(name):
    if not isinstance(name, str) or len(name) > 1024:
        raise LocalFailure('path')
    parts = name.split('/')
    if len(parts) < 2 or parts[0] not in ('input-data', 'input-template') or any(not p or p.startswith('.') or '\\' in p or '\0' in p for p in parts):
        raise LocalFailure('path')
    if len(parts) > MAX_DEPTH:
        raise LocalFailure('input_limits')
    return parts


def _files(files):
    if len(files) > MAX_FILES:
        raise LocalFailure('input_limits')
    result, total = {}, 0
    for name, value in files.items():
        _parts(name)
        if not isinstance(value, str):
            raise LocalFailure('shape')
        if len(value) > (MAX_FILE + 2) // 3 * 4:
            raise LocalFailure('input_limits')
        try:
            data = base64.b64decode(value, validate=True)
        except ValueError:
            raise LocalFailure('shape') from None
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_BYTES:
            raise LocalFailure('input_limits')
        result[name] = data
    return result


def request(engine, original, args, root, *, capture=False):
    from . import elevate, names, report_local
    from .state import State
    try:
        if args[0] in ('validate', 'render'):
            report_local.safe_path(original[1])
        final, dest = list(args), None
        if args[0] == 'render':
            oi = final.index('--out') + 1
            dest = original[oi]
            report_local.safe_path(dest)
            final[oi] = '/output/email.html'
        files, total = {}, 0
        for folder in ('input-data', 'input-template'):
            for path in (root / folder).rglob('*'):
                if path.is_symlink():
                    raise LocalFailure('path')
                if path.is_file():
                    name = path.relative_to(root).as_posix()
                    _parts(name)
                    if len(files) >= MAX_FILES or path.stat().st_size > MAX_FILE:
                        raise LocalFailure('input_limits')
                    data = path.read_bytes()
                    total += len(data)
                    if len(data) > MAX_FILE or total > MAX_BYTES:
                        raise LocalFailure('input_limits')
                    files[name] = base64.b64encode(data).decode()
        final = [v.replace(str(root) + '/', '/input/', 1) if v.startswith(str(root) + '/') else v for v in final]
        req = {'t': 'report_local', 'args': final, 'files': files}
        if len(json.dumps(req).encode()) > MAX_WIRE:
            raise LocalFailure('input_limits')
        st = State()
        res = elevate.client_request(st, req, timeout=45, path=names.elevate_sock(st))
        if not isinstance(res, dict) or 'code' not in res:
            res = failure('host_unavailable')
        if res.get('ok') and dest is not None and res.get('returncode') == 0:
            data = base64.b64decode(res['email'], validate=True)
            if len(data) > MAX_OUTPUT:
                raise LocalFailure('output_limits')
            report_local.check_email(data, allow_https_images=not any(k.startswith('input-template/') for k in files))
            target = report_local.export_email(dest, data)
            res['stdout'] = f'Rendered local email → {target}\n'
    except LocalFailure as exc:
        res = failure(exc.code)
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        res = failure('path_or_output_refused')
    result = subprocess.CompletedProcess(original, res.get('returncode', 0) if res.get('ok') else 4,
                                         res.get('stdout', ''), res.get('stderr', '') if res.get('ok') else json.dumps(res))
    if not capture:
        import sys
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
    return result


def bounded_run(argv, env):
    # Fixed system helper, never preexec_fn in the multi-threaded host. Limits are
    # inherited by bwrap/Python; stdout/stderr go to bounded files, not growing pipes.
    limits = ['/usr/bin/prlimit', '--as=536870912', '--cpu=15',
              '--nofile=64', '--fsize=8388608', '--core=0', '--', *argv]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(limits, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=30)
        if out.tell() >= MAX_OUTPUT or err.tell() >= MAX_OUTPUT or result.returncode < 0 or result.returncode in (137, 152, 153):
            raise LocalFailure('resource_limit')
        out.seek(0); err.seek(0)
        return subprocess.CompletedProcess(argv, result.returncode, out.read(MAX_OUTPUT).decode('utf-8'), err.read(MAX_OUTPUT).decode('utf-8'))


def execute(req):
    if not _JOB.acquire(blocking=False):
        return failure('host_busy')
    try:
        return _execute(req)
    except LocalFailure as exc:
        return failure(exc.code)
    except subprocess.TimeoutExpired:
        return failure('sandbox_timeout')
    except (ValueError, OSError, TypeError, IndexError):
        return failure('input_or_sandbox_refused')
    finally:
        _JOB.release()


def _execute(req):
    from . import report, report_local
    if not isinstance(req, dict) or set(req) != {'t', 'args', 'files'} or req['t'] != 'report_local' or not isinstance(req['args'], list) or not req['args'] or len(req['args']) > 16 or not all(isinstance(v, str) and len(v) <= 4096 for v in req['args']) or not isinstance(req['files'], dict):
        return failure('shape')
    args = req['args']
    if args[0] not in ('templates', 'schema', 'example', 'validate', 'render'):
        return failure('shape')
    try:
        report_local.validate_args(args)
    except ValueError:
        return failure('shape')
    if args[0] == 'render' and ('--email' not in args or args[args.index('--out') + 1] != '/output/email.html'):
        return failure('shape')
    if len(json.dumps(req).encode()) > MAX_WIRE:
        return failure('input_limits')
    files = _files(req['files'])
    for value in args:
        if value.startswith('/input/'):
            _parts(value[len('/input/'):])
    if args[0] in ('validate', 'render') and not args[1].startswith('/input/input-data/'):
        return failure('path')
    ti = args.index('--template') + 1 if '--template' in args else (1 if args[0] in ('schema', 'example') else None)
    if ti is not None:
        value = args[ti]
        if '/' in value and not value.startswith('/input/input-template/'):
            return failure('path')
        if not value.startswith('/input/input-template/') and (value.startswith(('.', '~')) or '\\' in value):
            return failure('path')
    with tempfile.TemporaryDirectory(prefix='agentj-report-host-') as tmp:
        root = pathlib.Path(tmp)
        for name, data in files.items():
            target = root.joinpath(*_parts(name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        source = report.find_engine()
        if source is None:
            return failure('engine_missing')
        # Bounded reads with no-follow FDs; even a mutable installed package
        # cannot inflate the private copy after a preflight size check.
        engine = root / 'engine'
        engine.mkdir()
        count = size = 0
        for path in source.rglob('*'):
            if '__pycache__' in path.parts:
                continue
            count += 1
            relative = path.relative_to(source)
            if count > 1000 or len(relative.parts) > 12 or path.is_symlink():
                return failure('integrity')
            target = engine / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return failure('integrity')
                data = stream.read(MAX_OUTPUT - size + 1)
            size += len(data)
            if size > MAX_OUTPUT:
                return failure('integrity')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if report_local.digest(engine) != report_local.TRUSTED_SHA256:
            return failure('integrity')
        for name in ('bwrap', 'python3', 'prlimit'):
            path = (pathlib.Path('/usr/bin') / name).resolve()
            if pathlib.Path('/usr') not in path.parents or not path.is_file():
                return failure('helper_missing')
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                return failure('helper_integrity')
        args = [str(root / v[len('/input/'):]) if v.startswith('/input/') else v for v in args]
        if args[0] == 'render':
            args[args.index('--out') + 1] = str(root / 'email.html')
        result = report_local.run(engine, args, args, root, capture=True, _host=True)
        if result is None:
            return failure('integrity')
        if result.returncode and ('bwrap:' in result.stderr or 'prlimit:' in result.stderr):
            return failure('sandbox_unavailable')
        res = {'ok': True, 'code': 'executed', 'receipt': 'report-local/executed', 'returncode': result.returncode,
               'stdout': result.stdout, 'stderr': result.stderr, 'approval_card_shown': False}
        if args[0] == 'render' and result.returncode == 0:
            res['email'] = base64.b64encode((root / 'email.html').read_bytes()).decode()
        from .elevate import MAX_FRAME
        if len(json.dumps(res, ensure_ascii=False).encode()) + 1 > MAX_FRAME:
            return failure('output_limits')
        return res
