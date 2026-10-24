"""Owner external-channel configuration and outward projection.

This module does not change the main session or create provider credentials. A
selection is persisted by the host in its purpose-separated encrypted store.
"""
from __future__ import annotations
import re
from . import privacy
from .text import agent_label

KINDS = ('claude', 'codex', 'opencode')
CONTEXT_WEB = '答案已发到 Agent J 网页，请回那边查看'
INTERNAL = re.compile(r'贾维斯|\bJarvis\b|文件路径|目录|保存位置|image_gen(?:eration)?|functions\.|exec_command|apply_patch|CODEX_HOME|agentj[-_]|\b(?:Bash|bwrap|web_search|code_mode|MCP)\b', re.I)


def internal_text(text):
    visible = re.sub(r'https?://[^\s<>"\]]+', '', text)
    visible = re.sub(r'(?<!\w)\d+(?:/\d+)+(?!\w)', '', visible)
    return bool(INTERNAL.search(visible)) or '/' in visible or '\\' in visible



def public_reply(text):
    """A failed projection is held for Web review, never replaced by raw output."""
    if not isinstance(text, str) or not text.strip() or internal_text(text) or privacy.credentials(text):
        return None
    return privacy.owner_text(text.strip())[:1800]


def handoff(host):
    name = host.st.agent_name()
    if not isinstance(name, str) or internal_text(re.sub(r'贾维斯|\bJarvis\b', '', name, flags=re.I)) or privacy.credentials(name):
        name = None
    name = ' '.join(name.split())[:64] if name else None
    return f'已转给 {agent_label(name)}，请回 Agent J 网页跟它确认'


def plan_rows(usable):
    return [r if isinstance(r, dict) else {'id':r, 'kind':r, 'config':{'kind':r}} for r in usable]


def choice(main, saved, usable):
    rows = [r for r in plan_rows(usable) if r.get('kind') in KINDS and r.get('selectable') is not False]
    ids = list(dict.fromkeys(r['id'] for r in rows))
    codex = next((r['id'] for r in rows if r['kind'] == 'codex'), None)
    return {'config': dict(saved.get('config') or main),
            'ask': len(ids) >= 2 and not saved.get('asked'),
            'recommend': codex or (ids[0] if ids else None)}


def choose(wanted, main, usable, *, free_model=None, router_bound=False, model=None):
    rows = plan_rows(usable)
    if wanted in ('免费', 'free'):
        raise ValueError('free_temporarily_unavailable')
    if wanted == 'openrouter':
        if not router_bound:
            raise ValueError('openrouter_not_bound')
        if not isinstance(model, str) or privacy.credentials(model) or not re.fullmatch(r'openrouter/[A-Za-z0-9_.:/-]{1,160}', model):
            raise ValueError('choose_openrouter_model')
        return {'kind':'opencode', 'model':model}
    if wanted == 'shared':
        if main.get('kind') not in KINDS:
            raise ValueError('harness_unavailable')
        return dict(main)
    row = next((r for r in rows if r['id'] == wanted and r.get('kind') in KINDS and r.get('selectable') is not False), None)
    if row is None:
        raise ValueError('harness_unavailable')
    return dict(main) if wanted == main.get('kind') else dict(row['config'])


class Reply(str):
    """Text plus host-verified native image bytes; no model-supplied paths."""
    def __new__(cls, text, images=()):
        obj = super().__new__(cls, text)
        obj.images = tuple(images)
        return obj


def openrouter_bound(host):
    """Exact native provider entry only. Unverified/unknown stores stay unbound."""
    import json
    import os
    from pathlib import Path
    from .provider_profiles import secure_read
    connected = getattr(getattr(host, 'agent', None), 'connected_at_start', None)
    if isinstance(connected, list) and 'openrouter' in connected:
        return True
    root = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    try:
        data = json.loads(secure_read(root / 'opencode/auth.json'))
        row = data.get('openrouter') if isinstance(data, dict) else None
        return isinstance(row, dict) and any(isinstance(row.get(k), str) and bool(row[k]) for k in ('key','access','refresh'))
    except (OSError, ValueError):
        return False
