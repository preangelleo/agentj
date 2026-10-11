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


def managed():
    """(path, statusLine) of an organization policy that sets the status line (it wins over every layer), else None.
    Agent J only reads managed policy."""
    from . import claude_inbound
    for mp in claude_inbound.MANAGED:
        try:
            doc, _ = claude_inbound._load(Path(mp))
        except (OSError, ValueError):
            continue
        if 'statusLine' in doc:
            return Path(mp), doc['statusLine']
    return None


def effective(directory=None):
    """(layer, path, statusLine) that Claude Code actually uses for `directory` (P116, B5); managed policy first (P127)."""
    from . import claude_inbound
    m = managed()
    if m:
        return 'managed', m[0], m[1]
    for name, p in layers(directory):
        doc, _ = claude_inbound._load(p)
        if 'statusLine' in doc:
            return name, p, doc['statusLine']
    return 'user', claude_inbound.path(), None


def set_enabled(enabled, state_root, directory=None, explicit=None):
    """Chain our tap at the layer that is really in effect (P116): a project's statusLine is chained from the private
    local layer (never by editing a shared project file); a local one (e.g. an older tap) is wrapped where it is.
    P127: `on` while installed but shadowed for `directory` (another layer won, or the folder changed) re-chains at the
    layer now in effect; managed policy is never changed; `explicit` ('on'/'off') records the owner's own choice."""
    from . import claude_inbound
    p = meta_path().resolve()
    meta0 = load_meta()
    if enabled and meta0.get('enabled') and not active(meta0, directory):
        try:
            set_enabled(False, state_root, None)
        except claude_inbound.SettingsError:
            private_write(p, {**meta0, 'enabled': False})   # our line was replaced since: nothing of ours to restore
        meta0 = load_meta()
    if meta0.get('enabled'):
        target = Path(meta0['settings']) if meta0.get('settings') else claude_inbound.path()   # pre-P116 installs: user layer
    else:
        layer, _, _ = effective(directory)
        if layer == 'managed' and enabled:
            raise claude_inbound.SettingsError('Managed policy sets the status line; not changed')
        target = dict(layers(directory))['local' if layer in ('local', 'local-legacy', 'project') else 'user']
    created = not target.exists()
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
                       'dir': str(Path(directory).expanduser().resolve()) if directory else None,
                       'created_file': created, 'explicit': explicit or meta.get('explicit')}
        doc['statusLine'] = installed
    changed = claude_inbound.update(change, target)
    if ctx.get('meta'):
        private_write(p, ctx['meta'])
    if not enabled:
        meta = load_meta()
        if meta.get('enabled'):
            if meta.get('created_file') and changed:
                try:   # precise restore: a local file we created only for the tap goes away again
                    if claude_inbound._load(target)[0] == {}:
                        target.unlink()
                except (OSError, ValueError):
                    pass
            meta = {**meta, 'enabled': False}
        if explicit:
            meta = {**meta, 'explicit': explicit}
        if meta != load_meta():
            private_write(p, meta)
    elif explicit and load_meta().get('explicit') != explicit:
        private_write(p, {**load_meta(), 'explicit': explicit})
    return changed


def active(meta=None, directory=None):
    """Installed AND the layer Claude Code actually uses for the session's folder (not shadowed by a higher one)."""
    from . import claude_inbound
    meta = load_meta() if meta is None else meta
    if not meta.get('enabled'):
        return False
    if managed():
        return False            # organization policy owns the status line
    if not meta.get('settings'):
        return claude_inbound.read().get('statusLine') == meta.get('installed')
    target = Path(meta['settings'])
    if claude_inbound._load(target)[0].get('statusLine') != meta.get('installed'):
        return False
    for name, lp in layers(directory or meta.get('dir')):
        if lp == target:
            return True
        if 'statusLine' in claude_inbound._load(lp)[0]:
            return False        # a higher layer shadows our tap: no data would arrive
    return False                # installed in another folder's layer: not this session's


def shadow(directory=None):
    """Why the installed tap does not reach the session in `directory`: 'managed' / 'local' / 'project' / 'user' (the
    layer that wins instead), 'moved' (installed for another folder), 'off', or None when it is in effect."""
    from . import claude_inbound
    meta = load_meta()
    if managed():
        return 'managed'
    if not meta.get('enabled'):
        return 'off'
    if active(meta, directory):
        return None
    layer = effective(directory)[0]
    target = meta.get('settings')
    if target and target not in {str(lp) for _, lp in layers(directory or meta.get('dir'))}:
        return 'moved'
    return layer


