import _hermetic
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from agentj import busy
from test_p71_peer_service import Base, pmsg

class Lifetime(unittest.TestCase):
    def test_live_pid_outlives_ttl_without_completion(self):
        with tempfile.TemporaryDirectory() as root:
            now = time.time()
            busy.write_item(root, busy.item('live', 'worker', 'ceo', since=now-busy.ITEM_TTL-1, pid=os.getpid()))
            busy.write_item(root, busy.item('no-pid', 'worker', 'ceo', since=now-busy.ITEM_TTL-1))
            self.assertEqual([x['key'] for x in busy.read_items(root, now)], ['live'])
            with patch.object(busy, '_alive', return_value=False):
                self.assertEqual(busy.read_items(root, now), [])
    def test_other_namespace_still_requires_fresh_heartbeat(self):
        with tempfile.TemporaryDirectory() as root:
            now = time.time()
            p = busy.write_item(root, busy.item('fence', None, 'ceo', since=now-busy.ITEM_TTL-1, pid=1, ns='other'))
            os.utime(p, (now, now))
            self.assertEqual(len(busy.read_items(root, now)), 1)
            self.assertEqual(busy.read_items(root, now+busy.STALE+1), [])

class Cards(Base):
    async def test_cli_lists_and_dismisses_without_reply_or_grant(self):
        k = self.friend()
        self.svc._question(k.id, pmsg('test'), 'draft', 'ask')
        cards = await self.svc.cli({'t':'asks'})
        self.assertEqual(len(cards['asks']), 1)
        rid = cards['asks'][0]['id']
        self.assertEqual(await self.svc.cli({'t':'asks','dismiss':rid}), {'ok':True,'dismissed':1})
        self.assertFalse(self.svc.asks)
        self.svc.restore_asks()
        self.assertFalse(self.svc.asks)
        self.assertFalse(self.svc.jobs)
        self.assertEqual((await self.svc.cli({'t':'asks','dismiss':'unknown'}))['why'], 'shape')
    async def test_clear_all_and_persisted_restore(self):
        k = self.friend()
        self.svc._question(k.id, pmsg('test'), 'draft', 'ask')
        self.assertEqual(self.svc.dismiss_asks(), 1)
        self.svc.restore_asks()
        self.assertFalse(self.svc.asks)


from test_p332_reset import Serve, sign
import asyncio
from unittest import mock

class Rescue(Serve):
    def test_signed_clear_and_unsigned_refusal(self):
        async def run():
            ph, other, host, s, s2 = self.host()
            host.peers = mock.Mock()
            host.peers.dismiss_asks.return_value = 2
            bad = {'t':'rescue_do','r':'bad419','action':'clear_friend_asks'}
            await host._app(s, bad)
            host.peers.dismiss_asks.assert_not_called()
            good = sign(ph, 'rescue_do', {'action':'clear_friend_asks'}, {'t':'rescue_do','r':'ok419','action':'clear_friend_asks'})
            await host._app(s, good)
            await host.reset_desk.task
            host.peers.dismiss_asks.assert_called_once_with()
            self.assertTrue(any(x.get('ok') for x in self.got('reset_done')))
        asyncio.run(run())

class AskCLI(unittest.TestCase):
    def test_all_alias_and_id_are_exclusive_and_use_existing_deny_request(self):
        import argparse,contextlib,io
        from agentj import peer_service
        parser=argparse.ArgumentParser()
        peer_service.add_parser(parser.add_subparsers(dest='cmd'))
        for flags,expected in [([],{'t':'asks'}),(['--all'],{'t':'asks','dismiss':'all'}),(['--dismiss','all'],{'t':'asks','dismiss':'all'}),(['--dismiss','ask1'],{'t':'asks','dismiss':'ask1'})]:
            a=parser.parse_args(['friends','asks',*flags,'--json'])
            self.assertEqual(peer_service._req(a),expected);self.assertTrue(a.json_sub)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            parser.parse_args(['friends','asks','--all','--dismiss','ask1'])
