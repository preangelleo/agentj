"""10-minute same-format admin approval (backlog #116, PROTOCOL §11.1): when the owner approves one `agentj sudo` card WITH
the admin password on a paired phone AND switches on the card's own toggle (off by default), later commands of exactly the
same format run without a new card for up to 600 s.

Allowlist, never a denylist. A command may share a grant only when ALL of these hold (anything else: a card every time):
- the program resolves (PATH, then realpath) to a root-owned file whose every folder is root-owned and not group/other-
  writable, its basename is the name the Agent asked for (no multi-call applet / symlinked alias such as unxz → xz), and that
  basename is in ALLOW below: read / inspect tools that cannot run code or write files from their arguments in the modes
  listed (no shell, interpreter, editor, pager, package manager, archiver in write mode, sed/awk/find/xargs/env/tar/rsync/cp/
  dd/tee/chmod, systemctl start …);
- every option is one the entry lists (exact spelling; combined short flags are split), a value-taking option keeps the
  value it was approved with, and modes that write are refused (gzip without -c/-l/-t, tail -f, dmesg -c, ss -K …);
- the class key = (program realpath, systemctl subcommand, the SET of options with their values, and — for every command
  with path operands — the one approved folder: the folder a file operand sits in, or the folder operand itself). Only
  operands may differ, and later paths must sit in that same folder (signed scope 2026-10-20: 「同目录」 for every program,
  not only the READERS that print contents); commands without paths (systemctl status <unit>, journalctl -u, uptime …) have
  no folder;
- a program that takes files needs at least one file operand (no operand = it would read stdin, where sudo's password went);
  du needs -s and never `/` or a top-level folder;
- every path operand is absolute, has no `..` and no symlink anywhere (path == realpath), is a regular file or folder, sits
  in folders only root can change (so the Agent cannot swap it after the check), and touches nothing sensitive (SENSITIVE*:
  /root, other accounts' homes, /proc /sys /dev /run /boot, /etc/ssh, sudoers, shadow, keys, .env, .ssh, tokens …).

The grant is the approval itself, kept smaller than it:
- created only after the approved command ran with the right password (sudo asked for it exactly once: a NOPASSWD sudo never
  gets it, run_sudo writes it only after its own prompt), only when the card offered it and the phone signed
  `grant:<class hash>` into the Ed25519 decision (elevate.signed_message) — the audit line proves the owner chose it; the
  helper path (approve-only, no password) never creates one;
- bound to the approving device (its approval key must be unchanged at use) and to the Agent session that asked (harness
  kind, a random per-agent-object nonce, native session id);
- auto-runs pin the executable to the resolved path; their timeout is capped by what is left of the window (≤ 600 s);
- a fixed deadline (use never extends it), memory only, the password copy a bytearray zeroed at the end (serve is non-
  dumpable); estop, revoke from any paired phone, unpair, a wrong stored password, serve stop all end it;
- every auto-pass, creation and end is one `elevate.log` line: class, grant id, request id, origin card, device. Never a
  password or the command text.
"""
from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hashlib
import os
import pwd
import re
import secrets
import shutil
import stat
import threading
import time

GRANT_SECS = 600
MAX_AUTO_TIMEOUT = 600
GRANT_CONTEXT = "agentjarvis-elevate-grant-v1"


def _spec(short="", long=(), vshort="", vlong=(), need=(), ops="paths", subs=()):
    """short: no-value letters · long: no-value long options · vshort / vlong: options that take a value (the value becomes
    part of the class) · need: at least one of these must be present (the read-only mode) · ops: what operands are —
    paths (checked) · pattern (grep: the first one is the pattern unless -e) · units (systemctl unit names) · words (ss
    filter words) · none · subs: systemctl's allowed subcommands."""
    return {"short": short, "long": frozenset(long), "vshort": vshort, "vlong": frozenset(vlong), "need": frozenset(need),
            "ops": ops, "subs": frozenset(subs)}


