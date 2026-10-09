"""Human-input boundary for native desktop mirrors; content is never logged."""
import re

# Harness-generated preambles occur at the beginning, not in quoted user prose.
_PREAMBLE = re.compile(r"^(?:Base directory for this skill:|# AGENTS.md instructions for |<environment_context>|You are performing a CONTEXT CHECKPOINT COMPACTION|This session is being continued from a previous conversation|<(?:(?:local-command-(?:stdout|stderr|caveat))|command-(?:name|message|args)|system-reminder|hook(?:[_-][\w-]+)?|compact(?:[_-][\w-]+)?)(?:[\s>]))")

def human_input(record, content, text):
    if not isinstance(record, dict) or not isinstance(text, str) or not text.strip():
        return False
    if any(record.get(k) for k in ('isMeta', 'isSidechain', 'isCompactSummary', 'synthetic', 'ignored')):
        return False
    if isinstance(content, list) and any(isinstance(b, dict) and
            (b.get('type') == 'tool_result' or b.get('synthetic') or b.get('ignored')) for b in content):
        return False
    return not _PREAMBLE.match(text.lstrip())
