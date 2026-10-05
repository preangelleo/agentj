"""Proxy variables for the harness child processes Agent J starts (owned Claude Code / Codex / OpenCode, and the Claude
Code a shared session spawns). Nothing else: ASR, the TTS command, updates and the relay connection never read it.

Two ways per variable (P59, ADR-A167):
- a direct value, `proxy.https` / `proxy.http` / `proxy.no_proxy` — e.g. a local Clash `http://127.0.0.1:7890`. Validated:
  scheme http/https/socks5/socks5h, a host and a port, nothing else. A URL with user:password@ is refused (credentials never
  live in preferences, history or logs): an authenticated proxy goes into an environment variable and `proxy.https_env`
  names it (P49);
- a variable NAME, `proxy.*_env` (P49): the value is read from the host's own environment at launch.
The value wins when both are set. `proxy.http` empty = follows `proxy.https`; with any direct value and no NO_PROXY from
anywhere, loopback is bypassed (`localhost,127.0.0.1,::1`). Each target is set in both spellings (HTTPS_PROXY and
https_proxy): several clients prefer the lower-case one, so a stale lower-case value must not win.
"""
import os
import re
from urllib.parse import urlsplit

from .preferences import get

TARGETS = (('https', 'HTTPS_PROXY'), ('http', 'HTTP_PROXY'), ('no_proxy', 'NO_PROXY'))
SCHEMES = ('http', 'https', 'socks5', 'socks5h')
LOOPBACK = 'localhost,127.0.0.1,::1'
_HOST = re.compile(r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*')
_BYPASS = re.compile(r'[A-Za-z0-9.*_:\[\]/-]{1,253}')


def url_problem(field: str, value: str) -> str | None:
    """Why a direct proxy URL is refused (None = fine). The value itself is never part of the message."""
    if not value:
        return None
    if '@' in value:
        return (f'a proxy with user:password@ is not stored in preferences: keep it in an environment variable and set '
                f'proxy.{field}_env to that variable NAME / 带账号密码的代理不写进配置：放进环境变量，再把变量名填到 proxy.{field}_env')
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        return 'expected scheme://host:port, e.g. http://127.0.0.1:7890 / 格式：http://127.0.0.1:7890'
    host = u.hostname or ''
    ip6 = host.count(':') >= 2 and re.fullmatch(r'[0-9a-fA-F:.]+', host)
    if (u.scheme.lower() not in SCHEMES or not host or not (ip6 or _HOST.fullmatch(host)) or port is None
            or not 1 <= port <= 65535 or u.path not in ('', '/') or u.query or u.fragment):
        return ('expected scheme://host:port with scheme http, https, socks5 or socks5h, e.g. http://127.0.0.1:7890 / '
                '格式：http(s)://主机:端口 或 socks5://主机:端口，例如 http://127.0.0.1:7890')
    return None


def bypass_problem(value: str) -> str | None:
    if not value:
        return None
    if '@' in value:
        return 'NO_PROXY lists hosts only / 只填主机名或网段'
    items = [x.strip() for x in value.split(',')]
    if len(items) > 64 or any(not _BYPASS.fullmatch(x) for x in items):
        return 'comma-separated hosts, domains or CIDR, e.g. localhost,127.0.0.1,.internal / 用逗号分隔主机名、域名或网段'
    return None


def environment(base=None, preferences=None):
    env = dict(os.environ if base is None else base)
    prefs = preferences or {}
    values = {f: get(prefs, 'proxy.' + f, '') or '' for f, _ in TARGETS}
    if not values['http'] and values['https'] and not get(prefs, 'proxy.http_env', ''):
        values['http'] = values['https']
    direct = bool(values['https'] or values['http'])
    for field, target in TARGETS:
        value, name = values[field], get(prefs, 'proxy.' + field + '_env', '')
        if value:
            pass
        elif name:
            value = env.get(name) or None      # a configured but missing source removes the target (P49)
        else:
            if field == 'no_proxy' and direct and not (env.get('NO_PROXY') or env.get('no_proxy')):
                value = LOOPBACK
            else:
                continue                        # nothing configured: the host's own environment, untouched
        for k in (target, target.lower()):
            if value:
                env[k] = value
            else:
                env.pop(k, None)
    return env
