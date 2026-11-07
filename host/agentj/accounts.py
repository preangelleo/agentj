"""#427: several sign-ins per harness, one tap to switch. Claude Code = a setup-token per account (slot
<state>/accounts/claude/<id>.token → CLAUDE_CODE_OAUTH_TOKEN; method `login` = no token). Codex = a CODEX_HOME slot per
account (<state>/accounts/codex/<id>/auth.json), swapped into $CODEX_HOME. 0700 / 0600, no-follow fds, temp + rename;
a credential never reaches a view, log or error. ADR-427 in agentjarvis/ARCHITECTURE.md.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import sys
import time
import unicodedata
from pathlib import Path

HARNESSES = ("claude", "codex")
LABEL = {"claude": "Claude Code", "codex": "Codex"}
MAX_ITEMS = 8
NAME_MAX = 24
FILE = "accounts.json"
DIR = "accounts"
LOCK = "accounts.lock"
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
TOKEN_RE = re.compile(rb"sk-ant-oat01-[A-Za-z0-9_\-]{40,400}")
ID_RE = re.compile(r"[0-9a-f]{8}")
MAIN = {"zh": "主号", "en": "Main"}         # the account that was in use before the first new one was added
FILE_MAX = 64 * 1024
SLOT_MAX = 256 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class AccountError(Exception):
    """args[0]: a fixed reason word, never a value."""


# ------------------------------------------------------------------ no-follow file helpers (worker threads only)
def _open_dir(base, parts=(), create=True) -> int:
    """fd of base/parts…, each part opened O_NOFOLLOW (created 0700)."""
    fd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for p in parts:
            try:
                nfd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(p, 0o700, dir_fd=fd)
                nfd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fd)
            except OSError:
                raise AccountError("unsafe") from None
            os.close(fd)
            fd = nfd
            info = os.fstat(fd)
            if info.st_uid != os.getuid():
                raise AccountError("unsafe")
            if stat.S_IMODE(info.st_mode) != 0o700:
                os.fchmod(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_at(dfd: int, name: str, cap: int = SLOT_MAX) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError:
        raise AccountError("unsafe") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > cap:
            raise AccountError("unsafe")
        chunks, left = [], cap + 1
        while left > 0:
            b = os.read(fd, min(65536, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
        if len(data) > cap:
            raise AccountError("unsafe")
        return data
    finally:
        os.close(fd)


def _write_at(dfd: int, name: str, data) -> None:
    from . import diskq
    tmp = f".{name}.agentj-{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=dfd)
    try:
        try:
            os.write(fd, bytes(data))
            os.fchmod(fd, 0o600)
            diskq.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dfd)
        raise


def _unlink_at(dfd: int, name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=dfd)


@contextlib.contextmanager
def _locked(root):
    if not Path(root).is_dir():
        raise AccountError("not_found")
    fd = os.open(str(Path(root) / LOCK), os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ------------------------------------------------------------------ the registry
def _blank() -> dict:
    return {"v": 1, "harness": {h: {"active": None, "items": []} for h in HARNESSES}}


def _clean_item(h: str, it) -> dict | None:
    if not isinstance(it, dict) or not isinstance(it.get("id"), str) or not ID_RE.fullmatch(it["id"]):
        return None
    if not isinstance(it.get("name"), str) or not 1 <= len(it["name"]) <= NAME_MAX:
        return None
    method = it.get("method")
    if method not in (("token", "login") if h == "claude" else ("device",)):
        return None
    out = {"id": it["id"], "name": it["name"], "method": method,
           "added": int(it["added"]) if isinstance(it.get("added"), (int, float)) else 0,
           "last_quota": _clean_quota(it.get("last_quota"))}
    if isinstance(it.get("minted"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", it["minted"]):
        out["minted"] = it["minted"]
    if isinstance(it.get("fp"), str) and re.fullmatch(r"[0-9a-f]{16}", it["fp"]):
        out["fp"] = it["fp"]
    return out


def _clean_quota(q) -> dict | None:
    if not isinstance(q, dict):
        return None
    pct, at = q.get("pct"), q.get("at")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not isinstance(at, (int, float)):
        return None
    return {"pct": round(float(pct), 1), "window": "h5" if q.get("window") == "h5" else "week", "at": int(at), **({"reset_at": q["reset_at"]} if isinstance(q.get("reset_at"), (int, float)) else {})}


def load(root) -> dict:
    """Missing / broken / unsafe file = no accounts (0.26.6 behaviour)."""
    d = _blank()
    try:
        fd = _open_dir(root, create=False)
        try:
            raw = _read_at(fd, FILE, FILE_MAX)
        finally:
            os.close(fd)
        doc = json.loads(raw) if raw else {}
    except (OSError, ValueError, AccountError):
        doc = {}
    hs = doc.get("harness") if isinstance(doc, dict) and isinstance(doc.get("harness"), dict) else {}
    for h in HARNESSES:
        src = hs.get(h) if isinstance(hs.get(h), dict) else {}
        items, seen = [], set()
        for it in (src.get("items") if isinstance(src.get("items"), list) else []):
            c = _clean_item(h, it)
            if c and c["id"] not in seen and c["name"].casefold() not in {x["name"].casefold() for x in items}:
                seen.add(c["id"])
                items.append(c)
        d["harness"][h]["items"] = items[:MAX_ITEMS]
        if src.get("active") in seen:
            d["harness"][h]["active"] = src["active"]
        if isinstance(src.get("switched"), (int, float)):
            d["harness"][h]["switched"] = float(src["switched"])
    return d


def _save(root, d: dict) -> None:
    fd = _open_dir(root, create=False)
    try:
        _write_at(fd, FILE, json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode())
    finally:
        os.close(fd)


def valid_name(name) -> str:
    if not isinstance(name, str):
        raise AccountError("bad_name")
    n = name.strip()
    if not 1 <= len(n) <= NAME_MAX or "/" in n or "\\" in n or \
            any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in n):   # F8: no bidi / zero-width tricks
        raise AccountError("bad_name")
    return n


def find(d: dict, h: str, ref) -> dict | None:
    if h not in HARNESSES or not isinstance(ref, str):
        return None
    items = d["harness"][h]["items"]
    ref = ref.strip()
    return next((i for i in items if i["id"] == ref), None) or \
        next((i for i in items if i["name"].casefold() == ref.casefold()), None)


def name_hash(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:8]


def _new_id(d: dict, h: str) -> str:
    used = {i["id"] for i in d["harness"][h]["items"]}
    while True:
        x = secrets.token_hex(4)
        if x not in used:
            return x


# ------------------------------------------------------------------ Codex helpers
def codex_home(environ=None) -> Path:
    from .quota import codex_home as h
    return h(environ)


def codex_store(home: Path) -> str:
    """`cli_auth_credentials_store` from $CODEX_HOME/config.toml (default "file")."""
    try:
        fd = os.open(str(home), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return "file"
    try:
        raw = _read_at(fd, "config.toml", 512 * 1024)
    except AccountError:
        return "file"
    finally:
        os.close(fd)
    if not raw:
        return "file"
    try:
        import tomllib
        v = tomllib.loads(raw.decode("utf-8", "replace")).get("cli_auth_credentials_store")
    except Exception:  # noqa: BLE001 — Codex itself reports a broken config
        return "file"
    return v if v in ("file", "keyring", "auto", "ephemeral") else "file"


def codex_fp(data: bytes | None) -> str | None:
    """16 hex of ChatGPT account id + id-token subject (identifiers, never a token)."""
    if not data:
        return None
    try:
        t = json.loads(data).get("tokens") or {}
        acct = t.get("account_id")
        idt = t.get("id_token")
        if isinstance(idt, dict):
            idt = idt.get("raw_jwt")
        sub = None
        if isinstance(idt, str) and idt.count(".") == 2:
            seg = idt.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
            sub = claims.get("sub") or claims.get("email")
    except (ValueError, AttributeError, TypeError):
        return None
    if not acct and not sub:
        return None
    return hashlib.sha256(f"{acct}|{sub}".encode()).hexdigest()[:16]


def _codex_unsupported(home: Path, has_file: bool) -> bool:
    store = codex_store(home)
    return store in ("keyring", "ephemeral") or (store == "auto" and not has_file)


def _codex_current(home: Path) -> bytes | None:
    try:
        fd = os.open(str(home), os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    try:
        return _read_at(fd, "auth.json")
    finally:
        os.close(fd)


# ------------------------------------------------------------------ migration + adding
def _meta_minted(root) -> str | None:
    from . import auth_rescue
    from .state import State
    with contextlib.suppress(Exception):
        c = auth_rescue.read_meta(State(Path(root))).get("claude")
        if isinstance(c, dict) and isinstance(c.get("minted"), str):
            return c["minted"]
    return None


def _migrate(root, d: dict, h: str, lang: str) -> None:
    """First add: what is in use becomes 「主号」 (nothing in use → nothing)."""
    hd = d["harness"][h]
    if hd["items"]:
        return
    iid = _new_id(d, h)
    item = {"id": iid, "name": MAIN.get(lang, MAIN["zh"]), "added": int(time.time()), "last_quota": None}
    if h == "claude":
        from . import auth_rescue
        tok = (auth_rescue.effective_auth_env().get(TOKEN_ENV) or "").encode()
        if TOKEN_RE.fullmatch(tok):
            fd = _open_dir(root, (DIR, "claude"))
            try:
                _write_at(fd, iid + ".token", tok)
            finally:
                os.close(fd)
            item["method"] = "token"
            minted = _meta_minted(root)
            if minted:
                item["minted"] = minted
        else:
            item["method"] = "login"
        tok = b""
    else:
        cur = _codex_current(codex_home())
        if not cur:
            return
        fd = _open_dir(root, (DIR, "codex", iid))
        try:
            _write_at(fd, "auth.json", cur)
        finally:
            os.close(fd)
        item["method"] = "device"
        fp = codex_fp(cur)
        if fp:
            item["fp"] = fp
        cur = None
    hd["items"].append(item)
    hd["active"] = iid


def check_new(root, h: str, name, lang: str = "zh") -> str:
    """→ the cleaned name, or AccountError before any sign-in starts."""
    if h not in HARNESSES:
        raise AccountError("unsupported")
    n = valid_name(name)
    d = load(root)
    items = d["harness"][h]["items"]
    if any(i["name"].casefold() == n.casefold() for i in items) or \
            (not items and n.casefold() == MAIN.get(lang, MAIN["zh"]).casefold()):
        raise AccountError("name_taken")
    if len(items) + (0 if items else 1) >= MAX_ITEMS:
        raise AccountError("full")
    if h == "codex":
        home = codex_home()
        if _codex_unsupported(home, _codex_current(home) is not None):
            raise AccountError("unsupported_store")
    return n


def add_claude_token(root, name, tok, lang: str = "zh") -> dict:
    """A live-checked token → a NEW slot; the account in use is untouched."""
    if not TOKEN_RE.fullmatch(bytes(tok)):
        raise AccountError("bad_token")
    with _locked(root):
        n = check_new(root, "claude", name, lang)
        d = load(root)
        _migrate(root, d, "claude", lang)
        iid = _new_id(d, "claude")
        fd = _open_dir(root, (DIR, "claude"))
        try:
            _write_at(fd, iid + ".token", tok)
        finally:
            os.close(fd)
        item = {"id": iid, "name": n, "method": "token", "added": int(time.time()), "last_quota": None,
                "minted": time.strftime("%Y-%m-%d")}
        d["harness"]["claude"]["items"].append(item)
        _save(root, d)
    return public(item)


def new_codex_slot(root) -> tuple[str, Path]:
    """A fresh empty CODEX_HOME (0700, created exclusively, never a registered id) with the file store pinned (F8:
    Codex must write auth.json, not the keyring). → (id, path)."""
    used = {i["id"] for i in load(root)["harness"]["codex"]["items"]}
    fd = _open_dir(root, (DIR, "codex"))
    try:
        while True:
            iid = secrets.token_hex(4)
            if iid in used:
                continue
            try:
                os.mkdir(iid, 0o700, dir_fd=fd)
                break
            except FileExistsError:
                continue
        sfd = os.open(iid, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fd)
        try:
            _write_at(sfd, "config.toml", b'cli_auth_credentials_store = "file"\n')
        finally:
            os.close(sfd)
    finally:
        os.close(fd)
    return iid, Path(root) / DIR / "codex" / iid


def discard_slot(root, h: str, iid: str) -> None:
    if h not in HARNESSES or not isinstance(iid, str) or not ID_RE.fullmatch(iid):
        return
    try:
        fd = _open_dir(root, (DIR, h), create=False)
    except (FileNotFoundError, AccountError):
        return
    try:
        if h == "claude":
            _unlink_at(fd, iid + ".token")
            return
        _rmtree_at(fd, iid)
    finally:
        os.close(fd)


def _rmtree_at(dfd: int, name: str, depth: int = 0) -> None:
    """Never follows a symlink."""
    try:
        sfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=dfd)
    except FileNotFoundError:
        return
    except OSError:
        _unlink_at(dfd, name)
        return
    try:
        for e in os.listdir(sfd):
            try:
                is_dir = stat.S_ISDIR(os.stat(e, dir_fd=sfd, follow_symlinks=False).st_mode)
            except FileNotFoundError:
                continue
            if is_dir and depth < 8:
                _rmtree_at(sfd, e, depth + 1)
            else:
                _unlink_at(sfd, e)
    finally:
        os.close(sfd)
    with contextlib.suppress(OSError):
        os.rmdir(name, dir_fd=dfd)


def commit_codex(root, iid: str, name, lang: str = "zh", *, bound_live=None) -> dict:
    """After the slot's login and live check: register it. Same ChatGPT account as one already here → refused (two
    slots of one account would invalidate each other's rotating refresh token)."""
    with _locked(root):
        try:
            if bound_live is not None and not bound_live():
                raise AccountError("cancelled")
            n = check_new(root, "codex", name, lang)
            fd = _open_dir(root, (DIR, "codex", iid), create=False)
            try:
                data = _read_at(fd, "auth.json")
            finally:
                os.close(fd)
            if not data:
                raise AccountError("unsupported_store")
            fp = codex_fp(data)
            data = None
            d = load(root)
            _migrate(root, d, "codex", lang)
            if fp and any(i.get("fp") == fp for i in d["harness"]["codex"]["items"]):
                raise AccountError("same_account")
        except BaseException:
            discard_slot(root, "codex", iid)
            raise
        item = {"id": iid, "name": n, "method": "device", "added": int(time.time()), "last_quota": None,
                **({"fp": fp} if fp else {})}
        d["harness"]["codex"]["items"].append(item)
        if bound_live is not None and not bound_live():
            raise AccountError("cancelled")
        _save(root, d)
    return public(item)


