"""Fixed Host setup stages. Native effects reuse existing installer/card paths.

Legacy manifest verification remains for compatibility; native installation uses
the existing official/npm trust chain and requires no new installation manifest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import platform
import re
import time

from . import minisign, setup_ops

MAX_DOWNLOAD = 300 * 1024 * 1024


from .setup_errors import RuntimeErrorCode


@dataclass(frozen=True)
class Artifact:
    data: bytes
    manifest: bytes
    signature: str


def verify_artifact(artifact: Artifact, cli: str, source: str) -> None:
    """Authenticate manifest bytes BEFORE trusting hash or installing packages.

    Signer is the existing compiled-in release-purpose keyring, never supplied by
    the request/platform. No new pins or signing roots are invented here.
    """
    if len(artifact.data) > MAX_DOWNLOAD or len(artifact.manifest) > 4096:
        raise RuntimeErrorCode('checksum')
    try:
        comment = minisign.verify(artifact.manifest, artifact.signature, minisign.keyring('release'))
        doc = json.loads(artifact.manifest)
        if (comment != 'agentj-install-manifest/v1' or set(doc) != {'cli','source','sha256','size','platform','architecture','version','expires_at'}
                or doc['cli'] != cli or doc['source'] != source
                or doc['platform'] != platform.system().lower()
                or doc['architecture'] != platform.machine().lower()
                or not isinstance(doc['version'], str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', doc['version'])
                or type(doc['expires_at']) is not int or not time.time() < doc['expires_at'] <= time.time() + 86400
                or type(doc['size']) is not int or doc['size'] != len(artifact.data)
                or doc['sha256'] != hashlib.sha256(artifact.data).hexdigest()):
            raise ValueError('manifest')
    except (ValueError, TypeError, KeyError, minisign.MinisignError):
        raise RuntimeErrorCode('checksum') from None


class UnqualifiedPlatform:
    """Production default. A typed unavailable dependency, never a pretend install."""
    async def sys_check(self):
        raise RuntimeErrorCode('unavailable')

    async def download(self, cli, source):
        raise RuntimeErrorCode('unavailable')

    async def package_install(self, cli, artifact):
        raise RuntimeErrorCode('unavailable')

    async def owner_secret_card(self, provider, owner_device):
        # Future backend MUST use elevate's sealed, signed card with only=owner
        # and a fixed destination. No plaintext input/output method is provided.
        raise RuntimeErrorCode('unavailable')

    async def owner_login(self, cli, owner_device):
        raise RuntimeErrorCode('unavailable')

    async def ai_probe(self, cli, model):
        raise RuntimeErrorCode('unavailable')

    async def ai_connect(self, cli, model):
        raise RuntimeErrorCode('unavailable')

    async def service_apply(self):
        raise RuntimeErrorCode('unavailable')

    async def rollback_connect(self):
        raise RuntimeErrorCode('unavailable')

    async def browser_setup(self):
        raise RuntimeErrorCode('unavailable')


class Runtime:
    """Finite execution graph; guard and audit are called at every effect boundary.

    Backends expose separate operations, cannot accept shell/URL/path requests,
    and must cancel their subprocess/network/card work on task cancellation.
    Return values are not exposed; receipts are fixed metadata only.
    """
    def __init__(self, platform=None, host=None):
        if platform is None and host is not None:
            platform = NativePlatform(host)
        self.platform = platform if platform is not None else UnqualifiedPlatform()

    async def execute(self, op, params, owner_device, guard, audit):
        params = setup_ops.validate(op, params)  # repeat schema at effect boundary
        async def stage(name, fn, *args):
            guard()
            audit(name)
            value = await fn(*args)
            guard()
            if name != 'download' and value is not True:
                raise RuntimeErrorCode({'owner_secret_card': 'key_failed', 'owner_login': 'login_failed',
                                        'ai_probe': 'call_failed', 'ai_connect': 'connect_failed',
                                        'service_apply': 'no_service'}.get(name, 'error'))
            return value
        p = self.platform
        if isinstance(p, NativePlatform):
            p.owner(owner_device)
        if op == 'sys.check':
            # Backend facts must be consumed by a planner, never forwarded raw.
            await stage('system_check', p.sys_check)
            facts = await p.facts() if isinstance(p, NativePlatform) else {}
            return {'checked': True, **facts}
        if op == 'ai.install':
            cli, source = params['cli'], params.get('source', 'official')
            if isinstance(p, NativePlatform):
                await stage('package_install', p.install, cli, source, owner_device)
                return {'cli':cli, 'source':source, 'installed':True}
            artifact = await stage('download', p.download, cli, source)
            guard()
            verify_artifact(artifact, cli, source)
            audit('verified_download')
            await stage('package_install', p.package_install, cli, artifact)
            return {'cli': cli, 'source': source, 'installed': True}
        if op == 'ai.key':
            await stage('owner_secret_card', p.owner_secret_card, params['provider'], owner_device)
            return {'provider': params['provider'], 'saved': True}
        if op == 'ai.login':
            await stage('owner_login', p.owner_login, params['cli'], owner_device)
            return {'cli': params['cli'], 'signed_in': True}
        if op == 'ai.probe':
            await stage('ai_probe', p.ai_probe, params['cli'], params.get('model'))
            return {'cli': params['cli'], 'reply_verified': True}
        if op == 'ai.connect':
            # No implicit model probe or install; connect/service are distinct.
            await stage('ai_connect', p.ai_connect, params['cli'], params.get('model'))
            try:
                await stage('service_apply', p.service_apply)
            except BaseException:
                # Rollback only restores this backend's transaction; no arbitrary
                # path/config document comes from the requesting device.
                await p.rollback_connect()
                raise
            return {'cli': params['cli'], 'connected': True}
        if op == 'browser.setup':
            await stage('browser_setup', p.browser_setup)
            return {'ready': True}
        raise RuntimeErrorCode('unavailable')

# Imported after RuntimeErrorCode is defined (native adapter shares typed failures).
from .setup_native import NativePlatform
