from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import re
import secrets
import time

from . import approvals, contacts, codex_procs, passkey, provenance, wire

TOOL = 'agentj mail workflow-grant'
CAPABILITY = 'workflow-owner-mail-v1'
PURPOSE = 'artifact_delivery'
FIXED_PURPOSE = 'fixed_recipient_artifact_delivery'
FIXED_CAPABILITY = 'workflow-fixed-recipient-mail-v2'
ROLES = ['captions', 'cover', 'materials']
MAX_BYTES = 25 * 1024 * 1024
STATS = ('last_sent_at', 'send_count', 'updated', 'nicks')
CARD_TTL, WAIT, COOLDOWN = 1800, 600, 6 * 3600
RENEW = ('contact_changed', 'account_changed', 'contract_changed', 'expired', 'proof_invalid')
HEX = re.compile(r'[0-9a-f]{64}')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
IMG = ('.jpg', '.jpeg', '.png', '.webp')
KINDS = ('.txt', '.md', '.json', '.srt', '.vtt', '.csv') + IMG
REFUSE = ('manifest recipient binding format content content_hash files attachment path attachment_hash artifact_secret artifact_type '
             'archive archive_size archive_path archive_type archive_secret archive_image').split()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def ident(rec):
    return digest({k: v for k, v in rec.items() if k not in STATS})


def same(old, rec):
    return bool(rec) and (old == ident(rec) or any(old == digest({**rec, **x}) for x in
                          [{}, {'last_sent_at': None, 'send_count': 0}] + [{'last_sent_at': (rec.get('created') or 0) - d, 'send_count': 1} for d in (0, 1)]))


def drift(st, core):
    book, now, b = contacts.read(st), owner_binding(st), core['binding']
    if set(b) != set(now) or b['host'] != now['host'] or b['account_sha256'] != now['account_sha256']:
        return 'account_changed'
    if b['primary'] != now['primary'] or not same(b['owner_sha256'], contacts.owner(book)):
        return 'contact_changed'
    for x in core.get('cc_contacts') or []:
        rec = contacts.get(book, x['id'])
        em = (rec or {}).get('emails') or [{}]
        if x['id'] == contacts.OWNER_ID or contacts.norm_email(em[0].get('v')) != x['address'] or x['address'] == now['primary'] or not same(x['sha256'], rec):
            return 'contact_changed'
    return None


def card_key(c):
    return digest([c.get(k) for k in ('contract', 'artifact_root', 'binding', 'cc_contacts', 'purpose', 'max', 'per_day')])


def pin_current(proc):
    scope = provenance.task_scope()
    if scope is not None and scope.get('live') and scope.get('mail_contract'):
        token = codex_procs.start_token(proc.pid)
        if token:
            scope.setdefault('mail_roots', []).append((proc, token))


def descendant(pid, root, token):
    if type(pid) is not int or pid <= 1 or codex_procs.start_token(root) != token:
        return False
    seen = set()
    for _ in range(128):
        if pid in seen or pid <= 1:
            return False
        seen.add(pid)
        try:
            if os.stat(f'/proc/{pid}').st_uid != os.getuid():
                return False
        except OSError:
            return False
        if pid == root:
            return codex_procs.start_token(root) == token
        pid = codex_procs.parent(pid)
        if pid is None:
            return False
    return False


def task_contract(e):
    return {'workflow_id': e['id'], 'root': str(pathlib.Path(e['dir']).resolve()), 'tsha': e['tsha']}


def source(host, peer):
    scheduler = getattr(host, 'scheduler', None)
    scope = getattr(scheduler, 'mail_scope', None)
    if not scope or not scope.get('live') or host.stopped():
        return None
    if not any(p.returncode is None and descendant(peer, p.pid, tok) for p, tok in scope.get('mail_roots', [])):
        return None
    from . import tasks
    try:
        e = tasks.find(scheduler.workdir(), scope['mail_contract']['workflow_id'])
        a = scope.get('mail_admission') or {}
        journal = tasks.load(host.st)
        run = journal['runs'].get(a.get('run_id')) or {}
        on = journal['on'].get(e['id']) or {}
        if (e['problems'] or task_contract(e) != scope['mail_contract'] or a.get('research') is not False
                or e.get('task', {}).get('mode') == 'research' or run.get('status') != 'running'
                or run.get('id') != e['id'] or run.get('tsha') != e['tsha']
                or run.get('instance_id') != a.get('instance_id')
                or a.get('scheduled') and on.get('tsha') != e['tsha']):
            return None
    except (tasks.TaskError, KeyError, OSError):
        return None
    return dict(scope['mail_contract'])


