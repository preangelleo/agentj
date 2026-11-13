"""P503d mail authorization utilities. Global executor policy remains follow-up work."""
import json
import hashlib


def display_normalizer(*, lang: str = "en", workdir: str | None = None, index=None):
    """Existing bounded structured/encoded display projection, shared by cards.

    Build the local fragment index in an I/O worker; the returned projector
    retains salted hashes and never edits authority or execution input.
    """
    import base64
    import binascii
    import os
    import re
    from urllib.parse import unquote_plus
    from .privacy import redact_with_report, _CRED_NAMES, R
    from .peer_guard import SecretIndex

    index = index if index is not None else SecretIndex.build(workdir=workdir or os.getcwd(), fragment_size=8)
    # Every approval projection is an externally visible card, including mail,
    # sudo, and hook actions.  Never present a partial inventory as reviewable.
    index.require_complete()
    names = re.compile(r"(?:" + _CRED_NAMES + r"|key|authorization|cookie|set-cookie)", re.I)
    pairs = re.compile(r"(?<![\w-])(" + names.pattern + r"[\"']?\s*[:：=]\s*)(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s&;,\"'<>}]+)", re.I)
    headers = re.compile(r"(?i)(\b(?:Authorization|Cookie|Set-Cookie)\s*:\s*)([^\r\n\"']+)")
    quoted_json = re.compile(r'"(?:\\.|[^"\\])*"')
    # Eight decoded ASCII characters need at least eleven base64 characters.
    encoded = re.compile(r"(?<![\w+/-])[A-Za-z0-9_+/-]{11,}={0,2}(?![\w+/-])")
    decoder = json.JSONDecoder()
    cli_password_flags = frozenset(('--password', '--passwd', '--passphrase'))
    def cli_password_flag(key, match, start):
        # Only a literal CLI flag at a shell word boundary delegates to the
        # shared whole-argument grammar. Form/query/JSON credential keys retain
        # their existing projection, including URL-encoded flag-like keys.
        return key.lower() in cli_password_flags and (start == 0 or match.string[start - 1] in ' \t\n')

    def visible(value, depth=0, sensitive=None):
        def note(kind):
            if sensitive is not None:
                sensitive.add(kind)
        if depth > 32:
            raise ValueError("approval value nesting limit")
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if names.fullmatch(str(key)):
                    if item != R:
                        note('credential_field')
                    out[key] = R
                else:
                    out[key] = visible(item, depth + 1, sensitive)
            return out
        if isinstance(value, list):
            return [visible(v, depth + 1, sensitive) for v in value]
        if not isinstance(value, str) or value == R:
            return value
        # Parse the raw spelling, before outer JSON introduces backslashes.
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            if parsed != value and isinstance(parsed, (dict, list, str)):
                return json.dumps(visible(parsed, depth + 1, sensitive), ensure_ascii=False)

        def embedded(m):
            token = m.group(0)
            try:
                decoded = json.loads(token)
            except ValueError:
                return token
            safe = visible(decoded, depth + 1, sensitive)
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
                    out.append(json.dumps(visible(obj, depth + 1, sensitive), ensure_ascii=False))
                    pos = end
                    continue
            out.append(value[pos]); pos += 1
        text = quoted_json.sub(embedded, "".join(out))

        # Preserve query/form keys and unrelated values, including their spelling.
        # Decode each component before testing names or nested payloads.
        def form(m):
            prefix, key, val = m.group(1), m.group(2), m.group(3)
            if cli_password_flag(key, m, m.start(2)):
                return m.group(0)
            decoded = unquote_plus(val)
            if names.fullmatch(unquote_plus(key)):
                safe = R
                if decoded != R:
                    note('credential_form')
            else:
                safe = visible(decoded, depth + 1, sensitive)
            return prefix + key + "=" + (safe if safe != decoded else val)
        text = re.sub(r"(^|[?&\s\"'])([^=\s?&\"']+)=([^&\s\"']*)", form, text)
        def header(m):
            result = m.group(1) + R
            if result != m.group(0):
                note('credential_header')
            return result
        text = headers.sub(header, text)
        def pair(m):
            label = m.group(1).rstrip()
            if label.endswith('=') and cli_password_flag(label[:-1].rstrip(), m, m.start(1)):
                return m.group(0)
            val = m.group(2)
            quote = val[0] if val[0] in "\"'" and val[-1] == val[0] else ""
            result = m.group(1) + quote + R + quote
            if result != m.group(0):
                note('credential_assignment')
            return result
        text = pairs.sub(pair, text)

        def wrapped(m):
            token = m.group(0)
            try:
                decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, UnicodeError, binascii.Error):
                return token
            safe = visible(decoded, depth + 1, sensitive)
            if safe == decoded:
                return token
            label = "decoded" if str(lang).lower().startswith("en") else "已解码"
            return safe + " [" + label + "]"
        text = encoded.sub(wrapped, text)
        # Structured JSON/query/base64 values have already been reduced to safe
        # leaves. Mask any remaining known fragment in an opaque token, preserving
        # surrounding commands and effects. Split/normalised hits still fail shut.
        if index.hits(text):
            note('known_fragment')
            text = re.sub(r"[^\s\"';&|]+", lambda m: R if index.hits(m.group(0)) else m.group(0), text)
            if index.hits(text):
                return R
        result, hits = redact_with_report(text, owner=True)
        for kind, count in hits.items():
            if count and kind != 'home_path':
                note(kind)
        return result

    def sensitive_kinds(value):
        # Names only. The same walker/index performs the same projection; this
        # per-call set never retains values or contaminates another request.
        try:
            index.require_complete()
        except OSError:
            return frozenset({'incomplete_index'})
        found = set()
        visible(value, sensitive=found)
        return frozenset(found)
    visible.sensitive_kinds = sensitive_kinds
    return visible


