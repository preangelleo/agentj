"""Host-private provenance of the last successful transcription; never audio or text."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile

_SUPPRESSED = ContextVar('asr_usage_suppressed', default=False)
_ENGINES = frozenset(('custom', 'openai', 'openrouter', 'sherpa', 'voxtype'))
_KEYS = frozenset(('engine', 'model', 'at', 'fallback', 'elapsed_ms'))


def _path(state_dir):
    if state_dir is None:
        from .state import state_dir as default_state
        state_dir = default_state()
    return Path(state_dir) / 'asr' / 'last_used.json'


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


def record(result, *, state_dir, model, elapsed_ms, fallback):
    """Atomic single-record replacement; concurrent threads/processes never share a temp file.

    Failed takes retain the previous success and its timestamp. A write failure must
    never turn successful speech into an error or expose filesystem exception text.
    """
    if _SUPPRESSED.get() or not result.get('ok') or result.get('engine') not in _ENGINES:
        return
    tmp = None
    try:
        path = _path(state_dir)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        row = {'engine': result['engine'], 'model': _model(model),
               'at': datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
               'fallback': bool(fallback), 'elapsed_ms': max(0, int(elapsed_ms))}
        fd, tmp = tempfile.mkstemp(prefix='.last_used-', dir=path.parent)
        with os.fdopen(fd, 'w') as stream:
            json.dump(row, stream, ensure_ascii=False)
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def last_used(state_dir=None):
    """Null for absent/unreadable/invalid metadata; bounded read, no symlink following."""
    try:
        fd = os.open(_path(state_dir), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            row = json.loads(stream.read(4097))
        if (not isinstance(row, dict) or set(row) != _KEYS or row['engine'] not in _ENGINES
                or type(row['fallback']) is not bool or type(row['elapsed_ms']) is not int
                or row['elapsed_ms'] < 0 or not isinstance(row['at'], str)
                or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z', row['at'])
                or row['model'] is not None and _model(row['model']) != row['model']):
            return None
        return row
    except (OSError, ValueError, TypeError):
        return None
