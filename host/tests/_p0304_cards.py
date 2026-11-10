"""0304 A2 shared fixture: a real Host (synthetic State, paired passkey phone) driving the live list_approval path."""
import _hermetic  # noqa: F401
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_review, provenance
from _p541_device import Phone, _owner_host
from test_l1 import _state, _ready


class LiveCards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.st = _state(tmp.name)
        self.sent = []
        self.h = _owner_host(self.st, self.sent)
        self.h.ask_ttl = 2
        self.ph = Phone(self.st, with_passkey=True)
        self.s = _ready(self.h, self.ph)
        self.h.agent.submit = mock.Mock()

    def turn(self, src, source, quote=None):
        """A host-recorded turn (src = the history row the adapter wrote) and the Host-held origin remember() builds."""
        row = dict(src)
        if quote is not None:
            row['quote'] = {'id': quote['id']}
        turn = self.h.hist_add(row, '', 'done')
        origin = approval_review.remember(self.h, turn, source)
        self.h.agent.cur_send = SimpleNamespace(approval_origin=origin, source=source)
        return origin

    def cards(self):
        return [o for _, o in self.sent if isinstance(o, dict) and o.get('t') == 'ask']

    async def open(self, tool='Bash', inp=None):
        before = len(self.cards())
        task = asyncio.create_task(self.h.ask(tool, inp or {'command': 'echo synthetic'}))
        for _ in range(200):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.002)
        if task.done():
            self.fail('no card: ' + str(task.result()))
        a = next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.close, task, a)
        for _ in range(200):                      # the card goes out right after the ask is registered
            if len(self.cards()) > before:
                break
            await asyncio.sleep(.002)
        self.assertGreater(len(self.cards()), before, 'the card reached the paired phone')
        return task, a

    async def close(self, task, a):
        if not a.fut.done():
            a.fut.set_result(('deny', None, None, None))
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def decide(self, tool, inp, allow=True):
        """(card shown, UV required, result of the owner's signed answer) — the list_approval decision for one request."""
        task, a = await self.open(tool, inp)
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(a, self.s), allow))
        result = await task
        self.h.asks.clear()
        return True, bool(a.require_passkey), result.get('behavior')


def otg_agent():
    return SimpleNamespace(cur_send=None, native_source={'kind': 'same_owner', 'role': 'task', 'fid': 'synthetic-peer',
                                                          'otg_required': True})


def plain(source):
    return SimpleNamespace(cur_send=None, native_source=source)


OWNER_TG = 4242001
assert provenance.normalize(provenance.telegram_owner(OWNER_TG))['role'] == 'owner'
