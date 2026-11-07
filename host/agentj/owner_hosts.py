"""Owner-confirmed SSH destinations. Discovery is data, never authority.

Only paired control-v1 + UV replaces the table. Every use re-verifies its signature,
current device, host/seat scope, revision and expiry. A receipt is Host memory, never
an input flag. The supported transport spelling pins the signed public host key in
OpenSSH itself, bypassing user config, proxies, forwarding and writable known_hosts.
No discovery probes, SSH connections or agent-to-agent channel are made here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time
from dataclasses import dataclass

ACTION = "owner_hosts_set"
MAX_ROWS = 12  # keep the state + pinned command templates below the p33 frame limit
MAX_LIFETIME = 90 * 86400
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}\Z")
USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
FIELDS = {"alias", "hostname", "user", "port", "fingerprint", "public_key", "owner", "peer", "expires"}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(public_key):
    """Only the non-secret OpenSSH Ed25519 public host-key format is supported."""
    if not isinstance(public_key, str) or len(public_key) > 200:
        raise ValueError("public_key")
    parts = public_key.split(" ")
    if len(parts) != 2 or parts[0] != "ssh-ed25519":
        raise ValueError("public_key")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError as e:
        raise ValueError("public_key") from e
    if len(blob) != 51 or blob[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("public_key")
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def valid_row(row, now=None, *, allow_expired=False):
    now = time.time() if now is None else now
    try:
        return (isinstance(row, dict) and set(row) == FIELDS
                and bool(NAME.fullmatch(row["alias"])) and bool(HOST.fullmatch(row["hostname"]))
                and bool(USER.fullmatch(row["user"])) and type(row["port"]) is int and 1 <= row["port"] <= 65535
                and row["owner"] == "self" and isinstance(row["peer"], str) and len(row["peer"]) <= 80
                and type(row["expires"]) is int and row["expires"] > 0
                and (allow_expired or now < row["expires"]) and row["expires"] <= now + MAX_LIFETIME
                and row["fingerprint"] == fingerprint(row["public_key"]))
    except (KeyError, TypeError, ValueError):
        return False


def parse_config(text):
    """Fixed supported Host/HostName/User/Port subset; no Include, Match or shell.

    Returned rows are ALWAYS pending, including LAN addresses and unsafe stanzas.
    Wildcard/multi-pattern blocks are not expanded. No private key files are read.
    """
    out, current = [], []
    for line in text[:65536].splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            for r in current:
                r["unsupported"] = True
            continue
        if not words:
            continue
        key = words[0].lower()
        if key == "host":
            current = []
            for alias in words[1:]:
                if NAME.fullmatch(alias) and len(out) < 128:
                    r = {"alias": alias, "hostname": alias, "user": "", "port": 22,
                         "status": "pending", "source": "ssh_config", "unsupported": len(words) != 2}
                    out.append(r)
                    current.append(r)
        elif key == "match":
            current = []
        elif key in ("hostname", "user", "port") and len(words) == 2:
            for r in current:
                value = words[1]
                if key == "port":
                    if value.isdecimal() and 1 <= int(value) <= 65535:
                        r[key] = int(value)
                    else:
                        r["unsupported"] = True
                elif (HOST if key == "hostname" else USER).fullmatch(value):
                    r[key] = value
                else:
                    r["unsupported"] = True
        else:
            for r in current:
                r["unsupported"] = True
    return out


def discover_config(path=None):
    """Read only the fixed SSH config file at runtime; caller tests pass a fixture."""
    path = Path.home() / ".ssh" / "config" if path is None else Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            import stat
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return []
            return parse_config(f.read(65537))
    except (OSError, UnicodeError):
        return []


def _read_metadata(path, limit):
    """Never follow a replaced table/marker into some other, possibly secret file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        import stat
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("metadata")
        text = f.read(limit + 1)
        if len(text) > limit:
            raise ValueError("metadata")
        return text


def scope(st):
    from . import cloud, wire
    cfg = st.config()
    binding = cloud.read_cloud(st) or {}
    return {"channel": cfg["channel"], "host": wire.b64u(st.signing_pub()),
            "seat": binding.get("host_id", "unbound:" + cfg["channel"])}


