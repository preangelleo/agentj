"""Bound host.log diagnostic values before copying/JSON serialization; never used by audit writers."""
from __future__ import annotations

import math
import re
from . import privacy

TEXT_MAX, TEXT_TOTAL, NODES, DEPTH, ITEMS = 1024, 8192, 256, 4, 16


def record(ev, fields, allowed):
    remaining = [NODES, TEXT_TOTAL]
    cut = [False]

    def text(value):
        n = min(TEXT_MAX, remaining[1])
        remaining[1] -= min(n, len(value))
        if len(value) > n:
            cut[0] = True
            # Do not expose a partial token whose suffix fell outside the slice.
            prefix = value[:n]
            value = re.sub(r"\S+$", "", prefix) + "…"
        return privacy.redact_draft(value, "", "")

    def value(obj, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0:
            cut[0] = True
            return "<bounded>"
        kind = type(obj)
        if kind is str:
            return text(obj)
        if obj is None or kind is bool:
            return obj
        if kind is int:
            if obj.bit_length() <= 128:
                return obj
        elif kind is float:
            if math.isfinite(obj):
                return obj
        elif kind in (dict, list, tuple) and depth < DEPTH:
            out = {} if kind is dict else []
            for i, item in enumerate(obj.items() if kind is dict else obj):
                if i >= ITEMS or remaining[0] <= 0:
                    cut[0] = True
                    break
                if kind is dict:
                    key, child = item
                    if type(key) is str:
                        sensitive = re.fullmatch(privacy._CRED_NAMES, key[:TEXT_MAX], re.I) is not None
                        out[text(key)] = "<redacted>" if sensitive else value(child, depth + 1)
                else:
                    out.append(value(item, depth + 1))
            return out
        cut[0] = True
        return "<bounded>"

    out = {"ev": value(ev)}
    # Fixed allowlist traversal: no copy, repr, deepcopy, or full walk of caller data.
    priority = ("status", "cid", "id", "request", "phase", "task", "code", "exception_type")
    for key in sorted(allowed, key=lambda k: (k not in priority, priority.index(k) if k in priority else k)):
        if key in fields:
            if remaining[0] <= 0:
                cut[0] = True
                break
            out[key] = value(fields[key], DEPTH if key in priority and type(fields[key]) in (dict, list, tuple) else 0)
    if cut[0]:
        out["diagnostic_truncated"] = True
    return out
