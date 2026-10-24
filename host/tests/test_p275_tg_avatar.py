"""P275: a Telegram bot bound through Agent J gets the official Agent J photo when it has none — once, never over the owner's
own photo — plus `agentj bots avatar status|set|reset <bot>` and the bot card's 「换成 Agent J 头像」. Offline: a local fake
Bot API (owner bot, `telegram.BASE`) and fake transports (customer-service bots). No real bot, no real key."""
import _hermetic  # noqa: F401,I001
import asyncio
import email.parser
import email.policy
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    from PIL import Image  # noqa: E402  (pixel checks; the host venv has no Pillow, the site toolchain does)
except ImportError:
    Image = None
from agentj import telegram as tg, tg_avatar  # noqa: E402
from agentj.bots import telegram as btg  # noqa: E402
from agentj.bots.store import BotError  # noqa: E402

KEY_ENV = 'AJ_TEST_P275_KEY'
SECRET = '12345:' + 'A' * 30            # shape of a bot key, built at run time; never a real one


class FakeBotAPI:
    """getMe / getUserProfilePhotos (photos = self.photos) / setMyProfilePhoto (multipart) / removeMyProfilePhoto."""

    def __init__(self, photos=0):
        self.photos, self.calls, self.lock = photos, [], threading.Lock()
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                method = self.path.rsplit('/', 1)[-1]
                ct = self.headers.get('Content-Type', '')
                fields, files = {}, {}
                if ct.startswith('multipart/form-data'):
                    m = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(b'Content-Type: ' + ct.encode() + b'\r\n\r\n' + body)
                    for part in m.iter_parts():
                        name = part.get_param('name', header='content-disposition')
                        if part.get_filename() is not None:
                            files[name] = {'filename': part.get_filename(), 'mime': part.get_content_type(), 'data': part.get_payload(decode=True)}
                        else:
                            fields[name] = part.get_payload(decode=True).decode()
                else:
                    fields = json.loads(body or b'{}')
                with api.lock:
                    api.calls.append({'method': method, 'fields': fields, 'files': files, 'path': self.path})
                    if method == 'getMe':
                        res = {'id': 4242, 'is_bot': True, 'username': 'p275_bot'}
                    elif method == 'getUserProfilePhotos':
                        res = {'total_count': api.photos, 'photos': []}
                    elif method == 'setMyProfilePhoto':
                        api.photos += 1; res = True
                    elif method == 'removeMyProfilePhoto':
                        api.photos = max(0, api.photos - 1); res = True
                    else:
                        res = {}
                data = json.dumps({'ok': True, 'result': res}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()

    def methods(self):
        with self.lock:
            return [c['method'] for c in self.calls]


def check_upload(test, fields, files):
    """setMyProfilePhoto: photo = InputProfilePhotoStatic pointing at the attached JPG (Bot API 2026-02)."""
    photo = json.loads(fields['photo'])
    test.assertEqual(photo, {'type': 'static', 'photo': 'attach://' + tg_avatar.FIELD})
    f = files[tg_avatar.FIELD]
    test.assertEqual(f['mime'], 'image/jpeg')
    test.assertEqual(f['data'], tg_avatar.ASSET.read_bytes())


def jpeg_size(data):
    i = 2
    while i < len(data):
        marker, n = data[i + 1], int.from_bytes(data[i + 2:i + 4], 'big')
        if marker in (0xc0, 0xc1, 0xc2):
            return int.from_bytes(data[i + 7:i + 9], 'big'), int.from_bytes(data[i + 5:i + 7], 'big')
        i += 2 + n
    raise ValueError('no SOF')


class Asset(unittest.TestCase):
    def test_jpeg_640(self):
        data = tg_avatar.ASSET.read_bytes()
        self.assertEqual(data[:3], b'\xff\xd8\xff')
        self.assertEqual(jpeg_size(data), (640, 640))
        self.assertLess(len(data), 64 * 1024)

    def test_official_avatar_pixels(self):
        if Image is None:
            self.skipTest('Pillow not installed (host venv)')
        im = Image.open(tg_avatar.ASSET)
        self.assertEqual((im.format, im.size), ('JPEG', (640, 640)))
        rgb = im.convert('RGB')
        for xy in [(0, 0), (639, 0), (0, 639), (639, 639), (320, 40)]:
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(rgb.getpixel(xy), (0xfa, 0xf9, 0xf5))), xy)   # Lotion
        # the robot sits inside Telegram's round crop: every non-background pixel within 70 % of the circle's radius
        far = 0
        for y in range(0, 640, 4):
            for x in range(0, 640, 4):
                if sum(abs(a - b) for a, b in zip(rgb.getpixel((x, y)), (0xfa, 0xf9, 0xf5))) > 30:
                    far = max(far, ((x - 320) ** 2 + (y - 320) ** 2) ** .5)
        self.assertGreater(far, 100)
        self.assertLessEqual(far, 0.70 * 320 + 4)
        green = sum(1 for p in rgb.get_flattened_data() if abs(p[0] - 0x62) < 8 and abs(p[1] - 0x98) < 8 and abs(p[2] - 0x87) < 8)
        self.assertGreater(green, 20000)   # Emerald Oasis body: the green main logo

    def test_asset_matches_brand_kit_generator(self):
        site_tools = HERE.parents[1] / 'site' / 'tools' / 'brand-kit.py'
        if Image is None:
            self.skipTest('Pillow not installed (host venv)')
        if not site_tools.exists():
            self.skipTest('public export: host/ ships without site/')
        import importlib.util
        spec = importlib.util.spec_from_file_location('brand_kit', site_tools)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        self.assertEqual(mod.avatar(), tg_avatar.ASSET.read_bytes())