def owner_binding(st):
    own = contacts.owner(contacts.read(st))
    emails = (own or {}).get('emails') or []
    address = contacts.norm_email(emails[0].get('v')) if emails else None
    if not address:
        raise ValueError('no_owner_address')
    from . import cloud
    linked = cloud.read_cloud(st)
    account = {k: linked.get(k) for k in ('api', 'host_id', 'linked_at')} if linked else None
    if account is not None:
        account['tenant'] = linked['tenant']['slug']
    return {'primary': address, 'owner_sha256': ident(own), 'host': st.config()['channel'],
            'account_sha256': digest(account)}


def cc_binding(st, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('cc_contacts')
    book = contacts.read(st)
    primary = owner_binding(st)['primary']
    rows = []
    for cid in sorted(ids):
        rec = contacts.get(book, cid)
        emails = (rec or {}).get('emails') or []
        address = contacts.norm_email(emails[0].get('v')) if emails else ''
        if not rec or cid == contacts.OWNER_ID or not address or address == primary or address in [x['address'] for x in rows]:
            raise ValueError('cc_contacts')
        rows.append({'id': cid, 'address': address, 'sha256': ident(rec)})
    return rows


def fixed_core(core):
    return core.get('v') == 2 and core.get('purpose') == FIXED_PURPOSE


def shown(core):
    from datetime import datetime, timezone
    c, bind = core['contract'], core['binding']
    until = datetime.fromtimestamp(core['expires'], timezone.utc).isoformat()
    if core.get('lang') == 'en':
        lines = ['Authorize workflow artifact delivery', f"Workflow: {c['workflow_id']}", f"Task folder: {c['root']}", f"Artifact folder: {core['artifact_root']}",
                 f"Your primary mailbox: {bind['primary']}", 'Purpose: deliver published artifacts to yourself',
                 f"Expires: {until}", f"Limits: {core['max']} total, {core['per_day']} per UTC day",
                 f"Attachments: at most {core['max_files']} files / {core['max_bytes'] // (1024*1024)} MiB per message",
                 'Allowed attachments: cover, publication materials, captions',
                 'Owner, mailbox, account, workflow or signing-key changes invalidate this grant.',
                 'Each message and attachment is hash-bound. A send with an uncertain outcome is never retried.',
                 'Revoke: agentj mail workflow-revoke --workflow ' + c['workflow_id']]
    else:
        lines = ['授权工作流交付素材', f"工作流：{c['workflow_id']}", f"任务文件夹：{c['root']}", f"产物文件夹：{core['artifact_root']}",
                 f"本人主邮箱：{bind['primary']}", '用途：向本人交付已发布产物', f"到期时间：{until}",
                 f"次数：总计 {core['max']} 次，每个 UTC 日最多 {core['per_day']} 次",
                 f"附件：每封最多 {core['max_files']} 个文件，共 {core['max_bytes'] // (1024*1024)} MiB",
                 '附件种类：封面、发布素材包、字幕', '主人、邮箱、账号、工作流或签名密钥变更后失效。',
                 '每封邮件和附件都绑定哈希；发送结果不确定时不会重发。',
                 '撤销：agentj mail workflow-revoke --workflow ' + c['workflow_id']]
    if fixed_core(core):
        lines[0] = 'Authorize fixed-recipient workflow delivery' if core.get('lang') == 'en' else '授权工作流向固定收件人交付素材'
        lines = [x for x in lines if x not in ('Purpose: deliver published artifacts to yourself', '用途：向本人交付已发布产物')]
        lines += [('Purpose: artifact delivery to this fixed To/Cc list' if core.get('lang') == 'en' else '用途：向此固定收件人与抄送名单交付素材'),
                  'Cc: ' + ', '.join(x['address'] for x in core['cc_contacts']),
                  ('Recipient/contact changes require a new approval.' if core.get('lang') == 'en' else '收件人或联系人记录变更须重新授权。')]
    if core.get('renew'):
        lines.append(('Renewal reason: ' if core.get('lang') == 'en' else '重新授权原因：') + NOTE[core.get('lang') == 'en'][core['renew']])
    return '\n'.join(lines + ['Grant: ' + core['id'], 'SHA256: ' + digest(core)])


NOTE = ({'contact_changed': '联系人记录变化（旧版误把发信计数算入，已修复）', 'account_changed': '账号变化', 'contract_changed': '任务定义变化',
         'expired': '已到期', 'proof_invalid': '遥控器或通行密钥变化', 'grant_invalid': '记录无效', 'never_granted': '未授权：任务运行中执行 workflow-grant',
         'revoked': '主人已撤销', 'sent': '已自动发授权卡，批准即生效', 'pending': '授权卡等待批准', 'cooldown': '上张卡未获批准，card_retry_at 后的运行自动重发',
         'unverified': '只读查询，不发卡', 'unavailable': '联系人或产物文件夹不可用，需修正'},
        {'contact_changed': 'contact changed (send counts no longer count)', 'account_changed': 'account changed', 'contract_changed': 'task changed',
         'expired': 'expired', 'proof_invalid': 'remote or passkey changed', 'grant_invalid': 'invalid record', 'never_granted': 'not granted: run workflow-grant in the task run',
         'revoked': 'revoked by owner', 'sent': 'grant card sent automatically', 'pending': 'grant card awaiting approval', 'cooldown': 'card not approved; a run after card_retry_at re-sends',
         'unverified': 'read-only query, no card', 'unavailable': 'contact or artifact folder unavailable: fix it'})


def proof_ok(st, core, proof):
    try:
        if proof['summary'] != shown(core) or proof['channel'] != core['host']:
            return False
        sk = st.sign_key(proof['device'])
        pk = st.passkey_of_device(proof['device'])
        if not sk or not pk or proof['kind'] not in ('sandbox', 'perm'):
            return False
        d = approvals.shown_digest(TOOL, proof['summary'])
        if not approvals.verify(sk, wire.unb64u(proof['sig']), proof['channel'], proof['device'], proof['rid'], 'allow', d):
            return False
        f = passkey.approval_fields(proof['fa'])
        if f['id'] != pk['id']:
            return False
        ch = passkey.elevate_challenge(proof['channel'], proof['device'], proof['rid'], proof['kind'], proof['rid'], d)
        # Historical proof: do not advance the live counter.
        passkey.verify_assertion({k: pk.get(k) for k in ('alg', 'spki', 'rp')}, f, ch)
        return True
    except (KeyError, TypeError, ValueError, passkey.PasskeyError):
        return False


def checked_artifact_root(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or wire.text_problem(value, 4096):
        raise ValueError('artifact_root')
    p = pathlib.Path(value).expanduser().resolve(strict=True)
    home = pathlib.Path.home().resolve()
    if not p.is_dir() or p == home or home.is_relative_to(p):
        raise ValueError('artifact_root')
    if any(part.lower() in ('.ssh', '.gnupg', '.aws', '.config', '.local', 'credentials') for part in p.parts):
        raise ValueError('artifact_root')
    return str(p)


def secret_content(text):
    import html
    from . import privacy
    text = html.unescape(text)
    if privacy.credentials(text):
        return True
    try:
        decoded = json.loads(text)
        return bool(privacy.credentials(canonical(decoded).decode()))
    except (ValueError, TypeError):
        return False
    except RecursionError:
        return True


def image(ext, data):
    return (data.startswith(b'\xff\xd8\xff') if ext in IMG[:2] else data.startswith(b'\x89PNG\r\n\x1a\n') if ext == '.png' else
            data.startswith(b'RIFF') and data[8:12] == b'WEBP')


def check_archive(name, blob):
    import io
    import zipfile
    ext = pathlib.Path(name).suffix.lower()
    if ext not in KINDS + ('.zip',):
        raise ValueError('artifact_type')
    if ext in IMG:
        if not image(ext, blob):
            raise ValueError('artifact_type')
    elif ext != '.zip' and secret_content(blob.decode('utf-8', 'replace')):
        raise ValueError('artifact_secret')
    if ext != '.zip' and not zipfile.is_zipfile(io.BytesIO(blob)):
        return
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            items = z.infolist()
            if len(items) > 100 or sum(i.file_size for i in items) > MAX_BYTES:
                raise ValueError('archive_size')
            if secret_content('\n'.join([z.comment.decode('utf-8', 'replace')] + z.namelist())):
                raise ValueError('archive_secret')
            for i in items:
                p = pathlib.PurePosixPath(i.filename)
                if i.flag_bits & 1 or p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts):
                    raise ValueError('archive_path')
                if i.is_dir():
                    continue
                suffix = p.suffix.lower()
                if suffix not in KINDS:
                    raise ValueError('archive_type')
                with z.open(i) as member:
                    data = member.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError('archive_size')
                if suffix in IMG:
                    if not image(suffix, data) or zipfile.is_zipfile(io.BytesIO(data)):
                        raise ValueError('archive_image')
                elif secret_content(data.decode('utf-8', 'replace')):
                    raise ValueError('archive_secret')
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, NotImplementedError) as exc:
        raise ValueError('archive') from exc


