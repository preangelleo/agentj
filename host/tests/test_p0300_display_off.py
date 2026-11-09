"""0.30.0 ships outbound display masking OFF (chairman/Jarvis 2026-11-09).

Incident 09:17: the installed 0.30.0 host masked every phone bubble as <redacted> because
46 `.env*` backups overflowed the display secret index and project() failed closed.
Decision: back to the 0.28.6 display behaviour — every display frame passes through
unchanged, never <redacted>, never fail-closed. Code stays dormant behind ENABLED.
"""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from agentj.serve import Host
from agentj import display_privacy, wire

ENV_PREFIX = '_'.join(('DB', 'PASSWORD')) + '='


class DisplayOff(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cwd = self.home / 'coding' / 'project'; self.cwd.mkdir(parents=True)
        # The incident shape: many backup env files, far past the index cap.
        for i in range(46):
            body = ''.join(f'K{j}={ "v%03d_%03d_opaquevalue" % (i, j) }\n' for j in range(300))
            (self.home / 'coding' / f'.env.bak-{i}').write_text(ENV_PREFIX + 'ordinarypassword123\n' + body)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'CODEX_HOME': str(self.home / '.codex'),
                                           'AGENTJ_ENV_FILE': str(self.home / 'absent.env')}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.frames = []
        def encrypt(aad, data):
            self.frames.append(wire.unpad_json(data))
            return b'ciphertext'
        self.s = NS(cid=1, device='fixture', pub=b'pub', state='ready', p33=False, send=NS(encrypt=encrypt))
        self.h = NS(agent_cfg={'dir': str(self.cwd)}, sessions={1: self.s}, send_lock=asyncio.Lock(), _op=AsyncMock(),
                    st=NS(log=lambda *a, **k: None, is_allowed=lambda _: True))

    async def send(self, message):
        await Host.send_app(self.h, self.s, message)
        return self.frames[-1]

    def test_switch_defaults_off(self):
        self.assertIs(display_privacy.ENABLED, False)

    def test_project_is_identity_and_never_scans(self):
        msg = {'t': 'msg', 'text': 'see https://example.com/docs?page=1 and ~/coding/README.md, ok = true, x'}
        with patch.object(display_privacy, 'index', side_effect=AssertionError('must not scan')):
            self.assertIs(display_privacy.project(self.h, msg), msg)
            self.assertEqual(display_privacy.text(self.h, 'ordinarypassword123'), 'ordinarypassword123')

    async def test_send_app_shows_original_text_with_overflowing_inventory(self):
        corpus = ['你好，今天的部署已完成。', 'ok', 'x', 'https://example.com/docs?page=1', 'see ~/coding/README.md',
                  'port 8080, took 12 s, ok = true', 'commit c607b74a04 release(0.30.0)', '好的，我看一下 /etc/hosts 第 3 行']
        for text in corpus:
            out = await self.send({'t': 'msg', 'text': text})
            self.assertEqual(out['text'], text)
            self.assertNotIn('<redacted>', json.dumps(out, ensure_ascii=False))
        nested = {'t': 'msg', 'content': [{'type': 'text', 'id': 'b1', 'text': 'hello'}], 'files': [{'name': 'a.md'}]}
        out = await self.send(dict(nested))
        self.assertEqual(out['content'], nested['content'])
        self.assertEqual(out['files'], nested['files'])

    async def test_scan_failure_is_not_fail_closed(self):
        from agentj import peer_guard
        with patch.object(peer_guard.SecretIndex, 'require_complete', side_effect=OSError('synthetic incomplete')):
            out = await self.send({'t': 'msg', 'text': 'visible during scan failure'})
        self.assertEqual(out['text'], 'visible during scan failure')


if __name__ == '__main__':
    unittest.main()