def renew_from_isolated_slot(root, iid: str, *, expected_active, expected_switched, bound_live) -> dict | None:
    """Explicit owned renewal only. Different known FP returns None for ordinary registration.

    Keep the switch generation: identity/selection did not change. The caller must
    still check its binding after awaiting this operation; no other binding is adopted.
    No nested lock, OAuth parsing outside this boundary, or duplicate registration.
    """
    with _locked(root):
        d = load(root)
        def guard():
            current = load(root)["harness"]["codex"]
            return (callable(bound_live) and bound_live() and current.get("active") == expected_active
                    and current.get("switched") == expected_switched)
        if not guard():
            raise AccountError("cancelled")
        it = find(d, "codex", expected_active)
        if not isinstance(iid, str) or not ID_RE.fullmatch(iid) or find(d, "codex", iid):
            raise AccountError("not_found")
        fd = _open_dir(root, (DIR, "codex", iid), create=False)
        try:
            data = _read_at(fd, "auth.json")
        finally:
            os.close(fd)
        fp = codex_fp(data)
        if not fp:
            raise AccountError("slot_missing")
        unregistered = it is None
        if unregistered:
            # Ordinary single-account installs need not have opened Accounts.
            # Keep that registry absent/empty; use the isolated slot only as the
            # guarded temporary second copy through the existing switch writer.
            hd = d["harness"]["codex"]
            if expected_active is not None or hd["items"]:
                raise AccountError("not_found")
            native_fp = codex_fp(_codex_current(codex_home()))
            if not native_fp:
                raise AccountError("unsupported_store")
            it = {"id": iid, "name": "Codex", "fp": native_fp}
        if not it.get("fp"):
            raise AccountError("slot_missing")
        if fp != it["fp"]:
            return None
        result = _switch_codex(root, d, it, it, bound_live=guard, renewal_data=data)
        if not result.get("ok"):
            raise AccountError(result["why"])
        return {"ok": True, "renewed": True, "name": it["name"], "id": None if unregistered else it["id"]}


