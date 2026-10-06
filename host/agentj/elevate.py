"""Admin password and secret cards on the phone (F17, PROTOCOL §11): `agentj sudo` and `agentj secret request`.

The Agent asks; a paired phone shows the exact command (or the key name, purpose and destination); the human types the
password / pastes the key into a masked field; the phone seals it to a one-time host key for THIS card and signs the
decision; `serve` (outside any fence, non-dumpable) opens it, uses it once and wipes it. The Agent only ever gets the
command's output, or a receipt (key name, length, a short fingerprint, whether the check passed).

Layers, inside the Noise session that already hides everything from the relay (PROTOCOL §3):
- **Seal**: per card the host makes an X25519 key pair (private half in memory only). The phone makes its own ephemeral
  pair, `k = HKDF-SHA256(DH, salt = host_epk ‖ phone_epk, info = "agentjarvis-seal-v1")`, AES-256-GCM, 12-byte IV,
  AAD = `"agentjarvis-seal-v1\\n" channel \\n device \\n id \\n kind \\n nonce \\n digest`. So the value is ciphertext even
  inside the decrypted app message, bound to this card, this device, this attempt (nonce) and what was shown (digest).
- **Signature**: Ed25519(device approval key, `"agentjarvis-elevate-v1\\n" channel \\n device \\n id \\n kind \\n
  allow|deny \\n nonce \\n ts \\n digest \\n hex(SHA-256(ct))|-`). The host checks it against what IT stored for the card.
- **One time**: a card is good for 120 s (sudo) / 300 s (secret) and one attempt per nonce; a wrong sudo password re-arms the
  card with a new nonce and a new host key (≤ 3 tries), and consecutive wrong passwords lock all sudo cards (1 min, doubling,
  ≤ 1 h, persisted in `elevate.json`).
- **Face ID** (P59, ADR-A163): a device whose record holds a passkey (F20) must add a WebAuthn assertion (user verified)
  over `passkey.elevate_challenge(card, nonce, digest)` to 「同意」; 「拒绝」 never needs one; devices without a passkey: unchanged.
- **Never**: the password is not in argv, the environment, a log, the Agent's output or the chat — it goes to `sudo -S -k`
  on stdin from a bytearray that is zeroed after the write. A secret is written 0600 and only its receipt goes back.
- **Audit**: `elevate.log` (0600), one line per outcome: time, kind, id, result, device, its key and signature, the digest
  of what was shown, SHA-256 of argv (sudo) / key name + SHA-256 of the destination (secret). Never a value.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import passkey, wire

SIG_CONTEXT = "agentjarvis-elevate-v1"
SEAL_CONTEXT = "agentjarvis-seal-v1"
KINDS = ("sudo", "secret")
TTL = {"sudo": 120, "secret": 300}
TRIES = 3                       # wrong sudo passwords per card before it ends
LOCK_AFTER = 3                  # consecutive wrong sudo passwords (any card) before the lock
LOCK_BASE, LOCK_MAX = 60, 3600
TS_SKEW_MS = 120_000
MAX_OPEN = 4
MAX_VALUE = 8192                # bytes of a password / secret
MAX_OUT = 256 * 1024            # bytes of stdout / stderr returned to the Agent (each)
MAX_FRAME = 64 * 1024           # one request line from the CLI
CMD_TIMEOUT, CMD_TIMEOUT_MAX = 600, 3600
SOCK_NAME = "elevate.sock"
PHONE_TYPES = ("elev_answer",)
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_ID = re.compile(r"[0-9a-f]{32}")


class Refused(Exception):
    """A request the host will not turn into a card (`why` is a short code, shown to the Agent)."""
    def __init__(self, why: str, detail: str = ""):
        super().__init__(why)
        self.why, self.detail = why, detail


# ---------------------------------------------------------------- bytes that must not linger
def wipe(buf) -> None:
    """Zero a bytearray in place; for an immutable bytes object (what AES-GCM returns) overwrite its buffer — best effort,
    CPython only; serve is non-dumpable (fence.no_dump), this only shortens how long the value sits in memory."""
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0
    elif isinstance(buf, bytes) and len(buf) > 1:
        with contextlib.suppress(Exception):
            ctypes.memset(id(buf) + sys.getsizeof(buf) - len(buf) - 1, 0, len(buf))


# ---------------------------------------------------------------- what the phone shows, signs and seals
def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def shown_fields(card: dict) -> list[str]:
    """The texts the phone displays, in a fixed order. sudo: cmd, why, effect. secret: name, purpose, dest, verify."""
    if card["kind"] == "sudo":
        return [card["cmd"], card["why"], card.get("effect") or ""]
    return [card["name"], card["purpose"], card["dest"], card.get("verify") or ""]


def shown_digest(kind: str, fields: list[str]) -> str:
    """hex SHA-256 of `kind` then one line per shown field `sha256(field)` — the phone hashes what it shows."""
    if kind not in KINDS:
        raise ValueError("bad kind")
    return _h("\n".join([SIG_CONTEXT, kind] + [_h(f) for f in fields]))


def signed_message(channel: str, device: str, rid: str, kind: str, decision: str, nonce: str, ts: int, digest: str,
                   ct_sha: str | None) -> bytes:
    if decision not in ("allow", "deny") or kind not in KINDS:
        raise ValueError("bad decision")
    return "\n".join([SIG_CONTEXT, channel, device, rid, kind, decision, nonce, str(ts), digest, ct_sha or "-"]).encode()


def seal_aad(channel: str, device: str, rid: str, kind: str, nonce: str, digest: str) -> bytes:
    return "\n".join([SEAL_CONTEXT, channel, device, rid, kind, nonce, digest]).encode()


def _raw_pub(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _key(shared: bytes, host_epk: bytes, phone_epk: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=host_epk + phone_epk, info=SEAL_CONTEXT.encode()).derive(shared)


def seal(host_epk: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """What the phone does (protocol/wire.js `sealValue`); here for tests and tools. → (phone_epk, iv ‖ ciphertext)."""
    e = X25519PrivateKey.generate()
    epk = _raw_pub(e)
    k = _key(e.exchange(X25519PublicKey.from_public_bytes(host_epk)), host_epk, epk)
    iv = os.urandom(12)
    return epk, iv + AESGCM(k).encrypt(iv, plaintext, aad)


def open_sealed(host_priv: X25519PrivateKey, phone_epk: bytes, blob: bytes, aad: bytes) -> bytearray:
    """→ the value as a bytearray (the caller wipes it). Raises ValueError for anything that does not open."""
    if len(phone_epk) != 32 or len(blob) < 12 + 16 + 1 or len(blob) > 12 + 16 + MAX_VALUE:
        raise ValueError("shape")
    host_epk = _raw_pub(host_priv)
    try:
        shared = host_priv.exchange(X25519PublicKey.from_public_bytes(phone_epk))
        k = _key(shared, host_epk, phone_epk)
        pt = AESGCM(k).decrypt(blob[:12], blob[12:], aad)
    except (InvalidTag, ValueError) as e:
        raise ValueError("seal") from e
    out = bytearray(pt)
    wipe(pt)
    return out


# ---------------------------------------------------------------- request validation (from the Agent's CLI)
def _text(v, limit: int, *, required: bool, field: str) -> str:
    if v is None or v == "":
        if required:
            raise Refused("shape", f"{field} is required")
        return ""
    if not isinstance(v, str) or wire.text_problem(v, limit):
        raise Refused("shape", f"{field}: text up to {limit} characters, no control characters")
    return v


def norm_sudo(req: dict) -> dict:
    argv = req.get("argv")
    if not isinstance(argv, list) or not 1 <= len(argv) <= 256 or \
            not all(isinstance(a, str) and a and "\x00" not in a and len(a) <= 4096 for a in argv):
        raise Refused("shape", "argv: 1–256 non-empty strings")
    cmd = shlex.join(argv)
    if len(cmd) > 2000 or wire.text_problem(cmd.replace("\n", " ").replace("\t", " "), 2000):
        raise Refused("shape", "the command is too long to show on a phone (≤ 2000 characters)")
    cwd = req.get("cwd") or "/"
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        cwd = "/"
    timeout = req.get("timeout", CMD_TIMEOUT)
    if type(timeout) is not int or not 1 <= timeout <= CMD_TIMEOUT_MAX:
        raise Refused("shape", f"timeout: 1–{CMD_TIMEOUT_MAX} s")
    return {"kind": "sudo", "argv": list(argv), "cmd": cmd, "cwd": cwd, "timeout": timeout,
            "why": _text(req.get("why"), 300, required=True, field="why"),
            "effect": _text(req.get("effect"), 300, required=False, field="effect")}


def parse_dest(dest: str, name: str, cwd: str | None, state_root: pathlib.Path) -> dict:
    """`env:<file>[#KEY]` (one KEY=value line, the rest of the file kept) · `file:<path>` (the whole file is the value) ·
    a bare path: a `.env`-style name → env, else file. Relative → against the Agent's working directory. Never inside the
    agentj state directory, never through a symlink, never someone else's file; the parent folder must exist."""
    if not isinstance(dest, str) or not dest or len(dest) > 1024 or "\x00" in dest or wire.text_problem(dest, 1024):
        raise Refused("dest", "dest: env:<file>[#KEY] or file:<path>")
    mode, rest = ("env", dest[4:]) if dest.startswith("env:") else ("file", dest[5:]) if dest.startswith("file:") else ("", dest)
    key = name
    if "#" in rest and mode != "file":
        rest, key = rest.rsplit("#", 1)
        if not NAME_RE.fullmatch(key):
            raise Refused("dest", "the #KEY part is not a variable name")
    path = pathlib.Path(os.path.expanduser(rest))
    if not path.is_absolute():
        if not cwd or not os.path.isabs(cwd):
            raise Refused("dest", "relative destination without a working directory")
        path = pathlib.Path(cwd) / path
    path = pathlib.Path(os.path.normpath(path))
    if not mode:
        mode = "env" if path.name == ".env" or path.name.startswith(".env.") or path.suffix == ".env" else "file"
    parent = path.parent
    try:
        real_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Refused("dest", "the destination folder does not exist") from None
    root = state_root.resolve()
    target = real_parent / path.name
    if target == root or root in target.parents or real_parent == root:
        raise Refused("dest", "never inside Agent J's own state directory")
    if path.is_symlink():
        raise Refused("dest", "the destination is a symlink")
    if path.exists():
        stt = path.lstat()
        if not path.is_file():
            raise Refused("dest", "the destination is not a regular file")
        if stt.st_uid != os.getuid():
            raise Refused("dest", "the destination belongs to another user")
    elif not os.access(real_parent, os.W_OK):
        raise Refused("dest", "the destination folder is not writable")
    return {"mode": mode, "path": str(target), "key": key,
            "show": (f"{target} ({key}=…)" if mode == "env" else str(target))}


