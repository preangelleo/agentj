"""A verified stop classifies terminated sudo, never permits later work."""
import _hermetic  # noqa: F401
import asyncio
import unittest
import test_p406_sudo as p406


class SignedStop(unittest.TestCase):
    def denied_after_stop(self, change):
        async def steps(host, session, ph, card, sent, task, root):
            await host._app(session, p406.answer(ph, card, True, p406.PW))
            self.assertTrue(await p406.until(lambda: any(
                m.get('phase') == 'started' for m in p406.frames(sent, 'elev_progress', card['id'])), 3))
            await host._app(session, p406.sign(ph, 'elev_stop', {'id': card['id']},
                                             {'t': 'elev_stop', 'id': card['id']}))
            c = host.elevate.cards[card['id']]
            self.assertEqual(c['stop_by'], session.device)
            change(host, session, c)
            result = await asyncio.wait_for(asyncio.shield(task), 5)
            self.assertEqual((result['result'], result.get('why')), ('unknown', 'authority_changed'))
            self.assertFalse(host.elevate.grants.items())
        p406.Running().run_case("import time; print('before sleep', flush=True); time.sleep(60)", steps)

    def test_signed_stop_does_not_mask_epoch_change(self):
        def change(host, session, c): host.hist.epoch += 1
        self.denied_after_stop(change)

    def test_signed_stop_does_not_mask_revoked_device(self):
        self.denied_after_stop(lambda h, s, c: h.st.remove_device(s.device))

    def test_signed_stop_does_not_mask_removed_session(self):
        self.denied_after_stop(lambda h, s, c: h.sessions.pop(s.cid))

    def test_signed_stop_does_not_mask_revoked_card(self):
        self.denied_after_stop(lambda h, s, c: c.update(revoked=True))

    def test_unsigned_cancel_is_not_a_signed_stop(self):
        async def steps(host, session, ph, card, sent, task, root):
            await host._app(session, p406.answer(ph, card, True, p406.PW))
            self.assertTrue(await p406.until(lambda: any(
                m.get('phase') == 'started' for m in p406.frames(sent, 'elev_progress', card['id'])), 3))
            c = host.elevate.cards[card['id']]
            self.assertFalse(c.get('stop_by'))
            c['cancel'].set()
            result = await asyncio.wait_for(asyncio.shield(task), 5)
            self.assertEqual((result['result'], result.get('why')), ('unknown', 'authority_changed'))
        p406.Running().run_case("import time; print('before sleep', flush=True); time.sleep(60)", steps)
