"""#437 security round 1.

M1  a plaza request (host install or the `--owner-confirmed` card) from a gated turn is refused AT ONCE (group_source /
    owner_grant_scope): no fetch, no install thread, no queue slot — and when the owner's turn starts meanwhile, still no card.
L4  an unsigned package's inline-code refusal keeps package text out of VerifyRefused.short (the host card).
Synthetic plaza + synthetic phone (test_plaza_host_install fixtures); HOME / XDG / state are the fixtures' temp dirs.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import unittest
from unittest import mock

import test_plaza_host_install as hi
from agentj import market, plaza_perms, provenance

GROUP_TURN = {"kind": "telegram", "role": "group", "sender": 4242, "chat": -4243}


class GatedNow(hi.HostRequest):
    for _n in [n for n in dir(hi.HostRequest) if n.startswith("test_")]:
        locals()[_n] = None
    del _n

    def owner_turn(self):
        self.host.agent.native_source = provenance.terminal()

    def test_gated_host_request_refused_before_fetch_queue_or_card(self):
        self.package()
        real, calls = market.run_install, []

        def run_install(*a, **kw):                # if a gated request ever runs: the group turn has ended meanwhile
            calls.append(a)
            self.owner_turn()
            return real(*a, **kw)
        self.host.agent.native_source = GROUP_TURN
        with mock.patch.object(market, "run_install", side_effect=run_install):
            res, _ = self.run_host(self.req())
        self.assertEqual((res["ok"], res["why"]), (False, "group_source"), res)
        self.assertEqual(calls, [], "no fetch / install thread for a gated request")
        self.assertEqual(self.cards(), [], "no card, also not later under the owner's turn")
        self.assertEqual(self.host.__dict__.get("_plaza_installing", set()), set(), "no queue slot")
        self.assertFalse(self.installed())
        self.host.agent.native_source = {"kind": "same_owner", "role": "task", "fid": "synthetic-peer", "otg_required": True}
        with mock.patch.object(market, "run_install", side_effect=run_install):
            res, _ = self.run_host(self.req())
        self.assertEqual((res["why"], calls, self.cards()), ("owner_grant_scope", [], []))

    def test_gated_owner_confirmed_card_refused_before_the_card_queue(self):
        self.host.agent.native_source = GROUP_TURN
        real = plaza_perms.card_turn.__aenter__
        entered = []

        async def enter(this):
            entered.append(1)
            self.owner_turn()                     # the card waited its turn; the owner's turn has begun
            return await real(this)
        req = {"name": "demo-flow", "version": "1.2.0", "sha256": "a" * 64, "items": [{"kind": "tool", "name": "WebFetch"}]}
        with mock.patch.object(plaza_perms.card_turn, "__aenter__", enter):
            res = asyncio.run(plaza_perms.serve_card(self.host, req))
        self.assertEqual(res, {"ok": False, "why": "group_source"})
        self.assertEqual((entered, self.cards()), ([], []))


class InlineShort(unittest.TestCase):
    def test_l4_short_is_fixed_words(self):
        with self.assertRaises(market.VerifyRefused) as cm:
            market.parse_verify("python3 -cIGNOREPREV", certified=False)
        self.assertEqual(cm.exception.short, ("用了内联代码", "uses inline code"))
        self.assertIn("-cIGNORE", str(cm.exception), "the Agent's message still names the argument")


if __name__ == "__main__":
    unittest.main()