_SUMS = _spec("btz", ("--binary", "--text", "--tag", "--zero"))
_ZREAD = ("-c", "-l", "-t", "--stdout", "--to-stdout", "--list", "--test")
# Why each is safe in the listed modes: it only reads its operands (or kernel / service state) and prints; none of the listed
# options runs a program, opens a pager, reads a list of files from a file, writes a file or deletes one.
ALLOW = {
    "cat": _spec("AbeEnstTuv", ("--show-all", "--number-nonblank", "--number", "--squeeze-blank", "--show-ends",
                                "--show-tabs", "--show-nonprinting")),
    "head": _spec("qvz", ("--quiet", "--silent", "--verbose", "--zero-terminated"), "nc", ("--lines", "--bytes")),
    "tail": _spec("qvz", ("--quiet", "--silent", "--verbose", "--zero-terminated"), "nc", ("--lines", "--bytes")),  # no -f/-F
    "ls": _spec("aAlhdtrSi1Fns", ("--all", "--almost-all", "--human-readable", "--directory", "--inode", "--numeric-uid-gid",
                                  "--size", "--classify")),                                                        # no -R
    "stat": _spec("Lft", ("--dereference", "--file-system", "--terse"), "c", ("--format", "--printf")),
    "du": _spec("shcxkmb", ("--summarize", "--human-readable", "--total", "--one-file-system", "--bytes"), "d",
                ("--max-depth",), need=("-s", "--summarize")),                          # never /, /etc, /var … (below)
    "df": _spec("hHTiklaP", ("--human-readable", "--si", "--print-type", "--inodes", "--local", "--all", "--portability"),
                "t", ("--type",)),
    "wc": _spec("lwcmL", ("--lines", "--words", "--bytes", "--chars", "--max-line-length")),                # no --files0-from
    "file": _spec("bLi", ("--brief", "--dereference", "--mime", "--mime-type")),                       # no -m/-f/-z
    "sha256sum": _SUMS, "sha1sum": _SUMS, "sha512sum": _SUMS, "md5sum": _SUMS, "b2sum": _SUMS,               # no -c
    "grep": _spec("ivclLnhHoqswxEFGPz", ("--ignore-case", "--invert-match", "--count", "--files-with-matches",
                                          "--files-without-match", "--line-number", "--no-filename", "--with-filename",
                                          "--only-matching", "--quiet", "--silent", "--no-messages", "--word-regexp",
                                          "--line-regexp", "--extended-regexp", "--fixed-strings", "--basic-regexp",
                                          "--perl-regexp", "--null-data"),
                  "emABC", ("--regexp", "--max-count", "--after-context", "--before-context", "--context"), ops="pattern"),
    # no -r/-R/-d/-D/-f/--include: a recursive read would walk into folders the operand check never saw
    "zcat": _spec("flqtv", ("--force", "--list", "--quiet", "--test", "--verbose")),
    "gzip": _spec("dcfklqtv", ("--decompress", "--stdout", "--to-stdout", "--force", "--keep", "--list", "--quiet", "--test",
                               "--verbose"), need=_ZREAD),
    "gunzip": _spec("cfklqtv", ("--stdout", "--to-stdout", "--force", "--keep", "--list", "--quiet", "--test", "--verbose"),
                    need=_ZREAD),
    "xz": _spec("dcfklqtv", ("--decompress", "--stdout", "--to-stdout", "--force", "--keep", "--list", "--quiet", "--test",
                              "--verbose"), need=_ZREAD),
    "xzcat": _spec("fqv", ("--force", "--quiet", "--verbose")),
    "bzip2": _spec("dcfkqtvs", ("--decompress", "--stdout", "--force", "--keep", "--quiet", "--test", "--verbose", "--small"),
                   need=("-c", "-t", "--stdout", "--test")),
    "bzcat": _spec("sqv", ("--small", "--quiet", "--verbose")),
    "zstd": _spec("dcfklqtv", ("--decompress", "--stdout", "--force", "--keep", "--list", "--quiet", "--test", "--verbose"),
                  need=("-c", "-l", "-t", "--stdout", "--list", "--test")),                                     # no -o/--rm
    "zstdcat": _spec("fqv", ("--force", "--quiet", "--verbose")),
    "journalctl": _spec("kxraq", ("--no-pager", "--dmesg", "--reverse", "--utc", "--no-hostname", "--list-boots", "--all",
                                  "--quiet", "--catalog"),
                        "unpbSUotg", ("--unit", "--lines", "--priority", "--boot", "--since", "--until", "--output",
                                      "--identifier", "--grep"), ops="none"),               # no -f, --file/-D/--root/--image
    "dmesg": _spec("Tkxt", ("--ctime", "--kernel", "--decode", "--notime", "--nopager"), "l", ("--level",),
                   ops="none"),                                                              # no -c/-C/-n/-w/-H (pager)
    "ss": _spec("tulnpax46semiowr", ("--tcp", "--udp", "--listening", "--numeric", "--processes", "--all", "--unix",
                                     "--summary", "--extended", "--memory", "--info", "--options", "--raw", "--resolve"),
                ops="words"),                                                                # no -K (kill), -D, -F
    "lsof": _spec("nPltUwV", (), "puci", ()),
    "systemctl": _spec("alq", ("--no-pager", "--all", "--full", "--value", "--quiet", "--no-legend", "--failed"),
                       "pnt", ("--property", "--lines", "--type", "--state"), ops="units",
                       subs=("status", "show", "is-active", "is-enabled", "is-failed", "list-units", "list-timers")),
    "findmnt": _spec("lnJaDr", ("--list", "--noheadings", "--json", "--all", "--df", "--raw"), "ot", ("--output", "--types")),
    "lsblk": _spec("flnJabdpr", ("--fs", "--list", "--noheadings", "--json", "--all", "--bytes", "--nodeps", "--paths",
                                 "--raw"), "o", ("--output",), ops="words"),
    "blkid": _spec("p", ("--probe",), "os", ("--output", "--match-tag"), ops="words"),        # no -c (cache file)
    "uptime": _spec("ps", ("--pretty", "--since"), ops="none"),
    "free": _spec("hbkmgtw", ("--human", "--bytes", "--kibi", "--mebi", "--gibi", "--total", "--wide"), ops="none"),  # no -s
}

