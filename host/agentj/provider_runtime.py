"""P67: configured provider metadata; keys stay in the host's child environment."""
import asyncio
import ipaddress
import json
import math
import os
import re
import shlex
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from . import service, __version__


def configured():
    from .opencode_provider import config_file
    try:
        value = json.loads(Path(config_file()).read_text())
        return {k: p for k, p in value.get('provider', {}).items() if isinstance(k, str) and isinstance(p, dict)} if isinstance(value, dict) and isinstance(value.get('provider'), dict) else {}
    except (OSError, ValueError):
        return {}


def key_name(provider):
    if provider == "bailian-coding-plan":
        return None
    p = configured().get(provider, {})
    opts = p.get('options') if isinstance(p.get('options'), dict) else {}
    val = opts.get('apiKey')
    m = re.fullmatch(r'\{env:([A-Za-z_][A-Za-z0-9_]*)\}', val) if isinstance(val, str) else None
    return m[1] if m else None


def options(p):
    return p.get('options') if isinstance(p.get('options'), dict) else {}


def env_path():
    return service.env_file(service.name())


def fresh_environment(base, extra=()):
    """Only configured env references (+ `extra` names: P340 D, OpenCode's built-in plan providers), no shell evaluation;
    never mutate parent env."""
    names = ({key_name(p) for p in configured()} | set(extra)) - {None}
    env = dict(base)
    try:
        lines = Path(env_path()).read_text().splitlines()
    except OSError:
        return env
    for line in lines:
        k, sep, v = line.strip().partition('=')
        if sep and k in names:
            try:
                parts = shlex.split(v, comments=False)
                if len(parts) == 1:
                    env[k] = parts[0]
                elif not parts:
                    env[k] = ''
            except ValueError:
                pass
    return env


def public_url(base, suffix):
    if not isinstance(base, str):
        return None
    try:
        u = urllib.parse.urlsplit(base)
    except ValueError:
        return None
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
        return None
    try:
        ips = socket.getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)
        if not ips or any(not ipaddress.ip_address(x[4][0]).is_global for x in ips):
            return None
    except (OSError, ValueError):
        return None
    path = u.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    return urllib.parse.urlunsplit((u.scheme, u.netloc, path + '/v1/' + suffix, '', ''))


def windows(data):
    if not isinstance(data, dict):
        return []
    sub = data.get('subscription') or {}
    out = []
    if not isinstance(sub, dict):
        return out
    for w in ('weekly', 'daily', 'monthly'):
        used, limit = sub.get(w+'_usage_usd'), sub.get(w+'_limit_usd')
        if type(used) in (int, float) and type(limit) in (int, float) and math.isfinite(used) and math.isfinite(limit) and 0 <= used and limit > 0:
            out.append({'window': w, 'used': used, 'limit': limit, 'pct': min(100, used/limit*100)})
    return out


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(provider):
    from . import coding_usage
    p = configured().get(provider)
    builtin = coding_usage.BUILTIN.get(provider)   # P340 D: OpenCode's own models.dev plan providers (no config block)
    base = options(p).get('baseURL') if p else None
    if not base and builtin:
        base = builtin[0]
    plan = coding_usage.endpoint(base)   # P340 D: a Coding Plan's own usage read (fixed hosts), else P67's compatible /v1/usage
    url = plan[1] if plan else public_url(base, 'usage')
    name = key_name(provider) or (builtin[1] if builtin and plan else None)
    key = fresh_environment(os.environ, (name,)).get(name) if name else None
    if not url or not key:
        return []
    auth = coding_usage.headers(plan[0], key) if plan else {'Authorization': 'Bearer '+key}
    try:
        req = urllib.request.Request(url, headers={**auth, 'User-Agent': 'agentj/'+__version__, 'Accept': 'application/json'})
        with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as r:
            data = json.loads(r.read(65537))
    except (OSError, ValueError):
        return []
    return coding_usage.parse(plan[0], data) if plan else windows(data)


async def request_key(host, provider):
    from . import elevate
    name = key_name(provider)
    if not name:
        return {'result': 'unsupported'}
    # One open card per provider, including automatic error retries.
    if not hasattr(host, '_provider_cards'):
        host._provider_cards = set()
    if provider in host._provider_cards:
        return {'result': 'busy'}
    host._provider_cards.add(provider)
    try:
        from .opencode_provider import NPM, HEADER
        p = configured().get(provider, {})
        api = next((api for api, npm in NPM.items() if p.get('npm') == npm), None)
        if not api:
            return {'result': 'unsupported'}
        from .bailian import verify_for
        from . import ark
        fixed = verify_for(provider)   # P310: Bailian's Anthropic base has no model list; its OpenAI one does (or none: Coding Plan)
        if fixed is None:
            fixed = ark.verify_for(provider)   # P315: Ark's coding address has no published read-only check
        url = fixed[0] if fixed else await asyncio.to_thread(public_url, options(p).get('baseURL'), 'models')
        if not url and not fixed:
            host.agent_notice('无法验证该服务商地址，暂不能打开密钥卡。' if getattr(host, 'lang', 'en') == 'zh' else 'Cannot verify this provider address; the key card is unavailable.')
            return {'result': 'unsupported'}
        card = elevate.norm_secret({'name': name, 'purpose': provider+' API Key',
            'dest': 'env:'+env_path()+'#'+name, 'verify_url': url or '', 'verify_header': (fixed[1] if fixed else HEADER[api]) if url else ''},
            host.st.root)
        card["verify_before_write"] = bool(url)
        return await host.elevate.request(card)
    finally:
        host._provider_cards.discard(provider)


def shared_launch(agent, argv):
    """Owned shared serve keeps native permissions, with requested filesystem isolation."""
    from . import fence
    agent.isolation_effective = False
    if not agent.cfg.get('fence', True):
        return argv
    why = fence.problem(agent.host.st, agent.cfg['dir'])
    if why:
        agent.host.st.log('agent_fence_fail', agent=agent.kind, reason=why, status='blocked')
        agent.local_fail('无法启动隔离环境，请在手机设置中检查隔离选项。' if agent.cfg.get('language', 'en').startswith('zh') else 'Cannot start requested isolation; check Isolation in phone settings.')
        return None
    wrapped = fence.wrap(agent.host.st, argv, agent.cfg['dir'], allow_docker=agent.cfg.get('docker', False))
    agent.isolation_effective = True
    return wrapped