def norm_secret(req: dict, state_root: pathlib.Path) -> dict:
    name = req.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise Refused("shape", "name: a variable name like ELEVENLABS_API_KEY")
    purpose = _text(req.get("purpose"), 300, required=True, field="purpose")
    cwd = req.get("cwd") if isinstance(req.get("cwd"), str) else None
    d = parse_dest(req.get("dest"), name, cwd, state_root)
    vu, vh, vc = req.get("verify_url") or "", req.get("verify_header") or "", req.get("verify_cmd") or ""
    if vu and vc:
        raise Refused("shape", "one check: --verify-url or --verify-cmd")
    verify = ""
    if vu:
        u = urllib.parse.urlsplit(vu if isinstance(vu, str) else "")
        local = u.hostname in ("127.0.0.1", "localhost", "::1")
        if not isinstance(vu, str) or len(vu) > 500 or wire.text_problem(vu, 500) or not u.hostname or \
                not (u.scheme == "https" or (u.scheme == "http" and local)):
            raise Refused("shape", "verify-url: an https:// address")
        vh = vh or "Authorization: Bearer {value}"
        if not isinstance(vh, str) or len(vh) > 200 or wire.text_problem(vh, 200) or vh.count("{value}") != 1 or ":" not in vh \
                or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", vh.split(":", 1)[0].strip()):
            raise Refused("shape", "verify-header: 'Name: …{value}…'")
        verify = f"GET {vu}  ({vh})"
    elif vc:
        if not isinstance(vc, str) or len(vc) > 500 or wire.text_problem(vc, 500):
            raise Refused("shape", "verify-cmd: one line up to 500 characters")
        verify = f"$ {vc}  (${name})"
    return {"kind": "secret", "name": name, "purpose": purpose, "dest": d["show"], "dest_spec": d, "cwd": cwd or "/",
            "verify": verify, "verify_url": vu or "", "verify_header": vh if vu else "", "verify_cmd": vc or ""}


