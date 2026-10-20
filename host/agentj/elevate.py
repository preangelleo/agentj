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
- **One time**: a card is good for 120 s (sudo) / 600 s (secret) and one attempt per nonce; a wrong sudo password re-arms the
  card with a new nonce and a new host key (≤ 3 tries), and consecutive wrong passwords lock all sudo cards (1 min, doubling,
  ≤ 1 h, persisted in `elevate.json`).
- **Face ID** (P59, ADR-A163): a device whose record holds a passkey (F20) must add a WebAuthn assertion (user verified)
  over `passkey.elevate_challenge(card, nonce, digest)` to 「同意」; 「拒绝」 never needs one; devices without a passkey: unchanged.
- **Never**: the password is not in argv, the environment, a log, the Agent's output or the chat — it goes to `sudo -S -k`
  on stdin from a bytearray that is zeroed after the write. A secret is written 0600 and only its receipt goes back.
- **Audit**: `elevate.log` (0600), one line per outcome: time, kind, id, result, device, its key and signature, the digest
  of what was shown, SHA-256 of argv (sudo) / key name + SHA-256 of the destination (secret). Never a value.
- **A secret card outlives its CLI** (P73, ADR-A180): an Agent harness may kill `agentj secret request` (a short shell
  timeout, the turn ended). The card stays open until the phone answers or its 600 s run out, and the outcome (never the value)
  goes to `secret-results.json` (0600) — `agentj secret result <id>` reads it back through the socket. A sudo card is still
  withdrawn when its CLI goes away (nobody would get the command's output).
- **Outbound** (F32, `secret_out.py`): `agentj secret send` — the owner picks up a value on a paired phone after Face ID.
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
import selectors
import threading
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

from . import diskq, passkey, wire

SIG_CONTEXT = "agentjarvis-elevate-v1"
SEAL_CONTEXT = "agentjarvis-seal-v1"
KINDS = ("sudo", "secret", "login")   # P408: `login` = the computer password for the web terminal (terminal.py), host-internal
TTL = {"sudo": 120, "secret": 600, "login": 120}
TRIES = 3                       # wrong sudo passwords per card before it ends
LOCK_AFTER = 3                  # consecutive wrong sudo passwords (any card) before the lock
LOCK_BASE, LOCK_MAX = 60, 3600
TS_SKEW_MS = 120_000
MAX_OPEN = 4
MAX_VALUE = 8192                # bytes of a password / secret
MAX_OUT = 256 * 1024            # bytes of stdout / stderr returned to the Agent (each)
PROMPT_WAIT = 5.0               # s to wait for sudo's own prompt before closing its stdin unwritten
MAX_FRAME = 2 * 1024 * 1024     # one request line from the CLI (P411: a screen-help picture ≤ 1.4 MB travels base64 in it)
CMD_TIMEOUT, CMD_TIMEOUT_MAX = 600, 3600
SOCK_NAME = "elevate.sock"
ENV = "AGENTJ_ELEVATE_SOCK"
PHONE_TYPES = ("elev_answer", "secret_out_open", "secret_out_decline")   # the last two: F32 (secret_out.py)
PHONE_TYPES += ("elev_revoke",)   # #116: 「收回授权」 (elevate_grant.py)
PHONE_TYPES += ("elev_stop", "elev_watch")   # P406 #173 (§11.2): Stop / show the output on the running-command strip (signed)
PHONE_TYPES += ("login_qr_check", "login_qr_cancel", "login_qr_tg", "login_qr_refresh")   # P114: browser_login.py
PHONE_TYPES += ("help_done", "help_text", "help_cancel")   # P411: screen-help cards (screenhelp.py)
PHONE_TYPES += ("ct_list", "ct_save", "ct_card_answer", "mail_fwd")   # P184: contacts_serve.py (address book, forward to e-mail)
PHONE_TYPES += ("ct_rm", "ct_export", "ct_import")   # P207: contacts_serve.py (delete one, export vcf/csv, import a file)
RESULTS_NAME = "secret-results.json"
RESULTS_KEEP = 100
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
    """The texts the phone displays, in a fixed order. sudo: cmd, why, effect. secret: name, purpose, dest, verify.
    login (P408): the `r` of the pending `term_open` it belongs to (the page shows its own fixed words for this card)."""
    if card["kind"] == "sudo":
        return [card["cmd"], card["why"], card.get("effect") or ""]
    if card["kind"] == "login":
        return [card["term"]]
    return [card["name"], card["purpose"], card["dest"], card.get("verify") or ""]


def shown_digest(kind: str, fields: list[str]) -> str:
    """hex SHA-256 of `kind` then one line per shown field `sha256(field)` — the phone hashes what it shows."""
    if kind not in KINDS:
        raise ValueError("bad kind")
    return _h("\n".join([SIG_CONTEXT, kind] + [_h(f) for f in fields]))


def signed_message(channel: str, device: str, rid: str, kind: str, decision: str, nonce: str, ts: int, digest: str,
                   ct_sha: str | None, grant: str | None = None) -> bytes:
    """`grant` (#116, §11.1): the class hash of the card's 10-minute toggle, only when the owner switched it on."""
    if decision not in ("allow", "deny") or kind not in KINDS:
        raise ValueError("bad decision")
    tail = ["grant:" + grant] if grant else []
    return "\n".join([SIG_CONTEXT, channel, device, rid, kind, decision, nonce, str(ts), digest, ct_sha or "-"] + tail).encode()


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


_INTERP = re.compile(r"(?:r?bash|sh|ash|dash|zsh|ksh|mksh|t?csh|fish|busybox|python[^/]*|pypy[^/]*|node(?:js)?|perl(?:[0-9.]+)?"
                     r"|lua(?:jit)?[0-9.]*|ruby[0-9.]*|irb[0-9.]*|php[0-9.]*|tclsh[0-9.]*|wish[0-9.]*|deno|bun|java|jshell"
                     r"|osascript|pwsh|mise|asdf|rtx)")


def interpreter_command(argv: list[str]) -> bool:
    """Executable identity, including a symlink and the program a bare name resolves to on PATH (#116: rbash, mise shims),
    never an Agent-supplied risk flag."""
    names = {os.path.basename(argv[0]), os.path.basename(os.path.realpath(argv[0]))}
    if "/" not in argv[0] and (hit := shutil.which(argv[0])):
        names |= {os.path.basename(hit), os.path.basename(os.path.realpath(hit))}
    return any(_INTERP.fullmatch(n) for n in names)


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]")
WHY_MIN_CJK, WHY_MIN_LATIN = 6, 12
WHY_DETAIL = "--why must say in plain words what the command is for (shown first on the phone)"
SERVICE_DETAIL = ("Agent J's own service never needs admin rights: it runs as you. Check `agentj service status` (it asks the "
                  "service itself); to switch harness use `agentj agent switch`.")
_AGENTJ_SVC = re.compile(r"(?:^|[\s/;&|('\"`])(?:agentj(?:-[\w.-]+)?|aj)\b[^;&|\n]*?\bservice\s+(?:start|restart|stop|install|uninstall)\b")
_MANAGER = re.compile(r"(?:^|[\s/;&|('\"`])(?:launchctl|systemctl)\b([^;&|\n]*)")


def own_service_command(cmd: str) -> bool:
    """P339: the command manages Agent J's own service — `agentj service start|restart|stop|install|uninstall`, or
    launchctl / systemctl acting on its label / unit. That runs as the owner, never as root: a sudo card for it is a
    misdiagnosis (QA Mac 10-16: a fenced OpenCode could not see launchd, concluded the service was down, asked for sudo)."""
    if _AGENTJ_SVC.search(cmd):
        return True
    labels = {"net.agentj.", "agentj.service"}
    try:
        from . import service
        labels.add(service.name())
    except Exception:  # noqa: BLE001 — the default patterns still apply
        pass
    for m in _MANAGER.finditer(cmd):
        tail = m.group(1)
        if any(n in tail for n in labels) or re.search(r"(?:^|[\s/])agentj(?:[\s.@]|$)", tail):
            return True
    return False


