"""Host state directory: keys, config, allowlist, metadata log. Everything 0700 / 0600; plaintext messages never land here."""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import hmac
import json
import os
import pathlib
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import diskq, wire
from .devnames import device_label, device_names, platform_of  # noqa: F401  (P179; re-exported)
from .envcompat import getenv
from .noise import Keypair
from .text import agent_name_problem, normalise_agent_name

DEFAULT_RELAY = "wss://relay.agentj.app"
DEFAULT_WEB = "https://m.agentj.app"
OLD_DEFAULT_RELAY = "wss://alpha-relay.agentjarvis.net"   # ≤ 0.9 defaults: rewritten to the new ones by migrate.py
OLD_DEFAULT_WEB = "https://alpha-web.agentjarvis.net"
DEFAULT_STATE = "~/.local/state/agentj"
LEGACY_STATE = "~/.local/state/agentjarvis-alpha"   # ≤ 0.9; after the move (migrate.py) a symlink to DEFAULT_STATE
MAX_DEVICES = 10  # remotes per host = per seat (Q32 2026-10-02: 5; P171 2026-10-12 Leo: 10 — four colleagues × phone + computer
#                   = 8, plus room); enforced here, in the one writer of the allowlist


class DeviceLimit(Exception):
    """The allowlist already holds MAX_DEVICES devices and the caller did not allow an eviction (State.pair_device)."""


