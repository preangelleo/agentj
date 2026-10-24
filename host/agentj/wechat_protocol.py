"""Tencent iLink 2.4.10 wire adapter, ported from the verified ClawBot PoC.
Official source: Tencent/openclaw-weixin @ 874637a0dbc24050f72d1385add4fc6170a2d726.
No SDK, model, storage or logging here. Errors contain constant codes only.
"""
import base64
import hashlib
import json
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
BASE = 'https://ilinkai.weixin.qq.com'
CDN = 'https://novac2c.cdn.weixin.qq.com/c2c'
VERSION = '2.4.10'
MAX_MEDIA = 25 * 1024 * 1024
KINDS = {1: ('text', '文字'), 2: ('image', '图片'), 3: ('voice', '语音'), 4: ('file', '文件'), 5: ('video', '视频')}
class Failure(Exception):
    """Only constant codes and integers may enter this exception."""
    def __init__(self, code, http=0, ret=0):
        self.code, self.http, self.ret = code, http, ret
        super().__init__(code)


def safe_url(url, api=False):
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ''
    if (p.scheme != 'https' or p.username or p.password or p.port not in (None, 443)
            or not (host.endswith('.weixin.qq.com') or host == 'weixin.qq.com')):
        raise Failure('untrusted_tencent_url')
    if api and (p.query or p.fragment or p.path not in ('', '/')):
        raise Failure('invalid_api_base')
    return url.rstrip('/') if api else url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Failure('unexpected_http_redirect', http=code)


class Transport:
    def request(self, method, url, headers=None, body=None, timeout=20, limit=MAX_MEDIA + 32):
        # URL validation applies before any secret is sent. Never print URLs/errors/bodies.
        p = urllib.parse.urlsplit(url)
        safe_url(url)
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as r:
                data = r.read(limit + 1)
                if len(data) > limit:
                    raise Failure('response_too_large')
                return r.status, {k.lower(): v for k, v in r.headers.items()}, data
        except urllib.error.HTTPError as e:
            raise Failure('http_error', http=e.code) from None
        except (TimeoutError, socket.timeout):
            raise Failure('timeout') from None
        except urllib.error.URLError:
            raise Failure('network_error') from None


def parse_json(data):
    try:
        obj = json.loads(data)
        if not isinstance(obj, dict):
            raise ValueError()
        return obj
    except (ValueError, UnicodeError):
        raise Failure('invalid_json') from None


def checked(obj):
    for field in ('ret', 'errcode'):
        value = obj.get(field, 0)
        if value not in (None, 0):
            raise Failure('api_error', ret=value if isinstance(value, int) else -1)
    return obj


class API:
    def __init__(self, account=None, transport=None):
        self.account = account or {}
        self.base = safe_url(self.account.get('baseurl', BASE), api=True)
        self.transport = transport or Transport()

    def call(self, endpoint, body=None, query=None, base=None, timeout=20, auth=True):
        headers = {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': str((2 << 16) | (4 << 8) | 10)}
        url = safe_url(base or self.base, api=True) + '/ilink/bot/' + endpoint
        if query:
            url += '?' + urllib.parse.urlencode(query)
        raw = None
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'AuthorizationType': 'ilink_bot_token',
                            'X-WECHAT-UIN': base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
            if auth:
                headers['Authorization'] = 'Bearer ' + self.account['bot_token']
                body = dict(body, base_info={'channel_version': VERSION, 'bot_agent': 'AgentJ/1.0'})
            raw = json.dumps(body).encode()
        _, _, data = self.transport.request('POST' if raw is not None else 'GET', url, headers, raw, timeout, 2 * 1024 * 1024)
        return checked(parse_json(data))


def key_bytes(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) == 32:
            raw = bytes.fromhex(raw.decode('ascii'))
        if len(raw) != 16:
            raise ValueError()
        return raw
    except (ValueError, UnicodeError):
        raise Failure('invalid_media_key') from None


def encrypt(data, key):
    pad = padding.PKCS7(128).padder()
    padded = pad.update(data) + pad.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return cipher.update(padded) + cipher.finalize()


def decrypt(data, key):
    try:
        cipher = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = cipher.update(data) + cipher.finalize()
        unpad = padding.PKCS7(128).unpadder()
        return unpad.update(padded) + unpad.finalize()
    except ValueError:
        raise Failure('invalid_media_ciphertext') from None


class Media:
    def __init__(self, api):
        self.api = api

    def download(self, item):
        kind = KINDS[item['type']][0]
        detail = item.get(kind + '_item', {})
        media = detail.get('media', {})
        if not media.get('full_url') and not media.get('encrypt_query_param'):
            raise Failure('missing_media_reference')
        url = media.get('full_url') or CDN + '/download?' + urllib.parse.urlencode({'encrypted_query_param': media['encrypt_query_param']})
        _, _, data = self.api.transport.request('GET', url, timeout=60)
        if kind == 'image' and detail.get('aeskey'):
            try:
                key = bytes.fromhex(detail['aeskey'])
                if len(key) != 16:
                    raise ValueError()
            except ValueError:
                raise Failure('invalid_image_key') from None
        elif media.get('aes_key'):
            key = key_bytes(media['aes_key'])
        elif kind == 'image':
            key = None
        else:
            raise Failure('missing_media_key')
        plain = decrypt(data, key) if key is not None else data
        if len(plain) > MAX_MEDIA:
            raise Failure('local_media_limit_25MiB')
        return plain

    def upload(self, user, item, plain):
        if len(plain) > MAX_MEDIA:
            raise Failure('local_media_limit_25MiB')
        typ = item['type']
        key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
        cipher = encrypt(plain, key)
        upload = self.api.call('getuploadurl', {'filekey': filekey, 'media_type': {2: 1, 4: 3, 5: 2}[typ],
                              'to_user_id': user, 'rawsize': len(plain), 'rawfilemd5': hashlib.md5(plain).hexdigest(),
                              'filesize': len(cipher), 'no_need_thumb': True, 'aeskey': key.hex()})
        url = upload.get('upload_full_url', '').strip()
        if not url:
            if not upload.get('upload_param'):
                raise Failure('missing_upload_url')
            url = CDN + '/upload?' + urllib.parse.urlencode({'encrypted_query_param': upload['upload_param'], 'filekey': filekey})
        for attempt in range(3):
            try:
                status, headers, _ = self.api.transport.request('POST', url, {'Content-Type': 'application/octet-stream'}, cipher, 60)
                param = headers.get('x-encrypted-param')
                if status != 200 or not param:
                    raise Failure('cdn_missing_download_param', http=status)
                break
            except Failure as e:
                if 400 <= e.http < 500 or attempt == 2:
                    raise
                continue
        media = {'encrypt_query_param': param, 'aes_key': base64.b64encode(key.hex().encode()).decode(), 'encrypt_type': 1}
        if typ == 2:
            outbound = {'type': 2, 'image_item': {'media': media, 'mid_size': len(cipher)}}
        elif typ == 5:
            outbound = {'type': 5, 'video_item': {'media': media, 'video_size': len(cipher)}}
        else:
            name = item.get('file_item', {}).get('file_name') or 'echo.bin'
            name = name.replace('\\', '/').split('/')[-1][:200] or 'echo.bin'
            outbound = {'type': 4, 'file_item': {'media': media, 'file_name': name, 'len': str(len(plain))}}
        return outbound
