"""Diagnostic evidence for the unimplemented Claude Relay admission boundary.

These do not claim Claude dispatch acceptance. No provider/auth value is loaded.
"""
import _hermetic
import asyncio
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentj import busy_native, fence, owner_avatar
from agentj.serve import Host


class ExistingClaudeBoundary(IsolatedAsyncioTestCase):
    async def test_existing_side_claim_does_not_capture_native_ceiling_or_source(self):
        host=SimpleNamespace(st=SimpleNamespace(perm_sock_path='/tmp/fixture-perm.sock'),ask_ttl=30)
        _,side=Host.side_perm_env(host,'diagnostic owner-avatar label')
        self.assertEqual(set(side),{'token','label','on_card','claimed'})
        self.assertNotIn('native_context',side)
        self.assertNotIn('sandbox',side)
        self.assertNotIn('approval_origin',side)
        # A launch_context argument exists on the main path, but the side path
        # delegates only the avatar label/card, not this native-session snapshot.
        host._perm_send=AsyncMock()
        writer=object();native_context=object()
        ask=AsyncMock(return_value={'behavior':'deny'})
        with patch.object(owner_avatar,'ask_for',ask):
            await Host._perm_ask(host,writer,{'id':'probe','tool':'Bash','input':{'command':'true'}},
                                 {},side=side,launch_context=native_context)
        self.assertEqual(ask.await_count,1)
        self.assertEqual(ask.call_args.kwargs,{})
        self.assertNotIn(native_context,ask.call_args.args)
        Host.side_perm_drop(host,side)
        self.assertFalse(host.side_perm)

    async def test_current_claude_cannot_satisfy_existing_native_policy(self):
        # Real process identity, no mocked owner flag or invented Claude sandbox.
        host=SimpleNamespace(agent=SimpleNamespace(kind='claude',cfg={'dir':'/tmp'}))
        request={'argv':['codex','exec','one task'],'cwd':'/tmp'}
        with self.assertRaisesRegex(ValueError,'verified current Codex session'):
            busy_native.policy(host,request,os.getpid(),os.getuid())

    async def test_claude_relay_model_key_is_not_a_supported_launch_capability(self):
        with self.assertRaisesRegex(ValueError,'unsupported native model key'):
            fence._native_model_keys('claude','AGENTSRELAY_CLAUDE_KEY')
