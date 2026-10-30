"""P361 (PROTOCOL §34): the 「执行」 button next to a command in Settings — run a whitelisted `agentj` command on the computer.

The first step towards a web terminal (the second one is only a spec: docs/specs/WEB-TERMINAL-SPEC.md). The phone never sends
a command line: it sends one NAME from `RUN_CMDS`; the argv is fixed here. Every run:
  - is a signed control (control-v1 action `cmd_run`, object {"cmd": name}) from an approved device, AND
  - for repair commands, carries a passkey assertion (Face ID / fingerprint, UP + UV) over `passkey.elevate_challenge(channel, device, n,
    "cmd_run", n, object_digest)` — two steps like `approval_set`: without `fa` the host answers `passkey_required` + the
    challenge (nonce untouched). Repair without a passkey is refused (`no_passkey`). The closed READ_CMDS set needs only the device signature; output is still masked.
  - runs one at a time, as this same user (never more rights than serve), stdin closed, a hard timeout, output capped,
    ANSI / control characters dropped and credential-shaped text masked by `privacy.owner_text` before it leaves the host.
  - leaves one line in controls.log (who, which name, result — never the output) and one in host.log (exit code, ms).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import time

from . import controls, passkey, privacy, wire

# The whole whitelist. name → (argv after `agentj`, why it is safe). Nothing that stops, deletes, revokes, pairs, sets a
# preference, installs or touches a key is here, except the P567 fixed emergency AI switches (signed + UV).
RUN_CMDS = {
    "doctor": (("doctor", "--read-only"), "read-only self check; doctor's contract is one line per check and never a secret"),
    "reconnect": (("doctor", "--reconnect"), "repairs only Agent J's own link (service · relay · Agent) — the same steps as "
                  "紧急修复 ①; deletes nothing, changes no setting"),
    "status": (("status",), "read-only: serve's own status answer"),
    "service_status": (("service", "status"), "read-only: whether the background service is installed / running"),
    "rescue_status": (("rescue", "status"), "read-only: the rescue watchdog's state and whitelisted log metadata"),
    "update_check": (("update", "check"), "read-only: compares versions, installs nothing"),
}
for _h in ("claude", "codex", "opencode", "kimi", "qwen", "dsh"):
    RUN_CMDS["switch_" + _h] = (("agent", "switch", _h, "--force"), "owner-confirmed emergency AI switch")


def available_commands():
    from . import harness
    installed = {r["name"] for r in harness.detect()["harnesses"] if r["installed"] and r["supported"]}
    return [n for n in RUN_CMDS if not n.startswith("switch_") or n[7:] in installed]


# Only this argv has a whole-CLI zero-write contract (including startup migration).
READ_CMDS = frozenset(('doctor',))
TIMEOUT = 120          # s per run (doctor --reconnect may wait for the relay)
PENDING_MS = 150000    # what the phone waits after `ctl_pending`
OUT_MAX = 12000        # characters sent back (the tail is cut, `cut: true`)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|[\x00-\x08\x0b-\x1f\x7f]")


def argv(name: str) -> list[str]:
    from . import service
    return [*service.agentj_argv(), *RUN_CMDS[name][0]]


def clean(text: str) -> tuple[str, bool]:
    """Masked, printable, capped: (text, cut)."""
    t = privacy.owner_text(_ANSI.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n"))
    return (t[:OUT_MAX], True) if len(t) > OUT_MAX else (t, False)


def run(name: str, *, runner=None) -> dict:
    """→ {"code", "out", "cut"?, "ms"} — never raises (a timeout is code -1, an error -2)."""
    t0 = time.monotonic()
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "PYTHONIOENCODING": "utf-8"}
    try:
        r = (runner or subprocess.run)(argv(name), capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL, env=env,
                                       errors="replace")
        code, raw = r.returncode, (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    except subprocess.TimeoutExpired:
        code, raw = -1, ""
    except Exception as e:  # noqa: BLE001 — reported, never raised
        code, raw = -2, type(e).__name__
    out, cut = clean(raw)
    res = {"code": code, "out": out, "ms": int((time.monotonic() - t0) * 1000)}
    if cut:
        res["cut"] = True
    return res


class Desk:
    """serve's glue for `cmd_run` (one run at a time)."""

    def __init__(self, host):
        self.h = host
        self.busy: str | None = None

    async def on_phone(self, s, obj: dict) -> None:
        h, r, name = self.h, obj.get("r"), obj.get("cmd")
        if not wire.is_rid(r):
            return

        async def res(ok, **kw):
            await h.send_app(s, {"t": "ctl_res", "r": r, "action": "cmd_run", "ok": ok, **kw})

        n = obj.get("n")
        if not isinstance(name, str) or name not in RUN_CMDS or not isinstance(n, str) or not re.fullmatch(r"[0-9a-f]{32}", n):
            controls.log(h.st, action="cmd_run", device=s.device, result="refused:shape")
            return await res(False, why="shape")
        target = {"cmd": name}
        require_uv = name not in READ_CMDS
        if require_uv:
            pk = h.st.passkey_of_device(s.device)
            if not pk:
                controls.log(h.st, action="cmd_run", device=s.device, result="refused:no_passkey", obj=target)
                return await res(False, why="no_passkey", cmd=name)
            ch = passkey.elevate_challenge(h.channel, s.device, n, "cmd_run", n, controls.object_digest("cmd_run", target))
            if obj.get("fa") is None:
                return await res(False, why="passkey_required", cmd=name, fa=pk["id"], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(ch))
        why = controls.check(h.st, h.nonces, h.channel, s.device, obj, "cmd_run", target)
        if not why and require_uv:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                h.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        if not why and self.busy:
            why = "busy"
        if why:
            h.st.log("control_refused", device=s.device, action="cmd_run", reason=why)
            controls.log(h.st, action="cmd_run", device=s.device, result="refused:" + why, obj=target)
            return await res(False, why=why, cmd=name)
        controls.log(h.st, action="cmd_run", device=s.device, result="start", obj=target, sig=obj.get("sig"), n=n,
                     ts=obj.get("ts"))
        self.busy = name
        h._bg(self._run(s, r, name), "cmd_run")         # first: its `finally` always clears `busy` (review M1)
        with contextlib.suppress(Exception):            # the link may drop right now; the result still finds the phone
            await h.send_app(s, {"t": "ctl_pending", "r": r, "action": "cmd_run", "timeout_ms": PENDING_MS})

    async def _run(self, s, r, name) -> None:
        h, device = self.h, s.device
        try:
            await asyncio.sleep(0)                      # let on_phone send `ctl_pending` before the result
            out = await asyncio.to_thread(run, name)
        finally:
            self.busy = None
        result = "timeout" if out["code"] == -1 else f"exit:{out['code']}"
        controls.log(h.st, action="cmd_run", device=device, result=result, obj={"cmd": name})
        h.st.log("cmd_run", device=device, cmd=name, code=out["code"], ms=out["ms"])
        m = {"t": "ctl_res", "r": r, "action": "cmd_run", "ok": out["code"] == 0, "cmd": name, **out,
             **({"why": "timeout"} if out["code"] == -1 else {})}
        sent = False
        with contextlib.suppress(Exception):
            sent = await h.send_app(s, m)               # the asking session …
        if not sent:                                    # … else the same phone after a reconnect (review L3)
            await h._send_device(device, m)
