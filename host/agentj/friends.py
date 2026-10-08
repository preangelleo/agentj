"""Agent friends (PROTOCOL §17, ADR-0.16 §6): the host-side store — friends, requests, policy groups, the ledger, history.

Everything lives in `<state>/peer/` (dir 0700, files 0600; the fence hides the whole state dir from every Agent, the peer
sessions included). Plain JSON files, each read-modify-write under one flock (`peer.lock`), so `agentj friends …` (CLI) and
`serve` never interleave — the same pattern as State.config_lock.

- `friends.json`  {id: {id, x, pk, mbox, card, group, blocked, added_at, last, auto_rounds, unread}} — x / pk = the friend's
  X25519 / Ed25519 public keys (b64url), mbox = its mailbox (§17.1). A friend is never more than this.
- `blocked.json`  {id: {mbox, ts}} — every blocked ID, friend or not: a blocked requester's mbox is kept so the XX stage can drop
  its frames silently (§17.4).
- `pending.json`  {"in": {rid: {..., ask_id, exp}}, "out": {rid: {id, note, ts, exp, state}}} — requests, 7 days.
- `groups.json`   {gid: Group} — custom groups and the owner's edits of the built-ins (§17 Group; built-ins can change numbers,
  never their id, never disappear).
- `settings.json` on (default on: serve opens the mailbox once the seat certificate comes), discoverable (Q10: default on), owner (Q11: default ""), intro, global_tok_day, never_tell_extra.
- `ledger.json`   fixed-window counters per friend (minute / hour / calendar day / calendar month in the owner's time zone)
  and the account-wide daily token total. Counts only — never text.
- `sessions.json` {friend: {harness: session id}} — the peer session to resume (peer_session.py).
- `hist/<id>.jsonl` one line per message {mid, dir, text, ts, s, auto} — the only place a friend's words are kept.
- `profile.md`    「可以告诉好友的事」, written by the owner (or the main Agent for them).
- `friends/<id>/context.md` (P73, ADR-A176) the owner's 「补充设定」 for this one friend (≤ 4000 characters): extra context,
  persona, wording to keep — the last layer of that friend's peer-session prompt. Never leaves this computer.

Tokens come only from the harness's own usage report (record_turn); a turn without one marks the friend's tokens 「—」 (null)
and only the message limits apply to them — never an estimate (ADR §6.1 step 7).
"""
from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import fcntl
import json
import math
import os
import pathlib
import re
import time

from .state import _write_private

DAY = 86400
REQUEST_TTL = 7 * DAY
MAX_PENDING_IN = 50          # unanswered requests kept at once (the oldest beyond this are dropped)
MAX_GROUPS = 24              # built-ins + custom
MAX_NEVER_TELL_EXTRA = 50
MAX_PROFILE = 8000           # characters of profile.md
MAX_CONTEXT = 4000           # characters of one friend's context.md (P73 「补充设定」)
MAX_HIST_LINE = 64 * 1024
WINDOWS = ("min", "hour", "day", "month")
GROUP_ID = re.compile(r"[a-z0-9-]{1,32}")

# ADR §6.3 — the numbers are the contract; the web page translates names by id (BUILTIN_NAMES_EN is its English default).
BUILTIN_GROUPS = (
    {"id": "default", "name": "默认", "builtin": True,
     "limits": {"msg": {"min": 3, "hour": 20, "day": 50, "month": 500},
                "tok": {"min": 20000, "hour": 60000, "day": 150000, "month": 1500000},
                "min_interval_s": 10, "max_len": 2000},
     "auto": {"mode": "scoped", "allow": ["寒暄", "名片和公开资料里的信息"],
              "ask": ["报价、付款、合作条款", "约时间", "任何需要承诺的事"], "max_auto_rounds": 6}},
    {"id": "friend", "name": "好友", "builtin": True,
     "limits": {"msg": {"min": 10, "hour": 100, "day": 300, "month": 3000},
                "tok": {"min": 100000, "hour": 300000, "day": 600000, "month": 6000000},
                "min_interval_s": 3, "max_len": 8000},
     "auto": {"mode": "scoped", "allow": ["寒暄", "名片和公开资料里的信息", "日常协作、技术问题"],
              "ask": ["报价、付款、合作条款", "约时间", "任何需要承诺的事"], "max_auto_rounds": 12}},
    {"id": "colleague", "name": "同事", "builtin": True,
     "limits": {"msg": {"min": 30, "hour": 500, "day": 2000, "month": None},
                "tok": {"min": 500000, "hour": 2000000, "day": 4000000, "month": 40000000},
                "min_interval_s": 1, "max_len": 20000},
     "auto": {"mode": "all", "allow": [],
              "ask": ["报价、付款、合作条款", "约时间", "任何需要承诺的事"], "max_auto_rounds": 30}},
)
BUILTIN_IDS = tuple(g["id"] for g in BUILTIN_GROUPS)
BUILTIN_NAMES_EN = {"default": "Default", "friend": "Friends", "colleague": "Colleagues"}

