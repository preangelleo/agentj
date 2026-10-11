"""Human-input boundary for native desktop mirrors; content is never logged."""
import re

# Harness-generated preambles occur at the beginning, not in quoted user prose.
_PREAMBLE = re.compile(r"^(?:Base directory for this skill:|# AGENTS.md instructions for |<environment_context>|You are performing a CONTEXT CHECKPOINT COMPACTION|This session is being continued from a previous conversation|<(?:(?:local-command-(?:stdout|stderr|caveat))|command-(?:name|message|args)|system-reminder|hook(?:[_-][\w-]+)?|compact(?:[_-][\w-]+)?)(?:[\s>]))")

def proactive_origin(record, text=""):
    """P132: a turn the harness started on its own → 'loop' (a /loop or ScheduleWakeup firing) or 'task' (a background
    task finished), else None. Claude Code marks both on the record (turnOrigin / origin.kind); never a human input."""
    if not isinstance(record, dict):
        return None
    turn, kind = record.get('turnOrigin'), (record.get('origin') or {}).get('kind') if isinstance(record.get('origin'), dict) else None
    if turn == 'scheduled' or record.get('scheduledTaskId'):
        return 'loop'
    if turn == 'task_notification' or kind == 'task-notification' or (isinstance(text, str) and text.lstrip().startswith('<task-notification>')):
        return 'task'
    return None


def human_input(record, content, text):
    if not isinstance(record, dict) or not isinstance(text, str) or not text.strip():
        return False
    if proactive_origin(record, text):
        return False
    if any(record.get(k) for k in ('isMeta', 'isSidechain', 'isCompactSummary', 'synthetic', 'ignored')):
        return False
    if isinstance(content, list) and any(isinstance(b, dict) and
            (b.get('type') == 'tool_result' or b.get('synthetic') or b.get('ignored')) for b in content):
        return False
    return not _PREAMBLE.match(text.lstrip())