def on_rescue_token(root, tok) -> None:
    """A P258/P288 renewal of the token in use also refreshes the active Claude slot."""
    with _locked(root):
        d = load(root)
        hd = d["harness"]["claude"]
        it = next((i for i in hd["items"] if i["id"] == hd["active"]), None)
        if it is None or not TOKEN_RE.fullmatch(bytes(tok)):
            return
        fd = _open_dir(root, (DIR, "claude"))
        try:
            _write_at(fd, it["id"] + ".token", tok)
        finally:
            os.close(fd)
        it["method"], it["minted"] = "token", time.strftime("%Y-%m-%d")
        _save(root, d)


def on_codex_rescue(root) -> bool:
    """Only after our official login + successful live check. Refresh its matching registered slot.

    Ordinary switch never trusts a changed live JWT to select a different outgoing slot (F2).
    Unknown identities are not assigned to an existing account or automatically registered.
    """
    with _locked(root):
        d = load(root)
        hd = d["harness"]["codex"]
        if not hd["items"]:
            return False
        cur = _codex_current(codex_home())
        fp = codex_fp(cur)
        matches = [i for i in hd["items"] if fp and i.get("fp") == fp]
        if len(matches) != 1:
            return False
        it = matches[0]
        fd = _open_dir(root, (DIR, "codex", it["id"]), create=False)
        try:
            _write_at(fd, "auth.json", cur)
        finally:
            os.close(fd)
        hd["active"], hd["switched"] = it["id"], round(time.time(), 3)
        _save(root, d)
    return True


