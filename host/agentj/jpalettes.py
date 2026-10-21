"""P420: host-owned global My palettes. Only bounded HEX arrays cross the existing paired E2E path."""
from __future__ import annotations
import json
import os
import re
from . import jcards
from .state import State

MAX = 32
HEX = re.compile(r"#[0-9a-fA-F]{6}")
ID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")


def clean(row):
    if not isinstance(row, dict) or set(row) != {"id", "colors"}:
        return None
    cid, colors = row.get("id"), row.get("colors")
    if not isinstance(cid, str) or not ID.fullmatch(cid):
        return None
    if not isinstance(colors, list) or not 5 <= len(colors) <= 10 or any(not isinstance(c, str) or not HEX.fullmatch(c) for c in colors):
        return None
    return {"id": cid, "colors": [c.lower() for c in colors]}


def read(root, strict=False):
    try:
        with jcards._dirfd(root, ("cards",)) as d:
            raw, cut = jcards._read_fd(os.open("palettes.json", os.O_RDONLY | jcards._NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=d), 16384)
        if cut or not raw:
            raise ValueError("invalid palette library")
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > MAX:
            raise ValueError("invalid palette library")
        out, seen = [], set()
        for row in rows:
            item = clean(row)
            if strict and (not item or item["id"] in seen):
                raise ValueError("invalid palette library")
            if item and item["id"] not in seen:
                out.append(item)
                seen.add(item["id"])
        return out
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        if strict:
            raise
        return []


def request(root, obj):
    op = obj.get("op")
    if op == "list":
        return {"ok": True, "palettes": read(root)}
    if op not in ("save", "delete"):
        return {"ok": False, "why": "bad_op"}
    item = clean(obj.get("palette")) if op == "save" else None
    cid = item["id"] if item else obj.get("id")
    if (op == "save" and item is None) or not isinstance(cid, str) or not ID.fullmatch(cid):
        return {"ok": False, "why": "bad_value"}
    try:
        with State(root).file_lock("card-palettes"):
            rows = read(root, strict=True)
            if op == "save" and len(rows) >= MAX and not any(r["id"] == cid for r in rows):
                return {"ok": False, "why": "full"}
            rows = [r for r in rows if r["id"] != cid]
            if item:
                rows.insert(0, item)
            jcards._write_json(root, ("cards",), "palettes.json", rows)
        return {"ok": True, "palettes": rows}
    except (OSError, ValueError):
        return {"ok": False, "why": "refused"}
