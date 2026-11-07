"""Read-only native Codex quota/reset-credit metadata. Never opens authentication files.

The native app-server owns login handling. Only fixed read methods are sent; raw
provider replies, stderr, email and authentication material never leave this module.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import select
import subprocess
import threading
import time

TIMEOUT = 4.0
MAX_REPLY = 256 * 1024
CACHE_SECONDS = 60
_LOCK = threading.Lock()
_CACHE: dict = {}


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def unknown(status="unavailable", why="not_exposed"):
    return {"status": status, "why": why, "count": None, "credits": [], "nearest_expires_at": None}


def normalize(raw, now):
    """Missing support is unknown; an explicit empty native credits list is zero."""
    box = raw.get("rateLimitResetCredits") if isinstance(raw, dict) else None
    if not isinstance(box, dict) or not isinstance(box.get("credits"), list):
        return unknown()
    credits, seen = [], set()
    for c in box["credits"]:
        if not isinstance(c, dict):
            return unknown("error", "invalid_response")
        if c.get("status") != "available" or c.get("resetType") != "codexRateLimits":
            continue
        iid, exp = c.get("id"), c.get("expiresAt")
        if not isinstance(iid, str) or not 1 <= len(iid) <= 256 or any(ord(x) < 32 for x in iid):
            return unknown("error", "invalid_response")
        if exp is not None and (not number(exp) or not 0 <= exp <= 253402300799):
            return unknown("error", "invalid_response")
        if iid in seen or exp is not None and exp <= now:
            continue
        seen.add(iid)
        credits.append({"id": iid, "expires_at": exp})
    credits.sort(key=lambda c: float("inf") if c["expires_at"] is None else c["expires_at"])
    return {"status": "ok", "count": len(credits), "credits": credits,
            "nearest_expires_at": next((c["expires_at"] for c in credits if c["expires_at"] is not None), None),
            "source_at": now}


def weekly(raw, now):
    buckets = raw.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    bucket = bucket if isinstance(bucket, dict) else raw.get("rateLimits")
    if not isinstance(bucket, dict):
        return None
    out = {}
    for key in ("primary", "secondary"):
        w = bucket.get(key)
        if not isinstance(w, dict):
            continue
        pct, reset = w.get("usedPercent"), w.get("resetsAt")
        if not number(pct) or not 0 <= pct <= 100 or not number(reset) or reset <= now:
            continue
        target = {10080: "week", 300: "h5"}.get(w.get("windowDurationMins"))
        if target:
            out[target] = {"used_pct": float(pct), "reset_at": reset, "source_at": now}
    if "week" not in out:
        return None
    return {"harness": "codex", "status": "ok", "source": "app_server", **out["week"],
            **({"h5": out["h5"]} if "h5" in out else {})}


class _Rpc:
    def __init__(self, home, env, timeout, binary):
        self.deadline = time.monotonic() + timeout
        child_env = dict(os.environ if env is None else env)
        child_env["CODEX_HOME"] = str(home)
        # Explicit home + file credential store prevents fallback to another account's keyring.
        self.p = subprocess.Popen([binary, "app-server", "--stdio", "-c", 'cli_auth_credentials_store="file"'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=child_env)
        self.buf, self.seq, self.total = b"", 0, 0

    def call(self, method, params):
        if method not in ("initialize", "account/read", "account/rateLimits/read"):
            raise ValueError("read_method_only")
        self.seq += 1
        self.p.stdin.write((json.dumps({"id": self.seq, "method": method, "params": params}) + "\n").encode())
        self.p.stdin.flush()
        while time.monotonic() < self.deadline:
            if b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise ValueError("invalid_response")
                if reply.get("id") != self.seq:
                    continue
                if "error" in reply:
                    raise ValueError("native_error")
                if not isinstance(reply.get("result"), dict):
                    raise ValueError("invalid_response")
                return reply["result"]
            if not select.select([self.p.stdout], [], [], max(0, self.deadline-time.monotonic()))[0]:
                break
            chunk = os.read(self.p.stdout.fileno(), 65536)
            self.total += len(chunk)
            if not chunk or self.total > MAX_REPLY:
                raise ValueError("invalid_response")
            self.buf += chunk
        raise TimeoutError()

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        for stream in (self.p.stdin, self.p.stdout):
            if stream:
                stream.close()


def probe(home, now=None, env=None, timeout=TIMEOUT):
    now = time.time() if now is None else now
    rpc = None
    try:
        home = Path(home)
        if not home.is_dir() or home.is_symlink():
            return {"reset_credits": unknown(why="missing_home")}
        from .binaries import resolve
        binary = resolve("codex", environ=env)["path"]
        if not binary:
            return {"reset_credits": unknown(why="missing_app_server")}
        rpc = _Rpc(home, env, timeout, binary)
        rpc.call("initialize", {"clientInfo": {"name": "agentj_quota", "version": "1"}})
        rpc.p.stdin.write(b'{"method":"initialized","params":{}}\n')
        rpc.p.stdin.flush()
        before = rpc.call("account/read", {"refreshToken": False}).get("account")
        if not isinstance(before, dict) or before.get("type") != "chatgpt":
            return {"reset_credits": unknown(why="unsupported_account")}
        raw = rpc.call("account/rateLimits/read", {})
        after = rpc.call("account/read", {"refreshToken": False}).get("account")
        if before != after:
            return {"reset_credits": unknown("error", "account_changed")}
        native_id = raw.get("accountId")
        if not isinstance(native_id, str) or not native_id:
            return {"reset_credits": unknown("error", "missing_account_id")}
        return {"reset_credits": normalize(raw, now), "reading": weekly(raw, now),
                "identity": hashlib.sha256(native_id.encode()).hexdigest()}
    except TimeoutError:
        return {"reset_credits": unknown("error", "timeout")}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"reset_credits": unknown("error", "native_read_failed")}
    finally:
        if rpc:
            rpc.close()


def read_accounts(root, registry, now=None, env=None):
    """Read every registered home, in a bounded pool, without switching the active login."""
    from .quota import codex_home
    from . import accounts
    now = time.time() if now is None else now
    hd = registry.get("codex") or {}
    binding = lambda d: (d.get("active"), d.get("switched"), tuple((i["id"], i.get("fp")) for i in d.get("items", [])))
    expected = binding(hd)
    items = hd.get("items") or []
    homes = {it["id"]: codex_home(env) if it["id"] == hd.get("active") else Path(root)/"accounts"/"codex"/it["id"] for it in items}
    def one(it):
        iid, home = it["id"], homes[it["id"]]
        # No credential bytes are opened. Stat is only a cache invalidation hint.
        try:
            stamp = (home/"auth.json").stat().st_mtime_ns
        except OSError:
            stamp = None
        if iid != hd.get("active") and home.resolve() != Path(root).resolve()/"accounts"/"codex"/iid:
            return iid, {"reset_credits": unknown("error", "unsafe_home")}
        key = (str(root), iid, str(home), it.get("fp"), stamp)
        with _LOCK:
            cached = _CACHE.get(key)
            if cached and 0 <= now-cached[0] < CACHE_SECONDS:
                result = copy.deepcopy(cached[1])
                box = result.get("reset_credits", {})
                if box.get("status") == "ok":
                    live = [c for c in box["credits"] if c["expires_at"] is None or c["expires_at"] > now]
                    box.update(credits=live, count=len(live), nearest_expires_at=next((c["expires_at"] for c in live if c["expires_at"] is not None), None))
                return iid, result
        result = probe(home, now, env)
        with _LOCK:
            if len(_CACHE) > 128:
                _CACHE.clear()
            _CACHE[key] = (now, copy.deepcopy(result))
        return iid, result
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
        out = dict(pool.map(one, items))
    # A slot reading the same provider account as another slot is not evidence
    # for two separately registered accounts; suppress ambiguous attribution.
    identities = [r.get("identity") for r in out.values() if r.get("identity")]
    for result in out.values():
        if result.get("identity") and identities.count(result["identity"]) > 1:
            result.clear()
            result["reset_credits"] = unknown("error", "account_mismatch")
    try:
        # Do not hold the account mutation lock across network IO. A switch or
        # re-enrollment during probing invalidates all attribution, even switch-back.
        with accounts._locked(root):
            current = accounts.load(root)["harness"]["codex"]
            if binding(current) != expected:
                return {iid: {"reset_credits": unknown("error", "registry_changed")} for iid in out}
            pin_identities(root, items, out)
    except (OSError, accounts.AccountError):
        return {iid: {"reset_credits": unknown("error", "identity_store_failed")} for iid in out}
    return out


def expiring(box, now):
    return [c for c in (box or {}).get("credits", []) if number(c.get("expires_at")) and 0 < c["expires_at"]-now <= 7*86400] if (box or {}).get("status") == "ok" else []


def reminder(account, credit, lang="zh"):
    exp = time.localtime(credit["expires_at"])
    date = time.strftime("%b %d", exp) if lang == "en" else f"{exp.tm_mon} 月 {exp.tm_mday} 日"
    name = account["name"]
    return (f"{name} has a reset credit expiring {date}. Use this account more heavily over the next few days, then use the credit to reset the quota the day before it expires."
            if lang == "en" else f"{name} 账号有一张重置券 {date}到期，这几天可以把它用猛一点，用完前一天用券重置")


def summary(box, lang="zh"):
    if box.get("status") != "ok":
        return "Reset credits unavailable" if lang == "en" else "重置券：暂时读不到"
    count, exp = box["count"], box.get("nearest_expires_at")
    if exp is None:
        return f"{count} reset credits" if lang == "en" else f"重置券 {count} 张"
    tm = time.localtime(exp)
    return (f"{count} reset credits; nearest expiry {time.strftime('%b %d', tm)}" if lang == "en" else
            f"重置券 {count} 张，最近一张 {tm.tm_mon} 月 {tm.tm_mday} 日到期")


@contextlib.contextmanager
def reminder_lock(root, filename="quota-reminders.lock"):
    """Serialize atomic reminder/identity state updates across CLI and serve threads."""
    import fcntl
    path = Path(root)/filename
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def pin_identities(root, items, results):
    """Trust first native read, then pin the non-secret identity to registry id+fp.

    Enrollment's legacy fingerprint cannot be reversed without reading login tokens;
    it is an invalidation binding, not evidence of a provider account ID on first use.
    """
    path = Path(root)/"codex-quota-identities.json"
    with reminder_lock(root, "codex-quota-identities.lock"):
        pins = {}
        try:
            if not path.is_symlink() and path.stat().st_size < 65536:
                pins = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        if not isinstance(pins, dict):
            pins = {}
        changed = False
        for item in items:
            result = results[item["id"]]
            native = result.get("identity")
            if not native:
                continue
            key = item["id"]+":"+str(item.get("fp") or "")
            if key in pins and pins[key] != native:
                result.clear()
                result["reset_credits"] = unknown("error", "account_mismatch")
            elif key not in pins:
                pins[key] = native
                changed = True
        if changed:
            from .state import _write_private
            _write_private(path, json.dumps(dict(list(pins.items())[-64:])).encode())


async def refresh_meter(host):
    """Both shared and owned Codex sessions expose the same registered active account."""
    import asyncio
    from . import accounts
    agent = getattr(host, "agent", None)
    if getattr(agent, "kind", None) != "codex":
        return
    registry = accounts.load(host.st.root)["harness"]
    active = (registry.get("codex") or {}).get("active")
    if not active:
        return
    result = await asyncio.to_thread(read_accounts, host.st.root, registry)
    if getattr(host, "agent", None) is not agent or (accounts.load(host.st.root)["harness"]["codex"].get("active")) != active:
        return
    host.meter_update(reset_credits=(result.get(active) or {}).get("reset_credits", unknown()))
