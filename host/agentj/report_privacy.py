"""Customer reports: recursive local redaction and a non-bypassable credential check.

Never returns matched values or model-authored explanations as a refusal reason.
"""
from __future__ import annotations

import collections
import json
import re

from . import peer_guard, privacy

# Postal details are removed as a labelled field, preserving the surrounding report.
_ADDRESS = re.compile(r'(?im)(?:postal\s+address|street\s+address|address|地址|住址|收货地址)\s*[:：]\s*[^\n;；]+')
_STREET = re.compile(r'\b\d{1,6}\s+(?:[A-Za-z]+\s+){1,5}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr)\b[^\n;；]*', re.I)


def prepare(obj, *, identity=None, index=None):
    host, user = identity if identity is not None else privacy.local_identity()
    index = index if index is not None else peer_guard.SecretIndex.build()
    hits = collections.Counter()

    def redact(value):
        if isinstance(value, str):
            # Index first: a regex replacement could otherwise split a known secret.
            value, count = index.redact(value)
            hits['secret_fragment'] += count
            value = privacy.redact_draft(value, host, user, hits)
            for pattern, label in ((privacy._EMAIL, '<email>'), (_ADDRESS, '<address>'), (_STREET, '<address>')):
                value, count = pattern.subn(label, value)
                hits['email' if label == '<email>' else 'address'] += count
            return value
        if isinstance(value, list):
            return [redact(v) for v in value]
        if isinstance(value, dict):
            return {redact(k): redact(v) for k, v in value.items()}
        return value

    draft = redact(obj)
    text = json.dumps(draft, ensure_ascii=False)
    reason = 'secret_fragment' if index.hits(text) else peer_guard.credential_shape(text)
    return draft, +hits, reason


def stderr_summary(text):
    """A short, printable summary for the owner's card; fail closed on residual secrets."""
    draft, _, reason = prepare(str(text or '')[:16384])
    if reason:
        return '<redacted>'
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in draft).split())[:600]


def sudo_notice(res, lang='zh'):
    """Fixed classification plus the already-redacted summary, never raw stderr."""
    if res.get('result') == 'bad_password':
        return ('管理员密码未通过。可能是浏览器自动填入的旧密码，请手动输入。' if lang == 'zh' else
                'Password authentication failed. Browser autofill may have supplied an old password; type it manually.')
    if res.get('result') == 'done' and res.get('code') not in (None, 0):
        label = (f"管理员命令执行失败（退出码 {res['code']}）" if lang == 'zh' else f"Admin command failed (exit code {res['code']})")
        return label + (': ' + res['summary'] if res.get('summary') else '')
    return None
