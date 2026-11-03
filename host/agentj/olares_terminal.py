"""Advertise the chart-owned private PTY entrance; never create another shell service."""
import os
import re


def external_url() -> str | None:
    from . import olares_supervisor
    if not olares_supervisor.enabled():
        return None
    value = os.environ.get("AGENTJ_OLARES_TERMINAL_URL", "")
    host = value.removeprefix("https://")
    labels = host.split(".")
    if value.startswith("https://") and host == host.lower() and not host.replace(".", "").isdigit() and len(host) <= 253 and len(labels) >= 2 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        return value
    return None
