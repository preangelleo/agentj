"""F17 advanced (P46): the admin helper — type the password once, afterwards a sudo card only needs 「同意」 on a paired phone.

`agentj sudo-helper install` asks for ONE ordinary sudo card (the password typed on the phone, §11). That card's command:
1. copies three files the host just generated into a root-owned staging folder (a snapshot the user can no longer change),
2. checks each copy against the SHA-256 printed in the command itself (so the phone shows, and signs, exactly what goes in),
3. installs them: the helper `/usr/local/libexec/agentj-elevate-<uid>` (root, 0755, Python standard library only, run as
   `/usr/bin/python3 -I -S`, so nothing the user owns is imported), the allowlist `/etc/agentj/elevate-keys-<uid>.json` (root, 0644:
   this host's channel + the Ed25519 approval keys of the phones paired NOW), and a sudoers drop-in that lets this one user run
   exactly that helper command line without a password (checked with `visudo -cf` before it is moved into place).

Why this is not "passwordless sudo for the Agent": the Agent is the same OS user and CAN run the helper, but the helper runs
nothing without a fresh Ed25519 signature from a phone in its root-owned allowlist over `agentjarvis-elevate-v1 …` for THIS
argv / why / effect (it recomputes the digest itself), a timestamp within ±120 s of its own clock and a nonce it has never seen
(root-owned ledger, 10 min). The phone's private key is a non-extractable WebCrypto key; the Agent never holds it. A phone
removed from Agent J must also leave the helper: `agentj sudo-helper sync` (one password card) or `uninstall`.

Not done here (GAP, reported): Face ID / WebAuthn user verification before 「同意」; Windows (UAC cannot be fed remotely;
only a service-style helper would work); a macOS privileged helper (SMJobBless / SMAppService) — on macOS the same sudoers
helper is generated (`root:wheel`, `shasum -a 256`), covered by static tests only.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import pwd
import shlex
import sys
import tempfile

from . import wire

# Each installed seat owns its helper, allowlist, nonce ledger and sudoers entry.
# Do not remove legacy unsuffixed files: another installed user may still use them.
UID = os.getuid()
HELPER = f"/usr/local/libexec/agentj-elevate-{UID}"
KEYS = f"/etc/agentj/elevate-keys-{UID}.json"
STATE = f"/var/lib/agentj-elevate-{UID}"
SUDOERS = f"/etc/sudoers.d/agentj-elevate-{UID}"
PYTHON = "/usr/bin/python3"
HELPER_CMD = [PYTHON, "-I", "-S", HELPER]

# The helper itself: standalone, standard library only (it runs as root; it must not import anything the user can write).
HELPER_SOURCE = r'''#!/usr/bin/python3 -I -S
"""Agent J admin helper (F17). Runs ONE command as root when a paired phone signed it. Installed by `agentj sudo-helper`.
stdin: {"channel","device","id","n","ts","argv","why","effect","cwd","sig"} → the command's stdout / stderr / exit status."""
import fcntl, hashlib, json, os, shlex, subprocess, sys, time

KEYS = "/etc/agentj/elevate-keys.json"
STATE = "/var/lib/agentj-elevate"
CONTEXT = "agentjarvis-elevate-v1"

# ---- Ed25519 verification, RFC 8032 §5.1.7 (cofactorless), extended coordinates
P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

def _recover_x(y, sign):
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P:
        x = x * SQRT_M1 % P
    if (x * x - xx) % P:
        raise ValueError("not on curve")
    if x == 0 and sign:
        raise ValueError("bad sign")
    return P - x if x & 1 != sign else x

def _decode(b):
    if len(b) != 32:
        raise ValueError("length")
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= P:
        raise ValueError("range")
    x = _recover_x(y, sign)
    return (x, y, 1, x * y % P)

def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = p[3] * 2 * D * q[3] % P
    d = p[2] * 2 * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)

def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q

_BY = 4 * pow(5, P - 2, P) % P
_B = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % P)

