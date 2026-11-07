"""Fixed native setup effects. No caller shell, URL, filename or secret input."""
from __future__ import annotations
import asyncio
import contextlib
import os
from pathlib import Path
import platform
import signal
import sys
import tempfile

import urllib.error

from .setup_errors import RuntimeErrorCode

KEY_NAMES = {'agentsrelay':'ANTHROPIC_AUTH_TOKEN', 'anthropic':'ANTHROPIC_API_KEY', 'openai':'OPENAI_API_KEY'}


def secret_card(st, provider):
    from . import elevate, service
    name = KEY_NAMES[provider]
    # Existing fixed destination; no account/secret read or verify/model request.
    path = service.env_file(service.name())
    Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return elevate.norm_secret({'name': name,
        'purpose': 'Owner API key for ' + provider + ' / 主人的 API 密钥，仅保存在此主机',
        'dest': f'env:{path}#{name}'}, st.root)


async def owned_process(argv, *, env, cwd, timeout, spawn=asyncio.create_subprocess_exec):
    """Only this newly-created process group is cancelled; reap before returning."""
    task = asyncio.create_task(spawn(*argv, env=env, cwd=cwd, start_new_session=True,
                                   stdin=asyncio.subprocess.DEVNULL,
                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL))
    proc = None
    try:
        # Cancellation while spawning still transfers ownership to our cleanup.
        proc = await asyncio.shield(task)
        return await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        raise RuntimeErrorCode('timeout') from None
    finally:
        if proc is None:
            proc = await task
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except asyncio.TimeoutError:
            pass
        # Reap descendants too: the group belongs solely to the worker we created.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()


class NativePlatform:
    def __init__(self, host):
        self.host = host

    def owner(self, device):
        h = self.host
        # Pairing is established by Host, never by a socket uid or role JSON.
        paired = getattr(h, '_paired_live', None)
        if paired is None:
            raise RuntimeErrorCode('unavailable')
        for s in h.sessions.values():
            if s.device == device and paired(s) and h.st.sign_key(device):
                return s
        raise RuntimeErrorCode('no_device')

    async def sys_check(self):
        # No native CLI invocation/auth detection, environment or host file reads.
        return True

    async def facts(self):
        system = platform.system().lower()
        machine = platform.machine().lower()
        return {'system':system if system in ('linux','darwin','windows') else 'other',
                'arch':machine if machine in ('x86_64','amd64','aarch64','arm64') else 'other'}

    async def install(self, cli, source, owner_device):
        self.owner(owner_device)
        return await self._worker('install', cli, source)

    async def _worker(self, action, cli=None, source=None):
        # A killed worker's scratch/config/temp files are removed by the parent.
        root = self.host.st.root
        with tempfile.TemporaryDirectory(prefix='setup-native-', dir=root) as scratch:
            env = {k:os.environ[k] for k in ('HOME','PATH','LANG','SYSTEMROOT','AGENTJ_CLAUDE_BIN','AGENTJ_CODEX_BIN','AGENTJ_OPENCODE_BIN') if k in os.environ}
            env.update(TMPDIR=scratch, TEMP=scratch, TMP=scratch)
            argv = [sys.executable, '-m', 'agentj.setup_native', action, str(root), scratch]
            if action == 'install':
                argv += [cli, source]
            rc = await owned_process(argv, env=env, cwd=scratch, timeout=580 if action=='install' else 110)
            if rc:
                raise RuntimeErrorCode({20:'node_missing',21:'checksum',22:'unsupported_platform',23:'network'}.get(rc,
                                       'install_failed' if action=='install' else 'error'))
            return True

    async def owner_secret_card(self, provider, owner_device):
        self.owner(owner_device)
        card = secret_card(self.host.st, provider)
        card['only'] = owner_device
        result = await self.host.elevate.request(card)
        self.owner(owner_device)
        if result.get('result') != 'saved' or (result.get('receipt') or {}).get('verify') == 'fail':
            why = result.get('result')
            raise RuntimeErrorCode(why if why in ('denied','timeout','stopped','busy','no_device') else 'key_failed')
        # Only public routing constants are edited; no secret is read back here.
        if provider != 'openai':
            route_provider(provider)
        return True

    async def owner_login(self, cli, owner_device):
        self.owner(owner_device)
        return await login(self, cli, owner_device)

    async def ai_probe(self, cli, model):
        raise RuntimeErrorCode('unavailable')  # prohibited new model call

    async def ai_connect(self, cli, model):
        # switch_harness.request/worker gates include actual model calls. Never
        # bypass those transaction gates or start a model under this task policy.
        raise RuntimeErrorCode('unavailable')

    async def browser_setup(self):
        return await self._worker('browser')


