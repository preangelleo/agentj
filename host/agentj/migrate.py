"""0.9 → 0.10 state migration (the rename to Agent J): `~/.local/state/agentjarvis-alpha` → `~/.local/state/agentj`.

Developer reference: documentation/notes/P0263-HOST-REFERENCE.md#migrate.
"""
from __future__ import annotations

import json
import fcntl
import os
import sys
import time

from .legacy_state_probe import serve_running
from . import __version__, humane
from .envcompat import getenv
from .state import (DEFAULT_RELAY, DEFAULT_WEB, OLD_DEFAULT_RELAY, OLD_DEFAULT_WEB, State, default_state, legacy_state,
                    state_dir)

OLD_API, OLD_APP = "https://api.agentjarvis.net", "https://alpha-app.agentjarvis.net"


def _defaults():
    from .cloud import DEFAULT_API, DEFAULT_APP
    return {("config", "relay"): (OLD_DEFAULT_RELAY, DEFAULT_RELAY), ("config", "web"): (OLD_DEFAULT_WEB, DEFAULT_WEB),
            ("config", "api"): (OLD_API, DEFAULT_API), ("config", "app"): (OLD_APP, DEFAULT_APP),
            ("cloud", "api"): (OLD_API, DEFAULT_API)}


_said: set[str] = set()


def _say(msg: str, out=None, key: str | None = None) -> None:
    """One line on stderr, at most once per process for the same key."""
    if key:
        if key in _said:
            return
        _said.add(key)
    print(msg, file=out or sys.stderr, flush=True)


def tilde(p) -> str:
    h = os.path.expanduser("~").rstrip("/")
    p = str(p)
    return "~" + p[len(h):] if h and (p == h or p.startswith(h + "/")) else p