def _num(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def evict_order(devs: dict, online=frozenset()) -> list[str]:
    """Who goes first when a new remote needs a place (0.15.1): offline before online, then the longest unseen (last
    resume, else pairing time), then the earliest paired. Stable over the allowlist order for exact ties."""
    def key(kv):
        did, v = kv
        v = v if isinstance(v, dict) else {}
        return (did in online, max(_num(v.get("seen")), _num(v.get("paired_at"))), _num(v.get("paired_at")))
    return [k for k, _ in sorted(devs.items(), key=key)]


def _brief(did: str, v, online=frozenset()) -> dict:
    v = v if isinstance(v, dict) else {}
    return {"id": did, "name": str(v.get("name", "")), "paired_at": _num(v.get("paired_at")), "online": did in online}


def state_dir() -> pathlib.Path:
    """$AGENTJ_STATE_DIR (or, one version cycle, $AGENTJARVIS_STATE_DIR); else ~/.local/state/agentj — except while a
    0.9 state directory has not been moved yet (migrate.py refused: a serve is running on it): then that one."""
    d = getenv("AGENTJ_STATE_DIR")
    if d:
        return pathlib.Path(d)
    new, old = default_state(), legacy_state()
    if not os.path.lexists(new) and old.is_dir() and not old.is_symlink():
        from .migrate import serve_running    # P127: only a serve still running on it keeps the old one in use —
        if serve_running(old):                # an old directory nobody chose to adopt is never used silently
            return old
    return new


def default_state(home: str | None = None) -> pathlib.Path:
    return pathlib.Path(os.path.join(home, DEFAULT_STATE[2:]) if home else os.path.expanduser(DEFAULT_STATE))


def legacy_state(home: str | None = None) -> pathlib.Path:
    return pathlib.Path(os.path.join(home, LEGACY_STATE[2:]) if home else os.path.expanduser(LEGACY_STATE))


_WRITES = [0]   # P292: private writes by this process — part of the allowlist cache key (an inode number can be reused)


def _write_private(path: pathlib.Path, data: bytes) -> None:
    diskq.write(path, data)   # P142: atomic + fsync; on the event loop the fsync goes to the ordered disk writer
    _WRITES[0] += 1


class State:
    def __init__(self, root: pathlib.Path | None = None):
        self.root = root or state_dir()

    # ------------------------------------------------------------ paths
    @property
    def x25519_path(self): return self.root / "host_x25519.key"
    @property
    def ed25519_path(self): return self.root / "host_ed25519.key"
    @property
    def config_path(self): return self.root / "config.json"
    @property
    def devices_path(self): return self.root / "devices.json"
    @property
    def log_path(self): return self.root / "host.log"
    @property
    def sock_path(self): return self.root / "control.sock"
    @property
    def cloud_path(self): return self.root / "cloud.json"   # Dashboard link (PROTOCOL §7); metadata only, no secret
    @property
    def cloud_lock_path(self): return self.root / "cloud.lock"   # flock for cloud.json mutations (CLI ↔ serve), empty
    @property
    def devices_lock_path(self): return self.root / "devices.lock"   # flock for every allowlist mutation (review A32-02)
    @property
    def config_lock_path(self): return self.root / "config.lock"   # flock for config.json read-modify-writes (CLI ↔ serve ↔ admin)
    @property
    def unbind_path(self): return self.root / "remote_unbind.json"   # Dashboard unbind decisions: times + request ids only
    @property
    def removed_path(self): return self.root / "removed.json"   # 0.15.1: why a device left the allowlist (ids, reason, time)
    @property
    def perm_dir(self): return self.root / "agentperm"   # the only part of the state dir the fenced agent can see (L2)
    @property
    def perm_sock_path(self): return self.perm_dir / "perm.sock"   # serve ↔ the agent's permission tool (PROTOCOL §8), 0600
    @property
    def approver_path(self): return self.root / "approver.json"   # remote pairing password: scrypt hash + failure ledger (L2)
    @property
    def approvals_path(self): return self.root / "approvals.log"   # one line per approval decision, signed, no tool input
    @property
    def agent_path(self): return self.root / "agent.json"   # the agent's session id (to resume after a restart), nothing else
    @property
    def push_path(self): return self.root / "push.json"   # Web Push subscriptions per device (PROTOCOL §9)
    @property
    def vapid_path(self): return self.root / "push_vapid.key"   # this host's VAPID P-256 private key (PROTOCOL §9)
    @property
    def estop_path(self): return self.root / "estop.json"   # the stop-everything switch (controls.py), survives restarts
    @property
    def controls_path(self): return self.root / "controls.log"   # one line per signed phone command, hashes only
    @property
    def tasks_path(self): return self.root / "tasks.json"   # which scheduled tasks the human enabled + last runs (tasks.py)
    @property
    def migrated_path(self): return self.root / "migrated.json"   # the 0.9 → 0.10 move: from where, when, replaced URLs

    def exists(self) -> bool:
        return self.x25519_path.exists() and self.ed25519_path.exists() and self.config_path.exists()

    # ------------------------------------------------------------ init
    def init(self, relay: str = DEFAULT_RELAY, web: str = DEFAULT_WEB, force: bool = False) -> dict:
        if self.exists() and not force:
            raise FileExistsError(f"already initialised: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        kp = Keypair.generate()
        ed = Ed25519PrivateKey.generate()
        _write_private(self.x25519_path, kp.private_bytes())
        _write_private(self.ed25519_path, ed.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                           serialization.NoEncryption()))
        cfg = {"v": 1, "relay": relay, "web": web, "channel": wire.channel_id(self._ed_pub(ed)), "created": int(time.time())}
        _write_private(self.config_path, json.dumps(cfg, indent=1).encode())
        if not self.devices_path.exists() or force:
            _write_private(self.devices_path, b"{}")
        self.log("init", channel=cfg["channel"])
        return cfg

    @staticmethod
    def _ed_pub(ed: Ed25519PrivateKey) -> bytes:
        return ed.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    # ------------------------------------------------------------ load
    def check_perms(self) -> None:
        st = self.root.stat()
        if st.st_mode & 0o077:
            raise PermissionError(f"{self.root} must be 0700")
        for p in (self.x25519_path, self.ed25519_path, self.devices_path, self.config_path, self.log_path, self.cloud_path,
                  self.cloud_lock_path, self.config_lock_path, self.devices_lock_path, self.unbind_path,
                  self.approvals_path, self.agent_path, self.push_path, self.vapid_path, self.approver_path,
                  self.estop_path, self.controls_path, self.tasks_path, self.migrated_path):
            if p.exists() and p.stat().st_mode & 0o077:
                raise PermissionError(f"{p} must be 0600")

    def config(self) -> dict:
        return json.loads(self.config_path.read_text())

    def host_keypair(self) -> Keypair:
        return Keypair.from_private(self.x25519_path.read_bytes())

    def signing_key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(self.ed25519_path.read_bytes())

    def signing_pub(self) -> bytes:
        return self._ed_pub(self.signing_key())

    # ------------------------------------------------------------ allowlist
    DEVICES_TTL = 2.0   # P292: s an unchanged-looking allowlist is trusted before it is read again
    def devices(self) -> dict:
        """P292: every phone frame asks `is_allowed` — the parsed allowlist is kept, keyed by the file's identity (inode, size,
        mtime, ctime) and this process's own private-write count, and read again at least every DEVICES_TTL s. Every writer
        replaces the file by rename (a new inode, a new ctime), so a change by any process is seen at the next call; the count
        covers this process reusing a freed inode inside one timestamp tick, the TTL anything stranger. An unchanged file costs
        one stat instead of a read + parse on the event loop."""
        try:
            stt = os.stat(self.devices_path)
        except FileNotFoundError:
            return {}
        key = (stt.st_ino, stt.st_size, stt.st_mtime_ns, stt.st_ctime_ns, _WRITES[0])
        hit = getattr(self, "_devices_cache", None)
        if hit is not None and hit[0] == key and time.monotonic() - hit[2] < self.DEVICES_TTL:
            return self._live_devices(copy.deepcopy(hit[1]))
        try:
            d = json.loads(self.devices_path.read_text())
        except FileNotFoundError:
            return {}
        self._devices_cache = (key, d, time.monotonic())
        return self._live_devices(copy.deepcopy(d))

    def _live_devices(self, devices):
        from . import credential_revoke
        try:
            journal = credential_revoke.read(self)
            return {k:v for k,v in devices.items() if not credential_revoke.blocked(self,k,v,journal)}
        except (OSError, ValueError, TypeError):
            return {}  # damaged tombstone journal fails closed

    @contextlib.contextmanager
    def devices_lock(self):
        """Exclusive lock around every allowlist read-modify-write, across threads and processes (serve approving, `agentj
        revoke` / `agentj admin` editing offline): two concurrent revokes can never resurrect each other's device."""
        fd = os.open(self.devices_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def rename_device(self, device: str, name: str, via: str = "account") -> bool:
        """Owner metadata only, under the same allowlist lock as pairing/revocation. P179: the new name is the owner's
        (`owner_name` + when + through which entry: "account" = account page passkey, "phone" = that remote itself);
        the device's own pairing label stays in `self_name`."""
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("invalid device name")
        with self.devices_lock():
            devices = self.devices()
            if device not in devices:
                return False
            rec = devices[device]
            n = device_names(rec)
            if n["self_name"] is not None:
                rec["self_name"] = n["self_name"]
            rec["name"] = rec["owner_name"] = name.strip()
            rec["owner_named_at"] = int(time.time())
            rec["owner_named_via"] = via if via in ("account", "phone") else "account"
            self.write_private(self.devices_path, json.dumps(devices).encode())
        return True

    def migrate_device_names(self) -> int:
        """P179, once per record: a pre-P179 allowlist entry has only `name`. If the audit log shows the owner set exactly
        that name (an allowed account-page `device_name` decision for this device and this name), it is the owner's name
        and the device's own label is unknown; otherwise it is the device's own label. Returns how many records changed."""
        def legacy(v):
            return isinstance(v, dict) and "self_name" not in v and "owner_name" not in v
        if not any(legacy(v) for v in self.devices().values()):
            return 0
        approved = set()
        with contextlib.suppress(OSError):
            for line in self.approvals_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("tool") == "device_name" and r.get("decision") == "allow":
                    approved.add(r.get("input_sha256"))
        changed = 0
        with self.devices_lock():
            d = self.devices()
            for did, v in d.items():
                if not legacy(v):
                    continue
                name = str(v.get("name", ""))
                digest = hashlib.sha256(json.dumps({"device": did, "name": name}, sort_keys=True).encode()).hexdigest()
                if name.strip() and digest in approved:
                    v["owner_name"], v["owner_named_at"], v["owner_named_via"], v["self_name"] = name, 0, "account", None
                else:
                    v["self_name"] = name
                changed += 1
            if changed:
                _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return changed

    def add_device(self, pub: bytes, name: str, sign_pub: bytes | None = None) -> str:
        """sign_pub = the device's Ed25519 approval key (PROTOCOL §8), sent inside the encrypted pairing msg1."""
        return self.pair_device(pub, name, sign_pub)["device"]

    def pair_device(self, pub: bytes, name: str, sign_pub: bytes | None = None, *, iid: str | None = None,
                    evict: bool = False, online: frozenset | set = frozenset(), source: str = "local") -> dict:
        """Add (or re-pair) one device in ONE locked read-modify-write (0.15.1, P55). → {"device", "replaced": rec|None,
        "evicted": rec|None} where rec = {"id", "name", "paired_at", "online"}.
        - same key (= same device id) → its record is rewritten, nothing else changes (ADR-015's rule, here since A3.1);
        - same browser install (`iid`, a per-host hash the page sends in its pairing msg1) under another key — the browser
          lost its key, the human paired it again → the old record is replaced, not kept as a second remote;
        - a NEW device on a full allowlist: evict=False → DeviceLimit; evict=True → the stalest remote goes (offline before
          online, then the longest unseen). The human who typed the code + passphrase for the new device agreed to it."""
        did = wire.device_id(pub)
        replaced = evicted = None
        with self.devices_lock():
            d = self.devices()
            from .security_rotation import read as rotation_state
            rotation = rotation_state(self)
            if rotation and not rotation.get('done') and did not in d:
                if len(d) >= MAX_DEVICES or (iid and any(v.get('iid')==iid for v in d.values() if isinstance(v,dict))):
                    raise DeviceLimit(len(d))  # P490: rescue remotes cannot be automatically replaced/evicted
            earlier = d.get(did)        # P179: the same device / browser pairing again keeps the name its owner gave it
            if did not in d and iid:
                twin = next((k for k, v in d.items() if isinstance(v, dict) and v.get("iid") == iid), None)
                if twin:
                    earlier = d.pop(twin)
                    replaced = _brief(twin, earlier, online)
            if did not in d and len(d) >= MAX_DEVICES:
                if not evict:
                    raise DeviceLimit(len(d))
                victim = evict_order(d, online)[0]
                evicted = _brief(victim, d.pop(victim), online)
            rec = {"pub": wire.b64u(pub), "name": name, "self_name": name, "paired_at": int(time.time()), "source": source}
            kept = device_names(earlier) if isinstance(earlier, dict) else None
            if kept and kept["named_by"] == "owner":
                rec["name"] = rec["owner_name"] = kept["owner_name"]
                rec["owner_named_at"] = kept["owner_named_at"]
                rec["owner_named_via"] = earlier.get("owner_named_via", "account")
            if sign_pub:
                rec["sk"] = wire.b64u(sign_pub)
            if iid:
                rec["iid"] = iid
            d[did] = rec
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        for gone in (replaced, evicted):
            if gone:
                self.note_removed(gone["id"], "replaced")
        self.forget_removed(did)
        return {"device": did, "replaced": replaced, "evicted": evicted}

    def evict_candidate(self, online: frozenset | set = frozenset(), new: str | None = None) -> dict | None:
        """Who pair_device(evict=True) would take for a new device right now (None = there is room / it is listed)."""
        d = self.devices()
        if new in d or len(d) < MAX_DEVICES:
            return None
        k = evict_order(d, online)[0]
        return _brief(k, d[k], online)

    def touch_seen(self, did: str) -> None:
        """A device resumed: remember when (decides who is "longest unseen" when the list is full). ≤ 1 write a minute."""
        now = int(time.time())
        with self.devices_lock():
            d = self.devices()
            rec = d.get(did)
            if not isinstance(rec, dict) or now - _num(rec.get("seen")) < 60:
                return
            rec["seen"] = now
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())

    # why a device left: "replaced" (a newer pairing took its place) or "revoked" (a human removed it). A removed device that
    # resumes is told which (PROTOCOL §3 `removed`), so its page never says "the computer removed you" when it did not.
    REMOVED_KEEP = 64

    def removed_ledger(self) -> dict:
        try:
            d = json.loads(self.removed_path.read_text())
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            return {}
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict) and v.get("why") in ("replaced", "revoked")} \
            if isinstance(d, dict) else {}

    def note_removed(self, did: str, why: str) -> None:
        with self.devices_lock():
            d = self.removed_ledger()
            d.pop(did, None)
            d[did] = {"why": why, "at": int(time.time())}
            keep = sorted(d.items(), key=lambda kv: _num(kv[1].get("at")))[-self.REMOVED_KEEP:]
            _write_private(self.removed_path, json.dumps(dict(keep)).encode())

    def forget_removed(self, did: str) -> None:
        with self.devices_lock():
            d = self.removed_ledger()
            if d.pop(did, None) is not None:
                _write_private(self.removed_path, json.dumps(d).encode())

    def removed_why(self, did: str) -> str | None:
        v = self.removed_ledger().get(did)
        return v["why"] if v else None

    def sign_key(self, did: str) -> bytes | None:
        v = self.devices().get(did, {}).get("sk")
        try:
            b = wire.unb64u(v) if isinstance(v, str) else b""
        except ValueError:
            return None
        return b if len(b) == 32 else None

    def set_sign_key_if_absent(self, pub: bytes, sign_pub: bytes) -> bool:
        """A device paired before L1 adds its approval key on its next resume (the IK handshake already proved the device's
        static key, so this is as strong as pairing). Never replaces a key that is already there."""
        did = wire.device_id(pub)
        with self.devices_lock():
            d = self.devices()
            rec = d.get(did)
            if not rec or rec.get("pub") != wire.b64u(pub) or rec.get("sk"):
                return False
            rec["sk"] = wire.b64u(sign_pub)
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def is_allowed(self, pub: bytes) -> bool:
        rec = self.devices().get(wire.device_id(pub))
        return bool(rec) and rec.get("pub") == wire.b64u(pub)

    def remove_device(self, did: str, why: str = "revoked") -> bool:
        with self.devices_lock():
            d = self.devices()
            if did not in d:
                return False
            del d[did]
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        self.note_removed(did, why)
        return True

    def device_full(self) -> bool:
        return len(self.devices()) >= MAX_DEVICES

    # ------------------------------------------------------------ F20 passkey (PROTOCOL §12): lives INSIDE the device record,
    # so every way a record leaves the allowlist (revoke, eviction, iid replace, unbind) takes its passkey with it; nothing
    # else (removed.json, logs) ever holds a credential id or key.
    def passkey_of(self, cred_id: str) -> tuple[str, dict] | None:
        """(device id, its pk record) for a credential id, or None (unknown — or its device was removed)."""
        for did, v in self.devices().items():
            pk = v.get("pk") if isinstance(v, dict) else None
            if isinstance(pk, dict) and isinstance(pk.get("id"), str) and hmac.compare_digest(pk["id"], cred_id):
                return did, pk
        return None

    def passkey_of_device(self, did: str) -> dict | None:
        """The pk record of a listed device, or None (no passkey: its F17 cards need no Face ID — ADR-A163)."""
        rec = self.devices().get(did)
        pk = rec.get("pk") if isinstance(rec, dict) else None
        if not isinstance(pk, dict) or not isinstance(pk.get("id"), str):
            return None
        out = dict(pk)
        # P555: a key saved before P539 has no `local`. pk_reg always asks for this device's own (platform) authenticator
        # (serve._pk_where counts a missing attachment as local), so a key registered under this id is this device's own;
        # only a record a restore moved here (`dev` names another id) stays unknown (→ the one-tap self-check, pkpeer.py).
        if "local" not in out and out.get("dev", did) == did:
            out["local"] = True
        return out

    def passkey_state(self, did: str | None) -> str:
        """P539: none | local | borrowed | unknown for a listed device (passkey.pk_state of its record; advisory only)."""
        from . import passkey
        return passkey.pk_state(self.passkey_of_device(did) if did else None)

    def passkey_local(self, did: str, cred_id: str, local: bool) -> bool:
        """P555: after a verified self-check assertion, note where this device's key lives (advisory, like P539's `local`)."""
        with self.devices_lock():
            d = self.devices()
            pk = (d.get(did) or {}).get("pk")
            if not isinstance(pk, dict) or pk.get("id") != cred_id:
                return False
            pk["local"] = bool(local)
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def passkey_count(self, did: str, cred_id: str, count: int) -> bool:
        """Remember a verified non-zero signCount (a cloned authenticator then fails on the next use, ADR-A163).
        Every verified assertion of this device's own key lands here: P555 — a key whose place was never noted (「未确认」)
        just worked from this device, so it is noted as this device's (the display follows what works; advisory only)."""
        noted = False
        if not count and self.passkey_state(did) != "unknown":
            return False
        with self.devices_lock():
            d = self.devices()
            pk = (d.get(did) or {}).get("pk")
            if not isinstance(pk, dict) or pk.get("id") != cred_id:
                return False
            if "local" not in pk and pk.get("dev", did) != did:
                pk["local"] = noted = True
            if count:
                pk["sc"] = count
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        if noted and callable(cb := getattr(self, "on_passkey_noted", None)):
            with contextlib.suppress(RuntimeError):     # no running loop (a CLI / thread caller): the next resume repaints
                cb(did)
        return bool(count)

    def set_passkey(self, pub: bytes, pk: dict, *, replacement: dict | None = None, challenge: bytes | None = None,
                    peer_ok: bool = False) -> bool:
        """Store (or replace: one passkey per device) the passkey of the device with this static key, if still listed.
        A replacement needs the old key's assertion — or (P547) `peer_ok`: serve passes True ONLY for the one pk_offer it
        itself marked approved by another remote's key for this device (pkpeer.Desk.approved); the lineage is kept alike.
        P547 (review minor 4): the record notes `dev`, the device id that registered it — only that device may approve
        another's Face ID set-up (a restored record under a new id holds a key that lives elsewhere)."""
        did = wire.device_id(pub)
        from . import credential_revoke
        if credential_revoke.blocked(self,did,{"pk":pk}):
            return False
        with self.devices_lock():
            d = self.devices()
            rec = d.get(did)
            if not isinstance(rec, dict) or rec.get("pub") != wire.b64u(pub):
                return False
            if isinstance(rec.get("pk"), dict) and peer_ok is not True:
                # Verify against the current trust root under the same lock as the write.
                from . import passkey
                try:
                    f = passkey.approval_fields(replacement)
                    if not challenge or f["id"] != rec["pk"]["id"]:
                        return False
                    passkey.verify_assertion(rec["pk"], f, challenge)
                except (passkey.PasskeyError, ValueError):
                    return False
            if isinstance(rec.get("pk"),dict) and rec["pk"].get("id")!=pk.get("id"):
                lineage=credential_revoke.ancestors(rec)|{credential_revoke.target(rec['pk'])}
                if len(lineage)>credential_revoke.MAX_TARGETS: return False  # never forget an old grant to make room
                credential_revoke.note_replaced(self,rec["pk"])
                rec['pk_ancestors']=sorted(lineage)
            rec["pk"] = dict(pk, dev=did)
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def passkey_restore(self, old: str, cred_id: str, pub: bytes, sign_pub: bytes, count: int = 0,
                        iid: str | None = None, local: bool | None = None) -> str | None:
        """A verified passkey restore, in ONE locked write: the record `old` (still listed, still holding this credential)
        takes the new X25519 key (→ a new device id) and the new approval key — the old ones died with the old browser
        context — keeps its name, paired_at and passkey, and is marked seen. → the new device id, or None (the record left
        the allowlist meanwhile, or the new key belongs to another listed record). The caller ends the old id's sessions."""
        new = wire.device_id(pub)
        with self.devices_lock():
            d = self.devices()
            rec = d.get(old)
            pk = rec.get("pk") if isinstance(rec, dict) else None
            if not isinstance(pk, dict) or pk.get("id") != cred_id or (new != old and new in d):
                return None
            # P547: `dev` stays the id that registered the key (a legacy record gets `old`): under the new id this record is
            # no pk_peer approver — its key lives on another device (pkpeer.Desk._eligible)
            rec = dict(rec, pair_origin=rec.get("pair_origin", old), pub=wire.b64u(pub), sk=wire.b64u(sign_pub), seen=int(time.time()),
                       pk=dict(pk, dev=pk.get("dev") if isinstance(pk.get("dev"), str) else old))
            rec.pop("rt", None)           # P122: the old context's renewal ticket dies with it; serve issues a new one
            if count:
                rec["pk"]["sc"] = count
            if local is not None:          # P539: advisory — the passkey now lives on this device, or is borrowed
                rec["pk"]["local"] = bool(local)
            if iid:
                rec["iid"] = iid
            else:
                rec.pop("iid", None)      # the old browser's install hash must not match (and replace) this record later
            d.pop(old)
            d[new] = rec
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        if new != old:
            self.note_removed(old, "replaced")
        self.forget_removed(new)
        return new

    # ------------------------------------------------------------ P122 renewal ticket (PROTOCOL §12.1): also INSIDE the device
    # record (one per record), so it leaves with it exactly like the passkey above; never printed, logged or reported.
    def ticket_of(self, tid: str) -> tuple[str, dict] | None:
        """(device id, its rt record) for a ticket id, or None (unknown, rotated away, or its device was removed)."""
        for did, v in self.devices().items():
            rt = v.get("rt") if isinstance(v, dict) else None
            if isinstance(rt, dict) and isinstance(rt.get("i"), str) and hmac.compare_digest(rt["i"], tid):
                return did, rt
        return None

    def set_ticket(self, pub: bytes, rt: dict) -> bool:
        """Store (or replace: one ticket per device) the renewal ticket of the device with this static key, if still listed."""
        did = wire.device_id(pub)
        with self.devices_lock():
            d = self.devices()
            rec = d.get(did)
            if not isinstance(rec, dict) or rec.get("pub") != wire.b64u(pub):
                return False
            rec["rt"] = dict(rt)
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def ticket_restore(self, old: str, tid: str, pub: bytes, sign_pub: bytes, new_rt: dict, iid: str | None = None) -> str | None:
        """A verified ticket restore, in ONE locked write — passkey_restore's rule (the record `old`, still listed and still
        holding ticket `tid`, takes the new X25519 + approval keys and keeps name / paired_at / pk) plus the rotation: the used
        ticket is replaced by `new_rt` in the same write, so it can never be used twice. → the new device id, or None."""
        new = wire.device_id(pub)
        with self.devices_lock():
            d = self.devices()
            rec = d.get(old)
            rt = rec.get("rt") if isinstance(rec, dict) else None
            if not isinstance(rt, dict) or rt.get("i") != tid or (new != old and new in d):
                return None
            rec = dict(rec, pair_origin=rec.get("pair_origin", old), pub=wire.b64u(pub), sk=wire.b64u(sign_pub), seen=int(time.time()), rt=dict(new_rt))
            if iid:
                rec["iid"] = iid
            else:
                rec.pop("iid", None)
            d.pop(old)
            d[new] = rec
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        if new != old:
            self.note_removed(old, "replaced")
        self.forget_removed(new)
        return new

    # ------------------------------------------------------------ remote unbind switch (A3.1, PROTOCOL §7 sync)
    def remote_unbind(self) -> bool:
        """May the Dashboard's owner ask this host to remove a device? Default on; `agentj remote-unbind off` turns it off."""
        try:
            return self.config().get("remote_unbind", True) is not False
        except (OSError, ValueError):
            return False

    def set_remote_unbind(self, on: bool) -> None:
        with self.config_lock():
            cfg = self.config()
            cfg["remote_unbind"] = bool(on)
            _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())

    def report_machine(self) -> bool:
        """Report this machine's hostname to the Dashboard (`machine`)? Default on; `agentj report-hostname off`."""
        try:
            return self.config().get("report_machine", True) is not False
        except (OSError, ValueError):
            return False

    def set_report_machine(self, on: bool) -> None:
        with self.config_lock():
            cfg = self.config()
            cfg["report_machine"] = bool(on)
            _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())

    @contextlib.contextmanager
    def file_lock(self, name: str):
        """P142 (B19): flock `<state>/<name>.lock` — one read-modify-write of one state file at a time, across threads and
        processes (serve's scheduler, the phone's card, the account page and the terminal all edit tasks.json)."""
        fd = os.open(self.root / f"{name}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def config_lock(self):
        """Exclusive lock around every config.json read-modify-write, across threads and processes (`serve` adopting a
        synced name, `agentj name`, `agentj admin`, `agentj remote-unbind` never interleave)."""
        fd = os.open(self.config_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    # ------------------------------------------------------------ Agent name (A3.2): a display string, nothing else
    def agent_name(self) -> str | None:
        """The local Agent name, or None (unset, or a hand-edited value that breaks the §1 rules)."""
        try:
            v = self.config().get("agent_name")
        except (OSError, ValueError):
            return None
        return normalise_agent_name(v) if isinstance(v, str) and agent_name_problem(v) is None else None

    def set_agent_name(self, name: str) -> str:
        """Validate (§1) and store; returns the normalised name. Raises ValueError("bad_name" | "name_required")."""
        err = agent_name_problem(name)
        if err:
            raise ValueError(err)
        name = normalise_agent_name(name)
        with self.config_lock():
            cfg = self.config()
            cfg["agent_name"] = name
            _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
        return name

    # ------------------------------------------------------------ agent bridge (L1, PROTOCOL §8)
    AGENT_KINDS = ("claude", "codex", "opencode", "kimi", "qwen", "dsh", "core")   # P359: core = Agent J Harness

    def agent_config(self) -> dict | None:
        """{"kind": "claude"|"codex"|"opencode", "dir": absolute path, "model": str|None} or None (no agent: terminal chat as in A2)."""
        try:
            a = self.config().get("agent")
        except (OSError, ValueError):
            return None
        if not isinstance(a, dict) or a.get("kind") not in self.AGENT_KINDS or not isinstance(a.get("dir"), str):
            return None
        m = a.get("model")
        e = a.get("effort")
        # fence (L2): on unless `--unfenced` or (F14) the preference agent.isolation=false (CLI or a paired phone)
        # docker (G-A56): the container engines stay hidden unless `--allow-docker` or agent.allow_docker=true
        # effort (PROMPT-33 §10.11): the phone's pill; a short word the harness validates, or None (its default)
        from . import approval_level, preferences, session_choice, shared_pin
        prefs = preferences.effective(self)
        root = preferences.get(prefs, "agent.working_root")
        directory = os.path.realpath(os.path.expanduser(root)) if root else a["dir"]
        owner_pin = preferences.get(prefs, "agent.shared_session_id", "")
        return {"kind": a["kind"], "dir": directory, "working_root": directory,
                "instructions": preferences.get(prefs, "agent.instructions", ""),
                "private_instructions_file": preferences.get(prefs, "agent.private_instructions_file", ""),
                "shared_identity": preferences.get(prefs, "agent.shared_identity", True) is not False,
                "language": preferences.get(prefs, "appearance.language", "zh"),
                "model": m if isinstance(m, str) and m else None,
                "effort": e if isinstance(e, str) and e.isalpha() and len(e) <= 16 else None,
                "fence": a.get("fence") is not False and preferences.get(prefs, "agent.isolation", True) is not False,
                "docker": a.get("docker") is True or preferences.get(prefs, "agent.allow_docker", False) is True,
                "session_mode": session_choice.mode_in(self, prefs),
                # P330: this harness's own pin (agent.json `<kind>.pin`); the config value is only the owner's choice
                "shared_session_id": shared_pin.resolve(self, a["kind"], owner_pin, directory),
                "shared_pin_owner": owner_pin if isinstance(owner_pin, str) else "",
                "shared_opencode_port": preferences.get(prefs, "agent.shared_opencode_port", 0),
                "high_risk_warnings": preferences.get(prefs, "agent.high_risk_warnings", False),
                "approvals": approval_level.shared_mode(prefs, self),   # P336: safe level → "safe"; P551 harness → native
                "follow_harness": approval_level.follow_harness(prefs, self),   # P551 (#320): the hook asks only for ③
                # P305: Kimi's site (kimi.com = mainland-cn, kimi.ai = global) for an API key / the login rescue; None = mainland-cn
                "region": a.get("region") if a.get("region") in ("mainland-cn", "global") else None}

    def set_agent_config(self, kind: str | None, directory: str | None = None, model: str | None = None,
                         fence: bool = True, docker: bool = False, effort: str | None = None, record_root: bool = True) -> None:
        with self.config_lock():
            cfg = self.config()
            if kind is None:
                cfg.pop("agent", None)
            else:
                if kind not in self.AGENT_KINDS:
                    raise ValueError("bad_agent")
                d = os.path.realpath(os.path.expanduser(directory or os.getcwd()))
                if not os.path.isdir(d):
                    raise ValueError("bad_dir")
                from .fence import protected_paths
                for p in protected_paths(self):    # the agent's folder must not be (inside) what the fence hides
                    if d == p or d.startswith(p.rstrip(os.sep) + os.sep):
                        raise ValueError("protected_dir")
                cfg["agent"] = {"kind": kind, "dir": d, "model": model or None, "fence": bool(fence)}
                if effort:                         # P219: the default (or the owner's earlier) effort survives `agentj agent`
                    cfg["agent"]["effort"] = effort
                if docker:
                    cfg["agent"]["docker"] = True
            _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
        # An explicit folder is a human/API root selection, not a legacy fallback.
        # Record it in the one JSON5 owner so older saved preferences cannot silently
        # send the next session to a different (possibly deleted) directory.
        if record_root and kind is not None and directory is not None:
            from . import working_root
            working_root.record(self, pathlib.Path(d))

    def set_agent_region(self, region: str | None) -> None:
        """P305: `agentj agent kimi --region mainland-cn|global` (Kimi's two sites; a kimi.com key works on both endpoints)."""
        with self.config_lock():
            cfg = self.config()
            if isinstance(cfg.get("agent"), dict):
                if region in ("mainland-cn", "global"):
                    cfg["agent"]["region"] = region
                else:
                    cfg["agent"].pop("region", None)
                _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())

    def set_agent_model(self, model: str | None, *, expected: dict | None = None) -> bool:
        """/model from the phone (slash.py): only the model of the configured Agent changes."""
        with self.config_lock():
            cfg = self.config()
            a = cfg.get("agent")
            if expected is not None and (not isinstance(a, dict) or
                    any(a.get(k) != expected.get(k) for k in ("kind", "model"))):
                return False
            if isinstance(a, dict):
                a["model"] = model or None
                _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
                return True
            return False

    def set_agent_effort(self, effort: str | None, *, expected: dict | None = None) -> bool:
        """The phone's effort pill (PROMPT-33 §10.11): stored like the model; None = the harness's own default."""
        with self.config_lock():
            cfg = self.config()
            if expected is not None and (not isinstance(cfg.get("agent"), dict) or
                    any(cfg["agent"].get(k) != expected.get(k) for k in ("kind", "model"))):
                return False
            if isinstance(cfg.get("agent"), dict):
                if effort:
                    cfg["agent"]["effort"] = effort
                else:
                    cfg["agent"].pop("effort", None)
                _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
                return True
            return False

    def agent_session(self, kind: str) -> str | None:
        try:
            d = json.loads(self.agent_path.read_text())
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            return None
        v = d.get(kind) if isinstance(d, dict) else None
        return v if isinstance(v, str) and 0 < len(v) <= 128 and all(c.isalnum() or c in "-_" for c in v) else None

    def set_agent_session(self, kind: str, sid: str | None) -> None:
        try:
            d = json.loads(self.agent_path.read_text())
            d = d if isinstance(d, dict) else {}
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            d = {}
        if sid:
            d[kind] = sid
        else:
            d.pop(kind, None)
        _write_private(self.agent_path, json.dumps(d).encode())

    def append_private(self, path: pathlib.Path, line: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)

    UNBIND_KEEP_S = 7 * 86400   # request ids are remembered a week (the Dashboard expires them after 24 h)
    UNBIND_KEEP_IDS = 512

    def unbind_ledger(self) -> dict:
        """{executed: [unix s], decided: [unix s], throttled: [unix s], seen: {request id: [result, unix s]}} — survives
        restarts, so the hourly caps and "each request once" hold across `serve` restarts (review A31-02)."""
        try:
            d = json.loads(self.unbind_path.read_text())
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            d = {}
        now = time.time()
        num = lambda xs: [float(t) for t in xs if isinstance(t, (int, float)) and not isinstance(t, bool) and now - t < 3600]  # noqa: E731
        seen = d.get("seen") if isinstance(d.get("seen"), dict) else {}
        seen = {k: v for k, v in seen.items() if isinstance(k, str) and len(k) <= 64 and isinstance(v, list) and len(v) == 2
                and isinstance(v[0], str) and isinstance(v[1], int) and now - v[1] < self.UNBIND_KEEP_S}
        return {"executed": num(d.get("executed") or []), "decided": num(d.get("decided") or []),
                "throttled": num(d.get("throttled") or []), "seen": seen}

    def save_unbind_ledger(self, d: dict) -> None:
        seen = sorted(d["seen"].items(), key=lambda kv: kv[1][1])[-self.UNBIND_KEEP_IDS:]   # evict the oldest, never all
        rec = {"executed": d["executed"], "decided": d["decided"], "throttled": d["throttled"], "seen": dict(seen)}
        _write_private(self.unbind_path, json.dumps(rec).encode())

    def write_private(self, path: pathlib.Path, data: bytes) -> None:
        _write_private(path, data)

    # ------------------------------------------------------------ metadata log (never message text)
    # report_* events (PROTOCOL §7) carry only seq / status class / trigger — never labels, codes or URLs
    LOG_FIELDS = {"why", "exception_type", "http_status", "channel", "cid", "device", "name", "reason", "kind", "bytes", "code_ok", "seq", "status", "trigger",
                  "tenant", "request", "result", "ms", "id", "tool", "agent", "decision", "locked", "fence", "change", "action",
                  "session_mode", "isolation_requested", "isolation_effective", "phase", "version", "language", "core_sha256", "prompt_sha256", "mechanism", "working_root_sha256", "identity_session",
                  "generation", "model", "effort", "layers", "identity",
                  "ms", "code", "count",   # P129: timings (ms), close codes, failure counts — numbers only. P116: metadata only (no text, path or digest)
                  "cmd", "keys", "events", "event",   # P139: control command / config key / hook event NAMES only
                  "where", "task", "pstate", "majflt",   # P142: loop-stall diagnostics (site/task names, process state, fault counts)
                  "handoff", "sha", "full", "by",   # P198: P151 handoff present (bool), P156 heartbeat prompt sha8 / full / trigger
                  "to", "files", "skipped", "refused", "domains", "msgid", "smtp",   # P295 mail_fwd: counts, recipient
                  # domains (never a whole address), our Message-ID and the SMTP server's queue id from its 250 after DATA
                  "via", "plugin",   # P539: a 免问 grant's binding kind and the harness plugin id ("<name>@<marketplace>")
                  "tier", "ret", "age_h"}   # P559: P551's ask `tier` (uv | hold | card) was dropped before this; wechat_forward ret / age_h

    def log(self, ev: str, **kw) -> None:
        rec = {"ts": int(time.time()), "ev": ev, **{k: v for k, v in kw.items() if k in self.LOG_FIELDS}}
        diskq.append(self.log_path, (json.dumps(rec, ensure_ascii=False) + "\n").encode())   # P142: buffered on the loop
