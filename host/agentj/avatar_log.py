"""P537: bounded, read-only review of host-private encrypted avatar records.

Only allowlisted summaries leave the store, through the existing local Agent socket.
Old records have no reply evidence; absence is explicitly unknown.
"""
from __future__ import annotations
import datetime
import re
import time
from .privacy import redact


def since_time(raw, now=None):
    now = time.time() if now is None else now
    if raw in (None, ''):
        return now - 86400
    if not isinstance(raw, str):
        raise ValueError('since')
    m = re.fullmatch(r'(\d+)([mhd])', raw)
    if m:
        return now - int(m[1]) * {'m':60, 'h':3600, 'd':86400}[m[2]]
    try:
        return datetime.datetime.fromisoformat(raw).astimezone().timestamp()
    except ValueError:
        raise ValueError('since: 2h / 3d / 2026-10-01T08:00') from None


def rows(stores, since):
    result = []
    for store in stores:
        if store is None:
            continue
        for r in store.data.get('records', {}).values():
            if not isinstance(r, dict) or not isinstance(r.get('at'), (int, float)) or r['at'] < since:
                continue
            result.append({'at':r['at'], 'channel':r.get('source', 'wechat'), 'id':r.get('id'),
                           'summary':redact(str(r.get('text', '')))[:1000],
                           'reply':redact(str(r.get('reply_summary', '')))[:1000] or None,
                           'state':r.get('state', 'unknown'), 'outcome':r.get('outcome', 'unknown'),
                           'delivery':r.get('delivery', 'unknown'),
                           'needs_review':r.get('state') not in ('replied', 'claimed') or r.get('delivery') != 'sent'})
    return sorted(result, key=lambda r:(r['at'], str(r['id'])))


def snapshot(host, since):
    try:
        start = since_time(since)
    except ValueError as e:
        return {'ok':False, 'why':str(e)}
    stores = [getattr(getattr(host, name, None), 'store', None) for name in ('wechat', 'external_channels')]
    data = rows(stores, start)
    # Socket REQ_MAX is 64KiB. Bound UTF-8 bytes, not only character counts.
    import json
    selected = []
    for row in reversed(data):
        if len(json.dumps([row, *selected], ensure_ascii=False).encode()) > 48000:
            break
        selected.insert(0, row)
    return {'ok':True, 'records':selected, 'truncated':len(selected) < len(data),
            'coverage':'retained local records; historical replies/delivery may be unknown',
            'unavailable':[name for name, store in zip(('wechat', 'mail/telegram'), stores) if store is None]}


def cli(a):
    from .heartbeat import call
    import json
    res = call({'op':'avatar_log', 'since':a.since})
    if a.json or not res.get('ok'):
        print(json.dumps(res, ensure_ascii=False))
    else:
        print('分身处理记录 / Avatar handling records — 本机保留记录；历史回复和送达可能未知 / retained local records; old replies and delivery may be unknown')
        for r in res['records']:
            at = datetime.datetime.fromtimestamp(r['at']).astimezone().isoformat(timespec='seconds')
            print(f"{at} · {r['channel']} · {r['state']} · {r['outcome']} · delivery={r['delivery']} · {r['id']}")
            print('  收到 / In: ' + r['summary'])
            print('  回复 / Out: ' + (r['reply'] or '未知 / unknown'))
        if res['truncated']:
            print('记录已截短，请缩小时间范围 / Truncated; narrow --since')
        if res['unavailable']:
            print('通道不可用 / Unavailable: ' + ', '.join(res['unavailable']))
    raise SystemExit(0 if res.get('ok') else 1)