def prefix(row, program="ssh"):
    """Canonical pinned OpenSSH argv. KnownHostsCommand emits only a signed public key.

    /usr/bin/echo receives one literal safe argument, no shell or %-expansions.
    HostKeyAlias makes key lookup independent of DNS/IP spelling and port.
    """
    if program not in ("ssh", "scp"):
        raise ValueError("program")
    options = ["BatchMode=yes", "StrictHostKeyChecking=yes", "UserKnownHostsFile=/dev/null",
               "GlobalKnownHostsFile=/dev/null", "HostKeyAlias=" + row["alias"],
               "KnownHostsCommand=/usr/bin/echo '" + row["alias"] + " " + row["public_key"] + "'",
               "HostKeyAlgorithms=ssh-ed25519", "VerifyHostKeyDNS=no", "UpdateHostKeys=no",
               "CheckHostIP=no", "CanonicalizeHostname=no", "ProxyCommand=none", "ProxyJump=none",
               "ClearAllForwardings=yes", "ForwardAgent=no", "ForwardX11=no", "PermitLocalCommand=no",
               "ControlMaster=no", "ControlPath=none", "HostName=" + row["hostname"],
               "User=" + row["user"], "Port=" + str(row["port"])]
    return ["/usr/bin/" + program, "-F", "/dev/null", *[x for o in options for x in ("-o", o)]]


def command(row, remote="pwd", *, source=None, destination=None):
    """Suggested ordinary ssh/scp command; never executed by this module."""
    if source is None:
        return shlex.join(prefix(row) + [row["alias"], remote])
    return shlex.join(prefix(row, "scp") + [source, row["alias"] + ":" + (destination or "code/")])


@dataclass(frozen=True)
class _Receipt:
    digest: str
    revision: str
    alias: str
    expires: float
    remote: str | None
    destination: str | None


