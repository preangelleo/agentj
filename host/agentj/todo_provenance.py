"""Audit 2026-11-22 N-S1: was this to-do written in a clean turn? — a record the host keeps for itself.

The hole: trust_taint's "this turn has read external content" mark ends when the next host-built turn starts. An owner
turn that had read a poisoned page could write a to-do (a local write: no card); the next idle heartbeat — trust `host`,
a new turn — was told to pick it up and its outbound sends ran card-free.

The record is NOT a field of the item: the Agent writes the to-do file, so anything in it can be forged. The host keeps
`{item id: digest of the item's words}` for the items it saw being written in a clean turn, in its own state
(`todo-provenance.json` in the state directory — private host state, which the off-list rules refuse to the Agent). It is
taken at the host's own turn boundaries, by comparing the to-do file at the start of a turn (trust_taint.turn_start)
with the file at the end of that same turn (Host.agent_turn_end). Idle time between turns is credited to nobody. No command is parsed and no command word is listed.

An item is recorded clean only when ALL of this is known:
  - the turn that ended was the owner's own (phone / terminal) or a heartbeat that itself started clean;
  - that turn never took the external-read mark;
  - no background work was on the busy board at any time in it (a job's reads are not observed by the mark);
  - the item is new in that turn, or was clean before it and was edited in it.
An item that is not clean never becomes clean by an edit (reading it is reading what an unclean turn wrote). Everything
else — an item older than the record, a lost or unreadable record, an unreadable to-do file, a first turn after a
restart — is unknown, and unknown is not clean.

Use: a heartbeat turn starts with the mark (kind `todo`) unless EVERY item in the active to-do file is clean — any
owner, started or not, open or closed (security r1 H-1 / r2 HR-1: the heartbeat reads the whole list). The mark does what it always does and nothing more:
an outbound send the unchanged off-list rules would allow takes the existing Face ID card. It adds cards only.
"""
from __future__ import annotations

import hashlib
import json

FILE = "todo-provenance.json"
_ATTR = "_agentj_todo_provenance"
KEEP = 512
# Everything of an item a later turn reads and acts on. `status` too (security r1 H-2): a marked turn that flips a clean,
# finished item back to pending would otherwise replay the owner's old work card-free.
_WORDS = ("id", "text", "owner", "status", "start", "end", "source", "note")


