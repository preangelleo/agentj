"""P536: paired-phone reply forwarding; one provider send, never a fallback/replay."""
import asyncio
import re
import time
from . import privacy
from .silent import is_silent
from .wechat_protocol import Media

_FENCE = re.compile(r'^\s*(```|~~~)')
_RULE = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')
_LINK = re.compile(r'!?\[([^\]]*)\]\(\s*<?([^)\s>]*)>?(?:\s+"[^"]*")?\s*\)')
# ASCII-only word edges: CJK text hugs emphasis (已*完成*), while 2*3*4 / snake_case stay literal.
_EMPH = [re.compile(p) for p in (r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'(?<![A-Za-z0-9])__(?=\S)(.+?)(?<=\S)__(?![A-Za-z0-9])', r'~~(?=\S)(.+?)(?<=\S)~~',
                                 r'(?<![*A-Za-z0-9])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?![*A-Za-z0-9])',
                                 r'(?<![A-Za-z0-9_])_([^\s_](?:[^_\n]*?[^\s_])?)_(?![A-Za-z0-9_])')]


def _link(m):
    text, url = m.group(1).strip(), m.group(2)
    if url.startswith('reply:') or not url or url == text:
        return text or url
    return f'{text} {url}' if text else url


def _inline(s):
    out = []
    for i, part in enumerate(re.split(r'(`+)(.+?)\1', s)):
        if i % 3 == 2:
            out.append(part.strip())
        elif i % 3 == 0:
            # Backslash escapes hide in private-use code points so emphasis never pairs with them.
            part = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|>~])', lambda m: chr(0xE000 + ord(m.group(1))), part)
            part = _LINK.sub(_link, re.sub(r'<((?:https?|mailto):[^>\s]+)>', r'\1', part))
            for rx in _EMPH:
                part = rx.sub(r'\1', part)
            out.append(re.sub('[-]', lambda m: chr(ord(m.group()) - 0xE000), part))
    return ''.join(out)


def plain_text(text):
    """#304: WeChat renders no Markdown — owner-facing text leaves as clean plain text."""
    lines, code = [], None
    for line in str(text).replace('\r\n', '\n').split('\n'):
        m = _FENCE.match(line)
        if code is not None:
            if m and m.group(1) == code and not line.strip().strip(code[0]):
                code = None
            else:
                lines.append(line)
            continue
        if m:
            code = m.group(1)
            continue
        if re.fullmatch(r'\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*|\s*\|\s*:?-{2,}:?\s*\|\s*', line):
            continue  # table separator row
        if _RULE.match(line):
            lines.append('')
            continue
        line = re.sub(r'^(\s*>)+\s?', '', line)
        line = re.sub(r'^\s{0,3}#{1,6}\s+(.*?)(\s+#+)?\s*$', r'\1', line)
        if line.strip().startswith('|') and line.strip().endswith('|') and len(line.strip()) > 1:
            line = '  '.join(_inline(c.strip()) for c in line.strip()[1:-1].split('|'))
        else:
            line = re.sub(r'^(\s*)[-*+]\s+\[([ xX])\]\s+', lambda m: m.group(1) + ('• ☐ ' if m.group(2) == ' ' else '• ☑ '), line)
            line = re.sub(r'^(\s*)[-*+]\s+', r'\1• ', line)
            line = re.sub(r'^(\s*)(\d+)[.)]\s+', r'\1\2. ', line)
            line = _inline(line)
        lines.append(re.sub(r'<br\s*/?>', '\n', line).rstrip())
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip('\n')


async def forward(wc, turn_id, device, *, valid=lambda: True):
    if not valid() or not wc.can_forward():
        return {'ok': False, 'why': 'not_configured'}
    turn = wc.h.hist.get(turn_id) if type(turn_id) is int else None
    text = (turn or {}).get('reply', {}).get('text', '') if turn and turn.get('end') in ('done', 'stopped', 'failed') else ''
    if not text.strip() or is_silent(text):
        return {'ok': False, 'why': 'not_found'}
    now = time.monotonic()
    if turn_id in wc.fwd_inflight:
        return {'ok': False, 'why': 'in_flight'}
    if now - wc.fwd_sent.get(turn_id, -1e9) < 30:
        return {'ok': False, 'why': 'recently_sent'}
    rate = [at for at in wc.fwd_rate.get(device, []) if now - at < 60]
    if len(rate) >= 6:
        return {'ok': False, 'why': 'rate_limited'}
    wc.fwd_rate[device] = rate + [now]
    wc.fwd_inflight.add(turn_id)
    generation = wc.store.data['generation']
    account = dict(wc.store.data['account'])
    body = plain_text(privacy.owner_text(text))
    try:
        api = wc.api_factory(account)
        item = None
        if len(body.encode('utf-8')) > 4000:
            item = await asyncio.to_thread(Media(api).upload, account['ilink_user_id'],
                                         {'type': 4, 'file_item': {'file_name': 'Agent-J-reply.txt'}}, body.encode('utf-8'))
        if not valid() or not wc.can_forward() or generation != wc.store.data['generation'] or wc.store.data['account'] != account:
            return {'ok': False, 'why': 'not_configured'}
        await wc.send(api, body if item is None else '', generation, item=item)
        wc.fwd_sent = {k: v for k, v in wc.fwd_sent.items() if now - v < 30}
        wc.fwd_sent[turn_id] = time.monotonic()
        wc.h.st.log('wechat_forward', device=device, id=turn_id, result='ok')
        return {'ok': True}
    except Exception:
        wc.h.st.log('wechat_forward', device=device, id=turn_id, result='send_failed')
        return {'ok': False, 'why': 'send_failed'}
    finally:
        wc.fwd_inflight.discard(turn_id)
        wc.publish()


async def on_forward(host, session, rid, turn_id):
    def live():
        return (host.sessions.get(session.cid) is session and session.state == 'ready'
                and host.st.is_allowed(session.pub) and not host.stopped())
    if not live():
        return
    try:
        wc = getattr(host, 'wechat', None)
        res = await wc.forward(turn_id, session.device, valid=live) if wc else {'ok': False, 'why': 'not_configured'}
    except Exception:
        res = {'ok': False, 'why': 'send_failed'}
    if live():
        await host.send_app(session, {'t': 'wx_fwd_res', 'r': rid, **res})
