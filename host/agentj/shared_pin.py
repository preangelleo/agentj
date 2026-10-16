"""P330: the shared session each harness follows — one pin per harness, in the state's agent.json.

`agent.shared_session_id` (config.json5) used to be the ONE pin for every harness: a Claude session id left there sent
OpenCode / Codex the wrong way (P326), and Claude's own re-pin (P147 heal) wrote it back globally. Now:

- `<kind>.pin` (agent.json, per harness) is what that harness's shared session follows. Claude's heal / reconnect, Codex's
  discovery re-pin and its phone /clear write only their own `<kind>.pin`.
- `agent.shared_session_id` stays the OWNER's explicit choice. The first time a serve sees a value it has not absorbed yet
  (a fresh `agentj config set`, the phone's setting, or a pre-P330 config), it is recorded with the harness it belongs to
  (`pin.owner` + `pin.owner_kind`) and becomes that harness's `<kind>.pin`. Nothing new in the config schema — an older
  host that is rolled back to still reads the same config.json5.
- A value that cannot be this harness's id (an OpenCode `ses…`, a Codex UUIDv7, a Claude transcript in this folder) is
  credited to the harness it does belong to and never followed by this one.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

OWNER = "pin.owner"             # the last `agent.shared_session_id` value absorbed
OWNER_KIND = "pin.owner_kind"   # the harness it was credited to
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-([0-9a-fA-F])[0-9a-fA-F]{3}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
OC = re.compile(r"ses[A-Za-z0-9_-]+")
KINDS = ("claude", "codex", "opencode")


def key(kind: str) -> str:
    return f"{kind}.pin"


def _claude_transcript(directory, sid) -> bool:
    if not directory:
        return False
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(directory))
    try:
        return (root / "projects" / slug / (sid + ".jsonl")).is_file()
    except OSError:
        return False


def guess(sid: str, directory=None) -> str | None:
    """Which harness an id belongs to (cheap: format, one stat), None = not a session id at all."""
    if OC.fullmatch(sid):
        return "opencode"
    m = UUID.fullmatch(sid)
    if not m:
        return None
    if m.group(1) == "7":                 # Codex thread ids are UUIDv7; Claude Code's session ids are random (v4)
        return "codex"
    return "claude" if _claude_transcript(directory, sid) else None    # None: a v4 id, either Claude or an old Codex


def plausible(kind: str, sid: str, directory=None) -> bool:
    if not isinstance(sid, str) or not sid:
        return False
    g = guess(sid, directory)
    if kind == "opencode":
        return g == "opencode"
    if kind == "claude":
        return g in ("claude", None) and bool(UUID.fullmatch(sid))
    if kind == "codex":
        return g in ("codex", None) and bool(UUID.fullmatch(sid))
    return True


def _clean(v) -> str:
    return v if isinstance(v, str) else ""


def resolve(st, kind: str, owner_pin, directory=None) -> str:
    """The pin `kind`'s shared session follows now (read-only; `sync` writes the same answer down)."""
    owner_pin = _clean(owner_pin)
    rec = _clean(st.agent_session(OWNER))
    if owner_pin and owner_pin != rec:              # not absorbed yet: the owner's choice (or a pre-P330 config)
        if plausible(kind, owner_pin, directory):
            return owner_pin
    elif not owner_pin and rec and st.agent_session(OWNER_KIND) == kind:
        return ""                                   # the owner unset it: this harness picks by itself again
    return _clean(st.agent_session(key(kind)))


def sync(st, kind: str, owner_pin, directory=None) -> str:
    """Serve start: absorb a new owner choice / an unset into the per-harness pins; → `resolve`."""
    owner_pin = _clean(owner_pin)
    rec = _clean(st.agent_session(OWNER))
    if owner_pin and owner_pin != rec:
        to = kind if plausible(kind, owner_pin, directory) else (guess(owner_pin, directory) or "")
        st.set_agent_session(OWNER, owner_pin)
        st.set_agent_session(OWNER_KIND, to or None)
        if to in KINDS:
            st.set_agent_session(key(to), owner_pin)
        st.log("shared_pin_owner", agent=kind, to=to or "?", pin=owner_pin[:8])
    elif not owner_pin and rec:
        was = st.agent_session(OWNER_KIND)
        if was in KINDS:
            st.set_agent_session(key(was), None)
        st.set_agent_session(OWNER, None)
        st.set_agent_session(OWNER_KIND, None)
        st.log("shared_pin_owner", agent=kind, to="", pin="")
    return resolve(st, kind, owner_pin, directory)


def forget_owner(st) -> None:
    """`agentj config set/unset agent.shared_session_id`: the next serve start absorbs it again, even the same value."""
    try:
        st.set_agent_session(OWNER, None)
    except OSError:
        pass


def unpin(st) -> None:
    """`agentj config unset agent.shared_session_id`: the current harness picks its session by itself again (a pin it
    re-pinned on its own included); the other harnesses keep theirs."""
    try:
        a = st.config().get("agent")
        kind = a.get("kind") if isinstance(a, dict) else None
        if kind in KINDS:
            st.set_agent_session(key(kind), None)
    except (OSError, ValueError):
        pass


def write(st, kind: str, sid) -> None:
    """A harness re-pinned itself (heal / reconnect / discovery / phone /clear): its own pin only, never the config."""
    st.set_agent_session(key(kind), sid if isinstance(sid, str) and sid else None)
