"""P501b Codex native search/image turn in an empty-root Linux sandbox.

Port of the verified experimental command contract; no shell, plugins, local
execution, owner files, sockets or instructions. Only auth is mounted read-only
for the harness itself. Images are discovered by the host, never by model paths.
Other native executors remain tool-less until their policy broker is integrated.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
from . import peer_session
from .channel_policy import channel_forbidden

MAX_IMAGE = 25 * 1024 * 1024


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def command(exe, root, cfg, env, sid=None, images=()):
    """No writable host bind other than this avatar's private scratch tree."""
    exe, root = Path(exe), Path(root)
    bundled = exe.parent.parent / 'codex-resources/bwrap'
    bwrap = str(bundled) if bundled.is_file() else shutil.which('bwrap')
    auth = Path(peer_session._codex_home(env)) / 'auth.json'
    code_host = exe.with_name('codex-code-mode-host')
    if sys.platform != 'linux' or not bwrap or not auth.is_file() or not code_host.is_file():
        raise ValueError('native_image_unavailable')
    a = [bwrap, '--die-with-parent', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--new-session',
         '--ro-bind','/usr','/usr', '--ro-bind','/etc','/etc', '--ro-bind','/etc/resolv.conf','/run/systemd/resolve/stub-resolv.conf', '--symlink','usr/bin','/bin',
         '--symlink','usr/lib','/lib', '--symlink','usr/lib64','/lib64', '--dir','/proc', '--dev','/dev', '--tmpfs','/tmp',
         '--dir','/avatar-home',
         '--ro-bind',str(exe),'/codex-bin', '--dir','/proc/self', '--symlink','/codex-bin','/proc/self/exe', '--ro-bind',str(code_host),'/codex-code-mode-host',
         '--ro-bind',str(root),'/avatar', '--bind',str(root / 'outbox'),'/avatar/outbox', '--bind',str(root / 'codex'),'/codex',
         '--ro-bind',str(auth),'/codex/auth.json', '--bind',str(root / 'outbox'),'/codex/generated_images',
         '--chdir','/avatar']
    child_env = {'HOME':'/avatar-home', 'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'CODEX_HOME':'/codex'}
    for name in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY','https_proxy','http_proxy','all_proxy','no_proxy'):
        if env.get(name):
            child_env[name] = env[name]
    if channel_forbidden('image.generate') or channel_forbidden('web.search'):
        raise ValueError('channel_forbidden')
    features = peer_session.codex_features(str(exe))
    if not {'shell_tool', 'image_generation', 'code_mode_host'} <= features:
        raise ValueError('native_image_unavailable')
    a += ['/codex-bin','--enable','image_generation','--enable','code_mode_host','--search',
          '-c','approval_policy="never"','-c','sandbox_mode="read-only"','-c','service_tier="default"']
    for feature in peer_session.CODEX_DISABLE:
        if feature in features and feature not in ('image_generation','code_mode_host'):
            a += ['--disable',feature]
    user = peer_session.codex_user_model(env)
    if user.get('model_provider') not in (None, 'openai'):
        raise ValueError('native_profile_unavailable')
    model = cfg.get('model') or user.get('model')
    if model:
        a += ['-c', 'model=' + json.dumps(model)]
    effort = cfg.get('effort') or user.get('model_reasoning_effort')
    if effort:
        a += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
    a += ['exec'] + (['resume',sid] if sid else [])
    a += ['--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json']
    for name in images:
        a += ['-i', '/avatar/input/' + name]
    a += ['-']
    return a, child_env


class NativeCodex:
    def __init__(self, sessions):
        self.sessions = sessions
        self.lock = asyncio.Lock()

    async def turn(self, friend, group, wrapped):
        s = self.sessions
        gen = s._gen
        async with self.lock:
            if gen != s._gen:
                return peer_session._fail('stopped')
            root = peer_session.sandbox_root(s.st) / ('native-' + peer_session._safe(friend['id']))
            for directory in (root, root / 'codex', root / 'outbox', root / 'input'):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                if directory.is_symlink() or directory.resolve() != directory.absolute():
                    return peer_session._fail('fence')
                directory.chmod(0o700)
            fid = str(friend['id'])
            sid = s.store.session_id(fid, 'native-codex')
            try:
                exe = await asyncio.to_thread(s._exe, 'codex')
                image_names = []
                peer_session._empty_dir(root / 'input')
                for data in getattr(s, 'input_images', ())[:4]:
                    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE or not image_type(data):
                        continue
                    name = secrets.token_hex(8) + '.png'
                    s.st.write_private(root / 'input' / name, data)
                    image_names.append(name)
                argv, child_env = await asyncio.to_thread(command, exe, root, s.cfg(), os.environ, sid, image_names)
            except (OSError, TypeError, ValueError):
                return peer_session._fail('native_image_unavailable')
            before = {p.relative_to(root / 'outbox') for p in (root / 'outbox').rglob('*')}
            out, err, rc = await s._run(argv, str(root), child_env, s.system_prompt(friend, group) + '\n' + wrapped, gen)
            if gen != s._gen or rc == 'stopped':
                return peer_session._fail('stopped')
            if rc == 'timeout':
                return peer_session._fail('timeout')
            if rc != 0:
                detail = peer_session.PeerSessions.parse_codex(out, '')['detail'] or peer_session.failure_detail(err, tail=3)
                return peer_session._fail('login' if detail == 'login' else 'harness', detail)
            completed = any(json.loads(line).get('type') == 'turn.completed' for line in out.splitlines() if line.startswith('{'))
            parsed = peer_session.PeerSessions.parse_codex(out, '')
            # Native image/search/code-mode transport events are expected. All
            # actual executable tools remain disabled in argv, regardless of text.
            forbidden = [t for t in parsed['tools'] if t not in ('image_generation','image_generation_call','web_search','web_search_call','mcp_tool_call')]
            if forbidden:
                return peer_session._fail('channel_forbidden')
            if parsed['error'] or not completed:
                # P540: a failed native turn waits with its fixed reason instead of becoming an owner handoff
                detail = parsed['detail'] or 'no_reply'
                return peer_session._fail('login' if detail == 'login' else 'harness', detail)
            decision, text, topic, ok = peer_session.parse_decision(parsed['text'])
            result = peer_session.TurnResult(decision, text, topic, parsed['tokens'], None, parsed['sid'], parsed=ok)
            result.images = []
            if not channel_forbidden('image.generate'):
                for path in sorted((root / 'outbox').rglob('*')):
                    if (path.relative_to(root / 'outbox') in before or path.is_symlink() or not path.is_file()
                            or not path.resolve().is_relative_to((root / 'outbox').resolve()) or not 0 < path.stat().st_size <= MAX_IMAGE):
                        continue
                    data = path.read_bytes()
                    if image_type(data):
                        path.chmod(0o600)
                        result.images.append((image_type(data), data))
                        if len(result.images) >= 4:
                            break
            if parsed['sid'] and __import__('re').fullmatch(r'[A-Za-z0-9_-]{1,128}', parsed['sid']):
                s.store.set_session_id(fid, 'native-codex', parsed['sid'])
            return result