# ---------------------------------------------------------------- running sudo
def sudo_bin() -> str | None:
    return os.environ.get("AGENTJ_SUDO_BIN") or shutil.which("sudo") or ("/usr/bin/sudo" if os.path.exists("/usr/bin/sudo") else None)


def _cap(b: bytes) -> tuple[str, bool]:
    return b[:MAX_OUT].decode("utf-8", "replace"), len(b) > MAX_OUT


def run_sudo(argv: list[str], password: bytearray, cwd: str, timeout: int, sudo: str | None = None) -> dict:
    """`sudo -S -k -p <random marker> -- argv`, the password + newline on stdin and nothing else; → {result, code, stdout,
    stderr, truncated}. result: done (the command ran; `code` is its exit status) · bad_password · timeout · no_sudo.
    A wrong password shows as a second prompt (sudo asks again after "Sorry, try again", then gets EOF)."""
    sudo = sudo or sudo_bin()
    if not sudo:
        return {"result": "no_sudo", "code": None, "stdout": "", "stderr": "", "truncated": False}
    marker = "[agentj-" + secrets.token_hex(8) + "]"
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LOGNAME", "TZ", "TMPDIR")}
    env.update(LC_ALL="C", LANG="C")
    buf = bytearray(len(password) + 1)
    buf[:len(password)] = password
    buf[-1] = 0x0A
    try:
        p = subprocess.Popen([sudo, "-S", "-k", "-p", marker, "--", *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=cwd, env=env, start_new_session=True)
    except OSError:
        wipe(buf)
        return {"result": "no_sudo", "code": None, "stdout": "", "stderr": "", "truncated": False}
    try:
        try:
            view = memoryview(buf)
            off = 0
            while off < len(buf):
                off += os.write(p.stdin.fileno(), view[off:])
            view.release()
        except OSError:
            pass                     # sudo did not need it (NOPASSWD) and exited, or closed stdin
        finally:
            wipe(buf)
            with contextlib.suppress(OSError):
                p.stdin.close()
            p.stdin = None           # Python 3.11's communicate() flushes a closed stdin ("flush of closed file"); 3.12+ tolerates it
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(p.pid, 15)
            out, err = p.communicate()
            o, t1 = _cap(out)
            e, t2 = _cap(err.replace(marker.encode(), b""))
            return {"result": "timeout", "code": None, "stdout": o, "stderr": e, "truncated": t1 or t2}
    finally:
        wipe(buf)
    prompts = err.count(marker.encode())
    err = err.replace(marker.encode(), b"")
    bad = p.returncode != 0 and (prompts >= 2 or (not out and re.search(
        rb"incorrect password attempt|Sorry, try again|no password was provided|a password is required", err) is not None))
    if bad:
        return {"result": "bad_password", "code": p.returncode, "stdout": "", "stderr": "", "truncated": False}
    o, t1 = _cap(out)
    e, t2 = _cap(err)
    return {"result": "done", "code": p.returncode, "stdout": o, "stderr": e, "truncated": t1 or t2}


# ---------------------------------------------------------------- writing a secret
def _env_value(v: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+=,-]*", v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def write_secret(spec: dict, value: bytearray) -> None:
    """Write 0600 through a temp file + rename in the same folder. env: replace every `KEY=` / `export KEY=` line (or append
    one), keep everything else byte for byte. The value never goes anywhere else."""
    path = pathlib.Path(spec["path"])
    if path.is_symlink():
        raise Refused("dest", "the destination became a symlink")
    try:
        sval = value.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("value", "not UTF-8 text") from None
    if not sval or "\x00" in sval or (spec["mode"] == "env" and ("\n" in sval or "\r" in sval)):
        raise Refused("value", "empty, or a line break / NUL in the value")
    if spec["mode"] == "env":
        try:
            old = path.read_text("utf-8")
        except FileNotFoundError:
            old = ""
        pat = re.compile(r"^(\s*(?:export\s+)?)" + re.escape(spec["key"]) + r"\s*=.*$")
        lines, done = [], False
        for ln in old.splitlines():
            m = pat.match(ln)
            if m:
                if not done:
                    lines.append(f"{m.group(1)}{spec['key']}={_env_value(sval)}")
                    done = True
                continue
            lines.append(ln)
        if not done:
            lines.append(f"{spec['key']}={_env_value(sval)}")
        data = ("\n".join(lines) + "\n").encode()
    else:
        data = sval.encode()
    tmp = path.with_name("." + path.name + ".agentj-" + secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    finally:
        sval = None  # noqa: F841 — drop the reference; str cannot be wiped
    os.chmod(path, 0o600)


def fingerprint(value: bytearray) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()[:8]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # the key must not follow a redirect to another host
        return None


def verify_secret(card: dict, value: bytearray) -> dict:
    """Run the check the human saw on the card; → {"verify": ok|fail|skipped, "detail": "HTTP 200" | "exit 1" | …}.
    Nothing it prints or returns goes back but that."""
    sval = value.decode("utf-8", "replace")
    try:
        if card.get("verify_url"):
            name, tmpl = card["verify_header"].split(":", 1)
            req = urllib.request.Request(card["verify_url"], method="GET",
                                         headers={name.strip(): tmpl.strip().replace("{value}", sval), "User-Agent": "agentj"})
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                with opener.open(req, timeout=15) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            except (urllib.error.URLError, OSError, ValueError):
                return {"verify": "fail", "detail": "network"}
            return {"verify": "ok" if 200 <= code < 300 else "fail", "detail": f"HTTP {code}"}
        if card.get("verify_cmd"):
            env = dict(os.environ)
            env[card["name"]] = sval
            try:
                r = subprocess.run(["/bin/sh", "-c", card["verify_cmd"]], cwd=card.get("cwd") or "/", env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            except subprocess.TimeoutExpired:
                return {"verify": "fail", "detail": "timeout"}
            except OSError:
                return {"verify": "fail", "detail": "error"}
            return {"verify": "ok" if r.returncode == 0 else "fail", "detail": f"exit {r.returncode}"}
        return {"verify": "skipped", "detail": ""}
    finally:
        sval = None  # noqa: F841


# ---------------------------------------------------------------- lock ledger (elevate.json) and audit log (elevate.log)
def ledger_path(st) -> pathlib.Path:
    return st.root / "elevate.json"


def log_path(st) -> pathlib.Path:
    return st.root / "elevate.log"


def read_ledger(st) -> dict:
    try:
        d = json.loads(ledger_path(st).read_text())
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return {"fails": 0, "level": 0, "until": 0}
    except OSError:
        return {"fails": LOCK_AFTER, "level": 1, "until": time.time() + LOCK_MAX}   # unreadable reads as locked
    num = lambda k: d.get(k) if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool) else 0  # noqa: E731
    return {"fails": int(num("fails")), "level": int(num("level")), "until": float(num("until"))}


def locked_for(st, now: float | None = None) -> int:
    left = read_ledger(st)["until"] - (time.time() if now is None else now)
    return max(0, int(left + 0.999))


def note_password(st, ok: bool) -> int:
    """Record a sudo attempt; → seconds locked (0 = not locked)."""
    d = read_ledger(st)
    if ok:
        d = {"fails": 0, "level": max(0, d["level"] - 1) if d["until"] < time.time() else d["level"], "until": d["until"]}
    else:
        d["fails"] += 1
        if d["fails"] >= LOCK_AFTER:
            secs = min(LOCK_MAX, LOCK_BASE * (2 ** d["level"]))
            d = {"fails": 0, "level": d["level"] + 1, "until": time.time() + secs}
    st.write_private(ledger_path(st), json.dumps(d).encode())
    return locked_for(st)


def audit(st, card: dict | None, **kw) -> dict:
    rec = {"ts": int(time.time())}
    if card:
        rec.update(kind=card["kind"], id=card["id"], shown_sha256=card["digest"])
        if card["kind"] == "sudo":
            rec["argv_sha256"] = hashlib.sha256(json.dumps(card["argv"], ensure_ascii=False).encode()).hexdigest()
        else:
            rec["name"] = card["name"]
            rec["dest_sha256"] = _h(card["dest_spec"]["path"])
    rec.update({k: v for k, v in kw.items() if v is not None})
    st.append_private(log_path(st), json.dumps(rec, ensure_ascii=False))
    return rec


def read_log(st) -> list[dict]:
    try:
        lines = log_path(st).read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            r = json.loads(ln)
            if isinstance(r, dict):
                out.append(r)
    return out


def check_record(st, r: dict) -> str:
    """'ok' / 'ok_removed' (signature verifies; device still / no longer paired) · 'bad' · 'unsigned' (host-side outcome)."""
    if not r.get("sig"):
        return "unsigned"
    try:
        sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r["sig"])
        msg = signed_message(r["channel"], r["device"], r["id"], r["kind"], r["decision"], r["n"], r["sig_ts"],
                             r["shown_sha256"], r.get("ct_sha256"))
        Ed25519PublicKey.from_public_bytes(sk).verify(sig, msg)
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return "bad"
    cur = st.sign_key(str(r.get("device")))
    return "ok_removed" if cur is None else ("ok" if cur == sk else "bad")


# ---------------------------------------------------------------- the serve side
class Elevator:
    """Owned by serve.Host: the Agent-facing socket `<state>/agentperm/elevate.sock` (the only part of the state directory
    a fenced Agent sees; same-user peers only) and the phone cards."""

    def __init__(self, host):
        self.host = host
        self.st = host.st
        self.cards: dict[str, dict] = {}
        self.server = None
        self.sudo = None             # tests: a stand-in sudo; else AGENTJ_SUDO_BIN / PATH
        self.helper_status = None    # tests: a stand-in for elevate_helper.status()
        self.helper_cmd = None       # tests: a stand-in for the installed helper command line

    @property
    def sock_path(self) -> pathlib.Path:
        return self.st.perm_dir / SOCK_NAME

    async def start(self) -> None:
        self.st.perm_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.st.perm_dir, 0o700)
        with contextlib.suppress(FileNotFoundError):
            self.sock_path.unlink()
        old = os.umask(0o077)
        try:
            self.server = await asyncio.start_unix_server(self.on_client, path=str(self.sock_path), limit=MAX_FRAME)
        finally:
            os.umask(old)
        os.chmod(self.sock_path, 0o600)

    async def stop(self) -> None:
        self.cancel_all("gone")
        if self.server:
            self.server.close()
            with contextlib.suppress(FileNotFoundError):
                self.sock_path.unlink()

    # ---------------------------------------------------------- phone messages
    def helper_devices(self) -> list[str]:
        """Paired phones the installed admin helper accepts for THIS host (empty = every card needs the password)."""
        try:
            from . import elevate_helper
            h = self.helper_status() if self.helper_status else elevate_helper.status()
        except Exception:  # noqa: BLE001 — no helper is the normal case
            return []
        if not h.get("installed") or h.get("channel") != self.host.channel:
            return []
        return sorted(d for d in h.get("devices") or [] if self.st.sign_key(d))

    def card_msg(self, c: dict, s=None) -> dict:
        m = {"t": "elev", "id": c["id"], "kind": c["kind"], "n": c["nonce"], "epk": wire.b64u(c["epk"]),
             "ttl": max(0, int(c["deadline"] - time.monotonic())), "tries": TRIES - c["fails"]}
        if c["fails"]:
            m["bad"] = c["fails"]
        if c["kind"] == "sudo":
            m.update(cmd=c["cmd"], why=c["why"], effect=c.get("effect") or "")
            if c.get("helper"):
                m["helper"] = list(c["helper"])       # these phones may approve without a password (elevate_helper)
        else:
            m.update(name=c["name"], purpose=c["purpose"], dest=c["dest"], verify=c.get("verify") or "")
        pk = self.st.passkey_of_device(s.device) if s is not None and s.device else None
        if pk:
            m["fa"] = pk["id"]   # ADR-A163: this device saved a passkey → 「同意」 needs it (its own credential id only)
        return m

    async def _to_phones(self, obj_fn) -> None:
        await self.host._send_ready(obj_fn)

    def _arm(self, c: dict) -> None:
        """A fresh one-time nonce and host key (first show, and after a wrong password)."""
        old = c.get("priv")
        c["priv"] = X25519PrivateKey.generate()
        c["epk"] = _raw_pub(c["priv"])
        c["nonce"] = secrets.token_hex(16)
        c["deadline"] = time.monotonic() + TTL[c["kind"]]
        del old

    async def on_ready(self, s) -> None:
        for c in list(self.cards.values()):
            if not c["fut"].done() and not c.get("busy"):
                await self.host.send_app(s, self.card_msg(c, s))

    def _finish(self, c: dict, result: dict) -> None:
        if not c["fut"].done():
            c["fut"].set_result(result)

    def cancel_all(self, result: str) -> None:
        for c in list(self.cards.values()):
            if not c.get("busy"):
                self._finish(c, {"result": result})

    async def request(self, card: dict, gone: asyncio.Future | None = None) -> dict:
        """Show a card, wait for a decision, act; → the result for the Agent (never the value)."""
        if self.host.stopped():
            return {"result": "stopped"}
        if len([c for c in self.cards.values() if not c["fut"].done()]) >= MAX_OPEN:
            return {"result": "busy"}
        if card["kind"] == "sudo" and (left := locked_for(self.st)):
            audit(self.st, None, kind="sudo", result="locked", reason="locked")
            return {"result": "locked", "secs": left}
        if not self.host._approvers():
            return {"result": "no_device"}
        c = dict(card, id=secrets.token_hex(16), fails=0, fut=asyncio.get_running_loop().create_future())
        if c["kind"] == "sudo":
            c["helper"] = self.helper_devices()
        c["digest"] = shown_digest(c["kind"], shown_fields(c))
        self._arm(c)
        self.cards[c["id"]] = c
        self.st.log("elev_card", id=c["id"], kind=c["kind"])
        self.host.push_notify("ask")
        await self._to_phones(lambda s: self.card_msg(c, s))
        try:
            while True:
                waits = [c["fut"]] + ([gone] if gone is not None else [])
                left = c["deadline"] - time.monotonic()
                await asyncio.wait(waits, timeout=min(1.0, max(0.0, left)), return_when=asyncio.FIRST_COMPLETED)
                if c["fut"].done():
                    res = c["fut"].result()
                    if res.get("result") == "retry":       # wrong password: re-armed, shown again
                        c["fut"] = asyncio.get_running_loop().create_future()
                        await self._to_phones(lambda s: self.card_msg(c, s))
                        continue
                    break
                if gone is not None and gone.done() and not c.get("busy"):
                    res = {"result": "gone"}
                    break
                if time.monotonic() >= c["deadline"] and not c.get("busy"):
                    res = {"result": "timeout"}
                    break
                if c.get("busy"):                          # running: the deadline no longer applies
                    res = await c["fut"]
                    if res.get("result") == "retry":
                        c["fut"] = asyncio.get_running_loop().create_future()
                        await self._to_phones(lambda s: self.card_msg(c, s))
                        continue
                    break
        finally:
            self.cards.pop(c["id"], None)
            c.pop("priv", None)
        if not res.get("_logged"):
            audit(self.st, c, result=res["result"], reason=res["result"], channel=self.host.channel)
        res.pop("_logged", None)
        done_msg = {"t": "elev_done", "id": c["id"], "result": res["result"]}
        if c["kind"] == "sudo" and res.get("code") is not None:
            done_msg["code"] = res["code"]
        if c["kind"] == "secret" and res.get("receipt"):
            done_msg["verify"] = res["receipt"]["verify"]
        await self._to_phones(lambda s: done_msg)
        self.st.log("elev_done", id=c["id"], kind=c["kind"], result=res["result"])
        return res

    async def on_phone(self, s, obj: dict) -> None:
        rid = obj.get("id")
        c = self.cards.get(rid) if isinstance(rid, str) and _ID.fullmatch(rid) else None
        if c is None or c["fut"].done() or c.get("busy"):
            self.st.log("elev_refused", id=rid if isinstance(rid, str) else None, device=s.device, reason="unknown")
            return
        why = self._check(s, c, obj)
        if why:
            self.st.log("elev_refused", id=rid, device=s.device, reason=why)
            audit(self.st, c, result="refused", reason=why, device=s.device, channel=self.host.channel)
            if why.startswith("passkey"):          # ADR-A163: tell this phone, so its card leaves "sending" and stays open
                await self.host.send_app(s, {"t": "elev_refused", "id": c["id"], "why": "passkey"})
            return
        sk = self.st.sign_key(s.device)
        ok = obj["ok"]
        ct_sha = hashlib.sha256(wire.unb64u(obj["ct"])).hexdigest() if ok and "ct" in obj else None
        signed = dict(device=s.device, sk=wire.b64u(sk), sig=obj["sig"], n=obj["n"], sig_ts=obj["ts"],
                      decision="allow" if ok else "deny", ct_sha256=ct_sha, channel=self.host.channel,
                      passkey="uv" if ok and self.st.passkey_of_device(s.device) else None)
        if not ok:
            audit(self.st, c, result="denied", reason="device", **signed)
            return self._finish(c, {"result": "denied", "_logged": True})
        c["busy"] = True
        nonce, priv = c["nonce"], c["priv"]
        c["nonce"] = None                          # one attempt per nonce: a replay of this answer finds no match
        if "ct" not in obj:                        # approve-only: the admin helper checks the same signature as root
            from . import elevate_helper
            req = {"channel": self.host.channel, "device": s.device, "id": c["id"], "n": nonce, "ts": obj["ts"], "sig": obj["sig"],
                   "argv": c["argv"], "why": c["why"], "effect": c.get("effect") or "", "cwd": c["cwd"]}
            res = await asyncio.to_thread(elevate_helper.run, req, c["timeout"], self.sudo, self.helper_cmd)
            audit(self.st, c, result=res["result"], reason="helper", code=res.get("code"), **signed)
            res["_logged"] = True
            c["busy"] = False
            return self._finish(c, res)
        aad = seal_aad(self.host.channel, s.device, c["id"], c["kind"], nonce, c["digest"])
        try:
            value = open_sealed(priv, wire.unb64u(obj["epk"]), wire.unb64u(obj["ct"]), aad)
        except ValueError:
            c["busy"] = False
            audit(self.st, c, result="refused", reason="seal", **signed)
            return self._finish(c, {"result": "failed", "why": "seal", "_logged": True})
        try:
            if c["kind"] == "sudo":
                res = await asyncio.to_thread(run_sudo, c["argv"], value, c["cwd"], c["timeout"], self.sudo)
            else:
                res = await asyncio.to_thread(self._save, c, value)
        finally:
            wipe(value)
        if res["result"] == "bad_password":
            c["fails"] += 1
            lock = note_password(self.st, False)
            audit(self.st, c, result="bad_password", reason="device", **signed)
            c["busy"] = False
            if lock:
                return self._finish(c, {"result": "locked", "secs": lock, "_logged": True})
            if c["fails"] >= TRIES:
                return self._finish(c, {"result": "bad_password", "_logged": True})
            self._arm(c)
            return self._finish(c, {"result": "retry"})
        if c["kind"] == "sudo" and res["result"] == "done":
            note_password(self.st, True)
        audit(self.st, c, result=res["result"], reason="device", code=res.get("code"),
              verify=(res.get("receipt") or {}).get("verify"), **signed)
        res["_logged"] = True
        c["busy"] = False
        self._finish(c, res)

    def _check(self, s, c: dict, obj: dict) -> str | None:
        ok, n, ts, sig = obj.get("ok"), obj.get("n"), obj.get("ts"), obj.get("sig")
        if not isinstance(ok, bool) or not isinstance(n, str) or not isinstance(sig, str) or len(sig) > 100 \
                or type(ts) is not int:
            return "shape"
        if ok and "ct" not in obj and "epk" not in obj:
            if c["kind"] != "sudo" or s.device not in (c.get("helper") or []):
                return "shape"                      # approve-only exists only for a phone the admin helper accepts
        elif ok and (not isinstance(obj.get("epk"), str) or not isinstance(obj.get("ct"), str) or len(obj["ct"]) > 12000):
            return "shape"
        if time.monotonic() > c["deadline"]:
            return "late"
        if abs(time.time() * 1000 - ts) > TS_SKEW_MS:
            return "stale"
        if n != c["nonce"]:
            return "replay"
        sk = self.st.sign_key(s.device)
        if not sk:
            return "no_key"
        try:
            sigb = wire.unb64u(sig)
            ct_sha = hashlib.sha256(wire.unb64u(obj["ct"])).hexdigest() if ok and "ct" in obj else None
            if ok and "ct" in obj:
                wire.unb64u(obj["epk"])
            msg = signed_message(self.host.channel, s.device, c["id"], c["kind"], "allow" if ok else "deny", n, ts,
                                 c["digest"], ct_sha)
            Ed25519PublicKey.from_public_bytes(sk).verify(sigb, msg)
        except (InvalidSignature, ValueError):
            return "bad_signature"
        return self._check_passkey(s, c, obj) if ok else None   # 拒绝 never needs Face ID

    def _check_passkey(self, s, c: dict, obj: dict) -> str | None:
        """ADR-A163: a device whose record holds a passkey (F20) approves only with a fresh WebAuthn assertion (UP + UV)
        over elevate_challenge(this card, this nonce, what was shown). A device without one: unchanged."""
        pk = self.st.passkey_of_device(s.device)
        if not pk:
            return None
        if "fa" not in obj:
            return "passkey_missing"
        try:
            f = passkey.approval_fields(obj["fa"])
            if f["id"] != pk["id"]:
                raise passkey.PasskeyError("bad", "credential")
            ch = passkey.elevate_challenge(self.host.channel, s.device, c["id"], c["kind"], obj["n"], c["digest"])
            count = passkey.verify_assertion(pk, f, ch)
        except (passkey.PasskeyError, ValueError) as e:
            self.st.log("elev_passkey", id=c["id"], device=s.device, reason=getattr(e, "detail", "bad"))
            return "passkey_bad"
        self.st.passkey_count(s.device, pk["id"], count)
        return None

    def _save(self, c: dict, value: bytearray) -> dict:
        if len(value) > MAX_VALUE:
            return {"result": "failed", "why": "value"}
        try:
            write_secret(c["dest_spec"], value)
        except Refused as e:
            return {"result": "failed", "why": e.why, "detail": e.detail}
        except OSError:
            return {"result": "failed", "why": "io"}
        receipt = {"name": c["name"], "dest": c["dest"], "length": len(value), "fingerprint": fingerprint(value)}
        receipt.update(verify_secret(c, value))
        return {"result": "saved", "receipt": receipt}

    # ---------------------------------------------------------- the Agent's socket
    async def on_client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        gone = asyncio.get_running_loop().create_future()
        watch = None
        try:
            sock = w.get_extra_info("socket")
            if sock is not None and hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    return
            line = await asyncio.wait_for(r.readline(), 10)
            req = json.loads(line)
            if not isinstance(req, dict) or req.get("t") not in KINDS:
                raise Refused("shape", "t: sudo | secret")
            card = norm_sudo(req) if req["t"] == "sudo" else norm_secret(req, self.st.root)

            async def eof():
                with contextlib.suppress(Exception):
                    await r.read()
                if not gone.done():
                    gone.set_result(True)
            watch = asyncio.create_task(eof())
            res = await self.request(card, gone)
        except Refused as e:
            res = {"result": "refused", "why": e.why, "detail": e.detail}
        except (ValueError, asyncio.TimeoutError, asyncio.LimitOverrunError):
            res = {"result": "refused", "why": "shape", "detail": "one JSON line"}
        except (OSError, ConnectionError):
            return
        finally:
            if watch:
                watch.cancel()
        with contextlib.suppress(OSError, ConnectionError):
            w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
            await w.drain()
        w.close()