def summary(tool: str, inp: dict, *, credentials: bool = False, lang: str = "en",
            workdir: str | None = None, presentation: bool = False, index=None) -> str | dict:
    """Redact original values before serialization; bind the unchanged raw request.

    SecretIndex is the existing local .env/process fragment detector. It retains
    salted hashes only. Structured values are decoded before fragment checks on their leaves.
    Bounded recursive decoding also checks JSON strings, forms, URLs and base64.
    """
    raw = json.dumps(inp, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    visible = display_normalizer(lang=lang, workdir=workdir, index=index)
    from .privacy import R
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
        return {"summary": human, "details": text, **({"level":"signed"} if tool == "host_write" and mail_role_downgrade(inp) else {})}
    return text


NOTE_LABELS = ("Agent note:", "Agent 附注：")
REASON_KEYS = ("description", "summary", "card")


def reason(text: str) -> str:
    """P572 (#385): the Agent's own reason for a card, read back out of the SIGNED card text (never the raw input), so it
    is already redacted and the phone can check it against what the owner signs. "" = the Agent gave none."""
    if not isinstance(text, str):
        return ""
    for label in NOTE_LABELS:
        at = text.find("\n" + label + "\n")
        if at < 0:
            continue
        start = at + len(label) + 2
        try:
            prose, end = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            continue
        # The note block summary() writes ends at the FIRST request digest line (fields are JSON, so no raw line inside
        # them can look like it); a look-alike block after it (an appended origin line) is never read as the note.
        if not isinstance(prose, dict) or end != text.find("\nRequest SHA-256: "):
            continue
        for key in REASON_KEYS:
            value = prose.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return ""


def mail_config(old: dict, raw: dict) -> dict:
    """One effective settings merge for both the approval preview and execution. It includes the known-provider check
    server pin (mail.servid_merge, 0304 A4 security r1 M1): a function of (old, raw) only, so the summary, the signed
    target {before, cfg} and on_mail's execution give the same auth_servid / servid_optout."""
    from . import mail
    new = {**old, **{k: v for k, v in raw.items() if k != 'generation'}}
    if raw.get('preset') and raw.get('address'):
        if 'username' not in raw:
            new['username'] = ''
        new = mail.apply_preset(new, raw['preset'], raw['address'])
        if raw['preset'] == 'custom':
            new.update({k: raw[k] for k in ('imap', 'smtp') if k in raw})
    return mail.validate(mail.servid_merge(old, mail.validate(new)))


def mail_role_downgrade(target: dict) -> bool:
    """Only an exact owner→guest edit can omit UV; signed paired-device authorization is still mandatory."""
    if target.get('kind') != 'mail_set':
        return False
    try:
        old = target['before']
        new = mail_config(old, target['cfg'])
        if {k:v for k,v in old.items() if k != 'allow'} != {k:v for k,v in new.items() if k != 'allow'}:
            return False
        if len(old['allow']) != len(new['allow']):
            return False
        changed = False
        for before, after in zip(old['allow'], new['allow']):
            if before == after:
                continue
            if before.get('role') != 'owner' or after != {**before, 'role':'guest'}:
                return False
            changed = True
        return changed
    except (KeyError, TypeError, ValueError):
        return False


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
              ('mark_processed_read', tr('处理后标为已读', 'Mark processed mail as read')),
              ('auth', tr('发件人校验', 'Sender verification')), ('auth_servid', tr('可信邮件服务器', 'Trusted mail server')),
              ('servid_optout', tr('不自动固定校验服务器', 'No automatic check-server pin')),
              ('allow', tr('允许的发件人', 'Allowed senders')), ('reply_per_hour', tr('每小时回复上限', 'Replies per hour'))]
    def value(key, val):
        if key == 'preset':
            if val == 'gmail': return 'Gmail'
            return mail.PRESETS[val][0 if zh else 1] if val in mail.PRESETS else tr('未设置', 'Not set')
        if key == 'servid_optout': return '、'.join(val) or tr('无', 'None')
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


CLI_REASONS = frozenset(('application_ingress_unverified', 'level_harness', 'unknown', 'offline', 'no_device', 'host_unavailable', 'estop', 'group_source', 'owner_grant_scope',
                         'timeout', 'device', 'serve_stop', 'agent_gone', 'superseded', 'too_many',
                         # #0303: the real source / ingress codes pass through (enumerated, so no free text leaks)
                         'untrusted_turn', 'untrusted_source', 'task_plan', 'unverified_plan_producer', 'peer_unverified',
                         'source_denied', 'session_changed', 'not_ordinary_application', 'owner_phone_required',
                         'owner_required', 'source', 'caller', 'not_owner_agent'))


def confirm_cli(action: str, params: dict, *, st=None, receipt=None) -> bool:
    from .state import State
    from . import elevate, names
    st = st if st is not None else State()
    res = elevate.client_request(st, {"t":"policy_cli", "action":action, "params":params}, timeout=620,
                                 path=names.elevate_sock(st))
    if type(receipt) is dict:
        reason = res.get('reason')
        receipt.update(result=res.get('result') if res.get('result') in ('approved', 'denied', 'unavailable', 'refused') else 'unknown',
                       reason=reason if isinstance(reason, str) and reason in CLI_REASONS else ('host_unavailable' if res.get('result') == 'unavailable' else 'unknown'),
                       approval_card_shown=False if res.get('approval_card_shown') is False else None)
    return res.get("result") == "approved"
