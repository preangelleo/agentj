"""Semantic Olares probes run wholly inside the existing Agent fence.

No native candidate, version manager, plugin or free-model CLI is executed in a
host worker. The fixed image child verifies actual mounts, not an env assertion.
"""
from __future__ import annotations
import contextlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

MAX_OUTPUT = 64 * 1024
_CHILD = None
_VERSION = re.compile(r'(?<![\w.])v?(\d+\.\d+(?:\.\d+)?(?:(?:a|b|rc)\d+)?)(?![\w.])')


def inside():
    """Only our child entry plus real hidden-state/tmp/proc mounts permits recursion."""
    if _CHILD is None:
        return False
    return sealed()


def sealed():
    from .state import default_state
    try:
        mounts = {}
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            head, _, tail = line.partition(' - ')
            fields = head.split()
            if len(fields) > 4:
                mounts[fields[4].replace('\\040', ' ')] = tail.split()[0]
        root = str(default_state().resolve())
        return (mounts.get(root) == 'tmpfs' and mounts.get(root + '/agentperm') == 'tmpfs'
                and mounts.get('/tmp') == 'tmpfs' and mounts.get('/proc') == 'proc'
                and {p.name for p in Path(root).iterdir()} == {'agentperm'}
                and not any(Path(root + '/agentperm').iterdir()) and Path('/proc/1/ns/pid').stat().st_ino == Path('/proc/self/ns/pid').stat().st_ino)
    except (OSError, ValueError, IndexError):
        return False


def required():
    from . import olares_supervisor as sup
    return (sup.enabled() or any(Path(p).exists() for p in (sup.ENTRY, sup.ENV_PARSER, sup.IMAGE_PYTHON))) and not inside()


BASE_ENV = frozenset({'HOME', 'PATH', 'LANG', 'LANGUAGE', 'TZ', 'TERM', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                      'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME',
                      'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'ALL_PROXY',
                      'https_proxy', 'http_proxy', 'no_proxy', 'all_proxy'})
OWNER_SERVER_ENV = frozenset({'OPENCODE_SERVER_PASSWORD', 'OPENCODE_SERVER_USERNAME'})
CONFIG_ENV = {'claude': {'CLAUDE_CONFIG_DIR'}, 'codex': {'CODEX_HOME'},
              'opencode': {'CLAUDE_CONFIG_DIR'}, 'kimi': {'KIMI_CODE_HOME'},
              'qwen': {'QWEN_HOME'}, 'dsh': {'DSH_HOME'}}


def login_names(kind):
    from .harness import OPENCODE_KEY_ENV
    from .peer_session import LOGIN_ENV
    return set(OPENCODE_KEY_ENV) if kind == 'opencode' else set(LOGIN_ENV.get(kind, ()))


def environment(kind, base=None, *, metadata=False):
    from . import fence
    from .harness import BIN_ENV
    original = dict(os.environ if base is None else base)
    allowed = set(BASE_ENV) | CONFIG_ENV.get(kind, set()) | {BIN_ENV.get(kind)}
    if not metadata:
        allowed |= login_names(kind)
    hidden = set(fence.hide_env(original))
    # Values only come from this service's existing session environment. No
    # credential-file refresh, dynamic key names or caller-supplied env RPC.
    return {k: v for k, v in original.items() if k not in hidden and
            (k in allowed or re.fullmatch(r'LC_[A-Z_]+', k))}


@contextlib.contextmanager
def metadata_environment(kind):
    original = dict(os.environ)
    try:
        os.environ.clear(); os.environ.update(environment(kind, original, metadata=True))
        yield
    finally:
        os.environ.clear(); os.environ.update(original)


def failure(kind, op):
    if op == 'start':
        return {'secs': 0, 'reason': 'start_failed'}
    return {'harnesses': [{'name': kind, 'supported': True, 'installed': False, 'logged_in': None,
                           'callable': False, 'probe_reason': 'fence_unavailable'}], 'usable': [], 'decision': 'none'}


