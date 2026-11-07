"""#329: ephemeral, paired-only file cards. No HTTP handler and no caller-supplied paths.

Offers originate only in a completed reply's explicit references inside its working
folder. Each bounded Noise read is signed, sequential and bound to that live session.
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import stat
import time
import threading

from . import controls, media, seaturl, wire

CAP = 'privatefiles1'
TTL = 3600
MAX_OFFERS = 256
MAX_TRANSFERS = 64
_ID = re.compile(r'[0-9a-f]{32}')


class Downloads:
    def __init__(self, host, clock=time.monotonic):
        self.host, self.clock = host, clock
        self.offers, self.transfers = {}, {}
        self.lock = threading.RLock()

    def prune(self):
        with self.lock:
            self._prune()

    def _prune(self):
        now = self.clock()
        self.offers = {k: v for k, v in self.offers.items() if now < v['until'] and self.origin_live(v['origin'])}
        self.transfers = {k: v for k, v in self.transfers.items() if v['id'] in self.offers}

    def _page_digest(self, page):
        return hashlib.sha256(json.dumps({'src':page.get('src'), 'text':(page.get('reply') or {}).get('text'),
                                          'end':page.get('end')},sort_keys=True,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest()

    def origin(self, turn):
        history = getattr(self.host, 'hist', None)
        if history is None or type(turn) is not int:
            return None
        page = history.get(turn)
        if page is None or page.get('end') == 'open':
            return None
        parts = getattr(history, 'parts', {})
        ids = next((ids for ids in parts.values() if turn in ids), [turn])
        pages = [(tid, history.get(tid)) for tid in ids]
        if any(page is None for _, page in pages):
            return None
        return {'history':history, 'epoch':str(history.epoch),
                'pages':[(tid, page, self._page_digest(page)) for tid,page in pages]}

    def origin_live(self, origin):
        if not origin:
            return False
        history = getattr(self.host, 'hist', None)
        return (history is origin['history'] and str(history.epoch) == origin['epoch']
                and all(history.get(tid) is page and self._page_digest(page) == digest
                        for tid,page,digest in origin['pages']))

    def offer(self, scanner, ref, *, turn=None):
        with self.lock:
            return self._offer(scanner, ref, turn=turn)

    def _offer(self, scanner, ref, *, turn=None):
        self.prune()
        origin = self.origin(turn)
        if not origin:
            return None
        root = scanner.current() or scanner.workdir
        if not root or not ref.explicit or '..' in ref.path.split('/'):
            return None
        raw = os.path.abspath(os.path.join(root, ref.path))
        # No aliases, symlink components, outside scopes or generated-state paths.
        if raw != os.path.realpath(raw) or os.path.commonpath([raw, os.path.abspath(root)]) != os.path.abspath(root):
            return None
        rec, why = scanner.check(ref, owner=False, root=root, large=True)
        if why or not rec or rec['bytes'] <= media.CAPS[rec['kind']] or rec['root'] != os.path.realpath(root):
            return None
        fd = media.open_rel(rec['root'], rec['parts'])
        try:
            s = os.fstat(fd)
            if (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != (rec['dev'], rec['ino'], rec['bytes'], rec['mtime'], rec['ctime']):
                return None
            rec = {**rec, 'ctime': s.st_ctime_ns}
        finally:
            os.close(fd)
        for fid, row in self.offers.items():
            if (row['rec'] == rec and row['agent'] is self.host.agent
                    and row['session'] == self.host.st.agent_session(self.host.agent.kind)
                    and row['stop_epoch'] == self.stop_epoch() and row['origin'] == origin
                    and self.origin_live(origin)):
                return self.card(fid, row)
        if not self.origin_live(origin) or len(self.offers) >= MAX_OFFERS:
            return None
        fid = secrets.token_hex(16)
        row = {'rec': rec, 'until': self.clock() + TTL, 'expires': int(time.time()) + TTL,
               'seat': self.host.channel, 'root': root, 'agent': self.host.agent, 'origin': origin,
               'session': self.host.st.agent_session(self.host.agent.kind), 'stop_epoch': self.stop_epoch()}
        self.offers[fid] = row
        return self.card(fid, row)

    def card(self, fid, row):
        return {'id': fid, 'seat': row['seat'], 'bytes': row['rec']['bytes'],
                'expires': row['expires'], 'sha256': row['rec']['sha256'],
                'locator': f"/{seaturl.seat_id(row['seat'])}/#file={fid}"}

    def stop_epoch(self):
        # State-less test transports have no stop file. A real Host always does.
        return controls.estop_state(self.host.st) if hasattr(self.host.st, 'estop_path') else None

    def live(self, s, row):
        return (self.host._paired_live(s) and bool(self.host.st.sign_key(s.device)) and not self.host.stopped()
                and self.clock() < row['until'] and self.host.channel == row['seat']
                and (self.host.media.current() or self.host.media.workdir) == row['root'] and self.host.agent is row['agent']
                and self.host.st.agent_session(self.host.agent.kind) == row['session']
                and self.stop_epoch() == row['stop_epoch'] and self.origin_live(row['origin']))

    def read(self, row, offset):
        rec = row['rec']
        fd = media.open_rel(rec['root'], rec['parts'])
        try:
            def unchanged():
                s = os.fstat(fd)
                return (stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid()
                        and (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                        == (rec['dev'], rec['ino'], rec['bytes'], rec['mtime'], rec['ctime']))
            if not unchanged():
                raise media.Gone('changed')
            data = os.pread(fd, min(media.CHUNK, rec['bytes'] - offset), offset)
            if len(data) != min(media.CHUNK, rec['bytes'] - offset) or not unchanged():
                raise media.Gone('changed')
            return data
        finally:
            os.close(fd)

    async def on_phone(self, s, obj):
        if not wire.is_rid(obj.get('r')):
            return
        self.prune()
        req = obj.get('request')
        action = obj['t']
        why = 'unpaired' if not self.host._paired_live(s) else controls.check(
            self.host.st, self.host.nonces, self.host.channel, s.device, obj, action, {'request': req})
        controls.log(self.host.st, action=action, device=s.device, result=why or 'verified', obj={'request': req})
        result = {'ok': False, 'why': why or 'gone'}
        if not why and isinstance(req, dict):
            fid = req.get('id')
            row = self.offers.get(fid) if isinstance(fid, str) else None
            if row and req.get('seat') == row['seat'] and self.live(s, row):
                if action == 'private_file_revoke':
                    self.offers.pop(fid, None)
                    self.prune()
                    result = {'ok': True}
                elif req.get('info') is True:
                    result = {'ok': True, 'card': self.card(fid, row), 'name': row['rec']['name']}
                else:
                    offset, transfer = req.get('offset'), req.get('transfer')
                    key = (s.device, transfer) if isinstance(transfer, str) and _ID.fullmatch(transfer) else None
                    entry = self.transfers.get(key)
                    if (key and type(offset) is int and 0 <= offset < row['rec']['bytes']
                            and ((entry and entry['session'] is s and entry['id'] == fid and entry['next'] == offset
                                  and entry['signer'] == self.host.st.sign_key(s.device))
                                 or (entry is None and offset == 0 and len(self.transfers) < MAX_TRANSFERS))):
                        entry = {'session': s, 'id': fid, 'next': -1, 'signer': self.host.st.sign_key(s.device)}
                        self.transfers[key] = entry  # reserve before await: no concurrent repeat
                        try:
                            data = await asyncio.to_thread(self.read, row, offset)
                            if (self.offers.get(fid) is row and self.live(s, row)
                                    and self.host.st.sign_key(s.device) == entry['signer']):
                                entry['next'] = offset + len(data)
                                result = {'ok': True, 'offset': offset, 'd': wire.b64u(data),
                                          'last': entry['next'] == row['rec']['bytes'], 'sha256': hashlib.sha256(data).hexdigest()}
                        except (media.Gone, OSError):
                            self.offers.pop(fid, None)
        await self.host.send_app(s, {'t': 'private_file_res', 'r': obj.get('r'), **result})
