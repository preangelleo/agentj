"""Official side of #468. Uses the running host's existing PeerService identity.

Invitations contain public routing data only. No separate mailbox connection or private
key export. Import is a local operator action; an invitation is never execution authority:
only a fresh live customer grant allows the terminal at the customer's host.
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import time

from . import assist_keys, wire
from .assist import _ed_ok, _mkdir, audit, canonical, dir_of, hello_message
from .assist_terminal import message
from .peer import agent_id, mbox_of

INVITE_DOMAIN = b"agentj-assist-invite-v1\n"
_GID = re.compile(r"[0-9a-f]{24}")


def invite_message(obj):
    return INVITE_DOMAIN + canonical({k: v for k, v in obj.items() if k != "sig"})


def validate(obj, now=None):
    now = time.time() if now is None else now
    fields = {"v", "scope", "gid", "seat", "key_id", "not_before", "until", "aid", "x25519", "ed25519", "sig"}
    if not isinstance(obj, dict) or set(obj) != fields or len(canonical(obj)) > 4096:
        return None
    try:
        x, ed = wire.unb64u(obj["x25519"]), wire.unb64u(obj["ed25519"])
        if (type(obj.get("v")) is not int or obj.get("v") != 1 or obj.get("scope") != "terminal" or not _GID.fullmatch(obj["gid"])
                or len(x) != 32 or len(ed) != 32 or obj["aid"] != agent_id(x, ed)
                or not isinstance(obj.get("seat"), str) or not re.fullmatch(r"[a-z2-7]{8}", obj["seat"])
                or type(obj.get("not_before")) is not int or type(obj.get("until")) is not int
                or not obj["not_before"] - 120 <= now < obj["until"]
                or not 0 < obj["until"] - obj["not_before"] <= 3600
                or not isinstance(obj.get("key_id"), str) or not _ed_ok(ed, obj.get("sig"), invite_message(obj))):
            return None
        return dict(obj)
    except (KeyError, ValueError, TypeError):
        return None


class Operator:
    def __init__(self, assist):
        self.a, self.h, self.st = assist, assist.h, assist.st
        self.rows = {}
        self.ready = {}
        self.queues = {}
        self.seq = {}
        self.deadlines = {}
        self.hello_at = {}
        self.discover_at = 0.0
        self.discovering = False
        self.discovery_state = "not_checked"
        self.registry_dirty = False
        self.ended = {}
        try:
            ended = json.loads((dir_of(self.st) / "operator-ended.json").read_text())
        except (OSError, ValueError):
            ended = {}
        for gid, until in ended.items() if isinstance(ended, dict) else []:
            if isinstance(gid, str) and _GID.fullmatch(gid) and type(until) is int and self.a.clock() < until <= self.a.clock() + 3600:
                self.ended[gid] = until
        try:
            rows = json.loads((dir_of(self.st) / "operator-invites.json").read_text())
        except (OSError, ValueError):
            rows = []
        for obj in rows if isinstance(rows, list) else []:
            valid = validate(obj)
            if not valid and isinstance(obj, dict) and type(obj.get("not_before")) is int:
                # Only mark authenticated PUBLIC invitations as expired cleanup;
                # malformed/foreign records never become execution authority.
                was_valid = validate(obj, obj["not_before"])
                if was_valid and obj["until"] <= self.a.clock():
                    self.registry_dirty = True
            if valid and valid["gid"] in self.ended:
                self.registry_dirty = True
                continue
            if valid:
                self.rows[valid["gid"]] = valid
                self.deadlines[valid["gid"]] = time.monotonic() + max(0, valid["until"] - time.time())

    def keys(self):
        return getattr(getattr(self.h, "peers", None), "keys", None)

    def authorized(self, row):
        k = assist_keys.by_id(row["key_id"], self.a.clock())
        me = self.keys()
        return bool(k and me and me.x_pub == k["x25519"] and me.ed_pub == k["ed25519"])

    def live_rows(self):
        now = self.a.clock()
        return [r for r in self.rows.values() if r["gid"] not in self.ended and r["until"] > now and
                time.monotonic() < self.deadlines.get(r["gid"], 0) and self.authorized(r)]

    def row_for(self, aid, gid=None):
        rows = [r for r in self.live_rows() if r["aid"] == aid and (gid is None or r["gid"] == gid)]
        return max(rows, key=lambda r: (r["not_before"], r["gid"])) if rows else None

    def keys_of(self, aid):
        r = self.row_for(aid)
        return (wire.unb64u(r["x25519"]), wire.unb64u(r["ed25519"])) if r else None

    def by_mbox(self, mb):
        return next((r["aid"] for r in self.live_rows() if mbox_of(r["aid"]) == mb), None)

    def on_app(self, fid, obj):
        gid = obj.get("gid")
        if not isinstance(gid, str):
            return
        row = self.row_for(fid, gid)
        if row is None:
            return
        gid = row["gid"]
        if obj.get("t") == "assist_ready":
            if (obj.get("gid") != gid or obj.get("seat") != row["seat"] or obj.get("until") != row["until"]
                    or obj.get("scope") != "terminal" or type(obj.get("seq")) is not int):
                return
            self.ready[gid] = {"at": time.monotonic(), "seq": obj["seq"]}
            self.seq[gid] = max(self.seq.get(gid, 0), obj["seq"])
        elif obj.get("t") == "assist_term_end" and obj.get("gid") == gid:
            self.ended[gid] = row["until"]
            self.rows.pop(gid, None)
            for cache in (self.queues, self.ready, self.deadlines, self.seq, self.hello_at):
                cache.pop(gid, None)
            try:
                _mkdir(self.st)
                self.st.write_private(dir_of(self.st) / "operator-ended.json", canonical(self.ended))
                self.st.write_private(dir_of(self.st) / "operator-invites.json", canonical(list(self.rows.values())))
            except OSError:
                self.a.log("assist_operator_state_failed", reason="end_persist")
        elif obj.get("t") == "assist_term_event" and obj.get("gid") == gid:
            ev = obj.get("event")
            if not isinstance(ev, dict) or ev.get("t") not in ("term_ready", "term_out", "term_close", "term_resize"):
                return
            q = self.queues.setdefault(gid, collections.deque(maxlen=256))
            q.append(obj)
            if ev.get("t") == "term_close":
                self.ready.pop(gid, None)

    def outbound(self, fid, obj):
        row = self.row_for(fid, obj.get("gid"))
        return bool(row and row["gid"] == obj.get("gid") and self.fresh(row))

    def fresh(self, row):
        r = self.ready.get(row["gid"])
        return bool(r and time.monotonic() - r["at"] < 60 and row in self.live_rows())

    def hello(self, row):
        now = time.monotonic()
        if now - self.hello_at.get(row["gid"], 0) < 10:
            return True
        self.hello_at[row["gid"]] = now
        k = self.keys()
        ts = int(self.a.clock() * 1000)
        return self.a._send(row["aid"], {"t": "assist_hello", "ts": ts,
                                       "sig": wire.b64u(k.ed.sign(hello_message(row["aid"], ts)))})

    def invitation(self):
        g, keys = self.a.live(), self.keys()
        if not g or g.get("scope") != "terminal" or not self.a.terminal.live() or keys is None:
            return None
        obj = {"v": 1, "scope": "terminal", "gid": g["id"], "seat": g["seat"], "key_id": g["key_id"],
               "not_before": g["not_before"], "until": g["until"], "aid": keys.id,
               "x25519": wire.b64u(keys.x_pub), "ed25519": wire.b64u(keys.ed_pub)}
        return {**obj, "sig": wire.b64u(keys.ed.sign(invite_message(obj)))}

    def prune_registry(self):
        now, mono = self.a.clock(), time.monotonic()
        expired_ends = [gid for gid, until in self.ended.items() if until <= now]
        gone = [gid for gid, row in self.rows.items() if gid in self.ended or row["until"] <= now or
                mono >= self.deadlines.get(gid, 0) or not self.authorized(row)]
        if not gone and not expired_ends and not self.registry_dirty:
            return
        kept = {gid: row for gid, row in self.rows.items() if gid not in gone}
        _mkdir(self.st)
        self.st.write_private(dir_of(self.st) / "operator-invites.json", canonical(list(kept.values())))
        self.rows = kept
        for gid in expired_ends:
            self.ended.pop(gid, None)
        if expired_ends:
            self.st.write_private(dir_of(self.st) / "operator-ended.json", canonical(self.ended))
        for gid in gone:
            for cache in (self.queues, self.ready, self.deadlines, self.seq, self.hello_at):
                cache.pop(gid, None)
        self.registry_dirty = False

    async def ctl(self, req):
        action = req.get("action")
        if action in ("import", "list"):
            self.prune_registry()
        if action == "invite":
            obj = self.invitation()
            return {"ok": bool(obj), "invite": obj, "why": None if obj else "no_terminal_grant"}
        if action == "import":
            row = validate(req.get("invite"), self.a.clock())
            if row is None or not self.authorized(row):
                return {"ok": False, "why": "invalid_or_unpinned_invite"}
            if row["gid"] in self.ended:
                return {"ok": False, "why": "ended"}
            if row["gid"] in self.rows and self.rows[row["gid"]] != row:
                return {"ok": False, "why": "invite_conflict"}
            same = [r for r in self.rows.values() if r["aid"] == row["aid"] and r["gid"] != row["gid"]]
            if len(self.rows) - len(same) >= 128 and row["gid"] not in self.rows:
                return {"ok": False, "why": "registry_full"}
            if any(r["not_before"] > row["not_before"] for r in same):
                return {"ok": False, "why": "superseded_invite"}
            for previous in same:
                self.rows.pop(previous["gid"], None)
                self.ready.pop(previous["gid"], None)
                self.queues.pop(previous["gid"], None)
            self.rows[row["gid"]] = row
            self.deadlines[row["gid"]] = time.monotonic() + max(0, row["until"] - self.a.clock())
            audit(self.st, "operator_import", gid=row["gid"], key_id=row["key_id"], uid=os.getuid())
            self.st.write_private(dir_of(self.st) / "operator-invites.json", canonical(list(self.rows.values())))
            await self.a._apply_peers()
            self.hello(row)
            return {"ok": True, "gid": row["gid"]}
        if action == "list":
            self.kick_discovery()
            rows = []
            for row in self.live_rows():
                live = self.fresh(row)
                self.hello(row)
                rows.append({k: row[k] for k in ("gid", "aid", "until", "key_id", "seat")} |
                            {"live": live, "status": "live" if live else "checking"})
            return {"ok": True, "sessions": rows, "discovery": self.discovery_state}
        gid = req.get("gid")
        if isinstance(gid, str) and gid in self.ended:
            return {"ok": False, "why": "ended"}
        row = next((r for r in self.live_rows() if r["gid"] == gid), None)
        if row is None:
            return {"ok": False, "why": "no_authorized_session"}
        if action == "read":
            q = self.queues.get(gid, [])
            events = []
            # Match Host._ctl_send's real JSON representation, leaving headroom
            # under names.ctl_call's unchanged default64KiB reader.
            while q:
                event = q[0]
                candidate = {"ok": True, "events": [*events, event], "more": True}
                if len(json.dumps(candidate, ensure_ascii=False).encode()) + 1 > 56 * 1024:
                    if not events and isinstance(event, dict) and event.get("event", {}).get("t") == "term_out":
                        data = event["event"].get("d")
                        if isinstance(data, str) and len(data) > 1:
                            lo, hi = 1, len(data)
                            while lo < hi:
                                mid = (lo + hi + 1) // 2
                                part = {**event, "event": {**event["event"], "d": data[:mid]}}
                                size = len(json.dumps({"ok": True, "events": [part], "more": True}, ensure_ascii=False).encode()) + 1
                                if size <= 56 * 1024:
                                    lo = mid
                                else:
                                    hi = mid - 1
                            part = {**event, "event": {**event["event"], "d": data[:lo]}}
                            if len(json.dumps({"ok": True, "events": [part], "more": True}, ensure_ascii=False).encode()) + 1 <= 56 * 1024:
                                events.append(part)
                                q[0] = {**event, "event": {**event["event"], "d": data[lo:]}}
                                break
                    if not events:
                        return {"ok": False, "why": "event_too_large"}
                    break
                events.append(q.popleft() if hasattr(q, "popleft") else q.pop(0))
            self.hello(row)  # refresh independently of keyboard activity
            return {"ok": True, "events": events, "more": bool(q)}
        if not self.fresh(row):
            self.hello(row)
            return {"ok": False, "why": "grant_not_verified"}
        if action not in ("open", "in", "resize", "close"):
            return {"ok": False, "why": "action"}
        obj = {"t": "assist_term_" + action, "gid": gid, "seat": row["seat"],
               "seq": self.seq.get(gid, 0) + 1, "ts": int(self.a.clock() * 1000)}
        if action == "close":
            for name in ("summary", "diagnosis", "verification"):
                value = req.get(name)
                if value is not None:
                    if not isinstance(value, str) or len(value) > 2000:
                        return {"ok": False, "why": "shape"}
                    obj[name] = value
        if action == "in":
            d = req.get("d")
            if not isinstance(d, str) or not d or len(d.encode("utf-8", "replace")) > 8192:
                return {"ok": False, "why": "shape"}
            obj["d"] = d
            from .term_pty import terminal_reply
            if terminal_reply(d):
                obj["reply"] = True
        if action in ("open", "resize"):
            from .term_pty import size_ok
            if not size_ok(req.get("cols", 80), req.get("rows", 24)):
                return {"ok": False, "why": "shape"}
            obj.update(cols=req.get("cols", 80), rows=req.get("rows", 24))
        audit(self.st, "operator_" + action, gid=gid, uid=os.getuid(), seq=obj["seq"])
        self.seq[gid] = obj["seq"]
        obj["sig"] = wire.b64u(self.keys().ed.sign(message(obj)))
        return {"ok": self.a._send(row["aid"], obj)}


    def kick_discovery(self):
        if self.discovering or time.monotonic() - self.discover_at < 10:
            return
        self.discover_at = time.monotonic()
        self.discovering = True
        self.a._bg(self.discover())

    async def discover(self):
        import asyncio
        import secrets
        from . import cloud
        try:
            me = self.keys()
            key = next((k for k in assist_keys.load(self.a.clock()) if me and k["x25519"] == me.x_pub and k["ed25519"] == me.ed_pub), None)
            if not key:
                self.discovery_state = "not_official"
                return
            obj = {"v": 1, "key_id": key["id"], "ts": int(self.a.clock()), "nonce": secrets.token_hex(16)}
            obj["sig"] = wire.b64u(me.ed.sign(b"agentj-assist-discovery-v1\n" + canonical(obj)))
            code, res = await asyncio.to_thread(cloud.post_json, cloud.api_url(self.st) + "/v1/assist/list", obj,
                                                timeout=8, max_response=512 * 1024)
            if code != 200 or not isinstance(res, dict) or res.get("ok") is not True:
                self.discovery_state = "unavailable"
                return
            for invite in (res.get("invites") or [])[:128]:
                await self.ctl({"action": "import", "invite": invite})
            self.discovery_state = "ok"
        except Exception:
            self.discovery_state = "unavailable"
        finally:
            self.discovering = False

    async def publish(self, invite):
        import asyncio
        from . import cloud
        try:
            if not cloud.read_cloud(self.st):
                raise ValueError("unbound")
            obj = {"v": 1, "t": "assist_publish", "channel": cloud.channel_of(self.st), "ts": int(self.a.clock()), "invite": invite}
            code, res = await asyncio.to_thread(cloud.post_json, cloud.api_url(self.st) + "/v1/host/assist/publish",
                                                cloud.envelope("agentj-host-assist-publish-v1", obj, self.st.signing_key()),
                                                timeout=8, max_response=16384)
            if code != 200 or not isinstance(res, dict) or res.get("ok") is not True:
                raise ValueError("unavailable")
            if self.a.terminal.gid == invite.get("gid") and self.a.terminal.live():
                self.a.terminal.discovery = "ready"
                self.a.broadcast_state()
                self.a.notice("discoverable")
        except Exception:
            if self.a.terminal.gid == invite.get("gid") and self.a.terminal.live():
                self.a.terminal.discovery = "failed"
                self.a.broadcast_state()
                self.a.notice("discover_failed")

    async def revoke_published(self, gid):
        import asyncio
        from . import cloud
        try:
            if not cloud.read_cloud(self.st):
                return
            obj = {"v": 1, "t": "assist_revoke", "channel": cloud.channel_of(self.st), "ts": int(self.a.clock()), "gid": gid}
            await asyncio.to_thread(cloud.post_json, cloud.api_url(self.st) + "/v1/host/assist/revoke",
                                    cloud.envelope("agentj-host-assist-revoke-v1", obj, self.st.signing_key()),
                                    timeout=8, max_response=16384)
        except Exception:
            pass  # Cloud discovery never determines the customer's live authorization.


def cmd(a):
    """CLI: all mutations/signatures go through the existing private host socket."""
    import select
    import sys
    from . import names
    from .state import State
    st = State()
    def call(action, **kw):
        try:
            r = names.ctl_call(st, {"cmd": "assist_operator", "action": action, **kw}, 12)
        except (OSError, names.ServeBusy):
            r = None
        if not isinstance(r, dict):
            raise SystemExit("Agent J host is not responding / 主机暂未响应")
        return r
    action = a.action
    if action == "import":
        if a.file == "-":
            raw = sys.stdin.read(8193)
        else:
            with pathlib.Path(a.file).expanduser().open() as f:
                raw = f.read(8193)
        if len(raw) > 8192:
            raise SystemExit("Invitation too large")
        try:
            obj = json.loads(raw)
        except ValueError:
            raise SystemExit("Invalid public invitation") from None
        result = call("import", invite=obj)
    elif action in ("list", "invite"):
        result = call(action)
    elif action == "input":
        raw = sys.stdin.read(8193)
        if len(raw.encode()) > 8192:
            raise SystemExit("Input frame too large")
        result = call("in", gid=a.session, d=raw)
    elif action == "close":
        result = call("close", gid=a.session, **{k: getattr(a, k) for k in ("summary", "diagnosis", "verification") if getattr(a, k, None) is not None})
    elif action == "open":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise SystemExit("Open needs a real terminal; use agentj assist input for delegated commands.")
        import termios
        import tty
        import codecs
        gid = a.session
        size = os.get_terminal_size(sys.stdout.fileno())
        result = call("open", gid=gid, cols=max(20, min(300, size.columns)), rows=max(5, min(120, size.lines)))
        if not result.get("ok"):
            print(json.dumps(result, ensure_ascii=False))
            return
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            last_size = size
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            print("\r\n[Ctrl+] ends official assistance / Ctrl+] 结束远程协助]\r")
            while True:
                if select.select([fd], [], [], .08)[0]:
                    data = decoder.decode(os.read(fd, 8192))
                    if not data:
                        continue
                    if "\x1d" in data:
                        break
                    sent = call("in", gid=gid, d=data)
                    if not sent.get("ok"):
                        break
                polled = call("read", gid=gid)
                if not polled.get("ok"):
                    if polled.get("why") in ("ended", "no_authorized_session"):
                        sys.stdout.write("\r\n[Official assistance ended / 官方远程协助已结束]\r\n")
                        sys.stdout.flush()
                    break
                closed = False
                for frame in polled.get("events", []):
                    e = frame.get("event", {})
                    if e.get("t") == "term_out":
                        sys.stdout.write(e.get("d", ""))
                        sys.stdout.flush()
                    if e.get("t") == "term_close":
                        closed = True
                if closed:
                    break
                size = os.get_terminal_size(sys.stdout.fileno())
                if size != last_size:
                    call("resize", gid=gid, cols=max(20, min(300, size.columns)), rows=max(5, min(120, size.lines)))
                    last_size = size
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            with __import__("contextlib").suppress(Exception):
                call("close", gid=gid)
        return
    else:
        raise SystemExit("Unknown operator action")
    print(json.dumps(result, ensure_ascii=False))
