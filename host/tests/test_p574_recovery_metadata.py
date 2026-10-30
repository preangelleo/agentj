import _hermetic  # noqa: F401
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentj import handoff, provenance
import test_p570b_resid as prior


class RecoveryMetadata(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_and_privileged_shapes_cannot_become_recovery_authority(self):
        fixture = prior.Shared()
        candidates = [None, 'unknown', object(), {'kind':'phone', 'role':'owner'},
                      {'kind':'phone', 'otg_resume':'not-a-valid-handle'},
                      {'kind':'phone', 'otg_resume':'a'*32, 'device':'untrusted'}]
        for candidate in candidates:
            with self.subTest(kind=type(candidate).__name__), tempfile.TemporaryDirectory() as td:
                host, agent = fixture.agent(td)
                host.peers = SimpleNamespace(xfer=SimpleNamespace(recovery_source=lambda _:candidate))
                agent._registry_quiet = lambda:True
                with patch.object(handoff,'RECOVER_DELAY',0), patch('agentj.shared.claude_send') as send:
                    await agent._recover('compact',True,agent.turn_began_at)
                expected = provenance.system()
                if isinstance(candidate,dict) and candidate.get('otg_resume') == 'a'*32:
                    expected['otg_resume'] = 'a'*32
                self.assertEqual(send.call_args.args[2],expected)

    async def test_missing_recovery_capability_uses_system_source(self):
        fixture = prior.Shared()
        for peers in [None, SimpleNamespace(), SimpleNamespace(xfer=SimpleNamespace(recovery_source=None))]:
            with self.subTest(peers=peers), tempfile.TemporaryDirectory() as td:
                host, agent = fixture.agent(td)
                host.peers = peers
                agent._registry_quiet = lambda:True
                with patch.object(handoff,'RECOVER_DELAY',0), patch('agentj.shared.claude_send') as send:
                    await agent._recover('compact',True,agent.turn_began_at)
                self.assertEqual(send.call_args.args[2],provenance.system())
