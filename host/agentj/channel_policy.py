"""External-channel seam for the single permission table (P503 integration).

Only an action identifier from the executor belongs here; model claims of safety,
owner identity and confirmation are never evidence. Unknown actions fail closed.
The small automatic set is intentional until P503 supplies its authoritative table.
"""
AUTO = frozenset(('answer', 'translate', 'summarize', 'rewrite', 'image.generate', 'image.edit', 'web.search'))


def channel_forbidden(action: str) -> bool:
    """Any action needing long-press or UV in the Web App is forbidden here."""
    return not isinstance(action, str) or action not in AUTO
