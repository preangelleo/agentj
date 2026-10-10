"""The four long-task contracts (ADR-A193 §3–§5): registry · preflight · brief · report, JSON Schema 2020-12, closed fields.

The schema files in `longtask_schemas/` are the approved P86 design schemas byte-for-byte in meaning (only `$id` / `title`
renamed). The host checks them with this small validator — stdlib only, exactly the keywords those four files use
(type, const, enum, pattern, min/maxLength, min/maxItems, minimum/maximum, properties, required, additionalProperties,
items, anyOf, allOf, if/then/else). `format` is an annotation here, like jsonschema without a FormatChecker extra; the
schemas also pin every date with an explicit RFC 3339 `pattern`. An unknown keyword fails closed (SchemaError), so a
schema edit that needs more than this cannot pass silently.

A schema pass is structure only. Cross references, TTLs, digests, receipts and "no secret value" semantics are checked by
capability.py, never by trusting a field the Agent wrote.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("longtask_schemas")
KINDS = ("registry", "preflight", "brief", "report")
_ANNOTATIONS = {"$schema", "$id", "title", "format", "description"}
_KNOWN = _ANNOTATIONS | {"type", "const", "enum", "pattern", "minLength", "maxLength", "minItems", "maxItems", "minimum",
                         "maximum", "properties", "required", "additionalProperties", "items", "anyOf", "allOf", "if",
                         "then", "else"}


class SchemaError(ValueError):
    pass


@lru_cache(maxsize=None)
def schema(kind: str) -> dict:
    if kind not in KINDS:
        raise SchemaError("unknown contract")
    return json.loads((DATA / f"{kind}.schema.json").read_text(encoding="utf-8"))


def _is(v, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "null":
        return v is None
    raise SchemaError("unknown type " + t)


def _same(a, b) -> bool:
    """JSON equality (True is not 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _errors(v, s: dict, path: str, out: list[str]) -> None:
    unknown = set(s) - _KNOWN
    if unknown:
        raise SchemaError("unsupported keyword " + ", ".join(sorted(unknown)))
    t = s.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(v, x) for x in ts):
            out.append(f"{path or '$'}: must be {'/'.join(ts)}")
            return
    if "const" in s and not _same(v, s["const"]):
        out.append(f"{path or '$'}: must be {json.dumps(s['const'], ensure_ascii=False)}")
    if "enum" in s and not any(_same(v, x) for x in s["enum"]):
        out.append(f"{path or '$'}: not an allowed value")
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            out.append(f"{path}: too short")
        if "maxLength" in s and len(v) > s["maxLength"]:
            out.append(f"{path}: too long")
        if "pattern" in s and not re.search(s["pattern"], v):
            out.append(f"{path}: bad format")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            out.append(f"{path}: below minimum")
        if "maximum" in s and v > s["maximum"]:
            out.append(f"{path}: above maximum")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            out.append(f"{path}: needs at least {s['minItems']} item(s)")
        if "maxItems" in s and len(v) > s["maxItems"]:
            out.append(f"{path}: at most {s['maxItems']} item(s)")
        if isinstance(s.get("items"), dict):
            for i, x in enumerate(v):
                _errors(x, s["items"], f"{path}[{i}]", out)
    if isinstance(v, dict):
        props = s.get("properties") or {}
        for k in s.get("required") or []:
            if k not in v:
                out.append(f"{path}.{k}: missing")
        for k, x in v.items():
            if k in props:
                _errors(x, props[k], f"{path}.{k}", out)
            elif s.get("additionalProperties") is False:
                out.append(f"{path}.{k}: unknown field")
    if "anyOf" in s:
        if not any(not _collect(v, sub, path) for sub in s["anyOf"]):
            out.append(f"{path or '$'}: matches none of the allowed shapes")
    for sub in s.get("allOf") or []:
        _errors(v, sub, path, out)
    if "if" in s:
        if not _collect(v, s["if"], path):
            if "then" in s:
                _errors(v, s["then"], path, out)
        elif "else" in s:
            _errors(v, s["else"], path, out)


def _collect(v, s: dict, path: str) -> list[str]:
    out: list[str] = []
    _errors(v, s, path, out)
    return out


def problems(kind: str, obj) -> list[str]:
    """Every way `obj` breaks the `kind` contract (empty = structurally valid)."""
    return _collect(obj, schema(kind), "")


def canonical(obj) -> bytes:
    """The bytes a digest / signature covers: sorted keys, no spaces, UTF-8 as is (wire.js canonicalJson, JCS for our data:
    integers and strings only — the contracts hold no floats)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
