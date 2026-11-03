"""Attach one checked file to the admitted owner turn; never select a recipient."""
import asyncio
import json
import os
import sys
from . import media, owner_call, privacy, provenance, history


def request(path, label='', cwd=None):
    # Shell argv expands ~/ before CLI entry; native tool input still has the tilde.
    if isinstance(path, str) and path.startswith('~/'):
        path = os.path.expanduser(path)
    return {'t': 'file_show', 'path': path, 'label': label, 'cwd': os.path.abspath(cwd or os.getcwd())}


def cli(args):
    from . import elevate, names
    from .state import State
    st = State()
    try:
        result = elevate.client_request(st, request(args.path, args.label), timeout=30,
                                        path=os.environ.get(elevate.ENV) or names.elevate_sock(st))
    except (OSError, ValueError):
        result = failure('broker_unavailable')
    if result.get('result') == 'unavailable':
        result = failure('broker_unavailable')
    print(json.dumps(result, ensure_ascii=False) if args.json else result.get('result', 'failure') + ': ' + result.get('why', ''))
    if not args.json and result.get('result') == 'queued':
        print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get('result') in ('queued', 'attached_to_page') else 1)


def add_parser(sub):
    p = sub.add_parser('file', help='当前主人回合的文件 / Files in the active admitted owner turn')
    show = p.add_subparsers(dest='file_action', required=True).add_parser('show', help='附到当前页；不代表手机已下载 / Attach to current page; not a download receipt',
        description='POSIX Linux/macOS; Windows via WSL. Linux tested; macOS/WSL not verified. Requires a live admitted owner turn.')
    show.add_argument('path')
    show.add_argument('--label', default='')
    show.add_argument('--json', action='store_true')
    show.set_defaults(fn=cli)


def failure(why):
    return {'result': 'failure', 'why': why, 'client_downloaded': False}


async def attach(host, req, check, admitted_root=None):
    send = getattr(host.agent, 'cur_send', None)
    tid = getattr(host, 'cur_turn', None)
    page = host.hist.get(tid)
    cfg = host.agent_cfg or {}
    root = admitted_root or cfg.get('dir')
    root = os.path.realpath(root) if root else None
    ids = next((ids for ids in host.hist.parts.values() if tid in ids), [tid])
    owner_page = ids[0]
    def refusal():
        if not check():
            return 'expired'
        if not owner_call.bound():
            return 'native_context_missing'
        if not root or os.path.realpath((host.agent_cfg or {}).get('dir') or '') != root:
            return 'project_changed'
        if send is None or host.agent.cur_send is not send:
            return 'no_owner_session'
        if not provenance.is_owner(send.source):
            return 'owner_source_required'
        if (page is None or host.hist.get(tid) is not page or page.get('end') != 'open'
                or host.cur_turn != tid or send.turn not in ids):
            return 'owner_page_inactive'
        return None
    if why := refusal():
        return failure(why)
    path = req.get('path')
    label = req.get('label', '')
    if not isinstance(path, str) or not path or len(path) > media.REF_MAX or not isinstance(label, str) or len(label) > 128:
        return failure('shape')
    raw = os.path.abspath(os.path.join(req['cwd'], os.path.expanduser(path)))
    # The native call's configured project is the ceiling, not a caller-chosen root.
    if not media._inside(raw, os.path.realpath(root)) or os.path.realpath(raw) != raw:
        return failure('outside')
    shown = privacy.owner_text(raw).replace('>', '%3E').replace('<', '%3C')
    ref = media.Ref('<' + shown + '>', raw, True)
    def prepare():
        rec, why = host.media.check(ref, owner=True, root=root, mask=False)
        if why or not rec:
            return None, why or 'gone'
        host.media._persist(rec)
        return rec, None
    try:
        rec, why = await asyncio.to_thread(prepare)
    except Exception:
        rec, why = None, 'failed'
    if refusal():
        return failure('expired')
    if why:
        return failure(why)
    existing = {item['mid']: item for pid in ids for item in (host.hist.get(pid) or {}).get('media', [])}
    retained = [item for item in existing.values() if item.get('ref') != rec['ref']]
    if len(retained) >= media.MAX_ITEMS:
        return failure('too_many')
    if sum(item.get('bytes', 0) for item in retained) + rec['bytes'] > media.PAGE_BYTES:
        return failure('too_big')
    with host.media.lock:
        old = next(((mid, r) for mid, r in host.media.table.items() if r.get('page') == owner_page
                    and r.get('ref') == rec['ref'] and r.get('sha256') == rec['sha256']), None)
    item = ({'mid': old[0], **{k: rec[k] for k in ('name', 'mime', 'kind', 'bytes', 'sha256', 'ref')}}
            if old else host.media.register(rec, owner_page))
    name = privacy.owner_text(label or rec['name'])[:128].replace('[', '').replace(']', '').replace('\n', ' ')
    shown = privacy.owner_text(raw).replace('>', '%3E').replace('<', '%3C')
    link = f'[{name}](<{shown}>)'
    room = history.PART_MAX - history._units(page['reply']['text'] + history.SEP)
    if room < history._units(link):
        link = '\n' * max(room, 0) + link
    changed = host.hist_update(tid, append=link,
                     media=([*(x for x in (page.get('media') or []) if x.get('ref') != item['ref']), item], page.get('media_skip') or []))
    if changed:
        tid = host.cur_turn = changed[-1]['id']

    return {'result': 'attached_to_page', 'page': tid, 'mid': item['mid'], 'mime': item['mime'],
            'bytes': item['bytes'], 'client_downloaded': False}
