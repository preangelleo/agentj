"""OpenCode free turn over its native server, in an empty-root Linux sandbox.

No personal HOME, login, provider config or secret variables enter this process.
Session rules deny executable tools; pending permissions are rejected by the host.
Server errors are read from messages rather than waiting on the synchronous POST.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import contextlib
import shutil
import sys
import time
import re
from . import opencode_free, peer_session
from .channel_policy import channel_forbidden

_CATALOGUE = {}


def catalogue(exe):
    """Refresh public models.dev metadata in an empty HOME, never personal auth.

    The same bytes enter the isolated server: a bundled, withdrawn catalogue
    must not qualify a free route. No paid model or credential is a fallback.
    """
    hit = _CATALOGUE.get(exe)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1], hit[2]
    if not exe:
        return [], None
    with tempfile.TemporaryDirectory(prefix='agentj-channel-models-') as directory:
        env = {k:v for k,v in __import__('os').environ.items()
               if k in ('PATH','LANG') or k.lower() in ('http_proxy','https_proxy','all_proxy','no_proxy')}
        env.update(HOME=directory, XDG_CONFIG_HOME=directory+'/config', XDG_DATA_HOME=directory+'/data',
                   XDG_STATE_HOME=directory+'/state', XDG_CACHE_HOME=directory+'/cache',
                   OPENCODE_DISABLE_DEFAULT_PLUGINS='1', OPENCODE_DISABLE_CLAUDE_CODE='1',
                   OPENCODE_DISABLE_AUTOUPDATE='1', OPENCODE_DISABLE_SHARE='1')
        try:
            result = subprocess.run([exe,'models','opencode','--refresh','--verbose'], env=env, cwd=directory,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
            from .opencode_catalogue import native_models, retired_models_cli
            if retired_models_cli(result):
                # 2.1 discovery only. No v1 cache or isolated avatar execution is qualified here.
                free = native_models(exe, 25) or []
                if free:
                    _CATALOGUE[exe] = (time.monotonic(), free, None)
                return free, None
            path = Path(directory)/'cache/opencode/models.json'
            if result.returncode or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
                return [], None
            raw = path.read_bytes()
            models = json.loads(raw)['opencode']['models']
            free = []
            for model in opencode_free.parse_verbose(result.stdout):
                row = models.get(model.split('/',1)[1]) or {}
                cost = row.get('cost') or {}
                if cost.get('input') == 0 and cost.get('output') == 0 and row.get('status') != 'deprecated':
                    free.append(model)
            if free:
                _CATALOGUE[exe] = (time.monotonic(), free, raw)
                return free, raw
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            pass
    return [], None


def option(exe):
    """Catalogue discovery stays visible; no real isolated reply is qualified yet."""
    models, _ = catalogue(exe)
    return {'id':'free', 'kind':'opencode', 'models':models, 'selectable':False,
            'why':'free_temporarily_unavailable',
            'label':{'zh':'免费模型：暂不可用', 'en':'Free models: temporarily unavailable'}}


def command(exe, root, port, password, proxy):
    from .agent_opencode import opencode_env
    exe, root = Path(exe), Path(root)
    bwrap = shutil.which('bwrap')
    if sys.platform != 'linux' or not bwrap or not exe.is_file():
        raise ValueError('free_isolation_unavailable')
    env = opencode_env({'HOME':'/avatar-home', 'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8',
        'XDG_CONFIG_HOME':'/avatar-home/config', 'XDG_DATA_HOME':'/avatar-home/data',
        'XDG_STATE_HOME':'/avatar-home/state', 'XDG_CACHE_HOME':'/avatar-home/cache',
        'OPENCODE_DISABLE_DEFAULT_PLUGINS':'1', 'OPENCODE_DISABLE_CLAUDE_CODE':'1',
        'OPENCODE_CONFIG_CONTENT':'{"plugin":[],"mcp":{},"instructions":[]}'}, password)
    env.update({k:v for k,v in proxy.items() if k.lower() in ('https_proxy','http_proxy','all_proxy','no_proxy')})
    argv = [bwrap, '--die-with-parent', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--new-session',
        '--ro-bind','/usr','/usr', '--ro-bind','/etc','/etc',
        '--ro-bind','/etc/resolv.conf','/run/systemd/resolve/stub-resolv.conf',
        '--symlink','usr/bin','/bin', '--symlink','usr/lib','/lib', '--symlink','usr/lib64','/lib64',
        '--proc','/proc', '--dev','/dev', '--tmpfs','/tmp', '--tmpfs','/avatar-home',
        '--ro-bind',str(exe),'/opencode-bin', '--bind',str(root),'/avatar',
        '--dir','/avatar-home/cache/opencode',
        '--ro-bind',str(root/'models.json'),'/avatar-home/cache/opencode/models.json',
        '--ro-bind',str(root/'models.json'),'/avatar/models.json', '--chdir','/avatar']
    # Password/config stay in the child environment, never in argv or logs.
    return argv + ['/opencode-bin','serve','--hostname','127.0.0.1','--port',str(port)], env


def rules():
    from .agent_opencode import session_rules, _rule
    out = session_rules([], research=True)
    for action in ('bash','edit','write','webfetch','websearch','skill','lsp','task','question','todowrite'):
        out.append(_rule(action, '*', 'deny'))
    out.extend((_rule('read','*','deny'), _rule('read','/avatar/*','allow')))
    return out


def message_result(messages):
    """(finished, text, tokens, error); any tool event refuses the turn."""
    for msg in reversed(messages if isinstance(messages, list) else []):
        info = msg.get('info') or {}
        if info.get('role') != 'assistant':
            continue
        if info.get('error'):
            return True, '', None, ('free_quota' if 'free usage limit reached' in str(info['error']).lower() else opencode_free.classify(info['error']))
        parts = msg.get('parts') or []
        if any(p.get('type') == 'tool' for p in parts):
            return True, '', None, 'channel_forbidden'
        if (info.get('time') or {}).get('completed'):
            text = '\n'.join(p.get('text','') for p in parts if p.get('type') == 'text')
            tokens = info.get('tokens') or {}
            total = sum(v for k,v in tokens.items() if k in ('input','output','reasoning') and type(v) is int)
            return True, text, total or None, None
    return False, '', None, None


class NativeFree:
    def __init__(self, sessions):
        self.sessions = sessions
        self.lock = asyncio.Lock()

    async def turn(self, friend, group, wrapped):
        # P501d: also refuse persisted selections. No provider call or paid fallback.
        return peer_session._fail('free_temporarily_unavailable')

    async def _native_turn(self, friend, group, wrapped):
        """Dormant transport retained for isolated qualification, not a user route."""
        from .agent_opencode import free_port, split_model, Client, HTTPError
        s, gen = self.sessions, self.sessions._gen
        async with self.lock:
            if gen != s._gen:
                return peer_session._fail('stopped')
            cfg = s.cfg()
            exe = await asyncio.to_thread(s._exe, 'opencode')
            models, raw_catalogue = await asyncio.to_thread(catalogue, exe)
            if cfg.get('model') not in (models or []) or not str(cfg.get('model','')).startswith('opencode/'):
                return peer_session._fail('free_unavailable')
            root = peer_session.sandbox_root(s.st) / ('free-' + secrets.token_hex(8))
            root.mkdir(mode=0o700, parents=True)
            (root/'models.json').write_bytes(raw_catalogue)
            (root/'models.json').chmod(0o600)
            port, password = free_port(), secrets.token_urlsafe(32)
            deadline = time.monotonic() + peer_session.TURN_TIMEOUT
            proc = None
            client = Client(port, password)
            event_task = None
            errors = []
            connected = asyncio.Event()
            sid = None

            async def call(method, path, body=None):
                status, result = await client.request(method, path, body, timeout=3)
                if status not in (200, 204):
                    raise ValueError('native_http_failed')
                return result

            async def watch():
                try:
                    async for event in client.events():
                        connected.set()
                        prop = event.get('properties') or {}
                        if event.get('type') == 'session.status' and prop.get('sessionID') == sid:
                            status = prop.get('status') or {}
                            text = str(status.get('message') or '').lower()
                            if status.get('type') == 'retry':
                                if 'free usage limit reached' in text:
                                    errors.append('free_quota')
                                elif 'model is unavailable' in text:
                                    errors.append('free_model_unavailable')
                                elif 'freetiererror' in text:
                                    errors.append('free_tier')
                        if event.get('type') == 'session.error' and prop.get('sessionID') == sid:
                            error = prop.get('error') or {}
                            errors.append('free_quota' if 'free usage limit reached' in str(error).lower()
                                          else opencode_free.classify(error))
                except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
                    errors.append('free_unavailable')

            try:
                if channel_forbidden('answer'):
                    return peer_session._fail('channel_forbidden')
                proxy = peer_session.clean_env('opencode', preferences=getattr(s.host,'preferences',None))
                argv, env = command(exe, root, port, password, proxy)
                # The subprocess receives only this explicit environment: no
                # inherited login, provider keys, config paths or control sockets.
                proc = await asyncio.create_subprocess_exec(*argv, env=env, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                s._procs.add(proc)
                startup = min(deadline, time.monotonic() + 20)
                while gen == s._gen and proc.returncode is None and time.monotonic() < startup:
                    try:
                        await call('GET','/global/health')
                        break
                    except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
                        await asyncio.sleep(.1)
                else:
                    return peer_session._fail('stopped' if gen != s._gen else 'free_unavailable')
                session = await call('POST','/session',{'title':'Agent J external channel','permission':rules()})
                sid = session['id']
                if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',sid):
                    raise ValueError('native_session_invalid')
                event_task = asyncio.create_task(watch())
                await asyncio.wait_for(connected.wait(), 5)
                await call('POST','/session/' + sid + '/prompt_async',{
                    'model':split_model(cfg['model']), 'system':s.system_prompt(friend, group),
                    'parts':[{'type':'text','text':wrapped}]})
                while gen == s._gen and proc.returncode is None and time.monotonic() < deadline:
                    if errors:
                        return peer_session._fail(errors[0])
                    pending = await call('GET','/permission')
                    for req in pending if isinstance(pending,list) else []:
                        rid = req.get('id','')
                        if isinstance(rid,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',rid):
                            await call('POST','/permission/' + rid + '/reply',{'reply':'reject'})
                    messages = await call('GET','/session/' + sid + '/message')
                    done, text, tokens, error = message_result(messages)
                    if gen != s._gen:
                        return peer_session._fail('stopped')
                    if done:
                        if error:
                            return peer_session._fail(error)
                        if channel_forbidden('answer'):
                            return peer_session._fail('channel_forbidden')
                        decision, text, topic, parsed = peer_session.parse_decision(text)
                        return peer_session.TurnResult(decision,text,topic,tokens,None,parsed=parsed)
                    await asyncio.sleep(.2)
                return peer_session._fail('stopped' if gen != s._gen else 'timeout')
            except (OSError, ValueError, KeyError, TypeError, HTTPError, asyncio.TimeoutError):
                return peer_session._fail('stopped' if gen != s._gen else 'free_unavailable')
            finally:
                if event_task is not None:
                    event_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_task
                if proc is not None:
                    await s._kill(proc)
                    s._procs.discard(proc)
                await asyncio.to_thread(shutil.rmtree, root, True)