class Ensure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = self.tmp.name
        self.calls, self.uploads, self.photos = [], [], 0

    def call(self, m, v):
        self.calls.append((m, v))
        if m == 'getUserProfilePhotos':
            return {'total_count': self.photos, 'photos': []}
        if m == 'removeMyProfilePhoto':
            self.photos = 0; return True
        raise AssertionError(m)

    def upload(self, method, fields, field, name, mime, data):
        self.uploads.append((method, fields, field, name, mime, data)); self.photos = 1; return True

    def test_no_photo_sets_once(self):
        self.assertEqual(tg_avatar.ensure(self.root, self.call, self.upload, 7), 'set')
        method, fields, field, name, mime, data = self.uploads[0]
        self.assertEqual(method, 'setMyProfilePhoto')
        check_upload(self, fields, {field: {'mime': mime, 'data': data}})
        self.assertEqual(self.calls[0], ('getUserProfilePhotos', {'user_id': 7, 'limit': 1}))
        self.assertEqual(tg_avatar.ensure(self.root, self.call, self.upload, 7), 'skipped')
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(tg_avatar.recorded(self.root, 7), 'set')
        self.assertEqual(oct(os.stat(pathlib.Path(self.root) / tg_avatar.LEDGER).st_mode & 0o777), '0o600')

    def test_own_photo_is_kept(self):
        self.photos = 1
        self.assertEqual(tg_avatar.ensure(self.root, self.call, self.upload, 8), 'kept')
        self.assertEqual(self.uploads, [])
        self.assertEqual(tg_avatar.ensure(self.root, self.call, self.upload, 8), 'skipped')   # also never re-checked

    def test_failure_is_not_remembered(self):
        def broken(m, v): raise OSError('network')
        with self.assertRaises(OSError):
            tg_avatar.ensure(self.root, broken, self.upload, 9)
        self.assertIsNone(tg_avatar.recorded(self.root, 9))
        self.assertEqual(tg_avatar.ensure(self.root, self.call, self.upload, 9), 'set')

    def test_actions(self):
        self.photos = 1
        self.assertEqual(tg_avatar.act(self.root, 'status', self.call, self.upload, 5), {'has_photo': True, 'agentj': False})
        self.assertEqual(tg_avatar.act(self.root, 'set', self.call, self.upload, 5), {'has_photo': True, 'agentj': True})
        self.assertEqual(len(self.uploads), 1)   # set replaces even an existing photo (the owner asked)
        self.assertEqual(tg_avatar.act(self.root, 'reset', self.call, self.upload, 5), {'has_photo': False, 'agentj': False})
        self.assertEqual(tg_avatar.ensure(self.root, self.call, self.upload, 5), 'skipped')   # removed by the owner: never re-added
        with self.assertRaises(ValueError):
            tg_avatar.act(self.root, 'paint', self.call, self.upload, 5)