# ------------------------------------------------------------------ quota per account
def snapshot(root, h: str, since: float | None = None) -> dict | None:
    """Live weekly reading {pct, window, at} taken after `since`, else None."""
    from . import quota
    try:
        src = quota.claude_source(root) if h == "claude" else quota.codex_source()
    except Exception:  # noqa: BLE001
        return None
    at = src.get("source_at")
    if src.get("status") != "ok" or not isinstance(at, (int, float)) or (since and at < since):
        return None
    return {"pct": round(float(src["used_pct"]), 1), "window": "week", "at": int(at), "reset_at": src.get("reset_at")}


def public(it: dict, quota: dict | None = None) -> dict:
    return {"id": it["id"], "name": it["name"], "method": it["method"], "added": it.get("added", 0), "quota": quota}


def view(root) -> dict:
    """The phone's / CLI's list: no token, no path, no fingerprint."""
    d = load(root)
    out = {}
    from . import codex_reset_credits
    native = codex_reset_credits.read_accounts(root, d["harness"])
    for h in HARNESSES:
        hd = d["harness"][h]
        rows = []
        for it in hd["items"]:
            q = snapshot(root, h, hd.get("switched")) if it["id"] == hd["active"] else it.get("last_quota")
            row = public(it, q)
            if h == "codex":
                result = native.get(it["id"]) or {}
                row["reset_credits"] = result.get("reset_credits", codex_reset_credits.unknown())
                reading = result.get("reading")
                if reading:
                    row["quota"] = {"pct": reading["used_pct"], "window": "week", "at": reading["source_at"],
                                    "reset_at": reading["reset_at"], "source": "app_server"}
            rows.append(row)
        out[h] = {"active": hd["active"], "items": rows}
    try:
        from . import auth_rescue
        out["conflict"] = auth_rescue.conflicts() if d["harness"]["claude"]["items"] else []
    except Exception:  # noqa: BLE001
        out["conflict"] = []
    return out


def phone_view(host) -> dict:
    """#536: primary status has the key manager's single source; saved slots stay intact.

    keys' OAuth rows also describe API-key/token precedence. Copy only their public
    status fields, never key tails, source paths, fingerprints or enrollment material.
    Blocking login probes belong in the caller's worker thread. No migration/write.
    """
    from . import keys
    out = view(host.st.root)
    fields = ("type", "name", "set", "state", "use", "use_name", "use_src", "pending")
    out["primary"] = {row["name"]: {k: row[k] for k in fields if k in row}
                      for vendor in keys.scan(host).get("base", [])
                      for row in vendor.get("rows", [])
                      if row.get("type") == "oauth" and row.get("name") in HARNESSES}
    return out


# ------------------------------------------------------------------ switch / rename / remove (worker threads)
def switch_sync(root, h: str, ref) -> dict:
    """→ {ok, why?, conflict?, same?, from?, to?, name?}. Claude: the target's token into the service env file + this
    process's env (login = the key removed). Codex: the live auth.json back into its own slot, the target's in."""
    from . import auth_rescue
    with _locked(root):
        d = load(root)
        it = find(d, h, ref)
        if it is None:
            return {"ok": False, "why": "not_found"}
        hd = d["harness"][h]
        if hd["active"] == it["id"]:
            if h == "codex":
                cur = _codex_current(codex_home())
                if _codex_unsupported(codex_home(), cur is not None):
                    return {"ok": False, "why": "unsupported_store"}
                fp = codex_fp(cur)
                if not fp or fp != it.get("fp"):
                    return {"ok": False, "why": "unregistered_login"}
            return {"ok": True, "same": True, "to": it["id"], "name": it["name"]}
        old = next((i for i in hd["items"] if i["id"] == hd["active"]), None)
        snap = snapshot(root, h, hd.get("switched"))
        if h == "claude":
            res = _switch_claude(root, it)
        else:
            res = _switch_codex(root, d, it, old)
        if not res.get("ok"):
            return res
        if old is not None and snap:
            old["last_quota"] = snap
        hd["active"], hd["switched"] = it["id"], round(time.time(), 3)   # readings older than this belong to the old account
        _save(root, d)
        if h == "claude":
            with contextlib.suppress(Exception):
                from .state import State
                st = State(Path(root))
                meta = auth_rescue.read_meta(st)
                if it["method"] == "token" and it.get("minted"):
                    meta["claude"] = {"minted": it["minted"], "route": "setup_token"}   # P288 counts this account
                else:
                    meta.pop("claude", None)
                auth_rescue.write_meta(st, meta)
    return {"ok": True, "from": old["id"] if old else None, "to": it["id"], "name": it["name"]}


