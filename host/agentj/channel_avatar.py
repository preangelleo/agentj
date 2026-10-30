"""Owner external-channel configuration and outward projection.

This module does not change the main session or create provider credentials. A
selection is persisted by the host in its purpose-separated encrypted store.
"""
from __future__ import annotations
import os
import re
from . import privacy
from .text import agent_label

KINDS = ('claude', 'codex', 'opencode')
CONTEXT_WEB = '答案已发到 Agent J 网页，请回那边查看'
INTERNAL = re.compile(r'贾维斯|\bJarvis\b|文件路径|目录|保存位置|image_gen(?:eration)?|functions\.|exec_command|apply_patch|CODEX_HOME|agentj[-_]|\b(?:Bash|bwrap|web_search|code_mode|MCP)\b', re.I)
# P557: the clause-level wording check drops only the word for a folder; 「目录」 alone (a book's contents) is ordinary text
CLAUSE_INTERNAL = re.compile(INTERNAL.pattern.replace('|目录|', '|'), re.I)
URL = re.compile(r'https?://[^\s<>"\]]+')
_SEG = r'[^\s/\\<>"\'`|()（）「」『』【】，。；：！？、]+'
PATH = re.compile(r'(?<![A-Za-z0-9_])(?:file://)?(?:~|\.{1,2})?(?:/' + _SEG + r'){2,}/?'      # /a/b, ~/a/b, ./a/b
                  r'|(?<![A-Za-z0-9_])(?:~|\.{1,2})/' + _SEG +                                   # ~/a, ./a
                  r'|(?<![A-Za-z0-9_/])/' + _SEG + r'\.[A-Za-z0-9]{1,8}(?![A-Za-z0-9])' +           # /out.png
                  r'|(?<![A-Za-z0-9_./~-])[\w.-]+(?:/[\w.-]+)+\.[A-Za-z0-9]{1,8}(?![A-Za-z0-9])' +    # outbox/out.png
                  r'|[A-Za-z]:\\\S*|\\\\\S+')                                                        # C:\…, \\server\…
CLAUSE = re.compile(r'[^\n。！？；，!?;]*?(?:[。！？；，!?;]+|[.,](?=\s)|\n|$)')
IMAGE_ONLY = {'zh': '图片在这里。', 'en': 'Here is the image.'}
FILE_ONLY = {'zh': '文件在这里。', 'en': 'Here is the file.'}
FILES_MAX, FILES_BYTES = 8, 25 * 1024 * 1024   # per reply: WeChat's 25 MiB media cap; Telegram allows 8 per reply


def internal_text(text):
    visible = re.sub(r'https?://[^\s<>"\]]+', '', text)
    visible = re.sub(r'(?<!\w)\d+(?:/\d+)+(?!\w)', '', visible)
    return bool(INTERNAL.search(visible)) or '/' in visible or '\\' in visible


def outward(text):
    """P557: drop each clause that names a local path or an internal tool/word, keep the rest (links stay)."""
    kept = []
    for m in CLAUSE.finditer(text):
        clause = m.group(0)
        if not clause:
            continue
        bare = URL.sub('', clause)
        if PATH.search(bare) or CLAUSE_INTERNAL.search(bare):
            if clause.endswith('\n'):
                kept.append('\n')
            continue
        kept.append(clause)
    lines = [re.sub(r'^[\s，,、；;：:]+|[\s，,、；;：:]+$', '', ln) for ln in ''.join(kept).split('\n')]
    lines = [ln for ln in lines if re.search(r'\w', ln) or ln == '']
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def public_reply(text, images=(), lang=None, files=()):
    """P557: the owner's channels get the reply with local paths and internal words removed, never a handoff for wording.
    Only real credentials hold it for Web App review. Generated images and named files travel as attachments, so an
    attachment with no text left gets a fixed caption."""
    if not isinstance(text, str) or privacy.credentials(text):
        return None
    clean = outward(text)
    if not clean and (images or files):
        lang = lang if lang in IMAGE_ONLY else 'zh' if re.search(r'[\u4e00-\u9fff]', text) else 'en'
        clean = (IMAGE_ONLY if images else FILE_ONLY)[lang]
    if not clean:
        return None
    return privacy.owner_text(clean)[:1800]


def attachments(host, text):
    """P557: files an owner-channel reply names → (images [(mime, data)], files [(name, mime, data)], their paths, keys): the
    same owner-direction checks as a main reply to the owner's Telegram (tg_media.collect / read_checked: inside the working
    folder, deny list, no-follow open, secret scan). Credential files come back in `keys` for pickup cards, never attached."""
    from . import tg_media
    media, keys = getattr(host, 'media', None), []
    if not media or not getattr(media, 'workdir', None) or not isinstance(text, str):
        return [], [], [], keys
    try:
        items, _skips = tg_media.collect(media, text, keys)
    except Exception:   # noqa: BLE001 — no attachment is the old behaviour, never a lost reply
        return [], [], [], keys
    images, files, paths, total = [], [], [], 0
    for rec in items[:FILES_MAX]:
        try:
            data = tg_media.read_checked(rec)
        except Exception:   # noqa: BLE001
            continue
        if total + len(data) > FILES_BYTES:
            break
        total += len(data)
        paths.append(os.path.join(rec['root'], *rec['parts']))
        if rec.get('mime') in ('image/png', 'image/jpeg') and len(data) <= tg_media.PHOTO_MAX:
            images.append((rec['mime'], data))
        else:
            files.append((str(rec.get('name') or 'file')[:128], rec.get('mime') or 'application/octet-stream', data))
    return images, files, paths, keys


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
    """Text plus host-verified image bytes and checked files [(name, mime, data)]; no model-supplied paths."""
    def __new__(cls, text, images=(), files=()):
        obj = super().__new__(cls, text)
        obj.images = tuple(images)
        obj.files = tuple(files)
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
