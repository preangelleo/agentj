"""P536: paired-phone reply forwarding; one provider send, never a fallback/replay."""
import asyncio
import os
import re
import time
from . import privacy
from .silent import is_silent, strip_markers
from .wechat_filename import file_name
from .wechat_protocol import Failure, KINDS, MAX_MEDIA, Media

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
    for line in strip_markers(str(text)).replace('\r\n', '\n').split('\n'):
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


def _why(wc):
    """P554: bound but no context yet → `needs_message`; a refused (stale) context is still tried — the platform decides."""
    d = wc.store.data
    if wc.closed or wc.h.stopped() or not d['account'] or wc.error is not None:
        return 'not_configured'
    return None if d['context'] else 'needs_message'


async def forward(wc, turn_id, device, *, valid=lambda: True):
    res = await _forward(wc, turn_id, device, valid=valid)
    wc.last_forward = {**res, 'at': int(wc.clock())}
    wc.store.data['last_forward'] = wc.last_forward
    wc.store.save()
    wc.publish()
    return res


async def _forward(wc, turn_id, device, *, valid=lambda: True):
    if not valid() or _why(wc):
        return {'ok': False, 'why': _why(wc) if valid() else 'not_configured'}
    turn = wc.h.hist.get(turn_id) if type(turn_id) is int else None
    text = (turn or {}).get('reply', {}).get('text', '') if turn and turn.get('end') in ('done', 'stopped', 'failed') else ''
    if not text.strip() or (turn or {}).get("silent") is True or is_silent(text):
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
        files, skips = await _files(wc, api, turn, generation, account, valid)
        if skips:
            from .wechat import attachment_notice
            body = attachment_notice(body, skips, wc.h.lang)
            # File names/paths never enter the event; the client gets fixed reason codes.
            wc.h.st.log('wechat_error', reason='attachment_skipped', count=len(skips), why=skips[0]['why'])
        if files or any(s.get('delivery_unknown') for s in skips):
            wc.fwd_sent[turn_id] = time.monotonic()
        if not valid() or _why(wc) or generation != wc.store.data['generation'] or wc.store.data['account'] != account:
            return {'ok': False, 'why': (_why(wc) or 'not_configured') if valid() else 'not_configured'}
        item = None
        if len(body.encode('utf-8')) > 4000:
            item = await asyncio.to_thread(Media(api).upload, account['ilink_user_id'],
                                         {'type': 4, 'file_item': {'file_name': 'Agent-J-reply.txt'}}, body.encode('utf-8'))
        if not valid() or _why(wc) or generation != wc.store.data['generation'] or wc.store.data['account'] != account:
            return {'ok': False, 'why': (_why(wc) or 'not_configured') if valid() else 'not_configured'}
        await wc.send(api, body if item is None else '', generation, item=item)
        wc.fwd_sent = {k: v for k, v in wc.fwd_sent.items() if now - v < 30}
        wc.fwd_sent[turn_id] = time.monotonic()
        if skips:
            wc.h.st.log('wechat_forward', device=device, id=turn_id, result='partial', files=files, skipped=len(skips))
            return {'ok': False, 'why': skips[0]['why'], 'files': files, 'skips': skips, 'text_sent': True}
        wc.h.st.log('wechat_forward', device=device, id=turn_id, result='ok', files=files, skipped=0)
        return {'ok': True, **({'files':files} if files else {})}
    except Exception as e:
        if getattr(e, 'code', None) in ('context_rejected', 'no_context'):   # WeChat refused: message ClawBot first
            # P559: which one, the platform's ret (an integer; 10-27 TB: -2 at 29 h after a 23 h send passed) and the age of the
            # owner's last message, so host.log tells a platform refusal from a missing context
            at = wc.store.data.get('context_at') or 0
            wc.h.st.log('wechat_forward', device=device, id=turn_id, result='needs_message', why=e.code, ret=int(getattr(e, 'ret', 0) or 0),
                        age_h=int((getattr(wc, 'clock', time.time)() - at) // 3600) if at else -1)
            return {'ok': False, 'why': 'needs_message'}
        wc.h.st.log('wechat_forward', device=device, id=turn_id, result='send_failed')
        return {'ok': False, 'why': 'send_failed'}
    finally:
        wc.fwd_inflight.discard(turn_id)
        wc.publish()


_IMAGES = ('image/jpeg', 'image/png', 'image/gif', 'image/webp')


def attachment_failure(wc, exc, phase):
    """Return safe delivery metadata. An upload failure cannot have sent a chat item."""
    why, http, ret = 'internal', 0, 0
    if isinstance(exc, Failure):
        phase, why, http, ret = exc.phase or phase, exc.code, exc.http, exc.ret
    result = 'needs_message' if why in ('context_rejected', 'no_context') else 'provider_too_large' if http == 413 else 'send_failed'
    unknown = phase == 'sendmessage' and result == 'send_failed' and why not in (
        'api_error', 'stopped', 'binding_taken_over') and not 400 <= http < 500
    wc.store.count('send_failed')
    wc.h.st.log('wechat_attachment', result=result, phase=phase, why=why, http_status=http, ret=ret)
    return {'why': result, 'delivery_unknown': unknown}


async def _files(wc, api, turn, generation, account, valid):
    """#483: each file card of the page (already checked for the owner when the page ended, re-opened and
    re-hashed here by tg_media.read_checked) as its own WeChat message — pictures as images, everything else (videos too)
    as a file. Owner's own ClawBot chat only (this forward never picks a recipient). Over WeChat's 25 MiB → skipped with
    its reason; one failed upload stops the rest (no replay). → (files sent, [{name, why[, bytes]}])."""
    from . import tg_media
    from . import media as media_mod
    media = getattr(wc.h, 'media', None)
    sent, skipped = 0, []
    for it in (turn.get('media') or [])[:media_mod.MAX_ITEMS]:
        if not isinstance(it, dict) or not isinstance(it.get('mid'), str):
            continue
        rec = media.get(it['mid']) if media is not None else None
        label = str((rec or it).get('name') or 'file')[:128]
        if rec is None:
            skipped.append({'name': label, 'why': 'gone'})
            continue
        name = rec['parts'][-1]
        if int(rec.get('bytes') or 0) > MAX_MEDIA:
            skipped.append({'name': label, 'why': 'too_big', 'bytes': int(rec['bytes']), 'max_bytes': MAX_MEDIA})
            continue
        if not valid() or _why(wc) or generation != wc.store.data['generation'] or wc.store.data['account'] != account:
            skipped.append({'name': label, 'why': 'stopped'})
            break
        if name != rec['name'] and rec.get('mime') not in _IMAGES:
            try:
                await asyncio.to_thread(file_secret_check, rec['root'], os.path.join(rec['root'], *rec['parts']))
            except (OSError, ValueError):
                skipped.append({'name': 'file', 'why': 'secret'})
                continue
        try:
            data = await asyncio.to_thread(tg_media.read_checked, rec)
        except (media_mod.Gone, OSError):
            skipped.append({'name': label, 'why': 'gone'})
            continue
        if not valid() or _why(wc) or generation != wc.store.data['generation'] or wc.store.data['account'] != account:
            skipped.append({'name': label, 'why': 'stopped'})
            break
        phase = 'getuploadurl'
        try:
            spec = {'type': 2} if rec.get('mime') in _IMAGES else {'type': 4, 'file_item': {'file_name': name}}
            item = await asyncio.to_thread(Media(api).upload, account['ilink_user_id'], spec, data)
            if not valid() or _why(wc) or generation != wc.store.data['generation'] or wc.store.data['account'] != account:
                skipped.append({'name': label, 'why': 'stopped'})
                break
            phase = 'sendmessage'
            await wc.send(api, '', generation, item=item)
        except Exception as exc:   # noqa: BLE001 — a failed media send is never replayed; the reason is a fixed code
            if isinstance(exc, Failure) and exc.code == 'invalid_file_name':
                skipped.append({'name': 'file', 'why': exc.code})
                continue
            skipped.append({'name': label, **attachment_failure(wc, exc, phase)})
            break
        finally:
            data = b''
        sent += 1
    return sent, skipped


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


def file_binding(host):
    """Host-held snapshot taken at exact native send admission; never accepted from the CLI."""
    wc = getattr(host, 'wechat', None)
    if wc is None or _why(wc):
        return None
    return {'adapter': wc, 'account': dict(wc.store.data['account']),
            'generation': wc.store.data['generation'], 'context': wc.store.data['context']}


_FORWARDED = frozenset(('ref_msg', 'forward', 'forwarded', 'is_forwarded', 'forward_info',
                        'forward_from', 'forwarded_from', 'forward_origin'))


def inbound_file_binding(wc, msg, generation, items):
    """After sender authentication, attest only an original inbound request with its own reply context."""
    d = wc.store.data
    context = msg.get('context_token')
    if (not isinstance(context, str) or not context.strip() or _FORWARDED.intersection(msg)
            or msg.get('to_user_id') not in (None, '', d['account'].get('ilink_bot_id'))):
        return None
    body = False
    for item in items:
        if not isinstance(item, dict) or type(item.get('type')) is not int or item['type'] not in KINDS:
            return None
        detail = item.get(KINDS[item['type']][0] + '_item')
        if not isinstance(detail, dict) or _FORWARDED.intersection(item) or _FORWARDED.intersection(detail):
            return None
        if item['type'] in (1, 3) and isinstance(detail.get('text'), str) and detail['text'].strip():
            body = True
    if body:
        return {'adapter': wc, 'account': dict(d['account']), 'generation': generation,
                'context': context, 'inbound': True}
    return None


def file_binding_live(host, binding):
    if not isinstance(binding, dict):
        return False
    wc = binding.get('adapter')
    return bool(wc is not None and getattr(host, 'wechat', None) is wc and not _why(wc)
                and (not binding.get('inbound') or not (wc.store.data.get('binding_error') or wc.store.data.get('context_stale')))
                and wc.store.data['account'] == binding['account']
                and wc.store.data['generation'] == binding['generation']
                and wc.store.data['context'] == binding['context'])


def file_secret_check(root, path, data=None, mime=None):
    """Fresh local checks on raw names and actual reopened bytes, never JSON-escaped substitutes."""
    from types import SimpleNamespace
    from . import display_privacy, peer_guard, media as media_mod
    try:
        idx = display_privacy.index(SimpleNamespace(agent_cfg={'dir': root})).require_complete()
        if not peer_guard.outbound(path, idx).ok:
            raise ValueError('secret')
        if data is not None:
            text = data.decode('utf-8', 'replace')
            if idx.hits(text) or (mime in media_mod.TEXTY and peer_guard.credential_shape(text)):
                raise ValueError('secret')
    except ValueError:
        raise ValueError('secret') from None
    except Exception:
        raise OSError('privacy_unavailable') from None


async def send_file(host, item, binding, *, valid):
    """One admitted owner file → the pinned owner's existing WeChat; no new recipient or resend."""
    from . import file_show, tg_media
    def live():
        return valid() and file_binding_live(host, binding)
    if not live():
        return file_show.failure('binding_changed')
    wc = binding['adapter']
    rec = host.media.get(item['mid'])
    if rec is None:
        return file_show.failure('gone')
    if rec['bytes'] > MAX_MEDIA:
        return file_show.size_failure(MAX_MEDIA)
    phase = 'read'
    try:
        name = file_name(rec['parts'][-1])
        data = await asyncio.to_thread(tg_media.read_checked, rec)
        await asyncio.to_thread(file_secret_check, rec['root'], os.path.join(rec['root'], *rec['parts']), data, rec['mime'])
        if not live():
            return file_show.failure('binding_changed')
        api = wc.api_factory(binding['account'])
        spec = {'type': 2} if rec['mime'] in _IMAGES else {'type': 4, 'file_item': {'file_name': name}}
        guard = {'valid': live} if binding.get('inbound') else {}
        phase = 'getuploadurl'
        outgoing = await asyncio.to_thread(Media(api).upload, binding['account']['ilink_user_id'], spec, data, retry=False, **guard)
        await asyncio.to_thread(file_secret_check, rec['root'], os.path.join(rec['root'], *rec['parts']), data, rec['mime'])
        if not live():
            return file_show.failure('binding_changed')
        phase = 'sendmessage'
        if binding.get('inbound'):
            await wc.send(api, '', binding['generation'], item=outgoing, file_binding=binding, valid=live)
        else:
            await wc.send(api, '', binding['generation'], item=outgoing)
        return {'result': 'sent_to_wechat', 'platform_accepted': True, 'client_downloaded': False,
                'files': 1, 'mime': rec['mime'], 'bytes': rec['bytes']}
    except Exception as exc:
        if isinstance(exc, (OSError, ValueError)) and str(exc) in ('secret', 'privacy_unavailable'):
            return file_show.failure(str(exc))
        code = exc.code if isinstance(exc, Failure) else None
        if code == 'file_binding_changed':
            return file_show.failure('binding_changed')
        if code == 'invalid_file_name':
            return file_show.failure(code)
        detail = attachment_failure(wc, exc, phase)
        return {**file_show.failure(detail['why']), **detail}