def _switch_claude(root, it: dict) -> dict:
    from . import auth_rescue, elevate, keys
    try:
        bad = auth_rescue.conflicts()
    except Exception:  # noqa: BLE001
        bad = []
    if bad:
        return {"ok": False, "why": "conflict", "conflict": bad}
    path = auth_rescue.service_env_path()
    if not path:
        return {"ok": False, "why": "io"}
    if it["method"] == "token":
        fd = _open_dir(root, (DIR, "claude"))
        try:
            tok = bytearray(_read_at(fd, it["id"] + ".token", 4096) or b"")
        finally:
            os.close(fd)
        try:
            if not TOKEN_RE.fullmatch(bytes(tok)):
                return {"ok": False, "why": "slot_missing"}
            try:
                Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                elevate.write_secret({"mode": "env", "path": path, "key": TOKEN_ENV}, tok)
            except (elevate.Refused, OSError):
                return {"ok": False, "why": "io"}
            os.environ[TOKEN_ENV] = tok.decode()
        finally:
            for i in range(len(tok)):
                tok[i] = 0
        return {"ok": True}
    # login: a token from environment.d / the user manager would still win
    if auth_rescue.effective_auth_env(process=False, service=False).get(TOKEN_ENV):
        return {"ok": False, "why": "conflict", "conflict": [TOKEN_ENV]}
    try:
        keys.delete_name(TOKEN_ENV, path)
    except (elevate.Refused, OSError):
        return {"ok": False, "why": "io"}
    os.environ.pop(TOKEN_ENV, None)
    return {"ok": True}


def _switch_codex(root, d: dict, it: dict, old: dict | None, *, bound_live=None, renewal_data=None) -> dict:
    home = codex_home()
    try:
        hfd = os.open(str(home), os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        hfd = os.open(str(home), os.O_RDONLY | os.O_DIRECTORY)
    try:
        cur = _read_at(hfd, "auth.json")
        if _codex_unsupported(home, cur is not None):
            return {"ok": False, "why": "unsupported_store"}
        def guard():
            if bound_live is not None and not bound_live():
                raise AccountError("cancelled")
        if renewal_data is not None:
            if not callable(bound_live):
                raise AccountError("cancelled")
            # Same account only, with both original copies present. Never save the
            # outgoing native copy over the newly renewed credential.
            fp = it.get("fp")
            if old is not it or not fp or codex_fp(cur) != fp or codex_fp(renewal_data) != fp:
                return {"ok": False, "why": "unregistered_login"}
            sfd = _open_dir(root, (DIR, "codex", it["id"]), create=False)
            try:
                previous = _read_at(sfd, "auth.json")
                if codex_fp(previous) != fp:
                    return {"ok": False, "why": "slot_missing"}
                guard()
                _write_at(hfd, "auth.json", renewal_data)
                written = os.stat("auth.json", dir_fd=hfd, follow_symlinks=False)
                version = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                try:
                    guard()
                    _write_at(sfd, "auth.json", renewal_data)
                except BaseException:
                    # Only compensate our own still-current write. Another writer's
                    # replacement (even identical bytes) is never rolled back.
                    try:
                        current = os.stat("auth.json", dir_fd=hfd, follow_symlinks=False)
                        ours = version(current) == version(written) and _read_at(hfd, "auth.json") == renewal_data
                    except OSError:
                        ours = False
                    if ours:
                        _write_at(hfd, "auth.json", cur)
                    raise
            finally:
                os.close(sfd)
            return {"ok": True}
        if cur:
            # F2: the rotated live copy goes back only into the outgoing slot, only when it is that account's
            fp, ofp = codex_fp(cur), (old or {}).get("fp")
            if old is None or fp != ofp:
                return {"ok": False, "why": "unregistered_login"}
            sfd = _open_dir(root, (DIR, "codex", old["id"]))
            try:
                guard()
                _write_at(sfd, "auth.json", cur)
            finally:
                os.close(sfd)
            cur = None
        sfd = _open_dir(root, (DIR, "codex", it["id"]), create=False)
        try:
            data = _read_at(sfd, "auth.json")
        finally:
            os.close(sfd)
        if not data:
            return {"ok": False, "why": "slot_missing"}
        guard()
        _write_at(hfd, "auth.json", data)
        data = None
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "why": "slot_missing"}
    finally:
        os.close(hfd)


def rename_sync(root, h: str, ref, new) -> dict:
    with _locked(root):
        d = load(root)
        it = find(d, h, ref)
        if it is None:
            return {"ok": False, "why": "not_found"}
        try:
            n = valid_name(new)
        except AccountError as e:
            return {"ok": False, "why": e.args[0]}
        if any(i is not it and i["name"].casefold() == n.casefold() for i in d["harness"][h]["items"]):
            return {"ok": False, "why": "name_taken"}
        old, it["name"] = it["name"], n
        _save(root, d)
    return {"ok": True, "id": it["id"], "name": n, "old": old}


def remove_sync(root, h: str, ref) -> dict:
    """Never the account in use; slot first, then the row."""
    with _locked(root):
        d = load(root)
        it = find(d, h, ref)
        if it is None:
            return {"ok": False, "why": "not_found"}
        hd = d["harness"][h]
        if hd["active"] == it["id"]:
            return {"ok": False, "why": "active"}
        with contextlib.suppress(OSError):
            discard_slot(root, h, it["id"])
        slot = Path(root) / DIR / h / (it["id"] + ".token" if h == "claude" else it["id"])
        try:
            os.lstat(slot)
            return {"ok": False, "why": "io"}      # F4: keep the row while its credential exists
        except FileNotFoundError:
            pass
        hd["items"] = [i for i in hd["items"] if i["id"] != it["id"]]
        _save(root, d)
    return {"ok": True, "id": it["id"], "name": it["name"]}


