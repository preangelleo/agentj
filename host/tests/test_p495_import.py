"""#206 only registered safe sources; names only to main, existing signed import unchanged."""
import _hermetic
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from agentj import keys
from test_p362_keys_found import Base, OAUTH, ELEVEN, SUB


class Proposal(Base):
    def test_proposes_catalog_keys_without_values_and_deduplicates(self):
        note=keys.import_note(self.host)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN',note)
        self.assertIn('手机',note);self.assertIn('确认',note)
        self.assertNotIn(OAUTH,note);self.assertNotIn(ELEVEN,note)
        self.assertNotIn('ELEVENLABS_API_KEY',note)  # proactive scan is catalog names only
        self.assertEqual(keys.import_note(self.host),'')
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN',self.envf.read_text())

    def test_existing_managed_key_is_not_proposed(self):
        self.envf.write_text(SUB + '=' + 'managed-fixture\n')
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN',keys.import_note(self.host))

    def test_owner_message_receives_proposal(self):
        self.host.agent=SimpleNamespace(kind='claude',init={},passthrough=lambda n:False,submit=Mock())
        async def go():
            send=await self.host._accept(self.s,'hello',None)
            self.assertIn('CLAUDE_CODE_OAUTH_TOKEN',send.text)
            self.assertNotIn(OAUTH,send.text)
        asyncio.run(go())


class SlowDiscovery(Base):
    def test_slow_discovery_does_not_block_receive_lane_and_result_is_kept(self):
        import time
        from unittest.mock import patch
        async def go():
            def slow(_):
                time.sleep(.2)
                return 'names-only-fixture'
            with patch.object(keys,'import_note',side_effect=slow):
                start=time.monotonic()
                self.assertEqual(await self.host.key_import_note(),'')
                self.assertLess(time.monotonic()-start,.15)
                await asyncio.sleep(.25)
                self.assertEqual(await self.host.key_import_note(),'names-only-fixture')
        asyncio.run(go())