# ---------------------------------------------------------------- the CLI side (runs as the Agent)
def client_request(st, req: dict, timeout: float = CMD_TIMEOUT_MAX + 400) -> dict:
    path = st.perm_dir / SOCK_NAME
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except OSError:
            return {"result": "unavailable"}
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            try:
                chunk = s.recv(65536)
            except (socket.timeout, OSError):
                return {"result": "unavailable"}
            if not chunk:
                return {"result": "unavailable"}
            buf += chunk
        try:
            return json.loads(buf.split(b"\n", 1)[0])
        except ValueError:
            return {"result": "unavailable"}


MSG = {
    "denied": "手机上拒绝了。/ Declined on the phone.",
    "timeout": "手机上没人处理，卡片已过期。/ Nobody answered on the phone; the card expired.",
    "gone": "卡片已撤回。/ The card was withdrawn.",
    "stopped": "已急停，没有发卡片。/ Agent J is stopped; no card was sent.",
    "locked": "管理员密码连续输错，已暂时锁定 {secs} 秒。/ Too many wrong passwords; locked for {secs} s.",
    "bad_password": "管理员密码错了 3 次，这张卡已结束。/ Wrong password three times; this card ended.",
    "no_device": "没有能批准的已配对手机。/ No paired phone that can approve.",
    "busy": "已经有太多张卡在等手机处理。/ Too many cards are already waiting on the phone.",
    "unavailable": "Agent J 没在运行（agentj service status）。/ Agent J is not running.",
    "no_sudo": "这台电脑上没有 sudo。/ No sudo on this computer.",
    "failed": "没有完成：{why}。/ Not done: {why}.",
    "refused": "请求不合格：{detail}。/ Request refused: {detail}.",
}
EXIT_CARD = 125