# ------------------------------------------------------------------ serve side (phone wire + heartbeat.sock)
WHY = {"zh": {"not_found": "没有这个账号", "conflict": "环境里有优先级更高的凭据：{n}（先去掉它）", "active": "这是正在用的账号，先切到别的再删",
              "slot_missing": "这个账号的凭据不见了，请删掉后重新登记", "unsupported_store": "Codex 把登录存在系统钥匙串里，多账号切换暂不支持",
              "unregistered_login": "Codex 当前登录与登记的在用账号不一致。先到 AI 登录救援重新登录已登记的账号，再切换",
              "bad_name": "名字 1–24 个字", "name_taken": "这个名字已经有了", "full": "每个 AI 最多 8 个账号",
              "same_account": "你刚登录的是已登记的 Codex 账号。请在授权网页切换到要新增的另一个 ChatGPT 账号，再添加", "io": "写入失败", "untrusted_turn": "这条消息不是主人发的，不能改账号",
              "denied": "主人没有同意", "serve_not_running": "Agent J 没在运行", "unsupported": "不支持", "unsafe": "账号文件夹不安全（符号链接 / 权限）"},
       "en": {"not_found": "no such account", "conflict": "a higher-priority credential is set: {n} (remove it first)",
              "active": "this account is in use — switch to another one first", "slot_missing": "this account's credential is gone; remove and add it again",
              "unsupported_store": "Codex keeps its login in the system keyring; switching accounts is not supported there",
              "unregistered_login": "The current Codex login differs from the registered account in use. Use AI login rescue to sign in to a saved account, then switch again",
              "bad_name": "names are 1–24 characters", "name_taken": "that name is taken", "full": "at most 8 accounts per AI",
              "same_account": "The ChatGPT account you just signed in to is already added. Switch to the other ChatGPT account on the authorization page, then add it again", "io": "could not write", "untrusted_turn": "not the owner's message: accounts unchanged",
              "denied": "the owner did not allow it", "serve_not_running": "Agent J is not running", "unsupported": "unsupported",
              "unsafe": "the accounts folder is unsafe (symlink / permissions)"}}


def why_text(res: dict, lang: str = "zh") -> str:
    t = WHY["en" if lang == "en" else "zh"]
    return t.get(res.get("why"), str(res.get("why"))).format(n=", ".join(res.get("conflict") or []))


def _log(host, h: str, op: str, device, name: str, status=None) -> None:
    with contextlib.suppress(Exception):
        host.st.log("account", harness=h, op=op, device=device, name_hash=name_hash(name), **({"status": status} if status else {}))


async def use(host, h: str, ref, device=None, untrusted=False) -> dict:
    """Then the main Agent restarts before its next message (P320; conversation kept)."""
    if h not in HARNESSES:
        return {"ok": False, "why": "unsupported"}
    try:
        res = await asyncio.to_thread(switch_sync, host.st.root, h, ref)
    except AccountError as e:
        res = {"ok": False, "why": e.args[0]}
    except OSError:
        res = {"ok": False, "why": "io"}
    if not res.get("ok"):
        with contextlib.suppress(Exception):
            host.st.log("account", harness=h, op="use", device=device, status="failed", reason=res.get("why"))
        return res
    if res.get("same"):
        return res
    _log(host, h, "use", device, res.get("name", ""), "untrusted" if untrusted else None)
    a = getattr(host, "agent", None)
    rescue = getattr(host, "auth_rescue", None)
    if h == "claude":                    # #462: into the main session itself, then proven in its own context
        from . import claude_live
        live = await claude_live.deliver(host, "auth_rescue")
        claude_live.tell(host, live)
        res.update(live)
    else:
        note = rescue._restart(h) if rescue is not None else None
        res["restart"] = note or ("next_turn" if a is not None and getattr(a, "kind", None) == h else "none")
    if rescue is not None:
        with contextlib.suppress(Exception):
            rescue.credential_stamp = None
            rescue.broadcast()
    return res


async def _guard(fn, *a) -> dict:
    try:
        return await asyncio.to_thread(fn, *a)
    except AccountError as e:
        return {"ok": False, "why": e.args[0]}
    except OSError:
        return {"ok": False, "why": "io"}


async def rename(host, h: str, ref, new, device=None) -> dict:
    res = await _guard(rename_sync, host.st.root, h, ref, new)
    if res.get("ok"):
        _log(host, h, "rename", device, res["name"])
    return res


async def remove(host, h: str, ref, device=None, confirm=None) -> dict:
    """`confirm`: async → bool (the owner's card)."""
    d = await asyncio.to_thread(load, host.st.root)
    it = find(d, h, ref)
    if it is None:
        return {"ok": False, "why": "not_found"}
    if d["harness"][h]["active"] == it["id"]:
        return {"ok": False, "why": "active"}
    if confirm is not None and not await confirm(h, it["name"]):
        return {"ok": False, "why": "denied"}
    res = await _guard(remove_sync, host.st.root, h, it["id"])
    if res.get("ok"):
        _log(host, h, "remove", device, res["name"])
    return res


def remove_summary(h: str, name: str, lang: str) -> str:
    if lang == "en":
        return (f"Remove the {LABEL[h]} account 「{name}」 from this Agent J host? Its stored sign-in is deleted with it; "
                "the account itself is not touched.")
    return f"从这台 Agent J 主机删除 {LABEL[h]} 账号「{name}」？它在本机存的登录凭据会一起删掉；账号本身不受影响。"


async def confirm_card(host, h: str, name: str) -> bool:
    lang = "en" if getattr(host, "lang", "zh") == "en" else "zh"
    try:
        res = await host.ask("AuthRescue", {"summary": remove_summary(h, name, lang)}, batch=False, always_ask=True, _host_card=True)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(res, dict) and res.get("behavior") == "allow"


USE_GAP = 60.0
READ_OPS = ("account_list", "account_use")      # contract: allowed in any turn (switching needs no Face ID)
WRITE_OPS = ("account_rename", "account_remove")  # refused in an untrusted turn (heartbeat.handle)


async def handle(host, req: dict) -> dict:
    """heartbeat.sock ops (`agentj account …`)."""
    op, h = req.get("op"), req.get("harness")
    if op == "account_list":
        return {"ok": True, "view": await asyncio.to_thread(view, host.st.root)}
    if h not in HARNESSES:
        return {"ok": False, "why": "unsupported"}
    if op == "account_use":                      # F5: from the socket at most once per harness per USE_GAP
        from . import provenance
        last = host.__dict__.setdefault("_acct_use_at", {})
        if time.monotonic() - last.get(h, -1e9) < USE_GAP:
            return {"ok": False, "why": "rate"}
        last[h] = time.monotonic()
        res = await use(host, h, req.get("ref"), device="cli",
                        untrusted=bool(provenance.untrusted_turn(getattr(host, "agent", None))))
    elif op == "account_rename":
        res = await rename(host, h, req.get("ref"), req.get("name"), device="cli")
    elif op == "account_remove":
        res = await remove(host, h, req.get("ref"), device="cli", confirm=lambda hh, n: confirm_card(host, hh, n))
    else:
        return {"ok": False, "why": "unknown_op"}
    return {**res, "view": await asyncio.to_thread(view, host.st.root)}


