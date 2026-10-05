"""`agentj recall` (F22 as a main-Agent skill, 0.15.2; PROTOCOL §15.4): the main Agent finds an earlier conversation by
keyword and date — 「接着昨天那件事」 without a conversation list on the phone (PRODUCT_SPEC §0.1 principle 2).

What is searched: the phone's pages (history.py) — the current conversation and the archives `/clear` left (≤ 10), the
user's words (`src.text`) and the Agent's replies. Read-only; results are excerpts (EXCERPT characters per side) passed
through privacy.redact, so a key pasted into an old message does not come back in clear.

How the fenced Agent reaches it: the state directory is an empty tmpfs inside the fence (fence.py), so reading
`<state>/history` from the Agent's shell finds nothing. serve answers on `<state>/agentperm/recall.sock` — `agentperm/` is the
one folder bound back into the fence (like elevate.sock for `agentj sudo`); same-user peers only (SO_PEERCRED where the OS has
it; the socket is 0600 in a 0700 folder everywhere). serve also exports AGENTJ_RECALL_SOCK to the harness it starts, so a
custom state directory (AGENTJ_STATE_DIR is unset inside the fence) still works. Without serve (or unfenced on a computer
whose serve is down) the CLI reads the history files itself.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os
import pathlib
import socket
import struct
import sys
import time

SOCK_NAME = "recall.sock"
ENV = "AGENTJ_RECALL_SOCK"
EXCERPT = 300
LIMIT = 10
LIMIT_MAX = 50
Q_MAX = 200
REQ_MAX = 4096


# ------------------------------------------------------------------ the search (pure over turns)
def _day(ts_ms) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d") if isinstance(ts_ms, (int, float)) else ""


def _who(src: dict) -> str:
    k = src.get("k") if isinstance(src.get("k"), str) else "?"
    name = src.get("name") if isinstance(src.get("name"), str) else ""
    return (k + (":" + name[:64] if name else ""))[:80]


def _cut(s, n=EXCERPT) -> str:
    s = s if isinstance(s, str) else ""
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def search(turns, query: str = "", days: float | None = None, date: str | None = None, limit: int = LIMIT,
           now: float | None = None) -> list[dict]:
    """turns: iterable of (turn, archived). Every word of the query (case-insensitive) must appear in the page (the user's
    words, the reply, the sender's name). Newest first. Excerpts are redacted."""
    from .privacy import redact
    words = [w.casefold() for w in (query or "").split() if w][:20]
    now = time.time() if now is None else now
    since = now - days * 86400 if isinstance(days, (int, float)) and days > 0 else None
    out, seen = [], set()
    for t, archived in turns:
        if not isinstance(t, dict) or type(t.get("id")) is not int or t["id"] in seen:
            continue
        src = t.get("src") if isinstance(t.get("src"), dict) else {}
        reply = t.get("reply") if isinstance(t.get("reply"), dict) else {}
        ts = t.get("ts") if isinstance(t.get("ts"), (int, float)) else 0
        if since is not None and ts / 1000 < since:
            continue
        if date and _day(ts) != date:
            continue
        hay = " ".join(str(x) for x in (src.get("text"), reply.get("text"), src.get("name")) if isinstance(x, str)).casefold()
        if not all(w in hay for w in words):
            continue
        seen.add(t["id"])
        out.append({"id": t["id"], "ts": int(ts), "time": dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
                    if ts else "", "who": _who(src), "said": redact(_cut(src.get("text"))),
                    "reply": redact(_cut(reply.get("text"))), "archived": archived})
    out.sort(key=lambda h: (h["ts"], h["id"]), reverse=True)
    return out[:max(1, min(int(limit or LIMIT), LIMIT_MAX))]


def _read(path) -> list[dict]:
    from .history import History
    turns, _ = History._read(path)
    return list(turns.values())


def from_files(root: pathlib.Path, current: list[dict] | None = None):
    """(turn, archived) pairs: the current conversation (given, or current.jsonl) first, then the archives, newest first."""
    d = root / "history"
    for t in current if current is not None else _read(d / "current.jsonl"):
        yield t, False
    arch = d / "archive"
    files = []
    with contextlib.suppress(OSError):
        files = sorted(arch.glob("*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for p in files:
        for t in _read(p):
            yield t, True


def check(req: dict) -> dict:
    """The request's fields, bounded; ValueError on anything else."""
    if not isinstance(req, dict) or req.get("t") != "recall":
        raise ValueError("shape")
    q = req.get("q") or ""
    days, date, limit = req.get("days"), req.get("date"), req.get("limit") or LIMIT
    if not isinstance(q, str) or len(q) > Q_MAX:
        raise ValueError("q")
    if days is not None and (not isinstance(days, (int, float)) or isinstance(days, bool) or not 0 < days <= 3650):
        raise ValueError("days")
    if date is not None:
        if not isinstance(date, str):
            raise ValueError("date")
        dt.date.fromisoformat(date)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= LIMIT_MAX:
        raise ValueError("limit")
    return {"query": q, "days": days, "date": date, "limit": limit}


# ------------------------------------------------------------------ serve side
class Server:
    """Owned by serve.Host: `<state>/agentperm/recall.sock`, one JSON line in, one out."""

    def __init__(self, host):
        self.host = host
        self.server = None

    @property
    def path(self) -> pathlib.Path:
        return self.host.st.perm_dir / SOCK_NAME

    async def start(self) -> None:
        st = self.host.st
        st.perm_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(st.perm_dir, 0o700)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        old = os.umask(0o077)
        try:
            self.server = await asyncio.start_unix_server(self.on_client, path=str(self.path), limit=REQ_MAX)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        os.environ[ENV] = str(self.path)      # the harness serve starts inherits it (fence: AGENTJ_STATE_DIR is unset)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
            if os.environ.get(ENV) == str(self.path):
                os.environ.pop(ENV, None)

    def turns(self):
        h = getattr(self.host, "hist", None)
        cur = list(getattr(h, "turns", {}).values()) if h is not None else None   # memory: also when history is off
        return from_files(self.host.st.root, cur)

    async def on_client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            sock = w.get_extra_info("socket")
            if sock is not None and hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    return
            try:
                req = check(json.loads(await asyncio.wait_for(r.readline(), 10)))
                hits = await asyncio.to_thread(lambda: search(self.turns(), **req))
                res = {"ok": True, "hits": hits}
                self.host.st.log("recall", result="ok", status=str(len(hits)))
            except (ValueError, asyncio.TimeoutError, asyncio.LimitOverrunError):
                res = {"ok": False, "why": "shape"}
                self.host.st.log("recall", result="refused")
            w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
            await w.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(Exception):
                w.close()


# ------------------------------------------------------------------ CLI side (runs as the Agent, fenced or not)
def sock_paths() -> list[str]:
    out = []
    if os.environ.get(ENV):
        out.append(os.environ[ENV])
    with contextlib.suppress(Exception):
        from .state import State
        out.append(str(State().perm_dir / SOCK_NAME))
    return list(dict.fromkeys(out))


def ask(req: dict, timeout: float = 30) -> dict | None:
    """serve's answer, or None when no serve is listening."""
    for path in sock_paths():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                s.connect(path)
                s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                return json.loads(buf.split(b"\n", 1)[0])
            except (OSError, ValueError):
                continue
    return None


def add_parser(sub) -> None:
    rc = sub.add_parser("recall", help="找以前聊过的事：关键词 + 日期（主 Agent 用）/ find an earlier conversation by keyword and date",
                        description="Searches the phone's pages on this computer (current conversation + the archives /clear "
                                    "kept): every word must match. Read-only; excerpts are redacted. Works inside the fence "
                                    "through serve.")
    rc.add_argument("query", nargs="*", help="关键词（全部匹配）/ keywords (all must match); empty = the newest pages")
    rc.add_argument("--days", type=float, help="只看最近 N 天 / only the last N days")
    rc.add_argument("--date", help="只看这一天 YYYY-MM-DD / only this day")
    rc.add_argument("--limit", type=int, default=LIMIT, help=f"最多几条（≤ {LIMIT_MAX}）/ at most N hits")
    rc.add_argument("--json", action="store_true")
    rc.set_defaults(fn=cmd_recall)


def cmd_recall(a) -> None:
    req = {"t": "recall", "q": " ".join(a.query)[:Q_MAX], "days": a.days, "date": a.date,
           "limit": max(1, min(a.limit or LIMIT, LIMIT_MAX))}
    try:
        args = check(req)
    except ValueError as e:
        sys.exit(f"参数不对 / bad argument: {e}")
    res = ask(req)
    via = "serve"
    if res is None:                       # no serve: the files (unfenced; inside the fence the state dir is empty)
        from .state import State
        via = "files"
        root = State().root
        res = {"ok": True, "hits": search(from_files(root), **args)} if (root / "history").is_dir() else \
            {"ok": False, "why": "unavailable"}
    if a.json:
        print(json.dumps({**res, "via": via}, ensure_ascii=False, indent=1))
        sys.exit(0 if res.get("ok") else 1)
    if not res.get("ok"):
        sys.exit("找不到聊天记录：Agent J（serve）没有在运行，或这台电脑上还没有记录 / no history reachable: serve is not "
                 "running or nothing is kept yet")
    hits = res.get("hits") or []
    if not hits:
        print("没有找到 / nothing found")
        return
    for h in hits:
        print(f"#{h['id']}  {h['time']}  {h['who']}{'  (archived)' if h.get('archived') else ''}")
        if h.get("said"):
            print(f"  > {h['said']}")
        if h.get("reply"):
            print(f"  < {h['reply']}")