class OwnerBot(unittest.IsolatedAsyncioTestCase):
    """The owner's bot: telegram.api / telegram.upload against a local fake Bot API; Telegram.auto_avatar after bind."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg = {'owner_id': 1, 'key_env': KEY_ENV, 'generation': 't'}
        self.logs = []

    def start(self, photos):
        self.api = FakeBotAPI(photos); self.addCleanup(self.api.close)
        for p in (patch.object(tg, 'BASE', self.api.url), patch.dict(os.environ, {KEY_ENV: SECRET})):
            p.start(); self.addCleanup(p.stop)
        st = SimpleNamespace(log=lambda ev, **kw: self.logs.append((ev, kw)))
        return SimpleNamespace(root=pathlib.Path(self.tmp.name), host=SimpleNamespace(st=st))

    async def test_bind_sets_photo_when_missing(self):
        fake = self.start(0)
        await tg.Telegram.auto_avatar(fake, self.cfg, 4242)
        self.assertEqual(self.api.methods(), ['getUserProfilePhotos', 'setMyProfilePhoto'])
        c = self.api.calls[1]
        self.assertTrue(c['path'].endswith('/bot' + SECRET + '/setMyProfilePhoto'))
        check_upload(self, c['fields'], c['files'])
        self.assertEqual(self.logs, [('telegram_avatar', {'result': 'set'})])
        await tg.Telegram.auto_avatar(fake, self.cfg, 4242)          # restart / re-bind: no second call
        self.assertEqual(self.api.methods(), ['getUserProfilePhotos', 'setMyProfilePhoto'])

    async def test_bind_keeps_own_photo(self):
        fake = self.start(2)
        await tg.Telegram.auto_avatar(fake, self.cfg, 4242)
        self.assertEqual(self.api.methods(), ['getUserProfilePhotos'])
        self.assertEqual(self.logs, [('telegram_avatar', {'result': 'kept'})])

    async def test_failure_logs_kind_never_key(self):
        fake = self.start(0)
        with patch.object(tg, 'BASE', 'http://127.0.0.1:9'):          # nothing listens: network failure
            await tg.Telegram.auto_avatar(fake, self.cfg, 4242)
        self.assertEqual(self.logs[0][0], 'telegram_avatar')
        self.assertEqual(self.logs[0][1]['result'], 'failed')
        self.assertNotIn(SECRET, json.dumps(self.logs))

    async def test_bind_schedules_auto_avatar(self):
        src = (HERE.parent / 'agentj' / 'telegram.py').read_text()
        bind = src[src.index('    async def bind(self,cfg):'):src.index('    async def auto_avatar(')]
        self.assertIn('self.check_avatar(cfg,bot)', bind)


class CustomerServiceBot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        d = pathlib.Path(self.tmp.name) / 'secrets'; d.mkdir(mode=0o700)
        (d / btg.NAME).write_text(SECRET); os.chmod(d / btg.NAME, 0o600)
        self.store = SimpleNamespace(secrets=lambda bid: d)
        self.photos, self.seen, self.sent = 0, [], []

    def request(self, secret, method, body):
        self.assertEqual(secret, SECRET); self.seen.append(method)
        if method == 'getMe':
            return {'id': 777, 'is_bot': True}
        if method == 'getUserProfilePhotos':
            self.assertEqual(body, {'user_id': 777, 'limit': 1}); return {'total_count': self.photos}
        if method == 'removeMyProfilePhoto':
            self.photos = 0; return True
        raise AssertionError(method)

    def transport(self, url, method, body, headers, **kw):
        self.sent.append((url, method, body, headers, kw)); self.photos = 1
        return json.dumps({'ok': True, 'result': True}).encode()

    def send(self, secret, *a):
        return btg.upload(secret, *a, transport=self.transport)

    def test_auto_then_skip(self):
        root = self.tmp.name
        self.assertEqual(btg.avatar(self.store, 'b1', root, None, self.request, self.send), 'set')
        url, method, body, headers, kw = self.sent[0]
        self.assertEqual((url, method), ('https://api.telegram.org/bot' + SECRET + '/setMyProfilePhoto', 'POST'))
        self.assertEqual(kw['domains'], ['api.telegram.org'])
        m = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(b'Content-Type: ' + headers['Content-Type'].encode() + b'\r\n\r\n' + body)
        fields, files = {}, {}
        for part in m.iter_parts():
            name = part.get_param('name', header='content-disposition')
            if part.get_filename():
                files[name] = {'mime': part.get_content_type(), 'data': part.get_payload(decode=True)}
            else:
                fields[name] = part.get_payload(decode=True).decode()
        check_upload(self, fields, files)
        self.assertEqual(btg.avatar(self.store, 'b1', root, None, self.request, self.send), 'skipped')
        self.assertEqual(len(self.sent), 1)

    def test_kept_and_card_set(self):
        root = self.tmp.name; self.photos = 1
        self.assertEqual(btg.avatar(self.store, 'b2', root, None, self.request, self.send), 'kept')
        self.assertEqual(self.sent, [])
        self.assertEqual(btg.avatar(self.store, 'b2', root, 'set', self.request, self.send), {'has_photo': True, 'agentj': True})
        self.assertEqual(len(self.sent), 1)

    def test_errors_are_fixed_codes(self):
        def bad(url, *a, **k): raise RuntimeError('upstream said ' + url)
        with self.assertRaises(BotError) as e:
            btg.upload(SECRET, 'setMyProfilePhoto', {'photo': '{}'}, 'f', 'a.jpg', 'image/jpeg', b'x', transport=bad)
        self.assertEqual(str(e.exception), 'telegram_unavailable')
        self.assertNotIn(SECRET, repr(e.exception.__cause__) + repr(e.exception.__context__ if e.exception.__suppress_context__ is False else ''))
        with self.assertRaises(BotError):
            btg.upload(SECRET, 'sendPhoto', {}, 'f', 'a.jpg', 'image/jpeg', b'x', transport=self.transport)   # only setMyProfilePhoto
        with self.assertRaises(BotError):
            btg.api(SECRET, 'setWebhook', {})                                                             # whitelist unchanged otherwise
        for m in ('getUserProfilePhotos', 'removeMyProfilePhoto'):
            self.assertIn(m, (HERE.parent / 'agentj' / 'bots' / 'telegram.py').read_text())


class Cli(unittest.TestCase):
    def test_parser_and_request(self):
        import argparse
        from agentj.bots import cli
        p = argparse.ArgumentParser(); sub = p.add_subparsers(dest='cmd'); cli.add_parser(sub)
        a = p.parse_args(['bots', 'avatar', 'set', 'owner'])
        sent = []
        with patch.object(cli, 'send', lambda req: sent.append(req) or {'ok': True}), patch('sys.stdout', io.StringIO()):
            self.assertEqual(a.fn(a), 0)
        self.assertEqual(sent, [{'op': 'avatar', 'action': 'set', 'id': 'owner'}])
        with self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()):
            p.parse_args(['bots', 'avatar', 'upload', 'owner'])   # only status / set / reset: no arbitrary image


class Manager(unittest.IsolatedAsyncioTestCase):
    async def test_owner_route_uses_serve_environment(self):
        from agentj.bots.owner import Manager as M
        api = FakeBotAPI(0); self.addCleanup(api.close)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        logs = []
        host = SimpleNamespace(st=SimpleNamespace(root=pathlib.Path(tmp.name), log=lambda ev, **kw: logs.append((ev, kw))))
        m = M.__new__(M); m.host = host
        cfg = {'owner_id': 1, 'key_env': KEY_ENV, 'generation': 't'}
        with patch.object(tg, 'BASE', api.url), patch.dict(os.environ, {KEY_ENV: SECRET}), patch.object(tg, 'configuration', return_value=cfg):
            self.assertEqual(await m.avatar('status', 'owner'), {'has_photo': False, 'agentj': False})
            self.assertEqual(await m.avatar('set', 'owner'), {'has_photo': True, 'agentj': True})
            with self.assertRaises(BotError):
                await m.avatar('paint', 'owner')
        with patch.object(tg, 'configuration', return_value=None):
            with self.assertRaises(BotError) as e:
                await m.avatar('status', 'owner')
            self.assertEqual(str(e.exception), 'telegram_key_required')
        self.assertEqual(api.methods(), ['getMe', 'getUserProfilePhotos', 'getMe', 'setMyProfilePhoto'])
        self.assertNotIn(SECRET, json.dumps(logs))


if __name__ == '__main__':
    unittest.main()
