"""Resolve proxy variables locally. Preferences and diagnostics contain NAMES only."""
import os
from .preferences import get


def environment(base=None, preferences=None):
    env = dict(os.environ if base is None else base)
    for field, target in (('https_env','HTTPS_PROXY'),('http_env','HTTP_PROXY'),('no_proxy_env','NO_PROXY')):
        name = get(preferences or {}, 'proxy.' + field, '')
        if name:
            if env.get(name): env[target] = env[name]
            else: env.pop(target, None)
    return env
