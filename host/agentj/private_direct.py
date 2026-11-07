"""#578 one-device claims; file bytes use HTTPS, never the app/Noise stream."""
import asyncio
import copy
import math
import json
import os
import re
import secrets
import stat
from . import media, private_cipher as cipher, wire

_ID = re.compile(r'[0-9a-f]{32}')
MAX_ACTIVE = 2


def unchanged(rec, fd):
    s = os.fstat(fd)
    return (stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid()
            and (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            == (rec['dev'], rec['ino'], rec['bytes'], rec['mtime'], rec['ctime']))


def source(rec, index=None):
    fd = media.open_rel(rec['root'], rec['parts'])
    try:
        if not unchanged(rec, fd):
            raise media.Gone('changed')
        data = None if index is None else os.pread(fd, cipher.CHUNK, index * cipher.CHUNK)
        if not unchanged(rec, fd) or (index is not None and len(data) != min(cipher.CHUNK, rec['bytes'] - index * cipher.CHUNK)):
            raise media.Gone('changed')
        return data
    finally:
        os.close(fd)


class Direct:
    def __init__(self, downloads):
        self.downloads = downloads
        self.host = downloads.host
        self.claims = {}
        self.deliveries = {}
        self.cleanups = set()
        self.upload = self._upload

    async def send_key(self, session, message, fid, row):
        """Register only our actual ready response; no wire field grants delivery authority."""
        claim = self.claims.get(fid)
        if not claim or not claim.get('ready') or claim['session'] is not session:
            return False
        ticket = (message, session, fid, row, claim, claim['key'], claim['signer'], claim['transfer'], claim['nonce_prefix'], copy.deepcopy(row['rec']))
        self.deliveries[id(message)] = ticket
        try:
            return await self.host.send_app(session, message)
        finally:
            if self.deliveries.get(id(message)) is ticket:
                self.deliveries.pop(id(message), None)

    async def delivery_live(self, session, message):
        ticket = self.deliveries.get(id(message))
        if ticket is None or ticket[0] is not message or ticket[1] is not session:
            return False
        _, _, fid, row, claim, key, signer, transfer, prefix, rec = ticket
        def live():
            return (self.host.private_files is self.downloads and self.downloads.direct is self
                    and self.deliveries.get(id(message)) is ticket and self.claims.get(fid) is claim
                    and row['rec'] == rec and claim.get('ready') is True and claim['session'] is session and claim['key'] == key
                    and claim['signer'] == signer and claim['transfer'] == transfer and claim['nonce_prefix'] == prefix
                    and message.get('key') == wire.b64u(key)
                    and message.get('nonce_prefix') == wire.b64u(prefix)
                    and self.valid(fid, row, claim))
        if not live():
            return False
        try:
            await asyncio.to_thread(source, rec)
        except (OSError, media.Gone):
            return False
        return live()  # no await between this check and the caller's encryption

    def prune(self):
        for fid in list(self.claims):
            if fid in self.downloads.offers:
                continue
            claim = self.claims.pop(fid)
            task = claim.get('task')
            if task is not None and not task.done():
                task.get_loop().call_soon_threadsafe(task.cancel)
            claim['key'] = b''
            claim['nonce_prefix'] = b''

    def authority(self, row):
        refusal = getattr(self.host, 'seat_refusal', None)
        return (getattr(self.host, 'identity_ok', False) is True and callable(refusal) and not refusal()
                and not getattr(self.host, '_unbinding_all', False) and self.host.st is row['host_state']
                and getattr(self.host, '_pairing_epoch', 0) == row['host_epoch'])

    def valid(self, fid, row, claim):
        return (self.authority(row) and self.downloads.offers.get(fid) is row and self.downloads.live(claim['session'], row)
                and self.host.st.sign_key(claim['session'].device) == claim['signer'])

    def legacy_claim(self, session, fid, row, transfer):
        """Old signed offset clients reserve the same one-device offer, never an HTTPS bypass."""
        if not self.authority(row):
            return 'gone'
        if not isinstance(transfer, str) or not _ID.fullmatch(transfer):
            return 'shape'
        with self.downloads.lock:
            claim = self.claims.get(fid)
            if claim is None:
                if len(self.claims) >= MAX_ACTIVE:
                    return 'busy'
                signer = self.host.st.sign_key(session.device)
                if not isinstance(signer, bytes) or len(signer) != 32:
                    return 'unpaired'
                claim = {'session': session, 'signer': signer, 'transfer': transfer,
                         'mode': 'legacy', 'key': b'', 'nonce_prefix': b'',
                         'ready': False, 'failed': False, 'uploaded': 0, 'task': None}
                self.claims[fid] = claim
            if (claim.get('mode') != 'legacy' or claim['session'] is not session
                    or claim['transfer'] != transfer or not self.valid(fid, row, claim)):
                return 'claimed'
        return None

    async def claim(self, session, fid, row, req):
        if not self.authority(row):
            return {'ok': False, 'why': 'gone'}
        transfer = req.get('transfer')
        if not isinstance(transfer, str) or not _ID.fullmatch(transfer):
            return {'ok': False, 'why': 'shape'}
        # No await before the reservation: two paired claimants cannot both win.
        with self.downloads.lock:
            self.claims = {k: v for k, v in self.claims.items() if k in self.downloads.offers}
            claim = self.claims.get(fid)
            if claim is None:
                if len(self.claims) >= MAX_ACTIVE:
                    return {'ok': False, 'why': 'busy'}
                signer = self.host.st.sign_key(session.device)
                if not isinstance(signer, bytes) or len(signer) != 32:
                    return {'ok': False, 'why': 'unpaired'}
                claim = {'session': session, 'signer': signer, 'transfer': transfer,
                         'key': secrets.token_bytes(32), 'nonce_prefix': secrets.token_bytes(8),
                         'mode': 'https', 'ready': False, 'failed': False, 'uploaded': 0, 'task': None}
                self.claims[fid] = claim
            if claim.get('mode') != 'https':
                return {'ok': False, 'why': 'claimed'}
            # A reconnected Noise session needs a fresh signed claim, same paired
            # device/signing key/transfer; another device can never take it over.
            if (claim['session'] is not session and claim['session'].device == session.device
                    and claim['transfer'] == transfer and self.host.st.sign_key(session.device) == claim['signer']
                    and self.downloads.live(session, row)):
                claim['session'] = session
            if (claim['session'] is not session or claim['transfer'] != transfer
                    or not self.valid(fid, row, claim)):
                return {'ok': False, 'why': 'claimed'}
            if req.get('complete') is True:
                if not claim['ready']:
                    return {'ok': False, 'why': 'not_ready'}
                self.downloads.offers.pop(fid, None)
                self.downloads.prune()
                cleanup = asyncio.create_task(self._delete(fid))
                self.cleanups.add(cleanup)
                cleanup.add_done_callback(self.cleanups.discard)
                return {'ok': True, 'complete': True}
            if claim['failed'] and req.get('retry') is not True:
                return {'ok': False, 'why': 'failed', 'retryable': True}
            if claim['task'] is None or (claim['failed'] and req.get('retry') is True):
                claim['failed'] = False
                claim['task'] = asyncio.create_task(self._prepare(fid, row, claim))
        if not claim['ready']:
            return {'ok': True, 'pending': True, 'uploaded': claim['uploaded'], 'bytes': row['rec']['bytes']}
        return {'ok': True, 'pending': False, 'direct': True, 'total': row['rec']['bytes'],
                'chunk_size': cipher.CHUNK, 'chunks': math.ceil(row['rec']['bytes'] / cipher.CHUNK), 'card': self.downloads.card(fid, row),
                'key': wire.b64u(claim['key']), 'nonce_prefix': wire.b64u(claim['nonce_prefix']),
                'endpoint': claim['endpoint'], 'transfer': transfer,
                'grant': {'v': 1, 'offer': fid, 'transfer': transfer, 'device_pk': wire.b64u(claim['signer']),
                          'expires': row['expires']}}

    async def _prepare(self, fid, row, claim):
        try:
            endpoint = await self.upload(fid, row, claim)
            if not self.valid(fid, row, claim):
                raise media.Gone('changed')
            claim['endpoint'] = endpoint
            claim['ready'] = True
        except (Exception, asyncio.CancelledError):
            # A fixed result, never exception text: URLs/signatures/cipher keys cannot enter logs.
            claim['failed'] = True
            claim['ready'] = False

    async def authorize(self, message):
        """Only called for a challenge from our authenticated relay host socket."""
        fid, transfer, index = message.get('offer'), message.get('transfer'), message.get('index')
        claim = self.claims.get(fid) if isinstance(fid, str) else None
        row = self.downloads.offers.get(fid) if isinstance(fid, str) else None
        if (not row or not claim or not claim['ready'] or claim['transfer'] != transfer
                or message.get('device_pk') != wire.b64u(claim['signer'])
                or type(index) is not int or not 0 <= index < math.ceil(row['rec']['bytes'] / cipher.CHUNK)
                or not self.valid(fid, row, claim)):
            return False
        try:
            await asyncio.to_thread(source, row['rec'])
        except (OSError, media.Gone):
            return False
        return self.valid(fid, row, claim)

    async def _upload(self, fid, row, claim):
        from .private_http import RelayFiles
        transport = RelayFiles(self.host)
        return await transport.upload(self, fid, row, claim)

    async def relay_message(self, ws, message):
        if len(message) > 2048 or self.host.ws is not ws:
            return
        try:
            obj = json.loads(message)
        except (ValueError, TypeError):
            return
        if (not isinstance(obj, dict) or obj.get('t') != 'file_authorize'
                or not wire.is_id22(obj.get('rid'))):
            return
        ok = await self.authorize(obj)
        if self.host.ws is ws:
            await ws.send(json.dumps({'t': 'file_authorize_result', 'rid': obj['rid'], 'ok': ok}, separators=(',', ':')))

    async def _delete(self, fid):
        from .private_http import RelayFiles
        try:
            transport = RelayFiles(self.host)
            await asyncio.to_thread(transport.request, 'DELETE', f'/v1/file/{self.host.channel}/{fid}', b'')
        except Exception:
            pass  # local burn is authoritative; cloud bytes also have a fixed expiry
