"""Install curated signed plaza examples once per working root."""
from __future__ import annotations
import io
import json
import os
import time
from pathlib import Path
from . import wizard as wz

STARTERS = {
    'llm-wiki': {'zh': '知识库', 'en': 'Knowledge wiki'},
    'script-article-writing': {'zh': '脚本与文章', 'en': 'Scripts and articles'},
    'ebook-writing': {'zh': '电子书', 'en': 'Ebook writing'},
}
ENOUGH = 3
ENV_OFF = 'AGENTJ_NO_STARTERS'
SKIP_DIRS = {'node_modules', 'credentials', '__pycache__'}


def disabled():
    return os.environ.get(ENV_OFF, '').strip().lower() in ('1', 'true', 'yes', 'on')


def is_wdb(folder: Path) -> bool:
    doc = folder / "documentation"
    if (doc / "CONSTITUTION.md").is_file() and (doc / "ROLES.md").is_file():
        return True
    try:
        s = json.loads((doc / "STRUCTURE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(s, dict) and bool(s.get("bible_version"))


def wdb_folders(root: Path) -> list[str]:
    out = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if e.name.startswith(".") or e.name in SKIP_DIRS or not e.is_dir(follow_symlinks=False):
            continue
        if is_wdb(Path(e.path)):
            out.append(e.name)
    return out


def ensure(root, harnesses=None, lang=None, *, st=None, post=None, get=None, keyring=None):
    root = Path(root)
    if disabled() or not root.is_dir() or root.is_symlink():
        return []
    from . import market, cloud
    if st is None:
        try:
            st = market._state()
        except market.PlazaError:
            return []  # the root-document wizard can run before agentj init
    # init can precede account binding; do not consume the once-only marker then.
    linked = bool(cloud.read_cloud(st))
    h = next(iter(harnesses or []), None)
    h = {'claude': 'claude_code', 'claude_code': 'claude_code', 'codex': 'codex', 'opencode': 'opencode'}.get(h)
    if h is None and linked:
        try:
            h = market.resolve_harness(None, st)
        except market.PlazaError:
            linked = False
    out = []
    with wz._locked(root):
        m = wz.read_manifest(root)
        recs = m.get('starters') if isinstance(m.get('starters'), dict) else {}
        # The market ledger survives a crash between package installation and this manifest write.
        # Recover its decision before checking the directory: a deletion must stay deleted.
        installed = {r.get('name') for r in market.read_installed(st)
                     if r.get('target') == str(root / r.get('name', ''))}
        # Freeze the pre-existing count before installing this batch.
        enough = len(wdb_folders(root)) >= ENOUGH
        for name in STARTERS:
            rec = recs.get(name)
            if not isinstance(rec, dict) and name in installed:
                rec = recs[name] = {'state': 'installed', 'announced': False}
                m['starters'] = recs
                wz._write_manifest(root, m)
            if isinstance(rec, dict):
                if rec.get('state') == 'installed' and (not (root/name).is_dir() or (root/name).is_symlink()):
                    rec['state'] = 'removed'
                    m['starters'] = recs
                    wz._write_manifest(root, m)
                    out.append({'path': name, 'status': 'starter-removed'})
                continue
            if os.path.lexists(root/name):
                recs[name] = {'state': 'skipped', 'why': 'name-taken'}
            elif enough:
                recs[name] = {'state': 'skipped', 'why': 'enough'}
            elif not linked:
                continue
            else:
                try:
                    rc = market.run_install(name, harness=h, workspace=str(root), st=st,
                                            post=post, get=get, keyring=keyring, out=io.StringIO(), _starter=True)
                except (OSError, ValueError, market.PlazaError):
                    out.append({'path': name, 'status': 'starter-retry'})
                    continue
                if rc != market.EXIT_OK:
                    out.append({'path': name, 'status': 'starter-retry'})
                    continue
                recs[name] = {'state': 'installed', 'announced': False, 'at': int(time.time())}
            m['starters'] = recs
            # Persist after each success, so interruption cannot make a deleted package return.
            wz._write_manifest(root, m)
            out.append({'path': name, 'status': 'starter-' + recs[name]['state']})
    return out


def status(root):
    root = Path(root)
    recs = wz.read_manifest(root).get('starters') or {}
    out = []
    for name, titles in STARTERS.items():
        rec = recs.get(name) if isinstance(recs.get(name), dict) else {}
        state = rec.get('state', 'pending')
        if state == 'installed' and (not (root/name).is_dir() or (root/name).is_symlink()):
            state = 'removed'
        out.append({'name': name, 'state': state, 'why': rec.get('why'), 'company': titles['zh']})
    return out


def take_announcement(root, lang='zh'):
    root = Path(root)
    try:
        with wz._locked(root):
            m = wz.read_manifest(root)
            recs = m.get('starters') or {}
            names = [n for n in STARTERS if isinstance(recs.get(n), dict) and recs[n].get('state') == 'installed'
                     and recs[n].get('announced') is False and (root/n).is_dir() and not (root/n).is_symlink()]
            if not names:
                return ''
            for n in names:
                recs[n]['announced'] = True
            wz._write_manifest(root, m)
    except (OSError, wz.WizardError):
        return ''
    labels = ', '.join(STARTERS[n]['en' if lang == 'en' else 'zh'] + ' (`' + n + '`)' for n in names)
    if lang == 'en':
        return ('Installed plaza examples in your working folder: ' + labels +
                '. Open Workflows to try one. Tell Agent J: organise my other folders to this spec. '
                'Delete any example you do not need; it will not return automatically.')
    return ('已从广场预装示例工作流：' + labels + '。在「工作流」页开始第一步。交给 Agent J：帮我按这个规格整理我的其他文件夹。'
            '用不上可以删除，不会自动回来。')