# Paths that never auto-run (a card every time), checked on the resolved operand and every folder above it.
SENSITIVE_DIRS = ("/root", "/proc", "/sys", "/dev", "/run", "/boot", "/etc/ssh", "/etc/ssl/private", "/etc/sudoers.d",
                  "/etc/NetworkManager/system-connections", "/etc/wireguard", "/etc/credstore", "/etc/credstore.encrypted",
                  "/var/lib/systemd/credential", "/etc/libvirt/secrets", "/var/lib/docker", "/var/lib/containers",
                  "/var/lib/sss", "/var/spool/cron", "/etc/security", "/etc/polkit-1/rules.d", "/home", "/Users",
                  "/private/var/root", "/var/db/sudo", "/var/lib/iwd", "/var/lib/bluetooth", "/var/lib/tailscale",
                  "/var/lib/postgres", "/var/lib/mysql", "/etc/hysteria", "/etc/sing-box", "/etc/xray")
SENSITIVE_NAMES = ("shadow*", "gshadow*", "*sudoers*", "opasswd", "master.passwd", "*.htpasswd", ".htpasswd", ".ssh", "id_*",
                   "*.pem", "*.key", "*_key", "*.env", ".env*", "*.p12", "*.pfx", "*.jks", "*.keytab", "*.kdbx", "*.ovpn",
                   "*secret*", "*credential*", "*token*", "*passw*", ".netrc", ".pgpass", ".git-credentials",
                   "authorized_keys", ".gnupg", "gnupg", "private-keys-v1.d", "openpgp-revocs.d", "secring*", "*.gpg",
                   ".aws", ".kube", ".docker", "wpa_supplicant*", "environ")
# Programs that print what is inside a file: their grant is also bound to the folder of the file the owner approved.
READERS = frozenset({"cat", "head", "tail", "grep", "zcat", "xzcat", "bzcat", "zstdcat", "gzip", "gunzip", "xz", "bzip2",
                     "zstd"})
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:\\-]{0,199}")
_WORD = re.compile(r"[A-Za-z0-9:._=<>!()\[\]*,+%-][A-Za-z0-9:._=<>!()\[\]*,+%/-]{0,199}")


