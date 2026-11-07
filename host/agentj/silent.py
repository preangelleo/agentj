"""Final-reply silence and owner-facing projections (P551). Raw local history stays intact."""
MARKER = "〔静默消息〕"
EN_MARKER = "[silent]"
# Compatibility only: never instruct an Agent to emit the former marker.
LEGACY_MARKER = "〔不回群〕"
MARKERS = (MARKER, EN_MARKER, LEGACY_MARKER)


def is_silent(text):
    return isinstance(text, str) and (text.strip().startswith(MARKERS) or text.strip().endswith(MARKERS))


def strip_markers(text):
    if not isinstance(text, str):
        return text
    if not any(marker in text for marker in MARKERS):
        return text
    original = text
    for marker in MARKERS:
        text = text.replace(marker, "")
    if type(original) is not str and hasattr(original, "__dict__"):
        # Channel-avatar replies carry approved attachments on a str subclass.
        value = str.__new__(type(original), text)
        value.__dict__.update(original.__dict__)
        return value
    return text


def public_turn(turn):
    """A copy for the phone; silence never exposes its body in the main stream."""
    out = dict(turn)
    reply = dict(turn.get("reply") or {})
    hidden = turn.get("silent") is True or is_silent(reply.get("text"))
    out["reply"] = {k: strip_markers(v) if isinstance(v, str) else v for k, v in reply.items()}
    if hidden:
        if "silent_raw" in out["reply"]:
            out["reply"]["text"] = out["reply"].pop("silent_raw")
        out["silent"] = True
        pre = "\n\n".join(v for v in (out["reply"].get("pre", ""), out["reply"].get("text", "")) if v)
        from .history import PART_MAX, _cut_units
        out["reply"]["pre"] = pre[:_cut_units(pre, PART_MAX)]
        out["reply"]["text"] = ""
        out.pop("media", None)
        out.pop("media_skip", None)
    return out


# Replies/history are display copies. Approval/secret/config frames remain byte-exact:
# they contain payloads the owner signs or retrieves, not Agent reply prose.
DISPLAY_MESSAGES = frozenset(("msg", "hist_turn", "hist_page", "hist_found"))
OPAQUE_FIELDS = frozenset(("goal_add", "owner_grant", "sig", "signature", "value"))


def _display(obj):
    if isinstance(obj, str):
        return strip_markers(obj)
    if isinstance(obj, list):
        return [_display(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    if isinstance(obj.get("reply"), dict):
        obj = public_turn(obj)
    return {k: v if k in OPAQUE_FIELDS else _display(v) for k, v in obj.items()}


def public_message(obj):
    """Last-mile reply/history guard; never rewrite secret or approval contracts."""
    if not isinstance(obj, dict) or obj.get("t") not in DISPLAY_MESSAGES:
        return obj
    out = _display(obj)
    # #551b: deployed readers predate `silent` and recognize only this exact
    # sentinel. Put it back after prose cleanup; it is wire state, never raw prose.
    def legacy_state(value):
        if isinstance(value, list):
            return [legacy_state(v) for v in value]
        if not isinstance(value, dict):
            return value
        value = {k: v if k in OPAQUE_FIELDS else legacy_state(v) for k, v in value.items()}
        if value.get("silent") is True and isinstance(value.get("reply"), dict):
            value["reply"]["text"] = LEGACY_MARKER
        return value
    return legacy_state(out)
