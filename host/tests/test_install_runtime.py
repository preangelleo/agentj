"""Fake effects with real owner signatures; no subprocess/network/model calls."""
import asyncio
import unittest
from agentj import setup_runtime
import test_install_assist_host as boundary

class RuntimeBoundary(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request
    async def test_unsigned_artifact_never_reaches_package_manager(self):
        calls = []
        class Platform(setup_runtime.UnqualifiedPlatform):
            async def download(self, cli, source):
                calls.append(('download', cli, source))
                return setup_runtime.Artifact(b'not an installer', b'{}', 'invalid')
            async def package_install(self, cli, artifact):
                calls.append('INSTALL_MUST_NOT_RUN')
        self.runner.runtime = setup_runtime.Runtime(Platform())
        result = await self.runner.run(self.s, self.request('ai.install', {'cli':'claude'}))
        self.assertEqual(result['why'], 'checksum')
        self.assertEqual(calls, [('download', 'claude', 'official')])
    async def test_owner_card_is_targeted_and_secret_not_echoed(self):
        calls = []
        class Platform(setup_runtime.UnqualifiedPlatform):
            async def owner_secret_card(self, provider, owner_device):
                calls.append((provider, owner_device))
                return True
        self.runner.runtime = setup_runtime.Runtime(Platform())
        result = await self.runner.run(self.s, self.request('ai.key', {'provider':'openai'}))
        self.assertEqual(result['result'], {'provider':'openai','saved':True})
        self.assertEqual(calls, [('openai','owner')])
    async def test_failed_card_does_not_claim_saved(self):
        class Platform(setup_runtime.UnqualifiedPlatform):
            async def owner_secret_card(self, provider, owner_device):
                return False
        self.runner.runtime = setup_runtime.Runtime(Platform())
        result = await self.runner.run(self.s, self.request('ai.key', {'provider':'openai'}))
        self.assertFalse(result['ok'])
    async def test_connection_service_failure_rolls_back(self):
        calls=[]
        class Platform(setup_runtime.UnqualifiedPlatform):
            async def ai_connect(self, cli, model):
                calls.append('connect'); return True
            async def service_apply(self):
                calls.append('service')
                raise setup_runtime.RuntimeErrorCode('no_service')
            async def rollback_connect(self):
                calls.append('rollback')
        self.runner.runtime = setup_runtime.Runtime(Platform())
        result = await self.runner.run(self.s, self.request('ai.connect', {'cli':'codex'}))
        self.assertEqual(result['why'], 'no_service')
        self.assertEqual(calls, ['connect','service','rollback'])

class ArtifactCrypto(unittest.TestCase):
    def test_signed_manifest_hash_source_expiry_and_platform(self):
        import hashlib
        import json
        import platform
        import time
        from unittest.mock import patch
        from market_fixtures import TestKey
        from agentj import minisign
        key = TestKey()  # ephemeral synthetic key; no credential files
        data = b'synthetic artifact; never executed'
        manifest = {'cli':'claude','source':'official','size':len(data),
                    'sha256':hashlib.sha256(data).hexdigest(), 'platform':platform.system().lower(),
                    'architecture':platform.machine().lower(), 'version':'1.2.3','expires_at':int(time.time())+600}
        def artifact(doc, payload=data):
            raw = json.dumps(doc, sort_keys=True).encode()
            return setup_runtime.Artifact(payload, raw, key.sign(raw, 'agentj-install-manifest/v1'))
        # Substitute only the test release trust root; cryptographic verification
        # itself, hash comparison and all manifest checks remain real.
        with patch.object(minisign, 'keyring', return_value=key.keyring):
            setup_runtime.verify_artifact(artifact(manifest), 'claude', 'official')
            for doc in ({**manifest,'source':'mirror'}, {**manifest,'expires_at':1},
                        {**manifest,'platform':'foreign'}, {**manifest,'architecture':'foreign'}):
                with self.assertRaises(setup_runtime.RuntimeErrorCode):
                    setup_runtime.verify_artifact(artifact(doc), 'claude', 'official')
            with self.assertRaises(setup_runtime.RuntimeErrorCode):
                setup_runtime.verify_artifact(artifact(manifest,b'tampered'), 'claude', 'official')
        with self.assertRaises(setup_runtime.RuntimeErrorCode):
            setup_runtime.verify_artifact(artifact(manifest), 'claude', 'official')

class ErrorCodes(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request
    async def test_unknown_backend_code_is_not_logged_or_returned(self):
        class Platform(setup_runtime.UnqualifiedPlatform):
            async def owner_secret_card(self, provider, owner_device):
                raise setup_runtime.RuntimeErrorCode('SECRET_FROM_BACKEND')
        self.runner.runtime = setup_runtime.Runtime(Platform())
        result = await self.runner.run(self.s, self.request('ai.key', {'provider':'openai'}))
        self.assertEqual(result['why'],'error')
        self.assertNotIn('SECRET_FROM_BACKEND', str(result)+str(self.runner.events)+str(self.h.st.records))