async def phone(host, s, t: str, obj: dict) -> dict:
    """acct_* from a paired, ready session → acct_res fields."""
    h, iid = obj.get("harness"), obj.get("id")
    if t == "acct_get":
        res = {"ok": True}
    elif h not in HARNESSES or not isinstance(iid, str) or not ID_RE.fullmatch(iid):
        res = {"ok": False, "why": "unsupported"}
    elif t == "acct_use":
        res = await use(host, h, iid, device=s.device)
    elif t == "acct_rename":
        res = await rename(host, h, iid, obj.get("name"), device=s.device)
    elif t == "acct_del":
        from . import provenance

        async def ok(hh, n):
            with provenance.paired_request(lambda: host._paired_live(s)):
                return await confirm_card(host, hh, n)
        res = await remove(host, h, iid, device=s.device, confirm=ok)
    else:
        res = {"ok": False, "why": "unsupported"}
    out = {"ok": bool(res.get("ok")), "view": await asyncio.to_thread(phone_view, host)}
    if res.get("why"):
        out["why"] = res["why"]
    if res.get("conflict"):
        out["conflict"] = res["conflict"]
    for k in ("restart", "verified", "live"):
        if res.get(k) is not None:
            out[k] = res[k]
    return out


# ------------------------------------------------------------------ CLI: agentj account list|add|use|rename|remove
def add_parser(sub) -> None:
    p = sub.add_parser("account", help="Claude Code、Codex 备用账号；主账号到「模型和 API Key」修改 / backup AI accounts; manage the primary in Models & API keys")
    s = p.add_subparsers(dest="acct_cmd", required=True)
    x = s.add_parser("list", help="列出账号（当前 / 额度）/ list accounts")
    x.add_argument("--json", action="store_true")
    x = s.add_parser("add", help="登记一个新账号（官方登录，不覆盖现有账号；需要在终端里）/ add one (official sign-in, in a terminal)")
    x.add_argument("harness", choices=["claude", "codex"])
    x.add_argument("name")
    x = s.add_parser("use", help="切到这个账号（主 Agent 下一条消息前重启，对话接着）/ switch to it")
    x.add_argument("harness", choices=["claude", "codex"])
    x.add_argument("ref", help="名字或 id / name or id")
    x = s.add_parser("rename", help="改名 / rename")
    x.add_argument("harness", choices=["claude", "codex"])
    x.add_argument("ref")
    x.add_argument("name")
    x = s.add_parser("remove", help="删除账号和它存的凭据（不能删正在用的）/ remove it and its stored sign-in")
    x.add_argument("harness", choices=["claude", "codex"])
    x.add_argument("ref")
    p.set_defaults(fn=cmd_account)


def _lang() -> str:
    with contextlib.suppress(Exception):
        from . import preferences
        return "en" if preferences.get(preferences.effective(), "appearance.language", "zh") == "en" else "zh"
    return "zh"


REMOVE_WAIT = 190      # F1: > the owner's card TTL (serve ASK_TTL 120 s) + 30 s
CALL_WAIT = 30


def _ask(req: dict, timeout: float) -> dict | None:
    """None only when no serve listens; a taken request with no answer = serve_timeout (F1: never offline then)."""
    from . import heartbeat
    for path in heartbeat.sock_paths():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                s.connect(path)
            except (FileNotFoundError, ConnectionRefusedError):
                continue
            except OSError:
                return {"ok": False, "why": "serve_timeout"}
            try:
                s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
                buf = b""
                while b"\n" not in buf and len(buf) < 1 << 22:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                return json.loads(buf.split(b"\n", 1)[0])
            except (OSError, ValueError):
                return {"ok": False, "why": "serve_timeout"}
    return None


def _call(req: dict) -> dict:
    """serve (heartbeat.sock) when it runs, else offline on the state dir."""
    res = _ask(req, REMOVE_WAIT if req.get("op") == "account_remove" else CALL_WAIT)
    if res is not None and res.get("why") != "unknown_op":
        return res
    from .state import State
    root = State().root
    op, h = req["op"], req.get("harness")
    if op == "account_list":
        return {"ok": True, "serve": False, "view": view(root)}
    try:
        if op == "account_use":
            res = switch_sync(root, h, req.get("ref"))
        elif op == "account_rename":
            res = rename_sync(root, h, req.get("ref"), req.get("name"))
        else:
            res = remove_sync(root, h, req.get("ref"))
    except AccountError as e:
        res = {"ok": False, "why": e.args[0]}
    except OSError:
        res = {"ok": False, "why": "io"}
    return {**res, "serve": False, "view": view(root)}


def render(v: dict, lang: str = "zh") -> str:
    en = lang == "en"
    lines = []
    for h in HARNESSES:
        hd = v.get(h) or {}
        if not hd.get("items"):
            lines.append(f"{LABEL[h]}: " + ("one sign-in (no accounts added)" if en else "只有一个登录（没有登记多个账号）"))
            continue
        lines.append(f"{LABEL[h]}:")
        for it in hd["items"]:
            cur = it["id"] == hd.get("active")
            q = it.get("quota")
            if q:
                when = time.strftime("%m-%d %H:%M", time.localtime(q["at"]))
                qt = (f"{q['pct']:.0f}% used ({when})" if cur else f"last read {q['pct']:.0f}% ({when})") if en else \
                     (f"已用 {q['pct']:.0f}%（{when}）" if cur else f"上次读到 {q['pct']:.0f}%（{when}）")
            else:
                qt = "—"
            tag = (" (in use)" if en else "（当前）") if cur else ""
            meth = {"token": "token", "login": "/login", "device": "ChatGPT"}[it["method"]]
            lines.append(f"  {'*' if cur else ' '} {it['name']}{tag} · {meth} · {qt} · id {it['id']}")
    if v.get("conflict"):
        lines.append(("conflict: " if en else "冲突：") + ", ".join(v["conflict"]))
    return "\n".join(lines)


