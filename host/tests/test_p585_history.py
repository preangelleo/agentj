"""P585 real ingress → Host.history → browser fixture export; all transports are local synthetic fixtures."""
import _hermetic
import asyncio
import copy
import json
import os
from pathlib import Path
import unittest
from test_p562_acceptance import WeChatAcceptance, TelegramAcceptance, MailAcceptance, _Real
from test_p585_same_owner import Handoff
from test_p71_peer_service import pmsg


def capture(lang, kind, row):
    row = copy.deepcopy(row)
    row['kind'] = kind
    dest = os.environ.get('P585_HISTORY_FIXTURE')
    if dest:
        path = Path(dest)
        data = json.loads(path.read_text()) if path.exists() else {'zh': [], 'en': []}
        data[lang] = [x for x in data[lang] if x['kind'] != kind] + [row]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


class WeChatHistory(WeChatAcceptance):
    async def test_export(self):
        for lang in ('zh', 'en'):
            self.host.lang = lang
            words = '核查这份微信原文。' if lang == 'zh' else 'Review this original WeChat message.'
            await self.receive(words)
            send = self.sends()[-1]
            self.answer_turn(send, '微信任务已完成。' if lang == 'zh' else 'WeChat request completed.')
            await asyncio.gather(*list(self.c.card_tasks))
            row = self.page(send.turn)
            self.assertEqual(row['src']['text'], words)
            capture(lang, 'wechat', row)


class TelegramHistory(TelegramAcceptance):
    async def test_export(self):
        for lang in ('zh', 'en'):
            self.host.lang = lang
            words = '核查这份电报原文。' if lang == 'zh' else 'Review this original Telegram message.'
            await self.t.handle_round([self.update(words)], self.cfg)
            send = self.sends()[-1]
            self.answer_turn(send, '电报任务已完成。' if lang == 'zh' else 'Telegram request completed.')
            await self.flush()
            row = self.page(send.turn)
            self.assertEqual(row['src']['text'], words)
            capture(lang, 'telegram', row)


class EmailHistory(MailAcceptance):
    async def test_export(self):
        for lang in ('zh', 'en'):
            self.host.lang = lang
            words = '核查这份邮件原文。' if lang == 'zh' else 'Review this original email message.'
            await self.deliver(words)
            send = self.sends()[-1]
            await self.reply_with_card(send, '邮件任务已完成。' if lang == 'zh' else 'Email request completed.')
            row = self.page(send.turn)
            self.assertIn(words, row['src']['text'])
            capture(lang, 'email', row)


class SameOwnerHistory(Handoff):
    async def test_export(self):
        from test_l1 import _host
        await self.confirm()
        host = _host(self.host.st, [])
        host.agent = self.host.agent
        host.agent.halting = False
        self.host = host
        self.svc.host = host
        with self.store._edit('friends.json', {}) as rows:
            rows[self.b]['card']['name'] = 'Warden AWS'
        for lang in ('zh', 'en'):
            host.lang = lang
            words = '检查服务并回报原文。' if lang == 'zh' else 'Check the service and report the result.'
            await self.svc.same_owner.forward(self.b, pmsg(words))
            send = self.sends[-1]
            from unittest.mock import patch, AsyncMock
            with patch.object(self.svc.same_owner, 'status', AsyncMock(return_value={'ok': True})):
                self.assertTrue(await send.wait_ready())
            _Real.answer_turn(self, send, '检查已完成。' if lang == 'zh' else 'Service check completed.')
            row = host.hist.get(send.turn)
            self.assertEqual(row['src']['text'], words)
            self.assertEqual(row['src']['name'], 'Warden AWS')
            capture(lang, 'same-owner', row)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls('test_export') for cls in (WeChatHistory, TelegramHistory, EmailHistory, SameOwnerHistory))