class Service:
    def __init__(self, contact_service):
        self.contacts = contact_service
        self.host, self.st = contact_service.host, contact_service.st
        self.lock = asyncio.Lock()
        self.pending = {}

    def load(self):
        p = self.st.root / 'workflow-mail.json'
        try:
            d = json.loads(p.read_bytes())
        except FileNotFoundError:
            return {'v': 1, 'grants': {}, 'deliveries': {}, 'epochs': {}}
        if not isinstance(d, dict) or d.get('v') != 1:
            raise ValueError('ledger')
        return d

    def save(self, d):
        self.st.write_private(self.st.root / 'workflow-mail.json', canonical(d))

    def why(self, row, contract):
        if not row:
            return 'never_granted'
        if row.get('revoked'):
            return 'revoked'
        c = row.get('core') or {}
        try:
            if not ((c.get('v') == 1 and c['purpose'] == PURPOSE and 'cc_contacts' not in c) or fixed_core(c)):
                return 'grant_invalid'
            if contract is None or c['contract'] != contract:
                return 'contract_changed'
            if c['expires'] <= time.time():
                return 'expired'
            return drift(self.st, c) or (None if proof_ok(self.st, c, row['proof']) else 'proof_invalid')
        except (KeyError, TypeError, ValueError):
            return 'grant_invalid'

    def live(self, row, contract):
        return self.why(row, contract) is None

    def make(self, contract, artifact_root, cc_ids, days, count, daily, renew=None):
        cc_rows = cc_binding(self.st, cc_ids) if cc_ids else None
        binding, now = owner_binding(self.st), int(time.time())
        core = {'artifact_root': checked_artifact_root(artifact_root), 'v': 1, 'id': secrets.token_hex(16), 'host': binding['host'], 'binding': binding,
                'contract': contract, 'purpose': PURPOSE, 'manifest': 1, 'roles': ROLES, 'issued': now, 'expires': now + days * 86400,
                'max': count, 'per_day': daily, 'max_bytes': MAX_BYTES, 'max_files': 10, 'lang': 'en' if self.contacts.lang() == 'en' else 'zh'}
        if cc_rows is not None:
            core.update(v=2, purpose=FIXED_PURPOSE, manifest=2, cc_contacts=cc_rows)
        if renew:
            core['renew'] = renew
        return core

    def offer(self, data, wid, core):
        p = self.pending.get(wid)
        if p and not p.done():
            return p, False
        data.setdefault('cards', {})[wid] = {'at': int(time.time()), 'key': card_key(core)}
        self.pending[wid] = asyncio.create_task(self.card(wid, core, data.get('epochs', {}).get(wid, 0)))
        return self.pending[wid], True

    async def card(self, wid, core, epoch):
        from . import policy_registry, tasks
        ans = await self.host.ask(TOOL, {'summary': shown(core)}, batch=False, grant_info=True, always_ask=True,
                                  require_passkey=True, ttl=CARD_TTL, _company_proposal=policy_registry.HOST_CARD)
        proof = {k: v for k, v in (ans.get('grant_info') or {}).items() if k in
                 ('rid', 'device', 'sig', 'fa', 'summary', 'kind', 'channel')}
        async with self.lock:
            try:
                e = await asyncio.to_thread(tasks.find, self.host.scheduler.workdir(), wid)
                if (ans.get('behavior') != 'allow' or self.host.stopped() or e['problems'] or task_contract(e) != core['contract']
                        or await asyncio.to_thread(drift, self.st, core)
                        or not await asyncio.to_thread(proof_ok, self.st, core, proof)):
                    return {'ok': False, 'why': 'grant_required'}
                data = await asyncio.to_thread(self.load)
                if data.get('epochs', {}).get(wid, 0) != epoch:
                    return {'ok': False, 'why': 'grant_stale'}
                data['grants'][wid] = {'core': core, 'proof': proof}
                await asyncio.to_thread(self.save, data)
            except (tasks.TaskError, KeyError, TypeError, ValueError, OSError):
                return {'ok': False, 'why': 'grant_required'}
            self.st.log('workflow_mail_grant', task=wid, id=core['id'], sha=digest(core), device=proof['device'])
            return {'ok': True, 'workflow_id': wid, 'grant': {'status': 'active', 'expires': core['expires']}}

    async def control(self, req, peer):
        op, wid = req.get('op'), req.get('workflow_id')
        if not isinstance(wid, str) or not ID.fullmatch(wid):
            return {'ok': False, 'why': 'shape'}
        async with self.lock:
            try:
                data = await asyncio.to_thread(self.load)
                if op == 'revoke':
                    row = data['grants'].get(wid)
                    if row:
                        row['revoked'] = True
                    data.setdefault('epochs', {})[wid] = data.get('epochs', {}).get(wid, 0) + 1
                    await asyncio.to_thread(self.save, data)
                    self.st.log('workflow_mail_revoke', task=wid, generation=data['epochs'][wid])
                    return {'ok': True, 'workflow_id': wid, 'revoked': True}
                from . import tasks
                try:
                    e = await asyncio.to_thread(tasks.find, self.host.scheduler.workdir(), wid)
                    contract = task_contract(e) if not e['problems'] else None
                except tasks.TaskError:
                    contract = None
                row = data['grants'].get(wid)
                if op == 'status':
                    why = await asyncio.to_thread(self.why, row, contract)
                    active, verified, card, retry = why is None, contract is not None and await asyncio.to_thread(source, self.host, peer) == contract, None, None
                    if not active and why in RENEW and not verified:
                        card = 'unverified'
                    elif not active and why in RENEW:
                        c, last = row['core'], data.get('cards', {}).get(wid) or {}
                        try:
                            core = await asyncio.to_thread(self.make, contract, c['artifact_root'], [x['id'] for x in c.get('cc_contacts', [])],
                                                           max(1, min(30, (c['expires'] - c['issued']) // 86400)), c['max'], c['per_day'], why)
                        except (ValueError, OSError):
                            card = 'unavailable'
                        else:
                            busy = self.pending.get(wid) and not self.pending[wid].done()
                            if not busy and last.get('key') == card_key(core) and time.time() < last.get('at', 0) + COOLDOWN:
                                card, retry = 'cooldown', last['at'] + COOLDOWN
                            else:
                                card = 'sent' if self.offer(data, wid, core)[1] else 'pending'
                                await asyncio.to_thread(self.save, data)
                    used = [x for x in data['deliveries'].values() if row and x['grant'] == row['core']['id']]
                    remaining = max(0, row['core']['max'] - len(used)) if row else 0
                    today = sum(x['day'] == int(time.time()) // 86400 for x in used)
                    available = active and remaining > 0 and today < row['core']['per_day']
                    return {'ok': True, 'capability': CAPABILITY, 'capabilities': [CAPABILITY, FIXED_CAPABILITY], 'workflow_id': wid,
                            'source_verified': verified,
                            'grant': {'capability': (FIXED_CAPABILITY if fixed_core(row['core']) else CAPABILITY) if row else None,
                                      'cc_contact_ids': [x['id'] for x in row['core'].get('cc_contacts', [])] if row else [],
                                      'status': 'active' if available else 'exhausted' if active else 'inactive',
                                      'remaining': remaining, 'reason': why, 'card': card, 'card_retry_at': retry,
                                      'note': None if active else ' — '.join(NOTE[self.contacts.lang() == 'en'].get(x, x) for x in (why, card) if x),
                                      'expires': ((row or {}).get('core') or {}).get('expires')}}
                verified = await asyncio.to_thread(source, self.host, peer)
                if op != 'grant' or verified != contract or contract is None:
                    return {'ok': False, 'why': 'workflow_source'}
                raw_root = req.get('artifact_root')
                cc_ids = req.get('cc_contacts') or []
                days, count, daily = req.get('days', 30), req.get('max', 100), req.get('per_day', 3)
                if not all(type(x) is int and 1 <= x <= top for x, top in ((days, 30), (count, 100), (daily, 3))):
                    return {'ok': False, 'why': 'shape'}
                core = await asyncio.to_thread(self.make, contract, contract['root'] if raw_root is None else raw_root, cc_ids, days, count, daily)
                if (await asyncio.to_thread(self.live, row, contract) and [x['id'] for x in row['core'].get('cc_contacts', [])] == sorted(cc_ids)
                        and row['core']['artifact_root'] == core['artifact_root']):
                    return {'ok': True, 'workflow_id': wid, 'grant': {'status': 'active'}, 'unchanged': True}
                p, new = self.offer(data, wid, core)
                if not new:
                    return {'ok': False, 'why': 'card_pending', 'grant': {'status': 'inactive', 'card': 'pending'}}
                await asyncio.to_thread(self.save, data)
            except (ValueError, OSError):
                return {'ok': False, 'why': 'grant_stale'}
        # Revoke stays live while awaiting the card; the card outlives this call.
        done, _ = await asyncio.wait({p}, timeout=WAIT)
        return p.result() if p in done else {'ok': False, 'why': 'card_pending', 'grant': {'status': 'inactive', 'card': 'pending'}}

    async def send(self, req, peer):
        async with self.lock:
            attempted = False
            try:
                contract = await asyncio.to_thread(source, self.host, peer)
                if contract is None:
                    return {'ok': False, 'why': 'workflow_source', 'provider_acceptance': False}
                data = await asyncio.to_thread(self.load)
                row = data['grants'].get(contract['workflow_id'])
                why = await asyncio.to_thread(self.why, row, contract)
                if why:
                    return {'ok': False, 'why': 'grant_required', 'reason': why, 'provider_acceptance': False}
                manifest, files = await asyncio.to_thread(self.snapshot, req, contract, row['core']['artifact_root'], row['core'])
                key = digest({'host': row['core']['host'], 'workflow_id': contract['workflow_id'],
                              'root': contract['root'], 'delivery': manifest['idempotency_key']})
                if key in data['deliveries']:
                    return {'ok': False, 'why': 'duplicate', 'status': data['deliveries'][key]['status'],
                            'provider_acceptance': data['deliveries'][key].get('provider_acceptance')}
                c = row['core']
                used = [x for x in data['deliveries'].values() if x['grant'] == c['id']]
                if len(used) >= c['max'] or sum(x['day'] == int(time.time()) // 86400 for x in used) >= c['per_day']:
                    return {'ok': False, 'why': 'limit', 'provider_acceptance': False}
                cfg, pw, why = self.contacts.mail_state()
                if why:
                    return {'ok': False, 'why': why, 'provider_acceptance': False}
                # Persist before SMTP; uncertain results cannot retry.
                rec = {'grant': c['id'], 'manifest_sha256': digest(manifest), 'day': int(time.time()) // 86400,
                       'status': 'reserved', 'provider_acceptance': None}
                data['deliveries'][key] = rec
                await asyncio.to_thread(self.save, data)
                from . import mail, mail_template
                text, page, _ = mail_template.render_send(lang=self.contacts.lang(), agent=self.contacts.agent_name(),
                    subject=req['subject'], body=req['body'], fmt=req.get('format') or 'markdown', text_fn=None)
                recipient = c['binding']['primary']
                cc = [x['address'] for x in c['cc_contacts']] if fixed_core(c) else []
                recipients = [recipient] + cc
                msg = self.contacts.build(cfg, [recipient], req['subject'], text, files, kind='agent', html=page, cc=cc)
                # Recheck authority after all awaits before SMTP.
                if (await asyncio.to_thread(source, self.host, peer) != contract
                        or not await asyncio.to_thread(self.live, row, contract) or mail.read_config(self.st) != cfg):
                    rec['status'] = 'blocked'
                    rec['provider_acceptance'] = False
                    await asyncio.to_thread(self.save, data)
                    return {'ok': False, 'why': 'grant_stale', 'provider_acceptance': False}
                try:
                    attempted = True
                    await asyncio.to_thread(self.contacts.smtp_send, cfg, pw, msg, recipients)
                except Exception:
                    rec['status'] = 'unknown'
                    await asyncio.to_thread(self.save, data)
                    self.st.log('workflow_mail_send', task=contract['workflow_id'], id=c['id'], sha=rec['manifest_sha256'], result='unknown')
                    return {'ok': False, 'why': 'send_unknown', 'provider_acceptance': None}
                rec.update(status='sent', provider_acceptance=True)
                await asyncio.to_thread(self.save, data)
                self.st.log('workflow_mail_send', task=contract['workflow_id'], id=c['id'], sha=rec['manifest_sha256'], result='sent')
                return {'ok': True, 'provider_acceptance': True, 'sent': recipients, 'manifest_sha256': rec['manifest_sha256'],
                        'no_card': True, 'owner_direct': False, 'workflow_grant': True, 'workflow_id': contract['workflow_id']}
            except (ValueError, KeyError, TypeError, OSError) as e:
                w = (e.args or [0])[0]
                w = w if isinstance(e, ValueError) and w in REFUSE else 'file_unreadable' if isinstance(e, OSError) else 'request_invalid'
                return {'ok': False, 'why': 'send_unknown' if attempted else w,
                        'provider_acceptance': None if attempted else False}

    def snapshot(self, req, contract, artifact_root=None, core=None):
        from . import media, tg_media, privacy
        m = req.get('delivery_manifest')
        keys = {'v', 'workflow_id', 'purpose', 'idempotency_key', 'binding_sha256', 'subject_sha256', 'body_sha256', 'format', 'attachments'}
        fixed = core is not None and fixed_core(core)
        cc = [x['address'] for x in core['cc_contacts']] if fixed else []
        if fixed:
            keys.add('cc')
        if not isinstance(m, dict) or set(m) != keys or type(m['v']) is not int or m['v'] != (2 if fixed else 1) or m['workflow_id'] != contract['workflow_id'] or m['purpose'] != (FIXED_PURPOSE if fixed else PURPOSE):
            raise ValueError('manifest')
        if req.get('to') or (req.get('cc') or []) != cc or req.get('bcc') or req.get('to_owner') is not True or (fixed and m['cc'] != cc):
            raise ValueError('recipient')
        if not isinstance(m['idempotency_key'], str) or not ID.fullmatch(m['idempotency_key']) or not HEX.fullmatch(m['binding_sha256']):
            raise ValueError('binding')
        if m['format'] not in ('text', 'html', 'markdown') or m['format'] != req.get('format'):
            raise ValueError('format')
        for field in ('subject', 'body'):
            text = req.get(field)
            if not isinstance(text, str) or len(text.encode()) > (200 if field == 'subject' else 1000000) or secret_content(text):
                raise ValueError('content')
            if hashlib.sha256(text.encode()).hexdigest() != m[field + '_sha256']:
                raise ValueError('content_hash')
        att = m['attachments']
        paths = req.get('files') or []
        if not isinstance(att, list) or not 0 <= len(att) <= 10 or [a['path'] for a in att] != paths or len(set(paths)) != len(paths):
            raise ValueError('files')
        root = artifact_root or contract['root']
        checker = media.Media(self.st, root)
        files, total = [], 0
        for a in att:
            if set(a) != {'path', 'sha256', 'size', 'role'} or a['role'] not in ROLES or type(a['size']) is not int:
                raise ValueError('attachment')
            p = pathlib.Path(a['path'])
            if not p.is_absolute() or not p.resolve().is_relative_to(pathlib.Path(root)):
                raise ValueError('path')
            rec, why = checker.check(media.Ref(str(p), str(p), True), False)
            if why or not rec:
                raise ValueError('attachment')
            blob = tg_media.read_checked(rec)
            total += len(blob)
            if total > MAX_BYTES or len(blob) != a['size'] or hashlib.sha256(blob).hexdigest() != a['sha256']:
                raise ValueError('attachment_hash')
            check_archive(rec['name'], blob)
            files.append((rec['name'], rec['mime'], blob))
        return m, files
