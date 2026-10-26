"""OpenCode 2.1 public price catalogue: isolated native HTTP, never owner auth/config.

The v2 CLI removed models --verbose. Use its own /api/model response instead of
inferring zero prices from names or the human-readable CLI table.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.request

from . import scratch


def retired_models_cli(result) -> bool:
    error = str(getattr(result, 'stderr', '') or '')
    return bool(result.returncode and 'Unrecognized flag:' in error and ('--verbose' in error or '--refresh' in error))


def free_models(rows) -> list[str] | None:
    """Unknown/incomplete catalogue is None; every price band must explicitly be zero."""
    from .opencode_free import _MODEL
    if not isinstance(rows, list):
        return None
    own = [r for r in rows if isinstance(r, dict) and r.get('providerID') == 'opencode']
    if not own:
        return None
    out, known = [], False
    for r in own:
        mid = 'opencode/' + str(r.get('id') or '')
        bands = r.get('cost')
        if (not _MODEL.fullmatch(mid) or r.get('enabled') is False or r.get('status') == 'deprecated'
                or not isinstance(bands, list) or not bands):
            continue
        if not all(isinstance(b, dict) and all(type(b.get(k)) in (int, float) and b[k] >= 0
                                              for k in ('input', 'output')) for b in bands):
            continue
        known = True
        if all(b['input'] == 0 and b['output'] == 0 for b in bands):
            out.append(mid)
    return list(dict.fromkeys(out)) if known else None


def native_models(exe: str, timeout: float = 20) -> list[str] | None:
    from .agent_opencode import opencode_env
    from .opencode_free import ProbeError, _port, _stop, serve
    deadline = time.monotonic() + timeout
    # A public catalogue must never import the owner's keys, plugins or instructions.
    env = {k:v for k,v in os.environ.items() if k in ('PATH', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
           or k.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    proc = None
    with scratch.home('agentj-oc2-catalogue-') as home:
        env.update(HOME=home, XDG_CONFIG_HOME=home+'/config', XDG_DATA_HOME=home+'/data',
                   XDG_CACHE_HOME=home+'/cache', XDG_STATE_HOME=home+'/state',
                   OPENCODE_DISABLE_DEFAULT_PLUGINS='1', OPENCODE_DISABLE_CLAUDE_CODE='1')
        password, port = secrets.token_urlsafe(32), _port()
        env = opencode_env(env, password)
        env.pop('OPENCODE_DISABLE_MODELS_FETCH', None)  # fresh public metadata, not an old owner cache
        auth = 'Basic ' + base64.b64encode(('opencode:'+password).encode()).decode()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            proc = serve(exe, env, home, port, deadline)
            while time.monotonic() < deadline:
                request = urllib.request.Request(f'http://127.0.0.1:{port}/api/model', headers={'Authorization':auth})
                with opener.open(request, timeout=max(.1, deadline-time.monotonic())) as response:
                    raw = response.read(16*1024*1024+1)
                    if len(raw) > 16*1024*1024:
                        return None
                    body = json.loads(raw)
                got = free_models(body.get('data') if isinstance(body, dict) else None)
                if got is not None:
                    return got
                time.sleep(.05)
        except (OSError, ValueError, ProbeError):
            return None
        finally:
            _stop(proc)
    return None
