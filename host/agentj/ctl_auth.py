"""A4 (P139): who may change safety state through serve's control socket (control.sock, 0600, same uid).

The socket's file mode stops other users, not the Agent: in shared mode the owner's Claude/Codex session runs as the owner,
and an owned Agent's children are serve's own descendants. So the commands that change safety state — `resume` (lift the
emergency stop), `config_apply` (isolation, approvals …), `send` (speak as the host on every phone) and `revoke` (cut the
owner's phone off) — need a one-time claim, asked for on the same connection (like perm.sock's token, serve.on_perm):

- `{"cmd":"claim"}` → a token for this connection's peer process, single use, CLAIM_TTL s. It is refused when the peer
  (SO_PEERCRED / LOCAL_PEERPID) descends from a process serve runs or serves (owned Agent, scheduled task, the shared
  session), carries an Agent harness marker in its environment, or cannot be identified at all.
- `resume` additionally needs the remote pairing password, verified here by serve (the CLI checks it too), or — without one — a
  terminal on the caller's stdin.
- `config_apply` without a claim (an Agent running `agentj config set`, e.g. the agentj-config skill) still applies ordinary
  keys; a change to a SECURITY key becomes a signed approval card on the phone and is applied only after the owner allows it.

Same-uid code can always reach the owner's own terminal by other means; this closes the direct, silent path.
"""
from __future__ import annotations

import os
import subprocess
import sys

SENSITIVE = frozenset({"resume", "config_apply", "send", "revoke", "mail_cfg",   # P170: mail_cfg widening → the owner's card
                       "notify"})    # P365: the owner's own automations only — never an Agent (notify.py)
CLAIM_TTL = 30
AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
             "OPENCODE", "AGENTJ_PERM_TOKEN", "AGENTJ_PERM_SOCK", "AGENTJ_FENCED")
SECURITY_PREFIXES = ("agent.allow_docker", "agent.approvals", "agent.high_risk_warnings", "agent.isolation",
                     "agent.session_mode", "agent.working_root", "agent.private_instructions_file", "approval.", "human.",
                     "security.", "telegram.", "channels.", "updates.", "proxy.", "notify.")   # P365: who may post to the phone


def ancestors(pid: int) -> list:
    from .shared_hook import ancestors as walk
    return walk(pid)


def _environ(pid: int) -> set:
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return set()
    return {kv.split(b"=", 1)[0].decode("ascii", "replace") for kv in raw.split(b"\0") if b"=" in kv}


def refusal(pid, serve_pid: int, agent_pids) -> str | None:
    """None = the peer may claim; else a short reason (logged, never shown with details)."""
    if not isinstance(pid, int) or pid <= 0:
        return "unverifiable"
    chain = ancestors(pid)
    if not chain:
        return "unverifiable"
    if pid != serve_pid and serve_pid in chain[1:]:
        return "agent"                       # a child of serve: owned Agent, scheduled task, bot, peer session
    if set(chain) & {p for p in agent_pids if isinstance(p, int) and p > 1}:
        return "agent"                       # inside the served (shared) session's process tree
    if pid != serve_pid and _environ(pid) & set(AGENT_ENV):
        return "agent"                       # run from an Agent harness (another Claude/Codex/OpenCode session)
    return None


def has_tty(pid) -> bool:
    """The peer's stdin is a terminal (Linux /proc). P176: macOS has no /proc — there the peer's controlling terminal (`ps`)."""
    try:
        return os.readlink(f"/proc/{pid}/fd/0").startswith(("/dev/pts/", "/dev/tty"))
    except (OSError, TypeError):
        pass
    if sys.platform != "darwin" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        tty = subprocess.run(["/bin/ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(tty) and tty not in ("?", "??", "-")


class DuplicateKey(ValueError):
    """P318: two spellings of one flattened key (`{"agent": {"x": 1}, "agent.x": 2}`): never let the later one win silently."""


def _flat(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            part = _flat(v, key) if isinstance(v, dict) and v else {key: v}
            dup = set(part) & set(out)
            if dup:
                raise DuplicateKey(sorted(dup)[0])
            out.update(part)
    return out


def security_changes(old: dict, new: dict) -> list:
    """Security keys whose effective value differs between two validated configurations. Each schema security key is
    compared through preferences.get — the accessor the runtime reads with — so no spelling of a key can hide a change;
    security-prefixed keys outside the schema (validate refuses them anyway) are compared flattened, duplicates raise."""
    from . import preferences
    old, new = old or {}, new or {}
    keys = {k for k in preferences.SCHEMA if k.startswith(SECURITY_PREFIXES)}
    out = {k for k in keys if preferences.get(old, k) != preferences.get(new, k)}
    a, b = _flat(old), _flat(new)
    rest = {k for k in set(a) | set(b) if k.startswith(SECURITY_PREFIXES) and not any(k == s or k.startswith(s + ".") for s in keys)}
    out |= {k for k in rest if a.get(k) != b.get(k)}
    return sorted(out)
