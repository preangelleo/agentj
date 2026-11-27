"""Attach one checked file to the admitted owner turn; never select a recipient."""
import asyncio
import json
import os
import sys
from . import media, owner_call, privacy, provenance, history


def request(path, label='', cwd=None, *, delivery=None):
    # Shell argv expands ~/ before CLI entry; native tool input still has the tilde.
    if isinstance(path, str) and path.startswith('~/'):
        path = os.path.expanduser(path)
    req = {'t': 'file_show', 'path': path, 'label': label, 'cwd': os.path.abspath(cwd or os.getcwd())}
    if delivery is not None:
        req['delivery'] = delivery
    return req


def cli(args):
    from . import elevate, names
    from .state import State
    st = State()
    try:
        result = elevate.client_request(st, request(args.path, args.label, delivery=getattr(args, 'delivery', None)), timeout=30,
                                        path=os.environ.get(elevate.ENV) or names.elevate_sock(st))
    except (OSError, ValueError):
        result = failure('broker_unavailable')
    if result.get('result') == 'unavailable':
        result = failure('broker_unavailable')
    hints = SEND_FAILURE_HINTS if getattr(args, 'delivery', None) == 'wechat' else FAILURE_HINTS
    if hint := hints.get(result.get('why')):
        result['hint'] = hint
    print(json.dumps(result, ensure_ascii=False) if args.json else result.get('result', 'failure') + ': ' + result.get('why', ''))
    if not args.json and (result.get('result') == 'queued' or 'hint' in result):
        print(result.get('hint') or json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get('result') in ('queued', 'attached_to_page') else 1)


def add_parser(sub):
    p = sub.add_parser('file', help='当前主人回合的文件 / Files in the active admitted owner turn')
    actions = p.add_subparsers(dest='file_action', required=True)
    show = actions.add_parser('show', help='附到当前页；不代表手机已下载 / Attach to current page; not a download receipt',
        description='POSIX Linux/macOS; Windows via WSL. Linux tested; macOS/WSL not verified. Requires a live admitted owner turn.')
    show.add_argument('path')
    show.add_argument('--label', default='')
    show.add_argument('--json', action='store_true')
    show.set_defaults(fn=cli)
    send = actions.add_parser('send', help='发送单个文件到自己已绑定的微信 / Send one file to your bound WeChat',
        description='Requires an exact admitted live owner turn. queued is pending; later FILE_SEND_RESULT reports platform acceptance, not delivery.')
    send.add_argument('path', help='文件须在本轮工作目录内；在目录外的 ~/.codex/generated_images 图片须先复制进去 / '
                                   'File must be inside the admitted work folder. Copy images from ~/.codex/generated_images there first if needed.')
    send.add_argument('--to', required=True, choices=['wechat'])
    send.add_argument('--json', action='store_true')
    send.set_defaults(fn=cli, label='', delivery='wechat')


HINT = '请在当前主人回合让 Agent 单独运行 `agentj file show PATH --json`，不要加 cd、&&、管道或重定向。'

OWNER_HINT = '请用已配对的遥控器在这个会话重新发消息，让 Agent 附上文件。'

FAILURE_HINTS = {
    'native_admission_missing': HINT + ' 要附到网页，请从已配对的遥控器提出请求。微信来的文件请求只能发回同一个绑定微信。'
                                       ' To attach to the Web App, ask from a paired remote. A file requested from WeChat can only go back to that same bound WeChat chat.',
    'no_owner_session': OWNER_HINT,
    'native_admission_expired': OWNER_HINT,
    'native_admission_ambiguous': '请用已配对的遥控器重新发消息，让 Agent 一次只附一个文件。',
    'native_admission_cwd_mismatch': '请让 Agent 在当前会话的工作目录单独运行附文件命令。',
    'native_admission_request_mismatch': HINT,
}


