"""Session log (AGENTJ-CORE-SPEC 2.2 `core/session`, Pi session file / deepseek-harness event log): append-only JSONL, one
message per line, 0600. Loading it again IS resume — the next turn continues the same conversation (O3: a CEO that was
killed resumes with its context). Tool outputs are stored as the model saw them (already truncated and redacted).
P431 C5: a `compact` record replaces everything before the last `keep` messages with the summary message (compact.py);
`usage` records add up the tokens of the conversation since its last /clear (/cost, /usage)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class Session:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def load(self) -> list[dict]:
        out = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue                 # a torn last line after a kill: skipped
                    if isinstance(r, dict) and r.get("t") == "msg" and isinstance(r.get("m"), dict):
                        out.append(r["m"])
                    elif isinstance(r, dict) and r.get("t") == "clear":
                        out = []
                    elif isinstance(r, dict) and r.get("t") == "compact" and isinstance(r.get("m"), dict) \
                            and type(r.get("keep")) is int and r["keep"] >= 0:
                        out = [r["m"]] + (out[len(out) - r["keep"]:] if r["keep"] else [])
        except FileNotFoundError:
            pass
        # a step cut between the assistant's tool calls and their results cannot be replayed: drop the dangling tail
        for i in range(len(out) - 1, -1, -1):
            m = out[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids = {c.get("id") for c in m["tool_calls"]}
                got = {x.get("tool_call_id") for x in out[i + 1:] if x.get("role") == "tool"}
                if not ids <= got:
                    out = out[:i]
                break
        return out

    def append(self, m: dict) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": "msg", "ts": round(time.time(), 3), "m": m}, ensure_ascii=False) + "\n")

    def _record(self, obj: dict) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({**obj, "ts": round(time.time(), 3)}, ensure_ascii=False) + "\n")

    def compact(self, summary: dict, keep: int) -> None:
        self._record({"t": "compact", "m": summary, "keep": int(keep)})

    def add_usage(self, model: str, inp: int, out: int) -> None:
        if inp or out:
            self._record({"t": "usage", "model": str(model)[:100], "in": int(inp), "out": int(out)})

    def usage(self) -> dict:
        """{model: [input, output]} since the last clear; compactions count (the summary call is spent too)."""
        tot: dict = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(r, dict):
                        continue
                    if r.get("t") == "clear":
                        tot = {}
                    elif r.get("t") == "usage" and type(r.get("in")) is int and type(r.get("out")) is int:
                        t = tot.setdefault(str(r.get("model")), [0, 0])
                        t[0] += r["in"]
                        t[1] += r["out"]
        except FileNotFoundError:
            pass
        return tot

    def clear(self) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": "clear", "ts": round(time.time(), 3)}) + "\n")
