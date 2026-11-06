"""Completion receipts and Seen acknowledgments; never infer completion from dedup IDs."""
from __future__ import annotations
import contextlib
import email
import email.policy
import hashlib
import json
import re

NAME = 'read-receipts.json'


def load(m, cfg):
    from . import mail as M
    epoch = M.read_state(m.st)['uidvalidity']
    scope = hashlib.sha256(json.dumps([M.credential_destination(cfg), cfg['generation'], epoch]).encode()).hexdigest()
    d = M._read_json(M.root(m.st) / NAME, {})
    return d if d.get('scope') == scope else {'scope': scope, 'rows': {}}


def save(m, d):
    from . import mail as M
    M._write(m.st, NAME, json.dumps(d, separators=(',', ':')).encode())


async def completed(m, cfg, epoch, uid, mid):
    from . import mail as M
    if M.read_config(m.st) != cfg or str(M.read_state(m.st)['uidvalidity']) != str(epoch):
        return
    d = load(m, cfg)
    row = d['rows'].setdefault(mid, {'epoch': epoch, 'uid': uid, 'acked': False})
    save(m, d)                         # completion survives reconnect / STORE failure
    if row['acked']:
        return                        # owner may have manually changed it back to unread
    pw = m.password(cfg)
    if not pw:
        return
    try:
        import asyncio
        await asyncio.to_thread(M.mark_read, cfg, pw, row['epoch'], row['uid'], mid)
    except M.MailError as e:
        m.log('mark_read', result=e.why)
    else:
        if M.read_config(m.st) != cfg or str(M.read_state(m.st)['uidvalidity']) != str(epoch):
            return
        row['acked'] = True
        save(m, d)
        m.log('mark_read', result='ok', id=mid)


def historical(m, cfg, password):
    """Read-only matching: legacy notice receipts only; seen[] is a bound, never a conclusion."""
    from . import mail as M
    s = M.read_state(m.st)
    d = load(m, cfg)
    if d.get('legacy_scanned'):
        return []
    ids = set()
    baseline = False
    try:
        # A legacy baseline has no account digest. Any later config write makes its scope unprovable.
        changed = (M.root(m.st) / M.CONFIG).stat().st_mtime
        with m.st.log_path.open('rb') as f:
            for line in f:
                with contextlib.suppress(ValueError, TypeError):
                    r = json.loads(line)
                    if r.get('ev') == 'mail_baseline':
                        ids.clear()
                        baseline = isinstance(r.get('ts'), (int, float)) and r['ts'] + 1 >= changed
                    if baseline and r.get('ev') == 'mail_notice' and r.get('id') in s['seen'] and re.fullmatch('[0-9a-f]{32}', r.get('id', '')):
                        ids.add(r['id'])
    except OSError:
        pass
    ids -= set(d['rows'])
    if not ids or not s['last_uid'] or s['uidvalidity'] is None:
        d['legacy_scanned'] = True
        save(m, d)
        return []
    c = M.imap_login(cfg, password)
    try:
        if c.select('INBOX', readonly=True)[0] != 'OK':
            raise M.MailError('imap_select')
        epoch = c.untagged_responses.get('UIDVALIDITY', [b'0'])[-1].decode('ascii', 'replace')
        if epoch != str(s['uidvalidity']):
            return []
        typ, data = c.uid('SEARCH', None, 'ALL')
        if typ != 'OK':
            raise M.MailError('imap')
        todo = [int(x) for x in b' '.join(x for x in data if isinstance(x, bytes)).split() if x.isdigit()]
        out = []
        for uid in [u for u in todo if u <= s['last_uid']][-1000:]:
            typ, data = c.uid('FETCH', str(uid), '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)] BODY.PEEK[]<0.2048>)')
            fields = {}
            for x in data or []:
                if typ == 'OK' and isinstance(x, tuple) and isinstance(x[1], bytes):
                    key = 'header' if b'BODY[HEADER.FIELDS (MESSAGE-ID)]' in x[0].upper() else 'body' if b'BODY[]<0>' in x[0].upper() else None
                    if key: fields[key] = x[1]
            if set(fields) == {'header', 'body'}:
                mid = M.id_hash(M.msg_id(email.message_from_bytes(fields['header'], policy=email.policy.default)), fields['body'][:2048])
                if mid in ids:
                    out.append((epoch, uid, mid))
        return out
    finally:
        with contextlib.suppress(Exception):
            c.logout()


async def reconcile(m, cfg, password):
    from . import mail as M
    import asyncio
    if M.read_config(m.st) != cfg:
        return
    for mid, row in list(load(m, cfg)['rows'].items()):
        if not row['acked']:
            await completed(m, cfg, row['epoch'], row['uid'], mid)
    for epoch, uid, mid in await asyncio.to_thread(historical, m, cfg, password):
        await completed(m, cfg, epoch, uid, mid)
    if M.read_config(m.st) == cfg:
        d = load(m, cfg)
        d['legacy_scanned'] = True
        save(m, d)