def ensure_default(st, config=None):
    """P127: selecting shared Claude turns the status-line tap on (same moment as the claude-inbound default) unless the
    owner chose `off`; an install shadowed by another layer is re-chained where it is in effect. Never raises: failures
    (unsupported owner line, managed policy, broken JSON) are reported by doctor instead."""
    from . import claude_inbound
    try:
        cfg = config if config is not None else st.agent_config()
        if not cfg or cfg.get('kind') != 'claude' or cfg.get('session_mode', 'shared') != 'shared':
            return False
        directory = cfg.get('dir') or None
        meta = load_meta()
        if meta.get('explicit') == 'off':
            return False
        if meta.get('enabled') and active(meta, directory):
            return False
        first = not meta.get('enabled') and not meta.get('explicit')
        changed = set_enabled(True, st.root, directory)
        if first and changed:
            st.write_private(st.root / 'claude-statusline-notice.json', b'{"pending":true}\n')
        return changed
    except (OSError, ValueError, KeyError, claude_inbound.SettingsError):
        return False


def default_notice(st, lang='zh'):
    p = Path(st.root) / 'claude-statusline-notice.json'
    try:
        pending = json.loads(p.read_text()).get('pending') is True
    except (OSError, ValueError, AttributeError):
        return None
    if not pending:
        return None
    try:
        on = active(None, (st.agent_config() or {}).get('dir'))
    except (OSError, ValueError):
        on = False
    if not on:
        notice_delivered(st)
        return None
    return ('Enabled: the phone now shows Claude\'s 5-hour/weekly usage and context level (your status line still '
            'shows as before); to disable, run agentj config claude-statusline off.' if lang == 'en' else
            '已开启：手机显示 Claude 的 5 小时/周额度和上下文水位（电脑上原状态栏照常显示）；想关运行 agentj config claude-statusline off。')


def notice_delivered(st):
    p = Path(st.root) / 'claude-statusline-notice.json'
    if p.exists():
        st.write_private(p, b'{"pending":false}\n')


SHADOW_TEXT = {
    'managed': ('组织策略（managed settings）设定了状态栏，Agent J 不改它；手机看不到额度/水位。',
                'Managed policy sets the status line; Agent J never changes it, so the phone has no usage/context.'),
    'local': ('共享会话文件夹的本机私有设置（.claude/settings.local.json）有自己的状态栏，覆盖了 Agent J 的读数。',
              "The shared folder's private settings (.claude/settings.local.json) have their own status line, which shadows Agent J's tap."),
    'project': ('共享会话文件夹的项目设置（.claude/settings.json）有自己的状态栏，覆盖了 Agent J 的读数。',
                "The shared folder's project settings (.claude/settings.json) have their own status line, which shadows Agent J's tap."),
    'user': ('Agent J 的状态栏读数没有生效（用户设置被改过）。', "Agent J's status-line tap is not in effect (user settings changed)."),
    'moved': ('读数装在另一个文件夹的设置里，不是当前共享会话的。', "The tap is installed for another folder, not the current shared session's."),
    'off': ('手机额度条/水位未开启。', 'Phone usage/context meter is off.'),
}


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
        if args[0] != 'status': set_enabled(args[0] == 'on', st.root, directory, explicit=args[0])
        meta = load_meta()
        why = shadow(directory)
        layer = next((n for n, lp in layers(meta.get('dir')) if str(lp) == meta.get('settings')), 'user') if meta.get('enabled') else None
        print('Claude status-line tap: ' + ('on' if why is None else 'off' if why == 'off' else 'shadowed') + (f' ({layer} layer)' if layer else ''))
        if why not in (None, 'off'):
            zh, en = SHADOW_TEXT[why]
            print(zh + (' 运行 agentj config claude-statusline on 重新串联。' if why != 'managed' else '') + ' / ' + en +
                  (' Run agentj config claude-statusline on to re-chain.' if why != 'managed' else ''))
        print('保留原状态栏输出；仅同会话的模型/额度/花费送到手机。 / Original output preserved; only matching-session model/usage/cost reaches the phone.')
        return 0
    except (OSError, ValueError):
        print('状态栏设置未能安全修改；检查 JSON、权限、组织策略或主人后续编辑。 / Could not safely change status line; check JSON, permissions, managed policy or later owner edits.'); return 1


if __name__ == '__main__':
    raise SystemExit(tap(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == 'tap' else 2)
