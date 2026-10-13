"""P207: the owner's own data survives every upgrade — snapshots of the user files and a start-up self-heal.

What counts as the owner's data (USER_FILES): friends and the friend card, remote names, the address book, the theme, the
preferences file, the e-mail channel's settings. All of it lives in the state dir (and one preferences file), is written
atomically (diskq), and no upgrade or migration ever deletes it; this module is the net under that rule:

- `snapshot(st, reason)` copies the files that exist into `<state>/backups/<UTC stamp>-<reason>/` (dir 0700, files 0600, a
  manifest with sha256s) and keeps the newest KEEP snapshots. Taken before every upgrade (update.apply / authorized_apply)
  and once per installed version when serve starts (`version_snapshot`) — so an upgrade made by another path (fleet,
  `uv tool upgrade`, a reinstall) is covered too.
- `heal(st)` at serve start: a CRITICAL file that is missing (or a JSON file that no longer parses) while the newest snapshot
  that has it holds a non-empty readable copy is put back, and each restore is logged (`userdata_restored`) and returned so serve can tell the owner.
  A file that exists and parses is never touched — an owner who removed every friend has `friends.json` = {} and keeps it.

Backups stay inside the state dir: the fence hides it from every Agent, so nothing here widens who can read what. No
content is logged — names of files only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import shutil

KEEP = 10
DIR = "backups"
MANIFEST = "manifest.json"
# relative to the state dir; "pref:" = the preferences file (outside the state dir). Globs allowed (one level).
USER_FILES = (
    "peer/friends.json", "peer/settings.json", "peer/groups.json", "peer/blocked.json", "peer/pending.json",
    "peer/sessions.json", "peer/profile.md", "peer/friends/*/context.md",
    "peer/peer_ed25519.key", "peer/peer_x25519.key",
    "devices.json", "contacts/contacts.json", "agentperm/theme/theme.json", "language.json",
    "private-instructions.json", "mail/config.json", "pref:config.json5",
)
# files whose absence after an upgrade would read as "my data is gone" — restored by heal() when a snapshot has them
# Never auto-healed (kept in snapshots only): devices.json — restoring it could re-pair a remote revoked since the snapshot
# (red line: only paired devices); the peer keys — an owner may have reset the Agent ID on purpose.
CRITICAL = ("peer/friends.json", "peer/settings.json", "peer/groups.json", "peer/profile.md", "contacts/contacts.json")
_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9._-]{1,40}")


def _root(st) -> pathlib.Path:
    return pathlib.Path(st.root)


def _pref_path(st) -> pathlib.Path | None:
    """The preferences file belongs to the instance's own state dir only (an isolated AGENTJ_STATE_DIR — tests, QA — never
    reaches into the real ~/.config unless XDG_CONFIG_HOME points it elsewhere)."""
    try:
        from .instance import current, state_root
        from .preferences import path as pref_path
        if not os.environ.get("XDG_CONFIG_HOME") and _root(st).resolve() != state_root(current()).resolve():
            return None
        return pathlib.Path(pref_path())
    except Exception:  # noqa: BLE001 — no preferences path: the snapshot simply has no pref file
        return None


def _resolve(st, rel: str) -> list[tuple[str, pathlib.Path]]:
    if rel.startswith("pref:"):
        p = _pref_path(st)
        return [(rel, p)] if p is not None and p.is_file() else []
    root = _root(st)
    if "*" in rel:
        return [(str(p.relative_to(root)), p) for p in sorted(root.glob(rel)) if p.is_file()]
    p = root / rel
    return [(rel, p)] if p.is_file() else []


def _readable(rel: str, data: bytes) -> bool:
    if not data:
        return False
    if rel.endswith(".json") or rel.endswith(".json5"):
        if rel.endswith(".json5"):
            return True
        try:
            json.loads(data)
        except ValueError:
            return False
        return True
    if rel.endswith(".key"):
        return len(data) == 32
    return True


def _private_write(path: pathlib.Path, data: bytes) -> None:
    from .state import _write_private
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private(path, data)


def snapshots(st) -> list[pathlib.Path]:
    """Newest first."""
    d = _root(st) / DIR
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_dir() and _NAME.fullmatch(p.name)), key=lambda p: p.name, reverse=True)


def snapshot(st, reason: str, now: _dt.datetime | None = None) -> pathlib.Path | None:
    """Copy the user files that exist; None when there is nothing to keep (a brand-new state dir)."""
    root = _root(st)
    if not root.is_dir():
        return None
    files = [x for rel in USER_FILES for x in _resolve(st, rel)]
    if not files:
        return None
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    why = re.sub(r"[^a-z0-9._-]", "-", reason.lower())[:40] or "snapshot"
    base = root / DIR
    base.mkdir(mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)
    dest, n = base / f"{stamp}-{why}", 1
    while dest.exists():
        n += 1
        dest = base / f"{stamp}-{why[:40 - len(str(n))]}{n}"
    dest.mkdir(mode=0o700)
    manifest = {"reason": why, "at": stamp, "files": {}}
    for rel, p in files:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        name = rel.replace("pref:", "pref/")
        _private_write(dest / name, data)
        manifest["files"][rel] = hashlib.sha256(data).hexdigest()
    _private_write(dest / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1).encode())
    prune(st)
    return dest


def prune(st, keep: int = KEEP) -> int:
    gone = 0
    for p in snapshots(st)[keep:]:
        shutil.rmtree(p, ignore_errors=True)
        gone += 1
    return gone


def _manifest(snap: pathlib.Path) -> dict:
    try:
        m = json.loads((snap / MANIFEST).read_text())
    except (OSError, ValueError):
        return {}
    return m if isinstance(m, dict) else {}


def version_snapshot(st, version: str) -> pathlib.Path | None:
    """Once per installed version (serve start): the first start of a new version keeps what the old one left."""
    tag = "v" + re.sub(r"[^a-z0-9.]", "", version.lower())
    if any(_manifest(s).get("version") == version for s in snapshots(st)):
        return None
    snap = snapshot(st, "start-" + tag)
    if snap is not None:
        m = _manifest(snap)
        m["version"] = version
        _private_write(snap / MANIFEST, json.dumps(m, ensure_ascii=False, indent=1).encode())
    return snap


def _missing(st, rel: str) -> bool:
    """Gone, or a JSON file that no longer parses. An empty text file is the owner's choice (a cleared profile), not a loss."""
    p = _root(st) / rel
    try:
        data = p.read_bytes()
        return rel.endswith(".json") and not _readable(rel, data)
    except FileNotFoundError:
        return True
    except OSError:
        return False        # unreadable for another reason (permissions): never overwrite what we cannot see


