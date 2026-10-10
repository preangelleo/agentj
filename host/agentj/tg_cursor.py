"""B3 (P117): the Telegram cursor across restarts, re-enrollment and a Relay ⇄ Agent J switch. Ids and numbers only, 0600:
telegram-offset.json (next update_id, its bot, the update being handed over), telegram-outbox.json (turns owing a reply),
telegram-fence.json (drain), telegram-status.json (what serve last saw), telegram-migration.json (import/export events).
At-most-once like Relay; offsets only move forward. See documentation/ARCHITECTURE.md ADR-P117."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import pathlib
import time

OFFSET = "telegram-offset.json"
OUTBOX = "telegram-outbox.json"
FENCE = "telegram-fence.json"
STATUS = "telegram-status.json"
MIGRATION = "telegram-migration.json"
LOCK = "telegram-cursor.lock"
STALE = 60          # seconds: older status = serve is not consuming (stopped)
OUTBOX_MAX = 256
UNCERTAIN_MAX = 50
SKEW = 5            # seconds of clock slack for the fresh-cursor date fence


def _int(v):
    return v if type(v) is int else None


def _read(path: pathlib.Path):
    try:
        if path.is_symlink():
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: pathlib.Path, obj) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, json.dumps(obj, ensure_ascii=False).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(root: pathlib.Path):
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ------------------------------------------------------------------ the cursor
def load(root: pathlib.Path) -> dict:
    """v1 {offset, enrollment} and v2 both read; anything malformed → a fresh cursor."""
    d = _read(root / OFFSET)
    d = d if isinstance(d, dict) else {}
    off = _int(d.get("offset"))
    return {"v": 2, "offset": off if off and off > 0 else 0,
            "bot": _int(d.get("bot")),
            "enrollment": d.get("enrollment") if isinstance(d.get("enrollment"), dict) else None,
            "inflight": _int(d.get("inflight")),
            "pending_updates": list(dict.fromkeys(x for x in d.get("pending_updates", []) if type(x) is int))[:100]
            if isinstance(d.get("pending_updates"), list) else [],
            "imported": bool(d.get("imported")),
            "uncertain": [x for x in d.get("uncertain", []) if type(x) is int][-UNCERTAIN_MAX:]
            if isinstance(d.get("uncertain"), list) else []}


def save(root: pathlib.Path, cur: dict, reset: bool = False) -> dict:
    """Write the cursor; under the lock the offset never goes below what is on disk for the same bot (a CLI import while
    serve held an older number in memory). reset=True only when the bot itself changed. Returns what was written; the
    caller's dict gets the merged offset too."""
    keep = {k: cur.get(k) for k in ("v", "offset", "bot", "enrollment", "inflight", "pending_updates", "imported", "uncertain")}
    keep["v"] = 2
    enr = keep.get("enrollment")
    if isinstance(enr, dict):        # the enrollment record holds a variable NAME and ids only — never a value
        keep["enrollment"] = {k: enr[k] for k in ("owner_id", "key_env", "generation", "at") if k in enr}
    with locked(root):
        disk = load(root)
        if not reset and (disk["bot"] is None or keep.get("bot") is None or disk["bot"] == keep.get("bot")) \
                and disk["offset"] > (keep.get("offset") or 0):
            keep["offset"] = disk["offset"]
            keep["imported"] = bool(keep.get("imported") or disk["imported"])
            if keep.get("bot") is None:
                keep["bot"] = disk["bot"]
        _write(root / OFFSET, keep)
    cur.update(offset=keep["offset"], imported=keep["imported"], bot=keep["bot"])
    return keep


def adopt(cur: dict, cfg: dict, bot_id) -> tuple[int, str]:
    """B3: which offset a (re-)enrollment continues from. The cursor belongs to the bot, not to the enrollment generation:
    the same bot keeps its offset (a new enrollment must not restart at 0 and replay). Another bot starts fresh."""
    old = cur.get("enrollment")
    if cur.get("bot") is not None:
        if cur["bot"] == bot_id:
            return cur["offset"], "same_bot"
        return 0, "other_bot"
    # a v1 cursor (or an import without --bot-id) does not know its bot: same credential NAME → the same bot
    if old is None and cur["offset"]:
        return cur["offset"], "unbound"
    if old is not None and old == {k: cfg.get(k) for k in old}:
        return cur["offset"], "same_enrollment"
    if old is not None and old.get("key_env") == cfg.get("key_env") and old.get("owner_id") == cfg.get("owner_id"):
        return cur["offset"], "same_key_name"
    return 0, "unknown_bot"