def _say(res: dict) -> str:
    return MSG.get(res.get("result"), str(res.get("result"))).format(
        secs=res.get("secs", ""), why=res.get("why", ""), detail=res.get("detail") or res.get("why") or "")


def cmd_sudo(a) -> int:
    from .state import State
    argv = list(a.command)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("用法 / usage: agentj sudo --why '…' [--effect '…'] -- <command> [args…]", file=sys.stderr)
        return 2
    res = client_request(State(), {"t": "sudo", "argv": argv, "why": a.why, "effect": a.effect or "",
                                   "cwd": os.getcwd(), "timeout": a.timeout})
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("result") == "done" and res.get("code") == 0 else (res.get("code") or EXIT_CARD)
    if res.get("result") == "done":
        sys.stdout.write(res.get("stdout", ""))
        sys.stderr.write(res.get("stderr", ""))
        if res.get("truncated"):
            print(f"\n[agentj sudo: output cut at {MAX_OUT} bytes]", file=sys.stderr)
        return int(res.get("code") or 0)
    if res.get("result") == "timeout" and "stdout" in res:
        sys.stdout.write(res.get("stdout", ""))
        print("SUDO_RESULT: command_timeout", file=sys.stderr)
        return 124
    print(f"SUDO_RESULT: {res.get('result')} — {_say(res)}", file=sys.stderr)
    return EXIT_CARD


