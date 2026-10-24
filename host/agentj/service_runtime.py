"""P495: fixed service runtime presence projection, safe on the existing local status socket.

serve is intentionally non-dumpable. It reports its own fixed variable-presence booleans;
no environment value, proxy URL, digest or arbitrary name is exposed. Equal presence does
not prove equal PATH contents, HOME location, credentials or proxy settings.
"""
from __future__ import annotations
import os

NAMES = ('PATH', 'HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
         'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')


def snapshot(environ=None) -> dict:
    env = os.environ if environ is None else environ
    return {k: bool(env.get(k)) for k in NAMES}


def valid(value) -> bool:
    return isinstance(value, dict) and set(value) == set(NAMES) and all(type(v) is bool for v in value.values())