def heal(st) -> list[dict]:
    """Restore CRITICAL files that are gone (or corrupt) from the newest snapshot holding a good copy. → [{file, from}]."""
    if not _root(st).is_dir():
        return []
    snaps = snapshots(st)
    if not snaps:
        return []
    out = []
    for rel in CRITICAL:
        if not _missing(st, rel):
            continue
        for snap in snaps:
            src = snap / rel
            try:
                data = src.read_bytes()
            except OSError:
                continue
            if not data.strip() or (rel.endswith(".json") and _readable(rel, data) and json.loads(data) in ({}, [])):
                break       # the newest copy was empty too: nothing lost, nothing to bring back (never reach further back)
            if not _readable(rel, data):
                continue    # a corrupt copy: an older good one is still the owner's last real state
            _private_write(_root(st) / rel, data)
            out.append({"file": rel, "from": snap.name})
            break
    for r in out:
        st.log("userdata_restored", file=r["file"], snapshot=r["from"])
    return out


def restored_text(items: list[dict], lang: str = "zh") -> str:
    labels = {"peer/friends.json": ("好友名单", "friends"), "peer/settings.json": ("名片和好友设置", "card and friend settings"),
              "peer/groups.json": ("好友分组", "friend groups"), "peer/profile.md": ("可以告诉好友的事", "what friends may know"),
              "contacts/contacts.json": ("通讯录", "address book")}
    en = lang == "en"
    names = []
    for r in items:
        n = labels.get(r["file"], (r["file"], r["file"]))[1 if en else 0]
        if n not in names:
            names.append(n)
    if en:
        return "Some of your data was missing after start-up and has been restored from the automatic backup: " + ", ".join(names) + "."
    return "启动时发现有数据缺失，已从自动备份恢复：" + "、".join(names) + "。"
