"""P506 #275: real pairing/bind/retry seams; no production bot or key."""
import _hermetic  # noqa: F401
import asyncio
import os
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from agentj import telegram as tg, tg_avatar, preferences as prefs
from agentj.state import State
from test_p283_tg_pair import _Host
from _testtmp import TEST_TMP

KEY = 'AJ_TEST_P506_KEY'

class Avatar(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'AGENTJ_STATE_DIR':str(Path(self.tmp.name)/'st'), KEY:'synthetic-old'})
        env.start(); self.addCleanup(env.stop)
        self.st = State(); self.st.init()
        self.host = _Host(self.st); self.t = tg.Telegram(self.host)
        self.calls = []; self.bot = 11; self.fail_photo = False
        self.p = patch.object(tg, 'api', self.api); self.p.start(); self.addCleanup(self.p.stop)
        self.u = patch.object(tg, 'upload', return_value=True); self.u.start(); self.addCleanup(self.u.stop)

    def api(self, cfg, method, values, *args):
        self.calls.append((self.bot, method))
        if method == 'getMe': return {'id':self.bot, 'username':'test_bot'}
        if method == 'getUserProfilePhotos':
            if self.fail_photo: raise tg.TelegramError('network')
            return {'total_count':0}
        return []

    async def settled(self):
        task = getattr(self.t, 'avatar_task', None)
        self.assertIsNotNone(task, 'bound bot must schedule an avatar check')
        await asyncio.wait_for(task, 2)

    async def pair_new(self):
        await self.t.start_pairing(KEY)
        pr = self.t.pairing
        with patch.object(tg, 'IN_SERVE', True):
            got = await self.t.pair_message({'from':{'id':123}, 'chat':{'id':123,'type':'private'},
                'text':'/start '+pr.code, 'date':int(time.time())})
        self.assertEqual(got, 'bound')
        return tg.configuration(self.st)

    async def test_same_key_new_bot_reload_channel_add_bound_recovery_error(self):
        cfg = await self.pair_new(); await self.t.bind(cfg); await self.settled()
        self.assertEqual(tg_avatar.recorded(self.st.root, 11), 'set')
        # reload invalidates bot_id; the same registered NAME now holds a new bot token.
        os.environ[KEY] = 'synthetic-new'; self.t.bot_id = None; self.bot = 22
        cfg = await self.pair_new()
        with patch.object(self.t, 'recover', side_effect=OSError('synthetic recovery error')):
            with self.assertRaises(OSError): await self.t.bind(cfg)
        await self.settled()
        self.assertEqual(tg_avatar.recorded(self.st.root, 22), 'set')
        self.assertEqual(sum(m=='getUserProfilePhotos' for _,m in self.calls), 2)

    async def test_poll_retry_without_rebind_after_photo_failure(self):
        cfg = await self.pair_new(); self.fail_photo = True
        await self.t.bind(cfg); await self.settled()
        self.assertIsNone(tg_avatar.recorded(self.st.root, self.bot))
        self.assertTrue(any(r.get('ev')=='telegram_avatar' and r.get('result')=='failed' for r in map(json.loads, self.st.log_path.read_text().splitlines())))
        self.fail_photo = False
        # The next normal polling iteration must retry even though enrollment/bot_id are already set.
        async def drained(_): self.host.stopping.set()
        with patch.object(self.t, 'bind', side_effect=AssertionError('must not rebind')), \
             patch.object(self.t, 'drain', side_effect=drained), patch.object(tg, 'AVATAR_RETRY', 0, create=True):
            self.t._avatar_retry = (None, 0)
            await self.t._run()
        await self.settled()
        self.assertEqual(tg_avatar.recorded(self.st.root, self.bot), 'set')