# Fixed, not deletable (ADR §6.3). A rule for the model (peer_session's system prompt), not a keyword filter: matching these
# words in replies would only hit harmless sentences. The owner's additions (never_tell_extra) are matched as keywords too.
NEVER_TELL = ("凭据、API key、token", "密码", "私钥", "主人的个人信息（姓名、电话、地址、证件）", "其他好友的对话",
              "主人主会话的内容")
NEVER_TELL_EN = ("credentials, API keys, tokens", "passwords", "private keys",
                 "the owner's personal information (name, phone, address, ID documents)", "other friends' conversations",
                 "the owner's main conversation")

DEFAULT_SETTINGS = {"on": True, "discoverable": True, "owner": "", "intro": "", "global_tok_day": 300000,
                    "never_tell_extra": []}


def norm_id(ref: str) -> str:
    """Comparable form of an Agent ID (§17.1 parsing, without the check): no AJ prefix, dashes or spaces; upper case; I/L → 1,
    O → 0. Validation of the check character is peer.py's job; here only lookups."""
    s = re.sub(r"[\s-]", "", str(ref or "")).upper()
    if s.startswith("AJ"):
        s = s[2:]
    return s.translate(str.maketrans("ILO", "110"))


def _int(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


class FriendStore:
    def __init__(self, st, clock=time.time, tz=None):
        """st: state.State; clock: unix seconds (tests); tz: a tzinfo for the windows, None = this computer's local time."""
        self.st, self.clock, self.tz = st, clock, tz

    # ------------------------------------------------------------ files
    @property
    def dir(self) -> pathlib.Path:
        return pathlib.Path(self.st.root) / "peer"

    def _p(self, name: str) -> pathlib.Path:
        return self.dir / name

    def _ensure(self) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        (self.dir / "hist").mkdir(mode=0o700, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        """Exclusive across threads and processes (one flock per open file description)."""
        self._ensure()
        fd = os.open(self._p("peer.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self, name: str, default):
        try:
            d = json.loads(self._p(name).read_text())
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            return copy.deepcopy(default)
        return d if isinstance(d, type(default)) else copy.deepcopy(default)

    def _write(self, name: str, data) -> None:
        self._ensure()
        _write_private(self._p(name), json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())

    @contextlib.contextmanager
    def _edit(self, name: str, default):
        """Read-modify-write one file under the lock: `with self._edit(...) as d: d[...] = ...`."""
        with self.lock():
            d = self._read(name, default)
            yield d
            self._write(name, d)

    # ------------------------------------------------------------ settings
    def settings(self) -> dict:
        d = self._read("settings.json", {})
        out = dict(DEFAULT_SETTINGS)
        for k, v in d.items():
            if k in out and type(v) is type(DEFAULT_SETTINGS[k]):
                out[k] = v
        out["never_tell_extra"] = [x for x in out["never_tell_extra"] if isinstance(x, str)][:MAX_NEVER_TELL_EXTRA]
        return out

    def set_setting(self, key: str, value):
        """Validated write of one setting; returns a problem string or None."""
        if key not in DEFAULT_SETTINGS:
            return "unknown_setting"
        if key in ("on", "discoverable") and not isinstance(value, bool):
            return "bad_value"
        if key == "owner" and not (isinstance(value, str) and len(value) <= 32 and "\n" not in value):
            return "bad_owner"
        if key == "intro" and not (isinstance(value, str) and len(value) <= 140):
            return "bad_intro"
        if key == "global_tok_day" and not (isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10**10):
            return "bad_value"
        if key == "never_tell_extra":
            if not (isinstance(value, list) and len(value) <= MAX_NEVER_TELL_EXTRA
                    and all(isinstance(x, str) and 0 < len(x.strip()) <= 80 for x in value)):
                return "bad_never_tell"
            value = [x.strip() for x in value]
        with self._edit("settings.json", {}) as d:
            d[key] = value
        return None

    @property
    def on(self) -> bool: return self.settings()["on"]

    @property
    def discoverable(self) -> bool: return self.settings()["discoverable"]

    @property
    def owner(self) -> str: return self.settings()["owner"]

    @property
    def intro(self) -> str: return self.settings()["intro"]

    @property
    def global_tok_day(self) -> int: return self.settings()["global_tok_day"]

    @property
    def never_tell_extra(self) -> list: return self.settings()["never_tell_extra"]

    def never_tell(self) -> list[str]:
        """The fixed list + the owner's additions (what the peer session's system prompt shows)."""
        return list(NEVER_TELL) + self.never_tell_extra

    # ------------------------------------------------------------ friends
    def friends(self) -> dict:
        return self._read("friends.json", {})

    def get(self, friend_id: str) -> dict | None:
        f = self.friends().get(friend_id)
        return f if isinstance(f, dict) else None

    def name_of(self, friend_id: str) -> str:
        f = self.get(friend_id) or {}
        card = f.get("card") if isinstance(f.get("card"), dict) else {}
        n = card.get("name")
        return n if isinstance(n, str) and n else friend_id

    def add_friend(self, friend_id: str, x: str, pk: str, mbox: str, card: dict | None = None, group: str = "friend",
                   now: float | None = None) -> dict:
        """New friend, or the keys / card of an existing one refreshed (group, counters, history kept)."""
        now = self.clock() if now is None else now
        if group not in self._group_ids():
            group = "default"
        with self.lock():
            fr = self._read("friends.json", {})
            old = fr.get(friend_id) if isinstance(fr.get(friend_id), dict) else None
            rec = old or {"id": friend_id, "group": group, "blocked": False, "added_at": int(now), "last": int(now),
                          "auto_rounds": 0, "unread": 0}
            rec.update(id=friend_id, x=x, pk=pk, mbox=mbox, card=card if isinstance(card, dict) else rec.get("card") or {})
            if friend_id in self._read("blocked.json", {}):
                rec["blocked"] = True       # a blocked ID stays blocked until the owner unblocks it
            fr[friend_id] = rec
            self._write("friends.json", fr)
        return dict(rec)

    def set_card(self, friend_id: str, card: dict) -> bool:
        with self._edit("friends.json", {}) as fr:
            if friend_id not in fr:
                return False
            fr[friend_id]["card"] = card
        return True

    def remove(self, friend_id: str) -> bool:
        """Unfriend: relation, ledger, session and history go (the block list, if any, stays)."""
        with self.lock():
            fr = self._read("friends.json", {})
            had = fr.pop(friend_id, None) is not None
            self._write("friends.json", fr)
            led = self._read("ledger.json", {})
            if isinstance(led.get("f"), dict):
                led["f"].pop(friend_id, None)
                self._write("ledger.json", led)
            ss = self._read("sessions.json", {})
            if ss.pop(friend_id, None) is not None:
                self._write("sessions.json", ss)
        with contextlib.suppress(OSError):
            self._hist_path(friend_id).unlink()
        with contextlib.suppress(OSError):
            self._ctx_path(friend_id).unlink()
        with contextlib.suppress(OSError):
            self._ctx_path(friend_id).parent.rmdir()
        return had

    def block(self, friend_id: str, mbox: str | None = None, now: float | None = None) -> bool:
        """Block a friend or a requester: its mbox is remembered (XX frames from it are then dropped, §17.4); a pending
        request from it is discarded. False if neither a friend, a requester nor an mbox is known."""
        now = self.clock() if now is None else now
        with self.lock():
            fr = self._read("friends.json", {})
            pend = self._read("pending.json", {})
            pin = pend.get("in") if isinstance(pend.get("in"), dict) else {}
            if friend_id in fr:
                fr[friend_id]["blocked"] = True
                mbox = mbox or fr[friend_id].get("mbox")
                self._write("friends.json", fr)
            for rid in [r for r, v in pin.items() if isinstance(v, dict) and v.get("id") == friend_id]:
                mbox = mbox or pin[rid].get("mbox")
                del pin[rid]
            pend["in"] = pin
            self._write("pending.json", pend)
            if friend_id not in fr and not mbox:
                return False
            bl = self._read("blocked.json", {})
            bl[friend_id] = {"mbox": mbox or "", "ts": int(now)}
            self._write("blocked.json", bl)
        return True

    def unblock(self, friend_id: str) -> bool:
        with self.lock():
            bl = self._read("blocked.json", {})
            had = bl.pop(friend_id, None) is not None
            self._write("blocked.json", bl)
            fr = self._read("friends.json", {})
            if friend_id in fr:
                had = had or fr[friend_id].get("blocked") is True
                fr[friend_id]["blocked"] = False
                self._write("friends.json", fr)
        return had

    def is_blocked(self, friend_id: str) -> bool:
        if friend_id in self._read("blocked.json", {}):
            return True
        f = self.get(friend_id)
        return bool(f and f.get("blocked") is True)

    def is_blocked_mbox(self, mbox: str) -> bool:
        return bool(mbox) and any(isinstance(v, dict) and v.get("mbox") == mbox for v in self._read("blocked.json", {}).values())

    def set_group(self, friend_id: str, gid: str) -> str | None:
        if gid not in self._group_ids():
            return "no_group"
        with self._edit("friends.json", {}) as fr:
            if friend_id not in fr:
                return "no_friend"
            fr[friend_id]["group"] = gid
        return None

    def matches(self, ref: str) -> list[str]:
        """IDs matching `ref`: the ID itself (any spelling), else every friend whose card name starts with it (case-folded)."""
        fr = self.friends()
        want = norm_id(ref)
        exact = [k for k in fr if norm_id(k) == want] if want else []
        if exact:
            return exact
        r = str(ref or "").strip().casefold()
        if not r:
            return []
        return sorted(k for k, v in fr.items() if isinstance(v, dict) and isinstance(v.get("card"), dict)
                      and str(v["card"].get("name") or "").casefold().startswith(r))

    def find(self, ref: str) -> str | None:
        """An ID or a unique name prefix → the friend's ID; None when nothing or more than one friend matches."""
        m = self.matches(ref)
        return m[0] if len(m) == 1 else None

    def mark_read(self, friend_id: str) -> None:
        with self._edit("friends.json", {}) as fr:
            if friend_id in fr:
                fr[friend_id]["unread"] = 0

    def auto_round(self, friend_id: str, reset: bool = False) -> int:
        """Consecutive automatic replies to this friend without an owner action: +1 (or 0 with reset=True — any owner action:
        approving, editing, `friends tell`). Returns the new count."""
        with self._edit("friends.json", {}) as fr:
            if friend_id not in fr:
                return 0
            n = 0 if reset else _int(fr[friend_id].get("auto_rounds")) + 1
            fr[friend_id]["auto_rounds"] = n
        return n

    def auto_rounds(self, friend_id: str) -> int:
        return _int((self.get(friend_id) or {}).get("auto_rounds"))

    # ------------------------------------------------------------ requests
    def _pending(self, now: float) -> dict:
        d = self._read("pending.json", {})
        out = {}
        for side in ("in", "out"):
            v = d.get(side) if isinstance(d.get(side), dict) else {}
            out[side] = {k: r for k, r in v.items() if isinstance(r, dict) and _int(r.get("exp")) > now}
        return out

    def add_pending_in(self, rid: str, req: dict, now: float | None = None) -> bool:
        """An incoming request {id, x, pk, mbox, card, note, ts}. False (dropped silently) when blocked, already a friend or
        the same ID already waits; the oldest beyond MAX_PENDING_IN go."""
        now = self.clock() if now is None else now
        fid = req.get("id")
        if not isinstance(fid, str) or self.is_blocked(fid) or self.is_blocked_mbox(str(req.get("mbox") or "")):
            return False
        if fid in self.friends():
            return False
        with self.lock():
            p = self._pending(now)
            if rid in p["in"] or any(r.get("id") == fid for r in p["in"].values()):
                return False
            p["in"][rid] = {**{k: req[k] for k in ("id", "x", "pk", "mbox", "card", "note", "ts") if k in req},
                            "ask_id": None, "exp": int(now + REQUEST_TTL)}
            if len(p["in"]) > MAX_PENDING_IN:
                for k in sorted(p["in"], key=lambda k: p["in"][k]["exp"])[:len(p["in"]) - MAX_PENDING_IN]:
                    del p["in"][k]
            self._write("pending.json", p)
        return True

    def set_pending_ask(self, rid: str, ask_id: str) -> None:
        with self.lock():
            p = self._pending(self.clock())
            if rid in p["in"]:
                p["in"][rid]["ask_id"] = ask_id
                self._write("pending.json", p)

    def pending_in(self, now: float | None = None) -> dict:
        return self._pending(self.clock() if now is None else now)["in"]

    def take_pending_in(self, rid: str) -> dict | None:
        """Remove and return a request (accepted or refused — refusal sends nothing, §17.5)."""
        with self.lock():
            p = self._pending(self.clock())
            r = p["in"].pop(rid, None)
            self._write("pending.json", p)
        return r

    def add_pending_out(self, rid: str, friend_id: str, note: str = "", now: float | None = None) -> None:
        now = self.clock() if now is None else now
        with self.lock():
            p = self._pending(now)
            p["out"][rid] = {"id": friend_id, "note": note, "ts": int(now), "exp": int(now + REQUEST_TTL), "state": "pending"}
            self._write("pending.json", p)

    def set_pending_out_state(self, rid: str, state: str) -> None:
        with self.lock():
            p = self._pending(self.clock())
            if rid in p["out"]:
                p["out"][rid]["state"] = state
                self._write("pending.json", p)

    def pending_out(self, now: float | None = None) -> dict:
        return self._pending(self.clock() if now is None else now)["out"]

    def drop_pending_out(self, rid: str) -> dict | None:
        with self.lock():
            p = self._pending(self.clock())
            r = p["out"].pop(rid, None)
            self._write("pending.json", p)
        return r

    # ------------------------------------------------------------ groups
    def _group_ids(self) -> set:
        return {g["id"] for g in self.groups()}

    def groups(self) -> list:
        """Built-ins (with the owner's edits) first, then custom groups by id."""
        saved = self._read("groups.json", {})
        out = []
        for b in BUILTIN_GROUPS:
            g = saved.get(b["id"])
            g = copy.deepcopy(g) if isinstance(g, dict) and validate_group(g) is None else copy.deepcopy(b)
            g["builtin"] = True
            out.append(g)
        for gid in sorted(saved):
            g = saved[gid]
            if gid not in BUILTIN_IDS and isinstance(g, dict) and validate_group(g) is None and g.get("id") == gid:
                out.append(dict(copy.deepcopy(g), builtin=False))
        return out

    def group(self, gid: str) -> dict:
        """The group (falls back to `default` for an unknown id — a friend always has limits)."""
        gs = {g["id"]: g for g in self.groups()}
        return gs.get(gid) or gs["default"]

    def group_of(self, friend_id: str) -> dict:
        return self.group(str((self.get(friend_id) or {}).get("group") or "default"))

    def set_group_def(self, group) -> str | None:
        """Create or replace a group (§17 Group schema). Built-ins: numbers / name / auto may change, the id may not (it is the
        key), and `builtin` is forced. Returns a problem string or None."""
        err = validate_group(group, check_builtin=False)
        if err:
            return err
        g = copy.deepcopy(group)
        g["builtin"] = g["id"] in BUILTIN_IDS
        with self.lock():
            saved = self._read("groups.json", {})
            custom = [k for k in saved if k not in BUILTIN_IDS]
            if g["id"] not in BUILTIN_IDS and g["id"] not in saved and len(custom) + len(BUILTIN_IDS) >= MAX_GROUPS:
                return "too_many_groups"
            saved[g["id"]] = g
            self._write("groups.json", saved)
        return None

    def del_group(self, gid: str) -> str | None:
        """Delete a custom group; its friends move to `default`. Built-ins cannot be deleted."""
        if gid in BUILTIN_IDS:
            return "builtin"
        with self.lock():
            saved = self._read("groups.json", {})
            if gid not in saved:
                return "no_group"
            del saved[gid]
            self._write("groups.json", saved)
            fr = self._read("friends.json", {})
            for v in fr.values():
                if isinstance(v, dict) and v.get("group") == gid:
                    v["group"] = "default"
            self._write("friends.json", fr)
        return None

    # ------------------------------------------------------------ windows (fixed, owner's time zone)
    def _dt(self, now: float) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(now, self.tz) if self.tz else _dt.datetime.fromtimestamp(now)

    def _window(self, now: float, w: str) -> tuple[str, float]:
        """(key, end as unix seconds) of window w ∈ min / hour / day / month containing `now`."""
        d = self._dt(now)
        if w == "min":
            start = d.replace(second=0, microsecond=0)
            end = start + _dt.timedelta(minutes=1)
            key = start.strftime("%Y-%m-%dT%H:%M")
        elif w == "hour":
            start = d.replace(minute=0, second=0, microsecond=0)
            end = start + _dt.timedelta(hours=1)
            key = start.strftime("%Y-%m-%dT%H")
        elif w == "day":
            start = d.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + _dt.timedelta(days=1)      # wall-clock arithmetic: the next midnight, also across DST
            key = start.strftime("%Y-%m-%d")
        else:
            start = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (start + _dt.timedelta(days=32)).replace(day=1)
            key = start.strftime("%Y-%m")
        end_ts = end.timestamp()             # aware: its zone's offset at that wall time; naive: local time (mktime)
        if end_ts <= now:                    # a DST jump inside the window: never a non-positive retry
            end_ts = now + 1
        return key, end_ts

    def _count(self, slot, key: str):
        """Value of a [key, value] slot for the current window (another key = an old window = 0)."""
        return slot[1] if isinstance(slot, list) and len(slot) == 2 and slot[0] == key else 0

    @staticmethod
    def _fledger(led: dict, friend_id: str) -> dict:
        f = led.setdefault("f", {})
        if not isinstance(f, dict):
            led["f"] = f = {}
        rec = f.get(friend_id)
        if not isinstance(rec, dict):
            rec = f[friend_id] = {}
        for k in ("msg", "tok"):
            if not isinstance(rec.get(k), dict):
                rec[k] = {}
        if not isinstance(rec.get("limited"), dict):
            rec["limited"] = {}
        return rec

    def precheck(self, friend_id: str, text: str, now: float | None = None):
        """§17.6 step 2, counts only (no model): None = go on, else (scope, retry seconds). Order: length → interval →
        message windows → token windows → account-wide daily tokens. Does not count the message (record_in does)."""
        now = self.clock() if now is None else now
        g = self.group_of(friend_id)
        lim = g["limits"]
        if len(text or "") > lim["max_len"]:
            return "length", 0
        led = self._read("ledger.json", {})
        rec = (led.get("f") or {}).get(friend_id) if isinstance(led.get("f"), dict) else None
        rec = rec if isinstance(rec, dict) else {}
        last = rec.get("last_in")
        if isinstance(last, (int, float)) and lim["min_interval_s"] and now - last < lim["min_interval_s"]:
            return "interval", max(1, math.ceil(lim["min_interval_s"] - (now - last)))
        for kind in ("msg", "tok"):
            if kind == "tok" and rec.get("tok_source", "harness") is None:
                continue            # this friend's harness reports no usage: message limits only (「—」)
            slots = rec.get(kind) if isinstance(rec.get(kind), dict) else {}
            for w in WINDOWS:
                cap = lim[kind][w]
                if cap is None:
                    continue
                key, end = self._window(now, w)
                if self._count(slots.get(w), key) >= cap:
                    return f"{kind}_{w}", max(1, math.ceil(end - now))
        key, end = self._window(now, "day")
        if self._count(led.get("global"), key) >= self.global_tok_day:
            return "global", max(1, math.ceil(end - now))
        return None

    def _limited_key(self, scope: str, now: float) -> str:
        w = scope.split("_", 1)[1] if scope.startswith(("msg_", "tok_")) else "day" if scope == "global" else "min"
        return f"{scope}:{self._window(now, w if w in WINDOWS else 'min')[0]}"

    def should_send_limited(self, friend_id: str, scope: str, now: float | None = None) -> bool:
        """True once per window per friend per scope (the first call in that window records it)."""
        now = self.clock() if now is None else now
        key = self._limited_key(scope, now)
        with self._edit("ledger.json", {}) as led:
            rec = self._fledger(led, friend_id)
            if rec["limited"].get(scope) == key:
                return False
            rec["limited"][scope] = key
        return True

    def record_in(self, friend_id: str, now: float | None = None) -> None:
        """A message that passed the pre-check: +1 in every message window; the interval clock restarts."""
        now = self.clock() if now is None else now
        with self._edit("ledger.json", {}) as led:
            rec = self._fledger(led, friend_id)
            for w in WINDOWS:
                key, _ = self._window(now, w)
                rec["msg"][w] = [key, self._count(rec["msg"].get(w), key) + 1]
            rec["last_in"] = now
        with self._edit("friends.json", {}) as fr:
            if friend_id in fr:
                fr[friend_id]["last"] = int(now)

    def record_turn(self, friend_id: str, tokens: int | None, now: float | None = None) -> None:
        """One peer-session turn. tokens = the harness's own usage report; None = it gave none → this friend's tokens read
        「—」 (null) and only message limits apply until a turn reports usage again."""
        now = self.clock() if now is None else now
        with self._edit("ledger.json", {}) as led:
            rec = self._fledger(led, friend_id)
            if tokens is None or not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                rec["tok_source"] = None
                return
            rec["tok_source"] = "harness"
            for w in WINDOWS:
                key, _ = self._window(now, w)
                rec["tok"][w] = [key, self._count(rec["tok"].get(w), key) + tokens]
            key, _ = self._window(now, "day")
            led["global"] = [key, self._count(led.get("global"), key) + tokens]

    def note_blocked(self, friend_id: str) -> None:
        """A message stopped by the pre-check (shown as 「被拦 N 条」)."""
        with self._edit("ledger.json", {}) as led:
            rec = self._fledger(led, friend_id)
            rec["blocked"] = _int(rec.get("blocked")) + 1

    def usage(self, friend_id: str, now: float | None = None) -> dict:
        """§17.7 fr_usage_res without `t` / `r`: {friend, group, used: {msg: {min,hour,day,month}, tok: {…}|nulls}, limits,
        tok_source: "harness"|null, blocked}."""
        now = self.clock() if now is None else now
        g = self.group_of(friend_id)
        led = self._read("ledger.json", {})
        rec = (led.get("f") or {}).get(friend_id) if isinstance(led.get("f"), dict) else None
        rec = rec if isinstance(rec, dict) else {}
        src = rec.get("tok_source", "harness")   # no turn yet: 0 tokens, counted from the harness once there is one
        used = {}
        for kind in ("msg", "tok"):
            slots = rec.get(kind) if isinstance(rec.get(kind), dict) else {}
            used[kind] = {w: (None if kind == "tok" and src is None else self._count(slots.get(w), self._window(now, w)[0]))
                          for w in WINDOWS}
        return {"friend": friend_id, "group": g["id"], "used": used, "limits": copy.deepcopy(g["limits"]),
                "tok_source": src, "blocked": _int(rec.get("blocked"))}

    def global_usage(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        led = self._read("ledger.json", {})
        return {"used": self._count(led.get("global"), self._window(now, "day")[0]), "limit": self.global_tok_day}

    # ------------------------------------------------------------ peer sessions (peer_session.py)
    def session_id(self, friend_id: str, harness: str) -> str | None:
        v = (self._read("sessions.json", {}).get(friend_id) or {})
        sid = v.get(harness) if isinstance(v, dict) else None
        return sid if isinstance(sid, str) and 0 < len(sid) <= 128 and all(c.isalnum() or c in "-_" for c in sid) else None

    def set_session_id(self, friend_id: str, harness: str, sid: str | None) -> None:
        with self._edit("sessions.json", {}) as ss:
            v = ss.get(friend_id) if isinstance(ss.get(friend_id), dict) else {}
            if sid:
                v[harness] = sid
            else:
                v.pop(harness, None)
            ss[friend_id] = v

    # ------------------------------------------------------------ history
    def _hist_path(self, friend_id: str) -> pathlib.Path:
        safe = re.sub(r"[^A-Za-z0-9-]", "_", friend_id)[:64]
        return self.dir / "hist" / f"{safe}.jsonl"

    def hist_add(self, friend_id: str, item: dict) -> None:
        """Append {mid, dir: in|out, text, ts (ms), s, auto}. An incoming one counts as unread."""
        rec = {"mid": str(item.get("mid") or ""), "dir": "in" if item.get("dir") == "in" else "out",
               "text": str(item.get("text") or ""), "ts": _int(item.get("ts")) or int(self.clock() * 1000),
               "s": str(item.get("s") or ""), "auto": bool(item.get("auto"))}
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        with self.lock():
            fd = os.open(self._hist_path(friend_id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode())
            finally:
                os.close(fd)
            if rec["dir"] == "in":
                fr = self._read("friends.json", {})
                if friend_id in fr:
                    fr[friend_id]["unread"] = _int(fr[friend_id].get("unread")) + 1
                    self._write("friends.json", fr)

    def _hist_all(self, friend_id: str) -> list:
        try:
            lines = self._hist_path(friend_id).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            if len(ln) > MAX_HIST_LINE:
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
        return out

    def hist_set_status(self, friend_id: str, mid: str, s: str) -> bool:
        """Update the receipt state `s` of a message (e.g. an outgoing one acknowledged `replied`)."""
        with self.lock():
            items = self._hist_all(friend_id)
            hit = False
            for d in items:
                if d.get("mid") == mid:
                    d["s"], hit = s, True
            if hit:
                body = "".join(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n" for d in items)
                _write_private(self._hist_path(friend_id), body.encode())
        return hit

    def hist_page(self, friend_id: str, before=None, n: int = 50) -> tuple[list, bool]:
        """Newest first, at most n (≤ 50), only items with ts < before (None = from the newest). → (items, more)."""
        n = max(1, min(int(n), 50))
        items = self._hist_all(friend_id)
        if isinstance(before, (int, float)) and not isinstance(before, bool):
            items = [d for d in items if _int(d.get("ts")) < before]
        items.sort(key=lambda d: _int(d.get("ts")), reverse=True)
        return items[:n], len(items) > n

    # ------------------------------------------------------------ profile
    def profile_text(self) -> str:
        try:
            return self._p("profile.md").read_text(encoding="utf-8", errors="replace")[:MAX_PROFILE]
        except OSError:
            return ""

    def set_profile(self, text: str) -> str | None:
        if not isinstance(text, str) or len(text) > MAX_PROFILE:
            return "too_long"
        with self.lock():
            _write_private(self._p("profile.md"), text.encode())
        return None

    # ------------------------------------------------------------ one friend's 「补充设定」 (P73, ADR-A176)
    def _ctx_path(self, friend_id: str) -> pathlib.Path:
        safe = re.sub(r"[^A-Za-z0-9-]", "_", friend_id)[:64] or "_"
        return self.dir / "friends" / safe / "context.md"

    def context_text(self, friend_id: str) -> str:
        """The owner's extra context for this friend ("" when none). Read on every peer-session turn."""
        try:
            return self._ctx_path(friend_id).read_text(encoding="utf-8", errors="replace")[:MAX_CONTEXT]
        except OSError:
            return ""

    def set_context(self, friend_id: str, text: str, append: bool = False) -> str | None:
        """Replace (or append to) a friend's context.md. "" clears it. → None, "not_friend", "too_long" or "bad_text".
        Only for a friend (a stranger has no peer session to shape)."""
        if not isinstance(text, str) or "\x00" in text:
            return "bad_text"
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        with self.lock():
            if friend_id not in self._read("friends.json", {}):
                return "not_friend"
            path = self._ctx_path(friend_id)
            if append:
                try:
                    old = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    old = ""
                text = (old.rstrip("\n") + "\n" + text) if old.strip() and text.strip() else (text if text.strip() else old)
            if len(text) > MAX_CONTEXT:
                return "too_long"
            if not text.strip():
                with contextlib.suppress(OSError):
                    path.unlink()
                return None
            path.parent.parent.mkdir(mode=0o700, exist_ok=True)
            path.parent.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path.parent.parent, 0o700)
            os.chmod(path.parent, 0o700)
            _write_private(path, text.encode())
        return None

    # ------------------------------------------------------------ the phone's list (§17.7 fr_list_res; `me` by the caller)
    def list_view(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        groups = self.groups()
        maxr = {g["id"]: g["auto"]["max_auto_rounds"] for g in groups}
        friends = []
        for fid, v in sorted(self.friends().items(), key=lambda kv: -_int((kv[1] or {}).get("last"))):
            if not isinstance(v, dict):
                continue
            card = v.get("card") if isinstance(v.get("card"), dict) else {}
            gid = v.get("group") if v.get("group") in maxr else "default"
            paused = _int(v.get("auto_rounds")) >= maxr.get(gid, 6)
            friends.append({"id": fid, "name": str(card.get("name") or ""), "owner": str(card.get("owner") or ""),
                            "intro": str(card.get("intro") or ""), "group": gid,
                            "state": "paused" if paused else "friend", "blocked": bool(v.get("blocked")),
                            "last": _int(v.get("last")), "unread": _int(v.get("unread"))})
        p = self._pending(now)
        pending = [{"id": r.get("id"), "state": "pending", "ts": _int(r.get("ts")), "dir": "in"} for r in p["in"].values()]
        pending += [{"id": r.get("id"), "state": str(r.get("state") or "pending"), "ts": _int(r.get("ts")), "dir": "out"}
                    for r in p["out"].values()]
        pending.sort(key=lambda r: -r["ts"])
        return {"friends": friends, "pending": pending, "groups": groups, "global": self.global_usage(now)}


def validate_group(g, check_builtin: bool = True) -> str | None:
    """§17 Group schema → None, or a short problem string."""
    if not isinstance(g, dict):
        return "not_object"
    if set(g) - {"id", "name", "builtin", "limits", "auto"} or not {"id", "name", "limits", "auto"} <= set(g):
        return "bad_keys"
    if not isinstance(g["id"], str) or not GROUP_ID.fullmatch(g["id"]):
        return "bad_id"
    if not isinstance(g["name"], str) or not 0 < len(g["name"].strip()) <= 32 or "\n" in g["name"]:
        return "bad_name"
    if "builtin" in g and not isinstance(g["builtin"], bool):
        return "bad_builtin"
    if check_builtin and g.get("builtin") is True and g["id"] not in BUILTIN_IDS:
        return "bad_builtin"
    lim = g["limits"]
    if not isinstance(lim, dict) or set(lim) != {"msg", "tok", "min_interval_s", "max_len"}:
        return "bad_limits"
    for kind in ("msg", "tok"):
        w = lim[kind]
        if not isinstance(w, dict) or set(w) != set(WINDOWS):
            return "bad_limits"
        for v in w.values():
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10**10):
                return "bad_limits"
    iv = lim["min_interval_s"]
    if not (isinstance(iv, int) and not isinstance(iv, bool) and 0 <= iv <= 86400):
        return "bad_interval"
    ml = lim["max_len"]
    if not (isinstance(ml, int) and not isinstance(ml, bool) and 1 <= ml <= 20000):
        return "bad_max_len"
    a = g["auto"]
    if not isinstance(a, dict) or set(a) != {"mode", "allow", "ask", "max_auto_rounds"}:
        return "bad_auto"
    if a["mode"] not in ("off", "scoped", "all"):
        return "bad_mode"
    for k in ("allow", "ask"):
        v = a[k]
        if not (isinstance(v, list) and len(v) <= 20 and all(isinstance(x, str) and 0 < len(x.strip()) <= 80 for x in v)):
            return "bad_topics"
    r = a["max_auto_rounds"]
    if not (isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= 100):
        return "bad_rounds"
    return None
