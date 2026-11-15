"""Pure notification-row and ASR destination checks shared by config and runtime."""
import re
import urllib.parse
from .preference_values import ConfigError

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ROWS_MAX = 100

def rows_problem(rows) -> str | None:
    """notify.automations validation (preferences.validate)."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        return f"a list of up to {ROWS_MAX} entries"
    seen = set()
    for r in rows:
        if not isinstance(r, dict) or set(r) - {"id", "allow"} or type(r.get("allow")) is not bool:
            return 'each entry is {"id": "<name>", "allow": true|false}'
        rid = r.get("id")
        if not isinstance(rid, str) or not NAME_RE.fullmatch(rid) or rid in seen:
            return "each id unique: letters, digits, . _ - (1–64, starting with a letter or digit)"
        seen.add(rid)
    return None


def asr_endpoint(base_url):
    """Validate owner-selected destinations without retaining or echoing invalid URL text."""
    try:
        u=urllib.parse.urlsplit(base_url)
        if (u.scheme not in ('http','https') or not u.hostname or u.username is not None or
                u.password is not None or u.query or u.fragment or any(ord(c)<33 for c in base_url)):
            raise ValueError()
        _=u.port
    except (ValueError,TypeError):
        raise ConfigError('voice.asr.base_url','expected http(s) base URL without credentials, query or fragment') from None
    path=u.path.rstrip('/')
    if path.endswith('/audio/transcriptions'): return urllib.parse.urlunsplit((u.scheme,u.netloc,path,'',''))
    if not path.endswith('/v1'): path+='/v1'
    return urllib.parse.urlunsplit((u.scheme,u.netloc,path+'/audio/transcriptions','',''))




def tts_options(cfg):
    """Validate local provider-scoped data without echoing owner text or unknown fields."""
    import math
    from .preference_values import get
    prefixes = get(cfg, 'voice.tts.text_prefix', {})
    settings = get(cfg, 'voice.tts.settings', {})
    if not isinstance(prefixes, dict) or set(prefixes) - {'elevenlabs'}:
        raise ConfigError('voice.tts.text_prefix', 'expected provider object; only elevenlabs is supported')
    for value in prefixes.values():
        if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ConfigError('voice.tts.text_prefix', 'expected text of 0..256 characters without control characters')
    if not isinstance(settings, dict) or set(settings) - {'elevenlabs'}:
        raise ConfigError('voice.tts.settings', 'expected provider object; only elevenlabs is supported')
    for value in settings.values():
        if (not isinstance(value, dict) or set(value) - {'stability', 'similarity_boost'} or
                any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value.values())):
            raise ConfigError('voice.tts.settings', 'only stability and similarity_boost are supported, finite numbers in 0..1')
    if get(cfg, 'voice.tts.mode') == 'cloud' and get(cfg, 'voice.tts.provider') == 'elevenlabs':
        return prefixes.get('elevenlabs', ''), dict(settings.get('elevenlabs', {}))
    return '', {}
