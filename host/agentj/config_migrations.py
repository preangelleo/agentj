"""Trusted package migrations; user config never supplies code or migration paths.
Each transformation must be idempotent, touch only necessary spans, and preserve
unknown old fields until its own transformation has converted them. Successful
markers follow validation and all writes. Run while serve is stopped.
"""
import fcntl
import json
import os
from . import preferences as p
from .state import State, _write_private

# Version 1 starts with sparse overrides. No older public JSON5 format existed.
def _sparse_v1(raw):
    p.parse(raw)
    return raw

def _tts_successor(raw):
    # Exact former factory value only. Snapshot/custom models and non-OpenAI choices survive.
    doc = p.parse(raw)
    if p.get(doc, 'voice.tts.provider', 'openai') == 'openai' and p.get(doc, 'voice.tts.model') == 'gpt-4o-mini-tts':
        return p.edit(raw, 'voice.tts.model', 'gpt-realtime-2.1-mini')
    return raw

MIGRATIONS = (("202610040001", _sparse_v1), ("202610050047", _tts_successor))

def run(st=None, *, pending=False):
    st = st or State()
    directory = st.root / 'config-migrations'
    remaining = [(name, fn) for name, fn in MIGRATIONS if not (directory / (name+'.json')).exists()]
    if pending:
        return {'ok': True, 'pending': [name for name, _ in remaining], 'version': 1}
    st.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(st.root/'preferences.lock', os.O_RDWR|os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Another process may have completed the migration while we waited.
        remaining = [(name, fn) for name, fn in MIGRATIONS if not (directory / (name+'.json')).exists()]
        if not remaining:
            return {'ok': True, 'pending': [], 'applied': [], 'version': 1}
        from .names import ctl_call, ServeBusy
        try:
            live = ctl_call(st, {'cmd':'config_revision'}, 5) if st.exists() else None
        except ServeBusy:
            raise p.ConfigError('/', 'stop serve before configuration migration; host did not acknowledge', code=3) from None
        if live is not None:
            raise p.ConfigError('/', 'stop serve before configuration migration, then restart and doctor', code=3)
        p.ensure()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory,0o700)
        applied = []
        for name, transform in remaining:
            old = p.path().read_bytes()
            good = p.runtime(st)
            oldgood = good.read_bytes() if good.exists() else None
            marker = directory/(name+'.json')
            try:
                new = transform(old.decode())
                candidate = p.validate(p.parse(new))
                if new.encode() != old:
                    # History and last-good are durable before the override changes.
                    # Runtime capabilities are checked by the next serve startup.
                    p.commit_files(st, new, candidate)
                _write_private(marker, json.dumps({'id':name,'version':1}).encode())
            except Exception:
                # No live host exists, so restoring disk also restores effective state.
                _write_private(p.path(), old)
                if oldgood is None:
                    good.unlink(missing_ok=True)
                else:
                    _write_private(good, oldgood)
                marker.unlink(missing_ok=True)
                raise
            applied.append(name)
        return {'ok': True, 'pending': [], 'applied': applied, 'version': 1}
    finally:
        os.close(fd)