def why_problem(why: str, cmd: str) -> bool:
    """P339: the phone shows `why` first — it must say what the command is for, not repeat it or be a placeholder."""
    w = " ".join(why.split())
    if len(w) < (WHY_MIN_CJK if _CJK.search(w) else WHY_MIN_LATIN):
        return True
    return w.lower() in " ".join(cmd.split()).lower()


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
    if sys.platform == "darwin" and re.search(r"with\s+administrator\s+privileges", cmd, re.I):
        raise Refused("local_authorization", "Use agentj sudo with the direct command; osascript administrator privileges opens a local dialog.")
    if own_service_command(cmd):
        raise Refused("agentj_service", SERVICE_DETAIL)
    why = _text(req.get("why"), 300, required=True, field="why")
    if why_problem(why, cmd):
        raise Refused("why", WHY_DETAIL)
    effect = _text(req.get("effect"), 300, required=False, field="effect")
    if local_auth_risk():
        effect = (effect[:50] + "\n" + "本机 sudo 启用了生物验证，可能弹 Touch ID；此命令将拒绝执行。请在Agent J 主机前关闭 sudo Touch ID，见 /docs/keep-awake/。 / Local sudo biometrics may open Touch ID. Command will be refused; disable sudo Touch ID locally first. See /docs/keep-awake/.").strip()
    return {"kind": "sudo", "argv": list(argv), "cmd": cmd, "cwd": cwd, "timeout": timeout,
            "why": why, "effect": effect, "interpreter": interpreter_command(argv)}


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


# P408 (H4): the computer's own login / sudo password is asked for only by the host's `login` card (the web terminal) or an
# `agentj sudo` card — never by an Agent's secret card, where it would be written to a file the Agent chose. Narrow on
# purpose: a service's password (SMTP_PASSWORD, DB_PASSWORD, a mail app password) is still an ordinary secret.
_SYS_PW_NAME = re.compile(r"^(?:SUDO|ROOT|COMPUTER|SYSTEM|LOGIN|MAC|LINUX)(?:_LOGIN|_USER)?_?(?:PASS|PASSWORD|PW|PASSWD)$|"
                          r"^LOGIN_(?:PASS|PASSWORD|PW)$", re.I)   # R3 (M-3): the WHOLE name (MYSQL_ROOT_PASSWORD is a service's)
_SYS_PW_TEXT = re.compile(r"(?:电脑|本机|这台(?:电脑|机器|主机)?|sudo|系统登录)\s*的?\s*(?:登录|开机|用户)?\s*密码|"
                          r"\b(?:computer|sudo)(?:'s)?\s+password\b|\b(?:computer|mac|linux|system)(?:'s)?\s+login\s+password\b", re.I)
# R3: no bare 管理员 / root (a router's admin); R4: no bare "login password" (Gmail / a NAS) and no USER_PASSWORD (an app's user)


def system_password_ask(name: str, purpose: str) -> bool:
    """P408 (H4): does this secret request look like the computer's login / sudo password?"""
    return bool(_SYS_PW_NAME.search(name or "") or _SYS_PW_TEXT.search(purpose or ""))


def norm_secret(req: dict, state_root: pathlib.Path) -> dict:
    name = req.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise Refused("shape", "name: a variable name like ELEVENLABS_API_KEY")
    purpose = _text(req.get("purpose"), 300, required=True, field="purpose")
    if system_password_ask(name, purpose):
        raise Refused("login_password", "这台电脑的登录 / 管理员密码不能用密钥卡要：管理员命令用 `agentj sudo`。/ The computer's "
                                        "login or admin password is never asked for with a secret card; use `agentj sudo` "
                                        "for an admin command.")
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
                not (u.scheme == "https" or (u.scheme == "http" and local)) or "{value}" in u.netloc or "{value}" in u.scheme:
            raise Refused("shape", "verify-url: an https:// address")
        vh = vh or "Authorization: Bearer {value}"
        if not isinstance(vh, str) or len(vh) > 200 or wire.text_problem(vh, 200) or vh.count("{value}") != 1 or ":" not in vh \
                or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", vh.split(":", 1)[0].strip()):
            raise Refused("shape", "verify-header: 'Name: …{value}…'")
        verify = f"GET {vu}  ({vh})"
    elif vc:
        raise Refused("verify_cmd_disabled", "Command verification is disabled; use --verify-url instead.")
    return {"kind": "secret", "name": name, "purpose": purpose, "dest": d["show"], "dest_spec": d, "cwd": cwd or "/",
            "verify": verify, "verify_url": vu or "", "verify_header": vh if vu else "", "verify_cmd": vc or ""}


# ---------------------------------------------------------------- running sudo
LOCAL_AUTH_HINT = ("本机可能弹 Touch ID/Apple Watch 验证，远程时无法继续；请在Agent J 主机前用 sudoedit /etc/pam.d/sudo_local "
                   "注释启用的 pam_tid.so/pam_reattach.so 行（旧配置检查 /etc/pam.d/sudo），保留密码认证；不要远程自动修改 PAM。"
                   " / Local Touch ID/Watch authorization may block remote use. At the Agent J host, use sudoedit to disable the "
                   "enabled biometric PAM lines, preserving password authentication. See /docs/keep-awake/.")


def local_auth_risk(pam_dir=None) -> list[str]:
    """Conservative macOS preflight, active lines only; no biometric/PAM writes."""
    if sys.platform != "darwin":
        return []
    root = pathlib.Path(pam_dir or "/etc/pam.d")
    pending, seen, found = ["sudo"], set(), []
    while pending and len(seen) < 16:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            text = (root / name).read_text()
        except FileNotFoundError:
            continue
        except OSError:
            found.append("unreadable PAM: " + name)
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if re.search(r"pam_(?:tid|reattach|watchid)\.so", line, re.I):
                found.append(name + ": " + line)
            m = re.match(r"auth\s+include\s+([a-zA-Z0-9_-]+)$", line)
            if m:
                pending.append(m[1])
    return found


def sudo_bin() -> str | None:
    return os.environ.get("AGENTJ_SUDO_BIN") or shutil.which("sudo") or ("/usr/bin/sudo" if os.path.exists("/usr/bin/sudo") else None)


def _cap(b: bytes) -> tuple[str, bool]:
    return b[:MAX_OUT].decode("utf-8", "replace"), len(b) > MAX_OUT


def collect_command(p, timeout: float, cancel=None, input_data=None, gate: bytes | None = None,
                    watch=None) -> tuple[bytes, bytes, bool, bool]:
    """Drain bounded output off-loop; TERM/KILL and pipe drain have finite grace periods.

    Only byte counts/liveness may reach phones from here; `watch` (P406, elevate_live.Live) sees every chunk read and a tick
    per loop — it masks what goes to the phone's status strip (§11.2). The full output stays on the CLI path.
    `gate` (sudo's random prompt marker): `input_data` is written only after the marker shows on stderr before any stdout
    byte. No marker (sudo did not ask: NOPASSWD / !authenticate) means stdin is closed unwritten once the command prints or
    PROMPT_WAIT passes, so the password can never become the command's own input.
    """
    deadline = time.monotonic() + timeout
    buffers = [bytearray(), bytearray()]
    truncated, timed_out, stage = False, False, 0
    armed, tail, wait_until = gate is None, b"", time.monotonic() + PROMPT_WAIT
    with selectors.DefaultSelector() as sel:
        offset = 0
        def close_input():
            if p.stdin is not None and not p.stdin.closed:
                with contextlib.suppress(KeyError):
                    sel.unregister(p.stdin)
                p.stdin.close()
            if input_data is not None:
                wipe(input_data)
        if p.stdin is not None:
            os.set_blocking(p.stdin.fileno(), False)
            if input_data and armed:
                sel.register(p.stdin, selectors.EVENT_WRITE, 2)
            elif not input_data:
                close_input()
        for index, stream in enumerate((p.stdout, p.stderr)):
            os.set_blocking(stream.fileno(), False)
            sel.register(stream, selectors.EVENT_READ, index)
        while sel.get_map() or p.poll() is None:
            now = time.monotonic()
            if not armed and now >= wait_until:
                armed = True                       # sudo never asked: the command gets EOF, never the password
                close_input()
            if now >= deadline or (not stage and cancel is not None and cancel.is_set()):
                timed_out = True
                close_input()
                if stage == 2:
                    break  # A privileged descendant may hold a pipe; never wait forever for EOF.
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(p.pid, 15 if stage == 0 else 9)
                stage += 1
                deadline = now + .5
            if watch is not None:
                watch.tick(p.pid)
            for key, _ in sel.select(.05):
                if key.data == 2:
                    try:
                        view = memoryview(input_data)
                        try:
                            offset += os.write(key.fileobj.fileno(), view[offset:offset + 4096])
                        finally:
                            view.release()
                    except BlockingIOError:
                        continue
                    except (BrokenPipeError, OSError):
                        close_input()
                        continue
                    if offset == len(input_data):
                        close_input()
                    continue
                try:
                    data = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not data:
                    sel.unregister(key.fileobj)
                    continue
                if watch is not None:
                    watch.data(key.data, data)
                if not armed:
                    if key.data == 0:
                        armed = True               # the command already runs (sudo did not ask): close, never write
                        close_input()
                    elif gate in tail + data:
                        armed = True
                        if p.stdin is not None and not p.stdin.closed:
                            sel.register(p.stdin, selectors.EVENT_WRITE, 2)
                    else:
                        tail = (tail + data)[-(len(gate) - 1):]
                buf = buffers[key.data]
                remaining = MAX_OUT + 256 - len(buf)
                buf.extend(data[:max(0, remaining)])
                truncated |= len(data) > remaining
        close_input()
        for stream in (p.stdout, p.stderr):
            stream.close()
    with contextlib.suppress(subprocess.TimeoutExpired):
        p.wait(timeout=.5)
    return bytes(buffers[0]), bytes(buffers[1]), timed_out, truncated


