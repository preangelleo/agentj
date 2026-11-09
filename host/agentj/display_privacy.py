"""Phone reply/history display copies only; signed control and pickup frames stay exact.

Known values are salted window/prefix hashes, never plaintext caches. File I/O is
called in a worker thread. Revalidate all source bytes and the file inventory on
every display send; rebuild if changed. A failed scan masks display prose.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
from . import peer_guard, privacy, silent

DISPLAY_MESSAGES = silent.DISPLAY_MESSAGES | frozenset(('call_event', 'tts_script_res'))
# 0.30.0 (2026-11-09, chairman/Jarvis): outbound display masking is OFF — project() passes every
# frame through unchanged, the 0.28.6 display behaviour. The 09:17 install masked every bubble as
# <redacted> (incomplete scan → fail-closed). Code and tests stay; tests turn it on explicitly.
# 0.30.1 replaces this with "mask only real secrets + one scan reveals the whole message".
ENABLED = False
TEXT_FIELDS = frozenset(('text', 'pre', 'snip', 'summary', 'stdout', 'stderr', 'output', 'content', 'body', 'label', 'title', 'name'))

class DisplayIndex(peer_guard.SecretIndex):
    def __init__(self, *args, **kwargs):
        self.prefixes = set()
        self.short_values = set()
        self.added = 0
        super().__init__(*args, **kwargs)

    def add(self, value):
        if self.added >= peer_guard.MAX_VALUES:
            self.complete = False
            return
        self.added += 1
        super().add(value)
        if isinstance(value, str) and 0 < len(value) < 8:
            self.short_values.add(self._h(value))
        if isinstance(value, str) and len(value) >= 2:
            # A streamed suffix matching a known value's first bytes must not
            # leak before a later snapshot contains a complete indexed window.
            v = ''.join(value.split())
            self.prefixes.update((n, self._h(v[:n])) for n in range(1, min(8, len(v))))

    def display(self, text):
        text, _ = self.redact(text)
        text = re.sub(r"[^\s\"'`:：=,;{}()\[\]<>]+", lambda m: privacy.R if self._h(m[0]) in self.short_values else m[0], text)
        tail = re.search(r"[^\s\"'`:：=,;{}()\[\]<>]+$", text)
        if tail and (len(tail[0]), self._h(tail[0])) in self.prefixes:
            text = text[:tail.start()] + privacy.R
        return privacy.owner_text(text)


def sources(host):
    home = Path(os.path.expanduser('~'))
    cwd = Path((getattr(host, 'agent_cfg', None) or {}).get('dir') or home).resolve()
    dirs = [cwd]
    if home == cwd or home in cwd.parents:
        dirs += [p for p in cwd.parents if p == home or home in p.parents]
    envs = tuple(sorted({str(Path(e.path)) for d in dirs for e in os.scandir(d) if e.name.startswith('.env')}))
    auth = Path(os.environ.get('CODEX_HOME') or home/'.codex')/'auth.json'
    try:
        keys = tuple(sorted(e.path for e in os.scandir(home/'.ssh') if e.name.startswith('id_') and not e.name.endswith('.pub')))
    except FileNotFoundError:
        keys = ()
    return str(home), str(cwd), envs, str(auth), keys


def _file(idx, path):
    try:
        os.lstat(path)
    except FileNotFoundError:
        idx._source_proofs.append((path, None)); return None
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > peer_guard.MAX_FILE:
        raise OSError('local_secret_scan_incomplete')
    with open(path, encoding='utf-8', errors='replace') as fh:
        body = fh.read(peer_guard.MAX_FILE + 1)
    if len(body.encode('utf-8', 'replace')) > peer_guard.MAX_FILE:
        raise OSError('local_secret_scan_incomplete')
    idx._source_proofs.append((path, idx._h(body)))
    return body


def index(host):
    inventory = sources(host)
    old = host.__dict__.get('_display_secret_index')
    if old and old[0] == inventory:
        try:
            return old[1].require_complete()
        except OSError:
            pass
    home, cwd, envs, auth, keys = inventory
    idx = DisplayIndex.build(files=envs, workdir=cwd, home=home, fragment_size=8)
    body = _file(idx, auth)
    if body is not None:
        def tokens(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, str) and peer_guard.SECRET_NAME.search(key):idx.add(child)
                    elif isinstance(child, (dict, list)):tokens(child)
            elif isinstance(value, list):
                for child in value:tokens(child)
        tokens(json.loads(body))
    for path in keys:
        body = _file(idx, path)
        if body and peer_guard.PRIVATE_KEY.search(body):
            idx.add(body)
            for line in body.splitlines():
                if not line.startswith('-----'):idx.add(line)
    idx.require_complete()
    host.__dict__['_display_secret_index'] = (inventory, idx)
    return idx


def project(host, message):
    if not ENABLED:
        return message
    if not isinstance(message, dict) or message.get('t') not in DISPLAY_MESSAGES:
        return message
    try:
        current = index(host)
    except (OSError, ValueError):
        current = None
    def visit(value, text=False):
        if isinstance(value, str):
            return (current.display(value) if current is not None else privacy.R) if text and value else value
        if isinstance(value, list):return [visit(v, text) for v in value]
        if not isinstance(value, dict):return value
        return {k: v if k in (silent.OPAQUE_FIELDS - {'value'}) else visit(v, text or k in TEXT_FIELDS) for k,v in value.items()}
    return visit(message)


def text(host, value):
    return project(host, {"t": "msg", "text": value})["text"]