def cmd_secret(a) -> int:
    from .state import State
    if a.action == "log":
        return cmd_log(a)
    res = client_request(State(), {"t": "secret", "name": a.name, "purpose": a.purpose, "dest": a.dest,
                                   "verify_url": a.verify_url or "", "verify_header": a.verify_header or "",
                                   "verify_cmd": a.verify_cmd or "", "cwd": os.getcwd()})
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    elif res.get("result") == "saved":
        r = res["receipt"]
        v = {"ok": "校验通过 / check passed", "fail": f"校验失败 / check failed ({r.get('detail')})",
             "skipped": "未校验 / not checked"}[r["verify"]]
        print(f"SECRET_SAVED: {r['name']} → {r['dest']} · {r['length']} chars · {r['fingerprint']} · {v}")
    else:
        print(f"SECRET_RESULT: {res.get('result')} — {_say(res)}", file=sys.stderr)
    if res.get("result") != "saved":
        return EXIT_CARD
    return 0 if res["receipt"]["verify"] != "fail" else 3


def cmd_log(a) -> int:
    from .state import State
    st = State()
    rows = read_log(st)
    bad = 0
    for r in rows[-(getattr(a, "n", None) or 50):]:
        chk = check_record(st, r)
        bad += chk == "bad"
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
        what = r.get("name") or (r.get("argv_sha256") or "")[:12]
        print(f"{when}  {r.get('kind', '?'):6}  {r.get('result', '?'):12}  {r.get('device') or '-':16}  {what}  [{chk}]")
    if not rows:
        print("（没有记录 / no entries）")
    return 1 if bad else 0


