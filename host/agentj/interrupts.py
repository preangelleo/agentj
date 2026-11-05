"""0.26.9 #473 (E4): per task, how many cards the owner was shown and how many questions he was asked — counts only.

`<state>/interrupts.json` (0600): {"days": {"YYYY-MM-DD": {"<task key>": {"kind", "cards", "face", "questions"}}}}, 14 days.
A task key is opaque (the owner chain id that started it, or the turn's page id); no tool, command, summary or question
text is ever stored. Counted and written (a few KiB, 0600) next to approvals.log's own append when a card or question opens. `agentj approvals
--interruptions` (the good-morning brief) reads it.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

KEEP_DAYS = 14
MAX_TASKS = 400


def _path(root) -> Path:
    return Path(root) / "interrupts.json"


def _day(now=None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def read(root) -> dict:
    try:
        d = json.loads(_path(root).read_text())
        return d if isinstance(d, dict) and isinstance(d.get("days"), dict) else {"days": {}}
    except (OSError, ValueError):
        return {"days": {}}


class Counter:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.data = read(root)
        self.dirty = False

    def bump(self, task, kind: str, what: str, face: bool = False, now=None) -> None:
        if what not in ("cards", "questions"):
            return
        key = str(task or "none")[:64]
        with self.lock:
            day = self.data["days"].setdefault(_day(now), {})
            if key not in day and len(day) >= MAX_TASKS:
                key = "overflow"
            row = day.setdefault(key, {"kind": str(kind or "?")[:20], "cards": 0, "face": 0, "questions": 0})
            row[what] = int(row.get(what, 0)) + 1
            if face and what == "cards":
                row["face"] = int(row.get("face", 0)) + 1
            self.dirty = True

    def save(self) -> None:
        with self.lock:
            if not self.dirty:
                return
            days = sorted(self.data["days"])
            for d in days[:-KEEP_DAYS]:
                self.data["days"].pop(d, None)
            body = json.dumps(self.data, sort_keys=True).encode()
            self.dirty = False
        try:
            from .state import _write_private
            _write_private(_path(self.root), body)
        except OSError:
            pass


def summary(root, day: str) -> dict:
    """{day, tasks, cards, face_id, questions, per_task:[{task (ordinal), kind, cards, face_id, questions}]} — counts only."""
    rows = read(root)["days"].get(day) or {}
    per = [{"task": i + 1, "kind": v.get("kind", "?"), "cards": int(v.get("cards", 0)), "face_id": int(v.get("face", 0)),
            "questions": int(v.get("questions", 0))} for i, (_k, v) in enumerate(sorted(rows.items()))]
    return {"day": day, "tasks": len(per), "cards": sum(p["cards"] for p in per), "face_id": sum(p["face_id"] for p in per),
            "questions": sum(p["questions"] for p in per), "per_task": per}


def report(root, now=None) -> dict:
    now = time.time() if now is None else now
    return {"yesterday": summary(root, _day(now - 86400)), "today": summary(root, _day(now))}