def resolve_exe(argv0: str, path: str | None = None) -> str | None:
    """The program sudo would run, as a real path; None when it cannot be pinned (relative path with a slash, not found)."""
    if not isinstance(argv0, str) or not argv0:
        return None
    if "/" in argv0:
        p = argv0 if os.path.isabs(argv0) else None
    else:
        p = shutil.which(argv0, path=path if path is not None else os.environ.get("PATH", os.defpath))
    if not p:
        return None
    real = os.path.realpath(p)
    return real if os.path.isfile(real) and os.access(real, os.X_OK) else None


def root_safe(real: str) -> bool:
    """Only root can change this file or any folder above it (else the Agent could swap it inside the window)."""
    try:
        cur = real
        while True:
            st = os.stat(cur)
            if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return False
            parent = os.path.dirname(cur)
            if parent == cur:
                return True
            cur = parent
    except OSError:
        return False


def _homes() -> set[str]:
    """Every account's home except the shared roots some system accounts use (/, /var/empty, …)."""
    skip = {"/", "/nonexistent", "/var/empty", "/dev/null", "/usr", "/bin", "/sbin", "/var", "/etc", "/tmp"}
    out = set()
    with contextlib.suppress(Exception):
        for e in pwd.getpwall():
            h = os.path.normpath(e.pw_dir or "/")
            if h not in skip:
                out.add(h)
    return out


def sensitive(path: str) -> bool:
    """`path` (absolute, normalised) is, or is inside, something whose bytes are root's secrets."""
    def inside(p, d):
        return p == d or p.startswith(d.rstrip("/") + "/")
    if any(inside(path, d) for d in SENSITIVE_DIRS) or any(inside(path, h) for h in _homes()):
        return True
    parts = [x.lower() for x in path.split("/") if x]
    return any(fnmatch.fnmatchcase(x, pat.lower()) for x in parts for pat in SENSITIVE_NAMES)


def path_ok(arg: str) -> bool:
    """A path operand an auto-run may read: absolute, no `..` / `//` / symlink anywhere, a regular file or folder, every
    folder above it only root can change (the Agent cannot swap it after this check), nothing sensitive."""
    if not os.path.isabs(arg) or os.path.normpath(arg) != arg or os.path.realpath(arg) != arg or sensitive(arg):
        return False
    try:
        st = os.lstat(arg)
    except OSError:
        return False
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        return False
    return root_safe(os.path.dirname(arg))


def parse(name: str, args: list[str]) -> tuple[str, tuple[str, ...], list[str], list[str]] | None:
    """→ (subcommand, sorted options with values, path operands, other operands); None = not this program's read format."""
    sp = ALLOW.get(name)
    if sp is None:
        return None
    flags, ops = [], []
    i, ended = 0, False
    while i < len(args):
        a = args[i]
        i += 1
        if ended or a == "-" or not a.startswith("-"):
            ops.append(a)
            continue
        if a == "--":
            ended = True
            continue
        if a.startswith("--"):
            k, eq, v = a.partition("=")
            if k in sp["long"] and not eq:
                flags.append(k)
            elif k in sp["vlong"]:
                if not eq:
                    if i >= len(args):
                        return None
                    v, i = args[i], i + 1
                flags.append(k + "=" + v)
            else:
                return None
            continue
        j = 1
        while j < len(a):
            ch = a[j]
            if ch in sp["short"]:
                flags.append("-" + ch)
                j += 1
            elif ch in sp["vshort"]:
                v = a[j + 1:]
                if not v:
                    if i >= len(args):
                        return None
                    v, i = args[i], i + 1
                flags.append("-" + ch + "=" + v)
                break
            else:
                return None
    if sp["need"] and not {f.split("=", 1)[0] for f in flags} & sp["need"]:
        return None
    sub = ""
    if sp["subs"]:
        if not ops or ops[0] not in sp["subs"]:
            return None
        sub, ops = ops[0], ops[1:]
    kind = sp["ops"]
    paths, other = [], []
    if kind == "none":
        if ops:
            return None
    elif kind == "units":
        if not all(_UNIT.fullmatch(o) for o in ops):
            return None
        other = ops
    elif kind == "words":
        if not all(_WORD.fullmatch(o) and not o.startswith("/") for o in ops):
            return None
        other = ops
    elif kind == "pattern" and not any(f.startswith(("-e=", "--regexp=")) for f in flags):
        other, paths = ops[:1], ops[1:]
    else:
        paths = ops
    if kind in ("paths", "pattern") and not paths:
        return None                               # no file operand: it would read stdin (sudo -S feeds the password there)
    if name == "du" and any(p.count("/") <= 1 for p in paths):
        return None                               # du -s / or a top-level folder: a whole-disk walk
    return sub, tuple(sorted(set(flags))), paths, other