def run_sudo(argv: list[str], password: bytearray, cwd: str, timeout: int, sudo: str | None = None, cancel=None,
             watch=None) -> dict:
    """`sudo -S -k -p <random marker> -- argv`; the password + newline goes to stdin once, only after sudo printed the marker
    (collect_command `gate`), then stdin closes; → {result, code, stdout, stderr, truncated, prompts}. result: done (the
    command ran; `code` is its exit status) · bad_password · timeout · no_sudo. `prompts` = how many times sudo asked:
    exactly 1 on a done run means sudo really checked this password (0 = it never asked, NOPASSWD; the password was not
    sent). A wrong password shows as a second prompt (sudo asks again after "Sorry, try again", then gets EOF)."""
    if local_auth_risk():
        return {"result": "failed", "why": "local_auth_required", "code": None, "stdout": "", "stderr": "", "truncated": False,
                "detail": LOCAL_AUTH_HINT}
    sudo = sudo or sudo_bin()
    if not sudo:
        return {"result": "no_sudo", "code": None, "stdout": "", "stderr": "", "truncated": False}
    marker = "[agentj-" + secrets.token_hex(8) + "]"
    if watch is not None:
        watch.gate = marker.encode()           # P406: a second prompt = wrong password, never "started"
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
        out, err, timed_out, overflow = collect_command(p, timeout, cancel, buf, marker.encode(), watch)
        if timed_out:
            o, t1 = _cap(out)
            e, t2 = _cap(err.replace(marker.encode(), b""))
            return {"result": "timeout", "code": None, "stdout": o, "stderr": e, "truncated": overflow or t1 or t2}
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
    return {"result": "done", "code": p.returncode, "stdout": o, "stderr": e, "truncated": overflow or t1 or t2,
            "prompts": prompts}


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
            diskq.fsync(f.fileno())   # P142: never on the event loop
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        diskq.settle(path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    finally:
        sval = None  # noqa: F841 — drop the reference; str cannot be wiped
    os.chmod(path, 0o600)


def fingerprint(value: bytearray) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()[:8]


def _key_hint(card: dict, value: bytearray) -> dict:
    """P163: a key whose check failed and that is clearly shorter than its provider issues → {"key_hint": "short"}
    ("may be incomplete"). Only for a card with a live check (an API key), from the length alone."""
    from . import keyhint
    if card.get("verify_url") and keyhint.looks_short(len(value), card.get("name"), card.get("verify_url"), value):
        return {"key_hint": "short"}
    return {}


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
            url = card["verify_url"].replace("{value}", urllib.parse.quote(sval, safe=""))
            req = urllib.request.Request(url, method="GET",
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
            # Defense in depth: even an old or internal card cannot run a command.
            return {"verify": "fail", "detail": "command_disabled"}
        return {"verify": "skipped", "detail": ""}
    except (urllib.error.URLError, OSError, ValueError):
        return {"verify": "fail", "detail": "network"}
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
        elif card["kind"] == "login":
            rec["device"] = card.get("only")
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


def results_path(st) -> pathlib.Path:
    return st.root / RESULTS_NAME


def read_results(st) -> dict:
    """id → the last known outcome of a secret card (in or out); never a value. Unreadable → empty."""
    try:
        d = json.loads(results_path(st).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and _ID.fullmatch(k) and isinstance(v, dict)} if isinstance(d, dict) else {}


def remember(st, rid: str, rec: dict) -> None:
    """Record a secret card's state (`pending` when shown, the outcome when it ends) in secret-results.json (0600), so an
    Agent whose CLI was killed reads it later (`agentj secret result <id>`). Keeps the newest RESULTS_KEEP."""
    d = read_results(st)
    d[rid] = {**d.get(rid, {}), **rec, "id": rid, "at": int(time.time())}
    keep = sorted(d.items(), key=lambda kv: kv[1].get("at", 0))[-RESULTS_KEEP:]
    with contextlib.suppress(OSError):
        st.write_private(results_path(st), json.dumps(dict(keep), ensure_ascii=False).encode())


def check_record(st, r: dict) -> str:
    """'ok' / 'ok_removed' (signature verifies; device still / no longer paired) · 'bad' · 'unsigned' (host-side outcome) ·
    'passkey' (F32 pickup: the paired device's Noise session + a user-verified passkey assertion; no Ed25519 line)."""
    if r.get("kind") in ("secret_out", "keys"):   # P352: keys copy / delete — same passkey-only proof
        return "passkey" if r.get("passkey") == "uv" else "unsigned"
    if not r.get("sig"):
        return "unsigned"
    try:
        sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r["sig"])
        if r.get("decision") == "stop":            # P406: 「停止」 is a signed control action over the card id (controls.py)
            from . import controls
            ok = controls.verify(sk, sig, r["channel"], r["device"], "elev_stop", r["n"], r["sig_ts"],
                                 controls.object_digest("elev_stop", {"id": r["id"]}))
            if not ok:
                raise ValueError("stop")
            cur = st.sign_key(str(r.get("device")))
            return "ok_removed" if cur is None else ("ok" if cur == sk else "bad")
        msg = signed_message(r["channel"], r["device"], r["id"], r["kind"], r["decision"], r["n"], r["sig_ts"],
                             r["shown_sha256"], r.get("ct_sha256"), r.get("grant_sha256"))
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
        self.bg: set[asyncio.Task] = set()
        self.completed: dict[str, dict] = {}
        self.server = None
        self.sudo = None             # tests: a stand-in sudo; else AGENTJ_SUDO_BIN / PATH
        self.helper_status = None    # tests: a stand-in for elevate_helper.status()
        self.helper_cmd = None       # tests: a stand-in for the installed helper command line
        from .secret_out import Outbox
        self.out = Outbox(self)      # F32: outbound pickup cards (`agentj secret send`)
        from .browser_login import LoginCards
        self.login = LoginCards(self)  # P114: login QR cards (`agentj browser login`)
        from .screenhelp import HelpCards
        self.help = HelpCards(self)    # P411: `agentj qr push` / `agentj screen ask` (screen-help cards)
        from .contacts_serve import ContactService
        self.contacts = ContactService(self)  # P184: address book, contact cards, 「转发到邮箱」, `agentj mail send`
        self._upgrade_mem: dict = {}  # P205: the fenced `update apply`'s cached old wheel (update.host_op)
        from .elevate_grant import GrantBook
        self.grants = GrantBook(self)  # #116: 10-minute same-class approval (memory only)

    @property
    def sock_path(self) -> pathlib.Path:
        return self.st.perm_dir / SOCK_NAME

    async def start(self) -> None:
        # a card shown by a previous serve died with it (its one-time key lived only in that process's memory)
        for rid, r in read_results(self.st).items():
            if r.get("result") == "pending":
                remember(self.st, rid, {"result": "gone", "why": "restart"})
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
        os.environ[ENV] = str(self.sock_path)

    async def stop(self) -> None:
        if os.environ.get(ENV) == str(self.sock_path):
            os.environ.pop(ENV, None)
        for c in self.cards.values():
            if c.get("cancel") is not None:
                c["cancel"].set()
        self.grants.clear("gone")
        if self.bg:
            await asyncio.gather(*list(self.bg), return_exceptions=True)
        self.cancel_all("gone")
        self.out.end_all("gone")
        self.login.end_all("gone")
        self.help.end_all("gone")
        self.contacts.end_all("gone")
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
        if c.get("busy"):
            m.update(state="running", ttl=0, elapsed=max(0, int(time.monotonic() - c["started"])), timeout=c.get("timeout", 0))
            if c.get("live") is not None and c["live"].started:
                m["phase"] = "started"                # P406: the command runs — the page shows its strip, not the card
        if c["fails"]:
            m["bad"] = c["fails"]
        if c["kind"] == "sudo":
            m.update(cmd=c["cmd"], why=c["why"], effect=c.get("effect") or "", interpreter=interpreter_command(c["argv"]))
            if c.get("helper"):
                m["helper"] = list(c["helper"])       # these phones may approve without a password (elevate_helper)
            if c.get("grant"):
                m["grant"] = dict(c["grant"])         # #116: the opt-in toggle (off by default): program, options, hash
        elif c["kind"] == "login":
            m["term"] = c["term"]                     # P408: the pending term_open this password belongs to
        else:
            m.update(name=c["name"], purpose=c["purpose"], dest=c["dest"], verify=c.get("verify") or "")
        pk = self.st.passkey_of_device(s.device) if s is not None and s.device else None
        if pk:
            m["fa"] = pk["id"]   # ADR-A163: this device saved a passkey → 「同意」 needs it (its own credential id only)
        return m

    async def _to_phones(self, obj_fn, only: str | None = None) -> None:
        if only is None:
            return await self.host._send_ready(obj_fn)
        for s in list(self.host.sessions.values()):     # P408: a `login` card goes to the opening device only
            if s.state == "ready" and s.device == only and self.st.is_allowed(s.pub):
                await self.host.send_app(s, obj_fn(s))

    def _sees(self, c: dict, s) -> bool:
        return not c.get("only") or (s is not None and s.device == c["only"])

    def _arm(self, c: dict) -> None:
        """A fresh one-time nonce and host key (first show, and after a wrong password)."""
        old = c.get("priv")
        c["priv"] = X25519PrivateKey.generate()
        c["epk"] = _raw_pub(c["priv"])
        c["nonce"] = secrets.token_hex(16)
        c["deadline"] = time.monotonic() + TTL[c["kind"]]
        del old

    def open_msgs(self, s) -> list[dict]:
        """P203: the open sudo / secret cards as this device sees them (on_ready's replay and the cards_get snapshot)."""
        return [self.card_msg(c, s) for c in list(self.cards.values()) if not c["fut"].done() and self._sees(c, s)]

    def all_open_msgs(self, s) -> list[dict]:
        """P203: these and the pickup / sign-in / contact cards this module also owns."""
        return (self.open_msgs(s) + self.out.open_msgs(s) + self.login.open_msgs(s) + self.help.open_msgs(s)
                + self.contacts.open_msgs(s))

    def completed_msgs(self, s=None) -> list[dict]:
        return [dict(m) for m in self.completed.values()]

    async def on_ready(self, s) -> None:
        for m in self.completed_msgs(s) + self.open_msgs(s):
            await self.host.send_app(s, m)
        await self.grants.on_ready(s)
        await self.out.on_ready(s)
        await self.login.on_ready(s)
        await self.help.on_ready(s)
        await self.contacts.on_ready(s)

    def _finish(self, c: dict, result: dict) -> None:
        if not c["fut"].done():
            c["fut"].set_result(result)

    def cancel_all(self, result: str) -> None:
        for c in list(self.cards.values()):
            if not c.get("busy"):
                self._finish(c, {"result": result})
        if result == "stopped":                    # Stop everything also voids pickup cards (and wipes their values)
            self.grants.clear(result)              # … and every 10-minute approval
            self.out.end_all(result)
            self.login.end_all(result)
            self.help.end_all(result)
            self.contacts.end_all(result)

    async def request(self, card: dict, gone: asyncio.Future | None = None, on_card=None) -> dict:
        """Show a card, wait for a decision, act; → the result for the Agent (never the value). `on_card(c)` (async) runs
        once the card exists (the CLI prints its id, so an interrupted Agent can read the outcome later). A secret card's
        state and outcome go to secret-results.json (`remember`)."""
        if self.host.stopped():
            return {"result": "stopped"}
        from . import provenance
        if card["kind"] != "login" and provenance.untrusted_turn(getattr(self.host, "agent", None)) and not provenance.delegated_turn(getattr(self.host, "agent", None)):
            audit(self.st, None, kind=card.get("kind"), result="group_source", reason="group_source")
            return {"result": "group_source"}      # B1 (P117): a group member's turn never reaches the owner's card
        if len([c for c in self.cards.values() if not c["fut"].done()]) >= MAX_OPEN:
            return {"result": "busy"}
        if card["kind"] == "sudo" and (left := locked_for(self.st)):
            audit(self.st, None, kind="sudo", result="locked", reason="locked")
            return {"result": "locked", "secs": left}
        if not self.host._approvers():
            return {"result": "no_device"}
        same_owner_task = provenance.same_owner_source(getattr(self.host, "agent", None))
        if card["kind"] == "sudo" and not same_owner_task and (auto := await self.grants.auto(card, gone)) is not None:
            return auto                            # #116: same class within the owner's 10-minute approval, logged
        c = dict(card, id=secrets.token_hex(16), fails=0, fut=asyncio.get_running_loop().create_future())
        if c["kind"] == "sudo":
            c["helper"] = [] if interpreter_command(c["argv"]) else self.helper_devices()
            from .elevate_grant import scope_of
            c["scope"], c["grant"] = scope_of(self.host), None if same_owner_task else self.grants.offer(c)
        c["digest"] = shown_digest(c["kind"], shown_fields(c))
        self._arm(c)
        self.cards[c["id"]] = c
        self.st.log("elev_card", id=c["id"], kind=c["kind"])
        if c["kind"] == "secret":
            remember(self.st, c["id"], {"kind": "secret", "name": c["name"], "result": "pending",
                                        "until": int(time.time() + TTL["secret"])})
        if on_card is not None:
            try:
                await on_card(c)
            except Exception as e:  # noqa: BLE001 — P192 (B18): the card stays; its delivery hook failing is logged
                from . import bg
                bg.swallowed(self.st.log, "elev_card_hook_failed", e, why=c["kind"], every=0)
        if c["kind"] != "login":                   # P408: the login card shows inside the open terminal page only
            self.host.push_notify("ask")
        only = c.get("only")
        cancelled=False
        try:
            await self._to_phones(lambda s: self.card_msg(c, s), only)
            while True:
                waits = [c["fut"]] + ([gone] if gone is not None else [])
                left = c["deadline"] - time.monotonic()
                await asyncio.wait(waits, timeout=min(1.0, max(0.0, left)), return_when=asyncio.FIRST_COMPLETED)
                if c["fut"].done():
                    res = c["fut"].result()
                    if res.get("result") == "retry":       # wrong password: re-armed, shown again
                        c["fut"] = asyncio.get_running_loop().create_future()
                        await self._to_phones(lambda s: self.card_msg(c, s), only)
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
                        await self._to_phones(lambda s: self.card_msg(c, s), only)
                        continue
                    break
        except asyncio.CancelledError:
            cancelled=True
            res={"result":"cancelled"}
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
        if c["kind"] == "secret" and (res.get("key_hint") or (res.get("receipt") or {}).get("key_hint")):
            done_msg["key_hint"] = "short"          # P163: one word from the length, never the key
        if not only:                               # P408: a login card's end is replayed to nobody else
            self.completed[c["id"]] = dict(done_msg)
        live, tail = c.get("live"), None
        if live is not None and live.started:      # P406 (§11.2): only a phone watching the output gets the masked tail
            from .elevate_live import fit, DONE_TAIL
            tail = fit(live.tail[-DONE_TAIL:])
            if c.get("stop_by"):
                done_msg["stop"] = True
        watching = {x.cid for x in self._watchers(c)} if tail is not None else set()
        while len(self.completed) > RESULTS_KEEP:
            self.completed.pop(next(iter(self.completed)))
        if c["kind"] == "sudo":
            labels = {"done": ("已完成", "completed") if res.get("code") == 0 else ("执行失败", "failed"),
                      "timeout": ("执行超时", "timed out"), "denied": ("已拒绝", "denied"),
                      "gone": ("已取消", "cancelled"), "stopped": ("已停止", "stopped")}
            zh, en = labels.get(res["result"], ("执行失败", "failed"))
            code = f" ({res['code']})" if res.get("code") is not None else ""
            self.host.agent_notice(f"管理员命令{zh}{code} · {c['id'][:8]} / Admin command {en}{code} · {c['id'][:8]}")
        await self._to_phones(lambda s: dict(done_msg, tail=tail) if s.cid in watching else dict(done_msg), only)
        self.st.log("elev_done", id=c["id"], kind=c["kind"], result=res["result"])
        if c["kind"] == "secret":
            remember(self.st, c["id"], {"result": res["result"], "until": None,
                                        **{k: res[k] for k in ("why", "detail", "receipt", "key_hint") if res.get(k) is not None}})
        res["id"] = c["id"]
        if cancelled:raise asyncio.CancelledError
        return res

    contacts_types = ("ct_list", "ct_save", "ct_card_answer", "mail_fwd", "ct_rm", "ct_export", "ct_import")   # + P207

    async def on_phone(self, s, obj: dict) -> None:
        if obj.get("t") == "elev_revoke":                                # #116
            return await self.grants.on_phone(s, obj)
        if obj.get("t") in ("secret_out_open", "secret_out_decline"):   # F32
            return await self.out.on_phone(s, obj)
        if isinstance(obj.get("t"), str) and obj["t"].startswith("login_qr_"):   # P114: may wait on the browser (≤ 15 s) —
            task = asyncio.create_task(self.login.on_phone(s, obj))          # never hold the relay receive loop (P99 B1)
            self.login.bg.add(task)
            task.add_done_callback(self.login.bg.discard)
            return
        if obj.get("t") in ("help_done", "help_text", "help_cancel"):        # P411: screen-help cards
            return await self.help.on_phone(s, obj)
        if obj.get("t") in self.contacts_types:                              # P184: SMTP / file reads take seconds
            task = asyncio.create_task(self.contacts.on_phone(s, obj))
            self.contacts.bg.add(task)
            task.add_done_callback(self.contacts.bg.discard)
            return
        rid = obj.get("id")
        c = self.cards.get(rid) if isinstance(rid, str) and _ID.fullmatch(rid) else None
        if obj.get("t") == "elev_stop":
            return await self._stop(s, c, obj)
        if obj.get("t") == "elev_watch":
            return self._watch(s, c, obj)
        if c is None or c["fut"].done() or c.get("busy") or not self._sees(c, s):
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
                      grant_sha256=obj.get("grant") if ok else None,   # #116: signed — the owner switched the toggle on
                      passkey="uv" if ok and self.st.passkey_of_device(s.device) else None)
        if not ok:
            audit(self.st, c, result="denied", reason="device", **signed)
            return self._finish(c, {"result": "denied", "_logged": True})
        c["busy"] = True
        c["want_grant"] = obj.get("grant") if ok else None   # #116: checked and signed in _check
        c["started"] = time.monotonic()
        c["cancel"] = threading.Event()
        if c["kind"] == "sudo":                    # P406: the approve-only helper has no password to check: running at once
            from .elevate_live import Live
            c["live"], c["wake"] = Live(started="ct" not in obj), asyncio.Event()
            loop, ev = asyncio.get_running_loop(), c["wake"]
            c["live"].wake = lambda: loop.call_soon_threadsafe(ev.set)
        nonce, priv = c["nonce"], c.pop("priv")
        c["nonce"] = None  # reserve the one-time approval BEFORE returning to the receive loop
        task = asyncio.create_task(self._execute(c, s, obj, signed, nonce, priv))
        self.bg.add(task)
        task.add_done_callback(self.bg.discard)

    async def _progress(self, c) -> None:
        """Liveness every 5 s; P406: at once when the command started (`phase:"started"` → the page closes the card) and
        the masked output as it comes (`elev_out`, at most every 0.3 s)."""
        live, wake, shown, beat = c.get("live"), c.get("wake"), False, 0.0
        c.setdefault("watch", {})
        c.setdefault("shown_to", set())
        while c.get("busy") and not c["fut"].done():
            if wake is not None:
                wake.clear()                       # before reading: a chunk arriving from now on wakes the wait below
            now = time.monotonic()
            started = live is not None and live.started
            if now >= beat or (started and not shown):
                beat, shown = now + 5, started
                m = {"t": "elev_progress", "id": c["id"], "kind": c["kind"], "state": "running",
                     "elapsed": int(now - c["started"]), "timeout": c.get("timeout", 0)}
                if started:
                    m["phase"] = "started"
                with contextlib.suppress(Exception):
                    await self._to_phones(lambda s: dict(m), c.get("only"))
            if started:                            # P406 F1: output only to the phones that asked (signed elev_watch)
                text, tail = live.take()
                for x in self._watchers(c):
                    new = x.cid not in c["shown_to"]
                    if new or text:
                        c["shown_to"].add(x.cid)
                        with contextlib.suppress(Exception):
                            await self.host.send_app(x, {"t": "elev_out", "id": c["id"], "text": tail if new else text,
                                                         **({"full": True} if new else {})})
                c["shown_to"] &= set(c["watch"])
            if wake is None:
                await asyncio.sleep(5)
                continue
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(wake.wait(), max(.05, beat - time.monotonic()))
            await asyncio.sleep(.3)                # batch a chatty command's lines

    def _watchers(self, c) -> list:
        """The sessions that asked to see this card's output and may still see it: ready, allowlisted, with an approval key."""
        out = []
        for cid, x in list((c.get("watch") or {}).items()):
            if self.host.sessions.get(cid) is x and x.state == "ready" and self.st.is_allowed(x.pub) and self.st.sign_key(x.device):
                out.append(x)
            else:
                c["watch"].pop(cid, None)
        return out

    def _watch(self, s, c, obj) -> None:
        """P406 F1 (§11.2): 「展开输出」 on the strip — a signed control action `elev_watch` (object = card id \n on|off) from a
        ready allowlisted device with an approval key, for a running sudo card. Only that session then gets the masked output
        (the tail so far, then what follows, then the result's tail); `on:false`, the end of the card or the session closing
        stops it. Nothing of the output goes to any phone without it."""
        rid, on = obj.get("id"), obj.get("on")
        if c is None or c["kind"] != "sudo" or not c.get("busy") or c["fut"].done() or not self._sees(c, s) \
                or not isinstance(on, bool):
            self.st.log("elev_refused", id=rid if isinstance(rid, str) else None, device=s.device, reason="watch_unknown")
            return
        from . import controls
        why = controls.check(self.st, self.host.nonces, self.host.channel, s.device, obj, "elev_watch", {"id": c["id"], "on": on})
        if why:
            self.st.log("elev_refused", id=c["id"], device=s.device, reason="watch_" + why)
            return
        c.setdefault("watch", {})
        c.setdefault("shown_to", set())
        if on:
            c["watch"][s.cid] = s
        else:
            c["watch"].pop(s.cid, None)
            c["shown_to"].discard(s.cid)
        self.st.log("elev_watch", id=c["id"], device=s.device, on=on)
        if c.get("wake") is not None:
            c["wake"].set()

    async def _stop(self, s, c, obj) -> None:
        """P406 (§11.2): 「停止」 on the strip — a signed control action (controls.check, object = the card id) from any ready
        allowlisted device with an approval key, for a sudo card whose command is running. Same path as a timeout:
        process-group TERM, then KILL; the result is `stopped`."""
        rid = obj.get("id")
        if c is None or c["kind"] != "sudo" or not c.get("busy") or c["fut"].done() or not self._sees(c, s):
            self.st.log("elev_refused", id=rid if isinstance(rid, str) else None, device=s.device, reason="stop_unknown")
            return
        from . import controls
        why = controls.check(self.st, self.host.nonces, self.host.channel, s.device, obj, "elev_stop", {"id": c["id"]})
        if why:
            self.st.log("elev_refused", id=c["id"], device=s.device, reason="stop_" + why)
            return
        c["stop_by"] = s.device
        c["cancel"].set()
        audit(self.st, c, result="stop", reason="device", device=s.device, sk=wire.b64u(self.st.sign_key(s.device)),
              sig=obj["sig"], n=obj["n"], sig_ts=obj["ts"], decision="stop", channel=self.host.channel)

    async def _execute(self, c, s, obj, signed, nonce, priv) -> None:
        progress = asyncio.create_task(self._progress(c))
        worker = asyncio.create_task(self._execute_approved(c, s, obj, signed, nonce, priv))
        try:
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                c["cancel"].set()
                # to_thread cannot be cancelled: let the bounded child stop before wiping its input.
                await worker
                raise
        except Exception:  # never log exception strings: a verifier / subprocess may include private data
            c["busy"] = False
            audit(self.st, c, result="failed", reason="execution", **signed)
            self._finish(c, {"result": "failed", "why": "execution", "_logged": True})
        finally:
            progress.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress

    async def _execute_approved(self, c, s, obj, signed, nonce, priv) -> None:
        if "ct" not in obj:                        # approve-only: the admin helper checks the same signature as root
            from . import elevate_helper
            req = {"channel": self.host.channel, "device": s.device, "id": c["id"], "n": nonce, "ts": obj["ts"], "sig": obj["sig"],
                   "argv": c["argv"], "why": c["why"], "effect": c.get("effect") or "", "cwd": c["cwd"]}
            res = await asyncio.to_thread(elevate_helper.run, req, c["timeout"], self.sudo, self.helper_cmd, c["cancel"])
            if c.get("stop_by") and res.get("result") == "timeout":
                res["result"] = "stopped"          # P406: the owner's Stop, not the time limit
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
        keep = bytearray(value) if c.get("want_grant") and c["kind"] == "sudo" else None   # #116: zeroed unless granted
        res = None
        try:
            if c["kind"] == "sudo":
                res = await asyncio.to_thread(run_sudo, c["argv"], value, c["cwd"], c["timeout"], self.sudo, c["cancel"],
                                              c.get("live"))
            else:
                res = await asyncio.to_thread(self._save, c, value)
                if res.get("result") == "saved":
                    await self._reload_saved_env(c, res)
        finally:
            wipe(value)
            if keep is not None and ((res or {}).get("result") != "done" or res.get("prompts") != 1):
                # #116: only a password sudo actually asked for and accepted (exactly one prompt) may be kept
                wipe(keep)
                keep = None
        if c.get("live") is not None:
            c["live"].close()                      # P406: release what the masker still holds (masked)
        if c.get("stop_by") and res.get("result") == "timeout":
            res["result"] = "stopped"              # P406: the owner's Stop on the strip, not the time limit
        if keep is not None:                       # the password was right: the approving device's 10-minute window
            self.grants.create(c, s.device, wire.unb64u(signed["sk"]), keep)
        if c["kind"] == "login" and res.get("result") in ("retry", "locked", "bad_password"):
            c["fails"] += 1                        # P408: terminal.py counts its own locks (term_store.note), never elevate.json
            audit(self.st, c, result="bad_password", reason="device", **signed)
            c["busy"] = False
            if res["result"] == "retry" and c["fails"] < TRIES:
                self._arm(c)
                return self._finish(c, {"result": "retry"})
            return self._finish(c, {**res, "result": "locked" if res["result"] == "locked" else "bad_password", "_logged": True})
        if res["result"] == "bad_password":
            c["fails"] += 1
            c.pop("live", None)                    # P406: a wrong password never started anything (the card comes back)
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
            if c["kind"] != "sudo" or interpreter_command(c["argv"]) or s.device not in (c.get("helper") or []):
                return "shape"                      # approve-only exists only for a phone the admin helper accepts
        elif ok and (not isinstance(obj.get("epk"), str) or not isinstance(obj.get("ct"), str) or len(obj["ct"]) > 12000):
            return "shape"
        if "grant" in obj and not (ok and "ct" in obj and c.get("grant") and obj["grant"] == c["grant"]["h"]):
            return "shape"                          # #116: only the hash this card offered, only with the password
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
                                 c["digest"], ct_sha, obj.get("grant"))
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

    def _admin_open(self) -> dict:
        from .cli import spawn_admin_open
        old = getattr(self, "_admin_page", None)
        if old is not None and old.poll() is None:
            old.terminate()
            with contextlib.suppress(Exception):
                old.wait(5)
        ev, self._admin_page = spawn_admin_open(0)
        self.st.log("admin_open", trigger="agent", result=ev["ev"])
        return {"ev": ev["ev"], "port": ev.get("port")}

    async def _reload_saved_env(self, card: dict, result: dict) -> None:
        """Only a saved assignment to the managed service file triggers an in-process reload."""
        from . import service
        dest = card.get("dest_spec") or {}
        managed = os.environ.get("AGENTJ_SERVICE_ENV_FILE") or service.env_file(service.name())
        if dest.get("mode") != "env" or pathlib.Path(dest.get("path", "")).resolve() != pathlib.Path(managed).resolve():
            return
        try:
            changed = await asyncio.to_thread(service.reload_service_env, self.host.preferences)
            self.host._reload_runtime(bool(changed))
            if changed and self.host.telegram is not None:
                self.host.telegram.bot_id = None
            result["receipt"]["env_reloaded"] = True
        except service.ServiceError:
            result["receipt"]["env_reloaded"] = False
            self.st.log("secret_env_reload", result="failed")

    def _save(self, c: dict, value: bytearray) -> dict:
        if len(value) > MAX_VALUE:
            return {"result": "failed", "why": "value"}
        if callable(c.get("sink")):     # P170: a host-internal card (the mail password) — the host checks and stores it itself
            try:
                return c["sink"](value)
            except Exception:   # noqa: BLE001 — a fixed word only, never the value or a server reply
                return {"result": "failed", "why": "io"}
        verified = None
        if c.get('verify_before_write'):
            verified = verify_secret(c, value)
            if verified.get('verify') != 'ok':
                return {'result': 'failed', 'why': 'verify', 'detail': verified.get('detail', ''), **_key_hint(c, value)}
        try:
            write_secret(c["dest_spec"], value)
        except Refused as e:
            return {"result": "failed", "why": e.why, "detail": e.detail}
        except OSError:
            return {"result": "failed", "why": "io"}
        receipt = {"name": c["name"], "dest": c["dest"], "length": len(value), "fingerprint": fingerprint(value)}
        receipt.update(verified or verify_secret(c, value))
        if receipt.get("verify") == "fail":
            receipt.update(_key_hint(c, value))
        return {"result": "saved", "receipt": receipt}

    async def result_of(self, req: dict) -> dict:
        """`agentj secret result [<id>] [--wait]`: a card's last known state from secret-results.json (never a value). No id →
        the newest ten. `wait` → poll until it is no longer pending (≤ the card's own deadline + a little)."""
        rid = req.get("id")
        if rid in (None, ""):
            rows = sorted(read_results(self.st).values(), key=lambda r: r.get("at", 0))[-10:]
            return {"result": "list", "items": rows}
        if not isinstance(rid, str) or not _ID.fullmatch(rid):
            return {"result": "refused", "why": "shape", "detail": "id: 32 hex characters (printed by the first command)"}
        stop_at = time.monotonic() + TTL["secret"] + 30
        while True:
            r = read_results(self.st).get(rid)
            if r is None:
                return {"result": "unknown", "id": rid}
            if r.get("result") != "pending" or not req.get("wait") or time.monotonic() > stop_at:
                out = dict(r)
                if out.get("result") == "pending" and out.get("until"):
                    out["secs"] = max(0, int(out["until"] - time.time()))
                return out
            await asyncio.sleep(0.5)

    async def _p115(self, req: dict) -> dict:
        if req["t"] == "google":
            from . import google
            if req.get("op") not in google.OPS:
                return {"ok": False, "why": "shape", "detail": "op: " + " | ".join(google.OPS)}
            try:
                res = await asyncio.to_thread(google.execute, req["op"], self.st)
            except google.GoogleError as e:
                return {"ok": False, "why": e.reason, "detail": e.detail}
            self.st.log("google_cli", result=req["op"])
            return res
        from . import first_run
        op, item = req.get("op"), req.get("item")
        if op not in ("status", "resume", "check", "close", "open") or (item is not None and item not in first_run.BY_ID):
            return {"ok": False, "why": "shape", "detail": "op: status | resume | check | close | open; item: a checklist id"}
        serve = self.host.setup_serve() if hasattr(self.host, "setup_serve") else None
        try:
            if op == "status":
                res = await asyncio.to_thread(first_run.status_, self.st)
            elif op == "resume":
                res = await asyncio.to_thread(first_run.resume, self.st, serve)
            elif op == "close":          # P131: the owner said "close it" in chat — the pages hide it at once
                res = await asyncio.to_thread(first_run.close, self.st)
            elif op == "open":           # P131 追加2: only the owner's own ask reopens a closed checklist
                res = await asyncio.to_thread(first_run.reopen, self.st)
            else:
                res = await asyncio.to_thread(first_run.check, self.st, [item] if item else None, serve, None, bool(item))
        except first_run.SetupError as e:
            return {"ok": False, "why": e.reason}
        if op in ("resume", "open") and res.get("ok") and res.get("revision") and hasattr(self.host, "push_setup"):
            self.host.push_setup(res, show=True)
            res = {**res, "card": "sent"}
        if op == "close" and res.get("revision") and hasattr(self.host, "push_setup"):
            self.host.push_setup(res, show=False)
        return res

    async def _p131_offer(self) -> None:
        """P131: a browser already running here was proposed — refresh the checklist's browser rows and show it."""
        from . import first_run
        if not hasattr(self.host, "push_setup"):
            return
        try:
            if await asyncio.to_thread(first_run.read, self.st) is None:
                return
            res = await asyncio.to_thread(first_run.check, self.st, ["browser"], self.host.setup_serve())
        except (first_run.SetupError, OSError):
            return
        self.host.push_setup(res, show=True)

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
            if isinstance(req, dict) and req.get("t") == "keep_awake":
                from . import keep_awake
                if req.get("action") not in ("status", "on", "off") or req.get("power") not in ("ac", "battery", "all") or type(req.get("dry_run")) is not bool:
                    raise Refused("shape", "keep_awake: fixed action/power and boolean dry_run")
                # User state is outside the Agent fence. Each root change still creates
                # its own normal signed phone card through this same socket.
                res = await asyncio.to_thread(keep_awake.execute, req["action"], req["power"], req["dry_run"], self.st)
                w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req, dict) and req.get("t") == "browser":
                from . import browser
                # P114: the browser's state lives outside the Agent fence; fixed actions with structured arguments only.
                res = await asyncio.to_thread(browser.execute, req, self.st)
                if res.get("result") in ("confirm_on_phone", "existing_found"):
                    await self._p131_offer()      # the owner answers on the checklist page ("use it" / "install another")
                w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req, dict) and req.get("t") in ("google", "setup"):
                # P115: Google tools (google.py) and the first-run checklist (first_run.py) keep their state outside the
                # Agent fence; the Agent asks here. Fixed operations only; the checklist cannot mark choices from here.
                res = await self._p115(req)
                w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req, dict) and req.get("t") == "upgrade":
                # P205: `agentj update apply` inside the Agent fence cannot see the state directory: the host caches the old
                # wheel, writes the pending record / marker, rolls back and runs the post-upgrade doctor (update.host_op).
                from . import update
                if not isinstance(req.get("op"), str):
                    raise Refused("shape", "upgrade: op")
                res = await asyncio.to_thread(update.host_op, self.st, req, self._upgrade_mem)
                w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req, dict) and req.get("t") in ("harness_job", "serve_ping"):
                # P339: `agentj agent switch` (any harness, fenced or not) — serve launches the switch / scan job itself from
                # semantic fields (switch_harness.check_spec); inside the fence launchd / systemd cannot be reached.
                # serve_ping: "is serve running?" answered by serve itself (`agentj service status` inside the fence).
                if req["t"] == "serve_ping":
                    res = {"ok": True, "serve": "answering"}
                else:
                    from . import switch_harness
                    res = await asyncio.to_thread(switch_harness.host_job, self.st, req)
                w.write((json.dumps(res) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req, dict) and req.get("t") == "jcard_open":
                # P360 (§34): `agentj card open` — inside the Agent fence too (control.sock is hidden there). Navigation only:
                # serve checks the target, throttles, and pushes {"t":"jcard_open"} to the phones; no card, no data.
                res = await self.host.jcard_open(req)
                w.write((json.dumps(res) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req, dict) and req.get("t") == "admin_open":
                # P163: `agentj admin --open` from inside the Agent fence (state hidden, own pid namespace): serve opens
                # the local admin page in the owner's default browser. The one-time link stays in the page process;
                # only {"ev","port"} comes back. One page at a time: a new one replaces the previous one.
                res = await asyncio.to_thread(self._admin_open)
                w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
                await w.drain()
                w.close()
                return
            if isinstance(req,dict) and req.get('t')=='provider_profile':
                from . import provider_profiles
                # Same-user Agent-facing socket; fixed native provider targets only.
                # Secret values stay entirely in the host; metadata is the receipt.
                result=await asyncio.to_thread(provider_profiles.execute,req.get('spec'))
                self.st.log('provider_profile',result='ok' if result.get('ok') else 'failed')
                w.write((json.dumps(result)+'\n').encode());await w.drain();w.close()
                return
            if isinstance(req, dict) and req.get("t") in ("contacts", "mail_send"):
                # P184: the address book lives outside the Agent fence; fixed operations. A contact card waits for the owner
                # (early: the CLI prints CONTACT_CARD: <id> first); `mail_send` waits for the owner's approval card.
                async def early(c):
                    w.write((json.dumps({"t": "card", "id": c["id"], "kind": "contact", "ttl": 600}) + "\n").encode())
                    await w.drain()
                if req["t"] == "mail_send":
                    res = await self.contacts.agent_send(req)
                elif req.get("op") == "card":
                    res = await self.contacts.card(req, early if req.get("early") is True else None)
                else:
                    res = await self.contacts.agent(req)
            elif isinstance(req, dict) and req.get("t") == "secret_out":          # F32: a pickup card; answers at once
                res = await self.out.send(req)
            elif isinstance(req, dict) and req.get("t") == "browser_login":     # P114: a login QR card; answers at once
                res = await self.login.request(req)
            elif isinstance(req, dict) and req.get("t") == "browser_login_result":
                res = self.login.result(req)
            elif isinstance(req, dict) and req.get("t") == "help_push":         # P411: a screen-help card (+ optional wait)
                res = await self.help.push(req)
            elif isinstance(req, dict) and req.get("t") == "help_wait":
                res = await self.help.wait(req)
            elif isinstance(req, dict) and req.get("t") == "help_end":
                res = await self.help.end_req(req)
            elif isinstance(req, dict) and req.get("t") == "help_capture":      # the browser page, fixed CDP calls only
                res = await self.help.capture(req)
            elif isinstance(req, dict) and req.get("t") == "help_page":
                res = await self.help.page(req)
            elif isinstance(req, dict) and req.get("t") == "secret_result":     # P73: the outcome of an earlier card
                res = await self.result_of(req)
            else:
                if not isinstance(req, dict) or req.get("t") not in KINDS:
                    raise Refused("shape", "t: sudo | secret | secret_out | secret_result")
                card = norm_sudo(req) if req["t"] == "sudo" else norm_secret(req, self.st.root)
                on_card = None
                if req.get("early") is True:                                    # a CLI that prints the card id first
                    async def on_card(c):
                        w.write((json.dumps({"t": "card", "id": c["id"], "kind": c["kind"], "ttl": TTL[c["kind"]]}) + "\n").encode())
                        await w.drain()
                if card["kind"] == "sudo":
                    # sudo: the CLI going away withdraws the card (nobody would get the output). A secret card does NOT
                    # depend on its CLI (P73): it stays until the phone answers or it expires, the outcome is remembered.
                    async def eof():
                        with contextlib.suppress(Exception):
                            await r.read()
                        if not gone.done():
                            gone.set_result(True)
                    watch = asyncio.create_task(eof())
                    res = await self.request(card, gone, on_card)
                else:
                    res = await asyncio.shield(asyncio.ensure_future(self.request(card, None, on_card)))
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
def client_request(st, req: dict, timeout: float = CMD_TIMEOUT_MAX + 400, on_card=None, path=None) -> dict:
    """One request on elevate.sock → the final JSON line. With `on_card` the request asks for the card id first (`early`):
    `on_card({"t":"card","id",…})` is called as soon as the host shows the card. `path`: a socket chosen by the caller."""
    path = pathlib.Path(path or os.environ.get(ENV) or st.perm_dir / SOCK_NAME)
    if on_card is not None:
        req = {**req, "early": True}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except OSError:
            return {"result": "unavailable"}
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
        buf = b""
        while True:
            while b"\n" not in buf:
                try:
                    chunk = s.recv(65536)
                except (socket.timeout, OSError):
                    return {"result": "unavailable"}
                if not chunk:
                    return {"result": "unavailable"}
                buf += chunk
            line, buf = buf.split(b"\n", 1)
            try:
                obj = json.loads(line)
            except ValueError:
                return {"result": "unavailable"}
            if isinstance(obj, dict) and obj.get("t") == "card" and on_card is not None:
                on_card(obj)
                continue
            return obj


MSG = {
    "denied": "手机上拒绝了。/ Declined on the phone.",
    "timeout": "手机上没人处理，卡片已过期。/ Nobody answered on the phone; the card expired.",
    "gone": "命令提前结束，卡片已撤回（sudo 卡要这条命令一直等到手机处理）。请用 10 分钟的 shell 超时重新运行。/ The command ended "
            "early, so the card was withdrawn (a sudo card needs its command to wait for the phone). Run it again with a 10-minute shell timeout.",
    "stopped": "已急停，没有发卡片。/ Agent J is stopped; no card was sent.",
    "locked": "管理员密码连续输错，已暂时锁定 {secs} 秒。/ Too many wrong passwords; locked for {secs} s.",
    "bad_password": "管理员密码错了 3 次，这张卡已结束。/ Wrong password three times; this card ended.",
    "no_device": "没有能批准的已配对手机。/ No paired phone that can approve.",
    "busy": "已经有太多张卡在等手机处理。/ Too many cards are already waiting on the phone.",
    "group_source": "这一轮来自 Telegram 群成员，不是主人本人，不能向主人要批准，没有发卡片。/ This turn came from a Telegram group member, not the owner; no card was sent.",
    "unavailable": "Agent J 没在运行（agentj service status）。/ Agent J is not running.",
    "local_auth_required": LOCAL_AUTH_HINT,
    "no_sudo": "这台Agent J 主机上没有 sudo。/ No sudo on the Agent J host.",
    "failed": "没有完成：{why}。/ Not done: {why}.",
    "refused": "请求不合格：{detail}。/ Request refused: {detail}.",
}
# P73: what a secret card's outcome means, worded so the Agent tells the owner the right thing.
SECRET_MSG = {
    "gone": "卡片被撤回了：Agent J 服务停止或重启，手机上的提交没有保存。（命令被中断不会再撤回密钥卡。）请重新运行同一条命令，发一张新卡。"
            "/ The card was withdrawn because Agent J stopped or restarted; nothing was saved. (An interrupted command no longer "
            "withdraws a secret card.) Run the same command again for a new card.",
    "denied": "主人在手机上点了「不提供」，没有保存。不要反复追问；先问主人要不要重新发卡。/ The owner tapped \u201cDon't provide\u201d "
              "on the phone; nothing was saved. Do not keep asking; ask the owner before sending a new card.",
    "timeout": "10 分钟内手机上没人提交，卡片已过期，什么都没保存。/ Nobody submitted on the phone within 10 minutes; the card "
               "expired and nothing was saved.",
    "pending": "卡片还在手机上等主人处理（还剩 {secs} 秒）。稍后再运行 `agentj secret result {id}`，或加 --wait 等结果。/ Still waiting "
               "on the phone ({secs} s left). Run `agentj secret result {id}` later, or add --wait.",
    "unknown": "没有这张卡的记录（id 不对，或记录已被新卡挤掉）。/ No record of that card.",
}
EXIT_CARD = 125
EXIT_PENDING = 75


def _say(res: dict, kind: str = "") -> str:
    if res.get("why") == "local_auth_required":
        return LOCAL_AUTH_HINT
    table = {**MSG, **SECRET_MSG} if kind in ("secret", "secret_out") else MSG
    return table.get(res.get("result"), str(res.get("result"))).format(
        secs=res.get("secs", ""), why=res.get("why", ""), detail=res.get("detail") or res.get("why") or "", id=res.get("id", ""))


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


def _secret_report(res: dict, as_json: bool) -> int:
    """Print a secret card's outcome (never a value) → exit code: 0 saved · 3 saved but the check failed · 75 pending · 125."""
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    elif res.get("result") == "saved":
        r = res["receipt"]
        v = {"ok": "校验通过 / check passed", "fail": f"校验失败 / check failed ({r.get('detail')})",
             "skipped": "未校验 / not checked"}[r["verify"]]
        print(f"SECRET_SAVED: {r['name']} → {r['dest']} · {r['length']} chars · {r['fingerprint']} · {v}")
    else:
        print(f"SECRET_RESULT: {res.get('result')} — {_say(res, 'secret')}", file=sys.stderr)
    if not as_json and (res.get("key_hint") or (res.get("receipt") or {}).get("key_hint")) == "short":
        from .keyhint import HINT
        print(f"KEY_HINT: {HINT['zh']} / {HINT['en']}", file=sys.stderr)
    if res.get("result") == "pending":
        return EXIT_PENDING
    if res.get("result") != "saved":
        return EXIT_CARD
    return 0 if res["receipt"]["verify"] != "fail" else 3


def cmd_secret(a) -> int:
    from .state import State
    if a.action == "log":
        return cmd_log(a)
    if a.action == "send":
        from . import secret_out
        return secret_out.cmd_send(a)
    if a.action == "result":
        res = client_request(State(), {"t": "secret_result", "id": a.id or "", "wait": bool(a.wait)})
        if res.get("result") == "list":
            if a.json:
                print(json.dumps(res, ensure_ascii=False))
                return 0
            for r in res.get("items") or []:
                when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("at", 0)))
                print(f"{when}  {r.get('id')}  {r.get('kind', '?'):10}  {r.get('result', '?'):9}  {r.get('name', '')}")
            if not res.get("items"):
                print("（没有记录 / no entries）")
            return 0
        if res.get("kind") == "secret_out":
            from . import secret_out
            return secret_out.report(res, a.json)
        return _secret_report(res, a.json)

    def on_card(c):
        print(f"SECRET_CARD: {c['id']} — 已发到主人手机，最长等 {c.get('ttl', TTL['secret']) // 60} 分钟。若本命令被中断（超时/被杀），"
              f"卡片仍然有效，稍后运行 `agentj secret result {c['id']}` 查看结果（加 --wait 可等）。/ Card sent to the owner's phone. "
              f"If this command is interrupted the card stays valid; run `agentj secret result {c['id']}` later.",
              file=sys.stderr, flush=True)
    res = client_request(State(), {"t": "secret", "name": a.name, "purpose": a.purpose, "dest": a.dest,
                                   "verify_url": a.verify_url or "", "verify_header": a.verify_header or "",
                                   "verify_cmd": a.verify_cmd or "", "cwd": os.getcwd()}, on_card=on_card)
    return _secret_report(res, a.json)


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
                                    "there, sealed to the Agent J host and given to `sudo -S -k` on stdin. You only get the output.")
    sp.add_argument("--why", required=True, help="这条命令是做什么用的，用大白话写（手机上第一行显示；至少 6 个汉字或 12 个英文字符，"
                                                  "不能只是重复命令）/ what the command is for, in plain words (shown first on the "
                                                  "phone; ≥ 6 CJK or 12 Latin characters, not the command repeated)")
    sp.add_argument("--effect", default="", help="会改什么 / what it changes (shown on the phone)")
    sp.add_argument("--timeout", type=int, default=CMD_TIMEOUT, help=f"命令最长运行秒数（≤ {CMD_TIMEOUT_MAX}）")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("command", nargs="+", help="-- <command> [args…]")
    sp.set_defaults(fn=lambda a: sys.exit(cmd_sudo(a)))
    sc = sub.add_parser("secret", help="request：请手机上的人把一个 API Key 贴进来，直接存进文件（Agent 看不到值）· send：把主人自己的"
                                       "密钥/配置发到他的手机，Face ID 后领取 · result：查看一张卡的结果 · log / request: the human pastes a "
                                       "key on the phone, saved to a file · send: hand the owner their own secret on the phone (Face ID) · "
                                       "result: a card's outcome · log",
                        description="request waits for the phone (≤ 10 min) but the card does not depend on it: if the command is "
                                    "interrupted, run `agentj secret result <id>` later. send returns at once; the value never "
                                    "appears in its output.")
    sc.add_argument("action", choices=["request", "send", "result", "log"])
    sc.add_argument("id", nargs="?", help="result：卡片 id（request/send 打印的）；不写 = 最近十张")
    sc.add_argument("--name", help="request：变量名，如 ELEVENLABS_API_KEY · send：手机上显示的名字，如 'Shadowrocket SS 链接'")
    sc.add_argument("--purpose", help="用途（手机上原样显示）")
    sc.add_argument("--dest", help="存到哪：env:<文件>[#KEY]（.env 一行）或 file:<路径>（整个文件）")
    sc.add_argument("--verify-url", dest="verify_url", help="只读校验：GET 这个 https 地址，2xx = 有效")
    sc.add_argument("--verify-header", dest="verify_header", help="校验请求头模板，默认 'Authorization: Bearer {value}'")
    sc.add_argument("--verify-cmd", dest="verify_cmd", help="已禁用；请使用 --verify-url（不运行沙箱外命令）")
    sc.add_argument("--file", help="send：把这个文件（≤ 256 KiB）交给主人，手机上可下载")
    sc.add_argument("--value-from", dest="value_from", help="send：一段文本，从 env:<变量名> 或 file:<路径> 读（不要写在命令行上）")
    sc.add_argument("--ttl", type=int, default=None, help="send：多少秒内可领（30–600，默认 600）")
    sc.add_argument("--wait", action="store_true", help="result / send：等到有结果（领取、过期或拒绝）")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=lambda a: sys.exit(cmd_secret(a)))