# ------------------------------------------------------------------ the outbox (ids only)
def outbox(root: pathlib.Path) -> list[dict]:
    d = _read(root / OUTBOX)
    rows = d.get("items") if isinstance(d, dict) and isinstance(d.get("items"), list) else []
    return [r for r in rows if isinstance(r, dict) and type(r.get("turn")) is int
            and r.get("state") in ("awaiting", "queued", "sending")]


def _save_outbox(root: pathlib.Path, rows: list[dict]) -> bool:
    """Best effort (crash recovery of owed replies): a full disk never breaks a turn's end or the poll loop."""
    try:
        with locked(root):
            _write(root / OUTBOX, {"items": rows[-OUTBOX_MAX:]})
        return True
    except OSError:
        return False


def outbox_put(root: pathlib.Path, turn: int, chat: int, uid: int, device: str, generation, state: str = "awaiting") -> None:
    rows = [r for r in outbox(root) if r["turn"] != turn]
    rows.append({"turn": turn, "chat": chat, "uid": uid, "dev": device, "gen": generation, "state": state,
                 "at": int(time.time())})
    _save_outbox(root, rows)


def outbox_set(root: pathlib.Path, turn: int, state: str | None) -> None:
    rows = outbox(root)
    if state is None:
        rows = [r for r in rows if r["turn"] != turn]
    else:
        for r in rows:
            if r["turn"] == turn:
                r["state"] = state
    _save_outbox(root, rows)


# ------------------------------------------------------------------ fence + status
def fence(root: pathlib.Path) -> dict | None:
    d = _read(root / FENCE)
    return d if isinstance(d, dict) and d.get("fence") == "drain" else None


def set_fence(root: pathlib.Path, on: bool) -> float:
    at = time.time()
    with locked(root):
        if on:
            _write(root / FENCE, {"fence": "drain", "at": at})
        else:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(root / FENCE)
    return at


def write_status(root: pathlib.Path, **kw) -> None:
    _write(root / STATUS, {**kw, "at": time.time()})


def status(root: pathlib.Path) -> dict:
    d = _read(root / STATUS)
    d = d if isinstance(d, dict) else {}
    fresh = isinstance(d.get("at"), (int, float)) and time.time() - d["at"] <= STALE
    return {**d, "fresh": fresh}


POLLER = "telegram-poller.lock"