class Desk:
    def __init__(self, host):
        self.host = host
        self.st = host.st
        self._receipt = None
        self._revision = None

    def peer_active(self, peer):
        if not peer:
            return True  # the paired owner explicitly attested an unmanaged SSH machine
        svc = getattr(self.host, "peers", None)
        return bool(svc and svc.same_owner.active(peer))

    def _paths(self):
        root = Path(self.st.root)
        return root / "owner-hosts.json", root / "owner-hosts.revision"

    def read(self):
        from . import controls, wire
        path, mark = self._paths()
        try:
            rec = json.loads(_read_metadata(path, 32768))
            target = rec["target"]
            digest = hashlib.sha256(canonical(rec).encode()).hexdigest()
            if _read_metadata(mark, 64) != digest or (self._revision and self._revision != digest):
                return None
            if not self.shape(target, check_expiry=False) or target["scope"] != scope(self.st):
                return None
            key = self.st.sign_key(rec["device"])
            if not key or not controls.verify(key, wire.unb64u(rec["sig"]), target["scope"]["channel"],
                                             rec["device"], ACTION, rec["n"], rec["ts"],
                                             controls.object_digest(ACTION, target)):
                return None
            self._revision = digest
            return rec
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def shape(self, target, *, check_expiry=True):
        try:
            rows = target["rows"]
            now = time.time()
            return (set(target) == {"op", "rev", "scope", "rows"} and target["op"] == "replace"
                    and isinstance(target["rev"], str) and len(target["rev"]) <= 64
                    and isinstance(rows, list) and len(rows) <= MAX_ROWS
                    and len({r["alias"] for r in rows}) == len(rows)
                    and all(valid_row(r, now if check_expiry else r["expires"] - 1, allow_expired=True) for r in rows))
        except (KeyError, TypeError):
            return False

    def revision(self):
        _, mark = self._paths()
        try:
            return _read_metadata(mark, 64)
        except (OSError, ValueError):
            return ""

    def store(self, target, device, signed):
        """Called ONLY after live paired control signature + UV, with an optimistic revision."""
        if not self.shape(target) or target["scope"] != scope(self.st) or target["rev"] != self.revision():
            raise ValueError("changed")
        from .state import _write_private
        rec = {"target": target, "device": device, **{k: signed[k] for k in ("n", "ts", "sig")}}
        digest = hashlib.sha256(canonical(rec).encode()).hexdigest()
        path, mark = self._paths()
        _write_private(path, canonical(rec).encode())
        _write_private(mark, digest.encode())
        self._revision, self._receipt = digest, None

    def state(self, r=None, *, discovery=True):
        rec = self.read()
        rows = []
        if rec:
            for row in rec["target"]["rows"]:
                status = "active" if row["expires"] > time.time() and self.peer_active(row["peer"]) else "expired" if row["expires"] <= time.time() else "unbound"
                rows.append({**row, "status": status, "ssh": command(row),
                             "scp": command(row, source="./code.py", destination="code/")})
        candidates = discover_config() if discovery else []
        svc = getattr(self.host, "peers", None)
        if discovery and svc:
            candidates += [{"peer": fid, "status": "pending", "source": "same_owner"}
                           for fid in svc.store.friends() if svc.same_owner.active(fid)][:128]
        return {"t": "owner_hosts_state", "r": r, "scope": scope(self.st), "rev": self.revision(),
                "valid": bool(rec), "rows": rows, "candidates": candidates[:24]}

    def prove(self, tool, inp):
        """Host-only transport proof: exact canonical command, active signed row + key pin.

        No boolean, receipt, owner hostname or path in tool arguments is evidence.
        All wrappers/expansions/forwarding/options not in the generated argv fail closed.
        """
        if tool not in ("Bash", "exec_command", "shell_command") or not isinstance(inp, dict):
            return None
        if any(inp.get(k) for k in ("shell", "env")):
            return None
        if "cmd" in inp and "command" in inp and inp["cmd"] != inp["command"]:
            return None
        text = inp.get("command") if tool == "Bash" else inp.get("cmd", inp.get("command"))
        if not isinstance(text, str) or len(text) > 65536:
            return None
        try:
            words = shlex.split(text)
        except ValueError:
            return None
        if not words or words[0] not in ("/usr/bin/ssh", "/usr/bin/scp") or shlex.join(words) != text:
            return None
        rec = self.read()
        if not rec:
            return None
        for row in rec["target"]["rows"]:
            if row["expires"] <= time.time() or not self.peer_active(row["peer"]):
                continue
            program = words[0].rsplit("/", 1)[-1]
            start = prefix(row, program)
            if words[:len(start)] != start:
                continue
            rest = words[len(start):]
            remote = None
            destination = None
            if program == "ssh":
                if len(rest) != 2 or rest[0] != row["alias"] or not rest[1] or rest[1].startswith("-"):
                    continue
                remote = rest[1]
            else:
                if len(rest) != 2:
                    continue
                # One ordinary local source and one remote target; no legacy scp,
                # server-to-server transfers, glob/tilde expansion or option injection.
                src, dst = rest
                if not re.fullmatch(r"\.?/?[A-Za-z0-9_./-]+", src) or src.startswith("-") or ":" in src:
                    continue
                if not dst.startswith(row["alias"] + ":"):
                    continue
                path = dst[len(row["alias"]) + 1:]
                if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or path.startswith("-"):
                    continue
                destination = path
            receipt = _Receipt(hashlib.sha256(text.encode()).hexdigest(), self._revision, row["alias"],
                               min(time.time() + 5, row["expires"]), remote, destination)
            self._receipt = receipt
            return receipt
        return None

    def narrow(self, tool, inp, verdict):
        """Only proven transport send.upload is removed; remote effects are classified afresh."""
        from . import danger
        proof = self.prove(tool, inp)
        if proof is None or proof is not self._receipt or proof.expires <= time.time():
            return verdict
        items = [i for i in verdict.items if i[1] != "send.upload"]
        # The shared classifier may narrow delete/read categories. Remote execution
        # must retain real dangers and nested transports, even when rules deduplicate.
        if proof.remote is not None:
            extra = danger.classify_shared("Bash", {"command": proof.remote}, getattr(self.host, "danger_extra", []), "safe")
            items += extra.items
            for cat in extra.cats:
                if cat not in {i[0] for i in extra.items}:
                    rule = "shared.credential_access" if cat == "credentials" else "owner_hosts.remote." + cat
                    items.append((cat, rule, extra.why))
        if proof.destination is not None:
            items += danger.classify("Write", {"file_path": proof.destination}, getattr(self.host, "danger_extra", [])).items
        hits = danger._Hits()
        for cat, rule, why in items:
            hits.add(cat, rule, why)
        narrowed = hits.verdict()
        # Never clear a category for which a caller did not supply item provenance.
        if any(c not in {i[0] for i in verdict.items} for c in verdict.cats):
            return verdict
        narrowed.seen = verdict.seen
        return narrowed

    def narrow_requirement(self, tool, inp, requirement, verdict):
        """A registry hold caused ONLY by this transport may follow its receipt.

        Explicit UV/deny, other effects, learned skills and bound actions keep
        their original floor. Host.ask calls this before raising the floor.
        """
        if requirement != "hold" or verdict.cats or self.prove(tool, inp) is None:
            return requirement
        from . import danger
        text = inp.get("command") if tool == "Bash" else inp.get("cmd", inp.get("command"))
        original = danger.classify("Bash", {"command": text})
        shared = danger.classify_shared("Bash", {"command": text}, mode="safe")
        if (set(original.cats) | set(shared.cats)) == {"send"} and (set(original.rules) | set(shared.rules)) <= {"send.upload"}:
            return "allow"
        return requirement

    async def on_phone(self, session, obj):
        from . import controls, passkey, wire
        host, action = self.host, ACTION
        r = obj.get("r")
        if not wire.is_rid(r) or not host._paired_live(session):
            return
        if obj["t"] == "owner_hosts_get":
            return await host.send_app(session, self.state(r))
        target = obj.get("target")
        async def refuse(why, **kw):
            controls.log(self.st, action=action, device=session.device, result="refused:" + why)
            return await host._ctl_res(session, r, action, False, why=why, **kw)
        if not isinstance(target, dict) or not self.shape(target) or target["scope"] != scope(self.st):
            return await refuse("shape")
        if target["rev"] != self.revision():
            return await refuse("changed")
        previous = self.read()
        old_rows = previous["target"]["rows"] if previous else []
        if any(not self.peer_active(row["peer"]) and row not in old_rows for row in target["rows"]):
            return await refuse("unbound")
        if host.stopped():
            return await refuse("estop")
        pk = self.st.passkey_of_device(session.device)
        if not pk:
            return await refuse("no_passkey")
        n = obj.get("n")
        if not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
            return await refuse("shape")
        ch = passkey.elevate_challenge(host.channel, session.device, n, action, n, controls.object_digest(action, target))
        if obj.get("fa") is None:
            return await refuse("passkey_required", fa=pk["id"], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(ch))
        why = controls.check(self.st, host.nonces, host.channel, session.device, obj, action, target)
        if why:
            return await refuse(why)
        try:
            fields = passkey.approval_fields(obj["fa"])
            if fields["id"] != pk["id"]:
                raise ValueError("credential")
            count = passkey.verify_assertion(pk, fields, ch)
            self.st.passkey_count(session.device, pk["id"], count)
        except (passkey.PasskeyError, ValueError):
            return await refuse("passkey_failed")
        try:
            self.store(target, session.device, obj)
        except (OSError, ValueError):
            return await refuse("changed")
        controls.log(self.st, action=action, device=session.device, result="ok", obj=target,
                     sig=obj["sig"], n=obj["n"], ts=obj["ts"], extra={"passkey": "uv"})
        await host._ctl_res(session, r, action, True)
        await host.send_app(session, self.state())