def read_record(st: State) -> dict:
    try:
        d = json.loads(st.migrated_path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_record(st: State, rec: dict) -> None:
    st.write_private(st.migrated_path, json.dumps(rec, indent=1, ensure_ascii=False).encode())


def _same(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.rstrip("/") == b.rstrip("/")


def _read_json(path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def needs_url_upgrade(st: State) -> bool:
    """Read-only preflight: modern/custom URLs never enter the migration writer.

    Do not cache by version: an owner may restore old state after an upgrade.
    Missing/malformed files stay untouched; a later repaired file is checked again.
    Existing config/cloud locks and the durable urls/urls_at rollback record remain
    owned by upgrade_urls, rather than a second migration/configuration system.
    """
    if not st.config_path.exists():
        return False
    table = _defaults()
    for name, path in (("config", st.config_path), ("cloud", st.cloud_path)):
        doc = _read_json(path)
        if doc is not None and any(f == name and _same(doc.get(k), old)
                                   for (f, k), (old, _) in table.items()):
            return True
    return False


def upgrade_urls_if_needed(st: State, out=None) -> dict:
    """Serialize legacy URL writers, without creating a lock on a modern read path.

    The second check lets concurrent CLI processes observe the first completion.
    Exceptions propagate without a new completion record, allowing the next call
    to retry. upgrade_urls retains the existing per-file locks and rollback record.
    """
    if not needs_url_upgrade(st):
        return {}
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(st.root / "migrate-urls.lock", flags, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not needs_url_upgrade(st):
            return {}
        return upgrade_urls(st, out)


def _remember_urls(st: State, changed: dict) -> None:
    """Write rollback intent before a config/cloud replacement can become visible.

    A crash may leave an intent for an unchanged file; restore already compares
    against the new default, so that intent is safe and retryable.
    """
    rec = read_record(st)
    rec.setdefault("v", 1)
    rec["urls"] = {**(rec.get("urls") or {}), **changed}
    rec.setdefault("urls_at", int(time.time()))
    _write_record(st, rec)


def upgrade_urls(st: State, out=None) -> dict:
    """Old default URLs → new defaults (config.json, cloud.json). → {"config.relay": old, …} of what changed (also kept in
    migrated.json under "urls"). Idempotent; custom values untouched."""
    if not st.config_path.exists():
        return {}
    from .cloud import cloud_lock
    changed: dict[str, str] = {}
    table = _defaults()
    with st.config_lock():
        cfg = _read_json(st.config_path)
        if cfg is not None:
            hit = False
            for (f, k), (old, new) in table.items():
                if f == "config" and _same(cfg.get(k), old):
                    changed[f"config.{k}"] = cfg[k]
                    cfg[k] = new
                    hit = True
            if hit:
                _remember_urls(st, changed)
                st.write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
    if st.cloud_path.exists():
        with cloud_lock(st):
            c = _read_json(st.cloud_path)
            old, new = table[("cloud", "api")]
            if c is not None and _same(c.get("api"), old):
                changed["cloud.api"] = c["api"]
                c["api"] = new
                _remember_urls(st, changed)
                st.write_private(st.cloud_path, json.dumps(c, indent=1, ensure_ascii=False).encode())
    if changed:
        _say("Agent J：已把旧网址换成新网址 / switched to the new addresses (" + ", ".join(sorted(changed)) + ")", out)
    return changed


def _restore_urls(st: State) -> list[str]:
    """Put the recorded old URLs back — only where the value is still the new default (a later edit by the human stays)."""
    rec = read_record(st)
    urls = rec.get("urls") if isinstance(rec.get("urls"), dict) else {}
    table, done = _defaults(), []
    from .cloud import cloud_lock
    if any(k.startswith("config.") for k in urls) and st.config_path.exists():
        with st.config_lock():
            cfg = _read_json(st.config_path)
            if cfg is not None:
                for key, old in urls.items():
                    f, k = key.split(".", 1)
                    if f == "config" and (f, k) in table and _same(cfg.get(k), table[(f, k)][1]):
                        cfg[k] = old
                        done.append(key)
                st.write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
    if "cloud.api" in urls and st.cloud_path.exists():
        with cloud_lock(st):
            c = _read_json(st.cloud_path)
            if c is not None and _same(c.get("api"), table[("cloud", "api")][1]):
                c["api"] = urls["cloud.api"]
                st.write_private(st.cloud_path, json.dumps(c, indent=1, ensure_ascii=False).encode())
                done.append("cloud.api")
    return done


def pending(home: str | None = None) -> str | None:
    """Why the default state still lives at the old path: "serve_running" · "env" (an override is set) · None (nothing to
    move, or already moved)."""
    new, old = default_state(home), legacy_state(home)
    if not (old.is_dir() and not old.is_symlink()) or os.path.lexists(new):
        return None
    if bool(getenv("AGENTJ_STATE_DIR")):
        return "env"
    return "serve_running" if serve_running(old) else "ready"


LEGACY_ENV = "AGENTJ_LEGACY_STATE"     # migrate | fresh: the owner's answer for a run without a terminal


def legacy_summary(old) -> dict:
    """What the old state holds, for the owner to recognise it — names and counts only, never a key or a token."""
    st = State(old)
    out = {"path": tilde(old), "agent_name": None, "phones": [], "account": None, "created": None, "last_used": None}
    try:
        cfg = st.config()
        out["agent_name"] = cfg.get("agent_name") if isinstance(cfg.get("agent_name"), str) else None
        out["created"] = int(cfg["created"]) if isinstance(cfg.get("created"), (int, float)) else None
    except (OSError, ValueError):
        pass
    try:
        out["phones"] = [str((v or {}).get("name") or "?")[:40] for v in st.devices().values()]
    except (OSError, ValueError, AttributeError):
        pass
    c = _read_json(st.cloud_path)
    if c and isinstance(c.get("tenant"), dict) and isinstance(c["tenant"].get("slug"), str):
        out["account"] = c["tenant"]["slug"]
    times = []
    for p in (st.log_path, st.config_path, st.devices_path):
        try:
            times.append(int(p.stat().st_mtime))
        except OSError:
            pass
    out["last_used"] = max(times) if times else None
    return out


def _day(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "?"


def legacy_text(s: dict) -> str:
    phones = "、".join(s["phones"]) if s["phones"] else "无 / none"
    return ("Agent J：发现一份旧的身份（≤ 0.9 的状态目录）/ found an older Agent J identity:\n"
            f"  位置 / where: {s['path']}\n"
            f"  Agent 名字 / name: {s['agent_name'] or '（未设置 / not set）'}\n"
            f"  已配对手机 / paired phones: {len(s['phones'])}（{phones}）\n"
            f"  Agent J 账号 / account: {s['account'] or '（未加入 / none）'}\n"
            f"  创建 / created: {_day(s['created'])} · 最近使用 / last used: {_day(s['last_used'])}")


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _decision(decision):
    d = decision or (getenv(LEGACY_ENV) or "").strip().lower() or None
    return d if d in ("migrate", "fresh") else None


def auto(out=None, decision: str | None = None, interactive: bool | None = None) -> str:
    """Called before any command opens state. → "moved" · "refused" · "failed" · "fresh" · "undecided" (no terminal and
    no answer: the caller stops) · "none" (nothing to do / env override). Also replaces the old default URLs inside
    whichever state directory is in use."""
    result = "none"
    if not bool(getenv("AGENTJ_STATE_DIR")):
        new, old = default_state(), legacy_state()
        if old.is_dir() and not old.is_symlink() and not os.path.lexists(new):
            d = _decision(decision)
            if serve_running(old) or d == "migrate":
                result = _move(old, new, out)      # a live serve on the old directory: keep using it (refused)
            else:
                if d is None and (_interactive() if interactive is None else interactive):
                    _say(legacy_text(legacy_summary(old)), out)
                    try:
                        ans = input("把这份旧身份搬过来继续用？/ Move it here and keep using it? [y/N] ").strip().lower()
                    except EOFError:
                        ans = ""
                    d = "migrate" if ans in ("y", "yes", "是") else "fresh"
                    if d == "migrate":
                        result = _move(old, new, out)
                if d == "fresh":
                    result = _fresh(old, new, out)
                elif d is None:
                    _say(legacy_text(legacy_summary(old)) + "\n"
                         "没有终端可以问你，所以什么都没动 / no terminal to ask you, so nothing was changed. 请主人决定后重跑："
                         "\n  搬过来继续用 / keep using it:  agentj --migrate-legacy <命令 / command>"
                         "\n  不要它，用全新身份 / start fresh:  agentj --fresh <命令 / command>（旧目录原样保留 / the old "
                         "directory stays as it is）\n  （或环境变量 / or env AGENTJ_LEGACY_STATE=migrate|fresh）", out, "undecided")
                    return "undecided"
    st = State(state_dir())
    try:
        upgrade_urls_if_needed(st, out)
    except OSError as e:
        kind = type(e).__name__
        _say(humane.say("migration.urls_failed", kind=kind) + " / "
             + humane.say("migration.urls_failed", lang="en", kind=kind), out, "urls")
    return result


def _move(old, new, out=None) -> str:
    if serve_running(old):
        _say(f"Agent J：有一个 serve 正在使用旧状态目录 {tilde(old)}，这次先不搬，照常使用它（一切正常）。停掉它（或运行 "
             "`agentj service install` 换成新服务）后会自动搬到 " + tilde(new) + " / a serve is running on the old state "
             "directory: it stays in use for now and moves automatically once that serve has stopped.", out, "refused")
        return "refused"
    try:
        os.rename(old, new)
    except OSError as e:
        if os.path.isdir(new) and not os.path.lexists(old):     # another agentj process moved it a moment ago
            return "moved"
        _say(f"Agent J：状态目录没能搬动（{type(e).__name__}），继续使用 {tilde(old)}（一切正常） / could not move the state "
             f"directory: {tilde(old)} stays in use", out, "failed")
        return "failed"
    os.chmod(new, 0o700)
    link = True
    try:
        os.symlink(os.path.basename(new), old)       # relative: same parent directory
    except OSError:
        link = False
    st = State(new)
    rec = read_record(st)
    rec.update({"v": 1, "migrated_from": str(old), "at": int(time.time()), "version": __version__, "symlink": link})
    _write_record(st, rec)
    _say(f"Agent J：状态目录已搬到 {tilde(new)}（密钥、已配对的手机、遥控器配对密码、记录都在；旧路径 {tilde(old)} 留了一个指向它的链接）。"
         f"撤销：agentj migrate rollback / the state directory moved to {tilde(new)}; the old path is a link to it", out)
    return "moved"


def _fresh(old, new, out=None) -> str:
    """The owner chose a new identity: create the empty new state directory (so this is never asked again) and leave
    the old one exactly where it is."""
    try:
        os.mkdir(new, 0o700)
    except FileExistsError:
        return "fresh"
    os.chmod(new, 0o700)
    _write_record(State(new), {"v": 1, "legacy_left": str(old), "at": int(time.time()), "version": __version__})
    _say(f"Agent J：用全新身份；旧目录 {tilde(old)} 原样留着，没动（不再问）。不要了可以自己删掉。/ starting fresh; the old "
         f"directory {tilde(old)} is left untouched (you will not be asked again)", out)
    return "fresh"


def status() -> dict:
    """What `agentj migrate status` shows."""
    new, old = default_state(), legacy_state()
    cur = state_dir()
    st = State(cur)
    rec = read_record(st) if cur.exists() else {}
    link = old.is_symlink() and os.path.realpath(old) == os.path.realpath(new)
    return {"state_dir": str(cur), "env_override": bool(getenv("AGENTJ_STATE_DIR")), "new": str(new), "old": str(old),
            "moved": bool(rec.get("migrated_from")) or link, "old_is_link": link, "pending": pending(),
            "record": rec}


class RollbackError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def rollback(out=None) -> dict:
    """Undo: old URLs back, symlink removed, directory renamed back. Raises RollbackError("serve_running" | "nothing" |
    "old_path_taken"). With a state-dir override only the URLs are restored."""
    if bool(getenv("AGENTJ_STATE_DIR")):
        st = State(state_dir())
        if serve_running(st.root):
            raise RollbackError("serve_running")
        urls = _restore_urls(st)
        if not urls:
            raise RollbackError("nothing")
        rec = read_record(st)
        rec.pop("urls", None)
        rec.pop("urls_at", None)
        _write_record(st, rec)
        return {"moved_back": False, "urls": urls, "state_dir": str(st.root)}
    new, old = default_state(), legacy_state()
    if not new.is_dir() or new.is_symlink():
        raise RollbackError("nothing")
    st = State(new)
    rec = read_record(st)
    link = old.is_symlink() and os.path.realpath(old) == os.path.realpath(new)
    if not rec.get("migrated_from") and not link:
        raise RollbackError("nothing")
    if os.path.lexists(old) and not link:
        raise RollbackError("old_path_taken")
    if serve_running(new):
        raise RollbackError("serve_running")
    urls = _restore_urls(st)
    try:
        st.migrated_path.unlink()
    except FileNotFoundError:
        pass
    if link:
        os.unlink(old)
    os.rename(new, old)
    return {"moved_back": True, "urls": urls, "state_dir": str(old)}


ROLLBACK_MESSAGES = {
    "serve_running": "有 serve 正在运行：先停掉它（`agentj service uninstall` 或 Ctrl-C）再撤销 / a serve is running: stop it first",
    "nothing": "没有可撤销的搬迁 / nothing to roll back",
    "old_path_taken": "旧路径已被别的文件占用，没有动任何东西 / the old path is taken by something else: nothing changed",
}
