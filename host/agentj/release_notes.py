"""Local harness projection; safety prose always remains complete."""
import re

_TAG = re.compile(r'^([^\S\r\n]*(?:[-*] |\d+[.)] )?)\[([a-z][a-z0-9_-]*(?:[^\S\r\n]*,[^\S\r\n]*[a-z][a-z0-9_-]*)*)\][^\S\r\n]*')
_WARNING = re.compile(r'\b(?:security|warnings?|vulnerabilit(?:y|ies)|CVE|exploits?|risks?|breach(?:es)?|advisory)\b|安全|警告|漏洞|风险', re.IGNORECASE)


KINDS = frozenset(('claude', 'codex', 'opencode', 'kimi', 'qwen', 'dsh', 'core'))


def strip_tags(text):
    return ''.join(_TAG.sub(lambda m: m[1], line, count=1)
                   for line in str(text or '').splitlines(keepends=True))


def filter_text(text, harness=None):
    if _WARNING.search(str(text or '')):
        return strip_tags(text)
    harness = harness if isinstance(harness, str) and harness in KINDS else None
    out = []
    for line in str(text or '').splitlines():
        match = _TAG.match(line)
        if match:
            tags = {tag.strip() for tag in match[2].split(',')}
            if 'all' not in tags and (not isinstance(harness, str) or harness not in tags):
                continue
            line = match[1] + line[match.end():]
        out.append(line)
    return '\n'.join(out).strip()


def current(st, agent=None):
    kind = getattr(agent, 'kind', None)
    if agent is not None:
        return kind if isinstance(kind, str) and kind in KINDS else None
    cfg = st.agent_config()
    return cfg.get('kind') if isinstance(cfg, dict) else None