SEND_HINT = ('Ask from a paired remote or in a new message from your bound WeChat chat. Group, quoted and forwarded messages cannot request a file. '
             'Run `agentj file send PATH --to wechat --json` alone in the session’s work folder. queued is pending; wait for FILE_SEND_RESULT. '
             'Do not retry uncertain delivery. '
             '请从已配对的遥控器或在绑定微信私聊中亲自发一条新消息。群聊、引用和转发消息不能要文件。'
             '文件须在本轮工作目录内。单独运行发送命令，看到 queued 后等 FILE_SEND_RESULT。未确认送出时请勿自动重试。')

SEND_FAILURE_HINTS = {
    'native_admission_missing': SEND_HINT,
    'no_owner_session': '请在已绑定的微信私聊中发一条新消息，或从已配对的遥控器提出发送请求。 / Send a new message from your bound WeChat chat or ask from a paired remote.',
    'native_admission_expired': '这次文件发送请求已失效，请从已绑定的微信私聊或已配对的遥控器重新发起。 / This request expired. Ask again in your bound WeChat chat or from a paired remote.',
    'native_admission_cwd_mismatch': '请让 Agent 在当前会话的工作目录单独运行微信文件发送命令。',
    'native_admission_request_mismatch': '请核对这次微信文件发送命令里的路径与获准命令是否一致。',
    'binding_changed': '原来的微信连接或消息已失效。请确认微信已绑定，再在私聊中发一条新消息。 / The original WeChat binding or message is no longer active. Check the binding, then send a new private message.',
    'needs_message': '微信没有接受这次发送。请在已绑定的微信私聊中发一条新消息。 / WeChat declined the send. Send a new message in your bound private chat.',
}

WECHAT_FILE_HINTS = {
    'wechat_page_forbidden': ('从微信提出的文件请求只能发回同一个绑定微信。要把文件附到网页，请从已配对的遥控器重新提出。 '
                              '/ A file requested from WeChat can only go back to that same bound WeChat chat. To attach it to the Web App, ask from a paired remote.'),
    'wechat_origin_invalid': ('请在已绑定的微信私聊中亲自发一条新消息来要文件。群聊、引用和转发消息不能用于这项操作。 '
                              '/ Ask for the file in a new message in your bound WeChat chat. Group, quoted and forwarded messages cannot request this operation.'),
}


def source_refusal(host, req, source, send, binding):
    if provenance.is_owner(source):
        return None
    if provenance.owner_channel(source) != 'wechat':
        return 'owner_source_required'
    if req.get('delivery') != 'wechat':
        return 'wechat_page_forbidden'
    if (not binding or not binding.get('inbound') or getattr(send, 'wechat_file_binding', None) is not binding
            or provenance.owner_channel(send.source) != 'wechat'):
        return 'wechat_origin_invalid'
    from .wechat_forward import file_binding_live
    return None if file_binding_live(host, binding) else 'binding_changed'


def failure(why):
    return {'result': 'failure', 'why': why, 'client_downloaded': False}


def generated_image_hint(host, root, path):
    """Only the already checked owner turn gets a hint about its own generated images."""
    if media._inside(path, root):
        return None
    image_root = host.media.generated_root()
    if image_root and path != image_root and media._inside(path, image_root):
        from .text import pick
        return pick(host.lang, '图片在 Codex 的生成目录里。请先将图片复制到当前会话的工作目录，再对副本单独运行文件命令。',
                    'The image is in the Codex output folder. First copy it into this session’s working directory, then run the file command on the copy.')
    return None


async def attach(host, req, check, admitted_root=None, delivery_binding=None):
    if req.get('delivery') not in (None, 'wechat'):
        return failure('shape')
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
        if why := source_refusal(host, req, send.source, send, delivery_binding):
            return why
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
    if why_now := refusal():
        if delivery_binding and delivery_binding.get('inbound'):
            why_now = source_refusal(host, req, send.source, send, delivery_binding) or 'native_admission_expired'
            return failure(why_now)
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
    if req.get('delivery') == 'wechat':
        from . import wechat_forward
        return await wechat_forward.send_file(host, item, delivery_binding, valid=lambda: refusal() is None)
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