def ed25519_verify(pub, msg, sig):
    try:
        if len(sig) != 64:
            return False
        a, r = _decode(pub), _decode(sig[:32])
        s = int.from_bytes(sig[32:], "little")
        if s >= L:
            return False
        h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % L
        x, y = _mul(s, _B), _add(r, _mul(h, a))
        return (x[0] * y[2] - y[0] * x[2]) % P == 0 and (x[1] * y[2] - y[1] * x[2]) % P == 0
    except ValueError:
        return False

def unb64u(s):
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def _h(s):
    return hashlib.sha256(s.encode()).hexdigest()

def fail(why, code=126):
    sys.stderr.write("agentj-elevate: refused (" + why + ")\n")
    sys.exit(code)

def main():
    if os.geteuid() != 0:
        fail("not root")
    try:
        req = json.loads(sys.stdin.read(70000))
        keys = json.load(open(KEYS))
        argv, why, effect = req["argv"], req["why"], req.get("effect") or ""
        if not (isinstance(argv, list) and argv and all(isinstance(x, str) and x and "\x00" not in x for x in argv)):
            fail("argv")
        if req["channel"] != keys["channel"]:
            fail("channel")
        pub = unb64u(keys["devices"][req["device"]])
        ts, n = int(req["ts"]), str(req["n"])
        if len(n) != 32 or any(c not in "0123456789abcdef" for c in n):
            fail("nonce")
    except (ValueError, KeyError, TypeError, OSError):
        fail("shape")
    if abs(time.time() * 1000 - ts) > 120000:
        fail("stale")
    digest = _h("\n".join([CONTEXT, "sudo", _h(shlex.join(argv)), _h(why), _h(effect)]))
    msg = "\n".join([CONTEXT, req["channel"], req["device"], req["id"], "sudo", "allow", n, str(ts), digest, "-"]).encode()
    if not ed25519_verify(pub, msg, unb64u(req["sig"])):
        fail("signature")
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    with open(os.path.join(STATE, "nonces.lock"), "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        path = os.path.join(STATE, "nonces.json")
        try:
            seen = json.load(open(path))
        except (OSError, ValueError):
            seen = {}
        now = time.time()
        seen = {k: v for k, v in seen.items() if now - v < 600}
        if n in seen:
            fail("replay")
        seen[n] = now
        with open(path + ".tmp", "w") as f:
            json.dump(seen, f)
        os.replace(path + ".tmp", path)
    with open(os.path.join(STATE, "log"), "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "device": req["device"], "id": req["id"],
                            "argv_sha256": hashlib.sha256(json.dumps(argv, ensure_ascii=False).encode()).hexdigest()}) + "\n")
    cwd = req.get("cwd") if isinstance(req.get("cwd"), str) and os.path.isdir(req.get("cwd")) else "/"
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/root"}
    try:
        sys.exit(subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL).returncode)
    except OSError as e:
        fail("exec: " + e.__class__.__name__, 127)

if __name__ == "__main__":
    main()
