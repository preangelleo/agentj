"""External avatar image input: Claude stream-json and OpenCode v1 serve file parts.

Image bytes travel only on child stdin / authenticated loopback HTTP. The peer fence,
tool denials and output parser remain authoritative; no image-reading tool is enabled.
"""
from __future__ import annotations
import asyncio
import base64
import json
import re
import secrets
import time
from . import peer_session


class Unsupported(ValueError):
    pass


def unsupported(text):
    return bool(re.search(r'(?is)(?:does not support|not support(?:ed)?|unsupported|cannot (?:read|process))[^\n]{0,90}(?:image|vision)|(?:image|vision)[^\n]{0,90}(?:not support|unsupported|not allowed)', text))


def blocks(images):
    from .channel_native import MAX_IMAGE, image_type
    out = []
    if len(images) > 4:
        raise Unsupported()
    for data in images:
        if not isinstance(data, bytes) or len(data) > MAX_IMAGE:
            raise Unsupported()
        mime = image_type(data)
        if not mime:
            raise Unsupported()
        out.append((mime, data))
    return out


def claude_input(text, images):
    content = [{'type':'text', 'text':text}]
    content += [{'type':'image', 'source':{'type':'base64', 'media_type':mime,
                 'data':base64.b64encode(data).decode()}} for mime, data in images]
    return json.dumps({'type':'user', 'message':{'role':'user', 'content':content},
                       'parent_tool_use_id':None}, ensure_ascii=False) + '\n'


async def opencode_run(s, exe, cwd, env, text, gen, cfg, images, sid=None):
    from .agent_opencode import Client, HTTPError, free_port, split_model
    from .channel_plans import load
    row = load(cfg)
    if row and row.get('vision') is False:
        raise Unsupported()
    port, password = free_port(), secrets.token_urlsafe(32)
    env = dict(env, OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME='opencode')
    argv = peer_session.fenced(s.st, [exe, 'serve', '--hostname', '127.0.0.1', '--port', str(port)], cwd, env)
    client = Client(port, password)
    proc = None
    async def call(method, path, body=None, timeout=3):
        status, value = await client.request(method, path, body, timeout=timeout)
        if status not in (200, 204):
            if unsupported(json.dumps(value)):
                raise Unsupported()
            raise ValueError('channel_image_http')
        return value
    try:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        s._procs.add(proc)
        deadline = time.monotonic() + min(20, peer_session.TURN_TIMEOUT)
        while gen == s._gen and proc.returncode is None and time.monotonic() < deadline:
            try:
                health = await call('GET', '/global/health')
                if isinstance(health, dict) and health.get('healthy'):
                    break
            except (OSError, ValueError, HTTPError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(.1)
        else:
            return '', '', 'stopped' if gen != s._gen else 'timeout'
        model = split_model(cfg.get('model') or json.loads(env['OPENCODE_CONFIG_CONTENT']).get('model') or '')
        providers = await call('GET', '/provider')
        if not model:
            defaults = providers.get('default') or {}
            if len(defaults) == 1:
                pid, mid = next(iter(defaults.items()))
                model = {'providerID':pid, 'modelID':mid}
        meta = next((p.get('models', {}).get(model.get('modelID'), {}) for p in providers.get('all', [])
                     if p.get('id') == model.get('providerID')), {}) if model else {}
        # Refuse before inference: OpenCode otherwise replaces unsupported images by a text note.
        if (meta.get('capabilities', {}).get('input', {}).get('image') is not True):
            raise Unsupported()
        if not sid:
            session = await call('POST', '/session', {'title':'Agent J external channel',
                'permission':[{'permission':'*','pattern':'*','action':'deny'}]})
            sid = session['id']
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
            raise ValueError('channel_image_session')
        parts = [{'type':'text','text':text}] + [{'type':'file', 'mime':mime,
            'url':'data:' + mime + ';base64,' + base64.b64encode(data).decode()} for mime, data in images]
        if gen != s._gen:
            return '', '', 'stopped'
        result = await call('POST', '/session/' + sid + '/message',
            {'agent':peer_session.PEER_AGENT, 'model':model, 'parts':parts, 'tools':{'*':False}}, peer_session.TURN_TIMEOUT)
        if gen != s._gen:
            return '', '', 'stopped'
        info, events = result.get('info') or {}, []
        if info.get('error'):
            if unsupported(json.dumps(info['error'])):
                raise Unsupported()
            events.append({'type':'error'})
        for part in result.get('parts') or []:
            if part.get('type') in ('text','tool','tool_use'):
                events.append({'type':part['type'], 'part':part})
        if isinstance(info.get('tokens'), dict):
            events.append({'type':'step_finish','part':{'tokens':info['tokens']}})
        return '\n'.join(json.dumps(dict(e, sessionID=sid)) for e in events), '', 0
    except (HTTPError, OSError, asyncio.TimeoutError):
        return '', '', 'stopped' if gen != s._gen else 'timeout'
    finally:
        if proc is not None:
            await s._kill(proc)
            s._procs.discard(proc)
