"""Host state directory: keys, config, allowlist, metadata log. Everything 0700 / 0600; plaintext messages never land here."""
from __future__ import annotations

import contextlib
import fcntl
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
    """The allowlist already holds MAX_DEVICES devices: unbind one (`agentj revoke`) before approving another."""


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
        did = wire.device_id(pub)
        with self.devices_lock():
            d = self.devices()
            if did not in d and len(d) >= MAX_DEVICES:
                raise DeviceLimit(len(d))
            rec = {"pub": wire.b64u(pub), "name": name, "paired_at": int(time.time())}
            if sign_pub:
                rec["sk"] = wire.b64u(sign_pub)
            d[did] = rec
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return did

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

    def remove_device(self, did: str) -> bool:
        with self.devices_lock():
            d = self.devices()
            if did not in d:
                return False
            del d[did]
            _write_private(self.devices_path, json.dumps(d, indent=1, ensure_ascii=False).encode())
        return True

    def device_full(self) -> bool:
        return len(self.devices()) >= MAX_DEVICES

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
        # fence (L2): on unless the human explicitly chose `--unfenced` at this terminal; anything else reads as on
        # docker (G-A56): the container engines stay hidden unless the human explicitly chose `--allow-docker`
        return {"kind": a["kind"], "dir": a["dir"], "model": m if isinstance(m, str) and m else None,
                "fence": a.get("fence") is not False, "docker": a.get("docker") is True}

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

    def set_agent_model(self, model: str | None) -> None:
        """/model from the phone (slash.py): only the model of the configured Agent changes."""
        with self.config_lock():
            cfg = self.config()
            if isinstance(cfg.get("agent"), dict):
                cfg["agent"]["model"] = model or None
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
    LOG_FIELDS = {"channel", "cid", "device", "name", "reason", "kind", "bytes", "code_ok", "seq", "status", "trigger",
                  "tenant", "request", "result", "id", "tool", "agent", "decision", "locked", "fence", "change", "action"}

    def log(self, ev: str, **kw) -> None:
        rec = {"ts": int(time.time()), "ev": ev, **{k: v for k, v in kw.items() if k in self.LOG_FIELDS}}
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)