def route_provider(provider):
    # Existing service env editor retains unrelated bytes; fixed nonsecret keys.
    from . import service
    from .setup_env import env_edit
    path = service.env_file(service.name())
    if provider == 'agentsrelay':
        env_edit(path, {'ANTHROPIC_BASE_URL':'https://agentsrelay.net'}, ('ANTHROPIC_API_KEY',))
    else:
        env_edit(path, {}, ('ANTHROPIC_BASE_URL','ANTHROPIC_AUTH_TOKEN'))


async def login(backend, cli, owner_device):
    """Original P224 PTY route, sealed code sink; no health/model probe."""
    from . import binaries, harness
    from .setup_login import LoginProcess, LOGIN_ARGV, login_url, _DEVCODE, _ANSI
    exe = binaries.resolve(cli)['path']
    if not exe:
        candidate = Path.home()/'.local/share/agentj-setup/npm/bin'/cli
        if candidate.is_file() and os.access(candidate, os.X_OK):
            exe = str(candidate)
    if not exe:
        raise RuntimeErrorCode('not_installed')
    env = {k:v for k,v in os.environ.items() if not k.startswith('AGENTJ_')}
    # PTY creation is synchronous so cancellation cannot lose the child handle.
    proc = LoginProcess([exe] + LOGIN_ARGV[cli], env)
    card_task = None
    try:
        url = None
        while proc.done() is None:
            backend.owner(owner_device)
            url = login_url(proc.read())
            if url:
                break
            await asyncio.sleep(.1)
        url = url or login_url(proc.read())
        if not url:
            raise RuntimeErrorCode('login_url')
        code = _DEVCODE.search(_ANSI.sub('', proc.read()))
        await backend.host._send_device(owner_device, {'t':'setup_op_ev','op':'ai.login','stage':'url',
            'url':url, **({'code':code.group(0)} if cli=='codex' and code else {})})
        if cli == 'claude':
            def sink(value):
                if not value or len(value)>4096 or b'\n' in value or b'\r' in value:
                    return {'result':'failed'}
                proc.write(bytes(value)+b'\r')
                return {'result':'saved'}
            card = {'kind':'secret', 'name':'CLAUDE_AUTH_CODE', 'purpose':'Owner Claude authorization code / 主人登录授权码',
                    'only':owner_device, 'dest':'Claude login (not stored)',
                    'dest_spec':{'mode':'login','path':'','key':'CLAUDE_AUTH_CODE','show':'claude'},
                    'cwd':'/', 'verify':'','verify_url':'','verify_header':'','verify_cmd':'','sink':sink}
            async def code_card():
                from . import provenance
                with provenance.paired_request(lambda: bool(backend.owner(owner_device))):
                    return await backend.host.elevate.request(card)
            card_task = asyncio.create_task(code_card())
        while proc.done() is None:
            backend.owner(owner_device)
            proc.read()
            if card_task and card_task.done() and card_task.result().get('result') != 'saved':
                raise RuntimeErrorCode('login_failed')
            await asyncio.sleep(.1)
        if proc.done() != 0 or (card_task and (not card_task.done() or card_task.result().get('result') != 'saved')):
            raise RuntimeErrorCode('login_failed')
        logged = harness.claude_login(env)[0] if cli=='claude' else harness.login_of(cli)[0]
        if logged not in ('ok','env'):
            raise RuntimeErrorCode('login_failed')
        return True
    finally:
        proc.close()
        if card_task and not card_task.done():
            card_task.cancel()
            await asyncio.gather(card_task, return_exceptions=True)


def worker(argv):
    action, root, scratch, *args = argv
    if action == 'install' and len(args)==2:
        from .setup_packages import install
        from . import binaries, service
        from .setup_env import env_edit
        cli, source = args
        if cli not in ('claude','codex','opencode') or source not in ('official','mirror'):
            return 1
        if binaries.resolve(cli)['path']:
            return 0  # retain the owner's selected existing executable
        prefix = Path.home()/'.local/share/agentj-setup/npm'
        try:
            install(cli, source, prefix, scratch)
            env_edit(service.env_file(service.name()), {binaries.ENV[cli]:str(prefix/'bin'/cli)})
        except ValueError as e:
            return {'node_missing':20,'checksum':21,'unsupported_platform':22,'network':23}.get(str(e), 1)
        except (TimeoutError, urllib.error.URLError) as e:
            return 1 if isinstance(e, urllib.error.HTTPError) else 23
        return 0
    if action == 'browser' and not args:
        from . import browser
        from .state import State
        return 0 if browser.setup(State(Path(root)), own=True).get('ok') is True else 1
    return 1


if __name__ == '__main__':
    try:
        sys.exit(worker(sys.argv[1:]))
    except Exception:
        sys.exit(1)  # fixed receipt; never disclose exception/path/env/credential