def digest(item: dict) -> str:
    body = {k: item.get(k) for k in _WORDS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read(root):
    """→ (items, {id: digest | None}) or (None, None) when the to-do file cannot be read (unknown, not empty).
    An id stored twice has no digest: it can never match a record."""
    from . import jcards_todo as todo
    try:
        items, problem, _ = todo.read(root)
    except Exception:  # noqa: BLE001 — unknown
        return None, None
    if problem not in (None, "empty", "not_found"):
        return None, None
    marks = {}
    for it in items:
        marks[it["id"]] = None if it["id"] in marks else digest(it)
    return items, marks


def _open(items) -> set:
    """Ids a heartbeat may read: EVERY item in the active to-do file (security r1 H-1, r2 HR-1) — whoever owns it,
    started or not, open or closed: the heartbeat's review runs `agentj card todo list --json`, which prints finished
    and cancelled items too. Only an untouched factory sample item is not counted (it is the shipped text)."""
    from pathlib import Path
    from . import jcards_todo as todo
    try:
        raw = Path(todo.__file__).with_name('cardpacks').joinpath('todo', 'sample', 'todo.json').read_bytes()
        seed = {x['id']: x for x in todo.load(raw)[0]}
    except Exception:  # noqa: BLE001 — cannot tell a sample from an item: count it
        seed = {}
    return {x["id"] for x in items if x != seed.get(x["id"])}


def _state(host) -> dict:
    st = host.__dict__.get(_ATTR)
    if st is None:
        clean = {}
        try:
            doc = json.loads((host.st.root / FILE).read_text(encoding="utf-8"))
            rows = doc.get("clean") if isinstance(doc, dict) and doc.get("v") == 1 else None
            if isinstance(rows, dict):
                clean = {k: v for k, v in list(rows.items())[-KEEP:]
                         if isinstance(k, str) and isinstance(v, str) and len(v) == 64}
        except Exception:  # noqa: BLE001 — a missing / unreadable record: nothing is known to be clean
            clean = {}
        st = host.__dict__[_ATTR] = {"clean": clean, "window": None}
    return st


def _save(host, st) -> None:
    from .state import _write_private
    try:
        _write_private(host.st.root / FILE, json.dumps({"v": 1, "clean": st["clean"]}).encode("utf-8"))
    except Exception:  # noqa: BLE001 — not persisted: after a restart these items are unknown (the card), never the reverse
        pass


def _background(host):
    """(how many background items the board has ever started, is one there now). None: no board (nothing to observe)."""
    board = getattr(host, "busy", None)
    if board is None:
        return None
    lock = board._unlocked() if hasattr(board, "_unlocked") else None
    if lock is None:
        return (getattr(board, "started_n", 0), bool(getattr(board, "mem", None) or getattr(board, "outside", None)))
    with lock:
        return (getattr(board, "started_n", 0), bool(getattr(board, "mem", None) or getattr(board, "outside", None)))


GRANDFATHERED = "todo-provenance-grandfathered.json"


def _upgraded_to(st, version) -> bool:
    """Is this the first start after the updater moved this state from ANOTHER version to `version`? Read (never taken)
    from the updater's own receipts in the state directory: update.UPGRADED (`upgraded.json`, written right before the
    restart: {from, to, at}) or upgrade_diag.LAST (`last-upgrade.json`, result ok)."""
    from . import update, upgrade_diag
    for name, need_ok in ((update.UPGRADED, False), (upgrade_diag.LAST, True)):
        try:
            d = json.loads((st.root / name).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — no such receipt
            continue
        if (isinstance(d, dict) and d.get("to") == version and isinstance(d.get("from"), str) and d["from"]
                and d["from"] != version and (not need_ok or d.get("result") == "ok")):
            return True
    return False


def _sync_dir(root) -> None:
    """fsync the state directory (security r2): after a power loss the marker must still be there — never a second run."""
    import os
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def grandfather(st, version=None) -> int | None:
    """Chairman 2026-11-22, a deliberate one-time relaxation (the silent form): the to-do items that exist when the
    updater first starts a version that keeps this record are recorded clean (id + digest, like every other record), so
    old work does not raise Face ID cards at night. → how many were recorded; None when the question was already settled.

    Narrow by construction:
      - called from exactly one place, `cli.cmd_serve`, after config_migrations.run and before the Host exists — so
        before any turn or heartbeat of the new version. No CLI subcommand, control request or preference reaches it;
      - once per state directory, for good: the marker is created first, exclusively (O_CREAT | O_EXCL), before anything
        is recorded. A marker that exists ends the call — also when the record was deleted or is unreadable (then
        everything is unclean, as designed) and on a downgrade / re-upgrade;
      - only on an upgrade: the updater's own receipt must say this state was just moved from another version to the
        running one (_upgraded_to). A fresh install, a hand install, a state with no receipt: the marker is written and
        nothing is recorded;
      - a crash after the marker and before the record leaves nothing recorded (cards), never a second run;
      - an item changed later follows the ordinary rules (its digest, status included, no longer matches).
    Marker, record and receipts are in the state directory, which the Agent's fence hides whole (fence.bwrap_argv
    `--tmpfs <state>`; fence.sbpl_profile `deny file-read* file-write* (subpath STATE)`). With isolation off the Agent
    is the same user and only the off-list private-state refusal stands in front of these files — the limit every host
    state file has. Known residue (accepted by the chairman): an item already poisoned before the upgrade is recorded
    clean; so is one written between the old version's stop and this call."""
    import os
    root = st.root
    try:
        fd = os.open(root / GRANDFATHERED, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None                    # settled before — or the marker cannot be made: nothing is recorded (cards)
    n, why = 0, "no_upgrade_receipt"
    try:
        with os.fdopen(fd, "w") as f:
            f.write('{"v": 1}\n')
            f.flush()
            os.fsync(f.fileno())
        _sync_dir(root)                # the marker's directory entry is durable before anything is recorded
        if version is None:
            from . import __version__ as version
        if _upgraded_to(st, version):
            why = "unreadable"
            items, marks = _read(root)
            if items is not None:
                old = {i: d for i, d in marks.items() if d}
                clean = {}
                try:
                    doc = json.loads((root / FILE).read_text(encoding="utf-8"))
                    rows = doc.get("clean") if isinstance(doc, dict) and doc.get("v") == 1 else None
                    clean = {k: v for k, v in (rows or {}).items() if isinstance(k, str) and isinstance(v, str) and len(v) == 64}
                except Exception:  # noqa: BLE001 — no earlier record
                    clean = {}
                clean.update(old)
                from .state import _write_private
                _write_private(root / FILE, json.dumps({"v": 1, "clean": dict(list(clean.items())[-KEEP:])}).encode("utf-8"))
                _sync_dir(root)
                n, why = len(old), "upgrade"
    except Exception:  # noqa: BLE001 — the marker stands; whatever was not recorded is simply not clean
        n, why = 0, "failed"
    try:
        st.log("todo_grandfathered", count=n, reason=why)
    except Exception:  # noqa: BLE001
        pass
    return n


def phone_wrote(host, session, before, item) -> bool:
    """Jarvis under the chairman's delegation, 2026-11-22: 「手机上新加的待办：主人已配对手机回合新加的、且该回合没读过外部内容的
    记为干净；读过外部内容的按标记规则走，不特殊放行。」 Called by Host.on_jcard right after the HOST itself wrote a to-do
    item for a `jcard_note` frame; `item` is exactly the item it wrote (jcards_todo.change's receipt), `before` the item
    as it was (None for a new one). Recorded clean only when ALL of this holds (security r2 §A6):
      - the request is a paired phone's: the session is the live, ready, allow-listed one and the request source of
        this very task is `phone` (provenance.paired_request, set per frame by the relay lane) — never an Agent tool
        call (the CLI does not come here), a friend, a channel, mail or a webhook;
      - no Agent turn that carries the external-read mark is in progress (then the ordinary rules decide at its end);
      - a new item; or an edit of an item that was clean before it. The frame carries only the item's text, so an
        edit never cleans an item whose other words (note, source, conditions, status) an unclean turn may have written.
    → True when recorded. Any later change by anything else breaks the digest as always."""
    try:
        from . import provenance, trust_taint
        if not host._paired_live(session):
            return False
        src = provenance._paired_source()
        if not isinstance(src, dict) or src.get("kind") != "phone" or src.get("role") != "owner":
            return False
        st = _state(host)
        if st["window"] is not None and trust_taint.tainted(getattr(host, "agent", None)) is not None:
            return False
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return False
        if before is not None and st["clean"].get(item["id"]) != digest(before):
            return False
        st["clean"][item["id"]] = digest(item)
        st["clean"] = dict(list(st["clean"].items())[-KEEP:])
        _save(host, st)
        return True
    except Exception:  # noqa: BLE001 — not recorded: unknown (the card)
        return False


def end_window(host, taint) -> None:
    """Host.agent_turn_end: the turn is over NOW. Security r1 H-3: only what changed between this turn's start and this
    moment is credited to it — the idle time until the next turn belongs to nobody (a cron job, a timer, any writer the
    busy board never sees), so whatever changes then stays unknown. `taint`: the turn's mark at this moment. Record what
    the turn wrote as clean only if everything in the module's list holds; otherwise record nothing. A turn end that
    comes early (a forged Stop while the model keeps working) closes the window early: later writes are not credited."""
    try:
        st = _state(host)
        window, st["window"] = st["window"], None
        if window is None:
            return
        items, now = _read(host.st.root)
        if now is None:
            return
        before = dict(st["clean"])
        bg = _background(host)
        if (window["clean"] and taint is None and window["snap"] is not None
                and (bg is None or (bg == window["bg"] and not bg[1]))):
            snap = window["snap"]
            for iid, d in now.items():
                if d is None or snap.get(iid) == d:
                    continue                                   # unchanged in this turn (or unreadable twice-stored id)
                if iid not in snap or (snap[iid] is not None and before.get(iid) == snap[iid]):
                    st["clean"][iid] = d                       # new here, or clean before and edited here
            st["clean"] = dict(list(st["clean"].items())[-KEEP:])
        if st["clean"] != before:
            _save(host, st)
    except Exception:  # noqa: BLE001 — nothing recorded: unknown stays unknown
        host.__dict__.pop(_ATTR, None)


def close_window(host, taint) -> None:
    """The next host-built turn is starting. A window still open here belongs to a turn whose end the host never saw:
    nothing of it is credited. Records of items that no longer exist drop out."""
    try:
        st = _state(host)
        st["window"] = None
        items, now = st["read"] = _read(host.st.root)    # open_window, called next, uses the same reading
        if now is None:
            return
        before = dict(st["clean"])
        st["clean"] = {i: d for i, d in st["clean"].items() if i in now}      # deleted / archived items drop out
        if st["clean"] != before:
            _save(host, st)
    except Exception:  # noqa: BLE001 — nothing recorded: unknown stays unknown
        host.__dict__.pop(_ATTR, None)


def open_window(host, send, ingress) -> bool:
    """A host-built turn starts. → True when it is a heartbeat whose claimable to-do work is not all known to be clean
    (the caller sets the mark). `ingress`: trust_taint.ingress_kind(send) — a turn that starts marked is never clean."""
    beat = getattr(send, "heartbeat_key", None) is not None       # heartbeat.HeartbeatSend, built only by the host
    try:
        from . import provenance
        st = _state(host)
        items, snap = st.pop("read", None) or _read(host.st.root)
        unclean = False
        if beat:
            unclean = snap is None or any(snap.get(i) is None or st["clean"].get(i) != snap[i] for i in _open(items))
        owner = send is not None and provenance.is_owner(getattr(send, "source", None))
        st["window"] = {"snap": snap, "bg": _background(host),
                        "clean": ingress is None and not unclean and (owner or beat)}
        return unclean
    except Exception:  # noqa: BLE001 — cannot tell: no clean window, and a heartbeat takes the mark
        host.__dict__.pop(_ATTR, None)
        return beat
