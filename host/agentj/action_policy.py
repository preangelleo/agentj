"""P503d mail authorization utilities. Global executor policy remains follow-up work."""
import json
import hashlib


def summary(tool: str, inp: dict, *, credentials: bool = False, lang: str = "en",
            workdir: str | None = None) -> str:
    """Redact original values before serialization; bind the unchanged raw request.

    SecretIndex is the existing local .env/process fragment detector. It retains
    salted hashes only. Structured values are decoded before fragment checks on their leaves.
    Bounded recursive decoding also checks JSON strings, forms, URLs and base64.
    """
    import base64
    import binascii
    import os
    import re
    from urllib.parse import unquote_plus
    from .privacy import redact, _CRED_NAMES, R
    from .peer_guard import SecretIndex

    raw = json.dumps(inp, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    index = SecretIndex.build(workdir=workdir or os.getcwd(), fragment_size=8)
    names = re.compile(r"(?:" + _CRED_NAMES + r"|key|authorization|cookie|set-cookie)", re.I)
    pairs = re.compile(r"(?<![\w-])(" + names.pattern + r"[\"']?\s*[:：=]\s*)(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s&;,\"'<>}]+)", re.I)
    headers = re.compile(r"(?i)(\b(?:Authorization|Cookie|Set-Cookie)\s*:\s*)([^\r\n\"']+)")
    quoted_json = re.compile(r'"(?:\\.|[^"\\])*"')
    # Eight decoded ASCII characters need at least eleven base64 characters.
    encoded = re.compile(r"(?<![\w+/-])[A-Za-z0-9_+/-]{11,}={0,2}(?![\w+/-])")
    decoder = json.JSONDecoder()

    def visible(value, depth=0):
        if depth > 32:
            raise ValueError("approval value nesting limit")
        if isinstance(value, dict):
            return {k: R if names.fullmatch(str(k)) else visible(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [visible(v, depth + 1) for v in value]
        if not isinstance(value, str) or value == R:
            return value
        # Parse the raw spelling, before outer JSON introduces backslashes.
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            if parsed != value and isinstance(parsed, (dict, list, str)):
                return json.dumps(visible(parsed, depth + 1), ensure_ascii=False)

        def embedded(m):
            token = m.group(0)
            try:
                decoded = json.loads(token)
            except ValueError:
                return token
            safe = visible(decoded, depth + 1)
            return json.dumps(safe, ensure_ascii=False) if safe != decoded else token

        # Shell payloads may contain JSON objects or escaped JSON string literals.
        # Walk complete object/array spans, leaving the surrounding command intact.
        out, pos = [], 0
        while pos < len(value):
            if value[pos] in "{[":
                try:
                    obj, end = decoder.raw_decode(value, pos)
                except ValueError:
                    pass
                else:
                    out.append(json.dumps(visible(obj, depth + 1), ensure_ascii=False))
                    pos = end
                    continue
            out.append(value[pos]); pos += 1
        text = quoted_json.sub(embedded, "".join(out))

        # Preserve query/form keys and unrelated values, including their spelling.
        # Decode each component before testing names or nested payloads.
        def form(m):
            prefix, key, val = m.group(1), m.group(2), m.group(3)
            decoded = unquote_plus(val)
            safe = R if names.fullmatch(unquote_plus(key)) else visible(decoded, depth + 1)
            return prefix + key + "=" + (safe if safe != decoded else val)
        text = re.sub(r"(^|[?&\s\"'])([^=\s?&\"']+)=([^&\s\"']*)", form, text)
        text = headers.sub(lambda m: m.group(1) + R, text)
        def pair(m):
            val = m.group(2)
            quote = val[0] if val[0] in "\"'" and val[-1] == val[0] else ""
            return m.group(1) + quote + R + quote
        text = pairs.sub(pair, text)

        def wrapped(m):
            token = m.group(0)
            try:
                decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, UnicodeError, binascii.Error):
                return token
            safe = visible(decoded, depth + 1)
            if safe == decoded:
                return token
            label = "decoded" if str(lang).lower().startswith("en") else "已解码"
            return safe + " [" + label + "]"
        text = encoded.sub(wrapped, text)
        # Structured JSON/query/base64 values have already been reduced to safe
        # leaves. Mask any remaining known fragment in an opaque token, preserving
        # surrounding commands and effects. Split/normalised hits still fail shut.
        if index.hits(text):
            text = re.sub(r"[^\s\"';&|]+", lambda m: R if index.hits(m.group(0)) else m.group(0), text)
            if index.hits(text):
                return R
        return redact(text, owner=True)

    safe = visible(inp)
    # A plain unlabelled credential-file body has no reviewable structure. Only
    # that body is opaque; paths, command structure and effect metadata survive.
    if credentials:
        for key in ('content', 'new_string', 'old_string'):
            value = safe.get(key)
            if isinstance(value, str) and value == inp.get(key) and not any(c in value for c in '{}[]=:'):
                safe[key] = R
    prose = {k: v for k, v in safe.items() if k in ('description', 'summary', 'card')}
    fields = {k: v for k, v in safe.items() if k not in prose}
    command = safe.get('command')
    heading = command.splitlines()[0] if tool == 'Bash' and isinstance(command, str) and command else tool
    text = heading + "\n" + json.dumps(fields, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if prose:
        label = "Agent note:" if str(lang).lower().startswith("en") else "Agent 附注："
        text += "\n" + label + "\n" + json.dumps(prose, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return text + "\nRequest SHA-256: " + hashlib.sha256(raw.encode()).hexdigest()


def confirm_cli(action: str, params: dict, *, st=None) -> bool:
    from .state import State
    from . import elevate, names
    st = st if st is not None else State()
    res = elevate.client_request(st, {"t":"policy_cli", "action":action, "params":params}, timeout=620,
                                 path=names.elevate_sock(st))
    return res.get("result") == "approved"
