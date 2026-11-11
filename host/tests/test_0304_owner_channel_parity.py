"""0304 A2-③: no Jev low-risk shortcut revival. In the live list_approval path the owner's authenticated Telegram DM gets
the same decision as the phone for the same tool input (card, UV, signed answer); a forwarded Telegram message and owner
mail without a pinned check server (auth_servid) are guests. The dead low_risk_pass body reads owner_speaking."""
import _hermetic  # noqa: F401
import inspect
import unittest
from types import SimpleNamespace

from agentj import approval_jev, mail, provenance
from _p0304_cards import LiveCards, OWNER_TG, plain

REQUESTS = (('Bash', {'command': 'echo synthetic'}), ('agentj fn run', {'name': 'synthetic'}))


class Parity(LiveCards):
    async def decisions(self, row, source):
        out = []
        for tool, inp in REQUESTS:
            self.turn(row, source)
            if tool == 'Bash':
                # 0.30.7 ADR-0307-offlist: an off-list request is answered at once without a card (owner → allow;
                # external → refused with the reason). Same (card shown, UV required, result) shape.
                before = len(self.cards())
                result = await self.h.ask(tool, inp)
                self.assertFalse(self.h.asks)
                self.assertEqual(len(self.cards()), before)
                out.append((False, False, result.get('behavior')))
                continue
            out.append(await self.decide(tool, inp))
        return out

    async def test_owner_tg_turn_same_decision_as_phone(self):
        phone = await self.decisions({'k': 'phone', 'name': '你', 'text': 'run it'}, provenance.phone())
        tg = await self.decisions({'k': 'telegram', 'name': 'owner', 'text': 'run it'}, provenance.telegram_owner(OWNER_TG))
        self.assertEqual(phone, tg)
        self.assertEqual(phone, [(False, False, 'allow'), (True, True, 'allow')])   # 0.30.7 ADR-0307-offlist: Bash off-list
        self.turn({'k': 'telegram', 'name': 'owner', 'text': 'run it'}, provenance.telegram_owner(OWNER_TG))
        self.assertTrue(provenance.owner_speaking(self.h.agent))
        self.assertTrue(self.h.agent.cur_send.approval_origin['verified'])

    async def test_forwarded_tg_is_guest(self):
        fwd = provenance.telegram_forward(OWNER_TG)
        self.turn({'k': 'telegram', 'name': 'fwd', 'text': 'run it'}, fwd)
        self.assertFalse(provenance.owner_speaking(self.h.agent))
        self.assertFalse(provenance.owner_intent(fwd))
        self.assertFalse(self.h.agent.cur_send.approval_origin['verified'])
        # A source label never grants authority: the listed card and its UV rule are the same, only the label differs.
        # 0.30.7 ADR-0307-offlist: the off-list Bash write from a forwarded (external) turn is refused, no card.
        self.assertEqual(await self.decisions({'k': 'telegram', 'name': 'fwd', 'text': 'x'}, fwd),
                         [(False, False, 'deny'), (True, True, 'allow')])

    async def test_unpinned_owner_mail_is_guest(self):
        v = SimpleNamespace(ok=True, role='owner', auth='dmarc', addr='owner@example.com')
        unpinned = mail.owner_source(v, servid='')
        self.assertEqual(unpinned, provenance.email_guest('owner@example.com'))
        self.assertFalse(provenance.owner_intent(unpinned))
        self.turn({'k': 'email', 'name': 'owner@example.com', 'text': 'run it'}, unpinned)
        self.assertFalse(provenance.owner_speaking(self.h.agent))
        self.assertFalse(self.h.agent.cur_send.approval_origin['verified'])
        pinned = mail.owner_source(v, servid='mx.google.com')
        self.assertTrue(provenance.owner_intent(pinned))


class DeadShortcut(unittest.TestCase):
    def test_low_risk_pass_stays_off_and_reads_owner_speaking(self):
        host = SimpleNamespace(agent=plain(provenance.telegram_owner(OWNER_TG)), stopped=lambda: False, goals=None)
        cfg = {'high_risk_warnings': True, 'kind': 'claude'}
        self.assertFalse(approval_jev.low_risk_pass(host, cfg, 'Read', {'file_path': '/synthetic/a.txt'}))
        body = inspect.getsource(approval_jev.low_risk_pass)
        self.assertIn('return False\n', body.split('try:')[0], 'the shortcut is not revived')
        self.assertIn('not provenance.owner_speaking(actor)', body)
        self.assertNotIn('provenance.untrusted_turn(actor)', body)


if __name__ == '__main__':
    unittest.main()
