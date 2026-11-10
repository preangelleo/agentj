"""Human-input boundary for native desktop mirrors; content is never logged."""
import re

# Harness-generated preambles occur at the beginning, not in quoted user prose.
_PREAMBLE = re.compile(r"^(?:Base directory for this skill:|# AGENTS.md instructions for |<environment_context>|You are performing a CONTEXT CHECKPOINT COMPACTION|This session is being continued from a previous conversation|<(?:(?:local-command-(?:stdout|stderr|caveat))|command-(?:name|message|args)|system-reminder|hook(?:[_-][\w-]+)?|compact(?:[_-][\w-]+)?)(?:[\s>]))")

# ADR-0305: Codex writes a finished native sub-agent's result into the PARENT rollout as a user-role message whose whole text is
# one <subagent_notification>{json}</subagent_notification> block (content_item_kinds multi_agent.subagent_notification).
_SUBAGENT = re.compile(r"\A\s*<subagent_notification>\s*(\{.*\})\s*</subagent_notification>\s*\Z", re.S)


def subagent_notification(record, text) -> bool:
    """Codex's own sub-agent completion notice (never a person): the metadata marks every content item so, or the whole text is
    exactly one such block around a JSON object naming agent_path and status. A quoted tag inside other prose is not."""
    meta = record.get('internal_chat_message_metadata_passthrough') if isinstance(record, dict) else None
    kinds = meta.get('content_item_kinds') if isinstance(meta, dict) else None
    if isinstance(kinds, list) and kinds and all(k == 'multi_agent.subagent_notification' for k in kinds):
        return True
    m = _SUBAGENT.match(text) if isinstance(text, str) else None
    if not m:
        return False
    try:
        import json
        body = json.loads(m.group(1))
    except ValueError:
        return False
    return isinstance(body, dict) and 'agent_path' in body and 'status' in body


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
    if proactive_origin(record, text) or subagent_notification(record, text):
        return False
    if any(record.get(k) for k in ('isMeta', 'isSidechain', 'isCompactSummary', 'synthetic', 'ignored')):
        return False
    if isinstance(content, list) and any(isinstance(b, dict) and
            (b.get('type') == 'tool_result' or b.get('synthetic') or b.get('ignored')) for b in content):
        return False
    return not _PREAMBLE.match(text.lstrip())