'''


# Bind the standalone source to this installer UID; tests execute these exact bytes.
HELPER_SOURCE = HELPER_SOURCE.replace('KEYS = "/etc/agentj/elevate-keys.json"', f'KEYS = "{KEYS}"').replace('STATE = "/var/lib/agentj-elevate"', f'STATE = "{STATE}"')


def is_mac() -> bool:
    return platform.system() == "Darwin"


def sudoers_text(user: str) -> str:
    if not user or not all(c.isalnum() or c in "._-" for c in user):
        raise ValueError("unusual user name")
    return ("# Agent J admin helper (F17) — written by `agentj sudo-helper install`. The helper runs nothing without a fresh\n"
            "# signature from a paired phone. Remove: `agentj sudo-helper uninstall` (or delete this file as root).\n"
            f"{user} ALL=(root) NOPASSWD: {' '.join(HELPER_CMD)}\n")


def keys_doc(st) -> dict:
    devs = {did: v["sk"] for did, v in st.devices().items() if v.get("sk")}
    return {"version": 1, "channel": st.config()["channel"], "devices": devs}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def files(st, user: str) -> dict[str, bytes]:
    return {"agentj-elevate": HELPER_SOURCE.encode(), "elevate-keys.json": (json.dumps(keys_doc(st), indent=1) + "\n").encode(),
            "sudoers": sudoers_text(user).encode()}


def install_script(src: str, hashes: dict[str, str], mac: bool | None = None, keys_only: bool = False) -> str:
    """The one shell line the sudo card shows. Copies into a root-owned staging folder FIRST, checks the hashes printed here,
    then installs — the user-owned temp folder can change after the human looked, the staged root copy cannot."""
    mac = is_mac() if mac is None else mac
    grp = "wheel" if mac else "root"
    check = "shasum -a 256 -c" if mac else "sha256sum -c"
    stage = STATE + "/stage-" + os.urandom(8).hex()
    q = shlex.quote
    names = ["elevate-keys.json"] if keys_only else ["agentj-elevate", "elevate-keys.json", "sudoers"]
    parts = ["set -eu", f"umask 077", f"rm -rf {stage}", f"install -d -m 0700 -o root -g {grp} {STATE} {stage}"]
    for n in names:
        parts.append(f"install -m 0600 -o root -g {grp} {q(src + '/' + n)} {stage}/{n}")
    parts.append("cd " + stage)
    parts.append("printf '%s\\n' " + " ".join(q(f"{hashes[n]}  {n}") for n in names) + f" | {check} --quiet -")
    parts.append(f"install -d -m 0755 -o root -g {grp} /etc/agentj")
    if not keys_only:
        parts.append(f"install -d -m 0755 -o root -g {grp} /usr/local/libexec")
        parts.append(f"install -m 0755 -o root -g {grp} agentj-elevate {HELPER}")
    parts.append(f"install -m 0644 -o root -g {grp} elevate-keys.json {KEYS}")
    if not keys_only:
        parts.append("visudo -cqf sudoers")
        parts.append(f"install -d -m 0750 -o root -g {grp} /etc/sudoers.d")
        parts.append(f"install -m 0440 -o root -g {grp} sudoers {SUDOERS}")
    parts.append(f"rm -rf {stage}")
    return "; ".join(parts)


def uninstall_script() -> str:
    return f"set -eu; rm -f {SUDOERS} {HELPER} {KEYS}; rm -rf {STATE}"


def status() -> dict:
    """Installed = the helper and its allowlist exist and are root-owned (a non-root process cannot look into
    /etc/sudoers.d; a missing drop-in shows up as `helper_missing` on first use). devices = the phones the helper accepts."""
    out = {"installed": False, "devices": [], "channel": None}
    try:
        for p in (HELPER, KEYS):
            if os.lstat(p).st_uid != 0:
                return out
        d = json.loads(pathlib.Path(KEYS).read_text())
        out.update(installed=True, devices=sorted(d.get("devices") or {}), channel=d.get("channel"))
    except (OSError, ValueError):
        pass
    return out


def helper_for(st, device: str) -> bool:
    s = status()
    return s["installed"] and s["channel"] == st.config()["channel"] and device in s["devices"]


def run(req: dict, timeout: int, sudo: str | None = None, helper_cmd: list[str] | None = None) -> dict:
    """serve → `sudo -n <helper>` with the signed request on stdin (no password anywhere)."""
    import subprocess
    from .elevate import sudo_bin, _cap
    sudo = sudo or sudo_bin()
    if not sudo:
        return {"result": "no_sudo", "code": None, "stdout": "", "stderr": "", "truncated": False}
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LOGNAME", "TZ")}
    env.update(LC_ALL="C", LANG="C")
    try:
        p = subprocess.run([sudo, "-n", "--", *(helper_cmd or HELPER_CMD)], input=json.dumps(req).encode(), capture_output=True,
                           env=env, cwd="/", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        o, t1 = _cap(e.stdout or b"")
        er, t2 = _cap(e.stderr or b"")
        return {"result": "timeout", "code": None, "stdout": o, "stderr": er, "truncated": t1 or t2}
    except OSError:
        return {"result": "no_sudo", "code": None, "stdout": "", "stderr": "", "truncated": False}
    o, t1 = _cap(p.stdout)
    er, t2 = _cap(p.stderr)
    if p.returncode == 126 and b"agentj-elevate: refused" in p.stderr:
        return {"result": "failed", "why": "helper_refused", "code": None, "stdout": "", "stderr": er, "truncated": False}
    if p.returncode == 1 and b"a password is required" in p.stderr:
        return {"result": "failed", "why": "helper_missing", "code": None, "stdout": "", "stderr": "", "truncated": False}
    return {"result": "done", "code": p.returncode, "stdout": o, "stderr": er, "truncated": t1 or t2}


# ---------------------------------------------------------------- CLI: agentj sudo-helper install | sync | uninstall | status
def cmd(a) -> int:
    from .elevate import client_request, EXIT_CARD, _say
    from .state import State
    st = State()
    if a.action == "status":
        s = status()
        print(json.dumps(s) if a.json else (f"已安装 / installed · {len(s['devices'])} 台手机 / phones" if s["installed"]
                                           else "未安装 / not installed — agentj sudo-helper install"))
        return 0
    if a.action == "uninstall":
        argv, why, effect = ["sh", "-c", uninstall_script()], "卸载 Agent J 管理员小助手 / remove the Agent J admin helper", \
            "删除 sudoers 片段和小助手；之后管理员操作又要输密码 / admin cards need the password again"
    else:
        user = pwd.getpwuid(os.getuid()).pw_name
        fs = files(st, user)
        if not keys_doc(st)["devices"]:
            print("没有能批准的已配对手机 / no paired phone with an approval key", file=sys.stderr)
            return EXIT_CARD
        st.perm_dir.mkdir(mode=0o700, exist_ok=True)       # visible to serve AND to a fenced Agent (its /tmp is private)
        src = tempfile.mkdtemp(prefix="helper-", dir=st.perm_dir)
        for n, data in fs.items():
            p = pathlib.Path(src) / n
            p.write_bytes(data)
            p.chmod(0o644)
        os.chmod(src, 0o755)
        keys_only = a.action == "sync"
        argv = ["sh", "-c", install_script(src, {n: sha(d) for n, d in fs.items()}, keys_only=keys_only)]
        n = len(keys_doc(st)["devices"])
        why = (f"更新管理员小助手认可的手机（{n} 台）/ update the phones the admin helper accepts ({n})" if keys_only else
               "安装 Agent J 管理员小助手：以后管理员操作只要在手机上点同意 / install the admin helper: later admin cards need only Approve")
        effect = (f"写入 {KEYS}" if keys_only else
                  f"写入 {HELPER}、{KEYS}、{SUDOERS}（只允许这个小助手免密，且它只认已配对手机的签名）")
    try:
        res = client_request(st, {"t": "sudo", "argv": argv, "why": why, "effect": effect, "cwd": "/", "timeout": 120})
    finally:
        if a.action != "uninstall":
            import shutil
            shutil.rmtree(src, ignore_errors=True)
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "stdout"}, ensure_ascii=False))
    elif res.get("result") == "done" and res.get("code") == 0:
        print("SUDO_HELPER: ok — " + ("已卸载 / removed" if a.action == "uninstall" else "已安装 / installed"))
    else:
        sys.stderr.write(res.get("stderr", ""))
        print(f"SUDO_HELPER: {res.get('result')} — {_say(res) if res.get('result') != 'done' else 'exit ' + str(res.get('code'))}",
              file=sys.stderr)
    return 0 if res.get("result") == "done" and res.get("code") == 0 else EXIT_CARD


def add_parser(sub) -> None:
    sp = sub.add_parser("sudo-helper", help="管理员小助手：install 装一次（手机上输一次密码），之后 sudo 卡只要点同意 · sync · uninstall · status / "
                                            "the admin helper: one password card, then sudo cards need only Approve")
    sp.add_argument("action", choices=["install", "sync", "uninstall", "status"])
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=lambda a: sys.exit(cmd(a)))


def _b64(k: bytes) -> str:
    return wire.b64u(k)
