"""0.26.8: binary image attachments are not scanned as text; specific workflow mail refusals; local build versions."""
import _hermetic  # noqa: F401
import copy
import hashlib
import io
import os
import random
import unittest
import zipfile
from unittest.mock import patch

from agentj import mail, update, workflow_mail as wm
from test_workflow_owner_mail import Delivery

CRED = b'password="hunter2hunter"'
rnd = random.Random(268).randbytes
JPG = b'\xff\xd8\xff\xe0' + rnd(4000) + CRED + rnd(4000) + b'\xff\xd9'
PNG = b'\x89PNG\r\n\x1a\n' + rnd(3000) + CRED
WEBP = b'RIFF\x00\x10\x00\x00WEBPVP8 ' + rnd(3000) + CRED


def zipped(members, comment=b'', method=zipfile.ZIP_STORED):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', method) as z:
        for n, d in members:
            z.writestr(n, d)
        z.comment = comment
    return b.getvalue()


def why(name, blob):
    try:
        wm.check_archive(name, blob)
    except ValueError as e:
        return e.args[0]


class BinaryScan(unittest.TestCase):
    def test_credential_like_bytes_inside_real_images_pass(self):
        self.assertTrue(wm.secret_content(JPG.decode('utf-8', 'replace')))   # what used to refuse the cover
        for name, blob in [('cover3x4.jpg', JPG), ('c.JPEG', JPG), ('c.png', PNG), ('c.webp', WEBP)]:
            self.assertIsNone(why(name, blob), name)
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            self.assertIsNone(why('materials.zip', zipped([('cover.jpg', JPG), ('p.png', PNG), ('n.txt', b'safe')], method=method)))

    def test_text_behind_image_extension_or_text_credentials_still_refused(self):
        self.assertEqual(why('cover.jpg', CRED), 'artifact_type')
        self.assertEqual(why('cover.png', JPG), 'artifact_type')
        self.assertEqual(why('notes.txt', CRED), 'artifact_secret')
        self.assertEqual(why('notes.md', b'ok ' + CRED), 'artifact_secret')
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            self.assertEqual(why('m.zip', zipped([('n.txt', CRED)], method=method)), 'archive_secret')
            self.assertEqual(why('m.zip', zipped([('a.jpg', CRED)], method=method)), 'archive_image')
        self.assertEqual(why('m.zip', zipped([('n.txt', b'safe')], comment=CRED)), 'archive_secret')
        inner = zipped([('n.txt', CRED)])
        self.assertEqual(why('m.zip', zipped([('a.jpg', JPG + inner)])), 'archive_image')   # image/zip polyglot member
        self.assertEqual(why('cover.jpg', JPG + inner), 'archive_secret')                     # top-level polyglot: members scanned
        self.assertIn(why('notes.txt', inner), ('artifact_secret', 'archive_secret'))
        self.assertEqual(why('m.zip', b'not a zip'), 'archive')


class Refusals(Delivery):
    async def test_send_returns_the_specific_reason(self):
        await self.grant()
        for field, value, code in [('body', 'different body', 'content_hash'), ('to', ['outsider@example.test'], 'recipient'),
                                   ('format', 'html', 'format'), ('files', [], 'files')]:
            req = copy.deepcopy(self.req); req[field] = value
            res = await self.service.send(req, os.getpid())
            self.assertEqual((res['why'], res['provider_acceptance']), (code, False))
        req = copy.deepcopy(self.req); del req['delivery_manifest']['attachments'][0]['sha256']
        self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'attachment')
        req = copy.deepcopy(self.req); del req['subject']
        self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'content')
        for exc, code in [(KeyError('path'), 'request_invalid'), (TypeError('x'), 'request_invalid'),
                          (OSError(13, 'denied', '/private/x'), 'file_unreadable'),
                          (ValueError('/private/x password=abc'), 'request_invalid'), (ValueError(['manifest']), 'request_invalid')]:
            with patch.object(self.service, 'snapshot', side_effect=exc):
                res = await self.service.send(self.req, os.getpid())
            self.assertEqual(res, {'ok': False, 'why': code, 'provider_acceptance': False})
        self.file.write_text('changed bytes')
        self.assertEqual((await self.service.send(self.req, os.getpid()))['why'], 'attachment_hash')
        self.assertEqual(self.smtp.sent, [])

    async def test_jpeg_cover_with_credential_like_bytes_is_delivered(self):
        await self.grant()
        cover = self.work / 'cover3x4.jpg'; cover.write_bytes(JPG)
        req = copy.deepcopy(self.req); req['files'] = [str(cover)]
        req['delivery_manifest']['attachments'] = [{'path': str(cover), 'sha256': hashlib.sha256(JPG).hexdigest(),
                                                    'size': len(JPG), 'role': 'cover'}]
        res = await self.service.send(req, os.getpid())
        self.assertTrue(res['ok'], res); self.assertEqual(len(self.smtp.sent), 1)

    def test_every_code_has_plain_words(self):
        for lang in ('zh', 'en'):
            for code in wm.REFUSE + ['request_invalid', 'file_unreadable']:
                self.assertNotEqual(mail.why_text(code, lang), code)


class LocalVersion(unittest.TestCase):
    def test_local_segment_parses_and_yields_to_release(self):
        self.assertIsNotNone(update.parse('0.26.6a1+local428'))
        self.assertEqual(update.compare('0.26.7a1', '0.26.6a1+local428'), 1)
        self.assertEqual(update.compare('0.26.6a1', '0.26.6a1+local428'), 1)    # same number: the release is what to install
        self.assertEqual(update.compare('0.26.6a1+local428', '0.26.6a1+local428'), 0)
        self.assertEqual(update.compare('0.26.5', '0.26.6a1+local428'), -1)
        self.assertIsNone(update.parse('0.26.6a1+'))
        self.assertIsNone(update.parse('0.26.6a1+a b'))

    def test_check_reports_newer_for_a_local_build(self):
        with patch.object(update, '__version__', '0.26.6a1+local428'), \
             patch.object(update, 'fetch_latest', return_value=('0.26.7a1', 'ok')), \
             patch.object(update, 'install_kind', return_value={'kind': 'uv', 'where': '/x/bin/agentj'}):
            r = update.check()
        self.assertEqual((r['status'], r['current']), ('newer', '0.26.6a1+local428'))
        self.assertIn('0.26.7a1', r['command'])
        self.assertIn('本地构建', update.WHY['local']); self.assertIn('local build', update.WHY['local'])

if __name__ == '__main__':
    unittest.main()
