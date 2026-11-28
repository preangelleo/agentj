"""Host-private transcription provenance, with no audio or transcript."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile

HISTORY_LIMIT = 128
_SUPPRESSED = ContextVar('asr_usage_suppressed', default=False)
_REMOTE_ENGINES = frozenset(('custom', 'openai', 'openrouter'))
_ENGINES = _REMOTE_ENGINES | {'sherpa', 'voxtype'}
_FAILURE_RESULTS = frozenset(('not_installed', 'off', 'bad_audio', 'timeout', 'busy', 'engine_failed', 'no_speech', 'cancelled'))
_RESULTS = _FAILURE_RESULTS | {'ok'}
_FAILURES = frozenset((
    'missing_key', 'authentication', 'quota', 'rate_limit', 'endpoint', 'http_client', 'http_server',
    'redirect', 'connection_refused', 'timeout', 'dns', 'tls', 'network', 'invalid_response',
    'empty_response', 'invalid_config', 'bad_audio', 'engine_failed', 'busy',
))
_KEYS = frozenset(('engine', 'model', 'at', 'fallback', 'elapsed_ms'))
_HISTORY_KEYS = frozenset(('engine', 'at', 'fallback', 'elapsed_ms', 'result',
                           'remote_failure', 'http_status', 'retry_failure', 'retried'))


def _directory(state_dir):
    if state_dir is None:
        from .state_paths import state_dir as default_state
        state_dir = default_state()
    return Path(state_dir) / 'asr'


def _model(value):
    # A model identifier, never an endpoint, credential, response or config dump.
    from .taskspec import secret_like
    return value if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', value)
                     and not secret_like(value)) else None


@contextmanager
def suppress_nested():
    """The outer voice router owns total elapsed time and the remote→local decision."""
    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def _choice(value, allowed, default=None):
    return value if isinstance(value, str) and value in allowed else default


def _status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def fields(result, *, elapsed_ms, fallback=None):
    """Closed diagnostic fields safe for host.log and the private history."""
    if fallback is None:
        fallback = result.get('fallback') is True or result.get('notice') == 'local_fallback'
    failure = _choice(result.get('remote_failure'), _FAILURES)
    remote = (result.get('remote') is True or result.get('remote_attempted') is True
              or _choice(result.get('engine'), _REMOTE_ENGINES))
    return {
        'engine': 'remote' if not fallback and remote else 'local',
        'fallback': bool(fallback),
        'elapsed_ms': max(0, int(elapsed_ms)),
        'result': 'ok' if result.get('ok') else _choice(result.get('reason'), _FAILURE_RESULTS, 'engine_failed'),
        'remote_failure': failure,
        'http_status': _status(result.get('http_status')),
        'retry_failure': _choice(result.get('retry_failure'), _FAILURES),
        'retried': result.get('retried') is True,
    }


def _read(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return b''
            data = stream.read(limit + 1)
        return data if len(data) <= limit else b''
    except OSError:
        return b''


def _replace(path, data):
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.stem}-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _history_rows(path):
    for line in _read(path, HISTORY_LIMIT * 512).splitlines()[-HISTORY_LIMIT:]:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if (isinstance(row, dict) and set(row) == _HISTORY_KEYS and _valid_common(row)
                and _choice(row['engine'], ('remote', 'local')) and _choice(row['result'], _RESULTS)
                and type(row['retried']) is bool and _status(row['http_status']) == row['http_status']
                and all(_choice(row[k], _FAILURES) == row[k] for k in ('remote_failure', 'retry_failure'))):
            yield row


def record(result, *, state_dir, model, elapsed_ms, fallback=None):
    """Keep the latest 128 route outcomes, with atomic replacement under a host/CLI lock.

    Keep the original latest-success projection and schema: failures do not move
    its timestamp. Disk errors cannot change the result of transcription.
    """
    if _SUPPRESSED.get():
        return
    from .state import State

    directory = _directory(state_dir)
    state = State(directory.parent)
    diagnostic = fields(result, elapsed_ms=elapsed_ms, fallback=fallback)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.log('asr_route', **diagnostic)
    except OSError:
        pass
    try:
        history = directory / 'history.jsonl'
        with state.file_lock('asr-usage'):
            at = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
            rows = list(_history_rows(history))
            rows.append({'at': at, **diagnostic})
            _replace(history, b''.join((json.dumps(row) + '\n').encode() for row in rows[-HISTORY_LIMIT:]))
            engine = _choice(result.get('engine'), _ENGINES)
            if result.get('ok') and engine:
                row = {'engine': engine, 'model': _model(model), 'at': at,
                       'fallback': diagnostic['fallback'], 'elapsed_ms': diagnostic['elapsed_ms']}
                _replace(directory / 'last_used.json', json.dumps(row, ensure_ascii=False).encode())
    except (OSError, ValueError, TypeError):
        pass


def _valid_common(row):
    return (type(row['fallback']) is bool and type(row['elapsed_ms']) is int and row['elapsed_ms'] >= 0
            and isinstance(row['at'], str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z', row['at']))


def last_used(state_dir=None):
    """Null for absent/unreadable/invalid metadata; bounded read, no symlink following."""
    try:
        row = json.loads(_read(_directory(state_dir) / 'last_used.json', 4096))
        if (isinstance(row, dict) and set(row) == _KEYS and _choice(row['engine'], _ENGINES)
                and _valid_common(row) and (row['model'] is None or _model(row['model']) == row['model'])):
            return row
    except (ValueError, RecursionError):
        pass
    return None