def klass(argv: list[str], path: str | None = None, cwd: str = "/") -> tuple | None:
    """(real executable, subcommand, options, folder) when this exact command may share a grant; None = it needs its own
    card. folder: the one folder (realpath) every path operand belongs to — a file's folder, a folder operand itself —
    for every program (P393 #116 review: 「同目录」 binds ls / stat / du / wc / sha256sum … too); "" = no path operands."""
    from . import elevate
    if not argv or elevate.interpreter_command(argv):
        return None
    real = resolve_exe(argv[0], path)
    if not real or os.path.basename(real) != os.path.basename(argv[0]) or not root_safe(real):
        return None
    p = parse(os.path.basename(real), list(argv[1:]))
    if p is None or not all(path_ok(x) for x in p[2]):
        return None
    if sensitive(os.path.normpath(cwd)):
        return None
    folder = ""
    if p[2]:
        dirs = {os.path.realpath(x if os.path.isdir(x) else os.path.dirname(x)) for x in p[2]}
        if len(dirs) != 1:
            return None                           # operands in two folders: a card (the grant covers one folder)
        (folder,) = dirs
    return real, p[0], p[1], folder


def class_hash(k: tuple) -> str:
    """What the phone signs when the owner switches the toggle on: the program, its subcommand, its options, the window."""
    real, sub, flags, folder = k
    return hashlib.sha256("\n".join([GRANT_CONTEXT, real, sub, *flags, "", str(GRANT_SECS), folder]).encode()).hexdigest()


def offer_of(k: tuple) -> dict:
    """The card's `grant` object: the program, the fixed options (and `<folder>/*` for path commands) it covers, the hash."""
    real, sub, flags, folder = k
    shown = " ".join([os.path.basename(real)] + ([sub] if sub else []) + [f.replace("=", " ", 1) if f.startswith("-") and
                                                                           not f.startswith("--") else f for f in flags]
                     + ([os.path.join(folder, "*")] if folder else []))
    out = {"secs": GRANT_SECS, "prog": os.path.basename(real), "fmt": shown, "h": class_hash(k)}
    if folder:
        out["dir"] = folder
    return out


def scope_of(host) -> tuple | None:
    """The Agent session a request belongs to now: (harness kind, the agent object's random nonce, native session id)."""
    agent = getattr(host, "agent", None)
    if agent is None:
        return None
    nonce = getattr(agent, "_aj_grant_nonce", None)
    if not isinstance(nonce, str):
        nonce = secrets.token_hex(16)
        try:
            agent._aj_grant_nonce = nonce
        except (AttributeError, TypeError):
            return None                           # cannot tell this Agent from the next one: no grant at all
    kind = str(getattr(agent, "kind", "?"))
    sid = ""
    with contextlib.suppress(Exception):
        sid = host.st.agent_session(kind) or ""
    return kind, nonce, sid


class Grant:
    __slots__ = ("gid", "key", "exe", "fmt", "device", "sk", "scope", "origin", "expires", "pw")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def public(self, now: float) -> dict:
        return {"g": self.gid, "exe": os.path.basename(self.exe), "fmt": self.fmt, "dev": self.device,
                "left": max(0, int(self.expires - now + 0.999))}


