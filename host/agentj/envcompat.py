"""The host's own environment variables: `AGENTJ_*` (0.10). For one version cycle the old `AGENTJARVIS_*` name is still read
when the new one is unset, so a shell profile, a service unit or a script written for 0.9 keeps working. Everything the host
itself exports to the processes it starts uses the new names only.
"""
from __future__ import annotations

import os

PREFIX, LEGACY_PREFIX = "AGENTJ_", "AGENTJARVIS_"


def legacy_name(name: str) -> str | None:
    """AGENTJ_X → AGENTJARVIS_X; None for a name that is not one of ours."""
    return LEGACY_PREFIX + name[len(PREFIX):] if name.startswith(PREFIX) else None


def getenv(name: str, default=None, environ=None):
    """AGENTJ_X, else (one version cycle) AGENTJARVIS_X, else default. Any other name is read as is. An empty value
    counts as unset (the same as `os.environ.get(...) or …` everywhere in this package)."""
    e = os.environ if environ is None else environ
    v = e.get(name)
    if v:
        return v
    old = legacy_name(name)
    v = e.get(old) if old else None
    return v if v else default


def isset(name: str, environ=None) -> bool:
    """Is the variable present (even empty) under either name?"""
    e = os.environ if environ is None else environ
    old = legacy_name(name)
    return name in e or (old is not None and old in e)
