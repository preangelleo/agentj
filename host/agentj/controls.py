"""Phone controls (PROMPT-26 item 3; ADR-A50 – A54; PROTOCOL §8 "phone controls"): signed write commands from a paired phone
and the stop-everything switch.

Every write a phone asks for — delete a memory item, undo it, stop everything, resume, enable / disable a scheduled task — is
decided exactly like an approval: only a ready session of a device on the allowlist that has an approval key, and only with a
valid Ed25519 signature over

    "agentjarvis-control-v1\\n" + channel + "\\n" + device id + "\\n" + action + "\\n" + nonce + "\\n" + ts + "\\n"
    + hex(SHA-256(UTF-8 object))

where `object` is the action's target, spelled out per action in `object_text()` (it carries the content hash the phone saw,
so a signature cannot be re-pointed at another item). `ts` (ms) must be within ±120 s of the host's clock and each nonce is
taken once. Signature wrong → nothing happens; every accepted or refused command is one line in `controls.log` (0600, hashes
only — like approvals.log) and, when the activity log is on, a readable line in activity.log.

The stop switch (`estop.json`, 0600) survives restarts: while it is on, serve interrupts and does not run the Agent, denies
every permission request, ends every batch grant and runs no scheduled task. Only a signed `resume` from a phone or
`agentj resume` at the terminal (remote pairing password) turns it off.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import wire

CONTEXT = "agentjarvis-control-v1"
ACTIONS = ("mem_rm", "mem_undo", "estop", "resume", "task_on", "task_off",
           # P71 (PROTOCOL §17.7): the friends page's signed writes
           "fr_set", "pg_set", "pg_del", "fr_add", "fr_discoverable", "fr_card",
           "fr_ctx")                   # P73 (ADR-A176): a friend's 「补充设定」 — the owner's own setting, never a message
FRIEND_ACTIONS = ACTIONS[6:]
ACTIONS += ("update", "bots_write", "setup_mark")   # P115: the first-run checklist card (first_run.py)
ACTIONS += ("pair_new",)                             # P153: a paired remote opens a pairing for another one (§3.3)
ACTIONS += ("dev_name", "dev_unbind", "dev_add", "dev_confirm")   # P171 (§24): the phone's 遥控器管理 page (remotes.py)
ACTIONS += ("agent_name",)          # P181 (§22.2): long-press the Agent's name in the reply card header → rename (names.py)
ACTIONS += ("cards_get",)           # P203 (§29.1): the signed read of every open card (a snapshot, changes nothing)
ACTIONS += ("mail_allow",)          # P207 (§25): the address book adds an address to the e-mail channel's allowlist (P170)
ACTIONS += ("reset", "rescue_do")    # P332 (§33): 紧急修复 — ① reset the connection · a whitelisted repair action (reset.py)
ACTIONS += ("approval_set",)        # P336 (§8): the approval level / goal-without-card switch (approval_level.py)
ACTIONS += ("cmd_run",)             # P361 (§34): Settings 「执行」 — one whitelisted command name (cmdrun.RUN_CMDS)
ACTIONS += ("term_open", "term_line", "term_sudo", "term_forget")   # P408 (§38): the web terminal (terminal.py)
ACTIONS += ("elev_stop",)           # P406 (§11.2): 「停止」 on a running sudo command's strip (elevate.py)
ACTIONS += ("elev_watch",)          # P406 F1 (§11.2): show / stop showing that command's masked output on this phone
TS_SKEW_MS = 120_000
NONCE_KEEP_S = 600
_NONCE = re.compile(r"[0-9a-f]{32}")


def object_text(action: str, obj: dict) -> str:
    """The exact text whose SHA-256 the phone signs for each action (the phone builds the same string from what it shows).

    mem_rm   : source id \\n file name ("" for a single-file source) \\n file SHA-256 \\n item id
    mem_undo : trash id
    estop / resume : "all"
    task_on / task_off : task id \\n contract SHA-256 (task.json + its prompt file, tasks.contract_sha)
    fr_set   : friend id \\n op (group | block | unblock | delete) \\n value (the group id; "" otherwise)
    pg_set   : the group's canonical JSON (sorted keys, no spaces, UTF-8 — wire.js canonicalJson)
    pg_del   : group id
    fr_add   : Agent ID \\n note
    fr_discoverable : "on" | "off"
    fr_card  : owner \\n intro
    fr_ctx   : friend id \\n text (the whole new 「补充设定」; "" clears it)
    dev_name : device id \\n new name          (P171, §24)
    dev_unbind : device id
    dev_add  : "new" · P190 (§27) a reverse pairing: "rv" \n the scanned code's rendezvous channel
    dev_confirm : device id \\n the 6-digit code the new device shows
    agent_name : the new Agent name as typed (P181, §22.2)
    cards_get : "cards" (P203, §29.1)
    mail_allow : address as sent \n role (owner | guest)  (P207, §25)
    reset    : the `what` ("connection")                  (P332, §33)
    rescue_do : the action name (help | report | reconnect | restart | linger | rollback | new_session)
    approval_set : key (approval.level | approval.goal_nocard) \n value (safe | standard | full | true | false)  (P336)
    term_open : the mode ("line")          term_line : terminal id \n the line          (P408, §38)
    term_sudo : terminal id \n sudo request id          term_forget : "forget"
    elev_stop : the running sudo card's id   (P406, §11.2)      elev_watch : card id \n on | off
    (a missing / null text field is "", as wire.js `?? ''`)
    """
    if action == "bots_write":
        return json.dumps(obj["request"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if action == "setup_mark":       # item id \n choice (selected | unused | later | start_exit) \n checklist revision
        return f"{obj['item']}\n{obj['choice']}\n{int(obj['rev'])}"
    if action == "update":
        return "latest"
    if action == "cards_get":        # P203: a read; the signature proves the approved device, the object is constant
        return "cards"
    if action == "pair_new":
        return "start"
    if action == "dev_name":         # P171: device id \n new name
        return f"{obj['device']}\n{obj['name']}"
    if action == "dev_unbind":
        return str(obj["device"])
    if action == "agent_name":       # P181: the new name exactly as sent (the host normalises after the check)
        return str(obj["name"])
    if action == "mail_allow":       # P207: the address exactly as sent \n role
        return f"{obj['addr']}\n{obj['role']}"
    if action == "reset":            # P332: ① — the object is constant {"what": "connection"}
        return str(obj["what"])
    if action == "rescue_do":        # P332: the whitelisted action's name (reset.RESCUE_ACTIONS); no argument travels
        return str(obj["action"])
    if action == "cmd_run":          # P361: the whitelisted command's name (cmdrun.RUN_CMDS); no argument travels
        return str(obj["cmd"])
    if action == "term_open":        # P408 (§38): object {"mode": "line"} → "line"
        return str(obj["mode"])
    if action == "term_line":        # the terminal id \n the line exactly as sent (a confirmed dangerous line)
        return f"{obj['id']}\n{obj['line']}"
    if action == "term_sudo":        # the terminal id \n the host's sudo request id
        return f"{obj['id']}\n{obj['rid']}"
    if action == "term_forget":      # object {} — delete this device's cached computer password
        return "forget"
    if action == "elev_stop":        # P406 (§11.2): the running sudo card's id
        return str(obj["id"])
    if action == "elev_watch":       # P406 F1 (§11.2): the card's id \n on | off
        return f"{obj['id']}\n{'on' if obj['on'] is True else 'off'}"
    if action == "approval_set":     # P336: key \n value (a boolean as true / false)
        v = obj["value"]
        return f"{obj['key']}\n{('true' if v else 'false') if isinstance(v, bool) else v}"
    if action == "dev_add":
        return f"rv\n{obj['rv']}" if obj.get("rv") else "new"
    if action == "dev_confirm":      # the new device's id \n the 6-digit code its screen shows
        return f"{obj['device']}\n{obj['code']}"
    if action == "mem_rm":
        return f"{obj['src']}\n{obj['file']}\n{obj['fsha']}\n{obj['iid']}"
    if action == "mem_undo":
        return str(obj["id"])
    if action in ("estop", "resume"):
        return "all"
    if action in ("task_on", "task_off"):
        return f"{obj['id']}\n{obj['tsha']}"
    if action == "fr_set":
        return f"{obj['friend']}\n{obj['op']}\n{_s(obj.get('value'))}"
    if action == "pg_set":
        return json.dumps(obj["group"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if action == "pg_del":
        return str(obj["id"])
    if action == "fr_add":
        return f"{obj['id']}\n{_s(obj.get('note'))}"
    if action == "fr_discoverable":
        return "on" if obj.get("on") else "off"
    if action == "fr_card":
        return f"{_s(obj.get('owner'))}\n{_s(obj.get('intro'))}"
    if action == "fr_ctx":
        return f"{obj['friend']}\n{_s(obj.get('text'))}"
    raise ValueError("bad action")


def _s(v) -> str:
    return "" if v is None else str(v)


def object_digest(action: str, obj: dict) -> str:
    return hashlib.sha256(object_text(action, obj).encode()).hexdigest()


def signed_message(channel: str, device: str, action: str, nonce: str, ts: int, digest_hex: str) -> bytes:
    if action not in ACTIONS:
        raise ValueError("bad action")
    return f"{CONTEXT}\n{channel}\n{device}\n{action}\n{nonce}\n{ts}\n{digest_hex}".encode()


def verify(sign_pub: bytes, sig: bytes, channel: str, device: str, action: str, nonce: str, ts: int, digest_hex: str) -> bool:
    if len(sign_pub) != 32 or len(sig) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(sig, signed_message(channel, device, action, nonce, ts, digest_hex))
        return True
    except (InvalidSignature, ValueError):
        return False


class Nonces:
    """Each nonce once, within the ts window (in memory: a Noise session cannot be replayed anyway; this is the second lock)."""

    def __init__(self):
        self.seen: dict[str, float] = {}

    def take(self, n: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        for k in [k for k, t in self.seen.items() if now - t > NONCE_KEEP_S]:
            del self.seen[k]
        if n in self.seen or len(self.seen) > 10_000:
            return False
        self.seen[n] = now
        return True


def check(st, nonces: Nonces, channel: str, device: str, obj: dict, action: str, target: dict) -> str | None:
    """None when the command may run, else the refusal reason (shape | stale | replay | no_key | bad_signature)."""
    n, ts, sig = obj.get("n"), obj.get("ts"), obj.get("sig")
    if not isinstance(n, str) or not _NONCE.fullmatch(n) or not isinstance(ts, int) or isinstance(ts, bool) \
            or not isinstance(sig, str) or len(sig) > 100:
        return "shape"
    if abs(time.time() * 1000 - ts) > TS_SKEW_MS:
        return "stale"
    sk = st.sign_key(device)
    if not sk:
        return "no_key"
    try:
        sigb = wire.unb64u(sig)
    except ValueError:
        sigb = b""
    try:
        digest = object_digest(action, target)
    except (KeyError, TypeError, ValueError):
        return "shape"
    if not verify(sk, sigb, channel, device, action, n, ts, digest):
        return "bad_signature"
    if not nonces.take(n):
        return "replay"
    return None


def log(st, *, action: str, device: str | None, result: str, obj: dict | None = None, sig: str | None = None,
        n: str | None = None, ts: int | None = None) -> None:
    """One line in controls.log (0600): what was asked, by whom, the outcome — the object only as its SHA-256."""
    rec = {"ts": int(time.time()), "action": action, "device": device, "result": result}
    if obj is not None:
        try:
            rec["object_sha256"] = object_digest(action, obj)
        except (KeyError, TypeError, ValueError):
            pass
    if sig:
        rec["n"], rec["sig_ts"], rec["sig"] = n, ts, sig
    st.append_private(st.controls_path, json.dumps(rec, ensure_ascii=False))


# ------------------------------------------------------------------ the stop switch (estop.json)
def estop_state(st) -> dict:
    """{"on": bool, "at": unix s, "by": "phone:<device>" | "terminal"} — a broken file reads as ON (fail closed)."""
    try:
        d = json.loads(st.estop_path.read_text())
    except FileNotFoundError:
        return {"on": False}
    except (OSError, ValueError, UnicodeDecodeError):
        return {"on": True, "at": 0, "by": "unreadable"}
    if not isinstance(d, dict):
        return {"on": True, "at": 0, "by": "unreadable"}
    return {"on": d.get("on") is not False, "at": d.get("at") if isinstance(d.get("at"), int) else 0,
            "by": str(d.get("by", ""))[:80]}


def set_estop(st, on: bool, by: str) -> dict:
    rec = {"on": bool(on), "at": int(time.time()), "by": by[:80]}
    with st.config_lock():
        st.write_private(st.estop_path, json.dumps(rec).encode())
    return rec
