"""Host-private encrypted WeChat state; inbox files use the existing no-follow writer.

Same local-identity/HKDF/AES-GCM storage boundary as mail.py; purpose-separated key
and AAD. Nothing in this file is an authorization decision supplied by Tencent.
"""
from __future__ import annotations
import json
import fcntl
import os
import secrets
import tempfile
import time
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from . import inbox

AAD = b'agentj-wechat-v1'
PENDING_TTL = 86400
MAX_PENDING = 100


class Store:
    def __init__(self, st, *, exclusive=False, namespace="wechat"):
        self.st = st
        if namespace not in ('wechat', 'external'):
            raise ValueError('channel_namespace')
        self.aad = AAD if namespace == 'wechat' else b'agentj-external-v1'
        self.path = st.root / (namespace + '.bin')
        self.fd = None
        if exclusive:
            fd = os.open(st.root / (namespace + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise ValueError('wechat_already_running') from None
            self.fd = fd
        try:
            self.data = self.read()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def key(self):
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=self.aad,
                    info=b'local-channel-state').derive(self.st.x25519_path.read_bytes())

    def read(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('wechat_state_invalid')
            blob = self.path.read_bytes()
        except FileNotFoundError:
            return {'account': {}, 'cursor': '', 'context': '', 'done': [], 'records': {},
                    'sessions': {}, 'counts': {}, 'generation': secrets.token_hex(16)}
        try:
            d = json.loads(AESGCM(self.key()).decrypt(blob[:12], blob[12:], self.aad))
            if not isinstance(d, dict) or not isinstance(d['records'], dict):
                raise ValueError()
            return d
        except Exception:
            # Never overwrite unreadable state: dedup/uncertain handoffs must survive.
            raise ValueError('wechat_state_invalid') from None

    def save(self):
        iv = secrets.token_bytes(12)
        plain = json.dumps(self.data, ensure_ascii=False).encode()
        if len(plain) > 8 * 1024 * 1024 - 32:
            raise ValueError('wechat_backpressure')
        self.st.write_private(self.path, iv + AESGCM(self.key()).encrypt(iv, plain, self.aad))

    def session_id(self, fid, kind):
        return self.data['sessions'].get(fid + ':' + kind)

    def set_session_id(self, fid, kind, sid):
        self.data['sessions'][fid + ':' + kind] = sid
        self.save()

    def count(self, name):
        c = self.data['counts']
        c[name] = c.get(name, 0) + 1
        self.save()

    def expire(self, now=None):
        now = time.time() if now is None else now
        for r in self.data['records'].values():
            if r['state'] == 'pending' and r['until'] <= now:
                r['state'] = 'expired'
        self.save()

    def put(self, rid, paths, text, *, now=None):
        self.expire(now)
        records = self.data['records']
        if rid in records:
            return records[rid]
        if sum(r['state'] in ('pending', 'uncertain') for r in records.values()) >= MAX_PENDING:
            raise ValueError('wechat_backpressure')
        # Completed rows can be evicted; a busy text conversation must not lock the
        # channel for seven days. Pending/uncertain work is never evicted.
        now = time.time() if now is None else now
        records = {k: r for k, r in records.items() if r['state'] in ('pending', 'uncertain', 'processing') or r['at'] > now - 7 * 86400}
        finished = sorted((k for k, r in records.items() if r['state'] not in ('pending', 'uncertain', 'processing')),
                          key=lambda k: records[k]['at'])
        for key in finished[:max(0, len(records) - 249)]:
            del records[key]
        if len(records) >= 250:
            raise ValueError('wechat_backpressure')
        r = {'id': rid, 'paths': paths, 'text': text[:2000], 'at': now, 'until': now + PENDING_TTL,
             'state': 'received', 'source': 'wechat', 'untrusted': True, 'execute': False}
        records[rid] = r
        self.data['records'] = records
        self.save()
        return r

    def place(self, workdir, data, name, mime):
        # Temporary bytes stay in protected host state and are always removed.
        fd, path = tempfile.mkstemp(prefix='wechat-', dir=self.st.root)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            return inbox.place(workdir, path, 'wechat-' + name, mime, secrets.token_hex(16))
        finally:
            os.unlink(path)