def hold_poller(root: pathlib.Path):
    """serve's Telegram loop holds this flock for its lifetime → the CLI knows a poller exists (not a heartbeat guess)."""
    fd = os.open(root / POLLER, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def poller_running(root: pathlib.Path) -> bool:
    fd = hold_poller(root)
    if fd is None:
        return True
    os.close(fd)
    return False


def quiet(root: pathlib.Path, since: float | None = None, need_fence: bool = False) -> tuple[bool, str]:
    """Is it safe for the CLI to move the cursor? No poller at all, or serve acknowledged (after `since`) that it stopped
    consuming. need_fence: only the drain fence counts (a disabled channel can be re-enabled at any moment)."""
    if not poller_running(root):
        return True, "serve_not_running"
    s = status(root)
    if not s["fresh"]:
        return False, "status_stale"
    if s.get("consuming"):
        return False, "consuming"
    if since is not None and s.get("at", 0) < since:
        return False, "not_acknowledged"
    if need_fence and not (s.get("fenced") and fence(root)):
        return False, "not_fenced"
    return True, "fenced" if s.get("fenced") else "channel_off"


def drained(root: pathlib.Path) -> bool:
    if not poller_running(root):
        return not outbox(root) and not load(root)["pending_updates"] and load(root)["inflight"] is None
    s = status(root)
    return bool(s["fresh"] and not s.get("consuming") and not s.get("pending") and not s.get("out") and not s.get("inflight"))


def record_migration(root: pathlib.Path, **event) -> None:
    d = _read(root / MIGRATION)
    rows = d.get("events") if isinstance(d, dict) and isinstance(d.get("events"), list) else []
    rows.append({**event, "at": int(time.time())})
    with locked(root):
        _write(root / MIGRATION, {"events": rows[-100:]})


# ------------------------------------------------------------------ Relay's cursor file (one integer)
def read_relay(path: pathlib.Path) -> int:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Relay offset file missing or a symlink")
    raw = path.read_text().strip()
    if not raw.isdigit():
        raise ValueError("Relay offset file is not an integer")
    return int(raw)


def write_relay(path: pathlib.Path, value: int) -> None:
    parent = path.parent
    if path.is_symlink() or parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != os.getuid():
        raise ValueError("Relay offset directory must be a real directory owned by this user")
    tmp = parent / (path.name + f".agentj-{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(str(value))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def import_relay(root: pathlib.Path, relay: pathlib.Path, bot_id: int | None = None) -> dict:
    """Relay → Agent J: continue from Relay's next update_id (the larger cursor wins; never lower)."""
    ok, why = quiet(root)
    if not ok:
        return {"ok": False, "error": why}
    value = read_relay(relay)
    cur = load(root)
    previous = cur["offset"]
    new = max(previous, value)
    cur.update(offset=new, imported=True)
    if bot_id is not None:
        cur["bot"] = bot_id
    save(root, cur)
    record_migration(root, dir="relay->agentj", relay_offset=value, previous=previous, offset=new, bot_bound=bot_id is not None)
    return {"ok": True, "offset": new, "relay_offset": value}


def export_relay(root: pathlib.Path, relay: pathlib.Path, force: bool = False) -> dict:
    """Agent J → Relay (rollback): Relay resumes after the last update Agent J consumed. Only on a drained channel whose
    poller is gone or has acknowledged the drain fence."""
    f = fence(root)
    ok, why = quiet(root, f["at"] if f else None, need_fence=True)
    if not ok:
        return {"ok": False, "error": why}
    owed = [r["turn"] for r in outbox(root)]
    if not drained(root) and not (force and not poller_running(root)):
        return {"ok": False, "error": "not_drained", "owed": len(owed)}
    cur = load(root)
    try:
        old = read_relay(relay)
    except ValueError:
        if relay.exists() or relay.is_symlink():
            raise
        old = 0
    new = max(old, cur["offset"])
    if new != old:
        write_relay(relay, new)
    record_migration(root, dir="agentj->relay", relay_previous=old, offset=new, inflight=cur["inflight"],
                     uncertain=len(cur["uncertain"]), pending_updates=cur["pending_updates"], owed_turns=owed if force else [])
    return {"ok": True, "offset": new, "relay_previous": old, "uncertain": cur["uncertain"], "pending_updates": cur["pending_updates"], "owed_turns": owed if force else []}


# ------------------------------------------------------------------ CLI: agentj telegram-cursor …
def _state_root():
    from .state import State
    return State().root


def cmd(a) -> int:
    root = _state_root()
    if a.tc == "status":
        cur, st = load(root), status(root)
        print(json.dumps({"offset": cur["offset"], "bot_bound": cur["bot"] is not None, "imported": cur["imported"],
                          "inflight": cur["inflight"], "pending_updates": cur["pending_updates"], "uncertain": cur["uncertain"], "fence": bool(fence(root)),
                          "serve": {k: st.get(k) for k in ("fresh", "consuming", "fenced", "pending", "out", "inflight")},
                          "outbox": [{k: r[k] for k in ("turn", "state")} for r in outbox(root)],
                          "drained": drained(root)}, ensure_ascii=False))
        return 0
    if a.tc == "fence":
        at = set_fence(root, a.state == "drain")
        if a.state == "off":
            print(json.dumps({"ok": True, "fence": False}))
            return 0
        deadline = time.time() + max(0, a.wait)
        while True:
            ok, why = quiet(root, at)
            if ok and (not a.wait or drained(root)):
                print(json.dumps({"ok": True, "fence": True, "quiet": why, "drained": drained(root)}))
                return 0
            if time.time() >= deadline:
                print(json.dumps({"ok": not a.wait, "fence": True, "quiet": why if ok else False, "drained": drained(root)}))
                return 0 if not a.wait else 1
            time.sleep(0.5)
    try:
        if a.tc == "import":
            res = import_relay(root, pathlib.Path(a.relay_offset).expanduser(), a.bot_id)
        else:
            res = export_relay(root, pathlib.Path(a.relay_offset).expanduser(), getattr(a, "force", False))
    except ValueError as e:
        res = {"ok": False, "error": str(e)}
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def add_parser(sub) -> None:
    p = sub.add_parser("telegram-cursor", help="Telegram 接管 / 回滚的 cursor：status · fence drain|off · import · export（只有编号，不存消息）"
                                               " / Telegram cursor for a Relay⇄Agent J switch")
    s = p.add_subparsers(dest="tc", required=True)
    s.add_parser("status")
    f = s.add_parser("fence")
    f.add_argument("state", choices=("drain", "off"))
    f.add_argument("--wait", type=int, default=0, help="seconds to wait until every owed reply is sent")
    i = s.add_parser("import", help="Relay → Agent J：从 Relay 的 offset 文件接续（取较大值）")
    i.add_argument("--relay-offset", required=True)
    i.add_argument("--bot-id", type=int, default=None)
    e = s.add_parser("export", help="Agent J → Relay（回滚）：把已消费的位置写回 Relay（只前进不后退）")
    e.add_argument("--relay-offset", required=True)
    e.add_argument("--force", action="store_true", help="Agent J stopped with replies still owed: export anyway, list those turns as uncertain")
    p.set_defaults(fn=cmd)
