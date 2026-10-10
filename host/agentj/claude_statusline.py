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


def local_settings_root(directory: Path) -> Path:
    """Native CLI >=2.1.211: local settings at owned git/main-checkout root.

    Shared project settings stay in the primary cwd. An older local file there is
    still read, below the root local file. Unowned/home-root repositories fall back.
    See https://code.claude.com/docs/en/settings#where-claude-code-keeps-the-local-file-in-a-git-repository
    """
    try:
        r = subprocess.run(["git", "-C", str(directory), "rev-parse", "--path-format=absolute",
                            "--show-toplevel", "--git-common-dir"], capture_output=True, text=True, timeout=2)
        if r.returncode:
            return directory
        lines = r.stdout.strip().splitlines()
        if len(lines) != 2:
            return directory
        checkout, common = map(Path, lines)
        root = common.parent if common.name == ".git" else checkout
        if root == Path.home().resolve() or os.name == "nt":
            return directory
        uid = os.getuid()
        for p in (checkout, checkout / ".git", root, common, root / ".claude"):
            if (p.exists() or p.is_symlink()) and p.lstat().st_uid != uid:
                return directory
        return root
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return directory


def layers(directory=None):
    """File precedence for a native CLI session; managed/CLI policy is separate."""
    from . import claude_inbound
    user = claude_inbound.path()
    if not directory:
        return [('user', user)]
    cwd = Path(directory).expanduser().resolve()
    root = local_settings_root(cwd)
    out = [('local', root / '.claude/settings.local.json')]
    if root != cwd:
        out.append(('local-legacy', cwd / '.claude/settings.local.json'))
    return [*out, ('project', cwd / '.claude/settings.json'), ('user', user)]


def effective(directory=None):
    """(layer, path, statusLine) that Claude Code actually uses for `directory` (P116, B5)."""
    from . import claude_inbound
    for name, p in layers(directory):
        doc, _ = claude_inbound._load(p)
        if 'statusLine' in doc:
            return name, p, doc['statusLine']
    return 'user', claude_inbound.path(), None


def set_enabled(enabled, state_root, directory=None):
    """Chain our tap at the layer that is really in effect (P116): a project's statusLine is chained from the private
    local layer (never by editing a shared project file); a local one (e.g. an older tap) is wrapped where it is."""
    from . import claude_inbound
    p = meta_path().resolve()
    meta0 = load_meta()
    if meta0.get('enabled'):
        target = Path(meta0['settings']) if meta0.get('settings') else claude_inbound.path()   # pre-P116 installs: user layer
    else:
        layer, _, _ = effective(directory)
        target = dict(layers(directory))['local' if layer in ('local', 'local-legacy', 'project') else 'user']
    ctx = {}
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
        if old is None:
            _, _, old = effective(directory)   # chain the lower layer's command that was in effect
        if old is not None and (not isinstance(old, dict) or old.get('type') != 'command' or not isinstance(old.get('command'), str)):
            raise claude_inbound.SettingsError('Unsupported owner status line; not changed')
        installed = dict(old or {})
        installed.update(type='command', command=' '.join(shlex.quote(str(x)) for x in
                         (sys.executable, Path(__file__).resolve(), 'tap', p)))
        ctx['meta'] = {'enabled': True, 'had_original': 'statusLine' in doc, 'original': old, 'installed': installed,
                       'state_root': str(Path(state_root).resolve()), 'settings': str(target),
                       'dir': str(Path(directory).expanduser().resolve()) if directory else None}
        doc['statusLine'] = installed
    changed = claude_inbound.update(change, target)
    if ctx.get('meta'):
        private_write(p, ctx['meta'])
    if not enabled:
        meta = load_meta()
        if meta.get('enabled'):
            private_write(p, {**meta, 'enabled': False})
    return changed


def active(meta=None):
    """Installed AND the layer Claude Code actually uses for the session's folder (not shadowed by a higher one)."""
    from . import claude_inbound
    meta = load_meta() if meta is None else meta
    if not meta.get('enabled'):
        return False
    if not meta.get('settings'):
        return claude_inbound.read().get('statusLine') == meta.get('installed')
    target = Path(meta['settings'])
    if claude_inbound._load(target)[0].get('statusLine') != meta.get('installed'):
        return False
    for name, lp in layers(meta.get('dir')):
        if lp == target:
            return True
        if 'statusLine' in claude_inbound._load(lp)[0]:
            return False        # a higher layer shadows our tap: no data would arrive
    return True


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
    usd = obj(blob, 'cost').get('total_cost_usd')
    return {'session_id': sid, 'model': text(model.get('id')), 'model_name': text(model.get('display_name')),
            'effort': text(effort.get('level')), 'ctx': context or None,
            'h5': quota('five_hour'), 'week': quota('seven_day'), 'cost_usd': round(usd, 4) if number(usd) else None}


def read(state_root, session_id, compacted_at=None):
    empty = dict.fromkeys(('model','model_name','effort','ctx','h5','week','source_at'))
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id): return empty
    try:
        from . import claude_inbound
        meta = load_meta()
        if not active(meta): return empty
        p = Path(state_root) / 'claude-statusline' / (session_id + '.json')
        if p.is_symlink() or p.stat().st_size > 16384: return empty
        data = json.loads(p.read_text()); at = p.stat().st_mtime
        if data.get('session_id') != session_id: return empty
        out = {k: data.get(k) for k in empty}
        out['source_at'] = at
        if compacted_at and at <= compacted_at: out['ctx'] = None
        return out
    except (OSError, ValueError, AttributeError, TypeError): return empty


def cost(state_root, session_id):
    """P116 (B5): Claude Code's own cost.total_cost_usd for this session from the same tapped status line, or None."""
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id): return None
    try:
        if not active(): return None
        p = Path(state_root) / 'claude-statusline' / (session_id + '.json')
        if p.is_symlink() or p.stat().st_size > 16384: return None
        data = json.loads(p.read_text())
        v = data.get('cost_usd') if data.get('session_id') == session_id else None
        return v if number(v) else None
    except (OSError, ValueError, AttributeError, TypeError): return None


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
        from .state import State
        st = State()
        cfg = st.agent_config() if st.exists() else None
        directory = (cfg or {}).get('dir')
        if args[0] != 'status': set_enabled(args[0] == 'on', st.root, directory)
        meta = load_meta()
        on = active(meta)
        layer = next((n for n, lp in layers(meta.get('dir')) if str(lp) == meta.get('settings')), 'user') if meta.get('enabled') else None
        print('Claude status-line tap: ' + ('on' if on else 'shadowed' if meta.get('enabled') else 'off') + (f' ({layer} layer)' if layer else ''))
        if meta.get('enabled') and not on:
            print('一个更高优先级的设置层有自己的状态栏，手机收不到读数；先 off 再 on 重新链接。 / A higher settings layer shadows the tap; run off then on to re-chain.')
        print('保留原状态栏输出；仅同会话的模型/额度/花费送到手机。 / Original output preserved; only matching-session model/usage/cost reaches the phone.')
        return 0
    except (OSError, ValueError):
        print('状态栏设置未能安全修改；检查 JSON、权限或主人后续编辑。 / Could not safely change status line; check JSON, permissions or later owner edits.'); return 1


if __name__ == '__main__':
    raise SystemExit(tap(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == 'tap' else 2)
