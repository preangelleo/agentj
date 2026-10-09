"""Exact-session native slash transport. Never type into a busy/blocked pane."""
from .shared_interrupt import herdr_bin, locate, _run


def send(session, text):
    if not (text == '/clear' or text == '/compact' or text.startswith('/compact ')) or any(c in text for c in '\r\n\x1b\x00'):
        return 'refused'
    binary = herdr_bin()
    if not binary:
        return 'no_herdr'
    pane, status = locate(session, binary)
    if not pane:
        return 'no_exact_pane'
    if status not in ('idle', 'done'):
        return 'not_idle'
    # Herdr prompt also refuses blocked panes; never issue send-keys/Enter.
    return 'sent' if _run(binary, 'agent', 'prompt', pane, text) is not None else 'input_failed'
