"""#468: a time-boxed official PTY, separate from paired-owner input authority.

The granting customer's real Noise session receives all approvals. The official peer
has no device record, no passkey and no ability to answer those approvals. Production
requires the signed package's pinned official keys; source test overrides are unchanged.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import hashlib
import json
import os
import re
import secrets
import time

from . import assist_keys, controls, passkey, term_store, wire
from .terminal import Desk

CAP = "assist_term1"
PEER_TYPES = ("assist_term_open", "assist_term_in", "assist_term_resize", "assist_term_close")
ANSWERS = ("term_line", "term_confirm_no", "term_sudo_ok", "term_sudo_no")
DOMAIN = b"agentj-assist-terminal-v1\n"


def message(obj: dict) -> bytes:
    return DOMAIN + json.dumps({k: v for k, v in obj.items() if k != "sig"}, sort_keys=True,
                               separators=(",", ":"), ensure_ascii=False).encode()


def grant_digest(key_id: str, seat: str, minutes: int) -> str:
    return hashlib.sha256(f"assist_term_grant\n{key_id}\n{seat}\n{minutes}\nterminal".encode()).hexdigest()


class _Host:
    """Transport adapter, NOT a paired device. s remains the real customer session."""
    def __init__(self, terminal, desk):
        self.a, self.owner_desk = terminal, desk

    def __getattr__(self, name):
        if name == "elevate":
            return _Elevate(self.a, self.owner_desk)
        return getattr(self.a.h, name)

    async def send_app(self, s, event):
        a = self.a
        if a.desk is not self.owner_desk:
            return False
        if event.get("t") not in ("term_close",) and not a.live():
            return
        event = dict(event)
        pk = a.h.st.passkey_of_device(s.device)
        if event.get("t") in ("term_ready", "term_sudo", "term_confirm") and pk:
            event["fa"] = pk["id"]
        if event.get("t") == "term_ready" and a.desk is not None and a.desk.term is not None:
            event.update(cols=a.desk.term.cols, rows=a.desk.term.rows, expires_at=(a.assist.g or {}).get("until"))
        targets = [s]
        if event.get("t") in ("term_ready", "term_out", "term_close", "term_resize"):
            targets += [v for v in list(a.h.sessions.values()) if v is not s and v.state == "ready" and
                        a.h.st.is_allowed(v.pub) and getattr(v, "p33", False)]
        result = True
        for target in targets:
            if not self.owner_desk.login_guard():
                return False
            result = await a.h.send_app(target, {"t": "assist_term_event", "gid": a.gid, "event": event})
        # The official receives only display events. Never sudo/UV/PRF cards.
        if event.get("t") in ("term_ready", "term_out", "term_close", "term_resize") and self.owner_desk.login_guard():
            public = {k: v for k, v in event.items() if k not in ("fa", "sudo")}
            a.assist._send(a.assist.peer_id(), {"t": "assist_term_event", "gid": a.gid, "event": public})
        return result


class _Elevate:
    def __init__(self, a, desk):
        self.a, self.desk = a, desk

    async def request(self, card):
        if not self.desk.login_guard():
            return {"result": "cancelled"}
        sink = card.get("sink")
        def guarded(value):
            if not self.desk.login_guard():
                return {"result": "cancelled"}
            return sink(value)
        gone = asyncio.get_running_loop().create_future()
        self.a.login_gone = gone
        try:
            return await self.a.h.elevate.request({**card, "sink": guarded, "assist_gid": self.a.gid}, gone=gone)
        finally:
            if self.a.login_gone is gone:
                self.a.login_gone = None


class _Desk(Desk):
    def __init__(self, a):
        self.a, self.grant_id = a, a.gid
        super().__init__(_Host(a, self))

    def login_guard(self):
        return self.a.live() and self.a.desk is self and self.a.gid == self.grant_id

    def h_paired(self, s):
        # 0.30.1 hotfix (security review N1): an official assist open is not the owner's phone frame — never
        # borrow the phone's paired label for its cards; the assist grant + login_guard stay its authority.
        return contextlib.nullcontext()

    def _pty_write(self, x, text):
        # Includes held Enter, queued typeahead, color replies and async flushes.
        if not self.login_guard() or self.term is not x or x.closing or self.clock() >= x.expires:
            return False
        return super()._pty_write(x, text)

    async def _askpass(self, x, rd, wr):
        desk = self
        class Writer:
            def __getattr__(self, name):
                return getattr(wr, name)
            def write(self, data):
                return wr.write(data if desk.login_guard() else b"-")
        return await super()._askpass(x, rd, Writer())

    async def _sudo_take(self, x, rid, prf, vn=None):
        if not self.login_guard():
            return None
        value = await super()._sudo_take(x, rid, prf, vn)
        if not self.login_guard():
            if value is not None:
                from .elevate import wipe
                wipe(value)
            return None
        return value

    async def _start(self, s, r, mode, pw, size=None):
        if not self.login_guard() or getattr(self.a.h.term_desk, "term", None) is not None:
            if pw:
                from .elevate import wipe
                wipe(pw)
            raise RuntimeError("assist ended or terminal busy")
        await super()._start(s, r, mode, pw, size)

    async def _pty_spawn(self, x, *args, **kw):
        await super()._pty_spawn(x, *args, **kw)
        x.io.write_guard = lambda: self.login_guard() and self.term is x and not x.closing and self.clock() < x.expires

    def _pty_release(self, x, line):
        from .assist import audit
        from .term_mask import Masker
        if not self.login_guard() or self.term is not x or self.clock() >= x.expires:
            return
        mask = Masker()
        shown = (mask.feed(line + "\n") + mask.close()).strip()[:512]
        operation = {"at": int(self.a.assist.clock()), "command": shown,
                     "state": "shell_line_released", "exit_status": "unknown"}
        try:
            audit(self.st, "terminal_command", gid=self.a.gid, **operation)
        except OSError:
            self.a.failed = True
            self.a.assist._bg(self.a.fail("audit"))
            return
        self.a.operations.append(operation)
        self.a.operations_total += 1
        return super()._pty_release(x, line)

    async def _frame(self, x, data, prompt):
        if not self.login_guard() or self.term is not x:
            return
        if not await self.a.record("output", data=data) or not self.login_guard():
            return
        await super()._frame(x, data, prompt)

    async def _cast(self, x, kind, data):
        if not self.login_guard() or self.term is not x or not data:
            return
        if x.cast is None:
            await self.a.fail("audit")
            return
        ln = (json.dumps([round(self.clock() - x.t0, 3), kind, data], ensure_ascii=False) + "\n").encode()
        try:
            wrote = await asyncio.to_thread(os.write, x.cast, ln)
            if wrote != len(ln):
                raise OSError("short audit write")
        except OSError:
            if self.login_guard():
                await self.a.fail("audit")

    def _log(self, action, device, result, obj=None, **kw):
        if not self.login_guard():
            return
        # Exact line text is already masked in the normal cast; never record keys.
        from .assist import audit
        try:
            if result == "confirmed" and action == "term_line":
                self.a.confirmations += 1
            if result.startswith("refused:") or result == "denied":
                self.a.refusals += 1
            audit(self.st, "terminal_action", gid=self.a.gid, action=action, result=result,
                  actor="customer_confirmation" if action == "term_sudo" or result in ("confirmed", "denied", "refused:owner") else "official_terminal")
        except OSError:
            self.a.failed = True
            self.a.assist._bg(self.a.fail("audit"))

    async def tick(self):
        x = self.term
        if x is None or x.closing:
            return
        if not self.a.live():
            return await self.a.fail("ended")
        if self.clock() >= x.expires:
            return await self.a.fail("expired")
        if self.clock() - x.last >= term_store.IDLE_S:
            return await self.a.fail("idle")
        if self.pty_blocked():
            return await self.a.fail("fence")
        self.pty_tick(x)

    async def close(self, why):
        had = self.term is not None
        await super().close(why)
        if had and not self.a.assist.ending:
            await self.a.assist.end(why)


class Terminal:
    def __init__(self, assist):
        self.assist, self.h = assist, assist.h
        self.gid = None
        self.owner = None
        self.deadline = 0.0
        self.desk = None
        self.failed = False
        self.lock = asyncio.Lock()
        self.inputs = 0
        self.report = None
        self.operations = collections.deque(maxlen=64)
        self.operations_total = 0
        self.official_claim = None
        self.discovery = "not_started"
        self.discovery_retry_at = 0.0
        self.login_gone = None

    def live(self):
        g, s = self.assist.live(), self.owner
        return bool(not self.failed and time.monotonic() < self.deadline and g and g.get("scope") == "terminal" and g["id"] == self.gid and s is not None
                    and self.h.sessions.get(s.cid) is s and s.state == "ready" and self.h.st.is_allowed(s.pub)
                    and self.h.st.passkey_of_device(s.device) and s.device == g["granted_by"])

    async def record(self, ev, **kw):
        from .assist import audit
        gid = self.gid
        try:
            await asyncio.to_thread(audit, self.h.st, "terminal_" + ev, gid=gid, **kw)
            return True
        except OSError:
            if self.gid == gid:
                await self.fail("audit")
            return False

    async def fail(self, why):
        self.failed = True
        await self.assist.end(why)

    def bind(self, s, gid):
        self.owner, self.gid, self.failed = s, gid, False
        self.deadline = time.monotonic() + max(0, (self.assist.g or {}).get("until", 0) - self.assist.clock())
        self.inputs, self.report = 0, None
        self.confirmations, self.refusals = 0, 0
        self.operations.clear()
        self.operations_total = 0
        self.official_claim = None
        self.discovery = "publishing"

    async def end(self, why):
        if self.gid is None:
            self.gid = (self.assist.g or {}).get("id")
        if self.login_gone is not None and not self.login_gone.done():
            self.login_gone.set_result(None)
        desk = self.desk
        self.desk = None
        if desk is not None:
            await desk.close(why)
        self.report = {"id": self.gid, "ended_at": int(self.assist.clock()), "reason": why,
                       "input_frames": self.inputs, "result": "session_ended",
                       "key_id": (self.assist.g or {}).get("key_id"),
                       "started_at": (self.assist.g or {}).get("not_before"),
                       "confirmed_actions": getattr(self, "confirmations", 0),
                       "operations": list(self.operations),
                       "operations_total": self.operations_total,
                       "official_statement": self.official_claim or {"summary": "未提交修复结论 / No repair conclusion submitted"},
                       "scope": "terminal",
                       "refused_actions": getattr(self, "refusals", 0),
                       "unresolved": ["Repair outcome must be verified; session termination alone is not proof of repair."],
                       "note": "操作会话已结束；终止不撤销已完成的改动。 / Session ended; completed changes are not undone."}
        from .assist import audit, dir_of
        try:
            await asyncio.to_thread(audit, self.h.st, "terminal_report", gid=self.gid, report=report_view(self.report),
                                    official_statement=self.official_claim, full_report_id=self.gid)
            data = json.dumps(self.report, ensure_ascii=False).encode()
            gid = self.gid
            def write_reports():
                with self.assist.glock:
                    self.h.st.write_private(dir_of(self.h.st) / ("report-" + gid + ".json"), data)
                    self.h.st.write_private(dir_of(self.h.st) / "report.json", data)
            await asyncio.to_thread(write_reports)
        except OSError:
            self.failed = True
            self.report = {**self.report, "audit_failed": True}
        if self.owner is not None:
            with contextlib.suppress(Exception):
                await self.h.send_app(self.owner, customer_message({"t": "assist_report", "gid": self.gid, "report": self.report}))

    def accept(self, fid, obj):
        from .assist import _ed_ok, TS_SKEW_MS
        g = self.assist.live()
        t = obj.get("t")
        if t not in PEER_TYPES or not self.live() or fid != self.assist.peer_id():
            return self.assist.refuse("terminal_grant", str(t))
        if (obj.get("gid") != g["id"] or obj.get("seat") != g["seat"] or type(obj.get("seq")) is not int
                or obj["seq"] <= g.get("seq", 0) or type(obj.get("ts")) is not int
                or abs(int(self.assist.clock() * 1000) - obj["ts"]) > TS_SKEW_MS
                or not g["not_before"] * 1000 - TS_SKEW_MS <= obj["ts"] < g["until"] * 1000):
            return self.assist.refuse("terminal_binding", t)
        k = assist_keys.by_id(g["key_id"], self.assist.clock())
        if not k or not _ed_ok(k["ed25519"], obj.get("sig"), message(obj)):
            return self.assist.refuse("terminal_signature", t)
        if t == "assist_term_in" and (not isinstance(obj.get("d"), str) or not obj["d"] or
                len(obj["d"].encode("utf-8", "replace")) > 8192 or obj.get("reply", False) not in (True, False)):
            return self.assist.refuse("terminal_shape", t)
        if t in ("assist_term_open", "assist_term_resize"):
            from .term_pty import size_ok
            if not size_ok(obj.get("cols", 80), obj.get("rows", 24)):
                return self.assist.refuse("terminal_shape", t)
        g["seq"] = obj["seq"]
        self.assist._bg(self.apply(g, dict(obj)))

    async def apply(self, g, obj):
        async with self.lock:
            if not self.live():
                return
            try:
                if not await asyncio.to_thread(self.assist._write_seq, g):
                    return await self.fail("audit")
            except OSError:
                return await self.fail("audit")
            t = obj["t"]
            if not await self.record("received", action=t, seq=obj["seq"], bytes=len(obj.get("d", "").encode())):
                return
            if t == "assist_term_close":
                from .privacy import owner_text
                fields = ("summary", "diagnosis", "verification")
                if any(k in obj and (not isinstance(obj[k], str) or len(obj[k]) > 2000) for k in fields):
                    return self.assist.refuse("terminal_shape", t)
                self.official_claim = {k: owner_text(obj[k]) for k in fields if k in obj} or None
                return await self.assist.end("official_closed")
            if t == "assist_term_open":
                if self.desk is not None or getattr(getattr(self.h, "term_desk", None), "term", None) is not None:
                    return self.assist.refuse("terminal_busy", t)
                self.desk = _Desk(self)
                if not self.desk.pty_supported() or self.desk.pty_blocked():
                    return await self.fail("fence")
                try:
                    # Grant authorizes only a clean same-user PTY. No owner password
                    # is copied into it; sudo remains an independent customer card.
                    pk = self.h.st.passkey_of_device(self.owner.device)
                    pend = {"mode": "pty", "size": (obj.get("cols", 80), obj.get("rows", 24)),
                            "salt": secrets.token_bytes(32)}
                    await self.desk._open(self.owner, g["id"][:16], pend, None, pk)
                    if self.assist.g is not g:
                        return
                    if self.desk is None or self.desk.term is None:
                        return await self.fail("terminal_login")
                    x = self.desk.term
                    remaining = max(0, g["until"] - self.assist.clock())
                    x.expires, x.expires_at = self.desk.clock() + remaining, g["until"]
                    if not self.live():
                        await self.fail("ended")
                except Exception:
                    if self.assist.g is g:
                        await self.fail("terminal_start")
                return
            desk = self.desk
            x = desk.term if desk else None
            if x is None or x.closing:
                return self.assist.refuse("terminal_not_open", t)
            if t == "assist_term_resize":
                await desk.on_resize(x, self.owner, obj)
                await desk.h.send_app(self.owner, {"t": "term_resize", "id": x.id, "cols": x.cols, "rows": x.rows})
                return
            self.inputs += 1
            # Direct peer entry, separate from on_phone's paired writer checks.
            # All customer callbacks keep x.s and x.device as their real recipient.
            await desk.on_term_in(x, self.owner, obj)

    async def answer(self, s, obj):
        if not self.live() or s is not self.owner or obj.get("gid") != self.gid:
            return
        answer = obj.get("obj")
        if not isinstance(answer, dict) or answer.get("t") not in ANSWERS:
            return
        desk = self.desk
        if desk is None or desk.term is None or answer.get("id") != desk.term.id:
            return
        if not await self.record("customer_answer", action=answer["t"]):
            return
        # term_line must be an answer to an existing held confirmation, never a
        # fresh line. Only that exact nonce/line can release the pending Enter.
        if answer["t"] == "term_line" and (desk.term.hold != "confirm" or answer.get("n") != desk.term.hold_n):
            return
        await desk.on_phone(s, answer)


    async def observer(self, s):
        desk = self.desk
        x = desk.term if desk else None
        if not self.live() or x is None or x.closing:
            return
        async with x.frame_lock:
            if not self.live() or self.desk is not desk or desk.term is not x:
                return
            ready = {**x.ready_msg, "cols": x.cols, "rows": x.rows, "expires_at": (self.assist.g or {}).get("until")}
            if not await self.h.send_app(s, {"t": "assist_term_event", "gid": self.gid, "event": ready}):
                return
            replay = list(x.replay)
            start = max(0, x.seq - len(replay))
            for i, (data, prompt) in enumerate(replay, 1):
                if not self.live() or self.h.sessions.get(s.cid) is not s or not self.h.st.is_allowed(s.pub):
                    return
                event = {"t": "term_out", "id": x.id, "seq": start + i, "d": data}
                if prompt:
                    event["prompt"] = True
                await self.h.send_app(s, {"t": "assist_term_event", "gid": self.gid, "event": event})

    async def log(self, s, obj):
        from .assist import dir_of
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        if self.h.sessions.get(s.cid) is not s or s.state != "ready" or not self.h.st.is_allowed(s.pub):
            return await self.h.send_app(s, {"t": "assist_log_res", "r": r, "entries": [], "report": None,
                                              "more": False, "next_cursor": None, "why": "not_owner"})
        def read():
            requested = obj.get("gid")
            if requested is not None and (not isinstance(requested, str) or not _GID.fullmatch(requested)):
                return {"gid": None, "entries": [], "report": None, "next_cursor": None, "more": False, "why": "shape"}
            report = None
            target = requested or self.gid
            path = dir_of(self.h.st) / (("report-" + target + ".json") if target else "report.json")
            try:
                with path.open("rb") as f:
                    raw = f.read(256 * 1024 + 1)
                if len(raw) <= 256 * 1024:
                    candidate = json.loads(raw)
                    if isinstance(candidate, dict) and isinstance(candidate.get("id"), str) and _GID.fullmatch(candidate["id"]):
                        if target is None or target == candidate["id"]:
                            report, target = candidate, candidate["id"]
            except (OSError, ValueError, UnicodeDecodeError):
                pass
            if target is None:
                return {"gid": None, "entries": [], "report": report, "next_cursor": None, "more": False}
            return {**log_page(self.h.st, target, obj.get("cursor")), "report": report}
        result = await asyncio.to_thread(read)
        await self.h.send_app(s, customer_message({"t": "assist_log_res", "r": r, **result}))


def _text_view(value, max_bytes):
    text = value if isinstance(value, str) else ""
    raw = text.encode("utf-8", "replace")
    value = raw[:max_bytes].decode("utf-8", "ignore")
    if len(wire.dumps(value)) <= max_bytes + 2:
        return value
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(wire.dumps(value[:mid])) <= max_bytes + 2:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo]


def report_view(report, max_bytes=12 * 1024):
    """A clearly labelled display summary; the complete report stays host-local."""
    if not isinstance(report, dict):
        return None
    out = {}
    for key in ("id", "scope", "key_id", "reason", "result", "note"):
        if key in report:
            out[key] = _text_view(report[key], 512 if key == "note" else 100)
    for key in ("started_at", "ended_at", "input_frames", "confirmed_actions", "refused_actions"):
        if type(report.get(key)) is int:
            out[key] = report[key]
    if report.get("audit_failed"):
        out["audit_failed"] = True
    unresolved = report.get("unresolved") if isinstance(report.get("unresolved"), list) else []
    out["unresolved"] = [_text_view(v, 256) for v in unresolved[:8] if isinstance(v, str)]
    statement = report.get("official_statement")
    out["official_statement"] = {k: _text_view(statement[k], 1000) for k in ("summary", "diagnosis", "verification")
                                 if isinstance(statement, dict) and isinstance(statement.get(k), str)}
    operations = report.get("operations") if isinstance(report.get("operations"), list) else []
    total = report.get("operations_total")
    out["operations_total"] = total if type(total) is int and total >= len(operations) else len(operations)
    out.update(operations=[], operations_shown=0, operations_truncated=bool(out["operations_total"]),
               report_view="bounded_summary", full_audit_via_pages=True, full_report_local=True)
    for op in reversed(operations):
        if not isinstance(op, dict):
            continue
        item = {k: _text_view(op[k], 1600 if k == "command" else 80) for k in ("command", "state", "exit_status") if k in op}
        if type(op.get("at")) is int:
            item["at"] = op["at"]
        candidate = {**out, "operations": [item, *out["operations"]], "operations_shown": out["operations_shown"] + 1}
        candidate["operations_truncated"] = candidate["operations_shown"] < candidate["operations_total"]
        if len(wire.dumps(candidate)) > max_bytes:
            break
        out = candidate
    return out


def customer_message(obj):
    """Use the existing P33 fragmentation/reassembly, with a bounded report view.

    Log entries are never silently trimmed to fit a physical frame: Host.send_app
    owns that existing transport contract. Pagination remains bounded separately.
    """
    result = dict(obj)
    if "report" in result:
        result["report"] = report_view(result["report"])
    if len(wire.dumps(result)) > wire.FRAG_MAX_TOTAL:
        raise ValueError("assist display exceeds existing reassembly limit")
    return result


_GID = re.compile(r"[0-9a-f]{24}")
_CURSOR = re.compile(r"v1:([0-9a-f]{24}):([0-9a-f]{1,16}):([0-9a-f]{1,16}):([0-9a-f]{1,16})")


def log_page(st, gid, cursor=None):
    """Read one bounded, backwards page for one grant; cursors die on prune.

    The cursor is advisory position, not authority. The authenticated customer
    session and exact per-record gid filter decide which data can be returned.
    No caller-supplied path, whole-file read or unbounded readline is used.
    """
    from .assist import dir_of
    empty = {"gid": gid, "entries": [], "next_cursor": None, "more": False}
    if not isinstance(gid, str) or not _GID.fullmatch(gid):
        return {**empty, "why": "shape"}
    parsed = None
    if cursor is not None:
        parsed = _CURSOR.fullmatch(cursor) if isinstance(cursor, str) and len(cursor) <= 120 else None
        if parsed is None or parsed.group(1) != gid:
            return {**empty, "why": "bad_cursor"}
    path = dir_of(st) / "log.jsonl"
    try:
        with path.open("rb") as f:
            meta = os.fstat(f.fileno())
            end = meta.st_size
            if parsed is not None:
                dev, ino, end = (int(parsed.group(i), 16) for i in (2, 3, 4))
                if (dev, ino) != (meta.st_dev, meta.st_ino) or end > meta.st_size:
                    return {**empty, "why": "pruned"}
            start = max(0, end - 64 * 1024)
            f.seek(start)
            raw = f.read(end - start)
        current = path.stat()
        if (current.st_dev, current.st_ino) != (meta.st_dev, meta.st_ino):
            return {**empty, "why": "pruned"}
    except OSError:
        return {**empty, **({"why": "pruned"} if cursor is not None else {})}
    base = start
    if start:
        nl = raw.find(b"\n")
        if nl < 0:
            token = f"v1:{gid}:{meta.st_dev:x}:{meta.st_ino:x}:{start:x}" if start else None
            return {**empty, "next_cursor": token, "more": bool(start), "why": "unreadable_line"}
        base, raw = start + nl + 1, raw[nl + 1:]
    rows, pos, unreadable = [], base, False
    for line in raw.splitlines(keepends=True):
        line_start, pos = pos, pos + len(line)
        if not line.endswith(b"\n"):
            unreadable = True
            continue
        try:
            entry = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            unreadable = True
            continue
        if isinstance(entry, dict) and entry.get("gid") == gid:
            rows.append((line_start, entry))
    if len(rows) > 200:
        rows = rows[-200:]
        next_end = rows[0][0]
    else:
        next_end = base if start and base < end else start
    more = next_end > 0
    token = f"v1:{gid}:{meta.st_dev:x}:{meta.st_ino:x}:{next_end:x}" if more else None
    return {"gid": gid, "entries": [entry for _, entry in rows], "next_cursor": token, "more": more,
            **({"why": "unreadable_line"} if unreadable else {})}