def _terminal_only() -> str | None:
    """`add` needs the owner's own terminal."""
    from . import ctl_auth
    if not sys.stdin.isatty():
        return "no_tty"
    if ctl_auth.refusal(os.getpid(), -1, ()) is not None:
        return "agent"
    for pid in ctl_auth.ancestors(os.getpid())[1:]:    # F6: `env -i` does not hide an Agent / serve further up
        if ctl_auth._environ(pid) & set(ctl_auth.AGENT_ENV) or _is_serve(pid):
            return "agent"
    return None


def _is_serve(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return False
    return b"serve" in argv and any(a.endswith(b"agentj") or b"agentj" in a for a in argv[:3])


def _add_local(h: str, name: str, lang: str) -> dict:
    from . import auth_rescue
    from .state import State
    root = State().root
    n = check_new(root, h, name, lang)
    if h == "claude":
        import getpass
        flow = auth_rescue.ClaudeFlow("setup_token")
        try:
            url = flow.start()
            print(("Open this link, sign in with the NEW account, then paste the code it shows:\n" if lang == "en" else
                   "打开这个链接，用要新增的那个账号登录，然后把页面给的授权码粘贴到下面：\n") + url, flush=True)
            code = getpass.getpass("code: " if lang == "en" else "授权码：")
            tok = flow.submit(code)
            code = ""
        finally:
            flow.close()
        if tok is None:
            raise AccountError("rejected")
        try:
            print("checking…" if lang == "en" else "正在实测…", flush=True)
            if not auth_rescue.live_claude(tok):
                raise AccountError("verify")
            return add_claude_token(root, n, tok, lang)
        finally:
            for i in range(len(tok)):
                tok[i] = 0
    iid, slot = new_codex_slot(root)
    try:
        flow = auth_rescue.CodexFlow(home=slot)
        url, code, ttl = flow.start()
        print((f"Open {url} , sign in with the NEW ChatGPT account and enter the code {code} (valid {ttl // 60} min)" if lang == "en"
               else f"打开 {url} ，用要新增的 ChatGPT 账号登录，输入设备码 {code}（{ttl // 60} 分钟内有效）"), flush=True)
        r = flow.wait()
        if r != "ok":
            raise AccountError(r)
        hc = auth_rescue.health_check("codex", env_extra={"CODEX_HOME": str(slot)})
        if not hc.get("ok"):
            raise AccountError("verify_codex")
    except BaseException:
        discard_slot(root, "codex", iid)
        raise
    return commit_codex(root, iid, n, lang)


def cmd_account(a) -> None:
    lang = _lang()
    c = a.acct_cmd
    if c == "list":
        res = _call({"op": "account_list"})
        print(json.dumps(res.get("view"), ensure_ascii=False) if a.json else render(res.get("view") or {}, lang))
        return
    if c == "add":
        why = _terminal_only()
        if why:
            sys.exit(("`agentj account add` needs the owner's own terminal; on the phone: Settings → AI → add account" if lang == "en"
                      else "`agentj account add` 要在主人自己的终端里运行；手机上：设置 → AI → 添加账号") + f" ({why})")
        try:
            it = _add_local(a.harness, a.name, lang)
        except (AccountError, Exception) as e:  # noqa: BLE001 — a fixed reason word only, never flow output
            w = e.args[0] if isinstance(e, AccountError) or type(e).__name__ == "RescueError" else type(e).__name__
            sys.exit(("not added: " if lang == "en" else "没有登记：") + why_text({"why": w}, lang))
        print((f"added {LABEL[a.harness]} account 「{it['name']}」 (not in use yet: agentj account use {a.harness} {it['name']})"
               if lang == "en" else f"已登记 {LABEL[a.harness]} 账号「{it['name']}」（还没切过去：agentj account use {a.harness} {it['name']}）"))
        return
    req = {"op": {"use": "account_use", "rename": "account_rename", "remove": "account_remove"}[c], "harness": a.harness, "ref": a.ref}
    if c == "rename":
        req["name"] = a.name
    res = _call(req)
    if not res.get("ok"):
        sys.exit(("failed: " if lang == "en" else "没成功：") + why_text(res, lang))
    if c == "use":
        if res.get("same"):
            print(f"{LABEL[a.harness]}: " + ("already in use" if lang == "en" else "已经是这个账号"))
            return
        tail = {"owner_session": ("reopen your own terminal session to use it" if lang == "en" else "你自己终端里的会话要重开一次才用新账号"),
                "next_turn": ("the main Agent restarts before its next message, same conversation" if lang == "en"
                              else "主 Agent 在下一条消息前重启，对话接着"),
                "reopening": ("the main conversation reopens with it now, same conversation" if lang == "en" else "主对话正用新账号重开，对话接着"),
                "none": "", None: ("takes effect when Agent J starts" if lang == "en" else "Agent J 下次启动时生效")}
        if res.get("verified") is False:       # #462: never a silent success
            from .claude_live import text
            tail[res.get("restart")] = text(lang, "owner" if res.get("restart") == "owner_session" else "bad", res.get("live"))
        print((f"{LABEL[a.harness]} → 「{res.get('name')}」" + (f" · {tail.get(res.get('restart'), '')}" if tail.get(res.get("restart")) else "")
               + (" · running background tasks finish on the old account" if lang == "en" else " · 正在跑的后台任务用完旧账号为止")))
        return
    print(("done" if lang == "en" else "好了") + (f" · {res.get('name')}" if res.get("name") else ""))
