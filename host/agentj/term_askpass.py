"""P408 (ADR-P408-terminal C2 N1 N4): `sudo -A` inside the web terminal asks serve, never a file, for the password.

Every open writes a fresh helper (`<state>/terminal/ap-<random>/<random>.py`, dir 0700, file 0700). R3 (H-1): it runs under
`-IS` (no site, no .pth, no user / env paths) by a SYSTEM interpreter (`safe_python`: its real path, its parents and its stdlib
+ lib-dynload dirs root-owned and not group/other-writable) — never Agent J's venv, which the fenced Agent can write (F14);
none found → sudo in the terminal says so and asks nothing and a socket `<state>/terminal/askpass.sock` (0600). The helper sends the terminal's token (only in
that shell's environment) and copies one answer line to its stdout — sudo's pipe. serve hands the password out only when
ALL of `check_peer` hold for the connecting process (SO_PEERCRED): same uid; the token; its cmdline names exactly this
helper file and the file's inode / mtime / ctime / SHA-256 are unchanged; its parent is `Name: sudo` with effective uid 0 (the
cmdline path is only a hint — a root process's argv cannot be rewritten by the user, but its name is what we trust); peer
and parent are in the shell's session (sid = the shell's pid). The caller adds the last rule: a phone line is running.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import os
import pathlib
import re
import secrets
import shutil
import subprocess

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import controls, passkey, term_mac, term_store as store, wire
from .term_shell import _sha, local_ok

TOKEN_ENV = "AGENTJ_TERM_TOKEN"
SUDO_TTL = 60                      # s the phone has for 「允许 sudo」
# R3: sudo's own words in English and zh_CN only; another locale's wording is not recognised → the cache is kept (fail closed:
# sudo just fails again, nothing is deleted on a guess).
SUDO_WRONG = re.compile(r"Sorry, try again|incorrect password|对不起，请重试|密码错误")
_HEX16 = re.compile(r"[0-9a-f]{16}")

HELPER = '''#!{py} -IS
import os, socket, sys
def main():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(120)
    try:
        s.connect({sock!r})
        s.sendall((os.environ.get({env!r}, "") + "\\n").encode())
        buf = bytearray()
        while len(buf) < 9000:
            b = s.recv(4096)
            if not b:
                break
            buf += b
    except OSError:
        return 1
    finally:
        s.close()
    if buf[:1] != b"+":
        return 1
    os.write(1, bytes(buf[1:]))
    for i in range(len(buf)):
        buf[i] = 0
    return 0
sys.exit(main())
'''


NO_PYTHON = ("这台电脑上找不到系统自带、只有管理员能改的 python3，网页终端里的 sudo 不能安全地取用记住的密码。/ No system python3 "
             "that only an administrator can change: sudo in the web terminal cannot use the remembered password safely.")
_PY_DIRS = ("/usr/bin", "/bin", "/usr/local/bin")


def _root_owned(path: str, chain: bool = True) -> bool:
    """`path` (and every parent) belongs to root and is not group / other writable."""
    p = pathlib.Path(path)
    for q in [p, *p.parents] if chain else [p]:
        try:
            st = os.stat(q)
        except OSError:
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
    return True


def python_ok(path: str) -> str | None:
    """The real path of `path` when it is a root-owned interpreter whose stdlib and lib-dynload are root-owned, else None."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    if not os.path.isfile(real) or not _root_owned(real) or any(c.isspace() for c in real) or len(real) > 200:
        return None
    try:
        r = subprocess.run([real, "-I", "-S", "-c", "import os, _socket; print(os.path.dirname(os.__file__)); "
                            "print(os.path.dirname(getattr(_socket, '__file__', '') or os.__file__))"],
                           capture_output=True, text=True, timeout=10, env={}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    dirs = r.stdout.split()
    if r.returncode != 0 or len(dirs) != 2 or not all(os.path.isabs(d) and _root_owned(d) for d in dirs):
        return None
    return real


def safe_python(candidates=None) -> str | None:
    """A system python3 for the helper (R3 H-1); None when there is none."""
    if candidates is None and term_mac.DARWIN:
        candidates = term_mac.PYTHONS                # P437: the Command Line Tools' Python, never the /usr/bin stub
    for c in candidates or [os.path.join(d, n) for d in _PY_DIRS for n in ("python3",)]:
        real = python_ok(c)
        if real:
            return real
    return None


class Helper:
    """One open's askpass helper file (N1)."""

    def __init__(self, parent: pathlib.Path, sock: pathlib.Path, py: str):
        if not py or any(c.isspace() for c in py) or len(py) > 200:
            raise OSError("interpreter path unusable in a #! line")
        self.dir = parent / ("ap-" + secrets.token_hex(8))
        self.dir.mkdir(mode=0o700)
        self.path = self.dir / (secrets.token_hex(8) + ".py")
        data = HELPER.format(py=py, sock=str(sock), env=TOKEN_ENV).encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        st = os.stat(self.path, follow_symlinks=False)
        self.ino, self.mtime, self.ctime, self.sha = st.st_ino, st.st_mtime_ns, st.st_ctime_ns, hashlib.sha256(data).hexdigest()

    def intact(self) -> bool:
        try:
            st = os.stat(self.path, follow_symlinks=False)
            with open(self.path, "rb") as f:
                sha = hashlib.sha256(f.read(65536)).hexdigest()
        except OSError:
            return False
        return st.st_ino == self.ino and st.st_mtime_ns == self.mtime and st.st_ctime_ns == self.ctime and \
            hmac.compare_digest(sha, self.sha)

    def remove(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------- /proc
def _mac(proc: str) -> bool:
    """P437: macOS has no /proc — the real "/proc" there means term_mac (libproc / sysctl); a test's fake dir stays a dir."""
    return proc == "/proc" and term_mac.DARWIN


def proc_info(pid: int, proc: str = "/proc") -> dict | None:
    """{"ppid", "sid", "name", "euid", "argv"} of a live process, or None. Linux / WSL /proc layout; macOS: term_mac."""
    if _mac(proc):
        return term_mac.proc_info(pid)
    try:
        with open(f"{proc}/{pid}/stat", "rb") as f:
            stat = f.read().decode("utf-8", "replace")
        with open(f"{proc}/{pid}/status", "rb") as f:
            status = f.read().decode("utf-8", "replace")
        with open(f"{proc}/{pid}/cmdline", "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0")[:-1] or []]
    except (OSError, TypeError):
        return None
    try:
        rest = stat[stat.rindex(")") + 2:].split()
        ppid, sid = int(rest[1]), int(rest[3])
        name = next(ln.split(":", 1)[1].strip() for ln in status.splitlines() if ln.startswith("Name:"))
        euid = int(next(ln.split()[2] for ln in status.splitlines() if ln.startswith("Uid:")))
    except (ValueError, IndexError, StopIteration):
        return None
    return {"ppid": ppid, "sid": sid, "name": name, "euid": euid, "argv": argv}


def check_peer(pid, uid, token: str, want_token: str, shell_pid: int, helper: Helper, proc: str = "/proc"):
    """→ (None, sudo's pid) when the peer may get the password, else (why, None). why: uid · token · peer · helper · file ·
    parent · session."""
    if uid != os.getuid():
        return "uid", None
    if not isinstance(token, str) or not want_token or not hmac.compare_digest(token.encode(), want_token.encode()):
        return "token", None
    me = proc_info(pid, proc) if isinstance(pid, int) and pid > 0 else None
    if me is None:
        return "peer", None
    if str(helper.path) not in me["argv"][1:3]:
        return "helper", None
    if not helper.intact():
        return "file", None
    parent = proc_info(me["ppid"], proc)
    if parent is None or parent["name"] != "sudo" or parent["euid"] != 0:
        return "parent", None
    if me["sid"] != shell_pid or parent["sid"] != shell_pid:
        return "session", None
    return None, me["ppid"]


def in_session(shell_pid: int, token: str, proc: str = "/proc") -> list[int]:
    """M2 sweep: this user's processes whose session is the shell's, or whose environment carries the token."""
    if _mac(proc):
        return term_mac.in_session(shell_pid, token, TOKEN_ENV + "=" + token)
    out, mine, needle = [], os.getuid(), (TOKEN_ENV + "=" + token).encode()
    for d in os.listdir(proc):
        if not d.isdigit() or int(d) == os.getpid():
            continue
        pid = int(d)
        with contextlib.suppress(OSError):
            if os.stat(f"{proc}/{d}").st_uid != mine:
                continue
            info = proc_info(pid, proc)
            if info and info["sid"] == shell_pid:
                out.append(pid)
                continue
            with open(f"{proc}/{d}/environ", "rb") as f:
                if token and needle in f.read().split(b"\0"):
                    out.append(pid)
    return out


# ---------------------------------------------------------------- the Desk's sudo half (terminal.Desk mixes this in)
class SudoDesk:
    """`term_sudo` → Face ID (+ PRF) → `term_sudo_ok`: the password once to the helper, then zeroed (C2). A second askpass
    call from the same sudo = the password was refused; the cache is deleted only after sudo's own wrong-password message
    (N4), never on a timeout or an interrupted prompt."""
    async def _askpass(self, x, rd, wr) -> None:
        from .shared_hook import peer_pid
        buf = None
        try:
            pid, uid = peer_pid(wr.get_extra_info("socket"))
            if uid is None and term_mac.DARWIN:          # P437: macOS LOCAL_PEERPID gives no uid — LOCAL_PEERCRED does
                uid = term_mac.peer_uid(wr.get_extra_info("socket"))
            token = (await asyncio.wait_for(rd.readline(), 5)).decode("utf-8", "replace").strip()
            if x is not self.term or x.closing or x.proc is None:
                why, spid = "closed", None
            else:
                why, spid = await asyncio.to_thread(check_peer, pid, uid, token, x.token, x.proc.pid, x.helper, self.proc)
            if not why and not x.running:
                why = "idle"
            if why:
                self.st.log("term_askpass", device=x.device, result="refused", reason=why)
            else:
                buf = await self._sudo_pw(x, spid)
            out = bytearray(b"+") + buf + b"\n" if buf else bytearray(b"-")
            try:
                wr.write(out)
                await wr.drain()
            finally:
                from .elevate import wipe
                wipe(out)
        except Exception as ex:  # noqa: BLE001 — never the value
            from . import bg
            bg.swallowed(self.st.log, "term_askpass_failed", ex)
        finally:
            if buf is not None:
                from .elevate import wipe
                wipe(buf)
            with contextlib.suppress(Exception):
                wr.close()

    async def _sudo_pw(self, x, spid: int):
        n_seen = x.seen.get(spid, 0) + 1
        x.seen[spid] = n_seen
        if n_seen == 1:
            # P414 (R2-N3): only what arrives after THIS sudo first asked may count as its message — output alone (a
            # command's own "Sorry, try again") never deletes the cache; the signal is sudo asking a second time.
            x.recent = ""
        if n_seen > 1:                                       # sudo asks again: the password it got was refused …
            for _ in range(20):
                if SUDO_WRONG.search(x.recent):
                    break
                await asyncio.sleep(0.1)
            if SUDO_WRONG.search(x.recent):                  # … but only sudo's own message deletes the cache (N4)
                await asyncio.to_thread(store.forget_kid, self.st, x.device)   # P414 (L2): that kid and every wrap on it
                if x.pw:
                    from .elevate import wipe
                    wipe(x.pw)
                    x.pw = None
                await self._frame(x, "\n[电脑密码可能改过了：已删除记住的密码，下次打开终端会重新要密码 / need_password]\n", False)
                self._prefs()
            return None
        info = await asyncio.to_thread(proc_info, spid, self.proc) or {"argv": []}
        from .privacy import owner_text
        argv = [owner_text(a)[:500] for a in info["argv"][:64]]
        cmd = [a for a in argv[1:] if a != "-A"]
        differs = " ".join(cmd) not in x.line
        rid, n = secrets.token_hex(8), secrets.token_hex(16)
        priv = X25519PrivateKey.generate()
        from .elevate import _raw_pub
        digest = _sha("\0".join(argv))
        m = store.meta(self.st, x.device)
        pk = self.st.passkey_of_device(x.device)
        vn = secrets.token_bytes(32) if m and m["mode"] == "prf" and pk and m["cred"] == pk["id"] else None   # P414 (M3)
        fut = asyncio.get_running_loop().create_future()
        x.sudo[rid] = {"n": n, "priv": priv, "digest": digest, "fut": fut, "argv": argv}
        msg = {"t": "term_sudo", "id": x.id, "rid": rid, "argv": argv, "line": owner_text(x.line), "differs": differs, "n": n,
               "fa_ch": wire.b64u(passkey.elevate_challenge(self.h.channel, x.device, x.id, "term_sudo", n, digest)),
               "salt": wire.b64u(m["salt"]) if m and m["mode"] == "prf" else "", "epk": wire.b64u(_raw_pub(priv)),
               **({"vn": wire.b64u(vn)} if vn else {})}
        self._log("term_sudo", x.device, "asked", obj={"id": x.id, "rid": rid})
        x.sudo_hold = getattr(x, "sudo_hold", 0) + 1     # P414 (H3): nothing until the answer enters the Agent's transcript
        try:
            await self.h.send_app(x.s, msg)
            try:
                prf = await asyncio.wait_for(fut, SUDO_TTL)
            except asyncio.TimeoutError:
                prf = None
            finally:
                x.sudo.pop(rid, None)
        finally:
            x.sudo_hold -= 1
        if prf is None:
            return None
        try:
            return await self._sudo_take(x, rid, prf, vn)
        finally:
            from .elevate import wipe
            wipe(prf)                                        # R3: the PRF output lives in a bytearray, zeroed here

    async def _sudo_take(self, x, rid, prf: bytearray, vn: bytes | None = None):
        pk = self.st.passkey_of_device(x.device)
        if x.mode == "session" or not pk:
            pw = bytearray(x.pw) if x.pw else None
        elif x.mode == "local" and not local_ok(self.h):
            await asyncio.to_thread(store.drop_local, self.st)
            pw = None
        else:
            pw = await asyncio.to_thread(store.load, self.st, self.h.channel, x.device, pk["id"], prf or None, vn)
        if pw is None:
            await self._frame(x, "\n[sudo：这台电脑上没有可用的电脑密码，sudo 不会运行 / no computer password available]\n", False)
            return None
        x.recent = ""
        self._log("term_sudo", x.device, "allowed", obj={"id": x.id, "rid": rid})
        return pw

    async def on_sudo_answer(self, x, s, obj: dict) -> None:
        rid = obj.get("rid")
        w = x.sudo.get(rid) if isinstance(rid, str) and _HEX16.fullmatch(rid) else None
        if w is None or w["fut"].done():
            return
        if obj["t"] == "term_sudo_no":
            self._log("term_sudo", s.device, "denied", obj={"id": x.id, "rid": rid})
            return w["fut"].set_result(None)
        why = "replay" if obj.get("n") != w["n"] else controls.check(self.st, self.h.nonces, self.h.channel, s.device, obj,
                                                                      "term_sudo", {"id": x.id, "rid": rid})
        why = why or self._assert(s, obj, x.id, "term_sudo", w["n"], w["digest"])
        prf = bytearray()
        if not why and obj.get("pct") is not None:
            # the same seal rule as term_open: id = nonce = n, digest = sha256(the control object text)
            prf = self._open_prf(w["priv"], obj, s.device, w["n"], w["n"], controls.object_digest("term_sudo", {"id": x.id, "rid": rid}))
            why = None if prf is not None else "shape"
        self._log("term_sudo", s.device, "refused:" + why if why else "ok", {"id": x.id, "rid": rid}, sig=obj.get("sig"),
                  n=obj.get("n"), ts=obj.get("ts"))
        if why:
            from .elevate import wipe
            wipe(prf)
            self.st.log("control_refused", device=s.device, action="term_sudo", reason=why)
            return
        w["fut"].set_result(prf)

