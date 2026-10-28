"""P503d mail authorization utilities. Global executor policy remains follow-up work."""
import json
import hashlib


def summary(tool: str, inp: dict, *, credentials: bool = False, lang: str = "en",
            workdir: str | None = None, presentation: bool = False) -> str | dict:
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
    mail_target = inp if tool == 'host_write' else ({'kind':'mail_set', 'before':inp['before'], 'cfg':inp['after']}
                  if tool == 'agentj mail' and isinstance(inp.get('before'), dict) and isinstance(inp.get('after'), dict) else None)
    if mail_target and mail_target.get('kind') == 'mail_set':
        # Keep the signed original digest, but never repeat the allowlist history in Details either.
        for side in ('before', 'cfg', 'after'):
            if isinstance(fields.get(side), dict):
                fields[side] = {k:v for k,v in fields[side].items() if k != 'allow'}
    text = heading + "\n" + json.dumps(fields, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if prose:
        label = "Agent note:" if str(lang).lower().startswith("en") else "Agent 附注："
        text += "\n" + label + "\n" + json.dumps(prose, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    text += "\nRequest SHA-256: " + hashlib.sha256(raw.encode()).hexdigest()
    if presentation:
        human = visible(_mail_summary(mail_target, lang)) if mail_target else text
        if len((human + text).encode('utf-8')) > 24000:
            raise ValueError('approval presentation too large')
        return {"summary": human, "details": text}
    return text


def mail_config(old: dict, raw: dict) -> dict:
    """One effective settings merge for both the approval preview and execution."""
    from . import mail
    new = {**old, **{k: v for k, v in raw.items() if k != 'generation'}}
    if raw.get('preset') and raw.get('address'):
        if 'username' not in raw:
            new['username'] = ''
        new = mail.apply_preset(new, raw['preset'], raw['address'])
        if raw['preset'] == 'custom':
            new.update({k: raw[k] for k in ('imap', 'smtp') if k in raw})
    return mail.validate(new)


def _mail_summary(target: dict, lang: str) -> str:
    """Presentation only; the complete prose then passes the P513 redactor."""
    from . import mail
    zh = str(lang).lower().startswith('zh')
    def tr(a, b): return a if zh else b
    before = target['before']
    if target['kind'] == 'mail_test':
        return '\n'.join([tr('测试邮件连接', 'Test mail connection'), before['address'],
                          *[f"{k.upper()}: {before[k]['host']}:{before[k]['port']} ({before[k]['security']})" for k in ('imap', 'smtp')],
                          tr('仅登录检查，不发邮件。', 'Login checks only; no mail is sent.')])
    patch = target['cfg']
    after = mail_config(before, patch)
    labels = [('address', tr('邮箱', 'Mailbox')), ('preset', tr('服务商', 'Provider')),
              ('username', tr('登录账号', 'Login name')), ('imap', 'IMAP'), ('smtp', 'SMTP'),
              ('poll_minutes', tr('检查频率', 'Check interval')), ('enabled', tr('邮件通道', 'Mail channel')),
              ('auth', tr('发件人校验', 'Sender verification')), ('auth_servid', tr('可信邮件服务器', 'Trusted mail server')),
              ('allow', tr('允许的发件人', 'Allowed senders')), ('reply_per_hour', tr('每小时回复上限', 'Replies per hour'))]
    def value(key, val):
        if key == 'preset':
            if val == 'gmail': return 'Gmail'
            return mail.PRESETS[val][0 if zh else 1] if val in mail.PRESETS else tr('未设置', 'Not set')
        if key in ('imap', 'smtp'): return f"{val['host'] or '—'}:{val['port']} ({val['security']})"
        if key == 'poll_minutes': return f'每 {val} 分钟' if zh else f'Every {val} minutes'
        if key == 'enabled': return tr('启用', 'On') if val else tr('关闭', 'Off')
        if key == 'auth': return {'strict':tr('严格', 'Strict'), 'standard':tr('标准', 'Standard'), 'off':tr('关闭', 'Off')}[val]
        if key == 'allow':
            return '、'.join(r['addr'] + ' (' + (tr('本人', 'Owner') if r['role'] == 'owner' else tr('访客', 'Guest')) + ')' + (' · ' + r['name'] if r.get('name') else '') + (' · ' + tr('包含子域名', 'Include subdomains') if r.get('subdomains') else '') for r in val) or tr('无', 'None')
        return str(val) if val != '' else tr('未设置', 'Not set')
    # A preset names its fixed servers; duplicate login names and its derived
    # server changes remain in Details. Custom/independent server edits stay explicit.
    implicit = {'imap', 'smtp'} if patch.get('preset') and patch.get('address') and after['preset'] != 'custom' else set()
    if before['username'] == before['address'] and after['username'] == after['address']:
        implicit.add('username')
    changes = [f'{label}: {value(key, before[key])} → {value(key, after[key])}'
               for key, label in labels if key != 'allow' and key not in implicit and before[key] != after[key]]
    removed = [r for r in before['allow'] if r not in after['allow']]
    added = [r for r in after['allow'] if r not in before['allow']]
    for label, rows in [(tr('新增', 'Added'), added), (tr('移除', 'Removed'), removed)]:
        if rows:
            changes.append(label + ': ' + value('allow', rows))
    return '\n'.join([tr('保存邮件通道设置', 'Save mail channel settings'),
                      *(changes or [tr('设置未改变', 'Settings unchanged')])])


def confirm_cli(action: str, params: dict, *, st=None) -> bool:
    from .state import State
    from . import elevate, names
    st = st if st is not None else State()
    res = elevate.client_request(st, {"t":"policy_cli", "action":action, "params":params}, timeout=620,
                                 path=names.elevate_sock(st))
    return res.get("result") == "approved"