def add_parser(sub) -> None:
    sp = sub.add_parser("sudo", help="请手机上的人批准并输入管理员密码，执行一条 sudo 命令（Agent 只拿到输出）/ run one command "
                                     "with sudo after the human approves it and types the password on the phone",
                        description="The phone shows the exact command, why, and what it changes; the password is typed "
                                    "there, sealed to this computer and given to `sudo -S -k` on stdin. You only get the output.")
    sp.add_argument("--why", required=True, help="为什么要这条命令（手机上原样显示）/ why (shown on the phone)")
    sp.add_argument("--effect", default="", help="会改什么 / what it changes (shown on the phone)")
    sp.add_argument("--timeout", type=int, default=CMD_TIMEOUT, help=f"命令最长运行秒数（≤ {CMD_TIMEOUT_MAX}）")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("command", nargs="+", help="-- <command> [args…]")
    sp.set_defaults(fn=lambda a: sys.exit(cmd_sudo(a)))
    sc = sub.add_parser("secret", help="request：请手机上的人把一个 API Key 贴进来，直接存进文件（Agent 看不到值）· log / "
                                       "ask the human to paste a key on the phone; saved to a file, never shown to you")
    sc.add_argument("action", choices=["request", "log"])
    sc.add_argument("--name", help="变量名，如 ELEVENLABS_API_KEY")
    sc.add_argument("--purpose", help="用途（手机上原样显示）")
    sc.add_argument("--dest", help="存到哪：env:<文件>[#KEY]（.env 一行）或 file:<路径>（整个文件）")
    sc.add_argument("--verify-url", dest="verify_url", help="只读校验：GET 这个 https 地址，2xx = 有效")
    sc.add_argument("--verify-header", dest="verify_header", help="校验请求头模板，默认 'Authorization: Bearer {value}'")
    sc.add_argument("--verify-cmd", dest="verify_cmd", help="只读校验命令（值在环境变量 $NAME 里；手机上会显示这条命令）")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=lambda a: sys.exit(cmd_secret(a)))
