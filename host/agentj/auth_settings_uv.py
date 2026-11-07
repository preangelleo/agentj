"""#454: five-minute, exact-Noise-session UV scope for settings add/re-login only.

Only _answer, after its existing device signature and WebAuthn verification, can
mint this memory-only scope. It never authorizes account deletion or an Agent tool.
"""
from __future__ import annotations
import hashlib
import json
import time

SECONDS = 300
ACTIONS = frozenset(('add', 'login'))


def _binding(host, session):
    if session is None or not host._paired_live(session) or host.stopped():
        return None
    if getattr(host, 'stopping', None) is not None and host.stopping.is_set():
        return None
    sk = host.st.sign_key(session.device)
    pk = host.st.passkey_of_device(session.device)
    if not sk or not pk or not all(pk.get(k) is not None for k in ('id', 'alg', 'spki', 'rp')):
        return None
    # Counter changes are expected after a valid UV; identity/replacement is not.
    pub = {k: pk.get(k) for k in ('id', 'alg', 'spki', 'rp', 'local', 'dev')}
    return (host.channel, session.device, sk, hashlib.sha256(json.dumps(pub,sort_keys=True).encode()).hexdigest())


def clear(host, session=None):
    records = host.__dict__.get('_auth_settings_uv', {})
    if session is None:
        records.clear()
    else:
        records.pop(id(session), None)


def mint(host, session, ask):
    """Called only after verified signed UV: never from an input flag or tool name."""
    if (not ask.host_card or ask.tool != 'AuthRescue' or ask.settings_session is not session
            or not ask.require_passkey or not ask.uv_ok or ask.only != session.device):
        return False
    verified_at = ask.settings_uv_at
    if not isinstance(verified_at, (int, float)) or not 0 <= time.monotonic() - verified_at < SECONDS:
        return False
    binding = _binding(host, session)
    if binding is None:
        return False
    host.__dict__.setdefault('_auth_settings_uv', {})[id(session)] = {
        'session':session, 'binding':binding, 'until':verified_at+SECONDS, 'rid':ask.rid}
    host.st.log('auth_settings_uv_open', device=session.device, id=ask.rid, secs=SECONDS, scope='add_login')
    return True


def reuse(host, session, action):
    if action not in ACTIONS or session is None:
        return False
    records = host.__dict__.get('_auth_settings_uv', {})
    rec = records.get(id(session))
    if not rec:
        return False
    if (rec['session'] is not session or time.monotonic() >= rec['until']
            or rec['binding'] != _binding(host, session)):
        records.pop(id(session), None)
        return False
    host.st.log('auth_settings_uv_reuse', device=session.device, id=rec['rid'], action=action, scope='add_login')
    return True


def note(lang):
    return ('For 5 minutes, adding another AI account or signing in again in these settings needs no further confirmation.'
            if lang == 'en' else '此后5分钟，在本设置页添加另一个AI账号或重新登录免重复确认。')
