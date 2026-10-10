"""Host state directory: keys, config, allowlist, metadata log. Everything 0700 / 0600; plaintext messages never land here."""
from __future__ import annotations

import contextlib
import fcntl
import hmac
import json
import os
import pathlib
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import wire
from .envcompat import getenv
from .noise import Keypair
from .text import agent_name_problem, normalise_agent_name

DEFAULT_RELAY = "wss://relay.agentj.app"
DEFAULT_WEB = "https://m.agentj.app"
OLD_DEFAULT_RELAY = "wss://alpha-relay.agentjarvis.net"   # ≤ 0.9 defaults: rewritten to the new ones by migrate.py
OLD_DEFAULT_WEB = "https://alpha-web.agentjarvis.net"
DEFAULT_STATE = "~/.local/state/agentj"
LEGACY_STATE = "~/.local/state/agentjarvis-alpha"   # ≤ 0.9; after the move (migrate.py) a symlink to DEFAULT_STATE
MAX_DEVICES = 5   # remotes per host = per seat (Q32, 2026-10-02); enforced here, in the one writer of the allowlist


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
        return old
    return new


def default_state(home: str | None = None) -> pathlib.Path:
    return pathlib.Path(os.path.join(home, DEFAULT_STATE[2:]) if home else os.path.expanduser(DEFAULT_STATE))


def legacy_state(home: str | None = None) -> pathlib.Path:
    return pathlib.Path(os.path.join(home, LEGACY_STATE[2:]) if home else os.path.expanduser(LEGACY_STATE))


def _write_private(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")  # serve and the CLI may both write
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


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
    def approver_path(self): return self.root / "approver.json"   # approval passphrase: scrypt hash + failure ledger (L2)
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
    def devices(self) -> dict:
        try:
            return json.loads(self.devices_path.read_text())
        except FileNotFoundError:
            return {}

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
            if did not in d and iid:
                twin = next((k for k, v in d.items() if isinstance(v, dict) and v.get("iid") == iid), None)
                if twin:
                    replaced = _brief(twin, d.pop(twin), online)
            if did not in d and len(d) >= MAX_DEVICES:
                if not evict:
                    raise DeviceLimit(len(d))
                victim = evict_order(d, online)[0]
                evicted = _brief(victim, d.pop(victim), online)
            rec = {"pub": wire.b64u(pub), "name": name, "paired_at": int(time.time()), "source": source}
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
        return dict(pk) if isinstance(pk, dict) and isinstance(pk.get("id"), str) else None

    def passkey_count(self, did: str, cred_id: str, count: int) -> bool:
        """Remember a verified non-zero signCount (a cloned authenticator then fails on the next use, ADR-A163)."""
        if not count:
            return False
        with self.devices_lock():
            d = self.devices()
            pk = (d.get(did) or {}).get("pk")
            if not isinstance(pk, dict) or pk.get("id") != cred_id:
                return False
            pk["sc"] = count
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def set_passkey(self, pub: bytes, pk: dict) -> bool:
        """Store (or replace: one passkey per device) the passkey of the device with this static key, if still listed."""
        did = wire.device_id(pub)
        with self.devices_lock():
            d = self.devices()
            rec = d.get(did)
            if not isinstance(rec, dict) or rec.get("pub") != wire.b64u(pub):
                return False
            rec["pk"] = dict(pk)
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def passkey_restore(self, old: str, cred_id: str, pub: bytes, sign_pub: bytes, count: int = 0,
                        iid: str | None = None) -> str | None:
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
            rec = dict(rec, pub=wire.b64u(pub), sk=wire.b64u(sign_pub), seen=int(time.time()), pk=dict(pk))
            rec.pop("rt", None)           # P122: the old context's renewal ticket dies with it; serve issues a new one
            if count:
                rec["pk"]["sc"] = count
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
            rec = dict(rec, pub=wire.b64u(pub), sk=wire.b64u(sign_pub), seen=int(time.time()), rt=dict(new_rt))
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
    AGENT_KINDS = ("claude", "codex", "opencode")

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
        from . import preferences
        prefs = preferences.effective(self)
        root = preferences.get(prefs, "agent.working_root")
        directory = os.path.realpath(os.path.expanduser(root)) if root else a["dir"]
        return {"kind": a["kind"], "dir": directory, "working_root": directory,
                "instructions": preferences.get(prefs, "agent.instructions", ""),
                "language": preferences.get(prefs, "appearance.language", "zh"),
                "model": m if isinstance(m, str) and m else None,
                "effort": e if isinstance(e, str) and e.isalpha() and len(e) <= 16 else None,
                "fence": a.get("fence") is not False and preferences.get(prefs, "agent.isolation", True) is not False,
                "docker": a.get("docker") is True or preferences.get(prefs, "agent.allow_docker", False) is True,
                "session_mode": preferences.get(prefs, "agent.session_mode", "shared"),
                "shared_session_id": preferences.get(prefs, "agent.shared_session_id", ""),
                "shared_opencode_port": preferences.get(prefs, "agent.shared_opencode_port", 0),
                "high_risk_warnings": preferences.get(prefs, "agent.high_risk_warnings", False)}

    def set_agent_config(self, kind: str | None, directory: str | None = None, model: str | None = None,
                         fence: bool = True, docker: bool = False) -> None:
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
                if docker:
                    cfg["agent"]["docker"] = True
            _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())
        # An explicit folder is a human/API root selection, not a legacy fallback.
        # Record it in the one JSON5 owner so older saved preferences cannot silently
        # send the next session to a different (possibly deleted) directory.
        if kind is not None and directory is not None:
            from . import working_root
            working_root.record(self, pathlib.Path(d))

    def set_agent_model(self, model: str | None) -> None:
        """/model from the phone (slash.py): only the model of the configured Agent changes."""
        with self.config_lock():
            cfg = self.config()
            if isinstance(cfg.get("agent"), dict):
                cfg["agent"]["model"] = model or None
                _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())

    def set_agent_effort(self, effort: str | None) -> None:
        """The phone's effort pill (PROMPT-33 §10.11): stored like the model; None = the harness's own default."""
        with self.config_lock():
            cfg = self.config()
            if isinstance(cfg.get("agent"), dict):
                if effort:
                    cfg["agent"]["effort"] = effort
                else:
                    cfg["agent"].pop("effort", None)
                _write_private(self.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())

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
                  "tenant", "request", "result", "id", "tool", "agent", "decision", "locked", "fence", "change", "action",
                  "session_mode", "isolation_requested", "isolation_effective", "phase", "version", "language", "core_sha256", "prompt_sha256", "mechanism", "working_root_sha256", "identity_session"}

    def log(self, ev: str, **kw) -> None:
        rec = {"ts": int(time.time()), "ev": ev, **{k: v for k, v in kw.items() if k in self.LOG_FIELDS}}
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)