def handle_commands(host, req):
    """Read-only IPC metadata; templates are never receipts or execution grants."""
    def error(why):
        return {"ok": False, "error": why, "rows": []}
    allowed = {"cmd", "alias", "remote", "source", "destination"}
    if (not isinstance(req, dict) or set(req) - allowed
            or req.get("cmd") != "owner_hosts_commands"):
        return error("invalid_request")
    for key in allowed - {"cmd"}:
        if key in req and (not isinstance(req[key], str) or not req[key]
                           or len(req[key]) > (64 if key == "alias" else 4096)
                           or any(ord(c) < 32 or ord(c) == 127 for c in req[key])):
            return error("invalid_" + key)
    alias = req.get("alias")
    if alias is not None and not NAME.fullmatch(alias):
        return error("invalid_alias")
    source, destination = req.get("source"), req.get("destination")
    if (destination is not None and source is None) or (source is not None and "remote" in req):
        return error("invalid_parameters")
    if "remote" in req and req["remote"].startswith("-"):
        return error("invalid_remote")
    for key, value in (("source", source), ("destination", destination)):
        if value is not None and (not re.fullmatch(r"[A-Za-z0-9_./-]+", value) or value.startswith("-")):
            return error("invalid_" + key)
    desk = host.owner_hosts
    rec = desk.read()
    if rec is None:
        return error("no_verified_table")
    rows = []
    for row in rec["target"]["rows"]:
        if ((alias is not None and row["alias"] != alias)
                or row["expires"] <= time.time() or not desk.peer_active(row["peer"])):
            continue
        ssh = command(row, req.get("remote", "pwd"))
        scp = command(row, source=source or "./code.py", destination=destination or "code/")
        rows.append({"alias": row["alias"], "status": "active", "expires": row["expires"],
                     "ssh": ssh, "scp": scp, "ssh_argv": shlex.split(ssh), "scp_argv": shlex.split(scp)})
    return {"ok": bool(rows), "error": None if rows else "no_active_match",
            "scope": dict(rec["target"]["scope"]), "rev": desk._revision, "rows": rows}
