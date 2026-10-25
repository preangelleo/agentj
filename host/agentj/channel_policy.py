"""Compatibility seam: external channels use the host's shared action registry."""
from . import policy_registry


def channel_forbidden(action: str) -> bool:
    """Unknown actions and anything requiring Web App confirmation stay held."""
    return policy_registry.decision("channel", action) != "allow"