class GrantBook:
    """Owned by elevate.Elevator. Tests may replace `clock` (monotonic seconds) and `path` (the PATH used to resolve)."""

    def __init__(self, el):
        self.el = el
        self.grants: dict[str, Grant] = {}
        self.running: dict[str, threading.Event] = {}
        self.clock = time.monotonic
        self.path: str | None = None

    # ------------------------------------------------------------ lifecycle
    def offer(self, card: dict) -> dict | None:
        """The card's opt-in toggle (program + fixed options + hash), or None (no toggle: this command never shares)."""
        if card.get("kind") != "sudo" or scope_of(self.el.host) is None:
            return None
        k = klass(card["argv"], self.path, card.get("cwd") or "/")
        return offer_of(k) if k is not None else None

    def create(self, card: dict, device: str, sk: bytes, pw: bytearray) -> Grant | None:
        from . import elevate
        k = klass(card["argv"], self.path, card.get("cwd") or "/")
        scope, off = card.get("scope"), card.get("grant")
        if k is None or not off or off["h"] != class_hash(k) or card.get("want_grant") != off["h"] or scope is None \
                or scope != scope_of(self.el.host) or self.el.st.sign_key(device) != sk:
            elevate.wipe(pw)
            return None
        gid = secrets.token_hex(8)
        try:
            for g in [g for g in self.grants.values() if (g.key, g.device, g.scope) == (k, device, scope)]:
                self._end(g, "replaced", push=False)     # a fresh approval of the same class starts a fresh window
            g = Grant(gid=gid, key=k, exe=k[0], fmt=off["fmt"], device=device, sk=sk, scope=scope,
                      origin=card["id"], expires=self.clock() + GRANT_SECS, pw=pw)
            self.grants[g.gid] = g
            elevate.audit(self.el.st, None, kind="sudo", result="grant_on", grant=g.gid, origin=g.origin, device=device,
                          exe=g.exe, cls=off["h"], secs=GRANT_SECS, channel=self.el.host.channel)
            self.el.st.log("elev_grant", grant=g.gid, result="on")
        except BaseException:
            self.grants.pop(gid, None)                    # half-made: no grant, and the kept password copy is zeroed
            elevate.wipe(pw)
            raise
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().call_later(GRANT_SECS + 1, self.sweep)
        self._push()
        return g

    def _end(self, g: Grant, reason: str, push: bool = True) -> None:
        from . import elevate
        if self.grants.pop(g.gid, None) is None:
            return
        elevate.wipe(g.pw)
        elevate.audit(self.el.st, None, kind="sudo", result="grant_off", grant=g.gid, origin=g.origin, device=g.device,
                      exe=g.exe, reason=reason)
        self.el.st.log("elev_grant", grant=g.gid, result="off", reason=reason)
        if push:
            self._push()

    def sweep(self) -> None:
        now = self.clock()
        for g in list(self.grants.values()):
            if now >= g.expires:
                self._end(g, "expired")
            elif self.el.st.sign_key(g.device) != g.sk:
                self._end(g, "unpaired")

    def revoke(self, gid: str | None = None, reason: str = "revoked") -> int:
        hit = [g for g in list(self.grants.values()) if gid is None or g.gid == gid]
        for g in hit:
            self._end(g, reason, push=False)
        if hit:
            self._push()
        return len(hit)

    def drop_device(self, did: str, reason: str = "unpaired") -> None:
        hit = [g for g in list(self.grants.values()) if g.device == did]
        for g in hit:
            self._end(g, reason, push=False)
        if hit:
            self._push()

    def clear(self, reason: str) -> None:
        for ev in self.running.values():
            if reason in ("stopped", "gone"):
                ev.set()
        self.revoke(None, reason)

    def match(self, card: dict) -> Grant | None:
        self.sweep()
        if card.get("kind") != "sudo":
            return None
        k = klass(card["argv"], self.path, card.get("cwd") or "/")
        scope = scope_of(self.el.host)
        if k is None or scope is None:
            return None
        for g in self.grants.values():
            if g.key == k and g.scope == scope:
                return g
        return None

    def items(self) -> list[dict]:
        self.sweep()
        now = self.clock()
        return [g.public(now) for g in self.grants.values()]

    def msg(self, s=None) -> dict:
        return {"t": "elev_grants", "items": self.items()}

    def _push(self) -> None:
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            task = loop.create_task(self.el._to_phones(self.msg))
            self.el.bg.add(task)
            task.add_done_callback(self.el.bg.discard)

    async def on_ready(self, s) -> None:
        if self.grants:
            await self.el.host.send_app(s, self.msg(s))

    async def on_phone(self, s, obj: dict) -> None:
        """`{"t":"elev_revoke"[,"g"]}` from any ready paired device (taking rights away never needs more proof)."""
        if not self.el.st.sign_key(s.device):
            self.el.st.log("elev_refused", device=s.device, reason="no_key")
            return
        gid = obj.get("g")
        if gid is not None and not (isinstance(gid, str) and len(gid) == 16 and all(c in "0123456789abcdef" for c in gid)):
            return
        n = self.revoke(gid, "revoked")
        if not n:
            await self.el.host.send_app(s, self.msg(s))   # stale page: show the truth
        self.el.st.log("elev_revoke", device=s.device, count=n)

    # ------------------------------------------------------------ the auto-pass
    async def auto(self, card: dict, gone: asyncio.Future | None) -> dict | None:
        """Run `card` under a matching grant (no card on the phone); None = no grant applies, show a card as usual."""
        from . import elevate
        g = self.match(card)
        if g is None:
            return None
        if self.el.st.sign_key(g.device) != g.sk:
            self._end(g, "unpaired")
            return None
        left = int(g.expires - self.clock())
        if left < 1:
            self._end(g, "expired")
            return None
        timeout = min(int(card["timeout"]), left, MAX_AUTO_TIMEOUT)   # a root command never outlives the window
        rid = secrets.token_hex(16)
        c = dict(card, id=rid)
        c["digest"] = elevate.shown_digest("sudo", elevate.shown_fields(c))
        cancel = threading.Event()
        self.running[rid] = cancel
        def hang_up(_f):                              # the CLI went away: nobody would get the output
            cancel.set()
        if gone is not None:
            gone.add_done_callback(hang_up)
        pw = bytearray(g.pw)
        argv = [g.exe] + list(card["argv"][1:])     # pinned: the program the owner approved, same basename
        try:
            res = await asyncio.to_thread(elevate.run_sudo, argv, pw, card["cwd"], timeout, self.el.sudo, cancel)
        except Exception:  # noqa: BLE001 — never log exception text (a child may print private data)
            res = {"result": "failed", "why": "execution"}
        finally:
            elevate.wipe(pw)
            self.running.pop(rid, None)
            if gone is not None:
                gone.remove_done_callback(hang_up)
        if res["result"] == "bad_password":          # the stored password no longer works: back to a normal card
            self._end(g, "bad_password")
            return None
        meta = dict(grant=g.gid, origin=g.origin, device=g.device, exe=g.exe, timeout=timeout, channel=self.el.host.channel)
        elevate.audit(self.el.st, c, result=res["result"], reason="grant", code=res.get("code"), **meta)
        self.el.st.log("elev_auto", id=rid, grant=g.gid, result=res["result"])
        out = {"t": "elev_auto", "id": rid, "g": g.gid, "exe": os.path.basename(g.exe), "result": res["result"],
               "left": max(0, int(g.expires - self.clock() + 0.999))}
        if res.get("code") is not None:
            out["code"] = res["code"]
        labels = {"done": ("已完成", "completed") if res.get("code") == 0 else ("执行失败", "failed"),
                  "timeout": ("执行超时", "timed out")}
        zh, en = labels.get(res["result"], ("执行失败", "failed"))
        code = f" ({res['code']})" if res.get("code") is not None else ""
        name = os.path.basename(g.exe)
        self.el.host.agent_notice(f"管理员命令按 10 分钟授权自动执行（{name}）：{zh}{code} · {rid[:8]} / Admin command ran "
                                  f"automatically under your 10-minute approval ({name}): {en}{code} · {rid[:8]}")
        await self.el._to_phones(lambda s: out)
        res = dict(res, id=rid, auto=True)
        return res
