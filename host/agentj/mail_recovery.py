"""P506: verify registered app-password candidates with the configured mailbox.

Historical secret cards need no account metadata. IMAP+SMTP success is the account
binding; values stay in this process and only the existing encrypted secret changes.
Only login connections are retried before SMTP DATA, never a send operation.
"""
from __future__ import annotations
import contextvars
import threading
import re
from . import keys, keys_found, elevate

_HOST = contextvars.ContextVar('mail_recovery_host', default=None)
_LOCK = threading.RLock()
PASSWORD_SUFFIXES = ('APP_SPECIFIC_PASSWORD', 'APP_PASSWORD', 'APP_PASS', 'AUTH_CODE', 'PASSWORD')
MAIL_NAMES = frozenset(('MAIL','EMAIL','GMAIL','SMTP','IMAP','QQ','FOXMAIL','NETEASE','OUTLOOK','HOTMAIL','ICLOUD','YAHOO','ALIMAIL','EXMAIL'))

def mailbox_name(name):
    return bool(set(name.split('_')) & MAIL_NAMES)


def run(host, fn, *args):
    token = _HOST.set(host)
    try:
        return fn(*args)
    finally:
        _HOST.reset(token)


def account_prefix(name):
    for suffix in PASSWORD_SUFFIXES:
        if name.endswith('_'+suffix):
            return name[:-len(suffix)]
    return None


def card_account(card):
    # Purpose is part of the phone-signed card digest; the value is never inspected.
    if account_prefix(str(card.get('name', ''))) is None:
        return None
    purpose = re.sub(r"[A-Za-z0-9._%+'=-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", '', card.get('purpose') or '')
    if not mailbox_name(card.get('name', '')) and not re.search(r'(?i)\b(?:gmail|e-?mail|mailbox|mail|smtp|imap)\b|邮箱|邮件', purpose):
        return None
    addresses = set(re.findall(r"[A-Za-z0-9._%+'=-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", card.get('purpose') or ''))
    return next(iter(addresses)).lower() if len(addresses)==1 else None


def candidates(host, cfg):
    managed = keys.env_path()
    sources = []
    paths = []
    bindings = {}
    # P423 already records safe destination metadata when a phone secret card saves.
    for row in sorted(elevate.read_results(host.st).values(), key=lambda r:r.get('at', 0), reverse=True):
        dest = row.get('where') or {}
        if row.get('result') == 'saved' and dest.get('mode') == 'env' and isinstance(dest.get('key'), str) and account_prefix(dest['key']):
            path = dest.get('path')
            if isinstance(path, str):
                if path not in paths:
                    paths.append(path)
                if row.get('mail_account') in (cfg['address'].lower(), (cfg.get('username') or cfg['address']).lower()):
                    bindings.setdefault(path, set()).add(dest['key'])
    if managed not in paths:
        paths.append(managed)
    for path in paths:
        try:
            sources.append({'values':keys_found.parse(keys_found.read_file(path)), 'bound':bindings.get(path,set())})
        except (keys_found.Unsafe, OSError, UnicodeError):
            continue
    sources.extend(keys_found.sources(host, managed))
    seen = set()
    from . import mail as M
    for source in sources:
        values = source.get('values') or {}
        for name in values:
            if account_prefix(name) is None:
                continue
            # Explicit app/auth-code names are candidates even without a mail label.
            # A generic PASSWORD needs a mail name or a recorded secret-card key.
            if not name.endswith(tuple('_'+s for s in PASSWORD_SUFFIXES[:-1])) and not mailbox_name(name) and name not in source.get('bound', ()):
                continue
            pw = values[name]
            if isinstance(pw, str) and M.password_provider(cfg) == 'gmail':
                pw = ''.join(pw.split())
            if not isinstance(pw, str) or not pw or len(pw)>512 or any(ord(c)<32 for c in pw) or pw in seen:
                continue
            seen.add(pw)
            yield pw


def recover(host, cfg, old):
    """Verify IMAP+SMTP without sending. Replace only the existing encrypted mail secret."""
    from . import mail as M
    with _LOCK:
        if M.read_config(host.st) != cfg:
            return None
        current = M.load_secret(host.st, cfg['address'])
        values = candidates(host, cfg)
        if current and current != old:
            values = iter((current, *values))
        for pw in values:
            if pw == old:
                continue
            # Verification cannot recursively start another recovery.
            token = _HOST.set(None)
            try:
                result = M.test_connection(cfg, pw)
            finally:
                _HOST.reset(token)
            if M.read_config(host.st) != cfg or M.load_secret(host.st, cfg['address']) != current:
                return None
            if result.get('ok') is not True:
                continue
            M.store_secret(host.st, bytearray(pw.encode()), cfg['address'])
            state = M.read_state(host.st)
            state['last_error'] = None
            M.save_state(host.st, state)
            host.st.log('mail_password_recovery', result='ok')
            return pw
    host.st.log('mail_password_recovery', result='unavailable')
    return None


def reconnect(why, cfg, old):
    """Called only after a login failed and its connection was closed."""
    host = _HOST.get()
    if host is None or why not in ('imap_login', 'smtp_login'):
        return None
    pw = recover(host, cfg, old)
    if pw is None:
        return None
    from . import mail as M
    token = _HOST.set(None)
    try:
        return (M.imap_login if why == 'imap_login' else M.smtp_open)(cfg, pw)
    finally:
        _HOST.reset(token)
