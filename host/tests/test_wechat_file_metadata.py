"""Read iLink file metadata back at the actual API/CDN serialization boundary."""
import _hermetic  # noqa: F401
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit

from agentj import media, wechat_protocol as wx


class WireCapture:
    def __init__(self):
        self.requests = []

    def request(self, method, url, headers=None, body=None, *_args):
        path = urlsplit(url).path
        self.requests.append((method, path, headers, body))
        if path.endswith('/getuploadurl'):
            return 200, {}, b'{"upload_param":"fixture-upload"}'
        if path.endswith('/upload'):
            return 200, {'x-encrypted-param': 'fixture-download'}, b''
        return 200, {}, b'{}'


class FileMetadata(unittest.TestCase):
    def setUp(self):
        self.transport = WireCapture()
        self.api = wx.API({'bot_token': 'fixture-only'}, self.transport)
        self.media = wx.Media(self.api)

    def check_file(self, name, plain):
        self.transport.requests.clear()
        item = self.media.upload('fixture-recipient', {'type': 4, 'file_item': {'file_name': name}}, plain)
        self.api.call('sendmessage', {'msg': {'item_list': [item]}})
        presign, upload, send = self.transport.requests
        self.assertEqual([r[0] for r in self.transport.requests], ['POST'] * 3)
        self.assertEqual([r[1].rsplit('/', 1)[-1] for r in self.transport.requests],
                         ['getuploadurl', 'upload', 'sendmessage'])
        request = json.loads(presign[3])
        self.assertEqual(request['media_type'], 3)
        self.assertEqual(request['rawsize'], len(plain))
        self.assertEqual(request['rawfilemd5'], hashlib.md5(plain).hexdigest())
        self.assertEqual(request['filesize'], len(upload[3]))
        self.assertEqual(upload[2]['Content-Type'], 'application/octet-stream')
        self.assertTrue(send[2]['Content-Type'].startswith('application/json'))
        sent = json.loads(send[3])['msg']['item_list'][0]
        self.assertEqual(sent['type'], 4)
        detail = sent['file_item']
        media = detail['media']
        actual = wx.decrypt(upload[3], wx.key_bytes(media['aes_key']))
        self.assertEqual(media['encrypt_query_param'], 'fixture-download')
        self.assertEqual(media['encrypt_type'], 1)
        self.assertEqual(detail['file_name'], name)
        self.assertEqual(detail['len'], str(len(actual)))
        self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(plain).digest())
        print(f'iLink file={name!r} bytes={len(actual)} sha256={hashlib.sha256(actual).hexdigest()} '
              f'rawsize={request["rawsize"]} ciphertext={request["filesize"]} media_type=3 item_type=4 '
              f'upload_type={upload[2]["Content-Type"]}')

    def test_common_extensions_and_utf8_names(self):
        fixtures = {
            'pptx': (b'PK\x03\x04', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
            'docx': (b'PK\x03\x04', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
            'xlsx': (b'PK\x03\x04', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            'pdf': (b'%PDF-1.4\n', 'application/pdf'),
            'zip': (b'PK\x03\x04', 'application/zip'),
            'mp4': (b'\x00\x00\x00\x18ftypmp42', 'video/mp4'),
            'md': (b'# Test file\n', 'text/markdown'),
        }
        with tempfile.TemporaryDirectory(prefix='aj-file-metadata-') as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            registry = media.Media(SimpleNamespace(root=Path(tmp) / 'state'), str(work))
            for ext, (header, mime) in fixtures.items():
                for stem in ('纯中文', '演示稿 终版（十一月）', 'Plain file'):
                    name = f'{stem}.{ext}'
                    with self.subTest(name=name):
                        plain = header + name.encode()
                        (work / name).write_bytes(plain)
                        rec, why = registry.check(media.Ref(name, name, True), owner=True)
                        self.assertIsNone(why)
                        self.assertEqual((rec['name'], rec['mime']), (name, mime))
                        self.check_file(rec['name'], plain)

    def test_name_is_not_truncated_before_extension(self):
        self.check_file('a' * 201 + '.pptx', b'long filename fixture')

    def test_missing_or_unsafe_filename_is_refused_before_upload(self):
        invalid_names = ('', '../deck.pptx', 'section\\deck.pptx', 'line\nbreak.pptx')
        items = [{'type': 4}, *({'type': 4, 'file_item': {'file_name': name}} for name in invalid_names)]
        for item in items:
            with self.subTest(item=item):
                self.transport.requests.clear()
                with self.assertRaises(wx.Failure):
                    self.media.upload('fixture-recipient', item, b'empty filename fixture')
                self.assertEqual(self.transport.requests, [])

    def test_14_mib_is_below_local_limit(self):
        self.check_file('演示稿 大文件.pptx', b'P' * (14 * 1024 * 1024))
