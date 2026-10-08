"""Claude's own status-line measurements; owner-controlled chaining, session-bound reads.

Only whitelisted meter fields are stored (0600); the original stdin/output and exit
status belong to the owner's original command. No raw status-line JSON is logged.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def private_write(p, doc):
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix='.agentj-statusline-', dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(doc, f, ensure_ascii=False, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def meta_path():
    from . import claude_inbound
    return claude_inbound.path().parent / 'agentj-statusline.json'


def load_meta():
    p = meta_path()
    if p.is_symlink(): raise ValueError('symlink metadata')
    try: doc = json.loads(p.read_text())
    except FileNotFoundError: return {}
    if not isinstance(doc, dict): raise ValueError("invalid status-line metadata")
    return doc


def set_enabled(enabled, state_root):
    from . import claude_inbound
    p = meta_path().resolve()
    def change(doc):
        meta = load_meta()
        if meta.get('enabled'):
            if doc.get('statusLine') != meta.get('installed'):
                raise claude_inbound.SettingsError('Status line changed; review before restoring')
            if enabled: return
            if meta['had_original']: doc['statusLine'] = meta['original']
            else: doc.pop('statusLine', None)
            return
        if not enabled: return
        old = doc.get('statusLine')
        if old is not None and (not isinstance(old, dict) or old.get('type') != 'command' or not isinstance(old.get('command'), str)):
            raise claude_inbound.SettingsError('Unsupported owner status line; not changed')
        installed = dict(old or {})
        installed.update(type='command', command=' '.join(shlex.quote(str(x)) for x in
                         (sys.executable, Path(__file__).resolve(), 'tap', p)))
        private_write(p, {'enabled': True, 'had_original': 'statusLine' in doc, 'original': old,
                          'installed': installed, 'state_root': str(Path(state_root).resolve())})
        doc['statusLine'] = installed
    changed = claude_inbound.update(change)
    if not enabled:
        meta = load_meta()
        if meta.get('enabled'):
            private_write(p, {**meta, 'enabled': False})
    return changed


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


def pct(v):
    return min(100, round(v, 1)) if number(v) else None


def obj(d, k):
    v = d.get(k)
    return v if isinstance(v, dict) else {}


def measured(blob):
    sid = blob.get('session_id')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', sid): return None
    model, effort = obj(blob, 'model'), obj(blob, 'effort')
    ctx, limits = obj(blob, 'context_window'), obj(blob, 'rate_limits')
    p = pct(ctx.get('used_percentage'))
    usage = obj(ctx, 'current_usage')
    vals = [usage.get(k) for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    context = {'pct': p} if p is not None else {}
    if all(number(v) for v in vals): context['used'] = sum(vals)
    if number(ctx.get('context_window_size')) and ctx['context_window_size'] > 0: context['max'] = ctx['context_window_size']
    def quota(key):
        q = obj(limits, key); p = pct(q.get('used_percentage'))
        if p is None: return None
        return {'pct': p, 'reset': int(q['resets_at']) if number(q.get('resets_at')) else None}
    def text(v):
        return ''.join(c for c in v if c.isprintable())[:100] if isinstance(v, str) else None
    return {'session_id': sid, 'model': text(model.get('id')), 'model_name': text(model.get('display_name')),
            'effort': text(effort.get('level')), 'ctx': context or None,
            'h5': quota('five_hour'), 'week': quota('seven_day')}


def read(state_root, session_id, compacted_at=None):
    empty = dict.fromkeys(('model','model_name','effort','ctx','h5','week','source_at'))
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id): return empty
    try:
        from . import claude_inbound
        meta = load_meta()
        if not meta.get('enabled') or claude_inbound.read().get('statusLine') != meta.get('installed'): return empty
        p = Path(state_root) / 'claude-statusline' / (session_id + '.json')
        if p.is_symlink() or p.stat().st_size > 16384: return empty
        data = json.loads(p.read_text()); at = p.stat().st_mtime
        if data.get('session_id') != session_id: return empty
        out = {k: data.get(k) for k in empty}
        out['source_at'] = at
        if compacted_at and at <= compacted_at: out['ctx'] = None
        return out
    except (OSError, ValueError, AttributeError, TypeError): return empty


def tap(meta_file):
    raw = sys.stdin.buffer.read()
    meta = {}
    try:
        meta = json.loads(Path(meta_file).read_text())
        if len(raw) <= 1048576:
            data = measured(json.loads(raw))
            if data and meta.get('enabled'):
                private_write(Path(meta['state_root']) / 'claude-statusline' / (data['session_id'] + '.json'), data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError): pass
    original = meta.get('original')
    if isinstance(original, dict) and isinstance(original.get('command'), str):
        try: return subprocess.run(original['command'], shell=True, input=raw).returncode
        except OSError: return 1
    return 0


def command(args):
    if len(args) != 1 or args[0] not in ('on','off','status'):
        print('Usage: agentj config claude-statusline on|off|status'); return 2
    try:
        from . import claude_inbound
        from .state import State
        if args[0] != 'status': set_enabled(args[0] == 'on', State().root)
        meta = load_meta()
        on = meta.get('enabled') and claude_inbound.read().get('statusLine') == meta.get('installed')
        print('Claude status-line tap: ' + ('on' if on else 'off'))
        print('保留原状态栏输出；仅同会话的模型/额度送到手机。 / Original output preserved; only matching-session model/usage reaches the phone.')
        return 0
    except (OSError, ValueError):
        print('状态栏设置未能安全修改；检查 JSON、权限或主人后续编辑。 / Could not safely change status line; check JSON, permissions or later owner edits.'); return 1


if __name__ == '__main__':
    raise SystemExit(tap(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == 'tap' else 2)