def spec(req):
    from . import harness
    from .slash import MODEL_RE
    if not isinstance(req, dict) or set(req) - {'op', 'kind', 'model', 'timeout', 'session_mode', 'shared_opencode_port', 'shared_session_id'}:
        raise ValueError('bad_field')
    op, kind, model, timeout = (req.get(k) for k in ('op', 'kind', 'model', 'timeout'))
    if op not in ('detect', 'probe', 'start') or kind not in harness.NAMES or (op == 'start' and kind not in ('codex', 'opencode')):
        raise ValueError('bad_kind')
    if model is not None and (not isinstance(model, str) or len(model) > 160 or not
            (re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/-]+', model) if kind == 'opencode' else MODEL_RE.fullmatch(model))):
        raise ValueError('bad_model')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 150:
        raise ValueError('bad_timeout')
    if req.get('session_mode', 'independent') not in ('independent', 'shared'):
        raise ValueError('bad_mode')
    port, sid = req.get('shared_opencode_port'), req.get('shared_session_id')
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('bad_port')
    if sid is not None and (not isinstance(sid, str) or not re.fullmatch(r'ses[A-Za-z0-9_-]{1,120}', sid)):
        raise ValueError('bad_session')
    return req


def row(value, kind):
    """Accept only bounded metadata from our fixed child; never native free text."""
    if not isinstance(value, dict) or value.get('name') != kind:
        raise ValueError('bad_result')
    out = {'name': kind}
    for key in ('installed', 'supported', 'callable'):
        if key in value:
            if type(value[key]) is not bool:
                raise ValueError('bad_result')
            out[key] = value[key]
    if value.get('logged_in') not in (True, False, None):
        raise ValueError('bad_result')
    out['logged_in'] = value.get('logged_in')
    for key in ('version',):
        if isinstance(value.get(key), str):
            match = _VERSION.search(value[key][:200])
            out[key] = match[1] if match else None
        else:
            out[key] = None
    code = value.get('probe_reason')
    if isinstance(code, str) and re.fullmatch('[a-z_]{1,40}', code):
        out['probe_reason'] = code
    if value.get('login') == 'free':
        out['login'] = 'free'
    for key in ('free_model',):
        v = value.get(key)
        if isinstance(v, str) and re.fullmatch(r'opencode/[A-Za-z0-9._-]{1,80}', v):
            out[key] = v
    if isinstance(value.get('free_models'), list):
        out['free_models'] = [v for v in value['free_models'][:128] if isinstance(v, str) and
                              re.fullmatch(r'opencode/[A-Za-z0-9._-]{1,80}', v)]
    def path(v):
        return isinstance(v, str) and len(v) <= 1024 and v.startswith('/') and not any(ord(c) < 32 for c in v)
    if path(value.get('executable')):
        out['executable'] = value['executable']
    r = value.get('resolution')
    if isinstance(r, dict):
        out['resolution'] = {k: v for k, v in r.items() if (k in ('requested', 'path', 'pinned') and path(v)) or
                             (k == 'reason' and isinstance(v, str) and re.fullmatch('[a-z_]{1,40}', v)) or
                             (k == 'wrapper' and type(v) is bool)}
    installs = value.get('installations')
    if isinstance(installs, list):
        out['installations'] = []
        for i in installs[:128]:
            if not isinstance(i, dict) or not path(i.get('path')):
                continue
            m = _VERSION.search(str(i.get('version') or '')[:200])
            out['installations'].append({'path': i['path'], 'version': m[1] if m else None,
                                         'wrapper': i.get('wrapper') is True, 'selected': i.get('selected') is True})
    return out


def result(value, kind, op):
    if op == 'start':
        if value is None:
            return {'secs': 0, 'reason': 'start_failed'}  # unavailable isolation is never a successful measurement
        if not isinstance(value, dict) or set(value) - {'secs', 'reason', 'err'} or type(value.get('secs')) not in (int, float) or \
                not math.isfinite(value['secs']) or not 0 <= value['secs'] <= 600:
            raise ValueError('bad_result')
        return {k: v for k, v in value.items() if k == 'secs' or isinstance(v, str) and re.fullmatch('[a-z_]{1,40}', v)}
    if not isinstance(value, dict) or not isinstance(value.get('harnesses'), list) or len(value['harnesses']) != 1:
        raise ValueError('bad_result')
    r = row(value['harnesses'][0], kind)
    usable = bool(r.get('callable')) if op == 'probe' else bool(r.get('installed') and r.get('supported') and r.get('logged_in') is not False)
    return {'harnesses': [r], 'usable': [kind] if usable else [], 'decision': 'use:' + kind if usable else 'none'}


def child_argv():
    from .olares_supervisor import IMAGE_PYTHON
    return [IMAGE_PYTHON, '-I', '-c', 'from agentj.olares_probe import main; main()']


def run(op, kind, model=None, timeout=60, *, cfg=None, environ=None):
    from . import fence, olares_supervisor as sup
    from .state import State, default_state
    req = {'op': op, 'kind': kind, 'model': model, 'timeout': timeout}
    cfg = cfg or {}
    if op == 'start':
        for key in (('session_mode', 'shared_opencode_port', 'shared_session_id') if kind == 'opencode' else ('session_mode',)):
            value = cfg.get(key)
            if value is None or (key == 'shared_opencode_port' and type(value) is int and value == 0) or \
                    (key == 'shared_session_id' and type(value) is str and value == ''):
                continue  # existing own-server / no-pin defaults, not malformed fields
            req[key] = value
    try:
        spec(req)
        if not sup.enabled() or not all(sup.trusted(p) for p in (__file__, '/usr/bin/bwrap')):
            return failure(kind, op)
        st = State()
        # Olares has exactly the chart's default state; no caller-supplied state root.
        if st.root.resolve() != default_state().resolve():
            return failure(kind, op)
        current = st.agent_config() or {}
        work = Path(current.get('dir') or Path.home() / 'coding').resolve()
        if not work.is_dir() or work == st.root.resolve() or st.root.resolve() in work.parents:
            return failure(kind, op)
        if op == 'start' and kind == 'opencode':
            from . import olares_runtime
            if not olares_runtime.owner_server(req) and not olares_runtime.prepare(st, cfg):
                return failure(kind, op)
        st.perm_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        original = dict(os.environ if environ is None else environ)
        env = environment(kind, original, metadata=op == 'detect')
        if (op == 'start' and kind == 'opencode' and req.get('session_mode') == 'shared' and
                type(req.get('shared_opencode_port')) is int and req['shared_opencode_port'] > 0):
            env.update({k: original[k] for k in OWNER_SERVER_ENV if k in original})
        cmd = fence.bwrap_argv(st, str(work), environ=original)
        cmd[0] = '/usr/bin/bwrap'  # never execute PATH-selected helpers before confinement
        # Probes get no control-plane sockets, including the usual Agent-facing ones.
        i = next(i for i in range(len(cmd)-2) if cmd[i:i+3] == ['--bind', str(st.perm_dir.resolve()), str(st.perm_dir.resolve())])
        cmd[i:i+3] = ['--tmpfs', str(st.perm_dir.resolve())]
        p = subprocess.Popen(cmd + child_argv(), cwd=str(work), env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            p.stdin.write(json.dumps(req).encode() + b'\n'); p.stdin.close()
            data = bytearray()
            end = time.monotonic() + (180 if op == 'detect' else timeout + 180)
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ)
                while True:
                    if time.monotonic() >= end:
                        raise ValueError('probe_timeout')
                    if not selector.select(min(1, max(.01, end-time.monotonic()))):
                        continue
                    part = os.read(p.stdout.fileno(), 8192)
                    if not part:
                        break
                    data.extend(part)
                    if len(data) > MAX_OUTPUT:
                        raise ValueError('probe_output_limit')
            if p.wait(timeout=3) != 0:
                raise ValueError('probe_failed')
            value = result(json.loads(data), kind, op)
            if op == 'detect' and any(original.get(k) for k in login_names(kind)):
                # Presence hint only: version candidates never receive the value.
                value['harnesses'][0]['logged_in'] = True
                value = result(value, kind, op)
            return value
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            p.stdout.close()
    except (OSError, ValueError, TypeError, IndexError, StopIteration, subprocess.TimeoutExpired):
        return failure(kind, op)


def main():
    global _CHILD
    from . import harness, fence
    fence.no_dump()
    try:
        req = spec(json.loads(sys.stdin.buffer.readline(4097)))
        if not sealed():
            raise ValueError('fence_unavailable')
        _CHILD = req['kind']
        if req['op'] == 'detect':
            with metadata_environment(_CHILD):
                r = {'harnesses': [harness._one(_CHILD)]}
        elif req['op'] == 'probe':
            r = harness.probe(only=(_CHILD,), model=req['model'], timeout=req['timeout'])
        else:
            from .state import State
            cfg = {k: req[k] for k in ('session_mode', 'shared_opencode_port', 'shared_session_id') if k in req}
            cfg.update(dir=os.getcwd(), kind=_CHILD, model=req['model'], fence=False)
            if _CHILD == 'opencode':
                from .opencode_start import start_probe
            else:
                from .agent_codex import start_probe
            r = start_probe(State(), cfg, req['timeout'])
        data = json.dumps(result(r, _CHILD, req['op'])).encode()
        if len(data) > MAX_OUTPUT:
            raise ValueError('probe_output_limit')
        sys.stdout.buffer.write(data)
    except Exception:
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
