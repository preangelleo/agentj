"""0.9 → 0.10 state migration (the rename to Agent J): `~/.local/state/agentjarvis-alpha` → `~/.local/state/agentj`.

Runs at the start of every `agentj` command that opens state (cli.main → auto()), `serve` included — so a service unit
written by 0.9 that now starts the new code moves its own state before it binds the control socket. Rules (never lose a
key, a paired device, the approval passphrase or a log):
  * only when neither AGENTJ_STATE_DIR nor AGENTJARVIS_STATE_DIR is set, the old path is a real directory and the new path
    does not exist;
  * never while a serve is running on the old directory (its control socket answers): then the old directory stays in use
    and the reason is printed once;
  * one os.rename (same parent directory, so the same filesystem — atomic; nothing is copied, nothing deleted). If it fails
    the old directory stays in use. Afterwards a symlink at the old path points to the new one, so an older `jarvis`
    (rolled back) still finds everything; `migrated.json` in the new directory records from where, when and by which
    version; the directory stays 0700.
  * inside the state, the ≤ 0.9 default URLs (relay / web in config.json; api / app in config.json; api in cloud.json) are
    replaced by the new defaults. A value that is not exactly an old default (a custom relay, a test server) is left alone.
    The old values go into migrated.json, so `agentj migrate rollback` puts them back. devices.json is never touched:
    paired phones keep working because the relay answers on both host names.
`agentj migrate rollback` (no serve running): URLs restored, symlink removed, directory renamed back.
Every message goes to stderr: stdout stays the command's own (`--json` output is never mixed with a notice).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time

from . import __version__
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


def serve_running(root) -> bool:
    """Is a serve using this directory? = its control socket accepts a connection. A socket file that refuses (a crashed
    serve) is not running; anything unclear counts as running (never move a directory under a live process)."""
    sock = os.path.join(str(root), "control.sock")
    if not os.path.exists(sock):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(sock)
        return True
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except OSError:
        return True
    finally:
        s.close()


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
                st.write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
    if st.cloud_path.exists():
        with cloud_lock(st):
            c = _read_json(st.cloud_path)
            old, new = table[("cloud", "api")]
            if c is not None and _same(c.get("api"), old):
                changed["cloud.api"] = c["api"]
                c["api"] = new
                st.write_private(st.cloud_path, json.dumps(c, indent=1, ensure_ascii=False).encode())
    if changed:
        rec = read_record(st)
        rec.setdefault("v", 1)
        rec["urls"] = {**(rec.get("urls") or {}), **changed}
        rec.setdefault("urls_at", int(time.time()))
        _write_record(st, rec)
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


def auto(out=None) -> str:
    """Called before any command opens state. → "moved" · "refused" · "failed" · "none" (nothing to do / env override).
    Also replaces the old default URLs inside whichever state directory is in use."""
    result = "none"
    if not bool(getenv("AGENTJ_STATE_DIR")):
        new, old = default_state(), legacy_state()
        if old.is_dir() and not old.is_symlink() and not os.path.lexists(new):
            result = _move(old, new, out)
    st = State(state_dir())
    if st.config_path.exists():
        try:
            upgrade_urls(st, out)
        except OSError as e:
            _say(f"Agent J：旧网址没能替换（{type(e).__name__}），照常使用 / could not update the old addresses", out, "urls")
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
    _say(f"Agent J：状态目录已搬到 {tilde(new)}（密钥、已配对的手机、批准口令、记录都在；旧路径 {tilde(old)} 留了一个指向它的链接）。"
         f"撤销：agentj migrate rollback / the state directory moved to {tilde(new)}; the old path is a link to it", out)
    return "moved"


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
